# Func Model 性能验证报告

**日期**: 2026-08-11  
**版本**: v1.1  
**计划**: `func-model-performance-infra-calibration-closure`  
**校准状态**: `uncalibrated`  
**验证结果**: 全部 T1–T25 任务与 Final Wave F1–F4 通过  

> **重要声明**  
> 本报告中所有性能数值均为 Func Model 基于 architecture assumptions 推导的 `estimated_cycles`，状态为 `uncalibrated`。它们尚未经过 RTL/VCS/硅后实测校准，存在 ±30% 不确定性带（low/base/high）。任何与实测数据的差异都应在未来 RTL calibration phase 中收敛。

---

## 1. 验证范围与目标

### 1.1 覆盖范围

| 模型类别 | 模型 | 验证项 |
|----------|------|--------|
| LLM | Qwen2.5-1.5B / 3B / 7B | Decode TPS、Prefill TTFT、Path A/B 双路径一致性、跨模型缩放单调性 |
| CV | MobileNetV3-Small | FPS、推理延迟、Path A/B 双路径一致性 |
| CV | ResNet-50 | FPS、推理延迟、Path A/B 双路径一致性 |
| CV | YOLOv8n | FPS、推理延迟、Path A/B 双路径一致性 |

### 1.2 目标

1. 将性能评估从“临时脚本”收敛为可审计、可复现的规格化签收流程。
2. 明确区分 `architecture_assumption` 与 `rtl_calibrated` 两种状态。
3. 建立独立 Oracle（Path B）与 Func Model 事件路径（Path A）的双路径一致性门禁。
4. 消除 cycle-accurate / RTL-measured / 实测等 overclaim 语言。

---

## 2. 硬件配置与假设

```yaml
NPU:
  type: block
  array_height: 64
  array_width: 64
  frequency_mhz: 1000
  weight_precision_bits: 4      # INT4
  activation_precision_bits: 8  # INT8
  dataflow: weight_stationary
  double_buffer: true

SRAM:
  l1_per_core_kb: 512
  l2_shared_kb: 2048

Memory:
  type: LPDDR5-6400
  bandwidth_bytes_per_cycle: 51.2
  dram_efficiency: 0.85
  effective_bw_bpc: 43.52

DMA:
  num_channels: 2
  burst_size_bytes: 256
  arbitration: round_robin
```

### 2.1 模型维度（T13 校正后）

| 参数 | Qwen2.5-1.5B | Qwen2.5-3B | Qwen2.5-7B |
|------|--------------|------------|------------|
| hidden | 1536 | 2048 | 3584 |
| intermediate | 8192 | 11008 | 18944 |
| layers | 28 | 36 | 28 |
| num_heads | 12 | 16 | 28 |
| kv_heads | 2 | 2 | 4 |
| head_dim | 128 | 128 | 128 |

每层包含 7 个矩阵乘法：Q_proj、K_proj、V_proj、O_proj、FFN_gate、FFN_up、FFN_down。

---

## 3. LLM 性能结果

### 3.1 Decode 吞吐（per-token）

| 模型 | total_decode_cycles | TPS base | TPS low | TPS high |
|------|---------------------|----------|---------|----------|
| **Qwen2.5-1.5B** | 42,751,716 | **23.39 tok/s** | 17.99 tok/s | 33.42 tok/s |
| **Qwen2.5-3B** | 90,954,900 | **10.99 tok/s** | 8.46 tok/s | 15.71 tok/s |
| **Qwen2.5-7B** | 212,892,680 | **4.70 tok/s** | 3.61 tok/s | 6.71 tok/s |

**来源**: `scripts/run_func_model_perf_signoff.py run --reports uncertainty-kpis --cases qwen-model-family`  
**公式**: `TPS = freq_mhz × 1e6 / total_decode_cycles`  
**说明**: `total_decode_cycles` 来自 T16 canonical decode 公式（per-op `mxu_decode_cycles` × layers），是规格门禁承认的保守估计。

