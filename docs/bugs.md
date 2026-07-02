# Bug Tracking — MXU 模块级性能验证

> 更新: 2026-07-02
> 被测对象: `rtl/mxu/` — 64×64 Broadcast MAC Array (8 RTL files, 1,304 lines)
> 测试用例: `rtl/testcase-list-mxu-perf.md` — 18 cases (MX-P01..MX-P18)
> 前序记录: [`docs/issues_found.md`](issues_found.md) (Func Model 开发阶段问题)

## 使用规则

1. 性能验证中发现的 **每个 bug** 都必须记录在此文件
2. 新 bug 始终 **追加** (append) 到末尾, 不覆盖已有条目
3. RTL bug 必须包含「详细根因分析」(Detailed Root Cause Analysis) 子章节
4. 修复后更新 Fix / Verification / Status 字段, 不删除原条目
5. 每个 bug 可附带独立分析文件 `docs/bugs/BUG-XXX.md`, 但 bugs.md 必须保留摘要

## 严重级别 (Severity)

| 标签 | 定义 |
|------|------|
| **Critical** | 功能错误或周期偏差 > 25% — 阻塞后续 P0-P4 case 执行 |
| **Major** | 周期偏差 1~25% 或非功能性设计缺陷 |
| **Minor** | 周期偏差 ≤ 1 cycle 但测试可复现, 可能需要设计确认 |
| **Trivial** | 文档错误、信号命名不一致、日志格式问题等 |

## 类型标签 (Type)

| 标签 | 定义 |
|------|------|
| **RTL** | Verilog 逻辑错误 — FSM、datapath、pipeline |
| **Testbench** | `tb_mxu_perf.v` 测量逻辑错误 |
| **Script** | Python 脚本 (analyze_perf.py, gen_mxu_vectors.py 等) |
| **Environment** | VCS 编译、EDA server、module 加载等问题 |
| **Formula** | 预期 cycle 公式推导错误 |
| **Tooling** | 分析工具、diff 脚本、CI 等问题 |

## Bug 条目模板

每个 bug 记录遵循以下结构。RTL 类型必须包含「详细根因分析 (Detailed Root Cause Analysis)」子章节。

```
### BUG-MX-PERF-001

| 字段 | 内容 |
|------|------|
| **Date** | YYYY-MM-DD |
| **Case** | MX-PXX (关联测试 case ID) |
| **Severity** | Critical / Major / Minor / Trivial |
| **Type** | RTL / Testbench / Script / Environment / Formula / Tooling |
| **Status** | Open / Fixed / Won't Fix / Duplicate |
| **Found by** | Agent / Human |

#### Symptom (症状)

简要描述观察到的失败现象或周期偏差。

#### Root Cause (根因)

描述根本原因。RTL bug 必须包含「详细根因分析 (Detailed Root Cause Analysis)」。

#### Detailed Root Cause Analysis (详细根因分析)

> 仅 RTL 类型需要此章节。以下为必需内容。

1. **涉及模块**: 受影响 RTL 文件及行号范围
2. **触发条件**: 什么配置或数据序列触发该 bug
3. **机制分析**: 从 RTL 源码层面描述信号/状态错误传播路径
4. **影响范围**: 哪些 case、哪些配置受影响; 是否影响功能正确性
5. **为什么未被前期功能测试发现**: 分析功能测试 (MX-01..MX-16) 覆盖率缺口

#### Fix (修复)

描述修复方式, 涉及的文件和修改概要。Status=Fixed 时必填。

#### Verification (验证)

修复后如何验证。Status=Fixed 时必填。

#### References (参考)

相关 commit hash、issue 链接、波形文件路径等。
```

---

## Bug 日志

<!-- 每发现一个 bug, 在下方按模板追加一条新记录。不要覆盖已有条目。 -->

### BUG-MX-PERF-000 (占位示例 — 非真实 Bug)

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-02 |
| **Case** | MX-PXX |
| **Severity** | Minor |
| **Type** | Testbench |
| **Status** | Fixed |
| **Found by** | Agent |

#### Symptom (症状)

`tb_mxu_perf.v` 中 `perf_cycle` 计数器在 `$display` 输出中显示 `READ_DIMS=0`, 导致首 tile 缺少 1 cycle。

#### Root Cause (根因)

计数器使用 `if (perf_counting)` 控制累加, 而 `perf_counting` 在 `READ_DIMS` 状态后一个 cycle 才拉高, 导致 `READ_DIMS` 期间未被计入。

#### Detailed Root Cause Analysis (详细根因分析)

> 注: 此章节为 RTL bug 专用。此处为 Testbench bug, 仅用于格式示例。

N/A — Testbench 类型不需要此章节。

#### Fix (修复)

将累加条件从 `if (perf_counting)` 改为 `if (state != S_IDLE && state != S_DONE)`, 确保 FSM 进入 READ_DIMS 即开始计数。

#### Verification (验证)

重新运行 MX-P01 (shape=64,64,64): `total=134`, `cnt_read_dims=1`, 与公式预期一致。P0 三个 case 全部 PASS。

#### References (参考)

- Commit: `a1b2c3d4`
- 见 learnings.md 2026-07-02 Phase 0b 条目

---

## 统计

| 指标 | 值 |
|------|:---:|
| Bug 总数 | 0 |
| Open | 0 |
| Fixed | 0 |
| Won't Fix | 0 |
| Duplicate | 0 |
| Critical | 0 |
| Major | 0 |
| Minor | 0 |
| Trivial | 0 |
| RTL | 0 |
| Testbench | 0 |
| Script | 0 |
| Environment | 0 |
| Formula | 0 |
| Tooling | 0 |
