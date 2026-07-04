# SFU + Vector Engine — Module-Level Performance Test Plan

> 最后更新: 2026-07-03
> 被测对象: `CaduceusCore/rtl/sfu/` (8 files, 2,678 lines) + `CaduceusCore/rtl/vector/` (5 files, 1,094 lines)
> 参考实现: `CaduceusCore/sim/golden_executor.py` (GoldenSFU / GoldenVector), `CaduceusCore/sim/models/sfu.py`, `CaduceusCore/sim/models/vector.py`
> 方法论: zartbot pattern — Agent 读源码→推导周期公式→VCS 测量→写回状态
> 参考模板: `CaduceusCore/rtl/testcase-list-mxu-perf.md` (MXU perf, 18 cases, all ✅)

---

## 方法论：两层性能验证

> **核心原则**: Func Model 是架构 Spec，RTL FSM 是实现。两者分开验证，分层不混淆。

本 testcase list 采用两层验收体系（与 MXU 一致，MX-P15 为架构校准 case）：

| 层级 | 验收标准来源 | 容差 | 用途 |
|------|------------|:---:|------|
| **Tier 1 — 实现一致性** | RTL FSM 设计意图公式（从 RTL pipeline/FSM 结构推导） | `|delta| ≤ 1-5`（紧） | 防 RTL bug：FSM 死锁、pipeline 挂起、状态跳变错误 |
| **Tier 2 — 架构校准** | Func Model.estimate() 预测值（`sim/models/sfu.py`, `vector.py` + `npu_config.yaml`） | 记录差距，不设硬性阈值 | 防架构漂移：RTL 实现是否偏离架构意图？差距是否可解释？ |

**Func Model 预测公式**（配置来自 `sim/config/npu_config.yaml`，`sfu.width=128`, `vector.width=128`）：

```
SFUModel.estimate(op, N)  = ceil(N / 128) × pipeline_depth
  其中 pipeline_depth: gelu=4, silu=4, rope=12, softmax=8, layernorm=6, exp=12, div=16

VectorModel.estimate(op, N) = ceil(N / 128) × op_latency
  其中 op_latency: add=1, mul=1, max=1, resid_add=1, sum_reduce≈3
  注意：`sim/models/vector.py` 中部分 op 未预定义（max, resid_add, sum_reduce），校准 case 需标注 "Model N/A", conv≈ceil(N/128)×132*  
   * CONV 的 Func Model 因 type_convert 为 1-element/cycle 需扩展；见 SFV-P28_calib
```

> **注意**: Func Model 是 batch 级抽象（假设 SFU 每 cycle 处理 width=128 个元素），RTL 实际有 FSM 多遍 SRAM 遍历开销。差距可达 4x-48x，这在架构层面是可解释的。Tier 2 校准 case 的目的是**透明记录**这个差距，不是判断"通过/失败"。

---

## 验收标准

| 模块 | 指标 | 阈值 | 来源 | 理由 |
|------|------|------|:---:|------|
| SFU streaming ops | 周期偏差 | `|delta| ≤ 1 cycle` | Tier 1 | 固定流水线深度，确定性 |
| SFU reduction ops | 周期偏差 | `|delta| ≤ 5 cycles` | Tier 1 | FSM 多遍处理，内部迭代器时序抖动 |
| Vector ALU/resid ops | 周期偏差 | `|delta| ≤ 1 cycle` | Tier 1 | 确定性的块迭代器 |
| Vector SUM/MAX reduce | 周期偏差 | `|delta| ≤ 1 cycle` | Tier 1 | 确定性的 reduce_tree 流水线 |
| Vector CONV | 周期偏差 | `|delta| ≤ 1 cycle` | Tier 1 | 确定性的 type_convert 流水线 |
| SFU Func Model calib | 周期差距 | 记录差距，不判 PASS/FAIL | Tier 2 | 批量模型 vs 逐 cycle 实现的架构差异 |
| Vector Func Model calib | 周期差距 | 记录差距，不判 PASS/FAIL | Tier 2 | 同上 |
| Roundtrip bit-exact | 数据完整性 | 所有 golden 比较 PASS | — | 防功能回归 |
| MMIO 时序 | 信号延迟 | BUSY 在 CMD 后 ≤ 2 cycles 内上升 | Tier 1 | 控制路径性能 |
| 反真空断言 | 活动性检查 | 每个 case 所有检查 PASS | — | 验证 DUT 确实在工作 |

