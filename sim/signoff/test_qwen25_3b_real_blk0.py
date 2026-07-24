"""Preflight assertions for the real Qwen2.5-3B Q4_K_M GGUF checkpoint.

Validates file provenance (exact SHA-256, size), metadata (block count,
dimensions, attention heads), and layer-0 tensor shapes without dequantizing
all 36 layers.

This is the real-GGUF half of Wave 1 T0B. The synthetic half is in
test_qwen_blk0_synthetic_stress.py.

Required GGUF file: $QWEN3B_GGUF (default /home/zhengs/models/...)
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np

import gguf
from gguf import GGUFReader

CASE_ID = "task-0b-qwen3b-synthetic-and-real-preflight"

# Pinned provenance for Qwen2.5-3B-Instruct Q4_K_M
_EXPECTED_SHA256 = "626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d"
_EXPECTED_SIZE = 2104932768
_EXPECTED_BLOCK_COUNT = 36
_EXPECTED_HIDDEN = 2048
_EXPECTED_INTERMEDIATE = 11008
_EXPECTED_NUM_HEADS = 16
_EXPECTED_KV_HEADS = 2
_EXPECTED_HEAD_DIM = 128


def _gguf_path() -> Path:
    env = os.environ.get("QWEN3B_GGUF", "")
    if env:
        return Path(env)
    return Path("/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf")


def _emit_metric(capsys, key: str, value) -> None:
    """Emit a SIGNOFF_METRIC line. Leading newline keeps the line at column 0."""
    line = json.dumps({"case": CASE_ID, "key": key, "value": value})
    with capsys.disabled():
        print(f"\nSIGNOFF_METRIC {line}")


def _compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8 * 1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def _get_field_value(reader: GGUFReader, field_name: str) -> int:
    field = reader.fields.get(field_name)
    if field is None:
        raise KeyError(f"GGUF field '{field_name}' not found")
    val = field.parts[field.data[-1]]
    if isinstance(val, np.ndarray):
        return int(val[0]) if val.size == 1 else int(val)
    return int(val)


def test_qwen25_3b_real_model_provenance_and_shapes(capsys) -> None:
    """Verify GGUF provenance, metadata, and layer-0 tensor shapes.

    Does NOT dequantize all 36 layers — reads only header metadata.
    """
    gguf_path = _gguf_path()
    assert gguf_path.is_file(), f"GGUF not found: {gguf_path}"

    actual_size = gguf_path.stat().st_size
    assert actual_size == _EXPECTED_SIZE, (
        f"Expected size {_EXPECTED_SIZE}, got {actual_size}"
    )

    actual_sha = _compute_sha256(gguf_path)
    assert actual_sha == _EXPECTED_SHA256, (
        f"SHA-256 mismatch: expected {_EXPECTED_SHA256[:16]}..., "
        f"got {actual_sha[:16]}..."
    )

    reader = GGUFReader(str(gguf_path))

    block_count = _get_field_value(reader, "qwen2.block_count")
    assert block_count == _EXPECTED_BLOCK_COUNT

    hidden = _get_field_value(reader, "qwen2.embedding_length")
    assert hidden == _EXPECTED_HIDDEN

    intermediate = _get_field_value(reader, "qwen2.feed_forward_length")
    assert intermediate == _EXPECTED_INTERMEDIATE

    num_heads = _get_field_value(reader, "qwen2.attention.head_count")
    assert num_heads == _EXPECTED_NUM_HEADS

    num_kv_heads = _get_field_value(reader, "qwen2.attention.head_count_kv")
    assert num_kv_heads == _EXPECTED_KV_HEADS

    head_dim = hidden // num_heads
    assert head_dim == _EXPECTED_HEAD_DIM

    # Layer-0 tensor shapes (metadata only, no dequantization)
    expected_shapes = {
        "blk.0.attn_q.weight": (2048, 2048),
        "blk.0.attn_k.weight": (2048, 256),
        "blk.0.attn_v.weight": (2048, 256),
        "blk.0.attn_output.weight": (2048, 2048),
        "blk.0.ffn_gate.weight": (2048, 11008),
        "blk.0.ffn_up.weight": (2048, 11008),
        "blk.0.ffn_down.weight": (11008, 2048),
    }
    found = {}
    for tensor in reader.tensors:
        if tensor.name in expected_shapes:
            found[tensor.name] = tuple(int(d) for d in tensor.shape)
    for name, expected_shape in expected_shapes.items():
        assert name in found, f"Tensor '{name}' not found"
        assert found[name] == expected_shape, (
            f"Tensor '{name}': expected {expected_shape}, got {found[name]}"
        )

    # Emit required SIGNOFF_METRIC lines
    _emit_metric(capsys, "model.sha256", actual_sha)
    _emit_metric(capsys, "model.hidden", hidden)
    _emit_metric(capsys, "model.intermediate", intermediate)
    _emit_metric(capsys, "model.num_heads", num_heads)
    _emit_metric(capsys, "model.num_kv_heads", num_kv_heads)
    _emit_metric(capsys, "model.head_dim", head_dim)
