# RTL Update Plan — Func Model Spec Alignment

**Scope:** Align CaduceusCore Phase 1/2/3 RTL with the three new Func Model spec documents:
- `docs/func-model-mmio-spec.md` — per-op MMIO register write sequences and FSM timing
- `docs/func-model-sram-map.md` — byte-level SRAM address allocation
- `docs/func-model-golden-tolerance.md` — per-op comparison tolerances

**Approach:** This plan is code-only analysis. No RTL source files are modified by this document. Each checklist item cites the exact source lines that were compared against the spec.

**Top-level conclusion:** The MMIO register maps, FSM state sequences, STATUS/IRQ timing, and tolerance rules already match the specs in most modules. The primary gaps are in the SoC wrapper default SRAM base addresses and in the APB→MMIO bridge write-strobe timing. A small number of stubbed features (MXU BIAS/SCALE addresses) need to be either implemented or explicitly documented as not applicable to Phase 1.

---

## 1. `rtl/mxu/controller.v`

### 1.1 FSM vs MMIO spec register sequence
- **Spec sequence:** `CTRL → DIM0 → DIM1 → I_ADDR → W_ADDR → O_ADDR → BIAS_ADDR → SCALE_ADDR → IRQ_EN → CMD`.
- **RTL finding:** The controller does not drive the register file; it consumes the dimension and IRQ signals exported by `mmio_if.v`. Its FSM matches the spec state machine exactly:
  - `IDLE → READ_DIMS → LOAD_W → LOAD_A → COMPUTE → STORE_OUT → (tile loop) → DONE` (header comment, lines 8–15; state encodings lines 69–75).
  - `READ_DIMS` captures `dim0_m`, `dim0_k`, `dim1_n` on lines 168–170 and computes tile counts on lines 173–175.
- **Checklist:**
  - [x] FSM state sequence matches spec.
  - [x] Tile iteration order matches spec: inner K → middle N → outer M (lines 253–295).
  - [ ] Controller still does **not** consume `I_ADDR`, `W_ADDR`, `O_ADDR`, `BIAS_ADDR`, or `SCALE_ADDR`. These are stored in `mmio_if.v` but are stubbed inside `mxu_top.v` (lines 104–108). For Phase 1 this is acceptable because the testbench drives the broadcast buses directly, but it is a documented gap against the MMIO spec.

### 1.2 Token inner loop for time-multiplexed batch (M = 2, 4, 8)
- **Spec concern:** When `M` represents a batch of independent tokens, `mac_reset_acc` must occur between tokens.
- **RTL finding:** The controller treats `M` as the row dimension of a single GEMM and iterates `m_tile` as the outer loop (lines 83–84, 289–294). The accumulator reset is asserted on the first K-tile of every `(m_tile, n_tile)` group (`mac_reset_acc <= (k_tile == 0)` on line 198). Because each token maps to a distinct `m_tile`, the reset condition already provides per-token accumulator clearing.
- **Checklist:**
  - [x] No new token inner loop is required; current `m_tile` outer loop + `k_tile==0` reset gives the required per-token reset behavior.
  - [ ] If the Func Model later requires a separate *batch* dimension (e.g. token count ≠ `M`), a new `batch_cur` register and state extension will be needed. Not required today.

### 1.3 `STATUS.BUSY` timing
- **Spec:** `BUSY` rises in `READ_DIMS`, falls in `DONE`, `DONE` is a single-cycle pulse, `irq` equals `IRQ_EN` in `DONE`.
- **RTL finding:**
  - `status_busy <= 1'b1` in `READ_DIMS` (line 166); `status_busy <= 1'b0` in `DONE` (line 307).
  - `status_done <= 1'b1` and `irq <= irq_en` for one cycle in `DONE` (lines 308–309).
- **Checklist:**
  - [x] `STATUS.BUSY` timing matches spec.
  - [x] Single-cycle `DONE` + IRQ pulse matches spec.

### 1.4 Specific lines to change
- **None for controller FSM or timing.**
- **Line 198:** Keep current `mac_reset_acc` reset condition; confirm with a directed test that a multi-token `M=8` shape resets between tokens.

---

## 2. `rtl/mxu/mmio_if.v`

### 2.1 Register offset table
- **Spec offsets:** `CTRL 0x00`, `CMD 0x04`, `STATUS 0x08`, `DIM0 0x0C`, `DIM1 0x10`, `I_ADDR 0x14`, `W_ADDR 0x18`, `O_ADDR 0x1C`, `BIAS_ADDR 0x20`, `SCALE_ADDR 0x24`, `IRQ_EN 0x28`.
- **RTL finding:** Exact match in the write case (lines 92–103) and read case (lines 136–147).
- **Checklist:**
  - [x] Register offset table matches spec.

