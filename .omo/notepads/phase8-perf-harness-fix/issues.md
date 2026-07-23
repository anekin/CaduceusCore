# Phase 8 -- PERF Harness Fix: Issues

## FIXED: PERF-11 (cos_sim low due to data layout)
- **Status**: Fixed (2026-07-19, Task 2)
- **Root cause**: `PR.mmul()` wrote raw row-major activations; MXU requires tile-major
- **Fix**: Use `pack_int8_activation_tile_major()` and `pack_int4_tile_major()` in `PR.mmul()`
- **Evidence**: `build/evidence/ph8-diagnostic.txt` + Task 2 verification

## NOT RESOLVED: P0 Batch — PERF-01..04 FAIL on sz0001 (Task 4 re-run)
- **Status**: NOT RESOLVED (2026-07-19, Task 4 evidence)
- **PERF-01** (M=1,K=256,N=64): cs=0.436549, cyc=22545 — MXU computed wrong output
- **PERF-04** (M=1,K=128,N=128): cs=-0.217997, cyc=2 — doorbell stale from PERF-01
- **Root cause (PERF-01)**: Firmware doorbell path produces incorrect MXU results. Tile-major packing verified correct for individual bytes; firmware offsets (k_start*64, wgt offset) are tile-major compatible; wrapper preload parameters match. Suspected MMIO address conflict between firmware mxu_start() and wrapper preload registers.
- **Root cause (PERF-04)**: Doorbell ring buffer reuse — after PERF-01, NPU_HEAD=1; second HTAIL=1 write is no-op.
- **Evidence**: build/evidence/ph8-perf-04-regression.txt, build/evidence/w4-perf-p0.txt, build/evidence/ph8-p0_p1.log

## OPEN: PERF-11 cos_sim=0.381 despite tile-major packing
- **Status**: Open (2026-07-19, Task 5 evidence)
- **Finding**: Pre-fix (row-major) → ALL ZEROS output. Post-fix (tile-major) → non-zero output (cos_sim=0.381). Packing IS causal but INSUFFICIENT.
- **Root cause hypotheses**:
  1. pack_int4_tile_major nibble ordering mismatch with firmware expectation
  2. Scale data format (FP16 4B-padded) mismatch with descriptor
  3. Descriptor size field (32768B for act_packed) causing firmware SRAM address computation error
  4. Firmware doorbell path adds transformation not present in direct preload path (which achieved cos_sim=1.0 in Task 1 diagnostic)
- **Evidence**: build/evidence/ph8-perf-11-before-after.txt, build/evidence/ph8-perf-11-standalone.log
- **Next**: Compare firmware path vs direct preload path to isolate the divergence point

## FIXED: Ring buffer reuse in test_w4_perf_p2/p3/p4
- **Status**: Fixed (2026-07-19, Task 6)
- **Root cause**: All mmul() calls in P2/P3/P4 wrote to ring index 0, set HTAIL=1, waited for NHEAD=1. After first call, NHEAD stayed at 1 → subsequent calls exited immediately with stale data.
- **Fix**: Added `_ring_tail` counter to `PR`. Each `mmul()` increments `_ring_tail`, writes to `RING_BASE + (ring_tail-1)*32`, sets `HOST_TAIL` to the new count, and waits for `NPU_HEAD == ring_tail`.
- **Evidence**: P3 run 2 shows all 9 MMULs producing distinct (non-stale) output. P18a/P18b both work at cos_sim=1.0.

## BLOCKERS
### NOT RESOLVED: PERF-13 — M=1 multi-tile MMUL cos_sim < 0.999
- **Status**: NOT RESOLVED (2026-07-19, Task 6)
- **Description**: 7/9 MMULs in P3 fail with cos_sim 0.386–0.796. Only M=32 cases (attn_score, attn_weight) pass. M=1 cases with K>64 or N>64 (multi-tile) produce incorrect results.
- **Root cause hypothesis**: Firmware tile iteration loop or MXU wrapper preload sequencer bug for M=1 multi-tile. Single-tile M=1 (K=64,N=64, P18) works at cos_sim=1.0.
- **Impact**: Blocks PERF-13 correctness verification. Analytical entries (PERF-14/15/16) unaffected.

### NOT RESOLVED: PERF-17 — M=1,K=128,N=128 cos_sim=0.711
- **Status**: NOT RESOLVED (2026-07-19, Task 6)
- **Same root cause** as PERF-13: M=1 multi-tile bug.

### RESOLVED: PERF-20 repeatability passes (0.01% ≤ 1%)
### RESOLVED: PERF-18 inter-op_gap measured at 0 (sequential 2x M=1,K=64,N=64, cos_sim=1.0)

