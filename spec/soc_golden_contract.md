# CaduceusCore SoC Golden Observable Contract v1.0

> **Status:** FROZEN — derived from `spec/npu_abi.json` v1.0 on 2026-07-29.
>
> This document freezes the 9 architecture-observable behaviors the Func Model
> MUST match for SoC RTL golden signoff. Every value below is authoritative.
> Drift between this contract and any generated artifact (`gen/npu_abi.*`)
> MUST be detected by `scripts/contract_check.py --check`.
>
> **What belongs here:** only SoC-interface observable behavior.
> **What does NOT:** timing, performance, internal implementation.

---

## 1. Register Map

The SoC exposes 13 address regions. Each region's base address and size
are architecturally fixed; any change is an ABI major version break.

### 1.1 Address Regions

| Region | Base | Size | Description |
|--------|------|------|-------------|
| `BOOT_ROM` | `0x00000000` | `0x00010000` (64 KB) | Ibex reset vector + firmware boot ROM |
| `IBEX_DMEM` | `0x00010000` | `0x00010000` (64 KB) | Ibex data memory: stack + .data/.bss |
| `SRAM` | `0x20000000` | `0x00400000` (4 MB) | NPU unified compute buffer SRAM |
| `MXU` | `0x40000000` | `0x00001000` (4 KB) | Matrix Multiply Unit MMIO |
| `SFU` | `0x40001000` | `0x00001000` (4 KB) | Special Function Unit MMIO |
| `VECTOR` | `0x40002000` | `0x00001000` (4 KB) | Vector Engine MMIO |
| `DMA` | `0x40003000` | `0x00001000` (4 KB) | DMA Engine MMIO |
| `PCIE` | `0x40004000` | `0x00001000` (4 KB) | PCIe EP MMIO |
| `DOORBELL` | `0x40005000` | `0x00001000` (4 KB) | Host↔NPU doorbell ring buffer control |
| `INTC` | `0x40006000` | `0x00001000` (4 KB) | Interrupt controller MMIO |
| `PCIE_DMA` | `0x40007000` | `0x00001000` (4 KB) | PCIe DMA Engine MMIO |
| `DRAM` | `0x80000000` | `0x80000000` (2 GB) | Host DDR DRAM data space |

### 1.2 MXU Registers (0x40000000)

| Offset | Register | Width | Access | Reset | Fields |
|--------|----------|-------|--------|-------|--------|
| `0x00` | `CTRL` | 32 | rw | `0x00000000` | `[1:0]=DTYPE` (0=INT4xINT8, 1=INT8xINT8, 2=BF16) |
| `0x04` | `CMD` | 32 | wo | `0x00000000` | `bit[0]=START`, `bit[1]=ABORT` |
| `0x08` | `STATUS` | 32 | ro | `0x00000000` | `bit[0]=BUSY`, `bit[1]=DONE`, `bit[2]=ERROR` |
| `0x0C` | `DIM0` | 32 | rw | `0x00000000` | `[15:0]=M rows`, `[31:16]=K inner dimension` |
| `0x10` | `DIM1` | 32 | rw | `0x00000000` | `[15:0]=N columns`, `[31:16]=reserved` |
| `0x14` | `I_ADDR` | 32 | rw | `0x00000000` | Input activation SRAM byte address |
| `0x18` | `W_ADDR` | 32 | rw | `0x00000000` | Weight SRAM byte address |
| `0x1C` | `O_ADDR` | 32 | rw | `0x00000000` | Output SRAM byte address |
| `0x20` | `BIAS_ADDR` | 32 | rw | `0x00000000` | Bias SRAM byte address (0 = no bias) |
| `0x24` | `SCALE_ADDR` | 32 | rw | `0x00000000` | Scale SRAM byte address (0 = no scale) |
| `0x28` | `IRQ_EN` | 32 | rw | `0x00000000` | bit[0]=completion IRQ |

