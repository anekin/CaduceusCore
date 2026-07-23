#!/usr/bin/env bash
set -euo pipefail
export REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SZ0001="192.168.0.11"
export ZHENGS="zhengs"
p9_ssh() {
  ssh "${ZHENGS}@${SZ0001}" "set -e; source /NAS/Tools/methodology/modules/init/bash; module load vcs/vcs_2023.12sp2; cd '${REPO_ROOT}' && source sim/regression/run_env.sh && ${1-}"
}
p9_chmod() { chmod +x "$@"; }
