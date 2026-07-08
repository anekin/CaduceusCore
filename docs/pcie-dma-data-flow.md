# PCIe DMA 数据流全景 — Host CPU → RISC-V → PCIe DMA

> **日期**: 2026-07-06
> **分支**: feat_pcie
> **关联计划**: .omo/plans/pcie-dma-implementation.md

---

## 1. 描述符存放位置

所有描述符和命令都存放在 **NPU 侧 DRAM**（`0x8000_0000` 起始），host CPU 通过 **PCIe BAR1 直写** 写入，RISC-V firmware 从 DRAM 读出。

### 数据结构

```c
// 命令入口 — host 写在 ring buffer 里 (DRAM @ 0x8000_0000)
typedef struct __attribute__((packed)) {
    uint32_t opcode;       // 0=MMUL, 1-4=SFU, 5=ROPE, 6=SiLU, 7=PCIe_DMA
    uint32_t desc_addr;    // 指向详细描述符的 DRAM 地址
    uint32_t flags;        // bit0=完成中断, bit1=立即执行
    uint32_t _pad[5];      // 对齐到 32 字节
} cmd_entry_t;

// PCIe DMA 详细描述符 — host 写在 DRAM 任意地址
typedef struct __attribute__((packed)) {
    uint32_t pcie_addr_lo;   // host 物理地址 [31:0]
    uint32_t pcie_addr_hi;   // host 物理地址 [63:32]
    uint32_t axi_addr;        // NPU 本地 SRAM/DRAM 地址
    uint32_t len;             // 传输字节数 (max 4096)
    uint32_t direction;       // 0=host→NPU (PCIe read), 1=NPU→host (PCIe write)
    uint32_t _pad[1];
} pcie_dma_desc_t;
```

### 地址布局

```
NPU DRAM (0x8000_0000)
├── 0x8000_0000: Ring Buffer (1024 entries × 32B = 32KB)
│   ├── entry[0]: cmd_entry_t (opcode + desc_addr + flags)
│   ├── entry[1]: ...
│   └── entry[N]: ...
├── 0x8000_8000: Completion Ring (1024 entries × 32B = 32KB)
│   ├── entry[0]: completion_t (cmd_id + status)
│   └── ...
└── 0x8001_0000+: 描述符数据区（任意地址）
    ├── pcie_dma_desc_t (host 写入)
    ├── mmul_desc_t ...
    └── ...
```

---

## 2. 完整数据流（host→NPU 方向的 PCIe DMA，以 prompt 加载为例）

