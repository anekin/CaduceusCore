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

