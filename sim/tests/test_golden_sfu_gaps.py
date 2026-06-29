"""GoldenSFU gap coverage: SF-01 through SF-07.

SF-01: rmsnorm_hw vs ref — random 5 groups, max_error < 1e-5.
SF-02: _build_exp_lut — LUT table entries vs np.exp, max_error < 1e-5 (float32 rounding).
SF-03: _build_gelu_lut — boundary ±eps no jump.
SF-04: _build_cordic_table — 12-stage angles vs theory arctan(2^-i), gain ≈ 0.607253.
SF-05: softmax_hw — large values [1000,0,...] → [1.0,~0] not NaN.
SF-06: rope_hw — pos=0 identity, pos=100000 large angle validity.
SF-07: gelu_hw — approximate odd symmetry: gelu(-x) ≈ -gelu(x).

References
----------
sim/testplan.md lines 93-105 (P3 GoldenSFU)
sim/golden_executor.py lines 285-639 (GoldenSFU class)
"""

import numpy as np
import pytest

from golden_executor import GoldenSFU

# ── Reproducible RNG ──────────────────────────────────────────────────
_rng = np.random.RandomState(20260629)
_rng_rope = np.random.RandomState(20260630)


# ── RoPE test vectors (used by SF-06) ──────────────────────────────────
# Standard transformer dimensions: 32 query heads, 2 key heads, head_dim=128
_ROPE_NUM_HEADS = 32
_ROPE_HEAD_DIM = 128
_ROPE_THETA = 10000.0
_ROPE_XQ = _rng_rope.randn(_ROPE_NUM_HEADS * _ROPE_HEAD_DIM).astype(np.float32) * 0.5
_ROPE_XK = _rng_rope.randn(2 * _ROPE_HEAD_DIM).astype(np.float32) * 0.5


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sfu():
    """Single GoldenSFU instance shared across all tests in this module."""
    return GoldenSFU()


# ══════════════════════════════════════════════════════════════════════
# SF-01: rmsnorm_hw vs ref — 5 random groups, max_error < 1e-5
# ══════════════════════════════════════════════════════════════════════

# 5 groups with varying sizes: 3 1D inputs + 2 2D inputs
_RMSNORM_GROUPS = [
    (_rng.randn(2560).astype(np.float32) * 2.0, "rmsnorm_1d_2560"),
    (_rng.randn(512).astype(np.float32) * 3.0, "rmsnorm_1d_512"),
    (_rng.randn(128).astype(np.float32) * 0.5, "rmsnorm_1d_128"),
    (_rng.randn(4, 256).astype(np.float32) * 2.0, "rmsnorm_2d_4x256"),
    (_rng.randn(8, 64).astype(np.float32) * 1.5, "rmsnorm_2d_8x64"),
]


@pytest.mark.parametrize("x,label", _RMSNORM_GROUPS,
                         ids=[p[1] for p in _RMSNORM_GROUPS])
def test_sf01_rmsnorm_hw_vs_ref(x, label):
    """SF-01: RMSNorm HW output vs float64 reference — max_error < 1e-5."""
    hw = GoldenSFU.rmsnorm_hw(x)
    ref = GoldenSFU.rmsnorm_ref(x)
    abs_diff = np.abs(hw.astype(np.float64) - ref.astype(np.float64))
    max_err = float(np.max(abs_diff))
    assert max_err < 1e-5, \
        f"{label}: max_error={max_err:.2e} >= 1e-5"
    # Anti-vacuous: ensure error is non-zero (test is measuring real HW-vs-ref difference)
    assert max_err > 0, \
        f"{label}: max_error is exactly 0 — test is vacuous"


# ══════════════════════════════════════════════════════════════════════
# SF-02: _build_exp_lut — LUT table entries match np.exp within float32 rounding
# ══════════════════════════════════════════════════════════════════════


def test_sf02_exp_lut_entries_vs_numpy(sfu):
    """SF-02: Verify all exp LUT table entries match numpy.exp at corresponding points.

    _build_exp_lut stores np.exp(xs).astype(np.float32) for each entry in the LUT.
    At these exact knot points the hardware performs no interpolation (frac=0),
    so the stored value must match numpy.exp within float32 rounding error (< 1e-5).

    This test directly inspects the LUT table (sfu.exp_lut), NOT the interpolation
    path (_exp_hw). Linear interpolation accuracy is a function of entry count and
    is validated via the RTL-level tolerance (abs_tol=2e-3 with 256-entry ROM).
    """
    entries = sfu.exp_lut_entries
    x_min = sfu.exp_lut_x_min
    x_max = sfu.exp_lut_x_max
    xs = np.linspace(x_min, x_max, entries, dtype=np.float64)
    ref_f64 = np.exp(xs)  # float64 reference (not cast to float32)
    abs_diff = np.abs(sfu.exp_lut.astype(np.float64) - ref_f64)
    max_err = float(np.max(abs_diff))
    assert max_err < 1e-5, \
        f"exp LUT entries: max_error={max_err:.2e} (threshold 1e-5)"
    # Anti-vacuous: float32 rounding means error must be non-zero vs float64 reference
    assert max_err > 0, \
        "exp LUT: error is exactly 0 — test vacuous"


