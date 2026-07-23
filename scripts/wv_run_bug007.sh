#!/usr/bin/env bash
set -euo pipefail
# wv_run_bug007.sh -- compile + run BUG-007 directed tests on sz0001
# ==============================================================================
# Task: wrapper-level-verification / T6 (Wave 2)
#
# Compiles tb_mxu_wrapper and tb_sfu_wrapper with cocotb VPI (reusing
# existing simv binaries if present), runs BUG-007 test cases, and writes
# results to build/evidence/wrap-bug007-result.txt.
#
# Exits 0 regardless of test outcome (evidence capture mode).
# ==============================================================================

source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

EVIDENCE_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$EVIDENCE_DIR"

RESULT_FILE="$EVIDENCE_DIR/wrap-bug007-result.txt"

# Clear result file
echo "" > "$RESULT_FILE"
echo "=== BUG-007 Consecutive Dispatch Results ===" >> "$RESULT_FILE"
echo "Date: $(date '+%Y-%m-%d %H:%M:%S')" >> "$RESULT_FILE"
echo "" >> "$RESULT_FILE"

# ══════════════════════════════════════════════════════════════════════════════
# Step 1: Compile MXU wrapper simv (reuse if present)
# ══════════════════════════════════════════════════════════════════════════════

SIMV_MXU="$EVIDENCE_DIR/simv_tb_mxu_wrapper"
SIMV_SFU="$EVIDENCE_DIR/simv_tb_sfu_wrapper"

_compile_mxu() {
    local compile_log="$EVIDENCE_DIR/wv-bug007-compile-mxu.log"
    echo "[wv_run_bug007.sh] Compiling tb_mxu_wrapper on sz0001..."

    local compile_cmd="
set +e
echo '=== Compiling tb_mxu_wrapper ==='
rm -rf '${SIMV_MXU}.daidir' '${SIMV_MXU}'
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k \
    +define+COCOTB_SIM=1 +vpi -P \"\$PLI_TAB\" -load \"\$COCOTB_VPI_LIB\" \
    -f rtl/tb/wrapper.flist \
    -top tb_mxu_wrapper \
    rtl/tb/tb_mxu_wrapper.v \
    -o '${SIMV_MXU}' \
    -l '${compile_log}'
RC=\$?
echo \"COMPILE_EXIT_CODE=\$RC\"
"
    p9_ssh "$compile_cmd" > "$compile_log" 2>&1
    grep -oP 'COMPILE_EXIT_CODE=\K\d+' "$compile_log" || echo "1"
}

_compile_sfu() {
    local compile_log="$EVIDENCE_DIR/wv-bug007-compile-sfu.log"
    echo "[wv_run_bug007.sh] Compiling tb_sfu_wrapper on sz0001..."

    local compile_cmd="
set +e
echo '=== Compiling tb_sfu_wrapper ==='
rm -rf '${SIMV_SFU}.daidir' '${SIMV_SFU}'
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k \
    +define+COCOTB_SIM=1 +vpi -P \"\$PLI_TAB\" -load \"\$COCOTB_VPI_LIB\" \
    -f rtl/tb/wrapper.flist \
    -top tb_sfu_wrapper \
    rtl/tb/tb_sfu_wrapper.v \
    -o '${SIMV_SFU}' \
    -l '${compile_log}'
RC=\$?
echo \"COMPILE_EXIT_CODE=\$RC\"
"
    p9_ssh "$compile_cmd" > "$compile_log" 2>&1
    grep -oP 'COMPILE_EXIT_CODE=\K\d+' "$compile_log" || echo "1"
}

# Check if simv exists on remote
_mxu_exists=$(p9_ssh "test -x $SIMV_MXU && echo yes || echo no" 2>&1 | tail -1)
_sfu_exists=$(p9_ssh "test -x $SIMV_SFU && echo yes || echo no" 2>&1 | tail -1)

if [ "$_mxu_exists" != "yes" ]; then
    MXU_COMPILE_RC=$(_compile_mxu)
    if [ "$MXU_COMPILE_RC" != "0" ]; then
        echo "[wv_run_bug007.sh] ERROR: MXU compilation failed (exit $MXU_COMPILE_RC)"
        echo "MXU: COMPILE FAIL" >> "$RESULT_FILE"
        echo "SFU: SKIP (MXU compile failed)" >> "$RESULT_FILE"
        cat "$RESULT_FILE"
        exit 0
    fi
    echo "[wv_run_bug007.sh] MXU compilation passed."
else
    echo "[wv_run_bug007.sh] Reusing existing MXU simv."
fi

if [ "$_sfu_exists" != "yes" ]; then
    SFU_COMPILE_RC=$(_compile_sfu)
    if [ "$SFU_COMPILE_RC" != "0" ]; then
        echo "[wv_run_bug007.sh] ERROR: SFU compilation failed (exit $SFU_COMPILE_RC)"
        echo "SFU: COMPILE FAIL" >> "$RESULT_FILE"
        cat "$RESULT_FILE"
        exit 0
    fi
    echo "[wv_run_bug007.sh] SFU compilation passed."
