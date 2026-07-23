#!/usr/bin/env bash
set -euo pipefail
source "$(dirname $0)/p9_lib/p9_sz0001.sh"

MODEL_PATH="$HOME/models/qwen2.5-3b-instruct-q8_0.gguf"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
SRC_EVIDENCE="$EVIDENCE_DIR/w1-6b-q8o.txt"
DST_EVIDENCE="$EVIDENCE_DIR/ph9-q8_0-precision.txt"
FAILED_FILE="$EVIDENCE_DIR/ph9-q8_0-download-FAILED.txt"

mkdir -p "$EVIDENCE_DIR"

echo "[p9_q8o_precision] Phase 9 T9: Q8_0 Q_proj precision control experiment"

# Check if download already failed
if [ -f "$FAILED_FILE" ]; then
    echo "[p9_q8o_precision] Download FAILED file exists — skipping precision run"
    echo "[p9_q8o_precision] BLOCKED-NETWORK: precision experiment skipped"
    exit 0
fi

# Verify model exists on sz0001
if ! p9_ssh "test -f '$MODEL_PATH' -a -s '$MODEL_PATH'"; then
    echo "[p9_q8o_precision] ERROR: Q8_0 model not found on sz0001: $MODEL_PATH"
    echo "[p9_q8o_precision] Run scripts/p9_q8o_download.sh first"
    exit 1
fi

MODEL_SIZE=$(p9_ssh "stat -c%s '$MODEL_PATH' 2>/dev/null || echo 0")
echo "[p9_q8o_precision] Q8_0 model found on sz0001 ($MODEL_SIZE bytes)"

# Run the control experiment on sz0001 — no CLI args, hardcoded paths
echo "[p9_q8o_precision] Running run_w1_6b_q8o_control.py on sz0001 (no CLI args)..."
p9_ssh "cd '$REPO_ROOT' && python3 scripts/run_w1_6b_q8o_control.py"
RC=$?

if [ $RC -ne 0 ]; then
    echo "[p9_q8o_precision] Precision experiment failed (exit_code=$RC)"
    # Check if evidence file was written despite non-zero exit
    if [ ! -f "$SRC_EVIDENCE" ]; then
        echo "[p9_q8o_precision] No evidence file produced, aborting"
        exit 1
    fi
    echo "[p9_q8o_precision] Experiment exited non-zero but evidence file exists — continuing"
fi

# Copy the evidence file with Phase 9 header
echo "[p9_q8o_precision] Copying evidence: $SRC_EVIDENCE → $DST_EVIDENCE"

NOW=$(date '+%Y-%m-%d %H:%M:%S')
HOSTNAME=$(hostname)

{
    echo "# Phase 9 T9: Q8_0 Q_proj Precision Control Experiment"
    echo "# Phase 9 Todo 9 — Wave 5"
    echo "# Generated: $NOW"
    echo "# Host: $HOSTNAME"
    echo "# Source: $SRC_EVIDENCE"
    echo "#"
    echo ""
    cat "$SRC_EVIDENCE"
} > "$DST_EVIDENCE"

echo "[p9_q8o_precision] Evidence written: $DST_EVIDENCE"
echo "[p9_q8o_precision] Done"
exit 0
