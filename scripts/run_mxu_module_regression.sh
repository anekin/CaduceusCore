#!/usr/bin/env bash
set -euo pipefail
# run_mxu_module_regression.sh — MXU module-level regression
# ==============================================================================
# Compiles build/simv_mxu on sz0001, runs 9 named + 100 random scenarios,
# compares against Func Model golden, and writes
# build/evidence/fix-mxu-module-regression.txt.
#
# Expected final evidence line:
#   MXU_MODULE_REGRESSION: PASS 109/109
# ==============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SERVER="zhengs@192.168.0.11"
SIMV="$REPO_ROOT/build/simv_mxu"
RESULTS_DIR="$REPO_ROOT/rtl/results"
VECTORS_DIR="$REPO_ROOT/rtl/test_vectors/mxu"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
EVIDENCE_FILE="$EVIDENCE_DIR/fix-mxu-module-regression.txt"
RANDOM_DIR="$VECTORS_DIR/random_regression"

mkdir -p "$RESULTS_DIR" "$EVIDENCE_DIR"

# Ensure MXU test vectors exist (generate locally if missing)
if [ ! -d "$VECTORS_DIR/single_tile" ] || [ ! -d "$RANDOM_DIR" ]; then
    echo "[run_mxu_module_regression.sh] Generating MXU test vectors..."
    python3 "$REPO_ROOT/scripts/gen_mxu_vectors.py" --scenario all --out-dir "$VECTORS_DIR"
fi

# Remote environment setup on sz0001
REMOTE_ENV="source /NAS/Tools/methodology/modules/init/bash && module load vcs/vcs_2023.12sp2"

# ══════════════════════════════════════════════════════════════════════════════
# Step 1: Compile simv_mxu on sz0001
# ══════════════════════════════════════════════════════════════════════════════
echo "[run_mxu_module_regression.sh] Compiling build/simv_mxu on sz0001..."

COMPILE_LOG="$RESULTS_DIR/vcs_compile_tb_mxu.log"
ssh "$SERVER" "
    set +e
    cd '$REPO_ROOT'
    $REMOTE_ENV
    rm -rf '$SIMV' '$SIMV.daidir'
    vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_mxu \
        rtl/tb/tb_mxu.v rtl/mxu/*.v \
        -o '$SIMV' \
        -l '$COMPILE_LOG'
    echo \"COMPILE_EXIT_CODE=\$?\"
" > "$RESULTS_DIR/ssh_compile.log" 2>&1

COMPILE_RC=$(grep -oP 'COMPILE_EXIT_CODE=\K\d+' "$RESULTS_DIR/ssh_compile.log" || echo "1")
if [ "$COMPILE_RC" != "0" ]; then
    echo "[run_mxu_module_regression.sh] ERROR: Compilation failed (exit $COMPILE_RC)"
    echo "MXU_MODULE_REGRESSION: FAIL (compile error)" > "$EVIDENCE_FILE"
    exit 1
fi
echo "[run_mxu_module_regression.sh] Compilation passed."

# ══════════════════════════════════════════════════════════════════════════════
# Step 2: Run 9 named scenarios
# ══════════════════════════════════════════════════════════════════════════════
NAMED_SCENARIOS=(
    single_tile
    multi_tile_K
    multi_tile_N
    multi_tile_M
    overflow
    zero_dim
    partial_tile_K
    partial_tile_N
    partial_tile_M
)

PASS=0
FAIL=0

: > "$EVIDENCE_FILE"
echo "=== MXU Module-Level Regression $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$EVIDENCE_FILE"
echo "" >> "$EVIDENCE_FILE"
echo "Named scenarios:" >> "$EVIDENCE_FILE"

for s in "${NAMED_SCENARIOS[@]}"; do
    TESTDIR="$VECTORS_DIR/$s"
    echo "[run_mxu_module_regression.sh] Running scenario: $s ..."

    ssh "$SERVER" "
        cd '$REPO_ROOT'
        $REMOTE_ENV
        '$SIMV' +testdir='$TESTDIR' +scenario=$s -l '$RESULTS_DIR/vcs_sim_$s.log'
    " > "$RESULTS_DIR/ssh_sim_$s.log" 2>&1

    cp "$RESULTS_DIR/mxu_$s.hex" "$TESTDIR/result.hex"

    if python3 "$REPO_ROOT/sim/compare_rtl.py" "$TESTDIR" > "$RESULTS_DIR/compare_$s.log" 2>&1; then
        echo "  $s: PASS" >> "$EVIDENCE_FILE"
        PASS=$((PASS + 1))
    else
        echo "  $s: FAIL" >> "$EVIDENCE_FILE"
        FAIL=$((FAIL + 1))
    fi
done

# ══════════════════════════════════════════════════════════════════════════════
# Step 3: Run 100 random scenarios
# ══════════════════════════════════════════════════════════════════════════════
echo "[run_mxu_module_regression.sh] Running 100 random scenarios ..."

for i in $(seq -f '%03g' 0 99); do
    TESTDIR="$RANDOM_DIR/random_$i"
    ssh "$SERVER" "
        cd '$REPO_ROOT'
        $REMOTE_ENV
        '$SIMV' -no_save +testdir='$TESTDIR' +scenario=random_$i -l '$RESULTS_DIR/vcs_sim_random_$i.log'
    " > "$RESULTS_DIR/ssh_sim_random_$i.log" 2>&1
    cp "$RESULTS_DIR/mxu_random_$i.hex" "$TESTDIR/result.hex"
done

echo "" >> "$EVIDENCE_FILE"
echo "Random regression:" >> "$EVIDENCE_FILE"

BATCH_JSON="$RESULTS_DIR/random_batch.json"
if python3 "$REPO_ROOT/sim/compare_rtl.py" --batch --json "$RANDOM_DIR" > "$BATCH_JSON" 2>&1; then
    RANDOM_PASSED=$(python3 -c "import json,sys; d=json.load(open('$BATCH_JSON')); print(d['passed'])")
    RANDOM_TOTAL=$(python3 -c "import json,sys; d=json.load(open('$BATCH_JSON')); print(d['total'])")
    RANDOM_FAILED=$((RANDOM_TOTAL - RANDOM_PASSED))
    echo "  random_regression: $RANDOM_PASSED/$RANDOM_TOTAL PASS" >> "$EVIDENCE_FILE"
    PASS=$((PASS + RANDOM_PASSED))
    FAIL=$((FAIL + RANDOM_FAILED))
else
    echo "  random_regression: FAIL (batch compare error)" >> "$EVIDENCE_FILE"
    FAIL=$((FAIL + 100))
fi

# ══════════════════════════════════════════════════════════════════════════════
# Step 4: Write evidence summary
# ══════════════════════════════════════════════════════════════════════════════
TOTAL=$((PASS + FAIL))

echo "" >> "$EVIDENCE_FILE"
if [ "$FAIL" -eq 0 ]; then
    echo "MXU_MODULE_REGRESSION: PASS $PASS/$TOTAL" >> "$EVIDENCE_FILE"
else
    echo "MXU_MODULE_REGRESSION: FAIL $PASS/$TOTAL" >> "$EVIDENCE_FILE"
fi

echo ""
echo "=== MXU Module-Level Regression Results ==="
cat "$EVIDENCE_FILE"
echo ""
echo "[run_mxu_module_regression.sh] Evidence written to $EVIDENCE_FILE"

if [ "$FAIL" -eq 0 ]; then
    exit 0
else
    exit 1
fi
