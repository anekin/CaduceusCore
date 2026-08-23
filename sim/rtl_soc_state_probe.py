#!/usr/bin/env python3
"""
rtl_soc_state_probe.py — reproduce the segment-run state: run layer(s) via
ibex_execute_layer, then the L19 down-MMUL+VRESID, and compare vs golden.

STAGE1 env: comma-separated layer list to run first (e.g. STAGE1=0 or STAGE1=9,10).
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
from q4_dequant import load_weights_from_gguf  # noqa: E402
import spike_host as sh  # noqa: E402
import rtl_soc_segment_run as segrun  # noqa: E402

L = 19
M = 1
H = 2048
I = 11008
P10 = sh.P10_RESID_SCALE


def _cos(a, b):
    return sh._cosine_similarity(np.asarray(a, np.float64).ravel(),
                                 np.asarray(b, np.float64).ravel())


@cocotb.test()
async def test_state_probe(dut):
    bridge = CocotbBridge(dut)
    await bridge.start_clock()
    await bridge.reset(5)

    model_path = os.environ.get("QWEN3B_GGUF",
                                str(Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"))
    print(f"[STATE] loading weights from {model_path}", flush=True)
    weights = load_weights_from_gguf(model_path)
    spike = np.load(str(_REPO / "build" / "evidence" / "ph10-36layer-spike.npz"),
                    allow_pickle=True)
    ibex = np.load(str(_REPO / "build" / "evidence" / "ph10-36layer-ibex-checkpoints.npz"),
                   allow_pickle=True)
    l19_ref = ibex["hw_layer_19_output"].astype(np.float32).ravel()

    dims = {
        "hidden_size": H, "intermediate_size": I, "q_dim": 2048, "kv_dim": 256,
        "num_heads": 16, "num_kv_heads": 2, "heads": 16, "kv_heads": 2,
        "head_dim": 128, "num_hidden_layers": 36,
    }
    model = FuncModel(dram_mb=8, sram_kb=4096)
    ring_offset = 0

    stage1 = [int(x) for x in os.environ.get("STAGE1", "0").split(",") if x]
    hidden = spike["input_embedding"].astype(np.float32).copy()
    for Ls in stage1:
        print(f"[STATE] dispatching full L{Ls} via ibex_execute_layer", flush=True)
        hw_l_out, consumed = await segrun.ibex_execute_layer(
            bridge, model, hidden, weights, Ls, dims, M=1, ring_offset=ring_offset)
        ring_offset += consumed
        hidden = (hw_l_out.astype(np.float32) / P10).copy()
        print(f"[STATE] L{Ls} done ring_offset={ring_offset} "
              f"hw_range=[{hw_l_out.min()},{hw_l_out.max()}]", flush=True)

    # L19 down-MMUL (3 tiles) + VRESID, exactly like the standalone probe
    hidden19 = spike["layer_18_output"].astype(np.float32)
    normed = sh._forward_rmsnorm(hidden19, weights[f"blk.{L}.attn_norm.weight"], sh.QWEN_RMS_EPS)
    q = normed @ weights[f"blk.{L}.attn_q.weight"].T
    k = normed @ weights[f"blk.{L}.attn_k.weight"].T
    v = normed @ weights[f"blk.{L}.attn_v.weight"].T
    attn_out = sh._forward_attention(q, k, v, n_heads=16, n_kv_heads=2, head_dim=128)
    o = attn_out @ weights[f"blk.{L}.attn_output.weight"].T
    residual1 = hidden19 + o
    ffn_input = sh._forward_rmsnorm(residual1, weights[f"blk.{L}.ffn_norm.weight"], sh.QWEN_RMS_EPS)
    gate = ffn_input @ weights[f"blk.{L}.ffn_gate.weight"].T
    up = ffn_input @ weights[f"blk.{L}.ffn_up.weight"].T
    silu = sh._forward_silu(gate)
    ffn_hidden = silu * up
    ffn_i8, ffn_scale = sh._int8_quantize(ffn_hidden)

    sh._reset_act_allocator()
    ffn_out_in_addr = sh._act_alloc(((I + 63) // 64) * 4096)
    ffn_out_addr = sh._act_alloc(H * 4)
    l_out_addr = sh._act_alloc(H * 4)
    resid_addr = sh._act_alloc(H * 4)

    W_down = weights[f"blk.{L}.ffn_down.weight"].astype(np.float32)
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

    down_hw = np.frombuffer(bytes(model.dram[ffn_out_addr - DRAM_BASE:
                                            ffn_out_addr - DRAM_BASE + H * 4]),
                            dtype=np.float32).ravel()
    print(f"[STATE] L19 down min={np.nanmin(down_hw):.4f} max={np.nanmax(down_hw):.4f} "
          f"nan={int(np.isnan(down_hw).sum())}", flush=True)

    A = np.rint(residual1 * P10).astype(np.int32)
    B = np.rint(down_hw * ffn_scale * P10).astype(np.int32)
    sh._reset_wave_arena()
    ops2 = []
    sh._add_vector_op(ops2, model, resid_addr, ffn_out_addr, l_out_addr,
                      sh.VEC_OP_ADD, A, B, H)
    n2 = ring_offset + segrun._ibex_schedule_chain(model, ops2, ring_offset)
    await bridge.segment_preload(bytes(model.dram))
    await bridge.segment_kick(n2)
    ok = await bridge.segment_wait(n2, 100_000_000, 20_000)
    assert ok, "VRESID timeout"

    data2 = await bridge.segment_read_dram(l_out_addr, H * 4)
    off2 = l_out_addr - DRAM_BASE
    model.dram[off2:off2 + H * 4] = data2
    l_hw = np.frombuffer(bytes(model.dram[off2:off2 + H * 4]),
                         dtype=np.int32).ravel()
    l_golden = A.astype(np.int64) + B.astype(np.int64)
    print(f"[STATE] l_out range [{l_hw.min()},{l_hw.max()}] "
          f"expected [{l_golden.min()},{l_golden.max()}]", flush=True)
    print(f"[STATE] l_out vs golden cos={_cos(l_hw, l_golden):.6f}", flush=True)
    print(f"[STATE] l_out vs segment-run-L19 cos={_cos(l_hw, l19_ref):.6f}", flush=True)
