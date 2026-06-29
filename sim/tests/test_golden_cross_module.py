"""Golden cross-module integration tests: XL-01 through XL-03 from sim/testplan.md.

XL-01: MXU INT32 → BF16 → SFU softmax vs float32 reference, error < 1e-4.
XL-02: SFU rope → Vector residual_add full path, bit-exact.
XL-03: INT4 → INT8 → INT32 → BF16 → FP32 end-to-end, max_rel_err < 1e-3.

References
----------
sim/testplan.md lines 109-118 (P4 Cross-module integration)
sim/golden_executor.py — GoldenMXU, GoldenSFU, GoldenVector
"""

import numpy as np
import pytest

from golden_executor import GoldenMXU, GoldenSFU, GoldenVector

SEED = 20260629


# ── Module-level fixtures ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mxu():
    """Single GoldenMXU instance (64x64 broadcast)."""
    return GoldenMXU()


@pytest.fixture(scope="module")
def sfu():
    """Single GoldenSFU instance with LUTs built."""
    return GoldenSFU()


@pytest.fixture(scope="module")
def vec():
    """Single GoldenVector instance."""
    return GoldenVector()


# ══════════════════════════════════════════════════════════════════════
# XL-01: MXU INT32 → BF16 → SFU softmax vs float32 ref, error < 1e-4
# ══════════════════════════════════════════════════════════════════════


class TestXL01MXUBF16Softmax:
    """MXU→SFU quantisation path: INT32 matmul output → BF16 truncation → LUT softmax.

    The BF16 step (conv_i32_to_f16) is the precision bottleneck. The reference
    bypasses BF16 and computes softmax directly from the exact INT32 values.
    """

    @pytest.mark.parametrize("M,K,N", [
        pytest.param(1, 64, 32, id="M1_K64_N32"),
        pytest.param(2, 128, 64, id="M2_K128_N64"),
        pytest.param(4, 64, 48, id="M4_K64_N48"),
        pytest.param(3, 96, 32, id="M3_K96_N32"),
        pytest.param(1, 256, 64, id="M1_K256_N64"),
    ])
    def test_mxu_bf16_softmax_vs_float32_ref(self, mxu, sfu, vec, M, K, N):
        """XL-01: INT32→BF16→softmax vs float32 reference, max_abs_error < 1e-4.

        Path under test (HW):
          1. MXU matmul_int32 → INT32 output (shape M×N)
          2. Vector conv_i32_to_f16 → BF16 (float16 truncation)
          3. SFU softmax_hw → softmax probabilities (LUT-based exp)

        Reference path:
          1. Same INT32 output → float32 (no BF16 truncation)
          2. SFU softmax_ref → reference softmax (float64 kernel → float32)
        """
        rng = np.random.RandomState(SEED + M * 113 + K * 7 + N)

        act = rng.randint(-128, 128, size=M * K, dtype=np.int8)
        w_vals = rng.randint(-8, 8, size=K * N, dtype=np.int8)
        w_packed = mxu.pack_int4(w_vals)

        # HW path: MXU → INT32 → BF16 → softmax_hw
        int32_out = mxu.matmul_int32(act, w_packed, M, K, N)  # (M, N)
        bf16 = vec.conv_i32_to_f16(int32_out)                   # BF16 truncation

        hw_results = np.empty((M, N), dtype=np.float32)
        for i in range(M):
            hw_results[i] = sfu.softmax_hw(bf16[i].astype(np.float32))

        # Reference: INT32 → float32 → softmax_ref
        ref_input = int32_out.astype(np.float32)
        ref_results = np.empty((M, N), dtype=np.float32)
        for i in range(M):
            ref_results[i] = sfu.softmax_ref(ref_input[i].astype(np.float64))

        # Softmax outputs sum to ~1 → max absolute error is meaningful
        abs_diff = np.abs(hw_results.astype(np.float64) - ref_results.astype(np.float64))
        max_err = float(np.max(abs_diff))

        assert max_err < 1e-4, (
            f"XL-01 M={M},K={K},N={N}: max_error={max_err:.2e} >= 1e-4"
        )

        # Verify outputs are valid probabilities
        assert not np.any(np.isnan(hw_results)), "softmax_hw produced NaN"
        assert not np.any(np.isinf(hw_results)), "softmax_hw produced Inf"
        for i in range(M):
            assert abs(float(np.sum(hw_results[i])) - 1.0) < 1e-3, (
                f"Row {i}: softmax sum={np.sum(hw_results[i]):.6f} != 1.0"
            )

    def test_anti_vacuous_bf16_effect(self, mxu, vec):
        """Anti-vacuous: BF16 truncation changes the floating-point values.

        Compares BF16-quantised INT32 values against direct float32 cast.
        For large enough values, the float16 mantissa (11 bits) cannot represent
        all INT32 values exactly. K=512 ensures dot products are large enough
        that BF16 truncation is measurable on the raw input (not softmax output,
        which can be one-hot and mask the difference).
        """
        rng = np.random.RandomState(SEED + 9999)
        # K=512 → dot products up to 512*127*7 ≈ 458K, well beyond float16 exact range
        M, K, N = 1, 512, 16

        act = rng.randint(-128, 128, size=M * K, dtype=np.int8)
        w_vals = rng.randint(-8, 8, size=K * N, dtype=np.int8)
        w_packed = mxu.pack_int4(w_vals)

        int32_out = mxu.matmul_int32(act, w_packed, M, K, N)

        # BF16 path vs direct float32
        bf16_vals = vec.conv_i32_to_f16(int32_out).astype(np.float32)
        direct_vals = int32_out.astype(np.float32)

        # For values this large, BF16 truncation must change at least one value
        assert not np.allclose(bf16_vals, direct_vals, atol=1e-7), (
            "BF16 truncation produced identical float32 values — vacuous"
        )


