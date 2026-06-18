#!/usr/bin/env python3
"""Qwen2.5-7B NPU trace generator for NPUSimulator.

Same architecture as 3B but larger dimensions:
  hidden=3584, intermediate=18944, num_kv_heads=4, num_layers=28
"""

import sys
from pathlib import Path
from typing import List, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent / "sim"))

# Qwen2.5-7B architecture
HIDDEN = 3584
INTERMEDIATE = 18944
NUM_HEADS = 28
NUM_KV_HEADS = 4
HEAD_DIM = 128
NUM_LAYERS = 28
QKV_DIM = NUM_HEADS * HEAD_DIM   # 3584
KV_DIM = NUM_KV_HEADS * HEAD_DIM # 512


def generate_qwen7b_trace(prompt_len: int = 1) -> List[Tuple[int, int, int, int, str]]:
    """7 GEMMs per layer: Q, K, V, O, FFN_gate, FFN_up, FFN_down."""
    M = prompt_len
    trace = []
    for layer in range(NUM_LAYERS):
        trace.append((M, HIDDEN, QKV_DIM, layer, "Q_proj"))
        trace.append((M, HIDDEN, KV_DIM, layer, "K_proj"))
        trace.append((M, HIDDEN, KV_DIM, layer, "V_proj"))
        trace.append((M, QKV_DIM, HIDDEN, layer, "O_proj"))
        trace.append((M, HIDDEN, INTERMEDIATE, layer, "FFN_gate"))
        trace.append((M, HIDDEN, INTERMEDIATE, layer, "FFN_up"))
        trace.append((M, INTERMEDIATE, HIDDEN, layer, "FFN_down"))
    return trace


def run_qwen7b_decode(config_path: str = None) -> dict:
    """Run NPU simulator decode for Qwen2.5-7B."""
    if config_path is None:
        config_path = str(Path(__file__).parent.parent / "sim" / "config" / "npu_config_wc.yaml")

    from npu_sim import NPUSimulator
    sim = NPUSimulator(str(config_path))
    sim.kv.configure_for_model(
        num_kv_heads=NUM_KV_HEADS, head_dim=HEAD_DIM,
        num_layers=NUM_LAYERS, max_context=2048,
    )

    trace = generate_qwen7b_trace(prompt_len=1)
    report = sim.simulate_decode(trace)

    return {
        "model": "Qwen2.5-7B",
        "config": {
            "array": f"{report.array_height}x{report.array_width}",
            "weight_bits": report.weight_bits,
            "freq_mhz": report.freq_mhz,
            "weight_cache": sim.config.get("optimizations", {}).get("weight_cache", False),
        },
        "decode_us": round(report.decode_per_token_us, 1),
        "tok_per_s": round(report.decode_tok_per_s, 1),
        "breakdown_us": report.decode_breakdown,
        "num_layers": report.num_layers,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_qwen7b_decode(), indent=2))
