#!/usr/bin/env python3
"""
W1.3: Run Qwen2.5-3B 3-layer verification on the RTL SoC testbench.

Steps:
  1. Ensure RTL test vectors exist (run generator if missing).
  2. Invoke the cocotb RTL target on sz0001 via sim/regression/soc-verification-run.sh.
  3. Compare RTL per-layer outputs with W1.2 Func Model golden vectors.
  4. Write evidence to build/evidence/w1-3-rtl-3layer.txt.

Usage (on sz0001):
    PYTHONPATH=sim:scripts python3 scripts/run_qwen25_3b_rtl.py
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT / "sim"))
sys.path.insert(0, str(_PROJECT / "scripts"))

from qwen25_forward import cosine_similarity

W12_GOLDEN_DIR = _PROJECT / "rtl" / "test_vectors" / "soc_e2e" / "qwen25-3b-3layer"
RTL_VECTORS_DIR = _PROJECT / "rtl" / "test_vectors" / "soc_e2e" / "qwen25-3b-3layer-rtl"
RTL_OUTPUTS = _PROJECT / "build" / "wave1" / "w1-3-rtl-layer-outputs.npz"
EVIDENCE_PATH = _PROJECT / "build" / "evidence" / "w1-3-rtl-3layer.txt"


def generate_vectors(layers: list) -> None:
    from gen_qwen25_3b_rtl_vectors import generate_vectors

    generate_vectors(layers)


def run_rtl_simulation() -> tuple:
    make_cmd = [
        "make", "-C", str(_PROJECT / "sim" / "regression"),
        "run_qwen25_3b_3layer",
    ]
    print(f"[W1.3] Running RTL simulation: {' '.join(make_cmd)}")
    result = subprocess.run(make_cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    return result.returncode == 0, result.stdout + result.stderr


def compare_outputs() -> dict:
    if not RTL_OUTPUTS.exists():
        raise FileNotFoundError(f"RTL outputs not found: {RTL_OUTPUTS}")

    golden_path = RTL_VECTORS_DIR / "expected.npz"
    if not golden_path.exists():
        raise FileNotFoundError(f"W1.3 golden not found: {golden_path}")

    rtl = np.load(RTL_OUTPUTS)
    golden = np.load(golden_path)

    layers = []
    for key in sorted(rtl.keys()):
        if key.startswith("layer_") and key.endswith("_output"):
            layer_idx = int(key.split("_")[1])
            layers.append(layer_idx)

    # RTL VRESID post-FFN outputs are INT32 in fixed-point scale 1024.
    VRESID_SCALE = 1024.0

    results = {"tests": len(layers), "passed": 0, "failed": 0, "per_layer": {}}
    for layer_idx in sorted(layers):
        rtl_key = f"layer_{layer_idx}_output"
        golden_key = f"layer_{layer_idx}_output_rtl"
        rtl_arr = rtl[rtl_key].astype(np.float64) / VRESID_SCALE
        golden_arr = golden[golden_key].astype(np.float64).flatten() / VRESID_SCALE
        golden_rounded = np.rint(golden_arr)

        cos_sim = cosine_similarity(rtl_arr, golden_arr)
        rel_err = float(np.max(np.abs(rtl_arr - golden_arr) / (np.abs(golden_arr) + 1e-8)))
        max_abs = float(np.max(np.abs(rtl_arr - golden_arr)))
        max_abs_rounded = float(np.max(np.abs(rtl_arr - golden_rounded)))
        passed = cos_sim >= 0.999 and max_abs_rounded <= 1.0

        results["per_layer"][layer_idx] = {
            "cos_sim": cos_sim,
            "max_rel_err": rel_err,
            "max_abs_err": max_abs,
            "max_abs_err_vs_rounded": max_abs_rounded,
            "passed": passed,
        }
        if passed:
            results["passed"] += 1
        else:
            results["failed"] += 1

    return results


def write_evidence(results: dict) -> None:
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVIDENCE_PATH, "w") as f:
        f.write("# W1.3: Qwen2.5-3B 3-Layer SoC RTL Forward Pass\n")
        f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"TESTS={results['tests']} PASS={results['passed']} FAIL={results['failed']}\n\n")
        for layer_idx in sorted(results["per_layer"].keys()):
            r = results["per_layer"][layer_idx]
            status = "PASS" if r["passed"] else "FAIL"
            f.write(
                f"[{status}] Layer {layer_idx}: "
                f"cos_sim={r['cos_sim']:.6f}, "
                f"max_rel_err={r['max_rel_err']:.2e}, "
                f"max_abs_err={r['max_abs_err']:.2e}, "
                f"max_abs_err_vs_rounded={r['max_abs_err_vs_rounded']:.2e}\n"
            )
    print(f"[W1.3] Evidence saved: {EVIDENCE_PATH}")


def main():
    parser = argparse.ArgumentParser(description="W1.3 RTL 3-layer forward pass")
    parser.add_argument("--layers", type=int, nargs="+", default=[0, 1, 2])
    parser.add_argument("--skip-generate", action="store_true", help="Skip vector generation")
    parser.add_argument("--skip-rtl", action="store_true", help="Skip RTL simulation")
    args = parser.parse_args()

    layers = sorted(set(args.layers))

    if not args.skip_generate:
        generate_vectors(layers)

    if not args.skip_rtl:
        ok, _ = run_rtl_simulation()
        if not ok:
            print("[W1.3] RTL simulation failed", file=sys.stderr)
            sys.exit(1)

    results = compare_outputs()
    write_evidence(results)

    print(f"\n[W1.3] TESTS={results['tests']} PASS={results['passed']} FAIL={results['failed']}")
    if results["failed"] > 0:
        sys.exit(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
