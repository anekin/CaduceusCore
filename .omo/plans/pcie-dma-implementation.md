# pcie-dma-implementation — Work Plan

## TL;DR (For humans)

**What you'll get**: PCIe DMA initiator integrated into CaduceusCore SoC — NPU can autonomously read/write host memory over PCIe Gen4 x4 via descriptor-driven DMA engine. Never modifies any vendored verilog-pcie source. Includes Func Model golden reference, RTL integration, APB adapter, firmware dispatch, and cocotb E2E verification.  

**Why this approach**: `dma_if_pcie` (already vendored) uses the same generic TLP interface as our existing `pcie_axi_master` bridge, with `dma_if_axi` translating its RAM interface to AXI4 for the crossbar. Descriptors fed via a thin (~350-line) APB-to-descriptor adapter, reusing the existing firmware doorbell dispatch pattern. Test-first with Func Model ensures bit-exact golden reference before any RTL work.

**What it will NOT do** (D9: never modify vendored verilog-pcie):  
- Will NOT touch any vendored verilog-pcie `.v` file  
- Will NOT provide a Linux host driver (stops at firmware + cocotb)  
- Will NOT support Xilinx Ultrascale `pcie_us_axi_dma`  
- Will NOT break existing PCIe bridge (host↔BAR0/BAR1) functionality  

**Effort**: 6 components, ~1300 lines new RTL, each 1–3 days.  

**Risk**: TLP mux arbitration under load; dma_if_axi latency pipeline; crossbar hardcoded width assumption (MUST pre-audit axi_crossbar.v before NUM_M change).  

**Repository wide constraints** (per `docs/caduceus-verification-lessons.md` and project rules):
- **所有仿真在 sz0001 (192.168.0.11) 上跑** — Func Model pytest、VCS 编译/仿真、cocotb 全部在 EDA server 上执行。禁止在开发机编译后 scp。（Lessons 原则 10）
- **复用开源资源** — 优先使用 upstream verilog-pcie 的 `tb/` testbench 和 cocotb BFM 作为验证起点，不一味自研。Linux driver 参考 upstream `example_driver.c`，APB adapter 参考 upstream `example_core.v` 的 AXI-Lite→descriptor FSM 模式。
- **Func Model 先过，再做 RTL** — DmaEngine 在 Python 上 100% 跑通（含边界：zero-length、max-length、unaligned、concurrent RD+WR）后，才写 RTL。（Lessons 原则 1）
- **增量替换 RTL，PCIe 最先** — 按风险排序：PCIe DMA 模块最先独立验证，再集成 SoC，CPU Ibex 最后换。（Lessons 原则 13）
- **每个 Wave 设 Review Gate** — 使用 Atlas 三态裁决（approve/reject/missing），不靠执行 agent 自评 PASS/FAIL。（Lessons 原则 8）
- **已知未覆盖点显式记录** — `docs/bugs/bugs-pcie-dma.md` 从 W1 开始创建，不攒到 phase 结束。（Lessons 原则 11/14）
- **Testbench 进 VCS 前 dry-run** — 所有 cocotb `_build_*()` 和 `@cocotb.test()` 在 EDA server 上用纯 Python 跑一遍，验证不抛异常、数据非空。（Lessons 原则 12）  
- **脚本优先，封装工具和环境** — 所有涉及 2 个以上工具/环境变量的 QA 命令必须用 `scripts/run_*.sh` 封装，固化 EDA server SSH、PYTHONPATH、module load、日志重定向。不同 agent 执行时不靠记忆拼裸命令。（Lessons 原则 22）

**Decisions**: All architecture decisions locked (D1–D9 inline in Scope below); parameter overrides, address ranges, opcode assignment, and descriptor protocol clarified in Scope section.

---

## Scope

### C1 — TLP Porting **(D2: TLP port separation at SoC boundary)**
- The bridge TLP ports (`rx_req_tlp_*`, `tx_cpl_tlp_*`) remain inside `pcie_ep_wrapper.v` and connect to the existing `pcie_axi_master`.
- The DMA engine TLP ports (`pcie_dma_tx_rd_req_*`, `pcie_dma_tx_wr_req_*`, `pcie_dma_rx_cpl_*`) are exposed as a second independent TLP port group at the `caduceus_soc_top.v` boundary.
- **Rationale for not using `pcie_tlp_mux`/`pcie_tlp_demux` inside `pcie_ep_wrapper.v`**: Keeping the streams separate preserves the proven bridge interface, avoids any risk of arbitration/state interaction between host-initiated BAR traffic and NPU-initiated DMA traffic, and gives the cocotb testbench direct visibility into each stream for debugging and dual-compare verification. The functional equivalence (host sees a single PCIe endpoint) is maintained by the host model in the testbench.
- Future integration step (deferred): merge both streams through `pcie_tlp_mux`/`pcie_tlp_demux` inside `pcie_ep_wrapper.v` when a single external TLP link is required.

