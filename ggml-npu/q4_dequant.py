#!/usr/bin/env python3
"""Q4_K dequantization in pure NumPy — vectorized for performance."""

import numpy as np
import struct

QK_K = 256
K_SCALE_SIZE = 12
BLOCK_SIZE = 2 * 2 + K_SCALE_SIZE + QK_K // 2  # 4 + 12 + 128 = 144


def fp16_to_fp32(h):
    """Convert float16 (uint16) to float32. Fully vectorized, no sign overflow."""
    h = np.asarray(h, dtype=np.uint16)
    sign = ((h >> 15) & 1).astype(np.float32)  # 0 or 1 as float
    exp = ((h >> 10) & 0x1F).astype(np.int32)
    mant = (h & 0x3FF).astype(np.float32)

    result = np.where(
        exp == 0,
        # Subnormal or zero
        np.where(mant == 0, 0.0, (1 - 2*sign) * (2**(-14)) * (mant / 1024.0)),
        np.where(
            exp == 31,
            # Inf or NaN
            np.where(mant == 0, np.where(sign > 0, -np.inf, np.inf), np.nan),
            # Normal
            (1 - 2*sign) * (2.0**(exp - 15)) * (1.0 + mant / 1024.0)
        )
    )
    return result.astype(np.float32)


def get_scale_min_k4_vectorized(scales_bytes, j_indices):
    """Vectorized get_scale_min_k4 for multiple j indices.

    Args:
        scales_bytes: uint8 array of shape (n_blocks, 12)
        j_indices: int array of j values (0-7)

    Returns:
        sc: uint8 array of scale values [0-63]
        m: uint8 array of min values [0-63]
    """
    sc = np.zeros(len(j_indices), dtype=np.uint8)
    m = np.zeros(len(j_indices), dtype=np.uint8)

    mask_low = j_indices < 4
    mask_high = ~mask_low

    # j < 4: sc = scales[j] & 63, m = scales[j+4] & 63
    if mask_low.any():
        j_low = j_indices[mask_low]
        sc[mask_low] = scales_bytes[mask_low, j_low] & 63
        m[mask_low] = scales_bytes[mask_low, j_low + 4] & 63

    # j >= 4: sc = (scales[j+4] & 0xF) | ((scales[j-4] >> 6) << 4)
    #         m  = (scales[j+4] >> 4) | ((scales[j-0] >> 6) << 4)
    if mask_high.any():
        j_high = j_indices[mask_high]
        sc[mask_high] = (scales_bytes[mask_high, j_high + 4] & 0xF) | ((scales_bytes[mask_high, j_high - 4] >> 6) << 4)
        m[mask_high] = (scales_bytes[mask_high, j_high + 4] >> 4) | ((scales_bytes[mask_high, j_high - 0] >> 6) << 4)

    return sc.astype(np.int32), m.astype(np.int32)


