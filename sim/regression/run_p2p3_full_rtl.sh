#!/bin/bash
# =============================================================================
# run_p2p3_full_rtl.sh — FM-SOC-013/027/017-020/028-031 RTL + Spike CPU regression
# =============================================================================
# Runs P2 integration and P3 boundary cases against the full RTL SoC with a
# Spike RISC-V CPU driving the CPU AXI4/APB master ports.
#
# Usage:
#   cd CaduceusCore
#   bash sim/regression/run_p2p3_full_rtl.sh [case_id]
#
# If case_id is omitted, all active P2+P3 cases are run sequentially against
# the same compiled simv.  Superseded cases (014/015/016) are not executed.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# RTL $readmemh paths are relative to the repository parent directory.
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
cd "$REPO_ROOT"

# Source EDA environment (VCS, cocotb Python)
source "$REPO_ROOT/sim/regression/run_env.sh"

# Build directories
BUILD_DIR="$REPO_ROOT/build/p2p3_full_rtl"
P0_SIMV="$REPO_ROOT/build/p0_full_rtl/simv_soc_spike"
P1_SIMV="$REPO_ROOT/build/p1_full_rtl/simv_soc_spike"
SIMV="$BUILD_DIR/simv_soc_spike"
mkdir -p "$BUILD_DIR"

# Determine case list
if [ $# -ge 1 ]; then
    CASES="$1"
else
    CASES="FM-SOC-013 FM-SOC-027 FM-SOC-017 FM-SOC-018 FM-SOC-019 FM-SOC-020 FM-SOC-028 FM-SOC-029 FM-SOC-030 FM-SOC-031"
fi

# Cocotb module discovery
export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.rtl_soc_runner
export TOPLEVEL=tb_soc_spike
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=spike
export TESTCASE=test_soc_spike_p2p3

# Reuse an existing simv if available; Python runner changes do not require
# VCS recompilation, and the P0/P1 builds already compiled tb_soc_spike.
if [ ! -x "$SIMV" ]; then
    if [ -x "$P1_SIMV" ]; then
        echo "[INFO] Reusing existing P1 simv: $P1_SIMV"
        SIMV="$P1_SIMV"
    elif [ -x "$P0_SIMV" ]; then
        echo "[INFO] Reusing existing P0 simv: $P0_SIMV"
        SIMV="$P0_SIMV"
    fi
fi

if [ ! -x "$SIMV" ]; then
    echo "[INFO] Compiling simv_soc_spike for P2P3 ..."
    vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
        -kdb \
        -Mdir="$BUILD_DIR/csrc" \
        -f "$REPO_ROOT/rtl/cpu/ibex.flist" \
        -f "$REPO_ROOT/rtl/ip/verilog-axi.flist" \
        -f "$REPO_ROOT/rtl/ip/verilog-pcie.flist" \
        -f "$REPO_ROOT/rtl/soc/soc.flist" \
        "$REPO_ROOT/rtl/tb/tb_soc_spike.v" \
        -top tb_soc_spike \
        -o "$SIMV" \
        -l "$BUILD_DIR/elaborate.log" \
        +vpi \
        -P "$PLI_TAB" \
        -load "$COCOTB_VPI_LIB"
    echo "[INFO] Compile complete: $SIMV"
else
    echo "[INFO] Reusing existing simv: $SIMV"
fi

# Run cases sequentially
EVIDENCE_DIR="$BUILD_DIR/evidence"
mkdir -p "$EVIDENCE_DIR"

PASS=0
FAIL=0
SKIP=0
for CASE in $CASES; do
    echo ""
    echo "============================================================"
    echo "[RUN] $CASE"
    echo "============================================================"
    export FM_SOC_CASE_ID="$CASE"
    export TESTCASE=test_soc_spike_p2p3
    CASE_LOG="$EVIDENCE_DIR/${CASE}.log"
    (cd "$RUN_DIR" && "$SIMV" +COCOTB +FM_SOC_CASE_ID="$CASE") > "$CASE_LOG" 2>&1 || true
    if grep -qE 'TESTS=1 PASS=1 FAIL=0 SKIP=0' "$CASE_LOG"; then
        echo "[PASS] $CASE"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] $CASE (log: $CASE_LOG)"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "============================================================"
echo "[SUMMARY] P2+P3 Full RTL + Spike"
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  SKIP: $SKIP"
echo "  TOTAL: $((PASS + FAIL + SKIP))"
echo "============================================================"

if [ $FAIL -ne 0 ]; then
    exit 1
fi