# ══════════════════════════════════════════════════════════════════════
# SF-03: _build_gelu_lut — boundary ±eps no jump
# ══════════════════════════════════════════════════════════════════════

# GELU LUT segment boundaries: 64 entries over [-4, 4], step = 8/63
_GELU_BOUNDARIES = np.linspace(-4.0, 4.0, 64, dtype=np.float64)


def test_sf03_gelu_boundary_continuity(sfu):
    """SF-03: GELU LUT at every interior segment boundary: no jump > 1e-5 when crossing.

    For each LUT entry x_i (i=1..62, interior), evaluate gelu_hw at x_i - eps,
    x_i, and x_i + eps. All three must agree within 1e-5, proving the piecewise
    linear interpolation is C0 at knot points with no off-by-one or clamping glitch.
    """
    eps = 1e-6
    boundaries = _GELU_BOUNDARIES
    n = len(boundaries)

    for i in range(1, n - 1):  # skip first and last (clamp transitions)
        x_b = float(boundaries[i])
        x_left = np.array([x_b - eps], dtype=np.float32)
        x_right = np.array([x_b + eps], dtype=np.float32)
        x_exact = np.array([x_b], dtype=np.float32)

        v_left = float(sfu.gelu_hw(x_left)[0])
        v_right = float(sfu.gelu_hw(x_right)[0])
        v_exact = float(sfu.gelu_hw(x_exact)[0])

        jump_lr = abs(v_left - v_right)
        assert jump_lr < 1e-5, \
            f"boundary {i} at x={x_b:.4f}: jump L-R={jump_lr:.2e}"
        assert abs(v_left - v_exact) < 1e-5, \
            f"boundary {i} at x={x_b:.4f}: |L-exact|={abs(v_left - v_exact):.2e}"
        assert abs(v_right - v_exact) < 1e-5, \
            f"boundary {i} at x={x_b:.4f}: |R-exact|={abs(v_right - v_exact):.2e}"


def test_sf03_gelu_clamp_boundaries(sfu):
    """Anti-vacuous: GELU clamp transitions at [-4, 4] must also be continuous.

    Inside-LUT values near the edge must match the clamped extrapolation values,
    and the function must not have a visible discontinuity at the clamp point.
    """
    eps = 1e-6

    # At x = -4: inside-LUT value vs clamp (below -4)
    for tag, x_val in [("at", -4.0), ("below", -4.0 - eps), ("above", -4.0 + eps)]:
        v = float(sfu.gelu_hw(np.array([x_val], dtype=np.float32))[0])
        assert abs(v) < 1e-2, \
            f"GELU clamp at x=-4 ({tag}): val={v:.4e} (expected ~0)"

    # At x = 4: inside-LUT value vs clamp (above 4)
    for tag, x_val in [("at", 4.0), ("below", 4.0 - eps), ("above", 4.0 + eps)]:
        v = float(sfu.gelu_hw(np.array([x_val], dtype=np.float32))[0])
        assert v == pytest.approx(4.0, abs=2e-2), \
            f"GELU clamp at x=4 ({tag}): val={v:.4e} (expected ~4.0)"


# ══════════════════════════════════════════════════════════════════════
# SF-04: _build_cordic_table — 12-stage angles vs theory
# ══════════════════════════════════════════════════════════════════════

# Theoretical CORDIC gain for 12 iterations: prod_{i=0}^{11} cos(atan(2^-i))
_CORDIC_THEORY_GAIN_F64 = float(np.prod(np.cos(np.arctan(2.0 ** -np.arange(12, dtype=np.float64)))))
_CORDIC_GAIN_TOL = 1e-6  # float32 rounding for product of 12 cos terms


def test_sf04_cordic_angles_vs_theory(sfu):
    """SF-04: Each CORDIC angle entry matches arctan(2^-i) within float32 rounding."""
    iterations = sfu.cordic_iterations
    assert iterations == 12, f"expected 12 CORDIC iterations, got {iterations}"

    for i in range(iterations):
        theory = np.arctan(2.0 ** -i)   # float64 reference
        actual = float(sfu.cordic_angles[i])
        err = abs(actual - theory)
        assert err < 1e-6, \
            f"cordic_angles[{i}]: actual={actual:.10e} theory={theory:.10e} err={err:.2e}"

    # Anti-vacuous: angles must be strictly decreasing
    for i in range(iterations - 1):
        assert sfu.cordic_angles[i] > sfu.cordic_angles[i + 1], \
            f"cordic_angles[{i}]={sfu.cordic_angles[i]} not > cordic_angles[{i+1}]={sfu.cordic_angles[i+1]}"
    # First angle ≈ arctan(1) = π/4 ≈ 0.785
    assert abs(sfu.cordic_angles[0] - np.pi / 4) < 0.01, \
        f"cordic_angles[0]={sfu.cordic_angles[0]:.4f} not ≈ π/4"


