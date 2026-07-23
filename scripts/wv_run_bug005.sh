#!/usr/bin/env bash
set -euo pipefail
# wv_run_bug005.sh — compile and run BUG-005 X-propagation directed tests
# ============================================================================
# Task: wrapper-level-verification / T5 (Wave 2)
#
# Steps:
#   1. Compile tb_sfu_wrapper_sparse and tb_vector_wrapper_sparse on sz0001
#   2. Run test_bug005_sfu_nonaligned_xprop (SFU sparse TB)
#   3. Run test_bug005_vector_nonaligned_wstrb (Vector sparse TB)
#   4. Write evidence to build/evidence/wrap-bug005-result.txt
#   5. Always exits 0 (evidence capture mode)
# ============================================================================

source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

EVIDENCE_DIR="$REPO_ROOT/build/evidence"
mkdir -p "$EVIDENCE_DIR"

# ── Remote runner on sz0001 ─────────────────────────────────────────────────
# Escaping convention inside double-quoted RUN_CMD:
#   \${var}  → remote shell expands $var
#   \"       → literal double-quote on remote
#   ${LOCAL} → local shell expands
# ---------------------------------------------------------------------------
RUN_CMD="
set +e
BUILD_DIR=\"${EVIDENCE_DIR}\"
LOG_DIR=\"\${BUILD_DIR}/wv_bug005_logs\"
EVIDENCE_FILE=\"\${BUILD_DIR}/wrap-bug005-result.txt\"
mkdir -p \"\${BUILD_DIR}\" \"\${LOG_DIR}\"

SFU_SIMV=\"\${BUILD_DIR}/simv_tb_sfu_wrapper_sparse\"
VECTOR_SIMV=\"\${BUILD_DIR}/simv_tb_vector_wrapper_sparse\"

# === Compile sparse SFU testbench ===
echo '=== [BUG-005] Compiling tb_sfu_wrapper_sparse ==='
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k \
    +define+COCOTB_SIM=1 +vpi -P \"\$PLI_TAB\" -load \"\$COCOTB_VPI_LIB\" \
    -f rtl/tb/wrapper.flist \
    -top tb_sfu_wrapper_sparse \
    rtl/tb/tb_sfu_wrapper_sparse.v \
    -o \"\$SFU_SIMV\" \
    -l \"\${LOG_DIR}/wv-bug005-compile-sfu.log\"
SFU_COMPILE_RC=\$?
echo \"SFU compile exit: \$SFU_COMPILE_RC\"

# === Compile sparse Vector testbench ===
echo ''
echo '=== [BUG-005] Compiling tb_vector_wrapper_sparse ==='
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k \
    +define+COCOTB_SIM=1 +vpi -P \"\$PLI_TAB\" -load \"\$COCOTB_VPI_LIB\" \
    -f rtl/tb/wrapper.flist \
    -top tb_vector_wrapper_sparse \
    rtl/tb/tb_vector_wrapper_sparse.v \
    -o \"\$VECTOR_SIMV\" \
    -l \"\${LOG_DIR}/wv-bug005-compile-vector.log\"
VECTOR_COMPILE_RC=\$?
echo \"Vector compile exit: \$VECTOR_COMPILE_RC\"

# === Start evidence file ===
echo '=== BUG-005 X-propagation Results ===' > \"\$EVIDENCE_FILE\"
echo \"Date: \$(date)\" >> \"\$EVIDENCE_FILE\"
echo '' >> \"\$EVIDENCE_FILE\"

# === Run SFU BUG-005 test ===
if [ \"\$SFU_COMPILE_RC\" -ne 0 ]; then
    echo 'SFU: COMPILE-FAILED' >> \"\$EVIDENCE_FILE\"
    echo 'SFU: COMPILE-FAILED'
else
    echo '[BUG-005] Running SFU X-propagation test...'
    export COCOTB_ANSI_OUTPUT=1
    export TOPLEVEL='tb_sfu_wrapper_sparse'
    export MODULE='sim.tests.wrapper.test_sfu_wrapper'
    export TESTCASE='test_bug005_sfu_nonaligned_xprop'
    export PYTHONPATH=\"\$PWD:\$PYTHONPATH\"

    SFU_LOG=\"\${LOG_DIR}/bug005-sfu.log\"
    \"\$SFU_SIMV\" -l \"\${SFU_LOG}.dbg\" > \"\$SFU_LOG\" 2>&1
    SFU_RC=\$?

    SFU_STATUS=\$(grep -oP 'BUG005_SFU_FINAL: \K.*' \"\$SFU_LOG\" 2>/dev/null || echo 'UNKNOWN')
    if [ -z \"\$SFU_STATUS\" ]; then
        SFU_STATUS=\"EXIT_\$SFU_RC\"
    fi
    echo \"SFU: \$SFU_STATUS\" | tee -a \"\$EVIDENCE_FILE\"
    grep -E '(SFU BUG-005|SFU: |BUG005_SFU_FINAL)' \"\$SFU_LOG\" 2>/dev/null || true
fi

echo '' >> \"\$EVIDENCE_FILE\"

# === Run Vector BUG-005 test ===
if [ \"\$VECTOR_COMPILE_RC\" -ne 0 ]; then
    echo 'Vector: COMPILE-FAILED' >> \"\$EVIDENCE_FILE\"
    echo 'Vector: COMPILE-FAILED'
else
    echo '[BUG-005] Running Vector wstrb masking test...'
    export TOPLEVEL='tb_vector_wrapper_sparse'
    export MODULE='sim.tests.wrapper.test_vector_wrapper'
    export TESTCASE='test_bug005_vector_nonaligned_wstrb'
    export PYTHONPATH=\"\$PWD:\$PYTHONPATH\"

    VECTOR_LOG=\"\${LOG_DIR}/bug005-vector.log\"
    \"\$VECTOR_SIMV\" -l \"\${VECTOR_LOG}.dbg\" > \"\$VECTOR_LOG\" 2>&1
    VECTOR_RC=\$?

    VECTOR_STATUS=\$(grep -oP 'BUG005_VECTOR_FINAL: \K.*' \"\$VECTOR_LOG\" 2>/dev/null || echo 'UNKNOWN')
    if [ -z \"\$VECTOR_STATUS\" ]; then
        VECTOR_STATUS=\"EXIT_\$VECTOR_RC\"
    fi
    echo \"Vector: \$VECTOR_STATUS\" | tee -a \"\$EVIDENCE_FILE\"
    grep -E '(Vector BUG-005|Vector: |BUG005_VECTOR_FINAL)' \"\$VECTOR_LOG\" 2>/dev/null || true
fi

echo '' >> \"\$EVIDENCE_FILE\"
echo '=== Done ===' >> \"\$EVIDENCE_FILE\"
exit 0
"

echo "[wv_run_bug005.sh] Starting BUG-005 tests on sz0001..."
p9_ssh "$RUN_CMD"

echo ""
echo "[wv_run_bug005.sh] Done. Evidence: $EVIDENCE_DIR/wrap-bug005-result.txt"
echo ""
grep -E '^(SFU:|Vector:)' "$EVIDENCE_DIR/wrap-bug005-result.txt" 2>/dev/null || true
