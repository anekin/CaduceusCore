#!/usr/bin/env bash
# run_fm_l3_signoff.sh
# Script-first runner for Func Model L3 signoff (item 6 in
# .omo/plans/soc-verification-gaps-phase5.md).
#
# Runs scripts/verify_36layer_l3.py on the EDA server (sz0001) and writes the
# evidence file to build/evidence/w1-6-fm-l3-signoff.txt.
#
# Usage:
#   bash sim/regression/run_fm_l3_signoff.sh

set -euo pipefail

REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
EDA_USER="${EDA_USER:-$USER}"
EDA_HOST="${EDA_HOST:-192.168.0.11}"
EVIDENCE_DIR="${REPO_ROOT}/build/evidence"
EVIDENCE_FILE="${EVIDENCE_DIR}/w1-6-fm-l3-signoff.txt"

# If we are not on the EDA server, re-run this script there via SSH.
if [[ "$(hostname -s)" != "sz0001" ]]; then
    echo "[fm-l3] current host is $(hostname -s); forwarding to ${EDA_HOST}"
    exec ssh -o BatchMode=yes -o ConnectTimeout=10 "${EDA_USER}@${EDA_HOST}" \
        "bash '${REPO_ROOT}/sim/regression/run_fm_l3_signoff.sh'"
fi

mkdir -p "${EVIDENCE_DIR}"

echo "[fm-l3] running 36-layer Func Model L3 signoff on $(hostname -s)"
echo "[fm-l3] repo=${REPO_ROOT}"

cd "${REPO_ROOT}"

# Pure Python Func Model signoff: use the base Anaconda python that has
# numpy + gguf.  No EDA module loading is required for this test.
export PATH="/NAS/Tools/anaconda3/bin:${PATH}"

PYTHONPATH=sim python3 scripts/verify_36layer_l3.py \
    --evidence "${EVIDENCE_FILE}"

echo "[fm-l3] evidence written to ${EVIDENCE_FILE}"