### C2 — DMA Engine Integration **(D1: dma_if_pcie)**
- Instantiate `dma_if_pcie` with **explicit parameter overrides** (addresses Metis M1, M3):

  | Parameter | Value | File:Line Default | Rationale |
  |-----------|-------|-------------------|-----------|
  | `TLP_DATA_WIDTH` | 512 | `dma_if_pcie.v:37` (default 256) | Must match SoC TLP width |
  | `TLP_HDR_WIDTH` | 128 | `dma_if_pcie.v:41` | PCIe spec |
  | `TLP_SEG_COUNT` | 1 | `dma_if_pcie.v:43` | Single-segment |
  | `PCIE_ADDR_WIDTH` | 64 | `dma_if_pcie.v:63` | 64-bit PCIe addresses |
  | `PCIE_TAG_COUNT` | 256 | `dma_if_pcie.v:65` | Max outstanding reads |
  | `READ_OP_TABLE_SIZE` | 256 | `dma_if_pcie.v:75` | = PCIE_TAG_COUNT |
  | `WRITE_OP_TABLE_SIZE` | 256 | `dma_if_pcie.v:83` | Same sizing |
  | `READ_TX_LIMIT` | 128 | `dma_if_pcie.v:69` | Half of tag space |
  | `WRITE_TX_LIMIT` | 128 | `dma_if_pcie.v:85` | |
  | `READ_CPLH_FC_LIMIT` | 64 | `dma_if_pcie.v:78` | Conservative; per RC buffering |
  | `READ_CPLD_FC_LIMIT` | 256 | `dma_if_pcie.v:81` | 4× CPLH; 256 completions |
  | `IMM_ENABLE` | 0 | `dma_if_pcie.v:67` | No immediate write |

### C3 — AXI Bridge to Crossbar **(D3: dma_if_axi)**
- Instantiate `dma_if_axi` with **explicit parameter overrides** (addresses Metis M2):

  | Parameter | Value | File:Line Default | Rationale |
  |-----------|-------|-------------------|-----------|
  | `AXI_DATA_WIDTH` | 512 | `dma_if_axi.v:37` (default 32) | Match crossbar |
  | `AXI_ADDR_WIDTH` | 32 | — | SoC address space |
  | `AXI_ID_WIDTH` | 6 | — | Match crossbar M_ID_WIDTH |
  | `AXI_MAX_BURST_LEN` | 256 | — | Max burst |
  | `RAM_SEL_WIDTH` | 2 | — | Match dma_if_pcie |
  | `RAM_ADDR_WIDTH` | 16 | — | Match dma_if_pcie |
  | `RAM_SEG_COUNT` | 2 | — | = TLP_SEG_COUNT*2 |
  | `RAM_SEG_DATA_WIDTH` | 512 | — | = TLP_DATA_WIDTH*2/RAM_SEG_COUNT; must match `dma_if_pcie.v:57` default |
  | `RAM_SEG_BE_WIDTH` | 32 | — | = RAM_SEG_DATA_WIDTH / 8 |

- Connect `dma_if_axi.m_axi_*` to crossbar master port 6.

### C4 — NoC Expansion **(D4: crossbar M6→M7, D5: APB 7→8)**

#### Crossbar (`axi_crossbar.v`)
- **Change `NUM_M` from 6 to 7** at `axi_crossbar.v:37`  
- **Pre-audit requirement** (addresses Metis A1): BEFORE changing the parameter, run `grep -nE '(NUM_M|\[5:0\]|\[6:1\]|\[6\])' axi_crossbar.v` to confirm no hardcoded constants exist besides the parameter declaration. Evidence must be recorded in `.omo/evidence/`.  
- All `for (gmi = 0; gmi < NUM_M; ...)` loops auto-scale. No other changes needed in `axi_crossbar.v`.

#### SoC Top (`caduceus_soc_top.v`)
- `CROSSBAR_MASTERS` default: 6 → 7 (line 42)
- `CB_NUM_M` localparam: 6 → 7 (line 73)
- New signal group for master 6 (32 wires, after line 315)
- New crossbar mapping assigns for `cb_m_*[6]` (30 assignments, after line 685)
- Update comment header (lines 15, 22, 27, 496–497)
- New `pcie_dma_wrapper` instance (after line 1122)

#### APB Decoder
- Widen port arrays `[6:0]` → `[7:0]` in `apb_decoder.v` (lines 35–36, 42–44) and `caduceus_soc_top.v` (lines 329–330, 336–338)
- Add `slave_sel[7]` decode: `assign slave_sel[7] = (page == 4'd7);`  
- New slave #7 at `0x4000_7000` with 4 KB window: **0x4000_7000–0x4000_7FFF** (addresses Metis M6)
- Add `pready_masked[7]`, `pslverr_masked[7]`, `prdata[7]` mux

#### Interconnect YAML (`sim/config/interconnect.yaml`)
- `num_masters: 6` → `7` (line 37)
- Add master 6 entry (addresses Metis M7):

```yaml
- id: 6
  name: PCIe_DMA
  description: "PCIe DMA engine (NPU→host autonomous descriptor-based DMA)"
  data_width: 512
  axi_id_width: 6
  priority: 2
  intended_slaves: [SRAM, DRAM]
```

