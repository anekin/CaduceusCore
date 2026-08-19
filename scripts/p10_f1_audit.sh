#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

#─────────────────────────────────────────────────────────────────────
# F1: Phase 10 Final Wave — Plan Compliance Audit
#
# Checks (per .omo/plans/phase10-rtl-verification.md Final Wave F1):
#   1. Evidence files build/evidence/task-{1..22}-phase10-rtl-verification.txt
#      all exist. Exception: todo 5's plan declares inline evidence
#      ("无需额外 evidence 文件" — commit message + task-4 evidence notes),
#      so todo 5 is verified via git log + task-4 notes instead of a file.
#   2. Each todo's terminal state matches the acceptance mapping:
#        todo 10: PASS | WAIVED
#        todo 21: PASS | BLOCKED-NETWORK
#        todos 1-9, 11-20, 22: PASS only
#   3. Todo 12 evidence asserts engine=spike; todo 13 evidence asserts
#      engine=ibex (when the evidence file exists).
#   4. git log corresponds to the plan: each todo's planned commit message
#      (or a commit referenced by its evidence) exists in history.
#
# Exit 0 when all checks pass, exit 1 otherwise.
# Report: build/evidence/task-F1-phase10-rtl-verification.txt
#─────────────────────────────────────────────────────────────────────
PLAN="${REPO_ROOT}/.omo/plans/phase10-rtl-verification.md"
EVDIR="${REPO_ROOT}/build/evidence"
REPORT="${EVDIR}/task-F1-phase10-rtl-verification.txt"
INLINE_TODO=5

mkdir -p "${EVDIR}"

# Terminal-state acceptance mapping (per todo)
declare -A ACCEPT
for n in $(seq 1 22); do ACCEPT[$n]="PASS"; done
ACCEPT[10]="PASS|WAIVED"
ACCEPT[21]="PASS|BLOCKED-NETWORK"

GIT_SUBJECTS="$(cd "${REPO_ROOT}" && git log --no-merges --format=%s -150 2>/dev/null)"
GIT_HASHES="$(cd "${REPO_ROOT}" && git log --no-merges --format=%H -200 2>/dev/null)"
HEAD_COMMIT="$(cd "${REPO_ROOT}" && git rev-parse --short HEAD 2>/dev/null)"

pass_count=0
fail_count=0
declare -a LINES

#─────────────────────────────────────────────────────────────────────
# helpers
#─────────────────────────────────────────────────────────────────────

# Extract the planned commit message of todo N from the plan file.
plan_commit_msg() {
  awk -v n="$1" '
    $0 ~ ("^- \\[[ x]\\] " n "\\.") {found=1}
    found && /^  Commit:/ {sub(/^  Commit: [A-Z] \| /, ""); print; exit}
  ' "${PLAN}"
}

# Generic terminal PASS / FAIL indicator families shared across todos.
has_pass() {
  grep -qE '^[[:space:]]*(Result|OVERALL|Overall): PASS[[:space:]]*$|^[[:space:]]*Overall status: PASS[[:space:]]*$|^[[:space:]]*Verification: PASS[[:space:]]*$|^[[:space:]]*LADDER=PASS[[:space:]]*$|DOCS-SYNC=PASS|ROOT_CAUSE_FIXED=YES|^[[:space:]]*testcase-list: 21/21 PASS' "$1"
}
has_fail() {
  grep -qE '^[[:space:]]*(Result|OVERALL|Overall|Verification): FAIL[[:space:]]*$|^[[:space:]]*LADDER=FAIL[[:space:]]*$|DOCS-SYNC=FAIL|^[[:space:]]*ROOT_CAUSE_FIXED=NO' "$1"
}

