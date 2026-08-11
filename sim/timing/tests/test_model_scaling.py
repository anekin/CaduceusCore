"""T19 cross-model scaling report tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from timing.model_scaling import (
    REPO_ROOT,
    build_scaling_report,
    run_model_scaling_negative,
)


class TestBuildScalingReport:
    """GREEN path: Qwen2.5-1.5B/3B/7B scaling report must pass all assertions."""

    def test_default_models_pass(self):
        report = build_scaling_report()
        assert report["verdict"] == "pass"
        assert report["report_only"] is True
        assert len(report["models"]) == 3
        assert report["passed"] is True

    def test_weight_bytes_strictly_increasing(self):
        report = build_scaling_report()
        weights = [m["weight_bytes"] for m in report["models"]]
        assert weights[0] < weights[1] < weights[2]

    def test_total_decode_cycles_strictly_increasing(self):
        report = build_scaling_report()
        cycles = [m["total_decode_cycles"] for m in report["models"]]
        assert cycles[0] < cycles[1] < cycles[2]

    def test_memory_bound_per_weight_byte_delta_within_20pct(self):
        report = build_scaling_report()
        ratios = [m["memory_bound_per_weight_byte"] for m in report["models"]]
        for i in range(len(ratios) - 1):
            delta = abs(ratios[i + 1] - ratios[i]) / max(ratios[i], 1e-12) * 100.0
            assert delta <= 20.0, f"adjacent ratio delta {delta}% exceeds 20%"

    def test_all_reports_carry_report_only_flag(self):
        report = build_scaling_report()
        for m in report["models"]:
            assert m["report_only"] is True

    def test_assumptions_present(self):
        report = build_scaling_report()
        for m in report["models"]:
            assert "assumptions" in m
            assert m["assumptions"]["weight_precision"] == "INT4"
            assert m["assumptions"]["weight_bytes_per_param"] == 0.5


class TestNegativeFaults:
    """RED path: swapped params and KPI hard gate must be rejected."""

    def test_swapped_model_params_rejected(self):
        report = build_scaling_report(
            swap_models=("qwen2.5-1.5b", "qwen2.5-7b"),
        )
        assert report["verdict"] == "fail"
        monotonic_fail = any(
            a["id"].endswith("_monotonic") and a["result"] == "fail"
            for a in report["assertions"]
        )
        assert monotonic_fail, "swapped params must break monotonic assertions"

    def test_kpi_target_gate_rejected(self):
        report = build_scaling_report(
            kpi_target_gate={"metric": "decode_tps", "operator": ">=", "value": 100},
        )
        assert report["verdict"] == "fail"
        assert report["kpi_target_gate_rejected"] is True
        kpi_assert = any(
            a["id"] == "kpi_target_gate_rejected" and a["result"] == "fail"
            for a in report["assertions"]
        )
        assert kpi_assert, "KPI hard gate must be explicitly rejected"

    def test_negative_runner_rejects_both_faults(self):
        report = run_model_scaling_negative(
            ["swapped-model-params", "kpi-target-gate"]
        )
        assert report["rejected"] == 2
        assert report["accepted"] == 0
        assert report["verdict"] == "pass"


class TestSignoffRunnerCLI:
    """scripts/run_func_model_perf_signoff.py must expose T19 QA scenarios."""

    RUNNER = REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"

    def test_run_scaling_report_only_exits_zero(self):
        cmd = [
            sys.executable, str(self.RUNNER),
            "run",
            "--cases", "qwen-scaling-1p5b-3b-7b",
            "--report-only",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["verdict"] == "pass"
        assert data["report_only"] is True
        assert len(data["models"]) == 3

    def test_negative_model_scaling_exits_zero(self):
        cmd = [
            sys.executable, str(self.RUNNER),
            "negative",
            "--case", "model-scaling",
            "--faults", "swapped-model-params,kpi-target-gate",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["accepted"] == 0
        assert data["rejected"] == 2
        assert data["all_passed"] is True

    def test_run_scaling_produces_json_evidence(self, tmp_path):
        evidence = tmp_path / "model-scaling.json"
        cmd = [
            sys.executable, str(self.RUNNER),
            "run",
            "--cases", "qwen-scaling-1p5b-3b-7b",
            "--report-only",
            "--evidence-path", str(evidence),
            "--todo-id", "task-19-test",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120)
        assert result.returncode == 0, result.stderr
        assert evidence.is_file()
        payload = json.loads(evidence.read_text())
        assert payload["verdict"] == "pass"
        assert "doneclaim" in payload
        assert payload["doneclaim"]["green_result"]["verdict"] == "pass"