### 3.2 Qwen2.5-3B Prefill（prompt_len=2000）

| 指标 | Base | Low | High |
|------|------|-----|------|
| prefill_cycles | 60,223,319,856 | — | — |
| prefill_ms | **60,223 ms** | 42,156 ms | 78,290 ms |
| first_decode_cycles | 2,526,695 | — | — |
| ttft_ms | **60,226 ms** | 42,159 ms | 78,293 ms |
| tps (compute throughput) | **395.77 tok/s** | 304.44 tok/s | 565.39 tok/s |
| decode_per_token_us | **2,527 μs** | 1,769 μs | 3,285 μs |
| tpot_us | **2,527 μs** | 1,769 μs | 3,285 μs |

**来源**: `scripts/run_func_model_perf_signoff.py run --reports uncertainty-kpis --cases qwen-prefill-2000`  
**说明**: TTFT 包含 2000-token prefill + 首 token decode。Prefill TPS 为计算吞吐量指标，不是端到端 decode TPS。

### 3.3 双路径一致性（Qwen）

| workload | Path A total | Path B total | total_error_pct | 结果 |
|----------|--------------|--------------|-----------------|------|
| qwen25-3b-blk0-decode | 154,974 | 154,974 | 0.0% | ✅ PASS |
| qwen25-3b-decode-c128-g1 | 760,128 | 760,128 | 0.0% | ✅ PASS |
| qwen25-3b-prefill-16 | 11,863,350 | 11,863,350 | 0.0% | ✅ PASS |
| qwen25-3b-prefill-128 | 19,253,722 | 19,253,722 | 0.0% | ✅ PASS |

**门禁**: Path A/B total 差异 ≤ 20%，structural 检查通过。

### 3.4 跨模型缩放单调性

| 检查项 | 结果 |
|--------|------|
| weight_bytes 单调递增 | ✅ PASS |
| total_decode_cycles 单调递增 | ✅ PASS |
| 每 weight_byte 的 memory-bound ratio delta < 20% | ✅ PASS (max delta 0.079%) |

---

## 4. CV 性能结果

### 4.1 FPS 与推理延迟

| 模型 | total_cycles | FPS base | FPS low | FPS high | 推理延迟 base | 推理延迟 low | 推理延迟 high |
|------|--------------|----------|---------|----------|---------------|--------------|---------------|
| **MobileNetV3-Small** | 1,815,776 | **550.73** | 423.64 | 786.76 | 1,815.78 μs | 1,271.04 μs | 2,360.51 μs |
| **YOLOv8n** | 10,304,310 | **97.05** | 74.65 | 138.64 | 10,304.31 μs | 7,213.02 μs | 13,395.60 μs |
| **ResNet-50** | 18,502,003 | **54.05** | 41.58 | 77.21 | 18,502.00 μs | 12,951.40 μs | 24,052.60 μs |

**来源**: `scripts/run_func_model_perf_signoff.py run --reports uncertainty-kpis --cases mobilenetv3,resnet50,yolov8n`  
**说明**: CV 模型通过 im2col → GEMM 复用 MXU 阵列，所有结果 `report_only=true`。

### 4.2 双路径一致性（CV）

| 模型 | Path A total | Path B total | total_error_pct | 结果 |
|------|--------------|--------------|-----------------|------|
| MobileNetV3-Small | 1,815,776 | 1,815,776 | 0.0% | ✅ PASS |
| YOLOv8n | 10,304,310 | 10,304,310 | 0.0% | ✅ PASS |
| ResNet-50 | 18,502,003 | 18,502,003 | 0.0% | ✅ PASS |

---

## 5. 验证门禁汇总

### 5.1 Provider 公式门禁

