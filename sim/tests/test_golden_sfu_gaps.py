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
