"""Systolic Engine — weight-stationary systolic array (现有 MXU v2 模型)"""

import math
from typing import Any, Dict

from engine.mac_engine import MACEngine, EngineResult


class SystolicEngine(MACEngine):
    """Weight-stationary systolic array — 时空映射.

    权重对角加载到 PE 阵列，激活流式穿过。
    每 tile 有 pipeline fill(H+W) + drain(M+H) 开销。
    """

    @property
    def engine_type(self) -> str:
        return "systolic"

    @property
    def area_estimate_mm2(self) -> float:
        # 8mm² PE array + 2mm² SFU + 2.5mm² L1/SRAM
        return (self.H * self.W / (128 * 128)) * 8.0

    def estimate(self, M: int, K: int, N: int,
                 weight_preloaded: bool = False) -> EngineResult:
        """Systolic GEMM estimate.

        For M=1 decode: tile overhead dominates.
        For M>1 prefill: utilization improves.
        """
        K_tiles = math.ceil(K / self.H)
        N_tiles = math.ceil(N / self.W)
        total_tiles = K_tiles * N_tiles

        # Per-tile data
        tile_weight_bytes = math.ceil(self.H * self.W * self.w_bits / 8)
        tile_act_bytes = math.ceil(M * self.H * self.a_bits / 8)

        # Pipeline
        pipeline_fill = self.H + self.W
        pipeline_drain = M + self.H
        per_tile_compute = pipeline_fill + pipeline_drain
        per_tile_dma = (tile_weight_bytes + tile_act_bytes) / self.eff_bw

        bottleneck = max(per_tile_compute, per_tile_dma)
        first_tile_cold = per_tile_dma + per_tile_compute

        if total_tiles > 1:
            total = int(first_tile_cold + (total_tiles - 1) * bottleneck)
        else:
            total = int(first_tile_cold)

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
                "K_tiles": K_tiles, "N_tiles": N_tiles,
                "per_tile_compute": per_tile_compute,
                "per_tile_dma": round(per_tile_dma, 1),
                "pipeline_fill": pipeline_fill,
                "pipeline_drain": pipeline_drain,
            },
        )

    def estimate_weight_cache_pair(self, M: int, K: int, N: int) -> EngineResult:
        """Gate+Up with PE dual weight register."""
        K_tiles = math.ceil(K / self.H)
        N_tiles = math.ceil(N / self.W)
        total_dual = K_tiles * N_tiles

        dual_weight_bytes = 2 * math.ceil(self.H * self.W * self.w_bits / 8)
        dual_act_bytes = math.ceil(M * self.H * self.a_bits / 8)
        dual_dma = (dual_weight_bytes + dual_act_bytes) / self.eff_bw

        per_matm_drain = M + self.W
        dual_compute = 2 * per_matm_drain + 1

        fill = self.H + self.W
        drain = M + self.H

        bottleneck = max(dual_dma, dual_compute)
        first_cold = dual_dma + dual_compute

        if N_tiles >= 2:
            per_Ktile = fill + first_cold + (N_tiles - 1) * bottleneck + drain
        else:
            per_Ktile = fill + first_cold + drain

        total = int(K_tiles * per_Ktile)

        total_macs = M * K * N * 2
        total_weight_bytes = total_dual * (dual_weight_bytes + dual_act_bytes)
        ideal = math.ceil(total_macs / self.peak_macs_per_cycle)
        util = ideal / total if total > 0 else 0.0

        return EngineResult(
            compute_cycles=int(dual_compute * total_dual),
            dma_cycles=int(total - dual_compute * total_dual),
            total_cycles=total,
            utilization=util,
            ops=total_macs,
            num_tiles=total_dual,
            weight_bytes=total_weight_bytes,
            bottleneck="compute" if dual_compute > dual_dma else "dma",
            details={
                "K_tiles": K_tiles, "N_tiles": N_tiles,
                "dual_dma": round(dual_dma, 1),
                "dual_compute": dual_compute,
                "weight_cache": True,
            },
        )
