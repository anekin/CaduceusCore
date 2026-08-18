# Func Model MMIO Register Interface Timing Spec

**Purpose:** This document defines the exact MMIO register write sequences, APB
latency, status/interrupt timing, and controller FSM behavior for every NPU op
type. RTL designers can implement the APB slave, register file, and dispatch
sequencer directly from this spec without reading the Python Func Model.

**Scope:** Covers MXU (matrix multiply), SFU (special functions), Vector Engine,
and DMA engine register interfaces.

**Source files defining this spec:**
- `sim/regmap.py` — canonical register offsets and bit fields
- `rtl/mxu/mmio_if.v` — MXU register file implementation
- `rtl/mxu/controller.v` — MXU tile-iteration FSM
- `rtl/sfu/README.md` — SFU MMIO map and op encoding
- `rtl/vector/README.md` — Vector MMIO map and op encoding
- `rtl/ip/dma_wrapper.v` — DMA APB slave and descriptor FSM
- `rtl/soc/apb_decoder.v` — APB decode and zero-wait-state response
- `rtl/wrapper/apb_to_mmio.v` — APB→MMIO bridge (access-phase-only `cs` strobe)

---

## 1. APB Interface Basics

All engine control registers are reached through the SoC APB decoder at
`0x4000_0000 ~ 0x4000_7FFF`. Each engine occupies one 4 KB window.

| Engine | Base Address | Window |
|--------|-------------:|--------|
| MXU    | `0x4000_0000` | 4 KB |
| SFU    | `0x4000_1000` | 4 KB |
| Vector | `0x4000_2000` | 4 KB |
| DMA    | `0x4000_3000` | 4 KB |

### 1.1 APB Transaction Latency

The APB decoder and all engine APB slaves are **zero-wait-state**:

- `pready = 1` for every valid transfer.
- Each register write therefore costs **2 PCLK cycles**: 1 setup cycle
  (`psel=1, penable=0`) and 1 access cycle (`psel=1, penable=1`).
- The register latch update occurs on the rising edge at the end of the access
  cycle.
- The APB→MMIO bridge (`rtl/wrapper/apb_to_mmio.v`) gates the MMIO chip-select
  with `penable`: `cs = psel && penable`. The MMIO slave therefore sees `cs=1`
  only during the access phase and latches exactly once per transfer, matching
  this spec.
- Back-to-back writes to the same engine can be issued every **2 PCLK cycles**.
- Reads are combinatorial; read data is valid during the access cycle.

> **RTL implication:** Firmware must wait **≥2 PCLK cycles** between consecutive
> register writes to the same engine (or poll `STATUS` between writes if a
> longer delay is acceptable).

### 1.2 Common Register Conventions

- `CTRL` — R/W, configures op type / dtype. Safe to write at any time before
  `CMD.START`.
- `CMD` — write-only, write-1 pulses. Bit[0]=START, Bit[1]=ABORT.
- `STATUS` — read-only from firmware. Bit[0]=BUSY, Bit[1]=DONE, Bit[2]=ERROR.
- `IRQ_EN` — R/W, Bit[0] enables the completion interrupt.
- All unused register bits read back as 0.

---

## 2. MXU — Matrix Multiply Unit

### 2.1 Register Map (BASE = `0x4000_0000`)

| Offset | Name      | Access | Bit Fields |
|-------:|-----------|:------:|------------|
| `0x00` | CTRL      | R/W    | [1:0]=dtype: `0=INT4×INT8`, `1=INT8×INT8`, `2=BF16` |
| `0x04` | CMD       | W      | [0]=START (pulse), [1]=ABORT (pulse) |
| `0x08` | STATUS    | R      | [0]=BUSY, [1]=DONE, [2]=ERROR |
| `0x0C` | DIM0      | R/W    | [15:0]=M, [31:16]=K |
| `0x10` | DIM1      | R/W    | [15:0]=N |
| `0x14` | I_ADDR    | R/W    | Activation SRAM byte address |
| `0x18` | W_ADDR    | R/W    | Weight SRAM byte address |
| `0x1C` | O_ADDR    | R/W    | Output SRAM byte address |
| `0x20` | BIAS_ADDR | R/W    | Bias SRAM address; `0` = no bias |
| `0x24` | SCALE_ADDR| R/W    | Scale SRAM address; `0` = no scale |
| `0x28` | IRQ_EN    | R/W    | [0]=completion IRQ enable |

