#!/usr/bin/env python3
"""Convert raw hidden-state dumps from llama.cpp into a single .npz file."""

import glob
import json
import os
import re
import struct

import numpy as np


def load_raw_float32(path: str, ne: list) -> np.ndarray:
    """Load a raw float32 file and reshape it using the ggml tensor dimensions.

    ggml layout is row-major with the dimensions in ne[0]..ne[3] where ne[0]
    varies fastest. For a 2D hidden-state tensor, ne = (hidden_size, seq_len).
    We return a numpy array of shape (seq_len, hidden_size) in C order, which
    matches the underlying memory layout without a copy.
    """
    with open(path, "rb") as f:
        raw = f.read()

    n_floats = len(raw) // 4
    arr = np.frombuffer(raw, dtype=np.float32)
    if arr.size != n_floats:
        raise ValueError(f"size mismatch in {path}")

    # Drop trailing singleton dimensions for reshaping.
    shape = [int(x) for x in ne if x > 1]
    if not shape:
        shape = [1]

    arr = arr.reshape(shape)
    if len(shape) == 2:
        arr = arr.reshape(shape[1], shape[0])

    return arr


def main() -> None:
    out_dir = "refs"
    out_npz = os.path.join(out_dir, "qwen_l0_l1_hidden.npz")

    raw_files = sorted(glob.glob(os.path.join(out_dir, "*.raw")))
    if not raw_files:
        raise RuntimeError(f"no *.raw files found in {out_dir}")

    arrays = {}
    for raw_path in raw_files:
        base = os.path.splitext(raw_path)[0]
        json_path = base + ".json"
        if not os.path.exists(json_path):
            raise RuntimeError(f"missing metadata {json_path}")

        with open(json_path, "r") as f:
            meta = json.load(f)

        name = meta["name"]
        m = re.match(r"l_out-(\d+)", name)
        if m:
            npy_key = f"l_out_{int(m.group(1))}"
        else:
            npy_key = os.path.basename(base).replace(" ", "_")

        arr = load_raw_float32(raw_path, meta["ne"])
        arrays[npy_key] = arr
        print(f"{npy_key}: name={name} ne={meta['ne']} shape={arr.shape} dtype={arr.dtype}")

    np.savez(out_npz, **arrays)
    print(f"wrote {out_npz} with {len(arrays)} arrays")


if __name__ == "__main__":
    main()