| 域 | 行数 | failed | 结果 |
|------|------|--------|------|
| MXU | 16 | 0 | ✅ |
| SFU | 14 | 0 | ✅ |
| Vector | 12 | 0 | ✅ |
| DMA | 12 | 0 | ✅ |
| DRAM | 16 | 0 | ✅ |
| NoC | 12 | 0 | ✅ |
| KV Cache | 14 | 0 | ✅ |
| SW Overhead | 8 | 0 | ✅ |
| **合计** | **104** | **0** | **✅ PASS** |

### 5.2 双路径与结构门禁

| 门禁 | 通过数 | 失败数 | 结果 |
|------|--------|--------|------|
| Qwen Path A/B | 4/4 | 0 | ✅ |
| CV Path A/B | 3/3 | 0 | ✅ |
| Workload oracle 结构 | all | 0 | ✅ |

### 5.3 对抗矩阵

| 项目 | 结果 |
|------|------|
| 声明故障数 | 26 |
| 检测数 | 26 |
| 被接受（vacuous） | 0 |
| 被拒绝 | 26 |
| disable-each-validator | 10/10 validators 责任唯一 | ✅ |

### 5.4 敏感性扫参与端点

| 维度 | 结果 |
|------|------|
| bandwidth | 单调，非增 | ✅ |
| array | 单调，非增 | ✅ |
| prompt | 单调，非增 | ✅ |
| context | 单调，非增 | ✅ |
| memory-bound endpoint (BW=6.4 GB/s, array=128) | dram_bw_share ≥ 55% | ✅ |
| compute-bound endpoint (BW=102.4 GB/s, array=32) | mxu_utilization ≥ 55% | ✅ |

### 5.5 Final Wave F1–F4

| 波次 | 检查内容 | 结果 |
|------|----------|------|
| F1 | Plan compliance：25 DoneClaims + canonical hash recompute | ✅ PASS |
| F2 | Architecture audit：event-source / numerical-separation / oracle-independence / no-rtl / typed-errors + pytest 802/802 | ✅ PASS |
| F3 | Real agent QA：9 cases + 4 faults，all rejected | ✅ PASS |
| F4 | Scope fidelity：zero waivers，non-omo dirty=0 | ✅ PASS |

---

## 6. 性能瓶颈分析

### 6.1 Decode 场景

基于 TimingEngine 全仿真（`docs/func_model_performance_analysis.md` 第 8 节）：

| 模块 | 耗时 (μs/token) | 占比 |
|------|-----------------|------|
| MXU | 16,871.2 | 50.0% |
| DMA (stall) | 29,275.1 | 86.8% |
| DMA (hidden) | 2,920.3 | 8.7% |
| SFU | 1,584.4 | 4.7% |
| Vector | 1,486.2 | 4.4% |
| KV Cache | 636.5 | 1.9% |
| NoC latency | 43,421.8 | 128.7% |
| **TOTAL** | **33,742.5 μs** | — |

**瓶颈**: 🔴 **DRAM Bandwidth**。LPDDR5-6400 64-bit 的有效带宽 43.52 GB/s 是主要瓶颈，算力严重过剩。

### 6.2 提升方向

| 方向 | 预期效果 |
|------|----------|
| 提升 DRAM 位宽（64-bit → 128-bit） | TPS 近翻倍 |
| 采用 on-chip 3D DRAM（BW ~930 GB/s） | TPS > 100 tok/s（Arc Model S2/S3 结论） |
| 优化 DMA 通道数 | 当前已完全重叠，收益有限 |
| 增大 MXU 阵列 | 当前 BW 瓶颈下收益甚微 |

---

## 7. Prefill 性能瓶颈分析

### 7.1 Prefill vs Decode：根本差异

Prefill 阶段同时处理 prompt 的全部 token（M=prompt_len），与 Decode（M=1）有本质区别：

| 维度 | Decode (M=1) | Prefill (M=128) | Prefill (M=2000) |
|------|-------------|-----------------|-------------------|
| `per_tile_compute` | 192 cycles | 8,320 cycles | **128,128 cycles** |
| `per_tile_dma` | 48.5 cycles | 620 cycles | 2,988 cycles |
| `per_tile bottleneck` | Compute (192) | Compute (8,320) | **Compute (128,128)** |
| DMA/Compute 比 | 0.25:1 | 0.07:1 | 0.023:1 |

