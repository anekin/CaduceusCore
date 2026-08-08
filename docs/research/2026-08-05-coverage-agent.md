# Coverage Agent：LLM 驱动的芯片验证覆盖率收敛 — 调研报告

> **来源**: Coverage Agent 方法论文章
> **日期**: 2026-08-05
> **关联**: CaduceusCore Func Model 验证、Evidence 体系

---

## 核心论点

```
❌ 把 URG HTML / 截图直接给 LLM → "Coverage 有点低，多加点测试吧"（废话）
✅ .vdb → UCAPI 结构化提取 → Coverage JSON
   → 结合 Spec/Test Plan/Regression/RTL → 可执行 Closure Action
```

---

## Coverage Agent 数据流

```
coverage.vdb
    ↓ UCAPI 结构化提取
Coverage JSON（Hierarchy + Metric + Uncovered Object + File/Line）
    ↓
+ Specification + Test Plan + Regression + RTL Context
    ↓
Gap Classification + Closure Recommendation
```

---

## 关键数据结构

### Hierarchy Tree Schema
```
tb_top.dut.u_dma → self_metrics + cum_metrics + children[]
```
Self vs Cumulative 区别：self 好但 cum 差→问题在 child module。

### File Gap Summary
```
哪个文件 → 对应哪个 Module/Instance
→ 哪种 Metric 没收敛 → 多少未覆盖对象 → 典型对象+行号
```

### Recommendation Schema
```
Coverage Gap → 触发条件 → Test Plan 状态 → Regression 状态 → Closure Action
```
每条建议附带五段可追溯证据。

---

## 和 CaduceusCore 的精确对应

| Coverage Agent | CaduceusCore |
|---|---|
| `.vdb` → UCAPI 提取 JSON | `npu_abi.json` → 生成 Python/C/SystemVerilog |
| Hierarchy + Gap Summary | SoC Golden Contract（13 地址区 + 寄存器表） |
| Spec/Test Plan/Regression 结合推理 | Scenario 共享 + Differential Runner + Evidence |
| 可追溯 Closure Action | Bug track + Learnings → fix → evidence |
| Coverage 低 ≠ 缺测试 | mmul=0, sfu=0 ≠ 固件缺 MMUL，是 descriptor 偏移错了 |

---

## 对 CaduceusCore 的可操作改进

### 1. Evidence 粒度升级

```
现在：task-w3t11.json → verdict=pass, npu_ops_executed=4887
建议：
  + hierarchy: 哪些模块参与？MXU/SFU/Vector 各自过了多少？
  + gap_summary: 哪些 op 被跳过？（非 pass/fail 二元）
  + file_line: 哪个文件哪一行验证的？
```

### 2. Evidence 链补全

当前 evidence 缺中间三段：
```
现在：todo fail → fix applied
缺：触发条件 → Test Plan 状态 → Regression 确认
```

给 signoff runner 加 `evidence_chain` 字段，强制每个 gate 五段证据齐全。

### 3. 误诊防护

文章最经典案例：FSM Coverage 66.7%→LLM 可能建议「增加 Error 测试」。实际上测试已写但 disabled、stimulus 在错误 Phase 注入。CaduceusCore 的 Agent 自动验证同样面临类似风险——需要结合 Spec/Plan/Regression 上下文判断，而非仅凭 pass/fail。
