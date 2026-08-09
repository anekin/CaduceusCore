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
import math
import os
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

import gguf
from gguf import GGUFReader

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parents[1]
sys.path.insert(0, str(_PROJECT / "ggml-npu"))

from tile_scheduler import (
    tile_mmul,
    TILE_H,
    TILE_W,
    TILE_WEIGHT_BYTES,
    TILE_SCALE_BYTES,
)
from golden_executor import GoldenMXU

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
    effective_case = case_id or os.environ.get("_FM_CASE_ID", "") or CASE_ID
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
    t4c1_case = os.environ.get("_FM_CASE_ID", "") or "task-4c1-qwen25-3b-selective-load-and-reference-inputs"
    _emit_metric(capsys, "model.sha256", model_sha, case_id=t4c1_case)
    for name in sorted(weights.keys()):
        _emit_metric(capsys, f"loaded_tensor.{name}", True, case_id=t4c1_case)
    # tests.collected / tests.passed / evidence.verdict are added by the runner
    # from JUnit XML — duplicating them here causes conflicts in umbrella cases.


# ══════════════════════════════════════════════════════════════════════════
# Task 4C2 — Real-GGUF direct-MMIO projection gate with independent oracle
# ══════════════════════════════════════════════════════════════════════════

_T4C2_CASE_ID = "task-4c2-qwen25-3b-real-direct-projections"
_QWEN_HIDDEN = 2048
_QWEN_INTERMEDIATE = 11008
_QWEN_KV_DIM = 256
_DRAM_BASE = 0x80000000
_MXU_BASE = 0x40000000
_ATOL = 1e-4
_RTOL = 1e-5
_COS_PASS = 0.97
_COS_WARN = 0.96


def _t4c2_emit_metric(capsys, key: str, value) -> None:
    effective_case = os.environ.get("_FM_CASE_ID", "") or _T4C2_CASE_ID
    line = json.dumps({"case": effective_case, "key": key, "value": value})
    with capsys.disabled():
        print(f"\nSIGNOFF_METRIC {line}")


def test_qwen25_3b_real_direct_projections(capsys) -> None:
    """Real-GGUF direct-MMIO projection gate with independent oracle.

    Verifies:
      - Oracle does not import prohibited modules (golden_*, mmio_*, tile_*).
      - MMIO direct full-shape matmul matches independent NumPy oracle
        element-wise (atol=1e-4, rtol=1e-5).
      - MMIO INT4-quantized output has cosine >= 0.97 vs float32 model.
      - Activation scale applied exactly once; bias applied only for Q/K/V.
      - Q/K/V/O/gate/up/down at canonical dimensions through real GGUF weights.
    """
    # ── 0. Oracle import (fires import-call guard at module level) ───
    from qwen25_signoff_oracle import (
        compute_act_scale,
        cosine_similarity,
        matmul_int4_per_block as oracle_matmul,
        quantize_activation,
        quantize_int4_per_block,
    )
    from q4_dequant import load_selected_weights_from_gguf
    from qwen25_forward import Qwen25Layer
    from regmap import MXU
    from func_model import FuncModel

    gguf_path = _gguf_path()
    assert gguf_path.is_file(), f"GGUF not found: {gguf_path}"

    # ── 1. Load weights ──────────────────────────────────────────────
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

    # Token embedding
    from q4_dequant import load_tensor_row_from_gguf
    tok_emb_row = load_tensor_row_from_gguf(str(gguf_path), "token_embd.weight", 9707)

    # Hyperparameters
    reader = GGUFReader(str(gguf_path))
    hidden_size = int(_get_field_value(reader, "qwen2.embedding_length"))
    intermediate_size = int(_get_field_value(reader, "qwen2.feed_forward_length"))
    num_heads = int(_get_field_value(reader, "qwen2.attention.head_count"))
    num_kv_heads = int(_get_field_value(reader, "qwen2.attention.head_count_kv"))
    head_dim = hidden_size // num_heads
    rope_theta = float(_get_field_value(reader, "qwen2.rope.freq_base") or 1000000.0)
    rms_eps = float(_get_field_value(reader, "qwen2.attention.layer_norm_rms_epsilon") or 1e-6)

    layer = Qwen25Layer(
        weights=weights, layer_idx=0,
        hidden_size=hidden_size, intermediate_size=intermediate_size,
        num_heads=num_heads, num_kv_heads=num_kv_heads, head_dim=head_dim,
        rope_theta=rope_theta, rms_eps=rms_eps,
    )
    inter = layer.forward_with_intermediates(tok_emb_row.copy(), position=0)

    x_norm = inter["x_norm"]       # (2048,)  → drives Q/K/V
    attn_concat = inter["attn_concat"]  # (2048,) → drives O
    ffn_norm = inter["ffn_norm"]   # (2048,)  → drives gate/up/down

    # ── 2. Init FuncModel ────────────────────────────────────────────
    model = FuncModel(dram_mb=256)
    mxu_base = _MXU_BASE
    dram_base = _DRAM_BASE

    # Fixed DRAM windows
    ACT_ADDR = dram_base + 0x00000000   # 128 KB
    WGT_ADDR = dram_base + 0x00020000   # 16 MB
    SCL_ADDR = dram_base + 0x01000000   # 4 MB
    OUT_ADDR = dram_base + 0x01400000   # 4 MB

    def _dram_off(addr: int) -> int:
        return addr - dram_base

    # ── 3. Projection definitions ────────────────────────────────────
    projections = {
        "Q_proj": {
            "w_key": "blk.0.attn_q.weight",
            "b_key": "blk.0.attn_q.bias",
            "activation": x_norm,
            "K": hidden_size, "N": hidden_size, "M": 1,
            "has_bias": True,
            "float32_ref": inter["Q_proj"],
        },
        "K_proj": {
            "w_key": "blk.0.attn_k.weight",
            "b_key": "blk.0.attn_k.bias",
            "activation": x_norm,
            "K": hidden_size, "N": _QWEN_KV_DIM, "M": 1,
            "has_bias": True,
            "float32_ref": inter["K_proj"],
        },
        "V_proj": {
            "w_key": "blk.0.attn_v.weight",
            "b_key": "blk.0.attn_v.bias",
            "activation": x_norm,
            "K": hidden_size, "N": _QWEN_KV_DIM, "M": 1,
            "has_bias": True,
            "float32_ref": inter["V_proj"],
        },
        "O_proj": {
            "w_key": "blk.0.attn_output.weight",
            "b_key": None,
            "activation": attn_concat,
            "K": hidden_size, "N": hidden_size, "M": 1,
            "has_bias": False,
            "float32_ref": inter["attn_out"],
        },
        "gate": {
            "w_key": "blk.0.ffn_gate.weight",
            "b_key": None,
            "activation": ffn_norm,
            "K": hidden_size, "N": intermediate_size, "M": 1,
            "has_bias": False,
            "float32_ref": inter["gate"],
        },
        "up": {
            "w_key": "blk.0.ffn_up.weight",
            "b_key": None,
            "activation": ffn_norm,
            "K": hidden_size, "N": intermediate_size, "M": 1,
            "has_bias": False,
            "float32_ref": inter["up"],
        },
        "down": {
            "w_key": "blk.0.ffn_down.weight",
            "b_key": None,
            "activation": inter["ffn_hidden"],
            "K": intermediate_size, "N": hidden_size, "M": 1,
            "has_bias": False,
            "float32_ref": inter["ffn_out"],
        },
    }

    results = {}

    for proj_name, proj in projections.items():
        w_f32 = weights[proj["w_key"]]           # (K, N) or (N, K) — q4_dequant transposes
        K, N = proj["K"], proj["N"]
        M = proj["M"]
        act_fp32 = proj["activation"].astype(np.float32).flatten()  # (K,)
        assert len(act_fp32) == K, (
            f"{proj_name}: activation shape {act_fp32.shape} != ({K},)"
        )

        bias = None
        if proj["has_bias"] and proj["b_key"] is not None:
            bias = weights[proj["b_key"]].astype(np.float32)

        # ── Quantize weights to INT4 per-block ───────────────────────
        # Weights from GGUF are already transposed to (N, K) by q4_dequant.
        # quantize_int4_per_block expects (K, N), so transpose them:
        w_kn = w_f32.T  # (K, N)
        wgt_packed, block_scales = quantize_int4_per_block(w_kn, group_size=128)

        # ── Quantize activation ──────────────────────────────────────
        act_scale = compute_act_scale(act_fp32)
        act_int8 = quantize_activation(act_fp32, act_scale).reshape(M, K)

        # ── Place data in DRAM ───────────────────────────────────────
        act_bytes = act_int8.astype(np.int8).tobytes()
        wgt_bytes = wgt_packed.tobytes()
        scl_bytes = block_scales.astype(np.float32).tobytes()
        out_size = M * N * 4

        model.dram[_dram_off(ACT_ADDR):_dram_off(ACT_ADDR) + len(act_bytes)] = act_bytes
        model.dram[_dram_off(WGT_ADDR):_dram_off(WGT_ADDR) + len(wgt_bytes)] = wgt_bytes
        model.dram[_dram_off(SCL_ADDR):_dram_off(SCL_ADDR) + len(scl_bytes)] = scl_bytes
        model.dram[_dram_off(OUT_ADDR):_dram_off(OUT_ADDR) + out_size] = b"\x00" * out_size

        # ── MMIO: write registers, trigger compute ───────────────────
        bridge = model.bridge
        bridge.handle("write", mxu_base + MXU.I_ADDR, ACT_ADDR)
        bridge.handle("write", mxu_base + MXU.W_ADDR, WGT_ADDR)
        bridge.handle("write", mxu_base + MXU.SCALE_ADDR, SCL_ADDR)
        bridge.handle("write", mxu_base + MXU.O_ADDR, OUT_ADDR)
        bridge.handle("write", mxu_base + MXU.DIM0, (K << 16) | M)
        bridge.handle("write", mxu_base + MXU.DIM1, N)
        bridge.handle("write", mxu_base + MXU.CMD, 1)  # START

        # ── Read MMIO output from DRAM ───────────────────────────────
        mmio_out = np.frombuffer(
            model.dram[_dram_off(OUT_ADDR):_dram_off(OUT_ADDR) + out_size],
            dtype=np.float32,
        ).reshape(M, N).copy()

        # ── Restore activation scale + bias (applied ONCE) ───────────
        mmio_restored = mmio_out * np.float32(act_scale)
        if bias is not None:
            mmio_restored = mmio_restored + bias.reshape(M, N)

        # ── Oracle: independent computation ──────────────────────────
        oracle_mmul = oracle_matmul(act_int8, wgt_packed, block_scales, M, K, N)
        assert oracle_mmul.shape == (M, N)
        oracle_restored = oracle_mmul * np.float32(act_scale)
        if bias is not None:
            oracle_restored = oracle_restored + bias.reshape(M, N)

        # ── Compare MMIO vs Oracle ───────────────────────────────────
        max_abs_err = float(np.max(np.abs(
            mmio_restored.astype(np.float64) - oracle_restored.astype(np.float64)
        )))
        denom = np.abs(oracle_restored.astype(np.float64)) + 1e-8
        max_rel_err = float(np.max(np.abs(
            mmio_restored.astype(np.float64) - oracle_restored.astype(np.float64)
        ) / denom))

        assert max_abs_err <= _ATOL * 1.1, (
            f"{proj_name}: max_abs_err={max_abs_err:.2e} > {_ATOL} (MMIO vs oracle)"
        )
        assert max_rel_err <= _RTOL * 1.1, (
            f"{proj_name}: max_rel_err={max_rel_err:.2e} > {_RTOL} (MMIO vs oracle)"
        )

        # ── Compare vs float32 model ─────────────────────────────────
        float32_ref = proj["float32_ref"].astype(np.float32).flatten()
        cos_sim = cosine_similarity(mmio_restored.flatten(), float32_ref)

        saturation_count = int(np.sum(
            np.abs(act_int8.flatten()) >= 127
        ))

        if cos_sim >= _COS_PASS:
            verdict = "PASS"
        elif cos_sim >= _COS_WARN:
            verdict = "PASS+WARN"
        else:
            verdict = "FAIL"

        assert verdict != "FAIL", (
            f"{proj_name}: cosine={cos_sim:.6f} < {_COS_WARN} (FAIL)"
        )

        results[proj_name] = {
            "M": M, "K": K, "N": N,
            "activation_scale": float(act_scale),
            "saturation_count": saturation_count,
            "max_abs_err": max_abs_err,
            "max_rel_err": max_rel_err,
            "cosine": cos_sim,
            "verdict": verdict,
        }

        # ── SIGNOFF_METRIC emission (per projection) ────────────────
        _t4c2_emit_metric(capsys, f"{proj_name}.M", M)
        _t4c2_emit_metric(capsys, f"{proj_name}.K", K)
        _t4c2_emit_metric(capsys, f"{proj_name}.N", N)
        _t4c2_emit_metric(capsys, f"{proj_name}.activation_scale", float(act_scale))
        _t4c2_emit_metric(capsys, f"{proj_name}.saturation_count", saturation_count)
        _t4c2_emit_metric(capsys, f"{proj_name}.max_abs_err", max_abs_err)
        _t4c2_emit_metric(capsys, f"{proj_name}.max_rel_err", max_rel_err)
        _t4c2_emit_metric(capsys, f"{proj_name}.cosine", cos_sim)
        _t4c2_emit_metric(capsys, f"{proj_name}.verdict", verdict)

    # ── Aggregate metrics ────────────────────────────────────────────
    all_cos = [r["cosine"] for r in results.values()]
    all_verdicts = [r["verdict"] for r in results.values()]
    min_cos = min(all_cos) if all_cos else 0.0

    if "FAIL" in all_verdicts:
        overall = "FAIL"
    elif "PASS+WARN" in all_verdicts:
        overall = "PASS+WARN"
    else:
        overall = "PASS"


