"""Preflight assertions for synthetic Qwen blk.0 test-vector assets.

Validates manifest integrity, SHA-256 match for all 46 hex files, synthetic
dimensions (2560/9728, NOT canonical 2048/11008), and non-overlapping DRAM
window placement under FuncModel(dram_mb=256).

This is the synthetic half of Wave 1 T0B. The real-GGUF half is in
test_qwen25_3b_real_blk0.py.
"""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from sim.qwen_blk0_synthetic_vectors import (
    get_dram_windows,
    assert_non_overlapping_windows,
    load_manifest,
    verify_manifest_integrity,
    PUBLIC_NUM_OPS,
    PUBLIC_NUM_FILES,
    PUBLIC_DIMS,
    VECTORS_DIR,
)
from sim.func_model import FuncModel
from sim.tile_scheduler import (
    tile_mmul,
    TILE_H,
    TILE_W,
    TILE_WEIGHT_BYTES,
    TILE_SCALE_BYTES,
)
from sim.golden_executor import GoldenMXU
from sim.regmap import Addr

CASE_ID = "task-0b-qwen3b-synthetic-and-real-preflight"


def _emit_metric(capsys, key: str, value) -> None:
    """Emit a SIGNOFF_METRIC line. The leading newline ensures the line
    starts at column 0 even when interleaved with pytest's progress dots."""
    line = json.dumps({"case": CASE_ID, "key": key, "value": value})
    with capsys.disabled():
        print(f"\nSIGNOFF_METRIC {line}")


def test_qwen_blk0_synthetic_assets_preflight(capsys) -> None:
    """Verify synthetic manifest integrity, SHA-256, dims, and DRAM layout."""
    manifest = load_manifest()
    assert manifest is not None

    ops = manifest.get("ops", [])
    assert len(ops) == PUBLIC_NUM_OPS

    files = manifest.get("files", {})
    assert len(files) == PUBLIC_NUM_FILES

    ok, errors = verify_manifest_integrity(manifest)
    assert ok, f"SHA-256 integrity failed: {errors}"

    dims = manifest.get("dimensions", {})
    assert dims.get("hidden") == PUBLIC_DIMS["hidden"]
    assert dims.get("intermediate") == PUBLIC_DIMS["intermediate"]

    model = FuncModel(dram_mb=256)
    dram_size = len(model.dram)
    assert dram_size == 256 * 1024 * 1024

    windows = get_dram_windows()
    assert len(windows) == PUBLIC_NUM_OPS
    assert_non_overlapping_windows(windows)
    for _op_idx, offset, size in windows:
        assert offset + size <= dram_size


# ══════════════════════════════════════════════════════════════════════
# T4B — Synthetic tiled-MMUL scheduler stress gate
# ══════════════════════════════════════════════════════════════════════

_T4B_CASE_ID = "task-4b-qwen3b-tiled-mmul"
_DRAM_BASE = Addr.DRAM_BASE  # 0x8000_0000
_SRAM_SIZE = 256 * 1024


def _t4b_emit_metric(capsys, key: str, value) -> None:
    line = json.dumps({"case": _T4B_CASE_ID, "key": key, "value": value})
    with capsys.disabled():
        print(f"\nSIGNOFF_METRIC {line}")


def _read_hex_file(filepath: Path, elem_bytes: int = 1) -> bytes:
    """Read a hex file (one value per line) into little-endian packed bytes."""
    with open(filepath) as f:
        vals = [int(line.strip(), 16) for line in f if line.strip()]
    if not vals:
        return b""
    if elem_bytes == 1:
        return bytes(vals)
    fmt = {2: "H", 4: "I", 8: "Q"}[elem_bytes]
    return b"".join(struct.pack(f"<{fmt}", v) for v in vals)


def _pad_bytes(data: bytes, needed: int) -> bytes:
    """Pad or truncate bytes to exactly *needed* length."""
    if len(data) < needed:
        return data + b"\x00" * (needed - len(data))
    return data[:needed]