# Parse the terminal state out of a todo's evidence file.
terminal_state() {
  local n="$1" f="$2"
  case "$n" in
    4)  grep -qE '^ROOT_CAUSE=' "$f" && { echo PASS; return 0; } ;;
    7)  grep -qE '^ROOT_CAUSE=(FIRMWARE|RTL)' "$f" && { echo PASS; return 0; } ;;
    10) grep -qE 'FM-SOC-001: PASS' "$f" && { echo PASS; return 0; }
        grep -q 'WAIVED' "$f" && { echo WAIVED; return 0; } ;;
    21) grep -q 'BLOCKED-NETWORK' "$f" && { echo BLOCKED-NETWORK; return 0; }
        grep -q 'DOWNLOAD=SUCCESS' "$f" && { echo PASS; return 0; } ;;
    18) if grep -q 'Wrapper PASS count: 5' "$f" \
          && grep -q 'Wrapper FAIL count: 0' "$f" \
          && grep -q 'SFU: 319/319 PASS' "$f"; then echo PASS; return 0; fi ;;
    20) grep -q 'DOCS-SYNC=PASS' "$f" && { echo PASS; return 0; } ;;
  esac
  if has_pass "$f"; then echo PASS; return 0; fi
  if has_fail "$f"; then echo FAIL; return 0; fi
  echo UNKNOWN
  return 1
}

# Check a todo's git-log correspondence. $1 = planned commit message,
# $2 = evidence file (may be empty/missing). Prints the match method used.
todo_commit_ok() {
  local msg="$1" evfile="$2"
  # Normalize: drop the <scope> placeholder and the " or type(scope):"
  # prefix alternation.
  local norm="${msg//<scope>/}"
  norm="$(printf '%s' "$norm" | sed -E 's/ or [a-z]+\([^)]*\):/:/')"
  local pat
  pat="$(printf '%s' "$norm" | sed 's/[][\.^$*+?(){}|]/\\&/g')"
  if grep -qE "$pat" <<< "$GIT_SUBJECTS"; then echo "ok(plan-msg)"; return 0; fi
  # Try the description alternatives after the colon ("resolve or waive X").
  local desc="${norm#*:}"
  if [ "$desc" != "$norm" ]; then
    while IFS= read -r alt; do
      [ -z "$alt" ] && continue
      local ap
      ap="$(printf '%s' "$alt" | sed 's/[][\.^$*+?(){}|]/\\&/g')"
      if [ "${#ap}" -ge 12 ] && grep -qE "$ap" <<< "$GIT_SUBJECTS"; then
        echo "ok(plan-desc)"; return 0
      fi
    done < <(printf '%s' "$desc" | sed 's/ or /\n/g')
  fi
  # Fallback: any commit hash referenced by the evidence exists in history.
  if [ -n "$evfile" ] && [ -f "$evfile" ]; then
    local h
    while read -r h; do
      [ -z "$h" ] && continue
      if grep -qE "^${h}" <<< "$GIT_HASHES"; then echo "ok(evidence-commit)"; return 0; fi
    done < <(grep -oiE 'commit[[:space:]]*[:#]?[[:space:]]*[0-9a-f]{7,40}' "$evfile" \
             | grep -oE '[0-9a-f]{7,40}' | sort -u)
  fi
  return 1
}

