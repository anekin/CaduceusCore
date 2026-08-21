"""Smoke test for the tb_soc_ibex bulk DRAM backdoor ($readmemh port).

Verifies against the REAL compiled simv:
  1. first preload writes all non-zero words (sampled readback matches)
  2. delta preload rewrites dirty-to-zero words (readback is zero)
  3. skipped all-zero words stay zero (tb zero-init)
Run with the same binary as the segment run:
  MODULE=test_dram_bulk TOPLEVEL=tb_soc_ibex
"""
import time

import cocotb

from cocotb_bridge import CocotbBridge, DRAM_BASE


@cocotb.test()
async def test_bulk_and_word(dut):
    bridge = CocotbBridge(dut)
    await bridge.start_clock()
    await bridge.reset(5)

    n_words = 8 * 1024 * 1024 // 64
    img = bytearray(8 * 1024 * 1024)
    for w in range(n_words):
        if w == 130000:
            continue  # leave zero: exercises zero-skip + tb zero-init
        img[w * 64:w * 64 + 4] = ((w + 0x5A5A0000) & 0xFFFFFFFF).to_bytes(
            4, "little")
    img_bytes = bytes(img)

    t0 = time.time()
    await bridge.segment_preload(img_bytes)
    t1 = time.time()
    dut._log.info(f"[BULK-TEST] first preload {t1 - t0:.2f}s")

    bad = 0
    for w in range(0, n_words, 4097):
        got = await bridge.segment_read_dram(DRAM_BASE + w * 64, 64)
        expect = img_bytes[w * 64:(w + 1) * 64]
        if got != expect:
            bad += 1
    dut._log.info(f"[BULK-TEST] sampled readback bad={bad}")

    img2 = bytearray(img_bytes)
    img2[100 * 64:100 * 64 + 4] = b"\x00" * 4
    t0 = time.time()
    await bridge.segment_preload(bytes(img2))
    t1 = time.time()
    dut._log.info(f"[BULK-TEST] delta preload {t1 - t0:.2f}s")

    got = await bridge.segment_read_dram(DRAM_BASE + 100 * 64, 64)
    ok_delta = got == bytes(img2[100 * 64:101 * 64])
    dut._log.info(f"[BULK-TEST] dirty-to-zero ok={ok_delta}")

    got = await bridge.segment_read_dram(DRAM_BASE + 130000 * 64, 64)
    ok_zero = got == bytes(64)
    dut._log.info(f"[BULK-TEST] skipped-zero-stays-zero ok={ok_zero}")

    assert bad == 0, f"{bad} sampled words mismatch after bulk preload"
    assert ok_delta, "delta dirty-to-zero word was not written"
    assert ok_zero, "skipped all-zero word is not zero in RTL DRAM"
    dut._log.info("[BULK-TEST] ALL OK")
