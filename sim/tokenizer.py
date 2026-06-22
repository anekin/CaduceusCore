#!/usr/bin/env python3
"""Qwen2.5 tokenizer + GGUF embedding lookup for CaduceusCore forward pass.

This module builds a BPE tokenizer directly from the GGUF file's stored
vocabulary and merges so that tokenization matches llama.cpp exactly.  It also
performs a minimal GGUF read of only the ``token_embd.weight`` tensor and
dequantizes it to float32 so callers can look up embeddings for arbitrary token
IDs.
"""

import argparse
import os
import sys
from typing import List

import numpy as np

# Allow importing q4_dequant.py helpers from ggml-npu when PYTHONPATH=sim.
_GGML_NPU_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "ggml-npu"))
if _GGML_NPU_DIR not in sys.path:
    sys.path.insert(0, _GGML_NPU_DIR)

import gguf
from q4_dequant import dequantize_q4_k, dequantize_q6_k, fp16_to_fp32

# Cache: gguf_path -> tokenizers.Tokenizer
_tokenizer_cache = {}


def _load_tokenizer(gguf_path: str):
    """Build a BPE tokenizer from the vocab and merges stored in ``gguf_path``."""
    if gguf_path in _tokenizer_cache:
        return _tokenizer_cache[gguf_path]

    reader = gguf.GGUFReader(gguf_path)
    fields = reader.fields

    tokens = []
    for part in fields["tokenizer.ggml.tokens"].parts[4:]:
        if part.dtype == np.uint8:
            tokens.append(bytes(part).decode("utf-8", errors="ignore"))

    merges = []
    for part in fields["tokenizer.ggml.merges"].parts[4:]:
        if part.dtype == np.uint8:
            merges.append(bytes(part).decode("utf-8", errors="ignore"))

    vocab = {t: i for i, t in enumerate(tokens)}
    bpe_merges = [tuple(m.split(" ")) for m in merges]

    from tokenizers import Tokenizer, models, pre_tokenizers, decoders

    bpe = models.BPE(vocab=vocab, merges=bpe_merges, unk_token=None)
    tok = Tokenizer(bpe)
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tok.decoder = decoders.ByteLevel()

    _tokenizer_cache[gguf_path] = tok
    return tok


def tokenize(text: str, gguf_path: str = None) -> List[int]:
    """Return Qwen2.5 token IDs for ``text`` matching llama.cpp.

    Args:
        text: Prompt to tokenize.
        gguf_path: Path to a Qwen2.5 GGUF file.  If omitted, the environment
            variable ``QWEN_GGUF`` or the default model path is used.
    """
    if gguf_path is None:
        gguf_path = os.environ.get(
            "QWEN_GGUF",
            os.path.expanduser("~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"),
        )
    tok = _load_tokenizer(gguf_path)
    return tok.encode(text).ids


def _load_token_embd(gguf_path: str) -> np.ndarray:
    """Load and dequantize ``token_embd.weight`` from a GGUF file.

    Returns a float32 array of shape ``[vocab_size, hidden_dim]``.
    """
    import gguf

    reader = gguf.GGUFReader(gguf_path)
    tensor = None
    for t in reader.tensors:
        if t.name == "token_embd.weight":
            tensor = t
            break
    if tensor is None:
        raise ValueError(f"token_embd.weight not found in {gguf_path}")

    raw = bytes(tensor.data.tobytes()) if hasattr(tensor.data, "tobytes") else bytes(tensor.data)
    tt = tensor.tensor_type.name

    if tt == "Q4_K":
        w = dequantize_q4_k(raw)
    elif tt == "Q6_K":
        w = dequantize_q6_k(raw)
    elif tt == "F32":
        w = np.frombuffer(raw, dtype=np.float32).copy()
    elif tt == "F16":
        w = fp16_to_fp32(np.frombuffer(raw, dtype=np.uint16))
    else:
        raise ValueError(f"Unsupported token_embd.weight tensor type: {tt}")

    if len(tensor.shape) != 2:
        raise ValueError(f"Unexpected token_embd.weight shape: {tensor.shape}")

    # GGUF stores 2-D weights as [hidden_dim, vocab_size]; row-major reshape in
    # q4_dequant yields [vocab_size, hidden_dim], which is what we want.
    return w.reshape(tensor.shape[1], tensor.shape[0]).astype(np.float32)


def embedding_lookup(token_ids: List[int], gguf_path: str) -> np.ndarray:
    """Return float32 embeddings of shape ``[seq_len, hidden_dim]``.

    Args:
        token_ids: Integer token IDs produced by :func:`tokenize`.
        gguf_path: Path to a Qwen2.5 GGUF file containing ``token_embd.weight``.

    Raises:
        ValueError: If a token ID is outside the embedding table vocabulary.
    """
    emb_table = _load_token_embd(gguf_path)
    token_ids_arr = np.asarray(token_ids, dtype=np.int64)

    if token_ids_arr.size == 0:
        hidden_dim = emb_table.shape[1]
        return np.empty((0, hidden_dim), dtype=np.float32)

    if np.any(token_ids_arr < 0) or np.any(token_ids_arr >= emb_table.shape[0]):
        raise ValueError(
            f"token IDs out of range [0, {emb_table.shape[0]}): {token_ids_arr}"
        )

    return emb_table[token_ids_arr].astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Qwen2.5 tokenizer + GGUF embedding lookup"
    )
    parser.add_argument("--prompt", required=True, help="Input prompt")
    parser.add_argument("--model", required=True, help="Path to Qwen2.5 GGUF file")
    args = parser.parse_args()

    ids = tokenize(args.prompt, args.model)
    emb = embedding_lookup(ids, args.model)
    print(f"token IDs: {ids}")
    print(f"embedding shape: {emb.shape}")


if __name__ == "__main__":
    main()
