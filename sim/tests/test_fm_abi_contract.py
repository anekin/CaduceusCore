"""FM ABI contract tests (todo 4, bug-012-fm-audit).

Three FM contracts pinned as explicit assertions:
(a) MXU DIM1 (spec/npu_abi.json:147-153, offset 0x10, "[15:0]=N columns")
    holds the REAL N: write N / read back == N for N in {2, 33, 64}, and a
    compute command driven with DIM1=N=33 through the MMIOBridge
    (_run_mxu_compute path) yields dense M×N INT32 output in SRAM bit-equal
    to GoldenMXU.matmul_int32 — the FM computes with the actual N, no
    64-column output padding.
(b) GoldenMXU output is dense row-major M×N: element (r, c) sits at flat
    index r*N + c.
(c) Zero-padding audit: the engine-N pad-up formula (DIM1 rounded up to a
    64 multiple) exists ONLY in the RTL-driver files sim/cocotb_bridge.py
    and sim/diagnose_data_layout.py — the FM domain must have zero hits.

Oracle independence: imports only the devices under test (golden_executor,
cocotb_bridge pack helper, mmio_bridge, regmap). Forbidden-module imports
(models / engine / timing / npu_sim) must not appear.
"""

import os
import re

import numpy as np
import pytest

from cocotb_bridge import pack_int8_activation_tile_major
from golden_executor import GoldenMXU
from mmio_bridge import MMIOBridge
from regmap import MXU

SEED = 42
# Engine-N pad-up pattern (round N up to a 64 multiple), as it appears in
# the RTL drivers. Kept as a regex so the audit never matches this file.
PAD_PATTERN = re.compile(
    r"\(\([^)]*(?:dim_n|\bN\b)\s*\+\s*63\)\s*//\s*64\)\s*\*\s*64"
)


# ══════════════════════════════════════════════════════════════════════
# (a) DIM1 = actual N
# ══════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("N", [2, 33, 64])
def test_dim1_write_readback_real_n(N):
    """MXU DIM1 register write/read-back: stores the actual N, not padded."""
    bridge = MMIOBridge(modules={"mxu": GoldenMXU()})
    bridge.handle("write", MXU.BASE + MXU.DIM1, N)
    assert bridge.handle("read", MXU.BASE + MXU.DIM1) == N, (
        f"DIM1 must read back the actual N={N}"
    )


def test_dim1_n33_compute_dense_no_padding():
    """DIM1=N=33 compute: dense M×N INT32 in SRAM == matmul_int32(M,K,33)."""
    M, K, N = 8, 64, 33
    rng = np.random.default_rng(SEED)
    act = rng.integers(-128, 128, size=(M, K), dtype=np.int8)
    wgt = rng.integers(-8, 8, size=(K, N), dtype=np.int8)
    w_packed = GoldenMXU.pack_int4(wgt)
    # Activation staged in the 4096-byte K-tile broadcast layout, weights as
    # dense packed bytes — this is exactly how _run_mxu_compute reads them.
    act_packed = pack_int8_activation_tile_major(act.tobytes(), M, K)

    sram = bytearray(0x400000)
    i_off, w_off, o_off = 0x1000, 0x4000, 0x8000
    sram[i_off:i_off + len(act_packed)] = act_packed
    sram[w_off:w_off + len(w_packed)] = w_packed.tobytes()
    # Sentinel tail: FM padding output to 64 columns would clobber this.
    tail_start = o_off + M * N * 4
    tail_end = o_off + M * 64 * 4
    sram[tail_start:tail_end] = b"\xAB" * (tail_end - tail_start)

    bridge = MMIOBridge(modules={"mxu": GoldenMXU(), "sram": sram})
    bridge.handle("write", MXU.BASE + MXU.DIM0, (K << 16) | M)
    bridge.handle("write", MXU.BASE + MXU.DIM1, N)
    bridge.handle("write", MXU.BASE + MXU.I_ADDR, i_off)
    bridge.handle("write", MXU.BASE + MXU.W_ADDR, w_off)
    bridge.handle("write", MXU.BASE + MXU.O_ADDR, o_off)
    bridge.handle("write", MXU.BASE + MXU.CMD, 1)

    got = np.frombuffer(sram[o_off:o_off + M * N * 4],
                        dtype=np.int32).reshape(M, N)
    ref = GoldenMXU().matmul_int32(act, w_packed, M, K, N)
    assert np.array_equal(got, ref), "DIM1=N=33 compute must be bit-exact"
    assert bytes(sram[tail_start:tail_end]) == b"\xAB" * (tail_end - tail_start), (
        "FM must write dense M×N output — no padding to 64 columns"
    )


# ══════════════════════════════════════════════════════════════════════
# (b) Dense row-major output contract
# ══════════════════════════════════════════════════════════════════════

def test_golden_mxu_dense_row_major():
    """GoldenMXU output: dense C-contiguous M×N; element (r,c) at r*N+c."""
    M, K, N = 32, 128, 2
    rng = np.random.default_rng(SEED)
    act = rng.integers(-128, 128, size=(M, K), dtype=np.int8)
    wgt = rng.integers(-8, 8, size=(K, N), dtype=np.int8)
    out = GoldenMXU().matmul_int32(act, GoldenMXU.pack_int4(wgt), M, K, N)

    assert out.shape == (M, N), "output shape must be exactly (M, N)"
    assert out.flags.c_contiguous, "output must be dense row-major"
    flat = out.ravel()
    for r, c in ((0, 0), (0, N - 1), (M - 1, 0), (M - 1, N - 1), (7, 1)):
        assert out[r, c] == flat[r * N + c], (
            f"row-major indexing broken at (r={r}, c={c})"
        )


# ══════════════════════════════════════════════════════════════════════
# (c) FM-domain zero-padding audit
# ══════════════════════════════════════════════════════════════════════

def test_dim1_padding_audit_only_in_rtl_drivers():
    """Pad-up formula lives ONLY in the two RTL-driver files under sim/."""
    expected = {
        os.path.join("sim", "cocotb_bridge.py"),
        os.path.join("sim", "diagnose_data_layout.py"),
    }
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sim_dir = os.path.join(repo_root, "sim")
    hits = []
    for root, _dirs, files in os.walk(sim_dir):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                if PAD_PATTERN.search(fh.read()):
                    hits.append(os.path.relpath(path, repo_root))
    assert sorted(hits) == sorted(expected), (
        f"pad-up formula files={sorted(hits)} — contract allows only the "
        "RTL drivers {sim/cocotb_bridge.py, sim/diagnose_data_layout.py}"
    )
