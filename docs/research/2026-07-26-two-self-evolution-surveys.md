# 自演化与自改进智能体：两篇综述对照 — 调研报告

> **来源**: vibe life 整理, Fang et al. (2025) + Gao et al. (2026), 2026-07-26
> **关联**: Hermes 自进化体系、快慢双环、四范式演进

---

## 两篇综述的互补视角

| | Fang et al. (2025) | Gao et al. (2026) |
|---|---|---|
| **框架** | 统一反馈环（Inputs→System→Env→Optimiser） | 双路径（θ 参数 + Σ 脚手架） |
| **侧重** | 单/多/领域三类自演化技术 | 改什么 + 信号从哪来 |
| **三定律** | Endure / Excel / Evolve | — |
| **范式演进** | MOP→MOA→MAO→MASE | — |

---

## 四范式演进链（Fang et al.）

| 范式 | 改参数？ | 反馈来源 | 人工配置 |
|---|---|---|---|
| **MOP** 离线预训练 | 否 | 静态语料 | 全静态 |
| **MOA** 在线适应 | 是 (SFT/LoRA) | 标注/评分 | 人工触发 |
| **MAO** 多 Agent 编排 | 否 | Agent 间消息 | 手工 workflow |
| **MASE** 多 Agent 自演化 | 否（改 prompt/memory/tool） | 环境反馈+meta-reward | 最少 |

每一跳解决前一阶段的「静态性」。

---

## 自演化三定律

1. **Endure（存活）**：在动态环境中持续运行，不崩溃、不退化
2. **Excel（卓越）**：适应任务变化，性能提升
3. **Evolve（进化）**：自主修改内部组件，实现能力跃迁

---

## 统一反馈环框架

```
System Inputs（任务设定）
    ↓
Agent System（执行）
    ↓
Environment（给反馈）
    ↓
Optimiser（据此更新 Agent System）
    ↓
循环直到达到性能阈值
```

---

## 对 Hermes 的对照

| 框架要素 | Hermes 实现 |
|---|---|
| System Inputs | 用户消息 + cron prompt |
| Agent System | Skills + Memory + Tools + delegate_task |
| Environment | 真实系统（terminal/browser/微信）+ error_db 日志 |
| Optimiser | Rule Abstractor + Skill Loop + Pattern Loop + Memory Loop |

Hermes 当前处于 **MAO→MASE 过渡带**——多 Agent 编排已具备（delegate_task），但群体自主演化（meta-reward 驱动、自动精炼）尚未实现。