### 1.3 SFU Registers (0x40001000)

| Offset | Register | Width | Access | Reset | Fields |
|--------|----------|-------|--------|-------|--------|
| `0x00` | `CTRL` | 32 | rw | `0x00000000` | `[3:0]=OP` (0-6: SOFTMAX..RMSNORM) |
| `0x04` | `CMD` | 32 | wo | `0x00000000` | `bit[0]=START` |
| `0x08` | `STATUS` | 32 | ro | `0x00000000` | `bit[0]=BUSY`, `bit[1]=DONE` |
| `0x0C` | `I_ADDR` | 32 | rw | `0x00000000` | Input SRAM byte address |
| `0x10` | `O_ADDR` | 32 | rw | `0x00000000` | Output SRAM byte address |
| `0x14` | `DIM` | 32 | rw | `0x00000000` | `[15:0]=elements`, `[31:16]=head_dim` (RoPE) |
| `0x18` | `POS` | 32 | rw | `0x00000000` | Position (RoPE) |
| `0x1C` | `IRQ_EN` | 32 | rw | `0x00000000` | bit[0]=completion IRQ |

### 1.4 VECTOR Registers (0x40002000)

| Offset | Register | Width | Access | Reset | Fields |
|--------|----------|-------|--------|-------|--------|
| `0x00` | `CTRL` | 32 | rw | `0x00000000` | `[3:0]=OP` (0-5: ADD..RESID_ADD) |
| `0x04` | `CMD` | 32 | wo | `0x00000000` | `bit[0]=START` |
| `0x08` | `STATUS` | 32 | ro | `0x00000000` | `bit[0]=BUSY`, `bit[1]=DONE` |
| `0x0C` | `A_ADDR` | 32 | rw | `0x00000000` | Operand A SRAM byte address |
| `0x10` | `B_ADDR` | 32 | rw | `0x00000000` | Operand B SRAM byte address (unused for unary) |
| `0x14` | `O_ADDR` | 32 | rw | `0x00000000` | Output SRAM byte address |
| `0x18` | `DIM` | 32 | rw | `0x00000000` | `[15:0]=element count` |
| `0x1C` | `IRQ_EN` | 32 | rw | `0x00000000` | bit[0]=completion IRQ |

### 1.5 DMA Registers (0x40003000)

| Offset | Register | Width | Access | Reset | Description |
|--------|----------|-------|--------|-------|-------------|
| `0x00` | `CTRL` | 32 | rw | `0x00000000` | `[0]=linked_list_en`, `[1:2]=channel_mode` |
| `0x04` | `CMD` | 32 | wo | `0x00000000` | `bit[0]=START`, `bit[1]=ABORT` |
| `0x08` | `STATUS` | 32 | ro | `0x00000000` | `bit[0]=BUSY`, `bit[1]=DONE`, `[7:4]=active_channel` |
| `0x10` | `CH0_SRC` | 32 | rw | `0x00000000` | Channel 0: DRAM source address |
| `0x14` | `CH0_DST` | 32 | rw | `0x00000000` | Channel 0: SRAM destination address |
| `0x18` | `CH0_SIZE` | 32 | rw | `0x00000000` | Channel 0: transfer bytes |
| `0x1C` | `CH0_STRIDE` | 32 | rw | `0x00000000` | Channel 0: 2D stride |
| `0x20` | `CH1_SRC` | 32 | rw | `0x00000000` | Channel 1: SRAM source address |
| `0x24` | `CH1_DST` | 32 | rw | `0x00000000` | Channel 1: DRAM destination address |
| `0x28` | `CH1_SIZE` | 32 | rw | `0x00000000` | Channel 1: transfer bytes |
| `0x2C` | `CH1_STRIDE` | 32 | rw | `0x00000000` | Channel 1: 2D stride |
| `0x30` | `DESC_ADDR` | 32 | rw | `0x00000000` | Descriptor chain base address (DRAM) |
| `0x34` | `DESC_CNT` | 32 | rw | `0x00000000` | Descriptor count |
| `0x38` | `IRQ_EN` | 32 | rw | `0x00000000` | bit[0]=completion IRQ |

