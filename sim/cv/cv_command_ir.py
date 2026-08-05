"""ONNX → Caduceus command IR converter for MobileNetV3-Small.

Maps ONNX topology (from ``onnx_importer``) to a production ``CommandBlob``
via the ``software/compiler/command_ir`` package.  Produces an encoded blob
suitable for device dispatch (B4) and provides a ``decode()`` helper for
round-trip verification.

Supported mappings:
  - Conv (pointwise / depthwise) → im2col + MMUL (MXU)
  - HardSwish                → SFU_RELU + VMUL
  - HardSigmoid              → SFU_RELU + VMUL
  - GlobalAveragePool        → VRED_SUM + VMUL
  - SE block (ReduceMean + Conv + ReLU + HardSigmoid + Mul)
                             → VRED_SUM → MMUL → SFU_RELU → SFU_RELU → VMUL
  - Add                      → VADD
  - Mul                      → VMUL
  - ReLU                     → SFU_RELU
  - Gemm                     → MMUL
"""

from __future__ import annotations

import os
import struct
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Path setup — the production CommandBlob lives under software/compiler
# and imports gen.npu_abi (repo root).
# ---------------------------------------------------------------------------
_cv_dir = os.path.dirname(os.path.abspath(__file__))
_sim_dir = os.path.dirname(_cv_dir)
_repo_root = os.path.dirname(_sim_dir)
_sw_dir = os.path.join(_repo_root, "software")
for _p in (_repo_root, _sw_dir):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gen.npu_abi as _abi


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class UnsupportedCVOp(ValueError):
    """Raised when an ONNX operator cannot be mapped to Caduceus NPU primitives.

    The message includes the offending operator name.
    """
    pass


# ---------------------------------------------------------------------------
# Lazy import of the production IR (deferred until first use).
# ---------------------------------------------------------------------------

_IR = None  # cached tuple (CommandBlob, LowerStatus, cap-constants)


def _get_ir():
    """Return ``(CommandBlob, LowerStatus, CAP_DMA, CAP_MXU, CAP_SFU, CAP_VECTOR)``."""
    global _IR
    if _IR is not None:
        return _IR
    from software.compiler.command_ir import CommandBlob, LowerStatus  # noqa: F811
    from software.compiler.command_ir_types import (
        CAD_CAP_DMA,
        CAD_CAP_MXU,
        CAD_CAP_SFU,
        CAD_CAP_VECTOR,
    )
    _IR = (CommandBlob, LowerStatus, CAD_CAP_DMA, CAD_CAP_MXU, CAD_CAP_SFU, CAD_CAP_VECTOR)
    return _IR


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dim_product(shape: list[int] | None, exclude_batch: bool = False) -> int:
    """Product of positive dimension values; ``0`` (symbolic) is skipped."""
    if shape is None:
        return 1
    n = 1
    start = 1 if exclude_batch and len(shape) > 1 else 0
    for d in shape[start:]:
        if d > 0:
            n *= d
    return max(n, 1)


def _conv_gemm_dims(layer: dict[str, Any]) -> tuple[int, int, int]:
    """Derive (M, K, N) GEMM dims from a Conv / depthwise_conv layer."""
    sh = layer.get("in_shape")
    osh = layer.get("out_shape")
    k = layer.get("kernel")

    if sh is None or osh is None or k is None:
        raise UnsupportedCVOp(
            f"Cannot derive GEMM dims for '{layer.get('name', '?')}': "
            f"missing shape or kernel info"
        )

    C_in = sh[1] if len(sh) >= 2 else 1
    C_out = osh[1] if len(osh) >= 2 else 1
    H = sh[2] if len(sh) >= 3 else 1
    W = sh[3] if len(sh) >= 4 else 1
    kh, kw = k[0], k[1]
    stride = (layer.get("stride") or [1, 1])[0]
    padding = (layer.get("padding") or [0, 0, 0, 0])[0]
    groups = layer.get("groups", 1)

    from .conv_mapper import map_conv_to_gemm

    r = map_conv_to_gemm(
        C_in, C_out, H, W, max(kh, kw), stride, padding, groups
    )
    return r["M"], r["K"], r["N"]


# ---------------------------------------------------------------------------
# Layer → command mapping context and dispatch
# ---------------------------------------------------------------------------

