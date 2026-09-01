## P0 (2026-08-31)
- User manually deleted the worktree dir; `git worktree prune` left only the main worktree. No agent backup — user action, recorded in evidence.
- Evidence commit a87997b landed on fix/fm-soc-10x-sfu-desc per plan (NOT an ancestor of the task branch); the 8 evidence updates are invisible in the task-branch worktree but preserved in git. Do not delete that branch.
- 3 housekeeping commits: a87997b chore(evidence) / 7f14690 docs(remediation) (11 files) / 9d80c96 chore(gitignore) (5 rules incl. 87M build/ibex_segment_rtl/).
- Task branch soc-rtl-review-remediation @ 9d80c96 == f982bef + 2 commits; porcelain clean except the P0 evidence file itself.

## W1 RED tests (2026-08-31) — 6/6 verified
- todo1 test_timeout_behavior.sh: 4 FAIL RED (124→exit0; SEG_TIMEOUT_S no validation). Verbatim-extraction contract with todo9 fixer (anchors: SEG_TIMEOUT_S region line 59 / `if [ "$RUN_RC" -eq 124 ]` block → EOF).
- todo2 axi_crossbar_fairness_tb.sv P4 + per-transaction watchdog (10k): EDA run reproduced phantom-accept deadlock → `FAIRNESS: FAIL`, master 0 AR accepted @385 never completed. P4(b) checks deferred to todo7 GREEN.
- todo3 apb_conformance_real_tb.sv: VCS 0 errors, 7/7 peripherals, 168 checks / 128 pass / 40 divergences (PCIE 27, DOORBELL 6, INTC 4, MXU/SFU/VECTOR 3; DMA fully conforms). Log: sim/regression/run_apb_real.log.
- todo4 test_regression_stats.sh + docs/fm_soc_case_manifest.csv (33 rows 25/6/2): A1 FAIL (grep `superseded by FM-SOC-032/10X` misses real `FM-SOC-027/032/10X` → 6 superseded counted PASS), A4 FAIL (`|| true` swallows exit 124).
- todo5 test_firmware_addr_allowlist.py: 6 failed/1 passed RED. (a) ROM/hole/MMIO accepted (dram_range_ok only upper-bound); (b) derived-size not validated; (c) FuncModel(ring_size=1024) head 1019 → COMPLETION_STATUS[1019] clobbered INTC.PENDING=0x1 @0x40006000. Near-end runtime control deferred to Spike (RISCVMini lacks sltu/sltiu).
- todo6 test_evidence_provenance.sh: 17 FAIL/2 PASS RED. (a) stale evidence append+exit0; (b) 8 provenance field groups absent; (c) allow_pickle=True @346/412 (canary RCE proven), no commit validation on resume.
- Commits: 4a55494..013dfeb (6 test commits). All AdversarialVerify confirmed.

