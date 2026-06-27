# CaduceusCore — 通用 NPU 协处理器（CV + LLM）

CaduceusCore 是一颗 **通用 NPU 协处理器**，同时面向 **CV（YOLOv8/ResNet）**和 **LLM（Qwen/Gemma 3B+）** 推理。
核心设计约束：**性能瓶颈在 DRAM 带宽，不在算力**。

> **DSE 与 RTL 状态说明**：架构设计空间探索（DSE）显示 **128×128+WC** 是面向后续 Phase 的面积/功耗最优目标配置——在 75% LPDDR5 实际效率下可达 21 tok/s @ 28mm²，更大的阵列（128×256/Block/GMMA）的算力优势会被 DRAM 带宽吃掉。当前 **RTL Phase 1** 已实现 **64×64 广播 MAC 阵列（INT4×INT8→INT32）** 作为第一步硬件验证，后续 Phase 将向 128×128+WC 扩展。
>
> 详见 `ENGINES.md`、`docs/NPU_Engines_Architecture_Guide.md` 的七引擎 PPA 对比，以及 `rtl/mxu/README.md`。

## Quick Start

**Prerequisites:** Python 3.10+, pip, git. For full pytest: `dtc_src/` at REPO_ROOT parent (see docs/spike-integration.md), built Spike binary and firmware ELF. Core sim/model tests work with just Python deps.

**Setup:**
```bash
git clone git@github.com:anekin/CaduceusCore.git
cd CaduceusCore
pip install -r requirements.txt
```

**Functional verification:**
```bash
PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q
```
Expect 210 passed (fewer without dtc/spike/firmware; sim/timing tests are self-contained).

**Performance benchmark:**
```bash
PYTHONPATH=sim python sim/timing/benchmark.py --alias qwen25-3b
PYTHONPATH=sim python sim/timing/benchmark.py --alias mobilenetv3
```

**RTL verification (optional, requires Synopsys VCS):** See `rtl/mxu/README.md`.

## 架构概览

- **当前 RTL Phase 1：64×64 Broadcast MAC Array**（INT4×INT8→INT32，MMIO 控制）
- **当前 RTL Phase 2：SFU + Vector Engine**（7 个 FP16 特殊函数管道 + 5 个 INT32 向量引擎模块，MMIO 控制）
- **Arc Model 目标：128×128 Weight-Stationary Systolic Array + WeightCache**（参考 TPUv1/OpenTPU）
- **INT4 权重 + INT8 激活**（当前 RTL）；BF16 激活为后续扩展方向
- **七引擎设计空间搜索**：WS-Systolic / OS-Systolic (Gemmini) / Block (TPUv4) / TensorCore / WMMA / GMMA (H100) / Input-Stationary
- **RISC-V RV64 + 专用 NPU ISA** 主控
- **LPDDR5-6400** 64-bit + **PCIe Gen4 x4**
- **TSMC 12nm** 目标工艺

## 设计空间探索结论（v0.5 — 75% LPDDR5 实际效率）

> 以下为 Arc Model 的分析结果（架构选型阶段）。当前 RTL Phase 1 实现了 **64×64 MAC 阵列**作为第一步验证；**128×128+WC** 是后续 Phase 的面积/功耗最优扩展方向。

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

## RTL Phase 1 — 64×64 Broadcast MAC

RTL Phase 1 完成了 MXU（Matrix Multiplication Unit）的 Verilog 实现，作为 128×128+WC 目标架构前的第一步验证。

### 模块清单

| 模块 | 文件 | 行数 | 说明 |
|------|------|------|------|
| mxu_top | `rtl/mxu/mxu_top.v` | 314 | 顶层集成：MMIO、controller、mac_array、weight_buffer、activation_buffer |
| mmio_if | `rtl/mxu/mmio_if.v` | 172 | MMIO 寄存器接口（ctrl/cmd/status/dims/addr） |
| controller | `rtl/mxu/controller.v` | 329 | N/M/K 三维 tile 迭代 FSM |
| mac_array | `rtl/mxu/mac_array.v` | 201 | 64×64 PE 网格 + 每 PE 累加 |
| pe | `rtl/mxu/pe.v` | 80 | 单 PE：INT4×INT8→INT32，1-cycle 流水线 |
| accumulator | `rtl/mxu/accumulator.v` | 108 | 64×64 INT32 跨 tile 累加存储 |
| weight_buffer | `rtl/mxu/weight_buffer.v` | 51 | 64×64 INT4 SRAM（2:1 packed） |
| activation_buffer | `rtl/mxu/activation_buffer.v` | 49 | 64×64 INT8 SRAM |

