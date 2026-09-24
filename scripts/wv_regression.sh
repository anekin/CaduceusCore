#!/usr/bin/env bash
set -euo pipefail
# wv_regression.sh — Wave 3 regression aggregator for wrapper-level verification
# ==============================================================================
# Task: wrapper-level-verification / T8 (Wave 3)
#
# Parses the wrapper-suite evidence files from T2-T6 through the shared
# fail-closed verdict parser (scripts/parse_cocotb_verdict.sh) and produces a
# structured summary.  If an aggregate evidence file is missing or stale, the
# corresponding runner is invoked to regenerate it.
#
# Verdict source: the three cocotb suites are judged ONLY by
# scripts/parse_cocotb_verdict.sh (summary line self-consistency + per-test
# cross-check).  The earlier revision hardcoded "1 PASS / 4 FAIL / 5 total" for
# SFU, grepped 'ALL 5 PASS' for Vector and 'Summary: 5 PASS, 0 FAIL' for MXU,
# and always exited 0 — that is the fail-open pattern this aggregator no longer
# has.  Exit status is now 0 only when all three suites are PASS.
#
# Evidence files consumed:
#   build/evidence/wrap-sfu-regression.txt   (T2, raw SFU cocotb output)
#   build/evidence/wv_vector_logs/*.log      (T3, one log per Vector case)
#   build/evidence/wv-mxu-*.log              (T4, one log per MXU case)
#   build/evidence/wrap-bug005-result.txt    (T5, BUG-005 result lines)
#   build/evidence/wrap-bug007-result.txt    (T6, BUG-007 result lines)
#
# Output:
#   build/evidence/wrap-regression-summary.txt
# ==============================================================================

source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

EVIDENCE_DIR="$REPO_ROOT/build/evidence"
SUMMARY_FILE="$EVIDENCE_DIR/wrap-regression-summary.txt"
SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
VERDICT_HELPER="$SCRIPTS_DIR/parse_cocotb_verdict.sh"

mkdir -p "$EVIDENCE_DIR"

# ── Helper: check evidence file existence and freshness ─────────────────
_evidence_ok() {
    local file="$1"
    local label="$2"
    local min_bytes="${3:-50}"

    if [ ! -f "$file" ]; then
        echo "[wv_regression] $label: MISSING ($file)"
        return 1
    fi
    local sz
    sz=$(stat -c%s "$file" 2>/dev/null || echo 0)
    if [ "$sz" -lt "$min_bytes" ]; then
        echo "[wv_regression] $label: TOO SMALL ($sz bytes < $min_bytes)"
        return 1
    fi
    echo "[wv_regression] $label: OK ($sz bytes)"
    return 0
}

# ── Helper: per-case log list of a runner (single source of truth) ──────
# Reads the runner's `TESTS=( ... )` block so the aggregate can never silently
# drift from the cases the runner actually executes.
_runner_tests() {
    sed -n '/^TESTS=(/,/^)/p' "$1" | grep -oE 'test_[A-Za-z0-9_]+' | sort -u
}

# ── Helper: sum verdicts of a list of cocotb logs via the shared parser ──
# Echoes "<logs> <tests> <pass> <fail> <notpass_logs>"; files that do not
# exist are skipped (the caller treats logs==0 as EVIDENCE-MISSING).
# shellcheck disable=SC2016
_verdict_stats() {
    local log parsed t p f
    local logs=0 tests=0 pass=0 fail=0 notpass=0
    for log in "$@"; do
        [ -f "$log" ] || continue
        logs=$((logs + 1))
        if parsed=$(bash "$VERDICT_HELPER" --log "$log" 2>/dev/null); then
            :
        else
            notpass=$((notpass + 1))
        fi
        t=$(printf '%s\n' "$parsed" | grep -oE 'TESTS=[0-9]+' | head -1 | cut -d= -f2)
        p=$(printf '%s\n' "$parsed" | grep -oE ' PASS=[0-9]+' | head -1 | cut -d= -f2)
        f=$(printf '%s\n' "$parsed" | grep -oE ' FAIL=[0-9]+' | head -1 | cut -d= -f2)
        tests=$((tests + ${t:-0}))
        pass=$((pass + ${p:-0}))
        fail=$((fail + ${f:-0}))
    done
    echo "$logs $tests $pass $fail $notpass"
}

