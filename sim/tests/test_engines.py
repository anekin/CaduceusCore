"""Engine calibration tests — BlockEngine broadcast pipeline model."""

import pytest

from engine.mac_engine import create_engine


_BASE_CONFIG = {
    "mac_engine": {
        "array_height": 128,
        "array_width": 128,
        "frequency_mhz": 1000,
        "weight_precision_bits": 4,
        "activation_precision_bits": 8,
    },
    "memory": {
        "bandwidth_bytes_per_cycle": 51.2,
        "dram_efficiency": 0.85,
    },
}


def _engine_config(engine_type: str) -> dict:
    cfg = {
        "mac_engine": dict(_BASE_CONFIG["mac_engine"]),
        "memory": dict(_BASE_CONFIG["memory"]),
    }
    cfg["mac_engine"]["type"] = engine_type
    return cfg


def _tok_s(result, f_mhz: int = 1000) -> float:
    """Convert cycle result to tok/s for a single M=1 decode GEMM."""
    return f_mhz * 1e6 / result.total_cycles


def test_block_decode():
    """BlockEngine decode tok/s should be 1.2-3× SystolicEngine, not ~8×."""
    M, K, N = 1, 11008, 2048

    block = create_engine(_engine_config("block"))
    systolic = create_engine(_engine_config("systolic"))

    r_block = block.estimate(M, K, N)
    r_systolic = systolic.estimate(M, K, N)

    block_tok_s = _tok_s(r_block)
    systolic_tok_s = _tok_s(r_systolic)
    ratio = block_tok_s / systolic_tok_s

    # The old 1-cycle/tile model gave ~8x; realistic broadcast pipeline
    # should land in 1.2-3x.
    assert 1.2 <= ratio <= 3.0, (
        f"Block/Systolic tok/s ratio {ratio:.2f} outside [1.2, 3.0]; "
        f"block={block_tok_s:.1f}, systolic={systolic_tok_s:.1f}"
    )

    # Block engine must be DMA-bound for this representative decode config.
    assert r_block.bottleneck == "dma", (
        f"Expected DMA-bound block engine, got {r_block.bottleneck}"
    )

    # Sanity: broadcast pipeline overhead is now documented and non-trivial.
    assert r_block.details["per_tile_compute"] >= 3


def test_block_weight_cache():
    """Weight-cache pair should be faster than two separate estimates."""
    M, K, N = 1, 11008, 2048

    block = create_engine(_engine_config("block"))

    r_pair = block.estimate_weight_cache_pair(M, K, N)
    r_single = block.estimate(M, K, N)

    r_two = r_single.total_cycles * 2

    assert r_pair.total_cycles < r_two, (
        f"Weight-cache pair ({r_pair.total_cycles}) not faster than "
        f"two separate estimates ({r_two})"
    )

    # Sanity: pair reports positive weight-cache savings.
    assert "weight_cache_savings" in r_pair.details
    assert r_pair.details["weight_cache_savings"] > 0

    # Pair is still DMA-bound for the representative decode config.
    assert r_pair.bottleneck == "dma", (
        f"Expected DMA-bound weight-cache pair, got {r_pair.bottleneck}"
    )
