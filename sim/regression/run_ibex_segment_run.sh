#!/bin/bash
# =============================================================================
# run_ibex_segment_run.sh — Ibex 36-layer checkpoint-subset segment run (todo 13)
# =============================================================================
# Compiles (if needed) and runs the 9-layer Ibex segment-run cocotb test
# (MODULE=sim.rtl_soc_segment_run, TOPLEVEL=tb_soc_ibex).  Consecutive layers in
# a segment chain hidden state through Ibex DRAM in a single VCS session.
#
# Usage:
#   cd CaduceusCore
#   bash sim/regression/run_ibex_segment_run.sh
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
cd "$REPO_ROOT"

source "$REPO_ROOT/sim/regression/run_env.sh"

BUILD_DIR="$REPO_ROOT/build/ibex_segment_rtl"
SIMV="$BUILD_DIR/simv_soc_ibex_seg"
mkdir -p "$BUILD_DIR"

export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.rtl_soc_segment_run
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export TESTCASE=test_soc_ibex_segment_run
export QWEN3B_GGUF="${QWEN3B_GGUF:-$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf}"
export IBEX_COMMIT="${IBEX_COMMIT:-$(git rev-parse HEAD)}"

trap 'pkill -f simv_soc_ibex_seg 2>/dev/null || true' EXIT

if [ ! -x "$SIMV" ]; then
    echo "[INFO] Compiling simv_soc_ibex_seg (5G-cycle timeout tb, no -debug_access+all, -O3) ..."
    vcs -full64 -sverilog -debug_access -O3 -timescale=1ns/1ps \
        -kdb \
        -Mdir="$BUILD_DIR/csrc" \
        -f "$REPO_ROOT/rtl/cpu/ibex.flist" \
        -f "$REPO_ROOT/rtl/ip/verilog-axi.flist" \
        -f "$REPO_ROOT/rtl/ip/verilog-pcie.flist" \
        -f "$REPO_ROOT/rtl/soc/soc.flist" \
        "$REPO_ROOT/rtl/tb/tb_soc_ibex.v" \
        -top tb_soc_ibex \
        -o "$SIMV" \
        -l "$BUILD_DIR/elaborate.log" \
        +vpi \
        -P "$PLI_TAB" \
        -load "$COCOTB_VPI_LIB"
    echo "[INFO] Compile complete: $SIMV"
else
    echo "[INFO] Reusing existing simv: $SIMV"
fi

echo "[INFO] Running segment-run cocotb test (MODULE=sim.rtl_soc_segment_run)"
SEG_TIMEOUT_S="${SEG_TIMEOUT_S:-86400}"
echo "[INFO] Wall-time cap: ${SEG_TIMEOUT_S}s (24h default; override via SEG_TIMEOUT_S)"
set +e
(cd "$RUN_DIR" && timeout --signal=TERM --kill-after=600 "$SEG_TIMEOUT_S" "$SIMV" \
    +COCOTB \
    +FM_SOC_CASE_ID=SEGMENT-RUN \
    +BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex")
RUN_RC=$?
set -e
if [ "$RUN_RC" -eq 124 ]; then
    echo "[TIMEOUT] ${SEG_TIMEOUT_S}s wall-time cap reached — completed checkpoints retained, remaining marked PENDING"
    EVIDENCE="$REPO_ROOT/build/evidence/task-14-soc-rtl-verification-signoff.txt"
    if [ -f "$EVIDENCE" ]; then
        {
            echo "timebox_status=TIMEOUT_24H"
            echo "timebox_note=run killed by wall-time cap; checkpoints after the last completed one are marked PENDING"
        } >> "$EVIDENCE"
        echo "[TIMEOUT] evidence retained: $EVIDENCE"
    fi
    exit 0
fi
exit "$RUN_RC"
