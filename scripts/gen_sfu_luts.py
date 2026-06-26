#!/usr/bin/env python3
"""SFU LUT Generator — generates $readmemh-format hex files for RTL SFU LUTs.

Matches GoldenSFU._build_exp_lut / _build_gelu_lut semantics
(golden_executor.py:307-319, 381-395).
Covers:
  - 256-entry exponential LUT for exp(x) over [-20, 0]:
    * exp_lut.hex              — Q1.14, 15-bit (shared LUT for silu_hw, softmax_hw).
    * softmax_exp_lut_q12.hex  — Q0.12, 12-bit (legacy, kept for reference).
  - 64-entry GELU LUT over [-4, 4] in signed Q3.12 fixed-point.

Usage:
    python3 CaduceusCore/scripts/gen_sfu_luts.py --luts exp
    python3 CaduceusCore/scripts/gen_sfu_luts.py --luts gelu
    python3 CaduceusCore/scripts/gen_sfu_luts.py --luts all
"""

import argparse
import math
import sys
from pathlib import Path


# ══════════════════════════════════════════════════════════════════════
# Shared exp LUT constants
# ══════════════════════════════════════════════════════════════════════

EXP_LUT_ENTRIES = 256
EXP_LUT_X_MIN = -20.0
EXP_LUT_X_MAX = 0.0


def _generate_exp_samples() -> list[float]:
    """256 linearly-spaced x values in [-20, 0] and their exp(x)."""
    xs = [EXP_LUT_X_MIN + i * (EXP_LUT_X_MAX - EXP_LUT_X_MIN) / (EXP_LUT_ENTRIES - 1)
          for i in range(EXP_LUT_ENTRIES)]
    return [math.exp(x) for x in xs]


# ══════════════════════════════════════════════════════════════════════
# Q1.14 Fixed-Point (15-bit) — shared exp_lut.hex
# ══════════════════════════════════════════════════════════════════════
# 1 integer + 14 fraction bits (15-bit unsigned).
# Value = raw / 16384.  Range: [0, ~1.99994].
# 1.0 → raw = 16384 → hex 0x4000.
# 0.0 → raw =     0 → hex 0x0000.
# Rounding: round-half-up (Python built-in round()).

Q1P14_FRAC = 14
Q1P14_SCALE = 1 << Q1P14_FRAC  # 16384
Q1P14_MASK  = (1 << (Q1P14_FRAC + 1)) - 1  # 32767 (15-bit)


def _quantize_q1_14(val: float) -> int:
    """Quantize float to Q1.14 unsigned (15-bit), round-half-up, clamp."""
    raw = round(val * Q1P14_SCALE)
    if raw < 0:
        return 0
    if raw > Q1P14_MASK:
        return Q1P14_MASK
    return raw


# ══════════════════════════════════════════════════════════════════════
# Q0.12 Fixed-Point (12-bit) — legacy softmax_exp_lut_q12.hex
# ══════════════════════════════════════════════════════════════════════
# 12-bit unsigned: 0 integer + 12 fraction bits.
# Value = raw / 4096.  Range: [0, ~0.99976].
# 1.0 → raw = 4096 → saturated to 4095 (12-bit max).
# Rounding: round-half-up, then clamp.

Q0P12_FRAC = 12
Q0P12_SCALE = 1 << Q0P12_FRAC  # 4096
Q0P12_MAX   = (1 << Q0P12_FRAC) - 1  # 4095


def _quantize_q0_12(val: float) -> int:
    """Quantize float to Q0.12 unsigned (12-bit), round-half-up, clamp."""
    raw = round(val * Q0P12_SCALE)
    if raw < 0:
        return 0
    if raw > Q0P12_MAX:
        return Q0P12_MAX
    return raw


# ══════════════════════════════════════════════════════════════════════
# Legacy Q8.4 — kept for reference; no longer used by active RTL.
# ══════════════════════════════════════════════════════════════════════

Q8P4_FRAC  = 4
Q8P4_SCALE = 1 << Q8P4_FRAC  # 16
Q8P4_MAX   = 0xFFF  # 4095


def _quantize_q8_4(val: float) -> int:
    """Quantize float to Q8.4 unsigned (12-bit), round-half-up, clamp."""
    raw = round(val * Q8P4_SCALE)
    if raw < 0:
        return 0
    if raw > Q8P4_MAX:
        return Q8P4_MAX
    return raw


# ══════════════════════════════════════════════════════════════════════
# exp LUT — matches GoldenSFU._build_exp_lut (golden_executor.py:307-319)
# ══════════════════════════════════════════════════════════════════════

def generate_exp_lut_q1_14() -> list[int]:
    """Generate 256-entry exp LUT in Q1.14 (15-bit).

    Domain: x ∈ [-20, 0], 256 linearly-spaced points.
    entry[0]   = exp(-20) ≈ 0
    entry[255] = exp(0)   = 1.0 → Q1.14 = 16384 (0x4000)
    """
    return [_quantize_q1_14(v) for v in _generate_exp_samples()]


def generate_exp_lut_q0_12() -> list[int]:
    """Generate 256-entry exp LUT in Q0.12 (12-bit).

    Saturated at 4095/4096 (max 12-bit). Last entry ≈ 0.99976.
    Used by softmax_hw for backward compatibility.
    """
    return [_quantize_q0_12(v) for v in _generate_exp_samples()]


