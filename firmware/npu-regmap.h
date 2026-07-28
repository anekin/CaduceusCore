/* NPU MMIO Register Map — Firmware header
 * Now includes the authoritative generated ABI (gen/npu_abi_firmware.h)
 * and retains legacy struct/field naming for backward compatibility.
 *
 * This file is no longer the sole source of truth for base addresses,
 * descriptor sizes, and engine opcodes — those are generated from
 * spec/npu_abi.json via scripts/gen_npu_abi.py.
 *
 * DO NOT hand-edit base address or opcode values here; update the
 * schema and regenerate gen/npu_abi_firmware.h instead.
 */

#ifndef NPU_REGMAP_H
#define NPU_REGMAP_H

/* ── Generated ABI contract (NPU_ABI_* namespace, no struct types) ── */
#include "../gen/npu_abi_firmware.h"

#include <stdint.h>

/* ══════════════════════════════════════════════════════════════════════
 * Base Addresses — legacy aliases to generated constants
 * ══════════════════════════════════════════════════════════════════════ */
#define NPU_MXU_BASE       NPU_ABI_MXU_BASE
#define NPU_SFU_BASE       NPU_ABI_SFU_BASE
#define NPU_VECTOR_BASE    NPU_ABI_VECTOR_BASE
#define NPU_DMA_BASE       NPU_ABI_DMA_BASE
#define NPU_PCIE_BASE      NPU_ABI_PCIE_BASE
#define NPU_DOORBELL_BASE  NPU_ABI_DOORBELL_BASE
#define NPU_INTC_BASE      NPU_ABI_INTC_BASE
#define NPU_PCIE_DMA_BASE  NPU_ABI_PCIE_DMA_BASE
#define NPU_SRAM_BASE      NPU_ABI_SRAM_BASE
#define NPU_SRAM_SIZE      NPU_ABI_SRAM_SIZE

/* ── Engine Opcode Aliases (old names → ABI names) ─────────────────── */
#define OP_PCIE_DMA        NPU_ABI_ENGINE_OP_PCIE_DMA

/* ══════════════════════════════════════════════════════════════════════
 * Struct type definitions — legacy field names preserved for
 * firmware compatibility. Offsets match gen/npu_abi.h structs.
 * ══════════════════════════════════════════════════════════════════════ */

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

/* ── SFU Sub-Opcodes ────────────────────────────────────────────── */

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

/* ── Vector Sub-Opcodes ─────────────────────────────────────────── */

#define VEC_OP_ADD   0
#define VEC_OP_MUL   1
#define VEC_OP_MAX   2
#define VEC_OP_SUM   3     /* ABI name: VEC_OP_SUM_REDUCE */
#define VEC_OP_CONV  4
#define VEC_OP_RESID 5     /* ABI name: VEC_OP_RESID_ADD */

/* ── Wrapper Offsets (SoC internal, not in ABI schema) ──────────── */

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
 *
 * NOTE: gen/npu_abi.h names these fields CTRL/STATUS; legacy firmware
 * code uses PCIE_CTRL/PCIE_STATUS. This struct preserves the legacy names.
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

/* ══════════════════════════════════════════════════════════════════════
 * Compiler-stable module base pointers
 *
 * RISC-V GCC -O2 common-subexpression-eliminates (or otherwise derives)
 * plain integer-cast base pointers, causing some MMIO sequences to compute
 * MXU/SFU/Vector registers from the DMA base (0x40003000) instead of their
 * own base.  The inline-asm loaders below force each base into its own
 * register as an opaque value; GCC cannot CSE across them because it does
 * not see the immediate relationship between the lui results.
 * ══════════════════════════════════════════════════════════════════════ */

static inline npu_mxu_t *npu_mxu_base(void) {
    npu_mxu_t *p;
    __asm__ volatile ("lui %0, 0x40000" : "=r"(p));
    return p;
}

static inline npu_sfu_t *npu_sfu_base(void) {
    npu_sfu_t *p;
    __asm__ volatile ("lui %0, 0x40001" : "=r"(p));
    return p;
}

static inline npu_vector_t *npu_vector_base(void) {
    npu_vector_t *p;
    __asm__ volatile ("lui %0, 0x40002" : "=r"(p));
    return p;
}

static inline npu_dma_t *npu_dma_base(void) {
    npu_dma_t *p;
    __asm__ volatile ("lui %0, 0x40003" : "=r"(p));
    return p;
}

static inline npu_pcie_dma_t *npu_pcie_dma_base(void) {
    npu_pcie_dma_t *p;
    __asm__ volatile ("lui %0, 0x40007" : "=r"(p));
    return p;
}

static inline npu_doorbell_t *npu_doorbell_base(void) {
    npu_doorbell_t *p;
    __asm__ volatile ("lui %0, 0x40005" : "=r"(p));
    return p;
}

static inline npu_intc_t *npu_intc_base(void) {
    npu_intc_t *p;
    __asm__ volatile ("lui %0, 0x40006" : "=r"(p));
    return p;
}

