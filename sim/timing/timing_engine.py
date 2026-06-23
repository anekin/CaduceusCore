"""Timing engine wrapper around NPUSimulator.

Accumulates per-module cycles from SimulationReport layer breakdowns and
timeline events, producing TokenTiming and RequestMetrics dataclasses.
"""

from typing import Dict, List, Tuple

import yaml

from sim.engine.timeline import SimulationReport
from sim.model_specs import ModelSpec
from sim.npu_sim import NPUSimulator
from sim.timing.types import ModuleBreakdown, RequestMetrics, TokenTiming


MODULE_KEYS = ("mxu", "sfu", "vector", "dma_weight", "dma_effective", "kv_cache", "noc_latency", "noc_contention")


def _build_llm_trace(model_spec: ModelSpec, m: int) -> List[Tuple[int, int, int, int, str]]:
    """Generalized transformer GEMM trace for any ModelSpec.

    Mirrors ``npu_sim.generate_qwen3b_trace`` but uses the model dimensions
    from ``model_spec`` instead of hard-coded Qwen2.5-3B values.

    Each transformer layer emits 7 matmuls:
      Q/K/V projections, O projection, FFN gate, FFN up, FFN down.
    """
    hidden = model_spec.hidden
    intermediate = model_spec.intermediate
    qkv_dim = model_spec.qkv_dim
    kv_dim = model_spec.kv_heads * model_spec.head_dim

    trace: List[Tuple[int, int, int, int, str]] = []
    for layer in range(model_spec.layers):
        trace.append((m, hidden, qkv_dim, layer, "Q_proj"))
        trace.append((m, hidden, kv_dim, layer, "K_proj"))
        trace.append((m, hidden, kv_dim, layer, "V_proj"))
        trace.append((m, qkv_dim, hidden, layer, "O_proj"))
        trace.append((m, hidden, intermediate, layer, "FFN_gate"))
        trace.append((m, hidden, intermediate, layer, "FFN_up"))
        trace.append((m, intermediate, hidden, layer, "FFN_down"))
    return trace


def _aggregate_layer_breakdowns(report: SimulationReport) -> ModuleBreakdown:
    """Sum per-module cycles across all LayerBreakdown entries."""
    mb = ModuleBreakdown()
    for layer in report.layer_breakdowns:
        for key in MODULE_KEYS:
            mb.cycles[key] += int(getattr(layer, key, 0))
    return mb


def _aggregate_events(report: SimulationReport) -> ModuleBreakdown:
    """Fallback: sum cycles from timeline events when layer_breakdowns are empty."""
    mb = ModuleBreakdown()
    for ev in report.events:
        cycles = ev.end_cycle - ev.start_cycle
        if ev.module == "mxu":
            mb.cycles["mxu"] += cycles
        elif ev.module == "sfu":
            mb.cycles["sfu"] += cycles
        elif ev.module == "vector":
            mb.cycles["vector"] += cycles
        elif ev.module == "kv":
            mb.cycles["kv_cache"] += cycles
        elif ev.module == "dma":
            # Overlapped DMA is effective (hidden) work; non-overlapped is
            # weight/stall work exposed on the critical path.
            if ev.overlapped:
                mb.cycles["dma_effective"] += cycles
            else:
                mb.cycles["dma_weight"] += cycles
        elif ev.module == "noc":
            if ev.overlapped:
                mb.cycles["noc_latency"] += cycles
            else:
                mb.cycles["noc_contention"] += cycles
    return mb


def _report_to_token_timing(
    report: SimulationReport,
    token_idx: int,
    phase: str,
) -> TokenTiming:
    """Convert a SimulationReport into a TokenTiming."""
    if report.layer_breakdowns:
        mb = _aggregate_layer_breakdowns(report)
    else:
        mb = _aggregate_events(report)

    total_cycles = sum(mb.cycles.values())
    return TokenTiming(
        token_idx=token_idx,
        phase=phase,
        total_cycles=total_cycles,
        module_breakdown=mb,
    )


class TimingEngine:
    """Wraps ``NPUSimulator`` to produce cycle-level ``TokenTiming`` objects."""

    def __init__(self, config_path: str):
        with open(config_path) as f:
            self.config = yaml.safe_load(f)
        self.sim = NPUSimulator(config_path)
        self.freq_mhz = int(
            self.config.get("mxu", {}).get("frequency_mhz", 1000)
        )

    def simulate_prefill(self, model_spec: ModelSpec, prompt_len: int) -> TokenTiming:
        """Run a prefill simulation and return a ``TokenTiming``.

        ``model_spec`` is accepted for API symmetry with ``simulate_decode``;
        the underlying ``NPUSimulator.simulate_prefill`` currently uses a
        hard-coded Qwen2.5-3B trace.  Module cycles are extracted from the
        returned ``SimulationReport``.
        """
        report = self.sim.simulate_prefill(prompt_len)
        return _report_to_token_timing(report, token_idx=0, phase="prefill")

    def simulate_decode(
        self,
        model_spec: ModelSpec,
        prompt_len: int = 1,
    ) -> TokenTiming:
        """Run a decode simulation and return a ``TokenTiming``.

        ``prompt_len`` is the batch/M dimension passed to each GEMM; for
        autoregressive decode this is ``1``.
        """
        trace = _build_llm_trace(model_spec, m=prompt_len)
        report = self.sim.simulate_decode(trace)
        return _report_to_token_timing(report, token_idx=0, phase="decode")

    def simulate_request(
        self,
        model_spec: ModelSpec,
        prompt_len: int,
        gen_len: int,
    ) -> RequestMetrics:
        """Orchestrate one prefill plus ``gen_len`` decode tokens."""
        prefill_timing = self.simulate_prefill(model_spec, prompt_len)
        decode_cycles_per_token: List[int] = []
        for _ in range(gen_len):
            decode_timing = self.simulate_decode(model_spec, prompt_len=1)
            decode_cycles_per_token.append(decode_timing.total_cycles)

        return RequestMetrics(
            prompt_len=prompt_len,
            output_tokens=gen_len,
            prefill_cycles=prefill_timing.total_cycles,
            decode_cycles_per_token=decode_cycles_per_token,
        )

    def simulate_cv(self, cv_trace: list[dict]) -> RequestMetrics:
        """Run a CV trace through the decode path and return request metrics.

        Only entries with ``M > 0`` contribute GEMM work; they are converted to
        the LLM-style tuple ``(M, K, N, layer_idx, op_name)`` consumed by
        ``NPUSimulator.simulate_decode``.  The resulting cycle count is reported
        as a single-token decode so that ``MetricsCollector`` and ``Dashboard``
        can treat it like a one-shot inference.
        """
        llm_trace: List[Tuple[int, int, int, int, str]] = []
        for layer_idx, entry in enumerate(cv_trace):
            m = int(entry.get("M", 0))
            if m <= 0:
                continue
            k = int(entry.get("K", 0))
            n = int(entry.get("N", 0))
            op_name = str(entry.get("name", entry.get("type", f"layer{layer_idx}")))
            llm_trace.append((m, k, n, layer_idx, op_name))

        report = self.sim.simulate_decode(llm_trace)
        timing = _report_to_token_timing(report, token_idx=0, phase="decode")

        metrics = RequestMetrics(
            output_tokens=1,
            prefill_cycles=0,
            decode_cycles_per_token=[timing.total_cycles],
        )
        metrics.module_breakdown = timing.module_breakdown.cycles
        return metrics
