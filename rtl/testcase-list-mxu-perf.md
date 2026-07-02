# MXU Module-Level Performance Characterization Test Plan

> 最后更新: 2026-07-02
> 被测对象: `rtl/mxu/` — 64×64 Broadcast MAC Array (8 RTL files, 1,304 lines)
> 参考模型: `sim/models/mxu.py` (MXUModel, 128×128 config — **注意**: array size 与 RTL 64×64 不匹配, 见 Section 10)
> 测试框架: VCS + `tb_mxu.v` (模块级, 直接广播驱动, 无 crossbar/wrapper 开销)
> 方法论: zartbot pattern — Agent 读 RTL → 设计 cycle 测量 testbench → VCS 仿真 → 写回状态
> 目标时钟: 1 GHz (1 ns/cycle — 所有指标以 cycle 为单位)

---

## 1. 背景

### 1.1 为什么需要独立的模块级性能测试

现有的 [testcase-list-perf.md](testcase-list-perf.md) (PERF-01..PERF-20) 覆盖 **SoC 级** multi-tile MMUL
性能验证——包含 Cocotb wrapper、AXI crossbar、DMA 等系统效应。该测试是端到端 Pipeline 驱动(Qwen blk.0 场景),
wrapper store-out 序列化、crossbar contention 和 DMA 反压会混入总 cycle 计数。

**本文件**聚焦 **模块级 (MXU ⊆ tb_mxu.v) 纯 RTL 性能特征**:

| 维度 | SoC 级 (PERF-*) | 模块级 (本文件 MX-P*) |
|------|-----------------|---------------------|
| 测试接口 | Cocotb + AXI wrapper | VCS + tb_mxu.v, 直接广播驱动 |
| 干扰项 | DMA、crossbar、wrapper store-out | 无干扰 — 纯 MXU FSM + PE 网格 |
| measurement | `func_model_cycles.json` per-op | FSM state 计数器 (debug ports) |
| Func Model 参考 | MXUModel with DMA overlap | 仅 MXU compute 部分 |
| 覆盖 | 真实 LLM 权重多 tile | 可控参数扫描 (K/N/M sweep) |

两者互补: SoC 级打通端到端 pipeline; 模块级提供纯净的基层性能数据用于建模校准。

### 1.2 已知的 MXU RTL 周期公式 (基于 Controller FSM 推导)

Controller FSM:
```
IDLE → READ_DIMS → LOAD_W → LOAD_A → COMPUTE → STORE_OUT → (tile loop) → DONE
```

**常量 (64×64 array):**
- `READ_DIMS` = 1 cycle (仅在命令首个 tile 执行)
- `LOAD_W` = 1 cycle (weight_load_en 脉冲)
- `LOAD_A` = 1 cycle (activation_load_en 脉冲)
- `COMPUTE()` = `k_cur + 2` cycles (k_cur = 1..64; 包含 PE 流水线排空)
- `STORE_OUT()` = `m_cur + 1` cycles (m_cur = 1..64; 行地址 0..m_cur-1 + 过渡 cycle)

**全 tile (K=64, M=64):** COMPUTE=66, STORE_OUT=65

**全命令周期公式:**
```
Total = 1(READ_DIMS) + M_tiles × N_tiles × (K_tiles × (w+a+c) + s)
      = 1 + M_tiles × N_tiles × (K_tiles × 68 + (m_cur+1))
```

其中 `M_tiles=ceil(M/64)`, `N_tiles=ceil(N/64)`, `K_tiles=ceil(K/64)`,
`m_cur = min(64, M - m_tile×64)` (末 tile 可能 `< 64`)。

### 1.3 现有 MXU 功能覆盖 (全部 ✅ PASS)

| 功能域 | Cases | 状态 |
|--------|-------|------|
| buffer nibble/concurrent | MX-01..MX-03 | ✅ |
| accumulator saturation/conflict | MX-04..MX-05 | ✅ |
| mmio_if reserved/CMD/unaligned | MX-06..MX-08 | ✅ |
| controller ABORT/IRQ | MX-09..MX-11 | ✅ |
| mac_array pipeline K+2 | MX-12 | ✅ |
| mxu_top serialization/back2back/SRAM/status timing | MX-13..MX-16 | ✅ |

