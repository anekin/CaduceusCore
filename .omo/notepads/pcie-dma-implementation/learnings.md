# pcie-dma-implementation Learnings

## [2026-07-06] Plan Start
- Plan selected: pcie-dma-implementation
- Worktree: none (work in current directory)
- Branch: feat_pcie
- Constraint: never modify vendored rtl/ip/verilog-pcie/ source
- All simulation on sz0001 EDA server

## [2026-07-06] T0.4 run_pcie_dma_elab.sh
- Created `scripts/run_pcie_dma_elab.sh` — VCS elaboration wrapper for SoC PCIe DMA
- Shebang `#!/usr/bin/env bash`, `set -euo pipefail`
- Uses `module load vcs` to activate EDA environment
- VCS command: `vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist -f rtl/cpu/ibex.flist -top caduceus_soc_top -o simv_soc_top`
- Tees output to `.omo/evidence/elab.log`
- Follows existing pattern from `run_fm_pcie_dma.sh` (REPO_ROOT detection, evidence dir creation)
- Verified: executable (`chmod +x`), content validated (module load, vcs, tee all present)
- Do NOT run until RTL PCIe DMA file lists are verified

## [2026-07-06] T0.1 — docs/bugs/bugs-pcie-dma.md created
- Created `docs/bugs/bugs-pcie-dma.md` — PCIe DMA dedicated bug tracking file
- Added `## 已知未覆盖` section with 4 entries:
  - UCOV-PCIE-001: Completion Timeout Recovery
  - UCOV-PCIE-002: 多 Function RC Model
  - UCOV-PCIE-003: AER Error Reporting
  - UCOV-PCIE-004: AXI DECERR 响应
- Matches style of existing `docs/bugs/bugs-soc-rtl.md`

## [2026-07-06] T0.3 — run_fm_pcie_dma.sh wrapper
- Created `scripts/run_fm_pcie_dma.sh` — PYTHONPATH=sim pytest wrapper for PCIe DMA Func Model
- Pattern: `#!/usr/bin/env bash` + `set -euo pipefail`, same style as `extract_blk0_status.sh`
- Tees output to `.omo/evidence/fm_pcie_dma.log`

## [2026-07-06] T0.6 — run_cocotb_pcie_dma.sh wrapper
- Created `scripts/run_cocotb_pcie_dma.sh` — cocotb PCIe DMA E2E wrapper
- Pattern: `module load vcs`, then `cd sim/regression`, sets `COCOTB_PY_ENV` explicitly, runs `make run_pcie_dma_e2e`
- Follows same `REPO_ROOT` / `EVIDENCE_DIR` pattern as `run_pcie_dma_elab.sh`
- Tees output to `.omo/evidence/cocotb_e2e.log`

## [2026-07-06] T0.7 — run_spike_pcie_dma.sh wrapper
- Created `scripts/run_spike_pcie_dma.sh` — Spike firmware E2E wrapper for PCIe DMA
- Command: `PYTHONPATH=sim python sim/spike_host.py --mode pcie_dma`
- Pattern: same as T0.3 (`#!/usr/bin/env bash` + `set -euo pipefail` + `REPO_ROOT`/`EVIDENCE_DIR` pattern)
- Tees output to `.omo/evidence/spike_e2e.log`
- Do not run yet — firmware handler for pcie_dma mode will be created in T4.2

## [2026-07-06] T0.5 — run_soc_regression.sh wrapper
- Created `scripts/run_soc_regression.sh` — calls `sim/regression/run_fm_soc_all.sh` and tees to `.omo/evidence/soc_regression.log`
- Exit code preserved via `set -o pipefail`: if the underlying script fails, pipe returns its exit code and `set -e` propagates it
- Uses same pattern as `run_fm_pcie_dma.sh`: shebang, `set -euo pipefail`, `REPO_ROOT` computed from script location, `mkdir -p` evidence dir


## [2026-07-06] sz0001 Env Check
- Exit code: 1
- Result: 1 failed, 7 passed, 38 deselected in 0.35s
- Log: `.omo/evidence/env_check.log`

## [2026-07-06] T1.1: DmaEngine class added to sim/models/pcie.py

### Implementation summary
- Added `DmaEngine` class (~620 lines, 30 methods/properties) to `sim/models/pcie.py`
- Models NPU-initiated PCIe DMA engine (dma_if_pcie behavior) as a Func Model golden reference

### Key capabilities implemented
1. **TLP header builders**: `_build_memwr_header_3dw/4dw`, `_build_memrd_header_3dw/4dw`, `_build_cpld_header`
   - 3-DW (12-byte) headers for addresses ≤ 0xFFFFFFFF
   - 4-DW (16-byte) headers for addresses > 0xFFFFFFFF (64-bit)
   - Fmt+Type encodings match `pcie_axi_master.v` lines 158-163: `MWR_3DW=0x40`, `MWR_4DW=0x60`, `MRD_3DW=0x00`, `MRD_4DW=0x20`, `CPLD=0x4A`
2. **Tag lifecycle management**: `PCIE_TAG_COUNT=256`, `_alloc_tag()` / `_free_tag()`, pool exhaustion raises `RuntimeError`
3. **Max payload splitting**: MPS=256 bytes default (PCIe encoding 1), payloads cross MPS boundaries → segmented TLPs
4. **Completion parsing**: `_parse_cpld_header()` extracts length, tag, byte_count, status from 12-byte CPLD header
5. **Descriptor-to-TLP translation**:
   - `submit_write_desc(pcie_addr, axi_addr, len, tag)` — reads NPU memory → MWr TLPs to host
   - `submit_read_desc(pcie_addr, axi_addr, len, tag)` — MRd TLPs → CPLD reassembly → writes to NPU memory
6. **Error propagation**: UR/CA completion status → `DESC_ERR_UR`/`DESC_ERR_CA` descriptor errors; AXI DECERR → `DESC_ERR_DECERR`
7. **IRQ assertion**: `irq` property asserted on any descriptor completion (edge-triggered, clears on read)
8. **Error injection**: `inject_completion_error(tag, status)` and `inject_axi_dec_error(tag)` for smoke testing

### RTL references used
- `rtl/ip/verilog-pcie/dma_if_pcie.v` — `PCIE_TAG_COUNT=256`, descriptor port definitions, `PCIE_ADDR_WIDTH=64`, `TAG_WIDTH=8`, `LEN_WIDTH=16`
- `rtl/ip/verilog-pcie/pcie_axi_master.v` (lines 158-163) — TLP Fmt+Type encoding (`010_00000` = 3-DW MWr, `011_00000` = 4-DW MWr)
- `sim/models/pcie.py` (PCIeModel) — reused `struct.pack(">III", ...)` header-packing pattern, MPS=256, tag increment

### Design decisions
- Host memory simulated as 16MB `bytearray` (expandable on writes within range)
- `tlp_write` gracefully skips host_mem writes for addresses beyond buffer bounds (prevents MemoryError for 64-bit smoke test addresses)
- CPLD status embedded in DW2 bits 7:4 (Func Model extension; real hardware uses DW2[15:13])
- Crossbar access optional: `submit_*_desc` works with or without crossbar (fallback to host_mem for smoke testing without crossbar)
- `tlp_read_with_reassembly()` added for RCB=128 split-completion testing (separate from basic `tlp_read()`)

### Verification
- All 7 smoke assertions pass: `PYTHONPATH=. python sim/models/pcie.py`
- Existing test suite: 42/46 pass (4 pre-existing failures are missing test vector manifest files — not related to this change)
- Import verification: `PYTHONPATH=sim python -c "from sim.models.pcie import DmaEngine"` succeeds
