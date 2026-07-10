# Arc Model v3 改进计划

> 本文按当前项目推进口径定义 Arc Model v3：在 v2 引入 FSA 之后，把 DSE 从“可探索的分析脚本”升级为“产品需求驱动、可复现、可交接给 Func Model 的架构候选生成器”。

## 目标

Arc Model v3 不直接生成 RTL spec。它的目标是：

1. 从产品需求和场景约束出发，生成可复现的架构候选。
2. 对候选做硬约束过滤、PPA 排名、敏感性分析和交叉校验。
3. 输出 Architecture Contract 草案，交给 Func Model v2+ 做 executable spec / golden reference。

v3 完成后，README 中的三场景 DSE 结论应该能由一组命令复现，而不是只存在于手写报告里。

## 当前基线

当前分支：`feat_arc_model`

已完成的 v3.0 baseline：

1. `sim/design_space_explorer.py` 已支持 `--scenario`。
2. `lpddr5_3b` / `onchip_7b` / `onchip_7b_chat` 可从 `sim/config/scenarios.yaml` 注入内存、工艺和约束。
3. on-chip 3D DRAM 场景的搜索空间已覆盖 `80x1536` 这类宽阵列。
4. `tok_s_from_layer()` 已按配置频率计算，不再写死 1000MHz。
5. 主 sweep 不再静默吞掉 invalid config，并在 JSON 里输出 `invalid_configs`。
6. DSE 输出已带基础 `config` 结构，含 scenario、engine、array、precision、frequency、memory、SRAM 等字段。
7. Arc 相关基础测试已恢复通过。

v3.0 仍只是 baseline，不代表 Arc Model v3 完成。后续开发重点是把 DSE 从“能跑 scenario”升级成“产品约束驱动、可审计、可生成 Architecture Contract”。

## 剩余问题

### P0: 产品约束尚未完全进入 DSE 闭环

- Pareto 仍主要按 `max tok/s / min area`，还没有把功耗、TTFT、内存形态、精度门禁和产品线约束作为完整 hard filter。
- 输出还缺少完整的 `pass/fail reasons`、TTFT、带宽利用率、TOPS、memory feasibility。
- 部分敏感性分析仍依赖 `config_label` 字符串反解析，容易遗漏 SRAM、on-chip memory、scenario 等关键维度。

### P0: 性能模型关键路径缺口

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

- `sim/design_space_explorer.py` 已经承担搜索空间、仿真、敏感性、CLI、报告、交叉校验等多个职责，需要拆分。
- JSON 结构仍不够稳定，后续 Func Model / report generator 不应依赖临时字段。
- 三场景报告中的历史结论和当前代码可复现结果需要明确区分。

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

## 开发计划

### 总体节奏

Arc Model v3 按 5 个增量版本推进：

| 版本 | 目标 | 主要产物 | 合并门槛 |
|------|------|----------|----------|
| v3.1 | 约束闭环和结果结构稳定 | `DSEPoint`/constraint schema、pass/fail reasons、scenario hard filter | 三个 scenario 均能输出可审计 JSON |
| v3.2 | TTFT/prefill/FSA 建模闭环 | decode/prefill trace、FSA attention path、TTFT breakdown | Block 与 Func Model baseline 可对比，FSA 走真实 attention 估算路径 |
| v3.3 | Memory/PPA 产品化校准 | 3D DRAM stack model、area/power breakdown、calibration notes | S2/S3 宽阵列候选 area/BW/power 可解释 |
| v3.4 | 报告和 Architecture Contract 生成 | JSON + Markdown + YAML contract 输出 | Func Model 能按 contract 启动 v2 建模 |
| v3.5 | 工程拆分和长期维护 | `sim/dse/` 模块化、CLI wrapper、回归测试 | `design_space_explorer.py` 只保留 CLI 和编排 |

开发默认继续在 `/home/prj/zhengs/codex/CaduceusCore` 的 `feat_arc_model` 分支上进行。每个版本都应保持命令行兼容：已有 `--quick`、`--top`、`--output`、`--model-spec`、`--batch-m`、`--cv-model` 不应被破坏。

