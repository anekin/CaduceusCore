# issues — wrp-defects-and-tooling-fixes

## Open (environment / residual — outside this repo's source tree)

### I-1 (OPEN, env) `ssh zhengs@192.168.0.11` from sz0001 itself is unauthorized
`bash sim/regression/soc-verification-run.sh run_wrapper_mxu` forwards to sz0001, runs the (now fixed)
recipe, and then `scripts/p9_lib/p9_sz0001.sh` fails its self-loop SSH:
```
Permission denied, please try again.  (x2)
Permission denied (publickey,password).
make: *** [run_wrapper_mxu] Error 255
```
The Error-127 defect (recipe cwd) is fixed and proven; what remains is that every `wv_run_*.sh` drives
sz0001 through `p9_ssh` (hard-coded `192.168.0.11`), which only works from a host that has
key access — sz0002 does, sz0001 does not (no self key/authorized_keys entry). Impact: p9_ssh-based
`make run_wrapper_*` / `run_wrapper_all` targets are not runnable *from sz0001* until a self-loop key is
installed. Workaround used and verified: run the same recipe from the dev host
(`cd sim/regression && make run_wrapper_mxu`, 160 s, 4 PASS / 2 FAIL). Not touched here: keys are
environment, and the todo's file scope is scripts/Makefile/fixtures/.gitignore/evidence.

### I-2 (OPEN, residual) `scripts/wv_run_mxu.sh` per-test simv is still unbounded
Its 6 cases finish in ~160 s including the forced compile and no hang has been observed, so the bound
was added only to the two runners rewritten here (`wv_run_sfu.sh` 900 s whole-module,
`wv_run_vector.sh` 240 s per test). If mxu ever hangs, kill the remote simv and add the same
`timeout -k 10` wrapper.

### I-3 (OPEN, residual) `scripts/wv_run_bug005.sh` / `wv_run_bug007.sh` untouched
Out of scope for T1; their `wrap-bug005-result.txt` / `wrap-bug007-result.txt` remain tracked statics
and are transcribed (not re-derived) by `wv_regression.sh`, labelled as such in the summary output.

## Closed by this todo (with the proof)

### I-4 (FIXED, pre-existing) `wv_run_vector.sh` could not import its own test module
`ModuleNotFoundError: No module named 'tests.wrapper'` from
`sim/tests/wrapper/test_vector_wrapper.py:26` — the remote command exported `PYTHONPATH=$PWD`
(=REPO_ROOT) and not `$PWD/sim`. Fixed with `export PYTHONPATH="$PWD/sim:$PWD:$PYTHONPATH"`; the suite
now runs all 6 cases (5 PASS / 1 FAIL) in 90 s on a freshly rebuilt simv.

### I-5 (FIXED, pre-existing) cocotb failed-import path spins forever (no fail-fast)
Same run: after the traceback the simv never exited (`'NoneType' object has no attribute 'log'`), the
first attempt was killed by an outer 1800 s bound and left remote simv PID 131691 (killed explicitly).
Fixed with per-test/per-module `timeout` bounds + rc=124/137 TIMEOUT classification, so a hung case is
reported as TIMEOUT/FAIL instead of hanging the runner.

### I-6 (FIXED, pre-existing) two months of fiction in the wrapper evidence
`build/evidence/wrap-vec-regression.txt` said `ALL 5 PASS` (module has 6 tests; the 6th FAILS) and
`wrap-regression-summary.txt` hardcoded SFU `1 PASS / 4 FAIL / 5 total`, Vector `5/5`, MXU
`Summary: 5 PASS, 0 FAIL`, while `wv_regression.sh` always exited 0. Now all three suites are derived
through `scripts/parse_cocotb_verdict.sh`; the fresh aggregate reads SFU 6/7, Vector 5/6, MXU 4/6 →
`Overall: FAIL (0/3 suites PASS, 3 not-PASS)`, exit 1.

### I-7 (FIXED, pre-existing) six regenerated artifacts + 5 `.dbg` files were tracked
Every runner rewrote them in place → permanent dirty-worktree noise and stale "evidence". Snapshotted
to `.omo/evidence/task-1-wrp-untrack-snapshot.txt` (167 KB, per-file sha256 + full content), removed
with `git rm --cached`, covered by NAMED `.gitignore` rules (no blanket `*.dbg`). Protected statics
(`wrap-bug005/007-result.txt`, `wv-bug007-*.log.dbg`, `wv_bug005_logs/*.dbg`) stayed tracked (6/6).

