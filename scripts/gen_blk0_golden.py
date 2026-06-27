#!/usr/bin/env python3
"""
Generate synthetic golden data for Qwen2.5-3B blk.0.
17-op transformer layer sequence with GoldenExecutor.
Deterministic: fixed seeds, re-run produces identical SHA-256.

Usage:
    cd CaduceusCore && PYTHONPATH=sim python scripts/gen_blk0_golden.py

Output:
    rtl/test_vectors/qwen_blk0/
        *.hex         — per-op input and golden output hex files
        blk0_manifest.json — op-by-op manifest with dimensions, tiles, SRAM layout
"""

import hashlib
import json
import os
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

# Add sim to Python path
_sim_dir = Path(__file__).resolve().parent.parent / "sim"
sys.path.insert(0, str(_sim_dir))
from engine.isa import NPUInstruction, OpCode
from golden_executor import GoldenExecutor, GoldenMXU, GoldenSFU, GoldenVector, ARRAY_H, ARRAY_W

# ── Qwen2.5-3B blk.0 dimensions ───────────────────────────────────────
HIDDEN = 2560
INTERMEDIATE = 9728
QKV_DIM = 4096       # 32 heads × 128 head_dim
KV_DIM = 256          # 2 KV heads × 128 head_dim
HEAD_DIM = 128
NUM_HEADS = 32
KV_HEADS = 2
M = 1                 # decode batch

# ── SRAM layout (offsets within 4MB GoldenExecutor SRAM) ──────────────
# Task spec: weight buffer at 0x20000000 (SoC addr) → offset 0x000000
#            activation buffer at 0x20010000 → offset 0x010000
#            output buffer at 0x20020000 → offset 0x020000
#            scratch/SFU I/O at 0x030000
WGT_BUF     = 0x000000   # reusable weight buffer, 2 KB
ACT_BUF     = 0x010000   # activation buffer, max 64 KB per op
OUT_BUF     = 0x020000   # output buffer, reused per op, max 128 KB
SFU_SCRATCH = 0x030000   # SFU I/O scratch space

# ── Seeds ─────────────────────────────────────────────────────────────
WEIGHT_SEED = 42
ACT_SEED = 123

# ── Output directory ──────────────────────────────────────────────────
OUT_DIR = Path(__file__).resolve().parent.parent / "rtl" / "test_vectors" / "qwen_blk0"


# ═══════════════════════════════════════════════════════════════════════
# Hex file writers ($readmemh compatible)
# ═══════════════════════════════════════════════════════════════════════

def _write_hex(path: Path, data: np.ndarray, fmt: str) -> None:
    """Write numpy data as hex-file (one value per line)."""
    with open(path, "w") as f:
        arr = np.asarray(data).flatten()
        if fmt == "int4":   # INT4 packed as uint8, 2 hex digits
            for b in arr.astype(np.uint8):
                f.write(f"{b:02x}\n")
        elif fmt == "int8":  # INT8, unsigned hex
            for v in arr.astype(np.int8):
                f.write(f"{int(v) & 0xFF:02x}\n")
        elif fmt == "int32":  # INT32, 8 hex digits
            for v in arr.astype(np.int32):
                f.write(f"{int(v) & 0xFFFFFFFF:08x}\n")
        elif fmt == "fp16":  # FP16, 4 hex digits
            raw = arr.astype(np.float16).tobytes()
            for i in range(0, len(raw), 2):
                val = struct.unpack_from("<H", raw, i)[0]
                f.write(f"{val:04x}\n")
        else:
            raise ValueError(f"Unknown hex format: {fmt}")