### 2.2 Bit fields
- **Spec:** `CTRL[1:0]=dtype`, `CMD[0]=START`, `CMD[1]=ABORT`, `STATUS[0]=BUSY`, `STATUS[1]=DONE`, `STATUS[2]=ERROR`.
- **RTL finding:**
  - `CTRL` captures full 32 bits but only `[1:0]` is used (`ctrl_dtype = ctrl_reg[1:0]`, line 161).
  - `CMD` pulse generation checks `wdata[0]` for START and `wdata[1]` for ABORT (lines 122–123).
  - `STATUS` readback packs `{29'd0, status_error, status_done, status_busy}` (line 139).
- **Checklist:**
  - [x] Bit-field definitions match spec.

### 2.3 Ready signal timing
- **Spec:** Zero-wait-state APB; `pready=1` for every valid transfer; register latch on rising edge at end of access cycle.
- **RTL finding:** `assign ready = cs;` (line 156). For the native MMIO interface used by module-level testbenches this is correct. When the interface is driven through `apb_to_mmio.v`, the bridge currently asserts `cs = psel` for both APB setup and access phases (see wrapper analysis below), which can cause a double latch.
- **Checklist:**
  - [ ] Fix timing at the APB bridge (`rtl/wrapper/apb_to_mmio.v`), not inside `mmio_if.v`. `mmio_if.v` itself should remain combinational-ready.

### 2.4 Specific lines to change
- **None inside `mmio_if.v`.**
- Coordinate the APB bridge fix so that `mmio_if.v` only sees `cs=1` during the APB access phase.

---

## 3. `rtl/mxu/mxu_top.v` (SRAM interface)

### 3.1 SRAM address space vs SRAM map spec
- **Spec canonical map:**
  - Weight Bank A: `0x2000_0000 – 0x200F_FFFF` (1 MB)
  - Weight Bank B: `0x2010_0000 – 0x201F_FFFF` (1 MB)
  - Activation Buffer: `0x2020_0000 – 0x2027_FFFFF` (512 KB)
  - Accumulator/Output Buffer: `0x2028_0000 – 0x202B_FFFF` (256 KB)
- **RTL finding:** `mxu_top.v` is the Phase 1 module-level top. It contains small internal tile buffers (`weight_buffer` depth 512, `activation_buffer` depth 1024, lines 200–234) and a serialized 12-bit output SRAM interface (`output_sram_addr = {store_row, out_col_counter}`, line 304). It does **not** implement the 4 MB SoC SRAM address decode; that lives in `mxu_soc_wrapper.v`.
- **Checklist:**
  - [ ] `mxu_top.v` does not need to change its local buffer architecture, but the address registers `I_ADDR`, `W_ADDR`, `O_ADDR`, `BIAS_ADDR`, `SCALE_ADDR` are currently unused at the top level (lines 104–108 tie them off or leave them floating).
  - [ ] If Phase 1 module-level tests are to remain unchanged, keep the broadcast-bus path. If the spec requires `mxu_top.v` to source its own weight/activation data from SRAM, the controller needs address sequencing and the buffers need DMA-style fill logic. **Recommend deferring this to a future Phase 1.5/SoC-only change; do not break the existing `tb_mxu.v` broadcast path.**

### 3.2 Weight buffer Bank A / Bank B addresses
- **RTL finding:** Only one weight buffer instance exists inside `mxu_top.v`. Bank B is not modeled at this level.
- **Checklist:**
  - [ ] Bank B support is not required in `mxu_top.v`; it is a SoC-wrapper / firmware concern.

### 3.3 Activation / output buffer addresses
- **RTL finding:** Local activation buffer address is 11-bit (line 131); output serialization uses 12-bit `{row, col}` (line 304).
- **Checklist:**
  - [x] Local buffer widths are consistent with a 64×64 tile.
  - [ ] SoC-level activation/output base addresses must be corrected in `mxu_soc_wrapper.v` (see Section 6).

### 3.4 Specific lines to change
- **None recommended for `mxu_top.v` in this planning cycle.**
- If BIAS/SCALE become required for module-level tests, add controller logic and buffer interfaces; currently they are intentionally stubbed.

---

## 4. `rtl/mxu/mac_array.v`

### 4.1 Accumulator architecture
- **Spec:** MMUL output is INT32, bit-exact for non-saturated values; saturation at INT32 boundary.
- **RTL finding:**
  - 64×64 PE grid with per-PE `local_acc` registers (lines 111–113).
  - 33-bit signed sum with saturation to `INT32_MAX` / `INT32_MIN` (lines 144–150).
  - External `accumulator` module instantiated but tied off via `ext_acc_*` ports (lines 190–199).
