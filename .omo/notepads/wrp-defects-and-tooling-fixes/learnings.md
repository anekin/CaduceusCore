# learnings — wrp-defects-and-tooling-fixes

Plan: `.omo/plans/wrp-defects-and-tooling-fixes.md` · Branch: `wrp-defects-and-tooling-fixes`

## T1 — tooling trustworthiness (a/b/c/d + FM-SOC forced-rebuild baseline), commit `c9a67d6`

### L1. A verdict is a claim about a *log*, never about a runner's exit code or prose
`grep -qE 'TEST.*PASS'` matches the FAILING cocotb summary line `TESTS=1 PASS=0 FAIL=1` (and cocotb
exits 0 even when a test fails — `TEST_EXIT_CODE=0` sits right after a FAIL summary). Constructive,
permanent proof: `sim/regression/fixtures/verdict-fail.log` is a real mxu FAIL log; the legacy pattern
returns rc=0 on it, the helper returns rc=1. Fail-closed rules that actually caught things here:
**exactly one** summary line, `n>0`, `PASS==TESTS`, `FAIL==SKIP==0`, **plus** a per-test cross-check
(count == TESTS, PASS count == PASS). Anything ambiguous (duplicate/absent summary, mismatch, empty
log, missing file, empty argv) exits 1.

### L2. Freshness proofs must be state deltas, not intentions
`csrc`/`simv` mtime + file-set hash + binary sha256 differing from every earlier artifact is the only
evidence that survives review. Concretely: repo-root `csrc` 596 files/hash `4157c01a` → destroyed →
freshly built 38 files/`feffb27b`; `simv_soc_ibex` ABSENT → created (sha `d2e71b22` ≠ `72199d8a`
pre-clean, ≠ `22d5e773` previous build); `simv_apb_conformance_real` (sha `6ffc68ea`) → ABSENT. The
flist tracks FILES, not the RTL inside them, so "reuse if present" is always a stale-state bug.

### L3. Bounding is not optional for simulator runners
A cocotb *import* error inside a VCS+cocotb simv does not fail fast: the GPI event loop spins forever
(`AttributeError: 'NoneType' object has no attribute 'log'`). An outer kill leaves an ORPHANED remote
simv on sz0001 (PID 131691 had to be pkilled). Per-test/per-module `timeout -k 10 <bound>` plus an
explicit rc=124/137 TIMEOUT classification is the fix: the same 6-case suite then finishes in 90 s.

### L4. `PYTHONPATH=$PWD` is not enough for the `tests.wrapper.*` import style
`sim/tests/wrapper/test_{vector,sfu}_wrapper.py` do `from tests.wrapper.wrapper_common import ...`,
which needs `$REPO_ROOT/sim` on `sys.path`; `test_mxu_wrapper.py` does not import it — which is why mxu
"worked" while vector could never re-import its own module in a clean session. The suite only ever
looked green because some session had an ambient `PYTHONPATH=sim` (repo convention). Export both roots.

### L5. Never trust a "green" suite that has not been re-run since RTL/tooling changed
`wrap-vec-regression.txt` claimed `ALL 5 PASS` for two months although the runner could not import the
module at all (L4) and the module has 6 tests — the 6th, `test_bug005_vector_nonaligned_wstrb`, FAILS.
Same class as the SFU `1 PASS / 4 FAIL / 5 total` hardcode. Untrack regenerated artifacts, derive every
count from a parser, and treat "logs found != cases declared" as FAIL, not as PASS-by-default.

### L6. `git check-ignore -v` reports the *directory* rule, not your named rules
For any path under an excluded directory git never evaluates deeper patterns, so named rules inside
`build/evidence/` are declarative intent only; the functional untracking is `git rm --cached`
(gitignore never affects tracked files). Also: snapshotting a deleted tracked file's full content makes
git *display* the pair as a rename (`R090`) — use `--no-renames` for audits; the commit really contains
one add + one delete.

