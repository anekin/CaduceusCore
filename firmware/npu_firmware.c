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
#define RING_ENTRIES         1024
#define CMD_DESC_SIZE        32
#define COMPLETION_RING_ADDR (DRAM_BASE + RING_ENTRIES * CMD_DESC_SIZE)

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
    uint32_t sfu_op;       // SFU sub-operation (hardware op code)
    uint32_t _pad[3];
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

/* 操作描述符 — PCIe DMA */
typedef struct __attribute__((packed)) {
    uint32_t pcie_addr_lo;   /* PCIe target address [31:0] */
    uint32_t pcie_addr_hi;   /* PCIe target address [63:32] */
    uint32_t axi_addr;       /* Local AXI source/destination */
    uint32_t len;            /* Transfer bytes */
    uint32_t direction;      /* 0=host→NPU (read), 1=NPU→host (write) */
    uint32_t _pad[1];
} pcie_dma_desc_t;

/* 完成条目 */
typedef struct __attribute__((packed)) {
    uint32_t cmd_id;
    uint32_t status;       // 0=success, non-zero=error
    uint32_t _pad[6];
} completion_t;

/* ── 全局状态 ────────────────────────────────────────────────────── */

static uint32_t g_cmd_count = 0;

/* ── MMIO 读写原语 ───────────────────────────────────────────────── */

static inline uint32_t mmio_read(volatile uint32_t *addr) {
    uint32_t v = *addr;
    __asm__ volatile("" ::: "memory");
    return v;
}

static inline void mmio_write(volatile uint32_t *addr, uint32_t value) {
    *addr = value;
    __asm__ volatile("" ::: "memory");
}

/* ── 模块操作 ────────────────────────────────────────────────────── */

/* The on-chip axi_cdma engine truncates single transfers at 64 KiB.
 * Split larger copies into 64 KiB chunks so the full payload lands. */
#define DMA_MAX_CHUNK 32768U

static void dma_copy(uint32_t src, uint32_t dst, uint32_t size,
                     int channel) {
    npu_dma_t *dma = NPU_DMA;
    while (size > 0) {
        uint32_t chunk = size > DMA_MAX_CHUNK ? DMA_MAX_CHUNK : size;
        if (channel == 0) {
            /* Clear CH1_SIZE so the wrapper does not re-run a stale CH1 transfer. */
            dma->CH1_SIZE  = 0;
            dma->CH1_STRIDE = 0;
            dma->CH0_SRC   = src;
            dma->CH0_DST   = dst;
            dma->CH0_SIZE  = chunk;
            dma->CH0_STRIDE = 0;
        } else {
            /* Clear CH0_SIZE so the wrapper does not re-run a stale CH0 transfer. */
            dma->CH0_SIZE  = 0;
            dma->CH0_STRIDE = 0;
            dma->CH1_SRC   = src;
            dma->CH1_DST   = dst;
            dma->CH1_SIZE  = chunk;
            dma->CH1_STRIDE = 0;
        }
        npu_start(&dma->CMD);
        npu_wait_done(&dma->STATUS);
        src += chunk;
        dst += chunk;
        size -= chunk;
    }
}

static uint32_t pcie_dma_exec(uint32_t desc_sram_addr) {
    volatile uint32_t *src = (volatile uint32_t *)(uintptr_t)desc_sram_addr;
    pcie_dma_desc_t desc;
    desc.pcie_addr_lo = src[0];
    desc.pcie_addr_hi = src[1];
    desc.axi_addr     = src[2];
    desc.len          = src[3];
    desc.direction    = src[4];

    npu_pcie_dma_t *pcie = NPU_PCIE_DMA;
    pcie->PCIE_CTRL     = 0;
    pcie->PCIE_ADDR_LO  = desc.pcie_addr_lo;
    pcie->PCIE_ADDR_HI  = desc.pcie_addr_hi;
    pcie->AXI_ADDR      = desc.axi_addr;
    pcie->LEN           = desc.len;
    pcie->TAG           = 0;

    uint32_t start_bit = (desc.direction == 0) ? PCIE_DMA_CTRL_START_RD
                                               : PCIE_DMA_CTRL_START_WR;
    pcie->PCIE_CTRL = PCIE_DMA_CTRL_IRQ_EN | start_bit;

    uint32_t done_bit = (desc.direction == 0) ? PCIE_DMA_STATUS_RD_DONE
                                              : PCIE_DMA_STATUS_WR_DONE;
    uint32_t timeout = 1000000;
    while (timeout--) {
        uint32_t status = pcie->PCIE_STATUS;
        if (status & PCIE_DMA_STATUS_ERROR)
            return 1;
        if (status & done_bit)
            return 0;
    }
    return 1;  /* timeout */
}