### 1.6 PCIE_DMA Registers (0x40007000)

| Offset | Register | Width | Access | Reset | Description |
|--------|----------|-------|--------|-------|-------------|
| `0x00` | `CTRL` | 32 | rw | `0x00000000` | `bit[0]=start_rd`, `[1]=start_wr`, `[2]=abort`, `[3]=irq_en` |
| `0x04` | `STATUS` | 32 | ro | `0x00000000` | `bit[0]=rd_busy`, `[1]=wr_busy`, `[2]=rd_done`, `[3]=wr_done`, `[4]=error` |
| `0x08` | `PCIE_ADDR_LO` | 32 | rw | `0x00000000` | Host PCIe address `[31:0]` |
| `0x0C` | `PCIE_ADDR_HI` | 32 | rw | `0x00000000` | Host PCIe address `[63:32]` |
| `0x10` | `AXI_ADDR` | 32 | rw | `0x00000000` | NPU AXI address |
| `0x14` | `LEN` | 32 | rw | `0x00000000` | Transfer length in bytes |
| `0x18` | `TAG` | 32 | rw | `0x00000000` | Descriptor tag |
| `0x1C` | `RD_ERR_CODE` | 32 | ro | `0x00000000` | Read descriptor error code |
| `0x20` | `WR_ERR_CODE` | 32 | ro | `0x00000000` | Write descriptor error code |

---

## 2. Descriptor Layout

### 2.1 Descriptor Sizes (bytes)

| Descriptor | Packed Size | Field Count |
|------------|------------|-------------|
| `MMUL` | 60 | 15 |
| `SFU` | 60 | 15 |
| `VECTOR` | 60 | 15 |
| `DMA_COPY` | 60 | 15 |
| `PCIE_DMA` | 24 | 6 |

### 2.2 MMUL Descriptor Field Offsets (60 bytes)

| Index | Offset | Name | Type | Description |
|-------|--------|------|------|-------------|
| 0 | 0 | `input_addr` | uint32 | DRAM activation address |
| 1 | 4 | `weight_addr` | uint32 | DRAM weight address |
| 2 | 8 | `output_addr` | uint32 | DRAM output address |
| 3 | 12 | `scale_addr` | uint32 | DRAM scale address (0=none) |
| 4 | 16 | `input_sram` | uint32 | SRAM activation target address |
| 5 | 20 | `weight_sram` | uint32 | SRAM weight target address |
| 6 | 24 | `output_sram` | uint32 | SRAM output target address |
| 7 | 28 | `scale_sram` | uint32 | SRAM scale target address |
| 8 | 32 | `input_size` | uint32 | Activation size in bytes |
| 9 | 36 | `weight_size` | uint32 | Weight size in bytes |
| 10 | 40 | `output_size` | uint32 | Output size in bytes |
| 11 | 44 | `scale_size` | uint32 | Scale size in bytes |
| 12 | 48 | `M` | uint32 | Row dimension |
| 13 | 52 | `K` | uint32 | Inner dimension |
| 14 | 56 | `N` | uint32 | Column dimension |

### 2.3 SFU Descriptor Field Offsets (60 bytes)