### 2.2 MMUL Register Write Sequence

For a single MMUL op, write the registers in the following order. Each line is
one APB write (2 PCLK cycles).

```
1.  CTRL      ← dtype (0 for current RTL INT4×INT8)
2.  DIM0      ← (K << 16) | M
3.  DIM1      ← N
4.  I_ADDR    ← activation SRAM byte address
5.  W_ADDR    ← weight SRAM byte address
6.  O_ADDR    ← output SRAM byte address
7.  BIAS_ADDR ← bias SRAM address, or 0
8.  SCALE_ADDR← scale SRAM address, or 0
9.  IRQ_EN    ← 1 (if completion interrupt required)
10. CMD       ← 1   // START pulse
```

- **Minimum elapsed time before START:** 9 writes × 2 cycles = **18 PCLK cycles**
  (assuming BIAS_ADDR, SCALE_ADDR, and IRQ_EN are all written).
- If BIAS/SCALE are unused and IRQ is disabled, the minimum sequence is
  CTRL → DIM0 → DIM1 → I_ADDR → W_ADDR → O_ADDR → CMD = **6 writes = 12 PCLK
  cycles** before START.
- No register write ordering is enforced by hardware except that `CMD.START`
  must be the last write.

### 2.3 Controller FSM and STATUS Timing

The MXU controller implements the following state machine:

```
IDLE → READ_DIMS → LOAD_W → LOAD_A → COMPUTE → STORE_OUT → (tile loop) → DONE
```

State transitions occur on the rising PCLK edge.

| Cycle (relative to CMD.START) | State       | STATUS.BUSY | Visible Behavior |
|------------------------------:|-------------|:-----------:|------------------|
| 0 (CMD write access phase)    | IDLE        | 0           | `cmd_start` pulse sampled by controller |
| 1                             | READ_DIMS   | 1           | Capture M, K, N; compute tile counts |
| 2                             | LOAD_W      | 1           | `weight_load_en` = 1 for one cycle |
| 3                             | LOAD_A      | 1           | `activation_load_en` = 1 for one cycle |
| 4 … 4+k_cur+1                 | COMPUTE     | 1           | `compute_en` strobed for `k_cur+2` cycles |
| …                             | STORE_OUT   | 1           | Row address sequenced 0 … m_cur−1 |
| (final tile)                  | DONE        | 0           | `STATUS.DONE`=1, `irq`=`IRQ_EN` for 1 cycle |
| next cycle                    | IDLE        | 0           | `STATUS.DONE` returns to 0 automatically |

- `STATUS.BUSY` rises in `READ_DIMS` and falls in `DONE`.
- `STATUS.DONE` is a single-cycle pulse at the end of the final output row.
- `STATUS.ERROR` is set if `cmd_abort` is asserted during execution.
- Tile iteration order: **inner K-tile (accumulate) → middle N-tile → outer
  M-tile**. A new K-tile loops back to `LOAD_W`; a new N-tile or M-tile also
  loops back to `LOAD_W`.

### 2.4 MMUL Timing Diagram

Example: `MMUL(M=64, K=64, N=64)`, single 64×64 tile, `k_cur=64`.