static void mxu_wrapper_preload(uint32_t w_addr, uint32_t i_addr,
                                uint32_t o_addr, uint32_t k_tiles,
                                uint32_t dim_n) {
    volatile uint32_t *wrp = (volatile uint32_t *)npu_mxu_base();
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
    uint32_t k_tiles = (K + 63) / 64;
    mxu_wrapper_preload(w_addr, i_addr, o_addr, k_tiles, N & 0xFFFF);
    npu_mxu_t *mxu = NPU_MXU;
    mxu->I_ADDR = i_addr;
    mxu->W_ADDR = w_addr;
    mxu->O_ADDR = o_addr;
    mxu->SCALE_ADDR = scale_addr;
    mxu->CTRL   = ctrl & 0xF;
    mxu->DIM0   = (M & 0xFFFF) | ((K & 0xFFFF) << 16);
    mxu->DIM1   = (N & 0xFFFF);
    npu_start(&mxu->CMD);
    npu_wait_done(&mxu->STATUS);
    /* Give the wrapper store-out FIFO time to drain to SRAM before the
     * caller starts a DMA read of the output tile. */
    for (volatile uint32_t d = 0; d < 2000; d++) __asm__ volatile("nop");
}

#define SFU_SCRATCH_IN  (NPU_SRAM_BASE + 0x80000)
#define SFU_SCRATCH_OUT (NPU_SRAM_BASE + 0x80400)
#define VEC_SCRATCH_A   (NPU_SRAM_BASE + 0x81000)
#define VEC_SCRATCH_B   (NPU_SRAM_BASE + 0x81400)
#define VEC_SCRATCH_O   (NPU_SRAM_BASE + 0x81800)

static uint32_t sfu_scratch_size(uint32_t elements) {
    uint32_t bytes = elements * 2;
    return ((bytes + 511) / 512) * 512;
}

static void sfu_start(uint32_t op, uint32_t i_addr, uint32_t o_addr,
                      uint32_t elements, uint32_t dim, uint32_t pos) {
    npu_sfu_t *sfu = NPU_SFU;
    uint32_t size = sfu_scratch_size(elements);

    NPU_DB->LAST_STATUS = 0x00005000 | (op & 0xFF);
    dma_copy(i_addr, SFU_SCRATCH_IN, size, 0);

    NPU_DB->LAST_STATUS = 0x00005100 | (op & 0xFF);
    sfu->CTRL   = op & 0xF;
    sfu->I_ADDR = SFU_SCRATCH_IN;
    sfu->O_ADDR = SFU_SCRATCH_OUT;
    sfu->DIM    = (elements & 0xFFFF) | ((dim & 0xFFFF) << 16);
    sfu->POS    = pos;

    NPU_DB->LAST_STATUS = 0x00005200 | (op & 0xFF);
    npu_start(&sfu->CMD);
    NPU_DB->LAST_STATUS = 0x00005300 | (op & 0xFF);
    npu_wait_done(&sfu->STATUS);

    NPU_DB->LAST_STATUS = 0x00005400 | (op & 0xFF);
    dma_copy(SFU_SCRATCH_OUT, o_addr, size, 0);

    NPU_DB->LAST_STATUS = 0x00005500 | (op & 0xFF);
}

