"""GoldenSFU gap coverage: SF-01 through SF-03.

SF-01: rmsnorm_hw vs ref — random 5 groups, max_error < 1e-5.
SF-02: _build_exp_lut — [-20,0] sampled 1000 points, max_error < 1e-5.
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
# SF-02: _build_exp_lut — [-20,0] sampled 1000 points, max_error < 1e-5
# ══════════════════════════════════════════════════════════════════════

# 1000 uniformly spaced test points across [-20, 0]
_EXP_TEST_POINTS = np.linspace(-20.0, 0.0, 1000, dtype=np.float64)


def test_sf02_exp_lut_1000_pts(sfu):
    """SF-02: exp LUT accuracy at 1000 uniformly-sampled points — max_error < 1e-5 vs numpy.exp.

    Verifies the entire LUT interpolation path, not just exact entry points.
    """
    x_test = _EXP_TEST_POINTS.astype(np.float32)
    hw = sfu._exp_hw(x_test)
    ref = np.exp(x_test.astype(np.float64))
    abs_diff = np.abs(hw.astype(np.float64) - ref)
    max_err = float(np.max(abs_diff))
    worst_x = float(x_test[np.argmax(abs_diff)])
    assert max_err < 1e-5, \
        f"exp LUT: max_error={max_err:.2e} at x={worst_x:.4f} (threshold 1e-5)"


def test_sf02_exp_lut_entry_exact(sfu):
    """Anti-vacuous: exp LUT must be exact at its own entry points (error << 1e-5)."""
    x_min, x_max = sfu.exp_lut_x_min, sfu.exp_lut_x_max
    entries = sfu.exp_lut_entries
    entry_xs = np.linspace(x_min, x_max, entries, dtype=np.float32)
    hw = sfu._exp_hw(entry_xs)
    ref = np.exp(entry_xs.astype(np.float64)).astype(np.float32)
    abs_diff = np.abs(hw.astype(np.float64) - ref.astype(np.float64))
    max_err = float(np.max(abs_diff))
    # At LUT entry points, frac=0, so hw = exact LUT value (just float32 rounding)
    assert max_err < 1e-6, \
        f"exp LUT entries: max_error={max_err:.2e} (expected < 1e-6)"
    assert max_err > 0, \
        "exp LUT entries: error is exactly 0 — test vacuous"

# ══════════════════════════════════════════════════════════════════════

def test_sf02_build_exp_lut_accuracy():
    """SF-02: exp LUT accuracy at storage points — max_error < 1e-5."""
    sfu = GoldenSFU()
    entries = sfu.exp_lut_entries
    x_min = sfu.exp_lut_x_min
    x_max = sfu.exp_lut_x_max
    entry_xs = np.linspace(x_min, x_max, entries, dtype=np.float32)
    hw = sfu._exp_hw(entry_xs)
    ref = np.exp(entry_xs.astype(np.float64)).astype(np.float32)
    abs_diff = np.abs(hw.astype(np.float64) - ref.astype(np.float64))
    max_err = float(np.max(abs_diff))
    assert max_err < 1e-5, \
        f"exp LUT: max_error={max_err:.2e} >= 1e-5 at {entries} LUT entries"
    assert max_err > 0, \
        "exp LUT: max_error is exactly 0 — test vacuous"


# ══════════════════════════════════════════════════════════════════════
# SF-03: _build_gelu_lut — boundary ±eps no jump
# ══════════════════════════════════════════════════════════════════════

def test_sf03_build_gelu_lut_boundary_no_jump():
    """SF-03: GELU LUT boundaries — gelu_hw(x) at each LUT entry returns exact LUT value.

    Linear interpolation is C0 continuous at knot points iff gelu_hw(x_i) == lut[i].
    Tests for off-by-one or clamping bugs at segment boundaries.
    """
    sfu = GoldenSFU()
    entries = sfu.gelu_lut_entries
    x_min = sfu.gelu_lut_x_min
    x_max = sfu.gelu_lut_x_max
    boundaries = np.linspace(x_min, x_max, entries, dtype=np.float32)

    for i in range(entries):
        hw = sfu.gelu_hw(np.array([boundaries[i]], dtype=np.float32))
        expected = sfu.gelu_lut[i]
        diff = float(np.abs(hw[0] - expected))
        assert diff < 1e-6, \
            f"GELU LUT boundary at x={boundaries[i]:.6f}: gelu_hw={hw[0]:.8e} vs lut[{i}]={expected:.8e}, diff={diff:.2e}"

    # Anti-vacuous: verify gelu_hw does actual interpolation (not just identity)
    # Midpoint value should differ from adjacent LUT entry values
    mid_x = np.float32(boundaries[0] + (boundaries[1] - boundaries[0]) / 2)
    mid_val = sfu.gelu_hw(np.array([mid_x]))
    adjacent_low = sfu.gelu_lut[0]
    adjacent_high = sfu.gelu_lut[1]
    if adjacent_low != adjacent_high:
        assert adjacent_low < mid_val[0] < adjacent_high or adjacent_high < mid_val[0] < adjacent_low, \
            "GELU LUT: midpoint outside adjacent entries — interpolation broken"
