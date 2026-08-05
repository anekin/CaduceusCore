#!/usr/bin/env python3
"""
E2E evidence aggregator for the fm-e2e-qwen-cv-software-stack plan (S2).

Collects the latest evidence for Track A (Qwen) and Track B (CV),
verifies presence + SHA-256 hashes + pass flags, and writes a unified
JSON signoff report.

Usage:
    PYTHONPATH=sim:gen:software python3 scripts/aggregate_e2e_signoff.py \
        --evidence-dir .omo/evidence \
        --output .omo/evidence/e2e-aggregated-signoff.json \
        --strict
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_DIR = REPO_ROOT / ".omo" / "evidence"
DEFAULT_OUTPUT = DEFAULT_EVIDENCE_DIR / "e2e-aggregated-signoff.json"


# ---------------------------------------------------------------------------
# File discovery — most recent by mtime
# ---------------------------------------------------------------------------
def _most_recent(pattern: str, evidence_dir: Path) -> Optional[Path]:
    """Return the most recent file matching *pattern* by st_mtime, or None."""
    candidates = sorted(
        evidence_dir.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# SHA-256 fingerprint
# ---------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    """Return the hex SHA-256 digest of a file's contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Track A — Qwen
# ---------------------------------------------------------------------------
def _check_qwen_full_forward(
    path: Path,
) -> Tuple[bool, List[str]]:
    """Validate a qwen-full-forward-*.json evidence file.

    Returns (passed, list_of_failure_reasons).
    """
    failures: List[str] = []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return False, [f"cannot parse {path.name}: {exc}"]

    # passed == true
    if data.get("passed") is not True:
        failures.append("passed != true")

    # non-empty generated_token_text
    token_text = data.get("generated_token_text")
    if not isinstance(token_text, str) or not token_text.strip():
        failures.append("generated_token_text is empty or missing")

    # npu_ops_executed > 0
    npu_ops = data.get("npu_ops_executed")
    if not isinstance(npu_ops, (int, float)) or npu_ops <= 0:
        failures.append(f"npu_ops_executed <= 0 (got {npu_ops!r})")

    return len(failures) == 0, failures


def _check_qwen_per_layer_compare(
    path: Path,
) -> Tuple[bool, List[str]]:
    """Validate a qwen-per-layer-compare-*.json evidence file."""
    failures: List[str] = []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return False, [f"cannot parse {path.name}: {exc}"]

    # passed == true
    if data.get("passed") is not True:
        failures.append("passed != true")

    # summary.first_layer.passed == true
    summary = data.get("summary", {})
    first_layer = summary.get("first_layer", {})
    if first_layer.get("passed") is not True:
        failures.append("summary.first_layer.passed != true")

    # summary.last_layer.passed == true
    last_layer = summary.get("last_layer", {})
    if last_layer.get("passed") is not True:
        failures.append("summary.last_layer.passed != true")

    # n_layers == 36
    n_layers = data.get("n_layers")
    if n_layers != 36:
        failures.append(f"n_layers != 36 (got {n_layers!r})")

    return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# Track B — CV
# ---------------------------------------------------------------------------
def _check_cv_golden(
    path: Path,
) -> Tuple[bool, List[str]]:
    """Validate a cv-golden.json evidence file."""
    failures: List[str] = []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return False, [f"cannot parse {path.name}: {exc}"]

    # top5_indices length 5
    top5_indices = data.get("top5_indices")
    if not isinstance(top5_indices, list) or len(top5_indices) != 5:
        failures.append(f"top5_indices missing or length != 5 (got {top5_indices!r})")

    # top5_logits length 5
    top5_logits = data.get("top5_logits")
    if not isinstance(top5_logits, list) or len(top5_logits) != 5:
        failures.append(f"top5_logits missing or length != 5 (got {top5_logits!r})")

    # seed == 42
    if data.get("seed") != 42:
        failures.append(f"seed != 42 (got {data.get('seed')!r})")

    return len(failures) == 0, failures


def _check_cv_host_runner(
    path: Path,
) -> Tuple[bool, List[str]]:
    """Validate a cv-host-runner-*.json evidence file.

    Only considers files where full_graph_passed == true.
    """
    failures: List[str] = []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return False, [f"cannot parse {path.name}: {exc}"]

    # full_graph_passed == true
    if data.get("full_graph_passed") is not True:
        failures.append("full_graph_passed != true")

    # error is None
    if data.get("error") is not None:
        failures.append(f"error is not None (got {data.get('error')!r})")

    return len(failures) == 0, failures


