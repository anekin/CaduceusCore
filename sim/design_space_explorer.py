#!/usr/bin/env python3
"""设计空间搜索器 — 多引擎多配置对比，输出 Pareto 前沿

用法:
  python3 design_space_explorer.py              # 默认搜索
  python3 design_space_explorer.py --quick      # 快速扫描（减少组合）
  python3 design_space_explorer.py --output results/pareto.json
"""

import sys, json, copy, math, itertools
from pathlib import Path
from typing import Dict, Any, List, Tuple

sys.path.insert(0, str(Path(__file__).parent))
from engine.ppa_model import AreaModel, PowerModel, PPA
from engine.mac_engine import create_engine
from model_specs import get_spec, all_aliases

import yaml

SIM_DIR = Path(__file__).parent

_CV_MODEL: str = ""
_CV_TRACE: List[Any] = []
_CV_ONNX_PATH: str = ""

_NUM_LAYERS: int = 28
_LLM_TRACE: List[Tuple] = []
_SEQ_KV: int = 2048      # KV cache sequence length for decode
_KV_HEADS: int = 2        # num_kv_heads from model spec
_HEAD_DIM: int = 128      # head_dim from model spec


def generate_trace_from_spec(alias: str, batch_m: int = 1) -> List[Tuple]:
    global _KV_HEADS, _HEAD_DIM
    spec = get_spec(alias)
    H = spec.hidden
    I = spec.intermediate
    qkv = spec.qkv_dim
    kv = spec.kv_heads * spec.head_dim
    _KV_HEADS = spec.kv_heads
    _HEAD_DIM = spec.head_dim
    trace = []
    m_attn = batch_m  # attention projections batch all tokens
    m_ffn = batch_m if batch_m > 1 else 1  # prefill: batch tokens; decode: single token
    trace.append((m_attn, H, qkv, 0, "Q_proj"))
    trace.append((m_attn, H, kv,  0, "K_proj"))
    trace.append((m_attn, H, kv,  0, "V_proj"))
    trace.append((m_attn, qkv, H, 0, "O_proj"))
    trace.append((m_ffn, H, I,    0, "FFN_gate"))
    trace.append((m_ffn, H, I,    0, "FFN_up"))
    trace.append((m_ffn, I, H,    0, "FFN_down"))
    return trace


_LLM_TRACE = generate_trace_from_spec("qwen2.5-3b", batch_m=1)

SFU_CYCLES_PER_LAYER = {
    "attn": 33,   # softmax + layernorm + rope (simplified)
    "ffn": 8,     # gelu + layernorm
}


def _compute_kv_cycles(config: Dict[str, Any], batch_m: int = 1) -> int:
    """Dynamic KV cache DRAM read cycles per layer.

    - For decode (batch_m=1): K,V read from DRAM, 40% of L2 SRAM as tile buffer
    - For prefill (batch_m>1): KV written (not read), negligible cost
    - Scales with seq_kv, SRAM size, and effective bandwidth
    """
    if batch_m > 1:
        return 0  # Prefill: KV is being written, not a read bottleneck

    sram = config.get("sram", {})
    l2_kb = int(sram.get("l2_shared_kb", 2048))
    kvbuf_kb = int(l2_kb * 0.4)

    # K + V: 2 × seq_kv × kv_heads × head_dim × 1 byte (INT8)
    kv_bytes = 2 * _SEQ_KV * _KV_HEADS * _HEAD_DIM * 1

    mem = config.get("memory", {})
    bw_raw = float(mem.get("bandwidth_bytes_per_cycle", 51.2))
    dram_eff = float(mem.get("dram_efficiency", 0.85))
    eff_bw = bw_raw * dram_eff

    kv_mb = kv_bytes / (1024 * 1024.0)
    kvbuf_mb = kvbuf_kb / 1024.0
    ratio = kvbuf_mb / max(kv_mb, 0.001)
    kv_dram_eff = 0.55 + 0.40 * ratio / (0.3 + ratio)

    if eff_bw <= 0 or kv_dram_eff <= 0:
        return 0

    # cycles = bytes / (effective GB/s) — BW values are in GB/s units
    return int(kv_bytes / (eff_bw * kv_dram_eff))


