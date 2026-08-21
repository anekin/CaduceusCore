#!/usr/bin/env python3
"""
rtl_soc_mmul_probe.py — ISSUE-13B minimal reproduction (single MMUL-down + VRESID).

Dispatches ONE FFN-down MMUL tile (M=1, K=512, N=768) plus a VRESID VADD through
the on-chip Ibex firmware, then reads the MMUL output back from DRAM and compares
it against the Func Model dequantized FP32 golden
(GoldenMXU.matmul_int4_per_block — per-block scale, FP32 accumulate).

This isolates the failing link of the 9-layer segment run: the hardware MMUL
store-out must apply the per-block scales and produce FP32, not raw INT32.

Run: MODULE=sim.rtl_soc_mmul_probe TOPLEVEL=tb_soc_ibex (same simv as the
segment run).  Prints [PROBE] diagnostics; asserts the fix when NAN_EXPECT=0.
"""
import os
import struct
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO))

import cocotb  # noqa: E402
from cocotb_bridge import CocotbBridge, DRAM_BASE  # noqa: E402
from func_model import FuncModel  # noqa: E402
from golden_executor import GoldenMXU  # noqa: E402
from quantize import quantize_int4_per_block  # noqa: E402
import spike_host as sh  # noqa: E402
import rtl_soc_segment_run as segrun  # noqa: E402

M = 1
K = 512      # 8 per-block (128) scale groups -> exercises FP32 accumulate
N = 768      # 12 N-tiles of 64 columns

if os.environ.get("PROBE_SMALL"):
    K, N = 64, 64   # single K-tile / single N-tile: one MXU call
elif os.environ.get("PROBE_BIG"):
    K, N = 512, 2048  # segment-run-sized VRESID dim

NAN_EXPECT = int(os.environ.get("NAN_EXPECT", "0"))


def _stats(tag, arr, ref):
    nan_cnt = int(np.isnan(arr).sum())
    bad = np.where((~np.isclose(arr.astype(np.float64),
                                 ref.astype(np.float64),
                                 rtol=1e-3, atol=1e-6)) |
                   np.isnan(arr))[0]
    if nan_cnt:
        cos = float("nan")
    else:
        cos = float(sh._cosine_similarity(arr.astype(np.float64),
                                          ref.astype(np.float64)))
    diff = np.abs(arr.astype(np.float64) - ref.astype(np.float64))
    print(f"[PROBE] {tag}: nan={nan_cnt}/{arr.size} cos={cos:.6f} "
          f"max_abs_diff={float(np.nanmax(diff)):.4e}", flush=True)
    if bad.size:
        for i in bad[:6]:
            print(f"[PROBE]   bad[{i}]: hw={arr.flat[i]!r} ref={ref.flat[i]!r}",
                  flush=True)
    return nan_cnt, cos


