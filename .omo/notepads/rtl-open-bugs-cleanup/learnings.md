# learnings

## [2026-09-23] session start

## [2026-09-23] todo 1 — BUG-RTL-SOC-010 doc-side (commit 2e468ac)
- **Scope authority beats bug severity.** BUG-RTL-SOC-010's symptom ("CTRL[3]=enable not
  implemented") invites an RTL fix, but `docs/waivers/REMEDIATION-RTL-EXCEPTION-2026-08-28.md`
  authorizes product-code edits for EXACTLY two files: `rtl/soc/axi_crossbar.v` +
  `firmware/npu_firmware.c` (F4 gate: "除上表两文件外，不得有其他 rtl/ / firmware/ 产品代码改动").
  So the in-authority fix is aligning the header with the RTL; TB/runner/evidence/docs are
  explicitly outside that restriction.
- **CTRL[3]=enable is not implementable without touching vendored IP.**
  `rtl/ip/verilog-pcie/pcie_axi_master.v` has NO top-level `enable` port (:58-130 — its
  Configuration group is only `completer_id` + `max_payload_size`); the sole `enable` token
  in that file is the internal hard-tie `.enable(1'b1)` at :217. Wiring CTRL[3] would require
  editing vendored IP — forbidden by rtl/AGENTS.md.
- **A DOC-DIV flag is a *bucket*, not a check.** Both dispatch sites are
  `if (REG_DOCDIV[s][r]) check_docdiv(...) else check(...)`, and each increments `test_num`
  exactly once. Retiring a flag therefore re-buckets a check without changing Total checks.
  Measured: 263 checks before AND after; only `doc_div_cnt` moved 4 -> 2.
  (Prediction written down before the run; confirmed.)
- **Always run a pre-edit control run.** A baseline run on the unmodified tree (GREEN,
  doc_div=4) proved the harness + sz0001 environment were healthy, so the post-edit result is
  attributable to the change. It also showed the gate grep returning 2 hits pre-edit, proving
  the pattern discriminates — without that, a post-edit 0 would be indistinguishable from a
  broken regex.
- **Two independent fresh-binary proofs (stale_state).** (1) Runner's "removing stale simv to
  force full rebuild" + compile-log mtime + `CPU time: 16.259 seconds to compile`.
  (2) A *structural* proof: the sim's own `$finish` report line moved 1268 -> 1280, and +12 is
  exactly this commit's net line delta (header +2, bug-list +7, REG_DOCDIV annotation +1,
  doc_bug mux +2). A stale simv would still print 1268. Prefer a source-derived invariant over
  mtimes alone.
- **`doc_bug(s)` mux arms are only safe to delete if no UNCONDITIONAL call path reaches them.**
  Removing the `4:` arm is safe because PCIE's REG_ACC row (:671) contains no ACC_WOS and no
  ACC_DOCDIVR — the only two arms that call `check_docdiv(..., doc_bug(s))` unconditionally
  (:1120/:1125 and :1154/:1159). Had PCIE had a WOS row, deleting the arm would have silently
  emitted `BUG-RTL-SOC-???`. Check for unconditional call paths before deleting a mux arm.
- **Header comment tables carry a column contract.** The pcie_ep_wrapper register map aligns
  its Description column at column 38 (both the `0x00` RW/9-char-name and `0x18` RO/14-char-name
  rows) — edits must preserve that alignment.
- **Gate-grep hardening is cheap.** Beyond the plan's case-sensitive
  `grep -E '\[DOC-DIV\].*BUG-RTL-SOC-010'` (0 hits), also ran a case-INSENSITIVE variant (also 0)
  and counted the check_docdiv emission prefix `filed as BUG-RTL-SOC-010` -> 0, versus
  `filed as BUG-RTL-SOC-011` -> 2. The non-zero 011 count proves the probe would have caught a
  real emission.

## [2026-09-23] todo 2 — BUG-RTL-SOC-011 RTL side (commit 9b43075)