**性能缺口**: 以上所有 case 验证**功能正确性**, 不测量 cycle 计数。没有任何 case
测量 per-component 周期分解、tile 过渡开销、多 tile 缩放行为或吞吐量。

---

## 2. 验收标准

| 模块 | 指标 | 阈值 | 理由 |
|------|------|------|------|
| MXU total cycles per CMD | 测量值 vs Controller FSM 公式预测 | `abs(measured - expected) ≤ 1 cycle` | FSM 状态机是确定性的 — 任何偏差 = FSM bug |
| Per-component cycle breakdown | READ_DIMS/LOAD_W/LOAD_A/COMPUTE/STORE_OUT 各阶段 cycle | 各阶段与公式预测逐项一致(≤1 cycle) | 用于性能模型校准的精细数据 |
| MXUModel comparison | RTL total vs MXUModel.estimate() | delta ≤ 1000% (定性参考, 见 Section 10) | MXUModel 使用 128×128 配置，RTL 为 64×64 |
| Throughput stability (back-to-back) | 连续 10 次同配置操作的 cycle 标准差 | std ≤ 1 cycle (首个 warmup ±0) | 确定性硬件应有 0 cycle 偏差 |
| Per-tile cycle consistency | 同尺寸 tile 间 cycle | 所有 tile cycle 完全相同 (确定性 FSM) | FSM 无数据依赖性不定性 |

---

## 3. 优先级说明

- **P0**: 基础测量能力 — 单一 tile 的 cycle 分解和加载带宽 (不通则后续全阻塞)
- **P1**: 多 tile 特征 — K/N/M tile 过渡开销、首个 vs 后续 tile 差异
- **P2**: 缩放行为 — K/N/M 独立扫描、偏 tile 测量
- **P3**: 吞吐与建模 — 背靠背吞吐、Func Model 系统校准
- **P4**: 边界/全量 — 极端 tile 尺寸、存出时间占比、全量参数

---

## 4. 状态图例

- ⬜ TODO — 待执行
- 🔄 RUNNING — 执行中
- ✅ PASS — 通过
- ❌ FAIL — 失败（修复后重试，最多 3 次）
- ⏸️ SKIP — 已有覆盖/无需重复

---

## 5. 测量方法

所有 case 使用 **tb_mxu.v** 扩展版本 (`tb_mxu_perf.v`), 添加以下 cycle 计数器:

```
// 总操作 cycle: CMD.START → STATUS.DONE 上升沿
reg [31:0] total_cycles;

// 各 FSM state 驻留 cycle 计数器
reg [31:0] cnt_read_dims, cnt_load_w, cnt_load_a, cnt_compute, cnt_store_out;

// compute_en 和 store_out 的 cycle 计数
reg [31:0] cnt_compute_en, cnt_store_out_active;

// Per-tile cycle 记录 (tiles_completed 变化时采样)
reg [31:0] per_tile_cycles[0:65535];

// 背靠背操作: 记录每次 STATUS.DONE 的 cycle 间隔
reg [31:0] last_done_cycle, inter_op_gap;
```

测量脚本比较 `$monitor` / `$display` 输出, 通过 Python 后处理 (扩展 `compare_rtl.py` 或新增 `analyze_perf.py`) 自动判断 PASS/FAIL。

### 预期 cycle 速查表 (64×64 array)

| 配置 (M,N,K) | M_tiles | N_tiles | K_tiles | m_cur | 公式 | 预期 total |
|:-----------:|:-------:|:-------:|:-------:|:-----:|------|:--------:|
| 1,64,64 | 1 | 1 | 1 | 1 | 1+1×1×(1×68+2) | **71** |
| 4,64,64 | 1 | 1 | 1 | 4 | 1+1×1×(1×68+5) | **74** |
| 16,64,64 | 1 | 1 | 1 | 16 | 1+1×1×(1×68+17) | **86** |
| 32,64,64 | 1 | 1 | 1 | 32 | 1+1×1×(1×68+33) | **102** |
| 64,64,64 | 1 | 1 | 1 | 64 | 1+1×1×(1×68+65) | **134** |
| 64,64,128 | 1 | 2 | 1 | 64 | 1+2×1×(1×68+65) | **267** |
| 64,128,64 | 1 | 1 | 2 | 64 | 1+1×1×(2×68+65) | **202** |
| 64,64,80 | 1 | 1 | 2 | 64 | 1+1×1×(68+20+65) | **154** |
| 64,64,1 | 1 | 1 | 1 | 64 | 1+1×1×(1×3+65) | **69** |
| 1,1,1 | 1 | 1 | 1 | 1 | 1+1×1×(1×3+2) | **6** ← 但 k_cur=1 时 COMPUTE=3, m_cur=1 时 STORE_OUT=2 |
| 1,1,1 | — | — | — | — | 1+1+1+3+2 = **8** | **8** |

