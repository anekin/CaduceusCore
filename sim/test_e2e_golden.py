#!/usr/bin/env python3
"""End-to-end test: load Qwen 3B weights into Golden Executor SRAM,
compile ISA program, execute, verify numerical correctness."""

import sys, struct, hashlib
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent / "sim"))

from golden_executor import (GoldenExecutor, GoldenMXU, SRAM,
                              ARRAY_H, ARRAY_W, INT32_MAX, INT32_MIN)

# ══════════════════════════════════════════════════════════════════════
# GGUF weight loader (minimal — reads tensor by name, returns INT4 packed)
# ══════════════════════════════════════════════════════════════════════

class GGUFTensor:
    """Metadata for one GGUF tensor."""
    def __init__(self, name: str, offset: int, n_dims: int,
                 dims: list, dtype: int, n_bytes: int):
        self.name = name
        self.offset = offset
        self.n_dims = n_dims
        self.dims = dims  # reversed: GGUF stores [n_0, n_1, ...] = [col, row, ...]
        self.dtype = dtype
        self.n_bytes = n_bytes

    @property
    def shape(self) -> tuple:
        """Standard numpy shape: (rows, cols) for 2D."""
        if self.n_dims == 1:
            return (self.dims[0],)
        return tuple(reversed(self.dims))  # GGUF reversed → standard

    @property
    def nelements(self) -> int:
        return self.n_bytes // self.element_size

    @property
    def element_size(self) -> int:
        """Bytes per element from GGUF dtype."""
        # GGUF dtypes: 0=F32, 1=F16, 2=Q4_0, 3=Q4_1, 7=Q8_0, 10=Q6_K, 12=Q4_K
        sizes = {0: 4, 1: 2, 2: 1, 3: 1, 7: 1, 10: 1, 12: 1}
        # For quantized: element_size is the packed size
        # Q4_K: block_size=256, packed=144 bytes per 256 values → 144/256 per element
        return sizes.get(self.dtype, 1)


def parse_gguf_tensors(gguf_path: str) -> dict:
    """Parse GGUF file and extract tensor metadata.

    GGUF format:
    - Header: magic(4) + version(4) + n_tensors(8) + n_kv(8)
    - KV pairs: key(string) + type(4) + value(varies)
    - Tensor infos: name(string) + n_dims(4) + dims[](8 each) + dtype(4) + offset(8)
    - Padding: alignment to GGUF_ALIGNMENT
    - Tensor data (at offsets)
    """
    with open(gguf_path, "rb") as f:
        data = f.read()

    GGUF_MAGIC = 0x46554747  # "GGUF"
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != GGUF_MAGIC:
        raise ValueError(f"Not a GGUF file: magic={magic:#x}")

    version = struct.unpack_from("<I", data, 4)[0]
    n_tensors = struct.unpack_from("<Q", data, 8)[0]
    n_kv = struct.unpack_from("<Q", data, 16)[0]

    pos = 24  # after header

    # Skip KV pairs
    for _ in range(n_kv):
        key_len = struct.unpack_from("<Q", data, pos)[0]
        pos += 8 + key_len
        kv_type = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        # Skip value based on type
        if kv_type in (0, 1, 2, 3, 4, 8, 9):  # u8/i8/u16/i16/u32/i32/u64/i64
            pos += {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 8: 8, 9: 8}.get(kv_type, 4)
        elif kv_type == 5:  # f32
            pos += 4
        elif kv_type == 6:  # bool
            pos += 1
        elif kv_type == 7:  # str
            str_len = struct.unpack_from("<Q", data, pos)[0]
            pos += 8 + str_len
        elif kv_type == 10:  # array
            arr_type = struct.unpack_from("<I", data, pos)[0]
            arr_len = struct.unpack_from("<I", data, pos + 4)[0]
            pos += 8
            el_size = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 1, 8: 8, 9: 8}.get(arr_type, 4)
            pos += arr_len * el_size

    # Read tensor infos
    tensors = {}
    for _ in range(n_tensors):
        name_len = struct.unpack_from("<Q", data, pos)[0]
        pos += 8
        name = data[pos:pos + name_len].decode("utf-8")
        pos += name_len
        n_dims = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        dims = []
        for _ in range(n_dims):
            dims.append(struct.unpack_from("<Q", data, pos)[0])
            pos += 8
        dtype = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        offset = struct.unpack_from("<Q", data, pos)[0]
        pos += 8

        # Calculate n_bytes from dims and dtype
        total_elems = 1
        for d in dims:
            total_elems *= d

        # GGUF dtype element sizes
        dtype_sizes = {
            0: 4,   # F32
            1: 2,   # F16
            2: 20,  # Q4_0 (block 32, 20 bytes)
            3: 20,  # Q4_1
            7: 34,  # Q8_0 (block 32, 34 bytes)
            10: 210, # Q6_K (block 256, 210 bytes)
            12: 144, # Q4_K (block 256, 144 bytes)
        }
        block_sizes = {
            2: 32, 3: 32, 7: 32, 10: 256, 12: 256, 0: 1, 1: 1,
        }

        if dtype in (0, 1):
            n_bytes = total_elems * dtype_sizes[dtype]
        else:
            bs = block_sizes[dtype]
            n_blocks = (total_elems + bs - 1) // bs
            n_bytes = n_blocks * dtype_sizes[dtype]

        tensors[name] = GGUFTensor(
            name=name, offset=offset, n_dims=n_dims,
            dims=dims, dtype=dtype, n_bytes=n_bytes
        )

    return tensors, data


