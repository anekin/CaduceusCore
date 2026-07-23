#!/bin/bash
# =============================================================================
# Phase 8: Standalone PERF-11 Run (Clean, No Ring Buffer Contention)
# =============================================================================
# Runs ONLY PERF-11 with the current working tree (tile-major packing).
# Writes build/evidence/w4-perf-p2.txt with PERF-11 evidence.
# Also writes a standalone log for PERF-11 SRAM/DRAM hex extraction.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$EVIDENCE_DIR"

source "$REPO_ROOT/sim/regression/run_env.sh" || exit 1

SIMV="$REPO_ROOT/build/ibex_full_rtl/simv_soc_ibex"
BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex"

echo "[STANDALONE PERF-11] Running isolated PERF-11 with tile-major packing..."

export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.perf_tests_standalone_p11
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export TESTCASE=test_w4_perf_p11_standalone
export BOOTROM_HEX

STANDALONE_LOG="$EVIDENCE_DIR/ph8-perf-11-standalone.log"

(cd "$RUN_DIR" && "$SIMV" +COCOTB +BOOTROM_HEX="$BOOTROM_HEX" \
    -l "$STANDALONE_LOG" \
    > "$STANDALONE_LOG" 2>&1) || true

echo ""
echo "[RESULT] Tail of standalone log:"
tail -30 "$STANDALONE_LOG"
echo ""
echo "[DONE] Log: $STANDALONE_LOG"
