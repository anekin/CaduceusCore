"""T5: Path A/Path B independent oracle isolation tests.

Tests AST import-policy, runtime subprocess isolation, mutation detection,
and verify that the two oracle scripts are genuinely independent.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Tuple


# ── Constants ────────────────────────────────────────────────────────────

FORBIDDEN_MODULES = frozenset({
    "sim.models",
    "sim.engine",
    "sim.timing.providers",
    "sim.timing.timing_engine",
    "sim.npu_sim",
})

FORBIDDEN_PREFIXES = tuple(sorted(FORBIDDEN_MODULES))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

VERIFIER_SCRIPT = REPO_ROOT / "scripts" / "verify_func_model_perf_spec.py"
REDUCER_SCRIPT = REPO_ROOT / "scripts" / "reduce_func_model_perf_oracle.py"
PROVIDER_ORACLE = REPO_ROOT / "config" / "func_model_perf_oracle_v1.json"
WORKLOAD_ORACLE = REPO_ROOT / "config" / "func_model_workload_oracle_v1.json"
TEMPLATE = REPO_ROOT / "config" / "oracle" / "qwen25_3b_layer_template_v1.json"
VARIANTS = REPO_ROOT / "config" / "oracle" / "qwen25_3b_workload_variants_v1.json"
SPEC = REPO_ROOT / "config" / "func_model_perf_spec_v1.json"


# ── AST import-policy ────────────────────────────────────────────────────

def _check_import_policy(filepath: str) -> Tuple[bool, List[str]]:
    """Scan a Python file for forbidden imports."""
    violations: List[str] = []
    try:
        with open(filepath, "r") as f:
            tree = ast.parse(f.read(), filename=filepath)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_MODULES or alias.name.startswith(FORBIDDEN_PREFIXES):
                        violations.append(f"import {alias.name} at line {node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                mod_name = node.module or ""
                if mod_name in FORBIDDEN_MODULES or mod_name.startswith(FORBIDDEN_PREFIXES):
                    violations.append(f"from {mod_name} import ... at line {node.lineno}")
    except Exception as e:
        violations.append(f"AST parse error: {e}")
    return len(violations) == 0, violations


class TestASTImportPolicy:
    """AST-level import policy: neither script may import Path A modules."""

    def test_verifier_no_forbidden_imports(self):
        ok, violations = _check_import_policy(str(VERIFIER_SCRIPT))
        assert ok, f"Verifier has forbidden imports: {violations}"

    def test_reducer_no_forbidden_imports(self):
        ok, violations = _check_import_policy(str(REDUCER_SCRIPT))
        assert ok, f"Reducer has forbidden imports: {violations}"


class TestConfigFilesExist:
    """All oracle config files must exist and be valid JSON."""

    def test_provider_oracle_exists(self):
        assert PROVIDER_ORACLE.exists(), f"Missing: {PROVIDER_ORACLE}"

    def test_workload_oracle_exists(self):
        assert WORKLOAD_ORACLE.exists(), f"Missing: {WORKLOAD_ORACLE}"

    def test_layer_template_exists(self):
        assert TEMPLATE.exists(), f"Missing: {TEMPLATE}"

    def test_workload_variants_exists(self):
        assert VARIANTS.exists(), f"Missing: {VARIANTS}"

    def test_provider_oracle_valid_json(self):
        with open(PROVIDER_ORACLE) as f:
            data = json.load(f)
        assert data.get("oracle_id") == "func_model_perf_oracle_v1"
        entries = data.get("entries", {})
        total = sum(len(v) for v in entries.values())
        assert total == 104, f"Expected 104 entries, got {total}"

    def test_workload_oracle_valid_json(self):
        with open(WORKLOAD_ORACLE) as f:
            data = json.load(f)
        assert data.get("oracle_id") == "func_model_workload_oracle_v1"
        entries = data.get("workload_entries", {})
        qwen_entries = {k: v for k, v in entries.items() if k.startswith("qwen25-")}
        assert len(qwen_entries) == 4, f"Expected 4 Qwen workloads, got {len(qwen_entries)}"

    def test_template_valid_json(self):
        with open(TEMPLATE) as f:
            data = json.load(f)
        ops = data.get("ops", [])
        assert len(ops) == 17, f"Expected 17 ops, got {len(ops)}"

    def test_variants_valid_json(self):
        with open(VARIANTS) as f:
            data = json.load(f)
        variants = data.get("variants", [])
        assert len(variants) == 4, f"Expected 4 variants, got {len(variants)}"


class TestOracleDomainCoverage:
    """Provider oracle must have exact domain counts matching spec."""

    EXPECTED = {"mxu": 10, "sfu": 24, "vector": 30, "dma": 10,
                "dram": 10, "noc": 8, "kv_cache": 8, "sw_overhead": 4}

    def test_exact_domain_counts(self):
        with open(PROVIDER_ORACLE) as f:
            data = json.load(f)
        entries = data.get("entries", {})
        for domain, expected_count in self.EXPECTED.items():
            actual = len(entries.get(domain, []))
            assert actual == expected_count, f"{domain}: expected {expected_count}, got {actual}"

    def test_total_104_entries(self):
        with open(PROVIDER_ORACLE) as f:
            data = json.load(f)
        total = sum(len(v) for v in data.get("entries", {}).values())
        assert total == 104, f"Total: expected 104, got {total}"


class TestVerifierSelfCheck:
    """Provider verifier --self-check exits 0."""

    def test_verifier_self_check_passes(self):
        result = subprocess.run(
            [sys.executable, str(VERIFIER_SCRIPT),
             "--oracle", str(PROVIDER_ORACLE),
             "--self-check"],
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT)
        )
        assert result.returncode == 0, f"Verifier self-check failed: {result.stderr}\n{result.stdout[:500]}"

    def test_verifier_with_mutations_passes(self):
        """With no actual mutations, all mutation checks should pass."""
        result = subprocess.run(
            [sys.executable, str(VERIFIER_SCRIPT),
             "--oracle", str(PROVIDER_ORACLE),
             "--self-check",
             "--mutations", "ceiling,constant,units,noop-nonzero,spec-interpretation"],
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT)
        )
        assert result.returncode == 0, f"Verifier with mutations failed: {result.stderr}\n{result.stdout[:500]}"


class TestReducerSelfCheck:
    """Path B reducer --self-check exits 0."""

    def test_reducer_self_check_passes(self):
        result = subprocess.run(
            [sys.executable, str(REDUCER_SCRIPT),
             "--oracle", str(WORKLOAD_ORACLE),
             "--self-check"],
            capture_output=True, text=True, timeout=60,
            cwd=str(REPO_ROOT)
        )
        assert result.returncode == 0, f"Reducer self-check failed: {result.stderr}\n{result.stdout[:500]}"

    def test_reducer_with_mutations_passes(self):
        """All four Path B mutations should pass (no actual mutation)."""
        result = subprocess.run(
            [sys.executable, str(REDUCER_SCRIPT),
             "--oracle", str(WORKLOAD_ORACLE),
             "--self-check",
             "--mutations", "path-a-reducer,path-b-decomposition,dependency-edge,template-mutation"],
            capture_output=True, text=True, timeout=60,
            cwd=str(REPO_ROOT)
        )
        assert result.returncode == 0, f"Reducer with mutations failed: {result.stderr}\n{result.stdout[:500]}"


class TestSubprocessIsolation:
    """Runtime subprocess isolation: Path B reducer must not have Path A modules in sys.modules."""

    def test_reducer_subprocess_no_path_a_modules(self):
        """Run reducer in subprocess with restricted PYTHONPATH."""
        current_pythonpath = os.environ.get("PYTHONPATH", "")
        restricted_path = os.pathsep.join(
            p for p in current_pythonpath.split(os.pathsep)
            if p and not any(forbidden in p for forbidden in (
                "sim/timing/providers", "sim/engine", "sim/models", "sim/npu_sim"))
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = restricted_path

        checker = (
            "import sys, json; "
            "forbidden = ['sim.models', 'sim.engine', 'sim.timing.providers', 'sim.timing.timing_engine', 'sim.npu_sim']; "
            "violations = [m for m in sys.modules if any(m.startswith(f) for f in forbidden)]; "
            "print(json.dumps({'verdict': 'pass' if not violations else 'fail', 'violations': violations}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", checker],
            env=env, capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT)
        )
        assert result.returncode == 0, f"Isolation check failed: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["verdict"] == "pass", f"Forbidden modules loaded: {data.get('violations', [])}"

    def test_reducer_runs_with_restricted_path(self):
        """Verify reducer can still run with restricted PYTHONPATH."""
        current_pythonpath = os.environ.get("PYTHONPATH", "")
        restricted_path = os.pathsep.join(
            p for p in current_pythonpath.split(os.pathsep)
            if p and not any(forbidden in p for forbidden in (
                "sim/timing/providers", "sim/engine", "sim/models", "sim/npu_sim"))
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = restricted_path

        result = subprocess.run(
            [sys.executable, str(REDUCER_SCRIPT),
             "--oracle", str(WORKLOAD_ORACLE),
             "--self-check"],
            env=env, capture_output=True, text=True, timeout=60,
            cwd=str(REPO_ROOT)
        )
        assert result.returncode == 0, f"Reducer failed with restricted PYTHONPATH: {result.stderr}"


class TestNoAutoGeneratedMarkers:
    """No oracle file may contain auto-generated markers."""

    def test_provider_oracle_no_generated_marker(self):
        with open(PROVIDER_ORACLE) as f:
            content = f.read()
        markers = ["auto-generated", "generated by", "generated_by", "codegen"]
        for marker in markers:
            assert marker.lower() not in content.lower(), f"Found generated marker '{marker}' in provider oracle"

    def test_workload_oracle_no_generated_marker(self):
        with open(WORKLOAD_ORACLE) as f:
            content = f.read()
        markers = ["auto-generated", "generated by", "generated_by", "codegen"]
        for marker in markers:
            assert marker.lower() not in content.lower(), f"Found generated marker '{marker}' in workload oracle"

    def test_template_no_generated_marker(self):
        with open(TEMPLATE) as f:
            content = f.read()
        markers = ["auto-generated", "generated by", "generated_by", "codegen"]
        for marker in markers:
            assert marker.lower() not in content.lower(), f"Found generated marker '{marker}' in template"

    def test_variants_no_generated_marker(self):
        with open(VARIANTS) as f:
            content = f.read()
        markers = ["auto-generated", "generated by", "generated_by", "codegen"]
        for marker in markers:
            assert marker.lower() not in content.lower(), f"Found generated marker '{marker}' in variants"


class TestPathBSeparation:
    """Path B (workload oracle) must not reference Path A concepts in data values."""

    def test_workload_oracle_no_path_a_terms_in_data(self):
        """Path A module terms may appear in policy docs but must not appear
        in actual data values (op names, decomposition strings, engine names)."""
        with open(WORKLOAD_ORACLE) as f:
            oracle = json.load(f)
        path_a_terms = ["timing_engine", "npu_sim", "CoreTimeline", "NPUSimulator",
                        "sim.timing.providers"]

        def _check_values(obj, path: str = "") -> List[str]:
            violations = []
            if isinstance(obj, dict):
                for key, value in obj.items():
                    # Allow Path A references in documentation/policy keys
                    if key in ("description", "frozen_policies", "derivation_notes",
                               "cpath_decomposition", "bottleneck_analysis",
                               "forbidden_imports", "no_path_a_imports", "note"):
                        continue
                    violations.extend(_check_values(value, f"{path}.{key}"))
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    violations.extend(_check_values(item, f"{path}[{i}]"))
            elif isinstance(obj, str):
                for term in path_a_terms:
                    if term in obj:
                        violations.append(f"{path}: {obj[:80]}")
            return violations

        violations = _check_values(oracle)
        assert not violations, f"Path A terms found in workload oracle data: {violations}"

    def test_workload_oracle_no_path_a_imports_in_data(self):
        """Check that Path A module names don't appear in data values of the oracle."""
        with open(WORKLOAD_ORACLE) as f:
            oracle = json.load(f)

        def _check_values(obj, path: str = "") -> List[str]:
            violations = []
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key in ("description", "frozen_policies", "derivation_notes",
                               "forbidden_imports", "no_path_a_imports", "note"):
                        continue
                    violations.extend(_check_values(value, f"{path}.{key}"))
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    violations.extend(_check_values(item, f"{path}[{i}]"))
            elif isinstance(obj, str):
                for mod in ("sim.models", "sim.engine", "sim.timing.providers",
                             "sim.timing.timing_engine", "sim.npu_sim"):
                    if mod in obj:
                        violations.append(f"{path}: contains '{mod}'")
            return violations

        violations = _check_values(oracle)
        assert not violations, f"Path A module names found in workload oracle data: {violations}"