## W2a GREEN fixes (2026-08-31) — 7/8/9 verified
- todo7 crossbar accept/grant coupling (c478ae5): m_*ready_o = !active && (!hit || (grant_window && would_win)); DECERR exemption + full grant-window gating preserved; FIXED_PRIORITY param (default 0) for mutation. EDA: fairness P4 GREEN (grant 393x7 diff=0, 0 deadlock), mutation RED (2751 vs 0), stress PASS. Waiver docs/waivers/REMEDIATION-RTL-EXCEPTION-2026-08-28.md created before RTL edit. Makefile mutation target landed in todo-9 commit (shared file).
- todo8 firmware (cee6697): dram_range_ok whitelist (SRAM 4MB + DRAM 8MB per WVR-SOC-RTL-002); derived-size validation via umul16+shifts (objdump: 0 sltu/slt in binary); completion mirror min(cmd_id,15), DRAM ring full 1024. Cross-layer: spike_firmware.py clamped-mirror read (DRAM-ring read impossible — MMIO plugin traps only [0x20000000,0x40011FFF]); ring_alignment 3/3 PASS w/ real Spike; RING-WRAP log text updated. test_firmware_boot_sequence out_addr 0x81000000→0x80050000 (fixture adaptation, allowed by new allowlist). pytest 7+9+3 green; make clean all 0 warnings.
- todo9 runners (6230e21): SEG_TIMEOUT_S validation exit 2 before EDA gate; 124/137 → fresh run-keyed evidence + exit nonzero; full_rtl `|| true` removed, rc→TIMEOUT/FAIL mapping, :4279/:4282 unified SKIP greps, four-class summary TOTAL=33; Makefile pipefail via target-scoped SHELL := /bin/bash + PIPESTATUS; scripts/audit_fm_soc_statistics.py vs manifest (rc=1 on stale logs = expected pre-todo13). test_timeout 8/8 GREEN, test_regression_stats A0-A4 GREEN (PASS=25 SKIP=8).
- All three AdversarialVerify confirmed (0.97). Remaining dirty: firmware/build/* binaries (todo 13 chore(firmware)).

## W2b GREEN fixes (2026-08-31) — 10/11/12 verified
- todo10 (f38ae1c): _verify_10X truncation removed; 17/17 ops verified via run_fm_soc_all.sh FM-SOC-10X (per-op [10X-VERIFY] lines; op01 corrupt-expected; causal-regression for downstream ops; len!=17 guard). ROUTING TRAP documented: soc-verification-run.sh run_fm_soc_case cannot exercise this (SSH drops CASE_ID → silent FM-SOC-001; smoke test routes 10X to golden-vector path). Also fixed 8192B TLP read timeout via 256B MRd chunking.
- todo11 (18c8a4a): gen/check_evidence_provenance.py (8 hash classes); both runners integrate (after firmware/simv, before sim); allow_pickle removed at both NPZ sites; resume commit-validates vs HEAD; stale task-14 evidence archived to build/evidence/archive/*-c506f8ec-stale.txt (b0 sample check skips until todo 13 regenerates). test_evidence_provenance 17 PASS/0 FAIL; timeout/stats tests still GREEN.
- todo12 (80f6516): apb_conformance_real_tb.sv oracle rebuilt to real-RTL semantics (RW/MASK/RO/CONST/WO/WOS/FIELD/UNMAP/W1C/DOC-DIV); run_apb_conformance_real target; EDA GREEN 7/7 peripherals, 214 checks, 0 fail, 8 doc-div filed: BUG-RTL-SOC-009 (doorbell ABI window), 010 (PCIe header overstate), 011 (DMA README access classes). pcie_dma_wrapper excluded (AXI master M6, psel_o[7] guard).
- All three AdversarialVerify confirmed (0.9-0.95). Log traps: stale sim/regression/fm_soc_FM-SOC-10X.log (Aug-27 FAIL) vs real build/ibex_full_rtl/evidence/FM-SOC-10X.log; stale run_apb_real.log (RED) vs apb_conformance_real.log (GREEN).

## W3/W4 partial (2026-08-31) — 13 BLOCKED-accounting, 18/20 done
- todo18: BUG-RTL-SOC-007 ledger updated (todo 15 ATTN-WEIGHT-CHAIN 已执行 2026-08-27, 26 命令 cycles>0, op07 cycles=30755 cos=1.0, 链级未复现, 根因仍未知, Status=Open) — 5 处 "pending todo 15" 清零; vplan :165 同步。已提交（8957ff8 chore(docs) via 并行 worker）。
- todo20: docs/soc-rtl-review-remediation-blockers.md 8 类 blocker + §7 P2/P3 10 项映射表（10/10 可追溯）; 已提交（be3bf04）。
- todo13: 功能回归 GREEN（25 执行 PASS, FAIL=0, TIMEOUT=0, 回滚门未触发, commit 4eac85d），但账务门禁 RED：rtl_soc_runner.py:4402 logger.info 判决不进 cocotb 日志 → 8 个设计跳过 case 误计 PASS（audit rc=1, executed=33）。修复中：logger.info→logger.warning + EDA 重跑 8 case + 重审计（后台 bg_3dbfb521）。

## todo 13 closed GREEN (2026-08-31)
- Full 33-case regression ran clean-commit (evidence commit 4eac85d): 25 executed PASS, FAIL=0, TIMEOUT=0, rollback gate NOT triggered (crossbar fix retained).
- Accounting gate initially RED: superseded/N-A verdicts emitted via logger.info (rtl_soc_runner.py:4402) invisible in cocotb 1.9.0 VCS log → 8 designed-skip cases counted PASS (audit executed=33). Fix: logger.info→logger.warning at :4402 (IbexRunner, critical) + :4386 (P4SpikeRunner sibling) — commit 95c306d; 8-case EDA re-run (SKIP=8, messages 1x each) + re-audit rc=0 {25/6/2, mismatches=[]} — commit a1f6ff0.
- Deviation: EDA server git HEAD was 311b046 (sibling chore(omo)) vs local 4eac85d; fix commits landed on top of 311b046.

## todos 14/15 done (2026-08-31)
- todo15: docs/soc-rtl-review-remediation-rg-table.md 6 组 RED→GREEN 双证据配对（timeout/crossbar/APB/stats/firmware/provenance），全部 live-grep 验证；mutation 保持 RED（crossbar fixed-priority max-min=2751 FAIL；provenance forged/truncated REJECTED）。GREEN。
- todo14: F1 17/21 PASS（0-13,18,20）+ 15/16/17/19 IN-FLIGHT；F2 APPROVE 0 blocking 2 non-blocking（固件 weight size 校验用逻辑 K*N/2，tile DMA 按 2048 填充——非 64 倍数维度下有 gap，非阻塞）；F3 真实：pytest 9 collection errors（cocotb/device_protocol 本地缺失）+ 1536 测试 18F/4E/4s + 确定性死锁（timing 独立 774 passed）；Spike Q_proj max_diff=7.64e+02 FAIL 复现（→todo16）；sz0001 run_e2e_blk0 REAL FAIL（op05_attn_score 62/64 mismatch, MXU FSM error=1 → 待基线判定是否 crossbar 回归，回滚门调查中）；run_crossbar_fairness REAL PASS。F4 10/10。结论 APPROVE-WITH-CONCERNS。

## todo 16 closed FIXED (2026-08-31)
- Spike L0 Q_proj max_diff=7.64e+02 ROOT-CAUSED: out_addr=0x81000000 (16MB) outside firmware 8MB allowlist (WVR-SOC-RTL-002) → dram_range_ok rejects → MMUL silently skipped (status=1, doorbell still completes) → host reads zeros → max_diff == max|golden| (7.64e+02). Co-bugs fixed: row-major act vs broadcast tile-major (ISSUE-13B), missing `return ok`. Fix in sim/spike_host.py run_one_op (computed in-window out_addr + _pack_act_tile_major_contig + firmware-order golden). Re-run bit-exact PASS (0.0 diff, 576 tiles real compute). Commit ba15df6.
- blk0 investigation (F3): run_e2e_blk0 op05 attn_score FAIL = PRE-EXISTING (byte-identical failure pre/post crossbar fix) — rollback gate NOT triggered, c478ae5 exonerated. New bug filed: BUG-RTL-SOC-012 (MXU accumulator drain writes only row 0; M=32,N=2,K=128,tiles=2 → words 2-63 zero). Evidence .omo/evidence/task-14-blk0-{investigation.txt,repro.log,baseline.log}.

## todo 17 closed GREEN (2026-08-31)
- checklist: top Performance ✅PASS → ⚠️ PARTIAL + calibration_state=uncalibrated（12 处一致；FAIL/PARTIAL 残留 0）；"33/33" 3 处改为 25+6+2 复审计口径（原文仅作带日期标注的历史引用）。
- vplan: 33/33/33 cases → 25 executed + 6 superseded + 2 N/A（todo 13 证据）；Spike 行 E2E-06/FW-08 标注 todo 16 FIXED；BUG-RTL-SOC-012 加入台账（Total 14/Open 2）。
- CSV: 已跟踪（7f14690 起），内容同步 25+6+2 + Spike FIXED。
- commits: 9eea3ae docs(bugs) BUG-RTL-SOC-012 + blk0 investigation 证据；6f2e5cf docs(signoff)。
- 遗留：bugs-soc-rtl.md 统计表仍 Total 13/Open 1（→14/2 交给 todo 19 收尾）；8 处 "Waived" 位置已盘点（checklist:270、vplan:164/171/181、bugs:78/373/387/410）留给 todo 19。

## todo 19 closed GREEN (2026-08-31) — ALL 21 TODOS COMPLETE
- BUG-RTL-SOC-002 台账 "formally Waived" → Pending（waiver 待用户签署）6 处 + stats（Total 14 / Waived 0 / Pending 1 / Open 2，BUG-RTL-SOC-012 并入 Major/By-Module MXU row）；checklist/vplan 同步；waiver 文件 closure 条件原文保留、签字栏留空（USER 动作不代签）。commit 9cf6f91。
- 验收 grep "formally Waived|is Waived" docs/ + vplan = 0 命中。
- 至此 0-20 全部完成：P0 / W1 六红 / W2 六绿 / W3 全量回归+真实F1-F4+红转绿+Spike修复 / W4 文档口径+BUG-007+blockers+waiver Pending。剩：最终 F1-F4 验收波 + 用户确认 + --no-ff merge main。

## Final wave (2026-08-31)
- F1 VERDICT: APPROVE — 21/21 todos: evidence files exist with verdict markers; commit map verified against actual `git log f982bef..HEAD` (39 commits; a87997b on sibling branch per P0 housekeeping, documented in task-0 evidence); spot-greps (todo7 P4 GREEN + mutation RED, todo13 25/6/2 mismatches=[], todo16 Spike 1 PASS/0 FAIL, todo19 "formally Waived"=0 hits) all pass; rg-table 6 rows with paired RED+GREEN; plan checkboxes 0-20 all [x]; branch==plan name; HEAD descends from f982bef.
- F2 VERDICT: APPROVE — crossbar would_win no comb loop (regs+inputs only), DECERR `!hit` intact, grant_window triple-gated, FIXED_PRIORITY=0; firmware SRAM 4MB+DRAM 8MB allowlist, umul16 checked arithmetic, completion clamp min(cmd_id,15); tile-padding gap confirmed recorded in task-14 evidence (not fixed, per disposition); runners fail-closed (SEG_TIMEOUT_S exit 2, 124→non-zero, 4-class accounting, pipefail); soc-verification-run.sh ZERO diff; provenance sha256 spot-verified vs sha256sum (flist exact match; driver matches run-time commits 4eac85d/311b046); spike_host out_addr in-window + _pack_act_tile_major_contig + return ok; APB TB 8 doc-div filed as BUG-009/010/011, not silently passed. Zero blocking concerns.
- F3 VERDICT: APPROVE — live executions: 3 behavior scripts GREEN (8/8, 25+8, 17/17); firmware pytest 16 passed; Spike mmul_smoke L0 Q_proj PASS (1 PASS/0 FAIL, exit 0); EDA run_crossbar_fairness "[soc-run] RESULT: PASS" (23 passed/0 failed); timing suite 774 passed standalone; sim/tests per-file: 1430 passed, all 6 failing files classified pre-existing/environment (verified identical at f982bef for engines/runtime_real/dut_adapter via temp worktree), 9 collection errors = caduceus_device_protocol env limitation, spike ladder test passes in 268.87s (long-running, not hang). Zero plan regressions.
- F4 VERDICT: APPROVE — zero diffs on frozen spec / vendored IP / engine internals; rtl-perf-decomposition-calibration.md added-only (7f14690); main==f982bef with 0 plan commits; origin/main only external bot daily-syncs; stash@{0} WIP on main preserved; P2/P3 tracking-only (blockers doc); only waiver-allowed RTL files changed (axi_crossbar.v + tb/).
- OVERALL VERDICT: APPROVE. Evidence: .omo/evidence/final-wave-f1f4-soc-rtl-review-remediation.txt. No git commit made by final wave.