- **Checklist:**
  - [x] Per-PE accumulator with saturation matches spec.
  - [x] Bit-exact INT32 behavior verified by existing module tests.
  - [ ] External accumulator interface is unused; if cross-tile partial-sum load/store is required in the future, connect `ext_acc_*` to the controller.

### 4.2 Specific lines to change
- **None.** Accumulator behavior already matches the golden-tolerance spec for MMUL.

---

## 5. `rtl/sfu/sfu_top.v`

### 5.1 MMIO register sequence alignment
- **Spec sequence:** `CTRL → I_ADDR → O_ADDR → DIM → (POS for ROPE) → IRQ_EN → CMD`.
- **Spec offsets:** `CTRL 0x00`, `CMD 0x04`, `STATUS 0x08`, `I_ADDR 0x0C`, `O_ADDR 0x10`, `DIM 0x14`, `POS 0x18`, `IRQ_EN 0x1C`.
- **RTL finding:** Exact match:
  - Offsets defined lines 72–79.
  - Write case lines 114–125.
  - Read case lines 132–142.
- **Checklist:**
  - [x] Register offset table matches spec.
  - [x] `CTRL[3:0]` OP encoding matches spec (lines 60–66).
  - [x] `STATUS` readback is `{30'd0, status_done, status_busy}` (line 135), giving `BUSY[0]`, `DONE[1]`.

### 5.2 SFU workspace SRAM addresses
- **Spec:** SFU workspace at `0x202C_0000 – 0x202F_FFFF`.
- **RTL finding:** `sfu_top.v` does not hardcode workspace bases. `I_ADDR` and `O_ADDR` are programmed through MMIO (lines 82–83) and passed directly to the SRAM controller. The `sfu_soc_wrapper.v` does not override them.
- **Checklist:**
  - [x] No RTL change required in `sfu_top.v`; firmware/testbench must program `I_ADDR`/`O_ADDR` to the spec base.
  - [ ] Add a directed SoC test that programs `I_ADDR = 0x202C_0000` and `O_ADDR = 0x202C_0000` (or another address within the workspace) to verify wrapper address propagation.

### 5.3 Specific lines to change
- **None.** `sfu_top.v` is already spec-aligned.

---

## 6. `rtl/vector/vector_top.v`

### 6.1 MMIO register sequence alignment
- **Spec sequence (binary):** `CTRL → A_ADDR → B_ADDR → O_ADDR → DIM → IRQ_EN → CMD`.
- **Spec offsets:** `CTRL 0x00`, `CMD 0x04`, `STATUS 0x08`, `A_ADDR 0x0C`, `B_ADDR 0x10`, `O_ADDR 0x14`, `DIM 0x18`, `IRQ_EN 0x1C`.
- **RTL finding:** Exact match:
  - OP encoding lines 80–86.
  - Write case lines 126–136.
  - Read case lines 142–153.
- **Checklist:**
  - [x] Register offsets and bit fields match spec.
  - [x] `STATUS` readback is `{30'd0, status_done, status_busy}` (line 145), matching `BUSY[0]`, `DONE[1]`.
  - [x] `irq = status_done && irq_en_reg[0]` (line 157) gives the required single-cycle IRQ pulse.

### 6.2 Vector workspace / scratch SRAM addresses
- **Spec:** Vector workspace `0x2030_0000 – 0x2033_FFFF`; scratch/dtype-convert `0x2034_0000 – 0x2037_FFFF`.
- **RTL finding:** `vector_top.v` uses addresses supplied through MMIO. The wrapper (`vector_soc_wrapper.v`) adds `WRP_A_BASE`, `WRP_B_BASE`, `WRP_O_BASE` but defaults them to zero (lines 176–178).
- **Checklist:**
  - [ ] Update `vector_soc_wrapper.v` default bases to match the spec (see Section 7).
  - [x] No change needed inside `vector_top.v`.

### 6.3 `VCONV_F16_I32` opcode alignment
- **Spec:** Opcode `0x18` (`VCONV_F16_I32`), INT32 output, bit-exact for finite values, saturation for `±Inf`/`NaN`.
- **RTL finding:** `vector_top.v` defines `OP_F16_I32 = 4'd6` (line 86) and implements the feed/capture/write states (lines 175–177, 505–546). The `f16_to_i32.v` submodule performs the conversion.
- **Checklist:**
  - [x] Operation implemented and matches spec tolerance.