def _latest_cv_host_runner_with_full_graph(
    evidence_dir: Path,
) -> Optional[Path]:
    """Return the most recent cv-host-runner-*.json where full_graph_passed == true."""
    candidates = sorted(
        evidence_dir.glob("cv-host-runner-*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in candidates:
        try:
            data = json.loads(path.read_text())
            if data.get("full_graph_passed") is True:
                return path
        except (json.JSONDecodeError, OSError):
            continue
    return None


# ---------------------------------------------------------------------------
# S1 — E2E signoff summary
# ---------------------------------------------------------------------------
def _check_e2e_summary(
    path: Path,
) -> Tuple[bool, List[str]]:
    """Validate e2e-signoff-summary.json."""
    failures: List[str] = []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return False, [f"cannot parse {path.name}: {exc}"]

    # overall_passed == true
    if data.get("overall_passed") is not True:
        failures.append("overall_passed != true")

    # fail_count == 0
    if data.get("fail_count") != 0:
        failures.append(f"fail_count != 0 (got {data.get('fail_count')!r})")

    return len(failures) == 0, failures


# ---------------------------------------------------------------------------
# Cross-check
# ---------------------------------------------------------------------------
def _cross_check_cv_mtime(
    host_runner: Optional[Path],
    golden: Optional[Path],
) -> Optional[str]:
    """Return a warning if the CV host-runner is not newer than CV golden."""
    if host_runner is None or golden is None:
        return None
    hr_mtime = host_runner.stat().st_mtime
    gd_mtime = golden.stat().st_mtime
    if hr_mtime <= gd_mtime:
        return (
            f"cv-host-runner mtime ({datetime.fromtimestamp(hr_mtime, tz=timezone.utc).isoformat()}) "
            f"is not newer than cv-golden mtime ({datetime.fromtimestamp(gd_mtime, tz=timezone.utc).isoformat()})"
        )
    return None


# ---------------------------------------------------------------------------
# File info helper
# ---------------------------------------------------------------------------
def _file_info(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    try:
        rel = str(path.relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
    return {
        "path": rel,
        "name": path.name,
        "mtime_utc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "sha256": _sha256(path),
    }


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------
def aggregate(
    evidence_dir: Path,
) -> Dict[str, Any]:
    """Collect and validate all evidence, return a unified JSON report."""
    now_utc = datetime.now(timezone.utc)
    warnings: List[str] = []
    missing_evidence: List[str] = []

    # ---- Track A: Qwen ----
    qwen_full = _most_recent("qwen-full-forward-*.json", evidence_dir)
    qwen_layer = _most_recent("qwen-per-layer-compare-*.json", evidence_dir)

    qwen_full_passed, qwen_full_failures = False, ["missing"]
    qwen_layer_passed, qwen_layer_failures = False, ["missing"]
    qwen_files: List[Dict[str, Any]] = []
    qwen_hashes: Dict[str, str] = {}

    if qwen_full is None:
        missing_evidence.append("qwen-full-forward-*.json")
    else:
        qwen_full_passed, qwen_full_failures = _check_qwen_full_forward(qwen_full)
        info = _file_info(qwen_full)
        qwen_files.append(info)
        qwen_hashes[info["name"]] = info["sha256"]

    if qwen_layer is None:
        missing_evidence.append("qwen-per-layer-compare-*.json")
    else:
        qwen_layer_passed, qwen_layer_failures = _check_qwen_per_layer_compare(qwen_layer)
        info = _file_info(qwen_layer)
        qwen_files.append(info)
        qwen_hashes[info["name"]] = info["sha256"]

    track_a_passed = qwen_full_passed and qwen_layer_passed

    # ---- Track B: CV ----
    cv_golden = _most_recent("cv-golden.json", evidence_dir)
    cv_host_runner = _latest_cv_host_runner_with_full_graph(evidence_dir)

    cv_golden_passed, cv_golden_failures = False, ["missing"]
    cv_hr_passed, cv_hr_failures = False, ["missing"]
    cv_files: List[Dict[str, Any]] = []
    cv_hashes: Dict[str, str] = {}

    if cv_golden is None:
        missing_evidence.append("cv-golden.json")
    else:
        cv_golden_passed, cv_golden_failures = _check_cv_golden(cv_golden)
        info = _file_info(cv_golden)
        cv_files.append(info)
        cv_hashes[info["name"]] = info["sha256"]

    if cv_host_runner is None:
        missing_evidence.append("cv-host-runner-*.json (none with full_graph_passed=true)")
    else:
        cv_hr_passed, cv_hr_failures = _check_cv_host_runner(cv_host_runner)
        info = _file_info(cv_host_runner)
        cv_files.append(info)
        cv_hashes[info["name"]] = info["sha256"]

    # Cross-check: host-runner mtime vs golden mtime
    cv_mtime_warning = _cross_check_cv_mtime(cv_host_runner, cv_golden)
    if cv_mtime_warning:
        warnings.append(cv_mtime_warning)

    track_b_passed = cv_golden_passed and cv_hr_passed

    # ---- S1: E2E summary ----
    e2e_summary = _most_recent("e2e-signoff-summary.json", evidence_dir)

    e2e_passed: bool = False
    e2e_failures: List[str] = ["missing"]
    e2e_files: List[Dict[str, Any]] = []
    e2e_hashes: Dict[str, str] = {}

    if e2e_summary is None:
        missing_evidence.append("e2e-signoff-summary.json")
    else:
        e2e_passed, e2e_failures = _check_e2e_summary(e2e_summary)
        info = _file_info(e2e_summary)
        e2e_files.append(info)
        e2e_hashes[info["name"]] = info["sha256"]

    # ---- Overall ----
    overall_passed = track_a_passed and track_b_passed and e2e_passed and not missing_evidence

    report: Dict[str, Any] = {
        "report_type": "e2e_aggregated_signoff",
        "report_version": "1.0",
        "timestamp": now_utc.isoformat(),
        "overall_passed": overall_passed,
        "tracks": {
            "qwen": {
                "passed": track_a_passed,
                "checks": {
                    "full_forward": {
                        "passed": qwen_full_passed,
                        "failures": qwen_full_failures if not qwen_full_passed else [],
                    },
                    "per_layer_compare": {
                        "passed": qwen_layer_passed,
                        "failures": qwen_layer_failures if not qwen_layer_passed else [],
                    },
                },
                "evidence_files": qwen_files,
                "hashes": qwen_hashes,
            },
            "cv": {
                "passed": track_b_passed,
                "checks": {
                    "golden": {
                        "passed": cv_golden_passed,
                        "failures": cv_golden_failures if not cv_golden_passed else [],
                    },
                    "host_runner": {
                        "passed": cv_hr_passed,
                        "failures": cv_hr_failures if not cv_hr_passed else [],
                    },
                },
                "evidence_files": cv_files,
                "hashes": cv_hashes,
            },
        },
        "e2e_summary": {
            "passed": e2e_passed,
            "failures": e2e_failures if not e2e_passed else [],
            "evidence_files": e2e_files,
            "hashes": e2e_hashes,
        },
        "missing_evidence": missing_evidence,
        "warnings": warnings,
    }

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate E2E evidence for Qwen + CV software stack signoff (S2)",
    )
    parser.add_argument(
        "--evidence-dir",
        default=str(DEFAULT_EVIDENCE_DIR),
        help=f"Evidence directory (default: {DEFAULT_EVIDENCE_DIR})",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero unless every required evidence file is present and every check passes",
    )
    args = parser.parse_args()

    evidence_dir = Path(args.evidence_dir)
    if not evidence_dir.is_dir():
        print(f"ERROR: evidence directory not found: {evidence_dir}", file=sys.stderr)
        sys.exit(2)

    report = aggregate(evidence_dir)

    # Write output
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True))

    # Print summary
    print(f"Track A (Qwen):  {'PASS' if report['tracks']['qwen']['passed'] else 'FAIL'}")
    print(f"Track B (CV):    {'PASS' if report['tracks']['cv']['passed'] else 'FAIL'}")
    print(f"E2E Summary (S1):{'PASS' if report['e2e_summary']['passed'] else 'FAIL'}")
    print(f"Overall:         {'PASS' if report['overall_passed'] else 'FAIL'}")

    if report["missing_evidence"]:
        print("\nMissing evidence:")
        for item in report["missing_evidence"]:
            print(f"  - {item}")

    if report["warnings"]:
        print("\nWarnings:")
        for w in report["warnings"]:
            print(f"  - {w}")

    print(f"\nReport written to: {out_path}")

    if args.strict:
        sys.exit(0 if report["overall_passed"] else 1)
    sys.exit(0)


if __name__ == "__main__":
    main()
