"""Tests for RMSNORM ISA opcode (0x17) — encoding, decoding, and execution."""

import numpy as np

from engine.isa import NPUEncoder, NPUDecoder, NPUInstruction, OpCode
from golden_executor import GoldenExecutor, GoldenSFU


class TestRMSNormOpcode:
    """Unit tests for RMSNORM = 0x17 opcode."""

    def test_opcode_value(self):
        """Smoke: OpCode.RMSNORM is 0x17 (decimal 23)."""
        assert OpCode.RMSNORM == 0x17, f"Expected 0x17, got {OpCode.RMSNORM:#x}"
        assert int(OpCode.RMSNORM) == 23

    def test_encode_decode_roundtrip(self):
        """Encoder/decoder round-trip for an RMSNORM instruction.

        The ISA encodes elements as 3 bits (matching the generic SFU format used by
        SOFTMAX, LAYERNORM, GELU, etc.).  Use a value that fits in 3 bits so the
        encoder→decoder round-trip preserves it exactly.
        Real vectors (e.g. 64 or 2560 elements) are executed via direct
        executor.step() calls, not through encode/decode.
        """
        elements = 5  # fits in 3-bit ISA field (0–7)
        instr = NPUInstruction(
            opcode=OpCode.RMSNORM,
            operands={"sa": 0x100, "da": 0x200, "elements": elements},
        )
        words = NPUEncoder.encode(instr)
        assert len(words) == 1, f"Expected 1 word, got {len(words)}"

        decoded = NPUDecoder.decode(words)
        assert decoded.opcode == OpCode.RMSNORM
        assert decoded.operands["sa"] == 0x100
        assert decoded.operands["da"] == 0x200
        assert decoded.operands["elements"] == elements

    def test_execute_random_fp16_vector(self):
        """Given: a random 64-element FP16 vector written to SRAM.
        When: GoldenExecutor.step() executes an RMSNORM instruction.
        Then: the SRAM output matches GoldenSFU.rmsnorm_hw() within FP16 tolerance.
        """
        rng = np.random.default_rng(42)
        elements = 64
        sa = 0x200000  # activation buffer
        da = 0x2C0000  # SFU I/O buffer

        # Generate random FP16 data
        raw_fp32 = rng.standard_normal(elements, dtype=np.float32) * 2.0
        raw_fp16 = raw_fp32.astype(np.float16)

        # Compute expected golden output
        expected = GoldenSFU.rmsnorm_hw(raw_fp16.astype(np.float32))

        # Set up executor and load data into SRAM
        executor = GoldenExecutor()
        executor.sram.write_float16(sa, raw_fp16)

        # Create and execute RMSNORM instruction
        instr = NPUInstruction(
            opcode=OpCode.RMSNORM,
            operands={"sa": sa, "da": da, "elements": elements},
        )
        executor.step(instr)

        # Read result back from SRAM
        result_fp16 = executor.sram.read_float16(da, elements)

        # Compare within FP16 tolerance
        result_f32 = result_fp16.astype(np.float32)
        expected_f32 = expected.astype(np.float32)
        assert np.allclose(result_f32, expected_f32, atol=1e-3, rtol=1e-2), (
            f"RMSNORM output mismatch:\n"
            f"  max_abs_diff: {np.max(np.abs(result_f32 - expected_f32)):.2e}\n"
            f"  max_rel_diff: {np.max(np.abs(result_f32 - expected_f32) / (np.abs(expected_f32) + 1e-12)):.2e}"
        )

    def test_elements_zero_raises_valueerror(self):
        """Given: an RMSNORM instruction with elements=0.
        When: GoldenExecutor.step() is called.
        Then: ValueError is raised.
        """
        executor = GoldenExecutor()
        instr = NPUInstruction(
            opcode=OpCode.RMSNORM,
            operands={"sa": 0, "da": 0, "elements": 0},
        )
        try:
            executor.step(instr)
            raise AssertionError("Expected ValueError for elements=0")
        except ValueError as e:
            assert "elements" in str(e).lower(), f"Unexpected message: {e}"

    def test_from_mnemonic(self):
        """Smoke: 'rmsnorm' mnemonic maps to OpCode.RMSNORM."""
        assert OpCode.from_mnemonic("rmsnorm") == OpCode.RMSNORM
        assert OpCode.from_mnemonic("RMSNORM") == OpCode.RMSNORM