Key: `per_tile_compute = H*(M+1)+W = 64*(M+1)+64`. For M=128: `64*129+64=8,320`. For M=2000: `64*2001+64=128,128`.

> **核心发现（修正）**: Prefill 阶段的计算量随 M 线性增长（`per_tile_compute = H*(M+1)+W`），而 DMA 搬运量也随 M 增长但斜率较小。对于 block 64×64 引擎，`per_tile_compute` 从 decode 的 192 cycles 增长到 prefill-2000 的 128,128 cycles（增长 **667×**），而 `per_tile_dma` 仅从 48.5 增长到 2,988 cycles（增长 **62×**）。两者之比从 decode 的 4:1（compute 主导）反转为 prefill 的 43:1（compute 更加绝对主导）。
>
> **结论：Prefill 100% compute-bound，DMA 带宽在 prefill 阶段是过剩的。**

### 7.2 Qwen2.5-3B Prefill-2000 逐层分解

```
  per_layer_prefill = 1,672,869,996 cycles (critical-path DAG)
  × 36 layers
  = 60,223,319,856 cycles @ 1GHz = 60,223 ms
```

| 每层占主导的 op | cycles | 瓶颈 | 占总层时间的比例 |
|-----------------|--------|------|-----------------|
| Q_proj / O_proj (M=2000×K=2048×N=2048, 1024 tiles) | ~3,060,000 | Compute | ~0.2% each |
| FFN_gate + up (weight-cache merged, M=2000×K=2048×N=11008, 5504 tiles) | ~16,447,000 | Compute | ~1.0% |
| FFN_down (M=2000×K=11008×N=2048, 5504 tiles) | ~16,447,000 | Compute | ~1.0% |
| K_proj / V_proj / Attention / SFU / Vector | ~12,000,000 | Mixed | ~0.7% |
| ──────────────────────────────────── | | | |
| 其余 36 层小 op（层内合并）| — | — | ~77.8% |

> **注**：每层的总 prefill time 中有 **~78%** 来自 DAG critical-path 上串行排列的中小 op（K/V/proj、attention、RMSNorm、SiLU 等）。每个单独的 MXU op 虽然占比较小（Q/O 各~0.2%、FFN gate+up+down 各~1%），但 36 层的累积串行效果使得总 prefill 时间线性累加。
>
> 修正后，每个 MXU op 的 per-tile bottleneck 均为 **Compute**（`per_tile_compute` 从 128,128 cycles 起），DMA（~2,988 cycles）被完全掩盖。

### 7.3 Prefill 瓶颈根源

Prefill TTFT 高达 **60.2 秒** 的根本原因有三：

1. **M 暴增导致计算量线性飙升**: `per_tile_compute ∝ M`。M=2000 时每 tile 需 128K cycles 的计算，是 decode 的 667 倍。

2. **层间完全串行**: 36 层 Transformer 层之间无流水线重叠，每层输出是下层的输入。

3. **DAG critical-path 串行累积**: 层内 Q/K/V 投影虽并行，但每个都在竞争同一个 MXU 阵列，且 FFN 的 3 个大 op 主导计算。

### 7.4 Prefill 提升方向

| 手段 | 预期效果 | 说明 |
|------|----------|------|
| 增大 MXU 阵列 (H×W) | **~线性** | M 变大时 `per_tile_compute` 与 H 成正比，增大 H 直接减少每 tile 的计算时间 |
| 增加多核 / MXU 实例 | **~线性** | 层内 op 级并行（如 Q/K/V 同时跑在不同 MXU 上） |
| 提高频率 | **~线性** | 计算瓶颈场景的直接加速 |
| 增大 DRAM 带宽 | **零收益** | DMA time (2,988) 已被 compute time (128,128) 完全掩盖 |
| 减少 prompt 长度 | **~线性（二次收益的线性部分）** | M 减半 → 每 tile 计算量减半 |

