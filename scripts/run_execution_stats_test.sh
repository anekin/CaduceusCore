#!/usr/bin/env bash
# run_execution_stats_test.sh — Start device_server, run execution_stats test, stop.
set -euo pipefail

BIN="$1"
SOCK="/tmp/caduceus_stats.sock"
URI="fm://unix?path=${SOCK}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "=== Starting FM device server ==="
PYTHONPATH="${REPO_ROOT}/sim:${REPO_ROOT}/gen" python3 "${REPO_ROOT}/sim/device_server.py" --sock "${SOCK}" &
SERVER_PID=$!
sleep 1

cleanup() {
    echo "=== Stopping FM device server ==="
    kill "${SERVER_PID}" 2>/dev/null || true
    wait "${SERVER_PID}" 2>/dev/null || true
    rm -f "${SOCK}"
}
trap cleanup EXIT

echo "=== Running execution_stats test ==="
"${BIN}" "${URI}"
RC=$?

exit $RC
