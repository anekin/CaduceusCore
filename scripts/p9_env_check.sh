#!/usr/bin/env bash
set -euo pipefail
source "$(dirname $0)/p9_lib/p9_sz0001.sh"
echo "[p9_env_check] placeholder"; p9_ssh "ls build/evidence/ph9-* 2>/dev/null || true; which vcs; test -f firmware/build/npu_firmware.elf; git status --short | head"
