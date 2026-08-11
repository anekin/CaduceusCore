"""Versioned performance-spec regression baseline for T22.

Creates, validates, and mutates a canonical baseline keyed by spec/workload/
provider hashes.  Structural/formula/workload/invariant gates are absolute;
KPI drift only produces a report diff.

No RTL paths are ever opened or hashed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from timing.cv_spec_gates import evaluate_cv_workload
from timing.model_scaling import build_scaling_report
from timing.qwen_spec_gates import evaluate_qwen_workload
from timing.sweeps import run_sweeps
from timing.uncertainty_kpis import run_uncertainty_kpis


REPO_ROOT = Path(__file__).resolve().parents[2]

CANONICAL_PATHS = {
    "spec": REPO_ROOT / "config" / "func_model_perf_spec_v1.json",
    "matrix": REPO_ROOT / "config" / "func_model_perf_matrix_v1.json",
    "oracle": REPO_ROOT / "config" / "func_model_perf_oracle_v1.json",
    "workload_oracle": REPO_ROOT / "config" / "func_model_workload_oracle_v1.json",
    "provider_config": REPO_ROOT / "config" / "perf_providers" / "spec-block64-v1.json",
}

WORKLOAD_MANIFEST_PATHS = {
    "qwen25_3b": REPO_ROOT / "config" / "workloads" / "qwen25_3b_perf_spec_v1.json",
    "mobilenetv3": REPO_ROOT / "config" / "workloads" / "mobilenetv3_perf_spec_v1.json",
    "resnet50": REPO_ROOT / "config" / "workloads" / "resnet50_perf_spec_v1.json",
    "yolov8n": REPO_ROOT / "config" / "workloads" / "yolov8n_perf_spec_v1.json",
}

QWEN_WORKLOAD_IDS = [
    "qwen25-3b-blk0-decode",
    "qwen25-3b-decode-c128-g1",
    "qwen25-3b-prefill-16",
    "qwen25-3b-prefill-128",
]

CV_WORKLOAD_IDS = ["mobilenetv3", "resnet50", "yolov8n"]

UNCERTAINTY_CASES = [
    "qwen-prefill-2000",
    "qwen-model-family",
    "mobilenetv3",
    "resnet50",
    "yolov8n",
]

SWEEP_IDS = ["bandwidth", "array", "dma-channels", "prompt", "context", "noc-hop"]

_KPI_KEYS = {
    "ttft_ms",
    "tps",
    "tpot_us",
    "prefill_ms",
    "decode_per_token_us",
    "fps",
    "inference_latency_us",
}

DEFAULT_BASELINE_ID = "func_model_perf_spec_v1"

CHANGE_POLICY = {
    "summary": "Baseline updates require a changed spec version/rationale; never 'accept current output'.",
    "rules": [
        "Validate mode is read-only and never mutates the baseline file.",
        "Structural/formula/workload/invariant gate regressions are hard failures.",
        "KPI drift produces a report diff but does not fail validation.",
        "Baseline must be recreated only when spec/matrix/oracle/manifest/provider config changes.",
    ],
}


def _hash_file(path: Path) -> str:
    """SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_spec_hash(spec_path: Path) -> str:
    """Content hash of the spec excluding volatile metadata."""
    data = json.loads(spec_path.read_text(encoding="utf-8"))

    def _strip(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: _strip(v)
                for k, v in obj.items()
                if k not in ("created", "updated", "timestamp", "content_hash")
            }
        if isinstance(obj, list):
            return [_strip(v) for v in obj]
        return obj

    canonical = json.dumps(_strip(data), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_matrix_hash(matrix_path: Path) -> str:
    """Content hash of the matrix excluding volatile metadata."""
    data = json.loads(matrix_path.read_text(encoding="utf-8"))

    def _strip(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {
                k: _strip(v)
                for k, v in obj.items()
                if k not in ("created", "updated", "timestamp", "content_hash")
            }
        if isinstance(obj, list):
            return [_strip(v) for v in obj]
        return obj

    canonical = json.dumps(_strip(data), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_input_hashes(
    spec_path: Optional[Path] = None,
    matrix_path: Optional[Path] = None,
    oracle_path: Optional[Path] = None,
    workload_oracle_path: Optional[Path] = None,
    provider_config_path: Optional[Path] = None,
    workload_manifest_paths: Optional[Dict[str, Path]] = None,
) -> Dict[str, Any]:
    """Compute content hashes for all canonical inputs."""
    spec_path = spec_path or CANONICAL_PATHS["spec"]
    matrix_path = matrix_path or CANONICAL_PATHS["matrix"]
    oracle_path = oracle_path or CANONICAL_PATHS["oracle"]
    workload_oracle_path = workload_oracle_path or CANONICAL_PATHS["workload_oracle"]
    provider_config_path = provider_config_path or CANONICAL_PATHS["provider_config"]
    workload_manifest_paths = workload_manifest_paths or WORKLOAD_MANIFEST_PATHS

    return {
        "spec_hash": _canonical_spec_hash(spec_path),
        "matrix_hash": _canonical_matrix_hash(matrix_path),
        "oracle_hash": _hash_file(oracle_path),
        "workload_oracle_hash": _hash_file(workload_oracle_path),
        "provider_config_hash": _hash_file(provider_config_path),
        "workload_manifest_hashes": {
            wid: _hash_file(p) for wid, p in workload_manifest_paths.items()
        },
    }


def run_provider_gates(oracle_path: Optional[Path] = None) -> Dict[str, Any]:
    """Run the provider-vs-oracle verifier for all domains via subprocess."""
    oracle_path = oracle_path or CANONICAL_PATHS["oracle"]
    cmd = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "verify_func_model_perf_spec.py"),
        "--oracle", str(oracle_path),
        "--domain", "mxu,sfu,vector,dma,dram,noc,kv,sw_overhead",
        "--self-check",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=120,
    )
    output: Dict[str, Any] = {}
    if result.stdout.strip():
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError:
            output = {"parse_error": result.stdout[:500]}
    output["_exit_code"] = result.returncode

    dv = output.get("domain_validation", {})
    normalized = {
        "verdict": output.get("verdict", "fail"),
        "rows": dv.get("rows", 0),
        "failed": dv.get("failed", 0),
        "domain_validation": dv,
        "import_policy": output.get("import_policy", {}),
        "_exit_code": result.returncode,
    }
    return normalized


def run_qwen_paths() -> Dict[str, Any]:
    """Run all four canonical Qwen workloads through Path A/B comparison."""
    results: Dict[str, Any] = {}
    passed = 0
    failed = 0
    for workload_id in QWEN_WORKLOAD_IDS:
        comparison = evaluate_qwen_workload(workload_id)
        results[workload_id] = {
            "passed": comparison.get("passed", False),
            "path_a_total": comparison.get("path_a_total"),
            "path_b_total": comparison.get("path_b_total"),
            "total_error_pct": comparison.get("total_error_pct"),
            "workload_hash": comparison.get("path_a", {}).get("workload_hash"),
        }
        if comparison.get("passed"):
            passed += 1
        else:
            failed += 1
    return {
        "workloads": results,
        "passed": passed,
        "failed": failed,
        "verdict": "pass" if failed == 0 else "fail",
    }


def run_cv_paths() -> Dict[str, Any]:
    """Run all three canonical CV workloads through Path A/B comparison."""
    results: Dict[str, Any] = {}
    passed = 0
    failed = 0
    for workload_id in CV_WORKLOAD_IDS:
        comparison = evaluate_cv_workload(workload_id)
        results[workload_id] = {
            "passed": comparison.get("passed", False),
            "path_a_total": comparison.get("path_a_total"),
            "path_b_total": comparison.get("path_b_total"),
            "total_error_pct": comparison.get("total_error_pct"),
            "workload_hash": comparison.get("path_a", {}).get("workload_hash"),
        }
        if comparison.get("passed"):
            passed += 1
        else:
            failed += 1
    return {
        "workloads": results,
        "passed": passed,
        "failed": failed,
        "verdict": "pass" if failed == 0 else "fail",
    }


def run_sweep_checks() -> Dict[str, Any]:
    """Run the six frozen sweep dimensions with memory+compute endpoint checks."""
    report = run_sweeps(
        sweep_ids=SWEEP_IDS,
        require_endpoints=["memory", "compute"],
    )
    return {
        "sweeps": report.get("sweeps", {}),
        "endpoints": report.get("endpoints", []),
        "monotonicity_failures": report.get("monotonicity_failures", []),
        "endpoint_failures": report.get("endpoint_failures", []),
        "verdict": report.get("verdict", "fail"),
    }


def run_scaling_report() -> Dict[str, Any]:
    """Run the cross-model scaling report (report-only)."""
    report = build_scaling_report()
    return {
        "models": [
            {
                "model": m["model"],
                "hidden": m["hidden"],
                "layers": m["layers"],
                "weight_bytes": m["weight_bytes"],
                "total_decode_cycles": m["total_decode_cycles"],
                "memory_bound_per_weight_byte": m["memory_bound_per_weight_byte"],
                "report_only": m["report_only"],
            }
            for m in report.get("models", [])
        ],
        "assertions": report.get("assertions", []),
        "passed": report.get("passed", False),
        "verdict": report.get("verdict", "fail"),
        "report_only": report.get("report_only", True),
    }


def _run_uncertainty_kpis() -> Dict[str, Any]:
    """Run uncertainty-aware KPI reports for all five frozen cases."""
    report = run_uncertainty_kpis(UNCERTAINTY_CASES)
    reports_data = report.get("reports_data", {})
    slim_data: Dict[str, Any] = {}
    for case, data in reports_data.items():
        if not isinstance(data, dict):
            continue
        if "models" in data:
            slim_data[case] = {
                "case": data.get("case", case),
                "report_only": data.get("report_only"),
                "canonical_hash": data.get("canonical_hash"),
                "passed": data.get("passed"),
                "verdict": data.get("verdict"),
                "models": [
                    {
                        "model": m.get("model"),
                        "total_decode_cycles": m.get("total_decode_cycles"),
                        "tps": m.get("tps"),
                        "weight_bytes": m.get("weight_bytes"),
                        "canonical_hash": m.get("canonical_hash"),
                    }
                    for m in data.get("models", [])
                ],
            }
        else:
            slim_data[case] = {
                "case": data.get("case", case),
                "report_only": data.get("report_only"),
                "canonical_hash": data.get("canonical_hash"),
                "total_cycles": data.get("total_cycles"),
                "path_a_total": data.get("path_a_total"),
                "path_b_total": data.get("path_b_total"),
                "total_error_pct": data.get("total_error_pct"),
            }
            for kpi_key in _KPI_KEYS:
                if kpi_key in data:
                    slim_data[case][kpi_key] = data[kpi_key]

    return {
        "reports_data": slim_data,
        "assertions": report.get("assertions", []),
        "passed": report.get("passed", False),
        "verdict": report.get("verdict", "fail"),
        "report_only": report.get("report_only", True),
    }


def compute_canonical_results(oracle_path: Optional[Path] = None) -> Dict[str, Any]:
    """Compute the full canonical result set used as baseline contents."""
    return {
        "provider_gates": run_provider_gates(oracle_path=oracle_path),
        "qwen_paths": run_qwen_paths(),
        "cv_paths": run_cv_paths(),
        "sweep_checks": run_sweep_checks(),
        "scaling_report": run_scaling_report(),
        "uncertainty_kpis": _run_uncertainty_kpis(),
    }


def compute_baseline_content_hash(baseline: Dict[str, Any]) -> str:
    """Deterministic hash of the baseline excluding volatile metadata."""
    excluded = {"created", "canonical_content_hash", "utc_start", "utc_end"}
    filtered = {k: v for k, v in baseline.items() if k not in excluded}
    canonical = json.dumps(filtered, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_baseline(
    output_path: Path,
    baseline_id: Optional[str] = None,
    spec_path: Optional[Path] = None,
    matrix_path: Optional[Path] = None,
    oracle_path: Optional[Path] = None,
    workload_oracle_path: Optional[Path] = None,
    provider_config_path: Optional[Path] = None,
    workload_manifest_paths: Optional[Dict[str, Path]] = None,
) -> Dict[str, Any]:
    """Create a fresh performance-spec regression baseline.

    Always recomputes canonical results from source files (the --from-latest-fresh
    path in the runner simply invokes this function).
    """
    hashes = compute_input_hashes(
        spec_path=spec_path,
        matrix_path=matrix_path,
        oracle_path=oracle_path,
        workload_oracle_path=workload_oracle_path,
        provider_config_path=provider_config_path,
        workload_manifest_paths=workload_manifest_paths,
    )
    results = compute_canonical_results()

    baseline: Dict[str, Any] = {
        "baseline_id": baseline_id or DEFAULT_BASELINE_ID,
        "created": datetime.now(timezone.utc).isoformat(),
        "spec_hash": hashes["spec_hash"],
        "matrix_hash": hashes["matrix_hash"],
        "oracle_hash": hashes["oracle_hash"],
        "workload_oracle_hash": hashes["workload_oracle_hash"],
        "provider_config_hash": hashes["provider_config_hash"],
        "workload_manifest_hashes": hashes["workload_manifest_hashes"],
        "canonical_results": results,
        "policy": CHANGE_POLICY,
    }
    baseline["canonical_content_hash"] = compute_baseline_content_hash(baseline)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return baseline


def _check_freshness(
    baseline_path: Path,
    spec_path: Path,
    matrix_path: Path,
    oracle_path: Path,
    workload_oracle_path: Path,
    provider_config_path: Path,
    workload_manifest_paths: Dict[str, Path],
) -> Tuple[bool, Dict[str, Any]]:
    """Return (ok, details) using the T4 freshness predicate."""
    if not baseline_path.is_file():
        return False, {"error": "baseline_file_missing", "path": str(baseline_path)}

    baseline_mtime = baseline_path.stat().st_mtime
    data_mtimes: Dict[str, float] = {
        "spec": spec_path.stat().st_mtime,
        "matrix": matrix_path.stat().st_mtime,
        "oracle": oracle_path.stat().st_mtime,
        "workload_oracle": workload_oracle_path.stat().st_mtime,
        "provider_config": provider_config_path.stat().st_mtime,
    }
    for wid, p in workload_manifest_paths.items():
        data_mtimes[f"workload_manifest:{wid}"] = p.stat().st_mtime

    max_data_mtime = max(data_mtimes.values())
    details: Dict[str, Any] = {
        "baseline_mtime": baseline_mtime,
        "data_mtimes": data_mtimes,
        "max_data_mtime": max_data_mtime,
    }
    if baseline_mtime < max_data_mtime:
        details["failures"] = ["stale_evidence: baseline older than data dependencies"]
        return False, details
    return True, details


def _extract_base_kpis(report: Dict[str, Any]) -> Dict[str, Any]:
    """Extract scalar base KPI values from a report for diffing."""
    kpis: Dict[str, Any] = {}
    if "models" in report:
        for m in report["models"]:
            model = m.get("model", "unknown")
            for key in _KPI_KEYS:
                val = m.get(key)
                if isinstance(val, dict) and "base" in val:
                    kpis[f"{model}.{key}"] = val["base"]
    else:
        for key in _KPI_KEYS:
            val = report.get(key)
            if isinstance(val, dict) and "base" in val:
                kpis[key] = val["base"]
    return kpis


def _compare_canonical_results(
    stored: Dict[str, Any],
    current: Dict[str, Any],
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Compare stored vs current canonical results.

    Returns (hard_failures, report_diffs).
    """
    hard_failures: List[str] = []
    report_diffs: List[Dict[str, Any]] = []

    # Provider formula gates: absolute.
    sp = stored.get("provider_gates", {})
    cp = current.get("provider_gates", {})
    if cp.get("verdict") != "pass":
        hard_failures.append("provider_gates_verdict_fail")
    if cp.get("failed", 0) != 0:
        hard_failures.append(f"provider_gates_failed_nonzero: {cp.get('failed')}")
    if cp.get("rows") != sp.get("rows"):
        hard_failures.append(
            f"provider_gates_rows_changed: stored={sp.get('rows')} current={cp.get('rows')}"
        )

    # Qwen/CV dual-path gates: absolute.
    for section in ("qwen_paths", "cv_paths"):
        stored_section = stored.get(section, {})
        current_section = current.get(section, {})
        for workload_id, s_result in stored_section.get("workloads", {}).items():
            c_result = current_section.get("workloads", {}).get(workload_id)
            if not c_result:
                hard_failures.append(f"{section}.{workload_id}_missing")
                continue
            if not c_result.get("passed"):
                hard_failures.append(f"{section}.{workload_id}_not_passed")
                continue
            if c_result.get("total_error_pct") != s_result.get("total_error_pct"):
                hard_failures.append(
                    f"{section}.{workload_id}_total_error_changed: "
                    f"stored={s_result.get('total_error_pct')} current={c_result.get('total_error_pct')}"
                )
            if c_result.get("path_a_total") != s_result.get("path_a_total"):
                hard_failures.append(
                    f"{section}.{workload_id}_path_a_total_changed: "
                    f"stored={s_result.get('path_a_total')} current={c_result.get('path_a_total')}"
                )
            if c_result.get("path_b_total") != s_result.get("path_b_total"):
                hard_failures.append(
                    f"{section}.{workload_id}_path_b_total_changed: "
                    f"stored={s_result.get('path_b_total')} current={c_result.get('path_b_total')}"
                )
            if c_result.get("workload_hash") != s_result.get("workload_hash"):
                hard_failures.append(f"{section}.{workload_id}_workload_hash_changed")

    # Sweep monotonicity/endpoint checks: absolute.
    ss = stored.get("sweep_checks", {})
    cs = current.get("sweep_checks", {})
    if cs.get("verdict") != "pass":
        hard_failures.append("sweep_checks_verdict_fail")
    if len(cs.get("monotonicity_failures", [])) != len(ss.get("monotonicity_failures", [])):
        hard_failures.append(
            f"sweep_monotonicity_failures_changed: stored={len(ss.get('monotonicity_failures', []))} "
            f"current={len(cs.get('monotonicity_failures', []))}"
        )
    if len(cs.get("endpoint_failures", [])) != len(ss.get("endpoint_failures", [])):
        hard_failures.append(
            f"sweep_endpoint_failures_changed: stored={len(ss.get('endpoint_failures', []))} "
            f"current={len(cs.get('endpoint_failures', []))}"
        )

    # Scaling report monotonicity: absolute.
    ss = stored.get("scaling_report", {})
    cs = current.get("scaling_report", {})
    if cs.get("verdict") != "pass":
        hard_failures.append("scaling_report_verdict_fail")
    if cs.get("passed") != ss.get("passed"):
        hard_failures.append(
            f"scaling_report_passed_changed: stored={ss.get('passed')} current={cs.get('passed')}"
        )

    # Uncertainty KPIs: structural/invariant absolute; numeric base values diff-only.
    su = stored.get("uncertainty_kpis", {})
    cu = current.get("uncertainty_kpis", {})
    if cu.get("verdict") != "pass":
        hard_failures.append("uncertainty_kpis_verdict_fail")
    if cu.get("passed") != su.get("passed"):
        hard_failures.append(
            f"uncertainty_kpis_passed_changed: stored={su.get('passed')} current={cu.get('passed')}"
        )

    stored_reports = su.get("reports_data", {})
    current_reports = cu.get("reports_data", {})
    for case, s_report in stored_reports.items():
        c_report = current_reports.get(case)
        if not isinstance(c_report, dict):
            hard_failures.append(f"uncertainty_kpis.{case}_missing")
            continue
        if c_report.get("report_only") != s_report.get("report_only"):
            hard_failures.append(f"uncertainty_kpis.{case}_report_only_changed")
        if not c_report.get("canonical_hash"):
            hard_failures.append(f"uncertainty_kpis.{case}_canonical_hash_missing")

        s_kpis = _extract_base_kpis(s_report)
        c_kpis = _extract_base_kpis(c_report)
        for kpi_name, s_val in s_kpis.items():
            c_val = c_kpis.get(kpi_name)
            if c_val != s_val:
                report_diffs.append({
                    "section": "uncertainty_kpis",
                    "case": case,
                    "kpi": kpi_name,
                    "stored": s_val,
                    "current": c_val,
                })

    return hard_failures, report_diffs


def validate_baseline(
    baseline_path: Path,
    require_fresh: bool = False,
    spec_path: Optional[Path] = None,
    matrix_path: Optional[Path] = None,
    oracle_path: Optional[Path] = None,
    workload_oracle_path: Optional[Path] = None,
    provider_config_path: Optional[Path] = None,
    workload_manifest_paths: Optional[Dict[str, Path]] = None,
) -> Dict[str, Any]:
    """Validate a stored baseline against current canonical results.

    Never modifies the baseline file.
    """
    spec_path = spec_path or CANONICAL_PATHS["spec"]
    matrix_path = matrix_path or CANONICAL_PATHS["matrix"]
    oracle_path = oracle_path or CANONICAL_PATHS["oracle"]
    workload_oracle_path = workload_oracle_path or CANONICAL_PATHS["workload_oracle"]
    provider_config_path = provider_config_path or CANONICAL_PATHS["provider_config"]
    workload_manifest_paths = workload_manifest_paths or WORKLOAD_MANIFEST_PATHS

    if not baseline_path.is_file():
        return {
            "verdict": "fail",
            "baseline_path": str(baseline_path),
            "error": "baseline_file_missing",
        }

    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    current_hashes = compute_input_hashes(
        spec_path=spec_path,
        matrix_path=matrix_path,
        oracle_path=oracle_path,
        workload_oracle_path=workload_oracle_path,
        provider_config_path=provider_config_path,
        workload_manifest_paths=workload_manifest_paths,
    )

    hash_match = True
    hash_failures: List[str] = []
    for key in ("spec_hash", "matrix_hash", "oracle_hash", "workload_oracle_hash", "provider_config_hash"):
        if baseline.get(key) != current_hashes.get(key):
            hash_match = False
            hash_failures.append(f"{key}_mismatch")
    stored_manifest_hashes = baseline.get("workload_manifest_hashes", {})
    for wid, h in current_hashes["workload_manifest_hashes"].items():
        if stored_manifest_hashes.get(wid) != h:
            hash_match = False
            hash_failures.append(f"workload_manifest_hash_mismatch:{wid}")

    freshness: Dict[str, Any] = {"checked": require_fresh}
    if require_fresh:
        fresh_ok, fresh_details = _check_freshness(
            baseline_path,
            spec_path,
            matrix_path,
            oracle_path,
            workload_oracle_path,
            provider_config_path,
            workload_manifest_paths,
        )
        freshness.update({"ok": fresh_ok, "details": fresh_details})
        if not fresh_ok:
            hash_failures.append("freshness_check_failed")

    current_results = compute_canonical_results(oracle_path=oracle_path)
    stored_results = baseline.get("canonical_results", {})
    hard_failures, report_diffs = _compare_canonical_results(stored_results, current_results)

    all_failures = hash_failures + hard_failures
    verdict = "pass" if not all_failures else "fail"

    return {
        "verdict": verdict,
        "baseline_path": str(baseline_path),
        "baseline_id": baseline.get("baseline_id"),
        "canonical_content_hash_match": baseline.get("canonical_content_hash")
        == compute_baseline_content_hash(baseline),
        "input_hash_match": hash_match,
        "hash_failures": hash_failures,
        "hard_failures": hard_failures,
        "report_diffs": report_diffs,
        "freshness": freshness,
        "read_only": True,
    }


def _mutate_spec_for_stale_spec(path: Path, tmpdir: Path) -> Path:
    """Create a temp spec copy with one numeric parameter changed."""
    data = json.loads(path.read_text(encoding="utf-8"))
    domains = data.get("domains", {})
    for domain, params in domains.items():
        for p in params:
            if isinstance(p.get("estimated_cycles"), (int, float)):
                p["estimated_cycles"] = int(p["estimated_cycles"]) + 1
                p.setdefault("_mutation_note", "T22 stale-spec fault injected")
                break
        else:
            continue
        break
    mutated_path = tmpdir / "mutated_spec.json"
    mutated_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return mutated_path


def _mutate_oracle_for_hidden_hard_gate(path: Path, tmpdir: Path) -> Path:
    """Create a temp oracle copy with one provider entry scaled out of tolerance."""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", {})
    for domain, domain_entries in entries.items():
        for entry in domain_entries:
            if isinstance(entry.get("expected_cycles"), (int, float)):
                entry["expected_cycles"] = int(entry["expected_cycles"]) * 100
                entry.setdefault("_mutation_note", "T22 hidden-hard-gate fault injected")
                break
        break
    mutated_path = tmpdir / "mutated_oracle.json"
    mutated_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return mutated_path


def run_baseline_negative(
    faults: List[str],
    baseline_path: Path,
    spec_path: Optional[Path] = None,
    oracle_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run T22 baseline negative fault injectors.

    Args:
        faults: List of fault names (``accept-current``, ``stale-spec``,
            ``hidden-hard-gate``).
        baseline_path: Path to the canonical baseline JSON.
        spec_path: Optional override for the spec path.
        oracle_path: Optional override for the provider oracle path.

    Returns:
        Structured report dict with accepted/rejected counts.
    """
    spec_path = spec_path or CANONICAL_PATHS["spec"]
    oracle_path = oracle_path or CANONICAL_PATHS["oracle"]

    report: Dict[str, Any] = {
        "test": "negative-baseline",
        "utc": datetime.now(timezone.utc).isoformat(),
        "requested_faults": faults,
        "results": {},
        "accepted": 0,
        "rejected": 0,
    }

    tmpdir = Path(tempfile.mkdtemp(prefix="perf_baseline_negative_"))
    try:
        for fault_name in faults:
            result: Dict[str, Any] = {"fault": fault_name, "rejected": False}

            if fault_name == "accept-current":
                # Validate must be read-only: baseline file must not change.
                before_stat = baseline_path.stat()
                before_hash = _hash_file(baseline_path)
                validate_report = validate_baseline(baseline_path)
                after_stat = baseline_path.stat()
                after_hash = _hash_file(baseline_path)
                unchanged = (
                    before_stat.st_mtime == after_stat.st_mtime
                    and before_stat.st_size == after_stat.st_size
                    and before_hash == after_hash
                    and validate_report.get("read_only") is True
                )
                result["rejected"] = unchanged and validate_report.get("verdict") == "pass"
                result["detail"] = (
                    "validate mode left baseline unchanged"
                    if result["rejected"]
                    else "validate mode modified baseline or failed unexpectedly"
                )
                result["validate_verdict"] = validate_report.get("verdict")

            elif fault_name == "stale-spec":
                mutated_spec = _mutate_spec_for_stale_spec(spec_path, tmpdir)
                validate_report = validate_baseline(
                    baseline_path,
                    spec_path=mutated_spec,
                )
                result["rejected"] = validate_report.get("verdict") == "fail" and any(
                    "spec_hash" in f for f in validate_report.get("hash_failures", [])
                )
                result["detail"] = (
                    "mutated spec hash mismatch detected"
                    if result["rejected"]
                    else "mutated spec not rejected"
                )
                result["hash_failures"] = validate_report.get("hash_failures", [])

            elif fault_name == "hidden-hard-gate":
                mutated_oracle = _mutate_oracle_for_hidden_hard_gate(oracle_path, tmpdir)
                validate_report = validate_baseline(
                    baseline_path,
                    oracle_path=mutated_oracle,
                )
                # Recompute uncertainty KPIs independently to show they are stable.
                kpi_report = run_uncertainty_kpis(UNCERTAINTY_CASES)
                kpi_still_pass = kpi_report.get("verdict") == "pass"
                provider_failed = any(
                    "provider_gates" in f for f in validate_report.get("hard_failures", [])
                ) or validate_report.get("verdict") == "fail" and any(
                    "provider" in f for f in validate_report.get("hash_failures", [])
                )
                result["rejected"] = validate_report.get("verdict") == "fail" and kpi_still_pass
                result["detail"] = (
                    "provider formula gate regression detected while KPIs remain stable"
                    if result["rejected"]
                    else "hidden hard gate not rejected"
                )
                result["kpi_verdict"] = kpi_report.get("verdict")
                result["validate_failures"] = (
                    validate_report.get("hard_failures", [])
                    + validate_report.get("hash_failures", [])
                )

            else:
                result["error"] = f"Unknown fault: {fault_name}"

            report["results"][fault_name] = result
            if result.get("rejected"):
                report["rejected"] += 1
            else:
                report["accepted"] += 1

    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    report["all_passed"] = report["accepted"] == 0 and report["rejected"] == len(faults)
    report["verdict"] = "pass" if report["all_passed"] else "fail"
    return report
