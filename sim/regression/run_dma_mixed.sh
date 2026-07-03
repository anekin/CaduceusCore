#!/bin/bash
# =============================================================================
# run_dma_mixed.sh — Incremental DMA + engine-wrapper mixed-mode regression
# =============================================================================
# SoC Phase 3-4 / Todo 5 (soc-rtl-substitution)
#
# Runs:
#   FM-SOC-013 with USE_RTL_DMA  (DMA wrapper in RTL, engines are Func Model)
#   FM-SOC-010 with USE_RTL_MXU  (MXU wrapper in RTL)
#   FM-SOC-011 with USE_RTL_SFU  (SFU wrapper in RTL)
#   FM-SOC-012 with USE_RTL_VECTOR (Vector wrapper in RTL)
#
# PCIe is kept in RTL for all runs so the reduced mixed-mode DUT is used.
# Exits 0 only when FM-SOC-013 passes and at least one of FM-SOC-010/011/012
# passes.
#
# Usage:
#   cd CaduceusCore/sim/regression
#   ./run_dma_mixed.sh
# =============================================================================

set -uo pipefail

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

RESULTS_DIR="${WORKSPACE_ROOT}/.omo/evidence"
RESULTS_FILE="${RESULTS_DIR}/task-5-dma-mixed.txt"
mkdir -p "${RESULTS_DIR}"

# Each row: CASE_ID RTL_MODULE EXTRA_DEFINES
RUNS=(
    "FM-SOC-013 dma +define+USE_RTL_PCIE +define+USE_RTL_DMA"
    "FM-SOC-010 mxu +define+USE_RTL_PCIE +define+USE_RTL_MXU"
    "FM-SOC-011 sfu +define+USE_RTL_PCIE +define+USE_RTL_SFU"
    "FM-SOC-012 vector +define+USE_RTL_PCIE +define+USE_RTL_VECTOR"
)

echo "============================================================" | tee "${RESULTS_FILE}"
echo "DMA + engine-wrapper mixed-mode regression" | tee -a "${RESULTS_FILE}"
echo "Started: $(date)" | tee -a "${RESULTS_FILE}"
echo "============================================================" | tee -a "${RESULTS_FILE}"

DMA_PASS=0
ENGINE_PASS_COUNT=0

for RUN in "${RUNS[@]}"; do
    CASE_ID="$(echo "${RUN}" | awk '{print $1}')"
    RTL_MODULE="$(echo "${RUN}" | awk '{print $2}')"
    EXTRA_DEFINES="$(echo "${RUN}" | cut -d' ' -f3-)"

    echo "" | tee -a "${RESULTS_FILE}"
    echo "--- Running ${CASE_ID} (RTL ${RTL_MODULE}) ---" | tee -a "${RESULTS_FILE}"
    echo "Defines: ${EXTRA_DEFINES}" | tee -a "${RESULTS_FILE}"

    export FM_SOC_CASE_ID="${CASE_ID}"
    export FM_SOC_RTL_MODULE="${RTL_MODULE}"

    # Force recompilation of the mixed-mode simv because each run uses a
    # different set of +define+ flags. VCS defines are compile-time only.
    # Also remove csrc so VCS incremental cache does not carry stale defines.
    rm -rf "${REPO_ROOT}/sim/regression/simv_mixed_cocotb" \
           "${REPO_ROOT}/sim/regression/simv_mixed_cocotb.daidir" \
           "${REPO_ROOT}/csrc"

    BUILD_LOG="${REPO_ROOT}/sim/regression/fm_soc_${CASE_ID}_build.log"
    timeout 600 make run_fm_soc_case \
        "FM_SOC_CASE_ID=${CASE_ID}" \
        "FM_SOC_EXTRA_DEFINES=${EXTRA_DEFINES}" > "${BUILD_LOG}" 2>&1
    EXIT_CODE=$?
    cat "${BUILD_LOG}" >> "${RESULTS_FILE}"
    if [ ${EXIT_CODE} -eq 0 ]; then
        echo "${CASE_ID}: PASS" | tee -a "${RESULTS_FILE}"
        if [ "${CASE_ID}" == "FM-SOC-013" ]; then
            DMA_PASS=1
        else
            ENGINE_PASS_COUNT=$((ENGINE_PASS_COUNT + 1))
        fi
    else
        echo "${CASE_ID}: FAIL" | tee -a "${RESULTS_FILE}"
    fi
done

echo "" | tee -a "${RESULTS_FILE}"
echo "============================================================" | tee -a "${RESULTS_FILE}"
if [ ${DMA_PASS} -eq 1 ] && [ ${ENGINE_PASS_COUNT} -ge 1 ]; then
    echo "DMA + engine-wrapper mixed-mode regression: PASS" | tee -a "${RESULTS_FILE}"
    echo "DMA case passed and ${ENGINE_PASS_COUNT}/3 engine cases passed." | tee -a "${RESULTS_FILE}"
    echo "Finished: $(date)" | tee -a "${RESULTS_FILE}"
    echo "============================================================" | tee -a "${RESULTS_FILE}"
    exit 0
else
    echo "DMA + engine-wrapper mixed-mode regression: FAIL" | tee -a "${RESULTS_FILE}"
    echo "DMA_PASS=${DMA_PASS} ENGINE_PASS_COUNT=${ENGINE_PASS_COUNT}" | tee -a "${RESULTS_FILE}"
    echo "Finished: $(date)" | tee -a "${RESULTS_FILE}"
    echo "============================================================" | tee -a "${RESULTS_FILE}"
    exit 1
fi