```text
PCLK        :  0   1   2   3   4   5       67  68  69  70  71
APB phase   :  S A S A S A S A S A S A ...  S A
            :  |CTRL |DIM0 |DIM1 |I_ADDR|W_ADDR|O_ADDR|BIAS |SCALE|IRQ_EN|CMD |
            :  0 1  2 3  4 5  6 7  8 9  10 11 12 13 14 15 16 17 18 19

psel        :__|-|_|-|_|-|_|-|_|-|_|-|_|-|_|-|_|-|_____...__________________
penable     :____|-|___|-|___|-|___|-|___|-|___|-|___|-|_____________________
pwrite      :__|-|-|-|-|-|-|-|-|-|-|-|-|-|-|-|-|-|-|-|-|____________________
paddr       :  CTRL DIM0 DIM1 IADDR WADDR OADDR BIAS SCALE IRQEN CMD

controller  :
  state     : IDLE        READ_DIMS LOAD_W LOAD_A COMPUTE(64+2) STORE_OUT(64) DONE IDLE
  status_busy:0           1         1      1      1             1             0    0
  status_done:0                                                          1     0
  irq       :0                                                           ^     0
  weight_load_en:                 ^
  activation_load_en:                          ^
  compute_en:                                        ^^^^^^^^^^^^^^^^^^^
  store_out :                                                            ^^^^^^^

(Clock numbers are illustrative; the exact compute length depends on k_cur.)
```

Legend:
- `S A` = APB setup/access phases (one write = two PCLK cycles).
- `^` = single-cycle pulse.
- `COMPUTE` length = `k_cur + 2` cycles to flush the PE pipeline.
- `STORE_OUT` length = `m_cur` cycles (one row per cycle).

### 2.5 Abort Behavior

Writing `CMD = 0x2` (ABORT=1) at any time forces the controller to `IDLE` on
the next cycle, clears `STATUS.BUSY`, and sets `STATUS.ERROR` for one cycle.
Partial results already written to SRAM remain unchanged.

---

## 3. SFU — Special Function Unit

### 3.1 Register Map (BASE = `0x4000_1000`)

| Offset | Name    | Access | Bit Fields |
|-------:|---------|:------:|------------|
| `0x00` | CTRL    | R/W    | [3:0]=OP: `0=SOFTMAX`, `1=LAYERNORM`, `2=GELU`, `3=RELU`, `4=SILU`, `5=ROPE`, `6=RMSNORM` |
| `0x04` | CMD     | W      | [0]=START |
| `0x08` | STATUS  | R      | [0]=BUSY, [1]=DONE |
| `0x0C` | I_ADDR  | R/W    | Input SRAM byte address |
| `0x10` | O_ADDR  | R/W    | Output SRAM byte address |
| `0x14` | DIM     | R/W    | [15:0]=element count, [31:16]=head_dim (ROPE only) |
| `0x18` | POS     | R/W    | Position index (ROPE only) |
| `0x1C` | IRQ_EN  | R/W    | [0]=completion IRQ enable |

### 3.2 SFU Op Register Write Sequence

All SFU ops follow the same pattern. ROPE uses the extra `POS` register.

**Generic SFU op (SOFTMAX, LAYERNORM, GELU, RELU, SILU, RMSNORM):**

```
1. CTRL   ← OP code
2. I_ADDR ← input SRAM byte address
3. O_ADDR ← output SRAM byte address
4. DIM    ← element count (and head_dim for ROPE)
5. IRQ_EN ← 1 (optional)
6. CMD    ← 1   // START pulse
```

**ROPE only:** add `POS` write between `DIM` and `IRQ_EN`:

```
5. POS     ← position index
6. IRQ_EN  ← 1
7. CMD     ← 1
```

- Minimum pre-START time: **5 writes = 10 PCLK cycles** (generic), or
  **6 writes = 12 PCLK cycles** (ROPE).
- `STATUS.BUSY` rises on the cycle after `CMD.START` and stays high until the
  pipeline has drained and the last output element has been written to SRAM.
- `STATUS.DONE` is a single-cycle pulse; `irq` is asserted simultaneously if
  `IRQ_EN=1`.

---

## 4. Vector Engine

