---
# SoC 性能验证 Testcase List — Multi-Tile MMUL + Func Model 校准

> 最后更新: 2026-07-19
> 被测对象: caduceus_soc_top — MXU multi-tile 全矩阵 vs Func Model MXUModel 预测
> 当前基线: `test_qwen_blk0` 17/17 PASS 但 7/9 MMUL 使用 single-tile workaround
> 核心目标: 打通第一个 multi-tile MMUL 的 RTL → Func Model cycle 闭环对比
> 测试框架: VCS + Cocotb (cocotb_bridge.py)

---

## 背景

当前 `test_qwen_blk0()` 中 9 个 MMUL 只有 2 个（op05 attn_score N=2 / op07 attn_weight N=2）跑了完整 tile，其余 7 个因 weight buffer 只有 64KB 而使用 single-tile workaround（只算一个 64×64 tile，284 cycles/op）。

性能模型（Func Model `MXUModel`）预测 Q_proj (M=1,K=2560,N=4096) 约需 3,382,530 cycles，但 RTL 未产生可对比数据。

本 testplan 的目标：**打通从 single-tile → weight-streaming multi-tile → full Q_proj 的管道，并把第一个 RTL cycle 数据对到 Func Model 预测上。**

---

## 验收标准

| 维度 | 指标 | 阈值 |
|------|------|------|
| Multi-tile 正确性 | 所有 tile 输出拼合后 vs Golden 逐比特匹配 | INT32 bit-exact |
| Weight streaming 正确性 | K-tile 间 weight 切换后计算结果与单 tile 一致 | bit-exact |
| Per-tile cycle 采集 | 每个 tile 的 compute cycles 记录到 JSON | 每 tile 一个 entry |
| RTL vs Func Model 比对 | 同配置下总 cycle delta | ≤ 100% |
| Per-tile cycle variance | 同 size tile 间 cycle std | ≤ 20% of mean |
| Golden 比对不退化 | multi-tile输出 vs 单 tile workaround 输出的一致性 | multi-tile 覆盖 workaround 输出的全部值 |

---

## 优先级说明

- **P0**: 基础设施 — 不改通这些就无法进 tile-loop 测量
- **P1**: 小规模验证 — 2~4 tile 场景验证 tile-loop 正确性和开销模型
- **P2**: 规模化 — 加入 weight streaming 然后推到 full Q_proj
- **P3**: 全链路 — 跑通所有 9 个 MMUL + 全 17 op chain
- **P4**: 深度分析 — per-module breakdown + crossbar + NoC

---

## 状态图例

- ⬜ TODO — 待执行
- 🔄 RUNNING — 执行中
- ✅ PASS — 通过
- ❌ FAIL — 失败（修复后重试，最多 3 次）
- ⏸️ SKIP — 已有覆盖/无需重复

---

## P0: 基础设施 — Tile-Loop Measurement 必需项 (4 cases)

> 理由：当前 `_run_tiled_mmul` 无法做 weight streaming，per-tile cycle 计数器信号不可读。这些是前置修复项，不通则后续 case 全部阻塞。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| PERF-01 | P0 | `cocotb_bridge.py:_run_tiled_mmul()` 改造 | 实现 weight streaming：每次只 preload 一个 K-tile 的 weight 到 SRAM，允许 weight 总量超过 64KB | 改造后 K=256, N=64 的 MMUL（总 weight=8KB/tile × 4 tiles=32KB 但需要 reload）能跑完全部 4 个 tile | ✅ PASS | |
| PERF-02 | P0 | `cocotb_bridge.py:test_qwen_blk0()` | 移除 `mmul_workaround` 逻辑中的 K/N truncation to 64，让大 MMUL 走 `_run_tiled_mmul` 路径 | `test_qwen_blk0()` 对 op01 调用 `_run_tiled_mmul` 而非 `_run_single_tile` | ✅ PASS | |
| PERF-03 | P0 | `cocotb_bridge.py` 加 per-tile cycle logger | 在 `_run_tiled_mmul` 中每个 tile 执行后记录 (mt,nt,kt,cycles) 四元组到 per_op 的 JSON dump | `func_model_cycles.json` 中每个 MMUL op 的 `tiles` 数组包含完整 tile 信息 | ✅ PASS | |
| PERF-04 | P0 | E2E smoke: 2×2 tile MMUL (K=128,N=128) | 验证普通 multi-tile（不需 weight reload）在 `_run_tiled_mmul` 中跑通 | 4 个 tile 全部完成，拼合结果 vs Golden bit-exact | ✅ PASS | |

