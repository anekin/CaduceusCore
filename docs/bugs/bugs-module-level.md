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
  `SO_RD_SCALE_AR`/`SO_RD_SCALE_R`/`SO_WRITE_AW`/`SO_WRITE_W`) and is a
  **per-phase budget** (one budget for the whole pre-load phase, one per
  store-out row): it is cleared once both sequencers have left the AXI-wait
  states (`PL_READY`/`SO_IDLE`/`SO_TRANSFORM`), **not** per transaction stretch —
  the K-tile turnover inside the pre-load is `wait → wait`
  (`PL_LOAD_W_R -(rlast, more tiles)-> PL_LOAD_W_AR`), so one budget covers every
  tile of the phase. Consequence: a deliberately slow slave (~244+ cycles/beat
  average) on a large-K pre-load could in principle reach the threshold. What
  holds instead is the headroom argument below plus the empirical result (clean
  FM-SOC 33-case and e2e MXU runs, no false trip observed), not a per-cycle
  reset. *(Wording corrected 2026-09-25 per F2-1/R2: the earlier counter-rule
  claim — cleared on any FSM progress, therefore unable to accumulate — was wrong
  about the transition rule.)*
- On trip: sticky `wrp_wdt_timeout`, surgical FSM recovery back to IDLE (gated on
  the FSM actually waiting — `wdt_trip && pl_axi_wait` / `wdt_trip && so_axi_wait`,
  where `wdt_trip = wdt_fire && !wdt_ax_hs`: a cycle in which a handshake actually
  completes is progress and is never treated as a stall — so a legitimately set
  `LOAD_DONE` is never clobbered), and
  `irq = mxu_irq | wrp_wdt_timeout` (shared INTC bit0). `wdt_cnt` saturates at
  `WDT_TIMEOUT-1` and `wdt_trip` re-evaluates on the next stalled cycle, so a
  handshake-qualified deferral is at most 1 cycle and cannot disarm the watchdog.
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

**Note — defects / process debt discovered during this fix** *(rewritten
2026-09-25; module-level statistics are now **6 Fixed / 0 Open**, not the
"4 Fixed / 0 Open" recorded when this note was first written):*

- `DEFECT-WRP-1` — store-out AXI drain precedes `STATUS.DONE`; firmware-visible
  (`MISMATCH: 3180/4096`, first mismatch row 11). `test_mxu_single_tile_compute`.
  **Filed, fixed and closed as `BUG-MXU-WRP-001`** (2026-09-25 entry at the end of
  this log).
- `DEFECT-WRP-2` — `m_axi_wdata` X in accumulate mode. `test_mxu_accumulate_mode`.
  **Filed, fixed and closed as `BUG-MXU-WRP-002`** (2026-09-25 entry at the end of
  this log).
- `PROCESS-1` — `run_wrapper_mxu` Makefile target missing `cd $(REPO_ROOT)` → Error 127.
- `PROCESS-2` — runner verdict `grep -qE 'TEST.*PASS'` false-positives AND exits 0
  with real failures.
- `PROCESS-3` — tracked `build/evidence/wrap-mxu-regression.txt` is unreliable
  (claims 5 PASS; the real HEAD control baseline was 1 PASS / 4 FAIL).

The three `PROCESS-*` items remain unfiled process debt; the follow-up wave
(`c9a67d6`, branch `wrp-defects-and-tooling-fixes`) fixed the Makefile cwd
(`PROCESS-1`), replaced the fail-open grep with the fail-closed
`scripts/parse_cocotb_verdict.sh` (`PROCESS-2`) and untracked the regenerated
artifacts (`PROCESS-3`) — evidence
`.omo/evidence/task-1-wrp-defects-and-tooling-fixes.txt`. Full detail:
`.omo/notepads/rtl-open-bugs-cleanup/problems.md`.

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

### 2026-09-25 [Major] MXU STATUS.DONE Surfaced Before Store-Out Drain (BUG-MXU-WRP-001)

