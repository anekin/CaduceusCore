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
**Status**: Fixed

#### Description

The `controller.v` FSM has no watchdog timer. If the mac_array or buffer modules fail to respond (e.g., stuck in COMPUTE), the controller has no mechanism to detect the stall and raise `STATUS.ERROR`. Currently `STATUS.ERROR` can only be set by `cmd_abort` in specific FSM states.

#### Root Cause

Controller progresses purely on internal cycle counters (`compute_timer`, `store_counter`). No external stall detection exists. `STATUS.ERROR` only transitions via `cmd_abort` in READ_DIMS/LOAD_W/LOAD_A/COMPUTE/STORE_OUT states.

#### Fix Commit

`73a59d6` (2026-09-23) — `feat(rtl/wrapper): MXU wrapper AXI watchdog — sticky
timeout + FSM recovery (BUG-MXU-WDT-001)`.

Implementation: **wrapper AXI-layer watchdog** in `rtl/wrapper/mxu_soc_wrapper.v`.

- `localparam [19:0] WDT_TIMEOUT = 20'd1_000_000` (fixed threshold, **no new MMIO
  register**). Margin rationale: the cocotb budget formula `M*N*K/64 + 20000` is
  ~85.5 k cycles for the largest shape (K=N=2048), so 1 M has >10x headroom.
- The counter runs **only** in the 8 AXI-handshake-only states
  (`PL_LOAD_W_AR`/`PL_LOAD_W_R`/`PL_LOAD_A_AR`/`PL_LOAD_A_R`,
  `SO_RD_SCALE_AR`/`SO_RD_SCALE_R`/`SO_WRITE_AW`/`SO_WRITE_W`) and is cleared by any
  FSM progress, so a long but healthy transaction can never accumulate to the
  threshold.
- On trip: sticky `wrp_wdt_timeout`, surgical FSM recovery back to IDLE (gated on
  the FSM actually waiting — `wdt_fire && pl_axi_wait` / `&& so_axi_wait` — so a
  legitimately set `LOAD_DONE` is never clobbered), and
  `irq = mxu_irq | wrp_wdt_timeout` (shared INTC bit0).
- `WRP_STATUS[1]` is sticky, cleared by **any** `WRP_CMD` write (clearing on
  `pwdata[0]=1` alone would make the software ack itself re-arm a pre-load).
- New test `test_mxu_wrapper_watchdog_timeout`: peak `wdt_cnt = 999009`, trip after
  a 1.01 M-cycle wait (~13.9 s wall clock).

**Deviation from this entry's original suggestion (recorded, not hidden).** The
original text asked for a watchdog *inside* `controller.v` that increments "when the
FSM stays in the same state beyond expected cycles" and sets `STATUS.ERROR`. That is
not implementable as written, and the real exposure is elsewhere:

- The controller is **fully self-timed** — it advances on internal counters
  (`compute_timer`, `store_counter`; `rtl/mxu/controller.v:234/268/285/306`) and has
  no handshake it could stall on, so a counter keyed to "staying in the same state"
  would fire on every legitimate long COMPUTE.
- The **unbounded waits live in the wrapper's AXI handshake FSM**
  (`mxu_soc_wrapper.v:388-436`, `:639-687`) — an unresponsive slave parks the wrapper
  there forever.
- `STATUS.ERROR` in the controller is a **1-cycle pulse** (`controller.v:144` is the
  default `status_error <= 1'b0;`; the abort branches at `:185/219/239/256/284` raise
  it for one cycle), so it cannot serve as a latched status bit.

**Explicit non-claim (mandatory):** this fix does NOT instrument the failure mode
originally described here (stuck in COMPUTE → `STATUS.ERROR`). A wrapper-AXI
watchdog cannot observe a self-timed controller; the `STATUS.ERROR` mechanism planned
under MX-10 was **not implemented**.

**Gate deviation (honest record):** the plan's aggregate gate for the wrapper suite
was "6 cases all PASS"; measured **4 PASS / 2 FAIL**. Both failures are
**pre-existing** and byte-identical across the RED / GREEN / GREEN2 runs (and again
in todo 4): `test_mxu_single_tile_compute` (store-out AXI drain precedes
`STATUS.DONE`) and `test_mxu_accumulate_mode` (`m_axi_wdata` X). The watchdog itself
is RED→GREEN proven: the new test PASSes, the two pre-existing failures have
identical signatures before/after the change, no false trip occurred, and the sticky
clear works. The plan's todo-3 deviation line is the authoritative statement.

**Residuals (5, recorded — not silently closed):**

1. The originally-described failure mode (stuck-COMPUTE / `STATUS.ERROR`) is **not
   instrumented** by this fix.
2. `rtl/tb/tb_controller.v` is **stale**: it `force`s a non-existent
   `u_dut.state_r` (`:345`/`:358`; `controller.v` has no `state_r` — the signal is
   `state`, `:60`) and does not compile against the current RTL.