---

## 优先级说明

- **P0**: 每个操作的单元素/最小向量基线 — 建立测量基础设施并推导精确的周期公式
- **P1**: 参数扫描（元素数量、位置、DIM）— 验证周期公式在操作范围内线性缩放
- **P2**: 背靠背吞吐量 — 测量连续操作之间的间隙以及流水线利用率
- **P3**: 边缘情况 — 最小/最大尺寸、部分块、饱和边界

---

## 状态图例

- ⬜ TODO — 待执行
- 🔄 RUNNING — 执行中
- ✅ PASS — 通过
- ❌ FAIL — 失败（修复后重试，最多 3 次）
- ⏸️ SKIP — 已有覆盖/无需重复

---

## 模块性能特征 — 参考

### SFU 流水线深度

| 操作 | 流水线风格 | 流水线深度 | 吞吐量 | FSM 状态 | 周期公式（每个 N 元素向量） |
|----------|----------------|:----:|-----------|:---:|-----|
| **gelu_hw** | 流式 4 阶段 | 4 | 1 元素/周期 | 无 | `N + 7` |
| **silu_hw** | 流式 4 阶段 | 4 | 1 元素/周期 | 无 | `N + 7` |
| **rope_hw** | 流式 16 阶段 CORDIC | 16 | 1 对/周期 | 无 | `N + 19` |
| **softmax_hw** | 3 遍顺序 + 24 步除法器 | 可变 (3N+31) | 1/周期 在遍历时 | 10 | `3N + 33` |
| **layernorm_hw** | 3 遍顺序 + 12 步 sqrt | 可变 (3N+15) | 1/周期 在遍历时 | 8 | `3N + 17` |
| **rmsnorm_hw** | 2 遍顺序 + 8 sq+8 recip | 可变 (2N+19) | 1/周期 在遍历时 | 8 | `2N + 21` |

*注意: 周期公式包括 `sfu_top` 开销（2 周期 MMIO 启动 + 1 周期 READ_INIT + 1 周期 DONE）。`sfu_top` 在 ST_RUN 中提供元素，并等待子模块在 ST_FLUSH 中完成流水线。通过 VCS 仿真验证。*

### Vector 每个块的周期

| 操作 | 数据路径 | 每个块的周期 (128 元素) | 总周期（N 个元素） |
|----------|------|:---:|-----|
| **ADD / MUL / MAX / RESID** | `vector_alu` / `resid_add` (1-周期) | 4 | `ceil(N/128) × 4 + 2` |
| **SUM** (规约) | `reduce_tree` (7-周期) | 10 | `ceil(N/128) × 10 + 2` |
| **CONV** (INT32→FP16) | `type_convert` (1-周期，逐个元素) | 132 | `ceil(N/128) × 132 + 2` |

*注意: Vector 分块开销包括 1 周期 READ + 1 周期 LATCH + 子模块执行。启动延迟：从 MMIO START 写入到第一个 SRAM 读取约 2 周期。*

> ⚠️ **公式验证状态**: 以上所有周期公式均从 RTL FSM/pipeline 源码分析推导，**尚未经过 VCS 实测基线校准**。在执行 P0 基线 case 时，需实测每个 op 的一个 N 值，确认 `|measured - formula| ≤ tolerance`。若公式偏差超过容差，更新公式并记录修正于 testcase list。

---

## P0: 每个操作的基线 — 最先执行

> 理由：建立所有后续 case 的测量基础设施和周期公式。每个操作必须至少有一个精确的基线测量。

