"""WMMA Engine — 16×16×16 Warp Matrix Multiply Accumulate

参考: NVIDIA Volta/Ampere Tensor Core WMMA API

每个 warp (32 threads) 协作算一个 16×16×16 块。
对 M=1 decode 来说，16×16 粒度产生灾难性 DMA 碎片。

与 Block Engine 的本质区别:
  - Block: 全阵列 128×128 一个大 tile
  - WMMA: 阵列被切成 64 个独立的 16×16 小块，各自 DMA
  - 同样 total bytes 但 64× 更多的 DMA 事务 → 启动开销爆炸
"""

import math
from typing import Any, Dict
from engine.mac_engine import MACEngine, EngineResult


class WMMAEngine(MACEngine):
    """Warp Matrix Multiply Accumulate — 16×16 TC blocks."""

    TILE_M = 16
    TILE_N = 16
    TILE_K = 16
    DMA_STARTUP_CYCLES = 10  # Per-tile DMA initiation overhead

    @property
    def engine_type(self) -> str:
        return "wmma"


    @property
    def num_warps(self) -> int:
        """Number of independent 16×16 blocks that fit in the array."""
        return (self.H // self.TILE_M) * (self.W // self.TILE_N)

    def estimate(self, M: int, K: int, N: int,
                 weight_preloaded: bool = False) -> EngineResult:
        """WMMA GEMM — massive tile fragmentation for M=1."""

        # Output grid
        M_tiles = math.ceil(M / self.TILE_M)
        N_tiles = math.ceil(N / self.TILE_N)
        K_steps = math.ceil(K / self.TILE_K)

        total_tiles = M_tiles * N_tiles * K_steps

        # Per-tile data
        tile_w = math.ceil(self.TILE_M * self.TILE_K * self.w_bits / 8)
        tile_a = math.ceil(M * self.TILE_K * self.a_bits / 8)

        # DMA: each tile loads weight + activation plus startup overhead
        per_tile_dma = (tile_w + tile_a) / self.eff_bw
        per_tile_dma_with_startup = per_tile_dma + self.DMA_STARTUP_CYCLES

        # Warp-level parallelism
        nw = self.num_warps
        waves = math.ceil(total_tiles / nw)

        # Per wave: all warps fire, but share DRAM bus
        per_wave_dma = (tile_w + tile_a) * nw / self.eff_bw + nw * self.DMA_STARTUP_CYCLES
        per_wave_compute = 1.0

        bottleneck = max(per_wave_compute, per_wave_dma)
        first_wave = per_wave_dma + per_wave_compute

        if waves > 1:
            total = int(first_wave + (waves - 1) * bottleneck)
        else:
            total = int(first_wave)

        total_macs = M * K * N
        weight_bytes = total_tiles * (tile_w + tile_a)
        ideal = math.ceil(total_macs / self.peak_macs_per_cycle)
        util = ideal / total if total > 0 else 0.0

        return EngineResult(
            compute_cycles=waves,
            dma_cycles=int(total - waves),
            total_cycles=total,
            utilization=util,
            ops=total_macs,
            num_tiles=total_tiles,
            weight_bytes=weight_bytes,
            bottleneck="dma",
            details={
                "total_tiles": total_tiles,
                "num_warps": nw,
                "waves": waves,
                "per_wave_dma": round(per_wave_dma, 1),
                "per_wave_compute": per_wave_compute,
                "tile_size": f"{self.TILE_M}×{self.TILE_K}×{self.TILE_N}",
                "dma_startup_per_tile": self.DMA_STARTUP_CYCLES,
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
            bottleneck="dma",
            details={"note": "No weight cache benefit for WMMA — tiles too small"},
        )