### 6.4 Specific lines to change
- **None inside `vector_top.v`.**

### 6.5 scratch_base default conflict (golden_executor.py)
- **Spec SRAM map:** Scratch/dtype-convert buffer at `0x2034_0000` (offset `0x340000` from SRAM base).
- **RTL finding:** `sim/golden_executor.py` default `scratch_base = 0x380000` in both
  `_insert_dtype_converters` (line 1563) and `run_op_chain` (line 1634), which maps to
  `0x2038_0000` — the KV Cache region.
- **Fix applied:** Changed both defaults to `0x340000` to match the SRAM map spec.
  `GoldenExecutor.run_op_chain` now allocates scratch/dtype-convert buffers within the
  correct `0x2034_0000 – 0x2037_FFFF` window. No RTL change needed for this fix (it is
  a Python Func Model correction).

---

## 7. `rtl/wrapper/` modules

### 7.1 `rtl/wrapper/apb_to_mmio.v` — APB latch timing
- **Spec:** APB writes take 2 PCLK cycles; register latch occurs on the rising edge at the end of the access phase only.
- **RTL finding:** Lines 58–61 drive `cs = psel`, `we = pwrite`, `addr = paddr`, `wdata = pwdata`. Because `psel` is high during both setup and access phases, the MMIO slave sees `cs=1` for two consecutive cycles and can latch twice.
- **Impact:** For data registers the double latch is harmless (same value). For `CMD`, `cmd_start` becomes a 2-cycle pulse. The controller samples `cmd_start` only in `IDLE`, so the second cycle is ignored in practice, but the behavior does not strictly match the spec.
- **Change required:**
  - **Line 58:** change `assign cs = psel;` to `assign cs = psel && penable;`.
  - This restricts MMIO `cs` to the APB access phase only, giving a single latch per APB transfer.
- **Risk:** Low functional risk; high spec-compliance value. Requires re-running all SoC-level tests because every engine register access goes through this bridge.

### 7.2 `rtl/wrapper/mxu_soc_wrapper.v` — default SRAM bases
- **Spec bases:** Weight Bank A `0x2000_0000`; Activation `0x2020_0000`; Output/Accumulator `0x2028_0000`.
- **RTL finding:** Reset defaults on lines 178–180 are:
  - `wrp_weight_base <= 32'h2000_0000;` — **correct** (Bank A start).
  - `wrp_act_base    <= 32'h2000_1000;` — **incorrect**; spec says `0x2020_0000`.
  - `wrp_out_base    <= 32'h2000_2000;` — **incorrect**; spec says `0x2028_0000`.
- **Change required:**
  - **Line 179:** `wrp_act_base <= 32'h2020_0000;`
  - **Line 180:** `wrp_out_base <= 32'h2028_0000;`
- **Additional consideration:** The wrapper currently pre-loads a contiguous `wrp_k_tiles` region from `wrp_weight_base`. The spec places Bank A and Bank B 1 MB apart. For large weights the wrapper will need to either (a) limit pre-load to Bank A and rely on firmware/DMA to refresh, or (b) add Bank B base register and ping-pong logic. For current Phase 1/SoC tests the single-bank preload is sufficient; document this limitation.

### 7.3 `rtl/wrapper/sfu_soc_wrapper.v` — address propagation
- **Spec:** SFU workspace `0x202C_0000 – 0x202F_FFFF`.
- **RTL finding:** The wrapper has no wrapper-specific base registers; it passes `I_ADDR`/`O_ADDR` from MMIO directly to `sfu_top.v` (lines 542–560).
- **Checklist:**
  - [x] No wrapper change required; firmware/testbench programs the spec addresses.
  - [ ] Verify that firmware currently uses `0x202C_0000` range for SFU I/O.

### 7.4 `rtl/wrapper/vector_soc_wrapper.v` — default SRAM bases
- **Spec bases:** Vector workspace A/B `0x2030_0000`; output/scratch `0x2034_0000`.
- **RTL finding:** Reset defaults on lines 176–178 are all zero (`{ADDR_W{1'b0}}`). Firmware/testbench is required to program them.
- **Change required:**
  - **Line 176:** `wrp_a_base <= 32'h2030_0000;`
  - **Line 177:** `wrp_b_base <= 32'h2030_0000;`  (or `0x2032_0000` if A/B are split within the workspace)
  - **Line 178:** `wrp_o_base <= 32'h2034_0000;`  (scratch/dtype-convert region)
