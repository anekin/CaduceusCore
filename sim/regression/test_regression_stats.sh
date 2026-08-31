#!/bin/bash
# =============================================================================
# test_regression_stats.sh — negative test for the 33-case FM-SOC classification
# loop in sim/regression/run_ibex_full_rtl.sh (plan todo 4, RED now; designed to
# turn GREEN after todo 9 fixes the runner). Fully local: no EDA, no VCS, no
# network — only reads run_ibex_full_rtl.sh, the case manifest, and real logs.
#
# Method: the CURRENT classification loop body is extracted from the LIVE
# run_ibex_full_rtl.sh at run time (anchors: 'for CASE in $CASES; do' ... the
# first bare 'done') and evaluated in a sandbox where $SIMV is a fake simulator
# stub that replays pre-made sample logs. Because the logic is re-extracted on
# every run, this test re-validates whatever version of the runner is checked
# out — RED against the pre-fix script, GREEN after todo 9 fixes it.
#
# Verbatim reference of the PRE-FIX classification logic
# (extracted 2026-08-31 from run_ibex_full_rtl.sh:85-96):
#
#   :85  (cd "$RUN_DIR" && "$SIMV" +COCOTB +FM_SOC_CASE_ID="$CASE" \
#          +BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex") \
#          > "$CASE_LOG" 2>&1 || true
#   :86  if grep -qE 'superseded by FM-SOC-032/10X' "$CASE_LOG" || \
#   :87     grep -qE 'skipped: direct APB/AXI case not applicable to Ibex RTL mode' "$CASE_LOG"; then
#   :88      echo "[SKIP] $CASE"
#   :89      SKIP=$((SKIP + 1))
#   :90  elif grep -qE 'TESTS=1 PASS=1 FAIL=0 SKIP=0' "$CASE_LOG"; then
#   :91      echo "[PASS] $CASE"
#   :92      PASS=$((PASS + 1))
#   :93  else
#   :94      echo "[FAIL] $CASE (log: $CASE_LOG)"
#   :95      FAIL=$((FAIL + 1))
#   :96  fi
#
# Note the :86 grep pattern 'superseded by FM-SOC-032/10X' does NOT match the
# message the runner actually emits at sim/rtl_soc_runner.py:4279
# ("superseded by FM-SOC-027/032/10X"), so a superseded case whose cocotb run
# reports 'TESTS=1 PASS=1 FAIL=0 SKIP=0' falls through to :90 and is counted
# PASS. That is the bug assertion A1 pins. Assertion A4 pins the `|| true` at
# :85 swallowing a simulator's non-zero exit code.
#
# Sample logs (three kinds, per the manifest docs/fm_soc_case_manifest.csv):
#   (a) EXECUTED (25):  real logs from build/ibex_full_rtl/evidence/<case>.log
#                       (contain the cocotb PASS summary).
#   (b) SUPERSEDED (6): real log + injected runner message from
#                       sim/rtl_soc_runner.py:4279.
#   (c) N/A (2):        real log + injected runner message from
#                       sim/rtl_soc_runner.py:4282.
#   (probe)             synthetic log truncated before any summary; fake
#                       simulator exits 124 (GNU timeout kill).
#
# Assertions (pre-fix expectation):
#   A1 superseded 014/015/016-style logs classified SKIP, NOT PASS  -> RED
#   A2 N/A (017/019) logs classified SKIP                           -> GREEN
#   A3 summary emits four classes PASS/SKIP/FAIL/TIMEOUT, sum == 33 -> GREEN
#   A4 simulator non-zero exit code NOT swallowed by `|| true`      -> RED
#   A0 manifest integrity (33 rows, 25/6/2 split)                   -> GREEN
#
# Post-todo-9 contract assumed by A3/A4: the fixed loop keeps the variable
# names PASS/SKIP/FAIL/TIMEOUT, maps exit 124 -> TIMEOUT, exit 0 + cocotb
# summary -> PASS, other non-zero -> FAIL, and its SKIP greps match the
# rtl_soc_runner.py:4279/:4282 messages (per plan todo 9 acceptance criteria).
# =============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNNER="$REPO_ROOT/sim/regression/run_ibex_full_rtl.sh"
LOGS="$REPO_ROOT/build/ibex_full_rtl/evidence"
MANIFEST="$REPO_ROOT/docs/fm_soc_case_manifest.csv"