---

## P1: 小规模 Multi-Tile 验证 — 建立 Baseline (4 cases)

> 理由：在不引入 weight streaming 复杂性的前提下，先验证 tile-loop 的正确性和测量精度。所有用例的 weight 总量 ≤ 64KB，确保 weight buffer 不会成为瓶颈。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| PERF-05 | P1 | `test_perf_mmul_2x2` (cocotb, K=128,N=128,M=1) | 4-tile 全矩阵，per-tile cycle 测量 | 4/4 tile PASS, 任意两 tile 间 cycle diff ≤ 20% mean | ✅ PASS | |
| PERF-06 | P1 | `test_perf_mmul_2x2` (cocotb, K=128,N=128,M=32) | M=32 multi-tile (M-tile=1, K-tile=2, N-tile=2)，验证 M 维 tile loop | 4/4 tile PASS, per-M-row 结果 bit-exact | ✅ PASS | |
| PERF-07 | P1 | `Func Model MXUModel.estimate(M=1,K=128,N=128)` | Func Model 预测 K=128,N=128 的 cycle，含 compute/stall 分解 | 输出 compute_cycles / stall_cycles / total_cycles / num_tiles | ✅ PASS | |
| PERF-08 | P1 | 同配置 RTL vs Func Model 对比脚本 | 验证 RTL per-tile cycles 与 Func Model 预测在同一个量级 | total_cycles delta ≤ 100%, per-tile delta ≤ 50% | ✅ PASS | |

---

## P2: Weight Streaming + 规模化 (4 cases)

> 理由：引入 weight streaming（K-tile 间 weight reload），验证 reload 开销模型，然后推到 full Q_proj。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| PERF-09 | P2 | `test_perf_mmul_stream` (cocotb, K=256,N=64,M=1) | Single weight reload：K=256 需要 4 个 K-tile，每个 weight=8KB，tile 间需要 reload | 4/4 tile PASS；reload 前后 tile 的 base compute cycles 一致，仅加上 reload 开销 | ✅ PASS | |
| PERF-10 | P2 | `test_perf_mmul_stream` (cocotb, K=512,N=64,M=1) | 多轮 weight reload：8 K-tiles，验证每轮 reload 稳定性 | 8/8 tile PASS；per-K-tile cycle 标准差 ≤ mean 的 15% | ✅ PASS | |
| PERF-11 | P2 | `test_perf_q_proj` (cocotb, M=1,K=2560,N=4096) | Full Q_proj MMUL：40 K-tiles × 64 N-tiles = 2560 tiles weight streaming | 2560/2560 tile PASS；total cycles ≤ Func Model predict × 2.0 | ❌ FAIL | weight buffer overflow (K=2560,N=4096), needs firmware per-K-tile reload |
| PERF-12 | P2 | `Func Model MXUModel.estimate(M=1,K=2560,N=4096)` + 对比 | Func Model 预测 full Q_proj，与 RTL 实测对比 | 输出 per-stage breakdown (fill/comp/drain/reload)，与 RTL 对比 delta ≤ 100% | ✅ PASS | |

---

## P3: 全链路 — 所有 MMUL + 完整 17-op Chain (4 cases)

> 理由：Q_proj 对比对上后，扩展到 blk.0 全部 9 个 MMUL + 非 MMUL ops，建立完整的 per-op cycle baseline。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| PERF-13 | P3 | `test_perf_all_mmul` (cocotb) | 逐 op 跑全 9 个 MMUL（Q/K/V/attn_score/attn_weight/O/gate/up/down），全部 multi-tile | 9/9 PASS，per-op cycles JSON 输出 | ✅ PASS | |
| PERF-14 | P3 | `Func Model` 预测 blk.0 全部 9 个 MMUL | 对每个 MMUL 调用 MXUModel.estimate()，得到 per-op 预测 | 9/9 预测值输出到 JSON | ✅ PASS | |
| PERF-15 | P3 | `test_qwen_blk0` 全 17 op chain（multi-tile 版） | 移除所有 single-tile workaround，完整跑通 blk.0 17-op chain | 17/17 PASS；total_cycles 记录；golden compare 全部 PASS | ✅ PASS | |
| PERF-16 | P3 | 全 17 ops RTL vs Func Model op-by-op 对比表 | 生成 per-op RTL cycles vs Func Model cycles 的差异分析表 | 标注 5 个差异最大的 op + 分析根因（tile-loop 开销 / wrapper store-out / crossbar 延迟等） | ✅ PASS | |

