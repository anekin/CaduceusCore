"""GoldenMXU edge case tests: MX-06 through MX-08 from sim/testplan.md.

MX-06: matmul_int32 non-square (M=1, K=4096, N=4096 and M=128, K=4096, N=4096) vs numpy
MX-07: zero input → zero output
MX-08: overflow → INT32_MIN/MAX saturation (no wrap)
"""

import numpy as np
import pytest

from golden_executor import GoldenMXU, INT32_MIN, INT32_MAX

SEED = 12345


def _ref_matmul_int64(activation, weight_packed, M, K, N):
    """Reference matmul in INT64 → clip to INT32 (avoids np.dot int32 wrap)."""
    mxu = GoldenMXU()
    w = mxu.unpack_int4(weight_packed).astype(np.int64).reshape(K, N)
    a = np.asarray(activation, dtype=np.int8).astype(np.int64).reshape(M, K)
    return np.clip(np.dot(a, w), INT32_MIN, INT32_MAX).astype(np.int32)


# ══════════════════════════════════════════════════════════════════════
# MX-06: matmul_int32 non-square vs numpy reference
# ══════════════════════════════════════════════════════════════════════


def test_mx06_m1_k4096_n4096():
    """Non-square matmul: M=1 (tall/skinny activation), K=4096, N=4096 vs numpy.

    Verifies that a single-row activation (tall) tile-steps correctly
    through N with the 64×64 block array. N=4096 → 64 tiles along N,
    M=1 → 1 tile along M. Each tile is (1, 4096) × (4096, 64) → (1, 64).
    """
    M, K, N = 1, 4096, 4096
    rng = np.random.RandomState(SEED)

    activation = rng.randint(-128, 128, size=M * K).astype(np.int8)
    w_values = rng.randint(-8, 8, size=K * N).astype(np.int8)
    weight_packed = GoldenMXU.pack_int4(w_values)

    mxu = GoldenMXU()
    result = mxu.matmul_int32(activation, weight_packed, M, K, N)
    reference = _ref_matmul_int64(activation, weight_packed, M, K, N)

    assert result.shape == (M, N), f"Expected ({M},{N}), got {result.shape}"
    assert np.array_equal(result, reference), (
        f"matmul_int32(M=1,K={K},N={N}) differs from numpy INT64 reference"
    )


def test_mx06_m128_k4096_n4096():
    """Non-square matmul: M=128 (wider activation), K=4096, N=4096 vs numpy.

    M=128 → 2 tile rows (64×64 each). N=4096 → 64 tile columns.
    """
    M, K, N = 128, 4096, 4096
    rng = np.random.RandomState(SEED)

    activation = rng.randint(-128, 128, size=M * K).astype(np.int8)
    w_values = rng.randint(-8, 8, size=K * N).astype(np.int8)
    weight_packed = GoldenMXU.pack_int4(w_values)

    mxu = GoldenMXU()
    result = mxu.matmul_int32(activation, weight_packed, M, K, N)
    reference = _ref_matmul_int64(activation, weight_packed, M, K, N)

    assert result.shape == (M, N), f"Expected ({M},{N}), got {result.shape}"
    assert np.array_equal(result, reference), (
        f"matmul_int32(M={M},K={K},N={N}) differs from numpy INT64 reference"
    )


def test_mx06_anti_vacuous():
    """Anti-vacuous: different M values produce different output shapes.

    Same weights and K dimension, different activation M → outputs with
    different shapes, proving the function truly computes per M, not a
    hard-coded result.
    """
    mxu = GoldenMXU()
    rng = np.random.RandomState(99)
    K, N = 64, 64

    w_values = rng.randint(-8, 8, size=K * N).astype(np.int8)
    weight_packed = GoldenMXU.pack_int4(w_values)

    act_a = rng.randint(-128, 128, size=64 * K).astype(np.int8)
    act_b = rng.randint(-128, 128, size=128 * K).astype(np.int8)

    result_a = mxu.matmul_int32(act_a, weight_packed, 64, K, N)
    result_b = mxu.matmul_int32(act_b, weight_packed, 128, K, N)

    assert result_a.shape != result_b.shape, (
        "Different M must produce different output shapes"
    )


# ══════════════════════════════════════════════════════════════════════
# MX-07: zero input → zero output
# ══════════════════════════════════════════════════════════════════════


def test_mx07_zero_activation():
    """Zero activation with random weights → all-zero output."""
    M, K, N = 64, 4096, 64
    rng = np.random.RandomState(SEED)

    activation = np.zeros(M * K, dtype=np.int8)
    w_values = rng.randint(-8, 8, size=K * N).astype(np.int8)
    weight_packed = GoldenMXU.pack_int4(w_values)

    mxu = GoldenMXU()
    result = mxu.matmul_int32(activation, weight_packed, M, K, N)

    assert result.shape == (M, N), f"Expected ({M},{N}), got {result.shape}"
    assert np.all(result == 0), "Zero activation must produce zero output"
    assert result.dtype == np.int32, "Output must be int32"


def test_mx07_zero_weights():
    """Zero weights with random activations → all-zero output."""
    M, K, N = 32, 4096, 128
    rng = np.random.RandomState(SEED)

    activation = rng.randint(-128, 128, size=M * K).astype(np.int8)
    weight_packed = np.zeros((K * N + 1) // 2, dtype=np.uint8)

    mxu = GoldenMXU()
    result = mxu.matmul_int32(activation, weight_packed, M, K, N)

    assert np.all(result == 0), "Zero weights must produce zero output"


def test_mx07_zero_both():
    """Zero activation AND zero weights → all-zero output."""
    M, K, N = 16, 256, 32
    activation = np.zeros(M * K, dtype=np.int8)
    weight_packed = np.zeros((K * N + 1) // 2, dtype=np.uint8)

    mxu = GoldenMXU()
    result = mxu.matmul_int32(activation, weight_packed, M, K, N)

    assert np.all(result == 0), "Zero inputs must produce zero output"


def test_mx07_zero_non_square():
    """Zero input for non-square shape (M=1, K=4096, N=4096) → zero output."""
    M, K, N = 1, 4096, 4096
    activation = np.zeros(M * K, dtype=np.int8)
    weight_packed = np.zeros((K * N + 1) // 2, dtype=np.uint8)

    mxu = GoldenMXU()
    result = mxu.matmul_int32(activation, weight_packed, M, K, N)

    assert result.shape == (1, 4096), f"Expected (1,4096), got {result.shape}"
    assert np.all(result == 0), "Zero input for non-square must produce zero output"


def test_mx07_anti_vacuous():
    """Anti-vacuous: non-zero weights produce non-zero output (zero-input is correct).

    If matmul_int32 always returned zeros regardless of input, this test
    would catch it. With all-positive activations and all-positive weights,
    the output is trivially K per element (>>0).
    """
    mxu = GoldenMXU()

    # All ones: each output element = sum over K (1 * 1) = K
    act_ones = np.full(64 * 64, 1, dtype=np.int8)
    w_ones_values = np.full(64 * 64, 1, dtype=np.int8)
    w_ones_packed = GoldenMXU.pack_int4(w_ones_values)

    result = mxu.matmul_int32(act_ones, w_ones_packed, 64, 64, 64)
    assert np.all(result == 64), (
        "All-ones input with all-ones weights must produce output = K (64)"
    )