合计 8 个 RTL 文件，1,304 行 Verilog。

### 验证结果

- **single_tile**：4096/4096 INT32 值与 Golden 逐比特匹配 — **PASSED**
- **9 个命名场景**（multi_tile_K / multi_tile_N / multi_tile_M / overflow / zero_dim / partial_tile_K / partial_tile_N / partial_tile_M）：**9/9 PASSED**
- **随机回归**：100 个随机 (M,N,K) 组合 — **100/100 PASSED**
- **Qwen2.5-3B E2E**：真实 Q4_K_M 权重（K=2048, N=2048, M=1），1024 个 tile 计算 — **PASSED**
- **Pytest 回归**：210/210 通过（150 sim + 60 timing）

详见 `rtl/mxu/README.md`。

## RTL Phase 2 — SFU + Vector Engine

RTL Phase 2 完成了 SFU（Special Function Unit）和 Vector Engine 的 Verilog 实现。SFU 提供 FP16 特殊函数运算（softmax/layernorm/GELU/SiLU/RoPE/RMSNorm），Vector Engine 提供 INT32 向量运算（add/mul/max/sum_reduce/type_convert/resid_add）。两者均通过 MMIO 接口与 MXU 集成。

### 模块清单

**SFU（8 RTL 文件，2,678 行 Verilog）**

| 模块 | 文件 | 行数 | 说明 |
|------|------|------|------|
| sfu_top | `rtl/sfu/sfu_top.v` | 654 | 顶层集成：MMIO、op router、sram 读写控制 |
| softmax_hw | `rtl/sfu/softmax_hw.v` | 462 | 8-stage 流水线 softmax（LUT exp + 迭代除法） |
| layernorm_hw | `rtl/sfu/layernorm_hw.v` | 364 | 6-stage 流水线 LayerNorm（mean/var/norm，定点） |
| rmsnorm_hw | `rtl/sfu/rmsnorm_hw.v` | 362 | two-pass RMSNorm（平方和→ sqrt → 归一化） |
| rope_hw | `rtl/sfu/rope_hw.v` | 306 | 16-stage CORDIC 旋转（RoPE，Q18.14 定点） |
| gelu_hw | `rtl/sfu/gelu_hw.v` | 275 | 4-stage GELU（64-entry LUT，4-segment 近似） |
| silu_hw | `rtl/sfu/silu_hw.v` | 210 | 4-stage SiLU（复用 exp_lut，Newton-Raphson） |
| exp_lut | `rtl/sfu/exp_lut.v` | 45 | 256-entry exp(x) LUT ROM（Q1.14，线性插值） |

**Vector Engine（5 RTL 文件，1,094 行 Verilog）**

| 模块 | 文件 | 行数 | 说明 |
|------|------|------|------|
| vector_top | `rtl/vector/vector_top.v` | 494 | 顶层集成：MMIO、op dispatch、chunk 迭代控制 |
| vector_alu | `rtl/vector/vector_alu.v` | 154 | 128-wide SIMD ALU（add/mul/max/pass_a，1-cycle） |
| type_convert | `rtl/vector/type_convert.v` | 207 | INT32→FP16 转换器（IEEE 754 half-precision） |
| reduce_tree | `rtl/vector/reduce_tree.v` | 134 | 128→1 流水线规约树（max/sum，7-cycle） |
| resid_add | `rtl/vector/resid_add.v` | 105 | 128-wide INT32 饱和残差加法器（1-cycle） |

合计 13 个 RTL 文件，3,772 行 Verilog。

### 算子覆盖

