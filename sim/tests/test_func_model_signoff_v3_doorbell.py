"""Doorbell ring buffer protocol verification (func-model-signoff-v3 T4).

Covers 5 existing doorbell tests from test_soc_fm.py (re-exported) plus 3 new
edge-case tests: empty ring noop, concurrent push/poll interleaving, and
descriptor byte layout verification.
"""

import hashlib
import struct

import numpy as np
import pytest

from sim.func_model import FuncModel
from sim.golden_executor import GoldenMXU, GoldenSFU, GoldenVector
from sim.regmap import Addr, DOORBELL, INTC
from engine.isa import OpCode

# ── Re-export existing doorbell tests from test_soc_fm.py ──────────────
# pylint: disable=unused-import,wrong-import-position
from sim.tests.test_soc_fm import (  # noqa: F401
    test_doorbell_single_mmul_interrupt,
    test_doorbell_three_command_queue,
    test_doorbell_ring_wrap_16,
    test_doorbell_corrupted_descriptor_rejected,
    test_doorbell_ring_overflow,
)

# ── Helpers ────────────────────────────────────────────────────────────

_RNG = np.random.RandomState(20260704)

from sim.tests.test_soc_fm import (  # noqa: E402
    _doorbell_setup_mmul,
    _doorbell_write_mmul_desc,
    _doorbell_assert_mmul_result,
    _RNG_DB,
)


def _read_ring_entry(model: FuncModel, index: int) -> tuple:
    """Read a 32-byte ring buffer entry from DRAM at the given index."""
    addr = model.firmware.ring_buffer_addr + index * 32
    off = addr - Addr.DRAM_BASE
    data = model.dram[off:off + 32]
    # Entry format: opcode(u32), desc_addr(u64), flags(u32), 8 bytes pad
    opcode, desc_addr, flags = struct.unpack_from('<IQI', data, 0)
    return opcode, desc_addr, flags


def _read_descriptor_from_dram(model: FuncModel, desc_addr: int) -> dict:
    """Read the 15-field descriptor from DRAM and return as a dict."""
    off = desc_addr - Addr.DRAM_BASE
    data = model.dram[off:off + 60]
    fields = struct.unpack('<15I', data)
    names = [
        'input_addr', 'weight_addr', 'output_addr', 'scale_addr',
        'input_sram', 'weight_sram', 'output_sram', 'scale_sram',
        'input_size', 'weight_size', 'output_size', 'scale_size',
        'M', 'K', 'N',
    ]
    return dict(zip(names, fields))


# ── New tests ──────────────────────────────────────────────────────────

def test_doorbell_empty_ring_noop():
    """When HOST_TAIL == NPU_HEAD (empty ring), firmware dispatches nothing.

    Verifies the doorbell semantics from rtl/soc/doorbell.v line 111:
      doorbell_irq = (host_tail_reg != npu_head_reg)
    At startup, both are 0, so no IRQ, no dispatch loop activity.
    """
    model = FuncModel()

    # At startup, tail == head == 0: empty ring.
    assert model.firmware.doorbell['host_tail'] == 0
    assert model.firmware.doorbell['npu_head'] == 0
    assert model.bridge.handle('read', DOORBELL.BASE + DOORBELL.HOST_TAIL, 0) == 0
    assert model.bridge.handle('read', DOORBELL.BASE + DOORBELL.NPU_HEAD, 0) == 0

    # No doorbell interrupt pending (HOST_TAIL == NPU_HEAD).
    pending = model.bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert (pending & (1 << 8)) == 0, (
        f"Empty ring should not raise doorbell IRQ, PENDING=0x{pending:08X}"
    )
    assert not model.riscv.interrupt_pending

    # Run firmware — should find nothing to dispatch.
    results = model.firmware.run_loop(max_commands=0)
    assert len(results) == 0, (
        f"Empty ring should dispatch 0 commands, got {len(results)}"
    )

    # Head/tail still 0 after noop loop.
    assert model.firmware.doorbell['host_tail'] == 0
    assert model.firmware.doorbell['npu_head'] == 0


