---
slug: evidence-integrity-and-readme-status
status: plan-written (dual-review R4 in progress)
intent: clear
review_required: false
pending-action: dual-review passed (R4 both OKAY); awaiting user `/start-work evidence-integrity-and-readme-status`
approach: 三段式修复 —— ①git add -f 把 10 个台账引用但未入库的 evidence 文件补入库（~650K，含尺寸守卫）；②修正台账断链/假 sha（BUG-006 死引用改指真实存在且入库的 FM-SOC-026.log + 已入库替代证据；module-level 用考古到的真实 sha 675afe0a/513fba6 替换占位/补缺，PERF-000 附"无 pre-fix 提交版本"诚实注记）；③README 顶部新增「项目状态总览」section（状态快照表 + 关键文件索引表，台账为权威源，README 为带日期戳的入口快照）。
---

# Draft: evidence-integrity-and-readme-status

## Components (topology ledger)
| id | outcome | status | evidence path |
|---|---|---|---|
| C1 evidence-ingestion | 10 个被台账引用但未入库的 evidence 文件 `git add -f` 入库并提交 | active | 见 Findings #3 文件清单 |
| C2 ledger-citation-fix | BUG-006 死引用修复（bugs-soc-rtl.md:263）+ module-level 两个 Fix Commit 修正（PERF-000 假 sha、PERF-MXU-001 缺 sha） | active | docs/bugs/bugs-soc-rtl.md:263; docs/bugs/bugs-module-level.md:53,99 |
| C3 readme-status-entry | README 新增「项目状态总览」section：状态快照表 + 关键文件索引表 | active | README.md:1-34（插入点：DSE 引言块之后、文档索引之前） |
| C4 audit-verification | 全台账 evidence 引用复审（入库后所有引用可解析）+ 验收 evidence 落档 | active | .omo/evidence/task-N-evidence-integrity-and-readme-status.txt |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
|---|---|---|---|
| BUG-MX-PERF-000 处置 | 保留条目，假 sha `a1b2c3d4` → 真实 `675afe0a`（修正态随文件诞生），附注"无 pre-fix 提交版本，占位 sha 系 2026-07-06 docs-split commit 2983e97b 误写"+ 交叉引用 bugs-archive.md:101 的"占位示例"标注 | 诚实最小修复；archive 与 module-level 两文档矛盾以注记方式共存，不删条目 | yes |
| BUG-PERF-MXU-001 处置 | Fix Commit 段补真实 sha `513fba6b7a1319542276e6cd2fedaac959c4aa8a`（2026-08-12，同 commit 写入台账条目） | 考古已验证：stat/diff/日期全匹配 | yes |
| BUG-006 死引用处置 | 删除不存在的 `.omo/evidence/task-7-p1-full-rtl.txt` 引用；保留 `build/p1_full_rtl/evidence/FM-SOC-026.log`（改写为 repo 相对路径并 add -f 入库）；补充 3 个已入库替代证据引用（fm-soc-regression.txt / task-16-soc-rtl-verification-signoff.txt / task-22-phase10-rtl-verification.txt） | 原文件从未入 git（`git log --all` 零命中），不可恢复；替代证据全部 tracked 且提及 FM-SOC-026 | yes |
| 入库集合 | 10 个文件：8 个主审计文件（~640K）+ 2 个 ph9-probe K2048 jsonl（BUG-MXU-P9-001-doorbell-divergence.md 引用）；`f3-final-summary.txt` 不入库（无台账条目引用它，超出"每个 bug fix 有 evidence"范围） | 严格按"台账引用的文件必须可解析"圈定范围 | yes |
| 入库尺寸守卫 | 单文件 >5MB 或总入库 >10MB → 停止并上报（不静默跳过） | 防止误把巨型日志塞进仓库 | yes |
| README 语言/维护约定 | 中文（与现 README 一致）；状态快照带日期戳；README 只做快照+入口，详细状态以各台账为唯一权威源（避免双真相源漂移） | 用户要求 README 为入口文件；台账已是 source of truth | yes |
| 分支/提交约定 | 新分支 `evidence-integrity-and-readme-status`（当前目录，不建 worktree），一个 todo 一个原子 commit，最终 wave 全 APPROVE + 用户 okay 后 merge 回 main | 沿用仓库既定工作流 | yes |
| 测试策略 | 无产品代码改动 → 无 TDD/无新测试；每 todo 的 QA = agent 可执行的 grep/复核命令 + 验收 evidence 文件 | 本计划全部是 docs/git/README 改动 | n/a |

