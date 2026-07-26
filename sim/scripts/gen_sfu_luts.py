#!/usr/bin/env python3
"""SFU LUT Generator — generates $readmemh-format hex files for RTL SFU LUTs.

Matches GoldenSFU._build_exp_lut / _build_gelu_lut semantics
(golden_executor.py:307-319, 381-395).
Covers:
  - 256-entry exponential LUT for exp(x) over [-20, 0] in Q1.14 fixed-point.
  - 64-entry GELU LUT over [-4, 4] in signed Q3.12 fixed-point.
  - 64-entry RoPE inv_freq LUT for head_dim=128 in signed Q0.30 fixed-point.

Usage:
    python3 CaduceusCore/sim/scripts/gen_sfu_luts.py --luts exp
    python3 CaduceusCore/sim/scripts/gen_sfu_luts.py --luts gelu
    python3 CaduceusCore/sim/scripts/gen_sfu_luts.py --luts all
"""

import argparse
import math
import sys
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════
# Q1.14 Fixed-Point Encoding (exp LUT)
# ══════════════════════════════════════════════════════════════════════
# 15-bit unsigned: 1 integer + 14 fraction bits.
# Value = raw / 16384.  Range: [0, 1.9999].
# 1.0 → raw = 16384 → hex 0x4000.
# 0.0 → raw =     0 → hex 0x0000.
# Rounding: round-half-up (Python built-in round()).

EXP_FRAC_BITS = 14
EXP_SCALE = 1 << EXP_FRAC_BITS  # 16384
MAX_U15 = 0x7FFF  # 32767


def _quantize_q1_14(val: float) -> int:
    """Quantize float to Q1.14 unsigned (15-bit), round-half-up, clamp."""
    raw = round(val * EXP_SCALE)
    if raw < 0:
        return 0
    if raw > MAX_U15:
        return MAX_U15
    return int(raw)


# ══════════════════════════════════════════════════════════════════════
# exp LUT — matches GoldenSFU._build_exp_lut (golden_executor.py:307-319)
# ══════════════════════════════════════════════════════════════════════

EXP_LUT_ENTRIES = 256
EXP_LUT_X_MIN = -20.0
EXP_LUT_X_MAX = 0.0


def generate_exp_lut() -> list[int]:
    """Generate 256-entry exp LUT in Q1.14.

    Domain: x ∈ [-20, 0], 256 linearly-spaced points.
    LUT stores exp(x) for each x.
    entry[0]   = exp(-20) ≈ 0
    entry[255] = exp(0)   = 1.0 → Q1.14 = 16384
    """
    xs = [EXP_LUT_X_MIN + i * (EXP_LUT_X_MAX - EXP_LUT_X_MIN) / (EXP_LUT_ENTRIES - 1)
          for i in range(EXP_LUT_ENTRIES)]
    return [_quantize_q1_14(math.exp(x)) for x in xs]


