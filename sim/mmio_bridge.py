"""
MMIO Bridge — intercepts RISC-V load/store to NPU register space,
routing to GoldenMXU/SFU/Vector/DMA simulators.

Used by both RISCVMini (Python emulator) and Spike (when available).
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import numpy as np

from sim.regmap import Addr, MXU, SFU, VECTOR, DMA, DOORBELL, INTC


@dataclass
class MMIOBridge:
    """Route MMIO reads/writes to hardware module simulators."""

    modules: Dict[str, Any] = field(default_factory=dict)
    # modules['mxu'], ['sfu'], ['vector'], ['dma'], ['dram'], ['sram']

    def __post_init__(self):
        self._status: Dict[int, int] = {}   # addr → value for status registers
        self._trace: list = []               # MMIO access trace
        self.tracer = None                   # AXITracer, set externally

    def handle(self, rw: str, addr: int, value: int = 0) -> int:
        """Handle MMIO access. 'read' → returns value. 'write' → updates state."""
        self._trace.append(f"{'R' if rw == 'read' else 'W'} {addr:08X} = {value:08X}")
        if self.tracer:
            self.tracer.record(rw, addr, value, self.tracer.classify_addr(addr))
        base = addr & 0xFFFFF000

        if base == MXU.BASE:
            return self._handle_mxu(rw, addr, value)
        elif base == SFU.BASE:
            return self._handle_sfu(rw, addr, value)
        elif base == VECTOR.BASE:
            return self._handle_vector(rw, addr, value)
        elif base == DMA.BASE:
            return self._handle_dma(rw, addr, value)
        elif base == DOORBELL.BASE:
            return self._handle_doorbell(rw, addr, value)
        elif base == INTC.BASE:
            return self._handle_intc(rw, addr, value)

        return 0

    # ── MXU ─────────────────────────────────────────────────────────

    def _handle_mxu(self, rw: str, addr: int, value: int) -> int:
        off = addr - MXU.BASE
        mxu = self.modules.get('mxu')
        if not mxu:
            return 0

        if rw == 'write':
            if off == MXU.CTRL:
                self._status[MXU.BASE + MXU.CTRL] = value  # CTRL stored

            elif off == MXU.CMD and (value & 1):  # START
                self._status[MXU.BASE + MXU.STATUS] = 1  # BUSY
                ctrl = self._status.get(MXU.BASE + MXU.CTRL, 0)
                accumulate = bool(ctrl & 4)  # bit[2] = ACCUMULATE

                M = (self._status.get(MXU.BASE + MXU.DIM0, 0)) & 0xFFFF
                K = (self._status.get(MXU.BASE + MXU.DIM0, 0) >> 16) & 0xFFFF
                N = self._status.get(MXU.BASE + MXU.DIM1, 0) & 0xFFFF
                i_addr = self._status.get(MXU.BASE + MXU.I_ADDR, 0)
                w_addr = self._status.get(MXU.BASE + MXU.W_ADDR, 0)
                o_addr = self._status.get(MXU.BASE + MXU.O_ADDR, 0)
                s_addr = self._status.get(MXU.BASE + MXU.SCALE_ADDR, 0)
                sram = self.modules.get('sram', bytearray())

                if sram and M > 0 and K > 0 and N > 0:
                    act_bytes = M * K
                    act = np.frombuffer(sram[i_addr:i_addr + act_bytes], dtype=np.int8).reshape(M, K)
                    wgt_packed_bytes = (K * N + 1) // 2
                    wgt_packed = np.frombuffer(sram[w_addr:w_addr + wgt_packed_bytes], dtype=np.uint8)

                    if s_addr > 0:
                        num_blocks = (K + 127) // 128
                        scale_bytes = num_blocks * N * 4
                        scales = np.frombuffer(sram[s_addr:s_addr + scale_bytes],
                                               dtype=np.float32).reshape(num_blocks, N)
                        result = mxu.matmul_int4_per_block(act, wgt_packed, scales,
                                                           M, K, N, group_size=128)
                        result_bytes = result.astype(np.float32).tobytes()
                        dtype_out = np.float32
                    else:
                        result = mxu.matmul_int32(act, wgt_packed, M, K, N)
                        result_bytes = result.astype(np.int32).tobytes()
                        dtype_out = np.int32

                    # Accumulate mode: add to existing output
                    if accumulate:
                        existing = np.frombuffer(sram[o_addr:o_addr + len(result_bytes)],
                                                 dtype=dtype_out).reshape(M, N)
                        result = existing + result
                        result_bytes = result.astype(dtype_out).tobytes()

                    sram[o_addr:o_addr + len(result_bytes)] = result_bytes

                self._status[MXU.BASE + MXU.STATUS] = 2  # DONE
                if self._status.get(MXU.BASE + MXU.IRQ_EN, 0) & 1:
                    self._set_irq(0)

            else:
                self._status[addr & 0xFFFFFFFC] = value

        elif rw == 'read':
            return self._status.get(addr & 0xFFFFFFFC, 0)
        return 0

    # ── SFU ─────────────────────────────────────────────────────────

    def _handle_sfu(self, rw: str, addr: int, value: int) -> int:
        off = addr - SFU.BASE
        sfu = self.modules.get('sfu')

        if rw == 'write':
            if off == SFU.CMD and (value & 1):
                self._status[SFU.BASE + SFU.STATUS] = 1
                # (simplified — full dispatch in firmware loop)
                self._status[SFU.BASE + SFU.STATUS] = 2
            else:
                self._status[addr & 0xFFFFFFFC] = value
        elif rw == 'read':
            return self._status.get(addr & 0xFFFFFFFC, 0)
        return 0

    # ── VECTOR ──────────────────────────────────────────────────────

    def _handle_vector(self, rw: str, addr: int, value: int) -> int:
        off = addr - VECTOR.BASE

        if rw == 'write':
            if off == VECTOR.CMD and (value & 1):
                self._status[VECTOR.BASE + VECTOR.STATUS] = 1
                self._status[VECTOR.BASE + VECTOR.STATUS] = 2
            else:
                self._status[addr & 0xFFFFFFFC] = value
        elif rw == 'read':
            return self._status.get(addr & 0xFFFFFFFC, 0)
        return 0

    # ── DMA ─────────────────────────────────────────────────────────

    def _handle_dma(self, rw: str, addr: int, value: int) -> int:
        off = addr - DMA.BASE

        if rw == 'write':
            if off == DMA.CMD and (value & 1):
                self._status[DMA.BASE + DMA.STATUS] = 1

                # Channel 0: DRAM → SRAM
                ch0_src = self._status.get(DMA.BASE + DMA.CH0_SRC, 0)
                ch0_dst = self._status.get(DMA.BASE + DMA.CH0_DST, 0)
                ch0_size = self._status.get(DMA.BASE + DMA.CH0_SIZE, 0)
                if ch0_size > 0:
                    src_mem = self._get_mem(ch0_src)
                    dst_mem = self._get_mem(ch0_dst)
                    src_off = self._translate_addr(ch0_src)
                    dst_off = self._translate_addr(ch0_dst)
                    if src_mem is not None and dst_mem is not None:
                        dst_mem[dst_off:dst_off + ch0_size] = \
                            src_mem[src_off:src_off + ch0_size]

                # Channel 1: SRAM → DRAM
                ch1_src = self._status.get(DMA.BASE + DMA.CH1_SRC, 0)
                ch1_dst = self._status.get(DMA.BASE + DMA.CH1_DST, 0)
                ch1_size = self._status.get(DMA.BASE + DMA.CH1_SIZE, 0)
                if ch1_size > 0:
                    src_mem = self._get_mem(ch1_src)
                    dst_mem = self._get_mem(ch1_dst)
                    src_off = self._translate_addr(ch1_src)
                    dst_off = self._translate_addr(ch1_dst)
                    if src_mem is not None and dst_mem is not None:
                        dst_mem[dst_off:dst_off + ch1_size] = \
                            src_mem[src_off:src_off + ch1_size]

                self._status[DMA.BASE + DMA.STATUS] = 2  # DONE
                # Clear sizes to prevent stale re-trigger on next CMD
                self._status[DMA.BASE + DMA.CH0_SIZE] = 0
                self._status[DMA.BASE + DMA.CH1_SIZE] = 0
                if self._status.get(DMA.BASE + DMA.IRQ_EN, 0) & 1:
                    self._set_irq(3)  # DMA IRQ
            else:
                self._status[addr & 0xFFFFFFFC] = value
        elif rw == 'read':
            return self._status.get(addr & 0xFFFFFFFC, 0)
        return 0

    # ── Doorbell ────────────────────────────────────────────────────

    def _handle_doorbell(self, rw: str, addr: int, value: int) -> int:
        if rw == 'write':
            self._status[addr & 0xFFFFFFFC] = value
        return self._status.get(addr & 0xFFFFFFFC, 0)

    # ── INTC ────────────────────────────────────────────────────────

    def _handle_intc(self, rw: str, addr: int, value: int) -> int:
        off = addr - INTC.BASE
        if rw == 'write' and off == INTC.ACK:
            self._status[INTC.BASE + INTC.PENDING] &= ~value
        elif rw == 'write':
            self._status[addr & 0xFFFFFFFC] = value
        return self._status.get(addr & 0xFFFFFFFC, 0)

    # ── Helpers ─────────────────────────────────────────────────────

    def _get_mem(self, addr: int):
        """Get the bytearray backing a given address, with offset translation."""
        if addr >= Addr.DRAM_BASE:
            return self.modules.get('dram')
        elif addr < 0x40000000:
            return self.modules.get('sram')
        return None

    def _translate_addr(self, addr: int) -> int:
        """Convert absolute address to buffer offset."""
        if addr >= Addr.DRAM_BASE:
            return addr - Addr.DRAM_BASE
        return addr  # SRAM starts at 0

    def _set_irq(self, module_bit: int):
        base = INTC.BASE
        self._status[base + INTC.PENDING] = \
            self._status.get(base + INTC.PENDING, 0) | (1 << module_bit)

    @property
    def trace(self) -> list:
        return self._trace

    def clear_trace(self):
        self._trace.clear()
