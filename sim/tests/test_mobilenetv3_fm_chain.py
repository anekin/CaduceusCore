"""MobileNetV3 CV chain FM gate (E2E-05, todo 12 of fm-soc-datapath-hardening).

Runs the FULL MobileNetV3-Small conv chain through the FuncModel doorbell
command ring: every Conv / depthwise / Gemm layer is issued as one or more
MMUL ring commands via ``FuncModel.host_write_command`` + ``firmware.run_loop``
and every per-layer output is compared against the
``GoldenMXU.matmul_int4_per_block`` golden reference (cos_sim >= 0.99 for all
>=15 conv layers).

Design notes
------------
- Topology comes from ``sim/cv/``: ``cv.onnx_importer.import_mobilenetv3``
  (per-layer shapes / kernel / stride / padding / groups) and
  ``cv.cv_trace.generate_mobilenetv3_trace`` (GEMM M/K/N per layer via
  ``map_conv_to_gemm``).  Real weights/bias are loaded from the ONNX
  initializers so the chain is the genuine MobileNetV3-Small dataflow.
- Non-conv operators between convs (HardSwish, HardSigmoid, Relu, SE
  ReduceMean/Mul, residual Add, head ReduceMean/Reshape) are evaluated with
  identical numpy code in BOTH the golden chain and the ring chain — only the
  GEMM layers themselves go through the ring, which is what the
  ``GoldenMXU.matmul_int4_per_block`` golden can verify.
- INT4 per-block quantization (group_size=128) + per-tensor symmetric INT8
  activation quantization, mirroring the W3.4 golden pattern in
  ``sim/tests/test_cv_mobilenetv3.py``.  The activation scale is folded into
  the weight block scales (the firmware MMUL dispatcher applies FP32 scales
  but has no activation-scale path).
- Data layouts (verified bit-exact against the bridge ``_run_mxu_compute``):
  activations are staged in the mxu_soc_wrapper broadcast tile-major layout
  (``pack_int8_activation_tile_major``, 64 K-indices per 4096-byte tile, max
  64 M-rows per tile); weights live in firmware 128x128 tile order at fixed
  8192-byte slot offsets ``(n_tile * num_blocks + k_block) * TILE_WEIGHT_BYTES``;
  block scales live in fixed 512-byte slots with the same indexing.
- Each ring MMUL is chunked to M <= 64 rows (broadcast layout limit) and
  N <= 128 columns.  The N <= 128 limit is a WORKAROUND for a pre-existing
  ``tile_scheduler.tile_mmul`` gap: its output-tile offset is
  ``out_sram + n_start * 4`` (column stride), which only matches the tile
  size for M == 1 — for M > 1 the second N-tile overlaps and clobbers the
  first (SRAM accumulator AND DRAM output).  See learnings.
- Depthwise convs are scheduled per input channel (K = kh*kw, N = 1), the
  same decomposition the W3.4 golden uses.
- Failure injection: the weight address of one mid-chain pointwise conv's
  descriptors is tampered (+64 bytes into its own weight buffer) — that layer
  must mismatch golden (cos < 0.99) while every other layer stays bit-exact
  (downstream layers chain from the corrupted value in BOTH paths).

No changes to ``sim/cv/``, ``sim/`` model code, or the scheduling algorithm.
"""

import sys
import time
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Path / environment setup
# ---------------------------------------------------------------------------

_sim_dir = Path(__file__).resolve().parent.parent
if str(_sim_dir) not in sys.path:
    sys.path.insert(0, str(_sim_dir))
_repo_root = _sim_dir.parent

_ONNX_PATH = _repo_root / "assets" / "mobilenetv3_small.onnx"
# The exporter stores initializer VALUES in an external data file; both are
# the model.  Missing either => skip with the mandated reason.
_ONNX_DATA_PATH = _repo_root / "assets" / "mobilenetv3_small.onnx.data"
if not (_ONNX_PATH.is_file() and _ONNX_DATA_PATH.is_file()):
    pytest.skip("MobileNetV3 ONNX model not found", allow_module_level=True)

