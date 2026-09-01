---
slug: bug-007-root-cause
status: plan-v5-approved
intent: clear
review_required: false
pending-action: await user decision to run `/start-work`; both Momus and Oracle OKAY on v5
approach: W1 基线+双模式 HEAD 复跑+生成器 provenance 考古+重建不确定性标注 → W2 H0 差分（优先复用 sim/verification 框架）+ H1/H2/H3/H4（含固件驻留干扰 cheap diff、status-lifecycle 残差、blk0 tiny-K 形状）→ W2' 条件触发有界翻转考古（7 个 family，≤3 点，重建失败定级）→ W3 归属判定+ledger 处置 → F1-F4
---

# Draft: bug-007-root-cause

## Components (topology ledger)
| id | outcome (one line) | status | evidence path |
|---|---|---|---|
| C0 | 基线：分支 + 新固件 hex + 重生成向量 + 生成前后 manifest hash + provenance | active | task-0 |
| C1 | MODE-ORIG 裁定 + 双模式 HEAD verdict + 重建不确定性（09f753e2/79654175/b6b0a89 日期） | active | task-1 |
| C2 | H0 差分 2×2（优先 fm_adapter，其次 bounded built，再次 missing-degraded）+ 残差候选清单 | active | task-2 |
| C3 | H1 ring/desc 完整性 + 固件驻留干扰 cheap diff + ring_entries 考古 | conditional | task-3 |
| C4 | H2 地址表审计 + 生成器漂移检查 | conditional | task-4 |
| C5 | H3 START/status-lifecycle 波形（含 stale-DONE 残差） | conditional | task-5 |
| C6 | H4 blk0 tiny-K + N=128 补测（区分 3-layer 已覆盖 vs blk0 缺口） | active | task-6 |
| C7 | 翻转考古（7 family，≤3 点，重建失败 caveat） | conditional | task-7 |
| C8 | 归属判定 + ledger 处置 + Blocker-6 回填 | active | task-8 |
| C9 | 交叉记录 + learnings + 汇总表 | active | task-9 |

## Open assumptions (announced defaults)
| assumption | adopted default | rationale | reversible? |
|---|---|---|---|
| MODE-ORIG 大概率 = per-op preload（Python 直驱） | 不押注：双模式都跑；固件 resident 但不调度 | cocotb_bridge.py:5261 docstring + 5282 load_firmware + phase5 以 per-op 关闭 | yes |
| 重建不确定性 = 高 | 原始 evidence 已删；首个 3-layer testcase 79654175 在失败后一天；CPU 模型 09f753e2 在失败前一天切换 | 客观时间线 | no —— 只能降低，不能消除 |
| 生成器 ≈ 原始 | `gen_qwen25_3b_rtl_vectors.py` 自 b6b0a89（07-08）后无修改 | git log 验证 | yes（todo 1/4 会确认） |
| H0 优先复用 sim/verification 框架 | fm_adapter.py 已实现 python/spike 两种 firmware 模式 + DUTAdapter | Oracle F5 发现 | yes（若不能 dispatch attn_weight 则 bounded build） |
| H0 的 "RTL 侧" 收窄为 {RTL ∪ testcase/allocator/driver} | H2/H4 本身是 testcase 层；FM 与 RTL 驱动不同 | Oracle F4 | no（这是归因逻辑） |
| H4 真实缺口是 blk0 chain M=32/K=2/N=128，而非 3-layer M=16/K=16/N=128 | 3-layer manifest op07 已是 M=16/K=16/N=128/tiles=2，且 MODE-A `_run_streamed_mmul` N-tile 流式已覆盖 | Oracle F1 + 亲自读取 manifest/cocotb_bridge.py 验证 | no |
| BUG-012 不并案 | 仅 cross-ref 注记 | 用户请求限定 BUG-007 | yes |
| 历史复跑 worktree | 仅 /tmp 只读临时、≤3 次；每个点需完整重编译 simv | Oracle F6/F7 + 用户"当前目录"规则 | yes |