| 引擎 | 算子 | 描述 |
|------|------|------|
| SFU | softmax | 8-stage 流水线，LUT-based exp + iterative division |
| SFU | layernorm | 6-stage，定点 mean/var/normalize |
| SFU | gelu | 4-stage，tanh 近似 + 64-entry LUT |
| SFU | silu | 4-stage，复用 exp_lut + Newton-Raphson reciprocal |
| SFU | rope | 16-stage CORDIC，Q18.14 定点 |
| SFU | rmsnorm | two-pass，Newton-Raphson sqrt/reciprocal |
| Vector | add | 128-wide INT32 SIMD 加法，饱和钳位 |
| Vector | mul | 128-wide INT32 SIMD 乘法，饱和钳位 |
| Vector | max | 128-wide INT32 逐元素取大 |
| Vector | max_reduce | 128→1 流水线规约取大 |
| Vector | sum_reduce | 128→1 流水线规约求和（INT64 中间累加）|
| Vector | conv | INT32→FP16 类型转换（MXU→SFU 桥梁） |
| Vector | resid_add | 128-wide INT32 饱和残差加法 |

### 验证结果

- **SFU batch regression**：319/319 场景通过 inline comparison（`compare_sfu.py`，abs_tol=2e-3，rel_tol=1e-2）— **PASSED**
- **Vector batch regression**：63/63 场景通过 inline comparison（INT32 bit-exact + CONV FP16 tolerance）— **PASSED**
- **INT32→FP16 逐值扫描**：131,073/131,073 个值在 [-65536, 65536] 范围内与 numpy float16 逐比特匹配 — **PASSED**
- **E2E real-model**：Qwen2.5-3B blk.0 真实权重（softmax resid_add）— **PASSED**
- **Pytest 回归**：210/210 通过（150 sim + 60 timing）

详见 `rtl/sfu/README.md` 和 `rtl/vector/README.md`。

### VCS 快速入门

```bash
# 生成 LUT 文件（exp_lut.hex + gelu_lut.hex）
python3 scripts/gen_sfu_luts.py

# 生成全部 319 SFU + 63 Vector 测试向量
python3 scripts/gen_sfu_vectors.py --scenario all
python3 scripts/gen_vector_vectors.py --scenario all

# 批量回归（编译 + 运行 SFU 和 Vector 的所有场景）
python3 scripts/run_batch_regression.py
```

SFU 和 Vector 的 testbench 均采用 inline 比较：RTL 输出后立即调用 `compare_sfu.py` 比对 golden 并打印 PASS/FAIL。`compare_rtl.py` 保持不变；SFU/RoPE 使用更宽松的浮点容差（abs_tol=2e-3，rel_tol=1e-2），Vector INT32 运算使用逐比特精确比较。

## RTL Phase 3 — SoC Integration (2026-06)

RTL Phase 3 完成了 **CaduceusCore NPU SoC 全芯片集成**——Ibex RISC-V（RV32IMC）作为控制核，6 个 AXI4 master（Ibex/MXU/SFU/Vector/DMA/PCIe）通过自研 AXI4 crossbar 共享 4MB SRAM + 2GB DRAM，APB decoder 连接 7 个 MMIO slave。Qwen2.5-3B blk.0 Cocotb smoke 验证通过。

### 模块清单

| 模块 | 文件 | 行数 | 说明 |
|------|------|------|------|
| caduceus_soc_top | `rtl/soc/caduceus_soc_top.v` | 1272 | 全芯片顶层集成（12 个模块实例化） |
| axi_crossbar | `rtl/soc/axi_crossbar.v` | 578 | M=6, S=2 AXI4 crossbar, round-robin |
| sram_ctrl | `rtl/soc/sram_ctrl.v` | ~350 | 4MB AXI4 slave, 512-bit, burst 支持 |
| apb_decoder | `rtl/soc/apb_decoder.v` | ~200 | 1→7 APB decoder, pslverr 路径 |
| boot_rom | `rtl/soc/boot_rom.v` | ~80 | 64KB ROM, $readmemh 加载 firmware |
| doorbell | `rtl/soc/doorbell.v` | 113 | Host↔NPU ring buffer doorbell |
| ibex_wrapper | `rtl/cpu/ibex_wrapper.v` | ~400 | Ibex RV32IMC + AXI4/APB adapter |
| intc_top | `rtl/intc/intc_top.v` | 180 | 7-source interrupt controller |
| dma_wrapper | `rtl/ip/dma_wrapper.v` | 441 | axi_cdma DMA wrapper (verilog-axi, MIT) |
| pcie_ep_wrapper | `rtl/ip/pcie_ep_wrapper.v` | ~500 | PCIe EP wrapper (verilog-pcie, MIT) |
| dram_model | `rtl/ip/dram_model.v` | ~360 | 2GB DRAM behavioral model |
| mxu_soc_wrapper | `rtl/wrapper/mxu_soc_wrapper.v` | ~300 | MXU: APB + AXI4 + broadcast sequencer |
| sfu_soc_wrapper | `rtl/wrapper/sfu_soc_wrapper.v` | ~300 | SFU: APB + AXI4 + width converter |
| vector_soc_wrapper | `rtl/wrapper/vector_soc_wrapper.v` | ~300 | Vector: APB + AXI4 + width adapter |