- **The runner's `rm -rf` does NOT cover the APB conformance simv — and the
  Makefile does not track the RTL inside the flists. This is a stale-state trap.**
  `soc-verification-run.sh` removes `simv_soc_cocotb*`, but this target builds
  `simv_apb_conformance_real`. Worse, the rule is
  `$(APB_REAL_SIMV): $(APB_REAL_SRC) $(SOC_FLIST) $(IBEX_FLIST) $(AXI_FLIST) $(PCIE_FLIST)`
  — the prerequisites are the FLIST FILES, not the RTL files listed inside them.
  Consequence: editing `rtl/ip/dma_wrapper.v` alone triggers NO rebuild (proven:
  the pre-edit control run at 19:15:33 reused todo 1's 19:00:48 binary, same
  mtimes). The plan's inherited note "the conformance target always full-rebuilds"
  is WRONG for this target — it only holds when the TB itself changed (the TB *is*
  a direct prerequisite, which is why todo 1 never noticed).
  **Rule for every future todo on this target: `rm -f sim/regression/simv_apb_conformance_real`
  before any judged run**, then prove freshness twice (compile CPU time + a
  source-derived invariant).

- **`$finish` line delta is a reliable source-derived freshness invariant.**
  Predicted before the run: TB net line delta `git diff --numstat` = +21/-10 = +11
  => `$finish` must move 1280 -> 1291. Measured 1291 on the first try (and again
  on the re-capture). A stale simv would still print 1280. Cheap and decisive.

- **How to re-capture a RED log that `tee` overwrote, without risking the fix.**
  `cp <file> <scratch>/fixed` -> `git diff -- <file> > <scratch>/patch` ->
  `git checkout -- <file>` -> prove revert (`sha256sum` == `git show HEAD:<file> | sha256sum`)
  -> run -> `cp <scratch>/fixed <file>` -> prove restore (same hash) AND prove
  the patch is byte-identical (`git diff | diff - <scratch>/patch`).
  With hash equality the earlier GREEN result stays valid; re-running GREEN once
  more leaves the on-disk simv paired with the surviving GREEN log.

- **A DOC-DIV flag retirement is a re-bucket, confirmed again.** ACC_WOS and
  ACC_WO each emit exactly 2 checks (store-probe + zero-probe), so
  `ACC_WOS -> ACC_WO` moved 2 checks out of the doc-div bucket with Total checks
  unchanged at 263 and DMA's per-slave count unchanged at 43. Only `doc_div_cnt`
  (2 -> 0) and `slv_docdivs[3]` (2 -> 0) moved.

- **Diffing the RED log against the GREEN log is the cheapest observability
  probe.** The two runs differ in exactly one check (`wo-store DMA +0x004`), which
  proves (a) the RED flip bit the intended line and (b) the STATUS read-clear
  removal changed nothing observable. Same-run-pair diffing beats reasoning.

- **STATUS read-clear is structurally unobservable in this TB.** The clear can
  only matter when DONE==1, and DONE is only set by a completed transfer. This TB
  never launches one (its own Phase 5: "no CMD.START was ever written"; the DMA
  CMD probes are 0x42 pre-edit / 0x80 post-edit, bit0 never set). ACC_RO compares
  read-vs-read, and 0 == 0 either way. Recorded as a coverage note (never a silent
  skip) — todo 4's FM-SOC firmware path is the end-to-end guard for this half.

- **Mux-arm deletion: re-run the unconditional-call-path check every time.**
  `doc_bug(s)` is reached unconditionally only from ACC_WOS / ACC_DOCDIVR. After
  the DMA flip, `grep -c 'ACC_WOS\|ACC_DOCDIVR'` over all REG_ACC rows == 0 and
  no REG_DOCDIV flag is non-zero => `doc_bug(3)` is unreachable => deleting the
  `3:` arm cannot emit `BUG-RTL-SOC-???`. Deleting it *before* the flip would have
  been unsafe.

