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

from q4_dequant import load_weights_from_gguf
from sim.func_model import FuncModel
from sim.golden_executor import GoldenMXU
from sim.quantize import quantize_int4_per_block
from sim.regmap import Addr, DOORBELL, MXU
from sim.spike_mmio_server import DEFAULT_SOCK_PATH, serve


# ── Paths ──────────────────────────────────────────────────────────

PROJECT = _HERE.parent
SPIKE_BIN = Path("/home/prj/zhengs/caduceuscore/spike_src/build/spike")
FIRMWARE_ELF = PROJECT / "firmware" / "build" / "npu_firmware.elf"
PLUGIN_SO = PROJECT / "spike_src" / "plugins" / "npu_mmio_plugin.so"

FIRMWARE_RING_BASE = 0x80100000  # hard-coded in C firmware; shadows part of low DRAM

# C firmware mmul_desc_t uses a 12-field packed layout.
MMUL_DESC_FMT = "<12I"
MMUL_DESC_SIZE = struct.calcsize(MMUL_DESC_FMT)

SFU_DESC_FMT = "<12I"
SFU_DESC_SIZE = struct.calcsize(SFU_DESC_FMT)

VECTOR_DESC_FMT = "<8I"
VECTOR_DESC_SIZE = struct.calcsize(VECTOR_DESC_FMT)

DMA_COPY_DESC_FMT = "<8I"
DMA_COPY_DESC_SIZE = struct.calcsize(DMA_COPY_DESC_FMT)

CMD_ENTRY_FMT = "<8I"
CMD_ENTRY_SIZE = struct.calcsize(CMD_ENTRY_FMT)

DESC_BASE = 0x80001000
DESC_STRIDE = 64


# ── Helpers ────────────────────────────────────────────────────────

def write_mmul_descriptor(model: FuncModel, desc_addr: int,
                          input_addr: int, weight_addr: int, output_addr: int,
                          input_sram: int, weight_sram: int, output_sram: int,
                          input_size: int, weight_size: int, output_size: int,
                          M: int, K: int, N: int):
    """Write a descriptor in the format expected by firmware npu_firmware.c."""
    buf = struct.pack(MMUL_DESC_FMT,
                      input_addr, weight_addr, output_addr,
                      input_sram, weight_sram, output_sram,
                      input_size, weight_size, output_size,
                      M, K, N)
    model.host_write_data(desc_addr, np.frombuffer(buf, dtype=np.uint8))


def write_sfu_descriptor(model: FuncModel, desc_addr: int,
                         op: int, input_addr: int, output_addr: int,
                         input_sram: int, output_sram: int, size: int,
                         dim: int = 0, pos: int = 0):
    """Write an SFU descriptor in the format expected by firmware npu_firmware.c."""
    buf = struct.pack(SFU_DESC_FMT,
                      op, input_addr, output_addr,
                      input_sram, output_sram, size, dim, pos,
                      0, 0, 0, 0)
    model.host_write_data(desc_addr, np.frombuffer(buf, dtype=np.uint8))


def write_vector_descriptor(model: FuncModel, desc_addr: int,
                            op: int, a_addr: int, b_addr: int, o_addr: int,
                            dim: int):
    """Write a Vector descriptor in the format expected by firmware npu_firmware.c."""
    buf = struct.pack(VECTOR_DESC_FMT,
                      op, a_addr, b_addr, o_addr, dim,
                      0, 0, 0)
    model.host_write_data(desc_addr, np.frombuffer(buf, dtype=np.uint8))


