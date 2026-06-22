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

#define DRAM_BASE  0x80100000UL   // DRAM data (firmware at 0x80000000)
#define DRAM_SIZE  0x0FF00000UL   // ~255 MB
#define SRAM_BASE  0x20000000UL
#define SRAM_SIZE  0x00400000UL   // 4 MB

/* Vector 运算使用的固定 SRAM scratch 区域 */
#define VEC_A_SRAM (SRAM_BASE + 0x000000UL)
#define VEC_B_SRAM (SRAM_BASE + 0x100000UL)
#define VEC_O_SRAM (SRAM_BASE + 0x200000UL)

/* ── Ring Buffer 配置 ────────────────────────────────────────────── */

#define RING_BUF_ADDR       DRAM_BASE   // Ring Buffer 基址 (DRAM 数据区开头)
#define RING_ENTRIES        64
#define CMD_DESC_SIZE       32             // 命令描述符大小 (bytes)

/* 命令描述符结构 (与 Host 约定) */
typedef struct __attribute__((packed)) {
    uint32_t opcode;       // 0=MMUL, 1=SFU, 2=VECTOR, 3=DMA_COPY
    uint32_t desc_addr;    // 操作描述符的 DRAM 地址
    uint32_t flags;        // bit0=中断完成, bit1=立即执行
    uint32_t _pad[5];      // 对齐到 32B
} cmd_entry_t;

