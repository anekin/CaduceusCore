"""Tests for ArcModel precision metrics."""
import numpy as np
import pytest
from arc_model import ArcModel, PrecisionReport


def test_precision_report_mse_fields():
    """PrecisionReport exposes MSE/max-abs-error fields and _run_precision populates them."""
    # Direct construction with all positional fields (Task 18 constructor smoke test)
    pr = PrecisionReport(1, 0.99, 0.97, 0.01, "l0", 0.97, True, 0.001, 0.0005, 0.05)
    assert pr.mse_mean == pytest.approx(0.001)
    assert pr.mse_min == pytest.approx(0.0005)
    assert pr.max_abs_error == pytest.approx(0.05)

    # to_dict() includes the new fields
    d = pr.to_dict()
    assert d["mse_mean"] == pytest.approx(0.001)
    assert d["mse_min"] == pytest.approx(0.0005)
    assert d["max_abs_error"] == pytest.approx(0.05)

    # _run_precision populates the fields for a minimal synthetic weight set
    rng = np.random.RandomState(42)
    weights = {"test.weight": rng.randn(128, 128).astype(np.float32)}
    arc = ArcModel()
    pr2 = arc._run_precision(weights, "per-channel")
    assert pr2.n_layers == 1
    assert pr2.mse_mean >= 0.0
    assert pr2.mse_min >= 0.0
    assert pr2.max_abs_error >= 0.0
    assert pr2.mse_min <= pr2.mse_mean
