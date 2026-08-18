#!/usr/bin/env bash
# Phase 10 T21: Retry Qwen2.5-3B Q8_0 GGUF download (BLOCKED-NETWORK short-circuit).
#
# Wave 5 todo 21 (blocked by todo 2, blocks nothing). The Phase 9 attempt was
# BLOCKED-NETWORK; this retries once with a bounded number of attempts.
#
# Terminal states (both exit 0):
#   DOWNLOAD=SUCCESS  -> model available, evidence records local path
#   DOWNLOAD=FAIL     -> BLOCKED-NETWORK evidence written
#
# Non-network failures (disk full, permission) are classified ERROR and exit
# non-zero so they are never mislabelled as BLOCKED-NETWORK.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
TASK21_FILE="$EVIDENCE_DIR/task-21-phase10-rtl-verification.txt"
FAILED_FILE="$EVIDENCE_DIR/ph10-q8_0-download-FAILED.txt"

MODEL_REPO="Qwen/Qwen2.5-3B-Instruct-GGUF"
MODEL_FILE="qwen2.5-3b-instruct-q8_0.gguf"
MODEL_PATH="${MODEL_PATH:-$HOME/models/qwen2.5-3b-instruct-q8_0.gguf}"
LOCAL_DIR="$(dirname "$MODEL_PATH")"

MAX_RETRIES=3
TIMEOUT_SEC=300

# opt-in sz0001 fallback: only sourced when explicitly requested and local path
# failed, per the "only if sz0001 is needed" rule.
P10_TRY_SZ0001="${P10_TRY_SZ0001:-0}"

# Failure is treated as BLOCKED-NETWORK ONLY when the log shows genuine
# network symptoms (DNS / connect / timeout / reset). Anything else — CLI
# usage errors, HTTP status responses, filesystem or permission issues —
# classifies as a hard error and exits non-zero.
NETWORK_MARKERS='timed out|timed-out|timeout|Could not resolve host|Name or service not known|Temporary failure in name resolution|getaddrinfo failed|Connection refused|Connection reset|Connection timed out|Connection aborted|Network is unreachable|Failed to connect|Operation timed out|Read timed out|Remote end closed connection|Max retries exceeded|curl: \(6\)|curl: \(7\)|curl: \(28\)|Failed to establish a new connection|No route to host|ProxyError'

log_info() { echo "[p10_q8o_retry] $*"; }

verify_model() {
    [ -f "$MODEL_PATH" ] && [ -s "$MODEL_PATH" ]
}

download_local() {
    local log="$1"
    if command -v hf >/dev/null 2>&1; then
        timeout "$TIMEOUT_SEC" hf download "$MODEL_REPO" "$MODEL_FILE" \
            --local-dir "$LOCAL_DIR" >"$log" 2>&1
    else
        timeout "$TIMEOUT_SEC" huggingface-cli download "$MODEL_REPO" "$MODEL_FILE" \
            --local-dir "$LOCAL_DIR" >"$log" 2>&1
    fi
}

download_sz0001() {
    local log="$1"
    source "$REPO_ROOT/scripts/p10_lib/p10_sz0001.sh"
    if p10_ssh "mkdir -p ~/models && timeout $TIMEOUT_SEC hf download '$MODEL_REPO' '$MODEL_FILE' --local-dir ~/models" >>"$log" 2>&1; then
        p10_ssh "test -f ~/models/$MODEL_FILE -a -s ~/models/$MODEL_FILE" \
            && mkdir -p "$LOCAL_DIR" \
            && scp -o ConnectTimeout=10 -o BatchMode=yes \
                 "${SZ0001_USER:-zhengs}@${SZ0001:-192.168.0.11}:~/$MODEL_FILE" \
                 "${MODEL_PATH}.sz0001.tmp" >>"$log" 2>&1 \
            && mv "${MODEL_PATH}.sz0001.tmp" "$MODEL_PATH" >>"$log" 2>&1
    fi
}

is_network_failure() {
    local log="$1"
    grep -qE "$NETWORK_MARKERS" "$log"
}

cleanup_partials() {
    rm -f "$MODEL_PATH" "${MODEL_PATH}.incomplete" "${MODEL_PATH}.sz0001.tmp"
    find "$LOCAL_DIR" -name '*.incomplete' -delete 2>/dev/null || true
}

run_minimal_experiment() {
    # INFO-level, not gated: 6b precision control depends on the Q8_0 model.
    # Only meaningful on DOWNLOAD=SUCCESS; a full 36-layer signoff is out of
    # scope here, so we record the artifact and (if the helper exists) run it.
    local size
    size=$(stat -c%s "$MODEL_PATH" 2>/dev/null || echo 0)
    log_info "INFO minimal 6b precision experiment: Q8_0 asset available at $MODEL_PATH ($size bytes)"
    if [ -x "$REPO_ROOT/scripts/run_q8o_precision_check.sh" ]; then
        log_info "INFO running scripts/run_q8o_precision_check.sh (INFO level, not gated)"
        "$REPO_ROOT/scripts/run_q8o_precision_check.sh" "$MODEL_PATH" || true
    else
        log_info "INFO helper scripts/run_q8o_precision_check.sh not present — recording path only"
    fi
}

