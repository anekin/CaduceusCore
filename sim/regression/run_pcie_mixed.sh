#!/bin/bash
# =============================================================================
# run_pcie_mixed.sh — PCIe-only mixed-mode wrapper for FM-SOC-003/004/007
# =============================================================================
# Runs the three PCIe-only Func Model SoC cases with USE_RTL_PCIE enabled.
# Exits 0 only when all three cases PASS.
#
# Usage:
#   cd CaduceusCore/sim/regression
#   ./run_pcie_mixed.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
WORKSPACE_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# Source EDA environment
if [ -f "${SCRIPT_DIR}/run_env.sh" ]; then
    source "${SCRIPT_DIR}/run_env.sh"
else
    echo "ERROR: run_env.sh not found in ${SCRIPT_DIR}"
    exit 1
fi

cd "${REPO_ROOT}/sim/regression"

CASES=("FM-SOC-003" "FM-SOC-004" "FM-SOC-007")
EXTRA_DEFINES="+define+USE_RTL_PCIE"
RESULTS_DIR="${WORKSPACE_ROOT}/.omo/evidence"
RESULTS_FILE="${RESULTS_DIR}/task-4-pcie-mixed.txt"

mkdir -p "${RESULTS_DIR}"

echo "============================================================" | tee "${RESULTS_FILE}"
echo "PCIe-only mixed-mode regression (USE_RTL_PCIE)" | tee -a "${RESULTS_FILE}"
echo "Cases: ${CASES[*]}" | tee -a "${RESULTS_FILE}"
echo "Started: $(date)" | tee -a "${RESULTS_FILE}"
echo "============================================================" | tee -a "${RESULTS_FILE}"

OVERALL_PASS=1
for CASE_ID in "${CASES[@]}"; do
    echo "" | tee -a "${RESULTS_FILE}"
    echo "--- Running ${CASE_ID} ---" | tee -a "${RESULTS_FILE}"
    if make run_fm_soc_case "FM_SOC_CASE_ID=${CASE_ID}" "FM_SOC_EXTRA_DEFINES=${EXTRA_DEFINES}"; then
        echo "${CASE_ID}: PASS" | tee -a "${RESULTS_FILE}"
    else
        echo "${CASE_ID}: FAIL" | tee -a "${RESULTS_FILE}"
        OVERALL_PASS=0
    fi
done

echo "" | tee -a "${RESULTS_FILE}"
echo "============================================================" | tee -a "${RESULTS_FILE}"
if [ ${OVERALL_PASS} -eq 1 ]; then
    echo "PCIe-only mixed-mode regression: ALL PASS" | tee -a "${RESULTS_FILE}"
    echo "Finished: $(date)" | tee -a "${RESULTS_FILE}"
    echo "============================================================" | tee -a "${RESULTS_FILE}"
    exit 0
else
    echo "PCIe-only mixed-mode regression: FAIL" | tee -a "${RESULTS_FILE}"
    echo "Finished: $(date)" | tee -a "${RESULTS_FILE}"
    echo "============================================================" | tee -a "${RESULTS_FILE}"
    exit 1
fi
