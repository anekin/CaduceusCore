"""RED tests for NPUFirmware opcode dispatch (Task 8/26 F2).

Current _dispatch() only handles opcode==0 (MMUL).
Non-MMUL opcodes return status='unknown'.

Expected:
  - test_dispatch_mmul()    → PASS (opcode 0 handled)
  - test_dispatch_sfu()     → FAIL (unknown opcode)
  - test_dispatch_vector()  → FAIL (unknown opcode)
  - test_dispatch_dma()     → FAIL (unknown opcode)
"""

import struct

import pytest

from engine.isa import OpCode, NPUInstruction, NPUEncoder
from sim.miniv import NPUFirmware

# ── Helpers ────────────────────────────────────────────────────────────


def _make_firmware() -> NPUFirmware:
    """Create a minimal NPUFirmware instance with DRAM (no bridge)."""
    dram = bytearray(64 * 1024 * 1024)
    return NPUFirmware(sim_modules={"dram": dram})


def _write_descriptor(firmware: NPUFirmware, desc_addr: int = 0x8000_0100) -> int:
    """Write a valid 15-uint32 descriptor to DRAM at desc_addr.

    Indices: [12]=M, [13]=K, [14]=N must be positive integers
    for tile_mmul() validation.
    """
    fields = [0] * 15
    fields[12] = 1    # M
    fields[13] = 128  # K
    fields[14] = 128  # N
    data = struct.pack("<15I", *fields)
    off = desc_addr - 0x8000_0000
    firmware.mod["dram"][off : off + 60] = data
    return desc_addr


def _isa_opcode(opcode: OpCode) -> int:
    """Build an ISA instruction and return its opcode integer."""
    instr = NPUInstruction(opcode=opcode)
    NPUEncoder.encode(instr)
    return instr.opcode.value


def _make_cmd(opcode_val: int, desc_addr: int = 0x8000_0100) -> dict:
    """Build a command dict for _dispatch()."""
    return {"opcode": opcode_val, "desc_addr": desc_addr, "flags": 0}


# ── Tests ──────────────────────────────────────────────────────────────


def test_dispatch_mmul():
    """MMUL should dispatch successfully → status='done'."""
    fw = _make_firmware()
    _write_descriptor(fw)
    op = _isa_opcode(OpCode.MMUL)  # 0
    result = fw._dispatch(_make_cmd(op))
    assert result["status"] == "done"


def test_dispatch_sfu():
    """SFU dispatch currently returns 'unknown' — test expects 'done' (RED)."""
    fw = _make_firmware()
    _write_descriptor(fw)
    op = _isa_opcode(OpCode.SOFTMAX)  # 0x01
    result = fw._dispatch(_make_cmd(op))
    assert result["status"] == "done", (
        f"SFU dispatch returned '{result['status']}', expected 'done'"
    )


def test_dispatch_vector():
    """VECTOR dispatch currently returns 'unknown' — test expects 'done' (RED)."""
    fw = _make_firmware()
    _write_descriptor(fw)
    op = _isa_opcode(OpCode.VADD)  # 0x0F
    result = fw._dispatch(_make_cmd(op))
    assert result["status"] == "done", (
        f"VECTOR dispatch returned '{result['status']}', expected 'done'"
    )


def test_dispatch_dma():
    """DMA dispatch currently returns 'unknown' — test expects 'done' (RED)."""
    fw = _make_firmware()
    _write_descriptor(fw)
    op = _isa_opcode(OpCode.DMA_LD)  # 0x09
    result = fw._dispatch(_make_cmd(op))
    assert result["status"] == "done", (
        f"DMA dispatch returned '{result['status']}', expected 'done'"
    )
