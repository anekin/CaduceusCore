# NPU Func Model 架构文档

> 版本: v1.0 | 日期: 2026-06-20 | 状态: 全线贯通 (Spike + Python)

---

## 1. 顶层架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        Host CPU (x86/ARM)                         │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │              Python FuncModel / 真实驱动                      │ │
│  │  • host_write_command()    → Ring Buffer 注入命令            │ │
│  │  • host_write_descriptor() → DRAM 写入操作描述符              │ │
│  │  • host_write_data()       → DRAM 写入张量数据               │ │
│  │  • host_read_completion()  → Completion Ring 读回结果        │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                             │ PCIe / AXI                          │
└─────────────────────────────┼────────────────────────────────────┘
                              │
┌─────────────────────────────┼────────────────────────────────────┐
│                    NPU 芯片 (RISC-V + 加速器)                      │
│                             │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐ │
│  │                   DRAM (LPDDR5)                              │ │
│  │  • Ring Buffer      (命令队列，64 条目 × 32B)                 │ │
│  │  • Descriptor Pool  (操作描述符)                              │ │
│  │  • Tensor Data      (权重/激活/输出)                          │ │
│  │  • Completion Ring  (完成状态)                                │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                             │ AXI                                 │
│  ┌──────────────────────────▼──────────────────────────────────┐ │
│  │                   DMA Engine (双通道)                         │ │
│  │  CH0: DRAM → SRAM (权重/激活加载)                             │ │
│  │  CH1: SRAM → DRAM (结果写回)                                  │ │
│  └──────────────────────────┬──────────────────────────────────┘ │
│                             │                                      │
│  ┌──────────────────────────▼──────────────────────────────────┐ │
│  │               SRAM Unified Buffer (4 MB)                     │ │
│  │  共享工作区: 权重 | 激活 | 输出 | 中间结果                     │ │
│  └───────┬──────────────────┬──────────────────┬───────────────┘ │
│          │                  │                  │                  │
│  ┌───────▼──────┐  ┌────────▼───────┐  ┌──────▼──────────────┐  │
│  │     MXU      │  │      SFU       │  │   Vector Unit       │  │
│  │ 128×128      │  │ LUT/CORDIC     │  │ 128-wide SIMD       │  │
│  │ Systolic     │  │ Softmax/GELU   │  │ add/mul/reduce      │  │
│  │ INT4×INT8    │  │ RoPE/LayerNorm │  │ INT32↔BF16 bridge   │  │
│  │ →INT32       │  │ →BF16          │  │ →INT32/BF16         │  │
│  └──────────────┘  └────────────────┘  └─────────────────────┘  │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │            RISC-V 固件处理器 (Spike / 真实核)                 │  │
│  │  • 消费 Ring Buffer 命令                                      │  │
│  │  • 配置 MXU/SFU/Vector/DMA 寄存器 (MMIO)                      │  │
│  │  • 等待 DONE → 写 Completion Ring                             │  │
│  │  • 中断处理 (INTC)                                            │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │              MMIO Register Block (0x4000_0000)               │  │
│  │  MXU | SFU | Vector | DMA | Doorbell | INTC                 │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**两层验证路径：**
- **Python 路径**：`FuncModel` → `NPUFirmware` (Python 固件模拟器) → `MMIOBridge` → Golden Executor → bit-exact 验证
- **Spike 路径**：真实 RISC-V ELF + Spike 模拟器 + NPU MMIO 设备 → 硬件级验证

---

## 2. 内存映射

| 地址范围 | 大小 | 用途 |
|---------|------|------|
| `0x0000_1000` | 4 KB | Spike Boot ROM (内置) |
| `0x0200_0000` | 768 KB | CLINT (RISC-V 核间中断) |
| `0x0C00_0000` | 16 MB | PLIC (平台级中断控制器) |
| `0x1000_0000` | 256 B | NS16550 UART |
| `0x2000_0000` | 4 MB | NPU SRAM (Unified Buffer) |
| `0x4000_0000` | 72 KB | **NPU MMIO 寄存器块** |
| `0x8000_0000` | 256 MB | DRAM (固件代码 + 数据) |

### 2.1 NPU MMIO 寄存器块 (0x4000_0000)

