// ABI Layout Test — Verify generated C/C++ bindings match hardware contract.
// Compiles against gen/npu_abi.h and validates struct sizes and field offsets
// via static_assert and offsetof.
//
// Build (standalone):
//   g++ -std=c++17 -Igen software/tests/test_abi_layout.cpp -o /dev/null
//
// Build (CMake):
//   cmake -S software -B build/software -DCADUCEUS_BUILD_TESTS=ON
//   cmake --build build/software

#include <cstddef>
#include <cstdint>
#include "../gen/npu_abi.h"

// ─── Register Struct Size Checks ────────────────────────────────────

static_assert(sizeof(npu_mxu_t) == 11 * 4,
              "ABI: MXU register struct must be 11 words");
static_assert(sizeof(npu_sfu_t) == 8 * 4,
              "ABI: SFU register struct must be 8 words");
static_assert(sizeof(npu_vector_t) == 8 * 4,
              "ABI: VECTOR register struct must be 8 words");

// DOORBELL: 5 scalar fields (20 bytes) + COMPLETION_STATUS[16] (64 bytes) = 84
static_assert(sizeof(npu_doorbell_t) == 5 * 4 + 16 * 4,
              "ABI: DOORBELL register struct must be 84 bytes");

static_assert(sizeof(npu_intc_t) == 4 * 4,
              "ABI: INTC register struct must be 4 words");
static_assert(sizeof(npu_pcie_dma_t) == 36,
              "ABI: PCIE_DMA register struct must be 36 bytes (9 registers)");

// DMA: 3 control + 1 pad (4B) + 11 data regs = 15 words = 60 bytes
static_assert(sizeof(npu_dma_t) == 15 * 4,
              "ABI: DMA register struct must be 60 bytes");

// ─── Struct Field Offset Checks ─────────────────────────────────────

// MXU offsets
static_assert(offsetof(npu_mxu_t, CTRL)   == 0x00, "ABI: MXU.CTRL offset");
static_assert(offsetof(npu_mxu_t, CMD)    == 0x04, "ABI: MXU.CMD offset");
static_assert(offsetof(npu_mxu_t, STATUS) == 0x08, "ABI: MXU.STATUS offset");
static_assert(offsetof(npu_mxu_t, DIM0)   == 0x0C, "ABI: MXU.DIM0 offset");
static_assert(offsetof(npu_mxu_t, DIM1)   == 0x10, "ABI: MXU.DIM1 offset");
static_assert(offsetof(npu_mxu_t, I_ADDR) == 0x14, "ABI: MXU.I_ADDR offset");
static_assert(offsetof(npu_mxu_t, W_ADDR) == 0x18, "ABI: MXU.W_ADDR offset");
static_assert(offsetof(npu_mxu_t, O_ADDR) == 0x1C, "ABI: MXU.O_ADDR offset");
static_assert(offsetof(npu_mxu_t, BIAS_ADDR)  == 0x20, "ABI: MXU.BIAS_ADDR offset");
static_assert(offsetof(npu_mxu_t, SCALE_ADDR) == 0x24, "ABI: MXU.SCALE_ADDR offset");
static_assert(offsetof(npu_mxu_t, IRQ_EN) == 0x28, "ABI: MXU.IRQ_EN offset");

// DMA offsets (gen header includes _pad_000c between STATUS and CH0_SRC)
static_assert(offsetof(npu_dma_t, CTRL)        == 0x00, "ABI: DMA.CTRL offset");
static_assert(offsetof(npu_dma_t, CMD)         == 0x04, "ABI: DMA.CMD offset");
static_assert(offsetof(npu_dma_t, STATUS)      == 0x08, "ABI: DMA.STATUS offset");
static_assert(offsetof(npu_dma_t, CH0_SRC)     == 0x10, "ABI: DMA.CH0_SRC offset");
static_assert(offsetof(npu_dma_t, CH0_DST)     == 0x14, "ABI: DMA.CH0_DST offset");
static_assert(offsetof(npu_dma_t, CH0_SIZE)    == 0x18, "ABI: DMA.CH0_SIZE offset");
static_assert(offsetof(npu_dma_t, CH0_STRIDE)  == 0x1C, "ABI: DMA.CH0_STRIDE offset");
static_assert(offsetof(npu_dma_t, IRQ_EN)      == 0x38, "ABI: DMA.IRQ_EN offset");