ALL_CASES="FM-SOC-001 FM-SOC-002 FM-SOC-003 FM-SOC-004 FM-SOC-005 FM-SOC-006 FM-SOC-007 FM-SOC-008 FM-SOC-009 FM-SOC-010 FM-SOC-011 FM-SOC-012 FM-SOC-013 FM-SOC-014 FM-SOC-015 FM-SOC-016 FM-SOC-017 FM-SOC-018 FM-SOC-019 FM-SOC-020 FM-SOC-021 FM-SOC-022 FM-SOC-023 FM-SOC-024 FM-SOC-025 FM-SOC-026 FM-SOC-027 FM-SOC-028 FM-SOC-029 FM-SOC-030 FM-SOC-031 FM-SOC-032 FM-SOC-10X"
SUPERSEDED_CASES="FM-SOC-014 FM-SOC-015 FM-SOC-016 FM-SOC-021 FM-SOC-022 FM-SOC-023"
NA_CASES="FM-SOC-017 FM-SOC-019"
PROBE="FM-SOC-PROBE-124"

TMP="$(mktemp -d /tmp/test_regression_stats.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
RUN_DIR="$TMP/run"
EVIDENCE_DIR="$TMP/evidence"
SAMPLE_DIR="$TMP/samples"
mkdir -p "$RUN_DIR" "$EVIDENCE_DIR" "$SAMPLE_DIR"

FAILED_ASSERTS=0
note()  { echo "[TEST] $*"; }
apass() { echo "ASSERT $1: PASS - $2"; }
afail() { echo "ASSERT $1: FAIL - $2"; FAILED_ASSERTS=$((FAILED_ASSERTS + 1)); }

# ---------------------------------------------------------------------------
# 1. Extract the CURRENT classification loop body from the live runner script.
# ---------------------------------------------------------------------------
BODY="$(awk '/^for CASE in \$CASES; do$/{f=1} f{print} f&&/^done$/{exit}' "$RUNNER")"
if [ -z "$BODY" ]; then
    echo "ASSERT EXTRACT: FAIL - could not extract loop body from $RUNNER (anchor 'for CASE in \$CASES; do' .. 'done')"
    exit 2
fi

if printf '%s\n' "$BODY" | grep -qF '|| true'; then
    RUNNER_STATE="pre-fix (line with '|| true' present in live script)"
else
    RUNNER_STATE="MODIFIED (no '|| true' in live loop) - todo 9 fix applied?"
fi
if printf '%s\n' "$BODY" | grep -qF "grep -qE 'superseded by FM-SOC-032/10X'"; then
    GREP_STATE="pre-fix superseded grep ('FM-SOC-032/10X' - does not match rtl_soc_runner.py:4279 message 'FM-SOC-027/032/10X')"
else
    GREP_STATE="modified superseded grep (matches :4279 message?)"
fi

note "runner: $RUNNER"
note "extracted loop state: $RUNNER_STATE"
note "extracted superseded grep: $GREP_STATE"
note "manifest: $MANIFEST"
note "real log source: $LOGS"

# ---------------------------------------------------------------------------
# 2. Build the 33 sample case logs (+ probe).
# ---------------------------------------------------------------------------
for c in $ALL_CASES; do
    src="$LOGS/$c.log"
    dst="$SAMPLE_DIR/$c.log.sample"
    if [ -f "$src" ]; then
        cp "$src" "$dst"
    else
        printf '** TESTS=1 PASS=1 FAIL=0 SKIP=0 **\n' > "$dst"
        note "no real log for $c - using synthetic PASS summary sample"
    fi
    case " $SUPERSEDED_CASES " in
        *" $c "*) sed -i "1i IbexRunner $c: PASS - superseded by FM-SOC-027/032/10X" "$dst" ;;
    esac
    case " $NA_CASES " in
        *" $c "*) sed -i "1i IbexRunner $c: PASS - skipped: direct APB/AXI case not applicable to Ibex RTL mode" "$dst" ;;
    esac
done

# Probe: log truncated before any summary (realistic timeout kill).
if [ -f "$LOGS/FM-SOC-001.log" ]; then
    head -n 8 "$LOGS/FM-SOC-001.log" > "$SAMPLE_DIR/$PROBE.log.sample"
else
    : > "$SAMPLE_DIR/$PROBE.log.sample"
fi
printf '** simulator killed (GNU timeout, exit 124) - log truncated, no summary **\n' \
    >> "$SAMPLE_DIR/$PROBE.log.sample"