static void vec_wrapper_load_a(uint32_t a_addr, uint32_t o_addr,
                               uint32_t elements) {
    volatile uint32_t *wrp = (volatile uint32_t *)npu_vector_base();
    wrp[VEC_WRP_A_BASE / 4] = a_addr;
    wrp[VEC_WRP_O_BASE / 4] = o_addr;
    wrp[VEC_WRP_LEN / 4]    = elements & 0xFFFF;
    wrp[VEC_WRP_CMD / 4]    = 0x00000001;
    while (!(wrp[VEC_WRP_STATUS / 4] & 0x00000001));
}

static void vec_wrapper_load_b(uint32_t b_addr, uint32_t elements) {
    volatile uint32_t *wrp = (volatile uint32_t *)npu_vector_base();
    wrp[VEC_WRP_B_BASE / 4] = b_addr;
    wrp[VEC_WRP_LEN / 4]    = elements & 0xFFFF;
    wrp[VEC_WRP_CMD / 4]    = 0x00000002;
    while (!(wrp[VEC_WRP_STATUS / 4] & 0x00000001));
}

static void vec_wrapper_store_o(uint32_t o_addr, uint32_t elements) {
    volatile uint32_t *wrp = (volatile uint32_t *)npu_vector_base();
    wrp[VEC_WRP_O_BASE / 4] = o_addr;
    wrp[VEC_WRP_LEN / 4]    = elements & 0xFFFF;
    wrp[VEC_WRP_CMD / 4]    = 0x00000004;
    while (!(wrp[VEC_WRP_STATUS / 4] & 0x00000001));
}

static uint32_t vector_scratch_size(uint32_t elements) {
    return ((elements + 127) / 128) * 512;
}

