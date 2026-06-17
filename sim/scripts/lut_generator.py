#!/usr/bin/env python3
"""SCALE-Sim Lookup Table Generator

Pre-computes MXU cycle counts for common (M, K, N) combinations
using the analytical model. Can optionally run SCALE-Sim v3 for
higher accuracy (requires SCALE-Sim installed).

Usage:
    python3 lut_generator.py                # Generate LUT
    python3 lut_generator.py --accurate     # Use SCALE-Sim (slow but precise)
    python3 lut_generator.py --load lut.json # Load existing LUT
"""

import json
import math
import sys
from pathlib import Path
from typing import Dict, Tuple

# Add parent to sim path
sys.path.insert(0, str(Path(__file__).parent.parent))
from models.mxu import MXUModel
import yaml


def generate_qwen3b_combos() -> list:
    """Generate all (M,K,N) combos for Qwen2.5-3B."""
    combos = []
    # Decode: M=1
    for (M, K, N, op) in [
        (1, 2560, 4096, "q_proj"),
        (1, 2560, 256, "k_proj"),
        (1, 2560, 256, "v_proj"),
        (1, 4096, 2560, "o_proj"),
        (1, 2560, 9728, "gate_proj"),
        (1, 2560, 9728, "up_proj"),
        (1, 9728, 2560, "down_proj"),
    ]:
        combo = f"{M}x{K}x{N}"
        combos.append({
            "combo": combo, "M": M, "K": K, "N": N,
            "op": op, "mode": "decode",
        })

    # Prefill: M=128, 256, 512, 1024, 2048
    for prompt_len in [128, 256, 512, 1024, 2048]:
        for (K, N, op) in [
            (2560, 4096, "q_proj"),
            (2560, 256, "k_proj"),
            (2560, 256, "v_proj"),
            (4096, 2560, "o_proj"),
            (2560, 9728, "gate_proj"),
            (2560, 9728, "up_proj"),
            (9728, 2560, "down_proj"),
        ]:
            combo = f"{prompt_len}x{K}x{N}"
            combos.append({
                "combo": combo, "M": prompt_len, "K": K, "N": N,
                "op": op, "mode": f"prefill_{prompt_len}",
            })
    return combos


def build_analytical_lut(config_path: str) -> Dict:
    """Build LUT using analytical model (fast)."""
    with open(config_path) as f:
        config = yaml.safe_load(f)
    mxu = MXUModel(config)
    combos = generate_qwen3b_combos()

    lut = {
        "generated_by": "analytical_model",
        "config": {"array": "128x128", "freq_mhz": 1000, "weight_bits": 4},
        "entries": {},
    }

    for c in combos:
        key = c["combo"]
        result = mxu.estimate(c["M"], c["K"], c["N"],
                              weight_preloaded=(c["mode"] == "decode"))
        lut["entries"][key] = {
            "compute": result.compute_cycles,
            "stall_dram": result.stall_cycles_dram,
            "stall_sram": result.stall_cycles_sram,
            "total": result.total_cycles,
            "utilization": round(result.utilization, 3),
            "ops": result.ops,
            "mode": c["mode"],
        }

    return lut


def main():
    config_path = Path(__file__).parent.parent / "config" / "npu_config.yaml"
    lut = build_analytical_lut(str(config_path))

    output_path = Path(__file__).parent.parent / "results" / "mxu_lut.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(lut, f, indent=2)

    # Summary
    total = len(lut["entries"])
    avg_util = sum(e["utilization"] for e in lut["entries"].values()) / total
    print(f"Generated LUT: {total} entries, avg utilization: {avg_util:.1%}")
    print(f"Saved to: {output_path}")


if __name__ == "__main__":
    main()