@cocotb.test()
async def test_mmul_down_probe(dut):
    bridge = CocotbBridge(dut)
    await bridge.start_clock()
    await bridge.reset(5)

    if os.environ.get("PROBE_APB"):
        from cocotb.triggers import RisingEdge

        async def _mon():
            while True:
                await RisingEdge(dut.clk)
                try:
                    psel = int(dut.u_dut.u_ibex_wrapper.apb_psel.value)
                    penable = int(dut.u_dut.u_ibex_wrapper.apb_penable.value)
                    pwrite = int(dut.u_dut.u_ibex_wrapper.apb_pwrite.value)
                    pready = int(dut.u_dut.u_ibex_wrapper.apb_pready.value)
                    if not (psel and penable and pwrite and pready):
                        continue
                    addr = int(dut.u_dut.u_ibex_wrapper.apb_paddr.value)
                    data = int(dut.u_dut.u_ibex_wrapper.apb_pwdata.value)
                    if 0x40005000 <= addr < 0x40006000:
                        print(f"[PROBE-APB] db write 0x{addr:08x} = 0x{data:08x}",
                              flush=True)
                except Exception:
                    pass
        cocotb.start_soon(_mon())

    rng = np.random.RandomState(13)
    ffn_hidden = (rng.randn(M, K).astype(np.float32) * 3.0)
    ffn_i8, ffn_scale = sh._int8_quantize(ffn_hidden)
    W_down = (rng.randn(K, N).astype(np.float32) * 0.1)

    packed_raw, scales_raw, _ = quantize_int4_per_block(W_down, 128)
    golden = GoldenMXU().matmul_int4_per_block(ffn_i8, packed_raw, scales_raw,
                                               M, K, N, group_size=128)

    model = FuncModel(dram_mb=8, sram_kb=4096)
    sh._reset_act_allocator()
    mmul_in_addr = sh._act_alloc(((K + 63) // 64) * 4096)
    mmul_out_addr = sh._act_alloc(N * 4)
    l_out_addr = sh._act_alloc(N * 4)
    resid_addr = sh._act_alloc(N * 4)

    sh._reset_wave_arena()
    ops = []
    sh._add_mmul_tiles_phase10(ops, model, mmul_in_addr, mmul_out_addr,
                               W_down, M, K, N, ffn_i8, tile_n=N,
                               tile_lo=0, tile_hi=1)
    n = segrun._ibex_schedule_chain(model, ops, 0)
    print(f"[PROBE] dispatching {n} mmul cmds", flush=True)
    await bridge.segment_preload(bytes(model.dram))
    await bridge.segment_kick(n)
    ok = await bridge.segment_wait(n, 100_000_000, 20_000)
    assert ok, "MMUL wave timeout"

    if os.environ.get("PROBE_SRAM"):
        # Dump RTL SRAM staging buffers and compare against python layout.
        # Addresses follow npu_firmware.c: act_sram=0, act_end=align64(input_size),
        # wbuf[i]=act_end+i*2048, sbuf[0]=align64(wbuf[1]+2048).
        act_sram_abs = 0x20000000
        act_end = (len(sh._pack_act_tile_major_contig(ffn_i8, M, K)) + 63) & ~63
        w0_abs = 0x20000000 + act_end
        s0_abs = 0x20000000 + ((act_end + 2 * 2048 + 63) & ~63)
        for (nm, a, ln) in (("act", act_sram_abs, 64), ("scale", s0_abs, 16),
                            ("wgt", w0_abs, 64)):
            raw = await bridge._sram_backdoor_read(a, ln)
            print(f"[PROBE-SRAM] {nm}@0x{a:08x}: {raw[:ln].hex()}", flush=True)
        # python expected
        act_packed = sh._pack_act_tile_major_contig(ffn_i8, M, K)
        print(f"[PROBE-SRAM] expect act:  {act_packed[:64].tobytes().hex()}", flush=True)
        packed_re, scales_re = sh._quantize_weight_tile(W_down, 0, N)
        print(f"[PROBE-SRAM] expect wgt:  {packed_re[:64].tobytes().hex()}", flush=True)
        print(f"[PROBE-SRAM] expect scale:{scales_re[:16].tobytes().hex()}", flush=True)
        # RTL DRAM at the descriptor source addresses vs python model.dram
        desc_w = struct.unpack("<15I", bytes(model.dram[sh.DESC_BASE - DRAM_BASE:
                                                       sh.DESC_BASE - DRAM_BASE + 60]))
        wgt_addr, scale_addr = desc_w[1], desc_w[3]
        for (nm, a, ln) in (("dram-wgt", wgt_addr, 64), ("dram-scale", scale_addr, 16)):
            raw = await bridge.segment_read_dram(a, ln)
            py = bytes(model.dram[a - DRAM_BASE:a - DRAM_BASE + ln])
            print(f"[PROBE-SRAM] {nm}@0x{a:08x}: rtl={raw.hex()} py={py.hex()}", flush=True)

    data = await bridge.segment_read_dram(mmul_out_addr, N * 4)
    off = mmul_out_addr - DRAM_BASE
    model.dram[off:off + N * 4] = data
    down_hw = np.frombuffer(bytes(model.dram[off:off + N * 4]),
                            dtype=np.float32).reshape(M, N)
    dump = os.environ.get("PROBE_DUMP")
    if dump:
        np.savez(dump, down_hw=down_hw, golden=golden,
                 scale=ffn_scale, ffn_i8=ffn_i8, wgt=W_down)
    nan_cnt, cos = _stats("mmul_down_hw", down_hw, golden)

    # VRESID (VADD) consuming the hardware down output, like segment-run wave 11
    residual1 = (rng.randn(M, N).astype(np.float32) * 2.0)
    b_operand = np.rint(down_hw * ffn_scale * sh.P10_RESID_SCALE).astype(np.int32)
    sh._reset_wave_arena()
    ops2 = []
    sh._add_vector_op(ops2, model, resid_addr, mmul_out_addr, l_out_addr,
                      sh.VEC_OP_ADD,
                      np.rint(residual1 * sh.P10_RESID_SCALE).astype(np.int32),
                      b_operand, N)
    n2 = n + segrun._ibex_schedule_chain(model, ops2, n)
    await bridge.segment_preload(bytes(model.dram))
    await bridge.segment_kick(n2)
    ok = await bridge.segment_wait(n2, 100_000_000, 20_000)
    assert ok, "VRESID wave timeout"

    data2 = await bridge.segment_read_dram(l_out_addr, N * 4)
    off2 = l_out_addr - DRAM_BASE
    model.dram[off2:off2 + N * 4] = data2
    l_hw = np.frombuffer(bytes(model.dram[off2:off2 + N * 4]),
                         dtype=np.int32).reshape(M, N)
    if os.environ.get("PROBE_VRESID"):
        # completion ring: entry cmd_id at 0x80008000 + cmd_id*32 = [cmd_id, status]
        comp = await bridge.segment_read_dram(0x80008000, 32 * 8)
        print(f"[PROBE] completions: {comp.hex()}", flush=True)
        for nm, a in (("resid", resid_addr), ("b", mmul_out_addr), ("l", l_out_addr)):
            raw = await bridge.segment_read_dram(a, 64)
            print(f"[PROBE] {nm}@0x{a:08x}: {raw[:32].hex()}", flush=True)
        for nm, a in (("vec_a", 0x20300000), ("vec_o", 0x20320000)):
            raw = await bridge._sram_backdoor_read(a, 64)
            print(f"[PROBE] {nm}@0x{a:08x}: {raw[:32].hex()}", flush=True)
    l_golden = (np.rint(residual1 * sh.P10_RESID_SCALE).astype(np.int32) +
                np.rint(golden * ffn_scale * sh.P10_RESID_SCALE).astype(np.int32))
    l_nan, l_cos = _stats("vresid_l_out", l_hw.astype(np.float64),
                          l_golden.astype(np.float64))

    if NAN_EXPECT:
        assert nan_cnt > 0, "expected NaN garbage on pre-fix RTL, got clean output"
        print("[PROBE] ROOT CAUSE CONFIRMED: NaN in MMUL-down readback "
              "(raw INT32 stored instead of dequantized FP32)", flush=True)
    else:
        assert nan_cnt == 0, f"MMUL-down output contains {nan_cnt} NaN values"
        assert cos > 0.999, f"MMUL-down cos={cos:.6f} below 0.999"
        assert l_nan == 0 and l_cos > 0.999, f"VRESID cos={l_cos:.6f}"
        print("[PROBE] FIX CONFIRMED: dequantized FP32 MMUL-down + VRESID "
              "match Func Model golden", flush=True)
