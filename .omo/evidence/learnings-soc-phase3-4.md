# SoC Phase 3-4 — Lessons Learned

> CaduceusCore SoC Integration Project
> Date: 2026-06-27 | 18 tasks, 6 waves, ~2 weeks
> Evidence collected across `.omo/evidence/task-*-soc-phase3-4.txt`

---

## L1: AXI4 Crossbar Concurrent Stress Results (Task 7)

**Finding**: A custom M=6, S=2 round-robin AXI4 crossbar with per-slave independent AW/AR arbitration passed 11,455 cycles of concurrent MXU+DMA+PCIe stress with 0 data errors across 1,260 transactions. Single outstanding per master per direction was sufficient for the NPU workload pattern.

**Design detail**: The 2-cycle latch clear mechanism for AW/AR forwarding was critical to avoid race conditions when back-to-back transactions from different masters targeted the same slave. Without it, the stale AR latch could retain the previous transaction's address for one cycle after a context switch.

**Key takeaway**: Deadlock-free verification through practical stress testing (≥10k cycles, 0 timeout) is a viable alternative to formal proofs for a custom crossbar of this complexity. The custom crossbar (578 lines) is simpler and more auditable than integrating a full NoC IP.

**Evidence**: `task-7-soc-phase3-4.txt` — Crossbar stress TC4: 1,260 transactions, 0 errors.

---

## L2: Ibex + Boot ROM Bring-Up Log (Task 4)

**Finding**: Ibex RV32IMC with a 64KB boot ROM at 0x0000_0000 (loaded via `$readmemh` from `firmware.hex`) booted successfully in VCS simulation. The Ibex→AXI4 adapter (32-bit→512-bit width converter via `axi_adapter` from verilog-axi) required careful ID width handling: Ibex native 4-bit ID padded to 6-bit for the crossbar, with the adapter's `FORWARD_ID=1` and `CONVERT_BURST=1` parameters.

**Key challenge**: The Ibex `ibex_top` module uses a request/grant/rvalid memory protocol internally. The `ibex_wrapper` must translate this to standard AXI4 AW/W/B/AR/R channels. The boot ROM needs to present a 32-bit instruction word in 1 cycle to match Ibex's `fetch_rvalid` timing.

**Filelist ordering**: `ibex.flist` MUST be the first filelist on the VCS command line because `ibex_pkg.sv` defines SystemVerilog packages used by `ibex_wrapper.v`. Incorrect ordering causes "unknown package" compilation errors.

**Evidence**: `task-4-soc-phase3-4.txt`, `task-13-soc-phase3-4.txt` (VCS: ibex.flist first).

---

## L3: APB Decoder Address Aliasing (Task 3)

**Finding**: The 7-slave APB decoder at 0x4000_0000 uses a simple address prefix decode (addr[15:12] as slave select). This means addresses 0x4000_7000~0x4000_FFFF alias to slaves 7-15, which do not exist. The decoder correctly asserts `pslverr` for these unmapped ranges.

**Address space discipline**: Each APB slave has a 4KB window (addr[11:0]). The `apb_to_mmio.v` bridge converts APB transactions to the engine's native MMIO protocol (cs/we/addr/wdata/rdata/ready). The decoder's psel/pready muxing handles the 7→1 response path correctly.

**Key insight**: The decoder must handle the case where the APB master (Ibex) does a read from an unmapped address. The `catch-all` pslverr path prevents bus hang. The `prdata` multiplexer must return 0 for unmapped reads to avoid X-propagation.

**Evidence**: `task-3-soc-phase3-4.txt` — APB smoke test: 7 slaves select correctly, unmapped → pslverr.

---

## L4: axi_cdma Descriptor Format (Task 11)

**Finding**: The `dma_wrapper` translates the firmware-compatible register map (CH0_SRC/CH0_DST/CH0_SIZE at offsets 0x10/0x14/0x18) to `axi_cdma`'s streaming descriptor format (SRC_ADDR/DST_ADDR/LEN/TAG). The wrapper's translation FSM converts multi-descriptor chains (CH0 then CH1) into sequential axi_cdma transfer requests.

**Descriptor protocol**: axi_cdma uses a simple 3-field descriptor (address + address + length), unlike DW_axi_dmac's 8-field format (SAR/DAR/CTL_LOW/CTL_HIGH/LLI + padding). This simplicity makes the wrapper's translation FSM straightforward—a single FSM state per channel.

**Interrupt handling**: `transfer_done & IRQ_EN → dma_irq`. The STATUS.DONE auto-clears on read (standard behavior). CMD.START auto-clears after one cycle (edge detection). These conventions match the existing `npu_firmware.c` polling loop.

**Verification**: VCS simulation confirmed APB register readback, CMD.START→STATUS.BUSY, IRQ_EN behavior, and CH1 transfer initiation.

