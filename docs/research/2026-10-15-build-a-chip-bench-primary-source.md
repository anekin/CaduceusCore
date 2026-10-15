---
title: Build-A-Chip-Bench 一手源提炼 —— 端到端芯片设计 agent 基准与它暴露的四类工程闭环缺陷
date: 2026-10-15
type: primary-source-analysis
trigger: 用户分享公众号「芯片验证日记」中文稿（2026-10-15 08:36），已回溯一手源核实
primary_source: "https://chipagents.ai/research/build-a-chip-bench（技术页，含 Table 1/公式/4 条 finding 的 agent trace）"
secondary_source: "https://chipagents.ai/newsroom/chipagents-nvidia-build-a-chip-bench（官方新闻稿，2026-10-14，Santa Clara）"
authors: "Surya Gunukula †, Jerry Yicheng Wang †, Kevin Zhang（† 同等贡献）；版权 © Alpha Design AI 2026（ChipAgents）"
collaborators: "Analog Devices, MediaTek, NVIDIA（官方致谢，字母序）"
topics: [T9-Agent辅助芯片设计, T3-Agent方法论, T11-计算机体系结构, T1-Hermes自我进化]
vault_note: 阅读存档/2026-10-15-Build-A-Chip-Bench端到端芯片设计agent基准-芯片验证日记中文稿核实.md
research_loop_feedback: "①「f·g·p 连乘 + 硬门归零」是可直接搬进 ACE 夹具的评分骨架，解决"局部高分掩盖整体交付失败" ②三容器隔离（agent / 隐藏 VIP / grader）是防 agent 改考卷的工程范式，与我方 oracle 不得 import sim.models 的约束同源 ③四条 finding 全部带时间戳 trace，可作为长任务编排的反例库 ④单次 P&R >4 小时、任务预算 72 小时 —— 这是「观察代价」轴在真实基准里的首次量化，直接补上 064 文章的已知空白"
---

# Build-A-Chip-Bench：从 workload specification 到 GDS（一手源提炼）

## 结论

这是**第一份把芯片设计评测从「点任务」推到「端到端交付」的公开基准**，也是目前对 agent「工程闭环能力」最诚实的成绩单。它的价值不在分数低，而在两点：

1. **评分设计堵住了"局部漂亮掩盖整体失败"**——`Score = (1/N) Σ f_i·g_i·p_i`，功能门或 GDS 门任一失败，该任务直接归零，少数任务的极高 PPA 无法翻盘。
2. **四条 finding 全部附时间戳 agent trace**——不是"我们发现 agent 不擅长 X"，而是"agent 在第 06:03 记录了正确方案，然后花了 6 小时 34 分试后端参数，最后在 12:37 才回头改 RTL"。

## 一、它评测什么

**范围**：workload specification → GDS，共 12 步（Workload / Arch / µArch / RTL / Verif / Synth / Fplan / Place / CTS / Route / Signoff / PV），**agent 拥有中间每一步的决定权**。工具链与 PDK 均为开源（换取可复现、可比较）。

**隔离设计（三容器）**：

| 容器 | 里面有什么 | agent 权限 |
|---|---|---|
| agent 容器 | workload spec + 必须对接的 **pinned interface** + 开源工具链 + 开源 PDK | 完全操作权 |
| 验证容器 | **隐藏测试向量 + 隐藏 VIP** | **可发请求，不可查看** |
| grader 容器 | 评分器 + 每个任务的**参考设计** | **完全无权访问** |

**评分（官方公式，逐符号）**：
```
Score = (1/N) · Σ(i=1..N) f_i · g_i · p_i
  f_i = 1 若任务 i 通过隐藏 VIP 的功能验证，否则 0
  g_i = 1 若任务 i 产出干净 GDS（可布线、无 hold 违例、DRC clean），否则 0
  p_i = 该任务的 PPA vs. 约束，0–100%
```
官方对 p 的口径说明：**"PPA vs constraint 是对通过两个门的设计求平均，单独看没有意义"**。

## 二、首轮成绩（Table 1，官方）

| Model | Functional pass | GDS pass | PPA vs. constraint | **Score** |
|---|---|---|---|---|
| **Claude Fable 5.1** | 65% | 75% | 25.5% | **16.6%** |
| **GPT-6 Astra** | 85% | 85% | 10.2% | **8.7%** |
| Gemini 3.8 Flash | 20% | 45% | 4.1% | 0.8% |
| Kimi K3 | 25% | 25% | 1.3% | 0.2% |
| GLM-5.3 | 15% | 5% | 0.0% | **0.0%** |

- 全部模型以 **OpenCode** 作为 agent 运行
- 官方新闻稿补充：**平均约 40% 的任务通过功能验证，但只有约 10% 达到 PPA 目标**
- **任务预算 72 小时**（Figure 6 展示前 36 小时的迭代曲线；Fable 5.1 在单个 sample task 上花 35 小时把分数从 52 提到 61）
- ⚠️ **任务总数未公开**，结果未经第三方复现

## 三、四条 finding（每条一句话 + 原文 trace）

### F1 闭源前沿模型能走完全流程；开源前沿模型前端吃力、后端更糟
原文：「Closed frontier models are generally able to work through the whole silicon design flow… Backend tool use is reasonably reliable: agents can usually configure and run synthesis and place-and-route tools without much trouble… **Open frontier models struggle with the frontend and even more with the backend.**」
→ 分数印证：GLM-5.3 = **0.0%**、Kimi K3 = **0.2%**。

