"""
wrapper_common.py — Shared helpers for wrapper-level cocotb verification
=========================================================================
Task: wrapper-level-verification / T1 scaffolding

Provides factory functions and utilities for wrapper testbenches that use
cocotbext-axi ApbMaster (for APB register access) and AxiRam (for AXI4
functional verification).

Key functions:
  create_apb_master(dut) -> ApbMaster
  create_axi_ram(dut, size=2**20) -> AxiRam
  write_reg(apb, base, offset, value) -> awaitable
  read_reg(apb, base, offset) -> awaitable int
  wait_done(apb, base, timeout_cycles=100000) -> awaitable
  gen_nonaligned_data(n_elements) -> bytes
  check_no_x(data) -> bool

All APB/AXI master/ram instances use reset_active_level=False because
wrapper testbenches use rst_n (active-low reset).
"""

# cocotb may not be importable outside simulation; only used inside tests.
# Use try/except for documentation and AST-validity outside cocotb.
try:
    import cocotb                     # noqa: F401
    from cocotb.clock import Clock     # noqa: F401
    from cocotb.triggers import ClockCycles, RisingEdge  # noqa: F401
    from cocotb.binary import BinaryValue  # noqa: F401
    COCOTB_AVAILABLE = True
except ImportError:
    COCOTB_AVAILABLE = False

try:
    from cocotbext.axi import AxiBus, AxiRam      # noqa: F401
    from cocotbext.axi import ApbBus, ApbMaster   # noqa: F401
    COCOTBEXT_AVAILABLE = True
except ImportError:
    COCOTBEXT_AVAILABLE = False

from typing import Optional


def create_apb_master(dut):
    """
    Create an ApbMaster attached to the DUT's ``apb_*`` prefixed ports.

    Args:
        dut: cocotb DUT handle (tb_sfu_wrapper, etc.)

    Returns:
        cocotbext.axi.ApbMaster instance with reset_active_level=False

    Raises:
        RuntimeError: if cocotbext-axi is not available
    """
    if not COCOTBEXT_AVAILABLE:
        raise RuntimeError("cocotbext-axi not available")
    bus = ApbBus.from_prefix(dut, "apb")
    return ApbMaster(bus, dut.clk, dut.rst_n, reset_active_level=False)


def create_axi_ram(dut, size=2**20):
    """
    Create an AxiRam (behavioral AXI4 slave with sparse memory) attached
    to the DUT's ``m_axi_*`` prefixed ports.

    Args:
        dut: cocotb DUT handle
        size: memory size in bytes (default 2**20 = 1 MB)

    Returns:
        cocotbext.axi.AxiRam instance with reset_active_level=False

    Raises:
        RuntimeError: if cocotbext-axi is not available
    """
    if not COCOTBEXT_AVAILABLE:
        raise RuntimeError("cocotbext-axi not available")
    bus = AxiBus.from_prefix(dut, "m_axi")
    return AxiRam(bus, dut.clk, dut.rst_n, reset_active_level=False, size=size)


async def write_reg(apb, base, offset, value):
    """
    Write a 32-bit value to an APB register.

    Args:
        apb: ApbMaster instance
        base: base address of the APB target (unused for direct signal access)
        offset: byte offset within the 4KB APB window (0x00..0xFFC)
        value: 32-bit word to write

    Note: wrapper TBs connect APB directly to the wrapper, so the address
    is just the offset (paddr[11:0]). ``base`` is kept for API compatibility
    but unused.
    """
    await apb.write(offset, value.to_bytes(4, "little"))


async def read_reg(apb, base, offset):
    """
    Read a 32-bit value from an APB register.

    Args:
        apb: ApbMaster instance
        base: base address (unused — see write_reg)
        offset: byte offset within the APB window

    Returns:
        int: 32-bit register value
    """
    data = await apb.read(offset, 4)
    return int.from_bytes(data, "little")


async def wait_done(
    apb, base, status_offset=0x08, done_bit=1, timeout_cycles=100000,
    clk=None
):
    """
    Poll STATUS.DONE until it is set or timeout expires.

    For SFU/VECTOR wrappers: done_bit=1 (bit 1 of STATUS).
    For MXU wrapper: the DONE bit is in STATUS[0] (done_bit=0).
    For wrapper-level preload checks: poll WRP_STATUS[0] (done_bit=0).

    Args:
        apb: ApbMaster instance
        base: unused (signal-level access)
        status_offset: APB byte offset of the STATUS register (default 0x08)
        done_bit: bit position to check (default 1 = STATUS.DONE)
        timeout_cycles: maximum cycles to wait (default 100000)
        clk: optional clock signal for ClockCycles; fallback to apb._bus.clk

    Raises:
        TimeoutError: if DONE is not asserted within timeout_cycles
    """
    import cocotb
    from cocotb.triggers import ClockCycles

    _clk = clk if clk is not None else apb._bus.clk
    for _ in range(timeout_cycles):
        data = await read_reg(apb, 0, status_offset)
        if data & (1 << done_bit):
            return data
        await ClockCycles(_clk, 1)
    raise TimeoutError(
        f"wait_done: timeout after {timeout_cycles} cycles "
        f"(status_offset=0x{status_offset:02X}, done_bit={done_bit})"
    )


def gen_nonaligned_data(n_elements, elem_bytes=2):
    """
    Generate a byte string of ``n_elements`` elements (FP16 by default),
    where n_elements is deliberately not a multiple of 128 (512-byte
    alignment). Used to test non-aligned burst padding and X-propagation.

    Args:
        n_elements: number of elements
        elem_bytes: bytes per element (default 2 for FP16)

    Returns:
        bytes of length n_elements * elem_bytes
    """
    import struct
    data = bytearray()
    for i in range(n_elements):
        val = (i * 7 + 1) % 65536  # pseudo-random pattern
        data.extend(struct.pack("<H", val))
    return bytes(data)


def check_no_x(data):
    """
    Check that a cocotb BinaryValue or bytes object contains no X or Z bits.

    For BinaryValue: checks that .binstr contains no 'x' or 'z'.
    For bytes: always True (bytes cannot represent X).
    For int: always True.

    Args:
        data: BinaryValue, bytes, or int

    Returns:
        bool: True if no X/Z found
    """
    if data is None:
        return True
    if isinstance(data, int):
        return True
    if isinstance(data, bytes):
        return True
    # cocotb BinaryValue
    try:
        s = data.binstr.lower()
        return ('x' not in s) and ('z' not in s)
    except (AttributeError, TypeError):
        # fallback: try string conversion
        s = str(data).lower()
        return ('x' not in s) and ('z' not in s)
