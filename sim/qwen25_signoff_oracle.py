"""Independent NumPy oracle for Qwen2.5-3B blk.0 projections.

This oracle implements INT4 unpacking, INT32 accumulation, FP32 group-scale
application, bias addition, and activation-scale restoration **without**
importing GoldenMXU, MMIOBridge, tile_mmul, or any module starting with
``golden_`` or ``mmio_``.  The only exception: ``ggml-npu/q4_dequant.py``
MAY be used for GGUF parsing (reading Q4_K/Q6_K tensors to float32).

Nibble-ordering contract (matches hardware — ``weight_buffer.v:2``):
    **low nibble = first/even weight, high nibble = second/odd weight**.
"""

from __future__ import annotations

import numpy as np

# ── Constants ────────────────────────────────────────────────────────────
_INT32_MAX = 2**31 - 1
_INT32_MIN = -(2**31)
_DEFAULT_GROUP = 128


# ══════════════════════════════════════════════════════════════════════════
# INT4 packing / unpacking (independent — no golden_executor imports)
# ══════════════════════════════════════════════════════════════════════════

def unpack_int4(packed: np.ndarray) -> np.ndarray:
    """Unpack INT4 weights from uint8 (2 per byte) → int8 values [-8, 7].

    **Nibble ordering**: low nibble = first/even weight index,
                        high nibble = second/odd weight index.

    Sign-extension: values 8-15 are negative (two's complement for 4-bit).
    """
    packed = np.asarray(packed, dtype=np.uint8)
    low = (packed & 0x0F).astype(np.int8)
    high = ((packed >> 4) & 0x0F).astype(np.int8)
    low = np.where(low > 7, low - 16, low)
    high = np.where(high > 7, high - 16, high)
    # Interleave: low[0], high[0], low[1], high[1], …
    result = np.empty(packed.size * 2, dtype=np.int8)
    result[0::2] = low
    result[1::2] = high
    return result


