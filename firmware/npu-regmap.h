/* NPU MMIO Register Map — Firmware header
 * 与 sim/regmap.py 保持同步。
 */

#ifndef NPU_REGMAP_H
#define NPU_REGMAP_H

#include <stdint.h>

/* ── Base Addresses ─────────────────────────────────────────────── */

#define NPU_MXU_BASE       0x40000000UL
#define NPU_SFU_BASE       0x40001000UL
#define NPU_VECTOR_BASE    0x40002000UL
#define NPU_DMA_BASE       0x40003000UL
#define NPU_PCIE_BASE      0x40004000UL
#define NPU_DOORBELL_BASE  0x40005000UL
#define NPU_INTC_BASE      0x40006000UL
#define NPU_PCIE_DMA_BASE  0x40007000UL
#define NPU_SRAM_BASE      0x20000000UL
#define NPU_SRAM_SIZE      (4 * 1024 * 1024)

/* ── MXU Registers ──────────────────────────────────────────────── */

typedef struct {
    volatile uint32_t CTRL;       /* 0x00: [1:0]=dtype */
    volatile uint32_t CMD;        /* 0x04: bit0=START */
    volatile uint32_t STATUS;     /* 0x08: bit0=BUSY, bit1=DONE */
    volatile uint32_t DIM0;       /* 0x0C: [15:0]=M, [31:16]=K */
    volatile uint32_t DIM1;       /* 0x10: [15:0]=N */
    volatile uint32_t I_ADDR;     /* 0x14: activation SRAM addr */
    volatile uint32_t W_ADDR;     /* 0x18: weight SRAM addr */
    volatile uint32_t O_ADDR;     /* 0x1C: output SRAM addr */
    volatile uint32_t BIAS_ADDR;  /* 0x20: bias addr, 0=none */
    volatile uint32_t SCALE_ADDR; /* 0x24: scale addr, 0=none */
    volatile uint32_t IRQ_EN;     /* 0x28: bit0=irq enable */
} npu_mxu_t;

/* ── SFU Registers ──────────────────────────────────────────────── */

typedef struct {
    volatile uint32_t CTRL;       /* 0x00: [3:0]=OP */
    volatile uint32_t CMD;        /* 0x04: bit0=START */
    volatile uint32_t STATUS;     /* 0x08: bit0=BUSY, bit1=DONE */
    volatile uint32_t I_ADDR;     /* 0x0C: input SRAM addr */
    volatile uint32_t O_ADDR;     /* 0x10: output SRAM addr */
    volatile uint32_t DIM;        /* 0x14: [15:0]=elements */
    volatile uint32_t POS;        /* 0x18: position (ROPE) */
    volatile uint32_t IRQ_EN;     /* 0x1C: bit0=irq enable */
} npu_sfu_t;

#define SFU_OP_SOFTMAX   0
#define SFU_OP_LAYERNORM 1
#define SFU_OP_GELU      2
#define SFU_OP_RELU      3
#define SFU_OP_SILU      4
#define SFU_OP_ROPE      5
#define SFU_OP_RMSNORM   6

/* ── VECTOR Registers ───────────────────────────────────────────── */

typedef struct {
    volatile uint32_t CTRL;       /* 0x00: [3:0]=OP */
    volatile uint32_t CMD;        /* 0x04: bit0=START */
    volatile uint32_t STATUS;     /* 0x08: bit0=BUSY, bit1=DONE */
    volatile uint32_t A_ADDR;     /* 0x0C: operand A SRAM addr */
    volatile uint32_t B_ADDR;     /* 0x10: operand B SRAM addr */
    volatile uint32_t O_ADDR;     /* 0x14: output SRAM addr */
    volatile uint32_t DIM;        /* 0x18: [15:0]=elements */
    volatile uint32_t IRQ_EN;     /* 0x1C: bit0=irq enable */
} npu_vector_t;

#define VEC_OP_ADD   0
#define VEC_OP_MUL   1
#define VEC_OP_MAX   2
#define VEC_OP_SUM   3
#define VEC_OP_CONV  4
#define VEC_OP_RESID 5

#define OP_PCIE_DMA 7

#define MXU_WRP_WEIGHT_BASE 0x30
#define MXU_WRP_ACT_BASE    0x34
#define MXU_WRP_OUT_BASE    0x38
#define MXU_WRP_CMD         0x3C
#define MXU_WRP_STATUS      0x40
#define MXU_WRP_K_TILES     0x44
#define MXU_WRP_DIM_N       0x48

#define VEC_WRP_A_BASE      0x30
#define VEC_WRP_B_BASE      0x34
#define VEC_WRP_O_BASE      0x38
#define VEC_WRP_CMD         0x3C
#define VEC_WRP_STATUS      0x40
#define VEC_WRP_LEN         0x44

/* ── DMA Registers ──────────────────────────────────────────────── */

typedef struct {
    volatile uint32_t CTRL;          /* 0x00 */
    volatile uint32_t CMD;           /* 0x04: bit0=START */
    volatile uint32_t STATUS;        /* 0x08 */
    volatile uint32_t _pad0;         /* 0x0C */
    volatile uint32_t CH0_SRC;       /* 0x10: DRAM src addr */
    volatile uint32_t CH0_DST;       /* 0x14: SRAM dst addr */
    volatile uint32_t CH0_SIZE;      /* 0x18: bytes */
    volatile uint32_t CH0_STRIDE;    /* 0x1C: 2D stride */
    volatile uint32_t CH1_SRC;       /* 0x20: SRAM src addr */
    volatile uint32_t CH1_DST;       /* 0x24: DRAM dst addr */
    volatile uint32_t CH1_SIZE;      /* 0x28: bytes */
    volatile uint32_t CH1_STRIDE;    /* 0x2C: 2D stride */
    volatile uint32_t DESC_ADDR;     /* 0x30: descriptor chain */
    volatile uint32_t DESC_CNT;      /* 0x34: descriptor count */
    volatile uint32_t IRQ_EN;        /* 0x38: bit0=irq enable */
} npu_dma_t;