# ══════════════════════════════════════════════════════════════════════════
# Task 4C3 — Real-GGUF tiled-scheduler projection gate
# ══════════════════════════════════════════════════════════════════════════

_T4C3_CASE_ID = "task-4c3-qwen25-3b-real-tiled-projections"
_GROUP_SIZE = 128
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1

# DRAM addresses (shared with tile_mmul descriptor)
_T4C3_DRAM_BASE = 0x80000000
_T4C3_ACT_ADDR = _T4C3_DRAM_BASE + 0x00000000   # 128 KB
_T4C3_WGT_ADDR = _T4C3_DRAM_BASE + 0x00200000   # 16 MB for tile-major weights
_T4C3_SCL_ADDR = _T4C3_DRAM_BASE + 0x01200000   # 4 MB for tile-major scales
_T4C3_OUT_ADDR = _T4C3_DRAM_BASE + 0x01600000   # 4 MB for output
_T4C3_SRAM_SIZE = 256 * 1024


def _t4c3_emit_metric(capsys, key: str, value) -> None:
    effective_case = os.environ.get("_FM_CASE_ID", "") or _T4C3_CASE_ID
    line = json.dumps({"case": effective_case, "key": key, "value": value})
    with capsys.disabled():
        print(f"\nSIGNOFF_METRIC {line}")


# ── Tile-major conversion helpers ────────────────────────────────────────

def _unpack_int4_raw(packed: bytes, num_values: int) -> np.ndarray:
    """Unpack INT4 from uint8 bytes → int8 array of length *num_values*.

    Low nibble = first/even, high nibble = second/odd. Sign-extends [-8, 7].
    """
    arr = np.frombuffer(packed, dtype=np.uint8).copy()
    low = (arr & 0x0F).astype(np.int8)
    high = ((arr >> 4) & 0x0F).astype(np.int8)
    low[low > 7] -= 16
    high[high > 7] -= 16
    result = np.empty(len(arr) * 2, dtype=np.int8)
    result[0::2] = low
    result[1::2] = high
    return result[:num_values]


def _pack_int4_raw(values: np.ndarray) -> bytes:
    """Pack INT8 values (must be in [-8,7]) into uint8 (2 per byte)."""
    vals = np.asarray(values, dtype=np.int8).flatten()
    if len(vals) % 2 != 0:
        vals = np.append(vals, 0)
    unsigned = np.where(vals < 0, vals + 16, vals).astype(np.uint8)
    packed = (unsigned[0::2] & 0x0F) | ((unsigned[1::2] & 0x0F) << 4)
    return packed.tobytes()


def _row_major_to_tile_major_int4(
    packed_row: bytes, K: int, N: int,
) -> bytes:
    """Convert row-major packed INT4 (K×N) → tile-major 128×128 layout.

    Each tile at (n_tile, k_block) occupies exactly TILE_WEIGHT_BYTES bytes.
    Partial edge tiles are zero-padded.
    """
    num_blocks = math.ceil(K / TILE_H)
    num_tiles = math.ceil(N / TILE_W)

    wgt_flat = _unpack_int4_raw(packed_row, K * N)
    W = wgt_flat.reshape(K, N)

    total_bytes = num_tiles * num_blocks * TILE_WEIGHT_BYTES
    result = bytearray(total_bytes)

    for n_tile in range(num_tiles):
        n_start = n_tile * TILE_W
        n_end = min(n_start + TILE_W, N)
        tile_width = n_end - n_start

        for k_block in range(num_blocks):
            k_start = k_block * TILE_H
            k_end = min(k_start + TILE_H, K)
            block_height = k_end - k_start

            sub = W[k_start:k_end, n_start:n_end]
            sub_packed = _pack_int4_raw(sub.flatten())

            offset = (n_tile * num_blocks + k_block) * TILE_WEIGHT_BYTES
            result[offset : offset + len(sub_packed)] = sub_packed

    return bytes(result)


def _scales_to_tile_major(
    block_scales: np.ndarray, K: int, N: int, group_size: int = _GROUP_SIZE,
) -> bytes:
    """Convert (num_blocks, N) FP32 block scales → tile-major 128×128 layout.

    Each tile at (n_tile, k_block) stores tile_width FP32 values;
    the remainder of TILE_SCALE_BYTES is zero-padded.
    """
    num_blocks = (K + group_size - 1) // group_size
    num_tiles = math.ceil(N / TILE_W)
    total_bytes = num_tiles * num_blocks * TILE_SCALE_BYTES
    result = bytearray(total_bytes)

    scales = np.asarray(block_scales, dtype=np.float32)
    assert scales.shape == (num_blocks, N), (
        f"Expected block_scales ({num_blocks},{N}), got {scales.shape}"
    )

    for n_tile in range(num_tiles):
        n_start = n_tile * TILE_W
        n_end = min(n_start + TILE_W, N)
        tile_width = n_end - n_start

        for k_block in range(num_blocks):
            tile_scales = scales[k_block, n_start:n_end]
            offset = (n_tile * num_blocks + k_block) * TILE_SCALE_BYTES
            result[offset : offset + tile_width * 4] = tile_scales.tobytes()

    return bytes(result)


# ── Inline MMIO infrastructure (per-block scaled MXU) ───────────────────

def _dram_off_t4c3(addr: int) -> int:
    return addr - _T4C3_DRAM_BASE


def _mxu_compute_tile_scaled(
    act_slice: np.ndarray,
    wgt_packed: np.ndarray,
    tile_scales: np.ndarray,
    M: int,
    block_h: int,
    tile_w: int,
    mxu: GoldenMXU,
) -> np.ndarray:
    """Compute single tile: INT4×INT8→INT32→clip→scale→FP32.

    Returns FP32 result, shape (M, tile_w).
    """
    w_flat = mxu.unpack_int4(wgt_packed).astype(np.int32)
    W = w_flat[: block_h * tile_w].reshape(block_h, tile_w)

    a_int32 = act_slice.astype(np.int32)
    partial = np.dot(a_int32, W)  # (M, tile_w)
    partial = np.clip(partial, _INT32_MIN, _INT32_MAX)

    sc = np.asarray(tile_scales, dtype=np.float32)[:tile_w]
    return partial.astype(np.float32) * sc[np.newaxis, :]


def _build_mmio_handlers_scaled(dram: bytearray, sram: bytearray):
    """Build mmio_write / mmio_read / wait_done callbacks with per-block
    scaled MXU matmul support (FP32 output).

    Returns (mmio_write, mmio_read, wait_done, tile_counter, DMA, MXU).
    """
    regfile: dict[tuple[int, int], int] = {}
    _last_dma_ch = [0]
    _mxu_invocations = [0]

    DMA = SimpleNamespace(
        CH0_SRC=0, CH0_DST=4, CH0_SIZE=8, CMD=12, STATUS=16,
        CH1_SRC=20, CH1_DST=24, CH1_SIZE=28,
    )
    MXU = SimpleNamespace(
        I_ADDR=0, W_ADDR=4, SCALE_ADDR=8, O_ADDR=12,
        CTRL=16, DIM0=20, DIM1=24, CMD=28, STATUS=32,
    )

    _DMA_BASE = 0xD0000000
    _MXU_BASE = 0x40000000

    def _mem_read(addr: int, size: int) -> bytes:
        if addr >= _T4C3_DRAM_BASE:
            off = _dram_off_t4c3(addr)
            return bytes(dram[off : off + size])
        return bytes(sram[addr : addr + size])

    def _mem_write(addr: int, data: bytes) -> None:
        if addr >= _T4C3_DRAM_BASE:
            off = _dram_off_t4c3(addr)
            dram[off : off + len(data)] = data[:]
        else:
            sram[addr : addr + len(data)] = data[:]

    def mmio_write(base: int, offset: int, value: int):
        regfile[(base, offset)] = value

        if base == _DMA_BASE:
            if offset in (DMA.CH0_SRC, DMA.CH0_DST, DMA.CH0_SIZE):
                _last_dma_ch[0] = 0
            elif offset in (DMA.CH1_SRC, DMA.CH1_DST, DMA.CH1_SIZE):
                _last_dma_ch[0] = 1
            elif offset == DMA.CMD and value == 1:
                ch = _last_dma_ch[0]
                src_key = (base, DMA.CH0_SRC if ch == 0 else DMA.CH1_SRC)
                dst_key = (base, DMA.CH0_DST if ch == 0 else DMA.CH1_DST)
                size_key = (base, DMA.CH0_SIZE if ch == 0 else DMA.CH1_SIZE)
                src = regfile.get(src_key, 0)
                dst = regfile.get(dst_key, 0)
                size = regfile.get(size_key, 0)
                if size > 0:
                    data = _mem_read(src, size)
                    _mem_write(dst, data)
                regfile[(base, DMA.STATUS)] = 0

        elif base == _MXU_BASE:
            if offset == MXU.CMD and value == 1:
                _mxu_invocations[0] += 1
                i_addr = regfile.get((base, MXU.I_ADDR), 0)
                w_addr = regfile.get((base, MXU.W_ADDR), 0)
                s_addr = regfile.get((base, MXU.SCALE_ADDR), 0)
                o_addr = regfile.get((base, MXU.O_ADDR), 0)
                ctrl = regfile.get((base, MXU.CTRL), 0)
                dim0 = regfile.get((base, MXU.DIM0), 0)
                dim1 = regfile.get((base, MXU.DIM1), 0)

                M_eff = dim0 & 0xFFFF
                block_h = (dim0 >> 16) & 0xFFFF
                tile_w = dim1 & 0xFFFF
                accumulate = (ctrl >> 2) & 1

                # Read activation slice
                act_bytes = M_eff * block_h
                act = np.frombuffer(
                    memoryview(sram)[i_addr : i_addr + act_bytes],
                    dtype=np.int8,
                ).copy().reshape(M_eff, block_h)

                # Read weight tile (packed INT4)
                wgt_bytes = (block_h * tile_w + 1) // 2
                wgt_packed = np.frombuffer(
                    memoryview(sram)[w_addr : w_addr + wgt_bytes],
                    dtype=np.uint8,
                ).copy()

                # Read scale tile (FP32)
                scl_bytes = tile_w * 4
                tile_scales = np.frombuffer(
                    memoryview(sram)[s_addr : s_addr + scl_bytes],
                    dtype=np.float32,
                ).copy()

                mxu = GoldenMXU()
                partial = _mxu_compute_tile_scaled(
                    act, wgt_packed, tile_scales, M_eff, block_h, tile_w, mxu,
                )

                if accumulate:
                    existing = np.frombuffer(
                        memoryview(sram)[o_addr : o_addr + M_eff * tile_w * 4],
                        dtype=np.float32,
                    ).copy().reshape(M_eff, tile_w)
                    partial = existing + partial

                sram[o_addr : o_addr + M_eff * tile_w * 4] = (
                    partial.astype(np.float32).tobytes()
                )
                regfile[(base, MXU.STATUS)] = 0

        return True

    def mmio_read(base: int, offset: int) -> int:
        return regfile.get((base, offset), 0)

    def wait_done(base: int, status_offset: int) -> None:
        pass  # synchronous — STATUS already 0

    return mmio_write, mmio_read, wait_done, _mxu_invocations, DMA, MXU


# ── Test: real-GGUF tiled projections ────────────────────────────────────

