# CaduceusCore Func Model 性能分析完整报告——以 Qwen2.5-3B 为例

## 1. 架构总览

CaduceusCore 的 Func Model 是一个 **cycle-level NPU 性能模拟器**。它不对 RTL 进行仿真，而是通过分析模型计算 NPU 硬件各模块在一次推理 request 中需要的时钟周期数，进而推导出 **tok/s、TTFT、TPOT、ITL** 等端到端性能指标。

整个流水线分为四层：

```
  YAML Config  →  [Model Registry]  →  [NPUSimulator]  →  [TimingEngine]  →  [Dashboard]
   (硬件参数)       (模型维度表)           (cycle 级仿真)          (指标聚合)          (JSON/MD 输出)
```

以 Qwen2.5-3B 为例，一次完整的性能分析按以下步骤展开。

---

## 2. Qwen2.5-3B 模型维度

从 `sim/model_specs.py` 中的模型注册表获取：

| 参数 | 值 | 含义 |
|------|-----|------|
| `hidden` | 2560 | 隐藏层维度 |
| `intermediate` | 9728 | FFN 中间层维度 |
| `qkv_dim` | 4096 (32 heads × 128) | Q 投影输出维度 |
| `kv_dim` | 256 (2 KV heads × 128) | K/V 投影输出维度 (GQA=2) |
| `layers` | 28 | Transformer 层数 |
| `num_heads` | 32 | 注意力头数 |
| `kv_heads` | 2 | KV 头数 (Grouped-Query Attention) |
| `head_dim` | 128 | 每个头的维度 |

**每层 7 个矩阵乘法：**

| 序号 | 运算 | 维度 (M,K,N) | 说明 |
|------|------|-------------|------|
| 1 | Q_proj | (M, 2560, 4096) | Query 投影 |
| 2 | K_proj | (M, 2560, 256) | Key 投影 |
| 3 | V_proj | (M, 2560, 256) | Value 投影 |
| 4 | O_proj | (M, 4096, 2560) | Output 投影 |
| 5 | FFN_gate | (M, 2560, 9728) | SiLU 门控 |
| 6 | FFN_up | (M, 2560, 9728) | 上投影 |
| 7 | FFN_down | (M, 9728, 2560) | 下投影 |

> **Decode** 时 M=1（逐 token 生成）；**Prefill** 时 M=128（并行处理 prompt）。

28 层 × 7 个 GEMM = **196 个矩阵乘法**构成一次 decode；prefill 同理。

---

## 3. 硬件配置（NPU 微架构参数）

从 `sim/config/npu_config.yaml` 加载。当前默认配置为：

```yaml
mxu:
  type: block              # Block 引擎 (64×64 BRAM MAC 阵列)
  array_height: 64
  array_width: 64
  frequency_mhz: 1000      # 1GHz
  weight_precision_bits: 4 # INT4 权重量化
  activation_precision_bits: 8
  dataflow: weight_stationary
  double_buffer: true      # PE 双缓冲，允许 gate+up 合并

sram:
  l1_per_core_kb: 512      # 每核 L1 (2×256KB 双口)
  l2_shared_kb: 2048       # 共享 L2

memory:
  type: LPDDR5-6400
  bandwidth_bytes_per_cycle: 51.2  # 1GHz 下 51.2 GB/s

dma:
  num_channels: 2
  burst_size_bytes: 256
  arbitration: round_robin

interconnect:
  type: crossbar
  ports: 4
  bandwidth_gbps: 500
  flit_width_bits: 256
```

---

## 4. 性能模型组件（8 个模块）

`NPUSimulator.__init__()` 实例化以下模型，每个模块独立计算 cycle 开销：

| 模块 | 类 | 建模内容 |
|------|-----|---------|
| **MXU** | `create_engine()` → Block/Systolic/... | 矩阵乘法 tiling + 计算延迟 + DMA 搬运 |
| **SFU** | `SFUModel` | softmax(exp+div)、layernorm、gelu、silu、RoPE 等非线性 |
| **Vector** | `VectorModel` | element-wise add/mul、scale、bias、residual add |
| **DMA** | `DMAModel` | DRAM↔SRAM 权重搬运，含 burst/descriptor/FIFO 反压/多通道仲裁 |
| **NoC** | `NoCModel` | 片内互联 (crossbar/mesh) 逐跳延迟 + 串行化 + 竞争 |
| **KV Cache** | `KVCacheModel` | 层间切换开销 + 逐 GEMM 的 KV 访问 |
| **DRAM** | `DRAMModel` | 刷新开销 (refresh overhead) |
| **RISC-V** | (pipeline 常数) | 取指/译码/分发 overhead |