No slave or address route changes needed (DMA accesses same SRAM/DRAM).

### C5 — Descriptor Adapter **(D6: APB→stream adapter, ~360 lines)**

**Functional description**: APB register file → AXI-Stream descriptor adapter for `dma_if_pcie`. Firmware writes PCIE_ADDR / AXI_ADDR / LEN / TAG into APB registers, then sets START bit. A small FSM reads the registers and emits `s_axis_read_desc_*` or `s_axis_write_desc_*` handshake toward `dma_if_pcie`. Completion reported via DONE flag + IRQ.

**APB Register Map** (4 KB window, 0x4000_7000–0x4000_7FFF):

| Offset | Name | Width | Access | Description |
|--------|------|-------|--------|-------------|
| 0x00 | PCIE_CTRL | 32 | RW | [0]=start_rd, [1]=start_wr, [2]=abort, [3]=irq_en |
| 0x04 | PCIE_STATUS | 32 | RO | [0]=rd_busy, [1]=wr_busy, [2]=rd_done, [3]=wr_done, [4]=error |
| 0x08 | PCIE_ADDR_LO | 32 | RW | PCIe address [31:0] |
| 0x0C | PCIE_ADDR_HI | 32 | RW | PCIe address [63:32] |
| 0x10 | AXI_ADDR | 32 | RW | Local AXI address |
| 0x14 | LEN | 32 | RW | Transfer length (bytes) |
| 0x18 | TAG | 32 | RW | Descriptor tag (for completion tracking) |
| 0x1C | RD_ERR_CODE | 32 | RO | Read error code (from `m_axis_read_desc_status_error`) |
| 0x20 | WR_ERR_CODE | 32 | RO | Write error code |

**Line count estimate** (addresses Metis S1):  
- APB decode + register file: ~120 lines  
- Read descriptor FSM (APB→`s_axis_read_desc_*`): ~100 lines  
- Write descriptor FSM (APB→`s_axis_write_desc_*`): ~100 lines  
- IRQ generation + status: ~40 lines  
- **Total: ~360 lines** (revised from original ~150)

### C6 — Firmware **(D7: handler + opcode 7)**

**New opcode**: `OP_PCIE_DMA = 7` (addresses Metis M5; opcode 5 is already `ROPE` at `npu_firmware.c:424`). File: `firmware/npu-regmap.h` (after line 79).

**New descriptor struct in `firmware/npu_firmware.c`:

```c
typedef struct __attribute__((packed)) {
    uint32_t pcie_addr_lo;   // PCIe target address [31:0]
    uint32_t pcie_addr_hi;   // PCIe target address [63:32]
    uint32_t axi_addr;       // Local AXI source/destination
    uint32_t len;            // Transfer bytes
    uint32_t direction;      // 0=host→NPU (read), 1=NPU→host (write)
    uint32_t _pad[1];
} pcie_dma_desc_t;
```

**New handler function** `pcie_dma_exec()` following existing `dma_copy()` pattern (`npu_firmware.c:111-133`). Direction field selects read vs write APB register block. Completion via IRQ + STATUS poll.

**Doorbell dispatch** (`dispatch_cmd()` at `npu_firmware.c:346`): add `case 7: /* PCIe_DMA */ status = pcie_dma_exec(desc_addr); break;` after existing opcode cases. Opcode space confirmed — dispatch uses `if/else` chain with gaps between 0x06 and 0x0A.

**Completion notification** (addresses Metis A6/B3): DMA completion is signaled locally to the NPU firmware via `pcie_dma_irq` → INTC source bit 7 → `cpu_irq` → Ibex. This is NOT an MSI-X interrupt to the host — MSI-X is used only for the existing bridge error reporting. Firmware has two modes: (a) poll `PCIE_STATUS.rd_done` or `PCIE_STATUS.wr_done` after starting DMA, OR (b) enable `PCIE_CTRL.irq_en` and register an ISR for INTC bit 7. The `pcie_dma_irq` signal is asserted by the descriptor adapter FSM when `m_axis_read_desc_status_valid=1` or `m_axis_write_desc_status_valid=1` with status indicating completion.

---

## Verification Strategy **(D8: test-first, Func Model → RTL → cocotb)**

### Phase 1: Func Model (Python, ~300 lines) **(PREREQUISITE: Func Model fully verified before any RTL — Lessons 原则 1)**
- File: `sim/models/pcie.py` — add `DmaEngine` class
- Reuse upstream `tb/pcie.py` as reference: TLP header field layouts (`_build_memwr_header`, `_build_memrd_header`), DMA test scenarios (device-to-device DMA, device-to-root DMA), completion generation logic
- Capabilities: NPU-initiated MWr TLP generation (both 3-DW and 4-DW addressing), MRd TLP generation, CPLD capture (single + split), descriptor-to-TLP translation, local IRQ assertion on DMA completion
- **Func Model self-verification (golden reference sufficiency)**: Before RTL begins, all 7 TCs must pass on sz0001 AND the Func Model must demonstrate correctness on:
  1. TLP header format: 3-DW (32-bit addr) vs 4-DW (64-bit addr) — verified by comparing against upstream `tb/pcie.py` _build_*_header() reference output
  2. Max payload splitting: payloads crossing 256/512/1024-byte MPS boundaries generate correct segmented TLPs
  3. Tag lifecycle: allocate → use → complete → reuse; verify no tag leak after 256+ sequential operations
  4. Completion errors: UR, CA, and malformed CplD — verify status codes propagate to descriptor status
  5. Known uncovered (recorded in `docs/bugs/bugs-pcie-dma.md`): completion timeout recovery, multi-function RC model, AER error reporting