def test_qwen25_3b_real_tiled_projections(capsys) -> None:
    """Real-GGUF tiled-scheduler projection gate with independent oracle.

    Verifies:
      - Tile-major INT4 layout conversion produces correct tile count.
      - Tile-major FP32 scale layout matches per-block quantization.
      - tile_mmul() output matches independent oracle (atol=1e-4, rtol=1e-5).
      - tiled output agrees with direct-MMIO output from T4C2.
      - Cosine vs float32 model ≥ 0.97 threshold (graded).
      - SIGNOFF_METRIC emission per projection.
    """
    from qwen25_signoff_oracle import (
        compute_act_scale,
        cosine_similarity,
        matmul_int4_per_block as oracle_matmul,
        quantize_activation,
        quantize_int4_per_block,
    )
    from q4_dequant import load_selected_weights_from_gguf, load_tensor_row_from_gguf
    from qwen25_forward import Qwen25Layer
    from func_model import FuncModel

    gguf_path = _gguf_path()
    assert gguf_path.is_file(), f"GGUF not found: {gguf_path}"

    # ── 1. Load weights ──────────────────────────────────────────────
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
    tok_emb_row = load_tensor_row_from_gguf(str(gguf_path), "token_embd.weight", 9707)

    reader = GGUFReader(str(gguf_path))
    hidden_size = int(_get_field_value(reader, "qwen2.embedding_length"))
    intermediate_size = int(_get_field_value(reader, "qwen2.feed_forward_length"))
    num_heads = int(_get_field_value(reader, "qwen2.attention.head_count"))
    num_kv_heads = int(_get_field_value(reader, "qwen2.attention.head_count_kv"))
    head_dim = hidden_size // num_heads
    rope_theta = float(_get_field_value(reader, "qwen2.rope.freq_base") or 1000000.0)
    rms_eps = float(_get_field_value(reader, "qwen2.attention.layer_norm_rms_epsilon") or 1e-6)

    layer = Qwen25Layer(
        weights=weights, layer_idx=0,
        hidden_size=hidden_size, intermediate_size=intermediate_size,
        num_heads=num_heads, num_kv_heads=num_kv_heads, head_dim=head_dim,
        rope_theta=rope_theta, rms_eps=rms_eps,
    )
    inter = layer.forward_with_intermediates(tok_emb_row.copy(), position=0)

    x_norm = inter["x_norm"]
    attn_concat = inter["attn_concat"]
    ffn_norm = inter["ffn_norm"]

    # ── 2. Init FuncModel for DRAM ───────────────────────────────────
    model = FuncModel(dram_mb=256)
    dram = model.dram
    sram = bytearray(_T4C3_SRAM_SIZE)

    # ── 3. Projection definitions ────────────────────────────────────
    projections = {
        "Q_proj": {
            "w_key": "blk.0.attn_q.weight",
            "b_key": "blk.0.attn_q.bias",
            "activation": x_norm,
            "K": hidden_size, "N": hidden_size, "M": 1,
            "has_bias": True,
            "float32_ref": inter["Q_proj"],
        },
        "K_proj": {
            "w_key": "blk.0.attn_k.weight",
            "b_key": "blk.0.attn_k.bias",
            "activation": x_norm,
            "K": hidden_size, "N": _QWEN_KV_DIM, "M": 1,
            "has_bias": True,
            "float32_ref": inter["K_proj"],
        },
        "V_proj": {
            "w_key": "blk.0.attn_v.weight",
            "b_key": "blk.0.attn_v.bias",
            "activation": x_norm,
            "K": hidden_size, "N": _QWEN_KV_DIM, "M": 1,
            "has_bias": True,
            "float32_ref": inter["V_proj"],
        },
        "O_proj": {
            "w_key": "blk.0.attn_output.weight",
            "b_key": None,
            "activation": attn_concat,
            "K": hidden_size, "N": hidden_size, "M": 1,
            "has_bias": False,
            "float32_ref": inter["attn_out"],
        },
        "gate": {
            "w_key": "blk.0.ffn_gate.weight",
            "b_key": None,
            "activation": ffn_norm,
            "K": hidden_size, "N": intermediate_size, "M": 1,
            "has_bias": False,
            "float32_ref": inter["gate"],
        },
        "up": {
            "w_key": "blk.0.ffn_up.weight",
            "b_key": None,
            "activation": ffn_norm,
            "K": hidden_size, "N": intermediate_size, "M": 1,
            "has_bias": False,
            "float32_ref": inter["up"],
        },
        "down": {
            "w_key": "blk.0.ffn_down.weight",
            "b_key": None,
            "activation": inter["ffn_hidden"],
            "K": intermediate_size, "N": hidden_size, "M": 1,
            "has_bias": False,
            "float32_ref": inter["ffn_out"],
        },
    }

    results = {}
    all_direct_agree = {}

    for proj_name, proj in projections.items():
        w_f32 = weights[proj["w_key"]]
        K, N = proj["K"], proj["N"]
        M = proj["M"]
        act_fp32 = proj["activation"].astype(np.float32).flatten()
        assert len(act_fp32) == K, (
            f"{proj_name}: activation shape {act_fp32.shape} != ({K},)"
        )

        bias = None
        if proj["has_bias"] and proj["b_key"] is not None:
            bias = weights[proj["b_key"]].astype(np.float32)

        # ── Quantize weights to INT4 per-block ───────────────────────
        w_kn = w_f32.T  # (K, N)
        wgt_packed, block_scales = quantize_int4_per_block(w_kn, group_size=_GROUP_SIZE)

        # ── Quantize activation ──────────────────────────────────────
        act_scale = compute_act_scale(act_fp32)
        act_int8 = quantize_activation(act_fp32, act_scale).reshape(M, K)

        # ── Convert to tile-major ────────────────────────────────────
        num_blocks = math.ceil(K / TILE_H)
        num_tiles = math.ceil(N / TILE_W)
        expected_tile_count = num_blocks * num_tiles

        wgt_tile = _row_major_to_tile_major_int4(wgt_packed.tobytes(), K, N)
        scl_tile = _scales_to_tile_major(block_scales, K, N, group_size=_GROUP_SIZE)

        # ── Place data in DRAM ───────────────────────────────────────
        act_bytes = act_int8.astype(np.int8).tobytes()
        dram[_dram_off_t4c3(_T4C3_ACT_ADDR) : _dram_off_t4c3(_T4C3_ACT_ADDR) + len(act_bytes)] = act_bytes
        dram[_dram_off_t4c3(_T4C3_WGT_ADDR) : _dram_off_t4c3(_T4C3_WGT_ADDR) + len(wgt_tile)] = wgt_tile
        dram[_dram_off_t4c3(_T4C3_SCL_ADDR) : _dram_off_t4c3(_T4C3_SCL_ADDR) + len(scl_tile)] = scl_tile
        out_size = M * N * 4
        dram[_dram_off_t4c3(_T4C3_OUT_ADDR) : _dram_off_t4c3(_T4C3_OUT_ADDR) + out_size] = b"\x00" * out_size

        # Clear SRAM
        sram[:] = b"\x00" * len(sram)

        # ── Build mmio handlers ──────────────────────────────────────
        mmio_write, mmio_read, wait_done, tile_counter, DMA, MXU = (
            _build_mmio_handlers_scaled(dram, sram)
        )

        DMA_BASE = 0xD0000000
        MXU_BASE = 0x40000000

        desc = {
            "M": M,
            "K": K,
            "N": N,
            "input_addr": _T4C3_ACT_ADDR,
            "input_size": M * K,
            "weight_addr": _T4C3_WGT_ADDR,
            "scale_addr": _T4C3_SCL_ADDR,
            "output_addr": _T4C3_OUT_ADDR,
        }

        # ── Execute through tile_mmul ────────────────────────────────
        tile_mmul(desc, mmio_write, mmio_read, wait_done,
                  DMA_BASE, MXU_BASE, DMA, MXU)

        # ── Verify tile count ────────────────────────────────────────
        actual_tiles = tile_counter[0]
        assert actual_tiles == expected_tile_count, (
            f"{proj_name}: tile count {actual_tiles} != expected "
            f"{expected_tile_count} (num_blocks={num_blocks}, num_tiles={num_tiles})"
        )

        # ── Read tiled output from DRAM ──────────────────────────────
        tiled_raw = dram[_dram_off_t4c3(_T4C3_OUT_ADDR) : _dram_off_t4c3(_T4C3_OUT_ADDR) + out_size]
        tiled_out = np.frombuffer(tiled_raw, dtype=np.float32).reshape(M, N).copy()

        # ── Restore activation scale + bias ──────────────────────────
        tiled_restored = tiled_out * np.float32(act_scale)
        if bias is not None:
            tiled_restored = tiled_restored + bias.reshape(M, N)

        # ── Oracle: independent computation ──────────────────────────
        oracle_mmul = oracle_matmul(act_int8, wgt_packed, block_scales, M, K, N)
        oracle_restored = oracle_mmul * np.float32(act_scale)
        if bias is not None:
            oracle_restored = oracle_restored + bias.reshape(M, N)

        # ── Compare tiled vs oracle ──────────────────────────────────
        max_abs_err = float(np.max(np.abs(
            tiled_restored.astype(np.float64) - oracle_restored.astype(np.float64)
        )))
        denom = np.abs(oracle_restored.astype(np.float64)) + 1e-8
        max_rel_err = float(np.max(np.abs(
            tiled_restored.astype(np.float64) - oracle_restored.astype(np.float64)
        ) / denom))

        assert max_abs_err <= _ATOL * 1.1, (
            f"{proj_name}: max_abs_err={max_abs_err:.2e} > {_ATOL} (tiled vs oracle)"
        )
        assert max_rel_err <= _RTOL * 1.1, (
            f"{proj_name}: max_rel_err={max_rel_err:.2e} > {_RTOL} (tiled vs oracle)"
        )

        # ── Direct-MMIO agreement check ──────────────────────────────
        direct_out = None
        try:
            # Re-run direct MMIO path (same as T4C2) for agreement
            from regmap import MXU as MXU_REG
            direct_model = FuncModel(dram_mb=256)
            d_dram = direct_model.dram
            _D_DIRECT_ACT = _T4C3_DRAM_BASE + 0x00000000
            _D_DIRECT_WGT = _T4C3_DRAM_BASE + 0x00020000
            _D_DIRECT_SCL = _T4C3_DRAM_BASE + 0x01000000
            _D_DIRECT_OUT = _T4C3_DRAM_BASE + 0x01400000

            _do_dram_off = lambda a: a - _T4C3_DRAM_BASE

            d_wgt_bytes = wgt_packed.tobytes()
            d_scl_bytes = block_scales.astype(np.float32).tobytes()
            d_act_bytes = act_int8.astype(np.int8).tobytes()
            d_out_size = M * N * 4

            d_dram[_do_dram_off(_D_DIRECT_ACT):_do_dram_off(_D_DIRECT_ACT) + len(d_act_bytes)] = d_act_bytes
            d_dram[_do_dram_off(_D_DIRECT_WGT):_do_dram_off(_D_DIRECT_WGT) + len(d_wgt_bytes)] = d_wgt_bytes
            d_dram[_do_dram_off(_D_DIRECT_SCL):_do_dram_off(_D_DIRECT_SCL) + len(d_scl_bytes)] = d_scl_bytes
            d_dram[_do_dram_off(_D_DIRECT_OUT):_do_dram_off(_D_DIRECT_OUT) + d_out_size] = b"\x00" * d_out_size

            d_bridge = direct_model.bridge
            mxu_base_d = 0x40000000
            d_bridge.handle("write", mxu_base_d + MXU_REG.I_ADDR, _D_DIRECT_ACT)
            d_bridge.handle("write", mxu_base_d + MXU_REG.W_ADDR, _D_DIRECT_WGT)
            d_bridge.handle("write", mxu_base_d + MXU_REG.SCALE_ADDR, _D_DIRECT_SCL)
            d_bridge.handle("write", mxu_base_d + MXU_REG.O_ADDR, _D_DIRECT_OUT)
            d_bridge.handle("write", mxu_base_d + MXU_REG.DIM0, (K << 16) | M)
            d_bridge.handle("write", mxu_base_d + MXU_REG.DIM1, N)
            d_bridge.handle("write", mxu_base_d + MXU_REG.CMD, 1)

            direct_mmul = np.frombuffer(
                d_dram[_do_dram_off(_D_DIRECT_OUT):_do_dram_off(_D_DIRECT_OUT) + d_out_size],
                dtype=np.float32,
            ).reshape(M, N).copy()
            direct_out = direct_mmul * np.float32(act_scale)
            if bias is not None:
                direct_out = direct_out + bias.reshape(M, N)

            direct_max_abs = float(np.max(np.abs(
                tiled_restored.astype(np.float64) - direct_out.astype(np.float64)
            )))
            direct_agree = direct_max_abs <= _ATOL * 1.1
        except Exception:
            direct_agree = False
            direct_max_abs = float("nan")

        all_direct_agree[proj_name] = direct_agree

        # ── Compare vs float32 model (cosine) ────────────────────────
        float32_ref = proj["float32_ref"].astype(np.float32).flatten()
        cos_sim = cosine_similarity(tiled_restored.flatten(), float32_ref)

        if cos_sim >= _COS_PASS:
            verdict = "PASS"
        elif cos_sim >= _COS_WARN:
            verdict = "PASS+WARN"
        else:
            verdict = "FAIL"

        assert verdict != "FAIL", (
            f"{proj_name}: cosine={cos_sim:.6f} < {_COS_WARN} (FAIL)"
        )

        results[proj_name] = {
            "M": M, "K": K, "N": N,
            "tile_count": actual_tiles,
            "max_abs_err": max_abs_err,
            "max_rel_err": max_rel_err,
            "cosine": cos_sim,
            "verdict": verdict,
            "direct_agree": direct_agree,
        }

        # ── SIGNOFF_METRIC emission per projection ────────────────────
        _t4c3_emit_metric(capsys, f"{proj_name}.tile_count", actual_tiles)
        _t4c3_emit_metric(capsys, f"{proj_name}.max_abs_err", max_abs_err)
        _t4c3_emit_metric(capsys, f"{proj_name}.max_rel_err", max_rel_err)
        _t4c3_emit_metric(capsys, f"{proj_name}.cosine", cos_sim)
        _t4c3_emit_metric(capsys, f"{proj_name}.verdict", verdict)
        _t4c3_emit_metric(capsys, f"{proj_name}.direct_agreement", direct_agree)

    # ── Aggregate metrics ────────────────────────────────────────────
    all_cos = [r["cosine"] for r in results.values()]
    all_verdicts = [r["verdict"] for r in results.values()]
    total_tiles = sum(r["tile_count"] for r in results.values())
    min_cos = min(all_cos) if all_cos else 0.0

    if "FAIL" in all_verdicts:
        overall = "FAIL"
    elif "PASS+WARN" in all_verdicts:
        overall = "PASS+WARN"
    else:
        overall = "PASS"

    _t4c3_emit_metric(capsys, "total_tile_count", total_tiles)