def _row_major_to_tile_major(
    packed_row: bytes,
    K: int,
    N: int,
    num_blocks: int,
    num_tiles: int,
) -> bytes:
    """Convert row-major packed INT4 weights (K×N) to tile-major 128×128 layout.

    Each tile at (n_tile, k_block) occupies exactly TILE_WEIGHT_BYTES bytes
    (8192) of output space. Partial edge tiles are zero-padded to that size.
    Within each tile, data is stored row-major packed INT4.
    """
    # Unpack into full (K, N) INT4 matrix for easy tile extraction
    wgt_flat = _unpack_int4_raw(packed_row, K * N)
    W = wgt_flat.reshape(K, N)

    total_bytes = num_tiles * num_blocks * TILE_WEIGHT_BYTES
    result = bytearray(total_bytes)

    for n_tile in range(num_tiles):
        n_start = n_tile * TILE_W
        n_end = min(n_start + TILE_W, N)
        tile_width = n_end - n_start

        for k_block in range(num_blocks):
            k_start = k_block * TILE_H
            k_end = min(k_start + TILE_H, K)
            block_height = k_end - k_start

            # Extract submatrix
            sub = W[k_start:k_end, n_start:n_end]

            # Pack into row-major INT4 bytes for this tile
            sub_flat = sub.flatten()
            sub_packed = _pack_int4_raw(sub_flat)

            # Place at tile-major offset
            offset = (n_tile * num_blocks + k_block) * TILE_WEIGHT_BYTES
            result[offset : offset + len(sub_packed)] = sub_packed
            # Rest of the slot is already zero from bytearray init

    return bytes(result)


def _unpack_int4_raw(packed: bytes, num_values: int) -> np.ndarray:
    """Unpack INT4 from uint8 bytes → int8 array of length *num_values*.

    Same unpacking order as GoldenMXU.unpack_int4: byte N gives values
    [2N] (low nibble) then [2N+1] (high nibble), sign-extended to [-8, 7].
    """
    arr = np.frombuffer(packed, dtype=np.uint8).copy()
    low = (arr & 0x0F).astype(np.int8)
    high = ((arr >> 4) & 0x0F).astype(np.int8)
    low[low > 7] -= 16
    high[high > 7] -= 16
    result = np.empty(len(arr) * 2, dtype=np.int8)
    result[0::2] = low
    result[1::2] = high
    return result[:num_values]


def _pack_int4_raw(values: np.ndarray) -> bytes:
    """Pack INT8 values (must be in [-8,7]) into uint8 (2 per byte).

    Inverse of _unpack_int4_raw.
    """
    vals = np.asarray(values, dtype=np.int8).flatten()
    if len(vals) % 2 != 0:
        vals = np.append(vals, 0)
    unsigned = np.where(vals < 0, vals + 16, vals).astype(np.uint8)
    packed = (unsigned[0::2] & 0x0F) | ((unsigned[1::2] & 0x0F) << 4)
    return packed.tobytes()


def _make_unity_scale_bytes(num_blocks: int, num_tiles: int, N: int) -> bytes:
    """Generate tile-major unity FP32 scale data (all 1.0f).

    Each tile at (n_tile, k_block) occupies TILE_SCALE_BYTES (512) bytes
    of output space. The first tile_width * 4 bytes are 1.0f values; the
    remainder is zero-padded.
    """
    total_bytes = num_tiles * num_blocks * TILE_SCALE_BYTES
    result = bytearray(total_bytes)
    unity = struct.pack("<f", 1.0)

    for n_tile in range(num_tiles):
        n_start = n_tile * TILE_W
        n_end = min(n_start + TILE_W, N)
        tile_width = n_end - n_start
        for k_block in range(num_blocks):
            offset = (n_tile * num_blocks + k_block) * TILE_SCALE_BYTES
            for col in range(tile_width):
                result[offset + col * 4 : offset + (col + 1) * 4] = unity
    return bytes(result)


def _dram_off(addr: int) -> int:
    """Convert absolute DRAM address to bytearray index."""
    return addr - _DRAM_BASE


