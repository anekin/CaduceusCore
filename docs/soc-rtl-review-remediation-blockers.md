# SoC RTL 评审整改 — P2/P3 Blocker 跟踪清单

- **生成日期**: 2026-08-31
- **所属计划**: `.omo/plans/soc-rtl-review-remediation.md` todo 20（Wave 4）
- **分支**: `soc-rtl-review-remediation`
- **评审报告**: `reports/CaduceusCore-review-report-2026-08-28.md`（§4 项目级事项 / §7 整改优先级）

## 定位声明

本清单**只跟踪、不执行**（计划 Must NOT #1）。评审报告 §7 的 P0/P1 项由本计划
todos 0-16 执行；P2/P3 项不在此计划执行，全部在此登记为 blocker 行，每项含四个字段：
**现状证据路径 / 阻塞原因 / 解除条件 / 建议 owner**。任一 blocker 解除时更新对应行并
回填解除证据路径。

---

## Blocker 1 — Perf CI RSS 17.4GB 超限（评审报告 §4.1 / §7 P2.2）

| 字段 | 内容 |
|------|------|
| **现状证据路径** | `.omo/evidence/task-23-perf-spec-ci.txt`（HEAD 版本 2026-08-24T16:58, head `0422d2469`：`exit_code=1`、`verdict=fail`、`peak_rss_mb=17399.88`、`reasons=["rss_limit_breach: 17399.9MB > 4096.0MB"]`，各子阶段 provider/qwen-cv/sweep/uncertainty/adversarial 均 pass，仅资源门禁失败）；`reports/CaduceusCore-review-report-2026-08-28.md:187-198`（§4.1） |
| **阻塞原因** | Perf signoff CI 峰值 RSS 17399.9MB 超过 4096MB 上限，总体资源门禁 fail。**Gating 关系（Metis M10）**：该失败阻塞**项目级 signoff**，**不阻塞 SoC RTL 功能 signoff**（SoC RTL 功能回归证据与 CI 内存门禁无耦合）。 |
| **解除条件** | 在干净 commit 上重跑 `scripts/run_func_model_perf_signoff.py run --all-spec --ci-mode`，`peak_rss_mb ≤ 4096` 且 `verdict=pass`，evidence 落库并绑定该 commit。 |
| **建议 owner** | Func Model 性能 signoff 负责人（perf CI 维护者） |

> **对抗性注记**: 工作区 dirty 版本（2026-08-31T04:31, head `886ccb4`）已显示
> `verdict=pass`、`peak_rss_mb=124.87`，但该运行为 dirty 工作区状态（27 个 dirty paths），
> 不能作为正式解除证据；解除以干净 commit 重跑为准。

## Blocker 2 — E2E-07 性能校准（评审报告 §4.2 / §7 P2.1）

| 字段 | 内容 |
|------|------|
| **现状证据路径** | `.omo/plans/rtl-perf-decomposition-calibration.md`（22 todos、5 waves，**待执行**；本清单只引用、不修改该计划——本计划 Must NOT #7）；`.omo/plans/soc-rtl-verification-vplan.md:22,133,184`（E2E-07 保持 ❌，`calibration_state=uncalibrated`，deferred 到流片/FPGA 实测；full SoC RTL signoff 未到 100% 的直接原因之一） |
| **阻塞原因** | RTL 性能校准未执行，`calibration_state=uncalibrated`。Func Model 公式/规格验证通过（task-23 子阶段）不能等价于 RTL 实测 calibration 完成；E2E-07 无 RTL 证据即不满足完整 SoC RTL signoff 条件。 |
| **解除条件** | rtl-perf-decomposition-calibration 计划 W1-W5 执行完毕：产出 `docs/rtl-perf-calibration-report.md`（`calibration_state=rtl_calibrated_atoms`，±20% 置信带），vplan E2E-07 由 ❌ 更新为 ✅(rtl_atom_calibrated) 并附证据链接。 |
| **建议 owner** | RTL 性能验证负责人（rtl-perf-decomposition-calibration 计划执行者） |

## Blocker 3 — FPGA L5 NO-GO + ggml lifecycle BLOCKED（评审报告 §4.3 / §7 P2.4）

