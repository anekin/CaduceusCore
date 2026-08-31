#!/bin/bash
# =============================================================================
# test_timeout_behavior.sh — negative test for run_ibex_segment_run.sh
# timeout handling (plan todo 1, RED before todo 9 fix)
# =============================================================================
# Proves two defects in the CURRENT runner (sim/regression/run_ibex_segment_run.sh):
#
#   (a) Timeout exit-code mapping (runner :68-79): when the wall-time cap fires,
#       GNU timeout returns 124, but the runner maps RUN_RC=124 to `exit 0` —
#       a TIMED-OUT run reports SUCCESS to its caller.
#   (b) SEG_TIMEOUT_S validation (runner :59): the value is used directly as a
#       GNU timeout DURATION argument with no validation.  Garbage values
#       (--help / abc / -5) are not rejected with a validation error (exit 2);
#       they flow into the timeout command.
#
# RED/GREEN contract (design rule for this TDD test):
#   * Today (before W2 todo 9 fixes the runner) this script MUST exit non-zero
#     (RED) and print FAIL [RED] per failing assertion.
#   * After todo 9 lands (timeout maps 124/137 to a NON-zero exit; SEG_TIMEOUT_S
#     strictly validated, invalid value -> exit 2, no simulator launch) the
#     SAME script MUST exit 0 (GREEN).  It achieves this by extracting the
#     runner's handling logic VERBATIM from the live file at run time, so the
#     test automatically tracks whatever todo 9 changes.
#
# Why verbatim extraction instead of invoking the full runner script:
#   * run_ibex_segment_run.sh sources run_env.sh, which requires the EDA server
#     (/NAS/Tools/EDA/env/modules.bash) and aborts the script at that gate on
#     any non-EDA host.  Invoking the full script locally would exit 1 at the
#     EDA gate BEFORE ever reaching the SEG_TIMEOUT_S region, so a full-script
#     probe could never turn GREEN after todo 9 (it would keep failing on the
#     EDA gate, an unrelated behavior).  Extracting the region verbatim probes
#     exactly the logic todo 9 will edit, with no EDA dependency.
#   * The full script is still smoke-invoked ONCE (informational, no assertion)
#     below, guarded so it only runs where run_env.sh aborts before the
#     runner's `pkill -f simv_soc_ibex_seg` EXIT trap is armed (i.e. only when
#     the EDA mount is absent).  This documents the real local behavior and
#     why extraction is the assertion vehicle.
#
# Extraction contract with the W2 fixer (todo 9):
#   * Assertion (a) anchors on a line containing:   if [ "$RUN_RC" -eq 124 ]
#     and extracts that line through EOF (the timeout-decision block).  The
#     fix must keep this decision anchored such that RUN_RC=124 exits non-zero.
#   * Assertion (b) anchors on the FIRST line mentioning SEG_TIMEOUT_S and
#     extracts that line through EOF (validation + run region).  The fix must
#     place the strict positive-integer validation inside this region so that
#     invalid values exit 2 before the simulator is launched.
#   * If either anchor disappears, the test fails loudly with a contract error
#     instead of silently passing.
#
# Hazards addressed:
#   * The real simv at build/ibex_segment_rtl/simv_soc_ibex_seg is NEVER
#     touched.  The stub simulator lives in a private mktemp directory.
#   * With the garbage SEG_TIMEOUT_S values used here, GNU timeout always
#     aborts at argument parsing and never executes the (stubbed) simulator.
#     The launch marker assertion additionally proves no simulator ran.
#   * Evidence-side effects of the extracted block (append to
#     task-14-...-signoff.txt) are redirected into the private temp dir via
#     REPO_ROOT; nothing under the real repo is written.
#
# Assertion style (anti-misleading-success):
#   * This test asserts EXIT CODES and marker-file existence only.  It never
#     greps command output for fabricated text.
#
# Exit codes of THIS test script:
#   0  GREEN — all assertions passed (expected only after todo 9 fix)
#   1  RED   — at least one assertion failed (expected today)
#   3  INVALID ENVIRONMENT — prerequisites missing / timeout probe != 124
# =============================================================================

