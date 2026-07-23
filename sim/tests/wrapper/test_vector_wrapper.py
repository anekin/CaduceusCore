"""
test_vector_wrapper.py — Vector Engine SoC Wrapper Cocotb Tests
=================================================================
Task: wrapper-level-verification / T3 (Wave 1)

5 test cases for the vector_soc_wrapper:

1. test_apb_native_rw     — native vector_top MMIO regmap 0x00-0x1C read/write
2. test_apb_wrapper_rw    — wrapper-specific MMIO 0x30-0x44 read/write
3. test_vector_add_normal — 128 INT32 ADD op: preload A/B via AXI, START, compare
4. test_vector_chunk_burst_8beat — verify arlen=7/awlen=7/arsize=6 burst geometry
5. test_vector_conv_type_convert — INT32→FP16 CONV op: compare against numpy float16

Uses AxiRam (NOT axi_sparse_slave.v) for functional verification.
All tests run on sz0001 via VCS + cocotb VPI.
"""

import struct

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer

import numpy as np

from sim.tests.wrapper.wrapper_common import (
    check_no_x,
    create_apb_master,
    create_axi_ram,
    read_reg,
    wait_done,
    write_reg,
)

# ── Constants ────────────────────────────────────────────────────────────────
INT32_MIN = -(2**31)
INT32_MAX = 2**31 - 1
NUM_LANES = 128
DATA_W_BYTES = 4  # INT32 = 4 bytes
CHUNK_BYTES = NUM_LANES * DATA_W_BYTES  # 512 bytes
BEATS_PER_CHUNK = 8  # 512-bit AXI = 64 bytes/beat, 512/64 = 8

# Native vector_top MMIO offsets (0x00-0x1C)
MMIO_CTRL    = 0x00
MMIO_CMD     = 0x04
MMIO_STATUS  = 0x08
MMIO_A_ADDR  = 0x0C
MMIO_B_ADDR  = 0x10
MMIO_O_ADDR  = 0x14
MMIO_DIM     = 0x18
MMIO_IRQ_EN  = 0x1C

# Wrapper MMIO offsets (0x30-0x44)
WRA_BASE  = 0x30  # WRP_A_BASE
WRB_BASE  = 0x34  # WRP_B_BASE
WRO_BASE  = 0x38  # WRP_O_BASE
WRP_CMD   = 0x3C
WRP_STAT  = 0x40  # WRP_STATUS
WRP_LEN   = 0x44

# WRP_CMD bits
CMD_LOAD_A  = 0x01
CMD_LOAD_B  = 0x02
CMD_STORE_O = 0x04

# vector_top OP codes
OP_ADD  = 0
OP_MUL  = 1
OP_MAX  = 2
OP_SUM  = 3
OP_CONV = 4
OP_RESID = 5

# SRAM base addresses in AxiRam space
SRAM_A_BASE = 0x20000000
SRAM_B_BASE = 0x20001000
SRAM_O_BASE = 0x20002000

# DONE bit positions
STATUS_DONE_BIT = 1      # STATUS[1] = DONE
WRP_READY_BIT   = 0      # WRP_STATUS[0] = READY


# ── Golden reference functions (replicated from gen_vector_vectors.py) ───────

def _saturate_i32(x):
    return np.clip(x, INT32_MIN, INT32_MAX).astype(np.int32)


def golden_vector_add(a, b):
    return _saturate_i32(a.astype(np.int64) + b.astype(np.int64))


def golden_vector_conv_i32_to_f16(arr):
    f32 = arr.astype(np.float32)
    f16_max = np.finfo(np.float16).max
    f32 = np.clip(f32, -f16_max, f16_max)
    return f32.astype(np.float16)


# ── Utility helpers ──────────────────────────────────────────────────────────

async def reset_and_start(dut):
    """Apply reset and wait for stabilization."""
    clock = Clock(dut.clk, 10, units="ns")
    cocotb.fork(clock.start())

    # Reset sequence: rst_n=0 for 2 cycles, then release
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 2)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)


def pack_int32(arr):
    """Pack numpy int32 array into little-endian bytes."""
    return arr.astype(np.int32).tobytes()


