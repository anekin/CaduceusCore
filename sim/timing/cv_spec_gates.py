"""T17 CV workload dual-path spec-gate API.

Path A loads the frozen CV manifest, estimates per-entry cycles from the same
architectural formulas used for Qwen, and reduces the entry dependency DAG with
the T15 critical-path reducer.

Path B is invoked externally via ``scripts/reduce_func_model_perf_oracle.py``
in a subprocess with a restricted PYTHONPATH so it cannot import Path A modules.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from timing.qwen_spec_gates import (
    _MAJOR_BREAKDOWN_CATEGORIES,
    _ceil_div,
    _sfu_cycles,
    _vector_cycles,
    compare_path_results,
)
from timing.timing_engine import compute_critical_path_from_dag
from timing.workloads import build_cv_workload


REPO_ROOT = Path(__file__).resolve().parents[2]

_CV_WORKLOAD_IDS = {"mobilenetv3", "resnet50", "yolov8n"}


# Re-use the Qwen MXU decode formula but expose the im2col/activation-bytes
# multiplier so the ``im2col-bytes-x8`` adversarial fault can inflate DMA.
def _cv_mxu_cycles(
    M: int,
    K: int,
    N: int,
    array_H: int = 64,
    array_W: int = 64,
    bw_bpc: float = 43.52,
    weight_bytes_per_elem: float = 0.5,
    im2col_bytes_multiplier: float = 1.0,
) -> int:
    K_tiles = _ceil_div(K, array_H)
    N_tiles = _ceil_div(N, array_W)
    total_tiles = K_tiles * N_tiles
    # For large M the tile compute collapses to a fixed fill+drain cycle count.
    if M >= array_H:
        per_tile_compute = array_H + array_W + array_H
    else:
        per_tile_compute = array_H * (M + 1) + array_W
    weight_bytes = array_H * array_W * weight_bytes_per_elem
    act_bytes = M * array_H * im2col_bytes_multiplier
    per_tile_dma = (weight_bytes + act_bytes) / bw_bpc if bw_bpc > 0 else float("inf")
    first_tile_cold = per_tile_compute + per_tile_dma
    bottleneck = max(per_tile_compute, per_tile_dma)
    raw = first_tile_cold + (total_tiles - 1) * bottleneck
    return math.ceil(raw)


def _is_depthwise(op: Dict[str, Any]) -> bool:
    """Detect depthwise entries when the manifest does not label them explicitly.

    Depthwise convolutions mapped via im2col have a single output channel per
    group (N == 1) and a small K that corresponds to a square spatial kernel.
    """
    shape = op.get("shape", {})
    N = shape.get("N", 0)
    K = shape.get("K", 0)
    name = op.get("name", "")
    if "depthwise" in name.lower() or op.get("op") == "depthwise_conv":
        return True
    if N == 1 and K > 1:
        return True
    return False


def compute_path_a_result(
    workload_id: str,
    im2col_bytes_multiplier: float = 1.0,
    dropped_depthwise: bool = False,
    inject_unknown_op: bool = False,
) -> Dict[str, Any]:
    """Reduce a CV workload through Path A and return a structured result."""
    if workload_id not in _CV_WORKLOAD_IDS:
        raise ValueError(f"Unknown CV workload_id '{workload_id}'")

    workload = build_cv_workload(workload_id)
    ops: List[Dict[str, Any]] = list(workload["ops"])

    if dropped_depthwise:
        ops = [op for op in ops if not _is_depthwise(op)]

    if inject_unknown_op:
        ops.append(
            {
                "seq": len(ops),
                "name": "unknown_fault_op",
                "engine": "unknown",
                "op": "unknown",
                "host_only": False,
                "shape": {},
                "depends_on": [],
            }
        )

    seq_to_idx = {op["seq"]: i for i, op in enumerate(ops)}
    nodes: List[Dict[str, Any]] = []
    breakdown: Dict[str, int] = {"mxu": 0, "sfu": 0, "vector": 0, "host_only": 0}
    engine_counts: Dict[str, int] = {"mxu": 0, "sfu": 0, "vector": 0, "host_only": 0}

    for op in ops:
        engine = op.get("engine")
        host_only = op.get("host_only", False) or engine is None
        name = op.get("name", "")
        shape = op.get("shape", {})

        if host_only:
            cycles = 0
            breakdown["host_only"] += 0
            engine_counts["host_only"] += 1
        elif engine == "mxu":
            cycles = _cv_mxu_cycles(
                shape.get("M", 0),
                shape.get("K", 0),
                shape.get("N", 0),
                im2col_bytes_multiplier=im2col_bytes_multiplier,
            )
            breakdown["mxu"] += cycles
            engine_counts["mxu"] += 1
        elif engine == "sfu":
            cycles = _sfu_cycles("silu", shape.get("elements", 0))
            breakdown["sfu"] += cycles
            engine_counts["sfu"] += 1
        elif engine == "vector":
            cycles = _vector_cycles("mul", shape.get("dim", 0))
            breakdown["vector"] += cycles
            engine_counts["vector"] += 1
        else:
            cycles = 0
            engine_counts.setdefault(str(engine), 0)
            engine_counts[str(engine)] += 1

        nodes.append({"cycles": cycles, "engine": engine, "name": name})

    edges: List[Tuple[int, int]] = []
    for dst_idx, op in enumerate(ops):
        for dep_seq in op.get("depends_on", []):
            src_idx = seq_to_idx.get(dep_seq)
            if src_idx is not None:
                edges.append((src_idx, dst_idx))

    critical_path = compute_critical_path_from_dag(nodes, edges)

    return {
        "path": "Path A",
        "workload_id": workload_id,
        "total_cycles": critical_path,
        "critical_path_cycles": critical_path,
        "breakdown": breakdown,
        "op_count": len(ops),
        "engine_counts": engine_counts,
        "units": "cycles",
        "workload_hash": workload["content_hash"],
        "manifest_ref": str(
            REPO_ROOT / "config" / "workloads" / f"{workload_id}_perf_spec_v1.json"
        ),
    }


def evaluate_cv_workload(
    workload_id: str,
    path_b_subprocess_env: Optional[Dict[str, str]] = None,
    path_b_oracle: str = "config/func_model_workload_oracle_v1.json",
    im2col_bytes_multiplier: float = 1.0,
    dropped_depthwise: bool = False,
    inject_unknown_op: bool = False,
) -> Dict[str, Any]:
    """Run both paths for one CV workload and return the comparison result."""
    path_a = compute_path_a_result(
        workload_id,
        im2col_bytes_multiplier=im2col_bytes_multiplier,
        dropped_depthwise=dropped_depthwise,
        inject_unknown_op=inject_unknown_op,
    )

    env = (path_b_subprocess_env or {}).copy()
    env.setdefault("PYTHONPATH", "")
    cmd = [
        "python3",
        str(REPO_ROOT / "scripts" / "reduce_func_model_perf_oracle.py"),
        "--oracle",
        path_b_oracle,
        "--workload-id",
        workload_id,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=env,
        timeout=60,
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


def _combine_cv_results(results: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Combine per-workload Path A/B results for aggregate negative checks."""
    path_a: Dict[str, Any] = {
        "path": "Path A",
        "workload_id": "cv-combined",
        "total_cycles": 0,
        "breakdown": {"mxu": 0, "sfu": 0, "vector": 0, "host_only": 0},
        "op_count": 0,
        "engine_counts": {"mxu": 0, "sfu": 0, "vector": 0, "host_only": 0},
        "units": "cycles",
        "workload_hash": "",
    }
    path_b: Dict[str, Any] = {
        "path": "Path B",
        "workload_id": "cv-combined",
        "total_cycles": 0,
        "breakdown": {"mxu": 0, "sfu": 0, "vector": 0, "host_only": 0},
        "op_count": 0,
        "engine_counts": {"mxu": 0, "sfu": 0, "vector": 0, "host_only": 0},
        "units": "cycles",
        "workload_hash": "",
    }
    hash_parts: List[str] = []

    for r in results:
        a = r["path_a"]
        b = r["path_b"]
        path_a["total_cycles"] += a["total_cycles"]
        path_b["total_cycles"] += b["total_cycles"]
        path_a["op_count"] += a["op_count"]
        path_b["op_count"] += b["op_count"]
        hash_parts.append(a["workload_hash"])
        for cat in path_a["breakdown"]:
            path_a["breakdown"][cat] += a["breakdown"].get(cat, 0)
            path_b["breakdown"][cat] += b["breakdown"].get(cat, 0)
        for eng in path_a["engine_counts"]:
            path_a["engine_counts"][eng] += a["engine_counts"].get(eng, 0)
            path_b["engine_counts"][eng] += b["engine_counts"].get(eng, 0)

    path_a["workload_hash"] = "combined:" + ",".join(hash_parts)
    path_b["workload_hash"] = path_a["workload_hash"]
    return path_a, path_b


