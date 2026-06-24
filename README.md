# CaduceusCore — 通用 NPU 协处理器（CV + LLM）

CaduceusCore 是一颗 **通用 NPU 协处理器**，同时面向 **CV（YOLOv8/ResNet）**和 **LLM（Qwen/Gemma 3B+）** 推理。
核心设计约束：**性能瓶颈在 DRAM 带宽，不在算力**。

128×128+WC 配置的选择理由：更大的阵列（128×256/Block/GMMA）的算力优势被 LPDDR5 带宽全部吃掉，
多花的面积得不到回报。在 75% DRAM 实际效率下，21 tok/s @ 28mm² 是唯一诚实的配置。

详见 `ENGINES.md` 和 `docs/NPU_Engines_Architecture_Guide.md` 的七引擎 PPA 对比。

## 架构概览

- **128×128 Weight-Stationary Systolic Array + WeightCache**（参考 TPUv1/OpenTPU）
- **INT4 权重 + BF16 激活** 混合精度
- **七引擎设计空间搜索**：WS-Systolic / OS-Systolic (Gemmini) / Block (TPUv4) / TensorCore / WMMA / GMMA (H100) / Input-Stationary
- **RISC-V RV64 + 专用 NPU ISA** 主控
- **LPDDR5-6400** 64-bit + **PCIe Gen4 x4**
- **TSMC 12nm** 目标工艺

## 设计空间探索结论（v0.5 — 75% LPDDR5 实际效率）

| 引擎 | tok/s | 面积 | DRAM利用率(75%) | 判定 |
|------|:---:|:---:|:---:|------|
| **WS-Systolic 128×128+WC** ✅ | **21** | **28mm²** | **74%** | 推荐 |
| WS-Systolic 128×128 | 16 | 28mm² | 57% | 保守 |
| WS-Systolic 128×256+WC | 23* | 36mm² | 95% | DRAM临界 |
| OS-Systolic / Block / GMMA / IS | 28* | 48-60mm² | 110%+ | DRAM不可达 |

> *标星号为 75% DRAM 效率下实际可达值，名义模型预测更高但受带宽限制。

**核心结论**：LPDDR5 实际效率 75-80%（含刷新/行冲突/bank竞争），有效带宽 38.4 GB/s。在此约束下，128×128+WC 是唯一诚实的配置——21 tok/s @ 28mm²，DRAM 余量充裕。大引擎（128×256+WC/Block/GMMA）的算力优势被 DRAM 带宽全部吃掉，多花的面积得不到回报。详见 `docs/NPU硬件详细架构设计v0.1.md`。

## 开发工作流

CaduceusCore 的开发遵循严格的三阶段流程：

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Arc Model   │ ──→ │  Func Model  │ ──→ │     RTL      │
│  (架构沙盘)   │     │ (Golden Ref) │     │  (硬件实现)   │
├──────────────┤     ├──────────────┤     ├──────────────┤
│ 职责:         │     │ 职责:         │     │ 职责:         │
│ 设计空间搜索   │     │ 按选定配置     │     │ 照着 Func     │
│ 多引擎对比     │     │ 做 bit-exact  │     │ Model 接口    │
│ 量化方案评估   │     │ 行为模型      │     │ 写 Verilog    │
│ PPA 估算      │     │ ISA 指令仿真   │     │              │
├──────────────┤     ├──────────────┤     ├──────────────┤
│ 产出:         │     │ 产出:         │     │ 产出:         │
│ 最优配置选择   │     │ Golden Ref   │     │ 门级网表      │
│ PPA 报告      │     │ $readmemh    │     │              │
│              │     │ 验证数据      │     │              │
└──────────────┘     └──────┬───────┘     └──────┬───────┘
                            │                    │
                            └──── $readmemh ─────┘
                                 逐比特对比验证
