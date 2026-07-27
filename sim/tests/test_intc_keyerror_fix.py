"""Test that _handle_intc ACK-before-PENDING does not raise KeyError.

This regression test covers the fix for Issue 003: when Spike firmware
issues INTC.ACK before any INTC.PENDING register has been written,
_handle_intc at mmio_bridge.py:590 would crash with:

    KeyError: 1073766400   (= INTC.BASE + INTC.PENDING)

Note: 1073766400 decimal = 0x40006000 = INTC.BASE + INTC.PENDING.
"""

import pytest
from sim.mmio_bridge import MMIOBridge
from sim.regmap import INTC


# ══════════════════════════════════════════════════════════════════════
# Test (a): ACK before any PENDING write does not crash
# ══════════════════════════════════════════════════════════════════════


def test_ack_before_pending_no_crash():
    """ACK on an uninitialized PENDING dict must not raise KeyError,
    and PENDING must remain 0."""
    bridge = MMIOBridge()

    # ACK bit 0 (MXU) without ever writing PENDING
    bridge.handle('write', INTC.BASE + INTC.ACK, 1 << 0)

    pending = bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending == 0, (
        f"Expected PENDING=0 after ACK on empty dict, got 0x{pending:08X}"
    )


# ══════════════════════════════════════════════════════════════════════
# Test (b): _set_irq(8) then ACK clears the correct bit
# ══════════════════════════════════════════════════════════════════════


def test_ack_clears_correct_bit():
    """After _set_irq(8), ACK bit 8 must clear only that bit.
    Other bits (if any) must be preserved."""
    bridge = MMIOBridge()

    # Use _set_irq to set bit 8 (Host Doorbell interrupt)
    bridge._set_irq(8)  # pylint: disable=protected-access

    pending_before = bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending_before & (1 << 8), (
        f"Expected PENDING bit 8 set after _set_irq(8), "
        f"got 0x{pending_before:08X}"
    )

    # ACK bit 8
    bridge.handle('write', INTC.BASE + INTC.ACK, 1 << 8)

    pending_after = bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending_after == 0, (
        f"Expected PENDING=0 after ACK of sole bit 8, "
        f"got 0x{pending_after:08X}"
    )


def test_ack_preserves_other_bits():
    """When multiple bits are pending, ACK of one bit must preserve others."""
    bridge = MMIOBridge()

    # Set bits 1 (SFU) and 3 (DMA) simultaneously via direct write
    bridge.handle('write', INTC.BASE + INTC.PENDING, (1 << 1) | (1 << 3))

    # ACK only bit 1
    bridge.handle('write', INTC.BASE + INTC.ACK, 1 << 1)

    pending = bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert not (pending & (1 << 1)), (
        f"Bit 1 should be cleared after ACK, got 0x{pending:08X}"
    )
    assert pending & (1 << 3), (
        f"Bit 3 should remain pending, got 0x{pending:08X}"
    )


# ══════════════════════════════════════════════════════════════════════
# Test (c): Multiple consecutive ACKs on empty PENDING do not crash
# ══════════════════════════════════════════════════════════════════════


def test_multiple_consecutive_acks_no_crash():
    """Multiple ACK writes on an empty PENDING dict must not crash,
    and PENDING must remain 0 after each."""
    bridge = MMIOBridge()

    for i in range(10):
        bit = i % 9  # cycle through bits 0-8
        bridge.handle('write', INTC.BASE + INTC.ACK, 1 << bit)
        pending = bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
        assert pending == 0, (
            f"After ACK #{i} (bit {bit}): expected PENDING=0, "
            f"got 0x{pending:08X}"
        )
