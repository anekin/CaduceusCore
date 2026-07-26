#!/usr/bin/env bash
set -euo pipefail
# wv_run_mxu.sh — compile + run MXU wrapper cocotb tests on sz0001
# ==============================================================================
# Task: wrapper-level-verification / T4 (Wave 1)
#
# Compiles tb_mxu_wrapper with cocotb VPI, runs all 5 test cases, and writes
# results to build/evidence/wrap-mxu-regression.txt.
# ==============================================================================

source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

BUILD_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$BUILD_DIR"

SIMV="$BUILD_DIR/simv_tb_mxu_wrapper"
REGRESSION_FILE="$BUILD_DIR/wrap-mxu-regression.txt"

# ── List of test cases ──────────────────────────────────────────────────────
TESTS=(
  "test_apb_regmap_rw"
  "test_mxu_preload_single_tile"
  "test_mxu_single_tile_compute"
  "test_mxu_store_out_burst"
  "test_mxu_accumulate_mode"
)

# ══════════════════════════════════════════════════════════════════════════════
# Step 1: Compile tb_mxu_wrapper on sz0001
# ══════════════════════════════════════════════════════════════════════════════

echo "[wv_run_mxu.sh] Compiling tb_mxu_wrapper on sz0001..."

COMPILE_CMD="
set +e
echo '=== Compiling tb_mxu_wrapper ==='
# Clean stale daidir on remote side to avoid NFS/incremental compilation conflicts
rm -rf \"${SIMV}.daidir\" \"${SIMV}\"
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k \
    +define+COCOTB_SIM=1 +vpi -P \"\$PLI_TAB\" -load \"\$COCOTB_VPI_LIB\" \
    -f rtl/tb/wrapper.flist \
    -top tb_mxu_wrapper \
    rtl/tb/tb_mxu_wrapper.v \
    -o \"${SIMV}\" \
    -l \"${BUILD_DIR}/wv-compile-tb_mxu_wrapper.log\"
RC=\$?
echo \"COMPILE_EXIT_CODE=\$RC\"
"

COMPILE_LOG="$BUILD_DIR/wv-compile-mxu-run.log"
p9_ssh "$COMPILE_CMD" > "$COMPILE_LOG" 2>&1
COMPILE_RC=$(grep -oP 'COMPILE_EXIT_CODE=\K\d+' "$COMPILE_LOG" || echo "1")

if [ "$COMPILE_RC" != "0" ]; then
    echo "[wv_run_mxu.sh] ERROR: Compilation failed (exit $COMPILE_RC)"
    echo "See $COMPILE_LOG for details."
    echo "COMPILE: FAIL" > "$REGRESSION_FILE"
    exit 1
fi
echo "[wv_run_mxu.sh] Compilation passed."

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Run each test case on sz0001
# ══════════════════════════════════════════════════════════════════════════════

PASS_COUNT=0
FAIL_COUNT=0
echo "" > "$REGRESSION_FILE"
echo "=== MXU Wrapper Regression $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$REGRESSION_FILE"
echo "" >> "$REGRESSION_FILE"

for TC in "${TESTS[@]}"; do
    echo "[wv_run_mxu.sh] Running test: $TC ..."

    TEST_LOG="$BUILD_DIR/wv-mxu-${TC}.log"

    # Run a single test case via cocotb+VCS
    RUN_CMD="
set +e
cd '$REPO_ROOT'
export MODULE='sim.tests.wrapper.test_mxu_wrapper'
export TOPLEVEL='tb_mxu_wrapper'
export TOPLEVEL_LANG='verilog'
export COCOTB_ANSI_OUTPUT=1
export TESTCASE='${TC}'
'${SIMV}' -l '${TEST_LOG}' 2>&1
RC=\$?
echo \"TEST_EXIT_CODE=\$RC\"
"

    p9_ssh "$RUN_CMD" > "$TEST_LOG" 2>&1

    # Parse result — grep for PASS/FAIL in the log
    # Cocotb reports "TEST ... PASS" or "TEST ... FAIL" at the end
    if grep -qE 'TEST.*PASS' "$TEST_LOG" 2>/dev/null; then
        RESULT="PASS"
        PASS_COUNT=$((PASS_COUNT + 1))
    elif grep -qE 'TEST.*FAIL' "$TEST_LOG" 2>/dev/null; then
        RESULT="FAIL"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    else
        RESULT="UNKNOWN"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi

    echo "  $TC: $RESULT" >> "$REGRESSION_FILE"
    echo "[wv_run_mxu.sh]   $TC: $RESULT"
done

# ── Summary ─────────────────────────────────────────────────────────────────
echo "" >> "$REGRESSION_FILE"
echo "Summary: $PASS_COUNT PASS, $FAIL_COUNT FAIL (${#TESTS[@]} total)" >> "$REGRESSION_FILE"

if [ "$FAIL_COUNT" -eq 0 ]; then
    echo "[wv_run_mxu.sh] All ${#TESTS[@]} tests PASSED."
    exit 0
else
    echo "[wv_run_mxu.sh] $FAIL_COUNT test(s) FAILED. See $REGRESSION_FILE and $BUILD_DIR/wv-mxu-*.log"
    exit 1
fi
