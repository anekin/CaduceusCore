#!/usr/bin/env python3
"""
diagnose_data_layout.py — Phase 8 Data-Layout Hypothesis Diagnostic
====================================================================
Fail-first test: prove raw row-major activation → MXU FAILS,
tile-major packed activation → MXU PASSES.

Uses mxu_soc_wrapper preload path (NOT firmware doorbell) to isolate
the activation layout as the only variable. Runs both variants in one
simulation against the same weight matrix.

Usage:
  MODULE=sim.diagnose_data_layout TESTCASE=test_diag_both \
      simv_soc_ibex +COCOTB +BOOTROM_HEX=... -l diag_both.log
"""

import os, sys, struct

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "sim"))

import numpy as np

try:
    import cocotb
    from cocotb.triggers import ClockCycles
    COCOTB_AVAILABLE = True
except ImportError:
    COCOTB_AVAILABLE = False

from cocotb_bridge import (
    CocotbBridge,
    SRAM_BASE,
    pack_int8_activation_tile_major,
    pack_int4_tile_major,
)

# ── Constants ──────────────────────────────────────────────────────────
MXU_BASE = 0x40000000
# MXU MMIO offsets (mxu_top mmio_if)
OFF_CTRL       = 0x00   # CTRL
OFF_CMD        = 0x04   # CMD (write 1 = START)
OFF_STATUS     = 0x08   # STATUS (bit 0 = DONE)
OFF_DIM_MK     = 0x0C   # DIM0: [31:16]=K, [15:0]=M
OFF_DIM_N      = 0x10   # DIM1: N
OFF_SRC_ACT    = 0x14   # Activation SRAM addr
OFF_SRC_WGT    = 0x18   # Weight SRAM addr
OFF_DST_OUT    = 0x1C   # Output SRAM addr
OFF_SRC_SCALE  = 0x20   # Scale SRAM addr

# Wrapper MMIO offsets (mxu_soc_wrapper)
OFF_WRP_WBASE  = 0x30
OFF_WRP_ABASE  = 0x34
OFF_WRP_OBASE  = 0x38
OFF_WRP_CMD    = 0x3C
OFF_WRP_STATUS = 0x40
OFF_WRP_KTILES = 0x44
OFF_WRP_DIMN   = 0x48

# SRAM scratch areas
ACT_SRAM  = SRAM_BASE + 0x050000
WGT_SRAM  = SRAM_BASE + 0x051000
OUT_SRAM  = SRAM_BASE + 0x052000
SCALE_SRAM = SRAM_BASE + 0x053000

TILE = 64


