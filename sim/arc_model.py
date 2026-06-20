#!/usr/bin/env python3
"""Arc Model — unified NPU architecture evaluation.

Two dimensions:
  A. Precision:  load GGUF weights → per-block INT4 quant → cos_sim check (GATE)
  B. Performance: MXU timing model → decode tok/s, utilization, DRAM stall

Gate rule: all weight layers must pass cos_sim ≥ 0.97 before performance eval.
"""

import sys
import time
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "ggml-npu"))
sys.path.insert(0, str(_HERE))

from q4_dequant import load_weights_from_gguf
from golden_executor import GoldenMXU
from quantize import quantize_int4_per_block


@dataclass
class PrecisionReport:
    n_layers: int = 0
    cos_mean: float = 0.0
    cos_min: float = 0.0
    cos_std: float = 0.0
    worst_layer: str = ""
    worst_cos: float = 1.0
    passed: bool = False


@dataclass
class PerfReport:
    decode_tok_s: float = 0.0
    decode_us_tok: float = 0.0
    mxu_util_pct: float = 0.0
    dram_stall_pct: float = 0.0
    total_mac_g: float = 0.0


@dataclass
class ArcReport:
    model_name: str = ""
    hidden: int = 0
    intermediate: int = 0
    layers: int = 0
    precision: Optional[PrecisionReport] = None
    perf: Optional[PerfReport] = None
    passed: bool = False
    error: str = ""