## Findings (cited - path:lines)
1. **审计结论**（本会话已完成的三方对照：台账引用 ↔ 磁盘存在 ↔ git 入库）：SoC RTL 台账 11 个 Fixed bug 中 3 个完好（P9-00A/00B/00D）、4 个部分未入库（BUG-005/008/WV-001/WV-007）、1 个断链（BUG-006）、3 个叙述式无文件引用（BUG-001/003/004，早期 bug，不做回溯造假）；Func Model 台账 8 个引用全部 tracked；module-level 2 个坏 Fix Commit。
2. **BUG-MX-PERF-000 考古**（explore session ses_fa0098334ffeL4u3kSI0HDblHP）：`git log --all -S "if (perf_counting)" -- '*tb_mxu_perf*'` 为空 —— buggy 门控从未存在于任何提交版本；`rtl/tb/tb_mxu_perf.v` 由 `675afe0a`（2026-07-02）创建时即为修正态（`:290` `if (state != S_IDLE && state != S_DONE)`）；假 sha `a1b2c3d4` 由 `2983e97b`（2026-07-06 docs-split）写入；bugs-archive.md:101 标注该 ID 为"占位示例 — 非真实 Bug"（Oracle-R2-A2：在 :101，非 :109）。
3. **待入库文件清单**（全部 on-disk-untracked，`git ls-files --error-unmatch` 实测）：
   - build/evidence/fix-module-regression.txt (4.0K)
   - build/evidence/l0l19-probe-evidence.txt (4.0K)
   - build/evidence/l0l19-probe.json (4.0K)
   - build/evidence/task-18-phase10-rtl-verification.txt (4.0K)
   - build/evidence/wrap-sfu-regression.txt (148K)
   - .omo/evidence/task-14-blk0-repro.log (224K)
   - .omo/evidence/task-14-blk0-baseline.log (224K)
   - build/p1_full_rtl/evidence/FM-SOC-026.log (28K)
   - build/evidence/ph9-probe-case1-direct-K2048-N64.jsonl（尺寸待入库前 du 复核）
   - build/evidence/ph9-probe-case1-firmware-K2048-N64.jsonl（同上）
4. **BUG-PERF-MXU-001 考古**：`513fba6` "fix(perf): correct prefill bottleneck from DMA-bound to compute-bound"（2026-08-12 11:16:58）——stat 触及 qwen_spec_gates.py(−2)/model_scaling.py(−2)/bugs-module-level.md(+33)/reports/func-model-perf-verification-report.md(68 行变更)（Oracle-R2-A1 完整口径），diff 精确移除两处 `per_tile_compute = array_H + array_W + array_H` 死覆盖（行位 ≈50-51 / ≈125-126），`cat-file -t`=commit 且为 HEAD 祖先。
5. **BUG-006 替代证据**：`grep -l "FM-SOC-026"` 命中且 tracked —— build/evidence/fm-soc-regression.txt、build/evidence/task-16-soc-rtl-verification-signoff.txt、build/evidence/task-22-phase10-rtl-verification.txt；死引用 `.omo/evidence/task-7-p1-full-rtl.txt` 磁盘+git 全历史均不存在。
6. **README 结构**（README.md:1-34）：标题+引言（:1-4）→ Arc Model DSE 引言块（:6-11）→ `## 文档索引`（:13-32）→ `## Quick Start`（:34+）。状态总览推荐插入点：DSE 块之后、文档索引之前。
7. **README 状态数据源**（均已为本会话刚刷新的权威数据）：bugs-soc-rtl.md 统计（Total 17：Fixed 11 / Pending-waiver 1 / Accepted 1 / Open 4）；module-level（3 Fixed + 1 Open WDT）；func-model（全 Fixed/Deferred）；PCIe（4 UCOV Uncovered）；blockers（#5 waiver 待签 / #7 工作区清理 / #8 signoff manifest；#6 已随用户接受关闭）；E2E-07 perf calibration defer FPGA；BUG-007 调查新增 live hazard（MXU SCALE_ADDR 跨流程泄漏，task-8 残差 #3）。

## Decisions (with rationale)
- D1（默认）：PERF-000 保留条目+真实 sha+诚实注记（见 Open assumptions）。理由：删条目或纯标"占位"会丢失"计数器门控设计依据"这一真实信息；注记让两文档矛盾透明化。
- D2（默认）：f3-final-summary.txt 不入库 —— 无 bug 台账条目引用它，超出本计划范围。
- D3（默认）：BUG-001/003/004（SoC RTL 早期叙述式 Verification）不做回溯性 evidence 造假，仅在 README/汇总如实反映"早期 bug 为叙述式验证"；如未来重跑可补真实 evidence。
- D4（待用户）：README 状态区形态 —— Q1 三选项（推荐 A）。

## Scope IN
- C1-C4 全部四个组件
- docs/bugs/bugs-soc-rtl.md（仅 BUG-006 Evidence 行修复）
- docs/bugs/bugs-module-level.md（仅 PERF-000 Fix Commit 段 + PERF-MXU-001 Fix Commit 段）
- README.md（新增一个 section，不改既有章节内容）
- .omo/evidence/task-*-evidence-integrity-and-readme-status.txt（验收 evidence）
- .omo/plans/evidence-integrity-and-readme-status.md（本计划）