def _pack_w(wgt: np.ndarray) -> np.ndarray:
    """Pack INT8→INT4 weights: 2 per byte, low nibble first."""
    K, N = wgt.shape
    p = np.zeros(K * N // 2, dtype=np.uint8)
    for k in range(K):
        for n in range(0, N, 2):
            lo = int(wgt[k, n]) & 0xF
            hi = int(wgt[k, min(n + 1, N - 1)]) & 0xF
            p[(k * N + n) // 2] = (hi << 4) | lo
    return p


def _gen(M, K, N, seed=42):
    """Generate golden reference data."""
    rng = np.random.RandomState(seed)
    w = rng.randint(-8, 8, size=(K, N), dtype=np.int8)
    a = rng.randint(-128, 128, size=(M, K), dtype=np.int8)
    g = np.zeros((M, N), dtype=np.int32)
    for m in range(M):
        for n in range(N):
            s = 0
            for k in range(K):
                s += int(a[m, k]) * int(w[k, n])
            g[m, n] = s
    return {"act": a, "wgt": w, "golden": g}


def _make_scales(K, N, value=np.float16(1.0)):
    """Generate per-block scale data."""
    k_blocks = (K + TILE - 1) // TILE
    n_tiles = (N + TILE - 1) // TILE
    fp16 = struct.pack("<e", value)
    padded = fp16 + b'\x00\x00'
    tile_data = padded * TILE
    return tile_data * k_blocks * n_tiles


async def _run_one_variant(bridge, tag, M, K, N, act, wgt, golden, use_tile_major):
    """Run a single MMUL through the mxu_soc_wrapper preload path.

    Writes data to SRAM, configures wrapper preload, starts MXU,
    polls for completion, reads back results.
    """
    wp = _pack_w(wgt)
    scales = _make_scales(K, N)

    # Pack activations
    if use_tile_major:
        act_bytes = pack_int8_activation_tile_major(act.tobytes(), M, K)
    else:
        act_bytes = act.tobytes()

    # Pack weights (tile-major for weights always — for K=64,N=64 they're identical to raw)
    wgt_bytes = pack_int4_tile_major(wp.tobytes(), K, N)

    # Write data to SRAM
    await bridge._sram_backdoor_write(ACT_SRAM, act_bytes)
    await bridge._sram_backdoor_write(WGT_SRAM, wgt_bytes)
    await bridge._sram_backdoor_write(SCALE_SRAM, scales)

    # Configure wrapper preload
    k_tiles = max(1, (K + 63) // 64)
    await bridge._apb_write(MXU_BASE + OFF_WRP_WBASE, WGT_SRAM)
    await bridge._apb_write(MXU_BASE + OFF_WRP_ABASE, ACT_SRAM)
    await bridge._apb_write(MXU_BASE + OFF_WRP_OBASE, OUT_SRAM)
    await bridge._apb_write(MXU_BASE + OFF_WRP_KTILES, k_tiles)
    await bridge._apb_write(MXU_BASE + OFF_WRP_DIMN, N)

    # Trigger preload
    await bridge._apb_write(MXU_BASE + OFF_WRP_CMD, 0x0000_0001)

    # Poll WRP_STATUS until preload complete
    for _ in range(10000):
        s = await bridge._apb_read(MXU_BASE + OFF_WRP_STATUS)
        if s & 0x1:
            break
        await ClockCycles(bridge.dut.clk, 1)
    else:
        raise TimeoutError(f"{tag}: WRP_STATUS timeout")

    # Configure MXU MMIO
    engine_n = ((N + 63) // 64) * 64  # padded N for the controller
    await bridge._apb_write(MXU_BASE + OFF_CTRL, 0x00000000)       # CTRL: INT4xINT8, no accumulate
    await bridge._apb_write(MXU_BASE + OFF_DIM_MK, (K << 16) | M)  # DIM0
    await bridge._apb_write(MXU_BASE + OFF_DIM_N, engine_n)        # DIM1
    await bridge._apb_write(MXU_BASE + OFF_SRC_ACT, ACT_SRAM)      # activation SRAM addr
    await bridge._apb_write(MXU_BASE + OFF_SRC_WGT, WGT_SRAM)      # weight SRAM addr
    await bridge._apb_write(MXU_BASE + OFF_DST_OUT, OUT_SRAM)      # output SRAM addr
    await bridge._apb_write(MXU_BASE + OFF_SRC_SCALE, SCALE_SRAM)  # scale SRAM addr

    # Start computation
    await bridge._apb_write(MXU_BASE + OFF_CMD, 0x0000_0001)

    # Poll STATUS until DONE
    for _ in range(100000):
        s = await bridge._apb_read(MXU_BASE + OFF_STATUS)
        if s & 0x1:
            break
        await ClockCycles(bridge.dut.clk, 1)
    else:
        raise TimeoutError(f"{tag}: STATUS DONE timeout")

    # Wait for store-out sequencer to finish writing to SRAM
    await ClockCycles(bridge.dut.clk, 500)

    # Read back results from SRAM output area
    result_size = M * N * 4
    raw = await bridge._sram_backdoor_read(OUT_SRAM, min(result_size, 256))
    out_full = np.frombuffer(bytes(raw), dtype=np.int32)

    # Pad if needed
    gflat = golden.ravel()
    if len(out_full) < len(gflat):
        out_full = np.pad(out_full, (0, len(gflat) - len(out_full)))

    a = out_full.flatten().astype(float)
    g = gflat.flatten().astype(float)
    na, ng = np.linalg.norm(a), np.linalg.norm(g)
    cs = float(np.dot(a, g) / (na * ng)) if na > 0 and ng > 0 else 0.0

    # Hex dumps
    dram_hex = bytes(raw[:64]).hex() if len(raw) >= 64 else bytes(raw).hex()
    golden_hex = bytes(golden.ravel()[:16].tobytes()).hex()  # first 16 i32 = 64 bytes

    print(f"[{tag}] M={M} K={K} N={N} tile_major={use_tile_major}")
    print(f"[{tag}] SRAM_OUT first64={dram_hex[:64]}...")
    print(f"[{tag}] golden first64={golden_hex[:64]}...")
    print(f"[{tag}] cos={cs:.6f} na={na:.1f}")

    return {
        "tag": tag,
        "tile_major": use_tile_major,
        "cos_sim": round(cs, 6),
        "sram_hex_64": dram_hex[:128],  # 64 bytes
        "golden_hex_64": golden_hex[:128],
        "passed": bool(cs > 0.999 and na > 0),
    }


@cocotb.test()
async def test_diag_both(dut):
    """Run raw row-major vs tile-major activation comparison."""
    bridge = CocotbBridge(dut)
    await bridge.start_clock()
    await bridge.reset(5)
    # No firmware needed — we use the wrapper preload path directly
    await ClockCycles(dut.clk, 200)

    M, K, N = 1, 64, 64
    data = _gen(M, K, N, seed=42)

    results = []

    # Variant (a): Raw row-major → expected FAIL
    r1 = await _run_one_variant(bridge, "DIAG-RAW", M, K, N,
                                 data["act"], data["wgt"], data["golden"],
                                 use_tile_major=False)
    results.append(r1)

    # Variant (b): Tile-major packed → expected PASS
    r2 = await _run_one_variant(bridge, "DIAG-PACKED", M, K, N,
                                 data["act"], data["wgt"], data["golden"],
                                 use_tile_major=True)
    results.append(r2)

    # ── Write evidence ──────────────────────────────────────────────
    evidence_dir = os.path.join(_ROOT, "build", "evidence")
    os.makedirs(evidence_dir, exist_ok=True)
    evidence_path = os.path.join(evidence_dir, "ph8-diagnostic.txt")

    raw_passed = r1["passed"]
    packed_passed = r2["passed"]

    lines = []
    lines.append("=" * 70)
    lines.append("Phase 8 Diagnostic: Data-Layout Hypothesis")
    lines.append("=" * 70)
    lines.append(f"Test: M={M}, K={K}, N={N} (single 64x64 tile)")
    lines.append("")

    lines.append("-" * 70)
    lines.append(f"{'Variant':<20} {'cos_sim':<10} {'PASS/FAIL':<10} {'SRAM_OUT hex (first 64B)':<50}")
    lines.append("-" * 70)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        label = "tile-major" if r["tile_major"] else "raw row-major"
        lines.append(f"{label:<20} {r['cos_sim']:<10.6f} {status:<10} {r['sram_hex_64'][:50]}")
    lines.append("-" * 70)
    lines.append("")

    for r in results:
        lines.append(f"--- {r['tag']} ({'tile-major' if r['tile_major'] else 'raw row-major'}) ---")
        lines.append(f"SRAM_OUT hex (first 64B): {r['sram_hex_64']}")
        lines.append(f"Golden i32 hex (first 64B): {r['golden_hex_64']}")
        lines.append("")

    lines.append("=" * 70)
    if not raw_passed and packed_passed:
        lines.append("VERDICT: HYPOTHESIS CONFIRMED")
        lines.append("  => Raw row-major activation FAILS (cos_sim < 0.999)")
        lines.append("  => Tile-major packed activation PASSES (cos_sim >= 0.999)")
        lines.append("  => Root cause: activation must use tile-major layout for MXU")
    elif raw_passed and not packed_passed:
        lines.append("VERDICT: HYPOTHESIS FALSIFIED")
        lines.append("  => Raw PASS but tile-major FAIL -- opposite of expected")
        falsified = os.path.join(evidence_dir, "ph8-hypothesis-falsified.txt")
        with open(falsified, "w") as f:
            f.write("\n".join(lines))
        print(f"HYPOTHESIS FALSIFIED: see {falsified}")
    elif raw_passed and packed_passed:
        lines.append("VERDICT: HYPOTHESIS FALSIFIED")
        lines.append("  => Both PASS -- layout is not the root cause")
        falsified = os.path.join(evidence_dir, "ph8-hypothesis-falsified.txt")
        with open(falsified, "w") as f:
            f.write("\n".join(lines))
    else:
        lines.append("VERDICT: INCONCLUSIVE")
        lines.append("  => Both FAIL -- issue is deeper than activation layout alone")
        falsified = os.path.join(evidence_dir, "ph8-hypothesis-falsified.txt")
        with open(falsified, "w") as f:
            f.write("\n".join(lines))
    lines.append("=" * 70)

    content = "\n".join(lines)
    with open(evidence_path, "w") as f:
        f.write(content)
    print(f"\nEvidence written to {evidence_path}")

    # Assert for cocotb
    if not raw_passed:
        print("DIAG-RAW FAILED as expected (data-layout hypothesis supported)")
    if packed_passed:
        print("DIAG-PACKED PASSED as expected (tile-major layout works)")
    assert not raw_passed, f"Raw row-major unexpectedly PASSED (cos={r1['cos_sim']})"
    assert packed_passed, f"Tile-major unexpectedly FAILED (cos={r2['cos_sim']})"


if __name__ == "__main__":
    print("diagnose_data_layout.py — run via cocotb:")
    print("  MODULE=sim.diagnose_data_layout TESTCASE=test_diag_both simv_soc_ibex +COCOTB ...")
