#!/usr/bin/env bash
# run_spike_pcie_dma.sh
# Spike firmware E2E wrapper for PCIe DMA.
# Runs spike_host.py --mode pcie_dma with PYTHONPATH=sim and logs to .omo/evidence/.
# Usage: ./scripts/run_spike_pcie_dma.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="${REPO_ROOT}/.omo/evidence"
mkdir -p "${EVIDENCE_DIR}"

cd "${REPO_ROOT}"
PYTHONPATH=sim python sim/spike_host.py --mode pcie_dma 2>&1 | tee "${EVIDENCE_DIR}/spike_e2e.log"
