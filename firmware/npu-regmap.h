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
#define NPU_DOORBELL_BASE  0x40010000UL
#define NPU_INTC_BASE      0x40011000UL
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

/* ── Doorbell Registers ─────────────────────────────────────────── */

typedef struct {
    volatile uint32_t HOST_TAIL;  /* 0x00: W: host writes after cmd */
    volatile uint32_t NPU_HEAD;   /* 0x04: R/W: fw consumed pointer */
    volatile uint32_t HOST_HEAD;  /* 0x08: R: host completion ring */
    volatile uint32_t NPU_TAIL;   /* 0x0C: R: host submission ring */
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
#define INTC_HOST    (1 << 8)

/* ── Module instance pointers ────────────────────────────────────── */

#define NPU_MXU    ((npu_mxu_t *)     NPU_MXU_BASE)
#define NPU_SFU    ((npu_sfu_t *)     NPU_SFU_BASE)
#define NPU_VECTOR ((npu_vector_t *)  NPU_VECTOR_BASE)
#define NPU_DMA    ((npu_dma_t *)     NPU_DMA_BASE)
#define NPU_DB     ((npu_doorbell_t *)NPU_DOORBELL_BASE)
#define NPU_INTC   ((npu_intc_t *)    NPU_INTC_BASE)

/* ── Helpers ─────────────────────────────────────────────────────── */

static inline void npu_wait_done(volatile uint32_t *status_reg) {
    while (*status_reg & 1);  /* spin while BUSY */
}

static inline void npu_start(volatile uint32_t *cmd_reg) {
    *cmd_reg = 1;
}

#endif /* NPU_REGMAP_H */