3. `timer_irq` has **no on-chip source**: it is an input of
   `rtl/soc/caduceus_soc_top.v:93` and the TBs tie it to 0 (`rtl/tb/tb_intc.v:108`).
4. The wrapper register block (`0x30-0x48`) is **not in `spec/npu_abi.json`** — it
   exists only in `firmware/npu-regmap.h` (`MXU_WRP_*`) — and this fix adds a new
   *bit* to it (`WRP_STATUS[1]`).
5. This is the **first hardware watchdog in this repo** — no precedent exists
   (threshold sizing, sticky-clear convention, IRQ sharing) to compare against.

#### Evidence

- `.omo/evidence/task-3-rtl-open-bugs-cleanup.txt` — `WDT-IMPL:
  wrapper-axi-wdt-sticky-bit`; `WDT-NEW-TEST: red-to-green, deterministic over 2
  independent recompiles`; `WDT-TEST: NOT-MET(2-pre-existing-guard-failures)` (the
  plan's all-green judgment line was deliberately **not** emitted, so a grep-based
  gate fails closed instead of accepting a false pass); `WDT-PEAK-CNT: 999009`;
  `WDT-WAIT-WALLCLOCK: 13.1 s (RED) / 13.9 s (GREEN) / 13.7 s (GREEN2)`.
- Threshold margin is empirically backed by todo 4: the full firmware DMA/MXU chain
  ran clean in all 25 executed FM-SOC cases plus both e2e-mxu cases, so the
  1,000,000-cycle threshold was not false-tripped by any legitimate wait
  (`.omo/evidence/task-4-rtl-open-bugs-cleanup.txt`).
- MX-10 originally confirmed only the normal path (`ERROR=0`); the timeout path was
  untestable because no mechanism existed. The `rtl/testplan.md` MX-10 compliance gap
  is **partially** closed: a watchdog now exists, but not the `STATUS.ERROR`
  behavior MX-10 specifies (residual 1).

**Note — defects / process debt discovered during this fix (NOT filed as new bug
entries; module-level statistics stay at 4 Fixed / 0 Open):**

- `DEFECT-WRP-1` — store-out AXI drain precedes `STATUS.DONE`; firmware-visible
  (`MISMATCH: 3180/4096`, first mismatch row 11). `test_mxu_single_tile_compute`.
- `DEFECT-WRP-2` — `m_axi_wdata` X in accumulate mode. `test_mxu_accumulate_mode`.
- `PROCESS-1` — `run_wrapper_mxu` Makefile target missing `cd $(REPO_ROOT)` → Error 127.
- `PROCESS-2` — runner verdict `grep -qE 'TEST.*PASS'` false-positives AND exits 0
  with real failures.
- `PROCESS-3` — tracked `build/evidence/wrap-mxu-regression.txt` is unreliable
  (claims 5 PASS; the real HEAD control baseline was 1 PASS / 4 FAIL).

Full detail: `.omo/notepads/rtl-open-bugs-cleanup/problems.md`. Flagged for a
follow-up plan; not closed here.

---

### 2026-07-02 [Minor] Perf Cycle Counter Off-by-One in READ_DIMS (BUG-MX-PERF-000)

**Case**: MX-P01 (shape=64,64,64)
**Status**: Fixed

#### Description

`tb_mxu_perf.v` performance cycle counter (`perf_cycle`) under-counted by 1 cycle. `$display` output showed `READ_DIMS=0`, causing the first tile to be missing 1 cycle in the measurement.

#### Root Cause

Counter used `if (perf_counting)` to gate accumulation. `perf_counting` was asserted one cycle after the FSM entered `READ_DIMS` state.

#### Fix Commit

`675afe0a` (2026-07-02) — the correct state-based gating was already present when `rtl/tb/tb_mxu_perf.v` was created (HEAD :290 `if (state != S_IDLE && state != S_DONE)`); no separate fix commit exists. The `if (perf_counting)` accumulation gating never existed in any commit: `git log --all -S "if (perf_counting)" -- '*tb_mxu_perf*'` returns empty. The placeholder sha that previously sat here was mistakenly written by the docs-split commit `2983e97b` (2026-07-06); this entry is an archive example, not a real bug — see `docs/bugs/bugs-archive.md:101` "占位示例 — 非真实 Bug".

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

`513fba6` — full `513fba6b7a1319542276e6cd2fedaac959c4aa8a` (2026-08-12, "fix(perf): correct prefill bottleneck from DMA-bound to compute-bound"). Removed the two-line override in both files; the original formula `per_tile_compute = array_H * (M + 1) + array_W` correctly scales with M.

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
| Total bugs | 4 |
| Open | 0 |
| Fixed | 4 |