| Index | Offset | Name | Type | Description |
|-------|--------|------|------|-------------|
| 0 | 0 | `input_addr` | uint32 | DRAM input activation address |
| 1 | 4 | `_reserved_1` | uint32 | Reserved |
| 2 | 8 | `output_addr` | uint32 | DRAM output address |
| 3 | 12 | `_reserved_3` | uint32 | Reserved |
| 4 | 16 | `input_sram` | uint32 | SRAM input (firmware ignores) |
| 5 | 20 | `output_sram` | uint32 | SRAM output (firmware ignores) |
| 6 | 24 | `_reserved_6` | uint32 | Reserved |
| 7 | 28 | `_reserved_7` | uint32 | Reserved |
| 8 | 32 | `dim` | uint32 | Elements (low 16) \| head_dim (high 16) |
| 9 | 36 | `pos` | uint32 | Position (RoPE only) |
| 10 | 40 | `sfu_op` | uint32 | SFU hardware sub-opcode (0-6) |
| 11 | 44 | `_reserved_11` | uint32 | Reserved |
| 12 | 48 | `_unused_12` | uint32 | Unused |
| 13 | 52 | `_unused_13` | uint32 | Unused |
| 14 | 56 | `_unused_14` | uint32 | Unused |

### 2.4 VECTOR Descriptor Field Offsets (60 bytes)

| Index | Offset | Name | Type | Description |
|-------|--------|------|------|-------------|
| 0 | 0 | `a_addr` | uint32 | DRAM operand A address |
| 1 | 4 | `b_addr` | uint32 | DRAM operand B address |
| 2 | 8 | `o_addr` | uint32 | DRAM output address |
| 3 | 12 | `_reserved_3` | uint32 | Reserved |
| 4 | 16 | `a_sram` | uint32 | SRAM operand A |
| 5 | 20 | `b_sram` | uint32 | SRAM operand B |
| 6 | 24 | `o_sram` | uint32 | SRAM output |
| 7 | 28 | `_reserved_7` | uint32 | Reserved |
| 8 | 32 | `dim` | uint32 | Element count |
| 9 | 36 | `_reserved_9` | uint32 | Reserved |
| 10 | 40 | `_reserved_10` | uint32 | Reserved |
| 11 | 44 | `_reserved_11` | uint32 | Reserved |
| 12 | 48 | `_unused_12` | uint32 | Unused |
| 13 | 52 | `_unused_13` | uint32 | Unused |
| 14 | 56 | `_unused_14` | uint32 | Unused |

### 2.5 DMA_COPY Descriptor Field Offsets (60 bytes)

| Index | Offset | Name | Type | Description |
|-------|--------|------|------|-------------|
| 0 | 0 | `src_addr` | uint32 | DRAM source address |
| 1 | 4 | `_reserved_1` | uint32 | Reserved |
| 2 | 8 | `dst_addr` | uint32 | DRAM/SRAM destination address |
| 3 | 12 | `_reserved_3` | uint32 | Reserved |
| 4 | 16 | `_reserved_4` | uint32 | Reserved |
| 5 | 20 | `_reserved_5` | uint32 | Reserved |
| 6 | 24 | `_reserved_6` | uint32 | Reserved |
| 7 | 28 | `_reserved_7` | uint32 | Reserved |
| 8 | 32 | `size` | uint32 | Transfer size in bytes |
| 9 | 36 | `_reserved_9` | uint32 | Reserved |
| 10 | 40 | `_reserved_10` | uint32 | Reserved |
| 11 | 44 | `_reserved_11` | uint32 | Reserved |
| 12 | 48 | `_unused_12` | uint32 | Unused |
| 13 | 52 | `_unused_13` | uint32 | Unused |
| 14 | 56 | `_unused_14` | uint32 | Unused |

### 2.6 PCIE_DMA Descriptor Field Offsets (24 bytes)

| Index | Offset | Name | Type | Description |
|-------|--------|------|------|-------------|
| 0 | 0 | `pcie_addr_lo` | uint32 | PCIe target address `[31:0]` |
| 1 | 4 | `pcie_addr_hi` | uint32 | PCIe target address `[63:32]` |
| 2 | 8 | `axi_addr` | uint32 | Local AXI source/destination address |
| 3 | 12 | `len` | uint32 | Transfer length in bytes |
| 4 | 16 | `direction` | uint32 | 0=host→NPU, 1=NPU→host |
| 5 | 20 | `_pad` | uint32 | Padding |