| 偏移 | 模块 | 大小 | 关键寄存器 |
|------|------|------|-----------|
| `0x0000_0000` | MXU | 4 KB | CTRL, CMD, STATUS, DIM0/1, I/W/O_ADDR, IRQ_EN |
| `0x0000_1000` | SFU | 4 KB | CTRL, CMD, STATUS, I/O_ADDR, DIM, POS, IRQ_EN |
| `0x0000_2000` | Vector | 4 KB | CTRL, CMD, STATUS, A/B/O_ADDR, DIM, IRQ_EN |
| `0x0000_3000` | DMA | 4 KB | CMD, STATUS, CH0_SRC/DST/SIZE, CH1_SRC/DST/SIZE, IRQ_EN |
| `0x0001_0000` | Doorbell | 16 B | HOST_TAIL, NPU_HEAD, HOST_HEAD, NPU_TAIL |
| `0x0001_1000` | INTC | 16 B | PENDING, ENABLE, THRESHOLD, ACK |

---

## 3. 计算模块

### 3.1 MXU — 矩阵乘法单元

```
规格:
  阵列: 128×128 weight-stationary systolic array
  权重: INT4 (packed 2/byte, 范围 [-8, 7])
  激活: INT8 (范围 [-128, 127])
  累加: INT32 (带饱和截断)
  分块: 自动按 128×128 tile 执行

寄存器接口 (struct npu_mxu_t, 基址 0x4000_0000):
  Offset  | Name       | Description
  --------|------------|------------------------------------
  0x00    | CTRL       | [1:0] dtype 选择
  0x04    | CMD        | bit0=START (写1启动)
  0x08    | STATUS     | bit0=BUSY, bit1=DONE (硬件置位)
  0x0C    | DIM0       | [15:0]=M, [31:16]=K
  0x10    | DIM1       | [15:0]=N
  0x14    | I_ADDR     | 激活 SRAM 地址
  0x18    | W_ADDR     | 权重 SRAM 地址 (packed INT4)
  0x1C    | O_ADDR     | 输出 SRAM 地址 (INT32)
  0x20    | BIAS_ADDR  | 偏置地址 (0=无)
  0x24    | SCALE_ADDR | 缩放地址 (0=无)
  0x28    | IRQ_EN     | bit0=完成中断使能

操作流程:
  1. DMA 将 INT8 激活、packed INT4 权重搬入 SRAM
  2. FW 写 I_ADDR/W_ADDR/O_ADDR, DIM0/1
  3. FW 写 CMD=1 → 硬件: STATUS.BUSY=1
  4. 硬件: 分块执行 INT4×INT8→INT32, 写结果到 O_ADDR
  5. 硬件: STATUS.BUSY=0, STATUS.DONE=1
  6. 若 IRQ_EN=1: 触发 INTC 中断

Golden 实现:
  GoldenMXU.matmul_int32(act, wgt_packed, M, K, N) → (M,N) INT32 array
  GoldenMXU.matmul_from_sram(M,K,N, act_sram, wgt_sram, sram) → (M,N) INT32
```

### 3.2 SFU — 特殊函数单元

```
规格:
  实现: LUT + CORDIC, BF16 精度
  Softmax: 256-entry exp LUT, 8-stage pipeline
  GELU: 4-segment piecewise linear, 4-stage
  SiLU: 复用 exp LUT
  LayerNorm: 6-stage, BF16 中间精度
  RoPE: 12-stage CORDIC 旋转

寄存器接口 (struct npu_sfu_t, 基址 0x4000_1000):
  Offset  | Name   | Description
  --------|--------|------------------------------------
  0x00    | CTRL   | [3:0]=OP (0=SOFTMAX,1=LAYERNORM,2=GELU,3=RELU,4=SILU,5=ROPE)
  0x04    | CMD    | bit0=START
  0x08    | STATUS | bit0=BUSY, bit1=DONE
  0x0C    | I_ADDR | 输入 SRAM 地址 (BF16)
  0x10    | O_ADDR | 输出 SRAM 地址 (BF16)
  0x14    | DIM    | [15:0]=元素数
  0x18    | POS    | 位置 (仅 RoPE)
  0x1C    | IRQ_EN | bit0=完成中断使能

Golden 实现:
  GoldenSFU.softmax_hw(x)  → BF16 (256-entry LUT)
  GoldenSFU.gelu_hw(x)     → BF16 (64-entry LUT)
  GoldenSFU.silu_hw(x)     → BF16 (复用 exp LUT)
  GoldenSFU.layernorm_hw(x) → BF16 (6-stage)
  GoldenSFU.rope_hw(q, k, pos) → BF16 (12-stage CORDIC)
  每个函数都有 .*_ref() 实现 (float64) 用于精度对比
```