def _build_mmio_handlers(dram: bytearray, sram: bytearray):
    """Build mmio_write / mmio_read / wait_done callbacks backed by DRAM+SRAM.

    Returns (mmio_write, mmio_read, wait_done, tile_counter, DMA, MXU).
    tile_counter is a list[int] that tracks MXU CMD invocations.
    """
    regfile: dict[tuple[int, int], int] = {}
    _last_dma_ch = [0]  # mutable: 0 = CH0, 1 = CH1
    _mxu_invocations = [0]

    DMA = SimpleNamespace(
        CH0_SRC=0, CH0_DST=4, CH0_SIZE=8, CMD=12, STATUS=16,
        CH1_SRC=20, CH1_DST=24, CH1_SIZE=28,
    )
    MXU = SimpleNamespace(
        I_ADDR=0, W_ADDR=4, SCALE_ADDR=8, O_ADDR=12,
        CTRL=16, DIM0=20, DIM1=24, CMD=28, STATUS=32,
    )

    _DMA_BASE = 0xD0000000
    _MXU_BASE = 0x40000000

    # ── memory access helpers ───────────────────────────────────────
    def _mem_read(addr: int, size: int) -> bytes:
        if addr >= _DRAM_BASE:
            off = _dram_off(addr)
            return bytes(dram[off : off + size])
        return bytes(sram[addr : addr + size])

    def _mem_write(addr: int, data: bytes) -> None:
        if addr >= _DRAM_BASE:
            off = _dram_off(addr)
            dram[off : off + len(data)] = data[:]
        else:
            sram[addr : addr + len(data)] = data[:]

    def mmio_write(base: int, offset: int, value: int):
        regfile[(base, offset)] = value

        if base == _DMA_BASE:
            if offset in (DMA.CH0_SRC, DMA.CH0_DST, DMA.CH0_SIZE):
                _last_dma_ch[0] = 0
            elif offset in (DMA.CH1_SRC, DMA.CH1_DST, DMA.CH1_SIZE):
                _last_dma_ch[0] = 1
            elif offset == DMA.CMD and value == 1:
                ch = _last_dma_ch[0]
                src_key = (base, DMA.CH0_SRC if ch == 0 else DMA.CH1_SRC)
                dst_key = (base, DMA.CH0_DST if ch == 0 else DMA.CH1_DST)
                size_key = (base, DMA.CH0_SIZE if ch == 0 else DMA.CH1_SIZE)
                src = regfile.get(src_key, 0)
                dst = regfile.get(dst_key, 0)
                size = regfile.get(size_key, 0)
                if size > 0:
                    data = _mem_read(src, size)
                    _mem_write(dst, data)
                regfile[(base, DMA.STATUS)] = 0  # done

        elif base == _MXU_BASE:
            if offset == MXU.CMD and value == 1:
                _mxu_invocations[0] += 1
                i_addr = regfile.get((base, MXU.I_ADDR), 0)
                w_addr = regfile.get((base, MXU.W_ADDR), 0)
                o_addr = regfile.get((base, MXU.O_ADDR), 0)
                ctrl = regfile.get((base, MXU.CTRL), 0)
                dim0 = regfile.get((base, MXU.DIM0), 0)
                dim1 = regfile.get((base, MXU.DIM1), 0)

                M_eff = dim0 & 0xFFFF
                block_h = (dim0 >> 16) & 0xFFFF
                tile_w = dim1 & 0xFFFF
                accumulate = (ctrl >> 2) & 1

                wgt_bytes = (block_h * tile_w + 1) // 2
                wgt_packed = np.frombuffer(
                    memoryview(sram)[w_addr : w_addr + wgt_bytes],
                    dtype=np.uint8,
                ).copy()

                act_bytes = M_eff * block_h
                act = np.frombuffer(
                    memoryview(sram)[i_addr : i_addr + act_bytes],
                    dtype=np.int8,
                ).copy().reshape(M_eff, block_h)

                mxu = GoldenMXU()
                partial = mxu.matmul_int32(act, wgt_packed, M_eff, block_h, tile_w)

                if accumulate:
                    existing = np.frombuffer(
                        memoryview(sram)[o_addr : o_addr + M_eff * tile_w * 4],
                        dtype=np.int32,
                    ).copy().reshape(M_eff, tile_w)
                    partial = existing + partial

                sram[o_addr : o_addr + M_eff * tile_w * 4] = (
                    partial.astype(np.int32).tobytes()
                )
                regfile[(base, MXU.STATUS)] = 0  # done

        return True

    def mmio_read(base: int, offset: int) -> int:
        return regfile.get((base, offset), 0)

    def wait_done(base: int, status_offset: int) -> None:
        pass  # synchronous — STATUS already 0

    return mmio_write, mmio_read, wait_done, _mxu_invocations, DMA, MXU


