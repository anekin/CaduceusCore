#!/usr/bin/env python3
"""
rtl_soc_l19_probe.py — ISSUE-13B-followup: minimal L19 down-MMUL + VRESID repro.

Replays the segment-run L19 waves 8-11 (FFN-down tiles 768/768/512 + VRESID
VADD) through the on-chip Ibex, starting from the clean spike L18 input, and
dumps the B-operand DRAM region right before the VADD dispatch.

Run: MODULE=sim.rtl_soc_l19_probe TOPLEVEL=tb_soc_ibex
"""
import os
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "ggml-npu"))

import cocotb  # noqa: E402
from cocotb_bridge import CocotbBridge, DRAM_BASE  # noqa: E402
from func_model import FuncModel  # noqa: E402
from golden_executor import GoldenMXU  # noqa: E402
from q4_dequant import load_weights_from_gguf  # noqa: E402
from quantize import quantize_int4_per_block  # noqa: E402
import spike_host as sh  # noqa: E402
import rtl_soc_segment_run as segrun  # noqa: E402

L = 19
M = 1
H = 2048
I = 11008
P10 = sh.P10_RESID_SCALE

DUMP_B = int(os.environ.get("DUMP_B", "0"))


def _cos(a, b):
    return sh._cosine_similarity(np.asarray(a, np.float64).ravel(),
                                 np.asarray(b, np.float64).ravel())


