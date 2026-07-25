"""Shared Host/Firmware opcode contract.

``EngineOp`` is the single source of truth for engine-level opcode numeric values
used by both the Python host (func model / verification) and the C firmware
(``firmware/npu_firmware.c`` dispatch table).

When adding or changing an opcode, update both ``EngineOp`` here and the
corresponding ``switch``/``if-else`` chain in ``firmware/npu_firmware.c``.

Reference C header snippet (keep in sync with ``firmware/npu_firmware.c``):

.. code-block:: c

    // Engine-level opcodes — must match sim/opcode.py EngineOp
    #define ENGINE_OP_MMUL      0x00
    #define ENGINE_OP_SFU_BASE  0x01   // 0x01..0x04, 0x06, 0x17 routed to SFU
    #define ENGINE_OP_ROPE      0x05
    #define ENGINE_OP_PCIE_DMA  0x07
    #define ENGINE_OP_DMA_COPY  0x09   // 0x09, 0x0A, 0x15, 0x16 routed to DMA
    #define ENGINE_OP_VEC_BASE  0x0F   // 0x0F..0x14 routed to Vector engine
"""

from enum import IntEnum


class EngineOp(IntEnum):
    """Engine-level opcode shared between host (Python) and firmware (C).

    Values are the raw 8-bit opcode field from the command descriptor ring buffer.
    The firmware ``dispatch_cmd()`` routes each opcode range to the appropriate
    hardware engine (MMUL, SFU, Vector, DMA, PCIe DMA).
    """

    # ── MMUL (matrix multiply engine) ──────────────────────────────
    MMUL = 0x00

    # ── SFU engine sub-opcodes ─────────────────────────────────────
    # The firmware dispatches opcodes 0x01..0x04, 0x06, 0x17 to the SFU
    # engine via ``sfu_hw_op()`` remapping.
    SFU_SOFTMAX  = 0x01
    SFU_LAYERNORM = 0x02
    SFU_GELU     = 0x03
    SFU_RELU     = 0x04
    SFU_SILU     = 0x06
    SFU_RMSNORM  = 0x17

    # -- SFU alias: generic SFU dispatch uses 0x01 as the canonical value
    SFU = 0x01

    # ── ROPE (dispatched to SFU engine with separate handler) ──────
    ROPE = 0x05

    # ── PCIe DMA (host↔NPU data transfer) ──────────────────────────
    PCIE_DMA = 0x07

    # ── DMA copy engine ────────────────────────────────────────────
    DMA_COPY     = 0x09  # DRAM → SRAM (load)
    DMA_ST       = 0x0A  # SRAM → DRAM (store)
    DMA_COPY_LDD = 0x15  # DMA load (descriptor chain mode)
    DMA_COPY_STD = 0x16  # DMA store (descriptor chain mode)

    # ── Vector engine sub-opcodes ──────────────────────────────────
    VECTOR     = 0x0F  # VADD (canonical vector base / VADD)
    VADD       = 0x0F
    VMUL       = 0x10
    VRED_MAX   = 0x11
    VRED_SUM   = 0x12
    VCONV      = 0x13
    VRESID     = 0x14
