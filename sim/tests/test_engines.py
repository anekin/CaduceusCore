"""Engine calibration tests — BlockEngine broadcast pipeline model + SystolicEngine regression."""

import json
import subprocess
from pathlib import Path

import pytest

from engine.mac_engine import create_engine
from models.mxu import MXUModel
from model_specs import get_spec


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


_SYSTOLIC_CONFIG = {
    "mxu": {
        "type": "systolic",
        "array_height": 128,
        "array_width": 128,
        "frequency_mhz": 1000,
        "weight_precision_bits": 4,
        "activation_precision_bits": 8,
        "ops_per_mac": 2,
        "double_buffer": True,
    },
    "memory": {
        "bandwidth_bytes_per_cycle": 51.2,
        "dram_efficiency": 0.85,
    },
}


def _qwen3b_geometries(M: int):
    """Yield (name, M, K, N) for each of the 7 GEMM ops in Qwen2.5-3B.

    Shapes match the trace produced by npu_sim.generate_qwen3b_trace:
      - Q_proj: (M, hidden, qkv_dim)     — H=2560, QKV=4096
      - K_proj: (M, hidden, kv_dim)      — KV=256
      - V_proj: (M, hidden, kv_dim)
      - O_proj: (M, qkv_dim, hidden)
      - FFN_gate: (M, hidden, intermediate)  — I=9728
      - FFN_up: (M, hidden, intermediate)
      - FFN_down: (M, intermediate, hidden)
    """
    spec = get_spec("qwen2.5-3b")
    H = spec.hidden
    I = spec.intermediate
    QKV = spec.qkv_dim
    KV = spec.kv_heads * spec.head_dim

    return [
        ("Q_proj", M, H, QKV),
        ("K_proj", M, H, KV),
        ("V_proj", M, H, KV),
        ("O_proj", M, QKV, H),
        ("FFN_gate", M, H, I),
        ("FFN_up", M, H, I),
        ("FFN_down", M, I, H),
    ]


def _make_engines():
    systolic = create_engine(_SYSTOLIC_CONFIG)
    mxumodel = MXUModel(_SYSTOLIC_CONFIG)
    return systolic, mxumodel


def test_os_systolic_decode():
    """OS-Systolic decode tok/s should not exceed BlockEngine for the same array.

    OS avoids WS pipeline fill/drain, but its PEs are wider (accumulator +
    output register) so the same die area buys fewer MACs. For an equal 128×128
    array it should land in the same DMA-bound ballpark as BlockEngine, not
    above it.
    """
    M, K, N = 1, 11008, 2048

    os_engine = create_engine(_engine_config("os_systolic"))
    block = create_engine(_engine_config("block"))

    r_os = os_engine.estimate(M, K, N)
    r_block = block.estimate(M, K, N)

    os_tok_s = _tok_s(r_os)
    block_tok_s = _tok_s(r_block)

    assert r_os.bottleneck == "dma", (
        f"Expected DMA-bound OS-Systolic engine, got {r_os.bottleneck}"
    )
    assert os_tok_s <= block_tok_s, (
        f"OS-Systolic tok/s ({os_tok_s:.1f}) should not exceed "
        f"BlockEngine tok/s ({block_tok_s:.1f})"
    )
    assert r_os.details["per_tile_compute"] >= 3


def test_systolic_vs_mxumodel_decode():
    """SystolicEngine decode (M=1, M=2) total_cycles match MXUModel byte-for-byte."""
    systolic, mxumodel = _make_engines()

    for M in (1, 2):
        for name, M_used, K, N in _qwen3b_geometries(M):
            r_sys = systolic.estimate(M_used, K, N)
            r_mxu = mxumodel.estimate(M_used, K, N)

            assert r_sys.total_cycles == r_mxu.total_cycles, (
                f"[{name} M={M}] SystolicEngine total_cycles={r_sys.total_cycles} "
                f"≠ MXUModel total_cycles={r_mxu.total_cycles}"
            )


def test_systolic_vs_mxumodel_prefill():
    """SystolicEngine prefill (M=128) total_cycles match MXUModel byte-for-byte."""
    systolic, mxumodel = _make_engines()

    for name, M_used, K, N in _qwen3b_geometries(128):
        r_sys = systolic.estimate(M_used, K, N)
        r_mxu = mxumodel.estimate(M_used, K, N)

        assert r_sys.total_cycles == r_mxu.total_cycles, (
            f"[{name}] SystolicEngine total_cycles={r_sys.total_cycles} "
            f"≠ MXUModel total_cycles={r_mxu.total_cycles}"
        )


def test_systolic_npu_sim_baseline():
    """npu_sim.py --engine systolic --json produces decode tok/s near 20.0."""
    sim_dir = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["python", "npu_sim.py", "--engine", "systolic", "--json"],
        cwd=str(sim_dir),
        capture_output=True, text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"npu_sim.py failed:\n{result.stderr}"

    output = json.loads(result.stdout)
    tok_per_s = output["decode"]["tok_per_s"]

    assert tok_per_s == pytest.approx(20.0, rel=0.01), (
        f"Systolic decode tok/s={tok_per_s} not within ±1% of 20.0"
    )
