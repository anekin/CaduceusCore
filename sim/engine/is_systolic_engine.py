"""Input-Stationary Engine — 第三经典数据流

参考: SCALE-Sim 三种数据流对比 (arXiv 2410.22595)

Input-stationary: 激活常驻 PE，权重流式穿过。
对 M=1 decode: 极差 — 只有 1 行激活，权重重复流过 K 次。
主要是为了完整性对比，确认 WS/OS/IS 三者的 M=1 性能排序。
"""

import math
from typing import Any, Dict
from engine.mac_engine import MACEngine, EngineResult


class InputStationaryEngine(MACEngine):
    """Input-stationary systolic array.

    激活常驻 PE，权重从上方流入，部分和从左方/下方流出。
    适合: 激活复用率高（大 M）、权重相对小的场景。
    不适合: M=1 decode（几乎没有激活复用）。
    """

    @property
    def engine_type(self) -> str:
        return "input_stationary"

    @property
    def area_estimate_mm2(self) -> float:
        return (self.H * self.W / (128 * 128)) * 8.0

    def estimate(self, M: int, K: int, N: int,
                 weight_preloaded: bool = False) -> EngineResult:
        """IS GEMM estimate.

        激活 (M×K) 常驻 PE。对 M=1, K=2560:
          - 只有 1 行激活，占用 H=128 PE 中的 1 个
          - 利用率 1/128 = 0.78%

        每 N-tile: K 个权重流过 H 个 PE
          - pipeline_fill = H (weights flow top→bottom)
          - pipe = K (all K weights flow through)
          - drain = W (results flow out)
        """
        # IS: M maps to array rows (H), N to columns (W)
        M_tiles = math.ceil(M / self.H)  # for M=1: 1
        N_tiles = math.ceil(N / self.W)
        total_tiles = M_tiles * N_tiles

        # Per tile: K weights flow through
        pipeline_fill = self.H   # weight flow top→bottom
        pipeline_drain = self.W  # results flow left→right (or bottom)
        per_tile_compute = pipeline_fill + K + pipeline_drain

        # Weight data: K × N_tile columns = K × min(N,W)
        tile_weight_bytes = math.ceil(K * min(N, self.W) * self.w_bits / 8)
        # Activation data: M_tile × K
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
                "dataflow": "input_stationary",
            },
        )

    def estimate_weight_cache_pair(self, M: int, K: int, N: int) -> EngineResult:
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
        )
