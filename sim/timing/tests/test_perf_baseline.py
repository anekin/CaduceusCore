"""T22 versioned performance-spec regression baseline tests.

Covers baseline create, validate, read-only validate, and the three negative
faults: accept-current, stale-spec, hidden-hard-gate.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from timing.perf_baseline import (
    _hash_file,
    _mutate_oracle_for_hidden_hard_gate,
    _mutate_spec_for_stale_spec,
    compute_baseline_content_hash,
    compute_input_hashes,
    create_baseline,
    run_baseline_negative,
    validate_baseline,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


class TestBaselineCreate(unittest.TestCase):
    def test_create_baseline_writes_required_fields(self):
        """create_baseline writes a JSON file with all required fields."""
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "baseline.json"
            baseline = create_baseline(output)
            self.assertTrue(output.is_file())

            data = json.loads(output.read_text(encoding="utf-8"))
            for key in (
                "baseline_id",
                "created",
                "spec_hash",
                "matrix_hash",
                "oracle_hash",
                "workload_oracle_hash",
                "provider_config_hash",
                "workload_manifest_hashes",
                "canonical_results",
                "canonical_content_hash",
                "policy",
            ):
                self.assertIn(key, data, f"missing required field: {key}")

            self.assertEqual(data["baseline_id"], "func_model_perf_spec_v1")
            self.assertEqual(
                data["canonical_content_hash"],
                compute_baseline_content_hash(data),
            )

    def test_create_baseline_hashes_match_input_hashes(self):
        """Stored hashes match independently computed input hashes."""
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "baseline.json"
            baseline = create_baseline(output)
            current = compute_input_hashes()
            self.assertEqual(baseline["spec_hash"], current["spec_hash"])
            self.assertEqual(baseline["matrix_hash"], current["matrix_hash"])
            self.assertEqual(baseline["oracle_hash"], current["oracle_hash"])
            self.assertEqual(baseline["workload_oracle_hash"], current["workload_oracle_hash"])
            self.assertEqual(baseline["provider_config_hash"], current["provider_config_hash"])
            self.assertEqual(baseline["workload_manifest_hashes"], current["workload_manifest_hashes"])


class TestBaselineValidate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp())
        cls.baseline_path = cls.tmpdir / "baseline.json"
        cls.baseline = create_baseline(cls.baseline_path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_validate_baseline_passes_fresh(self):
        """validate_baseline passes against an unchanged baseline."""
        report = validate_baseline(self.baseline_path, require_fresh=True)
        self.assertEqual(report["verdict"], "pass")
        self.assertTrue(report["input_hash_match"])
        self.assertTrue(report["canonical_content_hash_match"])
        self.assertEqual(report["hard_failures"], [])
        self.assertEqual(report["hash_failures"], [])
        self.assertTrue(report["read_only"])

    def test_validate_baseline_rejects_stale_spec(self):
        """Mutated spec produces a spec_hash mismatch failure."""
        mutated_spec = _mutate_spec_for_stale_spec(
            REPO_ROOT / "config" / "func_model_perf_spec_v1.json",
            self.tmpdir,
        )
        report = validate_baseline(self.baseline_path, spec_path=mutated_spec)
        self.assertEqual(report["verdict"], "fail")
        self.assertTrue(any("spec_hash" in f for f in report["hash_failures"]))

    def test_validate_baseline_rejects_hidden_hard_gate(self):
        """Mutated provider oracle fails provider formula gates while KPIs stay stable."""
        mutated_oracle = _mutate_oracle_for_hidden_hard_gate(
            REPO_ROOT / "config" / "func_model_perf_oracle_v1.json",
            self.tmpdir,
        )
        report = validate_baseline(self.baseline_path, oracle_path=mutated_oracle)
        self.assertEqual(report["verdict"], "fail")
        self.assertTrue(
            any("provider_gates" in f for f in report["hard_failures"]),
            f"expected provider gate failure, got {report['hard_failures']}",
        )

    def test_validate_baseline_is_read_only(self):
        """validate_baseline does not modify the baseline file."""
        before_hash = _hash_file(self.baseline_path)
        before_mtime = self.baseline_path.stat().st_mtime
        validate_baseline(self.baseline_path)
        after_hash = _hash_file(self.baseline_path)
        after_mtime = self.baseline_path.stat().st_mtime
        self.assertEqual(before_hash, after_hash)
        self.assertEqual(before_mtime, after_mtime)


class TestBaselineNegative(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp())
        cls.baseline_path = cls.tmpdir / "baseline.json"
        create_baseline(cls.baseline_path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_negative_baseline_rejects_all_three_faults(self):
        """run_baseline_negative returns rejected=3, accepted=0."""
        report = run_baseline_negative(
            ["accept-current", "stale-spec", "hidden-hard-gate"],
            self.baseline_path,
        )
        self.assertEqual(report["rejected"], 3)
        self.assertEqual(report["accepted"], 0)
        self.assertTrue(report["all_passed"])
        self.assertEqual(report["verdict"], "pass")
        for fault in ("accept-current", "stale-spec", "hidden-hard-gate"):
            self.assertTrue(
                report["results"][fault]["rejected"],
                f"{fault} was not rejected",
            )


if __name__ == "__main__":
    unittest.main()
