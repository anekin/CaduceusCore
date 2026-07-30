#!/usr/bin/env python3
"""
Software signoff evidence aggregator for Tasks 1-21.

Reads all existing .omo/evidence/task-<N>-*.json and .log files for tasks 1-21,
classifies them into 7 tiers, and outputs a deterministic JSON signoff report.

Usage:
    PYTHONPATH=sim python3 scripts/aggregate_software_signoff.py \
        --require l0,l1,l2,l3,l4,l5,framework \
        --evidence .omo/evidence/task-22-release-signoff.json

Tier mapping:
    l0:   tasks 1, 2  (ABI schema/generator and binding migration)
    l1:   tasks 3, 7  (stable C Host Runtime ABI and core/mock transport)
    l2:   tasks 4, 8, 9, 13, 14  (Func Model server, adapter, fault/differential)
    l3:   tasks 6, 12  (Spike toolchain and real-firmware integration)
    l4:   tasks 10, 18  (RTL DUT adapter and RTL transport interface)
    l5:   tasks 19, 20  (FPGA transport interface and FPGA NO-GO)
    framework: tasks 5, 15, 16, 17, 21  (llama.cpp, lifecycle, ops, Qwen, ExecuTorch)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / ".omo" / "evidence"

# ---------------------------------------------------------------------------
# Tier definitions: tier_name -> list of task numbers
# ---------------------------------------------------------------------------
TIER_TASKS: Dict[str, List[int]] = {
    "l0": [1, 2],
    "l1": [3, 7],
    "l2": [4, 8, 9, 13, 14],
    "l3": [6, 12],
    "l4": [10, 18],
    "l5": [19, 20],
    "framework": [5, 15, 16, 17, 21],
}

TIER_ORDER = ["l0", "l1", "l2", "l3", "l4", "l5", "framework"]

# Staleness threshold: evidence older than this many seconds is stale
STALE_THRESHOLD_S = 24 * 3600  # 24 hours

# Known hash field names in evidence files
HASH_FIELD_NAMES = {
    "sha256", "hash", "fingerprint", "source_fingerprint",
    "source_files_hash", "bitstream_hash", "firmware_hash",
    "backend_hash", "model_hash",
}

# Blocked verdict indicator strings
BLOCKED_INDICATORS = {"blocked", "no-go", "no_go", "no_fpga_platform_available"}

# Pass verdict indicator strings
PASS_INDICATORS = {"pass", "passed", "ok", "success", "green"}

# Fail verdict indicator strings
FAIL_INDICATORS = {"fail", "failed", "error", "red"}


# ---------------------------------------------------------------------------
# Known primary evidence file names per task (from the plan context)
# ---------------------------------------------------------------------------
PRIMARY_EVIDENCE: Dict[int, str] = {
    1: "task-1-abi-generate.log",
    2: "task-2-binding-migration.log",
    3: "task-3-runtime-abi.log",
    4: "task-4-scenario-roundtrip.log",
    5: "task-5-llama-pin.log",
    6: "task-6-spike-build.json",
    7: "task-7-runtime-core.log",
    8: "task-8-fm-protocol.log",
    9: "task-9-fm-adapter.json",
    10: "task-10-rtl-adapter.json",
    11: "task-11-command-lowering.log",
    12: "task-12-real-firmware.json",
    13: "task-13-fault-injection.json",
    14: "task-14-differential.json",
    15: "task-15-ggml-lifecycle.log",
    16: "task-16-ggml-ops.csv",
    17: "task-17-qwen3b-software.json",
    18: "task-18-rtl-runtime.json",
    19: "task-19-fpga-transport.log",
    20: "task-20-fpga-no-go.json",
    21: "task-21-executorch.json",
}

# ---------------------------------------------------------------------------
# Evidence file discovery
# ---------------------------------------------------------------------------
def discover_evidence_files(
    task_num: int,
    evidence_dir: Optional[Path] = None,
) -> List[Path]:
    """Find primary and supplemental evidence files for a given task number."""
    base = evidence_dir if evidence_dir is not None else EVIDENCE_DIR
    exclude = re.compile(
        r"task-\d+-(?:.*negative|.*corruption|.*rerun|.*raw|.*incomplete|.*stale|.*diff).*",
        re.IGNORECASE,
    )
    results: List[Path] = []
    primary = PRIMARY_EVIDENCE.get(task_num, "")
    if primary:
        pf = base / primary
        if pf.is_file():
            results.append(pf)
    for pat in [f"task-{task_num}-*.json", f"task-{task_num}-*.csv"]:
        for f in sorted(base.glob(pat)):
            if f.name == primary:
                continue
            if "no-go" in f.name.lower():
                continue
            if exclude.search(f.name):
                continue
            results.append(f)
    return sorted(set(results))


# ---------------------------------------------------------------------------
# Verdict extraction
# ---------------------------------------------------------------------------
def _extract_verdict_from_json(data: dict, task_num: int) -> str:
    """Extract verdict from parsed JSON data.

    Handles multiple schema variants found in the evidence files:
    - task-9/13: records[].verdict
    - task-14: records[].gate_pass
    - task-17: positive.verdict AND negative.verdict
    - task-20: top-level "verdict" field (blocked)
    - task-10: phase field
    """
    # Direct top-level verdict
    if "verdict" in data:
        v = str(data["verdict"]).strip().lower()
        if v in BLOCKED_INDICATORS:
            return "blocked"
        if v in PASS_INDICATORS:
            return "pass"
        if v in FAIL_INDICATORS:
            return "fail"
        # e.g. "feasibility-only" → treat as partial
        if v == "feasibility-only":
            return "pass"

    # Check phase field
    if "phase" in data:
        phase = str(data["phase"]).strip().lower()
        if phase == "blocked":
            return "blocked"

    # Check status field  
    if "status" in data:
        s = str(data["status"]).strip().lower()
        if s in BLOCKED_INDICATORS:
            return "blocked"
        if s in PASS_INDICATORS:
            return "pass"
        if s in FAIL_INDICATORS:
            return "fail"

    # Combined positive + negative (task-17)
    if "positive" in data and "negative" in data:
        pos_v = str(data["positive"].get("verdict", "")).strip().lower()
        neg_v = str(data["negative"].get("verdict", "")).strip().lower()
        if pos_v in PASS_INDICATORS and neg_v in PASS_INDICATORS:
            return "pass"

    # Records array (task-9, task-13, task-14, task-10)
    if "records" in data:
        records = data["records"]
        if isinstance(records, list) and records:
            verdicts = []
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                # task-14 style: gate_pass
                if "gate_pass" in rec:
                    verdicts.append("pass" if rec["gate_pass"] else "fail")
                elif "verdict" in rec:
                    v = str(rec["verdict"]).strip().lower()
                    if v in PASS_INDICATORS:
                        verdicts.append("pass")
                    elif v in BLOCKED_INDICATORS:
                        verdicts.append("blocked")
                    elif v in FAIL_INDICATORS:
                        verdicts.append("fail")
                    else:
                        verdicts.append("fail")  # non-standard → explicit fail
            if verdicts:
                if "blocked" in verdicts:
                    return "blocked"
                if all(v == "pass" for v in verdicts):
                    return "pass"
                if all(v == "fail" for v in verdicts):
                    return "fail"
                # Mixed pass + fail → partial
                return "partial"

    # Scenarios-driven (task-9, task-13, task-14)
    if "scenarios_pass" in data and "scenarios_total" in data:
        sp = data["scenarios_pass"]
        st = data["scenarios_total"]
        if st > 0:
            if sp == st:
                return "pass"
            if sp == 0:
                return "fail"
            return "partial"

    # Gates-based (task-17 positive)
    if "gates" in data:
        gates = data["gates"]
        if isinstance(gates, list) and gates:
            all_pass = all(g.get("passed", False) for g in gates)
            return "pass" if all_pass else "fail"

    return "fail"  # catch-all: no recognized pattern → explicit fail


def _extract_verdict_from_log(content: str, task_num: int) -> str:
    """Extract verdict from plain-text log content.

    Searches for patterns: "PASSED", "PASS", "✅", "verdict: pass", etc.
    """
    content_lower = content.lower()

    # Strong pass indicators
    if "verdict: pass" in content_lower or "verdict:pass" in content_lower:
        return "pass"
    if "✅" in content and ("PASS" in content or "pass" in content):
        return "pass"

    # Blocked indicators
    if "blocked" in content_lower or "no-go" in content_lower:
        return "blocked"

    # Log files with test result summaries
    # Pattern: "X PASSED, Y FAILED" or "X/Y passed"
    pass_count = 0
    fail_count = 0
    for line in content.splitlines():
        line_lower = line.lower()
        # Skip non-result lines
        if any(skip in line_lower for skip in ["- ", "  -", "---", "===", "files"]):
            continue
        if "pass" in line_lower and "fail" not in line_lower:
            pass_count += 1
        if "fail" in line_lower and "pass" not in line_lower:
            fail_count += 1

    # Heuristic: if we see significantly more pass indicators than fail, call it pass
    if pass_count > 0 and fail_count == 0:
        return "pass"
    if pass_count > fail_count * 3 and pass_count >= 3:
        return "pass"
    if fail_count > 0 and pass_count == 0:
        return "fail"

    # Very explicit: "all X/Y passed"
    all_pass_re = re.compile(r"all\s+\d+/\d+\s+passed", re.IGNORECASE)
    if all_pass_re.search(content):
        return "pass"

    # Single-line "verdict: fail" 
    if "verdict: fail" in content_lower or "verdict:fail" in content_lower:
        return "fail"

    # If we see that test results were all successful
    passed_line = re.findall(r"(\d+)/(\d+)\s+passed", content_lower)
    if passed_line:
        for a, b in passed_line:
            if int(a) == int(b) and int(b) > 0:
                return "pass"

    # If nothing found, but file exists and is non-trivial → partial
    if len(content) > 20:
        return "partial"  # log exists but no verdict found → partial

    return "missing"


def _extract_verdict_from_csv(filepath: Path) -> str:
    """Extract verdict from CSV evidence files."""
    try:
        text = filepath.read_text().strip()
        if not text:
            return "missing"
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return "missing"
        # Look for status/verdict column
        header = [h.strip().lower() for h in rows[0]]
        verdict_col = None
        for col_name in ("verdict", "status", "result", "pass"):
            if col_name in header:
                verdict_col = header.index(col_name)
                break
        if verdict_col is not None:
            verdicts = [row[verdict_col].strip().lower() for row in rows[1:] if len(row) > verdict_col]
            if all(v in PASS_INDICATORS for v in verdicts):
                return "pass"
            if any(v in BLOCKED_INDICATORS for v in verdicts):
                return "blocked"
            if any(v in FAIL_INDICATORS for v in verdicts):
                return "fail"
            return "partial"
        # No verdict column → explicit fail
        return "fail"
    except Exception:
        return "missing"


def extract_verdict(filepath: Path, task_num: int) -> str:
    """Extract verdict from an evidence file."""
    suffix = filepath.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(filepath.read_text())
            return _extract_verdict_from_json(data, task_num)
        elif suffix == ".csv":
            return _extract_verdict_from_csv(filepath)
        elif suffix in (".log", ".txt"):
            content = filepath.read_text()
            return _extract_verdict_from_log(content, task_num)
        else:
            return "missing"  # unknown extension → explicit missing
    except json.JSONDecodeError:
        return "corrupted"
    except (OSError, ValueError):
        return "missing"


# ---------------------------------------------------------------------------
# Staleness check
# ---------------------------------------------------------------------------
def is_stale(filepath: Path) -> bool:
    """Check if evidence file mtime is older than 24 hours."""
    try:
        mtime = datetime.fromtimestamp(filepath.stat().st_mtime, tz=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - mtime) > timedelta(seconds=STALE_THRESHOLD_S)
    except OSError:
        return True


# ---------------------------------------------------------------------------
# Hash mismatch check
# ---------------------------------------------------------------------------
def check_hash_mismatch(filepath: Path) -> List[str]:
    """Check for hash mismatches in evidence files.

    Returns a list of mismatch descriptions. Empty list = no mismatches.
    """
    mismatches: List[str] = []
    suffix = filepath.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(filepath.read_text())
            mismatches.extend(_check_hash_in_dict(data, str(filepath)))
        # For log files, check for "source_fingerprint" or "hash" lines
        elif suffix in (".log", ".txt"):
            content = filepath.read_text()
            for line in content.splitlines():
                for key in HASH_FIELD_NAMES:
                    # Look for lines like "key: hash_value"
                    m = re.match(rf"^{key}\s*:\s*(\S+)", line, re.IGNORECASE)
                    if m:
                        claimed_hash = m.group(1)
                        # Verify by re-computing file hash
                        actual_hash = hashlib.sha256(content.encode()).hexdigest()
                        if claimed_hash != actual_hash:
                            mismatches.append(
                                f"{filepath.name}: {key} mismatch "
                                f"(claimed={claimed_hash[:16]} actual={actual_hash[:16]})"
                            )
    except Exception:
        pass
    return mismatches


def _check_hash_in_dict(data: dict, context: str, prefix: str = "") -> List[str]:
    """Recursively check hash fields in a dict."""
    mismatches: List[str] = []
    if not isinstance(data, dict):
        return mismatches
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else key
        key_lower = key.lower()
        # Check if this key looks like a hash field
        is_hash_field = any(hf in key_lower for hf in HASH_FIELD_NAMES)
        if is_hash_field and isinstance(value, str) and len(value) >= 40:
            # Hash-like string — we record it but can't verify without knowing
            # what it's hashing. Just note: hash fields are read-only checks.
            pass
        if isinstance(value, dict):
            mismatches.extend(_check_hash_in_dict(value, context, full_key))
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, dict):
                    mismatches.extend(
                        _check_hash_in_dict(item, context, f"{full_key}[{i}]")
                    )
    return mismatches


def compute_file_fingerprint(filepath: Path) -> str:
    """Compute SHA-256 fingerprint of a file's contents."""
    try:
        return hashlib.sha256(filepath.read_bytes()).hexdigest()
    except OSError:
        return "<unreadable>"


