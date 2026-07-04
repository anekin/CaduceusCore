/* NPU Firmware — bare-metal RISC-V (RV32IM)
 *
 * 运行在 NPU 侧的 RISC-V 核上，通过 MMIO 控制 MXU/SFU/Vector/DMA。
 * Host 通过 Doorbell + Ring Buffer 下发命令，固件消费并写回 Completion Ring。
 *
 * 构建: make -C firmware
 * 运行: spike --isa=RV32IM -m0x80000000:0x10000000,0x00000000:0x00400000 \
 *          +mmio_plugin=sim/spike_mmio_plugin.py firmware/build/npu_firmware.elf
 */

#include "npu-regmap.h"

/* ── 内存布局 ───────────────────────────────────────────────────── */

#define DRAM_BASE  0x80000000UL   // DRAM data (Host DDR)
#define DRAM_SIZE  0x0FF00000UL   // ~255 MB
#define SRAM_BASE  0x20000000UL
#define SRAM_SIZE  0x00400000UL   // 4 MB

/* Vector 运算使用的固定 SRAM scratch 区域 */
#define VEC_A_SRAM (SRAM_BASE + 0x000000UL)
#define VEC_B_SRAM (SRAM_BASE + 0x100000UL)
#define VEC_O_SRAM (SRAM_BASE + 0x200000UL)

/* ── Ring Buffer 配置 ────────────────────────────────────────────── */

#define RING_BUF_ADDR        DRAM_BASE
#define RING_ENTRIES         16
#define CMD_DESC_SIZE        32
#define COMPLETION_RING_ADDR (DRAM_BASE + 0x800)

/* 命令描述符结构 (与 Host 约定) */
typedef struct __attribute__((packed)) {
    uint32_t opcode;       // engine-level OpCode (MMUL/SFU/Vector/DMA)
    uint32_t desc_addr;    // 操作描述符的 DRAM 地址
    uint32_t flags;        // bit0=中断完成, bit1=立即执行
    uint32_t _pad[5];      // 对齐到 32B
} cmd_entry_t;

/* 操作描述符 — MMUL (matches Func Model host_write_descriptor 15-word layout) */
typedef struct __attribute__((packed)) {
    uint32_t input_addr;
    uint32_t weight_addr;
    uint32_t output_addr;
    uint32_t scale_addr;
    uint32_t input_sram;
    uint32_t weight_sram;
    uint32_t output_sram;
    uint32_t scale_sram;
    uint32_t input_size;
    uint32_t weight_size;
    uint32_t output_size;
    uint32_t scale_size;
    uint32_t M, K, N;
} mmul_desc_t;

/* 操作描述符 — SFU */
typedef struct __attribute__((packed)) {
    uint32_t op;           // SFU_OP_*
    uint32_t input_addr;
    uint32_t output_addr;
    uint32_t input_sram;
    uint32_t output_sram;
    uint32_t size;
    uint32_t dim;          // head_dim for ROPE, elements for others
    uint32_t pos;          // position for ROPE
    uint32_t _pad[4];
} sfu_desc_t;

/* 操作描述符 — Vector */
typedef struct __attribute__((packed)) {
    uint32_t op;
    uint32_t a_addr;
    uint32_t b_addr;
    uint32_t o_addr;
    uint32_t dim;
    uint32_t _pad[3];
} vector_desc_t;

/* 操作描述符 — DMA_COPY */
typedef struct __attribute__((packed)) {
    uint32_t src_addr;
    uint32_t dst_addr;
    uint32_t size;
    uint32_t _pad[5];
} dma_copy_desc_t;

/* 完成条目 */
typedef struct __attribute__((packed)) {
    uint32_t cmd_id;
    uint32_t status;       // 0=success, non-zero=error
    uint32_t _pad[6];
} completion_t;

/* ── 全局状态 ────────────────────────────────────────────────────── */

static uint32_t g_cmd_count = 0;

/* ── MMIO 读写原语 ───────────────────────────────────────────────── */

static inline uint32_t mmio_read(uint32_t addr) {
    return *(volatile uint32_t *)addr;
}

static inline void mmio_write(uint32_t addr, uint32_t value) {
    *(volatile uint32_t *)addr = value;
}

/* ── 模块操作 ────────────────────────────────────────────────────── */

static void dma_copy(uint32_t src, uint32_t dst, uint32_t size,
                     int channel) {
    npu_dma_t *dma = NPU_DMA;
    if (channel == 0) {
        dma->CH0_SRC   = src;
        dma->CH0_DST   = dst;
        dma->CH0_SIZE  = size;
        dma->CH0_STRIDE = 0;
    } else {
        dma->CH1_SRC   = src;
        dma->CH1_DST   = dst;
        dma->CH1_SIZE  = size;
        dma->CH1_STRIDE = 0;
    }
    npu_start(&dma->CMD);
    npu_wait_done(&dma->STATUS);
}

