#!/usr/bin/env bash
# soc-verification-run.sh
# Script-first runner for SoC e2e isolated targets.
# Usage: ./soc-verification-run.sh <make-target> [clean]
# Example: ./soc-verification-run.sh run_e2e_mxu_multi
#
# VCS/Verdi live on the EDA server (sz0001 / 192.168.0.11).  If this script is
# invoked from another host, it re-executes itself over SSH on the EDA server
# so that module paths and licenses are available.

set -euo pipefail

TARGET="${1:-}"
CLEAN="${2:-${CLEAN:-1}}"
REPO_ROOT="${REPO_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
SOC_DIR="${REPO_ROOT}/sim/regression"
EDA_USER="${EDA_USER:-$USER}"
EDA_HOST="${EDA_HOST:-192.168.0.11}"

if [[ -z "$TARGET" ]]; then
    echo "ERROR: missing make target"
    echo "Usage: $0 <make-target>"
    exit 1
fi

# If we are not on the EDA server, re-run this script there via SSH.
# The project path is identical on both sides (NFS-shared).
if [[ "$(hostname -s)" != "sz0001" ]]; then
    echo "[soc-run] current host is $(hostname -s); forwarding to $EDA_HOST"
    exec ssh -o BatchMode=yes -o ConnectTimeout=10 "${EDA_USER}@${EDA_HOST}" \
        "bash '${REPO_ROOT}/sim/regression/soc-verification-run.sh' '$TARGET' '$CLEAN'"
fi

echo "[soc-run] target=$TARGET clean=$CLEAN repo=$REPO_ROOT"

# Load EDA tool environment (must be sourced, not executed standalone)
source /NAS/Tools/methodology/modules/init/bash
module load vcs/vcs_2023.12sp2

cd "$SOC_DIR"

# Avoid the VCS incremental-compile trap after wrapper/bridge changes.
if [[ "$CLEAN" == "1" ]]; then
    echo "[soc-run] removing stale simv to force full rebuild"
    rm -rf simv_soc_cocotb simv_soc_cocotb.daidir csrc
fi

echo "[soc-run] make $TARGET"
if make "$TARGET"; then
    echo "[soc-run] RESULT: PASS for $TARGET"
    exit 0
else
    echo "[soc-run] RESULT: FAIL for $TARGET"
    exit 1
fi