**Case**: MXU SoC wrapper — store-out path (`test_mxu_single_tile_compute`; firmware doorbell flow)
**Status**: Fixed

#### Description

The wrapper's APB `STATUS` readback (`paddr == 0x008`) exposed the MXU engine's raw `status_done`,
which `rtl/mxu/controller.v` asserts inside `S_DONE` — i.e. as soon as the last row has been queued
into the store-out FIFO. The store-out drain FSM was still writing rows to SRAM over AXI4, so a
poller that saw DONE and then read the output tile observed stale data.

RED witness (pre-fix, todo 2 evidence): `test_mxu_single_tile_compute` failed with
`MISMATCH: 3180/4096 elements differ, max_abs_diff=6400, first_mismatches=[(11,0),(11,2),(11,3),(11,4),(11,5)]`;
`STATUS.DONE asserted` was logged at 2880 ns while only rows 0..10 had been written (last store W
burst `awaddr 0x00040a00`), the drain needing 64 rows × 60 ns.

#### Root Cause

The engine's `status_done` is an `mxu_top`-internal wire (`rtl/mxu/mxu_top.v:113`), not a port: the
wrapper's APB read mux forwarded the engine STATUS.DONE with **no connection to the store-out drain
FSM**, so DONE was observable while rows 11+ were still queued. (`dbg_state == S_DONE` is itself a
one-cycle condition — S_DONE returns to S_IDLE on the next cycle unless a new CMD.START arrives — so
a raw passthrough could not have been used as a latch either.)

#### Fix Commit

`f65f5b9` (2026-09-24) — `fix(rtl/wrapper): gate MXU DONE on store-out drain (BUG-MXU-WRP-001)`.

Implementation in `rtl/wrapper/mxu_soc_wrapper.v`:

- `mxu_done_seen`: latched engine-done, set from `dbg_state == S_DONE`, cleared by the
  `mmio_cs`-qualified CMD.START write (`mmio_cs && mmio_we && mmio_addr==12'h04 && mmio_wdata[0]`;
  the qualifier is mandatory because `mmio_we`/`mmio_addr`/`mmio_wdata` are ungated raw APB signals).
  Same-cycle priority: the clear wins over the set, so a new command never inherits the previous DONE.
- `so_drain_done = so_fifo_empty && (so_state == SO_IDLE)`.
- The AND of the two is surfaced as STATUS bit1 via the `apb_mmio_prdata_wrp` wrap **before** the APB
  response mux, for `paddr == 12'h008` only; bit0 (BUSY) and bit2 (ERROR) pass through unchanged and
  every other offset stays byte-identical.

GREEN (todo 2): DONE moves 2880 ns → 6080 ns (+53 drained rows × 60 ns), asserted exactly one drain
row after the last W burst, `Bit-exact match: 0 mismatches out of 4096 elements`.

**Wording correction (recorded, important):** this fixes the **STATUS.DONE / APB read contract**.
The BUSY-based waiters are unaffected and still need their own drain wait —
`firmware/npu-regmap.h:269-271` (`npu_wait_done()`) polls `*status_reg & 1` (bit0 = BUSY), which the
controller holds through the drain, and cocotb's `_poll_done` does the same. The 256-nop loop at
`firmware/npu_firmware.c:275-281` therefore remains **LOAD-BEARING** (it gives the store-out FIFO time
to drain before the caller DMAs the output tile) and must not be deleted on the strength of this fix.

#### Evidence

- `.omo/evidence/task-2-wrp-defects-and-tooling-fixes.txt` — `WRP1-FIX: done` / `WRP1-GATE: green`;
  the RED signature is preserved at `build/evidence/task-2-red/` (the live per-test logs were
  overwritten by the GREEN run); post-fix suite 6/6 at commit `3a0e87c`
  (`.omo/evidence/task-4-wrp-defects-and-tooling-fixes.txt`).
