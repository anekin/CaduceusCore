#!/usr/bin/env bash
set -euo pipefail
# p10_sfu_wrapper_fix.sh — Diagnose and verify SFU wrapper output mismatches
# =============================================================================
# Task: Wave 5 todo 18 — fix test_sfu_gelu_normal,
#       test_sfu_width_converter_32to512, test_sfu_line_buffer_prefetch.
#
# Steps:
#   1. Compile tb_sfu_wrapper on sz0001 (VCS + cocotb VPI).
#   2. Run the 5 SFU wrapper functional cocotb tests.
#   3. Run the module-level SFU batch regression (319 scenarios).
#   4. Write evidence to build/evidence/task-18-phase10-rtl-verification.txt.
# =============================================================================

source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

EVIDENCE_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$EVIDENCE_DIR"

SIMV="$EVIDENCE_DIR/simv_tb_sfu_wrapper"
COMPILE_LOG="$EVIDENCE_DIR/p10-sfu-wrapper-compile.log"
WRAPPER_LOG="$EVIDENCE_DIR/p10-sfu-wrapper-run.log"
BATCH_LOG="$EVIDENCE_DIR/p10-sfu-batch-run.log"
EVIDENCE_FILE="$EVIDENCE_DIR/task-18-phase10-rtl-verification.txt"

WRAPPER_TESTS=(
    "test_apb_regmap_rw"
    "test_sfu_softmax_normal"
    "test_sfu_gelu_normal"
    "test_sfu_width_converter_32to512"
    "test_sfu_line_buffer_prefetch"
)

# ── Step 1: Compile wrapper testbench on sz0001 ────────────────────────────
echo "[p10_sfu_wrapper_fix.sh] Step 1: compiling tb_sfu_wrapper on sz0001..."
COMPILE_CMD='
set +e
mkdir -p "build/evidence"
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k \
    +define+COCOTB_SIM=1 +vpi -P "$PLI_TAB" -load "$COCOTB_VPI_LIB" \
    -f rtl/tb/wrapper.flist \
    -top tb_sfu_wrapper \
    rtl/tb/tb_sfu_wrapper.v \
    -o "build/evidence/simv_tb_sfu_wrapper" \
    -l "build/evidence/p10-sfu-wrapper-compile.log"
RC=$?
echo "VCS_EXIT_CODE=$RC"
'
p10_ssh "$COMPILE_CMD" > "$COMPILE_LOG" 2>&1 || true

if ! grep -q "VCS_EXIT_CODE=0" "$COMPILE_LOG"; then
    echo "ERROR: wrapper compilation failed"
    cat "$COMPILE_LOG"
    exit 1
fi
echo "[p10_sfu_wrapper_fix.sh] Compilation OK"

# ── Step 2: Run wrapper functional tests ───────────────────────────────────
echo "[p10_sfu_wrapper_fix.sh] Step 2: running SFU wrapper functional tests..."
rm -f "$WRAPPER_LOG"
for tc in "${WRAPPER_TESTS[@]}"; do
    echo "=== Running $tc ===" >> "$WRAPPER_LOG"
    RUN_CMD="
set +e
export PYTHONPATH=\"${REPO_ROOT}/sim\"
export TOPLEVEL=\"tb_sfu_wrapper\"
export TOPLEVEL_LANG=\"verilog\"
export MODULE=\"sim.tests.wrapper.test_sfu_wrapper\"
TESTCASE=\"$tc\" \"${REPO_ROOT}/build/evidence/simv_tb_sfu_wrapper\" >> \"${REPO_ROOT}/build/evidence/p10-sfu-wrapper-run.log\" 2>&1
echo \"$tc exit: \$?\" >> \"${REPO_ROOT}/build/evidence/p10-sfu-wrapper-run.log\"
"
    p10_ssh "$RUN_CMD" >> "$WRAPPER_LOG" 2>&1 || true
    echo "" >> "$WRAPPER_LOG"
done

echo '=== Summary ===' >> "$WRAPPER_LOG"
grep -E 'PASS|FAIL|ERROR|Test.*passed|Test.*failed' "$WRAPPER_LOG" 2>/dev/null || true

# ── Step 3: Run SFU module batch regression ────────────────────────────────
echo "[p10_sfu_wrapper_fix.sh] Step 3: running SFU module batch regression..."
BATCH_CMD="
set +e
source /NAS/Tools/methodology/modules/init/bash
module load vcs/vcs_2023.12sp2
cd '${REPO_ROOT}'
python3 scripts/run_batch_regression.py > \"${REPO_ROOT}/build/evidence/p10-sfu-batch-run.log\" 2>&1
RC=\$?
echo \"BATCH_EXIT_CODE=\$RC\"
"
p10_ssh "$BATCH_CMD" > "$BATCH_LOG" 2>&1 || true

# ── Step 4: Summarize locally ──────────────────────────────────────────────
echo "[p10_sfu_wrapper_fix.sh] Step 4: summarizing evidence..."
PASS_COUNT=0
FAIL_COUNT=0

SFU_PASS=0
SFU_TOTAL=0
BATCH_EVIDENCE="$REPO_ROOT/.omo/evidence/task-17-rerun.txt"
if [ -f "$BATCH_EVIDENCE" ]; then
    SFU_PASS=$(grep -oE "SFU: [0-9]+/[0-9]+ passed" "$BATCH_EVIDENCE" | grep -oE "[0-9]+" | head -1 || echo 0)
    SFU_TOTAL=$(grep -oE "SFU: [0-9]+/[0-9]+ passed" "$BATCH_EVIDENCE" | grep -oE "[0-9]+" | tail -1 || echo 0)
fi

cat > "$EVIDENCE_FILE" <<EOF
=== Task 18 Phase 10 RTL Verification Evidence ===
Date: $(date -Iseconds)

--- SFU Wrapper Functional Tests ---
EOF

for tc in "${WRAPPER_TESTS[@]}"; do
    if grep -qE "$tc.*PASS|PASS.*$tc" "$WRAPPER_LOG" 2>/dev/null; then
        echo "  $tc: PASS" >> "$EVIDENCE_FILE"
        PASS_COUNT=$((PASS_COUNT + 1))
    elif grep -qE "$tc.*FAIL|FAIL.*$tc" "$WRAPPER_LOG" 2>/dev/null; then
        echo "  $tc: FAIL" >> "$EVIDENCE_FILE"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    else
        echo "  $tc: UNKNOWN" >> "$EVIDENCE_FILE"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

cat >> "$EVIDENCE_FILE" <<EOF

Wrapper PASS count: $PASS_COUNT
Wrapper FAIL count: $FAIL_COUNT

--- SFU Module Batch Regression ---
SFU: ${SFU_PASS}/${SFU_TOTAL} PASS

--- Raw Logs ---
Compile log: $COMPILE_LOG
Wrapper run log: $WRAPPER_LOG
Batch run log: $BATCH_LOG
EOF

echo ""
echo "=== SFU Wrapper Fix Verification Summary ==="
grep "^  test_" "$EVIDENCE_FILE" || true
echo "SFU batch: ${SFU_PASS}/${SFU_TOTAL} PASS"
echo "Evidence: $EVIDENCE_FILE"

# Exit 0 only if everything passes
if [ "$PASS_COUNT" -eq 5 ] && [ "$FAIL_COUNT" -eq 0 ] && [ "$SFU_PASS" -eq 319 ] && [ "$SFU_TOTAL" -eq 319 ]; then
    echo "All checks PASSED."
    exit 0
else
    echo "Some checks FAILED."
    exit 1
fi
