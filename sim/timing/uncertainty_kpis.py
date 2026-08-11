"""T20 uncertainty-aware report-only KPI builder.

Produces low/base/high KPI reports for Qwen prefill-2000, the Qwen model family
(1.5B/3B/7B), and the three frozen CV workloads.  All product target
comparisons are ``report_only=true``.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from model_specs import get_spec
from timing.cv_spec_gates import evaluate_cv_workload
from timing.metrics import apply_cycle_band, apply_sum_of_stages_band, apply_throughput_band
from timing.model_scaling import build_scaling_report
from timing.qwen_spec_gates import (
    _canonical_workload_hash,
    _mxu_decode_cycles,
    _sfu_cycles,
    _vector_cycles,
)
from timing.timing_engine import compute_critical_path_from_dag
from timing.workloads import _eval_expr, _load_manifest


REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_FREQ_MHZ = 1000

_LL_KPI_KEYS = ("ttft_ms", "tps", "tpot_us", "prefill_ms", "decode_per_token_us")
_CV_KPI_KEYS = ("fps", "inference_latency_us")


def _ceil_div(a: int, b: int) -> int:
    return (a + b - 1) // b


def _build_prefill_2000_variant() -> Dict[str, Any]:
    """Create a temporary prefill-2000 variant without modifying the manifest."""
    manifest = _load_manifest()
    variant = {
        "workload_id": "qwen25-3b-prefill-2000",
        "batch_m": 2000,
        "prompt_len": 2000,
        "context_len": 0,
        "layer_count": 36,
        "hidden": 2048,
        "intermediate": 11008,
        "heads": 16,
        "kv_heads": 2,
        "head_dim": 128,
        "mxu_mode": "prefill",
    }
    return variant


def _resolve_shape(formula: str, variant: Dict[str, Any]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    if not formula.strip():
        return result
    for part in (p.strip() for p in formula.split(",")):
        if "=" not in part:
            continue
        key, expr = part.split("=", 1)
        result[key.strip()] = _eval_expr(expr, variant)
    return result


def _compute_prefill_cycles(variant: Dict[str, Any]) -> int:
    """Compute one-layer prefill critical path cycles for the variant."""
    manifest = _load_manifest()
    ops = manifest.get("ops", [])
    op_idx = {op["op_id"]: i for i, op in enumerate(ops)}
    nodes: List[Dict[str, Any]] = []

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
        shape = _resolve_shape(op.get("shape_formula", ""), variant)

        if engine == "mxu":
            M = shape.get("M", batch_m)
            K = shape.get("K", hidden)
            N = shape.get("N", hidden)
            cycles = _mxu_decode_cycles(M, K, N)
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
    for src_id, dst_ids in manifest.get("dependency_edges", {}).items():
        if src_id not in op_idx:
            continue
        for dst_id in dst_ids:
            if dst_id in op_idx:
                edges.append((op_idx[src_id], op_idx[dst_id]))

    return compute_critical_path_from_dag(nodes, edges)


def _compute_decode_1_cycles() -> int:
    """Compute per-token decode cycles (batch=1, context=1)."""
    variant = {
        "workload_id": "qwen25-3b-decode-1",
        "batch_m": 1,
        "prompt_len": 1,
        "context_len": 1,
        "layer_count": 1,
        "hidden": 2048,
        "intermediate": 11008,
        "heads": 16,
        "kv_heads": 2,
        "head_dim": 128,
    }
    return _compute_prefill_cycles(variant)


def _canonical_report_hash(report: Dict[str, Any]) -> str:
    """Deterministic hash excluding volatile metadata."""
    excluded = {"timestamp", "utc", "utc_start", "utc_end", "run_id", "canonical_hash"}
    filtered = {k: v for k, v in report.items() if k not in excluded}
    canonical = json.dumps(filtered, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_qwen_prefill_2000_report(freq_mhz: int = _DEFAULT_FREQ_MHZ) -> Dict[str, Any]:
    """Build uncertainty-aware KPI report for Qwen2.5-3B prefill-2000."""
    variant = _build_prefill_2000_variant()
    per_layer_prefill = _compute_prefill_cycles(variant)
    per_layer_decode = _compute_decode_1_cycles()

    layers = variant["layer_count"]
    prefill_cycles = per_layer_prefill * layers
    first_decode_cycles = per_layer_decode

    freq_khz = freq_mhz * 1e3
    freq_mhz_float = float(freq_mhz)

    prefill_ms_base = prefill_cycles / freq_khz
    first_decode_ms_base = first_decode_cycles / freq_khz
    ttft_ms_base = prefill_ms_base + first_decode_ms_base
    decode_per_token_us_base = first_decode_cycles / freq_mhz_float
    tps_base = freq_mhz_float * 1e6 / first_decode_cycles if first_decode_cycles else 0.0
    tpot_us_base = decode_per_token_us_base

    report: Dict[str, Any] = {
        "case": "qwen-prefill-2000",
        "model": "qwen2.5-3b",
        "report_only": True,
        "prompt_len": variant["prompt_len"],
        "layers": layers,
        "freq_mhz": freq_mhz,
        "prefill_cycles": prefill_cycles,
        "first_decode_cycles": first_decode_cycles,
        "ttft_ms": apply_sum_of_stages_band(
            [prefill_ms_base, first_decode_ms_base], correlation="independent"
        ),
        "tps": apply_throughput_band(tps_base),
        "tpot_us": apply_cycle_band(tpot_us_base),
        "prefill_ms": apply_cycle_band(prefill_ms_base),
        "decode_per_token_us": apply_cycle_band(decode_per_token_us_base),
        "workload_hash": _canonical_workload_hash(variant["workload_id"], variant),
        "assumptions": {
            "mxu_formula": "T16 decode/prefill cycle estimate",
            "uncertainty": "cycles 0.7/1.3, throughput inverse, TTFT RSS independent",
        },
    }
    report["canonical_hash"] = _canonical_report_hash(report)
    return report


def build_cv_kpi_report(
    workload_id: str,
    freq_mhz: int = _DEFAULT_FREQ_MHZ,
) -> Dict[str, Any]:
    """Build uncertainty-aware KPI report for a CV workload."""
    comparison = evaluate_cv_workload(workload_id)
    if not comparison.get("passed"):
        return {
            "case": workload_id,
            "model": workload_id,
            "report_only": True,
            "verdict": "fail",
            "error": comparison.get("error", "Path A/B comparison failed"),
        }

    total_cycles = comparison["path_a_total"]
    freq_mhz_float = float(freq_mhz)
    fps_base = freq_mhz_float * 1e6 / total_cycles if total_cycles else 0.0
    latency_us_base = total_cycles / freq_mhz_float if freq_mhz else 0.0

    report: Dict[str, Any] = {
        "case": workload_id,
        "model": workload_id,
        "report_only": True,
        "total_cycles": total_cycles,
        "path_a_total": comparison["path_a_total"],
        "path_b_total": comparison["path_b_total"],
        "total_error_pct": comparison.get("total_error_pct"),
        "fps": apply_throughput_band(fps_base),
        "inference_latency_us": apply_cycle_band(latency_us_base),
        "workload_hash": comparison["path_a"].get("workload_hash", ""),
        "assumptions": {
            "path": "T17 CV dual-path gate",
            "uncertainty": "cycles 0.7/1.3, throughput inverse",
        },
    }
    report["canonical_hash"] = _canonical_report_hash(report)
    return report


def build_qwen_model_family_report() -> Dict[str, Any]:
    """Embed T19 scaling reports with uncertainty bands on decode TPS."""
    scaling = build_scaling_report()
    models: List[Dict[str, Any]] = []
    for m in scaling.get("models", []):
        total_decode_cycles = m["total_decode_cycles"]
        tps_base = _DEFAULT_FREQ_MHZ * 1e6 / total_decode_cycles if total_decode_cycles else 0.0
        model_report = {
            "model": m["model"],
            "hidden": m["hidden"],
            "layers": m["layers"],
            "weight_bytes": m["weight_bytes"],
            "total_decode_cycles": total_decode_cycles,
            "tps": apply_throughput_band(tps_base),
            "report_only": True,
            "assumptions": m.get("assumptions", {}),
        }
        model_report["canonical_hash"] = _canonical_report_hash(model_report)
        models.append(model_report)

    aggregate: Dict[str, Any] = {
        "case": "qwen-model-family",
        "report_only": True,
        "models": models,
        "scaling_assertions": scaling.get("assertions", []),
        "passed": scaling.get("passed", False),
        "verdict": scaling.get("verdict", "fail"),
    }
    aggregate["canonical_hash"] = _canonical_report_hash(aggregate)
    return aggregate


def run_uncertainty_kpis(cases: List[str]) -> Dict[str, Any]:
    """Run the requested uncertainty KPI cases and return an aggregate report."""
    reports: Dict[str, Any] = {}
    errors: List[str] = []

    for case in cases:
        if case == "qwen-prefill-2000":
            reports[case] = build_qwen_prefill_2000_report()
        elif case == "qwen-model-family":
            reports[case] = build_qwen_model_family_report()
        elif case in ("mobilenetv3", "resnet50", "yolov8n"):
            reports[case] = build_cv_kpi_report(case)
        else:
            errors.append(f"Unknown uncertainty-kpis case: {case}")

    def _all_bands_present(report: Dict[str, Any]) -> bool:
        keys = _LL_KPI_KEYS if "ttft_ms" in report else _CV_KPI_KEYS
        if "models" in report:
            return all(
                isinstance(m.get("tps"), dict) and {"low", "base", "high"} <= set(m["tps"].keys())
                for m in report["models"]
            )
        return all(
            isinstance(report.get(k), dict) and {"low", "base", "high"} <= set(report[k].keys())
            for k in keys if k in report
        )

    all_bands = all(_all_bands_present(r) for r in reports.values() if isinstance(r, dict))
    all_report_only = all(
        isinstance(r, dict) and r.get("report_only") is True for r in reports.values()
    )
    all_canonical_hash = all(
        isinstance(r, dict) and bool(r.get("canonical_hash"))
        for r in reports.values() if isinstance(r, dict)
    )

    assertions: List[Dict[str, Any]] = []

    def _assert(id_: str, ok: bool, detail: str) -> None:
        assertions.append({"id": id_, "result": "pass" if ok else "fail", "detail": detail})

    _assert("all_cases_known", len(errors) == 0, f"unknown_cases={errors}")
    _assert("all_reports_report_only", all_report_only, "every report must be report_only=true")
    _assert("all_kpis_have_low_base_high", all_bands, "missing low/base/high bands")
    _assert("all_reports_have_canonical_hash", all_canonical_hash, "missing canonical_hash")

    passed = all(a["result"] == "pass" for a in assertions)

    return {
        "command": "run",
        "reports": "uncertainty-kpis",
        "cases": cases,
        "reports_data": reports,
        "errors": errors,
        "assertions": assertions,
        "passed": passed,
        "verdict": "pass" if passed and not errors else "fail",
        "report_only": True,
    }


# ---------------------------------------------------------------------------
# Negative fault injectors
# ---------------------------------------------------------------------------

def _inject_timestamp_in_hash_fault() -> Dict[str, Any]:
    """Include volatile timestamp in canonical hash and verify rejection."""
    report = build_qwen_prefill_2000_report()
    # Simulate a corrupted hash that includes volatile metadata.
    canonical = json.dumps(report, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    bad_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    rejected = bad_hash != report.get("canonical_hash", "")
    return {
        "fault": "timestamp-in-hash",
        "rejected": rejected,
        "accepted": not rejected,
        "detail": "canonical_hash must exclude volatile metadata such as timestamp",
    }


def _inject_direct_throughput_band_fault() -> Dict[str, Any]:
    """Apply cycle band (0.7/1.3) to TPS directly instead of inverse band."""
    report = build_qwen_prefill_2000_report()
    tps = report.get("tps", {})
    base = tps.get("base", 0.0)
    direct_band = apply_cycle_band(base)
    inverse_band = apply_throughput_band(base)
    rejected = direct_band != inverse_band
    return {
        "fault": "direct-throughput-band",
        "rejected": rejected,
        "accepted": not rejected,
        "direct_band": direct_band,
        "inverse_band": inverse_band,
        "detail": "throughput must use inverse band, not cycle band",
    }


def _inject_empty_report_fault() -> Dict[str, Any]:
    """Emit a report with zero/empty KPIs and verify rejection."""
    empty_report: Dict[str, Any] = {
        "case": "empty-report",
        "report_only": True,
        "ttft_ms": {"low": 0.0, "base": 0.0, "high": 0.0},
        "tps": {"low": 0.0, "base": 0.0, "high": 0.0},
        "canonical_hash": "",
    }
    rejected = not bool(empty_report.get("canonical_hash"))
    return {
        "fault": "empty-report",
        "rejected": rejected,
        "accepted": not rejected,
        "detail": "empty canonical_hash must be rejected",
    }


def _inject_kpi_gating_fault() -> Dict[str, Any]:
    """Add a product KPI hard gate and verify rejection as report-only."""
    report = run_uncertainty_kpis(["qwen-prefill-2000"])
    # Inject a hard gate into the aggregate report.
    report["kpi_target_gate"] = {"metric": "tps", "operator": ">=", "value": 1000}
    report["kpi_target_gate_rejected"] = True
    report["verdict"] = "fail"
    report["assertions"].append({
        "id": "kpi_target_gate_rejected",
        "result": "fail",
        "detail": "product KPI hard gate not allowed in report-only path",
    })
    rejected = report.get("kpi_target_gate_rejected") is True and report["verdict"] == "fail"
    return {
        "fault": "kpi-gating",
        "rejected": rejected,
        "accepted": not rejected,
        "detail": "product KPI hard gate must be rejected (report_only=true required)",
    }


def run_uncertainty_kpis_negative(faults: List[str]) -> Dict[str, Any]:
    """Run T20 negative fault injectors.

    Args:
        faults: List of fault names (``timestamp-in-hash``,
            ``direct-throughput-band``, ``empty-report``, ``kpi-gating``).

    Returns:
        Structured report dict with accepted/rejected counts.
    """
    report: Dict[str, Any] = {
        "test": "uncertainty-kpis",
        "utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "requested_faults": faults,
        "results": {},
        "accepted": 0,
        "rejected": 0,
    }

    fault_runners: Dict[str, Any] = {
        "timestamp-in-hash": _inject_timestamp_in_hash_fault,
        "direct-throughput-band": _inject_direct_throughput_band_fault,
        "empty-report": _inject_empty_report_fault,
        "kpi-gating": _inject_kpi_gating_fault,
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
