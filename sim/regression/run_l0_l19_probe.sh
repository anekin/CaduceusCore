#!/bin/bash
# =============================================================================
# run_l0_l19_probe.sh — todo 13 L19 root-cause probe (L0 -> L19 in one VCS
# session with per-wave DRAM readbacks).
# =============================================================================
# Compiles (if needed) and runs the cocotb probe
# (MODULE=sim.rtl_soc_l0_l19_probe, TOPLEVEL=tb_soc_ibex).  Uses its own
# build dir and simv name (simv_soc_ibex_probe) so it can coexist with — and
# must never kill — a concurrently running full segment run
# (simv_soc_ibex_seg).
#
# Usage:
#   cd CaduceusCore
#   MODULE=sim.rtl_soc_l0_l19_probe TESTCASE=test_l0_l19_probe \
#       bash sim/regression/run_l0_l19_probe.sh
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
cd "$REPO_ROOT"

source "$REPO_ROOT/sim/regression/run_env.sh"

BUILD_DIR="$REPO_ROOT/build/ibex_probe_rtl"
SIMV="$BUILD_DIR/simv_soc_ibex_probe"
mkdir -p "$BUILD_DIR"

export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.rtl_soc_l0_l19_probe
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export TESTCASE=test_l0_l19_probe
export QWEN3B_GGUF="${QWEN3B_GGUF:-$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf}"
export IBEX_COMMIT="${IBEX_COMMIT:-$(git rev-parse HEAD)}"

trap 'pkill -f simv_soc_ibex_probe 2>/dev/null || true' EXIT

if [ ! -x "$SIMV" ]; then
    echo "[INFO] Compiling simv_soc_ibex_probe (probe tb, -O3) ..."
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

echo "[INFO] Running L0->L19 probe cocotb test (MODULE=sim.rtl_soc_l0_l19_probe)"
(cd "$RUN_DIR" && "$SIMV" \
    +COCOTB \
    +FM_SOC_CASE_ID=L0L19-PROBE \
    +BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex")