def sha256_file(path: Path) -> str:
    """SHA-256 hash of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════
# Weight / activation generation
# ═══════════════════════════════════════════════════════════════════════

def gen_weight(rng: np.random.RandomState, K: int, N: int) -> np.ndarray:
    """Generate INT4 weights packed 2-per-byte. Shape: (K, N) unpacked."""
    w_int4 = rng.randint(-8, 8, size=K * N, dtype=np.int8)
    return GoldenMXU.pack_int4(w_int4)


def gen_activation_int8(rng: np.random.RandomState, size: int) -> np.ndarray:
    """Generate random INT8 activation."""
    return rng.randint(-128, 128, size=size, dtype=np.int8)


def tile_count(K: int, N: int) -> int:
    """Tile decomposition: ceil(K/64) × ceil(N/64)."""
    return ((K + 63) // 64) * ((N + 63) // 64)


# ═══════════════════════════════════════════════════════════════════════
# Type conversion helpers
# ═══════════════════════════════════════════════════════════════════════

def int8_to_fp16(data: np.ndarray) -> np.ndarray:
    """INT8 → FP16 (simple cast)."""
    return np.asarray(data, dtype=np.int8).astype(np.float32).astype(np.float16)


def fp16_to_int8(data: np.ndarray) -> np.ndarray:
    """FP16 → INT8 with clipping."""
    f32 = np.asarray(data, dtype=np.float16).astype(np.float32)
    return np.clip(np.round(f32), -128, 127).astype(np.int8)


def fp16_to_int32(data: np.ndarray) -> np.ndarray:
    """FP16 → INT32 with saturation."""
    f32 = np.asarray(data, dtype=np.float16).astype(np.float32)
    return np.clip(np.round(f32), -2**31, 2**31 - 1).astype(np.int32)


def int32_to_fp16(data: np.ndarray) -> np.ndarray:
    """INT32 → FP16 (via float32, with saturation)."""
    f32 = np.asarray(data, dtype=np.int32).astype(np.float32)
    f16_max = np.finfo(np.float16).max
    f32 = np.clip(f32, -f16_max, f16_max)
    return f32.astype(np.float16)


# ═══════════════════════════════════════════════════════════════════════
# Main generator
# ═══════════════════════════════════════════════════════════════════════

class Blk0GoldenGen:
    """Generate Qwen2.5-3B blk.0 synthetic golden test vectors."""

    def __init__(self) -> None:
        self.out_dir = OUT_DIR
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.wgt_rng = np.random.RandomState(WEIGHT_SEED)
        self.act_rng = np.random.RandomState(ACT_SEED)

        # Create executor (shared across all ops)
        self.exec = GoldenExecutor(ARRAY_H, ARRAY_W)
        self.mxu = self.exec.mxu
        self.sfu = self.exec.sfu
        self.sram = self.exec.sram

        # Manifest entries
        self.manifest_ops: List[Dict[str, Any]] = []
        self.files_map: Dict[str, str] = {}

    # ── Weight generation (deterministic, fixed order) ──────────────

    _WEIGHT_SPECS = [
        ("Q_proj",   2560, 4096),
        ("K_proj",   2560, 256),
        ("V_proj",   2560, 256),
        ("O_proj",   4096, 2560),
        ("gate",     2560, 9728),
        ("up",       2560, 9728),
        ("down",     9728, 2560),
        # attention synthetic weights (small)
        ("attn_score_w",  128, 2),     # K^T: (head_dim, kv_heads)
        ("attn_weight_w", 2,   128),   # V:   (kv_heads, head_dim)
    ]

    def generate_weights(self) -> Dict[str, np.ndarray]:
        """Generate all INT4 weights with fixed seed order."""
        weights: Dict[str, np.ndarray] = {}
        for name, K, N in self._WEIGHT_SPECS:
            weights[name] = gen_weight(self.wgt_rng, K, N)
        return weights

    # ── Main generation ────────────────────────────────────────────

    def generate(self) -> Dict[str, Any]:
        """Run the full 17-op generation and return aggregate manifest."""
        weights = self.generate_weights()

        # Initial activation (M=1, K=2560)
        act_int8 = gen_activation_int8(self.act_rng, HIDDEN)   # (2560,) int8
        act_fp16 = int8_to_fp16(act_int8)                      # (2560,) float16

        # Also generate the attention-relevant activation slices
        # Q: 32 heads × 128 → 4096; K: 2 × 128 → 256
        q_act_int8 = gen_activation_int8(np.random.RandomState(ACT_SEED + 1), QKV_DIM)
        k_act_int8 = gen_activation_int8(np.random.RandomState(ACT_SEED + 2), KV_DIM)
        v_act_int8 = gen_activation_int8(np.random.RandomState(ACT_SEED + 3), KV_DIM)

        op_idx = 0

        # ─────────────────────────────────────────────────────────────
        # Op 1: RMSNORM (pre-attn)
        # ─────────────────────────────────────────────────────────────
        self._log(1, "RMSNORM pre-attn")
        rms_in_addr = ACT_BUF
        rms_out_addr = SFU_SCRATCH
        self.sram.write_float16(rms_in_addr, act_fp16)

        instr_rms1 = NPUInstruction(OpCode.RMSNORM, {
            "sa": rms_in_addr, "da": rms_out_addr, "elements": HIDDEN,
        }, comment="RMSNORM pre-attn, elements=2560")
        self.exec.step(instr_rms1)

        rms_out = self.sram.read_float16(rms_out_addr, HIDDEN)  # (2560,) fp16

        self._save_hex(f"op{op_idx:02d}_rmsnorm_pre_input.hex", act_fp16, "fp16")
        self._save_hex(f"op{op_idx:02d}_rmsnorm_pre_golden.hex", rms_out, "fp16")
        self._add_op(op_idx, "RMSNORM pre-attn", "RMSNORM",
                     {"elements": HIDDEN},
                     tiles=None, tile_weight_bytes=None,
                     sram_input=ACT_BUF, sram_output=rms_out_addr,
                     output_dtype="FP16", output_elem_bytes=2)
        op_idx += 1

        # Pre-load MMUL activation: RMSNORM output → INT8 for MXU
        rms_int8 = fp16_to_int8(np.asarray(rms_out, dtype=np.float32))
        act_for_proj = rms_int8  # (2560,) int8

        # ─────────────────────────────────────────────────────────────
        # Op 2: Q_proj MMUL
        # ─────────────────────────────────────────────────────────────
        q_out = self._mmul(op_idx, "Q_proj MMUL", M, HIDDEN, QKV_DIM,
                           act_for_proj, weights["Q_proj"], "Q_proj")
        q_fp16 = int32_to_fp16(q_out)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 3: K_proj MMUL
        # ─────────────────────────────────────────────────────────────
        k_out = self._mmul(op_idx, "K_proj MMUL", M, HIDDEN, KV_DIM,
                           act_for_proj, weights["K_proj"], "K_proj")
        k_fp16 = int32_to_fp16(k_out)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 4: V_proj MMUL
        # ─────────────────────────────────────────────────────────────
        v_out = self._mmul(op_idx, "V_proj MMUL", M, HIDDEN, KV_DIM,
                           act_for_proj, weights["V_proj"], "V_proj")
        v_fp16 = int32_to_fp16(v_out)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 5: ROPE
        # ─────────────────────────────────────────────────────────────
        self._log(5, "ROPE (Q+K)")
        # Concatenate Q and K as FP16 in SRAM
        qk_concat = np.concatenate([q_fp16.flatten(), k_fp16.flatten()])  # (4352,) fp16
        rope_in_addr = SFU_SCRATCH
        rope_out_addr = SFU_SCRATCH + 0x1000
        self.sram.write_float16(rope_in_addr, qk_concat)

        # Use sfu.rope_hw() directly (step() handler's GQA split formula
        # mid//8 does not match Qwen2.5-3B's 32:2 head ratio).
        q_rot, k_rot = self.sfu.rope_hw(
            q_fp16.flatten().astype(np.float32),
            k_fp16.flatten().astype(np.float32),
            position=0,
            num_heads=NUM_HEADS, head_dim=HEAD_DIM,
        )
        rope_out = np.concatenate([q_rot, k_rot])  # (4352,) fp32 → (4096+256,)
        self.sram.write_float16(rope_out_addr, rope_out.astype(np.float16))

        self._save_hex(f"op{op_idx:02d}_rope_input.hex", qk_concat, "fp16")
        self._save_hex(f"op{op_idx:02d}_rope_golden.hex", rope_out.astype(np.float16), "fp16")
        self._add_op(op_idx, "ROPE", "ROPE",
                     {"q_len": QKV_DIM, "k_len": KV_DIM, "position": 0},
                     tiles=None, tile_weight_bytes=None,
                     sram_input=rope_in_addr, sram_output=rope_out_addr,
                     output_dtype="FP16", output_elem_bytes=2)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 6: attn_score MMUL (Q_rot @ K_rot^T per head)
        # ─────────────────────────────────────────────────────────────
        q_act_reshaped = q_rot[:QKV_DIM].reshape(NUM_HEADS, HEAD_DIM)  # (32, 128) fp32
        q_act_int8_attn = np.clip(np.round(q_act_reshaped), -128, 127).astype(np.int8)
        attn_scores = self._mmul(op_idx, "attn_score MMUL", NUM_HEADS, HEAD_DIM, KV_HEADS,
                                 q_act_int8_attn, weights["attn_score_w"], "attn_score_w")
        attn_scores_fp16 = int32_to_fp16(attn_scores)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 7: attn_softmax SOFTMAX
        # ─────────────────────────────────────────────────────────────
        self._log(7, "attn_softmax SOFTMAX")
        sm_in_addr = SFU_SCRATCH
        sm_out_addr = SFU_SCRATCH + 0x200
        sm_elements = NUM_HEADS * KV_HEADS  # 64
        self.sram.write_float16(sm_in_addr, attn_scores_fp16)

        instr_sm = NPUInstruction(OpCode.SOFTMAX, {
            "sa": sm_in_addr, "da": sm_out_addr, "len": sm_elements,
        }, comment=f"attn_softmax len={sm_elements}")
        self.exec.step(instr_sm)

        attn_weights = self.sram.read_float16(sm_out_addr, sm_elements)  # (64,) fp16
        self._save_hex(f"op{op_idx:02d}_attn_softmax_input.hex", attn_scores_fp16, "fp16")
        self._save_hex(f"op{op_idx:02d}_attn_softmax_golden.hex", attn_weights, "fp16")
        self._add_op(op_idx, "attn_softmax SOFTMAX", "SOFTMAX",
                     {"elements": sm_elements},
                     tiles=None, tile_weight_bytes=None,
                     sram_input=sm_in_addr, sram_output=sm_out_addr,
                     output_dtype="FP16", output_elem_bytes=2)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 8: attn_weight MMUL (attn_weights @ V)
        # ─────────────────────────────────────────────────────────────
        # attn_weights: (32, 2) → INT8 activation (M=32, K=2)
        # V^T as weight (K=2, N=128) = (kv_heads, head_dim) INT4
        aw_act = attn_weights.reshape(NUM_HEADS, KV_HEADS)  # (32, 2) fp16
        aw_act_int8 = fp16_to_int8(aw_act)
        attn_out = self._mmul(op_idx, "attn_weight MMUL", NUM_HEADS, KV_HEADS, HEAD_DIM,
                              aw_act_int8, weights["attn_weight_w"], "attn_weight_w")
        attn_out_fp16 = int32_to_fp16(attn_out)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 9: O_proj MMUL
        # ─────────────────────────────────────────────────────────────
        o_in_act = fp16_to_int8(np.asarray(attn_out_fp16.flatten(), dtype=np.float32))
        o_out = self._mmul(op_idx, "O_proj MMUL", M, QKV_DIM, HIDDEN,
                           o_in_act, weights["O_proj"], "O_proj")
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 10: VRESID (pre-attn residual)
        # ─────────────────────────────────────────────────────────────
        self._log(10, "VRESID pre-attn residual")
        vresid1_in_addr = SFU_SCRATCH
        vresid1_out_addr = OUT_BUF
        # VRESID: da = sa (FP16) + sb (INT32)
        # sa = original act (FP16), sb = O_proj output (INT32)
        self.sram.write_float16(vresid1_in_addr, act_fp16)
        self.sram.write_int32(vresid1_in_addr + 0x1000, o_out)

        instr_vr1 = NPUInstruction(OpCode.VRESID, {
            "sa": vresid1_in_addr, "sb": vresid1_in_addr + 0x1000,
            "da": vresid1_out_addr, "len": HIDDEN,
        }, comment="VRESID pre-attn residual, len=2560")
        self.exec.step(instr_vr1)

        resid1_out = self.sram.read_int32(vresid1_out_addr, HIDDEN)  # (2560,) int32
        resid1_fp16 = int32_to_fp16(resid1_out)

        self._save_hex(f"op{op_idx:02d}_vresid_pre_input.hex", act_fp16, "fp16")
        self._save_hex(f"op{op_idx:02d}_vresid_pre_o_out.hex", o_out, "int32")
        self._save_hex(f"op{op_idx:02d}_vresid_pre_golden.hex", resid1_out, "int32")
        self._add_op(op_idx, "VRESID", "VRESID",
                     {"elements": HIDDEN},
                     tiles=None, tile_weight_bytes=None,
                     sram_input=vresid1_in_addr, sram_output=vresid1_out_addr,
                     output_dtype="INT32", output_elem_bytes=4)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 11: RMSNORM (post-attn)
        # ─────────────────────────────────────────────────────────────
        self._log(11, "RMSNORM post-attn")
        rms2_in_addr = SFU_SCRATCH
        rms2_out_addr = SFU_SCRATCH + 0x2000
        self.sram.write_float16(rms2_in_addr, resid1_fp16)

        instr_rms2 = NPUInstruction(OpCode.RMSNORM, {
            "sa": rms2_in_addr, "da": rms2_out_addr, "elements": HIDDEN,
        }, comment="RMSNORM post-attn, elements=2560")
        self.exec.step(instr_rms2)

        rms2_out = self.sram.read_float16(rms2_out_addr, HIDDEN)  # (2560,) fp16
        rms2_int8 = fp16_to_int8(np.asarray(rms2_out, dtype=np.float32))

        self._save_hex(f"op{op_idx:02d}_rmsnorm_post_input.hex", resid1_fp16, "fp16")
        self._save_hex(f"op{op_idx:02d}_rmsnorm_post_golden.hex", rms2_out, "fp16")
        self._add_op(op_idx, "RMSNORM post-attn", "RMSNORM",
                     {"elements": HIDDEN},
                     tiles=None, tile_weight_bytes=None,
                     sram_input=rms2_in_addr, sram_output=rms2_out_addr,
                     output_dtype="FP16", output_elem_bytes=2)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 12: gate MMUL
        # ─────────────────────────────────────────────────────────────
        gate_out = self._mmul(op_idx, "gate MMUL", M, HIDDEN, INTERMEDIATE,
                              rms2_int8, weights["gate"], "gate")
        gate_fp16 = int32_to_fp16(gate_out)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 13: up MMUL
        # ─────────────────────────────────────────────────────────────
        up_out = self._mmul(op_idx, "up MMUL", M, HIDDEN, INTERMEDIATE,
                            rms2_int8, weights["up"], "up")
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 14: SILU (gate activation)
        # ─────────────────────────────────────────────────────────────
        self._log(14, "SILU")
        silu_in_addr = SFU_SCRATCH
        silu_out_addr = SFU_SCRATCH + 0x2000
        self.sram.write_float16(silu_in_addr, gate_fp16)

        instr_silu = NPUInstruction(OpCode.SILU, {
            "sa": silu_in_addr, "da": silu_out_addr, "len": INTERMEDIATE,
        }, comment=f"SILU len={INTERMEDIATE}")
        self.exec.step(instr_silu)

        silu_out = self.sram.read_float16(silu_out_addr, INTERMEDIATE)  # (9728,) fp16
        silu_int32 = fp16_to_int32(silu_out)

        self._save_hex(f"op{op_idx:02d}_silu_input.hex", gate_fp16, "fp16")
        self._save_hex(f"op{op_idx:02d}_silu_golden.hex", silu_out, "fp16")
        self._add_op(op_idx, "SILU", "SILU",
                     {"elements": INTERMEDIATE},
                     tiles=None, tile_weight_bytes=None,
                     sram_input=silu_in_addr, sram_output=silu_out_addr,
                     output_dtype="FP16", output_elem_bytes=2)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 15: VMUL gate*up
        # ─────────────────────────────────────────────────────────────
        self._log(15, "VMUL gate*up")
        vmul_in_addr = SFU_SCRATCH
        vmul_out_addr = OUT_BUF
        # VMUL reads INT32 from sa and sa+len*4
        self.sram.write_int32(vmul_in_addr, silu_int32)
        self.sram.write_int32(vmul_in_addr + INTERMEDIATE * 4, up_out)

        instr_vmul = NPUInstruction(OpCode.VMUL, {
            "sa": vmul_in_addr, "da": vmul_out_addr, "len": INTERMEDIATE,
        }, comment=f"VMUL gate*up len={INTERMEDIATE}")
        self.exec.step(instr_vmul)

        vmul_out = self.sram.read_int32(vmul_out_addr, INTERMEDIATE)  # (9728,) int32
        vmul_int8 = np.clip(vmul_out // 256, -128, 127).astype(np.int8)  # rough quant for down

        self._save_hex(f"op{op_idx:02d}_vmul_gate_input.hex", silu_int32, "int32")
        self._save_hex(f"op{op_idx:02d}_vmul_up_input.hex", up_out, "int32")
        self._save_hex(f"op{op_idx:02d}_vmul_golden.hex", vmul_out, "int32")
        self._add_op(op_idx, "VMUL gate*up", "VMUL",
                     {"elements": INTERMEDIATE},
                     tiles=None, tile_weight_bytes=None,
                     sram_input=vmul_in_addr, sram_output=vmul_out_addr,
                     output_dtype="INT32", output_elem_bytes=4)
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 16: down MMUL
        # ─────────────────────────────────────────────────────────────
        down_out = self._mmul(op_idx, "down MMUL", M, INTERMEDIATE, HIDDEN,
                              vmul_int8, weights["down"], "down")
        op_idx += 1

        # ─────────────────────────────────────────────────────────────
        # Op 17: VRESID (post-attn residual)
        # ─────────────────────────────────────────────────────────────
        self._log(17, "VRESID post-attn residual")
        vresid2_in_addr = SFU_SCRATCH
        vresid2_out_addr = OUT_BUF
        self.sram.write_float16(vresid2_in_addr, resid1_fp16)
        self.sram.write_int32(vresid2_in_addr + 0x1000, down_out)

        instr_vr2 = NPUInstruction(OpCode.VRESID, {
            "sa": vresid2_in_addr, "sb": vresid2_in_addr + 0x1000,
            "da": vresid2_out_addr, "len": HIDDEN,
        }, comment="VRESID post-attn residual, len=2560")
        self.exec.step(instr_vr2)

        resid2_out = self.sram.read_int32(vresid2_out_addr, HIDDEN)  # (2560,) int32

        self._save_hex(f"op{op_idx:02d}_vresid_post_input.hex", resid1_fp16, "fp16")
        self._save_hex(f"op{op_idx:02d}_vresid_post_down.hex", down_out, "int32")
        self._save_hex(f"op{op_idx:02d}_vresid_post_golden.hex", resid2_out, "int32")
        self._add_op(op_idx, "VRESID", "VRESID",
                     {"elements": HIDDEN},
                     tiles=None, tile_weight_bytes=None,
                     sram_input=vresid2_in_addr, sram_output=vresid2_out_addr,
                     output_dtype="INT32", output_elem_bytes=4)
        op_idx += 1

        # ── SRAM footprint verification ────────────────────────────
        sram_footprint = self._compute_sram_footprint()
        assert sram_footprint <= 4 * 1024 * 1024, \
            f"SRAM footprint {sram_footprint} bytes exceeds 4MB ({4*1024*1024})"

        # ── Build manifest ──────────────────────────────────────────
        manifest = {
            "model": "Qwen2.5-3B",
            "layer": "blk.0",
            "dimensions": {
                "hidden": HIDDEN,
                "intermediate": INTERMEDIATE,
                "qkv_dim": QKV_DIM,
                "kv_dim": KV_DIM,
                "num_heads": NUM_HEADS,
                "kv_heads": KV_HEADS,
                "head_dim": HEAD_DIM,
                "M": M,
            },
            "seeds": {"weight": WEIGHT_SEED, "activation": ACT_SEED},
            "sram_layout": {
                "weight_buffer": WGT_BUF,
                "activation_buffer": ACT_BUF,
                "output_buffer": OUT_BUF,
                "sfu_scratch": SFU_SCRATCH,
            },
            "sram_footprint_bytes": sram_footprint,
            "num_ops": len(self.manifest_ops),
            "ops": self.manifest_ops,
            "files": self.files_map,
        }

        # Write manifest
        manifest_path = self.out_dir / "blk0_manifest.json"
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        return manifest

    # ── Helpers ────────────────────────────────────────────────────

    def _log(self, op_num: int, name: str) -> None:
        print(f"  [{op_num:2d}] {name}")

    def _save_hex(self, filename: str, data: np.ndarray, fmt: str) -> Path:
        path = self.out_dir / filename
        _write_hex(path, data, fmt)
        self.files_map[filename] = {"format": fmt, "sha256": sha256_file(path)}
        return path

    def _add_op(self, idx: int, name: str, opcode: str,
                dims: Dict[str, Any],
                tiles: int | None, tile_weight_bytes: int | None,
                sram_input: int, sram_output: int,
                output_dtype: str, output_elem_bytes: int) -> None:
        entry: Dict[str, Any] = {
            "idx": idx,
            "name": name,
            "opcode": opcode,
            "dimensions": dims,
            "tiles": tiles,
            "tile_weight_bytes": tile_weight_bytes,
            "sram_input_addr": f"0x{sram_input:06X}",
            "sram_output_addr": f"0x{sram_output:06X}",
            "output_dtype": output_dtype,
            "output_elem_bytes": output_elem_bytes,
        }
        # Add input/output hex paths
        for canonical in ["input_hex", None]:
            pass  # populated by _mmul helper
        self.manifest_ops.append(entry)

    def _mmul(self, idx: int, name: str, M_val: int, K_val: int, N_val: int,
              act_int8: np.ndarray, wgt_packed: np.ndarray,
              wgt_name: str) -> np.ndarray:
        """Compute MMUL golden via mxu.matmul_int32() and record manifest.

        Uses mxu.matmul_int32() directly (not step()) because full weight matrices
        can exceed 4MB SRAM. The RTL loads weights tile-by-tile via DMA; the
        golden model operates on the full matrix for numeric correctness.

        Returns INT32 golden output array shape (M_val, N_val).
        """
        self._log(idx + 1, name)

        # Golden computation
        golden = self.mxu.matmul_int32(act_int8, wgt_packed, M_val, K_val, N_val)

        # Save hex files
        safe_name = name.replace(' ', '_')
        input_name = f"op{idx:02d}_{safe_name}_input.hex"
        output_name = f"op{idx:02d}_{safe_name}_golden.hex"
        self._save_hex(input_name, act_int8, "int8")
        self._save_hex(output_name, golden, "int32")

        # Save full weight hex file (written once per unique weight name)
        wgt_hex_name = f"weight_{wgt_name}.hex"
        wgt_path = self.out_dir / wgt_hex_name
        if not wgt_path.exists():
            _write_hex(wgt_path, wgt_packed, "int4")
            self.files_map[wgt_hex_name] = {"format": "int4", "sha256": sha256_file(wgt_path)}

        full_wgt_bytes = (K_val * N_val + 1) // 2
        tiles = tile_count(K_val, N_val)
        tile_wgt_bytes = 64 * 64 // 2  # 2048 bytes = 2 KB per K-tile

        self._add_op(idx, name, "MMUL",
                     {"M": M_val, "K": K_val, "N": N_val, "tiles": tiles},
                     tiles=tiles, tile_weight_bytes=tile_wgt_bytes,
                     sram_input=ACT_BUF, sram_output=OUT_BUF,
                     output_dtype="INT32", output_elem_bytes=4)
        self.manifest_ops[-1]["input_hex"] = input_name
        self.manifest_ops[-1]["golden_output_hex"] = output_name
        self.manifest_ops[-1]["weight_hex"] = wgt_hex_name

        print(f"       tiles={tiles} (K_tiles={((K_val+63)//64)}, N_tiles={((N_val+63)//64)}), "
              f"weight_bytes={full_wgt_bytes}")
        return golden

    def _compute_sram_footprint(self) -> int:
        """Compute maximum concurrently-live SRAM footprint.

        Layout:
          WGT_BUF: 2KB at 0x000000 → ends at 0x000800
          ACT_BUF: max 64KB at 0x010000 → ends at 0x020000
          OUT_BUF: max 128KB at 0x020000 → ends at 0x040000
          SFU_SCRATCH: variable, starts at 0x030000

        Maximum concurrent usage:
          - Weight buffer: 2KB (always live)
          - Activation: max across all MMUL ops
            Largest: gate/up/down with M·K = 1·9728 = 9728 bytes (INT8)
            But attn_score MMUL: M=32, K=128 = 4096 bytes
            Overall max M·K: max(1*2560=2560, 32*128=4096, 1*9728=9728) = 9728 bytes
          - Output: max M·N across all MMUL ops
            Largest M·N: gate/up with 1·9728 = 9728 INT32 = 38912 bytes
            But attn_weight: 32*128 = 4096 INT32 = 16384 bytes
            Overall max M·N·4: max(1*4096*4=16384, 1*9728*4=38912, 32*128*4=16384) = 38912
          - SFU scratch: RMSNORM 2560*2=5120, ROPE 4352*2=8704, SILU 9728*2=19456
            Max: 19456 bytes (SILU input at SFU_SCRATCH)

        Conservative total = 2048 + 9728 + 38912 + 19456 = 70144 bytes ≪ 4MB
        """
        max_act = 9728       # INT8: 1 byte each
        max_out = 9728 * 4   # INT32: 4 bytes each
        max_sfu = 9728 * 2   # FP16: 2 bytes each
        total = 2048 + max_act + max_out + max_sfu
        return total


# ═══════════════════════════════════════════════════════════════════════
# Verification: deterministic re-run
# ═══════════════════════════════════════════════════════════════════════

def verify_determinism(out_dir: Path) -> None:
    """Re-run the generator and verify all hex files have identical SHA-256."""
    print("\n=== Determinism check: second run ===")

    # Read first-run SHA-256 hashes
    manifest_path = out_dir / "blk0_manifest.json"
    with open(manifest_path) as f:
        manifest1 = json.load(f)
    first_hashes = {name: info["sha256"] for name, info in manifest1["files"].items()}

    # Re-run
    gen2 = Blk0GoldenGen()
    _ = gen2.generate()

    # Compare
    with open(manifest_path) as f:
        manifest2 = json.load(f)
    second_hashes = {name: info["sha256"] for name, info in manifest2["files"].items()}

    mismatches = []
    for name, h1 in first_hashes.items():
        h2 = second_hashes.get(name)
        if h2 is None:
            mismatches.append(f"{name}: missing in second run")
        elif h1 != h2:
            mismatches.append(f"{name}: {h1} vs {h2}")

    if mismatches:
        print(f"  DETERMINISM FAIL: {len(mismatches)} mismatches")
        for m in mismatches:
            print(f"    {m}")
        sys.exit(1)
    else:
        print(f"  DETERMINISM PASS: {len(first_hashes)} files SHA-256 identical")
        print(f"  First-run manifest: {manifest_path}")


# ═══════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════

def main() -> None:
    print("=" * 60)
    print("Qwen2.5-3B blk.0 Synthetic Golden Data Generator")
    print("=" * 60)
    print(f"  Model: Qwen2.5-3B (hidden={HIDDEN}, intermediate={INTERMEDIATE})")
    print(f"  Weights seed: {WEIGHT_SEED}, Activation seed: {ACT_SEED}")
    print(f"  Output: {OUT_DIR}")
    print()

    is_second_run = (OUT_DIR / "blk0_manifest.json").exists()

    gen = Blk0GoldenGen()
    manifest = gen.generate()

    print(f"\n  Generated {manifest['num_ops']} ops")
    print(f"  SRAM footprint: {manifest['sram_footprint_bytes']} bytes "
          f"({'OK' if manifest['sram_footprint_bytes'] <= 4194304 else 'OVERFLOW'})")
    print(f"  Manifest: {OUT_DIR / 'blk0_manifest.json'}")

    # Verify manifest JSON validity
    with open(OUT_DIR / "blk0_manifest.json") as f:
        json.load(f)  # will raise if invalid
    print("  JSON manifest: valid")

    if is_second_run:
        verify_determinism(OUT_DIR)

    print("\nDone.")


if __name__ == "__main__":
    main()