- Tests: `sim/tests/test_pcie_dma_fm.py` — pytest suite. Scenarios per Metis AC2:

| Test | Description | Accept Criterion |
|------|-------------|-----------------|
| TC1 | Single MWr NPU→host, 256 bytes | Host memory matches expected pattern |
| TC2 | Single MRd NPU←host, 512 bytes, split completion | CPLD re-assembly correct |
| TC3 | Unaligned transfer (address not 4B-aligned, odd length) | Correct byte-level data |
| TC4 | Max-length transfer (4096 bytes, max payload 256) | Full data integrity |
| TC5 | Concurrent read+write descriptors (AC3) | Both complete without interference |
| TC6 | PCIe completion error (UR) → read descriptor status error | error_status reports UR |
| TC7 | AXI slave error → write descriptor status error | `error` flag set in STATUS |

### Phase 2: RTL Standalone (VCS SystemVerilog, ~300 lines added)
- File: `rtl/ip/pcie_dma_tb.sv` (new) or extend `pcie_ep_tb.sv`
- Test `dma_if_pcie` + `dma_if_axi` + descriptor adapter in isolation (no crossbar)
- Clock: 1 GHz, reset: from `~rst_n` pad. Reset polarity conversion per `pcie_ep_wrapper.v:162` pattern for every new instance (addresses Metis A3):

| Instance | Reset Domain | Conversion |
|----------|-------------|------------|
| `dma_if_pcie` | SoC-wide `~rst_n` | Same pad in `pcie_dma_wrapper` |
| `dma_if_axi` | SoC-wide `~rst_n` | Same pad in `pcie_dma_wrapper` |
| `pcie_tlp_mux` | Inside `pcie_ep_wrapper` | Already converted (`rst = ~rst_n`, line 162) |
| `pcie_tlp_demux` | Inside `pcie_ep_wrapper` | Already converted |

### Phase 3: SoC Integration Tests (cocotb)
- File: `sim/cocotb_bridge.py` — extend host model to RECEIVE MWr/MRd from NPU and SEND CplD back (addresses Metis S3)
- Tests in `sim/tests/test_soc_pcie_dma.py` (new):

| Test | Description |
|------|-------------|
| TC-SOC1 | Host sets up memory, firmware executes PCIe DMA read, verifies host memory match |
| TC-SOC2 | Host sets up data in memory, firmware executes PCIe DMA write, verifies NPU DRAM contents |
| TC-SOC3 | Concurrent bridge (host RBAR read) + DMA (NPU MRd) — no corruption |
| TC-SOC4 | DMA error injection: invalid descriptor → fw detects error |
| TC-SOC5 | DMA error injection: host returns UR → fw detects error |
| TC-SOC6 | DMA completion IRQ → INTC → firmware handler — verify interrupt fires on DMA done |

### Phase 4: Vendored File Gate
- **Acceptance Criterion** (addresses Metis AC1): `git diff --name-only origin/main..feat_pcie | grep 'verilog-pcie/'` returns empty. This is checked IN CI / final verification, not manually.

---

## Execution Strategy

**Phased integration per Metis AC6**:

| Wave | What | Delivers | Gate |
|------|------|----------|------|
| W1 | Func Model (DmaEngine in pcie.py + pytest, reuse upstream cocotb BFM patterns) | `test_pcie_dma_fm.py` all 7 TCs PASS on sz0001 | Atlas review: Func Model evidence |
| W2 | RTL: dma_if_pcie + dma_if_axi + descriptor adapter standalone TB (reuse upstream tb/dma_if_pcie_rd/test_*.py patterns) | `pcie_dma_tb.sv` PASS (VCS on sz0001) | Atlas review: parameter override + reset polarity verified |
| W3 | RTL: TLP mux/demux in pcie_ep_wrapper, SoC top expansion, crossbar + APB widening, INTC expansion | Full SoC elaboration + existing tests PASS (VCS on sz0001) | Atlas review: all 33 FM-SOC tests still PASS |
| W4 | Firmware: pcie_dma_desc_t + handler + doorbell dispatch (build on sz0001) | firmware builds, dispatch test (Spike on sz0001) | Atlas review: opcode 7 routed correctly, Spike E2E passes |
| W5 | Cocotb E2E: extend cocotb host model + 6 E2E tests (run on sz0001) | `test_soc_pcie_dma.py` all PASS (VCS cocotb on sz0001) | Atlas review: NPU-initiated DMA E2E + backdoor/interface dual compare |
| W6 | Final regression: all existing tests + vendored file gate (on sz0001) | Full regression PASS, no vendored changes | Atlas final review: approve plan completion |

