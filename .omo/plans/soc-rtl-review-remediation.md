# soc-rtl-review-remediation - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 一份针对外部评审报告的整改：先为每个假阳性路径写"必然失败"的负向测试，再根治两个真实硬件缺陷（总线并发死锁、固件地址越界），把回归统计改成真实的"25 执行 + 6 废弃 + 2 不适用"四类口径，给所有证据绑定完整来源哈希，最后在干净代码状态上重跑全部回归并真实执行最终验收——不再有"超时算成功""空跑算通过"这类假阳性。

**Why this approach:** 评审指出的问题本质是"测试太容易假通过"——超时被转成成功、废弃用例被算成通过、干跑被算成真实执行。TDD 反向操作（先写必然失败的测试证明缺陷存在，再修复）保证每个修复都真正消除了缺陷，而不是又绕过了它。修复硬件缺陷（总线死锁、固件越界）是根治，只改证据记录只是粉饰。

**What it will NOT do:** 不执行评审列出的项目级遗留项（性能校准、FPGA、内存超限优化、36 层连续仿真）——这些单独跟踪为清单；不签署 8MB 内存窗口豁免（那是需要你亲笔签字的动作）；不修改冻结的性能规格和第三方 IP；不开新 worktree——先把你当前目录未提交的修改分类提交（不丢弃任何内容），再就地执行。

**Effort:** Large
**Risk:** Medium — 总线协议修复可能影响全部 33 个用例（有自动回滚门保护）；固件地址白名单可能误拒合法访问（先审计后修改）。
**Decisions to sanity-check:** (1) 总线修复方案已定为 accept/grant 耦合（option a 强制、option b 禁用）——请确认该决定；(2) 真实外设寄存器一致性检查如果做不全，是否接受降级声明为"路由检查 + 部分外设语义"；(3) Spike 数值差异若 1 天内修不好，接受正式记录为 bug 而非静默通过。

Your next move: approve to start execution, or request a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): Large effort, Medium risk — 21 todos in 5 waves: W0 in-place workspace prep (resolve dirty state in current dir, no worktree) → W1 negative tests RED (6 groups) → W2 fixes GREEN (crossbar/firmware/runner/10X/provenance/APB) → W3 clean-commit full re-run + real F1-F4 → W4 doc/status unification + P2/P3 blocker list. Delivers honest 25+6+2 accounting, fail-closed runners, hash-bound evidence, and two real bug fixes; excludes P2/P3 execution.

## Scope
### Must have
1. **RTL 修复**：crossbar accept/grant 协议修复，消除 phantom-accept deadlock（`rtl/soc/axi_crossbar.v`）——两个 master 在同 slave-free cycle 同时断言 VALID 时，只能有一个被 accept 并 grant，另一个必须保持 VALID 等待（或收到 DECERR），不得出现 accept 后永久等待。
2. **固件修复**：`firmware/npu_firmware.c` (a) 地址 allowlist——`dram_range_ok()` 改为白名单语义（仅 SRAM 窗口 + DRAM 8MB 窗口合法，ROM/空洞/MMIO 拒绝）；(b) MMUL/SFU/Vector/DMA dispatch 校验实际所需字节数（由 M/K/N/tile 推导）不超过 descriptor 声明 size；(c) completion-status 写入越界修复——ring head ≥1019 时 status 索引不得超过 ABI 定义的 completion 区域（不得溢出写 INTC APB 窗口）。
3. **测试基础设施加固**：(a) fairness TB 改为真实并发竞争（7 master 同周期持续竞争同一 slave，无死锁、grant 差 ≤1、全部事务 OKAY）+ 固定优先级仲裁器 mutation 必须 FAIL；(b) APB conformance 连接真实 7 外设 RTL（或按评审建议显式降级声明为 decoder routing test）；(c) FM-SOC-10X `_verify_10X` 补齐 17-op 全验证（移除 `idx > corrupt_op_idx` 截断或显式改报告口径）。
4. **Runner fail-closed**：(a) `run_ibex_segment_run.sh` timeout 必须非零退出（exit 124/137），`SEG_TIMEOUT_S` 严格正整数校验；(b) 全量回归统计口径：33 个 case 结构化统计为 25 执行 / 6 superseded（FM-SOC-014/015/016/021/022/023）/ 2 N/A（FM-SOC-017/019），SKIP grep 与 cocotb 消息统一，`|| true` 改为退出码传播；(c) Makefile targets 加 `set -o pipefail` / `PIPESTATUS` 检查，simulator 失败必须传播；(d) 旧 evidence 不得满足新运行（evidence 写入前清空或带 run ID 校验）。
5. **Evidence provenance**：证据文件绑定 hash 集合（git HEAD + dirty state、simv 路径+时间戳、RTL flist 内容 hash、Python driver hash、firmware ELF/HEX hash、golden/checkpoint hash）；checkpoint resume 的 `allow_pickle=True` 改为安全方案（`.npy` 内存映射或 pickle 白名单校验）。
6. **真实重新验证**：干净 commit fresh build + 全量 33-case 回归重跑（真实统计口径）+ 真实执行 F1-F4（禁止 DRY-RUN/DEFERRED 自动转 PASS）。F3 真实执行 pytest + Spike smoke + sz0001 spot checks；Spike L0 Q_proj FAIL（max_diff=7.64e+02）必须如实记录为 FAIL 并修复或明确声明为已知限制。
7. **文档与状态一致性**：`docs/func-model-signoff-checklist.md` 顶部 Performance 状态与正文/实际 calibration 状态统一；BUG-RTL-SOC-007 ledger 文本更新为"todo 15 已执行、未复现、根因仍未知"；WVR-SOC-RTL-002 保持 Pending 直到用户签署（ledger 同步改回 Pending）；vplan 的 33/33 口径改为 25+6+2；`docs/soc-rtl-verification-feature-status.csv` 纳入版本控制。
8. **P2/P3 blocker 跟踪清单**：输出 `docs/soc-rtl-review-remediation-blockers.md` 列出 8 项（详见 todo 20）：Perf CI RSS 17.4GB、E2E-07 校准（引用 `.omo/plans/rtl-perf-decomposition-calibration.md`）、FPGA L5 + ggml、36 层连续、waiver 签署、BUG-007 根因追查、P3.1 遗留工作区状态清理、P3.4 可重放 signoff manifest/用户签收——本计划只跟踪不执行。

### Must NOT have (guardrails, anti-slop, scope boundaries)
1. **不执行 P2/P3 blocker**：Perf CI 内存优化、E2E-07 性能校准执行、FPGA/ggml 解除阻塞、36 层连续仿真均不在本计划执行——只写入 blocker 跟踪清单。
2. **不改 frozen perf spec**：`config/func_model_perf_spec_v1.json`、`func_model_perf_matrix_v1.json` 保持不变（与 E2E-07 无关的本计划不触碰）。
3. **不改 vendored IP**：crossbar 修复只改 `rtl/soc/axi_crossbar.v`；不碰 `rtl/cpu/ibex/`、`rtl/ip/verilog-axi/`、`rtl/ip/verilog-pcie/`。
4. **不改引擎 internals**：`rtl/mxu|sfu|vector/*` 的 `*_top.v` 内部不动；修复只在外围（crossbar、firmware、TB、runner、evidence）。
5. **不修 Spike 数值差异**：F3 中 Spike L0 Q_proj max_diff=7.64e+02 若无法在限定时间内修复，如实记录为 FAIL/已知限制，不得静默转 PASS。
6. **不 claim 完整 signoff**：本计划结束只 claim"评审 P0/P1 整改完成 + 干净环境回归重跑通过"；完整 SoC RTL signoff 仍需 E2E-07 校准（P2）与 waiver 签署。
7. **不改既有计划 rtl-perf-decomposition-calibration**：E2E-07 引用它，不复制不修改它的 todos。
8. **就地执行 + 分支规则（2026-08-30 用户指令，替代原"保留 dirty"条款）**：不开新 worktree；执行全部发生在当前目录。项目分支规则：新任务从干净工作区开始；**分支名 = plan 名**（本计划分支 `soc-rtl-review-remediation`，P0 把已存在的 `fix/soc-rtl-review-remediation` 重命名过来）；**plan 完成（F1-F4 全 APPROVE + 用户确认）后 `--no-ff` merge 到 main 并 push**。执行前必须先做 P0 工作区准备——8 个 evidence/notepad 文本 commit 保留（不丢弃任何内容，以 `git status --porcelain` 实况路径为准）、5 个已跟踪 firmware 构建产物还原 HEAD（P0 阶段不提交；todo 13 重建后按 Commit strategy 的 chore(firmware) 规则处理）、应入库未跟踪文件（CSV/2 份 plan/评审报告/AGENTS.md × 5/2 个 0 字节 notepad stub）commit 入库、87M simv 树等 build 产物加 .gitignore 不入库、已建 worktree 先 `status --porcelain` + `diff -r` 验证并备份差异文件（worktree 内存在 284 行旧版 plan 副本与独有 notepad stub，`--force` 会静默删除——Oracle round-5 实测）后再 `git worktree remove --force` 拆除；不动 stash@{0}；不修改 f982bef 的父历史；本计划 todos 的改动不得直接 commit 到 main。
9. **RTL/firmware 修改例外正式化**（Metis M1）：修改 `rtl/soc/axi_crossbar.v` 与 `firmware/npu_firmware.c` 前，先创建 `docs/waivers/REMEDIATION-RTL-EXCEPTION-2026-08-28.md`，列明两文件、用户批准记录、理由、撤销条件；F4 核对除这两文件外无其他 `rtl/` 产品代码改动。
10. **EDA 可用性约束**（Metis C2）：W3 全量回归与 F3 的 sz0001 项依赖 EDA server 可用；若 sz0001 不可用，F3 的 (c)(e) 项如实标记 BLOCKED 而非 PASS，全量回归顺延并在 evidence 记录。日历预算：W3 全量回归须在 5 个日历日内完成；单个 segment 触 24h timeout 记 TIMEOUT 不再无限重试（Metis M8）。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **TDD** — 每个假阳性路径先写负向测试（红），再修复（绿）。负向测试载体：bash 测试脚本（runner 行为）、SystemVerilog TB（crossbar/fairness/conformance）、cocotb case（FM-SOC 验证）、pytest（固件地址负例的 C 级模拟或 firmware 单测）。
- Evidence: `.omo/evidence/task-<N>-soc-rtl-review-remediation.<ext>`；RTL 回归 evidence 沿用 `build/evidence/`（git add -f）。
- EDA server：所有 VCS 编译/仿真必须 SSH 到 `zhengs@192.168.0.11`（`bash sim/regression/soc-verification-run.sh <target>` 自动转发）。
- 负向测试断言清单（红）：(1) timeout=124 退出码非零；(2) `SEG_TIMEOUT_S=--help` 必须报错退出；(3) 固定优先级 mutation 的 fairness TB 必须 FAIL；(4) 真实并发 7-master 竞争无死锁且 grant 差 ≤1；(5) 固件访问 ROM/hole/MMIO 地址必须拒绝（status=1）；(6) completion index ≥ 上限必须被钳制；(7) 旧 evidence 存在时 runner 必须拒绝或覆盖而非追加；(8) superseded/N/A case 必须计入 SKIP 不计 PASS；(9) simulator 非零退出码必须传播到 make target 退出码。
- 统计口径：全量回归 summary 必须输出 `PASS=<实际执行通过数> SKIP=<superseded+N/A> FAIL=<n> TIMEOUT=<n>` 四类，PASS+SKIP+FAIL+TIMEOUT = 33。
- 红转绿证据：每个 todo 的 QA failure 场景先在修复前执行并记录红结果，修复后重跑记录绿结果，两条证据都写入 `.omo/evidence/`。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

