#!/usr/bin/env python3
"""RoPE / CORDIC smoke test vector generator.

Matches GoldenExecutor._cordic_rotate with 12 CORDIC iterations.

Usage:
    python3 CaduceusCore/scripts/gen_rope_vectors.py \
        --out-dir CaduceusCore/rtl/test_vectors/sfu/rope_smoke
"""

import argparse
import math
from pathlib import Path

import numpy as np


def float_to_fp16(x):
    return int(np.float16(x).view("uint16"))


def cordic_rotate(x0, y0, theta, iterations=12):
    angles = [math.atan(2.0**-i) for i in range(iterations)]
    K = 1.0
    for a in angles:
        K *= math.cos(a)

    theta = theta % (2.0 * math.pi)
    if theta > math.pi:
        theta -= 2.0 * math.pi

    flip = False
    if theta > math.pi / 2:
        theta -= math.pi
        flip = True
    elif theta < -math.pi / 2:
        theta += math.pi
        flip = True

    x = x0 * K
    y = y0 * K
    z = theta

    for i in range(iterations):
        d = 1 if z >= 0 else -1
        x_new = x - d * y * (2.0**-i)
        y_new = y + d * x * (2.0**-i)
        z = z - d * angles[i]
        x, y = x_new, y_new

    if flip:
        x, y = -x, -y
    return x, y


TESTS = [
    (1.0, 0.0, 0.0, "angle_0"),
    (1.0, 0.0, math.pi / 4, "angle_pi4"),
    (1.0, 0.0, math.pi / 2, "angle_pi2"),
    (1.0, 0.0, 3 * math.pi / 4, "angle_3pi4"),
    (1.0, 0.0, math.pi, "angle_pi"),
    (1.0, 0.0, -math.pi / 4, "angle_minus_pi4"),
    (1.0, 1.0, math.pi / 4, "diag_pi4"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, default=Path("CaduceusCore/rtl/test_vectors/sfu/rope_smoke"))
    args = parser.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    input_file = out_dir / "input.hex"
    golden_file = out_dir / "golden.hex"

    with open(input_file, "w") as fi, open(golden_file, "w") as fg:
        for x, y, theta, desc in TESTS:
            xr, yr = cordic_rotate(x, y, theta)
            x_i = float_to_fp16(x)
            y_i = float_to_fp16(y)
            t_i = float_to_fp16(theta)
            x_o = float_to_fp16(xr)
            y_o = float_to_fp16(yr)
            fi.write(f"{x_i:04x}{y_i:04x}{t_i:04x}\n")
            fg.write(f"{x_o:04x}{y_o:04x}\n")
            print(f"{desc}: in=({x_i:04x},{y_i:04x},{t_i:04x}) "
                  f"out=({x_o:04x},{y_o:04x}) real=({xr:.6f},{yr:.6f})")

    print(f"\nWrote {input_file} and {golden_file}")


if __name__ == "__main__":
    main()