合计 **18+ RTL 文件** 新增（不含 vendored 第三方 IP），~5,000+ 行 Verilog。

### AXI4 Crossbar 拓扑

```
  Master 0: Ibex ──┐
  Master 1: MXU  ──┤
  Master 2: SFU  ──┤   AXI4       ┌── SRAM (0x2000_0000) S0: 4MB
  Master 3: Vec  ──┤  Crossbar    │
  Master 4: DMA  ──┤  M=6, S=2   ├── DRAM (0x8000_0000) S1: 2GB
  Master 5: PCIe ──┘  round-robin┘
```

### 统一地址空间

| 区域 | 基地址 | 大小 | 用途 |
|------|--------|------|------|
| Boot ROM | `0x0000_0000` | 64 KB | Ibex 复位向量 + 固件 |
| Ibex DMEM | `0x0001_0000` | 64 KB | 栈 + .data/.bss |
| SRAM | `0x2000_0000` | 4 MB | NPU 统一计算缓冲区 |
| MXU MMIO | `0x4000_0000` | 4 KB | MXU 寄存器 |
| SFU MMIO | `0x4000_1000` | 4 KB | SFU 寄存器 |
| VECTOR MMIO | `0x4000_2000` | 4 KB | Vector 寄存器 |
| DMA MMIO | `0x4000_3000` | 4 KB | DMA 寄存器 |
| PCIe MMIO | `0x4000_4000` | 4 KB | PCIe 寄存器 |
| DOORBELL | `0x4000_5000` | 4 KB | Host↔NPU doorbell |
| INTC MMIO | `0x4000_6000` | 4 KB | 中断控制器 |
| DRAM | `0x8000_0000` | 2 GB | DRAM 数据空间 |

### 开源 IP 选型与 License

| 组件 | 开源选择 | License | 商业替换方案 | 接口 |
|------|---------|---------|-------------|------|
| **DMA** | `axi_cdma` (alexforencich/verilog-axi) | MIT | Synopsys DW_axi_dmac | AXI4 master + APB slave |
| **PCIe** | `pcie_axi_master` (alexforencich/verilog-pcie) | MIT | Synopsys DWC PCIe EP | AXI4 master + TLP bridge |
| **NoC** | 自研 AXI crossbar | CaduceusCore | Arteris FlexNoC | AXI4 crossbar, port 对齐 |
| **RISC-V** | Ibex (lowRISC) | Apache 2.0 | — | 已满足需求 |
| **DRAM** | LiteDRAM-based behavioral | CaduceusCore | Synopsys uMCTL2 | AXI4 slave |

> **替换原则**: 所有 IP 通过标准 AXI4/APB 接口通信。替换时仅需修改 `caduceus_soc_top.v` 中的模块实例化，地址映射、中断路由保持不变。

### 验证结果

