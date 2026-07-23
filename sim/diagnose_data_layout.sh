#!/bin/bash
# =============================================================================
# diagnose_data_layout.sh — Phase 8 Data-Layout Hypothesis Diagnostic
# =============================================================================
# Uses MXU wrapper preload path (not firmware doorbell) to isolate
# activation layout as the only variable.
#
# Usage:
#   cd CaduceusCore
#   bash sim/diagnose_data_layout.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
cd "$REPO_ROOT"

source "$REPO_ROOT/sim/regression/run_env.sh"

BUILD_DIR="$REPO_ROOT/build/ibex_full_rtl"
SIMV="$BUILD_DIR/simv_soc_ibex"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$EVIDENCE_DIR"

if [ ! -x "$SIMV" ]; then
    echo "[ERROR] simv_soc_ibex not found at $SIMV"
    exit 1
fi

export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.diagnose_data_layout
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex"
export TESTCASE=test_diag_both

LOG="$EVIDENCE_DIR/ph8-diagnostic-both.log"

echo "============================================================"
echo "[DIAGNOSTIC] Running test_diag_both (MXU wrapper preload path)"
echo "[DIAGNOSTIC] MODULE=$MODULE TESTCASE=$TESTCASE"
echo "[DIAGNOSTIC] Log: $LOG"
echo "============================================================"

(cd "$RUN_DIR" && "$SIMV" +COCOTB +BOOTROM_HEX="$BOOTROM_HEX" \
    -l "$LOG" \
    > "$LOG" 2>&1) || true

echo ""
if grep -q 'HYPOTHESIS CONFIRMED' "$EVIDENCE_DIR/ph8-diagnostic.txt" 2>/dev/null; then
    echo "============================================================"
    echo " HYPOTHESIS CONFIRMED"
    echo "============================================================"
    grep -A5 'VERDICT' "$EVIDENCE_DIR/ph8-diagnostic.txt" 2>/dev/null || true
elif grep -q 'HYPOTHESIS FALSIFIED' "$EVIDENCE_DIR/ph8-diagnostic.txt" 2>/dev/null; then
    echo "============================================================"
    echo " HYPOTHESIS FALSIFIED"
    echo "============================================================"
    cat "$EVIDENCE_DIR/ph8-diagnostic.txt"
elif [ -f "$EVIDENCE_DIR/ph8-hypothesis-falsified.txt" ]; then
    echo "============================================================"
    echo " HYPOTHESIS FALSIFIED"
    echo "============================================================"
    cat "$EVIDENCE_DIR/ph8-hypothesis-falsified.txt"
else
    echo "[FAIL] No verdict — see log: $LOG"
    echo "--- last 30 lines ---"
    tail -30 "$LOG"
    echo "--- end ---"
    exit 1
fi

exit 0
