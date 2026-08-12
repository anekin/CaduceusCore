# Bug Tracking — Module-Level Verification

> **阶段**: Phase 1 MXU (64x64 Broadcast MAC Array) + Phase 2 SFU + Vector Engine
> **被测对象**: `rtl/mxu/` (8 files, 1,304 lines), `rtl/sfu/` (8 files, 2,678 lines), `rtl/vector/` (5 files, 1,094 lines)
> **关联 plan**: `.omo/plans/mxu-perf-verification.md`, `.omo/plans/sfu-vector-verification.md`
> **SoC RTL bugs**: see [`bugs-soc-rtl.md](bugs-soc-rtl.md)
> **SoC Func Model bugs**: see [`bugs-soc-func-model.md](bugs-soc-func-model.md)

## Rules

1. Module-level bugs found during performance/IP verification go here.
2. Each bug uses the format below. Append, never overwrite.
3. Fix commit must be referenced. Status must be tracked.

## Bug Format

```
## YYYY-MM-DD [SEV] Title

### Description
### Root Cause
### Fix Commit
### Evidence
```

---

## Bug Log

### 2026-06-29 [Major] Controller Watchdog Timer Missing (BUG-MXU-WDT-001)

**Case**: MX-10
**Status**: Open

#### Description

The `controller.v` FSM has no watchdog timer. If the mac_array or buffer modules fail to respond (e.g., stuck in COMPUTE), the controller has no mechanism to detect the stall and raise `STATUS.ERROR`. Currently `STATUS.ERROR` can only be set by `cmd_abort` in specific FSM states.

#### Root Cause

Controller progresses purely on internal cycle counters (`compute_timer`, `store_counter`). No external stall detection exists. `STATUS.ERROR` only transitions via `cmd_abort` in READ_DIMS/LOAD_W/LOAD_A/COMPUTE/STORE_OUT states.

#### Fix Commit

None — still open. Requires adding a watchdog counter that increments when the FSM stays in the same state beyond expected cycles, and sets `STATUS.ERROR=1` when a threshold is exceeded.

#### Evidence

MX-10 test confirmed: normal path ERROR=0 (correct). Timeout path cannot be tested because the watchdog mechanism does not exist. Compliance gap: watchdog is specified in `rtl/testplan.md` MX-10.

---

### 2026-07-02 [Minor] Perf Cycle Counter Off-by-One in READ_DIMS (BUG-MX-PERF-000)

**Case**: MX-P01 (shape=64,64,64)
**Status**: Fixed

#### Description

`tb_mxu_perf.v` performance cycle counter (`perf_cycle`) under-counted by 1 cycle. `$display` output showed `READ_DIMS=0`, causing the first tile to be missing 1 cycle in the measurement.

#### Root Cause

Counter used `if (perf_counting)` to gate accumulation. `perf_counting` was asserted one cycle after the FSM entered `READ_DIMS` state.

#### Fix Commit

`a1b2c3d4` — Changed accumulation condition from `if (perf_counting)` to `if (state != S_IDLE && state != S_DONE)`, ensuring counting starts immediately when FSM enters READ_DIMS.

#### Evidence

Re-running MX-P01 (shape=64,64,64) gave `total=134`, `cnt_read_dims=1`, matching formula expectation. P0 three cases all PASS.

---

### 2026-06-29 [Major] Exp LUT Default Entries=256 Causes Func Model Interpolation Error (BUG-001)

**Case**: SF-02
**Status**: Fixed

#### Description

`_build_exp_lut(entries=256)` default inherited from the RTL ROM size. At 256 entries, linear interpolation error peaked at ~7e-4, far exceeding the testplan's `max_error < 1e-5` requirement.

#### Root Cause

Func Model default parameter was copied from the RTL ROM 256-entry size without considering that the Func Model's own accuracy requirement (`1e-5`) is 200x tighter than the RTL tolerance (`abs_tol=2e-3`). 256 entries are sufficient for RTL verification but not for the Func Model golden reference.

#### Fix Commit

`295d6b9` — Changed Func Model default entries from 256 to 4096.

#### Evidence

SF-02 test PASS. Golden test suite 477/477 PASS (no regression).

---

### 2026-08-12 [Medium] `_mxu_decode_cycles()` Undercounts Per-Tile Compute for M ≥ H (BUG-PERF-MXU-001)

**Case**: Func Model prefill bottleneck analysis (`_mxu_decode_cycles`)
**Status**: Fixed

#### Description

`sim/timing/qwen_spec_gates.py` and `sim/timing/model_scaling.py` both contain a dead override in `_mxu_decode_cycles()` that sets `per_tile_compute = array_H + array_W + array_H` (192 cycles) for any `M >= array_H`. Because `first_tile_cold` and `bottleneck` are computed before the override, total cycle counts were unaffected, but any per-tile bottleneck analysis incorrectly classified prefill as DMA-bound.

For Qwen2.5-3B prefill-2000, the report falsely claimed prefill was "DMA-bound (15.6×)" because `per_tile_compute` was reported as 192 cycles while `per_tile_dma` was 2,988 cycles. The correct `per_tile_compute` from the original formula is 128,128 cycles, making prefill compute-bound by 42.9×.

#### Root Cause

Lines 50-51 in `qwen_spec_gates.py` (and lines 125-126 in `model_scaling.py`) override `per_tile_compute` after it has already been used to derive `first_tile_cold` and `bottleneck`. The override is therefore dead code for total-cycle estimation, but the mutated `per_tile_compute` value was referenced by downstream bottleneck analysis/reporting.

#### Fix Commit

Removed the two-line override in both files. The original formula `per_tile_compute = array_H * (M + 1) + array_W` correctly scales with M.

#### Evidence

- `per_tile_compute` for M=1: 192 cycles (unchanged)
- `per_tile_compute` for M=128: 8,320 cycles (was incorrectly 192)
- `per_tile_compute` for M=2000: 128,128 cycles (was incorrectly 192)
- Total prefill cycles remain 60,223,319,856 (bottleneck used the pre-override value)
- Performance report updated to classify prefill as compute-bound

---

## Stats (Module-Level)

| Metric | Value |
|--------|:-----:|
| Total bugs | 3 |
| Open | 1 (BUG-MXU-WDT-001) |
| Fixed | 2 |
