# arXiv "agent harness" 论文数量趋势调研

> 日期：2026-08-24
> 类型：文献趋势调研（field trend statistics）
> 口径：arXiv API，摘要含精确短语 "agent harness" 的论文（`abs:"agent harness"`）

## 一、调研方法与口径

- **检索方式**：arXiv API，`search_query=abs:"agent harness"`，`sortBy=submittedDate&sortOrder=descending`
- **口径说明**：摘要里明确出现 "agent harness" 精确词组的论文。arXiv 对 harness/harnesses/agentic 等做了词干化归并，故该口径已覆盖 "agentic harness"、"agent harnesses" 等变体。
- **数据边界（诚实标注）**：
  - 这是**下限估计**——用 "evaluation harness"、"harness for LLM agents" 等不同写法的论文未计入核心集，实际更多。
  - 趋势**方向可靠**，绝对数字可能 ±20%。

## 二、核心数据

### 按年统计（共 207 篇）

| 年份 | 论文数 | 占比 |
|------|-------:|-----:|
| 2023 | 2 | 1.0% |
| 2024 | 1 | 0.5% |
| 2025 | 6 | 2.9% |
| 2026 | 198 | **95.7%** |

### 2026 年逐月

| 月 | 1月 | 2月 | 3月 | 4月 | 5月 | 6月 | 7月 | 8月* |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 篇数 | 2 | 5 | 7 | 16 | 40 | 38 | 51 | 39 |

\* 8 月统计截至 8-21（月未结束，年化更高）

## 三、趋势分析

**一个清晰的爆发式增长，分两个阶段：**

1. **萌芽期（2023-10 → 2025 底）**：术语 2023-10 首次出现（最早一篇 "Balancing Autonomy and Alignment: A Multi-Dimensional Taxonomy for Autonomous LLM Agents"）。三年合计仅 9 篇，零星出现。

2. **爆发期（2026 年）**：198 篇，占全部 95.7%。增长斜率极陡——从 1 月 2 篇 → 4 月 16 篇 → 5 月 40 篇 → 7 月峰值 51 篇，**6 个月 25 倍增长**。1-7 月完整月均 22.7 篇。

**主分类分布**：cs.AI 90（43%）、cs.CL 30、cs.SE 22、cs.CR 16、cs.CV 12——AI 为主，软件工程、安全、视觉均有渗透，说明 harness 概念已跨领域扩散。

## 四、解读

"agent harness"（评测/运行 LLM agent 的框架层）在 2026 年成为热点，反映一个方向性转变：

> **LLM agent 从"研究 demo"走向"系统化评测与部署"**——harness 是 agent 的"操作系统层"（环境接入、动作执行、评测闭环、RL 训练接口），它的爆发意味着 agent 工程化、可评测化、可量产化的需求集中释放。

2026 年的代表性论文（样例）印证了这个判断：SemaPLC（PLC 代码生成 harness）、HarnessRisk（harness 安全基准）、Agent Lightning（harnessed agentic RL）、LEGO-RL（harness-native RL）、ClawGym II（harness 上的黑盒 RL）。

## 五、与 Hermes 的关联

Hermes 本身就是一套 agent harness——tools（terminal/browser/computer_use）、skills、memory、cron、error_db 正是 harness 的"环境接入 + 动作执行 + 评测闭环"三件套。这个趋势直接印证：**harness 层是 agent 价值链的必争之地**，与 Hermes 的定位高度重合。

值得深挖的方向（后续可走 benchmark-survey 或 paper-close-reading）：
- HarnessRisk（harness 安全）——harness 层的安全/对齐新议题
- Agent Lightning / LEGO-RL——"harness-native RL"，把 harness 从评测工具升级为训练基础设施

## 六、原始数据

- 完整 207 篇论文元数据（id/title/published/summary/cats）：`/tmp/agent_harness_clean.json`
- 趋势图：`/tmp/agent_harness_trend.png`
