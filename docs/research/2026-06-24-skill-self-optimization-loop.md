# Skill 自优化闭环：观察者 Skill 模式 — 调研报告

> **来源**: Warp 创始人 Zach Lloyd 工程实践, 无糖AI 整理, 2026-06-24
> **关联**: Hermes Skill 自进化、快慢双环专利

---

## 核心模式：观察者 Skill

**创建一个 Observer Skill，专门评估、诊断、改进另一个 Inner Skill。**

```
Observer Skill
  ├── 接收测试输入（N 个样本）
  ├── 批量调用 Inner Skill 执行任务
  ├── Computer Use + Browser Use 自动化质检（视觉+行为对比）
  ├── SOTA 模型做结果综合分析（共性失败模式）
  ├── 生成 Diff，自动改进 Inner Skill 源文件
  └── 重复，直到收益递减 → 退出
```

---

## 内层循环 vs 外层循环

| | 内层循环（Inner Loop） | 外层循环（Outer Loop） |
|---|---|---|
| **目标** | 把这一次任务做对 | 让 Skill 本身变强 |
| **对象** | 当前具体的输入 | Skill 的 Prompt/脚本/配置 |
| **频率** | 每次执行 | 发现问题→批量改进 |

---

## 执行链路六步

1. **接收测试输入**：N 个待处理的样本
2. **批量执行**：依次或并行调用内层 Skill
3. **自动化质检**：Computer Use 打开原站+迁移后站点，截图比对、交互验证
4. **综合分析**：结构化数据喂给 SOTA 模型，总结共性失败模式
5. **生成 Diff**：编程 Agent 直接改 Skill 源文件、生成 PR
6. **退出条件**：diff 越改越小时停止，避免过度优化

---

## 对 Hermes 的精确对应

| 观察者 Skill 模式 | Hermes 实现 |
|---|---|
| Observer Skill | Rule Abstractor cron job + Skill Loop |
| Inner Skill | 被监测的任意 skill |
| 自动化质检 | evidence 收集 + pass/fail 判定 |
| SOTA 模型综合分析 | error_db pattern 匹配 + learnings 沉淀 |
| 生成 Diff | skill_manage(action='patch') |
| 退出条件 | pattern 评分 ≥ 80 → PATTERN_STABLE |

**Hermes 缺的一步**：Computer Use 做视觉质检。你们当前的 evidence 主要是文本/数值对比，对于 UI 类产出的质量判断没有「像人一样点开看」的能力。

---

## 三条适用边界

1. **依赖明确的验证标准**：任务「对/错」能被自动判断才能跑闭环。模糊任务（如创意文案）自动评分困难。
2. **容易卡局部最优**：迭代多轮后可能停在「还不错」无法跳到「最好」。
3. **退出条件不可缺失**：Observer Skill 必须内置停止标准，否则无限烧 token。