/* ── PCIe DMA Registers ──────────────────────────────────────────── */

/* NOTE: The full hardware APB map exposes 9 registers (36 bytes).  The
 * doorbell descriptor path only uses the first 8 registers (32 bytes);
 * RD_ERR_CODE and WR_ERR_CODE are status/debug registers.  sizeof() is
 * therefore 36, which exceeds the 32-byte doorbell descriptor budget; the
 * firmware descriptor struct pcie_dma_desc_t is kept separately at 24 bytes.
 */
typedef struct __attribute__((packed)) {
    volatile uint32_t PCIE_CTRL;         /* 0x00: [0]=start_rd, [1]=start_wr,
                                                 [2]=abort, [3]=irq_en */
    volatile uint32_t PCIE_STATUS;       /* 0x04: [0]=rd_busy, [1]=wr_busy,
                                                 [2]=rd_done, [3]=wr_done,
                                                 [4]=error */
    volatile uint32_t PCIE_ADDR_LO;      /* 0x08: PCIe address [31:0] */
    volatile uint32_t PCIE_ADDR_HI;      /* 0x0C: PCIe address [63:32] */
    volatile uint32_t AXI_ADDR;          /* 0x10: Local AXI address */
    volatile uint32_t LEN;               /* 0x14: Transfer length (bytes) */
    volatile uint32_t TAG;               /* 0x18: Descriptor tag */
    volatile uint32_t RD_ERR_CODE;       /* 0x1C: Read error code */
    volatile uint32_t WR_ERR_CODE;       /* 0x20: Write error code */
} npu_pcie_dma_t;                        /* sizeof == 36 bytes */

#define PCIE_DMA_CTRL_START_RD  (1 << 0)
#define PCIE_DMA_CTRL_START_WR  (1 << 1)
#define PCIE_DMA_CTRL_ABORT     (1 << 2)
#define PCIE_DMA_CTRL_IRQ_EN    (1 << 3)

#define PCIE_DMA_STATUS_RD_BUSY (1 << 0)
#define PCIE_DMA_STATUS_WR_BUSY (1 << 1)
#define PCIE_DMA_STATUS_RD_DONE (1 << 2)
#define PCIE_DMA_STATUS_WR_DONE (1 << 3)
#define PCIE_DMA_STATUS_ERROR   (1 << 4)

/* ── Doorbell Registers ─────────────────────────────────────────── */

typedef struct {
    volatile uint32_t HOST_TAIL;              /* 0x00: W: host writes after cmd */
    volatile uint32_t NPU_HEAD;               /* 0x04: R/W: fw consumed pointer */
    volatile uint32_t HOST_HEAD;              /* 0x08: R: host completion ring */
    volatile uint32_t NPU_TAIL;               /* 0x0C: R: host submission ring */
    volatile uint32_t LAST_STATUS;            /* 0x10: R/W: last command status */
    volatile uint32_t COMPLETION_STATUS[16];  /* 0x14: per-ring-index status */
} npu_doorbell_t;

/* ── INTC Registers ─────────────────────────────────────────────── */

typedef struct {
    volatile uint32_t PENDING;    /* 0x00: R: irq pending bits */
    volatile uint32_t ENABLE;     /* 0x04: R/W: irq enable mask */
    volatile uint32_t THRESHOLD;  /* 0x08: R/W: priority threshold */
    volatile uint32_t ACK;        /* 0x0C: W: clear irq */
} npu_intc_t;

#define INTC_MXU     (1 << 0)
#define INTC_SFU     (1 << 1)
#define INTC_VECTOR  (1 << 2)
#define INTC_DMA     (1 << 3)
#define INTC_PCIE    (1 << 4)
#define INTC_HOST    (1 << 5)
#define INTC_TIMER   (1 << 6)

/* ── Module instance pointers ────────────────────────────────────── */

#define NPU_MXU    ((npu_mxu_t *)     NPU_MXU_BASE)
#define NPU_SFU    ((npu_sfu_t *)     NPU_SFU_BASE)
#define NPU_VECTOR ((npu_vector_t *)  NPU_VECTOR_BASE)
#define NPU_DMA      ((npu_dma_t *)       NPU_DMA_BASE)
#define NPU_PCIE_DMA ((npu_pcie_dma_t *)  NPU_PCIE_DMA_BASE)
#define NPU_DB       ((npu_doorbell_t *)  NPU_DOORBELL_BASE)
#define NPU_INTC   ((npu_intc_t *)    NPU_INTC_BASE)

/* ── Helpers ─────────────────────────────────────────────────────── */

static inline void npu_wait_done(volatile uint32_t *status_reg) {
    while (*status_reg & 1)
        __asm__ volatile("" ::: "memory");  /* spin while BUSY */
}

static inline void npu_start(volatile uint32_t *cmd_reg) {
    *cmd_reg = 1;
}

#endif /* NPU_REGMAP_H */
