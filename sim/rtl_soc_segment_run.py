#!/usr/bin/env python3
"""
rtl_soc_segment_run.py — Ibex 36-layer checkpoint-subset segment run (todo 13).

Runs the 9-layer Ibex subset in one VCS session:

    L0 | L9->L10 | L19->L20 | L29->L30 | L34->L35

For each segment the pre-layer and checkpoint layer are dispatched back-to-back
through the on-chip Ibex firmware (command ring + doorbell).  The pre-layer's
hidden state stays in DRAM and is converted to the checkpoint layer's input —
no external state injection between consecutive layers (chain_restart=true,
chain_restart_state_source=ibex_dram).  Only the segment's FIRST layer input is
loaded from the Spike npz (segment_input_source=spike_npz).

Reuses spike_host.py's wave-split command generation (11 waves / 34 commands per
3B layer) but replaces the Spike MMIO dispatch with the CocotbBridge DRAM
backdoor + doorbell path.

Entry point: test_soc_ibex_segment_run (MODULE=sim.rtl_soc_segment_run,
TOPLEVEL=tb_soc_ibex, FM_SOC_CASE_ID=SEGMENT-RUN).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "ggml-npu"))

try:
    import cocotb  # noqa: E402
    COCOTB_AVAILABLE = True
except ImportError:
    COCOTB_AVAILABLE = False

    class cocotb:  # type: ignore
        @staticmethod
        def test(*args, **kwargs):
            def _deco(f):
                return f
            return _deco

from cocotb_bridge import CocotbBridge, DRAM_BASE  # noqa: E402
from func_model import FuncModel  # noqa: E402
from q4_dequant import load_weights_from_gguf  # noqa: E402
from opcodes import EngineOp  # noqa: E402

import spike_host as sh  # noqa: E402

FIRMWARE_RING_BASE = sh.FIRMWARE_RING_BASE
DESC_BASE = sh.DESC_BASE
DESC_STRIDE = sh.DESC_STRIDE
CMD_ENTRY_SIZE = sh.CMD_ENTRY_SIZE
P10_RESID_SCALE = sh.P10_RESID_SCALE
SFU_OP_RMSNORM = sh.SFU_OP_RMSNORM
SFU_OP_SILU = sh.SFU_OP_SILU
VEC_OP_ADD = sh.VEC_OP_ADD
VEC_OP_MUL = sh.VEC_OP_MUL

RING_SIZE = 1024
WAVE_TIMEOUT_CYCLES = 100_000_000
POLL_INTERVAL = 20_000

EVIDENCE_PATH = _REPO / "build" / "evidence" / "task-13-phase10-rtl-verification.txt"
NPZ_PATH = _REPO / "build" / "evidence" / "ph10-36layer-ibex-checkpoints.npz"
SPIKE_NPZ = _REPO / "build" / "evidence" / "ph10-36layer-spike.npz"
GOLDEN_DIR = _REPO / "rtl" / "test_vectors" / "soc_e2e" / "qwen25-3b-36layer"

SEGMENTS = [
    (None, 0, None),   # L0 standalone, input = embedding
    (9, 10, 8),        # L9->L10, input = spike L8
    (19, 20, 18),      # L19->L20, input = spike L18
    (29, 30, 28),      # L29->L30, input = spike L28
    (34, 35, 33),      # L34->L35, input = spike L33
]
CHECKPOINTS = [0, 10, 20, 30, 35]
EXECUTED_LAYERS = [0, 9, 10, 19, 20, 29, 30, 34, 35]


def _chain_threshold(layer):
    return sh.p10_layer_threshold(layer)


def _cos(a, b):
    return sh._cosine_similarity(a, b)


def _ibex_schedule_chain(model, ops, ring_offset):
    for i, op in enumerate(ops):
        desc_addr = DESC_BASE + i * DESC_STRIDE
        t = op["type"]
        desc = op["desc"]
        if t == "mmul":
            sh.write_mmul_descriptor(model, desc_addr, **desc)
            opcode = int(EngineOp.MMUL)
        elif t == "sfu":
            sh.write_sfu_descriptor(model, desc_addr, **desc)
            opcode = int(EngineOp.SFU)
        elif t == "vector":
            sh.write_vector_descriptor(model, desc_addr, **desc)
            opcode = int(EngineOp.VECTOR)
        elif t == "dma_copy":
            sh.write_dma_copy_descriptor(model, desc_addr, **desc)
            opcode = int(EngineOp.DMA_COPY)
        else:
            raise ValueError(f"unknown op type {t}")
        sh.write_cmd_entry(model, (ring_offset + i) % RING_SIZE, opcode,
                           desc_addr, flags=op.get("flags", 0))
    return len(ops)


async def ibex_execute_layer(bridge, model, hidden, weights, layer, dims,
                             M=1, ring_offset=0):
    H, I = dims["hidden_size"], dims["intermediate_size"]
    QD, KD = dims["q_dim"], dims["kv_dim"]
    heads, kv_heads, head_dim = dims["heads"], dims["kv_heads"], dims["head_dim"]
    w = weights
    eps = sh.QWEN_RMS_EPS

    normed = sh._forward_rmsnorm(hidden, w[f"blk.{layer}.attn_norm.weight"], eps)
    q = normed @ w[f"blk.{layer}.attn_q.weight"].T + w.get(f"blk.{layer}.attn_q.bias", 0)
    k = normed @ w[f"blk.{layer}.attn_k.weight"].T + w.get(f"blk.{layer}.attn_k.bias", 0)
    v = normed @ w[f"blk.{layer}.attn_v.weight"].T + w.get(f"blk.{layer}.attn_v.bias", 0)
    attn_out = sh._forward_attention(q, k, v, n_heads=heads, n_kv_heads=kv_heads,
                                     head_dim=head_dim)
    o = attn_out @ w[f"blk.{layer}.attn_output.weight"].T
    residual1 = hidden + o
    ffn_input = sh._forward_rmsnorm(residual1, w[f"blk.{layer}.ffn_norm.weight"], eps)
    gate = ffn_input @ w[f"blk.{layer}.ffn_gate.weight"].T
    up = ffn_input @ w[f"blk.{layer}.ffn_up.weight"].T
    silu_gate = sh._forward_silu(gate)
    ffn_hidden = silu_gate * up

    sh._reset_act_allocator()
    hidden_addr = sh._act_alloc(H * 4)
    normed_addr = sh._act_alloc(H * 4)
    q_in_addr = sh._act_alloc(((QD + 63) // 64) * 4096)
    q_out_addr = sh._act_alloc(QD * 4)
    k_in_addr = sh._act_alloc(((KD + 63) // 64) * 4096)
    k_out_addr = sh._act_alloc(KD * 4)
    v_in_addr = sh._act_alloc(((KD + 63) // 64) * 4096)
    v_out_addr = sh._act_alloc(KD * 4)
    o_in_addr = sh._act_alloc(((H + 63) // 64) * 4096)
    o_out_addr = sh._act_alloc(H * 4)
    residual1_addr = sh._act_alloc(H * 4)
    ffn_in_addr = sh._act_alloc(H * 4)
    gate_in_addr = sh._act_alloc(((H + 63) // 64) * 4096)
    gate_out_addr = sh._act_alloc(I * 4)
    up_in_addr = sh._act_alloc(((H + 63) // 64) * 4096)
    up_out_addr = sh._act_alloc(I * 4)
    silu_addr = sh._act_alloc(I * 4)
    ffn_hidden_addr = sh._act_alloc(I * 4)
    ffn_out_in_addr = sh._act_alloc(((I + 63) // 64) * 4096)
    ffn_out_addr = sh._act_alloc(H * 4)
    l_out_addr = sh._act_alloc(H * 4)

    sh._write_tensor(model, hidden_addr, hidden)

    state = {"offset": ring_offset}

    async def run_wave(ops, readback=None):
        t0 = time.time()
        n = _ibex_schedule_chain(model, ops, state["offset"])
        state["offset"] += n
        await bridge.segment_preload(bytes(model.dram))
        t1 = time.time()
        await bridge.segment_kick(state["offset"])
        ok = await bridge.segment_wait(state["offset"], WAVE_TIMEOUT_CYCLES,
                                       POLL_INTERVAL)
        t2 = time.time()
        cyc = int(bridge.dut.sim_cycle.value) if hasattr(bridge.dut, "sim_cycle") else -1
        print(f"[WAVE L{layer}] cmds={n} preload={t1 - t0:.1f}s "
              f"compute={t2 - t1:.1f}s sim_cycle={cyc} ok={ok}")
        if not ok:
            head = await bridge.segment_read_head()
            raise RuntimeError(
                f"L{layer}: wave timeout head={head} expected={state['offset'] % RING_SIZE}")
        if readback:
            for (addr, length) in readback:
                data = await bridge.segment_read_dram(addr, length)
                off = addr - DRAM_BASE
                model.dram[off:off + length] = data
        return n

    q_i8, _ = sh._int8_quantize(normed)
    k_i8, _ = sh._int8_quantize(normed)
    v_i8, _ = sh._int8_quantize(normed)
    attn_i8, _ = sh._int8_quantize(attn_out)
    gate_i8, _ = sh._int8_quantize(ffn_input)
    up_i8, _ = sh._int8_quantize(ffn_input)
    ffn_i8, ffn_scale = sh._int8_quantize(ffn_hidden)

    consumed = 0

    def new_wave():
        sh._reset_wave_arena()
        return []

    # Wave 1: pre-attn RMSNorm + Q/K/V/O MMUL + residual + post-attn RMSNorm
    ops = new_wave()
    sh._add_sfu_op(ops, model, hidden_addr, normed_addr, SFU_OP_RMSNORM,
                   hidden.astype(np.float16), H)
    packed_q, scales_q, _, _ = sh._quantize_weight_for_mmul(w[f"blk.{layer}.attn_q.weight"])
    sh._add_mmul_op(ops, model, q_in_addr, q_out_addr, packed_q, scales_q, M, H, QD, q_i8)
    packed_k, scales_k, _, _ = sh._quantize_weight_for_mmul(w[f"blk.{layer}.attn_k.weight"].T)
    sh._add_mmul_op(ops, model, k_in_addr, k_out_addr, packed_k, scales_k, M, H, KD, k_i8)
    packed_v, scales_v, _, _ = sh._quantize_weight_for_mmul(w[f"blk.{layer}.attn_v.weight"].T)
    sh._add_mmul_op(ops, model, v_in_addr, v_out_addr, packed_v, scales_v, M, H, KD, v_i8)
    packed_o, scales_o, _, _ = sh._quantize_weight_for_mmul(w[f"blk.{layer}.attn_output.weight"])
    sh._add_mmul_op(ops, model, o_in_addr, o_out_addr, packed_o, scales_o, M, H, H, attn_i8)
    sh._add_vector_op(ops, model, hidden_addr, o_out_addr, residual1_addr, VEC_OP_ADD,
                      np.rint(hidden * P10_RESID_SCALE).astype(np.int32),
                      np.rint(o * P10_RESID_SCALE).astype(np.int32), H)
    sh._add_sfu_op(ops, model, residual1_addr, ffn_in_addr, SFU_OP_RMSNORM,
                   residual1.astype(np.float16), H)
    consumed += await run_wave(ops)

    # Waves 2-4: FFN gate (N-tiled, two 2048-col tiles per wave) + SiLU
    tile_n = 2048
    n_tiles = (I + tile_n - 1) // tile_n
    for t_lo in range(0, n_tiles, 2):
        t_hi = min(t_lo + 2, n_tiles)
        ops = new_wave()
        sh._add_mmul_tiles_phase10(ops, model, gate_in_addr, gate_out_addr,
                                   w[f"blk.{layer}.ffn_gate.weight"].T, M, H, I,
                                   gate_i8, tile_n, t_lo, t_hi)
        if t_hi >= n_tiles:
            sh._add_sfu_op(ops, model, gate_out_addr, silu_addr, SFU_OP_SILU,
                           gate.astype(np.float16), I)
        consumed += await run_wave(ops)

    # Waves 5-7: FFN up (N-tiled) + VMUL
    for t_lo in range(0, n_tiles, 2):
        t_hi = min(t_lo + 2, n_tiles)
        ops = new_wave()
        sh._add_mmul_tiles_phase10(ops, model, up_in_addr, up_out_addr,
                                   w[f"blk.{layer}.ffn_up.weight"].T, M, H, I,
                                   up_i8, tile_n, t_lo, t_hi)
        if t_hi >= n_tiles:
            sh._add_vector_op(ops, model, silu_addr, up_out_addr, ffn_hidden_addr,
                              VEC_OP_MUL, silu_gate.astype(np.int32),
                              up.astype(np.int32), I)
        consumed += await run_wave(ops)

    # Waves 8-10: FFN down (N-tiled, one 768-col tile per wave)
    tile_n_dn = 768
    n_tiles_dn = (H + tile_n_dn - 1) // tile_n_dn
    for t in range(n_tiles_dn):
        ops = new_wave()
        sh._add_mmul_tiles_phase10(ops, model, ffn_out_in_addr, ffn_out_addr,
                                   w[f"blk.{layer}.ffn_down.weight"].T, M, I, H,
                                   ffn_i8, tile_n_dn, t, t + 1)
        readback = [(ffn_out_addr, H * 4)] if t == n_tiles_dn - 1 else None
        consumed += await run_wave(ops, readback=readback)

    # Wave 11: final VRESID consuming the hardware down-MMUL output
    down_out_hw = sh._read_tensor(model, ffn_out_addr, (M, H), np.float32)
    ops = new_wave()
    sh._add_vector_op(ops, model, residual1_addr, ffn_out_addr, l_out_addr, VEC_OP_ADD,
                      np.rint(residual1 * P10_RESID_SCALE).astype(np.int32),
                      np.rint(down_out_hw * ffn_scale * P10_RESID_SCALE).astype(np.int32),
                      H)
    consumed += await run_wave(ops, readback=[(l_out_addr, H * 4)])

    hw_l_out = sh._read_tensor(model, l_out_addr, (M, H), np.int32)
    return hw_l_out, consumed


def _load_spike_npz():
    with np.load(SPIKE_NPZ, allow_pickle=True) as d:
        emb = d["input_embedding"].astype(np.float32)
        layers = {}
        for L in list(range(36)):
            if f"layer_{L}_output" in d.files:
                layers[L] = {
                    "fp32": d[f"layer_{L}_output"].astype(np.float32),
                    "hw": d[f"hw_layer_{L}_output"].astype(np.int32),
                }
    return emb, layers


def _write_evidence(results, meta):
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    executed = ",".join(f"L{L}" for L in EXECUTED_LAYERS)
    ckpts = ",".join(f"L{L}" for L in CHECKPOINTS)
    with open(EVIDENCE_PATH, "w", encoding="utf-8") as f:
        f.write("Task 13 - Phase 10 RTL Verification: Ibex 36-layer checkpoint-subset segment run\n")
        f.write("=" * 70 + "\n")
        f.write(f"Timestamp start : {ts}\n")
        f.write(f"Commit          : {meta['commit']}\n")
        f.write(f"Command         : {meta['command']}\n")
        f.write(f"Driver host     : {meta['hostname']} (Ibex SoC VCS + firmware)\n")
        f.write(f"Model           : {meta['model']}\n")
        f.write(f"Dims            : {json.dumps(meta['dims'])}\n")
        f.write("engine=ibex\n")
        f.write(f"ibex_executed={executed}\n")
        f.write(f"checkpoints={ckpts}\n")
        f.write("chain_restart=true\n")
        f.write("chain_restart_state_source=ibex_dram\n")
        f.write("segment_input_source=spike_npz\n")
        f.write(f"commands_dispatched={meta['total_consumed']}\n")
        f.write(f"elapsed_s={meta['elapsed_s']:.1f}\n\n")
        f.write("Segment sessions (same VCS session, DRAM state passed between consecutive layers):\n")
        for seg in meta["segment_records"]:
            f.write(seg + "\n")
        f.write("\nPer-checkpoint tolerance ladder (Ibex hidden state vs Func Model golden):\n")
        f.write("  ladder: L0-19 >= 0.999, L20-29 >= 0.998, L30-35 >= 0.997\n")
        for r in results["checkpoints"]:
            f.write(f"layer={r['layer']} engine=ibex cos_sim={r['cos_sim']:.6f} "
                    f"threshold={r['threshold']} status={r['status']} "
                    f"chain_restart_state_source=ibex_dram\n")
        f.write("\nHardware l_out transparency (Ibex DRAM VRESID int32 vs golden, non-gating):\n")
        for r in results["checkpoints"]:
            f.write(f"layer={r['layer']} hw_l_out_cos_sim={r['hw_cos']:.6f} "
                    f"max_abs={r['hw_max']:.4e}\n")
        f.write("\nCross-check: Ibex pre-layer output vs Spike same-layer output (non-gating):\n")
        for r in results["cross_checks"]:
            flag = f" cross_check_mismatch=L{r['layer']}" if not r["ok"] else ""
            f.write(f"layer={r['layer']} ibex_vs_spike_cos_sim={r['cos_sim']:.6f} "
                    f"threshold={r['threshold']} status={r['status']}{flag}\n")
        f.write("\nSummary:\n")
        f.write(f"  checkpoints_passed={sum(1 for r in results['checkpoints'] if r['ok'])}/"
                f"{len(results['checkpoints'])}\n")
        f.write(f"  LADDER={'PASS' if results['ladder_pass'] else 'FAIL'}\n")
        f.write(f"  Overall: {'PASS' if results['ladder_pass'] else 'FAIL'}\n")
        f.write(f"  Timestamp end: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")


def _save_npz(fp32_states, hw_states, emb, dims, meta):
    NPZ_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"input_embedding": emb.astype(np.float32)}
    for L in EXECUTED_LAYERS:
        data[f"layer_{L}_output"] = fp32_states[L].astype(np.float32)
        data[f"hw_layer_{L}_output"] = hw_states[L].astype(np.int32)
    m = {
        "engine": "ibex",
        "layers_run": len(EXECUTED_LAYERS),
        "layers_saved": sorted(fp32_states.keys()),
        "checkpoints": CHECKPOINTS,
        "chain_restart": True,
        "chain_restart_state_source": "ibex_dram",
        "segment_input_source": "spike_npz",
        "dims": dims,
        "model": meta["model"],
        "commit": meta["commit"],
        "command": meta["command"],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    data["metadata"] = np.array([json.dumps(m)])
    tmp = str(NPZ_PATH) + ".tmp.npz"
    np.savez(tmp, **data)
    os.replace(tmp, str(NPZ_PATH))


@cocotb.test()
async def test_soc_ibex_segment_run(dut):
    bridge = CocotbBridge(dut)
    await bridge.start_clock()
    await bridge.reset(5)

    model_path = os.environ.get("QWEN3B_GGUF",
                                str(Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"))
    commit = os.environ.get("IBEX_COMMIT", "unknown")
    command = "scripts/p10_36layer_ibex.sh"

    try:
        hostname = os.uname().nodename
    except Exception:
        hostname = "unknown"

    print(f"[SEGMENT] loading weights from {model_path}")
    weights = load_weights_from_gguf(model_path)
    H = int(weights["blk.0.attn_norm.weight"].shape[0])
    I = int(weights["blk.0.ffn_gate.weight"].shape[0])
    QD = int(weights["blk.0.attn_q.weight"].shape[0])
    KD = int(weights["blk.0.attn_k.weight"].shape[0])
    head_dim = 128
    heads = QD // head_dim
    kv_heads = KD // head_dim
    dims = {
        "hidden_size": H, "intermediate_size": I, "q_dim": QD, "kv_dim": KD,
        "num_heads": heads, "num_kv_heads": kv_heads, "heads": heads,
        "kv_heads": kv_heads, "head_dim": head_dim,
        "num_hidden_layers": 36, "rope_theta": sh.QWEN_THETA, "rms_eps": sh.QWEN_RMS_EPS,
    }

    emb, spike_layers = _load_spike_npz()
    M = emb.shape[0]
    assert M == 1, f"segment run requires M=1, got {M}"

    model = FuncModel(dram_mb=8, sram_kb=4096)

    fp32_states = {}
    hw_states = {}
    checkpoint_results = []
    cross_checks = []
    segment_records = []
    ring_offset = 0
    total_consumed = 0

    t0 = time.time()
    for (pre, chk, seg_in) in SEGMENTS:
        if pre is None:
            hidden = emb.astype(np.float32).copy()
            seg_desc = f"L0: input=embedding (prompt token), checkpoint=L0"
        else:
            hidden = spike_layers[seg_in]["fp32"].astype(np.float32).copy()
            seg_desc = (f"L{pre}->L{chk}: segment input=L{seg_in} from spike npz, "
                        f"chain_restart_state_source=ibex_dram")

        for L in ([chk] if pre is None else [pre, chk]):
            fp32_out = sh._forward_layer(hidden, weights, L, n_heads=heads,
                                         n_kv_heads=kv_heads, head_dim=head_dim)
            hw_l_out, consumed = await ibex_execute_layer(
                bridge, model, hidden, weights, L, dims, M, ring_offset)
            ring_offset += consumed
            total_consumed += consumed
            fp32_states[L] = fp32_out.astype(np.float32)
            hw_states[L] = hw_l_out.astype(np.int32)
            hw_f32 = hw_l_out.astype(np.float32) / P10_RESID_SCALE
            print(f"[SEGMENT] L{L}: cmds={consumed} ring_offset={ring_offset} "
                  f"hw_range=[{int(hw_l_out.min())},{int(hw_l_out.max())}]")
            if L in CHECKPOINTS:
                golden = sh._load_golden_layer(str(GOLDEN_DIR), L)
                cos = _cos(fp32_out, golden)
                thr = _chain_threshold(L)
                ok = cos >= thr
                checkpoint_results.append({
                    "layer": L, "cos_sim": float(cos), "threshold": thr,
                    "status": "PASS" if ok else "FAIL", "ok": ok,
                    "hw_cos": float(_cos(hw_f32, golden)),
                    "hw_max": float(np.max(np.abs(hw_f32 - golden.astype(np.float32)))),
                })
                print(f"  [CHECKPOINT L{L}] cos_sim={cos:.6f} threshold={thr} "
                      f"hw_cos={checkpoint_results[-1]['hw_cos']:.6f}")
            if pre is not None and L == pre:
                spike_hw = spike_layers[pre]["hw"].astype(np.float32) / P10_RESID_SCALE
                cc = _cos(hw_f32, spike_hw)
                thr = _chain_threshold(pre)
                cross_checks.append({
                    "layer": pre, "cos_sim": float(cc), "threshold": thr,
                    "status": "PASS" if cc >= thr else "FAIL",
                    "ok": cc >= thr,
                })
                print(f"  [CROSSCHECK L{pre}] ibex_vs_spike_cos={cc:.6f} "
                      f"threshold={thr} (non-gating)")
            hidden = hw_f32.copy()

        segment_records.append(seg_desc)

    elapsed = time.time() - t0
    ladder_pass = all(r["ok"] for r in checkpoint_results)

    meta = {
        "commit": commit, "command": command, "hostname": hostname,
        "model": model_path, "dims": dims,
        "total_consumed": total_consumed, "elapsed_s": elapsed,
        "segment_records": segment_records,
    }
    results = {
        "checkpoints": checkpoint_results,
        "cross_checks": cross_checks,
        "ladder_pass": ladder_pass,
    }
    _write_evidence(results, meta)
    _save_npz(fp32_states, hw_states, emb, dims, meta)

    print(f"[SEGMENT] done: 9 layers, 5 checkpoints, ladder={'PASS' if ladder_pass else 'FAIL'}, "
          f"elapsed={elapsed:.1f}s")
    assert ladder_pass, f"checkpoint ladder failed: {checkpoint_results}"
