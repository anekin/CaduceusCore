#!/usr/bin/env bash
# run_pcie_dma_elab.sh
# VCS elaboration wrapper for CaduceusCore SoC with PCIe DMA.
# Loads VCS environment and elaborates caduceus_soc_top with PCIe/DMA file lists.
# Usage: ./scripts/run_pcie_dma_elab.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="${REPO_ROOT}/.omo/evidence"
mkdir -p "${EVIDENCE_DIR}"

cd "${REPO_ROOT}"

module load vcs
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
    -f rtl/cpu/ibex.flist \
    -f rtl/ip/verilog-axi.flist \
    -f rtl/ip/verilog-pcie.flist \
    -f rtl/soc/soc.flist \
    -top caduceus_soc_top -o simv_soc_top 2>&1 | tee "${EVIDENCE_DIR}/elab.log"
