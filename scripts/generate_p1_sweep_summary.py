#!/usr/bin/env python3
"""Generate sfv-P1-sweep-summary.json from P1 sweep PERF logs."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

from analyze_sfu_perf import expected_cycles, tolerance_for

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = REPO_ROOT / "build" / "evidence"
SUMMARY_FILE = EVIDENCE_DIR / "sfv-P1-sweep-summary.json"

PERF_RE = re.compile(
    r"PERF\|case=([^|]+)\|op=([^|]+)\|event=([^|]+)\|cycles=(\d+)"
)

OP_FIELD_RE = re.compile(
    r"op=(\w+),dim=(\d+)(?:,pos=(\d+))?"
)

OPS = ["softmax", "layernorm", "rmsnorm", "rope"]
DIMS = [16, 32, 64, 128, 256, 512, 1024, 2048, 4096]


def parse_perf_log(log_path: Path) -> list[dict]:
    """Parse PERF| entries from a single simulation log."""
    entries = []
    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = PERF_RE.search(line)
            if m:
                entries.append(
                    {
                        "case": m.group(1),
                        "op_field": m.group(2),
                        "event": m.group(3),
                        "cycles": int(m.group(4)),
                    }
                )
    return entries


def extract_total_cycles(entries: list[dict]) -> list[dict]:
    """Return only TOTAL event entries with parsed op/dim/pos."""
    totals = []
    for e in entries:
        if e["event"] != "TOTAL":
            continue
        m = OP_FIELD_RE.match(e["op_field"])
        if not m:
            continue
        totals.append(
            {
                "case": e["case"],
                "op": m.group(1).lower(),
                "dim": int(m.group(2)),
                "pos": int(m.group(3)) if m.group(3) is not None else None,
                "cycles": e["cycles"],
            }
        )
    return totals


def build_summary() -> dict:
    """Build the P1 sweep summary structure."""
    logs = sorted(EVIDENCE_DIR.glob("sfv-P1*_r3_sim.log"))

    all_totals: list[dict] = []
    for log in logs:
        entries = parse_perf_log(log)
        totals = extract_total_cycles(entries)
        all_totals.extend(totals)

    # Group by (op, dim)
    grouped: dict[tuple[str, int], list[int]] = {}
    for t in all_totals:
        key = (t["op"], t["dim"])
        grouped.setdefault(key, []).append(t["cycles"])

    summary = {
        "metadata": {
            "task": "2.5 SFU P1 parameter sweep",
            "ops": OPS,
            "dims": DIMS,
            "runs_per_point": 3,
            "total_configs": len(OPS) * len(DIMS) * 3,
            "total_logs": len(logs),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "per_op": {},
        "cycle_table": {},
        "pass_summary": {
            "formula_pass_count": 0,
            "formula_fail_count": 0,
            "per_element_threshold_violations": [],
        },
    }

    formula_pass = 0
    formula_fail = 0
    pe_violations = []

    for op in OPS:
        op_results = []
        cycle_table = []
        for dim in DIMS:
            key = (op, dim)
            runs = sorted(grouped.get(key, []))
            exp = expected_cycles(op, dim)
            tol = tolerance_for(op)

            # Per-element threshold check
            # 1-cycle ops: gelu/silu/rope streaming; multi-cycle: softmax/layernorm/rmsnorm
            is_streaming = op in {"gelu", "silu", "rope"}
            pe_threshold = 1.0 if is_streaming else 8.0
            pe_values = [c / dim for c in runs]
            pe_viol = [c for c in pe_values if c > pe_threshold]
            if pe_viol:
                pe_violations.append(
                    {"op": op, "dim": dim, "threshold": pe_threshold, "values": pe_viol}
                )

            deltas = [c - exp for c in runs]
            passes = [abs(d) <= tol for d in deltas]
            op_results.append(
                {
                    "dim": dim,
                    "expected": exp,
                    "tolerance": tol,
                    "measured_runs": runs,
                    "deltas": deltas,
                    "mean_measured": round(mean(runs), 2) if runs else None,
                    "min_measured": min(runs) if runs else None,
                    "max_measured": max(runs) if runs else None,
                    "per_element_cycles": [round(v, 4) for v in pe_values],
                    "per_element_threshold": pe_threshold,
                    "all_runs_pass": all(passes) and len(runs) == 3,
                }
            )

            cycle_table.append(
                {
                    "dim": dim,
                    "expected": exp,
                    "measured_mean": round(mean(runs), 2) if runs else None,
                    "measured_min": min(runs) if runs else None,
                    "measured_max": max(runs) if runs else None,
                    "delta_mean": round(mean(deltas), 2) if runs else None,
                    "per_element_mean": round(mean(pe_values), 4) if runs else None,
                    "per_element_threshold": pe_threshold,
                }
            )

            if all(passes) and len(runs) == 3:
                formula_pass += 1
            else:
                formula_fail += 1

        summary["per_op"][op] = op_results
        summary["cycle_table"][op] = cycle_table

    summary["pass_summary"]["formula_pass_count"] = formula_pass
    summary["pass_summary"]["formula_fail_count"] = formula_fail
    summary["pass_summary"]["per_element_threshold_violations"] = pe_violations
    summary["pass_summary"]["overall_formula_pass"] = formula_fail == 0
    summary["pass_summary"][
        "overall_per_element_pass"
    ] = len(pe_violations) == 0

    return summary


def main() -> int:
    summary = build_summary()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_FILE.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[summary] wrote {SUMMARY_FILE}")
    print(
        f"[result] formula: {summary['pass_summary']['formula_pass_count']} PASS, "
        f"{summary['pass_summary']['formula_fail_count']} FAIL"
    )
    print(
        f"[result] per-element threshold violations: "
        f"{len(summary['pass_summary']['per_element_threshold_violations'])}"
    )
    return 0 if summary["pass_summary"]["overall_formula_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