> 修正: K=1 → k_cur=1 → COMPUTE=3, M=1 → m_cur=1 → STORE_OUT=2
> 公式: 1(READ_DIMS) + 1(LOAD_W) + 1(LOAD_A) + 3(COMPUTE) + 2(STORE_OUT) = **8 cycles**

---

## P0: 基础测量 — 单 tile 周期分解

> 理由: 没有这些基础测量就无法解释多 tile 行为。所有 P0 case 在单一命令内完成, 不涉及 tile 间过渡。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| MX-P01 | P0 | `tb_mxu_perf.v` — single tile K=64,N=64,M=64 | **单全 tile baseline 周期分解**: 测量 total cycles 和各 FSM state 驻留 cycle (READ_DIMS/LOAD_W/LOAD_A/COMPUTE/STORE_OUT) | total_cycles = 134 (≤1 cycle err); per-state: RD=1, LW=1, LA=1, COMP=66, SO=65; cnt_compute_en=66, cnt_store_out_active=65 | ✅ | |
| MX-P02 | P0 | `tb_mxu_perf.v` — single tile K=64,N=64,M=1 | **M=1 最小存出开销**: 测量 M=1 时的 store_out drain cycle, 验证 STORE_OUT=m_cur+1=2 | total_cycles = 71; cnt_store_out_active = 2; cnt_compute_en = 66 | ✅ | |
| MX-P03 | P0 | `tb_mxu_perf.v` — single tile K=64,N=1,M=64 | **最小 N 维度**: N=1 下的单 tile 行为, 验证 N-tile=1 always 且公式 | total_cycles = 134 (与 MX-P01 一致, N 不改变 tile 大小); cnt_load_w=1, cnt_load_a=1 | ✅ | |

---

## P1: 多 Tile 特征 — 过渡开销

> 理由: tile 间过渡开销 (LOAD_W/LOAD_A bubble) 影响性能模型精度。每个过渡有 2 cycle 固定开销。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| MX-P04 | P1 | `tb_mxu_perf.v` — multi-K-tile K=128,N=64,M=64 | **K-tile 过渡开销**: 2 K-tiles; tile 0→tile 1 过渡不经过 STORE_OUT, 验证 K-accum bubble | total_cycles = 202; per-tile breakdown: tile0=69(1+68), tile1=68(1+1+66), store=65; K-tile 过渡 gap = 2 cycles (LOAD_W+LOAD_A) | ✅ | |
| MX-P05 | P1 | `tb_mxu_perf.v` — multi-N-tile K=64,N=128,M=64 | **N-tile 过渡开销**: 2 N-tiles; STORE_OUT→LOAD_W→LOAD_A→COMPUTE 过渡 | total_cycles = 267; tile0=134, tile1=133; N-tile 过渡 gap = 2 cycles (STORE_OUT 末 cycle 同步切换 + LOAD_W+LOAD_A) | ✅ | |
| MX-P06 | P1 | `tb_mxu_perf.v` — multi-M-tile K=64,N=64,M=128 | **M-tile 过渡开销**: 2 M-tiles; 过渡包括 accumulator reset (mac_reset_acc=1) | total_cycles = 267; tile0=134, tile1=133; M-tile 过渡额外包括 reset_acc (应不影响 cycle 计数, 与 LOAD_W 同 cycle) | ✅ | |
| MX-P07 | P1 | `tb_mxu_perf.v` — K=128,N=128,M=64 全 4-tile | **复合过渡**: 4 tiles (K=2,N=2), 同时包含 K-tile 积累和 N-tile 过渡 | total_cycles = 403 [1+2×(2×68+65)=1+2×201=403]; per-tile cycle 记录 4 个值: 69, 68, 201, 200; tile0 > tile2 因 READ_DIMS | ✅ | |

---

## P2: 缩放行为 — 独立参数扫描

