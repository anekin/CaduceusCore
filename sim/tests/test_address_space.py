"""Unit tests for sim/address_space.py (fm-hardening-phase10, todo 1).

Pins the DRAM address-space contract against its external truth sources:

- ``sim/spike_host.py``:44,66,67,347-352
  (FIRMWARE_RING_BASE, DESC_BASE, DESC_STRIDE, P10_ACT_*, P10_WGT_*)
- ``spec/npu_abi.json``:1435,1579-1582
  (rings.configuration: ring_buffer_addr, ring_entries, cmd_entry_size,
   completion_ring_addr, completion_entry_size)

Contract semantics under test:

- ``regions_overlap(a, b)`` operates on half-open ``[base, base + size)``
  intervals; touching boundaries are NOT overlap.
- ``addr_in_window(addr, size)`` checks containment in the 8 MB window
  ``[0x80000000, 0x80800000)``.
- ``contract_check()`` asserts (a) ``desc_base >=`` completion-ring end and
  (b) ``desc_base + desc_count * DESC_STRIDE <= act_base`` when ``act_base``
  is not None; violations raise ``OverlapError``, out-of-window raises
  ``WindowError``.
"""

import json
from pathlib import Path

import pytest

from sim import spike_host
from sim.address_space import (
    CMD_ENTRY_SIZE,
    COMPLETION_RING_ADDR,
    DESC_BASE,
    DESC_STRIDE,
    DRAM_BASE,
    DRAM_END,
    DRAM_SIZE,
    P10_ACT_BASE,
    P10_ACT_END,
    P10_WGT_BASE,
    P10_WGT_END,
    REGIONS,
    RING_ENTRIES,
    OverlapError,
    WindowError,
    addr_in_window,
    contract_check,
    regions_overlap,
)

_ABI_PATH = Path(__file__).resolve().parents[2] / "spec" / "npu_abi.json"


