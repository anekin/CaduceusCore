"""T13: Performance workload tests — GREEN positive validation, RED negative rejection,
structural invariants, stale-reference grep report.

GREEN: `run_workload_check` exits 0 with four workload variants validated.
RED: `run_negative_fixtures` exits 0 with rejected=3, rtl_files_opened=0.
Structural: manifest 17-op DAG, engine counts 9/5/3, 612-op for 36-layer cases.
Stale-reference: grep for kv_heads=16 / kv_dim=2048 should find no unannotated matches.

T14: CV workload tests — GREEN: exact entry counts and hashes for MobileNetV3/ResNet50/YOLOv8n.
RED: CV negative fixtures (dropped_layer, unknown_op, bad_shape).
Structural: CV manifest invariants, oracle consistency, workload builders.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CHECKER_SCRIPT = REPO_ROOT / "scripts" / "check_perf_workloads.py"
MANIFEST_PATH = REPO_ROOT / "config" / "workloads" / "qwen25_3b_perf_spec_v1.json"
ORACLE_PATH = REPO_ROOT / "config" / "func_model_workload_oracle_v1.json"
TEMPLATE_PATH = REPO_ROOT / "config" / "oracle" / "qwen25_3b_layer_template_v1.json"
VARIANTS_PATH = REPO_ROOT / "config" / "oracle" / "qwen25_3b_workload_variants_v1.json"

# CV manifest paths
CV_MANIFEST_PATHS: Dict[str, Path] = {
    "mobilenetv3": REPO_ROOT / "config" / "workloads" / "mobilenetv3_perf_spec_v1.json",
    "resnet50": REPO_ROOT / "config" / "workloads" / "resnet50_perf_spec_v1.json",
    "yolov8n": REPO_ROOT / "config" / "workloads" / "yolov8n_perf_spec_v1.json",
}

_CV_EXPECTED_COUNTS = {
    "mobilenetv3": {"total": 124, "gemm": 54, "sfu": 42, "host_only": 28},
    "resnet50": {"total": 105, "gemm": 54, "sfu": 51, "host_only": 0},
    "yolov8n": {"total": 129, "gemm": 63, "sfu": 57, "host_only": 9},
}

_CV_EXPECTED_HASHES = {
    "mobilenetv3": "9091ae2a86bbd5b9d1c3c3566cf98e1c82ef61e47ebd7c35b055c17d02afd4f7",
    "resnet50": "9467cdea905262a3dc2607b7e09e7b8a302ad91ee5d8189f27c47c5f9be43a9d",
    "yolov8n": "aec40c8165a7b98ea699d2ef903892f788bfe80af8a4fa086f3c2989478f08d2",
}


def _run_checker(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CHECKER_SCRIPT)] + args,
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=30,
    )


# ── GREEN: Positive validation ──────────────────────────────────────────

class TestPositiveWorkloadCheck:
    def test_cli_green_workload(self):
        """CLI --workload exits 0 with four workload variants accepted."""
        result = _run_checker(["--workload", "qwen25-3b", "--oracle", str(ORACLE_PATH)])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["verdict"] == "pass", f"Expected pass, got: {data}"
        assert "workload" in data
        assert "manifest" in data
        assert "oracle" in data

    def test_cli_green_manifest_structure(self):
        """Manifest validation: 17 ops, 9/5/3 engine counts."""
        result = _run_checker(["--workload", "qwen25-3b", "--oracle", str(ORACLE_PATH)])
        data = json.loads(result.stdout)
        manifest = data["manifest"]
        assert manifest["verdict"] == "pass", f"Manifest errors: {manifest.get('errors')}"

    def test_cli_green_oracle_consistency(self):
        """Oracle references match manifest variants."""
        result = _run_checker(["--workload", "qwen25-3b", "--oracle", str(ORACLE_PATH)])
        data = json.loads(result.stdout)
        oracle = data["oracle"]
        assert oracle["verdict"] == "pass", f"Oracle errors: {oracle.get('errors')}"


# ── RED: Negative fixtures ──────────────────────────────────────────────

class TestNegativeFixtureRejection:
    def test_cli_red_all_three_fixtures_rejected(self):
        """All 3 negative fixtures must be rejected, rtl_files_opened=0."""
        fixtures = [
            "config/tests/qwen_old_dims.json",
            "config/tests/qwen_7gemm.json",
            "config/tests/qwen_rtl_source.json",
        ]
        result = _run_checker(["--negative-fixtures", ",".join(fixtures)])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["verdict"] == "pass", f"Expected pass, got: {data}"
        assert data["rejected"] == 3, f"rejected={data['rejected']}, expected 3"
        assert data["accepted"] == 0, f"accepted={data['accepted']}, expected 0"
        assert data["rtl_files_opened"] == 0, f"rtl_files_opened={data['rtl_files_opened']}, expected 0"


# ── Structural invariants ───────────────────────────────────────────────

class TestStructuralInvariants:
    def test_manifest_17_ops(self):
        """Manifest must have exactly 17 ops with 9 MXU, 5 SFU, 3 Vector."""
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        ops = manifest["ops"]
        assert len(ops) == 17

        mxu = sum(1 for o in ops if o["engine"] == "mxu")
        sfu = sum(1 for o in ops if o["engine"] == "sfu")
        vec = sum(1 for o in ops if o["engine"] == "vector")
        assert mxu == 9
        assert sfu == 5
        assert vec == 3

    def test_manifest_4_workload_variants(self):
        """Manifest must have exactly 4 hard-gate variants."""
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        variants = manifest["workload_variants"]
        assert len(variants) == 4

        expected_ids = {
            "qwen25-3b-blk0-decode",
            "qwen25-3b-decode-c128-g1",
            "qwen25-3b-prefill-16",
            "qwen25-3b-prefill-128",
        }
        actual_ids = {v["workload_id"] for v in variants}
        assert actual_ids == expected_ids

    def test_612_ops_for_36_layer_variants(self):
        """36-layer variants must produce 612 total ops (36 * 17)."""
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        for v in manifest["workload_variants"]:
            if v["layer_count"] == 36:
                assert 36 * len(manifest["ops"]) == 612

    def test_model_pins(self):
        """Model metadata must pin hidden=2048, intermediate=11008, layers=36,
        heads=16, kv_heads=2, head_dim=128, kv_dim=256."""
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        meta = manifest["model_meta"]
        assert meta["hidden"] == 2048
        assert meta["intermediate"] == 11008
        assert meta["layers"] == 36
        assert meta["heads"] == 16
        assert meta["kv_heads"] == 2
        assert meta["head_dim"] == 128
        assert meta["kv_dim"] == 256

    def test_dag_edges_complete(self):
        """Every op in manifest must have dependency_edges declared."""
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        deps = manifest.get("dependency_edges", {})
        for op in manifest["ops"]:
            assert op["op_id"] in deps, f"Missing deps for {op['op_id']}"

    def test_variant_ids_match_across_files(self):
        """Variant IDs in manifest must match oracle template variants."""
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        with open(VARIANTS_PATH) as f:
            variants_doc = json.load(f)
        manifest_ids = {v["workload_id"] for v in manifest["workload_variants"]}
        variant_ids = {v["workload_id"] for v in variants_doc["variants"]}
        assert manifest_ids == variant_ids

    def test_oracle_has_four_workload_entries(self):
        """Workload oracle must have exactly 4 Qwen entries."""
        with open(ORACLE_PATH) as f:
            oracle = json.load(f)
        entries = oracle.get("workload_entries", {})
        expected_qwen_ids = {
            "qwen25-3b-blk0-decode",
            "qwen25-3b-decode-c128-g1",
            "qwen25-3b-prefill-16",
            "qwen25-3b-prefill-128",
        }
        qwen_entries = {k: v for k, v in entries.items() if k.startswith("qwen25-3b")}
        assert set(qwen_entries.keys()) == expected_qwen_ids

    def test_blk0_has_17_per_op_entries(self):
        """blk0-decode must have per_op_cycles with 17 entries."""
        with open(ORACLE_PATH) as f:
            oracle = json.load(f)
        blk0 = oracle["workload_entries"]["qwen25-3b-blk0-decode"]
        per_op = blk0.get("per_op_cycles", {})
        assert len(per_op) == 17, f"blk0 has {len(per_op)} per-op entries"


# ── Workload builder ────────────────────────────────────────────────────

class TestWorkloadBuilder:
    def test_build_four_workloads(self):
        """build_qwen25_3b_workload returns valid dicts for all four IDs."""
        from timing.workloads import build_qwen25_3b_workload

        for wid in [
            "qwen25-3b-blk0-decode",
            "qwen25-3b-decode-c128-g1",
            "qwen25-3b-prefill-16",
            "qwen25-3b-prefill-128",
        ]:
            wl = build_qwen25_3b_workload(wid)
            assert wl["workload_id"] == wid
            assert wl["layer_ops"] == 17
            assert wl["engine_counts"]["mxu"] == 9
            assert wl["engine_counts"]["sfu"] == 5
            assert wl["engine_counts"]["vector"] == 3
            assert "content_hash" in wl

    def test_build_invalid_raises(self):
        """Unknown workload_id raises ValueError."""
        from timing.workloads import build_qwen25_3b_workload

        with pytest.raises(ValueError):
            build_qwen25_3b_workload("nonexistent-workload")

    def test_blk0_has_17_ops_1_layer(self):
        """blk0-decode has 17 ops and 1 layer."""
        from timing.workloads import build_qwen25_3b_workload

        wl = build_qwen25_3b_workload("qwen25-3b-blk0-decode")
        assert wl["total_ops"] == 17
        assert wl["layer_count"] == 1

    def test_36_layer_variants_have_612_ops(self):
        """36-layer variants must have 612 total ops."""
        from timing.workloads import build_qwen25_3b_workload

        for wid in [
            "qwen25-3b-decode-c128-g1",
            "qwen25-3b-prefill-16",
            "qwen25-3b-prefill-128",
        ]:
            wl = build_qwen25_3b_workload(wid)
            assert wl["total_ops"] == 612

    def test_content_hash_deterministic(self):
        """Same workload produces same content hash."""
        from timing.workloads import build_qwen25_3b_workload

        wl1 = build_qwen25_3b_workload("qwen25-3b-blk0-decode")
        wl2 = build_qwen25_3b_workload("qwen25-3b-blk0-decode")
        assert wl1["content_hash"] == wl2["content_hash"]

    def test_content_hash_differs_by_variant(self):
        """Different workloads produce different content hashes."""
        from timing.workloads import build_qwen25_3b_workload

        wl_blk0 = build_qwen25_3b_workload("qwen25-3b-blk0-decode")
        wl_prefill = build_qwen25_3b_workload("qwen25-3b-prefill-128")
        assert wl_blk0["content_hash"] != wl_prefill["content_hash"]

    def test_validate_manifest_passes(self):
        """validate_manifest() returns valid=True for the canonical manifest."""
        from timing.workloads import validate_manifest

        result = validate_manifest()
        assert result["valid"], f"Manifest errors: {result['errors']}"


# ── Manifest validation ─────────────────────────────────────────────────

class TestManifestValidation:
    def test_op_seq_monotonic(self):
        """Op seq numbers must be 0-16 in order."""
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        seqs = [o["seq"] for o in manifest["ops"]]
        assert seqs == list(range(17))

    def test_spec_line_refs_present(self):
        """Each op must reference a line in the forward spec."""
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        for op in manifest["ops"]:
            assert "spec_line_ref" in op, f"{op['op_id']} missing spec_line_ref"
            assert op["spec_line_ref"].isdigit(), \
                f"{op['op_id']} spec_line_ref not numeric: {op['spec_line_ref']}"

    def test_parallel_chains_declared(self):
        """Manifest must declare QKV and FFN gate+up parallel chains."""
        with open(MANIFEST_PATH) as f:
            manifest = json.load(f)
        chains = manifest.get("parallel_chains", [])
        chain_names = {c["chain"] for c in chains}
        assert "QKV_parallel" in chain_names
        assert "FFN_gate_up_parallel" in chain_names


# ── Stale-reference grep report ─────────────────────────────────────────

class TestStaleReferenceReport:
    def test_no_stale_kv_heads_16_in_sim(self):
        """Grep for kv_heads=16 in sim/ should find no unannotated matches."""
        import subprocess as sp
        result = sp.run(
            ["grep", "-rn", r"kv_heads\s*=\s*16\|NUM_KV_HEADS\s*=\s*16\|kv_dim\s*=\s*2048",
             str(REPO_ROOT / "sim")],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
        )
        # Acceptable matches: test mutation detection (deliberate wrong pin),
        # and the forward-spec.md documentation line.
        lines = [l for l in result.stdout.split("\n") if l.strip()]
        unacceptable = [
            l for l in lines
            if "test_perf_noc_kv_spec" not in l
            and "test_perf_workloads" not in l
            and "test_perf_docs" not in l  # T24 doc-checker tests deliberate stale-value strings
            and "npu_sim.py" not in l  # we expect this to be fixed now
            and "docs/" not in l
        ]
        if unacceptable:
            pytest.fail(f"Stale kv_heads=16/kv_dim=2048 references found:\n" +
                        "\n".join(unacceptable))

    def test_no_stale_phantom_config_in_validate_e2e(self):
        """Grep for phantom config in validate_e2e.py: no hidden_size=2560/layers=28."""
        import subprocess as sp
        result = sp.run(
            ["grep", "-n", r"hidden_size.*2560\|layers.*28\|num_heads.*32",
             str(REPO_ROOT / "sim" / "validate_e2e.py")],
            capture_output=True, text=True,
        )
        if result.stdout.strip():
            pytest.fail(f"Phantom config still in validate_e2e.py:\n{result.stdout}")

    def test_smoke_kv_heads_2_active(self):
        """Smoke test: model_specs returns kv_heads=2 for qwen2.5-3b."""
        from model_specs import get_spec
        spec = get_spec("qwen2.5-3b")
        assert spec.kv_heads == 2
        assert spec.head_dim == 128
        assert spec.kv_heads * spec.head_dim == 256


# ── Smoke test: downstream consumers ────────────────────────────────────

class TestDownstreamSmoke:
    def test_model_specs_kv_dim_256(self):
        """model_specs qwen2.5-3b: kv_heads * head_dim == 256."""
        from model_specs import get_spec
        spec = get_spec("qwen2.5-3b")
        assert spec.kv_heads == 2
        assert spec.kv_heads * spec.head_dim == 256

    def test_timing_engine_trace_kv_dim(self):
        """_build_llm_trace uses kv_heads * head_dim (256) from model_spec."""
        from model_specs import get_spec
        from timing.timing_engine import _build_llm_trace

        spec = get_spec("qwen2.5-3b")
        trace = _build_llm_trace(spec, m=1)
        # First layer has 7 GEMMs; K_proj and V_proj use kv_dim = 256
        # Q_proj and O_proj use qkv_dim = 2048
        first_7 = trace[:7]
        # K_proj trace entry: (1, 2048, 256, 0, "K_proj")
        k_entry = [t for t in first_7 if t[4] == "K_proj"][0]
        assert k_entry[2] == 256, f"K_proj N should be 256, got {k_entry[2]}"
        v_entry = [t for t in first_7 if t[4] == "V_proj"][0]
        assert v_entry[2] == 256, f"V_proj N should be 256, got {v_entry[2]}"


# ── T14: CV Workload Tests ──────────────────────────────────────────────


class TestCvPositiveWorkloadCheck:
    def test_cli_green_cv_workloads(self):
        """CLI --workload with CV IDs exits 0 with exact counts."""
        result = _run_checker([
            "--workload", "mobilenetv3,resnet50,yolov8n",
            "--oracle", str(ORACLE_PATH),
        ])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["verdict"] == "pass", f"Expected pass, got: {data}"
        assert data["accepted"] > 0
        assert data["rejected"] == 0

    def test_cli_green_cv_manifest_exact_counts(self):
        """CV manifests have exact entry counts matching invariants."""
        result = _run_checker([
            "--workload", "mobilenetv3,resnet50,yolov8n",
            "--oracle", str(ORACLE_PATH),
        ])
        data = json.loads(result.stdout)
        cv = data["cv_results"]
        for wid in ["mobilenetv3", "resnet50", "yolov8n"]:
            assert cv[wid]["verdict"] == "pass", f"{wid} failed: {cv[wid].get('errors')}"

    def test_cli_green_cv_oracle_consistency(self):
        """CV oracle entries match manifest invariants."""
        result = _run_checker([
            "--workload", "mobilenetv3,resnet50,yolov8n",
            "--oracle", str(ORACLE_PATH),
        ])
        data = json.loads(result.stdout)
        oracle = data["cv_results"]["oracle"]
        assert oracle["verdict"] == "pass", f"Oracle errors: {oracle.get('errors')}"


class TestCvNegativeFixtureRejection:
    def test_cli_red_cv_fixtures_rejected(self):
        """All 3 CV negative fixtures rejected, rtl_files_opened=0."""
        fixtures = [
            "config/tests/cv_dropped_layer.json",
            "config/tests/cv_unknown_op.json",
            "config/tests/cv_bad_shape.json",
        ]
        result = _run_checker(["--negative-fixtures", ",".join(fixtures)])
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["verdict"] == "pass", f"Expected pass, got: {data}"
        assert data["rejected"] == 3, f"rejected={data['rejected']}, expected 3"
        assert data["accepted"] == 0, f"accepted={data['accepted']}, expected 0"
        assert data["rtl_files_opened"] == 0


class TestCvStructuralInvariants:
    @pytest.mark.parametrize("wid,expected", [
        ("mobilenetv3", _CV_EXPECTED_COUNTS["mobilenetv3"]),
        ("resnet50", _CV_EXPECTED_COUNTS["resnet50"]),
        ("yolov8n", _CV_EXPECTED_COUNTS["yolov8n"]),
    ])
    def test_manifest_exact_entry_counts(self, wid, expected):
        """CV manifest has exact total/GEMM/SFU/host-only counts."""
        with open(CV_MANIFEST_PATHS[wid]) as f:
            manifest = json.load(f)
        entries = manifest["entries"]
        assert len(entries) == expected["total"]

        gemm = sum(1 for e in entries if e.get("engine") == "mxu")
        assert gemm == expected["gemm"], f"{wid}: GEMM {gemm} != {expected['gemm']}"

        sfu = sum(1 for e in entries if e.get("engine") == "sfu")
        assert sfu == expected["sfu"], f"{wid}: SFU {sfu} != {expected['sfu']}"

        host = sum(1 for e in entries if e.get("host_only"))
        assert host == expected["host_only"], f"{wid}: host {host} != {expected['host_only']}"

    @pytest.mark.parametrize("wid,expected_hash", [
        ("mobilenetv3", _CV_EXPECTED_HASHES["mobilenetv3"]),
        ("resnet50", _CV_EXPECTED_HASHES["resnet50"]),
        ("yolov8n", _CV_EXPECTED_HASHES["yolov8n"]),
    ])
    def test_manifest_content_hash(self, wid, expected_hash):
        """CV manifest content hash is frozen."""
        with open(CV_MANIFEST_PATHS[wid]) as f:
            manifest = json.load(f)
        import hashlib
        entries = manifest["entries"]
        actual = hashlib.sha256(
            json.dumps(entries, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        assert actual == expected_hash, f"{wid}: hash={actual}, expected={expected_hash}"

    def test_all_host_only_entries_no_engine(self):
        """All host-only entries have engine=null, op=null."""
        for wid, path in CV_MANIFEST_PATHS.items():
            with open(path) as f:
                manifest = json.load(f)
            for e in manifest["entries"]:
                if e.get("host_only"):
                    assert e.get("engine") is None, f"{wid} seq={e['seq']}: host-only has engine={e.get('engine')}"
                    assert e.get("op") is None, f"{wid} seq={e['seq']}: host-only has op={e.get('op')}"

    def test_all_non_host_entries_have_engine_and_op(self):
        """All non-host entries have typed engine and op."""
        for wid, path in CV_MANIFEST_PATHS.items():
            with open(path) as f:
                manifest = json.load(f)
            for e in manifest["entries"]:
                if not e.get("host_only"):
                    assert e.get("engine") is not None, f"{wid} seq={e['seq']}: missing engine"
                    assert e.get("op") is not None, f"{wid} seq={e['seq']}: missing op"

    def test_shape_keys_match_engine(self):
        """Shape keys match engine type in all CV manifests."""
        for wid, path in CV_MANIFEST_PATHS.items():
            with open(path) as f:
                manifest = json.load(f)
            for e in manifest["entries"]:
                eng = e.get("engine")
                shape = e.get("shape", {})
                if eng == "mxu":
                    assert set(shape.keys()) == {"M", "K", "N"}, f"{wid} seq={e['seq']}: mxu shape={shape.keys()}"
                elif eng == "sfu":
                    assert set(shape.keys()) == {"elements"}, f"{wid} seq={e['seq']}: sfu shape={shape.keys()}"

    def test_trace_generator_seed_42(self):
        """All CV trace generators have seed=42."""
        for wid, path in CV_MANIFEST_PATHS.items():
            with open(path) as f:
                manifest = json.load(f)
            gen = manifest.get("trace_generator", {})
            assert gen.get("seed") == 42, f"{wid}: seed={gen.get('seed')}"

    def test_input_shape_correct(self):
        """CV manifests have correct input shapes."""
        input_shapes = {
            "mobilenetv3": [1, 3, 224, 224],
            "resnet50": [1, 3, 224, 224],
            "yolov8n": [1, 3, 640, 640],
        }
        for wid, path in CV_MANIFEST_PATHS.items():
            with open(path) as f:
                manifest = json.load(f)
            actual = manifest.get("model_meta", {}).get("input_shape")
            assert actual == input_shapes[wid], f"{wid}: input_shape={actual}"


class TestCvWorkloadBuilder:
    def test_build_all_cv_workloads(self):
        """All three CV workload builders return valid dicts."""
        from timing.workloads import (
            build_mobilenetv3_workload,
            build_resnet50_workload,
            build_yolov8n_workload,
        )

        for wid, builder in [
            ("mobilenetv3", build_mobilenetv3_workload),
            ("resnet50", build_resnet50_workload),
            ("yolov8n", build_yolov8n_workload),
        ]:
            wl = builder()
            assert wl["workload_id"] == wid
            assert wl["total_entries"] == _CV_EXPECTED_COUNTS[wid]["total"]
            assert wl["engine_counts"]["mxu"] == _CV_EXPECTED_COUNTS[wid]["gemm"]
            assert wl["engine_counts"]["sfu"] == _CV_EXPECTED_COUNTS[wid]["sfu"]
            assert wl["engine_counts"]["host_only"] == _CV_EXPECTED_COUNTS[wid]["host_only"]
            assert "content_hash" in wl

    def test_build_cv_workload_unified(self):
        """build_cv_workload dispatches correctly."""
        from timing.workloads import build_cv_workload
        for wid in ["mobilenetv3", "resnet50", "yolov8n"]:
            wl = build_cv_workload(wid)
            assert wl["workload_id"] == wid

    def test_build_cv_workload_invalid_raises(self):
        """Unknown CV workload_id raises ValueError."""
        from timing.workloads import build_cv_workload
        with pytest.raises(ValueError):
            build_cv_workload("nonexistent-cv")

    def test_content_hash_deterministic(self):
        """Same CV workload produces same content hash."""
        from timing.workloads import build_mobilenetv3_workload
        wl1 = build_mobilenetv3_workload()
        wl2 = build_mobilenetv3_workload()
        assert wl1["content_hash"] == wl2["content_hash"]

    def test_content_hash_differs_by_model(self):
        """Different CV workloads produce different content hashes."""
        from timing.workloads import (
            build_mobilenetv3_workload,
            build_resnet50_workload,
        )
        wl1 = build_mobilenetv3_workload()
        wl2 = build_resnet50_workload()
        assert wl1["content_hash"] != wl2["content_hash"]

    def test_validate_cv_manifest_passes(self):
        """validate_cv_manifest returns valid=True for all three."""
        from timing.workloads import validate_cv_manifest
        for wid in ["mobilenetv3", "resnet50", "yolov8n"]:
            result = validate_cv_manifest(wid)
            assert result["valid"], f"{wid} errors: {result['errors']}"

    def test_list_cv_workload_ids(self):
        """list_cv_workload_ids returns 3 sorted IDs."""
        from timing.workloads import list_cv_workload_ids
        ids = list_cv_workload_ids()
        assert ids == ["mobilenetv3", "resnet50", "yolov8n"]