set -u          # no `set -e`: assertions drive control flow explicitly

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# RUNNER_UNDER_TEST override exists so the W2 fixer (and this task) can
# dry-verify GREEN against a fixed copy of the runner without editing the
# product script.  Default: the real runner.
RUNNER="${RUNNER_UNDER_TEST:-$REPO_ROOT/sim/regression/run_ibex_segment_run.sh}"
RUNNER_NAME="$(basename "$RUNNER")"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/test_timeout_behavior.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

PASSES=0
FAILURES=0
pass() { PASSES=$((PASSES + 1)); printf 'PASS: %s\n' "$1"; }
fail() { FAILURES=$((FAILURES + 1)); printf 'FAIL [RED]: %s\n' "$1"; }

echo "=== test_timeout_behavior.sh (todo 1 negative test) ==="
echo "REPO_ROOT: $REPO_ROOT"
echo "RUNNER:    $RUNNER"
if [ -f "$RUNNER" ]; then
    echo "RUNNER SHA256: $(sha256sum "$RUNNER" | awk '{print $1}')"
else
    echo "ERROR: runner not found: $RUNNER"
    exit 3
fi

# ── 0. Environment sanity ─────────────────────────────────────────────────
for dep in bash timeout sleep mktemp sha256sum; do
    if ! command -v "$dep" >/dev/null 2>&1; then
        echo "ERROR: prerequisite '$dep' not found on PATH"
        exit 3
    fi
done

# Generate a REAL 124 exactly as the runner's timeout wrapper would, to prove
# 124 is a reachable outcome of `timeout --signal=TERM` in this environment.
timeout --signal=TERM --kill-after=1 1 sleep 5
PROBE_124=$?
echo "real-124 probe: timeout --signal=TERM --kill-after=1 1 sleep 5 -> exit $PROBE_124"
if [ "$PROBE_124" -ne 124 ]; then
    echo "ERROR: environment cannot reproduce GNU timeout exit 124 (got $PROBE_124); test cannot be valid"
    exit 3
fi
pass "environment sanity: real timeout 124 reproducible (rc=124)"
RUN_RC_INPUT="$PROBE_124"

# Isolate evidence side effects of the extracted blocks.
mkdir -p "$TMP_DIR/build/evidence"

# ── (a) Timeout exit-code mapping ─────────────────────────────────────────
# Extract the runner's timeout-DECISION block (anchor -> EOF) verbatim and run
# it with RUN_RC=124 in an isolated subshell.  Expected fixed behavior:
# non-zero exit.  Current behavior: exit 0 (defect).
ANCHOR_A="$(grep -nF 'if [ "$RUN_RC" -eq 124 ]' "$RUNNER" | head -1 | cut -d: -f1)"
echo
echo "--- assertion (a): timeout exit-code mapping ---"
echo "extraction anchor (runner line with the 124 decision): ${ANCHOR_A:-<NOT FOUND>}"
if [ -z "$ANCHOR_A" ]; then
    fail "extraction contract broken: no 'if [ \"\$RUN_RC\" -eq 124 ]' line in $RUNNER_NAME (todo 9 must keep this decision anchor)"
else
    sed -n "${ANCHOR_A},\$p" "$RUNNER" > "$TMP_DIR/decision_block.sh"
    echo "[verbatim extracted decision block from $RUNNER_NAME:$ANCHOR_A..EOF]"
    cat "$TMP_DIR/decision_block.sh"
    echo "[end extracted block]"
    RUN_RC="$RUN_RC_INPUT" SEG_TIMEOUT_S=86400 REPO_ROOT="$TMP_DIR" \
        bash "$TMP_DIR/decision_block.sh" > "$TMP_DIR/a.out" 2>&1
    RC_A=$?
    echo "extracted block exit code with RUN_RC=124: $RC_A"
    if [ "$RC_A" -ne 0 ]; then
        pass "timeout exit-code mapping: RUN_RC=124 maps to NON-zero exit ($RC_A)"
    else
        fail "timeout exit-code mapping: RUN_RC=124 maps to exit 0 ($RC_A) — a timed-out run reports SUCCESS (runner ${RUNNER_NAME}:${ANCHOR_A}..EOF)"
    fi
fi

