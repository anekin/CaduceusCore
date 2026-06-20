#!/usr/bin/env python3
"""
E2E Verification: llama.cpp hex protocol → Func Model (tile-level).

Flow:
  1. Load GGUF weights → per-block INT4 quantize → tile-major pack
  2. Generate hex files (simulating llama.cpp MUL_MAT request)
  3. Func Model: tile-level DMA → MXU(per-block+accumulate) → DMA out
  4. Compare vs direct per-block golden
"""

import sys, time, argparse, numpy as np
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "ggml-npu"))
sys.path.insert(0, str(_HERE))

from q4_dequant import load_weights_from_gguf
from golden_executor import GoldenMXU
from quantize import quantize_int4_per_block
from func_model import FuncModel
from regmap import Addr
from tile_scheduler import TILE_H, TILE_W, TILE_WEIGHT_BYTES, TILE_SCALE_BYTES


def pack_tile_major(wgt_row_packed: np.ndarray, wgt_scales: np.ndarray,
                    K: int, N: int) -> tuple:
    """Convert row-major packed INT4 + scales to tile-major layout."""
    num_blocks = (K + TILE_H - 1) // TILE_H
    num_tiles = (N + TILE_W - 1) // TILE_W
    wgt_bytes = bytearray()
    sc_bytes = bytearray()

    for n_tile in range(num_tiles):
        nc = min(TILE_W, N - n_tile * TILE_W)
        for k_block in range(num_blocks):
            kr = min(TILE_H, K - k_block * TILE_H)
            for r in range(kr):
                row_start = (k_block * TILE_H + r) * (N // 2) + n_tile * (nc // 2)
                wgt_bytes.extend(wgt_row_packed[row_start:row_start + nc // 2])
            sc_start = (k_block * N + n_tile * TILE_W) * 4
            sc_bytes.extend(wgt_scales.tobytes()[sc_start:sc_start + nc * 4])

    return bytes(wgt_bytes), bytes(sc_bytes)


def e2e_verify(gguf_path: str, layers: int, M: int = 1):
    """End-to-end: GGUF → tile-major → hex → Func Model → verify."""
    print(f"{'='*70}")
    print(f"E2E Verify: {Path(gguf_path).name}  layers={layers}  M={M}")
    print(f"{'='*70}")

    t0 = time.time()
    weights = load_weights_from_gguf(gguf_path)
    print(f"[1] Loaded {len(weights)} tensors in {time.time()-t0:.1f}s")

    mxu = GoldenMXU()
    rng = np.random.RandomState(42)
    OPS = ["Q_proj", "K_proj", "V_proj", "O_proj"]
    passed = 0
    failed = 0

    for layer in range(min(layers, 28)):
        for op in OPS:
            # Find weight
            target = f"blk.{layer}.attn_{op.lower().replace('_proj','')}.weight"
            if target not in weights:
                continue
            W_f32 = weights[target]
            K, N = W_f32.shape

            # Quantize + tile-major
            wgt_row, wgt_scales, _ = quantize_int4_per_block(W_f32, 128)
            wgt_tm, sc_tm = pack_tile_major(wgt_row, wgt_scales, K, N)

            # Activation
            act = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)

            # Golden
            golden = mxu.matmul_int4_per_block(act, wgt_row, wgt_scales, M, K, N, group_size=128)

            # Func Model
            model = FuncModel()
            # Safe addresses: scale after weight to avoid collision
            wgt_addr = 0x80020000
            act_addr = 0x80010000
            out_addr = 0x81000000
            scale_addr = wgt_addr + len(wgt_tm) + 0x100000  # 1MB gap after weights
            model.host_write_data(wgt_addr, np.frombuffer(wgt_tm, dtype=np.uint8))
            model.host_write_data(act_addr, act)
            model.host_write_data(scale_addr, np.frombuffer(sc_tm, dtype=np.float32))
            model.host_write_descriptor(0x80000080,
                input_addr=act_addr, weight_addr=wgt_addr, output_addr=out_addr,
                scale_addr=scale_addr, scale_size=len(sc_tm),
                input_size=act.nbytes, weight_size=len(wgt_tm),
                output_size=M * N * 4, M=M, K=K, N=N)
            model.host_write_command(0, 0x80000080)
            model.run()

            out_off = out_addr - Addr.DRAM_BASE
            out_fw = np.frombuffer(model.dram[out_off:out_off + M * N * 4],
                                   dtype=np.float32).reshape(M, N)
            ok = np.allclose(out_fw, golden, rtol=1e-5)
            icon = "✅" if ok else "❌"
            if passed + failed < 10:
                print(f"  [{icon}] L{layer} {op:12s} ({K}×{N})")
            if ok:
                passed += 1
            else:
                failed += 1
                print(f"    FAIL: max_diff={np.max(np.abs(out_fw-golden)):.2e}")

    print(f"\n{'='*70}")
    print(f"E2E Summary: {passed} PASS, {failed} FAIL")
    print(f"{'='*70}")
    return failed == 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=str(Path.home() / "models" /
                        "qwen2.5-1.5b-instruct-q4_k_m.gguf"))
    parser.add_argument("--layers", type=int, default=2)
    parser.add_argument("--prefill", type=int, default=1)
    args = parser.parse_args()
    e2e_verify(args.model, args.layers, M=args.prefill)
