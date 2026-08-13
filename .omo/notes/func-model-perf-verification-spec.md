# Func Model 性能验证规格 — Bootstrap (Block 64×64)

**日期**: 2026-08-12 | **版本**: v1.0  
**对应配置**: Block 64×64 @ 1GHz, LPDDR5-6400 (43.5 GB/s eff), INT4/INT8, Qwen2.5-3B  
**对应 DSE**: Arc DSE v1 (Block 引擎选型)

---

## 1. 开发流程与闭环

```
应用场景 → Arc DSE ──→ 性能目标 ──→ Func Model 实现
              ↑           │              │
              │           │         [功能验证]
              │           │         [性能验证] ←── 本规格定义
              │           │              │
              │   校正 ←──┘              │
              │         (差距分析)        │
              │                          ▼
              └────────────── Func Model 验证结果
                                         │
                                    RTL 实现
                                         │
                                    RTL 验证结果
                                         │
                              校正 ────→ Func Model
                             (calibration_state: uncalibrated → rtl_calibrated)
```

**关键关系**:

| 闭环 | 方向 | 作用 |
|------|------|------|
| **DSE ↔ Func Model** | Func Model 结果 → 校正 DSE | 发现 DSE 建模过简（如 tile overhead 被忽略）→ 补 DSE 模型 |
| **Func Model ↔ RTL** | RTL 实测 cycle → 校正 Func Model | 从 architecture_assumption → rtl_calibrated |

---

## 2. 三项验证门禁

### Gate 1a: DSE 一致性 (TPS)

> **目的**: 验证 Func Model decode TPS 估算与 Arc DSE 目标收敛，发现建模级偏差。

**目标值**: Arc DSE v1 对 Block 64×64 的估算为 **TPS ≈ 25.1**（`simulate_layer` → `tok_s_from_layer`，36 层，serial GEMM sum）。

**判定规则**:

| 等级 | 条件 | 含义 |
|------|------|------|
| **PASS** | 0.5 × DSE ≤ Func Model TPS ≤ 2.0 × DSE | Func Model 与 DSE 量级一致，无系统性偏离 |
| **WARN** | Func Model TPS 在 PASS 范围外但 ≤ BW ceiling | 存在建模差异，需分析原因并记录 |
| **FAIL** | Func Model TPS > BW ceiling 或 < 0 | 物理不可能，模型错误 |

**当前实测**:

| Func Model 路径 | TPS | vs DSE 25.1 | 判定 |
|------|:---:|:---:|:---:|
| canonical formula (`_mxu_decode_cycles`) | **30.75** | **1.22×** | **PASS** — canonical 公式已对齐 BlockEngine broadcast 模型 |
| full simulation (TimingEngine) | ~29.6 | 1.18× | PASS |

**校正动作**: canonical 公式从 systolic `H*(M+1)+W` 切换为 BlockEngine broadcast `M*(H+4)`（per-token-per-tile compute = 64 + 2 + 2 = 68），消除了 2.8× 人工保守度，canonical/full-sim 差距闭合。

---

### Gate 1b: DSE 一致性 (TTFT)

> **目的**: 验证 Func Model prefill TTFT 估算与 Arc DSE TTFT 目标收敛（Block 64×64 @ 1GHz, LPDDR5-64b, WC）。TTFT 定义：`prefill_layer_cycles × num_layers / freq_mhz` [ms]，不含首 token decode（与 uncertainty-kpis 的 `prefill_ms` 对齐）。

**目标值**: Arc DSE 对 Block 64×64 @ 1GHz LPDDR5-64b (WC) 的 TTFT（`simulate_prefill` → `ttft_ms_from_prefill`，trace 按 `batch_m` 生成）：

| Prefill 规模 (M) | DSE TTFT 目标 (ms) | 证据来源 |
|------|:---:|------|
| M=128 | **2,649.49** | `.omo/evidence/task-3-dse-ttft-m128.json` (bloc 64×64 INT4 1000MHz WC LPDDR5-64b) |
| M=2000 | **41,398.27** | `.omo/evidence/task-3-dse-ttft-m2000.json` (bloc 64×64 INT4 1000MHz WC LPDDR5-64b) |

**判定规则**:

| 等级 | 条件 | 含义 |
|------|------|------|
| **PASS** | 0.5 × DSE_TTFT ≤ Func Model TTFT ≤ 2.0 × DSE_TTFT | Func Model 与 DSE TTFT 量级一致，无系统性偏离 |
| **FAIL** | Func Model TTFT < 0.5 × DSE_TTFT 或 > 2.0 × DSE_TTFT | prefill 建模存在系统性偏差，需分析原因并校正 |

**当前实测**:

| Prefill 规模 (M) | DSE TTFT 目标 (ms) | Func Model TTFT (ms) | 比值 | 判定 |
|------|:---:|:---:|:---:|:---:|
| M=128 | 2,649.49 | **3,911.05** | **1.48×** | **PASS** — task-16 `qwen25-3b-prefill-128` Path A total × 36 / 1GHz / 1000 |
| M=2000 | 41,398.27 | **63,924.19** | **1.54×** | **PASS** — task-20 `ttft_ms` base |

**校正动作**: DSE TTFT 模型修复（`simulate_layer` 按 `batch_m` 重新生成 trace，新增 `simulate_prefill`/`ttft_ms_from_prefill`，CLI `--batch-m` 放宽）后，DSE TTFT 成为可用验证目标；Func Model/DSE 比值 1.48×–1.54×，落在 [0.5×, 2.0×] PASS 区间内，差距来源于 trace 结构（7-op layer vs 17-op DAG）与层内并行假设。

---

### Gate 2: 物理天花板

> **目的**: 确保 Func Model 的估算不违反物理定律。任何超过天花板的值 = 模型 bug。

**Compute Ceiling**:

```
peak_macs_per_cycle = 64 × 64 × 2 = 8,192
Peak MAC/s = 8.192 T MAC/s
Qwen2.5-3B MACs per token (decode) ≈ 5.5 × 10^9
TPS_compute_ceiling = 8.192e12 / 5.5e9 ≈ 1,490 tok/s
```

**Memory Ceiling**:

```
weight_bytes (3B INT4) ≈ 1.39 × 10^9
BW_effective = 43.52 bytes/cycle
TPS_memory_ceiling = 1e9 / (1.39e9 / 43.52) ≈ 31.3 tok/s
```

**判定规则**:

| 门禁 | 条件 | 动作 |
|------|------|------|
| TPS > compute ceiling | impossible → **FAIL** | compute 模型错误 |
| TPS > memory ceiling | impossible → **FAIL** | DMA/BW 模型错误 |
| TTFT < compute minimum | impossible → **FAIL** | prefill 模型错误 |
| TPS < 0.05 × memory ceiling | **WARN** | 异常低效，检查 overhead 假设 |

**当前实测**:

| 指标 | Func Model canonical | Ceiling | 状态 |
|------|:---:|:---:|:---:|
| TPS | **30.75** | ≤ 31.3 | ✅ 在天花板内（接近 memory ceiling） |
| TTFT (M=128) | 3.86s | ≥ 0.088s | ✅ 在最小之上 |

---

### Gate 3: 带宽敏感性

> **目的**: 验证瓶颈分类正确——BW 应该是 decode 的主要约束，prefill 的主要约束应是 compute。

**判定规则**:

| 检查项 | 条件 | 动作 |
|------|------|------|
| **decode BW 敏感性** | BW ×1.3 → TPS 应上升，BW ×0.7 → TPS 应下降 | BW 变化对 TPS 有方向正确的单调影响 |
| **prefill BW 不敏感性** | BW ×2 → TTFT 变化 < 10% | 确认 prefill 是 compute-bound（非 BW-bound） |
| **BW 天花板约束** | 所有 BW 变化下的 TPS ≤ 新 BW 天花板 | BW 升高时 TPS 不应超过新的天花板 |
| **endpoint 检测** | BW=6.4GB/s → dram_bw_share ≥ 55% (memory-bound) | 确认低 BW 端点行为正确 |
| | array=32 → mxu_utilization ≥ 55% (compute-bound) | 确认小阵列端点行为正确 |

**当前状态**（来自 T18 sweep）:
- decode BW 敏感性: ✅ 单调
- prefill endpoint: compute-bound（canonical 公式已对齐 BlockEngine broadcast，per_tile_compute = M*68 主导）✅
- memory-bound endpoint (BW=6.4): dram_bw_share=87% ≥ 55% ✅
- compute-bound endpoint (array=32): mxu_utilization=94% ≥ 55% ✅

---

## 3. 集成到 signoff 流程

在 `scripts/run_func_model_perf_signoff.py` 中新增 `--check-dse-consistency` 和 `--check-physical-ceiling` 命令：

```bash
# DSE 一致性检查
python3 scripts/run_func_model_perf_signoff.py run --check-dse-consistency \
    --dse-target-tps 25.1 --func-model-tps $(...)

# 物理天花板检查
python3 scripts/run_func_model_perf_signoff.py run --check-physical-ceiling \
    --compute-ceiling 1490 --memory-ceiling 31.3

# 全量签收（含三项新门禁）
python3 scripts/run_func_model_perf_signoff.py run --all-spec \
    --check-dse-consistency --check-physical-ceiling --check-bw-sensitivity
```

**判定汇总**:

| Gate | 当前状态 | 备注 |
|------|:---:|------|
| DSE 一致性 (Gate 1a, TPS) | **PASS** | canonical TPS 30.75，落在 [0.5×, 2.0×] DSE 25.1 范围内 |
| DSE 一致性 (Gate 1b, TTFT) | **PASS** | M=128: 3,911.05 / 2,649.49 = 1.48×；M=2000: 63,924.19 / 41,398.27 = 1.54×，均落在 [0.5×, 2.0×] 范围内 |
| 物理天花板 | PASS | 无 impossible 值 |
| BW 敏感性 | PASS | 单调性和端点检测通过 |

---

## 4. S1 配置迁移后的目标

当 Func Model 从 Block 64×64 迁移到 FSA 128×256 (S1) 时，DSE 目标更新为：

| 指标 | Block 64×64 DSE 目标 | FSA 128×256 DSE 目标 | 来源 |
|------|:---:|:---:|------|
| TPS | 25.1 | 23.0 | arch-report-A / Arc DSE three-scenario |
| TTFT (M=128) | (未建模) | 88ms | arch-report-A |
| TOPS | 8.2 | 33 | hardware config |
| BW ceiling | 31.3 | 31.3 | 相同 LPDDR5 |
| compute ceiling | 1,490 | ~5,960 | 4× MACs |

S1 阶段 Gate 1 的 PASS 区间: TPS ∈ [11.5, 46.0]（0.5-2× DSE 目标 23.0）

---

## 5. 校正闭环记录

| 日期 | 闭环 | 发现 | 动作 |
|------|------|------|------|
| 2026-08-12 | DSE → Func Model | canonical TPS=30.75 vs DSE=25.1, 比值 1.22×（修复前 10.99, gap=2.3×） | 修复 BUG-PERF-MXU-001 (per-tile compute 死代码); canonical 公式切换为 BlockEngine broadcast `M*(H+4)` |
| 2026-08-12 | DSE → Func Model | DSE TTFT 未真正建模 prefill (trace 固定 M=1) | 标记 DSE TTFT 不可用作验证目标; 需 Func Model 自行建模 |
| 2026-08-12 | Func Model → DSE | prefill 实际 compute-bound, DSE 未区分 decode/prefill 瓶颈 | 建议 DSE 增加 prefill batch-m 模式和 compute vs DMA bottleneck 输出 |
| 2026-08-13 | DSE → Func Model | **DSE TTFT model fixed**: `simulate_layer` 按 `batch_m` 重新生成 trace, 新增 `simulate_prefill`/`ttft_ms_from_prefill`, CLI `--batch-m` 放宽至任意正整数; Block 64×64 @ 1GHz LPDDR5-64b (WC) TTFT 目标可用 — M=128: 2,649.49 ms, M=2000: 41,398.27 ms（证据: task-3-dse-ttft-m128/m2000.json） | 建立 Gate 1b 并录入规格; Func Model TTFT 实测 3,911.05 / 63,924.19 ms, 比值 1.48× / 1.54×, PASS |
