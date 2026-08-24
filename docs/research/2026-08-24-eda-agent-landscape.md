# EDA Agent 全景调研（公众号选题：芯片设计 Agent 全景）

> 日期：2026-08-24
> 类型：公众号选题素材 + 领域全景整理
> 选题：芯片设计 Agent 全景（B 框架：EDA 演进三幕 + harness 层）

## 一、完整图景（广义 EDA agent，139 篇）

检索口径：`abs:"EDA agent"` + `abs:"hardware agent"` + `abs:"electronic design automation" AND agent` + `place and route` + `logic synthesis` + `RTL` 组合，按 arXiv ID 去重。

### 两个时代

| 时代 | 时间 | 篇数 | 特点 |
|------|------|-----:|------|
| 传统 RL/优化 | 2011–2022 | 7 | RL 做逻辑综合、硬件安全，非 LLM |
| LLM agent | 2023–2026 | 132 | 从 ChatEDA 起，2026 年爆发 |

**关键转折点**：ChatEDA（2308.10204，2023-08）——第一个 LLM-powered 自主 EDA agent。

### LLM 时代（2024+）132 篇的 EDA 环节分布

| EDA 环节 | 篇数 | 占比 |
|------|-----:|-----:|
| 前端 RTL 生成 | 100 | 76% |
| 模拟/电路 | 10 | 7.6% |
| 验证/修复 | 9 | 6.8% |
| 逻辑综合 | 4 | 3.0% |
| 后端物理设计 | 2 | 1.5% |
| 系统级/架构 | 2 | 1.5% |

**结构性失衡**：RTL 生成占 76%（文本生成任务，LLM 天然擅长）；后端物理设计（2 篇）+ 逻辑综合（4 篇）严重稀缺（几何约束/时序/面积的结构化优化，LLM 不擅长）。

## 二、演进三幕（文章主线）

1. **第一幕 · 传统 RL（2011–2022）**：RL 做逻辑综合（DRiLLS 1911.04021，2019-11）、技术映射、硬件安全。特征是"优化算法 + 特定环节"，无通用性。
2. **第二幕 · ChatEDA 转折（2023-08）**：ChatEDA（2308.10204）首次用 LLM 做自主 EDA agent，把 RTL 生成/综合/验证串起来，开创"LLM 驱动 EDA"范式。
3. **第三幕 · LLM 爆发 + harness 层（2024–2026）**：132 篇，76% 挤在 RTL 生成；2026 年 harness 层兴起，补上后端/验证的工程化闭环。

## 三、Harness 层（芯片设计专用，5 篇，2026 年）

| 论文 | arXiv | 时间 | 环节 | 核心机制 |
|------|-------|------|------|---------|
| Design Conductor（第一版） | 2603.08716 | 2026-02 | 系统级 | 多 agent 自主构建 1.5GHz Linux-capable RISC-V CPU（12h） |
| Verilog Code Generation | 2603.19347 | 2026-03 | RTL 生成 | Verilog 生成 agentic 框架首个系统评测（CVDP） |
| Clover | 2604.17288 | 2026-04 | RTL 修复 | 神经符号 harness，树状搜索修 RTL bug |
| Design Conductor 2.0 | 2605.05170 | 2026-05 | 系统级 | 80h 构建 TurboQuant 推理加速器（80x 更大任务） |
| AgenticPD | 2607.04758 | 2026-07 | 后端物理设计 | stage-aware 框架，Judge Agent 按流阶段优化 QoR |
| CLOSER-Bench | 2607.16632 | 2026-07 | 跨阶段评测 | 硬件 agent 跨抽象层 design closure 评测 |

> 注：Design Conductor 两版展示了 harness 的进化——12h 建 CPU → 80h 建加速器，任务规模 80 倍。

## 四、文章叙事框架（B，已确认）

**标题候选**：
- 「芯片设计 Agent 全景：从 RL 到 Harness，EDA 这十年怎么被 AI 接管」
- 「EDA Agent 十年：从逻辑综合 RL 到 harness 操作系统」

**主线**：EDA agent 演进三幕（传统 RL → ChatEDA 转折 → LLM 爆发 + harness 层），落点 = harness 层补上后端/验证的工程化闭环 + 76% 失衡背后的机会判断。

**差异化卖点**：
1. 历史纵深（2011 起，别人只讲 2026 的 harness）
2. 76% 失衡的量化洞察（RTL 生成拥挤、后端物理设计 2 篇）
3. harness 层恰好落在最稀缺方向（AgenticPD 物理设计、Clover 验证、CLOSER-Bench closure）
4. 双演进线：模型层（ChipNeMo→CodeV-R1→CRUX）+ harness 层（这 5 篇）

## 五、数据边界（诚实标注）

- 139 篇是"多关键词组合 + 去重"的下限估计，关键词未覆盖全（如 AgenticPD 用 "physical design" 而非 "place and route"，需人工补）。
- "EDA agent" 精确短语仅 5 篇，但 "EDA + agent" 组合 33 篇、"RTL + agent" 92 篇——说明领域术语不统一，数量统计必然有漏。
- 趋势方向可靠，绝对数字 ±20%。

## 六、待深挖素材（todo 2）

- ChatEDA 具体机制（开山，需读全文）
- DRiLLS 的 RL 机制（传统时代代表）
- AgenticPD 的 Judge Agent 机制
- Design Conductor 两版的架构演进（12h CPU → 80h 加速器）
- CLOSER-Bench 的评测协议设计
