"""Unit tests for the Func Model signoff evidence runner.

Covers: success case, failure case, expected-RED case, zero-test detection,
skip/xfail rejection, missing-metric rejection, stale-HEAD rejection,
stale-source-fingerprint rejection, stale-command rejection, atomic-write behavior.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Add scripts/ to sys.path so we can import the runner module
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS_DIR = _REPO_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import run_func_model_signoff as _runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_junit_xml(
    collected: int = 5,
    passed: int = 5,
    failed: int = 0,
    skipped: int = 0,
    xfailed: int = 0,
) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="{failed}" failures="0"
             skipped="{skipped}" tests="{collected}" time="0.123"
             xfail="{xfailed}">
    <properties>
      <property name="passed" value="{passed}"/>
    </properties>
  </testsuite>
</testsuites>"""


def _write_junit(path: Path, **kwargs: Any) -> None:
    path.write_text(_make_junit_xml(**kwargs))


def _make_metric_line(case_id: str, key: str, value: Any) -> str:
    return f'SIGNOFF_METRIC {json.dumps({"case": case_id, "key": key, "value": value})}'


def _make_run_argv(case_id: str) -> List[str]:
    return [
        "python3", str(_SCRIPTS_DIR / "run_func_model_signoff.py"),
        "run", "--case", case_id,
    ]


# ---------------------------------------------------------------------------
# Direct function tests
# ---------------------------------------------------------------------------

class TestParseMetrics:
    def test_empty_stdout(self) -> None:
        assert _runner.parse_metrics_from_stdout("") == []

    def test_valid_metrics(self) -> None:
        stdout = _make_metric_line("test-case", "tests.passed", 10) + "\n"
        metrics = _runner.parse_metrics_from_stdout(stdout)
        assert len(metrics) == 1
        assert metrics[0]["case"] == "test-case"
        assert metrics[0]["key"] == "tests.passed"
        assert metrics[0]["value"] == 10

    def test_malformed_json_skipped(self) -> None:
        stdout = 'SIGNOFF_METRIC {not valid json}\n'
        stdout += _make_metric_line("test-case", "tests.passed", 5) + "\n"
        metrics = _runner.parse_metrics_from_stdout(stdout)
        assert len(metrics) == 1
        assert metrics[0]["value"] == 5

    def test_mixed_output(self) -> None:
        stdout = "some log line\n"
        stdout += _make_metric_line("a", "k1", 1) + "\n"
        stdout += "more log\n"
        stdout += _make_metric_line("a", "k2", "v2") + "\n"
        metrics = _runner.parse_metrics_from_stdout(stdout)
        assert len(metrics) == 2


