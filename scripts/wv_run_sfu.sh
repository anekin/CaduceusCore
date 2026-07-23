#!/usr/bin/env bash
set -euo pipefail
# wv_run_sfu.sh — compile and run SFU wrapper cocotb tests on sz0001
# ============================================================================
# Task: wrapper-level-verification / T2 (Wave 1)
#
# Steps:
#   1. Compile tb_sfu_wrapper via VCS + cocotb VPI on sz0001
#   2. Run the 5 cocotb tests (test_apb_regmap_rw, test_sfu_softmax_normal,
#      test_sfu_gelu_normal, test_sfu_width_converter_32to512,
#      test_sfu_line_buffer_prefetch)
#   3. Collect output to build/evidence/wrap-sfu-regression.txt
#
# Reuses:
#   - p9_ssh() from scripts/p9_lib/p9_sz0001.sh (SSH + VCS env wrapper)
#   - wv_compile.sh — compilation handled here for self-containment
#   - sim/regression/run_env.sh — sourced remotely for Python + cocotb env
# ============================================================================

source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

EVIDENCE_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$EVIDENCE_DIR"

SIMV="$EVIDENCE_DIR/simv_tb_sfu_wrapper"
COMPILE_LOG="$EVIDENCE_DIR/wv-sfu-compile.log"
RUN_LOG="$EVIDENCE_DIR/wv-sfu-run.log"
REGRESSION_FILE="$EVIDENCE_DIR/wrap-sfu-regression.txt"

# ── Step 1: Build or reuse the SFU wrapper simv on sz0001 ────────────────
# The simv is built by wv_compile.sh (T1 artifact).  If missing or stale,
# fall back to running wv_compile.sh to rebuild it.
_SIMV_EXISTS=$(p9_ssh "test -x $SIMV && echo yes || echo no" 2>&1 | tail -1)
echo "[wv_run_sfu.sh] simv exists on remote: $_SIMV_EXISTS"

if [ "$_SIMV_EXISTS" != "yes" ]; then
    echo "[wv_run_sfu.sh] simv missing; running wv_compile.sh to rebuild..."
    bash "$(dirname "$0")/wv_compile.sh"
fi

# ── Step 2: Run all 5 cocotb tests on sz0001 ──────────────────────────────
# CocoTB VPI discovers tests from MODULE (all @cocotb.test() decorated
# functions).  No TESTCASE= filter means all 5 run in sequence.
#
# set +e: we collect PASS/FAIL regardless of test outcome.
echo "[wv_run_sfu.sh] Step 2: Running SFU wrapper cocotb tests..."

RUN_CMD="
set +e  # collect results regardless of test failures
export PYTHONPATH=\"${REPO_ROOT}/sim\"
export MODULE=\"sim.tests.wrapper.test_sfu_wrapper\"
export TOPLEVEL=\"tb_sfu_wrapper\"
export TOPLEVEL_LANG=\"verilog\"
export COCOTB_TESTCASE=

\"${REPO_ROOT}/build/evidence/simv_tb_sfu_wrapper\" \
    -l \"${REPO_ROOT}/build/evidence/wv-sfu-run.log\"
RUN_EXIT=\$?

echo ''
echo '=== Regression Summary ==='
grep -E 'PASS|FAIL|ERROR|Running test|Test.*passed|Test.*failed' \
    \"${REPO_ROOT}/build/evidence/wv-sfu-run.log\" 2>/dev/null || true
echo ''
echo \"RUN_EXIT_CODE=\$RUN_EXIT\"
"

p9_ssh "$RUN_CMD" > "$REGRESSION_FILE" 2>&1 || true

# ── Step 3: Summarize results locally ─────────────────────────────────────
echo ""
echo "=== SFU Wrapper Regression Summary ==="
echo "Evidence: $REGRESSION_FILE"
echo ""

# Extract PASS/FAIL status
PASS_COUNT=$(grep -cE "test_.*\.py:[0-9]+:.*PASS|test_.*PASS" "$REGRESSION_FILE" 2>/dev/null || echo 0)
FAIL_COUNT=$(grep -cE "FAIL" "$REGRESSION_FILE" 2>/dev/null || echo 0)
ERROR_COUNT=$(grep -cE "ERROR" "$REGRESSION_FILE" 2>/dev/null || echo 0)

echo "PASS  : $PASS_COUNT"
echo "FAIL  : $FAIL_COUNT"
echo "ERROR : $ERROR_COUNT"

if grep -qE "RUN_EXIT_CODE=0" "$REGRESSION_FILE" 2>/dev/null; then
    if [ "$FAIL_COUNT" -eq 0 ] && [ "$ERROR_COUNT" -eq 0 ]; then
        echo "All tests PASSED (exit code 0)."
        exit 0
    fi
fi

# Still exit 0 for the script itself — failures are recorded in the
# regression file for review, not for aborting the flow.
echo "Regression complete (failures captured in $REGRESSION_FILE)."
exit 0