```

**模型即 Spec**：Arc Model 选定的配置是唯一标准。Func Model 照此实现 golden reference。RTL 照 Func Model 接口写，Func Model 输出做 RTL 验证的 bit-exact 参考数据。

## Func Model — 三重角色

1. **RTL 开发的 Spec**：RTL 开发者只需看 Func Model 定义的接口和行为，
   不需要了解 Arc Model 的几百种配置。模块划分、寄存器布局、ISA 指令集
   均以 Func Model 为准。

2. **RTL 验证的 Golden Reference**：
   - Func Model 通过 `sim/gen_rtl_tests.py` 生成 `$readmemh` 格式的
     权值/输入/期望输出 hex 文件
   - RTL 仿真结果必须逐比特匹配 Func Model 的 golden 输出
   - 验证流程: `golden_executor.py gen-test` → Verilog `$readmemh` →
     `compare_rtl.py` → PASS/FAIL

3. **性能测量**（已实现）：
   - 8 个模块独立 cycle 估算（MXU/DMA/NoC/SFU/Vector/KV/DRAM/RISC-V）
   - 事件驱动时间轴 + DMA/NoC 重叠追踪
   - 输出：TTFT、TPS、TPOT、ITL、per-module cycle breakdown、DMA overlap ratio
   - Benchmark CLI 支持设计空间扫描（--sweep-dma-channels / --sweep-noc-topology）
   - Dashboard 输出 JSON + Markdown 双格式报告

### Arc vs Func 对比

| | Arc Model | Func Model |
|------|------|------|
| 用途 | 架构选型（扫参） | 精确实现 + 性能验证 |
| 速度 | 秒级 | 分钟级 |
| 精度 | 近似（解析公式） | 精确（逐 cycle） |
| 测 TPS | ✅ 公式估算 | ✅ 真实流程 |
| 测 TTFT | ❌ 不模拟 prefill | ✅ 202.63ms（Qwen2.5-3B）|
| 输出 | PPA 报告 | Golden Ref + 性能报告 |

详见 [`docs/arc_vs_func.md`](docs/arc_vs_func.md)。

## Timing Pipeline — 性能评估

Func Model 现已完整实现 cycle 级性能模拟，从 YAML 硬件配置到 JSON/MD 性能报告一键生成：

```
  YAML Config → [ModelSpec] → NPUSimulator → TimingEngine → Dashboard
