#!/usr/bin/env python3
"""SF-01 post-processing: Convert Q1.14 raw hex to FP16 and compare with golden.

Usage:
    python3 scripts/post_sf01_compare.py

Reads /tmp/sf01_result_raw.hex (Q1.14 format), converts to FP16,
writes /tmp/sf01_result.hex, then calls compare_sfu.py.
"""
import json
import struct
import sys
from pathlib import Path
import numpy as np


def q1_14_to_fp16(raw_val: int) -> float:
    """Convert Q1.14 raw integer to Python float."""
    return raw_val / 16384.0


def main():
    raw_path = Path("CaduceusCore/rtl/results/sf01_result_raw.hex")
    result_path = Path("CaduceusCore/rtl/results/sf01_result.hex")
    test_dir = Path("CaduceusCore/rtl/test_vectors/sfu/sf01_exp_lut_256")

    # Read raw Q1.14 values
    raw_vals = []
    with raw_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                raw_vals.append(int(line, 16))

    if len(raw_vals) != 256:
        print(f"SF-01 ERROR: Expected 256 entries, got {len(raw_vals)}", file=sys.stderr)
        return 1

    # Convert to FP16 and write
    floats = [q1_14_to_fp16(v) for v in raw_vals]
    f16 = np.array(floats, dtype=np.float16).view(np.uint16)
    with result_path.open("w") as f:
        for v in f16:
            f.write(f"{int(v):04x}\n")
    print(f"SF-01: Converted {len(f16)} Q1.14 values to FP16 → {result_path}")

    # Also validate Q1.14 quantization error vs numpy.exp
    # (This is the actual SF-01 metric: each hw entry within 1/2^14 of np.exp)
    xs = [-20.0 + i * 20.0 / 255.0 for i in range(256)]
    q14_tol = 1.0 / (2**14)  # 6.1e-5
    errors = 0
    max_err = 0.0
    for i, (raw, x) in enumerate(zip(raw_vals, xs)):
        hw_val = raw / 16384.0
        gold = float(np.exp(x))
        err = abs(hw_val - gold)
        if err > max_err:
            max_err = err
        if err >= q14_tol:
            errors += 1
            print(f"  FAIL: entry[{i}] x={x:.4f} hw={hw_val:.6e} gold={gold:.6e} err={err:.6e}")
    print(f"SF-01 Q1.14 check: {256 - errors}/256 within Q1.14 limit ({q14_tol:.6e}), max_err={max_err:.6e}")
    if errors > 0:
        print(f"SF-01 Q1.14 check: {errors} FAILURES")
        return 1

    # Now run compare_sfu.py (FP16-level comparison)
    compare_script = Path("CaduceusCore/scripts/compare_sfu.py")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(compare_script), str(test_dir), str(result_path)],
        cwd="/home/prj/zhengs/caduceuscore",
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr)
        print("SF-01: compare_sfu.py FAILED")
        return 1
    print("SF-01: compare_sfu.py PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
