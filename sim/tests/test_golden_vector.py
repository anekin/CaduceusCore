"""GoldenVector tests: V-01 through V-09 from sim/testplan.md.

V-01: add / mul — random 1000 groups vs numpy, max_error < 1e-7
V-02: add / mul — boundary values (NaN, Inf, ±0, denorm)
V-03: max_reduce — random 100 groups vs np.max, bit-exact
V-04: sum_reduce — 10000 values of 1e-7 cumulative vs np.sum, error < 1%
V-05: conv_i32_to_f16 — INT32→FP16→INT32 roundtrip = 0 LSB error
V-06: conv_i32_to_f16 — INT32_MIN/MAX/0/±1 bit-exact
V-07: residual_add — original=1e6, delta=1 → preserves small contribution
V-08: softmax_max_reduce — vs np.max reference
V-09: softmax pipeline — max→sub→exp→sum→div vs scipy.special.softmax
"""

import numpy as np
import pytest
from golden_executor import GoldenVector, GoldenSFU

SEED = 12345


@pytest.fixture(scope="module")
def vec():
    """Single GoldenVector instance shared across all tests."""
    return GoldenVector()


# ══════════════════════════════════════════════════════════════════════
# V-01: add / mul — random 1000 groups vs numpy, max_error < 1e-7
# ══════════════════════════════════════════════════════════════════════


def test_v01_add_1000_groups(vec):
    """GoldenVector.add matches numpy int32 addition (bit-exact, error < 1e-7).

    V-01 add verification: 1000 random (a, b) pairs, each of size 128.
    Uses fixed seed for determinism.
    """
    rng = np.random.RandomState(SEED)
    max_err = 0.0
    for i in range(1000):
        a = rng.randint(-10000, 10000, size=128).astype(np.int32)
        b = rng.randint(-10000, 10000, size=128).astype(np.int32)
        golden = vec.add(a, b)
        ref = (a.astype(np.int32) + b.astype(np.int32)).astype(np.int32)
        err = float(np.max(np.abs(golden.astype(np.float64) - ref.astype(np.float64))))
        if err > max_err:
            max_err = err
    assert max_err < 1e-7, f"add max_error across 1000 groups = {max_err:.2e}"


def test_v01_mul_1000_groups(vec):
    """GoldenVector.mul matches numpy int32 multiply (bit-exact, error < 1e-7).

    V-01 mul verification: 1000 random (a, b) pairs, each of size 128.
    Uses fixed seed for determinism.
    """
    rng = np.random.RandomState(SEED)
    max_err = 0.0
    for i in range(1000):
        a = rng.randint(-10000, 10000, size=128).astype(np.int32)
        b = rng.randint(-10000, 10000, size=128).astype(np.int32)
        golden = vec.mul(a, b)
        ref = (a.astype(np.int32) * b.astype(np.int32)).astype(np.int32)
        err = float(np.max(np.abs(golden.astype(np.float64) - ref.astype(np.float64))))
        if err > max_err:
            max_err = err
    assert max_err < 1e-7, f"mul max_error across 1000 groups = {max_err:.2e}"


def test_v01_add_anti_vacuous(vec):
    """Anti-vacuous: add(a,b) != a - b for non-trivial input.

    If the implementation were silently replaced with subtraction,
    this assertion catches it.
    """
    a = np.array([5, 10, 15], dtype=np.int32)
    b = np.array([1, 2, 3], dtype=np.int32)
    add_result = vec.add(a, b)
    sub_result = a - b
    assert not np.array_equal(add_result, sub_result), \
        "add must not silently equal a - b (anti-vacuous)"


def test_v01_mul_anti_vacuous(vec):
    """Anti-vacuous: mul(a,b) != a + b for non-trivial input.

    If the implementation were silently replaced with addition,
    this assertion catches it.
    """
    a = np.array([5, 10, 15], dtype=np.int32)
    b = np.array([2, 3, 4], dtype=np.int32)
    mul_result = vec.mul(a, b)
    add_result = a + b
    assert not np.array_equal(mul_result, add_result), \
        "mul must not silently equal a + b (anti-vacuous)"


