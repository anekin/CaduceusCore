#!/usr/bin/env python3
"""
Vector Test Vector Generator — standalone GoldenVector replication.

Generates $readmemh-format test vectors for RTL Vector verification.
Does NOT import golden_executor.py (it has transitive dependencies that break
standalone import). The GoldenVector semantics are replicated inline from
golden_executor.py:641-721, with saturation arithmetic for ADD/MUL/RESID/SUM
matching the RTL Vector unit behavior.

Coverage:
    add (128, 4096 elements)
    mul (4096)
    max_reduce (128, 4096)
    sum_reduce (128, 4096)
    conv (100, 4096)
    resid_add (128, 4096)
    random_regression x50 (random op + elements 1..4096)

Usage:
    python3 CaduceusCore/scripts/gen_vector_vectors.py --scenario add_128
    python3 CaduceusCore/scripts/gen_vector_vectors.py --scenario all
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_CADUCEUS_CORE = _SCRIPT_DIR.parent

INT32_MIN = -(2 ** 31)
INT32_MAX = 2 ** 31 - 1

# ══════════════════════════════════════════════════════════════════════
# Hex file writers
# ══════════════════════════════════════════════════════════════════════


def write_int32_hex(path: Path, arr: np.ndarray):
    """Write INT32 array as one 8-digit hex value per line."""
    i32 = np.asarray(arr, dtype=np.int32).flatten()
    with open(path, "w") as f:
        for v in i32.view(np.uint32):
            f.write(f"{int(v):08x}\n")


def write_fp16_hex(path: Path, arr: np.ndarray):
    """Write float16 array as one 4-digit hex value per line."""
    f16 = np.asarray(arr, dtype=np.float16).flatten()
    with open(path, "w") as f:
        for v in f16.view(np.uint16):
            f.write(f"{int(v):04x}\n")


def write_params(path: Path, op: str, dim: int):
    """Write params.txt: OP=<NAME>,DIM=<N>."""
    with open(path, "w") as f:
        f.write(f"OP={op},DIM={dim}\n")


def write_manifest(path: Path, scenario_name: str, golden_shape: Tuple[int, ...],
                   *, sfu_op: bool = False):
    """Write manifest.json for compare_rtl.py."""
    manifest: Dict[str, Any] = {
        "name": scenario_name,
        "files": {"golden": "golden_output.hex"},
        "results": {"golden_shape": list(golden_shape)},
    }
    if sfu_op:
        manifest["sfu_op"] = True
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


# ══════════════════════════════════════════════════════════════════════
# Standalone GoldenVector (replicated from golden_executor.py)
# ══════════════════════════════════════════════════════════════════════


def _saturate_i32(x: np.ndarray) -> np.ndarray:
    """Saturate to INT32 range."""
    return np.clip(x, INT32_MIN, INT32_MAX).astype(np.int32)


def vector_add(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return _saturate_i32(a.astype(np.int64) + b.astype(np.int64))


def vector_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return _saturate_i32(a.astype(np.int64) * b.astype(np.int64))


def vector_max_reduce(x: np.ndarray) -> np.ndarray:
    return np.array([np.max(x)], dtype=np.int32)


def vector_sum_reduce(x: np.ndarray) -> np.ndarray:
    return _saturate_i32(np.array([np.sum(x.astype(np.int64))]))


def vector_conv_i32_to_f16(arr: np.ndarray) -> np.ndarray:
    f32 = arr.astype(np.float32)
    f16_max = np.finfo(np.float16).max
    f32 = np.clip(f32, -f16_max, f16_max)
    return f32.astype(np.float16)


def vector_residual_add(original: np.ndarray, delta: np.ndarray) -> np.ndarray:
    return _saturate_i32(original.astype(np.int64) + delta.astype(np.int64))


# ══════════════════════════════════════════════════════════════════════
# Scenario generation
# ══════════════════════════════════════════════════════════════════════


def _make_out_dir(out_dir: Path, name: str) -> Path:
    scenario_dir = out_dir / name
    scenario_dir.mkdir(parents=True, exist_ok=True)
    return scenario_dir


def _generate_int32_inputs(rng: np.random.Generator, N: int, wide: bool = False) -> np.ndarray:
    if wide:
        return rng.integers(INT32_MIN, INT32_MAX + 1, size=N, dtype=np.int64).astype(np.int32)
    return rng.integers(-1024, 1025, size=N, dtype=np.int32)


def generate_add(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    a = _generate_int32_inputs(rng, N, wide=True)
    b = _generate_int32_inputs(rng, N, wide=True)
    golden = vector_add(a, b)
    scenario_dir = _make_out_dir(out_dir, name)
    write_int32_hex(scenario_dir / "a.hex", a)
    write_int32_hex(scenario_dir / "b.hex", b)
    write_int32_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "ADD", N)
    write_manifest(scenario_dir / "manifest.json", name, (N,))


def generate_mul(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    a = _generate_int32_inputs(rng, N, wide=True)
    b = _generate_int32_inputs(rng, N, wide=True)
    golden = vector_mul(a, b)
    scenario_dir = _make_out_dir(out_dir, name)
    write_int32_hex(scenario_dir / "a.hex", a)
    write_int32_hex(scenario_dir / "b.hex", b)
    write_int32_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "MUL", N)
    write_manifest(scenario_dir / "manifest.json", name, (N,))


def generate_max_reduce(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    x = _generate_int32_inputs(rng, N, wide=True)
    golden = vector_max_reduce(x)
    scenario_dir = _make_out_dir(out_dir, name)
    write_int32_hex(scenario_dir / "x.hex", x)
    write_int32_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "MAX", N)
    write_manifest(scenario_dir / "manifest.json", name, (1,))


def generate_sum_reduce(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    x = _generate_int32_inputs(rng, N, wide=True)
    golden = vector_sum_reduce(x)
    scenario_dir = _make_out_dir(out_dir, name)
    write_int32_hex(scenario_dir / "x.hex", x)
    write_int32_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "SUM", N)
    write_manifest(scenario_dir / "manifest.json", name, (1,))


def generate_conv(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    x = _generate_int32_inputs(rng, N, wide=True)
    golden = vector_conv_i32_to_f16(x)
    scenario_dir = _make_out_dir(out_dir, name)
    write_int32_hex(scenario_dir / "x.hex", x)
    write_fp16_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "CONV", N)
    write_manifest(scenario_dir / "manifest.json", name, (N,), sfu_op=True)


def generate_resid_add(out_dir: Path, name: str, N: int, seed: int):
    rng = np.random.default_rng(seed)
    original = _generate_int32_inputs(rng, N, wide=True)
    delta = _generate_int32_inputs(rng, N, wide=True)
    golden = vector_residual_add(original, delta)
    scenario_dir = _make_out_dir(out_dir, name)
    write_int32_hex(scenario_dir / "a.hex", original)
    write_int32_hex(scenario_dir / "b.hex", delta)
    write_int32_hex(scenario_dir / "golden_output.hex", golden)
    write_params(scenario_dir / "params.txt", "RESID", N)
    write_manifest(scenario_dir / "manifest.json", name, (N,))


# Map op symbol → (generator, base_seed)
VECTOR_OP_GENERATORS: Dict[str, Tuple[Callable, int]] = {
    "ADD":   (generate_add, 10000),
    "MUL":   (generate_mul, 11000),
    "MAX":   (generate_max_reduce, 12000),
    "SUM":   (generate_sum_reduce, 13000),
    "CONV":  (generate_conv, 14000),
    "RESID": (generate_resid_add, 15000),
}


def generate_random_regression(out_dir: Path, seed: int = 20000, num_cases: int = 50):
    rng = np.random.default_rng(seed)
    base_dir = out_dir / "random_regression"
    base_dir.mkdir(parents=True, exist_ok=True)

    op_symbols = list(VECTOR_OP_GENERATORS.keys())
    for idx in range(num_cases):
        op = rng.choice(op_symbols)
        gen_fn, base_seed = VECTOR_OP_GENERATORS[op]
        N = int(rng.integers(1, 4097))
        case_seed = int(base_seed + idx * 31 + rng.integers(0, 1000000))
        name = f"random_{op.lower()}_{idx:03d}"
        gen_fn(base_dir, name, N, case_seed)


# ══════════════════════════════════════════════════════════════════════
# Scenario registry
# ══════════════════════════════════════════════════════════════════════

NAMED_SCENARIOS = [
    ("add_128", generate_add, 128, 20001),
    ("add_4096", generate_add, 4096, 20002),
    ("mul_4096", generate_mul, 4096, 20003),
    ("max_reduce_128", generate_max_reduce, 128, 20004),
    ("max_reduce_4096", generate_max_reduce, 4096, 20005),
    ("sum_reduce_128", generate_sum_reduce, 128, 20006),
    ("sum_reduce_4096", generate_sum_reduce, 4096, 20007),
    ("conv_100", generate_conv, 100, 20008),
    ("conv_4096", generate_conv, 4096, 20009),
    ("resid_add_128", generate_resid_add, 128, 20010),
    ("resid_add_4096", generate_resid_add, 4096, 20011),
]


def _generate_named(out_dir: Path):
    for name, gen_fn, N, seed in NAMED_SCENARIOS:
        print(f"Generating {name}: N={N} ...")
        gen_fn(out_dir, name, N, seed)


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Vector test vectors via standalone GoldenVector replication"
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
        help="Output base directory (default: CaduceusCore/rtl/test_vectors/vector)",
    )
    args = parser.parse_args()

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = _CADUCEUS_CORE / "rtl" / "test_vectors" / "vector"
    out_dir.mkdir(parents=True, exist_ok=True)

    scenario = args.scenario
    named_names = {entry[0] for entry in NAMED_SCENARIOS}

    if scenario == "all":
        _generate_named(out_dir)
        print("Generating random_regression x50 ...")
        generate_random_regression(out_dir, seed=20000, num_cases=50)
        print("Done — all Vector scenarios generated.")

    elif scenario == "random_regression":
        print("Generating random_regression x50 ...")
        generate_random_regression(out_dir, seed=20000, num_cases=50)
        print("Done.")

    elif scenario in named_names:
        entry = next(e for e in NAMED_SCENARIOS if e[0] == scenario)
        entry[1](out_dir, entry[0], entry[2], entry[3])
        print("Done.")

    else:
        print(f"ERROR: Unknown scenario '{scenario}'.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