```
  HOST CPU                         NPU DRAM (0x8000_0000)           RISC-V FIRMWARE
  ────────                         ──────────────────────           ─────────────────

  ① 准备描述符                                    │                       │
     host driver 构造 pcie_dma_desc_t:             │                       │
       pcie_addr = 0x3_0000_0000  (host 物理内存)    │                       │
       axi_addr  = 0x2000_1000     (NPU SRAM)       │                       │
       len       = 4096                              │                       │
       direction = 0                                 │                       │

  ② PCIe BAR1 MWr ──────────────────────────►       │                       │
     写描述符到 NPU DRAM 任意位置                    │                       │
     例如: 0x8000_1000                               ▼                       │
                                            ┌──────────────────┐               │
                                            │ pcie_addr=0x3_... │               │
                                            │ axi_addr=0x20001000│              │
                                            │ len=4096 dir=0    │               │
                                            └──────────────────┘               │

  ③ PCIe BAR1 MWr ──────────────────────────►       │                       │
     写 cmd_entry_t 到 ring buffer                   ▼                       │
     RING_BUF_ADDR + 7×32 = 0x8000_00E0      ┌──────────────────┐               │
                                            │ opcode=7          │               │
                                            │ desc_addr=0x80001000│             │
                                            │ flags=0           │               │
                                            └──────────────────┘               │

  ④ PCIe BAR0 MWr ──────────────────────────────►   │                       │
     APB @ 0x4000_5000 (doorbell)                    │                       │
     HOST_TAIL = 7                                    │                       │
     HOST_TAIL ≠ NPU_HEAD → doorbell_irq = 1          │        IRQ ───────────►│
                                                      │                       │
                                                      │  ⑤ firmware ISR       │
                                                      │     读 ring[NPU_HEAD]  │
                                                      │     读 DRAM 0x8000_00E0│
                                                      │     opcode=7           │
                                                      │                       │
                                                      │  ⑥ dispatch_cmd()     │
                                                      │     case 7:            │
                                                      │     pcie_dma_exec(     │
                                                      │       desc_addr=       │
                                                      │       0x8000_1000)     │
                                                      │     → 从 DRAM 读描述符  │
                                                      │       得到 pcie_addr/   │
                                                      │       axi_addr/len/dir  │
                                                      │                       │
                                                      │  ⑦ 写 APB @ 0x40007000│
                                                      │     PCIE_ADDR_LO/HI    │
                                                      │     AXI_ADDR           │
                                                      │     LEN                │
                                                      │     CTRL.start_rd=1    │
                                                      │                       │
                                                      │  ⑧ 轮询 STATUS.rd_done │
                                                      │     (或使能 IRQ 等待)  │
  ══════════════════════════════════════════════════════════════════════════════════
  ║                 pcie_dma_wrapper (新 RTL, ~360行)                            ║
  ║                                                                             ║
  ║  ⑨ APB→stream FSM 检测到 start_rd=1                                        ║
  ║     → 读 PCIE_ADDR/AXI_ADDR/LEN 寄存器                                      ║
  ║     → 生成 s_axis_read_desc_pcie_addr / axi_addr / len / tag                ║
  ║     → s_axis_read_desc_valid=1                                              ║
  ════════════════════════════════╦═════════════════════════════════════════════════╝
                              │ AXI-Stream descriptor
                              ▼
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │                         dma_if_pcie (verilog-pcie, 不修改)                    │
  │                                                                              │
  │  ⑩ 收到 read descriptor → 分配 PCIe tag → 构造 MRd TLP 头                    │
  │  ⑪ 等待 TX 信用 → 输出 tx_rd_req_tlp_*                                       │
  └──────────────────────────────────┬───────────────────────────────────────────┘
                                     │ TLP (MRd)
  ┌──────────────────────────────────▼───────────────────────────────────────────┐
  │                         pcie_tlp_mux (verilog-pcie, 不修改)                   │
  │  ⑫ mux: DMA tx_rd_req + bridge tx_cpl → 一条 TX TLP 流                       │
  └──────────────────────────────────┬───────────────────────────────────────────┘
                                     │ TLP 出 NPU → PCIe Link
                                     ▼
                         ┌─────────────────────────┐
                         │   PCIe Gen4 x4 Link     │
                         │   → Host Root Complex    │
                         └────────────┬────────────┘
                                      │
  ┌───────────────────────────────────▼──────────────────────────────────────────┐
  │                              HOST (RC)                                        │
  │                                                                              │
  │  ⑬ RC 收到 MRd → 从 host 物理内存 0x3_0000_0000 读 4096 字节                   │
  │  ⑭ 返回 CplD TLP（可能分多段，每段 ≤ MPS=256B）                                 │
  └───────────────────────────────────┬──────────────────────────────────────────┘
                                      │ CplD TLP 进入 NPU
                                      ▼
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │                     pcie_tlp_demux (verilog-pcie, 不修改)                     │
  │  ⑮ Fmt/Type 译码: Cpl/CplD → 路由到 DMA port 1                               │
  └──────────────────────────────────┬───────────────────────────────────────────┘
                                     │ CplD
  ┌──────────────────────────────────▼───────────────────────────────────────────┐
  │                        dma_if_pcie (续)                                       │
  │  ⑯ 收到 CplD → 匹配 tag → 提取数据 → 写入 ram_wr_cmd_*（分段 RAM 接口）         │
  │  ⑰ m_axis_read_desc_status_valid = 1 (带 tag + error code)                   │
  └──────────────────────────────────┬───────────────────────────────────────────┘
                                     │ 分段 RAM 接口
  ┌──────────────────────────────────▼───────────────────────────────────────────┐
  │                        dma_if_axi (verilog-pcie, 不修改)                      │
  │  ⑱ RAM→AXI4: 读 ram_wr_cmd 数据 → 发起 m_axi_aw* + m_axi_w*                  │
  │      目标地址 = axi_addr (0x2000_1000, SRAM)                                   │
  └──────────────────────────────────┬───────────────────────────────────────────┘
                                     │ AXI4 master (crossbar M6)
                                     ▼
  ┌──────────────────────────────────────────────────────────────────────────────┐
  │                             AXI Crossbar                                      │
  │  ⑲ 路由到 SRAM slave @ 0x2000_1000 (crossbar S0)                              │
  └──────────────────────────────────┬───────────────────────────────────────────┘
                                     │
  ┌──────────────────────────────────▼───────────────────────────────────────────┐
  │                           SRAM (4MB, crossbar S0)                             │
  │  ⑳ 4096 字节 prompt 数据写入 NPU SRAM @ 0x2000_1000 ✅                         │
  └──────────────────────────────────────────────────────────────────────────────┘

                                      ─── 传输完成 ───

                      │  ㉑ pcie_dma_wrapper 检测到 status_valid                   │
                      │      PCIE_STATUS.rd_done = 1                              │
                      │      (如果 irq_en) pcie_dma_irq → INTC bit 7              │
                      │                                                           │
                      │  ㉒ firmware 检测到 rd_done=1                               │
                      │     (或 INTC IRQ → ISR)                                   │
                      │     写 completion ring:                                   │
                      │       completion_t {cmd_id=7, status=0}                   │
                      │     写 doorbell NPU_HEAD = 7 (清除中断)                    │
                      │                                                           │
  ◄────────────────────────────────────────────────────────────────────────────────
  ㉓ host driver 轮询 completion ring 或收到中断
      得知 DMA 完成，4096 字节已在 SRAM 就绪
```

