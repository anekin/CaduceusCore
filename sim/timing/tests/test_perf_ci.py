import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"
EVIDENCE_DIR = REPO_ROOT / ".omo" / "evidence"


def _run(argv):
    cmd = [sys.executable, str(RUNNER)] + argv
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**dict(__import__("os").environ), "PYTHONPATH": "sim"},
    )
    return proc


@pytest.fixture(scope="module")
def ci_run_evidence():
    proc = _run([
        "run", "--all-spec", "--ci-mode",
        "--todo-id", "task-23",
        "--evidence-path", "task-23-perf-spec-ci.txt",
    ])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "pass"
    assert payload["peak_rss_mb"] <= 4096
    assert payload["elapsed_s"] <= 1800
    for stage in payload["stages"]:
        assert stage["verdict"] == "pass"
        assert stage["within_limit"] is True
    return EVIDENCE_DIR / "task-23-perf-spec-ci.txt"


def test_run_all_spec_ci_mode():
    proc = _run(["run", "--all-spec", "--ci-mode", "--todo-id", "task-23"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "pass"
    assert payload["run_id"]
    assert payload["canonical_hash"]
    assert payload["peak_rss_mb"] <= 4096
    assert payload["elapsed_s"] <= 1800


def test_validate_done_claims_1_to_22(ci_run_evidence):
    proc = _run([
        "validate",
        "--require-fresh",
        "--require-done-claims", "1-22",
    ])
    assert proc.returncode == 0, proc.stderr


def test_negative_ci_faults():
    proc = _run([
        "negative",
        "--case", "ci",
        "--faults", "vcs-command,rtl-path,previous-head,timeout,rss-limit",
    ])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "pass"
    assert payload["rejected"] == 5
    assert payload["accepted"] == 0
    assert payload["all_passed"] is True
    for fault in ["vcs-command", "rtl-path", "previous-head", "timeout", "rss-limit"]:
        assert payload["results"][fault]["rejected"] is True


def test_audit_recompute_and_checks(ci_run_evidence):
    proc = _run([
        "audit",
        "--run-id-from", str(ci_run_evidence),
        "--checks", "no-rtl,no-vcs-in-ci,scope,provenance,report-only",
        "--recompute",
    ])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["recompute"]["match"] is True
    for name, result in payload["results"].items():
        assert result["verdict"] == "pass", f"audit check {name} failed: {result}"


def test_rerun_cases_and_faults():
    proc = _run(["rerun", "--cases", "qwen-blk0,qwen-decode-c128-g1"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["verdict"] == "pass"
    assert payload["passed"] == 2

    proc = _run(["rerun", "--faults", "stale-head,rtl-path"])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["rejected"] == 2
    assert payload["accepted"] == 0


def test_ci_workflow_has_no_vcs_commands():
    ci_file = REPO_ROOT / ".github" / "workflows" / "caduceus-core-ci.yml"
    text = ci_file.read_text()
    assert "vcs " not in text.lower()
    assert "verdi" not in text.lower()
    assert "simv" not in text.lower()
    assert "ncvlog" not in text.lower()