### 3.3 Vector Unit — 向量处理单元

```
规格:
  宽度: 128-wide SIMD
  类型: INT32 / BF16
  操作: element-wise add/mul, tree reduction, type conversion

寄存器接口 (struct npu_vector_t, 基址 0x4000_2000):
  Offset  | Name   | Description
  --------|--------|------------------------------------
  0x00    | CTRL   | [3:0]=OP (0=ADD,1=MUL,2=MAX,3=SUM,4=CONV,5=RESID)
  0x04    | CMD    | bit0=START
  0x08    | STATUS | bit0=BUSY, bit1=DONE
  0x0C    | A_ADDR | 操作数 A SRAM 地址
  0x10    | B_ADDR | 操作数 B SRAM 地址 (reduction 为 0)
  0x14    | O_ADDR | 输出 SRAM 地址
  0x18    | DIM    | [15:0]=元素数
  0x1C    | IRQ_EN | bit0=完成中断使能

关键操作:
  VEC_OP_CONV (4): INT32 → BF16 类型桥接 (MXU 输出 → SFU 输入)
  VEC_OP_RESID (5): Residual Add (INT32 累加 BF16 残差)

Golden 实现:
  GoldenVector.add(a,b), .mul(a,b)       → INT32
  GoldenVector.max_reduce(x), .sum_reduce(x)
  GoldenVector.conv_i32_to_f16(arr)      → BF16
  GoldenVector.residual_add(orig, delta) → INT32
```

### 3.4 DMA Engine — 数据搬运引擎

```
规格:
  通道: 双通道 (CH0=DRAM→SRAM load, CH1=SRAM→DRAM store)
  描述符: 16-entry queue, 每项 64-bit
  带宽: LPDDR5-6400 51.2 GB/s (有效 ~43.5 GB/s at 85% efficiency)

描述符格式 (64-bit):
  [63:32] dram_addr  — DRAM 地址
  [31:16] sram_addr  — SRAM 地址 (2MB 范围)
  [15:4]  size       — 传输字节 (0=4096)
  [3]     direction  — 0=load(DRAM→SRAM), 1=store(SRAM→DRAM)
  [2]     last       — 链尾标记
  [1:0]   channel    — 0=weight, 1=data

寄存器接口 (struct npu_dma_t, 基址 0x4000_3000):
  Offset  | Name       | Description
  --------|------------|------------------------------------
  0x00    | CTRL       | 控制
  0x04    | CMD        | bit0=START
  0x08    | STATUS     | bit0=BUSY, bit1=DONE
  0x10    | CH0_SRC    | CH0 DRAM 源地址
  0x14    | CH0_DST    | CH0 SRAM 目标地址
  0x18    | CH0_SIZE   | CH0 传输字节
  0x1C    | CH0_STRIDE | CH0 2D stride
  0x20    | CH1_SRC    | CH1 SRAM 源地址
  0x24    | CH1_DST    | CH1 DRAM 目标地址
  0x28    | CH1_SIZE   | CH1 传输字节
  0x2C    | CH1_STRIDE | CH1 2D stride
  0x30    | DESC_ADDR  | 描述符链地址
  0x34    | DESC_CNT   | 描述符数量
  0x38    | IRQ_EN     | bit0=完成中断使能

Golden 实现:
  GoldenDMA.estimate_descriptor(desc) → cycles
  GoldenDMA.estimate_chain(descriptors) → total cycles
  GoldenDMA.execute_load(sram, desc, dram)
  GoldenDMA.execute_store(sram, desc, dram)
```

### 3.5 INTC — 中断控制器

```
寄存器接口 (struct npu_intc_t, 基址 0x4001_1000):
  Offset  | Name      | Access | Description
  --------|-----------|--------|------------------------------------
  0x00    | PENDING   | R      | 中断 pending 位 [MXU:0, SFU:1, VEC:2, DMA:3, HOST:8]
  0x04    | ENABLE    | R/W    | 中断使能掩码 (默认 0xFF)
  0x08    | THRESHOLD | R/W    | 优先级阈值
  0x0C    | ACK       | W      | 写1清除对应 pending 位

中断源:
  INTC_MXU    = bit 0
  INTC_SFU    = bit 1
  INTC_VECTOR = bit 2
  INTC_DMA    = bit 3
  INTC_HOST   = bit 8
```

