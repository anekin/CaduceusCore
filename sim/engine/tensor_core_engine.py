"""Tensor Core 风格引擎 — 多小块 Block Engine 并行

参考: NVIDIA Tensor Core (16×16×16), Google TPUv4 (128×128)

NVIDIA 做法: 每个 SM 内多个 16×16 TC，warp 级调度隐藏延迟。
TPUv4 做法: 单个大 128×128 MXU，编译器做 tiling。

对 M=1 decode:
  - 小块 (16×16): tile 数多 → DMA 启动开销大 → 效率低
  - 大块 (128×128): tile 数少 → DMA 启动少 → 效率高
  - 所以 Tensor Core 风格在 M=1 下不如大块 Block Engine

本引擎建模: N 个独立的 TC 块，每个 16×16，并行执行。
总面积 MAC 数 = N × 16 × 16 = 保持与 systolic 128×128 一致。
"""

import math
from typing import Any, Dict
from engine.mac_engine import MACEngine, EngineResult


class TensorCoreEngine(MACEngine):
    """Multi-TC engine — many small block engines in parallel.

    Equivalent to: (128/16)² = 64 independent 16×16 TCs.
    Each TC processes a 16×16 output tile per K-step.

    Key distinction from monolithic Block Engine:
      - 64× more tiles → 64× more DMA initiations
      - Same total weight data, but fragmented into smaller bursts
      - DMA efficiency drops due to smaller burst sizes
    """

    TC_SIZE = 16  # fixed for Tensor Core modeling

    @property
    def engine_type(self) -> str:
        return "tensor_core"


    def estimate(self, M: int, K: int, N: int,
                 weight_preloaded: bool = False) -> EngineResult:
        """TC-style GEMM estimate.

        Workload partitioning:
          - Output (M,N) partitioned across TCs
          - M dimension: distributed across TC rows
          - N dimension: distributed across TC columns
          - K dimension: processed sequentially (each TC does its K portion)

        Each TC invocation: process (TC_SIZE, TC_SIZE, TC_SIZE) matmul.
        """
        # How many TCs per output element?
        # Output grid: (M, N) partitioned by (TC_SIZE, TC_SIZE)
        M_blocks = math.ceil(M / self.TC_SIZE)   # output M blocks
        N_blocks = math.ceil(N / self.TC_SIZE)   # output N blocks
        K_steps = math.ceil(K / self.TC_SIZE)    # K sequential steps

        total_invocations = M_blocks * N_blocks * K_steps

        # Per TC invocation:
        # Weight: TC_SIZE² × 4bit/8 = 128 bytes
        # Activation: M_tc × TC_SIZE × 8bit/8
        M_tc = min(M, self.TC_SIZE)
        tile_weight = math.ceil(self.TC_SIZE * self.TC_SIZE * self.w_bits / 8)
        tile_act = math.ceil(M_tc * self.TC_SIZE * self.a_bits / 8)
        
        # TC-level parallelism: we have (H*W)/(16*16) = 64 TCs
        num_tcs = (self.H * self.W) // (self.TC_SIZE * self.TC_SIZE)
        
        # per WAVE: all num_tcs TCs fire in parallel, share DRAM bus
        per_wave_dma = (tile_weight + tile_act) * num_tcs / self.eff_bw
        per_wave_compute = 1.0  # all TCs fire in single cycle
        # Total invocations distributed across TCs
        parallel_invocations = math.ceil(total_invocations / num_tcs)

        # Each wave: all TCs fire, then next wave
        bottleneck = max(per_wave_compute, per_wave_dma)
        first_cold = per_wave_dma + per_wave_compute

        # With 64-way parallel TCs and double buffering per TC
        if parallel_invocations > 1:
            total = int(first_cold + (parallel_invocations - 1) * bottleneck)
        else:
            total = int(first_cold)

        total_macs = M * K * N
        total_weight_bytes = total_invocations * (tile_weight + tile_act)
        ideal = math.ceil(total_macs / self.peak_macs_per_cycle)
        util = ideal / total if total > 0 else 0.0

        return EngineResult(
            compute_cycles=parallel_invocations,
            dma_cycles=int(total - parallel_invocations),
            total_cycles=total,
            utilization=util,
            ops=total_macs,
            num_tiles=parallel_invocations,
            weight_bytes=total_weight_bytes,
            bottleneck="compute" if per_wave_compute > per_wave_dma else "dma",
            details={
                "total_invocations": total_invocations,
                "num_tcs": num_tcs,
                "parallel_waves": parallel_invocations,
                "per_wave_dma": round(per_wave_dma, 1),
                "per_wave_compute": per_wave_compute,
                "tc_size": self.TC_SIZE,
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
        )
