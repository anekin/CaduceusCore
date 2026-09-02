# evidence-integrity-and-readme-status - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 修复 bug 台账的证据链完整性——10 份被台账引用但从未入库的验证证据文件补进 git（固定 8 + ph9-probe 枚举 2）、2 处错误/缺失的修复提交号换上考古到的真实值、1 处指向不存在文件的死引用改指真实证据；同时在 README 顶部新增一张带日期的「项目状态总览」快照表和关键文件索引，让任何人打开 README 就能了解项目当前的整体状态与去哪里看细节。

**Why this approach:** 证据缺口分三类（未入库 / 假提交号 / 死链），逐类做最小诚实修复而不伪造历史；README 只做带"数据来源"列的入口快照，权威数据留在各台账，避免两处维护打架。

**What it will NOT do:** 不改任何硬件/模型/脚本产品代码；不重跑任何仿真；不为早期只有文字结论的 bug 事后补造证据文件；不处理与本任务无关的 7 个并行会话未提交文件；不自动推送 git。

**Effort:** Short
**Risk:** Low - 全部是文档与 git 操作；唯一风险是误 stage 并行会话文件，已用"仅显式路径 staging"纪律封死
**Decisions to sanity-check:** README 状态区采用方案 A（独立 section + 数据来源列）；BUG-MX-PERF-000 保留条目并注明"修正态随文件诞生、无 pre-fix 提交版本"而非删除条目

Your next move: 批准后可直接 `/start-work evidence-integrity-and-readme-status` 开始执行，或先跑一次高精度评审。Full execution detail follows below.

---

> TL;DR (machine): Short | Low | docs+git only — ingest 10 ledger-referenced evidence files (fixed 8 + ph9-probe delta), fix 3 ledger citations (real shas + dead path), add README status snapshot; zero product-code changes, zero simulation reruns.