**Evidence**: `task-11-soc-phase3-4.txt` — 5 test cases, ALL PASSED.

---

## L5: cocotbext-axi / cocotbext-pcie Integration (Task 14)

**Finding**: The Cocotb system testbench (`tb_soc.v` + `cocotb_bridge.py`) successfully integrates Alex Forencich's `cocotbext-axi` and `cocotbext-pcie` Python packages for protocol-level verification. The `cocotbext-pcie` host model allows simulating PCIe TLP transactions without a real PCIe PHY.

**Key architecture**: The Cocotb bridge exposes three entry points: `test_soc_smoke` (APB write/readback), `test_soc_e2e` (firmware boot + single instruction), and `test_qwen_smoke` (Qwen2.5-3B blk.0 4-instruction smoke). The `COCOTB_AVAILABLE` guard gracefully handles environments without cocotb installed.

**DPI-C interface**: `tb_soc.v` uses DPI-C stubs for Python control (clock tick, signal access), with `$value$plusargs("BOOTROM_HEX", path)` for firmware loading. The standalone mode runs a basic clock/reset sanity check without Cocotb.

**Limitation**: cocotbext-axi and cocotbext-pcie require `pip install` in the Python environment. The regression Makefile documents this requirement but does not automate it.

**Evidence**: `task-14-soc-phase3-4.txt` — VCS compile: 47 modules OK, Python imports verified.

---

## L6: SRAM Multi-Master Arbitration (Task 2 + Task 7)

**Finding**: The SRAM controller (`sram_ctrl.v`) is a 4MB AXI4 slave with 512-bit data width, supporting INCR/WRAP burst types. Multi-master access is arbitrated by the crossbar's round-robin per-slave arbiter, not by the SRAM controller itself.

**Decoupling**: The SRAM controller implements simple AXI4 slave semantics (respond to requests, handle burst, enforce address bounds). All multi-master arbitration lives in the crossbar. This separation of concerns means the SRAM controller could be replaced with a different memory technology (e.g., register file, eDRAM) without changing the crossbar.

**Address guards**: The SRAM controller returns DECERR for addresses outside 0x2000_0000~0x203F_FFFF. This is redundant with the crossbar's own DECERR injection but provides defense-in-depth against misrouted transactions.

**Evidence**: `task-2-soc-phase3-4.txt` — Single and burst AXI4 writes/reads correct; out-of-bounds → DECERR.

---

## L7: MXU Broadcast Bus Sequencer Design (Task 5)

**Finding**: The MXU engine internally uses a broadcast bus architecture (`weight_bus_i[255:0]`, `activation_bus_i[511:0]`) driven by the testbench in standalone mode. The `mxu_soc_wrapper` replaces the testbench driver with an AXI4→broadcast bus sequencer that reads 512-bit rows from SRAM, deserializes them, and drives the weight/activation buses. Output data is serialized and written back to SRAM via AXI4.

**Design constraint**: The sequencer must respect the MXU's tile-by-tile compute cadence. The wrapper cannot issue the next weight/activation load until the current tile completes. This is enforced by the MXU's internal controller FSM handshaking (compute_en, weight_load, activation_load, store_out signals exposed as debug ports).

**Separation**: The MXU internal RTL is completely unchanged. The wrapper only adds AXI4 master and APB slave ports, plus the broadcast bus sequencer. Unit tests for `mxu_top` standalone continue to work.

**Evidence**: `task-5-soc-phase3-4.txt` — Wrapper compiles, APB→CTRL→readback verified, AXI4→SRAM→engine reads→readback correct.

---

## L8: Width Converter Corner Cases (ibex_wrapper → crossbar)

**Finding**: The Ibex core's 32-bit AXI4 data width must be converted to the crossbar's 512-bit width. The `axi_adapter` from verilog-axi handles this with `CONVERT_BURST=1` and `CONVERT_NARROW_BURST=0`. However, several corner cases required attention:

1. **ID width**: Ibex 4-bit ID → adapter (4-bit passthrough with `FORWARD_ID=1`) → pad to 6-bit for crossbar master port 0. Response routing demuxes back correctly.
2. **Byte strobe mapping**: 4-bit (32-bit) strb → 64-bit (512-bit) strb. The adapter handles packing/unpacking automatically.
3. **Burst conversion**: 32-bit bursts of 16 beats become 512-bit bursts of 1 beat when properly aligned.
4. **Unaligned access**: Ibex may issue sub-word accesses (byte/halfword). The adapter handles this at the AXI protocol level.

**The adapter output ports for lock/cache/prot/qos/region/user are tied off (unused by the crossbar). These need explicit tie-offs to avoid undriven net warnings in VCS.**

**Evidence**: `caduceus_soc_top.v` lines 221-244 (adapter tie-offs), `task-13-soc-phase3-4.txt`.

---

## L9: DOORBELL Ring Buffer Protocol (Task D)

