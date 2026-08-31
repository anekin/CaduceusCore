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

# ── Wall-time cap validation (fail-closed; todo 9) ──────────────────────────
# Strict positive-integer check BEFORE the EDA gate / compile / launch so that
# garbage values (--help, abc, -5, 0, oversized) abort with exit 2 and never
# reach the simulator.  This must remain the FIRST mention of the cap variable
# in this script: the verbatim-extraction contract of test_timeout_behavior.sh
# (todo 1) anchors on it and requires invalid values to exit 2 before any
# simulator launch.
SEG_TIMEOUT_S="${SEG_TIMEOUT_S:-86400}"
case "$SEG_TIMEOUT_S" in
    ''|*[!0-9]*)
        echo "ERROR: SEG_TIMEOUT_S must be a strict positive integer (seconds); got '$SEG_TIMEOUT_S'" >&2
        exit 2
        ;;
esac
if ! [ "$SEG_TIMEOUT_S" -ge 1 ] 2>/dev/null; then
    echo "ERROR: SEG_TIMEOUT_S must be >= 1 (seconds); got '$SEG_TIMEOUT_S'" >&2
    exit 2
fi
echo "[INFO] Wall-time cap: ${SEG_TIMEOUT_S}s (24h default; override via SEG_TIMEOUT_S)"

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

# ── Provenance binding (todo 11) ─────────────────────────────────────────
# Snapshot timing contract: this block runs AFTER the firmware is rebuilt
# (make -C firmware clean all, todo 13 flow) and after the simv compile above,
# but BEFORE the simulator starts — the recorded firmware/simv/flist/driver/
# golden/checkpoint sha256 values therefore bind the evidence to exactly the
# build this run exercises.  The same block is prepended to any evidence this
# run writes (including the timeout evidence below).
RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S)-$$}"
export IBEX_RUN_ID="$RUN_ID"
export IBEX_SIMV="$SIMV"
PROVENANCE_FILE="$REPO_ROOT/build/evidence/provenance-$RUN_ID.txt"
mkdir -p "$REPO_ROOT/build/evidence"
python3 "$REPO_ROOT/scripts/gen_evidence_provenance.py" \
    --run-id "$RUN_ID" \
    --simv "$SIMV" \
    --flist "$REPO_ROOT/rtl/soc/soc.flist" \
    --driver "$REPO_ROOT/sim/rtl_soc_segment_run.py" \
    --firmware "$REPO_ROOT/firmware/build/npu_firmware.hex" \
    --golden "$REPO_ROOT/rtl/test_vectors/soc_e2e/qwen25-3b-36layer" \
    --checkpoint "$REPO_ROOT/build/evidence/task-14-soc-rtl-verification-checkpoints.npz" \
    --out "$PROVENANCE_FILE" \
    || echo "[WARN] provenance generation failed (evidence will lack hash binding)"
echo "[INFO] Provenance (hash-bound evidence header, todo 11):"
sed -e 's/^/    | /' "$PROVENANCE_FILE" 2>/dev/null || true

echo "[INFO] Running segment-run cocotb test (MODULE=sim.rtl_soc_segment_run)"
set +e
(cd "$RUN_DIR" && timeout --signal=TERM --kill-after=600 "$SEG_TIMEOUT_S" "$SIMV" \
    +COCOTB \
    +FM_SOC_CASE_ID=SEGMENT-RUN \
    +BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex")
RUN_RC=$?
set -e
# Fail-closed timeout handling (todo 9): 124 = GNU timeout SIGTERM at wall-time
# cap, 137 = SIGKILL.  Either way the run did NOT complete — write TIMEOUT
# evidence to a FRESH run-keyed file (never append to a pre-existing evidence
# file) and exit non-zero so callers can never read a timed-out run as SUCCESS.
# test_timeout_behavior.sh (todo 1) anchors verbatim on the 124-decision line
# below and requires this region to exit non-zero for RUN_RC=124.
if [ "$RUN_RC" -eq 124 ]; then
    TIMEOUT_LABEL="TIMEOUT_24H"
    TIMEOUT_NOTE="run killed by wall-time cap (SIGTERM); checkpoints after the last completed one are marked PENDING"
elif [ "$RUN_RC" -eq 137 ]; then
    TIMEOUT_LABEL="TIMEOUT_KILLED"
    TIMEOUT_NOTE="run killed by SIGKILL; checkpoints after the last completed one are marked PENDING"
fi
if [ -n "${TIMEOUT_LABEL:-}" ]; then
    RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S)-$$}"
    EVIDENCE_DIR="$REPO_ROOT/build/evidence"
    mkdir -p "$EVIDENCE_DIR"
    EVIDENCE="$EVIDENCE_DIR/task-14-soc-rtl-verification-signoff-$RUN_ID.txt"
    {
        if [ -f "${PROVENANCE_FILE:-}" ]; then
            echo "provenance_source=$PROVENANCE_FILE"
            cat "$PROVENANCE_FILE"
        fi
        echo "timebox_status=$TIMEOUT_LABEL"
        echo "timebox_note=$TIMEOUT_NOTE"
        echo "runner_exit=$RUN_RC"
        echo "seg_timeout_s=$SEG_TIMEOUT_S"
        echo "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        echo "commit=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    } > "$EVIDENCE"
    echo "[TIMEOUT] ${SEG_TIMEOUT_S}s wall-time cap reached (exit=$RUN_RC) — evidence written to fresh run-keyed file: $EVIDENCE"
    exit "$RUN_RC"
fi
exit "$RUN_RC"