// SFU offsets
static_assert(offsetof(npu_sfu_t, CTRL)   == 0x00, "ABI: SFU.CTRL offset");
static_assert(offsetof(npu_sfu_t, CMD)    == 0x04, "ABI: SFU.CMD offset");
static_assert(offsetof(npu_sfu_t, STATUS) == 0x08, "ABI: SFU.STATUS offset");
static_assert(offsetof(npu_sfu_t, I_ADDR) == 0x0C, "ABI: SFU.I_ADDR offset");
static_assert(offsetof(npu_sfu_t, O_ADDR) == 0x10, "ABI: SFU.O_ADDR offset");
static_assert(offsetof(npu_sfu_t, DIM)    == 0x14, "ABI: SFU.DIM offset");
static_assert(offsetof(npu_sfu_t, POS)    == 0x18, "ABI: SFU.POS offset");
static_assert(offsetof(npu_sfu_t, IRQ_EN) == 0x1C, "ABI: SFU.IRQ_EN offset");

// VECTOR offsets
static_assert(offsetof(npu_vector_t, CTRL)   == 0x00, "ABI: VECTOR.CTRL offset");
static_assert(offsetof(npu_vector_t, CMD)    == 0x04, "ABI: VECTOR.CMD offset");
static_assert(offsetof(npu_vector_t, STATUS) == 0x08, "ABI: VECTOR.STATUS offset");
static_assert(offsetof(npu_vector_t, A_ADDR) == 0x0C, "ABI: VECTOR.A_ADDR offset");
static_assert(offsetof(npu_vector_t, B_ADDR) == 0x10, "ABI: VECTOR.B_ADDR offset");
static_assert(offsetof(npu_vector_t, O_ADDR) == 0x14, "ABI: VECTOR.O_ADDR offset");
static_assert(offsetof(npu_vector_t, DIM)    == 0x18, "ABI: VECTOR.DIM offset");
static_assert(offsetof(npu_vector_t, IRQ_EN) == 0x1C, "ABI: VECTOR.IRQ_EN offset");

// DOORBELL offsets
static_assert(offsetof(npu_doorbell_t, HOST_TAIL)          == 0x00, "ABI: DOORBELL.HOST_TAIL offset");
static_assert(offsetof(npu_doorbell_t, NPU_HEAD)           == 0x04, "ABI: DOORBELL.NPU_HEAD offset");
static_assert(offsetof(npu_doorbell_t, HOST_HEAD)          == 0x08, "ABI: DOORBELL.HOST_HEAD offset");
static_assert(offsetof(npu_doorbell_t, NPU_TAIL)           == 0x0C, "ABI: DOORBELL.NPU_TAIL offset");
static_assert(offsetof(npu_doorbell_t, LAST_STATUS)        == 0x10, "ABI: DOORBELL.LAST_STATUS offset");
static_assert(offsetof(npu_doorbell_t, COMPLETION_STATUS)  == 0x14, "ABI: DOORBELL.COMPLETION_STATUS offset");