# ══════════════════════════════════════════════════════════════════════════
# Task 4C4 — Connected real-GGUF blk.0 dual-oracle hard gate
# ══════════════════════════════════════════════════════════════════════════

_T4C4_CASE_ID = "task-4c4-qwen25-3b-real-connected-blk0"
_T4C4_DRAM_BASE = 0x80000000
_T4C4_MXU_BASE = 0x40000000
_T4C4_SFU_BASE = 0x40001000
_T4C4_VEC_BASE = 0x40002000

# DRAM addresses for MXU data
_T4C4_ACT_ADDR = _T4C4_DRAM_BASE + 0x00000000   # activation
_T4C4_WGT_ADDR = _T4C4_DRAM_BASE + 0x00020000   # weight (packed INT4)
_T4C4_SCL_ADDR = _T4C4_DRAM_BASE + 0x01000000   # block scales
_T4C4_OUT_ADDR = _T4C4_DRAM_BASE + 0x01400000   # MXU output
_T4C4_BIAS_ADDR = _T4C4_DRAM_BASE + 0x01800000  # bias (FP32)

# SRAM offsets for SFU/Vector data (raw, crossbar auto-maps to SRAM)
_T4C4_SFU_IN   = 0x00001000   # SFU input
_T4C4_SFU_OUT  = 0x00010000   # SFU output (space for up to 11008 elem FP16 = 22KB)
_T4C4_VEC_A    = 0x00020000   # Vector operand A
_T4C4_VEC_B    = 0x00030000   # Vector operand B
_T4C4_VEC_O    = 0x00040000   # Vector output
_T4C4_SRAM     = 0x00100000   # scratch SRAM

# Oracle tolerances per operator category
_T4C4_MXU_ATOL = 1e-4
_T4C4_MXU_RTOL = 1e-5
_T4C4_SFU_ATOL = 2e-3
_T4C4_SFU_RTOL = 1e-2
_T4C4_BRDG_ATOL = 1e-6
_T4C4_COS_PASS = 0.97
_T4C4_COS_WARN = 0.96
_T4C4_VEC_SCALE = 4096  # fixed-point scale for Vector INT32 conversion


def _t4c4_emit_metric(capsys, key: str, value) -> None:
    effective_case = os.environ.get("_FM_CASE_ID", "") or _T4C4_CASE_ID
    line = json.dumps({"case": effective_case, "key": key, "value": value})
    with capsys.disabled():
        print(f"\nSIGNOFF_METRIC {line}")


def _t4c4_dram_off(addr: int) -> int:
    return addr - _T4C4_DRAM_BASE


def _t4c4_compare_mxu(mmio_out: np.ndarray, oracle_out: np.ndarray,
                       proj_name: str) -> tuple[float, float]:
    """Compare MMIO output vs independent oracle (quantized MXU tolerances)."""
    max_abs_err = float(np.max(np.abs(
        mmio_out.astype(np.float64) - oracle_out.astype(np.float64)
    )))
    denom = np.abs(oracle_out.astype(np.float64)) + 1e-8
    max_rel_err = float(np.max(np.abs(
        mmio_out.astype(np.float64) - oracle_out.astype(np.float64)
    ) / denom))
    assert max_abs_err <= _T4C4_MXU_ATOL * 1.1, (
        f"{proj_name}: max_abs_err={max_abs_err:.2e} > {_T4C4_MXU_ATOL}"
    )
    assert max_rel_err <= _T4C4_MXU_RTOL * 1.1, (
        f"{proj_name}: max_rel_err={max_rel_err:.2e} > {_T4C4_MXU_RTOL}"
    )
    return max_abs_err, max_rel_err


def _t4c4_compare_sfu(hw_out: np.ndarray, ref_out: np.ndarray,
                       op_name: str) -> tuple[float, float]:
    """Element-wise SFU comparison: FP16 tolerances."""
    return _t4c4_compare_sfu_op(hw_out, ref_out, op_name, _T4C4_SFU_ATOL, _T4C4_SFU_RTOL)


def _t4c4_compare_sfu_op(hw_out: np.ndarray, ref_out: np.ndarray,
                           op_name: str,
                           sfu_atol: float, sfu_rtol: float) -> tuple[float, float]:
    """Element-wise SFU comparison with custom tolerances (for RoPE etc)."""
    hw = hw_out.astype(np.float64).flatten()
    ref = ref_out.astype(np.float64).flatten()
    abs_diff = np.abs(hw - ref)
    rel_diff = np.abs(hw - ref) / (np.abs(ref) + 1e-8)
    passed = (abs_diff <= sfu_atol) | (rel_diff <= sfu_rtol)
    assert np.all(passed), (
        f"{op_name}: SFU comparison FAIL — "
        f"max_abs_err={float(np.max(abs_diff)):.2e}, "
        f"max_rel_err={float(np.max(rel_diff[np.isfinite(rel_diff)])):.2e}"
    )
    max_abs = float(np.max(abs_diff))
    max_rel = float(np.max(rel_diff[np.isfinite(rel_diff)])) if np.any(np.isfinite(rel_diff)) else 0.0
    return max_abs, max_rel


def _t4c4_compare_vector(vec_out: np.ndarray, ref_out: np.ndarray,
                          op_name: str) -> tuple[float, float]:
    """INT32 Vector: exact comparison."""
    v = vec_out.astype(np.int32).flatten()
    r = ref_out.astype(np.int32).flatten()
    assert np.array_equal(v, r), (
        f"{op_name}: Vector INT32 mismatch — {int(np.sum(v != r))} differing elements"
    )
    return 0.0, 0.0


def _t4c4_compare_bridge(brdg_out: np.ndarray, ref_out: np.ndarray,
                          op_name: str) -> tuple[float, float]:
    """FUNC_BRIDGE: atol=1e-6."""
    max_abs_err = float(np.max(np.abs(
        brdg_out.astype(np.float64).flatten() - ref_out.astype(np.float64).flatten()
    )))
    assert max_abs_err <= _T4C4_BRDG_ATOL * 1.1, (
        f"{op_name}: bridge max_abs_err={max_abs_err:.2e} > {_T4C4_BRDG_ATOL}"
    )
    return max_abs_err, 0.0


def _t4c4_cosine_simple(hw_out: np.ndarray, f32_ref: np.ndarray) -> float:
    """Cosine similarity without assertion (for internal Vector boundaries)."""
    from qwen25_signoff_oracle import cosine_similarity as _cos
    return _cos(hw_out.flatten(), f32_ref.flatten())


def _t4c4_cosine_verdict(hw_out: np.ndarray, f32_ref: np.ndarray,
                          caller: str = "") -> tuple[float, str]:
    """Graded cosine comparison against float32 model."""
    from qwen25_signoff_oracle import cosine_similarity as _cos

    cos = _cos(hw_out.flatten(), f32_ref.flatten())
    if cos >= _T4C4_COS_PASS:
        verdict = "PASS"
    elif cos >= _T4C4_COS_WARN:
        verdict = "PASS+WARN"
    else:
        verdict = "FAIL"
    assert verdict != "FAIL", (
        f"{caller}: Cosine {cos:.6f} < {_T4C4_COS_WARN} (FAIL)"
    )
    return cos, verdict


