"""Tests for sim.cv.cv_command_ir — ONNX → Caduceus command IR converter.

Covers:
  (a) Conv pointwise mapping
  (b) Conv depthwise mapping
  (c) HardSwish
  (d) HardSigmoid
  (e) GlobalAveragePool
  (f) SE block round-trip
  (g) unsupported op raises UnsupportedCVOp
  (h) full MobileNetV3 graph produces a decode-able blob
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

# Ensure sim/ and repo root are on sys.path
_sim_dir = Path(__file__).resolve().parent.parent
_repo_root = _sim_dir.parent
for _p in (str(_sim_dir), str(_repo_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cv.cv_command_ir import (
    UnsupportedCVOp,
    convert_layer_list,
    convert_mobilenetv3_graph,
    convert_mobilenetv3_graph_full,
    decode_cv_blob,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _roundtrip(data: bytes) -> dict[str, Any]:
    """Round-trip encode→decode and return summary dict."""
    blob = decode_cv_blob(data)
    return {
        "num_commands": blob.num_commands(),
        "version_major": blob.version_major,
        "version_minor": blob.version_minor,
        "caps": blob.caps,
    }


def _minimal_conv_layer(
    name: str = "conv_pw",
    in_shape: list[int] | None = None,
    out_shape: list[int] | None = None,
    kernel: list[int] | None = None,
    groups: int = 1,
    stride: list[int] | None = None,
    padding: list[int] | None = None,
) -> dict[str, Any]:
    """Return a synthetic pointwise conv layer dict."""
    return {
        "type": "Conv",
        "name": name,
        "in_shape": in_shape or [1, 16, 56, 56],
        "out_shape": out_shape or [1, 16, 56, 56],
        "kernel": kernel or [1, 1],
        "groups": groups,
        "stride": stride or [1, 1],
        "padding": padding or [0, 0, 0, 0],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestConvPointwise:
    """(a) Conv pointwise mapping — 1×1 kernel, groups=1."""

    def test_roundtrip(self):
        layer = _minimal_conv_layer("pw_conv", groups=1)
        data = convert_layer_list([layer], input_shape=[1, 16, 56, 56])
        info = _roundtrip(data)
        # 1 MMUL + 1 barrier = 2 commands
        assert info["num_commands"] >= 2

    def test_mmul_opcode_present(self):
        layer = _minimal_conv_layer("pw_conv_2", groups=1)
        data = convert_layer_list([layer], input_shape=[1, 32, 28, 28])
        blob = decode_cv_blob(data)
        cmd_kinds = {c.kind for c in blob.commands}
        assert "mmul" in cmd_kinds


class TestConvDepthwise:
    """(b) Conv depthwise mapping — groups == C_in."""

    def test_roundtrip(self):
        layer = _minimal_conv_layer(
            "dw_conv",
            in_shape=[1, 16, 56, 56],
            out_shape=[1, 16, 56, 56],
            kernel=[3, 3],
            groups=16,
        )
        data = convert_layer_list([layer], input_shape=[1, 16, 56, 56])
        info = _roundtrip(data)
        assert info["num_commands"] >= 2

    def test_mmul_opcode_present(self):
        layer = _minimal_conv_layer(
            "dw_conv_2",
            in_shape=[1, 24, 28, 28],
            out_shape=[1, 24, 28, 28],
            kernel=[3, 3],
            groups=24,
        )
        data = convert_layer_list([layer], input_shape=[1, 24, 28, 28])
        blob = decode_cv_blob(data)
        cmd_kinds = {c.kind for c in blob.commands}
        assert "mmul" in cmd_kinds


class TestHardSwish:
    """(c) HardSwish → SFU_RELU + VMUL (B3 INT path)."""

    def test_roundtrip(self):
        layer: dict[str, Any] = {
            "type": "HardSwish",
            "name": "hs_test",
            "in_shape": [1, 16, 56, 56],
            "out_shape": [1, 16, 56, 56],
        }
        data = convert_layer_list([layer], input_shape=[1, 16, 56, 56])
        info = _roundtrip(data)
        # 2 commands (SFU + Vector) + 1 barrier
        assert info["num_commands"] >= 3

    def test_sfu_relu_present(self):
        layer: dict[str, Any] = {
            "type": "HardSwish",
            "name": "hs_test_2",
            "in_shape": [1, 10, 10, 10],
            "out_shape": [1, 10, 10, 10],
        }
        data = convert_layer_list([layer])
        blob = decode_cv_blob(data)
        cmd_kinds = {c.kind for c in blob.commands}
        assert "sfu" in cmd_kinds
        assert "vector" in cmd_kinds


class TestHardSigmoid:
    """(d) HardSigmoid → SFU_RELU + VMUL (B3 INT path)."""

    def test_roundtrip(self):
        layer: dict[str, Any] = {
            "type": "HardSigmoid",
            "name": "hsm_test",
            "in_shape": [1, 8, 32, 32],
            "out_shape": [1, 8, 32, 32],
        }
        data = convert_layer_list([layer], input_shape=[1, 8, 32, 32])
        info = _roundtrip(data)
        assert info["num_commands"] >= 3

    def test_sfu_relu_present(self):
        layer: dict[str, Any] = {
            "type": "HardSigmoid",
            "name": "hsm_test_2",
            "in_shape": [1, 20, 10, 10],
            "out_shape": [1, 20, 10, 10],
        }
        data = convert_layer_list([layer])
        blob = decode_cv_blob(data)
        cmd_kinds = {c.kind for c in blob.commands}
        assert "sfu" in cmd_kinds
        assert "vector" in cmd_kinds


class TestHardSwishHardSigmoidF32:
    """F32 path uses dedicated SFU opcodes for HardSwish/HardSigmoid."""

    @pytest.mark.skipif(
        not (Path(__file__).resolve().parent.parent.parent
             / "assets" / "mobilenetv3_small.onnx").is_file(),
        reason="ONNX model not found",
    )
    def test_hardswish_and_hardsigmoid_opcodes_in_full_graph(self):
        onnx_path = str(
            Path(__file__).resolve().parent.parent.parent
            / "assets" / "mobilenetv3_small.onnx"
        )
        blob, _buf_map, _wm, _bias, _sm = convert_mobilenetv3_graph_full(
            onnx_path
        )
        decoded = decode_cv_blob(blob)
        sfu_ops = {c.sfu[0] for c in decoded.commands if c.kind == "sfu"}
        assert 5 in sfu_ops, "HardSwish SFU opcode 5 not found"
        assert 6 in sfu_ops, "HardSigmoid SFU opcode 6 not found"


class TestGlobalAveragePool:
    """(e) GlobalAveragePool → segmented VRED_SUM (mean per channel)."""

    def test_roundtrip(self):
        layer: dict[str, Any] = {
            "type": "GlobalAveragePool",
            "name": "gap_test",
            "in_shape": [1, 96, 7, 7],
            "out_shape": [1, 96, 1, 1],
        }
        data = convert_layer_list([layer], input_shape=[1, 96, 7, 7])
        info = _roundtrip(data)
        assert info["num_commands"] >= 2

    def test_vector_reduction_present(self):
        layer: dict[str, Any] = {
            "type": "GlobalAveragePool",
            "name": "gap_test_2",
            "in_shape": [1, 96, 7, 7],
            "out_shape": [1, 96, 1, 1],
        }
        data = convert_layer_list([layer])
        blob = decode_cv_blob(data)
        vector_cmds = [c for c in blob.commands if c.kind == "vector"]
        assert len(vector_cmds) == 1
        assert vector_cmds[0].vector[0] == 3  # VRED_SUM


class TestSEBlock:
    """(f) SE block — ReduceMean + Conv + ReLU + Conv + HardSigmoid + Mul."""

    def _se_layers(self) -> list[dict[str, Any]]:
        """Build a synthetic SE-block layer list.

        Pattern: ReduceMean → Conv(1×1) → ReLU → Conv(1×1) → HardSigmoid → Mul
        """
        return [
            {
                "type": "ReduceMean",
                "name": "se_rm",
                "in_shape": [1, 24, 28, 28],
                "out_shape": [1, 24, 1, 1],
                "se_block": True,
            },
            {
                "type": "Conv",
                "name": "se_conv1",
                "in_shape": [1, 24, 1, 1],
                "out_shape": [1, 6, 1, 1],
                "kernel": [1, 1],
                "groups": 1,
                "stride": [1, 1],
                "padding": [0, 0, 0, 0],
                "se_block": True,
            },
            {
                "type": "Relu",
                "name": "se_relu",
                "in_shape": [1, 6, 1, 1],
                "out_shape": [1, 6, 1, 1],
                "se_block": True,
            },
            {
                "type": "Conv",
                "name": "se_conv2",
                "in_shape": [1, 6, 1, 1],
                "out_shape": [1, 24, 1, 1],
                "kernel": [1, 1],
                "groups": 1,
                "stride": [1, 1],
                "padding": [0, 0, 0, 0],
                "se_block": True,
            },
            {
                "type": "HardSigmoid",
                "name": "se_hsigm",
                "in_shape": [1, 24, 1, 1],
                "out_shape": [1, 24, 1, 1],
                "se_block": True,
            },
            {
                "type": "Mul",
                "name": "se_mul",
                "in_shape": [1, 24, 28, 28],
                "out_shape": [1, 24, 28, 28],
                "se_block": True,
                "se_output": True,
            },
        ]

    def test_roundtrip(self):
        data = convert_layer_list(self._se_layers(), input_shape=[1, 24, 28, 28])
        info = _roundtrip(data)
        # 6 layers: RM→Conv→ReLU→Conv→HSigm→Mul + barrier = many commands
        assert info["num_commands"] >= 7
        assert info["version_major"] == 1

    def test_all_op_types_present(self):
        data = convert_layer_list(self._se_layers(), input_shape=[1, 24, 28, 28])
        blob = decode_cv_blob(data)
        cmd_kinds = {c.kind for c in blob.commands}
        assert "mmul" in cmd_kinds, "SE block must contain Conv→MMUL"
        assert "sfu" in cmd_kinds, "SE block must contain ReLU/HardSigmoid→SFU"
        assert "vector" in cmd_kinds, "SE block must contain ReduceMean/Mul→Vector"


class TestUnsupportedOp:
    """(g) Unsupported op raises ``UnsupportedCVOp``."""

    def test_bad_op_raises(self):
        layer: dict[str, Any] = {
            "type": "NonExistentOp",
            "name": "bad",
            "in_shape": [1, 3, 224, 224],
            "out_shape": [1, 3, 224, 224],
        }
        with pytest.raises(UnsupportedCVOp, match="NonExistentOp"):
            convert_layer_list([layer])

    def test_message_includes_op_name(self):
        layer: dict[str, Any] = {
            "type": "BatchNormalization",
            "name": "bn_like",
            "in_shape": [1, 10, 10, 10],
            "out_shape": [1, 10, 10, 10],
        }
        with pytest.raises(UnsupportedCVOp, match="BatchNormalization"):
            convert_layer_list([layer])


class TestFullMobileNetV3:
    """(h) Full MobileNetV3-Small graph → decode-able blob."""

    @pytest.mark.skipif(
        not os.path.exists(
            os.path.join(os.path.dirname(__file__), "..", "..", "assets", "mobilenetv3_small.onnx")
        ),
        reason="assets/mobilenetv3_small.onnx not found — run scripts/export_mobilenetv3_onnx.py",
    )
    def test_full_graph_roundtrip(self):
        onnx_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "assets", "mobilenetv3_small.onnx"
        )
        data = convert_mobilenetv3_graph(onnx_path)
        info = _roundtrip(data)
        assert info["num_commands"] > 0
        assert info["version_major"] == 1

    def test_missing_onnx_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError, match="ONNX model not found"):
            convert_mobilenetv3_graph("/nonexistent/model.onnx")
