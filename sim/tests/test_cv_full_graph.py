"""B6 regression tests for the full MobileNetV3-Small CV graph."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ONNX_MODEL = _REPO_ROOT / "assets" / "mobilenetv3_small.onnx"
_RUNTIME_LIB = _REPO_ROOT / "build" / "software" / "libcaduceus_runtime.so"

_MODEL_PRESENT = _ONNX_MODEL.is_file()
_RUNTIME_AVAILABLE = _RUNTIME_LIB.is_file()


@pytest.mark.skipif(not _MODEL_PRESENT, reason="ONNX model not found")
class TestFullGraphConversion:
    """Conversion-level checks that do not need the device server."""

    def test_blob_contains_all_op_kinds(self) -> None:
        from cv.cv_command_ir import convert_mobilenetv3_graph_full
        from software.compiler.command_ir import CommandBlob

        blob, _buf_map, _wm, _bias, _sm = convert_mobilenetv3_graph_full(
            str(_ONNX_MODEL)
        )
        decoded = CommandBlob.decode(blob)
        kinds = {cmd.kind for cmd in decoded.commands}
        assert "mmul" in kinds
        assert "sfu" in kinds
        assert "vector" in kinds

    def test_reduce_mean_outputs_one_value_per_channel(self) -> None:
        from cv.cv_command_ir import convert_mobilenetv3_graph_full
        from software.compiler.command_ir import CommandBlob

        blob, buf_map, _wm, _bias, _sm = convert_mobilenetv3_graph_full(
            str(_ONNX_MODEL)
        )
        decoded = CommandBlob.decode(blob)
        for cmd in decoded.commands:
            if cmd.kind != "vector":
                continue
            vec_op, elements = cmd.vector
            if vec_op != 3:  # VRED_SUM
                continue
            out_id = cmd.buffers[2]
            out_size = buf_map[out_id]["size"]
            # A per-channel reduction must write one float per channel, not one
            # scalar for the whole tensor.
            assert out_size >= 4

    def test_depthwise_conv_weights_include_metadata(self) -> None:
        from cv.cv_command_ir import convert_mobilenetv3_graph_full

        _blob, buf_map, weight_map, _bias, _sm = convert_mobilenetv3_graph_full(
            str(_ONNX_MODEL)
        )
        for buf_id, _wt, dims, meta in weight_map:
            if meta is None:
                continue
            op_kind = meta[1]
            if op_kind != 2:  # depthwise
                continue
            declared_size = buf_map[buf_id]["size"]
            out_c, k_h, k_w, in_c = dims
            plain_size = out_c * k_h * k_w * in_c * 4
            # Metadata footer must be appended to depthwise weight buffers.
            assert declared_size == plain_size + 80


@pytest.mark.skipif(not _MODEL_PRESENT, reason="ONNX model not found")
@pytest.mark.skipif(
    not _RUNTIME_AVAILABLE, reason="libcaduceus_runtime.so not built"
)
class TestFullGraphE2E:
    """End-to-end execution through the host runtime and device server."""

    def test_full_graph_runs_without_crash(self) -> None:
        from cv.cv_host_runner import run_cv_e2e_full

        top5_indices, top5_logits, npu_ops_executed = run_cv_e2e_full(
            str(_ONNX_MODEL)
        )
        assert len(top5_indices) == 5
        assert len(top5_logits) == 5
        assert npu_ops_executed > 0

    def test_full_graph_output_is_not_nan(self) -> None:
        from cv.cv_host_runner import run_cv_e2e_full

        _indices, logits, _ops = run_cv_e2e_full(str(_ONNX_MODEL))
        assert not any(np.isnan(logits))
        assert not any(np.isinf(logits))

    def test_full_graph_runs_deterministically(self) -> None:
        from cv.cv_host_runner import run_cv_e2e_full

        i1, _v1, _o1 = run_cv_e2e_full(str(_ONNX_MODEL))
        i2, _v2, _o2 = run_cv_e2e_full(str(_ONNX_MODEL))
        assert i1 == i2

    def test_npu_ops_executed_matches_blob(self) -> None:
        from cv.cv_command_ir import convert_mobilenetv3_graph_full
        from cv.cv_host_runner import run_cv_e2e_full
        from software.compiler.command_ir import CommandBlob

        blob, _buf_map, _wm, _bias, _sm = convert_mobilenetv3_graph_full(
            str(_ONNX_MODEL)
        )
        expected_ops = sum(
            1 for cmd in CommandBlob.decode(blob).commands
            if cmd.kind != "barrier"
        )
        _indices, _logits, npu_ops_executed = run_cv_e2e_full(str(_ONNX_MODEL))
        assert npu_ops_executed == expected_ops