**Wave 0 (W0): 工作区准备（就地执行前置）** — 1 todo。分类提交脏状态 → 拆除已建 worktree → 分支重命名为 plan 名 `soc-rtl-review-remediation`（== main）并就地 checkout → docs 入库 + .gitignore。本地，无 EDA 依赖。所有后续波次的前提。
**Wave 1 (W1): 负向测试先行（红）** — 6 todos。先写并跑红：timeout 行为、crossbar 真实竞争 + mutation、APB 真实外设、统计口径、固件地址负例、evidence provenance 负例。EDA server 必需（VCS）。
**Wave 2 (W2): 修复（绿）** — 6 todos。crossbar 协议修复、固件三项修复、runner fail-closed、FM-SOC-10X 全验证、evidence hash 绑定、APB 外设连接。依赖 W1 红测试。EDA server 必需。
**Wave 3 (W3): 重新验证** — 4 todos。干净 commit fresh build + 全量回归重跑、真实 F1-F4、红转绿汇总、Spike FAIL 处置。依赖 W2 修复。EDA server 必需。
**Wave 4 (W4): 文档与状态** — 4 todos。状态口径统一、BUG-007 ledger、waiver Pending、P2/P3 blocker 清单。本地。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 0 (P0 工作区准备) | — | 1-13（全部执行前提） | — |
| 1 (timeout 负测) | 0 | 9 | 2, 3, 4, 5, 6 |
| 2 (fairness 并发改造) | 0 | 7 | 1, 3, 4, 5, 6 |
| 3 (APB 真实外设负测) | 0 | 12 | 1, 2, 4, 5, 6 |
| 4 (统计口径负测) | 0 | 9 | 1, 2, 3, 5, 6 |
| 5 (固件地址负例) | 0 | 8 | 1, 2, 3, 4, 6 |
| 6 (evidence provenance 负测) | 0 | 11 | 1, 2, 3, 4, 5 |
| 7 (crossbar 修复) | 2 | 13 | 8, 12 |
| 8 (固件三项修复) | 5 | 13 | 7, 12 |
| 9 (runner fail-closed) | 1, 4 | 13 | 10, 11 |
| 10 (FM-SOC-10X 全验证) | 0 | 13 | 9, 11 |
| 11 (evidence hash 绑定) | 6 | 13 | 9, 10 |
| 12 (APB 外设连接) | 3 | 13 | 7, 8 |
| 13 (干净环境全量重跑) | 7, 8, 9, 10, 11, 12 | 14, 15 | — |
| 14 (真实 F1-F4) | 13 | 16 | 15 |
| 15 (红转绿汇总) | 13 | 16 | 14 |
| 16 (Spike FAIL 处置) | 13, 14, 15 | 17 | — |
| 17 (状态口径统一) | 16 | 19 | 18 |
| 18 (BUG-007 ledger) | 0 | 19 | 17 |
| 19 (waiver Pending) | 17, 18 | — | 20 |
| 20 (P2/P3 blocker 清单) | 0 | — | 19 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 0: 工作区准备（就地执行前置）

- [x] 0. P0 工作区准备：分类提交脏状态 + 按项目分支规则建立任务分支（2026-08-30 用户指令修订）
  What to do / Must NOT do: 在**当前目录**（主 checkout，不开新 worktree）按顺序执行——项目分支规则（用户 2026-08-30 定）：新任务从干净工作区开始；**分支名 = plan 名**；plan 完成后 merge 到 main。(a) 8 个 evidence/notepad 文本（5 个 `.omo/evidence`+notepads：task-0-signoff-v3-runner.txt、task-20-uncertainty-kpis.json、task-23-perf-spec-ci.txt、fm-e2e-qwen-cv-software-stack/learnings.md、phase6-rtl-verification/learnings.md + 3 个 `build/evidence`：fm-cv-chain.txt、task-F3-spike-smoke.log、w3-4-mobilenetv3-fm.txt——执行时以 `git status --porcelain` 实况为准，枚举命令：`git status --porcelain | awk '/^ M/ && ($2 ~ /^\.omo\/(evidence|notepads)/ || $2 ~ /^build\/evidence\//) {print $2}'`）commit 到当前分支 `fix/fm-soc-10x-sfu-desc`（消息 `chore(evidence): commit pending evidence and notepad updates`）；(b) 5 个已跟踪 `firmware/build/npu_*` 构建产物（npu_firmware.elf/.map/.o + npu_firmware_spike.elf/.map）**不进入 (a) 的 commit**，`git checkout -- firmware/build/` 还原 HEAD——注意这只是防止混入 (a) 的安全门，(e) 切分支后它们会再次变为 f982bef 版本（正常）；(c) 拆除已建 worktree——**先验证再强制**（Oracle 审查实测：worktree 内 `.omo/plans/soc-rtl-review-remediation.md` 是 284 行旧版、`.omo/notepads/soc-rtl-review-remediation/` 4 个 0 字节 stub 为 worktree 独有，`--force` 会静默删除，不得依赖"字节相同"假设）：先 `git -C /home/prj/zhengs/caduceuscore/CaduceusCore-wt-soc-rtl-review-remediation status --porcelain` 与 `diff -r` 对照主目录，把差异化文件备份到 `.omo/drafts/worktree-backup-2026-08-30/`（gitignored），在 evidence 记录差异清单，然后 `git worktree remove --force <该路径>`；(d) `git branch -m fix/soc-rtl-review-remediation soc-rtl-review-remediation`——分支名改为 plan 名（== f982bef == main tip，满足"从干净 main 开始"）；rename 前加 guard：`git show-ref --verify --quiet refs/heads/soc-rtl-review-remediation && { echo "目标分支已存在，停止询问用户"; exit 1; }`（注：先拆除 worktree 再 rename 更稳妥；git 2.34 实测 rename 会同步更新 worktree HEAD，两者顺序可互换，非"必须"）；(e) `git checkout soc-rtl-review-remediation` 就地切换（16 个未跟踪路径在 main 中均未被跟踪，实测零碰撞，随工作区带过来）；(g1) 提交前验证：`git rev-parse HEAD` == f982bef、`git branch --show-current` == soc-rtl-review-remediation、`git worktree list` 仅主目录、stash@{0} 原样保留；(f) 在任务分支上 commit 两笔：docs 入库（`docs/soc-rtl-verification-feature-status.csv`、`.omo/plans/soc-rtl-review-remediation.md`、`.omo/plans/rtl-perf-decomposition-calibration.md`、`reports/CaduceusCore-review-report-2026-08-28.md`、AGENTS.md × 5、`.omo/notepads/soc-rtl-verification-signoff/{decisions,problems}.md` 两个 0 字节 stub）→ `docs(remediation): track plan, review report, CSV and AGENTS knowledge base`；.gitignore 更新（`build/ibex_segment_rtl/`、`build/fw_dis.txt`、`build/hex_head.txt`、`build/hex_old.txt`、`sim/regression/verdi_config_file`）→ `chore(gitignore): ignore VCS build artifacts`（放任务分支而非 fix 分支，保证执行期 `git status` 干净）；(g2) 提交后验证：`git status --porcelain` 输出为空、`git merge-base --is-ancestor f982bef HEAD` 为真、87M build 产物未被 git 跟踪、firmware/build 5 个文件与 f982bef 版本一致（未改动）。Must NOT：丢弃任何 evidence/notepad 内容（全部经 commit 保留）；把 87M simv 树提交入库；pop/drop stash@{0}；force-push；改写 f982bef 父历史；把本计划 todos 的改动直接 commit 到 main。
  Parallelization: Wave 0 | Blocked by: — | Blocks: 1-13 | Can parallelize with: —
  References: explore 侦查 ses_face2ac5affe6Rw0agbtjF0iox（porcelain 列表、diff stat、worktree list、branch -a、main == f982bef）；round-5 Oracle 实测（worktree 旧版 plan 284 行、5 个 firmware 文件、main 未跟踪 16 路径零碰撞、rename 顺序可互换）；本计划 Must NOT #8 与 Commit strategy（2026-08-30 修订版）
  Acceptance criteria: (g1) 4 项与 (g2) 4 项检查全部通过；(a)(f) 的 commits 存在且消息符合所述；8 个 evidence/notepad 文本经 commit 保留（以实际 `git status --porcelain` 路径为准，不按固定数量断言——Oracle 审查：计划写 9/4 与实况 8/5 不符）；worktree 差异文件已备份且有证据记录；分支名 == soc-rtl-review-remediation（== plan 名，无前缀）。
  QA scenarios: happy — (g1)(g2) 8 项检查全过、无内容丢失、worktree 已拆除且差异已备份、分支名与 plan 名一致；failure — porcelain 非空、或 worktree 仍在、或分支名仍带 fix/ 前缀、或 stash 被改动、或有 evidence 文件消失且无 commit 记录、或 worktree 差异文件未备份即被删。Evidence `.omo/evidence/task-0-soc-rtl-review-remediation.txt`
  Commit: Y | chore(workspace): resolve dirty state in-place and set up plan-named task branch（本行由 P0 的 3 个 housekeeping commits 替代：chore(evidence) / docs(remediation) / chore(gitignore)，见 Commit strategy——round-6 Oracle 修正）

