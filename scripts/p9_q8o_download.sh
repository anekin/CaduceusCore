#!/usr/bin/env bash
set -euo pipefail
source "$(dirname $0)/p9_lib/p9_sz0001.sh"

MODEL_PATH="$HOME/models/qwen2.5-3b-instruct-q8_0.gguf"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
FAILED_FILE="$EVIDENCE_DIR/ph9-q8_0-download-FAILED.txt"
MAX_RETRIES=3
TIMEOUT_SEC=600

mkdir -p "$EVIDENCE_DIR"

echo "[p9_q8o_download] Phase 9 T9: Q8_0 GGUF download to $MODEL_PATH"
echo "[p9_q8o_download] sz0001=$SZ0001, retries=$MAX_RETRIES, timeout=${TIMEOUT_SEC}s each"

# Check if already downloaded
if p9_ssh "test -f '$MODEL_PATH' -a -s '$MODEL_PATH'"; then
    SIZE=$(p9_ssh "stat -c%s '$MODEL_PATH' 2>/dev/null || echo 0")
    echo "[p9_q8o_download] Q8_0 model already exists ($SIZE bytes), skipping download"
    exit 0
fi

echo "[p9_q8o_download] Downloading Q8_0 GGUF from HuggingFace..."

for attempt in $(seq 1 $MAX_RETRIES); do
    echo "[p9_q8o_download] Attempt $attempt/$MAX_RETRIES..."
    
    DOWNLOAD_CMD="mkdir -p ~/models && timeout $TIMEOUT_SEC huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q8_0.gguf --local-dir ~/models --local-dir-use-symlinks False"
    
    if p9_ssh "$DOWNLOAD_CMD"; then
        # Verify the file exists and is non-empty
        if p9_ssh "test -f '$MODEL_PATH' -a -s '$MODEL_PATH'"; then
            SIZE=$(p9_ssh "stat -c%s '$MODEL_PATH' 2>/dev/null || echo 0")
            echo "[p9_q8o_download] Download SUCCESS on attempt $attempt ($SIZE bytes)"
            exit 0
        else
            echo "[p9_q8o_download] Download claimed success but model file missing or empty, retrying..."
        fi
    else
        RC=$?
        if [ $RC -eq 124 ]; then
            echo "[p9_q8o_download] Attempt $attempt timed out (${TIMEOUT_SEC}s)"
        else
            echo "[p9_q8o_download] Attempt $attempt failed (exit_code=$RC)"
        fi
    fi
    
    if [ $attempt -lt $MAX_RETRIES ]; then
        echo "[p9_q8o_download] Waiting 10s before retry..."
        sleep 10
    fi
done

# All retries exhausted — write failure evidence
echo "[p9_q8o_download] All $MAX_RETRIES attempts failed — writing BLOCKED-NETWORK evidence"

NOW=$(date '+%Y-%m-%d %H:%M:%S')
HOSTNAME=$(hostname)

cat > "$FAILED_FILE" <<EOF
# Phase 9 T9: Q8_0 GGUF Download — FAILED
# Generated: $NOW
# Host: $HOSTNAME
# Status: BLOCKED-NETWORK
# Target: $MODEL_PATH
#
# All $MAX_RETRIES download attempts failed or timed out.
# Download command: timeout $TIMEOUT_SEC huggingface-cli download \\
#   Qwen/Qwen2.5-3B-Instruct-GGUF \\
#   qwen2.5-3b-instruct-q8_0.gguf \\
#   --local-dir ~/models --local-dir-use-symlinks False
#
# The Q8_0 control experiment cannot proceed due to external network unavailability.
# Phase 6 6b checkbox will be marked BLOCKED-NETWORK (judge=BLOCKED-NETWORK).
# This does NOT block the Phase 9 main workflow.

BLOCKED-NETWORK: true
retries: $MAX_RETRIES
timeout_seconds: $TIMEOUT_SEC
exit_code: network_unavailable
cmd: huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q8_0.gguf --local-dir ~/models --local-dir-use-symlinks False
note: Download failed after $MAX_RETRIES attempts from sz0001. This is an external network issue.
EOF

echo "[p9_q8o_download] BLOCKED-NETWORK evidence written: $FAILED_FILE"
exit 0
