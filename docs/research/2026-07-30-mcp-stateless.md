# MCP 无状态化改版 — 调研报告

> **来源**: MCP 2026-07-28 规范更新
> **日期**: 2026-07-30
> **关联**: Hermes 微信投递、cron job 架构、CaduceusCore Host Runtime

---

## 核心改动

| 改了什么 | 之前 | 之后 |
|---|---|---|
| **握手** | `initialize` 协商能力，绑定会话 | 每次请求带 `_meta`，按需 `server/discover` |
| **会话** | `Mcp-Session-Id` 粘滞到特定实例 | 无状态，每个请求自包含 |
| **状态传递** | 隐藏在传输元数据里 | 显式句柄（如 `basket_id`），对模型可见 |
| **Server 请求 Client** | 允许（elicitation/sampling） | 禁止——改用 MRTR |

---

## MRTR：Return, don't call back

```
第一轮: Client → Server: reserve_room(A101)
        Server → Client: input_required + requestState
        ↑ 请求已结束，Server 无状态

第二轮: Client → Server: reserve_room(A101) + inputResponses + requestState
        可路由到另一台实例——requestState 含全部上下文
```

---

## 对 Hermes 的意义

### 1. Cron job 不再需要会话亲和性

旧协议多实例需 sticky session。cron 重启后可能连到不同实例、会话丢失。无状态化后轮询即可，cron 重试天然安全。

### 2. 显式句柄 → 模型可自己管理状态

旧协议状态藏在 `Mcp-Session-Id` 里，模型看不见。新协议变工具参数——模型可以「把 basket_id 记下来，下次传回去」。Memory/skill/todo 间的状态传递不需要协议层黑盒。

### 3. 迁移窗口 12 个月

旧版仍支持 `initialize` 回退，不需紧急迁移。

### 4. 对微信限频的直接启示

MRTR 的 `Return, don't call back` 直接对应推送守卫问题：

```
旧：早报投递失败→推送守卫不断重试→每次重试消耗额度→无限封锁
新(MRTR)：投递失败→记录 requestState→等用户主动拉取→一次一条
```

---

## 和 CaduceusCore 的对照

CaduceusCore 的 Host Runtime 设计先天无状态——`cadDeviceOpen(uri)` 携带所有参数，没有服务器端会话。MCP 花了两年才意识到的事，在设计阶段就做对了。验证了 Vulkan/CUDA 风格 host API 的决策。
