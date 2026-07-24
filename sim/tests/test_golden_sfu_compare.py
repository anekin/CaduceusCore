"""RED-phase regression tests for FP16/SFU tolerance comparator.

These tests expose the bug in GoldenSFU.compare_hw_vs_ref() where the
within_tolerance decision uses np.all(abs_diff < tol) OR np.all(rel_diff < tol)
instead of an element-wise check (each element must pass EITHER abs OR rel).

TDD Phase 1 (RED): Tests FAIL against current buggy implementation.
TDD Phase 2 (GREEN): Fix GoldenSFU.compare_hw_vs_ref() so these pass.
"""

import numpy as np
from golden_executor import GoldenSFU


# ── RED test: mixed abs/rel element-wise pass ───────────────────────────

def test_compare_mixed_abs_rel_pass():
    """One element passes only atol, another passes only rtol.

    The current comparator uses np.all(abs_diff < atol) OR np.all(rel_diff < rtol)
    which rejects this case because neither atol alone nor rtol alone covers
    ALL elements.  The correct element-wise semantics: each element individually
    must pass EITHER abs OR rel tolerance.
    """
    # fp16 values chosen so that:
    #   ref[0]=0.25, hw[0]≈0.250488 → abs_diff≈0.488e-3 < 1e-3 ✓, rel_diff≈1.95e-3 > 1e-3 ✗
    #   ref[1]=2.0,  hw[1]≈2.00195 → abs_diff≈1.95e-3 > 1e-3 ✗, rel_diff≈0.977e-3 < 1e-3 ✓
    ref_f64 = np.array([0.25, 2.0])
    hw_f64 = np.array([0.2505, 2.002])
    ref = ref_f64.astype(np.float16)
    hw = hw_f64.astype(np.float16)

    result = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=1e-3, tol_rel=1e-3)

    assert result["within_tolerance"], (
        "mixed abs/rel must pass element-wise: "
        f"max_abs={result['max_abs_err']:.4e} max_rel={result['max_rel_err']:.4e}"
    )


# ── Genuine out-of-tolerance ────────────────────────────────────────────

def test_compare_out_of_tolerance_fail():
    """Values outside both abs and rel tolerance must fail."""
    ref = np.array([1.0, 0.5], dtype=np.float16)
    hw = np.array([1.1, 0.6], dtype=np.float16)
    result = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=1e-3, tol_rel=1e-3)
    assert not result["within_tolerance"], (
        f"out-of-tolerance should fail: max_abs={result['max_abs_err']:.4e}"
    )


# ── NaN mismatch ────────────────────────────────────────────────────────

def test_compare_nan_mismatch():
    """NaN vs finite or NaN at different positions must fail."""
    ref = np.array([1.0, 1.0], dtype=np.float16)
    hw = np.array([1.0005, np.nan], dtype=np.float16)
    result = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=1e-3, tol_rel=1e-3)
    assert not result["within_tolerance"], "NaN mismatch must fail"


# ── Inf mismatch ────────────────────────────────────────────────────────

def test_compare_inf_mismatch():
    """Opposite-sign infinities or finite vs infinite must fail."""
    ref = np.array([1.0, np.inf], dtype=np.float16)
    hw = np.array([1.0, -np.inf], dtype=np.float16)
    result = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=1e-3, tol_rel=1e-3)
    assert not result["within_tolerance"], "Inf sign mismatch must fail"


# ── Exact boundary (<= behavior) ────────────────────────────────────────

def test_compare_exact_boundary():
    """Values exactly at the tolerance boundary (<= semantics).

    Uses a tolerance that aligns with fp16 step size so that
    abs_diff falls exactly at tol_abs, exercising ≤ vs < behavior.
    """
    # fp16 step at 1.0 is 2^-10 = 0.0009765625
    tol = float(np.float16(2.0 ** -10))
    ref = np.array([1.0], dtype=np.float16)
    hw = np.array([1.0 + tol], dtype=np.float16)
    result = GoldenSFU.compare_hw_vs_ref(hw, ref, tol_abs=tol, tol_rel=1e-6)
    assert result["within_tolerance"], (
        f"exact boundary should pass (≤ tol): "
        f"max_abs={result['max_abs_err']:.6e} tol_abs={tol:.6e}"
    )