### P0-SFU: SFU 操作基线

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| SFV-P01 | P0 | `tb_sfu_perf.v` — `--op softmax --dim 64` | **Softmax 基线 N=64**: 测量总周期 + 每个 FSM 状态的周期分解 | `total_cycles ≤ 3×64+33 = 225` (≤5 周期容差)。Golden ML 比较 PASS（float16 容差） | ⬜ | |
| SFV-P02 | P0 | `tb_sfu_perf.v` — `--op layernorm --dim 64` | **LayerNorm 基线 N=64**: 总周期 + 3 遍分解（PASS1_SUM, PASS2_SUB_SQ, PASS3_NORM） | `total_cycles ≤ 3×64+17 = 209` (≤5 周期容差)。Golden ML 比较 PASS | ⬜ | |
| SFV-P03 | P0 | `tb_sfu_perf.v` — `--op rmsnorm --dim 64` | **RMSNorm 基线 N=64**: 总周期 + 2 遍分解（PASS1 累加, PASS2 归一化） | `total_cycles ≤ 2×64+21 = 149` (≤5 周期容差)。Golden ML 比较 PASS | ⬜ | |
| SFV-P04 | P0 | `tb_sfu_perf.v` — `--op gelu --dim 64` | **GELU 流式基线 N=64**: 总周期 + 确认每周期 1 元素的吞吐量 | `total_cycles ≤ 64+7 = 71` (≤1 周期容差)。Golden ML 比较 PASS | ⬜ | |
| SFV-P05 | P0 | `tb_sfu_perf.v` — `--op silu --dim 64` | **SiLU 流式基线 N=64**: 总周期 + 确认每周期 1 元素的吞吐量 | `total_cycles ≤ 64+7 = 71` (≤1 周期容差)。Golden ML 比较 PASS | ⬜ | |
| SFV-P06 | P0 | `tb_sfu_perf.v` — `--op rope --dim 64 --pos 0` | **RoPE 基线 N=64 对**: 总周期 + 确认每周期 1 对的吞吐量 | `total_cycles ≤ 64+19 = 83` (≤1 周期容差)。Golden ML 比较 PASS（float16 容差） | ⬜ | |
| SFV-P07 | P0 | `tb_sfu_perf.v` — MMIO 时序 | **SFU MMIO 时序**: 测量 `CMD.START → STATUS.BUSY` 延迟和 `STATUS.DONE → irq` 延迟 | `BUSY` 在 2 cycles 内上升，`DONE` 在 IRQ 前 1 cycle 置位，`irq` 脉冲持续 1 cycle | ⬜ | |

### P0-Vector: Vector 操作基线

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| SFV-P08 | P0 | `tb_vector_perf.v` — `--op add --dim 128` | **ADD 基线 N=128（1 块）**: 总周期 + 每个 FSM 状态的分解 | `total_cycles ≤ 4+2 = 6` (≤1 周期)。Golden INT32 逐位精确 | ⬜ | |
| SFV-P09 | P0 | `tb_vector_perf.v` — `--op mul --dim 128` | **MUL 基线 N=128**: 与 ADD 相同（相同的 1-cycle ALU 路径） | `total_cycles ≤ 6` (≤1 周期)。Golden INT32 逐位精确（非溢出值） | ⬜ | |
| SFV-P10 | P0 | `tb_vector_perf.v` — `--op max --dim 128` | **MAX 基线 N=128**: 每元素比较，相同的 1-cycle ALU 路径 | `total_cycles ≤ 6` (≤1 周期)。Golden INT32 逐位精确 | ⬜ | |
| SFV-P11 | P0 | `tb_vector_perf.v` — `--op sum --dim 128` | **SUM 规约基线 N=128（1 块）**: 总周期 + 确认 7-cycle reduce_tree 流水线 | `total_cycles ≤ 10+2 = 12` (≤1 周期)。Golden INT32 逐位精确 | ⬜ | |
| SFV-P12 | P0 | `tb_vector_perf.v` — `--op conv --dim 128` | **CONV 基线 N=128（1 块）**: 逐个元素的 INT32→FP16，最慢路径 | `total_cycles ≤ 132+2 = 134` (≤1 周期)。Golden ML 比较 PASS（float16 容差） | ⬜ | |
| SFV-P13 | P0 | `tb_vector_perf.v` — `--op resid --dim 128` | **RESID 基线 N=128**: 与 ADD 相同（相同的 1-cycle resid_add） | `total_cycles ≤ 6` (≤1 周期)。Golden INT32 逐位精确（非溢出值） | ⬜ | |
| SFV-P14 | P0 | `tb_vector_perf.v` — MMIO 时序 | **Vector MMIO 时序**: 测量 `CMD.START → STATUS.BUSY` 和 `DONE → irq` | `BUSY` 在 2 cycles 内上升，`DONE` 迟滞 1 cycle，`irq` 持续 1 cycle | ⬜ | |

