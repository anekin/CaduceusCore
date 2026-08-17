#!/usr/bin/env bash
set -euo pipefail
source "$(dirname $0)/p10_lib/p10_sz0001.sh"

# --- bash syntax validation ---
echo "[p10_env_check] bash syntax check"
bash -n "$0"
bash -n "$(dirname $0)/p10_lib/p10_sz0001.sh"

# --- REPO_ROOT resolution ---
echo "[p10_env_check] REPO_ROOT resolution"
test -n "$REPO_ROOT" || { echo "REPO_ROOT empty" >&2; exit 1; }
echo "REPO_ROOT=$REPO_ROOT"
test -d "$REPO_ROOT" || { echo "REPO_ROOT not a directory" >&2; exit 1; }

# --- executable permissions ---
if [ ! -x "$0" ]; then
  echo "[p10_env_check] chmod +x $0"
  p10_chmod "$0"
fi

# --- SSH reachability ---
echo "[p10_env_check] SSH reachability (${ZHENGS}@${SZ0001})"
if p10_ssh "true" >/dev/null 2>&1; then
  echo "p10_ssh ready"
  echo "[p10_env_check] OK"
else
  echo "SSH unreachable: ${ZHENGS}@${SZ0001}" >&2
  exit 1
fi
