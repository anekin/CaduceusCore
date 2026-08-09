# Jeff Dean 离职谷歌创立 Discovery Loop + BigBang-V1 原生 RSI 模型 — 调研报告

> **来源**: 新智元报道, 2026-08-06
> **日期**: 2026-08-06
> **关联**: RSI 递归自我改进、CaduceusCore 全栈验证、公众号文章方向

---

## Jeff Dean 离职 + Discovery Loop

- 谷歌传奇 Jeff Dean 告别 27 年谷歌
- 与三位顶尖研究者创立 **Discovery Loop**
- 方向：**RSI（递归自我改进）**——让 AI 持续自我迭代，自动攻克 ML/科研/工程难题

---

## BigBang-V1：第一个原生 RSI 训练的基座模型

**团队**：上海交大人工智能学院 + 深势科技 + 上海算法创新研究院（「无尽前沿团队」）

| 指标 | 数据 |
|---|---|
| 参数规模 | **35B** |
| 训练数据 | **100% AI 合成** |
| 对比对手 | DeepSeek V4 Pro（1.6T，**45 倍体量**） |

### 十项第一（35B 级别）

| 基准 | BigBang-V1 | DeepSeek V4 Pro |
|---|---|---|
| FrontierScience Research (OpenAI) | **46.2** | 低于 BigBang |
| Humanity's Last Exam | **50.3** | 低于，堪比 Gemini 3.1 Pro |
| PaperBench Code-Dev (OpenAI) | **53.6** | 50.4 |
| MLE-Bench Lite (OpenAI) | **59.1** | 低于 |
| BioMysteryBench Human-Difficult (Anthropic) | **15.7** | 13.7 |

---

## 两个现场案例

### 案例 1：找病毒（BioMysteryBench）
三份肠道类器官测序原始数据，判断感染哪种 RNA 病毒。
- BigBang：3/3 全对（诺如病毒 GII.4）
- DeepSeek V4 Flash：3 次三种不同病毒，全错

### 案例 2：复现论文，找到了论文的 bug
复现一篇论文时，BigBang 发现核心设定有矛盾——某个量被固定住了，导致「子集不断缩小」无法发生。**主动修改了这处设计**，修复梯度中断问题。复现得分 0.7657。
- DeepSeek V4 Flash：只做语法检查，代码一次没跑。0.3053

### 案例 3：设计抗体流水线
BigBang 把多个开源模型串成流水线：蛋白质设计模型生成候选 → AlphaFold 预测结构 → 物理打分器评估。两层独立计算方法交叉验证。

---

## RSI 引擎架构

```
Generator Agent（出题者）
  ├── 直接修改/运行/调试数据合成程序
  ├── 根据模型能力缺口调整任务和验证规则
  ├── 决定：领域/推理链长度/工具/验证方式/样本筛选/失败策略淘汰
  └── 产出：100% AI 合成的多学科前沿科研数据

+ 外部真实考试（不可被 Agent 修改的评测）
```

**关键设计**：Generator Agent 不只是出题，而是控制数据合成的代码和策略本身。

---

## 和之前讨论的 RSI 框架精确对应

| 之前讨论的理论 | BigBang-V1 的实现 |
|---|---|
| RSI 三层：工具级→架构级→规范级 | 目前在工具级+架构级之间（改数据策略+训练流程） |
| 评价器捕获风险 | 「外部真实考试」= 不可被 Agent 修改的 e |
| RSIBench-Data 的四步 | BigBang 全做了：Diagnose→Hypothesis→Generate→Validate |
| Kimi 案例：闭环≠改进 | BigBang 证明了闭环 CAN = 改进（35B 超 1.6T） |
| 100% AI 合成数据 | 对应 Polar 的 API 层 RL + RSI 的自生成训练数据 |

---

## 对 CaduceusCore/公众号的启示

### 1. RSI 从理论到产品只用了不到一个月

7 月 29 日 RSI 哲学文章还在讨论「评价器连续性」、7 月 30 日 RSIBench-Data 说「58% 首轮超、78% 后续退步」——8 月 6 日 BigBang-V1 已经是跑通的 RSI 产品了。

### 2. 公众号文章方向

你们的 Func Model 全栈验证方法论 + BigBang-V1 的 RSI 引擎 + Jeff Dean 的 Discovery Loop，可以写一篇「RSI 从概念到落地」的综述文章——既有理论框架（你早上整理的 RSI 三层），又有工程实践（BigBang），还有产业动向（Jeff Dean）。

### 3. CaduceusCore 的对应

BigBang 的「Generator Agent 改数据合成代码 + 外部真实考试」和 CaduceusCore 的「Agent 自动验证 + SoC Golden Contract」是同一架构模式的两种实现——前者改数据生成策略，后者改芯片验证策略。验证方法论是通用的。
