#!/usr/bin/env python3
"""Regression tests for CV trace generators.

Covers the six synthetic CV trace generators under ``sim/cv/traces/``:
  - yolov8n
  - resnet18
  - resnet50
  - vit
  - qwen_vl_vit
  - sd_unet

Each generator is pure Python and returns a list of trace entries.  The tests
verify that the generator runs without crashing and that every returned entry
has legal dimensions and the expected schema.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_sim_dir = Path(__file__).resolve().parent.parent
if str(_sim_dir) not in sys.path:
    sys.path.insert(0, str(_sim_dir))

from cv.traces.yolov8n_trace import generate_yolov8n_trace
from cv.traces.resnet18_trace import generate_resnet18_trace
from cv.traces.resnet50_trace import generate_resnet50_trace
from cv.traces.vit_trace import generate_vit_trace
from cv.traces.qwen_vl_vit_trace import generate_qwen_vl_vit_trace
from cv.traces.sd_unet_trace import generate_sd_unet_step

_REQUIRED_KEYS = {
    "type",
    "name",
    "M",
    "K",
    "N",
    "im2col_overhead_cycles",
    "sfu_cycles",
}


def _assert_legal_trace(trace: list[dict[str, Any]]) -> None:
    """Shared assertions: non-empty trace with legal entry shapes."""
    assert isinstance(trace, list), f"Expected list, got {type(trace).__name__}"
    assert len(trace) > 0, "Trace must not be empty"

    for idx, entry in enumerate(trace):
        assert isinstance(entry, dict), (
            f"Entry {idx} must be a dict, got {type(entry).__name__}"
        )
        missing = _REQUIRED_KEYS - entry.keys()
        assert not missing, f"Entry {idx} missing required keys: {missing}"

        # String metadata
        assert isinstance(entry["name"], str), f"Entry {idx} name must be str"
        assert isinstance(entry["type"], str), f"Entry {idx} type must be str"

        # Dimensions must be integers and non-negative.
        for key in ("M", "K", "N", "sfu_cycles"):
            value = entry[key]
            assert isinstance(value, int), (
                f"Entry {idx} key '{key}' must be int, got {type(value).__name__}"
            )
            assert value >= 0, f"Entry {idx} key '{key}' must be >= 0, got {value}"

        # im2col overhead is a float estimate and must be non-negative.
        overhead = entry["im2col_overhead_cycles"]
        assert isinstance(overhead, (int, float)), (
            f"Entry {idx} im2col_overhead_cycles must be numeric, got {type(overhead).__name__}"
        )
        assert overhead >= 0, (
            f"Entry {idx} im2col_overhead_cycles must be >= 0, got {overhead}"
        )

        # GEMM-bearing entries must have strictly positive M/K/N.
        if entry["type"] in ("pointwise_conv", "depthwise_conv", "gemm", "conv"):
            assert entry["M"] > 0, f"Entry {idx} GEMM entry has zero M"
            assert entry["K"] > 0, f"Entry {idx} GEMM entry has zero K"
            assert entry["N"] > 0, f"Entry {idx} GEMM entry has zero N"


def _trace_macs(trace: list[dict[str, Any]], *, mul_add: bool = False) -> int:
    """Sum M*K*N over the trace; double when the generator counts mul+add."""
    raw = sum(e["M"] * e["K"] * e["N"] for e in trace)
    return 2 * raw if mul_add else raw


class TestCVTraceGenerators:
    """Per-generator smoke and shape-legality tests."""

    def test_yolov8n_trace(self):
        """YOLOv8n trace generates non-empty legal entries."""
        trace = generate_yolov8n_trace()
        _assert_legal_trace(trace)
        macs = _trace_macs(trace, mul_add=True)
        assert 8_000_000_000 <= macs <= 9_500_000_000, (
            f"YOLOv8n MACs {macs:,} outside expected [8G, 9.5G]"
        )

    def test_resnet18_trace(self):
        """ResNet-18 trace generates non-empty legal entries."""
        trace = generate_resnet18_trace()
        _assert_legal_trace(trace)
        macs = _trace_macs(trace)
        assert 1_700_000_000 <= macs <= 2_000_000_000, (
            f"ResNet-18 MACs {macs:,} outside expected [1.7G, 2.0G]"
        )

    def test_resnet50_trace(self):
        """ResNet-50 trace generates non-empty legal entries."""
        trace = generate_resnet50_trace()
        _assert_legal_trace(trace)
        macs = _trace_macs(trace)
        assert 3_500_000_000 <= macs <= 4_300_000_000, (
            f"ResNet-50 MACs {macs:,} outside expected [3.5G, 4.3G]"
        )

    def test_vit_trace(self):
        """ViT-B/16 trace generates non-empty legal entries."""
        trace = generate_vit_trace()
        _assert_legal_trace(trace)
        macs = _trace_macs(trace)
        assert 16_000_000_000 <= macs <= 19_000_000_000, (
            f"ViT-B/16 MACs {macs:,} outside expected [16G, 19G]"
        )

    def test_qwen_vl_vit_single_crop_trace(self):
        """Qwen2.5-VL ViT single-crop trace generates legal entries."""
        trace = generate_qwen_vl_vit_trace(num_crops=1)
        _assert_legal_trace(trace)
        macs = _trace_macs(trace)
        assert macs > 250_000_000_000, (
            f"Qwen-VL ViT single-crop MACs {macs:,} below expected 250G"
        )

    def test_qwen_vl_vit_multi_crop_trace(self):
        """Qwen2.5-VL ViT 4-crop trace scales linearly and stays legal."""
        single = generate_qwen_vl_vit_trace(num_crops=1)
        multi = generate_qwen_vl_vit_trace(num_crops=4)
        _assert_legal_trace(multi)
        assert len(multi) == 4 * len(single), (
            f"4-crop trace length {len(multi)} != 4 * single-crop {len(single)}"
        )
        macs = _trace_macs(multi)
        assert macs > 4 * 250_000_000_000, (
            f"Qwen-VL ViT 4-crop MACs {macs:,} below expected 1T"
        )

    def test_sd_unet_step_trace(self):
        """SD 1.5 UNet single-step trace generates non-empty legal entries."""
        trace = generate_sd_unet_step()
        _assert_legal_trace(trace)
        macs = _trace_macs(trace)
        assert 150_000_000_000 <= macs <= 500_000_000_000, (
            f"SD UNet step MACs {macs:,} outside expected [150G, 500G]"
        )
