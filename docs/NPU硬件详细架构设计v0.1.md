# NPU 硬件详细架构设计 v0.2

> 基于 Phase 1 MRD/PRD 的技术指标，进入微架构设计
> 2026-06-17
> **v0.2 更新**：NPU 改为 IP 核架构，支持单核/多核实例化

---

## 一、设计原则

1. **不重复造轮子**：TPUv1/OpenTPU 验证过的模块直接复用或参考，专注重写 Transformer 特有部分
2. **IP 核化**：NPU 核心作为可参数化 IP，SoC 客户可实例化 1-N 个核，共享外设
3. **片内多核扩展**：核间走片上 Crossbar/FIFO（~500 GB/s），不走 PCIe P2P（7.88 GB/s）
4. **模块化**：每个模块独立验证，通过标准 AXI4 总线互联
5. **混合精度**：权重 INT4，激活 BF16，累加 INT32，KV Cache INT8
6. **约简**：不做多余的灵活性和可编程性——这是一个推理专用加速器

---

## 二、顶层架构

### 2.1 芯片顶层框图（多核配置示例：4 核）

```
┌──────────────────────────────────────────────────────────────┐
│              NPU SoC @ TSMC 12nm                              │
│                                                              │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐                       │
│  │ 核₀  │ │ 核₁  │ │ 核₂  │ │ 核₃  │  ← 每个核：MXU+SFU+L1 │
│  │128× │ │128× │ │128× │ │128× │                       │
│  │128  │ │128  │ │128  │ │128  │                       │
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘                       │
│     │        │        │        │                             │
│     ├────────┼──┬─────┼────────┤  ← 核间 FIFO（流水线并行）  │
│     │        │  │     │        │                             │
│     └────┬───┴──┴─────┴───┬────┘                             │
│          │                │                                   │
│     ┌────┴────────────────┴────┐                              │
│     │   Crossbar / NoC 互联     │  ← ~500 GB/s 核间+L2+外设  │
│     │   共享 L2 SRAM 2-8 MB    │                              │
│     └────┬────────────────┬────┘                              │
│          │                │                                   │
│  ┌───────┴───────┐ ┌──────┴──────────┐                       │
│  │ LPDDR5 控制器  │ │ RISC-V + DMA    │                       │
│  │ 64/128-bit    │ │ + PCIe EP        │                       │
│  │ (共享)        │ │ (共享)           │                       │
│  └───────────────┘ └─────────────────┘                       │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 核内结构（单 NPU 核心）

```
┌─────────────────────────────────┐
│         NPU Core                │
│  ┌──────┐  ┌──────┐  ┌───────┐ │
│  │ MXU  │  │ SFU  │  │ KV    │ │
│  │128×  │  │7算子 │  │ Cache │ │
│  │128   │  │      │  │ 256KB │ │
│  └──┬───┘  └──┬───┘  └───┬───┘ │
│     │         │          │     │
│  ┌──┴─────────┴──────────┴───┐ │
│  │    L1 SRAM 256KB×2 (双口) │ │
│  └────────────┬──────────────┘ │
│               │ ← 核间 FIFO    │
│  ┌────────────┴──────────────┐ │
│  │   AXI4 从接口（挂 Crossbar）│ │
│  └───────────────────────────┘ │
└─────────────────────────────────┘
```

### 2.3 模块清单（按配置缩放）

### 2.3 模块清单（按配置缩放）

| 模块 | 每核 | 共享 | 复用程度 | 开发量 |
|------|:---:|:---:|------|:---:|
| MXU（128×128 Systolic Array） | 8mm² | — | 参考 TPUv1/OpenTPU | 中等 |
| L1 SRAM 256KB×2 | 1mm² | — | 标准 SRAM 宏 | 低 |
| SFU（7 算子） | 2mm² | — | 中科院+自研 | 高 |
| KV Cache SRAM 256KB | 1mm² | — | 自研 | 高 |
| 核间 FIFO 4KB×2 | 0.5mm² | — | 标准 FIFO IP | 低 |
| **每核心小计** | **~12.5mm²** | | | |
| | | | | |
| Crossbar / NoC | — | 1.5mm² | 标准互联 | 低 |
| L2 SRAM 2-8 MB | — | 5-20mm² | 标准 SRAM | 低 |
| RISC-V 核 + 指令发射 | — | 1mm² | Coral NPU 范式 | 低 |
| DMA 引擎 | — | 1mm² | 业界参考 | 中等 |
| PCIe Gen4 x4 EP | — | 3.5mm² | 商用 IP（采购） | 无 |
| LPDDR5 控制器+PHY | — | 7mm² | 商用 IP（采购） | 无 |

### 2.4 按核心数缩放的总面积

| 配置 | 核心×面积 | +共享 | 总面积 | INT8 TOPS | 适用 |
|------|:---:|:---:|:---:|------|------|
| 1 核 | 1×12.5 | 14 | **~27 mm²** | 26-33 | 3B 模型，25 tok/s |
| 2 核 | 2×12.5 | 14+3 | **~42 mm²** | 52-66 | 7B 流水线，~18 tok/s |
| 4 核 | 4×12.5 | 14+5 | **~69 mm²** | 104-132 | 13B 流水线，~12 tok/s |
| 8 核 | 8×12.5 | 14+8 | **~122 mm²** | 208-264 | 30B 流水线，~10 tok/s |

> 共享增量含：L2 扩容（2/4/8/12MB）+ Crossbar 端口 ×N + 更多 DMA 通道

---

## 三、模块详细设计

### 3.1 MXU：128×128 Weight-Stationary Systolic Array

#### 设计参考
- **TPUv1**（Google, ISCA 2017）：256×256，INT8 weight-stationary，已验证架构
- **OpenTPU**（UCSB ArchLab）：TPUv1 的开源 Verilog 重实现
- **Gemmini**（UC Berkeley）：可配置 systolic array 生成器，Chisel → Verilog
- **Calabash**（IEEE 2023）：双 systolic array 链做 attention

#### 结构

```
         ← weights 预加载（对角线流水）
    ┌───────────────────────────────────┐
    │  PE₀,₀   PE₀,₁  PE₀,₂  ... PE₀,₁₂₇│ ← activation 数据从左向右流
    │    ↓        ↓       ↓           ↓  │
    │  PE₁,₀   PE₁,₁  PE₁,₂  ... PE₁,₁₂₇│
    │    ↓        ↓       ↓           ↓  │
    │   ...      ...     ...         ... │
    │    ↓        ↓       ↓           ↓  │
    │  PE₁₂₇,₀ PE₁₂₇,₁ ...  PE₁₂₇,₁₂₇ │
    └───────────────────────────────────┘
         ↓ 部分和向下累积