def write_dma_copy_descriptor(model: FuncModel, desc_addr: int,
                              src_addr: int, dst_addr: int, size: int):
    """Write a DMA_COPY descriptor in the format expected by firmware npu_firmware.c."""
    buf = struct.pack(DMA_COPY_DESC_FMT,
                      src_addr, dst_addr, size,
                      0, 0, 0, 0, 0)
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
            opcode = 0
        elif op_type == 'sfu':
            write_sfu_descriptor(model, desc_addr, **desc)
            opcode = 1
        elif op_type == 'vector':
            write_vector_descriptor(model, desc_addr, **desc)
            opcode = 2
        elif op_type == 'dma_copy':
            write_dma_copy_descriptor(model, desc_addr, **desc)
            opcode = 3
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

    # Quantize to row-major INT4 + per-block scales (C firmware reads row-major)
    wgt_packed, wgt_scales, _ = quantize_int4_per_block(W_f32, 128)
    wgt_bytes = wgt_packed.tobytes()
    scale_bytes = wgt_scales.tobytes()

    # The C firmware copies one contiguous weight blob via DMA.  Pack weights
    # and scales back-to-back so the bridge can find scales at SCALE_ADDR.
    combined_weight_blob = wgt_bytes + scale_bytes

    # Activation
    rng = np.random.RandomState(42)
    act = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)

    # Golden reference (row-major weights + scales)
    mxu = GoldenMXU()
    golden = mxu.matmul_int4_per_block(act, wgt_packed, wgt_scales, M, K, N, group_size=128)

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
                          input_sram=input_sram, weight_sram=weight_sram, output_sram=output_sram,
                          input_size=act.nbytes, weight_size=len(combined_weight_blob),
                          output_size=M * N * 4, M=M, K=K, N=N)
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
    return ok


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

FP_DRAM_BASE = 0x81000000
FP_DRAM_SIZE = 0x07000000


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


def _forward_layer(hidden: np.ndarray, weights: dict, layer: int) -> np.ndarray:
    normed = _forward_rmsnorm(hidden, weights[f'blk.{layer}.attn_norm.weight'])
    q = normed @ weights[f'blk.{layer}.attn_q.weight'].T + weights.get(f'blk.{layer}.attn_q.bias', 0)
    k = normed @ weights[f'blk.{layer}.attn_k.weight'].T + weights.get(f'blk.{layer}.attn_k.bias', 0)
    v = normed @ weights[f'blk.{layer}.attn_v.weight'].T + weights.get(f'blk.{layer}.attn_v.bias', 0)
    attn_out = _forward_attention(q, k, v)
    o = attn_out @ weights[f'blk.{layer}.attn_output.weight'].T
    residual = hidden + o
    ffn_input = _forward_rmsnorm(residual, weights[f'blk.{layer}.ffn_norm.weight'])
    gate = ffn_input @ weights[f'blk.{layer}.ffn_gate.weight'].T
    up = ffn_input @ weights[f'blk.{layer}.ffn_up.weight'].T
    ffn_hidden = _forward_silu(gate) * up
    ffn_out = ffn_hidden @ weights[f'blk.{layer}.ffn_down.weight'].T
    return residual + ffn_out


def _allocate_dram(size: int, base: int = FP_DRAM_BASE, align: int = 64) -> int:
    if not hasattr(_allocate_dram, 'cursor'):
        _allocate_dram.cursor = base
    addr = (_allocate_dram.cursor + align - 1) & ~(align - 1)
    _allocate_dram.cursor = addr + size
    if _allocate_dram.cursor > base + FP_DRAM_SIZE:
        raise MemoryError('Forward pass DRAM allocation exceeded')
    return addr


def _reset_dram_allocator(base: int = FP_DRAM_BASE):
    _allocate_dram.cursor = base


def _write_tensor(model: FuncModel, addr: int, data: np.ndarray):
    model.host_write_data(addr, data)


def _read_tensor(model: FuncModel, addr: int, shape: tuple, dtype: type) -> np.ndarray:
    off = addr - Addr.DRAM_BASE
    n = int(np.prod(shape)) * np.dtype(dtype).itemsize
    return np.frombuffer(model.dram[off:off + n], dtype=dtype).reshape(shape)


def _quantize_weight_for_mmul(W_f32: np.ndarray, group_size: int = 128
                              ) -> tuple:
    packed, scales, _ = quantize_int4_per_block(W_f32, group_size)
    return packed, scales, packed.nbytes, scales.nbytes


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
    _write_tensor(model, input_addr, input_data)
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
            'output_addr': output_addr,
            'input_sram': input_sram,
            'weight_sram': weight_sram,
            'output_sram': output_sram,
            'input_size': input_data.nbytes,
            'weight_size': len(packed.tobytes()),
            'output_size': M * N * 4,
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
                     log_fp=None) -> dict:
    """Run a complete 2-layer Qwen2.5-1.5B forward pass through Spike firmware."""
    from sim.tokenizer import tokenize, embedding_lookup

    weights = load_weights_from_gguf(gguf_path)
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