### I-8 (RESIDUAL, recorded not fixed) MXU completion IRQ is not drain-gated
The fix gates the *APB* STATUS.DONE readback on `so_fifo_empty && so_state==SO_IDLE`, but `mxu_irq`
still fires the cycle the controller enters `S_DONE`, i.e. before the store-out drain. A naive gate
`(mxu_irq && so_drain_done)` would NOT work and must not be attempted: controller.v default-clears
`irq` every cycle and raises it only in `S_DONE` (`:145` vs `:319`), so it is a ONE-CYCLE PULSE and a
combinational AND with a drain condition that is false at that instant swallows the interrupt forever.
Correct future fix: latch `mxu_irq` into a sticky `mxu_irq_seen`, gate the latch, and add an IRQ_EN=1
TB case (the current suite writes IRQ_EN=0; the watchdog case is satisfied by the sticky wdt bit).
Marked grep-ably in `rtl/wrapper/mxu_soc_wrapper.v` as `WRP1-IRQ-RESIDUAL:`; todo 6 mirrors it in the
bug ledger.

### I-9 (RESIDUAL, recorded not fixed) `so_overflow` tripwire not implemented
`so_fifo_empty` is write-pointer == read-pointer, which is only a valid "all rows landed" test because
depth 64 == MAX_TILE and one command pushes at most 64 rows, so the write pointer cannot lap the read
pointer while per-row drain latency < 64 cycles (measured ≈6 cycles/row in the wrapper TB, ≈11-15 in
FM-SOC). The condition "wr_ptr == rd_ptr while a capture is still in flight" would make that an
explicit check instead of an assumption; recorded in the RTL header as a future item, deliberately NOT
implemented this wave.

### I-10 (OBSERVATION for todo 6) the firmware waits on BUSY, not on STATUS.DONE
`firmware/npu_firmware.c:275-281`'s 256-nop workaround and `npu-regmap.h:268-270` show the firmware
polls BUSY; the practical hidden defect was that STATUS.DONE/APB reported completion before the data
was visible. So WRP-1's fix corrects the *STATUS.DONE/APB contract* (the documented usage flow at
mxu_soc_wrapper.v header step 7), not the firmware's poll loop — the ledger wording for
BUG-MXU-WRP-001 must say that, per the plan.

### I-11 (greened) pre-existing WRP-1 defect: STATUS.DONE before the store-out drain
Witnessed RED at HEAD c9a67d6 (`test_mxu_single_tile_compute`): raw DONE at 2880 ns with only rows 0-10
written (0x00040000..0x00040a00, 60 ns/row → 53 of 64 rows still queued) and
`MISMATCH: 3180/4096 ... first_mismatches=[(11, 0), (11, 2), (11, 3), (11, 4), (11, 5)]` — byte-identical
to `.omo/evidence/task-3-rtl-open-bugs-cleanup.txt:278-291`. After the latch+drain gate: DONE at 6080 ns
(one drain row after the last W burst at 0x00043f00) and `Bit-exact match: 0 mismatches out of 4096
elements`. Fifth failure `test_mxu_accumulate_mode` is untouched (WRP-2, todo 3) and still dies
with the same `ValueError: Unresolvable bit in binary string: 'x'` at 2940 ns — it did not turn into
a DONE timeout, so the gate neither masked nor moved it.

## T3 — BUG-MXU-WRP-002 opened/closed items (commit `7179a82`)

### I-12 (OPEN, residual — historical script) `scripts/p9_fix_branch_b.sh:18` guards the retired wire
Step (a) of that phase-9 one-off is `if ! grep -q 'wrp_k_tiles_derived' "$WRAPPER"; then exit 1` — the
guard for the *old* P9-B fix, which is exactly the derivation this todo removed as the defect's root
cause. Re-running the script now exits 1 at step (a). It is not referenced by any Makefile target or
live regression path (only phase-9 plan/docs), so it is recorded, not edited: the todo's commit is a
3-path pathspec commit and touching this script was out of scope. If anyone re-runs the phase-9 wave,
update the guard to check `wrp_k_tiles_eff` (or delete the historical script).