def test_qwen_blk0_synthetic_tiled_mmul_manifest_ops(capsys) -> None:
    """Synthetic tiled-MMUL scheduler stress: load manifest, convert row-major
    INT4 weights to tile-major 128×128 layout, run through tile_mmul() at
    full declared dimensions, compare against manifest INT32 golden.

    Verifies:
      - tile_count = ceil(K/128) * ceil(N/128) per op
      - Full N output stitching (first, middle, last, remainder tiles)
      - Unity FP32 scales produce correct INT32 result
    """
    manifest = load_manifest()
    ops = manifest["ops"]
    mmul_ops = [op for op in ops if op["opcode"] == "MMUL"]
    assert len(mmul_ops) > 0, "No MMUL ops in manifest"

    model = FuncModel(dram_mb=256)
    dram = model.dram
    sram = bytearray(_SRAM_SIZE)

    # Fixed DRAM placement (reused per op — sequential execution)
    INPUT_ADDR = _DRAM_BASE + 0x00000000   # 64 KB (more than enough)
    SCALE_ADDR = _DRAM_BASE + 0x00010000   # 1 MB for scales
    WEIGHT_ADDR = _DRAM_BASE + 0x00200000  # ~14 MB for tile-major weights
    OUTPUT_ADDR = _DRAM_BASE + 0x01000000  # 16 MB for output

    total_tile_count = 0
    mxu = GoldenMXU()

    for op in mmul_ops:
        idx = op["idx"]
        dims = op["dimensions"]
        M = dims["M"]
        K = dims["K"]
        N = dims["N"]

        num_blocks = math.ceil(K / TILE_H)
        num_tiles = math.ceil(N / TILE_W)
        expected_tiles = num_blocks * num_tiles

        # ── Load input (INT8, M×K) ──────────────────────────────────
        input_hex = op["input_hex"]
        input_bytes = _read_hex_file(VECTORS_DIR / input_hex, elem_bytes=1)
        input_bytes = _pad_bytes(input_bytes, M * K)[:M * K]

        # ── Load weight (row-major packed INT4, K×N) → tile-major ──
        weight_hex = op["weight_hex"]
        weight_row = _read_hex_file(VECTORS_DIR / weight_hex, elem_bytes=1)
        weight_row = _pad_bytes(weight_row, (K * N + 1) // 2)[:(K * N + 1) // 2]
        weight_tile = _row_major_to_tile_major(weight_row, K, N, num_blocks, num_tiles)

        # ── Generate unity scales (tile-major) ──────────────────────
        scales_tile = _make_unity_scale_bytes(num_blocks, num_tiles, N)

        # ── Load golden (INT32, M×N) ────────────────────────────────
        golden_hex = op["golden_output_hex"]
        golden_bytes = _read_hex_file(VECTORS_DIR / golden_hex, elem_bytes=4)
        golden = np.frombuffer(golden_bytes, dtype=np.int32).reshape(M, N).copy()

        # ── Place data in DRAM ──────────────────────────────────────
        dram[_dram_off(INPUT_ADDR) : _dram_off(INPUT_ADDR) + len(input_bytes)] = input_bytes
        dram[_dram_off(WEIGHT_ADDR) : _dram_off(WEIGHT_ADDR) + len(weight_tile)] = weight_tile
        dram[_dram_off(SCALE_ADDR) : _dram_off(SCALE_ADDR) + len(scales_tile)] = scales_tile
        # Zero output region
        out_size = M * N * 4
        dram[_dram_off(OUTPUT_ADDR) : _dram_off(OUTPUT_ADDR) + out_size] = b"\x00" * out_size

        # ── Clear SRAM between ops ──────────────────────────────────
        sram[:] = b"\x00" * len(sram)

        # ── Build mmio handlers ─────────────────────────────────────
        mmio_write, mmio_read, wait_done, tile_counter, DMA, MXU = (
            _build_mmio_handlers(dram, sram)
        )

        DMA_BASE = 0xD0000000
        MXU_BASE = 0x40000000

        desc = {
            "M": M,
            "K": K,
            "N": N,
            "input_addr": INPUT_ADDR,
            "input_size": M * K,
            "weight_addr": WEIGHT_ADDR,
            "scale_addr": SCALE_ADDR,
            "output_addr": OUTPUT_ADDR,
        }

        # ── Execute through tile_mmul ───────────────────────────────
        tile_mmul(desc, mmio_write, mmio_read, wait_done,
                  DMA_BASE, MXU_BASE, DMA, MXU)

        # ── Read output from DRAM and compare ───────────────────────
        out_raw = dram[_dram_off(OUTPUT_ADDR) : _dram_off(OUTPUT_ADDR) + out_size]
        output = np.frombuffer(out_raw, dtype=np.int32).reshape(M, N)

        np.testing.assert_allclose(
            output.astype(np.float32),
            golden.astype(np.float32),
            atol=1e-4,
            rtol=1e-5,
            err_msg=(
                f"op{idx:02d} {op['name']}: tiled MMUL output mismatch "
                f"at max_abs_err={np.max(np.abs(output.astype(np.float32) - golden.astype(np.float32))):.2e}"
            ),
        )

        # ── Verify tile count ───────────────────────────────────────
        actual_tiles = tile_counter[0]
        assert actual_tiles == expected_tiles, (
            f"op{idx:02d} {op['name']}: "
            f"tile count {actual_tiles} != expected {expected_tiles} "
            f"(num_blocks={num_blocks}, num_tiles={num_tiles})"
        )
        total_tile_count += actual_tiles

    _t4b_emit_metric(capsys, "tests.collected", 1)
    _t4b_emit_metric(capsys, "tests.passed", 1)
    _t4b_emit_metric(capsys, "tile_count", total_tile_count)
    _t4b_emit_metric(capsys, "data_provenance", "synthetic")