---

## P1: 参数扫描 — 缩放行为

> 理由：验证周期公式随参数（N、位置、DIM）线性缩放。确认流水线深度在跨越块边界时保持不变。

### P1-SFU: SFU 元素数量扫描

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| SFV-P15 | P1 | `tb_sfu_perf.v` — `--op softmax --dim 16,32,64,128,256,512,1024,2048,4096` | **Softmax N 扫描（8 个点）**: 验证 total_cycles = 3N + C（线性缩放）。标记首次出现非线性行为的点 | 每个点 `|delta| ≤ 5 cycles`。绘制周期 vs N 曲线；斜率为 ~3.0 | ⬜ | |
| SFV-P16 | P1 | `tb_sfu_perf.v` — `--op layernorm --dim 16,32,64,128,256,512,1024,2048,4096` | **LayerNorm N 扫描（8 个点）**: 验证 3 遍顺序缩放 | 每个点 `|delta| ≤ 5 cycles`。斜率 ~3.0 | ⬜ | |
| SFV-P17 | P1 | `tb_sfu_perf.v` — `--op rmsnorm --dim 16,32,64,128,256,512,1024,2048,4096` | **RMSNorm N 扫描（8 个点）**: 验证 2 遍顺序缩放（比 LN 快 ~50%） | 每个点 `|delta| ≤ 5 cycles`。斜率 ~2.0。N=4096 时确认相对于 layernorm 的加速 | ⬜ | |
| SFV-P18 | P1 | `tb_sfu_perf.v` — `--op gelu --dim 16,64,256,1024,4096` | **GELU 吞吐量扫描（5 个点）**: 验证固定 4-cycle 流水线深度与 N 无关 | 每个点 `|delta| ≤ 1 cycle`。斜率 ~1.0（纯流式） | ⬜ | |
| SFV-P19 | P1 | `tb_sfu_perf.v` — `--op rope --dim 16,32,64,128` — `--pos 0,42,100,127` | **RoPE 扫描：N×位置（4×4 格子）**: 验证延迟与 N 成比例且与位置无关 | 每个点 `|delta| ≤ 1 cycle`。斜率 ~1.0。位置必须不影响周期计数 | ⬜ | |

### P1-Vector: Vector DIM 扫描

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| SFV-P20 | P1 | `tb_vector_perf.v` — `--op add --dim 128,256,512,1024,2048,4096` | **ALU ops DIM 扫描（6 个点）**: 验证 total = ceil(N/128)×4 + 2。确认块边界开销恒定 | 每个点 `|delta| ≤ 1 cycle`。每个块周期必须相同 | ⬜ | |
| SFV-P21 | P1 | `tb_vector_perf.v` — `--op sum --dim 128,256,512,1024,2048,4096` | **SUM DIM 扫描（6 个点）**: 验证 total = ceil(N/128)×10 + 2。确认每个块 7-cycle 流水线 | 每个点 `|delta| ≤ 1 cycle`。每个块周期必须相同 | ⬜ | |
| SFV-P22 | P1 | `tb_vector_perf.v` — `--op conv --dim 128,256,512,1024,2048,4096` | **CONV DIM 扫描（6 个点）**: 验证 total = ceil(N/128)×132 + 2。确认 type_convert 逐个元素处理 | 每个点 `|delta| ≤ 1 cycle`。每个块 132 cycles 恒定 | ⬜ | |

