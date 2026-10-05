---
title: Jev / System One Models 与黑客松项目 mu —— 一手源核实报告
date: 2026-10-05
type: primary-source-verification
trigger: 用户分享微信稿《把Jev整合进Harness，开源项目拿下黑客松总冠军！》（智猩猩AI，2026-10-05）
topics: [T1-Hermes自我进化, T3-Agent方法论]
vault_note: 阅读存档/2026-10-05-把Jev整合进Harness-mu黑客松冠军-一手源核实.md
ima_synced: "2026-10-05 已同步 ima Research(folder_7500790050594871)，回读验证命中（Research 20→21）；⚠️ 禁止重复同步（本字段即护栏，ima_sync.py 已识别）。注：同日首次同步误用脚本旧默认值（Mercury 根 folder_7501435130349554），在 Research/Mercury 根留下一条副本，ima openapi 无删除/移动能力（实测 move_knowledge 返回 code:0 但为 no-op），需在 ima UI 手动清理。"
research_loop_feedback: 本报告补上一条此前完全空白的线索——harness 层出现「专职做判断的小模型」层；建议把「判定点清点 + shadow 试点」列入 T1 下一轮实验候选。
---

# 一手源核实：Jev / System One Models × mu（μ）

## 结论

中文稿（智猩猩AI，2026-10-05，全文 2,920 字）讲的是黑客松冠军项目 **mu** 如何把 **Jev** 整合进 Harness：**主干属实**，但**只写了应用层、漏掉了底座**——Jev 是 **TypeSafe AI 2026-09-15 发布的 System One Model**，不是通用 LLM，而是「类型化决策模型」，且已在 9 月中下旬形成一个上百仓库的生态。**这条底座才是真正的增量。**

## 一手源

| 源 | 关键事实 |
|:--|:--|
| `typesafe.ai/blog/introducing-system-one-models-and-jev`（2026-09-15，Diogo Almeida，founder） | System One Model = 为「快速、结构化、软件可直接使用的决策」而建的新模型类别；新架构 + parallel sampler + **RLCD**（Reinforcement Learning for Calibrated Decisions）；首个模型 **Jev**（early access）；官方口径「System One 任务上智能接近现有 LLM，**快两个数量级**、更省」；**放弃字符串生成**；作者自述在 OpenAI 参与过让语言模型遵循指令/对话的方法 |
| typesafe.ai 首页定位图 | pre-trained LLM → RLHF chat models → RLVR reasoning models → **RLCD / TypeSafe** |
| GitHub `typesafe-ai` 组织 | `skills` 2583★（System One API 的 agent 技能）、`system-one-adapter-python` 380★（LLM API 兜底）、`typesafe-sdk-python/js`、`WorkflowEvals` 15★ |
| GitHub 生态（2026-09-16 ~ 09-23 集中出现） | `laya-mlx` 6768★（M3 Max 上 7–14 ms 短决策）、`SemIf-OpenJev` 4695★（自家 3090 复刻，声明与 TypeSafe 无关）、`awesome-jev` 2144★/928★、`Intent-Router` 834★、`ollaya` 1197★、`pg-jev` 889★、`jev-seo` 503★、`winnow` 101★（Claude Code 每个工具结果先过 System One 模型）、`jev-judge-mcp` 90★（verify/screen/find/classify/rerank/decide）、`harnessjudge`、`jev-judge-calibration`（预注册校准 Jev 1.13 当完成报告裁判）、`dsh-jev-prune`（DeepSeek Harness 上下文压缩）、`house-party-protocol`（Claude Code/Codex 本地 agent teams harness） |
| Jev 的准确定义（引自生态清单 README） | 「Jev 不是聊天模型：输入非结构化状态 + **类型化问题**，返回**类型化决策**（选择 / 打分 / 布尔），每个带置信度」→ 定位为软件里的**决策层**：分类、路由、评分、校验、agent 护栏 |

## 与源稿的差异（4 条）

1. **漏底座**：只写 mu 怎么用 Jev，未交代 Jev 是何物、何时发布、谁做的（官方文 9/15）。
2. **归属易误读**：源稿称「本地判定模型 Laya」——Laya 是**开源社区**的 Jev 兼容 System-1 决策模型（laya-mlx / ollaya / receptron），非 TypeSafe 官方本地模型。
3. **口径混淆**：0.3s / 0.44s / 51% 均为 **mu 作者自述**；官方只有「快两个数量级」。
4. **未取到 mu 仓库与黑客松一手页面**（「进化酒馆黑客松深圳收官之战」无公开一手信息）→ 记为待补核。

## 对我方的意义（可执行）

1. **判定点清点**：把 Hermes 隐式判断（compaction 触发、错误分类、skill 采纳、心跳、终止条件、投递判定）编号列出，标注「规则 / 小模型 / 必须主模型」。mu 的 35 个判定点说明：**先编号才可观测、才可替换**。
2. **shadow 模式**：新判定先只记录、不改行为，积累后比对命中率再启用——补上 skill/error_db 缺的「采纳前后对照」。
3. **无损折叠优先于语义摘要**：先做确定性去重（mu 实测 7 份失败日志 14 万字符 → −51%，不调模型），我们目前以有损摘要为主。
4. **产出与汇报分离**：由判定层决定「是否有新进展要告诉用户」，再由表达模型整理且**不回灌主上下文**——正面命中长任务汇报的已知问题。

## 与我方机制对照

| 我方 | 形态 | 与本篇的差距 |
|:--|:--|:--|
| `correction-learning`（用户纠正→subagent Judge） | 用**大模型**当裁判 | 贵；判断频率高时应换小模型/规则 |
| `decision_db` / `skill_impact` | 自评置信度 | 无外部标准逐条打分（生态里有 `jev-judge-calibration` 预注册校准可对标） |
| `compaction_guard` / `context_router` | 规则 + 摘要 | 非「入口判定」，且摘要是有损的 |
| ACE v4 评测 | 连续 33 轮满分 = 零区分度 | 缺 per-step 判定 |