---

## Todos

### Wave 0: 环境准备

- [x] 1. **T0.1** —  `docs/bugs/bugs-pcie-dma.md`: 创建 PCIe DMA 专用 bug 跟踪文件  
- References: Lessons 原则 11/14  
- Acceptance: 文件存在，包含初始已知未覆盖条目: (1) completion timeout recovery 未覆盖, (2) 多 function RC model 未覆盖, (3) AER error reporting 未覆盖, (4) AXI DECERR 响应未覆盖  
- QA: `test -f docs/bugs/bugs-pcie-dma.md` → 文件存在且包含 `## 已知未覆盖` section，至少 4 条  
- Commit: `docs(pcie): create PCIe DMA bug tracking file`

- [x] 2. **T0.2** —  确认 sz0001 环境：cocotb, cocotbext-pcie, cocotbext-axi, VCS, Python 3.11 全部可用  
- References: Lessons 原则 10  
- Acceptance: `ssh sz0001 'python3 -c "import cocotb; import cocotbext.pcie; print(\"OK\")"'` 输出 OK  
- QA: `bash scripts/run_sz0001_env_check.sh` → benchmark PCIe 基线至少 5/7 通过，log in `.omo/evidence/`  
- Commit: 无需 commit（环境验证，记录到 `.omo/evidence/`）

- [x] 3. **T0.2b** — `scripts/run_sz0001_env_check.sh`: 封装 sz0001 环境基线验证  
- Content: `ssh sz0001 'cd wa2_caduceuscore/CaduceusCore && PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py -q -k pcie 2>&1' | tee .omo/evidence/env_check.log`  
- QA: 脚本存在且可执行  
- Commit: `chore(pcie): add run_sz0001_env_check.sh wrapper`

- [x] 4. **T0.3** —  `scripts/run_fm_pcie_dma.sh`: 封装 Func Model pytest 环境  
- References: Lessons 原则 22  
- Content: `PYTHONPATH=sim python -m pytest sim/tests/test_pcie_dma_fm.py -v 2>&1 | tee .omo/evidence/fm_pcie_dma.log`  
- QA: `bash scripts/run_fm_pcie_dma.sh` → 输出到 `.omo/evidence/` 且 exit code=0（T1.2 之后才能通过）  
- Commit: `chore(pcie): add run_fm_pcie_dma.sh wrapper`

- [x] 5. **T0.4** —  `scripts/run_pcie_dma_elab.sh`: 封装 VCS 编译环境  
- References: Lessons 原则 22  
- Content: `module load vcs; vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist -f rtl/cpu/ibex.flist -top caduceus_soc_top -o simv_soc_top 2>&1 | tee .omo/evidence/elab.log`  
- QA: `bash scripts/run_pcie_dma_elab.sh` → elaboration 0 errors  
- Commit: `chore(pcie): add run_pcie_dma_elab.sh wrapper`

- [x] 6. **T0.5** —  `scripts/run_soc_regression.sh`: 封装全量 SoC 回归  
- References: Lessons 原则 22; `sim/regression/run_fm_soc_all.sh`  
- Content: 调用 `run_fm_soc_all.sh` 并 tee 到 `.omo/evidence/`  
- QA: `bash scripts/run_soc_regression.sh` → 33/33 PASS 写入 evidence  
- Commit: `chore(pcie): add run_soc_regression.sh wrapper`

- [x] 7. **T0.6** —  `scripts/run_cocotb_pcie_dma.sh`: 封装 cocotb E2E 环境  
- References: Lessons 原则 22  
- Content: `module load vcs; cd sim/regression; COCOTB_PY_ENV=/NAS/Tools/anaconda3/envs/py3.11 make run_pcie_dma_e2e 2>&1 | tee ../../.omo/evidence/cocotb_e2e.log`  
- QA: `bash scripts/run_cocotb_pcie_dma.sh` → 6/6 PASS（T5.2 之后才能通过）  
- Commit: `chore(pcie): add run_cocotb_pcie_dma.sh wrapper`

- [x] 8. **T0.7** —  `scripts/run_spike_pcie_dma.sh`: 封装 Spike firmware E2E  
- References: Lessons 原则 22  
- Content: `PYTHONPATH=sim python sim/spike_host.py --mode pcie_dma 2>&1 | tee .omo/evidence/spike_e2e.log`  
- QA: `bash scripts/run_spike_pcie_dma.sh` → opcode 7 dispatch 通过（T4.2 之后才能通过）  
- Commit: `chore(pcie): add run_spike_pcie_dma.sh wrapper`

### Wave 1: Func Model Foundation