# ── Ensure each aggregate evidence file exists; re-run runner if missing ──
for runner in sfu vector mxu bug005 bug007; do
    case "$runner" in
        sfu)
            EVIDENCE="$EVIDENCE_DIR/wrap-sfu-regression.txt"
            RUNNER="$SCRIPTS_DIR/wv_run_sfu.sh"
            MIN_BYTES=1000
            ;;
        vector)
            EVIDENCE="$EVIDENCE_DIR/wrap-vec-regression.txt"
            RUNNER="$SCRIPTS_DIR/wv_run_vector.sh"
            MIN_BYTES=50
            ;;
        mxu)
            EVIDENCE="$EVIDENCE_DIR/wrap-mxu-regression.txt"
            RUNNER="$SCRIPTS_DIR/wv_run_mxu.sh"
            MIN_BYTES=50
            ;;
        bug005)
            EVIDENCE="$EVIDENCE_DIR/wrap-bug005-result.txt"
            RUNNER="$SCRIPTS_DIR/wv_run_bug005.sh"
            MIN_BYTES=50
            ;;
        bug007)
            EVIDENCE="$EVIDENCE_DIR/wrap-bug007-result.txt"
            RUNNER="$SCRIPTS_DIR/wv_run_bug007.sh"
            MIN_BYTES=50
            ;;
    esac

    if ! _evidence_ok "$EVIDENCE" "$runner" "$MIN_BYTES"; then
        echo "[wv_regression] Running $RUNNER to regenerate evidence..."
        bash "$RUNNER" || echo "[wv_regression] WARNING: $RUNNER exited non-zero (evidence capture mode)"
    fi
done

# ══════════════════════════════════════════════════════════════════════════════
# Parse evidence files and build aggregated summary
# ══════════════════════════════════════════════════════════════════════════════

echo "=== Wrapper-Level Regression Summary ===" > "$SUMMARY_FILE"
echo "Date: $(date '+%Y-%m-%d %H:%M:%S %Z')" >> "$SUMMARY_FILE"
echo "Plan:  wrapper-level-verification (Waves 0-3)" >> "$SUMMARY_FILE"
echo "Verdict source: scripts/parse_cocotb_verdict.sh (fail-closed)" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"

# ── SFU (single aggregate cocotb log: all 7 cases in one summary) ───────
echo "--- SFU Wrapper ---" >> "$SUMMARY_FILE"
SFU_FILE="$EVIDENCE_DIR/wrap-sfu-regression.txt"
read -r SFU_LOGS SFU_TESTS SFU_PASS SFU_FAIL SFU_NOTPASS < <(_verdict_stats "$SFU_FILE")
if [ "$SFU_LOGS" -eq 0 ]; then
    echo "  Status: EVIDENCE-MISSING ($SFU_FILE)" >> "$SUMMARY_FILE"
    echo "  SFU: EVIDENCE-MISSING" >> "$SUMMARY_FILE"
    SFU_STATUS="EVIDENCE-MISSING"
else
    echo "# Legacy context: the historical SFU failures were logged as" >> "$SUMMARY_FILE"
    echo "# BUG-RTL-SOC-WV-001 (STATUS.DONE never asserts)." >> "$SUMMARY_FILE"
    echo "  Tests: $SFU_TESTS total, $SFU_PASS PASS, $SFU_FAIL FAIL" >> "$SUMMARY_FILE"
    if [ "$SFU_NOTPASS" -eq 0 ]; then
        echo "  Status: PASS" >> "$SUMMARY_FILE"
        echo "  SFU: PASS" >> "$SUMMARY_FILE"
        SFU_STATUS="PASS"
    else
        echo "  Status: FAIL ($SFU_NOTPASS of $SFU_LOGS logs did not parse as all-PASS)" >> "$SUMMARY_FILE"
        echo "  SFU: FAIL" >> "$SUMMARY_FILE"
        SFU_STATUS="FAIL"
    fi
fi
echo "" >> "$SUMMARY_FILE"

# ── Vector (one cocotb log per case, aggregated) ────────────────────────
echo "--- Vector Wrapper ---" >> "$SUMMARY_FILE"
VEC_LOGS=()
while IFS= read -r _test; do
    VEC_LOGS+=("$EVIDENCE_DIR/wv_vector_logs/${_test}.log")
