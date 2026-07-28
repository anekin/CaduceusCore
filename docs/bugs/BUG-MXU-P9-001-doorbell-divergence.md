# BUG-MXU-P9-001-doorbell-divergence — Doorbell Divergence Diagnostic

**Date:** 2026-07-21
**Phase:** Phase 9 Wave 1
**Trigger:** T3 divergence sweep

## Symptom

M=1 multi-tile MMUL via firmware doorbell dispatch shows cos_sim < 0.999,
while the same (M,K,N) executed through direct wrapper preload reaches
cos_sim ~1.0.

CONCLUSION: (B): Divergence pattern correlates with N/tile geometry rather than K-block count; direct preload passes but firmware doorbell fails because repeated wrapper preload triggers broadcast/store-out beat miscount at mxu_soc_wrapper.v:456-458 (act_buf_idx/w_buf_idx) or store-out sizing at mxu_soc_wrapper.v:572-578 (row_bytes_per_store/so_beats).
Citation: mxu_soc_wrapper.v:456-458

## Probe Evidence

Probe snapshots were captured in:

- `build/evidence/ph9-probe-case{1,2,3}-direct-K*.jsonl`
- `build/evidence/ph9-probe-case{1,2,3}-firmware-K*.jsonl`

Each snapshot contains >=5 wrapper/internal signal samples (preload FSM,
broadcast driver, store-out FIFO, AXI channels, MXU debug) recorded via
cocotb VPI backdoor with no RTL/firmware modification.

## Root Cause Verdict

CONCLUSION: (B): Divergence pattern correlates with N/tile geometry rather than K-block count; direct preload passes but firmware doorbell fails because repeated wrapper preload triggers broadcast/store-out beat miscount at mxu_soc_wrapper.v:456-458 (act_buf_idx/w_buf_idx) or store-out sizing at mxu_soc_wrapper.v:572-578 (row_bytes_per_store/so_beats).

Citation: mxu_soc_wrapper.v:456-458

## Recommended Fix

- If verdict (A): remove or gate the redundant I/W/O_ADDR writes in
  `firmware/npu_firmware.c:199-201` so that `mxu_wrapper_preload` is the
  single source of preload address state.
- If verdict (B): correct the broadcast/store-out geometry in
  `rtl/wrapper/mxu_soc_wrapper.v` around the cited lines.
- If verdict (C): run additional focused probes before Wave 2.

## Verification Plan

1. Re-run `bash scripts/p9_divergence_sweep.sh` after the chosen fix.
2. Confirm all three M=1 cases reach cos_sim >= 0.999 via firmware doorbell.
3. Run `bash scripts/p9_causality.sh` to prove the fix is causal.
