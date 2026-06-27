# CaduceusCore IP Wrappers — 开源 IP 集成

SoC Phase 3-4 集成的外部开源 IP 及其封装。所有 IP 通过标准 AXI4/APB 接口连接，可无缝替换为商业 IP。

## IP 选型与替换策略

| 组件 | 开源选择 | License | 商业替换方案 | 接口兼容性 |
|------|---------|---------|-------------|:----------:|
| **DMA** | `axi_cdma` (alexforencich/verilog-axi) | MIT | Synopsys DW_axi_dmac | 均为 AXI4 master + APB slave |
| **DRAM** | 自研行为模型 (简化自 LiteDRAM) | CaduceusCore | Synopsys uMCTL2 | 均为 AXI4 slave |
| **PCIe** | `pcie_axi_master` (alexforencich/verilog-pcie) | MIT | Synopsys DWC PCIe EP | 均为 AXI4 master + PCIe→AXI bridge |
| **NoC** | 自研 AXI crossbar | CaduceusCore | Arteris FlexNoC | 均为 AXI4 crossbar, port 对齐即可替换 |
| **RISC-V** | Ibex (lowRISC) | Apache 2.0 | — | 已满足需求 |

> **替换原则**: 所有 IP 之间通过标准 AXI4/APB 接口通信。替换时仅需修改 `caduceus_soc_top.v` 中的模块实例化，无需改动互联逻辑（地址映射、中断路由保持不变）。

## IP Wrapper 详细说明

### 1. dma_wrapper — axi_cdma DMA Engine

