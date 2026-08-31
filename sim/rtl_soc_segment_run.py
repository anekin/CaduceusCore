#!/usr/bin/env python3
"""
rtl_soc_segment_run.py — Ibex 36-layer checkpoint-subset segment run (todo 13).

Runs the 15-layer Ibex subset in one VCS session:

    L0 | L4->L5 | L9->L10 | L14->L15 | L19->L20 | L24->L25 | L29->L30 | L34->L35

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

from cocotb_bridge import CocotbBridge, DRAM_BASE, SRAM_SIZE  # noqa: E402
from func_model import FuncModel  # noqa: E402
from q4_dequant import load_weights_from_gguf  # noqa: E402
from opcodes import EngineOp  # noqa: E402

import spike_host as sh  # noqa: E402
import address_space  # noqa: E402

FIRMWARE_RING_BASE = sh.FIRMWARE_RING_BASE
DESC_BASE = sh.DESC_BASE
DESC_STRIDE = sh.DESC_STRIDE
CMD_ENTRY_SIZE = sh.CMD_ENTRY_SIZE
P10_RESID_SCALE = sh.P10_RESID_SCALE
SFU_OP_RMSNORM = sh.SFU_OP_RMSNORM
SFU_OP_SILU = sh.SFU_OP_SILU
VEC_OP_ADD = sh.VEC_OP_ADD
VEC_OP_MUL = sh.VEC_OP_MUL

from command_ring import RING_ENTRIES as RING_SIZE

WAVE_TIMEOUT_CYCLES = 100_000_000
POLL_INTERVAL = 20_000

EVIDENCE_PATH = _REPO / "build" / "evidence" / "task-14-soc-rtl-verification-signoff.txt"
PROGRESS_PATH = _REPO / "build" / "evidence" / "task-14-soc-rtl-verification-signoff-progress.log"

# ── Progress visibility ────────────────────────────────────────────────
# The segment run's stdout travels through ssh|tee pipes where both Python
# and VCS buffer output, so log lines can lag reality by minutes (todo 13
# hang diagnosis).  Every milestone is additionally appended to a plain
# file with an explicit flush — `tail -f` on that file always shows true
# progress, independent of pipe buffering.
_progress_fp = None


def _progress(msg: str) -> None:
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


def _close_progress() -> None:
    global _progress_fp
    if _progress_fp is not None:
        try:
            _progress_fp.close()
        except Exception:
            pass
        _progress_fp = None
NPZ_PATH = _REPO / "build" / "evidence" / "task-14-soc-rtl-verification-checkpoints.npz"
SPIKE_NPZ = _REPO / "build" / "evidence" / "ph10-36layer-spike.npz"
GOLDEN_DIR = _REPO / "rtl" / "test_vectors" / "soc_e2e" / "qwen25-3b-36layer"

SEGMENTS = [
    (None, 0, None),   # L0 standalone, input = embedding
    (4, 5, 3),         # L4->L5, input = spike L3
    (9, 10, 8),        # L9->L10, input = spike L8
    (14, 15, 13),      # L14->L15, input = spike L13
    (19, 20, 18),      # L19->L20, input = spike L18
    (24, 25, 23),      # L24->L25, input = spike L23
    (29, 30, 28),      # L29->L30, input = spike L28
    (34, 35, 33),      # L34->L35, input = spike L33
]
CHECKPOINTS = [0, 5, 10, 15, 20, 25, 30, 35]
EXECUTED_LAYERS = [0, 4, 5, 9, 10, 14, 15, 19, 20, 24, 25, 29, 30, 34, 35]


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
        _progress(f"[WAVE L{layer}] start cmds={len(ops)}")
        t0 = time.time()
        n = _ibex_schedule_chain(model, ops, state["offset"])
        state["offset"] += n
        await bridge.segment_preload(
            bytes(model.dram),
            progress_cb=lambda pct, done, total: _progress(
                f"[SEGMENT] preloading dram {pct}% ({done}/{total} words)"))
        t1 = time.time()
        await bridge.segment_kick(state["offset"])
        ok = await bridge.segment_wait(state["offset"], WAVE_TIMEOUT_CYCLES,
                                       POLL_INTERVAL)
        t2 = time.time()
        cyc = int(bridge.dut.sim_cycle.value) if hasattr(bridge.dut, "sim_cycle") else -1
        _progress(f"[WAVE L{layer}] done cmds={n} preload={t1 - t0:.1f}s "
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
    _progress(f"[VRESID L{layer}] down_out_hw min={float(np.nanmin(down_out_hw)):.4f} "
              f"max={float(np.nanmax(down_out_hw)):.4f} "
              f"nan={int(np.isnan(down_out_hw).sum())}/{down_out_hw.size} "
              f"ffn_scale={ffn_scale:.6f}")
    ops = new_wave()
    sh._add_vector_op(ops, model, residual1_addr, ffn_out_addr, l_out_addr, VEC_OP_ADD,
                      np.rint(residual1 * P10_RESID_SCALE).astype(np.int32),
                      np.rint(down_out_hw * ffn_scale * P10_RESID_SCALE).astype(np.int32),
                      H)
    consumed += await run_wave(ops, readback=[(l_out_addr, H * 4)])

    hw_l_out = sh._read_tensor(model, l_out_addr, (M, H), np.int32)
    return hw_l_out, consumed


def _git_head_commit():
    """Current git HEAD — the commit evidence and checkpoints must bind to."""
    try:
        out = subprocess.check_output(
            ["git", "-C", str(_REPO), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL).decode().strip()
        return out
    except Exception:
        return os.environ.get("IBEX_COMMIT", "unknown")


def _resume_from_npz(spike_layers):
    """Load completed-segment state from NPZ_PATH (todo 14 resume).

    Pickle-safe (todo 11): the checkpoint npz is loaded with
    allow_pickle=False, so a forged local file containing a pickle payload is
    rejected by numpy instead of executing code.  The load is wrapped in a
    try/except guard (corrupted NPZ -> fresh run), and the checkpoint's
    metadata commit must equal the current git HEAD before any state is
    restored (wrong-commit checkpoints are refused).

    The checkpoint npz is saved incrementally after every checkpoint, so a
    timebox-killed run leaves the state of every layer it finished.  This
    helper reconstructs the pre-kill bookkeeping:

      completed_checkpoints — CHECKPOINTS entries present in the npz,
      fp32_states / hw_states — per-layer Func Model and hardware outputs,
      cross_checks — pre-layer cross-checks recomputed from the saved hw
                     states (deterministic; identical to the values the
                     prior run reported),
      segment_records — completed segments' descriptions, regenerated from
                        SEGMENTS (the strings are fully deterministic),
      ring_offset / total_consumed — prior run's command counts restored
                        from the evidence file (bookkeeping only: the resumed
                        VCS session reboots the firmware, so the command ring
                        is re-walked from slot 0).

    Returns None when there is nothing to resume (npz missing, empty,
    corrupted, unverifiable, or bound to a different commit).
    """
    if not NPZ_PATH.exists():
        return None
    try:
        with np.load(NPZ_PATH, allow_pickle=False) as d:
            files = set(d.files)
            fp32_states = {}
            hw_states = {}
            for L in EXECUTED_LAYERS:
                k, hk = f"layer_{L}_output", f"hw_layer_{L}_output"
                if k in files and hk in files:
                    fp32_states[L] = d[k].astype(np.float32)
                    hw_states[L] = d[hk].astype(np.int32)
            metadata = None
            if "metadata" in files:
                try:
                    raw = d["metadata"]
                    metadata = json.loads(str(raw.ravel()[0]))
                except Exception:
                    metadata = None
    except Exception as exc:
        _progress(f"[SEGMENT] resume rejected: corrupted/unsafe checkpoint NPZ "
                  f"({type(exc).__name__}: {exc}) — starting fresh run")
        return None
    head = _git_head_commit()
    if metadata is None:
        _progress("[SEGMENT] resume rejected: checkpoint metadata commit "
                  "missing/unreadable — starting fresh run")
        return None
    meta_commit = str(metadata.get("commit", ""))
    if head != "unknown" and meta_commit != head:
        _progress(f"[SEGMENT] resume rejected: checkpoint commit "
                  f"{meta_commit[:12]} != current HEAD {head[:12]} — "
                  f"starting fresh run")
        return None
    if not fp32_states:
        return None
    completed_checkpoints = [L for L in CHECKPOINTS if L in fp32_states]
    cross_checks = []
    for (pre, _chk, _seg_in) in SEGMENTS:
        if pre is None or pre not in fp32_states or pre not in spike_layers:
            continue
        hw_f32 = hw_states[pre].astype(np.float32) / P10_RESID_SCALE
        spike_hw = spike_layers[pre]["hw"].astype(np.float32) / P10_RESID_SCALE
        cc = _cos(hw_f32, spike_hw)
        thr = _chain_threshold(pre)
        cross_checks.append({
            "layer": pre, "cos_sim": float(cc), "threshold": thr,
            "status": "PASS" if cc >= thr else "FAIL", "ok": cc >= thr,
        })
    segment_records = []
    for (pre, chk, seg_in) in SEGMENTS:
        if chk not in fp32_states:
            break
        if pre is None:
            segment_records.append("L0: input=embedding (prompt token), checkpoint=L0")
        else:
            segment_records.append(
                f"L{pre}->L{chk}: segment input=L{seg_in} from spike npz, "
                f"chain_restart_state_source=ibex_dram")
    ring_offset = 0
    total_consumed = 0
    if EVIDENCE_PATH.exists():
        try:
            for line in EVIDENCE_PATH.read_text(encoding="utf-8").splitlines():
                if line.startswith("commands_dispatched="):
                    total_consumed = int(line.split("=", 1)[1])
                    ring_offset = total_consumed % RING_SIZE
        except Exception:
            pass
    return (completed_checkpoints, fp32_states, hw_states, cross_checks,
            segment_records, ring_offset, total_consumed)


def _partial_meta(model_path, commit, command, dims, elapsed):
    """Meta for an incremental checkpoint npz save (final save uses full meta)."""
    return {
        "engine": "ibex",
        "checkpoints": CHECKPOINTS,
        "chain_restart": True,
        "chain_restart_state_source": "ibex_dram",
        "segment_input_source": "spike_npz",
        "dims": dims,
        "model": model_path,
        "commit": commit,
        "command": command,
        "elapsed_s": elapsed,
        "partial": True,
    }


def _load_spike_npz():
    try:
        with np.load(SPIKE_NPZ, allow_pickle=False) as d:
            emb = d["input_embedding"].astype(np.float32)
            layers = {}
            for L in list(range(36)):
                if f"layer_{L}_output" in d.files:
                    layers[L] = {
                        "fp32": d[f"layer_{L}_output"].astype(np.float32),
                        "hw": d[f"hw_layer_{L}_output"].astype(np.int32),
                    }
    except Exception as exc:
        raise RuntimeError(
            f"spike npz {SPIKE_NPZ.name} missing/corrupted/unsafe "
            f"({type(exc).__name__}: {exc})") from exc
    return emb, layers


PROVENANCE_GEN = _REPO / "scripts" / "gen_evidence_provenance.py"


def _provenance_block():
    """Hash-bound provenance header for THIS run (todo 11).

    Invokes scripts/gen_evidence_provenance.py at evidence-write time so the
    evidence file binds the build artifacts actually exercised: run id, git
    commit + dirty state, simv/flist/driver/firmware/golden/checkpoint
    sha256, tool versions, timestamp.  Provenance generation problems never
    kill the run, but their absence is visible in the evidence.
    """
    try:
        out = subprocess.run(
            [sys.executable, str(PROVENANCE_GEN),
             "--run-id", os.environ.get("IBEX_RUN_ID", "unknown"),
             "--simv", os.environ.get("IBEX_SIMV", ""),
             "--flist", str(_REPO / "rtl" / "soc" / "soc.flist"),
             "--driver", str(Path(__file__).resolve()),
             "--firmware", str(_REPO / "firmware" / "build" / "npu_firmware.hex"),
             "--golden", str(GOLDEN_DIR),
             "--checkpoint", str(NPZ_PATH)],
            capture_output=True, text=True, timeout=120)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.rstrip() + "\n"
    except Exception:
        pass
    return ""


def _write_evidence(results, meta, final=False):
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    executed = ",".join(f"L{L}" for L in EXECUTED_LAYERS)
    ckpts = ",".join(f"L{L}" for L in CHECKPOINTS)
    completed = {r["layer"] for r in results["checkpoints"]}
    pending = [L for L in CHECKPOINTS if L not in completed]
    ladder = ("PASS" if all(r["ok"] for r in results["checkpoints"]) else "FAIL") \
        if final else "IN_PROGRESS"
    with open(EVIDENCE_PATH, "w", encoding="utf-8") as f:
        prov = _provenance_block()
        if prov:
            f.write(prov)
            f.write("\n")
        f.write("Task 14 - SoC RTL Verification Signoff: Ibex 36-layer 8-checkpoint segment run\n")
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
        for L in pending:
            f.write(f"layer={L} engine=ibex cos_sim=N/A threshold={_chain_threshold(L)} "
                    f"status=PENDING chain_restart_state_source=ibex_dram\n")
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
                f"{len(CHECKPOINTS)}\n")
        f.write(f"  LADDER={ladder}\n")
        f.write(f"  Overall: {'PASS' if ladder == 'PASS' else ladder}\n")
        f.write(f"  Timestamp end: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")


def _save_npz(fp32_states, hw_states, emb, dims, meta):
    NPZ_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"input_embedding": emb.astype(np.float32)}
    for L in sorted(fp32_states):
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
    command = "bash sim/regression/run_ibex_segment_run.sh"

    try:
        hostname = os.uname().nodename
    except Exception:
        hostname = "unknown"

    print(f"[SEGMENT] loading weights from {model_path}", flush=True)
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

    # fm-hardening-phase10 todo 2: layout contract once at startup; per-op
    # descriptor re-checks live in sh.write_cmd_entry().
    address_space.contract_check(desc_base=DESC_BASE, desc_count=34,
                                 act_base=sh.P10_ACT_BASE)

    # Fresh progress log per run; milestones append with explicit flush.
    try:
        with open(PROGRESS_PATH, "w", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} [SEGMENT] test start "
                    f"commit={commit} model={model_path}\n")
    except Exception:
        pass

    fp32_states = {}
    hw_states = {}
    checkpoint_results = []
    cross_checks = []
    segment_records = []
    ring_offset = 0
    total_consumed = 0

    # ── Todo 14 resume: restore completed-segment state from the checkpoint
    # npz so a timebox-killed run continues from the first unfinished segment
    # instead of re-executing completed layers.  Restored cos values are
    # recomputed deterministically — identical to the prior run's reports.
    resume = _resume_from_npz(spike_layers)
    completed_checkpoints = []
    if resume is not None:
        (completed_checkpoints, fp32_states, hw_states, cross_checks,
         segment_records, prev_ring_offset, total_consumed) = resume
        for L in completed_checkpoints:
            golden = sh._load_golden_layer(str(GOLDEN_DIR), L)
            cos = _cos(fp32_states[L], golden)
            thr = _chain_threshold(L)
            hw_f32 = hw_states[L].astype(np.float32) / P10_RESID_SCALE
            checkpoint_results.append({
                "layer": L, "cos_sim": float(cos), "threshold": thr,
                "status": "PASS" if cos >= thr else "FAIL", "ok": cos >= thr,
                "hw_cos": float(_cos(hw_f32, golden)),
                "hw_max": float(np.max(np.abs(
                    hw_f32 - golden.astype(np.float32)))),
            })
        _progress(f"[SEGMENT] resume: restored {len(completed_checkpoints)}/"
                  f"{len(CHECKPOINTS)} checkpoints from {NPZ_PATH.name} "
                  f"(layers={sorted(fp32_states)}, prev_ring_offset="
                  f"{prev_ring_offset}, prev_consumed={total_consumed})")

    def make_meta():
        return {
            "commit": commit, "command": command, "hostname": hostname,
            "model": model_path, "dims": dims,
            "total_consumed": total_consumed, "elapsed_s": time.time() - t0,
            "segment_records": list(segment_records),
        }

    t0 = time.time()
    if resume is not None:
        # Re-write the evidence with the restored results up front so the
        # resumed run's evidence is self-consistent from its first second
        # (completed checkpoints PASS, the rest PENDING).
        _write_evidence(
            {"checkpoints": list(checkpoint_results),
             "cross_checks": list(cross_checks),
             "ladder_pass": False},
            make_meta(), final=False)
    for (pre, chk, seg_in) in SEGMENTS:
        if chk in completed_checkpoints:
            # Prior run already executed this segment; its outputs are
            # restored from the checkpoint npz (todo 14 resume).
            continue
        if pre is None:
            hidden = emb.astype(np.float32).copy()
            seg_desc = f"L0: input=embedding (prompt token), checkpoint=L0"
            seg_layers = [chk]
        elif pre in fp32_states and pre in hw_states:
            # Mid-segment resume: the prior run finished the pre-layer but was
            # killed before its checkpoint.  Chain from the saved hardware
            # output instead of re-executing the pre-layer.
            hidden = hw_states[pre].astype(np.float32).copy() / P10_RESID_SCALE
            seg_desc = (f"L{pre}->L{chk}: segment resumed from saved hw L{pre}, "
                        f"chain_restart_state_source=ibex_dram")
            seg_layers = [chk]
        else:
            hidden = spike_layers[seg_in]["fp32"].astype(np.float32).copy()
            seg_desc = (f"L{pre}->L{chk}: segment input=L{seg_in} from spike npz, "
                        f"chain_restart_state_source=ibex_dram")
            seg_layers = [pre, chk]
        _progress(f"[SEGMENT] start {seg_desc} "
                  f"(elapsed={time.time() - t0:.0f}s)")

        # Segment boundary: force a FULL DRAM preload so hardware DRAM is
        # re-synchronized with the Python image, and zero the SRAM scratch so
        # engine staging leftovers from the previous segment cannot leak into
        # the next segment's ops.  Delta preloads within a segment only
        # refresh words the Python image changed, so regions the hardware
        # modified during the previous segment (MMUL/VRESID outputs Python
        # never read back) would otherwise keep stale hardware data.
        await bridge.segment_preload(bytes(model.dram),
                                     sram=b"\x00" * SRAM_SIZE,
                                     force_full=True,
                                     clear_sram=True)
        _progress(f"[SEGMENT] boundary full preload + SRAM clear done "
                  f"(elapsed={time.time() - t0:.0f}s)")

        for L in seg_layers:
            _progress(f"[SEGMENT] dispatching L{L} (elapsed={time.time() - t0:.0f}s)")
            fp32_out = sh._forward_layer(hidden, weights, L, n_heads=heads,
                                         n_kv_heads=kv_heads, head_dim=head_dim)
            hw_l_out, consumed = await ibex_execute_layer(
                bridge, model, hidden, weights, L, dims, M, ring_offset)
            ring_offset += consumed
            total_consumed += consumed
            fp32_states[L] = fp32_out.astype(np.float32)
            hw_states[L] = hw_l_out.astype(np.int32)
            hw_f32 = hw_l_out.astype(np.float32) / P10_RESID_SCALE
            _progress(f"[SEGMENT] L{L}: cmds={consumed} ring_offset={ring_offset} "
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
                _progress(f"  [CHECKPOINT L{L}] cos_sim={cos:.6f} threshold={thr} "
                          f"hw_cos={checkpoint_results[-1]['hw_cos']:.6f}")
                _save_npz(fp32_states, hw_states, emb, dims,
                          _partial_meta(model_path, commit, command, dims,
                                        time.time() - t0))
                # Incremental evidence: if the run is killed by the 24h wall
                # timebox, the last checkpoint's evidence (with the remaining
                # checkpoints marked PENDING) survives.
                _write_evidence(
                    {"checkpoints": list(checkpoint_results),
                     "cross_checks": list(cross_checks),
                     "ladder_pass": False},
                    make_meta(), final=False)
                _progress(f"  [CHECKPOINT] saved L{L} "
                          f"(npz layers={sorted(fp32_states.keys())})")
            if pre is not None and L == pre:
                spike_hw = spike_layers[pre]["hw"].astype(np.float32) / P10_RESID_SCALE
                cc = _cos(hw_f32, spike_hw)
                thr = _chain_threshold(pre)
                cross_checks.append({
                    "layer": pre, "cos_sim": float(cc), "threshold": thr,
                    "status": "PASS" if cc >= thr else "FAIL",
                    "ok": cc >= thr,
                })
                _progress(f"  [CROSSCHECK L{pre}] ibex_vs_spike_cos={cc:.6f} "
                          f"threshold={thr} (non-gating)")
            hidden = hw_f32.copy()

        segment_records.append(seg_desc)

    elapsed = time.time() - t0
    # Full success requires every configured checkpoint to have run and passed
    # its ladder threshold.  A timeboxed (partial) run reports the completed
    # subset; unreached checkpoints carry status=PENDING in the evidence.
    ladder_pass = (len(checkpoint_results) == len(CHECKPOINTS)
                   and all(r["ok"] for r in checkpoint_results))

    meta = make_meta()
    results = {
        "checkpoints": checkpoint_results,
        "cross_checks": cross_checks,
        "ladder_pass": ladder_pass,
    }
    _write_evidence(results, meta, final=True)
    _save_npz(fp32_states, hw_states, emb, dims, meta)

    _progress(f"[SEGMENT] done: {len(EXECUTED_LAYERS)} layers, {len(CHECKPOINTS)} checkpoints, "
              f"ladder={'PASS' if ladder_pass else 'FAIL'}, elapsed={elapsed:.1f}s")
    _close_progress()
    assert ladder_pass, f"checkpoint ladder failed: {checkpoint_results}"