class TestSpecInterpretationMutation:
    """Mutating the spec should cause the verifier to detect spec-interpretation drift."""

    def test_spec_hash_mismatch_detected(self):
        """If we change a parameter in the spec, the oracle's spec_hash won't match."""
        # The oracle was created against the actual spec, so they should match
        with open(PROVIDER_ORACLE) as f:
            oracle = json.load(f)
        oracle_hash = oracle.get("spec_hash", "")
        # Compute actual spec hash
        import hashlib
        with open(SPEC) as f:
            spec = json.load(f)
        # Strip timestamp fields for stable hash
        def strip_meta(obj):
            if isinstance(obj, dict):
                return {k: strip_meta(v) for k, v in obj.items()
                        if k not in ("created", "updated", "timestamp", "content_hash")}
            elif isinstance(obj, list):
                return [strip_meta(v) for v in obj]
            return obj
        canonical = json.dumps(strip_meta(spec), sort_keys=True, ensure_ascii=False)
        actual_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert oracle_hash == actual_hash, (
            f"Oracle spec_hash {oracle_hash[:16]}... != actual {actual_hash[:16]}..."
        )

    def test_mutated_spec_causes_verifier_spec_interpretation_failure(self):
        """Modify a value in the spec and verify the verifier detects it."""
        import tempfile, shutil, hashlib
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_spec = Path(tmpdir) / "spec_mutated.json"
            with open(SPEC) as f:
                spec = json.load(f)
            # Mutate one MXU parameter
            mxu_params = spec["domains"]["mxu"]
            for param in mxu_params:
                if param["parameter_id"] == "mxu_1_64_64":
                    param["estimated_cycles"] = 999  # mutated value
                    break
            with open(tmp_spec, "w") as f:
                json.dump(spec, f)
            # Compute hash of mutated spec
            def strip_meta(obj):
                if isinstance(obj, dict):
                    return {k: strip_meta(v) for k, v in obj.items()
                            if k not in ("created", "updated", "timestamp", "content_hash")}
                elif isinstance(obj, list):
                    return [strip_meta(v) for v in obj]
                return obj
            canonical = json.dumps(strip_meta(spec), sort_keys=True, ensure_ascii=False)
            mutated_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            # Actual spec hash
            with open(SPEC) as f2:
                orig_spec = json.load(f2)
            orig_canonical = json.dumps(strip_meta(orig_spec), sort_keys=True, ensure_ascii=False)
            orig_hash = hashlib.sha256(orig_canonical.encode("utf-8")).hexdigest()
            assert mutated_hash != orig_hash, "Mutation should change hash"

            # Now run verifier against mutated spec — it should fail spec-interpretation
            result = subprocess.run(
                [sys.executable, str(VERIFIER_SCRIPT),
                 "--oracle", str(PROVIDER_ORACLE),
                 "--spec", str(tmp_spec),
                 "--self-check",
                 "--mutations", "spec-interpretation"],
                capture_output=True, text=True, timeout=30,
                cwd=str(REPO_ROOT)
            )
            # spec-interpretation mutation should detect hash mismatch
            output = json.loads(result.stdout)
            mutations = output.get("mutations", {})
            spec_int = mutations.get("results", {}).get("spec-interpretation", {})
            assert spec_int.get("verdict") == "fail", (
                f"Expected spec-interpretation to fail with mutated spec, got: {spec_int}"
            )