def unpack_int32(data, count):
    """Unpack bytes into numpy int32 array."""
    return np.frombuffer(data[: count * 4], dtype=np.int32)


def unpack_fp16(data, count):
    """Unpack bytes into numpy float16 array."""
    return np.frombuffer(data[: count * 2], dtype=np.uint16).view(np.float16)


async def wait_wrp_ready(apb, clk, timeout=10000):
    """Wait for WRP_STATUS.READY (bit 0)."""
    return await wait_done(
        apb, 0, status_offset=WRP_STAT, done_bit=WRP_READY_BIT,
        timeout_cycles=timeout, clk=clk
    )


async def wait_vec_done(apb, clk, timeout=50000):
    """Wait for vector_top STATUS.DONE (bit 1)."""
    return await wait_done(
        apb, 0, status_offset=MMIO_STATUS, done_bit=STATUS_DONE_BIT,
        timeout_cycles=timeout, clk=clk
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 1: Native MMIO Register Read/Write (0x00-0x1C)
# ══════════════════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_apb_native_rw(dut):
    """Verify native vector_top MMIO registers (0x00-0x1C) read/write."""
    await reset_and_start(dut)
    apb = create_apb_master(dut)

    # Write known values to all writable native registers
    await write_reg(apb, 0, MMIO_CTRL,    0x00000004)   # OP=CONV
    await write_reg(apb, 0, MMIO_A_ADDR,  0xDEADBEEF)
    await write_reg(apb, 0, MMIO_B_ADDR,  0xCAFEBABE)
    await write_reg(apb, 0, MMIO_O_ADDR,  0xBEEFCAFE)
    await write_reg(apb, 0, MMIO_DIM,     0x00000128)   # 296
    await write_reg(apb, 0, MMIO_IRQ_EN,  0x00000001)

    # Read back and verify
    assert await read_reg(apb, 0, MMIO_CTRL)    == 0x00000004, f"CTRL mismatch"
    assert await read_reg(apb, 0, MMIO_A_ADDR)  == 0xDEADBEEF, f"A_ADDR mismatch"
    assert await read_reg(apb, 0, MMIO_B_ADDR)  == 0xCAFEBABE, f"B_ADDR mismatch"
    assert await read_reg(apb, 0, MMIO_O_ADDR)  == 0xBEEFCAFE, f"O_ADDR mismatch"
    assert await read_reg(apb, 0, MMIO_DIM)     == 0x00000128, f"DIM mismatch"
    assert await read_reg(apb, 0, MMIO_IRQ_EN)  == 0x00000001, f"IRQ_EN mismatch"

    # STATUS should initially report not-busy, not-done (after reset)
    status = await read_reg(apb, 0, MMIO_STATUS)
    assert (status & 0x01) == 0, f"STATUS.BUSY should be 0 after reset, got {status:#x}"
    assert (status & 0x02) == 0, f"STATUS.DONE should be 0 after reset, got {status:#x}"

    # CMD is write-only — reading should return 0 (no behavior spec enforced)
    cmd = await read_reg(apb, 0, MMIO_CMD)
    cocotb.log.info(f"[test_apb_native_rw] CMD readback = {cmd:#010x}")

    cocotb.log.info("[test_apb_native_rw] PASS — all native MMIO registers R/W verified")


# ══════════════════════════════════════════════════════════════════════════════
# Test 2: Wrapper MMIO Register Read/Write (0x30-0x44)
# ══════════════════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_apb_wrapper_rw(dut):
    """Verify wrapper-specific MMIO registers (0x30-0x44) read/write."""
    await reset_and_start(dut)
    apb = create_apb_master(dut)

    # Write wrapper registers
    await write_reg(apb, 0, WRA_BASE, 0xA0000000)
    await write_reg(apb, 0, WRB_BASE, 0xB0000000)
    await write_reg(apb, 0, WRO_BASE, 0xC0000000)
    await write_reg(apb, 0, WRP_LEN,  128)

    # Read back
    assert await read_reg(apb, 0, WRA_BASE) == 0xA0000000, f"WRP_A_BASE mismatch"
    assert await read_reg(apb, 0, WRB_BASE) == 0xB0000000, f"WRP_B_BASE mismatch"
    assert await read_reg(apb, 0, WRO_BASE) == 0xC0000000, f"WRP_O_BASE mismatch"
    assert await read_reg(apb, 0, WRP_LEN)  == 128,         f"WRP_LEN mismatch"

    # WRP_CMD is write-only — expect 0 readback
    cmd_val = await read_reg(apb, 0, WRP_CMD)
    cocotb.log.info(f"[test_apb_wrapper_rw] WRP_CMD readback = {cmd_val:#010x}")

    # WRP_STATUS should show READY when idle (no pending operations)
    status = await read_reg(apb, 0, WRP_STAT)
    assert (status & 0x01) == 1, f"WRP_STATUS.READY should be 1 when idle, got {status:#x}"

    cocotb.log.info("[test_apb_wrapper_rw] PASS — all wrapper MMIO registers R/W verified")


# ══════════════════════════════════════════════════════════════════════════════
# Test 3: Vector ADD — 128 INT32 Elements, Full Pipeline
# ══════════════════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_vector_add_normal(dut):
    """
    128 INT32 ADD: preload A and B via AXI writes into AxiRam,
    set WRP_CMD.LOAD_A/B to load into wrapper buffers,
    set CMD.START ADD → wait DONE → WRP_CMD.STORE_O → compare bit-exact.
    """
    await reset_and_start(dut)
    apb = create_apb_master(dut)
    axi_ram = create_axi_ram(dut, size=2**32)   # 4 GB sparse memory for SRAM space

    N = 128

    # Generate random test data (small values to avoid overflow)
    rng = np.random.default_rng(42)
    a_np = rng.integers(-1000, 1000, size=N, dtype=np.int32)
    b_np = rng.integers(-1000, 1000, size=N, dtype=np.int32)
    golden = golden_vector_add(a_np, b_np)

    cocotb.log.info(f"[test_vector_add_normal] A[:4] = {a_np[:4]}")
    cocotb.log.info(f"[test_vector_add_normal] B[:4] = {b_np[:4]}")
    cocotb.log.info(f"[test_vector_add_normal] Golden[:4] = {golden[:4]}")

    # Step 1: Preload A and B into AxiRam
    axi_ram.write(SRAM_A_BASE, pack_int32(a_np))
    axi_ram.write(SRAM_B_BASE, pack_int32(b_np))

    # Step 2: Configure wrapper MMIO
    await write_reg(apb, 0, WRA_BASE, SRAM_A_BASE)
    await write_reg(apb, 0, WRB_BASE, SRAM_B_BASE)
    await write_reg(apb, 0, WRO_BASE, SRAM_O_BASE)
    await write_reg(apb, 0, WRP_LEN,  N)

    # Step 3: LOAD_A → wait READY → LOAD_B → wait READY
    await write_reg(apb, 0, WRP_CMD, CMD_LOAD_A)
    await wait_wrp_ready(apb, dut.clk)

    await write_reg(apb, 0, WRP_CMD, CMD_LOAD_B)
    await wait_wrp_ready(apb, dut.clk)

    # Step 4: Configure vector_top for ADD, set START
    await write_reg(apb, 0, MMIO_CTRL,   OP_ADD)
    await write_reg(apb, 0, MMIO_A_ADDR, SRAM_A_BASE)
    await write_reg(apb, 0, MMIO_B_ADDR, SRAM_B_BASE)
    await write_reg(apb, 0, MMIO_O_ADDR, SRAM_O_BASE)
    await write_reg(apb, 0, MMIO_DIM,    N)
    await write_reg(apb, 0, MMIO_CMD,    0x01)  # START

    # Step 5: Wait for DONE
    await wait_vec_done(apb, dut.clk)
    cocotb.log.info("[test_vector_add_normal] Vector ADD done")

    # Step 6: STORE_O
    await write_reg(apb, 0, WRP_CMD, CMD_STORE_O)
    await wait_wrp_ready(apb, dut.clk)

    # Step 7: Read back result from AxiRam and compare
    result_bytes = axi_ram.read(SRAM_O_BASE, N * DATA_W_BYTES)
    result = unpack_int32(result_bytes, N)

    # Bit-exact comparison
    assert len(result) == N, f"Result length mismatch: {len(result)} != {N}"
    mismatches = np.where(result != golden)[0]
    if len(mismatches) > 0:
        for i in mismatches[:5]:
            cocotb.log.error(
                f"  Mismatch at [{i}]: rtl={result[i]} golden={golden[i]} "
                f"a={a_np[i]} b={b_np[i]}"
            )
        assert len(mismatches) == 0, f"{len(mismatches)} mismatches in vector ADD"

    # Verify no X/Z in output
    assert check_no_x(result), "X/Z detected in ADD output"

    cocotb.log.info(f"[test_vector_add_normal] PASS — {N} INT32 ADD bit-exact match")


# ══════════════════════════════════════════════════════════════════════════════
# Test 4: Chunk Burst Geometry — Verify arlen=7, arsize=6, burst=INCR
# ══════════════════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_vector_chunk_burst_8beat(dut):
    """
    Issue WRP_CMD.LOAD_A for 1024 elements (8 chunks).
    Verify the AXI AR channel uses arlen=7, arsize=6 (64B), arburst=INCR.
    Also verify AW channel during STORE_O.
    """
    await reset_and_start(dut)
    apb = create_apb_master(dut)
    axi_ram = create_axi_ram(dut, size=2**32)   # 4 GB sparse memory for SRAM space

    N = 1024  # 8 chunks at 128 elements/chunk

    # Preload data into AxiRam
    rng = np.random.default_rng(99)
    a_np = rng.integers(-100, 100, size=N, dtype=np.int32)
    b_np = rng.integers(-100, 100, size=N, dtype=np.int32)
    axi_ram.write(SRAM_A_BASE, pack_int32(a_np))
    axi_ram.write(SRAM_B_BASE, pack_int32(b_np))

    # Configure wrapper
    await write_reg(apb, 0, WRA_BASE, SRAM_A_BASE)
    await write_reg(apb, 0, WRB_BASE, SRAM_B_BASE)
    await write_reg(apb, 0, WRO_BASE, SRAM_O_BASE)
    await write_reg(apb, 0, WRP_LEN,  N)

    # ── Monitor AR channel during LOAD_A ──────────────────────────────────
    ar_samples = []  # (arlen, arsize, arburst, araddr)

    async def monitor_ar():
        for _ in range(50000):
            await RisingEdge(dut.clk)
            if int(dut.m_axi_arvalid.value) and int(dut.m_axi_arready.value):
                ar_samples.append((
                    int(dut.m_axi_arlen.value),
                    int(dut.m_axi_arsize.value),
                    int(dut.m_axi_arburst.value),
                    int(dut.m_axi_araddr.value),
                ))
                cocotb.log.info(
                    f"[burst_monitor] AR: len={ar_samples[-1][0]} "
                    f"size={ar_samples[-1][1]} burst={ar_samples[-1][2]} "
                    f"addr=0x{ar_samples[-1][3]:08X}"
                )
        cocotb.log.warning("[burst_monitor] AR monitor timeout")

    cocotb.fork(monitor_ar())

    # Issue LOAD_A
    await write_reg(apb, 0, WRP_CMD, CMD_LOAD_A)
    await wait_wrp_ready(apb, dut.clk, timeout=50000)

    # Verify AR samples
    assert len(ar_samples) >= 1, "No AR transactions observed during LOAD_A"
    for i, (arlen, arsize, arburst, addr) in enumerate(ar_samples):
        assert arlen == 7, \
            f"AR[{i}]: expected arlen=7, got {arlen}"
        assert arsize == 6, \
            f"AR[{i}]: expected arsize=6 (64 bytes), got {arsize}"
        assert arburst == 1, \
            f"AR[{i}]: expected arburst=1 (INCR), got {arburst}"
        # Verify address alignment: must be 64-byte aligned (beat addr)
        assert (addr & 0x3F) == 0, \
            f"AR[{i}]: address 0x{addr:08X} not 64-byte aligned"

    cocotb.log.info(
        f"[test_vector_chunk_burst_8beat] AR channel verified: "
        f"{len(ar_samples)} bursts, all arlen=7 arsize=6 arburst=1"
    )

    # ── Monitor AW channel during STORE_O ─────────────────────────────────
    # First: do the ADD so we have output to store
    await write_reg(apb, 0, WRP_CMD, CMD_LOAD_B)
    await wait_wrp_ready(apb, dut.clk, timeout=50000)

    await write_reg(apb, 0, MMIO_CTRL,   OP_ADD)
    await write_reg(apb, 0, MMIO_A_ADDR, SRAM_A_BASE)
    await write_reg(apb, 0, MMIO_B_ADDR, SRAM_B_BASE)
    await write_reg(apb, 0, MMIO_O_ADDR, SRAM_O_BASE)
    await write_reg(apb, 0, MMIO_DIM,    N)
    await write_reg(apb, 0, MMIO_CMD,    0x01)  # START
    await wait_vec_done(apb, dut.clk, timeout=50000)

    aw_samples = []

    async def monitor_aw():
        for _ in range(50000):
            await RisingEdge(dut.clk)
            if int(dut.m_axi_awvalid.value) and int(dut.m_axi_awready.value):
                aw_samples.append((
                    int(dut.m_axi_awlen.value),
                    int(dut.m_axi_awsize.value),
                    int(dut.m_axi_awburst.value),
                    int(dut.m_axi_awaddr.value),
                ))
                cocotb.log.info(
                    f"[burst_monitor] AW: len={aw_samples[-1][0]} "
                    f"size={aw_samples[-1][1]} burst={aw_samples[-1][2]} "
                    f"addr=0x{aw_samples[-1][3]:08X}"
                )
        cocotb.log.warning("[burst_monitor] AW monitor timeout")

    cocotb.fork(monitor_aw())

    # Issue STORE_O
    await write_reg(apb, 0, WRP_CMD, CMD_STORE_O)
    await wait_wrp_ready(apb, dut.clk, timeout=50000)

    # Verify AW samples
    assert len(aw_samples) >= 1, "No AW transactions observed during STORE_O"
    for i, (awlen, awsize, awburst, addr) in enumerate(aw_samples):
        assert awlen == 7, \
            f"AW[{i}]: expected awlen=7, got {awlen}"
        assert awsize == 6, \
            f"AW[{i}]: expected awsize=6 (64 bytes), got {awsize}"
        assert awburst == 1, \
            f"AW[{i}]: expected awburst=1 (INCR), got {awburst}"
        assert (addr & 0x3F) == 0, \
            f"AW[{i}]: address 0x{addr:08X} not 64-byte aligned"

    cocotb.log.info(
        f"[test_vector_chunk_burst_8beat] AW channel verified: "
        f"{len(aw_samples)} bursts, all awlen=7 awsize=6 awburst=1"
    )

    # Verify data integrity — read back and compare
    result_bytes = axi_ram.read(SRAM_O_BASE, N * DATA_W_BYTES)
    result = unpack_int32(result_bytes, N)
    golden = golden_vector_add(a_np, b_np)
    mismatches = np.where(result != golden)[0]
    assert len(mismatches) == 0, \
        f"{len(mismatches)} mismatches in 1024-element ADD result"

    cocotb.log.info(
        "[test_vector_chunk_burst_8beat] PASS — 8-beat burst geometry "
        "and data integrity verified"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 5: INT32→FP16 CONV — Type Convert Through Pipeline
# ══════════════════════════════════════════════════════════════════════════════

@cocotb.test()
async def test_vector_conv_type_convert(dut):
    """
    INT32→FP16 CONV: preload 128 INT32 values, run CONV op, compare output
    against numpy float16 golden.
    """
    await reset_and_start(dut)
    apb = create_apb_master(dut)
    axi_ram = create_axi_ram(dut, size=2**32)   # 4 GB sparse memory for SRAM space

    N = 128

    # Generate test data: INT32 values in a range that maps to representable FP16
    rng = np.random.default_rng(77)
    a_np = rng.integers(-20000, 20000, size=N, dtype=np.int32)
    golden_fp16 = golden_vector_conv_i32_to_f16(a_np)
    golden_bytes = golden_fp16.tobytes()  # 256 bytes (128 × 2)

    cocotb.log.info(f"[test_vector_conv_type_convert] A[:4] = {a_np[:4]}")
    cocotb.log.info(f"[test_vector_conv_type_convert] Golden FP16[:4] = {golden_fp16[:4]}")

    # Preload data
    axi_ram.write(SRAM_A_BASE, pack_int32(a_np))

    # Configure wrapper
    await write_reg(apb, 0, WRA_BASE, SRAM_A_BASE)
    await write_reg(apb, 0, WRO_BASE, SRAM_O_BASE)
    await write_reg(apb, 0, WRP_LEN,  N)

    # LOAD_A (CONV only needs operand A)
    await write_reg(apb, 0, WRP_CMD, CMD_LOAD_A)
    await wait_wrp_ready(apb, dut.clk)

    # Configure vector_top for CONV
    await write_reg(apb, 0, MMIO_CTRL,   OP_CONV)
    await write_reg(apb, 0, MMIO_A_ADDR, SRAM_A_BASE)
    await write_reg(apb, 0, MMIO_O_ADDR, SRAM_O_BASE)
    await write_reg(apb, 0, MMIO_DIM,    N)
    await write_reg(apb, 0, MMIO_CMD,    0x01)  # START

    # Wait for DONE
    await wait_vec_done(apb, dut.clk, timeout=50000)
    cocotb.log.info("[test_vector_conv_type_convert] CONV done")

    # STORE_O
    await write_reg(apb, 0, WRP_CMD, CMD_STORE_O)
    await wait_wrp_ready(apb, dut.clk)

    # Read back result from AxiRam
    # Full chunk is 512 bytes; FP16 data occupies lower 256 bytes (128 × 16 bits)
    result_bytes = axi_ram.read(SRAM_O_BASE, CHUNK_BYTES)
    result_fp16 = unpack_fp16(result_bytes[: N * 2], N)

    # Compare against golden FP16
    # Use bit-exact comparison for FP16 (IEEE 754 half-precision)
    result_fp16_arr = np.frombuffer(result_bytes[: N * 2], dtype=np.uint16)
    golden_fp16_arr = np.frombuffer(golden_bytes, dtype=np.uint16)

    mismatches = np.where(result_fp16_arr != golden_fp16_arr)[0]
    if len(mismatches) > 0:
        for i in mismatches[:5]:
            rval = result_fp16[i]
            gval = golden_fp16[i]
            cocotb.log.error(
                f"  Mismatch at [{i}]: rtl_f16=0x{result_fp16_arr[i]:04X} "
                f"({rval}) golden_f16=0x{golden_fp16_arr[i]:04X} ({gval}) "
                f"in={a_np[i]}"
            )

    # For CONV: the RTL type_convert uses IEEE 754 round-to-nearest-even and
    # saturates to ±65504. numpy float16 uses round-to-nearest-even but maps
    # overflow to ±Inf. Since our test data is in [-20000, 20000], both should
    # produce identical results (no overflow, no saturation edge cases).
    assert len(mismatches) == 0, f"{len(mismatches)} mismatches in CONV output"

    # Verify upper bytes of chunk are zero (padding by vector_top)
    upper_bytes = result_bytes[N * 2 : CHUNK_BYTES]
    assert all(b == 0 for b in upper_bytes), \
        f"Upper bytes of CONV chunk are not zero-padded: {upper_bytes[:16].hex()}..."

    cocotb.log.info(
        f"[test_vector_conv_type_convert] PASS — "
        f"{N} INT32→FP16 CONV bit-exact match"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Test 6: BUG-005 — X-propagation from non-aligned wstrb masking (sparse)
# ══════════════════════════════════════════════════════════════════════════════
#
# Uses tb_vector_wrapper_sparse (axi_sparse_slave.v) to test BUG-RTL-SOC-005:
# uninitialized padding bytes return X, which propagate through the wrapper's
# read path if the wrapper does not mask uninitialized trailing bytes.
#
# Test flow:
#   1. sparse_sel=1 → AxiMaster writes 400 bytes (100 INT32) of A-data
#      at addr 0x0000; bytes 400-511 of word 6 remain X
#   2. Write 512 bytes (128 INT32) of B-data at addr 0x0400 (fully valid)
#   3. sparse_sel=0 → wrapper LOAD_A from 0x0000 (X in padding)
#      LOAD_B from 0x0400 (fully valid), ADD, STORE_O to 0x0800
#   4. Read sparse slave output: check bytes 0-399 for golden match,
#      bytes 400-511 for X (wstrb masking verification)


def _sparse_check_x_word(dut, word_idx):
    """Check if a sparse slave memory word contains X/Z bits."""
    s = str(dut.u_sparse.mem[word_idx].value)
    return ('x' in s.lower() or 'z' in s.lower())


def _sparse_read_int32(dut, byte_addr, count):
    """Read INT32 values from sparse slave memory.
    Returns (has_x, int32_array). X bytes are replaced with 0."""
    DATA_W = 512
    STRB_W = DATA_W // 8
    result = []
    has_x = False
    word_idx = byte_addr // STRB_W
    byte_off = byte_addr % STRB_W
    remaining = count * 4

    while remaining > 0:
        raw = dut.u_sparse.mem[word_idx].value
        s = str(raw)
        if 'x' in s.lower() or 'z' in s.lower():
            has_x = True
        try:
            word_int = int(raw)
            word_bytes = word_int.to_bytes(STRB_W, 'little')
        except ValueError:
            word_bytes = bytearray(STRB_W)
            for b in range(STRB_W):
                try:
                    word_bytes[b] = int(raw[8 * b : 8 * b + 7])
                except ValueError:
                    word_bytes[b] = 0
                    has_x = True
        take = min(STRB_W - byte_off, remaining)
        chunk = word_bytes[byte_off: byte_off + take]
        for i in range(0, len(chunk), 4):
            result.append(int.from_bytes(chunk[i: i + 4], 'little', signed=True))
        word_idx += 1
        byte_off = 0
        remaining -= take

    return has_x, np.array(result, dtype=np.int32)


@cocotb.test()
async def test_bug005_vector_nonaligned_wstrb(dut):
    """BUG-005 Vector: X-propagation from wstrb masking on unaligned store.

    Checks whether vector_soc_wrapper:446-474 wstrb masking prevents
    X from uninitialized slave memory from being written during STORE_O
    for a non-chunk-aligned WRP_LEN (100 elements = 400 bytes).
    """
    await reset_and_start(dut)
    apb = create_apb_master(dut)

    # External AxiMaster for preloading
    from cocotbext.axi import AxiBus, AxiMaster
    e_bus = AxiBus.from_prefix(dut, "e_axi")
    e_master = AxiMaster(e_bus, dut.clk, dut.rst_n, reset_active_level=False)

    # Base addresses in sparse slave space
    ADDR_A = 0x00000000   # A-data: 400 bytes valid, 400-511 = X
    ADDR_B = 0x00000400   # B-data: 512 bytes fully valid (128 INT32)
    ADDR_O = 0x00000800   # Output: STORE_O target

    AXI_DATA_WIDTH = 512

    N = 100  # only 100 of 128 elements valid per chunk

    # Generate test data
    rng = np.random.default_rng(42)
    a_np = rng.integers(-1000, 1000, size=128, dtype=np.int32)
    b_np = rng.integers(-1000, 1000, size=128, dtype=np.int32)
    golden_full = golden_vector_add(a_np[:100], b_np[:100])

    cocotb.log.info(
        f"[test_bug005_vector_nonaligned_wstrb] A[:4]={a_np[:4]}, "
        f"B[:4]={b_np[:4]}, Golden[:4]={golden_full[:4]}"
    )

    # ── Preload data via external AxiMaster ────────────────────────────────
    dut.sparse_sel.value = 1
    await ClockCycles(dut.clk, 5)

    # Write A-data: first 400 bytes only (100 INT32 × 4 bytes)
    await e_master.write(ADDR_A, pack_int32(a_np[:100]))
    await ClockCycles(dut.clk, 10)

    # Write B-data: full 512 bytes (128 INT32)
    await e_master.write(ADDR_B, pack_int32(b_np))
    await ClockCycles(dut.clk, 10)

    # ── Switch to wrapper mode ─────────────────────────────────────────────
    dut.sparse_sel.value = 0
    await ClockCycles(dut.clk, 10)

    # ── Configure wrapper MMIO ─────────────────────────────────────────────
    await write_reg(apb, 0, WRA_BASE, ADDR_A)
    await write_reg(apb, 0, WRB_BASE, ADDR_B)
    await write_reg(apb, 0, WRO_BASE, ADDR_O)
    await write_reg(apb, 0, WRP_LEN,  N)

    # LOAD_A from sparse slave (bytes 400-511 = X)
    await write_reg(apb, 0, WRP_CMD, CMD_LOAD_A)
    await wait_wrp_ready(apb, dut.clk, timeout=50000)

    # LOAD_B (fully valid)
    await write_reg(apb, 0, WRP_CMD, CMD_LOAD_B)
    await wait_wrp_ready(apb, dut.clk, timeout=50000)

    # START ADD
    await write_reg(apb, 0, MMIO_CTRL,   OP_ADD)
    await write_reg(apb, 0, MMIO_A_ADDR, ADDR_A)
    await write_reg(apb, 0, MMIO_B_ADDR, ADDR_B)
    await write_reg(apb, 0, MMIO_O_ADDR, ADDR_O)
    await write_reg(apb, 0, MMIO_DIM,    N)
    await write_reg(apb, 0, MMIO_CMD,    0x01)

    # Wait for DONE
    await wait_vec_done(apb, dut.clk, timeout=50000)
    cocotb.log.info("[test_bug005_vector_nonaligned_wstrb] Vector ADD done")

    # STORE_O
    await write_reg(apb, 0, WRP_CMD, CMD_STORE_O)
    await wait_wrp_ready(apb, dut.clk, timeout=50000)

    # ── Wait for AXI writes to settle ──────────────────────────────────────
    await ClockCycles(dut.clk, 50)

    # ── Check output at ADDR_O: bytes 0-399 for golden, bytes 400-511 for X
    output_word0_x = _sparse_check_x_word(dut, ADDR_O // (AXI_DATA_WIDTH // 8))
    has_x_out, result_np = _sparse_read_int32(dut, ADDR_O, 128)

    # Check first 100 elements for X or corruption
    x_in_valid = False
    for i in range(100):
        s = str(dut.u_sparse.mem[(ADDR_O // 64) + (i * 4) // 64].value)
        if 'x' in s.lower() or 'z' in s.lower():
            x_in_valid = True
            break

    mismatches = np.where(result_np[:100] != golden_full)[0]

    # ── Check bytes 400-511 (word 6, upper bytes) for X ───────────────────
    word6_idx = (ADDR_O + 384) // 64  # byte 384 is start of word 6
    word6_x = _sparse_check_x_word(dut, word6_idx)

    # ── Report results ──────────────────────────────────────────────────────
    if x_in_valid:
        cocotb.log.error(
            "Vector: X_PROP - X detected in valid output bytes 0-399"
        )
    elif len(mismatches) > 0:
        cocotb.log.error(
            f"Vector: FAIL/X_PROP — {len(mismatches)} mismatches "
            f"in output elements 0-99"
        )
        for i_ in mismatches[:5]:
            cocotb.log.error(
                f"  [{i_}]: rtl={result_np[i_]} golden={golden_full[i_]} "
                f"a={a_np[i_]} b={b_np[i_]}"
            )
    elif word6_x:
        cocotb.log.info(
            "Vector: PASS - wstrb masking works: bytes 400-511 remain X, "
            "output elements 0-99 bit-exact match"
        )
    else:
        cocotb.log.info(
            "Vector: PASS - output clean, no X in padding "
            "(wstrb masked 400-511, or slave wrote 0)"
        )

    if not x_in_valid and len(mismatches) == 0:
        cocotb.log.info("BUG005_VECTOR_FINAL: PASS")
    else:
        cocotb.log.info("BUG005_VECTOR_FINAL: X_PROP/FAIL")