- **"These two docs are the ONLY docs to touch" constrains FILES, not LINES.**
  The plan named the STATUS row; the same file carried three more now-FALSE
  statements (`:310` "STATUS.DONE clears on read", `:357` "this auto-clears DMA
  DONE", `:390` §7.2 "Clears on read of STATUS") plus the TB header's "CMD STORES
  the written value and reads it back". All were corrected and disclosed — leaving
  them would have re-created the exact doc-vs-RTL divergence this plan removes.

- **Four-way convergence is the acceptance frame for this bug class.** After the
  fix: ABI `"access": "wo"` (spec/npu_abi.json, 4 engines) == Func Model
  `access="w"` (sim/models/apb_peripheral.py:288, read-only corroboration) == RTL
  write-only-on-bus == docs W + no-read-clear. The internal `dma_reg[1]` copy is
  an implementation detail the ABI never described.

## [2026-09-23] todo 3 — BUG-MXU-WDT-001 wrapper AXI watchdog (commit dd08a19)

- **The runner's VERDICT is a false-positive detector. Read the cocotb summary
  lines, never the runner.** `scripts/wv_run_mxu.sh:96-103` does
  `if grep -qE 'TEST.*PASS' "$TEST_LOG"; then RESULT="PASS"` — and cocotb's own
  summary line for a FAILING test is
  `** TESTS=1 PASS=0 FAIL=1 SKIP=0 ...`, which MATCHES `TEST.*PASS`
  (measured: `grep -cE 'TEST.*PASS'` == 1 on the failing test's log). So the
  PASS branch always wins. Consequence: on all three judged runs (control, RED,
  GREEN) the runner printed "All 6 tests PASSED" and wrote
  `Summary: 6 PASS, 0 FAIL` into `build/evidence/wrap-mxu-regression.txt`, while
  the RED run's cocotb log says the new test FAILED. Authoritative extraction
  used here: `grep -E '^\s+\*\* sim\.tests\.wrapper'` on each per-test log.
  Todo 2's stale-BINARY lesson generalizes: the verdict EXTRACTION can lie too.

- **A recorded green baseline is not a baseline.** `wrap-mxu-regression.txt` at
  HEAD says "5 PASS, 0 FAIL" (2026-07-23) and `docs/bugs/bugs-soc-rtl.md:624`
  cites it. The CONTROL run on the UNMODIFIED tree proved the truth:
  **1 PASS / 4 FAIL**, all four with
  `AttributeError: 'ApbMaster' object has no attribute '_bus'` at 160-180 ns.
  The false green came from the grep above. Always run the control at HEAD and
  read its per-test lines before believing any recorded count.

- **A guard suite that crashes at 160 ns guards nothing.** 4 of the 5 MXU
  wrapper tests died in `wrapper_common.wait_done()`'s `apb._bus.clk` fallback
  (cocotbext-axi 0.1.28's ApbMaster has no `_bus`). Both sibling suites
  (`test_sfu_wrapper.py`, `test_vector_wrapper.py`) ALWAYS pass `clk=`; only the
  MXU file omitted it, at 3 of 6 call sites (the file's own test 6 already used
  `clk=dut.clk`). Repairing those 3 sites is what let the tests reach their
  comparisons — and immediately exposed two deeper pre-existing failures that had
  been invisible. **Fixing a harness crash is a prerequisite for having a guard,
  not scope creep** (still: disclose it loudly, it was 3 lines in a whitelisted
  file).

- **cocotb 1.9's `ClockCycles(N)` is a Python loop over N edge triggers**
  (`triggers.py`: `for _ in range(self.num_cycles): await trigger`), so a
  1M-cycle wait is ~1M Python resumptions — but it is still affordable:
  measured **13.1 / 13.9 / 13.7 s** for 1,010,000 cycles (~75k cycles/s) on the
  MXU wrapper TB, far under the 10-minute bound. What made it fast: **not
  starting a cocotb `Clock`**. The TB already drives `clk` with
  `always #5 clk = ~clk`, so a cocotb Clock is a redundant second driver adding
  ~2 Python wake-ups per cycle. Assert the period instead (see next bullet).

- **A phase-offset trap in clock sanity checks.** My first RED run failed on my
  OWN assert: `ClockCycles(clk, 10)` measured 95 ns, not 100 ns, because the test
  starts at t=0 while the first rising edge is at t=5 ns. Fix: compare two
  consecutive 10-cycle windows (edge-to-edge), which is exactly 100 ns. A sanity
  assert that is itself wrong is worse than none — it produced a RED for the
  wrong reason and cost a full re-run.

- **Put the pre-flight assertions INSIDE the long test.** Asserting
  `pl_state == PL_LOAD_W_AR`, `m_axi_arvalid == 1`, `arready == 0` and logging
  `irq` BEFORE the 1M-cycle wait made the RED run self-validating: it proved the
  VPI hierarchy probe resolves (`dut.u_dut.pl_state`), that the dead slave really
  holds the handshakes low, and that the DUT is genuinely parked — so the
  semantic failure at the end (`WRP_STATUS[1] == 0`) was unambiguous rather than
  possibly a harness artifact. It also validated the probe mechanism in RED, so
  GREEN could not surprise me.

- **Surgical FSM recovery, not a blanket reset.** "On trip, force the wrapper FSM
  to IDLE" done naively (`else if (wdt_fire) pl_state <= PL_IDLE;`) would also
  clobber `PL_READY` and clear a LEGITIMATELY set `LOAD_DONE` whenever only the
  store-out FSM is stuck. Gate it on the FSM actually being in a waiting state:
  `else if (wdt_fire && pl_axi_wait)`. Same for SO. Zero effect when no trip.

- **Verilog declaration-order discipline beats a forward reference.** `pl_axi_wait`
  could be defined before the PL FSM (only references `pl_state`), but
  `so_axi_wait` references `so_state`, declared 250 lines later. Rather than
  forward-reference it, the wire is DECLARED in the watchdog section and ASSIGNED
  next to the store-out FSM, with a comment in both places explaining the split.
  (Also verified: begin/end 66/66, case/endcase 3/3, one driver each for
  `irq`/`mxu_irq`/`wrp_wdt_timeout`.)

- **Root-cause a data mismatch by counting the slave's own log events.** The
  pre-existing `test_mxu_single_tile_compute` failure ("3180/4096 elements
  differ, first_mismatches=[(11,0),...]") was proven to be a store-out drain lag
  by reading AxiRam's write bursts: the last burst before `STATUS.DONE` is
  `awaddr 0x00040a00` = row 10, and the first mismatch is row 11. Counting log
  events beat reasoning about FIFO depths and drain rates.

- **Tracked artifacts that the runner rewrites.** `build/evidence/wrap-mxu-regression.txt`
  (tracked) and `results.xml` (tracked, written by cocotb into the repo root) are
  both modified by every `wv_run_mxu.sh` invocation. Restore them with
  `git checkout --` before committing, or the worktree M-set grows beyond
  {7 protected} U {plan} and the next todo's precheck trips.

- **The mandated runner command is broken, and the whitelist forbade fixing it.**
  `bash sim/regression/soc-verification-run.sh run_wrapper_mxu` ->
  `bash: scripts/wv_run_mxu.sh: No such file or directory` / make Error 127,
  because `sim/regression/Makefile:1313-1315` omits the `cd $(REPO_ROOT) &&`
  prefix every other recipe has while the runner cds into `sim/regression` first.
  Affects all three wrapper targets. The sanctioned equivalent (and the
  historical invocation per `docs/bugs/bugs-soc-rtl.md:624` and
  `scripts/wv_regression.sh:65`) is the script itself:
  `bash scripts/wv_run_mxu.sh` — it routes ALL compile+simulate work to sz0001
  through `scripts/p9_lib/p9_sz0001.sh`'s `p9_ssh`, so nothing ran locally.

- **Raw RED logs: preserve them, the runner overwrites its own.** Every
  `wv_run_mxu.sh` run rewrites `build/evidence/wv-mxu-<test>.log`, so the
  pre-RTL (RED) logs are gone the moment the GREEN run starts. The RED raw logs
  for this todo were copied to `build/evidence/wv-mxu-<test>.RED-pre-RTL.log`
  (+ `wrap-mxu-regression.RED-pre-RTL.txt`). That directory is gitignored, so the
  copies are invisible to `git status` and to any name-only scope gate.
  To re-capture RED from scratch (deterministic, ~3 min):
    `cp rtl/wrapper/mxu_soc_wrapper.v /tmp/wdt-fixed.v`
    `git checkout HEAD~1 -- rtl/wrapper/mxu_soc_wrapper.v`
    `bash scripts/wv_run_mxu.sh`            # expect: new test FAIL at WRP_STATUS[1]==0
    `cp /tmp/wdt-fixed.v rtl/wrapper/mxu_soc_wrapper.v`
    `bash scripts/wv_run_mxu.sh`            # expect: new test PASS

- **Grep-guard your OWN evidence file before committing.** The first version of
  `.omo/evidence/task-3-rtl-open-bugs-cleanup.txt` explained that the plan's
  required all-six-green judgment line was NOT emitted — and in doing so it
  contained that line's literal spelling. A gate doing
  `grep -n "<that line>" <evidence>` would have matched it and accepted a FALSE
  PASS. Caught by my own guard check, reworded so the literal is absent, and the
  commit was amended (local-only branch, message and 4-path set unchanged:
  dd08a19 -> 73a59d6). **Rule: when a gate is NOT met, do not quote the
  pass token anywhere in the evidence — not even to deny it.**

## [2026-09-23] todo 4 — sz0001 serial regression (commit 46b17cf)

- **The stale-binary risk is real and was measured, not assumed.** STEP 0's inventory
  found `build/ibex_full_rtl/simv_soc_ibex` at 2026-09-22 16:21 (sha 72199d8a…) —
  that is ~30 h old, i.e. OLDER than todos 1/2/3's RTL changes — while
  `sim/regression/simv_apb_conformance_real` was todo 2's binary and
  `build/evidence/simv_tb_mxu_wrapper` was todo 3's GREEN2 binary. Had the run not
  force-cleaned, the FM-SOC 33 would have tested pre-todo RTL and the whole
  regression would have been false evidence. **Always inventory mtime+sha256 BEFORE
  deleting, so the "was it stale?" question is answerable afterwards.**

- **Four targets, four different binaries — one `rm -rf` list must cover all four.**
  `run_apb_conformance_real` → `sim/regression/simv_apb_conformance_real`;
  `run_wrapper_mxu` → `build/evidence/simv_tb_mxu_wrapper`;
  `run_e2e_mxu_*` → `sim/regression/simv_soc_cocotb` (+ `sim/regression/csrc`);
  `run_fm_soc_all` → `build/ibex_full_rtl/simv_soc_ibex` (+ `build/ibex_full_rtl/csrc`,
  because the script passes `-Mdir=$BUILD_DIR/csrc`). soc-verification-run.sh only
  knows about the third one.

- **Source-derived freshness invariants beat mtimes.** Three cheap ones that a stale
  binary cannot fake: (1) the conformance sim prints
  `$finish called from file "rtl/tb/apb_conformance_real_tb.sv", line 1291.` and 1291
  IS the current TB's `$finish` line (the same report read 1268 → 1280 → 1291 across
  the three TB revisions); (2) the wrapper logs `peak wdt_cnt = 999009`, a signal that
  only exists in the post-todo-3 RTL; (3) `run_ibex_full_rtl.sh` writes a hash-bound
  provenance file (simv/flist/driver/firmware/golden/checkpoint sha256 + git commit)
  and PREPENDS it to all 33 case logs — so each FM-SOC verdict is bound to its binary
  with no extra work. Prefer these over mtime alone.

- **ANSI escape codes silently break per-test greps.** `wv_run_mxu.sh` exports
  `COCOTB_ANSI_OUTPUT=1`, so the summary line is literally
  `** ...test_x  ^[[32m PASS ^[[49m^[[39m ...` — a regex like
  `\.test_x\s+(PASS|FAIL)` returns ZERO hits and every test looks like "NO-LINE".
  Strip first: `sed -e 's/\x1b\[[0-9;]*m//g' log | grep -aE '^ *\*\* sim\.tests'`.
  (The `-a` matters too — these logs contain bytes grep may consider binary.)

- **The wrapper runner's false-positive detector also corrupts the EXIT CODE.**
  Because its first pattern `grep -qE 'TEST.*PASS'` matches a FAILING test's own
  `** TESTS=1 PASS=0 FAIL=1 SKIP=0 **` line, `FAIL_COUNT` never increments, so the
  script prints "All 6 tests PASSED" AND exits 0 while two tests really failed. Exit
  status is therefore not a usable verdict for that runner either — parse the per-test
  lines. This is one notch worse than todo 3 recorded (it is not just a log message).

- **`wv_run_mxu.sh` cannot be driven from sz0001 itself.** Its `p9_ssh` helper runs
  `ssh zhengs@192.168.0.11`, and sz0001→sz0001 SSH answers
  "Permission denied (publickey,password)". Run it from a dev host (sz0002) as a
  sequencer; the compile+simulate still lands on sz0001. `soc-verification-run.sh`
  self-forwards, `run_fm_soc_all.sh` does NOT (source `run_env.sh` needs /NAS, which
  sz0002 does not mount) — so FM-SOC must be invoked as
  `ssh sz0001 "cd $REPO && bash sim/regression/run_fm_soc_all.sh"`.

- **Per-slave tables are not the whole check count — reconcile them against the
  source.** The conformance per-slave rows sum to 253 while Total checks = 263. The
  missing 10 are the checks tagged `slv = -1` (global/edge checks deliberately kept
  out of `slv_checks[]`): `grep -nE ',\s*-1\s*,' rtl/tb/apb_conformance_real_tb.sv`
  returns exactly 10 (Phase-2b 0x50/0x54 window edges, Phase-3 pslverr checks, the
  end-of-test irq check). Doing this arithmetic turns "the table looks right" into a
  real consistency proof.

- **Wall-clock budget for this regression** (useful for scheduling future todos):
  conformance 307 s, wrapper 164 s, e2e mxu single 311 s, e2e mxu multi 313 s,
  FM-SOC 33 = 3276 s (54.6 min) — of which FM-SOC-032 alone is ~30 min and every
  other case is ~1 min. Each simv compile is only ~30 s CPU but ~2-3 min wall
  (52-module elaboration). Total serial ≈ 1 h 13 min.

- **Tracked artifacts the wrapper runner rewrites (both must be restored before
  committing):** `build/evidence/wrap-mxu-regression.txt` and `results.xml` (cocotb
  writes it into the repo root because the runner `cd`s to REPO_ROOT). Restoring them
  keeps the dirty M-set at exactly {7 protected} ∪ {plan}.

## [2026-09-23] todo 5 — ledger wrap-up (commit 1af5c70)

- **A stale count hides in more than the row you remember.** The task named "two
  README snapshot rows", but `grep -n "3 Fixed / 1 Open" README.md` returned **2**
  sites: `:26` (the snapshot row) **and `:39`** (the 关键文件索引 row for
  `docs/bugs/bugs-module-level.md`, which repeated the same count string). Fixing
  only the snapshot row would have left the zero-hit probe failing. **Enumerate the
  stale token with grep first; never trust a remembered row list.** Same for the
  SoC ledger: 3 stale sites (`:440`, `:443`, README `:25`), found by grep, not by
  reading the table I already "knew".

- **Plans can contradict themselves; the marked-frozen rule wins.** The plan's own
  summary line 29 says the By-Severity rows should have their **member lists**
  updated (010/011 removed) while the **count stays 2** — which is internally
  inconsistent (count would no longer match the list). The todo-5 detail line
  marks the opposite rule as **定死 (frozen)**: severity counts ALL bugs regardless
  of status → **zero change** to that table. Followed the frozen detail rule, and
  recorded the contradiction in the evidence. Rule of thumb: when a plan's
  high-level summary and its per-todo "frozen rule" disagree, the per-todo rule
  governs — but say so explicitly, because a reviewer reading the summary will
  expect the other thing.

- **Branch-level scope ≠ this-todo scope, and the acceptance line conflated them.**
  Todo 5's acceptance says `git diff main..HEAD --name-only -- docs/ README.md` ⊆
  {4 files}, but `main..HEAD` also contains todo 2's `docs/func-model-mmio-spec.md`
  — which the plan's **F4 whitelist explicitly allows** (plan line 180). The
  literal assertion is unsatisfiable without reverting approved work. Resolution:
  measure **both** (branch-level set AND this todo's own delta) and record the
  discrepancy instead of claiming a pass. Docs-only todos should have their scope
  gate phrased as `git diff HEAD --name-only` (the todo's delta), with the
  branch-level gate left to F4.

- **Docs-only todos have no RED/GREEN — the acceptance greps ARE the test, so run
  them twice.** Once to record the raw output in the evidence, once after the
  evidence is written as a **freshness self-check** that re-derives the same hit
  counts (G1=1 G2=1 G3=1 G4=1 G5=2 G6=0 G7=0/0). Cheap, and it converts "I ran it
  earlier" into "the quoted block is provably not stale".

- **Recompute the table arithmetic from the entries; it is a 20-line python job.**
  Parsing the By-Status / By-Severity / Quality-Metrics rows and asserting
  `sum(By-Status) == sum(By-Severity) == Total` (17) plus `15/17 == 88.2%` and
  `0/17 == 0.0%` is what makes "the table looks right" auditable. It also proves
  the Minor row still lists 010/011 (the zero-change rule) rather than assuming it.

- **Do not over-sanitize the evidence file.** Quoting old→new values
  (`"13 (76.5%)" -> "15 (88.2%)"`) creates strings that a *repo-wide* broad grep
  would hit — but prior evidence in this repo already does exactly that
  (`.omo/evidence/task-7-bug-009-doorbell-fix.txt:79` quotes the old row verbatim),
  and the gate is **path-scoped** (`docs/bugs/... README.md`). Match the repo
  precedent; scope the gate, don't censor the evidence.

- **A "Fixed" ledger entry with a "Status: Open" standalone file is the exact bug
  class this plan closes.** `docs/bugs/BUG-MXU-WDT-001.md` is one of the 4
  whitelisted files but the plan only named its `:4` severity line. Syncing its
  `Status: Open` too (with a pointer to the authoritative entry) was a deliberate,
  disclosed extra: leaving it would have re-created a doc-vs-reality divergence —
  i.e. BUG-RTL-SOC-010/011 all over again — inside the commit that closes those
  very bugs.

- **Where the residuals went (and why they are not bug entries).** The two defects
  found by todo 3/4 (store-out drain vs `STATUS.DONE`; `m_axi_wdata` X in
  accumulate mode) plus the three process-debt items are recorded as a **Note**
  inside the WDT entry, explicitly NOT as new bug IDs, so the module-level
  statistics stay at the arithmetic truth (4 Fixed / 0 Open). Rationale: filing
  them would have changed the numbers the plan fixed in advance (4F/0O), and this
  todo is documentation-only. They are flagged for a follow-up plan with full
  detail in `problems.md`.
