"""MXU 解析性能模型 — 128×128 Weight-Stationary Systolic Array"""

import math
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class MXUResult:
    compute_cycles: int
    stall_cycles_dram: int
    stall_cycles_sram: int
    total_cycles: int
    utilization: float
    ops: int  # total MAC operations

    def __repr__(self):
        return (f"MXU(compute={self.compute_cycles}, stall_dram={self.stall_cycles_dram}, "
                f"stall_sram={self.stall_cycles_sram}, util={self.utilization:.1%})")


class MXUModel:
    """Weight-stationary systolic array analytical model.

    TPUv1-style: weights preloaded diagonally, activations flow left→right,
    partial sums accumulate downward. INT4 weights × INT8 activations → INT32.

    Reference:
    - Jouppi et al., "In-Datacenter Performance Analysis of a Tensor Processing Unit", ISCA 2017
    - OpenTPU, UCSB ArchLab
    """

    def __init__(self, config: Dict[str, Any]):
        mxu = config["mxu"]
        self.H = int(mxu["array_height"])      # 128
        self.W = int(mxu["array_width"])       # 128
        self.f_mhz = int(mxu["frequency_mhz"]) # 1000
        self.w_bits = int(mxu["weight_precision_bits"])    # 4
        self.a_bits = int(mxu["activation_precision_bits"]) # 8
        self.ops_per_mac = int(mxu["ops_per_mac"])         # 2
        self.double_buffer = bool(mxu.get("double_buffer", True))

        mem = config["memory"]
        self.bw_bytes_per_cycle = float(mem["bandwidth_bytes_per_cycle"])
        self.refresh_pct = float(mem.get("refresh_overhead_percent", 3.0)) / 100.0

    def estimate(self, M: int, K: int, N: int, weight_preloaded: bool = False) -> MXUResult:
        """Estimate cycles for GEMM: (M×K) × (K×N) → (M×N).

        Args:
            M: output rows (1 for decode, prompt_len for prefill)
            K: inner dimension
            N: output columns
            weight_preloaded: True if weights already in SRAM (decode after 1st token)
        """
        total_macs = M * K * N

        macs_per_cycle = self.H * self.W * self.ops_per_mac  # 128*128*2 = 32768
        compute_cycles = math.ceil(total_macs / macs_per_cycle)

        pipeline_fill = self.H + self.W  # 256 cycles
        pipeline_drain = self.H + M  # partial sum drain
        compute_cycles += pipeline_fill + pipeline_drain

        # Weight load: K*N elements × w_bits/8 bytes
        # Only count if weights need to come from DRAM
        if not weight_preloaded:
            weight_bytes = math.ceil(K * N * self.w_bits / 8)
            weight_load_cycles = weight_bytes / self.bw_bytes_per_cycle
        else:
            weight_load_cycles = 0  # Already in SRAM

        # Activation load: M*K elements × a_bits/8 bytes
        activation_bytes = math.ceil(M * K * self.a_bits / 8)
        activation_load_cycles = activation_bytes / self.bw_bytes_per_cycle

        # Stalls
        stall_dram = max(0, weight_load_cycles - compute_cycles)
        stall_dram += max(0, activation_load_cycles - compute_cycles * 0.3)

        stall_sram = 0
        refresh_cycles = compute_cycles * self.refresh_pct

        total_cycles = int(compute_cycles + stall_dram + stall_sram + refresh_cycles)

        ideal_cycles = math.ceil(total_macs / macs_per_cycle)
        utilization = ideal_cycles / total_cycles if total_cycles > 0 else 0.0

        return MXUResult(
            compute_cycles=int(compute_cycles),
            stall_cycles_dram=int(stall_dram),
            stall_cycles_sram=int(stall_sram),
            total_cycles=total_cycles,
            utilization=utilization,
            ops=total_macs,
        )
