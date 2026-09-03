---
slug: bug-012-root-cause
status: plan-complete + dual high-accuracy review PASSED (R4 both unconditional APPROVE, receipts in review ledger below)
intent: clear
review_required: true (user opted in with "2")
pending-action: user starts execution — /start-work bug-012-root-cause
approach: investigation-only root-cause confirmation for BUG-RTL-SOC-012 — H-GOLDEN local closure (verify_ops re-run + dir-level regen determinism), H-STRIDE static formalization + 8dd5dbe archaeology, sz0001 isolated repro (run_e2e_attn_score live-golden) + manifest overlap audit, bounded two-phase characterization probe test (DIM1=64 layout dump vs DIM1=2 dense), ATTRIBUTION + ledger disposition (Status stays Open, fix direction recorded). No fix in-plan (user decision).
---

# Draft: bug-012-root-cause

## Components (topology ledger)
| id | outcome | status | evidence path |
| --- | --- | --- | --- |
| C1 stale-golden closure | H-GOLDEN verdict (expect refuted) | active | task-1 evidence |
| C2 stride static + archaeology | H-STRIDE-STATIC + INTRODUCING-COMMIT + LAST-KNOWN-PASS | active | task-2 evidence |
| C3 isolated empirical + overlap | ISO-VERDICT + OVERLAP-HAZARD | active | task-3 evidence |
| C4 empirical layout/trigger probe | H-STRIDE-EMPIRICAL + H-TRIGGER-DIM1 + NON-FIX-ASSERTION | active | task-4 evidence |
| C5 attribution + ledger disposition | ATTRIBUTION + BUG-012 ledger update (Status stays Open) | active | task-5 evidence |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
| --- | --- | --- | --- |
| Evidence location | `.omo/evidence/task-{N}-bug-012-root-cause.txt` | matches bug-007 + evidence-integrity plan precedent (both tracked, F1-auditable); resolves Metis #7 | yes |
| Empirical method | backdoor 8KB layout dump primary; FSDB awaddr trace fallback | deterministic, reuses `_sram_backdoor_read`; waveform only if backdoor blocked | yes |
| sz0001 VCS runs serialized (todo 3 → 4) | single simv queue, no parallel VCS jobs | task-14 precedent (shared simv rebuild races) | yes |
| Probe test committed as characterization (RED anchor) | new test fn + Makefile target, additive-only | future fix plan flips Phase A expectation; no existing behavior changed | yes |

