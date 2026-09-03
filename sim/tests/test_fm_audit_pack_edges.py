"""FM audit pack/tile/scale attack tests (todo 3, bug-012-fm-audit).

(a) Zero-fill contract for pack_int8_activation_tile_major /
    pack_int4_tile_major: partial 64-tiles must be zero-padded and total
    byte counts must match the 64-wide tile geometry. M is restricted to
    {4, 64} — pack is a single-64-tile primitive with no m-tiling; M=65 is
    covered by (b) tile-ceil and GoldenMXU tiling instead.
(b) Tile-schedule ceil arithmetic (n/k/m tiles) — same formulas as
    sim/cocotb_bridge.py:1839-1841.
(c) _read_scale_hex FP16→FP32 reshape (pad short blocks with zero, truncate
    over-long) + MMIOBridge accumulate: two K=64 commands (2nd with CTRL
    bit[2]) sharing one O_ADDR must equal a single matmul_int32(K=128) call
    — INT32 bit-exact, no tolerance.

Oracle independence: this file imports only the devices under test
(golden_executor, cocotb_bridge pack/scale helpers, mmio_bridge, regmap).
Forbidden-module imports (models / engine / timing / npu_sim) must not appear.
"""

import numpy as np
import pytest

from cocotb_bridge import (
    _read_scale_hex,
    pack_int4_tile_major,
    pack_int8_activation_tile_major,
)
from golden_executor import GoldenMXU
from mmio_bridge import MMIOBridge
from regmap import MXU

SEED = 42