# ══════════════════════════════════════════════════════════════════════
# XL-02: SFU rope → Vector residual_add full path, bit-exact
# ══════════════════════════════════════════════════════════════════════


class TestXL02RopeResidualAdd:
    """SFU→Vector collaboration: rope_hw rotation → int32 conversion → residual_add.

    The residual_add step is INT32 bit-exact by construction. The full chain
    must be deterministic and the residual_add portion must match the manual
    reference computation exactly.
    """

    @pytest.mark.parametrize("position", [
        pytest.param(0, id="pos0"),
        pytest.param(42, id="pos42"),
        pytest.param(1000, id="pos1000"),
        pytest.param(7777, id="pos7777"),
    ])
    def test_rope_residual_add_deterministic(self, sfu, vec, position):
        """XL-02: rope_hw → int32 → residual_add chain is deterministic and bit-exact.

        Verifies that:
        1. The same rope inputs produce the same residual_add result every time
        2. The residual_add result matches manual int64 computation from the
           same rope output values
        """
        rng = np.random.RandomState(SEED + 200 + position)
        num_heads, head_dim = 4, 128

        q_in = rng.randn(num_heads * head_dim).astype(np.float32) * 0.5
        k_in = rng.randn(2 * head_dim).astype(np.float32) * 0.5

        # Run rope (CORDIC rotation)
        q_rot, _k_rot = sfu.rope_hw(
            q_in.copy(), k_in.copy(), position=position,
            num_heads=num_heads, head_dim=head_dim,
        )

        # Original residual (skip connection, simulating previous layer output)
        original = rng.randn(num_heads * head_dim).astype(np.float32) * 5.0

        # HW chain: rope float32 output → int32 truncation → residual_add
        delta = q_rot.astype(np.int32)
        result = vec.residual_add(original, delta)

        # Reference: manual int32 truncation → int64 add → int32 clip
        orig_i32 = original.astype(np.float32).astype(np.int32)
        expected = np.clip(
            orig_i32.astype(np.int64) + delta.astype(np.int64),
            -2**31, 2**31 - 1,
        ).astype(np.int32)

        assert np.array_equal(result, expected), (
            f"XL-02 pos={position}: residual_add differs from manual reference\n"
            f"  max_diff={np.max(np.abs(result.astype(np.int64) - expected.astype(np.int64)))}"
        )
        assert result.dtype == np.int32, "residual_add output must be int32"

        # Verify determinism: second run produces identical INT32 result
        q_rot2, _k_rot2 = sfu.rope_hw(
            q_in.copy(), k_in.copy(), position=position,
            num_heads=num_heads, head_dim=head_dim,
        )
        delta2 = q_rot2.astype(np.int32)
        result2 = vec.residual_add(original, delta2)

        assert np.array_equal(result, result2), (
            f"XL-02 pos={position}: chain not deterministic — re-run differs"
        )

    def test_anti_vacuous_rope_effect(self, sfu, vec):
        """Anti-vacuous: rope rotation at non-zero position changes the output."""
        rng = np.random.RandomState(SEED + 9998)
        num_heads, head_dim = 4, 128

        q_in = rng.randn(num_heads * head_dim).astype(np.float32) * 0.5
        k_in = rng.randn(2 * head_dim).astype(np.float32) * 0.5
        original = rng.randn(num_heads * head_dim).astype(np.float32) * 5.0

        # Path with significant rope rotation (position=5000)
        q_rot, _ = sfu.rope_hw(
            q_in.copy(), k_in.copy(), position=5000,
            num_heads=num_heads, head_dim=head_dim,
        )
        result_with_rope = vec.residual_add(original, q_rot.astype(np.int32))

        # Path without rope (position=0, near-identity)
        q_rot0, _ = sfu.rope_hw(
            q_in.copy(), k_in.copy(), position=0,
            num_heads=num_heads, head_dim=head_dim,
        )
        result_near_id = vec.residual_add(original, q_rot0.astype(np.int32))

        assert not np.array_equal(result_with_rope, result_near_id), (
            "rope pos=5000 produced same residual_add result as pos=0 — vacuous"
        )