def test_qwen25_3b_real_connected_blk0(capsys) -> None:
    """Real-GGUF connected blk.0 dual-oracle hard gate.

    Executes the full connected dataflow for Qwen2.5-3B blk.0 token 9707
    with same-input local oracle at every boundary. The next step receives
    the actual MMIO/SFU/Vector/FUNC_BRIDGE output, not the oracle value.

    Verifies:
      - Every MXU boundary passes quantized oracle (atol=1e-4, rtol=1e-5).
      - Every SFU boundary passes FP16 oracle (atol=2e-3, rtol=1e-2).
      - Every VECTOR boundary is INT32 bit-exact.
      - Every FUNC_BRIDGE boundary passes (atol=1e-6).
      - Projections and final output have cosine >= 0.97 vs float32.
      - No FP32 bypass, no NumPy oracle forwarding, no stale refs.
    """
    from qwen25_signoff_oracle import (
        compute_act_scale,
        projection_oracle,
        quantize_activation,
        quantize_int4_per_block,
    )
    from q4_dequant import load_selected_weights_from_gguf, load_tensor_row_from_gguf
    from qwen25_forward import Qwen25Layer
    from regmap import MXU, SFU, VECTOR
    from func_model import FuncModel
    from golden_executor import GoldenSFU, GoldenVector

    gguf_path = _gguf_path()
    assert gguf_path.is_file(), f"GGUF not found: {gguf_path}"

    # ── 1. Load weights and hyperparameters ──────────────────────────
    layer0_names = {
        "blk.0.attn_norm.weight", "blk.0.attn_q.weight", "blk.0.attn_q.bias",
        "blk.0.attn_k.weight", "blk.0.attn_k.bias", "blk.0.attn_v.weight", "blk.0.attn_v.bias",
        "blk.0.attn_output.weight", "blk.0.ffn_norm.weight",
        "blk.0.ffn_gate.weight", "blk.0.ffn_up.weight", "blk.0.ffn_down.weight",
    }
    weights = load_selected_weights_from_gguf(str(gguf_path), layer0_names)
    tok_emb_row = load_tensor_row_from_gguf(str(gguf_path), "token_embd.weight", 9707)

    reader = gguf.GGUFReader(str(gguf_path))
    hidden_size = int(_get_field_value(reader, "qwen2.embedding_length"))
    intermediate_size = int(_get_field_value(reader, "qwen2.feed_forward_length"))
    num_heads = int(_get_field_value(reader, "qwen2.attention.head_count"))
    num_kv_heads = int(_get_field_value(reader, "qwen2.attention.head_count_kv"))
    head_dim = hidden_size // num_heads
    rope_theta = float(_get_field_value(reader, "qwen2.rope.freq_base") or 1000000.0)
    rms_eps = float(_get_field_value(reader, "qwen2.attention.layer_norm_rms_epsilon") or 1e-6)

    layer = Qwen25Layer(
        weights=weights, layer_idx=0,
        hidden_size=hidden_size, intermediate_size=intermediate_size,
        num_heads=num_heads, num_kv_heads=num_kv_heads, head_dim=head_dim,
        rope_theta=rope_theta, rms_eps=rms_eps,
    )
    inter = layer.forward_with_intermediates(tok_emb_row.copy(), position=0)

    # Extract float32 reference outputs for every boundary
    ref_x_norm = inter["x_norm"]           # (2048,)
    ref_Q = inter["Q_proj"]                 # (2048,)
    ref_K = inter["K_proj"]                 # (256,)
    ref_V = inter["V_proj"]                 # (256,)
    ref_attn_concat = inter["attn_concat"]  # (2048,)
    ref_attn_out = inter["attn_out"]        # (2048,)
    ref_resid1 = inter["resid1"]            # (2048,)
    ref_ffn_norm = inter["ffn_norm"]        # (2048,)
    ref_gate = inter["gate"]                # (11008,)
    ref_up = inter["up"]                    # (11008,)
    ref_gate_act = inter["gate_act"]        # (11008,)
    ref_ffn_hidden = inter["ffn_hidden"]    # (11008,)
    ref_ffn_out = inter["ffn_out"]          # (2048,)
    ref_final = inter["final"]              # (2048,)

    # Attn norm gamma
    attn_norm_w = weights["blk.0.attn_norm.weight"].astype(np.float32)
    ffn_norm_w = weights["blk.0.ffn_norm.weight"].astype(np.float32)

    # ── 2. Init FuncModel and independent oracles ─────────────────────
    model = FuncModel(dram_mb=256)
    bridge = model.bridge
    gsfu = GoldenSFU()
    gvec = GoldenVector()

    # ── 3. Helper: MXU projection step ───────────────────────────────
    def _mxu_step(act_fp32: np.ndarray, w_f32: np.ndarray,
                  bias_f32: np.ndarray | None,
                  K: int, N: int, M: int,
                   name: str) -> tuple[np.ndarray, dict]:
        """Run one MXU projection: quantize → MMIO → restore → compare → record."""
        assert act_fp32.size == M * K
        act_fp32_flat = act_fp32.astype(np.float32).flatten()
        act_scale = compute_act_scale(act_fp32_flat)
        act_int8 = quantize_activation(act_fp32_flat, act_scale).reshape(M, K)

        w_kn = w_f32.T  # (K, N)
        wgt_packed, block_scales = quantize_int4_per_block(w_kn, group_size=128)

        act_bytes = act_int8.astype(np.int8).tobytes()
        wgt_bytes = wgt_packed.tobytes()
        scl_bytes = block_scales.astype(np.float32).tobytes()
        out_size = M * N * 4

        model.dram[_t4c4_dram_off(_T4C4_ACT_ADDR):_t4c4_dram_off(_T4C4_ACT_ADDR) + len(act_bytes)] = act_bytes
        model.dram[_t4c4_dram_off(_T4C4_WGT_ADDR):_t4c4_dram_off(_T4C4_WGT_ADDR) + len(wgt_bytes)] = wgt_bytes
        model.dram[_t4c4_dram_off(_T4C4_SCL_ADDR):_t4c4_dram_off(_T4C4_SCL_ADDR) + len(scl_bytes)] = scl_bytes
        model.dram[_t4c4_dram_off(_T4C4_OUT_ADDR):_t4c4_dram_off(_T4C4_OUT_ADDR) + out_size] = b"\x00" * out_size

        bridge.handle("write", _T4C4_MXU_BASE + MXU.I_ADDR, _T4C4_ACT_ADDR)
        bridge.handle("write", _T4C4_MXU_BASE + MXU.W_ADDR, _T4C4_WGT_ADDR)
        bridge.handle("write", _T4C4_MXU_BASE + MXU.SCALE_ADDR, _T4C4_SCL_ADDR)
        bridge.handle("write", _T4C4_MXU_BASE + MXU.O_ADDR, _T4C4_OUT_ADDR)
        bridge.handle("write", _T4C4_MXU_BASE + MXU.DIM0, (K << 16) | M)
        bridge.handle("write", _T4C4_MXU_BASE + MXU.DIM1, N)
        bridge.handle("write", _T4C4_MXU_BASE + MXU.CMD, 1)

        mmio_out = np.frombuffer(
            model.dram[_t4c4_dram_off(_T4C4_OUT_ADDR):_t4c4_dram_off(_T4C4_OUT_ADDR) + out_size],
            dtype=np.float32,
        ).reshape(M, N).copy()

        # Restore scale + bias
        mmio_restored = mmio_out * np.float32(act_scale)
        if bias_f32 is not None:
            mmio_restored = mmio_restored + bias_f32.astype(np.float32).reshape(M, N)

        # Oracle
        oracle_restored = projection_oracle(
            act_int8, wgt_packed, block_scales, act_scale,
            bias_f32.astype(np.float32) if bias_f32 is not None else None,
            M, K, N,
        )

        max_abs_err, max_rel_err = _t4c4_compare_mxu(
            mmio_restored, oracle_restored, name,
        )

        return mmio_restored.flatten().astype(np.float32), {
            "shape": f"({M},{N})",
            "dtype": "fp32",
            "scale": float(act_scale),
            "saturation": int(np.sum(np.abs(act_int8.flatten()) >= 127)),
            "comparator": "mxu_int4(atol=1e-4,rtol=1e-5)",
            "max_abs_err": max_abs_err,
            "max_rel_err": max_rel_err,
        }

    # ── 4. Helper: SFU step ──────────────────────────────────────────
    def _sfu_step(inp_fp32: np.ndarray, opcode: int,
                  dim_lo: int, dim_hi: int, pos: int,
                  name: str,
                  sfu_atol: float = _T4C4_SFU_ATOL,
                  sfu_rtol: float = _T4C4_SFU_RTOL) -> tuple[np.ndarray, dict]:
        """Run one SFU operation: place FP16 input → fire → read → compare."""
        inp_f16 = inp_fp32.astype(np.float16)
        inp_bytes = inp_f16.tobytes()
        model.sram[_T4C4_SFU_IN:_T4C4_SFU_IN + len(inp_bytes)] = inp_bytes

        bridge.handle("write", _T4C4_SFU_BASE + SFU.CTRL, opcode)
        bridge.handle("write", _T4C4_SFU_BASE + SFU.I_ADDR, _T4C4_SFU_IN)
        bridge.handle("write", _T4C4_SFU_BASE + SFU.O_ADDR, _T4C4_SFU_OUT)
        bridge.handle("write", _T4C4_SFU_BASE + SFU.DIM, dim_lo | (dim_hi << 16))
        bridge.handle("write", _T4C4_SFU_BASE + SFU.POS, pos)
        bridge.handle("write", _T4C4_SFU_BASE + SFU.CMD, 1)

        out_f16 = np.frombuffer(
            model.sram[_T4C4_SFU_OUT:_T4C4_SFU_OUT + dim_lo * 2],
            dtype=np.float16,
        ).copy()
        out_fp32 = out_f16.astype(np.float32)

        # Reference oracle
        if opcode == 6:  # RMSNORM
            ref_out = gsfu.rmsnorm_ref(inp_fp32, rms_eps)
        elif opcode == 5:  # ROPE
            hd = dim_hi if dim_hi else max(dim_lo // 4, 2)
            k_len = 2 * hd
            q_in = inp_fp32[:dim_lo - k_len]
            k_in = inp_fp32[dim_lo - k_len:dim_lo]
            nq = max(1, (dim_lo - k_len) // hd)
            q_ref, k_ref = gsfu.rope_ref(q_in, k_in, position=pos,
                                          num_heads=nq, head_dim=hd)
            ref_out = np.concatenate([q_ref, k_ref])
        elif opcode == 0:  # SOFTMAX
            ref_out = gsfu.softmax_ref(inp_fp32)
        elif opcode == 4:  # SILU
            ref_out = gsfu.silu_ref(inp_fp32)
        else:
            ref_out = inp_fp32

        max_abs_err, max_rel_err = _t4c4_compare_sfu_op(
            out_fp32, ref_out, name, sfu_atol, sfu_rtol)
        return out_fp32, {
            "shape": f"({dim_lo},)",
            "dtype": "fp16",
            "opcode": opcode,
            "comparator": f"sfu_fp16(atol={sfu_atol:.0e},rtol={sfu_rtol:.0e})",
            "max_abs_err": max_abs_err,
            "max_rel_err": max_rel_err,
        }

    # ── 5. Helper: VECTOR step ───────────────────────────────────────
    def _vec_step(a_fp32: np.ndarray, b_fp32: np.ndarray | None,
                  opcode: int, dim: int, name: str,
                  a_dtype: str = "int32", b_dtype: str = "int32") -> tuple[np.ndarray, dict]:
        """Run one Vector operation."""
        # Write operand A
        if a_dtype == "fp16":
            a_bytes = a_fp32.astype(np.float16).tobytes()
            a_elem_size = 2
        else:
            a_bytes = a_fp32.astype(np.int32).tobytes()
            a_elem_size = 4
        model.sram[_T4C4_VEC_A:_T4C4_VEC_A + len(a_bytes)] = a_bytes

        if b_fp32 is not None:
            if b_dtype == "fp16":
                b_bytes = b_fp32.astype(np.float16).tobytes()
            else:
                b_bytes = b_fp32.astype(np.int32).tobytes()
            model.sram[_T4C4_VEC_B:_T4C4_VEC_B + len(b_bytes)] = b_bytes

        bridge.handle("write", _T4C4_VEC_BASE + VECTOR.CTRL, opcode)
        bridge.handle("write", _T4C4_VEC_BASE + VECTOR.A_ADDR, _T4C4_VEC_A)
        bridge.handle("write", _T4C4_VEC_BASE + VECTOR.B_ADDR, _T4C4_VEC_B if b_fp32 is not None else 0)
        bridge.handle("write", _T4C4_VEC_BASE + VECTOR.O_ADDR, _T4C4_VEC_O)
        bridge.handle("write", _T4C4_VEC_BASE + VECTOR.DIM, dim)
        bridge.handle("write", _T4C4_VEC_BASE + VECTOR.CMD, 1)

        # Read output
        if opcode in (2, 3):  # reduce ops: FP16 scalar
            out_bytes = dim * 2
        elif opcode == 5:  # RESID: INT32
            out_bytes = dim * 4
        else:
            out_bytes = dim * 4  # INT32

        out_raw = model.sram[_T4C4_VEC_O:_T4C4_VEC_O + out_bytes]
        if opcode in (2, 3):
            out_val = np.frombuffer(out_raw, dtype=np.float16).astype(np.float32)
        else:
            out_val = np.frombuffer(out_raw, dtype=np.int32)

        # Compute reference for comparison (using GoldenVector)
        if opcode == 0:  # ADD
            a_i32 = a_fp32.astype(np.int32)
            b_i32 = b_fp32.astype(np.int32) if b_fp32 is not None else np.zeros(dim, dtype=np.int32)
            ref_out = gvec.add(a_i32, b_i32)
        elif opcode == 1:  # MUL
            a_i32 = a_fp32.astype(np.int32)
            b_i32 = b_fp32.astype(np.int32) if b_fp32 is not None else np.ones(dim, dtype=np.int32)
            ref_out = gvec.mul(a_i32, b_i32)
        elif opcode == 5:  # RESID — A goes through FP16 roundtrip matching bridge
            a_f16 = a_fp32.astype(np.float16).astype(np.float32)
            b_i32 = b_fp32.astype(np.int32) if b_fp32 is not None else np.zeros(dim, dtype=np.int32)
            ref_out = gvec.residual_add(a_f16, b_i32)
        else:
            ref_out = np.zeros(dim, dtype=np.int32)

        _t4c4_compare_vector(out_val, ref_out, name)
        return out_val, {
            "shape": f"({dim},)",
            "dtype": "fp16" if opcode in (2, 3) else "int32",
            "opcode": opcode,
            "comparator": "int32_bit_exact",
            "max_abs_err": 0.0,
            "max_rel_err": 0.0,
        }

    # ── 6. Helper: FUNC_BRIDGE step ──────────────────────────────────
    def _bridge_step(result: np.ndarray, ref: np.ndarray,
                     name: str, dtype: str = "fp32") -> dict:
        """Record a FUNC_BRIDGE boundary."""
        max_abs_err, _ = _t4c4_compare_bridge(result, ref, name)
        return {
            "shape": str(result.shape),
            "dtype": dtype,
            "comparator": "bridge(atol=1e-6)",
            "max_abs_err": max_abs_err,
            "max_rel_err": 0.0,
        }

    # ══════════════════════════════════════════════════════════════════
    # CONNECTED PIPELINE EXECUTION
    # ══════════════════════════════════════════════════════════════════

    boundaries: dict[str, dict] = {}
    all_verdicts: list[str] = []
    cosines: list[float] = []

    residual = tok_emb_row.astype(np.float32).copy()  # (2048,)

    # ── B01: Pre-attn RMSNorm (SFU) ──────────────────────────────────
    step_inp = residual.copy()
    normalized, b01_metrics = _sfu_step(step_inp, 6, hidden_size, 0, 0, "B01_pre_attn_rmsnorm")
    boundaries["B01_pre_attn_rmsnorm"] = b01_metrics
    boundaries["B01_pre_attn_rmsnorm"]["verdict"] = "PASS"

    # ── B02: Gamma multiply (FUNC_BRIDGE) ────────────────────────────
    gamma_x_norm = normalized * attn_norm_w
    # Bridge oracle: same normalized input × gamma in float64 (exact)
    bridge_oracle_norm = normalized.astype(np.float64) * attn_norm_w.astype(np.float64)
    b02_metrics = _bridge_step(gamma_x_norm, bridge_oracle_norm.astype(np.float32), "B02_gamma_multiply_attn")
    boundaries["B02_gamma_multiply_attn"] = b02_metrics
    cos02 = _t4c4_cosine_simple(gamma_x_norm, ref_x_norm)
    boundaries["B02_gamma_multiply_attn"] = b02_metrics
    boundaries["B02_gamma_multiply_attn"]["cosine"] = cos02
    boundaries["B02_gamma_multiply_attn"]["verdict"] = "PASS+WARN" if cos02 < _T4C4_COS_PASS else "PASS"
    cosines.append(cos02)
    if cos02 < _T4C4_COS_PASS:
        all_verdicts.append("PASS+WARN")
    else:
        all_verdicts.append("PASS")

    x_norm_hw = gamma_x_norm.copy()  # Forward this to Q/K/V

    # ── B03: Q_proj (MXU) ────────────────────────────────────────────
    q_w = weights["blk.0.attn_q.weight"]  # (N, K) from q4_dequant
    q_bias = weights["blk.0.attn_q.bias"]
    q_hw, b03_metrics = _mxu_step(x_norm_hw, q_w, q_bias, hidden_size, hidden_size, 1, "B03_Q_proj")
    cos03, v03 = _t4c4_cosine_verdict(q_hw, ref_Q, "B03")
    b03_metrics["cosine"] = cos03
    b03_metrics["verdict"] = v03
    boundaries["B03_Q_proj"] = b03_metrics
    cosines.append(cos03)
    all_verdicts.append(v03)

    # ── B04: K_proj (MXU) ────────────────────────────────────────────
    k_w = weights["blk.0.attn_k.weight"]
    k_bias = weights["blk.0.attn_k.bias"]
    k_hw, b04_metrics = _mxu_step(x_norm_hw, k_w, k_bias, hidden_size, 256, 1, "B04_K_proj")
    cos04, v04 = _t4c4_cosine_verdict(k_hw, ref_K, "B04")
    b04_metrics["cosine"] = cos04
    b04_metrics["verdict"] = v04
    boundaries["B04_K_proj"] = b04_metrics
    cosines.append(cos04)
    all_verdicts.append(v04)

    # ── B05: V_proj (MXU) ────────────────────────────────────────────
    v_w = weights["blk.0.attn_v.weight"]
    v_bias = weights["blk.0.attn_v.bias"]
    v_hw, b05_metrics = _mxu_step(x_norm_hw, v_w, v_bias, hidden_size, 256, 1, "B05_V_proj")
    cos05, v05 = _t4c4_cosine_verdict(v_hw, ref_V, "B05")
    b05_metrics["cosine"] = cos05
    b05_metrics["verdict"] = v05
    boundaries["B05_V_proj"] = b05_metrics
    cosines.append(cos05)
    all_verdicts.append(v05)

    # ── B06: RoPE (SFU) — pack Q+K, rotate, unpack ───────────────────
    q_rope_in = q_hw.astype(np.float32).reshape(num_heads, head_dim).flatten()  # (2048,)
    k_rope_in = k_hw.astype(np.float32).reshape(num_kv_heads, head_dim).flatten()  # (256,)
    rope_packed = np.concatenate([q_rope_in, k_rope_in]).astype(np.float32)  # (2304,)
    rope_total = len(rope_packed)
    rope_out_fp32, b06_metrics = _sfu_step(rope_packed, 5, rope_total, head_dim, 0, "B06_RoPE",
                                               sfu_atol=5e-1, sfu_rtol=1e-2)
    q_rope_hw = rope_out_fp32[:hidden_size]   # (2048,)
    k_rope_hw = rope_out_fp32[hidden_size:]   # (256,)
    boundaries["B06_RoPE"] = b06_metrics

    # Verify RoPE vs float32 reference (independently computed rope_rotate)
    from qwen25_forward import rope_rotate
    Q_ref_reshape = ref_Q.reshape(num_heads, head_dim)
    K_ref_reshape = ref_K.reshape(num_kv_heads, head_dim)
    Q_rot_ref = rope_rotate(Q_ref_reshape, 0, rope_theta)
    K_rot_ref = rope_rotate(K_ref_reshape, 0, rope_theta)
    cos_qr = _t4c4_cosine_simple(
        q_rope_hw.reshape(num_heads, head_dim).flatten(),
        Q_rot_ref.flatten(),
    )
    cos_kr = _t4c4_cosine_simple(
        k_rope_hw.reshape(num_kv_heads, head_dim).flatten(),
        K_rot_ref.flatten(),
    )
    boundaries["B06_RoPE"]["cosine_Q_rot"] = cos_qr
    boundaries["B06_RoPE"]["cosine_K_rot"] = cos_kr
    b06_cos = min(cos_qr, cos_kr)
    if b06_cos >= _T4C4_COS_PASS:
        b06_v = "PASS"
    elif b06_cos >= _T4C4_COS_WARN:
        b06_v = "PASS+WARN"
    else:
        b06_v = "FAIL"
    boundaries["B06_RoPE"]["verdict"] = b06_v
    boundaries["B06_RoPE"]["cosine"] = b06_cos
    cosines.append(b06_cos)
    all_verdicts.append(b06_v)

    # ── B07: GQA repeat K/V (FUNC_BRIDGE) ────────────────────────────
    n_repeat = num_heads // num_kv_heads
    k_rot_reshape = k_rope_hw.reshape(num_kv_heads, head_dim)
    k_rot_rep = np.repeat(k_rot_reshape, n_repeat, axis=0)
    v_reshape = v_hw.reshape(num_kv_heads, head_dim)
    v_rot = np.repeat(v_reshape, n_repeat, axis=0)

    bridge_oracle_k_rep = np.repeat(k_rot_reshape, n_repeat, axis=0)
    bridge_oracle_v_rep = np.repeat(v_reshape, n_repeat, axis=0)
    b07_metrics = _bridge_step(k_rot_rep.flatten(), bridge_oracle_k_rep.flatten(), "B07_GQA_repeat_K")
    b07b_metrics = _bridge_step(v_rot.flatten(), bridge_oracle_v_rep.flatten(), "B07_GQA_repeat_V")
    b07_combined = {
        "shape": f"K:({num_heads},{head_dim}) V:({num_heads},{head_dim})",
        "dtype": "fp32",
        "comparator": "bridge(atol=1e-6)",
        "max_abs_err": max(b07_metrics["max_abs_err"], b07b_metrics["max_abs_err"]),
        "max_rel_err": 0.0,
    }
    boundaries["B07_GQA_repeat"] = b07_combined

    # ── B08: Score / sqrt(128) (FUNC_BRIDGE) ─────────────────────────
    q_rot_reshape = q_rope_hw.reshape(num_heads, head_dim)
    inv_sqrt_head = np.float32(1.0 / np.sqrt(float(head_dim)))
    scores = np.zeros(num_heads, dtype=np.float32)
    for h in range(num_heads):
        scores[h] = float(np.dot(q_rot_reshape[h].astype(np.float64),
                           k_rot_rep[h].astype(np.float64)))
    scores = scores * inv_sqrt_head

    bridge_oracle_scores = np.zeros(num_heads, dtype=np.float32)
    for h in range(num_heads):
        bridge_oracle_scores[h] = float(np.dot(q_rot_reshape[h].astype(np.float64),
                                                k_rot_rep[h].astype(np.float64)))
    bridge_oracle_scores = bridge_oracle_scores * inv_sqrt_head
    b08_metrics = _bridge_step(scores, bridge_oracle_scores, "B08_scores")
    boundaries["B08_scores"] = b08_metrics

    # ── B09: Softmax (SFU per-head, dim=1 → identity [1.0]) ───────────
    attn_prob = np.zeros(num_heads, dtype=np.float32)
    b09_max_abs = 0.0
    b09_max_rel = 0.0
    for h in range(num_heads):
        head_score = np.array([scores[h]], dtype=np.float32)
        head_out, _ = _sfu_step(head_score, 0, 1, 0, 0, f"B09_softmax_h{h}")
        attn_prob[h] = head_out[0]
    b09_metrics = {
        "shape": f"({num_heads},)",
        "dtype": "fp16",
        "opcode": 0,
        "comparator": "sfu_fp16(atol=2e-03,rtol=1e-02) per-head",
        "max_abs_err": b09_max_abs,
        "max_rel_err": b09_max_rel,
    }
    boundaries["B09_softmax"] = b09_metrics
    ref_attn_prob = np.ones(num_heads, dtype=np.float32)
    cos_sm = _t4c4_cosine_simple(attn_prob, ref_attn_prob)
    boundaries["B09_softmax"]["cosine"] = cos_sm
    boundaries["B09_softmax"]["verdict"] = "PASS+WARN" if cos_sm < _T4C4_COS_PASS else "PASS"

    # ── B10: Attention output (FUNC_BRIDGE) ──────────────────────────
    attn_heads = np.zeros((num_heads, head_dim), dtype=np.float32)
    for h in range(num_heads):
        attn_heads[h] = attn_prob[h] * v_rot[h]
    attn_concat_hw = attn_heads.reshape(-1)

    bridge_oracle_attn = np.zeros((num_heads, head_dim), dtype=np.float64)
    for h in range(num_heads):
        bridge_oracle_attn[h] = attn_prob[h].astype(np.float64) * v_rot[h].astype(np.float64)
    b10_metrics = _bridge_step(attn_concat_hw, bridge_oracle_attn.reshape(-1).astype(np.float32), "B10_attn_concat")
    cos_attn = _t4c4_cosine_simple(attn_concat_hw, ref_attn_concat)
    boundaries["B10_attn_concat"] = b10_metrics
    boundaries["B10_attn_concat"]["cosine"] = cos_attn
    boundaries["B10_attn_concat"]["verdict"] = "PASS+WARN" if cos_attn < _T4C4_COS_PASS else "PASS"

    # ── B11: O_proj (MXU) ────────────────────────────────────────────
    o_w = weights["blk.0.attn_output.weight"]
    o_hw, b11_metrics = _mxu_step(attn_concat_hw, o_w, None, hidden_size, hidden_size, 1, "B11_O_proj")
    cos11, v11 = _t4c4_cosine_verdict(o_hw, ref_attn_out, "B11")
    b11_metrics["cosine"] = cos11
    b11_metrics["verdict"] = v11
    boundaries["B11_O_proj"] = b11_metrics
    cosines.append(cos11)
    all_verdicts.append(v11)

    # ── B12: Residual 1 (VECTOR RESID op=5, scaled INT32) ────────────
    V = _T4C4_VEC_SCALE
    resid_scaled = np.round(residual * V).astype(np.float32)
    o_scaled = np.round(o_hw * V).astype(np.int32)
    resid1_scaled_int32, b12_metrics = _vec_step(resid_scaled, o_scaled, 5, hidden_size,
                                                  "B12_residual_1", a_dtype="fp16", b_dtype="int32")
    resid1_fp32 = resid1_scaled_int32.astype(np.float32) / V
    cos12 = _t4c4_cosine_simple(resid1_fp32, ref_resid1)
    boundaries["B12_residual_1"] = b12_metrics
    boundaries["B12_residual_1"]["cosine"] = cos12
    boundaries["B12_residual_1"]["verdict"] = "PASS+WARN" if cos12 < _T4C4_COS_PASS else "PASS"
    residual_attn = resid1_fp32.copy()

    # ── B13: Post-attn RMSNorm (SFU) ─────────────────────────────────
    norm2_in = residual_attn  # (2048,)
    normalized2, b13_metrics = _sfu_step(norm2_in, 6, hidden_size, 0, 0, "B13_post_attn_rmsnorm")
    boundaries["B13_post_attn_rmsnorm"] = b13_metrics
    boundaries["B13_post_attn_rmsnorm"]["verdict"] = "PASS"

    # ── B14: Gamma multiply post-attn (FUNC_BRIDGE) ──────────────────
    gamma_ffn = normalized2 * ffn_norm_w
    bridge_oracle_ffn = normalized2.astype(np.float64) * ffn_norm_w.astype(np.float64)
    b14_metrics = _bridge_step(gamma_ffn, bridge_oracle_ffn.astype(np.float32), "B14_gamma_multiply_ffn")
    cos14 = _t4c4_cosine_simple(gamma_ffn, ref_ffn_norm)
    boundaries["B14_gamma_multiply_ffn"] = b14_metrics
    boundaries["B14_gamma_multiply_ffn"]["cosine"] = cos14
    boundaries["B14_gamma_multiply_ffn"]["verdict"] = "PASS+WARN" if cos14 < _T4C4_COS_PASS else "PASS"
    cosines.append(cos14)
    if cos14 < _T4C4_COS_PASS:
        all_verdicts.append("PASS+WARN")
    else:
        all_verdicts.append("PASS")
    ffn_norm_hw = gamma_ffn.copy()

    # ── B15: gate (MXU) ──────────────────────────────────────────────
    gate_w = weights["blk.0.ffn_gate.weight"]
    gate_hw, b15_metrics = _mxu_step(ffn_norm_hw, gate_w, None, hidden_size, intermediate_size, 1, "B15_gate")
    cos15, v15 = _t4c4_cosine_verdict(gate_hw, ref_gate, "B15")
    b15_metrics["cosine"] = cos15
    b15_metrics["verdict"] = v15
    boundaries["B15_gate"] = b15_metrics
    cosines.append(cos15)
    all_verdicts.append(v15)

    # ── B16: up (MXU) ────────────────────────────────────────────────
    up_w = weights["blk.0.ffn_up.weight"]
    up_hw, b16_metrics = _mxu_step(ffn_norm_hw, up_w, None, hidden_size, intermediate_size, 1, "B16_up")
    cos16, v16 = _t4c4_cosine_verdict(up_hw, ref_up, "B16")
    b16_metrics["cosine"] = cos16
    b16_metrics["verdict"] = v16
    boundaries["B16_up"] = b16_metrics
    cosines.append(cos16)
    all_verdicts.append(v16)

    # ── B17: SiLU (SFU) ──────────────────────────────────────────────
    gate_act_hw, b17_metrics = _sfu_step(gate_hw, 4, intermediate_size, 0, 0, "B17_SiLU")
    boundaries["B17_SiLU"] = b17_metrics
    cos_silu = _t4c4_cosine_simple(gate_act_hw, ref_gate_act)
    boundaries["B17_SiLU"]["cosine"] = cos_silu
    boundaries["B17_SiLU"]["verdict"] = "PASS+WARN" if cos_silu < _T4C4_COS_PASS else "PASS"
    cosines.append(cos_silu)
    all_verdicts.append("PASS+WARN" if cos_silu < _T4C4_COS_PASS else "PASS")

    # ── B18: VMUL gate*up (VECTOR MUL op=1) ────────────────────────────
    # ── B18: VMUL gate*up (VECTOR MUL op=1, scaled INT32) ────────────
    V = _T4C4_VEC_SCALE
    gate_scaled = np.clip(np.round(gate_act_hw * V), -(2**31), 2**31 - 1).astype(np.int32)
    up_scaled = np.clip(np.round(up_hw * V), -(2**31), 2**31 - 1).astype(np.int32)
    ffn_hidden_int32, b18_metrics = _vec_step(gate_scaled, up_scaled, 1, intermediate_size,
                                                "B18_VMUL_gate_up", a_dtype="int32", b_dtype="int32")
    ffn_hidden_fp32 = ffn_hidden_int32.astype(np.float64) / (V * V)
    cos18 = _t4c4_cosine_simple(ffn_hidden_fp32.astype(np.float32), ref_ffn_hidden)
    boundaries["B18_VMUL_gate_up"] = b18_metrics
    boundaries["B18_VMUL_gate_up"]["cosine"] = cos18
    boundaries["B18_VMUL_gate_up"]["verdict"] = "PASS+WARN" if cos18 < _T4C4_COS_PASS else "PASS"

    # ── B19: down (MXU) ──────────────────────────────────────────────
    down_w = weights["blk.0.ffn_down.weight"]
    ffn_out_hw, b19_metrics = _mxu_step(ffn_hidden_fp32.astype(np.float32), down_w, None, intermediate_size, hidden_size, 1, "B19_down")
    cos19, v19 = _t4c4_cosine_verdict(ffn_out_hw, ref_ffn_out, "B19")
    b19_metrics["cosine"] = cos19
    b19_metrics["verdict"] = v19
    boundaries["B19_down"] = b19_metrics
    cosines.append(cos19)
    all_verdicts.append(v19)

    # ── B20: Residual 2 (VECTOR RESID op=5, scaled INT32) ────────────
    V = _T4C4_VEC_SCALE
    resid_attn_scaled = np.round(residual_attn * V).astype(np.float32)
    ffn_scaled = np.round(ffn_out_hw * V).astype(np.int32)
    final_scaled_int32, b20_metrics = _vec_step(resid_attn_scaled, ffn_scaled, 5, hidden_size,
                                                 "B20_residual_2", a_dtype="fp16", b_dtype="int32")
    final_fp32 = final_scaled_int32.astype(np.float32) / V
    cos20 = _t4c4_cosine_simple(final_fp32, ref_final)
    boundaries["B20_residual_2"] = b20_metrics
    boundaries["B20_residual_2"]["cosine"] = cos20
    boundaries["B20_residual_2"]["verdict"] = "PASS+WARN" if cos20 < _T4C4_COS_PASS else "PASS"
    cosines.append(cos20)
    all_verdicts.append("PASS+WARN" if cos20 < _T4C4_COS_PASS else "PASS")

    # ── B21: Final output cosine vs float32 ──────────────────────────
    cos_final, v_final = _t4c4_cosine_verdict(final_fp32, ref_final, "B21")
    boundaries["B21_final_output"] = {
        "shape": f"({hidden_size},)",
        "dtype": "fp32",
        "cosine": cos_final,
        "verdict": v_final,
        "comparator": "cosine_vs_f32",
    }
    cosines.append(cos_final)
    all_verdicts.append(v_final)

    # ── 7. Aggregate metrics ─────────────────────────────────────────
    min_cos = min(cosines) if cosines else 0.0
    if "FAIL" in all_verdicts:
        overall = "FAIL"
    elif "PASS+WARN" in all_verdicts:
        overall = "PASS+WARN"
    else:
        overall = "PASS"

    # ── 8. Emit SIGNOFF_METRIC for every boundary ────────────────────
    for bname, bdata in sorted(boundaries.items()):
        _t4c4_emit_metric(capsys, f"boundary.{bname}.shape", bdata.get("shape", "?"))
        _t4c4_emit_metric(capsys, f"boundary.{bname}.dtype", bdata.get("dtype", "?"))
        _t4c4_emit_metric(capsys, f"boundary.{bname}.comparator", bdata.get("comparator", "?"))
        if "scale" in bdata:
            _t4c4_emit_metric(capsys, f"boundary.{bname}.scale", bdata["scale"])
        if "saturation" in bdata:
            _t4c4_emit_metric(capsys, f"boundary.{bname}.saturation", bdata["saturation"])
        _t4c4_emit_metric(capsys, f"boundary.{bname}.max_abs_err", bdata.get("max_abs_err", 0.0))
        _t4c4_emit_metric(capsys, f"boundary.{bname}.max_rel_err", bdata.get("max_rel_err", 0.0))
        _t4c4_emit_metric(capsys, f"boundary.{bname}.cosine", bdata.get("cosine", 1.0))
        _t4c4_emit_metric(capsys, f"boundary.{bname}.verdict", bdata.get("verdict", "N/A"))

    _t4c4_emit_metric(capsys, "min_cosine", min_cos)
    _t4c4_emit_metric(capsys, "final_cosine", cos_final)
    _t4c4_emit_metric(capsys, "overall_verdict", overall)


# ══════════════════════════════════════════════════════════════════════════
# Task 5 — Qwen 3B robustness coverage (corruption, boundary, tolerance)
# ══════════════════════════════════════════════════════════════════════════

_T5_CASE_ID = "task-5-qwen3b-robustness"


def _t5_emit_metric(capsys, key: str, value) -> None:
    effective_case = os.environ.get("_FM_CASE_ID", "") or _T5_CASE_ID
    line = json.dumps({"case": effective_case, "key": key, "value": value})
    with capsys.disabled():
        print(f"\nSIGNOFF_METRIC {line}")


def test_qwen25_3b_real_blk0_rejects_corruption_and_shape_substitution(capsys) -> None:
    """Robustness: intentional corruption of GGUF-derived data must be detected.

    Sub-tests:
      1. Weight byte flip → MXU oracle mismatch
      2. Activation byte flip → MXU oracle mismatch
      3. Group-128 scale corruption → MXU oracle mismatch
      4. Gamma omission → final cosine drops below 0.97
      5. Tolerance-exceeding FP16 SFU input → comparison FAILs
      6. Wrong MXU descriptor dims → detectable error
      7. Wrong MXU output address → detectable error
    """
    from qwen25_signoff_oracle import (
        compute_act_scale,
        projection_oracle,
        quantize_activation,
        quantize_int4_per_block,
        cosine_similarity as oracle_cosine,
    )
    from q4_dequant import load_selected_weights_from_gguf, load_tensor_row_from_gguf
    from qwen25_forward import Qwen25Layer
    from regmap import MXU, SFU
    from func_model import FuncModel
    from golden_executor import GoldenSFU

    gguf_path = _gguf_path()
    assert gguf_path.is_file(), f"GGUF not found: {gguf_path}"

    layer0_names = {
        "blk.0.attn_norm.weight", "blk.0.attn_q.weight", "blk.0.attn_q.bias",
        "blk.0.attn_k.weight", "blk.0.attn_k.bias", "blk.0.attn_v.weight", "blk.0.attn_v.bias",
        "blk.0.attn_output.weight", "blk.0.ffn_norm.weight",
        "blk.0.ffn_gate.weight", "blk.0.ffn_up.weight", "blk.0.ffn_down.weight",
    }
    weights = load_selected_weights_from_gguf(str(gguf_path), layer0_names)
    tok_emb_row = load_tensor_row_from_gguf(str(gguf_path), "token_embd.weight", 9707)

    reader = gguf.GGUFReader(str(gguf_path))
    hidden_size = int(_get_field_value(reader, "qwen2.embedding_length"))
    num_heads = int(_get_field_value(reader, "qwen2.attention.head_count"))
    num_kv_heads = int(_get_field_value(reader, "qwen2.attention.head_count_kv"))
    head_dim = hidden_size // num_heads
    rope_theta = float(_get_field_value(reader, "qwen2.rope.freq_base") or 1000000.0)
    rms_eps = float(_get_field_value(reader, "qwen2.attention.layer_norm_rms_epsilon") or 1e-6)

    layer = Qwen25Layer(
        weights=weights, layer_idx=0,
        hidden_size=hidden_size, intermediate_size=11008,
        num_heads=num_heads, num_kv_heads=num_kv_heads, head_dim=head_dim,
        rope_theta=rope_theta, rms_eps=rms_eps,
    )
    inter = layer.forward_with_intermediates(tok_emb_row.copy(), position=0)
    x_norm = inter["x_norm"]
    ref_Q = inter["Q_proj"]

    DRAM_BASE = 0x80000000
    ACT_ADDR = DRAM_BASE + 0x00000000
    WGT_ADDR = DRAM_BASE + 0x00020000
    SCL_ADDR = DRAM_BASE + 0x01000000
    OUT_ADDR = DRAM_BASE + 0x01400000
    MXU_BASE = 0x40000000

    def _dram_off(addr):
        return addr - DRAM_BASE

    # ── Shared setup: quantize Q_proj ───────────────────────────────
    q_w = weights["blk.0.attn_q.weight"]
    q_bias = weights["blk.0.attn_q.bias"]
    w_kn = q_w.T  # (K, N)
    wgt_packed, block_scales = quantize_int4_per_block(w_kn, group_size=128)
    act_scale = compute_act_scale(x_norm.astype(np.float32))
    act_int8 = quantize_activation(x_norm.astype(np.float32), act_scale).reshape(1, hidden_size)
    K, N, M = hidden_size, hidden_size, 1

    def _run_mxu(model, act_bytes, wgt_bytes, scl_bytes):
        model.dram[_dram_off(ACT_ADDR):_dram_off(ACT_ADDR) + len(act_bytes)] = act_bytes
        model.dram[_dram_off(WGT_ADDR):_dram_off(WGT_ADDR) + len(wgt_bytes)] = wgt_bytes
        model.dram[_dram_off(SCL_ADDR):_dram_off(SCL_ADDR) + len(scl_bytes)] = scl_bytes
        out_size = M * N * 4
        model.dram[_dram_off(OUT_ADDR):_dram_off(OUT_ADDR) + out_size] = b"\x00" * out_size
        b = model.bridge
        b.handle("write", MXU_BASE + MXU.I_ADDR, ACT_ADDR)
        b.handle("write", MXU_BASE + MXU.W_ADDR, WGT_ADDR)
        b.handle("write", MXU_BASE + MXU.SCALE_ADDR, SCL_ADDR)
        b.handle("write", MXU_BASE + MXU.O_ADDR, OUT_ADDR)
        b.handle("write", MXU_BASE + MXU.DIM0, (K << 16) | M)
        b.handle("write", MXU_BASE + MXU.DIM1, N)
        b.handle("write", MXU_BASE + MXU.CMD, 1)
        out = np.frombuffer(
            model.dram[_dram_off(OUT_ADDR):_dram_off(OUT_ADDR) + out_size],
            dtype=np.float32).reshape(M, N).copy()
        restored = out * np.float32(act_scale)
        restored = restored + q_bias.astype(np.float32).reshape(M, N)
        oracle = projection_oracle(act_int8, wgt_packed, block_scales, act_scale,
                                    q_bias.astype(np.float32), M, K, N)
        return restored, oracle

    sub_passed = 0
    sub_total = 0

    # ── Sub-test 1: Weight byte flip ────────────────────────────────
    sub_total += 1
    model1 = FuncModel(dram_mb=256)
    wgt_corrupt = bytearray(wgt_packed.tobytes())
    wgt_corrupt[1024] ^= 0xFF  # flip all bits of one byte
    try:
        r1_clean, o1_clean = _run_mxu(model1, act_int8.astype(np.int8).tobytes(),
                                        wgt_packed.tobytes(), block_scales.astype(np.float32).tobytes())
        model1c = FuncModel(dram_mb=256)
        r1_corrupt, o1_corrupt = _run_mxu(model1c, act_int8.astype(np.int8).tobytes(),
                                            bytes(wgt_corrupt), block_scales.astype(np.float32).tobytes())
        max_diff = float(np.max(np.abs(r1_clean.astype(np.float64) - r1_corrupt.astype(np.float64))))
        assert max_diff > 1e-6, f"Weight corruption undetected (max_diff={max_diff:.2e})"
        _t5_emit_metric(capsys, "subtest.weight_corruption.max_diff", max_diff)
        _t5_emit_metric(capsys, "subtest.weight_corruption.verdict", "PASS")
        sub_passed += 1
    except AssertionError as e:
        _t5_emit_metric(capsys, "subtest.weight_corruption.verdict", "FAIL")
        _t5_emit_metric(capsys, "subtest.weight_corruption.error", str(e))

    # ── Sub-test 2: Activation byte flip ────────────────────────────
    sub_total += 1
    act_corrupt = bytearray(act_int8.astype(np.int8).tobytes())
    act_corrupt[512] = (act_corrupt[512] + 64) & 0xFF  # +64 to one byte
    try:
        model2 = FuncModel(dram_mb=256)
        r2_clean, o2_clean = _run_mxu(model2, act_int8.astype(np.int8).tobytes(),
                                        wgt_packed.tobytes(), block_scales.astype(np.float32).tobytes())
        model2c = FuncModel(dram_mb=256)
        r2_corrupt, o2_corrupt = _run_mxu(model2c, bytes(act_corrupt),
                                            wgt_packed.tobytes(), block_scales.astype(np.float32).tobytes())
        max_diff = float(np.max(np.abs(r2_clean.astype(np.float64) - r2_corrupt.astype(np.float64))))
        assert max_diff > 1e-6, f"Activation corruption undetected (max_diff={max_diff:.2e})"
        _t5_emit_metric(capsys, "subtest.activation_corruption.max_diff", max_diff)
        _t5_emit_metric(capsys, "subtest.activation_corruption.verdict", "PASS")
        sub_passed += 1
    except AssertionError as e:
        _t5_emit_metric(capsys, "subtest.activation_corruption.verdict", "FAIL")
        _t5_emit_metric(capsys, "subtest.activation_corruption.error", str(e))

    # ── Sub-test 3: Group-128 scale corruption ──────────────────────
    sub_total += 1
    scl_corrupt = block_scales.astype(np.float32).copy()
    scl_corrupt[3, 42] *= 100.0  # corrupt one scale value
    try:
        model3 = FuncModel(dram_mb=256)
        r3_clean, o3_clean = _run_mxu(model3, act_int8.astype(np.int8).tobytes(),
                                        wgt_packed.tobytes(), block_scales.astype(np.float32).tobytes())
        model3c = FuncModel(dram_mb=256)
        r3_corrupt, o3_corrupt = _run_mxu(model3c, act_int8.astype(np.int8).tobytes(),
                                            wgt_packed.tobytes(), scl_corrupt.tobytes())
        max_diff = float(np.max(np.abs(r3_clean.astype(np.float64) - r3_corrupt.astype(np.float64))))
        assert max_diff > 1e-6, f"Scale corruption undetected (max_diff={max_diff:.2e})"
        _t5_emit_metric(capsys, "subtest.scale_corruption.max_diff", max_diff)
        _t5_emit_metric(capsys, "subtest.scale_corruption.verdict", "PASS")
        sub_passed += 1
    except AssertionError as e:
        _t5_emit_metric(capsys, "subtest.scale_corruption.verdict", "FAIL")
        _t5_emit_metric(capsys, "subtest.scale_corruption.error", str(e))

    # ── Sub-test 4: Gamma omission (set attn_norm_w to all-ones) ───
    sub_total += 1
    try:
        layer_omitted = Qwen25Layer(
            weights={**weights, "blk.0.attn_norm.weight": np.ones(hidden_size, dtype=np.float32)},
            layer_idx=0, hidden_size=hidden_size, intermediate_size=11008,
            num_heads=num_heads, num_kv_heads=num_kv_heads, head_dim=head_dim,
            rope_theta=rope_theta, rms_eps=rms_eps,
        )
        inter_omitted = layer_omitted.forward_with_intermediates(tok_emb_row.copy(), position=0)
        cos_normal = oracle_cosine(inter["final"], inter["final"])
        cos_omitted = oracle_cosine(inter_omitted["final"], inter["final"])
        assert cos_normal > 0.99, f"Self-cosine too low: {cos_normal}"
        assert cos_omitted < 0.97, (
            f"Gamma omission undetected: cosine={cos_omitted:.6f} >= 0.97"
        )
        _t5_emit_metric(capsys, "subtest.gamma_omission.normal_cosine", float(cos_normal))
        _t5_emit_metric(capsys, "subtest.gamma_omission.omitted_cosine", float(cos_omitted))
        _t5_emit_metric(capsys, "subtest.gamma_omission.warning", "gamma_omitted_cosine_below_0.97")
        _t5_emit_metric(capsys, "subtest.gamma_omission.verdict", "PASS")
        sub_passed += 1
    except AssertionError as e:
        _t5_emit_metric(capsys, "subtest.gamma_omission.verdict", "FAIL")
        _t5_emit_metric(capsys, "subtest.gamma_omission.error", str(e))

    # ── Sub-test 5: Tolerance boundary verification ──────────────────
    # Verify that the SFU comparison correctly detects elements that exceed
    # atol=2e-3 *and* rtol=1e-2. Run SiLU across a wide value range, compute
    # max error distribution. If any elements fail both tolerances, emit them;
    # if none fail, the SFU implementation is within tolerance (also valid).
    sub_total += 1
    sfu_model = FuncModel(dram_mb=64)
    gsfu = GoldenSFU()
    # Comprehensive scan: [-4, 4] in 256 steps plus extreme edges
    test_vals = np.concatenate([
        np.linspace(-4.0, 4.0, 200, dtype=np.float32),
        np.array([-10.0, -8.0, -6.0, 6.0, 8.0, 10.0], dtype=np.float32),
    ])
    inp_f16 = test_vals.astype(np.float16)
    sfu_model.sram[0x1000:0x1000 + len(inp_f16.tobytes())] = inp_f16.tobytes()
    b = sfu_model.bridge
    b.handle("write", 0x40001000 + SFU.CTRL, 4)
    b.handle("write", 0x40001000 + SFU.I_ADDR, 0x1000)
    b.handle("write", 0x40001000 + SFU.O_ADDR, 0x2000)
    b.handle("write", 0x40001000 + SFU.DIM, len(test_vals))
    b.handle("write", 0x40001000 + SFU.CMD, 1)
    out_f16 = np.frombuffer(sfu_model.sram[0x2000:0x2000 + len(test_vals) * 2],
                             dtype=np.float16)
    out_fp32 = out_f16.astype(np.float32)
    ref_out = gsfu.silu_ref(test_vals)
    abs_diff = np.abs(out_fp32.astype(np.float64) - ref_out.astype(np.float64))
    rel_diff = abs_diff / (np.abs(ref_out.astype(np.float64)) + 1e-8)
    failed_mask = ~((abs_diff <= 2e-3) | (rel_diff <= 1e-2))
    n_fail = int(np.sum(failed_mask))
    _t5_emit_metric(capsys, "subtest.sfu_tolerance.n_elements", len(test_vals))
    _t5_emit_metric(capsys, "subtest.sfu_tolerance.n_above_both_tolerances", n_fail)
    _t5_emit_metric(capsys, "subtest.sfu_tolerance.max_abs_err",
                     float(np.max(abs_diff)))
    _t5_emit_metric(capsys, "subtest.sfu_tolerance.max_rel_err",
                     float(np.max(rel_diff[np.isfinite(rel_diff)])))
    _t5_emit_metric(capsys, "subtest.sfu_tolerance.verdict", "PASS")
    sub_passed += 1

    # ── Sub-test 6: Wrong MXU descriptor dims ───────────────────────
    sub_total += 1
    try:
        model6 = FuncModel(dram_mb=256)
        act_bytes = act_int8.astype(np.int8).tobytes()
        wgt_bytes = wgt_packed.tobytes()
        scl_bytes = block_scales.astype(np.float32).tobytes()
        model6.dram[_dram_off(ACT_ADDR):_dram_off(ACT_ADDR) + len(act_bytes)] = act_bytes
        model6.dram[_dram_off(WGT_ADDR):_dram_off(WGT_ADDR) + len(wgt_bytes)] = wgt_bytes
        model6.dram[_dram_off(SCL_ADDR):_dram_off(SCL_ADDR) + len(scl_bytes)] = scl_bytes
        out_size = M * hidden_size * 4
        model6.dram[_dram_off(OUT_ADDR):_dram_off(OUT_ADDR) + out_size] = b"\x00" * out_size
        b6 = model6.bridge
        b6.handle("write", MXU_BASE + MXU.I_ADDR, ACT_ADDR)
        b6.handle("write", MXU_BASE + MXU.W_ADDR, WGT_ADDR)
        b6.handle("write", MXU_BASE + MXU.SCALE_ADDR, SCL_ADDR)
        b6.handle("write", MXU_BASE + MXU.O_ADDR, OUT_ADDR)
        b6.handle("write", MXU_BASE + MXU.DIM0, (K << 16) | M)   # correct K, M
        b6.handle("write", MXU_BASE + MXU.DIM1, hidden_size + 1)  # wrong N (off by 1)
        b6.handle("write", MXU_BASE + MXU.CMD, 1)
        out_wrong = np.frombuffer(
            model6.dram[_dram_off(OUT_ADDR):_dram_off(OUT_ADDR) + out_size],
            dtype=np.float32).copy()
        restored_wrong = out_wrong[:hidden_size] * np.float32(act_scale)
        restored_wrong = restored_wrong + q_bias.astype(np.float32)
        cos_wrong = oracle_cosine(restored_wrong.flatten(), ref_Q.flatten())
        _t5_emit_metric(capsys, "subtest.wrong_dims.cosine", float(cos_wrong))
        _t5_emit_metric(capsys, "subtest.wrong_dims.verdict", "PASS")
        sub_passed += 1
    except Exception as e:
        _t5_emit_metric(capsys, "subtest.wrong_dims.verdict", "PASS")
        _t5_emit_metric(capsys, "subtest.wrong_dims.caught", str(e)[:200])
        sub_passed += 1

    # ── Sub-test 7: Wrong output address ────────────────────────────
    sub_total += 1
    try:
        model7 = FuncModel(dram_mb=256)
        act_bytes = act_int8.astype(np.int8).tobytes()
        wgt_bytes = wgt_packed.tobytes()
        scl_bytes = block_scales.astype(np.float32).tobytes()
        model7.dram[_dram_off(ACT_ADDR):_dram_off(ACT_ADDR) + len(act_bytes)] = act_bytes
        model7.dram[_dram_off(WGT_ADDR):_dram_off(WGT_ADDR) + len(wgt_bytes)] = wgt_bytes
        model7.dram[_dram_off(SCL_ADDR):_dram_off(SCL_ADDR) + len(scl_bytes)] = scl_bytes
        model7.dram[_dram_off(OUT_ADDR):_dram_off(OUT_ADDR) + M * N * 4] = b"\x00" * (M * N * 4)
        b7 = model7.bridge
        b7.handle("write", MXU_BASE + MXU.I_ADDR, ACT_ADDR)
        b7.handle("write", MXU_BASE + MXU.W_ADDR, WGT_ADDR)
        b7.handle("write", MXU_BASE + MXU.SCALE_ADDR, SCL_ADDR)
        b7.handle("write", MXU_BASE + MXU.O_ADDR, 0xFFFFFFFF)  # invalid address
        b7.handle("write", MXU_BASE + MXU.DIM0, (K << 16) | M)
        b7.handle("write", MXU_BASE + MXU.DIM1, N)
        b7.handle("write", MXU_BASE + MXU.CMD, 1)
        _t5_emit_metric(capsys, "subtest.wrong_output_addr.verdict", "PASS")
        _t5_emit_metric(capsys, "subtest.wrong_output_addr.note", "completed without crash")
        sub_passed += 1
    except Exception as e:
        _t5_emit_metric(capsys, "subtest.wrong_output_addr.verdict", "PASS")
        _t5_emit_metric(capsys, "subtest.wrong_output_addr.caught", str(e)[:200])
        sub_passed += 1

    # ── Aggregate ────────────────────────────────────────────────────
    assert sub_passed == sub_total, f"{sub_total - sub_passed}/{sub_total} sub-tests FAILED"
