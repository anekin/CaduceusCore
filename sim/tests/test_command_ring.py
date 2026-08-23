"""Unit tests for sim/command_ring.py (fm-hardening-phase10, todo 3)."""

import pytest

from sim import address_space
from sim import command_ring


def test_expected_head_wraps_at_ring_entries():
    assert command_ring.expected_head(0) == 0
    assert command_ring.expected_head(1023) == 1023
    assert command_ring.expected_head(1024) == 0
    assert command_ring.expected_head(1300) == 1300 % 1024


def test_ring_entry_addr_wraps():
    assert command_ring.ring_entry_addr(0) == command_ring.RING_BASE
    assert command_ring.ring_entry_addr(1023) == command_ring.RING_BASE + 1023 * command_ring.CMD_ENTRY_SIZE
    assert command_ring.ring_entry_addr(1024) == command_ring.RING_BASE


def test_advance_head_wraps():
    assert command_ring.advance_head(0, 1) == 1
    assert command_ring.advance_head(1023, 1) == 0
    assert command_ring.advance_head(1000, 100) == 1000 + 100 - 1024


def test_p0_scoped_layout_guard():
    """P0: 32-cmd limit, desc 0x80001000 disjoint from used ring/completion."""
    command_ring.assert_ring_size(32, ring_size=32)
    command_ring.assert_desc_clear_of_used_regions(
        desc_base=0x80001000,
        desc_count=32,
        ring_usage_end=command_ring.RING_BASE + 32 * command_ring.CMD_ENTRY_SIZE,
        completion_usage_end=command_ring.COMPLETION_RING_ADDR + 32 * 32,
    )

    with pytest.raises(command_ring.RingOverflowError):
        command_ring.assert_ring_size(33, ring_size=32)


def test_p4_layout_contract():
    """P4: 1024-entry ring, desc base 0x80048000, act_base=0x80800000 passes."""
    address_space.contract_check(
        ring_entries=command_ring.RING_ENTRIES,
        desc_base=0x80048000,
        desc_count=23,
        act_base=0x80800000,
    )