else
    echo "[wv_run_bug007.sh] Reusing existing SFU simv."
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Run MXU BUG-007 test
# ══════════════════════════════════════════════════════════════════════════════

echo "[wv_run_bug007.sh] Running MXU BUG-007 test: test_bug007_consecutive_dispatch ..."

MXU_TEST_LOG="$EVIDENCE_DIR/wv-bug007-mxu.log"

_run_mxu_test() {
    local run_cmd="
set +e
cd '$REPO_ROOT'
export MODULE='sim.tests.wrapper.test_mxu_wrapper'
export TOPLEVEL='tb_mxu_wrapper'
export TOPLEVEL_LANG='verilog'
export COCOTB_ANSI_OUTPUT=1
export TESTCASE='test_bug007_consecutive_dispatch'
'${SIMV_MXU}' -l '${MXU_TEST_LOG}.dbg' > '${MXU_TEST_LOG}' 2>&1
echo \"TEST_EXIT_CODE=\$?\"
"
    p9_ssh "$run_cmd" > /dev/null 2>&1
}

_run_mxu_test

# Parse MXU result from evidence log (cocotb output captured via > redirection)
# Priority: explicit MXU: PASS/FAIL marker, then cocotb summary line, then UNKNOWN
if grep -qE 'MXU: PASS' "$MXU_TEST_LOG" 2>/dev/null; then
    MXU_RESULT="PASS"
elif grep -qE 'MXU: FAIL' "$MXU_TEST_LOG" 2>/dev/null; then
    MXU_RESULT="FAIL"
elif grep -qE '\bFAIL=[1-9]' "$MXU_TEST_LOG" 2>/dev/null; then
    MXU_RESULT="FAIL"
elif grep -qE '\bPASS=[1-9]' "$MXU_TEST_LOG" 2>/dev/null; then
    MXU_RESULT="PASS"
else
    MXU_RESULT="UNKNOWN"
fi

echo "MXU: $MXU_RESULT" >> "$RESULT_FILE"
echo "[wv_run_bug007.sh]   MXU BUG-007: $MXU_RESULT"

# ══════════════════════════════════════════════════════════════════════════════
# Step 3: Run SFU BUG-007 test
# ══════════════════════════════════════════════════════════════════════════════

echo "[wv_run_bug007.sh] Running SFU BUG-007 test: test_bug007_sfu_start_hold ..."

SFU_TEST_LOG="$EVIDENCE_DIR/wv-bug007-sfu.log"

_run_sfu_test() {
    local run_cmd="
set +e
cd '$REPO_ROOT'
export MODULE='sim.tests.wrapper.test_sfu_wrapper'
export TOPLEVEL='tb_sfu_wrapper'
export TOPLEVEL_LANG='verilog'
export COCOTB_ANSI_OUTPUT=1
export TESTCASE='test_bug007_sfu_start_hold'
'${SIMV_SFU}' -l '${SFU_TEST_LOG}.dbg' > '${SFU_TEST_LOG}' 2>&1
echo \"TEST_EXIT_CODE=\$?\"
"
    p9_ssh "$run_cmd" > /dev/null 2>&1
}

_run_sfu_test

# Parse SFU result from evidence log (cocotb output captured via > redirection)
# Priority: explicit SFU: PASS/MIXED/FAIL marker, then cocotb summary line, then UNKNOWN
if grep -qE 'SFU: PASS' "$SFU_TEST_LOG" 2>/dev/null; then
    SFU_RESULT="PASS"
elif grep -qE 'SFU: MIXED' "$SFU_TEST_LOG" 2>/dev/null; then
    SFU_RESULT="MIXED"
elif grep -qE 'SFU: FAIL' "$SFU_TEST_LOG" 2>/dev/null; then
    SFU_RESULT="FAIL"
elif grep -qE '\bFAIL=[1-9]' "$SFU_TEST_LOG" 2>/dev/null; then
    SFU_RESULT="FAIL"
elif grep -qE '\bPASS=[1-9]' "$SFU_TEST_LOG" 2>/dev/null; then
    SFU_RESULT="PASS"
else
    SFU_RESULT="UNKNOWN"
fi

echo "SFU: $SFU_RESULT" >> "$RESULT_FILE"
echo "[wv_run_bug007.sh]   SFU BUG-007: $SFU_RESULT"

# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Finalize evidence file
# ══════════════════════════════════════════════════════════════════════════════

echo "" >> "$RESULT_FILE"
echo "Evidence:" >> "$RESULT_FILE"
echo "  MXU log: $MXU_TEST_LOG" >> "$RESULT_FILE"
echo "  SFU log: $SFU_TEST_LOG" >> "$RESULT_FILE"
echo "" >> "$RESULT_FILE"
echo "=== End of BUG-007 Results ===" >> "$RESULT_FILE"

echo ""
echo "=== BUG-007 Results ==="
cat "$RESULT_FILE"
echo ""
echo "[wv_run_bug007.sh] Done. Evidence: $RESULT_FILE"
exit 0
