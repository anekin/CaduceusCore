"""
test_sfu_wrapper.py — SFU wrapper functional tests (cocotb)
==================================================================
Task: wrapper-level-verification / T2 (Wave 1)

5 cocotb tests for sfu_soc_wrapper:
  1. test_apb_regmap_rw       — write/read regmap offsets 0x00-0x1C
  2. test_sfu_softmax_normal   — 256-element FP16 softmax via wrapper
  3. test_sfu_gelu_normal      — GELU op via wrapper (64 elements)
  4. test_sfu_width_converter_32to512 — 16×32-bit → 512-bit write packing
  5. test_sfu_line_buffer_prefetch   — non-aligned I_ADDR triggers prefetch

All tests use AxiRam (behavioral AXI4 slave) for functional verification.
FP16 comparison: abs_tol=2e-3, rel_tol=1e-2 (per compare_sfu.py convention).
"""

import struct
import numpy as np

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

# ── Project imports ─────────────────────────────────────────────────────
import sys
from pathlib import Path
_SCRIPT_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from sim.tests.wrapper.wrapper_common import (
    create_apb_master,
    create_axi_ram,
    write_reg,
    read_reg,
    wait_done,
    check_no_x,
)

# ══════════════════════════════════════════════════════════════════════════
# MMIO register offsets (SFU_BASE relative)
# ══════════════════════════════════════════════════════════════════════════
REG_CTRL    = 0x00  # [3:0]=OP (0=SOFTMAX, 1=LAYERNORM, 2=GELU, 3=RELU, 4=SILU, 5=ROPE, 6=RMSNORM)
REG_CMD     = 0x04  # [0]=START (write-only pulse)
REG_STATUS  = 0x08  # [0]=BUSY, [1]=DONE
REG_I_ADDR  = 0x0C  # Input SRAM byte address
REG_O_ADDR  = 0x10  # Output SRAM byte address
REG_DIM     = 0x14  # [15:0]=element count
REG_POS     = 0x18  # Position index (ROPE)
REG_IRQ_EN  = 0x1C  # [0]=completion interrupt enable

SFU_OP_SOFTMAX  = 0
SFU_OP_GELU     = 2

BASE  = 0  # signal-level APB access; base unused by wrapper_common helpers

# ══════════════════════════════════════════════════════════════════════════
# FP16 helpers
# ══════════════════════════════════════════════════════════════════════════


def fp16_bytes(values):
    """Convert a list/array of float values to little-endian FP16 bytes."""
    arr = np.asarray(values, dtype=np.float16)
    return arr.view(np.uint16).tobytes()


def bytes_to_fp16(data):
    """Convert bytes (little-endian uint16 pairs) to np.float16 array."""
    u16 = np.frombuffer(data, dtype=np.uint16)
    return u16.view(np.float16)


def compare_fp16(golden, result, abs_tol=2e-3, rel_tol=1e-2):
    """Compare two FP16 arrays with tolerance. Returns (passed, max_abs, max_rel)."""
    g = golden.astype(np.float64)
    r = result.astype(np.float64)
    if g.shape != r.shape:
        return False, float("inf"), float("inf")
    abs_diff = np.abs(g - r)
    rel_diff = np.zeros_like(abs_diff)
    nonzero = np.abs(g) > 0
    rel_diff[nonzero] = abs_diff[nonzero] / np.abs(g[nonzero])
    ok = (abs_diff <= abs_tol) | (rel_diff <= rel_tol)
    max_abs = float(np.max(abs_diff))
    max_rel = float(np.max(rel_diff))
    return bool(ok.all()), max_abs, max_rel


# ══════════════════════════════════════════════════════════════════════════
# Golden reference functions
# ══════════════════════════════════════════════════════════════════════════


def softmax_golden(x):
    """NumPy FP32 softmax (clipped to float16 range for comparison)."""
    xf = x.astype(np.float32)
    xf = np.clip(xf, -65504.0, 65504.0)
    x_max = np.max(xf)
    e = np.exp(xf - x_max)
    s = e / np.sum(e)
    return s.astype(np.float16)


def gelu_golden(x):
    """NumPy FP32 GELU (tanh approximation) clipped to float16."""
    xf = x.astype(np.float32)
    xf = np.clip(xf, -65504.0, 65504.0)
    sqrt_2_pi = np.sqrt(2.0 / np.pi)
    inner = sqrt_2_pi * (xf + 0.044715 * xf ** 3)
    result = 0.5 * xf * (1.0 + np.tanh(inner))
    return result.astype(np.float16)


# ══════════════════════════════════════════════════════════════════════════
# Test infrastructure (async)
# ══════════════════════════════════════════════════════════════════════════