# ══════════════════════════════════════════════════════════════════════
# (a) Pack zero-fill
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("M", [4, 64])
@pytest.mark.parametrize("K", [64, 129])
def test_pack_activation_zero_fill(M, K):
    """pack_int8_activation_tile_major: k_tiles×64×64 bytes; pad rows≥M, k≥K."""
    rng = np.random.default_rng(SEED)
    act = rng.integers(-128, 128, size=(M, K), dtype=np.int8)
    packed = pack_int8_activation_tile_major(act.tobytes(), M, K)

    k_tiles = (K + 63) // 64
    assert len(packed) == k_tiles * 64 * 64, "act pack must be k_tiles×64×64 bytes"

    buf = np.frombuffer(packed, dtype=np.uint8).reshape(k_tiles, 64, 64)
    # Axis layout: [k_tile, k-within-tile, row]. Rows >= M must be zero.
    assert np.count_nonzero(buf[:, :, M:]) == 0, "rows >= M must be zero-filled"
    # k-positions >= K (tail of last k-tile) must be zero.
    if K % 64:
        assert np.count_nonzero(buf[k_tiles - 1, K % 64:, :]) == 0, (
            "k >= K must be zero-filled"
        )
    # Round-trip the real region: buf[kt, k, r] == act[r, kt*64 + k].
    for r in range(M):
        for k in range(K):
            assert int(buf[k // 64, k % 64, r]) == int(act[r, k]) & 0xFF, (
                f"round-trip mismatch at (r={r}, k={k})"
            )


def _unpack_int4_tiles(packed: bytes, k_tiles: int, n_tiles: int) -> np.ndarray:
    """Decode tile-major INT4 bytes into (n_tiles, k_tiles, 64, 64) int8."""
    out = np.zeros((n_tiles, k_tiles, 64, 64), dtype=np.int8)
    idx = 0
    for nt in range(n_tiles):
        for kt in range(k_tiles):
            for tr in range(64):
                for tc in range(0, 64, 2):
                    b = packed[idx]
                    idx += 1
                    lo = b & 0xF
                    hi = (b >> 4) & 0xF
                    out[nt, kt, tr, tc] = lo - 16 if lo > 7 else lo
                    out[nt, kt, tr, tc + 1] = hi - 16 if hi > 7 else hi
    return out


@pytest.mark.parametrize("N", [2, 10, 33])
@pytest.mark.parametrize("K", [64, 129])
def test_pack_weight_zero_fill(N, K):
    """pack_int4_tile_major: n_tiles×k_tiles×2048 bytes; pad rows≥K, cols≥N."""
    rng = np.random.default_rng(SEED)
    wgt = rng.integers(-8, 8, size=(K, N), dtype=np.int8)
    dense = GoldenMXU.pack_int4(wgt)  # dense row-major nibble pack, (K*N+1)//2 bytes
    packed = pack_int4_tile_major(dense.tobytes(), K, N)

    k_tiles = (K + 63) // 64
    n_tiles = (N + 63) // 64
    assert len(packed) == n_tiles * k_tiles * 2048, (
        "wgt pack must be n_tiles×k_tiles×2048 bytes"
    )

    buf = _unpack_int4_tiles(packed, k_tiles, n_tiles)
    for nt in range(n_tiles):
        for kt in range(k_tiles):
            for tr in range(64):
                r = kt * 64 + tr
                for tc in range(64):
                    c = nt * 64 + tc
                    if r < K and c < N:
                        assert buf[nt, kt, tr, tc] == wgt[r, c], (
                            f"round-trip mismatch at (r={r}, c={c})"
                        )
                    else:
                        assert buf[nt, kt, tr, tc] == 0, (
                            f"pad region (r={r}, c={c}) must be zero"
                        )


# ══════════════════════════════════════════════════════════════════════
# (b) Tile-ceil arithmetic
# ══════════════════════════════════════════════════════════════════════

def test_tile_ceil_arithmetic():
    """Tile-schedule ceil boundaries (cocotb_bridge.py:1839-1841 formulas)."""
    assert [(N + 63) // 64 for N in (33, 64, 65)] == [1, 1, 2], "n_tiles ceil"
    assert [(K + 63) // 64 for K in (64, 128, 129)] == [1, 2, 3], "k_tiles ceil"
    assert [(M + 63) // 64 for M in (64, 65)] == [1, 2], "m_tiles ceil"


# ══════════════════════════════════════════════════════════════════════
# (c) Scale reshape + accumulate equivalence
# ══════════════════════════════════════════════════════════════════════

def _write_scale_hex(path, values):
    """Write FP16 values as one uint16 hex per line (read_hex_file_bytes fmt)."""
    lines = []
    for v in values:
        u16 = int(np.frombuffer(np.array(v, dtype=np.float16).tobytes(),
                                dtype="<u2")[0])
        lines.append(f"{u16:04X}")
    path.write_text("\n".join(lines) + "\n")


def test_read_scale_hex_reshape(tmp_path):
    """_read_scale_hex: (num_blocks, N) float32; pad short blocks, truncate long."""
    p = tmp_path / "scales.hex"
    _write_scale_hex(p, [1.0, 2.0, 0.5, -1.5])
    scales = _read_scale_hex(str(p), K=256, N=2, group_size=128)
    assert scales.shape == (2, 2), "(256+127)//128 = 2 blocks × N=2"
    assert scales.dtype == np.float32
    assert np.array_equal(scales.ravel(), np.array([1.0, 2.0, 0.5, -1.5],
                                                   dtype=np.float32))

    # Missing block → zero-padded to num_blocks×N.
    p2 = tmp_path / "short.hex"
    _write_scale_hex(p2, [1.0, 2.0, 0.5])
    short = _read_scale_hex(str(p2), K=256, N=2, group_size=128)
    assert short.shape == (2, 2)
    assert short[1, 1] == 0.0, "missing block must zero-pad"

    # Over-long → truncated to num_blocks×N.
    p3 = tmp_path / "long.hex"
    _write_scale_hex(p3, [1.0, 2.0, 0.5, -1.5, 7.0, 8.0])
    long_ = _read_scale_hex(str(p3), K=256, N=2, group_size=128)
    assert long_.shape == (2, 2)
    assert long_[1, 1] == -1.5, "over-long input must truncate"


def test_mmio_accumulate_equals_single_k128():
    """Two K=64 MMIO commands (2nd CTRL bit[2] accumulate) == one K=128 matmul."""
    M, K, N = 8, 128, 10
    rng = np.random.default_rng(SEED)
    act = rng.integers(-128, 128, size=(M, K), dtype=np.int8)
    wgt = rng.integers(-8, 8, size=(K, N), dtype=np.int8)
    w_packed = GoldenMXU.pack_int4(wgt)  # dense packed bytes, (K*N+1)//2 = 640

    # Pre-allocate SRAM: mmio_bridge.py:292-293 silently no-ops on empty sram.
    sram = bytearray(0x400000)
    i_off, w_off, o_off = 0x1000, 0x4000, 0x8000
    act_packed = pack_int8_activation_tile_major(act.tobytes(), M, K)
    sram[i_off:i_off + len(act_packed)] = act_packed
    sram[w_off:w_off + len(w_packed)] = w_packed.tobytes()

    bridge = MMIOBridge(modules={"mxu": GoldenMXU(), "sram": sram})

    def run_cmd(k_half: int, accumulate: bool):
        bridge.handle("write", MXU.BASE + MXU.DIM0, (64 << 16) | M)
        bridge.handle("write", MXU.BASE + MXU.DIM1, N)
        bridge.handle("write", MXU.BASE + MXU.I_ADDR, i_off + k_half * 4096)
        bridge.handle("write", MXU.BASE + MXU.W_ADDR, w_off + k_half * 32 * N)
        bridge.handle("write", MXU.BASE + MXU.O_ADDR, o_off)
        bridge.handle("write", MXU.BASE + MXU.CTRL, 4 if accumulate else 0)
        bridge.handle("write", MXU.BASE + MXU.CMD, 1)

    run_cmd(0, accumulate=False)
    run_cmd(1, accumulate=True)

    got = np.frombuffer(sram[o_off:o_off + M * N * 4],
                        dtype=np.int32).reshape(M, N)
    ref = GoldenMXU().matmul_int32(act, w_packed, M, K, N)
    assert np.array_equal(got, ref), (
        "two-command accumulate must be INT32 bit-exact to single K=128 matmul"
    )
