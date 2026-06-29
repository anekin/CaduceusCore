"""GoldenSFU gap coverage: SF-01 through SF-03.

SF-01: rmsnorm_hw vs ref — random 5 groups, max_error < 1e-5.
SF-02: _build_exp_lut — LUT table entries vs np.exp, max_error < 1e-5 (float32 rounding).
SF-03: _build_gelu_lut — boundary ±eps no jump.

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