---

## 3. Ring Buffer Semantics

### 3.1 Command Ring

| Property | Value | Description |
|----------|-------|-------------|
| Base address (DRAM) | `0x80000000` | Command ring starts at DRAM_BASE |
| Entry count | **1024** | `ring_entries` |
| Entry size | **32 bytes** | `cmd_entry_size` — 8 × uint32 |
| Total size | 32768 bytes | `ring_entries × cmd_entry_size` |

#### Command Entry Layout (32 bytes)

| Offset | Name | Type | Description |
|--------|------|------|-------------|
| 0 | `opcode` | uint32 | Engine-level opcode (see §3.3) |
| 4 | `desc_addr` | uint32 | Operation descriptor DRAM address |
| 8 | `flags` | uint32 | `bit[0]=irq_on_completion`, `bit[1]=immediate_exec` |
| 12 | `_pad_3` | uint32 | Padding |
| 16 | `_pad_4` | uint32 | Padding |
| 20 | `_pad_5` | uint32 | Padding |
| 24 | `_pad_6` | uint32 | Padding |
| 28 | `_pad_7` | uint32 | Padding |

### 3.2 Completion Ring

| Property | Value | Description |
|----------|-------|-------------|
| Base address (DRAM) | `0x80008000` | Completion ring follows command ring |
| Completion entry size | **32 bytes** | Same as command entry |

#### Completion Entry Layout (32 bytes)

| Offset | Name | Type | Description |
|--------|------|------|-------------|
| 0 | `cmd_id` | uint32 | Command ID (ring index) |
| 4 | `status` | uint32 | Completion status (0=success, non-zero=error) |
| 8-28 | `_pad_*` | uint32 | Padding (6 fields) |

### 3.3 Engine Opcodes (Host Command Ring)

| Opcode | Value | Engine | Description |
|--------|-------|--------|-------------|
| `MMUL` | `0x00` (0) | MXU | Matrix multiply (INT4×INT8→INT32) |
| `SFU_SOFTMAX` | `0x01` (1) | SFU | Softmax activation |
| `SFU_LAYERNORM` | `0x02` (2) | SFU | Layer Normalization |
| `SFU_GELU` | `0x03` (3) | SFU | GELU activation |
| `SFU_RELU` | `0x04` (4) | SFU | ReLU activation |
| `ROPE` | `0x05` (5) | SFU | RoPE positional encoding |
| `SFU_SILU` | `0x06` (6) | SFU | SiLU activation |
| `PCIE_DMA` | `0x07` (7) | PCIE_DMA | PCIe DMA host↔NPU data transfer |
| `DMA_COPY` | `0x09` (9) | DMA | DRAM→SRAM data copy |
| `DMA_ST` | `0x0A` (10) | DMA | SRAM→DRAM data store |
| `VADD` | `0x0F` (15) | VECTOR | Vector element-wise addition |
| `VMUL` | `0x10` (16) | VECTOR | Vector element-wise multiplication |
| `VRED_MAX` | `0x11` (17) | VECTOR | Vector reduce max |
| `VRED_SUM` | `0x12` (18) | VECTOR | Vector reduce sum |
| `VCONV` | `0x13` (19) | VECTOR | INT32→FP16 type conversion |
| `VRESID` | `0x14` (20) | VECTOR | Residual add: dst = src_a + src_b |
| `DMA_COPY_LDD` | `0x15` (21) | DMA | DMA load (descriptor chain mode) |
| `DMA_COPY_STD` | `0x16` (22) | DMA | DMA store (descriptor chain mode) |
| `SFU_RMSNORM` | `0x17` (23) | SFU | RMS Normalization (two-pass) |

> **Opcodes 8, 11-14 are intentionally unused.** Adding a new opcode in an unused slot is a minor ABI change. Renumbering an existing opcode is a major break.

---

## 4. Doorbell Semantics

