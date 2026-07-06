#!/usr/bin/env bash
# run_sz0001_env_check.sh
# SSH to sz0001 EDA server and run the PCIe SoC FM baseline pytest.
# Output is logged to .omo/evidence/env_check.log and a summary is appended to
# .omo/notepads/pcie-dma-implementation/learnings.md.
# Usage: ./scripts/run_sz0001_env_check.sh [ssh-user]
#   Default SSH user: $USER

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SSH_USER="${1:-${USER}}"
SSH_HOST="sz0001"
REMOTE_DIR="wa2_caduceuscore/CaduceusCore"
EVIDENCE_DIR="${REPO_ROOT}/.omo/evidence"
LEARNINGS="${REPO_ROOT}/.omo/notepads/pcie-dma-implementation/learnings.md"
LOG_FILE=".omo/evidence/env_check.log"

mkdir -p "${EVIDENCE_DIR}"

echo "=== Run: $(date) ===" | tee -a "${LOG_FILE}"
echo "=== Connecting to ${SSH_USER}@${SSH_HOST} ===" | tee -a "${LOG_FILE}"
echo "" | tee -a "${LOG_FILE}"

# Execute the PCIe FM pytest on the EDA server
ssh "${SSH_USER}@${SSH_HOST}" \
  "cd ${REMOTE_DIR} && \
   PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py -q -k pcie 2>&1" \
  | tee -a "${LOG_FILE}"

echo "" | tee -a "${LOG_FILE}"
echo "=== Log written to ${LOG_FILE} ===" | tee -a "${LOG_FILE}"

# Parse result and append a concise entry to learnings.md
RESULT_LINE=$(grep -E '^(PASSED|FAILED|ERROR|tests/.*PASSED|tests/.*FAILED)' "${LOG_FILE}" | tail -1 || true)
if [ -n "${RESULT_LINE}" ]; then
  {
    echo ""
    echo "## [$(date +%F)] sz0001 Env Check"
    echo "- Result: ${RESULT_LINE}"
    echo "- Log: \`${LOG_FILE}\`"
  } >> "${LEARNINGS}"
  echo "=== Appended summary to ${LEARNINGS} ===" | tee -a "${LOG_FILE}"
else
  echo "=== No clear PASS/FAIL line found; learnings.md not updated ===" | tee -a "${LOG_FILE}"
fi