import onnx  # noqa: E402

from cv.cv_trace import generate_mobilenetv3_trace  # noqa: E402
from cv.onnx_importer import import_mobilenetv3  # noqa: E402
from cocotb_bridge import pack_int8_activation_tile_major  # noqa: E402
from engine.isa import OpCode  # noqa: E402
from func_model import FuncModel  # noqa: E402
from golden_executor import GoldenMXU  # noqa: E402
from quantize import quantize_int4_per_block  # noqa: E402
from tile_scheduler import TILE_SCALE_BYTES, TILE_WEIGHT_BYTES  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROUP_SIZE = 128       # INT4 per-block quantization group (matches hardware)
_M_CHUNK = 64          # broadcast activation layout holds at most 64 M-rows/tile
_N_CHUNK = 128         # see docstring: tile_mmul out-stride is M==1-only

# DRAM data regions (test-local; ring buffer lives at 0x8000_0000, 16x32B).
_ACT_ADDR = 0x8001_0000      # 64 KB broadcast-layout activation staging
_WGT_ADDR = 0x8002_0000      # 64 KB firmware-tiled weight slots
_SCALE_ADDR = 0x8004_0000    # 4 KB scale slots
_OUT_ADDR = 0x8005_0000      # 32 KB MMUL output (M_chunk x N_chunk float32)
_DESC_ADDR = 0x8006_0000     # single reused descriptor slot (issue->drain)

_CORRUPT_WADDR_OFFSET = 64   # failure injection: shift the weight pointer


# ---------------------------------------------------------------------------
# im2col / quantization / similarity (W3.4 golden pattern, numpy only)
# ---------------------------------------------------------------------------

def im2col_conv2d(x, weight_shape, stride, padding, groups):
    """Flatten (N,C,H,W) activation into (M, K) for GEMM via im2col.

    Column blocks are ordered by group (col0 = g * K_per_group) so a
    grouped / depthwise weight matrix stays block-diagonal.
    """
    N_val, C, H, W = x.shape
    _, _, kH, kW = weight_shape
    pH = padding[0] if isinstance(padding, (tuple, list)) else padding
    pW = padding[1] if isinstance(padding, (tuple, list)) else padding
    H_out = (H + 2 * pH - kH) // stride + 1
    W_out = (W + 2 * pW - kW) // stride + 1

    xp = np.pad(x, ((0, 0), (0, 0), (pH, pH), (pW, pW)), mode='constant')
    C_per_group = C // groups
    K_per_group = C_per_group * kH * kW
    M_per_group = N_val * H_out * W_out
    total_K = K_per_group * groups
    cols = np.zeros((M_per_group, total_K), dtype=np.float32)

    for g in range(groups):
        c0 = g * C_per_group
        c1 = c0 + C_per_group
        col0 = g * K_per_group
        col1 = col0 + K_per_group
        for i in range(H_out):
            for j in range(W_out):
                ii = i * stride
                jj = j * stride
                patch = xp[0, c0:c1, ii:ii + kH, jj:jj + kW]
                row = i * W_out + j
                cols[row, col0:col1] = patch.reshape(-1)
    return cols


def quantize_act_int8(x):
    """Per-tensor symmetric INT8 quantization. Returns (int8, scale)."""
    x_f32 = np.asarray(x, dtype=np.float32)
    max_abs = float(np.max(np.abs(x_f32)))
    if max_abs < 1e-12:
        return x_f32.astype(np.int8), 1.0
    scale = max_abs / 127.0
    q = np.clip(np.round(x_f32 / scale), -128, 127).astype(np.int8)
    return q, scale


def cos_sim(a, b):
    a_f = a.astype(np.float64).flatten()
    b_f = b.astype(np.float64).flatten()
    dot = float(np.dot(a_f, b_f))
    na = float(np.linalg.norm(a_f))
    nb = float(np.linalg.norm(b_f))
    if na < 1e-12 or nb < 1e-12:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Firmware data-layout packers (verified bit-exact against bridge compute)
