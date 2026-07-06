#!/usr/bin/env bash
# run_fm_pcie_dma.sh
# PCIe DMA Func Model pytest wrapper.
# Runs test_pcie_dma_fm.py with PYTHONPATH=sim and logs to .omo/evidence/.
# Usage: ./scripts/run_fm_pcie_dma.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="${REPO_ROOT}/.omo/evidence"
mkdir -p "${EVIDENCE_DIR}"

cd "${REPO_ROOT}"
PYTHONPATH=sim python -m pytest sim/tests/test_pcie_dma_fm.py -v 2>&1 | tee "${EVIDENCE_DIR}/fm_pcie_dma.log"