### Wave 1: 负向测试先行（红）

- [x] 1. Timeout 行为负向测试（红）：timeout 必须非零退出 + SEG_TIMEOUT_S 参数校验
  What to do / Must NOT do: 新建 `sim/regression/test_timeout_behavior.sh`，模拟 `run_ibex_segment_run.sh` 的 timeout 路径：(a) 用 `timeout --signal=TERM --kill-after=1 1 sleep 5` 复现 exit 124，断言脚本逻辑必须非零退出（当前 `run_ibex_segment_run.sh:68-79` exit 0 是红）；(b) `SEG_TIMEOUT_S=--help` / `abc` / `-5` 必须被拒绝（当前 :59 无校验）。Must NOT 修改 run_ibex_segment_run.sh 本体（W2 todo 9 才修）；只写测试并记录红结果。
  Parallelization: Wave 1 | Blocked by: 0 | Blocks: 9 | Can parallelize with: 2, 3, 4, 5, 6
  References: `sim/regression/run_ibex_segment_run.sh:59-79`（SEG_TIMEOUT_S 无校验 :59；RUN_RC=124 时 exit 0 :68-79）；评审报告 `reports/CaduceusCore-review-report-2026-08-28.md:72-84`（3.3 节）；GNU timeout 行为（124=TERM, 137=KILL, --help 退出 0 不执行命令）
  Acceptance criteria: `bash sim/regression/test_timeout_behavior.sh` 在修复前运行必须 RED（断言 exit 124 应映射为非零失败，但当前脚本逻辑 exit 0 → 测试 FAIL）；测试输出记录到 `.omo/evidence/task-1-soc-rtl-review-remediation.txt` 并包含 RED 标记。修复后（todo 9）重跑必须 GREEN。
  QA scenarios: happy — 测试脚本存在且修复前跑出 RED、修复后跑出 GREEN；failure — 测试脚本在修复前跑出 GREEN（说明测试没抓到 bug，测试无效）。Evidence `.omo/evidence/task-1-soc-rtl-review-remediation.txt`
  Commit: Y | test(regression): add negative test for timeout exit-code and SEG_TIMEOUT_S validation (RED)

- [x] 2. Crossbar 真实并发 fairness 负向测试（红）
  What to do / Must NOT do: 修改 `rtl/tb/axi_crossbar_fairness_tb.sv` 增加 P4 竞争 phase（Metis C1 修正：固定优先级 mutation 测试不能靠现有 sequential stimulus 触发——必须先加竞争 stimulus 本身）。P4 设计：(a) 7 个 master 在同一 slave-free cycle 同时断言 ARVALID/AWVALID 持续竞争同一 slave，断言修复前的未修改 RTL 出现 phantom-accept deadlock 或事务超时（RED）；(b) 竞争 phase 还应断言 grant 计数差 ≤1、全部事务 OKAY（此断言在修复前预期 RED、修复后 GREEN）。**P4 必须带事务 watchdog**（Oracle R3 审查）：phantom-accept 死锁表现为 hang，TB 需在 N cycle 内未完成即 FAIL（否则 RED 变成仿真挂起，只能靠 24h runner timeout 兜底、浪费 EDA 时间；建议 watchdog=10,000 cycle 与 todo 7 acceptance 的 ≥10,000 cycle 竞争窗口对齐）。watchdog 语义为**逐事务**（从该事务 VALID 被 accept 起算到 R/B 完成，正常 <100 cycle，10k 是 2-3 个数量级裕量；不得实现为"全局 N cycle 无完成"否则与 ≥10,000 cycle 竞争窗口冲突——Oracle R4 澄清）；watchdog 触发路径必须打印 "FAIRNESS: FAIL" 并 `$finish`（`run_crossbar_fairness` target 以 grep 该 marker 判定，`$finish` 退出码恒 0 无意义——Oracle R4 确认）。固定优先级仲裁器 mutation 测试移到 W2 todo 7（修复后作为回归守卫），不在本 todo。Must NOT 修改 `rtl/soc/axi_crossbar.v`（W2 todo 7 才修）；此 todo 只改 TB 并记录 RED（deadlock 复现）。
  Parallelization: Wave 1 | Blocked by: 0 | Blocks: 7 | Can parallelize with: 1, 3, 4, 5, 6
  References: `rtl/tb/axi_crossbar_fairness_tb.sv:10-24`（STIMULUS MODEL 注释：sequential rotation、phantom-accept deadlock 已知 out of scope）；`rtl/soc/axi_crossbar.v`（accept/grant 逻辑：m_arready_o = !m_ar_active && (!m_ar_hit || !ar_busy)）；`sim/regression/Makefile:242-251`（run_crossbar_fairness target）；评审报告 :41-55（3.1 节）
  Acceptance criteria: `make -C sim/regression run_crossbar_fairness`（经 `bash sim/regression/soc-verification-run.sh run_crossbar_fairness`）在 P4 竞争 phase 下对未修改 RTL 必须 RED（复现 phantom-accept deadlock 或事务超时）；修复后（todo 7）P4 转 GREEN。固定优先级 mutation 在 todo 7 验收（mutation 恒 RED）。
  QA scenarios: happy — P4 竞争 phase 修复前 RED（复现死锁）、修复后 GREEN；failure — P4 在修复前 GREEN（说明竞争 phase 没真正制造竞争）。Evidence `.omo/evidence/task-2-soc-rtl-review-remediation.txt`
  Commit: Y | test(rtl): add real-contention fairness phase exposing phantom-accept deadlock (RED)

- [x] 3. APB conformance 真实外设负向测试（红）
  What to do / Must NOT do: 新建 `rtl/tb/apb_conformance_real_tb.sv`：实例化真实 `apb_decoder` + 真实 7 个外设 RTL（mxu_soc_wrapper / sfu_soc_wrapper / vector_soc_wrapper / dma_wrapper / **pcie_ep_wrapper**（APB slave，注意不是 pcie_dma_wrapper——后者是 AXI master M6，`caduceus_soc_top.v:1258`）/ doorbell / intc_top，按 `caduceus_soc_top.v` 的 0x4000_0000 起的地址映射连接），用独立 regmap oracle（`gen/npu_abi_firmware.h` 的 NPU_ABI_* 常量）逐偏移验证 reset/RW/RO/W1C/hostile-write。当前 `apb_register_conformance_tb.sv:126-159` 用 7 个 `apb_conformance_slave` 模型（slv7 PCIE_DMA 还 SKIPPED）——记为该模型的 RED 限制。Must NOT 改 `apb_decoder.v`；只新增 TB。
  Parallelization: Wave 1 | Blocked by: 0 | Blocks: 12 | Can parallelize with: 1, 2, 4, 5, 6
  References: `rtl/tb/apb_register_conformance_tb.sv:100-159`（真实 decoder + 模型 slave）；`rtl/soc/caduceus_soc_top.v:41+`（外设实例化与地址映射参考）；`firmware/npu-regmap.h` / `gen/npu_abi_firmware.h`（寄存器语义 oracle）；`sim/regression/Makefile:156-166`（run_apb_conformance target）；评审报告 :57-70（3.2 节）
  Acceptance criteria: 新 TB 能编译（VCS）并运行，验证真实外设 reset 值/RW/RO 语义；对已知与模型 slave 期望表不一致的真实外设行为，测试必须暴露差异（RED）。记录哪些外设接入受阻（如 pcie_ep_wrapper 依赖复杂）及降级声明。
  QA scenarios: happy — 新 TB 编译运行，至少 5/7 外设接入并产生真实语义检查；failure — 新 TB 无法编译或全部 SKIP。Evidence `.omo/evidence/task-3-soc-rtl-review-remediation.txt`
  Commit: Y | test(rtl): add APB conformance TB against real peripheral RTL (RED)