### L7. Makefile recipes that call repo scripts must pin the cwd
`@bash scripts/wv_run_*.sh` with `cwd=sim/regression` can never work → `Error 127` before any VCS work.
`@cd $(REPO_ROOT) && bash scripts/...` (house style) is the fix. Note the p9_ssh helper always targets
`192.168.0.11`, so such targets run from a host that can SSH to sz0001 (sz0002 today) — see issues.md.

### L8. "Forced rebuild" changes the truth only if the truth was stale
The FM-SOC baseline on a from-scratch binary (rm simv + daidir + csrc) reproduced 25 PASS / 8 SKIP /
0 FAIL / 0 TIMEOUT / 33 with the same 8 SKIP IDs as the reused-binary baseline: the earlier number was
not a stale artifact. That is the right way to retire a stale-state doubt — rebuild, then compare.

### L9. A one-cycle FSM "done" state must be latched before it can gate anything
`rtl/mxu/controller.v` sets `status_done`/`irq` *inside* `S_DONE`, and `S_DONE` is entered for exactly
one clock: the next cycle returns to `S_IDLE` unless `cmd_start` arrives in the same cycle. So the
engine's completion is only observable as a one-cycle event; any wrapper-side gate either latches it
(`dbg_state == S_DONE` into `mxu_done_seen`, cleared on the next CMD.START) or loses it. The wrapper
cannot read `mxu_top`'s internal `status_done` wire (mxu_top.v:112, not a port) — `dbg_state` is the
only legitimate source, which is why the fix is a latch and not a re-route.

### L10. Clear qualifiers: `cs` is the *only* gating on an MMIO write port
`rtl/wrapper/apb_to_mmio.v` drives `we/addr/wdata` straight from `pwrite/paddr/pwdata` — raw, ungated
APB signals that are *always* live. Only `cs = psel && penable` marks a real access. Any synchronous
clear/ack built from `mmio_we && mmio_addr==… && mmio_wdata[0]` must therefore add `mmio_cs`, or the
latch is cleared on every idle bus cycle and can never set — the exact failure mode the plan flagged.
Same-cycle priority (clear wins over set) matters for back-to-back commands; the wrapper suite cannot
see it (commands never overlap), so it is documented in the RTL rather than covered.