- [x] 8. **T1.1** —  `sim/models/pcie.py`: Add `DmaEngine` class with MWr/MRd TLP generation + CPLD capture  
- References: `dma_if_pcie.v:98-210` TLP port definitions, `pcie_axi_master.v` header format  
- Acceptance: `DmaEngine.tlp_write(pcie_addr, data)` generates correct 3-DW or 4-DW MWr header; `DmaEngine.tlp_read(pcie_addr, len)` generates correct MRd header and captures CPLD response  
- QA: `python -c "from sim.models.pcie import DmaEngine; ..."` runs 7 smoke assertions; pytest `test_pcie_dma_fm.py::test_tc1` through `test_tc7` all PASS  
- Commit: `feat(pcie): add DmaEngine func model with MWr/MRd/CPLD/error handling`

- [x] 9. **T1.2** —  `sim/tests/test_pcie_dma_fm.py`: Write 7 Func Model pytest cases  
- References: Metis AC2 list (TC1–TC7 above)  
- Acceptance: All 7 tests execute; TC1–TC4 verify data integrity; TC5 verifies concurrent read+write; TC6–TC7 verify error paths  
- QA: `bash scripts/run_fm_pcie_dma.sh` → 7 passed, log in `.omo/evidence/fm_pcie_dma.log`  
- Commit: `test(pcie): add 7 Func Model DMA test cases (MWr/MRd/CPLD/error/concurrent)`

- [x] 10. **R1** —  Review Gate: Atlas 审计 W1 证据（Func Model 7 TCs + golden reference sufficiency check）  
- What: `task(subagent_type="atlas", ...)` 只读审计 T1.1+T1.2 输出  
- Evidence: `sim/tests/test_pcie_dma_fm.py`, pytest 日志, `docs/bugs/bugs-pcie-dma.md`, 上游 `tb/pcie.py` TLP 头对比日志  
- **Hard gate**: Func Model 全部 7 TCs PASS **且** golden sufficiency 5 项检查通过后，才允许进 W2 RTL。不通过则退回 W1 补 Func Model  
- Acceptance: Atlas 输出 **approve**; Func Model golden reference 就绪声明写入 `.omo/evidence/`  
- Blocks: W2 (不通过不放行)

### Wave 2: RTL DMA Engine Standalone

- [x] 11. **T2.1** —  `rtl/ip/pcie_dma_wrapper.v`: New file — APB descriptor adapter + dma_if_pcie + dma_if_axi instantiation  
- References: Section C2/C3/C5 parameter tables above; `dma_if_pcie.v:34-210`; `dma_if_axi.v:34-116`  
- Acceptance: (a) APB register readback matches written values, (b) START bit → FSM emits `s_axis_read_desc_valid=1` with correct data, (c) DONE bit sets after `m_axis_read_desc_status_valid=1` returned from DMA  
- QA: VCS standalone testbench (T2.2) compiles and passes; `grep -rnE '(TLP_DATA_WIDTH|AXI_DATA_WIDTH)' pcie_dma_wrapper.v` shows overrides to 512  
- Commit: `feat(pcie): add pcie_dma_wrapper (APB adapter + dma_if_pcie + dma_if_axi)`

- [x] 12. **T2.2** —  `rtl/ip/pcie_dma_tb.sv`: New testbench for standalone DMA engine  
- References: `pcie_ep_tb.sv` testbench pattern; C5 register map  
- Acceptance: (a) Firmware-simulated APB write to desc regs triggers descriptor stream, (b) TLP output matches expected header/data, (c) error injection on completion returns error status  
- QA: VCS simulation prints `PCIE_DMA_TEST: PASS` for 5 test cases  
- Commit: `test(pcie): add pcie_dma standalone testbench (5 TCs)`

- [x] 13. **R2** —  Review Gate: Atlas 审计 W2 证据（RTL TB + dry-run 结果）  
- What: `task(subagent_type="atlas", ...)` 审计 T2.1+T2.2 输出  
- Evidence: VCS 仿真日志、参数覆盖 grep 结果、`.omo/evidence/`  
- Acceptance: Atlas 输出 **approve**  
- Blocks: W3

### Wave 3: SoC Top Integration

- [x] 14. **T3.1** —  `rtl/soc/axi_crossbar.v`: Change NUM_M 6→7; pre-audit for hardcoded constants  
- References: `axi_crossbar.v:37`; Metis A1  
- Acceptance: Elaboration succeeds with NUM_M=7; all existing crossbar tests pass  
- QA: `make run_crossbar_stress` → PASS; `grep -nE '\[6\]|\[5:0\]|\[6:1\]' axi_crossbar.v` produces only expected `NUM_M-1:0` array declarations; output saved to `.omo/evidence/axi_crossbar_num_m_audit.txt`  
- Commit: `feat(soc): expand crossbar NUM_M 6→7 for PCIe DMA master`

- [x] 15. **T3.2** —  `rtl/soc/apb_decoder.v`: Expand 7→8 slaves; add slave 7 decode  
- References: `apb_decoder.v:35-44`; address range `0x4000_7000–0x4000_7FFF`  
- Acceptance: APB write to 0x4000_7000 asserts `psel_o[7]`; read returns correct value; out-of-range (0x4000_8000) → pslverr  
- QA: `make run_apb_smoke` → PASS (APB decoder test covers new slave)  
- Commit: `feat(soc): expand APB decoder 7→8 slaves for PCIe DMA`