```

每个 PE：**INT4×INT8 → INT32 乘累加**，一个周期完成。

#### 关键设计参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 阵列尺寸 | 128×128 | TPUv1 的 1/4 |
| MAC 数 | 16,384 | |
| 频率 | 800MHz-1GHz | TSMC 12nm 保守/典型 |
| INT8 算力 | 26-33 TOPS | 128²×2×f |
| 权重精度 | INT4 | 每个 PE 存 4-bit 权重 |
| 激活精度 | INT8 | 广播到阵列 |
| 累加精度 | INT32 | 避免溢出 |
| 数据流 | Weight-Stationary | 权重静态，激活数据流 |
| 双缓冲 | 是 | 一组运算时另一组加载权重 |

#### 与 TPUv1 的差异

| 维度 | TPUv1 | 本设计 | 原因 |
|------|------|------|------|
| 尺寸 | 256×256 | 128×128 | 面积/功耗/3B 模型匹配 |
| 精度 | INT8×INT8 | INT4×INT8 | 3B INT4 量化 |
| 控制 | 主机 CPU 逐指令发 | RISC-V 核本地调度 | 降低 PCIe 交互延迟 |

#### 开发策略
1. 从 OpenTPU Verilog 代码提取 PE 模块和阵列拓扑
2. 修改 PE 内部 MAC 为 INT4×INT8→INT32
3. 缩减尺寸：256×256 → 128×128（参数化配置）
4. 增加双缓冲控制逻辑
5. 加 INT4 解包逻辑（每个字节存 2 个 INT4 权重）

---

### 3.2 Unified Buffer + Accumulator

#### 设计参考
- **TPUv1**：Unified Buffer 24MB，双缓冲，直接连 MXU
- **TPUv2+**：合并 Accumulator 和 Activation Storage 为 Vector Memory

#### 结构

```
   LPDDR5                    MXU
     │                        │
     ▼                        ▼
