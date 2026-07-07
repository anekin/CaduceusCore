"""Tests for VCONV_F16_I32: FP16 -> INT32 dtype conversion.

Covers:
  - OpCode value and mnemonic mapping
  - GoldenVector.conv_f16_to_i32 boundary values
  - GoldenExecutor.step() SRAM readback
  - Random FP16 vectors vs numpy reference
"""

import numpy as np
import pytest

from engine.isa import NPUInstruction, OpCode
from golden_executor import GoldenExecutor, GoldenVector


class TestVConvF16I32Opcode:
    """ISA-level tests for VCONV_F16_I32."""

    def test_opcode_value(self):
        assert OpCode.VCONV_F16_I32 == 0x18
        assert int(OpCode.VCONV_F16_I32) == 0x18

    def test_from_mnemonic(self):
        assert OpCode.from_mnemonic("vconv_f16_i32") == OpCode.VCONV_F16_I32
        assert OpCode.from_mnemonic("VCONV_F16_I32") == OpCode.VCONV_F16_I32

    def test_encode_decode_generic_vector(self):
        instr = NPUInstruction(
            opcode=OpCode.VCONV_F16_I32,
            operands={"sa": 0xABC, "da": 0x123, "len": 7},
        )
        from engine.isa import NPUEncoder, NPUDecoder
        words = NPUEncoder.encode(instr)
        decoded = NPUDecoder.decode(words)
        assert decoded.opcode == OpCode.VCONV_F16_I32
        assert decoded.operands["sa"] == 0xABC
        assert decoded.operands["da"] == 0x123
        assert decoded.operands["len"] == 7


class TestGoldenVectorConvF16ToI32:
    """Func Model tests for GoldenVector.conv_f16_to_i32."""

    def test_boundary_values(self):
        boundary = np.array([
            0.0, -0.0, 1.0, -1.0, 1.5, -1.5,
            2.0, -2.0, 0.5, -0.5,
            np.float16(65504.0), np.float16(-65504.0),
            np.float16("inf"), np.float16("-inf"), np.float16("nan"),
            np.float16(1e-8), np.float16(-1e-8),
        ], dtype=np.float16)
        golden = GoldenVector.conv_f16_to_i32(boundary)

        # Finite truncation toward zero
        assert golden[0] == 0
        assert golden[1] == 0
        assert golden[2] == 1
        assert golden[3] == -1
        assert golden[4] == 1
        assert golden[5] == -1
        assert golden[6] == 2
        assert golden[7] == -2
        assert golden[8] == 0
        assert golden[9] == 0

        # Max normal FP16 values fit in INT32 without saturation
        assert golden[10] == 65504
        assert golden[11] == -65504

        # Inf/NaN saturate sign-aware
        assert golden[12] == np.iinfo(np.int32).max
        assert golden[13] == np.iinfo(np.int32).min
        assert golden[14] in (np.iinfo(np.int32).max, np.iinfo(np.int32).min)

        # Subnormals flush to zero
        assert golden[15] == 0
        assert golden[16] == 0

    def test_int32_extremes_as_fp16_input(self):
        # INT32_MAX/MIN are far outside FP16 range -> Inf -> saturate back
        inp = np.array([
            np.float16(np.iinfo(np.int32).max),
            np.float16(np.iinfo(np.int32).min),
        ], dtype=np.float16)
        golden = GoldenVector.conv_f16_to_i32(inp)
        assert golden[0] == np.iinfo(np.int32).max
        assert golden[1] == np.iinfo(np.int32).min

    def test_random_vectors_match_numpy_reference(self):
        rng = np.random.default_rng(42)
        max_err = 0
        for _ in range(100):
            x = rng.uniform(-5000.0, 5000.0, size=128).astype(np.float16)
            golden = GoldenVector.conv_f16_to_i32(x)
            ref = x.astype(np.float32).astype(np.int32)
            err = int(np.max(np.abs(golden.astype(np.int64) - ref.astype(np.int64))))
            if err > max_err:
                max_err = err
        assert max_err == 0, "finite FP16 values must truncate bit-exactly"

    def test_subnormal_flush(self):
        tiny = np.finfo(np.float16).tiny
        x = np.array([tiny / 2, -tiny / 2, tiny, -tiny], dtype=np.float16)
        golden = GoldenVector.conv_f16_to_i32(x)
        assert golden[0] == 0
        assert golden[1] == 0
        assert golden[2] == int(np.float16(tiny))
        assert golden[3] == -int(np.float16(tiny))


class TestGoldenExecutorVConvF16I32:
    """End-to-end Func Model execution tests."""

    def test_execute_fp16_vector_to_int32(self):
        rng = np.random.default_rng(123)
        length = 128
        sa = 0x200000
        da = 0x300000

        inp = rng.uniform(-1000.0, 1000.0, size=length).astype(np.float16)
        expected = GoldenVector.conv_f16_to_i32(inp)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, inp)

        instr = NPUInstruction(
            opcode=OpCode.VCONV_F16_I32,
            operands={"sa": sa, "da": da, "len": length},
        )
        executor.step(instr)

        result = executor.sram.read_int32(da, length)
        assert np.array_equal(result, expected), (
            f"VCONV_F16_I32 output mismatch: "
            f"max_abs_diff={np.max(np.abs(result.astype(np.int64) - expected.astype(np.int64)))}"
        )

    def test_execute_with_special_values(self):
        length = 16
        sa = 0x200000
        da = 0x300000

        inp = np.array([
            0.0, np.float16("inf"), np.float16("-inf"), np.float16("nan"),
            1.5, -1.5, 65504.0, -65504.0,
            np.float16(1e-8), np.float16(-1e-8), 100.0, -100.0,
            0.1, -0.1, 0.9, -0.9,
        ], dtype=np.float16)
        expected = GoldenVector.conv_f16_to_i32(inp)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, inp)

        instr = NPUInstruction(
            opcode=OpCode.VCONV_F16_I32,
            operands={"sa": sa, "da": da, "len": length},
        )
        executor.step(instr)

        result = executor.sram.read_int32(da, length)
        assert np.array_equal(result, expected)

    def test_partial_chunk(self):
        length = 37
        rng = np.random.default_rng(77)
        sa = 0x200000
        da = 0x300000

        inp = rng.uniform(-100.0, 100.0, size=length).astype(np.float16)
        expected = GoldenVector.conv_f16_to_i32(inp)

        executor = GoldenExecutor()
        executor.sram.write_float16(sa, inp)

        instr = NPUInstruction(
            opcode=OpCode.VCONV_F16_I32,
            operands={"sa": sa, "da": da, "len": length},
        )
        executor.step(instr)

        result = executor.sram.read_int32(da, length)
        assert np.array_equal(result, expected)
