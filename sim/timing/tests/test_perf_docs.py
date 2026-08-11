"""Tests for the Func Model performance documentation checker.

Covers:
- Required docs pass semantic checks (no forbidden phrases, corrected Qwen dims,
  required markers present).
- Bug ledger contains the two required deferred-scope entries and zero waivers.
- Negative fixtures are rejected as expected.
- Checker functions operate on temporary markdown content.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = REPO_ROOT / "scripts"
CHECKER = SCRIPTS_DIR / "check_func_model_perf_docs.py"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import check_func_model_perf_docs as checker


class TestDocScanning(unittest.TestCase):
    def test_forbidden_phrase_detected(self):
        text = "The Func Model is cycle-accurate and RTL-calibrated."
        findings = checker._scan_forbidden_phrases(text, "test.md")
        categories = {f.category for f in findings}
        self.assertIn("forbidden_phrase", categories)

    def test_stale_qwen_value_detected(self):
        text = "hidden=2560, intermediate=9728, layers=28"
        findings = checker._scan_stale_qwen_values(text, "test.md")
        self.assertTrue(findings)
        self.assertEqual({f.category for f in findings}, {"stale_qwen_dims"})

    def test_qkv_dim_not_matched_as_kv_dim(self):
        text = "qkv_dim=2048, kv_dim=256, kv_heads=2"
        findings = checker._scan_stale_qwen_values(text, "test.md")
        self.assertFalse(findings)

    def test_required_markers_missing(self):
        text = "Some generic doc without required terminology."
        findings = checker._scan_required_markers(text, "test.md")
        self.assertEqual(len(findings), len(checker._REQUIRED_MARKERS))

    def test_ignore_annotation_skips_line(self):
        text = "hidden=2560 <!-- doc-check: ignore -->\ncorrect hidden=2048"
        findings = checker._scan_stale_qwen_values(text, "test.md")
        self.assertEqual(len(findings), 0)


class TestBugLedger(unittest.TestCase):
    def test_required_deferred_entries_detected(self):
        text = "BUG-SOC-FM-009 non-Block engines deferred.\nBUG-SOC-FM-010 GMMA dead constant."
        result = checker.check_bug_ledger_from_text(text, "test.md")
        self.assertEqual(result.deferred_present, ["BUG-SOC-FM-009", "BUG-SOC-FM-010"])
        self.assertEqual(len(result.findings), 0)

    def test_missing_deferred_entry_fails(self):
        text = "BUG-SOC-FM-009 non-Block engines deferred."
        result = checker.check_bug_ledger_from_text(text, "test.md")
        self.assertTrue(any("BUG-SOC-FM-010" in f.message for f in result.findings))

    def test_waiver_language_rejected(self):
        text = "BUG-SOC-FM-009\nThis is a waiver.\nBUG-SOC-FM-010"
        result = checker.check_bug_ledger_from_text(text, "test.md")
        self.assertTrue(any(f.category == "waiver_found" for f in result.findings))


class TestNegativeFixtures(unittest.TestCase):
    def test_cycle_accurate_fixture_rejected(self):
        path = REPO_ROOT / "config/tests/docs_cycle_accurate.md"
        verdict = checker.check_negative_fixture(path)
        self.assertTrue(verdict["fixture_passes"])
        self.assertEqual(verdict["accepted"], 0)

    def test_old_qwen_fixture_rejected(self):
        path = REPO_ROOT / "config/tests/docs_old_qwen.md"
        verdict = checker.check_negative_fixture(path)
        self.assertTrue(verdict["fixture_passes"])
        self.assertEqual(verdict["accepted"], 0)

    def test_kpi_gate_fixture_rejected(self):
        path = REPO_ROOT / "config/tests/docs_kpi_gate.md"
        verdict = checker.check_negative_fixture(path)
        self.assertTrue(verdict["fixture_passes"])
        self.assertEqual(verdict["accepted"], 0)


class TestCLI(unittest.TestCase):
    def _run(self, args: str) -> subprocess.CompletedProcess:
        cmd = [sys.executable, str(CHECKER)] + args.split()
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=30,
        )

    def test_green_command_passes(self):
        r = self._run(
            "--spec config/func_model_perf_spec_v1.json "
            "--bugs docs/bugs/bugs-soc-func-model.md "
            "--json-output"
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        result = json.loads(r.stdout)
        self.assertTrue(result["docs"]["valid"])
        self.assertTrue(result["bugs"]["valid"])
        self.assertEqual(result["summary"]["blocking_open_defects"], 0)
        self.assertEqual(result["summary"]["waivers"], 0)

    def test_negative_fixtures_command(self):
        r = self._run(
            "--negative-fixtures "
            "config/tests/docs_cycle_accurate.md,"
            "config/tests/docs_old_qwen.md,"
            "config/tests/docs_kpi_gate.md "
            "--json-output"
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        result = json.loads(r.stdout)
        self.assertEqual(result["summary"]["rejected"], 3)
        self.assertEqual(result["summary"]["accepted"], 0)


if __name__ == "__main__":
    unittest.main()
