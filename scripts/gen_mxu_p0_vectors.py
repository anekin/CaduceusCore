#!/usr/bin/env python3
"""MXU P0 Test Vector Generator — golden data for submodule-level verification.

Usage:
    python3 CaduceusCore/scripts/gen_mxu_p0_vectors.py mx01
    python3 CaduceusCore/scripts/gen_mxu_p0_vectors.py all
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_CADUCEUS_CORE = _SCRIPT_DIR.parent
sys.path.insert(0, str(_CADUCEUS_CORE))

from sim.golden_executor import GoldenMXU, INT32_MAX, INT32_MIN  # noqa: E402

OUT_BASE = _CADUCEUS_CORE / "rtl" / "test_vectors" / "mxu_p0"


def write_hex_int32(path, values):
    with open(path, "w") as f:
        for v in np.asarray(values, dtype=np.int32).flat:
            f.write(f"{int(v) & 0xFFFFFFFF:08x}\n")


def write_manifest(path, case_name, golden_shape):
    manifest = {
        "name": case_name,
        "files": {"golden": "golden_output.hex"},
        "results": {"golden_shape": list(golden_shape)},
    }
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def _as_int32(val):
    return np.int32(np.uint32(val))


def gen_mx01():
    """weight_buffer nibble ordering: low nibble=even index, high=odd."""
    case_dir = OUT_BASE / "mx01"
    case_dir.mkdir(parents=True, exist_ok=True)
    DEPTH = 512
    golden = np.zeros(DEPTH, dtype=np.int32)
    for addr in range(DEPTH):
        b0_lo = (addr + 0) & 0x0F
        b0_hi = (addr + 1) & 0x0F
        b1_lo = (addr + 2) & 0x0F
        b1_hi = (addr + 3) & 0x0F
        b2_lo = (addr + 4) & 0x0F
        b2_hi = (addr + 5) & 0x0F
        b3_lo = (addr + 6) & 0x0F
        b3_hi = (addr + 7) & 0x0F
        word = (b3_hi << 28) | (b3_lo << 24) | (b2_hi << 20) | (b2_lo << 16) | \
               (b1_hi << 12) | (b1_lo << 8)  | (b0_hi << 4)  | b0_lo
        golden[addr] = _as_int32(word)
    write_hex_int32(case_dir / "golden_output.hex", golden)
    write_manifest(case_dir / "manifest.json", "mx01", (DEPTH,))

    packed_bytes = []
    for addr in range(DEPTH):
        w = int(golden[addr]) & 0xFFFFFFFF
        packed_bytes.extend([w & 0xFF, (w >> 8) & 0xFF, (w >> 16) & 0xFF, (w >> 24) & 0xFF])
    packed = np.array(packed_bytes, dtype=np.uint8)
    unpacked = GoldenMXU.unpack_int4(packed)
    ok = True
    for i in range(len(unpacked)):
        expected = ((i // 8) + (i % 8)) & 0x0F
        if unpacked[i] != expected:
            print(f"  MISMATCH at weight[{i}]: got={unpacked[i]}, expected={expected}")
            ok = False
            break
    print("  Golden self-check: PASS" if ok else "  Golden self-check: FAIL")
    print(f"  MX-01 golden: {case_dir}/golden_output.hex ({DEPTH} words)")


def gen_mx02():
    """weight_buffer multi-cycle write burst: 1024 back-to-back writes, final read-back."""
    case_dir = OUT_BASE / "mx02"
    case_dir.mkdir(parents=True, exist_ok=True)
    DEPTH = 512
    golden = np.zeros(DEPTH, dtype=np.int32)
    for addr in range(DEPTH):
        b0 = (addr + 0x10) & 0xFF
        b1 = (addr + 0x20) & 0xFF
        b2 = (addr + 0x40) & 0xFF
        b3 = (addr + 0x80) & 0xFF
        golden[addr] = _as_int32((b3 << 24) | (b2 << 16) | (b1 << 8) | b0)
    write_hex_int32(case_dir / "golden_output.hex", golden)
    write_manifest(case_dir / "manifest.json", "mx02", (DEPTH,))
    print(f"  MX-02 golden: {case_dir}/golden_output.hex ({DEPTH} words)")


def gen_mx03():
    """activation_buffer concurrent read-write: full buffer after test."""
    case_dir = OUT_BASE / "mx03"
    case_dir.mkdir(parents=True, exist_ok=True)
    DEPTH = 1024
    golden = np.zeros(DEPTH, dtype=np.int32)
    for addr in range(DEPTH):
        b0 = (addr + 0xAB) & 0xFF
        b1 = (addr + 0xCD) & 0xFF
        b2 = (addr + 0xEF) & 0xFF
        b3 = (addr + 0x12) & 0xFF
        golden[addr] = _as_int32((b3 << 24) | (b2 << 16) | (b1 << 8) | b0)
    write_hex_int32(case_dir / "golden_output.hex", golden)
    write_manifest(case_dir / "manifest.json", "mx03", (DEPTH,))
    print(f"  MX-03 golden: {case_dir}/golden_output.hex ({DEPTH} words)")


def gen_mx04():
    """accumulator saturation: overflow → INT32_MAX clamp, not wrap."""
    case_dir = OUT_BASE / "mx04"
    case_dir.mkdir(parents=True, exist_ok=True)
    golden = np.array([
        INT32_MAX,        # MAX+1 → MAX
        INT32_MIN,        # MIN+(-1) → MIN
        INT32_MAX,        # (MAX-1)+1 → MAX (boundary)
        INT32_MIN,        # (MIN+1)+(-1) → MIN (boundary)
        INT32_MAX,        # MAX+100 → MAX
        3000,             # 1000+2000 → 3000
    ], dtype=np.int32)
    write_hex_int32(case_dir / "golden_output.hex", golden)
    write_manifest(case_dir / "manifest.json", "mx04", (len(golden),))
    print(f"  MX-04 golden: {case_dir}/golden_output.hex ({len(golden)} tests)")


def gen_mx05():
    """accumulator address conflict: accumulate + read_out same address."""
    case_dir = OUT_BASE / "mx05"
    case_dir.mkdir(parents=True, exist_ok=True)
    golden = np.array([
        100,              # accum(100)+read → 100
        300,              # accum(200)+read → 300
        INT32_MAX,        # accum(MAX)+read → MAX (saturated)
        INT32_MAX - 50,   # accum(-50)+read → MAX-50
        0,                # reset+read → 0
    ], dtype=np.int32)
    write_hex_int32(case_dir / "golden_output.hex", golden)
    write_manifest(case_dir / "manifest.json", "mx05", (len(golden),))
    print(f"  MX-05 golden: {case_dir}/golden_output.hex ({len(golden)} tests)")


GENERATORS = {"mx01": gen_mx01, "mx02": gen_mx02, "mx03": gen_mx03,
              "mx04": gen_mx04, "mx05": gen_mx05}


def main():
    parser = argparse.ArgumentParser(description="MXU P0 test vector generator")
    parser.add_argument("scenario", choices=["mx01","mx02","mx03","mx04","mx05","all"])
    args = parser.parse_args()
    if args.scenario == "all":
        for name, gen in GENERATORS.items():
            print(f"\n=== Generating {name.upper()} ===")
            gen()
        print("\nAll MXU P0 golden vectors generated.")
    else:
        GENERATORS[args.scenario]()


if __name__ == "__main__":
    main()