---

## P2: 吞吐量与架构校准 — 背靠背 + Func Model 对比

> 理由：P2 有两类验证。**(a) 背靠背吞吐量**：transformer 推理以连续序列运行 SFU/Vector 操作，操作间间隙导致流水线气泡。**(b) 架构校准**：对比 Func Model 预测 vs RTL 实测，验证 RTL 没有偏离架构意图太远，并透明记录差距（参见方法论文档）。

### P2-SFU: 背靠背 + 校准

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| SFV-P23 | P2 | `tb_sfu_perf.v` — `--repeat 10` — softmax N=64 背靠背 | **Softmax 背靠背**: 10 个连续的 softmax 操作。测量操作间间隙 + 确认没有状态泄漏 | 间隙 `≤ 5 cycles`。所有 10 次运行的周期标准差 `≤ 1 cycle`。所有 golden 比较 PASS | ⬜ | |
| SFV-P24 | P2 | `tb_sfu_perf.v` — 混合 5 个操作序列，重复 3 次 | **混合操作背靠背**: 在单次仿真运行中切换 5 种 SFU 操作。测量操作间切换开销 | 每个操作间间隙 `≤ 5 cycles`。总计 15 次 golden 比较 PASS | ⬜ | |
| SFV-P25_calib | P2 | `SFUModel.estimate()` vs RTL 实测 — 所有 6 个 SFU 操作，N=128,1024,4096 | **SFU Func Model 架构校准**: 对每个操作和三种 N 大小，计算 `ceil(N/128)×pipeline_depth`(Func Model) vs RTL 实测周期。**不判 PASS/FAIL** — 只记录差距 | 记录每个 (op, N) 的 `RTL/FuncModel` 比值。预期 Softmax 40-50x, Gelu/Silu 20-35x, RoPE 5-10x, Layernorm 40-55x, RMSNorm 30-42x。超出范围在根因分析列标注异常，但**不改变 case 的 ⬜/✅ 状态** | ⬜ | |

### P2-Vector: 背靠背 + 校准

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| SFV-P26 | P2 | `tb_vector_perf.v` — `--repeat 10` — ADD N=128 | **Vector 背靠背**: 10 个连续的 ADD 操作。测量间隙 | 间隙 `≤ 5 cycles`。所有运行的周期标准差 `≤ 1 cycle`。所有 golden 比较 PASS | ⬜ | |
| SFV-P27 | P2 | `tb_vector_perf.v` — 混合 6 个操作序列，重复 2 次 | **混合操作背靠背**: 切换所有 6 种 Vector 操作。测量操作间切换开销 | 操作间间隙 `≤ 5 cycles`。总计 12 次 golden 比较 PASS | ⬜ | |
| SFV-P28_calib | P2 | `VectorModel.estimate()` vs RTL 实测 — 所有 6 个 Vector 操作，DIM=128,1024,4096 | **Vector Func Model 架构校准**: `ceil(DIM/128)×op_latency`(Func Model) vs RTL 实测周期。**不判 PASS/FAIL** | 记录每个 (op, DIM) 的 `RTL/FuncModel` 比值。预期 ADD/MUL/MAX/RESID 3-6x, SUM 2-4x, CONV 0.8-1.3x。超出范围在根因分析列标注异常，但**不改变 case 状态**。若 `VectorModel` 不支持某 op，在对比表中标注 "Model N/A" 并跳过比值计算 | ⬜ | |

---

## P3: 边缘情况 — 边界和极端值

> 理由：最小尺寸（N=1）和最大尺寸（N=4096）的路径通常具有特殊的 FSM 分支。部分块未使用的通道需要掩码验证。