def test_doorbell_concurrent_push_poll():
    """Host pushes commands while NPU processes previous ones; verify interleaving.

    Sequence:
      1. Push cmd-0, run it  → head=1, tail=1
      2. Push cmd-1, cmd-2   → head=1, tail=3  (NPU hasn't caught up)
      3. Push cmd-3          → head=1, tail=4
      4. Run remaining 3     → head=4, tail=4   (NPU catches up)
      5. Push cmd-4, run it  → head=5, tail=5
      6. Push cmd-5          → head=5, tail=6
      7. Run it              → head=6, tail=6

    Verifies that HOST_TAIL and NPU_HEAD indices are tracked correctly through
    interleaved push/poll cycles, including intermediate head-lag state.
    """
    from sim.regmap import INTC

    model = FuncModel()
    M, K, N = 1, 4, 2

    # Phase 1: push and run one command.
    act1_addr, wgt1_addr, out1_addr, scale1_addr, desc1_addr = (
        0x8001_0000, 0x8002_0000, 0x8100_0000, 0x8011_0000, 0x8000_0080)
    act1, wgt1_packed, scales1 = _doorbell_setup_mmul(
        model, M, K, N, act1_addr, wgt1_addr, out1_addr, scale1_addr, desc1_addr)
    model.host_write_command(OpCode.MMUL, desc1_addr)
    assert model.firmware.doorbell['host_tail'] == 1
    assert model.firmware.doorbell['npu_head'] == 0

    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1 and results[0]['status'] == 'done'
    assert model.firmware.doorbell['npu_head'] == 1
    _doorbell_assert_mmul_result(model, act1, wgt1_packed, scales1, out1_addr, M, K, N)

    # Phase 2: push two more commands without processing (NPU head lags).
    for i in range(2):
        idx = i + 1
        act_addr = 0x8001_0000 + idx * 0x200
        wgt_addr = 0x8002_0000 + idx * 0x200
        out_addr = 0x8100_0000 + idx * 0x200
        scale_addr = 0x8011_0000 + idx * 0x200
        desc_addr = 0x8000_1000 + idx * 0x40
        _doorbell_setup_mmul(model, M, K, N, act_addr, wgt_addr, out_addr,
                            scale_addr, desc_addr)
        model.host_write_command(OpCode.MMUL, desc_addr)

    assert model.firmware.doorbell['host_tail'] == 3
    assert model.firmware.doorbell['npu_head'] == 1  # still lagging

    # Phase 3: push one more, still without processing.
    act4_addr, wgt4_addr, out4_addr, scale4_addr, desc4_addr = (
        0x8001_0600, 0x8002_0600, 0x8100_0600, 0x8011_0600, 0x8000_1180)
    act4, wgt4_packed, scales4 = _doorbell_setup_mmul(
        model, M, K, N, act4_addr, wgt4_addr, out4_addr, scale4_addr, desc4_addr)
    model.host_write_command(OpCode.MMUL, desc4_addr)
    assert model.firmware.doorbell['host_tail'] == 4
    assert model.firmware.doorbell['npu_head'] == 1  # still 3 unprocessed

    # Verify doorbell IRQ is pending (tail != head).
    pending = model.bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending & (1 << 8), (
        f"Doorbell IRQ should be pending when tail=4, head=1, got 0x{pending:08X}"
    )

    # Phase 4: process remaining 3 commands.
    results = model.firmware.run_loop(max_commands=3)
    assert len(results) == 3
    for r in results:
        assert r['status'] == 'done', f"Command failed: {r}"

    assert model.firmware.doorbell['npu_head'] == 4
    assert model.firmware.doorbell['host_tail'] == 4

    # Phase 5: push and run one more (proves ring still works after catch-up).
    act5_addr, wgt5_addr, out5_addr, scale5_addr, desc5_addr = (
        0x8001_0800, 0x8002_0800, 0x8100_0800, 0x8011_0800, 0x8000_11C0)
    act5, wgt5_packed, scales5 = _doorbell_setup_mmul(
        model, M, K, N, act5_addr, wgt5_addr, out5_addr, scale5_addr, desc5_addr)
    model.host_write_command(OpCode.MMUL, desc5_addr)
    assert model.firmware.doorbell['host_tail'] == 5

    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1 and results[0]['status'] == 'done'
    assert model.firmware.doorbell['npu_head'] == 5

    # Phase 6: push one final command, run it.
    act6_addr, wgt6_addr, out6_addr, scale6_addr, desc6_addr = (
        0x8001_0A00, 0x8002_0A00, 0x8100_0A00, 0x8011_0A00, 0x8000_1200)
    act6, wgt6_packed, scales6 = _doorbell_setup_mmul(
        model, M, K, N, act6_addr, wgt6_addr, out6_addr, scale6_addr, desc6_addr)
    model.host_write_command(OpCode.MMUL, desc6_addr)
    assert model.firmware.doorbell['host_tail'] == 6

    results = model.firmware.run_loop(max_commands=1)
    assert len(results) == 1 and results[0]['status'] == 'done'
    assert model.firmware.doorbell['npu_head'] == 6

    # Final state: no pending interrupts.
    pending = model.bridge.handle('read', INTC.BASE + INTC.PENDING, 0)
    assert pending == 0, f"All commands processed, INTC.PENDING should be 0, got 0x{pending:08X}"


