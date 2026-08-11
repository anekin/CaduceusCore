"""T19 cross-model scaling report builder.

Generates report-only decode-1 workloads for Qwen2.5-1.5B/3B/7B from a single
builder path, using the canonical 17-op layer template from
``config/workloads/qwen25_3b_perf_spec_v1.json`` and model-specific dimensions
from ``sim.model_specs``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from model_specs import get_spec, ModelSpec
from timing.timing_engine import compute_critical_path_from_dag


REPO_ROOT = Path(__file__).resolve().parents[2]
_QWEN_MANIFEST_PATH = REPO_ROOT / "config" / "workloads" / "qwen25_3b_perf_spec_v1.json"

# Architectural constants aligned with T16 qwen_spec_gates.
_ARRAY_H = 64
_ARRAY_W = 64
_BW_BPC = 43.52  # LPDDR5-6400 64-bit effective bytes/cycle
_WEIGHT_BYTES_PER_ELEM = 0.5  # INT4 weights
_SFU_WIDTH = 128
_VECTOR_WIDTH = 128

_SFU_PIPELINE_DEPTHS = {
    "softmax": 227,
    "layernorm": 210,
    "rmsnorm": 150,
    "gelu": 71,
    "silu": 72,
    "rope": 82,
}

_SFU_NORM_OPS = frozenset({"softmax", "layernorm", "rmsnorm"})

_VECTOR_OP_LATENCIES = {
    "add": 5,
    "mul": 5,
    "max": 12,
    "sum": 12,
    "conv": 260,
    "resid": 5,
}


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def _load_manifest() -> Dict[str, Any]:
    with open(_QWEN_MANIFEST_PATH, "r") as f:
        return json.load(f)


def _eval_expr(expr: str, variant: Dict[str, Any]) -> int:
    """Evaluate a simple integer expression using variant tokens."""
    expr_clean = expr.replace(" ", "")
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


def _resolve_shape(formula: str, variant: Dict[str, Any]) -> Dict[str, int]:
    """Resolve 'M=batch_m, K=hidden, N=heads*head_dim' into concrete ints."""
    result: Dict[str, int] = {}
    if not formula.strip():
        return result
    for part in (p.strip() for p in formula.split(",")):
        if "=" not in part:
            continue
        key, expr = part.split("=", 1)
        result[key.strip()] = _eval_expr(expr, variant)
    return result


def _compute_model_weight_bytes(spec: ModelSpec) -> int:
    """Frozen INT4 weight-byte estimate from model spec.

    Per-layer params = 2 * hidden * head_dim * (num_heads + kv_heads)
                       + 3 * hidden * intermediate
    Total params = layers * per_layer_params
    Weight bytes = total_params * 0.5
    """
    per_layer = (
        2 * spec.hidden * spec.head_dim * (spec.num_heads + spec.kv_heads)
        + 3 * spec.hidden * spec.intermediate
    )
    total_params = spec.layers * per_layer
    return int(total_params * _WEIGHT_BYTES_PER_ELEM)


def _mxu_decode_cycles(
    M: int, K: int, N: int,
    array_H: int = _ARRAY_H, array_W: int = _ARRAY_W,
    bw_bpc: float = _BW_BPC,
) -> int:
    """T16 MXU decode cycle estimate for one MXU op."""
    K_tiles = _ceil_div(K, array_H)
    N_tiles = _ceil_div(N, array_W)
    total_tiles = K_tiles * N_tiles
    per_tile_compute = array_H * (M + 1) + array_W
    tile_weight_bytes = array_H * array_W * _WEIGHT_BYTES_PER_ELEM
    tile_act_bytes = M * array_H
    per_tile_dma = (tile_weight_bytes + tile_act_bytes) / bw_bpc if bw_bpc > 0 else float("inf")
    first_tile_cold = per_tile_compute + per_tile_dma
    bottleneck = max(per_tile_compute, per_tile_dma)
    if M >= array_H:
        per_tile_compute = array_H + array_W + array_H
    raw = first_tile_cold + (total_tiles - 1) * bottleneck
    return math.ceil(raw)


def _mxu_decode_dma_cycles(
    M: int, K: int, N: int,
    array_H: int = _ARRAY_H, array_W: int = _ARRAY_W,
    bw_bpc: float = _BW_BPC,
) -> int:
    """Memory-bound DMA cycles for one MXU op (total weight+activation bytes / BW)."""
    if K == 0 or N == 0:
        return 0
    K_tiles = _ceil_div(K, array_H)
    N_tiles = _ceil_div(N, array_W)
    total_tiles = K_tiles * N_tiles
    tile_weight_bytes = array_H * array_W * _WEIGHT_BYTES_PER_ELEM
    tile_act_bytes = M * array_H
    per_tile_dma = (tile_weight_bytes + tile_act_bytes) / bw_bpc if bw_bpc > 0 else 0.0
    return math.ceil(total_tiles * per_tile_dma)


def _sfu_cycles(op: str, elements: int) -> int:
    """T16/T9 SFU cycle estimate with normalization scaling for small dims."""
    if elements == 0:
        return 0
    depth = _SFU_PIPELINE_DEPTHS.get(op, 100)
    batches = _ceil_div(elements, _SFU_WIDTH)
    batch_elements = elements / batches
    if op in _SFU_NORM_OPS and batch_elements < 64:
        effective_depth = math.ceil(depth * batch_elements / 64)
    else:
        effective_depth = depth
    return effective_depth * batches


def _vector_cycles(op: str, dim: int) -> int:
    """T16 Vector cycle estimate."""
    if dim == 0:
        return 0
    latency = _VECTOR_OP_LATENCIES.get(op, 5)
    batches = _ceil_div(dim, _VECTOR_WIDTH)
    return latency * batches


def _build_decode_variant(spec: ModelSpec) -> Dict[str, Any]:
    """Build a decode-1 variant dict from a ModelSpec."""
    return {
        "workload_id": f"{spec.name}-decode-1",
        "batch_m": 1,
        "prompt_len": 1,
        "context_len": 1,
        "layer_count": 1,
        "hidden": spec.hidden,
        "intermediate": spec.intermediate,
        "heads": spec.num_heads,
        "kv_heads": spec.kv_heads,
        "head_dim": spec.head_dim,
    }


def _build_model_decode_workload(spec: ModelSpec) -> Dict[str, Any]:
    """Load the canonical Qwen layer template and resolve shapes for ``spec``."""
    manifest = _load_manifest()
    variant = _build_decode_variant(spec)
    ops_src = manifest.get("ops", [])

    ops: List[Dict[str, Any]] = []
    for op in ops_src:
        shape = _resolve_shape(op.get("shape_formula", ""), variant)
        ops.append({
            "op_id": op["op_id"],
            "seq": op["seq"],
            "name": op["name"],
            "engine": op["engine"],
            "shape": shape,
        })

    return {
        "workload_id": variant["workload_id"],
        "variant": variant,
        "ops": ops,
        "layer_count": spec.layers,
        "dependency_edges": manifest.get("dependency_edges", {}),
    }


def _compute_layer_cycles(workload: Dict[str, Any]) -> Tuple[int, int]:
    """Return (critical_path_cycles, memory_bound_cycles) for one layer."""
    ops = workload["ops"]
    op_idx = {op["op_id"]: i for i, op in enumerate(ops)}
    nodes: List[Dict[str, Any]] = []
    memory_bound_cycles = 0

    for op in ops:
        name = op["name"]
        engine = op["engine"]
        shape = op["shape"]

        if engine == "mxu":
            M = shape.get("M", 1)
            K = shape.get("K", 0)
            N = shape.get("N", 0)
            cycles = _mxu_decode_cycles(M, K, N)
            memory_bound_cycles += _mxu_decode_dma_cycles(M, K, N)
        elif engine == "sfu":
            elements = shape.get("elements", 0)
            sfu_op = name.lower()
            if sfu_op.startswith("rmsnorm"):
                sfu_op = "rmsnorm"
            elif sfu_op == "silu":
                sfu_op = "silu"
            elif sfu_op == "softmax":
                sfu_op = "softmax"
            elif sfu_op == "rope":
                sfu_op = "rope"
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

    edges: List[Tuple[int, int]] = []
    for src_id, dst_ids in workload["dependency_edges"].items():
        if src_id not in op_idx:
            continue
        for dst_id in dst_ids:
            if dst_id in op_idx:
                edges.append((op_idx[src_id], op_idx[dst_id]))

    critical_path = compute_critical_path_from_dag(nodes, edges)
    return critical_path, memory_bound_cycles


def _build_model_report(spec: ModelSpec) -> Dict[str, Any]:
    """Build a single model scaling report."""
    workload = _build_model_decode_workload(spec)
    per_layer_cycles, per_layer_memory = _compute_layer_cycles(workload)
    total_decode_cycles = per_layer_cycles * spec.layers
    memory_bound_cycles = per_layer_memory * spec.layers
    weight_bytes = _compute_model_weight_bytes(spec)

    return {
        "model": spec.name,
        "workload_id": workload["workload_id"],
        "hidden": spec.hidden,
        "intermediate": spec.intermediate,
        "layers": spec.layers,
        "num_heads": spec.num_heads,
        "kv_heads": spec.kv_heads,
        "head_dim": spec.head_dim,
        "weight_bytes": weight_bytes,
        "per_layer_decode_cycles": per_layer_cycles,
        "total_decode_cycles": total_decode_cycles,
        "memory_bound_cycles": memory_bound_cycles,
        "memory_bound_per_weight_byte": (
            memory_bound_cycles / weight_bytes if weight_bytes else 0.0
        ),
        "report_only": True,
        "assumptions": {
            "weight_precision": "INT4",
            "weight_bytes_per_param": _WEIGHT_BYTES_PER_ELEM,
            "context_len": 1,
            "batch_m": 1,
            "mxu_array": f"{_ARRAY_H}x{_ARRAY_W}",
            "effective_dram_bw_bytes_per_cycle": _BW_BPC,
            "cycle_model": "T16 qwen_spec_gates decode formula",
            "param_formula": "layers * (2*hidden*head_dim*(num_heads+kv_heads) + 3*hidden*intermediate)",
        },
    }


def build_scaling_report(
    models: Optional[List[str]] = None,
    swap_models: Optional[Tuple[str, str]] = None,
    kpi_target_gate: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a cross-model scaling report.

    Args:
        models: List of model aliases from ``sim.model_specs``. Defaults to the
            T19 Qwen family (1.5B, 3B, 7B).
        swap_models: Optional pair of model aliases whose hidden/layers are
            swapped for negative testing.
        kpi_target_gate: Optional product KPI target dict. If provided, the
            report is rejected because T19 reports must be ``report_only=true``.

    Returns:
        Structured report dict with ``models`` list and ``verdict``/assertions.
    """
    if models is None:
        models = ["qwen2.5-1.5b", "qwen2.5-3b", "qwen2.5-7b"]

    specs = [get_spec(alias) for alias in models]

    if swap_models is not None:
        a, b = swap_models
        idx_a = models.index(a)
        idx_b = models.index(b)
        spec_a = specs[idx_a]
        spec_b = specs[idx_b]
        specs[idx_a] = ModelSpec(
            name=spec_a.name,
            qkv_dim=spec_b.qkv_dim,
            hidden=spec_b.hidden,
            intermediate=spec_a.intermediate,
            layers=spec_b.layers,
            num_heads=spec_a.num_heads,
            kv_heads=spec_a.kv_heads,
            head_dim=spec_a.head_dim,
        )
        specs[idx_b] = ModelSpec(
            name=spec_b.name,
            qkv_dim=spec_a.qkv_dim,
            hidden=spec_a.hidden,
            intermediate=spec_b.intermediate,
            layers=spec_a.layers,
            num_heads=spec_b.num_heads,
            kv_heads=spec_b.kv_heads,
            head_dim=spec_b.head_dim,
        )

    reports = [_build_model_report(spec) for spec in specs]

    assertions: List[Dict[str, Any]] = []

    def _assert(id_: str, ok: bool, detail: str) -> None:
        assertions.append({"id": id_, "result": "pass" if ok else "fail", "detail": detail})

    # Monotonicity: weight_bytes and total_decode_cycles must strictly increase.
    weight_bytes = [r["weight_bytes"] for r in reports]
    total_cycles = [r["total_decode_cycles"] for r in reports]

    weights_increasing = all(weight_bytes[i] < weight_bytes[i + 1] for i in range(len(weight_bytes) - 1))
    cycles_increasing = all(total_cycles[i] < total_cycles[i + 1] for i in range(len(total_cycles) - 1))

    _assert(
        "weight_bytes_monotonic",
        weights_increasing,
        f"weight_bytes={weight_bytes}",
    )
    _assert(
        "total_decode_cycles_monotonic",
        cycles_increasing,
        f"total_decode_cycles={total_cycles}",
    )

    # Normalized memory-bound cycles per weight byte: adjacent deltas <= 20%.
    ratios = [r["memory_bound_per_weight_byte"] for r in reports]
    ratio_deltas = [
        abs(ratios[i + 1] - ratios[i]) / max(ratios[i], 1e-12) * 100.0
        for i in range(len(ratios) - 1)
    ]
    ratio_ok = all(d <= 20.0 for d in ratio_deltas)
    _assert(
        "memory_bound_per_weight_byte_delta_within_20pct",
        ratio_ok,
        f"ratios={ratios}, deltas(%)={ratio_deltas}",
    )

    # Every report must be report_only=true.
    all_report_only = all(r.get("report_only") is True for r in reports)
    _assert(
        "all_reports_report_only",
        all_report_only,
        "all scaling reports must carry report_only=true",
    )

    # Product KPI hard gates must be rejected in the GREEN path.
    kpi_rejected = False
    if kpi_target_gate is not None:
        kpi_rejected = True
        _assert(
            "kpi_target_gate_rejected",
            False,
            f"product KPI hard gate not allowed: {kpi_target_gate}",
        )

    passed = all(a["result"] == "pass" for a in assertions)

    return {
        "command": "run",
        "case": "qwen-scaling-1p5b-3b-7b",
        "report_only": True,
        "models": reports,
        "assertions": assertions,
        "kpi_target_gate": kpi_target_gate,
        "kpi_target_gate_rejected": kpi_rejected,
        "passed": passed,
        "verdict": "pass" if passed else "fail",
    }


