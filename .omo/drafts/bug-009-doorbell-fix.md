---
slug: bug-009-doorbell-fix
status: execution-started
intent: clear
review_required: true
pending-action: execute todo 0-7 + F1-F4 (worker dispatched), then F-wave, then user merge decision
approach: Plan A (user-decided) — expand the RTL doorbell window (LAST_STATUS@0x10 + COMPLETION_STATUS[16]@0x14-0x50); module-level tb_doorbell harness (FSDB dump + run_doorbell_tb) for xverif pre/post; flip tb_doorbell Test 12 + add Test 13/14; flip conformance TB doorbell rows (20 slots, REG_CNT 6→20, explicit 0x50/0x54 at slv=-1); mandatory simv_soc_ibex rebuild before FM-SOC/W4; spec KNOWN_DISCREPANCY resolution + generator literal rewrite + bounded STRUCT-FIELDS gate; ledger BUG-009 → Fixed (13 Fixed / 2 Open).
---

# Draft: bug-009-doorbell-fix

## REVIEW OUTCOME / EXECUTION DECISION (2026-09-22)
Five dual-review rounds were run. **Both reviewers independently verified the plan's engineering substance as correct** (Plan A, the `word_idx` re-key, the 21-word window, the 20-slot conformance arithmetic, `doc_div_cnt==4`, per-slave `checks==60`, `REG_CNT[5]==20`, all ledger row numbers, the pytest/W4 baselines). The review's highest-value catches:
1. **False-green BLOCKER**: `run_ibex_full_rtl.sh:49` / `run_w4_perf_batch.sh:25` reuse an existing `simv_soc_ibex` → FM-SOC-33 and the W4 zero-drift gate would have run **pre-fix RTL** (headline regression = false evidence). Now: mandatory `rm -rf build/ibex_full_rtl/simv_soc_ibex{,.daidir} csrc` + provenance must show a simv newer than the R1 commit.
2. **False-green channel**: `--check` is a spec↔gen filecmp (blind after regen), the bindings test catches offset drift only, the schema test catches width/enum/collision/COMPLETION `array_size` only → `access` rw→wo, `reset`, other `array_size` changes passed green. Now: a **bounded** `STRUCT-FIELDS` gate comparing only `offset/width/access/reset/array_size` against `git show main:spec/npu_abi.json` (notes/description excluded by construction → satisfiable).
3. Round-4's Oracle caught a defect **the planner introduced while folding** (a wrong PCIE docdiv attribution, since corrected); round-5's Oracle caught another (the read-mux instruction omitted words 0-3, since corrected).
**Process finding (recorded for the retro): marginal yield went NEGATIVE after round 3** — each planner fold introduced fresh defects (round-2 partial folds; round-3 wrong attribution; round-4 still-unsatisfiable gate; round-5 omitted read branch). The correct stopping point was ~round 3-4. Round 5's Momus half never delivered: it **looped** (551 messages / 1 hour, bare bash calls, no verdict) because the plan's lines exceed the Read tool's 2000-char truncation and it tried repeatedly to de-truncate via bash. It was cancelled; execution proceeded on Oracle's verified verdict (user chose option A).
**Execution anchors for the worker**: pass `fold -w 400 -s` over the plan in ONE bash call to read it without truncation (do not loop); todo order 0→1→2→3→4→5→6→7 with todo 6 strictly after 5; sz0001 serial; never merge/push.

