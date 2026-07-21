# BUG-MXU-P9-00B-broadcast-multitile — Doorbell Divergence Diagnostic

**Date:** 2026-07-21
**Phase:** Phase 9 Wave 1
**Trigger:** T3 divergence sweep

## Symptom

M=1 multi-tile MMUL via firmware doorbell dispatch showed cos_sim < 0.999
and decreasing as K grew, while the same (M,K,N) executed through direct
wrapper preload reached cos_sim ~1.0.

## Probe Evidence

Probe snapshots were captured in:

- `build/evidence/ph9-probe-case{1,2,3}-direct-K*.jsonl`
- `build/evidence/ph9-probe-case{1,2,3}-firmware-K*.jsonl`

Each snapshot contains wrapper/internal signal samples (preload FSM,
broadcast driver, store-out FIFO, AXI channels, MXU debug) recorded via
cocotb VPI backdoor with no RTL/firmware modification.

## Root Cause Verdict

CONCLUSION: **(D) Firmware K-tile loop + missing RTL accumulate mode +
SRAM/buffer overlap.**

Three independent defects combined to produce the divergence:

1. **All-K-tiles-at-once dispatch.** `firmware/npu_firmware.c` called
   `mxu_start()` once per N tile with `k_tiles = ceil(K/64)`. The MXU
   controller iterated the K dimension internally and reset the accumulator
   array on `k_tile == 0`. Only the first K-tile's partial products were
   retained, so results diverged as K grew.

2. **Missing cross-K-block accumulate mode.** When the firmware was changed
   to issue one `mxu_start()` per K-block, `rtl/mxu/controller.v` still
   reset accumulators on every `k_tile == 0`, so each new K-block overwrote
   the previous partial sum. The RTL had no way to suppress the reset for
   continuation tiles.

3. **SRAM and DRAM buffer overlap.** The fixed SRAM layout
   (`wbuf`/`sbuf`/`out_sram` at hard-coded offsets) was overlapped by the
   activation buffer for large K. `sim/perf_tests.py` also used fixed DRAM
   buffer addresses (`0x10000/0x20000/0x30000/0x40000`) that could overlap
   for large K/N, corrupting weights/scales before the MXU consumed them.

## Recommended Fix

1. **Firmware:** Replace the all-K-tiles dispatch with a per-K-block loop.
   For each N tile, call `mxu_start()` once per 64-element K block and set
   `CTRL[2] = 1` (`accumulate_ctrl = 4`) for every block after the first.
   Lay out SRAM dynamically so activation, double-buffered weights/scales,
   and the output scratch do not overlap.

2. **RTL:** Add an accumulate mode bit in `CTRL[2]`. In
   `rtl/mxu/controller.v`, only assert `mac_reset_acc` when
   `k_tile == 0 && !ctrl_acc_mode`. Wire `ctrl_acc_mode` through
   `rtl/mxu/mmio_if.v` and `rtl/mxu/mxu_top.v`.

3. **Testbench:** Spread DRAM buffers in `sim/perf_tests.py` based on the
   actual packed payload sizes so activations, weights, scales, and output
   never overlap.

## Verification Plan

1. Re-run the firmware-doorbell divergence sweep for all three M=1 cases.
2. Confirm every case reaches cos_sim >= 0.999.
3. Run `test_w4_perf_p9_causality` and write `build/evidence/ph9-causality.txt`
   to prove the fix is causal.
4. Run the direct-wrapper sweep to confirm no regression.