def write_hex(path: Path, values: list[int], width: int = 3,
              word_bits: int = 0, signed: bool = False):
    """Write hex file: one value per line, zero-padded to `width` hex digits.

    For 12-bit memories, use width=3 (VCS $readmemh requires hex digits
    not exceeding the memory word width). For 13-bit memories, use width=4.
    For 32-bit memories, use width=8.
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
    raw = round(val * GELU_SCALE)
    if raw < GELU_MIN_S16:
        return GELU_MIN_S16
    if raw > GELU_MAX_S16:
        return GELU_MAX_S16
    return int(raw)


def generate_gelu_lut() -> list[int]:
    xs = [GELU_LUT_X_MIN + i * (GELU_LUT_X_MAX - GELU_LUT_X_MIN) / (GELU_LUT_ENTRIES - 1)
          for i in range(GELU_LUT_ENTRIES)]
    return [_quantize_s3_12(
        0.5 * x * (1.0 + math.tanh(math.sqrt(2.0 / math.pi) * (x + 0.044715 * x**3)))
    ) for x in xs]


# ══════════════════════════════════════════════════════════════════════
# Validation
# ══════════════════════════════════════════════════════════════════════

def validate_gelu_lut(values: list[int]):
    assert len(values) == GELU_LUT_ENTRIES, (
        f"Expected {GELU_LUT_ENTRIES} entries, got {len(values)}"
    )
    assert abs(values[0]) <= 2, (
        f"entry[0] (gelu(-4)) expected ~0, got {values[0]}"
    )
    assert abs(values[-1] - _quantize_s3_12(4.0)) <= 2, (
        f"entry[63] (gelu(4)) expected ~{_quantize_s3_12(4.0)}, got {values[-1]}"
    )
    for i, v in enumerate(values):
        assert GELU_MIN_S16 <= v <= GELU_MAX_S16, (
            f"entry[{i}] = {v} out of 16-bit signed range"
        )
    print(f"gelu_lut validation PASSED: {len(values)} entries, endpoints OK")


def _validate_exp_lut(values: list[int], max_val: int, label: str):
    assert len(values) == 256, f"Expected 256 entries, got {len(values)}"
    for i in range(1, len(values)):
        assert values[i] >= values[i - 1], (
            f"{label}: Non-monotonic at entry {i}: {values[i]} < {values[i - 1]}"
        )
    assert values[0] == 0, f"{label}: entry[0] (exp(-20)) expected 0, got {values[0]}"
    for i, v in enumerate(values):
        assert 0 <= v <= max_val, f"{label}: entry[{i}] = {v} out of range [0, {max_val}]"
    print(f"{label} validation PASSED: {len(values)} entries, "
          f"monotonic, v[0]=0, v[255]={values[255]}")


def validate_exp_lut_q1_14(values: list[int]):
    _validate_exp_lut(values, Q1P14_MASK, "exp_lut Q1.14")
    assert values[255] == _quantize_q1_14(1.0), (
        f"entry[255] (exp(0)=1.0) expected {_quantize_q1_14(1.0)}, got {values[255]}"
    )


def validate_exp_lut_q0_12(values: list[int]):
    _validate_exp_lut(values, Q0P12_MAX, "exp_lut Q0.12")


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Generate SFU LUT hex files")
    parser.add_argument("--luts", choices=["exp", "gelu", "all"], default="all",
                        help="Which LUTs to generate (default: all)")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    caduceus_root = script_dir.parent  # scripts/ → CaduceusCore/
    out_dir = caduceus_root / "rtl" / "test_vectors" / "sfu" / "luts"

    if args.luts in ("exp", "all"):
        # Primary: Q1.14 (15-bit) — shared LUT for silu_hw / softmax_hw
        exp_q1_14 = generate_exp_lut_q1_14()
        write_hex(out_dir / "exp_lut.hex", exp_q1_14, width=4)
        validate_exp_lut_q1_14(exp_q1_14)
        print(f"Generated: {out_dir / 'exp_lut.hex'} ({len(exp_q1_14)} entries, Q1.14 15-bit)")
        print(f"  entry[0]={exp_q1_14[0]}, entry[255]={exp_q1_14[255]} (1.0→0x{exp_q1_14[255]:04x})")

        # Legacy: Q0.12 (12-bit) — kept for reference
        exp_q0_12 = generate_exp_lut_q0_12()
        write_hex(out_dir / "softmax_exp_lut_q12.hex", exp_q0_12, width=3)
        validate_exp_lut_q0_12(exp_q0_12)
        print(f"Generated: {out_dir / 'softmax_exp_lut_q12.hex'} ({len(exp_q0_12)} entries, Q0.12 12-bit)")

    if args.luts in ("gelu", "all"):
        gelu_vals = generate_gelu_lut()
        write_hex(out_dir / "gelu_lut.hex", gelu_vals, width=4,
                  word_bits=16, signed=True)
        validate_gelu_lut(gelu_vals)
        print(f"Generated: {out_dir / 'gelu_lut.hex'} ({len(gelu_vals)} entries, signed Q3.12)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
