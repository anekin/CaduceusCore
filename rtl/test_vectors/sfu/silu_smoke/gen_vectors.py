#!/usr/bin/env python3
"""Generate SiLU smoke test vectors for RTL verification.

Uses GoldenSFU.silu_hw() as the golden reference. Input values are FP16;
1000 random values are drawn uniformly from [-10, 10] plus a few corner cases.
"""

import sys
import struct
import json
from pathlib import Path

import numpy as np

# CaduceusCore is the parent of the sim package
CC_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(CC_ROOT))

from sim.golden_executor import GoldenSFU


def write_hex_float16(path: Path, data: np.ndarray):
    """Write FP16 values as $readmemh-compatible 4-digit hex (little-endian)."""
    raw = np.asarray(data, dtype=np.float16).tobytes()
    with open(path, "w") as f:
        for i in range(0, len(raw), 2):
            val = struct.unpack_from("<H", raw, i)[0]
            f.write(f"{val:04x}\n")


def main():
    rng = np.random.RandomState(42)
    n_random = 1000

    # Corner cases first, then random values in [-10, 10]
    corners = np.array([0.0, 10.0, -10.0, 100.0, -100.0, 1000.0, -1000.0],
                       dtype=np.float32)
    random_vals = rng.uniform(-10.0, 10.0, size=n_random).astype(np.float16).astype(np.float32)
    x = np.concatenate([corners, random_vals])

    golden = GoldenSFU().silu_hw(x)

    out_dir = Path(__file__).resolve().parent
    write_hex_float16(out_dir / "input.hex", x)
    write_hex_float16(out_dir / "golden.hex", golden)

    manifest = {
        "name": "silu_smoke",
        "sfu_op": "silu",
        "files": {"golden": "golden.hex"},
        "results": {"golden_shape": [int(x.size)]},
    }
    with open(out_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Generated {x.size} SiLU smoke vectors in {out_dir}")


if __name__ == "__main__":
    main()
