"""INTC ENABLE/THRESHOLD gating verification (SOC-17 / FW-10).

Verifies that ``MMIOBridge._set_irq`` evaluates the INTC gate before
notifying the CPU (``irq_notify_callback``):

    cpu_irq = |(PENDING & ENABLE)  and  popcount(PENDING & ENABLE) >= THRESHOLD

mirroring ``rtl/intc/intc_top.v``.  The FM keeps the legacy open-by-default
contract: when ENABLE is never programmed, all 9 sources are treated as
enabled so pre-gating interrupt flows keep working.  Once ENABLE is
written, masked sources can no longer raise cpu_irq.  PENDING always
accumulates set bits regardless of ENABLE — the mask gates the cpu_irq
assertion, not the pending register (matches RTL + test_soc_boundary).

Coverage:
  - single-source IRQ (happy)
  - ENABLE masking
  - THRESHOLD popcount gating
  - ACK clear / re-assert
  - WFI wake on gated IRQ + no wake when masked
  - multi-source concurrency
  - failure injection: ENABLE=0 → cpu_irq stays low regardless of PENDING
"""

import struct

from miniv import RISCVMini
from mmio_bridge import MMIOBridge
from regmap import INTC

WFI_INSN = (0x305 << 20) | 0x73  # funct12=0x305, opcode=SYSTEM


def _bridge_with_spy():
    """MMIOBridge whose irq_notify_callback records cpu_irq assertions."""
    fired = []
    bridge = MMIOBridge(irq_notify_callback=lambda: fired.append(1))
    return bridge, fired


def _wfi_rig(enable: int, threshold: int):
    """Bridge + RISCVMini wired for interrupt delivery, spinning in WFI."""
    bridge = MMIOBridge()
    handled = []
    emu = RISCVMini()
    emu.mmio_callback = bridge.handle
    emu.irq_handler = handled.append
    bridge.irq_notify_callback = emu.set_interrupt_pending
    bridge.handle("write", INTC.BASE + INTC.ENABLE, enable)
    bridge.handle("write", INTC.BASE + INTC.THRESHOLD, threshold)
    # Two back-to-back WFI instructions so the CPU spins across pc=0,4.
    emu.mem[0:4] = struct.pack("<I", WFI_INSN)
    emu.mem[4:8] = struct.pack("<I", WFI_INSN)
    emu.state.pc = 0
    emu.running = True
    return bridge, emu, handled


# ══════════════════════════════════════════════════════════════════════
# Legacy open-default contract (pinned by pre-change baseline)
# ══════════════════════════════════════════════════════════════════════


def test_default_state_notifies():
    """No INTC registers programmed → _set_irq still notifies.

    Pins the legacy open-default contract: before the gating change,
    every _set_irq call raised cpu_irq regardless of ENABLE/THRESHOLD.
    The gating implementation preserves this when ENABLE is unprogrammed
    (unset ENABLE masks nothing).
    """
    bridge, fired = _bridge_with_spy()
    bridge._set_irq(0)
    bridge._set_irq(8)
    assert len(fired) == 2, f"Expected default-state notify, got {len(fired)}"
    pending = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending & (1 << 0) and pending & (1 << 8)


# ══════════════════════════════════════════════════════════════════════
# Gating semantics
# ══════════════════════════════════════════════════════════════════════


def test_single_source_irq_asserts_cpu_irq():
    """Happy path: MXU IRQ + ENABLE + THRESHOLD=1 → cpu_irq asserted."""
    bridge, fired = _bridge_with_spy()
    bridge.handle("write", INTC.BASE + INTC.ENABLE, 1 << 0)
    bridge.handle("write", INTC.BASE + INTC.THRESHOLD, 1)
    bridge._set_irq(0)
    assert len(fired) == 1, "Expected one cpu_irq assertion"
    pending = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending & (1 << 0), f"PENDING[0] should be set, got 0x{pending:08X}"


def test_enable_mask_blocks_disabled_source():
    """ENABLE=1<<0 masks SFU (bit 1): PENDING set, cpu_irq stays low."""
    bridge, fired = _bridge_with_spy()
    bridge.handle("write", INTC.BASE + INTC.ENABLE, 1 << 0)
    bridge.handle("write", INTC.BASE + INTC.THRESHOLD, 1)
    bridge._set_irq(1)
    assert len(fired) == 0, "Masked source must not raise cpu_irq"
    pending = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending & (1 << 1), (
        f"PENDING[1] set regardless of ENABLE, got 0x{pending:08X}"
    )


def test_threshold_gates_below_popcount():
    """THRESHOLD=2 with ENABLE=0b111: 1 source below gate, 2 sources pass."""
    bridge, fired = _bridge_with_spy()
    bridge.handle("write", INTC.BASE + INTC.ENABLE, 0b111)
    bridge.handle("write", INTC.BASE + INTC.THRESHOLD, 2)
    bridge._set_irq(0)  # popcount = 1 < 2 → no cpu_irq
    assert len(fired) == 0, "popcount 1 must stay below THRESHOLD=2"
    bridge._set_irq(1)  # popcount = 2 >= 2 → cpu_irq
    assert len(fired) == 1, "popcount 2 must cross THRESHOLD=2"


