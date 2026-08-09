# Graph Engineering：从 Loop 到 Graph 的工程重心迁移 — 调研报告

> **来源**: 公众号文章《Graph Engineering: Loop 的继任者》
> **日期**: 2026-07-28
> **关联**: Hermes delegate_task、structured-execution

---

## 五层演进

```
Prompt Engineering → Context Engineering → Harness Engineering
→ Loop Engineering → Graph Engineering
```

Hermes 当前在 Loop 层（delegate_task、structured-execution、cron 链）。

---

## Loop 的结构性缺陷

| 缺陷 | Hermes 表现 |
|---|---|
| 上下文腐烂 | Memory 80% 满 |
| 错误级联 | subagent 失败→重试→再失败 |
| 工具过载 | 全工具集加载，选择精度下降 |
| 缺控制粒度 | cron 全有或全无 |
| 可观测性差 | 只知 tool call 序列，不知分支原因 |
| 目标失明（Goodhart） | 量化 PBO 典型案例 |

---

## Graph 四要素

```
G = (V 节点, E 边, S 状态, P 策略)
```

| 要素 | Hermes 已有 | 缺的 |
|---|---|---|
| V 节点 | delegate_task 子代理 | 独立 Verifier 验证器 |
| E 边 | 顺序+cron context_from | 扇出/扇入、条件路由 |
| S 状态 | session memory | 跨节点结构化共享 |
| P 策略 | 无 | 节点级权限 |

---

## 三种经典拓扑

- **菱形（扇出扇入）**: 并行→汇总，Anthropic 称为 fan-out/fan-in
- **主管模式（Orchestrator-Workers）**: 主 Agent 调度专职工人
- **流水线（Pipeline/Prompt Chaining）**: 固定步骤+gate 检查点

---

## Anthropic 五模式

| 模式 | 适用场景 |
|---|---|
| Prompt Chaining | 任务可干净拆分子步骤 |
| Routing | 输入种类多需分诊 |
| Parallelization | 子任务独立或需多视角交叉验证 |
| Orchestrator-Workers | 子任务运行时动态决定 |
| Evaluator-Optimizer | 有明确评价标准且迭代有收益 |

---

## 核心原则

1. **别为了 Graph 而 Graph**：改进单个 Agent 的提示可能达到同样效果
2. **价值来自确定性，非智能体数量**：分离执行和验证（Verifier 节点）
3. **最硬的锚点是代码和现实**：测试真跑过、钱真到账、用户真留下
4. **Anthropic 数据**: 多 Agent 比单 Agent 强 90.2%，但 token 消耗 15×

---

## 对 Hermes 的优先级建议

1. **P0: 加 Verifier 节点**——delegate_task 完成后独立子代理做结果审计
2. **P1: 节点级权限**——读 memory / 写 memory / 发微信 / 改 config 分层
3. **P2: 结构化状态传递**——subagent 间从 context 字段升级为 state 对象