def test_sf04_cordic_gain_vs_theory(sfu):
    """SF-04: CORDIC gain matches theoretical product of cos(atan(2^-i))."""
    actual = float(sfu.cordic_gain)
    theory = _CORDIC_THEORY_GAIN_F64
    err = abs(actual - theory)

    assert err < _CORDIC_GAIN_TOL, \
        f"cordic_gain: actual={actual:.10e} theory={theory:.10e} err={err:.2e}"

    # Anti-vacuous: gain must be significantly different from both 0 and 1
    assert actual > 0.5, f"cordic_gain={actual:.6f} too small, suspicious"
    assert actual < 0.7, f"cordic_gain={actual:.6f} too close to 1.0, suspicious"
    # Known constant: ≈ 0.607253 (within 6 decimals at 12 iterations)
    assert abs(actual - 0.607253) < 5e-6, \
        f"cordic_gain={actual:.10e} deviates from known constant 0.607253"


# ══════════════════════════════════════════════════════════════════════
# SF-05: softmax_hw — large values stability
# ══════════════════════════════════════════════════════════════════════


def test_sf05_softmax_large_value_no_nan(sfu):
    """SF-05: softmax_hw on [1000, 0, ...] must not produce NaN and must sum to ~1."""
    x = np.zeros(2560, dtype=np.float32)
    x[0] = 1000.0

    hw = sfu.softmax_hw(x)

    # No NaN, no Inf
    assert not np.any(np.isnan(hw)), "softmax_hw produced NaN on large input"
    assert not np.any(np.isinf(hw)), "softmax_hw produced Inf on large input"

    # Sum must be ~1
    total = float(np.sum(hw))
    assert total == pytest.approx(1.0, rel=1e-5), \
        f"softmax sum={total:.10e} (expected ~1.0)"

    # Primary element must dominate
    assert hw[0] == pytest.approx(1.0, rel=1e-5), \
        f"softmax[0]={hw[0]:.10e} (expected ~1.0)"

    # All other entries must be near 0
    others = np.max(hw[1:])
    assert others < 1e-3, \
        f"max of non-dominant entries={others:.10e} (expected < 1e-3)"

    # Anti-vacuous: a less-extreme input must NOT give exact [1,0,0,...]
    # (proves softmax_hw actually ran, not a canned return)
    x2 = np.zeros(10, dtype=np.float32)
    x2[0] = 10.0
    hw2 = sfu.softmax_hw(x2)
    assert not np.any(np.isnan(hw2)), "softmax_hw produced NaN on smaller large input"
    assert float(hw2[0]) != 1.0, \
        "softmax[0] == 1.0 exactly on [10,0,...] — test vacuous"
    assert np.max(hw2[1:]) != 0.0, \
        "non-dominant entries exactly 0 on [10,0,...] — test vacuous"
    assert float(np.sum(hw2)) == pytest.approx(1.0, rel=1e-5)


# ══════════════════════════════════════════════════════════════════════
# SF-06: rope_hw — pos=0 identity, pos=100000 large angle
# ══════════════════════════════════════════════════════════════════════

# Shared test data for RoPE edge cases
_ROPE_Q = _rng.randn(4096).astype(np.float32) * 0.5   # 32 heads × 128 dim
_ROPE_K = _rng.randn(256).astype(np.float32) * 0.5    # 2 heads × 128 dim


