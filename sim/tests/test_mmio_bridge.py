"""
RED tests: MMIOBridge SFU/Vector stub handlers return DONE without computing.

Both _handle_sfu() and _handle_vector() toggle STATUS→BUSY(1)→DONE(2)
immediately on CMD write, but never invoke the actual GoldenSFU/GoldenVector
computation. Output SRAM remains zero — these tests assert non-zero and
WILL FAIL until the handlers are fixed.
"""

import numpy as np

from sim.mmio_bridge import MMIOBridge
from sim.regmap import SFU, VECTOR


def _write_reg(bridge, module, offset, value):
    """Write 32-bit value to a module's MMIO register."""
    bridge.handle("write", module.BASE + offset, value)


def _read_reg(bridge, module, offset):
    """Read 32-bit value from a module's MMIO register."""
    return bridge.handle("read", module.BASE + offset)


def test_sfu_handler_computes():
    """Configure SFU softmax via MMIO, expect output SRAM non-zero.

    Starts SRAM with a known float16 input (0..63), writes all SFU config
    registers, then triggers START. The stub immediately sets STATUS=DONE
    without computing — so the output stays zero and this test FAILS.
    """
    sram = bytearray(1024 * 1024)
    bridge = MMIOBridge(modules={"sram": sram})

    # Input: 64 float16 values [0, 1, 2, ..., 63]
    input_data = np.arange(64, dtype=np.float16)
    in_addr = 0x2000
    out_addr = 0x3000
    sram[in_addr : in_addr + len(input_data.tobytes())] = input_data.tobytes()

    # Zero out the output region so we can detect computation
    sram[out_addr : out_addr + 128] = b"\x00" * 128

    # Configure SFU registers
    _write_reg(bridge, SFU, SFU.CTRL, 0)       # SOFTMAX
    _write_reg(bridge, SFU, SFU.I_ADDR, in_addr)
    _write_reg(bridge, SFU, SFU.O_ADDR, out_addr)
    _write_reg(bridge, SFU, SFU.DIM, 64)       # 64 elements

    # Trigger computation
    _write_reg(bridge, SFU, SFU.CMD, 1)        # START

    # Verify DONE status (stub does this correctly)
    status = _read_reg(bridge, SFU, SFU.STATUS)
    assert status == 2, f"SFU STATUS should be DONE(2), got {status}"

    # ❌ THIS ASSERTION WILL FAIL — stub never writes output
    output = np.frombuffer(sram[out_addr : out_addr + 128], dtype=np.float16)
    assert np.any(output != 0), (
        "SFU output is all zeros — stub returned DONE without computing"
    )


def test_vector_handler_computes():
    """Configure Vector ADD via MMIO, expect output non-zero.

    Places two INT32 arrays [1..8] and [10..80] in SRAM, writes all Vector
    config registers, then triggers START. The stub immediately sets STATUS=
    DONE without computing — so the output stays zero and this test FAILS.
    """
    sram = bytearray(1024 * 1024)
    bridge = MMIOBridge(modules={"sram": sram})

    # Operand A and B in SRAM
    a_data = np.array([1, 2, 3, 4, 5, 6, 7, 8], dtype=np.int32)
    b_data = np.array([10, 20, 30, 40, 50, 60, 70, 80], dtype=np.int32)
    a_addr = 0x1000
    b_addr = 0x2000
    out_addr = 0x3000

    sram[a_addr : a_addr + len(a_data.tobytes())] = a_data.tobytes()
    sram[b_addr : b_addr + len(b_data.tobytes())] = b_data.tobytes()
    sram[out_addr : out_addr + len(a_data.tobytes())] = (
        b"\x00" * len(a_data.tobytes())
    )

    # Configure Vector registers
    _write_reg(bridge, VECTOR, VECTOR.CTRL, 0)   # ADD
    _write_reg(bridge, VECTOR, VECTOR.A_ADDR, a_addr)
    _write_reg(bridge, VECTOR, VECTOR.B_ADDR, b_addr)
    _write_reg(bridge, VECTOR, VECTOR.O_ADDR, out_addr)
    _write_reg(bridge, VECTOR, VECTOR.DIM, 8)     # 8 elements

    # Trigger computation
    _write_reg(bridge, VECTOR, VECTOR.CMD, 1)     # START

    # Verify DONE status (stub does this correctly)
    status = _read_reg(bridge, VECTOR, VECTOR.STATUS)
    assert status == 2, f"Vector STATUS should be DONE(2), got {status}"

    # ❌ THIS ASSERTION WILL FAIL — stub never writes output
    output = np.frombuffer(
        sram[out_addr : out_addr + len(a_data.tobytes())], dtype=np.int32
    )
    assert np.any(output != 0), (
        "Vector output is all zeros — stub returned DONE without computing"
    )
