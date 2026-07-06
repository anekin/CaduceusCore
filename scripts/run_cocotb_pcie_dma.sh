#!/usr/bin/env bash
# run_cocotb_pcie_dma.sh
# Cocotb PCIe DMA E2E wrapper.
# Loads VCS, sets up cocotb Python env, runs make run_pcie_dma_e2e from sim/regression,
# and tees output to .omo/evidence/cocotb_e2e.log.
# Usage: ./scripts/run_cocotb_pcie_dma.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="${REPO_ROOT}/.omo/evidence"
mkdir -p "${EVIDENCE_DIR}"

module load vcs

cd "${REPO_ROOT}/sim/regression"
COCOTB_PY_ENV=/NAS/Tools/anaconda3/envs/py3.11 \
  make run_pcie_dma_e2e 2>&1 | tee "${EVIDENCE_DIR}/cocotb_e2e.log"
