# RSIBench-Data：Agent 数据研究能力基准评测 — 调研报告

> **来源**: Evolvent AI + NUS, RSIBench-Data (arXiv 2607.25886)
> **日期**: 2026-07-30
> **关联**: Hermes 自进化评测、快慢双环验证

---

## 核心实验设计

**固定所有变量，只测数据研究决策：**

训练/推理/评测全部服务化。Agent 只能做：读反馈→提假设→设计数据策略→训 checkpoint→分析结果→规划下轮。

---

## 关键结果

### 1. 58% 超过首轮：Agent 确实能研究
24 组 Agent-任务中，14 组超过首次有效尝试。

### 2. 78% 后续搜索退步：研究不稳定
23 条峰值后继续搜索的轨迹中，**18 条最终低于历史最好，0 条持续刷新。**

### 3. 花更多钱 ≠ 更好
Codex 用 69 美元拿 20%，另一个 Agent 用 157 美元只拿 5.6%。同一任务无 Agent 全面占优。

### 4. Kimi K2.6 闭环≠改进
Kimi 训练自己：闭环 7 轮，最佳候选 22%，不训练基线 33%。

---

## 四条成功轨迹共性

**Diagnose → Validate → Align → Preserve**

1. 找到真正的能力缺口，不是调表面参数
2. 可执行、可判定的验证信号嵌入数据构建
3. 训练监督与模型最终行为对齐
4. 显式保留历史最佳 checkpoint，知道何时停止

---

## 对 Hermes 的对照

| RSIBench 框架 | Hermes 对应 | 状态 |
|---|---|---|
| Data（数据策略） | error_db pattern 库 | ✅ |
| Algorithm（训练/调度） | 快慢环调度策略 | 已定义，待验证 |
| Architecture（结构） | Polar RL 微调 | 未开始 |
| Harness（工具/选择） | Graph Engineering | 讨论中 |

---

## 最关键的启示

**Hermes 缺少受控对比评测。** 无法区分「error_db 让系统变好了」还是「这次碰巧没遇到那个 bug」。需要一套固定的验证环境，只让 Agent 改 error_db/skill/memory，跑独立评测。

对应 RSIBench 的设计原则：**固定所有可控变量，隔离出被评测的能力维度。**
