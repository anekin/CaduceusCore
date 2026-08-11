"""Canonical Qwen2.5-3B and CV performance workload builders.

Loads frozen manifests from ``config/workloads/`` and exports typed workload
dicts keyed by hard-gate workload IDs.

Usage:
    from timing.workloads import build_qwen25_3b_workload, build_mobilenetv3_workload
    wl = build_qwen25_3b_workload("qwen25-3b-blk0-decode")
    cv_wl = build_mobilenetv3_workload()
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST_PATH = _REPO_ROOT / "config" / "workloads" / "qwen25_3b_perf_spec_v1.json"

# CV manifest paths
_CV_MANIFEST_PATHS: Dict[str, Path] = {
    "mobilenetv3": _REPO_ROOT / "config" / "workloads" / "mobilenetv3_perf_spec_v1.json",
    "resnet50": _REPO_ROOT / "config" / "workloads" / "resnet50_perf_spec_v1.json",
    "yolov8n": _REPO_ROOT / "config" / "workloads" / "yolov8n_perf_spec_v1.json",
}

_CV_WORKLOAD_IDS = frozenset({"mobilenetv3", "resnet50", "yolov8n"})

_VALID_WORKLOAD_IDS = frozenset({
    "qwen25-3b-blk0-decode",
    "qwen25-3b-decode-c128-g1",
    "qwen25-3b-prefill-16",
    "qwen25-3b-prefill-128",
})


def _load_manifest() -> Dict[str, Any]:
    with open(_MANIFEST_PATH, "r") as f:
        return json.load(f)


def _find_variant(manifest: Dict[str, Any], workload_id: str) -> Dict[str, Any]:
    for v in manifest.get("workload_variants", []):
        if v.get("workload_id") == workload_id:
            return v
    raise ValueError(f"Workload variant '{workload_id}' not found in manifest")


def _resolve_shape(op_formula: str, variant: Dict[str, Any]) -> Dict[str, int]:
    """Resolve a shape formula like 'M=batch_m, K=hidden, N=heads*head_dim' to concrete ints."""
    result: Dict[str, int] = {}
    if not op_formula.strip():
        return result
    parts = [p.strip() for p in op_formula.split(",")]
    for part in parts:
        if "=" not in part:
            continue
        key, expr = part.split("=", 1)
        key = key.strip()
        expr = expr.strip()
        # Expand variables from variant
        val = _eval_expr(expr, variant)
        result[key] = val
    return result


def _eval_expr(expr: str, variant: Dict[str, Any]) -> int:
    """Simple expression evaluator supporting variant field names and basic arithmetic."""
    expr_clean = expr.replace(" ", "")
    # Replace known tokens (longest first to avoid substring collisions)
    tokens = sorted(
        ["intermediate", "layer_count", "context_len", "prompt_len", "batch_m",
         "kv_heads", "head_dim", "heads", "hidden"],
        key=len, reverse=True,
    )
    for token in tokens:
        if token in expr_clean:
            expr_clean = expr_clean.replace(token, str(variant.get(token, 0)))
    if "*" in expr_clean:
        parts = expr_clean.split("*")
        result = 1
        for p in parts:
            result *= int(p)
        return result
    return int(expr_clean)


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def _compute_per_op_shapes(
    ops: List[Dict[str, Any]], variant: Dict[str, Any]
) -> Dict[str, Dict[str, int]]:
    """Resolve all per-op concrete shapes."""
    shapes: Dict[str, Dict[str, int]] = {}
    for op in ops:
        op_id = op["op_id"]
        formula = op.get("shape_formula", "")
        shapes[op_id] = _resolve_shape(formula, variant)
    return shapes


def build_qwen25_3b_workload(workload_id: str) -> Dict[str, Any]:
    """Build a typed workload dict from the canonical manifest + variant.

    Returns:
        dict with keys: workload_id, variant, ops (list of {op_id, name, engine,
        shape, seq}), total_ops, engine_counts (mxu/sfu/vector),
        dependency_edges, content_hash.
    """
    if workload_id not in _VALID_WORKLOAD_IDS:
        raise ValueError(
            f"Unknown workload_id '{workload_id}'. Valid: {sorted(_VALID_WORKLOAD_IDS)}"
        )

    manifest = _load_manifest()
    variant = _find_variant(manifest, workload_id)
    ops = manifest.get("ops", [])

    per_op_shapes = _compute_per_op_shapes(ops, variant)

    layer_count = variant.get("layer_count", 1)
    total_layer_ops = len(ops)

    op_list = []
    for op in ops:
        op_id = op["op_id"]
        shape = per_op_shapes.get(op_id, {})
        op_list.append({
            "op_id": op_id,
            "seq": op["seq"],
            "name": op["name"],
            "engine": op["engine"],
            "shape": shape,
        })

    engine_counts = {"mxu": 0, "sfu": 0, "vector": 0}
    for op in op_list:
        eng = op["engine"]
        if eng in engine_counts:
            engine_counts[eng] += 1

    result: Dict[str, Any] = {
        "workload_id": workload_id,
        "variant": variant,
        "ops": op_list,
        "layer_ops": total_layer_ops,
        "total_ops": layer_count * total_layer_ops,
        "layer_count": layer_count,
        "engine_counts": engine_counts,
        "dependency_edges": manifest.get("dependency_edges", {}),
        "parallel_chains": manifest.get("parallel_chains", []),
    }

    # Compute content hash (excludes volatile metadata)
    hash_data = {
        "workload_id": workload_id,
        "variant": {k: v for k, v in variant.items() if k != "description"},
        "ops": op_list,
        "dependency_edges": manifest.get("dependency_edges", {}),
    }
    result["content_hash"] = hashlib.sha256(
        json.dumps(hash_data, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    return result


def list_workload_ids() -> List[str]:
    return sorted(_VALID_WORKLOAD_IDS)


def validate_manifest() -> Dict[str, Any]:
    """Structural validation of the manifest file. Returns a dict with errors/warnings."""
    errors: List[str] = []
    warnings: List[str] = []

    manifest = _load_manifest()
    ops = manifest.get("ops", [])
    variants = manifest.get("workload_variants", [])
    deps = manifest.get("dependency_edges", {})

    # 1. 17-op layer count
    if len(ops) != 17:
        errors.append(f"Manifest has {len(ops)} ops, expected 17")
    else:
        # Count engines
        mxu = sum(1 for o in ops if o.get("engine") == "mxu")
        sfu = sum(1 for o in ops if o.get("engine") == "sfu")
        vec = sum(1 for o in ops if o.get("engine") == "vector")
        if mxu != 9:
            errors.append(f"MXU count: {mxu} != 9")
        if sfu != 5:
            errors.append(f"SFU count: {sfu} != 5")
        if vec != 3:
            errors.append(f"Vector count: {vec} != 3")

    # 2. 4 workload variants
    if len(variants) != 4:
        errors.append(f"Manifest has {len(variants)} variants, expected 4")
    else:
        variant_ids = {v["workload_id"] for v in variants}
        if variant_ids != _VALID_WORKLOAD_IDS:
            errors.append(f"Variant IDs mismatch: {variant_ids} != {_VALID_WORKLOAD_IDS}")

    # 3. Model metadata pins
    meta = manifest.get("model_meta", {})
    if meta.get("hidden") != 2048:
        errors.append(f"model_meta.hidden={meta.get('hidden')} != 2048")
    if meta.get("intermediate") != 11008:
        errors.append(f"model_meta.intermediate={meta.get('intermediate')} != 11008")
    if meta.get("layers") != 36:
        errors.append(f"model_meta.layers={meta.get('layers')} != 36")
    if meta.get("heads") != 16:
        errors.append(f"model_meta.heads={meta.get('heads')} != 16")
    if meta.get("kv_heads") != 2:
        errors.append(f"model_meta.kv_heads={meta.get('kv_heads')} != 2")
    if meta.get("head_dim") != 128:
        errors.append(f"model_meta.head_dim={meta.get('head_dim')} != 128")
    if meta.get("kv_dim") != 256:
        errors.append(f"model_meta.kv_dim={meta.get('kv_dim')} != 256")

    # 4. DAG edges
    for op in ops:
        op_id = op["op_id"]
        if op_id not in deps:
            errors.append(f"op {op_id} missing from dependency_edges")

    # 5. 612-op for 36-layer cases
    for v in variants:
        if v.get("layer_count") == 36:
            total = 36 * len(ops)
            if total != 612:
                errors.append(f"Variant {v['workload_id']}: 36*{len(ops)}={total} != 612")
        elif v.get("layer_count") == 1:
            if len(ops) != 17:
                errors.append(f"Variant {v['workload_id']}: 1-layer should have 17 ops")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


# ── CV workload builders ──────────────────────────────────────────────────────


def _load_cv_manifest(workload_id: str) -> Dict[str, Any]:
    path = _CV_MANIFEST_PATHS.get(workload_id)
    if path is None:
        raise ValueError(f"Unknown CV workload_id '{workload_id}'. Valid: {sorted(_CV_WORKLOAD_IDS)}")
    with open(path, "r") as f:
        return json.load(f)


def _build_cv_typed_entries(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Convert manifest entries to typed workload ops list."""
    entries = manifest.get("entries", [])
    result: List[Dict[str, Any]] = []
    for entry in entries:
        result.append({
            "seq": entry["seq"],
            "name": entry["name"],
            "engine": entry.get("engine"),
            "op": entry.get("op"),
            "host_only": entry.get("host_only", False),
            "shape": entry.get("shape", {}),
            "depends_on": entry.get("depends_on", []),
        })
    return result


