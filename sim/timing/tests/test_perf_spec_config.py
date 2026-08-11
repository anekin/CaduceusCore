"""Tests for the Func Model performance spec checker.

Validates:
- Positive: main spec passes validation with 0 errors, 104 accepted.
- Negative: bad_units fixture correctly rejected.
- Negative: rtl_basis fixture correctly rejected.
- CLI exit codes and structured JSON verdict.
- Baseline characterization test pinning current observable behavior.
- Content hash determinism (no timestamp sensitivity).
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Paths relative to repo root
REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SPEC_PATH = REPO_ROOT / "config" / "func_model_perf_spec_v1.json"
CHECKER_PATH = REPO_ROOT / "scripts" / "check_func_model_perf_spec.py"
BAD_UNITS_FIXTURE = REPO_ROOT / "config" / "tests" / "perf_spec_bad_units.json"
RTL_BASIS_FIXTURE = REPO_ROOT / "config" / "tests" / "perf_spec_rtl_basis.json"


def _run_checker(*args: str) -> "subprocess.CompletedProcess[str]":
    """Run the checker script with given arguments."""
    cmd = [sys.executable, str(CHECKER_PATH), "--quiet", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _parse_verdict(output: str) -> dict:
    """Parse the JSON verdict from checker output."""
    return json.loads(output.strip())


# ── fixture existence checks ───────────────────────────────────────────

def test_spec_file_exists():
    assert SPEC_PATH.exists(), f"Spec file not found: {SPEC_PATH}"


def test_checker_script_exists():
    assert CHECKER_PATH.exists(), f"Checker script not found: {CHECKER_PATH}"


def test_bad_units_fixture_exists():
    assert BAD_UNITS_FIXTURE.exists(), f"Bad units fixture not found: {BAD_UNITS_FIXTURE}"


def test_rtl_basis_fixture_exists():
    assert RTL_BASIS_FIXTURE.exists(), f"RTL basis fixture not found: {RTL_BASIS_FIXTURE}"


# ── positive tests ─────────────────────────────────────────────────────

class TestPositiveSpecValidation:
    """Validate the normative spec JSON passes all checks."""

    def test_spec_passes_validation(self):
        """Main spec must validate with exit 0 and valid=true."""
        result = _run_checker("--spec", str(SPEC_PATH))
        assert result.returncode == 0, f"Checker exited non-zero: {result.stderr}"
        verdict = _parse_verdict(result.stdout)
        assert verdict["spec"]["valid"] is True, f"Spec not valid: {verdict['spec'].get('error_details')}"

    def test_spec_zero_errors(self):
        """Main spec must have 0 errors."""
        result = _run_checker("--spec", str(SPEC_PATH))
        verdict = _parse_verdict(result.stdout)
        assert verdict["spec"]["errors"] == 0, f"Expected 0 errors, got {verdict['spec']['errors']}"

    def test_spec_104_accepted(self):
        """Main spec must have 104 accepted parameters (all valid)."""
        result = _run_checker("--spec", str(SPEC_PATH))
        verdict = _parse_verdict(result.stdout)
        assert verdict["spec"]["accepted"] == 104, f"Expected 104 accepted, got {verdict['spec']['accepted']}"

    def test_spec_0_rejected(self):
        """Main spec must have 0 rejected parameters."""
        result = _run_checker("--spec", str(SPEC_PATH))
        verdict = _parse_verdict(result.stdout)
        assert verdict["spec"]["rejected"] == 0, f"Expected 0 rejected, got {verdict['spec']['rejected']}"

    def test_spec_total_parameters(self):
        """Main spec must have exactly 104 total parameters."""
        result = _run_checker("--spec", str(SPEC_PATH))
        verdict = _parse_verdict(result.stdout)
        assert verdict["spec"]["total_parameters"] == 104

    def test_spec_domain_counts_match_frozen_matrix(self):
        """Verify domain counts match the frozen hard-gate matrix."""
        result = _run_checker("--spec", str(SPEC_PATH))
        verdict = _parse_verdict(result.stdout)
        counts = verdict["spec"]["domain_counts"]
        expected = {
            "mxu": 10,
            "sfu": 24,
            "vector": 30,
            "dma": 10,
            "dram": 10,
            "noc": 8,
            "kv_cache": 8,
            "sw_overhead": 4,
        }
        assert counts == expected, f"Domain counts mismatch: {counts} != {expected}"

    def test_spec_content_hash_present(self):
        """Spec verdict must include a content hash."""
        result = _run_checker("--spec", str(SPEC_PATH))
        verdict = _parse_verdict(result.stdout)
        ch = verdict["spec"].get("content_hash")
        assert ch is not None, "Content hash missing"
        assert len(ch) == 64, f"Expected SHA-256 (64 hex chars), got {len(ch)}"

    def test_spec_content_hash_deterministic(self):
        """Content hash must be deterministic (identical on two runs)."""
        r1 = _run_checker("--spec", str(SPEC_PATH))
        r2 = _run_checker("--spec", str(SPEC_PATH))
        v1 = _parse_verdict(r1.stdout)
        v2 = _parse_verdict(r2.stdout)
        assert v1["spec"]["content_hash"] == v2["spec"]["content_hash"], \
            "Content hash changed between runs (non-deterministic)"

    def test_spec_schema_version(self):
        """Spec must have schema_version '1.0'."""
        result = _run_checker("--spec", str(SPEC_PATH))
        verdict = _parse_verdict(result.stdout)
        assert verdict["spec"]["schema_version"] == "1.0"


# ── negative tests ─────────────────────────────────────────────────────

class TestNegativeFixtures:
    """Validate that negative fixtures are correctly rejected."""

    def test_bad_units_rejected(self):
        """Bad units fixture must be rejected (valid=False)."""
        result = _run_checker("--negative-fixtures", str(BAD_UNITS_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        fixtures = verdict["negative_fixtures"]
        assert len(fixtures) == 1
        f = fixtures[0]
        assert f["fixture_passes"] is True, f"Bad units fixture not rejected: {f.get('fail_reason', '')}"
        assert f["valid"] is False

    def test_bad_units_all_rejected(self):
        """Bad units fixture: all 3 parameters must be rejected individually."""
        result = _run_checker("--negative-fixtures", str(BAD_UNITS_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        f = verdict["negative_fixtures"][0]
        assert f["accepted"] == 0
        assert f["rejected"] == 3
        assert f["total_parameters"] == 3

    def test_bad_units_error_messages(self):
        """Bad units fixture errors must mention the invalid units."""
        result = _run_checker("--negative-fixtures", str(BAD_UNITS_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        errors = verdict["negative_fixtures"][0]["error_details"]
        error_msgs = " ".join(e["message"] for e in errors)
        assert "furlongs" in error_msgs
        assert "parsecs" in error_msgs
        assert "lightyears" in error_msgs

    def test_rtl_basis_rejected(self):
        """RTL basis fixture must be rejected (valid=False)."""
        result = _run_checker("--negative-fixtures", str(RTL_BASIS_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        f = verdict["negative_fixtures"][0]
        assert f["fixture_passes"] is True, f"RTL basis fixture not rejected: {f.get('fail_reason', '')}"
        assert f["valid"] is False

    def test_rtl_basis_all_rejected(self):
        """RTL basis fixture: all 3 parameters must be rejected."""
        result = _run_checker("--negative-fixtures", str(RTL_BASIS_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        f = verdict["negative_fixtures"][0]
        assert f["accepted"] == 0
        assert f["rejected"] == 3
        assert f["total_parameters"] == 3

    def test_rtl_basis_error_messages(self):
        """RTL basis fixture errors must mention rtl_measurement."""
        result = _run_checker("--negative-fixtures", str(RTL_BASIS_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        errors = verdict["negative_fixtures"][0]["error_details"]
        error_msgs = " ".join(e["message"] for e in errors)
        assert "rtl_measurement" in error_msgs

    def test_both_negative_fixtures_together(self):
        """Both negative fixtures together: rejected=6, accepted=0."""
        result = _run_checker(
            "--negative-fixtures",
            f"{BAD_UNITS_FIXTURE},{RTL_BASIS_FIXTURE}",
        )
        verdict = _parse_verdict(result.stdout)
        fixtures = verdict["negative_fixtures"]
        assert len(fixtures) == 2
        for f in fixtures:
            assert f["fixture_passes"] is True, f"Fixture {f['file']} not rejected"
            assert f["valid"] is False
            assert f["accepted"] == 0

    def test_spec_and_negative_fixtures_combined(self):
        """Combined: spec passes + both fixtures correctly rejected."""
        result = _run_checker(
            "--spec", str(SPEC_PATH),
            "--negative-fixtures", f"{BAD_UNITS_FIXTURE},{RTL_BASIS_FIXTURE}",
        )
        verdict = _parse_verdict(result.stdout)
        assert verdict["spec"]["valid"] is True
        for f in verdict["negative_fixtures"]:
            assert f["fixture_passes"] is True
            assert f["valid"] is False


# ── CLI exit code tests ────────────────────────────────────────────────

class TestCLIExitCodes:
    """Verify correct exit codes for various scenarios."""

    def test_valid_spec_exit_zero(self):
        """Checker exits 0 when spec is valid."""
        result = _run_checker("--spec", str(SPEC_PATH))
        assert result.returncode == 0

    def test_negative_fixtures_correctly_rejected_exit_zero(self):
        """Checker exits 0 when all negative fixtures are correctly rejected."""
        result = _run_checker("--negative-fixtures", f"{BAD_UNITS_FIXTURE},{RTL_BASIS_FIXTURE}")
        assert result.returncode == 0, f"Exit code: {result.returncode}"

    def test_missing_spec_file_exit_one(self):
        """Checker exits 1 when spec file not found."""
        result = _run_checker("--spec", "/nonexistent/path/spec.json")
        assert result.returncode == 1

    def test_json_output_flag(self):
        """--json-output works with valid spec."""
        result = subprocess.run(
            [sys.executable, str(CHECKER_PATH), "--spec", str(SPEC_PATH), "--json-output"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["spec"]["valid"] is True


# ── baseline characterization test ──────────────────────────────────────

class TestBaselineCharacterization:
    """Pin current observable behavior before any edits to timing/types.py or sim/models/*.

    These tests characterize the current state of the spec JSON itself,
    NOT the timing implementation. They serve as a change-detection canary:
    if someone accidentally modifies the spec, these tests will catch it.
    """

    def test_spec_json_parseable(self):
        """Spec JSON must be parseable as valid JSON."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "domains" in data

    def test_all_domains_present(self):
        """All 8 domains must be present in spec."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        domains = set(data["domains"].keys())
        expected = {"mxu", "sfu", "vector", "dma", "dram", "noc", "kv_cache", "sw_overhead"}
        assert domains == expected, f"Missing/extra domains: {domains ^ expected}"

    def test_all_parameters_have_required_fields(self):
        """Every parameter must have all required fields."""
        required = {"parameter_id", "domain", "description", "formula", "inputs",
                     "estimated_cycles", "units", "owner", "basis", "uncertainty", "rationale"}
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        for domain, params in data["domains"].items():
            for param in params:
                missing = required - set(param.keys())
                assert not missing, f"{param.get('parameter_id', domain)}: missing fields {missing}"

    def test_no_parameter_has_rtl_basis(self):
        """No parameter must use rtl_measurement basis."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        for domain, params in data["domains"].items():
            for param in params:
                assert param.get("basis") != "rtl_measurement", \
                    f"{param['parameter_id']}: basis is rtl_measurement"

    def test_all_estimated_cycles_are_finite(self):
        """All estimated_cycles values must be finite (not NaN/Inf)."""
        import math
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        for domain, params in data["domains"].items():
            for param in params:
                val = param.get("estimated_cycles")
                assert val is not None, f"{param['parameter_id']}: estimated_cycles is None"
                assert math.isfinite(float(val)), f"{param['parameter_id']}: estimated_cycles={val} not finite"
                assert float(val) >= 0, f"{param['parameter_id']}: estimated_cycles={val} is negative"

    def test_kv_token_pos_0_is_noop(self):
        """KV cache token_pos=0 must be declared expected_noop=true with 0 cycles."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        kv_params = data["domains"]["kv_cache"]
        found = [p for p in kv_params if p["parameter_id"] == "kv_token_pos_0"]
        assert len(found) == 1, "kv_token_pos_0 not found"
        p = found[0]
        assert p["expected_noop"] is True, "kv_token_pos_0 not marked expected_noop"
        assert p["estimated_cycles"] == 0, f"kv_token_pos_0 cycles={p['estimated_cycles']}, expected 0"

    def test_parameter_ids_are_unique(self):
        """All parameter_ids across all domains must be unique."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        seen = set()
        for domain, params in data["domains"].items():
            for param in params:
                pid = param["parameter_id"]
                assert pid not in seen, f"Duplicate parameter_id: {pid}"
                seen.add(pid)
        assert len(seen) == 104, f"Expected 104 unique IDs, got {len(seen)}"

    def test_monotonicity_annotations_present_on_all_params(self):
        """Every parameter must have monotonicity_annotations for T18."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        for domain, params in data["domains"].items():
            for param in params:
                assert "monotonicity_annotations" in param, \
                    f"{param['parameter_id']}: missing monotonicity_annotations"

    def test_spec_content_hash_matches_computed(self):
        """Content hash in spec verdict must match the computed value."""
        result = _run_checker("--spec", str(SPEC_PATH))
        verdict = _parse_verdict(result.stdout)
        # Re-run to verify determinism
        result2 = _run_checker("--spec", str(SPEC_PATH))
        verdict2 = _parse_verdict(result2.stdout)
        assert verdict["spec"]["content_hash"] == verdict2["spec"]["content_hash"]


# ── mutation tests ──────────────────────────────────────────────────────

class TestMutationDetection:
    """Verify checker detects spec mutations (tampered JSON)."""

    def test_nan_cycles_detected(self):
        """NaN cycles must be rejected."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        # Create a mutated copy
        mutated = json.loads(json.dumps(data))
        mutated["domains"]["mxu"][0]["estimated_cycles"] = float("nan")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--spec", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["spec"]["valid"] is False, "NaN cycles not detected"
            assert verdict["spec"]["errors"] > 0
        finally:
            Path(tf_path).unlink()

    def test_inf_cycles_detected(self):
        """Inf cycles must be rejected."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        mutated = json.loads(json.dumps(data))
        mutated["domains"]["sfu"][0]["estimated_cycles"] = float("inf")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--spec", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["spec"]["valid"] is False, "Inf cycles not detected"
        finally:
            Path(tf_path).unlink()

    def test_negative_cycles_detected(self):
        """Negative cycles must be rejected (unless expected_noop=true with exact zero)."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        mutated = json.loads(json.dumps(data))
        mutated["domains"]["vector"][0]["estimated_cycles"] = -5
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--spec", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["spec"]["valid"] is False, "Negative cycles not detected"
        finally:
            Path(tf_path).unlink()

    def test_missing_owner_detected(self):
        """Missing owner must be rejected."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        mutated = json.loads(json.dumps(data))
        mutated["domains"]["dma"][0]["owner"] = ""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--spec", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["spec"]["valid"] is False, "Empty owner not detected"
        finally:
            Path(tf_path).unlink()

    def test_rtl_basis_inline_detected(self):
        """Inline rtl_measurement in a mutated spec must be detected."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        mutated = json.loads(json.dumps(data))
        mutated["domains"]["dram"][0]["basis"] = "rtl_measurement"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--spec", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["spec"]["valid"] is False, "rtl_measurement not detected in mutated spec"
        finally:
            Path(tf_path).unlink()

    def test_duplicate_parameter_id_detected(self):
        """Duplicate parameter_id must be detected."""
        with open(SPEC_PATH, "r") as f:
            data = json.load(f)
        mutated = json.loads(json.dumps(data))
        # Duplicate the first MXU param
        dup = json.loads(json.dumps(mutated["domains"]["mxu"][0]))
        dup["parameter_id"] = "mxu_1_64_64"  # same as existing
        mutated["domains"]["mxu"].append(dup)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--spec", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["spec"]["valid"] is False, "Duplicate parameter_id not detected"
        finally:
            Path(tf_path).unlink()


# ── matrix validation tests ────────────────────────────────────────────

MATRIX_PATH = REPO_ROOT / "config" / "func_model_perf_matrix_v1.json"
MATRIX_DUP_FIXTURE = REPO_ROOT / "config" / "tests" / "perf_matrix_duplicate.json"
MATRIX_MISSING_FIXTURE = REPO_ROOT / "config" / "tests" / "perf_matrix_missing.json"
MATRIX_SKIP_FIXTURE = REPO_ROOT / "config" / "tests" / "perf_matrix_skip.json"
MATRIX_MISSING_6P4_FIXTURE = REPO_ROOT / "config" / "tests" / "perf_matrix_missing_6p4_endpoint.json"

ALL_MATRIX_FIXTURES = ",".join([
    str(MATRIX_DUP_FIXTURE),
    str(MATRIX_MISSING_FIXTURE),
    str(MATRIX_SKIP_FIXTURE),
    str(MATRIX_MISSING_6P4_FIXTURE),
])


# ── fixture existence checks ───────────────────────────────────────────

def test_matrix_file_exists():
    assert MATRIX_PATH.exists(), f"Matrix file not found: {MATRIX_PATH}"


def test_matrix_dup_fixture_exists():
    assert MATRIX_DUP_FIXTURE.exists(), f"Dup fixture not found: {MATRIX_DUP_FIXTURE}"


def test_matrix_missing_fixture_exists():
    assert MATRIX_MISSING_FIXTURE.exists(), f"Missing fixture not found: {MATRIX_MISSING_FIXTURE}"


def test_matrix_skip_fixture_exists():
    assert MATRIX_SKIP_FIXTURE.exists(), f"Skip fixture not found: {MATRIX_SKIP_FIXTURE}"


def test_matrix_missing_6p4_fixture_exists():
    assert MATRIX_MISSING_6P4_FIXTURE.exists(), f"6p4 fixture not found: {MATRIX_MISSING_6P4_FIXTURE}"


# ── positive matrix tests ──────────────────────────────────────────────

class TestPositiveMatrixValidation:

    def test_matrix_passes_validation(self):
        result = _run_checker("--matrix", str(MATRIX_PATH))
        assert result.returncode == 0, f"Matrix checker exited non-zero: {result.stderr}"
        verdict = _parse_verdict(result.stdout)
        assert verdict["matrix"]["valid"] is True

    def test_matrix_zero_errors(self):
        result = _run_checker("--matrix", str(MATRIX_PATH))
        verdict = _parse_verdict(result.stdout)
        assert verdict["matrix"]["errors"] == 0

    def test_matrix_seed_42(self):
        result = _run_checker("--matrix", str(MATRIX_PATH))
        verdict = _parse_verdict(result.stdout)
        assert verdict["matrix"]["seed_check"] is True

    def test_matrix_domain_counts_match_spec(self):
        result = _run_checker("--matrix", str(MATRIX_PATH))
        verdict = _parse_verdict(result.stdout)
        counts = verdict["matrix"]["domain_counts"]
        expected = {
            "mxu": 10, "sfu": 24, "vector": 30,
            "dma": 10, "dram": 10, "noc": 8,
            "kv_cache": 8, "sw_overhead": 4,
        }
        assert counts == expected, f"Matrix domain counts mismatch: {counts} != {expected}"

    def test_matrix_total_104_provider_rows(self):
        result = _run_checker("--matrix", str(MATRIX_PATH))
        verdict = _parse_verdict(result.stdout)
        assert verdict["matrix"]["total_parameters"] == 104

    def test_matrix_spec_combined(self):
        result = _run_checker(
            "--spec", str(SPEC_PATH),
            "--matrix", str(MATRIX_PATH),
        )
        assert result.returncode == 0
        verdict = _parse_verdict(result.stdout)
        assert verdict["spec"]["valid"] is True
        assert verdict["matrix"]["valid"] is True


# ── negative matrix fixture tests ──────────────────────────────────────

class TestNegativeMatrixFixtures:

    def test_duplicate_ids_rejected(self):
        result = _run_checker("--negative-fixtures", str(MATRIX_DUP_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        f = verdict["negative_fixtures"][0]
        assert f["fixture_passes"] is True, f"Dup fixture not rejected: {f.get('fail_reason', '')}"
        assert f["valid"] is False

    def test_missing_rows_rejected(self):
        result = _run_checker("--negative-fixtures", str(MATRIX_MISSING_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        f = verdict["negative_fixtures"][0]
        assert f["fixture_passes"] is True
        assert f["valid"] is False

    def test_skip_flag_rejected(self):
        result = _run_checker("--negative-fixtures", str(MATRIX_SKIP_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        f = verdict["negative_fixtures"][0]
        assert f["fixture_passes"] is True, f"Skip fixture not rejected: {f.get('fail_reason', '')}"
        assert f["valid"] is False

    def test_missing_6p4_endpoint_rejected(self):
        result = _run_checker("--negative-fixtures", str(MATRIX_MISSING_6P4_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        f = verdict["negative_fixtures"][0]
        assert f["fixture_passes"] is True, f"6p4 fixture not rejected: {f.get('fail_reason', '')}"
        assert f["valid"] is False

    def test_all_four_matrix_fixtures_rejected(self):
        result = _run_checker("--negative-fixtures", ALL_MATRIX_FIXTURES)
        verdict = _parse_verdict(result.stdout)
        fixtures = verdict["negative_fixtures"]
        assert len(fixtures) == 4
        for f in fixtures:
            assert f["fixture_passes"] is True, f"Fixture {f['file']} not rejected"
            assert f["valid"] is False

    def test_matrix_dup_error_mentions_duplicate(self):
        result = _run_checker("--negative-fixtures", str(MATRIX_DUP_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        errors = verdict["negative_fixtures"][0]["error_details"]
        error_msgs = " ".join(e["message"] for e in errors)
        assert "duplicate case_id" in error_msgs

    def test_matrix_skip_error_mentions_skip(self):
        result = _run_checker("--negative-fixtures", str(MATRIX_SKIP_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        errors = verdict["negative_fixtures"][0]["error_details"]
        error_msgs = " ".join(e["message"] for e in errors)
        assert "skip" in error_msgs.lower()

    def test_matrix_6p4_error_mentions_bottleneck(self):
        result = _run_checker("--negative-fixtures", str(MATRIX_MISSING_6P4_FIXTURE))
        verdict = _parse_verdict(result.stdout)
        errors = verdict["negative_fixtures"][0]["error_details"]
        error_msgs = " ".join(e["message"] for e in errors)
        assert "bottleneck" in error_msgs.lower()


# ── matrix CLI exit code tests ─────────────────────────────────────────

class TestMatrixCLIExitCodes:

    def test_valid_matrix_exit_zero(self):
        result = _run_checker("--matrix", str(MATRIX_PATH))
        assert result.returncode == 0

    def test_negative_fixtures_all_rejected_exit_zero(self):
        result = _run_checker("--negative-fixtures", ALL_MATRIX_FIXTURES)
        assert result.returncode == 0

    def test_missing_matrix_file_exit_one(self):
        result = _run_checker("--matrix", "/nonexistent/path/matrix.json")
        assert result.returncode == 1

    def test_matrix_json_output_flag(self):
        result = subprocess.run(
            [sys.executable, str(CHECKER_PATH), "--matrix", str(MATRIX_PATH), "--json-output"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["matrix"]["valid"] is True


# ── matrix baseline characterization tests ─────────────────────────────

class TestMatrixBaselineCharacterization:

    def test_matrix_json_parseable(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert "provider_matrix" in data

    def test_matrix_has_all_required_sections(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        required = {"provider_matrix", "workloads", "sweep_grids", "frozen_policies"}
        missing = required - set(data.keys())
        assert not missing, f"Missing sections: {missing}"

    def test_matrix_seed_is_42(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        assert data["seed"] == 42

    def test_matrix_all_case_ids_unique(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        seen = set()
        for domain, cases in data["provider_matrix"]["rows"].items():
            for case in cases:
                cid = case["case_id"]
                assert cid not in seen, f"Duplicate case_id: {cid}"
                seen.add(cid)
        assert len(seen) == 104

    def test_matrix_bottleneck_endpoints_present(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        eps = data["sweep_grids"]["bottleneck_endpoints"]
        eids = {e["endpoint_id"] for e in eps}
        assert "bottleneck_mem_bound" in eids
        assert "bottleneck_compute_bound" in eids

    def test_matrix_runtime_limits(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        limits = data["frozen_policies"]["runtime_limits"]
        assert limits["provider_case_seconds"]["max"] == 30
        assert limits["workload_seconds"]["max"] == 120
        assert limits["full_signoff_seconds"]["max"] == 1800
        assert limits["peak_rss_mb"]["max"] == 4096

    def test_matrix_bandwidth_includes_6p4(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        mem_eps = [e for e in data["sweep_grids"]["bottleneck_endpoints"]
                    if e["endpoint_id"] == "bottleneck_mem_bound"]
        assert len(mem_eps) == 1
        assert mem_eps[0]["config"]["bandwidth"] == 6.4
        assert mem_eps[0]["config"]["array"] == 128

    def test_matrix_bandwidth_includes_102p4(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        comp_eps = [e for e in data["sweep_grids"]["bottleneck_endpoints"]
                     if e["endpoint_id"] == "bottleneck_compute_bound"]
        assert len(comp_eps) == 1
        assert comp_eps[0]["config"]["bandwidth"] == 102.4
        assert comp_eps[0]["config"]["array"] == 32

    def test_matrix_workload_counts(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        wl_ids = {e["workload_id"] for e in data["workloads"]["entries"]}
        assert len(wl_ids) == 7
        assert wl_ids == {"qwen25-3b-blk0-decode", "qwen25-3b-decode-c128-g1",
                           "qwen-prefill-16", "qwen-prefill-128",
                           "mobilenetv3", "resnet50", "yolov8n"}


# ── matrix mutation tests ──────────────────────────────────────────────

class TestMatrixMutationDetection:

    def test_wrong_seed_detected(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        mutated = json.loads(json.dumps(data))
        mutated["seed"] = 12345
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--matrix", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["matrix"]["valid"] is False, "Wrong seed not detected"
            assert verdict["matrix"]["seed_check"] is False
        finally:
            Path(tf_path).unlink()

    def test_missing_no_silent_skip_detected(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        mutated = json.loads(json.dumps(data))
        del mutated["frozen_policies"]["no_silent_skip"]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--matrix", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["matrix"]["valid"] is False, "Missing no_silent_skip not detected"
        finally:
            Path(tf_path).unlink()

    def test_skip_flag_nested_detected(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        mutated = json.loads(json.dumps(data))
        mutated["provider_matrix"]["rows"]["mxu"][0]["skip"] = True
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--matrix", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["matrix"]["valid"] is False, "Nested skip flag not detected"
        finally:
            Path(tf_path).unlink()

    def test_duplicate_workload_id_detected(self):
        with open(MATRIX_PATH, "r") as f:
            data = json.load(f)
        mutated = json.loads(json.dumps(data))
        dup = json.loads(json.dumps(mutated["workloads"]["entries"][0]))
        mutated["workloads"]["entries"].append(dup)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
            json.dump(mutated, tf)
            tf_path = tf.name
        try:
            result = _run_checker("--matrix", tf_path)
            verdict = _parse_verdict(result.stdout)
            assert verdict["matrix"]["valid"] is False, "Duplicate workload_id not detected"
        finally:
            Path(tf_path).unlink()
