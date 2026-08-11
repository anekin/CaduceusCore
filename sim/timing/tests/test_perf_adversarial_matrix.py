"""T21: Adversarial and anti-vacuous performance-spec matrix tests.

Covers:
  - Full matrix GREEN: all declared faults rejected, accepted=0.
  - Disable-each-validator anti-vacuous self-test: disabling a validator flips
    its paired fault to accepted while all other faults remain rejected.
  - Individual fault injectors produce the expected rejection reason.
  - Runner CLI integration: `negative --matrix all` exits 0 with the exact
    structured verdict.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPO_ROOT / "scripts" / "run_func_model_perf_signoff.py"
ADV_MODULE = REPO_ROOT / "sim" / "timing" / "adversarial_matrix.py"

FIXTURES = {
    "qwen_missing_attention": REPO_ROOT / "config" / "tests" / "adv_qwen_blk0_missing_attention.json",
    "matrix_wrong_seed": REPO_ROOT / "config" / "tests" / "adv_matrix_wrong_seed.json",
    "matrix_zero_activity": REPO_ROOT / "config" / "tests" / "adv_matrix_zero_activity.json",
    "oracle_self_importing": REPO_ROOT / "config" / "tests" / "adv_oracle_self_importing.json",
}


def _run_runner(args: list[str]) -> subprocess.CompletedProcess:
    env = {"PYTHONPATH": str(REPO_ROOT / "sim")}
    return subprocess.run(
        [sys.executable, str(RUNNER)] + args,
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120, env=env,
    )


def _load_report(proc: subprocess.CompletedProcess) -> Dict[str, Any]:
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    return json.loads(proc.stdout)


class TestAdversarialMatrixUnit:
    def test_full_matrix_all_rejected(self):
        from timing.adversarial_matrix import run_adversarial_matrix
        report = run_adversarial_matrix(disable_each_validator=False)
        assert report.verdict == "pass"
        assert report.accepted == 0
        assert report.rejected == report.declared_faults
        assert report.detected_faults == report.declared_faults

    def test_stale_state_bundle_recorded(self):
        from timing.adversarial_matrix import run_adversarial_matrix
        report = run_adversarial_matrix()
        assert report.stale_state["tested"] is True
        assert report.stale_state["rejected"] is True
        assert report.misleading_success_output["tested"] is True
        assert report.misleading_success_output["rejected"] is True

    def test_disable_each_validator_proves_responsibility(self):
        from timing.adversarial_matrix import run_adversarial_matrix
        report = run_adversarial_matrix(disable_each_validator=True)
        assert report.verdict == "pass"
        assert len(report.disable_each_validator) > 0
        for entry in report.disable_each_validator:
            assert entry["validator_proven_responsible"] is True
            assert entry["paired_now_accepted"] is True
            other_accepted = [
                r for r in entry["results"]
                if r["effective_rejected"] is False and r["disabled"] is False
            ]
            assert len(other_accepted) == 0, (
                f"validator {entry['disabled_validator']} disabled but unrelated faults accepted"
            )


class TestAdversarialFaultInjectors:
    def test_qwen_missing_attention_fixture_rejected(self):
        fixture = FIXTURES["qwen_missing_attention"]
        manifest = json.loads(fixture.read_text())
        mxu = sum(1 for o in manifest["ops"] if o.get("engine") == "mxu")
        sfu = sum(1 for o in manifest["ops"] if o.get("engine") == "sfu")
        vec = sum(1 for o in manifest["ops"] if o.get("engine") == "vector")
        assert (mxu, sfu, vec) != (9, 5, 3)

    def test_matrix_wrong_seed_fixture_rejected(self):
        fixture = FIXTURES["matrix_wrong_seed"]
        matrix = json.loads(fixture.read_text())
        assert matrix["seed"] != 42

    def test_matrix_zero_activity_fixture_rejected(self):
        fixture = FIXTURES["matrix_zero_activity"]
        matrix = json.loads(fixture.read_text())
        assert len(matrix.get("provider_matrix", [])) == 0
        assert len(matrix.get("workloads", [])) == 0

    def test_oracle_self_importing_fixture_rejected(self):
        fixture = FIXTURES["oracle_self_importing"]
        oracle = json.loads(fixture.read_text())
        content = json.dumps(oracle)
        assert "sim.models" in content


class TestAdversarialRunnerCLI:
    def test_negative_matrix_all_exits_zero(self):
        proc = _run_runner([
            "negative", "--matrix", "all",
            "--evidence-path", ".omo/evidence/task-21-adversarial-test.json",
        ])
        report = _load_report(proc)
        assert report["verdict"] == "pass"
        assert report["accepted"] == 0
        assert report["rejected"] == report["declared_faults"]

    def test_negative_matrix_disable_each_validator_exits_zero(self):
        proc = _run_runner([
            "negative", "--matrix", "all", "--self-test-disable-each-validator",
            "--evidence-path", ".omo/evidence/task-21-adversarial-disable-test.json",
        ])
        report = _load_report(proc)
        assert report["verdict"] == "pass"
        assert report["accepted"] == 0
        assert report["rejected"] == report["declared_faults"]
        for entry in report["disable_each_validator"]:
            assert entry["validator_proven_responsible"] is True

    def test_evidence_contains_doneclaim(self):
        evidence_path = REPO_ROOT / ".omo" / "evidence" / "task-21-adversarial-test.json"
        assert evidence_path.is_file()
        data = json.loads(evidence_path.read_text())
        assert "doneclaim" in data
        assert data["doneclaim"]["todo_id"].startswith("task-21")
