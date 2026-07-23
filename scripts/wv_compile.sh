#!/usr/bin/env bash
set -euo pipefail
# wv_compile.sh — compile all 3 wrapper testbenches + axi_sparse_slave.v
# on sz0001 using VCS with cocotb VPI flags.
#
# Writes compilation output and VCS_EXIT_CODE to build/evidence/wv-compile.log.

source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

BUILD_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$BUILD_DIR"

# Remote compilation command (runs on sz0001 via p9_ssh).
# All paths are relative to REPO_ROOT because p9_ssh does 'cd $REPO_ROOT'
# before running this command.  PLI_TAB and COCOTB_VPI_LIB are set by
# run_env.sh on the remote side.
COMPILE_CMD='
set +e
BUILD_DIR=build/evidence
mkdir -p "$BUILD_DIR"
RC=0

for TB in tb_sfu_wrapper tb_vector_wrapper tb_mxu_wrapper; do
    echo "=== Compiling $TB ==="
    vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k \
        +define+COCOTB_SIM=1 +vpi -P "$PLI_TAB" -load "$COCOTB_VPI_LIB" \
        -f rtl/tb/wrapper.flist \
        -top "$TB" \
        "rtl/tb/${TB}.v" \
        -o "$BUILD_DIR/simv_${TB}" \
        -l "$BUILD_DIR/wv-compile-${TB}.log"
    TB_RC=$?
    echo "$TB exit: $TB_RC"
    if [ "$TB_RC" -ne 0 ]; then RC="$TB_RC"; fi
done

echo "VCS_EXIT_CODE=$RC"
'

echo "[wv_compile.sh] Starting remote compilation on sz0001..."
p9_ssh "$COMPILE_CMD" > "$BUILD_DIR/wv-compile.log"
echo "[wv_compile.sh] Compilation finished. Log: $BUILD_DIR/wv-compile.log"