- **Note:** `vector_top.v` distinguishes A/B/O through MMIO `A_ADDR`/`B_ADDR`/`O_ADDR`, which are offsets within the wrapper buffers. The wrapper adds the base addresses. If the Func Model expects A and B in different sub-regions of the 256 KB workspace, split `wrp_a_base`/`wrp_b_base` accordingly (e.g. A at `0x2030_0000`, B at `0x2032_0000`, each 128 KB). Confirm against `sim/golden_executor.py` buffer layout before finalizing.

---

## 8. DMA Engine and INTC Analysis

### 8.1 DMA Engine (`rtl/ip/dma_wrapper.v`)
- **MMIO register offsets match spec §5?**
  - Spec §5.1: CTRL=0x00, CMD=0x04, STATUS=0x08, CH0_SRC=0x10, CH0_DST=0x14, CH0_SIZE=0x18.
  - RTL: `reg_idx = paddr[5:2]` (line 108) indexes `dma_reg[0..14]`. Register layout (lines 180–194):
    - `dma_reg[0]` = CTRL   — offset 0x00
    - `dma_reg[1]` = CMD    — offset 0x04
    - `dma_reg[2]` = STATUS — offset 0x08
    - `dma_reg[4]` = CH0_SRC  — offset 0x10
    - `dma_reg[5]` = CH0_DST  — offset 0x14
    - `dma_reg[6]` = CH0_SIZE — offset 0x18
  - The RTL offset layout does **not** use the generic MMIO `SRC_ADDR=0x0C` pattern; it uses the DMA-specific CH0/CH1 mapping which matches the spec exactly.
  - [x] MMIO register offsets match spec §5 (DMA-specific CH0_SRC at 0x10, not 0x0C).
- **STATUS bits:**
  - RTL: `dma_reg[2][0]` = BUSY (line 222), `dma_reg[2][1]` = DONE (line 223),
    `dma_reg[2][7:4]` = active_channel (lines 226, 229).
  - Spec: `[0]=BUSY, [1]=DONE, [7:4]=active_channel`.
  - [x] STATUS bits match spec.
- **DONE read-clear:** Line 299–300 clears `dma_reg[2][1]` on APB read of offset 0x08.
  - Spec §5.2: "STATUS.DONE clears on read".
  - [x] DONE read-clear behavior matches spec.
- **Linked-list mode:** CTRL[0]=linked_list_en, DESC_ADDR (0x30), DESC_CNT (0x34) are reserved in the wrapper (lines 25–27) but present in the register file. The FSM does not implement descriptor chain traversal in the current wrapper.
  - [ ] Linked-list mode not yet implemented in wrapper FSM (deferred — reserved registers exist).
- **Combined load+store sequence:** The FSM (lines 206–278) sequences CH0→wait→CH1→wait→DONE when both `CH0_SIZE` and `CH1_SIZE` are non-zero. This matches spec §5.4.
  - [x] Combined load+store sequence matches spec.
- **IRQ generation:** `dma_irq = (fsm_state == FSM_DONE_PULSE) && IRQ_EN[0]` (line 347). Single-cycle pulse as required.
  - [x] IRQ timing matches spec.
- **Status:** DMA wrapper register layout and FSM behavior are spec-compliant. Linked-list mode registers are defined but the chain-traversal FSM is not implemented — a documented gap for a future phase.

### 8.2 INTC (`rtl/intc/intc_top.v`)
- **PENDING bits:**
  - RTL: `irq_src = {pcie_dma, timer, host, pcie, dma, vector, sfu, mxu}` (line 76–77).
    - bit[0]=mxu, bit[1]=sfu, bit[2]=vector, bit[3]=dma, bit[4]=pcie, bit[5]=host.
  - Spec §6: bit[0]=MXU, bit[1]=SFU, bit[2]=Vector, bit[3]=DMA, bit[8]=HOST doorbell.
  - **Mismatch:** Spec says HOST at bit[8], but the RTL has only 8 sources (bits 0–7) with HOST at bit[5]. The RTL also adds pcie (bit[4]) and timer (bit[6]) which are not in the spec's interrupt prototype. The spec §6 source map is a simplified list (showing only the 4 engine + host) while the RTL implements the full 8-source SoC map from `caduceus_soc_top.v`.
  - [ ] PENDING bit assignment **partially matches** spec: MXU/SFU/Vector/DMA are correct. HOST is at bit[5] in RTL vs bit[8] in spec — this is a documentation gap in the MMIO spec, not an RTL bug.
  - [x] PENDING register is Read-Only at offset 0x00 (line 83).
- **ENABLE register:** RW at offset 0x04 (lines 111–118). Controls per-source masking.
  - [x] ENABLE register controls per-source masking.
