"""Func Model L2 signoff: true op chains with automatic dtype conversion.

Each test drives two or three compute ops back-to-back through SRAM.  The
GoldenExecutor automatically inserts VCONV / VCONV_F16_I32 instructions when
adjacent op dtypes mismatch, so the output of op N feeds directly into op N+1
without any external hex preload.

Coverage:
  - INT32 -> FP16 : MMUL -> SOFTMAX
  - FP16  -> INT32: SOFTMAX -> VRESID
  - FP16  -> INT8 : GELU -> MMUL
"""

import numpy as np
import pytest

from engine.isa import NPUInstruction, OpCode
from golden_executor import GoldenExecutor, GoldenMXU, GoldenSFU, GoldenVector


SEED = 20260708


def _pack_int4(values: np.ndarray) -> np.ndarray:
    return GoldenMXU.pack_int4(values)


class TestChainInt32ToFp16:
    """INT32 -> FP16 closure: MMUL output drives an SFU op via VCONV."""

    def test_mmul_softmax_chain(self):
        """MMUL (INT32 out) -> VCONV -> SOFTMAX (FP16 in) passes with no hex preload."""
        rng = np.random.RandomState(SEED)
        M, K, N = 1, 64, 32
        length = M * N

        act = rng.randint(-32, 32, size=M * K, dtype=np.int8)
        w_vals = rng.randint(-7, 7, size=K * N, dtype=np.int8)
        w_packed = _pack_int4(w_vals)

        act_addr = 0x200000
        wgt_addr = 0x210000
        mmul_out_addr = 0x280000
        sfu_in_addr = 0x2C0000
        sfu_out_addr = 0x2C1000

        executor = GoldenExecutor()
        executor.sram.write_int8(act_addr, act)
        executor.sram.write_bytes(wgt_addr, w_packed)

        program = [
            NPUInstruction(
                OpCode.MMUL,
                {"wa": wgt_addr, "ia": act_addr, "oa": mmul_out_addr,
                 "M": M, "K": K, "N": N},
            ),
            NPUInstruction(
                OpCode.SOFTMAX,
                {"sa": sfu_in_addr, "da": sfu_out_addr, "len": length},
            ),
        ]

        executor.run_op_chain(program)

        mmul_ref = executor.mxu.matmul_int32(act, w_packed, M, K, N)
        expected = executor.sfu.softmax_hw(GoldenVector.conv_i32_to_f16(mmul_ref))
        actual = executor.sram.read_float16(sfu_out_addr, length)

        assert np.allclose(actual, expected, atol=1e-4, rtol=1e-3), (
            f"MMUL->SOFTMAX chain mismatch: max_abs={np.max(np.abs(actual - expected)):.2e}"
        )


class TestChainFp16ToInt32:
    """FP16 -> INT32 closure: SFU output drives a Vector INT32 op via VCONV_F16_I32."""

    def test_softmax_vresid_chain(self):
        """SOFTMAX (FP16 out) -> VCONV_F16_I32 -> VRESID (INT32 delta) passes."""
        rng = np.random.RandomState(SEED + 1)
        length = 64

        inp = rng.randn(length).astype(np.float32)
        residual = rng.randn(length).astype(np.float32)

        in_addr = 0x2C0000
        softmax_out_addr = 0x2C1000
        residual_addr = 0x2C2000
        vresid_out_addr = 0x2C3000

        executor = GoldenExecutor()
        executor.sram.write_float16(in_addr, inp.astype(np.float16))
        executor.sram.write_float16(residual_addr, residual.astype(np.float16))

        program = [
            NPUInstruction(
                OpCode.SOFTMAX,
                {"sa": in_addr, "da": softmax_out_addr, "len": length},
            ),
            NPUInstruction(
                OpCode.VRESID,
                {"sa": residual_addr, "sb": 0, "da": vresid_out_addr, "len": length},
            ),
        ]

        executor.run_op_chain(program)

        softmax_ref = executor.sfu.softmax_hw(inp)
        delta = GoldenVector.conv_f16_to_i32(softmax_ref.astype(np.float16))
        expected = GoldenVector.residual_add(residual, delta)
        actual = executor.sram.read_int32(vresid_out_addr, length)

        assert np.array_equal(actual, expected), (
            f"SOFTMAX->VRESID chain mismatch: max_diff="
            f"{np.max(np.abs(actual.astype(np.int64) - expected.astype(np.int64)))}"
        )