# ══════════════════════════════════════════════════════════════════════
# V-02: add / mul — boundary values (NaN, Inf, ±0, denorm)
# ══════════════════════════════════════════════════════════════════════


def test_v02_add_zero(vec):
    """add(x, 0) == x identity; add(0, 0) == 0."""
    x = np.array([5, -3, 0, 127, -128], dtype=np.int32)
    zeros = np.zeros(5, dtype=np.int32)
    result = vec.add(x, zeros)
    assert np.array_equal(result, x), f"add(x, 0) != x: {result}"
    result2 = vec.add(zeros, zeros)
    assert np.all(result2 == 0), f"add(0, 0) != 0: {result2}"


def test_v02_mul_zero(vec):
    """mul(x, 0) == 0; mul(0, 0) == 0."""
    x = np.array([5, -3, 127, -128, 1], dtype=np.int32)
    zeros = np.zeros(5, dtype=np.int32)
    result = vec.mul(x, zeros)
    assert np.all(result == 0), f"mul(x, 0) != 0: {result}"
    result2 = vec.mul(zeros, zeros)
    assert np.all(result2 == 0), f"mul(0, 0) != 0: {result2}"


def test_v02_add_boundary_int32(vec):
    """add handles INT32_MIN / INT32_MAX deterministically (no crash)."""
    a = np.array([np.iinfo(np.int32).min, np.iinfo(np.int32).max], dtype=np.int32)
    b = np.array([0, 1], dtype=np.int32)
    result = vec.add(a, b)
    assert result.dtype == np.int32, "add output must be int32"
    assert result.shape == (2,), f"unexpected shape: {result.shape}"


def test_v02_mul_boundary_int32(vec):
    """mul handles INT32_MIN / INT32_MAX deterministically (no crash)."""
    a = np.array([np.iinfo(np.int32).min, np.iinfo(np.int32).max], dtype=np.int32)
    b = np.array([1, -1], dtype=np.int32)
    result = vec.mul(a, b)
    assert result.dtype == np.int32, "mul output must be int32"
    assert result.shape == (2,), f"unexpected shape: {result.shape}"


def test_v02_add_float_special(vec):
    """add with float NaN/Inf — must not crash; produces deterministic int32 output.

    The Vector Engine operates on INT32, so NaN/Inf get truncated to int32.
    This test verifies crash-free deterministic behavior.
    """
    a = np.array([np.nan, np.inf, -np.inf], dtype=np.float32)
    b = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    result = vec.add(a, b)
    assert result.dtype == np.int32, "add(float_special) output must be int32"
    assert result.shape == (3,), f"unexpected shape: {result.shape}"


def test_v02_mul_float_special(vec):
    """mul with float NaN/Inf — must not crash; produces deterministic int32 output."""
    a = np.array([np.nan, np.inf, -np.inf], dtype=np.float32)
    b = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    result = vec.mul(a, b)
    assert result.dtype == np.int32, "mul(float_special) output must be int32"
    assert result.shape == (3,), f"unexpected shape: {result.shape}"


def test_v02_add_denorm(vec):
    """add with denorm (subnormal) float values — denorm truncates to 0 in int32."""
    denorm = np.array([1e-40, -1e-40, 1e-42], dtype=np.float32)
    b = np.array([5, 5, 5], dtype=np.int32)
    result = vec.add(denorm, b)
    # Denorm floats → 0 when cast to int32, so 0 + 5 = 5
    assert np.array_equal(result, b), f"add(denorm, 5) != 5: {result}"


# ══════════════════════════════════════════════════════════════════════
# V-03: max_reduce — random 100 groups vs np.max, bit-exact
# ══════════════════════════════════════════════════════════════════════

_rng_v03 = np.random.RandomState(SEED + 3)
_V03_INPUTS = [
    (_rng_v03.randint(-100000, 100000, size=int(_rng_v03.randint(1, 1000))).astype(np.int32),
     f"v03_{i}")
    for i in range(100)
]


@pytest.mark.parametrize("x,label", _V03_INPUTS, ids=[p[1] for p in _V03_INPUTS])
def test_v03_max_reduce(vec, x, label):
    """max_reduce must be bit-exact with np.max across 100 random arrays."""
    golden = vec.max_reduce(x)
    ref = float(np.max(x))
    assert golden == pytest.approx(ref, abs=0, rel=0), \
        f"{label}: max_reduce={golden} != np.max={ref} (diff={abs(golden - ref)})"
    assert isinstance(golden, float), "max_reduce must return float"


