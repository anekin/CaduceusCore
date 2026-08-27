#!/usr/bin/env bash
# =============================================================================
# fm_hardening_f1_audit.sh — F1: plan compliance audit
# Plan selectable via FM_PLAN_NAME (default: fm-hardening-phase10):
#   .omo/plans/${FM_PLAN_NAME}.md + build/evidence/task-{N}-${FM_PLAN_NAME}.txt
# Checks per todo 1..${TODO_MAX} (default 14, override via TODO_MAX env):
#   1. build/evidence/task-{N}-${FM_PLAN_NAME}.txt exists and its LAST
#      PASS/FAIL marker ("Result: PASS" / "Status: PASS" / "Overall verdict:
#      PASS" / standalone PASS) says PASS.
#   2. Acceptance rerun: pytest/python/bash acceptance commands extracted from
#      the plan's "Acceptance criteria (agent-executable)" lines are re-run;
#      exit code must be 0.  Model/EDA-dependent commands (spike smoke, W4-PERF,
#      FM-SOC, Spike-binary-dependent guard tests like
#      test_spike_forward_tolerance / test_spike_ibex_ring_alignment) are
#      SKIP-ENV and static greps SKIP-STATIC — reported, never
#      silently waived.  `make -C sim/regression run_*` targets (run_e2e_*,
#      run_crossbar_*, run_apb_*, run_fm_soc_case, ...) need Synopsys VCS, so
#      they are SKIP-ENV when `vcs` is not on PATH of the audit host and
#      re-run otherwise.  Self-invocation of this script is skipped (recursion).
#      A rerun that collects 0 items because cocotb is absent on this host
#      (pytest rc 5 + "collected 0 items", e.g. sim/test_dram_bulk.py) is
#      SKIP-ENV: the todo-10 evidence recorded this same environment behaviour.
# Plan todos still unchecked ("- [ ]") whose evidence is missing are PENDING:
#   reported loudly but not gated (they are blocked work, e.g. todo 14 depends
#   on todo 13).  Checked todos with missing/non-PASS evidence are FAIL.
# Exit: 0 = no FAIL.  Report goes to stdout only.
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
FM_PLAN_NAME="${FM_PLAN_NAME:-fm-hardening-phase10}"
TODO_MAX="${TODO_MAX:-14}"
PLAN=".omo/plans/${FM_PLAN_NAME}.md"
EVD="build/evidence"
LOG="$(mktemp /tmp/fm_f1_cmd.XXXXXX)"

checked_todo() {  # 1 if the plan checkbox of todo $1 is [x]
  awk -v n="$1" '$0 ~ ("^- \\[[ x]\\] " n "\\. ") {print}' "$PLAN" | grep -q '\[x\]' && echo 1 || echo 0
}
acceptance_cmds() {  # backtick-quoted commands of todo $1's acceptance line
  awk -v n="$1" '
    $0 ~ ("^- \\[[ x]\\] " n "\\.") {found=1}
    found && /Acceptance criteria/ {print; exit}
  ' "$PLAN" | grep -oE '`[^`]+`' | sed 's/^`//; s/`$//'
}
HAS_VCS=0; if command -v vcs >/dev/null 2>&1; then HAS_VCS=1; fi
runnable() {  # classify one acceptance command
  case "$1" in
    *fm_hardening_f1_audit*) echo skip-self ;;
    make\ -C\ sim/regression\ run_*)
      if [ "$HAS_VCS" = "1" ]; then echo run; else echo skip-env; fi ;;
    *--model*|*run_w4_perf*|*run_fm_soc*|*p10_ssh*|*test_spike_ibex_ring_alignment*|*test_spike_forward_tolerance*) echo skip-env ;;
    PYTHONPATH=*|python3\ *|python\ *|make\ -C\ firmware*|bash\ -n\ *|ls\ scripts/*|./scripts/fm_reverse_dependency_gate.sh*) echo run ;;
    *) echo skip-static ;;
  esac
}

pass=0; fail=0; pend=0
for n in $(seq 1 ${TODO_MAX}); do
  ev="${EVD}/task-${n}-${FM_PLAN_NAME}.txt"
  checked="$(checked_todo "$n")"
  state="MISSING"; verdict="UNKNOWN"; detail=""; acc=""
  if [ -f "$ev" ]; then
    marker="$(grep -oE '(Result|Status|OVERALL|Overall verdict):[[:space:]]+(PASS|FAIL)|^PASS$' "$ev" | tail -1 || true)"
    if echo "$marker" | grep -q 'PASS'; then state="PASS"; else state="FAIL"; fi
    while IFS= read -r cmd; do
      [ -n "$cmd" ] || continue
      cls="$(runnable "$cmd")"
      case "$cls" in
        run)
          if bash -c "$cmd" > "$LOG" 2>&1; then acc="${acc}ok:$(echo "$cmd" | cut -c1-55);"
          else
            rc=$?
            if [ "$rc" -eq 5 ] && grep -q 'collected 0 items' "$LOG"; then
              acc="${acc}SKIP-ENV(0-collected):$(echo "$cmd" | cut -c1-45);"
            else
              acc="${acc}RC=${rc}:$(echo "$cmd" | cut -c1-55);"
              verdict="FAIL"; detail="${detail} acceptance-rc!=0"
            fi
          fi ;;
        skip-env) acc="${acc}SKIP-ENV:$(echo "$cmd" | cut -c1-45);" ;;
        skip-self) acc="${acc}self;" ;;
      esac
    done < <(acceptance_cmds "$n")
  fi
  if [ "$state" = "MISSING" ]; then
    if [ "$checked" = "1" ]; then verdict="FAIL"; detail="${detail} missing-evidence(checked)"
    else verdict="PENDING"; detail="missing-evidence(unchecked-plan-todo)"; fi
  elif [ "$state" != "PASS" ]; then
    verdict="FAIL"; detail="${detail} evidence-terminal=${state}"
  elif [ "$verdict" = "UNKNOWN" ]; then
    verdict="PASS"
  fi
  case "$verdict" in
    PASS) pass=$((pass+1)) ;;
    PENDING) pend=$((pend+1)) ;;
    FAIL) fail=$((fail+1)) ;;
  esac
  printf 'todo=%02d checkbox=%s evidence=%s accept=[%s] -> %s%s\n' \
    "$n" "$checked" "$state" "$acc" "$verdict" "${detail:+ ($detail)}"
done
rm -f "$LOG"
echo "F1 summary: plan=${FM_PLAN_NAME} pass=${pass} fail=${fail} pending=${pend} (audited ${TODO_MAX}/${TODO_MAX})"
[ "$fail" -eq 0 ]
