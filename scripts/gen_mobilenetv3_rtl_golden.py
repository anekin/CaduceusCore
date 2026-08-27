#!/usr/bin/env python3
"""Generate the MobileNetV3-Small RTL golden data file (soc-rtl-verification-signoff todo 13).

Extracts the ONNX graph structure and per-GEMM-layer quantized weights/scales
into ``sim/cv/traces/mobilenetv3_rtl_golden.npz`` so the RTL cocotb test
``test_e2e_mobilenetv3_chain`` (``sim/cocotb_bridge.py``) can run on sz0001,
where the cocotb Python env has numpy but NOT the ``onnx`` package.

All data stored here is STATIC (independent of activations): the cocotb test
replays the graph in numpy (im2col, activation quantization, non-conv ops) and
feeds only the GEMM layers through the SoC doorbell ring, comparing per-layer
RTL output against ``GoldenMXU.matmul_int4_per_block``.

Layout decisions (mirror ``sim/tests/test_mobilenetv3_fm_chain.py``):
- Per-block INT4 quantization with group_size=128 (``quantize_int4_per_block``).
- Depthwise convs are exported as ONE block-diagonal GEMM per layer
  (K = C*kh*kw, N = C).  The RTL test slices it into N <= 128 chunks so the
  whole layer still goes through the MXU as ordinary GEMMs.
- The activation scale is NOT stored: it is derived per chunk at runtime
  (per-tensor symmetric INT8) and folded into the weight block scales there.

Output:
    sim/cv/traces/mobilenetv3_rtl_golden.npz
      graph         — JSON node list in ONNX graph order, one dict per node:
                      {op, name, inputs, outputs, params}
      mmul_index    — int array: graph node index of each MMUL node
                      (Conv / depthwise_conv / Gemm) in graph order
      mmul_w_packed — object array of dense INT4-packed uint8 weights
      mmul_w_scales — object array of float32 block scales, shape (G, N)
      mmul_bias     — object array of float32 bias (N,) or None
      mmul_k        — int array of K per MMUL node
      mmul_n        — int array of N per MMUL node
      input_name    — str: graph input tensor name
      seed          — int: RNG seed the cocotb test uses for the input tensor

Usage:
    PYTHONPATH=sim python3 scripts/gen_mobilenetv3_rtl_golden.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

_PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT / "sim"))

import onnx  # noqa: E402

from cv.onnx_importer import import_mobilenetv3  # noqa: E402
from quantize import quantize_int4_per_block  # noqa: E402

MODEL_PATH = _PROJECT / "assets" / "mobilenetv3_small.onnx"
OUT_PATH = _PROJECT / "sim" / "cv" / "traces" / "mobilenetv3_rtl_golden.npz"
GROUP_SIZE = 128
SEED = 42

# Ops the cocotb test replays in numpy (mirrors test_mobilenetv3_fm_chain).
REPLAY_OPS = frozenset({
    "Conv", "Gemm", "Relu", "HardSwish", "HardSigmoid", "Add", "Mul",
    "ReduceMean", "Reshape", "Squeeze", "Shape", "Concat",
})


def _layer_map_by_name(onnx_path: str) -> dict[str, dict]:
    return {l["name"]: l for l in import_mobilenetv3(onnx_path)}


def main() -> int:
    if not MODEL_PATH.is_file():
        print(f"ERROR: ONNX model not found: {MODEL_PATH}")
        return 1

    model = onnx.load(str(MODEL_PATH))
    graph = model.graph
    init_map = {i.name: onnx.numpy_helper.to_array(i) for i in graph.initializer}
    layers = _layer_map_by_name(str(MODEL_PATH))

    nodes: list[dict] = []
    mmul_index: list[int] = []
    mmul_w_packed: list[np.ndarray] = []
    mmul_w_compact: list[object] = []
    mmul_w_scales: list[np.ndarray] = []
    mmul_bias: list[object] = []
    mmul_k: list[int] = []
    mmul_n: list[int] = []

    n_conv = 0
    n_gemm = 0
    n_dw = 0
    for idx, node in enumerate(graph.node):
        op = node.op_type
        name = node.name if node.name else f"{op}_{idx}"
        if op not in REPLAY_OPS:
            raise NotImplementedError(
                f"operator '{op}' (node {name}) is not handled by the "
                f"chain replay; regenerate/check the ONNX export"
            )
        layer = layers.get(name, {})

        params: dict = {}
        if op == "Conv":
            # Depthwise detection comes from cv/onnx_importer (groups ==
            # in_channels of the INPUT tensor; the weight's per-group C_in
            # is 1 and must not be used for detection).
            w = init_map[node.input[1]]
            C_out, C_in, kH, kW = w.shape
            groups = int(layer.get("groups") or 1)
            is_dw = layer.get("type") == "depthwise_conv"
            params.update({
                "groups": groups,
                "stride": int((layer.get("stride") or [1, 1])[0]),
                "pad_h": int((layer.get("padding") or [0, 0, 0, 0])[0]),
                "pad_w": int((layer.get("padding") or [0, 0, 0, 0])[1]),
                "C_in": C_in, "kH": kH, "kW": kW,
                "is_dw": is_dw,
            })
            if is_dw:
                # Compact per-channel form (C, kh*kw); the RTL test builds
                # the block-diagonal GEMM chunk in numpy and quantizes it
                # there (storing the dense block-diag would cost ~64x more).
                w_2d = w[:, 0].reshape(C_out, kH * kW).astype(np.float32)
                params["w_compact"] = True
                n_dw += 1
            else:
                w_2d = w.reshape(C_out, C_in * kH * kW).T.astype(np.float32)
                params["w_compact"] = False
            n_conv += 1
        elif op == "Gemm":
            w = init_map[node.input[1]]
            trans_b = any(a.name == "transB" and a.i for a in node.attribute)
            if trans_b:
                w_2d = w.T.astype(np.float32)
            else:
                w_2d = w.astype(np.float32)
            params["transB"] = bool(trans_b)
            params["w_compact"] = False
            n_gemm += 1
        else:
            w_2d = None

        if w_2d is not None:
            K, N = w_2d.shape
            if params.get("w_compact"):
                # DW compact: (C, 9) — K = C, N = kh*kw here; swap so K holds
                # the band length and N the channel count for the test.
                w_p, w_sc = np.zeros((0,), dtype=np.uint8), np.zeros((0, 0), dtype=np.float32)
                w_compact_store = w_2d.astype(np.float32)
                K, N = w_2d.shape[1], w_2d.shape[0]
            else:
                w_p, w_sc, _ = quantize_int4_per_block(w_2d, GROUP_SIZE)
                w_compact_store = None
            bias = None
            if len(node.input) > 2 and node.input[2] in init_map:
                bias = init_map[node.input[2]].astype(np.float32)
            mmul_index.append(idx)
            mmul_w_packed.append(np.frombuffer(w_p.tobytes(), dtype=np.uint8))
            mmul_w_scales.append(w_sc.astype(np.float32))
            mmul_w_compact.append(w_compact_store)
            mmul_bias.append(bias)
            mmul_k.append(K)
            mmul_n.append(N)
            params["bias"] = bias is not None

        if op == "ReduceMean":
            params["axes"] = [int(a) for a in init_map[node.input[1]]]
            params["keepdims"] = any(a.name == "keepdims" and a.i
                                     for a in node.attribute)
        elif op == "Concat":
            params["axis"] = int(next(
                (a.i for a in node.attribute if a.name == "axis"), 0))
        elif op == "Reshape":
            params["out_shape"] = layer.get("out_shape")
        elif op == "Shape":
            attrs = {a.name: a.i for a in node.attribute}
            params["start"] = attrs.get("start", 0)
            params["end"] = attrs.get("end", None)

        nodes.append({
            "op": op,
            "name": name,
            "inputs": [str(i) for i in node.input],
            "outputs": [str(o) for o in node.output],
            "params": params,
        })

    # Non-weight initializers referenced by replay ops (Concat constants etc.).
    weight_names = set()
    for node in graph.node:
        if node.op_type in ("Conv", "Gemm"):
            for i in node.input[1:]:
                weight_names.add(i)
    extra_init = {}
    for name, arr in init_map.items():
        if name in weight_names or arr.size > 4096:
            continue
        extra_init[name] = {
            "dtype": str(arr.dtype),
            "shape": [int(s) for s in arr.shape],
            "data": arr.flatten().tolist(),
        }

    # Generation gates (mirror test_op_dict_conversion expectations).
    assert len(nodes) == 124, f"expected 124 graph nodes, got {len(nodes)}"
    assert n_conv == 52, f"expected 52 conv layers, got {n_conv}"
    assert n_gemm == 2, f"expected 2 classifier Gemms, got {n_gemm}"
    assert len(mmul_index) == 54, f"expected 54 MMUL nodes, got {len(mmul_index)}"
    total_packed = sum(len(b) for b in mmul_w_packed)
    assert 1_000_000 <= total_packed <= 2_000_000, (
        f"packed weight bytes {total_packed} outside expected MobileNetV3-Small range")
    print(f"nodes={len(nodes)} convs={n_conv} (dw={n_dw}) gemms={n_gemm} "
          f"mmul={len(mmul_index)} packed_weight_bytes={total_packed}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        OUT_PATH,
        graph=np.array(json.dumps(nodes), dtype=object),
        mmul_index=np.asarray(mmul_index, dtype=np.int64),
        mmul_w_packed=np.asarray(mmul_w_packed, dtype=object),
        mmul_w_scales=np.asarray(mmul_w_scales, dtype=object),
        mmul_w_compact=np.asarray(mmul_w_compact, dtype=object),
        mmul_bias=np.asarray(mmul_bias, dtype=object),
        mmul_k=np.asarray(mmul_k, dtype=np.int64),
        mmul_n=np.asarray(mmul_n, dtype=np.int64),
        input_name=np.array(graph.input[0].name, dtype=object),
        extra_init=np.array(json.dumps(extra_init), dtype=object),
        seed=np.asarray([SEED], dtype=np.int64),
        allow_pickle=True,
    )
    print(f"golden written to {OUT_PATH} "
          f"({OUT_PATH.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
