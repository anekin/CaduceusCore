import json
import os
import subprocess
import sys
from pathlib import Path

from design_space_explorer import (
    _load_base_config,
    generate_configs,
    simulate_layer,
    simulate_prefill,
    tok_s_from_layer,
    ttft_ms_from_prefill,
)


def _block_64x64_config():
    """Base design-space config with the RTL Phase-1 Block 64×64 array."""
    cfg = _load_base_config()
    cfg["mac_engine"]["type"] = "block"
    cfg["mac_engine"]["array_height"] = 64
    cfg["mac_engine"]["array_width"] = 64
    return cfg


def test_tok_s_from_layer_scales_with_frequency():
    layer_cycles = 1000
    num_layers = 10

    slow = tok_s_from_layer(layer_cycles, num_layers, f_mhz=800)
    base = tok_s_from_layer(layer_cycles, num_layers, f_mhz=1000)
    fast = tok_s_from_layer(layer_cycles, num_layers, f_mhz=1200)

    assert slow < base < fast


def test_generate_configs_applies_lpddr5_scenario():
    configs = generate_configs(quick=True, scenario_name="lpddr5_3b")

    assert configs
    assert {c["memory"]["bandwidth_gbps"] for c in configs} == {51.2}
    assert {c["memory"]["dram_width_bits"] for c in configs} == {64}
    assert {c["area_model"]["process_node"] for c in configs} == {12}
    assert {c["_scenario_name"] for c in configs} == {"lpddr5_3b"}


def test_prefill_ttft():
    """Prefill cycles grow with batch_m; TTFT helper returns milliseconds."""
    cfg = _block_64x64_config()

    cycles_1, _ = simulate_prefill(cfg, 1, "qwen2.5-3b")
    cycles_128, _ = simulate_prefill(cfg, 128, "qwen2.5-3b")

    assert cycles_1 > 0
    assert cycles_128 > cycles_1  # more prefill tokens → more cycles

    ttft = ttft_ms_from_prefill(cycles_128, 36, freq_mhz=1000)
    assert ttft > 0
    assert ttft == round(cycles_128 * 36 / 1000 / 1000.0, 2)


def test_ttft_ms_from_prefill_formula():
    """TTFT [ms] = prefill_cycles × num_layers / freq_mhz / 1000."""
    assert ttft_ms_from_prefill(1_000_000, 28, 1000) == 28.0
    assert ttft_ms_from_prefill(0, 28, 1000) == 0.0


def test_simulate_layer_defaults_to_batch_one():
    """batch_m=None must behave like batch_m=1 (decode) — backward compat."""
    cfg = _block_64x64_config()
    assert simulate_layer(cfg) == simulate_layer(cfg, batch_m=1)


def test_cli_accepts_batch_m_128_and_emits_ttft_ms(tmp_path):
    """CLI must accept --batch-m 128 and emit ttft_ms for Block 64×64."""
    sim_dir = Path(__file__).resolve().parents[1]
    out_path = tmp_path / "dse_ttft_m128.json"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(sim_dir) + os.pathsep + env.get("PYTHONPATH", "")

    proc = subprocess.run(
        [sys.executable, str(sim_dir / "design_space_explorer.py"),
         "--quick", "--batch-m", "128", "--model-spec", "qwen2.5-3b",
         "--output", str(out_path)],
        cwd=str(sim_dir), env=env,
        capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, f"CLI failed:\n{proc.stdout}\n{proc.stderr}"

    data = json.loads(out_path.read_text())
    block64 = [
        r for r in data["pareto_frontier"] + data["top_results"]
        if r["config"]["engine"] == "block"
        and r["config"]["array_height"] == 64
        and r["config"]["array_width"] == 64
    ]
    assert block64, "no Block 64×64 config found in DSE output"
    for r in block64:
        assert "ttft_ms" in r
        assert r["ttft_ms"] > 0