static void mxu_wrapper_preload(uint32_t w_addr, uint32_t i_addr,
                                uint32_t o_addr, uint32_t k_tiles,
                                uint32_t dim_n) {
    volatile uint32_t *wrp = (volatile uint32_t *)NPU_MXU_BASE;
    wrp[MXU_WRP_WEIGHT_BASE / 4] = w_addr;
    wrp[MXU_WRP_ACT_BASE / 4]    = i_addr;
    wrp[MXU_WRP_OUT_BASE / 4]    = o_addr;
    wrp[MXU_WRP_K_TILES / 4]     = k_tiles;
    wrp[MXU_WRP_DIM_N / 4]       = dim_n;
    wrp[MXU_WRP_CMD / 4]         = 0x00000001;
    while (!(wrp[MXU_WRP_STATUS / 4] & 0x00000001));
}

static void mxu_start(uint32_t i_addr, uint32_t w_addr, uint32_t o_addr,
                      uint32_t scale_addr,
                      uint32_t M, uint32_t K, uint32_t N,
                      uint32_t ctrl) {
    npu_mxu_t *mxu = NPU_MXU;
    uint32_t k_tiles = (K + 63) / 64;
    mxu_wrapper_preload(w_addr, i_addr, o_addr, k_tiles, N & 0xFFFF);
    mxu->I_ADDR = i_addr;
    mxu->W_ADDR = w_addr;
    mxu->O_ADDR = o_addr;
    mxu->SCALE_ADDR = scale_addr;
    mxu->CTRL   = ctrl & 0xF;
    mxu->DIM0   = (M & 0xFFFF) | ((K & 0xFFFF) << 16);
    mxu->DIM1   = (N & 0xFFFF);
    npu_start(&mxu->CMD);
    npu_wait_done(&mxu->STATUS);
}

/* Map engine-level OpCode to hardware SFU_OP_* value. */
static uint32_t sfu_hw_op(uint32_t opcode) {
    switch (opcode) {
    case 0x01: return 0;
    case 0x02: return 1;
    case 0x03: return 2;
    case 0x04: return 3;
    case 0x06: return 4;
    case 0x05: return 5;
    case 0x17: return 6;
    default:   return 0;
    }
}

static void sfu_start(uint32_t op, uint32_t i_addr, uint32_t o_addr,
                      uint32_t elements, uint32_t dim, uint32_t pos) {
    npu_sfu_t *sfu = NPU_SFU;
    sfu->CTRL   = op & 0xF;
    sfu->I_ADDR = i_addr;
    sfu->O_ADDR = o_addr;
    sfu->DIM    = (elements & 0xFFFF) | ((dim & 0xFFFF) << 16);
    sfu->POS    = pos;
    npu_start(&sfu->CMD);
    npu_wait_done(&sfu->STATUS);
}

static void vec_wrapper_load_a(uint32_t a_addr, uint32_t o_addr,
                               uint32_t elements) {
    volatile uint32_t *wrp = (volatile uint32_t *)NPU_VECTOR_BASE;
    wrp[VEC_WRP_A_BASE / 4] = a_addr;
    wrp[VEC_WRP_O_BASE / 4] = o_addr;
    wrp[VEC_WRP_LEN / 4]    = elements & 0xFFFF;
    wrp[VEC_WRP_CMD / 4]    = 0x00000001;
    while (!(wrp[VEC_WRP_STATUS / 4] & 0x00000001));
}

static void vec_wrapper_load_b(uint32_t b_addr, uint32_t elements) {
    volatile uint32_t *wrp = (volatile uint32_t *)NPU_VECTOR_BASE;
    wrp[VEC_WRP_B_BASE / 4] = b_addr;
    wrp[VEC_WRP_LEN / 4]    = elements & 0xFFFF;
    wrp[VEC_WRP_CMD / 4]    = 0x00000002;
    while (!(wrp[VEC_WRP_STATUS / 4] & 0x00000001));
}

static void vec_wrapper_store_o(uint32_t o_addr, uint32_t elements) {
    volatile uint32_t *wrp = (volatile uint32_t *)NPU_VECTOR_BASE;
    wrp[VEC_WRP_O_BASE / 4] = o_addr;
    wrp[VEC_WRP_LEN / 4]    = elements & 0xFFFF;
    wrp[VEC_WRP_CMD / 4]    = 0x00000004;
    while (!(wrp[VEC_WRP_STATUS / 4] & 0x00000001));
}