## Scope
### Must have
- **C1 evidence 入库**：固定 8 个非 ph9 的未跟踪文件 + todo 0 枚举出的全部 untracked `ph9-probe-*.jsonl`（预期 2 = case1 K2048 direct/firmware，合计预期 **10 个唯一路径**，~664K）`git add -f` 入库（清单见 todo 1）
- **C2 台账引用修复**：BUG-006 死引用改指入库后的 FM-SOC-026.log + 3 个已入库替代证据；module-level 两个 Fix Commit 换真实 sha（`675afe0a` / `513fba6`）+ Stats 表刷新为 Total 4 / Fixed 3 / Open 1
- **C3 README 项目状态总览**（方案 A，用户已选）：DSE 引言块后插入独立 section——带日期戳的状态快照表（9 行，每行来源格 `（来源：<path>）`，blocker 全 8 项逐列）+ 关键文件索引表（台账/blockers/waivers/plans/evidence/notepads/评审报告）
- **C4 全台账引用脚本化复审**：9 个 docs/bugs/*.md 的全部 evidence 引用（修正版正则，含 brace/嵌套目录）逐条分类（tracked / ingested-by-C1 / UNRESOLVED）+ 含 `evidence/` 行残余检查 + narrative-only 白名单逐条点名，验收判据 `UNRESOLVED == 0 且 RESIDUAL-CHECK: clean`
- 分支 `evidence-integrity-and-readme-status`（当前目录，不建 worktree）；一个 todo 一个原子 commit；F1-F4 全 APPROVE + 用户 okay 后 `--no-ff` merge 回 main
- 7 个已知并行会话 dirty 文件全程不动（清单见 Must NOT）

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **7 个并行会话 dirty 文件**（.omo/evidence/task-0-signoff-v3-runner.txt、.omo/evidence/task-20-uncertainty-kpis.json、.omo/evidence/task-23-perf-spec-ci.txt、.omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md、.omo/notepads/phase6-rtl-verification/learnings.md、build/evidence/fm-cv-chain.txt、build/evidence/w3-4-mobilenetv3-fm.txt）——不 stage、不 commit、不 stash、不 reset；**绝不 `git add .` / `git add -A` / `git commit -a`**，只允许逐 todo 显式路径 staging
- 产品代码零改动：`sim/`、`rtl/`、`firmware/`、`scripts/`、`gen/`、`config/` 冻结面与 vendored IP（`rtl/cpu/ibex/`、`rtl/ip/verilog-*/` 等）零 diff
- 零 VCS/仿真重跑（纯 docs+git；入库文件均为既有磁盘产物）
- 不改 Open bug 状态（BUG-RTL-SOC-009/010/011/012）、不动 WVR-SOC-RTL-002 waiver、不动 BUG-002 Pending 状态
- 不为 SoC RTL BUG-001/003/004（早期叙述式 Verification）回溯伪造 evidence 文件——在 C4 审计输出中如实归类 narrative-only
- `build/evidence/f3-final-summary.txt` 不入库（无台账条目引用它，超出"每个 bug fix 有 evidence"范围）
- README 不做第二真相源：快照带日期戳 + 数据来源列，详细/最新状态以各台账为唯一权威源
- 不 push（用户明示后才 push）

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **none**（纯 docs/git 改动，无产品代码）——每 todo 的验证 = agent 可执行的 grep / `git ls-files` / `test -f` / 内联审计脚本断言，零人工判读
- Evidence: `.omo/evidence/task-{0..5}-evidence-integrity-and-readme-status.txt`（随对应 todo commit 入库）
- 关键验收口径：todo 0 的 git-status 快照**必须恰好 7 行**且逐行匹配 Must NOT 清单（计划/draft 产物先提交再拍快照）；todo 5 审计 `UNRESOLVED: 0`；F1-F4 全 APPROVE 后仍需用户 explicit okay 才 merge

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1**: todo 0（P0 基线，阻塞全部后续）
- **Wave 2**: todos 1 / 2 / 3 / 4 **并行**（无文件冲突：todo 1=纯 git staging；todo 2=docs/bugs/bugs-soc-rtl.md；todo 3=docs/bugs/bugs-module-level.md；todo 4=README.md）
- **Wave 3**: todo 5（全台账复审门，需 1-4 全部完成）
- **Final**: F1-F4 并行评审波

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 0 | none | 1,2,3,4,5 | — |
| 1 | 0 | 5 | 2,3,4 |
| 2 | 0 | 5 | 1,3,4 |
| 3 | 0 | 5 | 1,2,4 |
| 4 | 0 | 5 | 1,2,3 |
| 5 | 1,2,3,4 | F1-F4 | — |
| F1-F4 | 5 | merge gate | 彼此并行 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 0. P0 基线：分支 + 入库预检（尺寸守卫 + 干跑 + 快照口径）
  What to do / Must NOT do: **执行顺序（Oracle-R2-A2：git-status 快照必须是最后一步——若先写 evidence 再拍快照，evidence 文件会成为 ?? 第 8 行自毁口径）**：(1) `git checkout -b evidence-integrity-and-readme-status main`（当前目录，**禁止新建 worktree**）。(2) **入库预检**：对固定 8 个文件（todo 1 清单中非 ph9 部分）逐个 `test -f`（在盘）+ `git ls-files --error-unmatch <p>` 必须失败（未跟踪）。(3) **ph9-probe 枚举（Oracle-R1-B2）**：`ls build/evidence/ph9-probe-*.jsonl` 逐文件记录在盘/tracked 状态——预期 8 个在盘（6 tracked + 2 untracked：`ph9-probe-case1-{direct,firmware}-K2048-N64.jsonl`）；**untracked 者全部纳入 todo 1 入库集**（若 >2 个也全部纳入，同一守卫）。(4) **入库全集 = 固定 8 + 枚举出的 untracked ph9-probe（预期 2，合计预期 10 个唯一路径——Momus-R2-B2：ph9 文件只来自枚举、不进固定集，杜绝重复计数）**；对全集 `du -ch`（预期 ~664K）断言**单文件 ≤5MB 且总量 ≤10MB**，超限 → STOP 上报（no_silent_skip，Metis #2）。(5) `git add -f -n <全集显式路径>` **干跑**：必须 exit 0 且列出行数 == 全集数（预期 10；验证嵌套 gitignored `build/p1_full_rtl/` 可强制加，Metis #10）。(6) 预检表写入 `.omo/evidence/task-0-evidence-integrity-and-readme-status.txt`。(7) **pathspec 提交计划 + draft + 本 evidence 文件**（3 个显式路径；draft 被 .gitignore 忽略需 `add -f`，先例 = bug-007 计划 todo 0）。(8) **全部提交完成后拍 `git status --porcelain` 快照（最后一步）**：**必须恰好 7 行**且逐行匹配 Scope Must-NOT 清单；任何额外行（含 `??`）→ STOP 上报。Must NOT：不 stash/不 commit 7 个 dirty 文件；不用 `git add .`/`-A`/`commit -a`。
  Parallelization: Wave 1 | Blocked by: none | Blocks: 1,2,3,4,5
  References (executor has NO interview context - be exhaustive): 固定 8 文件清单见 todo 1 What-to-do；7 个 dirty 文件清单见 Scope Must-NOT；P0 惯例先例 `.omo/evidence/task-0-bug-007-root-cause.txt`；ph9-probe 引用来源 = `docs/bugs/BUG-MXU-P9-001-doorbell-divergence.md:20-21`、`docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md:17-18`（brace 引用）、`docs/bugs/bugs-soc-rtl.md:498`（裸星）
  Acceptance criteria (agent-executable): `git branch --show-current` == `evidence-integrity-and-readme-status`；`git status --porcelain | wc -l` == 7 且逐行 ⊆ Must-NOT 清单（**快照在全部提交之后拍**）；evidence 含固定 8 文件"在盘/未跟踪"对照表 + ph9-probe 8 文件枚举/tracked 状态表 + du 总量输出 + 干跑输出（行数 == 全集数，预期 10）。
  QA scenarios (name the exact tool + invocation): happy=预检全过 + 快照恰好 7 行落档（bash：checkout/枚举/du/干跑/写 evidence/pathspec 提交/终拍快照）；failure=任一文件缺失、尺寸超限、干跑行数 ≠ 全集数、快照出现额外行 → STOP 记录根因。Evidence `.omo/evidence/task-0-evidence-integrity-and-readme-status.txt`
  Commit: Y | chore(omo): P0 baseline — branch + ingestion preflight

- [x] 1. C1 evidence 入库：固定 8 + 枚举 untracked ph9-probe（预期合计 10 个唯一路径）
  What to do / Must NOT do: (1) 复跑 todo 0 的干跑确认（防 stale）。(2) `git add -f` **全集 = 固定 8 个非 ph9 文件 + todo 0 枚举出的全部 untracked `build/evidence/ph9-probe-*.jsonl`（预期恰好 2：`ph9-probe-case1-{direct,firmware}-K2048-N64.jsonl`；合计预期 10 个唯一路径——Momus-R2-B2：ph9 文件只来自枚举，不进固定集，杜绝重复计数）**。固定 8 = `build/evidence/fix-module-regression.txt`、`build/evidence/l0l19-probe-evidence.txt`、`build/evidence/l0l19-probe.json`、`build/evidence/task-18-phase10-rtl-verification.txt`、`build/evidence/wrap-sfu-regression.txt`、`.omo/evidence/task-14-blk0-repro.log`、`.omo/evidence/task-14-blk0-baseline.log`、`build/p1_full_rtl/evidence/FM-SOC-026.log`。(3) 写本 todo evidence（每文件一句"被哪个 bug 引用"）并一并 `git add`。(4) 提交前断言 `git diff --cached --name-only` 恰好 == 全集 ∪ {本 todo evidence 文件}（Oracle-R1-A1）。(5) **带 pathspec 提交**（全集 + evidence 一起，Oracle-R2-B3：evidence 必须随本 todo 入库，否则成为 ?? 第 8 行破坏快照）。(6) **stat 断言用路径域口径（Oracle-R2-B3）**：`git show --stat HEAD -- <全集显式路径>` 的文件数 == 全集数（预期 10；evidence 文件不在全集断言内，它在整提交里单独 +1）。Must NOT：`build/evidence/f3-final-summary.txt` 不入库（D2，无台账条目引用）；绝不 `git add .`/`-A`/`commit -a`（Metis #6）。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 5 | 可与 2/3/4 并行
  References (executor has NO interview context - be exhaustive): 引用来源——BUG-005/WV-001/WV-007 引 `fix-module-regression.txt`（docs/bugs/bugs-soc-rtl.md 各自 Verification/Evidence 段）；BUG-008 引 `l0l19-probe-evidence.txt`/`.json`；WV-001 引 `task-18-phase10-rtl-verification.txt`/`wrap-sfu-regression.txt`；BUG-012 引 `task-14-blk0-{repro,baseline}.log`；BUG-006 引 `build/p1_full_rtl/evidence/FM-SOC-026.log`（:263）；ph9-probe jsonl 引用 = `docs/bugs/BUG-MXU-P9-001-doorbell-divergence.md:20-21`、`docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md:17-18`（brace）、`docs/bugs/bugs-soc-rtl.md:498`（裸星）——brace/裸星在盘展开共 8 文件（6 tracked + 2 untracked），本 todo 只补 untracked 部分
  Acceptance criteria (agent-executable): `git ls-files --error-unmatch <p>` 对全集（预期 10）全部 exit 0；`git show --stat HEAD -- <全集显式路径>` 文件数 == 全集数（预期 10，路径域口径）；提交后 `git status --porcelain` 仍恰好 7 行；`git log -1 --name-only` 不含任何 Must-NOT 文件。
  QA scenarios: happy=全集 tracked + evidence 落档 + 快照 7 行；failure=干跑/staging 异常或 staged 集与清单不符 → STOP。Evidence `.omo/evidence/task-1-evidence-integrity-and-readme-status.txt`
  Commit: Y | chore(evidence): track ledger-referenced evidence files (fixed 8 + untracked ph9-probe)

- [x] 2. C2a BUG-006 引用修复（docs/bugs/bugs-soc-rtl.md）
  What to do / Must NOT do: 修改 `docs/bugs/bugs-soc-rtl.md:263` 的 Evidence 行：(a) 删除死引用 `.omo/evidence/task-7-p1-full-rtl.txt`（该文件从未入 git——`git log --all --oneline -- .omo/evidence/task-7-p1-full-rtl.txt` 零命中，不可恢复）；(b) 把 `CaduceusCore/build/p1_full_rtl/evidence/FM-SOC-026.log` 改写为 repo 相对路径 `build/p1_full_rtl/evidence/FM-SOC-026.log`（已由 todo 1 入库）；(c) 附注一句"原 task-7 p1 full-RTL 证据引用从未入库，已移除（2026-09-02）"——**注意措辞不得包含字面路径 `task-7-p1-full-rtl`（Oracle-B1：注记与 grep 归零断言互斥）**；(d) 追加 3 个已入库替代证据：`build/evidence/fm-soc-regression.txt`、`build/evidence/task-16-soc-rtl-verification-signoff.txt`、`build/evidence/task-22-phase10-rtl-verification.txt`（三者 grep 命中 FM-SOC-026 且 tracked）。(e) **带 pathspec 提交**（Oracle-A1）：`git commit -m "…" -- docs/bugs/bugs-soc-rtl.md .omo/evidence/task-2-…txt`，提交前断言 staged 集恰为这两个文件。Must NOT：不改 BUG-006 其他字段/Status；不动相邻条目。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 5 | 可与 1/3/4 并行（引用修复是文本编辑，不依赖 todo 1 入库完成）
  References: `docs/bugs/bugs-soc-rtl.md:220-263`（BUG-006 条目与 Evidence 行）；explore 考古——死引用磁盘+git 全历史缺失；3 个替代文件 `grep -l "FM-SOC-026"` 实测 tracked
  Acceptance criteria (agent-executable): `grep -c "task-7-p1-full-rtl" docs/bugs/bugs-soc-rtl.md` == 0（注记措辞已避开该字面串，Oracle-B1）；`grep -c "CaduceusCore/build/p1_full_rtl" docs/bugs/bugs-soc-rtl.md` == 0（前缀确实移除，Momus-M1）；`grep -n "build/p1_full_rtl/evidence/FM-SOC-026.log" docs/bugs/bugs-soc-rtl.md` ≥1；在 BUG-006 区段（:220-:280 内）`grep -cE "fm-soc-regression\.txt|task-16-soc-rtl-verification-signoff\.txt|task-22-phase10-rtl-verification\.txt"` ≥3。
  QA scenarios: happy=死引用清零 + 前缀清零 + 替代证据落位；failure=编辑误伤相邻条目 → `git diff` 复核仅 :263 区段变更。Evidence `.omo/evidence/task-2-evidence-integrity-and-readme-status.txt`
  Commit: Y | docs(bugs): fix BUG-006 evidence citation — dead path → tracked artifacts

- [x] 3. C2b module-level Fix Commit 完整性 + Stats 表刷新（docs/bugs/bugs-module-level.md）
  What to do / Must NOT do: (a) **BUG-MX-PERF-000**（:53-75）Fix Commit 段——删除假 sha `a1b2c3d4`，改写为：`675afe0a`（2026-07-02）创建 `rtl/tb/tb_mxu_perf.v` 时即携带正确的 state-based 门控（HEAD :290 `if (state != S_IDLE && state != S_DONE)`）；`if (perf_counting)` 累加门控**从未存在于任何提交版本**（`git log --all -S "if (perf_counting)" -- '*tb_mxu_perf*'` 为空）；原占位 sha 系 2026-07-06 docs-split commit `2983e97b` 误写；交叉引用 `docs/bugs/bugs-archive.md:101`"占位示例 — 非真实 Bug"标注（**Oracle-A2：该标注在 :101，非 :109**；Metis #11：**不得暗示存在 pre-fix diff**）。(b) **BUG-PERF-MXU-001**（:99-130）Fix Commit 段补真实 sha：`513fba6`（`513fba6b7a1319542276e6cd2fedaac959c4aa8a`，2026-08-12 "fix(perf): correct prefill bottleneck from DMA-bound to compute-bound"——同 commit 写入本台账条目）。**台账正文只写 sha+日期+message，不转写 stat 细节**（Oracle-R2-A1：真实 stat = `docs/bugs/bugs-module-level.md` +33、`sim/timing/qwen_spec_gates.py` −2、`sim/timing/model_scaling.py` −2、`reports/func-model-perf-verification-report.md` 68 行变更——作为 executor 背景上下文留在 evidence，不进台账，避免转写失真）。(c) **Stats 表刷新**（:128-134，Metis #4）：Total 3→**4**、Fixed 2→**3**（BUG-MX-PERF-000 / BUG-001 / BUG-PERF-MXU-001）、Open 1 不变（BUG-MXU-WDT-001）。(d) **带 pathspec 提交**（Oracle-A1）。Must NOT：不改 BUG-001（sha `295d6b9` 已真实有效）与 WDT-001 条目正文。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 5 | 可与 1/2/4 并行
  References: `docs/bugs/bugs-module-level.md:53-75`（PERF-000）、`:99-130`（PERF-MXU-001）、`:128-134`（Stats）；explore 考古全量命令输出（`git log -S` / `git show --stat` / `git cat-file -t`）；`docs/bugs/bugs-archive.md:101`（占位示例标注所在行）
  Acceptance criteria (agent-executable): `grep -c "a1b2c3d4" docs/bugs/bugs-module-level.md` == 0；`grep -n "675afe0a" docs/bugs/bugs-module-level.md` ≥1；`grep -n "513fba6" docs/bugs/bugs-module-level.md` ≥1；`grep -n "bugs-archive.md:101" docs/bugs/bugs-module-level.md` ≥1（且不含 `:109` 误引）；Stats 断言 `grep -E "Total bugs.*\| 4|Fixed.*\| 3" docs/bugs/bugs-module-level.md` 命中。
  QA scenarios: happy=四处修正落位 + 断言全过；failure=Stats 漏改 → grep 断言失败可见。Evidence `.omo/evidence/task-3-evidence-integrity-and-readme-status.txt`（含 513fba6 完整 stat 背景上下文）
  Commit: Y | docs(bugs): module-level fix-commit integrity — real shas + stats refresh

- [x] 4. C3 README「项目状态总览」（方案 A，用户已选）
  What to do / Must NOT do: 在 `README.md` 插入独立 `## 项目状态总览` section——**插入点：line 12（DSE 引言块后的空行）之后、line 13 `## 文档索引` 之前**，保留前后空行分隔（Metis #7：不得吞空行导致引言块与标题粘连）。内容：(a) 快照头：`快照日期：2026-09-02` + 维护约定一句"本表为入口快照，详细/最新状态以各台账为唯一权威源"。(b) **状态快照表（9 行，每行来源格用固定格式 `（来源：<裸路径>）`——来源格内只放可 `test -f` 的路径，章节/字段名等说明性 prose 放括号外的行文本里，Momus-R2-M3/Oracle-R2-B4）**：①芯片/RTL 阶段：RTL Phase 3 SoC 集成完成（Ibex RV32IMC + AXI crossbar + APB + doorbell ring），详见 RTL Phase 3 章节（来源：README.md）；②验证基线：pytest 210 基线（Quick Start 口径）+ FM-SOC 33-case 全量回归套件（`run_fm_soc_all.sh`）+ 模块级回归详见各 Phase 章节，计数口径警告见 NOTES（来源：AGENTS.md）；③Bug 台账 SoC RTL：**17 = 11 Fixed / 1 Pending（waiver 待签）/ 1 Accepted（reconstruction-failure）/ 4 Open**，见 Final Bug Statistics（来源：docs/bugs/bugs-soc-rtl.md）；④module-level：**4 = 3 Fixed / 1 Open（WDT-001）**（来源：docs/bugs/bugs-module-level.md）；⑤Func Model：全部 Fixed/Deferred、零 waiver（来源：docs/bugs/bugs-soc-func-model.md）；⑥PCIe DMA：4 个 UCOV 覆盖缺口（Uncovered）（来源：docs/bugs/bugs-pcie-dma.md）；⑦**Blocker 全 8 项逐项列**（Oracle-R1-A6，不得只列子集）：#1 perf-CI 17.4GB RSS 超限（gating）、#2 36 层连续 forward 定界（defer FPGA）、#3 FPGA L5 + ggml lifecycle（**BLOCKED**）、#4 同 #2 defer FPGA、#5 WVR-SOC-RTL-002 waiver **待用户签署**、#6 BUG-007 根因追查**已关闭**（用户接受）、#7 工作区状态清理（open）、#8 可重放 signoff manifest + 用户签收（open）（来源：docs/soc-rtl-review-remediation-blockers.md）；⑧Defer：E2E-07 perf calibration → FPGA，见 NOTES（来源：AGENTS.md）；⑨已知 live hazard：MXU SCALE_ADDR 跨流程状态泄漏（BUG-007 调查发现，未立案，残差 #3）（来源：.omo/evidence/task-8-bug-007-root-cause.txt）。(c) **关键文件索引表**（每行一句话用途）：台账 4 个（`docs/bugs/bugs-soc-rtl.md`、`docs/bugs/bugs-soc-func-model.md`、`docs/bugs/bugs-module-level.md`、`docs/bugs/bugs-pcie-dma.md`——**必须用这四个确切文件名，Metis #5**）、`docs/soc-rtl-review-remediation-blockers.md`、`docs/waivers/`、`.omo/plans/`、`.omo/evidence/`、`.omo/notepads/`、`build/evidence/`（gitignored、关键件已 add -f）、`reports/CaduceusCore-review-report-2026-08-28.md`。(d) **带 pathspec 提交**（Oracle-A1）。Must NOT：不改 README 既有任何章节内容；快照数字全部带来源格（不做无出处数字）。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 5 | 可与 1/2/3 并行
  References: `README.md:1-34`（插入点与现有结构；**注意 README 现文已含 2 处"数据来源"字样于 :240/:896，故验收用增量口径，Oracle-A4**）；数据源——`docs/bugs/bugs-soc-rtl.md` Final Bug Statistics（17/11/1/1/4）、`docs/bugs/bugs-module-level.md`（todo 3 刷新后 Stats 4/3/1）、`docs/soc-rtl-review-remediation-blockers.md`（Blocker 1-8 全部行）、AGENTS.md NOTES（test-count 口径警告 + E2E-07 defer）、`.omo/evidence/task-8-bug-007-root-cause.txt`（残差 #3）
  Acceptance criteria (agent-executable): `grep -n '^## 项目状态总览' README.md` 命中且行号 ∈ [13,15]；`sed -n '11,16p' README.md` 显示空行分隔完好；**编辑前先落档基线 `grep -c '（来源：' README.md`（记为 B0），编辑后 `grep -c '（来源：' README.md` − B0 ≥ 9**（Oracle-R1-A4 增量口径）；快照表 9 行来源格内的**裸路径**逐个 `test -f` 全通过（来源格只放路径，Momus-R2-M3/Oracle-R2-B4）；blocker 行含全部 8 个编号（`grep -oE '#[1-8]' <快照区段> | sort -u | wc -l` == 8）。
  QA scenarios: happy=section 落位 + 来源格增量 ≥9 + 8 个 blocker 全列；failure=空行丢失/来源路径失效/blocker 漏列 → 断言失败可见。Evidence `.omo/evidence/task-4-evidence-integrity-and-readme-status.txt`（含 B0 基线记录）
  Commit: Y | docs(readme): add project status snapshot + key file index