> **核心设计原则**: 每个模块提供 `estimate()` → cycles 的纯函数，`NPUSimulator` 将各模块的 cycle 注入统一时间轴，并正确处理并行/重叠关系。

---

## 5. 仿真工作流（以 Decode 为例）

### 5.1 Trace 生成

```python
trace = generate_qwen3b_trace(prompt_len=1)   # M=1 decode
# trace = [(1, 2560, 4096, 0, "Q_proj"), (1, 2560, 256, 0, "K_proj"), ...] 共 196 条
```

如果是通过 `TimingEngine`/`benchmark.py` 调用，则使用 `_build_llm_trace()` 从 `ModelSpec` 动态生成，支持任意 LLM 模型。

### 5.2 逐 GEMM 循环

对 trace 中的每个 `(M,K,N,layer,op_name)`：

```
 ┌─────────────────────────────────────────────────────┐
 │ ① MXU: self.mxu.estimate(M,K,N) → EngineResult    │
 │        含 total_cycles / dma_cycles / weight_bytes │
 │    → timeline.add_mxu(cycles)                       │
 │    → layer_data[layer].mxu += cycles                │
 ├─────────────────────────────────────────────────────┤
 │ ② DMA: timeline.add_dma_parallel(dma_cycles)      │
 │    → _current_cycle 恢复到 MXU end (避免双重计数)  │
 │    → estimate_effective(dma, compute) 拆为          │
 │      dma_weight(重叠) + dma_effective(暴露)          │
 ├─────────────────────────────────────────────────────┤
 │ ③ NoC: self.noc.estimate_transfer(weight_bytes)    │
 │    → timeline.add_noc(noc_cycles)                   │
 │    → _current_cycle 恢复到 MXU end                  │
 ├─────────────────────────────────────────────────────┤
 │ ④ SFU+Vector (仅 O_proj / FFN_down 后触发)         │
 │    → softmax 分解: vector(max_reduce) → sfu(exp)   │
 │      → vector(sum_reduce) → sfu(div)                │
 │    → layernorm, RoPE, residual add 等               │
 ├─────────────────────────────────────────────────────┤
 │ ⑤ KV Cache: self.kv.estimate_per_decode()          │
 └─────────────────────────────────────────────────────┘
```

**关键机制——时间轴重叠**：
- `add_mxu()` 设置 `_mxu_busy_until` 水印。
- `add_dma_parallel()` 和 `add_noc()` **不推进** `_current_cycle`（手动恢复到 MXU end），仅记录 breakdown 事件。这避免了 DMA/NoC 与 MXU 计算被重复计时。
- DMA 实际 stall 已由 engine（如 BlockEngine）的 `total_cycles` 内部建模（首 tile 冷启动 + 双缓冲流水线重叠）。

**Weight Cache 优化**：
当 `optimizations.weight_cache: true` 时，`FFN_gate` 和 `FFN_up` 共享 (M,K) 维度，合并为一次 `estimate_weight_cache_pair()`，节省 pipeline fill 开销。

### 5.3 生成 SimulationReport

循环结束后：

```python
total_cycles = timeline.total_cycles      # 时间轴最终位置
decode_us    = total_cycles / f_mhz       # 微秒
decode_tok_per_s = 1e6 / decode_us        # tok/s
breakdown = breakdown_events(timeline.events)  # 按模块聚合 cycle

report = SimulationReport(
    decode_per_token_us = decode_us,
    decode_tok_per_s    = decode_tok_per_s,
    decode_breakdown    = {k: v/f_mhz for k,v in breakdown.items()},  # μs
    layer_breakdowns    = sorted(layer_data),  # 逐层明细
    events              = timeline.events,     # 完整事件流
)
```

`breakdown_events()` 的聚合逻辑：
- `mxu`/`sfu`/`vector`/`kv`：直接累加 cycle
- `dma`：overlapped → `"DMA (hidden)"`；非 overlapped → `"DMA (stall)"`
- `noc`：overlapped → `"noc_latency"`；非 overlapped → `"noc_contention"`

---

## 6. 关键性能指标

