#!/usr/bin/env python3
"""
Task 18: Generate E2E real-model SFU/Vector test vectors.

Since no Qwen2.5-3B GGUF model is present on this system, this script generates
deterministic synthetic data that mimics real layer-0 distributions:
- hidden_states: mean≈0, std≈1 (normal distribution)
- attn_weights: logit-like values in [-10, 10]
- ffn_hidden: post-GELU activations, mostly non-negative with right skew

Uses GoldenSFU/GoldenVector from CaduceusCore/sim/golden_executor.py for golden
reference generation. Does NOT modify golden_executor.py computation logic.

Outputs test vector directories under:
  CaduceusCore/rtl/test_vectors/sfu/e2e_<scenario>/
  CaduceusCore/rtl/test_vectors/vector/e2e_<scenario>/
"""

import json
import os
import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
CADUCEUS_CORE = SCRIPT_DIR.parent
sys.path.insert(0, str(CADUCEUS_CORE))
sys.path.insert(0, str(CADUCEUS_CORE / "sim"))

from sim.golden_executor import GoldenSFU, GoldenVector  # noqa: E402

E2E_SFU_DIR = CADUCEUS_CORE / "rtl" / "test_vectors" / "sfu"
E2E_VECTOR_DIR = CADUCEUS_CORE / "rtl" / "test_vectors" / "vector"

SEED = 42
rng = np.random.RandomState(SEED)

Qwen_hidden_size = 2048
Qwen_intermediate_size = 11008  # Qwen2.5-3B ff size

sfu = GoldenSFU()


def fp16_to_hex(arr: np.ndarray) -> list[str]:
    """Convert numpy float16 array to hex strings (4 hex digits)."""
    f16 = arr.astype(np.float16)
    as_uint16 = f16.view(np.uint16)
    return [f"{v:04x}" for v in as_uint16]


def i32_to_hex(arr: np.ndarray) -> list[str]:
    """Convert numpy int32 array to hex strings (8 hex digits)."""
    i32 = arr.astype(np.int32)
    as_uint32 = i32.view(np.uint32)
    return [f"{v:08x}" for v in as_uint32]