┌─────────┐  ┌─────────────────────┐
│  DMA    │  │  Unified Buffer      │
│  Engine │──┤  (Scratchpad SRAM)   │
└─────────┘  │  2 MB, 16 Banks      │
             │  双缓冲 Ping/Pong    │
             └──────────┬──────────┘
                        │
                   ┌────┴────┐
                   │ Accum   │ ← INT32 → BF16 转换
                   │ Regs    │
                   └────┬────┘
                        │
                    ┌───┴───┐
                    │  SFU  │  ← Activation pipeline
                    └───────┘
```

#### 参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 容量 | 2 MB | 约 TPUv1 的 1/12，匹配 128×128 阵列 |
| Bank | 16 | 并行读写避免冲突 |
| 双缓冲 | Ping-Pong | 一区运算，一区 DMA 加载 |
| 接口 | 读写 256-bit/cycle | 喂饱 128×128 阵列 |

---

### 3.3 SFU：Special Function Unit

#### 设计参考
- **中科院（2024）**："Hardware-oriented algorithms for softmax and layer normalization" — 统一 Softmax+LayerNorm 硬件架构
- **Softmax-Hardware-Accelerator**（GitHub）：流水线化 softmax
- **TPUv2 Vector Unit**：可编程向量单元替代 TPUv1 固定函数数据通路

#### 算子清单与硬件方案

| 算子 | 硬件方案 | 流水线级数 | 面积 |
|------|---------|:---:|:---:|
| **Softmax** | 指数查表 + 分段减法归一化 | 8 | ~0.4mm² |
| **LayerNorm** | 并行均值/方差 + 融合乘加 | 6 | ~0.3mm² |
| **GELU** | 分段查表 + 线性插值 | 4 | ~0.2mm² |
| **ReLU** | 组合逻辑（max(0,x)） | 1 | ~0.05mm² |
| **MaxPool 2×2** | 滑动窗口 4 选 1 比较器 | 3 | ~0.1mm² |
| **AvgPool 2×2** | 加法 + 移位（÷4） | 3 | ~0.1mm² |
| **RoPE** | CORDIC 旋转 | 12 | ~0.3mm² |
| **SiLU** | 复用 GELU 查表 | 4 | ~0.05mm² |

总 SFU 面积 ~1.5mm²。非同时激活——算子间共享部分硬件。

#### 中科院统一架构（参考实现）

论文方案将 Softmax 和 LayerNorm 融合为单一硬件通路——先算均值/方差（LN 前半段），再用指数减法归一化（Softmax 后半段），省了中间 buffer。我们在这个基础上加 CNN 算子即可。

---

### 3.4 KV Cache Manager

#### 参考
- **腾讯（2025 专利）**："多轮推理 KV-cache 内存碎片化导致 L2 命中率下降"
- **TPI-LLM**（OpenReview 2025）：多边缘设备 tensor parallelism 的 KV cache 调度
- **HeadInfer**（arXiv 2025）：按 head 卸载 KV cache，单卡 4090 跑 4M token 上下文

#### 设计

```
  ┌─────────────────────────────────────┐
  │            KV Cache Manager          │
  │                                     │
  │  ┌──────────┐   ┌───────────────┐   │
  │  │  页表     │   │  KV SRAM Cache │   │
  │  │  (硬件)   │   │  256 KB        │   │
  │  │  64 条目  │   │  (最近 N token) │   │
  │  └────┬─────┘   └───────┬───────┘   │
  │       │                 │           │
  │  ┌────┴─────────────────┴───────┐   │
  │  │        DMA 搬移逻辑           │   │
  │  │   SRAM ↔ LPDDR5 (KV区)       │   │
  │  └──────────────────────────────┘   │
  └─────────────────────────────────────┘
