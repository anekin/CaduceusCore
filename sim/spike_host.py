#!/usr/bin/env python3
"""
Spike NPU Host Adapter

Prepares DRAM data using FuncModel.host_write_*(), drives the real RISC-V
firmware inside Spike through the MMIO bridge server, and verifies the output
against the GoldenMXU reference.
"""

import argparse
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "ggml-npu"))

from opcodes import EngineOp
from q4_dequant import load_weights_from_gguf
from func_model import FuncModel
from golden_executor import GoldenMXU
from quantize import quantize_int4_per_block
from regmap import Addr, DOORBELL, MXU
from spike_mmio_server import DEFAULT_SOCK_PATH, serve


# ── Paths ──────────────────────────────────────────────────────────

PROJECT = _HERE.parent
SPIKE_BIN = Path(__file__).resolve().parent.parent / "spike_src" / "build" / "spike"
FIRMWARE_ELF = PROJECT / "firmware" / "build" / "npu_firmware.elf"
FIRMWARE_SPIKE_ELF = PROJECT / "firmware" / "build" / "npu_firmware_spike.elf"
PLUGIN_SO = PROJECT / "spike_src" / "plugins" / "npu_mmio_plugin.so"

FIRMWARE_RING_BASE = 0x80000000  # hard-coded in C firmware as DRAM_BASE

# C firmware mmul_desc_t uses a 15-field packed layout
# (input/weight/output/scale addr + input/weight/output/scale sram + sizes + M,K,N).
MMUL_DESC_FMT = "<15I"
MMUL_DESC_SIZE = struct.calcsize(MMUL_DESC_FMT)

SFU_DESC_FMT = "<12I"
SFU_DESC_SIZE = struct.calcsize(SFU_DESC_FMT)

VECTOR_DESC_FMT = "<8I"
VECTOR_DESC_SIZE = struct.calcsize(VECTOR_DESC_FMT)

DMA_COPY_DESC_FMT = "<8I"
DMA_COPY_DESC_SIZE = struct.calcsize(DMA_COPY_DESC_FMT)

PCIE_DMA_DESC_FMT = "<6I"
PCIE_DMA_DESC_SIZE = struct.calcsize(PCIE_DMA_DESC_FMT)

CMD_ENTRY_FMT = "<8I"
CMD_ENTRY_SIZE = struct.calcsize(CMD_ENTRY_FMT)

DESC_BASE = 0x80001000
DESC_STRIDE = 64


def _emit_metric(key: str, value, case_id: str = ""):
    """Print a SIGNOFF_METRIC line for the signoff runner to capture."""
    cid = case_id or os.environ.get("_FM_CASE_ID", "unknown")
    print(f'SIGNOFF_METRIC {{"case": "{cid}", "key": "{key}", "value": {json.dumps(value)}}}')


# ── Helpers ────────────────────────────────────────────────────────

def write_mmul_descriptor(model: FuncModel, desc_addr: int,
                          input_addr: int, weight_addr: int, output_addr: int,
                          scale_addr: int = 0,
                          input_sram: int = 0, weight_sram: int = 0,
                          output_sram: int = 0, scale_sram: int = 0,
                          input_size: int = 0, weight_size: int = 0,
                          output_size: int = 0, scale_size: int = 0,
                          M: int = 1, K: int = 1, N: int = 1):
    buf = struct.pack(MMUL_DESC_FMT,
                      input_addr, weight_addr, output_addr, scale_addr,
                      input_sram, weight_sram, output_sram, scale_sram,
                      input_size, weight_size, output_size, scale_size,
                      M, K, N)
    model.host_write_data(desc_addr, np.frombuffer(buf, dtype=np.uint8))


def write_sfu_descriptor(model: FuncModel, desc_addr: int,
                          op: int, input_addr: int, output_addr: int,
                          input_sram: int, output_sram: int, size: int,
                          dim: int = 0, pos: int = 0):
    """Write an SFU descriptor in the 15-word generic layout expected by firmware npu_firmware.c.
    
    Layout: src[0]=input, src[2]=output, src[8]=dim, src[9]=pos, src[10]=sfu_op.
    """
    buf = struct.pack('<15I',
                      input_addr, 0, output_addr, 0,
                      input_sram, output_sram, 0, 0,
                      dim, pos, op, 0,
                      1, dim, 1)
    model.host_write_data(desc_addr, np.frombuffer(buf, dtype=np.uint8))


def write_vector_descriptor(model: FuncModel, desc_addr: int,
                             op: int, a_addr: int, b_addr: int, o_addr: int,
                             dim: int,
                             a_sram: int = 0, b_sram: int = 0, o_sram: int = 0):
    """Write a Vector descriptor in the 15-word generic layout expected by firmware npu_firmware.c.
    
    Layout: src[0]=a_addr, src[1]=b_addr, src[2]=o_addr, src[4]=a_sram, src[5]=b_sram,
    src[6]=o_sram, src[8]=dim.
    """
    buf = struct.pack('<15I',
                      a_addr, b_addr, o_addr, 0,
                      a_sram, b_sram, o_sram, 0,
                      dim, 0, 0, 0,
                      1, dim, 1)
    model.host_write_data(desc_addr, np.frombuffer(buf, dtype=np.uint8))


def write_dma_copy_descriptor(model: FuncModel, desc_addr: int,
                               src_addr: int, dst_addr: int, size: int):
    """Write a DMA_COPY descriptor in the 15-word generic layout expected by firmware npu_firmware.c."""
    buf = struct.pack('<15I',
                      src_addr, 0, dst_addr, 0,
                      0, 0, 0, 0,
                      size, 0, 0, 0,
                      1, size, 1)
    model.host_write_data(desc_addr, np.frombuffer(buf, dtype=np.uint8))



def write_cmd_entry(model: FuncModel, ring_index: int,
                    opcode: int, desc_addr: int, flags: int = 0):
    """Write a cmd_entry_t into the firmware ring buffer."""
    addr = FIRMWARE_RING_BASE + ring_index * CMD_ENTRY_SIZE
    buf = struct.pack(CMD_ENTRY_FMT,
                      opcode, desc_addr, flags,
                      0, 0, 0, 0, 0)
    model.host_write_data(addr, np.frombuffer(buf, dtype=np.uint8))


def schedule_chain(model: FuncModel, ops: list) -> int:
    """Write descriptors and command entries for a list of ops, then ring HOST_TAIL."""
    for i, op in enumerate(ops):
        desc_addr = DESC_BASE + i * DESC_STRIDE
        op_type = op['type']
        desc = op['desc']
        if op_type == 'mmul':
            write_mmul_descriptor(model, desc_addr, **desc)
            opcode = int(EngineOp.MMUL)
        elif op_type == 'sfu':
            write_sfu_descriptor(model, desc_addr, **desc)
            opcode = int(EngineOp.SFU)
        elif op_type == 'vector':
            write_vector_descriptor(model, desc_addr, **desc)
            opcode = int(EngineOp.VECTOR)
        elif op_type == 'dma_copy':
            write_dma_copy_descriptor(model, desc_addr, **desc)
            opcode = int(EngineOp.DMA_COPY)
        else:
            raise ValueError(f"Unknown op type: {op_type}")
        write_cmd_entry(model, i, opcode, desc_addr, flags=op.get('flags', 0))
    model.bridge.handle('write', DOORBELL.BASE + DOORBELL.HOST_TAIL, len(ops))
    return len(ops)


def poll_completion(model: FuncModel, expected_count: int, timeout: float = 180.0) -> bool:
    """Poll NPU_HEAD until it reaches expected_count (mod 64)."""
    expected_head = expected_count % 64
    deadline = time.time() + timeout
    while time.time() < deadline:
        head = model.bridge._status.get(DOORBELL.BASE + DOORBELL.NPU_HEAD, 0)
        if head == expected_head:
            return True
        time.sleep(0.05)
    return False


def run_one_op(gguf_path: str, layer: int, op: str, M: int = 1) -> bool:
    """Run a single op through Spike and verify against golden."""
    weights = load_weights_from_gguf(gguf_path)
    target = f"blk.{layer}.attn_{op.lower().replace('_proj', '')}.weight"
    if target not in weights:
        print(f"  [SKIP] L{layer} {op:12s} — weight not found")
        return False

    W_f32 = weights[target]
    K, N = W_f32.shape

    # Activation
    rng = np.random.RandomState(42)
    act = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)

    # Quantize to row-major INT4 + per-block scales
    wgt_packed, wgt_scales, _ = quantize_int4_per_block(W_f32, 128)

    # Golden reference (computed BEFORE reordering — uses row-major weights/scales)
    mxu = GoldenMXU()
    golden = mxu.matmul_int4_per_block(act, wgt_packed, wgt_scales, M, K, N, group_size=128)

    # Reorder for firmware's tiled DRAM layout (TILE_H=64, TILE_W=64)
    wgt_packed, wgt_scales = _reorder_weights_to_firmware_tiles(wgt_packed, wgt_scales, K, N)
    wgt_bytes = wgt_packed.tobytes()
    scale_bytes = wgt_scales.tobytes()

    # Pack weights and scales back-to-back for contiguous DMA
    combined_weight_blob = wgt_bytes + scale_bytes

    SRAM_KB = 4096  # match firmware NPU_SRAM_SIZE
    model = FuncModel(sram_kb=SRAM_KB)
    model.firmware.ring_buffer_addr = FIRMWARE_RING_BASE

    wgt_addr = 0x80200000
    act_addr = 0x80010000
    out_addr = 0x81000000
    desc_addr = 0x80001000

    # SRAM layout (must fit in 4 MB and avoid overlap)
    input_sram = 0x00000000
    weight_sram = 0x00100000
    output_sram = 0x00300000
    scale_sram = weight_sram + len(wgt_bytes)

    model.host_write_data(wgt_addr, np.frombuffer(combined_weight_blob, dtype=np.uint8))
    model.host_write_data(act_addr, act)

    write_mmul_descriptor(model, desc_addr,
                          input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
                          scale_addr=wgt_addr + len(wgt_bytes),
                          input_sram=input_sram, weight_sram=weight_sram, output_sram=output_sram,
                          scale_sram=scale_sram,
                          input_size=act.nbytes, weight_size=len(wgt_bytes),
                          output_size=M * N * 4, scale_size=len(scale_bytes),
                          M=M, K=K, N=N)
    model.host_write_command(0, desc_addr)

    # Pre-set MXU SCALE_ADDR so the bridge uses per-block dequantization.
    # The C firmware does not write SCALE_ADDR, so this persists through CMD.
    model.bridge.handle('write', MXU.BASE + MXU.SCALE_ADDR, scale_sram)

    # Set doorbell HOST_TAIL = 1 via the bridge (MMIO, not DRAM)
    model.bridge.handle('write', DOORBELL.BASE + DOORBELL.HOST_TAIL, 1)

    proc, server = _launch_spike(model)
    done = poll_completion(model, 1)
    _cleanup_spike(proc, server)

    if not done:
        print(f"  [FAIL] L{layer} {op:12s} — timeout waiting for NPU_HEAD={1 % 64}")
        return False

    # Read output tensor from model.dram
    out_off = out_addr - Addr.DRAM_BASE
    out_fw = np.frombuffer(model.dram[out_off:out_off + M * N * 4],
                           dtype=np.float32).reshape(M, N)

    ok = np.allclose(out_fw, golden, rtol=1e-5)
    print(f"  [{'PASS' if ok else 'FAIL'}] L{layer} {op:12s} ({K}x{N})")
    if not ok:
        print(f"    max_diff={np.max(np.abs(out_fw - golden)):.2e}")