def inject_im2col_bytes_x8_fault() -> Dict[str, Any]:
    """Inflate Path A im2col bytes by 8x and assert the aggregate gate fails."""
    results = [
        evaluate_cv_workload(wid, im2col_bytes_multiplier=8.0)
        for wid in sorted(_CV_WORKLOAD_IDS)
    ]
    path_a, path_b = _combine_cv_results(results)
    comparison = compare_path_results(path_a, path_b)
    total_fail = any(
        a["id"] == "total_cycles_within_20pct" and a["result"] == "fail"
        for a in comparison["assertions"]
    )
    rejected = not comparison["passed"] and total_fail
    return {
        "fault": "im2col-bytes-x8",
        "rejected": rejected,
        "accepted": not rejected,
        "path_a_total": path_a["total_cycles"],
        "path_b_total": path_b["total_cycles"],
        "total_error_pct": comparison.get("total_error_pct"),
        "detail": "Path A im2col bytes x8 must exceed 20% total gate",
    }


def inject_dropped_depthwise_fault() -> Dict[str, Any]:
    """Drop depthwise entries from Path A and assert structural counts reject."""
    results = [
        evaluate_cv_workload(wid, dropped_depthwise=True)
        for wid in sorted(_CV_WORKLOAD_IDS)
    ]
    path_a, path_b = _combine_cv_results(results)
    comparison = compare_path_results(path_a, path_b)
    structural_fail = any(
        a["id"] in ("structural_op_count", "structural_engine_counts")
        and a["result"] == "fail"
        for a in comparison["assertions"]
    )
    rejected = not comparison["passed"] and structural_fail
    return {
        "fault": "dropped-depthwise",
        "rejected": rejected,
        "accepted": not rejected,
        "path_a_op_count": path_a["op_count"],
        "path_b_op_count": path_b["op_count"],
        "path_a_engine_counts": path_a["engine_counts"],
        "path_b_engine_counts": path_b["engine_counts"],
        "detail": "Dropped depthwise entries must change structural counts",
    }