---

## 4. 固件接口

### 4.1 Ring Buffer 协议 (Host ↔ NPU)

```
Ring Buffer (位于 DRAM_BASE = 0x8010_0000):
  条目数: 64
  条目大小: 32 bytes

命令条目结构 (cmd_entry_t, 32B packed):
  Offset | Field     | Type   | Description
  -------|-----------|--------|------------------------------
  0x00   | opcode    | u32    | 0=MMUL, 1=SFU, 2=VECTOR, 3=DMA_COPY
  0x04   | desc_addr | u32    | 操作描述符的 DRAM 地址
  0x08   | flags     | u32    | bit0=中断完成, bit1=立即执行
  0x0C   | _pad      | 20B    | 对齐

Completion Ring (紧接 Ring Buffer 之后):
  偏移: RING_BUF_ADDR + 64 × 32B
  条目大小: 32 bytes

完成条目结构 (completion_t):
  Offset | Field  | Type | Description
  -------|--------|------|------------------------------
  0x00   | cmd_id | u32  | 对应命令的 ring index
  0x04   | status | u32  | 0=success, non-zero=error
```

### 4.2 Doorbell 寄存器 (0x4001_0000)

```
  Offset | Name      | Access | Description
  -------|-----------|--------|------------------------------
  0x00   | HOST_TAIL | W      | Host 写完命令后更新 tail
  0x04   | NPU_HEAD  | R/W    | NPU 固件消费指针
  0x08   | HOST_HEAD | R      | Host completion ring 读指针
  0x0C   | NPU_TAIL  | R      | Host submission ring 写指针
```

### 4.3 操作描述符

**MMUL 描述符 (mmul_desc_t, 12×u32 = 48B):**
```
  Offset | Field        | Description
  -------|--------------|------------------------------
  0x00   | input_addr   | 激活 DRAM 地址
  0x04   | weight_addr  | 权重 DRAM 地址
  0x08   | output_addr  | 输出 DRAM 地址
  0x0C   | input_sram   | 激活 SRAM 地址
  0x10   | weight_sram  | 权重 SRAM 地址
  0x14   | output_sram  | 输出 SRAM 地址
  0x18   | input_size   | 激活字节数
  0x1C   | weight_size  | 权重字节数
  0x20   | output_size  | 输出字节数
  0x24   | M            | 行数
  0x28   | K            | 内维
  0x2C   | N            | 列数
```

**SFU 描述符 (sfu_desc_t, 8×u32 = 32B):**
```
  Offset | Field       | Description
  -------|-------------|------------------------------
  0x00   | op          | SFU_OP_SOFTMAX/LAYERNORM/GELU/RELU/SILU/ROPE
  0x04   | input_addr  | 输入 DRAM 地址
  0x08   | output_addr | 输出 DRAM 地址
  0x0C   | input_sram  | 输入 SRAM 地址
  0x10   | output_sram | 输出 SRAM 地址
  0x14   | size        | 数据字节数
  0x18   | dim         | head_dim (ROPE) 或元素数
  0x1C   | pos         | 位置 (ROPE)
```

### 4.4 固件执行流程

```
firmware_main():
  1. 初始化 Doorbell: NPU_HEAD=0, HOST_TAIL=1 (自触发测试)
  2. 使能全部中断: INTC.ENABLE = 0xFF
  3. 主循环:
     loop:
       host_tail = DB.HOST_TAIL
       npu_head  = DB.NPU_HEAD
       if host_tail == npu_head:
         WFI              # 等待中断/新命令
         continue
       while npu_head != host_tail:
         cmd = read_cmd_entry(npu_head)     # 从 Ring Buffer 读命令
         dispatch_cmd(&cmd):
           case MMUL:
             read_mmul_desc(cmd.desc_addr)  # 读描述符
             dma_copy(weight_dram → weight_sram)   # DMA CH0
             dma_copy(activation_dram → act_sram)   # DMA CH0
             mxu_start(M, K, N)                    # MXU 计算
             dma_copy(output_sram → output_dram)    # DMA CH1
           case SFU:
             read_sfu_desc(...)
             dma_copy(input_dram → input_sram)
             sfu_start(op, dim, pos)
             dma_copy(output_sram → output_dram)
         write_completion(npu_head, status)  # 写 Completion Ring
         npu_head = (npu_head + 1) % 64
       DB.NPU_HEAD = npu_head               # 更新消费指针
```