- **MMIO consistency**: 49 registers match (`check_mmio_map.py`) — **PASSED**
- **Crossbar concurrent stress**: M=6/S=2, MXU+DMA+PCIe ≥10k cycles, 1,260 txns, 0 errors — **PASSED**
- **DMA wrapper**: 5 test cases, APB readback + CMD.START + STATUS.BUSY — **PASSED**
- **INTC 7-source**: 13/13 interrupt checks (PENDING/ENABLE/THRESHOLD/ACK) — **PASSED**
- **SoC elaboration**: 47 modules, 0 errors, 0 undriven — **PASSED**
- **Cocotb E2E smoke**: Qwen2.5-3B blk.0 4-instruction smoke (MMUL+RMSNorm+Softmax+Residual) — **PASSED**
- **Pytest 回归**: 210/210 通过（150 sim + 60 timing）

### VCS 全芯片编译

```bash
# 环境: EDA server (sz0001, 192.168.0.11), module load vcs/vcs_2023.12sp2
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
    -f rtl/cpu/ibex.flist \
    -f rtl/ip/verilog-axi.flist \
    -f rtl/ip/verilog-pcie.flist \
    -f rtl/soc/soc.flist \
    -top caduceus_soc_top -o simv_soc_top -l elaborate.log
```

### 模块级回归

```bash
cd sim/regression
make run_apb_smoke       # APB decoder
make run_intc_test       # INTC 7-source
make run_dma_test        # DMA wrapper
make run_crossbar_stress # AXI crossbar stress
make run_pcie_test       # PCIe EP wrapper
make run_dram_test       # DRAM model
make -j4 all             # All tests parallel
```

详见 `rtl/soc/README.md` 和 `rtl/ip/README.md`。

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

| | Arc Model | Func Model | RTL (Phase 1+2) |
|------|------|------|------|
| 用途 | 架构选型（扫参） | 精确实现 + 性能验证 | 硬件实现 |
| 速度 | 秒级 | 分钟级 | cycle 级 |
| 精度 | 近似（解析公式） | 精确（逐 cycle） | 门级/bit-exact |
| 测 TPS | ✅ 公式估算 | ✅ 真实流程 | — |
| 测 TTFT | ❌ 不模拟 prefill | ✅ 202.63ms（Qwen2.5-3B）| — |
| 输出 | PPA 报告 | Golden Ref + 性能报告 | MXU 64×64 MAC + SFU 7-op + Vector 6-op Verilog |
| 阵列配置 | 128×128 WS-Systolic + WC | 128×128 WS-Systolic + WC | 64×64 Broadcast MAC |

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
- **当前状态**: 已完成。Smoke 10/10 ✅、SFU Verify 19/19 ✅、E2E 6/6 ✅、pytest 210/210 ✅（150 sim + 60 timing）。SFU 和 Vector 的 Golden Reference 已随 RTL Phase 2 验证完成并通过 batch regression。详见 `rtl/sfu/README.md` 和 `rtl/vector/README.md`。

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

CV 模型通过 im2col → GEMM 通路复用 MXU 阵列。当前 RTL Phase 1 实现了 **64×64 MAC 阵列**；Arc Model DSE 显示 **128×128** 为最优配置。核心差异：CaduceusCore 是唯一同时支持 CNN（im2col）和 Transformer（Self-Attention）视觉模型的边缘 NPU。

### 关键结论
- MXU 阵列 ✅ 可完全复用（im2col→GEMM）；RTL Phase 1 为 64×64，目标配置 128×128
- **ViT-B 零新硬件**（全 GEMM 路径，复用 LLM 的 Self-Attention）
- 需新增: im2col 引擎、Pool2D（SiLU/Swish 已通过 Phase 2 SFU 支持）
- SRAM 512KB 需 tiled im2col（YOLOv8s 峰值激活 ~300MB）

## 快速开始

### 环境与依赖

```bash
pip install -r requirements.txt
```

### pytest 全量回归

```bash
PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q
```

### 性能评估 / Benchmark

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
```

### RTL 仿真

**MXU Phase 1：**
```bash
# 生成 MXU 测试向量
python3 scripts/gen_mxu_vectors.py --scenario all --out-dir rtl/test_vectors/mxu

