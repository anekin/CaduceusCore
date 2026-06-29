#!/usr/bin/env python3
"""SF-01: Generate golden numpy.exp values as FP16 hex for all 256 exp_lut entries.

Usage: python3 CaduceusCore/scripts/gen_sf01_golden.py
"""
import json
import math
import struct
import sys
from pathlib import Path

EXP_ENTRIES = 256
X_MIN = -20.0
X_MAX = 0.0

TEST_DIR = Path(__file__).resolve().parent.parent / "rtl" / "test_vectors" / "sfu" / "sf01_exp_lut_256"


def float_to_fp16_hex(v: float) -> str:
    """Convert Python float to FP16 4-digit hex string."""
    # Use numpy float16 if available, else manual IEEE 754 half-precision
    try:
        import numpy as np
        u16 = np.float16(v).view(np.uint16)
        return f"{int(u16):04x}"
    except ImportError:
        pass
    # Manual conversion (simplified for values 0..1)
    if v <= 0:
        return "0000"
    if v >= 1.0:
        return "3c00"
    # Find exponent
    exp = 0
    frac = v
    while frac < 1.0:
        frac *= 2.0
        exp -= 1
    # frac is now in [1.0, 2.0)
    mant = int((frac - 1.0) * 1024 + 0.5)  # 10-bit mantissa, round half-up
    if mant >= 1024:
        exp += 1
        mant = 0
    biased_exp = exp + 15
    if biased_exp <= 0:
        return "0000"
    if biased_exp >= 31:
        return "3c00"
    u16 = (biased_exp << 10) | mant
    return f"{u16 & 0xFFFF:04x}"


def main():
    # 256 linearly-spaced x values in [-20, 0]
    xs = [X_MIN + i * (X_MAX - X_MIN) / (EXP_ENTRIES - 1) for i in range(EXP_ENTRIES)]
    golden_fp = [math.exp(x) for x in xs]

    # Write golden_output.hex as FP16 hex
    golden_path = TEST_DIR / "golden_output.hex"
    with open(golden_path, "w") as f:
        for v in golden_fp:
            f.write(f"{float_to_fp16_hex(v)}\n")
    print(f"SF-01: Golden written to {golden_path} ({EXP_ENTRIES} entries)")

    # Write manifest.json
    manifest = {
        "name": "sf01_exp_lut_256",
        "sfu_op": True,
        "files": {"golden": "golden_output.hex"},
        "results": {"golden_shape": [EXP_ENTRIES]},
    }
    manifest_path = TEST_DIR / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print(f"SF-01: Manifest written to {manifest_path}")

    # Also verify the existing LUT hex file is correct (Python-side check)
    lut_hex_path = Path(__file__).resolve().parent.parent / "rtl" / "test_vectors" / "sfu" / "luts" / "exp_lut.hex"
    if lut_hex_path.exists():
        errors = 0
        max_err = 0.0
        with open(lut_hex_path) as f:
            for i, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                raw = int(line, 16)
                hw_val = raw / 16384.0  # Q1.14
                gold = math.exp(xs[i])
                err = abs(hw_val - gold)
                if err > max_err:
                    max_err = err
                if err >= 1.0 / (2**14):  # Q1.14 quantization error limit
                    errors += 1
                    print(f"  FAIL: entry[{i}] x={xs[i]:.4f} hw={hw_val:.6e} gold={gold:.6e} err={err:.6e}")
        q14_limit = 1.0 / (2**14)
        print(f"SF-01 Python check: {EXP_ENTRIES - errors}/{EXP_ENTRIES} within Q1.14 limit ({q14_limit:.6e})")
        if errors > 0:
            print(f"SF-01 Python check: {errors} FAILURES (max_err={max_err:.6e})")
            return 1
        print(f"SF-01 Python check: ALL PASS (max_err={max_err:.6e})")
    else:
        print(f"SF-01: WARNING: exp_lut.hex not found at {lut_hex_path}, skipping Python check")

    return 0


if __name__ == "__main__":
    sys.exit(main())