static void vector_start(uint32_t op, uint32_t a_addr, uint32_t b_addr,
                         uint32_t o_addr, uint32_t elements) {
    npu_vector_t *vec = NPU_VECTOR;
    uint32_t a_size = vector_scratch_size(elements);
    uint32_t b_size = vector_scratch_size(elements);
    uint32_t o_size = vector_scratch_size(elements);

    NPU_DB->LAST_STATUS = 0x00006000 | (op & 0xFF);
    dma_copy(a_addr, VEC_SCRATCH_A, a_size, 0);
    NPU_DB->LAST_STATUS = 0x00006100 | (op & 0xFF);
    dma_copy(b_addr, VEC_SCRATCH_B, b_size, 0);

    NPU_DB->LAST_STATUS = 0x00006200 | (op & 0xFF);
    vec_wrapper_load_a(VEC_SCRATCH_A, VEC_SCRATCH_O, elements);
    vec_wrapper_load_b(VEC_SCRATCH_B, elements);
    vec->CTRL   = op & 0xF;
    vec->A_ADDR = VEC_SCRATCH_A;
    vec->B_ADDR = VEC_SCRATCH_B;
    vec->O_ADDR = VEC_SCRATCH_O;
    vec->DIM    = elements & 0xFFFF;
    npu_start(&vec->CMD);
    NPU_DB->LAST_STATUS = 0x00006300 | (op & 0xFF);
    npu_wait_done(&vec->STATUS);
    vec_wrapper_store_o(VEC_SCRATCH_O, elements);

    NPU_DB->LAST_STATUS = 0x00006400 | (op & 0xFF);
    dma_copy(VEC_SCRATCH_O, o_addr, o_size, 0);
    NPU_DB->LAST_STATUS = 0x00006500 | (op & 0xFF);
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
    desc->pos         = src[9];
    desc->sfu_op      = src[10];
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
    NPU_DB->LAST_STATUS = 0x00001000 | (op & 0xFF);
    int status = 1;

    if (op == 0) {  /* MMUL */
        mmul_desc_t desc;
        read_mmul_desc(cmd->desc_addr, &desc);

        if (desc.M == 0 || desc.K == 0 || desc.N == 0)
            status = 1;  /* corrupted descriptor */
        else {
            const uint32_t TILE_H = 64;
            const uint32_t TILE_W = 64;
            const uint32_t TILE_WEIGHT_BYTES = TILE_H * TILE_W / 2;
            const uint32_t TILE_SCALE_BYTES  = TILE_W * 4;
            const uint32_t SRAM_ALIGN = 64;

            // Place activation first, then double-buffered weights/scales and
            // output scratch, so large K does not clobber the scratch buffers.
            uint32_t act_sram  = 0x00000000;
            uint32_t act_sram_abs = NPU_SRAM_BASE + act_sram;

            dma_copy(desc.input_addr, act_sram_abs, desc.input_size, 0);

            uint32_t act_end = (act_sram + desc.input_size + SRAM_ALIGN - 1)
                               & ~(SRAM_ALIGN - 1);
            uint32_t wbuf[2]   = {act_end, act_end + TILE_WEIGHT_BYTES};
            uint32_t wbuf_end  = wbuf[1] + TILE_WEIGHT_BYTES;
            uint32_t sbuf[2]   = {(wbuf_end + SRAM_ALIGN - 1) & ~(SRAM_ALIGN - 1),
                                  ((wbuf_end + SRAM_ALIGN - 1) & ~(SRAM_ALIGN - 1))
                                  + TILE_SCALE_BYTES};
            uint32_t sbuf_end  = sbuf[1] + TILE_SCALE_BYTES;
            uint32_t out_sram  = (sbuf_end + SRAM_ALIGN - 1) & ~(SRAM_ALIGN - 1);

            uint32_t num_blocks = (desc.K + TILE_H - 1) / TILE_H;
            uint32_t num_tiles  = (desc.N + TILE_W - 1) / TILE_W;

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
            status = 0;
        }
    } else if (op == 0x01) {  /* SFU — sub-op in descriptor src[10] */
        sfu_desc_t desc;
        NPU_DB->LAST_STATUS = 0x00004000 | (op & 0xFF);
        read_sfu_desc(cmd->desc_addr, &desc);

        NPU_DB->LAST_STATUS = 0x00004100 | (op & 0xFF);
        sfu_start(desc.sfu_op, desc.input_addr, desc.output_addr, desc.dim, 0, desc.pos);
        status = 0;
    } else if (op == 0x05) {  /* ROPE: dim packs (head_dim << 16) | elements */
        sfu_desc_t desc;
        read_sfu_desc(cmd->desc_addr, &desc);

        uint32_t elements  = desc.dim & 0xFFFF;
        uint32_t head_dim  = (desc.dim >> 16) & 0xFFFF;
        sfu_start(5, desc.input_addr, desc.output_addr,
                  elements, head_dim, desc.pos);
        status = 0;
    } else if (op >= 0x0F && op <= 0x14) {  /* Vector: VADD/VMUL/VRED_MAX/VRED_SUM/VCONV/VRESID */
        vector_desc_t desc;
        read_vector_desc(cmd->desc_addr, &desc);

        uint32_t hw_op = op - 0x0F;  /* 0x0F..0x14 -> 0..5 */
        vector_start(hw_op, desc.a_addr, desc.b_addr, desc.o_addr, desc.dim);
        status = 0;
    } else if (op == 7) {  /* PCIe_DMA */
        status = pcie_dma_exec(cmd->desc_addr);
    } else if (op == 9 || op == 10 || op == 0x15 || op == 0x16) {  /* DMA_COPY */
        dma_copy_desc_t desc;
        read_dma_copy_desc(cmd->desc_addr, &desc);

        dma_copy(desc.src_addr, desc.dst_addr, desc.size, 0);
        status = 0;
    } else {
        status = 1;  /* unknown opcode */
    }

    NPU_DB->LAST_STATUS = 0x00002000 | (status & 0xFF);
    return status;
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
    /* Debug: verify completion ring DRAM address is writable. */
    {
        volatile uint32_t *test = (volatile uint32_t *)(uintptr_t)(COMPLETION_RING_ADDR);
        uint32_t mark = 0xDEADBEEF;
        test[0] = mark;
        uint32_t readback = test[0];
        NPU_DB->LAST_STATUS = (readback == mark) ? 0xAA : 0xBB;
    }

    for (;;) {
        uint32_t host_tail = NPU_DB->HOST_TAIL;

        if (host_tail == npu_head) {
            __asm__ volatile("wfi");
            continue;
        }

        while (npu_head != host_tail) {
            cmd_entry_t cmd = read_cmd_entry(npu_head);
            int status = dispatch_cmd(&cmd);
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