static void vector_start(uint32_t op, uint32_t a_addr, uint32_t b_addr,
                         uint32_t o_addr, uint32_t elements) {
    npu_vector_t *vec = NPU_VECTOR;
    vec_wrapper_load_a(a_addr, o_addr, elements);
    vec_wrapper_load_b(b_addr, elements);
    vec->CTRL   = op & 0xF;
    vec->A_ADDR = a_addr;
    vec->B_ADDR = b_addr;
    vec->O_ADDR = o_addr;
    vec->DIM    = elements & 0xFFFF;
    npu_start(&vec->CMD);
    npu_wait_done(&vec->STATUS);
    vec_wrapper_store_o(o_addr, elements);
}

/* ── 描述符读取 ──────────────────────────────────────────────────── */

static void read_mmul_desc(uint32_t desc_addr, mmul_desc_t *desc) {
    volatile uint32_t *src = (volatile uint32_t *)(uintptr_t)desc_addr;
    desc->input_addr  = src[0];
    desc->weight_addr = src[1];
    desc->output_addr = src[2];
    desc->scale_addr  = src[3];
    desc->input_sram  = src[4];
    desc->weight_sram = src[5];
    desc->output_sram = src[6];
    desc->scale_sram  = src[7];
    desc->input_size  = src[8];
    desc->weight_size = src[9];
    desc->output_size = src[10];
    desc->scale_size  = src[11];
    desc->M = src[12];
    desc->K = src[13];
    desc->N = src[14];
}

/* SFU/Vector/DMA descriptors are stored in the same 15-word layout as MMUL
 * (host_write_descriptor).  Extract the relevant fields here.
 */
static void read_sfu_desc(uint32_t desc_addr, sfu_desc_t *desc) {
    volatile uint32_t *src = (volatile uint32_t *)(uintptr_t)desc_addr;
    desc->input_addr  = src[0];
    desc->output_addr = src[2];
    desc->input_sram  = 0x00000000;
    desc->output_sram = 0x00018000;
    desc->dim         = src[8];
    desc->pos         = 0;
}

static void read_vector_desc(uint32_t desc_addr, vector_desc_t *desc) {
    volatile uint32_t *src = (volatile uint32_t *)(uintptr_t)desc_addr;
    desc->a_addr = src[0];
    desc->b_addr = src[1];
    desc->o_addr = src[2];
    desc->dim    = src[8];
}

static void read_dma_copy_desc(uint32_t desc_addr, dma_copy_desc_t *desc) {
    volatile uint32_t *src = (volatile uint32_t *)(uintptr_t)desc_addr;
    desc->src_addr = src[0];
    desc->dst_addr = src[2];
    desc->size     = src[8];
}

/* ── 命令消费 ────────────────────────────────────────────────────── */

static cmd_entry_t read_cmd_entry(uint32_t head) {
    cmd_entry_t entry;
    volatile uint32_t *entry_ptr =
        (volatile uint32_t *)(uintptr_t)(RING_BUF_ADDR + head * CMD_DESC_SIZE);
    entry.opcode    = entry_ptr[0];
    entry.desc_addr = entry_ptr[1];
    entry.flags     = entry_ptr[2];
    return entry;
}

static void write_completion(uint32_t cmd_id, uint32_t status) {
    volatile uint32_t *comp =
        (volatile uint32_t *)(uintptr_t)(COMPLETION_RING_ADDR + cmd_id * 32);
    comp[0] = cmd_id;
    comp[1] = status;
    NPU_DB->COMPLETION_STATUS[cmd_id] = status;
}