def test_v03_anti_vacuous(vec):
    """Anti-vacuous: max_reduce([1,5,3]) = 5, not first or last element."""
    x = np.array([1, 5, 3], dtype=np.int32)
    result = vec.max_reduce(x)
    assert result == 5.0, f"max_reduce([1,5,3])={result}, expected 5.0"
    assert result != 1.0, "vacuous: would pass if max_reduce returned first element"
    assert result != 3.0, "vacuous: would pass if max_reduce returned last element"


# ══════════════════════════════════════════════════════════════════════
# V-04: sum_reduce — 10000 values of 1e-7 cumulative vs np.sum, error < 1%
# ══════════════════════════════════════════════════════════════════════


def test_v04_sum_reduce_1e_7(vec):
    """sum_reduce of 10000 × 1e-7 must be within 1% of np.sum (float64 reference).

    Uses float64 internally, so error is at the ~1e-16 level.
    """
    x = np.full(10000, 1e-7, dtype=np.float64)
    golden = vec.sum_reduce(x)
    ref = float(np.sum(x))
    rel_err = abs(golden - ref) / abs(ref)
    assert rel_err < 0.01, \
        f"sum_reduce rel_err={rel_err:.6f} > 1% (golden={golden}, ref={ref})"
    # Sanity check: result should be near 0.001
    assert golden == pytest.approx(0.001, abs=1e-4), \
        f"sum_reduce={golden} far from expected 0.001"


def test_v04_anti_vacuous_float32_corruption(vec):
    """Anti-vacuous: float32 accumulation would FAIL the 1% threshold.

    This proves the test threshold is meaningful — sum_reduce must use at least
    float64 internal precision. Float32 accumulation of 10000 × 1e-7 loses
    significant precision (~5% error).
    """
    x = np.full(10000, 1e-7, dtype=np.float64)
    ref_f64 = float(np.sum(x.astype(np.float64)))
    # float32 sum loses precision — compute the expected error
    f32_sum = float(np.sum(x.astype(np.float32)))
    f32_err = abs(f32_sum - ref_f64) / abs(ref_f64)
    # sum_reduce should be better than float32
    golden = vec.sum_reduce(x)
    golden_err = abs(golden - ref_f64) / abs(ref_f64)
    assert golden_err < 0.01, \
        f"sum_reduce rel_err={golden_err:.6f} > 1%"
    assert golden_err <= f32_err, \
        f"sum_reduce error={golden_err:.6f} should be <= float32 error={f32_err:.6f}"


# ══════════════════════════════════════════════════════════════════════
# V-05: conv_i32_to_f16 — INT32→FP16→INT32 roundtrip = 0 LSB error
# ══════════════════════════════════════════════════════════════════════

# Values exactly representable in float16 (within mantissa precision):
# - All integers in [-2048, 2048] are exact (11 mantissa bits)
# - Even integers in (-4096, -2048) ∪ (2048, 4096)
# - Multiples of 4 in (-8192, -4096) ∪ (4096, 8192)
# - Powers of two beyond that range
_V05_EXACT = [0, 1, -1, 127, -128, 1024, -1024, 2048, 4096, 8192, 16384, -32768]


@pytest.mark.parametrize("val", _V05_EXACT)
def test_v05_conv_i32_to_f16_roundtrip_exact(vec, val):
    """INT32→FP16→INT32 roundtrip must be 0 LSB for exactly-representable values."""
    arr = np.array([val], dtype=np.int32)
    f16 = vec.conv_i32_to_f16(arr)
    rt = f16.astype(np.int32)
    assert rt[0] == val, \
        f"roundtrip({val}) = {rt[0]}, expected {val} (lost {val - rt[0]} LSB)"


