#!/usr/bin/env python3
"""Performance workload checker — validates Qwen2.5-3B and CV workload definitions.

Usage:
    python3 scripts/check_perf_workloads.py --workload qwen25-3b --oracle config/func_model_workload_oracle_v1.json
    python3 scripts/check_perf_workloads.py --workload mobilenetv3,resnet50,yolov8n --oracle config/func_model_workload_oracle_v1.json
    python3 scripts/check_perf_workloads.py --negative-fixtures config/tests/cv_dropped_layer.json,config/tests/cv_unknown_op.json,config/tests/cv_bad_shape.json

Behaviours:
    - --workload: loads canonical manifest and validates invariants (Qwen: 17-op DAG, 612-op
      for 36-layer; CV: exact entry counts, GEMM/SFU/host-only classification).
    - --negative-fixtures: loads fixture JSONs; each must be rejected. Rejects any
      declared source path under rtl/** before file access (rtl_files_opened=0).
    - Produces structured JSON verdict only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]

_MANIFEST_PATH = (
    _REPO_ROOT / "config" / "workloads" / "qwen25_3b_perf_spec_v1.json"
)

_CV_MANIFEST_PATHS: Dict[str, Path] = {
    "mobilenetv3": _REPO_ROOT / "config" / "workloads" / "mobilenetv3_perf_spec_v1.json",
    "resnet50": _REPO_ROOT / "config" / "workloads" / "resnet50_perf_spec_v1.json",
    "yolov8n": _REPO_ROOT / "config" / "workloads" / "yolov8n_perf_spec_v1.json",
}

_VALID_WORKLOAD_IDS = frozenset({
    "qwen25-3b-blk0-decode",
    "qwen25-3b-decode-c128-g1",
    "qwen25-3b-prefill-16",
    "qwen25-3b-prefill-128",
})

_CV_VALID_WORKLOAD_IDS = frozenset({"mobilenetv3", "resnet50", "yolov8n"})

_CV_EXPECTED_COUNTS: Dict[str, Dict[str, int]] = {
    "mobilenetv3": {"total": 124, "gemm": 54, "sfu": 42, "host_only": 28},
    "resnet50": {"total": 105, "gemm": 54, "sfu": 51, "host_only": 0},
    "yolov8n": {"total": 129, "gemm": 63, "sfu": 57, "host_only": 9},
}

_CV_EXPECTED_HASHES: Dict[str, str] = {
    "mobilenetv3": "9091ae2a86bbd5b9d1c3c3566cf98e1c82ef61e47ebd7c35b055c17d02afd4f7",
    "resnet50": "9467cdea905262a3dc2607b7e09e7b8a302ad91ee5d8189f27c47c5f9be43a9d",
    "yolov8n": "aec40c8165a7b98ea699d2ef903892f788bfe80af8a4fa086f3c2989478f08d2",
}

_RTL_PATH_RE = re.compile(r"^rtl[/\\]")


def _reject_rtl_path(path: str) -> None:
    """Reject any path under rtl/** before file access."""
    if _RTL_PATH_RE.search(path):
        raise PermissionError(
            f"RTL source path rejected before file access: {path}"
        )


def _load_json_safe(path: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Load JSON safely, rejecting RTL paths. Returns (data, error)."""
    _reject_rtl_path(path)
    try:
        with open(path, "r") as f:
            return json.load(f), None
    except PermissionError:
        raise
    except Exception as e:
        return None, f"Cannot load {path}: {e}"


def _validate_workload_manifest() -> Dict[str, Any]:
    """Validate the 17-op manifest against its declared invariants."""
    errors: List[str] = []
    warnings: List[str] = []
    accepted: int = 0
    rtl_files_opened: int = 0

    manifest, load_err = _load_json_safe(str(_MANIFEST_PATH))
    if load_err:
        return {"verdict": "fail", "errors": [load_err], "accepted": 0,
                "rejected": 1, "rtl_files_opened": rtl_files_opened}

    ops = manifest.get("ops", [])
    variants = manifest.get("workload_variants", [])
    meta = manifest.get("model_meta", {})

    # 1. 17-op layer DAG
    if len(ops) != 17:
        errors.append(f"Manifest has {len(ops)} ops, expected 17")
    else:
        accepted += 1

    mxu = sum(1 for o in ops if o.get("engine") == "mxu")
    sfu = sum(1 for o in ops if o.get("engine") == "sfu")
    vec = sum(1 for o in ops if o.get("engine") == "vector")
    if mxu != 9 or sfu != 5 or vec != 3:
        errors.append(f"Engine counts mxu={mxu} sfu={sfu} vec={vec}, expected 9/5/3")
    else:
        accepted += 1

    # 2. Model metadata pins
    if meta.get("hidden") != 2048:
        errors.append(f"model_meta.hidden={meta.get('hidden')} != 2048")
    else:
        accepted += 1
    if meta.get("intermediate") != 11008:
        errors.append(f"model_meta.intermediate={meta.get('intermediate')} != 11008")
    else:
        accepted += 1
    if meta.get("layers") != 36:
        errors.append(f"model_meta.layers={meta.get('layers')} != 36")
    else:
        accepted += 1
    if meta.get("heads") != 16:
        errors.append(f"model_meta.heads={meta.get('heads')} != 16")
    else:
        accepted += 1
    if meta.get("kv_heads") != 2:
        errors.append(f"model_meta.kv_heads={meta.get('kv_heads')} != 2")
    else:
        accepted += 1
    if meta.get("head_dim") != 128:
        errors.append(f"model_meta.head_dim={meta.get('head_dim')} != 128")
    else:
        accepted += 1
    if meta.get("kv_dim") != 256:
        errors.append(f"model_meta.kv_dim={meta.get('kv_dim')} != 256")
    else:
        accepted += 1

    # 3. 4 hard-gate variant IDs
    if len(variants) != 4:
        errors.append(f"Manifest has {len(variants)} variants, expected 4")
    else:
        variant_ids = {v["workload_id"] for v in variants}
        if variant_ids != _VALID_WORKLOAD_IDS:
            errors.append(f"Variant IDs mismatch: {variant_ids} != {_VALID_WORKLOAD_IDS}")
        else:
            accepted += 1

    # 4. 612-op for 36-layer cases
    for v in variants:
        lc = v.get("layer_count", 1)
        if lc == 36:
            total = 36 * len(ops)
            if total != 612:
                errors.append(f"Variant {v['workload_id']}: 36*{len(ops)}={total} != 612")
            else:
                accepted += 1
        elif lc == 1:
            if len(ops) != 17:
                errors.append(f"Variant {v['workload_id']}: 1-layer should have 17 ops")
            else:
                accepted += 1

    # 5. DAG edges integrity
    deps = manifest.get("dependency_edges", {})
    for op in ops:
        op_id = op["op_id"]
        if op_id not in deps:
            errors.append(f"op {op_id} missing from dependency_edges")
    accepted += 1  # DAG structure check

    # 6. Op seq numbering (0-16)
    seqs = [o.get("seq", -1) for o in ops]
    if seqs != list(range(17)):
        errors.append(f"Op seqs not monotonic 0-16: {seqs}")
    else:
        accepted += 1

    # 7. Shape formulas present
    missing_formula = [o["op_id"] for o in ops if not o.get("shape_formula")]
    if missing_formula:
        errors.append(f"Ops missing shape_formula: {missing_formula}")
    else:
        accepted += 1

    rejected = len(errors)
    verdict = "pass" if rejected == 0 else "fail"

    return {
        "verdict": verdict,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "warnings": warnings,
        "rtl_files_opened": rtl_files_opened,
    }


def _validate_oracle_consistency(oracle_path: str) -> Dict[str, Any]:
    """Validate that the workload oracle references match the manifest."""
    errors: List[str] = []
    warnings: List[str] = []
    accepted: int = 0
    rtl_files_opened: int = 0

    oracle, load_err = _load_json_safe(oracle_path)
    if load_err:
        return {"verdict": "fail", "errors": [load_err], "accepted": 0,
                "rejected": 1, "rtl_files_opened": rtl_files_opened}

    entries = oracle.get("workload_entries", {})

    # Check Qwen workload ID subset is present and complete
    qwen_entries = {k: v for k, v in entries.items() if k.startswith("qwen25-3b")}
    if set(qwen_entries.keys()) != _VALID_WORKLOAD_IDS:
        errors.append(
            f"Oracle Qwen workload IDs {set(qwen_entries.keys())} != {_VALID_WORKLOAD_IDS}"
        )
    else:
        accepted += 1

    # Check blk0 has per-op decomposition
    blk0 = entries.get("qwen25-3b-blk0-decode", {})
    per_op = blk0.get("per_op_cycles", {})
    if len(per_op) != 17:
        errors.append(f"blk0 per_op_cycles has {len(per_op)} entries, expected 17")
    else:
        accepted += 1

    # Check other workloads have total_serial_cpath > 0
    for wid in ["qwen25-3b-decode-c128-g1", "qwen25-3b-prefill-16", "qwen25-3b-prefill-128"]:
        entry = entries.get(wid, {})
        total = entry.get("total_serial_cpath", 0)
        if total <= 0:
            errors.append(f"{wid}: non-positive total_serial_cpath={total}")
        else:
            accepted += 1

    rejected = len(errors)
    verdict = "pass" if rejected == 0 else "fail"

    return {
        "verdict": verdict,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "warnings": warnings,
        "rtl_files_opened": rtl_files_opened,
    }


def run_workload_check(oracle_path: str) -> int:
    """Run the manifest + oracle validation for a named workload."""
    manifest_result = _validate_workload_manifest()
    oracle_result = _validate_oracle_consistency(oracle_path)

    accepted = manifest_result["accepted"] + oracle_result["accepted"]
    rejected = manifest_result["rejected"] + oracle_result["rejected"]
    errors = manifest_result["errors"] + oracle_result["errors"]
    warnings = manifest_result["warnings"] + oracle_result["warnings"]
    rtl_files_opened = manifest_result["rtl_files_opened"] + oracle_result["rtl_files_opened"]

    verdict = "pass" if rejected == 0 else "fail"

    result = {
        "test": "check_perf_workloads",
        "workload": "qwen25-3b",
        "verdict": verdict,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "warnings": warnings,
        "rtl_files_opened": rtl_files_opened,
        "manifest": manifest_result,
        "oracle": oracle_result,
    }
    print(json.dumps(result, indent=2))
    return 0 if verdict == "pass" else 1


def run_negative_fixtures(fixture_paths: List[str]) -> int:
    """Load each fixture JSON and verify it is rejected.

    rtl_files_opened counts only files whose absolute path contains 'rtl/'.
    Fixture paths like config/tests/qwen_rtl_source.json do NOT count —
    the rejection comes from their *content* referencing rtl/** paths.
    """
    rejected = 0
    accepted = 0
    rtl_files_opened = 0
    details: List[Dict[str, Any]] = []

    for path in fixture_paths:
        data, load_err = _load_json_safe(path)
        if load_err:
            rejected += 1
            details.append({"path": path, "accepted": False, "reason": load_err})
            continue

        # Classify rejection: old dims, 7-gemm, RTL source, or CV-specific
        is_old_dims = "qwen_old_dims" in path.lower()
        is_7gemm = "qwen_7gemm" in path.lower()
        is_rtl_source = "qwen_rtl_source" in path.lower()
        is_cv_dropped = "cv_dropped" in path.lower()
        is_cv_unknown = "cv_unknown" in path.lower()
        is_cv_bad_shape = "cv_bad_shape" in path.lower()

        data_str = json.dumps(data)
        fixture_error: Optional[str] = None

        if is_rtl_source:
            if re.search(r'rtl[/\\]', data_str):
                rejected += 1
                details.append({
                    "path": path, "accepted": False,
                    "rejection_reason": "Fixture declares RTL source path",
                    "rtl_files_opened": rtl_files_opened,
                })
                continue
            else:
                fixture_error = "RTL source fixture missing RTL path reference"

        elif is_old_dims:
            meta = data.get("model_meta", data.get("model", {}))
            hidden = meta.get("hidden", meta.get("hidden_size", 0))
            layers = meta.get("layers", meta.get("num_layers", meta.get("layer_count", 0)))
            if hidden == 2048 and layers == 36 and meta.get("heads") == 16:
                fixture_error = "old_dims fixture has correct canonical dimensions (should be wrong)"
            else:
                rejected += 1
                details.append({
                    "path": path, "accepted": False,
                    "rejection_reason": f"Wrong model dims: hidden={hidden}, layers={layers}",
                })
                continue

        elif is_7gemm:
            ops = data.get("ops", [])
            mxu_count = sum(1 for o in ops if o.get("engine") == "mxu")
            sfu_count = sum(1 for o in ops if o.get("engine") == "sfu")
            vec_count = sum(1 for o in ops if o.get("engine") == "vector")
            total_ops = len(ops)

            if total_ops > 7:
                fixture_error = f"7gemm fixture has {total_ops} ops (should have <= 7)"
            elif sfu_count > 0 or vec_count > 0:
                fixture_error = "7gemm fixture has SFU/Vector ops (should be MXU-only)"
            else:
                rejected += 1
                details.append({
                    "path": path, "accepted": False,
                    "rejection_reason": f"Only {total_ops} MXU ops, missing SFU/Vector (canonical=17)",
                })
                continue

        elif is_cv_dropped:
            entries = data.get("entries", [])
            inv = data.get("invariants", {})
            expected_total = inv.get("total_entries", 0)
            if len(entries) < expected_total:
                rejected += 1
                details.append({
                    "path": path, "accepted": False,
                    "rejection_reason": f"Dropped layer: {len(entries)} entries < expected {expected_total}",
                })
                continue
            else:
                fixture_error = f"cv_dropped_layer: has {len(entries)} entries == expected {expected_total} (should be fewer)"

        elif is_cv_unknown:
            entries = data.get("entries", [])
            unclassifiable = [
                e for e in entries
                if e.get("engine") is None and not e.get("host_only", False)
            ]
            if unclassifiable:
                rejected += 1
                details.append({
                    "path": path, "accepted": False,
                    "rejection_reason": f"Unknown op: {len(unclassifiable)} entries unclassifiable (no engine, not host-only)",
                })
                continue
            else:
                fixture_error = "cv_unknown_op: all entries classified (should have unclassifiable)"

        elif is_cv_bad_shape:
            entries = data.get("entries", [])
            bad_shapes = []
            for e in entries:
                shape = e.get("shape", {})
                eng = e.get("engine")
                if shape and any(v < 0 for v in shape.values()):
                    bad_shapes.append(e.get("name", f"seq={e.get('seq')}"))
                elif eng == "mxu" and set(shape.keys()) != {"M", "K", "N"}:
                    bad_shapes.append(e.get("name", f"seq={e.get('seq')}"))
                elif eng == "sfu" and set(shape.keys()) != {"elements"}:
                    bad_shapes.append(e.get("name", f"seq={e.get('seq')}"))
            if bad_shapes:
                rejected += 1
                details.append({
                    "path": path, "accepted": False,
                    "rejection_reason": f"Bad shapes: {len(bad_shapes)} entries with invalid shapes ({bad_shapes[:3]})",
                })
                continue
            else:
                fixture_error = "cv_bad_shape: no invalid shapes detected"

        if fixture_error:
            accepted += 1
            details.append({"path": path, "accepted": True,
                            "warning": f"Unexpectedly passed: {fixture_error}"})
        else:
            rejected += 1
            details.append({"path": path, "accepted": False})

    verdict = "pass" if accepted == 0 and rejected == len(fixture_paths) else "fail"

    result = {
        "test": "check_perf_workloads.negative_fixtures",
        "verdict": verdict,
        "rejected": rejected,
        "accepted": accepted,
        "rtl_files_opened": rtl_files_opened,
        "details": details,
    }
    print(json.dumps(result, indent=2))
    return 0 if verdict == "pass" else 1


def _check_fixture_should_reject(path: str, data: Dict[str, Any]) -> List[str]:
    """Given a fixture JSON, determine why it should be rejected.

    Returns empty list if the fixture should be rejected (correct rejection).
    Returns a list of reasons if the fixture should have been rejected but wasn't.
    """
    errors: List[str] = []

    # Check model dimensions for old_dims fixtures
    if "qwen_old_dims" in path.lower():
        meta = data.get("model_meta", data.get("model", {}))
        hidden = meta.get("hidden", meta.get("hidden_size", 0))
        layers = meta.get("layers", meta.get("num_layers", meta.get("layer_count", 0)))
        heads = meta.get("heads", meta.get("num_heads", meta.get("num_attention_heads", 0)))

        if hidden == 2048 and layers == 36 and heads == 16:
            errors.append("qwen_old_dims fixture has correct dims (should be wrong)")

    # Check 7 GEMM (no 17-op)
    if "qwen_7gemm" in path.lower():
        ops = data.get("ops", [])
        mxu_count = sum(1 for o in ops if o.get("engine") == "mxu")
        total_ops = len(ops)
        if mxu_count == 9 and total_ops == 17:
            errors.append("qwen_7gemm fixture has 9 MXU (should have 7)")
        elif total_ops == 17:
            errors.append("qwen_7gemm fixture has 17 ops (should have fewer)")

    # Check RTL source reference
    if "qwen_rtl_source" in path.lower():
        data_str = json.dumps(data)
        if not re.search(r'rtl[/\\]', data_str):
            errors.append("qwen_rtl_source fixture missing RTL path reference")

    # CV-specific fixtures
    if "cv_dropped_layer" in path.lower():
        entries = data.get("entries", [])
        inv = data.get("invariants", {})
        total = inv.get("total_entries", 0)
        if len(entries) == total:
            errors.append(f"cv_dropped_layer: {len(entries)} entries == expected {total} (should be fewer)")

    if "cv_unknown_op" in path.lower():
        entries = data.get("entries", [])
        has_unclassifiable = any(
            e.get("engine") is None and not e.get("host_only", False)
            for e in entries
        )
        if not has_unclassifiable:
            errors.append("cv_unknown_op: all entries have engine or host_only (should have unclassifiable)")

    if "cv_bad_shape" in path.lower():
        entries = data.get("entries", [])
        has_bad_shape = False
        for e in entries:
            shape = e.get("shape", {})
            if shape and any(v < 0 for v in shape.values()):
                has_bad_shape = True
                break
            eng = e.get("engine")
            if eng == "mxu" and set(shape.keys()) != {"M", "K", "N"}:
                has_bad_shape = True
                break
            if eng == "sfu" and set(shape.keys()) != {"elements"}:
                has_bad_shape = True
                break
        if not has_bad_shape:
            errors.append("cv_bad_shape: no bad shape detected (should have invalid shape)")

    return errors


# ── CV workload validation ────────────────────────────────────────────────────


def _validate_cv_manifest(workload_id: str) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    accepted: int = 0
    rtl_files_opened: int = 0

    manifest_path = _CV_MANIFEST_PATHS.get(workload_id)
    if manifest_path is None:
        return {"verdict": "fail", "errors": [f"Unknown CV workload: {workload_id}"],
                "accepted": 0, "rejected": 1, "rtl_files_opened": 0}

    manifest, load_err = _load_json_safe(str(manifest_path))
    if load_err:
        return {"verdict": "fail", "errors": [load_err], "accepted": 0,
                "rejected": 1, "rtl_files_opened": rtl_files_opened}

    entries = manifest.get("entries", [])
    inv = manifest.get("invariants", {})
    expected = _CV_EXPECTED_COUNTS.get(workload_id, {})

    # 1. Exact total entry count
    if len(entries) != expected.get("total", 0):
        errors.append(f"{workload_id}: {len(entries)} entries, expected {expected.get('total')}")
    else:
        accepted += 1

    # 2. GEMM count
    gemm_count = sum(1 for e in entries if e.get("engine") == "mxu")
    if gemm_count != expected.get("gemm", 0):
        errors.append(f"{workload_id}: {gemm_count} GEMM, expected {expected.get('gemm')}")
    else:
        accepted += 1

    # 3. SFU count
    sfu_count = sum(1 for e in entries if e.get("engine") == "sfu")
    if sfu_count != expected.get("sfu", 0):
        errors.append(f"{workload_id}: {sfu_count} SFU, expected {expected.get('sfu')}")
    else:
        accepted += 1

    # 4. Host-only count
    host_count = sum(1 for e in entries if e.get("host_only"))
    if host_count != expected.get("host_only", 0):
        errors.append(f"{workload_id}: {host_count} host-only, expected {expected.get('host_only')}")
    else:
        accepted += 1

    # 5. Content hash match
    actual_hash = hashlib.sha256(
        json.dumps(entries, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    expected_hash = _CV_EXPECTED_HASHES.get(workload_id, "")
    if actual_hash != expected_hash:
        errors.append(f"{workload_id}: content_hash={actual_hash}, expected={expected_hash}")
    else:
        accepted += 1

    # 6. All non-host entries have engine + op
    untyped = [e for e in entries if not e.get("host_only") and (e.get("engine") is None or e.get("op") is None)]
    if untyped:
        names = [e["name"] for e in untyped]
        errors.append(f"{workload_id}: {len(untyped)} entries missing engine/op: {names[:5]}")
    else:
        accepted += 1

    # 7. Shape key validation
    bad_shapes = []
    for e in entries:
        eng = e.get("engine")
        shape = e.get("shape", {})
        if eng == "mxu" and set(shape.keys()) != {"M", "K", "N"}:
            bad_shapes.append(e["name"])
        elif eng == "sfu" and set(shape.keys()) != {"elements"}:
            bad_shapes.append(e["name"])
        elif eng == "vector" and set(shape.keys()) != {"dim"}:
            bad_shapes.append(e["name"])
    if bad_shapes:
        errors.append(f"{workload_id}: {len(bad_shapes)} entries with wrong shape keys: {bad_shapes[:5]}")
    else:
        accepted += 1

    # 8. Dependency chain
    dep_errors = []
    for e in entries:
        for dep_seq in e.get("depends_on", []):
            if dep_seq < 0 or dep_seq >= len(entries):
                dep_errors.append(f"seq={e['seq']}: invalid dep={dep_seq}")
    if dep_errors:
        errors.append(f"{workload_id}: {len(dep_errors)} dependency errors")
    else:
        accepted += 1

    # 9. Host-only rubric: no engine assignment
    host_with_engine = [e for e in entries if e.get("host_only") and e.get("engine") is not None]
    if host_with_engine:
        errors.append(f"{workload_id}: {len(host_with_engine)} host-only entries have engine assigned")
    else:
        accepted += 1

    # 10. Trace generator seed = 42
    gen_info = manifest.get("trace_generator", {})
    if gen_info.get("seed") != 42:
        errors.append(f"{workload_id}: seed={gen_info.get('seed')} != 42")
    else:
        accepted += 1

    rejected = len(errors)
    verdict = "pass" if rejected == 0 else "fail"

    return {
        "verdict": verdict,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "warnings": warnings,
        "rtl_files_opened": rtl_files_opened,
    }


def _validate_cv_oracle_consistency(oracle_path: str) -> Dict[str, Any]:
    errors: List[str] = []
    rtl_files_opened: int = 0
    accepted: int = 0

    oracle, load_err = _load_json_safe(oracle_path)
    if load_err:
        return {"verdict": "fail", "errors": [load_err], "accepted": 0,
                "rejected": 1, "rtl_files_opened": rtl_files_opened}

    entries = oracle.get("workload_entries", {})

    for cv_id in ["mobilenetv3", "resnet50", "yolov8n"]:
        cv_entry = entries.get(cv_id)
        if cv_entry is None:
            errors.append(f"Oracle missing CV entry: {cv_id}")
            continue
        accepted += 1

        # Check per-workload decomposition exists (summary-format for CV)
        per_op = cv_entry.get("per_op_cycles", {})
        summary = per_op.get("summary", {})
        if not summary:
            errors.append(f"{cv_id}: per_op_cycles missing summary")
        else:
            accepted += 1
            # Verify summary counts match expected invariants
            expected = _CV_EXPECTED_COUNTS.get(cv_id, {})
            if summary.get("total_entries") != expected.get("total"):
                errors.append(f"{cv_id}: summary total_entries={summary.get('total_entries')} != {expected.get('total')}")
            else:
                accepted += 1
            if summary.get("gemm_entries") != expected.get("gemm"):
                errors.append(f"{cv_id}: summary gemm_entries={summary.get('gemm_entries')} != {expected.get('gemm')}")
            else:
                accepted += 1
            if summary.get("sfu_entries") != expected.get("sfu"):
                errors.append(f"{cv_id}: summary sfu_entries={summary.get('sfu_entries')} != {expected.get('sfu')}")
            else:
                accepted += 1

        # Check has critical_path
        cpath = cv_entry.get("critical_path", {})
        if not cpath or cpath.get("serialized_cpath_cycles", 0) <= 0:
            errors.append(f"{cv_id}: missing or zero critical_path")
        else:
            accepted += 1

    rejected = len(errors)
    verdict = "pass" if rejected == 0 else "fail"

    return {
        "verdict": verdict,
        "accepted": accepted,
        "rejected": rejected,
        "errors": errors,
        "warnings": [],
        "rtl_files_opened": rtl_files_opened,
    }


def run_cv_workload_check(workload_ids: List[str], oracle_path: str) -> int:
    errors: List[str] = []
    accepted_all: int = 0
    rejected_all: int = 0
    cv_results: Dict[str, Any] = {}

    for wid in workload_ids:
        if wid not in _CV_VALID_WORKLOAD_IDS:
            errors.append(f"Unknown CV workload: {wid}")
            continue
        r = _validate_cv_manifest(wid)
        cv_results[wid] = r
        accepted_all += r["accepted"]
        rejected_all += r["rejected"]
        errors.extend(r.get("errors", []))

    oracle_result = _validate_cv_oracle_consistency(oracle_path)
    cv_results["oracle"] = oracle_result
    accepted_all += oracle_result["accepted"]
    rejected_all += oracle_result["rejected"]
    errors.extend(oracle_result.get("errors", []))

    verdict = "pass" if rejected_all == 0 else "fail"

    result = {
        "test": "check_perf_workloads",
        "workload": ",".join(workload_ids),
        "verdict": verdict,
        "accepted": accepted_all,
        "rejected": rejected_all,
        "errors": errors,
        "warnings": [],
        "rtl_files_opened": 0,
        "cv_results": cv_results,
    }
    print(json.dumps(result, indent=2))
    return 0 if verdict == "pass" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Performance workload checker")
    parser.add_argument(
        "--workload", type=str, default=None,
        help="Workload name to validate (qwen25-3b or CV CSV: mobilenetv3,resnet50,yolov8n)"
    )
    parser.add_argument(
        "--oracle", type=str, default="config/func_model_workload_oracle_v1.json",
        help="Path to workload oracle JSON"
    )
    parser.add_argument(
        "--negative-fixtures", type=str, default="",
        help="Comma-separated paths to negative fixture JSON files"
    )
    args = parser.parse_args()

    if args.negative_fixtures:
        paths = [p.strip() for p in args.negative_fixtures.split(",") if p.strip()]
        if not paths:
            print("Error: --negative-fixtures requires at least one path", file=sys.stderr)
            return 1
        return run_negative_fixtures(paths)

    if args.workload:
        # Check if it's a CV workload (comma-separated or single CV ID)
        workload_ids = [w.strip() for w in args.workload.split(",")]
        cv_ids = [w for w in workload_ids if w in _CV_VALID_WORKLOAD_IDS]
        qwen_ids = [w for w in workload_ids if w == "qwen25-3b"]

        if cv_ids:
            # Run CV workload check
            return run_cv_workload_check(cv_ids, args.oracle)

        if qwen_ids:
            if args.workload != "qwen25-3b" and len(workload_ids) != 1:
                print(f"Error: unknown workload '{args.workload}' (only 'qwen25-3b' and CV IDs supported)",
                      file=sys.stderr)
                return 1
            return run_workload_check(args.oracle)

        print(f"Error: unknown workload '{args.workload}' (valid: qwen25-3b, mobilenetv3, resnet50, yolov8n)",
              file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
