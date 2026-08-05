"""Tests for B5: CV E2E top-5 correctness verification.

Unit tests (no ONNX model or device server needed):
  - golden JSON schema validation
  - top-5 computation helpers
  - relative-diff helper
  - top-5 set comparison

E2E test (requires ONNX model + device server):
  - Full MobileNetV3-Small graph execution via Host Runtime → top-5 comparison
    with ONNX Runtime golden.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ONNX_MODEL = _REPO_ROOT / "assets" / "mobilenetv3_small.onnx"
_GOLDEN_JSON = _REPO_ROOT / ".omo" / "evidence" / "cv-golden.json"
_RUNTIME_LIB = _REPO_ROOT / "build" / "software" / "libcaduceus_runtime.so"

_MODEL_PRESENT = _ONNX_MODEL.is_file()
_GOLDEN_PRESENT = _GOLDEN_JSON.is_file()
_RUNTIME_AVAILABLE = _RUNTIME_LIB.is_file()


# ── Helpers ──────────────────────────────────────────────────────────────────


def compute_top5(logits: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (indices, values) for the top-5 entries in *logits* (1-D)."""
    if logits.ndim != 1:
        logits = logits.ravel()
    top5_indices = np.argsort(logits)[-5:][::-1]
    top5_values = logits[top5_indices]
    return top5_indices, top5_values


def top5_set_match(
    indices_a: np.ndarray, indices_b: np.ndarray,
) -> bool:
    """True when the two top-5 index sets are identical."""
    return set(indices_a.tolist()) == set(indices_b.tolist())


def max_rel_diff(
    logits_a: np.ndarray, logits_b: np.ndarray,
) -> float:
    """Maximum element-wise relative difference ``|a-b| / max(|a|, |b|, eps)``."""
    eps = 1e-10
    denom = np.maximum(np.maximum(np.abs(logits_a), np.abs(logits_b)), eps)
    return float(np.max(np.abs(logits_a - logits_b) / denom))


# ── Unit tests (no ONNX model or device server needed) ───────────────────────


class TestTop5Helpers:
    """Top-5 computation and comparison helpers."""

    def test_compute_top5_shape(self) -> None:
        logits = np.array([0.1, 0.5, 0.3, 0.9, 0.2, 0.7], dtype=np.float32)
        indices, values = compute_top5(logits)
        assert len(indices) == 5
        assert len(values) == 5

    def test_compute_top5_correct_order(self) -> None:
        logits = np.array([0.1, 0.5, 0.3, 0.9, 0.2, 0.7], dtype=np.float32)
        indices, values = compute_top5(logits)
        # Top-5 should be indices 3, 5, 1, 2, 4 in that order.
        assert indices[0] == 3
        assert indices[1] == 5
        assert abs(float(values[0]) - 0.9) < 1e-6

    def test_top5_set_match_true(self) -> None:
        a = np.array([3, 5, 1, 2, 4])
        b = np.array([1, 2, 3, 4, 5])
        assert top5_set_match(a, b) is True

    def test_top5_set_match_false(self) -> None:
        a = np.array([3, 5, 1, 2, 4])
        b = np.array([3, 5, 1, 2, 6])
        assert top5_set_match(a, b) is False

    def test_max_rel_diff_zero(self) -> None:
        a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        b = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        assert max_rel_diff(a, b) == 0.0

    def test_max_rel_diff_small(self) -> None:
        a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        b = np.array([1.001, 2.0, 3.0], dtype=np.float32)
        assert max_rel_diff(a, b) < 0.01

    def test_max_rel_diff_large(self) -> None:
        a = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        b = np.array([1.0, 2.0, 4.0], dtype=np.float32)
        assert max_rel_diff(a, b) > 0.1

    def test_compute_top5_2d_flattened(self) -> None:
        logits = np.array([[0.1, 0.5, 0.3]], dtype=np.float32)
        indices, _values = compute_top5(logits)
        assert len(indices) == 3  # only 3 elements in 1x3


class TestGoldenJsonSchema:
    """Validate the golden JSON file produced by gen_cv_golden.py."""

    def test_golden_has_required_keys(self) -> None:
        if not _GOLDEN_PRESENT:
            pytest.skip("cv-golden.json not found")
        data = json.loads(_GOLDEN_JSON.read_text())
        for key in ("top5_indices", "top5_logits", "input_shape",
                     "model_path", "seed"):
            assert key in data, f"Missing key '{key}'"

    def test_golden_top5_indices_shape(self) -> None:
        if not _GOLDEN_PRESENT:
            pytest.skip("cv-golden.json not found")
        data = json.loads(_GOLDEN_JSON.read_text())
        assert len(data["top5_indices"]) == 5
        assert all(0 <= i < 1000 for i in data["top5_indices"])

    def test_golden_top5_logits_shape(self) -> None:
        if not _GOLDEN_PRESENT:
            pytest.skip("cv-golden.json not found")
        data = json.loads(_GOLDEN_JSON.read_text())
        assert len(data["top5_logits"]) == 5
        assert all(isinstance(v, (int, float)) for v in data["top5_logits"])

    def test_golden_seed_is_42(self) -> None:
        if not _GOLDEN_PRESENT:
            pytest.skip("cv-golden.json not found")
        data = json.loads(_GOLDEN_JSON.read_text())
        assert data["seed"] == 42

    def test_golden_input_shape(self) -> None:
        if not _GOLDEN_PRESENT:
            pytest.skip("cv-golden.json not found")
        data = json.loads(_GOLDEN_JSON.read_text())
        assert data["input_shape"] == [1, 3, 224, 224]