### I-13 (OPEN, residual — tooling) the tracked `results.xml` is rewritten by every wrapper run
`scripts/wv_run_mxu.sh` runs simv with `cd $REPO_ROOT`, so cocotb writes `results.xml` into the repo
root; `results.xml` is **tracked** (`git ls-files` confirms; committed by 7c54fd3 with an SFU run's
content), so each wrapper run leaves a stray `M results.xml` (seed + testcase list). It was restored
with `git checkout -- results.xml` before the commit. Todo 1's untrack sweep enumerated only the
`wrap-*`/`.dbg` regenerated artifacts, so this one escaped; a future tooling sweep should `git rm
--cached results.xml` (or point cocotb's `COCOTB_RESULTS_FILE` at `build/evidence/`).

### I-14 (RESIDUAL, recorded not fixed) K > 128 per command is still unsupported (X)
The buffers hold exactly 2 K-tiles (`W_BUF_DEPTH=64`, `A_BUF_DEPTH=128`), and the preload write indexes
(`pl_k_tile_cnt * 32/64`) plus the broadcast read indexes (`burst_cnt * 32/64 + …`) go out of range from
tile/burst index 2. The fix therefore bounds its claim to **K ≤ 128** and documents it in the RTL FSM
comment; growing the buffers was explicitly out of scope. No test covers K > 128 (by design); a future
change must deepen the buffers and the indexing together.

### I-15 (FIXED) preload derived its K-tile count from a register written a phase too late
Root cause: the preload FSM computed `ceil(K/64)` from the latched MXU `DIM0` (`:263-264` at HEAD) while
every client writes DIM0 **after** the preload handshake — for K=128 the count read the reset default 64,
one K-tile was fetched, and the second tile's `weight_buf[32..63]`/`activation_buf[64..127]` were never
written → X on `m_axi_wdata` → `ValueError: Unresolvable bit in binary string: 'x'` at 2940 ns
(`axi_slave.py:154`). Fix: the count now comes from `WRP_K_TILES` (0x44) via
`wrp_k_tiles_eff = (wrp_k_tiles == 0) ? 1 : wrp_k_tiles`; the derived wire + the dead `dim0_k` latch are
retired; the TB programs the register before `TRIG_LOAD` and asserts the readback; and
`test_mxu_accumulate_mode` now mechanically asserts the 4-burst preload geometry
(`(0x10000,31),(0x10800,31),(0x20000,63),(0x21000,63)`). Suite 5/6 → **6/6**, 0 X-hits, golden bit-exact.
Evidence `.omo/evidence/task-3-wrp-defects-and-tooling-fixes.txt`; commit `7179a82`.

### Artefacts (T3) — where the RED/GREEN witnesses live
`build/evidence/wv-mxu-*.log` are rewritten by every run (L11), so the pre-fix witness and the
GREEN#1 flake-comparison log were copied to the **gitignored** `build/evidence/task-3-red/`
(same house pattern as todo 2's `build/evidence/task-2-red/`): the RED accumulate log, the RED
`wrap-mxu-regression.txt`, the GREEN#1 accumulate log, pre/post-edit source shas, the RED/GREEN simv
shas, both run stdouts, and a README.txt with provenance. The canonical GREEN#2 logs remain at
`build/evidence/wv-mxu-*.log`. Scratch under `/tmp` was removed after the copy.

## T4 — F2 review nits: new records + hand-offs to todo 6 (commit `3a0e87c`)

### I-16 (OPEN, residual — out of todo 4's pathspec) `docs/bugs/bugs-module-level.md:53-59` keeps BOTH F2-1 defects
The module-level BUG-MXU-WDT-001 entry still says the counter "is cleared by any FSM progress, so a
long but healthy transaction can never accumulate to the threshold" **and** quotes the old trip
condition `` `wdt_fire && pl_axi_wait` / `&& so_axi_wait` ``. Both are now wrong at HEAD:
`rtl/wrapper/mxu_soc_wrapper.v` says PER-PHASE budget and trips on `wdt_trip`. F2-1 cited this file
as a second site. Todo 4's commit is a 4-path pathspec (RTL + TB + `bugs-soc-rtl.md` + evidence) so
this file was deliberately not edited; **todo 6 must mirror the corrected wording here** (it is
already editing ledgers) or the next review will re-file the same MEDIUM.

### I-17 (OPEN, residual — FROZEN this wave) F2-2's conformance-TB half is still mixed-revision
`rtl/tb/apb_conformance_real_tb.sv` `:24` and `:680` carry the same base/HEAD-mixed citations that
were fixed on the doc side. The file is explicitly frozen for this wave (todo 4 Must-NOT), the
`git diff --stat` for it is empty, and the residual is written into todo 4's evidence; **todo 6
mirrors it in the bug ledger**.

### I-18 (OPEN, coverage boundary — recorded, no test) the `wdt_trip` deferral branch is unexercised
No wrapper test completes a handshake on the watchdog's trip cycle, so `wdt_ax_hs` is always 0 in the
suite and the new gate's deferral path is only statically justified (saturation + next-cycle
re-evaluation) plus the unchanged covered path (peak `wdt_cnt` 999009). If a future wave wants
positive coverage: a slave that asserts `rvalid`/`arready` exactly on the trip cycle and then goes
silent — the recovery must land one cycle later, not on top of the accepted beat.

### I-19 (INFO hand-off — F2-6) `docs/soc-rtl-review-remediation-rg-table.md:77-80` quotes pre-branch ledger line numbers
F2-6 recorded this as INFO / no action (frozen T12 grep evidence). Untouched here by design; noted
so the todo-6 ledger sweep has the full F2 list in one place.

### I-13 (re-confirmed, still OPEN) the tracked `results.xml` is rewritten by every wrapper run
Happened again on todo 4's judged run; restored with `git checkout -- results.xml` and kept out of
the commit. Same fix as before: `git rm --cached results.xml` or point cocotb's `COCOTB_RESULTS_FILE`
at `build/evidence/`.

### I-13 (third confirmation, todo 5) same tax, same 3-second fix each time
Tick 3 of the same tax: the todo-5 wrapper run re-dirtied the tracked repo-root `results.xml`, the
extra `M results.xml` line appeared (M-count 8 -> 9), and it was restored with
`git checkout -- results.xml` before the commit. Note the FM-SOC run does NOT touch it (only the
wrapper/p9 cocotb runs whose CWD is `$REPO_ROOT`), so this stays a wrapper-tooling item.

### I-20 (OPEN, tooling — observability) a detached FM-SOC run cannot report its exit status
The todo-5 FM-SOC run was launched as `ssh sz0001 "nohup timeout -k 30 7500 bash
sim/regression/run_fm_soc_all.sh > log 2>&1 &"` so a ~45-min run could not be killed by a client
hiccup; the price is that the script's exit code was never captured. The run itself was provably
complete (33/33 per-case `runner_classification` lines, both post-loop summary blocks, the closing
banner, and process-table NONE_RUNNING afterwards), and `run_ibex_full_rtl.sh`'s own rule
(`exit 1` iff `TOTAL != N_CASES` or `FAIL/TIMEOUT != 0`) yields 0 — but that is derived, not observed.
Suggested fix for the todo-6 tooling sweep: make the runner append an explicit machine-readable
trailer (e.g. `[RUNNER-EXIT] rc=$?`) or `echo $? > "$RUN_DIR/exit_code"` as its last action, so any
detached/CI/redirected launch is self-judging without the derived argument.

## T6 — ledger close-out (commit `ba0bb3c`): which residuals are now recorded WHERE

Todo 6's deliverable was the module-level ledger, so the residuals below are now written into
`docs/bugs/bugs-module-level.md` (the "residuals carried out of the wrapper-fix wave" note) as
**notes, deliberately without new bug IDs**. This section is the notepad-side index; the items keep
their OPEN status until the fixes they describe are actually made.

### I-16 (CLOSED by todo 6) the module ledger no longer repeats the F2-1 defects
The WDT entry's counter rule now reads as a PER-PHASE budget (cleared at PL_READY/SO_IDLE/SO_TRANSFORM,
K-tile turnover inside the pre-load is wait→wait, ~244+ cycles/beat large-K exposure, >10x headroom +
empirical clean runs as the argument that holds) and the trip condition reads
`wdt_trip = wdt_fire && !wdt_ax_hs` with the saturation/1-cycle-deferral note. Probes
`grep 'cleared by any|cannot accumulate|every other state'` and `grep 'wdt_fire && pl_axi_wait'`
both return zero hits (the retraction marker is rephrased so it cannot match either — see L31).