### v3.0 Baseline Fix

目标：让现有 Arc 结果可信、测试基线干净。

状态：已完成。

已交付：

1. 统一 Qwen2.5-3B 模型规格和测试。
2. 修正 frequency sweep 写死 1000MHz。
3. 移除 silent exception，输出 invalid config 列表。
4. 增加 `--scenario` baseline。
5. 输出基础 structured config JSON，先不重构所有模块。

验收：

```bash
PYTHONPATH=sim python3 -m pytest sim/tests/test_arc_model.py sim/tests/test_arc_precision.py sim/tests/test_design_space_explorer.py -q
PYTHONPATH=sim python3 sim/design_space_explorer.py --quick --output results/dse/quick.json
```

### v3.1 Constraint and Result Schema Closure

目标：一条命令输出每个产品场景的可审计候选集，并能解释每个候选为什么通过或失败。

开发项：

1. 引入稳定结果结构：
   - `DSEConfig`
   - `DSEMetrics`
   - `ConstraintResult`
   - `DSEPoint`
2. 把 scenario constraints 变成 hard filter：
   - `tps_min`
   - `ttft_ms_max`
   - `area_mm2_max`
   - `power_w_max`，如果 scenario 未显式提供则输出 warning，不静默通过
   - required/excluded memory components
3. JSON 输出增加：
   - `passed_results`
   - `rejected_results`
   - `failed_reasons`
   - `warnings`
   - `scenario_hash`
   - `config_hash`
4. Pareto 和 sensitivity analysis 改为读取结构化字段，不再反解析 `config_label`。
5. 保留 `config_label` 作为展示字段，但禁止作为计算输入。

验收：

```bash
PYTHONPATH=sim python3 sim/design_space_explorer.py --scenario lpddr5_3b --output results/dse/lpddr5_3b.json
PYTHONPATH=sim python3 sim/design_space_explorer.py --scenario onchip_7b --output results/dse/onchip_7b.json
PYTHONPATH=sim python3 sim/design_space_explorer.py --scenario onchip_7b_chat --output results/dse/onchip_7b_chat.json
```

JSON 验收点：

- 每个 scenario 都有 `passed_results` 和 `rejected_results`。
- 每个 rejected point 至少有一个 `failed_reasons`。
- 每个 passed point 都有 scenario、engine、array、precision、frequency、memory、area、power、tok/s。
- 若没有 `power_w_max`，输出 warning，而不是隐式忽略功耗约束。

### v3.2 TTFT / FSA Model Closure

目标：FSA 和 block 在同一 prefill/decode 模型下公平比较。

开发项：

1. 把 workload 拆成 decode trace 和 prefill trace。
2. prefill trace 至少覆盖：
   - Q/K/V/O projection
   - QK^T
   - softmax
   - PV
   - FFN gate/up/down
   - RMSNorm/RoPE/SiLU 等 SFU/Vector 开销
   - DMA/NoC/SRAM overlap
3. 非 FSA 引擎使用 `QK^T + softmax + PV` attention 模型。
4. FSA 引擎必须通过 `FSAEngine.estimate_attention()` 建模 attention。
5. `batch_m > 1` 的 KV path 要显式建模 KV write/read，不再用 0 占位支撑结论。
6. 输出：
   - `decode_tok_s`
   - `prefill_ms`
   - `ttft_ms`
   - `cycle_breakdown.compute`
   - `cycle_breakdown.dma`
   - `cycle_breakdown.attention`
   - `cycle_breakdown.sfu_vector`

验收：

- Arc 对 Block 64x64 的 decode TPS 与 `docs/func_model_performance_analysis.md` 中 Func Model baseline 可对比，并解释差异来源。
- FSA scenario 结果中能证明 attention path 调用了 FSA 模型，而不是普通 GEMM 近似。
- `ttft_ms` 不是空字段或常数占位。

### v3.3 Memory / PPA Calibration