# ── (b) SEG_TIMEOUT_S validation ──────────────────────────────────────────
# Extract the runner's SEG_TIMEOUT_S region (first mention -> EOF) verbatim,
# run it per invalid value with a STUBBED simulator in a private dir, and
# check whether a validation error (exit 2) fired BEFORE the simulator was
# ever launched.  Expected fixed behavior: exit 2, launch marker absent.
# Current behavior: no validation — exit 0 (--help) / 125 (abc, -5).
ANCHOR_B="$(grep -nF 'SEG_TIMEOUT_S' "$RUNNER" | head -1 | cut -d: -f1)"
echo
echo "--- assertion (b): SEG_TIMEOUT_S validation ---"
echo "extraction anchor (runner line with the first SEG_TIMEOUT_S mention): ${ANCHOR_B:-<NOT FOUND>}"
if [ -z "$ANCHOR_B" ]; then
    fail "extraction contract broken: no SEG_TIMEOUT_S line in $RUNNER_NAME (todo 9 must keep the validation within this region)"
else
    sed -n "${ANCHOR_B},\$p" "$RUNNER" > "$TMP_DIR/seg_region.sh"
    echo "[verbatim extracted SEG_TIMEOUT_S region from $RUNNER_NAME:$ANCHOR_B..EOF ($(wc -l < "$TMP_DIR/seg_region.sh") lines)]"
    STUB="$TMP_DIR/stub_simv"
    printf '#!/bin/sh\ntouch "%s/launch_marker"\nexit 0\n' "$TMP_DIR" > "$STUB"
    chmod +x "$STUB"
    BAD_IDX=0
    for BAD_VALUE in --help abc -5; do
        BAD_IDX=$((BAD_IDX + 1))
        rm -f "$TMP_DIR/launch_marker"
        SEG_TIMEOUT_S="$BAD_VALUE" RUN_DIR="$TMP_DIR" REPO_ROOT="$TMP_DIR" SIMV="$STUB" \
            bash "$TMP_DIR/seg_region.sh" > "$TMP_DIR/b_${BAD_IDX}.out" 2> "$TMP_DIR/b_${BAD_IDX}.err"
        RC_B=$?
        echo "SEG_TIMEOUT_S='$BAD_VALUE' -> extracted region exit code: $RC_B; simulator launched: $( [ -f "$TMP_DIR/launch_marker" ] && echo YES || echo no )"
        if [ "$RC_B" -eq 2 ]; then
            pass "SEG_TIMEOUT_S='$BAD_VALUE' rejected with validation exit code 2"
        else
            fail "SEG_TIMEOUT_S='$BAD_VALUE' NOT rejected with exit 2 (region exited $RC_B) — no SEG_TIMEOUT_S validation in ${RUNNER_NAME}:${ANCHOR_B}..EOF"
        fi
        if [ ! -f "$TMP_DIR/launch_marker" ]; then
            pass "SEG_TIMEOUT_S='$BAD_VALUE': simulator NOT launched (validation must fire before any launch)"
        else
            fail "SEG_TIMEOUT_S='$BAD_VALUE': simulator WAS launched before any validation — ordering violation"
        fi
    done
fi

# ── Informational: full-runner smoke (no assertion) ───────────────────────
# Only where run_env.sh is guaranteed to abort before the runner's pkill trap
# is armed (no EDA mount).  Documents why the assertions use extraction.
echo
echo "--- informational: full-runner smoke (no assertion) ---"
if [ -f /NAS/Tools/EDA/env/modules.bash ]; then
    echo "[INFO] EDA mount present; skipping full-runner smoke to avoid arming the runner's pkill EXIT trap on a shared host"
else
    SEG_TIMEOUT_S=abc bash "$RUNNER" > "$TMP_DIR/smoke.out" 2>&1
    SMOKE_RC=$?
    echo "[INFO] SEG_TIMEOUT_S=abc bash $RUNNER_NAME -> exit $SMOKE_RC"
    echo "[INFO] (on non-EDA hosts run_env.sh aborts the script at its EDA gate before the SEG_TIMEOUT_S region, hence the verbatim-extraction assertions above)"
    sed -n '1,3p' "$TMP_DIR/smoke.out" | sed 's/^/[INFO] smoke output: /'
fi

# ── Summary ───────────────────────────────────────────────────────────────
echo
echo "=== summary ==="
echo "assertions passed: $PASSES"
echo "assertions failed: $FAILURES"
if [ "$FAILURES" -gt 0 ]; then
    echo "TEST RESULT: RED — $FAILURES assertion(s) failed (expected before todo 9 fix; must turn GREEN after the fix)"
    exit 1
fi
echo "TEST RESULT: GREEN — all assertions passed"
exit 0