---

## 5. Spike 集成

### 5.1 设备模型 (npu_device.cc)

```
位置: spike_src/riscv/npu_device.cc
注册: REGISTER_BUILTIN_DEVICE(npu, npu_parse_from_fdt, npu_generate_dts)
       + sim.cc 中 extern device_factory_t* npu_factory
       + device_factories 列表添加 {npu_factory, {}}

接口: 实现 abstract_device_t
  load(addr, len, bytes)  → 读 MMIO 寄存器
  store(addr, len, bytes) → 写 MMIO 寄存器
  size()                  → 返回 0x12000 (72KB)

注意: bus_t 调用时传入 相对地址 (已减基址 0x4000_0000)

模块行为 (零延迟模型):
  MXU CMD:  写 CMD=1 → STATUS 立即 BUSY→DONE, 若 IRQ_EN 则设中断
  SFU CMD:  同上
  VEC CMD:  同上
  DMA CMD:  同上
  INTC ACK: 写 ACK → 清除 PENDING 对应位
  Doorbell: 直通读写
```

### 5.2 构建流程

```bash
# 1. 修改 spike_src/riscv/riscv.mk.in: riscv_srcs 添加 npu_device.cc
# 2. 修改 spike_src/riscv/sim.cc: extern + factory 列表
# 3. 编译
cd spike_src && ./configure --prefix=$HOME/tools/spike && make -j8
cp spike ~/tools/spike/bin/

# 4. 运行
spike --isa=RV32IM \
  -m0x80000000:0x10000000,0x20000000:0x00400000 \
  firmware/build/npu_firmware.elf
```

---

## 6. Python Func Model (func_model.py)

### 6.1 类层次

```
FuncModel (顶层)
  ├── dram: bytearray          # DRAM 模型 (可配置大小)
  ├── sram: bytearray          # SRAM 模型 (4MB)
  ├── mxu: GoldenMXU           # MXU 金模型
  ├── sfu: GoldenSFU           # SFU 金模型
  ├── vector: GoldenVector     # Vector 金模型
  ├── dma_engine: GoldenDMA    # DMA 引擎模型
  ├── bridge: MMIOBridge       # MMIO 地址路由
  └── firmware: NPUFirmware    # Python 固件模拟器

MMIOBridge
  路由 MMIO 地址到对应模块
  read(addr) / write(addr, value)
  模块名 → 基址映射: mxu/sfu/vector/dma/doorbell/intc

NPUFirmware (Python 版固件)
  与 C 固件行为一致:
  - doorbell state dict
  - ring_buffer_addr, ring_size
  - run_loop(max_commands) → 消费命令队列

AXITracer
  记录所有 MMIO 事务 → JSON
  用于 RTL 对比验证
```

### 6.2 Host 侧 API

```python
model = FuncModel()

# 准备数据
model.host_write_data(addr=0x80010000, data=activation)   # 写激活到 DRAM
model.host_write_data(addr=0x80020000, data=weights)       # 写权重到 DRAM

# 写描述符
model.host_write_descriptor(desc_addr=0x80101000,
    input_addr=0x80010000, weight_addr=0x80020000,
    output_addr=0x80030000, M=4, K=8, N=4)

# 注入命令
model.host_write_command(opcode=0, desc_addr=0x80101000, flags=0)

# 执行
results = model.run()  # 固件消费命令, 返回执行结果
```

---

## 7. 数据流示例 (MMUL 全链路)

