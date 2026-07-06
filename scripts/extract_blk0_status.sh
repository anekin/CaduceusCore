#!/usr/bin/env bash
# extract_blk0_status.sh
# Parse qwen_blk0.log for per-op PASS/FAIL summary and mismatch details.
# Usage: ./extract_blk0_status.sh [log-path]

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${1:-${REPO_ROOT}/sim/regression/qwen_blk0.log}"

echo "=== Per-op PASS/FAIL ==="
grep -E '\[BLK0\] op[0-9]+' "$LOG" | tail -n 30 || true

echo ""
echo "=== Failed ops summary ==="
grep -E '\[BLK0\] Failed ops|FAIL|MISMATCH|op[0-9]+.*(FAIL|MISMATCH)' "$LOG" | tail -n 40 || true

echo ""
echo "=== Mismatch address/value details ==="
grep -E 'addr=0x[0-9a-f]+.*exp=.*got=' "$LOG" | tail -n 50 || true

echo ""
echo "=== Final result line ==="
grep -E 'test_qwen_blk0\s+(PASS|FAIL)|E2E_BLK0:' "$LOG" | tail -n 5 || true

echo ""
echo "=== FSDB artifacts ==="
ls -lh "$(dirname "$LOG")"/*.fsdb* 2>/dev/null || echo "No FSDB found in $(dirname "$LOG")"