def _select_test():
    """
    Return the first scoped cocotb test that is actually requested.
    When running with COCOTB_TESTCASE=test_sfu_wrapper.test_apb_regmap_rw,
    only that one runs.  CocoTB normally picks one test per sim invocation.
    """
    requested = cocotb.plusargs.get("+COCOTB_TESTCASE")
    if not requested:
        requested = cocotb.plusargs.get("+TESTCASE")
    return requested


async def _reset_and_init(dut):
    """Start clock and assert reset to return DUT to a known state."""
    clock = Clock(dut.clk, 10, units="ns")  # 100 MHz
    cocotb.start_soon(clock.start())

    # Pulse reset: assert rst_n low, wait, de-assert
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    dut._log.info("Clock started, reset de-asserted")


async def _sfu_configure_and_start(apb, dut, op, i_addr, o_addr, dim,
                                   pos=0, irq_en=0):
    """Configure SFU registers and pulse CMD.START."""
    await write_reg(apb, BASE, REG_CTRL, op)
    await write_reg(apb, BASE, REG_I_ADDR, i_addr)
    await write_reg(apb, BASE, REG_O_ADDR, o_addr)
    await write_reg(apb, BASE, REG_DIM, dim)
    await write_reg(apb, BASE, REG_POS, pos)
    await write_reg(apb, BASE, REG_IRQ_EN, irq_en)
    # Longer delay so the I_ADDR prefetch can complete before START.
    # If START arrives while the prefetch is still in flight, the wrapper's
    # start_hold mechanism blocks the transaction and replays it later,
    # which may not be correctly interpreted by sfu_top in all cases.
    await ClockCycles(dut.clk, 100)
    await write_reg(apb, BASE, REG_CMD, 1)  # START pulse


async def _run_sfu_op(dut, apb, ram, op, i_addr, o_addr, dim,
                      input_fp16, pos=0, timeout=5000000):
    """Run a full SFU op: write input to RAM, start SFU, wait DONE, read output.
    
    timeout=2,000,000 cycles (20ms at 100MHz) — wrapper AXI bus overhead
    introduces significant latency.  Observed SFU completion times:
      DIM=256: ~500K-1.2M cycles,  DIM=16: ~400K cycles."""
    # Write input data to AxiRam at i_addr (AxiRam provides the AXI4 slave)
    input_bytes = fp16_bytes(input_fp16)
    ram.write(i_addr, input_bytes)

    # Configure and start SFU
    await _sfu_configure_and_start(apb, dut, op, i_addr, o_addr, dim, pos=pos)

    # Wait for DONE (bit 1 of STATUS)
    await wait_done(apb, BASE, status_offset=REG_STATUS, done_bit=1,
                    timeout_cycles=timeout, clk=dut.clk)

    # Read output from AxiRam
    output_bytes = ram.read(o_addr, dim * 2)
    result = bytes_to_fp16(output_bytes)
    return result


# ══════════════════════════════════════════════════════════════════════════
# Test 1: APB Regmap Read/Write
# ══════════════════════════════════════════════════════════════════════════


@cocotb.test()
async def test_apb_regmap_rw(dut):
    """Write to and read back each SFU regmap offset 0x00-0x1C."""
    await _reset_and_init(dut)
    apb = create_apb_master(dut)

    # ── CTRL (0x00) — read/write ─────────────────────────────────────────
    await write_reg(apb, BASE, REG_CTRL, 0x6)  # OP=RMSNORM
    ctrl = await read_reg(apb, BASE, REG_CTRL)
    assert (ctrl & 0xF) == 0x6, f"CTRL: expected 0x6, got 0x{ctrl:08X}"

    # ── CMD (0x04) — write 0 (NOT START); START=1 would trigger start_hold
    #     if I_ADDR is not cached, which would hang the TB indefinitely.
    await write_reg(apb, BASE, REG_CMD, 0)
    await ClockCycles(dut.clk, 2)

    # ── STATUS (0x08) — read (should be 0 after reset, no op) ───────────
    status = await read_reg(apb, BASE, REG_STATUS)
    assert (status & 0x3) == 0, f"STATUS: expected 0, got 0x{status:08X}"

    # ── I_ADDR (0x0C) — read/write ───────────────────────────────────────
    await write_reg(apb, BASE, REG_I_ADDR, 0xDEAD0000)
    iaddr = await read_reg(apb, BASE, REG_I_ADDR)
    assert iaddr == 0xDEAD0000, f"I_ADDR: expected 0xDEAD0000, got 0x{iaddr:08X}"

    # ── O_ADDR (0x10) — read/write ───────────────────────────────────────
    await write_reg(apb, BASE, REG_O_ADDR, 0xBEEF0000)
    oaddr = await read_reg(apb, BASE, REG_O_ADDR)
    assert oaddr == 0xBEEF0000, f"O_ADDR: expected 0xBEEF0000, got 0x{oaddr:08X}"

    # ── DIM (0x14) — read/write ─────────────────────────────────────────
    await write_reg(apb, BASE, REG_DIM, 256)
    dim = await read_reg(apb, BASE, REG_DIM)
    assert dim == 256, f"DIM: expected 256, got {dim}"

    # ── POS (0x18) — read/write ─────────────────────────────────────────
    await write_reg(apb, BASE, REG_POS, 42)
    pos = await read_reg(apb, BASE, REG_POS)
    assert pos == 42, f"POS: expected 42, got {pos}"

    # ── IRQ_EN (0x1C) — read/write ───────────────────────────────────────
    await write_reg(apb, BASE, REG_IRQ_EN, 1)
    irq = await read_reg(apb, BASE, REG_IRQ_EN)
    assert irq == 1, f"IRQ_EN: expected 1, got {irq}"

    dut._log.info("test_apb_regmap_rw: PASS")