def _build_cv_workload(workload_id: str) -> Dict[str, Any]:
    manifest = _load_cv_manifest(workload_id)
    ops = _build_cv_typed_entries(manifest)
    inv = manifest.get("invariants", {})
    meta = manifest.get("model_meta", {})

    engine_counts: Dict[str, int] = {"mxu": 0, "sfu": 0, "vector": 0, "host_only": 0}
    for op_entry in ops:
        eng = op_entry.get("engine")
        if eng and eng in engine_counts:
            engine_counts[eng] += 1
        elif op_entry.get("host_only"):
            engine_counts["host_only"] += 1

    result: Dict[str, Any] = {
        "workload_id": workload_id,
        "model_meta": meta,
        "ops": ops,
        "total_entries": len(ops),
        "engine_counts": engine_counts,
        "invariants": inv,
        "content_hash": manifest.get("content_hash", ""),
        "trace_generator": manifest.get("trace_generator", {}),
    }
    return result


def build_mobilenetv3_workload() -> Dict[str, Any]:
    return _build_cv_workload("mobilenetv3")


def build_resnet50_workload() -> Dict[str, Any]:
    return _build_cv_workload("resnet50")


def build_yolov8n_workload() -> Dict[str, Any]:
    return _build_cv_workload("yolov8n")


