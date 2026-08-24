# 芯片设计专用 Agent Harness 整理（公众号选题素材）

> 日期：2026-08-24
> 来源：从 arXiv "agent harness" 精确短语口径（207 篇）中筛出芯片设计专用子集
> 类型：公众号选题素材 + 细分领域整理

## 一、核心结论：5 篇，占通用 harness 的 2.4%

在 207 篇 agent harness 论文中，芯片设计专用的仅 **5 篇（2.4%）**，且全部集中在 2026 年 3-7 月。芯片设计是 agent harness 的**冷门但高价值**细分方向。

## 二、5 篇全景（按芯片设计流程环节）

| 环节 | 论文 | 时间 | 核心机制 |
|------|------|------|---------|
| RTL 生成 | Exploring the Agentic Frontier of Verilog Code Generation | 2026-03 | Verilog 生成 agentic 框架的**首个系统评测**（CVDP benchmark） |
| RTL 修复 | Clover: Neural-Symbolic Agentic Harness for Verified RTL Repair | 2026-04 | 神经符号 harness，把 RTL 修复建模为结构化搜索（树状思考） |
| 系统级设计 | Design Conductor 2.0: builds a TurboQuant inference accelerator in 80 hours | 2026-05 | 多 agent harness，80h 自动构建推理加速器（前作 12h 构建 RISC-V CPU） |
| 物理设计 | AgenticPD: Stage-Aware Agentic Framework for Physical Design QoR | 2026-07 | stage-aware 框架，按物理设计流阶段边界组织 Judge Agent |
| 跨阶段评测 | CLOSER-Bench: Cross-Stage Design Closure for Hardware Agents | 2026-07 | 硬件 agent 的跨抽象层 design closure 评测协议 |

## 三、关键洞察

1. **起步晚于通用 harness 一个季度**：通用 agent harness 2026 年 1 月起步（2 篇），芯片设计专用 3 月才出现第一篇（Verilog Code Generation）。

2. **5 篇恰好覆盖芯片设计全流程**：生成（前端）→ 修复（验证反馈）→ 系统级设计（架构）→ 物理设计（后端）→ 跨阶段评测（贯穿）。这说明芯片设计 agent harness 虽然数量少，但**生态已初步成形**，不是单点突破。

3. **空白 = 机会**：尚未被 harness 覆盖的环节——形式验证（formal verification）、DFT、模拟/混合信号电路、封装测试、sign-off。这些都是 agent harness 的"无人区"，也是芯片设计 agent 下一步的必争之地。

4. **与芯片垂类模型谱系衔接**：这 5 篇是"agent harness（框架层）"，与之前 benchmark-survey 里整理的芯片垂类模型（ChipNeMo→CodeV→CodeV-R1→CRUX→VerilogCL，模型层）形成互补——**模型层（能力）→ harness 层（工程化/评测闭环）**，是芯片设计 AI 的两条演进线。

## 四、公众号选题角度（候选）

**主线叙事**：agent harness 爆发（207 篇，2026 年 25 倍增长）→ 芯片设计专用才 5 篇、刚起步 → 但 5 篇已覆盖全流程 → 空白环节即机会。

**标题候选**（热点词前置）：
- 「芯片设计 Agent 的'操作系统'来了：2026 年 5 个 harness 全景拆解」
- 「207 篇 Agent Harness 里，只有 5 篇做芯片——这个蓝海你看到了吗」

**差异化卖点**：不只是罗列 5 篇，而是点出「模型层 + harness 层」双演进线的框架，以及 5 个空白环节的机会判断。

## 五、数据边界（诚实标注）

- 口径是"摘要含精确短语 agent harness"——单独用 "hardware agent"、"EDA agent"、"chip design agent" 检索可能还有更多，但那些不属于 "agent harness" 口径。
- 5 篇是"agent harness 口径下的芯片设计子集"，不是"芯片设计 agent 的全部"。

## 六、原始论文链接

- Verilog Code Generation: arXiv 2603.19347
- Clover: arXiv 2604.17288
- Design Conductor 2.0: arXiv 2605.05170
- AgenticPD: arXiv 2607.04758
- CLOSER-Bench: arXiv 2607.16632