> 理由: 性能模型需要对 K/N/M 参数的独立缩放敏感度。P2 case 通过单变量扫描验证 FSM 周期公式的线性度。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| MX-P08 | P2 | `tb_mxu_perf.v` — K 扫描 [64, 128, 256, 512, 1024] | **K 维度独立缩放**: M=64,N=64; 测量 K_tiles 线性增长. K=1024→K_tiles=16 | K=64:134, K=128:202, K=256:338, K=512:610, K=1024:1154 [1+1×1×(16×68+65)=1+1153=1154]; 每 K-tile slope = 68 ±0 cycles | ✅ | |
| MX-P09 | P2 | `tb_mxu_perf.v` — N 扫描 [64, 128, 256, 512] | **N 维度独立缩放**: M=64,K=64; 测量 N_tiles 线性增长 | N=64:134, N=128:267, N=256:533, N=512:1065 [1+8×1×133=1+1064=1065]; 每 N-tile slope = 133 ±0 cycles | ✅ | |
| MX-P10 | P2 | `tb_mxu_perf.v` — 偏 K-tile K=80,N=64,M=64 | **末 K-tile 非全尺寸**: K=80→2 K-tiles (64+16). 首次验证 partial K-tile 的 COMPUTE 周期 | total_cycles = 154 [1+1×1×(68+20+65)=1+153=154]; tile1 COMPUTE=18 cycles (k_cur=16 → 16+2=18); 无 STORE_OUT 在 tile0 后 | ✅ | |
| MX-P11 | P2 | `tb_mxu_perf.v` — 偏 N-tile K=64,N=33,M=64 | **末 N-tile 非全尺寸**: N=33→1 N-tile (m_cur=64, n_cur=33). 验证 N 维偏 tile 不影响 cycle 计数 | total_cycles = 134; N=33 仍在 1 tile 内 (ceil(33/64)=1), 与 K=64,N=64 一致. 功能正确性由 MX-15 验证, 本 case 仅确认 cycle 不变 | ✅ | |

---

## P3: 吞吐与模型校准

> 理由: 单次操作周期分解后, 需要背靠背吞吐数据和 Func Model 交叉校准。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| MX-P12 | P3 | `tb_mxu_perf.v` — M 扫描 [1, 4, 16, 32, 64] | **M 维度独立缩放**: K=64,N=64; 测量 STORE_OUT = m_cur+1 的线性度 | M=1:71, M=4:74, M=16:86, M=32:102, M=64:134; total 随 M 增加斜率 = 1 cycle/tile (仅 STORE_OUT 延长) | ⬜ | |
| MX-P13 | P3 | `tb_mxu_perf.v` — 背靠背 10× single-tile | **Back-to-back 吞吐**: 连续 10 次 K=64,N=64,M=64, 无复位, 测 inter-op gap | 10 ops total = 10×134 = 1340 ±0 cycles; inter-op gap = 0 (DONE 后回到 IDLE, cmd_start 再次触发 READ_DIMS); 10 次 total_cycles 完全一致 (std=0) | ⬜ | |
| MX-P14 | P3 | `tb_mxu_perf.v` — 背靠背 10× multi-tile (K=256,N=64,M=64) | **Multi-tile 背靠背**: 连续 10 次 K=256,N=64,M=64 操作; 每次 4 K-tiles | 10 ops total = 10×338 = 3380 ±0 cycles; per-op per-tile 4 值各不偏差. 验证 store_out 后状态机完全复位 | ⬜ | |
| MX-P15 | P3 | Python script: 收集所有 P0-P3 实测 cycles, 调用 MXUModel.estimate() 生成对比表 | **Func Model 交叉校准**: 对 P0-P3 每个 (M,N,K) 配置, 记录 RTL cycles 和 MXUModel cycles, 标注 delta 和根因 | 生成 15+ 行对比表, 每行包含 M,N,K,RTL_cyc,Model_cyc,Delta,分析. MXUModel 使用 `MXUModel(config)` 其中 config 调整为 64×64 array | ⬜ | |

---

## P4: 边界/全量