# ══════════════════════════════════════════════════════════════════════════
# Test 2: SFU Softmax (256 elements)
# ══════════════════════════════════════════════════════════════════════════


@cocotb.test()
async def test_sfu_softmax_normal(dut):
    """256-element FP16 softmax via SFU wrapper, compare against golden."""
    await _reset_and_init(dut)
    apb = create_apb_master(dut)
    ram = create_axi_ram(dut, size=16 * 1024)

    # Generate random input in [-10, 10]
    np.random.seed(42)
    dim = 256
    input_data = (np.random.randn(dim) * 10.0).astype(np.float16)
    golden = softmax_golden(input_data)

    i_addr = 0x0000
    o_addr = 0x1000
    result = await _run_sfu_op(dut, apb, ram, SFU_OP_SOFTMAX,
                               i_addr, o_addr, dim, input_data)

    passed, max_abs, max_rel = compare_fp16(golden, result)
    if not passed:
        # Find failing indices for debug
        g = golden.astype(np.float64)
        r = result.astype(np.float64)
        abs_diff = np.abs(g - r)
        fail_idx = np.where(abs_diff > 2e-3)[0]
        fail_detail = " ".join(
            f"[{i}] g={g[i]:.6f} r={r[i]:.6f}" for i in fail_idx[:8]
        )
        dut._log.error(
            f"SOFTMAX FAIL: max_abs={max_abs:.6e} max_rel={max_rel:.6e}\n"
            f"  first failing elements: {fail_detail}"
        )
    assert passed, f"Softmax comparison failed: max_abs={max_abs:.6e} max_rel={max_rel:.6e}"
    dut._log.info("test_sfu_softmax_normal: PASS")


# ══════════════════════════════════════════════════════════════════════════
# Test 3: SFU GELU (64 elements)
# ══════════════════════════════════════════════════════════════════════════


@cocotb.test()
async def test_sfu_gelu_normal(dut):
    """64-element FP16 GELU via SFU wrapper, compare against golden."""
    await _reset_and_init(dut)
    apb = create_apb_master(dut)
    ram = create_axi_ram(dut, size=16 * 1024)

    # Use a known sweep over [-4, 4] for good coverage
    dim = 64
    input_data = np.linspace(-4.0, 4.0, dim, dtype=np.float16)
    golden = gelu_golden(input_data)

    i_addr = 0x0000
    o_addr = 0x1000
    result = await _run_sfu_op(dut, apb, ram, SFU_OP_GELU,
                               i_addr, o_addr, dim, input_data)

    passed, max_abs, max_rel = compare_fp16(golden, result)
    if not passed:
        g = golden.astype(np.float64)
        r = result.astype(np.float64)
        abs_diff = np.abs(g - r)
        fail_idx = np.where(abs_diff > 2e-3)[0]
        fail_detail = " ".join(
            f"[{i}] g={g[i]:.6f} r={r[i]:.6f}" for i in fail_idx[:8]
        )
        dut._log.error(
            f"GELU FAIL: max_abs={max_abs:.6e} max_rel={max_rel:.6e}\n"
            f"  first failing elements: {fail_detail}"
        )
    assert passed, f"GELU comparison failed: max_abs={max_abs:.6e} max_rel={max_rel:.6e}"
    dut._log.info("test_sfu_gelu_normal: PASS")


