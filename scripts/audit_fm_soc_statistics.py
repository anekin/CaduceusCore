#!/usr/bin/env python3
# =============================================================================
# audit_fm_soc_statistics.py — structured 33-case FM-SOC statistics audit
# (plan soc-rtl-review-remediation todo 9, Metis M7)
# =============================================================================
# Parses the 33 FM-SOC case logs produced by run_ibex_full_rtl.sh and derives
# the structured classification {executed, superseded, na, failed, timeout},
# then compares each case against docs/fm_soc_case_manifest.csv
# (25 EXECUTED / 6 SUPERSEDED / 2 N/A).  Any mismatch exits non-zero.
#
# Classification mirrors the fixed runner loop (exit-code-first, then the
# rtl_soc_runner.py:4279/:4282 structured messages, then the cocotb PASS
# summary), reading the runner's own `runner_classification=` markers where
# present (TIMEOUT/FAIL are only distinguishable through those markers).
#
# Usage:
#   python3 scripts/audit_fm_soc_statistics.py
#   python3 scripts/audit_fm_soc_statistics.py --evidence-dir build/ibex_full_rtl/evidence
# =============================================================================

import argparse
import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

MARKER_TIMEOUT = "runner_classification=TIMEOUT"
MARKER_FAIL = "runner_classification=FAIL"
MSG_SUPERSEDED = "superseded by FM-SOC-027/032/10X"
MSG_NA = "skipped: direct APB/AXI case not applicable to Ibex RTL mode"
MSG_PASS_SUMMARY = "TESTS=1 PASS=1 FAIL=0 SKIP=0"

EXPECTED_SPLIT = {"EXECUTED": 25, "SUPERSEDED": 6, "N/A": 2}
TOTAL_CASES = 33


def classify_log(text: str) -> str:
    """Classify one case log into executed/superseded/na/failed/timeout."""
    if MARKER_TIMEOUT in text:
        return "timeout"
    if MARKER_FAIL in text:
        return "failed"
    if MSG_SUPERSEDED in text:
        return "superseded"
    if MSG_NA in text:
        return "na"
    if MSG_PASS_SUMMARY in text:
        return "executed"
    return "failed"


def load_manifest(path: Path) -> dict:
    """Return {case_id: expected_status}; exit 2 on structural problems."""
    if not path.is_file():
        print(f"ERROR: manifest not found: {path}", file=sys.stderr)
        sys.exit(2)
    manifest: dict = {}
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None or "case_id" not in reader.fieldnames \
                or "expected_status" not in reader.fieldnames:
            print("ERROR: manifest missing case_id/expected_status columns", file=sys.stderr)
            sys.exit(2)
        for row in reader:
            manifest[row["case_id"].strip()] = row["expected_status"].strip()
    if len(manifest) != TOTAL_CASES:
        print(f"ERROR: manifest has {len(manifest)} case rows, expected {TOTAL_CASES}", file=sys.stderr)
        sys.exit(2)
    for status, count in EXPECTED_SPLIT.items():
        actual = sum(1 for s in manifest.values() if s == status)
        if actual != count:
            print(f"ERROR: manifest {status} count is {actual}, expected {count}", file=sys.stderr)
            sys.exit(2)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit 33 FM-SOC case logs against docs/fm_soc_case_manifest.csv")
    parser.add_argument("--evidence-dir",
                        default=str(REPO_ROOT / "build" / "ibex_full_rtl" / "evidence"),
                        help="directory holding the 33 <CASE>.log files")
    parser.add_argument("--manifest",
                        default=str(REPO_ROOT / "docs" / "fm_soc_case_manifest.csv"),
                        help="case manifest CSV")
    parser.add_argument("--json-out", default=None,
                        help="write JSON result to this file (default: stdout)")
    args = parser.parse_args()

    evidence_dir = Path(args.evidence_dir)
    manifest = load_manifest(Path(args.manifest))

    counts = {"executed": 0, "superseded": 0, "na": 0, "failed": 0, "timeout": 0}
    mismatches = []
    for case_id, expected in manifest.items():
        log = evidence_dir / f"{case_id}.log"
        if not log.is_file():
            actual = "missing"
        else:
            actual = classify_log(log.read_text(errors="replace"))
        counts[actual] = counts.get(actual, 0) + 1
        expected_class = {"EXECUTED": "executed", "SUPERSEDED": "superseded", "N/A": "na"}[expected]
        if actual != expected_class:
            mismatches.append({"case_id": case_id, "expected": expected_class, "actual": actual})

    result = {
        "executed": counts["executed"],
        "superseded": counts["superseded"],
        "na": counts["na"],
        "failed": counts["failed"],
        "timeout": counts["timeout"],
        "total_cases": TOTAL_CASES,
        "matched": TOTAL_CASES - len(mismatches),
        "mismatches": mismatches,
    }
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
        print(f"[audit] JSON written to {args.json_out}")
    else:
        print(payload)

    if mismatches:
        for m in mismatches:
            print(f"[audit] MISMATCH {m['case_id']}: expected {m['expected']}, got {m['actual']}",
                  file=sys.stderr)
        print(f"[audit] RESULT: FAIL — {len(mismatches)} case(s) deviate from the manifest",
              file=sys.stderr)
        return 1
    print(f"[audit] RESULT: PASS — all {TOTAL_CASES} cases match the manifest "
          f"({counts['executed']} executed / {counts['superseded']} superseded / "
          f"{counts['na']} na / {counts['failed']} failed / {counts['timeout']} timeout)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
