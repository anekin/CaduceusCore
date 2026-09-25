#!/usr/bin/env bash
set -euo pipefail
# wv_run_vector.sh — Run Vector wrapper cocotb tests on sz0001
# ============================================================================
# Steps:
#   1. FORCED rebuild of tb_vector_wrapper (rm -rf simv + daidir, then VCS +
#      cocotb VPI on sz0001; abort unless the compile exits 0 AND the binary
#      exists afterwards)
#   2. Run every case in TESTS in a separate simv invocation (clean per-test
#      PASS/FAIL isolation, one log per case)
#   3. Verdict per case via scripts/parse_cocotb_verdict.sh (fail-closed),
#      aggregated into build/evidence/wrap-vec-regression.txt
#
# The verdict logic must stay OUT of the remote heredoc: it runs locally on the
# per-test logs so that a missing/ambiguous summary fails the script instead of
# being reported as PASS.
# ============================================================================

source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="$REPO_ROOT/build/evidence"
LOG_DIR="$BUILD_DIR/wv_vector_logs"
EVIDENCE_FILE="$BUILD_DIR/wrap-vec-regression.txt"
STATUS_FILE="$BUILD_DIR/wrap-vec-status.txt"
SIMV="$BUILD_DIR/simv_tb_vector_wrapper"
COMPILE_LOG="$BUILD_DIR/wv-compile-vector-rerun.log"
COMPILE_RUN_LOG="$BUILD_DIR/wv-vec-compile-run.log"
RUN_LOG="$BUILD_DIR/wv-vec-run.log"
mkdir -p "$BUILD_DIR" "$LOG_DIR"

# Every @cocotb.test() case in sim/tests/wrapper/test_vector_wrapper.py.
TESTS=(
    test_apb_native_rw
    test_apb_wrapper_rw
    test_vector_add_normal
    test_vector_chunk_burst_8beat
    test_vector_conv_type_convert
    test_bug005_vector_nonaligned_wstrb
)
TEST_LIST="$(printf '%s ' "${TESTS[@]}")"
# Per-test wall-clock bound on sz0001: a test that hangs (e.g. cocotb's own
# failed-import path spins the GPI event loop forever) must be classified, not
# waited on.  Observed per-test wall time is ~10-30 s; 240 s is 8-20x headroom.
TEST_TIMEOUT_S="${WV_TEST_TIMEOUT_S:-240}"

# ── Step 1: forced rebuild of the Vector wrapper simv on sz0001 ──────────
echo "[wv_run_vector.sh] Step 1: forced rebuild of $SIMV on sz0001..."

COMPILE_CMD="
set +e
rm -rf '${SIMV}' '${SIMV}.daidir'
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k \
    +define+COCOTB_SIM=1 +vpi -P \"\$PLI_TAB\" -load \"\$COCOTB_VPI_LIB\" \
    -f rtl/tb/wrapper.flist \
    -top tb_vector_wrapper \
    rtl/tb/tb_vector_wrapper.v \
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
    echo "[wv_run_vector.sh] ERROR: forced rebuild failed (exit $COMPILE_RC)"

    echo "COMPILE: FAIL" > "$EVIDENCE_FILE"
    echo "wrap-vec-regression: FAIL (compile)" > "$STATUS_FILE"
    exit 1
fi
echo "[wv_run_vector.sh] Forced rebuild OK."

# ── Step 2: run each test case in its own simv invocation on sz0001 ──────
echo "[wv_run_vector.sh] Step 2: running ${#TESTS[@]} Vector wrapper tests on sz0001..."

RUN_CMD="
set +e
cd '${REPO_ROOT}'
BUILD_DIR='build/evidence'
LOG_DIR=\"\$BUILD_DIR/wv_vector_logs\"
mkdir -p \"\$LOG_DIR\"

