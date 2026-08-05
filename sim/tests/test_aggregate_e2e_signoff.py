"""Tests for scripts/aggregate_e2e_signoff.py — S2 evidence aggregation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

import pytest

# ---------------------------------------------------------------------------
# Helpers — write evidence files into tmp_path
# ---------------------------------------------------------------------------

QWEN_FULL_PASS: Dict[str, Any] = {
    "passed": True,
    "generated_token_text": "Hello",
    "npu_ops_executed": 1629,
    "device": "fm://python",
    "model": "/models/qwen.gguf",
    "prompt": "Hello",
    "layers": 36,
    "utc": "2026-07-31T09:35:01Z",
}

QWEN_FULL_FAIL_PASSED: Dict[str, Any] = {
    "passed": False,
    "generated_token_text": "Hello",
    "npu_ops_executed": 1629,
}

QWEN_FULL_EMPTY_TOKEN: Dict[str, Any] = {
    "passed": True,
    "generated_token_text": "",
    "npu_ops_executed": 1629,
}

QWEN_FULL_ZERO_OPS: Dict[str, Any] = {
    "passed": True,
    "generated_token_text": "Hello",
    "npu_ops_executed": 0,
}

QWEN_LAYER_PASS: Dict[str, Any] = {
    "passed": True,
    "n_layers": 36,
    "summary": {
        "first_layer": {"cos_sim": 1.0, "max_abs_diff": 0.0, "passed": True},
        "last_layer": {"cos_sim": 1.0, "max_abs_diff": 0.0, "passed": True},
        "total_layers": 36,
        "passed_layers": 36,
        "thresholds": {"cos_sim_min": 0.99, "max_abs_diff_max": 0.001},
    },
    "utc": "2026-07-31T12:39:48Z",
}

QWEN_LAYER_FIRST_FAIL: Dict[str, Any] = {
    "passed": True,
    "n_layers": 36,
    "summary": {
        "first_layer": {"cos_sim": 0.8, "max_abs_diff": 10.0, "passed": False},
        "last_layer": {"cos_sim": 1.0, "max_abs_diff": 0.0, "passed": True},
    },
}

QWEN_LAYER_WRONG_N: Dict[str, Any] = {
    "passed": True,
    "n_layers": 3,
    "summary": {
        "first_layer": {"passed": True},
        "last_layer": {"passed": True},
    },
}

CV_GOLDEN_PASS: Dict[str, Any] = {
    "top5_indices": [92, 21, 549, 574, 127],
    "top5_logits": [6.01, 5.95, 5.44, 5.27, 5.18],
    "seed": 42,
    "input_shape": [1, 3, 224, 224],
    "timestamp": "2026-08-04T04:34:52Z",
}

CV_GOLDEN_BAD_SEED: Dict[str, Any] = {
    "top5_indices": [92, 21, 549, 574, 127],
    "top5_logits": [6.01, 5.95, 5.44, 5.27, 5.18],
    "seed": 99,
}

CV_GOLDEN_SHORT_INDICES: Dict[str, Any] = {
    "top5_indices": [92, 21, 549],
    "top5_logits": [6.01, 5.95, 5.44, 5.27, 5.18],
    "seed": 42,
}

CV_HOST_PASS: Dict[str, Any] = {
    "full_graph_passed": True,
    "error": None,
    "first_conv_passed": False,
    "model": "assets/mobilenetv3_small.onnx",
    "device": "fm://python",
    "timestamp": "2026-08-04T17:24:28",
}

CV_HOST_PASS2: Dict[str, Any] = {
    "full_graph_passed": True,
    "error": None,
    "first_conv_passed": False,
    "model": "assets/mobilenetv3_small.onnx",
    "device": "fm://python",
    "timestamp": "2026-08-04T18:00:00",
}

CV_HOST_NO_FULL_GRAPH: Dict[str, Any] = {
    "full_graph_passed": False,
    "error": "MMUL failed",
    "first_conv_passed": False,
    "model": "assets/mobilenetv3_small.onnx",
    "device": "fm://python",
    "timestamp": "2026-08-04T17:00:00",
}

CV_HOST_ERROR_NOT_NONE: Dict[str, Any] = {
    "full_graph_passed": True,
    "error": "something went wrong",
    "first_conv_passed": False,
}

E2E_SUMMARY_PASS: Dict[str, Any] = {
    "title": "E2E Software Signoff",
    "overall_passed": True,
    "pass_count": 4,
    "fail_count": 0,
    "stages": [
        {"stage": "Qwen-positive-signoff", "passed": True, "exit_code": 0},
        {"stage": "CV-golden-gen", "passed": True, "exit_code": 0},
        {"stage": "CV-host-runner", "passed": True, "exit_code": 0},
        {"stage": "CV-E2E-pytest", "passed": True, "exit_code": 0},
    ],
}

E2E_SUMMARY_FAIL: Dict[str, Any] = {
    "title": "E2E Software Signoff",
    "overall_passed": False,
    "pass_count": 3,
    "fail_count": 1,
    "stages": [],
}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _touch(path: Path, mtime: float) -> None:
    """Create an empty file and set its mtime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("")
    os.utime(str(path), (mtime, mtime))