## CONFIRMED: FM-SOC 33/33 Regression — No Phase 8 Regressions
- **Status**: Confirmed (2026-07-19, Task 8)
- **Result**: 33/33 PASS, 0 FAIL, 0 SKIP on sz0001.
- **Evidence**: `build/evidence/fm-soc-regression.txt`
- FM-SOC path (`sim/rtl_soc_runner.py` + Ibex firmware) is orthogonal to `sim/perf_tests.py` PERF path.
- No RTL or firmware source files differ from committed baseline.

## RESOLVED: Task 7 Fullchain Pipeline — Single-Tile M=1 PASSES (cos_sim=1.0)
- **Status**: RESOLVED (2026-07-19, Task 7)
- **Result**: test_w4_perf_fullchain_sfu_vector PASS — cos_sim=1.000000, 5 gaps, DMA non-zero
- **Pipeline**: MMUL(M=1,K=64,N=64)→RMSNorm→VRESID→VCONV→SiLU — all 5 ops produce correct results
- **Key insight**: Single-tile M=1 works through firmware doorbell path for both MMUL (op=0x00) and SFU/Vector (ops 0x17/0x14/0x13/0x06)
- **Evidence**: build/evidence/fullchain-pipeline.txt, build/evidence/ph8-fullchain.log
- **Scope note**: This validates single-tile (K≤64,N≤64) path. Multi-tile M=1 (K>64 or N>64) remains unresolved (see PERF-13).

## Task 10: Phase 8 Resolution Status Documented (2026-07-19)
- **Status**: Complete
- **Artifact**: `docs/issues_found.md` — Phase 8 Resolution Status section with Root Cause Verdict Matrix (13 rows)
- **Key verdicts**: Data-layout hypothesis CONFIRMED, PERF-11 PARTIAL (DMA works, cs<0.999), PERF-13 NOT RESOLVED (firmware/RTL scope), FULLCHAIN single-tile RESOLVED (cs=1.0), FM-SOC NO REGRESSION (33/33), Q8_0/36-layer DEFERRED
- **Metis G11 compliance**: "Test PASS" vs "Blocker RESOLVED" distinction documented for PERF-20, PERF-18, FULLCHAIN
- **Verification**: both grep checks pass (`Phase 8`, `Root Cause Verdict`)

## NOT RESOLVED: PERF-13 — M=1 multi-tile MMUL cos_sim < 0.999
- **Status**: NOT RESOLVED (reconfirmed 2026-07-19, Task 7)
- **Updated analysis**: Single-tile M=1 (Task 7 fullchain, MMUL M=1,K=64,N=64) achieves cos_sim=1.0 through firmware doorbell. Multi-tile M=1 (K>64 or N>64) still fails.
- **Narrowed root cause**: The firmware tile iteration loop OR MXU wrapper broadcast sequencer produces incorrect output specifically when M=1 and the MMUL spans multiple K-tiles (K>64) or N-tiles (N>64). The tile-major packing IS correct (Task 1 direct preload achieved cs=1.0 for multi-tile via mxu_soc_wrapper direct path). The divergence point is in the firmware→MMIO→wrapper→MXU path, not the activation/weight data layout.
- **Impact**: Blocks PERF-01, PERF-04/09-13, PERF-17 for multi-tile M=1. Single-tile and M>1 cases unaffected.

## 2026-07-19: Task 9 — Status Sync to testcase-list-perf.md

### Status Summary
- **PASS (11)**: PERF-02,03,07,08,12,14,15,16,18,19,20 + FULLCHAIN-SFU-VEC
- **FAIL (6)**: PERF-01,04,05,06,13,17
- **PARTIAL (1)**: PERF-11 (cos_sim=0.381 standalone)
- **NOT RESOLVED (2)**: PERF-09,10 (no standalone evidence; blocked by M=1 multi-tile)

### New Blockers Identified
- **PERF-09/PERF-10**: No standalone Phase 8 evidence. Both blocked by M=1 multi-tile firmware path bug (same root cause as PERF-13). Need standalone re-run with ring buffer fix + M=1 multi-tile fix.
- **PERF-11 blocked by PERF-01/13 resolution**: Tile-major packing verified causal but insufficient. M=1 multi-tile fix in firmware is prerequisite for re-evaluation.
- **PERF-02/03 PASS but gated**: These structural tests pass (code path, logger) but their downstream verification (cos_sim≥0.999 on multi-tile) requires the M=1 multi-tile fix.

### Blockers for Task 8.7 (next)
- M=1 multi-tile firmware doorbell path fix (blocks PERF-01,04,05,09,10,11,13,17)
- Re-run P0+P1 batches with ring buffer fix applied
- Re-run PERF-09/10 standalone after M=1 fix
- Re-run PERF-11 (full Q_proj) after M=1 fix
- PERF-17 per-module breakdown requires correct MMUL output first
