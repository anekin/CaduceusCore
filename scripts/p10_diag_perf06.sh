#!/usr/bin/env bash
# =============================================================================
# p10_diag_perf06.sh — Phase 10 PERF-06 hypothesis-driven diagnosis
# =============================================================================
# Runs a cocotb diagnostic on sz0001 that compares M=1 (control) with M=32
# (failing PERF-06), probes MXU control signals, and writes evidence.
#
# Usage:
#   bash scripts/p10_diag_perf06.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Source Phase-10 sz0001 helpers (defines p10_ssh, p10_chmod)
source "$REPO_ROOT/scripts/p10_lib/p10_sz0001.sh"

SIMV="$REPO_ROOT/build/ibex_full_rtl/simv_soc_ibex"
BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
EVIDENCE_FILE="$EVIDENCE_DIR/task-7-phase10-rtl-verification.txt"
DIAG_PY="$REPO_ROOT/sim/p10_diag_perf06.py"
DIAG_LOG="$EVIDENCE_DIR/p10-diag-perf06.log"

mkdir -p "$EVIDENCE_DIR"

if [ ! -x "$SIMV" ]; then
    echo "[ERROR] simv_soc_ibex not found at $SIMV"
    echo "  Run: bash sim/regression/run_ibex_full_rtl.sh FM-SOC-001"
    exit 1
fi

if [ ! -f "$BOOTROM_HEX" ]; then
    echo "[ERROR] Boot ROM hex not found at $BOOTROM_HEX"
    echo "  Run: make -C firmware"
    exit 1
fi

if [ ! -f "$DIAG_PY" ]; then
    echo "[ERROR] Diagnostic Python module not found at $DIAG_PY"
    exit 1
fi

# Ensure the diagnostic module is executable/accessible on the remote side
p10_chmod "$DIAG_PY"

echo "============================================================"
echo "[P10-DIAG] Running PERF-06 diagnosis on sz0001"
echo "  simv: $SIMV"
echo "  evidence: $EVIDENCE_FILE"
echo "============================================================"

# Run the cocotb diagnostic on the EDA server via p10_ssh.
# Use the repository parent as RUN_DIR so RTL $readmemh paths resolve.
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"

p10_ssh "
export PYTHONPATH=\"${PYTHONPATH:-}:$REPO_ROOT\"
export MODULE=sim.p10_diag_perf06
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export BOOTROM_HEX=\"$BOOTROM_HEX\"
export TESTCASE=test_perf06_diagnosis

cd \"$RUN_DIR\"
\"$SIMV\" +COCOTB +BOOTROM_HEX=\"$BOOTROM_HEX\" \
    -l \"$DIAG_LOG\" \
    > \"$DIAG_LOG\" 2>&1
" || {
    echo "[ERROR] Diagnostic simulation failed (see $DIAG_LOG)"
    exit 1
}

# Copy remote log/evidence to local (they are on NFS, but be explicit)
echo ""
echo "[P10-DIAG] Evidence collection"
if [ -f "$EVIDENCE_FILE" ]; then
    LINES=$(wc -l < "$EVIDENCE_FILE")
    echo "  [OK] $EVIDENCE_FILE ($LINES lines)"
else
    echo "  [MISSING] $EVIDENCE_FILE"
    echo "  Showing tail of $DIAG_LOG:"
    tail -50 "$DIAG_LOG" || true
    exit 1
fi

# Verify required evidence fields
REQUIRED_FIELDS=(
    "M=1 CONTROL"
    "M=32 PERF-06"
    "Register/Config Diff"
    "CTRL[2]"
    "mac_reset_acc"
    "Falsification Experiment"
    "ROOT_CAUSE="
)

MISSING=0
for field in "${REQUIRED_FIELDS[@]}"; do
    if grep -qF "$field" "$EVIDENCE_FILE"; then
        echo "  [OK] contains '$field'"
    else
        echo "  [MISSING] '$field'"
        MISSING=$((MISSING + 1))
    fi
done

if [ $MISSING -ne 0 ]; then
    echo "[ERROR] Evidence file missing $MISSING required field(s)"
    exit 1
fi

echo ""
echo "============================================================"
echo "[P10-DIAG] Done"
echo "  Evidence: $EVIDENCE_FILE"
echo "  Log:      $DIAG_LOG"
echo "============================================================"
exit 0
