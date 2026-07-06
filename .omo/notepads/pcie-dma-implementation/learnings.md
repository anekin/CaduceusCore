# pcie-dma-implementation Learnings

## [2026-07-06] Plan Start
- Plan selected: pcie-dma-implementation
- Worktree: none (work in current directory)
- Branch: feat_pcie
- Constraint: never modify vendored rtl/ip/verilog-pcie/ source
- All simulation on sz0001 EDA server

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