@cocotb.test()
async def test_l19_down_vresid(dut):
    bridge = CocotbBridge(dut)
    await bridge.start_clock()
    await bridge.reset(5)

    model_path = os.environ.get("QWEN3B_GGUF",
                                str(Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"))
    print(f"[L19PROBE] loading weights from {model_path}", flush=True)
    weights = load_weights_from_gguf(model_path)
    spike = np.load(str(_REPO / "build" / "evidence" / "ph10-36layer-spike.npz"),
                    allow_pickle=True)
    hidden = spike["layer_18_output"].astype(np.float32)
    ibex = np.load(str(_REPO / "build" / "evidence" / "ph10-36layer-ibex-checkpoints.npz"),
                   allow_pickle=True)
    l19_ref_lout = ibex["hw_layer_19_output"].astype(np.float32).ravel()

    normed = sh._forward_rmsnorm(hidden, weights[f"blk.{L}.attn_norm.weight"], sh.QWEN_RMS_EPS)
    q = normed @ weights[f"blk.{L}.attn_q.weight"].T
    k = normed @ weights[f"blk.{L}.attn_k.weight"].T
    v = normed @ weights[f"blk.{L}.attn_v.weight"].T
    attn_out = sh._forward_attention(q, k, v, n_heads=16, n_kv_heads=2, head_dim=128)
    o = attn_out @ weights[f"blk.{L}.attn_output.weight"].T
    residual1 = hidden + o
    ffn_input = sh._forward_rmsnorm(residual1, weights[f"blk.{L}.ffn_norm.weight"], sh.QWEN_RMS_EPS)
    gate = ffn_input @ weights[f"blk.{L}.ffn_gate.weight"].T
    up = ffn_input @ weights[f"blk.{L}.ffn_up.weight"].T
    silu = sh._forward_silu(gate)
    ffn_hidden = silu * up
    ffn_i8, ffn_scale = sh._int8_quantize(ffn_hidden)

    model = FuncModel(dram_mb=8, sram_kb=4096)
    sh._reset_act_allocator()
    ffn_out_in_addr = sh._act_alloc(((I + 63) // 64) * 4096)
    ffn_out_addr = sh._act_alloc(H * 4)
    l_out_addr = sh._act_alloc(H * 4)
    resid_addr = sh._act_alloc(H * 4)
    print(f"[L19PROBE] ffn_out=0x{ffn_out_addr:08x} l_out=0x{l_out_addr:08x} resid=0x{resid_addr:08x}",
          flush=True)

    W_down = weights[f"blk.{L}.ffn_down.weight"].astype(np.float32)
    ring_offset = 0
    tile_n_dn = 768
    n_tiles_dn = (H + tile_n_dn - 1) // tile_n_dn
    for t in range(n_tiles_dn):
        sh._reset_wave_arena()
        ops = []
        sh._add_mmul_tiles_phase10(ops, model, ffn_out_in_addr, ffn_out_addr,
                                   W_down.T, M, I, H, ffn_i8, tile_n_dn, t, t + 1)
        n = segrun._ibex_schedule_chain(model, ops, ring_offset)
        ring_offset += n
        await bridge.segment_preload(bytes(model.dram))
        await bridge.segment_kick(ring_offset)
        ok = await bridge.segment_wait(ring_offset, 100_000_000, 20_000)
        assert ok, f"down tile {t} timeout"
        if t == n_tiles_dn - 1:
            data = await bridge.segment_read_dram(ffn_out_addr, H * 4)
            off = ffn_out_addr - DRAM_BASE
            model.dram[off:off + H * 4] = data

    down_out_hw = np.frombuffer(bytes(model.dram[ffn_out_addr - DRAM_BASE:
                                                ffn_out_addr - DRAM_BASE + H * 4]),
                                dtype=np.float32).ravel()
    print(f"[L19PROBE] down_out_hw min={np.nanmin(down_out_hw):.4f} "
          f"max={np.nanmax(down_out_hw):.4f} nan={int(np.isnan(down_out_hw).sum())}",
          flush=True)

    A = np.rint(residual1 * P10).astype(np.int32)
    B = np.rint(down_out_hw * ffn_scale * P10).astype(np.int32)
    print(f"[L19PROBE] A range [{A.min()},{A.max()}] B range [{B.min()},{B.max()}] "
          f"ffn_scale={ffn_scale:.6f}", flush=True)

    sh._reset_wave_arena()
    ops2 = []
    sh._add_vector_op(ops2, model, resid_addr, ffn_out_addr, l_out_addr,
                      sh.VEC_OP_ADD, A, B, H)
    n2 = ring_offset + segrun._ibex_schedule_chain(model, ops2, ring_offset)
    await bridge.segment_preload(bytes(model.dram))
    if DUMP_B:
        raw = await bridge.segment_read_dram(ffn_out_addr, 64)
        print(f"[L19PROBE] DRAM ffn_out (B region) before VADD: {raw[:32].hex()}",
              flush=True)
        py = bytes(model.dram[ffn_out_addr - DRAM_BASE:
                              ffn_out_addr - DRAM_BASE + 32])
        print(f"[L19PROBE] python model.dram ffn_out: {py.hex()}", flush=True)
    await bridge.segment_kick(n2)
    ok = await bridge.segment_wait(n2, 100_000_000, 20_000)
    assert ok, "VRESID timeout"

    data2 = await bridge.segment_read_dram(l_out_addr, H * 4)
    off2 = l_out_addr - DRAM_BASE
    model.dram[off2:off2 + H * 4] = data2
    l_hw = np.frombuffer(bytes(model.dram[off2:off2 + H * 4]),
                         dtype=np.int32).ravel()
    l_golden = A.astype(np.int64) + B.astype(np.int64)
    print(f"[L19PROBE] l_out range [{l_hw.min()},{l_hw.max()}] "
          f"expected [{l_golden.min()},{l_golden.max()}]", flush=True)
    print(f"[L19PROBE] l_out vs expected cos={_cos(l_hw, l_golden):.6f}", flush=True)
    print(f"[L19PROBE] l_out vs segment-run-stored-L19 cos={_cos(l_hw, l19_ref_lout):.6f}",
          flush=True)

    ok_all = np.allclose(l_hw.astype(np.int64), l_golden)
    print(f"[L19PROBE] {'REPRO MATCH' if ok_all else 'REPRO MISMATCH'}: "
          f"hardware l_out {'==' if ok_all else '!='} A+B golden", flush=True)

    if os.environ.get("TWICE"):
        # Run the identical down+VRESID sequence a second time in the same
        # session, reusing the FuncModel (state carried like segment-to-segment).
        ring2 = ring_offset + n2 - ring_offset  # cumulative offset after wave1
        cur = ring_offset + 3
        for t in range(n_tiles_dn):
            sh._reset_wave_arena()
            ops = []
            sh._add_mmul_tiles_phase10(ops, model, ffn_out_in_addr, ffn_out_addr,
                                       W_down.T, M, I, H, ffn_i8, tile_n_dn, t, t + 1)
            n = segrun._ibex_schedule_chain(model, ops, cur)
            cur += n
            await bridge.segment_preload(bytes(model.dram))
            await bridge.segment_kick(cur)
            ok = await bridge.segment_wait(cur, 100_000_000, 20_000)
            assert ok, f"twice down tile {t} timeout"
            if t == n_tiles_dn - 1:
                data = await bridge.segment_read_dram(ffn_out_addr, H * 4)
                off = ffn_out_addr - DRAM_BASE
                model.dram[off:off + H * 4] = data
        down2 = np.frombuffer(bytes(model.dram[ffn_out_addr - DRAM_BASE:
                                              ffn_out_addr - DRAM_BASE + H * 4]),
                              dtype=np.float32).ravel()
        print(f"[L19PROBE] TWICE down min={np.nanmin(down2):.4f} max={np.nanmax(down2):.4f}",
              flush=True)
        B2 = np.rint(down2 * ffn_scale * P10).astype(np.int32)
        sh._reset_wave_arena()
        ops2 = []
        sh._add_vector_op(ops2, model, resid_addr, ffn_out_addr, l_out_addr,
                          sh.VEC_OP_ADD, A, B2, H)
        n3 = cur + segrun._ibex_schedule_chain(model, ops2, cur)
        await bridge.segment_preload(bytes(model.dram))
        await bridge.segment_kick(n3)
        ok = await bridge.segment_wait(n3, 100_000_000, 20_000)
        assert ok, "twice VRESID timeout"
        data3 = await bridge.segment_read_dram(l_out_addr, H * 4)
        off3 = l_out_addr - DRAM_BASE
        model.dram[off3:off3 + H * 4] = data3
        l2 = np.frombuffer(bytes(model.dram[off3:off3 + H * 4]),
                           dtype=np.int32).ravel()
        print(f"[L19PROBE] TWICE l_out range [{l2.min()},{l2.max()}]", flush=True)
        print(f"[L19PROBE] TWICE cos vs golden={_cos(l2, (A.astype(np.int64)+B2.astype(np.int64))):.6f}",
              flush=True)