## Findings (cited - path:lines)
1. `docs/bugs/bugs-soc-rtl.md:328-367` — BUG-007 现状（cycles=0 签名、三假设、ABI-1024 排除）
2. `rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/manifest.json:159-167` — 3-layer op07 = M=16/K=16/N=128/tiles=2（**v2 修正**：不是 M=32/K=2/N=128）
3. `sim/cocotb_bridge.py:1959-2055` — `_run_streamed_mmul` N-tile 流式 + attn_weight probe block（MODE-A 已覆盖 N=128）
4. `rtl/test_vectors/qwen_blk0/blk0_manifest.json`（由 `sim/gen_soc_rtl_vectors.py:_load_blk0_manifest` 加载）— blk0 chain op07 = M=32/K=2/N=128，被 `_build_block` clip 成 N=64（task-15 evidence:64-68）
5. `sim/cocotb_bridge.py:5261-5283` — test_qwen25_3b_3layer 为 per-op preload，但**固件 resident**（load_firmware + 2000-cycle wait）
6. `git log --format="%H %aI %s" -1 09f753e2` — 2026-07-06T08:20:06+08:00 Spike→Ibex RTL，失败发生在第二天
7. `git log --format="%H %aI %s" -1 79654175` — 2026-07-08T07:38:28+08:00 首个提交的三层 testcase，07-07 场景为未提交祖先
8. `git log --format="%H %aI %s" -1 b6b0a89` — 2026-07-08T07:38:43+08:00 向量生成器提交，之后无修改
9. `spec/npu_abi.json:90-134` — STATUS 为 RO，BUSY/DONE 字段，ABI 对 DONE 生命周期（auto-clear-on-START）沉默
10. `sim/verification/fm_adapter.py` 存在 — `FuncModelAdapter` 支持 python/spike 两种 firmware 模式（Oracle F5）
11. `docs/bugs/bugs-soc-rtl.md:220-263` — BUG-006 start_hold 同构签名
12. `docs/bugs/bugs-soc-rtl.md:601-648` — BUG-008 DESC_BASE / ring 重叠先例
13. `docs/bugs/bugs-soc-rtl.md:801-843` — BUG-012 op05 N=2 tiles=2 multi-tile 共因线索
14. `docs/soc-rtl-review-remediation-blockers.md:66-73` — Blocker-6 关闭条件 (a)/(b)
15. **Momus v1 review findings**（bg_d4b021d3，ses_fa2bb770dffeY0AQG9cVSd8Q2B）：Mode B wrapper 参数透传冲突、dirty 文件数 8 vs 7、todo 2 依赖矩阵遗漏与 todo 1 并行、H0 instrument cap 未定义、success criteria grep 不严格、todo 6 skip 措辞冲突。已全部折入 v2。
16. **Oracle v1 review findings**（bg_0cef9aca，ses_fa2bb2e7bffeAQQfhbUavHZsOL）：两 manifest 混同（F1 blocking）、H1 忽略固件驻留干扰（F2 blocking）、H3 stale-DONE 无家可归（F3 major）、H0 路由标签错误且漏 cocotb-driver 层（F4 major）、H0 漏审 fm_adapter（F5 major）、todo 7 考古欠规范且候选集过窄（F6 major）、effort realism + P0SpikeRunner red herring（F7 minor）。已全部折入 v2。

## Decisions (with rationale)
- **两 manifest 必须分开**：3-layer manifest 已覆盖 N=128 流式；blk0 chain manifest 的 M=32/K=2/N=128 才是真正未 clip 的 tiny-K + multi-N 组合。H4 目标从" generic N=128"改为"blk0 tiny-K+N=128"。
- **固件 resident 不等于无关**：MODE-A 加载并 boot 固件，idle loop 仍可能污染 staged buffer；H1 增加 cheap differential（正常固件 vs halt-only stub 或 backdoor dump）。
- **H0 不再claim RTL 有罪**：FM-PASS+RTL-FAIL 只能把空间收窄到 {RTL ∪ testcase/allocator/driver}；若 H1-H4 全 refute，显式记录 cocotb-driver timing、stale-DONE 等残差候选。
- **优先复用 sim/verification 框架**：避免手搓 divergent instrument；fm_adapter 审计作为 H0 第一步。
- **翻转考古改为 family 列表 + 重建失败 caveat**：候选 family 从 3 扩到 7（sim-infra、wrapper、START/status、firmware per-K-block、CPU Spike→Ibex、crossbar fairness、BUG-008 DESC_BASE）；"三点全 CLEAR"不再等同于 environmental，而是 reconstruction-failed。
- **删除证据不可恢复**：重建不确定性高，任何 HEAD 重跑 CLEAR 都不意味着原始缺陷不存在，只能证明"当前形态不触发"。

## Scope IN
见 `.omo/plans/bug-007-root-cause.md` `## Scope / Must have`（7 项交付物）

## Scope OUT (Must NOT have)
见 plan `## Scope / Must NOT have`（8 条守卫：冻结面、rtl/firmware 默认只读、BUG-012 不并案、worktree 规则、no_silent_skip、sz0001、不代签、并行 dirty 文件不动）

## Open questions
（无阻塞项——v2 计划已按 Momus+Oracle 第一轮发现修订；下一步是双评审第二轮）

