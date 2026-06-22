"""Output-Stationary Engine — Gemmini 风格

参考: Gemmini (UC Berkeley), "Architectural Insights: Comparing WS and OS" (IEEE 2024)

Output-stationary: 每个 PE 持有一个输出元素，权重和激活同时流动。
对 M=1 decode: K 维度切成 tile，每次流过 H=128 个权重。
pipeline fill = W (weights enter from left, flow right) = 128
比 WS 的 fill=256 少一半，但 compute = K+H 也类似。
"""

import math
from typing import Any, Dict

from engine.mac_engine import MACEngine, EngineResult


class OutputStationaryEngine(MACEngine):
    """Output-stationary systolic array — Gemmini 风格.

    每个 PE 持有一个 output element (M,N)。
    权重从左流入、激活从上流入。

    对 M=1 decode:
      - pipeline fill: W (vs WS: H+W)
      - 但 K_tiles 保持不变
      - 总 overhead 比 WS 少 ~25%
    """

    @property
    def engine_type(self) -> str:
        return "os_systolic"


    def estimate(self, M: int, K: int, N: int,
                 weight_preloaded: bool = False) -> EngineResult:
        """OS GEMM estimate.

        对 (M,K,N):
          - Output tile: (M_tile, N_tile) where M_tile ≤ H, N_tile ≤ W
          - K dimension flows through (time)
          - Per tile: W(fill) + K*M/M_tile(compute) + M(drain)
        """
        # For OS: M maps to array rows, N to columns
        # Tile along M: ceil(M/H), along N: ceil(N/W)
        M_tiles = math.ceil(M / self.H)
        N_tiles = math.ceil(N / self.W)
        total_tiles = M_tiles * N_tiles

        # Per tile compute
        # K elements must flow through: K / (H?) — K is time dimension
        # Simplified: K cycles for all K to flow, + pipeline
        pipeline_fill = self.W   # weights enter from left
        pipeline_drain = min(M, self.H)  # output rows drain
        per_tile_compute = pipeline_fill + K + pipeline_drain

        # Per tile data: K×N weights + M×K activations (but only tile portion)
        tile_weight_bytes = math.ceil(K * min(N, self.W) * self.w_bits / 8)
        tile_act_bytes = math.ceil(min(M, self.H) * K * self.a_bits / 8)
        per_tile_dma = (tile_weight_bytes + tile_act_bytes) / self.eff_bw

        bottleneck = max(per_tile_compute, per_tile_dma)
        first_cold = per_tile_dma + per_tile_compute

        if total_tiles > 1:
            total = int(first_cold + (total_tiles - 1) * bottleneck)
        else:
            total = int(first_cold)

        total_macs = M * K * N
        total_weight_bytes = total_tiles * (tile_weight_bytes + tile_act_bytes)
        ideal = math.ceil(total_macs / self.peak_macs_per_cycle)
        util = ideal / total if total > 0 else 0.0

        return EngineResult(
            compute_cycles=int(per_tile_compute * total_tiles),
            dma_cycles=int(total - per_tile_compute * total_tiles),
            total_cycles=total,
            utilization=util,
            ops=total_macs,
            num_tiles=total_tiles,
            weight_bytes=total_weight_bytes,
            bottleneck="compute" if per_tile_compute > per_tile_dma else "dma",
            details={
                "M_tiles": M_tiles, "N_tiles": N_tiles,
                "per_tile_compute": per_tile_compute,
                "per_tile_dma": round(per_tile_dma, 1),
                "pipeline_fill": pipeline_fill,
                "dataflow": "output_stationary",
            },
        )

    def estimate_weight_cache_pair(self, M: int, K: int, N: int) -> EngineResult:
        """OS doesn't benefit from weight cache the way WS does."""
        r1 = self.estimate(M, K, N)
        r2 = self.estimate(M, K, N)
        return EngineResult(
            compute_cycles=r1.compute_cycles + r2.compute_cycles,
            dma_cycles=r1.dma_cycles + r2.dma_cycles,
            total_cycles=r1.total_cycles + r2.total_cycles,
            utilization=(r1.utilization + r2.utilization) / 2,
            ops=r1.ops + r2.ops,
            num_tiles=r1.num_tiles + r2.num_tiles,
            weight_bytes=r1.weight_bytes + r2.weight_bytes,
            bottleneck="compute",
            details={"note": "OS dataflow, weight cache not applicable"},
        )