def simulate_layer(config: Dict[str, Any], batch_m: int = None) -> tuple:
    """Simulate one transformer layer. Returns (total_cycles, weight_bytes).

    batch_m=1 for decode, >1 for prefill. If None, inferred from trace.
    """
    if batch_m is None:
        batch_m = _LLM_TRACE[0][0] if _LLM_TRACE else 1
    engine = create_engine(config)
    opts = config.get("optimizations", {})
    weight_cache = opts.get("weight_cache", False)

    total = 0
    weight_bytes = 0
    i = 0
    ops = _LLM_TRACE

    while i < len(ops):
        M, K, N, _, name = ops[i]

        # Weight cache merge
        if (weight_cache and name == "FFN_gate" and i + 1 < len(ops)
                and ops[i + 1][4] == "FFN_up"):
            r = engine.estimate_weight_cache_pair(M, K, N)
            i += 2
        else:
            r = engine.estimate(M, K, N)
            i += 1

        total += r.total_cycles
        weight_bytes += r.weight_bytes

        # SFU
        if name == "O_proj":
            total += SFU_CYCLES_PER_LAYER["attn"]
        elif name == "FFN_down":
            total += SFU_CYCLES_PER_LAYER["ffn"]

    # KV cache: dynamic read cost based on SRAM + bandwidth
    kv_cycles = _compute_kv_cycles(config, batch_m)
    total += kv_cycles

    return total, weight_bytes


def tok_s_from_layer(layer_cycles: int, num_layers: int) -> float:
    f_mhz = 1000
    total_us = layer_cycles * num_layers / f_mhz
    return round(1e6 / total_us, 1) if total_us > 0 else 0


def _depthwise_util_from_cv_result(cv_result: Dict[str, Any]) -> float:
    utils = [
        layer.get("mxu_util_pct", 0.0)
        for layer in cv_result.get("layers", [])
        if layer.get("type") == "depthwise_conv"
    ]
    return sum(utils) / len(utils) if utils else 0.0


def generate_configs(quick: bool = False) -> List[Dict[str, Any]]:
    """Generate design space configurations to sweep."""
    with open(SIM_DIR / "config" / "design_space.yaml") as f:
        base = yaml.safe_load(f)

    configs = []

    # Engine types — all seven architectures
    if quick:
        engines = ["systolic", "block", "gmma"]
    else:
        engines = ["systolic", "os_systolic", "block",
                   "tensor_core", "wmma", "gmma", "input_stationary", "fsa"]

    # Array dimensions (constrained by area)
    if quick:
        dims = [(128, 128), (128, 256), (256, 256)]
    else:
        dims = [(64, 64), (96, 96), (128, 128), (128, 192),
                (128, 256), (192, 256), (256, 256)]

    # DRAM bandwidth configurations (GB/s, width_bits, description)
    if quick:
        dram_configs = [
            (51.2, 64, "LPDDR5-64b"),
            (102.4, 128, "LPDDR5-128b"),
        ]
    else:
        dram_configs = [
            (25.6, 32, "LPDDR5-32b"),      # Low-end mobile
            (51.2, 64, "LPDDR5-64b"),      # Baseline
            (102.4, 128, "LPDDR5-128b"),   # Dual channel / 128-bit
            (204.8, 256, "LPDDR5-256b"),   # Quad channel
            (460.0, 1024, "HBM2e-1024b"),  # HBM2e 3.6Gbps
            (819.2, 1024, "HBM3-1024b"),   # HBM3 6.4Gbps
        ]

    # Weight precision
    if quick:
        precisions = [4]
    else:
        precisions = [4, 2]  # INT4, INT2

    # Frequency
    freqs = [1000] if quick else [800, 1000, 1200]

    # SRAM L2 sizes (KB) — critical for bandwidth-constrained performance
    sram_l2_sizes = [2048] if quick else [1024, 2048, 4096, 6144, 8192]

    for engine_type in engines:
        for H, W in dims:
            # Area constraints
            if engine_type in ("block", "os_systolic") and H * W / (128 * 128) * 32 > 200:
                continue
            if engine_type == "systolic" and H * W / (128 * 128) * 8 > 80:
                continue
            if engine_type in ("tensor_core", "wmma") and H * W / (128 * 128) * 37 > 200:
                continue
            if engine_type == "gmma" and H * W / (128 * 128) * 40 > 200:
                continue
            if engine_type == "input_stationary" and H * W / (128 * 128) * 24 > 150:
                continue

            for bw_gbps, dw_bits, dram_label in dram_configs:
                for w_bits in precisions:
                    for freq in freqs:
                        for l2_kb in sram_l2_sizes:
                            # weight_cache only for systolic
                            wc_options = [False]
                            if engine_type in ("systolic", "block", "gmma"):
                                wc_options = [False, True]

                            for wc in wc_options:
                                # Block/GMMA with weight_cache skip if bandwidth too low
                                if wc and engine_type != "systolic" and bw_gbps < 51.2:
                                    continue

                                cfg = copy.deepcopy(base)
                                cfg["mac_engine"]["type"] = engine_type
                                cfg["mac_engine"]["array_height"] = H
                                cfg["mac_engine"]["array_width"] = W
                                cfg["mac_engine"]["weight_precision_bits"] = w_bits
                                cfg["mac_engine"]["frequency_mhz"] = freq
                                cfg["memory"]["bandwidth_gbps"] = bw_gbps
                                cfg["memory"]["bandwidth_bytes_per_cycle"] = bw_gbps
                                cfg["memory"]["dram_width_bits"] = dw_bits
                                cfg["memory"]["dram_efficiency"] = 0.85
                                cfg["sram"]["l2_shared_kb"] = l2_kb
                                cfg["optimizations"]["weight_cache"] = wc
                                cfg["optimizations"]["dma_bw_multiplier"] = 1.0
                                cfg["_dram_label"] = dram_label

                                configs.append(cfg)

    return configs


