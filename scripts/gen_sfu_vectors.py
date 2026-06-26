#!/usr/bin/env python3
"""
SFU Test Vector Generator — standalone GoldenSFU replication.

Generates $readmemh-format test vectors for RTL SFU verification.
Does NOT import golden_executor.py (it has transitive dependencies that break
standalone import). The GoldenSFU semantics are replicated inline from
golden_executor.py:285-635.

Coverage:
    softmax (64, 256, 4096 elements)
    layernorm (4096)
    gelu (1000 random in [-4, 4])
    silu (1000 in [-10, 10])
    rope (128-dim, positions 0/42/100)
    rmsnorm (4096)
    random_regression x50 per SFU op (random elements 1..4096)

Usage:
    python3 CaduceusCore/scripts/gen_sfu_vectors.py --scenario softmax_64
    python3 CaduceusCore/scripts/gen_sfu_vectors.py --scenario all
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Tuple

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_CADUCEUS_CORE = _SCRIPT_DIR.parent

# ══════════════════════════════════════════════════════════════════════
# Hex file writers
# ══════════════════════════════════════════════════════════════════════


def write_fp16_hex(path: Path, arr: np.ndarray):
    """Write float16 array as one 4-digit hex value per line."""
    f16 = np.asarray(arr, dtype=np.float16).flatten()
    with open(path, "w") as f:
        for v in f16.view(np.uint16):
            f.write(f"{int(v):04x}\n")


def write_params(path: Path, op: str, dim: int, *, pos: int = 0, head_dim: int = 0,
                 theta_base: float = 10000.0):
    """Write params.txt with the fixed order tb_sfu.v expects."""
    if head_dim == 0:
        head_dim = dim
    with open(path, "w") as f:
        f.write(f"OP={op}\n")
        f.write(f"DIM={dim}\n")
        f.write(f"POS={pos}\n")
        f.write(f"HEAD_DIM={head_dim}\n")
        f.write(f"THETA_BASE={theta_base}\n")


def write_manifest(path: Path, scenario_name: str, golden_shape: Tuple[int, ...]):
    """Write manifest.json for compare_rtl.py SFU path."""
    manifest: Dict[str, Any] = {
        "name": scenario_name,
        "sfu_op": True,
        "files": {"golden": "golden_output.hex"},
        "results": {"golden_shape": list(golden_shape)},
    }
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


# ══════════════════════════════════════════════════════════════════════
# Standalone GoldenSFU (replicated from golden_executor.py)
# ══════════════════════════════════════════════════════════════════════


class GoldenSFU:
    """Hardware-equivalent SFU reference (no external dependencies)."""

    def __init__(self):
        self._build_exp_lut()
        self._build_gelu_lut()
        self._build_cordic_table()

    # ── Softmax LUT ─────────────────────────────────────────────────

    def _build_exp_lut(self, entries: int = 256, x_min: float = -20.0):
        self.exp_lut_x_min = x_min
        self.exp_lut_x_max = 0.0
        self.exp_lut_entries = entries
        self.exp_lut_step = (self.exp_lut_x_max - x_min) / (entries - 1)
        xs = np.linspace(x_min, 0.0, entries, dtype=np.float64)
        self.exp_lut = np.exp(xs).astype(np.float32)

    def _exp_hw(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        result = np.zeros_like(x, dtype=np.float32)
        valid = (x >= self.exp_lut_x_min) & (x <= self.exp_lut_x_max)
        if np.any(valid):
            xv = x[valid]
            idx_f = (xv - self.exp_lut_x_min) / self.exp_lut_step
            idx_lo = np.floor(idx_f).astype(np.int32)
            idx_hi = np.minimum(idx_lo + 1, self.exp_lut_entries - 1)
            frac = idx_f - idx_lo
            result[valid] = (
                self.exp_lut[idx_lo] * (1.0 - frac) +
                self.exp_lut[idx_hi] * frac
            ).astype(np.float32)
        result[x > 0] = 1.0
        result[x < self.exp_lut_x_min] = 0.0
        return result

    def softmax_hw(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        x_max = np.max(x)
        x_sub = x - x_max
        exp_vals = self._exp_hw(x_sub)
        s = np.sum(exp_vals)
        if s > 0:
            return exp_vals / s
        return exp_vals

    # ── GELU LUT ────────────────────────────────────────────────────

    def _build_gelu_lut(self, entries: int = 64, x_min: float = -4.0, x_max: float = 4.0):
        self.gelu_lut_entries = entries
        self.gelu_lut_x_min = x_min
        self.gelu_lut_x_max = x_max
        self.gelu_lut_step = (x_max - x_min) / (entries - 1)
        xs = np.linspace(x_min, x_max, entries, dtype=np.float64)
        self.gelu_lut = (0.5 * xs * (
            1.0 + np.tanh(np.sqrt(2.0 / np.pi) * (xs + 0.044715 * xs ** 3))
        )).astype(np.float32)

    def gelu_hw(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        result = np.zeros_like(x, dtype=np.float32)
        below = x < self.gelu_lut_x_min
        above = x > self.gelu_lut_x_max
        in_range = ~(below | above)
        result[below] = 0.0
        result[above] = x[above]
        if np.any(in_range):
            xv = x[in_range]
            idx_f = (xv - self.gelu_lut_x_min) / self.gelu_lut_step
            idx_lo = np.floor(idx_f).astype(np.int32)
            idx_hi = np.minimum(idx_lo + 1, self.gelu_lut_entries - 1)
            frac = idx_f - idx_lo
            result[in_range] = (
                self.gelu_lut[idx_lo] * (1.0 - frac) +
                self.gelu_lut[idx_hi] * frac
            ).astype(np.float32)
        return result

    # ── SiLU ────────────────────────────────────────────────────────

    def silu_hw(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        neg_exp = self._exp_hw(-np.abs(x))
        sigmoid = np.where(
            x >= 0,
            1.0 / (1.0 + neg_exp),
            neg_exp / (1.0 + neg_exp)
        )
        return x * sigmoid

    # ── LayerNorm ───────────────────────────────────────────────────

    @staticmethod
    def layernorm_hw(x: np.ndarray, eps: float = 1e-5) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        mean = np.mean(x, axis=-1, keepdims=True)
        var = np.var(x, axis=-1, keepdims=True)
        mean = mean.astype(np.float16).astype(np.float32)
        var = var.astype(np.float16).astype(np.float32)
        result = (x - mean) / np.sqrt(var + eps)
        return result.astype(np.float16).astype(np.float32)

    # ── RMSNorm ─────────────────────────────────────────────────────

    @staticmethod
    def rmsnorm_hw(x: np.ndarray, eps: float = 1e-5) -> np.ndarray:
        x = np.asarray(x, dtype=np.float32)
        if x.ndim == 1:
            mean_xsq = np.mean(x ** 2)
        else:
            mean_xsq = np.mean(x ** 2, axis=-1, keepdims=True)
        return (x / np.sqrt(mean_xsq + eps)).astype(np.float32)

    # ── RoPE (CORDIC-equivalent) ────────────────────────────────────

    def _build_cordic_table(self, iterations: int = 12):
        self.cordic_iterations = iterations
        self.cordic_angles = np.arctan(2.0 ** -np.arange(iterations)).astype(np.float32)
        self.cordic_gain = np.prod(np.cos(self.cordic_angles))

    def _cordic_rotate(self, x0: float, y0: float, theta: float) -> Tuple[float, float]:
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

        x = x0 * self.cordic_gain
        y = y0 * self.cordic_gain
        z = theta

        for i in range(self.cordic_iterations):
            d = 1 if z >= 0 else -1
            x_new = x - d * y * (2.0 ** -i)
            y_new = y + d * x * (2.0 ** -i)
            z = z - d * float(self.cordic_angles[i])
            x, y = x_new, y_new

        if flip:
            x, y = -x, -y
        return x, y

    def rope_hw(self, x_q: np.ndarray, x_k: np.ndarray, position: int,
                num_heads: int = 32, head_dim: int = 128,
                theta: float = 10000.0) -> Tuple[np.ndarray, np.ndarray]:
        x_q = np.asarray(x_q, dtype=np.float32)
        x_k = np.asarray(x_k, dtype=np.float32)
        freqs = 1.0 / (theta ** (np.arange(0, head_dim, 2, dtype=np.float64) / head_dim))
        angles = position * freqs

        def rotate_cordic(x, n_heads):
            x = x.reshape(n_heads, head_dim).copy()
            for h in range(n_heads):
                for i in range(0, head_dim, 2):
                    x_rot, y_rot = self._cordic_rotate(
                        float(x[h, i]), float(x[h, i + 1]), float(angles[i // 2])
                    )
                    x[h, i] = x_rot
                    x[h, i + 1] = y_rot
            return x.reshape(-1)

        return rotate_cordic(x_q, num_heads), rotate_cordic(x_k, 2)

    def rope_single_head(self, x: np.ndarray, position: int,
                         head_dim: int = 128, theta: float = 10000.0) -> np.ndarray:
        """Rotate a single head of interleaved (x,y) FP16 pairs."""
        x = np.asarray(x, dtype=np.float32)
        freqs = 1.0 / (theta ** (np.arange(0, head_dim, 2, dtype=np.float64) / head_dim))
        angles = position * freqs
        out = x.copy()
        for i in range(0, head_dim, 2):
            x_rot, y_rot = self._cordic_rotate(
                float(x[i]), float(x[i + 1]), float(angles[i // 2])
            )
            out[i] = x_rot
            out[i + 1] = y_rot
        return out


# ══════════════════════════════════════════════════════════════════════
# Scenario generation
# ══════════════════════════════════════════════════════════════════════

SFU = GoldenSFU()


def _make_out_dir(out_dir: Path, name: str) -> Path:
    scenario_dir = out_dir / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    return scenario_dir


def _generate_inputs(rng: np.random.Generator, N: int, dtype: str, **kwargs):
    if dtype == "fp16_uniform":
        low, high = kwargs.get("low", -1.0), kwargs.get("high", 1.0)
        return rng.uniform(low, high, size=N).astype(np.float32).astype(np.float16).astype(np.float32)
    if dtype == "fp16_range":
        low, high = kwargs["low"], kwargs["high"]
        return rng.uniform(low, high, size=N).astype(np.float32).astype(np.float16).astype(np.float32)
    raise ValueError(f"Unknown dtype {dtype}")


def generate_softmax(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    x = _generate_inputs(rng, N, "fp16_uniform", low=-2.0, high=2.0)
    golden = SFU.softmax_hw(x).astype(np.float16)
    scenario_dir = _make_out_dir(out_dir, name)
    write_fp16_hex(scenario_dir / "input.hex", x)
    write_fp16_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "SOFTMAX", N)
    write_manifest(scenario_dir / "manifest.json", name, (N,))


def generate_layernorm(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    x = _generate_inputs(rng, N, "fp16_uniform", low=-2.0, high=2.0)
    golden = SFU.layernorm_hw(x).astype(np.float16)
    scenario_dir = _make_out_dir(out_dir, name)
    write_fp16_hex(scenario_dir / "input.hex", x)
    write_fp16_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "LAYERNORM", N)
    write_manifest(scenario_dir / "manifest.json", name, (N,))


def generate_gelu(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    x = _generate_inputs(rng, N, "fp16_range", low=-4.0, high=4.0)
    golden = SFU.gelu_hw(x).astype(np.float16)
    scenario_dir = _make_out_dir(out_dir, name)
    write_fp16_hex(scenario_dir / "input.hex", x)
    write_fp16_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "GELU", N)
    write_manifest(scenario_dir / "manifest.json", name, (N,))


def generate_silu(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    x = _generate_inputs(rng, N, "fp16_range", low=-10.0, high=10.0)
    golden = SFU.silu_hw(x).astype(np.float16)
    scenario_dir = _make_out_dir(out_dir, name)
    write_fp16_hex(scenario_dir / "input.hex", x)
    write_fp16_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "SILU", N)
    write_manifest(scenario_dir / "manifest.json", name, (N,))


def generate_rmsnorm(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    x = _generate_inputs(rng, N, "fp16_uniform", low=-2.0, high=2.0)
    golden = SFU.rmsnorm_hw(x).astype(np.float16)
    scenario_dir = _make_out_dir(out_dir, name)
    write_fp16_hex(scenario_dir / "input.hex", x)
    write_fp16_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "RMSNORM", N)
    write_manifest(scenario_dir / "manifest.json", name, (N,))


def generate_rope(out_dir: Path, name: str, head_dim: int, position: int, seed: int,
                  theta_base: float = 10000.0):
    rng = np.random.default_rng(seed)
    num_pairs = head_dim // 2
    x = _generate_inputs(rng, head_dim, "fp16_uniform", low=-1.0, high=1.0)
    # Use a single head so the input length equals head_dim.
    golden = SFU.rope_single_head(x, position, head_dim=head_dim, theta=theta_base).astype(np.float16)
    scenario_dir = _make_out_dir(out_dir, name)
    # input.hex is interleaved (x,y) FP16 pairs — one value per line.
    write_fp16_hex(scenario_dir / "input.hex", x)
    write_fp16_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "ROPE", num_pairs, pos=position,
                 head_dim=head_dim, theta_base=theta_base)
    write_manifest(scenario_dir / "manifest.json", name, (head_dim,))


# Map op name → generator callable and a base seed for random_regression.
SFU_OP_GENERATORS = {
    "SOFTMAX":   (generate_softmax, 1000),
    "LAYERNORM": (generate_layernorm, 2000),
    "GELU":      (generate_gelu, 3000),
    "SILU":      (generate_silu, 4000),
    "RMSNORM":   (generate_rmsnorm, 5000),
    "ROPE":      (None, 6000),  # special handling below
}


def generate_random_regression(out_dir: Path, seed: int = 7000, cases_per_op: int = 50):
    rng = np.random.default_rng(seed)
    base_dir = out_dir / "random_regression"
    base_dir.mkdir(parents=True, exist_ok=True)

    for op, (gen_fn, base_seed) in SFU_OP_GENERATORS.items():
        for idx in range(cases_per_op):
            N = int(rng.integers(1, 4097))
            case_seed = int(base_seed + idx * 17 + rng.integers(0, 1000000))
            name = f"random_{op.lower()}_{idx:03d}"
            scenario_dir = base_dir / name
            scenario_dir.mkdir(parents=True, exist_ok=True)

            if op == "ROPE":
                # RoPE random cases always use head_dim=128 and random position.
                pos = int(rng.integers(0, 2048))
                generate_rope(base_dir, name, head_dim=128, position=pos, seed=case_seed)
            else:
                gen_fn(base_dir, name, N, case_seed)


# ══════════════════════════════════════════════════════════════════════
# Scenario registry
# ══════════════════════════════════════════════════════════════════════

NAMED_SCENARIOS = [
    ("softmax_64",      generate_softmax,   64,     101),
    ("softmax_256",     generate_softmax,   256,    102),
    ("softmax_4096",    generate_softmax,   4096,   103),
    ("layernorm_4096",  generate_layernorm, 4096,   104),
    ("gelu_1000",       generate_gelu,      1000,   105),
    ("silu_1000",       generate_silu,      1000,   106),
    ("rope_pos0",       generate_rope,      128,    0,   107),
    ("rope_pos42",      generate_rope,      128,    42,  108),
    ("rope_pos100",     generate_rope,      128,    100, 109),
    ("rmsnorm_4096",    generate_rmsnorm,   4096,   110),
]


def _generate_named(out_dir: Path):
    for entry in NAMED_SCENARIOS:
        name = entry[0]
        gen_fn = entry[1]
        if gen_fn is generate_rope:
            head_dim, position, seed = entry[2], entry[3], entry[4]
            print(f"Generating {name}: head_dim={head_dim}, pos={position} ...")
            gen_fn(out_dir, name, head_dim, position, seed)
        else:
            N, seed = entry[2], entry[3]
            print(f"Generating {name}: N={N} ...")
            gen_fn(out_dir, name, N, seed)


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate SFU test vectors via standalone GoldenSFU replication"
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default="all",
        help="Scenario name or 'all' (default: all). Choices: "
             + ", ".join([n for n, *_ in NAMED_SCENARIOS])
             + ", random_regression, all",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Output base directory (default: CaduceusCore/rtl/test_vectors/sfu)",
    )
    args = parser.parse_args()

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = _CADUCEUS_CORE / "rtl" / "test_vectors" / "sfu"
    out_dir.mkdir(parents=True, exist_ok=True)

    scenario = args.scenario
    named_names = {entry[0] for entry in NAMED_SCENARIOS}

    if scenario == "all":
        _generate_named(out_dir)
        print("Generating random_regression x50 per SFU op ...")
        generate_random_regression(out_dir, seed=7000, cases_per_op=50)
        print("Done — all SFU scenarios generated.")

    elif scenario == "random_regression":
        print("Generating random_regression x50 per SFU op ...")
        generate_random_regression(out_dir, seed=7000, cases_per_op=50)
        print("Done.")

    elif scenario in named_names:
        entry = next(e for e in NAMED_SCENARIOS if e[0] == scenario)
        if entry[1] is generate_rope:
            entry[1](out_dir, entry[0], entry[2], entry[3], entry[4])
        else:
            entry[1](out_dir, entry[0], entry[2], entry[3])
        print("Done.")

    else:
        print(f"ERROR: Unknown scenario '{scenario}'.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