def test_multi_source_concurrent_notifies():
    """ENABLE=0b1011 + THRESHOLD=1: MXU/SFU/DMA each assert cpu_irq."""
    bridge, fired = _bridge_with_spy()
    bridge.handle("write", INTC.BASE + INTC.ENABLE, 0b1011)
    bridge.handle("write", INTC.BASE + INTC.THRESHOLD, 1)
    bridge._set_irq(0)
    bridge._set_irq(1)
    bridge._set_irq(3)
    assert len(fired) == 3, f"Expected 3 cpu_irq assertions, got {len(fired)}"
    pending = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending == 0b1011, f"Expected PENDING=0b1011, got 0x{pending:08X}"


def test_multi_source_threshold_waits_for_count():
    """THRESHOLD=3: cpu_irq only after the 3rd enabled source pends."""
    bridge, fired = _bridge_with_spy()
    bridge.handle("write", INTC.BASE + INTC.ENABLE, 0b1011)
    bridge.handle("write", INTC.BASE + INTC.THRESHOLD, 3)
    bridge._set_irq(0)
    bridge._set_irq(1)
    assert len(fired) == 0, "popcount 2 < THRESHOLD=3"
    bridge._set_irq(3)
    assert len(fired) == 1, "popcount 3 must cross THRESHOLD=3"


def test_ack_clears_pending_and_irq_can_reassert():
    """ACK clears PENDING; a later _set_irq re-asserts cpu_irq."""
    bridge, fired = _bridge_with_spy()
    bridge.handle("write", INTC.BASE + INTC.ENABLE, 1 << 0)
    bridge.handle("write", INTC.BASE + INTC.THRESHOLD, 1)

    bridge._set_irq(0)
    assert len(fired) == 1
    bridge.handle("write", INTC.BASE + INTC.ACK, 1 << 0)
    pending = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending == 0, f"ACK must clear PENDING, got 0x{pending:08X}"

    bridge._set_irq(0)
    assert len(fired) == 2, "IRQ must re-assert after ACK + new event"


# ══════════════════════════════════════════════════════════════════════
# WFI wake via RISCVMini
# ══════════════════════════════════════════════════════════════════════


def test_wfi_wake_on_gated_irq():
    """CPU spinning in WFI wakes, traps, and ACKs a gated MXU IRQ."""
    bridge, emu, handled = _wfi_rig(enable=1 << 0, threshold=1)

    assert emu.step(), "WFI spin step should return True"
    assert not emu.interrupt_pending

    bridge._set_irq(0)
    assert emu.interrupt_pending, "cpu_irq must set interrupt_pending"

    assert emu.step(), "WFI wake step should return True"
    assert handled == [0], f"Trap handler should dispatch source 0, got {handled}"
    assert not emu.interrupt_pending, "interrupt_pending cleared after trap"
    pending = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending == 0, f"Trap handler ACK must clear PENDING, got 0x{pending:08X}"


def test_wfi_no_wake_when_masked():
    """ENABLE=0: IRQ fires but CPU in WFI never wakes (cpu_irq stays low)."""
    bridge, emu, handled = _wfi_rig(enable=0, threshold=1)

    assert emu.step(), "WFI spin step should return True"
    bridge._set_irq(0)
    assert not emu.interrupt_pending, "masked IRQ must not wake CPU"

    assert emu.step(), "WFI should spin (NOP) with no pending IRQ"
    assert handled == [], "Trap handler must not run for masked IRQ"
    pending = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
    assert pending & (1 << 0), "PENDING still set — cpu_irq low regardless"


# ══════════════════════════════════════════════════════════════════════
# Failure injection
# ══════════════════════════════════════════════════════════════════════


def test_failure_enable_zero_blocks_cpu_irq_regardless_of_pending():
    """Failure injection: ENABLE=0 → cpu_irq stays low regardless of PENDING.

    Fires MXU/SFU/DMA/Host sources; PENDING accumulates all bits but the
    cpu_irq callback must never fire.  Also holds at THRESHOLD=0 (the
    anti-vacuous corner: popcount(0) >= 0 must NOT raise cpu_irq).
    """
    for threshold in (0, 1):
        bridge, fired = _bridge_with_spy()
        bridge.handle("write", INTC.BASE + INTC.ENABLE, 0)
        bridge.handle("write", INTC.BASE + INTC.THRESHOLD, threshold)
        bridge._set_irq(0)
        bridge._set_irq(1)
        bridge._set_irq(3)
        bridge._set_irq(8)
        assert len(fired) == 0, (
            f"ENABLE=0 THRESHOLD={threshold}: cpu_irq must stay low, "
            f"got {len(fired)} assertions"
        )
        pending = bridge.handle("read", INTC.BASE + INTC.PENDING, 0)
        assert pending == (1 << 0) | (1 << 1) | (1 << 3) | (1 << 8), (
            f"PENDING must accumulate all fired sources, got 0x{pending:08X}"
        )
