"""
MMIO Bridge — intercepts RISC-V load/store to NPU register space,
routing to GoldenMXU/SFU/Vector/DMA simulators.

Used by both RISCVMini (Python emulator) and Spike (when available).
"""

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

import numpy as np

from golden_executor import GoldenSFU, GoldenVector
from models.crossbar import CrossbarModel
from regmap import Addr, MXU, SFU, VECTOR, DMA, PCIE_DMA, DOORBELL, INTC


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
        self._mxu_k_block = 0
        self.perf_session: Optional[Any] = None  # PerformanceSession, set by FuncModel

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
        elif base == PCIE_DMA.BASE:
            return self._handle_pcie_dma(rw, addr, value)
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
    # Wrapper register offsets used by firmware npx-regmap.h:
    #   MXU_WRP_WEIGHT_BASE=0x30  MXU_WRP_ACT_BASE=0x34  MXU_WRP_OUT_BASE=0x38
    #   MXU_WRP_CMD=0x3C          MXU_WRP_STATUS=0x40
    #   MXU_WRP_K_TILES=0x44      MXU_WRP_DIM_N=0x48
    MXU_WRP_BASE = 0x30
    MXU_WRP_WEIGHT_BASE = MXU_WRP_BASE + 0
    MXU_WRP_ACT_BASE    = MXU_WRP_BASE + 4
    MXU_WRP_OUT_BASE    = MXU_WRP_BASE + 8
    MXU_WRP_CMD         = MXU_WRP_BASE + 12
    MXU_WRP_STATUS      = MXU_WRP_BASE + 16
    MXU_WRP_K_TILES     = MXU_WRP_BASE + 20
    MXU_WRP_DIM_N       = MXU_WRP_BASE + 24

    def _handle_mxu(self, rw: str, addr: int, value: int) -> int:
        off = addr - MXU.BASE
        mxu = self.modules.get('mxu')
        if not mxu:
            return 0

        if rw == 'write':
            # Wrapper CMD: firmware writes preload command, bridge
            # acknowledges immediately (DRAM→SRAM DMA already done
            # in Python by host_write_data).
            if off == self.MXU_WRP_CMD:
                self._status[addr & 0xFFFFFFFC] = value
                self._status[MXU.BASE + self.MXU_WRP_STATUS] = 1  # done

            elif off == MXU.CTRL:
                self._status[MXU.BASE + MXU.CTRL] = value  # CTRL stored

            elif off == MXU.CMD and (value & 1):  # START
                self._status[MXU.BASE + MXU.STATUS] = 1  # BUSY
                ctrl = self._status.get(MXU.BASE + MXU.CTRL, 0)
                accumulate = bool(ctrl & 4)  # bit[2] = ACCUMULATE

                if accumulate:
                    self._mxu_k_block += 1
                else:
                    self._mxu_k_block = 0

                M = (self._status.get(MXU.BASE + MXU.DIM0, 0)) & 0xFFFF
                K = (self._status.get(MXU.BASE + MXU.DIM0, 0) >> 16) & 0xFFFF
                N = self._status.get(MXU.BASE + MXU.DIM1, 0) & 0xFFFF
                raw_i = self._status.get(MXU.BASE + MXU.I_ADDR, 0)
                raw_w = self._status.get(MXU.BASE + MXU.W_ADDR, 0)
                raw_o = self._status.get(MXU.BASE + MXU.O_ADDR, 0)
                raw_s = self._status.get(MXU.BASE + MXU.SCALE_ADDR, 0)

                # Perf event: MXU command accepted
                mxu_seq_id: int = 0
                if self.perf_session is not None:
                    try:
                        from timing.perf_contract import EngineType, OpType
                        accepted = self.perf_session.emit_accepted(
                            EngineType.MXU,
                            OpType.MMUL,
                            {"M": M, "K": K, "N": N},
                        )
                        mxu_seq_id = accepted.seq_id
                    except Exception:
                        pass

                if self.perf_session is not None and self.perf_session.profile_only:
                    # Profile-only: skip numerical kernel, still emit completion
                    pass
                elif M > 0 and K > 0 and N > 0:
                    self._run_mxu_compute(mxu, M, K, N, raw_i, raw_w, raw_o, raw_s, accumulate)

                self._status[MXU.BASE + MXU.STATUS] = 2

                # Perf event: MXU command completed
                if self.perf_session is not None and mxu_seq_id > 0:
                    try:
                        from timing.perf_contract import EngineType, OpType
                        self.perf_session.emit_completed(
                            mxu_seq_id,
                            EngineType.MXU,
                            OpType.MMUL,
                            {"M": M, "K": K, "N": N},
                        )
                    except Exception:
                        pass

                if self._status.get(MXU.BASE + MXU.IRQ_EN, 0) & 1:
                    self._set_irq(0)

            else:
                self._status[addr & 0xFFFFFFFC] = value

        elif rw == 'read':
            return self._status.get(addr & 0xFFFFFFFC, 0)
        return 0

    def _run_mxu_compute(self, mxu, M, K, N, raw_i, raw_w, raw_o, raw_s, accumulate):
        """Execute a single MXU tile computation (called per firmware tile iteration).

        Operates on per-tile data: K ≤ TILE_H (64), N ≤ TILE_W (64).
        The firmware has already DMA'd the correct 64×64 INT4 tile + per-tile
        scales into SRAM at the addresses passed via MMIO registers.

        Data layout assumption: the host must write weights to DRAM in
        firmware-compatible tiled order (for each N-tile, for each K-tile,
        TILE_WEIGHT_BYTES + TILE_SCALE_BYTES). See _reorder_weights_to_firmware_tiles().
        """
        wgt_packed_bytes = (K * N + 1) // 2
        xbar = self._crossbar

        trace_level = int(os.environ.get('BBRIDGE_TRACE', '0'))

        if xbar is not None:
            i_abs = self._to_crossbar_addr(raw_i)
            w_abs = self._to_crossbar_addr(raw_w)
            o_abs = self._to_crossbar_addr(raw_o)
            s_abs = self._to_crossbar_addr(raw_s)

            # ISSUE-13B: activations are staged in the mxu_soc_wrapper
            # broadcast layout — ceil(K/64) back-to-back 4096-byte K-tiles;
            # 64-byte word c of each tile holds column k (byte r = act[r, k]).
            act_k_tiles = (K + 63) // 64
            act_tiles = np.frombuffer(
                xbar.read(CrossbarModel.MASTER_MXU, i_abs, act_k_tiles * 64 * 64),
                dtype=np.uint8).reshape(act_k_tiles, 64, 64)
            act = act_tiles.transpose(2, 0, 1).reshape(64, act_k_tiles * 64)
            act = act[:M, :K].astype(np.int8).copy()
            wgt_packed = np.frombuffer(
                xbar.read(CrossbarModel.MASTER_MXU, w_abs, wgt_packed_bytes),
                dtype=np.uint8)

            if trace_level >= 2:
                act_head = act.tobytes()[:8].hex()
                wgt_head = wgt_packed.tobytes()[:8].hex()
                print(
                    f"BBRIDGE_T2 k_block={self._mxu_k_block} M={M} K={K} N={N} "
                    f"accumulate={accumulate} "
                    f"raw_i=0x{raw_i:08x} raw_w=0x{raw_w:08x} "
                    f"raw_o=0x{raw_o:08x} raw_s=0x{raw_s:08x} "
                    f"i_abs=0x{i_abs:08x} w_abs=0x{w_abs:08x} "
                    f"o_abs=0x{o_abs:08x} s_abs=0x{s_abs:08x} "
                    f"act_head={act_head} wgt_head={wgt_head}"
                )

            if raw_s > 0:
                num_blocks = (K + 127) // 128
                scale_bytes = num_blocks * N * 4
                scales = np.frombuffer(
                    xbar.read(CrossbarModel.MASTER_MXU, s_abs, scale_bytes),
                    dtype=np.float32).reshape(num_blocks, N)
                if trace_level >= 2:
                    scale_head = scales.tobytes()[:16].hex()
                    print(
                        f"BBRIDGE_T2_SCALE k_block={self._mxu_k_block} "
                        f"scale_head={scale_head}"
                    )
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
                if trace_level >= 2:
                    print(
                        f"BBRIDGE_T2_ACC k_block={self._mxu_k_block} "
                        f"existing={existing.flat[:4].tolist()}"
                    )
                result = existing + result
                result_bytes = result.astype(dtype_out).tobytes()

            xbar.write(CrossbarModel.MASTER_MXU, o_abs, result_bytes)
            if trace_level >= 2:
                print(
                    f"BBRIDGE_T2_RESULT k_block={self._mxu_k_block} "
                    f"result={result.flat[:4].tolist()}"
                )
            return

        i_off = self._translate_addr(raw_i)
        w_off = self._translate_addr(raw_w)
        o_off = self._translate_addr(raw_o)
        s_off = self._translate_addr(raw_s)
        sram = self.modules.get('sram', bytearray())
        if not sram:
            return

        act_k_tiles = (K + 63) // 64
        act_tiles = np.frombuffer(sram[i_off:i_off + act_k_tiles * 64 * 64],
                                  dtype=np.uint8).reshape(act_k_tiles, 64, 64)
        act = act_tiles.transpose(2, 0, 1).reshape(64, act_k_tiles * 64)
        act = act[:M, :K].astype(np.int8).copy()
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

                # Perf event: SFU command accepted
                sfu_seq_id: int = 0
                if self.perf_session is not None and length > 0:
                    try:
                        from timing.perf_contract import EngineType, OpType
                        sfu_op = self.perf_session.sfu_op(op) or OpType.SOFTMAX
                        accepted = self.perf_session.emit_accepted(
                            EngineType.SFU,
                            sfu_op,
                            {"elements": length},
                        )
                        sfu_seq_id = accepted.seq_id
                    except Exception:
                        pass

                if self.perf_session is not None and self.perf_session.profile_only:
                    pass  # profile-only: skip numerical kernel
                elif length > 0:
                    self._run_sfu_compute(sfu, raw_i, raw_o, length, head_dim, pos, op)

                self._status[SFU.BASE + SFU.STATUS] = 2

                # Perf event: SFU command completed
                if self.perf_session is not None and sfu_seq_id > 0:
                    try:
                        from timing.perf_contract import EngineType, OpType
                        sfu_op2 = self.perf_session.sfu_op(op) or OpType.SOFTMAX
                        self.perf_session.emit_completed(
                            sfu_seq_id,
                            EngineType.SFU,
                            sfu_op2,
                            {"elements": length},
                        )
                    except Exception:
                        pass

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
            hd = head_dim if head_dim else max(length // 4, 2)
            k_len = 2 * hd
            q_len = length - k_len
            if q_len <= 0:
                q_len = length // 2
                k_len = length - q_len
            q_in = inp[:q_len]
            k_in = inp[q_len:length]
            nq = max(1, q_len // hd) if hd else 1
            q_out, k_out = sfu.rope_hw(
                q_in, k_in, position=pos,
                num_heads=nq, head_dim=hd
            )
            out = np.zeros(length, dtype=np.float32)
            out[:q_len] = q_out
            out[q_len:length] = k_out
            return out
        return inp

    # ── VECTOR ──────────────────────────────────────────────────────
    # Wrapper register offsets (npx-regmap.h):
    #   VEC_WRP_A_BASE=0x30  VEC_WRP_B_BASE=0x34  VEC_WRP_O_BASE=0x38
    #   VEC_WRP_CMD=0x3C     VEC_WRP_STATUS=0x40  VEC_WRP_LEN=0x44
    VEC_WRP_BASE = 0x30
    VEC_WRP_CMD    = VEC_WRP_BASE + 12
    VEC_WRP_STATUS = VEC_WRP_BASE + 16

    def _handle_vector(self, rw: str, addr: int, value: int) -> int:
        off = addr - VECTOR.BASE
        vector = self.modules.get('vector')
        if vector is None:
            vector = GoldenVector()
            self.modules['vector'] = vector

        if rw == 'write':
            # Wrapper CMD: firmware writes DMA preload; acknowledge immediately
            if off == self.VEC_WRP_CMD:
                self._status[addr & 0xFFFFFFFC] = value
                self._status[VECTOR.BASE + self.VEC_WRP_STATUS] = 1  # done
            elif off == VECTOR.CMD and (value & 1):
                self._status[VECTOR.BASE + VECTOR.STATUS] = 1

                raw_a = self._status.get(VECTOR.BASE + VECTOR.A_ADDR, 0)
                raw_b = self._status.get(VECTOR.BASE + VECTOR.B_ADDR, 0)
                raw_o = self._status.get(VECTOR.BASE + VECTOR.O_ADDR, 0)
                dim = self._status.get(VECTOR.BASE + VECTOR.DIM, 0) & 0xFFFF
                op = self._status.get(VECTOR.BASE + VECTOR.CTRL, 0) & 0xF

                # Perf event: Vector command accepted
                vec_seq_id: int = 0
                if self.perf_session is not None and dim > 0:
                    try:
                        from timing.perf_contract import EngineType, OpType
                        vec_op = self.perf_session.vector_op(op) or OpType.ADD
                        accepted = self.perf_session.emit_accepted(
                            EngineType.VECTOR,
                            vec_op,
                            {"dim": dim},
                        )
                        vec_seq_id = accepted.seq_id
                    except Exception:
                        pass

                if self.perf_session is not None and self.perf_session.profile_only:
                    pass  # profile-only: skip numerical kernel
                elif dim > 0:
                    self._run_vector_compute(vector, raw_a, raw_b, raw_o, dim, op)

                self._status[VECTOR.BASE + VECTOR.STATUS] = 2

                # Perf event: Vector command completed
                if self.perf_session is not None and vec_seq_id > 0:
                    try:
                        from timing.perf_contract import EngineType, OpType
                        vec_op2 = self.perf_session.vector_op(op) or OpType.ADD
                        self.perf_session.emit_completed(
                            vec_seq_id,
                            EngineType.VECTOR,
                            vec_op2,
                            {"dim": dim},
                        )
                    except Exception:
                        pass

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

                ch1_src = self._status.get(DMA.BASE + DMA.CH1_SRC, 0)
                ch1_dst = self._status.get(DMA.BASE + DMA.CH1_DST, 0)
                ch1_size = self._status.get(DMA.BASE + DMA.CH1_SIZE, 0)

                total_size = ch0_size + ch1_size

                # Perf event: DMA command accepted
                dma_seq_id: int = 0
                if self.perf_session is not None and total_size > 0:
                    try:
                        from timing.perf_contract import EngineType, OpType
                        accepted = self.perf_session.emit_accepted(
                            EngineType.DMA,
                            OpType.DMA_COPY,
                            {"bytes": total_size},
                        )
                        dma_seq_id = accepted.seq_id
                    except Exception:
                        pass

                batch_skip = (
                    self.perf_session is not None
                    and self.perf_session.batch_profile
                )
                if not batch_skip:
                    if ch0_size > 0:
                        self._run_dma_transfer(ch0_src, ch0_dst, ch0_size)
                    if ch1_size > 0:
                        self._run_dma_transfer(ch1_src, ch1_dst, ch1_size)

                self._status[DMA.BASE + DMA.STATUS] = 2

                # Perf event: DMA command completed
                if self.perf_session is not None and dma_seq_id > 0:
                    try:
                        from timing.perf_contract import EngineType, OpType
                        self.perf_session.emit_completed(
                            dma_seq_id,
                            EngineType.DMA,
                            OpType.DMA_COPY,
                            {"bytes": total_size},
                        )
                    except Exception:
                        pass

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

    # ── PCIE_DMA ────────────────────────────────────────────────────

    def _handle_pcie_dma(self, rw: str, addr: int, value: int) -> int:
        off = addr - PCIE_DMA.BASE
        engine = self.modules.get('pcie_dma')

        if rw == 'write':
            if off == PCIE_DMA.CTRL and (value & 0x3):
                self._status[PCIE_DMA.BASE + PCIE_DMA.STATUS] = 0
                direction = value & 0x3
                pcie_lo = self._status.get(PCIE_DMA.BASE + PCIE_DMA.PCIE_ADDR_LO, 0)
                pcie_hi = self._status.get(PCIE_DMA.BASE + PCIE_DMA.PCIE_ADDR_HI, 0)
                axi_addr = self._status.get(PCIE_DMA.BASE + PCIE_DMA.AXI_ADDR, 0)
                length = self._status.get(PCIE_DMA.BASE + PCIE_DMA.LEN, 0)
                tag = self._status.get(PCIE_DMA.BASE + PCIE_DMA.TAG, 0)
                pcie_addr = (pcie_hi << 32) | pcie_lo

                irq_en = bool(value & 0x8)
                error_code = 0

                if engine is not None and length > 0:
                    if direction == 0x1:
                        engine.submit_read_desc(pcie_addr, axi_addr, length, tag)
                    elif direction == 0x2:
                        engine.submit_write_desc(pcie_addr, axi_addr, length, tag)
                    statuses = engine.desc_status
                    if statuses:
                        _, error_code = statuses[-1]

                status = 0x4 if direction == 0x1 else 0x8
                if error_code:
                    status |= 0x10
                    err_reg = PCIE_DMA.RD_ERR_CODE if direction == 0x1 else PCIE_DMA.WR_ERR_CODE
                    self._status[PCIE_DMA.BASE + err_reg] = error_code
                self._status[PCIE_DMA.BASE + PCIE_DMA.STATUS] = status

                if irq_en and error_code == 0:
                    self._set_irq(7)
            else:
                self._status[addr & 0xFFFFFFFC] = value

        return self._status.get(addr & 0xFFFFFFFC, 0)

    # ── Doorbell ────────────────────────────────────────────────────

    def _handle_doorbell(self, rw: str, addr: int, value: int) -> int:
        if rw == 'write':
            self._status[addr & 0xFFFFFFFC] = value
        return self._status.get(addr & 0xFFFFFFFC, 0)

    # ── INTC ────────────────────────────────────────────────────────

    def _handle_intc(self, rw: str, addr: int, value: int) -> int:
        off = addr - INTC.BASE
        if rw == 'write' and off == INTC.ACK:
            self._status[INTC.BASE + INTC.PENDING] = self._status.get(INTC.BASE + INTC.PENDING, 0) & ~value
        elif rw == 'write':
            self._status[addr & 0xFFFFFFFC] = value
        return self._status.get(addr & 0xFFFFFFFC, 0)

    # ── Helpers ─────────────────────────────────────────────────────

    def _get_mem(self, addr: int):
        """Fallback-only: Get the bytearray backing a given address.

        DEPRECATED: Prefer CrossbarModel (``self._crossbar.read/write``) for all
        memory access. This method exists solely for backward compatibility when
        no CrossbarModel instance is available in ``self.modules``.
        """
        if addr >= Addr.DRAM_BASE:
            return self.modules.get('dram')
        elif addr < 0x40000000:
            return self.modules.get('sram')
        return None

    def _translate_addr(self, addr: int) -> int:
        """Fallback-only: Convert absolute address to buffer offset.

        DEPRECATED: Prefer CrossbarModel (``self._crossbar.read/write``) for all
        memory access. This method exists solely for backward compatibility when
        no CrossbarModel instance is available in ``self.modules``.
        """
        if addr >= Addr.DRAM_BASE:
            return addr - Addr.DRAM_BASE
        if 0x20000000 <= addr < 0x20400000:
            return addr - 0x20000000
        return addr

    def _set_irq(self, module_bit: int):
        """Set INTC PENDING bit; raise cpu_irq only when the enabled-pending
        popcount meets THRESHOLD (mirrors rtl/intc/intc_top.v):

            cpu_irq = |(PENDING & ENABLE) and popcount(PENDING & ENABLE) >= THRESHOLD

        ENABLE defaults to 0x1FF (all 9 FM sources) when never programmed so
        legacy interrupt flows keep working; once ENABLE is written, masked
        sources can no longer raise cpu_irq.  PENDING is always set — ENABLE
        gates the cpu_irq assertion, not the pending register.
        """
        base = INTC.BASE
        self._status[base + INTC.PENDING] = \
            self._status.get(base + INTC.PENDING, 0) | (1 << module_bit)
        enable = self._status.get(base + INTC.ENABLE, 0x1FF)
        threshold = self._status.get(base + INTC.THRESHOLD, 0)
        enabled_pending = self._status.get(base + INTC.PENDING, 0) & enable
        if enabled_pending != 0 and bin(enabled_pending).count('1') >= threshold:
            if self.irq_notify_callback:
                self.irq_notify_callback()

    @property
    def trace(self) -> list:
        return self._trace

    def clear_trace(self):
        self._trace.clear()