## Components (topology ledger)
| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| 0-p0-baseline | branch bug-009-doorbell-fix + provenance (4 touched-file hashes) + 7-dirty snapshot inlined | active | .omo/evidence/task-0-bug-009-doorbell-fix.txt |
| 1-xverif-prefix | dead window characterized at APB transaction level (0x10/0x14 read-0 / write-dropped) | active | task-1 |
| 2-r1-rtl-window | doorbell.v implements LAST_STATUS + COMPLETION_STATUS[16]; tb_doorbell Test12 flipped + Test13/14 added; run_doorbell_tb target | active | task-2 |
| 3-t1-conformance | conformance TB doorbell rows DOC-DIV→ABI (20 slots); GREEN with doorbell docdiv=0 | active | task-3 |
| 4-xverif-postfix | live window verified (apb.query write→readback + transfer_window) | active | task-4 |
| 5-regression | apb_smoke + FM-SOC 33 + W4 6-0 + full pytest (failures same classes as bug-012 baseline) | active | task-5 |
| 6-spec-gen | KNOWN_DISCREPANCY resolved + pinned-decision resolution + gen regen note-only | active | task-6 |
| 7-ledger | BUG-009 Fixed, 13 Fixed/2 Open, README synced | active | task-7 |
| F1-F4 | final review wave all APPROVE | active | (plan file) |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| fix direction | A: expand RTL window | FM (apb_peripheral.py:349-350), firmware (20+ writes), device_server (:333) and 3 test files already implement/consume the 6-reg window — RTL is the laggard; single-file RTL change vs multi-layer rewrite | yes (B stays documented in the spec's pinned decision) |
| access-annotation drift | out of scope, documented-benign | RW superset is load-bearing (firmware polling reads HOST_TAIL); ledger already records it benign; avoids spec+gen churn | yes |
| test strategy | tests-after | tb_doorbell Test 12 is the RED anchor; conformance DOC-DIV rows flip post-fix | yes |
| conformance coverage | exactly 20 slots (4 ptr + 0x10 + array[0..14]) | MAX_REGS=20 fits with zero structural change; entry[15]@0x50 + 0x54-unmapped delegated to module TB Test 13/14 | yes |
| W4 evidence-file rewrite | restore tracked build/evidence/w4-*.txt to HEAD post-run; fresh data in gitignored logs + committed evidence | bug-012 precedent (F4-verified correct call); plan forbids build/* commits | yes |
| xverif pre-fix characterization | required, executed BEFORE the R1 edit (baseline code) | user asked for xverif; pre/post waveform contrast mirrors the bug012 case pattern | yes (could keep only post-fix) |
| P0 precondition | main must contain bug-012 merge a326ef9 | BUG-012 landed + pushed this session (2026-09-22); todo 0 gates on it | no (hard gate) |

## Findings (cited - path:lines)
- ABI declares the 6-register doorbell window: `spec/npu_abi.json:564-614` (HOST_TAIL@0x00 wo, NPU_HEAD@0x04 rw, HOST_HEAD@0x08 ro, NPU_TAIL@0x0C ro, LAST_STATUS@0x10 rw, COMPLETION_STATUS[16]@0x14 rw); pinned by `firmware/npu-regmap.h:308-310` (_Static_assert); mirrored `sim/regmap.py:153-160`
- RTL implements only 0x00-0x0C: `rtl/soc/doorbell.v:70` (`addr_valid = (paddr[11:4] == 8'h00)`); `:116-122` read-0 / write-dropped / pslverr=0
- Func Model already implements the window: `sim/models/apb_peripheral.py:349-350` (LAST_STATUS + COMPLETION_STATUS rw)
- Firmware writes the dead window: `firmware/npu_firmware.c` 20+ `LAST_STATUS` sites (:300-576); `write_completion()` :440-454 writes the clamped MMIO mirror `COMPLETION_STATUS[min(cmd_id,15)]` + the full unclamped record to the DRAM completion ring (`COMPLETION_RING_ADDR + cmd_id*32`, 1024 records)
- Host-side **writer** exists: `sim/device_server.py:329-335` **写**错误标记到 `DOORBELL.LAST_STATUS`（**Oracle#16 勘正**：原写"reads"是错的——那是写；真正的状态窗口**读取者**是 `sim/spike_firmware.py:145-167`，属 FM 路径）
- Tests already assume the window: `sim/tests/test_firmware_boot_sequence.py` (LAST_STATUS 0xAA / 0x2000 patterns), `sim/tests/test_spike_ibex_ring_alignment.py` (clamped mirror semantics), `sim/spike_firmware.py:155-167`
- Conformance TB pins the DEAD behavior: `rtl/tb/apb_conformance_real_tb.sv:25-28` (header), `:633-634` (REG_ADDR doorbell row — 6 offsets), `:654-655` (`ACC_DOCDIVR` ×2), `:758` (`doc_bug = "BUG-RTL-SOC-009"`), `:895-922` (`check_docdiv` task); target `sim/regression/Makefile:184-192`, gate `APB_CONFORMANCE_REAL: GREEN`
- Module TB asserts the dead window: `rtl/tb/tb_doorbell.v:448-460` (Test 12 "read undefined 0x10 returns 0")
- No Makefile target for tb_doorbell (only a comment at `sim/regression/Makefile:174`)
- Spec's pinned decision is open: `spec/npu_abi.json:1790-1792` (DOORBELL_COMPLETION_STATUS_SIZE — "define ... as separate concepts, or expand the hardware register window"); generated mirror of the note: `gen/npu_abi_firmware.h:164-168`
- xverif MCP is connected (`/tmp/xverif-mcp-wrapper.sh`); apb.query / apb.transfer_window among the 73 xdebug actions; FSDB compile precedent: `.omo/evidence/xverif-bug012-stride-demo.txt` §5
- BUG-012 landed + pushed this session: merge `a326ef9` on origin/main (BUG-012 now Fixed; ledger 12 Fixed / 3 Open)

## Decisions (with rationale)
1. **Plan A (expand RTL)** over B (shrink ABI) — user decision 2026-09-22; RTL is the laggard, B's blast radius (spec+gen+firmware+FM+tests+device_server) would also delete the host-visible status path
2. **Access-annotation drift out of scope** — user decision 2026-09-22 (documented-benign; RW superset load-bearing)
3. **tests-after** — user confirmed 2026-09-22
4. **Conformance row = exactly 20 slots**; entry[15]@0x50 + 0x54 unmapped delegated to module TB (MAX_REGS=20; zero structural change)
5. **xverif pre-fix characterization runs BEFORE the RTL edit** (baseline code) → pre/post APB-level contrast as the xverif deliverable
6. **Ledger Residual records three by-design constraints**: access drift / cmd_id>15 clamp+DRAM ring / 0x54+ silent-0
7. **P0 gate on a326ef9** — the bug-012 merge must be on main before branching (hard precondition)

## Scope IN
todos 0-7 + F1-F4 as written in `.omo/plans/bug-009-doorbell-fix.md`

## Scope OUT (Must NOT have)
firmware/ any change; sim/ product code (models/, regmap.py, device_server.py, spike_firmware.py); rtl/ other than doorbell.v + tb_doorbell.v + apb_conformance_real_tb.sv; vendored; spec structural fields (offset/width/access/array_size); hand-edited gen/; the 7 parallel-session dirty files; auto-push

## Open questions
(none — three forks answered by the user 2026-09-22: Plan A / keep drift documented-benign / tests-after)

## Approval gate
status: plan-complete-awaiting-start
User approved the approach (3 forks answered 2026-09-22) → plan written + Metis SOUND-WITH-FINDINGS (12 findings, all folded). Awaiting the explicit start instruction (`/start-work bug-009-doorbell-fix` or equivalent) before dispatching any execution worker. **No execution without that.**

## Review receipts
- Metis gap analysis (mandatory, ses_f396940deffe0zHKwSGH02cCJq, 2026-09-22): verdict **SOUND-WITH-FINDINGS** — 2 BLOCKER + 5 MAJOR + 5 MINOR. All 12 independently re-verified by the planner against source, then FOLDED into the plan:
  - **F1 BLOCKER** — `rtl/tb/apb_conformance_real_tb.sv` has zero `$fsdbDump*` (grep confirmed); `+define+FSDB -P novas.tab` alone writes no FSDB → todos 1/4 restructured onto **module-level `tb_doorbell`** with a required `` `ifdef FSDB `` dump block + `run_doorbell_tb` target (todo 1 = harness prep + characterization; todo 2 no longer creates the target).
  - **F2 MAJOR** — doorbell slave sees the 12-bit offset (`paddr_o[11:0]`, TB :515-531), not `0x4000_5010` → xverif config/queries moved to `tb_doorbell.u_dut` with **raw offsets 0x010 / 0x014-0x050**.
  - **F3 BLOCKER** — `scripts/gen_npu_abi.py:474-480` **hardcodes** the firmware-header "Known Discrepancy" block (only interpolates `ring_entries`; never reads spec notes) and the CLI needs `--generate` → todo 6 now edits the generator, uses `--generate`, adds `scripts/gen_npu_abi.py` to the diff whitelist; `firmware/npu-regmap.h:317-322` stale comment recorded as residual (d).
  - **F4 MAJOR** — `REG_CNT[5]=32'd6` (TB :619-620) with `MAX_REGS=20`; loops bound by REG_CNT (:998/:1023) → without 6→20 the array entries 0x18-0x4C never execute → todo 3 item (1b) + acceptance asserts the doorbell per-slave check count 20.
  - **F5 MAJOR** — ledger row cites were wrong: Fixed is **:413**, Open is **:417**, and **:415 is the Pending (waiver) row**; By-Severity Major is :406 → todo 7 re-cited with explicit do-not-touch on :415/:416.
  - **F6 MAJOR** — write/read must re-key on `word_idx`; keeping `case (reg_sel)` aliases COMPLETION_STATUS (word 5→reg_sel=1=NPU_HEAD …) onto the pointer registers and corrupts `doorbell_irq` → todo 2 items (3)-(4) mandate `case (word_idx)` + word_idx read mux.
  - **F7 MAJOR** — the spec/gen change (todo 6) would escape todo 5's pytest gate (ordering) → todo 6 adds targeted `pytest test_npu_abi_schema.py test_npu_abi_bindings.py` + `--check`; **must not rename the `KNOWN_DISCREPANCY` key** (:159 asserts it).
  - **F8 MINOR** — `REG_DOCDIV[5][4..5]=1` (:730) + final-report strings (:1183-1184/:1212) → todo 3 item (5b); acceptance now asserts `doc_div_cnt == 3`.
  - **F9 MINOR** — dangling "7 dirty files" reference → Scope Must-NOT now enumerates all 7 paths + notes the W4 files are clean at HEAD.
  - **F10 MINOR** — W4 tracked-file count was hardcoded/wrong and "significant drift" had no numeric gate → todo 5 records the list dynamically (pre/post `git status --porcelain build/`) and uses an explicit `== 0 or ≤1%` per-case gate.
  - **F11 MINOR** — `tb_doorbell.v:5-13` header lists only Tests 1-7 (8-11 undocumented) → todo 2 item (10) says update to 1-14.
  - **F12 MINOR** — `sim/rtl_soc_runner.py:2688-2689` stale comment → recorded as residual (e).
- **High-accuracy review ROUND 1 (2026-09-22): BOTH NOT-OKAY** — Momus 9 findings (2 BLOCKER / 3 MAJOR / 4 MINOR, ses_f386c4451ffeIOemF12jdR26sf) + Oracle 19 findings (3 BLOCKER / 6 MAJOR / 10 MINOR, ses_f386bf0c6ffe69HDjSWRLqbaGZ). Both independently derived the same two numeric errors I introduced while folding Metis: `doc_div_cnt == 3` (真值 **4** — DMA ACC_WOS 调 check_docdiv 两次) and per-slave check count `6→20` (真值 **16→60**) → both BLOCKER (false gates would hard-STOP or tempt fudging a number). Oracle's third BLOCKER: todo 1's X1-pre evidence for `0x014` was **unsatisfiable** — pre-fix `tb_doorbell` never touches 0x14. Round-1 folds applied (all 28 findings, deduped):
  - **BLOCKERs**: todo 3 acceptance + F3 + Success criteria → `doc_div_cnt==4` + `slv_docdivs[5]==0` + `checks==60` + `REG_CNT[5]==20` 分开判；todo 1 (2b) 新增 pre-fix `0x14` 探针两行使 0x014 证据可产出（并注明 todo 2 翻锚时一并重写）。
  - **MAJORs**: Momus#3 把**不可能满足**的 `-- gen/ | grep -E "offset|width|..."` 零命中门换成 **python 结构不变性比对**（`modules[*]['registers']` 子树 JSON 相等）+ `--check`；Momus#4/Oracle#6 把 todo 6 从"可与 3/4/5 并行"改为**依赖 5 串行**（NFS 同工作树竞争）；Momus#5 去掉生成器 either/or → 定为最小改动（直接改写 `:475-479` 字面量）；Oracle#4 pytest 判定改为 **`comm -13` node-ID 差集为空**（并纠正类别：stripped-env 是 **24F** 非 20F，基线 32F/11E）；Oracle#5 W4 漂移门改以 **todo 1 (4c) 现场采集的 pre-fix 六 case cycles** 为控制、要求严格相等（原引基线无 cycle 数值且 tracked 文件陈旧）；Oracle#7 补 `LAST_STATUS.description`（现值 "0=done,non-zero=error" 修复后可观察为假）；Oracle#8 conformance 循环外补 `0x50`/`0x54` 显式检查（0x50 是 firmware clamp 落点，最高风险）；Oracle#9 Test 14 非空洞化（先造 `HOST_TAIL!=NPU_HEAD` 使 irq=1 再快照断言）+ Test 13 改快照式比对。
  - **MINORs**: 8 SKIP 改按 case ID 精确比对（Oracle#18）；补 `H1-HARNESS`/`ABI-GATES` 判决行（Momus#9）；FSDB 陈旧 simv 守卫 + FSDB 卫生/路径/`.gitignore`（Momus#8、Oracle#17）；失败模式→检查矩阵 + Test 13 i=0..15 硬性（Oracle#10）；FM↔RTL 收敛限定声明窗口 + ≥0x54 分歧记为 residual f（Oracle#11）；TB 头声明访问标注有意不检（Oracle#12）；residual 统一为**七条**（Momus#7/Oracle#13）；Scope 归属矛盾修正（Oracle#14）；陈旧 "113 行" 文档漂移显式出范围记为 residual g（Oracle#15）；`device_server.py:333` 写/读引用勘正（Oracle#16）；字节通道别名 `0x51-0x53→slot15` 并入 residual c（Oracle#19）。
  - 另：Oracle 提出后**排除**的线索——`rtl/tb/tb_mixed.v:1083-1095` 虽实例化 doorbell 但无自检、无消费者依赖死窗口（非 finding）。
- **High-accuracy review ROUND 2 (2026-09-22): BOTH NOT-OKAY again, but convergence** — Momus 2 MAJOR + 1 MINOR-group (ses_f385e16f0ffeST81Nm4lKmZtec); Oracle **1 BLOCKER** + 5 MAJOR + 5 MINOR (ses_f385dc295ffed6bThkPNEiQqtE). Both independently verified my round-1 folds were numerically CORRECT (doc_div_cnt==4, checks==60, generator literal, comm -13 semantics, SKIP IDs, ledger lines) — the remaining defects were **incomplete folds + one hole nobody had seen**:
  - **Oracle BLOCKER (new, most important)**: todo 5's FM-SOC-33 and the W4 zero-drift gate would run the **pre-fix** full-SoC binary — `run_ibex_full_rtl.sh:47-49` only compiles when the simv is absent (else "Reusing existing simv"), `run_w4_perf_batch.sh:25-29` only errors when missing, and `build/ibex_full_rtl/simv_soc_ibex` is dated 2026-08-31 (pre-R1) → headline regression = false evidence; the drift gate passes trivially because the binary is unchanged. **Fold**: todo 5 now mandates `rm -rf build/ibex_full_rtl/simv_soc_ibex{,.daidir} csrc` before the run + provenance must show a simv hash/mtime newer than the R1 commit (F3/F4 check).
  - Structural-invariance gate was **self-contradictory** (both reviewers): `description` lives INSIDE `modules[*]['registers']` (spec :604/:612 are the very fields todo 6 rewrites) while `notes` lives outside → "byte-equal subtree allowing description diffs" is unsatisfiable. **Fold**: exact python snippet that recursively drops `description` from register entries and compares against **`main`** (not HEAD, which already contains todos 0-5).
  - todo-6 parallelism survived in 2 of 5 places (both): todo-6 title `:125` and Commit-strategy `:151`. **Fold**: both now "须待 5 完成后串行".
  - "三条 Residual" survived in 3 places (both): Scope item 7, todo-7 acceptance, Success #6. **Fold**: all three → 七条 (a)-(g).
  - Explicit 0x50/0x54 `check()` calls had no `slv` (both): `check()` increments `slv_checks[slv]` for `slv>=0` (TB:874-882) → passing `slv=5` makes the count 62-64, contradicting `checks == 60`. **Fold**: mandated `slv = -1`.
  - `:404` vs `:406` (both) → aligned to `:406`; `comm -13` baseline log unnamed and the cited evidence has zero node-ID lines (Oracle) → pinned to `build/evidence/bug-012-t3-pytest-run.log` + guard `grep -cE '^(FAILED|ERROR)' == 43`; W4 field is `cycles` not `estimated_cycles`, granularity 21 JSONL entries (Oracle) → fixed; "16 项 ignore" → **15** (Oracle); matrix row 1 `Blocks` → 2 (Oracle); `spec/soc_golden_contract.md:296` added to residual (g) (Oracle); `apb_peripheral.py:350→:351` (both).
  - Oracle also **confirmed present** the two items the brief suspected missing (TB-header unchecked-annotations clause; 0x51-0x53 aliasing wording), and confirmed no other dead-window consumer exists.
- **High-accuracy review ROUND 3 (2026-09-22): BOTH NOT-OKAY — but the defects were now in MY folded text, not the plan's substance.** Momus (ses_f3853b575ffeofQVy32Mrmg13c) confirmed the round-2 folds' *values* are right (`doc_div_cnt==4` ✓ with the correct source derivation, `checks==60` ✓, baseline citations `.omo/evidence/task-12-soc-rtl-review-remediation.txt:119` (`DOC-DIV : 8`) and `:96` (`DOORBELL 16/0/4`) ✓, `build/evidence/bug-012-t3-pytest-run.log` = 43 node-ID lines + `32 failed … 11 errors` ✓, AGENTS.md = **15** ignores ✓, `check()` increments `slv_checks[slv]` only for `slv>=0` (`apb_conformance_real_tb.sv:874-882`, global usage `:1130`) ✓) and then found:
  - **the round-2 structural-invariance python snippet was STILL unsatisfiable** — it dumps all of `modules`, which **includes `modules.DOORBELL.notes`** (the very `KNOWN_DISCREPANCY` text todo 6 step (1) must rewrite at `spec:568`), while only popping register-level `description` → assertion fails → the plan's own gate STOPs. **Third consecutive instance of the same class** (v1 grep → v2 byte-equal subtree → v3 notes-included).
  - the todo-6 acceptance criterion still quoted the OLD `HEAD:spec` + "只允许 notes/description 差异" wording, contradicting the new snippet.
  - `:45` still said "16 项 ignore" (the 15-fix had only landed at `:118`).
  - `:118` had **duplicate step numbers** `(3)` twice after the rebuild step was inserted.
  - the Makefile citation "`:174 注释提及`" was inaccurate — `tb_doorbell` appears **nowhere** in the Makefile.
  - the `doc_div_cnt` rationale mis-attributed a PCIE docdiv to the ACC_CONST branch (both PCIE docdivs are `ACC_FIELD @ :1093`; the gate value 4 is still correct).
  - `apb_peripheral.py` off-by-one again (`:349` LAST_STATUS / `:350` COMPLETION_STATUS).
  - **Resolution (deliberate design change)**: the hand-rolled structural-invariance gate is **deleted**; structural consistency is now judged by the project's own sanctioned gates — `gen_npu_abi.py --check` (exit 0) + `pytest test_npu_abi_schema.py test_npu_abi_bindings.py` (which itself runs `--check`) — and the `git diff main -- spec/npu_abi.json` inspection is demoted to an F2 human-observation item, not an acceptance gate. Rationale: three rounds proved that any hand-written gate here is a liability; the project's own gates are already executable and correct.
  - All other round-3 items folded (15 ignores at `:45`, step renumbering, Makefile citation, PCIE attribution, `:350`).
- **Consolidation pass (user chose option B, 2026-09-22)**: the plan was **rewritten wholesale** to strip all review-history narration from execution steps and acceptance criteria (the narration itself had been the defect source in rounds 2-4). Operative content preserved in full; `GEN-REGEN: note-only-diff` (unverifiable once the structural check was reworked) demoted to `GEN-REGEN: clean-regen`; the bounded `STRUCT-FIELDS` gate (5 structural fields, notes/description excluded by construction) is now the structural proof; todo 5 carries the mandatory `simv_soc_ibex` rebuild; todo 6/F3 carry the exact fmpytest env. F1-F4 and Success criteria updated to the corrected values (`doc_div_cnt==4`, `slv_docdivs[5]==0`, per-slave 60, `REG_CNT[5]==20`, `checks`/cycles semantics).
- **High-accuracy review ROUND 5 (final)**: dispatched to both reviewers against the scrubbed plan.
- **High-accuracy review ROUND 4 (2026-09-22): BOTH NOT-OKAY — and this round revealed a process problem.** Momus (ses_f384c62eeffeZr5QO50Th4PheV) = 3 findings (1 HIGH / 1 MEDIUM / 1 LOW); Oracle (ses_f384c1aa5ffev40Cu5ilQajxJ6) = NOT-OKAY.
  - Momus verified the round-2/3 numeric folds are all correct and then found: (i) **F2's text still demanded a hand-rolled "python 子树比对"** — a leftover contradicting todo 6 (2c), which would deadlock the F-wave (F2 must APPROVE) or spawn a 4th variant of the banned gate; (ii) **the delegated gates do NOT catch structural drift** — Momus proved `gen_npu_abi.py --check` is a spec↔gen consistency check (blind by construction after `--generate`), the bindings test catches **offset** drift only (via the frozen `sim/regmap.py` facade), and the schema test catches width≠32 / invalid access token / offset collision / COMPLETION_STATUS `array_size`≠16; **`access` (rw→wo), `reset` and other-register `array_size` changes all pass green** → a false-green channel, and `GEN-REGEN: note-only-diff` was asserted with no mechanism left to verify it; (iii) `:173` should be `:174`.
  - Oracle independently found the same F2/gate issues AND a **defect I had introduced in round 3**: my "correction" claiming *both* PCIE docdivs are `ACC_FIELD @ :1093` is **false** — the real table (`apb_conformance_real_tb.sv:653`) has PCIE `idx0 = ACC_FIELD` (CTRL, fires at `:1093`) and **`idx6 = ACC_CONST`** (BAR1_MASK 0x18, fires at `:1056`), so the ORIGINAL attribution was right and my round-3 fold broke it (gate value 4 unaffected). Oracle also flagged that the bounded pytest command in todo 6/F3 had no env (AGENTS.md makes the readline shim + fpmytest venv mandatory — a bare `pytest` is not acceptable).
  - **Folds applied**: F2's stale clause replaced (now points at the bounded field check); **bounded structural gate added** — `STRUCT-FIELDS: ok` via a python snippet comparing ONLY `offset/width/access/reset/array_size` per register against `git show main:spec/npu_abi.json` (notes/description excluded **by construction** → provably satisfiable, unlike the three earlier hand-rolled attempts, and it closes the access/reset/array_size false-green window); PCIE attribution restored with the correction documented; todo 6 + F3 now specify the exact fpmytest env (`PYTHONPATH=sim:gen:/tmp/fmpytest-shim`, venv python); `:173`→`:174`.
  - **PROCESS SIGNAL (why the loop was stopped here)**: rounds 2, 3 and 4 each found defects **introduced by the previous round's folding** (round-2 fold left 2-of-5 sites stale; round-3 fold broke a correct attribution; round-4's own new bounded gate was still being argued). The plan's *engineering substance* has now been independently verified correct by both reviewers across four rounds; the residual churn is in the planner's verification-scaffolding prose. Surfaced to the user with options rather than silently starting round 5.
