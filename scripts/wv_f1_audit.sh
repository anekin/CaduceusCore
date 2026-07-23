#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/p9_lib/p9_sz0001.sh"

PLAN_FILE="${REPO_ROOT}/.omo/plans/wrapper-level-verification.md"
EVIDENCE_DIR="${REPO_ROOT}/build/evidence"
ISSUES_FILE="${REPO_ROOT}/docs/issues_found.md"
LOG_FILE="${EVIDENCE_DIR}/wv-f1-audit.log"

mkdir -p "${EVIDENCE_DIR}"
FAILURES=()

check_file() {
  local desc="$1" path="$2"
  if [ -f "${path}" ]; then
    echo "  [PASS] ${desc}: ${path}"
  else
    echo "  [FAIL] ${desc}: ${path} MISSING"
    FAILURES+=("${desc}: ${path} MISSING")
  fi
}

check_grep() {
  local desc="$1" pattern="$2" file="$3"
  if grep -qE "${pattern}" "${file}" 2>/dev/null; then
    echo "  [PASS] ${desc}: grep -q '${pattern}' ${file}"
  else
    echo "  [FAIL] ${desc}: grep '${pattern}' ${file} NOT FOUND"
    FAILURES+=("${desc}: pattern '${pattern}' not found in ${file}")
  fi
}

echo "=========================================="
echo "F1 — Plan Compliance Audit"
echo "Date: $(date)"
echo "Plan: ${PLAN_FILE}"
echo "=========================================="
echo ""

# ── Step 1: Verify T1-T8 checkboxes ──
echo "--- Step 1: Check T1-T8 checkboxes in plan ---"
ALL_TASKS=0
MISSING_TASKS=0
for i in $(seq 1 8); do
  pattern="\[x\] ${i}\."
  planfile="${PLAN_FILE}"
  if grep -q "${pattern}" "${planfile}"; then
    echo "  [PASS] T${i} is [x]"
    ALL_TASKS=$((ALL_TASKS + 1))
  else
    # try with indentation: " - [x] N." (plan uses " - [x] N.")
    if grep -qE "\[x\] ${i}\." "${planfile}"; then
      echo "  [PASS] T${i} is [x] (indented)"
      ALL_TASKS=$((ALL_TASKS + 1))
    else
      echo "  [FAIL] T${i} is NOT [x]"
      FAILURES+=("T${i} checkbox is not [x]")
      MISSING_TASKS=$((MISSING_TASKS + 1))
    fi
  fi
done
echo "  Result: ${ALL_TASKS}/8 tasks [x], ${MISSING_TASKS} missing"
echo ""

# ── Step 2: Verify acceptance criteria files ──
echo "--- Step 2: Verify acceptance criteria files ---"
check_file "wrapper.flist"              "${REPO_ROOT}/rtl/tb/wrapper.flist"
check_file "tb_sfu_wrapper.v"           "${REPO_ROOT}/rtl/tb/tb_sfu_wrapper.v"
check_file "tb_vector_wrapper.v"         "${REPO_ROOT}/rtl/tb/tb_vector_wrapper.v"
check_file "tb_mxu_wrapper.v"           "${REPO_ROOT}/rtl/tb/tb_mxu_wrapper.v"
check_file "axi_sparse_slave.v"         "${REPO_ROOT}/rtl/tb/axi_sparse_slave.v"
check_file "wrapper_common.py"          "${REPO_ROOT}/sim/tests/wrapper/wrapper_common.py"
check_file "test_sfu_wrapper.py"         "${REPO_ROOT}/sim/tests/wrapper/test_sfu_wrapper.py"
check_file "test_vector_wrapper.py"      "${REPO_ROOT}/sim/tests/wrapper/test_vector_wrapper.py"
check_file "test_mxu_wrapper.py"         "${REPO_ROOT}/sim/tests/wrapper/test_mxu_wrapper.py"
check_file "wv_run_sfu.sh"              "${REPO_ROOT}/scripts/wv_run_sfu.sh"
check_file "wv_run_vector.sh"           "${REPO_ROOT}/scripts/wv_run_vector.sh"
check_file "wv_run_mxu.sh"              "${REPO_ROOT}/scripts/wv_run_mxu.sh"
check_file "wv_run_bug005.sh"           "${REPO_ROOT}/scripts/wv_run_bug005.sh"
check_file "wv_run_bug007.sh"           "${REPO_ROOT}/scripts/wv_run_bug007.sh"
check_file "wv_regression.sh"           "${REPO_ROOT}/scripts/wv_regression.sh"
echo ""

# ── Step 3: Verify grep acceptance criteria ──
echo "--- Step 3: Verify grep acceptance criteria ---"
check_grep "SFU regression status"   'SFU.*(PASS|FAIL|PARTIAL)'     "${EVIDENCE_DIR}/wrap-regression-summary.txt"
check_grep "Vector regression PASS"  'Vector.*PASS'                 "${EVIDENCE_DIR}/wrap-regression-summary.txt"
check_grep "MXU regression PASS"     'MXU.*PASS'                    "${EVIDENCE_DIR}/wrap-regression-summary.txt"
check_grep "BUG-005 in regression"   'BUG-005'                      "${EVIDENCE_DIR}/wrap-regression-summary.txt"
check_grep "BUG-007 in regression"   'BUG-007'                      "${EVIDENCE_DIR}/wrap-regression-summary.txt"
check_grep "issues_found.md WV section" 'Wrapper-Level Verification Results' "${ISSUES_FILE}"
check_grep "wv-closure.txt status"   'PASS|FAIL|forward'            "${EVIDENCE_DIR}/wv-closure.txt"
echo ""

# ── Step 4: Write audit log ──
echo "=========================================="
echo "AUDIT RESULT"
echo "=========================================="

{
  echo "F1-AUDIT-LOG: $(date)"
  echo "Plan: ${PLAN_FILE}"
  echo ""
  echo "--- Step 1: T1-T8 checkboxes ---"
  echo "Tasks marked [x]: ${ALL_TASKS}/8"
  echo "Tasks missing:    ${MISSING_TASKS}"
  echo ""
  echo "--- Step 2: Files ---"
  echo "Total checks: 15"
  echo ""
  echo "--- Step 3: Acceptance criteria grep ---"
  echo "Total checks: 7"
  echo ""
} >> "${LOG_FILE}"

if [ ${#FAILURES[@]} -eq 0 ] && [ "${MISSING_TASKS}" -eq 0 ]; then
  echo "  F1-AUDIT-PASS: All checks passed."
  echo "F1-AUDIT-PASS: All checks passed." >> "${LOG_FILE}"
else
  echo "  FAIL: ${#FAILURES[@]} failure(s) found."
  echo "FAIL: ${#FAILURES[@]} failure(s) found." >> "${LOG_FILE}"
  for f in "${FAILURES[@]}"; do
    echo "    - ${f}"
    echo "  - ${f}" >> "${LOG_FILE}"
  done
fi

exit 0

