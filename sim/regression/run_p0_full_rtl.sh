#!/bin/bash
# =============================================================================
# run_p0_full_rtl.sh — FM-SOC-001..008 RTL + Spike CPU regression
# =============================================================================
# Runs all P0 SoC infrastructure/data-integrity cases against the full RTL SoC
# with a Spike RISC-V CPU driving the CPU AXI4/APB master ports.
#
# Usage:
#   cd CaduceusCore
#   bash sim/regression/run_p0_full_rtl.sh [case_id]
#
# If case_id is omitted, all FM-SOC-001..008 cases are run sequentially
# against the same compiled simv.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Source EDA environment (VCS, cocotb Python)
source "$REPO_ROOT/sim/regression/run_env.sh"

# Build directories
BUILD_DIR="$REPO_ROOT/build/p0_full_rtl"
SIMV="$BUILD_DIR/simv_soc_spike"
mkdir -p "$BUILD_DIR"

# Determine case list
if [ $# -ge 1 ]; then
    CASES="$1"
else
    CASES="FM-SOC-001 FM-SOC-002 FM-SOC-003 FM-SOC-004 FM-SOC-005 FM-SOC-006 FM-SOC-007 FM-SOC-008"
fi

# Cocotb module discovery
export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.rtl_soc_runner
export TOPLEVEL=tb_soc_spike
export TOPLEVEL_LANG=verilog

# Compile simv if not present
if [ ! -x "$SIMV" ]; then
    echo "[INFO] Compiling simv_soc_spike ..."
    vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
        -kdb \
        -f "$REPO_ROOT/rtl/ip/verilog-axi.flist" \
        -f "$REPO_ROOT/rtl/ip/verilog-pcie.flist" \
        -f "$REPO_ROOT/rtl/soc/soc.flist" \
        -top tb_soc_spike \
        -o "$SIMV" \
        -l "$BUILD_DIR/elaborate.log" \
        +vpi \
        -P "$PLI_TAB"
    echo "[INFO] Compile complete: $SIMV"
else
    echo "[INFO] Reusing existing simv: $SIMV"
fi

# Run cases sequentially
EVIDENCE_DIR="$BUILD_DIR/evidence"
mkdir -p "$EVIDENCE_DIR"

PASS=0
FAIL=0
for CASE in $CASES; do
    echo ""
    echo "============================================================"
    echo "[RUN] $CASE"
    echo "============================================================"
    export FM_SOC_CASE_ID="$CASE"
    export FM_SOC_RTL_MODE=spike
    CASE_LOG="$EVIDENCE_DIR/${CASE}.log"
    if "$SIMV" +COCOTB +FM_SOC_CASE_ID="$CASE" > "$CASE_LOG" 2>&1; then
        echo "[PASS] $CASE"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] $CASE (log: $CASE_LOG)"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "============================================================"
echo "[SUMMARY] P0 Full RTL + Spike"
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  TOTAL: $((PASS + FAIL))"
echo "============================================================"

if [ $FAIL -ne 0 ]; then
    exit 1
fi