### P3-SFU

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| SFV-P29 | P3 | `tb_sfu_perf.v` — 所有 6 个操作 `--dim 1` | **单元素边缘（N=1）**: 所有 SFU 操作的最小合法输入。LN,N=1 → 输出强制为 0；RMS,N=1 → 输出为符号(x) | 所有操作 golden 比较 PASS。每个 op 的 `|measured - formula(N=1)| ≤ 5`（N=1 不应触发多遍遍历；例如 softmax `|measured-36| ≤ 5`） | ⬜ | |
| SFV-P30 | P3 | `tb_sfu_perf.v` — `--op softmax --dim 4096` + `--op layernorm --dim 4096` | **最大尺寸 N=4096**: 用于软最大值的 VEC_MAX 上限 + 用于 LN/RMS 的 MAX_LEN。确认没有溢出或状态机超时 | 两个操作 golden 比较 PASS。softmax `|measured - (3×4096+33)| ≤ 5`，layernorm `|measured - (3×4096+17)| ≤ 5` | ⬜ | |

### P3-Vector

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| SFV-P31 | P3 | `tb_vector_perf.v` — `--op add --dim 1` + `--op sum --dim 1` + `--op conv --dim 1` | **单元素边缘（DIM=1）**: 最小 Vector 操作。验证 lane_mask 正确处理部分 128-wide 块 | 所有操作 golden 比较 PASS。周期计数必须为常量（无块迭代循环）。CONV N=1 → 无块循环开销 | ⬜ | |
| SFV-P32 | P3 | `tb_vector_perf.v` — `--op add --dim 65535` | **最大 DIM（65,535）**: DIM 寄存器允许的最大值。确认无溢出、无超时、所有 512 个块正确累加 | golden 比较 PASS。实际周期数 = ceil(65535/128)×4+2 = 512×4+2 = 2,050 | ⬜ | |
| SFV-P33 | P3 | `tb_vector_perf.v` — `--op conv --dim 256` — INT32 值扫描覆盖完整 MXU 累加器范围 [-2³¹, 2³¹-1] 附近的饱和 | **CONV 饱和边缘**: 验证每个 type_convert 规格将 |x|>65504 的元素饱和到 ±0x7BFF | 所有元素 golden 比较 PASS。接近 65504 边界时无误舍入。INT32_MIN (-2³¹) → 正确的饱和值 | ⬜ | |
| SFV-P34 | P3 | `tb_sfu_perf.v` — `--op rope --pos 0,1,42,127,255,511,1023` | **RoPE 大位置值**: 测试 theta = pos × inv_freq 的角度累积。确认大位置的 CORDIC 精度 | 所有位置 golden 比较 PASS（float16 容差）。周期计数与位置无关 | ⬜ | |

---

## Agent 执行规则

1. **严格按 P0→P3 顺序执行**，不跳级
2. 每个 case：推导预期周期 → 生成测试向量 → VCS 编译 + 仿真 → 比较 golden → 分析周期 → 更新状态
3. 不满足验收标准 → ❌ FAIL → 分析根本原因（FSM/Pipeline RTL 错误或公式错误）→ 修复 → 重试（最多 3 次）
4. 3 次仍 FAIL → 保持 ❌ 等待人类介入
5. 可自主新增 case（Agent 发现未列出的方法/边界）

### Cycle 测量基础设施要求

在执行任何 case 之前，必须创建以下内容：

1. **`CaduceusCore/rtl/tb/tb_sfu_perf.v`**：扩展 `tb_sfu.v`。添加每 FSM 状态周期计数器（`cnt_IDLE, cnt_ST_READ_INIT, cnt_ST_RUN, cnt_ST_FLUSH, cnt_ST_DONE, cnt_TOTAL`）。添加 PERF 发射任务（`emit_perf`, `emit_perf_tile`）。添加反真空断言（`sram_ren` 切换, `sram_wen` 切换, `status_done` 精确脉冲 1 次）。遵循与 `tb_mxu_perf.v` 相同的模式。

2. **`CaduceusCore/rtl/tb/tb_vector_perf.v`**：扩展 `tb_vector.v`。添加每 FSM 状态周期计数器（13 状态 FSM + `cnt_TOTAL`）。添加块计数器。添加 PERF 发射任务。添加反真空断言（`sram_a_en`/`sram_b_en` 切换, `sram_o_wen` 切换, `status_done` 精确脉冲 1 次）。

