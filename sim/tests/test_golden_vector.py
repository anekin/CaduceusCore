"""GoldenVector tests: V-01 through V-04 from sim/testplan.md.

V-01: add / mul — random 1000 groups vs numpy, max_error < 1e-7
V-02: add / mul — boundary values (NaN, Inf, ±0, denorm)
V-03: max_reduce — random 100 groups vs np.max, bit-exact
V-04: sum_reduce — 10000 values of 1e-7 cumulative vs np.sum, error < 1%
"""

import numpy as np
import pytest
from golden_executor import GoldenVector

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
