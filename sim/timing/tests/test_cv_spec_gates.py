"""T17: CV workload dual-path spec-gate tests."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from timing.cv_spec_gates import (
    REPO_ROOT,
    _CV_WORKLOAD_IDS,
    compute_path_a_result,
    evaluate_cv_workload,
    inject_dropped_depthwise_fault,
    inject_im2col_bytes_x8_fault,
    inject_path_b_decomposition_fault,
    inject_unknown_op_fault,
)


class TestPathAComputation:
    """Path A must produce positive structural results for every CV workload."""

    @pytest.mark.parametrize("workload_id", sorted(_CV_WORKLOAD_IDS))
    def test_path_a_positive_total_cycles(self, workload_id: str):
        result = compute_path_a_result(workload_id)
        assert result["total_cycles"] > 0
        assert result["units"] == "cycles"
        assert result["op_count"] > 0
        assert result["workload_hash"]
        assert result["engine_counts"]["mxu"] > 0
        assert result["engine_counts"]["sfu"] > 0

    @pytest.mark.parametrize("workload_id", sorted(_CV_WORKLOAD_IDS))
    def test_path_a_critical_path_not_greater_than_sum(self, workload_id: str):
        result = compute_path_a_result(workload_id)
        assert result["critical_path_cycles"] <= sum(result["breakdown"].values())


class TestPathBSubprocess:
    """Path B reducer must run in isolation and emit valid JSON for CV workloads."""

    @pytest.mark.parametrize("workload_id", sorted(_CV_WORKLOAD_IDS))
    def test_path_b_subprocess_output(self, workload_id: str):
        env = os.environ.copy()
        env["PYTHONPATH"] = ""
        cmd = [
            "python3",
            str(REPO_ROOT / "scripts" / "reduce_func_model_perf_oracle.py"),
            "--oracle",
            str(REPO_ROOT / "config" / "func_model_workload_oracle_v1.json"),
            "--workload-id",
            workload_id,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["tool"] == "reduce_func_model_perf_oracle"
        assert data["path"] == "Path B (independent)"
        assert data["verdict"] == "pass"
        assert data["workload_hash"]
        assert data["engine_counts"]["mxu"] > 0


class TestDualPathComparison:
    """Path A and Path B must agree within the T17 <=20% gate for all CV workloads."""

    @pytest.mark.parametrize("workload_id", sorted(_CV_WORKLOAD_IDS))
    def test_all_workloads_pass_spec_gate(self, workload_id: str):
        comparison = evaluate_cv_workload(workload_id)
        assert comparison["passed"], comparison["assertions"]
        assert comparison["total_error_pct"] <= 20.0

    def test_compare_rejects_hash_mismatch(self):
        path_a = compute_path_a_result("mobilenetv3")
        path_b = compute_path_a_result("resnet50")
        from timing.cv_spec_gates import compare_path_results

        result = compare_path_results(path_a, path_b)
        assert not result["passed"]
        assert any(
            a["id"] == "structural_workload_hash" and a["result"] == "fail"
            for a in result["assertions"]
        )


class TestFaultInjectors:
    """Each T17 negative fault must be rejected."""

    def test_im2col_bytes_x8_rejected(self):
        result = inject_im2col_bytes_x8_fault()
        assert result["rejected"] is True
        assert result["accepted"] is False

    def test_dropped_depthwise_rejected(self):
        result = inject_dropped_depthwise_fault()
        assert result["rejected"] is True
        assert result["accepted"] is False

    def test_unknown_op_rejected(self):
        result = inject_unknown_op_fault()
        assert result["rejected"] is True
        assert result["accepted"] is False

    def test_path_b_decomposition_rejected(self):
        result = inject_path_b_decomposition_fault()
        assert result["rejected"] is True
        assert result["accepted"] is False


class TestSignoffRunnerCLI:
    """scripts/run_func_model_perf_signoff.py must expose the T17 QA scenarios."""

    def test_run_compare_paths_ab_passed_three(self):
        cmd = [
            "python3",
            str(REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"),
            "run",
            "--cases",
            "mobilenetv3,resnet50,yolov8n",
            "--compare-paths",
            "a,b",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["total"] == 3
        assert data["passed"] == 3
        assert data["failed"] == 0
        assert data["verdict"] == "pass"

    def test_negative_cv_paths_rejects_all_faults(self):
        cmd = [
            "python3",
            str(REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"),
            "negative",
            "--case",
            "cv-paths",
            "--faults",
            "im2col-bytes-x8,dropped-depthwise,unknown-op,path-b-decomposition",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180
        )
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["accepted"] == 0
        assert data["rejected"] == 4
        assert data["all_passed"] is True

    def test_run_compare_paths_produces_json_evidence(self, tmp_path):
        evidence = tmp_path / "cv-spec-gates.json"
        cmd = [
            "python3",
            str(REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"),
            "run",
            "--cases",
            "mobilenetv3",
            "--compare-paths",
            "a,b",
            "--evidence-path",
            str(evidence),
            "--todo-id",
            "task-17-test",
        ]
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120
        )
        assert result.returncode == 0, result.stderr
        assert evidence.is_file()
        payload = json.loads(evidence.read_text())
        assert payload["verdict"] == "pass"
        assert "doneclaim" in payload
        assert payload["doneclaim"]["green_result"]["passed"] == 1
