#!/usr/bin/env bash
set -euo pipefail
# wv_run_sfu.sh — compile and run SFU wrapper cocotb tests on sz0001
# ============================================================================
# Task: wrapper-level-verification / T2 (Wave 1)
#
# Steps:
#   1. FORCED rebuild of tb_sfu_wrapper (rm -rf simv + daidir, then VCS +
#      cocotb VPI on sz0001; abort unless the compile exits 0 AND the binary
#      exists afterwards)
#   2. Run every @cocotb.test() case of sim.tests.wrapper.test_sfu_wrapper
#   3. Verdict via scripts/parse_cocotb_verdict.sh (fail-closed); raw cocotb
#      output is collected in build/evidence/wrap-sfu-regression.txt
#
# Reuses:
#   - p9_ssh() from scripts/p9_lib/p9_sz0001.sh (SSH + VCS env wrapper)
#   - sim/regression/run_env.sh — sourced remotely for Python + cocotb env
#
# Must NOT reuse scripts/wv_compile.sh: it `set +e`s and never deletes the
# stale simv — that fail-open combination is the stale-binary trap this
# script now avoids.
# ============================================================================

source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$EVIDENCE_DIR"

SIMV="$EVIDENCE_DIR/simv_tb_sfu_wrapper"
COMPILE_LOG="$EVIDENCE_DIR/wv-sfu-compile.log"
COMPILE_RUN_LOG="$EVIDENCE_DIR/wv-sfu-compile-run.log"
RUN_LOG="$EVIDENCE_DIR/wv-sfu-run.log"
REGRESSION_FILE="$EVIDENCE_DIR/wrap-sfu-regression.txt"
# Whole-module wall-clock bound on sz0001: a hung test must be classified, not
# waited on (the vector twin was observed spinning forever on a cocotb
# import error until its runner was killed).  Observed whole-module wall time
# is seconds-to-minutes; 900 s is a wide margin for all 7 cases.
RUN_TIMEOUT_S="${WV_SFU_TIMEOUT_S:-900}"

# ── Step 1: forced rebuild of the SFU wrapper simv on sz0001 ─────────────
echo "[wv_run_sfu.sh] Step 1: forced rebuild of $SIMV on sz0001..."

COMPILE_CMD="
set +e
rm -rf '${SIMV}' '${SIMV}.daidir'
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k \
    +define+COCOTB_SIM=1 +vpi -P \"\$PLI_TAB\" -load \"\$COCOTB_VPI_LIB\" \
    -f rtl/tb/wrapper.flist \
    -top tb_sfu_wrapper \
    rtl/tb/tb_sfu_wrapper.v \
    -o '${SIMV}' \
    -l '${COMPILE_LOG}'
RC=\$?
echo \"COMPILE_EXIT_CODE=\$RC\"
if [ \$RC -ne 0 ] || [ ! -x '${SIMV}' ]; then
    echo 'COMPILE_OR_BINARY_FAILED=1'
    exit 1
fi
echo 'COMPILE_OK=1'
"

p9_ssh "$COMPILE_CMD" > "$COMPILE_RUN_LOG" 2>&1 || true
COMPILE_RC=$(grep -oP 'COMPILE_EXIT_CODE=\K\d+' "$COMPILE_RUN_LOG" || echo "1")
if [ "$COMPILE_RC" != "0" ] || ! grep -q '^COMPILE_OK=1$' "$COMPILE_RUN_LOG"; then
    echo "[wv_run_sfu.sh] ERROR: forced rebuild failed (exit $COMPILE_RC)"

    echo "COMPILE: FAIL" > "$REGRESSION_FILE"
    exit 1
fi
echo "[wv_run_sfu.sh] Forced rebuild OK."

# ── Step 2: Run every cocotb test in the module on sz0001 ────────────────
# CocoTB VPI discovers tests from MODULE (all @cocotb.test() decorated
# functions).  No TESTCASE= filter means the whole module runs in sequence.
#
# set +e: we collect PASS/FAIL regardless of test outcome; the verdict is
# decided locally in Step 3, never by the simulator exit code.
echo "[wv_run_sfu.sh] Step 2: Running SFU wrapper cocotb tests..."

RUN_CMD="
set +e  # collect results regardless of test failures
export PYTHONPATH=\"${REPO_ROOT}/sim:${REPO_ROOT}\"
export MODULE=\"sim.tests.wrapper.test_sfu_wrapper\"
export TOPLEVEL=\"tb_sfu_wrapper\"
export TOPLEVEL_LANG=\"verilog\"
export COCOTB_TESTCASE=

timeout -k 10 ${RUN_TIMEOUT_S} \"${SIMV}\" \
    -l \"${RUN_LOG}\"
RUN_EXIT=\$?

echo ''
echo '=== Regression Summary ==='
grep -E 'PASS|FAIL|ERROR|Running test|Test.*passed|Test.*failed' \
    \"${RUN_LOG}\" 2>/dev/null || true
echo ''
echo \"RUN_EXIT_CODE=\$RUN_EXIT\"
"

p9_ssh "$RUN_CMD" > "$REGRESSION_FILE" 2>&1 || true

RUN_RC="missing"
RUN_RC=$(grep -oP '^RUN_EXIT_CODE=\K\d+' "$REGRESSION_FILE" 2>/dev/null) || RUN_RC="missing"
if [ "$RUN_RC" = "124" ] || [ "$RUN_RC" = "137" ]; then
    echo "[wv_run_sfu.sh] TIMEOUT: simv hit the ${RUN_TIMEOUT_S}s wall-clock bound (rc=$RUN_RC) — see $REGRESSION_FILE" >&2
    exit 1
fi

# ── Step 3: fail-closed verdict (scripts/parse_cocotb_verdict.sh) ────────
if VERDICT_LINE=$(bash "$SCRIPT_DIR/parse_cocotb_verdict.sh" --log "$REGRESSION_FILE"); then
    echo "[wv_run_sfu.sh] $VERDICT_LINE"
    echo "[wv_run_sfu.sh] SFU wrapper: PASS"
    exit 0
fi

echo "[wv_run_sfu.sh] $VERDICT_LINE" >&2
echo "[wv_run_sfu.sh] SFU wrapper: FAIL — raw cocotb output in $REGRESSION_FILE" >&2
exit 1