def evaluate_config(cfg: Dict[str, Any], area_model: AreaModel,
                    power_model: PowerModel) -> PPA:
    """Evaluate one configuration → PPA."""
    engine_type = cfg["mac_engine"]["type"]

    if _CV_MODEL:
        from cv.cv_sim import simulate_cv
        cv_result = simulate_cv(_CV_TRACE, cfg)
        fps = 1e9 / cv_result["total_cycles"] if cv_result["total_cycles"] > 0 else 0.0
        area_result = area_model.estimate(cfg, engine_type)
        area = area_result["total_mm2"]
        power = power_model.estimate(area_model, cfg, engine_type)
        sram_spill = cv_result.get("sram_spill_mb", 0.0)
        dw_util = _depthwise_util_from_cv_result(cv_result)
    else:
        layer_cycles, _ = simulate_layer(cfg)
        fps = tok_s_from_layer(layer_cycles, _NUM_LAYERS)
        area_result = area_model.estimate(cfg, engine_type)
        area = area_result["total_mm2"]
        power = power_model.estimate(area_model, cfg, engine_type)
        sram_spill = 0.0
        dw_util = 0.0

    H = cfg["mac_engine"]["array_height"]
    W = cfg["mac_engine"]["array_width"]
    w_bits = cfg["mac_engine"]["weight_precision_bits"]
    wc = cfg["optimizations"]["weight_cache"]
    bw = cfg["optimizations"]["dma_bw_multiplier"]
    freq = cfg["mac_engine"]["frequency_mhz"]

    label = (f"{engine_type[:4]} {H}×{W} INT{w_bits} "
             f"{freq}MHz "
             f"{'WC' if wc else ''} "
             f"{cfg.get('_dram_label', '')}")

    return PPA(
        tok_s=fps,
        area_mm2=area,
        power_w=power,
        config_label=label,
        sram_spill_mb=sram_spill,
        depthwise_util_pct=dw_util,
    )