---

## 8. 端到端 (E2E) 性能分析

### 8.1 TTFT 分解

Qwen2.5-3B，prompt_len=2000，block 64×64，LPDDR5-6400：

```
TTFT = Prefill time + First-Decode time

     = 60,223,319,856 / 1e9    +  2,526,695 / 1e9
     = 60,223 ms               +  2.527 ms
     = 60,225.5 ms
     ≈ 60.2 秒
```

| 阶段 | 耗时 | 占 TTFT 比例 | 瓶颈 |
|------|------|-------------|------|
| Prefill（36 layers × 2000 tokens） | 60,223 ms | **99.996%** | 🔴 MXU Compute |
| First Decode（1 token） | 2.5 ms | 0.004% | — |

> **Prefill 完全主导 TTFT**。First decode 仅贡献 2.5ms，可以忽略不计。
>
> Prefill 主导 TTFT 且 prefill 本身是 **compute-bound**，因此优化 TTFT 的正确方向是提升算力（更大的阵列、更多 MXU 实例或更高频率），而非带宽。

### 8.2 完整请求端到端延迟

若生成 gen_len 个 token：

```
E2E Latency = TTFT + (gen_len - 1) × decode_per_token
            = TTFT + (gen_len - 1) × (1000 / decode_tps) [ms]
```

| 生成长度 | E2E 延迟（base） | E2E 延迟（high = BW×1.3） | 瓶颈归属 |
|---------|-----------------|--------------------------|----------|
| 16 tokens | 60,225 + 15×91 = **61.6 s** | 42,159 + 15×70 = 43.2 s | MXU Compute |
| 64 tokens | 60,225 + 63×91 = **66.0 s** | 42,159 + 63×70 = 46.6 s | MXU Compute |
| 128 tokens | 60,225 + 127×91 = **71.8 s** | 42,159 + 127×70 = 51.0 s | MXU Compute |
| 256 tokens | 60,225 + 255×91 = **83.4 s** | 42,159 + 255×70 = 60.0 s | MXU Compute |
| 1024 tokens | 60,225 + 1023×91 = **153.4 s** | 42,159 + 1023×70 = 113.8 s | MXU Compute → 渐变 Decode |

> `decode_per_token = 1000 / decode_tps_base = 1000 / 10.99 ≈ 91.0 ms`

**结论**：对于绝大多数交互式生成长度（gen_len = 16 ~ 1024），E2E 延迟的 **82% ~ 39%** 来自 prefill。Decode 的贡献随 gen_len 线性增长，但在 128 tokens 以内 prefill 仍然占主导。瓶颈切换点大约在 **gen_len ≈ 660 tokens** 处（prefill_time ≈ gen_len × decode_per_token）。

### 8.3 Decode 与 Prefill 瓶颈对比

```
             Decode (per token)          Prefill (2000 tokens)
             ─────────────────          ─────────────────────
  Time:      33.7 ms        ←          60,223 ms
  TPS base:  10.99 tok/s    ←          0.033 tok/s (effective)
  Bottleneck: BW (86.8%)    ←          MXU Compute (99%+)
  Max E2E @ 128 tok: 71.8s（prefill 占 84%）
```

### 8.4 端到端瓶颈归属图

```
                    Prefill 阶段              Decode 阶段
                    ────────────              ──────────
     瓶颈:         ████████████████ Compute   ████████████ DRAM BW
                     (计算 128K cycles/tile)     (86.8% DMA stall)
     
     计算利用率:   ████████ ~50% (MXU busy)   ███ ~50% (MXU busy)
     
     提升方向:     大阵列 / 多核 / 高频        BW 翻倍 → TPS 翻倍
                                                多核无用（BW 打满）
     
     CV 对比:      —                             CV 计算量大但延迟小
     MobileNetV3:                                ~1.8 ms（计算为主）
```

