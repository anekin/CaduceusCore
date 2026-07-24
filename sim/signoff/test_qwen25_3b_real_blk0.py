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


def _emit_metric(capsys, key: str, value, *, case_id: str = "") -> None:
    """Emit a SIGNOFF_METRIC line. Leading newline keeps the line at column 0."""
    effective_case = case_id or CASE_ID
    line = json.dumps({"case": effective_case, "key": key, "value": value})
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


def test_qwen25_3b_selective_loading_and_reference_inputs(capsys) -> None:
    """Selective GGUF loading: layer-0 weights only + token-embd row 9707.

    Verifies:
      - ``load_selected_weights_from_gguf`` loads only the requested tensors.
      - ``load_tensor_row_from_gguf`` extracts the correct embedding row.
      - ``forward_with_intermediates`` exposes x_norm, ffn_norm, attn_concat.
      - float32 forward hash is unchanged after intermediate extension.
      - ``FuncModel(dram_mb=256)`` initialises without error.
    """
    from q4_dequant import (
        load_selected_weights_from_gguf,
        load_tensor_row_from_gguf,
    )
    from qwen25_forward import Qwen25Layer

    gguf_path = _gguf_path()
    assert gguf_path.is_file(), f"GGUF not found: {gguf_path}"

    model_sha = _compute_sha256(gguf_path)

    # ── 1. Selective loading (layer-0 only, no 36-layer dequant) ───
    layer0_names = {
        "blk.0.attn_norm.weight",
        "blk.0.attn_q.weight", "blk.0.attn_q.bias",
        "blk.0.attn_k.weight", "blk.0.attn_k.bias",
        "blk.0.attn_v.weight", "blk.0.attn_v.bias",
        "blk.0.attn_output.weight",
        "blk.0.ffn_norm.weight",
        "blk.0.ffn_gate.weight",
        "blk.0.ffn_up.weight",
        "blk.0.ffn_down.weight",
    }
    weights = load_selected_weights_from_gguf(str(gguf_path), layer0_names)

    for name in layer0_names:
        assert name in weights, f"Missing: {name}"

    # Assert NO layer-1 tensors were accidentally loaded
    for key in weights:
        assert key.startswith("blk.0."), f"Leaked non-layer-0 tensor: {key}"

    # ── 2. Token embedding row ──────────────────────────────────────
    tok_emb_row = load_tensor_row_from_gguf(str(gguf_path), "token_embd.weight", 9707)
    assert tok_emb_row.shape == (2048,), f"Bad shape: {tok_emb_row.shape}"
    assert tok_emb_row.dtype == np.float32

    # Sanity: embedding for "Hello" should have non-trivial values
    assert abs(tok_emb_row.mean()) > 1e-6, "Embedding row is near-zero"

    # ── 3. Read hyperparameters ────────────────────────────────────
    reader = GGUFReader(str(gguf_path))
    hidden_size = int(_get_field_value(reader, "qwen2.embedding_length"))
    intermediate_size = int(_get_field_value(reader, "qwen2.feed_forward_length"))
    num_heads = int(_get_field_value(reader, "qwen2.attention.head_count"))
    num_kv_heads = int(_get_field_value(reader, "qwen2.attention.head_count_kv"))
    head_dim = hidden_size // num_heads
    rope_theta = float(_get_field_value(reader, "qwen2.rope.freq_base") or 1000000.0)
    rms_eps = float(_get_field_value(reader, "qwen2.attention.layer_norm_rms_epsilon") or 1e-6)

    layer = Qwen25Layer(
        weights=weights,
        layer_idx=0,
        hidden_size=hidden_size,
        intermediate_size=intermediate_size,
        num_heads=num_heads,
        num_kv_heads=num_kv_heads,
        head_dim=head_dim,
        rope_theta=rope_theta,
        rms_eps=rms_eps,
    )

    hidden = tok_emb_row.copy()

    # ── 4. Forward hash unchanged ───────────────────────────────────
    fwd_out = layer.forward(hidden, position=0)
    inter = layer.forward_with_intermediates(hidden, position=0)

    np.testing.assert_array_almost_equal(
        fwd_out, inter["final"], decimal=6,
        err_msg="forward_with_intermediates final differs from forward()",
    )
    fwd_hash = hashlib.sha256(fwd_out.tobytes()).hexdigest()[:16]
    inter_hash = hashlib.sha256(inter["final"].tobytes()).hexdigest()[:16]
    assert fwd_hash == inter_hash, (
        f"forward() vs forward_with_intermediates() hash mismatch: "
        f"{fwd_hash} != {inter_hash}"
    )

    # ── 5. Projection inputs exposed ────────────────────────────────
    assert "x_norm" in inter, "x_norm not in intermediates"
    assert "ffn_norm" in inter, "ffn_norm not in intermediates"
    assert "attn_concat" in inter, "attn_concat not in intermediates"
    assert inter["x_norm"].shape == (2048,)
    assert inter["ffn_norm"].shape == (2048,)
    assert inter["attn_concat"].shape == (2048,)

    # ── 6. FuncModel(dram_mb=256) integration ──────────────────────
    from func_model import FuncModel
    model = FuncModel(dram_mb=256)
    assert model is not None

    # ── 7. SIGNOFF_METRIC emission ──────────────────────────────────
    t4c1_case = "task-4c1-qwen25-3b-selective-load-and-reference-inputs"
    _emit_metric(capsys, "model.sha256", model_sha, case_id=t4c1_case)
    for name in sorted(weights.keys()):
        _emit_metric(capsys, f"loaded_tensor.{name}", True, case_id=t4c1_case)
    _emit_metric(capsys, "tests.collected", 1, case_id=t4c1_case)
    _emit_metric(capsys, "tests.passed", 1, case_id=t4c1_case)
    _emit_metric(capsys, "evidence.verdict", "pass", case_id=t4c1_case)
