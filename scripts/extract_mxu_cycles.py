#!/usr/bin/env python3
"""Batch-extract MXU cycle data from VCS simulation logs.

Scans rtl/results/vcs_sim_*.log, extracts scenario dimensions, tile counts,
START/IRQ timestamps, and computes compute cycles (1 ns clock period).
Outputs both CSV and JSON for downstream comparison and reporting.
"""

import csv
import json
import re
import sys
from pathlib import Path


# Regex patterns for the log lines we care about.
RE_SCENARIO_FROM_TESTDIR = re.compile(r"\+testdir=[^\s]+/(?P<scenario>[^/\s]+)")
RE_SCENARIO_FROM_ARG = re.compile(r"\+scenario=(?P<scenario>[^\s]+)")
RE_DIMENSIONS = re.compile(r"Parsed dimensions:\s*M=(\d+),\s*K=(\d+),\s*N=(\d+)")
RE_TILES = re.compile(r"tiles:\s*K=(\d+)\s+N=(\d+)\s+M=(\d+)")
RE_START = re.compile(r"Wrote CMD=START at (\d+)")
RE_IRQ = re.compile(r"IRQ asserted at (\d+)")
RE_DONE = re.compile(r"STATUS\.DONE asserted at (\d+)")

CSV_COLUMNS = [
    "scenario",
    "M",
    "K",
    "N",
    "tiles_K",
    "tiles_N",
    "tiles_M",
    "cycles",
]


def find_repo_root() -> Path:
    """Return the CaduceusCore repository root.

    The script may be run from the repo root, from CaduceusCore/, or from
    CaduceusCore/scripts/. Walk upward until we find the rtl/results dir or
    the directory containing this script, then resolve consistently.
    """
    script_dir = Path(__file__).resolve().parent
    # If installed under CaduceusCore/scripts/, rtl/ is a sibling of scripts/.
    candidate = script_dir.parent
    if (candidate / "rtl" / "results").is_dir():
        return candidate
    # Otherwise search upward from the current working directory.
    cwd = Path.cwd().resolve()
    for path in [cwd, *cwd.parents]:
        if (path / "rtl" / "results").is_dir():
            return path
    return candidate


def scenario_from_log_path(log_path: Path) -> str:
    """Derive a fallback scenario name from the log filename."""
    name = log_path.stem
    if name.startswith("vcs_sim_"):
        return name[len("vcs_sim_") :]
    return name


def extract_from_log(log_path: Path) -> dict | None:
    """Parse a single VCS log and return a data dict, or None if unusable."""
    text = log_path.read_text(encoding="utf-8", errors="replace")

    # Scenario: prefer +scenario=... then +testdir=.../scenario, then filename.
    scenario = None
    m_arg = RE_SCENARIO_FROM_ARG.search(text)
    if m_arg:
        scenario = m_arg.group("scenario")
    else:
        m_testdir = RE_SCENARIO_FROM_TESTDIR.search(text)
        if m_testdir:
            scenario = m_testdir.group("scenario")
    if scenario is None:
        scenario = scenario_from_log_path(log_path)

    m_dims = RE_DIMENSIONS.search(text)
    m_tiles = RE_TILES.search(text)
    m_start = RE_START.search(text)
    m_irq = RE_IRQ.search(text)
    m_done = RE_DONE.search(text)

    if not (m_dims and m_tiles and m_start and (m_irq or m_done)):
        return None

    m, k, n = map(int, m_dims.groups())
    tiles_k, tiles_n, tiles_m = map(int, m_tiles.groups())
    start_ps = int(m_start.group(1))
    end_ps = int((m_irq or m_done).group(1))

    if end_ps < start_ps:
        return None

    cycles = (end_ps - start_ps) // 1000

    return {
        "scenario": scenario,
        "M": m,
        "K": k,
        "N": n,
        "tiles_K": tiles_k,
        "tiles_N": tiles_n,
        "tiles_M": tiles_m,
        "cycles": cycles,
    }


def main() -> int:
    repo_root = find_repo_root()
    results_dir = repo_root / "rtl" / "results"
    log_files = sorted(results_dir.glob("vcs_sim_*.log"))

    if not log_files:
        print(f"No vcs_sim_*.log files found in {results_dir}", file=sys.stderr)
        return 1

    records = []
    for log_path in log_files:
        record = extract_from_log(log_path)
        if record is None:
            print(f"Skipping {log_path.name}: could not extract required fields", file=sys.stderr)
            continue
        records.append(record)

    if not records:
        print("No usable simulation logs found.", file=sys.stderr)
        return 1

    def _sort_key(r: dict) -> tuple:
        is_random = r["scenario"].startswith("random_")
        if is_random:
            try:
                idx = int(r["scenario"].split("_")[1])
            except (ValueError, IndexError):
                idx = 0
            return (1, idx, "")
        return (0, 0, r["scenario"])

    records.sort(key=_sort_key)

    csv_path = results_dir / "mxu_cycles.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(records)

    json_path = results_dir / "mxu_cycles.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2)

    print(f"Extracted {len(records)} scenarios to {csv_path} and {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