- [x] 4. 全量回归统计口径负向测试（红）
  What to do / Must NOT do: 新建 `sim/regression/test_regression_stats.sh`：模拟 33-case 循环的判定逻辑，输入三种 case log（真实执行 PASS summary、superseded 消息、N/A 消息），断言：(a) `superseded by FM-SOC-027/032/10X`（`rtl_soc_runner.py:4279`）必须计 SKIP——当前 `run_ibex_full_rtl.sh:86` 的 grep 模式 `superseded by FM-SOC-032/10X` 匹配不到它（RED）；(b) `skipped: direct APB/AXI case not applicable to Ibex RTL mode`（:4282）必须计 SKIP；(c) 最终 summary 必须输出四类 PASS/SKIP/FAIL/TIMEOUT 且总和=33；(d) simulator 非零退出码不得被 `|| true` 吞掉（:85）。同时新建 case 状态清单 `docs/fm_soc_case_manifest.csv`（Metis M2）：33 行，列 = case_id / expected_status（EXECUTED|SUPERSEDED|N/A）/ justification。明确枚举：superseded = FM-SOC-014/015/016/021/022/023（6 个）；N/A = FM-SOC-017/019（DIRECT_CASES，`rtl_soc_runner.py:2174`）；其余 25 个 EXECUTED。Must NOT 改 run_ibex_full_rtl.sh（W2 todo 9 才修）。
  Parallelization: Wave 1 | Blocked by: 0 | Blocks: 9 | Can parallelize with: 1, 2, 3, 5, 6
  References: `sim/regression/run_ibex_full_rtl.sh:77-97`（`\|\| true` :85；SKIP grep :86-89；PASS grep :90-92）；`sim/rtl_soc_runner.py:4277-4282`（superseded 消息 :4279 "FM-SOC-027/032/10X" 与 N/A 消息 :4282）；`sim/rtl_soc_runner.py:2174`（DIRECT_CASES = {FM-SOC-017, FM-SOC-019}）；`sim/rtl_soc_runner.py:2781-2783`（P4Spike 的 superseded 消息 "FM-SOC-032/10X" 能匹配 :86，但 014/015/016 走 :4279 不能匹配）；评审报告 :86-107（3.4 节）
  Acceptance criteria: 测试在修复前运行必须 RED（模拟 014/015/016 的 log 被误计 PASS）；修复后 GREEN。用实际 case log 样本（`build/ibex_full_rtl/evidence/FM-SOC-014.log` 等）验证。
  QA scenarios: happy — 修复前 RED、修复后 GREEN，四类统计总和恒等于 33；failure — 修复前 GREEN（测试未抓到 grep 不匹配 bug）。Evidence `.omo/evidence/task-4-soc-rtl-review-remediation.txt`
  Commit: Y | test(regression): add negative test for superseded/N/A case classification (RED)

- [x] 5. 固件地址 allowlist 负例测试（红）
  What to do / Must NOT do: 新建 `sim/tests/test_firmware_addr_allowlist.py`（或扩展 `sim/tests/test_firmware.py`），用 RISCVMini/NPUFirmware 单测验证当前 `dram_range_ok()` 的缺陷：(a) 低于 DRAM_BASE 的地址（ROM 0x0000_0000、空洞 0x1000_0000、MMIO 0x4000_xxxx）当前全部返回 True（RED——应为拒绝）；(b) 构造 MMUL descriptor：声明 size 很小但 M/K/N 很大，实际访问量超 descriptor size（RED——应拒绝）；(c) completion-status 写入越界：**必须用 `ring_size=1024`（或 ≥1019）构造 NPUFirmware**（默认 `ring_size=16` 时 head 只绕 0..15，永远到不了 cmd_id≥16/1019，负例不会触发缺陷——Oracle R2 审查），驱动 head 到 ≥1019，断言镜像索引越出 [16] 数组写 INTC（RED）。Must NOT 改 firmware/npu_firmware.c（W2 todo 8 才修）。
  Parallelization: Wave 1 | Blocked by: 0 | Blocks: 8 | Can parallelize with: 1, 2, 3, 4, 6
  References: `firmware/npu_firmware.c:458`（dram_range_ok 只查 upper-bound）；`firmware/npu_firmware.c:668`（completion 写入）；`firmware/npu-regmap.h`（completion ABI 区域定义）；`sim/tests/test_firmware.py`、`sim/tests/test_firmware_boot_sequence.py`（现有 firmware 单测模式）；评审报告 :155-168（3.9 节）、:137-146（3.7 节）
  Acceptance criteria: `PYTHONPATH=sim python -m pytest sim/tests/test_firmware_addr_allowlist.py -q` 修复前 RED（暴露 (a)(b)(c) 三类缺陷）；修复后 GREEN。测试必须覆盖评审列出的负例：ROM/空洞/MMIO、near-end、undersized buffer、最大维度。
  QA scenarios: happy — 修复前 RED、修复后 GREEN，负例清单全覆盖；failure — 修复前 GREEN（负例未真正触发缺陷路径）。Evidence `.omo/evidence/task-5-soc-rtl-review-remediation.txt`
  Commit: Y | test(firmware): add negative tests for address allowlist, size validation, completion bounds (RED)

- [x] 6. Evidence provenance 负向测试（红）
  What to do / Must NOT do: 新建 `sim/regression/test_evidence_provenance.sh`：(a) 对 `run_ibex_segment_run.sh` 的 evidence 追加路径——预置一个含旧 "PASS" 的 `task-14-*.txt`，模拟 timeout 场景，断言 runner 不得把旧 evidence 当作本轮结果（RED：当前 :71-77 直接向已存在文件追加且 exit 0）；(b) 断言新证据文件必须包含本轮 commit + run ID + simv hash；(c) 对 checkpoint NPZ resume 路径（`sim/rtl_soc_segment_run.py`）断言不允许 `allow_pickle=True` 直接加载不受信本地文件。Must NOT 改脚本（W2 todo 11 才修）。
  Parallelization: Wave 1 | Blocked by: 0 | Blocks: 11 | Can parallelize with: 1, 2, 3, 4, 5
  References: `sim/regression/run_ibex_segment_run.sh:70-77`（旧 evidence 追加 + exit 0）；`build/evidence/task-14-soc-rtl-verification-signoff.txt`（现有 evidence 格式；其头部 commit 以文件实况为准，不引用历史 hash——round-6 Momus 修正）；`sim/rtl_soc_segment_run.py`（checkpoint resume、allow_pickle 用法）；评审报告 :169-184（3.10 节）
  Acceptance criteria: 测试脚本修复前 RED（旧 evidence 被当新结果、pickle 风险未防）；修复后 GREEN。覆盖：旧 evidence、PENDING evidence、损坏 NPZ、错误 commit 四个负场景。
  QA scenarios: happy — 修复前 RED、修复后 GREEN，四个负场景全覆盖；failure — 修复前 GREEN。Evidence `.omo/evidence/task-6-soc-rtl-review-remediation.txt`
  Commit: Y | test(regression): add negative tests for evidence provenance and pickle safety (RED)

### Wave 2: 修复（绿）

- [x] 7. Crossbar accept/grant 协议修复（phantom-accept deadlock）
  What to do / Must NOT do: 修改 `rtl/soc/axi_crossbar.v` 的 AR/AW accept 逻辑：当前 `m_arready_o = !m_ar_active && (!m_ar_hit || !ar_busy)` 允许 slave-free 时多个 master 同时被 accept，但 arbiter 只 grant 一个，其余永久等待。**修复方案必须采用 (a) accept 与 grant 耦合——只有将被 arbiter 选中的 master 才给出 ready（Mandate，Oracle 审查结论）**。方案 (b)（被 accept 未 grant 的 master N cycle 后收 DECERR）**明确禁用**：AXI4 规定 VALID/READY 握手完成后交易必须完成响应，DECERR 语义是"地址不可解码"而非仲裁失败；Ibex 会收到意外 bus-error exception；持续竞争下可能 livelock。**实现必须保留两条关键路径（Oracle R2 审查）**：(i) **DECERR 豁免**——非 hit master（`!m_aw_hit`/`!m_ar_hit`）必须保留无条件 ready（`!active` 即可，不参与仲裁搜索；当前 P2/P3 fairness phase 依赖"DECERR 立即 accept、绕过 grant credit"，收紧 ready 会导致未映射地址访问永久 hang 而非 bus error）；(ii) **完整 grant 窗口门控**——would-win 组合逻辑必须复制时序搜索的全部门控条件 `!aw_busy && !s_awvalid_latched && !aw_latch_clr`（仅依赖 valid/hit/slave/active 会导致 latch-clear 窗口内 ready 误断言 → 该 master 被 accept 置 active、后续搜索跳过它 → phantom-accept 经另一路径复活）。正确形态：`m_awready_o[gmi] = !active[gmi] && (!hit[gmi] || (grant_window_open[slave] && would_win[gmi]))`。组合环确认无风险：would-win 只依赖寄存器状态（busy/active/priority）+ 输入（valid/hit/addr），不依赖 ready；现有 grant latch 条件 `&& m_awready_o[gnt_val]`（:513）自洽（gnt_val 即 would-win master）。前置动作（Metis M1）：先创建 `docs/waivers/REMEDIATION-RTL-EXCEPTION-2026-08-28.md`。固定优先级 mutation 测试加入 `axi_crossbar_fairness_tb.sv`：DUT 参数化，fixed-priority 下 fairness 断言必须 FAIL。回滚门（Metis m4）：修复后全量回归中任何修复前 PASS 的 FM-SOC case 变为 FAIL，自动回滚此 commit 并 file blocker。Must NOT 改 vendored IP；Must NOT 改引擎 internals；Must NOT 破坏 P1-P3 已有 fairness/DECERR 断言。
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: 13 | Can parallelize with: 8, 12
  References: `rtl/soc/axi_crossbar.v`（accept/grant 逻辑、ar_granted/aw_granted/ar_busy/aw_busy 信号）；`rtl/tb/axi_crossbar_fairness_tb.sv:42-45`（probe 层次引用）；`sim/tests/test_crossbar_arbitration.py`（FM 仲裁算法参考）；评审报告 :41-55
  Acceptance criteria: `bash sim/regression/soc-verification-run.sh run_crossbar_fairness` → P4 竞争 phase 对修复后 RTL GREEN（≥10,000 cycle 真实并发竞争：0 死锁、grant 差 ≤1、全部事务 OKAY——Metis M6 可执行标准）；fixed-priority mutation 恒 FAIL fairness 断言；`run_crossbar_stress` 仍 PASS（1,260 txns 0 errors 不回退）；全量 FM-SOC 回归（todo 13）中 crossbar 相关 case 不回退（回滚门触发条件）。
  QA scenarios: happy — P4 GREEN + mutation RED + stress 不回退；failure — P4 仍 RED 或 stress 回退（修复引入新死锁/饥饿）。Evidence `.omo/evidence/task-7-soc-rtl-review-remediation.txt`
  Commit: Y | fix(rtl): couple crossbar accept to arbiter grant to eliminate phantom-accept deadlock

