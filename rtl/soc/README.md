# CaduceusCore SoC Top-Level — 全芯片集成

CaduceusCore SoC 顶层将 6 个 AXI4 master（Ibex RISC-V、MXU、SFU、Vector、DMA、PCIe）通过自研 crossbar 共享 4MB SRAM + 2GB DRAM，Ibex 同时通过 APB decoder 配置 7 个 MMIO slave。

**RTL Phase 3** — SoC Integration. 所有 IP 采用标准 AXI4/APB 接口，未来可无缝替换为商业 IP。

## 模块层次

```
caduceus_soc_top.v                    # SoC 顶层 (1272 行)
├── ibex_wrapper                       # Ibex RV32IMC (32-bit AXI4 + APB master)
│   ├── ibex_top (lowRISC, Apache 2.0)
│   └── boot_rom (64KB @ 0x0000_0000)
├── axi_adapter (32→512-bit)           # Ibex→crossbar 宽度适配 (verilog-axi, MIT)
├── axi_crossbar                       # M=6 / S=2, round-robin (578 行)
├── sram_ctrl                          # 4MB AXI4 slave @ 0x2000_0000 (S0)
├── dram_model                         # 2GB AXI4 slave @ 0x8000_0000 (S1)
├── apb_decoder                        # 1→7 APB decoder @ 0x4000_0000
│   ├── [0] mxu_soc_wrapper            # MXU: APB + AXI4 master (crossbar M1)
│   ├── [1] sfu_soc_wrapper            # SFU: APB + AXI4 master (crossbar M2)
│   ├── [2] vector_soc_wrapper         # Vector: APB + AXI4 master (crossbar M3)
│   ├── [3] dma_wrapper                # axi_cdma DMA: APB + AXI4 master (M4)
│   ├── [4] pcie_ep_wrapper            # PCIe EP: APB + AXI4 master (M5)
│   ├── [5] doorbell                   # Host↔NPU ring buffer doorbell
│   └── [6] intc_top                   # 7-source interrupt controller
└── intc_top                           # IRQ汇聚→cpu_irq→Ibex
```

## AXI4 Crossbar 拓扑

```
                    ┌─────────────┐
  Master 0: Ibex ───┤             │
  Master 1: MXU  ───┤   AXI4      ├── SRAM (0x2000_0000)  S0: 4MB, 512-bit
  Master 2: SFU  ───┤  Crossbar   │
  Master 3: Vec  ───┤  M=6, S=2   ├── DRAM (0x8000_0000)  S1: 2GB, 512-bit
  Master 4: DMA  ───┤  round-robin│
  Master 5: PCIe ───┤             │
                    └─────────────┘

  Address Route:
    addr[31:22]==0b0010000000 → S0 (SRAM, 0x2000_0000~0x203F_FFFF)
    addr[31]==1                → S1 (DRAM, 0x8000_0000~0xFFFF_FFFF)
    其他                         → DECERR
```

### 仲裁策略

- Per-slave round-robin（AW/W 和 AR/R 独立仲裁）
- 每 master 每方向至多 1 笔 outstanding
- AXI ID 保留：`s_axid = {master_sel[2:0], axi_id[5:0]}`（9-bit）

### 并发压力测试结果 (Task 7)

```
TC4: MXU + DMA + PCIe 同时访问 SRAM
  210 iterations, 11,455 cycles (≥10k ✓)
  1,260 transactions — 0 data errors
  CROSSBAR_STRESS: PASS
```

## 统一地址空间

| 区域 | 基地址 | 大小 | 用途 |
|------|--------|------|------|
| Boot ROM | `0x0000_0000` | 64 KB | Ibex 复位向量 + 固件 .text/.rodata |
| Ibex DMEM | `0x0001_0000` | 64 KB | 数据 RAM（栈 + .data/.bss） |
| SRAM | `0x2000_0000` | 4 MB | NPU 统一计算缓冲区 |
| MXU MMIO | `0x4000_0000` | 4 KB | MXU 寄存器 |
| SFU MMIO | `0x4000_1000` | 4 KB | SFU 寄存器 |
| VECTOR MMIO | `0x4000_2000` | 4 KB | Vector 寄存器 |
| DMA MMIO | `0x4000_3000` | 4 KB | DMA 寄存器 |
| PCIe MMIO | `0x4000_4000` | 4 KB | PCIe 寄存器 |
| DOORBELL | `0x4000_5000` | 4 KB | Host↔NPU doorbell |
| INTC MMIO | `0x4000_6000` | 4 KB | 中断控制器 |
| DRAM | `0x8000_0000` | 2 GB | DRAM 数据空间 |