### 4.1 Doorbell Registers (0x40005000)

| Offset | Register | Width | Access | Description |
|--------|----------|-------|--------|-------------|
| `0x00` | `HOST_TAIL` | 32 | wo | Host writes after appending command entries; triggers NPU wakeup |
| `0x04` | `NPU_HEAD` | 32 | rw | NPU firmware updates head (consumed pointer) |
| `0x08` | `HOST_HEAD` | 32 | ro | NPU updates → host reads completion |
| `0x0C` | `NPU_TAIL` | 32 | ro | Host updates → NPU sees new commands |
| `0x10` | `LAST_STATUS` | 32 | rw | Last command status (0=done, non-zero=error) |
| `0x14` | `COMPLETION_STATUS[16]` | 32×16 | rw | Per-ring-index completion status array |

### 4.2 Doorbell Protocol

- **Host submission:** Host writes command entries into the command ring in DRAM, then writes
  the new tail index to `HOST_TAIL`. This write triggers the NPU wakeup interrupt.
- **NPU consumption:** The NPU firmware polls `HOST_TAIL` vs `NPU_HEAD`. When they differ,
  the firmware processes entries from `NPU_HEAD` to `HOST_TAIL-1`.
- **Completion notification:** After processing a command, the firmware writes the result to
  the completion ring and updates `HOST_HEAD`. The host polls `HOST_HEAD`.
- **Known discrepancy:** `COMPLETION_STATUS` is declared as 16 entries (64 bytes at offset
  0x14). The RTL `doorbell.v` implements only a single `LAST_STATUS` register at offset 0x10.
  Firmware writes `COMPLETION_STATUS[cmd_id]` with `cmd_id` up to 1023, which overflows the
  declared window. Resolution TBD in a future ABI revision.

---

## 5. INTC Semantics

### 5.1 Interrupt Controller Registers (0x40006000)

| Offset | Register | Width | Access | Description |
|--------|----------|-------|--------|-------------|
| `0x00` | `PENDING` | 32 | ro | Pending IRQ bits — see source assignments |
| `0x04` | `ENABLE` | 32 | rw | IRQ enable mask (same bit layout as PENDING) |
| `0x08` | `THRESHOLD` | 32 | rw | Priority threshold |
| `0x0C` | `ACK` | 32 | wo | Write 1 to corresponding bit to clear IRQ |

### 5.2 Interrupt Source Bit Assignments

| Bit | Source | Description |
|-----|--------|-------------|
| 0 | `MXU` | MXU completion interrupt |
| 1 | `SFU` | SFU completion interrupt |
| 2 | `VECTOR` | Vector engine completion interrupt |
| 3 | `DMA` | DMA completion interrupt |
| 4 | `PCIE` | PCIe event interrupt |
| 5 | `HOST` | Host doorbell interrupt |
| 6 | `TIMER` | Timer interrupt |

### 5.3 INTC Behavior

- `PENDING` register asserts bit[i] when the corresponding interrupt source fires.
- `ENABLE` register gates which interrupts are forwarded to the CPU.
- `THRESHOLD` is the minimum priority level for an interrupt to be raised.
- Writing `1 << bit` to `ACK` clears the corresponding pending bit.

---

## 6. Crossbar Address Decode

### 6.1 Address Space Map

The 32-bit physical address space is statically partitioned. All MMIO regions
use 4 KB windows; SRAM and DRAM use larger contiguous ranges.

