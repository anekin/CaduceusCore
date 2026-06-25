#!/usr/bin/env python3
"""
MXU Test Vector Generator — calls GoldenMXU.matmul_from_sram() directly.

Generates $readmemh-format test vectors for RTL MXU verification.
10 scenarios: single_tile, multi_tile_K/N/M, overflow, zero_dim,
               partial_tile_K/N/M, random_regression×100.

Usage:
    python3 CaduceusCore/scripts/gen_mxu_vectors.py --scenario single_tile --out-dir CaduceusCore/rtl/test_vectors/mxu
    python3 CaduceusCore/scripts/gen_mxu_vectors.py --scenario all --out-dir CaduceusCore/rtl/test_vectors/mxu
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

# Ensure CaduceusCore/ is on path so 'sim' is importable as a package
_SCRIPT_DIR = Path(__file__).resolve().parent
_CADUCEUS_CORE = _SCRIPT_DIR.parent
sys.path.insert(0, str(_CADUCEUS_CORE))

from sim.golden_executor import GoldenMXU  # noqa: E402

# ══════════════════════════════════════════════════════════════════════
# Hex file writers ($readmemh compatible — one 32-bit word per line)
# ══════════════════════════════════════════════════════════════════════


def _pack_bytes_to_u32_le(data: np.ndarray) -> np.ndarray:
    """Pack uint8 array into uint32 LE words (4 bytes per word). Pads with zero."""
    arr = np.asarray(data, dtype=np.uint8).flatten()
    if len(arr) % 4 != 0:
        arr = np.pad(arr, (0, 4 - len(arr) % 4), constant_values=0)
    groups = arr.reshape(-1, 4)
    words = (
        groups[:, 0].astype(np.uint32)
        | (groups[:, 1].astype(np.uint32) << 8)
        | (groups[:, 2].astype(np.uint32) << 16)
        | (groups[:, 3].astype(np.uint32) << 24)
    )
    return words


def write_weights_hex(path: Path, packed_int4: np.ndarray):
    """Write INT4 weights (already packed 2/byte) as 32-bit hex words.

    Packed bytes are grouped 4 per 32-bit word with little-endian byte order.
    Low nibble = even weight index, high nibble = odd weight index (per GoldenMXU convention).
    """
    words = _pack_bytes_to_u32_le(packed_int4)
    with open(path, "w") as f:
        for w in words:
            f.write(f"{w:08x}\n")


def write_activations_hex(path: Path, activations: np.ndarray):
    """Write INT8 activations as 32-bit hex words (4 activations per word, LE byte order)."""
    act_u8 = np.asarray(activations, dtype=np.int8).flatten().view(np.uint8)
    words = _pack_bytes_to_u32_le(act_u8)
    with open(path, "w") as f:
        for w in words:
            f.write(f"{w:08x}\n")


def write_golden_hex(path: Path, result: np.ndarray):
    """Write INT32 golden output as 32-bit hex words (one per line)."""
    with open(path, "w") as f:
        for v in np.asarray(result, dtype=np.int32).flatten():
            f.write(f"{int(v) & 0xFFFFFFFF:08x}\n")


def write_params(path: Path, M: int, K: int, N: int):
    """Write params.txt: three lines M=<value>, K=<value>, N=<value> (decimal)."""
    with open(path, "w") as f:
        f.write(f"M={M}\nK={K}\nN={N}\n")


def write_manifest(path: Path, scenario_name: str, M: int, N: int):
    """Write manifest.json for compare_rtl.py."""
    manifest = {
        "name": scenario_name,
        "files": {"golden": "golden_output.hex"},
        "results": {"golden_shape": [int(M), int(N)]},
    }
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


# ══════════════════════════════════════════════════════════════════════
# Deterministic value generators
# ══════════════════════════════════════════════════════════════════════


def generate_weights_int4(K: int, N: int) -> np.ndarray:
    """Generate INT4 weights with deterministic cyclic pattern covering [-8, 7]."""
    size = K * N
    vals = np.fromiter((((i * 3 + 5) % 16) - 8 for i in range(size)), dtype=np.int8)
    return vals.reshape(K, N)


def generate_activations_int8(M: int, K: int) -> np.ndarray:
    """Generate INT8 activations with deterministic cyclic pattern covering [-128, 127]."""
    size = M * K
    vals = np.fromiter((((i * 7 + 11) % 256) - 128 for i in range(size)), dtype=np.int8)
    return vals.reshape(M, K)


def generate_overflow_weights(K: int, N: int) -> np.ndarray:
    """Generate INT4 weights at extreme values [-8, 7] to maximize product magnitude."""
    size = K * N
    vals = np.fromiter(
        (-8 if i % 2 == 0 else 7 for i in range(size)), dtype=np.int8
    )
    return vals.reshape(K, N)


def generate_overflow_activations(M: int, K: int) -> np.ndarray:
    """Generate INT8 activations at extreme values [-128, 127] to maximize product."""
    size = M * K
    vals = np.fromiter(
        (-128 if i % 2 == 0 else 127 for i in range(size)), dtype=np.int8
    )
    return vals.reshape(M, K)


# ══════════════════════════════════════════════════════════════════════
# Core generation: produce weights/activations, run GoldenMXU, write files
# ══════════════════════════════════════════════════════════════════════


def generate_scenario(
    out_dir: Path,
    scenario_name: str,
    M: int,
    K: int,
    N: int,
    weights: np.ndarray | None = None,
    activations: np.ndarray | None = None,
    *,
    overflow: bool = False,
):
    """Generate test vectors for one (M,K,N) scenario.

    Args:
        out_dir: Base output directory (e.g. CaduceusCore/rtl/test_vectors/mxu).
        scenario_name: Subdirectory name.
        M, K, N: Dimensions.
        weights: Pre-generated INT4 weights (K×N), or None for deterministic pattern.
        activations: Pre-generated INT8 activations (M×K), or None for deterministic pattern.
        overflow: Use overflow-specific value patterns when True and weights/activations not provided.
    """
    scenario_dir = out_dir / scenario_name
    scenario_dir.mkdir(parents=True, exist_ok=True)

    # Generate weights and activations if not provided
    if weights is None:
        if overflow:
            weights = generate_overflow_weights(K, N)
        else:
            weights = generate_weights_int4(K, N)
    if activations is None:
        if overflow:
            activations = generate_overflow_activations(M, K)
        else:
            activations = generate_activations_int8(M, K)

    mxu = GoldenMXU()

    # Pack weights
    weight_packed = mxu.pack_int4(weights.flatten())

    # Build SRAM byte array: activations at offset 0, weights at offset M*K
    act_flat = activations.flatten().astype(np.int8)
    act_u8 = act_flat.view(np.uint8)
    act_bytes = M * K
    wgt_bytes = len(weight_packed)

    sram = np.zeros(act_bytes + wgt_bytes, dtype=np.uint8)
    sram[0:act_bytes] = act_u8
    sram[act_bytes : act_bytes + wgt_bytes] = weight_packed

    # GoldenMXU computation
    result = mxu.matmul_from_sram(M, K, N, act_sram_addr=0, wgt_sram_addr=act_bytes, sram=sram)

    # Write output files
    write_weights_hex(scenario_dir / "weights.hex", weight_packed)
    write_activations_hex(scenario_dir / "activations.hex", act_flat)
    write_golden_hex(scenario_dir / "golden_output.hex", result)
    write_params(scenario_dir / "params.txt", M, K, N)
    write_manifest(scenario_dir / "manifest.json", scenario_name, M, N)


def generate_random_regression(out_dir: Path, seed: int = 42, num_cases: int = 100):
    """Generate 100 random-regression subdirectories under random_regression/."""
    rng = np.random.default_rng(seed)
    mxu = GoldenMXU()

    base_dir = out_dir / "random_regression"
    base_dir.mkdir(parents=True, exist_ok=True)

    for idx in range(num_cases):
        subdir_name = f"random_{idx:03d}"
        scenario_dir = base_dir / subdir_name
        scenario_dir.mkdir(parents=True, exist_ok=True)

        # Random dimensions: 1 ≤ M,K,N ≤ 256
        M = int(rng.integers(1, 257))
        K = int(rng.integers(1, 257))
        N = int(rng.integers(1, 257))

        # Random weights and activations in full INT4/INT8 ranges
        weights_flat = rng.integers(-8, 8, size=K * N, dtype=np.int8)
        act_flat = rng.integers(-128, 128, size=M * K, dtype=np.int8)

        weight_packed = mxu.pack_int4(weights_flat)
        act_u8 = act_flat.view(np.uint8)
        act_bytes = M * K
        wgt_bytes = len(weight_packed)

        sram = np.zeros(act_bytes + wgt_bytes, dtype=np.uint8)
        sram[0:act_bytes] = act_u8
        sram[act_bytes : act_bytes + wgt_bytes] = weight_packed

        result = mxu.matmul_from_sram(M, K, N, act_sram_addr=0, wgt_sram_addr=act_bytes, sram=sram)

        write_weights_hex(scenario_dir / "weights.hex", weight_packed)
        write_activations_hex(scenario_dir / "activations.hex", act_flat)
        write_golden_hex(scenario_dir / "golden_output.hex", result)
        write_params(scenario_dir / "params.txt", M, K, N)
        write_manifest(scenario_dir / "manifest.json", subdir_name, M, N)


# ══════════════════════════════════════════════════════════════════════
# Scenario registry
# ══════════════════════════════════════════════════════════════════════

SCENARIOS: dict[str, dict] = {
    "single_tile":       {"M": 64,  "K": 64,  "N": 64},
    "multi_tile_K":     {"M": 64,  "K": 128, "N": 64},
    "multi_tile_N":     {"M": 64,  "K": 64,  "N": 128},
    "multi_tile_M":     {"M": 128, "K": 64,  "N": 64},
    "overflow":         {"M": 64,  "K": 64,  "N": 64,  "overflow": True},
    "zero_dim":         {"M": 0,   "K": 64,  "N": 64},
    "partial_tile_K":   {"M": 64,  "K": 33,  "N": 64},
    "partial_tile_N":   {"M": 64,  "K": 64,  "N": 33},
    "partial_tile_M":   {"M": 33,  "K": 64,  "N": 64},
}


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate MXU test vectors via GoldenMXU.matmul_from_sram()"
    )
    parser.add_argument(
        "--scenario",
        type=str,
        default="all",
        help="Scenario name or 'all' (default: all). Choices: %s, all, random_regression"
        % ", ".join(sorted(SCENARIOS)),
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default=None,
        help="Output base directory (default: CaduceusCore/rtl/test_vectors/mxu)",
    )
    args = parser.parse_args()

    # Determine output directory
    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = _SCRIPT_DIR.parent / "rtl" / "test_vectors" / "mxu"
    out_dir.mkdir(parents=True, exist_ok=True)

    scenario = args.scenario

    if scenario == "all":
        for name, params in SCENARIOS.items():
            M, K, N = params["M"], params["K"], params["N"]
            is_overflow = params.get("overflow", False)
            print(f"Generating {name}: M={M}, K={K}, N={N} ...")
            generate_scenario(out_dir, name, M, K, N, overflow=is_overflow)
        print("Generating random_regression × 100 ...")
        generate_random_regression(out_dir, seed=42, num_cases=100)
        print("Done — all scenarios generated.")

    elif scenario == "random_regression":
        print("Generating random_regression × 100 ...")
        generate_random_regression(out_dir, seed=42, num_cases=100)
        print("Done.")

    elif scenario in SCENARIOS:
        params = SCENARIOS[scenario]
        M, K, N = params["M"], params["K"], params["N"]
        is_overflow = params.get("overflow", False)
        print(f"Generating {scenario}: M={M}, K={K}, N={N} ...")
        generate_scenario(out_dir, scenario, M, K, N, overflow=is_overflow)
        print("Done.")

    else:
        print(f"ERROR: Unknown scenario '{scenario}'. Choices: {sorted(SCENARIOS)}, all, random_regression")
        sys.exit(1)


if __name__ == "__main__":
    main()
