"""Anti-vacuous gate for _golden_compare().

Proves that golden comparison is real — NOT vacuous:
- Valid golden_output with matching actual → PASS (True)
- Golden_output=None → raises ValueError (no longer silently skips)
- One byte corrupted in actual output → FAIL (False)
- FP16 tolerance: small perturbations within tolerance → PASS
- FP16 tolerance: large perturbations beyond tolerance → FAIL

T2 is NOT complete until this test passes.
"""

import asyncio
import struct

import pytest

from cocotb_bridge import CocotbBridge, NPUInstruction


def test_golden_matching_int32():
    """Valid golden_output with exact match → True."""
    bridge = CocotbBridge()
    golden = bytes(range(64))  # 16 INT32 elements
    instr = NPUInstruction(
        opcode="MMUL", op_id=0, elements=16,
        o_addr=0x20020000, golden_output=golden,
        output_elem_bytes=4, name="test_match",
    )
    actual = bytearray(golden)
    result = asyncio.run(bridge._golden_compare(instr, actual))
    assert result is True, "Exact byte match should return True"


def test_golden_missing_raises():
    """golden_output=None → raises ValueError (no longer silent skip)."""
    bridge = CocotbBridge()
    instr = NPUInstruction(
        opcode="MMUL", op_id=0, elements=16,
        o_addr=0x20020000, golden_output=None,
        output_elem_bytes=4, name="test_none",
    )
    actual = bytearray(64)
    with pytest.raises(ValueError, match="No golden_output provided"):
        asyncio.run(bridge._golden_compare(instr, actual))


def test_golden_corrupted_one_byte():
    """Corrupt one byte of actual output → False (first-mismatch logged)."""
    bridge = CocotbBridge()
    golden = bytes(range(64))
    instr = NPUInstruction(
        opcode="MMUL", op_id=0, elements=16,
        o_addr=0x20020000, golden_output=golden,
        output_elem_bytes=4, name="test_corrupt",
    )
    actual = bytearray(golden)
    actual[7] ^= 0xFF  # Corrupt byte 7
    result = asyncio.run(bridge._golden_compare(instr, actual))
    assert result is False, "Corrupted byte should cause comparison FAIL"


def test_golden_length_mismatch():
    """Actual output shorter than golden → False."""
    bridge = CocotbBridge()
    golden = bytes(range(64))
    instr = NPUInstruction(
        opcode="MMUL", op_id=0, elements=16,
        o_addr=0x20020000, golden_output=golden,
        output_elem_bytes=4, name="test_short",
    )
    actual = bytearray(golden[:60])  # 4 bytes short
    result = asyncio.run(bridge._golden_compare(instr, actual))
    assert result is False, "Length mismatch should cause comparison FAIL"


def test_golden_corrupted_int32_value():
    """Corrupt one INT32 element (4 bytes) → False."""
    bridge = CocotbBridge()
    golden = struct.pack("<16i", *range(16))  # 16 INT32: 0,1,2,...,15
    instr = NPUInstruction(
        opcode="MMUL", op_id=0, elements=16,
        o_addr=0x20020000, golden_output=golden,
        output_elem_bytes=4, name="test_int32_corrupt",
    )
    actual = bytearray(golden)
    # Corrupt the 5th element (bytes 16-19): change 4 → 999
    struct.pack_into("<i", actual, 16, 999)
    result = asyncio.run(bridge._golden_compare(instr, actual))
    assert result is False, "Corrupted INT32 element should cause FAIL"


def test_golden_fp16_match():
    """FP16 exact match → True."""
    bridge = CocotbBridge()
    # 8 FP16 values: 0.0, 1.0, 2.0, 3.0, -1.0, 0.5, 100.0, -0.25
    fp16_values = [0.0, 1.0, 2.0, 3.0, -1.0, 0.5, 100.0, -0.25]
    golden = struct.pack("<8e", *fp16_values)
    instr = NPUInstruction(
        opcode="SFU_SOFTMAX", op_id=0, elements=8,
        o_addr=0x20020000, golden_output=golden,
        output_elem_bytes=2, name="test_fp16_match",
    )
    actual = bytearray(golden)
    result = asyncio.run(bridge._golden_compare(instr, actual))
    assert result is True, "FP16 exact match should return True"


def test_golden_fp16_within_tolerance():
    """FP16 values within tolerance (abs=1e-3, rel=1e-2) → True."""
    bridge = CocotbBridge()
    # Golden: [1.0, 2.0, 100.0, -0.5]
    fp16_golden = [1.0, 2.0, 100.0, -0.5]
    # Actual with tiny epsilon (all within tolerance)
    fp16_actual = [1.0001, 2.005, 100.5, -0.5002]
    golden = struct.pack("<4e", *fp16_golden)
    instr = NPUInstruction(
        opcode="SFU_RMSNORM", op_id=6, elements=4,
        o_addr=0x20020000, golden_output=golden,
        output_elem_bytes=2, name="test_fp16_tolerance",
    )
    actual = struct.pack("<4e", *fp16_actual)
    result = asyncio.run(bridge._golden_compare(instr, bytearray(actual)))
    assert result is True, "FP16 values within tolerance should pass"


def test_golden_fp16_beyond_tolerance():
    """FP16 values beyond tolerance → False."""
    bridge = CocotbBridge()
    fp16_golden = [1.0, 2.0, 100.0, -0.5]
    # Actual with large error beyond tolerance
    fp16_actual = [1.0, 2.0, 0.0, -0.5]  # 100 → 0: abs_err=100 >> 1e-3
    golden = struct.pack("<4e", *fp16_golden)
    instr = NPUInstruction(
        opcode="SFU_SOFTMAX", op_id=0, elements=4,
        o_addr=0x20020000, golden_output=golden,
        output_elem_bytes=2, name="test_fp16_fail",
    )
    actual = struct.pack("<4e", *fp16_actual)
    result = asyncio.run(bridge._golden_compare(instr, bytearray(actual)))
    assert result is False, "FP16 values beyond tolerance should FAIL"


def test_golden_fp16_len_mismatch():
    """FP16 length mismatch → False."""
    bridge = CocotbBridge()
    golden = struct.pack("<4e", 1.0, 2.0, 3.0, 4.0)
    instr = NPUInstruction(
        opcode="SFU_SOFTMAX", op_id=0, elements=4,
        o_addr=0x20020000, golden_output=golden,
        output_elem_bytes=2, name="test_fp16_len",
    )
    actual = bytearray(struct.pack("<2e", 1.0, 2.0))  # only 2 elements
    result = asyncio.run(bridge._golden_compare(instr, actual))
    assert result is False, "FP16 length mismatch should FAIL"
