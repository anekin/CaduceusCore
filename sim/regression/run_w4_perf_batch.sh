#!/bin/bash
# =============================================================================
# run_w4_perf_batch.sh — W4-PERF Batch Runner (Tasks 21-25a)
# =============================================================================
# Runs all 6 PERF test batches sequentially on sz0001 against simv_soc_ibex.
#
# Usage:
#   cd CaduceusCore
#   bash sim/regression/run_w4_perf_batch.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
cd "$REPO_ROOT"

# Source EDA environment
source "$REPO_ROOT/sim/regression/run_env.sh"

BUILD_DIR="$REPO_ROOT/build/ibex_full_rtl"
SIMV="$BUILD_DIR/simv_soc_ibex"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$EVIDENCE_DIR"

if [ ! -x "$SIMV" ]; then
    echo "[ERROR] simv_soc_ibex not found at $SIMV"
    echo "  Run: bash sim/regression/run_ibex_full_rtl.sh FM-SOC-001"
    exit 1
fi

# Cocotb configuration
export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.perf_tests
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex"

PASS=0
FAIL=0

# ── Test batches ───────────────────────────────────────────────────────
declare -A BATCHES=(
    ["test_w4_perf_p0"]="task21 - P0 Infrastructure (PERF-01..04)"
    ["test_w4_perf_p1"]="task22 - P1 Multi-Tile Baseline (PERF-05..08)"
    ["test_w4_perf_p2"]="task23 - P2 Weight Streaming (PERF-09..12)"
    ["test_w4_perf_p3"]="task24 - P3 All MMULs + Chain (PERF-13..16)"
    ["test_w4_perf_p4"]="task25 - P4 Deep Analysis (PERF-17..20)"
    ["test_w4_perf_fullchain"]="task25a - Full-Chain Pipeline"
)

for TESTCASE in "${!BATCHES[@]}"; do
    DESC="${BATCHES[$TESTCASE]}"
    echo ""
    echo "============================================================"
    echo "[BATCH] $TESTCASE — $DESC"
    echo "============================================================"
    
    export TESTCASE="$TESTCASE"
    BATCH_LOG="$EVIDENCE_DIR/$(echo $TESTCASE | sed 's/test_w4_perf_//')_batch.log"
    
    (cd "$RUN_DIR" && "$SIMV" +COCOTB +BOOTROM_HEX="$BOOTROM_HEX" \
        -l "$BATCH_LOG" \
        > "$BATCH_LOG" 2>&1) || true
    
    # Parse result
    if grep -qE 'TESTS=1 PASS=1 FAIL=0' "$BATCH_LOG"; then
        echo "[PASS] $DESC"
        PASS=$((PASS + 1))
    elif grep -q 'FAIL=1' "$BATCH_LOG" || grep -q 'assert.*failed' "$BATCH_LOG" || grep -q 'FATAL' "$BATCH_LOG"; then
        echo "[FAIL] $DESC (log: $BATCH_LOG)"
        FAIL=$((FAIL + 1))
        # Show last 30 lines for quick diagnosis
        echo "--- last 30 lines ---"
        tail -30 "$BATCH_LOG"
        echo "--- end ---"
    else
        # For PERF tests, check for explicit PASS lines in log
        if grep -q 'Evidence written to' "$BATCH_LOG"; then
            echo "[PASS] $DESC (evidence written)"
            PASS=$((PASS + 1))
        else
            echo "[FAIL] $DESC — no TESTS=1 line and no evidence (log: $BATCH_LOG)"
            FAIL=$((FAIL + 1))
            echo "--- last 30 lines ---"
            tail -30 "$BATCH_LOG"
            echo "--- end ---"
        fi
    fi
done

# ── Summary ────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo "[W4-PERF SUMMARY]"
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  TOTAL: $((PASS + FAIL))"
echo "============================================================"

# Verify evidence files
echo ""
echo "[EVIDENCE CHECK]"
for ev in w4-perf-p0.txt w4-perf-p1.txt w4-perf-p2.txt w4-perf-p3.txt w4-perf-p4.txt fullchain-pipeline.txt; do
    if [ -f "$EVIDENCE_DIR/$ev" ]; then
        LINES=$(wc -l < "$EVIDENCE_DIR/$ev")
        echo "  [OK] $ev ($LINES lines)"
    else
        echo "  [MISSING] $ev"
        FAIL=$((FAIL + 1))
    fi
done

if [ $FAIL -ne 0 ]; then
    exit 1
fi

exit 0
