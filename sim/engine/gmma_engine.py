"""GMMA Engine — Group Matrix Multiply Accumulate (Hopper H100 style)

参考: NVIDIA Hopper H100 GMMA + TMA (Tensor Memory Accelerator)

GMMA = Block Engine 的异步升级版:
  1. Tile 粒度: 128×128×32 (Block Engine 的 K 维无上限，每次 tile K 不定)
  2. TMA: 异步 DMA 引擎，数据搬移和计算完全重叠
  3. Shared Memory: 大容量片上 SRAM 做权重 buffer

对单 die NPU decode:
  - TMA 的价值: DMA 不阻塞计算 (不是带宽翻倍，而是时间重叠)
  - 代价: TMA 单元面积 + Shared Memory 容量
  - DRAM 墙仍在 — 但利用率可到 100%
"""

import math
from typing import Any, Dict
from engine.mac_engine import MACEngine, EngineResult


class GMMAEngine(MACEngine):
    """GMMA — Group MMA with async TMA DMA.

    Key architectural difference from Block Engine:
      Block: DMA → compute → DMA → compute  (sequential)
      GMMA:  DMA overlap compute             (async via TMA)

    Area model:
      - MAC array: same as Block Engine
      - TMA unit: +2mm² for descriptor engine + crossbar
      - Shared Memory for weight buffer: +4MB → +6mm² (0.0015mm²/KB)
    """

    TILE_K_GMMA = 32   # GMMA processes K in chunks of 32
    TMA_AREA_MM2 = 2.0
    SHMEM_KB = 4096    # 4MB shared memory for weights

    @property
    def engine_type(self) -> str:
        return "gmma"


    def estimate(self, M: int, K: int, N: int,
                 weight_preloaded: bool = False) -> EngineResult:
        """GMMA GEMM — DMA/compute overlap model.

        TMA enables FULL overlap: while computing tile N, TMA loads tile N+1.
        effective_time = max(compute, dma) not compute + dma.
        """

        H_tiles = math.ceil(K / self.H)
        W_tiles = math.ceil(N / self.W)
        total_tiles = H_tiles * W_tiles

        # Per-tile data (weights cover H×W via K-slices of TILE_K_GMMA)
        K_slices_per_tile = math.ceil(self.H / self.TILE_K_GMMA)
        tile_weight_bytes = (
            K_slices_per_tile * self.TILE_K_GMMA * self.W * self.w_bits // 8
        )
        tile_act_bytes = math.ceil(M * self.H * self.a_bits / 8)

        # DMA time per tile
        dma_time = (tile_weight_bytes + tile_act_bytes) / self.eff_bw
        # Compute time per tile: 1 cycle (all MACs fire in parallel)
        compute_time = 1.0

        # ── GMMA async model ──
        # Cold start: first tile DMA + compute (no overlap possible)
        # Pipeline: subsequent tiles overlap — bottleneck = max(compute,dma)
        bottleneck = max(compute_time, dma_time)
        first_tile = dma_time + compute_time

        if total_tiles > 1:
            total = int(first_tile + (total_tiles - 1) * bottleneck)
        else:
            total = int(first_tile)

        total_macs = M * K * N
        weight_bytes = total_tiles * (tile_weight_bytes + tile_act_bytes)
        ideal = math.ceil(total_macs / self.peak_macs_per_cycle)
        util = ideal / total if total > 0 else 0.0

        # How much of DMA was hidden by overlap?
        dma_no_overlap = total_tiles * dma_time
        dma_with_overlap = total - total_tiles  # non-compute cycles
        dma_hidden_pct = (1 - dma_with_overlap / max(dma_no_overlap, 1)) * 100

        return EngineResult(
            compute_cycles=total_tiles,
            dma_cycles=int(total - total_tiles),
            total_cycles=total,
            utilization=util,
            ops=total_macs,
            num_tiles=total_tiles,
            weight_bytes=weight_bytes,
            bottleneck="dma" if dma_time > compute_time else "compute",
            details={
                "total_tiles": total_tiles,
                "per_tile_dma": round(dma_time, 1),
                "compute_per_tile": compute_time,
                "dma_hidden_pct": round(dma_hidden_pct, 1),
                "tma_async": True,
                "shared_mem_kb": self.SHMEM_KB,
            },
        )

    def estimate_weight_cache_pair(self, M: int, K: int, N: int) -> EngineResult:
        """Gate+Up with GMMA — TMA overlap already handles this.

        GMMA with TMA already achieves near-perfect DMA/compute overlap.
        Weight cache for gate/up pair saves weight reload but TMA hides it.
        """
        r1 = self.estimate(M, K, N)
        r2 = self.estimate(M, K, N)

        # Savings: shared activations for same K-tile
        activation_savings = (
            math.ceil(M * min(self.H, K) * self.a_bits / 8)
            / self.eff_bw
        )

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
            details={
                "note": "TMA hides most weight cache benefit; activation sharing only",
                "activation_savings_cycles": round(activation_savings),
            },
        )