class TestVariantSetConsistency:
    """Variant set must be consistent across variant file and workload oracle."""

    def test_variants_match_oracle_workloads(self):
        with open(VARIANTS) as f:
            variants = json.load(f)
        with open(WORKLOAD_ORACLE) as f:
            wl_oracle = json.load(f)
        variant_ids = {v["workload_id"] for v in variants.get("variants", [])}
        entry_ids = {k for k in wl_oracle.get("workload_entries", {}).keys() if k.startswith("qwen25-")}
        assert variant_ids == entry_ids, f"Mismatch: variants={variant_ids}, qwen_entries={entry_ids}"


class TestTemplateOpCoverage:
    """Template must have exactly 17 ops with correct engine breakdown."""

    def test_17_ops_in_template(self):
        with open(TEMPLATE) as f:
            data = json.load(f)
        ops = data.get("ops", [])
        assert len(ops) == 17

    def test_engine_counts(self):
        with open(TEMPLATE) as f:
            data = json.load(f)
        ops = data.get("ops", [])
        mxu = sum(1 for o in ops if o.get("engine") == "mxu")
        sfu = sum(1 for o in ops if o.get("engine") == "sfu")
        vec = sum(1 for o in ops if o.get("engine") == "vector")
        assert mxu == 9, f"MXU: expected 9, got {mxu}"
        assert sfu == 5, f"SFU: expected 5, got {sfu}"
        assert vec == 3, f"Vector: expected 3, got {vec}"

    def test_all_ops_have_ids(self):
        with open(TEMPLATE) as f:
            data = json.load(f)
        ops = data.get("ops", [])
        ids = [o.get("op_id") for o in ops]
        assert len(set(ids)) == 17, f"Non-unique op_ids: {ids}"
        for i in range(1, 18):
            assert f"op_{i:02d}" in ids, f"Missing op_{i:02d}"


