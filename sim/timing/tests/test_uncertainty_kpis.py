"""T20 uncertainty-aware KPI tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from timing.dashboard import Dashboard
from timing.metrics import (
    apply_cycle_band,
    apply_sum_of_stages_band,
    apply_throughput_band,
)
from timing.types import RequestMetrics
from timing.uncertainty_kpis import (
    REPO_ROOT,
    build_cv_kpi_report,
    build_qwen_model_family_report,
    build_qwen_prefill_2000_report,
    run_uncertainty_kpis,
    run_uncertainty_kpis_negative,
)


class TestUncertaintyBands:
    """Frozen formula helpers must produce canonical low/base/high bands."""

    def test_cycle_band(self):
        band = apply_cycle_band(100.0)
        assert band == {"low": 70.0, "base": 100.0, "high": 130.0}

    def test_throughput_band_is_inverse(self):
        band = apply_throughput_band(130.0)
        assert band["base"] == 130.0
        assert band["low"] == pytest.approx(100.0, rel=1e-9)
        assert band["high"] == pytest.approx(185.7142857, rel=1e-6)

    def test_sum_of_stages_independent_rss(self):
        # Three equal stages of 100 -> RSS deviation = sqrt(3 * 30^2) ≈ 51.96
        band = apply_sum_of_stages_band([100.0, 100.0, 100.0], correlation="independent")
        assert band["base"] == 300.0
        assert band["low"] == pytest.approx(248.04, rel=1e-3)
        assert band["high"] == pytest.approx(351.96, rel=1e-3)

    def test_sum_of_stages_correlated_linear(self):
        band = apply_sum_of_stages_band([100.0, 100.0, 100.0], correlation="correlated")
        assert band == {"low": 210.0, "base": 300.0, "high": 390.0}

    def test_linear_band_does_not_pass_for_three_independent_stages(self):
        """A mock 3-stage workload where linear ±30% is too wide vs RSS."""
        stages = [100.0, 100.0, 100.0]
        rss_band = apply_sum_of_stages_band(stages, correlation="independent")
        linear_band = apply_sum_of_stages_band(stages, correlation="correlated")

        # RSS high must be strictly less than linear high.
        assert rss_band["high"] < linear_band["high"]
        assert rss_band["low"] > linear_band["low"]
        # The difference is material (>10% of base deviation).
        assert (linear_band["high"] - rss_band["high"]) > 30.0

    def test_unsupported_correlation_raises(self):
        with pytest.raises(ValueError):
            apply_sum_of_stages_band([100.0], correlation="mixed")


class TestDashboardUncertainty:
    """Dashboard must emit low/base/high bands when uncertainty=True."""

    def _make_metrics(self) -> RequestMetrics:
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

    def test_llm_uncertainty_bands_present(self):
        result = Dashboard.generate_json(
            model_name="qwen-test",
            request_metrics=self._make_metrics(),
            module_breakdown={"mxu": 600},
            freq_mhz=800,
            is_cv=False,
            uncertainty=True,
        )
        for key in ("ttft_ms", "tps", "tpot_us", "prefill_ms", "decode_per_token_us"):
            assert isinstance(result[key], dict), f"{key} is not a band dict"
            assert {"low", "base", "high"} <= set(result[key].keys()), f"{key} missing band keys"

    def test_cv_uncertainty_bands_present(self):
        result = Dashboard.generate_json(
            model_name="resnet-test",
            request_metrics=RequestMetrics(),
            module_breakdown={"mxu": 800},
            freq_mhz=800,
            is_cv=True,
            uncertainty=True,
        )
        for key in ("fps", "inference_latency_us"):
            assert isinstance(result[key], dict), f"{key} is not a band dict"
            assert {"low", "base", "high"} <= set(result[key].keys()), f"{key} missing band keys"

    def test_canonical_hash_excludes_timestamp(self):
        result = Dashboard.generate_json(
            model_name="qwen-test",
            request_metrics=self._make_metrics(),
            module_breakdown={"mxu": 600},
            freq_mhz=800,
            uncertainty=True,
        )
        assert "canonical_hash" in result
        assert len(result["canonical_hash"]) == 64

    def test_backward_compatible_scalar_when_uncertainty_false(self):
        result = Dashboard.generate_json(
            model_name="qwen-test",
            request_metrics=self._make_metrics(),
            module_breakdown={"mxu": 600},
            freq_mhz=800,
            is_cv=False,
            uncertainty=False,
        )
        assert isinstance(result["tps"], float)
        assert isinstance(result["ttft_ms"], float)


class TestUncertaintyKpiReports:
    """Report builders must produce low/base/high bands and report_only=true."""

    def test_qwen_prefill_2000_report(self):
        report = build_qwen_prefill_2000_report()
        assert report["case"] == "qwen-prefill-2000"
        assert report["report_only"] is True
        for key in ("ttft_ms", "tps", "tpot_us", "prefill_ms", "decode_per_token_us"):
            assert {"low", "base", "high"} <= set(report[key].keys())
        assert report["canonical_hash"]
        assert report["prefill_cycles"] > 0
        assert report["first_decode_cycles"] > 0

    def test_cv_kpi_reports(self):
        for workload_id in ("mobilenetv3", "resnet50", "yolov8n"):
            report = build_cv_kpi_report(workload_id)
            assert report["case"] == workload_id
            assert report["report_only"] is True
            assert {"low", "base", "high"} <= set(report["fps"].keys())
            assert {"low", "base", "high"} <= set(report["inference_latency_us"].keys())
            assert report["canonical_hash"]

    def test_qwen_model_family_report(self):
        report = build_qwen_model_family_report()
        assert report["case"] == "qwen-model-family"
        assert report["report_only"] is True
        assert len(report["models"]) == 3
        for m in report["models"]:
            assert {"low", "base", "high"} <= set(m["tps"].keys())
            assert m["canonical_hash"]

    def test_run_uncertainty_kpis_all_cases(self):
        cases = ["qwen-prefill-2000", "qwen-model-family", "mobilenetv3", "resnet50", "yolov8n"]
        report = run_uncertainty_kpis(cases)
        assert report["verdict"] == "pass"
        assert report["report_only"] is True
        assert set(report["reports_data"].keys()) == set(cases)


class TestNegativeFaults:
    """RED path: all four declared faults must be rejected."""

    def test_negative_runner_rejects_all_faults(self):
        report = run_uncertainty_kpis_negative([
            "timestamp-in-hash",
            "direct-throughput-band",
            "empty-report",
            "kpi-gating",
        ])
        assert report["rejected"] == 4
        assert report["accepted"] == 0
        assert report["verdict"] == "pass"


class TestSignoffRunnerCLI:
    """scripts/run_func_model_perf_signoff.py must expose T20 QA scenarios."""

    RUNNER = REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"

    def test_run_uncertainty_kpis_exits_zero(self):
        cmd = [
            sys.executable, str(self.RUNNER),
            "run",
            "--reports", "uncertainty-kpis",
            "--cases", "qwen-prefill-2000,qwen-model-family,mobilenetv3,resnet50,yolov8n",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["verdict"] == "pass"
        assert data["report_only"] is True

    def test_negative_uncertainty_kpis_exits_zero(self):
        cmd = [
            sys.executable, str(self.RUNNER),
            "negative",
            "--case", "uncertainty-kpis",
            "--faults", "timestamp-in-hash,direct-throughput-band,empty-report,kpi-gating",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["accepted"] == 0
        assert data["rejected"] == 4
        assert data["all_passed"] is True

    def test_run_produces_json_evidence(self, tmp_path):
        evidence = tmp_path / "uncertainty-kpis.json"
        cmd = [
            sys.executable, str(self.RUNNER),
            "run",
            "--reports", "uncertainty-kpis",
            "--cases", "qwen-prefill-2000",
            "--evidence-path", str(evidence),
            "--todo-id", "task-20-test",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120)
        assert result.returncode == 0, result.stderr
        assert evidence.is_file()
        payload = json.loads(evidence.read_text())
        assert payload["verdict"] == "pass"
        assert "doneclaim" in payload
        assert payload["doneclaim"]["green_result"]["verdict"] == "pass"
