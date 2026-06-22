"""Block Engine — 全并行 MAC 阵列，纯空间映射

与 systolic 的本质区别:
  - 无 diagonal pipeline fill/drain: 数据广播到所有 MAC
  - 但仍有广播同步 + 累加/归约开销，不是理想化的 1 cycle/tile
  - 瓶颈: 通常是 DMA（加载权重+激活），但 compute 不再是零
  - 代价: 全互连 crossbar 广播总线，面积 3-5× 同规模 systolic

参考: NVIDIA Tensor Core, Google TPUv4 的 vector-matrix unit
"""

import math
from typing import Any, Dict

from engine.mac_engine import MACEngine, EngineResult


# Realistic broadcast-pipeline constants for BlockEngine.
# Block engine broadcasts weights + activations to all PEs simultaneously
# (no diagonal fill like systolic), but it still pays:
#   - broadcast synchronization overhead (fan-out + PE latch enable)
#   - accumulate/reduction latency (wider precision -> more cycles)
BROADCAST_SYNC_CYCLES = 2  # ~2 cycles for weight+activation fan-out


def _accumulate_cycles(w_bits: int, a_bits: int) -> int:
    """Accumulate/reduction cycles for block-engine MAC tile.

    Empirical mapping: INT4/INT8 mixed ~2 cycles, INT8/INT8 ~3 cycles,
    capped at 3 and floored at 1.  This replaces the old "1 cycle per tile"
    assumption that made BlockEngine ~8× faster than systolic reality.
    """
    return max(1, min(3, (w_bits + a_bits) // 8 + 1))


class BlockEngine(MACEngine):
    """Block MAC engine — all MACs fire in parallel per tile.

    Dataflow: broadcast weights + activations to all MAC units.
    Each tile processes H×K submatrix with broadcast-sync + accumulate
    latency; the old "1 cycle per tile" model was over-optimistic.

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
          - Compute: broadcast_sync + accumulate/reduction cycles
          - Bottleneck: usually DMA for M=1, but compute is no longer zero
        """
        K_tiles = math.ceil(K / self.H)
        N_tiles = math.ceil(N / self.W)
        total_tiles = K_tiles * N_tiles

        # Per-tile data
        tile_weight_bytes = math.ceil(self.H * self.W * self.w_bits / 8)
        tile_act_bytes = math.ceil(M * self.H * self.a_bits / 8)

        # DMA: weights + activations
        per_tile_dma = (tile_weight_bytes + tile_act_bytes) / self.eff_bw
        # Compute: realistic broadcast pipeline (not 1 cycle/tile)
        per_tile_compute = BROADCAST_SYNC_CYCLES + \
            _accumulate_cycles(self.w_bits, self.a_bits)

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
            compute_cycles=int(per_tile_compute * total_tiles),
            dma_cycles=int(total - per_tile_compute * total_tiles),
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
                "pipeline_overhead": per_tile_compute,
            },
        )

    def estimate_weight_cache_pair(self, M: int, K: int, N: int) -> EngineResult:
        """Gate+Up with block-engine weight cache.

        Block engine supports dual weight registers so gate and up tiles
        sharing the same (M,K) activation can be processed back-to-back.
        The main benefit is avoiding the second activation broadcast: only
        one set of activations is loaded per tile, while both gate and up
        weights are fetched together.
        """
        K_tiles = math.ceil(K / self.H)
        N_tiles = math.ceil(N / self.W)
        total_tiles = K_tiles * N_tiles

        tile_weight_bytes = math.ceil(self.H * self.W * self.w_bits / 8)
        tile_act_bytes = math.ceil(M * self.H * self.a_bits / 8)

        # With weight cache: load both gate and up weights, but one activation.
        dual_weight_bytes = 2 * tile_weight_bytes
        per_tile_dma = (dual_weight_bytes + tile_act_bytes) / self.eff_bw

        # Compute gate then up sequentially on the same PE array.
        per_tile_compute = 2 * (BROADCAST_SYNC_CYCLES +
                                _accumulate_cycles(self.w_bits, self.a_bits))

        # Double-buffering overlap, same shape as single estimate.
        bottleneck = max(per_tile_compute, per_tile_dma)
        first_cold = per_tile_dma + per_tile_compute

        if total_tiles > 1:
            total = int(first_cold + (total_tiles - 1) * bottleneck)
        else:
            total = int(first_cold)

        total_macs = M * K * N * 2
        total_weight_bytes = total_tiles * (dual_weight_bytes + tile_act_bytes)
        ideal = math.ceil(total_macs / self.peak_macs_per_cycle)
        util = ideal / total if total > 0 else 0.0

        # Activation re-broadcast savings vs two separate estimates.
        activation_savings = total_tiles * tile_act_bytes / self.eff_bw

        return EngineResult(
            compute_cycles=int(per_tile_compute * total_tiles),
            dma_cycles=int(total - per_tile_compute * total_tiles),
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
                "weight_cache_savings": int(activation_savings),
            },
        )