def test_sf06_rope_pos0_identity(sfu):
    """SF-06: RoPE at pos=0 must approximately preserve input (near-identity rotation)."""
    hw_q, hw_k = sfu.rope_hw(_ROPE_Q.copy(), _ROPE_K.copy(), position=0)
    ref_q, ref_k = sfu.rope_ref(_ROPE_Q, _ROPE_K, position=0)

    # At pos=0, reference rotation is exact identity
    assert np.allclose(ref_q, _ROPE_Q.astype(np.float64), atol=1e-12), \
        "rope_ref pos=0 is not identity — test flawed"

    # Hardware CORDIC approximation should be close to identity
    cmp_q = GoldenSFU.compare_hw_vs_ref(hw_q, _ROPE_Q.astype(np.float32), tol_abs=5e-3)
    cmp_k = GoldenSFU.compare_hw_vs_ref(hw_k, _ROPE_K.astype(np.float32), tol_abs=5e-3)
    assert cmp_q["within_tolerance"], \
        f"RoPE Q pos=0: max_abs={cmp_q['max_abs_err']:.2e} (expected < 5e-3)"
    assert cmp_k["within_tolerance"], \
        f"RoPE K pos=0: max_abs={cmp_k['max_abs_err']:.2e} (expected < 5e-3)"

    # Anti-vacuous: CORDIC is not numerically exact — error must exist
    assert cmp_q["max_abs_err"] > 0, "RoPE Q pos=0: error=0 — CORDIC is vacuous"
    assert cmp_k["max_abs_err"] > 0, "RoPE K pos=0: error=0 — CORDIC is vacuous"


def test_sf06_rope_large_position_valid(sfu):
    """SF-06: RoPE at pos=100000 must produce valid (non-NaN, non-Inf) outputs."""
    hw_q, hw_k = sfu.rope_hw(_ROPE_Q.copy(), _ROPE_K.copy(), position=100000)

    # No NaN, no Inf
    assert not np.any(np.isnan(hw_q)), "RoPE Q pos=100000: NaN detected"
    assert not np.any(np.isinf(hw_q)), "RoPE Q pos=100000: Inf detected"
    assert not np.any(np.isnan(hw_k)), "RoPE K pos=100000: NaN detected"
    assert not np.any(np.isinf(hw_k)), "RoPE K pos=100000: Inf detected"

    # Output must differ from input (rotation happened)
    assert not np.allclose(hw_q, _ROPE_Q, atol=1e-6), \
        "RoPE Q pos=100000: output identical to input — no rotation applied"
    assert not np.allclose(hw_k, _ROPE_K, atol=1e-6), \
        "RoPE K pos=100000: output identical to input — no rotation applied"

    # Magnitude approximately preserved (CORDIC gain compensated)
    ratio_q = np.linalg.norm(hw_q) / (np.linalg.norm(_ROPE_Q) + 1e-12)
    ratio_k = np.linalg.norm(hw_k) / (np.linalg.norm(_ROPE_K) + 1e-12)
    assert ratio_q == pytest.approx(1.0, rel=0.15), \
        f"RoPE Q pos=100000: magnitude ratio={ratio_q:.4f} (expected ~1.0)"
    assert ratio_k == pytest.approx(1.0, rel=0.15), \
        f"RoPE K pos=100000: magnitude ratio={ratio_k:.4f} (expected ~1.0)"


# ══════════════════════════════════════════════════════════════════════
# SF-07: gelu_hw — approximate odd symmetry
# ══════════════════════════════════════════════════════════════════════

_GELU_SYMMETRY_XS = np.linspace(-3.0, 3.0, 300, dtype=np.float32)


def test_sf07_gelu_negative_consistent_with_ref(sfu):
    """SF-07: gelu_hw at negative x must match gelu_ref (no NaN, symmetry-preserving).

    GELU is not exactly odd — the x^3 term creates asymmetry that grows with |x|.
    This test verifies the HW LUT handles negative inputs correctly by comparing
    against the reference GELU with the same HW tolerance as positive inputs.
    """
    xs = _GELU_SYMMETRY_XS
    hw = sfu.gelu_hw(xs)
    ref = sfu.gelu_ref(xs.astype(np.float64)).astype(np.float32)

    cmp = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=2e-3)
    assert cmp["within_tolerance"], \
        f"gelu_hw vs ref on [-3,3]: max_abs={cmp['max_abs_err']:.2e}"
    assert not np.any(np.isnan(hw)), "gelu_hw produced NaN on [-3,3]"
    assert not np.any(np.isinf(hw)), "gelu_hw produced Inf on [-3,3]"

    # Anti-vacuous: GELU is NOT exactly odd — extremes show strong asymmetry.
    # gelu(4) ≈ 4 but gelu(-4) ≈ 0; prove the test actually measures non-trivial values.
    x_edge = np.array([4.0, -4.0], dtype=np.float32)
    hw_edge = sfu.gelu_hw(x_edge)
    assert hw_edge[0] == pytest.approx(4.0, abs=2e-2), \
        f"gelu(4)={hw_edge[0]:.4f} (expected ~4.0)"
    assert hw_edge[1] == pytest.approx(0.0, abs=2e-2), \
        f"gelu(-4)={hw_edge[1]:.4f} (expected ~0.0)"
    asymmetry = abs(float(hw_edge[0]) + float(hw_edge[1]))
    assert asymmetry > 3.0, \
        f"GELU asymmetry at ±4 too small: |gelu(4)+gelu(-4)|={asymmetry:.4f} (expected > 3.0)"
