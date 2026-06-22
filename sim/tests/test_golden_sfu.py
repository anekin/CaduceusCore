"""Parameterized SFU precision tests from golden_executor.py.

Each SFU op has a hardware-equivalent (hw) and reference (ref) implementation.
These tests verify that the HW precision stays within specified tolerances,
matching the CLI `golden_executor.py sfu-verify` behavior.

SFU ops covered: softmax, gelu, silu, layernorm, rope
"""

import numpy as np
import pytest
from golden_executor import GoldenSFU

# ── Test inputs (reproducible, seeded) ──────────────────────────────

_rng = np.random.RandomState(12345)

# Softmax: 5 random vectors of 2560 elements each
_SOFTMAX_INPUTS = [
    (_rng.randn(2560).astype(np.float32) * 2.0, f"softmax_v{i}")
    for i in range(5)
]

# GELU / SiLU: 1 input in range [-4, 4]
_gelu_x = np.clip(_rng.randn(1000).astype(np.float32) * 2.0, -4, 4)

# LayerNorm: 3 random vectors of 2560 elements each
_LAYERNORM_INPUTS = [
    (_rng.randn(2560).astype(np.float32) * 2.0, f"layernorm_v{i}")
    for i in range(3)
]

# RoPE: 2 positions (0, 42) with standard dimensions
_x_q = _rng.randn(4096).astype(np.float32) * 0.5
_x_k = _rng.randn(256).astype(np.float32) * 0.5
_ROPE_POSITIONS = [0, 42]

# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sfu():
    """Single GoldenSFU instance shared across all tests in this module."""
    return GoldenSFU()


# ── Softmax ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("x,label", _SOFTMAX_INPUTS,
                         ids=[p[1] for p in _SOFTMAX_INPUTS])
def test_softmax_hw_vs_ref(sfu, x, label):
    """Softmax HW output must stay within abs tolerance of reference."""
    hw = sfu.softmax_hw(x)
    ref = sfu.softmax_ref(x)
    cmp = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=1e-3, tol_rel=1e-3)
    assert cmp["within_tolerance"], \
        f"{label}: max_abs={cmp['max_abs_err']:.2e} max_rel={cmp['max_rel_err']:.2e}"


@pytest.mark.parametrize("x,label", _SOFTMAX_INPUTS,
                         ids=[p[1] for p in _SOFTMAX_INPUTS])
def test_softmax_sums_to_one(sfu, x, label):
    """Softmax output must sum to ~1.0."""
    hw = sfu.softmax_hw(x)
    total = np.sum(hw)
    assert total == pytest.approx(1.0, rel=1e-3), \
        f"{label}: sum={total:.6f}"


@pytest.mark.parametrize("x,label", _SOFTMAX_INPUTS,
                         ids=[p[1] for p in _SOFTMAX_INPUTS])
def test_softmax_non_negative(sfu, x, label):
    """Softmax output must be all non-negative."""
    hw = sfu.softmax_hw(x)
    assert np.all(hw >= 0), f"{label}: found negative values"


# ── GELU ────────────────────────────────────────────────────────────

def test_gelu_hw_vs_ref(sfu):
    """GELU HW output must stay within tolerance of reference."""
    hw = sfu.gelu_hw(_gelu_x)
    ref = sfu.gelu_ref(_gelu_x)
    cmp = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=2e-3)
    assert cmp["within_tolerance"], \
        f"GELU: max_abs={cmp['max_abs_err']:.2e} max_rel={cmp['max_rel_err']:.2e}"


def test_gelu_negative_saturates(sfu):
    """GELU must saturate to 0 for very negative inputs (< -4)."""
    x = np.array([-10.0, -5.0, -4.1], dtype=np.float32)
    hw = sfu.gelu_hw(x)
    assert np.allclose(hw, 0.0, atol=1e-6), f"GELU negative not zero: {hw}"


def test_gelu_positive_linear(sfu):
    """GELU must approximate identity for large positive inputs (> 4)."""
    x = np.array([4.1, 5.0, 10.0], dtype=np.float32)
    hw = sfu.gelu_hw(x)
    assert np.allclose(hw, x, rtol=1e-3), f"GELU positive not linear: {hw}"


# ── SiLU ────────────────────────────────────────────────────────────

def test_silu_hw_vs_ref(sfu):
    """SiLU HW output must stay within tolerance of reference."""
    hw = sfu.silu_hw(_gelu_x)
    ref = sfu.silu_ref(_gelu_x)
    cmp = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=2e-3)
    assert cmp["within_tolerance"], \
        f"SiLU: max_abs={cmp['max_abs_err']:.2e} max_rel={cmp['max_rel_err']:.2e}"


def test_silu_negative_goes_to_zero(sfu):
    """SiLU must approach 0 for very negative inputs."""
    x = np.array([-10.0, -8.0, -6.0], dtype=np.float32)
    hw = sfu.silu_hw(x)
    assert np.all(np.abs(hw) < 0.1), f"SiLU negative not near zero: {hw}"


def test_silu_zero_is_zero(sfu):
    """SiLU(0) must be 0."""
    hw = sfu.silu_hw(np.array([0.0], dtype=np.float32))
    assert hw[0] == pytest.approx(0.0, abs=1e-6)


# ── LayerNorm ───────────────────────────────────────────────────────

@pytest.mark.parametrize("x,label", _LAYERNORM_INPUTS,
                         ids=[p[1] for p in _LAYERNORM_INPUTS])
def test_layernorm_hw_vs_ref(sfu, x, label):
    """LayerNorm HW output must stay within tolerance of reference."""
    hw = sfu.layernorm_hw(x)
    ref = sfu.layernorm_ref(x)
    cmp = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=1e-2)
    assert cmp["within_tolerance"], \
        f"{label}: max_abs={cmp['max_abs_err']:.2e} max_rel={cmp['max_rel_err']:.2e}"