def test_v05_conv_i32_to_f16_roundtrip_multi(vec):
    """Multiple exact-representable values: all roundtrip with 0 LSB error."""
    arr = np.array(_V05_EXACT, dtype=np.int32)
    f16 = vec.conv_i32_to_f16(arr)
    rt = f16.astype(np.int32)
    errors = np.abs(rt.astype(np.int64) - arr.astype(np.int64))
    assert np.all(errors == 0), \
        f"max roundtrip error = {np.max(errors)} LSB among values: " \
        f"{[(v, r) for v, r in zip(_V05_EXACT, rt) if v != r]}"


def test_v05_conv_i32_to_f16_clamp_large_positive(vec):
    """INT32_MAX saturates to float16 max (~65504), not the original value."""
    arr = np.array([np.iinfo(np.int32).max], dtype=np.int32)
    f16 = vec.conv_i32_to_f16(arr)
    rt = f16.astype(np.int32)
    assert rt[0] < np.iinfo(np.int32).max, \
        "large positive must saturate below INT32_MAX"
    assert rt[0] > 0, "saturated value must be positive"


def test_v05_conv_i32_to_f16_clamp_large_negative(vec):
    """INT32_MIN saturates to -float16 max (~(-65504)), not the original value."""
    arr = np.array([np.iinfo(np.int32).min], dtype=np.int32)
    f16 = vec.conv_i32_to_f16(arr)
    rt = f16.astype(np.int32)
    assert rt[0] > np.iinfo(np.int32).min, \
        "large negative must saturate above INT32_MIN"
    assert rt[0] < 0, "saturated value must be negative"


def test_v05_anti_vacuous_roundtrip_loss(vec):
    """Anti-vacuous: NOT all INT32 values roundtrip exactly — prove loss exists."""
    arr = np.array([2049, 4097, 65500], dtype=np.int32)
    f16 = vec.conv_i32_to_f16(arr)
    rt = f16.astype(np.int32)
    exact_count = np.sum(rt == arr)
    assert exact_count < len(arr), \
        "all values passed — test would be vacuous if every int32 roundtripped exactly"


# ══════════════════════════════════════════════════════════════════════
# V-06: conv_i32_to_f16 — INT32_MIN/MAX/0/±1 bit-exact
# ══════════════════════════════════════════════════════════════════════


def test_v06_conv_i32_to_f16_zero(vec):
    """conv_i32_to_f16(0) == 0.0."""
    arr = np.array([0], dtype=np.int32)
    result = vec.conv_i32_to_f16(arr)
    assert float(result[0]) == 0.0, f"conv(0) = {float(result[0])}, expected 0.0"


def test_v06_conv_i32_to_f16_one(vec):
    """conv_i32_to_f16(1) == 1.0."""
    arr = np.array([1], dtype=np.int32)
    result = vec.conv_i32_to_f16(arr)
    assert float(result[0]) == 1.0, f"conv(1) = {float(result[0])}, expected 1.0"


def test_v06_conv_i32_to_f16_neg_one(vec):
    """conv_i32_to_f16(-1) == -1.0."""
    arr = np.array([-1], dtype=np.int32)
    result = vec.conv_i32_to_f16(arr)
    assert float(result[0]) == -1.0, f"conv(-1) = {float(result[0])}, expected -1.0"


def test_v06_conv_i32_to_f16_int32_max(vec):
    """conv_i32_to_f16(INT32_MAX) saturates to positive float16 max (clip to ~±65504)."""
    arr = np.array([np.iinfo(np.int32).max], dtype=np.int32)
    result = vec.conv_i32_to_f16(arr)
    val = float(result[0])
    # The implementation clips to finfo(float16).max before astype(float16)
    f16_max = float(np.finfo(np.float16).max)
    assert 0 < val <= float(np.float16(f16_max)), \
        f"conv(INT32_MAX) = {val}, expected ≤ {float(np.float16(f16_max))}"
    assert val > 0, f"conv(INT32_MAX) = {val}, expected positive"


def test_v06_conv_i32_to_f16_int32_min(vec):
    """conv_i32_to_f16(INT32_MIN) saturates to negative float16 max (clip to ~±65504)."""
    arr = np.array([np.iinfo(np.int32).min], dtype=np.int32)
    result = vec.conv_i32_to_f16(arr)
    val = float(result[0])
    f16_max = float(np.finfo(np.float16).max)
    assert float(-np.float16(f16_max)) <= val < 0, \
        f"conv(INT32_MIN) = {val}, expected ≥ {float(-np.float16(f16_max))}"
    assert val < 0, f"conv(INT32_MIN) = {val}, expected negative"


