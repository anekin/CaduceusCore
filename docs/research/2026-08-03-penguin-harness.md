# PenguinHarness：LlamaFactory 作者新作「让 Agent 自己造 Agent」— 调研报告

> **来源**: PrismShadow AI / Yaowei Zheng, 2026-08-03
> **关联**: Hermes Skill 自进化、快慢双环专利、观察者 Skill 模式

---

## 核心主张

**一句话生成完整 Agent 应用，内置自我进化循环。**

---

## 关键数据

| 框架 | 模型 | 准确率 | Token | 成本 |
|---|---|---|---|---|
| **PenguinHarness** | DeepSeek V4 Pro | **66.67%** | 18M | **$0.55** |
| OpenAI Codex | GPT-5.5 | 53.33% | 14M | $19.41 |
| Claude Code | Claude Opus 4.8 | 53.33% | 22M | $38.48 |

成本为 Claude Code 的 **1/70**。

---

## 自我进化架构

```
Optimizer（调度器）
  ├── 跑一圈基准测试
  ├── 调度多个 Evaluator 并行打分
  ├── 分析 Trace 日志定位失分原因
  ├── 从版本 N 升级到 N+1
  ├── 升级前自动快照（可一键回滚）
  └── 重复
```

---

## 对 Hermes 的对照

| PenguinHarness | Hermes |
|---|---|
| Optimizer 调度 | cron job 调度（Rule Abstractor / Skill Loop） |
| Evaluator 并行打分 | Evidence 收集 + aggregator 判定 |
| Trace 日志分析 | session_search + error_db pattern 匹配 |
| 版本 N→N+1 + 快照 | skill_manage(action='patch') + git |
| 回滚 | ❌ 缺自动回滚——skill 改坏只能手动 git revert |

---

## 最值得借鉴

**自动快照 + 回滚机制。** 你们的 Skill Loop 改 skill 后如果出问题，当前只能手动 git revert。PenguinHarness 的「升级前自动 commit、失败一键回滚」可以加到 Rule Abstractor cron 里。