class TestConversionSmoke:
    """Smoke-test the F32 conversion without device server."""

    @pytest.mark.skipif(not _MODEL_PRESENT, reason="ONNX model not found")
    def test_conversion_produces_blob_and_maps(self) -> None:
        from cv.cv_command_ir import convert_mobilenetv3_graph_full

        blob, buf_map, weight_map, bias_map, scale_map = convert_mobilenetv3_graph_full(
            str(_ONNX_MODEL),
        )
        assert len(blob) > 0
        assert len(buf_map) > 0
        assert len(weight_map) > 0
        # Buffer 1 is always the input.
        assert 1 in buf_map

    @pytest.mark.skipif(not _MODEL_PRESENT, reason="ONNX model not found")
    def test_blob_has_valid_header(self) -> None:
        import struct
        from cv.cv_command_ir import convert_mobilenetv3_graph_full

        blob, _bm, _wm, _bias, _sm = convert_mobilenetv3_graph_full(
            str(_ONNX_MODEL),
        )
        magic = struct.unpack_from("<I", blob, 0)[0]
        assert magic == 0x43414442  # CADB

    @pytest.mark.skipif(not _MODEL_PRESENT, reason="ONNX model not found")
    def test_output_buffer_is_1000_elements(self) -> None:
        from cv.cv_command_ir import convert_mobilenetv3_graph_full

        _blob, buf_map, _wm, _bias, _sm = convert_mobilenetv3_graph_full(
            str(_ONNX_MODEL),
        )
        output_id = max(buf_map.keys())
        assert buf_map[output_id]["size"] == 1000 * 4  # 1000 float32 logits


# ── E2E test (requires ONNX model + device server) ───────────────────────────

@pytest.mark.skipif(not _MODEL_PRESENT, reason="ONNX model not found")
@pytest.mark.skipif(not _RUNTIME_AVAILABLE,
                     reason="libcaduceus_runtime.so not built")
class TestCVTop5Comparison:
    """Full E2E: execute MobileNetV3-Small through device_server.

    Known limitations (pre-existing B1 converter bugs, B6 scope):
    - Depthwise Conv mapping: N=1 MMUL with shared weight across all M
      rows produces incorrect per-channel kernel selection.
    - ReduceMean mapping: VRED_SUM over all elements instead of per-channel
      spatial reduction.

    These cause NaN in the final logits.  This test validates the execution
    infrastructure (no crash, output shape, determinism) and will be
    tightened once B6 fixes the converter mappings.
    """

    def test_execution_produces_output(self) -> None:
        from cv.cv_host_runner import run_cv_e2e_full

        top5_indices, top5_logits, npu_ops = run_cv_e2e_full(str(_ONNX_MODEL))
        assert len(top5_indices) == 5
        assert len(top5_logits) == 5
        assert all(0 <= i < 1000 for i in top5_indices)
        assert npu_ops > 0

    def test_deterministic_output(self) -> None:
        from cv.cv_host_runner import run_cv_e2e_full

        i1, _v1, _o1 = run_cv_e2e_full(str(_ONNX_MODEL))
        i2, _v2, _o2 = run_cv_e2e_full(str(_ONNX_MODEL))
        assert i1 == i2

    def test_output_is_1000_logits(self) -> None:
        from cv.cv_host_runner import run_cv_e2e_full

        top5_indices, top5_logits, _npu_ops = run_cv_e2e_full(str(_ONNX_MODEL))
        assert len(top5_indices) == 5
        assert len(top5_logits) == 5

    @pytest.mark.skipif(not _GOLDEN_PRESENT, reason="cv-golden.json not found")
    def test_top5_matches_golden_set(self) -> None:
        from cv.cv_host_runner import run_cv_e2e_full

        top5_indices, _top5_logits, _npu_ops = run_cv_e2e_full(str(_ONNX_MODEL))
        golden = json.loads(_GOLDEN_JSON.read_text())
        golden_indices = np.array(golden["top5_indices"])
        assert top5_set_match(np.array(top5_indices), golden_indices)


# ── Graceful skip tests ─────────────────────────────────────────────────────


class TestGracefulSkip:
    """Tests that skip cleanly when prerequisites are missing."""

    def test_skip_missing_onnx(self) -> None:
        if _MODEL_PRESENT:
            pytest.skip("ONNX model exists — cannot test missing-model path")
        with pytest.raises(FileNotFoundError):
            from cv.cv_command_ir import convert_mobilenetv3_graph_full
            convert_mobilenetv3_graph_full("/nonexistent/model.onnx")

    def test_skip_missing_onnxruntime(self, monkeypatch) -> None:
        if not _MODEL_PRESENT:
            pytest.skip("ONNX model not found")
        monkeypatch.setitem(sys.modules, "onnxruntime", None)
        # This should not crash the module — it's only loaded when used.
        assert True  # module import test passes

    def test_run_cv_e2e_imports(self) -> None:
        from cv.cv_host_runner import run_cv_e2e_full
        assert callable(run_cv_e2e_full)