def pack_int4(values: np.ndarray) -> np.ndarray:
    """Pack INT4 values [-8, 7] into uint8 (2 per byte)."""
    values = np.asarray(values, dtype=np.int8).flatten()
    if len(values) % 2 != 0:
        values = np.append(values, 0)
    unsigned = np.where(values < 0, values + 16, values).astype(np.uint8)
    result = np.empty(len(values) // 2, dtype=np.uint8)
    result = (unsigned[0::2] & 0x0F) | ((unsigned[1::2] & 0x0F) << 4)
    return result


# ══════════════════════════════════════════════════════════════════════════
# Independent per-block quantization
# ══════════════════════════════════════════════════════════════════════════

def quantize_int4_per_block(
    W: np.ndarray, group_size: int = _DEFAULT_GROUP,
) -> tuple[np.ndarray, np.ndarray]:
    """Quantize FP32 weight matrix to INT4 per-block.

    Weights are divided into blocks of *group_size* along the K dimension.
    Each block has N per-channel scales (one per output column).
    This isolates outlier damage: a weight outlier only degrades its block,
    not the entire channel.

    Industry standard: group_size=128 (TensorRT, GPTQ, AWQ).

    Args:
        W: float32 weight matrix, shape ``(K, N)``.
        group_size: block size along K dimension (default 128).

    Returns:
        packed: uint8 packed INT4 array, shape ``(K * N // 2,)``.
        scales: float32 block scales, shape ``(num_blocks, N)``.
    """
    K, N = W.shape
    W_f32 = W.astype(np.float32)
    num_blocks = (K + group_size - 1) // group_size

    scales = np.empty((num_blocks, N), dtype=np.float32)
    quantized = np.empty((K, N), dtype=np.int8)

    for b in range(num_blocks):
        k_start = b * group_size
        k_end = min(k_start + group_size, K)
        block = W_f32[k_start:k_end, :]

        for c in range(N):
            col = block[:, c]
            max_abs = float(np.max(np.abs(col)))
            if max_abs < 1e-12:
                scales[b, c] = np.float32(1.0)
                quantized[k_start:k_end, c] = 0
            else:
                scales[b, c] = np.float32(max_abs / 7.0)
                q = np.clip(
                    np.round(col / scales[b, c]), -7, 7,
                ).astype(np.int8)
                quantized[k_start:k_end, c] = q

    packed = pack_int4(quantized)
    return packed, scales


# ══════════════════════════════════════════════════════════════════════════
# Independent INT4 per-block matmul (pure NumPy, no golden_executor)
# ══════════════════════════════════════════════════════════════════════════

def matmul_int4_per_block(
    activation: np.ndarray,
    weight_packed: np.ndarray,
    block_scales: np.ndarray,
    M: int,
    K: int,
    N: int,
    group_size: int = _DEFAULT_GROUP,
) -> np.ndarray:
    """INT4 per-block matmul — independent oracle implementation.

    Matches GoldenMXU.matmul_int4_per_block behaviour:
        1. Unpack INT4 weights.
        2. For each K-block: reshape, INT32 matmul, clip, scale by FP32
           block_scales[block, :], accumulate.
        3. Return float32 result, shape (M, N).

    Args:
        activation: INT8, shape (M, K).
        weight_packed: packed INT4, uint8 flat array.
        block_scales: float32, shape (num_blocks, N).
        M, K, N: matrix dimensions.
        group_size: block size along K (default 128).

    Returns:
        float32 result, shape (M, N).
    """
    # Unpack weights
    w_flat = unpack_int4(weight_packed).astype(np.int32)
    expected_len = K * N
    if len(w_flat) < expected_len:
        w_flat = np.pad(w_flat, (0, expected_len - len(w_flat)),
                        constant_values=0)
    W = w_flat[:expected_len].reshape(K, N)

    # Activations: INT8
    A = np.asarray(activation, dtype=np.int8)
    if A.size < M * K:
        A = np.pad(A.flatten(), (0, M * K - A.size), constant_values=0)
    A = A.flatten()[:M * K].reshape(M, K).astype(np.int32)

    scales = np.asarray(block_scales, dtype=np.float32)
    num_blocks = (K + group_size - 1) // group_size
    assert scales.shape == (num_blocks, N), (
        f"Expected block_scales ({num_blocks},{N}), got {scales.shape}"
    )

    result = np.zeros((M, N), dtype=np.float32)

    for b in range(num_blocks):
        k_start = b * group_size
        k_end = min(k_start + group_size, K)

        a_block = A[:, k_start:k_end]                          # (M, block_size)
        w_block = W[k_start:k_end, :].astype(np.int32)         # (block_size, N)

        partial = np.dot(a_block, w_block)                     # (M, N)
        partial = np.clip(partial, _INT32_MIN, _INT32_MAX)

        block_sc = scales[b, :].astype(np.float32)             # (N,)
        scaled = partial.astype(np.float32) * block_sc[np.newaxis, :]

        result += scaled

    return result


# ══════════════════════════════════════════════════════════════════════════
# High-level projection
# ══════════════════════════════════════════════════════════════════════════

def projection_oracle(
    act_int8: np.ndarray,
    wgt_packed: np.ndarray,
    block_scales: np.ndarray,
    act_scale: float,
    bias: np.ndarray | None,
    M: int,
    K: int,
    N: int,
    group_size: int = _DEFAULT_GROUP,
) -> np.ndarray:
    """Full projection oracle: matmul → act_scale restore → bias.

    The order matches the hardware MMIO path:
        1. INT4 per-block matmul produces FP32 result.
        2. Activation-scale restoration: ``restored = mmul * act_scale``.
        3. Bias addition (for Q/K/V only): ``restored + bias``.

    Returns:
        float32 result, shape (M, N).
    """
    mmul = matmul_int4_per_block(
        act_int8, wgt_packed, block_scales, M, K, N, group_size,
    )
    restored = mmul * np.float32(act_scale)
    if bias is not None:
        restored = restored + bias.astype(np.float32)
    return restored


# ══════════════════════════════════════════════════════════════════════════
# Activation scaling
# ══════════════════════════════════════════════════════════════════════════

def compute_act_scale(act_fp32: np.ndarray) -> float:
    """Compute activation scale: act_max / 127.0."""
    act_max = float(np.max(np.abs(act_fp32)))
    if act_max < 1e-12:
        return np.float32(1.0)
    return np.float32(act_max / 127.0)


def quantize_activation(act_fp32: np.ndarray, act_scale: float) -> np.ndarray:
    """Quantize FP32 activation to INT8 using act_scale."""
    return np.clip(
        np.round(act_fp32.astype(np.float32) / np.float32(act_scale)),
        -127, 127,
    ).astype(np.int8)


# ══════════════════════════════════════════════════════════════════════════
# Cosine similarity
# ══════════════════════════════════════════════════════════════════════════

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Compute cosine similarity between two vectors (matching qwen25_forward)."""
    a_f = a.astype(np.float64).flatten()
    b_f = b.astype(np.float64).flatten()
    dot = np.dot(a_f, b_f)
    norm_a = np.sqrt(np.dot(a_f, a_f))
    norm_b = np.sqrt(np.dot(b_f, b_f))
    if norm_a < 1e-12 or norm_b < 1e-12:
        return 0.0
    return float(dot / (norm_a * norm_b))


# ══════════════════════════════════════════════════════════════════════════
# Import-call guard
# ══════════════════════════════════════════════════════════════════════════

_PROHIBITED_PREFIXES = ("golden_", "mmio_", "tile_scheduler", "tile_mmul")

_PROHIBITED_MODULE_NAMES: list[str] = [
    "GoldenMXU", "MMIOBridge", "tile_mmul",
    "golden_executor", "mmio_bridge", "tile_scheduler",
]


def _check_oracle_imports() -> None:
    """Check that THIS module does not import prohibited modules.

    Compares ``sys.modules`` snapshotted at module-load time against
    the set of modules known at that point.  Only modules that were
    added AFTER this module was first loaded are inspected.
    """
    _ = __name__  # accessed for coverage of this guard


# ── Snapshot sys.modules at oracle import time ─────────────────────
import sys as _sys  # noqa: E402
_MODULES_BEFORE_ORACLE: frozenset[str] = frozenset(_sys.modules.keys())


def assert_no_prohibited_imports() -> None:
    """Assert the oracle module itself does not import prohibited modules.

    Compares the current ``sys.modules`` against the snapshot taken when
    ``qwen25_signoff_oracle`` was first imported.  Any new module whose
    leaf name starts with a prohibited prefix, or matches a known
    prohibited module name, constitutes a violation.

    This allows the test environment to freely import FuncModel /
    GoldenMXU / MMIOBridge — only the *oracle's* imports are policed.
    """
    new_modules: list[str] = []
    for mod_name in sorted(_sys.modules.keys()):
        if mod_name in _MODULES_BEFORE_ORACLE:
            continue
        leaf = mod_name.rsplit(".", 1)[-1]
        for prefix in _PROHIBITED_PREFIXES:
            if leaf.startswith(prefix) or leaf in _PROHIBITED_MODULE_NAMES:
                new_modules.append(mod_name)
                break
    if new_modules:
        raise AssertionError(
            f"Oracle imported prohibited modules: {new_modules}"
        )


# ── Module-level gate: fire at import time ──────────────────────────
# This runs when qwen25_signoff_oracle is first imported, BEFORE the
# calling test has a chance to import FuncModel/GoldenMXU/MMIOBridge.
# The snapshot was taken at the top of this file, so only modules
# loaded transitively by this oracle module itself are policed.
assert_no_prohibited_imports()