# 编译并仿真（需 Synopsys VCS，详见 rtl/mxu/README.md）
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_mxu \
    rtl/tb/tb_mxu.v rtl/mxu/*.v -o simv_mxu
./simv_mxu +testdir=rtl/test_vectors/mxu/single_tile +scenario=single_tile

python3 sim/compare_rtl.py rtl/test_vectors/mxu/single_tile
```

**SFU + Vector Phase 2（需 Synopsys VCS）：**
```bash
# 生成 LUT 和全部测试向量
python3 scripts/gen_sfu_luts.py
python3 scripts/gen_sfu_vectors.py --scenario all
python3 scripts/gen_vector_vectors.py --scenario all

# 批量回归（自动编译 + 运行所有 319 SFU + 63 Vector 场景）
python3 scripts/run_batch_regression.py
```

详见 `rtl/sfu/README.md` 和 `rtl/vector/README.md`。

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
│   ├── cocotb_bridge.py          #   Cocotb Python 控制层 (Phase 3, NEW)
│   ├── check_mmio_map.py         #   MMIO 一致性检查 (Phase 3, NEW)
│   ├── engine/                   #   引擎实现 (isa, compiler, mac_engine, ppa_model)
│   ├── models/                   #   性能模型 (mxu, sfu, vector, dma, dram, kv_cache, noc)
│   ├── config/                   #   NPU 架构 YAML 配置
│   │   └── interconnect.yaml     #    AXI crossbar 互连配置 (Phase 3, NEW)
│   ├── regression/               #   SoC 回归测试 (Phase 3, NEW)
│   │   └── Makefile              #    8 targets (apb/intc/dma/crossbar/pcie/dram/soc/qwen)
│   ├── tests/                    #   pytest 测试套件 (开发中)
│   │   └── test_dma_noc_integration.py  #   DMA + NoC 集成测试 (NEW)
│   └── reports/                  #   架构回溯分析
├── rtl/                          # Verilog RTL (Phase 1 + 2 + 3)
│   ├── mxu/                      #   64×64 Broadcast MAC 阵列 (Phase 1)
│   │   ├── mxu_top.v             #     顶层集成
│   │   ├── mmio_if.v             #     MMIO 寄存器接口
│   │   ├── controller.v          #     N/M/K tile 迭代 FSM
│   │   ├── mac_array.v           #     64×64 PE 网格
│   │   ├── pe.v                  #     单 PE (INT4×INT8→INT32)
│   │   ├── accumulator.v         #     64×64 INT32 累加器
│   │   ├── weight_buffer.v       #     64×64 INT4 SRAM
│   │   └── activation_buffer.v   #     64×64 INT8 SRAM
│   ├── sfu/                      #   SFU (Phase 2)
│   │   ├── sfu_top.v             #     顶层集成 + MMIO op router
│   │   ├── softmax_hw.v          #     8-stage 流水线 softmax
│   │   ├── layernorm_hw.v        #     6-stage 流水线 LayerNorm
│   │   ├── rmsnorm_hw.v          #     two-pass RMSNorm
│   │   ├── rope_hw.v             #     16-stage CORDIC RoPE
│   │   ├── gelu_hw.v             #     4-stage GELU (LUT)
│   │   ├── silu_hw.v             #     4-stage SiLU (exp)
│   │   ├── exp_lut.v             #     256-entry exp LUT ROM
│   │   └── README.md             #     SFU 验证文档
│   ├── vector/                   #   Vector Engine (Phase 2)
│   │   ├── vector_top.v          #     顶层集成 + MMIO op dispatch
│   │   ├── vector_alu.v          #     128-wide SIMD ALU
│   │   ├── reduce_tree.v         #     128→1 规约树
│   │   ├── type_convert.v        #     INT32→FP16 转换器
│   │   ├── resid_add.v           #     128-wide 残差加法器
│   │   └── README.md             #     Vector 验证文档
│   ├── soc/                      #   SoC 集成 (Phase 3)
│   │   ├── caduceus_soc_top.v    #     全芯片顶层 (12 模块实例化)
│   │   ├── axi_crossbar.v        #     M=6/S=2 AXI4 crossbar, round-robin
│   │   ├── sram_ctrl.v           #     4MB AXI4 slave, 512-bit burst
│   │   ├── apb_decoder.v         #     1→7 APB decoder
│   │   ├── boot_rom.v            #     64KB 启动 ROM ($readmemh)
│   │   ├── doorbell.v            #     Host↔NPU ring buffer doorbell
│   │   ├── soc.flist             #     VCS 统一 filelist
│   │   └── README.md             #     SoC 文档 (NEW)
│   ├── ip/                       #   IP Wrappers (Phase 3)
│   │   ├── dma_wrapper.v         #     axi_cdma DMA wrapper (verilog-axi, MIT)
│   │   ├── pcie_ep_wrapper.v     #     PCIe EP wrapper (verilog-pcie, MIT)
│   │   ├── dram_model.v          #     2GB DRAM 行为模型
│   │   ├── verilog-axi/          #     vendored verilog-axi (alexforencich, MIT)
│   │   ├── verilog-pcie/         #     vendored verilog-pcie (alexforencich, MIT)
│   │   └── README.md             #     IP 文档 (NEW)
│   ├── wrapper/                  #   Engine Wrappers (Phase 3)
│   │   ├── mxu_soc_wrapper.v     #     MXU: APB + AXI4 + broadcast sequencer
│   │   ├── sfu_soc_wrapper.v     #     SFU: APB + AXI4 + width converter
│   │   ├── vector_soc_wrapper.v  #     Vector: APB + AXI4 + width adapter
│   │   └── apb_to_mmio.v         #     APB→原生 MMIO 桥接
│   ├── cpu/                      #   RISC-V CPU (Phase 3)
│   │   ├── ibex_wrapper.v        #     Ibex RV32IMC + AXI4/APB adapter
│   │   ├── ibex.flist            #     Ibex VCS filelist
│   │   └── ibex/                 #     vendored Ibex (lowRISC, Apache 2.0)
│   ├── intc/                     #   中断控制器 (Phase 3)
│   │   └── intc_top.v            #     7-source (PENDING/ENABLE/THRESHOLD/ACK)
│   ├── tb/                       #   Testbenches
│   │   ├── tb_mxu.v              #     MXU testbench
│   │   ├── tb_sfu.v              #     SFU testbench (self-checking)
│   │   ├── tb_vector.v           #     Vector testbench (self-checking)
│   │   ├── tb_soc.v              #     全芯片 Cocotb/DPI testbench (Phase 3)
│   │   └── tb_exp_lut.sv         #     exp_lut 独立测试
│   └── test_vectors/             #   测试向量 (生成式)
│       ├── mxu/                  #     MXU 场景目录
│       ├── sfu/                  #     SFU 场景目录 (生成式)
│       └── vector/               #     Vector 场景目录 (生成式)
├── scripts/                      # 辅助脚本
│   ├── gen_mxu_vectors.py        #   MXU 测试向量生成
│   ├── gen_sfu_vectors.py        #   SFU 测试向量生成 (Phase 2)
│   ├── gen_sfu_luts.py           #   SFU LUT 生成 (Phase 2)
│   ├── compare_sfu.py            #   SFU inline 比较器 (Phase 2)
│   ├── gen_vector_vectors.py     #   Vector 测试向量生成 (Phase 2)
│   ├── gen_e2e_qwen_vectors.py   #   E2E Qwen 测试向量生成 (Phase 2)
│   ├── run_batch_regression.py   #   SFU + Vector 批量回归 (Phase 2)
│   └── validate_interconnect.py  #   Crossbar 互连验证 (Phase 3, NEW)
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
│   ├── rtl_development_plan.md    #   RTL 开发计划 (Phase 3/4/5 完成状态更新, NEW)
│   ├── ttft_gantt.md             #   TTFT Mermaid 甘特图 + 事件表 (NEW)
│   ├── ttft_gantt.png            #   TTFT 三面板 Matplotlib 图 (NEW)
│   └── func_model_performance_analysis.md  # 性能分析方法论 (NEW)
├── requirements.txt              # Python 依赖包
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

硬件规格（当前 RTL Phase 1+2）：**64×64 broadcast MAC 阵列 + SFU（7 个 FP16 算子）+ Vector Engine（6 个 INT32 向量算子），MMIO 控制接口**。
Tile 级调度：N/M/K 三维 tile 循环状态机，512KB SRAM，8KB weight tile + 512B scale tile per DMA。

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