目标：on-chip 3D DRAM 方案可用于产品讨论。

开发项：

1. 显式 memory stack model：
   - `ExternalLPDDR5`
   - `OnChip3DMemory`
   - `HBM`
2. on-chip 3D DRAM 需要结构化建模：
   - capacity
   - bandwidth
   - bandwidth per mm2
   - stack area
   - TSV overhead
   - package overhead
   - stack power
3. external DRAM 和 on-chip memory 的 power model 分离。
4. area/power 输出拆分为：
   - compute
   - SRAM
   - memory PHY or memory stack
   - DMA/NoC
   - PCIe
   - overhead
5. cross-validation TOPS 从实际 `H * W * ops_per_mac * frequency` 推导。
6. 产品数据库来源字段进入 output，不把 benchmark 口径混进自研估算。

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
- `arc_version: v3`
- `scenario`
- `model`
- `engine`
- `array`
- `memory`
- `precision`
- `constraints`
- `metrics`
- `assumptions`
- `source_commit`

验收：

```bash
PYTHONPATH=sim python3 sim/design_space_explorer.py --scenario lpddr5_3b --report --contract
```

应生成：

- `results/dse/lpddr5_3b.json`
- `reports/arc-v3-lpddr5_3b.md`
- `contracts/architecture/lpddr5_3b.yaml`

### v3.5 Modularization

目标：降低 Arc Model v3 后续演进成本，避免 DSE 主文件继续膨胀。

开发项：

1. 按下表拆分 `sim/design_space_explorer.py`。
2. CLI wrapper 只负责解析参数、调用 evaluator、写输出。
3. 新模块都需要有局部单元测试。
4. 保持现有 CLI 行为兼容。

验收：

```bash
PYTHONPATH=sim python3 -m pytest sim/tests/test_arc_model.py sim/tests/test_arc_precision.py sim/tests/test_design_space_explorer.py -q
PYTHONPATH=sim python3 sim/design_space_explorer.py --quick --top 5 --output /tmp/caduceus_quick.json
PYTHONPATH=sim python3 sim/design_space_explorer.py --quick --scenario lpddr5_3b --top 5 --output /tmp/caduceus_lpddr5_3b.json
PYTHONPATH=sim python3 sim/design_space_explorer.py --quick --scenario onchip_7b --top 5 --output /tmp/caduceus_onchip_7b.json
```

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

v3.0 baseline 已验证：

```bash
PYTHONPATH=sim python3 -m pytest sim/tests/test_arc_model.py sim/tests/test_arc_precision.py sim/tests/test_design_space_explorer.py -q
```

结果：7 passed。

```bash
PYTHONPATH=sim python3 sim/design_space_explorer.py --quick --top 5 --output /tmp/caduceus_quick.json
PYTHONPATH=sim python3 sim/design_space_explorer.py --quick --scenario lpddr5_3b --top 5 --output /tmp/caduceus_lpddr5_3b.json
PYTHONPATH=sim python3 sim/design_space_explorer.py --quick --scenario onchip_7b --top 5 --output /tmp/caduceus_onchip_7b.json
```

结果：

- default quick：36 valid configs。
- `lpddr5_3b` quick：18 valid configs。
- `onchip_7b` quick：26 valid configs。

限制：

- quick 模式仍不是三场景报告的完整复现。
- `ttft_ms`、prefill attention、FSA attention path、memory stack PPA 仍待 v3.2/v3.3 完成。
- FSA 候选在 Func Model v2 未实现前只能标注为 `architecture_candidate`。

## v3 完成定义

Arc Model v3 完成时，应满足：

1. Arc 相关 pytest 通过。
2. 三个 scenario 都能一条命令生成 JSON + Markdown。
3. 每个候选都有 pass/fail reasons。
4. S1/S2/S3 报告中的推荐点可由当前代码复现或明确标为历史结果。
5. FSA 候选清楚标注为 `architecture_candidate`，直到 Func Model v2 验证完成。
6. 输出 Architecture Contract 草案，供 Func Model 开发使用。