#define NPU_MXU       (npu_mxu_base())
#define NPU_SFU       (npu_sfu_base())
#define NPU_VECTOR    (npu_vector_base())
#define NPU_DMA       (npu_dma_base())
#define NPU_PCIE_DMA  (npu_pcie_dma_base())
#define NPU_DB        (npu_doorbell_base())
#define NPU_INTC      (npu_intc_base())

/* ── Spin-wait helpers ─────────────────────────────────────────── */

static inline void npu_wait_done(volatile uint32_t *status_reg) {
    while (*status_reg & 1)
        __asm__ volatile("" ::: "memory");  /* spin while BUSY */
}

static inline void npu_start(volatile uint32_t *cmd_reg) {
    *cmd_reg = 1;
    __asm__ volatile("" ::: "memory");
}

/* ══════════════════════════════════════════════════════════════════════
 * ABI Consistency Checks (compile-time)
 *
 * These static assertions verify that the legacy macros and struct
 * offsets in this file match the generated ABI contract.  Any mismatch
 * is a build-time error.
 * ══════════════════════════════════════════════════════════════════════ */

_Static_assert(NPU_MXU_BASE      == 0x40000000UL, "ABI: MXU base mismatch");
_Static_assert(NPU_SFU_BASE      == 0x40001000UL, "ABI: SFU base mismatch");
_Static_assert(NPU_VECTOR_BASE   == 0x40002000UL, "ABI: Vector base mismatch");
_Static_assert(NPU_DMA_BASE      == 0x40003000UL, "ABI: DMA base mismatch");
_Static_assert(NPU_DOORBELL_BASE == 0x40005000UL, "ABI: Doorbell base mismatch");
_Static_assert(NPU_INTC_BASE     == 0x40006000UL, "ABI: INTC base mismatch");
_Static_assert(NPU_PCIE_DMA_BASE == 0x40007000UL, "ABI: PCIe DMA base mismatch");
_Static_assert(NPU_SRAM_BASE     == 0x20000000UL, "ABI: SRAM base mismatch");

/* Verify struct layout matches ABI register offsets */
_Static_assert(__builtin_offsetof(npu_mxu_t, CTRL)        == 0x00, "ABI: MXU.CTRL offset");
_Static_assert(__builtin_offsetof(npu_mxu_t, CMD)         == 0x04, "ABI: MXU.CMD offset");
_Static_assert(__builtin_offsetof(npu_mxu_t, STATUS)      == 0x08, "ABI: MXU.STATUS offset");
_Static_assert(__builtin_offsetof(npu_mxu_t, IRQ_EN)      == 0x28, "ABI: MXU.IRQ_EN offset");

_Static_assert(__builtin_offsetof(npu_sfu_t, CTRL)        == 0x00, "ABI: SFU.CTRL offset");
_Static_assert(__builtin_offsetof(npu_sfu_t, POS)         == 0x18, "ABI: SFU.POS offset");

_Static_assert(__builtin_offsetof(npu_vector_t, CTRL)     == 0x00, "ABI: VECTOR.CTRL offset");
_Static_assert(__builtin_offsetof(npu_vector_t, DIM)      == 0x18, "ABI: VECTOR.DIM offset");

_Static_assert(__builtin_offsetof(npu_doorbell_t, HOST_TAIL)    == 0x00, "ABI: DOORBELL.HOST_TAIL offset");
_Static_assert(__builtin_offsetof(npu_doorbell_t, LAST_STATUS) == 0x10, "ABI: DOORBELL.LAST_STATUS offset");
_Static_assert(__builtin_offsetof(npu_doorbell_t, COMPLETION_STATUS) == 0x14, "ABI: DOORBELL.COMPLETION_STATUS offset");

_Static_assert(__builtin_offsetof(npu_intc_t, PENDING)   == 0x00, "ABI: INTC.PENDING offset");
_Static_assert(__builtin_offsetof(npu_intc_t, ACK)       == 0x0C, "ABI: INTC.ACK offset");

_Static_assert(sizeof(npu_pcie_dma_t) == 36, "ABI: PCIe DMA sizeof must be 36 bytes");

/* Known Discrepancy: DOORBELL COMPLETION_STATUS
 * COMPLETION_STATUS is declared as [16] uint32 (64 bytes at offset 0x14)
 * in the ABI schema. Firmware writes COMPLETION_STATUS[cmd_id] where
 * cmd_id ranges up to RING_ENTRIES-1 (1023). RTL doorbell.v implements
 * only LAST_STATUS at 0x10 with no COMPLETION_STATUS array.
 * Resolution TBD in a future ABI revision. */

/* Known Discrepancy: SFU SRAM hardcoding
 * read_sfu_desc() in firmware hardcodes input_sram=0x00000000,
 * output_sram=0x00018000, ignoring descriptor fields [4]/[5].
 * Python host writes valid SRAM values at these offsets.
 * Not an alignment bug but a design inconsistency. */

/* Known Discrepancy: PCIE_DMA sizeof
 * sizeof(npu_pcie_dma_t) == 36 bytes, but the doorbell descriptor path
 * only uses 8 registers (32 bytes). RD_ERR_CODE/WR_ERR_CODE at 0x1C/0x20
 * are status/debug registers outside the descriptor budget. */

#endif /* NPU_REGMAP_H */
