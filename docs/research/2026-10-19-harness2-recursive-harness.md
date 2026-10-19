# Harness²：递归 harness 自改进（arXiv:2610.14224）

- **日期**：2026-10-19
- **论文**：Harness^2: Recursive Agent Harnessing for an Open World（arXiv:2610.14224，2026-10-09 v1）
- **机构**：Google Cloud AI Research × Northwestern University（Ruiyao Xu, Yanfei Chen, Zhongying CuiZhu, Bhavana Dalvi Mishra, Yifei Ming, Han Yu, Rujun Han, Chen-Yu Lee, Tomas Pfister）
- **代码**：github.com/google-research/harness2
- **中文转载**：公众号「模智空间」2026-10-19 17:46
- **完整阅读存档**：`~/Documents/Obsidian_Vault/阅读存档/2026-10-19-Harness2-递归Agent-Harness.md`（含 4 张原图、逐条核对的中文稿口径差异）

## 结论

模型权重冻结，改外层 harness；关键是**两级递归**：

1. **任务级递归**：contrastive Propose–Probe–Compose——提候选改动 → 实际跑 → 用「改前 vs 改后」的行为差做对比 → 组合定稿。**不需要 ground truth**，这是它能进开放世界的原因。
2. **harness 优化级递归**：把「这次是怎么改的」提炼成经验，更新一个专门负责改进的 harness。即：不只沉淀成品，还沉淀**改进过程**（诊断、试过的改法、结果）。

## 数字（已与论文核对）

| 项 | 值 |
|---|---|
| WorkBuddy-Bench 最大单步增益 | +11.5（GLM-5 / OpenCode） |
| JobBench 最大单步增益 | +4.5 |
| LAB Partial pass rate | +11.8 pp |
| k=1 → k=5（Gemini 3.5 Flash / OpenCode） | JobBench +4.5 → +7.5；WorkBuddy +5.1 → +8.4 |
| 弱模型反超 | GLM-5 改进后 75.7 / 75.3 > Gemini 原始 72.4 / 74.5 |
| 消融（WB / Job） | 去经验库 −3.1 / −1.8；去「通用/领域」分工 −2.9 / −3.7；去组合 −1.6 / −1.9 |
| 泛化 | 经验库跨任务迁移有效；harness 跨模型迁移有效；5 个执行模型全部提升 |

**编辑器模型固定为 Claude Opus 4.8（OpenCode）**——中文稿未提，但这决定方法天花板。

## 对我们的意义

**证据强度**：这是「模型定上限、harness 定逼近度」最直接的一组数据——同一批权重只改外层，WorkBuddy 涨 11.5 分；弱模型配好 harness 反超强模型配原始 harness。harness 的差距可以跨一个模型档位。

**已有 vs 缺失**：
- 已有：`~/.hermes/scripts/error_db.py`（踩坑模式）、`skill_impact.py`（patch 的 proposal_id + Δ/regression 标记）。这两者合起来就是论文说的 **improvement harness 的经验库**。
- 缺失：「自动提出下一次改法」这一环。现在是人工讨论后改 skill，不是系统读经验库后提 proposal。

**候选实验（未执行）**：让一个「编辑器」角色读某 skill 的 error_db pattern + skill_impact 历史 Δ，产出 N 条 patch 提案；由 harness 在同一批历史案例上回放（改前 vs 改后），Δ>0 才采纳。这正是我们已有的 patch 审计流程，只把「提提案」自动化。

**风险/保留**：三基准由作者选定；无真值，全靠行为对比；存在退化案例（选择器挑错、错误假设被继承、规则过严）；收益是「有界预算下的帕累托」，非普适最优。方向可信，绝对幅度打折。

## 关联

- 同期在看的 harness 线：`Cortex`（arXiv:2610.14216，测量门控的 skill/rule 增删）、`Self-Evolving Agent Harness`（arXiv:2610.18813，形式化 + 准入/保留界）
- 我们的对应物：hook（pre_tool_call）、skills、subagents、context_router、compaction guard