### 4.1 Register Map (BASE = `0x4000_2000`)

| Offset | Name   | Access | Bit Fields |
|-------:|--------|:------:|------------|
| `0x00` | CTRL   | R/W    | [3:0]=OP: `0=ADD`, `1=MUL`, `2=MAX`, `3=SUM`, `4=CONV`, `5=RESID` |
| `0x04` | CMD    | W      | [0]=START |
| `0x08` | STATUS | R      | [0]=BUSY, [1]=DONE |
| `0x0C` | A_ADDR | R/W    | Operand A SRAM byte address |
| `0x10` | B_ADDR | R/W    | Operand B SRAM byte address (ignored for unary ops) |
| `0x14` | O_ADDR | R/W    | Output SRAM byte address |
| `0x18` | DIM    | R/W    | [15:0]=element count |
| `0x1C` | IRQ_EN | R/W    | [0]=completion IRQ enable |

### 4.2 Vector Op Register Write Sequence

**Binary ops (VADD, VMUL, VMAX, VRESID):**

```
1. CTRL   ← OP code
2. A_ADDR ← operand A SRAM byte address
3. B_ADDR ← operand B SRAM byte address
4. O_ADDR ← output SRAM byte address
5. DIM    ← element count
6. IRQ_EN ← 1 (optional)
7. CMD    ← 1   // START pulse
```

**Unary ops (VRED_MAX, VRED_SUM, VCONV, VCONV_F16_I32):**

```
1. CTRL   ← OP code
2. A_ADDR ← source SRAM byte address
3. O_ADDR ← destination SRAM byte address
4. DIM    ← element count
5. IRQ_EN ← 1 (optional)
6. CMD    ← 1   // START pulse
```

- Minimum pre-START time: **6 writes = 12 PCLK cycles** (binary), or
  **5 writes = 10 PCLK cycles** (unary).
- `B_ADDR` is ignored by unary ops but can be written safely.
- `STATUS.BUSY`/DONE/irq timing matches the SFU pattern.

---

## 5. DMA Engine

### 5.1 Register Map (BASE = `0x4000_3000`)

| Offset | Name      | Access | Bit Fields |
|-------:|-----------|:------:|------------|
| `0x00` | CTRL      | R/W    | [0]=linked_list_en, [1:2]=channel_mode |
| `0x04` | CMD       | W      | [0]=START, [1]=ABORT |
| `0x08` | STATUS    | R      | [0]=BUSY, [1]=DONE, [7:4]=active_channel |
| `0x10` | CH0_SRC   | R/W    | DRAM source address (load) |
| `0x14` | CH0_DST   | R/W    | SRAM destination address (load) |
| `0x18` | CH0_SIZE  | R/W    | Transfer bytes for channel 0 |
| `0x1C` | CH0_STRIDE| R/W    | 2D stride (reserved) |
| `0x20` | CH1_SRC   | R/W    | SRAM source address (store) |
| `0x24` | CH1_DST   | R/W    | DRAM destination address (store) |
| `0x28` | CH1_SIZE  | R/W    | Transfer bytes for channel 1 |
| `0x2C` | CH1_STRIDE| R/W    | 2D stride (reserved) |
| `0x30` | DESC_ADDR | R/W    | Descriptor chain base (linked-list mode) |
| `0x34` | DESC_CNT  | R/W    | Descriptor count |
| `0x38` | IRQ_EN    | R/W    | [0]=completion IRQ enable |

### 5.2 DMA_LD Register Write Sequence (DRAM → SRAM)

```
1. CTRL     ← 0x0  // simple mode, linked_list_en=0
2. CH0_SRC  ← DRAM byte address (must be ≥ 0x8000_0000)
3. CH0_DST  ← SRAM byte address (must be ≥ 0x2000_0000)
4. CH0_SIZE ← number of bytes to transfer
5. IRQ_EN   ← 1 (optional)
6. CMD      ← 1   // START pulse
```

