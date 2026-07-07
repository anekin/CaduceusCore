#!/usr/bin/env python3
"""Diagnose per-op drift across all 36 layers."""
import sys
from pathlib import Path

_PROJECT = Path(__file__).resolve().parent
sys.path.insert(0, str(_PROJECT / "sim"))

from qwen25_forward import DEFAULT_MODEL_PATH, run_forward_pass, cosine_similarity
from qwen25_l3 import INTERMEDIATE_MAP
import numpy as np


def main():
    layers = list(range(36))
    results = run_forward_pass(DEFAULT_MODEL_PATH, layers, "Hello", capture_intermediates=True)
    ref_dir = _PROJECT / "llama_ref" / "refs"
    llama = {"per_layer": {}, "per_op": {}}
    per_op_rank = {}
    import re, json
    for raw_file in sorted(ref_dir.glob("*.raw")):
        base = raw_file.stem
        json_file = ref_dir / f"{base}.json"
        if not json_file.exists():
            continue
        with open(json_file) as f:
            meta = json.load(f)
        with open(raw_file, "rb") as f:
            raw = f.read()
        arr = np.frombuffer(raw, dtype=np.float32)
        ne_all = [int(x) for x in meta["ne"]]
        ne = [x for x in ne_all if x > 1]
        if not ne:
            ne = [1]
        arr = arr.reshape(ne)
        if len(ne) == 2:
            arr = arr.reshape(ne[1], ne[0])
        flat = arr.astype(np.float32).flatten()

        m = re.match(r"(l_out|attn_norm|ffn_inp|ffn_norm|ffn_gate|ffn_up|ffn_out)-(\d+)_(\d+)", base)
        if m:
            name, layer, _ = m.groups()
            key = f"{name}_{int(layer)}"
            llama["per_layer"][key] = flat
            continue
        m = re.match(r"(Qcur|Kcur|Vcur)-(\d+)_(\d+)", base)
        if m:
            name, layer, _ = m.groups()
            key = f"{name}_{int(layer)}"
            n_non_singleton = sum(1 for x in ne_all if x > 1)
            existing_rank = per_op_rank.get(key, 1)
            if key not in llama["per_op"] or n_non_singleton > existing_rank:
                llama["per_op"][key] = flat
                per_op_rank[key] = n_non_singleton
            continue
    llama["per_layer"].update(llama["per_op"])
    llama = llama["per_layer"]

    print("Per-op cos_sim per layer:")
    for L in layers:
        fm_int = results["intermediates"][L]
        print(f"\nLayer {L}")
        for op_name, pattern in INTERMEDIATE_MAP.items():
            key = pattern.format(layer=L)
            fm_arr = fm_int.get(op_name)
            ll_arr = llama.get(key)
            if fm_arr is None or ll_arr is None:
                continue
            fm_flat = fm_arr.astype(np.float64).flatten()
            ll_flat = ll_arr.astype(np.float64).flatten()
            if fm_flat.size != ll_flat.size:
                continue
            cos = cosine_similarity(fm_flat, ll_flat)
            print(f"  {op_name:10s}: cos={cos:.6f}")


if __name__ == "__main__":
    main()