| 字段 | 内容 |
|------|------|
| **现状证据路径** | `docs/func-model-signoff-checklist.md:339-341`（L5 **BLOCKED** — Task 20 FPGA is NO-GO，本阶段无 FPGA 平台；Framework **BLOCKED** — Task 15 ggml lifecycle，`fm://python` device server prerequisite 不可用（`cadDeviceOpen(fm://python) failed: Device lost`）；**Overall BLOCKED**，L5/Framework 按 aggregator 规则传播）；`docs/func-model-signoff-checklist.md:343-352`（What Is NOT Claimed 对应说明） |
| **阻塞原因** | **外部依赖**不可用：无 FPGA 平台（L5 / Task 20）、`fm://python` Func Model device server 不可用（Framework / Task 15 ggml lifecycle）。项目级 signoff 聚合状态 Overall BLOCKED。 |
| **解除条件** | FPGA 平台可用并真实完成 Task 20（VFIO/UIO/vendor 传输，NO-GO 证据转真实运行证据）；`fm://python` device server 可用并完成 Task 15 ggml lifecycle；checklist 聚合 L5/Framework 由 BLOCKED 转 PASS。 |
| **建议 owner** | FPGA bring-up 负责人（L5）+ 软件栈负责人（ggml lifecycle）——外部依赖所有者，本计划无权解除 |

## Blocker 4 — 36 层连续仿真 deferred 到 FPGA（评审报告 §4.4 / §7 P2.3）

| 字段 | 内容 |
|------|------|
| **现状证据路径** | `reports/CaduceusCore-review-report-2026-08-28.md:212-216`（§4.4：当前为分段 checkpoint subset——执行 15 个选定层、检查 8 个 checkpoint，各段首输入来自 Spike NPZ，非 L0→L35 连续 forward；全连续 36 层 deferred 到 FPGA 阶段）；分段证据 `build/evidence/task-14-soc-rtl-verification-signoff.txt`（8 checkpoint cosine，**已归档**：commit `4d8dab8` archive，工作区已删除；8-checkpoint 数据引用另见 `.omo/plans/rtl-perf-decomposition-calibration.md` W4 交叉验证章节） |
| **阻塞原因** | 无 L0→L35 全连续 Ibex forward 证据；EDA 仿真时间成本（47,241.5s 分段运行、单段 24h timeout 约束）使全连续运行在当前阶段不可行，已 deferred 到 FPGA。评审要求"完成或明确重新定界"。 |
| **解除条件** | 二选一：(a) 明确重新定界——正式声明分段 8-checkpoint subset 为当前阶段交付形态并更新 signoff 口径/文档；或 (b) FPGA bring-up 阶段完成全连续 36 层并产出证据。 |
| **建议 owner** | FPGA bring-up 阶段 RTL 验证负责人（重新定界则由 signoff 负责人决策） |

## Blocker 5 — WVR-SOC-RTL-002 待用户签署（评审报告 §4.6 / §7 P2.6）

| 字段 | 内容 |
|------|------|
| **现状证据路径** | `docs/waivers/WVR-SOC-RTL-002.md:11-12`（Status = **提交待签 pending sign-off**，Sign-off 栏留空）、`:53-61`（关闭条件 #3 = 用户签署本 waiver，签署前不生效）；`docs/bugs/bugs-soc-rtl.md:373,387`（台账曾写 formally Waived——状态漂移，由本计划 todo 19 改回 Pending）；`reports/CaduceusCore-review-report-2026-08-28.md:222-228`（§4.6） |
| **阻塞原因** | 8MB DRAM 窗口约束 waiver 的明确生效/关闭条件是**用户签署**；签字栏为空，不能作为正式 closure 依据。签署是不可代理的用户动作。 |
| **解除条件** | 用户亲笔签署本 waiver（Signature/日期落款）；签署后 bug 台账 BUG-RTL-SOC-002 从 Pending 同步为正式 Waived。 |
| **建议 owner** | **用户**（签署动作，agent 不得代签） |

## Blocker 6 — BUG-RTL-SOC-007 根因追查（评审报告 §4.5 / §7 P2.5）