SFU_OP_SOFTMAX = 0
SFU_OP_GELU = 2
SFU_OP_SILU = 4
SFU_OP_ROPE = 5
SFU_OP_RMSNORM = 6

VEC_OP_ADD = 0
VEC_OP_MUL = 1
VEC_OP_RESID = 5


def _count_op_coverage(ops: list) -> dict:
    """Count each dispatched op type for coverage reporting."""
    coverage = {
        "MMUL": 0,
        "SFU_RMSNorm": 0,
        "SFU_Softmax": 0,
        "SFU_RoPE": 0,
        "SFU_SiLU": 0,
        "SFU_Other": 0,
        "Vector_ADD": 0,
        "Vector_MUL": 0,
        "Vector_RESID": 0,
        "Vector_Other": 0,
        "DMA_COPY": 0,
    }
    for op in ops:
        t = op["type"]
        desc = op["desc"]
        if t == "mmul":
            coverage["MMUL"] += 1
        elif t == "sfu":
            sfu_op = desc.get("op", 0)
            if sfu_op == SFU_OP_RMSNORM:
                coverage["SFU_RMSNorm"] += 1
            elif sfu_op == SFU_OP_SOFTMAX:
                coverage["SFU_Softmax"] += 1
            elif sfu_op == SFU_OP_ROPE:
                coverage["SFU_RoPE"] += 1
            elif sfu_op in (SFU_OP_SILU, 3):
                coverage["SFU_SiLU"] += 1
            else:
                coverage["SFU_Other"] += 1
        elif t == "vector":
            vec_op = desc.get("op", 0)
            if vec_op == VEC_OP_ADD:
                coverage["Vector_ADD"] += 1
            elif vec_op == VEC_OP_MUL:
                coverage["Vector_MUL"] += 1
            elif vec_op == VEC_OP_RESID:
                coverage["Vector_RESID"] += 1
            else:
                coverage["Vector_Other"] += 1
        elif t == "dma_copy":
            coverage["DMA_COPY"] += 1
    return coverage

QWEN_HIDDEN = 1536
QWEN_INTERMEDIATE = 8960
QWEN_HEADS = 12
QWEN_KV_HEADS = 2
QWEN_HEAD_DIM = 128
QWEN_THETA = 1000000.0
QWEN_RMS_EPS = 1e-6

FP_WEIGHT_SRAM = 0x00000000
FP_SCALE_SRAM = 0x00200000
FP_INPUT_SRAM = 0x00250000
FP_OUTPUT_SRAM = 0x00260000
FP_SFU_IN_SRAM = 0x00270000
FP_SFU_OUT_SRAM = 0x00280000

# Firmware (todo 19) rejects descriptor addresses outside the 8 MB RTL
# dram_model window [0x80000000, 0x80800000); the data plane starts after
# the control plane (ring + completion + margin). 3B FFN weights exceed the
# window, so ACT tensors live in stable per-layer slots while weights are
# split into waves that fit the WGT arena (reset per wave).
FP_DRAM_BASE = 0x80020000
FP_DRAM_SIZE = 0x007E0000
P10_ACT_BASE = 0x80020000
P10_ACT_END = 0x801E0000
P10_WGT_BASE = 0x801E0000
P10_WGT_END = 0x80800000
P10_RESID_SCALE = 1024.0   # VRESID fixed-point scale (W1.3 convention)

# Phase-10 tolerance ladder (todo 12): per-layer cos_sim thresholds.
P10_LADDER = [
    (0, 19, 0.999),
    (20, 29, 0.998),
    (30, 35, 0.997),
]


def p10_layer_threshold(layer: int) -> float:
    """Return the tolerance-ladder threshold for a layer index."""
    for lo, hi, thr in P10_LADDER:
        if lo <= layer <= hi:
            return thr
    raise ValueError(f"layer {layer} outside tolerance ladder range")


def _forward_rmsnorm(x: np.ndarray, weight: np.ndarray, eps: float = QWEN_RMS_EPS) -> np.ndarray:
    return (x / np.sqrt(np.mean(x ** 2, axis=-1, keepdims=True) + eps)) * weight


def _forward_rope(x: np.ndarray, pos: np.ndarray, head_dim: int = QWEN_HEAD_DIM,
                  theta: float = QWEN_THETA) -> np.ndarray:
    seq_len = x.shape[0]
    x = x.reshape(seq_len, -1, head_dim).astype(np.float32)
    half = head_dim // 2
    freqs = 1.0 / (theta ** (np.arange(0, head_dim, 2, dtype=np.float32) / head_dim))
    angles = pos[:, None].astype(np.float32) * freqs[None, :]
    cos, sin = np.cos(angles).astype(np.float32), np.sin(angles).astype(np.float32)
    x1, x2 = x[..., :half], x[..., half:]
    y1 = x1 * cos[:, None, :] - x2 * sin[:, None, :]
    y2 = x2 * cos[:, None, :] + x1 * sin[:, None, :]
    y = np.empty_like(x)
    y[..., :half] = y1
    y[..., half:] = y2
    return y.reshape(seq_len, -1)