- Minimum pre-START time: **5 writes = 10 PCLK cycles**.
- `STATUS.BUSY` rises on the cycle after START and falls when the AXI read
  burst completes and the last write response is received.
- `STATUS.DONE` pulses for one cycle at completion; `irq` fires if `IRQ_EN=1`.
- `STATUS.DONE` clears on read (hardware auto-clear on `STATUS` read).

### 5.3 DMA_ST Register Write Sequence (SRAM → DRAM)

```
1. CTRL     ← 0x0
2. CH1_SRC  ← SRAM byte address
3. CH1_DST  ← DRAM byte address
4. CH1_SIZE ← number of bytes to transfer
5. IRQ_EN   ← 1 (optional)
6. CMD      ← 1   // START pulse
```

- Minimum pre-START time: **5 writes = 10 PCLK cycles**.
- Channel 1 is used for store even though it is nominally the "output" channel;
  the wrapper auto-submits CH1 after CH0 if both sizes are non-zero.

### 5.4 Combined Load + Store Sequence

If both `CH0_SIZE` and `CH1_SIZE` are non-zero when `CMD.START` is written, the
DMA wrapper submits CH0 first, waits for completion, then submits CH1, and
asserts DONE only after both finish. Firmware can therefore chain a load and a
store with a single START pulse.

---

## 6. Interrupt Sequence

All engines share the same completion-interrupt protocol:

1. Firmware writes `IRQ_EN = 1` before `CMD.START`.
2. Engine completes the op and asserts its `irq` output for **one PCLK cycle**
   in the DONE state.
3. The interrupt controller (`INTC`, base `0x4000_6000`) latches the event in
   `INTC.PENDING` (full 8-source SoC map, `rtl/intc/intc_top.v`):
   - bit[0] = MXU done
   - bit[1] = SFU done
   - bit[2] = Vector done
   - bit[3] = DMA done
   - bit[4] = PCIe
   - bit[5] = HOST doorbell
   - bit[6] = Timer
   - bit[7] = PCIe DMA
4. The CPU interrupt `cpu_irq` is asserted when
   `popcount(PENDING & ENABLE) ≥ THRESHOLD` (default `THRESHOLD = 1`).
5. Firmware interrupt handler:
   - Reads `INTC.PENDING` to identify the source(s).
   - Reads the engine `STATUS` register (this auto-clears DMA `DONE`).
   - Writes `INTC.ACK` with the matching bit(s) to clear the pending flag.

> **RTL implication:** Engine `irq` must be a single-cycle pulse. `INTC.PENDING`
> is level-set by the pulse and cleared only by `INTC.ACK`. The engine itself
> does not require a DONE clear write; for MXU/SFU/Vector, DONE is
> self-clearing after one cycle.

---

## 7. Summary Tables

### 7.1 Register Writes per Op Type

| Op Type | Registers Written (order) | Min Writes | Min Pre-START Cycles |
|---------|---------------------------|-----------:|---------------------:|
| MMUL    | CTRL, DIM0, DIM1, I_ADDR, W_ADDR, O_ADDR, BIAS_ADDR, SCALE_ADDR, IRQ_EN, CMD | 10 | 18 |
| SFU     | CTRL, I_ADDR, O_ADDR, DIM, IRQ_EN, CMD | 6 | 10 |
| SFU ROPE| CTRL, I_ADDR, O_ADDR, DIM, POS, IRQ_EN, CMD | 7 | 12 |
| Vector binary | CTRL, A_ADDR, B_ADDR, O_ADDR, DIM, IRQ_EN, CMD | 7 | 12 |
| Vector unary  | CTRL, A_ADDR, O_ADDR, DIM, IRQ_EN, CMD | 6 | 10 |
| DMA_LD  | CTRL, CH0_SRC, CH0_DST, CH0_SIZE, IRQ_EN, CMD | 6 | 10 |
| DMA_ST  | CTRL, CH1_SRC, CH1_DST, CH1_SIZE, IRQ_EN, CMD | 6 | 10 |