```

### 8 个性能模型

| 模块 | 类 | 建模内容 |
|------|-----|---------|
| MXU | BlockEngine / SystolicEngine / ... (7种) | tile fill + MAC + drain，含 DMA overlap |
| SFU | SFUModel | softmax/layernorm/gelu/silu/RoPE 流水线延迟 |
| Vector | VectorModel | element-wise add/mul/scale/bias |
| DMA | DMAModel | burst/descriptor/FIFO 反压/多通道仲裁 (round_robin/fixed_priority) |
| NoC | NoCModel | crossbar/mesh 拓扑，hop latency + serialisation + contention |
| KV Cache | KVCacheModel | 层间切换 + 逐GEMM KV 访问 |
| DRAM | DRAMModel | LPDDR5/HBM refresh overhead |
| RISC-V | pipeline constants | fetch/decode/dispatch per instruction |

### 核心设计

- **Timeline 事件驱动**：`CoreTimeline` 管理 MXU/DMA/NoC/SFU 的并行重叠关系
- **DMA/NoC breakdown-only**：DMA 和 NoC 事件记录在时间轴上但不推进 wall-clock（避免双重计数），仅用于瓶颈识别
- **DMAModel 共享**：GoldenExecutor（ISA 路径）和 NPUSimulator（GEMM 路径）共用同一 DMAModel 实例，保证 DMA cycle 估算一致性

### 支持的指标

| 指标 | 含义 |
|------|------|
| TPS | decode 吞吐 (tok/s) |
| TTFT | 首 token 延迟 (ms) |
| TPOT | 平均每 token 生成时间 (μs) |
| ITL | Inter-Token Latency 序列 |
| DMA overlap ratio | DMA 被计算隐藏的比例 |
| NoC latency / contention | 片内互联延迟和竞争占比 |

### 设计空间扫描

```bash
# DMA 通道数扫描
python -m sim.timing.benchmark --model qwen2.5-3b --sweep-dma-channels 1,2,4,8
# NoC 拓扑扫描
python -m sim.timing.benchmark --model qwen2.5-3b --sweep-noc-topology crossbar,mesh --sweep-noc-ports 2,4,8
# 全部模型评测
python -m sim.timing.benchmark --all
```

### TTFT Gantt Chart

完整的 TTFT (Time-To-First-Token) 时序分析以三种格式产出——[Mermaid 甘特图 + 精确事件表](docs/ttft_gantt.md) 和 [Matplotlib 三面板 PNG](docs/ttft_gantt.png)。

**Qwen2.5-3B + Block 64×64 + INT4 @ 1GHz + LPDDR5-6400:**
- **TTFT = 202.63ms** (Prefill 168.89ms + 首Token 33.75ms)
- MXU 占 wall-clock 的 92%，DMA 和 NoC 被 tile 计算完全掩盖
- 详见 [`docs/func_model_performance_analysis.md`](docs/func_model_performance_analysis.md)

## 验证体系

### 第一层 — Arc Model 架构评估
- **目的**: 硬件选型决策（引擎类型、阵列尺寸、量化方案）
- **方法**: 设计空间搜索，七引擎 × 六种 DRAM × 多种配置
- **输出**: PPA 报告、最优配置推荐、量化精度 baseline
- **不验证** 硬件实现的正确性

### 第二层 — Func Model 行为验证
- **目的**: Golden Reference 的 bit-exact 正确性
- **方法**: 合成数据测试（smoke test, SFU 精度验证）
- **输出**: 每个模块的 golden 输出 → 用于 RTL 对比
- **当前状态**: 仍在开发中。Smoke 10/10 ✅、SFU Verify 19/19 ✅、E2E 6/6 ✅、pytest 109 tests ✅。SFU/Vector 模块存在空桩，func_model.py 硬编码 trace 路径待修复。详见 `docs/issues_found.md`（待生成）

### 第三层 — E2E 全链路验证 (Spike)
- **目的**: 从真实模型文件到 RISC-V 固件的完整数据流验证
- **方法**: GGUF → INT4 量化 → Spike (RISC-V) + firmware → MMIO bridge → Func Model → 逐层比对
- **当前状态**: Qwen2.5-1.5B 前 2 层 forward pass 通过 Spike 验证。
  - 126 条命令/层 (MMUL 48, SFU 8, Vector 6, DMA 66)
  - 确定性验证: 3 次运行 bit-identical
  - 回归: mmul_smoke PASS
  - 算子覆盖: RMSNorm (SFU OP=6), Softmax, RoPE, SiLU, Vector ADD/MUL
  - qwen_l0_l1_hidden.npz 逐层比对 (已知量化通路数值差异)

详见 `docs/verification_methodology.md`。

## Model Zoo

完整的 Model Zoo 规划定义了 19 个模型（LLM 10 + CV 9），按 B类（Baseline，竞品对标）和 C类（Competitive，独有优势）分层。

详见 [`docs/model_zoo.md`](docs/model_zoo.md)。

### 快速总览

| | LLM | CV |
|------|:---:|:---:|
| B类（竞品对标） | Llama 3.2-1B/3B, Qwen2.5-1.5B, Phi-3.5-mini | MobileNetV3-Small, ResNet-18, YOLOv8n, EfficientDet-Lite0 |
| C类（独有优势） | Qwen2.5-3B, DeepSeek-R1-1.5B, Qwen3-8B, Llama 3.1-8B, Gemma-4-12B, Mistral-7B | **ViT-B/16**（竞品不支持）, ResNet-50, YOLOv8s, EfficientNet-B0, YOLOv8s-seg |

### CV 支持

CV 模型通过 im2col → GEMM 通路复用 MXU 128×128 阵列。核心差异：CaduceusCore 是唯一同时支持 CNN（im2col）和 Transformer（Self-Attention）视觉模型的边缘 NPU。

### 关键结论
- MXU 128×128 阵列 ✅ 可完全复用（im2col→GEMM）
- **ViT-B 零新硬件**（全 GEMM 路径，复用 LLM 的 Self-Attention）
- 需新增: im2col 引擎、ReLU/SiLU/Swish（SFU 扩展）、Pool2D
- SRAM 512KB 需 tiled im2col（YOLOv8s 峰值激活 ~300MB）

## 快速开始

### 性能评估

```bash
# 单次性能仿真
cd sim && PYTHONPATH=. python npu_sim.py --json

