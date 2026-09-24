#!/usr/bin/env bash
set -euo pipefail
# ─────────────────────────────────────────────────────────────────────────────
# parse_cocotb_verdict.sh — fail-closed cocotb verdict parser (exactly one log)
#
# WHY THIS EXISTS
#   Two runner bugs proved that a naive `grep -qE 'TEST.*PASS'` matches the
#   FAILING cocotb summary line "TESTS=1 PASS=0 FAIL=1" and returns exit 0
#   (BLOCKER-2, .omo/evidence/task-4-rtl-open-bugs-cleanup.txt).  A verdict may
#   therefore only be derived from a self-consistent cocotb summary line PLUS a
#   per-test cross-check, and every ambiguity must FAIL CLOSED.
#
# USAGE
#   bash scripts/parse_cocotb_verdict.sh <cocotb-log>
#   bash scripts/parse_cocotb_verdict.sh --log <cocotb-log>
#
# CONTRACT (exit status — there is no third state)
#   0  verdict PASS:
#        * exactly one summary line `TESTS=<n> PASS=<n> FAIL=<n> SKIP=<n>` exists
#        * n > 0, FAIL == 0, SKIP == 0, PASS == TESTS           (self-consistent)
#        * per-test result lines (`** <module>.<test>  PASS|FAIL  **`, ANSI
#          stripped) cross-check: count == TESTS and PASS count == PASS.
#   1  verdict FAIL or malformed/ambiguous input: missing log, missing or
#      duplicated summary line, FAIL/SKIP present, n == 0, per-test lines absent
#      or disagreeing with the summary, bad usage.
#
# OUTPUT
#   stdout: exactly one machine-readable line (safe for $(...) capture even on
#           failure, counts are 0 when unknown):
#     COCOTB_VERDICT: PASS|FAIL TESTS=<n> PASS=<n> FAIL=<n> SKIP=<n> TEST_LINES=<k> LOG=<path>
#   stderr: one-line reason for a non-PASS verdict.
# ─────────────────────────────────────────────────────────────────────────────

usage() {
    sed -n '2,40p' "${BASH_SOURCE[0]}" | sed -e 's/^# \{0,1\}//'
}

LOG=""
case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    --log)
        LOG="${2:-}"
        ;;
    "")
        LOG=""
        ;;
    *)
        LOG="${1}"
        ;;
esac

if [ -z "$LOG" ]; then
    echo "COCOTB_VERDICT: FAIL TESTS=0 PASS=0 FAIL=0 SKIP=0 TEST_LINES=0 LOG=<none>"
    echo "parse_cocotb_verdict: usage: $(basename "${BASH_SOURCE[0]}") [--log] <cocotb-log>" >&2
    exit 1
fi

# Emits the stdout verdict line and a stderr reason, then exits 1 (fail-closed).
#   $1=reason $2=TESTS $3=PASS $4=FAIL $5=SKIP $6=TEST_LINES
emit_fail() {
    echo "COCOTB_VERDICT: FAIL TESTS=${2:-0} PASS=${3:-0} FAIL=${4:-0} SKIP=${5:-0} TEST_LINES=${6:-0} LOG=${LOG}"
    echo "parse_cocotb_verdict: FAIL (${1})" >&2
    exit 1
}

if [ ! -f "$LOG" ]; then
    emit_fail "log-not-found"
fi
if [ ! -s "$LOG" ]; then
    emit_fail "log-empty"
fi

SUMMARY_RE='TESTS=[0-9]+ PASS=[0-9]+ FAIL=[0-9]+ SKIP=[0-9]+'
# Per-test result line: `** <name>  PASS|FAIL  <numbers>  **`.  The summary line
# is removed first, so `TESTS=.. PASS=..` can never be counted as a test.
TEST_RE='^[[:space:]]*\*\*[[:space:]]+[^[:space:]]+[[:space:]]+(PASS|FAIL)([[:space:]]|$)'

TMP_RAW="$(mktemp "${TMPDIR:-/tmp}/cocotb-verdict-raw.XXXXXX")"
TMP_LINES="$(mktemp "${TMPDIR:-/tmp}/cocotb-verdict-lines.XXXXXX")"
trap 'rm -f "$TMP_RAW" "$TMP_LINES"' EXIT

# Pre-filter keeps memory/disk bounded even for multi-GB logs, then strip ANSI
# (COCOTB_ANSI_OUTPUT=1 colours the PASS/FAIL token, which breaks naive greps).
LC_ALL=C grep -aE "$SUMMARY_RE|\*\*" "$LOG" 2>/dev/null \
    | LC_ALL=C sed -e 's/\x1b\[[0-9;]*m//g' > "$TMP_RAW" || true

SUMMARY_COUNT=$(LC_ALL=C grep -cE "$SUMMARY_RE" "$TMP_RAW" || true)
if [ "$SUMMARY_COUNT" -ne 1 ]; then
    emit_fail "summary-line-count=$SUMMARY_COUNT (expected exactly 1)"
fi

read -r TESTS PASS FAIL SKIP <<<"$(LC_ALL=C grep -m1 -oE "$SUMMARY_RE" "$TMP_RAW" \
    | LC_ALL=C sed -E 's/(TESTS|PASS|FAIL|SKIP)=//g')"

LC_ALL=C grep -vE "$SUMMARY_RE" "$TMP_RAW" > "$TMP_LINES" || true
TEST_LINES=$(LC_ALL=C grep -cE "$TEST_RE" "$TMP_LINES" || true)
TEST_PASS=$(LC_ALL=C grep -E "$TEST_RE" "$TMP_LINES" | LC_ALL=C grep -cE '(^|[[:space:]])PASS([[:space:]]|$)' || true)
TEST_FAIL=$(LC_ALL=C grep -E "$TEST_RE" "$TMP_LINES" | LC_ALL=C grep -cE '(^|[[:space:]])FAIL([[:space:]]|$)' || true)

if [ "$TESTS" -le 0 ]; then
    emit_fail "TESTS=$TESTS (zero-test logs are never PASS)" "$TESTS" "$PASS" "$FAIL" "$SKIP" "$TEST_LINES"
fi
if [ "$FAIL" -ne 0 ] || [ "$SKIP" -ne 0 ] || [ "$PASS" -ne "$TESTS" ]; then
    emit_fail "summary-not-all-pass (TESTS=$TESTS PASS=$PASS FAIL=$FAIL SKIP=$SKIP)" \
        "$TESTS" "$PASS" "$FAIL" "$SKIP" "$TEST_LINES"
fi
if [ "$TEST_LINES" -ne "$TESTS" ]; then
    emit_fail "per-test-lines=$TEST_LINES != summary TESTS=$TESTS" \
        "$TESTS" "$PASS" "$FAIL" "$SKIP" "$TEST_LINES"
fi
if [ "$TEST_PASS" -ne "$PASS" ] || [ "$TEST_FAIL" -ne 0 ]; then
    emit_fail "per-test-status-mismatch (lines PASS=$TEST_PASS FAIL=$TEST_FAIL vs summary PASS=$PASS FAIL=$FAIL)" \
        "$TESTS" "$PASS" "$FAIL" "$SKIP" "$TEST_LINES"
fi

echo "COCOTB_VERDICT: PASS TESTS=$TESTS PASS=$PASS FAIL=$FAIL SKIP=$SKIP TEST_LINES=$TEST_LINES LOG=$LOG"
exit 0