- [x] 8. 固件地址 allowlist + 实际 size 校验 + completion 越界修复
  What to do / Must NOT do: 修改 `firmware/npu_firmware.c`：(a) `dram_range_ok()` 改为白名单语义——合法区域仅 SRAM 窗口（0x2000_0000 起 4MB）与 DRAM 窗口（0x8000_0000 起 8MB，WVR-SOC-RTL-002 约束）；ROM/boot 区、地址空洞、0x4000_xxxx MMIO 一律拒绝（status=1）；(b) MMUL/SFU/Vector/DMA dispatch 前校验：由 M/K/N 与 tile 数推导的实际字节数 ≤ descriptor 声明的 input_size/weight_size/output_size，否则拒绝；推导字节数用 checked arithmetic（uint32 乘法/加法防溢出，溢出即拒绝——Oracle 审查补充的 3.9 项）；(c) completion-status 写入修复（Oracle 审查修正钳制边界）：`NPU_DB->COMPLETION_STATUS[]` 是 **16 条目** MMIO 镜像数组（`npu_doorbell_t` 0x14 偏移），cmd_id=1019 时写地址恰为 0x4000_6000（INTC base，越界开始点）；正确修复是钳制 MMIO 镜像索引到 `min(cmd_id, 15)` 或完全移除镜像写——DRAM completion ring（`COMPLETION_RING_ADDR + cmd_id*32`）保留完整 1024 条记录不受影响。Must NOT 只钳制到 1019（那仍会让索引 16..1018 越出声明的 [16] 数组边界）。**跨层一致性（Oracle R2/R3 审查）**：(i) `sim/spike_firmware.py:146` 的 Spike host 模型是**读取**镜像（`bridge._status.get(status_addr, 0)` 判定 done/error，非镜像写）——固件钳制后必须同步钳制 host 模型读取索引为 `min(ring_idx, 15)`（否则 ring_idx≥16 读缺失键默认 0 会掩盖真实错误）；(ii) `sim/tests/test_spike_ibex_ring_alignment.py` 的 `spike_trace["completion"]` fixture 直接读 bridge._status 未钳制偏移并断言 0..207 全部为 0——**两者必须同时改**（R3 审查：只钳 host 模型不修测试仍 FAIL，只修测试不改 host 模型会掩盖错误，不存在二选一）；测试期望改为：索引 0..15 反映钳制写，16..207 允许缺失；**同步更新 `test_spike_ibex_ring_alignment.py` 的 docstring**（原断言"COMPLETION_STATUS 数组 per dispatch index 0..207 各写一次"修复后对 Spike 路径不再成立——Oracle R4）；evidence 记录最终语义。无损替代方案（推荐作为 alignment 测试主路径，Oracle R4）：host 模型与测试改读 DRAM completion ring（`COMPLETION_RING_ADDR + cmd_id*32`，固件写入含 cmd_id 字段且不钳制）获得完整 1024 条状态——断言 `entry.cmd_id == index && entry.status == 0` 反而比原镜像断言更强（同时验证 cmd_id 字段）；host 读 model.dram 即可（fixture 已有 model.dram 访问）。若选镜像钳制默认路径，注意 16 槽镜像对 >16 条链有损（cmd 200 的错误会被 cmd 207 的成功覆盖），DRAM ring 是无损唯一方案；(iii) `sim/rtl_soc_runner.py:4247-4253` RING-WRAP-STRESS 的 KNOWN-BEHAVIOR 日志（"mirror writes dropped / i≥1019 spill into INTC"）修复后过时，需同步更新为钳制后行为。兼容性门（Metis M5）：修改前先审计全部 FM-SOC case 与固件路径是否有 descriptor 地址落在 SRAM/DRAM 之外（含 boot 流程）；若存在合法低地址访问，白名单显式放行并记录。修复后跑 todo 5 负例转 GREEN。Must NOT 改 descriptor ABI 布局（保持 15-word packed struct）；Must NOT 放宽 8MB DRAM 窗口（WVR-SOC-RTL-002 未解除）。
  Parallelization: Wave 2 | Blocked by: 5 | Blocks: 13 | Can parallelize with: 7, 12
  References: `firmware/npu_firmware.c:458`（dram_range_ok）；`firmware/npu_firmware.c:37-158`（descriptor struct 与 size 字段）；`firmware/npu_firmware.c:660-680`（dispatch 循环与 completion 写入）；`firmware/npu-regmap.h:287-315`（_Static_assert ABI 检查）；`docs/waivers/WVR-SOC-RTL-002.md`（8MB 窗口约束）；评审报告 :137-168（3.7、3.9 节）
  Acceptance criteria: `PYTHONPATH=sim python -m pytest sim/tests/test_firmware_addr_allowlist.py -q` GREEN；`make -C firmware` 编译通过且 `_Static_assert` 全过；FM-SOC 回归中 doorbell/ring 相关 case（FM-SOC-006、RING-WRAP-STRESS）不回退；completion 镜像索引 ≤15 且 DRAM ring 完整保留 1024 条记录；补充 wrap 过程中仍依赖 IRQ/WFI 的场景测试（评审 3.7 项：ENABLE 门控 + WFI 唤醒在 wrap 后仍正常工作）。
  QA scenarios: happy — 负例全 GREEN + firmware 编译过 + ring-wrap 不回退；failure — 负例仍 RED 或正常 dispatch 被误拒（allowlist 过严）。Evidence `.omo/evidence/task-8-soc-rtl-review-remediation.txt`
  Commit: Y | fix(firmware): add address allowlist, actual-size validation, and completion-status bounds

- [x] 9. Runner fail-closed：timeout 非零退出 + pipefail + 结构化统计
  What to do / Must NOT do: 修改 (a) `sim/regression/run_ibex_segment_run.sh`：`SEG_TIMEOUT_S` 严格正整数校验（非法值立即报错退出 2）；timeout 124/137 时写 TIMEOUT evidence 并 `exit 124`（或专用非零码），绝不 exit 0；evidence 不追加到旧文件——写 `task-14-soc-rtl-verification-signoff-<RUN_ID>.txt` 新文件或先备份；(b) `sim/regression/run_ibex_full_rtl.sh`：移除 `|| true`，改为捕获退出码（0=PASS、非 0=FAIL、124=TIMEOUT）；SKIP 判定改为检查 cocotb 结构化消息，统一 `rtl_soc_runner.py:4279/4282` 的 superseded/N/A 消息与 grep 模式；summary 输出四类统计；(c) `sim/regression/Makefile` 的 cocotb 类 target 加 `set -o pipefail`，`tee` 后检查 `PIPESTATUS[0]`，simulator 非零退出必须让 make target 失败；(d) 新建 `scripts/audit_fm_soc_statistics.py`（Metis M7）：解析全部 33 个 case log → JSON `{executed: N, superseded: N, na: N, failed: N, timeout: N}`，与 todo 4 的 `docs/fm_soc_case_manifest.csv` 核对，不一致即失败。Must NOT 改 Makefile 之外的 build 语义；Must NOT 破坏 `soc-verification-run.sh` 的 SSH 转发契约（退出码必须透传）。
  Parallelization: Wave 2 | Blocked by: 1, 4 | Blocks: 13 | Can parallelize with: 10, 11
  References: `sim/regression/run_ibex_segment_run.sh:59-79`；`sim/regression/run_ibex_full_rtl.sh:77-97,108-110`；`sim/regression/Makefile:1028-1042`（run_fm_soc_case 的 tee 模式）、`876-885`（run_e2e_mobilenetv3 的 tee 模式）；`sim/rtl_soc_runner.py:4277-4282,2174`（superseded/N/A 消息源）；评审报告 :72-107（3.3、3.4 节）、:129-136（3.6 节）
  Acceptance criteria: todo 1 与 todo 4 的负向测试转 GREEN；`SEG_TIMEOUT_S=--help bash sim/regression/run_ibex_segment_run.sh` 退出码 2；模拟 124 的测试退出码 124；全量回归 summary 显示 `PASS=<n> SKIP=8 FAIL=0 TIMEOUT=0`（8 = 6 superseded + 2 N/A）；make target 在 simulator 崩溃时返回非零。
  QA scenarios: happy — 红测试全 GREEN + summary 四类统计正确；failure — 任一负向测试仍 RED 或 PASS 计数包含 superseded。Evidence `.omo/evidence/task-9-soc-rtl-review-remediation.txt`
  Commit: Y | fix(regression): make runners fail-closed with structured PASS/SKIP/FAIL/TIMEOUT accounting