> 理由: 覆盖极端 tile 尺寸和吞吐上限。

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| MX-P16 | P4 | `tb_mxu_perf.v` — 最小 tile K=1,N=1,M=1 | **最小 tile**: k_cur=1, m_cur=1. COMPUTE=3, STORE_OUT=2 | total_cycles = 8 (1+1+1+3+2); compute_en 仅 3 cycles | ⬜ | |
| MX-P17 | P4 | `tb_mxu_perf.v` — 最大 per-CMD K=64,N=64,M=64 (cube) | **Cube 满配置**: 4096 个 PE 在 64×64×64 上全利用. 验证 store_out=65 cycles 与 compute=66 cycles 的比例 | total = 134; compute = 49.3% (66/134), store-out = 48.5% (65/134), overhead = 2.2% (3/134). 注: 大 M 时 store-out 占比 > compute | ⬜ | |
| MX-P18 | P4 | `tb_mxu_perf.v` — M 扫描 [1, 2, 4, 8, 16, 32, 64] 的 store-out 占比分析 | **Store-out drain 占比分析**: 测量 store_out_active / total_cycles 比率随 M 变化 | M=1: 2/71=2.8%; M=4: 5/74=6.7%; M=16: 17/86=19.8%; M=32: 33/102=32.4%; M=64: 65/134=48.5%. 输出折线趋势, 确认 STORE_OUT 行数正比于 M | ⬜ | |

---

## 6. Agent 执行规则

1. **严格按 P0→P4 顺序执行**, 不跳级
2. 先读 RTL 源码 (controller FSM / mac_array pipeline), 理解 cycle 边界
3. 每个 case:
   - 在 `rtl/tb/tb_mxu.v` 基础上扩展 cycle 计数器或创建 `tb_mxu_perf.v`
   - 根据 case 参数调用 `gen_mxu_vectors.py --shape M,N,K` 生成测试向量
   - 测量 cycle 计数: `$display` 输出 cycle 戳, `$monitor` 记录 state 变化
   - Python 后处理脚本解析 $display 输出, 验证公式预期
   - 更新状态、结果列
4. **每次状态变更后立即 git commit + git push** (zartbot 模式)
5. 不满足验收标准 → ❌ FAIL → 分析根因 → 修复 (RTL 或 testbench) → 重试 (最多 3 次)
6. 3 次仍 FAIL → 保持 ❌ 等待人类介入
7. 可自主新增 case (Agent 发现未列出的性能特征)

### 预期 cycle 推导规则 (Agent 验证用)

MXU Controller FSM 是**完全确定性的** — 给定 (M,N,K), cycles 是唯一定值。
Agent 在验证每个 case 时应:

1. 计算 `M_tiles=ceil(M/64)`, `N_tiles=ceil(N/64)`, `K_tiles=ceil(K/64)`
2. 对 tile(t_m, t_n, t_k) 计算:
   - `k_cur = min(64, K - t_k×64)` — 若 K_tiles=1 则 k_cur=K
   - `m_cur = min(64, M - t_m×64)`
3. 首 tile 加 1 (READ_DIMS), 后续不加
4. 每个 K-tile (含首): LOAD_W(1) + LOAD_A(1) + COMPUTE(k_cur+2)
5. 末 K-tile of group 后: STORE_OUT(m_cur+1)
6. 求和

### VCS 环境

```bash
ssh zhengs@192.168.0.11
source /NAS/Tools/methodology/modules/init/bash
module load vcs/vcs_vW-2024.09-SP2_P
```

### 测试向量生成

```bash
python3 CaduceusCore/scripts/gen_mxu_vectors.py --scenario single_tile \
    --out-dir CaduceusCore/rtl/test_vectors/mxu/perf --shape 64,64,64
```

(若 `gen_mxu_vectors.py` 不支持 `--shape`, 使用 `--scenario perf_M_N_K` 模式或手写 hex)

---

## 7. Git 规则 (zartbot 模式)

```
每次 testcase-list-mxu-perf.md 状态变化 = 一次 git commit

commit 格式:
  [MX-PXX] ⬜ → NEW_STATUS | 具体结果描述

示例:
  [MX-P01] ⬜ → ✅ | total=134, per-state RD=1 LW=1 LA=1 COMP=66 SO=65
  [MX-P04] ⬜ → ✅ | total=202, K-tile tile0=69 tile1=68, gap=2 cycles
  [MX-P16] ⬜ → ✅ | K=1,N=1,M=1 total=8, compute_en=3, store_out=2

原则:
  - 每完成一个 case (无论 PASS/FAIL) 立即 commit + push
  - 不允许批量攒多个 case 再 commit
  - git log testcase-list-mxu-perf.md = 完整性能测量时间线
```