# Import module under test lazily
AGGREGATOR_SCRIPT = str(
    Path(__file__).resolve().parents[2] / "scripts" / "aggregate_e2e_signoff.py"
)


def _run_aggregator(
    evidence_dir: Path, output: Path, strict: bool = False
) -> subprocess.CompletedProcess[str]:
    """Run the aggregator script as a subprocess."""
    cmd = [
        sys.executable,
        AGGREGATOR_SCRIPT,
        "--evidence-dir", str(evidence_dir),
        "--output", str(output),
    ]
    if strict:
        cmd.append("--strict")
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Tests — discovery (most recent by mtime)
# ---------------------------------------------------------------------------

class TestDiscovery:
    """Given multiple evidence files, When the aggregator runs, Then the
    most recent by mtime is selected."""

    def test_selects_most_recent_qwen_full_forward(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        older = base / "qwen-full-forward-20260731T090000.json"
        newer = base / "qwen-full-forward-20260804T013826.json"

        _write_json(older, QWEN_FULL_PASS)
        _write_json(newer, QWEN_FULL_FAIL_PASSED)  # passed=False
        # Ensure newer is really newer by mtime
        os.utime(str(older), (1000, 1000))
        os.utime(str(newer), (2000, 2000))

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)
        assert result.returncode == 0

        report = json.loads(output.read_text())
        # The newer file has passed=False, so track should fail
        assert report["tracks"]["qwen"]["passed"] is False
        # Evidence should reference the newer file
        names = [f["name"] for f in report["tracks"]["qwen"]["evidence_files"]]
        assert "qwen-full-forward-20260804T013826.json" in names

    def test_selects_most_recent_per_layer_compare(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        older = base / "qwen-per-layer-compare-20260731T120000.json"
        newer = base / "qwen-per-layer-compare-20260731T123948.json"

        _write_json(older, QWEN_LAYER_WRONG_N)  # n_layers=3
        _write_json(newer, QWEN_LAYER_PASS)
        # Also provide a passing full forward so the track can pass
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_PASS)
        os.utime(str(older), (1000, 1000))
        os.utime(str(newer), (2000, 2000))

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)
        assert result.returncode == 0

        report = json.loads(output.read_text())
        # The newer per-layer file passes checks (track needs full_forward too)
        pl_check = report["tracks"]["qwen"]["checks"]["per_layer_compare"]
        assert pl_check["passed"] is True
        # The older file (n_layers=3) should not be selected
        names = [f["name"] for f in report["tracks"]["qwen"]["evidence_files"]]
        assert "qwen-per-layer-compare-20260731T123948.json" in names

    def test_selects_cv_host_runner_with_full_graph(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        with_full = base / "cv-host-runner-20260804T172430.json"
        without_full = base / "cv-host-runner-20260804T180000.json"

        _write_json(with_full, CV_HOST_PASS)  # full_graph_passed=True
        _write_json(without_full, CV_HOST_NO_FULL_GRAPH)  # full_graph_passed=False
        # Make the "without_full" file newer by mtime
        os.utime(str(with_full), (1000, 1000))
        os.utime(str(without_full), (2000, 2000))

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)
        assert result.returncode == 0

        report = json.loads(output.read_text())
        names = [f["name"] for f in report["tracks"]["cv"]["evidence_files"]]
        # Should select the one with full_graph_passed=True, not the newer one without it
        assert "cv-host-runner-20260804T172430.json" in names

    def test_selects_most_recent_full_graph_true(self, tmp_path: Path) -> None:
        """When multiple host-runner files have full_graph_passed=true,
        select the most recent one."""
        base = tmp_path / "evidence"
        older = base / "cv-host-runner-20260804T172430.json"
        newer = base / "cv-host-runner-20260804T180000.json"

        _write_json(older, CV_HOST_PASS)
        _write_json(newer, CV_HOST_PASS2)
        os.utime(str(older), (1000, 1000))
        os.utime(str(newer), (2000, 2000))

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)
        assert result.returncode == 0

        report = json.loads(output.read_text())
        names = [f["name"] for f in report["tracks"]["cv"]["evidence_files"]]
        assert "cv-host-runner-20260804T180000.json" in names