- [x] 10. FM-SOC-10X 补齐 17-op 全验证
  What to do / Must NOT do: 修改 `sim/rtl_soc_runner.py` `_verify_10X`（:3607-3641）：移除 `if idx > corrupt_op_idx: continue` 截断，验证全部 17 个 op（corrupt op 之后的 op 用其对应 golden 正常比对，corrupt op 本身保持 anti-vacuous 检查）；或按评审建议显式降级——若全验证不可行（超时/数据缺失），把返回消息改为 "前 N op + 因果回归 PASS（其余 op 未验证）"，不得再返回 "17-op blk.0 chain PASS"。必须选择显式方案并在 evidence 记录。Must NOT 改 _build_10X 的构造逻辑（corruption 注入机制保留）。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 13 | Can parallelize with: 9, 11
  References: `sim/rtl_soc_runner.py:3607-3641`（_verify_10X 截断与返回消息）；`sim/rtl_soc_runner.py:3590-3605`（_run_10X）；`build/evidence/task-16-soc-rtl-verification-signoff.txt`（当前 10X 证据）；评审报告 :147-153（3.8 节）
  Acceptance criteria: `bash sim/regression/soc-verification-run.sh run_fm_soc_case` with CASE_ID=FM-SOC-10X（经 run_fm_soc_all.sh FM-SOC-10X）→ 验证全部 17 op 且日志显示 per-op 结果；若降级方案，返回消息与 evidence 明确写 "仅前 N op 验证"。任一路径下 "17-op chain PASS" 字样只在全部 17 op 均验证时出现。
  QA scenarios: happy — 17 op 全验证 PASS 或显式降级声明；failure — 仍返回 "17-op chain PASS" 但存在 `idx > corrupt_op_idx` 跳过。Evidence `.omo/evidence/task-10-soc-rtl-review-remediation.txt`
  Commit: Y | fix(sim): verify all 17 ops in FM-SOC-10X or declare scope honestly

- [x] 11. Evidence provenance hash 绑定 + checkpoint pickle 安全
  What to do / Must NOT do: (a) 新建 `scripts/gen_evidence_provenance.py`：为一次 RTL 运行生成 provenance block（git HEAD + `git status --porcelain` dirty state、simv 路径 + sha256、RTL flist 内容 sha256、Python driver 文件 sha256、firmware ELF/HEX sha256、golden/checkpoint 文件 sha256、工具版本——VCS `vcs -ID`、cocotb、Python、riscv64-unknown-elf-gcc、GNU timeout、Spike——Metis m3、时间戳），SHA-256 算法，写入 evidence 文件头部；(b) `run_ibex_segment_run.sh` 与 `run_ibex_full_rtl.sh` 调用它并把 provenance 写入每个 case log/evidence；(c) 新建 `scripts/check_evidence_provenance.py`：验证 evidence 头存在且 hash 与当前构建一致，缺失/不一致即失败（Metis C3 可执行定义）；(d) `sim/rtl_soc_segment_run.py` 的 checkpoint resume 移除 `allow_pickle=True` 直接加载——改为 `.npy`/`np.load(..., allow_pickle=False)` 或 pickle 内容结构白名单校验（先校验 magic/字段类型再 load）。Must NOT 把 evidence 目录纳入常规 git（仍用 `git add -f` 提需留存的证据）。
  Parallelization: Wave 2 | Blocked by: 6 | Blocks: 13 | Can parallelize with: 9, 10
  References: `sim/regression/run_ibex_segment_run.sh:70-77`（evidence 追加）；`build/evidence/task-14-soc-rtl-verification-signoff.txt:1-5`（现有 evidence 头格式）；`sim/rtl_soc_segment_run.py`（checkpoint resume 代码）；`docs/agentic-ic-verification-research-report-2026-07-17.md`（hash 绑定方法论参考）；评审报告 :169-184（3.10 节）
  Acceptance criteria: todo 6 负向测试 GREEN；provenance 脚本输出含全部 8 类 hash；重跑 36 层 checkpoint 时 evidence 头部含本轮 commit + dirty state + simv/firmware/golden hash；损坏/伪造 NPZ 被拒绝加载。
  QA scenarios: happy — provenance block 完整 + pickle 负例被拒；failure — evidence 仍可来自旧运行或 pickle 任意加载。Evidence `.omo/evidence/task-11-soc-rtl-review-remediation.txt`
  Commit: Y | feat(scripts): add evidence provenance hash binding and safe checkpoint resume

- [x] 12. APB conformance 连接真实外设（承接 todo 3 红测试）
  What to do / Must NOT do: 完善 `rtl/tb/apb_conformance_real_tb.sv`（todo 3 建的骨架）：分两步（Metis C4 修正）——第一步 prototype：先只接一个真实外设（INTC 优先，寄存器语义最独立），对照独立 oracle 验证 reset/RW/RO；第二步扩展接入 mxu_soc_wrapper/sfu_soc_wrapper/vector_soc_wrapper/dma_wrapper/doorbell（pcie_ep_wrapper 若接入受阻则显式声明未覆盖），用 `gen/npu_abi_firmware.h` 常量作为独立 oracle 检查 reset/RW/RO/W1C；在 Makefile 加 `run_apb_conformance_real` target（走 soc-verification-run.sh）。回滚门（Metis C4）：若真实外设接入需要改外设 RTL 或导致现有 168/168 模型版 conformance 失效，降级为 "decoder routing + N/7 peripheral semantics"（N=实际接入数），vplan 同步更新。Must NOT 改外设 RTL 本身；发现的真实外设语义 bug 记录到 `docs/bugs/`。
  Parallelization: Wave 2 | Blocked by: 3 | Blocks: 13 | Can parallelize with: 7, 8
  References: `rtl/tb/apb_register_conformance_tb.sv:124-159`（模型 slave 现状）；`rtl/soc/caduceus_soc_top.v`（地址映射与外设例化）；`rtl/wrapper/*_soc_wrapper.v`（各外设寄存器表）；`gen/npu_abi_firmware.h`；`sim/regression/Makefile:156-166`；评审报告 :57-70
  Acceptance criteria: `bash sim/regression/soc-verification-run.sh run_apb_conformance_real` PASS，覆盖 ≥5 外设真实 reset/RW/RO 语义；未接入外设在 evidence 与 vplan 显式声明；`run_apb_conformance`（模型版）保留但声明降级。
  QA scenarios: happy — 真实外设语义检查 PASS 且声明口径一致；failure — 外设接入数 <5 且无显式降级声明。Evidence `.omo/evidence/task-12-soc-rtl-review-remediation.txt`
  Commit: Y | test(rtl): connect APB conformance to real peripheral RTL with independent oracle

### Wave 3: 重新验证

- [x] 13. 干净 commit fresh build + 全量回归重跑（真实统计口径）
  What to do / Must NOT do: 在 P0 清理后的干净工作区（**当前目录**，分支 `soc-rtl-review-remediation`，HEAD 为 f982bef（== main）的直系后代——f982bef + P0 的 2 笔 housekeeping + todos 1-12 修复提交链；`git status --porcelain` 运行前为空——2026-08-30 用户指令就地执行，不再用 worktree/stash 隔离）：`make -C firmware clean all` 重建固件 → **重建后若 `firmware/build/` 下已跟踪文件变更（5 个 npu_* 产物），commit 为 `chore(firmware): rebuild npu_* binaries after clean all`**（repo 先例 3e91c5ea；Oracle round-5：repo 跟踪这些二进制，不提交则 porcelain 非空、"干净 commit"闸门失败）→ **显式删除全量回归 simv**（`rm -f build/ibex_full_rtl/simv_soc_ibex build/ibex_full_rtl/simv_soc_ibex.daidir` 等——注意 `soc-verification-run.sh clean=1` 只删 `simv_soc_cocotb`，不覆盖全量回归的 `simv_soc_ibex`；`run_ibex_full_rtl.sh:49-68` 只在缺失时编译，必须显式删旧 simv 才真重编译——Oracle 审查修正 Metis M3）→ `bash sim/regression/run_ibex_full_rtl.sh` 跑全 33 case → 用 `scripts/audit_fm_soc_statistics.py` 对照 `docs/fm_soc_case_manifest.csv` 校验四类统计（PASS=25 目标、SKIP=8、FAIL=0、TIMEOUT=0）。同时重跑 crossbar fairness/stress、APB conformance real、FM-SOC-10X。日历预算（Metis M8）：全量回归 5 个日历日内完成；单 segment 触 24h timeout 记 TIMEOUT 不再无限重试。Must NOT 复用旧 evidence（todo 11 provenance 保证）；Must NOT 在 dirty 状态上跑——evidence 必须绑定**运行时实际 HEAD**（f982bef + todos 1-12 提交链，运行前 porcelain 为空；provenance 由 todo 11 记录实际 hash，而非字面 f982bef——Oracle round-5 修正）。
  Parallelization: Wave 3 | Blocked by: 7, 8, 9, 10, 11, 12 | Blocks: 14, 15 | Can parallelize with: —
  References: `sim/regression/run_ibex_full_rtl.sh`（33-case 入口）；`sim/regression/soc-verification-run.sh:28-49`（clean=1 强制重编译）；`firmware/Makefile`；评审报告 :270-296（P0/P1 整改优先级）
  Acceptance criteria: 全量回归在干净 commit 上完成：25 实际执行 case 全部 cocotb PASS（或失败如实记录），6 superseded + 2 N/A 计 SKIP，summary 四类总和=33；新 fairness/APB/10X 目标 PASS；evidence 头部含 provenance block（todo 11）。
  QA scenarios: happy — 干净 commit 全量回归四类统计正确且新目标全 PASS；failure — 出现 TIMEOUT/FAIL 未如实记录、或 SKIP 被计 PASS、或 evidence 无 provenance。Evidence `build/evidence/task-13-soc-rtl-review-remediation.txt`
  Commit: Y | test(regression): full 33-case regression re-run on clean commit with honest accounting

