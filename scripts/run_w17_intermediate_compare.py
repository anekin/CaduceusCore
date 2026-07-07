#!/usr/bin/env python3
import argparse
import os
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
EVIDENCE_PATH = _PROJECT / "build" / "evidence" / "w1-7-intermediate-compare.txt"


def main() -> int:
    parser = argparse.ArgumentParser(description="W1.7 per-op intermediate compare")
    parser.add_argument(
        "--vectors-dir",
        default=os.environ.get("QWEN25_3LAYER_VECTORS_DIR", ""),
        help="Override path to qwen25-3b-3layer-rtl vectors directory",
    )
    args = parser.parse_args()

    env = os.environ.copy()
    if args.vectors_dir:
        env["QWEN25_3LAYER_VECTORS_DIR"] = args.vectors_dir

    make_cmd = [
        "make", "-C", str(_PROJECT / "sim" / "regression"),
        "run_w17_intermediate_compare",
    ]
    print(f"[W1.7] Running: {' '.join(make_cmd)}")
    result = subprocess.run(make_cmd, env=env)

    if result.returncode == 0 and EVIDENCE_PATH.exists():
        print(f"[W1.7] Evidence: {EVIDENCE_PATH}")

    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
