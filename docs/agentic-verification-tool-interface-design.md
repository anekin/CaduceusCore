---
type: 设计文档
topic: NPU验证 + Agentic验证 v0.3
created: 2026-08-16
tags: [Agentic验证, 工具接口, gateway, FluxEDA, v0.3]
status: 参考设计（待 v0.2 解冻后纳入）
---

# Agentic 验证 v0.3 工具接口层参考设计

> 参考：FluxEDA（arXiv:2603.25243）gateway 架构 + CaduceusCore 14 条验证经验
> 定位：v0.2 解冻后，Agentic 验证工具接口层的落地设计。当前只作参考，不授权启动。

## 一、现状问题

Hermes 验证工具链（Func Model / golden_executor / arc_model / compare_rtl / e2e_llamacpp 等）是 Python 脚本，Agent 现在"直接跑脚本"。这带来 FluxEDA 论文说的同一批问题：

1. **无状态**：每次跑脚本，验证会话（RTL snapshot、golden 版本、test 集）不持久，Agent 反复重建上下文。
2. **无边界**：Agent 能改任意脚本/文件，没有"哪些是只读 golden、哪些可改"的权限边界。
3. **结果格式散**：各脚本输出格式不一，Agent 读不准，容易采信 SUMMARY 而漏掉 FAIL（14 条经验原则 8 的真实案例）。
4. **context 爆炸**：一次性暴露所有工具能力，LLM 预算被吃光。

## 二、设计目标

把"Agent 直接跑脚本"改成"Agent 通过结构化 gateway 调用验证工具"，落 FluxEDA 六个要素：

1. 分离推理 / 工具执行
2. gateway + api_* 注册 + 授权
3. 归一化结果
4. 持久会话 + rollback
5. 渐进式能力暴露
6. 运行时管理

## 三、架构映射（FluxEDA 五层 → Hermes）

| FluxEDA 层 | Hermes 落地 |
|---|---|
| Access | Agent（对话层），只发结构化请求 |
| Communication | RPC 客户端 + 会话管理器 |
| Gateway | 验证 gateway：注册 api_* 方法 + 四道检查 |
| Tool Adaptation | 适配 Func Model / golden_executor / compare_rtl / e2e |
| Runtime Management | 验证会话生命周期（snapshot / golden 版本 / 心跳 / 超时） |

## 四、api_* 方法清单（初版）

| 方法 | 功能 | 授权级别 |
|---|---|---|
| api_ping / api_list_method | 能力发现（渐进式暴露） | 只读 |
| api_get_golden | 取当前 golden reference | 只读 |
| api_run_func_model | 跑 Func Model | 执行 |
| api_compare_rtl | RTL vs golden 对比（bit-exact） | 执行 |
| api_run_e2e | 端到端验证 | 执行 |
| api_check_coverage | 查覆盖率 / 盲点 | 只读 |
| api_rollback | 回滚到上一个验证通过的 snapshot | 执行（受控） |
| api_list_artifacts | 列出证据 artifact（日志/diff/覆盖率） | 只读 |

**关键约束**：golden（Func Model 输出）是只读 Oracle，Agent 只能读不能改；改 golden 走独立的"golden 变更"流程（旧 golden 当 Oracle 验证新 golden，对应"改工具"的三道门）。

## 五、落地步骤（分阶段）

1. **Phase A（最小可用）**：只包 Func Model + compare_rtl 两个工具，实现 api_run_func_model / api_compare_rtl / api_get_golden 三个方法 + 归一化结果。
2. **Phase B**：加持久会话（snapshot + golden 版本） + api_rollback。
3. **Phase C**：加 api_check_coverage / api_list_artifacts（证据链）+ 渐进式能力暴露。

先跑通 Phase A，验证"Agent 通过 gateway 调用比直接跑脚本稳定多少"（可对照 RTL-to-GDS 评测的 12 组实验思路），再扩 B/C。

## 关联

← [[agentic-ic-verification-plan-v0.2]] ｜ [[caduceus-verification-lessons]]