- [x] 16. **T3.3** —  `rtl/soc/caduceus_soc_top.v`: Add master 6 signal group + mapping + pcie_dma_wrapper instance + APB widening  
- References: Section C4 checklist; `caduceus_soc_top.v:42, 73, 315, 685, 329-338, 1122`  
- Acceptance: VCS elaboration with `-f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist` → 0 errors, 0 undriven; existing 33 FM-SOC tests still pass  
- QA: `make run_soc_elab` → PASS; `bash scripts/run_soc_regression.sh` → 33/33 PASS, log in `.omo/evidence/`  
- Commit: `feat(soc): integrate PCIe DMA as crossbar master 6 + APB slave 7`

- [x] 17. **T3.4** —  `sim/config/interconnect.yaml`: Add master 6 entry; update num_masters  
- References: Section C4 interconnect YAML diff  
- Acceptance: `sim/check_mmio_map.py` reports master 6 present; existing validation passes  
- QA: `python sim/check_mmio_map.py` → all masters present; `python scripts/validate_interconnect.py` → PASS  
- Commit: `config(interconnect): add PCIe DMA as master 6`

- [x] 18. **T3.5** —  `rtl/intc/intc_top.v` + `caduceus_soc_top.v`: Expand INTC from 7 to 8 interrupt sources; wire `pcie_dma_irq` to source bit 7  
- References: C6 completion notification; `intc_top.v` (currently 7 sources, `irq_src [6:0]`, `pending_reg [6:0]`, `PENDING/ENABLE/ACK` APB registers 7-bit)  
- Changes: (a) widen `irq_src` to `[7:0]`, `pending_reg`/`enable_reg` to 8-bit, `PENDING`/`ENABLE`/`ACK` registers to 8-bit, (b) add `pcie_dma_irq` input to `intc_top`, (c) connect in `caduceus_soc_top.v`  
- Acceptance: 8-source INTC passes existing 13/13 checks; `make run_intc_test` → PASS; `pcie_dma_irq` assertion sets `PENDING[7]`  
- QA: `make run_intc_test` → PASS (extended to 8 sources); VCS elaboration 0 errors  
- Commit: `feat(intc): expand INTC to 8 sources for PCIe DMA IRQ`

- [x] 19. **R3** —  Review Gate: Atlas 审计 W3 证据（SoC 集成 + 33 regression + INTC 8-source）  
- What: `task(subagent_type="atlas", ...)` 审计 T3.1–T3.5 输出  
- Evidence: `make run_crossbar_stress`, `make run_apb_smoke`, `make run_intc_test`, `run_fm_soc_all.sh` 日志, `.omo/evidence/axi_crossbar_num_m_audit.txt`  
- Acceptance: Atlas 输出 **approve**; 33/33 FM-SOC PASS  
- Blocks: W4

### Wave 4: Firmware

- [x] 20. **T4.1** —  `firmware/npu-regmap.h`: Add `npu_pcie_dma_t` struct + `OP_PCIE_DMA = 7`  
- References: Section C6 register map  
- Acceptance: Struct layout matches new APB registers; sizeof(npu_pcie_dma_t) ≤ 32 bytes  
- QA: `make -C firmware` compiles with no warnings  
- Commit: `feat(fw): add PCIe DMA register map + opcode 7`

- [x] 21. **T4.2** —  `firmware/npu_firmware.c`: Add `pcie_dma_exec()` handler + dispatch case  
- References: Existing `dma_copy()` pattern at `npu_firmware.c:111-133`; `dispatch_cmd()` at line 346
- Acceptance: (a) Func Model simulation with `pcie_dma_exec(desc_sram_addr)` triggers DMA read, (b) correct descriptor appears in DmaEngine, (c) completion status sets `STATUS.rd_done` or `STATUS.wr_done`
- QA: `make -C firmware` then `bash scripts/run_spike_pcie_dma.sh` → opcode 7 dispatch PASS, log in `.omo/evidence/spike_e2e.log`
- Commit: `feat(fw): add PCIe DMA descriptor handler in doorbell dispatch`

- [x] 22. **R4** —  Review Gate: Atlas 审计 W4 证据（firmware build + Spike E2E）  
- What: `task(subagent_type="atlas", ...)` 审计 T4.1+T4.2 输出  
- Evidence: firmware ELF build log, Spike E2E dispatch log (`OP_PCIE_DMA = 7` case)  
- Acceptance: Atlas 输出 **approve**  
- Blocks: W5

### Wave 5: Cocotb E2E Verification