## Scope OUT (Must NOT have)
- 不修改任何产品代码（sim/、rtl/、firmware/、scripts/、gen/、config/ 冻结面、vendored IP）
- 不重跑任何 VCS/仿真（纯 docs+git 操作；FM-SOC-026.log 等为既有磁盘产物）
- 不删改 7 个并行会话 dirty 文件（.omo/evidence/task-0-signoff-v3-runner.txt、task-20-uncertainty-kpis.json、task-23-perf-spec-ci.txt、两个 learnings.md、fm-cv-chain.txt、w3-4-mobilenetv3-fm.txt）
- 不改 BUG-012/BUG-009/010/011 等 Open 条目状态；不动 waiver WVR-SOC-RTL-002
- 不为 BUG-001/003/004 回溯伪造 evidence 文件
- 不把 README 变成第二真相源（快照带日期戳，权威在台账）
- 不 push（用户明示后才 push）

## Open questions
- Q1（README 状态区形态，owner-decision）：
  - **A（推荐）**：README 顶部新增独立 `## 项目状态总览` section（置于 DSE 引言块后、文档索引前），含 ①带日期戳的状态快照表（验证基线 / bug 台账摘要 / blocker / defer 项）②关键文件索引表（台账 / blockers / waivers / plans / evidence / notepads / 评审报告路径）。README 为入口快照，台账为权威源。
  - B：最小改动 —— 仅在现有「文档索引」表中追加 3-4 行状态类条目，不加独立 section（状态不突出，但 README 结构零变化）。
  - C：README 只加一行链接指向新建 `docs/PROJECT_STATUS.md` 详细状态页（README 保持极简，但"通过 README 了解状态"的直达性最弱）。
  - Why：README 是公开入口文件，section 形态是跨切面产品选择；A 最符合"入口文件"诉求但增加 README 维护面。

## Approval gate
status: plan-written (user approved via "A" = README option A; Metis 15 findings folded)
dual-review ledger:
- R1: Momus OKAY (ses_f9fc9be62ffeY1Nodd9H41Pooj, 3 adv) | Oracle REJECT (ses_f9fc9693affe3VnSLGNNq1Qs0l, 2B+6A) → all folded
- R2: Momus REJECT (ses_f9fb57f16ffexAWxTPVkDWqaH9, 2B: jsonl 截断/固定集重复计数 +1A 来源格) | Oracle REJECT (ses_f9fb51bb3ffeL4a8v3ytdHflsr, 4B: 前缀无`/`丢目录/.diff 漏/todo1 验收三元组/来源格非裸路径 +3A: stat 背景句错/快照顺序/F1 HEAD 前移) → all folded:
  * regex v3 = `(?:[A-Za-z0-9_.-]+/)*evidence/[A-Za-z0-9._,{}*+-]+\.(txt|jsonl|json|md|log|csv|diff)` + 绝对路径 CaduceusCore/ 归一化
  * 入库集重定义 = 固定 8 非 ph9 + 枚举 untracked ph9（预期 2）= 预期 10 唯一路径（Momus-B2 正确：R1 的固定 10 已含 2 个 ph9，并集去重仅 10；Oracle-β"无冲突"判断错误）
  * todo 0 重排序：快照移到最后一步（先全部提交再拍）
  * todo 1 stat 断言改路径域口径 + evidence 随同 pathspec 提交
  * todo 3 stat 背景句改为准确四文件描述
  * todo 4 来源格只放裸路径，prose 移括号外
  * F1 HEAD 相关断言改用 todo commit sha 定向
- R3: Momus OKAY (ses_f9fa5f1b0ffeXZL2B5trQDg9qO, 9/9 修复落地 + 2 adv: brace 展开实现注/33-case 口径注) | Oracle REJECT (ses_f9fa5961afferWY71d58W7Pfw0, 仅 1 条 F4 白名单漏 build/p1_full_rtl/evidence/* + minors: draft 陈旧字段/正则实测 46 行) → 全部 folded：F4 白名单补 token、todo 5 brace 实现注、F2 33-case 口径注、draft front-matter/:101/#4 stat 修正
- R4: Momus OKAY (ses_f9f98a1f4ffeT5ygz8hXcfWjJv, 4/4 delta 全落地，1 minor markdown 格式) | Oracle OKAY (ses_f9f9864acffeA2naKMq41ypOIg, F4 21-file 变更集全过，3 advisory nits 全部顺修) → **双重评审通过（双重无条件 OKAY）**；draft L6/L23 与 plan todo-5 extglob 表述已顺修
- 评审完成：R1(OK/REJ) → R2(REJ/REJ) → R3(OK/REJ) → R4(OK/OK)；两份最终凭证齐全
plan: .omo/plans/evidence-integrity-and-readme-status.md (6 todos + F1-F4)
note: B2 算术争议已由 Oracle R2 裁定（explorer 正确：8 在盘 = 6 tracked + 2 untracked；动态枚举设计 sound）；bugs-archive.md 占位示例在 :101（非 :109）；Oracle-R3 实测 v3 正则捕获 46/46 行零漏零误。
