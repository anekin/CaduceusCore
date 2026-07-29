#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOCK="/tmp/caduceus_mmul_negative.sock"
rm -f "$SOCK"
cd "$REPO_ROOT"
PYTHONPATH=sim:gen python3 sim/device_server.py --sock "$SOCK" &
SERVER_PID=$!
sleep 1
"${1:-./build/software/test_fm_e2e_mmul}" "fm://unix?path=$SOCK" --negative
RC=$?
kill "$SERVER_PID" 2>/dev/null || true
wait "$SERVER_PID" 2>/dev/null || true
rm -f "$SOCK"
exit $RC
