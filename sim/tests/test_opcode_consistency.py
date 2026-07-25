"""Consistency tests for EngineOp — single source of truth for host/firmware opcodes.

If any enum value changes, the corresponding test fails, alerting the developer
to update both ``sim/opcode.py`` and ``firmware/npu_firmware.c`` in lockstep.
"""

from opcodes import EngineOp


class TestEngineOpValues:
    """Verifies every EngineOp member matches the firmware dispatch table.

    Reference: firmware/npu_firmware.c dispatch_cmd():
        op == 0                       → MMUL
        op in {0x01,0x02,0x03,0x04,0x06,0x17} → SFU
        op == 0x05                    → ROPE
        op == 0x07                    → PCIe_DMA
        op in {0x09,0x0A,0x15,0x16}  → DMA_COPY
        op in 0x0F..0x14              → Vector
    """

    # ── MMUL ────────────────────────────────────────────────────────

    def test_mmul(self):
        assert EngineOp.MMUL == 0x00

    # ── SFU engine ──────────────────────────────────────────────────

    def test_sfu_base(self):
        assert EngineOp.SFU == 0x01

    def test_sfu_softmax(self):
        assert EngineOp.SFU_SOFTMAX == 0x01

    def test_sfu_layernorm(self):
        assert EngineOp.SFU_LAYERNORM == 0x02

    def test_sfu_gelu(self):
        assert EngineOp.SFU_GELU == 0x03

    def test_sfu_relu(self):
        assert EngineOp.SFU_RELU == 0x04

    def test_sfu_silu(self):
        assert EngineOp.SFU_SILU == 0x06

    def test_sfu_rmsnorm(self):
        assert EngineOp.SFU_RMSNORM == 0x17

    # ── ROPE ────────────────────────────────────────────────────────

    def test_rope(self):
        assert EngineOp.ROPE == 0x05

    # ── PCIe DMA ────────────────────────────────────────────────────

    def test_pcie_dma(self):
        assert EngineOp.PCIE_DMA == 0x07

    # ── DMA copy engine ─────────────────────────────────────────────

    def test_dma_copy(self):
        assert EngineOp.DMA_COPY == 0x09

    def test_dma_st(self):
        assert EngineOp.DMA_ST == 0x0A

    def test_dma_copy_ldd(self):
        assert EngineOp.DMA_COPY_LDD == 0x15

    def test_dma_copy_std(self):
        assert EngineOp.DMA_COPY_STD == 0x16

    # ── Vector engine ───────────────────────────────────────────────

    def test_vector_base(self):
        assert EngineOp.VECTOR == 0x0F

    def test_vadd(self):
        assert EngineOp.VADD == 0x0F

    def test_vmul(self):
        assert EngineOp.VMUL == 0x10

    def test_vred_max(self):
        assert EngineOp.VRED_MAX == 0x11

    def test_vred_sum(self):
        assert EngineOp.VRED_SUM == 0x12

    def test_vconv(self):
        assert EngineOp.VCONV == 0x13

    def test_vresid(self):
        assert EngineOp.VRESID == 0x14

    # ── Round-trip: every member is convertible back ────────────────

    def test_no_duplicate_values(self):
        """No two members share the same value (except the SFU/VEC aliases)."""
        # SFU=0x01 is aliased by SFU_SOFTMAX=0x01 — intentional.
        # VECTOR=0x0F is aliased by VADD=0x0F — intentional.
        values = list(EngineOp)
        seen = {}
        for m in values:
            seen.setdefault(m.value, []).append(m.name)
        dupes = {v: names for v, names in seen.items() if len(names) > 1}
        # Allow known SFU and VECTOR aliases
        allowed_dupes = {1: {"SFU_SOFTMAX", "SFU"}, 15: {"VECTOR", "VADD"}}
        for v, names in dupes.items():
            name_set = set(names)
            if v in allowed_dupes and name_set == allowed_dupes[v]:
                continue
            raise AssertionError(
                f"Duplicate value 0x{v:02X} shared by: {', '.join(names)}"
            )

    # ── Range assertions matching firmware dispatch groups ──────────

    def test_sfu_range(self):
        """0x01..0x04, 0x06, 0x17 all belong to the SFU engine."""
        sfu_ops = {
            EngineOp.SFU_SOFTMAX,
            EngineOp.SFU_LAYERNORM,
            EngineOp.SFU_GELU,
            EngineOp.SFU_RELU,
            EngineOp.SFU_SILU,
            EngineOp.SFU_RMSNORM,
        }
        for op in sfu_ops:
            assert 0x01 <= int(op) <= 0x17 and int(op) not in (0x05, 0x07, 0x09, 0x0A)

    def test_vector_range(self):
        """0x0F..0x14 all belong to the Vector engine."""
        vec_ops = {
            EngineOp.VADD,
            EngineOp.VMUL,
            EngineOp.VRED_MAX,
            EngineOp.VRED_SUM,
            EngineOp.VCONV,
            EngineOp.VRESID,
        }
        for op in vec_ops:
            assert 0x0F <= int(op) <= 0x14