- [x] 5. C4 全台账引用复审（脚本化验收门）
  What to do / Must NOT do: 执行**内联 python3 审计脚本**（Metis #3：必须是脚本 + 机器可判定输出，非人工浏览；不新建永久 scripts/ 产品文件）：(1) 扫描 docs/bugs/ 下全部 9 个 md（4 个台账 + `BUG-001.md` / `BUG-MXU-P9-001-doorbell-divergence.md` / `BUG-MXU-P9-00B-broadcast-multitile.md` / `BUG-MXU-WDT-001.md` / `bugs-archive.md`），用**修正版正则 v3 `(?:[A-Za-z0-9_.-]+/)*evidence/[A-Za-z0-9._,{}*+-]+\.(txt|jsonl|json|md|log|csv|diff)` 提取引用**——三处关键修正：①前缀 `(?:[A-Za-z0-9_.-]+/)*` 可含多级目录段（捕获 `build/`、`.omo/`、`build/p1_full_rtl/` 前缀，Oracle-R2-B1：原 v2 前缀无 `/` 会把所有引用截成 `evidence/<name>` 导致 ls-files 全败）；②扩展名交替中 **`jsonl` 必须排在 `json` 之前**（Momus-R2-B1：否则 `.jsonl` 被贪婪截成 `.json`）；③**必须包含 `diff`**（Oracle-R2-B2：`docs/bugs/bugs-soc-func-model.md:386` 引用 `.omo/evidence/bridge-accum-t3-bugtracker.diff`，v2 捕获不到会确定性触发残余检查失败）；**归一化规则**：绝对路径行（如 `docs/bugs/bugs-soc-rtl.md:516` 的 `/home/…/CaduceusCore/build/evidence/…`）截取最后一个 `CaduceusCore/` 之后的 repo 相对路径（Oracle-R2-B1））；(2) brace/glob 型引用**在磁盘上展开**到具体文件（如 `ph9-probe-case{1,2,3}-{direct,firmware}-K*.jsonl` → 8 个在盘文件）——**实现注意（Momus-R3-A1）**：python 标准库 `glob` **不展开** `{a,b,c}` brace 模式，naive `glob.glob` 会返回 0 匹配 → 伪 UNRESOLVED → STOP；脚本必须先自实现 brace 展开（把 `case{1,2,3}` 拆成 3 个模式）再逐模式 `glob.glob`，或经 `subprocess` 调 bash 利用其默认 brace 展开特性（bash 默认支持 `{a,b,c}` 展开，无需 extglob）；(3) 每个展开文件/直接引用分类：**tracked**（`git ls-files` 命中）/ **ingested-by-C1**（todo 1 入库集）/ **UNRESOLVED**（未跟踪且不在 C1 集）；(4) **残余检查（Oracle-B2 fix-3）**：9 个文档中**每一条含 `evidence/` 字样的行**必须被提取器捕获、或落入显式白名单（narrative-only 条目清单）——任何既未捕获又未白名单的 `evidence/` 行 = 审计失败（不允许静默不可见）；(5) **narrative-only 白名单（逐条点名）**：SoC RTL BUG-001/003/004（bugs-soc-rtl.md:43-186 各 Verification 段无文件引用）+ module-level **全部 4 条**（BUG-MX-PERF-000、BUG-001、BUG-PERF-MXU-001、BUG-MXU-WDT-001——Oracle 角5：4 条均无文件引用，非 3 条）；(6) PASS 判据：**`UNRESOLVED == 0` 且 残余检查零遗漏**；(7) 结果写入 evidence：逐引用分类表 + 残余检查表 + 时间戳 + 脚本 + PASS/FAIL（Metis #14）。同时复检 README 结构（todo 4 的断言复跑）。(8) **带 pathspec 提交**（Oracle-A1）。Must NOT：UNRESOLVED>0 或残余检查有遗漏时 STOP 上报（列明缺口，流入用户决策），不静默放行。
  Parallelization: Wave 3 | Blocked by: 1,2,3,4 | Blocks: F1-F4
  References: 审计范围 = 上述 9 文件；分类先例 = 本会话审计 + explore 报告；C1 清单 = todo 1（固定 8 + 枚举增量，预期 10 个唯一路径）；ph9-probe 磁盘全集 = todo 0 枚举（预期 8：6 tracked + 2 ingested）；narrative-only 名单见 (5)
  Acceptance criteria (agent-executable): evidence 含逐引用分类表 + 残余检查表；存在判定行 `UNRESOLVED: 0` 与 `RESIDUAL-CHECK: clean`；README 断言复跑输出落档；`RESULT: PASS` 行存在。
  QA scenarios: happy=UNRESOLVED=0 + 残余零遗漏全表落档；failure=发现未跟踪引用或 evidence/ 行未被捕获 → STOP + 缺口清单（不静默）。Evidence `.omo/evidence/task-5-evidence-integrity-and-readme-status.txt`
  Commit: Y | test(evidence): full ledger citation audit — all references resolvable

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — 6 todos 逐条：evidence 存在、acceptance grep 断言全过、`Commit:` 行与实际提交一一对应、无 silent skip。**可执行化（Momus-M2 + Oracle-R2-A3）**：对每个 todo N——由 `git log --oneline main..HEAD` 按预声明 `Commit:` message 定位该 todo 的 commit sha，HEAD 相关断言（如 `git show --stat`）一律用该 sha 定向执行（F1 时 HEAD 已前移，不能用 HEAD 复跑 todo 1 断言）；其余 grep/test 断言原样复跑并对照 `.omo/evidence/task-N-…txt`。
- [ ] F2. Doc quality review — README 快照数字与台账**逐格核对**（每行 `（来源：…）` 指向的文档中 grep 实际值，Momus-M2 可执行化：逐行抽取"数字声明→来源→grep 验证"三元组落档；**口径注 Momus-R3-A2："FM-SOC 33-case" 为 AGENTS.md 简称，README/`run_fm_soc_all.sh` 字面为 "FM-SOC-001..032 + FM-SOC-10X"——逐格核对时这非数字错误**）；台账修复诚实性（PERF-000 注记不得暗示 pre-fix diff，Metis #11；`bugs-archive.md:101` 引用行号正确，Oracle-A2）；范围蔓延检查（`git diff $(git merge-base main HEAD) HEAD --name-only` 无 Must-NOT 外文件）。
- [ ] F3. Real manual QA — **agent 可执行**（非人工浏览，Metis #12）：fresh 独立复跑 todo 5 审计脚本 + todo 4 的 README 结构断言（含 `（来源：` 增量口径），输出与 task-5 evidence 逐项一致。
- [ ] F4. Scope fidelity — `git diff $(git merge-base main HEAD) HEAD --name-only`（**Oracle-A5：merge-base 三点口径，防 main 前移导致 two-dot 漂移**）变更集 ⊆ {README.md, docs/bugs/*, build/evidence/*, **build/p1_full_rtl/evidence/**（Oracle-R3-F1：todo 1 入库的 FM-SOC-026.log 位于 `build/evidence/` 的**兄弟目录** `build/p1_full_rtl/evidence/`，`build/evidence/*` fnmatch 不匹配，原白名单会被自家变更集确定性击穿）, .omo/evidence/*, .omo/plans/*, .omo/drafts/*}，`sim/`/`rtl/`/`firmware/`/`scripts/`/`gen/`/`config/`/vendored 零命中（Metis #15）；每个提交的 `git show --name-only` 均不含 7 个 dirty 文件（Metis #6 + Oracle-A1 pathspec 纪律复核）；未 push；分支纪律（当前目录单一 worktree）。

## Commit strategy
- 一个 todo 一个原子 commit（type: chore/docs/test），message 预声明于各 todo `Commit:` 行；evidence 随对应 todo 一并入库
- todo 0 **先提交计划/draft 产物再拍 git-status 快照**（口径只含 7 个并行 dirty 文件）
- staging 纪律：只允许逐 todo 显式路径 `git add` / `git add -f`；**禁止 `git add .`、`git add -A`、`git commit -a`**
- **并行波提交纪律（Oracle-A1）**：Wave 2 各 todo 提交必须带 pathspec —— `git commit -m "<msg>" -- <本 todo 显式路径列表>`，且提交前断言 `git diff --cached --name-only` 输出恰好等于本 todo 文件清单（防止并行 agent 互相卷入对方 staged 文件或 index.lock 竞争破坏原子性）
- 全部 todo + F1-F4 APPROVE + 用户 explicit okay 后 `--no-ff` merge 回 main；**不自动 push**

## Success criteria
1. 入库全集（固定 8 + 枚举 untracked ph9-probe，预期 **10 个唯一路径**）全部 tracked（`git ls-files --error-unmatch` 逐个 exit 0）
2. 台账零死引用/零假 sha：`grep -c "task-7-p1-full-rtl" docs/bugs/bugs-soc-rtl.md` == 0 且 `grep -c "CaduceusCore/build/p1_full_rtl" docs/bugs/bugs-soc-rtl.md` == 0 且 `grep -c "a1b2c3d4" docs/bugs/bugs-module-level.md` == 0；`675afe0a` / `513fba6` 落位；module-level Stats = Total 4 / Fixed 3 / Open 1
3. README「项目状态总览」落位（标题行号 ∈ [13,15]，空行分隔完好），`（来源：` 增量 ≥9，快照数字与台账逐格一致，来源格路径全部 `test -f` 通过，blocker 8 项全列
4. C4 审计 `UNRESOLVED: 0` **且** `RESIDUAL-CHECK: clean`（修正版正则 + 磁盘 glob 展开 + 含 `evidence/` 行全捕获/白名单），逐引用分类表落档于 task-5 evidence
5. F1-F4 全 APPROVE + 用户 okay；`git diff $(git merge-base main HEAD) HEAD --name-only` 变更集不含 sim/rtl/firmware/scripts/gen/config/vendored；每个提交 name-only 均不含 7 个并行 dirty 文件
