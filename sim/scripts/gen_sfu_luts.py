#!/usr/bin/env python3
"""SFU LUT Generator — generates $readmemh-format hex files for RTL SFU LUTs.

Matches GoldenSFU._build_exp_lut semantics (golden_executor.py:307-319).
Covers 256-entry exponential LUT for exp(x) over [-20, 0] in Q8.4 fixed-point.

Usage:
    python3 CaduceusCore/sim/scripts/gen_sfu_luts.py --luts exp
    python3 CaduceusCore/sim/scripts/gen_sfu_luts.py --luts all
"""

import argparse
import math
import sys
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════
# Q8.4 Fixed-Point Encoding
# ══════════════════════════════════════════════════════════════════════
# 12-bit unsigned: 8 integer + 4 fraction bits.
# Value = raw / 16.  Range: [0, 255.9375].
# 1.0 → raw = 16 → hex 0x0010.
# 0.0 → raw =  0 → hex 0x0000.
# Rounding: round-half-up (Python built-in round()).

FP_FRAC_BITS = 4
FP_SCALE = 1 << FP_FRAC_BITS  # 16
MAX_U12 = 0xFFF  # 4095


def _quantize_q8_4(val: float) -> int:
    """Quantize float to Q8.4 unsigned (12-bit), round-half-up, clamp."""
    raw = round(val * FP_SCALE)
    if raw < 0:
        return 0
    if raw > MAX_U12:
        return MAX_U12
    return raw


# ══════════════════════════════════════════════════════════════════════
# exp LUT — matches GoldenSFU._build_exp_lut (golden_executor.py:307-319)
# ══════════════════════════════════════════════════════════════════════

EXP_LUT_ENTRIES = 256
EXP_LUT_X_MIN = -20.0
EXP_LUT_X_MAX = 0.0


def generate_exp_lut() -> list[int]:
    """Generate 256-entry exp LUT in Q8.4.

    Domain: x ∈ [-20, 0], 256 linearly-spaced points.
    LUT stores exp(x) for each x.
    entry[0]   = exp(-20) ≈ 0
    entry[255] = exp(0)   = 1.0 → Q8.4 = 16
    """
    xs = [EXP_LUT_X_MIN + i * (EXP_LUT_X_MAX - EXP_LUT_X_MIN) / (EXP_LUT_ENTRIES - 1)
          for i in range(EXP_LUT_ENTRIES)]
    return [_quantize_q8_4(math.exp(x)) for x in xs]


def write_hex(path: Path, values: list[int], width: int = 3):
    """Write hex file: one value per line, zero-padded to `width` hex digits.

    For 12-bit memories, use width=3 (VCS $readmemh requires hex digits
    not exceeding the memory word width). For 32-bit memories, use width=8.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for v in values:
            f.write(f"{v:0{width}x}\n")


# ══════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════

def validate_exp_lut(values: list[int]):
    """Self-check: monotonic, endpoints correct, 256 entries."""
    assert len(values) == 256, f"Expected 256 entries, got {len(values)}"

    # Monotonic non-decreasing
    for i in range(1, len(values)):
        assert values[i] >= values[i - 1], (
            f"Non-monotonic at entry {i}: {values[i]} < {values[i - 1]}"
        )

    # Endpoints
    assert values[0] == 0, f"entry[0] (exp(-20)) expected 0, got {values[0]}"
    assert values[255] == _quantize_q8_4(1.0), (
        f"entry[255] (exp(0)) expected {_quantize_q8_4(1.0)}, got {values[255]}"
    )

    # All values in range
    for i, v in enumerate(values):
        assert 0 <= v <= MAX_U12, f"entry[{i}] = {v} out of 12-bit range"

    print(f"exp_lut validation PASSED: {len(values)} entries, "
          f"monotonic, endpoints OK")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate SFU LUT hex files")
    parser.add_argument("--luts", choices=["exp", "all"], default="all",
                        help="Which LUTs to generate (default: all)")
    args = parser.parse_args()

    # Determine output directory relative to project root
    # Script is at: CaduceusCore/sim/scripts/gen_sfu_luts.py
    # Output goes to: CaduceusCore/rtl/test_vectors/sfu/luts/
    script_dir = Path(__file__).resolve().parent
    # Navigate: sim/scripts/ → sim/ → CaduceusCore/
    caduceus_root = script_dir.parent.parent
    out_dir = caduceus_root / "rtl" / "test_vectors" / "sfu" / "luts"

    if args.luts in ("exp", "all"):
        exp_vals = generate_exp_lut()
        write_hex(out_dir / "exp_lut.hex", exp_vals)
        validate_exp_lut(exp_vals)
        print(f"Generated: {out_dir / 'exp_lut.hex'} ({len(exp_vals)} entries, Q8.4)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