done < <(_runner_tests "$SCRIPTS_DIR/wv_run_vector.sh")
read -r VEC_NLOGS VEC_TESTS VEC_PASS VEC_FAIL VEC_NOTPASS < <(_verdict_stats ${VEC_LOGS[@]+"${VEC_LOGS[@]}"})
if [ "$VEC_NLOGS" -eq 0 ]; then
    echo "  Status: EVIDENCE-MISSING (no logs under $EVIDENCE_DIR/wv_vector_logs)" >> "$SUMMARY_FILE"
    echo "  Vector: EVIDENCE-MISSING" >> "$SUMMARY_FILE"
    VEC_STATUS="EVIDENCE-MISSING"
else
    echo "  Tests: $VEC_TESTS total, $VEC_PASS PASS, $VEC_FAIL FAIL" >> "$SUMMARY_FILE"
    if [ "$VEC_NOTPASS" -eq 0 ] && [ "$VEC_NLOGS" -eq "${#VEC_LOGS[@]}" ]; then
        echo "  Status: PASS" >> "$SUMMARY_FILE"
        echo "  Vector: PASS ($VEC_PASS/$VEC_TESTS)" >> "$SUMMARY_FILE"
        VEC_STATUS="PASS"
    else
        echo "  Status: FAIL ($VEC_NOTPASS of ${#VEC_LOGS[@]} cases not all-PASS, $VEC_NLOGS logs found)" >> "$SUMMARY_FILE"
        echo "  Vector: FAIL" >> "$SUMMARY_FILE"
        VEC_STATUS="FAIL"
    fi
fi
echo "" >> "$SUMMARY_FILE"

# ── MXU (one cocotb log per case, aggregated) ───────────────────────────
echo "--- MXU Wrapper ---" >> "$SUMMARY_FILE"
MXU_LOGS=()
while IFS= read -r _test; do
    MXU_LOGS+=("$EVIDENCE_DIR/wv-mxu-${_test}.log")
done < <(_runner_tests "$SCRIPTS_DIR/wv_run_mxu.sh")
read -r MXU_NLOGS MXU_TESTS MXU_PASS MXU_FAIL MXU_NOTPASS < <(_verdict_stats ${MXU_LOGS[@]+"${MXU_LOGS[@]}"})
if [ "$MXU_NLOGS" -eq 0 ]; then
    echo "  Status: EVIDENCE-MISSING (no logs under $EVIDENCE_DIR/wv-mxu-*.log)" >> "$SUMMARY_FILE"
    echo "  MXU: EVIDENCE-MISSING" >> "$SUMMARY_FILE"
    MXU_STATUS="EVIDENCE-MISSING"
else
    echo "  Tests: $MXU_TESTS total, $MXU_PASS PASS, $MXU_FAIL FAIL" >> "$SUMMARY_FILE"
    if [ "$MXU_NOTPASS" -eq 0 ] && [ "$MXU_NLOGS" -eq "${#MXU_LOGS[@]}" ]; then
        echo "  Status: PASS" >> "$SUMMARY_FILE"
        echo "  MXU: PASS ($MXU_PASS/$MXU_TESTS)" >> "$SUMMARY_FILE"
        MXU_STATUS="PASS"
    else
        echo "  Status: FAIL ($MXU_NOTPASS of ${#MXU_LOGS[@]} cases not all-PASS, $MXU_NLOGS logs found)" >> "$SUMMARY_FILE"
        echo "  MXU: FAIL" >> "$SUMMARY_FILE"
        MXU_STATUS="FAIL"
    fi
fi
echo "" >> "$SUMMARY_FILE"

# ── BUG-005 ─────────────────────────────────────────────────────────────
# Transcription of the historical bug-runner result file; NOT a re-derived
# verdict (the file itself is a single-source artifact of wv_run_bug005.sh).
echo "--- BUG-005 (AXI Sparse Slave X-Propagation) ---" >> "$SUMMARY_FILE"
BUG005_FILE="$EVIDENCE_DIR/wrap-bug005-result.txt"
if [ -f "$BUG005_FILE" ]; then
    BUG005_SFU=$(grep -oP '^SFU: \K.*' "$BUG005_FILE" 2>/dev/null || echo "UNKNOWN")
    BUG005_VEC=$(grep -oP '^Vector: \K.*' "$BUG005_FILE" 2>/dev/null || echo "UNKNOWN")
    echo "  SFU:   $BUG005_SFU" >> "$SUMMARY_FILE"
    echo "  Vector: $BUG005_VEC" >> "$SUMMARY_FILE"
    echo "  BUG-005 SFU: $BUG005_SFU" >> "$SUMMARY_FILE"
    echo "  BUG-005 Vector: $BUG005_VEC" >> "$SUMMARY_FILE"
