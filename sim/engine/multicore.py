"""多核时间轴引擎 — 核间 FIFO + Crossbar 竞争

支持三种模式:
- independent: N 核各自处理不同 token（数据并行）
- pipeline: 核间 FIFO 流水线（层间流水线并行）
- shared_l2: 共享 L2 SRAM，Crossbar 仲裁
"""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from engine.timeline import CoreTimeline, TimelineEvent, breakdown_events


@dataclass
class FIFOConfig:
    """核间 FIFO 配置"""
    size_bytes: int = 4096        # 4KB per direction
    width_bits: int = 256         # bits per cycle
    latency_cycles: int = 2       # fixed pipeline delay
    bidirectional: bool = True    # 双向（上下游均可传）


@dataclass
class CrossbarConfig:
    """Crossbar / NoC 配置"""
    num_ports: int = 4
    bandwidth_gbps: float = 500.0  # per port
    arbitration_cycles: int = 3     # fixed arbitration overhead


class MultiCoreTimeline:
    """N 核时间轴，管理核间交互。

    每个核有自己的 CoreTimeline，额外追踪:
    - FIFO 传输延迟（激活值传递）
    - Crossbar 仲裁延迟（共享资源竞争）
    """

    def __init__(self, num_cores: int, fifo: FIFOConfig = None,
                 crossbar: CrossbarConfig = None):
        self.num_cores = num_cores
        self.cores = [CoreTimeline(i) for i in range(num_cores)]
        self.fifo = fifo or FIFOConfig()
        self.xbar = crossbar or CrossbarConfig()

        # Crossbar port allocation tracking
        self._xbar_ports_used: Dict[int, int] = {}  # core_id → ports used
        for i in range(num_cores):
            self._xbar_ports_used[i] = 0

    # ── FIFO 延迟计算 ───────────────────────────────────────────

    def fifo_transfer_cycles(self, num_elements: int, element_bytes: int = 2) -> int:
        """计算通过 FIFO 传输 num_elements 的延迟。

        Args:
            num_elements: BF16 元素数量（激活值）
            element_bytes: 每元素字节数（BF16=2）
        """
        bits_per_cycle = self.fifo.width_bits
        bytes_per_cycle = bits_per_cycle // 8  # 32
        total_bytes = num_elements * element_bytes
        transfer_cycles = math.ceil(total_bytes / bytes_per_cycle)
        return transfer_cycles + self.fifo.latency_cycles

    # ── Crossbar 竞争 ────────────────────────────────────────────

    def xbar_access_cycles(self, core_id: int, data_bytes: int) -> int:
        """计算 core_id 通过 Crossbar 访问共享资源的额外延迟。

        简化为: 固定仲裁延迟 + 按端口带宽的传输时间。
        多核同时访问时增加竞争延迟。
        """
        bw_bytes_per_cycle = self.xbar.bandwidth_gbps  # 500 GB/s ≡ 500 B/cycle @1GHz
        transfer_cycles = math.ceil(data_bytes / bw_bytes_per_cycle)

        # 竞争: 同时访问的核数越多，仲裁延迟越大
        active_cores = sum(1 for p in self._xbar_ports_used.values() if p > 0)
        contention_factor = max(1, active_cores * 0.1)  # 10% per active core

        return int(transfer_cycles + self.xbar.arbitration_cycles * contention_factor)

    # ── 工作模式 ─────────────────────────────────────────────────

    def simulate_pipeline(self,
                          layer_assignments: List[List[int]],
                          per_layer_cycles: List[int],
                          activation_size: int = 2560) -> Dict:
        """流水线并行: 核心 N 处理 Layer N，核间 FIFO 传递激活。

        Args:
            layer_assignments: [[0,1,...], [14,15,...]] — 每核负责的层
            per_layer_cycles: 每层需要的 MXU cycles
            activation_size: 层间传递的激活值元素数

        Returns:
            {core_id: total_cycles, fifo_overhead: cycles, ...}
        """
        total_cycles = 0
        core_progress = [0] * self.num_cores  # 每核当前已分配 cycles
        fifo_overhead = 0

        # Simplified: layers form a pipeline, FIFO after each layer
        num_layers = sum(len(la) for la in layer_assignments)
        for layer_idx in range(num_layers):
            core_id = layer_idx % self.num_cores
            cycles = (per_layer_cycles[layer_idx]
                      if layer_idx < len(per_layer_cycles) else 1000)

            # Execute on this core
            core_progress[core_id] += cycles

            # FIFO: if next layer is on different core, transfer activation
            next_core = (layer_idx + 1) % self.num_cores
            if next_core != core_id and layer_idx < num_layers - 1:
                fifo_cycles = self.fifo_transfer_cycles(activation_size)
                fifo_overhead += fifo_cycles

        # Total = max(core_progress) + fifo overhead (partial overlap)
        max_core = max(core_progress) if core_progress else 0
        # FIFO overhead is partially hidden behind compute
        effective_fifo = int(fifo_overhead * 0.3)  # 70% hidden
        total_cycles = max_core + effective_fifo

        return {
            "total_cycles": total_cycles,
            "core_max": max_core,
            "fifo_overhead": fifo_overhead,
            "fifo_effective": effective_fifo,
            "core_progress": core_progress,
        }

    def simulate_data_parallel(self, per_token_cycles: int,
                               num_tokens: int) -> Dict:
        """数据并行: 每核处理不同 token，吞吐 ×N。

        Since each core works independently, total tokens processed
        in the same wall-clock time = num_cores × single-core throughput.
        Crossbar contention slightly reduces efficiency.
        """
        base_tok_per_s = 1e6 / per_token_cycles if per_token_cycles > 0 else 0

        # Crossbar contention: shared LPDDR5 bandwidth split
        # N cores sharing one memory channel reduces effective BW per core
        bw_per_core = 51.2 / self.num_cores  # GB/s per core

        # Efficiency loss from contention
        contention_penalty = 1.0 - (self.num_cores - 1) * 0.05  # -5% per extra core
        contention_penalty = max(0.5, contention_penalty)  # floor at 50%

        effective_tok_per_s = base_tok_per_s * self.num_cores * contention_penalty

        return {
            "base_tok_per_s": base_tok_per_s,
            "num_cores": self.num_cores,
            "effective_tok_per_s": effective_tok_per_s,
            "contention_penalty": contention_penalty,
            "bw_per_core_gbps": bw_per_core,
        }
