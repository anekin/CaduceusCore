#!/usr/bin/env python3
"""
W1.2: Func Model 3-layer forward pass for Qwen2.5-3B-Instruct-Q4_K_M.

Generates per-layer hidden states as golden .npz vectors for RTL verification.
Compares against llama.cpp reference for validation.

Usage:
    PYTHONPATH=sim python3 scripts/run_qwen25_3b_forward.py --layers 0 1 2
"""

import argparse
import json
import os
import struct
import sys
import time
from pathlib import Path

import numpy as np

# ── Path setup ──────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT / "ggml-npu"))
sys.path.insert(0, str(_PROJECT / "sim"))

from q4_dequant import load_weights_from_gguf  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────
# Qwen2.5-3B canonical parameters (from docs/qwen25-3b-forward-spec.md)
DEFAULT_MODEL_PATH = str(Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf")
FALLBACK_MODEL_PATH = str(Path.home() / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf")

# prompt for llama.cpp reference generation
DEFAULT_PROMPT = "Hello"
DEFAULT_N_TOKENS = 1  # single-token decode for comparison

# output directories
GOLDEN_DIR = _PROJECT / "rtl" / "test_vectors" / "soc_e2e" / "qwen25-3b-3layer"
EVIDENCE_DIR = _PROJECT / "build" / "evidence"
LLAMA_REF_DIR = _PROJECT / "llama_ref" / "refs"


# ══════════════════════════════════════════════════════════════════════
# Qwen2.5 Transformer Operations (float32 reference)
# ══════════════════════════════════════════════════════════════════════

def rms_norm(x: np.ndarray, weight: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    """RMSNorm: x / rms(x) * weight. Matches Qwen2.5 implementation.

    Args:
        x: shape (hidden_size,), float32
        weight: shape (hidden_size,), float32 learnable scale
    Returns:
        shape (hidden_size,), float32
    """
    rms = np.sqrt(np.mean(x.astype(np.float64) ** 2) + eps)
    return (x / rms).astype(np.float32) * weight.astype(np.float32)


def rope_rotate(x: np.ndarray, position: int, theta: float = 1000000.0) -> np.ndarray:
    """Apply RoPE rotation to Q or K tensor.

    Qwen2.5 uses standard RoPE: rotate pairs of dimensions by
    position-dependent angles.

    Args:
        x: shape (num_heads, head_dim) or (num_kv_heads, head_dim), float32
        position: token position (0-based)
        theta: RoPE base frequency (Qwen2.5-3B: 1000000.0)
    Returns:
        rotated tensor, same shape, float32
    """
    num_heads, head_dim = x.shape
    x_out = x.copy()

    # frequency bands: theta^(-2i/d) for i in [0, head_dim/2)
    freqs = 1.0 / (theta ** (np.arange(0, head_dim, 2, dtype=np.float64) / head_dim))
    angles = position * freqs
    cos_vals = np.cos(angles).astype(np.float32)
    sin_vals = np.sin(angles).astype(np.float32)

    for h in range(num_heads):
        for i in range(0, head_dim, 2):
            x0, x1 = float(x_out[h, i]), float(x_out[h, i + 1])
            c, s = float(cos_vals[i // 2]), float(sin_vals[i // 2])
            x_out[h, i] = x0 * c - x1 * s
            x_out[h, i + 1] = x1 * c + x0 * s

    return x_out


def silu(x: np.ndarray) -> np.ndarray:
    """SiLU activation: x * sigmoid(x)."""
    x64 = x.astype(np.float64)
    return (x64 / (1.0 + np.exp(-x64))).astype(np.float32)


def softmax(x: np.ndarray) -> np.ndarray:
    """Numerically stable softmax."""
    x64 = x.astype(np.float64)
    x_max = np.max(x64)
    e_x = np.exp(x64 - x_max)
    return (e_x / np.sum(e_x)).astype(np.float32)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors."""
    a_f = a.astype(np.float64).flatten()
    b_f = b.astype(np.float64).flatten()
    dot = np.dot(a_f, b_f)
    norm_a = np.sqrt(np.dot(a_f, a_f))
    norm_b = np.sqrt(np.dot(b_f, b_f))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return float(dot / (norm_a * norm_b))


# ══════════════════════════════════════════════════════════════════════
# Qwen2.5 Transformer Layer (float32 reference)
# ══════════════════════════════════════════════════════════════════════

class Qwen25Layer:
    """Single Qwen2.5 transformer layer in float32 for golden reference."""

    def __init__(self, weights: dict, layer_idx: int,
                 hidden_size: int, intermediate_size: int,
                 num_heads: int, num_kv_heads: int, head_dim: int,
                 rope_theta: float = 1000000.0, rms_eps: float = 1e-6):
        self.layer_idx = layer_idx
        self.hidden_size = hidden_size
        self.intermediate_size = intermediate_size
        self.num_heads = num_heads
        self.num_kv_heads = num_kv_heads
        self.head_dim = head_dim
        self.rope_theta = rope_theta
        self.rms_eps = rms_eps

        # weight keys
        prefix = f"blk.{layer_idx}."

        # attention weights
        self.attn_norm_w = weights[f"{prefix}attn_norm.weight"]         # (hidden_size,)
        self.q_weight = weights[f"{prefix}attn_q.weight"]               # (hidden_size, q_dim)
        self.k_weight = weights[f"{prefix}attn_k.weight"]               # (hidden_size, k_dim)
        self.v_weight = weights[f"{prefix}attn_v.weight"]               # (hidden_size, v_dim)
        self.o_weight = weights[f"{prefix}attn_output.weight"]          # (hidden_size, hidden_size)

        # FFN weights
        self.ffn_norm_w = weights[f"{prefix}ffn_norm.weight"]           # (hidden_size,)
        self.gate_weight = weights[f"{prefix}ffn_gate.weight"]          # (hidden_size, intermediate_size)
        self.up_weight = weights[f"{prefix}ffn_up.weight"]              # (hidden_size, intermediate_size)
        self.down_weight = weights[f"{prefix}ffn_down.weight"]          # (intermediate_size, hidden_size)

        self.q_bias = weights.get(f"{prefix}attn_q.bias", None)
        self.k_bias = weights.get(f"{prefix}attn_k.bias", None)
        self.v_bias = weights.get(f"{prefix}attn_v.bias", None)

        self.q_dim = self.num_heads * self.head_dim
        self.k_dim = self.num_kv_heads * self.head_dim
        self.v_dim = self.num_kv_heads * self.head_dim

    def forward(self, hidden_states: np.ndarray, position: int = 0) -> np.ndarray:
        """Run a single Qwen2.5 transformer layer forward pass.

        Args:
            hidden_states: (hidden_size,) float32 input
            position: token position for RoPE
        Returns:
            (hidden_size,) float32 output after residual connection
        """
        residual = hidden_states.astype(np.float32).copy()
        x = hidden_states.astype(np.float32)

        # ── Pre-attention RMSNorm ──────────────────────────────────
        x_norm = rms_norm(x, self.attn_norm_w, self.rms_eps)

        # ── Q, K, V projections ────────────────────────────────────
        Q = self.q_weight @ x_norm
        K = self.k_weight @ x_norm
        V = self.v_weight @ x_norm
        if self.q_bias is not None:
            Q = Q + self.q_bias
        if self.k_bias is not None:
            K = K + self.k_bias
        if self.v_bias is not None:
            V = V + self.v_bias

        # ── RoPE ───────────────────────────────────────────────────
        Q_reshaped = Q.reshape(self.num_heads, self.head_dim)
        K_reshaped = K.reshape(self.num_kv_heads, self.head_dim)
        Q_rot = rope_rotate(Q_reshaped, position, self.rope_theta)
        K_rot = rope_rotate(K_reshaped, position, self.rope_theta)

        # ── Attention (single-token decode) ────────────────────────
        # Q: (num_heads, head_dim), K: (num_kv_heads, head_dim)
        # For models with GQA (num_kv_heads < num_heads): repeat K/V
        if self.num_kv_heads < self.num_heads:
            n_repeat = self.num_heads // self.num_kv_heads
            K_rot = np.repeat(K_rot, n_repeat, axis=0)  # (num_heads, head_dim)
            V_reshaped = V.reshape(self.num_kv_heads, self.head_dim)
            V_rot = np.repeat(V_reshaped, n_repeat, axis=0)
        else:
            V_rot = V.reshape(self.num_kv_heads, self.head_dim)

        # compute per-head attention scores
        attn_heads = np.zeros((self.num_heads, self.head_dim), dtype=np.float32)
        for h in range(self.num_heads):
            score = np.dot(Q_rot[h], K_rot[h]) / np.sqrt(self.head_dim)
            attn_prob = softmax(np.array([score]))  # single token → score=0
            attn_heads[h] = attn_prob[0] * V_rot[h]

        # ── O projection ───────────────────────────────────────────
        attn_concat = attn_heads.reshape(-1)
        attn_out = self.o_weight @ attn_concat

        # ── Residual add ───────────────────────────────────────────
        x = residual + attn_out

        # ── Pre-FFN RMSNorm ────────────────────────────────────────
        residual_ffn = x.astype(np.float32).copy()
        x_norm2 = rms_norm(x, self.ffn_norm_w, self.rms_eps)

        # ── Gate and Up projections ────────────────────────────────
        gate = self.gate_weight @ x_norm2
        up = self.up_weight @ x_norm2

        # ── SiLU activation on gate ────────────────────────────────
        gate_act = silu(gate)

        # ── Element-wise multiply: gate * up ───────────────────────
        ffn_hidden = gate_act * up  # (intermediate_size,)

        # ── Down projection ────────────────────────────────────────
        ffn_out = self.down_weight @ ffn_hidden

        # ── Residual add ───────────────────────────────────────────
        x = residual_ffn + ffn_out

        return x.astype(np.float32)


# ══════════════════════════════════════════════════════════════════════
# Token embedding
# ══════════════════════════════════════════════════════════════════════

def get_token_embedding(weights: dict, token_id: int) -> np.ndarray:
    """Get embedding vector for a token ID.

    Note: q4_dequant transposes 2D tensors, so token_embd.weight
    has shape (vocab_size, hidden_size) after loading.

    Args:
        weights: dict from load_weights_from_gguf()
        token_id: integer token ID
    Returns:
        (hidden_size,) float32 embedding
    """
    emb_w = weights["token_embd.weight"]  # shape (vocab_size, hidden_size)
    return emb_w[token_id, :].astype(np.float32).copy()


# ══════════════════════════════════════════════════════════════════════
# Forward pass runner
# ══════════════════════════════════════════════════════════════════════

def run_forward_pass(gguf_path: str, layers: list, prompt: str = "Hello",
                     n_tokens: int = 1) -> dict:
    """Run a multi-layer forward pass through Qwen2.5.

    Args:
        gguf_path: path to Qwen2.5 GGUF file
        layers: list of layer indices to run [0, 1, 2] means layers 0,1,2
        prompt: text prompt for tokenization
        n_tokens: max tokens to decode
    Returns:
        dict with keys: 'hidden_states' (dict layer→ndarray),
                        'model_params' (dict),
                        'token_ids' (list)
    """
    print(f"Loading GGUF: {gguf_path}")
    t0 = time.time()
    weights = load_weights_from_gguf(gguf_path)
    print(f"  Loaded {len(weights)} tensors in {time.time() - t0:.1f}s")

    # ── Parse model parameters from GGUF metadata ──────────────────
    import gguf
    reader = gguf.GGUFReader(gguf_path)

    def _get_field(key, default=None):
        try:
            return reader.fields[key].parts[-1][0]
        except (KeyError, IndexError, AttributeError):
            return default

    # After q4_dequant, 2D tensors are transposed to (N_out, K_in).
    hidden_size = int(weights["blk.0.attn_norm.weight"].shape[0])
    intermediate_size = int(weights["blk.0.ffn_gate.weight"].shape[0])
    num_heads = int(_get_field("qwen2.attention.head_count", default=16))
    num_kv_heads = int(_get_field("qwen2.attention.head_count_kv", default=16))
    head_dim = hidden_size // num_heads
    num_hidden_layers = int(_get_field("qwen2.block_count", default=36))
    rope_theta = float(_get_field("qwen2.rope.freq_base", default=1000000.0))
    rms_eps = float(_get_field("qwen2.attention.layer_norm_rms_epsilon", default=1e-6))

    params = {
        "hidden_size": hidden_size,
        "intermediate_size": intermediate_size,
        "num_heads": num_heads,
        "num_kv_heads": num_kv_heads,
        "head_dim": head_dim,
        "num_hidden_layers": num_hidden_layers,
        "rope_theta": rope_theta,
        "rms_eps": rms_eps,
        "model_file": gguf_path,
    }

    print(f"\nModel parameters:")
    for k, v in params.items():
        print(f"  {k}: {v}")

    # ── Tokenize prompt ────────────────────────────────────────────
    # Qwen2.5: add_bos=False; "Hello" → token 9707
    if prompt == "Hello":
        token_ids = [9707]
    else:
        bos_token_id = int(_get_field("tokenizer.ggml.bos_token_id", default=151643))
        token_ids = [bos_token_id]
    print(f"\nTokens: {token_ids} (prompt='{prompt}')")

    # ── Get input embedding ────────────────────────────────────────
    hidden = get_token_embedding(weights, token_ids[0])
    print(f"Input embedding shape: {hidden.shape}")

    # ── Run layers ─────────────────────────────────────────────────
    layer_outputs = {}
    for layer_idx in layers:
        print(f"\n{'=' * 60}")
        print(f"Layer {layer_idx}")
        print(f"{'=' * 60}")

        layer = Qwen25Layer(
            weights=weights,
            layer_idx=layer_idx,
            hidden_size=hidden_size,
            intermediate_size=intermediate_size,
            num_heads=num_heads,
            num_kv_heads=num_kv_heads,
            head_dim=head_dim,
            rope_theta=rope_theta,
            rms_eps=rms_eps,
        )

        t_start = time.time()
        hidden_out = layer.forward(hidden, position=0)
        elapsed = time.time() - t_start

        layer_outputs[layer_idx] = hidden_out.astype(np.float32)
        print(f"  Output shape: {hidden_out.shape}, dtype: {hidden_out.dtype}")
        print(f"  Output stats: mean={hidden_out.mean():.6f}, std={hidden_out.std():.6f}")
        print(f"  Output range: [{hidden_out.min():.6f}, {hidden_out.max():.6f}]")
        print(f"  Time: {elapsed:.2f}s")

        # Next layer input = current layer output
        hidden = hidden_out.astype(np.float32)

    return {
        "hidden_states": layer_outputs,
        "model_params": params,
        "token_ids": token_ids,
    }


# ══════════════════════════════════════════════════════════════════════
# Save golden .npz
# ══════════════════════════════════════════════════════════════════════

def save_golden_npz(results: dict, output_dir: Path):
    """Save per-layer hidden states as .npz golden vectors."""
    output_dir.mkdir(parents=True, exist_ok=True)

    hidden_states = results["hidden_states"]
    params = results["model_params"]
    token_ids = results["token_ids"]

    # Input embedding
    input_vec = results.get("input_embedding", None)

    # Per-layer outputs
    npz_data = {}
    for layer_idx, hs in sorted(hidden_states.items()):
        key = f"layer_{layer_idx}_output"
        npz_data[key] = hs.astype(np.float32)
        print(f"  {key}: shape={hs.shape}, mean={hs.mean():.6f}")

    # Input embedding (if stored)
    if input_vec is not None:
        npz_data["input_embedding"] = input_vec

    # Metadata
    metadata = {
        "params": params,
        "token_ids": token_ids,
        "layers": sorted(hidden_states.keys()),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "description": "Qwen2.5-3B 3-layer Func Model golden reference (float32)",
    }
    npz_data["metadata"] = np.array([json.dumps(metadata)])

    # Save combined .npz
    expected_path = output_dir / "expected.npz"
    np.savez(expected_path, **npz_data)
    print(f"\nSaved golden .npz: {expected_path}")
    print(f"  Keys: {list(npz_data.keys())}")

    # Save per-layer separate .npz for convenience
    for layer_idx, hs in sorted(hidden_states.items()):
        layer_path = output_dir / f"expected_l{layer_idx}.npz"
        np.savez(layer_path, output=hs.astype(np.float32),
                 layer=layer_idx, metadata=json.dumps(metadata))
        print(f"  Saved: {layer_path}")

    # Save input
    input_path = output_dir / "input.npz"
    # Re-read the input embedding for saving
    np.savez(input_path, token_ids=np.array(token_ids, dtype=np.int32),
             metadata=json.dumps(metadata))
    print(f"  Saved: {input_path}")


# ══════════════════════════════════════════════════════════════════════
# Llama.cpp reference comparison
# ══════════════════════════════════════════════════════════════════════

def run_llamacpp_reference(gguf_path: str, prompt: str,
                           ref_dir: Path, n_tokens: int = 1) -> dict:
    """Generate llama.cpp reference hidden states using dump_hidden_states.

    Returns:
        dict: {layer_idx: hidden_states_ndarray}
    """
    dump_bin = _PROJECT / "llama_ref" / "dump_hidden_states"

    if not dump_bin.exists():
        print(f"WARNING: dump_hidden_states not found at {dump_bin}")
        print("  Skipping llama.cpp reference comparison.")
        return None

    # Clear old refs
    import shutil
    if ref_dir.exists():
        shutil.rmtree(str(ref_dir))
    ref_dir.mkdir(parents=True, exist_ok=True)

    # Set LD_LIBRARY_PATH for llama.cpp shared libs
    lib_path = _PROJECT / "llama_ref" / "llama.cpp" / "build" / "bin"

    cmd = (
        f'LD_LIBRARY_PATH={lib_path} {dump_bin} '
        f'-m {gguf_path} -p "{prompt}" -n {n_tokens}'
    )
    print(f"\nRunning llama.cpp reference:")
    print(f"  {cmd}")

    import subprocess
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True,
        cwd=str(ref_dir.parent),  # run from llama_ref/
        timeout=300,
    )
    print(result.stderr[:500] if result.stderr else "(no stderr)")
    if result.returncode != 0:
        print(f"ERROR: dump_hidden_states failed: {result.stderr}")
        return None

    # Parse raw files into arrays
    # The ref_dir now contains *.raw and *.json files
    # We need to map l_out-N_0.raw to layer N output
    import re
    ref_outputs = {}
    for raw_file in sorted(ref_dir.glob("*.raw")):
        base = raw_file.stem
        json_file = ref_dir / f"{base}.json"
        if not json_file.exists():
            continue

        with open(json_file) as f:
            meta = json.load(f)

        name = meta.get("name", "")
        # Map l_out-0_0 → layer 0 output
        m = re.match(r"l_out-(\d+)", name)
        if m:
            layer_idx = int(m.group(1))
            if layer_idx not in ref_outputs:
                ne = meta["ne"]
                shape = [int(x) for x in ne if x > 1]
                if not shape:
                    shape = [1]

                with open(raw_file, "rb") as f:
                    raw = f.read()
                arr = np.frombuffer(raw, dtype=np.float32).reshape(shape)
                # Convert from (1, hidden_size) to (hidden_size,)
                if len(shape) == 2 and shape[0] == 1:
                    arr = arr.flatten()
                elif len(shape) == 2:
                    arr = arr.reshape(shape[1], shape[0]).flatten()

                ref_outputs[layer_idx] = arr.astype(np.float32)
                print(f"  llama.cpp l_out-{layer_idx}: shape={arr.shape}, "
                      f"mean={arr.mean():.6f}")

    return ref_outputs if ref_outputs else None


# ══════════════════════════════════════════════════════════════════════
# Comparison and reporting
# ══════════════════════════════════════════════════════════════════════

def compare_and_report(fm_outputs: dict, llama_outputs: dict,
                       layers: list, evidence_path: Path) -> dict:
    """Compare Func Model outputs against llama.cpp reference."""
    results = {
        "tests": len(layers),
        "passed": 0,
        "failed": 0,
        "per_layer": {},
    }

    print(f"\n{'=' * 60}")
    print(f"Comparison: Func Model vs llama.cpp")
    print(f"{'=' * 60}")

    for layer_idx in layers:
        fm = fm_outputs[layer_idx]
        ll = llama_outputs.get(layer_idx)

        if ll is None:
            print(f"  Layer {layer_idx}: SKIP (no llama.cpp reference)")
            continue

        cos_sim = cosine_similarity(fm, ll)
        rel_err = np.max(np.abs(fm.astype(np.float64) - ll.astype(np.float64)) /
                         (np.abs(ll.astype(np.float64)) + 1e-8))
        max_abs = float(np.max(np.abs(fm.astype(np.float64) - ll.astype(np.float64))))

        passed = cos_sim >= 0.999
        status = "PASS" if passed else "FAIL"

        print(f"  [{status}] Layer {layer_idx}: "
              f"cos_sim={cos_sim:.6f}, max_rel_err={rel_err:.2e}, max_abs_err={max_abs:.2e}")

        results["per_layer"][layer_idx] = {
            "cos_sim": cos_sim,
            "max_rel_err": rel_err,
            "max_abs_err": max_abs,
            "passed": passed,
        }

        if passed:
            results["passed"] += 1
        else:
            results["failed"] += 1

    # Summary
    print(f"\n  TESTS={results['tests']} PASS={results['passed']} FAIL={results['failed']}")

    # Write evidence log
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    with open(evidence_path, "w") as f:
        f.write(f"# W1.2: Qwen2.5-3B 3-Layer Func Model Forward Pass\n")
        f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"TESTS={results['tests']} PASS={results['passed']} FAIL={results['failed']}\n\n")
        for layer_idx in layers:
            r = results["per_layer"].get(layer_idx, {})
            if r:
                status = "PASS" if r.get("passed") else "FAIL"
                f.write(f"[{status}] Layer {layer_idx}: "
                        f"cos_sim={r['cos_sim']:.6f}, "
                        f"max_rel_err={r['max_rel_err']:.2e}, "
                        f"max_abs_err={r['max_abs_err']:.2e}\n")

    print(f"\nEvidence saved: {evidence_path}")
    return results


# ══════════════════════════════════════════════════════════════════════
# Main
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="W1.2: Qwen2.5-3B 3-layer Func Model forward pass"
    )
    parser.add_argument("--model", default=DEFAULT_MODEL_PATH,
                        help=f"Path to Qwen2.5 GGUF model (default: {DEFAULT_MODEL_PATH})")
    parser.add_argument("--layers", type=int, nargs="+", default=[0, 1, 2],
                        help="Layer indices to run (default: 0 1 2)")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT,
                        help=f"Prompt for tokenization (default: {DEFAULT_PROMPT})")
    parser.add_argument("--golden-dir", default=str(GOLDEN_DIR),
                        help="Output directory for golden .npz files")
    parser.add_argument("--skip-llamacpp", action="store_true",
                        help="Skip llama.cpp reference comparison")
    parser.add_argument("--evidence", default=str(EVIDENCE_DIR / "w1-2-fm-3layer.txt"),
                        help="Path for evidence log")

    args = parser.parse_args()

    # Resolve model path
    model_path = args.model
    if not os.path.exists(model_path):
        if os.path.exists(FALLBACK_MODEL_PATH):
            print(f"Model {model_path} not found, using fallback: {FALLBACK_MODEL_PATH}")
            model_path = FALLBACK_MODEL_PATH
        else:
            print(f"ERROR: Model not found at {model_path} or {FALLBACK_MODEL_PATH}")
            sys.exit(1)

    if not os.path.exists(model_path):
        print(f"ERROR: Model file not found: {model_path}")
        sys.exit(1)

    layers = sorted(set(args.layers))
    print(f"W1.2: Qwen2.5-3B Func Model Forward Pass")
    print(f"  Model: {model_path}")
    print(f"  Layers: {layers}")
    print(f"  Prompt: '{args.prompt}'")

    # ── Step 1: Run Func Model forward pass ────────────────────────
    results = run_forward_pass(model_path, layers, args.prompt)

    # ── Step 2: Save golden .npz ───────────────────────────────────
    golden_dir = Path(args.golden_dir)
    print(f"\n{'=' * 60}")
    print(f"Saving golden .npz to: {golden_dir}")
    save_golden_npz(results, golden_dir)

    # ── Step 3: Llama.cpp reference ────────────────────────────────
    if not args.skip_llamacpp:
        llama_outputs = run_llamacpp_reference(
            model_path, args.prompt,
            LLAMA_REF_DIR, n_tokens=DEFAULT_N_TOKENS
        )

        # ── Step 4: Compare ───────────────────────────────────────
        if llama_outputs is not None:
            compare_and_report(
                results["hidden_states"], llama_outputs,
                layers, Path(args.evidence)
            )
        else:
            print("\nWARNING: Could not generate llama.cpp reference.")
            print("  Golden .npz files saved successfully.")
            print("  Run comparison manually when llama.cpp is available.")
    else:
        print("\nSkipping llama.cpp comparison (--skip-llamacpp)")
        print("Golden .npz files saved successfully.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
