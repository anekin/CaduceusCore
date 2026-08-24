# Func Model Golden Reference 方法论 vs ChipMATE 无 Golden Oracle

> 日期：2026-08-24
> 来源：用户口述 + func_model_architecture.md + ChipMATE 论文（arXiv:2605.12857）
> 用途：Func Model 复盘文章的方法论对比素材 + NPU 项目方法论定义

## 一、我们的方法论（CaduceusCore NPU）

**核心流程（顺序化、有锚）：**

```
阶段 1：生成 + 验证 Func Model
  agent 生成 Python Func Model（GoldenMXU/SFU/Vector/DMA + MMIOBridge + NPUFirmware）
  + 复用 GitHub 开源模块（Spike RISC-V 模拟器等，作为可信底座）
  → 验证 Func Model 正确 → 升格为 golden reference（绝对标准，冻结）

阶段 2：开发 RTL
  agent 开发 RTL
  → RTL 与 golden reference 做 bit-exact 对拍（AXITracer 记录 MMIO 事务）
  → 不一致就修 RTL
```

**关键特征：**
1. **参考模型先被验证、升格、冻结**，成为 golden reference（绝对标准）
2. **RTL 后开发**，以冻结的 golden reference 为准，bit-exact 对拍
3. **可信锚点**：Func Model 不是纯 agent 从零生成，而是 agent 生成 + 复用开源模块（Spike 等久经考验的底座）
4. Func Model 是 agent 生成的（非工程师手写），但通过验证升格为 golden

## 二、ChipMATE 的方法论

**核心流程（并行、无锚）：**

```
Verilog agent（设计工程师）    Python reference-model agent（验证工程师）
     ↓ 生成 Verilog                ↓ 生成 Python 参考模型
     └────────── 交叉验证（代码盲 + 匹配率对比）──────────┘
                不一致 → 各自自查 + 回溯（只进不退）
```

**关键特征：**
1. **两个 agent 同时生成、同时验证**，没有先后
2. **无 golden oracle**：两个 agent 都不可信，不设绝对标准
3. 靠「交叉验证 + 回溯（修正只在匹配率严格提升时接受）」逼近正确
4. 纯训练模型生成（SFT + X-GRPO），无外部开源模块底座

## 三、本质对比

| 维度 | 我们 | ChipMATE |
|------|------|---------|
| 参考模型来源 | agent 生成 + 开源模块底座 | 纯训练模型生成 |
| 参考模型地位 | 先验证升格为 golden（绝对标准，冻结） | 不设 golden（相对标准） |
| 开发顺序 | **顺序化**：先锁定锚，再开发 RTL | **并行**：两 agent 同时生成互相验证 |
| 验证方式 | RTL 对拍 bit-exact（有标准答案） | 匹配率 + 回溯（无标准答案） |
| 可信锚点 | 有（开源模块 + 验证冻结） | 无 |

## 四、一句话总结

**同一套「双 agent 双轨」骨架（生成端 + 参考端，都用 Python 作参考载体），我们走了「有可信锚点 + 先验证升格 golden + 顺序开发」的稳妥路线，ChipMATE 走了「无锚点 + 交叉验证逼近 + 并行」的激进路线。**

我们的方法论本质 = **先锁定 golden reference（Func Model 验证冻结），再让 RTL 去够它**。ChipMATE = **不锁定，两个 agent 一起逼近**。

## 五、可写的落点（Func Model 复盘文章）

- 「先验证参考模型，再开发 RTL」这个顺序，是工业 golden model 验证的自然延伸，但把 golden model 的生成也 agent 化了——这是和 ChipMATE 的「无 golden oracle」路线最值得写清楚的分野。
- 可信锚点（开源模块底座）是能"升格 golden"的前提；没有这个底座，就只能像 ChipMATE 那样靠交叉验证兜底。
