---
type: 调研评估
topic: T9-Agent辅助芯片设计 + NPU验证
created: 2026-08-16
tags: [AgenticEDA, 证据链, NPU验证, 差距分析]
---

# Hermes 验证流 vs Agentic EDA 文章：差距分析

> 来源：公众号文章《Agentic EDA 不是 AI 造芯片》 + CaduceusCore 验证复盘（14 条经验 + Agentic IC 方案 v0.2）
> 结论一句话：文章的核心观点 Hermes 已在实践中落地且更深，文章价值是"外部佐证 + 3 个可借鉴点"，不是"补短板"。

## 一、对照文章框架，Hermes 现状

| 文章框架 | Hermes 现状 | 状态 |
|---|---|---|
| Oracle（不可绕过的 golden） | Golden Model 唯一真相源（原则1）+ bit-exact | ✅ 已覆盖 |
| 证据链（可查的责任链） | 独立保存比对（原则6）+ 双路比对 backdoor/interface（原则7）+ 按阶段 bug 文件发现即 commit（原则11） | ✅ 已覆盖 |
| 权限边界（Agent 不能自我认证） | Review Gate（Atlas 三态裁决）+ Agentic v0.2 明写"Agent 不能自我认证" | ✅ 已覆盖 |
| 回滚 | 增量替换从高风险模块开始（原则13） | ✅ 已覆盖 |
| 未覆盖点显式记录 | 原则14（issues_found.md 显式列盲区） | ✅ 已覆盖 |

## 二、真正可借鉴的 3 个点

1. **LLM4Cov 的"失败状态吃回训练"**（最值得动手）：Hermes 的 RTL 模型训练（T10）需确认是否收集了"student 自己会失败的场景"，而非只看 teacher 的成功轨迹。文章里 4B student 反超 30B teacher 靠的就是这个。可落地在 T10。
2. **硬预算**：AgentCore 每个 debug/优化环有硬 token/license 预算，不允许 Agent 无限烧。Hermes 的 Agentic v0.2 未显式见到预算机制，需补充。
3. **Self-Evolved ABC 的"Agent 改工具自己"**：Hermes 是"Agent 用工具"（Func Model/golden_executor 作为 Oracle），可探索"Agent 改进验证工具链本身"（三道门：编译→CEC→QoR）。

## 三、结论

文章是"外部佐证"，证明 Hermes 的验证方向（证据链/责任链/Oracle/回滚）是对的，而且 Hermes 已比 AgentCore（32 位处理器 Demo + ASAP7 预测工艺 + 开源工具）走得更远——CaduceusCore 是真实 NPU ASIC，有 14 条从实际 bug 复盘的验证经验。

3 个可借鉴点里，#1（失败状态吃回训练）最具体、最可落地，直接落在 T10 RTL 模型训练。#3（Agent 改工具）方向激进，可作为 Agentic 验证 v0.3 的探索项。