---

## 8. 首次启动检查清单

```bash
# 1. 确认 git 状态干净
cd CaduceusCore && git status

# 2. 确认 VCS 可用 (EDA server)
ssh zhengs@192.168.0.11 'source /NAS/Tools/methodology/modules/init/bash && module load vcs/vcs_vW-2024.09-SP2_P && which vcs'

# 3. 确认 Func Model 可导入
PYTHONPATH=sim python3 -c "from models.mxu import MXUModel; print('MXUModel OK')"

# 4. 确认现有 MXU 功能测试全部通过 (基线)
# (Module-level VCS: 9 named scenarios + 100 random = 109/109)
```

---

## 9. 统计

```
总计:     18 cases (新增)
P0:        3 cases (MX-P01..MX-P03)
P1:        4 cases (MX-P04..MX-P07)
P2:        4 cases (MX-P08..MX-P11)
P3:        4 cases (MX-P12..MX-P15)
P4:        3 cases (MX-P16..MX-P18)
─────────────────────────────────────
覆盖率:    0% → 目标 100% (所有 case 测量通过)
```

---

## 10. MXUModel 配置说明 (重要)

`sim/models/mxu.py` 中的 `MXUModel` 使用以下配置:

```python
# 来自 config["mxu"] (默认)
H = 128    # array_height
W = 128    # array_width
f_mhz = 1000
w_bits = 4
a_bits = 8
```

**RTL 是 64×64 array**, 而 MXUModel 配置为 128×128。
因此直接 cycle 比较会有 ~4× 偏差 (面积 4×, 每 tile MAC 量 4×)。

**建议两种方式使用 MXUModel 作为参考:**

1. **修改 config**: 将 `MXUModel` 实例化时传入临时 config `{"mxu": {"array_height": 64, "array_width": 64, ...}}` 进行同尺寸比较
2. **接受偏差**: 保持 128×128 配置, 将 RTL 结果按面积比例缩放后对比 (定性)

本 testplan 所有预期 cycle 基于 **RTL Controller FSM 公式**, **不是** MXUModel 预测值。
MXUModel 仅用于: (a) 确认物理合理范围 (b) 校准 Func Model 的 cycle 公式 (c) 发现建模假设偏差。

### MXUModel vs RTL 预期对照 (示意, 128×128 config)

| 配置 | RTL (64×64) | MXUModel (128×128) | delta | 根因 |
|------|:----------:|:-----------------:|:-----:|------|
| 64,64,64 | 134 | ~697 | ~420% | Model array 面积 4× |
| 1,64,64 | 71 | ~596 | ~739% | 面积差 + decode 模式 extra fill |
| 64,64,128 | 267 | ~939 | ~252% | 面积 + DMA model overhead |

修改 MXUModel config 后这些 delta 应大幅缩小。

---

## 11. 与 SoC 级 testcase-list-perf.md 的关系

| 本文件 MX-P* | SoC 级 PERF-* | 关系 |
|:----------:|:------------:|------|
| MX-P01..P03 单 tile baseline | — | 模块级新数据, SoC 级没有对应 |
| MX-P04/P05/P06 tile 过渡 | PERF-05/06 | MX-P 提供纯 FSM 开销; PERF 包含 wrapper/DMA |
| MX-P08 K 扫描 | PERF-09/10 (weight streaming) | MX-P 在 single tile 内测 K-tile accum; PERF 测真实 stream reload |
| MX-P13 背靠背吞吐 | PERF-20 (repeatability) | MX-P 仅 MXU 级; PERF 含全系统 |
| MX-P15 Func Model 对比 | PERF-07/08 | MX-P 聚焦 64×64 vs Model 64×64; PERF 用 stock 128×128 |
| — | PERF-01..04 基础设施 | SoC 特有, 模块级不需要 |
| — | PERF-11/12 Q_proj 全量 | 模块级没有真实 LLM 权重 (使用合成数据) |

**互斥说明**: 在 MX-P case 测量 cycle 时, 不需要同时运行 PERF case。
两个文件对应不同测试层级和测量目的。模块级数据优先用于校准 FSM cycle 公式,
SoC 级数据用于校准 DMA/NoC overlap。