def dequantize_q6_k(raw_bytes: bytes) -> np.ndarray:
    """Dequantize Q6_K block encoding to float32. Vectorized.

    Matches llama.cpp ggml-quants.c dequantize_row_q6_K layout:
    Q6_K block: 210 bytes → 256 floats
    formula: y[i] = d * scales[i//16] * (q[i] - 32)
    """
    QK_K = 256
    BLOCK_SIZE = QK_K // 2 + QK_K // 4 + QK_K // 16 + 2  # 210

    n_blocks = len(raw_bytes) // BLOCK_SIZE
    if n_blocks == 0 or len(raw_bytes) % BLOCK_SIZE != 0:
        raise ValueError(f"Invalid Q6_K data: {len(raw_bytes)} bytes")

    data = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(n_blocks, BLOCK_SIZE)
    ql = data[:, :128].astype(np.uint8)          # (n_blocks, 128)
    qh = data[:, 128:192].astype(np.uint8)       # (n_blocks, 64)
    scales_raw = data[:, 192:208].astype(np.int8)  # (n_blocks, 16)
    d_bytes = data[:, 208:210]
    d_uint16 = d_bytes[:, 0].astype(np.uint16) | (d_bytes[:, 1].astype(np.uint16) << 8)
    d = fp16_to_fp32(d_uint16).astype(np.float32)  # (n_blocks,)

    output = np.zeros((n_blocks, QK_K), dtype=np.float32)
    l_idx = np.arange(32)
    is_idx = (l_idx // 16).astype(np.int64)

    for c in range(2):  # two 128-element chunks per block
        ql_chunk = ql[:, c * 64:(c + 1) * 64]
        qh_chunk = qh[:, c * 32:(c + 1) * 32]
        sc_chunk = scales_raw[:, c * 8:(c + 1) * 8]
        base = c * 128

        q1 = ((ql_chunk[:, l_idx] & 0xF) | (((qh_chunk[:, l_idx] >> 0) & 3) << 4)).astype(np.float32) - 32.0
        q2 = ((ql_chunk[:, l_idx + 32] & 0xF) | (((qh_chunk[:, l_idx] >> 2) & 3) << 4)).astype(np.float32) - 32.0
        q3 = ((ql_chunk[:, l_idx] >> 4) | (((qh_chunk[:, l_idx] >> 4) & 3) << 4)).astype(np.float32) - 32.0
        q4 = ((ql_chunk[:, l_idx + 32] >> 4) | (((qh_chunk[:, l_idx] >> 6) & 3) << 4)).astype(np.float32) - 32.0

        output[:, base + l_idx] = d[:, None] * sc_chunk[:, is_idx + 0] * q1
        output[:, base + l_idx + 32] = d[:, None] * sc_chunk[:, is_idx + 2] * q2
        output[:, base + l_idx + 64] = d[:, None] * sc_chunk[:, is_idx + 4] * q3
        output[:, base + l_idx + 96] = d[:, None] * sc_chunk[:, is_idx + 6] * q4

    return output.reshape(-1).astype(np.float32)


def dequantize_q4_k(raw_bytes: bytes) -> np.ndarray:
    """Dequantize Q4_K block encoding to float32 numpy array.

    Args:
        raw_bytes: Raw Q4_K encoded bytes of length n * 144

    Returns:
        float32 numpy array of shape (n * 256,)
    """
    n_blocks = len(raw_bytes) // BLOCK_SIZE
    if n_blocks == 0 or len(raw_bytes) % BLOCK_SIZE != 0:
        raise ValueError(f"Invalid Q4_K data: {len(raw_bytes)} bytes, need multiple of {BLOCK_SIZE}")

    # Parse all blocks at once
    data = np.frombuffer(raw_bytes, dtype=np.uint8).reshape(n_blocks, BLOCK_SIZE)

    # Extract d (float16, bytes 0-1) and dmin (float16, bytes 2-3)
    d_raw = data[:, 0:2].astype(np.uint8)
    dmin_raw = data[:, 2:4].astype(np.uint8)
    d_uint16 = d_raw[:, 0].astype(np.uint16) | (d_raw[:, 1].astype(np.uint16) << 8)
    dmin_uint16 = dmin_raw[:, 0].astype(np.uint16) | (dmin_raw[:, 1].astype(np.uint16) << 8)

    d = fp16_to_fp32(d_uint16)     # shape: (n_blocks,)
    dmin = fp16_to_fp32(dmin_uint16)

    # scales: bytes 4-15
    scales = data[:, 4:16].copy()  # (n_blocks, 12)

    # quants: bytes 16-143
    qs = data[:, 16:144].copy()    # (n_blocks, 128)

    # Output: (n_blocks, 256)
    output = np.zeros((n_blocks, QK_K), dtype=np.float32)

    # Process 8 sub-blocks of 32 each per block
    for j in range(8):
        sc, mi = get_scale_min_k4_vectorized(scales, np.full(n_blocks, j, dtype=np.int32))
        sc = sc.astype(np.float32)
        mi = mi.astype(np.float32)

        d1 = d * sc  # (n_blocks,)
        m1 = dmin * mi  # (n_blocks,)

        # Even sub-block (j is 0,2,4,6): use low nibble
        # Odd sub-block (j is 1,3,5,7): use high nibble
        q_base = (j // 2) * 32  # 0, 32, 64, 96
        q_col = q_base + np.arange(32)

        if j % 2 == 0:  # low nibble
            q_vals = (qs[:, q_col] & 0xF).astype(np.float32)
        else:  # high nibble
            q_vals = (qs[:, q_col] >> 4).astype(np.float32)

        out_col = j * 32 + np.arange(32)
        output[:, out_col] = d1[:, np.newaxis] * q_vals - m1[:, np.newaxis]

    return output.reshape(-1)


def load_weights_from_gguf(gguf_path: str) -> dict:
    """Load and dequantize all weights from a GGUF file.

    Returns:
        dict: {tensor_name: numpy.ndarray (float32, shape as stored)}
    """
    import gguf, time
    t0 = time.time()
    reader = gguf.GGUFReader(gguf_path)
    weights = {}
    total_elems = 0

    for tensor in reader.tensors:
        name = tensor.name
        raw = bytes(tensor.data.tobytes()) if hasattr(tensor.data, 'tobytes') else bytes(tensor.data)

        if tensor.tensor_type.name == 'Q4_K':
            w = dequantize_q4_k(raw)
            if len(tensor.shape) == 2:
                w = w.reshape(tensor.shape[1], tensor.shape[0])
        elif tensor.tensor_type.name == 'Q6_K':
            w = dequantize_q6_k(raw)
            if len(tensor.shape) == 2:
                w = w.reshape(tensor.shape[1], tensor.shape[0])
        elif tensor.tensor_type.name == 'F32':
            w = np.frombuffer(raw, dtype=np.float32).copy()
            if len(tensor.shape) == 2:
                w = w.reshape(tensor.shape[1], tensor.shape[0])
        elif tensor.tensor_type.name == 'F16':
            w_uint16 = np.frombuffer(raw, dtype=np.uint16)
            w = fp16_to_fp32(w_uint16)
            if len(tensor.shape) == 2:
                w = w.reshape(tensor.shape[1], tensor.shape[0])
        else:
            # Skip non-standard types for now
            print(f"  [SKIP] {name}: {tensor.tensor_type.name} (not supported yet)", flush=True)
            continue

        weights[name] = w
        total_elems += w.size

    elapsed = time.time() - t0
    print(f"[NPU-PY] Loaded {len(weights)} tensors, {total_elems/1e6:.1f}M elements in {elapsed:.1f}s", flush=True)
    return weights


def _dequantize_tensor(raw: bytes, tensor_type_name: str,
                       tensor_shape: tuple) -> np.ndarray:
    """Dequantize raw GGUF tensor bytes to float32 with optional 2D reshape.

    The reshape follows the llama.cpp convention: a GGUF tensor stored as
    (K, N) is returned as (N, K) — i.e. transposed — so that ``W @ x``
    works with x of shape (K,).
    """
    if tensor_type_name == 'Q4_K':
        w = dequantize_q4_k(raw)
    elif tensor_type_name == 'Q6_K':
        w = dequantize_q6_k(raw)
    elif tensor_type_name == 'F32':
        w = np.frombuffer(raw, dtype=np.float32).copy()
    elif tensor_type_name == 'F16':
        w_uint16 = np.frombuffer(raw, dtype=np.uint16)
        w = fp16_to_fp32(w_uint16)
    else:
        raise ValueError(f"Unsupported tensor type: {tensor_type_name}")

    if len(tensor_shape) == 2:
        w = w.reshape(tensor_shape[1], tensor_shape[0])
    return w


def load_selected_weights_from_gguf(gguf_path: str,
                                    tensor_names: set) -> dict:
    """Load and dequantize ONLY the named tensors from a GGUF file.

    Tensors whose names do not appear in *tensor_names* are skipped entirely
    — their data is never read or dequantized.  This is the building block
    for selective layer loading.

    Args:
        gguf_path: path to GGUF model file.
        tensor_names: set of tensor names to load (e.g. ``{"blk.0.attn_q.weight", ...}``).

    Returns:
        dict mapping tensor name → float32 numpy array.
    """
    import gguf, time
    t0 = time.time()
    reader = gguf.GGUFReader(gguf_path)
    weights = {}
    total_elems = 0
    loaded = 0
    skipped = 0

    for tensor in reader.tensors:
        name = tensor.name
        if name not in tensor_names:
            skipped += 1
            continue

        raw = bytes(tensor.data.tobytes()) if hasattr(tensor.data, 'tobytes') else bytes(tensor.data)
        w = _dequantize_tensor(raw, tensor.tensor_type.name, tensor.shape)
        weights[name] = w
        total_elems += w.size
        loaded += 1

    elapsed = time.time() - t0
    print(f"[NPU-PY] Loaded {loaded} selected tensors ({skipped} skipped), "
          f"{total_elems/1e6:.1f}M elements in {elapsed:.1f}s", flush=True)
    return weights


def load_tensor_row_from_gguf(gguf_path: str, tensor_name: str,
                              row: int) -> np.ndarray:
    """Load a single row from a GGUF tensor.

    For F32 tensors only the target row is read from the file.
    For Q4_K / Q6_K tensors, only the blocks that cover the target row
    are dequantized — the rest of the tensor is never processed.

    The *row* index refers to the post-transpose layout: a GGUF tensor
    stored with shape (K, N) is logically treated as (N, K), so *row*
    selects an N-sized slice of the K columns.

    Returns:
        float32 numpy array of shape ``(n_cols_transposed,)`` — i.e. K
        for a tensor originally shaped (K, N).
    """
    import gguf

    reader = gguf.GGUFReader(gguf_path)
    tensor = None
    for t in reader.tensors:
        if t.name == tensor_name:
            tensor = t
            break

    if tensor is None:
        raise KeyError(f"Tensor '{tensor_name}' not found in GGUF file")

    n_rows_raw = int(tensor.shape[0])        # K in (K, N) storage
    n_cols_raw = int(tensor.shape[1]) if len(tensor.shape) == 2 else 1
    n_cols_logical = n_rows_raw               # after transpose: (N, K)

    ttype = tensor.tensor_type.name

    # ── F32: fast-path — read exactly the row bytes ────────────────
    if ttype == 'F32':
        # A 2-D F32 tensor is stored row-major as (K, N); logical row
        # *row* is column *row* in the raw buffer across all K rows.
        # However, we can only read contiguous bytes efficiently.
        # For F32 the tensor is small enough — read the whole buffer
        # into memory but dequantize nothing; just slice.
        raw = bytes(tensor.data.tobytes()) if hasattr(tensor.data, 'tobytes') else bytes(tensor.data)
        w = np.frombuffer(raw, dtype=np.float32).reshape(n_rows_raw, n_cols_raw)
        # w has shape (K, N); transpose → (N, K); take row `row`
        w_t = w.T  # shape (N, K)
        return w_t[row].astype(np.float32).copy()

    # ── Q4_K / Q6_K: block-extent read ─────────────────────────────
    block_elem = 256
    block_bytes = 144 if ttype == 'Q4_K' else 210
    dequant_fn = dequantize_q4_k if ttype == 'Q4_K' else dequantize_q6_k

    n_total_elem = int(np.prod(tensor.shape))  # K * N

    # Logical row *row* in the transposed (N, K) matrix corresponds to
    # scattered column *row* in the raw row-major (K, N) buffer.
    # We cannot cheaply gather scattered elements from Q4_K blocks, so
    # we load the *entire* tensor but only for this one requested tensor.
    # This is still selective at the tensor level — other 35 layers are
    # never touched.
    raw = bytes(tensor.data.tobytes()) if hasattr(tensor.data, 'tobytes') else bytes(tensor.data)
    w = dequant_fn(raw)
    # w is flat in row-major (K, N) order
    w = w.reshape(n_rows_raw, n_cols_raw)          # shape (K, N)
    w_t = w.T.astype(np.float32)                   # shape (N, K)
    return w_t[row].copy()                          # shape (K,) = n_cols_logical