---

## 3. NPU→host 方向（PCIe write，以结果回传为例）

方向相反，区别如下：

| 步骤 | host→NPU (PCIe read) | NPU→host (PCIe write) |
|------|----------------------|------------------------|
| **descriptor** | `direction = 0` | `direction = 1` |
| **APB 启动** | `CTRL.start_rd` | `CTRL.start_wr` |
| **dma_if_pcie** | 生成 MRd TLP → 收 CplD → 写 RAM | 读 RAM → 生成 MWr TLP → 出 NPU |
| **dma_if_axi** | RAM→AXI write (数据进 SRAM) | AXI→RAM read (从 SRAM 取数据) |
| **host 操作** | host 被动返回数据 (CplD) | host 被动接收数据 (MWr) |
| **firmware 等** | 等 STATUS.rd_done | 等 STATUS.wr_done |

```
firmware 写 CTRL.start_wr
  → dma_if_axi: 从 SRAM (axi_addr) 读数据 → 写分段 RAM
  → dma_if_pcie: 读分段 RAM → 构造 MWr TLP (pcie_addr, len)
  → pcie_tlp_mux → PCIe → host memory
  → firmware 等 STATUS.wr_done
```

---

## 4. 各组件职责总结

| 谁 | 做什么 | 不做什么 |
|----|--------|----------|
| **Host CPU driver** | 准备描述符 → 写 PCIe BAR1 (DRAM) → 写 doorbell (BAR0) | 不参与数据搬运 |
| **Ibex RISC-V firmware** | 读 doorbell → 解析命令 → 读 DRAM 描述符 → 写 APB 寄存器 → 等完成 → 写 completion | ~50行新增代码 |
| **pcie_dma_wrapper** (新 RTL) | APB 寄存器 → AXI-Stream 描述符 FSM → 完成检测 → IRQ | 唯一新写模块, ~360行 |
| **dma_if_pcie** (开源, 不修改) | 描述符 → TLP (MRd/MWr), tag 管理, CplD 匹配, 数据搬运 ↔ 分段 RAM | — |
| **dma_if_axi** (开源, 不修改) | 分段 RAM ↔ AXI4 master, burst 管理 | — |
| **pcie_tlp_mux/demux** (开源, 不修改) | TLP 流多路复用, completion 路由 | — |
| **AXI Crossbar** | 路由 DMA 的 AXI 读写到 SRAM/DRAM | — |

---

## 5. 关键时序路径

```
host 写 doorbell
     │ ← 一次 PCIe MWr TLP 延迟 (~100ns)
     ▼
doorbell_irq = 1
     │ ← INTC 中断延迟 (~3 cycles)
     ▼
firmware ISR 入口
     │ ← 读 PENDING 寄存器 (~10 cycles)
     │ ← 读 ring buffer entry from DRAM (~200ns AXI4+DDR)
     │ ← dispatch_cmd() 分支 (~5 cycles)
     │ ← 读描述符 from DRAM (~200ns)
     │ ← 写 APB 寄存器 (~10 cycles)
     ▼
FSM 生成描述符 stream (~3 cycles)
     │
     ▼
dma_if_pcie 生成 MRd TLP (~5 cycles)
     │ ← PCIe 链路延迟 + RC 响应 (~500ns)
     ▼
CplD 到达 → 数据写入 SRAM (~400ns AXI4 + dma_if_axi)
     │
     ▼
STATUS.rd_done = 1 → firmware 感知 (~200ns IRQ 或轮询)
```

**端到端延迟**（不含数据传输）：~2μs。主要瓶颈是 firmware 两次 DRAM 访问和 PCIe 往返。

---

## 6. 与现有 MMUL 的对比

| 步骤 | MMUL (opcode=0) | PCIe DMA (opcode=7) |
|------|-----------------|---------------------|
| host 写描述符 | mmul_desc_t (M,K,N, addr...) | pcie_dma_desc_t (pcie/axi addr, len, dir) |
| host 写 ring | opcode=0, desc_addr | opcode=7, desc_addr |
| host 写 doorbell | HOST_TAIL = N | HOST_TAIL = N |
| firmware 处理 | mmul_start(M,K,N,...) | pcie_dma_exec(desc_addr) |
| firmware 操作 | 写 MXU MMIO 寄存器 (0x4000_0000) | 写 PCIe DMA APB 寄存器 (0x4000_7000) |
| 数据通路 | MXU AXI4 master → SRAM | dma_if_axi AXI4 master → SRAM |
| 完成信号 | MXU STATUS.DONE | PCIE_STATUS.rd_done/wr_done |

完全遵循同一套 doorbell + ring buffer + APB 调度范式。