- [x] 23. **T5.1** —  `sim/cocotb_bridge.py`: Extend cocotb host model to receive NPU MWr/MRd + send CplD  
- References: Cocotb PCIe model inversion (Metis S3); `cocotbext-pcie` API  
- Acceptance: Host model can capture the renamed `pcie_tx_tlp_data` (renamed from `pcie_tx_cpl_tlp_data` to reflect it now carries both completions and DMA requests), parse NPU MWr/MRd, generate CplD responses  
- QA: Existing cocotb SoC tests still pass; new helper functions `receive_pcie_tlp()` and `send_cpl_for_mrd()` tested in T5.2  
- Commit: `test(pcie): extend cocotb host model for NPU-initiated TLP receive+reply`

- [x] 24. **T5.2** —  `sim/tests/test_soc_pcie_dma.py`: Write 6 E2E cocotb test cases  
- References: TC-SOC1–TC-SOC6 in Verification Strategy  
- Acceptance: All 6 tests pass; TC-SOC3 verifies no bridge corruption under concurrent DMA; TC-SOC1/SOC2 use **dual compare** — backdoor read (验证计算） + interface read (PCIe TLP 回读, 验证接口通路), 分开记录结果 (Lessons 原则 7)  
- QA: `bash scripts/run_cocotb_pcie_dma.sh` → 6/6 PASS, log in `.omo/evidence/cocotb_e2e.log`; dry-run first: `python -c "from sim.tests.test_soc_pcie_dma import *; [t() for t in [test_tc_soc1, test_tc_soc2, test_tc_soc3, test_tc_soc4, test_tc_soc5, test_tc_soc6]]"` 在 sz0001 上纯 Python 跑一遍不抛异常 (Lessons 原则 12)  
- Commit: `test(pcie): add 6 cocotb E2E PCIe DMA test cases`

- [x] 25. **R5** —  Review Gate: Atlas 审计 W5 证据（cocotb E2E 6 TCs + backdoor/interface dual compare）  
- What: `task(subagent_type="atlas", ...)` 审计 T5.1+T5.2 输出  
- Evidence: cocotb 仿真日志 (6/6 PASS), `docs/bugs/bugs-pcie-dma.md` 更新  
- Acceptance: Atlas 输出 **approve**; backdoor/interface dual compare 结果记录  
- Blocks: W6

### Wave 6: Final Regression + Gate

- [x] 26. **T6.1** —  Run full SoC regression 33 tests + vendored file gate  
- References: `sim/regression/run_fm_soc_all.sh`; AC1 git diff check  
- Acceptance: All 33 FM-SOC tests PASS; `git diff --name-only origin/main..feat_pcie | grep 'rtl/ip/verilog-pcie/'` returns empty  
- QA: `bash scripts/run_soc_regression.sh` → 33/33 PASS; vendored gate script returns exit 0  
- Commit: `chore(pcie): final regression PASS + vendored file gate verified`

---

## Final Verification Wave

After ALL todos complete, run in parallel:

- [x] F1. **F1**: Plan compliance — verify every acceptance criterion in Scope/C1–C6 is met; no vendored files touched
- [x] F2. **F2**: Code quality — lint check on new `.v` files; no undriven nets; no simulation warnings
- [x] F3. **F3**: Manual QA — `bash scripts/run_fm_pcie_dma.sh && bash scripts/run_cocotb_pcie_dma.sh` → all PASS
- [x] F4. **F4**: Scope fidelity — confirm `git diff --stat feat_pcie..origin/main` covers only planned files; no unexpected changes

---

## Commit Strategy

| Wave | Commits | Strategy |
|------|---------|----------|
| W0 | 1 (T0.1) + no-commit (T0.2/T0.2b) + 5 (T0.3–T0.7) | DOCS + ENV + SCRIPTS |
| W1 | 2 (T1.1 + T1.2) + 1 review | NEW; Func Model + tests |
| W2 | 2 (T2.1 + T2.2) + 1 review | NEW; RTL wrapper + standalone TB |
| W3 | 5 (T3.1–T3.5) + 1 review | NEW; crossbar, APB, SoC top, interconnect, INTC |
| W4 | 2 (T4.1 + T4.2) + 1 review | NEW; regmap header + firmware handler |
| W5 | 2 (T5.1 + T5.2) + 1 review | NEW; cocotb model + E2E tests |
| W6 | 1 (T6.1) + 1 final review | CHORE; regression + gate |

All commits on `feat_pcie` branch. No force push required (branch was created from main, not yet pushed). Prefix: `feat(pcie):`, `test(pcie):`, `config(interconnect):`, `chore(pcie):`.

---

## Success Criteria

1. Func Model DmaEngine passes 7/7 pytest cases with bit-exact TLP generation
2. Standalone RTL DMA engine passes 5/5 VCS test cases
3. Full SoC elaboration with PCIe DMA succeeds (0 errors, 0 undriven)
4. All 33 existing FM-SOC regression tests still pass (no regression)
5. Firmware builds and dispatches OP_PCIE_DMA correctly
6. 6/6 cocotb E2E tests pass (including concurrent bridge+DMA and error paths)
7. `git diff --name-only origin/main..feat_pcie | grep 'rtl/ip/verilog-pcie/'` returns empty
8. Plan is complete: every todo has references + acceptance criteria + QA + commit line
