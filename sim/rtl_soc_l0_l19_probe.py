#!/usr/bin/env python3
"""
rtl_soc_l0_l19_probe.py — todo 13 L19 root-cause probe: L0 -> L19 in one VCS
session with per-wave DRAM readbacks.

Reproduces the in-segment code path that corrupts L19 (L0 first, then the
segment-boundary full DRAM preload + SRAM clear, then L19 from the spike L18
input) and, after every wave of L19, reads back the wave's output tensors
from hardware DRAM and compares each against the Python golden to pinpoint
the first wave where hardware diverges.

Entry point: test_l0_l19_probe (MODULE=sim.rtl_soc_l0_l19_probe,
TOPLEVEL=tb_soc_ibex, FM_SOC_CASE_ID=L0L19-PROBE).

Evidence:
  build/evidence/l0l19-probe-progress.log  per-wave progress (append+flush)
  build/evidence/l0l19-probe.json          progressive per-wave cos/min/max
  build/evidence/l0l19-probe-evidence.txt  final summary (written at test end)
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "ggml-npu"))

import cocotb  # noqa: E402
from cocotb_bridge import CocotbBridge, DRAM_BASE, SRAM_SIZE  # noqa: E402
from func_model import FuncModel  # noqa: E402
from golden_executor import GoldenMXU  # noqa: E402
from q4_dequant import load_weights_from_gguf  # noqa: E402
from quantize import quantize_int4_per_block  # noqa: E402
import spike_host as sh  # noqa: E402
import rtl_soc_segment_run as segrun  # noqa: E402

PROGRESS_PATH = _REPO / "build" / "evidence" / "l0l19-probe-progress.log"
JSON_PATH = _REPO / "build" / "evidence" / "l0l19-probe.json"
EVIDENCE_PATH = _REPO / "build" / "evidence" / "l0l19-probe-evidence.txt"

RING_SIZE = segrun.RING_SIZE
WAVE_TIMEOUT_CYCLES = segrun.WAVE_TIMEOUT_CYCLES
POLL_INTERVAL = segrun.POLL_INTERVAL
P10_RESID_SCALE = segrun.P10_RESID_SCALE


def _cos(a, b):
    return sh._cosine_similarity(np.asarray(a, np.float64).ravel(),
                                 np.asarray(b, np.float64).ravel())


_progress_fp = None


def _log(msg: str) -> None:
    global _progress_fp
    try:
        if _progress_fp is None:
            PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
            _progress_fp = open(PROGRESS_PATH, "a", encoding="utf-8")
        _progress_fp.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        _progress_fp.flush()
    except Exception:
        pass
    print(msg, flush=True)


def _record(name: str, hw: np.ndarray, golden: np.ndarray) -> dict:
    a = np.asarray(hw, np.float64).ravel()
    g = np.asarray(golden, np.float64).ravel()
    return {
        "name": name,
        "cos": float(sh._cosine_similarity(a, g)),
        "min": float(a.min()),
        "max": float(a.max()),
        "g_min": float(g.min()),
        "g_max": float(g.max()),
    }


def _compute_mmul_expected(input_i8: np.ndarray, W_f32: np.ndarray,
                           M: int, K: int, N: int) -> np.ndarray:
    """Dequantized MMUL output expected from the hardware descriptor.

    The probe readbacks are compared against the hardware-expected values, not
    the model's FP32 semantic golden, because the Phase-10 descriptor dispatches
    INT4-per-block MMULs whose outputs are scaled partial sums.  Using the
    dequantized expected output isolates descriptor/quantization effects from
    genuine hardware corruption.
    """
    packed, scales, _ = quantize_int4_per_block(W_f32, 128)
    return GoldenMXU().matmul_int4_per_block(input_i8, packed, scales,
                                             M, K, N, group_size=128)


async def _read_dram(bridge, addr: int, shape, dtype) -> np.ndarray:
    n = int(np.prod(shape)) * np.dtype(dtype).itemsize
    data = await bridge.segment_read_dram(addr, n)
    return np.frombuffer(bytes(data), dtype=dtype).reshape(shape)


def _read_model(model, addr: int, shape, dtype) -> np.ndarray:
    """Read a tensor from the Python model.dram image (after explicit readback)."""
    off = addr - DRAM_BASE
    n = int(np.prod(shape)) * np.dtype(dtype).itemsize
    return np.frombuffer(model.dram[off:off + n], dtype=dtype).reshape(shape)


def _dump_json(state: dict) -> None:
    try:
        JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = str(JSON_PATH) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, str(JSON_PATH))
    except Exception:
        pass


async def _ibex_execute_layer_probe(bridge, model, hidden, weights, layer, dims,
                                    M=1, ring_offset=0, probe=None):
    """Local copy of segrun.ibex_execute_layer with per-wave readback hooks.

    The op-building section is transcribed verbatim from rtl_soc_segment_run so
    the dispatched op stream is identical to the segment run; the only
    additions are the probe readbacks after each wave.
    """
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
    down = ffn_hidden @ w[f"blk.{layer}.ffn_down.weight"].T
    golden_l_out = sh._forward_layer(hidden, weights, layer, n_heads=heads,
                                     n_kv_heads=kv_heads, head_dim=head_dim)

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
        _log(f"[WAVE L{layer}] start cmds={len(ops)}")
        t0 = time.time()
        n = segrun._ibex_schedule_chain(model, ops, state["offset"])
        state["offset"] += n
        await bridge.segment_preload(
            bytes(model.dram),
            progress_cb=lambda pct, done, total: _log(
                f"[SEGMENT] preloading dram {pct}% ({done}/{total} words)"))
        t1 = time.time()
        await bridge.segment_kick(state["offset"])
        ok = await bridge.segment_wait(state["offset"], WAVE_TIMEOUT_CYCLES,
                                       POLL_INTERVAL)
        t2 = time.time()
        cyc = int(bridge.dut.sim_cycle.value) if hasattr(bridge.dut, "sim_cycle") else -1
        _log(f"[WAVE L{layer}] done cmds={n} preload={t1 - t0:.1f}s "
             f"compute={t2 - t1:.1f}s sim_cycle={cyc} ok={ok}")
        if not ok:
            head = await bridge.segment_read_head()
            raise RuntimeError(
                f"L{layer}: wave timeout head={head} expected={state['offset'] % RING_SIZE}")
        if readback:
            # Brief wait so any trailing AXI write responses land in the DRAM
            # model before the backdoor readback snapshots the bytes.
            await bridge.wait_cycles(1000)
            for (addr, length) in readback:
                data = await bridge.segment_read_dram(addr, length)
                off = addr - DRAM_BASE
                model.dram[off:off + length] = data
        return n

    def new_wave():
        sh._reset_wave_arena()
        return []

    q_i8, _ = sh._int8_quantize(normed)
    k_i8, _ = sh._int8_quantize(normed)
    v_i8, _ = sh._int8_quantize(normed)
    attn_i8, _ = sh._int8_quantize(attn_out)
    gate_i8, _ = sh._int8_quantize(ffn_input)
    up_i8, _ = sh._int8_quantize(ffn_input)
    ffn_i8, ffn_scale = sh._int8_quantize(ffn_hidden)

    consumed = 0

    # Wave 1: pre-attn RMSNorm + Q/K/V/O MMUL + residual + post-attn RMSNorm
    ops = new_wave()
    sh._add_sfu_op(ops, model, hidden_addr, normed_addr, sh.SFU_OP_RMSNORM,
                   hidden.astype(np.float16), H)
    packed_q, scales_q, _, _ = sh._quantize_weight_for_mmul(w[f"blk.{layer}.attn_q.weight"])
    sh._add_mmul_op(ops, model, q_in_addr, q_out_addr, packed_q, scales_q, M, H, QD, q_i8)
    packed_k, scales_k, _, _ = sh._quantize_weight_for_mmul(w[f"blk.{layer}.attn_k.weight"].T)
    sh._add_mmul_op(ops, model, k_in_addr, k_out_addr, packed_k, scales_k, M, H, KD, k_i8)
    packed_v, scales_v, _, _ = sh._quantize_weight_for_mmul(w[f"blk.{layer}.attn_v.weight"].T)
    sh._add_mmul_op(ops, model, v_in_addr, v_out_addr, packed_v, scales_v, M, H, KD, v_i8)
    packed_o, scales_o, _, _ = sh._quantize_weight_for_mmul(w[f"blk.{layer}.attn_output.weight"])
    sh._add_mmul_op(ops, model, o_in_addr, o_out_addr, packed_o, scales_o, M, H, H, attn_i8)
    # Consumer ops overwrite their input DRAM addresses with golden reference
    # data, so stage those inputs separately and preserve producer outputs.
    resid_a_addr = sh._allocate_dram(H * 4)
    resid_b_addr = sh._allocate_dram(H * 4)
    sh._add_vector_op(ops, model, resid_a_addr, resid_b_addr, residual1_addr, sh.VEC_OP_ADD,
                      np.rint(hidden * P10_RESID_SCALE).astype(np.int32),
                      np.rint(o * P10_RESID_SCALE).astype(np.int32), H)
    rmsnorm_in_addr = sh._allocate_dram(H * 2)
    sh._add_sfu_op(ops, model, rmsnorm_in_addr, ffn_in_addr, sh.SFU_OP_RMSNORM,
                   residual1.astype(np.float16), H)
    wave1_readback = [
        (q_out_addr, M * QD * 4),
        (k_out_addr, M * KD * 4),
        (v_out_addr, M * KD * 4),
        (o_out_addr, M * H * 4),
        (residual1_addr, M * H * 4),
        (ffn_in_addr, M * H * 2),
    ]
    consumed += await run_wave(ops, readback=wave1_readback)

    q_exp = _compute_mmul_expected(q_i8, w[f"blk.{layer}.attn_q.weight"], M, H, QD)
    o_exp = _compute_mmul_expected(attn_i8, w[f"blk.{layer}.attn_output.weight"], M, H, H)

    if probe is not None:
        recs = [
            _record("q_out", _read_model(model, q_out_addr, (M, QD), np.float32), q_exp),
            _record("k_out", _read_model(model, k_out_addr, (M, KD), np.float32), k),
            _record("v_out", _read_model(model, v_out_addr, (M, KD), np.float32), v),
            _record("o_out", _read_model(model, o_out_addr, (M, H), np.float32), o_exp),
            _record("residual1", _read_model(model, residual1_addr, (M, H), np.int32),
                    np.rint(residual1 * P10_RESID_SCALE)),
            _record("ffn_in", _read_model(model, ffn_in_addr, (M, H), np.float16),
                    ffn_input),
        ]
        probe["waves"].append({"wave": "w1", "cmds": len(ops), "tensors": recs})
        _dump_json(probe)
        for r in recs:
            _log(f"[PROBE L{layer} w1] {r['name']} cos={r['cos']:.6f} "
                 f"hw=[{r['min']:.3f},{r['max']:.3f}] g=[{r['g_min']:.3f},{r['g_max']:.3f}]")

    gate_exp = _compute_mmul_expected(gate_i8, w[f"blk.{layer}.ffn_gate.weight"].T, M, H, I)
    up_exp = _compute_mmul_expected(up_i8, w[f"blk.{layer}.ffn_up.weight"].T, M, H, I)
    down_exp = _compute_mmul_expected(ffn_i8, w[f"blk.{layer}.ffn_down.weight"].T, M, I, H)

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
            silu_in_addr = sh._allocate_dram(I * 2)
            sh._add_sfu_op(ops, model, silu_in_addr, silu_addr, sh.SFU_OP_SILU,
                           gate.astype(np.float16), I)
        wave24_readback = [
            (gate_out_addr, M * I * 4),
            (silu_addr, M * I * 2),
        ] if t_hi >= n_tiles else None
        consumed += await run_wave(ops, readback=wave24_readback)
        if probe is not None and t_hi >= n_tiles:
            recs = [
                _record("gate_out", _read_model(model, gate_out_addr, (M, I), np.float32),
                        gate_exp),
                _record("silu", _read_model(model, silu_addr, (M, I), np.float16),
                        silu_gate),
            ]
            probe["waves"].append({"wave": "w2-4", "cmds": len(ops), "tensors": recs})
            _dump_json(probe)
            for r in recs:
                _log(f"[PROBE L{layer} w2-4] {r['name']} cos={r['cos']:.6f} "
                     f"hw=[{r['min']:.3f},{r['max']:.3f}] g=[{r['g_min']:.3f},{r['g_max']:.3f}]")

    # Waves 5-7: FFN up (N-tiled) + VMUL
    for t_lo in range(0, n_tiles, 2):
        t_hi = min(t_lo + 2, n_tiles)
        ops = new_wave()
        sh._add_mmul_tiles_phase10(ops, model, up_in_addr, up_out_addr,
                                   w[f"blk.{layer}.ffn_up.weight"].T, M, H, I,
                                   up_i8, tile_n, t_lo, t_hi)
        if t_hi >= n_tiles:
            vmul_a_addr = sh._allocate_dram(I * 4)
            vmul_b_addr = sh._allocate_dram(I * 4)
            sh._add_vector_op(ops, model, vmul_a_addr, vmul_b_addr, ffn_hidden_addr,
                              sh.VEC_OP_MUL, silu_gate.astype(np.int32),
                              up.astype(np.int32), I)
        wave57_readback = [
            (up_out_addr, M * I * 4),
            (ffn_hidden_addr, M * I * 4),
        ] if t_hi >= n_tiles else None
        consumed += await run_wave(ops, readback=wave57_readback)
        if probe is not None and t_hi >= n_tiles:
            recs = [
                _record("up_out", _read_model(model, up_out_addr, (M, I), np.float32),
                        up_exp),
                _record("ffn_hidden", _read_model(model, ffn_hidden_addr, (M, I), np.int32),
                        silu_gate.astype(np.int32) * up.astype(np.int32)),
            ]
            probe["waves"].append({"wave": "w5-7", "cmds": len(ops), "tensors": recs})
            _dump_json(probe)
            for r in recs:
                _log(f"[PROBE L{layer} w5-7] {r['name']} cos={r['cos']:.6f} "
                     f"hw=[{r['min']:.3f},{r['max']:.3f}] g=[{r['g_min']:.3f},{r['g_max']:.3f}]")

    # Waves 8-10: FFN down (N-tiled, one 768-col tile per wave)
    tile_n_dn = 768
    n_tiles_dn = (H + tile_n_dn - 1) // tile_n_dn
    for t in range(n_tiles_dn):
        ops = new_wave()
        sh._add_mmul_tiles_phase10(ops, model, ffn_out_in_addr, ffn_out_addr,
                                   w[f"blk.{layer}.ffn_down.weight"].T, M, I, H,
                                   ffn_i8, tile_n_dn, t, t + 1)
        readback = [(ffn_out_addr, M * H * 4)] if t == n_tiles_dn - 1 else None
        consumed += await run_wave(ops, readback=readback)
        if probe is not None and t == n_tiles_dn - 1:
            recs = [
                _record("ffn_out", _read_model(model, ffn_out_addr, (M, H), np.float32),
                        down_exp),
            ]
            probe["waves"].append({"wave": "w8-10", "cmds": len(ops), "tensors": recs})
            _dump_json(probe)
            for r in recs:
                _log(f"[PROBE L{layer} w8-10] {r['name']} cos={r['cos']:.6f} "
                     f"hw=[{r['min']:.3f},{r['max']:.3f}] g=[{r['g_min']:.3f},{r['g_max']:.3f}]")

    # Wave 11: final VRESID consuming the hardware down-MMUL output
    down_out_hw = sh._read_tensor(model, ffn_out_addr, (M, H), np.float32)
    _log(f"[VRESID L{layer}] down_out_hw min={float(np.nanmin(down_out_hw)):.4f} "
         f"max={float(np.nanmax(down_out_hw)):.4f} "
         f"nan={int(np.isnan(down_out_hw).sum())}/{down_out_hw.size} "
         f"ffn_scale={ffn_scale:.6f}")
    ops = new_wave()
    vresid_a_addr = sh._allocate_dram(H * 4)
    vresid_b_addr = sh._allocate_dram(H * 4)
    sh._add_vector_op(ops, model, vresid_a_addr, vresid_b_addr, l_out_addr, sh.VEC_OP_ADD,
                      np.rint(residual1 * P10_RESID_SCALE).astype(np.int32),
                      np.rint(down_out_hw * ffn_scale * P10_RESID_SCALE).astype(np.int32),
                      H)
    consumed += await run_wave(ops, readback=[(l_out_addr, H * 4)])

    hw_l_out = sh._read_tensor(model, l_out_addr, (M, H), np.int32)

    if probe is not None:
        recs = [
            _record("l_out", hw_l_out, np.rint(golden_l_out * P10_RESID_SCALE)),
        ]
        probe["waves"].append({"wave": "w11", "cmds": len(ops), "tensors": recs})
        probe["final"] = {
            "hw_l_out_range": [float(hw_l_out.min()), float(hw_l_out.max())],
            "golden_range": [float(golden_l_out.min() * P10_RESID_SCALE),
                             float(golden_l_out.max() * P10_RESID_SCALE)],
        }
        _dump_json(probe)
        for r in recs:
            _log(f"[PROBE L{layer} w11] {r['name']} cos={r['cos']:.6f} "
                 f"hw=[{r['min']:.3f},{r['max']:.3f}] g=[{r['g_min']:.3f},{r['g_max']:.3f}]")

    return hw_l_out, consumed


@cocotb.test()
async def test_l0_l19_probe(dut):
    bridge = CocotbBridge(dut)
    await bridge.start_clock()
    await bridge.reset(5)

    model_path = os.environ.get(
        "QWEN3B_GGUF", str(Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf"))
    commit = os.environ.get("IBEX_COMMIT", "unknown")

    _log(f"[PROBE] loading weights from {model_path}")
    weights = load_weights_from_gguf(model_path)
    emb, spike_layers = segrun._load_spike_npz()
    M = emb.shape[0]
    H = int(weights["blk.0.attn_norm.weight"].shape[0])
    I = int(weights["blk.0.ffn_gate.weight"].shape[0])
    QD = int(weights["blk.0.attn_q.weight"].shape[0])
    KD = int(weights["blk.0.attn_k.weight"].shape[0])
    dims = {
        "hidden_size": H, "intermediate_size": I, "q_dim": QD, "kv_dim": KD,
        "num_heads": QD // 128, "num_kv_heads": KD // 128, "heads": QD // 128,
        "kv_heads": KD // 128, "head_dim": 128, "num_hidden_layers": 36,
        "rope_theta": sh.QWEN_THETA, "rms_eps": sh.QWEN_RMS_EPS,
    }

    model = FuncModel(dram_mb=8, sram_kb=4096)
    t0 = time.time()

    # Phase 1: L0 from embedding (same code path as the segment run's L0)
    _log(f"[PROBE] phase1: L0 from embedding (commit={commit})")
    hw_l0, consumed0 = await segrun.ibex_execute_layer(
        bridge, model, emb.astype(np.float32), weights, 0, dims, M=1, ring_offset=0)
    l0_golden = sh._forward_layer(emb.astype(np.float32), weights, 0,
                                  n_heads=16, n_kv_heads=2, head_dim=128)
    _log(f"[PROBE] L0 done: cos_vs_golden={_cos(hw_l0, np.rint(l0_golden * P10_RESID_SCALE)):.6f} "
         f"ring_offset={consumed0} (elapsed={time.time() - t0:.0f}s)")

    # Phase 2: segment-boundary full DRAM preload + SRAM clear (6091ec9 fix)
    _log("[PROBE] phase2: boundary full preload + SRAM clear")
    await bridge.segment_preload(bytes(model.dram),
                                 sram=b"\x00" * SRAM_SIZE,
                                 force_full=True)
    _log(f"[PROBE] boundary done (elapsed={time.time() - t0:.0f}s)")

    # Phase 3: L19 from spike L18 with per-wave readbacks
    hidden = spike_layers[18]["fp32"].astype(np.float32)
    _log("[PROBE] phase3: L19 from spike L18 with per-wave readbacks")
    probe = {
        "engine": "ibex",
        "test": "l0_l19_probe",
        "commit": commit,
        "model": model_path,
        "layers_run": [0, 19],
        "waves": [],
        "final": None,
    }
    hw_l19, consumed19 = await _ibex_execute_layer_probe(
        bridge, model, hidden, weights, 19, dims, M=1, ring_offset=consumed0,
        probe=probe)

    # Final comparisons
    golden_l19 = sh._forward_layer(hidden, weights, 19, n_heads=16, n_kv_heads=2,
                                   head_dim=128)
    g_int = np.rint(golden_l19 * P10_RESID_SCALE)
    spike_hw19 = spike_layers[19]["hw"].astype(np.float32).ravel()
    cos_golden = _cos(hw_l19, g_int)
    cos_spike = _cos(hw_l19, spike_hw19)
    try:
        ibex_run1 = np.load(
            str(_REPO / "build" / "evidence" / "ph10-36layer-ibex-checkpoints-run1.npz"),
            allow_pickle=True)["hw_layer_19_output"].astype(np.float32).ravel()
        cos_run1 = _cos(hw_l19, ibex_run1)
    except Exception:
        cos_run1 = float("nan")
    _log(f"[PROBE] L19 final: cos_vs_golden={cos_golden:.6f} "
         f"cos_vs_spike={cos_spike:.6f} cos_vs_run1_garbage={cos_run1:.6f} "
         f"hw_range=[{float(hw_l19.min())},{float(hw_l19.max())}]")

    probe["final"] = {
        "cos_vs_golden": float(cos_golden),
        "cos_vs_spike": float(cos_spike),
        "cos_vs_run1_garbage": float(cos_run1),
        "hw_l_out_range": [float(hw_l19.min()), float(hw_l19.max())],
        "golden_range": [float(g_int.min()), float(g_int.max())],
        "elapsed_s": time.time() - t0,
    }
    _dump_json(probe)

    # Evidence file
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EVIDENCE_PATH, "w", encoding="utf-8") as f:
        f.write("Todo 13 - L19 root-cause probe: L0 -> L19 in one VCS session\n")
        f.write("=" * 70 + "\n")
        f.write(f"Timestamp      : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
        f.write(f"Commit         : {commit}\n")
        f.write(f"Model          : {model_path}\n")
        f.write("engine=ibex\n")
        f.write("layers_run=L0,L19\n")
        f.write("boundary=full_dram_preload+sram_clear\n")
        f.write(f"L0_cos_vs_golden={_cos(hw_l0, np.rint(l0_golden * P10_RESID_SCALE)):.6f}\n")
        f.write("\nPer-wave L19 readbacks (hardware DRAM vs Python golden):\n")
        for wrec in probe["waves"]:
            for r in wrec["tensors"]:
                f.write(f"wave={wrec['wave']:5s} tensor={r['name']:12s} "
                        f"cos={r['cos']:.6f} hw=[{r['min']:.3f},{r['max']:.3f}] "
                        f"g=[{r['g_min']:.3f},{r['g_max']:.3f}]\n")
        f.write("\nFinal L19 comparisons:\n")
        f.write(f"  l_out vs python golden  cos={cos_golden:.6f}\n")
        f.write(f"  l_out vs spike hw L19   cos={cos_spike:.6f}\n")
        f.write(f"  l_out vs run1 garbage   cos={cos_run1:.6f}\n")
        f.write(f"  hw_range=[{float(hw_l19.min())},{float(hw_l19.max())}]\n")
        f.write(f"  elapsed_s={time.time() - t0:.1f}\n")
        f.write("PROBE-RUN-COMPLETE\n")
    _log(f"[PROBE] evidence written: {EVIDENCE_PATH}")