### F2 追逐 PPA 会破坏正确性（Kimi K3，逐条 trace）
```
Baseline 07:36  canonical workloads → PASS · mbig: 158,460 cycles · 0.646 Mrows/s
Rewrite  09:43  想从 0.646 提到 1.2–1.35 Mrows/s：重写为 accumulator-based margins + 两棵树并行
Regression 12:22 OUTPUT MISMATCH at byte 0: got a8 want b7 → FAIL；regress_quick 900,000 ms 被终止（挂死）
Recovery 13:44  PASS · 163,595 cycles · 0.626 Mrows/s ← 比原版慢约 3%
```
原文：「一个正确设计被换成了从未实现的性能提升，有时一次运行下来两样都没有。**Agents rarely treat keeping existing behavior intact as a constraint while they optimize.**」
→ **可抄的约束**：优化前先冻结"当前正确行为"的回归基线，把"保持等价"写成硬约束而不是期望。

### F3 物理失败回溯到 RTL 太晚（GPT-6 Astra，逐条 trace）
```
04:20  Backend tuning：同一份 RTL 上暴露更多布线容量（M2–M10 track 预留 15%）
06:03  Note：记录 compact context-storage 方案，SRAM macro 可从 51 降到 27 —— **Defer（推迟）**
06:16  继续调后端：track 预留 → 0%、congestion_iterations=10；global routing 101 秒零拥塞，
       但 detailed routing 仍有 373,623 个违例；再换配置…（早先那次优化 5 轮后仍有 126,750 个违例）
12:37  Finally 改 context_store.sv + entropy_lane.sv → Total SRAM macros: 51 → 27
```
→ **正确方案被推迟了 6 小时 34 分**，期间全在后端参数上打转。

### F4 慢工具循环破坏收敛（Claude Fable 5.1，逐条 trace）
```
02:41  Plan：synthesis 20–40 min；placement 30–60 min；CTS ~10 min；routing 30–90 min
06:52  CTS: 52,726 endpoints with hold violations；hold-repair iteration 810 · WNS −0.987 ns；
       iteration 1,750 · WNS still −0.987 ns
08:57  Abandon：thought「old design is tying up 12 cores; abandon it」→ kill the flow
```
原文：「Agents bring the habits of fast try-and-retry loops into a flow where a **single place-and-route run can take more than four hours**. They underestimate how long the slow steps will take, **start runs they don't have time to finish**, and often abandon them partway through optimization.」

## 四、它对既有基准的判词（值得单独记）

- **CVDP**：近期模型已 plataeu 在 **65–70% pass@3**，且**许多剩余任务本身有问题**（spec 歧义、malformed testbenches）→「further model gains are unlikely to register on this benchmark」
- **ChipBench**：从发布时 **30.7%** 升到 **90.6%**（2026-09-22 Claude Opus 5.5），「pass rate 已超 90%，headroom 几乎没了」
- 两者都是 **point test**，只覆盖 12 步里的 RTL + Verif 两步

## 五、对我方项目最有用的五条

1. **评测骨架可直接搬**（ACE 夹具）：`门槛 × 质量`的连乘结构，任何硬门失败归零 —— 解决"局部指标漂亮但整体不可交付"的漏判。我们现有的分层通过率可在此之上加一层"硬门"。
2. **三容器隔离 = 防 agent 改考卷**：隐藏 VIP 与 grader 物理隔离。与我方 NPU 项目「oracle/verifier 不得 import `sim.models` / `sim.engine` / `sim.timing.providers`」的约束同源，可把这条约束推广到所有评测夹具。
3. **四条 finding 作反例库**（长任务编排）：① 优化前冻结回归基线（F2）② 根因在前端就果断回头，别在后端参数上耗（F3）③ 单步工具耗时 >4 小时的流程，必须显式算"这次运行值不值、剩余时间够不够跑完"（F4）。**这三条与 Hermes 的长任务行为直接对应**。
4. **"观察代价"轴首次被真实量化**：单次 P&R > 4 小时、任务预算 72 小时、agent 因理解错耗时而在 08:57 放弃 —— 这正是 064 文章里"没有任何基准报过迭代一轮多贵"的反例，**建议写进 064 或后续文章**。
5. **盯排行榜**：官方在与行业伙伴做**实时排行榜**（early access / contribute → `research@chipagents.ai`），任务集与模型集都在扩展。

## 六、待核 / 边界

- [ ] **任务总数未公开** → 16.6% 的分母规模未知，不能与其它基准的百分比直接比较
- [ ] 结果**未经第三方复现**（ChipAgents 自评）
- [ ] 用**开源工具链 + 开源 PDK**，与先进制程商业 EDA/复杂 IP/多时钟/DFT/封装环境差距明显（官方自限 scope 为"可复现、可比较"）
- [ ] 官方新闻稿只给 40% / 10% 两个汇总数，Table 1 在技术页 —— 引用时注意区分两个来源

## 七、出处

- 技术页 HTML：`temp/bacb_research.html`（208 KB，26,284 字符文本）
- 新闻稿 HTML：`temp/bacb_official.html`（57 KB）
- 中文稿：`temp/wx_m1ix.html` / `.txt`（5,148 字符）
- 核实笔记：`阅读存档/2026-10-15-Build-A-Chip-Bench端到端芯片设计agent基准-芯片验证日记中文稿核实.md`