### R1 / I-17 (recorded in the ledger, still OPEN) conformance-TB mixed-revision line refs
`rtl/tb/apb_conformance_real_tb.sv:24`/`:680` remain frozen this wave; the ledger note now names them
next to the doc-side fix in `docs/bugs/bugs-soc-rtl.md`.

### I-13 (recorded in the ledger, still OPEN) tracked `results.xml` re-dirtied per run
Fourth confirmation across todos 2/3/4/5; the ledger note records the restore-each-time practice and
the two candidate real fixes (`git rm --cached results.xml` or `COCOTB_RESULTS_FILE`).

### I-20 (recorded in the ledger, still OPEN) detached FM-SOC run loses the exit status
Ledger note now carries the suggested `[RUNNER-EXIT] rc=$?` trailer so the next runner change has a
written spec.

### I-1 (recorded in the ledger, still OPEN) `p9_ssh` self-loop blocks running the wrapper from sz0001
The ledger note states the workaround (invoke from a host with key access — sz0002 today). Environment
item, not touchable from a repo pathspec.

### INFO — the SFU 6/7 and Vector 5/6 verdicts are the fix working, now on the record
`run_wrapper_sfu` 6/7 and `run_wrapper_vector` 5/6 (the 6th vector case had been silently skipped for
two months and itself fails) are recorded in the ledger as honest verdicts from the fail-closed
parser, not as regressions of WRP-1/WRP-2. If a future wave fixes either suite, retire the note then.