def inject_unknown_op_fault() -> Dict[str, Any]:
    """Append an unknown-op entry to Path A and assert rejection."""
    results = [
        evaluate_cv_workload(wid, inject_unknown_op=True)
        for wid in sorted(_CV_WORKLOAD_IDS)
    ]
    path_a, path_b = _combine_cv_results(results)
    comparison = compare_path_results(path_a, path_b)
    structural_fail = any(
        a["id"] in ("structural_op_count", "structural_engine_counts", "structural_workload_hash")
        and a["result"] == "fail"
        for a in comparison["assertions"]
    )
    rejected = not comparison["passed"] and structural_fail
    return {
        "fault": "unknown-op",
        "rejected": rejected,
        "accepted": not rejected,
        "path_a_op_count": path_a["op_count"],
        "path_b_op_count": path_b["op_count"],
        "detail": "Unknown-op entry must be rejected by structural checks",
    }


def inject_path_b_decomposition_fault() -> Dict[str, Any]:
    """Mutate the combined Path B decomposition and assert the gate fails."""
    results = [evaluate_cv_workload(wid) for wid in sorted(_CV_WORKLOAD_IDS)]
    path_a, path_b = _combine_cv_results(results)
    # Mutate Path B host-only count to simulate a decomposed summary error.
    path_b["engine_counts"] = dict(path_b["engine_counts"])
    path_b["engine_counts"]["host_only"] = path_b["engine_counts"].get("host_only", 0) + 100
    path_b["op_count"] += 100
    comparison = compare_path_results(path_a, path_b)
    structural_fail = any(
        a["id"] in ("structural_op_count", "structural_engine_counts")
        and a["result"] == "fail"
        for a in comparison["assertions"]
    )
    rejected = not comparison["passed"] and structural_fail
    return {
        "fault": "path-b-decomposition",
        "rejected": rejected,
        "accepted": not rejected,
        "path_a_op_count": path_a["op_count"],
        "mutated_path_b_op_count": path_b["op_count"],
        "detail": "Mutated Path B decomposition must fail structural checks",
    }


def run_cv_paths_negative(fault_list: List[str]) -> Dict[str, Any]:
    """Run the four T17 negative fault injectors for CV dual-path gates."""
    from datetime import datetime, timezone

    fault_runners: Dict[str, Any] = {
        "im2col-bytes-x8": inject_im2col_bytes_x8_fault,
        "dropped-depthwise": inject_dropped_depthwise_fault,
        "unknown-op": inject_unknown_op_fault,
        "path-b-decomposition": inject_path_b_decomposition_fault,
    }

    report: Dict[str, Any] = {
        "test": "negative-cv-paths",
        "utc": datetime.now(timezone.utc).isoformat(),
        "requested_faults": fault_list,
        "results": {},
        "accepted": 0,
        "rejected": 0,
    }

    for fault_name in fault_list:
        runner = fault_runners.get(fault_name)
        if runner is None:
            result = {
                "fault": fault_name,
                "rejected": False,
                "accepted": True,
                "error": "Unknown fault",
            }
        else:
            result = runner()
        report["results"][fault_name] = result
        if result.get("rejected"):
            report["rejected"] += 1
        else:
            report["accepted"] += 1

    report["all_passed"] = report["accepted"] == 0 and report["rejected"] == len(fault_list)
    report["verdict"] = "pass" if report["all_passed"] else "fail"
    return report