/* 操作描述符 — MMUL */
typedef struct __attribute__((packed)) {
    uint32_t input_addr;
    uint32_t weight_addr;
    uint32_t output_addr;
    uint32_t input_sram;
    uint32_t weight_sram;
    uint32_t output_sram;
    uint32_t input_size;
    uint32_t weight_size;
    uint32_t output_size;
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

static uint32_t g_npu_head  = 0;
static uint32_t g_npu_tail  = 0;   // Host 更新后同步过来的
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

static void mxu_start(uint32_t i_addr, uint32_t w_addr, uint32_t o_addr,
                      uint32_t M, uint32_t K, uint32_t N) {
    npu_mxu_t *mxu = NPU_MXU;
    mxu->I_ADDR = i_addr;
    mxu->W_ADDR = w_addr;
    mxu->O_ADDR = o_addr;
    mxu->DIM0   = (M & 0xFFFF) | ((K & 0xFFFF) << 16);
    mxu->DIM1   = (N & 0xFFFF);
    npu_start(&mxu->CMD);
    npu_wait_done(&mxu->STATUS);
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

static void vector_start(uint32_t op, uint32_t a_addr, uint32_t b_addr,
                         uint32_t o_addr, uint32_t elements) {
    npu_vector_t *vec = NPU_VECTOR;
    vec->CTRL   = op & 0xF;
    vec->A_ADDR = a_addr;
    vec->B_ADDR = b_addr;
    vec->O_ADDR = o_addr;
    vec->DIM    = elements & 0xFFFF;
    npu_start(&vec->CMD);
    npu_wait_done(&vec->STATUS);
}

/* ── 描述符读取 ──────────────────────────────────────────────────── */

static void read_mmul_desc(uint32_t desc_addr, mmul_desc_t *desc) {
    volatile uint32_t *src = (volatile uint32_t *)(uintptr_t)desc_addr;
    desc->input_addr  = src[0];
    desc->weight_addr = src[1];
    desc->output_addr = src[2];
    desc->input_sram  = src[3];
    desc->weight_sram = src[4];
    desc->output_sram = src[5];
    desc->input_size  = src[6];
    desc->weight_size = src[7];
    desc->output_size = src[8];
    desc->M = src[9];
    desc->K = src[10];
    desc->N = src[11];
}

static void read_sfu_desc(uint32_t desc_addr, sfu_desc_t *desc) {
    volatile uint32_t *src = (volatile uint32_t *)(uintptr_t)desc_addr;
    desc->op          = src[0];
    desc->input_addr  = src[1];
    desc->output_addr = src[2];
    desc->input_sram  = src[3];
    desc->output_sram = src[4];
    desc->size        = src[5];
    desc->dim         = src[6];
    desc->pos         = src[7];
}

static void read_vector_desc(uint32_t desc_addr, vector_desc_t *desc) {
    volatile uint32_t *src = (volatile uint32_t *)(uintptr_t)desc_addr;
    desc->op     = src[0];
    desc->a_addr = src[1];
    desc->b_addr = src[2];
    desc->o_addr = src[3];
    desc->dim    = src[4];
}

static void read_dma_copy_desc(uint32_t desc_addr, dma_copy_desc_t *desc) {
    volatile uint32_t *src = (volatile uint32_t *)(uintptr_t)desc_addr;
    desc->src_addr = src[0];
    desc->dst_addr = src[1];
    desc->size     = src[2];
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
    /* Completion Ring 紧接 Ring Buffer */
    uint32_t comp_addr = RING_BUF_ADDR + RING_ENTRIES * CMD_DESC_SIZE;
    volatile uint32_t *comp =
        (volatile uint32_t *)(uintptr_t)(comp_addr + cmd_id * 32);
    comp[0] = cmd_id;
    comp[1] = status;
}

static int dispatch_cmd(cmd_entry_t *cmd) {
    switch (cmd->opcode) {
    case 0: {  /* MMUL */
        mmul_desc_t desc;
        read_mmul_desc(cmd->desc_addr, &desc);

        /* DMA: DRAM → SRAM (weight + activation) */
        dma_copy(desc.weight_addr, desc.weight_sram, desc.weight_size, 0);
        dma_copy(desc.input_addr,  desc.input_sram,  desc.input_size,  0);

        /* MXU 计算 */
        mxu_start(desc.input_sram, desc.weight_sram, desc.output_sram,
                  desc.M, desc.K, desc.N);

        /* DMA: SRAM → DRAM (output) */
        dma_copy(desc.output_sram, desc.output_addr, desc.output_size, 1);
        return 0;
    }
    case 1: {  /* SFU */
        sfu_desc_t desc;
        read_sfu_desc(cmd->desc_addr, &desc);

        dma_copy(desc.input_addr, desc.input_sram, desc.size, 0);
        sfu_start(desc.op, desc.input_sram, desc.output_sram,
                  desc.size >> 2, desc.dim, desc.pos);
        dma_copy(desc.output_sram, desc.output_addr, desc.size, 1);
        return 0;
    }
    case 2: {  /* Vector */
        vector_desc_t desc;
        read_vector_desc(cmd->desc_addr, &desc);

        uint32_t size = desc.dim * sizeof(uint32_t);
        dma_copy(desc.a_addr, VEC_A_SRAM, size, 0);
        dma_copy(desc.b_addr, VEC_B_SRAM, size, 0);
        vector_start(desc.op, VEC_A_SRAM, VEC_B_SRAM, VEC_O_SRAM, desc.dim);
        dma_copy(VEC_O_SRAM, desc.o_addr, size, 1);
        return 0;
    }
    case 3: {  /* DMA_COPY */
        dma_copy_desc_t desc;
        read_dma_copy_desc(cmd->desc_addr, &desc);

        dma_copy(desc.src_addr, desc.dst_addr, desc.size, 0);
        return 0;
    }
    default:
        return 1;  /* unknown opcode */
    }
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
    /* 初始化 Doorbell — 等待 Host 设置 HOST_TAIL */
    NPU_DB->NPU_HEAD = 0;

    /* 使能中断 */
    NPU_INTC->ENABLE = 0xFF;  /* 全部使能 */

    /* 主循环 */
    for (;;) {
        /* 读取 Host Tail */
        uint32_t host_tail = NPU_DB->HOST_TAIL;
        uint32_t npu_head  = NPU_DB->NPU_HEAD;

        /* 无新命令 → WFI 等中断 / 轮询 */
        if (host_tail == npu_head) {
            __asm__ volatile("wfi");
            continue;
        }

        /* 消费所有就绪命令 */
        while (npu_head != host_tail) {
            cmd_entry_t cmd = read_cmd_entry(npu_head);
            int status = dispatch_cmd(&cmd);
            write_completion(npu_head, status);
            npu_head = (npu_head + 1) % RING_ENTRIES;
            g_cmd_count++;
        }

        /* 更新 NPU Head → Host 可见 */
        NPU_DB->NPU_HEAD = npu_head;
    }
}

/* ── 入口 (由 startup.S 调用) ────────────────────────────────────── */

/* ── 陷阱处理 (最小实现) ─────────────────────────────────────────── */

void __attribute__((interrupt)) trap_handler(void) {
    handle_irq();
}