# ---------------------------------------------------------------------------
# Tests — validation checks
# ---------------------------------------------------------------------------

class TestQwenFullForwardValidation:
    """Given a qwen-full-forward evidence file, When validated, Then the
    correct pass/fail verdict is determined."""

    def test_passes_with_all_conditions_met(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_PASS)

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)
        assert result.returncode == 0

        report = json.loads(output.read_text())
        ff_check = report["tracks"]["qwen"]["checks"]["full_forward"]
        assert ff_check["passed"] is True
        assert ff_check["failures"] == []

    def test_fails_when_passed_is_false(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_FAIL_PASSED)

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        ff_check = report["tracks"]["qwen"]["checks"]["full_forward"]
        assert ff_check["passed"] is False
        assert any("passed" in f.lower() for f in ff_check["failures"])

    def test_fails_when_token_text_empty(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_EMPTY_TOKEN)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        ff_check = report["tracks"]["qwen"]["checks"]["full_forward"]
        assert ff_check["passed"] is False
        assert any("token" in f.lower() for f in ff_check["failures"])

    def test_fails_when_npu_ops_is_zero(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_ZERO_OPS)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        ff_check = report["tracks"]["qwen"]["checks"]["full_forward"]
        assert ff_check["passed"] is False
        assert any("npu_ops" in f.lower() for f in ff_check["failures"])