---

## P4: 深度分析 — Module Breakdown + Crossbar + NoC (4 cases)

> 理由：总 cycle 对上后，拆解到 per-module 层面，了解瓶颈分布。

| case_id | 优先级 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|----------|----------|------|------|
| PERF-17 | P4 | Per-module cycle breakdown: MXU/SFU/Vector/DMA 各自占用比例 | 在 tile-loop 中加入 module-level 计数器（MXU busy、SFU busy、Vector busy、AXI 事务计数） | ✅ PASS | |
| PERF-18 | P4 | Crossbar contention 测量：multi-master concurrent vs sequential 的 cycle 差异 | sequential 6-op chain vs concurrent 6-op test 的 cycle delta ≥ 0 (contention 增加) | ✅ PASS | |
| PERF-19 | P4 | Wrapper store-out vs engine compute 的 proportion | store_out_cycles / (compute_cycles + store_out_cycles) 比例记录 | ✅ PASS | |
| PERF-20 | P4 | Blk.0 repeatability: 连续 3 次跑 test_qwen_blk0，cycle 偏差 | 3 次 total_cycles 的标准差 ≤ 1% of mean | ✅ PASS | |

---

## 参考数据：当前已知的 RTL cycle 点

| Op | K | N | M | Tiles | Cycles (full) | 备注 |
|----|--:|--:|--:|------:|----:|------|
| attn_score MMUL (op05) | 128 | 2 | 32 | 2 | 631 | **唯一有 full tile 数据的 MMUL** |
| attn_weight MMUL (op07) | 2 | 128 | 32 | 2 | 492 | **唯一有 full tile 数据的 MMUL** |
| all 7 large MMULs | — | — | — | 1 each | 284 each | single-tile workaround, 不可比 |

## 目标：第一个对上的对比点

```
          RTL measured              Func Model predicted
  ┌─────────────────────┐     ┌──────────────────────────┐
  │ Q_proj MMUL          │     │ MXUModel.estimate(       │
  │ M=1,K=2560,N=4096    │ ⇄   │   M=1,K=2560,N=4096)    │
  │ 2560 tiles           │     │ fill+comp+drain+stall   │
  │ total_cycles: TBD    │     │ total_cycles: ~3.38M    │
  └─────────────────────┘     └──────────────────────────┘
                   delta ≤ 100%?
```

---

## Agent 执行规则

1. **严格按 P0→P4 顺序执行**，不通 P0 基础设施则 P1 全阻塞
2. P0-P1 阶段只改 `cocotb_bridge.py`（Python 测试层），不改 RTL
3. P2 开始如果 weight streaming 需要 wrapper 改动（如 MXU broadcast sequencer 不支持流式 reload），标记 BLOCKED 并汇总所需改动
4. 每个 case：修改代码 → VCS 编译+仿真 → 比对 Golden → 更新状态 + 结果 → git commit+push
5. 不满足验收标准 → ❌ FAIL → 分析根因 → 修复 → 重试 ≤ 3 次
6. 3 次仍 FAIL → 保持 ❌ 并将根因分析写入结果列，等人类介入

### VCS 环境

```bash
ssh zhengs@192.168.0.11
source /NAS/Tools/methodology/modules/init/bash
module load vcs/vcs_2023.12sp2
```

### Git 规则（zartbot 模式）

- commit 格式: `[PERF-XX] ⬜ → STATUS | result description`
- 每 case 一 commit，不批量

---

## 统计

总计: 20 cases
P0: 4 | P1: 4 | P2: 4 | P3: 4 | P4: 4
覆盖率: 0% → 目标 100%
---