def write_test_vector_dir(
    base_dir: Path,
    scenario: str,
    params: dict,
    manifest: dict,
    files: dict[str, list[str]],
):
    """
    Write a test vector directory.

    files: {filename_basename: [hex lines]}
    """
    sdir = base_dir / scenario
    sdir.mkdir(parents=True, exist_ok=True)

    # params.txt
    param_lines = [f"{k}={v}" for k, v in params.items()]
    (sdir / "params.txt").write_text("\n".join(param_lines) + "\n")

    # manifest.json
    (sdir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # data files
    for fname, lines in files.items():
        (sdir / fname).write_text("\n".join(lines) + "\n")

    print(f"  [{scenario}] written ({len(files)} files)")


def generate_e2e_softmax():
    """
    Generate E2E softmax: attn_weights from Qwen blk.0.
    Simulated as logit-like values in [-10, 10] with softmax producing
    concentrated probabilities (mimics attention logits after scaling).
    """
    scenario = "e2e_qwen_blk0_softmax"
    dim = 64  # single head

    # Simulate attention logits for one query against 64 keys
    logits = rng.randn(dim).astype(np.float32) * 2.0
    # Make one position dominant to mimic real attention
    logits[0] = 15.0
    logits[1] = 8.0
    logits = logits.astype(np.float16)

    golden = sfu.softmax_hw(logits.astype(np.float16))
    golden = golden.astype(np.float16)

    write_test_vector_dir(
        E2E_SFU_DIR,
        scenario,
        params={"OP": "softmax", "DIM": str(dim)},
        manifest={
            "sfu_op": True,
            "files": {"golden": "golden_output.hex", "input": "input.hex"},
            "results": {"golden_shape": [dim], "sfu_len": dim},
            "scenario": scenario,
            "description": "E2E Qwen2.5-3B blk.0 attn_weights softmax (synthetic-realistic)",
        },
        files={
            "input.hex": fp16_to_hex(logits),
            "golden_output.hex": fp16_to_hex(golden),
        },
    )
    return scenario


def generate_e2e_layernorm():
    """
    Generate E2E layernorm: hidden_states after attention.
    Qwen2.5-3B hidden_size=2048, distribution mean≈0, std≈1.
    """
    scenario = "e2e_qwen_blk0_layernorm"
    dim = 2048

    hidden = rng.randn(dim).astype(np.float32) * 1.0
    # Add slight bias as real hidden states are not perfectly zero-mean
    hidden += 0.05
    hidden = hidden.astype(np.float16)

    golden = GoldenSFU.layernorm_hw(hidden)
    golden = golden.astype(np.float16)

    write_test_vector_dir(
        E2E_SFU_DIR,
        scenario,
        params={"OP": "layernorm", "DIM": str(dim)},
        manifest={
            "sfu_op": True,
            "files": {"golden": "golden_output.hex", "input": "input.hex"},
            "results": {"golden_shape": [dim], "sfu_len": dim},
            "scenario": scenario,
            "description": "E2E Qwen2.5-3B blk.0 hidden_states layernorm (synthetic-realistic)",
        },
        files={
            "input.hex": fp16_to_hex(hidden),
            "golden_output.hex": fp16_to_hex(golden),
        },
    )
    return scenario


def generate_e2e_gelu():
    """
    Generate E2E GELU: FFN intermediate activations.
    Qwen2.5-3B intermediate_size=11008. Distribution: mostly non-negative,
    some negative values (pre-activation).
    """
    scenario = "e2e_qwen_blk0_gelu"
    dim = 2048

    # Pre-GELU activations: normal distribution with slight positive bias
    pre_gelu = rng.randn(dim).astype(np.float32) * 2.0 + 0.3
    pre_gelu = pre_gelu.astype(np.float16)

    golden = sfu.gelu_hw(pre_gelu)
    golden = golden.astype(np.float16)

    write_test_vector_dir(
        E2E_SFU_DIR,
        scenario,
        params={"OP": "gelu", "DIM": str(dim)},
        manifest={
            "sfu_op": True,
            "files": {"golden": "golden_output.hex", "input": "input.hex"},
            "results": {"golden_shape": [dim], "sfu_len": dim},
            "scenario": scenario,
            "description": "E2E Qwen2.5-3B blk.0 ffn_hidden GELU (synthetic-realistic)",
        },
        files={
            "input.hex": fp16_to_hex(pre_gelu),
            "golden_output.hex": fp16_to_hex(golden),
        },
    )
    return scenario


def generate_e2e_rmsnorm():
    """
    Generate E2E RMSNorm: post-attention hidden states.
    Qwen uses RMSNorm (not LayerNorm) at attention output.
    """
    scenario = "e2e_qwen_blk0_rmsnorm"
    dim = 2048

    hidden = rng.randn(dim).astype(np.float32) * 1.0 + 0.02
    hidden = hidden.astype(np.float16)

    golden = GoldenSFU.rmsnorm_hw(hidden)
    golden = golden.astype(np.float16)

    write_test_vector_dir(
        E2E_SFU_DIR,
        scenario,
        params={"OP": "rmsnorm", "DIM": str(dim)},
        manifest={
            "sfu_op": True,
            "files": {"golden": "golden_output.hex", "input": "input.hex"},
            "results": {"golden_shape": [dim], "sfu_len": dim},
            "scenario": scenario,
            "description": "E2E Qwen2.5-3B blk.0 hidden_states RMSNorm (synthetic-realistic)",
        },
        files={
            "input.hex": fp16_to_hex(hidden),
            "golden_output.hex": fp16_to_hex(golden),
        },
    )
    return scenario


def generate_e2e_resid_add():
    """
    Generate E2E residual add: original hidden + FFN output.
    Both INT32, mimicking MXU accumulator output.
    """
    scenario = "e2e_qwen_blk0_resid_add"
    dim = 2048

    # Original hidden states (skip connection), INT32 range
    orig = (rng.randn(dim).astype(np.float32) * 100.0).astype(np.int32)
    # FFN delta, smaller magnitude (residual contribution)
    delta = (rng.randn(dim).astype(np.float32) * 50.0).astype(np.int32)

    golden = GoldenVector.residual_add(orig, delta)
    golden = golden.astype(np.int32)

    write_test_vector_dir(
        E2E_VECTOR_DIR,
        scenario,
        params={"OP": "RESID", "DIM": str(dim)},
        manifest={
            "sfu_op": False,
            "files": {"a": "a.hex", "b": "b.hex", "golden": "golden_output.hex"},
            "results": {"golden_shape": [dim]},
            "scenario": scenario,
            "description": "E2E Qwen2.5-3B blk.0 resid_add (synthetic-realistic INT32)",
        },
        files={
            "a.hex": i32_to_hex(orig),
            "b.hex": i32_to_hex(delta),
            "golden_output.hex": i32_to_hex(golden),
        },
    )
    return scenario


def generate_e2e_conv():
    """
    Generate E2E type conversion: INT32 MXU output → FP16 SFU input.
    The critical bridge between MXU and SFU.
    """
    scenario = "e2e_qwen_blk0_conv"
    dim = 2048

    # INT32 accumulator outputs (MXU produces these)
    mxu_out = (rng.randn(dim).astype(np.float32) * 200.0).astype(np.int32)
    # Clip to INT32 range
    mxu_out = np.clip(mxu_out, -2**31 + 1, 2**31 - 1)

    golden = GoldenVector.conv_i32_to_f16(mxu_out)
    golden = golden.astype(np.float16)

    write_test_vector_dir(
        E2E_VECTOR_DIR,
        scenario,
        params={"OP": "CONV", "DIM": str(dim)},
        manifest={
            "sfu_op": True,  # FP16 output → uses compare_sfu.py float16 path
            "files": {"x": "x.hex", "golden": "golden_output.hex"},
            "results": {"golden_shape": [dim], "sfu_len": dim},
            "scenario": scenario,
            "description": "E2E Qwen2.5-3B blk.0 INT32→FP16 conv bridge (synthetic-realistic)",
        },
        files={
            "x.hex": i32_to_hex(mxu_out),
            "golden_output.hex": fp16_to_hex(golden),
        },
    )
    return scenario


def main():
    print("=== Task 18: E2E Real-Model Test Vector Generation ===\n")
    print("Note: Using deterministic synthetic-realistic data (no GGUF model).")
    print("Data mimics Qwen2.5-3B layer-0 distributions (mean≈0, std≈1).\n")

    scenarios = []

    print("--- SFU E2E scenarios ---")
    scenarios.append(("SFU", generate_e2e_softmax()))
    scenarios.append(("SFU", generate_e2e_layernorm()))
    scenarios.append(("SFU", generate_e2e_gelu()))
    scenarios.append(("SFU", generate_e2e_rmsnorm()))

    print("\n--- Vector E2E scenarios ---")
    scenarios.append(("Vector", generate_e2e_resid_add()))
    scenarios.append(("Vector", generate_e2e_conv()))

    print(f"\nGenerated {len(scenarios)} E2E scenarios:")
    for engine, name in scenarios:
        print(f"  [{engine}] {name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