class TestQwenPerLayerValidation:
    """Given a qwen-per-layer-compare evidence file, When validated, Then
    first/last layer pass flags and n_layers are checked."""

    def test_passes_with_all_conditions_met(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-per-layer-compare-test.json", QWEN_LAYER_PASS)

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        pl_check = report["tracks"]["qwen"]["checks"]["per_layer_compare"]
        assert pl_check["passed"] is True

    def test_fails_when_first_layer_not_passed(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-per-layer-compare-test.json", QWEN_LAYER_FIRST_FAIL)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        pl_check = report["tracks"]["qwen"]["checks"]["per_layer_compare"]
        assert pl_check["passed"] is False
        assert any("first_layer" in f.lower() for f in pl_check["failures"])

    def test_fails_when_n_layers_wrong(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-per-layer-compare-test.json", QWEN_LAYER_WRONG_N)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        pl_check = report["tracks"]["qwen"]["checks"]["per_layer_compare"]
        assert pl_check["passed"] is False
        assert any("n_layers" in f.lower() for f in pl_check["failures"])


class TestCVGoldenValidation:
    """Given a cv-golden evidence file, When validated, Then top5 lists and
    seed are checked."""

    def test_passes_with_all_conditions_met(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "cv-golden.json", CV_GOLDEN_PASS)

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        gd_check = report["tracks"]["cv"]["checks"]["golden"]
        assert gd_check["passed"] is True

    def test_fails_when_seed_not_42(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "cv-golden.json", CV_GOLDEN_BAD_SEED)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        gd_check = report["tracks"]["cv"]["checks"]["golden"]
        assert gd_check["passed"] is False
        assert any("seed" in f.lower() for f in gd_check["failures"])

    def test_fails_when_indices_short(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "cv-golden.json", CV_GOLDEN_SHORT_INDICES)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        gd_check = report["tracks"]["cv"]["checks"]["golden"]
        assert gd_check["passed"] is False
        assert any("top5_indices" in f.lower() for f in gd_check["failures"])


class TestCVHostRunnerValidation:
    """Given a cv-host-runner evidence file, When validated, Then
    full_graph_passed and error fields are checked."""

    def test_passes_with_all_conditions_met(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "cv-host-runner-test.json", CV_HOST_PASS)

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        hr_check = report["tracks"]["cv"]["checks"]["host_runner"]
        assert hr_check["passed"] is True

    def test_fails_when_error_not_none(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "cv-host-runner-test.json", CV_HOST_ERROR_NOT_NONE)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        hr_check = report["tracks"]["cv"]["checks"]["host_runner"]
        assert hr_check["passed"] is False
        assert any("error" in f.lower() for f in hr_check["failures"])

    def test_skips_full_graph_false(self, tmp_path: Path) -> None:
        """When no cv-host-runner has full_graph_passed=true,
        the evidence should be marked missing."""
        base = tmp_path / "evidence"
        _write_json(base / "cv-host-runner-test.json", CV_HOST_NO_FULL_GRAPH)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        assert "cv-host-runner-*.json" in str(report["missing_evidence"])


class TestE2ESummaryValidation:
    """Given an e2e-signoff-summary, When validated, Then overall_passed and
    fail_count are checked."""

    def test_passes_with_all_conditions_met(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "e2e-signoff-summary.json", E2E_SUMMARY_PASS)

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        assert report["e2e_summary"]["passed"] is True

    def test_fails_when_overall_passed_false(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "e2e-signoff-summary.json", E2E_SUMMARY_FAIL)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        assert report["e2e_summary"]["passed"] is False
        assert any("overall_passed" in f.lower() for f in report["e2e_summary"]["failures"])


# ---------------------------------------------------------------------------
# Tests — SHA-256 hashing
# ---------------------------------------------------------------------------

class TestSHA256Hashes:
    """Given evidence files, When the aggregator runs, Then SHA-256 hashes
    are computed and recorded for every consumed evidence file."""

    def test_hashes_recorded_for_all_evidence(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_PASS)
        _write_json(base / "qwen-per-layer-compare-test.json", QWEN_LAYER_PASS)
        _write_json(base / "cv-golden.json", CV_GOLDEN_PASS)
        _write_json(base / "cv-host-runner-test.json", CV_HOST_PASS)
        _write_json(base / "e2e-signoff-summary.json", E2E_SUMMARY_PASS)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())

        # Qwen hashes
        qwen_hashes = report["tracks"]["qwen"]["hashes"]
        assert len(qwen_hashes) == 2
        assert all(isinstance(v, str) and len(v) == 64 for v in qwen_hashes.values())

        # CV hashes
        cv_hashes = report["tracks"]["cv"]["hashes"]
        assert len(cv_hashes) == 2
        assert all(isinstance(v, str) and len(v) == 64 for v in cv_hashes.values())

        # E2E hashes
        e2e_hashes = report["e2e_summary"]["hashes"]
        assert len(e2e_hashes) == 1
        assert all(isinstance(v, str) and len(v) == 64 for v in e2e_hashes.values())

    def test_hash_changes_with_content(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        f1 = base / "cv-golden.json"
        _write_json(f1, CV_GOLDEN_PASS)

        output1 = tmp_path / "report1.json"
        _run_aggregator(base, output1, strict=False)
        report1 = json.loads(output1.read_text())
        hash1 = report1["tracks"]["cv"]["hashes"]["cv-golden.json"]

        # Change content
        _write_json(f1, CV_GOLDEN_BAD_SEED)

        output2 = tmp_path / "report2.json"
        _run_aggregator(base, output2, strict=False)
        report2 = json.loads(output2.read_text())
        hash2 = report2["tracks"]["cv"]["hashes"]["cv-golden.json"]

        assert hash1 != hash2


# ---------------------------------------------------------------------------
# Tests — mtime cross-check
# ---------------------------------------------------------------------------

class TestMtimeCrossCheck:
    """Given cv-golden and cv-host-runner evidence, When the host-runner
    is not newer than golden, Then a warning is emitted."""

    def test_warns_when_host_runner_not_newer_than_golden(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        golden = base / "cv-golden.json"
        host = base / "cv-host-runner-test.json"

        _write_json(golden, CV_GOLDEN_PASS)
        _write_json(host, CV_HOST_PASS)
        # Make host-runner older than golden
        os.utime(str(golden), (2000, 2000))
        os.utime(str(host), (1000, 1000))

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        warnings = report.get("warnings", [])
        assert any("not newer" in w.lower() for w in warnings)

    def test_no_warning_when_host_runner_is_newer(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        golden = base / "cv-golden.json"
        host = base / "cv-host-runner-test.json"

        _write_json(golden, CV_GOLDEN_PASS)
        _write_json(host, CV_HOST_PASS)
        # Make host-runner newer than golden
        os.utime(str(golden), (1000, 1000))
        os.utime(str(host), (2000, 2000))

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        warnings = report.get("warnings", [])
        assert not any("not newer" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# Tests — missing evidence
# ---------------------------------------------------------------------------

class TestMissingEvidence:
    """Given an evidence directory with missing files, When the aggregator
    runs, Then missing items are listed and overall_passed is false."""

    def test_all_missing(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        base.mkdir(parents=True, exist_ok=True)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        assert report["overall_passed"] is False
        missing = report["missing_evidence"]
        assert len(missing) >= 5  # all five evidence types

    def test_partially_missing(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        # Only provide qwen full forward and cv golden
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_PASS)
        _write_json(base / "cv-golden.json", CV_GOLDEN_PASS)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        assert report["overall_passed"] is False
        missing_names = " ".join(report["missing_evidence"])
        assert "per-layer-compare" in missing_names
        assert "host-runner" in missing_names
        assert "e2e-signoff-summary" in missing_names


# ---------------------------------------------------------------------------
# Tests — mtime_utc in report
# ---------------------------------------------------------------------------

class TestMtimeUTC:
    """Given evidence files, When the aggregator runs, Then each consumed
    file has its mtime_utc recorded."""

    def test_mtime_utc_recorded(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "cv-golden.json", CV_GOLDEN_PASS)
        _write_json(base / "e2e-signoff-summary.json", E2E_SUMMARY_PASS)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        for f_info in report["tracks"]["cv"]["evidence_files"]:
            assert "mtime_utc" in f_info
            assert f_info["mtime_utc"].endswith("+00:00")

    def test_mtime_utc_matches_file_stat(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        f = base / "cv-golden.json"
        _write_json(f, CV_GOLDEN_PASS)
        expected_mtime = 1_720_000_000.0
        os.utime(str(f), (expected_mtime, expected_mtime))

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        f_info = report["tracks"]["cv"]["evidence_files"][0]
        from datetime import datetime, timezone
        parsed = datetime.fromisoformat(f_info["mtime_utc"])
        assert parsed.timestamp() == pytest.approx(expected_mtime, abs=1)


# ---------------------------------------------------------------------------
# Tests — CLI behavior
# ---------------------------------------------------------------------------

class TestCLIBehavior:
    """Given the CLI, When invoked with various flags, Then it behaves as
    expected."""

    def test_strict_exit_zero_when_all_pass(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_PASS)
        _write_json(base / "qwen-per-layer-compare-test.json", QWEN_LAYER_PASS)
        _write_json(base / "cv-golden.json", CV_GOLDEN_PASS)
        _write_json(base / "cv-host-runner-test.json", CV_HOST_PASS)
        _write_json(base / "e2e-signoff-summary.json", E2E_SUMMARY_PASS)

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=True)
        assert result.returncode == 0

        report = json.loads(output.read_text())
        assert report["overall_passed"] is True

    def test_strict_exit_nonzero_when_failure(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_PASS)
        _write_json(base / "qwen-per-layer-compare-test.json", QWEN_LAYER_PASS)
        _write_json(base / "cv-golden.json", CV_GOLDEN_PASS)
        _write_json(base / "cv-host-runner-test.json", CV_HOST_PASS)
        # e2e-signoff-summary missing

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=True)
        assert result.returncode != 0

    def test_non_strict_exits_zero_even_on_failure(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_FAIL_PASSED)

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)
        assert result.returncode == 0

        report = json.loads(output.read_text())
        assert report["overall_passed"] is False

    def test_missing_evidence_dir_exits_nonzero(self, tmp_path: Path) -> None:
        output = tmp_path / "report.json"
        result = _run_aggregator(tmp_path / "nonexistent", output, strict=False)
        assert result.returncode != 0

    def test_help_works(self) -> None:
        result = subprocess.run(
            [sys.executable, AGGREGATOR_SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert "--evidence-dir" in result.stdout
        assert "--output" in result.stdout
        assert "--strict" in result.stdout

    def test_output_file_is_valid_json(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "cv-golden.json", CV_GOLDEN_PASS)
        _write_json(base / "e2e-signoff-summary.json", E2E_SUMMARY_PASS)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        # Verify it's valid JSON with expected top-level keys
        report = json.loads(output.read_text())
        assert report["report_type"] == "e2e_aggregated_signoff"
        assert "timestamp" in report
        assert "overall_passed" in report
        assert "tracks" in report
        assert "qwen" in report["tracks"]
        assert "cv" in report["tracks"]
        assert "missing_evidence" in report
        assert "warnings" in report


# ---------------------------------------------------------------------------
# Tests — report structure
# ---------------------------------------------------------------------------

class TestReportStructure:
    """Given a complete evidence directory, When the aggregator runs, Then
    the output JSON has the correct structure."""

    def test_full_report_structure(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "qwen-full-forward-test.json", QWEN_FULL_PASS)
        _write_json(base / "qwen-per-layer-compare-test.json", QWEN_LAYER_PASS)
        _write_json(base / "cv-golden.json", CV_GOLDEN_PASS)
        _write_json(base / "cv-host-runner-test.json", CV_HOST_PASS)
        _write_json(base / "e2e-signoff-summary.json", E2E_SUMMARY_PASS)
        # Ensure host-runner is newer than golden (avoid mtime cross-check warning)
        os.utime(str(base / "cv-golden.json"), (1000, 1000))
        os.utime(str(base / "cv-host-runner-test.json"), (2000, 2000))

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=True)

        report = json.loads(output.read_text())

        # Top-level keys
        assert report["report_type"] == "e2e_aggregated_signoff"
        assert report["report_version"] == "1.0"
        assert isinstance(report["timestamp"], str)
        assert report["overall_passed"] is True

        # Track A
        qwen = report["tracks"]["qwen"]
        assert qwen["passed"] is True
        assert "checks" in qwen
        assert "full_forward" in qwen["checks"]
        assert "per_layer_compare" in qwen["checks"]
        assert len(qwen["evidence_files"]) == 2
        assert len(qwen["hashes"]) == 2

        # Track B
        cv = report["tracks"]["cv"]
        assert cv["passed"] is True
        assert "checks" in cv
        assert "golden" in cv["checks"]
        assert "host_runner" in cv["checks"]
        assert len(cv["evidence_files"]) == 2
        assert len(cv["hashes"]) == 2

        # E2E summary
        assert report["e2e_summary"]["passed"] is True
        assert len(report["e2e_summary"]["evidence_files"]) == 1

        # No missing or warnings
        assert report["missing_evidence"] == []
        assert report["warnings"] == []


# ---------------------------------------------------------------------------
# Tests — path recording uses relative path
# ---------------------------------------------------------------------------

class TestPathRecording:
    """Given evidence files, When the aggregator runs, Then evidence file
    paths are recorded relative to REPO_ROOT."""

    def test_relative_paths(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        _write_json(base / "cv-golden.json", CV_GOLDEN_PASS)

        output = tmp_path / "report.json"
        _run_aggregator(base, output, strict=False)

        report = json.loads(output.read_text())
        for f_info in report["tracks"]["cv"]["evidence_files"]:
            assert "path" in f_info
            # Should not be absolute (relative to CWD which may contain tmp_path)
            assert f_info["name"] == "cv-golden.json"


# ---------------------------------------------------------------------------
# Tests — corrupt JSON handling
# ---------------------------------------------------------------------------

class TestCorruptJSON:
    """Given a corrupt evidence file, When the aggregator runs, Then it
    is treated as a validation failure, not a crash."""

    def test_corrupt_qwen_full_forward(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        f = base / "qwen-full-forward-test.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("not valid json{{{")

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)
        assert result.returncode == 0  # does not crash

        report = json.loads(output.read_text())
        ff_check = report["tracks"]["qwen"]["checks"]["full_forward"]
        assert ff_check["passed"] is False

    def test_corrupt_per_layer_compare(self, tmp_path: Path) -> None:
        base = tmp_path / "evidence"
        f = base / "qwen-per-layer-compare-test.json"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("broken json")

        output = tmp_path / "report.json"
        result = _run_aggregator(base, output, strict=False)
        assert result.returncode == 0

        report = json.loads(output.read_text())
        pl_check = report["tracks"]["qwen"]["checks"]["per_layer_compare"]
        assert pl_check["passed"] is False