class TestChainFp16ToInt8:
    """FP16 -> INT8 closure: SFU output drives MMUL activation via VCONV_F16_I32 + clip."""

    def test_gelu_mmul_chain(self):
        """GELU (FP16 out) -> VCONV_F16_I32 -> INT8 clip -> MMUL (INT8 in) passes."""
        rng = np.random.RandomState(SEED + 2)
        K, N = 64, 16
        M = 1
        gelu_in = rng.uniform(-2.0, 2.0, size=K).astype(np.float32)

        w_vals = rng.randint(-7, 7, size=K * N, dtype=np.int8)
        w_packed = _pack_int4(w_vals)

        gelu_in_addr = 0x2C0000
        gelu_out_addr = 0x2C1000
        wgt_addr = 0x210000
        mmul_out_addr = 0x280000

        executor = GoldenExecutor()
        executor.sram.write_float16(gelu_in_addr, gelu_in.astype(np.float16))
        executor.sram.write_bytes(wgt_addr, w_packed)

        program = [
            NPUInstruction(
                OpCode.GELU,
                {"sa": gelu_in_addr, "da": gelu_out_addr, "len": K},
            ),
            NPUInstruction(
                OpCode.MMUL,
                {"wa": wgt_addr, "ia": 0, "oa": mmul_out_addr,
                 "M": M, "K": K, "N": N},
            ),
        ]

        executor.run_op_chain(program)

        gelu_ref = executor.sfu.gelu_hw(gelu_in).astype(np.float16)
        act_int32 = GoldenVector.conv_f16_to_i32(gelu_ref)
        act_int8 = np.clip(act_int32, -128, 127).astype(np.int8)
        expected = executor.mxu.matmul_int32(act_int8.reshape(M, K), w_packed, M, K, N)
        actual = executor.sram.read_int32(mmul_out_addr, M * N)

        assert np.array_equal(actual, expected.flatten()), (
            f"GELU->MMUL chain mismatch: max_diff="
            f"{np.max(np.abs(actual.astype(np.int64) - expected.flatten().astype(np.int64)))}"
        )


class TestChainNoConversionWhenDtypesMatch:
    """Matching adjacent dtypes must not insert spurious converters."""

    def test_gelu_softmax_chain(self):
        """GELU -> SOFTMAX (both FP16) should execute without inserted VCONV."""
        rng = np.random.RandomState(SEED + 3)
        length = 64
        inp = rng.uniform(-2.0, 2.0, size=length).astype(np.float32)

        in_addr = 0x2C0000
        gelu_out_addr = 0x2C1000
        softmax_out_addr = 0x2C2000

        executor = GoldenExecutor()
        executor.sram.write_float16(in_addr, inp.astype(np.float16))

        program = [
            NPUInstruction(OpCode.GELU, {"sa": in_addr, "da": gelu_out_addr, "len": length}),
            NPUInstruction(OpCode.SOFTMAX, {"sa": gelu_out_addr, "da": softmax_out_addr, "len": length}),
        ]

        trace = executor.run_op_chain(program)

        expected = executor.sfu.softmax_hw(executor.sfu.gelu_hw(inp))
        actual = executor.sram.read_float16(softmax_out_addr, length)

        assert len(trace) == 2, f"Expected 2 executed steps, got {len(trace)}"
        assert np.allclose(actual, expected, atol=1e-4, rtol=1e-3), (
            f"GELU->SOFTMAX chain mismatch: max_abs={np.max(np.abs(actual - expected)):.2e}"
        )