### L11. Fixed log paths destroy the RED witness — preserve it before the GREEN run
`scripts/wv_run_mxu.sh` writes `build/evidence/wv-mxu-<test>.log` for every run, so `wv_run_mxu.sh`'s
own GREEN run overwrote the RED log minutes after it was produced; the committed
`wrap-mxu-regression.txt` from todo 3 is then the only RED artifact left. Cheap fix that works without
touching the runner or the tree: `git show HEAD:rtl/wrapper/mxu_soc_wrapper.v` into a gitignored
scratch dir, a copy of `rtl/tb/wrapper.flist` with only the wrapper line re-pointed at it, and a
one-off compile to a *distinct* `-o` path — the RED signature is then reproducible on demand and the
GREEN binary at the canonical path stays untouched (see todo 2's section D).

### L12. VCS `simv` exits 0 even when a cocotb test fails
The RED re-capture printed `RED_TEST_EXIT_CODE=0` while its own log said
`TESTS=1 PASS=0 FAIL=1` and `AssertionError: Golden comparison failed: 3180 mismatches`. A
pass/fail judgement read off the process status is meaningless here; the verdict exists only in
the parsed summary + per-test rows (this is why todo 1's fail-closed parser is load-bearing).

## T3 — BUG-MXU-WRP-002 (preload K-tile count), commit `7179a82`

### L13. The defect was a *phase* error, not a *value* error
Every client agreed on the value (ceil(K/64)) and disagreed on **when** it was visible. The firmware
writes `WRP_K_TILES` then `WRP_CMD` (`npu_firmware.c:247-272`); the bridge does the same
(`cocotb_bridge.py:2267`); the TB was the lone outlier that never wrote it and relied on the RTL
deriving the count from MXU `DIM0` — which the same TB writes **after** the preload handshake. So at
preload time the derived count read the reset default 64 for a K=128 problem, one tile landed, and the
second tile's buffer entries were never written (X on `m_axi_wdata`). When auditing protocol code,
check the *ordering* of register programming against the phase that consumes it, not just the value.

### L14. The plan's line numbers go stale as soon as an earlier todo edits the same file
Todo 3's plan text cites `mxu_soc_wrapper.v:238-239/483/509`, but todo 2 had inserted the WRP-1 gating
block, so the same code sat at `:263-264/563/589` in HEAD (and `:277/592/618` after this edit). Re-locate
every plan line reference by grep on the symbol before editing; never trust the number.

### L15. Retiring a wire means retiring its now-dead producers
`wrp_k_tiles_derived` was fed by a `dim0_k` latch whose **only** consumer was that wire
(`grep dim0_k` → decl/latch/comment only). Deleting the wire but keeping the latch would have left a
16-bit register plus a comparator driving nothing. Account for the producer set too, not just the
reference sites (`dim1_n` stayed: it still feeds `wrp_n_derived`).

### L16. Historical one-off fix scripts can carry grep-guards on code a later fix retires
`scripts/p9_fix_branch_b.sh:18` is step (a) of the phase-9 P9-B wave: `grep -q 'wrp_k_tiles_derived'`
or `exit 1`. Retiring the wire silently breaks that guard. It is not a live test (no Makefile target,
no regression path references it; only phase-9 plan/docs do), so the correct handling is an explicit
residual in evidence + issues, **not** an unplanned edit inside a 3-path pathspec commit.

### L17. Sampling a handshake at `RisingEdge` + `prev_valid` dedupe reproduces the RTL's own condition
`m_axi_arvalid` is combinational from the preload state, so it drops on the very edge the handshake
completes. Sampling *after* that edge would miss it. Sampling at the edge and counting the first
interval where `(arvalid && arready)` holds evaluates exactly the condition the FSM evaluates at the
next edge — one count per burst. cocotbext's `AxiRam` holds `arready` high and responds in the same
cycle, so the interval shows `(1,1)` exactly once; the 4-burst assertion passed identically in two
independent runs, which is the test of that reasoning. Cheap, mechanical protocol evidence beats a
"we saw the right data" assertion.

### L18. A suite that `cd`s to `$REPO_ROOT` regenerates the *tracked* `results.xml`
Every wrapper run (on sz0001, cwd = repo root) rewrites `results.xml` at the repo root, so a clean
worktree check suddenly shows an extra `M results.xml` whose diff is the cocotb seed + testcase list.
Restore it (`git checkout -- results.xml`) and record it; todo 1's untrack sweep missed this file
(only the `wrap-*` and `.dbg` artifacts were listed) — see issues I-13.

### L19. Copy the RED witness out *immediately* (L11, made concrete)
`wv_run_mxu.sh` writes `build/evidence/wv-mxu-<test>.log` for every run, so GREEN#1 overwrote the RED
accumulate log minutes after it was produced and GREEN#2 then overwrote GREEN#1's. Copying the log set
to a scratch dir right after each run is what preserved the byte-identical signature comparison
(lines 129/147/279/298 vs `t4-2-*`) and the GREEN#1 vs GREEN#2 flake comparison. Do this before the
next run starts, not after.

### L20. Two independent run artefacts make the freshness argument airtight
The edited TB prints a line the old TB cannot (`WRP_K_TILES=2 programmed + readback OK`) and the edited
RTL produces a burst geometry the old RTL cannot (4 preload bursts vs 2). Together with the runner's
`rm -rf simv+daidir` + checked `COMPILE_EXIT_CODE=0` + post-edit simv mtime, "the judged binary
contains the fix" is provable without trusting the VCS banner alone.

## T4 — F2 review nits (comment wording, penable, recovery gate, probe scope), commit `3a0e87c`

### L21. A watchdog's "no false trip" claim is a statement about the *counter rule*, not the state list
The old text said "every other state is progress and clears it". True for the *list* of states, false
for the *rule*: the pre-load's K-tile turnover is `PL_LOAD_W_R -(rlast, more tiles)-> PL_LOAD_W_AR`,
i.e. wait -> wait, so the counter accumulates from the first `PL_LOAD_W_AR` until `PL_READY` even
with zero stalls. The counter is a PER-PHASE budget (one per pre-load phase, one per store-out row),
not a per-transaction-stretch one. To review such a claim you must trace the transition pairs that
stay *inside* the wait set, not just read the set membership.

### L22. A handshake-qualified trip is a 1-cycle deferral when the counter saturates
`wdt_cnt` saturates at `WDT_TIMEOUT-1` and `wdt_fire` stays high while the FSM is in a wait state, so
gating the trip on `!wdt_ax_hs` can only postpone it to the next stalled cycle — it cannot disarm the
watchdog. That saturation property is what made F2-4's "gate the recovery on no-handshake" cheap and
safe to implement instead of parking it as a residual. Gate all four channel pairs, not only the
AR/AW examples F2 named: an accepted mid-burst R beat would feed a *later, unrelated* read, and a
completed non-final W beat is exactly the "burst without WLAST" case.

### L23. A low-risk behavior-adjacent edit can still be un-testable — say so instead of implying proof
The suite's dead slave ties every ready to 0, so the deferral branch of the new gate never fires in
any test. 6/6 therefore proves *non-regression* (same trip cycle: peak `wdt_cnt` 999009, identical to
the ledger's recorded value) and not the new branch. Recording that boundary explicitly is the honest
form; a future wave can add a "handshake completes on the trip cycle" TB case.

### L24. A grep-based acceptance assertion can over-count: check the neighbours
`grep -c 'wrp_cs && pwrite && penable && (paddr == OFF_WRP_CMD)'` returns **2**, not 1, because the
pre-existing `wrp_trigger` line contains the fixed string as a substring. Verify each count you are
about to write into evidence (this claim was corrected from 1 hit to "2 lines: :255 wrp_trigger +
:586 the fixed clear") — an evidence file with a wrong self-check is worse than one with none.

### L25. "Cite HEAD numbers or drop" is not neutral when the paragraph describes pre-fix code
In `bugs-soc-rtl.md`'s Symptom block, HEAD numbers would point at the *fix* (the read mux now returns
0 where the bug text says it returns the stored value) and the read-clear statement no longer exists
at HEAD at all. The only non-misleading options are to drop the numbers (chosen) or to label the
whole block with its revision; mixing happens precisely because the paragraph outlives the code.

### L26. Every wrapper run re-dirties the tracked `results.xml` (I-13 still open)
Confirmed again this todo: the run rewrote the repo-root `results.xml`, the extra `M` line appeared,
and it was restored with `git checkout -- results.xml` before the commit. Until `results.xml` is
untracked or `COCOTB_RESULTS_FILE` is redirected, every wrapper todo pays this tax.

### L27. The FM-SOC 33-case suite is dominated by ONE case — plan the window around it
Todo 5's FM run: FM-SOC-032 alone consumed 1235 s of the 2620 s case loop (12,084,915.50 ns sim);
the only other slow case was FM-SOC-10X (69.5 s), and everything else was ≤ ~60 s. So the "53-min
baseline" is really "~1 min compile + ~21 min of case 032 + ~22 min of all other cases". Practical
consequence: poll by case-log mtime, not by "has the summary appeared", and before calling a quiet
stretch a hang, check the remote simv's CPU% (here 98.9 %, log growing) — a long single case looks
exactly like a stall if you only read the run log every 8 minutes.

### L28. A detached launch buys robustness and costs you the exit code
`nohup timeout … &` over ssh survives a client/session hiccup, but the script's shell status is then
unobservable (we saw this the hard way: the launching ssh hung because the background process still
held the channel's stdin — `</dev/null` or `ssh -f` avoids that). For a *judged* run, either capture
the status in the same remote command (`… ; echo RC=$? > rc.txt`) or accept the compensating
evidence used here: 33/33 per-case `runner_classification` lines + the runner's own post-loop exit
rule (TOTAL==N_CASES && FAIL==TIMEOUT==0) + a complete post-loop summary. Prefer the captured rc —
this todo had to document the gap instead of quoting a number.

### L29. `soc-verification-run.sh` CLEAN=1 deletes the OTHER target's simv too
The CLEAN block removes simv_soc_cocotb + `.daidir`, simv_apb_conformance_real + `.daidir` AND
`$REPO_ROOT/csrc`. So the conformance binary built and judged in (b) was already gone by the time the
(c1)/(c2) invocations ran — the sha had to be captured immediately after its own run (only a 128-bit
prefix survived). Functionally this is right (every target must rebuild), but for evidence work the
rule is: record each binary's mtime+sha the moment its run finishes; do not plan to read it later.

## T6 — ledger close-out (BUG-MXU-WRP-001/002 filed as Fixed), commit `ba0bb3c`

### L30. Plan-supplied line numbers need the same grep-before-write treatment as RTL ones (L14, mirrored)
The plan's todo 2 cites `rtl/mxu/mxu_top.v:112` for `status_done` and `firmware/npu-regmap.h:268-270`
for `npu_wait_done`; at HEAD these are `:113` and `:269-271` (the plan was written against a slightly
older revision). A ledger is read as ground truth long after the plan is forgotten, so every citation
that goes into it — including ones dictated by the task text — must be re-resolved by content first.
Cheap sweep that caught both: `grep -n 'status_done' rtl/mxu/mxu_top.v` and
`grep -n 'npu_wait_done' firmware/npu-regmap.h`. (`controller.v:205` and `mxu_soc_wrapper.v:415` did
survive verbatim.)

### L31. "Remove the old claim" means removing its grep-able strings, not just its meaning
A retraction that quotes the retracted sentence (`the earlier "cleared by any FSM progress" claim was
wrong`) reads fine to a human but is indistinguishable from a live claim to a grep-based auditor —
and this project's audits are grep-based by design (Acceptance commands are literally greps). After
rewriting the WDT counter rule, the file still matched `grep 'cleared by any|cannot accumulate'`
because the correction parenthetical kept the quote. Rephrasing the retraction as *"the earlier
counter-rule claim — cleared on any FSM progress, therefore unable to accumulate — was wrong about
the transition rule"* keeps the traceability and clears the probe. Rule: when a probe is planned,
write the replacement so the probe's pattern cannot match, even inside meta-text.

### L32. In an append-only ledger, cross-references have a direction — get it right
`docs/bugs/bugs-module-level.md` appends new entries after old ones (Rule 2), so the WDT entry at the
top sits *before* the 2026-09-25 entries. The rewritten 2026-09-23 Note must therefore say "see the
2026-09-25 entries **at the end of this log**", not "above"; a stale "above" would send the next
reader to the wrong place (and was the literal wording the original note would have needed if the
entries had been inserted near it). Check the file order before choosing the direction word.

### L33. Statistics rows and entry counts are two representations of one fact — diff them mechanically
The stats table is hand-maintained; the entries are the data. Writing `| Fixed | 6 |` by hand is a
claim, so the todo added an independent derivation: `grep -c '^\*\*Status\*\*: Fixed'` → 6 and `Open`
→ 0, cross-checked against `| Total bugs | 6 |`. This is the same discipline as L1 (a verdict is a
claim about a log) applied to documentation: never let the summary be the only source.

### L34. Docs-only todos still deserve the full adversarial pass (the two applicable classes were real)
`misleading_success_output` and `dirty_worktree` both applied: the first because the acceptance
"proof" is grep output that a lazy actor could fabricate (so the raw lines + hit counts were quoted
verbatim and an extra inventory cross-check added), the second because the ledger rewrite had to sit
next to an already-dirty worktree (7 protected files + the plan) and one stray `git add -A` would
have committed them. Everything else (stale_state, malformed_input, prompt_injection, cancel_resume,
hung_commands, flaky_tests, repeated_interrupts) is genuinely N/A for a two-file doc edit, and
recording that reasoning is cheaper than a reviewer asking.

### L35. An evidence file that quotes its own `git diff --cached --stat` drifts on the next edit
Practice carried over from todos 1-5: quote the staging check verbatim in the evidence. Here the first
`git diff --cached --stat` (recorded in section G) said `182 ++++++` / `3 files changed, 379
insertions`; appending that very section to the evidence file then grew it to 193 lines, so the
committed artifact reads `193` / `390 insertions` in the commit message while its own section G quotes
the earlier numbers. Both readings are true *at their moment* — this is the evidence-file version of
the commit-hash self-reference problem. Two clean options next time: (a) make the evidence file
immutable after the first `git add -f` and put the staging output only in the (uncommitted) ledger
line, or (b) freeze the final numbers in a post-commit follow-up line without re-quoting the stat.
The 11-line delta is exactly the G section itself, so the artifact is self-explanatory to any reviewer
who diffs it against the commit — but the rule to write down is: **do not quote a stat that your own
edit is about to change.**


## [2026-09-25] F 波收尾 — 跨 todo 的类级经验（供后续计划复用）
- **"完成信号不覆盖数据路径"是一类缺陷**：MXU 的 DONE 与 store-out FIFO drain 无连接 → DONE 可读时 53/64 行仍在队列。修法：latch（用**可见**的粘滞代理，不用内部网）+ 数据路径完成条件（`so_fifo_empty && so_state==SO_IDLE`）∧ 组合；t=0 假阳性用"引擎完成置位 + 下次 start 清除（清除胜出）"化解。
- **单周期状态必须锁存**：`dbg_state==S_DONE` 只亮 1 拍（提前用 `controller.v:76/:316-330` 核实，别信"它保持到下次 start"的推测）；APB 轮询 2-3 周期/次，组合实现必然超时。
- **清位条件必须带 `cs`/`penable` 限定**：`apb_to_mmio` 的 `we/addr/wdata` 是**未门控裸信号**，少了 `mmio_cs` 会在总线空闲时把锁存逐拍清零 → DONE 永不置位（会被误诊成"门控源选错"）。
- **寄存器被驱动方正确编程、FSM 却忽略它**是第二类缺陷：`WRP_K_TILES` 固件/驱动都写、RTL 却从 DIM0 派生（而 DIM0 在 TRIG_LOAD 之后才写）→ 少加载 → X。判据：**驱动与 FSM 引用同一寄存器** + 读回断言。
- **台账里的信号级断言必须逐条对 RTL 复核**：本轮 F2 抓到 "BUSY 持续到 drain 结束" 是假的（`S_DONE` 同拍清 BUSY 置 DONE），结论对、理由错——这类错误只有行级审计能抓。
- **runner 的"发现测试数 vs 列表数"要核对**：vector 列表 5 个、模块实有 6 个 → 静默漏跑（no_silent_skip）。补上后暴露的既有失败是"修复生效"，不是回归。
- **玩具解析器必然 fail-open**：统一抽 fail-closed helper + fixture（必须含真实的 7 测试/1 测试样本），并断言"失败路径无裸 exit 0"。
- **stale 通道不止 simv 一条**：`csrc`（仓库根 + `-Mdir` 的 `build/ibex_full_rtl/csrc`）同样会复用旧编译；"先删再编 + 编后断言二进制存在"。
- **长跑必须留 rc**：`nohup` 起的 FM-SOC 丢了 shell 退出码 → 建议 runner 末尾打 `[RUNNER-EXIT] rc=`。