def _abi():
    with open(_ABI_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ── Characterization: constants match the external truth sources ───


def test_region_table_matches_spike_host_constants():
    """REGIONS and constants must match sim/spike_host.py:44,66,67,347-352."""
    assert DRAM_BASE == spike_host.FIRMWARE_RING_BASE
    assert DESC_BASE == spike_host.DESC_BASE
    assert DESC_STRIDE == spike_host.DESC_STRIDE
    assert P10_ACT_BASE == spike_host.P10_ACT_BASE
    assert P10_ACT_END == spike_host.P10_ACT_END
    assert P10_WGT_BASE == spike_host.P10_WGT_BASE
    assert P10_WGT_END == spike_host.P10_WGT_END
    assert REGIONS["command_ring"] == (
        spike_host.FIRMWARE_RING_BASE,
        RING_ENTRIES * CMD_ENTRY_SIZE,
    )
    assert REGIONS["descriptor_pool"] == (
        spike_host.DESC_BASE,
        spike_host.P10_ACT_BASE - spike_host.DESC_BASE,
    )
    assert REGIONS["activation"] == (
        spike_host.P10_ACT_BASE,
        spike_host.P10_ACT_END - spike_host.P10_ACT_BASE,
    )
    assert REGIONS["weight"] == (
        spike_host.P10_WGT_BASE,
        spike_host.P10_WGT_END - spike_host.P10_WGT_BASE,
    )


def test_region_table_matches_abi_constants():
    """Ring constants must match spec/npu_abi.json:1579-1582."""
    cfg = _abi()["rings"]["configuration"]
    assert DRAM_BASE == int(cfg["ring_buffer_addr"], 16)
    assert RING_ENTRIES == cfg["ring_entries"]
    assert CMD_ENTRY_SIZE == cfg["cmd_entry_size"]
    assert COMPLETION_RING_ADDR == int(cfg["completion_ring_addr"], 16)
    assert REGIONS["completion_ring"] == (
        COMPLETION_RING_ADDR,
        cfg["ring_entries"] * cfg["completion_entry_size"],
    )


# ── Region overlap / window primitives ─────────────────────────────


def test_desc_region_disjoint_from_ring():
    """Descriptor pool must not overlap command or completion rings."""
    assert not regions_overlap("descriptor_pool", "command_ring")
    assert not regions_overlap("descriptor_pool", "completion_ring")
    # Full default pool (1024 descriptors ends exactly at 0x80020000)
    # satisfies the ring contract, with and without the act bound.
    contract_check(desc_base=DESC_BASE, desc_count=1024)
    contract_check(desc_base=DESC_BASE, desc_count=1024, act_base=P10_ACT_BASE)


def test_desc_in_window():
    """Descriptor region sits inside the 8 MB window [0x80000000, 0x80800000)."""
    assert addr_in_window(DESC_BASE, DESC_STRIDE)
    assert addr_in_window(DESC_BASE, 1024 * DESC_STRIDE)  # full pool
    assert not addr_in_window(DESC_BASE, 0x8000000)       # far beyond 8 MB
    assert not addr_in_window(0x80800000)                 # window end exclusive
    assert addr_in_window(0x807FFFFF)                     # last byte inside
    assert addr_in_window(DRAM_BASE, DRAM_SIZE)           # whole window
    assert not addr_in_window(DRAM_BASE, DRAM_SIZE + 1)
    assert not addr_in_window(0x7FFFFFFF)                 # below window base


def test_regions_touching_boundaries_do_not_overlap():
    assert not regions_overlap("command_ring", "completion_ring")
    assert not regions_overlap("completion_ring", "descriptor_pool")
    assert not regions_overlap("activation", "weight")
    # Symmetric with raw (base, size) tuples.
    assert not regions_overlap((0x80008000, 0x8000), (0x80010000, 0x1000))
    assert not regions_overlap((0x80010000, 0x1000), (0x80008000, 0x8000))


def test_regions_overlap_detects_true_overlaps():
    assert regions_overlap((0x80010000, 0x1000), (0x80010F00, 0x1000))
    assert regions_overlap((0x80010F00, 0x1000), (0x80010000, 0x1000))
    assert regions_overlap("descriptor_pool", (0x8001F000, 0x2000))
    # Touching the activation base is still not an overlap.
    assert not regions_overlap("descriptor_pool", (0x80020000, 0x1000))


def test_named_regions_are_mutually_disjoint():
    names = sorted(REGIONS)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert not regions_overlap(a, b), (a, b)


def test_all_named_regions_fit_in_8mb_window():
    for name, (base, size) in REGIONS.items():
        assert addr_in_window(base, size), name


# ── contract_check semantics ───────────────────────────────────────


def test_contract_check_happy_qa_command():
    """Happy QA command from the plan passes without error."""
    contract_check(desc_base=0x80010000, desc_count=20)
    contract_check(desc_base=0x80010000, desc_count=20, act_base=P10_ACT_BASE)


def test_contract_check_desc_below_completion_end_raises_overlap():
    with pytest.raises(OverlapError):
        contract_check(desc_base=0x80001000, desc_count=20)  # QA failure case
    with pytest.raises(OverlapError):
        contract_check(desc_base=COMPLETION_RING_ADDR, desc_count=1)
    # Boundary: desc_base == completion-ring end is legal.
    contract_check(desc_base=0x80010000, desc_count=1)


def test_contract_check_act_base_bound_enforced_and_skippable():
    # desc_end == act_base is legal; one descriptor past it is not.
    contract_check(desc_base=DESC_BASE, desc_count=1024, act_base=P10_ACT_BASE)
    with pytest.raises(OverlapError):
        contract_check(desc_base=DESC_BASE, desc_count=1025, act_base=P10_ACT_BASE)
    # act_base=None skips assertion (b): the same span passes.
    contract_check(desc_base=DESC_BASE, desc_count=1025, act_base=None)


def test_contract_check_window_violations_raise_window_error():
    with pytest.raises(WindowError):
        contract_check(desc_base=0x807FFF00, desc_count=16)  # desc_end 0x80800300
    with pytest.raises(WindowError):
        contract_check(desc_base=0x7FFF0000, desc_count=1)   # below window base
    with pytest.raises(WindowError):
        contract_check(desc_base=DESC_BASE, desc_count=1, act_base=0x80800001)


def test_parameterized_ring_entries_per_runner_layouts():
    # P0-like per-runner layout: 32-entry ring, completion end 0x80000800.
    contract_check(ring_entries=32, desc_base=0x80001000, desc_count=32)
    # Same descriptor base is illegal under the 1024-entry firmware ring.
    with pytest.raises(OverlapError):
        contract_check(ring_entries=1024, desc_base=0x80001000, desc_count=32)


def test_contract_check_defaults_use_module_constants():
    contract_check()
    assert DRAM_END == DRAM_BASE + DRAM_SIZE == 0x80800000
    below_completion_end = DESC_BASE - 0x1000
    with pytest.raises(OverlapError):
        contract_check(desc_base=below_completion_end)