> 以上来自 `.omo/plans/soc-phase3-4.md`，为 SoC 集成的单一事实来源。

## 中断路由

```
mxu_irq ─────────→ intc_top.bit0 ┐
sfu_irq ─────────→ intc_top.bit1 │
vec_irq ─────────→ intc_top.bit2 │
dma_irq ─────────→ intc_top.bit3 ├── cpu_irq → ibex_wrapper.cpu_irq_i
pcie_irq ────────→ intc_top.bit4 │
doorbell_irq ────→ intc_top.bit5 │  (host)
timer_irq_i ─────→ intc_top.bit6 ┘
```

INTC 寄存器（APB @ 0x4000_6000）：PENDING(RO) / ENABLE(RW) / THRESHOLD(RW) / ACK(W1C)
`cpu_irq = |(PENDING & ENABLE) when popcount(PENDING & ENABLE) ≥ THRESHOLD else 0`

## VCS 编译

### SoC 全芯片编译

```bash
# 环境: EDA server (sz0001, 192.168.0.11)
# module load vcs/vcs_2023.12sp2

vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
    -f rtl/cpu/ibex.flist \
    -f rtl/ip/verilog-axi.flist \
    -f rtl/ip/verilog-pcie.flist \
    -f rtl/soc/soc.flist \
    -top caduceus_soc_top \
    -o simv_soc_top \
    -l elaborate.log
```

> **注意**: `ibex.flist` 必须排在第一位（含 `ibex_pkg.sv`，`ibex_wrapper.v` 依赖其 scope 解析）。

### Filelist 结构

| Filelist | 内容 | 行数 |
|----------|------|:----:|
| `rtl/soc/soc.flist` | SoC 顶层 + 基础设施 + 引擎核心 + wrapper | 67 |
| `rtl/cpu/ibex.flist` | Ibex RISC-V core + prim_generic + ibex_wrapper | ~76 |
| `rtl/ip/verilog-axi.flist` | axi_cdma + axi_adapter + arbiter/priority_encoder | ~64 |
| `rtl/ip/verilog-pcie.flist` | pcie_axi_master + pcie_tlp_* modules | ~74 |

### 模块级回归

```bash
# 从 sim/regression/ 目录执行
cd sim/regression

make run_apb_smoke        # APB decoder 7-slave select
make run_intc_test        # INTC 7-source 中断控制器
make run_dma_test         # DMA wrapper 传输测试
make run_crossbar_stress  # AXI crossbar 并发压力 (≥10k cycles)
make run_pcie_test        # PCIe EP wrapper
make run_dram_test        # DRAM 行为模型 (100 random txns)
make run_soc_elab         # SoC 顶层 elaboration
make -j4 all              # 全部并行执行
```

## 回归状态

| 测试 | 结果 | 证据 |
|------|:----:|------|
| `check_mmio_map.py` | ✅ 49 regs match | 本任务 |
| `validate_interconnect.py` | ✅ PASS | 本任务 |
| APB smoke | ✅ PASS | Task 3 |
| INTC 7-source | ✅ 13/13 PASS | Task 6 |
| Crossbar stress (≥10k cycles) | ✅ 11,455 cycles, 0 err | Task 7 |
| DMA wrapper | ✅ ALL TESTS PASSED | Task 11 |
| SoC elaboration (47 modules) | ✅ 0 errors | Task 13 |
| `pytest` (210 tests) | ✅ 210/210 | 本任务 |

## 设计原则

1. **单一事实来源**: 地址映射、中断路由以 `.omo/plans/soc-phase3-4.md` 为准
2. **接口标准化**: 所有 IP 间通过 AXI4/APB 通信，替换商业 IP 仅需修改实例化
3. **引擎封装**: MXU/SFU/Vector wrapper 不改动引擎内部 RTL
4. **无 glob 编译**: 全部通过 filelist 而非 glob 引用源文件

## 参考

- 计划: `.omo/plans/soc-phase3-4.md`
- 交叉开关配置: `sim/config/interconnect.yaml`
- 地址验证: `sim/check_mmio_map.py`
- 回归 Makefile: `sim/regression/Makefile`