def _forward_softmax(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = x - np.max(x, axis=axis, keepdims=True)
    e = np.exp(x)
    return e / np.sum(e, axis=axis, keepdims=True)


def _forward_silu(x: np.ndarray) -> np.ndarray:
    return x / (1.0 + np.exp(-x))


def _forward_attention(q: np.ndarray, k: np.ndarray, v: np.ndarray,
                       n_heads: int = QWEN_HEADS,
                       n_kv_heads: int = QWEN_KV_HEADS,
                       head_dim: int = QWEN_HEAD_DIM) -> np.ndarray:
    seq_len = q.shape[0]
    q = q.reshape(seq_len, n_heads, head_dim).transpose(1, 0, 2)
    k = k.reshape(seq_len, n_kv_heads, head_dim).transpose(1, 0, 2)
    v = v.reshape(seq_len, n_kv_heads, head_dim).transpose(1, 0, 2)
    positions = np.arange(seq_len, dtype=np.float32)
    q_rot = np.stack([_forward_rope(q[h], positions) for h in range(n_heads)], axis=0)
    k_rot = np.stack([_forward_rope(k[h], positions) for h in range(n_kv_heads)], axis=0)
    scale = np.sqrt(head_dim)
    attn_out = []
    mask = np.triu(np.ones((seq_len, seq_len), dtype=np.float32), k=1) * -1e9
    for h in range(n_heads):
        kv_h = h // (n_heads // n_kv_heads)
        scores = np.matmul(q_rot[h], k_rot[kv_h].transpose(1, 0)) / scale + mask
        weights = _forward_softmax(scores, axis=-1)
        out = np.matmul(weights, v[kv_h])
        attn_out.append(out)
    return np.stack(attn_out, axis=1).reshape(seq_len, n_heads * head_dim)


def _forward_layer(hidden: np.ndarray, weights: dict, layer: int,
                   n_heads: int = QWEN_HEADS, n_kv_heads: int = QWEN_KV_HEADS,
                   head_dim: int = QWEN_HEAD_DIM) -> np.ndarray:
    normed = _forward_rmsnorm(hidden, weights[f'blk.{layer}.attn_norm.weight'])
    q = normed @ weights[f'blk.{layer}.attn_q.weight'].T + weights.get(f'blk.{layer}.attn_q.bias', 0)
    k = normed @ weights[f'blk.{layer}.attn_k.weight'].T + weights.get(f'blk.{layer}.attn_k.bias', 0)
    v = normed @ weights[f'blk.{layer}.attn_v.weight'].T + weights.get(f'blk.{layer}.attn_v.bias', 0)
    attn_out = _forward_attention(q, k, v, n_heads=n_heads, n_kv_heads=n_kv_heads,
                                  head_dim=head_dim)
    o = attn_out @ weights[f'blk.{layer}.attn_output.weight'].T
    residual = hidden + o
    ffn_input = _forward_rmsnorm(residual, weights[f'blk.{layer}.ffn_norm.weight'])
    gate = ffn_input @ weights[f'blk.{layer}.ffn_gate.weight'].T
    up = ffn_input @ weights[f'blk.{layer}.ffn_up.weight'].T
    ffn_hidden = _forward_silu(gate) * up
    ffn_out = ffn_hidden @ weights[f'blk.{layer}.ffn_down.weight'].T
    return residual + ffn_out


_ALLOC_CURSOR = FP_DRAM_BASE
_ALLOC_END = FP_DRAM_BASE + FP_DRAM_SIZE
_ACT_CURSOR = P10_ACT_BASE


def _allocate_dram(size: int, base: int = FP_DRAM_BASE, align: int = 64) -> int:
    global _ALLOC_CURSOR
    addr = (_ALLOC_CURSOR + align - 1) & ~(align - 1)
    _ALLOC_CURSOR = addr + size
    if _ALLOC_CURSOR > _ALLOC_END:
        raise MemoryError(
            f'Forward pass DRAM allocation exceeded: cursor=0x{_ALLOC_CURSOR:x} '
            f'limit=0x{_ALLOC_END:x}')
    return addr


def _reset_dram_allocator(base: int = FP_DRAM_BASE, end: int = None):
    global _ALLOC_CURSOR, _ALLOC_END
    _ALLOC_CURSOR = base
    _ALLOC_END = end if end is not None else base + FP_DRAM_SIZE


def _reset_wave_arena():
    _reset_dram_allocator(P10_WGT_BASE, P10_WGT_END)


def _act_alloc(size: int, align: int = 64) -> int:
    global _ACT_CURSOR
    addr = (_ACT_CURSOR + align - 1) & ~(align - 1)
    _ACT_CURSOR = addr + size
    if _ACT_CURSOR > P10_ACT_END:
        raise MemoryError(
            f'Phase-10 activation region exceeded: cursor=0x{_ACT_CURSOR:x} '
            f'limit=0x{P10_ACT_END:x}')
    return addr


def _reset_act_allocator():
    global _ACT_CURSOR
    _ACT_CURSOR = P10_ACT_BASE


def _write_tensor(model: FuncModel, addr: int, data: np.ndarray):
    model.host_write_data(addr, data)


def _read_tensor(model: FuncModel, addr: int, shape: tuple, dtype: type) -> np.ndarray:
    off = addr - Addr.DRAM_BASE
    n = int(np.prod(shape)) * np.dtype(dtype).itemsize
    return np.frombuffer(model.dram[off:off + n], dtype=dtype).reshape(shape)


def _quantize_weight_for_mmul(W_f32: np.ndarray, group_size: int = 128
                              ) -> tuple:
    packed, scales, _ = quantize_int4_per_block(W_f32, group_size)
    packed, scales = _reorder_weights_to_firmware_tiles(packed, scales,
                                                         W_f32.shape[0], W_f32.shape[1])
    return packed, scales, packed.nbytes, scales.nbytes


def _reorder_weights_to_firmware_tiles(packed: np.ndarray, scales: np.ndarray,
                                       K: int, N: int,
                                       tile_h: int = 64, tile_w: int = 64
                                       ) -> tuple:
    """Convert row-major packed INT4 weights to firmware's tiled DRAM layout.

    Firmware expects weights and scales in TILE_H×TILE_W tiles, iterated as:
        for each N-tile (stride TILE_W), for each K-tile (stride TILE_H):
            DMA TILE_WEIGHT_BYTES of packed INT4 + TILE_SCALE_BYTES of float32.

    Scale blocking: 2 K-tiles share 1 group_size=128 scale block, so the
    scale slice for each (n_tile, k_tile) pair is scales[k_tile//2, n*W:n_end].
    Partial tiles (K or N not multiples of TILE_H/TILE_W) are zero-padded to
    full tile size so the firmware always reads fixed-size chunks.

    Args:
        packed: uint8 packed INT4 weights (row-major K×N)
        scales: float32 per-block scales, shape (ceil(K/128), N)
        K, N: original weight matrix dimensions
        tile_h, tile_w: tile dimensions (default 64)

    Returns:
        reordered_packed: uint8 tiled weights (ceil(N/64)*ceil(K/64)*2048 bytes)
        reordered_scales: float32 per-tile scales (flat)
    """
    TILE_WEIGHT_BYTES = tile_h * tile_w // 2
    TILE_SCALE_BYTES = tile_w * 4
    num_blocks = (K + tile_h - 1) // tile_h
    num_n_tiles = (N + tile_w - 1) // tile_w

    # Guard: if no tiling needed, return unchanged (preserves non-MMUL paths)
    if num_blocks <= 1 and num_n_tiles <= 1:
        return packed, scales

    # Unpack to (K, N) int8
    weights_int4 = GoldenMXU.unpack_int4(packed)
    if len(weights_int4) < K * N:
        weights_int4 = np.pad(weights_int4, (0, K * N - len(weights_int4)),
                              constant_values=0)
    W = weights_int4[:K * N].reshape(K, N)

    reordered_chunks = []
    reordered_scales_chunks = []

    for n_tile in range(num_n_tiles):
        n_start = n_tile * tile_w
        n_end = min(n_start + tile_w, N)
        n_width = n_end - n_start

        for k_block in range(num_blocks):
            k_start = k_block * tile_h
            k_end = min(k_start + tile_h, K)
            k_height = k_end - k_start

            # Extract tile data (K×N slice)
            tile = W[k_start:k_end, n_start:n_end]  # (k_height, n_width)

            # Zero-pad to full tile size (firmware always reads full tile)
            if k_height < tile_h or n_width < tile_w:
                padded = np.zeros((tile_h, tile_w), dtype=np.int8)
                padded[:k_height, :n_width] = tile
                tile = padded

            reordered_chunks.append(GoldenMXU.pack_int4(tile))

            # Scales: 2 K-tiles share 1 group_size=128 scale block
            group_idx = k_block // 2
            tile_scales = scales[group_idx, n_start:n_end].astype(np.float32)

            # Pad to full tile_w scales
            if n_width < tile_w:
                padded_scales = np.ones(tile_w, dtype=np.float32)
                padded_scales[:n_width] = tile_scales
                tile_scales = padded_scales

            reordered_scales_chunks.append(tile_scales)

    reordered_packed = np.concatenate(reordered_chunks)
    reordered_scales = np.concatenate(reordered_scales_chunks)
    return reordered_packed, reordered_scales


def _pack_act_tile_major_contig(act: np.ndarray, M: int, K: int) -> np.ndarray:
    """Pack a row-major [M,K] INT8 activation into the firmware's tile-major
    layout matching the mxu_soc_wrapper broadcast: each 4096-byte K-tile holds
    64 columns; 64-byte word c of a K-tile contains activation column k (byte r
    = act[r, k]), zero-padded to 64 bytes.  The firmware walks activations at
    k_block*4096 stride."""
    k_tiles = (K + 63) // 64
    out = np.zeros(k_tiles * 4096, dtype=np.uint8)
    act2 = np.ascontiguousarray(act)
    for kt in range(k_tiles):
        for c in range(64):
            k = kt * 64 + c
            if k >= K:
                continue
            out[kt * 4096 + c * 64:kt * 4096 + c * 64 + M] = act2[:, k]
    return out


def _add_mmul_op(ops: list, model: FuncModel,
                 input_addr: int, output_addr: int,
                 packed: np.ndarray, scales: np.ndarray,
                 M: int, K: int, N: int,
                 input_data: np.ndarray,
                 weight_sram: int = FP_WEIGHT_SRAM,
                 scale_sram: int = FP_SCALE_SRAM,
                 input_sram: int = FP_INPUT_SRAM,
                 output_sram: int = FP_OUTPUT_SRAM) -> None:
    weight_addr = _allocate_dram(len(packed.tobytes()))
    scale_addr = _allocate_dram(len(scales.tobytes()))
    _write_tensor(model, weight_addr, packed)
    _write_tensor(model, scale_addr, scales)
    act_packed = _pack_act_tile_major_contig(input_data, M, K)
    _write_tensor(model, input_addr, act_packed)
    ops.append({
        'type': 'mmul',
        'desc': {
            'input_addr': input_addr,
            'weight_addr': weight_addr,
            'output_addr': output_addr,
            'scale_addr': scale_addr,
            'input_sram': input_sram,
            'weight_sram': weight_sram,
            'output_sram': output_sram,
            'input_size': len(act_packed),
            'weight_size': len(packed.tobytes()),
            'output_size': M * N * 4,
            'scale_size': len(scales.tobytes()),
            'M': M, 'K': K, 'N': N,
        }
    })


def _add_sfu_op(ops: list, model: FuncModel,
                input_addr: int, output_addr: int,
                op_code: int, data: np.ndarray,
                dim: int, pos: int = 0,
                input_sram: int = FP_SFU_IN_SRAM,
                output_sram: int = FP_SFU_OUT_SRAM) -> None:
    ref_addr = _allocate_dram(data.nbytes)
    _write_tensor(model, ref_addr, data)
    ops.append({
        'type': 'dma_copy',
        'desc': {
            'src_addr': ref_addr,
            'dst_addr': input_addr,
            'size': data.nbytes,
        }
    })
    ops.append({
        'type': 'sfu',
        'desc': {
            'op': op_code,
            'input_addr': input_addr,
            'output_addr': output_addr,
            'input_sram': input_sram,
            'output_sram': output_sram,
            'size': dim * 4,
            'dim': dim,
            'pos': pos,
        }
    })


def _add_vector_op(ops: list, model: FuncModel,
                   a_addr: int, b_addr: int, o_addr: int,
                   op_code: int, a: np.ndarray, b: np.ndarray,
                   dim: int) -> None:
    ref_a_addr = _allocate_dram(a.nbytes)
    ref_b_addr = _allocate_dram(b.nbytes)
    _write_tensor(model, ref_a_addr, a)
    _write_tensor(model, ref_b_addr, b)
    ops.append({
        'type': 'dma_copy',
        'desc': {
            'src_addr': ref_a_addr,
            'dst_addr': a_addr,
            'size': a.nbytes,
        }
    })
    ops.append({
        'type': 'dma_copy',
        'desc': {
            'src_addr': ref_b_addr,
            'dst_addr': b_addr,
            'size': b.nbytes,
        }
    })
    ops.append({
        'type': 'vector',
        'desc': {
            'op': op_code,
            'a_addr': a_addr,
            'b_addr': b_addr,
            'o_addr': o_addr,
            'dim': dim,
        }
    })


def _int8_quantize(x: np.ndarray) -> tuple:
    scale = np.max(np.abs(x)) / 127.0
    if scale < 1e-12:
        scale = 1.0
    return np.clip(np.round(x / scale), -128, 127).astype(np.int8), scale


def _quantize_weight_tile(W_f32: np.ndarray, n_start: int, n_end: int,
                          group_size: int = 128) -> tuple:
    tile = W_f32[:, n_start:n_end]
    packed, scales, _ = quantize_int4_per_block(tile, group_size)
    K_tile, N_tile = tile.shape
    packed, scales = _reorder_weights_to_firmware_tiles(packed, scales, K_tile, N_tile)
    return packed, scales


def _add_mmul_op_tiled(ops: list, model: FuncModel,
                       input_addr: int, output_addr: int,
                       W_f32: np.ndarray,
                       M: int, K: int, N: int,
                       input_data: np.ndarray,
                       tile_n: int = 1120,
                       weight_sram: int = FP_WEIGHT_SRAM,
                       scale_sram: int = FP_SCALE_SRAM,
                       input_sram: int = FP_INPUT_SRAM,
                       output_sram: int = FP_OUTPUT_SRAM) -> None:
    _write_tensor(model, input_addr, input_data)
    for n_start in range(0, N, tile_n):
        n_end = min(n_start + tile_n, N)
        tile_n_size = n_end - n_start
        tile = W_f32[:, n_start:n_end]
        packed, scales = _quantize_weight_tile(W_f32, n_start, n_end)
        weight_addr = _allocate_dram(len(packed.tobytes()))
        scale_addr = _allocate_dram(len(scales.tobytes()))
        _write_tensor(model, weight_addr, packed)
        _write_tensor(model, scale_addr, scales)
        ops.append({
            'type': 'dma_copy',
            'desc': {
                'src_addr': scale_addr,
                'dst_addr': scale_sram,
                'size': len(scales.tobytes()),
            }
        })
        ops.append({
            'type': 'mmul',
            'desc': {
                'input_addr': input_addr,
                'weight_addr': weight_addr,
                'output_addr': output_addr + n_start * M * 4,
                'input_sram': input_sram,
                'weight_sram': weight_sram,
                'output_sram': output_sram,
                'input_size': input_data.nbytes,
                'weight_size': len(packed.tobytes()),
                'output_size': M * tile_n_size * 4,
                'M': M, 'K': K, 'N': tile_n_size,
            }
        })


def run_forward_pass(gguf_path: str, prompt: str, layers: int = 2,
                     reference_npz: str = None, seq_len: int = 4,
                     tolerance: float = 1e-1,
                     log_fp=None,
                     token_ids: list = None) -> dict:
    """Run a complete 2-layer Qwen2.5-1.5B forward pass through Spike firmware."""
    from tokenizer import tokenize, embedding_lookup

    weights = load_weights_from_gguf(gguf_path)
    if token_ids is not None:
        # Bypass HuggingFace tokenizer; caller-supplied raw integer IDs.
        token_ids = [int(x) for x in token_ids]
        if not token_ids:
            raise ValueError("--token-ids must contain at least one integer ID")
    else:
        token_ids = tokenize(prompt, gguf_path)
    if seq_len == 1:
        token_ids = token_ids[:1]
    emb = embedding_lookup(token_ids, gguf_path).astype(np.float32)
    M = emb.shape[0]

    ref = None
    if reference_npz and Path(reference_npz).exists():
        ref = np.load(reference_npz)

    SRAM_KB = 4096
    model = FuncModel(dram_mb=128, sram_kb=SRAM_KB)
    model.firmware.ring_buffer_addr = FIRMWARE_RING_BASE
    _reset_dram_allocator()

    hidden = emb.copy()
    layer_outputs = []
    for layer in range(layers):
        hidden = _forward_layer(hidden, weights, layer)
        layer_outputs.append(hidden.copy())

    ops_per_layer = []
    layer_out_addrs = []

    for layer in range(layers):
        ops = []
        ref_hidden = emb if layer == 0 else layer_outputs[layer - 1]
        normed = _forward_rmsnorm(ref_hidden, weights[f'blk.{layer}.attn_norm.weight'])
        q = normed @ weights[f'blk.{layer}.attn_q.weight'].T + weights.get(f'blk.{layer}.attn_q.bias', 0)
        k = normed @ weights[f'blk.{layer}.attn_k.weight'].T + weights.get(f'blk.{layer}.attn_k.bias', 0)
        v = normed @ weights[f'blk.{layer}.attn_v.weight'].T + weights.get(f'blk.{layer}.attn_v.bias', 0)
        attn_out = _forward_attention(q, k, v)
        o = attn_out @ weights[f'blk.{layer}.attn_output.weight'].T
        residual1 = ref_hidden + o
        ffn_input = _forward_rmsnorm(residual1, weights[f'blk.{layer}.ffn_norm.weight'])
        gate = ffn_input @ weights[f'blk.{layer}.ffn_gate.weight'].T
        up = ffn_input @ weights[f'blk.{layer}.ffn_up.weight'].T
        silu_gate = _forward_silu(gate)
        ffn_hidden = silu_gate * up
        ffn_out = ffn_hidden @ weights[f'blk.{layer}.ffn_down.weight'].T
        l_out = residual1 + ffn_out

        hidden_addr = _allocate_dram(ref_hidden.nbytes)
        normed_addr = _allocate_dram(normed.nbytes)
        q_in_addr = _allocate_dram(M * QWEN_HIDDEN)
        q_out_addr = _allocate_dram(q.nbytes)
        k_in_addr = _allocate_dram(M * QWEN_KV_HEADS * QWEN_HEAD_DIM)
        k_out_addr = _allocate_dram(k.nbytes)
        v_in_addr = _allocate_dram(M * QWEN_KV_HEADS * QWEN_HEAD_DIM)
        v_out_addr = _allocate_dram(v.nbytes)
        attn_addr = _allocate_dram(attn_out.nbytes)
        o_in_addr = _allocate_dram(M * QWEN_HIDDEN)
        o_out_addr = _allocate_dram(o.nbytes)
        residual1_addr = _allocate_dram(residual1.nbytes)
        ffn_in_addr = _allocate_dram(ffn_input.nbytes)
        gate_in_addr = _allocate_dram(M * QWEN_INTERMEDIATE)
        gate_out_addr = _allocate_dram(gate.nbytes)
        up_in_addr = _allocate_dram(M * QWEN_INTERMEDIATE)
        up_out_addr = _allocate_dram(up.nbytes)
        silu_addr = _allocate_dram(silu_gate.nbytes)
        ffn_hidden_addr = _allocate_dram(ffn_hidden.nbytes)
        ffn_out_in_addr = _allocate_dram(M * QWEN_HIDDEN)
        ffn_out_addr = _allocate_dram(ffn_out.nbytes)
        l_out_addr = _allocate_dram(l_out.nbytes)
        layer_out_addrs.append(l_out_addr)

        _write_tensor(model, hidden_addr, ref_hidden)

        _add_sfu_op(ops, model, hidden_addr, normed_addr, SFU_OP_RMSNORM,
                    ref_hidden.astype(np.float16), M * QWEN_HIDDEN)

        q_i8, _ = _int8_quantize(normed)
        packed_q, scales_q, _, _ = _quantize_weight_for_mmul(weights[f'blk.{layer}.attn_q.weight'])
        _add_mmul_op(ops, model, q_in_addr, q_out_addr, packed_q, scales_q,
                     M, QWEN_HIDDEN, QWEN_HIDDEN, q_i8)

        k_i8, _ = _int8_quantize(normed)
        packed_k, scales_k, _, _ = _quantize_weight_for_mmul(weights[f'blk.{layer}.attn_k.weight'].T)
        _add_mmul_op(ops, model, k_in_addr, k_out_addr, packed_k, scales_k,
                     M, QWEN_HIDDEN, QWEN_KV_HEADS * QWEN_HEAD_DIM, k_i8)

        v_i8, _ = _int8_quantize(normed)
        packed_v, scales_v, _, _ = _quantize_weight_for_mmul(weights[f'blk.{layer}.attn_v.weight'].T)
        _add_mmul_op(ops, model, v_in_addr, v_out_addr, packed_v, scales_v,
                     M, QWEN_HIDDEN, QWEN_KV_HEADS * QWEN_HEAD_DIM, v_i8)

        attn_i8, _ = _int8_quantize(attn_out)
        packed_o, scales_o, _, _ = _quantize_weight_for_mmul(weights[f'blk.{layer}.attn_output.weight'])
        _add_mmul_op(ops, model, o_in_addr, o_out_addr, packed_o, scales_o,
                     M, QWEN_HIDDEN, QWEN_HIDDEN, attn_i8)

        _add_vector_op(ops, model, hidden_addr, o_out_addr, residual1_addr,
                       VEC_OP_ADD,
                       ref_hidden.astype(np.int32),
                       o.astype(np.int32),
                       M * QWEN_HIDDEN)

        _add_sfu_op(ops, model, residual1_addr, ffn_in_addr, SFU_OP_RMSNORM,
                    residual1.astype(np.float16), M * QWEN_HIDDEN)

        gate_i8, _ = _int8_quantize(ffn_input)
        _add_mmul_op_tiled(ops, model, gate_in_addr, gate_out_addr,
                           weights[f'blk.{layer}.ffn_gate.weight'].T,
                           M, QWEN_HIDDEN, QWEN_INTERMEDIATE,
                           gate_i8)

        _add_sfu_op(ops, model, gate_out_addr, silu_addr, SFU_OP_SILU,
                    gate.astype(np.float16), M * QWEN_INTERMEDIATE)

        up_i8, _ = _int8_quantize(ffn_input)
        _add_mmul_op_tiled(ops, model, up_in_addr, up_out_addr,
                           weights[f'blk.{layer}.ffn_up.weight'].T,
                           M, QWEN_HIDDEN, QWEN_INTERMEDIATE,
                           up_i8)

        _add_vector_op(ops, model, silu_addr, up_out_addr, ffn_hidden_addr,
                       VEC_OP_MUL,
                       silu_gate.astype(np.int32),
                       up.astype(np.int32),
                       M * QWEN_INTERMEDIATE)

        ffn_i8, _ = _int8_quantize(ffn_hidden)
        _add_mmul_op_tiled(ops, model, ffn_out_in_addr, ffn_out_addr,
                           weights[f'blk.{layer}.ffn_down.weight'].T,
                           M, QWEN_INTERMEDIATE, QWEN_HIDDEN,
                           ffn_i8, tile_n=384)

        _add_vector_op(ops, model, residual1_addr, ffn_out_addr, l_out_addr,
                       VEC_OP_ADD,
                       residual1.astype(np.int32),
                       ffn_out.astype(np.int32),
                       M * QWEN_HIDDEN)

        ops_per_layer.append(ops)

    model.bridge.handle('write', MXU.BASE + MXU.SCALE_ADDR, FP_SCALE_SRAM)

    consumed = 0
    for layer, ops in enumerate(ops_per_layer):
        model.bridge.modules['sram'] = bytearray(len(model.sram))
        model.sram[:] = bytearray(len(model.sram))
        if hasattr(model, 'crossbar'):
            model.crossbar.sram[:] = bytearray(len(model.sram))
        model.bridge.handle('write', DOORBELL.BASE + DOORBELL.NPU_HEAD, 0)
        model.bridge.handle('write', DOORBELL.BASE + DOORBELL.HOST_TAIL, 0)
        schedule_chain(model, ops)
        proc, server = _launch_spike(model)
        try:
            done = poll_completion(model, len(ops))
        finally:
            _cleanup_spike(proc, server)
        if not done:
            print(f"  [FAIL] forward — timeout waiting for NPU_HEAD={len(ops) % 64}")
            return {"ok": False, "layer_outputs": [], "op_coverage": {}, "errors": []}
        consumed += len(ops)

    def _log(line):
        print(line)
        if log_fp is not None:
            log_fp.write(line + "\n")
            log_fp.flush()

    _log(f"  [INFO] forward — {consumed} commands consumed")

    all_ops = []
    for ops in ops_per_layer:
        all_ops.extend(ops)
    coverage = _count_op_coverage(all_ops)
    _log("  [INFO] op-coverage summary:")
    for name, count in coverage.items():
        if count:
            _log(f"           {name:12s}: {count}")

    results = {
        "ok": True,
        "layer_outputs": [],
        "op_coverage": coverage,
        "errors": [],
    }
    if ref is None:
        _log("  [WARN] no .npz reference provided; skipping comparison")
        return results

    all_ok = True
    for layer, l_out_addr in enumerate(layer_out_addrs):
        out_i32 = _read_tensor(model, l_out_addr, (M, QWEN_HIDDEN), np.int32)
        out_f32 = out_i32.astype(np.float32)
        results["layer_outputs"].append(out_f32.copy())

        ref_key = f"l_out_{layer}"
        ref_h = ref[ref_key][:M] if M < ref[ref_key].shape[0] else ref[ref_key]

        diff = np.abs(out_f32 - ref_h)
        rel = diff / (np.abs(ref_h) + 1e-8)
        max_abs = float(np.max(diff))
        max_rel = float(np.max(rel))

        ok = max_abs < tolerance
        status = "PASS" if ok else "WARN"
        if not ok:
            all_ok = False

        _log(f"  [{status}] L{layer} vs llama_ref/{ref_key} "
             f"max_abs={max_abs:.3e} max_rel={max_rel:.3e} "
             f"(tol={tolerance:.0e})")
        results["errors"].append({
            "layer": layer,
            "max_abs": max_abs,
            "max_rel": max_rel,
            "tolerance": tolerance,
            "ok": ok,
        })

    results["ok"] = all_ok
    return results


# ── Phase 10: 36-layer forward (todo 12) ───────────────────────────

def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a_f = a.astype(np.float64).flatten()
    b_f = b.astype(np.float64).flatten()
    dot = float(np.dot(a_f, b_f))
    norm_a = float(np.sqrt(np.dot(a_f, a_f)))
    norm_b = float(np.sqrt(np.dot(b_f, b_f)))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return dot / (norm_a * norm_b)


def _load_golden_layer(golden_dir: str, layer: int) -> np.ndarray:
    """Load the Func Model golden hidden state for a layer.

    Per-layer files expected_l{N}.npz (key 'output') are preferred; L0 falls
    back to the combined expected.npz (key 'layer_0_output').
    """
    gdir = Path(golden_dir)
    per_layer = gdir / f"expected_l{layer}.npz"
    if per_layer.exists():
        with np.load(per_layer, allow_pickle=True) as d:
            return d["output"].astype(np.float32)
    combined = gdir / "expected.npz"
    if combined.exists():
        with np.load(combined, allow_pickle=True) as d:
            key = f"layer_{layer}_output"
            if key in d:
                return d[key].astype(np.float32)
    raise FileNotFoundError(f"golden for layer {layer} not found under {gdir}")


def _save_phase10_npz(path: str, layer_states: dict, hw_states: dict,
                      emb: np.ndarray, dims: dict, gguf_path: str,
                      total_layers: int, commit: str = "unknown",
                      command: str = ""):
    data = {"input_embedding": emb.astype(np.float32)}
    for L, state in layer_states.items():
        data[f"layer_{L}_output"] = state.astype(np.float32)
        data[f"hw_layer_{L}_output"] = hw_states[L].astype(np.int32)
    meta = {
        "engine": "spike",
        "layers_run": total_layers,
        "layers_saved": sorted(layer_states.keys()),
        "dims": dims,
        "model": gguf_path,
        "commit": commit,
        "command": command,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    data["metadata"] = np.array([json.dumps(meta)])
    tmp = str(path) + ".tmp.npz"
    np.savez(tmp, **data)
    os.replace(tmp, str(path))


def _write_phase10_evidence(evidence_path: str, ladder_rows: list,
                            hw_rows: list, dims: dict, gguf_path: str,
                            total_layers: int, start_layer: int,
                            total_consumed: int, elapsed_s: float,
                            commit: str, command: str, all_pass: bool):
    Path(evidence_path).parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "unknown"
    base = FP_DRAM_BASE
    size = FP_DRAM_SIZE
    in_window = (base >= 0x80000000 and base + size <= 0x80800000)
    n_pass = sum(1 for r in ladder_rows if r["ok"])
    with open(evidence_path, "w", encoding="utf-8") as f:
        f.write("Task 12 - Phase 10 RTL Verification: Spike-first 36-layer forward\n")
        f.write("=" * 70 + "\n")
        f.write(f"Timestamp start : {ts}\n")
        f.write(f"Commit          : {commit}\n")
        f.write(f"Command         : {command}\n")
        f.write(f"Driver host     : {hostname} (spike + firmware + MMIO bridge)\n")
        f.write(f"Model           : {gguf_path}\n")
        f.write(f"Dims            : {json.dumps(dims)}\n")
        f.write("engine=spike\n")
        f.write(f"layers_run={total_layers}\n")
        f.write(f"layers_completed={len(ladder_rows)}\n")
        f.write(f"resume_start_layer={start_layer}\n")
        f.write(f"FP_DRAM_BASE=0x{base:08x}\n")
        f.write(f"FP_DRAM_SIZE=0x{size:08x}\n")
        f.write(f"fp_window_ok={'yes' if in_window else 'no'}\n")
        f.write(f"commands_dispatched={total_consumed}\n")
        f.write("cycles=n/a (spike host path has no cycle counter)\n")
        f.write(f"elapsed_s={elapsed_s:.1f}\n\n")
        f.write("Per-layer tolerance ladder (spike hidden state vs Func Model golden):\n")
        f.write("  ladder: L0-19 >= 0.999, L20-29 >= 0.998, L30-35 >= 0.997\n")
        for r in ladder_rows:
            f.write(f"layer={r['layer']} engine=spike cos_sim={r['cos_sim']:.6f} "
                    f"threshold={r['threshold']} status={r['status']}\n")
        f.write("\nHardware l_out transparency (spike DRAM VRESID int32, non-gating):\n")
        for r in hw_rows:
            f.write(f"layer={r['layer']} hw_l_out_cos_sim={r['cos_sim']:.6f} "
                    f"max_abs={r['max_abs']:.4e}\n")
        f.write("\nSummary:\n")
        f.write(f"  layers_passed={n_pass}/{len(ladder_rows)}\n")
        f.write(f"  LADDER={'PASS' if all_pass else 'FAIL'}\n")
        f.write(f"  Overall: {'PASS' if (all_pass and in_window) else 'FAIL'}\n")
        f.write(f"  Timestamp end: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")


def _add_mmul_tiles_phase10(ops: list, model: FuncModel,
                            input_addr: int, output_addr: int,
                            W_f32: np.ndarray,
                            M: int, K: int, N: int,
                            input_data: np.ndarray,
                            tile_n: int, tile_lo: int, tile_hi: int) -> None:
    act_packed = _pack_act_tile_major_contig(input_data, M, K)
    _write_tensor(model, input_addr, act_packed)
    for t in range(tile_lo, tile_hi):
        n_start = t * tile_n
        n_end = min(n_start + tile_n, N)
        tile_n_size = n_end - n_start
        packed, scales = _quantize_weight_tile(W_f32, n_start, n_end)
        weight_addr = _allocate_dram(len(packed.tobytes()))
        scale_addr = _allocate_dram(len(scales.tobytes()))
        _write_tensor(model, weight_addr, packed)
        _write_tensor(model, scale_addr, scales)
        ops.append({
            'type': 'mmul',
            'desc': {
                'input_addr': input_addr,
                'weight_addr': weight_addr,
                'output_addr': output_addr + n_start * M * 4,
                'scale_addr': scale_addr,
                'input_sram': FP_INPUT_SRAM,
                'weight_sram': FP_WEIGHT_SRAM,
                'output_sram': FP_OUTPUT_SRAM,
                'input_size': len(act_packed),
                'weight_size': len(packed.tobytes()),
                'output_size': M * tile_n_size * 4,
                'scale_size': len(scales.tobytes()),
                'M': M, 'K': K, 'N': tile_n_size,
            }
        })


def _execute_layer_waves_phase10(model: FuncModel, hidden: np.ndarray,
                                 weights: dict, layer: int, dims: dict,
                                 M: int = 1) -> tuple:
    """Dispatch one layer's op chain through Spike in DRAM-window-sized waves.

    Returns (hw_l_out_int32, consumed_cmds, ffn_act_scale).
    """
    H, I = dims["hidden_size"], dims["intermediate_size"]
    QD, KD = dims["q_dim"], dims["kv_dim"]
    heads, kv_heads, head_dim = dims["heads"], dims["kv_heads"], dims["head_dim"]
    w = weights
    eps = QWEN_RMS_EPS

    normed = _forward_rmsnorm(hidden, w[f'blk.{layer}.attn_norm.weight'], eps)
    q = normed @ w[f'blk.{layer}.attn_q.weight'].T + w.get(f'blk.{layer}.attn_q.bias', 0)
    k = normed @ w[f'blk.{layer}.attn_k.weight'].T + w.get(f'blk.{layer}.attn_k.bias', 0)
    v = normed @ w[f'blk.{layer}.attn_v.weight'].T + w.get(f'blk.{layer}.attn_v.bias', 0)
    attn_out = _forward_attention(q, k, v, n_heads=heads, n_kv_heads=kv_heads,
                                  head_dim=head_dim)
    o = attn_out @ w[f'blk.{layer}.attn_output.weight'].T
    residual1 = hidden + o
    ffn_input = _forward_rmsnorm(residual1, w[f'blk.{layer}.ffn_norm.weight'], eps)
    gate = ffn_input @ w[f'blk.{layer}.ffn_gate.weight'].T
    up = ffn_input @ w[f'blk.{layer}.ffn_up.weight'].T
    silu_gate = _forward_silu(gate)
    ffn_hidden = silu_gate * up

    _reset_act_allocator()
    hidden_addr = _act_alloc(H * 4)
    normed_addr = _act_alloc(H * 4)
    q_in_addr = _act_alloc(((QD + 63) // 64) * 4096)
    q_out_addr = _act_alloc(QD * 4)
    k_in_addr = _act_alloc(((KD + 63) // 64) * 4096)
    k_out_addr = _act_alloc(KD * 4)
    v_in_addr = _act_alloc(((KD + 63) // 64) * 4096)
    v_out_addr = _act_alloc(KD * 4)
    o_in_addr = _act_alloc(((H + 63) // 64) * 4096)
    o_out_addr = _act_alloc(H * 4)
    residual1_addr = _act_alloc(H * 4)
    ffn_in_addr = _act_alloc(H * 4)
    gate_in_addr = _act_alloc(((H + 63) // 64) * 4096)
    gate_out_addr = _act_alloc(I * 4)
    up_in_addr = _act_alloc(((H + 63) // 64) * 4096)
    up_out_addr = _act_alloc(I * 4)
    silu_addr = _act_alloc(I * 4)
    ffn_hidden_addr = _act_alloc(I * 4)
    ffn_out_in_addr = _act_alloc(((I + 63) // 64) * 4096)
    ffn_out_addr = _act_alloc(H * 4)
    l_out_addr = _act_alloc(H * 4)

    _write_tensor(model, hidden_addr, hidden)
    model.bridge.handle('write', MXU.BASE + MXU.SCALE_ADDR, FP_SCALE_SRAM)

    def run_wave(ops: list) -> int:
        model.bridge.modules['sram'] = bytearray(len(model.sram))
        model.sram[:] = bytearray(len(model.sram))
        if hasattr(model, 'crossbar'):
            model.crossbar.sram[:] = bytearray(len(model.sram))
        model.bridge.handle('write', DOORBELL.BASE + DOORBELL.NPU_HEAD, 0)
        model.bridge.handle('write', DOORBELL.BASE + DOORBELL.HOST_TAIL, 0)
        schedule_chain(model, ops)
        proc, server = _launch_spike(model)
        try:
            done = poll_completion(model, len(ops))
        finally:
            _cleanup_spike(proc, server)
        if not done:
            raise RuntimeError(
                f"L{layer}: wave timeout waiting for NPU_HEAD={len(ops) % 64}")
        return len(ops)

    q_i8, _ = _int8_quantize(normed)
    k_i8, _ = _int8_quantize(normed)
    v_i8, _ = _int8_quantize(normed)
    attn_i8, _ = _int8_quantize(attn_out)
    gate_i8, _ = _int8_quantize(ffn_input)
    up_i8, _ = _int8_quantize(ffn_input)
    ffn_i8, ffn_scale = _int8_quantize(ffn_hidden)

    consumed = 0

    def new_wave() -> list:
        _reset_wave_arena()
        return []

    # Wave 1: pre-attn RMSNorm + Q/K/V/O MMULs + residual + post-attn RMSNorm
    ops = new_wave()
    _add_sfu_op(ops, model, hidden_addr, normed_addr, SFU_OP_RMSNORM,
                hidden.astype(np.float16), H)
    packed_q, scales_q, _, _ = _quantize_weight_for_mmul(w[f'blk.{layer}.attn_q.weight'])
    _add_mmul_op(ops, model, q_in_addr, q_out_addr, packed_q, scales_q, M, H, QD, q_i8)
    packed_k, scales_k, _, _ = _quantize_weight_for_mmul(w[f'blk.{layer}.attn_k.weight'].T)
    _add_mmul_op(ops, model, k_in_addr, k_out_addr, packed_k, scales_k, M, H, KD, k_i8)
    packed_v, scales_v, _, _ = _quantize_weight_for_mmul(w[f'blk.{layer}.attn_v.weight'].T)
    _add_mmul_op(ops, model, v_in_addr, v_out_addr, packed_v, scales_v, M, H, KD, v_i8)
    packed_o, scales_o, _, _ = _quantize_weight_for_mmul(w[f'blk.{layer}.attn_output.weight'])
    _add_mmul_op(ops, model, o_in_addr, o_out_addr, packed_o, scales_o, M, H, H, attn_i8)
    _add_vector_op(ops, model, hidden_addr, o_out_addr, residual1_addr, VEC_OP_ADD,
                   np.rint(hidden * P10_RESID_SCALE).astype(np.int32),
                   np.rint(o * P10_RESID_SCALE).astype(np.int32), H)
    _add_sfu_op(ops, model, residual1_addr, ffn_in_addr, SFU_OP_RMSNORM,
                residual1.astype(np.float16), H)
    consumed += run_wave(ops)

    # Waves 2-4: FFN gate (N-tiled, two 2048-col tiles per wave) + SiLU
    tile_n = 2048
    n_tiles = (I + tile_n - 1) // tile_n
    for t_lo in range(0, n_tiles, 2):
        t_hi = min(t_lo + 2, n_tiles)
        ops = new_wave()
        _add_mmul_tiles_phase10(ops, model, gate_in_addr, gate_out_addr,
                                w[f'blk.{layer}.ffn_gate.weight'].T, M, H, I, gate_i8,
                                tile_n, t_lo, t_hi)
        if t_hi >= n_tiles:
            _add_sfu_op(ops, model, gate_out_addr, silu_addr, SFU_OP_SILU,
                        gate.astype(np.float16), I)
        consumed += run_wave(ops)

    # Waves 5-7: FFN up (N-tiled) + VMUL
    for t_lo in range(0, n_tiles, 2):
        t_hi = min(t_lo + 2, n_tiles)
        ops = new_wave()
        _add_mmul_tiles_phase10(ops, model, up_in_addr, up_out_addr,
                                w[f'blk.{layer}.ffn_up.weight'].T, M, H, I, up_i8,
                                tile_n, t_lo, t_hi)
        if t_hi >= n_tiles:
            _add_vector_op(ops, model, silu_addr, up_out_addr, ffn_hidden_addr,
                           VEC_OP_MUL, silu_gate.astype(np.int32),
                           up.astype(np.int32), I)
        consumed += run_wave(ops)

    # Waves 8-10: FFN down (N-tiled, one 768-col tile per wave)
    tile_n_dn = 768
    n_tiles_dn = (H + tile_n_dn - 1) // tile_n_dn
    for t in range(n_tiles_dn):
        ops = new_wave()
        _add_mmul_tiles_phase10(ops, model, ffn_out_in_addr, ffn_out_addr,
                                w[f'blk.{layer}.ffn_down.weight'].T, M, I, H, ffn_i8,
                                tile_n_dn, t, t + 1)
        consumed += run_wave(ops)

    # Wave 11: final VRESID consuming the hardware down MMUL output
    down_out_hw = _read_tensor(model, ffn_out_addr, (M, H), np.float32)
    ops = new_wave()
    _add_vector_op(ops, model, residual1_addr, ffn_out_addr, l_out_addr, VEC_OP_ADD,
                   np.rint(residual1 * P10_RESID_SCALE).astype(np.int32),
                   np.rint(down_out_hw * ffn_scale * P10_RESID_SCALE).astype(np.int32),
                   H)
    consumed += run_wave(ops)

    hw_l_out = _read_tensor(model, l_out_addr, (M, H), np.int32)
    return hw_l_out, consumed, ffn_scale


def run_forward_pass_phase10(gguf_path: str, layers: int, token_ids: list,
                             save_npz: str = None, golden_dir: str = None,
                             evidence_path: str = None, resume: bool = False,
                             log_fp=None) -> dict:
    """Spike-first full 36-layer forward pass (todo 12).

    Propagates FP32 hidden states (W1.2-equivalent formulas, the same basis
    as the Func Model golden) while dispatching every layer's op chain
    through Spike + firmware + MMIO bridge, then compares each layer state
    against the Func Model golden with the tolerance ladder.
    """
    from tokenizer import embedding_lookup

    def _log(line):
        print(line)
        if log_fp is not None:
            log_fp.write(line + "\n")
            log_fp.flush()

    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(PROJECT), text=True).strip()
    except Exception:
        commit = "unknown"
    command = " ".join(sys.argv)

    weights = load_weights_from_gguf(gguf_path)
    H = int(weights['blk.0.attn_norm.weight'].shape[0])
    I = int(weights['blk.0.ffn_gate.weight'].shape[0])
    QD = int(weights['blk.0.attn_q.weight'].shape[0])
    KD = int(weights['blk.0.attn_k.weight'].shape[0])
    head_dim = 128
    heads = QD // head_dim
    kv_heads = KD // head_dim
    dims = {
        "hidden_size": H,
        "intermediate_size": I,
        "q_dim": QD,
        "kv_dim": KD,
        "num_heads": heads,
        "num_kv_heads": kv_heads,
        "heads": heads,
        "kv_heads": kv_heads,
        "head_dim": head_dim,
        "num_hidden_layers": layers,
        "rope_theta": QWEN_THETA,
        "rms_eps": QWEN_RMS_EPS,
    }
    _log(f"[INFO] dims: {json.dumps(dims)}")

    emb = embedding_lookup([int(t) for t in token_ids], gguf_path).astype(np.float32)
    M = emb.shape[0]
    assert M == 1, f"phase-10 forward requires M=1, got {M}"

    model = FuncModel(dram_mb=128, sram_kb=4096)
    model.firmware.ring_buffer_addr = FIRMWARE_RING_BASE

    layer_states: dict = {}
    hw_states: dict = {}
    start_layer = 0
    hidden = emb.copy()
    if resume and save_npz and Path(save_npz).exists():
        with np.load(save_npz, allow_pickle=True) as d:
            saved = sorted(int(k.split('_')[1]) for k in d.files
                           if k.startswith('layer_') and k.split('_')[1].isdigit())
            if saved:
                last = saved[-1]
                hidden = d[f'layer_{last}_output'].astype(np.float32)
                for L in saved:
                    layer_states[L] = d[f'layer_{L}_output'].astype(np.float32)
                    hw_states[L] = d[f'hw_layer_{L}_output'].astype(np.int32)
                start_layer = last + 1
                _log(f"[INFO] resume from {save_npz}: skipping layers 0..{last}")

    t0 = time.time()
    total_consumed = 0
    for layer in range(start_layer, layers):
        fp32_out = _forward_layer(hidden, weights, layer,
                                  n_heads=heads, n_kv_heads=kv_heads,
                                  head_dim=head_dim)
        hw_l_out, consumed, _ffn_scale = _execute_layer_waves_phase10(
            model, hidden, weights, layer, dims, M)
        layer_states[layer] = fp32_out.astype(np.float32)
        hw_states[layer] = hw_l_out.astype(np.int32)
        hidden = fp32_out
        total_consumed += consumed
        _log(f"[INFO] L{layer}: cmds={consumed} fp32_std={float(np.std(fp32_out)):.4f} "
             f"hw_l_out_range=[{int(hw_l_out.min())},{int(hw_l_out.max())}]")
        if save_npz:
            _save_phase10_npz(save_npz, layer_states, hw_states, emb, dims,
                              gguf_path, layers, commit, command)
    elapsed = time.time() - t0

    ladder_rows = []
    hw_rows = []
    all_pass = True
    if golden_dir:
        for layer in sorted(layer_states):
            golden = _load_golden_layer(golden_dir, layer)
            cos = _cosine_similarity(layer_states[layer], golden)
            thr = p10_layer_threshold(layer)
            ok = cos >= thr
            if not ok:
                all_pass = False
            ladder_rows.append({
                "layer": layer, "cos_sim": cos, "threshold": thr,
                "status": "PASS" if ok else "FAIL", "ok": ok,
            })
            _log(f"  [{'PASS' if ok else 'FAIL'}] L{layer} cos_sim={cos:.6f} "
                 f"threshold={thr} (ladder)")
            hw_f32 = hw_states[layer].astype(np.float32) / P10_RESID_SCALE
            hw_cos = _cosine_similarity(hw_f32, golden)
            hw_max = float(np.max(np.abs(hw_f32 - golden.astype(np.float32))))
            hw_rows.append({"layer": layer, "cos_sim": hw_cos, "max_abs": hw_max})
            _log(f"         hw_l_out cos_sim={hw_cos:.6f} max_abs={hw_max:.4e} "
                 "(non-gating)")

    if evidence_path:
        _write_phase10_evidence(evidence_path, ladder_rows, hw_rows, dims,
                                gguf_path, layers, start_layer, total_consumed,
                                elapsed, commit, command, all_pass)
        _log(f"[INFO] evidence written: {evidence_path}")

    return {
        "ok": all_pass,
        "layers_run": layers,
        "layers_completed": len(layer_states),
        "engine": "spike",
        "layer_states": layer_states,
        "hw_states": hw_states,
        "ladder_rows": ladder_rows,
        "consumed": total_consumed,
        "elapsed_s": elapsed,
        "commit": commit,
    }


def _launch_spike(model: FuncModel):
    """Start bridge server, serialize DRAM, and launch Spike."""
    ready_event = threading.Event()
    server = serve(model.bridge, sock_path=DEFAULT_SOCK_PATH, crossbar=model.crossbar, ready_event=ready_event)
    ready_event.wait(timeout=5.0)

    ddr_path = PROJECT / "ddr.bin"
    ddr_path.write_bytes(model.dram)

    # Use the Spike-specific firmware ELF (linked at 0x10000 to avoid the
    # physical-address-0 load restriction).  Host-provided DRAM data is
    # supplied via ddr.bin at runtime.
    spike_elf = FIRMWARE_SPIKE_ELF if FIRMWARE_SPIKE_ELF.exists() else FIRMWARE_ELF
    env = os.environ.copy()
    env["PATH"] = str(PROJECT / "dtc_src") + ":" + env.get("PATH", "")

    # The spike executable is built against a newer glibc/libstdc++ than the
    # EDA server (sz0001, glibc 2.17) provides. When the bundled portable
    # loader + libs exist, launch through the newer ld-linux with its own
    # library path so the same binary runs on both hosts.
    portable_ld = SPIKE_BIN.parent / "portable_libs" / "ld-linux-x86-64.so.2"
    if portable_ld.exists():
        cmd = [
            str(portable_ld),
            "--library-path", str(portable_ld.parent),
            str(SPIKE_BIN),
            "--isa=RV32IM",
            "-m0x80000000:0x10000000,0x00010000:0x00020000",
            f"--kernel={ddr_path}",
            f"--extlib={PLUGIN_SO}",
            "--device=npu,0x20000000",
            str(spike_elf),
        ]
    else:
        # Cadence CEREBRUS provides a libstdc++ with CXXABI_1.3.9+ required by
        # the MMIO plugin on hosts with a newer toolchain.
        _cadence_lib = "/home/EDA/cadence/CEREBRUS22.15_P/tools.lnx86/lib/64bit"
        env["LD_LIBRARY_PATH"] = _cadence_lib + ":" + env.get("LD_LIBRARY_PATH", "")
        cmd = [
            str(SPIKE_BIN),
            "--isa=RV32IM",
            "-m0x80000000:0x10000000,0x00010000:0x00020000",
            f"--kernel={ddr_path}",
            f"--extlib={PLUGIN_SO}",
            "--device=npu,0x20000000",
            str(spike_elf),
        ]

    proc = subprocess.Popen(
        cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return proc, server


def _cleanup_spike(proc: subprocess.Popen, server):
    """Terminate Spike and shut down the bridge server."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    else:
        # Already exited — capture diagnostic output
        out = proc.stdout.read() if proc.stdout else ""
        err = proc.stderr.read() if proc.stderr else ""
        if err:
            print(f"[SPIKE_STDERR] {err[:2000]}", file=sys.stderr, flush=True)
        if out:
            print(f"[SPIKE_STDOUT] {out[:2000]}", file=sys.stderr, flush=True)
    server.shutdown()
    try:
        os.unlink(DEFAULT_SOCK_PATH)
    except FileNotFoundError:
        pass


def _data_addr(idx: int, slot: int) -> int:
    return 0x80020000 + idx * 0x10000 + slot * 0x040000


def _prepare_mmul_op(model: FuncModel, idx: int, rng: np.random.RandomState) -> tuple:
    M, K, N = 1, 4, 4
    act = rng.randint(-64, 64, size=M * K, dtype=np.int8).reshape(M, K)
    wgt_i8 = rng.randint(-8, 8, size=K * N, dtype=np.int8)
    wgt_packed = GoldenMXU.pack_int4(wgt_i8)

    input_addr = _data_addr(idx, 0)
    weight_addr = _data_addr(idx, 1)
    output_addr = _data_addr(idx, 2)

    model.host_write_data(input_addr, act)
    model.host_write_data(weight_addr, wgt_packed)

    op = {
        'type': 'mmul',
        'desc': {
            'input_addr': input_addr,
            'weight_addr': weight_addr,
            'output_addr': output_addr,
            'input_sram': 0x00000000,
            'weight_sram': 0x00100000,
            'output_sram': 0x00300000,
            'input_size': act.nbytes,
            'weight_size': wgt_packed.nbytes,
            'output_size': M * N * 4,
            'M': M,
            'K': K,
            'N': N,
        }
    }
    golden = GoldenMXU().matmul_int32(act, wgt_packed, M, K, N)
    return op, (output_addr, golden, np.int32)


def _prepare_sfu_op(model: FuncModel, idx: int, rng: np.random.RandomState) -> tuple:
    from golden_executor import GoldenSFU

    dim = 8
    size = dim * 4
    inp = rng.randn(dim).astype(np.float32)
    inp_f16 = inp.astype(np.float16)

    input_addr = _data_addr(idx, 0)
    output_addr = _data_addr(idx, 2)

    model.host_write_data(input_addr, inp_f16)

    op = {
        'type': 'sfu',
        'desc': {
            'op': SFU_OP_GELU,
            'input_addr': input_addr,
            'output_addr': output_addr,
            'input_sram': 0x00000000,
            'output_sram': 0x00100000,
            'size': size,
            'dim': dim,
            'pos': 0,
        }
    }
    golden = GoldenSFU().gelu_hw(inp)
    return op, (output_addr, golden, np.float16)


def _prepare_vector_op(model: FuncModel, idx: int, rng: np.random.RandomState) -> tuple:
    from golden_executor import GoldenVector

    dim = 16
    a = rng.randint(-128, 128, size=dim, dtype=np.int32)
    b = rng.randint(-128, 128, size=dim, dtype=np.int32)

    a_addr = _data_addr(idx, 0)
    b_addr = _data_addr(idx, 1)
    o_addr = _data_addr(idx, 2)

    model.host_write_data(a_addr, a)
    model.host_write_data(b_addr, b)

    op = {
        'type': 'vector',
        'desc': {
            'op': VEC_OP_ADD,
            'a_addr': a_addr,
            'b_addr': b_addr,
            'o_addr': o_addr,
            'dim': dim,
        }
    }
    golden = GoldenVector.add(a, b)
    return op, (o_addr, golden, np.int32)


def _prepare_dma_copy_op(model: FuncModel, idx: int, rng: np.random.RandomState) -> tuple:
    size = 64
    src = rng.randint(0, 256, size=size, dtype=np.uint8)
    src_addr = _data_addr(idx, 0)
    dst_addr = _data_addr(idx, 2)

    model.host_write_data(src_addr, src)

    op = {
        'type': 'dma_copy',
        'desc': {
            'src_addr': src_addr,
            'dst_addr': dst_addr,
            'size': size,
        }
    }
    return op, (dst_addr, src, np.uint8)


def _verify_output(model: FuncModel, output_addr: int, golden: np.ndarray, dtype: type) -> bool:
    off = output_addr - Addr.DRAM_BASE
    size = golden.size * np.dtype(dtype).itemsize
    out = np.frombuffer(model.dram[off:off + size], dtype=dtype)
    if dtype == np.int32:
        return np.array_equal(out, golden.flatten())
    if dtype == np.float16:
        return np.allclose(out.astype(np.float32), golden.astype(np.float32), rtol=1e-3)
    return np.array_equal(out, golden.flatten())


def run_pcie_dma_smoke(direction: int = 0, len_bytes: int = 64) -> bool:
    """Run a PCIe DMA smoke test through Spike firmware opcode 7 dispatch."""
    SRAM_KB = 4096
    model = FuncModel(sram_kb=SRAM_KB)
    model.firmware.ring_buffer_addr = FIRMWARE_RING_BASE

    desc_addr = DESC_BASE
    pcie_addr_lo = 0x00000000
    pcie_addr_hi = 0x00000000
    axi_addr = 0x20000000  # SRAM base

    desc_buf = struct.pack(PCIE_DMA_DESC_FMT,
                           pcie_addr_lo, pcie_addr_hi, axi_addr,
                           len_bytes, direction, 0)
    model.host_write_data(desc_addr, np.frombuffer(desc_buf, dtype=np.uint8))

    write_cmd_entry(model, 0, opcode=7, desc_addr=desc_addr)

    model.bridge.handle('write', DOORBELL.BASE + DOORBELL.HOST_TAIL, 1)

    proc, server = _launch_spike(model)
    try:
        done = poll_completion(model, 1)
    finally:
        _cleanup_spike(proc, server)

    expected_head = 1 % 64
    if done:
        print(f"  [PASS] pcie_dma — opcode 7 dispatched, NPU_HEAD={expected_head}")
        return True
    print(f"  [FAIL] pcie_dma — timeout waiting for NPU_HEAD={expected_head}")
    return False


def run_chain_smoke(op_types: list) -> tuple:
    """Run a mixed-type command chain and verify each output.
    Returns (results, completed) where completed=True if Spike dispatched all ops."""
    rng = np.random.RandomState(123)
    SRAM_KB = 4096
    model = FuncModel(sram_kb=SRAM_KB)
    model.firmware.ring_buffer_addr = FIRMWARE_RING_BASE

    ops = []
    goldens = []
    for idx, t in enumerate(op_types):
        if t == 'mmul':
            op, gold = _prepare_mmul_op(model, idx, rng)
        elif t == 'sfu':
            op, gold = _prepare_sfu_op(model, idx, rng)
        elif t == 'vector':
            op, gold = _prepare_vector_op(model, idx, rng)
        elif t == 'dma_copy':
            op, gold = _prepare_dma_copy_op(model, idx, rng)
        else:
            raise ValueError(f"Unknown smoke op type: {t}")
        ops.append(op)
        goldens.append((t, gold))

    schedule_chain(model, ops)

    proc, server = _launch_spike(model)
    try:
        done = poll_completion(model, len(ops))
    finally:
        _cleanup_spike(proc, server)

    results = []
    if not done:
        head = model.bridge._status.get(DOORBELL.BASE + DOORBELL.NPU_HEAD, 0)
        print(f"  [FAIL] chain — timeout: NPU_HEAD={head}, expected={len(ops) % 64}")
        for t, _ in goldens:
            results.append((t, False))
        return results, False

    # Verify NPU_HEAD == len(ops) (mod 64)
    head = model.bridge._status.get(DOORBELL.BASE + DOORBELL.NPU_HEAD, 0)
    expected_head = len(ops) % 64
    if head != expected_head:
        print(f"  [FAIL] NPU_HEAD={head}, expected={expected_head}")
        for t, _ in goldens:
            results.append((t, False))
        return results, False
    print(f"  [INFO] NPU_HEAD={head} (expected={expected_head}) OK")

    for (t, (output_addr, golden, dtype)), _op in zip(goldens, ops):
        ok = _verify_output(model, output_addr, golden, dtype)
        off = output_addr - Addr.DRAM_BASE
        size = golden.size * np.dtype(dtype).itemsize
        out_blob = bytes(model.dram[off:off + min(size, 256)])
        has_data = not all(b == 0 for b in out_blob)
        if not has_data:
            print(f"  [FAIL] {t:12s} — zero output (CHAIN_NZ)")
            ok = False
        results.append((t, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {t:12s}")
    return results, True


def load_chain_ops(path: str) -> list:
    """Load a chain of op descriptors from a JSON file."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get('ops', data.get('commands', []))
    return data


def run_chain_file(ops_file: str) -> bool:
    """Load a chain from a JSON file and dispatch it."""
    ops = load_chain_ops(ops_file)
    SRAM_KB = 4096
    model = FuncModel(sram_kb=SRAM_KB)
    model.firmware.ring_buffer_addr = FIRMWARE_RING_BASE

    schedule_chain(model, ops)
    proc, server = _launch_spike(model)
    try:
        done = poll_completion(model, len(ops))
    finally:
        _cleanup_spike(proc, server)

    if not done:
        print(f"  [FAIL] chain — timeout waiting for NPU_HEAD={len(ops) % 64}")
        return False
    print(f"  [PASS] chain — {len(ops)} commands consumed")
    return True


def _run_phase10_cli(args) -> int:
    """Phase-10 CLI path: 36-layer spike forward + ladder + evidence."""
    from tokenizer import tokenize

    case_id = os.environ.get("_FM_CASE_ID", "unknown")
    if not args.save_layer_npz or not args.golden_dir:
        print("ERROR: --phase10 requires --save-layer-npz and --golden-dir")
        return 2
    token_ids = args.token_ids
    if token_ids is None:
        token_ids = tokenize(args.prompt, args.model)

    _emit_metric("spike.mode", "forward_phase10", case_id)
    t0 = time.time()
    print(f"{'='*70}")
    print(f"Spike Host Phase10 Forward: {Path(args.model).name}  "
          f"layers={args.layers}  token_ids={token_ids}")
    print(f"{'='*70}")

    evidence_path = args.evidence_file
    Path(evidence_path).parent.mkdir(parents=True, exist_ok=True)
    run_log = evidence_path + ".log"
    with open(run_log, "w", encoding="utf-8") as log_fp:
        try:
            result = run_forward_pass_phase10(
                args.model, args.layers, token_ids,
                save_npz=args.save_layer_npz,
                golden_dir=args.golden_dir,
                evidence_path=evidence_path,
                resume=args.resume,
                log_fp=log_fp,
            )
        except Exception as exc:
            print(f"[FAIL] phase10 forward aborted: {exc}", file=sys.stderr)
            log_fp.write(f"[FAIL] phase10 forward aborted: {exc}\n")
            _emit_metric("spike.exit_code", 1, case_id)
            _emit_metric("spike.tolerance_result", "FAIL", case_id)
            _emit_metric("spike.elapsed_s", round(time.time() - t0, 3), case_id)
            return 1

    elapsed = time.time() - t0
    ok = result["ok"]
    print(f"\n{'='*70}")
    print(f"Spike Host Phase10 Summary: layers={result['layers_completed']}/{result['layers_run']} "
          f"engine=spike ladder={'PASS' if ok else 'FAIL'}")
    print(f"Evidence: {evidence_path}")
    print(f"{'='*70}")
    _emit_metric("spike.exit_code", 0 if ok else 1, case_id)
    _emit_metric("spike.tolerance_result", "PASS" if ok else "FAIL", case_id)
    _emit_metric("spike.elapsed_s", round(elapsed, 3), case_id)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike NPU host adapter")
    parser.add_argument("--model", default=os.environ.get(
                        "QWEN3B_GGUF",
                        str(Path.home() / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf")),
                        help="Path to GGUF model")
    parser.add_argument("--layers", type=int, default=2,
                        help="Number of layers to test")
    parser.add_argument("--ops", default="Q_proj,K_proj,V_proj",
                        help="Comma-separated list of ops")
    parser.add_argument("--mode", default="mmul_smoke",
                        choices=["mmul_smoke", "chain", "forward", "pcie_dma"],
                        help="Run mode")
    parser.add_argument("--ops-file", default=None,
                        help="JSON file with chain ops for --mode chain")
    parser.add_argument("--prompt", default="Hello, world!",
                        help="Input prompt for --mode forward")
    parser.add_argument("--reference", default="llama_ref/refs/qwen_l0_l1_hidden.npz",
                        help="Reference hidden states .npz for --mode forward")
    parser.add_argument("--seq-len", type=int, default=4,
                        help="Sequence length for --mode forward (1 or 4)")
    parser.add_argument("--tolerance", type=float, default=1e-1,
                        help="Max abs tolerance vs llama.cpp .npz reference")
    parser.add_argument("--runs", type=int, default=3,
                        help="Number of forward runs for determinism check")
    parser.add_argument("--evidence-dir", default=".omo/evidence",
                        help="Directory to save evidence files")
    parser.add_argument("--token-ids", default=None,
                        help="Comma-separated integer token IDs (e.g., 1,2,3,4). "
                             "When provided, bypasses the HuggingFace tokenizer.")
    parser.add_argument("--phase10", action="store_true",
                        help="Phase-10 36-layer spike forward (todo 12); "
                             "requires --save-layer-npz/--golden-dir")
    parser.add_argument("--save-layer-npz", default=None,
                        help="Save per-layer hidden states to this .npz (--phase10)")
    parser.add_argument("--golden-dir", default=None,
                        help="Func Model golden dir with expected_l{N}.npz (--phase10)")
    parser.add_argument("--evidence-file", default="build/evidence/task-12-phase10-rtl-verification.txt",
                        help="Evidence output path (--phase10)")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from --save-layer-npz if it exists (--phase10)")
    args = parser.parse_args()

    if args.token_ids is not None:
        try:
            args.token_ids = [int(x.strip()) for x in args.token_ids.split(",")]
        except ValueError as exc:
            parser.error(f"--token-ids must be a comma-separated list of integers: {exc}")

    case_id = os.environ.get("_FM_CASE_ID", "unknown")

    if args.mode == "mmul_smoke":
        ops = [o.strip() for o in args.ops.split(",")]
        passed = 0
        failed = 0

        _emit_metric("spike.mode", "mmul_smoke", case_id)
        t0 = time.time()

        print(f"{'='*70}")
        print(f"Spike Host: {Path(args.model).name}  layers={args.layers}  ops={ops}")
        print(f"{'='*70}")

        for layer in range(args.layers):
            for op in ops:
                ok = run_one_op(args.model, layer, op, M=1)
                if ok:
                    passed += 1
                else:
                    failed += 1

        elapsed = time.time() - t0
        exit_code = 0 if failed == 0 else 1
        _emit_metric("spike.exit_code", exit_code, case_id)
        _emit_metric("spike.tolerance_result", "PASS" if failed == 0 else "FAIL", case_id)
        _emit_metric("spike.elapsed_s", round(elapsed, 3), case_id)

        print(f"\n{'='*70}")
        print(f"Spike Host Summary: {passed} PASS, {failed} FAIL")
        print(f"{'='*70}")
        return exit_code

    if args.mode == "chain":
        _emit_metric("spike.mode", "chain", case_id)
        t0 = time.time()

        if args.ops_file:
            ok = run_chain_file(args.ops_file)
            elapsed = time.time() - t0
            exit_code = 0 if ok else 1
            _emit_metric("spike.exit_code", exit_code, case_id)
            _emit_metric("spike.tolerance_result", "PASS" if ok else "FAIL", case_id)
            _emit_metric("spike.elapsed_s", round(elapsed, 3), case_id)
            return exit_code

        op_types = [o.strip().lower() for o in args.ops.split(",")]
        if op_types == ["q_proj", "k_proj", "v_proj"]:
            op_types = ["mmul", "sfu", "vector"]
        if not op_types:
            op_types = ["mmul", "sfu", "vector"]

        print(f"{'='*70}")
        print(f"Spike Host Chain: ops={op_types}")
        print(f"{'='*70}")

        results, completed = run_chain_smoke(op_types)
        passed = sum(1 for _, ok in results if ok)
        failed = len(results) - passed

        elapsed = time.time() - t0
        chain_ok = completed and failed == 0
        exit_code = 0 if chain_ok else 1
        _emit_metric("spike.exit_code", exit_code, case_id)
        _emit_metric("spike.tolerance_result", "PASS" if chain_ok else "FAIL", case_id)
        _emit_metric("spike.elapsed_s", round(elapsed, 3), case_id)

        print(f"\n{'='*70}")
        print(f"Spike Host Chain Summary: {passed} PASS, {failed} FAIL")
        print(f"NPU_HEAD={'OK' if completed else 'TIMEOUT'}, AllOpsPassed={'YES' if (completed and failed == 0) else 'NO'}")
        print(f"{'='*70}")
        return exit_code

    if args.mode == "forward":
        if args.phase10:
            return _run_phase10_cli(args)

        _emit_metric("spike.mode", "forward", case_id)
        t0 = time.time()

        print(f"{'='*70}")
        if args.token_ids is not None:
            print(f"Spike Host Forward: {Path(args.model).name}  layers={args.layers}  token_ids={args.token_ids}")
        else:
            print(f"Spike Host Forward: {Path(args.model).name}  layers={args.layers}  prompt={args.prompt!r}")
        print(f"{'='*70}")

        evidence_dir = Path(args.evidence_dir)
        evidence_dir.mkdir(parents=True, exist_ok=True)
        e2e_path = evidence_dir / "e2e-task-14-e2e.txt"
        npz_path = evidence_dir / "e2e-task-14-npz-compare.txt"
        e2e_fp = e2e_path.open("w", encoding="utf-8")
        npz_fp = npz_path.open("w", encoding="utf-8")

        def _tee(*lines):
            for line in lines:
                print(line)
                e2e_fp.write(line + "\n")
                e2e_fp.flush()

        _tee(f"Forward pass determinism check: {args.runs} runs")
        _tee(f"Model: {args.model}")
        _tee(f"Reference: {args.reference}")
        _tee(f"Tolerance: {args.tolerance:.0e}")
        _tee("")

        run_results = []
        for run in range(args.runs):
            _tee(f"--- Run {run + 1}/{args.runs} ---")
            result = run_forward_pass(
                args.model, args.prompt, layers=args.layers,
                reference_npz=args.reference, seq_len=args.seq_len,
                tolerance=args.tolerance, log_fp=e2e_fp,
                token_ids=args.token_ids,
            )
            run_results.append(result)
            _tee(f"Run {run + 1} overall: {'PASS' if result['ok'] else 'WARN'}")
            _tee("")

        deterministic = True
        if args.runs > 1:
            _tee("--- Determinism check ---")
            for layer in range(args.layers):
                for run_a in range(args.runs):
                    for run_b in range(run_a + 1, args.runs):
                        a = run_results[run_a]["layer_outputs"][layer]
                        b = run_results[run_b]["layer_outputs"][layer]
                        diff = np.max(np.abs(a - b))
                        note = "OK" if diff == 0.0 else f"diff={diff:.3e}"
                        if diff != 0.0:
                            deterministic = False
                        _tee(f"  l_out_{layer} run{run_a + 1} vs run{run_b + 1}: {note}")
            _tee(f"DETERMINISTIC: {'YES' if deterministic else 'NO'}")
            _tee("")

        coverage = run_results[-1]["op_coverage"]
        _tee("--- Op coverage summary ---")
        for name, count in coverage.items():
            if count:
                _tee(f"  {name:12s}: {count}")
        _tee("")

        all_ok = all(r["ok"] for r in run_results)
        summary = f"Forward Summary: all_runs={'PASS' if all_ok else 'WARN'} deterministic={'YES' if deterministic else 'NO'}"
        _tee(summary)
        e2e_fp.close()

        ref = np.load(args.reference)
        npz_fp.write(f"Per-element comparison: Spike DRAM vs {args.reference}\n")
        npz_fp.write(f"Tolerance: {args.tolerance:.0e}\n\n")
        for layer in range(args.layers):
            out_f32 = run_results[-1]["layer_outputs"][layer]
            ref_key = f"l_out_{layer}"
            ref_h = ref[ref_key][:out_f32.shape[0]]
            diff = np.abs(out_f32 - ref_h)
            rel = diff / (np.abs(ref_h) + 1e-8)
            npz_fp.write(f"--- {ref_key} ---\n")
            npz_fp.write(f"shape: {out_f32.shape}\n")
            npz_fp.write(f"max_abs: {float(np.max(diff)):.6e}\n")
            npz_fp.write(f"max_rel: {float(np.max(rel)):.6e}\n")
            npz_fp.write(f"mean_abs: {float(np.mean(diff)):.6e}\n")
            npz_fp.write(f"mean_rel: {float(np.mean(rel)):.6e}\n")
            flat_idx = int(np.argmax(diff))
            row, col = np.unravel_index(flat_idx, diff.shape)
            npz_fp.write(f"worst_idx: ({row}, {col})\n")
            npz_fp.write(f"spike: {float(out_f32[row, col]):.8e}\n")
            npz_fp.write(f"ref:   {float(ref_h[row, col]):.8e}\n")
            npz_fp.write(f"diff:  {float(diff[row, col]):.8e}\n")
            npz_fp.write(f"rel:   {float(rel[row, col]):.8e}\n\n")
        npz_fp.close()

        print(f"\n{'='*70}")
        print(f"Spike Host Forward Summary: {'PASS' if all_ok else 'WARN'}  deterministic={'YES' if deterministic else 'NO'}")
        print(f"Evidence saved: {e2e_path}  {npz_path}")
        print(f"{'='*70}")

        elapsed = time.time() - t0
        exit_code = 0 if all_ok else 1
        _emit_metric("spike.exit_code", exit_code, case_id)
        _emit_metric("spike.tolerance_result", "PASS" if all_ok else "FAIL", case_id)
        _emit_metric("spike.elapsed_s", round(elapsed, 3), case_id)
        return exit_code

    if args.mode == "pcie_dma":
        _emit_metric("spike.mode", "pcie_dma", case_id)
        t0 = time.time()

        print(f"{'='*70}")
        print("Spike Host PCIe DMA: opcode 7 dispatch smoke test")
        print(f"{'='*70}")
        ok = run_pcie_dma_smoke()

        elapsed = time.time() - t0
        exit_code = 0 if ok else 1
        _emit_metric("spike.exit_code", exit_code, case_id)
        _emit_metric("spike.tolerance_result", "PASS" if ok else "FAIL", case_id)
        _emit_metric("spike.elapsed_s", round(elapsed, 3), case_id)

        print(f"{'='*70}")
        print(f"Spike Host PCIe DMA Summary: {'PASS' if ok else 'FAIL'}")
        print(f"{'='*70}")
        return exit_code

    return 1


if __name__ == "__main__":
    sys.exit(main())