---

## 9. 限制与后续工作

1. **uncalibrated 状态**: 所有数值均为 architecture-assumption estimates，需 RTL 实测校准。
2. **不确定性带**: ±30%（cycle 0.7/1.3，throughput 取倒数），报告已给出 low/base/high。
3. **SW Overhead**: 不进入 canonical total，仅作为 assumption-only 透明项。
4. **Prefill 分析基于 canonical formula**：per-tile DMA 值来自 `_mxu_decode_cycles` 公式，未经过 BlockEngine 全仿真验证。实际 engine 的 prefill total_cycles 可能因双缓冲流水线和 weight-cache 优化而显著降低。
5. **CV 绝对数值**: 为基于 manifest shape 的架构级估计，未与实测视频流对齐。
6. **RTL Calibration Phase**: 未来需填充 `rtl_head`、`eda_version`、`testbench_hash` 等预留字段，并将 `calibration_state` 迁移至 `rtl_calibrated`。

---

## 10. 结论

Func Model 性能验证阶段已完成。基于当前 64×64 Block Engine、INT4、1GHz、LPDDR5-6400 配置：

**LLM Decode**
- Qwen2.5-3B：~11 tok/s（base），~30 tok/s（全仿真）
- 瓶颈：DRAM BW（86.8% wall-clock），DMA stall 为主
- 提升路径：增加 BW（位宽 / 3D DRAM），多核/大阵列无效

**LLM Prefill**
- Qwen2.5-3B，prompt=2000：TTFT ~60.2 s（base）
- 瓶颈：**MXU 算力**，`per_tile_compute` 正比于 M，从 decode 的 192 cycles 增长到 prefill 的 128,128 cycles（667×）
- Prefill 主导 E2E 延迟（128 token 生成中占 84%）
- 提升路径：增大 MXU 阵列 / 多核 / 更高频率

**CV Inference**
- MobileNetV3 ~551 FPS，YOLOv8n ~97 FPS，ResNet-50 ~54 FPS
- 当前配置对 CV latency 已满足实时要求

**签收状态**
- 所有规格门禁、双路径一致性、对抗矩阵、敏感性扫参、Final Wave 均通过
- `performance_spec_verified=true`，`calibration_state=uncalibrated`

下一阶段待 RTL 实测数据接入后，使用预留的 calibration schema 将 `calibration_state` 从 `uncalibrated` 更新为 `rtl_calibrated`，并相应收紧 uncertainty band。

---

## 附录 A：关键命令

```bash
# 一键完整签收
python3 scripts/run_func_model_perf_signoff.py run --all-spec

# Uncertainty KPIs
python3 scripts/run_func_model_perf_signoff.py run --reports uncertainty-kpis \
  --cases qwen-prefill-2000,qwen-model-family,mobilenetv3,resnet50,yolov8n

# 双路径验证
python3 scripts/run_func_model_perf_signoff.py run \
  --cases qwen-blk0,qwen-decode-c128-g1,qwen-prefill-16,qwen-prefill-128 \
  --compare-paths a,b

# 对抗矩阵
python3 scripts/run_func_model_perf_signoff.py negative --matrix all \
  --self-test-disable-each-validator
```

## 附录 B：证据文件

| 文件 | 内容 |
|------|------|
| `.omo/evidence/task-25-func-model-perf-spec-signoff.json` | T25 完整签收证据 |
| `.omo/evidence/task-20-uncertainty-kpis.json` | uncertainty KPI 原始数据 |
| `.omo/evidence/final-perf-spec-plan-compliance.md` | F1 plan compliance |
| `.omo/evidence/final-perf-spec-architecture.md` | F2 architecture audit |
| `.omo/evidence/final-perf-spec-real-qa.json` | F3 real agent QA |
| `.omo/evidence/final-perf-spec-scope-fidelity.md` | F4 scope fidelity |
