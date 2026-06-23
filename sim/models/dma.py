"""DMA 带宽模型 — dual-channel descriptor-based DMA engine"""

import math
from typing import Any, Dict, Tuple


class DMAModel:
    """DMA engine with two channels, burst transfers, descriptor chains.

    Models: LPDDR5 ↔ L2 SRAM data movement.
    Key insight: DMA loads next layer's weights while MXU computes current layer.
    """

    def __init__(self, config: Dict[str, Any]):
        dma = config["dma"]
        self.channels = int(dma["channels"])
        self.burst_size = int(dma["burst_size_bytes"])          # 256
        self.descriptor_overhead = int(dma["descriptor_overhead_cycles"])  # 5
        self.max_pending = int(dma.get("max_pending_descriptors", 16))

        # DW_axi_dmac-spec configurable parameters (v0.4)
        self.num_channels = int(dma.get("num_channels", 2))
        self.fifo_depth = int(dma.get("per_channel_fifo_depth", 64))
        self.max_burst_length = int(dma.get("max_burst_length", 8))
        self.multi_block_mode = str(dma.get("multi_block_mode", "linked_list"))
        self.ll_prefetch_en = bool(dma.get("ll_prefetch_en", True))

        mem = config["memory"]
        self.bw_bytes_per_cycle = float(mem["bandwidth_bytes_per_cycle"])  # 51.2

    def estimate_transfer(self, size_bytes: int, direction: str = "load") -> int:
        """Estimate cycles for a single DMA transfer.

        direction: 'load' (DRAM→SRAM) or 'store' (SRAM→DRAM)

        Returns total cycles including descriptor overhead.
        """
        if size_bytes <= 0:
            return 0

        # Number of bursts
        num_bursts = math.ceil(size_bytes / self.burst_size)

        # Transfer time: bytes / bandwidth
        transfer_cycles = size_bytes / self.bw_bytes_per_cycle

        # Burst overhead: one cycle per burst for address handshake
        burst_overhead = num_bursts

        total = (self.descriptor_overhead + transfer_cycles + burst_overhead)
        return int(math.ceil(total))

    def estimate_weight_load(self, K: int, N: int, weight_bits: int = 4) -> int:
        """Estimate cycles to load weight matrix (K×N) from DRAM to SRAM.

        This is the dominant DMA operation — streaming weights into the
        weight-stationary systolic array.
        """
        size_bytes = math.ceil(K * N * weight_bits / 8)
        return self.estimate_transfer(size_bytes, "load")

    def allocate_channel(self, request_type: str) -> int:
        """Map a DMA request type to a channel index.

        request_type: 'weight_load', 'kv_access', 'output_store'
        Returns channel index in [0, num_channels).
        """
        mapping = {"weight_load": 0, "kv_access": 1, "output_store": 2}
        base = mapping.get(request_type, 0)
        return base % self.num_channels

    def estimate_effective(self, transfer_cycles: int,
                           compute_cycles: int) -> Tuple[int, int]:
        """Calculate effective (non-overlapped) DMA cycles.

        Returns (effective_cycles, hidden_cycles).
        effective = DMA cycles that block (couldn't overlap with compute)
        hidden = DMA cycles hidden behind compute
        """
        hidden = min(transfer_cycles, compute_cycles)
        effective = max(0, transfer_cycles - compute_cycles)
        return effective, hidden
