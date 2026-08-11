"""T16/T17: Qwen dual-path spec-gate tests.

Path A reduces the canonical workload via ``timing.qwen_spec_gates``.
Path B is invoked as a subprocess via ``scripts/reduce_func_model_perf_oracle.py``
with a restricted PYTHONPATH so it cannot import Path A modules.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from timing.qwen_spec_gates import (
    REPO_ROOT,
    compare_path_results,
    compute_path_a_result,
    evaluate_qwen_workload,
)

_WORKLOAD_IDS = [
    "qwen25-3b-blk0-decode",
    "qwen25-3b-decode-c128-g1",
    "qwen25-3b-prefill-16",
    "qwen25-3b-prefill-128",
]


class TestPathAComputation:
    """Path A must produce positive structural results for every hard-gate workload."""

    @pytest.mark.parametrize("workload_id", _WORKLOAD_IDS)
    def test_path_a_positive_total_cycles(self, workload_id: str):
        result = compute_path_a_result(workload_id)
        assert result["total_cycles"] > 0
        assert result["units"] == "cycles"
        assert result["op_count"] in (17, 612)
        assert result["workload_hash"]

    def test_path_a_critical_path_not_greater_than_sum(self):
        result = compute_path_a_result("qwen25-3b-blk0-decode")
        assert result["critical_path_cycles"] <= sum(result["breakdown"].values())


class TestPathBSubprocess:
    """Path B reducer must run in isolation and emit valid JSON."""

    @pytest.mark.parametrize("workload_id", _WORKLOAD_IDS)
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


class TestDualPathComparison:
    """Path A and Path B must agree within the T16 <=20% gate for all workloads."""

    @pytest.mark.parametrize("workload_id", _WORKLOAD_IDS)
    def test_all_workloads_pass_spec_gate(self, workload_id: str):
        comparison = evaluate_qwen_workload(workload_id)
        assert comparison["passed"], comparison["assertions"]
        assert comparison["total_error_pct"] <= 20.0

    def test_compare_rejects_hash_mismatch(self):
        path_a = compute_path_a_result("qwen25-3b-blk0-decode")
        path_b = compute_path_a_result("qwen25-3b-decode-c128-g1")
        result = compare_path_results(path_a, path_b)
        assert not result["passed"]
        assert any(a["id"] == "structural_workload_hash" and a["result"] == "fail" for a in result["assertions"])


class TestSignoffRunnerCLI:
    """scripts/run_func_model_perf_signoff.py must expose the T16 QA scenarios."""

    def test_run_compare_paths_ab_passed_four(self):
        cmd = [
            "python3", str(REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"),
            "run",
            "--cases", "qwen-blk0,qwen-decode-c128-g1,qwen-prefill-16,qwen-prefill-128",
            "--compare-paths", "a,b",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["total"] == 4
        assert data["passed"] == 4
        assert data["failed"] == 0
        assert data["verdict"] == "pass"

    def test_negative_qwen_paths_rejects_all_faults(self):
        cmd = [
            "python3", str(REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"),
            "negative",
            "--case", "qwen-paths",
            "--faults", "missing-attention,path-a-double-count,path-b-decomposition",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=180)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["accepted"] == 0
        assert data["rejected"] == 3
        assert data["all_passed"] is True

    def test_run_compare_paths_produces_json_evidence(self, tmp_path):
        evidence = tmp_path / "qwen-spec-gates.json"
        cmd = [
            "python3", str(REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"),
            "run",
            "--cases", "qwen-blk0",
            "--compare-paths", "a,b",
            "--evidence-path", str(evidence),
            "--todo-id", "task-16-test",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120)
        assert result.returncode == 0, result.stderr
        assert evidence.is_file()
        payload = json.loads(evidence.read_text())
        assert payload["verdict"] == "pass"
        assert "doneclaim" in payload
        assert payload["doneclaim"]["green_result"]["passed"] == 1