## v3 修订记录（针对 Momus + Oracle 第二轮 ISSUES）
- **Mixed-mode routing table**：新增 3×4 路由表，明确 `MODE-ORIG` 对应模式的主 verdict 用于触发 todo 3-5/todo 7；非对应模式 FAIL 记为 `NEW-DEFECT-NON-CORRESPONDING-MODE` 不混入 BUG-007。Dependency matrix 同步更新（todo 6 对 todo 8 为软依赖）。
- **todo 1 史实修正**：`docs/vector-workaround-3layer-issue.md:40-61` 实际是 45 PASS / 6 FAIL；原 plan "45/51 失败" 改为 "45 PASS / 6 FAIL"。
- **Makefile target 修正**：W1.7 逐层 cos 目标为 `run_w17_intermediate_compare`（原 `run_qwen25_3b_3layer_intermediate_compare` 不存在）。
- **todo 0 生成器漂移规则**：保留提交态 manifest hash，重生成后比较，若不同则 `git checkout` 恢复提交态，避免用漂移后的向量污染 todo 1 复跑。
- **todo 3 ring 大小考古 grep 修正**：`git log -S "NPU_RING_ENTRIES" -- gen/npu_abi.h` + `git show <sha>:spec/npu_abi.json | grep -n '"ring_entries"'`。
- **todo 5 FSDB 前置条件**：明确需 `+define+FSDB` 重编译 simv（`tb_soc.v:543` 有 `` `ifdef FSDB ``），记录 simv sha/mtime。
- **todo 3 H1 固件驻留干扰**：stub 必须在 `/tmp` 新建 halt-only RISC-V 程序编译为 hex，**不得修改 firmware/ 产品源码**。
- **todo 6 H4 构造失败降级**：`construction-failed` 不 STOP 整个计划，而作为 `RESIDUAL-CANDIDATES` 流入 todo 8。
- **todo 7 考古候选 pruning**：剔除 79654175 之前的 commit（`744413e2`、`a203b463`、`09f753e2`）作为 rerun 点；`09f753e2` 仅 static-diff；默认 point-selection 给出 ≤3 点方案（79654175 + family (i) 中点 + wrapper/START 修复族）。
- **todo 8 disposition**：合并 (a') 进 (a)，统一为 Fixed（修复族已合入 HEAD 或做最小修复）+ 51-op 重跑 cycles>0 & golden 匹配。
- **todo 2/6 cap**：从 "todo 2/6 共享 cap" 改为每个有界任务独立 cap。

## v4 修订记录（针对 Momus + Oracle 第三轮 ISSUES）
- **Mixed-mode routing table / dependency matrix 一致性**：在 routing table 前新增 `主 verdict 定义`，明确 `MODE-ORIG=unknown` 时主 verdict = "任一模式 FAIL → REPRO-FAIL；双 CLEAR → REPRO-CLEAR"；routing table `unknown` 行改为 "n/a | 任一模式 FAIL 即跑 todo 3-5 | 双 CLEAR 才跑 todo 7 | n/a"；dependency matrix 行 7 简化为 `1(主 verdict REPRO-CLEAR)`，与主 verdict 定义一致。
- **todo 3 ring 大小考古命令**：`git show <sha>:spec/npu_abi.json` 改为 `git show <sha>:firmware/npu_firmware.c | grep -n "RING_ENTRIES"`，因 spec/gen 的 ring_entries 常量直到 2026-07-28 的 `b0096d0c` 才首次入库；已验证 `79654175` firmware 中 `RING_ENTRIES=1024`。
- **todo 6 H4 construction-failed 一致性**：acceptance criteria 加入 `construction-failed` 并说明需附障碍详情/流入 RESIDUAL-CANDIDATES；QA scenario 改为 "不 STOP，继续流入 todo 8"。
- **todo 1 References 歧义**："45/51 失败分布" 改为 "51-op 结果分布：45 PASS / 6 FAIL"。
- **Evidence grep-able 行**：Verification strategy 与 todo 1 acceptance 新增 `PRIMARY-VERDICT:` 和 `NEW-DEFECT-NON-CORRESPONDING-MODE:`，便于 F1 审计 routing table 应用。
- **todo 7 考古决策规则**：从 "相邻 commit FAIL→CLEAR" 改为 "family / commit 区间" 本地化；明确 families (v)/(vi) 优先级低、默认 3 点预算通常不覆盖。

## v5 修订记录（针对 Momus + Oracle 第四轮 ISSUES）
- **todo 3 ring-size 措辞**：将 "台账 ABI-1024 排除不适用" 改为明确"ring-overflow 假设仍被排除；只是台账原引用 `gen/npu_abi.h:299` 对 07-07 提交不具时效性，应改用 `firmware/npu_firmware.c` 作为证据来源"。
- **todo 0 acceptance**：`git status --porcelain` 接受本计划产物 + Scope #8 列出的 7 个已知并行会话 dirty 文件，并强调这些 dirty 文件未被修改/提交。
- **todo 7 决策规则**：新增 `≤3 点全部 FAIL` 情形，输出 `FLIP: no-clear-observed`。
- **todo 7 acceptance / QA**：`FLIP:` 记录形式同步为 `localized-to-family|localized-to-interval|no-clear-observed|reconstruction-failed`。

## Approval gate
- 用户原始批准：2026-09-01（方案含 H0 差分对照）。
- v2/v3/v4/v5 修订依据：Momus + Oracle 第一、二、三、四轮 ISSUES 发现，已 personally verify manifest/cocotb_bridge/fm_adapter/09f753e2/79654175/b6b0a89/npu_abi.json/FSDB-guard/ring-grep/firmware-ring-entries 关键事实。
- 状态：**待第五轮 Momus+Oracle 评审通过**。
