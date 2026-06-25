#!/usr/bin/env python3
"""
Generate MXU E2E test vectors from a real Qwen2.5-3B-Instruct Q4_K_M GGUF.

Flow:
  1. Load GGUF and extract blk.0.attn_q.weight (the Qwen equivalent of Q_proj).
  2. Dequantize Q4_K blocks to float32.
  3. Symmetric-quantize the float32 weights to INT4 [-7, 7].
  4. Build M=1 INT8 activations (deterministic cyclic pattern).
  5. Call GoldenMXU.matmul_from_sram() and write RTL hex vectors.

This script intentionally does NOT modify golden_executor.py or compare_rtl.py.
"""

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_CADUCEUS_CORE = _SCRIPT_DIR.parent
sys.path.insert(0, str(_CADUCEUS_CORE))

from sim.golden_executor import GoldenMXU  # noqa: E402


def _load_module_from_path(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_Q4_DEQUANT_PATH = _CADUCEUS_CORE / "ggml-npu" / "q4_dequant.py"
_q4_dequant = _load_module_from_path("q4_dequant_local", _Q4_DEQUANT_PATH)
dequantize_q4_k = _q4_dequant.dequantize_q4_k

_GEN_VECTORS_PATH = _CADUCEUS_CORE / "scripts" / "gen_mxu_vectors.py"
_gen_vectors = _load_module_from_path("gen_mxu_vectors_local", _GEN_VECTORS_PATH)
generate_activations_int8 = _gen_vectors.generate_activations_int8
generate_scenario = _gen_vectors.generate_scenario


def load_q4_k_tensor(gguf_path: Path, tensor_name: str) -> np.ndarray:
    """Load and dequantize a single Q4_K tensor to float32."""
    import gguf

    reader = gguf.GGUFReader(str(gguf_path))
    for tensor in reader.tensors:
        if tensor.name == tensor_name:
            raw = bytes(tensor.data.tobytes()) if hasattr(tensor.data, "tobytes") else bytes(tensor.data)
            if tensor.tensor_type.name != "Q4_K":
                raise ValueError(f"{tensor_name} is {tensor.tensor_type.name}, expected Q4_K")
            w = dequantize_q4_k(raw)
            if len(tensor.shape) == 2:
                w = w.reshape(tensor.shape[1], tensor.shape[0])
            print(f"[GGUF] {tensor_name}: dequantized shape {w.shape}, dtype {w.dtype}")
            return w
    raise KeyError(f"Tensor {tensor_name} not found in {gguf_path}")


def quantize_int4_symmetric(w: np.ndarray) -> np.ndarray:
    """Quantize float32 weights to signed INT4 [-7, 7] symmetrically.

    Scale = max(|w|) / 7 so the largest magnitude maps to +/-7.
    This keeps zero exactly representable and uses the hardware INT4 range
    without clipping asymmetrically.
    """
    w = np.asarray(w, dtype=np.float32)
    max_abs = float(np.max(np.abs(w)))
    if max_abs == 0.0:
        return np.zeros_like(w, dtype=np.int8)
    scale = max_abs / 7.0
    q = np.rint(w / scale)
    q = np.clip(q, -7, 7)
    return q.astype(np.int8)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate MXU qwen_e2e vectors from real GGUF weights")
    parser.add_argument("--gguf", type=str, default="/tmp/models/qwen2.5-3b-instruct-q4_k_m.gguf",
                        help="Path to Qwen2.5-3B-Instruct Q4_K_M GGUF")
    parser.add_argument("--tensor", type=str, default="blk.0.attn_q.weight",
                        help="Tensor name to extract (Qwen GGUF uses attn_q.weight for Q_proj)")
    parser.add_argument("--out-dir", type=str, default=None,
                        help="Output base directory")
    args = parser.parse_args()

    gguf_path = Path(args.gguf)
    if not gguf_path.exists():
        print(f"ERROR: GGUF not found: {gguf_path}")
        sys.exit(1)

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        out_dir = _CADUCEUS_CORE / "rtl" / "test_vectors" / "mxu"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. Extract real weights
    w_f32 = load_q4_k_tensor(gguf_path, args.tensor)
    K, N = w_f32.shape
    print(f"[GGUF] Real Q_proj dimensions: K={K}, N={N}")

    # 2. Quantize to INT4
    weights_int4 = quantize_int4_symmetric(w_f32)
    unique = np.unique(weights_int4)
    print(f"[INT4] Unique quantized values: {unique}")
    print(f"[INT4] Quantization scale = {np.max(np.abs(w_f32)) / 7.0:.6e}")

    # 3. M=1 deterministic INT8 activations
    activations = generate_activations_int8(M=1, K=K)

    # 4. Generate RTL vectors + golden
    generate_scenario(
        out_dir=out_dir,
        scenario_name="qwen_e2e",
        M=1,
        K=K,
        N=N,
        weights=weights_int4,
        activations=activations,
    )

    scenario_dir = out_dir / "qwen_e2e"
    print(f"[DONE] Wrote vectors to {scenario_dir}")
    for fn in ["weights.hex", "activations.hex", "golden_output.hex", "params.txt", "manifest.json"]:
        p = scenario_dir / fn
        print(f"  {fn}: {p.stat().st_size} bytes")

    # 5. Quick round-trip sanity check: reload hex files and recompute golden
    M = 1
    mxu = GoldenMXU()
    with open(scenario_dir / "weights.hex") as f:
        w_hex = [int(line.strip(), 16) for line in f if line.strip()]
    with open(scenario_dir / "activations.hex") as f:
        a_hex = [int(line.strip(), 16) for line in f if line.strip()]
    packed = np.array(w_hex, dtype=np.uint32).view(np.uint8)
    act_u8 = np.array(a_hex, dtype=np.uint32).view(np.uint8)
    act_bytes = M * K
    sram = np.zeros(act_bytes + len(packed), dtype=np.uint8)
    sram[:act_bytes] = act_u8[:act_bytes]
    sram[act_bytes:] = packed
    result = mxu.matmul_from_sram(M, K, N, act_sram_addr=0, wgt_sram_addr=act_bytes, sram=sram)
    with open(scenario_dir / "golden_output.hex") as f:
        golden = np.array([int(line.strip(), 16) for line in f if line.strip()], dtype=np.uint32).view(np.int32)
    golden = golden.reshape(1, N)
    diff = np.abs(result.astype(np.int64) - golden.astype(np.int64))
    print(f"[CHECK] Round-trip max diff: {int(np.max(diff))} (shape {result.shape})")
    if not np.array_equal(result, golden):
        print("ERROR: Round-trip mismatch!")
        sys.exit(1)


if __name__ == "__main__":
    main()