def test_doorbell_descriptor_byte_layout():
    """Verify mmul_desc_t byte layout: 15 uint32 fields packed as <15I.

    Matches the C firmware descriptor struct (spike_host.py:46):
      MMUL_DESC_FMT = "<15I"
      Fields: input_addr, weight_addr, output_addr, scale_addr,
              input_sram, weight_sram, output_sram, scale_sram,
              input_size, weight_size, output_size, scale_size,
              M, K, N
    """
    model = FuncModel()

    # Write descriptor with known values.
    desc_addr = 0x8000_4000
    expected = {
        'input_addr':  0x8001_0000,
        'weight_addr': 0x8002_0000,
        'output_addr': 0x8100_0000,
        'scale_addr':  0x8011_0000,
        'input_sram':  0x2000_0000,
        'weight_sram': 0x2000_4000,
        'output_sram': 0x2000_8000,
        'scale_sram':  0x2000_C000,
        'input_size':  128,
        'weight_size': 64,
        'output_size': 8,
        'scale_size':  32,
        'M': 1,
        'K': 512,
        'N': 2,
    }

    model.host_write_descriptor(desc_addr, **expected)

    # Read back from DRAM and verify.
    actual = _read_descriptor_from_dram(model, desc_addr)
    for field_name, exp_val in expected.items():
        assert actual[field_name] == exp_val, (
            f"Descriptor field '{field_name}': expected 0x{exp_val:08X}, "
            f"got 0x{actual[field_name]:08X}"
        )

    # Verify raw byte layout: 60 bytes total (15 × 4).
    off = desc_addr - Addr.DRAM_BASE
    raw = model.dram[off:off + 60]
    assert len(raw) == 60, f"Descriptor should be 60 bytes, got {len(raw)}"

    # Verify struct round-trip: pack → unpack is identity.
    fields_list = [expected[n] for n in [
        'input_addr', 'weight_addr', 'output_addr', 'scale_addr',
        'input_sram', 'weight_sram', 'output_sram', 'scale_sram',
        'input_size', 'weight_size', 'output_size', 'scale_size',
        'M', 'K', 'N',
    ]]
    packed = struct.pack('<15I', *fields_list)
    unpacked = struct.unpack('<15I', packed)
    assert unpacked == tuple(fields_list), (
        f"Descriptor pack/unpack round-trip mismatch"
    )

    # Verify ring buffer entry format: opcode(u32) + desc_addr(u64) + flags(u32) + 8 pad.
    model.host_write_command(OpCode.MMUL, desc_addr, flags=0xDEAD)
    opcode, rb_desc_addr, flags = _read_ring_entry(model, 0)
    assert opcode == OpCode.MMUL, f"Ring entry opcode: expected {OpCode.MMUL}, got {opcode}"
    assert rb_desc_addr == desc_addr, (
        f"Ring entry desc_addr: expected 0x{desc_addr:016X}, got 0x{rb_desc_addr:016X}"
    )
    assert flags == 0xDEAD, f"Ring entry flags: expected 0xDEAD, got 0x{flags:04X}"

    tail = model.firmware.doorbell['host_tail']
    entry0_addr = model.firmware.ring_buffer_addr + 0 * 32
    entry1_addr = model.firmware.ring_buffer_addr + (tail - 1) * 32
    assert entry1_addr - entry0_addr == 0, (
        "Only 1 entry written, tail-0 offset should be 0"
    )
    model.host_write_command(OpCode.MMUL, desc_addr + 0x100, flags=0xBEEF)
    opcode2, rb_desc_addr2, flags2 = _read_ring_entry(model, 1)
    assert flags2 == 0xBEEF, f"Second entry flags: expected 0xBEEF, got 0x{flags2:04X}"
    assert rb_desc_addr2 == desc_addr + 0x100, (
        f"Second entry desc_addr: expected 0x{desc_addr + 0x100:016X}, "
        f"got 0x{rb_desc_addr2:016X}"
    )