| 字段 | 内容 |
|------|------|
| **现状证据路径** | `docs/bugs/bugs-soc-rtl.md:326-366`（Status = **Open**；todo 15 ATTN-WEIGHT-CHAIN 已执行 2026-08-27，26 命令 cycles>0、op07 attn_weight cycles=30755 cos=1.0，链级未复现；Root Cause = Under Investigation，三假设（ring overflow / weight 地址越界 / START 阻塞）均未证实；待 FPGA/更早日志追踪）；链级证据 `build/evidence/task-15-soc-rtl-verification-signoff.txt`；`reports/CaduceusCore-review-report-2026-08-28.md:218-220`（§4.5） |
| **阻塞原因** | 原 W1.3 三处 `attn_weight` cycles=0 的根因仍未定位（链级未复现 ≠ 根因已找到）；不能 claim Fixed。完整 SoC RTL signoff 要求 Open bug 有明确处置结论。 |
| **解除条件** | 二选一：(a) 根因定位并修复，重跑 3-layer forward 全部 op cycles>0 且 golden 匹配，Status 转 Fixed；(b) 正式定级为环境性/偶发并记录复现条件与影响面，ledger 写明处置结论（不 claim Fixed 则需用户接受）。 |
| **建议 owner** | RTL/固件调试负责人（联合 FPGA 阶段更早日志追踪） |

## Blocker 7 — P3.1 遗留工作区状态清理（评审报告 §7 P3.1）

| 字段 | 内容 |
|------|------|
| **现状证据路径** | (a) `git stash list` → `stash@{0}: WIP on main: c244935`（`git stash show --stat stash@{0}` 实测：14 files changed，全部为 `.omo/evidence/*`，含 final-code-quality/final-manual-qa/final-plan-compliance/final-scope-fidelity 与 task-22-release-signoff 等）; (b) `git branch` → `fix/fm-soc-10x-sfu-desc` 分支仍存在（评审时工作分支，未 merge 收尾/关闭，远端同名分支同步待处置）; (c) `du -sh build/ibex_segment_rtl` = 87M（已 gitignore：`.gitignore:50` `build/ibex_segment_rtl/`），属"已 gitignore 的 87M build 产物磁盘清理"；`reports/CaduceusCore-review-report-2026-08-28.md:35-37`（工作区非干净交付状态） |
| **阻塞原因** | (a) stash@{0} 内 14 个 evidence 文件处置未定（保留入库 / 备份 / 丢弃均未决策）——P0 已分类提交其余 dirty（计划 todo 0），本项仅跟踪该剩余 stash；(b) `fix/fm-soc-10x-sfu-desc` 分支收尾/合并未做，历史分支悬挂；(c) 87M gitignore 构建产物占用磁盘，干净可复现交付快照未形成。 |
| **解除条件** | (a) stash@{0} 明确处置：14 文件按项目 evidence 规则入库（`git add -f`）或备份后 `git stash drop`，处置记录留痕；(b) `fix/fm-soc-10x-sfu-desc` 合并入 main（`--no-ff`）或正式关闭并删除本地/远端引用；(c) 87M build 产物磁盘清理完成；工作区 `git status --porcelain` 清洁。 |
| **建议 owner** | 项目维护者（git housekeeping；执行时以当前 stash/branch 实况为准） |

## Blocker 8 — P3.4 可重放 signoff manifest + 最终用户签收记录（评审报告 §7 P3.4 / §6.5）

| 字段 | 内容 |
|------|------|
| **现状证据路径** | `reports/CaduceusCore-review-report-2026-08-28.md:267`（§6 第 5 条：plan 要求 F1-F4 全 APPROVE 后等待用户 explicit okay，现有 Git 中**没有用户签收记录**）、`:311-312`（§7 P3.4：形成可重放的 signoff manifest 和最终签收记录）；现有 signoff 证据散落于 `build/evidence/` 与 `.omo/evidence/`，无单一入口 |
| **阻塞原因** | 缺少单一可重放 manifest（固定 commit + hash 绑定 provenance + 命令序列 + 结果汇总 + 输入产物 hash）与用户最终签收记录；第三方无法从单一入口独立重放并验证整个 signoff。 |
| **解除条件** | 产出可重放 signoff manifest（绑定 commit、provenance hash 集合、命令序列、结果汇总、输入产物 hash）入库版本控制；用户最终签收记录落 git（explicit okay）；F1-F4 全 APPROVE 且用户确认后 `--no-ff` merge 到 main。 |
| **建议 owner** | signoff 负责人（manifest 编制）+ **用户**（最终签收，不可代理） |

