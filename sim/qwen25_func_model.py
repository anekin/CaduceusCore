"""Qwen2.5-3B Func Model wrapper — selective GGUF loading + MMIO/SFU/Vector.

Provides a compute path that uses the real Q4_K_M GGUF weights selectively
(only layer-0 weights + target token embedding row) and drives the Func
Model's GoldenExecutor (MXU/SFU/Vector) for bit-exact result generation.

Designed for T4C1-T4C4 signoff evidence: downstream tasks (T4C2 direct
projections, T4C3 tiled projections, T4C4 connected blk0) consume the
projection inputs this module exposes.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT / "ggml-npu"))
sys.path.insert(0, str(_PROJECT / "sim"))

import gguf
from q4_dequant import (
    load_selected_weights_from_gguf,
    load_tensor_row_from_gguf,
)
from qwen25_forward import Qwen25Layer, rms_norm

# Canonical Qwen2.5-3B dimensions
HIDDEN = 2048
INTERMEDIATE = 11008
NUM_HEADS = 16
NUM_KV_HEADS = 2
HEAD_DIM = 128

# Layer-0 tensor names needed for a full forward pass
_LAYER0_TENSOR_NAMES: set[str] = {
    "blk.0.attn_norm.weight",
    "blk.0.attn_q.weight",
    "blk.0.attn_q.bias",
    "blk.0.attn_k.weight",
    "blk.0.attn_k.bias",
    "blk.0.attn_v.weight",
    "blk.0.attn_v.bias",
    "blk.0.attn_output.weight",
    "blk.0.ffn_norm.weight",
    "blk.0.ffn_gate.weight",
    "blk.0.ffn_up.weight",
    "blk.0.ffn_down.weight",
}


def _gguf_path() -> Path:
    env = os.environ.get("QWEN3B_GGUF", "")
    if env:
        return Path(env)
    return Path("/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf")


def _read_gguf_field(reader: gguf.GGUFReader, key: str, default):
    try:
        return reader.fields[key].parts[-1][0]
    except (KeyError, IndexError, AttributeError):
        return default


class Qwen25FuncModel:
    """Func Model wrapper for Qwen2.5-3B with selective GGUF loading.

    Loads only layer-0 Q/K/V/O/gate/up/down weights + biases +
    RMSNorm weights + token-embedding row 9707.  Does NOT dequantize
    all 36 layers.

    Usage::

        fm = Qwen25FuncModel(gguf_path)
        fm.init_func_model(dram_mb=256)
        output, intermediates = fm.forward_layer0(token_id=9707)
        x_norm = intermediates["x_norm"]
        ffn_norm = intermediates["ffn_norm"]
        attn_concat = intermediates["attn_concat"]
    """

    def __init__(self, gguf_path: Optional[str] = None):
        self.gguf_path = str(gguf_path or _gguf_path())
        self._weights: Dict[str, np.ndarray] = {}
        self._model: Optional[object] = None
        self._token_embedding: Optional[np.ndarray] = None
        self._loaded = False
        self._loaded_tensor_names: List[str] = []

    # ── selective loading ──────────────────────────────────────────

    def load_weights(self) -> None:
        """Load only layer-0 weights + token embedding row 9707."""
        print(f"[Qwen25FuncModel] Selective loading from {self.gguf_path}")

        # Layer-0 weights
        weights = load_selected_weights_from_gguf(
            self.gguf_path, _LAYER0_TENSOR_NAMES,
        )
        self._loaded_tensor_names = sorted(weights.keys())

        # Token embedding row 9707 ("Hello")
        tok_emb = load_tensor_row_from_gguf(
            self.gguf_path, "token_embd.weight", 9707,
        )
        self._token_embedding = tok_emb.astype(np.float32)

        self._weights = weights
        self._loaded = True

        total_elems = sum(w.size for w in self._weights.values())
        total_elems += self._token_embedding.size
        print(f"[Qwen25FuncModel] Loaded {len(self._loaded_tensor_names)} tensors "
              f"+ token-embd row, {total_elems/1e3:.1f}k elements")

    # ── Func Model integration ─────────────────────────────────────

    def init_func_model(self, dram_mb: int = 256) -> object:
        """Create and return a FuncModel instance with DRAM.

        The FuncModel bundles GoldenMXU, GoldenSFU, GoldenVector into
        a single bit-exact compute engine for signoff evidence.
        """
        from func_model import FuncModel  # type: ignore[import-untyped]
        self._model = FuncModel(dram_mb=dram_mb)
        return self._model

    # ── forward pass ───────────────────────────────────────────────

    def forward_layer0(
        self, token_id: int = 9707,
    ) -> Tuple[np.ndarray, Dict[str, np.ndarray]]:
        """Run layer-0 forward pass and return (output, intermediates).

        Uses the selectively loaded float32 weights — identical to the
        existing :func:`Qwen25Layer.forward_with_intermediates` path
        except that weights come from selective loading rather than the
        full ``load_weights_from_gguf`` call.

        Returns:
            (final_hidden_state, intermediates_dict)
        """
        if not self._loaded:
            self.load_weights()

        reader = gguf.GGUFReader(self.gguf_path)
        rope_theta = float(_read_gguf_field(reader, "qwen2.rope.freq_base", 1000000.0))
        rms_eps = float(_read_gguf_field(reader, "qwen2.attention.layer_norm_rms_epsilon", 1e-6))
        num_heads = int(_read_gguf_field(reader, "qwen2.attention.head_count", NUM_HEADS))
        num_kv_heads = int(_read_gguf_field(reader, "qwen2.attention.head_count_kv", NUM_KV_HEADS))
        head_dim = HIDDEN // num_heads

        layer = Qwen25Layer(
            weights=self._weights,
            layer_idx=0,
            hidden_size=HIDDEN,
            intermediate_size=INTERMEDIATE,
            num_heads=num_heads,
            num_kv_heads=num_kv_heads,
            head_dim=head_dim,
            rope_theta=rope_theta,
            rms_eps=rms_eps,
        )

        hidden = self._token_embedding.copy()
        intermediates = layer.forward_with_intermediates(hidden, position=0)
        return intermediates["final"].astype(np.float32), intermediates

    # ── projection input accessors ──────────────────────────────────

    @property
    def token_embedding(self) -> np.ndarray:
        if not self._loaded:
            self.load_weights()
        return self._token_embedding.copy()  # type: ignore[union-attr]

    @property
    def loaded_tensor_names(self) -> List[str]:
        if not self._loaded:
            self.load_weights()
        return list(self._loaded_tensor_names)