# ---------------------------------------------------------------------------
# 3. Fake simulator stub (no EDA). Replays the sample log for FM_SOC_CASE_ID
#    and exits with $STUB_EXIT_CODE (0 = normal cocotb completion).
# ---------------------------------------------------------------------------
STUB="$RUN_DIR/simv_soc_ibex"
cat > "$STUB" <<'STUBEOF'
#!/bin/bash
cid="${FM_SOC_CASE_ID:-unknown}"
cat "${SAMPLE_DIR}/${cid}.log.sample" 2>/dev/null \
    || cat "${SAMPLE_DIR}/FM-SOC-014.log.sample" 2>/dev/null \
    || true
exit "${STUB_EXIT_CODE:-0}"
STUBEOF
chmod +x "$STUB"

# ---------------------------------------------------------------------------
# 4. Evaluate the extracted loop body in a sandbox against the samples.
# ---------------------------------------------------------------------------
run_loop() {  # $1 = case list, $2 = stub exit code
    (
        set -u
        PASS=0; FAIL=0; SKIP=0; TIMEOUT=0
        CASES="$1"
        EVIDENCE_DIR="$EVIDENCE_DIR"
        RUN_DIR="$RUN_DIR"
        SIMV="$STUB"
        REPO_ROOT="$REPO_ROOT"
        STUB_EXIT_CODE="$2"
        SAMPLE_DIR="$SAMPLE_DIR"
        export FM_SOC_CASE_ID STUB_EXIT_CODE SAMPLE_DIR
        eval "$BODY"
        printf 'STATS PASS=%d SKIP=%d FAIL=%d TIMEOUT=%d\n' \
            "$PASS" "$SKIP" "$FAIL" "$TIMEOUT"
    )
}

note "running extracted loop over all 33 cases (stub exit code 0) ..."
OUT_33="$(run_loop "$ALL_CASES" 0)"
STATS_33="$(printf '%s\n' "$OUT_33" | grep '^STATS ' | tail -1)"
CLASS_33="$(printf '%s\n' "$OUT_33" | sed -n 's/^\[\(PASS\|SKIP\|FAIL\|TIMEOUT\)\] \(FM-SOC-[0-9A-Z]*\).*/\1 \2/p')"

p33=$(printf '%s\n' "$STATS_33" | sed -n 's/.* PASS=\([0-9]*\).*/\1/p'); p33=${p33:-0}
s33=$(printf '%s\n' "$STATS_33" | sed -n 's/.* SKIP=\([0-9]*\).*/\1/p'); s33=${s33:-0}
f33=$(printf '%s\n' "$STATS_33" | sed -n 's/.* FAIL=\([0-9]*\).*/\1/p'); f33=${f33:-0}
t33=$(printf '%s\n' "$STATS_33" | sed -n 's/.* TIMEOUT=\([0-9]*\).*/\1/p'); t33=${t33:-0}

# ---------------------------------------------------------------------------
# 5. Assertions.
# ---------------------------------------------------------------------------

# A1: superseded cases (014/015/016/021/022/023) must be SKIP, NOT PASS.
A1_BAD=""
for c in $SUPERSEDED_CASES; do
    actual=$(printf '%s\n' "$CLASS_33" | grep " $c\$" | awk '{print $1}')
    if [ "$actual" = "SKIP" ]; then
        echo "  A1 $c: SKIP (ok)"
    else
        A1_BAD="$A1_BAD $c->${actual:-UNSEEN}"
    fi
done
if [ -z "$A1_BAD" ]; then
    apass 1 "all 6 superseded cases (FM-SOC-014/015/016/021/022/023) classified SKIP, not PASS"
else
    afail 1 "superseded cases misclassified (must be SKIP, not PASS):${A1_BAD} - the :86 grep 'superseded by FM-SOC-032/10X' does not match the :4279 message 'superseded by FM-SOC-027/032/10X'"
fi

# A2: N/A cases (017/019) must be SKIP.
A2_BAD=""
for c in $NA_CASES; do
    actual=$(printf '%s\n' "$CLASS_33" | grep " $c\$" | awk '{print $1}')
    if [ "$actual" = "SKIP" ]; then
        echo "  A2 $c: SKIP (ok)"
    else
        A2_BAD="$A2_BAD $c->${actual:-UNSEEN}"
    fi
done
if [ -z "$A2_BAD" ]; then
    apass 2 "N/A cases (FM-SOC-017/019) classified SKIP"
else
    afail 2 "N/A cases misclassified:${A2_BAD}"
fi