class _Ctx:
    """Mutable context shared across layer mappings within one conversion."""

    def __init__(self, blob) -> None:  # noqa: ANN001
        self.blob = blob
        self.last_buf: int = 0
        self.second_last_buf: int = 0
        self.num_commands: int = 0
        self._temp_pool: list[int] = []   # pre-declared scratch buffer IDs
        self._temp_next: int = 0          # next pool slot to allocate

    def declare(self, size: int, alignment: int = 64, host_addr: int = 0) -> int:
        return self.blob.declare_buffer(size, alignment, host_addr)

    def declare_dram(self, size: int, alignment: int = 64) -> int:
        """Declare a buffer in DRAM (avoids SRAM exhaustion for large models)."""
        return self.blob.declare_buffer(size, alignment, host_addr=_abi.Addr.DRAM)

    def declare_output(self, size: int) -> int:
        """Declare a persistent output buffer in DRAM."""
        return self.declare_dram(size)

    def temp_buf(self, size: int) -> int:
        """Return a scratch buffer ID from a pre-allocated pool.

        Keeps the total buffer count under ``CAD_MAX_BUFFERS`` for large
        models by cycling through a fixed set of reusable temporary IDs.
        All pool slots are sized generously (1 MiB) so any intermediate
        tensor fits; the pool is allocated on first use.
        """
        pool_size = 32
        if not self._temp_pool:
            pool_sz = max(size, 1024 * 1024)  # at least 1 MiB
            for _i in range(pool_size):
                self._temp_pool.append(
                    self.blob.declare_buffer(pool_sz, 64, host_addr=_abi.Addr.DRAM)
                )
        idx = self._temp_next % pool_size
        self._temp_next += 1
        return self._temp_pool[idx]

    def _deps(self) -> list[int] | None:
        return [self.last_buf] if self.last_buf else None


# ── Vector opcode offsets (EngineOp.VADD + offset) ────────────────────────

_VEC_VADD = 0          # VADD  (0x0f + 0)
_VEC_VMUL = 1          # VMUL  (0x0f + 1)
_VEC_VRED_SUM = 3      # VRED_SUM (0x0f + 3)  ← NOT 2 (that's VRED_MAX)

# ── SFU sub-opcodes (as used by CommandBlob.add_sfu) ──────────────────────

_SFU_RELU = 3          # SFU_RELU
_SFU_HARDSWISH = 5     # x * ReLU6(x+3) / 6
_SFU_HARDSIGMOID = 6   # ReLU6(x+3) / 6

# ── F32 depthwise/conv metadata footer (embedded in weight buffer) ─────────
#
# The F32 CV executors in device_server.py perform real im2col convolution
# for kernels larger than 1×1.  The metadata is appended to the weight bytes
# so the executor knows kernel/stride/pad/shape/layout without adding a new
# IR opcode.  Format: 20 × uint32 little-endian.
# ---------------------------------------------------------------------------

_CONV_META_MAGIC = 0xCADB0001
_CONV_META_FIELDS = 20
_CONV_META_SIZE = _CONV_META_FIELDS * 4


def _pack_conv_meta(
    op_kind: int,
    layer: dict[str, Any],
    input_layout: str,
) -> bytes:
    """Pack convolution metadata into a fixed-size little-endian footer.

    Parameters
    ----------
    op_kind:
        0 = pointwise/gemm (no im2col), 1 = standard conv, 2 = depthwise.
    layer:
        ONNX layer dict from ``onnx_importer``.
    input_layout:
        ``'nchw'`` for the model input, ``'nhwc'`` for intermediate tensors.
    """
    sh = layer.get("in_shape") or [1, 1, 1, 1]
    osh = layer.get("out_shape") or [1, 1, 1, 1]
    kernel = layer.get("kernel") or [1, 1]
    stride = layer.get("stride") or [1, 1]
    padding = layer.get("padding") or [0, 0, 0, 0]

    fields = [
        _CONV_META_MAGIC,
        op_kind,
        kernel[0],                       # kernel_h
        kernel[1],                       # kernel_w
        stride[0],                       # stride_h
        stride[1],                       # stride_w
        padding[0],                      # pad_top
        padding[1],                      # pad_left
        padding[2],                      # pad_bottom
        padding[3],                      # pad_right
        sh[2] if len(sh) >= 4 else 1,    # in_h
        sh[3] if len(sh) >= 4 else 1,    # in_w
        osh[2] if len(osh) >= 4 else 1,  # out_h
        osh[3] if len(osh) >= 4 else 1,  # out_w
        sh[1] if len(sh) >= 2 else 1,    # channels_in
        osh[1] if len(osh) >= 2 else 1,  # channels_out
        layer.get("groups", 1),          # groups
        0 if input_layout == "nchw" else 1,
        0,                               # reserved
        0,                               # reserved
    ]
    return struct.pack(f"<{_CONV_META_FIELDS}I", *fields)


# ── Individual op mappers ──────────────────────────────────────────────────


