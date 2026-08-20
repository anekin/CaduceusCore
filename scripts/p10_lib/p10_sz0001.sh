#!/usr/bin/env bash
set -euo pipefail
export REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SZ0001="${SZ0001:-192.168.0.11}"
export ZHENGS="zhengs"

# When the script itself runs on sz0001 (e.g. inside a tmux session started
# there), execute commands locally: sz0001 has no ssh key for self-login, so
# the remote path would fail with "Permission denied (publickey)".
if [ "$(hostname -s 2>/dev/null || hostname)" = "sz0001" ]; then
  p10_ssh() {
    bash -c "set -e; source /NAS/Tools/methodology/modules/init/bash; \
module load vcs/vcs_2023.12sp2; cd '${REPO_ROOT}' && \
source sim/regression/run_env.sh && ${1-}"
  }
else
  p10_ssh() {
    ssh -o ConnectTimeout=10 -o BatchMode=yes "${ZHENGS}@${SZ0001}" "set -e; source /NAS/Tools/methodology/modules/init/bash; module load vcs/vcs_2023.12sp2; cd '${REPO_ROOT}' && source sim/regression/run_env.sh && ${1-}"
  }
fi
p10_chmod() { chmod +x "$@"; }
