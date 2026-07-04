#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# RTL $readmemh paths (e.g. CaduceusCore/rtl/test_vectors/sfu/luts/*.hex)
# are relative to the repository parent directory, so simv must run from there.
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
cd "$REPO_ROOT"

source "$REPO_ROOT/sim/regression/run_env.sh"

BUILD_DIR="$REPO_ROOT/build/p1_full_rtl"
P0_SIMV="$REPO_ROOT/build/p0_full_rtl/simv_soc_spike"
SIMV="$BUILD_DIR/simv_soc_spike"
mkdir -p "$BUILD_DIR"

if [ $# -ge 1 ]; then
    CASES="$1"
else
    CASES="FM-SOC-009 FM-SOC-010 FM-SOC-011 FM-SOC-012 FM-SOC-024 FM-SOC-025 FM-SOC-026"
fi

export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.rtl_soc_runner
export TOPLEVEL=tb_soc_spike
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=spike
export TESTCASE=test_soc_spike_p1

if [ ! -x "$SIMV" ] && [ -x "$P0_SIMV" ]; then
    echo "[INFO] Reusing existing P0 simv: $P0_SIMV"
    SIMV="$P0_SIMV"
fi

if [ ! -x "$SIMV" ]; then
    echo "[INFO] Compiling simv_soc_spike for P1 ..."
    vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
        -kdb \
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
fi

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
    export TESTCASE=test_soc_spike_p1
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
echo "[SUMMARY] P1 Full RTL Spike-firmware"
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  SKIP: $SKIP"
echo "  TOTAL: $((PASS + FAIL + SKIP))"
echo "============================================================"

if [ $FAIL -ne 0 ]; then
    exit 1
fi