static int dispatch_cmd(cmd_entry_t *cmd) {
    uint32_t op = cmd->opcode;

    if (op == 0) {  /* MMUL */
        mmul_desc_t desc;
        read_mmul_desc(cmd->desc_addr, &desc);

        if (desc.M == 0 || desc.K == 0 || desc.N == 0)
            return 1;  /* corrupted descriptor */

        const uint32_t TILE_H = 64;
        const uint32_t TILE_W = 64;
        const uint32_t TILE_WEIGHT_BYTES = TILE_H * TILE_W / 2;
        const uint32_t TILE_SCALE_BYTES  = TILE_W * 4;

        uint32_t act_sram  = 0x00000000;
        uint32_t wbuf[2]   = {0x00010000, 0x00012000};
        uint32_t sbuf[2]   = {0x00014000, 0x00015000};
        uint32_t out_sram  = 0x00018000;

        uint32_t act_sram_abs = NPU_SRAM_BASE + act_sram;

        uint32_t num_blocks = (desc.K + TILE_H - 1) / TILE_H;
        uint32_t num_tiles  = (desc.N + TILE_W - 1) / TILE_W;

        dma_copy(desc.input_addr, act_sram_abs, desc.input_size, 0);

        for (uint32_t n_tile = 0; n_tile < num_tiles; n_tile++) {
            uint32_t n_start = n_tile * TILE_W;
            uint32_t n_end   = (n_start + TILE_W < desc.N) ? (n_start + TILE_W) : desc.N;
            uint32_t tile_width = n_end - n_start;
            uint32_t out_offset = out_sram + n_start * 4;

            for (uint32_t k_block = 0; k_block < num_blocks; k_block++) {
                uint32_t k_start = k_block * TILE_H;
                uint32_t k_end   = (k_start + TILE_H < desc.K) ? (k_start + TILE_H) : desc.K;
                uint32_t block_height = k_end - k_start;

                uint32_t buf_idx = k_block % 2;
                uint32_t w_addr  = wbuf[buf_idx];
                uint32_t s_addr  = sbuf[buf_idx];
                uint32_t w_addr_abs = NPU_SRAM_BASE + w_addr;
                uint32_t s_addr_abs = (desc.scale_size > 0) ? (NPU_SRAM_BASE + s_addr) : 0;

                uint32_t wgt_offset = (n_tile * num_blocks + k_block) * TILE_WEIGHT_BYTES;
                dma_copy(desc.weight_addr + wgt_offset, w_addr_abs, TILE_WEIGHT_BYTES, 0);

                if (desc.scale_size > 0) {
                    uint32_t scale_offset = (n_tile * num_blocks + k_block) * TILE_SCALE_BYTES;
                    dma_copy(desc.scale_addr + scale_offset, s_addr_abs, TILE_SCALE_BYTES, 0);
                }

                uint32_t act_offset     = act_sram + k_start * 64;
                uint32_t act_offset_abs = NPU_SRAM_BASE + act_offset;
                uint32_t out_offset_abs = NPU_SRAM_BASE + out_offset;
                uint32_t accumulate_ctrl = (k_block > 0) ? 4 : 0;

                mxu_start(act_offset_abs, w_addr_abs, out_offset_abs,
                          s_addr_abs, desc.M, block_height, tile_width, accumulate_ctrl);
            }

            dma_copy(NPU_SRAM_BASE + out_offset, desc.output_addr + n_start * 4,
                     desc.M * tile_width * 4, 1);
        }
        return 0;
    }

    if (op == 0x01 || op == 0x02 || op == 0x03 || op == 0x04 ||
        op == 0x05 || op == 0x06 || op == 0x17) {
        sfu_desc_t desc;
        read_sfu_desc(cmd->desc_addr, &desc);

        uint32_t hw_op = sfu_hw_op(op);
        sfu_start(hw_op, desc.input_addr, desc.output_addr, desc.dim, 0, desc.pos);
        return 0;
    }

    if (op >= 0x0F && op <= 0x14) {  /* Vector: VADD/VMUL/VRED_MAX/VRED_SUM/VCONV/VRESID */
        vector_desc_t desc;
        read_vector_desc(cmd->desc_addr, &desc);

        uint32_t hw_op = op - 0x0F;  /* 0x0F..0x14 -> 0..5 */
        vector_start(hw_op, desc.a_addr, desc.b_addr, desc.o_addr, desc.dim);
        return 0;
    }

    if (op == 9 || op == 10 || op == 0x15 || op == 0x16) {  /* DMA_COPY */
        dma_copy_desc_t desc;
        read_dma_copy_desc(cmd->desc_addr, &desc);

        dma_copy(desc.src_addr, desc.dst_addr, desc.size, 0);
        return 0;
    }

    return 1;  /* unknown opcode */
}

/* ── 中断处理 ────────────────────────────────────────────────────── */

static void handle_irq(void) {
    npu_intc_t *intc = NPU_INTC;
    uint32_t pending = intc->PENDING & intc->ENABLE;
    if (pending == 0) return;

    /* 简单模式: ACK 全部 */
    intc->ACK = pending;
}

/* ── 主循环 ──────────────────────────────────────────────────────── */

void firmware_main(void) {
    uint32_t npu_head = NPU_DB->NPU_HEAD;
    NPU_INTC->ENABLE = 0x1FF;

    for (;;) {
        uint32_t host_tail = NPU_DB->HOST_TAIL;

        if (host_tail == npu_head) {
            __asm__ volatile("wfi");
            continue;
        }

        while (npu_head != host_tail) {
            cmd_entry_t cmd = read_cmd_entry(npu_head);
            NPU_DB->LAST_STATUS = 0x100 | (npu_head & 0xFF);
            int status = dispatch_cmd(&cmd);
            NPU_DB->LAST_STATUS = (uint32_t)status;
            write_completion(npu_head, status);
            npu_head = (npu_head + 1) % RING_ENTRIES;
            g_cmd_count++;
        }

        NPU_DB->NPU_HEAD = npu_head;
        NPU_DB->HOST_HEAD = npu_head;
        NPU_INTC->ACK = (1 << 8);
    }
}

/* ── 入口 (由 startup.S 调用) ────────────────────────────────────── */

/* ── 陷阱处理 (最小实现) ─────────────────────────────────────────── */

void __attribute__((interrupt)) trap_handler(void) {
    handle_irq();
}
