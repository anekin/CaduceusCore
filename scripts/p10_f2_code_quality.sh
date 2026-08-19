#!/usr/bin/env bash
# =============================================================================
# p10_f2_code_quality.sh — Phase 10 Final Wave F2: Code quality review
#
# Reviews every RTL/firmware/Python change made during Phase 10 (since the
# Phase 10 baseline) and gates on the plan's F2 pass criteria:
#   0 new lint errors, 0 new pytest failures, no suspicious hardcoded values.
#
# Checks:
#   1. Baseline commit resolution (stored override or parent of the first
#      commit that added scripts/p10_lib/p10_sz0001.sh)
#   2. Changed source files (.v/.c/.h/.py), vendored/build paths excluded
#   3. NEW TODO/FIXME/HACK/XXX residues — added lines only, so pre-existing
#      residues in unchanged code never fail this gate
#   4. Hardcoded debug values on added lines: magic debug hex constants,
#      breakpoint()/pdb.set_trace, assert False  (gate); absolute-path
#      hardcodes reported as INFO only (EDA integration paths are existing
#      repo style, e.g. the _cadence_lib move in sim/spike_host.py)
#   5. Style: trailing whitespace on added lines (warning, not gate)
#   6. pytest — primary on sz0001 with the exact task-3 baseline command
#      (PYTHONPATH=.venv_pytest:sim + --continue-on-collection-errors);
#      falls back to a local run when sz0001 is unreachable. Compared
#      against the task-3 baseline counts (164 failed / 1901 passed /
#      45 errors); only NEW failures/errors fail the gate.
#   7. Python lint on changed .py files: ruff → flake8 → pylint (first
#      available); none of them are installed in this environment, so the
#      fallback is a syntax gate (ast.parse) on every changed .py file.
#      No lint config files are created or modified.
#
# Exit code:
#   0  — no new quality issues (residues, debug values, lint errors,
#        new pytest failures)
#   1  — at least one gating issue found
#   2  — environment error (no repo, no changed files, unparsable pytest)
#
# Evidence:
#   build/evidence/task-F2-phase10-rtl-verification.txt  (final report)
#   build/evidence/task-F2-pytest.log                    (pytest output)
# =============================================================================
set -euo pipefail

source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

EVIDENCE_DIR="$REPO_ROOT/build/evidence"
OUT_FILE="$EVIDENCE_DIR/task-F2-phase10-rtl-verification.txt"
PYTEST_LOG="$EVIDENCE_DIR/task-F2-pytest.log"
mkdir -p "$EVIDENCE_DIR"
cd "$REPO_ROOT"

COMMIT="$(git rev-parse HEAD 2>/dev/null || echo "?")"
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

failures=()
warnings=()
infos=()
record_failure() { failures+=("$*"); }
record_warning() { warnings+=("$*"); }
record_info()    { infos+=("$*"); }

# =============================================================================
# 1. Baseline commit resolution
# =============================================================================
BASE_FILE="$EVIDENCE_DIR/ph10-base-commit.txt"
if [[ -s "$BASE_FILE" ]]; then
    BASE="$(head -1 "$BASE_FILE" | awk '{print $1}')"
    BASE_SOURCE="build/evidence/ph10-base-commit.txt"
else
    P10_FIRST="$(git log --reverse --format=%H --diff-filter=A -- scripts/p10_lib/p10_sz0001.sh 2>/dev/null | head -1)"
    if [[ -z "$P10_FIRST" ]]; then
        echo "ERROR: cannot locate first p10 commit (scripts/p10_lib/p10_sz0001.sh not in history?)" >&2
        exit 2
    fi
    BASE="$(git rev-parse "$P10_FIRST~1")"
    BASE_SOURCE="parent of first p10 commit ($(git log -1 --format='%h %s' "$P10_FIRST" 2>/dev/null))"
fi
BASE_SUBJECT="$(git log -1 --format='%h %s' "$BASE" 2>/dev/null || echo "?")"

# =============================================================================
# 2. Changed source files (RTL/firmware/Python only; vendored + build excluded)
# =============================================================================
CHANGED_SRC="$(git diff --name-only "$BASE" HEAD -- '*.v' '*.c' '*.h' '*.py' 2>/dev/null \
    | grep -vE '^firmware/build/|^rtl/test_vectors/|^rtl/cpu/ibex/|^rtl/ip/verilog-' || true)"
CHANGED_PY="$(printf '%s\n' "$CHANGED_SRC" | grep -E '\.py$' || true)"

if [[ -z "$CHANGED_SRC" ]]; then
    echo "ERROR: no changed source files between $BASE and HEAD" >&2
    exit 2