| 属性 | 值 |
|------|-----|
| 文件 | `rtl/ip/dma_wrapper.v` (~441 行) |
| 上游 IP | `axi_cdma` from [alexforencich/verilog-axi](https://github.com/alexforencich/verilog-axi) (MIT) |
| 实例化 | `axi_cdma` (Central DMA engine) |
| APB 地址 | `0x4000_3000` (4 KB) |
| AXI4 接口 | 512-bit master, max burst=16 → crossbar M4 |
| 中断 | `dma_irq` → INTC bit 3 |

**寄存器映射** (兼容 `npu_dma_t`):

| 偏移 | 名称 | 访问 | 描述 |
|:----:|------|:----:|------|
| 0x00 | CTRL | RW | [0]=linked_list_en |
| 0x04 | CMD | W | bit[0]=START, bit[1]=ABORT |
| 0x08 | STATUS | R | bit[0]=BUSY, bit[1]=DONE |
| 0x10 | CH0_SRC | RW | DRAM 源地址 |
| 0x14 | CH0_DST | RW | SRAM 目的地址 |
| 0x18 | CH0_SIZE | RW | 传输字节数 |
| 0x20 | CH1_SRC | RW | SRAM 源地址 |
| 0x24 | CH1_DST | RW | DRAM 目的地址 |
| 0x28 | CH1_SIZE | RW | 传输字节数 |
| 0x38 | IRQ_EN | RW | bit[0]=传输完成中断使能 |

**描述符格式**: wrapper 将 firmware 的 CH0/CH1 寄存器自动翻译为 axi_cdma 流式描述符 (SRC_ADDR, DST_ADDR, LEN, TAG)。

**接口**:
```
APB slave (psel/penable/paddr/pwrite/pwdata/prdata/pready/pslverr)
AXI4 master (awid/awaddr/awlen/awsize/awburst → wdata/wstrb/wlast → bid/bresp
              arid/araddr/arlen/arsize/arburst → rid/rdata/rresp/rlast)
IRQ output (dma_irq)
```

**上游许可**: `rtl/ip/verilog-axi/LICENSE` (MIT). 源码未修改.

---

### 2. pcie_ep_wrapper — PCIe Endpoint

| 属性 | 值 |
|------|-----|
| 文件 | `rtl/ip/pcie_ep_wrapper.v` (~500 行) |
| 上游 IP | `pcie_axi_master` from [alexforencich/verilog-pcie](https://github.com/alexforencich/verilog-pcie) (MIT) |
| APB 地址 | `0x4000_4000` (4 KB) |
| AXI4 接口 | 512-bit master → crossbar M5 |
| 中断 | `pcie_irq` → INTC bit 4 |

**功能**:
- PCIe→AXI4 bridge (pcie_axi_master): Host CPU TLP ↔ AXI4 读写
- BAR0→SRAM (0x2000_0000, 4MB), BAR1→DRAM (0x8000_0000, 2GB)
- MSI-X 中断支持 → `pcie_irq` → INTC
- Cocotb 验证: 使用 `cocotbext-pcie` Python host model (无需真 PCIe PHY)

**TLP 端口** (暴露到 SoC 顶层):
```
rx_req_tlp_data/hdr/valid/sop/eop/ready  (Host→NPU 写请求)
tx_cpl_tlp_data/strb/hdr/valid/sop/eop/ready  (NPU→Host 读完成)
```

**接口**:
```
TLP ports  (PCIe Transaction Layer Packet, 512-bit data + 128-bit header)
AXI4 master (512-bit, 5-channel, to crossbar M5)
APB slave   (BAR mapping, MSI-X control)
IRQ output  (pcie_irq)
```

**上游许可**: `rtl/ip/verilog-pcie/LICENSE` (MIT). 源码未修改.

---

### 3. dram_model — DRAM 行为模型

| 属性 | 值 |
|------|-----|
| 文件 | `rtl/ip/dram_model.v` (~360 行) |
| 设计 | 自研简化行为模型 (基于 LiteDRAM 概念) |
| License | CaduceusCore |
| APB 地址 | `0x8000_0000` (2 GB addressable) |
| AXI4 接口 | 512-bit slave ← crossbar S1 |

**实现**:
- `reg [511:0] mem [0:131071]` — capped 8MB 稀疏实现 (超出→DECERR)
- 模拟 DDR 延迟: 固定 tRC=48ns (48 cycles @1GHz), 可编程延迟计数器
- `$readmemh("dram_init.hex")` 支持仿真初始化
- 支持 AXI4 INCR burst, back-to-back 读写

**接口**:
```
AXI4 slave (awid/awaddr/awlen/awsize/awburst → wready/wdata/wstrb/wlast
            → bid/bresp/bvalid  ar→rid/rdata/rresp/rlast/rvalid) 
Clock/Reset (clk, rst_n)
```

**商业替换**: 替换为 Synopsys uMCTL2 时，只需将 `dram_model` 实例换为 uMCTL2 AXI slave wrapper，地址映射保持不变。

---

### 4. doorbell — Host↔NPU Ring Buffer Doorbell

| 属性 | 值 |
|------|-----|
| 文件 | `rtl/soc/doorbell.v` (113 行) |
| 设计 | 自研 |
| License | CaduceusCore |
| APB 地址 | `0x4000_5000` (4 KB) |
| 中断 | `doorbell_irq` → INTC bit 5 (host) |

**寄存器**:

| 偏移 | 名称 | 访问 | 描述 |
|:----:|------|:----:|------|
| 0x00 | HOST_TAIL | RW | Host 写入新 command tail |
| 0x04 | NPU_HEAD | RW | NPU firmware 已消费 head |
| 0x08 | HOST_HEAD | RW | Host completion ring head |
| 0x0C | NPU_TAIL | RW | NPU completion tail |

**中断协议**: `doorbell_irq = (HOST_TAIL != NPU_HEAD)`
- Host 写入 HOST_TAIL ≠ NPU_HEAD → doorbell_irq=1 → INTC → firmware 轮询
- Firmware 写入 NPU_HEAD=HOST_TAIL → doorbell_irq=0 (自动清除)
- 兼容 `npu_firmware.c` main loop 轮询逻辑

**接口**:
```
APB slave (psel/penable/paddr/pwrite/pwdata/prdata/pready/pslverr)
IRQ output (doorbell_irq)
```

---

## 第三方 IP License 合规

所有 vendored 第三方 IP 保留原始 LICENSE 文件，源码无修改:

| IP | 路径 | License 文件 |
|----|------|-------------|
| Ibex (lowRISC) | `rtl/cpu/ibex/` | `LICENSE` (Apache 2.0) |
| verilog-axi (alexforencich) | `rtl/ip/verilog-axi/` | `LICENSE` (MIT) |
| verilog-pcie (alexforencich) | `rtl/ip/verilog-pcie/` | `LICENSE` (MIT) |

CaduceusCore 自研模块 (MXU/SFU/Vector kernel, SoC infrastructure, IP wrappers) 使用 MIT License.

## VCS 编译

### IP 模块独立编译

```bash
# DMA wrapper (需 verilog-axi.flist)
vcs -full64 -sverilog -f rtl/ip/verilog-axi.flist \
    rtl/ip/dma_wrapper.v -top dma_wrapper

# PCIe EP wrapper (需 verilog-pcie.flist + verilog-axi.flist)
vcs -full64 -sverilog -f rtl/ip/verilog-axi.flist \
    -f rtl/ip/verilog-pcie.flist \
    rtl/ip/pcie_ep_wrapper.v -top pcie_ep_wrapper

# DRAM model (独立编译)
vcs -full64 -sverilog rtl/ip/dram_model.v -top dram_model
```

### 全芯片 SoC 编译

```bash
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
    -f rtl/cpu/ibex.flist \
    -f rtl/ip/verilog-axi.flist \
    -f rtl/ip/verilog-pcie.flist \
    -f rtl/soc/soc.flist \
    -top caduceus_soc_top -o simv_soc_top
```

## 商业 IP 替换指南

当需要替换为商业 IP 时，修改 `caduceus_soc_top.v` 中的对应实例化:

### DMA: axi_cdma → DW_axi_dmac

```verilog
// 替换 dma_wrapper 内部:
// axi_cdma u_axi_cdma (...)  →  DW_axi_dmac u_dmac (...)
// 接口相同: AXI4 master (data) + APB slave (regs)
// 寄存器映射可能需要适配 wrapper 的 descriptor 翻译 FSM
```

### NoC: axi_crossbar → FlexNoC

```verilog
// 替换 axi_crossbar 实例:
// axi_crossbar u_axi_crossbar (...)  →  flexnoc_wrapper u_flexnoc (...)
// 接口相同: AXI4 M×S crossbar
// 地址路由从 `sim/config/interconnect.yaml` 自动生成
```

### DRAM: dram_model → uMCTL2

```verilog
// 替换 dram_model 实例:
// dram_model u_dram_model (...)  →  umctl2_axi_wrapper u_dram (...)
// 接口相同: AXI4 slave
// 地址 0x8000_0000 不变
```

### PCIe: pcie_axi_master → DWC PCIe EP

```verilog
// 替换 pcie_ep_wrapper 内部:
// pcie_axi_master → DWC_pcie_ep
// AXI4 master 接口兼容，TLP 层协议细节封装在 wrapper 内
```

## 参考

- 计划: `.omo/plans/soc-phase3-4.md` (IP 选型与替换策略)
- vendored IP: `rtl/cpu/ibex/`, `rtl/ip/verilog-axi/`, `rtl/ip/verilog-pcie/`
- Crossbar 配置: `sim/config/interconnect.yaml`
- 回归 Makefile: `sim/regression/Makefile`