| 指标 | 公式 | 含义 |
|------|------|------|
| **TPS** | `freq_mhz × 1e6 / decode_cycles` | decode 吞吐（token/秒） |
| **TTFT** | `(prefill_cycles + first_decode_cycles) / (freq_mhz × 1e3)` ms | 首 token 延迟 |
| **TPOT** | `mean(decode_cycles[1:]) / freq_mhz` μs | 平均每 token 生成时间（排除首 token） |
| **ITL** | `[decode_cycles[i] / freq_mhz for i in range(gen_len)]` μs | Inter-Token Latency 序列 |
| **DMA overlap ratio** | `dma_effective / (dma_weight + dma_effective)` | DMA 被计算隐藏的比例 |
| **Bandwidth utilization** | `(dma_weight + dma_effective) / total_cycles × 100`% | 带宽占用比 |
| **NoC latency** | `noc_latency_cycles / freq_mhz` μs | NoC 传输延迟 |
| **NoC contention** | `noc_contention / total_cycles × 100`% | NoC 竞争占比 |

---

## 7. 如何运行

### 7.1 快速单次仿真

```bash
# Decode + Prefill 快速查看
cd CaduceusCore/sim
PYTHONPATH=. python npu_sim.py --json
```

支持 CLI 实时覆写硬件参数：

```bash
PYTHONPATH=. python npu_sim.py --json \
  --engine block --array 64x128 --dram 100 \
  --freq 2000 --precision 4 --weight-cache
```

### 7.2 完整 Benchmark（生成 JSON + MD 报告）

```bash
cd CaduceusCore
PYTHONPATH=.:sim python -m sim.timing.benchmark \
  --model qwen2.5-3b \
  --prompt-len 128 \
  --gen-len 128 \
  --output results/timing

# 输出:
# results/timing/qwen2.5-3b.json  ← 机器可读
# results/timing/qwen2.5-3b.md    ← 人类可读
```

### 7.3 设计空间扫描

```bash
# DMA 通道数扫描
PYTHONPATH=.:sim python -m sim.timing.benchmark \
  --model qwen2.5-3b --sweep-dma-channels 1,2,4,8
# → results/dma_sweep.csv

# NoC 拓扑扫描
PYTHONPATH=.:sim python -m sim.timing.benchmark \
  --model qwen2.5-3b --sweep-noc-topology crossbar,mesh --sweep-noc-ports 2,4,8
# → results/noc_sweep.csv
```

### 7.4 批量模型评测

```bash
PYTHONPATH=.:sim python -m sim.timing.benchmark --all
# 遍历 model_specs.py 中所有注册模型（qwen2.5-1.5b/3b/7b, qwen3-8b, gemma-4-12b + CV 模型）
```

---

## 8. Qwen2.5-3B 在当前配置下的性能结果

```
┌──────────────────────────────────────────────────┐
│  NPU System Simulation Report                    │
│  Model: Qwen2.5-3B | Layers: 28                  │
│  NPU: 1 core, block, 64×64, INT4, 1000MHz        │
├──────────────────────────────────────────────────┤
│                                                  │
│  --- Decode (per token) ---                      │
│    MXU                 16871.2 μs  (50.0%)        │
│    DMA (stall)         29275.1 μs  (86.8%)  ← 瓶颈│
│    DMA (hidden)         2920.3 μs  ( 8.7%)        │
│    SFU                  1584.4 μs  ( 4.7%)        │
│    Vector               1486.2 μs  ( 4.4%)        │
│    KV Cache              636.5 μs  ( 1.9%)        │
│    noc_latency         43421.8 μs  (128.7%)       │
│    noc_contention           0.0 μs  ( 0.0%)       │
│    ────────────────────────────────────           │
│    TOTAL               33742.5 μs                  │
│    → 29.632 tok/s                                  │
│                                                  │
│  --- Bottleneck Analysis ---                      │
│    🔴 DMA stall 86.8% — bandwidth-bound           │
│                                                  │
│  --- Multi-core Projection ---                    │
│    1 core:    29 tok/s   27mm²  Baseline          │
│    2 core:    57 tok/s   42mm²  DP, -5%           │
│    4 core:   111 tok/s   69mm²  DP, -10%          │
│    8 core:   211 tok/s  122mm²  DP, -15%          │
└──────────────────────────────────────────────────┘
```

**瓶颈分析**：当前 64×64 Block 引擎 + 51.2 GB/s LPDDR5-6400 配置下，性能受限于 **DMA 权重搬运带宽**（DMA stall 占 86.8%）。MXU 计算本身仅消耗 50% 的时间——这是典型的 **带宽受限 (bandwidth-bound)** 场景，而非计算受限。

> **注意**：`noc_latency` 和 `DMA (stall)` 在总和中可能超过 100%，这是因为它们与 MXU 存在部分并行——NoC 和 DMA 事件作为 breakdown-only marker 记录在时间轴上，但 `_current_cycle` 被恢复到 MXU end 位置，所以实际的墙钟时间（TOTAL = MXU end）不被它们延长。Breakdown 中的数字用于识别潜在瓶颈，而非简单的累加。