# 完整 Benchmark（生成 JSON + MD 报告）
PYTHONPATH=.:sim python -m sim.timing.benchmark --model qwen2.5-3b --output results/timing

# 批量模型评测
PYTHONPATH=.:sim python -m sim.timing.benchmark --all

# DMA 通道数扫描
PYTHONPATH=.:sim python -m sim.timing.benchmark --model qwen2.5-3b --sweep-dma-channels 1,2,4,8

# NoC 拓扑扫描
PYTHONPATH=.:sim python -m sim.timing.benchmark --model qwen2.5-3b --sweep-noc-topology crossbar,mesh --sweep-noc-ports 4

# 切换引擎 / DRAM / 阵列
PYTHONPATH=. python npu_sim.py --engine block --dram 100 --array 128x256 --json

# pytest 全量回归
PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q
```

### 依赖

```bash
pip3 install gguf huggingface_hub pytest tokenizers
```

### 下载模型

```bash
python3 -c "
from huggingface_hub import hf_hub_download
hf_hub_download('Qwen/Qwen2.5-1.5B-Instruct-GGUF',
    'qwen2.5-1.5b-instruct-q4_k_m.gguf', local_dir='$HOME/models')
"
```

### Spike E2E Forward Pass

```bash
# 构建固件
make -C firmware

# 生成 llama.cpp 参考数据
cd llama_ref && make && ./dump_hidden_states \
  -m ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  -p "Hello, world!" -n 2 && python3 save_npz.py

# 单算子通过 Spike 验证
env PYTHONPATH=sim python3 sim/spike_host.py \
  --mode mmul_smoke \
  --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --layers 2 --ops Q_proj,K_proj,V_proj

# Chain 调度验证
env PYTHONPATH=sim python3 sim/spike_host.py \
  --mode chain --ops mmul,sfu,vector,dma_copy

# 完整 2 层 forward pass
env PYTHONPATH=sim python3 sim/spike_host.py \
  --mode forward --layers 2 --prompt "Hello, world!" \
  --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --reference llama_ref/refs/qwen_l0_l1_hidden.npz

# 回归验证
env PYTHONPATH=sim python3 sim/spike_host.py \
  --mode mmul_smoke \
  --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
  --layers 1 --ops Q_proj