@pytest.mark.parametrize("x,label", _LAYERNORM_INPUTS,
                         ids=[p[1] for p in _LAYERNORM_INPUTS])
def test_layernorm_zero_mean(sfu, x, label):
    """LayerNorm output must have near-zero mean."""
    hw = sfu.layernorm_hw(x)
    mean = np.mean(hw)
    assert mean == pytest.approx(0.0, abs=1e-2), \
        f"{label}: mean={mean:.4e}"


@pytest.mark.parametrize("x,label", _LAYERNORM_INPUTS,
                         ids=[p[1] for p in _LAYERNORM_INPUTS])
def test_layernorm_unit_variance(sfu, x, label):
    """LayerNorm output must have near-unit variance."""
    hw = sfu.layernorm_hw(x)
    var = np.var(hw)
    assert var == pytest.approx(1.0, rel=2e-2), \
        f"{label}: var={var:.4e}"


# ── RMSNorm ──────────────────────────────────────────────────────────

_RMSNORM_INPUTS = [
    (_rng.randn(2560).astype(np.float32) * 2.0, f"rmsnorm_v{i}")
    for i in range(3)
]


@pytest.mark.parametrize("x,label", _RMSNORM_INPUTS,
                         ids=[p[1] for p in _RMSNORM_INPUTS])
def test_rmsnorm_hw_vs_ref(sfu, x, label):
    """RMSNorm HW output must stay within tolerance of reference."""
    hw = sfu.rmsnorm_hw(x)
    ref = sfu.rmsnorm_ref(x)
    cmp = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=1e-3, tol_rel=1e-3)
    assert cmp["within_tolerance"], \
        f"{label}: max_abs={cmp['max_abs_err']:.2e} max_rel={cmp['max_rel_err']:.2e}"


@pytest.mark.parametrize("x,label", _RMSNORM_INPUTS,
                         ids=[p[1] for p in _RMSNORM_INPUTS])
def test_rmsnorm_unit_rms(sfu, x, label):
    """RMSNorm output must have near-unit RMS (not variance — RMSNorm constraint)."""
    hw = sfu.rmsnorm_hw(x)
    rms = np.sqrt(np.mean(hw ** 2))
    assert rms == pytest.approx(1.0, rel=2e-2), \
        f"{label}: rms={rms:.4e}"


@pytest.mark.parametrize("x,label", _RMSNORM_INPUTS,
                         ids=[p[1] for p in _RMSNORM_INPUTS])
def test_rmsnorm_no_mean_constraint(sfu, x, label):
    """RMSNorm does NOT force zero mean — output mean can differ from layernorm mean."""
    hw = sfu.rmsnorm_hw(x)
    ref = sfu.rmsnorm_ref(x)
    # Both hw and ref should agree on the mean (within tolerance)
    cmp = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=1e-3)
    assert cmp["within_tolerance"], \
        f"{label}: hw mean {np.mean(hw):.4e} vs ref mean {np.mean(ref):.4e}"


# ── RoPE ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pos", _ROPE_POSITIONS, ids=[f"pos={p}" for p in _ROPE_POSITIONS])
def test_rope_hw_vs_ref_q(sfu, pos):
    """RoPE Q HW output must stay within tolerance of reference."""
    hw_q, _ = sfu.rope_hw(_x_q.copy(), _x_k.copy(), position=pos)
    ref_q, _ = sfu.rope_ref(_x_q, _x_k, position=pos)
    cmp = GoldenSFU.compare_hw_vs_ref(hw_q, ref_q, tol_abs=1e-1)
    assert cmp["within_tolerance"], \
        f"RoPE Q pos={pos}: max_abs={cmp['max_abs_err']:.2e} max_rel={cmp['max_rel_err']:.2e}"


@pytest.mark.parametrize("pos", _ROPE_POSITIONS, ids=[f"pos={p}" for p in _ROPE_POSITIONS])
def test_rope_hw_vs_ref_k(sfu, pos):
    """RoPE K HW output must stay within tolerance of reference."""
    _, hw_k = sfu.rope_hw(_x_q.copy(), _x_k.copy(), position=pos)
    _, ref_k = sfu.rope_ref(_x_q, _x_k, position=pos)
    cmp = GoldenSFU.compare_hw_vs_ref(hw_k, ref_k, tol_abs=1e-1)
    assert cmp["within_tolerance"], \
        f"RoPE K pos={pos}: max_abs={cmp['max_abs_err']:.2e} max_rel={cmp['max_rel_err']:.2e}"


@pytest.mark.parametrize("pos", _ROPE_POSITIONS, ids=[f"pos={p}" for p in _ROPE_POSITIONS])
def test_rope_preserves_magnitude(sfu, pos):
    """RoPE rotation must approximately preserve vector magnitude (CORDIC gain compensated)."""
    hw_q, hw_k = sfu.rope_hw(_x_q.copy(), _x_k.copy(), position=pos)
    ref_q, ref_k = sfu.rope_ref(_x_q, _x_k, position=pos)
    # Check magnitude ratio stays within 10%
    ratio_q = np.linalg.norm(hw_q) / (np.linalg.norm(ref_q) + 1e-12)
    ratio_k = np.linalg.norm(hw_k) / (np.linalg.norm(ref_k) + 1e-12)
    assert ratio_q == pytest.approx(1.0, rel=0.1), \
        f"RoPE Q pos={pos}: magnitude ratio={ratio_q:.4f}"
    assert ratio_k == pytest.approx(1.0, rel=0.1), \
        f"RoPE K pos={pos}: magnitude ratio={ratio_k:.4f}"