def _launch_spike(model: FuncModel):
    """Start bridge server, serialize DRAM, and launch Spike."""
    ready_event = threading.Event()
    server = serve(model.bridge, sock_path=DEFAULT_SOCK_PATH, ready_event=ready_event)
    ready_event.wait(timeout=5.0)

    ddr_path = PROJECT / "ddr.bin"
    ddr_path.write_bytes(model.dram)

    # Strip the firmware's pre-loaded DRAM section so the host kernel image wins.
    stripped_elf = PROJECT / "firmware" / "build" / "npu_firmware.host.elf"
    subprocess.run(
        ["riscv64-unknown-elf-objcopy", "--remove-section", ".data_dram",
         str(FIRMWARE_ELF), str(stripped_elf)],
        check=True,
        stderr=subprocess.DEVNULL,
    )

    env = os.environ.copy()
    env["PATH"] = "/home/prj/zhengs/caduceuscore/dtc_src:" + env.get("PATH", "")

    cmd = [
        str(SPIKE_BIN),
        "--isa=RV32IM",
        "-m0x80000000:0x10000000",
        f"--kernel={ddr_path}",
        f"--extlib={PLUGIN_SO}",
        "--device=npu,0x20000000",
        str(stripped_elf),
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
    from sim.golden_executor import GoldenSFU

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
    from sim.golden_executor import GoldenVector

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


def run_chain_smoke(op_types: list) -> list:
    """Run a mixed-type command chain and verify each output."""
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
        for t, _ in goldens:
            print(f"  [FAIL] {t:12s} — timeout waiting for NPU_HEAD={len(ops) % 64}")
            results.append((t, False))
        return results

    for (t, (output_addr, golden, dtype)), _op in zip(goldens, ops):
        ok = _verify_output(model, output_addr, golden, dtype)
        results.append((t, ok))
        print(f"  [{'PASS' if ok else 'FAIL'}] {t:12s}")
    return results


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Spike NPU host adapter")
    parser.add_argument("--model", default=str(Path.home() / "models" /
                        "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
                        help="Path to GGUF model")
    parser.add_argument("--layers", type=int, default=2,
                        help="Number of layers to test")
    parser.add_argument("--ops", default="Q_proj,K_proj,V_proj",
                        help="Comma-separated list of ops")
    parser.add_argument("--mode", default="mmul_smoke",
                        choices=["mmul_smoke", "chain", "forward"],
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
    args = parser.parse_args()

    if args.mode == "mmul_smoke":
        ops = [o.strip() for o in args.ops.split(",")]
        passed = 0
        failed = 0

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

        print(f"\n{'='*70}")
        print(f"Spike Host Summary: {passed} PASS, {failed} FAIL")
        print(f"{'='*70}")
        return 0 if failed == 0 else 1

    if args.mode == "chain":
        if args.ops_file:
            ok = run_chain_file(args.ops_file)
            return 0 if ok else 1

        op_types = [o.strip().lower() for o in args.ops.split(",")]
        if op_types == ["q_proj", "k_proj", "v_proj"]:
            op_types = ["mmul", "sfu", "vector"]
        if not op_types:
            op_types = ["mmul", "sfu", "vector"]

        print(f"{'='*70}")
        print(f"Spike Host Chain: ops={op_types}")
        print(f"{'='*70}")

        results = run_chain_smoke(op_types)
        passed = sum(1 for _, ok in results if ok)
        failed = len(results) - passed

        print(f"\n{'='*70}")
        print(f"Spike Host Chain Summary: {passed} PASS, {failed} FAIL")
        print(f"{'='*70}")
        return 0 if failed == 0 else 1

    if args.mode == "forward":
        print(f"{'='*70}")
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
        return 0 if all_ok else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())