# A3: four-class summary PASS/SKIP/FAIL/TIMEOUT with sum == 33.
TOTAL=$((p33 + s33 + f33 + t33))
echo "[SUMMARY] simulated 33-case summary (four-class contract PASS/SKIP/FAIL/TIMEOUT)"
echo "  PASS:    $p33"
echo "  SKIP:    $s33"
echo "  FAIL:    $f33"
echo "  TIMEOUT: $t33"
echo "  TOTAL:   $TOTAL"
if [ "$TOTAL" -eq 33 ]; then
    apass 3 "summary emits four classes PASS/SKIP/FAIL/TIMEOUT and their sum == 33"
else
    afail 3 "four-class sum = $TOTAL, expected 33"
fi

# A4: simulator non-zero exit code must NOT be swallowed by `|| true` (:85).
note "running probe $PROBE with stub exit code 124 (timeout kill) ..."
OUT_PROBE="$(run_loop "$PROBE" 124)"
STATS_PROBE="$(printf '%s\n' "$OUT_PROBE" | grep '^STATS ' | tail -1)"
CLASS_PROBE="$(printf '%s\n' "$OUT_PROBE" | sed -n 's/^\[\(PASS\|SKIP\|FAIL\|TIMEOUT\)\] \(FM-SOC-[0-9A-Z-]*\).*/\1 \2/p')"
probe_class=$(printf '%s\n' "$CLASS_PROBE" | awk '{print $1}' | head -1); probe_class=${probe_class:-UNSEEN}
tprobe=$(printf '%s\n' "$STATS_PROBE" | sed -n 's/.* TIMEOUT=\([0-9]*\).*/\1/p'); tprobe=${tprobe:-0}
echo "  probe classification: $probe_class"
echo "  probe STATS:          $STATS_PROBE"
if [ "$tprobe" -ge 1 ] || printf '%s\n' "$CLASS_PROBE" | grep -q '^TIMEOUT '; then
    apass 4 "simulator exit 124 surfaced as TIMEOUT (not swallowed by || true)"
else
    afail 4 "fake simulator exited 124 but exit code was swallowed by || true: classified '$probe_class', TIMEOUT=$tprobe (expected TIMEOUT)"
fi

# A0: manifest integrity — 33 rows with a 25/6/2 split and correct statuses.
EX=$(awk -F, 'NR>1 && $2=="EXECUTED"{n++} END{print n+0}' "$MANIFEST")
SU=$(awk -F, 'NR>1 && $2=="SUPERSEDED"{n++} END{print n+0}' "$MANIFEST")
NA=$(awk -F, 'NR>1 && $2=="N/A"{n++} END{print n+0}' "$MANIFEST")
MROWS=$(awk -F, 'NR>1 && $1!=""{n++} END{print n+0}' "$MANIFEST")
echo "[MANIFEST] rows=$MROWS EXECUTED=$EX SUPERSEDED=$SU N/A=$NA"
M_OK=1
[ "$MROWS" -eq 33 ] || { afail 0 "manifest row count $MROWS != 33"; M_OK=0; }
[ "$EX" -eq 25 ] || { afail 0 "manifest EXECUTED count $EX != 25"; M_OK=0; }
[ "$SU" -eq 6 ]  || { afail 0 "manifest SUPERSEDED count $SU != 6"; M_OK=0; }
[ "$NA" -eq 2 ]  || { afail 0 "manifest N/A count $NA != 2"; M_OK=0; }
for c in $SUPERSEDED_CASES; do
    awk -F, -v c="$c" '$1==c && $2=="SUPERSEDED"{f=1} END{exit !f}' "$MANIFEST" \
        || { afail 0 "manifest row for $c missing or wrong status"; M_OK=0; }
done
for c in $NA_CASES; do
    awk -F, -v c="$c" '$1==c && $2=="N/A"{f=1} END{exit !f}' "$MANIFEST" \
        || { afail 0 "manifest row for $c missing or wrong status"; M_OK=0; }
done
[ "$M_OK" -eq 1 ] && apass 0 "manifest OK: 33 rows, 25 EXECUTED / 6 SUPERSEDED / 2 N/A"

# ---------------------------------------------------------------------------
# 6. Verdict.
# ---------------------------------------------------------------------------
echo ""
if [ "$FAILED_ASSERTS" -ne 0 ]; then
    echo "OVERALL: RED - $FAILED_ASSERTS assertion(s) failed (expected pre-todo-9)"
    exit 1
fi
echo "OVERALL: GREEN - all assertions passed"
exit 0