# ---------------------------------------------------------------------------

def _pack_weights_fw_tiles(w_packed_dense: np.ndarray, K: int, N: int) -> bytes:
    """Dense packed (K,N) row-major -> firmware 128x128 tile slots.

    Slot (n_tile, k_block) lives at fixed offset
    ``(n_tile * num_blocks + k_block) * TILE_WEIGHT_BYTES`` — the same
    offset formula ``tile_scheduler.tile_mmul`` uses for its DMA reads.
    """
    mxu = GoldenMXU()
    w_flat = mxu.unpack_int4(np.frombuffer(w_packed_dense.tobytes(), dtype=np.uint8))
    W = w_flat[:K * N].reshape(K, N)
    num_blocks = (K + 127) // 128
    num_tiles = (N + 127) // 128
    out = bytearray()
    for nt in range(num_tiles):
        for kb in range(num_blocks):
            sub = W[kb * 128:(kb + 1) * 128, nt * 128:(nt + 1) * 128]
            if sub.shape[0] < 128:
                sub = np.pad(sub, ((0, 128 - sub.shape[0]), (0, 0)))
            slot = bytearray(TILE_WEIGHT_BYTES)
            slot[:sub.size // 2] = mxu.pack_int4(sub.flatten().astype(np.int8)).tobytes()
            out += slot
    return bytes(out)


def _pack_scales_fw_tiles(scales: np.ndarray, K: int, N: int) -> bytes:
    """Block scales (num_blocks, N) -> firmware 512-byte slots per (n_tile, k_block)."""
    num_blocks = (K + 127) // 128
    num_tiles = (N + 127) // 128
    out = bytearray()
    for nt in range(num_tiles):
        for kb in range(num_blocks):
            slot = bytearray(TILE_SCALE_BYTES)
            w = min(128, N - nt * 128)
            slot[:w * 4] = scales[kb, nt * 128:nt * 128 + w].astype(np.float32).tobytes()
            out += slot
    return bytes(out)


# ---------------------------------------------------------------------------
# Ring MMUL issue
# ---------------------------------------------------------------------------

def _issue_mmul(model, act_i8, w_packed_dense, scales, M, K, N,
                corrupt: bool = False):
    """Schedule one (already chunked) GEMM as ring MMUL commands.

    Returns (output (M, N) float32, number_of_ring_commands).
    """
    total_cmds = 0
    out = np.zeros((M, N), dtype=np.float32)
    mxu = GoldenMXU()
    for m0 in range(0, M, _M_CHUNK):
        m1 = min(m0 + _M_CHUNK, M)
        m_chunk = m1 - m0
        packed_act = pack_int8_activation_tile_major(
            act_i8[m0:m1].tobytes(), m_chunk, K)
        for n0 in range(0, N, _N_CHUNK):
            n1 = min(n0 + _N_CHUNK, N)
            n_chunk = n1 - n0
            if n_chunk == N:
                w_sub = w_packed_dense
            else:
                W_full = mxu.unpack_int4(
                    np.frombuffer(w_packed_dense.tobytes(), dtype=np.uint8)
                )[:K * N].reshape(K, N)
                w_sub = mxu.pack_int4(W_full[:, n0:n1].flatten().astype(np.int8))
            scales_sub = scales[:, n0:n1]
            w_tiled = _pack_weights_fw_tiles(w_sub, K, n_chunk)
            s_tiled = _pack_scales_fw_tiles(scales_sub, K, n_chunk)

            model.host_write_data(_ACT_ADDR, np.frombuffer(packed_act, dtype=np.uint8))
            model.host_write_data(_WGT_ADDR, np.frombuffer(w_tiled, dtype=np.uint8))
            model.host_write_data(_SCALE_ADDR, np.frombuffer(s_tiled, dtype=np.uint8))
            eff_waddr = _WGT_ADDR + _CORRUPT_WADDR_OFFSET if corrupt else _WGT_ADDR
            model.host_write_descriptor(
                _DESC_ADDR,
                input_addr=_ACT_ADDR, weight_addr=eff_waddr,
                output_addr=_OUT_ADDR, scale_addr=_SCALE_ADDR,
                input_size=len(packed_act), weight_size=(K * n_chunk + 1) // 2,
                output_size=m_chunk * n_chunk * 4, scale_size=scales_sub.size * 4,
                M=m_chunk, K=K, N=n_chunk,
            )
            model.host_write_command(OpCode.MMUL, _DESC_ADDR)
            results = model.firmware.run_loop(max_commands=1)
            assert len(results) == 1, f"firmware consumed {len(results)} commands"
            assert results[0]["status"] == "done", f"ring MMUL failed: {results[0]}"
            out[m0:m1, n0:n1] = np.frombuffer(
                model._dram_read(_OUT_ADDR, m_chunk * n_chunk * 4),
                dtype=np.float32,
            ).reshape(m_chunk, n_chunk)
            total_cmds += 1
    return out, total_cmds


# ---------------------------------------------------------------------------
# Per-conv golden + ring execution
# ---------------------------------------------------------------------------

def _conv_subproblems(x, w, layer):
    """Decompose one Conv/depthwise_conv layer into GEMM subproblems.

    Returns a list of dicts {cols_f32, w_packed, scales, M, K, N} covering
    the layer: one entry for pointwise/standard convs, one per input channel
    for depthwise convs (the W3.4 golden decomposition).
    """
    C_out, _, kH, kW = w.shape
    stride = int((layer.get("stride") or [1, 1])[0])
    padding = layer.get("padding") or [0, 0, 0, 0]
    groups = int(layer.get("groups") or 1)
    pH = padding[0]
    pW = padding[1]
    H_out = (x.shape[2] + 2 * pH - kH) // stride + 1
    W_out = (x.shape[3] + 2 * pW - kW) // stride + 1
    is_dw = layer.get("type") == "depthwise_conv"

    subs = []
    if is_dw:
        for c in range(C_out):
            x_c = x[:, c:c + 1, :, :]
            cols_c = im2col_conv2d(
                x_c, (1, 1, kH, kW), stride=stride, padding=padding, groups=1)
            w_c = w[c:c + 1, :, :, :].reshape(1, kH * kW).T.astype(np.float32)
            a_i8, a_s = quantize_act_int8(cols_c)
            w_p, w_sc, _ = quantize_int4_per_block(w_c, GROUP_SIZE)
            subs.append({
                "cols": a_i8, "w_packed": w_p, "scales": w_sc * a_s,
                "M": cols_c.shape[0], "K": kH * kW, "N": 1,
            })
        return subs, (1, C_out, H_out, W_out)

    cols = im2col_conv2d(x, w.shape, stride, padding, groups)
    if groups > 1:  # grouped (non-depthwise) — not used by MobileNetV3-Small
        raise NotImplementedError(f"grouped conv groups={groups} not expected")
    C_in = w.shape[1]
    W_2d = w.reshape(C_out, C_in * kH * kW).T.astype(np.float32)
    a_i8, a_s = quantize_act_int8(cols)
    w_p, w_sc, _ = quantize_int4_per_block(W_2d, GROUP_SIZE)
    subs.append({
        "cols": a_i8, "w_packed": w_p, "scales": w_sc * a_s,
        "M": cols.shape[0], "K": C_in * kH * kW, "N": C_out,
    })
    return subs, (1, C_out, H_out, W_out)


def _run_conv_layer(model, mxu, x, w, layer, corrupt=False):
    """Run one conv layer: golden (direct GoldenMXU) + ring (chunked MMUL).

    Returns (ring_raw_4d, golden_raw_4d, cmd_count, meta).  Bias is NOT
    included — both sides compare the raw ``matmul_int4_per_block`` output.
    """
    subs, out_shape = _conv_subproblems(x, w, layer)
    ring_parts = []
    golden_parts = []
    total_cmds = 0
    for sub in subs:
        golden = mxu.matmul_int4_per_block(
            sub["cols"], sub["w_packed"], sub["scales"],
            sub["M"], sub["K"], sub["N"], group_size=GROUP_SIZE,
        )
        ring, cmds = _issue_mmul(
            model, sub["cols"], sub["w_packed"], sub["scales"],
            sub["M"], sub["K"], sub["N"], corrupt=corrupt,
        )
        total_cmds += cmds
        golden_parts.append(golden)
        ring_parts.append(ring)

    if len(subs) == 1:
        # single GEMM: (M, N) -> (1, H, W, N) -> NCHW
        ring_4d = ring_parts[0].reshape(
            1, out_shape[2], out_shape[3], out_shape[1]).transpose(0, 3, 1, 2)
        golden_4d = golden_parts[0].reshape(
            1, out_shape[2], out_shape[3], out_shape[1]).transpose(0, 3, 1, 2)
    else:
        # depthwise: per-channel (M,1) -> (1, H, W) each -> stack to NCHW
        H_out, W_out = out_shape[2], out_shape[3]
        ring_4d = np.stack(
            [p.reshape(1, H_out, W_out) for p in ring_parts], axis=1)
        golden_4d = np.stack(
            [p.reshape(1, H_out, W_out) for p in golden_parts], axis=1)
    return ring_4d.astype(np.float32), golden_4d.astype(np.float32), total_cmds, out_shape


# ---------------------------------------------------------------------------
# Full-graph chain runner
# ---------------------------------------------------------------------------

def _layer_by_name(layers):
    return {l["name"]: l for l in layers}


def _run_chain(corrupt_conv_name=None):
    """Run the full MobileNetV3 graph once.

    Convs/Gemms execute through the doorbell ring (and are compared against
    the GoldenMXU reference); all other operators are evaluated with the same
    numpy code on both paths (they feed the next conv identically).

    Returns a list of per-GEMM-layer records (name, dims, cmd count, cos,
    exact, ring output) in graph order.
    """
    onnx_model = onnx.load(str(_ONNX_PATH))
    init_map = {i.name: onnx.numpy_helper.to_array(i) for i in onnx_model.graph.initializer}
    layers = _layer_by_name(import_mobilenetv3(str(_ONNX_PATH)))
    graph = onnx_model.graph

    rng = np.random.RandomState(42)
    x0 = rng.randn(1, 3, 224, 224).astype(np.float32)
    tensors = {graph.input[0].name: x0}

    model = FuncModel(dram_mb=256)
    mxu = GoldenMXU()
    records = []
    ring_size = model.firmware.ring_size
    assert ring_size == 16, "scenario assumes the FuncModel firmware 16-entry ring"

    for node in graph.node:
        op = node.op_type
        name = node.name
        out_name = node.output[0]
        if op in ("Conv", "depthwise_conv"):
            layer = layers[name]
            x = tensors[node.input[0]]
            w = init_map[node.input[1]]
            bias = init_map[node.input[2]] if len(node.input) > 2 else None
            ring_raw, golden_raw, cmds, out_shape = _run_conv_layer(
                model, mxu, x, w, layer, corrupt=(name == corrupt_conv_name))
            cos = cos_sim(ring_raw, golden_raw)
            exact = bool(np.array_equal(ring_raw, golden_raw))
            records.append({
                "name": name, "op": "conv", "cmds": cmds,
                "M": ring_raw.size // out_shape[1], "K": None, "N": out_shape[1],
                "cos": cos, "exact": exact, "out": ring_raw,
            })
            if bias is not None:
                ring_raw = ring_raw + bias.astype(np.float32).reshape(1, -1, 1, 1)
            tensors[out_name] = ring_raw
        elif op == "Gemm":
            x = tensors[node.input[0]]
            w = init_map[node.input[1]]
            bias = init_map[node.input[2]] if len(node.input) > 2 else None
            w_2d = w.T.astype(np.float32)  # ONNX Gemm transB=1
            K, N = w_2d.shape
            a_i8, a_s = quantize_act_int8(x.reshape(1, -1))
            w_p, w_sc, _ = quantize_int4_per_block(w_2d, GROUP_SIZE)
            scales = w_sc * a_s
            golden = mxu.matmul_int4_per_block(
                a_i8, w_p, scales, 1, K, N, group_size=GROUP_SIZE)
            ring, cmds = _issue_mmul(
                model, a_i8, w_p, scales, 1, K, N,
                corrupt=(name == corrupt_conv_name))
            cos = cos_sim(ring, golden)
            exact = bool(np.array_equal(ring, golden))
            records.append({
                "name": name, "op": "gemm", "cmds": cmds,
                "M": 1, "K": K, "N": N, "cos": cos, "exact": exact, "out": ring,
            })
            if bias is not None:
                ring = ring + bias.astype(np.float32).reshape(1, -1)
            tensors[out_name] = ring
        elif op == "Relu":
            tensors[out_name] = np.maximum(tensors[node.input[0]], 0)
        elif op == "HardSwish":
            v = tensors[node.input[0]]
            tensors[out_name] = v * np.clip(v / 6.0 + 0.5, 0.0, 1.0)
        elif op == "HardSigmoid":
            v = tensors[node.input[0]]
            tensors[out_name] = np.clip(v / 6.0 + 0.5, 0.0, 1.0)
        elif op == "Add":
            tensors[out_name] = tensors[node.input[0]] + tensors[node.input[1]]
        elif op == "Mul":
            tensors[out_name] = tensors[node.input[0]] * tensors[node.input[1]]
        elif op == "ReduceMean":
            axes = tuple(int(a) for a in init_map[node.input[1]])
            keepdims = True
            for a in node.attribute:
                if a.name == "keepdims":
                    keepdims = bool(a.i)
            tensors[out_name] = np.mean(
                tensors[node.input[0]], axis=axes, keepdims=keepdims)
        elif op == "Reshape":
            target = layers[name]["out_shape"]
            v = tensors[node.input[0]]
            dims = tuple(d if d > 0 else v.shape[i] for i, d in enumerate(target))
            tensors[out_name] = v.reshape(dims)
        elif op == "Squeeze":
            v = tensors[node.input[0]]
            tensors[out_name] = v.reshape(tuple(d for d in v.shape if d != 1))
        elif op == "Shape":
            start = node.attribute[0].i if node.attribute else 0
            end = node.attribute[1].i if len(node.attribute) > 1 else None
            tensors[out_name] = np.array(
                tensors[node.input[0]].shape[start:end], dtype=np.int64)
        elif op == "Concat":
            axis = node.attribute[0].i if node.attribute else 0
            parts = [tensors[i] if i in tensors else init_map[i] for i in node.input]
            tensors[out_name] = np.concatenate(parts, axis=axis)
        else:
            raise NotImplementedError(
                f"operator '{op}' (node {name}) is not handled by the chain runner")

    # Doorbell bookkeeping: the ring offset persisted across the whole chain
    # and must have wrapped at least once (thousands of commands, 16 entries).
    total_cmds = sum(r["cmds"] for r in records)
    heads = (model.firmware.doorbell["host_tail"], model.firmware.doorbell["npu_head"])
    assert heads[0] == heads[1] == total_cmds % ring_size, (
        f"ring bookkeeping diverged: host_tail/npu_head={heads}, "
        f"expected {total_cmds % ring_size}")
    assert total_cmds > ring_size, "expected ring wraps across the chain"

    records.insert(0, {"_total_cmds": total_cmds, "_wrap_count": total_cmds // ring_size})
    return records


# ---------------------------------------------------------------------------
# Op-dict conversion (cv_trace layer list -> {mmul,sfu,vector} dicts)
# ---------------------------------------------------------------------------

def _layer_list_to_op_dicts(layers, trace):
    """Map the cv_trace layer list to {mmul,sfu,vector} op dicts.

    Engine mapping mirrors ``cv/cv_command_ir.py``: conv/gemm -> MMUL;
    HardSwish/HardSigmoid -> SFU_RELU + VMUL; Relu -> SFU_RELU;
    Add -> VADD; Mul -> VMUL; ReduceMean -> VRED_SUM; shape ops -> meta.
    """
    ops = []
    for layer, entry in zip(layers, trace):
        t = entry["type"]
        if t in ("pointwise_conv", "depthwise_conv", "gemm"):
            ops.append({"engine": "mmul", "type": t, "name": layer["name"],
                        "M": entry["M"], "K": entry["K"], "N": entry["N"]})
        elif t == "hard_swish":
            ops.append({"engine": "sfu", "type": "sfu_relu", "name": layer["name"],
                        "sfu_cycles": entry["sfu_cycles"]})
            ops.append({"engine": "vector", "type": "vmul", "name": layer["name"]})
        elif t in ("hard_sigmoid", "relu"):
            ops.append({"engine": "sfu", "type": t, "name": layer["name"],
                        "sfu_cycles": entry["sfu_cycles"]})
        elif t == "add":
            ops.append({"engine": "vector", "type": "vadd", "name": layer["name"]})
        elif t == "mul":
            ops.append({"engine": "vector", "type": "vmul", "name": layer["name"]})
        elif t == "global_avg_pool":
            ops.append({"engine": "vector", "type": "vred_sum", "name": layer["name"]})
        else:
            ops.append({"engine": "meta", "type": t, "name": layer["name"]})
    return ops


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def graph_ctx():
    """Layer list + trace, loaded once per module."""
    t0 = time.time()
    layers = import_mobilenetv3(str(_ONNX_PATH))
    trace = generate_mobilenetv3_trace(str(_ONNX_PATH))
    return {"layers": layers, "trace": trace, "load_s": time.time() - t0}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMobileNetV3FMChain:

    def test_op_dict_conversion(self, graph_ctx):
        """Layer list -> {mmul,sfu,vector} op dicts: structure + counts."""
        layers, trace = graph_ctx["layers"], graph_ctx["trace"]
        assert len(layers) == len(trace) == 124

        ops = _layer_list_to_op_dicts(layers, trace)
        mmul = [o for o in ops if o["engine"] == "mmul"]
        sfu = [o for o in ops if o["engine"] == "sfu"]
        vec = [o for o in ops if o["engine"] == "vector"]

        # MobileNetV3-Small: 52 convs + 2 classifier Gemms.
        assert len(mmul) == 54, f"expected 54 mmul ops, got {len(mmul)}"
        assert sum(1 for o in mmul if o["type"] in ("pointwise_conv", "depthwise_conv")) == 52
        assert all(o["M"] > 0 and o["K"] > 0 and o["N"] > 0 for o in mmul)
        # hswish (19) -> SFU_RELU + VMUL pairs; hardsigmoid (9) + relu (14) -> SFU;
        # SE Muls (9) + Adds (6) + ReduceMeans (10: 9 SE + head pool) -> vector ops.
        assert len(sfu) == 19 + 9 + 14
        assert len(vec) == 19 + 9 + 6 + 10
        assert sum(1 for o in mmul if o["type"] == "depthwise_conv") == 11

        total_macs = sum(o["M"] * o["K"] * o["N"] for o in mmul)
        assert 50_000_000 <= total_macs <= 62_000_000, (
            f"total MACs {total_macs:,} outside expected [50M, 62M]")

    def test_full_chain_ring_vs_golden(self, graph_ctx):
        """Happy path: the full MobileNetV3 chain through the doorbell ring.

        Every conv/Gemm layer output must match the GoldenMXU
        ``matmul_int4_per_block`` reference (cos >= 0.99; verified bit-exact),
        and at least 15 conv layers must reach the 0.99 bar.
        """
        t0 = time.time()
        records = _run_chain()
        meta = records.pop(0)
        elapsed = time.time() - t0

        conv_records = [r for r in records if r["op"] == "conv"]
        assert len(conv_records) == 52

        print(f"Total ring commands: {meta['_total_cmds']} "
              f"(ring wraps: {meta['_wrap_count']}, chain wall: {elapsed:.1f}s)")
        hdr = f"{'layer':44s} {'M':>8s} {'K':>6s} {'N':>6s} {'cmds':>6s} {'cos':>10s} {'exact':>6s}"
        print(hdr)
        print("-" * len(hdr))
        for r in records:
            print(f"{r['name']:44s} {r['M']:8d} {str(r['K']):>6s} {r['N']:6d} "
                  f"{r['cmds']:6d} {r['cos']:10.6f} {str(r['exact']):>6s}")

        passing = [r for r in conv_records if r["cos"] >= 0.99]
        assert len(passing) >= 15, (
            f"need >=15 conv layers with cos_sim >= 0.99, got {len(passing)}")
        degenerate = 0
        for r in conv_records:
            assert r["exact"], (
                f"{r['name']}: ring output not bit-exact vs golden "
                f"(cos={r['cos']:.9f})")
            if r["cos"] > 0.0:
                assert r["cos"] >= 0.99, (
                    f"{r['name']}: ring vs golden cos_sim={r['cos']:.6f} < 0.99")
            else:
                degenerate += 1
        for r in records:
            assert r["exact"], (
                f"{r['name']} ({r['op']}) not bit-exact vs golden "
                f"(cos={r['cos']:.9f})")

        # The chain must actually have flowed: final classifier logits are
        # non-trivial and every dispatched command completed (asserted inside
        # _issue_mmul).
        logits = records[-1]["out"].flatten()
        assert np.std(logits) > 0.0 and np.all(np.isfinite(logits))
        print(f"\nFinal classifier logits: shape={logits.shape} "
              f"std={np.std(logits):.4f} argmax={int(np.argmax(logits))}")
        print(f"Conv layers with cos_sim >= 0.99: {len(passing)}/52 "
              f"({degenerate} degenerate zero-vs-zero layers, bit-exact)")

    def test_chain_determinism(self):
        """Two independent ring-chain runs must produce identical outputs."""
        rec_a = _run_chain()
        rec_b = _run_chain()
        assert rec_a[0]["_total_cmds"] == rec_b[0]["_total_cmds"]
        for ra, rb in zip(rec_a[1:], rec_b[1:]):
            assert ra["name"] == rb["name"]
            assert np.array_equal(ra["out"], rb["out"]), (
                f"non-deterministic layer output: {ra['name']}")

    def test_failure_weight_address_tamper(self):
        """Failure injection: tamper one mid-chain weight address.

        The corrupt layer's ring output must diverge from golden
        (cos < 0.99) while every OTHER layer stays bit-exact — downstream
        layers chain from the corrupted value in both paths.
        """
        clean = _run_chain()
        conv_names = [r["name"] for r in clean[1:] if r["op"] == "conv"]
        corrupt_name = conv_names[len(conv_names) // 2]
        records = _run_chain(corrupt_conv_name=corrupt_name)
        records.pop(0)

        assert any(r["name"] == corrupt_name and not r["exact"] for r in records), (
            f"failure injection not caught: {corrupt_name} still bit-exact")
        for r in records:
            if r["name"] == corrupt_name:
                assert r["cos"] < 0.99, (
                    f"tampered weight address produced matching output "
                    f"(cos={r['cos']:.6f})")
            else:
                assert r["exact"], (
                    f"tamper bled into {r['name']} (cos={r['cos']:.6f})")
        print(f"Failure injection OK: tampered weight address of {corrupt_name} "
              f"-> cos={[r['cos'] for r in records if r['name'] == corrupt_name][0]:.6f} "
              f"(< 0.99), all other {len(records) - 1} layers unchanged")