```

## 项目结构

```
CaduceusCore/
├── sim/                          # Python 模拟器
│   ├── timing/                   #   性能评估管道 (NEW)
│   │   ├── benchmark.py          #     CLI 入口 + sweep
│   │   ├── timing_engine.py      #     NPUSimulator wrapper
│   │   ├── dashboard.py          #     JSON + MD 报告生成
│   │   ├── metrics.py            #     TPS/TTFT/TPOT 指标推导
│   │   ├── types.py              #     TokenTiming / RequestMetrics
│   │   └── tests/                #     64 tests
│   ├── arc_model.py              #   架构模型 — 设计空间搜索 + 量化精度评估
│   ├── func_model.py             #   Func Model 顶层 — 固件 + MMIO + Golden Executor
│   ├── golden_executor.py        #   Golden Reference — MXU/SFU/Vector/DMA bit-exact
│   ├── npu_sim.py                #   性能模拟器主入口
│   ├── design_space_explorer.py  #   七引擎设计空间搜索
│   ├── e2e_llamacpp.py           #   E2E 全链路验证
│   ├── quantize.py               #   INT4 per-block / per-channel 量化
│   ├── tile_scheduler.py         #   Tile 级 SRAM 双重缓冲调度
│   ├── mmio_bridge.py            #   MMIO 路由 → 模块 handler
│   ├── miniv.py                  #   RISC-V 固件 Python 模拟器
│   ├── regmap.py                 #   MMIO 寄存器映射 (72KB 地址空间)
│   ├── gen_rtl_tests.py          #   $readmemh 测试向量生成
│   ├── compare_rtl.py            #   RTL 输出 vs Golden 比对
│   ├── engine/                   #   引擎实现 (isa, compiler, mac_engine, ppa_model)
│   ├── models/                   #   性能模型 (mxu, sfu, vector, dma, dram, kv_cache, noc)
│   ├── config/                   #   NPU 架构 YAML 配置
│   ├── tests/                    #   pytest 测试套件 (开发中)
│   │   └── test_dma_noc_integration.py  #   DMA + NoC 集成测试 (NEW)
│   └── reports/                  #   架构回溯分析
├── ggml-npu/                     # llama.cpp / GGUF 集成
│   ├── q4_dequant.py             #   Q4_K/Q6_K 反量化 + GGUF 权值加载
│   ├── npu_server.py             #   Hex 协议服务端
│   └── verify_hex.py             #   Hex 结果验证
├── docs/                         # 设计文档
│   ├── NPU硬件详细架构设计v0.1.md  #   硬件架构
│   ├── NPU软件架构方案v0.2.md      #   两阶段软件方案
│   ├── func_model_architecture.md #   Func Model 架构 (最准确的文档)
│   ├── verification_methodology.md#   验证方法论 + 模型库
│   ├── NPU_Engines_Architecture_Guide.md  # 七引擎 PPA 对比
│   ├── ttft_gantt.md             #   TTFT Mermaid 甘特图 + 事件表 (NEW)
│   ├── ttft_gantt.png            #   TTFT 三面板 Matplotlib 图 (NEW)
│   └── func_model_performance_analysis.md  # 性能分析方法论 (NEW)
├── firmware/                     # C 固件 (npu_firmware.c, npu-regmap.h)
├── patches/                      # Spike RISC-V 集成 patch
├── spike_src/                    # Spike 集成 (当前为空)
└── traces/                       # AXI 追踪输出
```

## 软件栈方案（v0.2）

```
阶段 1 (现在, 4-8周): GGUF → llama.cpp → ggml NPU backend → Python Model
阶段 2 (RTL后, 6-12周): PyTorch → ExecuTorch NPU Delegate → NPU 硬件
```

详情见 `docs/NPU软件架构方案v0.2.md`。

## 设计理念

**模型即 Spec**：Python 性能模拟器是唯一事实来源。RTL 照着 simulator 的接口写，
simulator 的 functional mode 做 golden reference 验证。详见上方"开发工作流"和"验证体系"。

## 量化方案

**当前：INT4 per-block (g=128)**。经 Arc Model 对比：
- per-channel: mean_cos=0.9763, min=0.9001
- **per-block: mean_cos=0.9903, min=0.9707** ✅

硬件规格：128×128 systolic, weight-stationary, PE 双 weight 寄存器。
Tile 级调度：K-block × N-tile 双循环，512KB SRAM，8KB weight tile + 512B scale tile per DMA。

## Lessons Learned

### LL-001: 性能模型不等于精度验证（2026-06）

**问题**：INT4 全局 scale 方案在 Func Model 首次跑真实 GGUF 权重时发现完全不可用（rel_err 10³-10⁴）。

**根因**：
- Layer 1 时序模型（`sim/models/mxu.py`/`sim/engine/systolic_engine.py`）只把 `weight_precision_bits=4` 当作字节数计算的除数。从未用真实 Transformer 权重做 INT4 量化→矩阵乘→对比验证。
- Layer 2 软件协议（`ggml-npu/npu_server.py`）走 float32 反量化路线，绕过了硬件 INT4 通路。
- 时序假设被默认当成了「已验证的精度方案」。

**教训**：
> 在搭建任何时序模型之前，先建数值模型验证精度方案能不能跑通。时序模型只回答「多快」，不回答「对不对」。

**修复**：
1. Func Model (`sim/golden_executor.py`) 承担数值模型角色——任何量化方案改动必须先在这里做 bit-exact 验证。
2. 新增验证门禁：新量化方案必须通过 `eval_models.py` 的 Qwen/Gemma 跨层对比（all-layer PASS）才能进入时序评估。
3. 后续 RTL 开发时，Func Model 输出直接作为 `$readmemh` golden reference。

## License

MIT