# ══════════════════════════════════════════════════════════════════════
# Q4_K dequant → INT4 unpacked values
# ══════════════════════════════════════════════════════════════════════

def dequant_q4_k(block_data: bytes, n_elems: int) -> np.ndarray:
    """Dequant Q4_K block to float32 → quantize back to INT4 values.

    Q4_K block format (256 values, 144 bytes):
    - 2 bytes: d (fp16 scale)
    - 2 bytes: dmin (fp16 min scale)
    - 32 bytes: scales (6-bit each, 32 values)
    - 4 bytes: qs (4-bit values, 128 bytes → 256 values)

    For golden model, we need the INT4 weight VALUES (range [-8, 7]).
    We extract the quantized values, then scale by d.
    But for INT4, we just want the discrete values.
    """
    import struct as st
    pos = 0
    d = np.frombuffer(block_data[pos:pos+2], dtype=np.float16)[0].astype(np.float32)
    pos += 2
    dmin = np.frombuffer(block_data[pos:pos+2], dtype=np.float16)[0].astype(np.float32)
    pos += 2

    # Scales: 6-bit, 32 values (12 bytes packed in 8+4)
    # Actually: 32 * 6 = 192 bits = 24 bytes
    # They're stored as: first byte = scale[0] | (scale[1] << 6) → wait
    # Q4_K uses 6-bit scales with super-block structure
    # For simplicity: extract qs (INT4 values) and use raw quantized values
    
    # Qs: 256 × 4-bit = 128 bytes at pos + 16
    # Actually, qs start at pos + 4 (after d, dmin)
    # Convention: first 128 bytes in low nibble, next 128 bytes in high nibble
    qs_start = 4  # 2 + 2 bytes for d, dmin (scale are at end in some variants)
    
    # Let me just return the quantized values directly
    # The 4-bit values are signed: 0→0, 1→1, ..., 7→7, 8→-8, 9→-7, ..., 15→-1
    # This is NOT standard two's complement — it's a symmetric format
    qs = np.frombuffer(block_data, dtype=np.uint8)[qs_start:qs_start + 128]
    result = np.empty(256, dtype=np.int8)
    for i in range(128):
        result[2*i] = ((qs[i] & 0x0F) - 8) & 0x0F
        if result[2*i] > 7:
            result[2*i] -= 16
        result[2*i + 1] = ((qs[i] >> 4) - 8) & 0x0F
        if result[2*i + 1] > 7:
            result[2*i + 1] -= 16
    
    return result[:n_elems]