def _map_conv(ctx: _Ctx, layer: dict[str, Any]) -> int:
    M, K, N = _conv_gemm_dims(layer)

    # Input = previous layer's output (already stored, no new buffer)
    input_buf = ctx.last_buf
    # Weight and output need unique IDs
    weight_buf = ctx.temp_buf(max((K * N) // 2, 1))
    output_buf = ctx.declare_output(M * N * 4)

    ctx.blob.add_mmul(
        input_buf, weight_buf, output_buf, 0, M, K, N, deps=ctx._deps(),
    )
    ctx.num_commands += 1
    return output_buf


def _map_hardswish(ctx: _Ctx, layer: dict[str, Any]) -> int:
    elements = _dim_product(layer.get("in_shape"))
    inp = ctx.last_buf

    # 1) SFU_RELU (uses scratch for intermediate that's consumed immediately)
    relu_buf = ctx.temp_buf(elements * 2)
    ctx.blob.add_sfu(_SFU_RELU, inp, relu_buf, elements, deps=ctx._deps())
    ctx.num_commands += 1

    # 2) VMUL(relu_output, input) — output is permanent
    mul_buf = ctx.declare_output(elements * 4)
    ctx.blob.add_vector(
        _VEC_VMUL, relu_buf, inp, mul_buf, elements,
        deps=[relu_buf, inp],
    )
    ctx.num_commands += 1
    return mul_buf


def _map_hardsigmoid(ctx: _Ctx, layer: dict[str, Any]) -> int:
    elements = _dim_product(layer.get("in_shape"))
    inp = ctx.last_buf

    relu_buf = ctx.temp_buf(elements * 2)
    ctx.blob.add_sfu(_SFU_RELU, inp, relu_buf, elements, deps=ctx._deps())
    ctx.num_commands += 1

    mul_buf = ctx.declare_output(elements * 4)
    ctx.blob.add_vector(
        _VEC_VMUL, relu_buf, inp, mul_buf, elements,
        deps=[relu_buf, inp],
    )
    ctx.num_commands += 1
    return mul_buf


def _map_global_avg_pool(ctx: _Ctx, layer: dict[str, Any]) -> int:
    sh = layer.get("in_shape")
    if sh and len(sh) >= 4:
        C = sh[1] if sh[1] > 0 else 1
        spatial = (sh[2] if sh[2] > 0 else 1) * (sh[3] if sh[3] > 0 else 1)
    else:
        C = 1
        spatial = 1

    total_in = C * spatial
    inp = ctx.last_buf

    zero_buf = ctx.temp_buf(total_in * 4)
    mean_buf = ctx.declare_output(C * 4)
    ctx.blob.add_vector(
        _VEC_VRED_SUM, inp, zero_buf, mean_buf, total_in,
        deps=[inp] if inp else None,
    )
    ctx.num_commands += 1
    return mean_buf


def _map_relu(ctx: _Ctx, layer: dict[str, Any]) -> int:
    elements = _dim_product(layer.get("in_shape"))
    inp = ctx.last_buf
    relu_buf = ctx.declare_output(elements * 2)  # permanent: feeds next layer
    ctx.blob.add_sfu(_SFU_RELU, inp, relu_buf, elements, deps=ctx._deps())
    ctx.num_commands += 1
    return relu_buf


def _map_mul(ctx: _Ctx, layer: dict[str, Any]) -> int:
    elements = _dim_product(layer.get("in_shape"))
    a_buf = ctx.last_buf
    b_buf = ctx.second_last_buf if ctx.second_last_buf else ctx.last_buf
    mul_buf = ctx.declare_output(elements * 4)
    ctx.blob.add_vector(
        _VEC_VMUL, a_buf, b_buf, mul_buf, elements,
        deps=[a_buf, b_buf],
    )
    ctx.num_commands += 1
    return mul_buf


def _map_add(ctx: _Ctx, layer: dict[str, Any]) -> int:
    elements = _dim_product(layer.get("in_shape"))
    a_buf = ctx.last_buf
    b_buf = ctx.second_last_buf if ctx.second_last_buf else ctx.last_buf
    add_buf = ctx.declare_output(elements * 4)
    ctx.blob.add_vector(
        _VEC_VADD, a_buf, b_buf, add_buf, elements,
        deps=[a_buf, b_buf],
    )
    ctx.num_commands += 1
    return add_buf


def _map_reduce_mean(ctx: _Ctx, layer: dict[str, Any]) -> int:
    sh = layer.get("in_shape")
    elements = _dim_product(sh)
    channels = sh[1] if sh and len(sh) >= 4 and sh[1] > 0 else 1
    inp = ctx.last_buf

    zero_buf = ctx.temp_buf(elements * 4)
    mean_buf = ctx.declare_output(channels * 4)  # permanent
    ctx.blob.add_vector(
        _VEC_VRED_SUM, inp, zero_buf, mean_buf, elements,
        deps=[inp] if inp else None,
    )
    ctx.num_commands += 1
    return mean_buf


def _map_gemm(ctx: _Ctx, layer: dict[str, Any]) -> int:
    sh = layer.get("in_shape")
    osh = layer.get("out_shape")
    if sh and osh and len(sh) >= 2 and len(osh) >= 2:
        M = sh[0] if sh[0] > 0 else 1
        K = sh[1] if sh[1] > 0 else 1
        N = osh[1] if osh[1] > 0 else 1
    else:
        M, K, N = 1, 1, 1

    input_buf = ctx.last_buf
    weight_buf = ctx.temp_buf(max((K * N) // 2, 1))
    output_buf = ctx.declare_output(M * N * 4)

    ctx.blob.add_mmul(
        input_buf, weight_buf, output_buf, 0, M, K, N, deps=ctx._deps(),
    )
    ctx.num_commands += 1
    return output_buf


# ── Operation dispatch table ───────────────────────────────────────────────

_OP_MAP: dict[str, Any] = {
    "Conv":               _map_conv,
    "depthwise_conv":     _map_conv,
    "HardSwish":          _map_hardswish,
    "HardSigmoid":        _map_hardsigmoid,
    "GlobalAveragePool":  _map_global_avg_pool,
    "Relu":               _map_relu,
    "Mul":                _map_mul,
    "Add":                _map_add,
    "ReduceMean":         _map_reduce_mean,
    "Gemm":               _map_gemm,
}

# Ops that produce no compute commands (metamorph / reshape).
_PASSTHROUGH_TYPES = frozenset({
    "Reshape", "Squeeze", "Shape", "Concat", "MaxPool",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def convert_layer_list(layers: list[dict[str, Any]], input_shape: list[int] | None = None) -> bytes:
    """Convert a list of ONNX layer dicts (as returned by ``onnx_importer``)
    to an encoded command blob.

    This is the core conversion routine.  ``convert_mobilenetv3_graph()``
    wraps it with an ONNX file load.

    Parameters
    ----------
    layers : list[dict]
        One dict per layer, matching the schema of ``onnx_importer`` output.
    input_shape : list[int] | None
        Input tensor shape.  Defaults to ``[1, 3, 224, 224]`` (MobileNetV3).

    Returns
    -------
    bytes
        Encoded command blob.
    """
    if input_shape is None:
        input_shape = [1, 3, 224, 224]

    CommandBlob, LowerStatus, CAP_DMA, CAP_MXU, CAP_SFU, CAP_VECTOR = _get_ir()

    caps = CAP_MXU | CAP_SFU | CAP_VECTOR | CAP_DMA
    blob = CommandBlob(caps=caps)
    ctx = _Ctx(blob)

    # Initial input buffer representing the model input in DRAM.
    input_elements = 1
    for d in input_shape:
        if d > 0:
            input_elements *= d
    ctx.last_buf = ctx.declare(input_elements * 4, 64, host_addr=_abi.Addr.DRAM)

    for layer in layers:
        op_type = layer.get("type", "")
        name = layer.get("name", "?")

        if op_type in _PASSTHROUGH_TYPES:
            continue

        mapper = _OP_MAP.get(op_type)
        if mapper is None:
            raise UnsupportedCVOp(
                f"Operator '{op_type}' (layer '{name}') cannot be mapped "
                f"to Caduceus NPU primitives. Supported: {sorted(_OP_MAP)}"
            )

        prev = ctx.last_buf
        output_buf = mapper(ctx, layer)
        ctx.second_last_buf = prev
        ctx.last_buf = output_buf

    # Terminal barrier
    blob.add_barrier()
    ctx.num_commands += 1

    status = blob.lower()
    if status != LowerStatus.OK:
        raise RuntimeError(f"Blob lowering failed: {status!r}")

    return blob.encode()


def convert_mobilenetv3_graph(onnx_path: str) -> bytes:
    """Load a MobileNetV3-Small ONNX file and convert to an encoded command blob.

    Parameters
    ----------
    onnx_path : str
        Path to the ``.onnx`` file (e.g. ``assets/mobilenetv3_small.onnx``).

    Returns
    -------
    bytes
        Versioned, self-describing command blob.

    Raises
    ------
    FileNotFoundError
        When *onnx_path* does not exist.
    UnsupportedCVOp
        When an ONNX operator in the graph cannot be mapped.
    RuntimeError
        When blob lowering fails.
    """
    if not os.path.exists(onnx_path):
        raise FileNotFoundError(
            f"ONNX model not found at '{onnx_path}'. "
            f"Run scripts/export_mobilenetv3_onnx.py or place "
            f"assets/mobilenetv3_small.onnx."
        )

    from .onnx_importer import import_mobilenetv3

    layers = import_mobilenetv3(onnx_path)
    return convert_layer_list(layers)


def convert_mobilenetv3_graph_full(
    onnx_path: str, dram_base: int = 0x8010_0000,
) -> tuple[bytes, dict[int, dict], list[tuple[int, tuple[int, int, int]]], dict[int, Any]]:
    """Convert the full MobileNetV3-Small graph with unique sequential DRAM
    addresses and float32 buffer sizes, suitable for E2E correctness testing.

    Unlike ``convert_mobilenetv3_graph()`` (which assigns all DRAM buffers to
    ``host_addr=0x80000000`` for round-trip compatibility), this function
    assigns each buffer a unique address starting from *dram_base* so that
    real input/weight/output data can be written and read through the Host
    Runtime protocol without address collisions.

    Buffer sizes use float32 format throughout (weight buffers are ``K*N*4``
    instead of ``(K*N)//2``, SFU scratch uses ``elements*4`` instead of
    ``elements*2``) so the device server can execute via a pure float32
    golden path and produce logits comparable with ONNX Runtime.

    Conv and Gemm layers that carry a bias tensor in the ONNX model get an
    additional VADD command after the MMUL, with the bias stored in its own
    float32 buffer.  Weights are reshaped from ``[C_out, C_in, KH, KW]`` to
    the im2col GEMM layout ``[K, N]`` automatically.

    Parameters
    ----------
    onnx_path : str
        Path to ``assets/mobilenetv3_small.onnx``.
    dram_base : int
        First DRAM address to assign (default 0x80100000).

    Returns
    -------
    (blob_bytes, buffer_map, weight_map, bias_map)
        *blob_bytes* is the encoded command blob.
        *buffer_map* maps buffer ID → ``{"phys_addr": int, "size": int,
        "alignment": int}``.
        *weight_map* is ``[(buf_id, (M, K, N)), ...]`` listing every MMUL
        weight buffer in layer order.
        *bias_map* is ``{buf_id: numpy_float32_array}`` for bias buffers.
    """
    import numpy as np
    import onnx as _onnx_module

    if not os.path.exists(onnx_path):
        raise FileNotFoundError(
            f"ONNX model not found at '{onnx_path}'."
        )

    onnx_model = _onnx_module.load(onnx_path)
    init_map: dict[str, Any] = {}
    for init in onnx_model.graph.initializer:
        arr = _onnx_module.numpy_helper.to_array(init)
        init_map[init.name] = arr

    weight_by_output: dict[str, np.ndarray] = {}
    bias_by_output: dict[str, np.ndarray] = {}
    for node in onnx_model.graph.node:
        if node.op_type in ("Conv", "Gemm"):
            w_name = node.input[1]
            if w_name in init_map:
                weight_by_output[node.name] = init_map[w_name]
            if len(node.input) > 2:
                b_name = node.input[2]
                if b_name in init_map:
                    bias_by_output[node.name] = init_map[b_name]

    from .onnx_importer import import_mobilenetv3

    CommandBlob, LowerStatus, CAP_DMA, CAP_MXU, CAP_SFU, CAP_VECTOR = _get_ir()
    caps = CAP_MXU | CAP_SFU | CAP_VECTOR | CAP_DMA
    blob = CommandBlob(caps=caps)

    layers = import_mobilenetv3(onnx_path)

    # Build tensor-name -> producing layer index and per-node I/O maps so
    # elementwise ops with side inputs (SE Mul, residual Add) wire buffers
    # correctly instead of guessing from last/second-last outputs.
    node_inputs: dict[str, list[str]] = {}
    node_outputs: dict[str, str] = {}
    tensor_to_layer: dict[str, int] = {}
    node_by_name = {node.name: node for node in onnx_model.graph.node}
    for idx, layer in enumerate(layers):
        node = node_by_name.get(layer.get("name", ""))
        if node is None:
            continue
        node_inputs[node.name] = list(node.input)
        node_outputs[node.name] = node.output[0]
        tensor_to_layer[node.output[0]] = idx

    input_name = onnx_model.graph.input[0].name
    input_shape = [1, 3, 224, 224]
    input_elements = 1
    for d in input_shape:
        if d > 0:
            input_elements *= d

    ctx = _CtxF32(
        blob,
        dram_base=dram_base,
        input_name=input_name,
        tensor_to_layer=tensor_to_layer,
        node_inputs=node_inputs,
        node_outputs=node_outputs,
    )
    ctx.last_buf = ctx.declare(input_elements * 4)
    ctx._input_buf = ctx.last_buf

    for layer_idx, layer in enumerate(layers):
        op_type = layer.get("type", "")
        name = layer.get("name", "?")

        if op_type in _PASSTHROUGH_TYPES:
            continue

        bias_tensor = bias_by_output.get(layer.get("name"))
        weight_tensor = weight_by_output.get(layer.get("name"))

        mapper = _F32_OP_MAP.get(op_type)
        if mapper is None:
            raise UnsupportedCVOp(
                f"Operator '{op_type}' (layer '{name}') cannot be mapped "
                f"to Caduceus NPU primitives. Supported: {sorted(_F32_OP_MAP)}"
            )

        prev = ctx.last_buf
        if op_type in ("Conv", "depthwise_conv"):
            output_buf = _f32_map_conv(
                ctx, layer, bias_tensor=bias_tensor,
                weight_tensor=weight_tensor,
            )
        elif op_type == "Gemm":
            output_buf = _f32_map_gemm(
                ctx, layer, bias_tensor=bias_tensor,
                weight_tensor=weight_tensor,
            )
        else:
            output_buf = mapper(ctx, layer)
        ctx._record_layer_output(layer_idx, output_buf)
        ctx.second_last_buf = prev
        ctx.last_buf = output_buf

    blob.add_barrier()

    status = blob.lower()
    if status != LowerStatus.OK:
        raise RuntimeError(f"Blob lowering failed: {status!r}")

    encoded = blob.encode()
    buffer_map = ctx.buffer_map()
    weight_map = ctx.weight_map()
    bias_map = ctx.bias_map()
    scale_map = ctx.scale_map()
    return encoded, buffer_map, weight_map, bias_map, scale_map


# ── F32 (float32) context and mappers ───────────────────────────────────────


class _CtxF32:
    """Mutable context for float32 full-graph conversion.

    Like ``_Ctx`` but assigns unique sequential DRAM addresses and uses
    float32 buffer sizes (weight = K*N*4, SFU scratch = elements*4).
    """

    def __init__(
        self,
        blob,
        dram_base: int = 0x8010_0000,
        input_name: str = "",
        tensor_to_layer: dict[str, int] | None = None,
        node_inputs: dict[str, list[str]] | None = None,
        node_outputs: dict[str, str] | None = None,
    ) -> None:
        self.blob = blob
        self.last_buf: int = 0
        self.second_last_buf: int = 0
        self._input_buf: int = 0
        self._input_name: str = input_name
        self._tensor_to_layer: dict[str, int] = tensor_to_layer or {}
        self._node_inputs: dict[str, list[str]] = node_inputs or {}
        self._node_outputs: dict[str, str] = node_outputs or {}
        self._layer_output_buf: dict[int, int] = {}
        self._next_addr: int = dram_base
        self._buf_phys: dict[int, int] = {}
        self._buf_size: dict[int, int] = {}
        self._buf_align: dict[int, int] = {}
        self._bias_data: list[tuple[int, Any]] = []
        self._weight_data: list[tuple[int, Any, tuple[int, int, int], Any]] = []
        self._scale_data: list[tuple[int, Any]] = []
        self._temp_pool: list[int] = []
        self._temp_next: int = 0

    def _record_layer_output(self, layer_idx: int, buf_id: int) -> None:
        self._layer_output_buf[layer_idx] = buf_id

    def _buf_for_tensor(self, name: str) -> int | None:
        if name == self._input_name:
            return self._input_buf
        layer_idx = self._tensor_to_layer.get(name)
        if layer_idx is None:
            return None
        return self._layer_output_buf.get(layer_idx)

    def _align(self, v: int, a: int = 64) -> int:
        return (v + a - 1) & ~(a - 1)

    def declare(self, size: int, alignment: int = 64) -> int:
        addr = self._align(self._next_addr, alignment)
        bid = self.blob.declare_buffer(size, alignment, host_addr=addr)
        self._buf_phys[bid] = addr
        self._buf_size[bid] = size
        self._buf_align[bid] = alignment
        self._next_addr = addr + self._align(size, alignment)
        return bid

    def temp_buf(self, size: int) -> int:
        pool_size = 16
        if not self._temp_pool:
            pool_sz = max(size, 512 * 1024)
            for _i in range(pool_size):
                addr = self._align(self._next_addr, 64)
                bid = self.blob.declare_buffer(pool_sz, 64, host_addr=addr)
                self._temp_pool.append(bid)
                self._buf_phys[bid] = addr
                self._buf_size[bid] = pool_sz
                self._buf_align[bid] = 64
                self._next_addr = addr + self._align(pool_sz, 64)
        idx = self._temp_next % pool_size
        self._temp_next += 1
        return self._temp_pool[idx]

    def buffer_map(self) -> dict[int, dict]:
        return {
            bid: {"phys_addr": self._buf_phys[bid],
                   "size": self._buf_size[bid],
                   "alignment": self._buf_align[bid]}
            for bid in self._buf_phys
        }

    def bias_map(self) -> dict[int, Any]:
        return {bid: tensor for bid, tensor in self._bias_data}

    def scale_map(self) -> dict[int, Any]:
        return {bid: tensor for bid, tensor in self._scale_data}

    def weight_map(self) -> list[tuple[int, Any, tuple[int, int, int], Any]]:
        return list(self._weight_data)

    def _deps(self) -> list[int] | None:
        return [self.last_buf] if self.last_buf else None


# ── F32 op mappers (float32 buffer sizes) ───────────────────────────────────


def _f32_map_conv(
    ctx: _CtxF32, layer: dict[str, Any],
    bias_tensor: Any = None,
    weight_tensor: Any = None,
) -> int:
    """Map a Conv layer → MMUL (+ optional bias VADD) with float32 buffers.

    Pointwise (1×1) convolutions keep the simple GEMM layout and execute as a
    plain matmul.  Standard and depthwise convolutions append a metadata footer
    to the weight buffer so the F32 executor can perform real im2col; this
    fixes the B5 depthwise bug where a single MMUL reused one kernel across
    all channels.
    """
    import numpy as np

    M, K, N = _conv_gemm_dims(layer)
    input_buf = ctx.last_buf

    groups = layer.get("groups") or 1
    in_shape = layer.get("in_shape") or [1, 1, 1, 1]
    is_depthwise = groups > 1 and groups == in_shape[1]
    kernel = layer.get("kernel") or [1, 1]
    is_pointwise = kernel == [1, 1] and groups == 1

    wt_data: Any = None
    meta: Any = None
    if is_pointwise:
        # 1×1 conv: plain GEMM, weight in [K, N] layout.
        weight_buf = ctx.declare(K * N * 4)
        if weight_tensor is not None:
            if weight_tensor.ndim == 4:
                c_out = weight_tensor.shape[0]
                wt_data = weight_tensor.reshape(c_out, K).T.astype(np.float32)
            elif weight_tensor.ndim == 2:
                wt_data = weight_tensor.T.astype(np.float32)
            else:
                wt_data = weight_tensor.astype(np.float32)
            if wt_data.size != K * N:
                wt_data = wt_data.ravel()[:K * N].reshape(K, N)
    else:
        # Standard / depthwise conv: store original weight plus metadata footer.
        # Internal activations remain NHWC; the executor transposes to NCHW for
        # im2col only when the source buffer is the model input.
        op_kind = 2 if is_depthwise else 1
        input_layout = "nchw" if input_buf == ctx._input_buf else "nhwc"
        meta = _pack_conv_meta(op_kind, layer, input_layout)

        core_bytes = K * N * 4
        if weight_tensor is not None:
            wt_raw = np.asarray(weight_tensor, dtype=np.float32).tobytes()
            core_bytes = max(core_bytes, len(wt_raw))
        else:
            wt_raw = b""
        weight_buf = ctx.declare(core_bytes + _CONV_META_SIZE)
        wt_data = wt_raw + meta

    ctx._weight_data.append((weight_buf, wt_data, (M, K, N), meta))

    if bias_tensor is not None:
        mmul_out = ctx.temp_buf(M * N * 4)
        ctx.blob.add_mmul(
            input_buf, weight_buf, mmul_out, 0, M, K, N, deps=ctx._deps(),
        )
        bias_len = int(np.asarray(bias_tensor).size)
        bias_buf = ctx.declare(bias_len * 4)
        out_buf = ctx.declare(M * N * 4)
        ctx.blob.add_vector(
            _VEC_VADD, mmul_out, bias_buf, out_buf, M * N,
            deps=[mmul_out],
        )
        ctx._bias_data.append((bias_buf, bias_tensor.astype(np.float32).ravel()[:bias_len]))
        return out_buf
    mmul_out = ctx.declare(M * N * 4)
    ctx.blob.add_mmul(
        input_buf, weight_buf, mmul_out, 0, M, K, N, deps=ctx._deps(),
    )
    return mmul_out


def _f32_map_hardswish(ctx: _CtxF32, layer: dict[str, Any]) -> int:
    elements = _dim_product(layer.get("in_shape"))
    inp = ctx.last_buf
    out_buf = ctx.declare(elements * 4)
    ctx.blob.add_sfu(
        _SFU_HARDSWISH, inp, out_buf, elements, deps=ctx._deps()
    )
    return out_buf


def _f32_map_hardsigmoid(ctx: _CtxF32, layer: dict[str, Any]) -> int:
    elements = _dim_product(layer.get("in_shape"))
    inp = ctx.last_buf
    out_buf = ctx.declare(elements * 4)
    ctx.blob.add_sfu(
        _SFU_HARDSIGMOID, inp, out_buf, elements, deps=ctx._deps()
    )
    return out_buf


def _f32_map_global_avg_pool(ctx: _CtxF32, layer: dict[str, Any]) -> int:
    import numpy as np

    sh = layer.get("in_shape")
    C = (sh[1] if sh and len(sh) >= 4 and sh[1] > 0 else 1)
    spatial = ((sh[2] if sh and len(sh) >= 4 and sh[2] > 0 else 1)
               * (sh[3] if sh and len(sh) >= 4 and sh[3] > 0 else 1))
    total_in = C * spatial
    inp = ctx.last_buf
    mean_buf = ctx.declare(C * 4)
    # A zero b_id tells the F32 executor that the input is NHWC, so it must
    # reduce over the non-contiguous spatial positions inside each channel.
    ctx.blob.add_vector(
        _VEC_VRED_SUM, inp, 0, mean_buf, total_in,
        deps=[inp] if inp else None,
    )
    return mean_buf


def _f32_map_relu(ctx: _CtxF32, layer: dict[str, Any]) -> int:
    elements = _dim_product(layer.get("in_shape"))
    inp = ctx.last_buf
    relu_buf = ctx.declare(elements * 4)
    ctx.blob.add_sfu(_SFU_RELU, inp, relu_buf, elements, deps=ctx._deps())
    return relu_buf


def _resolve_binary_bufs(ctx: _CtxF32, layer_name: str) -> tuple[int, int]:
    """Return the two dynamic input buffers for a Mul/Add node.

    Uses the ONNX node input names so side branches (SE scale, residuals)
    wire to the correct tensors instead of the most recent output.
    """
    names = ctx._node_inputs.get(layer_name, [])
    bufs = [b for n in names if (b := ctx._buf_for_tensor(n)) is not None]
    if len(bufs) < 2:
        # Fall back to the sequential heuristic for graphs without node maps.
        return (ctx.last_buf, ctx.second_last_buf or ctx.last_buf)
    if len(bufs) > 2:
        bufs = bufs[:2]
    # Put the larger operand first so the vector broadcast path tiles the
    # smaller operand (e.g. a 1x1xC scale) across the feature map.
    bufs = sorted(bufs, key=lambda b: ctx._buf_size.get(b, 0), reverse=True)
    return (bufs[0], bufs[1])


def _f32_map_mul(ctx: _CtxF32, layer: dict[str, Any]) -> int:
    # Mul/Add may broadcast a 1x1xC vector to a feature map, so the output
    # shape (not the smaller input shape) determines the buffer size.
    elements = _dim_product(layer.get("out_shape"))
    a_buf, b_buf = _resolve_binary_bufs(ctx, layer.get("name", ""))
    mul_buf = ctx.declare(elements * 4)
    ctx.blob.add_vector(
        _VEC_VMUL, a_buf, b_buf, mul_buf, elements,
        deps=[a_buf, b_buf],
    )
    return mul_buf


def _f32_map_add(ctx: _CtxF32, layer: dict[str, Any]) -> int:
    elements = _dim_product(layer.get("out_shape"))
    a_buf, b_buf = _resolve_binary_bufs(ctx, layer.get("name", ""))
    add_buf = ctx.declare(elements * 4)
    ctx.blob.add_vector(
        _VEC_VADD, a_buf, b_buf, add_buf, elements,
        deps=[a_buf, b_buf],
    )
    return add_buf


def _f32_map_reduce_mean(ctx: _CtxF32, layer: dict[str, Any]) -> int:
    """ReduceMean over spatial dims → one per-channel mean per batch."""
    sh = layer.get("in_shape")
    elements = _dim_product(sh)
    channels = sh[1] if sh and len(sh) >= 4 and sh[1] > 0 else 1
    inp = ctx.last_buf
    out_buf = ctx.declare(channels * 4)
    # b_id == 0 selects the NHWC-aware reduction path in the F32 executor.
    ctx.blob.add_vector(
        _VEC_VRED_SUM, inp, 0, out_buf, elements,
        deps=[inp] if inp else None,
    )
    return out_buf


def _f32_map_gemm(
    ctx: _CtxF32, layer: dict[str, Any],
    bias_tensor: Any = None,
    weight_tensor: Any = None,
) -> int:
    import numpy as np

    sh = layer.get("in_shape")
    osh = layer.get("out_shape")
    if sh and osh and len(sh) >= 2 and len(osh) >= 2:
        M = sh[0] if sh[0] > 0 else 1
        K = sh[1] if sh[1] > 0 else 1
        N = osh[1] if osh[1] > 0 else 1
    else:
        M, K, N = 1, 1, 1
    input_buf = ctx.last_buf
    weight_buf = ctx.declare(K * N * 4)

    wt_data = None
    if weight_tensor is not None:
        if weight_tensor.ndim == 2:
            wt_data = weight_tensor.T.astype(np.float32)
        else:
            wt_data = weight_tensor.astype(np.float32).ravel()[:K * N].reshape(K, N)
    ctx._weight_data.append((weight_buf, wt_data, (M, K, N), None))

    if bias_tensor is not None:
        mmul_out = ctx.temp_buf(M * N * 4)
        ctx.blob.add_mmul(
            input_buf, weight_buf, mmul_out, 0, M, K, N, deps=ctx._deps(),
        )
        bias_len = int(np.asarray(bias_tensor).size)
        bias_buf = ctx.declare(bias_len * 4)
        out_buf = ctx.declare(M * N * 4)
        ctx.blob.add_vector(
            _VEC_VADD, mmul_out, bias_buf, out_buf, M * N,
            deps=[mmul_out],
        )
        ctx._bias_data.append((bias_buf, bias_tensor.astype(np.float32).ravel()[:bias_len]))
        return out_buf
    mmul_out = ctx.declare(M * N * 4)
    ctx.blob.add_mmul(
        input_buf, weight_buf, mmul_out, 0, M, K, N, deps=ctx._deps(),
    )
    return mmul_out


_F32_OP_MAP: dict[str, Any] = {
    "Conv":               _f32_map_conv,
    "depthwise_conv":     _f32_map_conv,
    "HardSwish":          _f32_map_hardswish,
    "HardSigmoid":        _f32_map_hardsigmoid,
    "GlobalAveragePool":  _f32_map_global_avg_pool,
    "Relu":               _f32_map_relu,
    "Mul":                _f32_map_mul,
    "Add":                _f32_map_add,
    "ReduceMean":         _f32_map_reduce_mean,
    "Gemm":               _f32_map_gemm,
}


def decode_cv_blob(data: bytes) -> Any:
    """Decode an encoded command blob back to a ``CommandBlob``.

    Convenience helper for round-trip tests::

        original = encode(graph)
        decoded  = decode_cv_blob(original)
        assert decoded.num_commands() == expected_count
    """
    CommandBlob, _LowerStatus, _0, _1, _2, _3 = _get_ir()
    return CommandBlob.decode(data)
