"""Tests for Dashboard JSON / Markdown report generation."""

import json
import math
import os
import tempfile
from pathlib import Path

import pytest

from timing.dashboard import Dashboard
from timing.types import RequestMetrics


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _make_llm_request_metrics() -> RequestMetrics:
    return RequestMetrics(
        prompt_len=128,
        output_tokens=4,
        prefill_cycles=100_000_000,
        decode_cycles_per_token=[10_000_000, 12_000_000, 14_000_000, 16_000_000],
        ttft_us=137_500.0,
        tps=61.53846153846154,
        tpot_us=17_500.0,
        itl_us_list=[15_000.0, 17_500.0, 20_000.0],
    )


def _make_module_breakdown() -> dict[str, int]:
    return {
        "mxu": 600,
        "sfu": 200,
        "vector": 100,
        "dma_weight": 80,
        "dma_effective": 60,
        "kv_cache": 40,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestGenerateJsonRequiredKeys:
    """generate_json must include all required keys (LLM + CV)."""

    _LLM_REQUIRED = {
        "model", "engine", "config", "ttft_ms", "tps", "tpot_us",
        "itl_us_p50", "itl_us_p90", "itl_us_p99", "prefill_ms",
        "decode_per_token_us", "module_breakdown", "module_utilization_pct",
        "bandwidth_utilization_pct", "dma_overlap_ratio", "total_cycles",
        "timestamp",
    }

    _CV_REQUIRED = {
        "model", "engine", "config", "fps", "inference_latency_us",
        "itl_us_p50", "itl_us_p90", "itl_us_p99",
        "module_breakdown", "module_utilization_pct",
        "bandwidth_utilization_pct", "dma_overlap_ratio", "total_cycles",
        "timestamp",
    }

    _CV_EXCLUDED = {"tps", "ttft_ms", "tpot_us", "prefill_ms", "decode_per_token_us"}

    def test_llm_keys_present(self):
        result = Dashboard.generate_json(
            model_name="qwen2.5-1.5b",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )
        keys = set(result.keys())
        missing = self._LLM_REQUIRED - keys
        assert not missing, f"Missing LLM keys: {missing}"

    def test_cv_keys_present(self):
        result = Dashboard.generate_json(
            model_name="resnet18",
            request_metrics=RequestMetrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=True,
        )
        keys = set(result.keys())
        missing = self._CV_REQUIRED - keys
        assert not missing, f"Missing CV keys: {missing}"

    def test_cv_excludes_llm_keys(self):
        result = Dashboard.generate_json(
            model_name="resnet18",
            request_metrics=RequestMetrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=True,
        )
        keys = set(result.keys())
        overlap = self._CV_EXCLUDED & keys
        assert not overlap, f"CV output should not contain: {overlap}"


class TestGenerateJsonNoNanInf:
    """No NaN or Inf values, even with edge-case inputs."""

    def test_regular_llm_no_nan_inf(self):
        result = Dashboard.generate_json(
            model_name="qwen2.5-1.5b",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )
        for key, val in result.items():
            if isinstance(val, float):
                assert not math.isnan(val), f"NaN at {key}"
                assert not math.isinf(val), f"Inf at {key}"

    def test_empty_breakdown_no_nan_inf(self):
        result = Dashboard.generate_json(
            model_name="test",
            request_metrics=RequestMetrics(),
            module_breakdown={},
            freq_mhz=800,
            is_cv=False,
        )
        for key, val in result.items():
            if isinstance(val, float):
                assert not math.isnan(val), f"NaN at {key}"
                assert not math.isinf(val), f"Inf at {key}"

    def test_empty_itl_no_nan_inf(self):
        rm = _make_llm_request_metrics()
        rm.itl_us_list = []
        result = Dashboard.generate_json(
            model_name="test",
            request_metrics=rm,
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )
        assert result["itl_us_p50"] == 0.0
        assert result["itl_us_p90"] == 0.0
        assert result["itl_us_p99"] == 0.0

    def test_zero_freq_no_nan_inf(self):
        result = Dashboard.generate_json(
            model_name="test",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=0,
            is_cv=False,
        )
        for key, val in result.items():
            if isinstance(val, float):
                assert not math.isnan(val), f"NaN at {key}"
                assert not math.isinf(val), f"Inf at {key}"


class TestGenerateMarkdownContainsSections:
    """Markdown must contain all required sections."""

    def test_all_sections_present(self):
        json_data = Dashboard.generate_json(
            model_name="qwen2.5-1.5b",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )
        md = Dashboard.generate_markdown(json_data)

        assert "# Performance Dashboard — qwen2.5-1.5b" in md
        assert "## Summary" in md
        assert "## Per-Module Cycles" in md
        assert "## Module Utilization" in md
        assert "## ITL Distribution" in md
        assert "## Configuration" in md
        assert "*TTFT (Time-To-First-Token) is engine-only latency" in md

    def test_llm_metrics_in_summary(self):
        json_data = Dashboard.generate_json(
            model_name="qwen2.5-1.5b",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )
        md = Dashboard.generate_markdown(json_data)
        assert "Tps" in md or "tps" in md.lower()
        assert "Ttft Ms" in md or "TTFT" in md
        assert "Tpot Us" in md or "tpot" in md.lower()

    def test_ascii_histogram_present(self):
        json_data = Dashboard.generate_json(
            model_name="qwen2.5-1.5b",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )
        md = Dashboard.generate_markdown(json_data)
        assert "#" in md  # histogram bars are '#'
        assert "us:" in md  # histogram label


class TestSaveCreatesFiles:
    """save() must create both .json and .md files and return their paths."""

    def test_save_creates_json_and_md(self, tmp_path):
        dashboard = Dashboard()
        json_path, md_path = dashboard.save(
            output_dir=tmp_path,
            model_name="qwen-test",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )

        assert os.path.isfile(json_path), f"JSON not created at {json_path}"
        assert os.path.isfile(md_path), f"MD not created at {md_path}"

    def test_json_is_valid_and_has_keys(self, tmp_path):
        dashboard = Dashboard()
        json_path, _ = dashboard.save(
            output_dir=tmp_path,
            model_name="qwen-test",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )

        with open(json_path) as f:
            data = json.load(f)
        assert data["model"] == "qwen-test"
        assert "tps" in data
        assert "ttft_ms" in data

    def test_md_contains_model_name(self, tmp_path):
        dashboard = Dashboard()
        _, md_path = dashboard.save(
            output_dir=tmp_path,
            model_name="qwen-test",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )

        content = Path(md_path).read_text()
        assert "qwen-test" in content

    def test_creates_output_dir_if_missing(self, tmp_path):
        nested = tmp_path / "deeply" / "nested" / "dir"
        dashboard = Dashboard()
        json_path, _ = dashboard.save(
            output_dir=nested,
            model_name="qwen-test",
            request_metrics=_make_llm_request_metrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=False,
        )
        assert os.path.isfile(json_path)


class TestCvDashboardUsesFps:
    """CV mode must use fps / inference_latency_us, not tps / ttft_ms."""

    def test_fps_present_tps_absent(self):
        result = Dashboard.generate_json(
            model_name="resnet18",
            request_metrics=RequestMetrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=True,
        )
        assert "fps" in result
        assert "inference_latency_us" in result
        assert "tps" not in result
        assert "ttft_ms" not in result
        assert "tpot_us" not in result
        assert "prefill_ms" not in result

    def test_fps_formula_correct(self):
        # total_cycles = 600+200+100+80+60+40 = 1080
        # fps = 800 * 1e6 / 1080 ≈ 740740.74
        result = Dashboard.generate_json(
            model_name="resnet18",
            request_metrics=RequestMetrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=True,
        )
        expected = round(800 * 1e6 / 1080, 2)
        assert result["fps"] == expected

    def test_inference_latency_formula_correct(self):
        # inference_latency_us = 1080 / 800 = 1.35
        result = Dashboard.generate_json(
            model_name="resnet18",
            request_metrics=RequestMetrics(),
            module_breakdown=_make_module_breakdown(),
            freq_mhz=800,
            is_cv=True,
        )
        expected = round(1080 / 800, 2)
        assert result["inference_latency_us"] == expected