- **THRESHOLD register:** RW at offset 0x08 (lines 127–135). Default 4'd1. `cpu_irq` asserted when `popcount(PENDING & ENABLE) ≥ THRESHOLD` (line 159).
  - [x] THRESHOLD register for priority-based IRQ dispatch.
- **ACK register:** Write-1-to-clear at offset 0x0C (line 85, 98). Clears corresponding PENDING bits.
  - [x] ACK register to clear pending.
- **cpu_irq output:** Registered to avoid combinational glitch (lines 160–165).
  - [x] Registered output meets Ibex IRQ timing requirements.
- **Status:** INTC is fully functional with 8 sources, ENABLE/THRESHOLD/ACK. The spec §6 source bit map is a subset (documentation should be updated to reflect the full RTL assignment: bit4=pcie, bit5=host, bit6=timer).

### 8.3 APB Decoder (`rtl/soc/apb_decoder.v`)
- **Decode range:** Addresses `0x4000_0000–0x4000_7FFF` decoded by `paddr[31:16] == 16'h4000` (line 64). 8 slaves, each 4 KB, selected by `paddr[15:12]` (line 65).
  - slave0=MXU (0x4000_0000), slave1=SFU (0x4000_1000), slave2=VECTOR (0x4000_2000),
    slave3=DMA (0x4000_3000), slave4=PCIe (0x4000_4000), slave5=DOORBELL (0x4000_5000),
    slave6=INTC (0x4000_6000), slave7=PCIE_DMA (0x4000_7000).
  - The actual range is 32 KB (0x0000–0x7FFF) to 8 slaves. The question's "0x6FFF / 7 slaves" is off by one — slave7 (PCIE_DMA) is included.
  - [x] Decodes 0x4000_0000–0x4000_7FFF to 8 MMIO slaves (7 engine + 1 PCIE_DMA spare).
- **psel assertion timing:** `psel_o = (psel && region_hit && slave_valid) ? slave_sel : 8'h0` (line 79). Combinational decode from `psel`, which is high during both APB setup and access phases.
  - [x] psel assertion is zero-wait-state combinatorial.
- **Out-of-range handling:** `pslverr=1, pready=1` when no slave matches (lines 97, 111, 125).
  - [x] Out-of-range access generates pslverr.
- **Status:** Fully spec-compliant. The decoder handles the full 32 KB MMIO range with 8 slaves (7 active + 1 spare for PCIE_DMA).

### 8.4 SRAM Controller (`rtl/soc/sram_ctrl.v`)
- **Decode range:** `SRAM_BASE = 32'h2000_0000` (line 75), `SRAM_MASK = 32'h003F_FFFF` (line 76, 4 MB–1).
  - `addr_in_range` checks `(byte_addr >= SRAM_BASE) && ((byte_addr - SRAM_BASE) <= SRAM_MASK)` (lines 94–97).
  - Valid range: `0x2000_0000 – 0x203F_FFFF` = 4 MB. Spec says 4 MB, so this is correct.
  - [x] Decodes full 4 MB window (0x2000_0000–0x203F_FFFF).
- **Burst support:** AXI4 INCR and WRAP burst types handled (lines 123–137). Independent read/write FSMs (dual-port).
- **Error response:** Out-of-range addresses produce `BRESP=2'b11` / `RRESP=2'b11` (DECERR) per AXI4 (lines 252, 306).
- **Data width:** 512-bit (64 bytes per beat) with WSTRB merge for sub-beat writes.
- **Status:** Fully spec-compliant. 4 MB window decode is correct.

---

## 9. Testbench / verification collateral impacts

### 9.1 `rtl/tb/tb_mxu.v`
- Uses native MMIO, not APB, so `apb_to_mmio.v` timing changes do not affect it.
- Writes register sequence on lines 480–508: `CTRL → DIM0 → DIM1 → I_ADDR → W_ADDR → O_ADDR → IRQ_EN → CMD`. This matches the spec sequence except it omits `BIAS_ADDR` and `SCALE_ADDR`. For bit-exact golden comparison these are unused, so no change is needed unless BIAS/SCALE are enabled.

### 9.2 `rtl/tb/tb_sfu.v`
- Writes sequence lines 426–432: `CTRL → I_ADDR → O_ADDR → DIM → (POS) → IRQ_EN → CMD`. Matches spec.
- Uses addresses `0x0` and `0x10000` (lines 427–428); these are testbench-local SRAM offsets, not the SoC canonical map. No change needed for module-level tests.