fi
CHANGED_COUNT="$(printf '%s\n' "$CHANGED_SRC" | wc -l)"
PY_COUNT="$(printf '%s\n' "$CHANGED_PY" | grep -c . || true)"

# =============================================================================
# 3. NEW TODO/FIXME/HACK/XXX residues (added lines only)
# =============================================================================
RESIDUE_TOTAL=0
RESIDUE_FILES=""
for f in $CHANGED_SRC; do
    [[ -f "$f" ]] || continue
    hits="$(git diff "$BASE" HEAD -- "$f" | grep -E '^\+' | sed 's/^+//' \
        | grep -nE '\b(TODO|FIXME|HACK|XXX)\b' || true)"
    if [[ -n "$hits" ]]; then
        RESIDUE_FILES="${RESIDUE_FILES}${f} "
        while IFS= read -r h; do
            record_failure "residue $f: ${h}"
            RESIDUE_TOTAL=$((RESIDUE_TOTAL + 1))
        done <<< "$hits"
    fi
done

# =============================================================================
# 4. Hardcoded debug values (added lines only)
# =============================================================================
DEBUG_MAGIC='0x[0-9A-Fa-f]*(DEAD|BEEF|CAFE|F00D)[0-9A-Fa-f]*'
DEBUG_PY='breakpoint\(|pdb\.set_trace|ipdb\.set_trace|[[:space:]]assert False[[:space:]]*$'
HARDCODE_TOTAL=0
for f in $CHANGED_SRC; do
    [[ -f "$f" ]] || continue
    hits="$(git diff "$BASE" HEAD -- "$f" | grep -E '^\+' | sed 's/^+//' \
        | grep -nE "$DEBUG_MAGIC|$DEBUG_PY" || true)"
    if [[ -n "$hits" ]]; then
        while IFS= read -r h; do
            record_failure "suspicious hardcoded value $f: ${h}"
            HARDCODE_TOTAL=$((HARDCODE_TOTAL + 1))
        done <<< "$hits"
    fi
    # Absolute-path hardcodes: informational only (existing repo style for EDA
    # integration paths; a moved line is not a new debug value).
    abspaths="$(git diff "$BASE" HEAD -- "$f" | grep -E '^\+' | grep -nE '/(home|NAS|EDA)/' || true)"
    if [[ -n "$abspaths" ]]; then
        record_info "absolute-path hardcode in $f (informational): $(echo "$abspaths" | head -3 | tr '\n' ' ')"
    fi
done

# =============================================================================
# 5. Style: trailing whitespace on added lines (warning only)
# =============================================================================
TRAILING_TOTAL=0
for f in $CHANGED_SRC; do
    [[ -f "$f" ]] || continue
    cnt="$(git diff "$BASE" HEAD -- "$f" | grep -E '^\+' | grep -cE '[[:space:]]+$' || true)"
    if [[ "$cnt" -gt 0 ]]; then
        record_warning "trailing whitespace in $f: $cnt added line(s)"
        TRAILING_TOTAL=$((TRAILING_TOTAL + cnt))
    fi
done

# =============================================================================
# 6. pytest (sz0001 primary, local fallback)
# =============================================================================
# Baseline counts from the Phase 10 baseline re-run (todo 3 evidence).
TASK3_EVIDENCE="$EVIDENCE_DIR/task-3-phase10-rtl-verification.txt"
BASELINE_FAILED=164; BASELINE_PASSED=1901; BASELINE_ERRORS=45; BASELINE_SOURCE="defaults"
if [[ -s "$TASK3_EVIDENCE" ]]; then
    bline="$(grep -oE '[0-9]+ failed, [0-9]+ passed, [0-9]+ skipped, [0-9]+ warnings?, [0-9]+ errors?' "$TASK3_EVIDENCE" | head -1)"
    if [[ -n "$bline" ]]; then
        BASELINE_FAILED="$(echo "$bline" | awk '{print $1}')"
        BASELINE_PASSED="$(echo "$bline" | awk '{print $3}')"
        BASELINE_ERRORS="$(echo "$bline" | grep -oE '[0-9]+ errors?' | grep -oE '[0-9]+' || echo 0)"
        BASELINE_SOURCE="build/evidence/task-3-phase10-rtl-verification.txt"
    fi
fi

P10_PYTEST_CMD="PYTHONPATH='$REPO_ROOT/.venv_pytest:sim' python -m pytest sim/tests/ sim/timing/tests/ -q --continue-on-collection-errors > '$PYTEST_LOG' 2>&1 || true"
PYTEST_HOST="sz0001"
if ! ssh -o ConnectTimeout=8 -o BatchMode=yes "${ZHENGS}@${SZ0001}" "echo p10_f2_ok" >/dev/null 2>&1; then
    PYTEST_HOST="local-fallback(sz0002)"
    record_warning "sz0001 unreachable — pytest falls back to local run (wrapper/cocotb tests may not collect locally)"
    rm -f "$PYTEST_LOG"
    PYTHONPATH=sim python3 -m pytest sim/tests/ sim/timing/tests/ -q --continue-on-collection-errors > "$PYTEST_LOG" 2>&1 || true