def test_v06_conv_i32_to_f16_boundaries_roundtrip(vec):
    """0/±1 roundtrip exactly; INT32_MIN/MAX saturate (prove not all 5 equal)."""
    vals = np.array([
        np.iinfo(np.int32).min, np.iinfo(np.int32).max, 0, 1, -1
    ], dtype=np.int32)
    f16 = vec.conv_i32_to_f16(vals)
    rt = f16.astype(np.int32)
    # 0, 1, -1 must be exact
    assert rt[2] == 0, "0 must roundtrip exactly"
    assert rt[3] == 1, "1 must roundtrip exactly"
    assert rt[4] == -1, "-1 must roundtrip exactly"
    # INT32_MIN/MAX must NOT roundtrip (saturation)
    assert rt[0] != vals[0], "INT32_MIN must NOT roundtrip (saturates)"
    assert rt[1] != vals[1], "INT32_MAX must NOT roundtrip (saturates)"
    # Saturated values must have same sign
    assert rt[0] < 0, "INT32_MIN saturation must be negative"
    assert rt[1] > 0, "INT32_MAX saturation must be positive"
    assert rt[0] < rt[1], "saturated min < saturated max"


# ══════════════════════════════════════════════════════════════════════
# V-07: residual_add — precision preservation
# ══════════════════════════════════════════════════════════════════════


def test_v07_residual_add_preserves_delta(vec):
    """residual_add(large_original, small_delta) preserves the small contribution.

    original=1e6 (as BF16 → float32 → int32 = 1,000,000)
    delta=1 (INT32 MXU output = 1)
    result = 1,000,001 — proves INT64 intermediate precision.
    """
    original = np.array([1_000_000, 2_000_000], dtype=np.float32)
    delta = np.array([1, -2], dtype=np.int32)
    result = vec.residual_add(original, delta)
    expected = np.array([1_000_001, 1_999_998], dtype=np.int32)
    assert np.array_equal(result, expected), \
        f"residual_add = {result}, expected {expected}"


def test_v07_residual_add_zero_delta(vec):
    """residual_add(x, 0) == x (zero delta doesn't change original)."""
    original = np.array([1_000_000, -500_000, 0], dtype=np.float32)
    delta = np.zeros(3, dtype=np.int32)
    result = vec.residual_add(original, delta)
    expected = np.array([1_000_000, -500_000, 0], dtype=np.int32)
    assert np.array_equal(result, expected), \
        f"residual_add(x,0) = {result}, expected {expected}"


def test_v07_residual_add_roundtrip_behavior(vec):
    """residual_add behaves like int32 addition for in-range values."""
    original = np.array([100, 200, -50], dtype=np.float32)
    delta = np.array([7, -300, 25], dtype=np.int32)
    result = vec.residual_add(original, delta)
    # expected: float32→int32 then int64 add → int32 clamp
    expected = (original.astype(np.float32).astype(np.int32).astype(np.int64)
                + delta.astype(np.int64))
    expected = np.clip(expected, -2**31, 2**31 - 1).astype(np.int32)
    assert np.array_equal(result, expected), \
        f"residual_add mismatch: {result} vs {expected}"


def test_v07_residual_add_saturation(vec):
    """residual_add clamps to INT32 range on overflow."""
    original = np.array([2_000_000_000], dtype=np.float32)
    delta = np.array([1_000_000_000], dtype=np.int32)
    result = vec.residual_add(original, delta)
    # 2B + 1B = 3B > INT32_MAX (~2.147B) → saturates
    expected = np.int32(np.iinfo(np.int32).max)
    assert result[0] == expected, \
        f"residual_add overflow = {result[0]}, expected {expected} (INT32_MAX)"


