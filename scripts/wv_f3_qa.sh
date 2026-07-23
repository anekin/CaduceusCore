#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

EVIDENCE_DIR="${REPO_ROOT}/build/evidence"
SUMMARY="${EVIDENCE_DIR}/wrap-regression-summary.txt"
BUG005_FILE="${EVIDENCE_DIR}/wrap-bug005-result.txt"
BUG007_FILE="${EVIDENCE_DIR}/wrap-bug007-result.txt"
CHECKLIST="${EVIDENCE_DIR}/wv-f3-checklist.txt"

bug005_ok=0
bug007_ok=0
regression_pass=0
bug005_reason=""
bug007_reason=""
regression_reason=""

mkdir -p "${EVIDENCE_DIR}"

# ---------------------------------------------------------------
# 1. Check regression summary file exists with all sections
# ---------------------------------------------------------------
if [[ ! -f "${SUMMARY}" ]]; then
  regression_reason="MISSING: ${SUMMARY}"
elif ! grep -qE '^--- SFU Wrapper ---' "${SUMMARY}"; then
  regression_reason="MISSING SFU section in regression summary"
elif ! grep -qE '^--- Vector Wrapper ---' "${SUMMARY}"; then
  regression_reason="MISSING Vector section in regression summary"
elif ! grep -qE '^--- MXU Wrapper ---' "${SUMMARY}"; then
  regression_reason="MISSING MXU section in regression summary"
elif ! grep -qE '^--- BUG-005' "${SUMMARY}"; then
  regression_reason="MISSING BUG-005 section in regression summary"
elif ! grep -qE '^--- BUG-007' "${SUMMARY}"; then
  regression_reason="MISSING BUG-007 section in regression summary"
else
  regression_pass=1
fi

# ---------------------------------------------------------------
# 2. Check BUG-005 conclusions
#    Vector X_PROP:  summary (BUG-005.*Vector.*X_PROP)  OR  bug005 file (Vector: X_PROP)
#    SFU BLOCKED/FAIL-TIMEOUT:  summary (BUG-005.*SFU.*BLOCKED|FAIL-TIMEOUT)  OR  bug005 file (SFU: FAIL-TIMEOUT)
# ---------------------------------------------------------------
vector_xprop=0
sfu_blocked=0

if grep -qE 'BUG-005.*Vector.*X_PROP' "${SUMMARY}" 2>/dev/null || \
   grep -qE 'Vector: X_PROP' "${BUG005_FILE}" 2>/dev/null; then
  vector_xprop=1
fi

if grep -qE 'BUG-005.*SFU.*BLOCKED|FAIL-TIMEOUT' "${SUMMARY}" 2>/dev/null || \
   grep -qE 'SFU: FAIL-TIMEOUT' "${BUG005_FILE}" 2>/dev/null; then
  sfu_blocked=1
fi

if [[ "${vector_xprop}" -eq 1 && "${sfu_blocked}" -eq 1 ]]; then
  bug005_ok=1
else
  local reasons=""
  [[ "${vector_xprop}" -eq 0 ]] && reasons="${reasons}Vector_X_PROP_not_found "
  [[ "${sfu_blocked}" -eq 0 ]] && reasons="${reasons}SFU_BLOCKED/FAIL_TIMEOUT_not_found "
  bug005_reason="${reasons%% }"
fi

# ---------------------------------------------------------------
# 3. Check BUG-007 conclusions
#    MXU FAIL:  summary (BUG-007.*MXU.*FAIL)  OR  bug007 file (MXU: FAIL)
#    SFU PASS:  summary (BUG-007.*SFU.*PASS)  OR  bug007 file (SFU: PASS)
# ---------------------------------------------------------------
mxu_fail=0
sfu_pass=0

if grep -qE 'BUG-007.*MXU.*FAIL' "${SUMMARY}" 2>/dev/null || \
   grep -qE 'MXU: FAIL' "${BUG007_FILE}" 2>/dev/null; then
  mxu_fail=1
fi

if grep -qE 'BUG-007.*SFU.*PASS' "${SUMMARY}" 2>/dev/null || \
   grep -qE 'SFU: PASS' "${BUG007_FILE}" 2>/dev/null; then
  sfu_pass=1
fi

if [[ "${mxu_fail}" -eq 1 && "${sfu_pass}" -eq 1 ]]; then
  bug007_ok=1
else
  local reasons=""
  [[ "${mxu_fail}" -eq 0 ]] && reasons="${reasons}MXU_FAIL_not_found "
  [[ "${sfu_pass}" -eq 0 ]] && reasons="${reasons}SFU_PASS_not_found "
  bug007_reason="${reasons%% }"
fi

# ---------------------------------------------------------------
# 4. Write checklist
# ---------------------------------------------------------------
{
  echo "# Wrapper-Level Verification Final Wave F3 - QA Checklist"
  echo "# Generated: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  echo ""

  if [[ "${bug005_ok}" -eq 1 ]]; then
    echo "BUG005_OK=1"
  else
    echo "BUG005_OK=0 reason=${bug005_reason}"
  fi

  if [[ "${bug007_ok}" -eq 1 ]]; then
    echo "BUG007_OK=1"
  else
    echo "BUG007_OK=0 reason=${bug007_reason}"
  fi

  if [[ "${regression_pass}" -eq 1 ]]; then
    echo "REGRESSION_PASS=1"
  else
    echo "REGRESSION_PASS=0 reason=${regression_reason}"
  fi
} > "${CHECKLIST}"

echo "=== wv_f3_qa.sh completed ==="
echo "BUG005_OK=${bug005_ok}  ${bug005_reason}"
echo "BUG007_OK=${bug007_ok}  ${bug007_reason}"
echo "REGRESSION_PASS=${regression_pass}  ${regression_reason}"
echo "Checklist: ${CHECKLIST}"