```

| 参数 | 值 | 说明 |
|------|-----|------|
| KV 存储主位置 | LPDDR5 专用区域 | 2000 token × 3B × 2(K+V) ≈ 96 MB |
| KV Cache SRAM | 256 KB | 缓存最近 16 个 token，命中率 >85% |
| 格式 | INT8 | 省带宽 |
| 页表 | 硬件 64 条目 | RISC-V 维护，DMA 搬移 |
| 地址管理 | 连续分配 | 避免碎片（参考腾讯专利的教训） |

#### KV Cache 为什么不永久放 SRAM

2000 token 的 KV cache 需 96 MB，远超 2 MB L2 SRAM。只能放 LPDDR5。256 KB SRAM 缓存最近 token 足够——Transformer decode 只访问当前 token 和最近几个 token 的 KV。

---

### 3.5 DMA 引擎

#### 功能

- LPDDR5 ↔ L2 SRAM 数据搬移
- 支持 scatter-gather（分散-聚合）描述符链
- 双通道：权重加载通道 + 数据搬移通道
- 与 MXU 运算流水线化——运算时不阻塞 DMA

#### 参数

| 参数 | 值 |
|------|-----|
| 通道数 | 2 |
| 最大 burst | 256 bytes |
| 描述符队列深度 | 16 |

---

### 3.6 RISC-V 主控核

#### 设计参考
- **Coral NPU**（Google）：RISC-V RV32IMF + AXI4 总线 + 4 级流水线

#### 功能

1. **指令解析**：从指令 FIFO 取指，解码，分派到 MXU/SFU/DMA
2. **DMA 描述符管理**：构建描述符链，下发到 DMA 引擎
3. **异常处理**：MXU 溢出、DMA 超时、PCIe 错误
4. **不参与计算**：所有数值运算由 MXU/SFU 执行

#### 参数

| 参数 | 值 | 说明 |
|------|-----|------|
| ISA | RV64IMAFD | 64-bit，支持原子操作 |
| 流水线 | 4 级 | 取指-译码-执行-写回 |
| I-Cache | 16 KB | 指令缓存 |
| D-Cache | 16 KB | 数据缓存 |
| 中断 | 16 个外部中断源 | |

---

### 3.7 NPU 指令集架构（ISA）

CISC 风格，一条指令 = 一个完整算子。32-bit 定长指令字。

| 指令 | 格式 | 说明 |
|------|------|------|
| `MMUL wa, ia, oa, N` | 4 字段 | 权重地址 wa，输入地址 ia，输出地址 oa，大小 N。触发 MXU |
| `SOFTMAX sa, da, len` | 3 字段 | 源地址，目标地址，向量长度 |
| `LAYERNORM sa, da, len` | 3 字段 | |
| `GELU sa, da, len` | 3 字段 | |
| `RELU sa, da, len` | 3 字段 | |
| `MAXPOOL sa, da, H, W` | 4 字段 | |
| `AVGPOOL sa, da, H, W` | 4 字段 | |
| `ROPE sa, da, len, theta` | 4 字段 | |
| `DMA_LD dram, sram, size` | 3 字段 | LPDDR5→SRAM |
| `DMA_ST sram, dram, size` | 3 字段 | SRAM→LPDDR5 |
| `KV_LOAD token_id` | 1 字段 | 将指定 token 的 KV 加载到 SRAM cache |
| `KV_STORE token_id` | 1 字段 | 当前计算的 KV 写入 LPDDR5 |
| `BARRIER` | 0 字段 | 流水线同步 |
| `NOP` | 0 字段 | |

---

## 四、数据流与流水线

### 4.1 LLM Decode 阶段流水线

```
Time ──────────────────────────────────────────────────────→