- Regression context: wrapper 6/6, conformance GREEN/doc-div 0, e2e mxu single+multi PASS, FM-SOC
  25 pass / 8 skip / 0 fail / 0 timeout / 33 — `.omo/evidence/task-5-wrp-defects-and-tooling-fixes.txt`.

**Residuals (4, recorded — not silently closed):**

1. **IRQ unchanged** — `mxu_irq` still pulses in the cycle the controller enters `S_DONE`, i.e.
   before the drain. A naive gate (`mxu_irq && so_drain_done`) would swallow the one-cycle pulse
   forever and the INTC would never see a completion interrupt; a correct fix must latch `mxu_irq`
   into a sticky `mxu_irq_seen` and ship with an IRQ_EN=1 test case. Marked in the RTL as
   `WRP1-IRQ-RESIDUAL:` (`mxu_soc_wrapper.v:415`). The wrapper suite programs IRQ_EN=0, so no test
   covers it.
2. **Store-out WDT trip leaves DONE deasserted** — `wdt_trip && so_axi_wait` forces the drain FSM
   back to `SO_IDLE` and drops the in-flight row; the sticky `WRP_STATUS[1]` is the authoritative
   indicator for that fault path. The watchdog suite case covers only the pre-load path, not this one.
3. **`so_fifo_empty` is pointer equality** (`wr_ptr == rd_ptr`), a valid "everything landed" test only
   because the FIFO depth (64) equals `MAX_TILE`: one command pushes at most 64 rows, so the write
   pointer never laps the read pointer as long as the per-row drain latency stays below 64 cycles
   (measured ≈6 cycles/row in the wrapper TB, ≈11-15 in the FM-SOC mixed-mode runs). An `so_overflow`
   tripwire for the "wr_ptr == rd_ptr while a capture is still in flight" case is a recorded future
   item, not implemented in this wave.
4. **K > 128 per command remains unsupported** — the internal buffers are 2 K-tiles deep (see
   `BUG-MXU-WRP-002`).

---

### 2026-09-25 [Major] Preload K-Tile Count Derived From DIM0 Instead of WRP_K_TILES (BUG-MXU-WRP-002)

**Case**: MXU preload + accumulate (`test_mxu_accumulate_mode`; K=128 multi-tile)
**Status**: Fixed

#### Description

The wrapper's preload tile count was derived from the MXU `DIM0` register
(`wrp_k_tiles_derived = (dim0_k == 0) ? 1 : ((dim0_k + 63) >> 6)`). Both real clients program the
dedicated register — `firmware/npu_firmware.c:247-272` writes `WRP_K_TILES` then `WRP_CMD`, and
`sim/cocotb_bridge.py:2267` does the same — but the TB wrote `DIM0` only **after** the preload
handshake, so at preload time the derived count read the reset default 64. For K=128 exactly **one**
K-tile was fetched, the second tile's buffers were never written, and the uninitialized entries
surfaced as `X` on `m_axi_wdata` (`ValueError: Unresolvable bit in binary string: 'x'`).

RED witness: `build/evidence/t4-2-wv-mxu-test_mxu_accumulate_mode.log:129,147,181-198,276-298`
(single len-32 weight burst @0x10000 + single len-64 activation burst @0x20000; failure at 2940 ns).

#### Root Cause

A **phase** error, not a value error: every client agreed the count is `ceil(K/64)`, but `DIM0` is
written a phase too late to be usable as the preload tile count. The TB was the only client relying
on the wrapper's late derivation.

#### Fix Commit

`7179a82` (2026-09-24) — `fix(rtl/wrapper): preload honors WRP_K_TILES + TB programs K-tiles before TRIG_LOAD (BUG-MXU-WRP-002)`.

- RTL: the preload count is `wrp_k_tiles_eff = (wrp_k_tiles == 16'd0) ? 16'd1 : wrp_k_tiles`
  (`WRP_K_TILES`, 0x44); the `wrp_k_tiles_derived` wire and its now-dead `dim0_k` latch producer are
  retired; the header usage flow now shows K_TILES written **before** TRIG_LOAD.