### 7.2 STATUS Bits

| Engine | BUSY | DONE | ERROR | DONE Clear |
|--------|:--:|:--:|:--:|:---|
| MXU    | [0] | [1] | [2] | Self-clearing after 1 cycle |
| SFU    | [0] | [1] | —   | Self-clearing after 1 cycle |
| Vector | [0] | [1] | —   | Self-clearing after 1 cycle |
| DMA    | [0] | [1] | —   | Clears on read of STATUS |

### 7.3 Minimum Between-Write Delay

| Scenario | Minimum Delay |
|----------|--------------:|
| Consecutive writes to same engine | 2 PCLK cycles |
| Consecutive writes to different engines | 2 PCLK cycles (APB decoder still 2-cycle per transfer) |
| Write → read STATUS poll | 2 PCLK cycles |

---

## 7. Errata / Known Gaps

### 7.1 INTC HOST Doorbell Bit Position

**Spec says**: Section 6 lists HOST doorbell at `PENDING` bit[8].

**RTL implements** (`rtl/intc/intc_top.v` lines 76-77): The 8-source SoC interrupt map packs HOST doorbell at bit[5]:
```
assign irq_src = {pcie_dma_irq, timer_irq, host_irq, pcie_irq, dma_irq,
                  vector_irq, sfu_irq, mxu_irq};
```
- bit[5] = host_irq (HOST doorbell)
- bit[4] = pcie_irq
- bit[3] = dma_irq
- bit[6] = timer_irq
- bit[7] = pcie_dma_irq

**Status**: RESOLVED in docs (rtl-update-plan Phase 10). Section 6 above now
lists the full 8-source map matching `intc_top.v` (HOST at bit[5], PCIe at
bit[4], Timer at bit[6], PCIe DMA at bit[7]). No RTL change was required; the
RTL was already correct. The earlier spec bit map in Section 6 (HOST at bit[8])
was the documentation gap, now corrected.

### 7.2 MXU BIAS/SCALE Unimplemented in Phase 1

**Spec says**: Section 2.1 defines `BIAS_ADDR` (offset `0x20`) and `SCALE_ADDR` (offset `0x24`) as active R/W registers in the MXU register map. Section 2.2 includes them in the MMUL register write sequence.

**RTL implements** (`rtl/mxu/mxu_top.v` lines 104-108): `bias_addr_o` and `scale_addr_o` are declared as outputs from `mmio_if` but annotated "unused (stubbed)" at the MXU top level. The controller FSM does not consume these values in Phase 1.

**Status**: **Phase 1: NOT APPLICABLE** (decision recorded in
`.omo/plans/rtl-update-plan.md` §1.1/§3.4/§11). The MMIO registers exist and
are writable (`rtl/mxu/mmio_if.v` offsets `0x20`/`0x24`), but no functional
path consumes them in Phase 1. `mxu_top.v` ties off `bias_addr_o` and
`scale_addr_o` as unused, and the controller FSM never reads them. This is
acceptable because Phase 1 module-level testbenches drive the broadcast buses
directly and bypass the MMIO path, and no golden reference in the Phase 1 op
set requires bias/scale application. BIAS/SCALE consumption will be wired in a
future phase when the controller sequences weight scale and bias application
during the compute loop.

---

## 8. References

- Register offsets and fields: `sim/regmap.py`
- MXU register file / APB-ready behavior: `rtl/mxu/mmio_if.v`
- MXU controller FSM: `rtl/mxu/controller.v`
- SFU/Vector MMIO maps and op encodings: `rtl/sfu/README.md`, `rtl/vector/README.md`
- DMA APB slave and descriptor FSM: `rtl/ip/dma_wrapper.v`
- APB decoder / address map: `rtl/soc/apb_decoder.v`, `rtl/soc/README.md`
- Interrupt controller: `rtl/intc/intc_top.v`
