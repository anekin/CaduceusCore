"""Byte-for-byte equivalence guard between the two INT8 activation packers
(fm-hardening-phase10, todo 8 — C4 packer-equivalence contract).

ISSUE-13B root cause: `spike_host._pack_act_tile_major_contig()` wrote the
INT8 activation ROW-major, but the mxu_soc_wrapper broadcast presents
64-byte word ``c`` of each 4096-byte K-tile as column ``k`` (byte ``r`` =
``act[r, k]``) — a COLUMN-major broadcast layout.  That layout is what
`cocotb_bridge.pack_int8_activation_tile_major()` documents and what every
passing FM-SOC MMUL test relies on.  With the row-major buffer, cycle 0
presented all 64 activations of row 0 and cycles 1-63 presented zeros, so
the MAC array accumulated only the first K-term and every MMUL output in
the chain was garbage.

CONTRACT: both packers MUST produce the identical column-major broadcast
byte layout for the RTL golden path.  This module pins that contract
byte-for-byte across a grid of (M, K) shapes with deterministic random
INT8 activations, structurally pins the column-major layout itself (so a
regression where both packers drift to the same wrong layout is also
caught), and includes a failure-injection test that swaps in the pre-fix
row-major variant to prove the guard would catch the ISSUE-13B regression.
Neither packer implementation may be modified by this test.
"""

import numpy as np
import pytest

from sim import cocotb_bridge
from sim import spike_host

# (M, K) grid: covers single-row (M=1, where row-major and column-major
# byte layouts coincide), multi-row partial column fills (M<64), full
# column fill (M=64), and multi-K-tile shapes (K>64).
GRID = [(1, 64), (1, 128), (64, 128), (32, 256), (1, 2048), (64, 2048)]

# Fixed seed: deterministic random INT8 activations across every run.
SEED = 0x13B  # ISSUE-13B

TILE_BYTES = 4096  # 64 columns x 64 rows, K-tile stride


def _random_act(m: int, k: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    return rng.integers(-128, 127, size=(m, k), dtype=np.int8)


def _host_bytes(act: np.ndarray, m: int, k: int) -> bytes:
    return spike_host._pack_act_tile_major_contig(act, m, k).tobytes()


def _bridge_bytes(act: np.ndarray, m: int, k: int) -> bytes:
    return cocotb_bridge.pack_int8_activation_tile_major(act.tobytes(), m, k)


def _pack_act_row_major_pre_fix(act: np.ndarray, m: int, k: int) -> np.ndarray:
    """Replica of the pre-fix ISSUE-13B row-major packer shape.

    Each 4096-byte K-tile holds ``act[:, k_lo:k_hi].reshape(-1)``
    (row-major) instead of the column-major broadcast layout the hardware
    consumes.  Used only for failure injection.
    """
    k_tiles = (k + 63) // 64
    out = np.zeros(k_tiles * TILE_BYTES, dtype=np.uint8)
    act2 = np.ascontiguousarray(act)
    for kt in range(k_tiles):
        k_lo = kt * 64
        k_hi = min(k_lo + 64, k)
        tile = act2[:, k_lo:k_hi].reshape(-1)
        out[kt * TILE_BYTES:kt * TILE_BYTES + tile.size] = tile
    return out


@pytest.mark.parametrize("m,k", GRID)
def test_packers_byte_identical(m, k):
    """spike_host and cocotb_bridge packers must agree byte-for-byte."""
    act = _random_act(m, k)
    host = _host_bytes(act, m, k)
    bridge = _bridge_bytes(act, m, k)
    assert len(host) == ((k + 63) // 64) * TILE_BYTES
    assert len(bridge) == len(host)
    assert host == bridge, (
        f"packer divergence at (M,K)=({m},{k}): "
        f"first mismatch at byte {next((i for i, (a, b) in enumerate(zip(host, bridge)) if a != b), -1)}"
    )


def test_layout_is_column_major_broadcast():
    """Pin the layout itself: word c byte r of each K-tile == act[r, k].

    This catches a regression where BOTH packers drift to the same wrong
    layout (the byte-equivalence test alone cannot see that).
    """
    m, k = 64, 130  # 130 exercises both filled columns and the zero-pad tail
    act = _random_act(m, k)
    out = spike_host._pack_act_tile_major_contig(act, m, k)
    for kt in range((k + 63) // 64):
        for c in range(64):
            kk = kt * 64 + c
            if kk >= k:
                # Padding region must be zero (hardware never reads it).
                assert not out[kt * TILE_BYTES + c * 64:kt * TILE_BYTES + c * 64 + 64].any()
                continue
            np.testing.assert_array_equal(
                out[kt * TILE_BYTES + c * 64:kt * TILE_BYTES + c * 64 + 64],
                act[:, kk].astype(np.uint8),
                err_msg=f"layout violation at kt={kt} c={c}",
            )


@pytest.mark.parametrize("m,k", [(64, 128), (32, 256), (64, 2048)])
def test_row_major_variant_would_fail(monkeypatch, m, k):
    """Failure injection: pre-fix row-major packer must diverge from bridge.

    Swapping the host packer for its pre-fix ISSUE-13B row-major form makes
    the byte comparison fail, proving this suite catches the regression.
    (M=1 grid points are excluded: with a single row, row-major and
    column-major byte layouts coincide, which is exactly why the bug only
    surfaced in the real multi-row chain.)
    """
    monkeypatch.setattr(
        spike_host, "_pack_act_tile_major_contig", _pack_act_row_major_pre_fix
    )
    act = _random_act(m, k)
    assert _host_bytes(act, m, k) != _bridge_bytes(act, m, k)
