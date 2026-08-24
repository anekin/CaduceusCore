"""AXI crossbar arbitration fairness FM guard (SOC-14).

Verifies multi-master round-robin fairness via ``CrossbarModel._aw_grants``
and ``_ar_grants`` history, DECERR rejection of unmapped addresses, AXI ID
composition (``master_id << 8 | txn_id``), and failure injection that proves
the fairness assertions are real (not vacuous).

Reuses ``sim/models/crossbar.py`` as-is — no model changes.
"""

import pytest

from func_model import FuncModel
from regmap import Addr
from models.crossbar import CrossbarModel


NUM_TXNS = 100
MASTERS = [
    CrossbarModel.MASTER_MXU,
    CrossbarModel.MASTER_DMA,
    CrossbarModel.MASTER_PCIE,
]


# ── Fairness guards (test-local, no model mutation) ──────────────────


def _grant_counts(history, slave_idx=None):
    """Count grants per master, optionally filtered to one slave."""
    counts = {}
    for (s, m) in history:
        if slave_idx is not None and s != slave_idx:
            continue
        counts[m] = counts.get(m, 0) + 1
    return counts


def _assert_fair_history(history, masters, per_master, slave_idx=None):
    """Round-robin fairness guard: every master gets exactly `per_master` grants.

    Fails if the crossbar starves any master or inflates another's share.
    """
    counts = _grant_counts(history, slave_idx)
    total = sum(counts.values())
    assert total == len(masters) * per_master, (
        f"grant total {total} != expected {len(masters) * per_master}"
    )
    for m in masters:
        got = counts.get(m, 0)
        assert got == per_master, (
            f"master {m}: {got} grants, expected {per_master} — unfair arbitration"
        )


def _assert_strict_alternation(history, slave_idx=None):
    """Guard: consecutive grants to one slave never repeat the same master."""
    seq = [m for (s, m) in history if slave_idx is None or s == slave_idx]
    assert len(seq) >= 2
    for prev, cur in zip(seq, seq[1:]):
        assert prev != cur, "same master granted twice consecutively — starvation"


# ══════════════════════════════════════════════════════════════════════
# Happy paths — round-robin fairness over grant history
# ══════════════════════════════════════════════════════════════════════


def test_round_robin_aw_fairness_three_masters():
    """3 masters × 100 interleaved writes to SRAM (S0): AW grant history
    alternates evenly in round-robin order and all payloads roundtrip."""
    model = FuncModel()
    xbar = model.crossbar

    addr_base = 0x2000_1000
    for i in range(NUM_TXNS):
        for slot, m in enumerate(MASTERS):
            payload = bytes([m & 0xFF, i & 0xFF, (i + m) & 0xFF, 0xAA])
            xbar.write(m, addr_base + i * 64 + slot * 16, payload)

    aw = xbar._aw_grants
    assert len(aw) == NUM_TXNS * len(MASTERS), "unexpected AW grant count"
    _assert_fair_history(aw, MASTERS, NUM_TXNS, slave_idx=0)
    _assert_strict_alternation(aw, slave_idx=0)
    # Round-robin pointer ends on the last master of the final round.
    assert xbar._aw_last_granted[0] == MASTERS[-1]

    # Every written payload must roundtrip bit-exact through SRAM.
    for i in range(NUM_TXNS):
        for slot, m in enumerate(MASTERS):
            off = addr_base + i * 64 + slot * 16 - Addr.SRAM_BASE
            expect = bytes([m & 0xFF, i & 0xFF, (i + m) & 0xFF, 0xAA])
            assert bytes(model.sram[off:off + 4]) == expect, \
                f"SRAM roundtrip mismatch (txn {i}, master {m})"


def test_round_robin_ar_fairness_dram():
    """3 masters × 100 interleaved reads from DRAM (S1): AR grant history
    alternates evenly, each master reads back its own seeded data."""
    model = FuncModel()
    xbar = model.crossbar

    addr_base = 0x8000_2000
    for m in MASTERS:
        payload = bytes([m & 0xFF] * 8)
        off = addr_base - Addr.DRAM_BASE + m * 8
        model.dram[off:off + 8] = payload

    for _ in range(NUM_TXNS):
        for m in MASTERS:
            data = xbar.read(m, addr_base + m * 8, 8)
            assert data == bytes([m & 0xFF] * 8), \
                f"DRAM read mismatch for master {m}"

    ar = xbar._ar_grants
    assert len(ar) == NUM_TXNS * len(MASTERS), "unexpected AR grant count"
    _assert_fair_history(ar, MASTERS, NUM_TXNS, slave_idx=1)
    _assert_strict_alternation(ar, slave_idx=1)
    assert xbar._ar_last_granted[1] == MASTERS[-1]


# ══════════════════════════════════════════════════════════════════════
# DECERR — bad addresses rejected without consuming arbitration slots
# ══════════════════════════════════════════════════════════════════════