DMA:   [Load W₀] [Load W₁] [Load W₂] ...
MXU:             [MMUL₀ ] [MMUL₁ ] [MMUL₂ ] ...
SFU:                       [SM/LN₀] [GELU₀] [SM/LN₁] ...
DMA:   [KV-Ld ]                                   [KV-St ]
```

权重加载和矩阵运算流水线重叠——DMA 加载 Layer N+1 权重的同时，MXU 正在算 Layer N。

### 4.2 CNN 推理流水线

```
DMA:   [Load W]  [Load IA]
MXU:             [MMUL (im2col→GEMM)]
SFU:                       [ReLU] [Pool]
```

简单流水线。CNN 层间数据量小，DMA 几乎不占时间。

---

## 五、多核扩展架构（片内 IP 实例化）

### 5.1 核心思想

NPU 作为可参数化 IP 核。SoC 客户在同一个 die 上实例化 1-N 个核，所有外设（LPDDR5/PCIe/RISC-V）共享一套。核间通过片上互联（Crossbar）和专用 FIFO 通道通信，带宽 ~500 GB/s，比 PCIe P2P 快 60 倍。

**与 PCIe 多卡方案的对比**：

| | PCIe 多卡 | **片内多核 IP** |
|------|------|------|
| 核间带宽 | 7.88 GB/s (Gen4 x4) | **~500 GB/s** |
| 核间延迟 | ~1μs | **~10ns** |
| 每增一核算力 | +1 张卡（~$37） | **+12.5mm² die** |
| 共享 LPDDR5 | ❌ 各卡独立 | ✅ 共享物理内存池 |
| 扩展上限 | 受 PCIe 槽数限制 | 受 die 面积限制 |
| 商业模式 | 卖卡给终端用户 | **卖 IP 给 SoC 厂商** |

### 5.2 IP 参数化配置

```verilog
module npu_top #(
    parameter int NUM_CORES      = 1,   // 1, 2, 4, 8
    parameter int MXU_SIZE       = 128, // 64, 128, 256
    parameter int L2_SIZE_KB     = 2048,// 共享 L2
    parameter int LPDDR_DATA_W   = 64,  // 64 / 128
    parameter bit ENABLE_FIFO    = 1    // 使能核间 FIFO
) (
    input  wire        clk,
    input  wire        rst_n,
    // AXI4 主接口 → LPDDR5 控制器
    output wire [31:0] m_axi_awaddr,
    ...
    // AXI4 从接口 ← PCIe / 主机
    input  wire [31:0] s_axi_awaddr,
    ...
    // 中断输出
    output wire [NUM_CORES-1:0] irq
);
```

SoC 客户配 `NUM_CORES=4`，综合工具自动生成 4 核。

### 5.3 三种工作模式

| 模式 | 核间通信 | 每核 L1 | 共享 L2 | 适用场景 |
|------|:---:|------|------|------|
| **独立模式** | 无 | 私有 | 每核独立分区 | 数据并行——N 用户各占一核 |
| **共享内存** | L2 SRAM | 私有 | 权重分片共享 | 单模型跨核——权重拆到 L2 |
| **流水线 FIFO** | 核间 FIFO | 私有 | 激活中转 | 层间流水线——激活值直传 |

### 5.4 核间 FIFO 设计

```
核₀ ──[FIFO 4KB]──→ 核₁ ──[FIFO 4KB]──→ 核₂ ──→ ...
 ←──[FIFO 4KB]──   ←──[FIFO 4KB]──
```

- 双向各 4KB → 每核 8KB FIFO 存储
- 4KB = 512 个 BF16 元素，足够装一层中间激活
- 宽度 256-bit，深度 128。2 cycle 延迟
- 仅在流水线模式使能（`ENABLE_FIFO=1`）。不使能时综合工具优化掉

### 5.5 按核心数的性能缩放

| 配置 | 面积 | TOPS | 独立模式（吞吐） | 流水线模式（大模型） |
|------|:---:|------|------|------|
| 1 核 | 27mm² | 26-33 | 25 tok/s ×1 | 3B: 25 tok/s |
| 2 核 | 42mm² | 52-66 | 50 tok/s | **7B: ~18 tok/s** |
| 4 核 | 69mm² | 104-132 | 100 tok/s | **13B: ~12 tok/s** |
| 8 核 | 122mm² | 208-264 | 200 tok/s | **30B: ~10 tok/s** |

### 5.6 `NUM_CORES=2` 流水线并行数据流

```
Time ────────────────────────────────────────────────────────────→

