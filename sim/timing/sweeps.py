"""Monotonicity and bottleneck-transition sweep runner for T18."""

from __future__ import annotations

import copy
import json
import math
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from model_specs import ModelSpec, get_spec
from timing.metrics import MetricsCollector
from timing.timing_engine import TimingEngine

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = REPO_ROOT / "sim" / "config" / "npu_config.yaml"
DEFAULT_MATRIX_PATH = REPO_ROOT / "config" / "func_model_perf_matrix_v1.json"

RESOURCE_DIMS = {"bandwidth", "array", "dma_channels", "noc_hop"}
WORKLOAD_DIMS = {"prompt", "context"}

SWEEP_ALIASES: Dict[str, str] = {
    "dma-channels": "dma_channels",
    "noc-hop": "noc_hop",
}

DIM_MAP: Dict[str, Dict[str, Any]] = {
    "bandwidth": {
        "kind": "config",
        "overrides": {
            "memory.bandwidth_gbps": None,
            "memory.bandwidth_bytes_per_cycle": None,
        },
    },
    "array": {
        "kind": "config",
        "overrides": {
            "mxu.array_height": None,
            "mxu.array_width": None,
        },
    },
    "dma_channels": {
        "kind": "config",
        "overrides": {
            "dma.num_channels": None,
            "dma.channels": None,
        },
    },
    "noc_hop": {
        "kind": "config",
        "overrides": {"interconnect.hop_latency_cycles": None},
    },
    "prompt": {
        "kind": "workload",
        "metric": "prefill_cycles",
        "direction": "mono-increasing",
    },
    "context": {
        "kind": "workload",
        "metric": "decode_cycles",
        "direction": "mono-increasing",
        "config_override": {"kv_cache.total_tokens": None},
    },
}

ENDPOINT_CONFIGS: Dict[str, Dict[str, Any]] = {
    "memory": {
        "overrides": {
            "memory.bandwidth_gbps": 6.4,
            "memory.bandwidth_bytes_per_cycle": 6.4,
            "mxu.array_height": 128,
            "mxu.array_width": 128,
        },
        "check": "dram_bw_share_pct",
        "threshold": 55.0,
    },
    "compute": {
        "overrides": {
            "memory.bandwidth_gbps": 102.4,
            "memory.bandwidth_bytes_per_cycle": 102.4,
            "mxu.array_height": 32,
            "mxu.array_width": 32,
        },
        "check": "mxu_utilization_pct",
        "threshold": 55.0,
    },
}


def _clone_config_engine(
    base_config: Dict[str, Any],
    overrides: Dict[str, Any],
) -> TimingEngine:
    cfg = copy.deepcopy(base_config)
    for key, val in overrides.items():
        section, sub = key.split(".", 1)
        cfg.setdefault(section, {})[sub] = val
    tf = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
    yaml.dump(cfg, tf)
    tf.close()
    tmp = tf.name
    try:
        return TimingEngine(tmp)
    finally:
        Path(tmp).unlink(missing_ok=True)


def _compute_utilization(timing: Any) -> Dict[str, float]:
    mb = timing.module_breakdown.cycles
    total = timing.total_cycles
    mu = MetricsCollector.compute_module_utilization(mb, total)
    dram_eff = mb.get("dma_weight", 0) + mb.get("dma_effective", 0)
    share_denom = dram_eff + mb.get("noc_latency", 0)
    dram_share = (dram_eff / share_denom * 100.0) if share_denom > 0 else 0.0
    return {
        "mxu_utilization_pct": round(mu.get("mxu", 0.0), 2),
        "dram_bw_share_pct": round(dram_share, 2),
    }


def _run_point(
    engine: TimingEngine,
    spec: ModelSpec,
    dim: str,
    value: Any,
) -> Dict[str, Any]:
    if dim == "prompt":
        metrics = engine.simulate_request(spec, prompt_len=int(value), gen_len=1)
        row: Dict[str, Any] = {
            dim: value,
            "prefill_cycles": metrics.prefill_cycles,
            "decode_cycles": metrics.decode_cycles_per_token[0]
            if metrics.decode_cycles_per_token
            else 0,
        }
    elif dim == "context":
        timing = engine.simulate_decode(spec, prompt_len=1)
        row = {
            dim: value,
            "decode_cycles": timing.total_cycles,
        }
    else:
        timing = engine.simulate_decode(spec, prompt_len=1)
        row = {
            dim: value,
            "total_cycles": timing.total_cycles,
        }
    if dim != "prompt":
        timing = engine.simulate_decode(spec, prompt_len=1)
        row.update(_compute_utilization(timing))
    else:
        prefill = engine.simulate_prefill(spec, prompt_len=int(value))
        row.update(_compute_utilization(prefill))
    return row


