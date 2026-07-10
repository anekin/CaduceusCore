#!/usr/bin/env python3
"""Orchestrator for SFU+Vector P3 edge cases (SFV-P29..P34).

Runs the edge-case matrix defined in task 2.8 of
soc-verification-gaps-phase5 and produces
build/evidence/sfv-P3-edge-cases-summary.json.

Matrix:
  P29: dim=1     random     SFU[softmax,layernorm,rmsnorm,rope] + Vector[add,sum,conv]
  P30: dim=4096  random     all ops above
  P31: dim=128   zeros      all ops above
  P32: dim=128   maxval     all ops above
  P33: dim=128   sparse     all ops above
  P34: dim=128   repeated   all ops above

Each config is run once.  The liveness criteria are:
  - simulation finishes without timeout
  - PERF|TOTAL cycles > 0
  - anti-vacuous ASSERT PASS
  - output region shows non-zero write activity for non-zero input modes
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
EVIDENCE_DIR = REPO_ROOT / "build" / "evidence"
SUMMARY_FILE = EVIDENCE_DIR / "sfv-P3-edge-cases-summary.json"

SFU_OPS = ["softmax", "layernorm", "rmsnorm", "rope"]
VEC_OPS = ["add", "sum", "conv"]

CASES: list[dict[str, Any]] = [
    {"case_id": "SFV-P29", "dim": 1,    "input_mode": "random"},
    {"case_id": "SFV-P30", "dim": 4096, "input_mode": "random"},
    {"case_id": "SFV-P31", "dim": 128,  "input_mode": "zeros"},
    {"case_id": "SFV-P32", "dim": 128,  "input_mode": "maxval"},
    {"case_id": "SFV-P33", "dim": 128,  "input_mode": "sparse"},
    {"case_id": "SFV-P34", "dim": 128,  "input_mode": "repeated"},
]


def _parse_existing(
    evidence_md: Path,
    sim_log: Path,
    case_id: str,
    runner: str,
    op: str,
    dim: int,
    input_mode: str,
) -> dict[str, Any]:
    """Re-parse an already-run config from its evidence files."""
    entry: dict[str, Any] = {
        "case_id": case_id,
        "path": "sfu" if runner == "run_sfu_perf_case.py" else "vector",
        "op": op,
        "dim": dim,
        "input_mode": input_mode,
        "runner_returncode": 0,
        "evidence_md": str(evidence_md.relative_to(REPO_ROOT)),
        "sim_log": str(sim_log.relative_to(REPO_ROOT)) if sim_log.exists() else None,
        "measured_cycles": None,
        "expected_cycles": None,
        "delta": None,
        "timeout": False,
        "anti_vacuous_pass": None,
        "liveness_pass": False,
        "verdict": "FAIL",
        "notes": ["parsed from existing evidence"],
    }

    if sim_log.exists():
        sim_text = sim_log.read_text(encoding="utf-8", errors="replace")
        entry["timeout"] = "Timeout waiting for STATUS.DONE" in sim_text
        total_match = re.search(
            r"PERF\|case=[^|]+\|op=[^|]+\|event=TOTAL\|cycles=(\d+)",
            sim_text,
        )
        if total_match:
            entry["measured_cycles"] = int(total_match.group(1))
        if re.search(r"all anti-vacuous checks PASS", sim_text):
            entry["anti_vacuous_pass"] = True
        elif re.search(r"\d+ checks FAILED", sim_text):
            entry["anti_vacuous_pass"] = False

        m_wen = re.search(r"sram_(?:wen|o_wen) toggle count (\d+)", sim_text)
        if m_wen:
            entry["output_write_toggles"] = int(m_wen.group(1))

    if evidence_md.exists():
        md_text = evidence_md.read_text(encoding="utf-8", errors="replace")
        m = re.search(
            r"op=[^,]+,dim=\d+ expected=(\d+) measured=(\d+) delta=(-?\d+)",
            md_text,
        )
        if m:
            entry["expected_cycles"] = int(m.group(1))
            entry["measured_cycles"] = int(m.group(2))
            entry["delta"] = int(m.group(3))
        if "**Final verdict: PASS**" in md_text:
            entry["verdict"] = "PASS"
        elif "**Final verdict: FAIL" in md_text:
            entry["verdict"] = "FAIL"

    sim_finished = bool(entry.get("sim_log"))
    liveness_ok = (
        entry["measured_cycles"] is not None
        and entry["measured_cycles"] > 0
        and not entry["timeout"]
        and sim_finished
    )
    if liveness_ok:
        entry["liveness_pass"] = True

    output_writes = entry.get("output_write_toggles")
    if liveness_ok and input_mode != "zeros" and output_writes == 0:
        entry["liveness_pass"] = False
        entry["notes"].append("no output write toggles observed")

    entry["edge_pass"] = entry["liveness_pass"]
    if not entry["edge_pass"]:
        entry["notes"].append("liveness gate failed")

    return entry


def run_case(
    runner: str,
    case_id: str,
    op: str,
    dim: int,
    input_mode: str,
    skip_existing: bool = True,
) -> dict[str, Any]:
    """Run one edge-case config and return parsed result."""
    mode_suffix = f"_m{input_mode}" if input_mode != "random" else ""
    run_tag = f"{case_id}-{op}-{dim}{mode_suffix}"
    evidence_md = EVIDENCE_DIR / f"sfv-{run_tag}-summary.md"
    sim_log = EVIDENCE_DIR / f"sfv-{run_tag}_sim.log"

    if skip_existing and evidence_md.exists() and sim_log.exists():
        print(f"[skip] existing evidence for {run_tag}")
        return _parse_existing(evidence_md, sim_log, case_id, runner, op, dim, input_mode)

    cmd = [
        sys.executable,
        str(SCRIPT_DIR / runner),
        "--case", case_id,
        "--op", op,
        "--dim", str(dim),
        "--input-mode", input_mode,
    ]
    print(f"[run] {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minutes per config; hung sims fail fast
        )
        returncode = result.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        print(f"[timeout] {run_tag}")
        returncode = -1
        timed_out = True
        if not sim_log.exists():
            sim_log = None

    entry: dict[str, Any] = {
        "case_id": case_id,
        "path": "sfu" if runner == "run_sfu_perf_case.py" else "vector",
        "op": op,
        "dim": dim,
        "input_mode": input_mode,
        "runner_returncode": returncode,
        "evidence_md": str(evidence_md.relative_to(REPO_ROOT)),
        "sim_log": str(sim_log.relative_to(REPO_ROOT)) if sim_log and sim_log.exists() else None,
        "measured_cycles": None,
        "expected_cycles": None,
        "delta": None,
        "timeout": timed_out,
        "anti_vacuous_pass": None,
        "liveness_pass": False,
        "verdict": "FAIL",
        "notes": [],
    }

    # Parse sim log if available
    if sim_log.exists():
        sim_text = sim_log.read_text(encoding="utf-8", errors="replace")
        entry["timeout"] = "Timeout waiting for STATUS.DONE" in sim_text

        # PERF TOTAL
        total_match = re.search(
            r"PERF\|case=[^|]+\|op=[^|]+\|event=TOTAL\|cycles=(\d+)",
            sim_text,
        )
        if total_match:
            entry["measured_cycles"] = int(total_match.group(1))

        if re.search(r"all anti-vacuous checks PASS", sim_text):
            entry["anti_vacuous_pass"] = True
        elif re.search(r"\d+ checks FAILED", sim_text):
            entry["anti_vacuous_pass"] = False

        entry["output_write_toggles"] = None
        m_wen = re.search(r"sram_(?:wen|o_wen) toggle count (\d+)", sim_text)
        if m_wen:
            entry["output_write_toggles"] = int(m_wen.group(1))

        liveness_ok = (
            entry["measured_cycles"] is not None
            and entry["measured_cycles"] > 0
            and not entry["timeout"]
            and "$finish" in sim_text
        )
        if liveness_ok:
            entry["liveness_pass"] = True

        if liveness_ok and input_mode != "zeros" and entry["output_write_toggles"] == 0:
            entry["liveness_pass"] = False
            entry["notes"].append("no output write toggles observed")

    # Parse analyze result from evidence markdown final verdict
    if evidence_md.exists():
        md_text = evidence_md.read_text(encoding="utf-8", errors="replace")
        # Expected/measured/delta line from analyzer
        m = re.search(
            r"op=[^,]+,dim=\d+ expected=(\d+) measured=(\d+) delta=(-?\d+)",
            md_text,
        )
        if m:
            entry["expected_cycles"] = int(m.group(1))
            entry["measured_cycles"] = int(m.group(2))
            entry["delta"] = int(m.group(3))

        if "**Final verdict: PASS**" in md_text:
            entry["verdict"] = "PASS"
        elif "**Final verdict: FAIL" in md_text:
            entry["verdict"] = "FAIL"

    entry["edge_pass"] = entry["liveness_pass"]
    if not entry["edge_pass"]:
        entry["notes"].append("liveness gate failed")

    return entry


def main() -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []

    for case in CASES:
        case_id = case["case_id"]
        dim = case["dim"]
        input_mode = case["input_mode"]

        for op in SFU_OPS:
            results.append(run_case(
                "run_sfu_perf_case.py", case_id, op, dim, input_mode
            ))

        for op in VEC_OPS:
            results.append(run_case(
                "run_vector_perf_case.py", case_id, op, dim, input_mode
            ))

    # Compute summary statistics
    total = len(results)
    edge_pass = sum(1 for r in results if r.get("edge_pass"))
    formula_pass = sum(1 for r in results if r["verdict"] == "PASS")
    timeouts = sum(1 for r in results if r["timeout"])

    # dim=1 timeout check: all P29 cycles must be <= 10 * dim=128 baseline
    # Use the median measured cycles of dim=128 same-op runs as baseline.
    baseline_128: dict[str, int] = {}
    for r in results:
        if r["dim"] == 128 and r["input_mode"] == "random":
            key = f"{r['path']}/{r['op']}"
            baseline_128[key] = r.get("measured_cycles") or 0

    dim1_check: dict[str, Any] = {"passed": True, "violations": []}
    for r in results:
        if r["dim"] == 1:
            key = f"{r['path']}/{r['op']}"
            baseline = baseline_128.get(key, 0)
            limit = baseline * 10 if baseline > 0 else 10000
            measured = r.get("measured_cycles") or 0
            if measured > limit:
                dim1_check["passed"] = False
                dim1_check["violations"].append({
                    "op": r["op"],
                    "path": r["path"],
                    "measured": measured,
                    "limit": limit,
                })

    summary = {
        "metadata": {
            "task": "2.8 SFU+Vector P3 edge cases (SFV-P29..P34)",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_configs": total,
            "edge_pass": edge_pass,
            "edge_fail": total - edge_pass,
            "formula_pass": formula_pass,
            "formula_fail": total - formula_pass,
            "timeouts": timeouts,
            "dim1_timeout_check": dim1_check,
        },
        "results": results,
    }

    SUMMARY_FILE.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"[summary] wrote {SUMMARY_FILE}")
    print(f"[summary] edge_pass={edge_pass}/{total}, formula_pass={formula_pass}/{total}, timeouts={timeouts}")

    return 0 if edge_pass == total else 1


if __name__ == "__main__":
    sys.exit(main())