---

## 评审报告 §7 映射表

> 覆盖性声明：§7 全部 4+5+6+4 = 19 项均在此表可追溯。

### P0/P1 项 → 本计划 todos

| §7 项 | 本计划 todo | 执行状态 |
|--------|------------|----------|
| P0.1 crossbar 并发 accept/grant deadlock + 真实竞争 fairness | todo 2（RED 负测）+ todo 7（修复） | 本计划执行 |
| P0.2 APB conformance 接真实 7 外设 RTL | todo 3（RED 负测）+ todo 12（接入） | 本计划执行 |
| P0.3 runner fail-closed（退出码/timeout/旧 evidence/结构化统计） | todo 1 + todo 4（RED 负测）+ todo 9（修复） | 本计划执行 |
| P0.4 固件地址 allowlist + 实际 size 校验 + completion 越界 | todo 5（RED 负测）+ todo 8（修复） | 本计划执行 |
| P1.1 干净固定 commit fresh build | todo 13 | 本计划执行 |
| P1.2 evidence 绑定 hash（HEAD/dirty/simv/flist/driver/ELF/golden/工具） | todo 6（RED 负测）+ todo 11（provenance 脚本） | 本计划执行 |
| P1.3 回归口径 25 执行 + 6 superseded + 2 N/A | todo 4（RED 负测）+ todo 9 + `docs/fm_soc_case_manifest.csv` | 本计划执行 |
| P1.4 真实执行 F1-F4（禁 DRY-RUN/DEFERRED 转 PASS） | todo 14（+ todo 16 Spike FAIL 处置） | 本计划执行 |
| P1.5 checkpoint/timeout/旧 evidence/损坏 NPZ/错误 commit 负测 | todo 1 + todo 6 | 本计划执行 |

### P2/P3 项 → blocker 行

| §7 项 | Blocker 行 | 可追溯性备注 |
|--------|-----------|-------------|
| P2.1 完成 RTL performance decomposition/calibration | **#2** | E2E-07，引用 rtl-perf-decomposition-calibration 计划（待执行） |
| P2.2 处理 performance CI 17.4GB RSS 超限 | **#1** | gating：阻塞项目级 signoff，非 SoC RTL 功能 signoff |
| P2.3 完成或明确重新定界全连续 36 层 Ibex forward | **#4** | deferred 到 FPGA |
| P2.4 解除 FPGA L5 和 ggml lifecycle blocker | **#3** | 外部依赖 |
| P2.5 继续定位 BUG-RTL-SOC-007 根因 | **#6** | Open，FPGA/更早日志追踪 |
| P2.6 用户评审并签署 WVR-SOC-RTL-002，签署前保持 Pending | **#5** | 待用户签署 |
| P3.1 清理并分类当前 dirty worktree，不覆盖现有修改 | **#7** | stash@{0} + 分支收尾 + 87M 磁盘清理 |
| P3.2 正式 feature-status/计划/签核报告/必要 evidence 纳入版本控制 | **#7 + #8** | 主体由本计划 todo 0/17 执行（docs 入库、CSV 入库）；**residual**：stash@{0} 内 14 evidence 文件入库 → #7；manifest/签收记录最终入库 → #8 |
| P3.3 统一 checklist/bug ledger/waiver/vplan/evidence 状态口径 | **#8 + 本计划 todo 17/18/19** | 口径统一动作在本计划 todo 17/18/19 执行；统一后的正式记录纳入 #8 manifest |
| P3.4 可重放 signoff manifest + 最终签收记录 | **#8** | — |

**P2/P3 项可追溯性**: 10/10（P2 × 6 + P3 × 4 全部有对应 blocker 行或本计划 todo 映射，P3.2/P3.3 为交叉覆盖并在备注中注明分工）。

---

## 维护说明

- 本清单由 `.omo/plans/soc-rtl-review-remediation.md` todo 20 产出（可与其他 Wave 4 todos 并行）；
  执行归属：**本计划只跟踪，不执行**以上 8 项。
- 更新时机：任一 blocker 解除时，更新对应行并在"解除条件"后追加解除证据路径与日期；
  若与用户签署相关（#5/#8），签署记录由用户动作落库，agent 不代签。