export COCOTB_ANSI_OUTPUT=1
export TOPLEVEL='tb_vector_wrapper'
export MODULE='sim.tests.wrapper.test_vector_wrapper'
export PYTHONPATH=\"\$PWD/sim:\$PWD:\$PYTHONPATH\"

SIMV='${SIMV}'
for TEST in ${TEST_LIST}; do
    echo \"[wv_run_vector] === Running \$TEST ===\"
    export TESTCASE=\"\$TEST\"
    timeout -k 10 ${TEST_TIMEOUT_S} \"\$SIMV\" -l \"\$LOG_DIR/\${TEST}.log.dbg\" > \"\$LOG_DIR/\${TEST}.log\" 2>&1
    echo \"RUN_EXIT_CODE_\${TEST}=\$?\"
done
"

p9_ssh "$RUN_CMD" > "$RUN_LOG" 2>&1 || true

# ── Step 3: fail-closed verdict per case, then aggregate ─────────────────
{
    echo "=== Vector Wrapper Regression $(date '+%Y-%m-%d %H:%M:%S') ==="
    echo "Verdict source: scripts/parse_cocotb_verdict.sh (fail-closed)"
    echo ""
} > "$EVIDENCE_FILE"

PASS_N=0
FAIL_N=0
for TEST in "${TESTS[@]}"; do
    TEST_LOG="$LOG_DIR/${TEST}.log"
    RUN_RC="missing"
    RUN_RC=$(grep -oP "^RUN_EXIT_CODE_${TEST}=\K\d+" "$RUN_LOG" 2>/dev/null) || RUN_RC="missing"

    if [ "$RUN_RC" = "124" ] || [ "$RUN_RC" = "137" ]; then
        echo "  $TEST: TIMEOUT (simv rc=$RUN_RC; per-test wall-clock bound ${TEST_TIMEOUT_S}s)" >> "$EVIDENCE_FILE"
        echo "[wv_run_vector.sh]   $TEST: TIMEOUT (rc=$RUN_RC, bound ${TEST_TIMEOUT_S}s) — see $TEST_LOG" >&2
        FAIL_N=$((FAIL_N + 1))
    elif VERDICT_LINE=$(bash "$SCRIPT_DIR/parse_cocotb_verdict.sh" --log "$TEST_LOG"); then
        echo "  $TEST: PASS [$VERDICT_LINE]" >> "$EVIDENCE_FILE"
        echo "[wv_run_vector.sh]   $TEST: PASS [$(echo "$VERDICT_LINE" | grep -oE 'TESTS=[0-9]+ PASS=[0-9]+ FAIL=[0-9]+ SKIP=[0-9]+')]"
        PASS_N=$((PASS_N + 1))
    else
        echo "  $TEST: FAIL [$VERDICT_LINE]" >> "$EVIDENCE_FILE"
        echo "[wv_run_vector.sh]   $TEST: FAIL — see $TEST_LOG" >&2
        FAIL_N=$((FAIL_N + 1))
    fi
done
TOTAL_N=${#TESTS[@]}

echo "" >> "$EVIDENCE_FILE"
if [ "$FAIL_N" -eq 0 ]; then
    echo "=== Overall: ALL $TOTAL_N PASS ===" >> "$EVIDENCE_FILE"
    echo "wrap-vec-regression: PASS ($PASS_N/$TOTAL_N)" > "$STATUS_FILE"
    echo "[wv_run_vector.sh] Vector wrapper: PASS ($PASS_N/$TOTAL_N)"
    exit 0
fi

echo "=== Overall: FAIL ($PASS_N/$TOTAL_N PASS, $FAIL_N FAIL) ===" >> "$EVIDENCE_FILE"
echo "wrap-vec-regression: FAIL ($PASS_N/$TOTAL_N)" > "$STATUS_FILE"
echo "[wv_run_vector.sh] Vector wrapper: FAIL ($PASS_N/$TOTAL_N PASS, $FAIL_N FAIL)" >&2
exit 1
