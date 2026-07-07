#!/usr/bin/env bash
# run_fm_l2_signoff.sh
# Script-first runner for Func Model L2 signoff (item 5 in
# .omo/plans/soc-verification-gaps-phase5.md).
#
# Runs sim/tests/test_op_dtype_chains.py on the EDA server (sz0001) and writes
# a summary evidence file to build/evidence/w1-5-fm-l2-signoff.txt.
#
# Usage:
#   bash sim/regression/run_fm_l2_signoff.sh

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
EDA_USER="${EDA_USER:-$USER}"
EDA_HOST="${EDA_HOST:-192.168.0.11}"
EVIDENCE_DIR="${REPO_ROOT}/build/evidence"
EVIDENCE_FILE="${EVIDENCE_DIR}/w1-5-fm-l2-signoff.txt"

# If we are not on the EDA server, re-run this script there via SSH.
if [[ "$(hostname -s)" != "sz0001" ]]; then
    echo "[fm-l2] current host is $(hostname -s); forwarding to ${EDA_HOST}"
    exec ssh -o BatchMode=yes -o ConnectTimeout=10 "${EDA_USER}@${EDA_HOST}" \
        "bash '${REPO_ROOT}/sim/regression/run_fm_l2_signoff.sh'"
fi

mkdir -p "${EVIDENCE_DIR}"

echo "[fm-l2] running Func Model L2 dtype-chain signoff on $(hostname -s)"
echo "[fm-l2] repo=${REPO_ROOT}"

cd "${REPO_ROOT}"

# Pure Python Func Model signoff: use the base Anaconda python that has
# pytest+numpy.  No EDA module loading is required for this test.
export PATH="/NAS/Tools/anaconda3/bin:${PATH}"

PYTHONPATH=sim python3 -m pytest sim/tests/test_op_dtype_chains.py -v \
    --tb=short > "${EVIDENCE_FILE}.tmp" 2>&1

pytest_rc=$?

{
    echo "=================================================="
    echo "Func Model L2 Signoff — Dtype Closure Matrix + True Op Chains"
    echo "Host: $(hostname -s)"
    echo "Date: $(date -Iseconds)"
    echo "Commit: $(git rev-parse --short HEAD 2>/dev/null || echo 'unknown')"
    echo "=================================================="
    echo ""
    cat "${EVIDENCE_FILE}.tmp"
    echo ""
    if [[ ${pytest_rc} -eq 0 ]]; then
        echo "VERDICT: PASS — all dtype-chain cases passed."
    else
        echo "VERDICT: FAIL — see pytest output above."
    fi
} > "${EVIDENCE_FILE}"

rm -f "${EVIDENCE_FILE}.tmp"

echo "[fm-l2] evidence written to ${EVIDENCE_FILE}"
exit ${pytest_rc}