// PCIE_DMA offsets
static_assert(offsetof(npu_pcie_dma_t, CTRL)         == 0x00, "ABI: PCIE_DMA.CTRL offset");
static_assert(offsetof(npu_pcie_dma_t, STATUS)       == 0x04, "ABI: PCIE_DMA.STATUS offset");
static_assert(offsetof(npu_pcie_dma_t, PCIE_ADDR_LO) == 0x08, "ABI: PCIE_DMA.PCIE_ADDR_LO offset");
static_assert(offsetof(npu_pcie_dma_t, PCIE_ADDR_HI) == 0x0C, "ABI: PCIE_DMA.PCIE_ADDR_HI offset");
static_assert(offsetof(npu_pcie_dma_t, AXI_ADDR)     == 0x10, "ABI: PCIE_DMA.AXI_ADDR offset");
static_assert(offsetof(npu_pcie_dma_t, LEN)          == 0x14, "ABI: PCIE_DMA.LEN offset");
static_assert(offsetof(npu_pcie_dma_t, TAG)          == 0x18, "ABI: PCIE_DMA.TAG offset");
static_assert(offsetof(npu_pcie_dma_t, RD_ERR_CODE)  == 0x1C, "ABI: PCIE_DMA.RD_ERR_CODE offset");
static_assert(offsetof(npu_pcie_dma_t, WR_ERR_CODE)  == 0x20, "ABI: PCIE_DMA.WR_ERR_CODE offset");

// ─── Address Cross-Check ────────────────────────────────────────────

static_assert(NPU_MXU_BASE      == 0x40000000UL, "ABI: MXU base address");
static_assert(NPU_SFU_BASE      == 0x40001000UL, "ABI: SFU base address");
static_assert(NPU_VECTOR_BASE   == 0x40002000UL, "ABI: VECTOR base address");
static_assert(NPU_DMA_BASE      == 0x40003000UL, "ABI: DMA base address");
static_assert(NPU_DOORBELL_BASE == 0x40005000UL, "ABI: DOORBELL base address");
static_assert(NPU_INTC_BASE     == 0x40006000UL, "ABI: INTC base address");
static_assert(NPU_PCIE_DMA_BASE == 0x40007000UL, "ABI: PCIE_DMA base address");
static_assert(NPU_SRAM_BASE     == 0x20000000UL, "ABI: SRAM base address");
static_assert(NPU_DRAM_BASE     == 0x80000000UL, "ABI: DRAM base address");

// ─── Opcode Consistency ────────────────────────────────────────────

static_assert(NPU_ENGINE_OP_MMUL         == 0,  "ABI: MMUL opcode");
static_assert(NPU_ENGINE_OP_SFU_SOFTMAX  == 1,  "ABI: SFU_SOFTMAX opcode");
static_assert(NPU_ENGINE_OP_SFU_RMSNORM  == 23, "ABI: SFU_RMSNORM opcode");
static_assert(NPU_ENGINE_OP_PCIE_DMA     == 7,  "ABI: PCIE_DMA opcode");

// ─── Capability and Constant Checks ────────────────────────────────

static_assert(NPU_CAP_MXU_SUPPORTED    == (1U << 0),  "ABI: CAP_MXU");
static_assert(NPU_CAP_SFU_SUPPORTED    == (1U << 1),  "ABI: CAP_SFU");
static_assert(NPU_CAP_DESCRIPTOR_CHAIN == (1U << 13), "ABI: CAP_DESCRIPTOR_CHAIN");

static_assert(NPU_RING_ENTRIES          == 1024, "ABI: RING_ENTRIES");
static_assert(NPU_CMD_ENTRY_SIZE        == 32,   "ABI: CMD_ENTRY_SIZE");
static_assert(NPU_COMPLETION_ENTRY_SIZE == 32,   "ABI: COMPLETION_ENTRY_SIZE");

// ─── Negative test helper: proves a mutated layout is caught ───────
// This function is never called; it exists so the negative test can
// copy this file, mutate a value, and verify the compiler rejects it.

static void abi_mutation_detector(void) {
    (void)(uintptr_t)sizeof(npu_mxu_t);
    (void)(uintptr_t)sizeof(npu_sfu_t);
    (void)(uintptr_t)sizeof(npu_vector_t);
    (void)(uintptr_t)sizeof(npu_dma_t);
    (void)(uintptr_t)sizeof(npu_doorbell_t);
    (void)(uintptr_t)sizeof(npu_intc_t);
    (void)(uintptr_t)sizeof(npu_pcie_dma_t);
}

int main(void) {
    // All static_assert above are the real tests.
    // main() exists to satisfy linker requirements.
    abi_mutation_detector();
    return 0;
}
