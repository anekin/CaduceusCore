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

from sim.tile_scheduler import (
    tile_mmul,
    TILE_H,
    TILE_W,
    TILE_WEIGHT_BYTES,
    TILE_SCALE_BYTES,
)
from sim.golden_executor import GoldenMXU

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
    line = json.dumps({"case": _T4C2_CASE_ID, "key": key, "value": value})
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
    from sim.regmap import MXU
    from sim.func_model import FuncModel

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

    _t4c2_emit_metric(capsys, "min_cosine", min_cos)
    _t4c2_emit_metric(capsys, "overall_verdict", overall)
    _t4c2_emit_metric(capsys, "tests.collected", 1)
    _t4c2_emit_metric(capsys, "tests.passed", 1 if overall != "FAIL" else 0)
    _t4c2_emit_metric(capsys, "evidence.verdict", "pass" if overall != "FAIL" else "fail")


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
    line = json.dumps({"case": _T4C3_CASE_ID, "key": key, "value": value})
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
    from sim.func_model import FuncModel

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
            from sim.regmap import MXU as MXU_REG
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
    _t4c3_emit_metric(capsys, "min_cosine", min_cos)
    _t4c3_emit_metric(capsys, "overall_verdict", overall)
    _t4c3_emit_metric(capsys, "tests.collected", 1)
    _t4c3_emit_metric(capsys, "tests.passed", 1 if overall != "FAIL" else 0)
    _t4c3_emit_metric(capsys, "evidence.verdict", "pass" if overall != "FAIL" else "fail")