## Findings (cited - path:lines)
- Mechanism (statically confirmed by orchestrator spot-check): `rtl/wrapper/mxu_soc_wrapper.v:221` dim1_n(64, reset default :207) overrides WRP_DIM_N(2); `:719-721` row stride 256B; driver `sim/cocotb_bridge.py:2100-2107` pads DIM1=64 with stale comment; firmware `firmware/npu_firmware.c:273` writes actual N (unaffected).
- Chain: Makefile:416-430 → tb_soc.v + test_qwen_blk0 (cocotb_bridge.py:3211); firmware NOT in loop (WFI; 0 DESC_BASE lines, task-14 evidence:72).
- Readback cannot truncate: VPI backdoor full 256B window (cocotb_bridge.py:760-807, 2424-2438).
- Drain-only-row-0 refuted statically: controller.v:277-308 drains all M rows; wrapper FIFO :547-584 captures all.
- Stale-golden weakened: verify_ops PASS today (explore C live run — executor must re-run with provenance); isolated test recomputes golden live (cocotb_bridge.py:3914, Makefile:673).
- Timeline: golden a29e93c 07-07 (after 17/17 @ 9d2d4f9 07-02); 8dd5dbe 07-21 introduced dim1_n override (P9-00A); cf6736b 08-20 store changes (inactive for op05, SCALE_ADDR=0).
- Coverage gap: tb_mxu compiles rtl/mxu/*.v only; wrapper tests M=N=64 only (sim/tests/wrapper/test_mxu_wrapper.py:241-242); op05 = only blk0 MMUL with N<64 AND M>1.
- op05 stray writes span [0x20020000, 0x20022000) = 32 rows × 256B.

## Decisions (with rationale)
- USER DECISION (asked + answered 2026-09-02): **调查-only** — no fix in this plan; BUG-012 stays Open after confirmation; fix direction (driver DIM1=actual N [low-risk test-infra] vs wrapper WRP_DIM_N precedence [product RTL, full regression]) recorded for follow-up plan.
- Metis gap analysis (ses_f9d7cdbafffeuGIs0q33zTNf8X, 12 findings: 5 MAJOR / 5 MINOR / 2 NIT, 0 BLOCKER) — ALL FOLDED: #1 dir-level qwen_blk0 hash+restore; #2+#12 CHARACTERIZATION-ONLY firewall + NON-FIX-ASSERTION (post-addition rerun of run_e2e_attn_score, additive-only diff); #3 conditional verdict lines + routing (ISO-VERDICT / H-STRIDE-EMPIRICAL / H-TRIGGER-DIM1 branches); #4 H-DIM1-CLAIM promoted to testable hypothesis; #5 executable manifest overlap audit; #6 exact -S substring + fallback; #7 evidence location unified to .omo/evidence/; #8 F4 after restore + zero-diff + snapshot-not-diff for untracked; #9 exact 7-file snapshot assertion; #10 GENERATOR-COMMIT provenance + drift routing; #11 explicit 3-file cap + collision check; #12 non-fix assertion.

## Scope IN
- Todos 0-5 + F1-F4 as per plan; evidence per todo with provenance + grep-able verdict lines; BUG-012 ledger Root Cause/Fix/Verification rewrite (Status stays Open).

## Scope OUT (Must NOT have)
- No fix (rtl/, firmware/, scripts/ product read-only; no _configure_engine_regs change); vendored/frozen/gen/ untouched; BUG-002/007/009/010/011/WDT-001 cross-ref only; 7 dirty files untouched; no push; no silent skips.

## Open questions
- (none — the single owner fork was resolved: investigation-only)

## Approval gate
status: approved (user "okay" 2026-09-02, after brief presented)
<!-- gate cleared; plan written; user opted into dual high-accuracy review -->

## High-accuracy review ledger
- R1: Momus APPROVE (ses_f9baef70effeDGMZEpM9jwg0e3, 4A: 8KB read call form / manifest footprint derivation / NON-FIX exit-1 capture / untracked-log na) | Oracle REJECT (ses_f9bae96c0ffeGMkOZaAYCCRjP5, 1B + 8A; 三条承重复核全 VERIFIED，8dd5dbe diff 实锤) → 全部折叠：B1=Phase A 字索引消歧（窗口字 [64r,64r+1] == golden [2r,2r+1]）+ confirmed-via-FSDB 和解态；A2=FSDB +define+FSDB 重编译机制；A3=Commit message 对齐策略行；A4=0 条 contains X 告警门；A5=真实 PASS 串格式；A6=exit-1 ≠ run-error（todo 3 + todo 4 rerun 均 tee）；A7=逐文件 git log 日期过滤；A8=GENERATOR-CRASH 与 DRIFT 分记；A9=unexpected-pass × confirmed 互斥不变量；Momus 4A 同步折叠（A1/A2/A3/A4 与 Oracle 对应项合并）
- R2: Momus REJECT (ses_f9b9f4379ffeQa4oo0uFCwjcc1, 1B + 2A；B1=todo 1【向量目录非原子写入者】与 todo 3【同目录读取者】被依赖矩阵允许并行→文件竞争) | Oracle APPROVE (ses_f9b9ef4dcffeMH18vHjXvTzPdW, 0B + 7A；三条承重复核全 VERIFIED) → 全部折叠：Momus-B1=波次重排（Wave 2 只含 1+2；todo 3 移 Wave 3、Blocked by 0,1 + 前置恢复断言；scripts/ 只读不可改生成器故选串行化而非临时输出目录）；Oracle-A1=todo 1 happy path 显式 `GENERATOR-DRIFT: no`/`GENERATOR-CRASH: no`；A2=ISO 三分判据（`\[e2e_attn_score\] PASS` @ :3994 为 pass 判据、勿依赖 Makefile 门）；A3+Momus-A2=FSDB simv 以 /tmp -Mdir/-o 独立编译不覆盖共享 simv；A4=和解前须指明断言误差具体成因；A5=8dd5dbe 引 subject 原文；A6=FSDB 不可得→静态+ISO 双源 residual；A7=rerun exit 1 预期、签名行为唯一判据；A8=种子 :53-54 + wrapper compute 用例 :312/368/423/492/560 行号勘正；Momus-A3=contains X 字面串 grep
- R3: Momus REJECT (ses_f9b940a15ffenX1E10cK9P1Q8J, 1B + 2A；B1=声称 X-gate 不可达【wrapper 只写每行前 8B、余 248B 留 X】) | Oracle APPROVE (ses_f9b93b372ffeLOVI3BDgOiHpnY, 0B + 5A) → 裁决与折叠：**Momus-B1 前提不实**（Oracle R1/R3 亲核 `:725-759` 全 1 WSTRB + FIFO 整 2048-bit 行 + so_beats=4 → 整行 256B 写、cols 2-63 为计算零，读域预期无 X）——但质疑有价值：计划现**显式陈述写几何依据**（须照抄进 evidence）、X-gate 升格为整行写假设的实证守卫、新增 X 分支路由（`WRAPPER-WRITE-GEOMETRY: partial-row` + preload_sram 预清零重跑一次 + provenance 注记）；Momus-A1 查验后**拒绝采纳**（其建议的主串 "the wrapper's WRP_DIM_N …" 跨行 :2102-2103，git -S 不匹配；原主串在 :2103 行内连续——理由落档于 plan）；Momus-A2 采纳（unexpected-pass 须同时落档 cocotb PASS 行 + 脚本 RESULT: FAIL 行）；Oracle-A1 采纳（Commit strategy 陈旧 Wave 2 枚举勘正）；A2 采纳（ISO 第四分支 repro-fail-different-signary → STOP）；A3 采纳（Wave 3 NFS 编辑竞争 operationalize）；A4 采纳（H-TRIGGER-DIM1 增 layout-unchanged 分支）；A5 采纳（Phase B dense-pass ⇒ H-DIM1-CLAIM: refuted 显式化）
- R4: Momus APPROVE (ses_f9b862cedffeQ7JpD9OoPbNIoT, 2A) | Oracle APPROVE (ses_f9b85e1a6ffesvxtiPHpFQWtL4, 0B + 6A) → **双重评审通过（双无条件 OKAY）**。收尾折叠：Momus-A1 回读路径裁决（planner 亲读 :2424-2438 + :574-599——docstring "PCIe TLP" 过时，实际 DUT 在场走 `_sram_backdoor_read` VPI :591-592；落档 todo 2 并入 stale-documentation 家族）；Oracle-A1 unexpected-pass RESULT 行改为"如实记录实际输出"不预设矛盾；A2 H-TRIGGER insufficient/layout-unchanged 消歧；A3 X-分支认识论注记（预清零后 padding 失区分力、stride 仅靠行落位）；A4 OVERLAP-HAZARD 定性为同根因下游后果非第三假设。**跳过项（理由落档）**：Oracle-A5（run-error 桶对 `[e2e_attn_score] FAIL` 无 mismatch 行的归类略松——路由同为 STOP，纯 cosmetic）；Oracle-A6（LAST-KNOWN-PASS 07-07 过滤阈值略松——executor 落档实际 sha，无害）；Momus-A2（`grep '^-[^-]'` 对重命名/二进制边缘欠鲁棒——本计划纯文本增量，足够）
- 评审完成：R1(Momus OK/Oracle REJ) → R2(Momus REJ/Oracle OK) → R3(Momus REJ【前提经裁决不实】/Oracle OK) → R4(**Momus OK/Oracle OK**)；两份最终凭证齐全且均无条件 APPROVE