def write_hex(path: Path, values: list[int], width: int = 3,
              word_bits: int = 0, signed: bool = False):
    """Write hex file: one value per line, zero-padded to `width` hex digits.

    For 12-bit memories, use width=3 (VCS $readmemh requires hex digits
    not exceeding the memory word width). For 32-bit memories, use width=8.
    If `signed` is True, negative values are written as two's complement
    using `word_bits` total bits.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mask = (1 << word_bits) - 1 if word_bits else 0
    with open(path, "w") as f:
        for v in values:
            if signed and v < 0:
                v = v & mask
            f.write(f"{v:0{width}x}\n")


# ══════════════════════════════════════════════════════════════════════
# GELU LUT — matches GoldenSFU._build_gelu_lut (golden_executor.py:381-395)
# ══════════════════════════════════════════════════════════════════════

# Signed Q3.12: 1 sign + 3 integer + 12 fraction bits.
# Range [-8, 8), resolution 1/4096 ≈ 2.44e-4.
GELU_FRAC_BITS = 12
GELU_SCALE = 1 << GELU_FRAC_BITS  # 4096
GELU_MIN_S16 = -32768
GELU_MAX_S16 = 32767

GELU_LUT_ENTRIES = 64
GELU_LUT_X_MIN = -4.0
GELU_LUT_X_MAX = 4.0


def _quantize_s3_12(val: float) -> int:
    """Quantize float to signed Q3.12 (16-bit), round-half-up, clamp."""
    raw = round(val * GELU_SCALE)
    if raw < GELU_MIN_S16:
        return GELU_MIN_S16
    if raw > GELU_MAX_S16:
        return GELU_MAX_S16
    return int(raw)


def generate_gelu_lut() -> list[int]:
    """Generate 64-entry GELU LUT in signed Q3.12.

    Domain: x ∈ [-4, 4], 64 linearly-spaced points.
    Uses the tanh approximation from GoldenSFU:
        gelu(x) = 0.5 * x * (1 + tanh(sqrt(2/pi) * (x + 0.044715 * x^3)))
    """
    xs = [GELU_LUT_X_MIN + i * (GELU_LUT_X_MAX - GELU_LUT_X_MIN) / (GELU_LUT_ENTRIES - 1)
          for i in range(GELU_LUT_ENTRIES)]
    return [_quantize_s3_12(
        0.5 * x * (1.0 + math.tanh(math.sqrt(2.0 / math.pi) * (x + 0.044715 * x**3)))
    ) for x in xs]


# ══════════════════════════════════════════════════════════════════════
# RoPE inv_freq LUT — matches sfu_top.v theta generator
# ══════════════════════════════════════════════════════════════════════

ROPE_HEAD_DIM = 128
ROPE_INV_FRAC = 30
ROPE_INV_SCALE = 1 << ROPE_INV_FRAC
ROPE_THETA_BASE = 10000.0


def generate_rope_inv_freq_lut() -> list[int]:
    """Generate 64-entry RoPE inv_freq LUT in signed Q0.30.

    inv_freq[i] = 10000^(-2*i/128) for i = 0..63 (head_dim/2 pairs).
    Values are positive and fit in 32-bit signed fixed-point Q0.30.
    """
    values = []
    for i in range(ROPE_HEAD_DIM // 2):
        val = ROPE_THETA_BASE ** (-2.0 * i / ROPE_HEAD_DIM)
        values.append(int(round(val * ROPE_INV_SCALE)))
    return values


def validate_rope_inv_freq_lut(values: list[int]):
    """Self-check: 64 entries, monotonic decreasing, endpoints correct."""
    assert len(values) == ROPE_HEAD_DIM // 2, (
        f"Expected {ROPE_HEAD_DIM // 2} entries, got {len(values)}"
    )
    assert values[0] == int(round(1.0 * ROPE_INV_SCALE)), (
        f"entry[0] expected {int(round(1.0 * ROPE_INV_SCALE))}, got {values[0]}"
    )
    for i in range(1, len(values)):
        assert values[i] < values[i - 1], (
            f"Non-monotonic at entry {i}: {values[i]} >= {values[i - 1]}"
        )
        assert 0 <= values[i] < (1 << 31), (
            f"entry[{i}] = {values[i]} out of 32-bit signed range"
        )
    print(f"rope_theta_inv_freq validation PASSED: {len(values)} entries, endpoints OK")


# ══════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════

def validate_gelu_lut(values: list[int]):
    """Self-check: 64 entries, endpoints match tanh approximation."""
    assert len(values) == GELU_LUT_ENTRIES, (
        f"Expected {GELU_LUT_ENTRIES} entries, got {len(values)}"
    )

    # Endpoints: gelu(-4) ≈ 0, gelu(4) ≈ 4
    assert abs(values[0]) <= 2, (
        f"entry[0] (gelu(-4)) expected ~0, got {values[0]}"
    )
    assert abs(values[-1] - _quantize_s3_12(4.0)) <= 2, (
        f"entry[63] (gelu(4)) expected ~{_quantize_s3_12(4.0)}, got {values[-1]}"
    )

    # All values in signed 16-bit range
    for i, v in enumerate(values):
        assert GELU_MIN_S16 <= v <= GELU_MAX_S16, (
            f"entry[{i}] = {v} out of 16-bit signed range"
        )

    print(f"gelu_lut validation PASSED: {len(values)} entries, endpoints OK")


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
    assert values[255] == _quantize_q1_14(1.0), (
        f"entry[255] (exp(0)) expected {_quantize_q1_14(1.0)}, got {values[255]}"
    )

    # All values in range
    for i, v in enumerate(values):
        assert 0 <= v <= MAX_U15, f"entry[{i}] = {v} out of 15-bit range"

    print(f"exp_lut validation PASSED: {len(values)} entries, "
          f"monotonic, endpoints OK")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate SFU LUT hex files")
    parser.add_argument("--luts", choices=["exp", "gelu", "all"], default="all",
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
        write_hex(out_dir / "exp_lut.hex", exp_vals, width=4)
        validate_exp_lut(exp_vals)
        print(f"Generated: {out_dir / 'exp_lut.hex'} ({len(exp_vals)} entries, Q1.14)")

    if args.luts in ("gelu", "all"):
        gelu_vals = generate_gelu_lut()
        write_hex(out_dir / "gelu_lut.hex", gelu_vals, width=4,
                  word_bits=16, signed=True)
        validate_gelu_lut(gelu_vals)
        print(f"Generated: {out_dir / 'gelu_lut.hex'} ({len(gelu_vals)} entries, signed Q3.12)")

    if args.luts in ("exp", "all"):
        rope_vals = generate_rope_inv_freq_lut()
        write_hex(out_dir / "rope_theta_inv_freq.hex", rope_vals, width=8,
                  word_bits=32, signed=True)
        validate_rope_inv_freq_lut(rope_vals)
        print(f"Generated: {out_dir / 'rope_theta_inv_freq.hex'} ({len(rope_vals)} entries, signed Q0.30)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