def _inject_swapped_model_params() -> Dict[str, Any]:
    """Swap 1.5B and 7B hidden/layers and assert monotonicity fails."""
    report = build_scaling_report(
        models=["qwen2.5-1.5b", "qwen2.5-3b", "qwen2.5-7b"],
        swap_models=("qwen2.5-1.5b", "qwen2.5-7b"),
    )
    monotonic_fail = any(
        a["id"].endswith("_monotonic") and a["result"] == "fail"
        for a in report["assertions"]
    )
    rejected = report["verdict"] == "fail" and monotonic_fail
    return {
        "fault": "swapped-model-params",
        "rejected": rejected,
        "accepted": not rejected,
        "detail": "swapped hidden/layers must break monotonic ordering",
        "assertions": report["assertions"],
    }


def _inject_kpi_target_gate() -> Dict[str, Any]:
    """Inject a fake product KPI target and assert it is rejected."""
    report = build_scaling_report(
        models=["qwen2.5-1.5b", "qwen2.5-3b", "qwen2.5-7b"],
        kpi_target_gate={"metric": "decode_tps", "operator": ">=", "value": 100},
    )
    rejected = (
        report.get("kpi_target_gate_rejected") is True
        and report["verdict"] == "fail"
    )
    return {
        "fault": "kpi-target-gate",
        "rejected": rejected,
        "accepted": not rejected,
        "detail": "product KPI hard gate must be rejected (report_only=true required)",
        "assertions": report["assertions"],
    }


def run_model_scaling_negative(faults: List[str]) -> Dict[str, Any]:
    """Run T19 model-scaling negative fault injectors.

    Args:
        faults: List of fault names (currently ``swapped-model-params``,
            ``kpi-target-gate``).

    Returns:
        Structured report dict with accepted/rejected counts.
    """
    report: Dict[str, Any] = {
        "test": "model-scaling",
        "utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "requested_faults": faults,
        "results": {},
        "accepted": 0,
        "rejected": 0,
    }

    fault_runners: Dict[str, Any] = {
        "swapped-model-params": _inject_swapped_model_params,
        "kpi-target-gate": _inject_kpi_target_gate,
    }

    for fault_name in faults:
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

    report["all_passed"] = report["accepted"] == 0 and report["rejected"] == len(faults)
    report["verdict"] = "pass" if report["all_passed"] else "fail"
    return report