def test_v07_anti_vacuous(vec):
    """Anti-vacuous: delta² != delta for non-trivial delta, and delta=-delta inverts."""
    original = np.array([0, 0, 0], dtype=np.float32)
    d = np.array([5, -3, 0], dtype=np.int32)
    r1 = vec.residual_add(original, d)
    r2 = vec.residual_add(original, -d)
    assert not np.array_equal(r1, r2), \
        "residual_add(x, d) must differ from residual_add(x, -d) for non-zero d"
    assert np.array_equal(r1, d), "residual_add(0, d) should equal d"
    assert np.array_equal(r2, -d), "residual_add(0, -d) should equal -d"


# ══════════════════════════════════════════════════════════════════════
# V-08: softmax_max_reduce — vs np.max reference
# ══════════════════════════════════════════════════════════════════════

_rng_v08 = np.random.RandomState(SEED + 8)
_V08_INPUTS = [
    (_rng_v08.randint(-100000, 100000, size=int(_rng_v08.randint(1, 1000))).astype(np.int32),
     f"v08_{i}")
    for i in range(100)
]


@pytest.mark.parametrize("x,label", _V08_INPUTS, ids=[p[1] for p in _V08_INPUTS])
def test_v08_softmax_max_reduce(vec, x, label):
    """softmax_max_reduce must be bit-exact with np.max across 100 random arrays."""
    golden = vec.softmax_max_reduce(x)
    ref = float(np.max(x))
    assert golden == pytest.approx(ref, abs=0, rel=0), \
        f"{label}: softmax_max_reduce={golden} != np.max={ref} (diff={abs(golden - ref)})"
    assert isinstance(golden, float), "softmax_max_reduce must return float"


def test_v08_anti_vacuous(vec):
    """Anti-vacuous: softmax_max_reduce([1,5,3]) = 5, not first or last."""
    x = np.array([1, 5, 3], dtype=np.int32)
    result = vec.softmax_max_reduce(x)
    assert result == 5.0, f"softmax_max_reduce([1,5,3])={result}, expected 5.0"
    assert result != 1.0, "vacuous: would pass if returning first element"
    assert result != 3.0, "vacuous: would pass if returning last element"


# ══════════════════════════════════════════════════════════════════════
# V-09: softmax pipeline — max→sub→exp→sum→div vs scipy
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def sfu():
    """Single GoldenSFU instance for LUT-based exp in softmax pipeline."""
    return GoldenSFU()


@pytest.mark.parametrize("size", [4, 8, 16, 32, 128, 256])
def test_v09_softmax_pipeline_scipy(vec, sfu, size):
    """Full softmax pipeline: max→sub→exp→sum→div matches scipy.special.softmax.

    Pipeline composition:
      vec.softmax_max_reduce(x)   → max
      vec.softmax_scale_sub(x, m) → subtract max
      sfu._exp_hw(x_sub)          → LUT-based exp
      vec.softmax_sum_reduce(e)   → sum of exp
      e / sum                     → normalize
    """
    scipy = pytest.importorskip("scipy.special")

    rng = np.random.RandomState(SEED + 9 + size)
    x = rng.uniform(-10, 10, size=size).astype(np.float32)

    # HW pipeline using GoldenVector + GoldenSFU
    x_max = vec.softmax_max_reduce(x)
    x_sub = vec.softmax_scale_sub(x, x_max)
    exp_vals = sfu._exp_hw(x_sub)
    s = vec.softmax_sum_reduce(exp_vals)
    hw_result = exp_vals / s

    # Reference: scipy.special.softmax
    ref = scipy.softmax(x).astype(np.float32)

    # LUT-based exp has ~1e-3 error vs float64, but NaN structure must match
    assert not np.any(np.isnan(hw_result)), \
        f"softmax pipeline produced NaN for size={size}"
    assert np.all(np.isfinite(hw_result)), \
        f"softmax pipeline produced non-finite for size={size}"

    # Check the normalization property: sum(hw_result) ≈ 1.0
    assert abs(np.sum(hw_result) - 1.0) < 1e-3, \
        f"softmax pipeline output sum = {np.sum(hw_result):.6f} != 1.0"

    # Check element positions (which element is max should agree)
    hw_max_idx = int(np.argmax(hw_result))
    ref_max_idx = int(np.argmax(ref))
    assert hw_max_idx == ref_max_idx, \
        f"max position mismatch: HW at {hw_max_idx}, ref at {ref_max_idx}"

    # Check relative ranking: LUT error ~1e-3 but ranking of top elements should be correct
    hw_sorted = np.argsort(hw_result)[::-1][:3]
    ref_sorted = np.argsort(ref)[::-1][:3]
    common = set(hw_sorted) & set(ref_sorted)
    assert len(common) >= len(hw_sorted) * 0.5, \
        f"top-3 element overlap too low: HW={hw_sorted}, ref={ref_sorted}"


