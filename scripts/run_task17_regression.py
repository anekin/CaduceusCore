#!/usr/bin/env python3
"""Task 17 full batch regression runner."""
import json
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SFU_DIR = REPO_ROOT / "rtl/test_vectors/sfu"
VEC_DIR = REPO_ROOT / "rtl/test_vectors/vector"
SFU_SIMV = os.environ.get("SFU_SIMV", str(REPO_ROOT / "build/simv_tb_sfu_infra"))
VEC_SIMV = os.environ.get("VEC_SIMV", str(REPO_ROOT / "build/simv_tb_vector_sumfix"))
RESULTS_FILE = REPO_ROOT / "build/evidence/task-17-rerun.txt"
JOBS = int(os.environ.get("JOBS", "4"))


def discover_scenarios(base: Path):
    out = []
    for d in sorted(base.iterdir()):
        if not d.is_dir():
            continue
        if d.name == "random_regression":
            for sub in sorted(d.iterdir()):
                if sub.is_dir():
                    out.append((sub.relative_to(REPO_ROOT), sub.name))
        else:
            out.append((d.relative_to(REPO_ROOT), d.name))
    return out


def run_one(args):
    simv, testdir_rel, name, kind = args
    cmd = [
        simv,
        f"+testdir={testdir_rel}",
        f"+scenario={name}",
    ]
    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=180,
        )
        output = proc.stdout
        passed = "INLINE_COMPARE: PASS" in output or "[TB] PASS: All" in output or "All outputs match golden" in output
        if not passed and "INLINE_COMPARE: FAIL" in output:
            passed = False
        # For vector the pass string is [TB] PASS: All ...
        if "[TB] PASS:" in output:
            passed = True
        if "FAIL" in output and "INLINE_COMPARE: PASS" not in output:
            passed = False
        elapsed = time.time() - start
        return kind, name, passed, elapsed, output[-1000:]
    except subprocess.TimeoutExpired as e:
        return kind, name, False, 180.0, f"TIMEOUT\n{e.stdout[-500:] if e.stdout else ''}"
    except Exception as e:
        return kind, name, False, 0.0, f"EXCEPTION: {e}"


def main():
    tasks = []
    for rel, name in discover_scenarios(SFU_DIR):
        tasks.append((SFU_SIMV, rel, name, "sfu"))
    for rel, name in discover_scenarios(VEC_DIR):
        tasks.append((VEC_SIMV, rel, name, "vector"))

    print(f"Total scenarios: {len(tasks)} (SFU={len([t for t in tasks if t[3]=='sfu'])}, Vector={len([t for t in tasks if t[3]=='vector'])})")

    with multiprocessing.Pool(JOBS) as pool:
        results = pool.map(run_one, tasks)

    sfu_results = [r for r in results if r[0] == "sfu"]
    vec_results = [r for r in results if r[0] == "vector"]
    sfu_pass = sum(1 for r in sfu_results if r[2])
    vec_pass = sum(1 for r in vec_results if r[2])

    lines = []
    lines.append("=" * 80)
    lines.append("Task 17 Full Batch Regression Results")
    lines.append(f"Date: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"SFU:   {sfu_pass}/{len(sfu_results)} passed")
    lines.append(f"Vector: {vec_pass}/{len(vec_results)} passed")
    lines.append(f"Total: {sfu_pass+vec_pass}/{len(results)} passed")
    lines.append("=" * 80)

    for kind, name, passed, elapsed, tail in results:
        status = "PASS" if passed else "FAIL"
        lines.append(f"[{status}] {kind:6} {name:40} ({elapsed:.1f}s)")
        if not passed:
            lines.append("--- tail ---")
            lines.append(tail)
            lines.append("--- end tail ---")

    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_FILE.write_text("\n".join(lines))
    print(f"Results written to {RESULTS_FILE}")
    print(f"SFU {sfu_pass}/{len(sfu_results)} | Vector {vec_pass}/{len(vec_results)}")
    if sfu_pass != len(sfu_results) or vec_pass != len(vec_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
