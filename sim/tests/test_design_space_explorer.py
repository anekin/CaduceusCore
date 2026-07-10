from design_space_explorer import generate_configs, tok_s_from_layer


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
