#!/usr/bin/env python3
"""NPU System Simulator — Phase 2: MXU + SFU + DMA + KV Cache + DRAM

Usage:
    python3 npu_sim.py                          # default settings
    python3 npu_sim.py -c config/npu_2core.yaml # custom config
    python3 npu_sim.py --prefill 2048           # longer prompt
    python3 npu_sim.py --json                   # JSON output
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

# Add parent to path for relative imports
sys.path.insert(0, str(Path(__file__).parent))

from models.mxu import MXUModel
from models.sfu import SFUModel
from models.dma import DMAModel
from models.kv_cache import KVCacheModel
from models.dram import DRAMModel
from engine.timeline import (
    CoreTimeline, LayerBreakdown, SimulationReport, breakdown_events,
)
from engine.multicore import MultiCoreTimeline, FIFOConfig, CrossbarConfig


# ── Default 3B model trace ──────────────────────────────────────────
# Each GEMM: (M, K, N, layer, op_name)
# Derived from Qwen2.5-3B config (28 layers, hidden=2560, intermediate=9728)
# Decode: M=1 (single token), Prefill: M=128

def generate_qwen3b_trace(prompt_len: int = 128) -> List[Tuple[int, int, int, int, str]]:
    """Generate GEMM trace from Qwen2.5-3B architecture.

    Each transformer layer has 7 matmuls:
    - Q projection: (M, 2560, 2560/32*32) — Q full heads
    - K projection: (M, 2560, 128*2) — KV heads only (GQA=2)
    - V projection: (M, 2560, 128*2)
    - O projection: (M, 2560, 2560)
    - FFN gate: (M, 2560, 9728) — SiLU gate
    - FFN up: (M, 2560, 9728)
    - FFN down: (M, 9728, 2560)
    """
    HIDDEN = 2560
    INTERMEDIATE = 9728
    NUM_LAYERS = 28
    NUM_HEADS = 32
    NUM_KV_HEADS = 2
    HEAD_DIM = 128
    QKV_DIM = NUM_HEADS * HEAD_DIM   # 32 * 128 = 4096
    KV_DIM = NUM_KV_HEADS * HEAD_DIM # 2 * 128 = 256

    trace = []
    for layer in range(NUM_LAYERS):
        trace.append((prompt_len, HIDDEN, QKV_DIM, layer, "Q_proj"))
        trace.append((prompt_len, HIDDEN, KV_DIM, layer, "K_proj"))
        trace.append((prompt_len, HIDDEN, KV_DIM, layer, "V_proj"))
        trace.append((prompt_len, QKV_DIM, HIDDEN, layer, "O_proj"))
        trace.append((prompt_len, HIDDEN, INTERMEDIATE, layer, "FFN_gate"))
        trace.append((prompt_len, HIDDEN, INTERMEDIATE, layer, "FFN_up"))
        trace.append((prompt_len, INTERMEDIATE, HIDDEN, layer, "FFN_down"))
    return trace


# ── Simulator ────────────────────────────────────────────────────────

class NPUSimulator:
    """Phase 1: Single-core NPU performance simulator."""

    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        self.num_cores = int(self.config.get("cores", 1))
        self.f_mhz = int(self.config["mxu"]["frequency_mhz"])

        # Initialize models
        self.mxu = MXUModel(self.config)
        self.sfu = SFUModel(self.config)
        self.dma = DMAModel(self.config)
        self.kv = KVCacheModel(self.config)
        self.dram = DRAMModel(self.config)

        # Configure KV cache for Qwen2.5-3B
        self.kv.configure_for_model(
            num_kv_heads=2, head_dim=128, num_layers=28, max_context=2048
        )

    def simulate_decode(self, trace: List[Tuple[int, int, int, int, str]]) -> SimulationReport:
        """Simulate decode: M=1 per GEMM.

        Key insight: for weight-stationary decode, weights are loaded ONCE
        at inference start (prefill). Per-token decode only streams
        activations and KV cache through the array.
        """
        timeline = CoreTimeline(core_id=0)
        layer_data: Dict[int, LayerBreakdown] = {}
        total_tokens = 128  # base context size for KV estimation

        for (M, K, N, layer, op_name) in trace:
            if layer not in layer_data:
                layer_data[layer] = LayerBreakdown(layer=layer)
                # Layer switch: KV cache SRAM reload overhead
                kv_switch = self.kv.layer_switch_cost()
                timeline.add_kv(f"layer_switch", kv_switch, layer)
                layer_data[layer].kv_cache += kv_switch

            # MXU: GEMM execution (weights preloaded for decode)
            mxu_result = self.mxu.estimate(M, K, N, weight_preloaded=True)
            mxu_cycles = mxu_result.total_cycles
            timeline.add_mxu(f"{op_name} ({M}×{K}×{N})", mxu_cycles, layer)
            layer_data[layer].mxu += mxu_cycles

            # DMA: activation load only (weights pre-loaded, decode-only)
            # Activation: M×K elements × activation_bits/8 bytes
            act_bytes = math.ceil(M * K * self.config["mxu"]["activation_precision_bits"] / 8)
            dma_raw = self.dma.estimate_transfer(act_bytes, "load")
            eff, hidden = self.dma.estimate_effective(dma_raw, mxu_cycles)
            timeline.add_dma_parallel(f"Load Act({op_name})", dma_raw, layer)
            layer_data[layer].dma_weight += dma_raw
            layer_data[layer].dma_effective += eff

            # SFU: applied once per layer (not per GEMM)
            # We apply it after the last GEMM of the layer
            if op_name in ("O_proj",):  # after attention
                sfu_cycles = self.sfu.estimate("softmax", 2560)
                sfu_cycles += self.sfu.estimate("layernorm", 2560)
                sfu_cycles += self.sfu.estimate("rope", 2560 * 2)
                timeline.add_sfu("attn_sfu", sfu_cycles, layer)
                layer_data[layer].sfu += sfu_cycles
            elif op_name in ("FFN_down",):  # after FFN
                sfu_cycles = self.sfu.estimate("gelu", 9728)
                sfu_cycles += self.sfu.estimate("layernorm", 2560)
                timeline.add_sfu("ffn_sfu", sfu_cycles, layer)
                layer_data[layer].sfu += sfu_cycles

            # KV Cache: per-GEMM access (amortized)
            kv_cycles = self.kv.estimate_per_decode(total_tokens, total_tokens)
            timeline.add_kv("kv_access", kv_cycles, layer)
            layer_data[layer].kv_cache += kv_cycles

            # Update layer total
            layer_data[layer].total = (layer_data[layer].mxu + layer_data[layer].sfu
                                        + layer_data[layer].kv_cache)

        # Add DRAM refresh overhead (proportional to total)
        total_cycles_before = timeline.total_cycles
        refresh_cycles = self.dram.add_refresh_overhead(total_cycles_before)
        timeline.add_kv("dram_refresh", refresh_cycles, -1)

        total_cycles = timeline.total_cycles
        decode_us = total_cycles / self.f_mhz
        decode_tok_per_s = 1e6 / decode_us if decode_us > 0 else 0

        breakdown = breakdown_events(timeline.events)

        # Build report
        report = SimulationReport(
            model_name="Qwen2.5-3B",
            num_layers=28,
            prefill_prompt_len=128,
            prefill_total_ms=0.0,  # Not simulated in Phase 1
            decode_per_token_us=decode_us,
            decode_tok_per_s=decode_tok_per_s,
            decode_breakdown={k: v / self.f_mhz for k, v in breakdown.items()},
            layer_breakdowns=sorted(layer_data.values(), key=lambda lb: lb.layer),
            events=timeline.events,
        )
        return report

    # ── L2: ISA instruction interface ───────────────────────────────

    def run_instructions(self, program: "List[NPUInstruction]") -> SimulationReport:
        """L2 interface: execute NPU ISA program on the simulator.

        Maps each ISA instruction to the appropriate model call,
        advancing the timeline with proper overlap semantics.
        """
        from engine.isa import NPUInstruction, OpCode

        timeline = CoreTimeline(core_id=0)
        layer_data: Dict[int, LayerBreakdown] = {}
        current_layer = 0

        for instr in program:
            op = instr.opcode
            ops = instr.operands

            if op in (OpCode.NOP, OpCode.BARRIER):
                continue

            if op == OpCode.MMUL:
                N = ops.get("N", 2560)
                M = 1  # decode mode
                K = 2560  # hidden_size
                mxu_result = self.mxu.estimate(M, K, N, weight_preloaded=True)
                timeline.add_mxu(f"MMUL N={N}", mxu_result.total_cycles, current_layer)
                if current_layer not in layer_data:
                    layer_data[current_layer] = LayerBreakdown(layer=current_layer)
                layer_data[current_layer].mxu += mxu_result.total_cycles

            elif op == OpCode.DMA_LD:
                size = ops.get("size", 0)
                dma_cycles = self.dma.estimate_transfer(size, "load")
                timeline.add_dma_parallel(f"DMA_LD {size}B", dma_cycles, current_layer)

            elif op == OpCode.DMA_ST:
                size = ops.get("size", 0)
                dma_cycles = self.dma.estimate_transfer(size, "store")
                timeline.add_dma_parallel(f"DMA_ST {size}B", dma_cycles, current_layer)

            elif op in (OpCode.SOFTMAX, OpCode.LAYERNORM, OpCode.GELU, OpCode.RELU,
                        OpCode.SILU, OpCode.MAXPOOL, OpCode.AVGPOOL, OpCode.ROPE):
                length = ops.get("len", 2560)
                op_name = op.name.lower()
                sfu_cycles = self.sfu.estimate(op_name, length)
                timeline.add_sfu(op_name, sfu_cycles, current_layer)
                if current_layer not in layer_data:
                    layer_data[current_layer] = LayerBreakdown(layer=current_layer)
                layer_data[current_layer].sfu += sfu_cycles

            elif op in (OpCode.KV_LOAD, OpCode.KV_STORE):
                kv_cycles = self.kv.estimate_per_decode(128, 128)
                timeline.add_kv(op.name.lower(), kv_cycles, current_layer)

        # DRAM refresh
        refresh_cycles = self.dram.add_refresh_overhead(timeline.total_cycles)
        timeline.add_kv("dram_refresh", refresh_cycles, -1)

        total_cycles = timeline.total_cycles
        decode_us = total_cycles / self.f_mhz
        decode_tok_per_s = 1e6 / decode_us if decode_us > 0 else 0

        breakdown = breakdown_events(timeline.events)

        return SimulationReport(
            model_name="Qwen2.5-3B",
            num_layers=28,
            decode_per_token_us=decode_us,
            decode_tok_per_s=decode_tok_per_s,
            decode_breakdown={k: v / self.f_mhz for k, v in breakdown.items()},
            layer_breakdowns=sorted(layer_data.values(), key=lambda lb: lb.layer),
            events=timeline.events,
        )

    def simulate_prefill(self, prompt_len: int = 128) -> SimulationReport:
        """Simulate prefill: M=prompt_len per GEMM.

        Prefill is compute-heavy but bandwidth-friendly: large M means
        the systolic array stays full, utilization is high.
        """
        trace = generate_qwen3b_trace(prompt_len=prompt_len)
        timeline = CoreTimeline(core_id=0)

        for (M, K, N, layer, op_name) in trace:
            mxu_result = self.mxu.estimate(M, K, N, weight_preloaded=False)
            mxu_cycles = mxu_result.total_cycles
            timeline.add_mxu(f"{op_name} ({M}×{K}×{N})", mxu_cycles, layer)

            # DMA: load weights (prefill — first time)
            weight_bytes = math.ceil(K * N * self.config["mxu"]["weight_precision_bits"] / 8)
            dma_raw = self.dma.estimate_transfer(weight_bytes, "load")
            timeline.add_dma_parallel(f"Load W({op_name})", dma_raw, layer)

            if op_name in ("O_proj",):
                sfu_cycles = self.sfu.estimate("softmax", 2560 * prompt_len)
                sfu_cycles += self.sfu.estimate("layernorm", 2560 * prompt_len)
                sfu_cycles += self.sfu.estimate("rope", 2560 * 2 * prompt_len)
                timeline.add_sfu("attn_sfu", sfu_cycles, layer)
            elif op_name in ("FFN_down",):
                sfu_cycles = self.sfu.estimate("gelu", 9728 * prompt_len)
                sfu_cycles += self.sfu.estimate("layernorm", 2560 * prompt_len)
                timeline.add_sfu("ffn_sfu", sfu_cycles, layer)

            kv_cycles = self.kv.estimate_per_decode(prompt_len, prompt_len)
            timeline.add_kv("kv_access", kv_cycles, layer)

        # DRAM refresh overhead
        total_before = timeline.total_cycles
        refresh_cycles = self.dram.add_refresh_overhead(total_before)
        timeline.add_kv("dram_refresh", refresh_cycles, -1)

        total_cycles = timeline.total_cycles
        prefill_ms = total_cycles / self.f_mhz / 1000

        breakdown = breakdown_events(timeline.events)

        report = SimulationReport(
            model_name="Qwen2.5-3B",
            num_layers=28,
            prefill_prompt_len=prompt_len,
            prefill_total_ms=prefill_ms,
            prefill_breakdown={k: v / self.f_mhz / 1000 for k, v in breakdown.items()},
            decode_per_token_us=0,
            decode_tok_per_s=0,
            events=timeline.events,
        )
        return report


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NPU System Simulator — Phase 3")
    parser.add_argument("-c", "--config", default="config/npu_config.yaml")
    parser.add_argument("--prefill", type=int, default=128)
    parser.add_argument("--isa", action="store_true",
                        help="Use ISA instruction mode (L2 interface)")
    parser.add_argument("-o", "--output", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sim_dir = Path(__file__).parent
    config_path = sim_dir / args.config
    sim = NPUSimulator(str(config_path))

    if args.isa:
        # ── L2: ISA instruction mode ─────────────────────────────
        from engine.compiler import NPUCompiler
        compiler = NPUCompiler(num_cores=1)
        decode_trace = generate_qwen3b_trace(prompt_len=1)
        program = compiler.compile_decode(decode_trace, weight_preloaded=True)
        print(f"[ISA] Compiled {len(program)} instructions for decode")
        report = sim.run_instructions(program)
    else:
        # ── L1: CSV trace mode ───────────────────────────────────
        decode_trace = generate_qwen3b_trace(prompt_len=1)
        report = sim.simulate_decode(decode_trace)

    # Inject prefill results
    prefill_report = sim.simulate_prefill(prompt_len=args.prefill)
    report.prefill_prompt_len = args.prefill
    report.prefill_total_ms = prefill_report.prefill_total_ms
    report.prefill_breakdown = prefill_report.prefill_breakdown

    print(report.to_text())

    # ── Multi-core projection ────────────────────────────────────
    if report.decode_tok_per_s > 0:
        print(f"\n--- Multi-core Projection ---")
        mct = MultiCoreTimeline(num_cores=1)
        base_us = report.decode_per_token_us
        print(f"  {'Config':12s} {'Decode':>10s} {'Area':>8s} {'Notes'}")
        print(f"  {'─'*50}")
        for nc in [1, 2, 4, 8]:
            mct.num_cores = nc
            dp = mct.simulate_data_parallel(int(base_us), 1)
            area = {1: 27, 2: 42, 4: 69, 8: 122}.get(nc, 0)
            note = "Baseline" if nc == 1 else f"DP, -{int((1-dp['contention_penalty'])*100)}% contention"
            print(f"  {f'{nc} core':12s} {dp['effective_tok_per_s']:7,.0f} tok/s  {area:4d}mm²  {note}")

    # JSON output
    if args.json or args.output:
        output = {
            "decode": {
                "per_token_us": round(report.decode_per_token_us, 1),
                "tok_per_s": round(report.decode_tok_per_s, 0),
                "breakdown": {k: round(v, 1) for k, v in report.decode_breakdown.items()},
            },
            "prefill": {
                "prompt_len": args.prefill,
                "total_ms": round(prefill_report.prefill_total_ms, 1),
                "breakdown": {k: round(v, 1) for k, v in prefill_report.prefill_breakdown.items()},
            },
        }
        if args.json:
            print("\n" + json.dumps(output, indent=2))
        if args.output:
            with open(sim_dir / args.output, "w") as f:
                json.dump(output, f, indent=2)
                print(f"\nResults saved to {args.output}")


if __name__ == "__main__":
    main()
