#!/usr/bin/env python3
"""
Task 17 full SFU + Vector batch regression runner.

Discovers all scenario directories containing manifest.json under
CaduceusCore/rtl/test_vectors/{sfu,vector}/, writes batch files, compiles/runs
the fast VCS simv binaries once per engine, and produces an evidence file with
per-scenario PASS/FAIL results.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SFU_ROOT = REPO_ROOT / "CaduceusCore/rtl/test_vectors/sfu"
VECTOR_ROOT = REPO_ROOT / "CaduceusCore/rtl/test_vectors/vector"
RESULTS_DIR = REPO_ROOT / "CaduceusCore/rtl/results"
EVIDENCE_FILE = REPO_ROOT / ".omo/evidence/task-17-rerun.txt"
LEARNINGS_FILE = REPO_ROOT / ".omo/notepads/sfu-vector-phase2/learnings.md"

VCS_SETUP = "source /NAS/Tools/methodology/modules/init/bash && module load vcs/vcs_2023.12sp2"

SIMV_SFU = "/tmp/simv_tb_sfu_fast"
SIMV_VECTOR = "/tmp/simv_tb_vector_fast"

BATCH_SFU = "/tmp/sfu_batch.txt"
BATCH_VECTOR = "/tmp/vector_batch.txt"

BATCH_LOG_SFU = "/tmp/sfu_batch.log"
BATCH_LOG_VECTOR = "/tmp/vector_batch.log"

BATCH_RE = re.compile(r"^\[BATCH\]\s+(\S+)\s+(PASS|FAIL)$")


def discover_scenarios(root: Path) -> list[str]:
    """Return sorted scenario directory paths relative to REPO_ROOT."""
    scenarios: list[str] = []
    if root.exists():
        for manifest in sorted(root.rglob("manifest.json")):
            rel = manifest.parent.relative_to(REPO_ROOT)
            scenarios.append(str(rel))
    return scenarios


def write_batch_file(scenarios: list[str], path: str) -> None:
    with open(path, "w") as f:
        for scenario in scenarios:
            f.write(f"{scenario}\n")


def compile_simv(top: str, rtl_subdir: str, output: str) -> None:
    """Compile a fast simv binary without -debug_access."""
    cmd = (
        f"{VCS_SETUP} && cd {REPO_ROOT} && "
        f"vcs -full64 -sverilog -timescale=1ns/1ps -top {top} "
        f"CaduceusCore/rtl/tb/{top}.v CaduceusCore/rtl/{rtl_subdir}/*.v "
        f"-o {output} -l /tmp/{top}_compile.log"
    )
    print(f"[compile] {top} -> {output}")
    subprocess.run(cmd, shell=True, check=True, executable="/bin/bash")


def run_simv(simv: str, batchfile: str, logfile: str) -> None:
    """Run a compiled simv in batch mode."""
    cmd = f"{VCS_SETUP} && cd {REPO_ROOT} && {simv} +batchfile={batchfile} -l {logfile}"
    print(f"[run] {simv} +batchfile={batchfile}")
    subprocess.run(cmd, shell=True, check=True, executable="/bin/bash")


def parse_batch_log(logfile: str) -> list[tuple[str, bool]]:
    """Parse [BATCH] <scenario> PASS/FAIL lines from a VCS log."""
    results: list[tuple[str, bool]] = []
    with open(logfile) as f:
        for line in f:
            m = BATCH_RE.match(line.strip())
            if m:
                results.append((m.group(1), m.group(2) == "PASS"))
    return results


def run_engine(
    name: str,
    root: Path,
    simv: str,
    batchfile: str,
    logfile: str,
    top: str,
    rtl_subdir: str,
) -> tuple[int, int, list[str]]:
    scenarios = discover_scenarios(root)
    write_batch_file(scenarios, batchfile)
    print(f"[{name}] discovered {len(scenarios)} scenarios")

    if not Path(simv).exists():
        compile_simv(top, rtl_subdir, simv)

    run_simv(simv, batchfile, logfile)
    results = parse_batch_log(logfile)

    parsed = {name: ok for name, ok in results}
    if len(parsed) != len(scenarios):
        print(
            f"[{name}] warning: parsed {len(parsed)} results for {len(scenarios)} scenarios",
            file=sys.stderr,
        )

    passed = [s for s in scenarios if parsed.get(Path(s).name, False)]
    failed = [s for s in scenarios if not parsed.get(Path(s).name, True)]
    return len(scenarios), len(passed), failed


def main() -> int:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)

    timestamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sfu_total, sfu_passed, sfu_failed = run_engine(
        "SFU", SFU_ROOT, SIMV_SFU, BATCH_SFU, BATCH_LOG_SFU, "tb_sfu", "sfu"
    )
    vec_total, vec_passed, vec_failed = run_engine(
        "Vector", VECTOR_ROOT, SIMV_VECTOR, BATCH_VECTOR, BATCH_LOG_VECTOR,
        "tb_vector", "vector"
    )

    all_passed = not sfu_failed and not vec_failed

    lines = [
        f"Task 17 batch regression run at {timestamp}",
        f"SFU: {sfu_passed}/{sfu_total} passed",
        f"Vector: {vec_passed}/{vec_total} passed",
        "",
    ]

    if sfu_failed or vec_failed:
        lines.append("Failures:")
        for scenario in sfu_failed:
            lines.append(f"  [sfu] {scenario}")
        for scenario in vec_failed:
            lines.append(f"  [vector] {scenario}")
        lines.append("")
        lines.append("OVERALL: FAIL")
    else:
        lines.append("OVERALL: PASS")

    EVIDENCE_FILE.write_text("\n".join(lines) + "\n")
    print(f"[evidence] wrote {EVIDENCE_FILE}")

    if all_passed:
        learnings_line = f"\n## [{timestamp}] Task 17 full regression PASSED\n"
        if LEARNINGS_FILE.exists():
            with open(LEARNINGS_FILE, "a") as f:
                f.write(learnings_line)
        else:
            LEARNINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
            LEARNINGS_FILE.write_text(learnings_line)
        print(f"[learnings] appended to {LEARNINGS_FILE}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