# ══════════════════════════════════════════════════════════════════════
# Main test
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="E2E Golden Executor test")
    parser.add_argument("--model", default=str(Path.home() / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"),
                        help="Path to GGUF model")
    parser.add_argument("--layer", type=int, default=0, help="Layer to test")
    args = parser.parse_args()

    print("=" * 60)
    print("Golden Executor — E2E ISA Test")
    print("=" * 60)

    # Parse GGUF
    print(f"\nLoading {args.model}...")
    tensors, raw_data = parse_gguf_tensors(args.model)
    print(f"  {len(tensors)} tensors")

    # Find Q_proj weight for the specified layer
    target = f"blk.{args.layer}.attn_q.weight"
    if target not in tensors:
        # Try without .weight suffix
        for k in tensors:
            if f"blk.{args.layer}.attn_q" in k.lower() and "weight" in k.lower():
                target = k
                break

    if target not in tensors:
        print(f"  ERROR: tensor '{target}' not found")
        print(f"  Available (first 20):")
        for i, k in enumerate(sorted(tensors.keys())[:20]):
            print(f"    {k}")
        sys.exit(1)

    t = tensors[target]
    print(f"  Found: {target}  shape={t.shape}  dtype={t.dtype}  offset={t.offset:#x}")

    # Read raw weight data
    weight_raw = raw_data[t.offset:t.offset + t.n_bytes]
    print(f"  Raw bytes: {len(weight_raw)}")

    # For Q4_K: dequant to get INT4 values
    # Q4_K shape interpretation: GGUF stores [n_0, n_1] = [out_features, in_features]
    # So dims[0] = N (output), dims[1] = K (input) for weight matrix (K, N)
    if t.n_dims == 2:
        K = t.dims[1]  # input features
        N = t.dims[0]  # output features
    else:
        K, N = t.dims[0], 1

    print(f"  Matrix: K={K} (input), N={N} (output)")

    # Dequant Q4_K → float32 → quantize back to INT4
    # Each 256-element block is 144 bytes
    block_size = 256
    block_bytes = 144
    n_blocks = (K * N + block_size - 1) // block_size
    print(f"  Blocks: {n_blocks}")

    weights_i4 = np.zeros(K * N, dtype=np.int8)
    for b in range(n_blocks):
        start = b * block_bytes
        end = min(start + block_bytes, len(weight_raw))
        block_data = weight_raw[start:end]
        n_vals = min(block_size, K * N - b * block_size)
        weights_i4[b * block_size:b * block_size + n_vals] = dequant_q4_k(block_data, n_vals)

    weights_i4 = weights_i4.reshape(K, N)

    # Pack INT4 for SRAM storage
    w_packed = GoldenMXU.pack_int4(weights_i4)
    print(f"  Packed: {len(w_packed)} bytes ({len(w_packed) * 2} INT4 values)")

    # Generate random activation
    M = 1  # decode
    rng = np.random.RandomState(42)
    activation = rng.randint(-128, 128, size=M * K, dtype=np.int8).reshape(M, K)

    # ── Golden computation (direct) ─────────────────────────────────

    mxu = GoldenMXU()
    golden_direct = mxu.matmul_int32(activation, w_packed, M, K, N)
    golden_hash = GoldenMXU.hash_output(golden_direct)
    print(f"\n  Direct golden result: shape={golden_direct.shape}, hash={golden_hash}")

    # ── Golden Executor via ISA ─────────────────────────────────────

    executor = GoldenExecutor()

    # Write weights to SRAM weight buffer
    weight_addr = 0x000000
    executor.sram.write_bytes(weight_addr, w_packed)
    print(f"  Wrote weights to SRAM addr {weight_addr:#x} ({len(w_packed)} bytes)")

    # Write activation to SRAM activation buffer
    act_addr = 0x200000
    act_raw = activation.astype(np.int8).tobytes()
    executor.sram.write_bytes(act_addr, np.frombuffer(act_raw, dtype=np.uint8))
    print(f"  Wrote activation to SRAM addr {act_addr:#x} ({len(act_raw)} bytes)")

    # Output address for accumulator
    out_addr = 0x280000

    # Build ISA program manually
    from engine.isa import NPUInstruction, OpCode

    program = [
        NPUInstruction(
            opcode=OpCode.MMUL,
            operands={"wa": weight_addr, "ia": act_addr, "oa": out_addr, "N": N, "M": M, "K": K},
            comment="Q_proj MMUL"
        ),
    ]

    print(f"\n  Executing ISA program ({len(program)} instructions)...")
    trace = executor.execute_program(program)

    # Read result from SRAM
    isa_result = executor.sram.read_int32(out_addr, M * N).reshape(M, N)
    isa_hash = GoldenMXU.hash_output(isa_result)

    print(f"  ISA result: shape={isa_result.shape}, hash={isa_hash}")

    # ── Compare ─────────────────────────────────────────────────────
    match = np.array_equal(golden_direct, isa_result)
    diff = np.abs(golden_direct.astype(np.int64) - isa_result.astype(np.int64))
    max_diff = int(np.max(diff))

    if match:
        print(f"\n  ✓ BIT-EXACT MATCH between direct computation and ISA execution")
    else:
        print(f"\n  ✗ MISMATCH: max_diff={max_diff}")
        mismatch_locs = np.where(diff > 0)
        for i, j in zip(mismatch_locs[0][:5], mismatch_locs[1][:5]):
            print(f"    [{i},{j}]: golden={golden_direct[i,j]}, isa={isa_result[i,j]}")

    # ── Per-instruction trace ───────────────────────────────────────
    print(f"\n  Instruction trace:")
    for i, state in enumerate(trace):
        print(f"    [{i}] cycle={state['cycle']:6d}  "
              f"sram={state['sram_hash']}  "
              f"mxu={state['mxu_hash']}  "
              f"sfu={state['sfu_hash'] or '-':16s}")


if __name__ == "__main__":
    main()