| Address Range | Target | Master Access |
|---------------|--------|---------------|
| `0x00000000 - 0x0000FFFF` | Boot ROM (64 KB) | Ibex only |
| `0x00010000 - 0x0001FFFF` | Ibex DMEM (64 KB) | Ibex only |
| `0x20000000 - 0x203FFFFF` | SRAM (4 MB) | All AXI masters (Ibex/MXU/SFU/Vector/DMA/PCIe) |
| `0x40000000 - 0x40000FFF` | MXU MMIO (4 KB) | APB → MMIO |
| `0x40001000 - 0x40001FFF` | SFU MMIO (4 KB) | APB → MMIO |
| `0x40002000 - 0x40002FFF` | VECTOR MMIO (4 KB) | APB → MMIO |
| `0x40003000 - 0x40003FFF` | DMA MMIO (4 KB) | APB → MMIO |
| `0x40004000 - 0x40004FFF` | PCIe MMIO (4 KB) | APB → MMIO |
| `0x40005000 - 0x40005FFF` | DOORBELL MMIO (4 KB) | APB → MMIO |
| `0x40006000 - 0x40006FFF` | INTC MMIO (4 KB) | APB → MMIO |
| `0x40007000 - 0x40007FFF` | PCIE_DMA MMIO (4 KB) | APB → MMIO |
| `0x80000000 - 0xFFFFFFFF` | DRAM (2 GB) | All AXI masters via crossbar |

### 6.2 AXI Crossbar Topology

- **Configuration:** M=6 masters, S=2 slaves (SRAM + DRAM), round-robin arbitration.
- **Masters:** Ibex (0), MXU (1), SFU (2), Vector (3), DMA (4), PCIe (5).
- **Slaves:** SRAM at `0x20000000` (S0), DRAM at `0x80000000` (S1).
- **APB hierarchy:** A single APB decoder fans out to 7 MMIO slaves.
- **Ibex decode exception:** Boot ROM + DMEM are Ibex-local (no crossbar traversal).

---

## 7. PCIe / BAR TLP Behavior

### 7.1 PCIe Interface

- **Link:** PCIe Gen4 x4 (16 GT/s raw, ~15.75 GB/s effective per direction).
- **Endpoint model:** The NPU is a PCIe EP; the host is the Root Complex.
- **BAR0:** Maps the NPU's MMIO address space and DRAM into host address space.
- **TLP routing:** MWr (posted writes) for doorbell and command submission;
  MRd (non-posted reads) for completion polling.

### 7.2 PCIe DMA Engine Registers (0x40007000)

See §1.6 — PCIE_DMA registers. The PCIe DMA engine handles host↔NPU data
movement independent of the command ring doorbell path.

### 7.3 PCIe DMA Descriptor (24 bytes)

See §2.6 — 6-word descriptor: `{pcie_addr_lo, pcie_addr_hi, axi_addr, len, direction, _pad}`.
Direction: 0 = host→NPU read, 1 = NPU→host write.

### 7.4 TLP Behavior

- **Doorbell (host→NPU):** Host writes the command ring tail index to the NPU's
  `DOORBELL.HOST_TAIL` register via a single 32-bit MWr TLP. This generates
  the `INTC_HOST` interrupt.
- **Completion (NPU→host):** NPU firmware writes completion status to the
  completion ring in DRAM and updates `DOORBELL.HOST_HEAD`. The host polls
  `HOST_HEAD` or receives an MSI interrupt.
- **Data transfer (PCIe DMA):** Host configures `PCIE_DMA` registers with
  source/destination addresses and issues a START command. The engine generates
  MWr/MRd TLPs autonomously.

---

## 8. Reset Behavior

### 8.1 Reset Values

All MMIO registers reset to `0x00000000` (zero) unless otherwise specified.
This is architecturally guaranteed:

| Module | Affected Registers | Reset Value |
|--------|-------------------|-------------|
| MXU | All 11 registers | `0x00000000` |
| SFU | All 8 registers | `0x00000000` |
| VECTOR | All 8 registers | `0x00000000` |
| DMA | All 14 registers | `0x00000000` |
| DOORBELL | All 6 registers | `0x00000000` |
| INTC | All 4 registers | `0x00000000` |
| PCIE_DMA | All 9 registers | `0x00000000` |

### 8.2 Reset Sequence