else
    echo "  Status: EVIDENCE-MISSING" >> "$SUMMARY_FILE"
    echo "  BUG-005: EVIDENCE-MISSING" >> "$SUMMARY_FILE"
fi
echo "" >> "$SUMMARY_FILE"

# ── BUG-007 ─────────────────────────────────────────────────────────────
# Same transcription rule as BUG-005 above.
echo "--- BUG-007 (Consecutive Multi-Op Dispatch) ---" >> "$SUMMARY_FILE"
BUG007_FILE="$EVIDENCE_DIR/wrap-bug007-result.txt"
if [ -f "$BUG007_FILE" ]; then
    BUG007_MXU=$(grep -oP '^MXU: \K.*' "$BUG007_FILE" 2>/dev/null || echo "UNKNOWN")
    BUG007_SFU=$(grep -oP '^SFU: \K.*' "$BUG007_FILE" 2>/dev/null || echo "UNKNOWN")
    echo "  MXU: $BUG007_MXU" >> "$SUMMARY_FILE"
    echo "  SFU: $BUG007_SFU" >> "$SUMMARY_FILE"
    echo "  BUG-007 MXU: $BUG007_MXU" >> "$SUMMARY_FILE"
    echo "  BUG-007 SFU: $BUG007_SFU" >> "$SUMMARY_FILE"
else
    echo "  Status: EVIDENCE-MISSING" >> "$SUMMARY_FILE"
    echo "  BUG-007: EVIDENCE-MISSING" >> "$SUMMARY_FILE"
fi
echo "" >> "$SUMMARY_FILE"

# ── Overall tally (derived from the helper verdicts above) ──────────────
echo "--- Overall ---" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"

TOTAL_PASS=0
TOTAL_NOTPASS=0
for _spec in "SFU:$SFU_STATUS" "Vector:$VEC_STATUS" "MXU:$MXU_STATUS"; do
    _label="${_spec%%:*}"
    _status="${_spec#*:}"
    if [ "$_status" = "PASS" ]; then
        echo "  ${_label} wrapper: PASS" >> "$SUMMARY_FILE"
        TOTAL_PASS=$((TOTAL_PASS + 1))
    else
        echo "  ${_label} wrapper: $_status" >> "$SUMMARY_FILE"
        TOTAL_NOTPASS=$((TOTAL_NOTPASS + 1))
    fi
done
TOTAL_SUITES=$((TOTAL_PASS + TOTAL_NOTPASS))

echo "" >> "$SUMMARY_FILE"
echo "Evidence base:" >> "$SUMMARY_FILE"
echo "  SFU:    build/evidence/wrap-sfu-regression.txt" >> "$SUMMARY_FILE"
echo "  Vector: build/evidence/wv_vector_logs/<case>.log" >> "$SUMMARY_FILE"
echo "  MXU:    build/evidence/wv-mxu-<case>.log" >> "$SUMMARY_FILE"
echo "  BUG005: build/evidence/wrap-bug005-result.txt" >> "$SUMMARY_FILE"
echo "  BUG007: build/evidence/wrap-bug007-result.txt" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"
if [ "$TOTAL_NOTPASS" -eq 0 ]; then
    echo "  Overall: PASS ($TOTAL_PASS/$TOTAL_SUITES suites)" >> "$SUMMARY_FILE"
else
    echo "  Overall: FAIL ($TOTAL_PASS/$TOTAL_SUITES suites PASS, $TOTAL_NOTPASS not-PASS)" >> "$SUMMARY_FILE"
fi
echo "Generated: $(date -Iseconds)" >> "$SUMMARY_FILE"

echo ""
echo "=== Wrapper Regression Summary ==="
cat "$SUMMARY_FILE"
echo ""

if [ "$TOTAL_NOTPASS" -ne 0 ]; then
    echo "[wv_regression.sh] FAIL: $TOTAL_NOTPASS of $TOTAL_SUITES suites are not PASS — see $SUMMARY_FILE" >&2
    exit 1
fi
echo "[wv_regression.sh] PASS: all $TOTAL_SUITES suites PASS — summary written to: $SUMMARY_FILE"
exit 0