main() {
    mkdir -p "$LOCAL_DIR" "$EVIDENCE_DIR"
    NOW=$(date '+%Y-%m-%d %H:%M:%S')
    HOST=$(hostname)

    log_info "Phase 10 T21: retry Q8_0 GGUF download to $MODEL_PATH"
    log_info "host=$HOST retries=$MAX_RETRIES timeout=${TIMEOUT_SEC}s sz0001_fallback=$P10_TRY_SZ0001"

    if verify_model; then
        SIZE=$(stat -c%s "$MODEL_PATH")
        log_info "Q8_0 model already present ($SIZE bytes) — treating as SUCCESS"
        cat > "$TASK21_FILE" <<EOF
# Phase 10 T21: Q8_0 GGUF download retry
# Generated: $NOW
# Host: $HOST
# Status: DOWNLOAD=SUCCESS (already present)
# Path: $MODEL_PATH
# Size: $SIZE bytes

DOWNLOAD=SUCCESS
retries_used: 0
note: Model file was already present locally; no download needed.
EOF
        run_minimal_experiment
        echo "DOWNLOAD=SUCCESS"
        exit 0
    fi

    DOWNLOAD_OK=0
    HARD_ERR=0

    for attempt in $(seq 1 "$MAX_RETRIES"); do
        log="/tmp/p10_q8o_attempt_${attempt}.log"
        log_info "Attempt $attempt/$MAX_RETRIES..."
        : > "$log"

        if [ "$attempt" -eq "$MAX_RETRIES" ] && [ "$P10_TRY_SZ0001" = "1" ] && command -v ssh >/dev/null 2>&1; then
            log_info "  (attempt $attempt via sz0001 fallback)"
            download_sz0001 "$log" || true
        else
            download_local "$log" || true
        fi

        if verify_model; then
            SIZE=$(stat -c%s "$MODEL_PATH")
            log_info "Download SUCCESS on attempt $attempt ($SIZE bytes)"
            DOWNLOAD_OK=1
            break
        fi

        if ! is_network_failure "$log" && [ -s "$log" ]; then
            log_info "  non-network failure detected on attempt $attempt"
            HARD_ERR=1
        fi
        if [ -s "$log" ]; then
            tail -2 "$log" | sed 's/^/  /' || true
        fi

        if [ "$attempt" -lt "$MAX_RETRIES" ]; then
            log_info "Waiting 5s before retry..."
            sleep 5
        fi
    done

    if [ "$DOWNLOAD_OK" = "1" ]; then
        cleanup_partials
        cat > "$TASK21_FILE" <<EOF
# Phase 10 T21: Q8_0 GGUF download retry
# Generated: $NOW
# Host: $HOST
# Status: DOWNLOAD=SUCCESS
# Path: $MODEL_PATH
# Size: $(stat -c%s "$MODEL_PATH") bytes
# Command: hf download $MODEL_REPO $MODEL_FILE --local-dir $LOCAL_DIR

DOWNLOAD=SUCCESS
retries_used: $attempt
note: Q8_0 control experiment may proceed.
EOF
        run_minimal_experiment
        echo "DOWNLOAD=SUCCESS"
        exit 0
    fi

    # All attempts exhausted.
    cleanup_partials

    if [ "$HARD_ERR" = "1" ]; then
        # Never mislabel a filesystem/authorization error as BLOCKED-NETWORK.
        cat > "$TASK21_FILE" <<EOF
# Phase 10 T21: Q8_0 GGUF download retry
# Generated: $NOW
# Host: $HOST
# Status: DOWNLOAD=FAIL, ERROR (non-network failure)

DOWNLOAD=FAIL
BLOCKED-NETWORK: false
note: A non-network error was detected (disk full / permission / filesystem).
     Inspect /tmp/p10_q8o_attempt_*.log and fix before retrying.
EOF
        log_info "ERROR: non-network failure — NOT BLOCKED-NETWORK"
        echo "DOWNLOAD=FAIL"
        echo "ERROR"
        exit 1
    fi

    cat > "$FAILED_FILE" <<EOF
# Phase 10 T21: Q8_0 GGUF Download — FAILED (BLOCKED-NETWORK)
# Generated: $NOW
# Host: $HOST
# Status: BLOCKED-NETWORK
# Target: $MODEL_PATH
#
# All $MAX_RETRIES download attempts failed or timed out (local and/or sz0001).
# Download command: timeout $TIMEOUT_SEC hf download \\
#   $MODEL_REPO $MODEL_FILE --local-dir $LOCAL_DIR

BLOCKED-NETWORK: true
retries: $MAX_RETRIES
timeout_seconds: $TIMEOUT_SEC
exit_code: network_unavailable
note: External network unreachable (DNS/connect/timeout). This does NOT block
     the Phase 10 main workflow.
EOF

    cat > "$TASK21_FILE" <<EOF
# Phase 10 T21: Q8_0 GGUF download retry
# Generated: $NOW
# Host: $HOST
# Status: DOWNLOAD=FAIL, BLOCKED-NETWORK
# Path: $MODEL_PATH

DOWNLOAD=FAIL
BLOCKED-NETWORK
note: Q8_0 GGUF unavailable due to external network blockage. The 6b precision
     control experiment remains blocked; this is an expected terminal state.
     Detailed evidence: $FAILED_FILE
EOF

    log_info "All $MAX_RETRIES attempts failed (network) — wrote BLOCKED-NETWORK evidence"
    echo "DOWNLOAD=FAIL"
    echo "BLOCKED-NETWORK"
    exit 0
}

main "$@"