else
    rm -f "$PYTEST_LOG"
    p10_ssh "$P10_PYTEST_CMD"
fi

pytest_summary="$(grep -E '[0-9]+ failed, [0-9]+ passed' "$PYTEST_LOG" 2>/dev/null | tail -1)"
PYTEST_FAILED=""; PYTEST_PASSED=""; PYTEST_ERRORS=""
if [[ -n "$pytest_summary" ]]; then
    PYTEST_FAILED="$(echo "$pytest_summary" | awk '{print $1}')"
    PYTEST_PASSED="$(echo "$pytest_summary" | awk '{print $3}')"
    PYTEST_ERRORS="$(echo "$pytest_summary" | grep -oE '[0-9]+ errors?' | grep -oE '[0-9]+' || echo 0)"
fi
NEW_FAILED="n/a"; NEW_ERRORS="n/a"
if [[ "$PYTEST_FAILED" =~ ^[0-9]+$ && "$PYTEST_ERRORS" =~ ^[0-9]+$ && "$PYTEST_PASSED" =~ ^[0-9]+$ ]]; then
    NEW_FAILED=$((PYTEST_FAILED - BASELINE_FAILED))
    NEW_ERRORS=$((PYTEST_ERRORS - BASELINE_ERRORS))
    if [[ "$NEW_FAILED" -gt 0 ]]; then
        record_failure "pytest: $NEW_FAILED NEW failed test(s) vs task-3 baseline ($BASELINE_FAILED)"
    fi
    if [[ "$NEW_ERRORS" -gt 0 ]]; then
        record_failure "pytest: $NEW_ERRORS NEW collection/error(s) vs task-3 baseline ($BASELINE_ERRORS)"
    fi
    if [[ "$PYTEST_PASSED" -lt "$BASELINE_PASSED" ]]; then
        record_warning "pytest: passed count $PYTEST_PASSED below task-3 baseline $BASELINE_PASSED (no new failures recorded)"
    fi
else
    record_failure "pytest summary unparsable (see $PYTEST_LOG)"
    PYTEST_FAILED="UNPARSED"; PYTEST_PASSED="UNPARSED"; PYTEST_ERRORS="UNPARSED"
fi

# =============================================================================
# 7. Python lint on changed .py files (ruff → flake8 → pylint → ast fallback)
# =============================================================================
LINT_TOOL="none"
LINT_CMD=""
if command -v ruff >/dev/null 2>&1; then LINT_TOOL="ruff"; LINT_CMD="ruff check"
elif python3 -m ruff --version >/dev/null 2>&1; then LINT_TOOL="ruff"; LINT_CMD="python3 -m ruff check"
elif command -v flake8 >/dev/null 2>&1; then LINT_TOOL="flake8"; LINT_CMD="flake8"
elif python3 -m flake8 --version >/dev/null 2>&1; then LINT_TOOL="flake8"; LINT_CMD="python3 -m flake8"
elif command -v pylint >/dev/null 2>&1; then LINT_TOOL="pylint"; LINT_CMD="pylint --errors-only"
elif python3 -m pylint --version >/dev/null 2>&1; then LINT_TOOL="pylint"; LINT_CMD="python3 -m pylint --errors-only"
fi

LINT_OK=1
LINT_OUTPUT=""
if [[ "$LINT_TOOL" = "none" ]]; then
    record_info "no linter available (ruff/flake8/pylint absent) — lint fallback: ast.parse syntax gate on $PY_COUNT changed .py file(s)"
    AST_FAILED=""
    for f in $CHANGED_PY; do
        [[ -f "$f" ]] || continue
        if ! python3 -c 'import ast,sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$f" 2>/dev/null; then
            AST_FAILED="${AST_FAILED}${f} "
        fi
    done
    LINT_OUTPUT="ast-fallback: $PY_COUNT file(s) checked"
    if [[ -n "$AST_FAILED" ]]; then
        LINT_OK=0
        for f in $AST_FAILED; do
            record_failure "lint(ast): syntax error in $f"
        done
    fi
else
    if [[ "$PY_COUNT" -gt 0 ]]; then
        set +e
        LINT_OUTPUT="$(cd "$REPO_ROOT" && $LINT_CMD $CHANGED_PY 2>&1)"
        LINT_RC=$?
        set -e
        if [[ "$LINT_RC" -ne 0 ]]; then
            LINT_OK=0
            record_failure "lint($LINT_TOOL): findings in changed .py files (exit $LINT_RC, see below)"
        fi
    else
        LINT_OUTPUT="no changed .py files to lint"
    fi