class ArcModel:
    """Unified architecture evaluator: precision gate → performance model.

    Supports configurable quantization schemes:
      - per-channel: one scale per output channel
      - per-block:   group_size=128 along K (TensorRT/GPTQ standard)
      - both:        compare both schemes with per-layer breakdown
    """

    COS_THRESHOLD = 0.96   # per-layer vector cosine minimum (INT4: ~0.97 expected)
    SCHEMES = {
        "per-channel": {"name": "Per-Channel INT4", "desc": "1 scale/output channel"},
        "per-block":   {"name": "Per-Block INT4 (g=128)", "desc": "TensorRT/GPTQ standard"},
    }

    # Known model configs
    MODELS = {
        "qwen2.5-1.5b":  (1536, 8960, 28, 2),
        "qwen2.5-3b":    (2560, 9728, 28, 2),
        "qwen2.5-7b":    (3584, 18944, 28, 4),
        "qwen3-8b":      (4096, 12288, 32, 4),
        "gemma-4-12b":   (4096, 16384, 40, 8),
    }

    def __init__(self, config_path: str = "config/npu_config.yaml"):
        from npu_sim import NPUSimulator
        self.sim = NPUSimulator(config_path)
        self.mxu = GoldenMXU()
        self.rng = np.random.RandomState(42)

    def _run_precision(self, weights: dict, scheme: str) -> PrecisionReport:
        """Run precision validation for one quantization scheme."""
        from quantize import quantize_int4_per_channel, quantize_int4_per_block

        use_block = (scheme == "per-block")
        cos_values = []
        worst_layer = ""
        worst_cos = 1.0
        n_tested = 0

        for name, W_f32 in sorted(weights.items()):
            if W_f32.ndim != 2 or "weight" not in name.lower():
                continue
            K, N = W_f32.shape
            if K < 64 or N < 64:
                continue

            act = self.rng.randint(-128, 128, size=K, dtype=np.int8).reshape(1, K)
            golden = act.astype(np.float32) @ W_f32.astype(np.float32)
            g_vec = golden[0, :].astype(np.float64)
            ng = np.linalg.norm(g_vec)

            if use_block:
                packed, scales, _ = quantize_int4_per_block(W_f32, group_size=128)
                result = self.mxu.matmul_int4_per_block(act, packed, scales, 1, K, N, group_size=128)
            else:
                packed, scales, _ = quantize_int4_per_channel(W_f32)
                result = self.mxu.matmul_int4_per_channel(act, packed, scales, 1, K, N)

            t_vec = result[0, :].astype(np.float64)
            nt = np.linalg.norm(t_vec)
            cos_val = float(np.dot(g_vec, t_vec)) / max(ng * nt, 1e-16)
            cos_values.append(cos_val)

            if cos_val < worst_cos:
                worst_cos = cos_val
                worst_layer = name
            n_tested += 1

        cos_arr = np.array(cos_values)
        return PrecisionReport(
            n_layers=n_tested,
            cos_mean=float(np.mean(cos_arr)),
            cos_min=float(np.min(cos_arr)),
            cos_std=float(np.std(cos_arr)),
            worst_layer=worst_layer,
            worst_cos=worst_cos,
            passed=worst_cos >= self.COS_THRESHOLD,
        )

    def evaluate(self, gguf_path: str,
                 scheme: str = "per-block",
                 model_spec: Optional[tuple] = None) -> ArcReport:
        """Run full Arc evaluation: precision → performance.

        Args:
            gguf_path: path to Q4_K GGUF model
            scheme: "per-channel", "per-block", or "both"
            model_spec: optional (hidden, intermediate, layers, kv_heads) tuple

        Returns:
            ArcReport with precision and performance dimensions.
        """
        name = Path(gguf_path).stem
        report = ArcReport(model_name=name)

        # Auto-detect model spec
        spec = model_spec
        if spec is None:
            for key, val in self.MODELS.items():
                if key.replace(".", "").replace("-", "") in name.lower().replace(".", "").replace("-", ""):
                    spec = val
                    break
        if spec is None:
            report.error = f"Unknown model spec for {name}. Pass model_spec=(H,I,L,KV)"
            return report

        report.hidden, report.intermediate, report.layers = spec[0], spec[1], spec[2]

        # ── Load weights ────────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"Arc Model — Precision Gate")
        print(f"{'='*60}")

        t0 = time.time()
        try:
            weights = load_weights_from_gguf(gguf_path)
        except Exception as e:
            report.error = f"GGUF load failed: {e}"
            return report
        print(f"Loaded {len(weights)} tensors in {time.time()-t0:.1f}s")

        # ── A. Precision ────────────────────────────────────────────
        schemes_to_run = list(self.SCHEMES.keys()) if scheme == "both" else [scheme]

        scheme_results = {}
        for s in schemes_to_run:
            t1 = time.time()
            pr = self._run_precision(weights, s)
            dt = time.time() - t1
            scheme_results[s] = pr

            label = self.SCHEMES[s]["name"]
            icon = "✓" if pr.passed else "✗"
            print(f"\n  [{label}] {pr.n_layers} layers in {dt:.1f}s")
            print(f"    cos_sim: mean={pr.cos_mean:.6f}  min={pr.cos_min:.6f}  std={pr.cos_std:.6f}")
            print(f"    worst:   {pr.worst_layer[-60:]}  cos={pr.worst_cos:.6f}")
            print(f"    gate:    {icon} PASS (threshold={self.COS_THRESHOLD})")

        if scheme == "both":
            pc_pr = scheme_results["per-channel"]
            pb_pr = scheme_results["per-block"]
            delta = pb_pr.cos_mean - pc_pr.cos_mean
            winner = "per-block" if delta > 0 else "per-channel"
            print(f"\n  Comparison: per-block − per-channel = {delta:+.4f} cos_sim")
            print(f"  → {winner} wins  (min: {pc_pr.cos_min:.4f} vs {pb_pr.cos_min:.4f})")
            best = pb_pr if delta > 0 else pc_pr
            report.precision = best
            pr = best
        else:
            pr = scheme_results[scheme]
            report.precision = pr

        # ── B. Performance Model ────────────────────────────────────
        if not pr.passed:
            print(f"\n  → Skipping performance eval: precision gate not met")
            report.passed = False
            return report

        print(f"\n{'='*60}")
        print(f"Arc Model — Performance")
        print(f"{'='*60}")

        H, I, L = report.hidden, report.intermediate, report.layers
        kv_heads = spec[3]
        head_dim = 128
        qkv = spec[0]
        kv = kv_heads * head_dim

        trace = []
        for layer in range(L):
            trace.append((1, H, qkv, layer, "Q_proj"))
            trace.append((1, H, kv,  layer, "K_proj"))
            trace.append((1, H, kv,  layer, "V_proj"))
            trace.append((1, qkv, H,  layer, "O_proj"))
            trace.append((1, H, I,   layer, "FFN_gate"))
            trace.append((1, H, I,   layer, "FFN_up"))
            trace.append((1, I, H,   layer, "FFN_down"))

        perf = self.sim.simulate_decode(trace)
        total_mac = sum(m * k * n for m, k, n, _, _ in trace) * 2

        mxu_us = perf.decode_breakdown.get("MXU", 0)
        dma_us = perf.decode_breakdown.get("DMA (stall)", 0)
        total_us = perf.decode_per_token_us

        pf = PerfReport(
            decode_tok_s=perf.decode_tok_per_s,
            decode_us_tok=total_us,
            mxu_util_pct=mxu_us / total_us * 100 if total_us > 0 else 0,
            dram_stall_pct=dma_us / total_us * 100 if total_us > 0 else 0,
            total_mac_g=total_mac / 1e9,
        )
        report.perf = pf
        report.passed = True

        print(f"\n  Config: {H} hidden, {I} intermediate, {L} layers")
        print(f"  Decode: {pf.decode_tok_s:.1f} tok/s  ({pf.decode_us_tok:.0f} us/tok)")
        print(f"  MXU:    {pf.mxu_util_pct:.1f}% util")
        print(f"  DRAM:   {pf.dram_stall_pct:.1f}% stall")
        print(f"  MAC:    {pf.total_mac_g:.2f}G")

        return report

    def print_table(self, report: ArcReport):
        """Print final summary table."""
        pr = report.precision
        pf = report.perf
        print(f"\n{'='*80}")
        print(f"Arc Model — Final Report: {report.model_name}")
        print(f"{'='*80}")
        print(f"{'Dimension':<15} {'Metric':<22} {'Value':>15}")
        print(f"{'-'*15} {'-'*22} {'-'*15}")
        print(f"{'Precision':<15} {'layers':<22} {pr.n_layers:>15d}")
        print(f"{'Precision':<15} {'cos_sim (mean)':<22} {pr.cos_mean:>15.6f}")
        print(f"{'Precision':<15} {'cos_sim (min)':<22} {pr.cos_min:>15.6f}")
        print(f"{'Precision':<15} {'cos_sim (std)':<22} {pr.cos_std:>15.6f}")
        print(f"{'Precision':<15} {'gate passed':<22} {str(pr.passed):>15}")
        if pf:
            print(f"{'Performance':<15} {'decode tok/s':<22} {pf.decode_tok_s:>15.1f}")
            print(f"{'Performance':<15} {'decode us/tok':<22} {pf.decode_us_tok:>15.0f}")
            print(f"{'Performance':<15} {'MXU utilization':<22} {pf.mxu_util_pct:>14.1f}%")
            print(f"{'Performance':<15} {'DRAM stall':<22} {pf.dram_stall_pct:>14.1f}%")
        print(f"{'='*80}")
        print(f"  Overall: {'✓ PASS' if report.passed else '✗ FAIL'}")
        if report.error:
            print(f"  Error: {report.error}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Arc Model evaluation")
    parser.add_argument("--model", required=True, help="Path to GGUF model")
    parser.add_argument("--scheme", default="per-block",
                        choices=["per-channel", "per-block", "both"],
                        help="INT4 quantization scheme (default: per-block)")
    parser.add_argument("--spec", help="Model spec: H,I,L,KV (e.g., 1536,8960,28,2)")
    args = parser.parse_args()

    spec = None
    if args.spec:
        spec = tuple(int(x) for x in args.spec.split(","))

    arc = ArcModel()
    report = arc.evaluate(args.model, scheme=args.scheme, model_spec=spec)
    arc.print_table(report)
