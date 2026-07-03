"""
MMIO Bridge — intercepts RISC-V load/store to NPU register space,
routing to GoldenMXU/SFU/Vector/DMA simulators.

Used by both RISCVMini (Python emulator) and Spike (when available).
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import numpy as np

from sim.golden_executor import GoldenSFU, GoldenVector
from sim.models.crossbar import CrossbarModel
from sim.regmap import Addr, MXU, SFU, VECTOR, DMA, DOORBELL, INTC


@dataclass
class MMIOBridge:
    """Route MMIO reads/writes to hardware module simulators."""

    modules: Dict[str, Any] = field(default_factory=dict)
    # modules['mxu'], ['sfu'], ['vector'], ['dma'], ['dram'], ['sram']
    irq_notify_callback: Optional[Callable[[], None]] = None

    def __post_init__(self):
        self._status: Dict[int, int] = {}
        self._trace: list = []
        self.tracer = None

    @property
    def _crossbar(self) -> Optional[CrossbarModel]:
        return self.modules.get('crossbar')

    def _sram_size(self) -> int:
        xbar = self._crossbar
        if xbar is not None:
            return len(xbar.sram)
        return len(self.modules.get('sram', bytearray()))

    def _to_crossbar_addr(self, addr: int) -> int:
        if addr >= Addr.DRAM_BASE:
            return addr
        sram_size = self._sram_size()
        if Addr.SRAM_BASE <= addr < Addr.SRAM_BASE + sram_size:
            return addr
        if 0 <= addr < sram_size:
            return Addr.SRAM_BASE + addr
        return addr

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

    # ── APB Protocol Layer ──────────────────────────────────────────

    def apb_read(self, paddr: int, psel: int = 1, penable: int = 1) -> int:
        """APB read with handshake validation (functional, not cycle-accurate).

        On the APB bus, psel must be asserted (=1) before penable goes high.
        If psel is deasserted, the slave returns 0 (not selected).
        If penable is deasserted during the setup phase, no data is driven.

        Returns prdata (32-bit).
        """
        if not psel:
            return 0
        if not penable:
            return 0
        return self.handle('read', paddr, 0)

    def apb_write(self, paddr: int, pwdata: int, psel: int = 1, penable: int = 1):
        """APB write with handshake validation (functional, not cycle-accurate).

        APB write requires both psel and penable to be asserted.
        A write during setup phase (penable=0) is ignored silently.
        """
        if not psel or not penable:
            return
        self.handle('write', paddr, pwdata)

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
                raw_i = self._status.get(MXU.BASE + MXU.I_ADDR, 0)
                raw_w = self._status.get(MXU.BASE + MXU.W_ADDR, 0)
                raw_o = self._status.get(MXU.BASE + MXU.O_ADDR, 0)
                raw_s = self._status.get(MXU.BASE + MXU.SCALE_ADDR, 0)

                if M > 0 and K > 0 and N > 0:
                    self._run_mxu_compute(mxu, M, K, N, raw_i, raw_w, raw_o, raw_s, accumulate)

                self._status[MXU.BASE + MXU.STATUS] = 2
                if self._status.get(MXU.BASE + MXU.IRQ_EN, 0) & 1:
                    self._set_irq(0)

            else:
                self._status[addr & 0xFFFFFFFC] = value

        elif rw == 'read':
            return self._status.get(addr & 0xFFFFFFFC, 0)
        return 0

    def _run_mxu_compute(self, mxu, M, K, N, raw_i, raw_w, raw_o, raw_s, accumulate):
        act_bytes = M * K
        wgt_packed_bytes = (K * N + 1) // 2
        xbar = self._crossbar

        if xbar is not None:
            i_abs = self._to_crossbar_addr(raw_i)
            w_abs = self._to_crossbar_addr(raw_w)
            o_abs = self._to_crossbar_addr(raw_o)
            s_abs = self._to_crossbar_addr(raw_s)

            act = np.frombuffer(
                xbar.read(CrossbarModel.MASTER_MXU, i_abs, act_bytes),
                dtype=np.int8).reshape(M, K)
            wgt_packed = np.frombuffer(
                xbar.read(CrossbarModel.MASTER_MXU, w_abs, wgt_packed_bytes),
                dtype=np.uint8)

            if raw_s > 0:
                num_blocks = (K + 127) // 128
                scale_bytes = num_blocks * N * 4
                scales = np.frombuffer(
                    xbar.read(CrossbarModel.MASTER_MXU, s_abs, scale_bytes),
                    dtype=np.float32).reshape(num_blocks, N)
                result = mxu.matmul_int4_per_block(act, wgt_packed, scales,
                                                   M, K, N, group_size=128)
                result_bytes = result.astype(np.float32).tobytes()
                dtype_out = np.float32
            else:
                result = mxu.matmul_int32(act, wgt_packed, M, K, N)
                result_bytes = result.astype(np.int32).tobytes()
                dtype_out = np.int32

            if accumulate:
                existing = np.frombuffer(
                    xbar.read(CrossbarModel.MASTER_MXU, o_abs, len(result_bytes)),
                    dtype=dtype_out).reshape(M, N)
                result = existing + result
                result_bytes = result.astype(dtype_out).tobytes()

            xbar.write(CrossbarModel.MASTER_MXU, o_abs, result_bytes)
            return

        i_off = self._translate_addr(raw_i)
        w_off = self._translate_addr(raw_w)
        o_off = self._translate_addr(raw_o)
        s_off = self._translate_addr(raw_s)
        sram = self.modules.get('sram', bytearray())
        if not sram:
            return

        act = np.frombuffer(sram[i_off:i_off + act_bytes], dtype=np.int8).reshape(M, K)
        wgt_packed = np.frombuffer(sram[w_off:w_off + wgt_packed_bytes], dtype=np.uint8)

        if s_off > 0:
            num_blocks = (K + 127) // 128
            scale_bytes = num_blocks * N * 4
            scales = np.frombuffer(sram[s_off:s_off + scale_bytes],
                                   dtype=np.float32).reshape(num_blocks, N)
            result = mxu.matmul_int4_per_block(act, wgt_packed, scales,
                                               M, K, N, group_size=128)
            result_bytes = result.astype(np.float32).tobytes()
            dtype_out = np.float32
        else:
            result = mxu.matmul_int32(act, wgt_packed, M, K, N)
            result_bytes = result.astype(np.int32).tobytes()
            dtype_out = np.int32

        if accumulate:
            existing = np.frombuffer(sram[o_off:o_off + len(result_bytes)],
                                     dtype=dtype_out).reshape(M, N)
            result = existing + result
            result_bytes = result.astype(dtype_out).tobytes()

        sram[o_off:o_off + len(result_bytes)] = result_bytes

    # ── SFU ─────────────────────────────────────────────────────────

    def _handle_sfu(self, rw: str, addr: int, value: int) -> int:
        off = addr - SFU.BASE
        sfu = self.modules.get('sfu')
        if sfu is None:
            sfu = GoldenSFU()
            self.modules['sfu'] = sfu

        if rw == 'write':
            if off == SFU.CMD and (value & 1):
                self._status[SFU.BASE + SFU.STATUS] = 1

                raw_i = self._status.get(SFU.BASE + SFU.I_ADDR, 0)
                raw_o = self._status.get(SFU.BASE + SFU.O_ADDR, 0)
                dim = self._status.get(SFU.BASE + SFU.DIM, 0)
                length = dim & 0xFFFF
                head_dim = (dim >> 16) & 0xFFFF
                pos = self._status.get(SFU.BASE + SFU.POS, 0)
                op = self._status.get(SFU.BASE + SFU.CTRL, 0) & 0xF

                if length > 0:
                    self._run_sfu_compute(sfu, raw_i, raw_o, length, head_dim, pos, op)

                self._status[SFU.BASE + SFU.STATUS] = 2
                if self._status.get(SFU.BASE + SFU.IRQ_EN, 0) & 1:
                    self._set_irq(1)  # SFU IRQ
            else:
                self._status[addr & 0xFFFFFFFC] = value
        elif rw == 'read':
            return self._status.get(addr & 0xFFFFFFFC, 0)
        return 0

    def _run_sfu_compute(self, sfu, raw_i, raw_o, length, head_dim, pos, op):
        xbar = self._crossbar
        if xbar is not None:
            i_abs = self._to_crossbar_addr(raw_i)
            o_abs = self._to_crossbar_addr(raw_o)
            inp = np.frombuffer(
                xbar.read(CrossbarModel.MASTER_SFU, i_abs, length * 2),
                dtype=np.float16).astype(np.float32)
            out = self._sfu_op(sfu, inp, length, head_dim, pos, op)
            out_bytes = out.astype(np.float16).tobytes()
            xbar.write(CrossbarModel.MASTER_SFU, o_abs, out_bytes)
            return

        sram = self.modules.get('sram', bytearray())
        if not sram:
            return
        i_off = self._translate_addr(raw_i)
        o_off = self._translate_addr(raw_o)
        inp = np.frombuffer(
            sram[i_off:i_off + length * 2], dtype=np.float16
        ).astype(np.float32)
        out = self._sfu_op(sfu, inp, length, head_dim, pos, op)
        out_bytes = out.astype(np.float16).tobytes()
        sram[o_off:o_off + len(out_bytes)] = out_bytes

    @staticmethod
    def _sfu_op(sfu, inp, length, head_dim, pos, op):
        if op == 0:
            return sfu.softmax_hw(inp)
        if op == 1:
            return sfu.layernorm_hw(inp)
        if op == 2:
            return sfu.gelu_hw(inp)
        if op in (3, 4):
            return sfu.silu_hw(inp)
        if op == 6:
            return sfu.rmsnorm_hw(inp)
        if op == 5:
            half = length // 2
            q_in = inp[:half]
            k_in = inp[half:half + half] if length > half else q_in
            hd = head_dim if head_dim else (half if half % 2 == 0 else max(half, 2))
            nq = max(1, half // hd) if hd else 1
            nk = max(1, len(k_in) // hd) if hd else 1
            q_out, k_out = sfu.rope_hw(
                q_in, k_in, position=pos,
                num_heads=nq, head_dim=hd
            )
            return np.concatenate([q_out, k_out])
        return inp

    # ── VECTOR ──────────────────────────────────────────────────────

    def _handle_vector(self, rw: str, addr: int, value: int) -> int:
        off = addr - VECTOR.BASE
        vector = self.modules.get('vector')
        if vector is None:
            vector = GoldenVector()
            self.modules['vector'] = vector

        if rw == 'write':
            if off == VECTOR.CMD and (value & 1):
                self._status[VECTOR.BASE + VECTOR.STATUS] = 1

                raw_a = self._status.get(VECTOR.BASE + VECTOR.A_ADDR, 0)
                raw_b = self._status.get(VECTOR.BASE + VECTOR.B_ADDR, 0)
                raw_o = self._status.get(VECTOR.BASE + VECTOR.O_ADDR, 0)
                dim = self._status.get(VECTOR.BASE + VECTOR.DIM, 0) & 0xFFFF
                op = self._status.get(VECTOR.BASE + VECTOR.CTRL, 0) & 0xF

                if dim > 0:
                    self._run_vector_compute(vector, raw_a, raw_b, raw_o, dim, op)

                self._status[VECTOR.BASE + VECTOR.STATUS] = 2
                if self._status.get(VECTOR.BASE + VECTOR.IRQ_EN, 0) & 1:
                    self._set_irq(2)
            else:
                self._status[addr & 0xFFFFFFFC] = value
        elif rw == 'read':
            return self._status.get(addr & 0xFFFFFFFC, 0)
        return 0

    def _run_vector_compute(self, vector, raw_a, raw_b, raw_o, dim, op):
        xbar = self._crossbar
        if xbar is not None:
            a_abs = self._to_crossbar_addr(raw_a)
            b_abs = self._to_crossbar_addr(raw_b)
            o_abs = self._to_crossbar_addr(raw_o)
            out_bytes = self._vector_op_bytes(
                vector, xbar, CrossbarModel.MASTER_VEC,
                a_abs, b_abs, dim, op
            )
            if out_bytes:
                xbar.write(CrossbarModel.MASTER_VEC, o_abs, out_bytes)
            return

        sram = self.modules.get('sram', bytearray())
        if not sram:
            return
        a_off = self._translate_addr(raw_a)
        b_off = self._translate_addr(raw_b)
        o_off = self._translate_addr(raw_o)
        out_bytes = self._vector_op_bytes_direct(vector, sram, a_off, b_off, dim, op)
        if out_bytes:
            sram[o_off:o_off + len(out_bytes)] = out_bytes

    def _vector_op_bytes(self, vector, xbar, master_id, a_abs, b_abs, dim, op):
        if op == 0:
            a = np.frombuffer(xbar.read(master_id, a_abs, dim * 4), dtype=np.int32)
            b = np.frombuffer(xbar.read(master_id, b_abs, dim * 4), dtype=np.int32)
            return vector.add(a, b).astype(np.int32).tobytes()
        if op == 1:
            a = np.frombuffer(xbar.read(master_id, a_abs, dim * 4), dtype=np.int32)
            b = np.frombuffer(xbar.read(master_id, b_abs, dim * 4), dtype=np.int32)
            return vector.mul(a, b).astype(np.int32).tobytes()
        if op == 2:
            a = np.frombuffer(xbar.read(master_id, a_abs, dim * 2), dtype=np.float16).astype(np.float32)
            return np.array([vector.max_reduce(a)], dtype=np.float16).tobytes()
        if op == 3:
            a = np.frombuffer(xbar.read(master_id, a_abs, dim * 2), dtype=np.float16).astype(np.float32)
            return np.array([vector.sum_reduce(a)], dtype=np.float16).tobytes()
        if op == 4:
            a = np.frombuffer(xbar.read(master_id, a_abs, dim * 4), dtype=np.int32)
            return vector.conv_i32_to_f16(a).tobytes()
        if op == 5:
            a = np.frombuffer(xbar.read(master_id, a_abs, dim * 2), dtype=np.float16).astype(np.float32)
            b = np.frombuffer(xbar.read(master_id, b_abs, dim * 4), dtype=np.int32)
            return vector.residual_add(a, b).astype(np.int32).tobytes()
        return b''

    def _vector_op_bytes_direct(self, vector, sram, a_off, b_off, dim, op):
        if op == 0:
            a = np.frombuffer(sram[a_off:a_off + dim * 4], dtype=np.int32)
            b = np.frombuffer(sram[b_off:b_off + dim * 4], dtype=np.int32)
            return vector.add(a, b).astype(np.int32).tobytes()
        if op == 1:
            a = np.frombuffer(sram[a_off:a_off + dim * 4], dtype=np.int32)
            b = np.frombuffer(sram[b_off:b_off + dim * 4], dtype=np.int32)
            return vector.mul(a, b).astype(np.int32).tobytes()
        if op == 2:
            a = np.frombuffer(sram[a_off:a_off + dim * 2], dtype=np.float16).astype(np.float32)
            return np.array([vector.max_reduce(a)], dtype=np.float16).tobytes()
        if op == 3:
            a = np.frombuffer(sram[a_off:a_off + dim * 2], dtype=np.float16).astype(np.float32)
            return np.array([vector.sum_reduce(a)], dtype=np.float16).tobytes()
        if op == 4:
            a = np.frombuffer(sram[a_off:a_off + dim * 4], dtype=np.int32)
            return vector.conv_i32_to_f16(a).tobytes()
        if op == 5:
            a = np.frombuffer(sram[a_off:a_off + dim * 2], dtype=np.float16).astype(np.float32)
            b = np.frombuffer(sram[b_off:b_off + dim * 4], dtype=np.int32)
            return vector.residual_add(a, b).astype(np.int32).tobytes()
        return b''

    # ── DMA ─────────────────────────────────────────────────────────

    def _handle_dma(self, rw: str, addr: int, value: int) -> int:
        off = addr - DMA.BASE

        if rw == 'write':
            if off == DMA.CMD and (value & 1):
                self._status[DMA.BASE + DMA.STATUS] = 1

                ch0_src = self._status.get(DMA.BASE + DMA.CH0_SRC, 0)
                ch0_dst = self._status.get(DMA.BASE + DMA.CH0_DST, 0)
                ch0_size = self._status.get(DMA.BASE + DMA.CH0_SIZE, 0)
                if ch0_size > 0:
                    self._run_dma_transfer(ch0_src, ch0_dst, ch0_size)

                ch1_src = self._status.get(DMA.BASE + DMA.CH1_SRC, 0)
                ch1_dst = self._status.get(DMA.BASE + DMA.CH1_DST, 0)
                ch1_size = self._status.get(DMA.BASE + DMA.CH1_SIZE, 0)
                if ch1_size > 0:
                    self._run_dma_transfer(ch1_src, ch1_dst, ch1_size)

                self._status[DMA.BASE + DMA.STATUS] = 2
                self._status[DMA.BASE + DMA.CH0_SIZE] = 0
                self._status[DMA.BASE + DMA.CH1_SIZE] = 0
                if self._status.get(DMA.BASE + DMA.IRQ_EN, 0) & 1:
                    self._set_irq(3)
            else:
                self._status[addr & 0xFFFFFFFC] = value
        elif rw == 'read':
            return self._status.get(addr & 0xFFFFFFFC, 0)
        return 0

    def _run_dma_transfer(self, src_addr: int, dst_addr: int, size: int):
        xbar = self._crossbar
        if xbar is not None:
            src_abs = self._to_crossbar_addr(src_addr)
            dst_abs = self._to_crossbar_addr(dst_addr)
            data = xbar.read(CrossbarModel.MASTER_DMA, src_abs, size)
            xbar.write(CrossbarModel.MASTER_DMA, dst_abs, data)
            return

        src_mem = self._get_mem(src_addr)
        dst_mem = self._get_mem(dst_addr)
        src_off = self._translate_addr(src_addr)
        dst_off = self._translate_addr(dst_addr)
        if src_mem is not None and dst_mem is not None:
            dst_mem[dst_off:dst_off + size] = src_mem[src_off:src_off + size]

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
        if 0x20000000 <= addr < 0x20400000:
            return addr - 0x20000000
        return addr

    def _set_irq(self, module_bit: int):
        base = INTC.BASE
        self._status[base + INTC.PENDING] = \
            self._status.get(base + INTC.PENDING, 0) | (1 << module_bit)
        if self.irq_notify_callback:
            self.irq_notify_callback()

    @property
    def trace(self) -> list:
        return self._trace

    def clear_trace(self):
        self._trace.clear()