# ══════════════════════════════════════════════════════════════════════
# XL-03: INT4 → INT8 → INT32 → BF16 → FP32 end-to-end, error < 1e-3
# ══════════════════════════════════════════════════════════════════════


class TestXL03QuantE2E:
    """MXU full quantisation path: INT4 weights × INT8 activations → INT32
    accumulate → BF16 truncation → FP32 output.

    The reference uses the same INT32 matmul output cast directly to float32
    (bypassing BF16). The only error source is BF16 truncation. For INT32
    values within float16 exact-mantissa range ([-2048, 2048]), the error
    is zero. The parametrized tests use small K to keep outputs in this range.
    Anti-vacuous tests use larger K to demonstrate measurable BF16 loss.
    """

    @pytest.mark.parametrize("M,K,N,act_range", [
        pytest.param(1, 16, 32, 32, id="M1_K16_N32"),
        pytest.param(2, 16, 32, 32, id="M2_K16_N32"),
        pytest.param(4, 16, 24, 48, id="M4_K16_N24"),
        pytest.param(3, 24, 32, 32, id="M3_K24_N32"),
        pytest.param(1, 32, 16, 64, id="M1_K32_N16"),
    ])
    def test_int4_int8_int32_bf16_fp32_e2e(self, mxu, vec, M, K, N, act_range):
        """XL-03: INT4→INT8→INT32→BF16→FP32 vs same-INT32 float32 ref, max_rel_err < 1e-3.

        HW path:
          1. INT8 activations (range [-act_range, act_range]), packed INT4 weights
          2. matmul_int32 → INT32 accumulator (exact integer dot product)
          3. conv_i32_to_f16 → BF16 (hardware bridge: MXU→SFU)
          4. astype(float32) → FP32 final result

        Reference path (float32, no BF16):
          1. Same INT32 output → astype(float32) directly

        The test uses small K and moderate activation ranges to keep INT32
        output values within float16 exact-mantissa range ([-2048, 2048]),
        where BF16 conversion is lossless.
        """
        rng = np.random.RandomState(SEED + 300 + M * 7 + K * 13 + N)

        act = rng.randint(-act_range, act_range + 1, size=M * K, dtype=np.int8)
        w_vals = rng.randint(-8, 8, size=K * N, dtype=np.int8)
        w_packed = mxu.pack_int4(w_vals)

        # HW path: INT32 matmul → BF16 → FP32
        int32_out = mxu.matmul_int32(act, w_packed, M, K, N)   # (M, N) INT32
        bf16 = vec.conv_i32_to_f16(int32_out)                    # BF16 truncation
        fp32_out = bf16.astype(np.float32)                       # BF16 → FP32

        # Reference: same INT32 → float32 directly (no BF16)
        ref_f32 = int32_out.astype(np.float32)                   # (M, N) float32

        # Error metrics — purely BF16 truncation
        abs_diff = np.abs(fp32_out.astype(np.float64) - ref_f32.astype(np.float64))
        rel_diff = abs_diff / (np.abs(ref_f32.astype(np.float64)) + 1e-12)

        max_abs = float(np.max(abs_diff))
        max_rel = float(np.max(rel_diff))

        passed = max_rel < 1e-3 or max_abs < 1e-3
        assert passed, (
            f"XL-03 M={M},K={K},N={N},act_range={act_range}: "
            f"max_abs_err={max_abs:.2e}, max_rel_err={max_rel:.2e} >= 1e-3"
        )

        # Sanity: all outputs are finite
        assert np.all(np.isfinite(fp32_out)), "BF16→FP32 chain produced non-finite"

        # Verify INT32 outputs are within float16 exact range → BF16 lossless
        i32_abs = np.max(np.abs(int32_out))
        if i32_abs <= 2048:
            assert max_abs <= 1e-7, (
                f"INT32 values within float16 exact range (max|v|={i32_abs}), "
                f"but BF16 truncation introduced error max_abs={max_abs:.4f}"
            )

    def test_anti_vacuous_bf16_loss(self, mxu, vec):
        """Anti-vacuous: BF16 introduces measurable loss for large INT32 values.

        Uses large K to produce INT32 outputs beyond float16 exact-mantissa
        range, proving the BF16 step is not a no-op.
        """
        rng = np.random.RandomState(SEED + 9997)
        M, K, N = 2, 128, 32

        act = rng.randint(-128, 128, size=M * K, dtype=np.int8)
        w_vals = rng.randint(-8, 8, size=K * N, dtype=np.int8)
        w_packed = mxu.pack_int4(w_vals)

        int32_out = mxu.matmul_int32(act, w_packed, M, K, N)

        # Path with BF16 truncation (float16)
        bf16 = vec.conv_i32_to_f16(int32_out)
        fp32_via_bf16 = bf16.astype(np.float32)

        # Path without BF16 (direct INT32 → float32)
        fp32_direct = int32_out.astype(np.float32)

        # BF16 path must differ from direct float32 (measurable truncation)
        assert not np.allclose(fp32_via_bf16, fp32_direct, atol=1e-7), (
            "BF16 path identical to direct float32 — test vacuous"
        )

    def test_anti_vacuous_quant_error(self, mxu):
        """Anti-vacuous: different activations produce different INT32 output."""
        rng = np.random.RandomState(SEED + 9996)
        M, K, N = 2, 64, 16

        w_vals = rng.randint(-8, 8, size=K * N, dtype=np.int8)
        w_packed = mxu.pack_int4(w_vals)

        act1 = rng.randint(-128, 128, size=M * K, dtype=np.int8)
        act2 = rng.randint(-128, 128, size=M * K, dtype=np.int8)

        out1 = mxu.matmul_int32(act1, w_packed, M, K, N)
        out2 = mxu.matmul_int32(act2, w_packed, M, K, N)

        assert not np.array_equal(out1, out2), (
            "Different activations produced same INT32 output — vacuous"
        )