核₀: [Layer 0-15 的 MMUL+SFU] ──→ [FIFO 写激活值]
核₁:                              [FIFO 读] [Layer 16-31 的 MMUL+SFU]

DMA:  [Load W(0-15)→核₀ L1] [Load W(16-31)→核₁ L1]
```

- 权重各自加载到本地 L1，不抢占 L2 带宽
- KV Cache 按层分布到各核本地 SRAM——不需要全局 KV 池

### 5.7 软件侧适配

IREE HAL 后端感知多核：

```
iree_hal_npu_query_info() → {
    num_cores: 4,
    core_l1_kb: 256,
    shared_l2_kb: 4096,
    has_fifo: true
}
```

模型下发时由 Host Runtime 决定分区策略。应用层透明——只看到一个更强的 NPU。

---

## 六、开源复用矩阵

| 模块 | 参考源 | 复用方式 | 改动量 |
|------|--------|---------|:---:|
| **Systolic Array PE** | OpenTPU (Verilog) | 提取 PE 模块 | 小 |
| **阵列拓扑** | OpenTPU / TPUv1 | 256→128 参数化缩减 | 小 |
| **MAC 精度** | Gemmini (Chisel) | INT8→INT4 多精度设计参考 | 中 |
| **Unified Buffer** | TPUv1 | 双缓冲架构，容量缩小 | 小 |
| **Softmax+LN 融合** | 中科院 2024 论文 | 直接参考硬件方案 | 中 |
| **RISC-V 集成** | Coral NPU | AXI4 总线连接范式 | 小 |
| **AXI4 矩阵** | Coral NPU | 标准互联 | 低 |
| **指令集设计** | TPUv1 CISC ISA | 五类指令范式 | 小 |
| **PCIe EP** | 商用 IP | 采购 | 无 |
| **LPDDR5 控制器** | 商用 IP | 采购 | 无 |
| **DMA 引擎** | 业界参考设计 | 自研 | 中 |

---

## 七、关键学术/工业参考

| 论文/项目 | 来源 | 年份 | 参考价值 |
|------|------|:---:|------|
| **TPUv1 - In-Datacenter Performance Analysis** | Google, ISCA | 2017 | 架构基线 |
| **OpenTPU** | UCSB ArchLab | 2024 | Verilog 可直接读 |
| **Gemmini** | UC Berkeley | 2019+ | Systolic array 参数化设计方法论 |
| **Hardware-oriented Softmax+LayerNorm** | 中科院 | 2024 | SFU 融合架构 |
| **Calabash: Accelerating Attention on FPGA** | IEEE | 2023 | 双 systolic array 做 attention |
| **TPI-LLM** | OpenReview | 2025 | 多边缘设备跑 70B 模型 |
| **SCALE-Sim v3** | arXiv | 2025 | Cycle-accurate 性能模拟器 |
| **Coral NPU** | Google Research | 2025 | RISC-V+AXI 集成范式 |
| **腾讯 KV Cache 专利** | 腾讯 | 2025 | 内存碎片化问题的教训 |
| **HeadInfer** | arXiv | 2025 | KV cache 按 head 卸载策略 |
| **SystolicAttention** | arXiv | 2025 | Systolic array 上融合 FlashAttention |
| **A Survey on Hardware Accelerators for LLMs** | arXiv | 2024 | 全景参考 |

---

## 八、下一步：SCALE-Sim v3 性能建模

在 RTL 之前，用 SCALE-Sim v3 做 cycle-accurate 性能模拟：

1. 配置 128×128 weight-stationary systolic array
2. 注入 3B 模型的矩阵乘法 trace（从 HuggingFace 导出）
3. 验证 25 tok/s decode、0.37s prefill
4. 多 NPU 流水线并行建模

→ 模拟结果将反馈到 RTL 微架构参数调优。

---

> **文档版本**：v0.2 | **v0.2 变更**：NPU 核改为可参数化 IP；多核扩展从 PCIe P2P 改为片内实例化；新增加核间 FIFO、IP 参数化接口、三种工作模式、面积按核心数缩放表 | **下一步**：软件架构方案 + SCALE-Sim 建模