- [x] 14. 真实执行 F1-F4（禁止 DRY-RUN/DEFERRED 计 PASS）
  What to do / Must NOT do: 重新执行 final wave：(a) F1 plan compliance——本计划 20 todo 的证据逐条核对；**禁止 SKIP-ENV 自动计 PASS**（评审 6.6 项：EDA 依赖的 acceptance 若因环境跳过，标记 BLOCKED 而非 PASS）；(b) F2 code quality——新改的 crossbar/firmware/runner/TB 代码审查；(c) F3 real manual QA——真实执行 `PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q`（失败数如实记录，不得用 baseline 豁免）、真实 Spike mmul_smoke、真实 sz0001 spot checks（`bash sim/regression/soc-verification-run.sh run_e2e_blk0` 等）；(d) F4 scope fidelity——核对本计划 Must NOT 清单。EDA 可用性约束（Metis C2）：F3 中依赖 sz0001 的项（Spike smoke 与 sz0001 spot checks，即上述 (c) 项内的后两部分），若 sz0001 不可用如实标记 BLOCKED 而非 PASS。Spike 处置按 todo 16 的结果引用（修复 PASS 或正式 bug 条目），不得静默。任何无法执行项标记 INCONCLUSIVE/BLOCKED，绝不自动转 PASS。Must NOT 沿用旧 F3 evidence 的 "DRY-RUN → PASS" 模式。
  Parallelization: Wave 3 | Blocked by: 13 | Blocks: 16 | Can parallelize with: 15
  References: `build/evidence/task-F3-soc-rtl-verification-signoff.txt`（旧 F3 的 DRY-RUN 模式，反例）；`build/evidence/task-F3-spike-smoke.log`（Spike L0 Q_proj FAIL 记录）；`scripts/fm_hardening_f1_audit.sh`、`fm_hardening_f2_code_quality.sh`、`fm_hardening_f3_manual_qa.sh`、`fm_hardening_f4_scope_gate.sh`（F 波脚本入口，实际文件名带后缀——round-6 Momus 修正）；评审报告 :108-128（3.5 节）
  Acceptance criteria: 四个 F 波 evidence 均为真实执行结果；F3 的 pytest/Spike/sz0001 三项全部真实执行且 FAIL 项如实记录；F1-F4 结论为 APPROVE 或如实 INCONCLUSIVE；等待用户 explicit okay 后才声明完成。
  QA scenarios: happy — F1-F4 全部真实执行、FAIL 如实记录、结论可信；failure — 任一 F 波出现 DRY-RUN/DEFERRED 计 PASS。Evidence `.omo/evidence/task-14-soc-rtl-review-remediation.txt`
  Commit: Y | test(signoff): real-execution F1-F4 wave with honest INCONCLUSIVE/FAIL accounting

- [x] 15. 红转绿汇总表
  What to do / Must NOT do: 汇总 todo 1-6 的 6 组负向测试：输出 `docs/soc-rtl-review-remediation-rg-table.md`，每行含 负测名称 / 修复前 RED 证据路径 / 修复后 GREEN 证据路径 / 对应修复 todo / mutation 保持 RED 确认。Must NOT 声称"全部转绿"除非每条都有两条证据。
  Parallelization: Wave 3 | Blocked by: 13 | Blocks: 16 | Can parallelize with: 14
  References: `.omo/evidence/task-{1..12}-soc-rtl-review-remediation.txt`；`docs/agentic-ic-verification-plan-v0.2.md`（红绿证据方法论）
  Acceptance criteria: 汇总表覆盖 6 组负测，每组含 RED 与 GREEN 两条证据路径，全部 RED→GREEN 且 mutation 项保持 RED；表被提交到 git。
  QA scenarios: happy — 6/6 组 RED→GREEN 有双证据；failure — 缺任一证据或 mutation 变 GREEN。Evidence `docs/soc-rtl-review-remediation-rg-table.md`
  Commit: Y | docs(remediation): red-green summary table with paired evidence paths

- [x] 16. Spike FAIL 处置
  What to do / Must NOT do: 调查 F3 中 Spike L0 Q_proj FAIL（max_diff=7.64e+02，`build/evidence/task-F3-spike-smoke.log:5-13`）：(a) 若能快速定位根因（时间盒 1 工作日）——修复并重跑转 PASS；(b) 否则如实记录：bug 条目 `docs/bugs/bugs-soc-func-model.md`（或新 BUG-* 条目）写明场景、数值、影响面（3-layer forward pass 受影响），vplan E2E-03 标注 Spike FAIL 状态。Must NOT 静默标记 PASS；Must NOT 以 "baseline" 名义豁免。修复范围仅限 Spike bridge/host 侧（`sim/spike_host.py`、`sim/spike_rtl_bridge.py`），不碰 RTL。
  Parallelization: Wave 3 | Blocked by: 13 | Blocks: 17 | Can parallelize with: —
  References: `build/evidence/task-F3-spike-smoke.log:5-13`（FAIL 细节）；`sim/spike_host.py`（Spike host）；`docs/bugs/bugs-soc-func-model.md`（Func Model bug ledger）；`.omo/plans/soc-rtl-verification-vplan.md`（E2E-03 条目）；评审报告 :119-127
  Acceptance criteria: Spike L0 Q_proj 要么修复后 PASS（有新 evidence），要么 bug ledger 有正式条目且 vplan E2E-03 标注 FAIL；F3 evidence 引用真实结果。
  QA scenarios: happy — 修复 PASS 或正式 bug 条目 + 状态如实；failure — Spike FAIL 被静默或豁免。Evidence `.omo/evidence/task-16-soc-rtl-review-remediation.txt`
  Commit: Y | fix(sim): resolve Spike L0 Q_proj mismatch or file formal bug entry

### Wave 4: 文档与状态

- [x] 17. 状态口径统一（checklist/vplan/evidence）
  What to do / Must NOT do: (a) `docs/func-model-signoff-checklist.md` 顶部 Performance 状态与正文统一（明确 `calibration_state=uncalibrated`，Performance 相关行按实际证据写 PASS/FAIL/PARTIAL，消除自相矛盾）；(b) `.omo/plans/soc-rtl-verification-vplan.md` 的 33/33 口径改为 25 执行 + 6 superseded + 2 N/A，E2E-03 标 Spike FAIL 状态；(c) `docs/soc-rtl-verification-feature-status.csv` 纳入 git 跟踪并同步最新口径；(d) 交叉核对三处文档无矛盾。Must NOT 改 frozen perf spec；Must NOT claim 100% signoff。
  Parallelization: Wave 4 | Blocked by: 16 | Blocks: 19 | Can parallelize with: 18
  References: `docs/func-model-signoff-checklist.md`（状态矛盾处）；`.omo/plans/soc-rtl-verification-vplan.md:13-22,174-184`（覆盖率与 blocker）；`docs/soc-rtl-verification-feature-status.csv`（untracked 现状）；评审报告 :261-268（§6 文档一致性）
  Acceptance criteria: `grep -c "PASS" docs/func-model-signoff-checklist.md` 各 Performance 行与结论一致（用 checker 脚本或人工逐行核对记录）；vplan 含 "25 executed + 6 superseded + 2 N/A" 字样；CSV 已 `git add` 且内容与 vplan 一致。
  QA scenarios: happy — 三处文档口径一致且无 untracked 正式交付物；failure — 任一文档仍自相矛盾或 CSV 仍 untracked。Evidence `.omo/evidence/task-17-soc-rtl-review-remediation.txt`
  Commit: Y | docs(signoff): unify checklist/vplan/CSV status accounting

- [x] 18. BUG-RTL-SOC-007 ledger 文本更新
  What to do / Must NOT do: 更新 `docs/bugs/bugs-soc-rtl.md` 的 BUG-RTL-SOC-007 条目：把 "chain-level reproduction pending todo 15" 改为 "todo 15 ATTN-WEIGHT-CHAIN 已执行（2026-08-2x），26 命令 cycles>0、op07 attn_weight cycles=30755 cos=1.0，链级未复现；根因仍未知，保持 Open 待 FPGA/更早日志追踪"。同步 vplan 台账表。Must NOT 改 Status=Open 结论。
  Parallelization: Wave 4 | Blocked by: 0 | Blocks: 19 | Can parallelize with: 17
  References: `docs/bugs/bugs-soc-rtl.md:326-370`（007 条目现文本）；`build/evidence/task-15-soc-rtl-verification-signoff.txt`（ATTN-WEIGHT-CHAIN 证据）；`.omo/plans/soc-rtl-verification-vplan.md:165`（台账行）；评审报告 :218-220（4.5 节）
  Acceptance criteria: ledger 中 007 条目不再含 "pending todo 15" 字样；新增文本包含 todo 15 已执行、未复现、根因未知三点；Status 仍 Open。
  QA scenarios: happy — 文本更新且 Status 不变；failure — 仍写 pending 或误改为 Fixed。Evidence `.omo/evidence/task-18-soc-rtl-review-remediation.txt`
  Commit: Y | docs(bugs): update BUG-RTL-SOC-007 entry with todo-15 executed outcome

