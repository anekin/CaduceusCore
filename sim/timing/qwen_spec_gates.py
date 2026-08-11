"""T16 Qwen workload dual-path spec-gate API.

Path A builds the canonical 17-op workload from the manifest, estimates each
semantic op through the architectural formula used by the T7 Block64 provider,
and reduces the DAG with the T15 critical-path reducer.

Path B is invoked externally via ``scripts/reduce_func_model_perf_oracle.py``
in a subprocess with a restricted PYTHONPATH so it cannot import Path A modules.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from timing.timing_engine import compute_critical_path_from_dag
from timing.workloads import build_qwen25_3b_workload


REPO_ROOT = Path(__file__).resolve().parents[2]

_MAJOR_BREAKDOWN_CATEGORIES = frozenset({
    "mxu", "sfu", "vector", "dram", "dma", "dma_effective", "dma_weight",
    "noc_latency", "noc_contention", "kv_cache", "crossbar_wait",
    "sram_stall", "vcov_bubble", "host_only",
})


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def _mxu_decode_cycles(
    M: int, K: int, N: int,
    array_H: int = 64, array_W: int = 64,
    bw_bpc: float = 43.52, weight_bytes_per_elem: float = 0.5,
) -> int:
    K_tiles = _ceil_div(K, array_H)
    N_tiles = _ceil_div(N, array_W)
    total_tiles = K_tiles * N_tiles
    per_tile_compute = array_H * (M + 1) + array_W
    weight_bytes = array_H * array_W * weight_bytes_per_elem
    act_bytes = M * array_H
    per_tile_dma = (weight_bytes + act_bytes) / bw_bpc if bw_bpc > 0 else float("inf")
    first_tile_cold = per_tile_compute + per_tile_dma
    bottleneck = max(per_tile_compute, per_tile_dma)
    if M >= array_H:
        per_tile_compute = array_H + array_W + array_H
    raw = first_tile_cold + (total_tiles - 1) * bottleneck
    return math.ceil(raw)


def _sfu_cycles(
    op: str, elements: int, sfu_width: int = 128,
    pipeline_depths: Optional[Dict[str, int]] = None,
) -> int:
    if pipeline_depths is None:
        pipeline_depths = {
            "softmax": 227, "layernorm": 210, "rmsnorm": 150,
            "gelu": 71, "silu": 72, "rope": 82,
        }
    if elements == 0:
        return 0
    depth = pipeline_depths.get(op, 100)
    batches = _ceil_div(elements, sfu_width)
    return depth * batches


def _vector_cycles(
    op: str, dim: int, vector_width: int = 128,
    op_latencies: Optional[Dict[str, int]] = None,
) -> int:
    if op_latencies is None:
        op_latencies = {
            "add": 5, "mul": 5, "max": 12, "sum": 12,
            "conv": 260, "resid": 5,
        }
    if dim == 0:
        return 0
    latency = op_latencies.get(op, 5)
    batches = _ceil_div(dim, vector_width)
    return latency * batches


def _canonical_workload_hash(workload_id: str, variant: Dict[str, Any]) -> str:
    """Compute a deterministic hash matching Path B's reducer."""
    manifest_path = REPO_ROOT / "config" / "workloads" / "qwen25_3b_perf_spec_v1.json"
    manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    ops = manifest_doc.get("ops", [])

    op_snapshot = [
        {"op_id": op.get("op_id"), "name": op.get("name"),
         "engine": op.get("engine"), "shape_formula": op.get("shape_formula", "")}
        for op in ops
    ]
    numeric_variant = {k: v for k, v in variant.items()
                       if k not in ("description", "expected_noop_ops")
                       and not isinstance(v, (list, dict))}
    snapshot = {
        "workload_id": workload_id,
        "ops": op_snapshot,
        "variant": dict(sorted(numeric_variant.items())),
    }
    canonical = json.dumps(snapshot, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_path_a_result(
    workload_id: str,
    use_sum_of_breakdowns: bool = False,
    drop_attention_ops: bool = False,
) -> Dict[str, Any]:
    """Reduce a Qwen workload through Path A and return a structured result."""
    workload = build_qwen25_3b_workload(workload_id)
    ops = list(workload["ops"])
    variant = workload["variant"]

    if drop_attention_ops:
        attention_ids = {"op_05", "op_06", "op_07"}
        ops = [op for op in ops if op["op_id"] not in attention_ids]

    op_idx = {op["op_id"]: i for i, op in enumerate(ops)}
    nodes: List[Dict[str, Any]] = []
    breakdown: Dict[str, int] = {"mxu": 0, "sfu": 0, "vector": 0}

    batch_m = variant.get("batch_m", 1)
    context_len = variant.get("context_len", 0)
    hidden = variant.get("hidden", 2048)
    intermediate = variant.get("intermediate", 11008)
    heads = variant.get("heads", 16)
    kv_heads = variant.get("kv_heads", 2)
    head_dim = variant.get("head_dim", 128)

    for op in ops:
        name = op["name"]
        engine = op["engine"]
        shape = op["shape"]

        if engine == "mxu":
            M = shape.get("M", batch_m)
            K = shape.get("K", hidden)
            N = shape.get("N", hidden)
            cycles = _mxu_decode_cycles(M, K, N)
        elif engine == "sfu":
            elements = shape.get("elements", 0)
            sfu_op = name.lower()
            if sfu_op == "rope":
                sfu_op = "rope"
            elif sfu_op.startswith("rmsnorm"):
                sfu_op = "rmsnorm"
            elif sfu_op == "silu":
                sfu_op = "silu"
            elif sfu_op == "softmax":
                sfu_op = "softmax"
            else:
                sfu_op = "rmsnorm"
            cycles = _sfu_cycles(sfu_op, elements)
        elif engine == "vector":
            dim = shape.get("dim", 0)
            vec_op = "resid" if "resid" in name.lower() else "mul"
            cycles = _vector_cycles(vec_op, dim)
        else:
            cycles = 0

        nodes.append({"cycles": cycles, "engine": engine, "name": name})
        breakdown[engine] += cycles

    dependency_edges = workload["dependency_edges"]
    edges: List[Tuple[int, int]] = []
    for src_id, dst_ids in dependency_edges.items():
        if src_id not in op_idx:
            continue
        for dst_id in dst_ids:
            if dst_id in op_idx:
                edges.append((op_idx[src_id], op_idx[dst_id]))

    critical_path = compute_critical_path_from_dag(nodes, edges)
    total_cycles = sum(breakdown.values()) if use_sum_of_breakdowns else critical_path

    layer_count = workload["layer_count"]
    total_ops = workload["total_ops"]
    if drop_attention_ops:
        total_ops = len(ops) * layer_count

    return {
        "path": "Path A",
        "workload_id": workload_id,
        "total_cycles": total_cycles,
        "critical_path_cycles": critical_path,
        "breakdown": breakdown,
        "op_count": total_ops,
        "layer_count": layer_count,
        "units": "cycles",
        "workload_hash": _canonical_workload_hash(workload_id, workload["variant"]),
        "functional_pass": layer_count == 1,
        "manifest_ref": str(REPO_ROOT / "config" / "workloads" / "qwen25_3b_perf_spec_v1.json"),
    }


def _category_share(category: str, breakdown: Dict[str, int]) -> float:
    total = sum(breakdown.values())
    if total == 0:
        return 0.0
    return breakdown.get(category, 0) / total


def compare_path_results(
    path_a: Dict[str, Any],
    path_b: Dict[str, Any],
) -> Dict[str, Any]:
    """Compare Path A and Path B results under the T16 <=20% gate."""
    assertions: List[Dict[str, Any]] = []

    def _assert(id_: str, ok: bool, detail: str) -> None:
        assertions.append({"id": id_, "result": "pass" if ok else "fail", "detail": detail})

    workload_id = path_a["workload_id"]
    _assert(
        "structural_op_count",
        path_a["op_count"] == path_b["op_count"],
        f"{workload_id}: Path A op_count={path_a['op_count']}, Path B op_count={path_b['op_count']}",
    )
    expected_ops = 17 if path_a["layer_count"] == 1 else 612
    _assert(
        "canonical_op_count",
        path_a["op_count"] == expected_ops,
        f"{workload_id}: expected {expected_ops} ops, got {path_a['op_count']}",
    )
    _assert(
        "structural_workload_hash",
        path_a["workload_hash"] == path_b["workload_hash"],
        f"{workload_id}: workload hash mismatch",
    )
    _assert(
        "matching_units",
        path_a["units"] == path_b["units"] == "cycles",
        f"{workload_id}: units mismatch",
    )
    _assert(
        "positive_activity",
        path_a["total_cycles"] > 0 and path_b["total_cycles"] > 0,
        f"{workload_id}: non-positive total cycles",
    )

    a_total = path_a["total_cycles"]
    b_total = path_b["total_cycles"]
    total_err = abs(a_total - b_total) / max(b_total, 1) * 100.0
    _assert(
        "total_cycles_within_20pct",
        total_err <= 20.0,
        f"{workload_id}: total error {total_err:.2f}% (A={a_total}, B={b_total})",
    )

    categories = set(path_a["breakdown"].keys()) | set(path_b["breakdown"].keys())
    for cat in categories:
        if cat not in _MAJOR_BREAKDOWN_CATEGORIES:
            continue
        a_share = _category_share(cat, path_a["breakdown"])
        b_share = _category_share(cat, path_b["breakdown"])
        is_major = a_share >= 0.05 or b_share >= 0.05
        a_val = path_a["breakdown"].get(cat, 0)
        b_val = path_b["breakdown"].get(cat, 0)
        if is_major:
            denom = max(b_val, 1)
            err = abs(a_val - b_val) / denom * 100.0
            _assert(
                f"major_breakdown_{cat}_within_20pct",
                err <= 20.0,
                f"{workload_id}: {cat} error {err:.2f}% (A={a_val}, B={b_val})",
            )
        else:
            _assert(
                f"minor_breakdown_{cat}_within_2",
                abs(a_val - b_val) <= 2,
                f"{workload_id}: {cat} abs error {abs(a_val - b_val)} (A={a_val}, B={b_val})",
            )

    passed = all(a["result"] == "pass" for a in assertions)
    return {
        "workload_id": workload_id,
        "path_a_total": a_total,
        "path_b_total": b_total,
        "total_error_pct": round(total_err, 2),
        "path_a_breakdown": path_a["breakdown"],
        "path_b_breakdown": path_b["breakdown"],
        "assertions": assertions,
        "passed": passed,
    }


def evaluate_qwen_workload(
    workload_id: str,
    path_b_subprocess_env: Optional[Dict[str, str]] = None,
    path_b_oracle: str = "config/func_model_workload_oracle_v1.json",
    path_b_template: str = "config/oracle/qwen25_3b_layer_template_v1.json",
    path_b_variants: str = "config/oracle/qwen25_3b_workload_variants_v1.json",
    path_b_manifest: str = "config/workloads/qwen25_3b_perf_spec_v1.json",
    use_sum_of_breakdowns: bool = False,
    drop_attention_ops: bool = False,
) -> Dict[str, Any]:
    """Run both paths for one workload and return the comparison result."""
    path_a = compute_path_a_result(
        workload_id,
        use_sum_of_breakdowns=use_sum_of_breakdowns,
        drop_attention_ops=drop_attention_ops,
    )

    import subprocess

    env = (path_b_subprocess_env or {}).copy()
    env.setdefault("PYTHONPATH", "")
    cmd = [
        "python3", "scripts/reduce_func_model_perf_oracle.py",
        "--oracle", path_b_oracle,
        "--template", path_b_template,
        "--variants", path_b_variants,
        "--manifest", path_b_manifest,
        "--workload-id", workload_id,
    ]
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=str(REPO_ROOT), env=env, timeout=60,
    )
    if result.returncode != 0:
        return {
            "workload_id": workload_id,
            "verdict": "fail",
            "error": f"Path B reducer failed: {result.stderr}",
            "path_a": path_a,
        }
    path_b = json.loads(result.stdout)
    comparison = compare_path_results(path_a, path_b)
    comparison["path_a"] = path_a
    comparison["path_b"] = path_b
    return comparison
