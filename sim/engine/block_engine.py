"""Block Engine — 全并行 MAC 阵列，纯空间映射

与 systolic 的本质区别:
  - 无 pipeline fill/drain: 数据广播到所有 MAC，1 cycle 出结果
  - 瓶颈: 纯 DMA（加载权重+激活）
  - 代价: 全互连 crossbar 广播总线，面积 3-5× 同规模 systolic

参考: NVIDIA Tensor Core, Google TPUv4 的 vector-matrix unit
"""

import math
from typing import Any, Dict

from engine.mac_engine import MACEngine, EngineResult


class BlockEngine(MACEngine):
    """Block MAC engine — all MACs fire in parallel per tile.

    Dataflow: broadcast weights + activations to all MAC units.
    Each tile processes H×K submatrix in a single cycle.

    Area model:
      - MAC array: same as systolic (H×W PEs)
      - Crossbar: ~2× MAC area for broadcast interconnect
      - Register file: ~1× MAC area for local weight storage
      - Total: ~4× systolic per MAC (conservative)
    """

    @property
    def engine_type(self) -> str:
        return "block"


    def estimate(self, M: int, K: int, N: int,
                 weight_preloaded: bool = False) -> EngineResult:
        """Block GEMM estimate.

        For each (H,W) tile:
          - DMA: load H×W weights + M×H activations
          - Compute: 1 cycle (all MACs fire simultaneously)
          - Bottleneck: always DMA for M=1
        """
        K_tiles = math.ceil(K / self.H)
        N_tiles = math.ceil(N / self.W)
        total_tiles = K_tiles * N_tiles

        # Per-tile data
        tile_weight_bytes = math.ceil(self.H * self.W * self.w_bits / 8)
        tile_act_bytes = math.ceil(M * self.H * self.a_bits / 8)

        # DMA: weights + activations
        per_tile_dma = (tile_weight_bytes + tile_act_bytes) / self.eff_bw
        # Compute: 1 cycle per tile (plus minimal pipeline if any)
        per_tile_compute = 1.0  # all MACs fire in same cycle

        # Double-buffering: DMA next tile while computing current
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
            compute_cycles=total_tiles,  # 1 cycle per tile
            dma_cycles=int(total - total_tiles),
            total_cycles=total,
            utilization=util,
            ops=total_macs,
            num_tiles=total_tiles,
            weight_bytes=total_weight_bytes,
            bottleneck="dma" if per_tile_dma > per_tile_compute else "compute",
            details={
                "K_tiles": K_tiles, "N_tiles": N_tiles,
                "per_tile_dma": round(per_tile_dma, 1),
                "per_tile_compute": per_tile_compute,
                "pipeline_overhead": 0,
            },
        )

    def estimate_weight_cache_pair(self, M: int, K: int, N: int) -> EngineResult:
        """Gate+Up with block engine.

        Block engine already has single-cycle compute, so weight cache
        mainly avoids reloading activations for the shared K-tile.
        But the benefit is small since compute is already 1 cycle.
        """
        # Same as two separate estimates since block engine has no
        # pipeline fill/drain to save — the overhead is already 0.
        r1 = self.estimate(M, K, N)
        r2 = self.estimate(M, K, N)

        # Slight savings: shared activation for same K-tile
        # Activations are M*H bytes per tile. For M=1: 128 bytes → ~3 cycles
        activation_savings = math.ceil(M * self.H * self.a_bits / 8) * \
            math.ceil(K / self.H) / self.eff_bw

        total = r1.total_cycles + r2.total_cycles - int(activation_savings)

        return EngineResult(
            compute_cycles=r1.compute_cycles + r2.compute_cycles,
            dma_cycles=total - (r1.compute_cycles + r2.compute_cycles),
            total_cycles=total,
            utilization=(r1.utilization + r2.utilization) / 2,
            ops=r1.ops + r2.ops,
            num_tiles=r1.num_tiles + r2.num_tiles,
            weight_bytes=r1.weight_bytes + r2.weight_bytes,
            bottleneck="dma",
            details={"weight_cache_savings": round(activation_savings),
                     "note": "Block engine has 0 pipeline overhead, cache benefit minimal"},
        )