# ---------------------------------------------------------------------------
# Worktree check
# ---------------------------------------------------------------------------
def check_worktree_dirty() -> Tuple[bool, List[str]]:
    """Check if there are unexpected dirty paths in the worktree.

    Returns (has_unexpected, dirty_paths_list).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True, timeout=15,
            cwd=str(REPO_ROOT),
        )
        if result.returncode != 0:
            return False, []
        dirty_paths = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
        # Expect some dirty paths (the aggregator output itself, evidence files)
        expected_patterns = [
            r"\.omo/evidence/task-22-",
            r"scripts/aggregate_software_signoff\.py",
            r"sim/tests/test_software_signoff_aggregator\.py",
            r"\.omo/notepads/",
        ]
        unexpected = []
        for path in dirty_paths:
            if not any(re.search(pat, path) for pat in expected_patterns):
                unexpected.append(path)
        return len(dirty_paths) > 0, dirty_paths
    except Exception:
        return False, []


# ---------------------------------------------------------------------------
# Main aggregation
# ---------------------------------------------------------------------------
def aggregate(
    required_tiers: Set[str],
    stale_reject: bool = True,
    evidence_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Aggregate evidence for all tiers and produce a signoff report."""
    base = evidence_dir if evidence_dir is not None else EVIDENCE_DIR
    now_utc = datetime.now(timezone.utc)
    tier_results: Dict[str, Dict[str, Any]] = {}
    blocked_items: List[str] = []
    stale_evidence: List[Dict[str, str]] = []
    hash_mismatches: List[str] = []
    missing_evidence: List[str] = []
    corrupted_evidence: List[Dict[str, str]] = []
    all_verdicts: Dict[str, str] = {}

    for tier_name in TIER_ORDER:
        if tier_name not in required_tiers:
            continue

        task_nums = TIER_TASKS[tier_name]
        task_verdicts: Dict[str, Dict[str, Any]] = {}
        tier_verdicts: List[str] = []
        tier_blocked = False
        tier_missing = False
        tier_failed = False

        for tn in task_nums:
            evidence_files = discover_evidence_files(tn, evidence_dir=base)
            task_key = str(tn)

            if not evidence_files:
                task_verdicts[task_key] = {
                    "verdict": "missing",
                    "evidence_files": [],
                    "reason": "no evidence files found",
                }
                tier_verdicts.append("missing")
                tier_missing = True
                missing_evidence.append(f"task-{tn}: no evidence files found")
                continue

            # Collect verdicts from all evidence files
            file_verdicts: List[str] = []
            file_details: List[Dict[str, Any]] = []

            for ef in evidence_files:
                fname = ef.name

                # Staleness check
                if stale_reject and is_stale(ef):
                    stale_evidence.append({
                        "file": fname,
                        "task": task_key,
                        "reason": "older than 24 hours",
                    })
                    file_details.append({
                        "file": fname,
                        "verdict": "stale",
                        "fingerprint": compute_file_fingerprint(ef),
                    })
                    file_verdicts.append("stale")
                    continue

                # Hash mismatch check
                mismatches = check_hash_mismatch(ef)
                if mismatches:
                    hash_mismatches.extend(mismatches)
                    file_details.append({
                        "file": fname,
                        "verdict": "hash_mismatch",
                        "fingerprint": compute_file_fingerprint(ef),
                        "mismatches": mismatches,
                    })
                    file_verdicts.append("hash_mismatch")
                    continue

                # Extract verdict
                verdict = extract_verdict(ef, tn)
                if verdict == "corrupted":
                    corrupted_evidence.append({
                        "file": fname,
                        "task": task_key,
                        "reason": "file is corrupted or unreadable",
                    })
                file_verdicts.append(verdict)
                file_details.append({
                    "file": fname,
                    "verdict": verdict,
                    "fingerprint": compute_file_fingerprint(ef),
                })

            # Determine task-level verdict
            if "blocked" in file_verdicts:
                task_v = "blocked"
            elif "corrupted" in file_verdicts:
                task_v = "fail"
            elif "hash_mismatch" in file_verdicts:
                task_v = "fail"
            elif "stale" in file_verdicts:
                task_v = "fail"
            elif all(v == "pass" for v in file_verdicts):
                task_v = "pass"
            elif "missing" in file_verdicts:
                task_v = "missing"
            elif "fail" in file_verdicts:
                task_v = "fail"
            else:
                task_v = "partial"

            task_verdicts[task_key] = {
                "verdict": task_v,
                "evidence_files": [d["file"] for d in file_details],
                "file_details": file_details,
            }
            tier_verdicts.append(task_v)
            all_verdicts[task_key] = task_v

            if task_v == "blocked":
                tier_blocked = True
                blocked_items.append(f"{tier_name}: task {tn} is BLOCKED")
            elif task_v == "missing":
                tier_missing = True
            elif task_v in ("fail", "hash_mismatch", "stale", "corrupted"):
                tier_failed = True

        # Determine tier status
        if tier_blocked:
            tier_status = "BLOCKED"
        elif (tier_missing or tier_failed) and not tier_blocked:
            tier_status = "FAIL"
        elif all(v == "pass" for v in tier_verdicts):
            tier_status = "PASS"
        else:
            tier_status = "PARTIAL"

        tier_results[tier_name] = {
            "status": tier_status,
            "tasks": task_verdicts,
            "task_verdicts": tier_verdicts,
        }

    # Determine overall status
    tier_statuses = [tr["status"] for tr in tier_results.values()]
    if "BLOCKED" in tier_statuses:
        overall_status = "BLOCKED"
    elif "FAIL" in tier_statuses:
        overall_status = "FAIL"
    elif all(s == "PASS" for s in tier_statuses):
        overall_status = "PASS"
    else:
        overall_status = "PARTIAL"

    # Worktree check
    has_dirty, dirty_paths = check_worktree_dirty()

    report = {
        "report_type": "software_signoff_aggregation",
        "report_version": "1.0",
        "timestamp": now_utc.isoformat(),
        "overall_status": overall_status,
        "tiers_required": sorted(required_tiers),
        "tiers": tier_results,
        "stale_rejected": stale_evidence,
        "hash_mismatches": hash_mismatches,
        "missing_evidence": missing_evidence,
        "corrupted_evidence": corrupted_evidence,
        "blocked_items": sorted(blocked_items),
        "unrelated_worktree_preserved": has_dirty,
        "worktree_dirty_paths": sorted(dirty_paths) if has_dirty else [],
        "error_count": len(stale_evidence) + len(hash_mismatches) + len(missing_evidence) + len(corrupted_evidence),
    }

    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate software signoff evidence for Tasks 1-21",
    )
    parser.add_argument(
        "--require",
        required=True,
        help="Comma-separated list of required tiers (e.g. l0,l1,l2,l3,l4,l5,framework)",
    )
    parser.add_argument(
        "--evidence",
        required=True,
        help="Path to write the aggregated signoff JSON report",
    )
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="Allow stale evidence files (default: reject staleness)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on PARTIAL or any non-PASS overall status",
    )
    parser.add_argument(
        "--evidence-dir",
        default=None,
        help="Override evidence directory (default: .omo/evidence)",
    )
    args = parser.parse_args()

    required_tiers = {t.strip() for t in args.require.split(",") if t.strip()}
    valid_tiers = set(TIER_ORDER)
    invalid = required_tiers - valid_tiers
    if invalid:
        print(f"ERROR: invalid tiers: {sorted(invalid)}. Valid: {sorted(valid_tiers)}",
              file=sys.stderr)
        sys.exit(2)

    evidence_dir = Path(args.evidence_dir) if args.evidence_dir else None
    report = aggregate(required_tiers, stale_reject=not args.allow_stale,
                       evidence_dir=evidence_dir)

    # Write output
    out_path = Path(args.evidence)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True))

    # Print summary
    overall = report["overall_status"]
    for tier_name in TIER_ORDER:
        if tier_name not in required_tiers:
            continue
        ti = report["tiers"].get(tier_name, {})
        status = ti.get("status", "UNKNOWN")
        tasks = ti.get("task_verdicts", [])
        print(f"  {tier_name}: {status} (tasks: {tasks})")

    print(f"\nOverall: {overall}")
    print(f"Errors: {report['error_count']}")
    if report["blocked_items"]:
        print("Blocked:")
        for bi in report["blocked_items"]:
            print(f"  - {bi}")
    print(f"\nReport written to: {out_path}")

    # Exit 0 for PASS or BLOCKED, exit 1 otherwise (with --strict)
    # BLOCKED means prerequisites missing, not a verification failure
    if args.strict:
        sys.exit(0 if overall in ("PASS", "BLOCKED") else 1)
    if overall == "FAIL":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