- TB: `_preload_and_run` writes `OFF_WRP_K_TILES = (K + 63) // 64` after the base addresses and
  **before** `TRIG_LOAD`, then reads it back and asserts equality; `test_mxu_accumulate_mode` asserts
  the exact 4-burst preload geometry `[(0x10000,31),(0x10800,31),(0x20000,63),(0x21000,63)]`.

GREEN: suite 6/6, `WRP_K_TILES=2 programmed + readback OK (K=128)`, K=128 accumulate bit-exact
(4096/4096), X-hits 0/6 (RED: 1).

#### Evidence

`.omo/evidence/task-3-wrp-defects-and-tooling-fixes.txt` — `WRP2-FIX: done` / `WRP2-GATE: green`;
RED signature byte-identical to the recorded log; GREEN#1 plus an independent flake re-run GREEN#2.

**Notes (2, recorded):**

1. **Exact only for K ≤ 128** — the internal buffers hold exactly 2 K-tiles (`W_BUF_DEPTH=64`,
   `A_BUF_DEPTH=128`), so `WRP_K_TILES ∈ {1,2}` is honored exactly; K > 128 per command remains
   unsupported (X) and the buffer depths were deliberately not changed in this wave.
2. **`ctrl_acc_mode` is a dead controller input** — cross-K-tile accumulation is unconditional
   (`rtl/mxu/controller.v` `mac_reset_acc <= (k_tile == 0)`, `:205`); the TB comment was corrected
   accordingly. `COCOTB_RESOLVE_X` is **not** a valid approach for the X sighting (it would mask the
   symptom instead of fixing the phase error; 0 hits in the diff and in the edited files).

---

**Note — residuals carried out of the wrapper-fix wave** *(recorded, no new bug IDs;
last updated 2026-09-25):*

- `R1` — **conformance-TB line references deferred**: `rtl/tb/apb_conformance_real_tb.sv:24`/`:680`
  still carry the base/HEAD-mixed citations that were corrected on the doc side
  (`docs/bugs/bugs-soc-rtl.md`). The TB was frozen for this wave (zero diff); mirror of issues `I-17`.
- `I-13` — the tracked repo-root cocotb report `results.xml` is re-dirtied by every wrapper/cocotb
  run (cocotb CWD = repo root) and was restored with `git checkout -- results.xml` each time
  (todos 2/3/4/5); still **not fixed**. Real fix: `git rm --cached results.xml` or redirect
  `COCOTB_RESULTS_FILE` to `build/evidence/`.
- `I-20` — the FM-SOC runner launched detached (`nohup`) loses the shell exit status; the todo-5 run
  was judged from 33/33 per-case `runner_classification` lines plus the runner's own post-loop rule
  (`TOTAL != N_CASES` or `FAIL/TIMEOUT != 0` → `exit 1`). Suggested fix: append a machine-readable
  `[RUNNER-EXIT] rc=$?` trailer (or `echo $? > "$RUN_DIR/exit_code"`).
- `I-1` — `sim/regression/soc-verification-run.sh` cannot run **on** sz0001: the `p9_ssh` helper loops
  to `192.168.0.11` and there is no self-loop key. Invoke it from a host with key access to sz0001
  (sz0002 today).
- **Honest verdicts now visible** (the fail-closed parser working as intended, not regressions):
  `run_wrapper_sfu` 6/7 (one pre-existing SFU failure) and `run_wrapper_vector` 5/6 (the 6th case was
  previously silently skipped and itself fails) — `.omo/evidence/task-5-wrp-defects-and-tooling-fixes.txt`.

---

## Stats (Module-Level)

| Metric | Value |
|--------|:-----:|
| Total bugs | 6 |
| Open | 0 |
| Fixed | 6 |
