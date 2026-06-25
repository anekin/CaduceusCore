#!/usr/bin/env python3
"""Generate 4096-element FP16 RMSNorm smoke vectors for rmsnorm_hw."""

import sys, struct, json
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from sim.golden_executor import GoldenSFU


def write_hex_float16(path: Path, data: np.ndarray):
    raw = np.asarray(data, dtype=np.float16).tobytes()
    with open(path, "w") as f:
        for i in range(0, len(raw), 2):
            f.write(f"{struct.unpack_from('<H', raw, i)[0]:04x}\n")


def main():
    rng = np.random.RandomState(909)
    x = rng.randn(4096).astype(np.float32) * 2.0
    golden = GoldenSFU.rmsnorm_hw(x)

    out_dir = Path(__file__).parent
    write_hex_float16(out_dir / "input.hex", x)
    write_hex_float16(out_dir / "golden.hex", golden)

    manifest = {
        "name": "rmsnorm_smoke",
        "description": "4096-element FP16 RMSNorm smoke test",
        "sfu_op": "rmsnorm",
        "sfu_len": 4096,
        "seed": 909,
        "files": {
            "input": "input.hex",
            "golden": "golden.hex",
        },
        "format": {
            "input": "float16 (FP16), 4 hex digits per value, little-endian",
            "golden": "float16 (FP16), 4 hex digits per value, little-endian",
        },
    }
    with open(out_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"Generated {out_dir}/input.hex  ({x.size} values)")
    print(f"Generated {out_dir}/golden.hex ({golden.size} values)")


if __name__ == "__main__":
    main()