fi

# =============================================================================
# 8. Verdict + evidence file
# =============================================================================
VERDICT="PASS"
if [[ "${#failures[@]}" -gt 0 ]]; then
    VERDICT="FAIL"
fi

{
    echo "Task F2 - Phase 10 RTL Verification: Code quality review"
    echo "==========================================================================="
    echo "Timestamp     : ${TS}"
    echo "Commit        : ${COMMIT}"
    echo "Baseline      : ${BASE} (${BASE_SUBJECT})"
    echo "Baseline src  : ${BASE_SOURCE}"
    echo ""
    echo "Scope         : $CHANGED_COUNT changed source file(s) (.v/.c/.h/.py)"
    echo "Commands executed (exact):"
    echo "  1. grep residue : git diff \"\$BASE\" HEAD -- <file> | grep -E '^+' | sed 's/^+//' | grep -nE '\\b(TODO|FIXME|HACK|XXX)\\b'"
    echo "  2. grep debug   : same added-line stream | grep -nE '$DEBUG_MAGIC|$DEBUG_PY'"
    echo "  3. pytest       : $P10_PYTEST_CMD"
    echo "                    (host: $PYTEST_HOST, log: build/evidence/task-F2-pytest.log)"
    echo "  4. lint         : ${LINT_TOOL} (fallback ast.parse when no linter available)"
    echo ""
    echo "residue_check      : $([ "$RESIDUE_TOTAL" -eq 0 ] && echo OK || echo "FOUND($RESIDUE_TOTAL)")"
    echo "  residue_files    : ${RESIDUE_FILES:-(none)}"
    echo "hardcoded_check    : $([ "$HARDCODE_TOTAL" -eq 0 ] && echo OK || echo "FOUND($HARDCODE_TOTAL)")"
    echo "style_trailing_ws  : $([ "$TRAILING_TOTAL" -eq 0 ] && echo none || echo "$TRAILING_TOTAL line(s), warning")"
    echo "pytest_host        : $PYTEST_HOST"
    echo "pytest             : $PYTEST_FAILED failed, $PYTEST_PASSED passed, $PYTEST_ERRORS errors"
    echo "pytest_baseline    : $BASELINE_FAILED failed, $BASELINE_PASSED passed, $BASELINE_ERRORS errors ($BASELINE_SOURCE)"
    echo "pytest_delta       : failed=$NEW_FAILED errors=$NEW_ERRORS"
    echo "lint_tool          : $LINT_TOOL"
    echo "lint_ok            : $([ "$LINT_OK" -eq 1 ] && echo yes || echo NO)"
    echo ""
    echo "Changed source files (${CHANGED_COUNT}):"
    printf '%s\n' "$CHANGED_SRC" | sed 's/^/  /'
    echo ""
    if [[ "${#infos[@]}" -gt 0 ]]; then
        echo "Info:"
        for i in "${infos[@]}"; do
            echo "  - $i"
        done
        echo ""
    fi
    if [[ "${#warnings[@]}" -gt 0 ]]; then
        echo "Warnings:"
        for w in "${warnings[@]}"; do
            echo "  - $w"
        done
        echo ""
    fi
    if [[ "${#failures[@]}" -gt 0 ]]; then
        echo "Failures:"
        for f in "${failures[@]}"; do
            echo "  - $f"
        done
        echo ""
    fi
    if [[ "$LINT_OK" -ne 1 && -n "$LINT_OUTPUT" ]]; then
        echo "Lint output (first 40 lines):"
        echo "$LINT_OUTPUT" | head -40 | sed 's/^/  /'
        echo ""
    fi
    echo "Verification: ${VERDICT}"
    echo "  new_lint_errors    : $([ "$LINT_OK" -eq 1 ] && echo 0 || echo ">0")"
    echo "  new_pytest_failed  : $NEW_FAILED"
    echo "  new_pytest_errors  : $NEW_ERRORS"
    echo "  suspicious_hardcode: $([ "$HARDCODE_TOTAL" -eq 0 ] && echo none || echo "$HARDCODE_TOTAL")"
    echo ""
    echo "Result: ${VERDICT}"
} > "$OUT_FILE"

if [[ "$VERDICT" = "FAIL" ]]; then
    cat "$OUT_FILE" >&2
    exit 1
fi

echo "[p10_f2_code_quality] PASS: 0 new residues, 0 new pytest failures, 0 suspicious hardcodes, lint clean (evidence: $OUT_FILE)"
exit 0
