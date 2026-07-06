#!/usr/bin/env bash
# run_soc_regression.sh — Full SoC regression wrapper
# Calls run_fm_soc_all.sh (33 FM-SOC cases) and logs to .omo/evidence/.
# Usage: ./scripts/run_soc_regression.sh [case_id]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EVIDENCE_DIR="${REPO_ROOT}/.omo/evidence"
mkdir -p "${EVIDENCE_DIR}"

cd "${REPO_ROOT}"
bash sim/regression/run_fm_soc_all.sh "$@" 2>&1 | tee "${EVIDENCE_DIR}/soc_regression.log"