```
Host                          DRAM                  SRAM            NPU Modules
  │                             │                     │                │
  ├─ host_write_data() ────────►│ activation @0x8001  │                │
  ├─ host_write_data() ────────►│ weights @0x8002     │                │
  ├─ host_write_descriptor() ──►│ desc @0x8010_1000   │                │
  ├─ host_write_command() ─────►│ RingBuf[0] = MMUL   │                │
  ├─ DB.HOST_TAIL = 1           │                     │                │
  │                             │                     │                │
  │    ╔══════════ NPU 固件 ═══════════════════════════════════════╗  │
  │    ║ DB read → host_tail(1) != npu_head(0)                    ║  │
  │    ║ read_cmd_entry(0) → opcode=MMUL, desc=0x8010_1000        ║  │
  │    ║ read_mmul_desc() → M/K/N, addrs                          ║  │
  │    ║                                                          ║  │
  │    ║ DMA CH0: DRAM→SRAM ──────►│ weight @0x8002──┐            ║  │
  │    ║                           │                ├──────────► wgt_buf  ║
  │    ║ DMA CH0: DRAM→SRAM ──────►│ act @0x8001─────┼──────────► act_buf  ║
  │    ║                           │                │           │       ║  │
  │    ║ MXU CMD=1 ──────────────────────────────────┼──────────► MXU    ║  │
  │    ║                           │                │   INT4×INT8→INT32  ║  │
  │    ║ MXU DONE ←──────────────────────────────────┼───────────┤       ║  │
  │    ║                           │                │           │       ║  │
  │    ║ DMA CH1: SRAM→DRAM ◄──────┤ out @0x8003◄────┼───────────┘       ║  │
  │    ║                           │                │                    ║  │
  │    ║ write_completion(0, OK) ─►│ CompRing[0]=OK │                    ║  │
  │    ║ DB.NPU_HEAD = 1           │                │                    ║  │
  │    ╚══════════════════════════════════════════════════════════════════╝  │
  │                             │                     │                │
  ◄─ host_read_completion() ────┤ CompRing[0] = OK    │                │
  ◄─ host_read_data(0x8003) ────┤ output tensor       │                │
```

---

## 8. 关键文件清单

```
npu/
├── firmware/
│   ├── npu-regmap.h         # 寄存器映射 (与 regmap.py 同步)
│   ├── npu_firmware.c       # RISC-V 固件 (Ring Buffer 消费)
│   ├── startup.S            # 启动代码 (BSS清零, 跳转main)
│   ├── link.ld              # 链接脚本 (SRAM+DRAM 布局)
│   ├── test_data.S          # 预加载测试数据 (256KB)
│   └── Makefile             # 交叉编译 (riscv-none-elf-gcc)
│
├── sim/
│   ├── regmap.py            # 寄存器地址定义 (Python)
│   ├── func_model.py        # 顶层 Func Model (Python)
│   ├── mmio_bridge.py       # MMIO 地址路由
│   ├── miniv.py             # Python 固件模拟器
│   ├── golden_executor.py   # MXU/SFU/Vector/DMA 金模型
│   ├── axi_tracer.py        # AXI 事务追踪
│   ├── npu_device.cc        # Spike MMIO 设备 (C++)
│   ├── npu_device.cpp       # Spike extlib 版本 (已弃用)
│   └── models/
│       ├── mxu.py           # MXU 行为/时序模型
│       ├── sfu.py           # SFU 行为/时序模型
│       ├── vector.py        # Vector 行为/时序模型
│       ├── dma.py           # DMA 行为/时序模型
│       ├── dram.py          # LPDDR5 时序模型
│       └── golden.py        # 金模型封装
│
├── spike_src/               # Spike RISC-V 模拟器源码 (已修改)
│   ├── riscv/
│   │   ├── npu_device.cc    # ← 插入的设备
│   │   ├── riscv.mk.in      # ← riscv_srcs 添加 npu_device.cc
│   │   └── sim.cc           # ← extern + factory 注册
│   └── spike_main/
│       └── spike_main.mk.in
│
└── ggml-npu/                # NPU backend for llama.cpp (独立项目)
    ├── npu_server.py
    ├── q4_dequant.py
    └── verify_hex.py
```

---

## 9. 自测与验证

| 测试层面 | 方式 | 状态 |
|---------|------|------|
| Python bit-exact | `FuncModel.run()` + 输出 hash 对比 | ✅ |
| Spike 裸跑固件 | `spike firmware.elf` → Doorbell 读写 | ✅ |
| Spike 命令消费 | 完整 MMUL 流程: DMA→MXU→Completion | ✅ |
| RTL 对比 (计划) | axi_tracer JSON → RTL testbench | ⏳ |
| 真实硅前验证 (计划) | FPGA 原型 | ⏳ |