def test_decerr_unmapped_address_rejected_without_grant():
    """Unmapped addresses raise ValueError (DECERR) and must NOT consume an
    arbitration grant or an AXI transaction ID."""
    model = FuncModel()
    xbar = model.crossbar

    aw_before = list(xbar._aw_grants)
    ar_before = list(xbar._ar_grants)
    txn_before = list(xbar._txn_ids)

    bad_addrs = [
        0x0000_0000,                          # boot ROM — not crossbar-routed
        Addr.SRAM_BASE - 1,                   # just below SRAM window
        Addr.SRAM_BASE + len(model.sram),     # just above SRAM window
        Addr.DRAM_BASE - 1,                   # just below DRAM window
        Addr.DRAM_BASE + len(model.dram),     # just above DRAM window
        0x5000_0000,                          # APB MMIO region — not crossbar
        0xFFFF_FFFF,                          # top of 32-bit space
    ]
    for addr in bad_addrs:
        with pytest.raises(ValueError, match="DECERR"):
            xbar.read(CrossbarModel.MASTER_IBEX, addr, 4)
        with pytest.raises(ValueError, match="DECERR"):
            xbar.write(CrossbarModel.MASTER_IBEX, addr, b"decerr")

    # DECERR must not pollute grant history or AXI ID counters.
    assert xbar._aw_grants == aw_before, "DECERR consumed an AW grant"
    assert xbar._ar_grants == ar_before, "DECERR consumed an AR grant"
    assert xbar._txn_ids == txn_before, "DECERR consumed an AXI transaction ID"

    # Out-of-range master IDs are rejected too.
    with pytest.raises(ValueError, match="Invalid master_id"):
        xbar.read(CrossbarModel.NUM_MASTERS, 0x2000_0000, 4)
    with pytest.raises(ValueError, match="Invalid master_id"):
        xbar.write(-1, 0x2000_0000, b"bad")


# ══════════════════════════════════════════════════════════════════════
# AXI ID routing — master_id << 8 | txn_id preserved per master
# ══════════════════════════════════════════════════════════════════════


def test_axi_id_composition_master_id_shift_txn_id():
    """AXI ID = (master_id << 8) | txn_id; per-master counters advance
    independently and wrap at 8 bits."""
    model = FuncModel()
    xbar = model.crossbar

    # First transaction from master 5 (PCIe) composes ID 0x0500.
    xbar.write(CrossbarModel.MASTER_PCIE, 0x2000_0000, b"a")
    assert xbar._last_axi_id == (CrossbarModel.MASTER_PCIE << 8) | 0

    # Read from master 0 (Ibex) — its own first ID 0x0000, fields separable.
    xbar.read(CrossbarModel.MASTER_IBEX, 0x2000_0000, 1)
    axi_id = xbar._last_axi_id
    assert (axi_id >> 8) == CrossbarModel.MASTER_IBEX, "master field lost"
    assert (axi_id & 0xFF) == 0, "txn field wrong"

    # Per-master counters advance independently.
    for m in MASTERS:
        xbar._txn_ids[m] = 0
        assert xbar._next_axi_id(m) == (m << 8) | 0
        assert xbar._next_axi_id(m) == (m << 8) | 1
        assert xbar._txn_ids[m] == 2

    # txn_id wraps at 8 bits: 0xFF -> 0x00.
    xbar._txn_ids[CrossbarModel.MASTER_MXU] = 0xFF
    assert xbar._next_axi_id(CrossbarModel.MASTER_MXU) == \
        (CrossbarModel.MASTER_MXU << 8) | 0xFF
    assert xbar._next_axi_id(CrossbarModel.MASTER_MXU) == \
        (CrossbarModel.MASTER_MXU << 8) | 0x00
    assert xbar._txn_ids[CrossbarModel.MASTER_MXU] == 1


# ══════════════════════════════════════════════════════════════════════
# Failure injection — tampered grant history must fail the guards
# ══════════════════════════════════════════════════════════════════════


def test_failure_injection_tampered_grant_history():
    """Anti-vacuous: tampering with grant history makes the fairness and
    alternation guards fail. Proves the happy-path assertions bite."""
    model = FuncModel()
    xbar = model.crossbar

    for _ in range(NUM_TXNS):
        for m in MASTERS:
            xbar.write(m, 0x2000_1000 + m * 4, bytes([m & 0xFF] * 4))

    # Happy: unmodified history passes the fairness guard.
    _assert_fair_history(xbar._aw_grants, MASTERS, NUM_TXNS, slave_idx=0)

    # Injection 1: pad one master with 50 phantom grants → skew detected.
    xbar._aw_grants += [(0, CrossbarModel.MASTER_PCIE)] * 50
    with pytest.raises(AssertionError):
        _assert_fair_history(xbar._aw_grants, MASTERS, NUM_TXNS, slave_idx=0)

    # Injection 2: drop every grant of one master → missing share detected.
    xbar._aw_grants = [(s, m) for (s, m) in xbar._aw_grants
                       if m != CrossbarModel.MASTER_DMA]
    with pytest.raises(AssertionError):
        _assert_fair_history(xbar._aw_grants, MASTERS, NUM_TXNS, slave_idx=0)

    # Injection 3: batch-per-master history (no interleave) is fair by count
    # but never alternates → strict-alternation guard fails.
    xbar._aw_grants = []
    for m in MASTERS:
        xbar._aw_grants += [(0, m)] * NUM_TXNS
    _assert_fair_history(xbar._aw_grants, MASTERS, NUM_TXNS, slave_idx=0)
    with pytest.raises(AssertionError):
        _assert_strict_alternation(xbar._aw_grants, slave_idx=0)