def find_pareto(ppas: List[PPA]) -> List[PPA]:
    """Find Pareto-optimal points (max tok/s, min area)."""
    pareto = []
    for p in ppas:
        dominated = False
        for q in ppas:
            if (q.tok_s >= p.tok_s and q.area_mm2 <= p.area_mm2 and
                    (q.tok_s > p.tok_s or q.area_mm2 < p.area_mm2)):
                dominated = True
                break
        if not dominated:
            pareto.append(p)
    return sorted(pareto, key=lambda x: x.area_mm2)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--output", default=None)
    parser.add_argument("--top", type=int, default=20,
                        help="Show top N results")
    parser.add_argument("--cv-model", choices=["mobilenetv3-small", "yolov8n", "vit-b16", "resnet18", "resnet50"],
                        default=None,
                        help="Run CV design-space exploration")
    parser.add_argument("--model-spec",
                        choices=[a for a in all_aliases() if get_spec(a).model_type == "llm"],
                        default=None,
                        help="LLM model spec alias for DSE")
    parser.add_argument("--batch-m", type=int, choices=[1, 2], default=None,
                        help="Batch M dimension for attention ops (1 or 2)")
    args = parser.parse_args()

    if args.cv_model and (args.model_spec is not None or args.batch_m is not None):
        parser.error("--cv-model is mutually exclusive with --model-spec and --batch-m")

    model_spec = args.model_spec if args.model_spec is not None else "qwen2.5-3b"
    batch_m = args.batch_m if args.batch_m is not None else 1

    global _CV_MODEL, _CV_TRACE, _CV_ONNX_PATH, _LLM_TRACE, _NUM_LAYERS
    _CV_MODEL = args.cv_model or ""
    if _CV_MODEL:
        if args.cv_model == "mobilenetv3-small":
            from cv.cv_trace import generate_mobilenetv3_trace
            _CV_ONNX_PATH = str(Path(__file__).parent.parent / "assets" / "mobilenetv3_small.onnx")
            _CV_TRACE = generate_mobilenetv3_trace(_CV_ONNX_PATH)
        elif args.cv_model == "yolov8n":
            from cv.traces.yolov8n_trace import generate_yolov8n_trace
            _CV_TRACE = generate_yolov8n_trace()
        elif args.cv_model == "vit-b16":
            from cv.traces.vit_trace import generate_vit_trace
            _CV_TRACE = generate_vit_trace()
        elif args.cv_model == "resnet18":
            from cv.traces.resnet18_trace import generate_resnet18_trace
            _CV_TRACE = generate_resnet18_trace()
        elif args.cv_model == "resnet50":
            from cv.traces.resnet50_trace import generate_resnet50_trace
            _CV_TRACE = generate_resnet50_trace()
    else:
        _LLM_TRACE = generate_trace_from_spec(model_spec, batch_m)
        _NUM_LAYERS = get_spec(model_spec).layers

    with open(SIM_DIR / "config" / "design_space.yaml") as f:
        base_cfg = yaml.safe_load(f)

    area_model = AreaModel(base_cfg)
    power_model = PowerModel(base_cfg)

    configs = generate_configs(quick=args.quick)
    print(f"Design space: {len(configs)} configurations")
    print(f"  Engine types: systolic, block")
    dim_set = set((c['mac_engine']['array_height'],
                   c['mac_engine']['array_width']) for c in configs)
    print(f"  Array dims: {len(dim_set)}")
    print(f"  Sweeping...", end=" ", flush=True)

    results: List[PPA] = []
    for cfg in configs:
        try:
            ppa = evaluate_config(cfg, area_model, power_model)
            # Filter: unreasonable area
            if ppa.area_mm2 <= 200:
                results.append(ppa)
        except Exception as e:
            pass

    print(f"{len(results)} valid")

    # Pareto frontier
    pareto = find_pareto(results)

    # Top by tok/s (filter by area < 150mm²)
    reasonable = [r for r in results if r.area_mm2 <= 150]
    reasonable.sort(key=lambda x: x.tok_s, reverse=True)

    perf_label = "fps" if _CV_MODEL else "tok/s"
    eff_label = "fps/W" if _CV_MODEL else "tok/W"
    cv_extra_header = f" {'SRAM(MB)':>10} {'DW(%)':>8}" if _CV_MODEL else ""
    line_width = 100 if _CV_MODEL else 85

    # ── Output ──
    print(f"\n{'='*90}")
    print(f"  Pareto 前沿 (面积 vs 性能)")
    print(f"  {'Config':<45} {perf_label:>8} {'Area':>8} {'Power':>8} {eff_label:>8}{cv_extra_header}")
    print(f"  {'-'*line_width}")
    for p in pareto[:15]:
        arrow = "← Pareto" if p in pareto else ""
        extra = ""
        if _CV_MODEL:
            extra = f" {p.sram_spill_mb:>9.1f} {p.depthwise_util_pct:>7.3f}"
        print(f"  {p.config_label:<45} {p.tok_s:>7.0f} {p.area_mm2:>6.0f}mm² "
              f"{p.power_w:>6.1f}W {p.efficiency_tok_per_watt:>7.1f}{extra}")

    # ── Top by tok/s ──
    print(f"\n  Top {args.top} by {perf_label} (area ≤ 150mm²):")
    print(f"  {'Config':<45} {perf_label:>8} {'Area':>8} {'Power':>8} {eff_label:>8}{cv_extra_header}")
    print(f"  {'-'*line_width}")
    for p in reasonable[:args.top]:
        pareto_flag = "←" if p in pareto else ""
        extra = ""
        if _CV_MODEL:
            extra = f" {p.sram_spill_mb:>9.1f} {p.depthwise_util_pct:>7.3f}"
        print(f"  {p.config_label:<45} {p.tok_s:>7.0f} {p.area_mm2:>6.0f}mm² "
              f"{p.power_w:>6.1f}W {p.efficiency_tok_per_watt:>7.1f}{extra} {pareto_flag}")

    # ── Best per engine type ──
    print(f"\n  Best per engine type (area ≤ 80mm², DRAM ≤ 102.4 GB/s):")
    for eng in ["systolic", "os_systolic", "block", "tensor_core", "wmma", "gmma", "fsa"]:
        eng_results = [r for r in results
                       if eng in r.config_label and r.area_mm2 <= 80]
        if eng_results:
            best = max(eng_results, key=lambda x: x.tok_s)
            print(f"    {eng}: {best.tok_s:.0f} {perf_label}, {best.area_mm2:.0f}mm², "
                  f"{best.power_w:.1f}W — {best.config_label}")

    # ── Save ──
    if args.output:
        def _result_dict(p, on_pareto=False):
            d = {"label": p.config_label, "tok_s": p.tok_s,
                 "area_mm2": p.area_mm2, "power_w": p.power_w}
            if _CV_MODEL:
                d["sram_spill_mb"] = p.sram_spill_mb
                d["depthwise_util_pct"] = p.depthwise_util_pct
                prefix = (p.config_label or "").split()[0]
                engine_map = {
                    "syst": "systolic",
                    "os_s": "os_systolic",
                    "bloc": "block",
                    "tens": "tensor_core",
                    "wmma": "wmma",
                    "gmma": "gmma",
                    "inpu": "input_stationary",
                    "fsa ": "fsa",
                }
                d["engine_type"] = engine_map.get(prefix, prefix)
                d["pareto"] = on_pareto
            return d

        if _CV_MODEL:
            # CV mode: flat list of Pareto + top results so downstream tools
            # can verify engine diversity while keeping Pareto points primary.
            points = [_result_dict(p, True) for p in pareto]
            seen = {p.config_label for p in pareto}
            for p in reasonable[:args.top]:
                if p.config_label not in seen:
                    points.append(_result_dict(p, False))
            output = points
        else:
            output = {
                "cv_model": _CV_MODEL,
                "model_spec": model_spec,
                "batch_m": batch_m,
                "total_configs": len(configs),
                "valid_results": len(results),
                "pareto_frontier": [_result_dict(p, True) for p in pareto],
                "top_results": [_result_dict(p, False) for p in reasonable[:args.top]],
            }
        out_path = SIM_DIR / args.output if not args.output.startswith("/") else Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\n  Saved to {args.output}")


if __name__ == "__main__":
    main()