### F2-REJECT-CLOSED (commit ad30841) — all F2 + F1 final-wave findings remediated
F2 returned REJECT on one MEDIUM false ledger claim (+2 LOW +1 NIT); F1 APPROVE with 2 LOW
citations.  Closed in ONE docs/scripts/.gitignore/evidence-only commit — zero RTL/TB change.
- **F2-1 (MEDIUM)**: `docs/bugs/bugs-module-level.md` WRP-001 "Wording correction" rewritten:
  `S_DONE` clears `status_busy` in the same cycle it sets `status_done`
  (`rtl/mxu/controller.v:317-318`, readback `rtl/mxu/mmio_if.v:140`), so pre-fix NEITHER bit
  waited for the drain; post-fix only bit1=DONE is drain-gated while bit0=BUSY still clears on
  row-stream completion — the corrected reason the `npu_firmware.c:275-281` 256-nop stays
  LOAD-BEARING.  `grep 'holds through the drain'` -> rc=1.  `_poll_done` note kept.
- **F2-2 (LOW)**: `scripts/wv_regression.sh:15` rephrased (comment only); acceptance grep
  `SFU_PASS=1|ALL 5 PASS|Summary: 5 PASS` -> rc=1 (0 hits).
- **F2-3 (LOW)**: `.gitignore` — the 7 enumerated rules removed as redundant (check-ignore -v
  proves every probe resolves to the pre-existing `build/evidence/` rule); `wrap-sfu-debug.txt`
  proven static (0 writers at HEAD **and** f1d9652, byte-identical to its pre-untrack blob) ->
  restored to tracked with `git add -f`; `wrap-mxu-regression.RED-pre-RTL.txt` relabeled
  untracked-but-kept witness.
- **F2-4 (NIT)**: `COCOTB_RESOLVE_X` "0 hits" scoped to code paths (0 there; 14 in `.omo` prose).
- **F1-1 (LOW)**: task-5 SKIP causes split — FM-SOC-017/019 = not-applicable-to-Ibex; 014/015/016/
  021/022/023 = superseded by FM-SOC-027/032/10X (`sim/rtl_soc_runner.py:4535`).
- **F1-2 (LOW)**: task-2 RED citation -> verbatim `task-3-rtl-open-bugs-cleanup.txt:76-77`
  (prose restatement at :278-291).
- Wallpaper: `results.xml` was I-13 noise from an F-wave cocotb run -> `git checkout`-restored,
  never staged; M-set 9 -> 8 = {7 protected} ∪ {plan}.  Evidence:
  `.omo/evidence/f2fix-wrp-defects-and-tooling-fixes.txt`.  F3 re-run pending.