---

## 9. 数据流全景图

```
                         ┌──────────────┐
   ModelSpec             │ qwen2.5-3b   │  hidden=2560, intermediate=9728,
   (模型维度)             │ 28 layers    │  qkv_dim=4096, kv_heads=2
                         └──────┬───────┘
                                │ _build_llm_trace()
                                ▼
                         ┌──────────────┐
   GEMM Trace             │ 196 tuples   │  (M, K, N, layer, op_name)
   (计算图)               └──────┬───────┘
                                │ NPUSimulator.simulate_decode()
                                ▼
          ┌─────────────────────────────────────────────┐
          │          Per-GEMM Loop (196 iterations)      │
          │                                              │
          │  ┌───────┐  ┌───────┐  ┌───────┐  ┌──────┐ │
          │  │  MXU  │  │  DMA  │  │  NoC  │  │ SFU  │ │
          │  │engine │  │model  │  │model  │  │model │ │
          │  └───┬───┘  └───┬───┘  └───┬───┘  └──┬───┘ │
          │      │          │          │          │     │
          │      ▼          ▼          ▼          ▼     │
          │  ┌───────────────────────────────────────┐  │
          │  │        CoreTimeline (event-driven)     │  │
          │  │  add_mxu → add_dma_parallel           │  │
          │  │  add_noc → restore mxu_end           │  │
          │  │  add_sfu/vector/kv                    │  │
          │  └───────────────────────────────────────┘  │
          │                                              │
          │  ┌───────────────────────────────────────┐  │
          │  │        LayerBreakdown (per layer)      │  │
          │  │  mxu, sfu, vector, dma_weight,        │  │
          │  │  dma_effective, kv_cache,             │  │
          │  │  noc_latency, noc_contention          │  │
          │  └───────────────────────────────────────┘  │
          └─────────────────────────────────────────────┘
                                │
                                ▼
                         ┌──────────────┐
   SimulationReport       │ decode_tps   │  + breakdown (μs) + events + layer_breakdowns
   (仿真结果)             └──────┬───────┘
                                │ TimingEngine._report_to_token_timing()
                                ▼
                         ┌──────────────┐
   TokenTiming            │ total_cycles │  + ModuleBreakdown (8 keys)
   (单 token 指标)        └──────┬───────┘
                                │ MetricsCollector
                                ▼
                         ┌──────────────┐
   RequestMetrics         │ tps, ttft   │  + tpot + itl_us_list
   (完整 request 指标)    └──────┬───────┘
                                │ Dashboard.save()
                                ▼
                         ┌──────────────┐
   Dashboard              │ .json + .md │  NoC metrics, DMA overlap, utilization
   (报告输出)             └──────────────┘
```

---

## 10. 关键设计决策

| 决策 | 原因 |
|------|------|
| Engine 的 `total_cycles` 是权威时间 | MXU engine 内部已建模 tile 流水线 + DMA 冷启动；timeline 不做二次加法 |
| DMA/NoC 事件为 breakdown-only | `add_dma_parallel`/`add_noc` 记录事件后立即恢复 `_current_cycle`，避免双重计数 |
| `estimate_effective(dma, compute)` | 将 DMA cycle 拆为 hidden（被计算掩盖）和 effective（暴露在关键路径），后者计入 layer total |
| Weight Cache 合并 Gate+Up | PE 双 weight 寄存器允许两个共享 (M,K) 的 GEMM 共享一次 pipeline fill |
| NoC self-transfer (src=0,dst=0) | 单核场景下模拟 DRAM→SRAM 穿越跨总线 (crossbar) 的延迟，`noc_contention`=0 表示无竞争 |
| `ModuleBreakdown` 显式字段 | 替代动态 monkey-patching，确保类型安全（`RequestMetrics.module_breakdown: Dict[str,int]`） |

---

## 11. 扩展能力

1. **切换引擎**：`--engine systolic|block|tensor_core|wmma|gmma|input_stationary` 一键切换 7 种 MAC 引擎
2. **切换 DRAM**：`--dram 25|50|100|200|460|819` 从 LPDDR5-32b 到 HBM3
3. **调整阵列**：`--array 128x256` 改变 MAC 阵列尺寸
4. **精度扫描**：`--precision 2|4|8`
5. **DMA 扫描**：`--sweep-dma-channels 1,2,4,8` 输出 CSV
6. **NoC 扫描**：`--sweep-noc-topology crossbar,mesh --sweep-noc-ports 2,4,8`
7. **模型注册**：向 `MODELS` dict 添加新 `ModelSpec` 即可支持任意 Transformer 模型