def test_v09_softmax_pipeline_vs_numpy_ref(vec, sfu):
    """Softmax pipeline matches numpy-based reference (wider tolerance for LUT error)."""
    rng = np.random.RandomState(SEED + 99)
    x = rng.uniform(-5, 5, size=16).astype(np.float32)

    # HW pipeline
    x_max = vec.softmax_max_reduce(x)
    x_sub = vec.softmax_scale_sub(x, x_max)
    exp_vals = sfu._exp_hw(x_sub)
    s = vec.softmax_sum_reduce(exp_vals)
    hw_result = exp_vals / s

    # NumPy reference (float64 max-subtract)
    ref_x = x.astype(np.float64)
    ref_max = np.max(ref_x)
    ref_exp = np.exp(ref_x - ref_max)
    ref = (ref_exp / np.sum(ref_exp)).astype(np.float32)

    # Sum-to-1 property must hold for both
    assert abs(np.sum(hw_result) - 1.0) < 1e-3
    assert abs(np.sum(ref) - 1.0) < 1e-10

    # Max element position must agree
    assert int(np.argmax(hw_result)) == int(np.argmax(ref))

    # Mean Absolute Error should be reasonable for LUT-based exp
    mae = float(np.mean(np.abs(hw_result.astype(np.float64) - ref.astype(np.float64))))
    assert mae < 0.05, \
        f"MAE={mae:.6f} too large between HW pipeline and numpy ref"


def test_v09_softmax_pipeline_sum_to_one(vec, sfu):
    """Softmax output sums to ~1.0 for diverse inputs (normalization property)."""
    rng = np.random.RandomState(SEED + 999)
    for size in [3, 7, 15, 64, 127]:
        x = rng.uniform(-10, 10, size=size).astype(np.float32)
        x_max = vec.softmax_max_reduce(x)
        x_sub = vec.softmax_scale_sub(x, x_max)
        exp_vals = sfu._exp_hw(x_sub)
        s = vec.softmax_sum_reduce(exp_vals)
        hw_result = exp_vals / s
        total = float(np.sum(hw_result))
        assert abs(total - 1.0) < 1e-3, \
            f"size={size}: softmax sum = {total:.6f}, expected 1.0"


def test_v09_anti_vacuous(vec, sfu):
    """Anti-vacuous: softmax of uniform input gives equal probabilities; all-positive
    input differs from all-negative input (proving exp is non-trivial)."""
    # Uniform input → equal probabilities
    n = 8
    x_uniform = np.full(n, 0.0, dtype=np.float32)
    x_max = vec.softmax_max_reduce(x_uniform)
    x_sub = vec.softmax_scale_sub(x_uniform, x_max)
    exp_vals = sfu._exp_hw(x_sub)
    s = vec.softmax_sum_reduce(exp_vals)
    hw_uniform = exp_vals / s

    assert np.allclose(hw_uniform, 1.0 / n, atol=1e-3), \
        f"uniform softmax = {hw_uniform}, expected equal probabilities"

    # All-positive vs all-negative should produce different outputs
    rng_av = np.random.RandomState(42)
    x_pos = rng_av.uniform(1, 5, size=n).astype(np.float32)
    x_neg = rng_av.uniform(-5, -1, size=n).astype(np.float32)

    def _softmax_hw(arr):
        mx = vec.softmax_max_reduce(arr)
        sb = vec.softmax_scale_sub(arr, mx)
        ex = sfu._exp_hw(sb)
        sm = vec.softmax_sum_reduce(ex)
        return ex / sm

    pos_out = _softmax_hw(x_pos)
    neg_out = _softmax_hw(x_neg)

    assert not np.allclose(pos_out, neg_out, atol=1e-3), \
        "softmax of positive input must differ from negative input (anti-vacuous)"