- [x] 19. WVR-SOC-RTL-002 保持 Pending
  What to do / Must NOT do: `docs/waivers/WVR-SOC-RTL-002.md` 保持 `pending sign-off`；`docs/bugs/bugs-soc-rtl.md` 台账把 BUG-RTL-SOC-002 从 "formally Waived" 改回 "Pending（waiver 待用户签署）"；`docs/func-model-signoff-checklist.md` 中 "not claimed as fixed" 表述同步。本 todo 不代用户签署。Must NOT 删除 waiver 文件或提前关闭。
  Parallelization: Wave 4 | Blocked by: 17, 18 | Blocks: — | Can parallelize with: 20
  References: `docs/waivers/WVR-SOC-RTL-002.md:11-12,53-57`（pending sign-off 与 closure 条件）；`docs/bugs/bugs-soc-rtl.md:164`（formally Waived 行）；评审报告 :222-228（4.6 节）
  Acceptance criteria: ledger 与 waiver 状态一致为 Pending；closure 条件（33/33 回归 + FPGA 扩窗 + 用户签署）原文保留；无任何文档称其已 Waived。
  QA scenarios: happy — 三处状态统一 Pending；failure — 任一处仍写 formally Waived 或已关闭。Evidence `.omo/evidence/task-19-soc-rtl-review-remediation.txt`
  Commit: Y | docs(waiver): revert BUG-RTL-SOC-002 ledger status to Pending until user signature

- [x] 20. P2/P3 blocker 跟踪清单
  What to do / Must NOT do: 新建 `docs/soc-rtl-review-remediation-blockers.md`：列出 P2/P3 未闭环项——(1) Perf CI RSS 17.4GB 超限（`.omo/evidence/task-23-perf-spec-ci.txt`；注明 gating 关系：该失败阻塞项目级 signoff 而非 SoC RTL 功能 signoff——Metis M10）；(2) E2E-07 性能校准（引用 `.omo/plans/rtl-perf-decomposition-calibration.md`，标注待执行）；(3) FPGA L5 NO-GO + ggml lifecycle BLOCKED（外部依赖）；(4) 36 层连续仿真 deferred 到 FPGA；(5) WVR-SOC-RTL-002 待用户签署；(6) BUG-RTL-SOC-007 根因追查；(7) P3.1 遗留工作区状态清理：stash@{0}（WIP on main: c244935，14 个 evidence 文件）处置 + `fix/fm-soc-10x-sfu-desc` 分支自身的收尾/合并 + 已 gitignore 的 87M build 产物磁盘清理（P0 已分类提交其余 dirty，本项仅跟踪剩余项）；(8) P3.4 可重放 signoff manifest + 最终用户签收记录（Oracle R4 审查：评审报告 §7 P3 的 4 项需全数可追溯）。每项含：现状证据路径、阻塞原因、解除条件、建议 owner。Must NOT 在本计划内执行这些项。
  Parallelization: Wave 4 | Blocked by: 0 | Blocks: — | Can parallelize with: 19
  References: `.omo/evidence/task-23-perf-spec-ci.txt`（RSS 超限证据）；`.omo/plans/rtl-perf-decomposition-calibration.md`（E2E-07 计划）；`docs/func-model-signoff-checklist.md:339`（FPGA/ggml BLOCKED 行）；评审报告 :186-228（§4 项目级事项）
  Acceptance criteria: 文件存在且覆盖 8 类 blocker，每项有证据路径 + 解除条件；被 git 跟踪；评审报告 §7 P0/P1 项在本计划 todos 中可映射、P2/P3 各项均能在 blocker 清单找到对应行。
  QA scenarios: happy — 8 类 blocker 全覆盖有证据引用；failure — 缺项或无解除条件。Evidence `.omo/evidence/task-20-soc-rtl-review-remediation.txt`
  Commit: Y | docs(remediation): P2/P3 blocker tracking checklist with evidence and closure criteria

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — 21 todos 逐条核对：evidence 存在、acceptance 满足、commit 已做；特别核对红测试均有 RED+GREEN 双证据（todo 15 汇总表）与 P0 的干净验证 + 分支名 == plan 名。
- [x] F2. Code quality review — 审查 crossbar 修复（无新死锁/饥饿路径）、固件 allowlist（无过严误拒）、runner fail-closed（无破坏 SSH 透传）、provenance 脚本（hash 计算正确）。
- [x] F3. Real manual QA — 真实执行 pytest 全量、Spike smoke、sz0001 spot checks；任何 FAIL/DRY-RUN 如实记录，绝不自动转 PASS。
- [x] F4. Scope fidelity — 核对 Must NOT 清单：未碰 frozen spec / vendored IP / 引擎 internals；未执行 P2/P3 项（只写了 blocker 清单）；未改 rtl-perf-decomposition-calibration 计划；未覆盖用户 dirty worktree 修改（P0 已分类提交保留）；无本计划改动直接 commit 到 main。

> 全部 F 波 APPROVE 且用户 explicit okay 后：执行 merge 到 main（`--no-ff`；前置 fetch/冲突处置见 Commit strategy 与 Success criteria #12）。

## Commit strategy
- **分支规则（用户 2026-08-30 定，项目级）**：新任务从干净工作区开始；新建分支，**分支名 = plan 名**（本计划为 `soc-rtl-review-remediation`，无前缀——P0 将已存在的 `fix/soc-rtl-review-remediation` 重命名过来，== f982bef == main tip，满足"从干净 main 开始"）；**plan 完成（F1-F4 全 APPROVE + 用户 explicit okay）后 `--no-ff` merge 到 main 并 push**，merge 前不删除任务分支。执行全程在当前目录，无 worktree（2026-08-30 用户指令）。
- **merge 收尾预检（Oracle/Momus round-5）**：merge 前先 `git fetch origin`，若 `git rev-parse origin/main` != f982bef **或** `git rev-parse main` != f982bef（本地/远程 main 任一被 daily-sync 等更新），**停止并询问用户**（rebase vs merge 决策），不得自动处理；若 `git merge --no-ff` 报冲突，**必须停止、通知用户、不得自动解决**；冲突解决后视影响面重跑 F1-F4 相关项再 push（`git checkout main && git fetch origin && git merge --no-ff soc-rtl-review-remediation && git push origin main`）。
- 每 todo 一 commit（P0 例外：拆 3 个 housekeeping commits；其余 20 个 todo 各 1 个 → **合计 23 个 commit，另计条件性 `chore(firmware)` commits**——todo 13 重建、F3 Spike 重建产生变更时按下方固件构建产物规则加计，实际总数可能为 24/25——round-6 Oracle 修正），格式 `type(scope): summary`，与 todo 的 Commit: 行一致。
- P0 housekeeping commits 分布：evidence 提交于 `fix/fm-soc-10x-sfu-desc`；docs 入库与 .gitignore 提交于 `soc-rtl-review-remediation`。
- **固件构建产物规则（Oracle round-5）**：`firmware/build/npu_*`（elf/map/o 等）被 repo 跟踪；任何步骤重建固件（todo 13 `make clean all`、F 波 Spike 构建）导致其变更时，以独立 `chore(firmware): ...` commit 提交（先例 3e91c5ea），保证各 todo 的 evidence commit 落在 porcelain 为空的树上；证据中的 ELF/HEX hash 以实际测试构建为准。
- **provenance 采集时点（Oracle round-5）**：todo 11 的 provenance 快照必须在**固件重建之后、仿真启动之前**采集（否则记录的 firmware hash ≠ 实际测试二进制）。
- W1 负向测试 commit 前缀 `test(...)`，消息带 `(RED)` 标记；W2 修复 commit 使对应测试转绿。
- 红测试证据（修复前运行结果）在修复 commit 前先提交（或与测试同 commit 并在消息注明 RED）。
- evidence 用 `git add -f` 提交（build/evidence/ 被 gitignore）。

## Success criteria
1. **6 组负向测试 RED→GREEN**：timeout 退出码、crossbar 并发+mutation、APB 真实外设、统计口径、固件地址负例、evidence provenance——每组有修复前后双证据。
2. **Crossbar 死锁消除**：真实并发 7-master 竞争无死锁、grant 差 ≤1、全部 OKAY；固定优先级 mutation 恒 FAIL。
3. **固件三项修复落地**：地址 allowlist、实际 size 校验、completion 越界钳制；负例全 GREEN 且正常 dispatch 不回退。
4. **Runner fail-closed**：timeout 非零退出、SEG_TIMEOUT_S 校验、`|| true` 移除、pipefail、四类统计（PASS/SKIP/FAIL/TIMEOUT）总和=33。
5. **FM-SOC-10X 全验证或显式降级**：不再出现截断验证 + "17-op chain PASS"。
6. **Evidence provenance**：新证据全部绑定 hash 集合；pickle 负例被拒。
7. **干净 commit 全量重跑**：25 执行 PASS（或如实 FAIL）+ 8 SKIP，四类统计正确。
8. **真实 F1-F4**：全部真实执行，FAIL/INCONCLUSIVE 如实记录，用户 explicit okay。
9. **文档一致**：checklist/vplan/CSV 口径统一；BUG-007 ledger 更新；waiver Pending；CSV 入 git。
10. **P2/P3 blocker 清单**：8 类 blocker 有证据路径与解除条件，被 git 跟踪。
11. **就地执行前置（P0）**：当前目录干净（porcelain 空）、分支 == soc-rtl-review-remediation（plan 名）且 HEAD 为 f982bef 的直系后代（+ 2 笔 housekeeping commits）、worktree 已拆除且差异文件已备份、stash 未动、无内容丢弃。
12. **分支收尾**：F1-F4 全 APPROVE + 用户确认后，任务分支 `soc-rtl-review-remediation` `--no-ff` merge 到 main 并 push origin（merge 前 `git fetch origin` 并检查 origin/main 是否移动；冲突不得自动解决，见 Commit strategy）。
