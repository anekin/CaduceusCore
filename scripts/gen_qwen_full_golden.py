#!/usr/bin/env python3
"""Generate N-layer golden reference for Qwen full forward pass.

Uses the Func Model's float32 forward pass (loads weights from GGUF) to
generate per-layer hidden states, output norm, logits, and token IDs.

Output format 1 (combined .npz):  {output}/qwen-{N}l-golden.npz
  Keys: l_out_0..l_out_{N-1}, logits, tokens, metadata

Output format 2 (per-layer .npz): {output}/expected_l0.npz..expected_l_{N-1}.npz
  Compatible with scripts/run_36layer_checkpoint.py
  Keys: output, layer, metadata

Requires: PYTHONPATH=sim (for qwen25_forward, ggml-npu for q4_dequant)

Usage:
  PYTHONPATH=sim:ggml-npu python3 scripts/gen_qwen_full_golden.py \
    --model ~/models/qwen2.5-3b-instruct-q4_k_m.gguf \
    --layers 36 \
    --output .omo/evidence/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# ── Path setup ──────────────────────────────────────────────────────
_PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT / "sim"))
sys.path.insert(0, str(_PROJECT / "ggml-npu"))

import gguf  # noqa: E402
from qwen25_forward import (  # noqa: E402
    cosine_similarity,
    rms_norm,
    run_forward_pass,
)

DEFAULT_MODEL_PATH = str(Path.home() / "models" / "qwen2.5-3b-instruct-q4_k_m.gguf")
DEFAULT_OUTPUT = str(_PROJECT / ".omo" / "evidence")
DEFAULT_PROMPT = "Hello"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate N-layer golden reference for Qwen forward pass"
    )
    parser.add_argument(
        "--model", type=str, default=DEFAULT_MODEL_PATH,
        help="Path to Qwen2.5 GGUF model file",
    )
    parser.add_argument(
        "--layers", type=int, default=None,
        help="Number of layers to generate (default: read from GGUF metadata)",
    )
    parser.add_argument(
        "--output", type=str, default=DEFAULT_OUTPUT,
        help="Output directory for .npz files",
    )
    parser.add_argument(
        "--prompt", type=str, default=DEFAULT_PROMPT,
        help="Prompt text to tokenize",
    )
    parser.add_argument(
        "--use-llamacpp-dump", action="store_true",
        help="Also run llama.cpp dump_hidden_states binary for cross-reference",
    )
    parser.add_argument(
        "--llamacpp-dump-bin", type=str,
        default=str(_PROJECT / "llama_ref" / "dump_hidden_states"),
        help="Path to dump_hidden_states binary",
    )
    return parser.parse_args()


def _read_gguf_meta(model_path: str) -> Dict[str, Any]:
    """Read GGUF metadata fields needed for golden generation."""
    reader = gguf.GGUFReader(model_path)
    fields = reader.fields

    def _get(key: str, default: Any = None) -> Any:
        try:
            return fields[key].parts[-1][0]
        except (KeyError, IndexError, AttributeError):
            return default

    return {
        "block_count": int(_get("qwen2.block_count", 36)),
        "hidden_size": int(_get("qwen2.embedding_length", 2048)),
        "vocab_size": len(reader.tensors[0].data),  # will be corrected below
        "rms_eps": float(_get("qwen2.attention.layer_norm_rms_epsilon", 1e-6)),
        "rope_theta": float(_get("qwen2.rope.freq_base", 1000000.0)),
    }


def _validate_output(
    output_dir: Path, combined_npz: Path, n_layers: int,
) -> Tuple[bool, List[str]]:
    """Validate generated golden files. Returns (ok, issues)."""
    issues: List[str] = []

    if not combined_npz.exists():
        issues.append(f"Combined .npz missing: {combined_npz}")
        return False, issues

    try:
        data = np.load(combined_npz, allow_pickle=True)
        keys = sorted(data.keys())

        # Check all expected keys exist
        for i in range(n_layers):
            key = f"l_out_{i}"
            if key not in keys:
                issues.append(f"Missing key '{key}' in combined .npz")
                continue
            arr = data[key]
            if np.any(np.isnan(arr)) or np.any(np.isinf(arr)):
                issues.append(f"NaN/Inf in l_out_{i}")

        if "logits" not in keys:
            issues.append("Missing 'logits' key in combined .npz")
        else:
            logits = data["logits"]
            if np.any(np.isnan(logits)) or np.any(np.isinf(logits)):
                issues.append("NaN/Inf in logits")

        if "tokens" not in keys:
            issues.append("Missing 'tokens' key in combined .npz")

        data.close()
    except Exception as exc:
        issues.append(f"Failed to validate combined .npz: {exc}")
        return False, issues

    # Check per-layer .npz files
    for i in range(n_layers):
        layer_path = output_dir / f"expected_l{i}.npz"
        if not layer_path.exists():
            issues.append(f"Missing expected_l{i}.npz")
            continue
        try:
            ld = np.load(layer_path, allow_pickle=True)
            if "output" not in ld:
                issues.append(f"Missing 'output' key in expected_l{i}.npz")
            elif np.any(np.isnan(ld["output"])) or np.any(np.isinf(ld["output"])):
                issues.append(f"NaN/Inf in expected_l{i}.npz")
            ld.close()
        except Exception as exc:
            issues.append(f"Failed to validate expected_l{i}.npz: {exc}")

    if not issues:
        print(f"Validation PASSED: {n_layers} layers, all keys present, no NaN/Inf")
        return True, issues
    return len(issues) == 0, issues


def _save_combined_npz(
    output_dir: Path, hidden_states: Dict[int, np.ndarray],
    logits: np.ndarray, tokens: np.ndarray, n_layers: int,
    metadata: Dict[str, Any],
) -> Path:
    """Save combined .npz with all layers, logits, and tokens."""
    combined: Dict[str, np.ndarray] = {}
    for i in range(n_layers):
        combined[f"l_out_{i}"] = hidden_states[i].astype(np.float32)
    combined["logits"] = logits.astype(np.float32)
    combined["tokens"] = tokens.astype(np.int64)
    combined["metadata"] = np.array([json.dumps(metadata)])

    npz_name = f"qwen-{n_layers}l-golden.npz"
    npz_path = output_dir / npz_name
    np.savez(npz_path, **combined)
    print(f"\nSaved combined golden: {npz_path}")
    print(f"  Keys: {list(combined.keys())}")
    print(f"  Size: {npz_path.stat().st_size / 1e6:.1f} MB")
    return npz_path


def _save_per_layer_npz(
    output_dir: Path, hidden_states: Dict[int, np.ndarray],
    n_layers: int, metadata: Dict[str, Any],
) -> None:
    """Save per-layer .npz files compatible with run_36layer_checkpoint.py."""
    for i in range(n_layers):
        layer_path = output_dir / f"expected_l{i}.npz"
        hs = hidden_states[i].astype(np.float32)
        np.savez(
            layer_path,
            output=hs,
            layer=i,
            metadata=json.dumps(metadata),
        )
    print(f"\nSaved {n_layers} per-layer .npz files to {output_dir}")
    print(f"  Format: expected_l{{0..{n_layers - 1}}}.npz")


def _run_llamacpp_dump(
    model_path: str, dump_bin: Path, prompt: str, n_tokens: int = 1,
) -> Optional[Dict[str, np.ndarray]]:
    """Run llama.cpp dump_hidden_states binary for cross-reference.

    Returns a dict of layer_idx -> flat float32 array like save_npz.py.
    """
    dump_bin = Path(dump_bin)
    if not dump_bin.exists():
        print(f"WARNING: dump_hidden_states binary not found at {dump_bin}")
        print("  Skipping llama.cpp cross-reference. Build with: make -C llama_ref")
        return None

    ref_dir = Path(tempfile.mkdtemp(prefix="qwen_golden_ref_"))
    try:
        lib_path = _PROJECT / "llama_ref" / "llama.cpp" / "build" / "bin"
        ld_path = str(lib_path) if lib_path.exists() else ""

        env = os.environ.copy()
        env.pop("CADUCEUS_DEVICE", None)
        if ld_path:
            env["LD_LIBRARY_PATH"] = ld_path

        cmd = [
            str(dump_bin),
            "-m", model_path,
            "-p", prompt,
            "-n", str(n_tokens),
        ]
        print(f"\nRunning llama.cpp dump_hidden_states:\n  {' '.join(cmd)}")
        print(f"  LD_LIBRARY_PATH={ld_path}")
        proc = subprocess.run(
            cmd, cwd=str(ref_dir), env=env,
            capture_output=True, text=True, timeout=1800,
        )
        if proc.returncode != 0:
            stderr_tail = "\n".join(proc.stderr.splitlines()[-20:])
            raise RuntimeError(
                f"dump_hidden_states failed (exit {proc.returncode}):\n{stderr_tail}"
            )

        # Parse raw dumps into dict
        refs: Dict[str, np.ndarray] = {}
        refs_dir = ref_dir / "refs"
        if not refs_dir.exists():
            print(f"WARNING: no refs/ directory in dump output. "
                  f"dump_hidden_states may have changed output path.")
            return None

        for raw_file in sorted(refs_dir.glob("*.raw")):
            base = raw_file.stem
            json_file = refs_dir / f"{base}.json"
            if not json_file.exists():
                continue
            with open(json_file) as f:
                meta = json.load(f)
            name = meta["name"]
            m = re.match(r"l_out-(\d+)", name)
            if not m:
                continue
            layer_idx = int(m.group(1))
            raw_bytes = raw_file.read_bytes()
            arr = np.frombuffer(raw_bytes, dtype=np.float32)
            refs[f"l_out_{layer_idx}"] = arr.copy()
            print(f"  llama.cpp l_out_{layer_idx}: shape={arr.shape}")

        return refs if refs else None
    finally:
        shutil.rmtree(ref_dir, ignore_errors=True)


def main() -> int:
    args = _parse_args()
    model_path = args.model
    if not Path(model_path).exists():
        print(f"ERROR: model not found: {model_path}")
        return 1

    # ── Read GGUF metadata ──────────────────────────────────────────
    meta = _read_gguf_meta(model_path)
    n_layers = args.layers if args.layers is not None else meta["block_count"]
    hidden_size = meta["hidden_size"]
    rms_eps = meta["rms_eps"]

    print(f"Model:  {model_path}")
    print(f"Layers: {n_layers} (max in file: {meta['block_count']})")
    print(f"Hidden: {hidden_size}")
    print(f"RMS eps: {rms_eps}")

    if args.layers and args.layers > meta["block_count"]:
        print(f"WARNING: requested {args.layers} layers but model only has "
              f"{meta['block_count']}. Using {meta['block_count']}.")
        n_layers = meta["block_count"]

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Run Func Model forward pass for all layers ──────────────────
    print(f"\nRunning Func Model forward pass for {n_layers} layers...")
    t0 = time.time()
    results = run_forward_pass(
        gguf_path=model_path,
        layers=list(range(n_layers)),
        prompt=args.prompt,
        n_tokens=1,
    )
    elapsed = time.time() - t0
    print(f"Forward pass complete: {n_layers} layers in {elapsed:.1f}s")

    hidden_states = results["hidden_states"]

    # ── Compute logits: output_norm + lm_head ────────────────────────
    print("\nComputing logits and tokens...")
    final_hidden = hidden_states[n_layers - 1]

    # The Func Model's load_weights_from_gguf already loaded all tensors.
    # We need output_norm.weight and output.weight separately.
    # Instead of relying on the cached dict, load these specific tensors.
    reader = gguf.GGUFReader(model_path)
    output_norm_w: Optional[np.ndarray] = None
    output_w: Optional[np.ndarray] = None

    for tensor in reader.tensors:
        if tensor.name == "output_norm.weight":
            raw = bytes(tensor.data.tobytes()) if hasattr(tensor.data, 'tobytes') else bytes(tensor.data)
            assert tensor.tensor_type.name == "F32", \
                f"output_norm.weight expected F32, got {tensor.tensor_type.name}"
            output_norm_w = np.frombuffer(raw, dtype=np.float32).copy()
        elif tensor.name == "output.weight":
            # output.weight is Q6_K in Qwen2.5, so use dequantize
            from q4_dequant import dequantize_q6_k, dequantize_q4_k
            raw = bytes(tensor.data.tobytes()) if hasattr(tensor.data, 'tobytes') else bytes(tensor.data)
            if tensor.tensor_type.name == "Q6_K":
                w_raw = dequantize_q6_k(raw)
            elif tensor.tensor_type.name == "Q4_K":
                w_raw = dequantize_q4_k(raw)
            elif tensor.tensor_type.name == "F32":
                w_raw = np.frombuffer(raw, dtype=np.float32).copy()
            elif tensor.tensor_type.name == "F16":
                from q4_dequant import fp16_to_fp32
                w_raw = fp16_to_fp32(np.frombuffer(raw, dtype=np.uint16))
            else:
                raise ValueError(
                    f"output.weight unsupported type: {tensor.tensor_type.name}"
                )
            # Transpose: GGUF stores as (hidden_size, vocab_size),
            # we want (vocab_size, hidden_size) for @ operation
            if len(tensor.shape) == 2:
                output_w = w_raw.reshape(tensor.shape[1], tensor.shape[0])
            else:
                output_w = w_raw

    if output_norm_w is None:
        print("ERROR: output_norm.weight not found in GGUF model")
        return 1
    if output_w is None:
        print("ERROR: output.weight not found in GGUF model")
        return 1

    # Apply output norm
    normed = rms_norm(final_hidden, output_norm_w, rms_eps)
    # Apply lm_head: (vocab_size, hidden_size) @ (hidden_size,) -> (vocab_size,)
    logits = output_w @ normed.astype(np.float32)
    token_ids = np.argmax(logits)

    print(f"  Logits shape: {logits.shape}")
    print(f"  Logits range: [{logits.min():.4f}, {logits.max():.4f}]")
    print(f"  Predicted next token: {token_ids}")
    print(f"  Top-5 token IDs: {np.argsort(logits)[-5:][::-1]}")

    # ── Build metadata ───────────────────────────────────────────────
    metadata: Dict[str, Any] = {
        "model": model_path,
        "layers": n_layers,
        "prompt": args.prompt,
        "hidden_size": hidden_size,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "description": "Qwen2.5 Func Model float32 golden reference",
        "commit": _get_git_commit(),
    }

    # ── Save outputs ─────────────────────────────────────────────────
    combined_npz = _save_combined_npz(
        output_dir, hidden_states, logits,
        np.atleast_1d(token_ids).astype(np.int64),
        n_layers, metadata,
    )
    _save_per_layer_npz(output_dir, hidden_states, n_layers, metadata)

    # ── Optional: llama.cpp cross-reference ──────────────────────────
    if args.use_llamacpp_dump:
        print("\n--- Llama.cpp cross-reference ---")
        try:
            llama_refs = _run_llamacpp_dump(
                model_path, Path(args.llamacpp_dump_bin),
                args.prompt, n_tokens=1,
            )
            if llama_refs:
                # Compare a few layers
                check_layers = [0, min(10, n_layers - 1),
                                min(20, n_layers - 1),
                                n_layers - 1]
                print("\nCross-check: Func Model vs llama.cpp:")
                for li in check_layers:
                    fm = hidden_states[li]
                    key = f"l_out_{li}"
                    ll = llama_refs.get(key)
                    if ll is not None:
                        cos = cosine_similarity(fm.flatten(), ll.flatten())
                        max_ae = float(np.max(np.abs(
                            fm.flatten().astype(np.float64)
                            - ll.flatten().astype(np.float64)
                        )))
                        print(f"  L{li}: cos_sim={cos:.6f}, max_abs_err={max_ae:.4e}")
                    else:
                        print(f"  L{li}: no llama.cpp reference")
        except Exception as exc:
            print(f"llama.cpp cross-reference failed: {exc}")
            print("(This is optional, continuing with outputs)")

    # ── Validate outputs ─────────────────────────────────────────────
    ok, issues = _validate_output(output_dir, combined_npz, n_layers)
    for issue in issues:
        print(f"  WARNING: {issue}")

    if not ok:
        return 1

    # ── Print summary ────────────────────────────────────────────────
    print(f"\n{'=' * 60}")
    print("Golden generation complete.")
    print(f"  Combined .npz: {combined_npz}")
    print(f"  Per-layer .npz: {output_dir}/expected_l{{0..{n_layers - 1}}}.npz")
    print(f"\nTo verify with checkpoint runner:")
    print(f"  cp {output_dir}/expected_l*.npz "
          f"rtl/test_vectors/soc_e2e/qwen25-3b-36layer/")
    print(f"  PYTHONPATH=sim python3 scripts/run_36layer_checkpoint.py "
          f"--model {model_path} --layers 0 10 20 {n_layers - 1}")

    return 0


def _get_git_commit() -> str:
    """Get current git commit hash."""
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(_PROJECT), text=True,
            timeout=5,
        ).strip()
    except Exception:
        return "unknown"


if __name__ == "__main__":
    sys.exit(main())
