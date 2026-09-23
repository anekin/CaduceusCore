# issues

## [2026-09-23] session start

## [2026-09-23] todo 1 — issues / observations (BUG-RTL-SOC-010 doc-side)

- **[PRECHECK delta — benign, disclosed]** The precheck asserts
  `git status --porcelain | grep -cE '^ ?M' == 7` but measured **8**. The extra line is
  `.omo/plans/rtl-open-bugs-cleanup.md`: a one-character `[ ] 0.` -> `[x] 0.` checkbox flip
  written 2026-09-23 18:45:44, i.e. AFTER todo 0's commit (18:42:05). Todo 0 recorded that path
  as UNTRACKED (`?? .omo/plans/...`) in its pre-commit snapshot, and its own commit then made it
  tracked — so the post-commit checkbox flip *necessarily* surfaces as ` M`. It is plan metadata:
  not product code, not in the 7-file protected set.
  All substantive precheck items passed (branch == rtl-open-bugs-cleanup; HEAD subject exact;
  `git show --name-only HEAD` == exactly the 3 `.omo/` paths; all 7 protected files re-hashed
  byte-identical to todo 0's AFTER block, `diff` empty). Proceeded with the delta recorded rather
  than discarding a verified-good baseline.
  **Plan-authoring fix for future plans:** phrase this assertion as "the 7 protected paths are
  still dirty AND no other product file is", or explicitly exclude `.omo/plans/<slug>.md`
  (the executing agent's own orchestrator mutates it as todos complete).

- **[Waiver-vs-bug tension — for the ledger maintainer]** The correct long-term fix for
  BUG-RTL-SOC-010 is an RTL *behavior* fix (implement CTRL[3]; make BAR1_MASK bit31 writable).
  That requires (a) a new/extended remediation exception and (b) vendored-IP surgery for the
  enable bit — the vendored `pcie_axi_master` exposes no top-level `enable` port. This commit only
  removes the *documentation* divergence. **`docs/bugs/bugs-soc-rtl.md` still lists
  BUG-RTL-SOC-010 as `Status: Open`; todo 5 owns the ledger update.** Flagging so todo 5 does not
  mark it "Fixed" without recording the doc-only scope.

- **[6th comment edit site beyond the plan's literal (a)-(e) list]** The REG_DOCDIV comment
  block's two PCIE annotation lines (:751-753) were rewritten to RETIRED wording, mirroring the
  existing DOORBELL precedent four lines below. Left untouched it would have asserted
  "header :258 says [3]=enable (unimplemented)" directly above an all-zero PCIE row — i.e. a
  now-FALSE statement inside a commit whose whole purpose is doc/RTL alignment. Comment-only,
  zero behavior impact, disclosed in the evidence file.

- **[Evidence-delivery note]** `sim/regression/apb_conformance_real.log` and
  `compile_apb_real.log` plus `simv_apb_conformance_real` are all gitignored (verified with
  `git check-ignore`), so the run log is NOT versioned. The gate evidence therefore lives in
  `.omo/evidence/task-1-rtl-open-bugs-cleanup.txt` (committed via `git add -f`), with the
  measured values transcribed and the reconstruction commands recorded.
  Also: the runner does `rm -rf csrc` unconditionally, so the APB conformance target always
  performs a full ~16 s VCS compile — convenient for freshness, but it means every invocation
  costs a full rebuild.

## [2026-09-23] todo 2 — issues / observations (BUG-RTL-SOC-011 RTL side)

- **[PROCESS HAZARD — report, not a blocker: the APB conformance target can
  silently reuse a stale binary]** `soc-verification-run.sh` cleans
  `simv_soc_cocotb*`, but `run_apb_conformance_real` builds
  `simv_apb_conformance_real`; and the Makefile's prerequisites are the flist
  FILES, not the RTL files listed inside them. So an RTL-only edit (no TB edit)
  produces a GREEN verdict from the PREVIOUS binary — a false PASS waiting to
  happen for any future todo that changes only RTL. Measured instance: the
  pre-edit control run reused todo 1's binary (log 19:15:33 vs simv 19:00:48).
  Every judged run in this todo forced `rm -f sim/regression/simv_apb_conformance_real`.
  **Suggested plan-authoring fix:** add `simv_apb_conformance_real` to the
  runner's rm list, or make the rule depend on the flist *contents*. Flagging
  for whoever owns `sim/regression/` (out of todo 2's scope).

- **[PRECHECK — clean this time]** Unlike todo 1, the todo-2 precheck as written
  ("M-set ⊆ 7 protected ∪ plan file") matched exactly: 8 M-lines = 7 protected +
  `.omo/plans/rtl-open-bugs-cleanup.md` (orchestrator checkbox). No delta to
  disclose. HEAD `2e468ac` subject and `git show --name-only HEAD` (3 paths) both
  exact.

- **[Coverage gap, disclosed — todo 4 is the guard]** The STATUS **read-clear
  removal** is NOT observable in `apb_conformance_real_tb`: it can only differ
  when `STATUS.DONE==1`, and this TB never launches a transfer (Phase 5 asserts
  "no CMD.START was ever written"; the DMA CMD probes are 0x42 / 0x80, bit0 never
  set). Proven empirically, not just argued: the RED-B log (read-clear present)
  and the GREEN log (read-clear removed) are byte-identical except the one CMD
  check and the counters it feeds — every `DMA +0x008` STATUS check is identical.
  The end-to-end guard for the DONE/read semantics is todo 4's FM-SOC firmware
  path (real START -> DONE -> read).

- **[Extra edit sites beyond the plan's literal list — 4 more, all disclosed]**
  (1) TB header `:22-26` asserted "DMA: CMD STORES the written value and reads it
  back" — false after this commit; (2) `docs/func-model-mmio-spec.md:310`;
  (3) `:357`; (4) `:390` §7.2 table "Clears on read of STATUS". All corrected;
  all comment/doc-only, zero behavior impact; recorded in the evidence file.
  A repo-wide grep for the removed claims outside the two in-scope doc files
  (excluding vendored/gen) returned 0 hits.

- **[Scope note for todo 5 — doc-only vs behavior, made unambiguous]**
  todo 1 = BUG-RTL-SOC-010, DOC-ONLY (waiver restricts rtl/ edits to
  axi_crossbar.v + npu_firmware.c; the enable bit needs vendored-IP surgery).
  todo 2 = BUG-RTL-SOC-011, REAL RTL BEHAVIOR FIX inside rtl/ip/dma_wrapper.v
  (2 behavior changes) + TB retirement + 2 doc files. `docs/bugs/bugs-soc-rtl.md`
  is still untouched by this todo; todo 5 owns the ledger update and should mark
  011 as Fixed (RTL) with the "no read side effects / write-only on the bus"
  wording, and 010 as doc-aligned with the waiver reasoning intact.

- **[Pre-existing benign artifact worth recording]** Both RED-B and GREEN logs
  contain `Error: "rtl/wrapper/vector_soc_wrapper.v", 172: [VEC_WRP]
  valid_bytes_total 262140 exceeds buffer capacity 65536` — an intentional
  hostile-write diagnostic from the UNMODIFIED vector wrapper (neither that file
  nor the TB's VECTOR rows are in this diff), and the accompanying check still
  PASSes. It is absent from todo 1's evidence file; noting it here so a future
  reader does not mistake it for fallout from this commit.

## [2026-09-23] todo 3 — issues / observations (BUG-MXU-WDT-001)

- **[VERIFICATION-INTEGRITY, repo-wide — the big one] The wrapper runner's
  PASS/FAIL detection is a false-positive detector.**
  `scripts/wv_run_mxu.sh:96-103`:
  `if grep -qE 'TEST.*PASS' "$TEST_LOG"; then RESULT="PASS" elif grep -qE
  'TEST.*FAIL' ...`. cocotb's summary line for a FAILING test is
  `** TESTS=1 PASS=0 FAIL=1 SKIP=0 ...`, which matches `TEST.*PASS`, so the PASS
  branch always wins. Measured: `grep -cE 'TEST.*PASS'` == 1 on the failing
  `wv-mxu-test_mxu_single_tile_compute.log`. Effect: the runner reported
  "All 6 tests PASSED" on the RED run whose new test demonstrably failed, and
  `build/evidence/wrap-mxu-regression.txt` is a false-green artifact. The
  aggregator `scripts/wv_regression.sh` consumes that file (MIN_BYTES=50), so the
  false green propagates. **Not fixed**: `scripts/wv_run_mxu.sh` is in this
  todo's whitelist but the instruction allowed only the `TESTS=(...)` edit.
  Fix suggestion (for the owner of `scripts/`): match the per-test line, e.g.
  `grep -qE '^\s+\*\* sim\.tests\..*\sPASS\s'` or count
  `grep -cE 'TESTS=1 PASS=1'`, and treat "no verdict line found" as FAIL.
  `wv_run_sfu.sh` / `wv_run_vector.sh` very likely share the same pattern.

- **[RECORDED BASELINE IS FALSE] `docs/bugs/bugs-soc-rtl.md:624` and
  `build/evidence/wrap-mxu-regression.txt` claim the MXU wrapper suite was
  5/5 PASS.** The control run on the UNMODIFIED tree (2026-09-23 20:06) proves
  the truth was **1 PASS / 4 FAIL** — 4 tests died at 160-180 ns with
  `AttributeError: 'ApbMaster' object has no attribute '_bus'`.
  **For todo 5**: do not cite the 5/5 claim as a verification basis for anything.

- **[PRE-EXISTING DEFECT — store-out drain lags STATUS.DONE; firmware-visible]**
  `test_mxu_single_tile_compute` fails with
  `MISMATCH: 3180/4096 elements differ, max_abs_diff=6400,
  first_mismatches=[(11, 0), (11, 2), ...]`. Proven from the sim log: AxiRam's
  write bursts stop at `awaddr 0x00040a00` (row 10) when `STATUS.DONE` asserts
  at 2880 ns, and the first golden mismatch is row 11; the drain runs at
  ~60 ns/row so rows 11..63 land ~3.2 us later. This contradicts the module's own
  documented usage flow (`mxu_soc_wrapper.v` header step 7: "Poll STATUS.DONE ->
  read results from SRAM at WRP_OUT_BASE") and `firmware/npu_firmware.c` follows
  exactly that flow, so the wrapper has no store-out-complete signalling at all.
  Signature byte-identical pre- and post-RTL (no watchdog involvement).
  **NOT fixed**: needs either a store-out datapath change (behaviour change to
  the load/store path; forbidden by this todo, and `rtl/mxu/**` is out of
  bounds) or a drain wait in the test (which would MASK a real defect). Needs
  its own bug id + a scope decision.

- **[PRE-EXISTING DEFECT — X on `m_axi_wdata` in accumulate mode]**
  `test_mxu_accumulate_mode` dies inside cocotbext-axi:
  `cocotbext.axi.axi_slave._process_write -> ValueError: Unresolvable bit in
  binary string: 'x'` at the first store-out burst (2930 ns, K=128 accumulate
  path). The wrapper's own `MXU_WRP_DEBUG` block in `mxu_soc_wrapper.v` exists
  precisely for this hazard. Signature byte-identical pre- and post-RTL.
  **NOT fixed** (different bug class from WDT-001; same masking argument).

- **[RESIDUAL for todo 5 — wrapper status bits are outside the ABI]** The wrapper
  register map (0x30-0x48) is not in `spec/npu_abi.json` (only in
  `firmware/npu-regmap.h`) — a known residual — and this commit adds a new
  *bit* (WRP_STATUS[1] = WDT_TIMEOUT, sticky, cleared by any WRP_CMD write).
  So the ABI divergence now covers a status bit as well as a register block.
  Todo 5's WDT-001 Residual section should record it.

- **[WHITELIST-DRIVEN RESIDUALS left untouched on purpose]**
  (1) `sim/regression/Makefile:1313-1315` — the three `run_wrapper_*` recipes
  lack the `cd $(REPO_ROOT) &&` prefix, so the mandated
  `soc-verification-run.sh run_wrapper_mxu` cannot work (Error 127). The
  Makefile is not in this todo's whitelist.
  (2) `scripts/wv_run_mxu.sh` header comment still says "runs all 5 test cases"
  (now 6) — same whitelist constraint ("this is the only allowed edit there").

- **[DISCLOSED, in-whitelist repair] 3 x `wait_done(..., clk=dut.clk)` added to
  `sim/tests/wrapper/test_mxu_wrapper.py`** (`_preload_and_run` x2, test 2 x1).
  Semantically neutral (no assertion, threshold or comparison changed; the
  sibling suites and this file's own test 6 already pass `clk=`). It is what
  turned 4 crashing tests into 3 passing + 2 genuinely-failing ones. Also
  corrected the module docstring, which said "5 cocotb tests" and
  "Does NOT test watchdog (BUG-MXU-WDT-001)" — both now false.

- **[Test-design note, not a defect]** `test_mxu_store_out_burst` PASSES while
  `test_mxu_single_tile_compute` fails, because it only checks row 0 (columns
  0-31) — i.e. exactly the rows that ARE written before DONE. A single-row check
  cannot detect a drain lag; worth remembering when auditing this suite.

- **[Judgment-line discipline]** The plan required the line `WDT-TEST: 6-pass`.
  It was deliberately NOT emitted (only 4 of 6 pass); the evidence file carries
  `WDT-TEST: NOT-MET(2-pre-existing-guard-failures)` instead, so that a
  grep-based gate fails closed rather than accepting a false pass.

- **[Self-inflicted hazard, caught and fixed] Evidence file contained the
  literal spelling of the plan's required pass judgment line** while explaining
  that the line was not emitted -> a grep-based gate would have falsely accepted
  it. Reworded; commit amended to 73a59d6 (same message, same 4 paths, branch
  local-only). Recorded in the ledger as `task-artifact-amended`.

## [2026-09-23] todo 4 — regression findings (commit 46b17cf)

- **[CONFIRMED, still open] PROCESS-1 — `run_wrapper_mxu` make target is broken.**
  Re-confirmed by construction: this todo could not use the mandated
  `soc-verification-run.sh run_wrapper_mxu` and used `bash scripts/wv_run_mxu.sh`
  instead (Makefile:1313-1315 recipe lacks `cd $(REPO_ROOT) &&`; the runner cds to
  sim/regression first → Error 127). Evidence-only todo, so it was NOT fixed.
  Same defect family affects the sfu/vector wrapper targets.

- **[CONFIRMED, still open — one notch worse than todo 3 recorded] PROCESS-2 — the
  wrapper runner's PASS detector is a false positive AND it poisons the exit code.**
  This run it printed `[wv_run_mxu.sh] All 6 tests PASSED.` and **exited 0** while
  `test_mxu_single_tile_compute` and `test_mxu_accumulate_mode` really FAILED. Root
  cause unchanged: `grep -qE 'TEST.*PASS'` matches the failing test's own
  `** TESTS=1 PASS=0 FAIL=1 SKIP=0 **` line, so `FAIL_COUNT` never increments and the
  success branch wins. Consequence: a CI-style caller that trusts the exit status of
  this runner will treat a 4-pass/2-fail suite as green. Fix = parse
  `** sim.tests.wrapper.<t>  PASS|FAIL` lines (ANSI-stripped) or use
  `** TESTS=n PASS=n FAIL=n **`.

- **[CONFIRMED, still open] PROCESS-3 — the tracked `build/evidence/wrap-mxu-regression.txt`
  is not evidence.** The runner rewrote it again this run; it is restored to HEAD and
  must not be cited. (Same for `results.xml`.)

- **[CONFIRMED DETERMINISTIC, pre-existing, not regressions] DEFECT-WRP-1 / DEFECT-WRP-2
  reproduced byte-identically for the third consecutive suite run** (todo 3's
  RED/GREEN/GREEN2 + this one):
    DEFECT-WRP-1 `test_mxu_single_tile_compute` @2880 ns —
      `MISMATCH: 3180/4096 elements differ, max_abs_diff=6400,
       first_mismatches=[(11, 0), (11, 2), (11, 3), (11, 4), (11, 5)]`
      (store-out AXI drain lags `STATUS.DONE`; firmware-visible because
      `firmware/npu_firmware.c` polls DONE then reads results)
    DEFECT-WRP-2 `test_mxu_accumulate_mode` @2940 ns —
      `ValueError: Unresolvable bit in binary string: 'x'` raised from
      `cocotbext/axi/axi_slave.py:154 _process_write` (wrapper drives X on
      `m_axi_wdata`)
  Neither is caused by todo 3's watchdog (signatures unchanged pre/post that change)
  and neither is caused by todo 2's DMA change. Fixing them is outside an
  evidence-only todo.

- **[PASS / no action] Regression from todos 2 and 3 is clean.** FM-SOC 33 =
  25 pass / 8 skip / 0 fail / 0 timeout with the 8 SKIP IDs ID-for-ID identical to the
  `task-3-bug-009-doorbell-fix` baseline (014/015/016/021/022/023 superseded +
  017/019 not-applicable). FM-SOC-011 (the DMA case most exposed to todo 2's
  CMD-write-only + STATUS-no-read-clear change) PASSES, and the two full-chain cases
  FM-SOC-032 / FM-SOC-10X PASS. Conformance is GREEN with `doc_div_cnt == 0`,
  `slv_docdivs[3] == 0`, `slv_docdivs[4] == 0`, doorbell 60, Total 263.

- **[PASS / no action] Watchdog threshold margin is now empirically backed.** The
  whole firmware DMA/MXU chain ran clean in all 25 executed FM-SOC cases plus both
  e2e-mxu cases, so the 1,000,000-cycle `WDT_TIMEOUT` was not false-tripped by any
  legitimate wait. This closes the plan's "threshold margin" question (todo 3's
  argument was analytic — 85,536-cycle budget vs 1e6 — this is the empirical half).

- **[Observation, no action] The repo-root `csrc/` directory is a shared, pre-existing
  incremental-compile cache** (files from 2026-07-23; 578 files, 59 rewritten by this
  todo's e2e compiles because the Makefile recipe cds to REPO_ROOT and VCS defaults
  `-Mdir` to `./csrc`). It is gitignored, but it is worth knowing that e2e targets
  write there — do NOT mistake it for `sim/regression/csrc`, which is the one
  `soc-verification-run.sh` cleans.

## [2026-09-23] todo 5 — issues / observations (ledger wrap-up, commit 1af5c70)

- **[PLAN-INTERNAL CONTRADICTION — resolved, flagged for plan authors]** The
  plan's own summary line 29 says the By-Severity rows should have their member
  lists updated ("010/011 仍 Minor，行内移除它们即可") while the count stays 2 —
  which cannot be self-consistent (a count of 2 over a list of 0). The todo-5
  detail line marks the opposite rule as **定死**: severity counts ALL bugs
  regardless of status → **本表零改动**. Followed the frozen per-todo rule
  (zero change) and recorded the contradiction in the evidence §1 + §2.
  **Plan-authoring fix:** delete the summary-line variant, or state the frozen
  rule in both places. A reviewer reading only line 29 would expect a different
  diff.

- **[ACCEPTANCE-LINE CONFLICT — measured both, disclosed, not claimed]** Todo 5's
  acceptance requires
  `git diff main..HEAD --name-only -- docs/ README.md` ⊆ {4 files}. Measured
  branch-level set also contains `docs/func-model-mmio-spec.md`, which **todo 2**
  legitimately changed and which the plan's **F4 whitelist explicitly allows**
  (plan line 180). The literal assertion is therefore unsatisfiable without
  reverting approved work. Resolution: this todo's own delta (the uncommitted
  diff, and post-commit `git diff HEAD^ --name-only`) is **exactly** the 4
  whitelisted files; F4 governs the branch level. Recorded in evidence §5.
  **Plan-authoring fix:** phrase a docs-only todo's scope gate as
  `git diff HEAD --name-only` (delta), leaving `merge-base..HEAD` to F4.

- **[RESIDUAL for a future docs pass — historical text now superseded]**
  `docs/bugs/BUG-MXU-WDT-001.md`'s "Expected Behavior" (item 2: "Set
  `STATUS.ERROR=1` when a threshold is exceeded") and "Verification" ("Timeout
  path cannot be tested because the watchdog mechanism does not exist") are now
  historical/superseded. The Status line's explicit non-claim covers the
  semantics (the `STATUS.ERROR` behavior was NOT implemented; a wrapper-AXI
  watchdog exists but is a different mechanism), so no false claim survives — but
  a future pass may want to mark those two sections `[superseded 2026-09-23]`
  explicitly. Not done here: the plan scoped that file to its `:4` severity line,
  and the status-line sync was already a disclosed extra.

- **[Row-location note — line numbers in the plan drifted]** The plan's
  acceptance refers to `README.md :26` and `:39`; after the edit the SoC RTL row
  sits at `:25` and the module-level rows at `:26`/`:39` (the snapshot table
  starts at `:21`). Content-locate, don't line-locate: the greps used here are
  content-anchored (`"15 Fixed"`, `"4 = 4 Fixed / 0 Open"`), which is why the
  drift was harmless.

- **[Scope choice recorded — no "Ledger update" prose line added]**
  `docs/bugs/bugs-soc-rtl.md` has a convention of dated "Ledger update YYYY-MM-DD
  (...)" paragraphs at the top (the last is 2026-09-02). Todo 5's edit list does
  not include one, so none was added — the two entries themselves carry
  Date/Status/commit/evidence. Flagging so a future maintainer does not read the
  absence as an oversight; if the convention is meant to be mandatory, the plan
  should say so explicitly.

- **[Cleanup receipt]** No scratch files, no background jobs, nothing to cancel.
  This todo ran no cocotb/VCS, so the two tracked artifacts the wrapper runner
  normally rewrites (`results.xml`, `build/evidence/wrap-mxu-regression.txt`)
  were not touched and needed no restore. Post-commit worktree is back to
  {7 protected dirty files} U {plan file} U {pre-existing untracked entries}.
