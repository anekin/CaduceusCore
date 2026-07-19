#!/bin/bash
# =============================================================================
# Phase 8 Task 7: Fullchain 5-gap Pipeline Re-run on sz0001
# =============================================================================
# Runs test_w4_perf_fullchain_sfu_vector against simv_soc_ibex.
# Saves full VCS log to build/evidence/ph8-fullchain.log.
# The test writes build/evidence/fullchain-pipeline.txt.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$EVIDENCE_DIR"

echo "=== Phase 8 Task 7: Fullchain 5-gap Pipeline ==="
echo "REPO_ROOT=$REPO_ROOT"
echo "RUN_DIR=$RUN_DIR"

# Source EDA environment
echo "[STEP 1] Sourcing EDA environment..."
source "$REPO_ROOT/sim/regression/run_env.sh" || {
    echo "[ERROR] Failed to source EDA environment"
    exit 1
}

# Verify simv exists
SIMV="$REPO_ROOT/build/ibex_full_rtl/simv_soc_ibex"
if [ ! -x "$SIMV" ]; then
    echo "[ERROR] simv_soc_ibex not found at $SIMV"
    exit 1
fi
echo "[OK] simv_soc_ibex: $SIMV"

# Verify firmware
BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex"
if [ ! -f "$BOOTROM_HEX" ]; then
    echo "[ERROR] Firmware hex not found at $BOOTROM_HEX"
    exit 1
fi
echo "[OK] firmware: $BOOTROM_HEX"

# Cocotb configuration
export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.perf_tests
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export BOOTROM_HEX="$BOOTROM_HEX"
export TESTCASE=test_w4_perf_fullchain_sfu_vector

LOG_FILE="$EVIDENCE_DIR/ph8-fullchain.log"
echo "[RUN] test_w4_perf_fullchain_sfu_vector → $LOG_FILE"

(
    cd "$RUN_DIR"
    "$SIMV" +COCOTB +BOOTROM_HEX="$BOOTROM_HEX" -l "$LOG_FILE" > "$LOG_FILE" 2>&1
) || SIMV_EXIT=$?

echo ""
echo "=== SIMV exit code: ${SIMV_EXIT:-0} ==="

# Check evidence file
EVIDENCE_FILE="$EVIDENCE_DIR/fullchain-pipeline.txt"
if [ -f "$EVIDENCE_FILE" ]; then
    echo ""
    echo "=== Evidence file: $EVIDENCE_FILE ==="
    cat "$EVIDENCE_FILE"
    echo ""
    echo "Lines: $(wc -l < "$EVIDENCE_FILE")"
else
    echo "[WARN] Evidence file not found: $EVIDENCE_FILE"
    # Try to find it elsewhere
    find "$REPO_ROOT" -name "fullchain-pipeline.txt" -newer "$LOG_FILE" 2>/dev/null | head -5
fi

# Show log tail
echo ""
echo "=== Log tail (last 40 lines) ==="
tail -40 "$LOG_FILE"

echo ""
echo "=== Fullchain run complete ==="
