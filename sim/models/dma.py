"""DMA bandwidth model — multi-channel descriptor-based DMA engine"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

# Priority lookup for fixed_priority arbitration: higher value = higher priority.
_REQUEST_TYPE_PRIORITY: Dict[str, int] = {
    "weight_load": 2,
    "kv_access": 1,
    "output_store": 0,
}


@dataclass
class DMARequest:
    """A single DMA transfer request queued on a channel.

    Attributes:
        request_type: Logical category (weight_load, kv_access, output_store).
        size_bytes: Total payload bytes to transfer.
        direction: 'load' (DRAM→SRAM) or 'store' (SRAM→DRAM).
        block_count: Number of contiguous/scatter-gather blocks (default 1).
        priority: Arbitration priority (default 0, higher = more urgent).
    """
    request_type: str
    size_bytes: int
    direction: str
    block_count: int = 1
    priority: int = 0


class DMAModel:
    """DMA engine with configurable channels, arbitration, and burst transfers.

    Models: LPDDR5 ↔ L2 SRAM data movement.
    Key insight: DMA loads next layer's weights while MXU computes current layer.

    Arbitration modes:
        round_robin (default) — FIFO order on each channel.
        fixed_priority — sort requests by type priority:
            weight_load (2) > kv_access (1) > output_store (0).
    """

    def __init__(self, config: Dict[str, Any]):
        dma = config["dma"]
        self.burst_size = int(dma["burst_size_bytes"])          # 256
        self.descriptor_overhead = int(dma["descriptor_overhead_cycles"])  # 5
        self.max_pending = int(dma.get("max_pending_descriptors", 16))

        # DW_axi_dmac-spec configurable parameters (v0.4)
        self.num_channels = int(dma.get("num_channels", 2))
        self.fifo_depth = int(dma.get("per_channel_fifo_depth", 64))
        self.max_burst_length = int(dma.get("max_burst_length", 8))
        self.multi_block_mode = str(dma.get("multi_block_mode", "linked_list"))
        self.ll_prefetch_en = bool(dma.get("ll_prefetch_en", True))

        # Arbitration policy (v0.5)
        self.arbitration = str(dma.get("arbitration", "round_robin"))

        # Per-channel request queues for FIFO backpressure modelling
        self.channel_queues: List[List[DMARequest]] = [
            [] for _ in range(self.num_channels)
        ]

        mem = config["memory"]
        self.bw_bytes_per_cycle = float(mem["bandwidth_bytes_per_cycle"])  # 51.2

    def estimate_transfer(self, size_bytes: int, direction: str = "load") -> int:
        """Estimate cycles for a single DMA transfer.

        direction: 'load' (DRAM→SRAM) or 'store' (SRAM→DRAM)

        Returns total cycles including descriptor overhead.
        Uses math.ceil per T1 conventions. For sub-burst-cycle transfers
        (transfer_cycles < 1.0), rounds down per spec rationale:
        "sub-byte transfer rounds down to min 1 burst."
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

        # Sub-burst-cycle transfers: spec rounds down (floor) instead of ceil.
        # Only applies when the entire transfer fits in less than 1 BW-cycle.
        if transfer_cycles < 1.0:
            return int(total)

        return int(math.ceil(total))

    def estimate_weight_load(self, K: int, N: int, weight_bits: int = 4) -> int:
        """Estimate cycles to load weight matrix (K×N) from DRAM to SRAM.

        Internally enqueues a DMARequest, estimates via channel queues,
        then dequeues so callers see an unchanged queue state.
        """
        size_bytes = math.ceil(K * N * weight_bits / 8)
        request = DMARequest(
            request_type="weight_load",
            size_bytes=size_bytes,
            direction="load",
            block_count=1,
        )
        self.enqueue(request)
        cycles = self.estimate_total_cycles()
        # Dequeue: remove the request we just added (FIFO order)
        ch = self.allocate_channel("weight_load")
        self.channel_queues[ch].pop()
        return cycles

    def allocate_channel(self, request_type: str) -> int:
        """Map a DMA request type to a channel index.

        request_type: 'weight_load', 'kv_access', 'output_store'
        Returns channel index in [0, num_channels).
        """
        mapping = {"weight_load": 0, "kv_access": 1, "output_store": 2}
        base = mapping.get(request_type, 0)
        return base % self.num_channels

    def enqueue(self, request: DMARequest) -> int:
        """Enqueue a DMA request onto the appropriate channel queue.

        In round_robin mode, appends to the channel FIFO.
        In fixed_priority mode, inserts in priority order (highest first).

        Returns the channel index assigned via allocate_channel().
        """
        ch = self.allocate_channel(request.request_type)
        if self.arbitration == "fixed_priority":
            # Insert at correct priority position (highest first)
            queue = self.channel_queues[ch]
            req_prio = _REQUEST_TYPE_PRIORITY.get(request.request_type, 0)
            insert_at = 0
            for i, existing in enumerate(queue):
                existing_prio = _REQUEST_TYPE_PRIORITY.get(existing.request_type, 0)
                if req_prio > existing_prio:
                    insert_at = i
                    break
                insert_at = i + 1
            queue.insert(insert_at, request)
        else:
            self.channel_queues[ch].append(request)
        return ch

    def estimate_channel_cycles(self, channel_idx: int) -> int:
        """Estimate total cycles for all queued requests on one channel.

        Order follows the configured arbitration policy set during enqueue:
        round_robin → FIFO, fixed_priority → sorted by request-type priority.

        Components:
        - Per-transfer estimate_transfer() cycles for each request.
        - Multi-block overhead: block_count * descriptor_overhead.
        - Linked-list pointer fetch: block_count * 2 (halved if ll_prefetch_en).
        - FIFO backpressure: stall cycles when queued bytes exceed
          fifo_capacity = per_channel_fifo_depth * burst_size.
        """
        queue = self.channel_queues[channel_idx]
        if not queue:
            return 0

        total_cycles = 0
        total_queued_bytes = 0

        for req in queue:
            # Base transfer cycles
            transfer = self.estimate_transfer(req.size_bytes, req.direction)
            total_cycles += transfer

            # Multi-block descriptor overhead
            total_cycles += req.block_count * self.descriptor_overhead

            # Linked-list pointer fetch overhead
            if self.multi_block_mode == "linked_list":
                ll_cycles = req.block_count * 2
                if self.ll_prefetch_en:
                    ll_cycles = max(1, ll_cycles // 2)
                total_cycles += ll_cycles

            total_queued_bytes += req.size_bytes

        # FIFO backpressure: stall when queued data exceeds FIFO capacity
        fifo_capacity = self.fifo_depth * self.burst_size
        if total_queued_bytes > fifo_capacity:
            excess_bytes = total_queued_bytes - fifo_capacity
            stall_cycles = int(math.ceil(excess_bytes / self.burst_size))
            total_cycles += stall_cycles

        return total_cycles

    def estimate_total_cycles(self) -> int:
        """Estimate total DMA cycles across all channels.

        Since DMA channels operate in parallel, the total time is bounded
        by the busiest channel (conservative exposed stall).
        """
        if self.num_channels == 0:
            return 0
        return max(
            self.estimate_channel_cycles(ch) for ch in range(self.num_channels)
        )

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

    def estimate_tile_double_buffer_overlap(
        self,
        M: int, K: int, N: int,
        tile_H: int, tile_W: int,
        weight_bits: int, act_bits: int,
        per_tile_compute_cycles: int,
        double_buffer: bool = True,
    ) -> float:
        """Compute weight_streaming_overlap_ratio via tile-level double-buffering.

        Models tile-by-tile weight streaming with a 2-entry double-buffer for
        weight registers in each PE.  For each K-tile group across the N
        dimension, the first N-tile pays a cold-start cost (DMA + compute
        sequential); subsequent N-tiles overlap DMA for the next tile with
        compute of the current tile.  K-tile reloads incur a possible stall
        when the activation DMA plus first weight tile DMA for the next K-tile
        cannot be hidden behind the compute of the last N-tile of the previous
        K-tile.

        Parameters
        ----------
        M: Batch / token count (for decode M=1).
        K: Reduction dimension.
        N: Output dimension.
        tile_H: Array height (K-tile granularity).
        tile_W: Array width (N-tile granularity).
        weight_bits: Weight precision (e.g. 4 for INT4).
        act_bits: Activation precision (e.g. 8 for INT8).
        per_tile_compute_cycles: Compute cycles per tile (broadcast + MAC +
            accumulate).
        double_buffer: MXU weight-buffer ping-pong capability (mirrors the
            ``mxu.double_buffer`` config knob).  When False the weight SRAM is
            single-buffered and the controller FSM serializes
            LOAD_W/LOAD_A/COMPUTE/STORE_OUT per tile — every tile's DMA is
            fully exposed and the overlap ratio is 0.0.  Calibrated from the
            FM-3 RTL measurement (todo 16: overlap_ratio=0.00).

        Returns
        -------
        overlap_ratio : float in [0, 1]
            Fraction of total weight DMA hidden behind compute.  Returns 0.0
            when total DMA is zero (sentinel for unsupported / degenerate)
            and when double-buffering is disabled.

        Notes
        -----
        Cross-validation against W4 PERF-09..P12 VCS data is deferred.
        The representative PERF-09..P12 config uses Qwen2.5-3B Q_proj with
        K_in=2560, N_out=4096.
        """
        import math

        if M <= 0 or K <= 0 or N <= 0:
            return 0.0

        if not double_buffer:
            # Single-buffered weight SRAM: DMA and compute are fully
            # sequential per tile, so no DMA can be hidden behind compute.
            return 0.0

        K_tiles = math.ceil(K / max(tile_H, 1))
        N_tiles = math.ceil(N / max(tile_W, 1))

        if K_tiles <= 0 or N_tiles <= 0:
            return 0.0

        # Per-tile data volumes (bytes)
        tile_weight_bytes = math.ceil(tile_H * tile_W * weight_bits / 8)
        tile_act_bytes = math.ceil(tile_H * act_bits / 8)

        # Per-tile DMA cycles (weight + activation)
        per_tile_weight_dma = tile_weight_bytes / self.bw_bytes_per_cycle
        per_tile_act_dma = tile_act_bytes / self.bw_bytes_per_cycle

        # --- Without tile-level double-buffering: total DMA cycles ---
        # Weights loaded once per K-tile, activations once per K-tile.
        total_weight_dma = K_tiles * N_tiles * per_tile_weight_dma
        total_act_dma = K_tiles * per_tile_act_dma
        total_dma_all = M * (total_weight_dma + total_act_dma)

        # --- With tile-level double-buffering ---
        # For each K-tile, the first N-tile is cold (no preloaded buffer).
        # Subsequent N-tiles overlap DMA_next with compute_current.
        # Within a K-tile, activation is loaded once (shared across N-tiles).

        cold_first_tile = per_tile_weight_dma + per_tile_act_dma + per_tile_compute_cycles
        bottleneck_within_ktile = max(per_tile_weight_dma, per_tile_compute_cycles)

        dma_on_critical_path = 0.0

        for _kt in range(K_tiles):
            if N_tiles == 1:
                # Single N-tile: no overlap possible
                dma_on_critical_path += per_tile_weight_dma + per_tile_act_dma
            else:
                # First N-tile: weight DMA + act DMA are both on the path (cold)
                dma_on_critical_path += per_tile_weight_dma + per_tile_act_dma
                # Remaining N_tiles-1 within this K-tile:
                # DMA_next runs in parallel with compute_current via double-buffer.
                # Only the portion of DMA that exceeds compute leaks onto the
                # critical path.
                excess = max(0.0, per_tile_weight_dma - per_tile_compute_cycles)
                dma_on_critical_path += (N_tiles - 1) * excess

            # --- K-tile reload stall (between K-tiles) ---
            # When transitioning from K-tile k to k+1, the double-buffer has just
            # finished loading the last N-tile's weights of K-tile k.  The next
            # DMA burst (activation + first weight tile of K-tile k+1) can only
            # begin after compute of K-tile k's last N-tile starts (buffer
            # switch).  If the remaining compute time is shorter than the DMA
            # burst, the difference stalls.
            if _kt < K_tiles - 1:
                # DMA needed for next K-tile: activation + first weight tile
                dma_next_ktile = per_tile_act_dma + per_tile_weight_dma
                # Remaining compute after buffer switch for last N-tile of
                # current K-tile: per_tile_compute_cycles (the switch happens
                # right when compute for the last tile starts).
                remaining_compute = per_tile_compute_cycles
                ktile_reload_stall = max(0.0, dma_next_ktile - remaining_compute)
                dma_on_critical_path += min(ktile_reload_stall, dma_next_ktile)

        # For M>1 (prefill): each additional token incurs its own activation DMA
        # but can overlap with weight DMA if M-tiles share the same K-tile streaming.
        # Simplified: add per-token activation DMA on critical path for M>1.
        if M > 1:
            extra_act_dma = (M - 1) * K_tiles * per_tile_act_dma
            dma_on_critical_path += extra_act_dma

        total_dma_all_scaled = M * (total_weight_dma + total_act_dma)

        if total_dma_all_scaled <= 0:
            return 0.0

        overlap_ratio = max(0.0, min(1.0,
            1.0 - dma_on_critical_path / total_dma_all_scaled))
        return float(overlap_ratio)