#─────────────────────────────────────────────────────────────────────
# per-todo audit
#─────────────────────────────────────────────────────────────────────
for n in $(seq 1 22); do
  ev="${EVDIR}/task-${n}-phase10-rtl-verification.txt"
  ok=1
  state=""
  evtag="present"
  engine="n/a"
  gitlog=""
  reasons=""

  # 1) evidence presence + terminal state
  if [ "$n" -eq "${INLINE_TODO}" ]; then
    evtag="inline"
    t4="${EVDIR}/task-4-phase10-rtl-verification.txt"
    if [ -f "$t4" ] && grep -q 'commit 7aec7a3' "$t4"; then
      state="PASS"
    else
      state="MISSING"
      ok=0
      reasons="inline-evidence-missing(task-4-notes/commit-ref)"
    fi
  elif [ -f "$ev" ]; then
    if ! state="$(terminal_state "$n" "$ev")"; then state="UNKNOWN"; fi
    if [ "$state" = "UNKNOWN" ]; then ok=0; reasons="no-terminal-state-indicator"; fi
  else
    evtag="MISSING"
    state="MISSING"
    ok=0
    reasons="missing-evidence"
  fi

  # 2) terminal-state acceptance mapping
  if [ "$ok" -eq 1 ] && [ "$state" != "MISSING" ]; then
    accept="${ACCEPT[$n]}"
    if grep -qE "(^|\\|)${state}(\\||$)" <<< "$accept"; then
      :
    else
      ok=0
      reasons="${reasons} state=${state}-not-accepted(accept=${accept})"
    fi
  fi

  # 3) engine assertions (todo 12 spike / todo 13 ibex, when evidence exists)
  if [ "$n" -eq 12 ] && [ -f "$ev" ]; then
    if grep -qE '^engine=spike$' "$ev"; then engine="spike(ok)"; else
      ok=0; engine="spike(MISSING)"; reasons="${reasons} engine!=spike"
    fi
  elif [ "$n" -eq 13 ] && [ -f "$ev" ]; then
    if grep -qE '^engine=ibex$' "$ev"; then engine="ibex(ok)"; else
      ok=0; engine="ibex(MISSING)"; reasons="${reasons} engine!=ibex"
    fi
  fi

  # 4) git log correspondence with the plan
  gmsg="$(plan_commit_msg "$n")"
  if [ -n "$gmsg" ]; then
    if gitlog="$(todo_commit_ok "$gmsg" "$ev")"; then
      :
    else
      gitlog="no-match"
      ok=0
      reasons="${reasons} gitlog-no-correspondence"
    fi
  else
    gitlog="no-plan-commit-line"
    ok=0
    reasons="${reasons} no-commit-line-in-plan"
  fi

  if [ "$ok" -eq 1 ]; then
    pass_count=$((pass_count + 1))
    verdict="PASS"
  else
    fail_count=$((fail_count + 1))
    verdict="FAIL"
  fi
  reasons="${reasons# }"
  LINES+=("todo=$(printf '%02d' "$n") evidence=${evtag} state=${state} accept=${ACCEPT[$n]} engine=${engine} gitlog=${gitlog} verdict=${verdict}${reasons:+ | ${reasons}}")
done

#─────────────────────────────────────────────────────────────────────
# report
#─────────────────────────────────────────────────────────────────────
{
  echo "# Phase 10 F1 — Plan Compliance Audit"
  echo "# Generated: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "# Plan: ${PLAN}"
  echo "# Script: scripts/p10_f1_audit.sh"
  echo "# HEAD commit at audit time: ${HEAD_COMMIT}"
  echo ""
  echo "## Terminal-state acceptance mapping"
  echo "todo=10 accept=PASS|WAIVED"
  echo "todo=21 accept=PASS|BLOCKED-NETWORK"
  echo "todos=1-9,11-20,22 accept=PASS"
  echo "todo=5 evidence=inline (plan declares no evidence file; commit message + task-4 notes)"
  echo ""
  echo "## Per-todo status"
  for l in "${LINES[@]}"; do echo "$l"; done
  echo ""
  echo "## Summary"
  echo "pass_count=${pass_count}"
  echo "fail_count=${fail_count}"
  if [ "${fail_count}" -eq 0 ]; then
    echo "F1-AUDIT: PASS"
  else
    echo "F1-AUDIT: FAIL"
  fi
} > "${REPORT}"

echo "F1 audit complete. Report: ${REPORT}"
for l in "${LINES[@]}"; do echo "$l"; done
echo "Pass: ${pass_count}  Fail: ${fail_count}"
if [ "${fail_count}" -eq 0 ]; then
  exit 0
else
  exit 1
fi