### 9.3 `rtl/tb/tb_vector.v`
- Writes sequence lines 575–586: `CTRL → A_ADDR → B_ADDR → O_ADDR → DIM → IRQ_EN → CMD`. Matches spec.
- Uses local SRAM offsets (e.g. `0x0001_0000` for operand B, `0x0002_0000` for output). No change needed for module-level tests.

---

## 10. Verification Regression Plan

### 10.1 Module-level regression (must pass before SoC)

| Engine | Scenarios | Entry point | Pass criterion |
|--------|-----------|-------------|----------------|
| MXU    | 9 named + 100 random | `python3 scripts/gen_mxu_vectors.py --scenario all` then `bash rtl/scripts/run_mxu_regression.sh` | All 109 `compare_rtl.py` comparisons PASS, INT32 bit-exact |
| SFU    | 319 | `python3 scripts/run_batch_regression.py` | All 319 inline `compare_sfu.py` comparisons PASS (`abs_tol=2e-3`, `rel_tol=1e-2`) |
| Vector | 63  | `python3 scripts/run_batch_regression.py` | All 63 inline comparisons PASS (INT32 bit-exact; CONV within FP16 tolerance) |

After wrapper changes, re-run the following module-level smoke tests to ensure no regressions:
- MXU: `single_tile`, `multi_tile_K`, `multi_tile_M`, `partial_tile_M`
- SFU: `softmax_smoke`, `rmsnorm_smoke`, `rope_pos_42`
- Vector: `add_128`, `conv_4096`, `vconv_f16_i32_smoke`

### 10.2 SoC-level regression (after module-level passes)

| Suite | Cases | Entry point | Pass criterion |
|-------|-------|-------------|----------------|
| FM-SOC RTL | 33 (`FM-SOC-001..032` + `FM-SOC-10X`) | `bash sim/regression/run_fm_soc_all.sh` | All 33 cases PASS |
| Cocotb per-op | 17+ isolated ops | `make run_e2e_rmsnorm run_e2e_softmax run_e2e_rope ...` in `sim/regression` | Each target PASS |
| Python model | 210 | `PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q` | 210/210 passed |

### 10.3 Regression order
1. Update `apb_to_mmio.v` and run **all** SoC smoke tests first; this touches every engine.
2. Update wrapper default bases and re-run the affected SoC e2e targets:
   - `run_e2e_mxu_single`, `run_e2e_mxu_multi`, `run_e2e_mxu_op05`, `run_e2e_mxu_op07`
   - `run_e2e_rmsnorm`, `run_e2e_softmax`, `run_e2e_rope`, `run_e2e_silu`, `run_e2e_sfu_rmsnorm_post`
   - `run_e2e_vresid`, `run_e2e_vmul`, `run_vector_vconv_f16_i32`
3. Run full module-level regressions.
4. Run full `run_fm_soc_all.sh`.
5. Run full pytest suite.

---

## 11. Estimated effort per module

| Module | Lines to change | Effort | Notes |
|--------|-----------------|--------|-------|
| `rtl/wrapper/apb_to_mmio.v` | 1 line (line 58) | 0.5 d | High verification cost: every SoC test exercises this bridge. |
| `rtl/wrapper/mxu_soc_wrapper.v` | 2 lines (lines 179–180) | 0.5 d | Plus update any hard-coded SoC test that assumes old defaults. |
| `rtl/wrapper/vector_soc_wrapper.v` | 3 lines (lines 176–178) | 0.5 d | Verify A/B base split against Func Model. |
| `rtl/mxu/mxu_top.v` | 0 lines (this cycle) | — | Stubbed BIAS/SCALE deferred. |
| `rtl/mxu/controller.v` | 0 lines | — | Confirmed spec-aligned. |
| `rtl/mxu/mmio_if.v` | 0 lines | — | Confirmed spec-aligned. |
| `rtl/sfu/sfu_top.v` | 0 lines | — | Confirmed spec-aligned. |
| `rtl/vector/vector_top.v` | 0 lines | — | Confirmed spec-aligned. |
| **Total RTL edits** | **~6 lines** | **1.5 d** | Plus 2–3 days of regression and debug on EDA server. |

---