3. **`CaduceusCore/scripts/analyze_sfu_perf.py`**：SFU 周期公式 + VCS PERF 日志解析器。导出每个操作的 `expected_cycles()`。解析标准化的 `PERF|case=X|event=E|cycles=N` 行。判决逻辑：流式操作 `|delta| ≤ 1`，规约操作 `|delta| ≤ 5`。

4. **`CaduceusCore/scripts/analyze_vector_perf.py`**：Vector 周期公式 + VCS PERF 日志解析器。导出每个操作和 DIM 的 `expected_cycles()`。判决逻辑：`|delta| ≤ 1`。

5. **`CaduceusCore/scripts/run_sfu_perf_case.py`**：与 `run_mxu_perf_case.py` 相同的步骤管道（gen_vectors → SCP → VCS compile → simulate → compare_rtl → analyze_perf → evidence → commit）。仅将引擎特定的路径和命令适配到 SFU。

6. **`CaduceusCore/scripts/run_vector_perf_case.py`**：与上面对 Vector 引擎的适配相同。

### Git 规则（zartbot 模式）

每次 testcase-list.md 状态变化 = 一次 git commit

commit 格式: `[SFV-PXX] ⬜ → STATUS | result description`

原则:
  - 每完成一个 case（无论 PASS/FAIL）立即 commit
  - 不允许批量攒多个 case 再 commit
  - 修复后重新测试也要单独 commit
  - `git log testcase-list-sfu-vector-perf.md` = 完整测试执行时间线

### PERF 行格式（跨引擎标准化）

所有引擎必须使用完全相同的 `$display` 格式：

```
PERF|case={case_id}|shape/op={params}|event={event_name}|cycles={count}
```

对于每个引擎，{params} 替换为：
- SFU: `op={op_name},dim={N}`（例如，`op=softmax,dim=64`）
- Vector: `op={op_name},dim={N}`（例如，`op=add,dim=128`）
- SFU RoPE 扩展: `op=rope,dim={N},pos={P}`（例如，`op=rope,dim=64,pos=42`）

**VCS 版本**: SFU/Vector 必须使用 `vcs/vcs_2023.12sp2`（W-2024.09-SP2 在 EDA 服务器上存在 `rmapats.so` 编译错误；参见 `rtl/sfu/README.md:165` 和 `rtl/vector/README.md:128`）

这确保 `analyze_sfu_perf.py` 和 `analyze_vector_perf.py` 可以共享相同的正则表达式解析器。

### 反真空断言（每个引擎）

每个性能测试平台必须验证 DUT 确实在做工作：

| 引擎 | 断言 |
|--------|----------|
| SFU | `sram_ren` 在操作期间切换 ≥ N/2 次（每字 2 个元素）。`sram_wen` 在操作期间切换 ≥ N/2 次。`status_done` 精确脉冲 1 次。`status_busy` 在 CMD 后的 2 cycles 内上升 |
| Vector | `sram_a_en` 切换 ≥ ceil(N/128) 次。`sram_o_wen` 切换 ≥ ceil(NElemOutput/128) 次。对于 SUM：`reduce_valid_o` 每个块脉冲 1 次。`status_done` 精确脉冲 1 次 |

---

## 统计

总计:     34 cases
P0:       14 cases (7 SFU + 7 Vector) — 所有操作基线
P1:        8 cases (5 SFU + 3 Vector) — 参数扫描
P2:        6 cases (3 SFU + 3 Vector) — 背靠背 (4) + Func Model 架构校准 (2)
P3:        6 cases (2 SFU + 4 Vector) — 边缘情况

Tier 1 (实现一致性): 32 cases — 紧容差验收
Tier 2 (架构校准):   2 cases — SFV-P25_calib (SFU), SFV-P28_calib (Vector) — 记录差距，不判 PASS/FAIL
─────────────────────
覆盖率:    0% → 目标 100%
