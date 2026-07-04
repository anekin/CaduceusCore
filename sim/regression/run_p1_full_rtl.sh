#!/bin/bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"

source "$REPO_ROOT/sim/regression/run_env.sh"

BUILD_DIR="$REPO_ROOT/build/p1_full_rtl"
P0_SIMV="$REPO_ROOT/build/p0_full_rtl/simv_soc_spike"
SIMV="$BUILD_DIR/simv_soc_spike"
mkdir -p "$BUILD_DIR"

if [ $# -ge 1 ]; then
    CASES="$1"
else
    CASES="FM-SOC-010 FM-SOC-011 FM-SOC-012 FM-SOC-024 FM-SOC-025"
fi

export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.rtl_soc_runner
export TOPLEVEL=tb_soc_spike
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=direct

if [ ! -x "$SIMV" ] && [ -x "$P0_SIMV" ]; then
    echo "[INFO] Reusing existing P0 simv: $P0_SIMV"
    SIMV="$P0_SIMV"
fi

if [ ! -x "$SIMV" ]; then
    echo "[INFO] Compiling simv_soc_spike for P1 ..."
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
    if [ "$CASE" = "FM-SOC-009" ] || [ "$CASE" = "FM-SOC-026" ]; then
        echo "[SKIP] $CASE — doorbell/Spike firmware path blocked by missing/broken npu_mmio_plugin.so"
        SKIP=$((SKIP + 1))
        continue
    fi
    export FM_SOC_CASE_ID="$CASE"
    CASE_LOG="$EVIDENCE_DIR/${CASE}.log"
    if (cd "$RUN_DIR" && "$SIMV" +COCOTB +FM_SOC_CASE_ID="$CASE") > "$CASE_LOG" 2>&1; then
        echo "[PASS] $CASE"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] $CASE (log: $CASE_LOG)"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "============================================================"
echo "[SUMMARY] P1 Full RTL direct-MMIO"
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  SKIP: $SKIP"
echo "  TOTAL: $((PASS + FAIL + SKIP))"
echo "============================================================"

if [ $FAIL -ne 0 ]; then
    exit 1
fi
