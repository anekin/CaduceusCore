#!/usr/bin/env python3
"""Standalone NPU hex correctness verifier.
Reads a batch directory, re-computes every MUL_MAT, compares.
"""
import json, sys, struct, os
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from npu_server import read_f32_hex, get_weight, load_model, weight_buffer


def verify_batch(batch_dir: str, model_path: str):
    batch_dir = Path(batch_dir)
    manifest_path = batch_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"  SKIP: no manifest in {batch_dir}")
        return

    manifest = json.loads(manifest_path.read_text())
    ops = manifest.get("ops", [])

    passed = 0
    failed = 0
    skipped = 0

    for i, op in enumerate(ops):
        name = op["name"]
        M, K, N = op["M"], op["K"], op["N"]
        out_file = batch_dir / op["out_file"]
        act_file = batch_dir / op["act_file"]

        # Read NPU result
        n_out = op["out_bytes"] // 4
        npu_result = read_f32_hex(str(out_file), n_out).reshape(M, N)

        # Read activation
        n_act = M * K
        act = read_f32_hex(str(act_file), n_act).reshape(M, K)

        # Re-compute
        W = get_weight(name)
        if W is None:
            ref = np.zeros((M, N), dtype=np.float32)
            skipped += 1
            status = "SKIP (no weight)"
        else:
            if W.shape[0] == K:
                ref = act @ W
            else:
                ref = act @ W.T

            max_abs = np.max(np.abs(npu_result - ref))
            rel = np.max(np.abs((npu_result - ref) / (np.abs(ref) + 1e-8)))

            if max_abs < 1e-5 and rel < 1e-5:
                passed += 1
                status = "PASS"
            else:
                failed += 1
                status = f"FAIL max_abs={max_abs:.2e} rel={rel:.2e}"

        print(f"  [{status}] {name} M={M} K={K} N={N}")

    print(f"\n  Summary: {passed} PASS, {failed} FAIL, {skipped} SKIP")
    return failed == 0


def main():
    if len(sys.argv) < 2:
        print("Usage: verify_hex.py <batch_dir> [model_gguf]")
        sys.exit(1)

    batch_dir = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else str(Path.home() / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf")

    print(f"Loading model: {model}")
    load_model(model)
    print(f"Loaded {len(weight_buffer)} weight tensors")

    ok = verify_batch(batch_dir, model)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