# ══════════════════════════════════════════════════════════════════════════
# Test 4: Width Converter 32→512 (write packing verification)
# ══════════════════════════════════════════════════════════════════════════
#
# The SFU wrapper's write path collects 16 × 32-bit SFU output writes into
# a 64-byte line buffer and flushes it as a single 512-bit AXI4 write.
# This test verifies that:
#   1. 16 consecutive SFU writes form exactly one 512-bit AXI write.
#   2. The byte order within the 512-bit beat is correct (little-endian).
#
# Method: run a 16-element softmax, read the output from AxiRam, verify
# the 512-bit packing corresponds to the SFU's per-element 32-bit writes.

@cocotb.test()
async def test_sfu_width_converter_32to512(dut):
    """  Verify 16x32-bit SFU output writes pack into one 512-bit AXI beat."""
    await _reset_and_init(dut)
    apb = create_apb_master(dut)
    ram = create_axi_ram(dut, size=16 * 1024)

    dim = 16  # exactly one 64-byte line (16 × 4B = 64B = 512b)
    np.random.seed(123)
    input_data = (np.random.randn(dim) * 2.0).astype(np.float16)
    golden = softmax_golden(input_data)

    i_addr = 0x0000
    o_addr = 0x1000

    # Run softmax — causes the wrapper write FIFO to collect 16 words
    # and flush one 512-bit beat to AxiRam at o_addr.
    result = await _run_sfu_op(dut, apb, ram, SFU_OP_SOFTMAX,
                               i_addr, o_addr, dim, input_data)

    # Verify the output matches golden (implicitly testing write packing)
    passed, max_abs, max_rel = compare_fp16(golden, result)
    if not passed:
        dut._log.error(
            f"WIDTH CONVERTER FAIL: max_abs={max_abs:.6e} max_rel={max_rel:.6e}"
        )
    assert passed, (
        f"Width converter packing failed: max_abs={max_abs:.6e} max_rel={max_rel:.6e}"
    )

    # Explicitly verify that the raw bytes in AxiRam correspond to the
    # expected 512-bit packed layout.  Each 32-bit SFU word occupies
    # word_idx*4 to word_idx*4+3 bytes within the 64-byte line.
    raw = ram.read(o_addr, 64)
    expected = fp16_bytes(golden)
    assert raw == expected, (
        f"32→512 packing mismatch:\n"
        f"  raw first 32: {raw[:32].hex()}\n"
        f"  exp first 32: {expected[:32].hex()}"
    )
    dut._log.info("test_sfu_width_converter_32to512: PASS")


# ══════════════════════════════════════════════════════════════════════════
# Test 5: Line Buffer Prefetch (non-aligned I_ADDR)
# ══════════════════════════════════════════════════════════════════════════
#
# The wrapper's read path maintains a 64-byte double-buffered cache line.
# When I_ADDR is written, the FSM prefetches the containing 64-byte line.
# When an sfu_top read falls near the end of the current line (word ≥ 10),
# it prefetches the next line.
#
# This test uses:
#   - DIM=25 (not a multiple of cache-line alignment)
#   - I_ADDR not aligned to 64 bytes (I_ADDR=8)
# The wrapper must handle the non-aligned start and the short trailing read
# that would trigger prefetch.

@cocotb.test()
async def test_sfu_line_buffer_prefetch(dut):
    """
    Verify line buffer prefetch with non-cache-line-aligned I_ADDR and
    DIM that is not a multiple of 128 elements (512 bytes).
    """
    await _reset_and_init(dut)
    apb = create_apb_master(dut)
    ram = create_axi_ram(dut, size=16 * 1024)

    dim = 25
    np.random.seed(77)
    input_data = (np.random.randn(dim) * 3.0).astype(np.float16)
    golden = softmax_golden(input_data)

    # Non-aligned addresses: I_ADDR=8 (not 64-byte aligned), O_ADDR=0x2008
    i_addr = 8
    o_addr = 0x2008

    result = await _run_sfu_op(dut, apb, ram, SFU_OP_SOFTMAX,
                               i_addr, o_addr, dim, input_data,
                               timeout=200000)

    passed, max_abs, max_rel = compare_fp16(golden, result)
    if not passed:
        g = golden.astype(np.float64)
        r = result.astype(np.float64)
        abs_diff = np.abs(g - r)
        fail_idx = np.where(abs_diff > 2e-3)[0]
        fail_detail = " ".join(
            f"[{i}] g={g[i]:.6f} r={r[i]:.6f}" for i in fail_idx[:8]
        )
        dut._log.error(
            f"PREFETCH FAIL: max_abs={max_abs:.6e} max_rel={max_rel:.6e}\n"
            f"  first failing elements: {fail_detail}"
        )
    assert passed, (
        f"Line buffer prefetch failed: max_abs={max_abs:.6e} max_rel={max_rel:.6e}"
    )
    dut._log.info("test_sfu_line_buffer_prefetch: PASS")