def _metric_for(dim: str) -> Tuple[str, int]:
    if dim == "prompt":
        return "prefill_cycles", 1
    if dim == "context":
        return "decode_cycles", 1
    return "total_cycles", -1


def _check_monotonic(
    rows: List[Dict[str, Any]],
    dim: str,
    strict: bool = True,
) -> List[Dict[str, Any]]:
    metric, direction = _metric_for(dim)
    failures: List[Dict[str, Any]] = []
    zero_count = 0
    for prev, curr in zip(rows, rows[1:]):
        delta = curr[metric] - prev[metric]
        if not math.isfinite(delta):
            failures.append({"fault": "nan-slope", "dim": dim, "from": prev[dim], "to": curr[dim]})
            continue
        if delta == 0:
            zero_count += 1
            continue
        if delta * direction < 0:
            failures.append({
                "fault": "resource-positive-slope" if direction < 0 else "workload-negative-slope",
                "dim": dim,
                "from": prev[dim],
                "to": curr[dim],
                "delta": delta,
            })
    if strict and len(rows) > 1 and zero_count == len(rows) - 1:
        failures.append({"fault": "unreachable-transition", "dim": dim})
    return failures


def _check_endpoints(
    base_config: Dict[str, Any],
    spec: ModelSpec,
    required: List[str],
    endpoint_configs: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    configs = endpoint_configs if endpoint_configs is not None else ENDPOINT_CONFIGS
    results: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    for ep in required:
        cfg = configs.get(ep)
        if cfg is None:
            failures.append({"fault": "missing-6p4-endpoint", "endpoint": ep})
            continue
        engine = _clone_config_engine(base_config, cfg["overrides"])
        timing = engine.simulate_decode(spec, prompt_len=1)
        util = _compute_utilization(timing)
        value = util[cfg["check"]]
        passed = value >= cfg["threshold"]
        results.append(
            {
                "endpoint": ep,
                "check": cfg["check"],
                "value": value,
                "threshold": cfg["threshold"],
                "passed": passed,
            }
        )
        if not passed:
            failures.append(
                {
                    "fault": "missing-6p4-endpoint" if ep == "memory" else "unreachable-transition",
                    "endpoint": ep,
                    "value": value,
                    "threshold": cfg["threshold"],
                }
            )
    return results, failures


def run_sweeps(
    sweep_ids: List[str],
    require_endpoints: Optional[List[str]] = None,
    spec_alias: str = "qwen2.5-3b",
    config_path: Optional[Path] = None,
    matrix_path: Optional[Path] = None,
) -> Dict[str, Any]:
    require_endpoints = require_endpoints or []
    config_path = config_path or DEFAULT_CONFIG_PATH
    matrix_path = matrix_path or DEFAULT_MATRIX_PATH

    with open(config_path) as f:
        base_config = yaml.safe_load(f)
    with open(matrix_path) as f:
        matrix = json.load(f)

    grids = {g["sweep_id"]: g for g in matrix["sweep_grids"]["grids"]}
    spec = get_spec(spec_alias)

    report: Dict[str, Any] = {
        "command": "run",
        "spec": spec_alias,
        "sweeps": {},
        "endpoints": [],
        "monotonicity_failures": [],
        "endpoint_failures": [],
    }

    for sid in sweep_ids:
        canonical = SWEEP_ALIASES.get(sid, sid.replace("-", "_"))
        grid = grids.get(canonical)
        if grid is None:
            report["monotonicity_failures"].append(
                {"fault": "unknown-sweep", "dim": sid}
            )
            continue
        dim_info = DIM_MAP.get(canonical)
        if dim_info is None:
            report["monotonicity_failures"].append(
                {"fault": "unknown-sweep", "dim": sid}
            )
            continue

        rows: List[Dict[str, Any]] = []
        for value in grid["values"]:
            overrides: Dict[str, Any] = {}
            if dim_info["kind"] == "config":
                for key in dim_info["overrides"]:
                    overrides[key] = value
            elif dim_info.get("config_override"):
                for key in dim_info["config_override"]:
                    overrides[key] = value
            engine = _clone_config_engine(base_config, overrides)
            row = _run_point(engine, spec, sid, value)
            rows.append(row)

        failures = _check_monotonic(rows, canonical, strict=False)
        report["monotonicity_failures"].extend(
            {**f, "dim": sid} for f in failures
        )
        report["sweeps"][sid] = {
            "direction": grid.get("monotonicity_direction"),
            "unit": grid.get("unit"),
            "rows": rows,
            "failures": [{**f, "dim": sid} for f in failures],
        }

    if require_endpoints:
        ep_results, ep_failures = _check_endpoints(base_config, spec, require_endpoints)
        report["endpoints"] = ep_results
        report["endpoint_failures"].extend(ep_failures)

    all_failures = report["monotonicity_failures"] + report["endpoint_failures"]
    report["verdict"] = "fail" if all_failures else "pass"
    return report


def _inject_fault(rows: List[Dict[str, Any]], fault: str, dim: str) -> List[Dict[str, Any]]:
    metric, _ = _metric_for(dim)
    corrupted = copy.deepcopy(rows)
    if not corrupted:
        return corrupted
    if fault == "resource-positive-slope":
        for i in range(len(corrupted) - 1):
            corrupted[i + 1][metric] = corrupted[i][metric] + max(1, corrupted[i][metric] // 10)
    elif fault == "workload-negative-slope":
        for i in range(len(corrupted) - 1):
            corrupted[i + 1][metric] = corrupted[i][metric] - max(1, corrupted[i][metric] // 10)
            if corrupted[i + 1][metric] < 0:
                corrupted[i + 1][metric] = 0
    elif fault == "nan-slope":
        corrupted[-1][metric] = float("nan")
    elif fault == "missing-6p4-endpoint":
        for row in corrupted:
            row["dram_bw_share_pct"] = 0.0
            row["mxu_utilization_pct"] = 0.0
    elif fault == "unreachable-transition":
        for row in corrupted:
            row[metric] = corrupted[0][metric]
    return corrupted


def run_negative_sweeps(
    faults: List[str],
    sweep_ids: Optional[List[str]] = None,
    spec_alias: str = "qwen2.5-3b",
    config_path: Optional[Path] = None,
    matrix_path: Optional[Path] = None,
) -> Dict[str, Any]:
    sweep_ids = sweep_ids or ["bandwidth", "prompt"]
    config_path = config_path or DEFAULT_CONFIG_PATH
    matrix_path = matrix_path or DEFAULT_MATRIX_PATH

    with open(config_path) as f:
        base_config = yaml.safe_load(f)
    with open(matrix_path) as f:
        matrix = json.load(f)

    grids = {g["sweep_id"]: g for g in matrix["sweep_grids"]["grids"]}
    spec = get_spec(spec_alias)

    results: List[Dict[str, Any]] = []
    rejected = 0
    accepted = 0

    for fault in faults:
        dim = "bandwidth" if fault in ("resource-positive-slope", "nan-slope", "missing-6p4-endpoint", "unreachable-transition") else "prompt"
        grid = grids[dim]
        dim_info = DIM_MAP[dim]
        rows: List[Dict[str, Any]] = []
        for value in grid["values"]:
            overrides: Dict[str, Any] = {}
            if dim_info["kind"] == "config":
                for key in dim_info["overrides"]:
                    overrides[key] = value
            engine = _clone_config_engine(base_config, overrides)
            rows.append(_run_point(engine, spec, dim, value))

        corrupted = _inject_fault(rows, fault, dim)
        failures = _check_monotonic(corrupted, dim)
        if fault in ("missing-6p4-endpoint", "unreachable-transition"):
            if fault == "missing-6p4-endpoint":
                mem_cfg = dict(ENDPOINT_CONFIGS["memory"])
                mem_cfg["overrides"] = dict(ENDPOINT_CONFIGS["compute"]["overrides"])
                ep_results, ep_failures = _check_endpoints(
                    base_config,
                    spec,
                    ["memory"],
                    endpoint_configs={"memory": mem_cfg, "compute": ENDPOINT_CONFIGS["compute"]},
                )
            else:
                _, ep_failures = _check_endpoints(base_config, spec, ["memory", "compute"])
            failures.extend(ep_failures)

        detected = bool(failures)
        if detected:
            rejected += 1
        else:
            accepted += 1
        results.append(
            {
                "fault": fault,
                "dim": dim,
                "detected": detected,
                "failures": failures,
            }
        )

    return {
        "command": "negative",
        "case": "sweeps",
        "faults": faults,
        "rejected": rejected,
        "accepted": accepted,
        "results": results,
        "verdict": "pass" if accepted == 0 else "fail",
    }