_BUILD_CV_MAP: Dict[str, Any] = {
    "mobilenetv3": build_mobilenetv3_workload,
    "resnet50": build_resnet50_workload,
    "yolov8n": build_yolov8n_workload,
}


def build_cv_workload(workload_id: str) -> Dict[str, Any]:
    if workload_id not in _CV_WORKLOAD_IDS:
        raise ValueError(f"Unknown CV workload_id '{workload_id}'. Valid: {sorted(_CV_WORKLOAD_IDS)}")
    return _BUILD_CV_MAP[workload_id]()


def list_cv_workload_ids() -> List[str]:
    return sorted(_CV_WORKLOAD_IDS)


def validate_cv_manifest(workload_id: str) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []

    manifest = _load_cv_manifest(workload_id)
    entries = manifest.get("entries", [])
    inv = manifest.get("invariants", {})

    expected_total = inv.get("total_entries", 0)
    if len(entries) != expected_total:
        errors.append(f"Manifest has {len(entries)} entries, expected {expected_total}")

    expected_gemm = inv.get("gemm_entries", 0)
    actual_gemm = sum(1 for e in entries if e.get("engine") == "mxu")
    if actual_gemm != expected_gemm:
        errors.append(f"GEMM entries: {actual_gemm} != {expected_gemm}")

    expected_sfu = inv.get("sfu_entries", 0)
    actual_sfu = sum(1 for e in entries if e.get("engine") == "sfu")
    if actual_sfu != expected_sfu:
        errors.append(f"SFU entries: {actual_sfu} != {expected_sfu}")

    expected_host = inv.get("host_only_entries", 0)
    actual_host = sum(1 for e in entries if e.get("host_only"))
    if actual_host != expected_host:
        errors.append(f"Host-only entries: {actual_host} != {expected_host}")

    # Validate all entries have typed engine/op or host_only
    for entry in entries:
        engine = entry.get("engine")
        op_type = entry.get("op")
        host_only = entry.get("host_only", False)
        if not host_only and (engine is None or op_type is None):
            errors.append(
                f"Entry seq={entry['seq']} name={entry['name']}: "
                f"non-host entry missing engine/op (engine={engine}, op={op_type})"
            )

    # Validate shape keys per engine
    for entry in entries:
        engine = entry.get("engine")
        shape = entry.get("shape", {})
        if engine == "mxu":
            if set(shape.keys()) != {"M", "K", "N"}:
                errors.append(f"Entry seq={entry['seq']}: mxu shape keys {set(shape.keys())} != {{M,K,N}}")
        elif engine == "sfu":
            if set(shape.keys()) != {"elements"}:
                errors.append(f"Entry seq={entry['seq']}: sfu shape keys {set(shape.keys())} != {{elements}}")
        elif engine == "vector":
            if set(shape.keys()) != {"dim"}:
                errors.append(f"Entry seq={entry['seq']}: vector shape keys {set(shape.keys())} != {{dim}}")

    # Dependency chain consistency
    for entry in entries:
        deps = entry.get("depends_on", [])
        for dep_seq in deps:
            if dep_seq < 0 or dep_seq >= len(entries):
                errors.append(f"Entry seq={entry['seq']}: invalid dependency seq={dep_seq}")
        if deps and entry["seq"] == 0:
            errors.append(f"Entry seq=0 must have no dependencies")

    # Content hash stability
    meta = manifest.get("model_meta", {})
    input_shape = meta.get("input_shape")
    if workload_id in ("mobilenetv3", "resnet50") and input_shape != [1, 3, 224, 224]:
        errors.append(f"{workload_id} input_shape={input_shape} != [1,3,224,224]")
    if workload_id == "yolov8n" and input_shape != [1, 3, 640, 640]:
        errors.append(f"yolov8n input_shape={input_shape} != [1,3,640,640]")

    gen_info = manifest.get("trace_generator", {})
    if gen_info.get("seed") != 42:
        errors.append(f"trace_generator.seed={gen_info.get('seed')} != 42")

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }
