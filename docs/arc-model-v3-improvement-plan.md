# Arc Model v3 改进计划

> 本文按当前项目推进口径定义 Arc Model v3：在 v2 引入 FSA 之后，把 DSE 从“可探索的分析脚本”升级为“产品需求驱动、可复现、可交接给 Func Model 的架构候选生成器”。

## 目标

Arc Model v3 不直接生成 RTL spec。它的目标是：

1. 从产品需求和场景约束出发，生成可复现的架构候选。
2. 对候选做硬约束过滤、PPA 排名、敏感性分析和交叉校验。
3. 输出 Architecture Contract 草案，交给 Func Model v2+ 做 executable spec / golden reference。

v3 完成后，README 中的三场景 DSE 结论应该能由一组命令复现，而不是只存在于手写报告里。

## 当前问题

### P0: 复现性和版本基线

- 当前 `design_space_explorer.py` 没有 `--scenario`，主流程不会从 `sim/config/scenarios.yaml` 驱动搜索空间、约束和内存组件。
- `scenarios.yaml` 已有 `lpddr5_3b` / `onchip_7b` / `onchip_7b_chat`，但 DSE 入口仍用固定 sweep。
- 报告推荐了 `block 80x1536`，当前 sweep 维度没有覆盖这类宽阵列。
- README/报告存在 Arc 版本命名不一致风险。v3 应统一文档版本：Arc v1=Block 路线，Arc v2=引入 FSA，Arc v3=场景驱动和 contract 化。
- Arc 相关 pytest 当前不绿：`test_qkv_dimension_3b` 仍按旧 Qwen2.5-3B 参数断言。

### P0: 搜索目标不是产品约束驱动

- Pareto 只按 `max tok/s / min area`，没有把功耗、TTFT、内存形态、精度门禁和产品线约束作为硬过滤。
- 输出的 `PPA` 只有 `tok_s / area / power / label`，缺少 `pass/fail reasons`、TTFT、带宽利用率、TOPS、memory feasibility。
- 结果结构依赖 `config_label` 字符串，敏感性分析再反解析 label，容易遗漏 SRAM、on-chip memory、scenario 等关键维度。

### P0: 性能模型关键路径缺口

- 频率 sweep 存在实质错误：配置扫 800/1000/1200MHz，但 `tok_s_from_layer()` 写死 1000MHz。
- Decode trace 只覆盖 Q/K/V/O 和 FFN GEMM，prefill attention 的 `QK^T`、softmax、`PV` 未进入主 DSE trace。
- FSA 的 `estimate_attention()` 已存在，但主 `simulate_layer()` 没有调用，导致 FSA 的核心优势没有通过统一路径建模。
- `batch_m > 1` 时 KV cache cycles 直接返回 0，不足以支撑 TTFT/prefill 结论。

### P1: 内存和 PPA 模型需要产品化

- on-chip 3D DRAM 目前主要通过跳过 DRAM PHY 和加 TSV overhead 表示，缺少容量、堆叠面积、封装、带宽-面积、功耗模型。
- power model 对 on-chip memory 仍按 `memory.bandwidth_gbps / 51.2` 加 DRAM power，和 on-chip 场景不匹配。
- SRAM 模型已有 v2 修正，但 DSE 输出没有结构化暴露 wbuf/kvbuf 命中率、带宽利用率、ROI 拐点。

### P1: 精度门禁和架构 DSE 割裂

- `arc_model.py` 的 GGUF precision gate 和 `design_space_explorer.py` 的 PPA sweep 是两条路径。
- INT2 被纳入 full sweep，但没有和真实模型精度 gate 绑定；除非跨模型精度通过，否则 INT2 不应作为产品候选。
- 模型规格应统一来自 `model_specs.py`，并由测试锁定。旧测试和旧报告参数需要清理。

### P2: 工程质量和可观测性

- 主 sweep 中 `except Exception: pass` 会静默丢掉配置，导致 DSE 结果不可审计。
- “Best per engine type” 文案写了 DRAM 约束，但过滤条件没有按 DRAM 约束执行。
- cross-validation 用固定 `tops_int8`，没有从实际 H/W/frequency/ops_per_mac 推导。

## v3 设计方案

### 1. Scenario-driven DSE

新增 CLI：

```bash
PYTHONPATH=sim python3 sim/design_space_explorer.py --scenario lpddr5_3b --output results/dse/lpddr5_3b.json
PYTHONPATH=sim python3 sim/design_space_explorer.py --scenario onchip_7b --output results/dse/onchip_7b.json
PYTHONPATH=sim python3 sim/design_space_explorer.py --scenario onchip_7b_chat --output results/dse/onchip_7b_chat.json
```

要求：

- `--scenario` 加载 `scenarios.yaml`。
- Phase -1 默认只允许 explicit/inferred，高影响 default 必须在输出中标红。
- Phase 0 结果决定 search space：LPDDR5 走窄带宽/低面积；on-chip 走宽阵列和 BW-area coupling。
- 所有输出带 `scenario_name`、`scenario_hash`、`config_hash`，保证可复现。

### 2. Structured DSEPoint

用结构化结果替代 label 反解析：

```text
DSEPoint
  config: engine, H, W, freq, precision, SRAM, memory
  metrics: tok_s, ttft_ms, prefill_ms, area_mm2, power_w, tops_int8, bw_util_pct
  constraints: passed, failed_reasons[]
  breakdown: compute_cycles, dma_cycles, kv_cycles, sfu_cycles, attention_cycles
  provenance: scenario, model_spec, arc_version, source_config
```

`config_label` 只作为展示字段，不再作为计算输入。

### 3. Product-constrained Pareto

排序前先做硬过滤：

- precision passed
- `tok_s >= tps_min`
- `ttft_ms <= ttft_ms_max`
- `area_mm2 <= area_mm2_max`
- `power_w <= power_w_max`，如果 scenario 未显式提供则输出 warning
- memory type 和组件清单匹配
- package/form-factor 约束匹配

通过过滤后，再按场景优化：

- S1: 优先最小面积、最低功耗、满足 TPS/TTFT。
- S2: 优先满足 7B + 100 TPS + TTFT，再最小面积。
- S3: 优先 pipeline FPS / action latency，再复用 S2 die。

### 4. Prefill / TTFT first-class model

主 trace 拆成两个模式：

- decode trace: single-token steady state TPS。
- prefill trace: prompt length / seq_len 驱动 TTFT。

prefill 至少包括：

- Q/K/V/O projection
- QK^T
- softmax
- PV
- FFN gate/up/down
- RMSNorm/RoPE/SiLU 等 SFU/Vector 开销
- DMA/NoC/SRAM overlap

FSA 必须通过统一接口接入：

- 非 FSA 引擎：attention = QK^T + SFU softmax + PV。
- FSA 引擎：attention = `FSAEngine.estimate_attention()`。

### 5. 3D DRAM / memory stack model

v3 需要把 memory 从普通 dict 升级为显式模型：

```text
ExternalLPDDR5
  width_bits, gbps, efficiency, phy_area, phy_power

OnChip3DMemory
  capacity_gb, bw_gbps, bw_per_mm2, stack_area_mm2, tsv_overhead, stack_power

HBM
  stack_count, bus_width, phy_area, interposer/tsv cost
```

on-chip 场景的 `BW = area x 7.5 GB/s/mm2` 必须在模型里结构化计算，并进入 area/power/cost。

### 6. Precision gate integration

v3 的候选精度策略：

- INT4 per-block 是 baseline。
- INT2 只有在指定模型集 precision gate 通过后才进入候选。
- 每个 DSE output 记录 precision provenance：模型、层数、cos_min、mse、max_abs_error。
- CV 模型不能只用 cos_sim，需要 accuracy/mAP/top-1 或任务级 proxy。

### 7. Calibration loop

Arc v3 输出需要和 Func Model / RTL 建立校准字段：

- Arc predicted TTFT/TPS。
- Func measured TTFT/TPS。
- RTL measured cycles/trace。
- 差异比例和原因分类：model gap / implementation gap / contract gap。

Func Model 还未实现 FSA 前，Arc v3 的 FSA 结果只能标记为 `architecture_candidate`，不能标记为 `func_verified`。

## 实施优先级

### v3.0 Baseline Fix

目标：让现有 Arc 结果可信、测试基线干净。

1. 统一 Qwen2.5-3B 模型规格和测试。
2. 修正 frequency sweep 写死 1000MHz。
3. 移除 silent exception，输出 invalid config 列表。
4. 输出 structured JSON，先不重构所有模块。

验收：

```bash
PYTHONPATH=sim python3 -m pytest sim/tests/test_arc_model.py sim/tests/test_arc_precision.py -q
PYTHONPATH=sim python3 sim/design_space_explorer.py --quick --output results/dse/quick.json
```

### v3.1 Scenario-driven Search

目标：一条命令复现每个产品场景。

1. 加 `--scenario`。
2. 根据 `scenarios.yaml` 生成搜索空间。
3. 把 scenario constraints 变成 hard filter。
4. 增加 `pass/fail reasons`。

验收：

```bash
PYTHONPATH=sim python3 sim/design_space_explorer.py --scenario lpddr5_3b --output results/dse/lpddr5_3b.json
PYTHONPATH=sim python3 sim/design_space_explorer.py --scenario onchip_7b --output results/dse/onchip_7b.json
```

### v3.2 TTFT / FSA Model Closure

目标：FSA 和 block 在同一 prefill/decode 模型下公平比较。

1. 建 prefill trace。
2. 接入 `FSAEngine.estimate_attention()`。
3. 输出 TTFT breakdown。
4. 对比 Func Model `docs/func_model_performance_analysis.md` 里的 Block 64x64 基线。

验收：Arc 对 Block 64x64 的 TTFT/TPS 与 Func Model 误差在预设范围内，并解释差异来源。

### v3.3 Memory / PPA Calibration

目标：on-chip 3D DRAM 方案可用于产品讨论。

1. 显式 memory stack model。
2. on-chip area/power/cost 拆分。
3. cross-validation 从实际 config 推导 TOPS。
4. 增加产品数据库和来源字段。

验收：S2/S3 的 `80x1536` 候选能由 scenario sweep 生成，且 area/BW/power 组成可解释。

### v3.4 Report and Contract Generation

目标：Arc v3 直接产出 Func Model 可接收的 Architecture Contract 草案。

输出：

- `results/dse/<scenario>.json`
- `reports/arc-v3-<scenario>.md`
- `contracts/architecture/<scenario>.yaml`

Contract 必须标注：

- `status: architecture_candidate`
- `func_verified: false`，除非已有 Func Model v2 对应实现和验证证据。

## 建议的文件拆分

`sim/design_space_explorer.py` 已经承担了搜索空间、仿真、敏感性、CLI、报告、交叉校验等多个职责。v3 建议拆分：

| 文件 | 职责 |
|------|------|
| `sim/dse/types.py` | `DSEConfig`, `DSEPoint`, `ConstraintResult` |
| `sim/dse/search_space.py` | 从 scenario 生成 config |
| `sim/dse/workload.py` | decode/prefill/CV trace |
| `sim/dse/evaluator.py` | config -> DSEPoint |
| `sim/dse/constraints.py` | 产品约束过滤 |
| `sim/dse/pareto.py` | 多目标 Pareto |
| `sim/dse/report.py` | JSON/Markdown/contract 输出 |
| `sim/design_space_explorer.py` | 仅保留 CLI wrapper |

## 当前验证证据

本次复核运行：

```bash
PYTHONPATH=sim python3 sim/design_space_explorer.py --quick --top 10
```

结果：36 configs valid，但 quick 模式不包含 FSA，且输出和三场景报告不可直接复现。

```bash
PYTHONPATH=sim python3 -m pytest sim/tests/test_arc_model.py sim/tests/test_arc_precision.py -q
```

结果：5 项中 1 项失败，失败点是 `test_qkv_dimension_3b` 使用旧 Qwen2.5-3B 规格。

## v3 完成定义

Arc Model v3 完成时，应满足：

1. Arc 相关 pytest 通过。
2. 三个 scenario 都能一条命令生成 JSON + Markdown。
3. 每个候选都有 pass/fail reasons。
4. S1/S2/S3 报告中的推荐点可由当前代码复现或明确标为历史结果。
5. FSA 候选清楚标注为 `architecture_candidate`，直到 Func Model v2 验证完成。
6. 输出 Architecture Contract 草案，供 Func Model 开发使用。