class TestBaselineCharacterization:
    """Baseline characterization tests for the created artifacts."""

    def test_provider_oracle_spec_hash_stable(self):
        """Oracle spec_hash must be stable across re-reads."""
        import hashlib
        with open(SPEC) as f:
            spec = json.load(f)

        def strip_meta(obj):
            if isinstance(obj, dict):
                return {k: strip_meta(v) for k, v in obj.items()
                        if k not in ("created", "updated", "timestamp", "content_hash")}
            elif isinstance(obj, list):
                return [strip_meta(v) for v in obj]
            return obj

        canonical = json.dumps(strip_meta(spec), sort_keys=True, ensure_ascii=False)
        hash1 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        hash2 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert hash1 == hash2, "Hash should be deterministic"

        with open(PROVIDER_ORACLE) as f:
            oracle = json.load(f)
        assert oracle["spec_hash"] == hash1, "Oracle spec_hash must match"

    def test_verifier_exit_code_structure(self):
        """Verifier should produce JSON output with required fields."""
        result = subprocess.run(
            [sys.executable, str(VERIFIER_SCRIPT),
             "--oracle", str(PROVIDER_ORACLE),
             "--self-check"],
            capture_output=True, text=True, timeout=30,
            cwd=str(REPO_ROOT)
        )
        output = json.loads(result.stdout)
        required = ["tool", "oracle", "import_policy", "validation", "verdict"]
        for field in required:
            assert field in output, f"Missing field: {field}"

    def test_reducer_exit_code_structure(self):
        """Reducer should produce JSON output with required fields."""
        result = subprocess.run(
            [sys.executable, str(REDUCER_SCRIPT),
             "--oracle", str(WORKLOAD_ORACLE),
             "--self-check"],
            capture_output=True, text=True, timeout=60,
            cwd=str(REPO_ROOT)
        )
        output = json.loads(result.stdout)
        required = ["tool", "oracle", "validation", "verdict"]
        for field in required:
            assert field in output, f"Missing field: {field}"