class TestParseJunitXml:
    def test_basic_xml(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "junit.xml"
        _write_junit(xml_path, collected=10, passed=8, failed=1, skipped=1)
        result = _runner.parse_junit_xml(xml_path)
        assert result is not None
        assert result.collected == 10
        assert result.passed == 8
        assert result.failed == 1
        assert result.skipped == 1

    def test_all_pass(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "junit.xml"
        _write_junit(xml_path, collected=5, passed=5)
        result = _runner.parse_junit_xml(xml_path)
        assert result is not None
        assert result.failed == 0
        assert result.skipped == 0
        assert result.xfailed == 0
        assert not result.zero_tests

    def test_zero_collected(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "junit.xml"
        _write_junit(xml_path, collected=0, passed=0)
        result = _runner.parse_junit_xml(xml_path)
        assert result is not None
        assert result.zero_tests

    def test_with_skips(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "junit.xml"
        _write_junit(xml_path, collected=10, passed=8, skipped=2)
        result = _runner.parse_junit_xml(xml_path)
        assert result is not None
        assert result.any_skip

    def test_with_xfails(self, tmp_path: Path) -> None:
        xml_path = tmp_path / "junit.xml"
        _write_junit(xml_path, collected=10, passed=8, xfailed=1)
        result = _runner.parse_junit_xml(xml_path)
        assert result is not None
        assert result.any_xfail
        assert result.xfailed == 1

    def test_missing_file(self) -> None:
        assert _runner.parse_junit_xml(Path("/nonexistent/junit.xml")) is None


class TestCommandHash:
    def test_deterministic(self) -> None:
        h1 = _runner.command_hash(["a", "b"])
        h2 = _runner.command_hash(["a", "b"])
        assert h1 == h2

    def test_order_dependent(self) -> None:
        h1 = _runner.command_hash(["a", "b"])
        h2 = _runner.command_hash(["a", "b"])
        assert h1 == h2
        # Different order = different command = different hash
        h3 = _runner.command_hash(["b", "a"])
        assert h1 != h3


class TestBuildEnv:
    def test_pythonpath_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
        env = _runner.build_env()
        assert "PYTHONPATH" in env
        assert str(_runner.SIM_DIR) in env["PYTHONPATH"]

    def test_qwen3b_gguf_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QWEN3B_GGUF", raising=False)
        env = _runner.build_env()
        assert "QWEN3B_GGUF" in env

    def test_qwen3b_gguf_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QWEN3B_GGUF", "/custom/path/model.gguf")
        env = _runner.build_env()
        assert env["QWEN3B_GGUF"] == "/custom/path/model.gguf"


class TestDetermineVerdict:
    @staticmethod
    def _case(**kwargs: Any) -> _runner.CaseDef:
        defaults: Dict[str, Any] = {
            "case_id": "test",
            "argv": ["echo", "hello"],
            "evidence_path": "test.txt",
            "expected_exit": 0,
        }
        defaults.update(kwargs)
        return _runner.CaseDef(**defaults)

    @staticmethod
    def _result(**kwargs: Any) -> _runner.PytestResult:
        defaults: Dict[str, Any] = {
            "collected": 5,
            "passed": 5,
            "failed": 0,
            "skipped": 0,
            "xfailed": 0,
        }
        defaults.update(kwargs)
        return _runner.PytestResult(**defaults)

    def test_success(self) -> None:
        case = self._case()
        result = self._result()
        verdict = _runner._determine_verdict(case, 0, result, [], "")
        assert verdict == "pass"

    def test_wrong_exit_code(self) -> None:
        case = self._case(expected_exit=0)
        result = self._result()
        verdict = _runner._determine_verdict(case, 1, result, [], "")
        assert verdict == "fail"

    def test_zero_tests(self) -> None:
        case = self._case()
        result = self._result(collected=0, passed=0)
        verdict = _runner._determine_verdict(case, 0, result, [], "")
        assert verdict == "fail"

    def test_skip_forbidden(self) -> None:
        case = self._case(forbid_skip=True)
        result = self._result(skipped=1, passed=4)
        verdict = _runner._determine_verdict(case, 0, result, [], "")
        assert verdict == "fail"

    def test_xfail_forbidden(self) -> None:
        case = self._case(forbid_xfail=True)
        result = self._result(xfailed=1, passed=4)
        verdict = _runner._determine_verdict(case, 0, result, [], "")
        assert verdict == "fail"

    def test_below_min_collected(self) -> None:
        case = self._case(min_collected=10)
        result = self._result(collected=5)
        verdict = _runner._determine_verdict(case, 0, result, [], "")
        assert verdict == "fail"

    def test_below_min_passed(self) -> None:
        case = self._case(min_passed=10)
        result = self._result(passed=8)
        verdict = _runner._determine_verdict(case, 0, result, [], "")
        assert verdict == "fail"

    def test_missing_metric(self) -> None:
        case = self._case(required_metrics=["tests.passed"])
        result = self._result()
        metrics: List[Dict[str, Any]] = []  # empty — missing required metric
        verdict = _runner._determine_verdict(case, 0, result, metrics, "")
        assert verdict == "fail"

    def test_expected_failure_pass(self) -> None:
        case = self._case(expected_failure=True, expected_failure_pattern="mixed.*abs.*rel")
        verdict = _runner._determine_verdict(case, 1, None, [], "mixed abs rel assertion failed")
        assert verdict == "pass"

    def test_expected_failure_but_passed(self) -> None:
        case = self._case(expected_failure=True, expected_failure_pattern="mixed.*abs.*rel")
        verdict = _runner._determine_verdict(case, 0, None, [], "all tests passed")
        assert verdict == "fail"

    def test_expected_failure_wrong_pattern(self) -> None:
        case = self._case(expected_failure=True, expected_failure_pattern="mixed.*abs.*rel")
        verdict = _runner._determine_verdict(case, 1, None, [], "some other error")
        assert verdict == "fail"


class TestAtomicWrite:
    def test_atomic_write_no_partial(self, tmp_path: Path) -> None:
        """Verify atomic_write produces a complete file, not a partial one."""
        target = tmp_path / "evidence.txt"
        content = "line1\nline2\nline3\n"
        _runner._atomic_write(target, content)
        assert target.is_file()
        assert target.read_text() == content

    def test_atomic_write_overwrites(self, tmp_path: Path) -> None:
        target = tmp_path / "evidence.txt"
        target.write_text("old content")
        _runner._atomic_write(target, "new content\n")
        assert target.read_text() == "new content\n"


# ---------------------------------------------------------------------------
# Runner subprocess integration tests
# ---------------------------------------------------------------------------

class TestRunnerSelfTest:
    """Test that the runner can run its own case (task-0a-signoff-runner).

    Uses _FM_SIGNOFF_RECURSE_GUARD to prevent infinite recursion when the
    spawned pytest session imports the runner module again.
    """

    def test_runner_cli_help(self) -> None:
        """Verify runner CLI is functional (--help exits 0)."""
        result = subprocess.run(
            ["python3", str(_SCRIPTS_DIR / "run_func_model_signoff.py"), "--help"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0

    def test_runner_rejects_nonexistent_case(self) -> None:
        """Runner must exit non-zero for unknown case IDs."""
        result = subprocess.run(
            _make_run_argv("nonexistent-case"),
            cwd=str(_REPO_ROOT),
            env=_runner.build_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode != 0

    def test_runner_lists_known_cases(self) -> None:
        """Runner must report known case IDs on error."""
        result = subprocess.run(
            _make_run_argv("nonexistent-case"),
            cwd=str(_REPO_ROOT),
            env=_runner.build_env(),
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert "task-0a-signoff-runner" in result.stderr


# ---------------------------------------------------------------------------
# Validation tests (using crafted evidence)
# ---------------------------------------------------------------------------

class TestValidation:
    """Test the validate mode of the runner."""

    @staticmethod
    def _write_evidence(
        evidence_path: Path,
        case_id: str,
        head: str,
        fingerprint: str,
        command_hash: str,
        verdict: str = "pass",
        metrics: List[Dict[str, Any]] | None = None,
    ) -> None:
        lines = [
            f"case_id: {case_id}",
            "utc_start: 2026-01-01T00:00:00+00:00",
            "utc_end: 2026-01-01T00:00:01+00:00",
            "elapsed_s: 1.000",
            "branch: main",
            f"head: {head}",
            "dirty_worktree: clean",
            f"argv: {json.dumps(['echo','test'])}",
            f"command_hash: {command_hash}",
            f"exit_code: 0",
            f"source_fingerprint: {fingerprint}",
            "source_files (0):",
            f"verdict: {verdict}",
        ]
        if metrics:
            for m in metrics:
                lines.append(f"SIGNOFF_METRIC {json.dumps(m, sort_keys=True)}")
        lines.append("--- END ---")
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text("\n".join(lines))

    def test_validate_missing_evidence(self) -> None:
        case = _runner.CaseDef(
            case_id="nonexistent",
            argv=["echo", "hi"],
            evidence_path="nonexistent-evidence.txt",
            expected_exit=0,
            required_metrics=[],
        )
        assert _runner.validate_case(case) is False

    def test_missing_metric_rejection(self, tmp_path: Path) -> None:
        case = _runner.CaseDef(
            case_id="missing-metric",
            argv=["echo", "hi"],
            evidence_path="missing-metric.txt",
            expected_exit=0,
            required_metrics=["nonexistent.key"],
        )
        evidence = tmp_path / "missing-metric.txt"
        self._write_evidence(
            evidence, case.case_id,
            head=_runner.git_head(),
            fingerprint=_runner.compute_source_fingerprint(case.source_fingerprint_globs)[0],
            command_hash=_runner.command_hash(case.argv),
            verdict="pass",
        )
        # Monkeypatch EVIDENCE_DIR to use tmp_path
        import run_func_model_signoff as runner_mod
        original_dir = runner_mod.EVIDENCE_DIR
        try:
            runner_mod.EVIDENCE_DIR = tmp_path
            assert runner_mod.validate_case(case) is False
        finally:
            runner_mod.EVIDENCE_DIR = original_dir

    def test_stale_head_rejection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        case = _runner.CaseDef(
            case_id="stale-head",
            argv=["echo", "hi"],
            evidence_path="stale-head.txt",
            expected_exit=0,
            required_metrics=[],
        )
        evidence = tmp_path / "stale-head.txt"
        self._write_evidence(
            evidence, case.case_id,
            head="0000000000000000000000000000000000000000",
            fingerprint=_runner.compute_source_fingerprint(case.source_fingerprint_globs)[0],
            command_hash=_runner.command_hash(case.argv),
            verdict="pass",
        )
        import run_func_model_signoff as runner_mod
        original_dir = runner_mod.EVIDENCE_DIR
        try:
            runner_mod.EVIDENCE_DIR = tmp_path
            # HEAD recorded is fake, current is real → stale
            assert runner_mod.validate_case(case) is False
        finally:
            runner_mod.EVIDENCE_DIR = original_dir

    def test_stale_command_rejection(self, tmp_path: Path) -> None:
        case = _runner.CaseDef(
            case_id="stale-cmd",
            argv=["echo", "hi"],
            evidence_path="stale-cmd.txt",
            expected_exit=0,
            required_metrics=[],
        )
        evidence = tmp_path / "stale-cmd.txt"
        self._write_evidence(
            evidence, case.case_id,
            head=_runner.git_head(),
            fingerprint=_runner.compute_source_fingerprint(case.source_fingerprint_globs)[0],
            command_hash="0000000000000000",
            verdict="pass",
        )
        import run_func_model_signoff as runner_mod
        original_dir = runner_mod.EVIDENCE_DIR
        try:
            runner_mod.EVIDENCE_DIR = tmp_path
            assert runner_mod.validate_case(case) is False
        finally:
            runner_mod.EVIDENCE_DIR = original_dir

    def test_stale_fingerprint_rejection(self, tmp_path: Path) -> None:
        case = _runner.CaseDef(
            case_id="stale-fp",
            argv=["echo", "hi"],
            evidence_path="stale-fp.txt",
            expected_exit=0,
            required_metrics=[],
        )
        evidence = tmp_path / "stale-fp.txt"
        self._write_evidence(
            evidence, case.case_id,
            head=_runner.git_head(),
            fingerprint="0000000000000000000000000000000000000000000000000000000000000000",
            command_hash=_runner.command_hash(case.argv),
            verdict="pass",
        )
        import run_func_model_signoff as runner_mod
        original_dir = runner_mod.EVIDENCE_DIR
        try:
            runner_mod.EVIDENCE_DIR = tmp_path
            assert runner_mod.validate_case(case) is False
        finally:
            runner_mod.EVIDENCE_DIR = original_dir

    def test_validate_pass(self, tmp_path: Path) -> None:
        case = _runner.CaseDef(
            case_id="validate-pass",
            argv=["echo", "hi"],
            evidence_path="validate-pass.txt",
            expected_exit=0,
            required_metrics=[],
        )
        evidence = tmp_path / "validate-pass.txt"
        self._write_evidence(
            evidence, case.case_id,
            head=_runner.git_head(),
            fingerprint=_runner.compute_source_fingerprint(case.source_fingerprint_globs)[0],
            command_hash=_runner.command_hash(case.argv),
            verdict="pass",
        )
        import run_func_model_signoff as runner_mod
        original_dir = runner_mod.EVIDENCE_DIR
        try:
            runner_mod.EVIDENCE_DIR = tmp_path
            assert runner_mod.validate_case(case) is True
        finally:
            runner_mod.EVIDENCE_DIR = original_dir

    def test_malformed_metric_json(self, tmp_path: Path) -> None:
        case = _runner.CaseDef(
            case_id="malformed-metric",
            argv=["echo", "hi"],
            evidence_path="malformed-metric.txt",
            expected_exit=0,
            required_metrics=["tests.passed"],
        )
        evidence = tmp_path / "malformed-metric.txt"
        self._write_evidence(
            evidence, case.case_id,
            head=_runner.git_head(),
            fingerprint=_runner.compute_source_fingerprint(case.source_fingerprint_globs)[0],
            command_hash=_runner.command_hash(case.argv),
            verdict="pass",
            metrics=[
                {"case": case.case_id, "key": "SIGNOFF_METRIC", "value": "not-json"},
            ],
        )
        # Append a malformed line
        with open(evidence, "a") as f:
            f.write("SIGNOFF_METRIC {broken\n")
        import run_func_model_signoff as runner_mod
        original_dir = runner_mod.EVIDENCE_DIR
        try:
            runner_mod.EVIDENCE_DIR = tmp_path
            # Malformed JSON is skipped, but tests.passed metric is missing
            result = runner_mod.validate_case(case)
            assert result is False
        finally:
            runner_mod.EVIDENCE_DIR = original_dir

    def test_duplicate_metric_key_conflict(self, tmp_path: Path) -> None:
        case = _runner.CaseDef(
            case_id="dup-metric",
            argv=["echo", "hi"],
            evidence_path="dup-metric.txt",
            expected_exit=0,
            required_metrics=["tests.passed"],
        )
        evidence = tmp_path / "dup-metric.txt"
        self._write_evidence(
            evidence, case.case_id,
            head=_runner.git_head(),
            fingerprint=_runner.compute_source_fingerprint(case.source_fingerprint_globs)[0],
            command_hash=_runner.command_hash(case.argv),
            verdict="pass",
            metrics=[
                {"case": case.case_id, "key": "tests.passed", "value": 10},
                {"case": case.case_id, "key": "tests.passed", "value": 20},
            ],
        )
        import run_func_model_signoff as runner_mod
        original_dir = runner_mod.EVIDENCE_DIR
        try:
            runner_mod.EVIDENCE_DIR = tmp_path
            assert runner_mod.validate_case(case) is False
        finally:
            runner_mod.EVIDENCE_DIR = original_dir


# ---------------------------------------------------------------------------
# Case registry integrity tests
# ---------------------------------------------------------------------------

class TestCaseRegistry:
    def test_all_cases_have_ids(self) -> None:
        for cid, case in _runner.CASE_REGISTRY.items():
            assert case.case_id == cid, f"Case {cid} has mismatched case_id"

    def test_task_0a_registered(self) -> None:
        assert "task-0a-signoff-runner" in _runner.CASE_REGISTRY
        case = _runner.CASE_REGISTRY["task-0a-signoff-runner"]
        assert case.expected_exit == 0
        assert case.is_pytest is True

    def test_task_1_comparator_red_registered(self) -> None:
        assert "task-1-comparator-red" in _runner.CASE_REGISTRY
        case = _runner.CASE_REGISTRY["task-1-comparator-red"]
        assert case.expected_failure is True

    def test_expected_failure_cases_have_pattern(self) -> None:
        for cid, case in _runner.CASE_REGISTRY.items():
            if case.expected_failure:
                assert case.expected_failure_pattern, (
                    f"Case {cid} has expected_failure=True but no pattern"
                )

    def test_all_final_cases_registered(self) -> None:
        final_cases = [
            "final-plan-compliance",
            "final-code-quality",
            "final-real-qa",
            "final-scope-fidelity",
        ]
        for cid in final_cases:
            assert cid in _runner.CASE_REGISTRY, f"Missing final case: {cid}"

    def test_evidence_paths_unique(self) -> None:
        paths = [c.evidence_path for c in _runner.CASE_REGISTRY.values()]
        assert len(paths) == len(set(paths)), "Evidence paths must be unique"


# ---------------------------------------------------------------------------
# PytestResult tests
# ---------------------------------------------------------------------------

class TestPytestResult:
    def test_zero_tests(self) -> None:
        r = _runner.PytestResult(collected=0)
        assert r.zero_tests

    def test_not_zero_tests(self) -> None:
        r = _runner.PytestResult(collected=5)
        assert not r.zero_tests

    def test_any_skip(self) -> None:
        r = _runner.PytestResult(skipped=1, collected=5)
        assert r.any_skip

    def test_no_skip(self) -> None:
        r = _runner.PytestResult(skipped=0)
        assert not r.any_skip

    def test_any_xfail(self) -> None:
        r = _runner.PytestResult(xfailed=1)
        assert r.any_xfail

    def test_no_xfail(self) -> None:
        r = _runner.PytestResult(xfailed=0)
        assert not r.any_xfail
