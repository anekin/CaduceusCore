# NVIDIA Polar：API 层寄生式 Agent RL 训练 — 调研报告

> **来源**: NVIDIA Polar 开源 RL Rollout 框架
> **日期**: 2026-07-29
> **关联**: Hermes 自进化、模型权重优化路径

---

## 核心做法

不改 Agent 代码。在 LLM API 调用边界插 Proxy：

```
Agent 框架（Codex/Claude Code/Qwen Code）
    ↓ 标准 LLM API 调用
Polar Proxy（拦截层）
    ├── 检测格式 → 统一 Normalize 到 OpenAI Chat
    ├── 捕获 token 级轨迹数据
    ├── 转发到 vLLM/SGLang 推理
    └── 返回原格式响应（Agent 无感知）
```

---

## 关键数据

| 指标 | 数据 |
|---|---|
| Qwen3.5-4B + Codex | 3.8% → 26.4%（SWE-Bench, +22.6） |
| GPU 利用率 | 20% → 88%（prefix_merging） |
| 训练加速 | 5.39× |

---

## Proxy 四层

1. **检测**：自动识别 Anthropic/OpenAI/Google 格式
2. **Normalize**：统一转 OpenAI Chat Completions
3. **捕获**：存下 request messages、response tokens、log probs
4. **返回**：合成原始 Provider 格式（含流式 SSE）

Agent 完全无感知——它以为在直接调 API。

---

## 对 Hermes 的启发

### 1. 你可以在 API 层做 RL 而不改 Agent 框架

Hermes 的进化目前只改脚手架（error_db/skill/memory），不动模型权重。Polar 证明：在调用 LLM 的 API 层注入 reward signal，不改 Agent 代码就能训出 7 倍提升。

### 2. 快慢双环专利可以加一层权重优化

```
慢环（当前）→ 缺陷分析 → 改脚手架
快环（Polar 启发）→ API Proxy → 改模型权重（GRPO 微调）

互补：脚手架快=即时生效但单次；权重慢=需训练但全局提升
```

### 3. prefix_merging 是效率关键

同一 Agent 跑多个 rollout 时，共享 system prompt 只推理一次，后续复用 KV cache。Hermes 的 delegate_task 并行跑时存在同样优化机会。

### 4. Rollout as a Service

训练框架和推理框架解耦——HTTP 通信替代代码集成。Hermes 的 cronjob(context_from=...) 可以借鉴这个架构从静态 context 注入升级为动态 service 调用。