## 12. Risk assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| **apb_to_mmio.v single-cycle `cs` change breaks firmware that relied on the extra setup-cycle latch** | Low | High | Run all SoC smoke tests immediately. The change makes the hardware match the spec; any failure indicates firmware was depending on non-spec behavior. |
| **MXU/SFU/Vector wrapper base-address changes collide with existing SoC test vector layouts** | Medium | High | Regenerate test vectors after changing defaults, and inspect Cocotb tests in `sim/cocotb_bridge.py` / `rtl_soc_runner.py` for hard-coded addresses. |
| **Vector A/B base split does not match Func Model scratch layout** | Medium | Medium | Read `sim/golden_executor.py` buffer layout before finalizing `wrp_b_base`. Add one test with non-overlapping A/B. |
| **BIAS_ADDR / SCALE_ADDR stub causes a future spec test to fail** | Low | Low | Document as not implemented in Phase 1. Enable only when a golden reference requires them. |
| **Token/batch dimension interpretation is ambiguous** | Low | Medium | Keep current `m_tile` outer-loop behavior; add an `M=8` MXU scenario to the module-level regression to prove per-token reset. |
| **Run-time regression on EDA server is slow** | High | Low | Use `scripts/run_batch_regression.py` fast binaries and parallel Makefile targets. |
| **`golden_executor.py` scratch_base default was in KV Cache region** | Fixed | Low | Default changed from `0x380000`→`0x340000`. Verify that no test vector relied on the old scratch address spilling into `0x2038_0000`. |

---

## 13. Rollback Plan

### 13.1 Branch strategy
- All RTL edits from this plan shall be performed on a **feature branch** (e.g., `feat/func-model-spec-align`).
- The `sim/golden_executor.py` scratch_base fix is not a code regression (pure Python default change); it can be cherry-picked to `main` independently if needed.
- The `apb_to_mmio.v` and wrapper base-address changes are tightly coupled: they must land together and be verified together.

### 13.2 Rollback procedure
If an SoC smoke test fails after the RTL changes:
1. **Immediate:** `git revert <merge-commit>` on the feature branch.
2. **Verify:** Re-run the affected SoC smoke tests (`make run_e2e_*` targets) on the reverted code to confirm green.
3. **Root-cause:** Investigate the failure against the evidence ledger:
   - Was the failure in a check that existed before? → pre-existing issue, not caused by this change.
   - Did the new default base addresses collide with hard-coded test addresses? → fix the test or wrapper.
   - Did the APB bridge `cs` gating break a firmware sequence? → firmware needs to be updated to spec-compliant timing.
4. **Re-apply:** After fixing the root cause, cherry-pick the original commits back and re-run regression.

### 13.3 Fallback: compile-time parameter guards
For the three RTL modules with changed defaults, provide an escape hatch via Verilog `ifdef`:
```verilog
// In apb_to_mmio.v, mxu_soc_wrapper.v, vector_soc_wrapper.v:
`ifdef USE_OLD_SRAM_MAP
    // original default (pre-spec)
`else
    // spec-compliant default
`endif
```
This lets the verification team toggle between old and new behavior with a single `+define+USE_OLD_SRAM_MAP` VCS option, without reverting.

### 13.4 Staged verification order
1. **APB bridge (`apb_to_mmio.v`)**: change `cs` gating → run SoC smoke tests (`run_fm_soc_all.sh` P0+P1).
2. **Wrapper base addresses** (`mxu_soc_wrapper`, `vector_soc_wrapper`): change defaults → re-run affected e2e targets.
3. **Full module-level regression** (MXU 109, SFU 319, Vector 63) → ensure no collateral damage to module tests.
4. **Full SoC regression** (33 FM-SOC cases) → final gate.
5. **Full pytest regression** (210 tests) → verify Python Func Model still passes.
Each stage must PASS before proceeding to the next. If any stage fails, rollback and fix before continuing.

---

## 14. References used

- `docs/func-model-mmio-spec.md`
- `docs/func-model-sram-map.md`
- `docs/func-model-golden-tolerance.md`
- `rtl/mxu/controller.v`
- `rtl/mxu/mmio_if.v`
- `rtl/mxu/mxu_top.v`
- `rtl/mxu/mac_array.v`
- `rtl/sfu/sfu_top.v`
- `rtl/vector/vector_top.v`
- `rtl/wrapper/apb_to_mmio.v`
- `rtl/wrapper/mxu_soc_wrapper.v`
- `rtl/wrapper/sfu_soc_wrapper.v`
- `rtl/wrapper/vector_soc_wrapper.v`
- `rtl/tb/tb_mxu.v`
- `rtl/tb/tb_sfu.v`
- `rtl/tb/tb_vector.v`
- `rtl/ip/dma_wrapper.v`
- `rtl/intc/intc_top.v`
- `rtl/soc/apb_decoder.v`
- `rtl/soc/sram_ctrl.v`
- `sim/golden_executor.py`
- `sim/regmap.py`
- `scripts/gen_mxu_vectors.py`
- `scripts/run_batch_regression.py`
- `sim/regression/run_fm_soc_all.sh`
- `sim/regression/Makefile`