**Finding**: The doorbell module implements a simple 4-register ring buffer pointer protocol: HOST_TAIL (host writes new command tail), NPU_HEAD (firmware writes after consumption), HOST_HEAD/NPU_TAIL (completion ring). The interrupt is purely combinatorial: `doorbell_irq = (HOST_TAIL != NPU_HEAD)`.

**Protocol compatibility**: This design is compatible with the existing `npu_firmware.c` main loop polling logic. The firmware reads HOST_TAIL via APB, dispatches commands, then writes NPU_HEAD=HOST_TAIL. The interrupt automatically clears when HEAD catches up to TAIL. No explicit IRQ acknowledge register is needed.

**Multi-command race**: If the host writes TAIL twice before firmware catches up, the interrupt stays asserted (TAIL still ≠ HEAD). This is correct behavior—the firmware's polling loop will process all pending commands.

**Evidence**: `task-D-soc-phase3-4.txt` — APB write/readback verified, doorbell_irq toggles correctly.

---

## L10: INTC 7-Source Popcount Threshold Gate (Task 6)

**Finding**: The interrupt controller's `cpu_irq` output uses a popcount threshold gate: `cpu_irq = |(PENDING & ENABLE) when popcount(PENDING & ENABLE) ≥ THRESHOLD else 0`. This design is useful for coalescing multiple low-priority interrupts before waking the CPU.

**Implementation**: THRESHOLD defaults to 1 after reset (single pending interrupt fires IRQ). A THRESHOLD of 2 or higher requires multiple pending sources. This is verified in test cases TC6a/TC6b.

**PENDING register semantics**: Write-1-to-Clear (W1C) via the ACK register. If the interrupt source is still high when ACK is written, the bit re-sets on the next cycle (level-sensitive source). This is the correct behavior for level-sensitive interrupts.

**IP migration**: The INTC was expanded from 5 sources (MXU/SFU/Vector/DMA + 1 reserved) to 7 sources (adding PCIe/Host-doorbell/Timer). The `npu-regmap.h` was updated accordingly: `INTC_HOST` moved from bit 8 to bit 5, `INTC_PCIE` added at bit 4, `INTC_TIMER` added at bit 6.

**Evidence**: `task-6-soc-phase3-4.txt` — 13/13 checks passed, VCS simulation 111ns.

---

## L11: SoC Filelist Compilation Ordering (Task 13)

**Finding**: VCS filelist ordering is critical. `ibex.flist` must appear first on the command line because it contains `ibex_pkg.sv`—a SystemVerilog package that `ibex_wrapper.v` imports via `import ibex_pkg::*`. Incorrect ordering causes "Unknown package" errors.

**Flists used**: `ibex.flist` (76 lines), `verilog-axi.flist` (64 lines), `verilog-pcie.flist` (74 lines), `soc.flist` (67 lines). Total: ~281 files compiled across 47 modules.

**Module redefinition warnings**: `arbiter` and `priority_encoder` are duplicated between verilog-axi and verilog-pcie. VCS picks the last definition. This is harmless for simulation but should be documented.

**Elaboration**: 28.1s compile + 1.8s elaborate + 0.5s link. 0 errors, warnings only from vendored third-party code (TIMES_NOINHERIT, OPD, TFIPC). ZERO warnings from caduceus_soc_top.v itself.

**Evidence**: `task-13-soc-phase3-4.txt` — simv_soc_top generated, 47 modules, 0 errors.

---

## L12: DRAM Behavioral Model Capped Size (Task 9)

**Finding**: The DRAM behavioral model (`dram_model.v`) implements a 2GB addressable space (0x8000_0000~0xFFFF_FFFF) with sparse 8MB actual storage (`reg [511:0] mem[0:131071]`). Addresses beyond 8MB return DECERR. This approach keeps simulation memory footprint manageable while maintaining correct AXI4 slave behavior.

**DDR latency**: Fixed tRC=48ns (48 cycles @1GHz) simulated via a configurable delay counter. This is a simplification compared to a full DRAM PHY model but is sufficient for functional verification of the AXI4 data path.

**Initialization**: `$readmemh("dram_init.hex")` supports loading data at simulation start (for Qwen weight preloading). This is generated from firmware's `.data_dram` section.

**Evidence**: `task-9-soc-phase3-4.txt` — 100 random AXI4 transactions pass, write→read matched.

---

## Verification Summary

| Test | Tool | Result |
|------|------|:------:|
| MMIO consistency | `check_mmio_map.py` | 49 regs match |
| Interconnect validation | `validate_interconnect.py` | PASS |
| pytest regression (210) | pytest | 210/210 |
| Crossbar concurrent stress | VCS | 11,455 cycles, 0 err |
| DMA wrapper | VCS | 5/5 PASS |
| INTC 7-source | VCS | 13/13 PASS |
| SoC elaboration | VCS | 47 modules, 0 err |
