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

    Q6_K block: 210 bytes → 256 floats
    formula: y[i] = d * scales[i//16] * (q[i] - 32)
    """
    QK_K = 256
    BLOCK_SIZE = QK_K // 2 + QK_K // 4 + QK_K // 16 + 2  # 210

    n_blocks = len(raw_bytes) // BLOCK_SIZE
    if n_blocks == 0 or len(raw_bytes) % BLOCK_SIZE != 0:
        raise ValueError(f"Invalid Q6_K data: {len(raw_bytes)} bytes")

    raw = np.frombuffer(raw_bytes, dtype=np.uint8)

    # Reshape to (n_blocks, BLOCK_SIZE)
    ql = raw[0::BLOCK_SIZE][:, None]  # Wrong — need proper reshape
    # Actually, let's use a different approach

    # Build full q values (n_blocks, 256) using numpy indexing
    ql = raw.reshape(n_blocks, BLOCK_SIZE)[:, :128]  # (n_blocks, 128)
    qh = raw.reshape(n_blocks, BLOCK_SIZE)[:, 128:192]  # (n_blocks, 64)
    scales_raw = raw.reshape(n_blocks, BLOCK_SIZE)[:, 192:208]  # (n_blocks, 16)
    # d is bytes 208-209
    d_bytes = raw.reshape(n_blocks, BLOCK_SIZE)[:, 208:210]
    d_uint16 = d_bytes[:, 0].astype(np.uint16) | (d_bytes[:, 1].astype(np.uint16) << 8)
    d = fp16_to_fp32(d_uint16)  # (n_blocks,)

    # Vectorized q extraction for all 256 positions
    # For each i: q_low = (ql[i//2] >> (4*(i%2))) & 0xF
    #             q_high = (qh[i//4] >> (2*(i%4))) & 0x3
    i_low_idx = np.arange(256) // 2   # [0,0,1,1,2,2,...127,127]
    i_low_shift = 4 * (np.arange(256) % 2)  # [0,4,0,4,...]
    i_high_idx = np.arange(256) // 4  # [0,0,0,0,1,1,1,1,...63,63,63,63]
    i_high_shift = 2 * (np.arange(256) % 4)  # [0,2,0,2,0,2,0,2,...]

    q_low = (ql[:, i_low_idx] >> i_low_shift) & 0xF
    q_high = ((qh[:, i_high_idx] >> i_high_shift) & 0x3) << 4
    q = (q_low | q_high).astype(np.float32)  # (n_blocks, 256)

    # scales: repeat each scale 16 times
    sc = scales_raw[:, np.arange(256) // 16].astype(np.float32)  # (n_blocks, 256)

    result = d[:, np.newaxis] * sc * (q - 32.0)
    return result.reshape(-1).astype(np.float32)


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
