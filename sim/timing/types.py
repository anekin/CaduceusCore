"""Timing data types for cycle-level performance metrics."""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ModuleBreakdown:
    """Per-module cycle breakdown for a single token.

    Maps module name to cycle count for the six tracked modules.
    """
    cycles: Dict[str, int] = field(default_factory=lambda: {
        "mxu": 0,
        "sfu": 0,
        "vector": 0,
        "dma_weight": 0,
        "dma_effective": 0,
        "kv_cache": 0,
        "noc_latency": 0,
        "noc_contention": 0,
    })


@dataclass
class TokenTiming:
    """Cycle-level timing for a single token (prefill or decode)."""
    token_idx: int
    phase: str  # "prefill" or "decode"
    total_cycles: int = 0
    module_breakdown: ModuleBreakdown = field(default_factory=ModuleBreakdown)


@dataclass
class RequestMetrics:
    """Aggregated metrics for a complete inference request."""
    prompt_len: int = 0
    output_tokens: int = 0
    prefill_cycles: int = 0
    decode_cycles_per_token: List[int] = field(default_factory=list)
    ttft_us: float = 0.0
    tps: float = 0.0
    tpot_us: float = 0.0
    itl_us_list: List[float] = field(default_factory=list)