1. Hardware reset de-asserts.
2. All registers are at their reset values (all zeros).
3. Boot ROM at `0x00000000` begins executing.
4. No engines are running (STATUS.BUSY = 0).
5. All interrupts are masked (ENABLE = 0, no pending).
6. Ring buffer head/tail pointers are zero.
7. Firmware initializes the system: configures engines, enables interrupts,
   sets up the doorbell.

---

## 9. Error Behavior

### 9.1 Status Codes

The completion ring and `LAST_STATUS` register use these standard codes:

| Code | Name | Value | Description |
|------|------|-------|-------------|
| 0 | `SUCCESS` | 0 | Command completed successfully |
| 1 | `GENERIC_ERROR` | 1 | Generic / unknown error |
| 2 | `TIMEOUT` | 2 | Operation timed out |
| 3 | `CORRUPTED_DESCRIPTOR` | 3 | Descriptor validation failed (zero dimensions) |
| 4 | `UNKNOWN_OPCODE` | 4 | Unrecognized engine opcode |

### 9.2 Engine Error Behavior

- **MXU:** Errors are reported via `STATUS.ERROR` (bit 2). Conditions include
  invalid dimensions or address overflow.
- **SFU / VECTOR:** Errors are reported via `STATUS` bits. The `DONE` bit
  only asserts on successful completion.
- **DMA:** Transfer errors are reported via `STATUS` bits.
- **PCIe DMA:** Read/write errors are recorded in `RD_ERR_CODE` / `WR_ERR_CODE`
  status registers.

### 9.3 Contract Guarantees

- Any non-zero completion status indicates an error. The Func Model MUST
  produce the same status code for the same error condition.
- A command that completes with status 0 (SUCCESS) produces deterministic
  output data at the descriptor-specified output address.
- On error, the engine state is implementation-defined (the contract
  does not prescribe partial output behavior).

---

## Compatibility Rules

Derived from `spec/npu_abi.json` §compatibility:

| Change Category | Version Impact | Examples |
|----------------|---------------|----------|
| Register offset change | **Major break** | Moving MXU.CTRL from 0x00 to 0x04 |
| Opcode value renumbering | **Major break** | Changing MMUL from 0 to 1 |
| Address region remapping | **Major break** | Moving SRAM from 0x20000000 to 0x30000000 |
| Descriptor field reordering | **Major break** | Swapping input_addr and weight_addr positions |
| Ring layout change | **Major break** | Changing entry size from 32 to 64 bytes |
| Capability bit reassignment | **Major break** | Moving MXU_SUPPORTED from bit 0 to bit 2 |
| New register in reserved offset | **Minor add** | Adding MXU.TEST_MODE at 0x30 |
| New opcode in unused value slot | **Minor add** | Adding a new opcode at value 8 |
| New descriptor field at end of layout | **Minor add** | Appending a 15th field at offset 60 |
| New capability bit | **Minor add** | Adding bit 14 |
| New address region in reserved space | **Minor add** | Adding a new 4 KB MMIO window |

---

## Known Discrepancies (Contract vs Implementation)

These are known gaps acknowledged in the ABI schema. The contract reflects
the **declared** architecture; the notes document where implementation diverges.

### DOORBELL_COMPLETION_STATUS_SIZE [HIGH]
Declared as 16 entries (offset 0x14, 64 bytes). Firmware writes up to
`RING_ENTRIES-1` (1023). RTL implements only `LAST_STATUS` at 0x10.

### SFU_SRAM_HARDCODE [MEDIUM]
Firmware hardcodes SRAM addresses, ignoring descriptor fields [4]/[5].

### SFU_POS_HARDCODE [LOW]
Firmware hardcodes pos=0 for non-ROPE ops.

### PCIE_DMA_DOORBELL_SIZE_DISCREPANCY [MEDIUM]
`sizeof(npu_pcie_dma_t)` = 36 bytes; doorbell descriptor path uses 32 bytes.
