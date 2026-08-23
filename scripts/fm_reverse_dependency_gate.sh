#!/usr/bin/env bash
# =============================================================================
# fm_reverse_dependency_gate.sh — todo 11: reverse-dependency regression gate
# Detects changes to the RTL/firmware/sim-bridge surface the Func Model depends
# on since the last green gate run; if any, re-runs (all must pass, in order):
#   1. env PYTHONPATH=sim python -m pytest sim/tests/ -q \
#      --continue-on-collection-errors — 0 NEW failures/errors vs the recorded
#      vs the recorded baseline (legacy, env-dependent failures tolerated).
#      Baseline = "pytest" summary in .omo/last_fm_gate.json; first run
#      bootstraps from the task-3 legacy baseline (164 failed / 45 errors;
#      overridable: FM_GATE_BASE_FAILED / FM_GATE_BASE_ERRORS).
#   2. env PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py::test_mmul_scale_nonzero \
#      sim/tests/test_soc_fm.py::test_mmul_accumulate -v            (todo 6/7)
#   3. W4-PERF 6 batches on sz0001 via sim/regression/run_w4_perf_batch.sh
#      (existing entry point; batch logic never re-implemented here), driven
#      through scripts/p10_lib/p10_sz0001.sh (p10_ssh) when available.
#   4. Write .omo/last_fm_gate.json { head, hashes, pytest baseline, timestamp }.
#      On any failure: exit 1, state untouched.
# Usage: ./scripts/fm_reverse_dependency_gate.sh [--dry-run]
#        dry-run: exit 0 = "gate: clean"; exit 1 = "gate: triggered" + plan.
# =============================================================================
set -euo pipefail
trap 'echo "[fm_gate] FAILED at line $LINENO (rc=$?)" >&2' ERR

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$ROOT/.omo/last_fm_gate.json"
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
  esac
done
# Sensitive surface = Phase 10's actual RTL change surface + the firmware/
# ABI/sim-bridge files the Func Model consumes (todo 11 spec).
SENSITIVE_PATTERNS=(
  "rtl/mxu/*.v" "rtl/soc/*.v" "rtl/sfu/*.v" "rtl/vector/*.v" "rtl/wrapper/*.v" "rtl/ip/*.v"
  "firmware/npu_firmware.c" "firmware/npu-regmap.h" "gen/npu_abi_firmware.h"
  "sim/golden_executor.py" "sim/mmio_bridge.py" "sim/perf_tests.py"
  "sim/cocotb_bridge.py" "sim/tile_scheduler.py" "sim/func_model.py"
)
BASE_FAILED="${FM_GATE_BASE_FAILED:-164}"   # task-3 legacy baseline (bootstrap only)
BASE_ERRORS="${FM_GATE_BASE_ERRORS:-45}"
log() { echo "[fm_gate] $*"; }
current_files() {
  local pat f
  shopt -s nullglob
  for pat in "${SENSITIVE_PATTERNS[@]}"; do
    for f in "$ROOT"/$pat; do printf '%s\n' "${f#"$ROOT"/}"; done
  done
  shopt -u nullglob
}
hash_file() {
  git -C "$ROOT" hash-object "$ROOT/$1" 2>/dev/null || sha256sum "$ROOT/$1" | awk '{print $1}'
}
load_state() {
  [ -f "$STATE" ] || return 0
  python3 - "$STATE" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(d.get("head", ""))
for p in sorted(d.get("hashes", {})):
    print(p, d["hashes"][p])
PYEOF
}
compute_changed() {
  local f line
  CUR_FILES=()
  declare -gA CUR_HASH
  local -A OLD_HASH
  mapfile -t CUR_FILES < <(current_files | sort -u)
  CHANGED=()
  for f in "${CUR_FILES[@]}"; do CUR_HASH[$f]="$(hash_file "$f")"; done
  while IFS= read -r line; do
    OLD_HASH["${line%% *}"]="${line#* }"
  done < <(load_state | tail -n +2)
  for f in "${CUR_FILES[@]}"; do
    if [ -z "${OLD_HASH[$f]+x}" ] || [ "${OLD_HASH[$f]}" != "${CUR_HASH[$f]}" ]; then
      CHANGED+=("$f")
    fi
  done
  for f in "${!OLD_HASH[@]}"; do
    [ -n "${CUR_HASH[$f]+x}" ] || CHANGED+=("$f [deleted]")
  done
}
# ── W4-PERF invocation (reuse the existing 6-batch entry point) ────────────
P10_SSH_AVAIL=0
if [ -f "$ROOT/scripts/p10_lib/p10_sz0001.sh" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/scripts/p10_lib/p10_sz0001.sh"
  P10_SSH_AVAIL=1
fi
ON_SZ0001=0
[ "$(hostname -s 2>/dev/null || hostname)" = "sz0001" ] && ON_SZ0001=1

w4_run() {
  if [ "$P10_SSH_AVAIL" = "1" ] && [ "$ON_SZ0001" != "1" ]; then
    p10_ssh "bash sim/regression/run_w4_perf_batch.sh"
  else
    bash "$ROOT/sim/regression/run_w4_perf_batch.sh"
  fi
}
w4_plan() {
  if [ "$P10_SSH_AVAIL" = "1" ] && [ "$ON_SZ0001" != "1" ]; then
    echo "3. [sz0001 via p10_ssh ${ZHENGS}@${SZ0001}] bash sim/regression/run_w4_perf_batch.sh"
  elif [ "$P10_SSH_AVAIL" = "1" ]; then
    echo "3. [sz0001, local] bash sim/regression/run_w4_perf_batch.sh"
  else
    echo "3. [host] bash sim/regression/run_w4_perf_batch.sh  # NOTE: p10_lib helper missing; requires sz0001 EDA env (source sim/regression/run_env.sh)"
  fi
}
# ── main ───────────────────────────────────────────────────────────────────
ACT1="env PYTHONPATH=sim python -m pytest sim/tests/ -q --continue-on-collection-errors"
ACT2="env PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py::test_mmul_scale_nonzero sim/tests/test_soc_fm.py::test_mmul_accumulate -v"
compute_changed
if [ ${#CHANGED[@]} -eq 0 ] && [ -f "$STATE" ]; then
  echo "gate: clean"
  exit 0
fi
if [ "$DRY_RUN" = "1" ]; then
  echo "gate: triggered"
  [ -f "$STATE" ] || echo "  (no recorded gate state yet — bootstrap: all stages will run)"
  [ ${#CHANGED[@]} -gt 0 ] && printf '  changed: %s\n' "${CHANGED[@]}"
  echo "  planned:"
  echo "    1. cd '$ROOT' && $ACT1"
  echo "    2. cd '$ROOT' && $ACT2"
  w4_plan | sed 's/^/    /'
  echo "    4. write $STATE (head=$(git -C "$ROOT" rev-parse --short HEAD), ${#CUR_FILES[@]} files)"
  exit 1
fi
echo "gate: triggered ($(printf '%s' "${#CHANGED[@]}") changed file(s))"
[ ${#CHANGED[@]} -gt 0 ] && printf '  changed: %s\n' "${CHANGED[@]}"
# Stage 1 — full sim/tests; 0 NEW failures/errors vs baseline.
LOG1="$(mktemp /tmp/fm_gate_p1.XXXXXX)"
if ( cd "$ROOT" && $ACT1 ) > "$LOG1" 2>&1; then P1_RC=0; else P1_RC=$?; fi
count_of() { local n; n=$(grep -oE "[0-9]+ $1" "$LOG1" 2>/dev/null | tail -1 | awk '{print $1}'); echo "${n:-0}"; }
P1_FAILED=$(count_of failed)
P1_ERRORS=$(count_of errors)
P1_PASSED=$(count_of passed)
B_F="$BASE_FAILED"; B_E="$BASE_ERRORS"
if [ -f "$STATE" ]; then
  read -r B_F B_E < <(python3 - "$STATE" 2>/dev/null <<'PYEOF'
import json, sys
p = json.load(open(sys.argv[1], encoding="utf-8")).get("pytest") or {}
print(p.get("failed", 0), p.get("errors", 0))
PYEOF
) || true
fi
if [ "$P1_FAILED" -gt "$B_F" ] || [ "$P1_ERRORS" -gt "$B_E" ]; then
  log "stage 1 FAIL: ${P1_FAILED} failed / ${P1_ERRORS} errors vs baseline ${B_F} failed / ${B_E} errors"
  tail -40 "$LOG1" || true
  rm -f "$LOG1"
  exit 1
fi
if [ "$P1_PASSED" -eq 0 ]; then
  log "stage 1 FAIL: pytest collected no runnable tests (rc=$P1_RC)"; tail -20 "$LOG1" || true; rm -f "$LOG1"; exit 1
fi
log "stage 1 PASS: ${P1_FAILED} failed / ${P1_PASSED} passed / ${P1_ERRORS} errors (baseline ${B_F}/${B_E})"
# Stage 2 — todo 6/7 scale + accumulate FM regressions (must be green).
LOG2="$(mktemp /tmp/fm_gate_p2.XXXXXX)"
( cd "$ROOT" && $ACT2 ) > "$LOG2" 2>&1 || {
  log "stage 2 FAIL"; tail -30 "$LOG2" || true; rm -f "$LOG1" "$LOG2"; exit 1
}
log "stage 2 PASS (scale + accumulate regressions)"
# Stage 3 — W4-PERF 6 batches on sz0001.
LOG3="$(mktemp /tmp/fm_gate_p3.XXXXXX)"
w4_run > "$LOG3" 2>&1 || {
  log "stage 3 FAIL (W4-PERF)"; tail -30 "$LOG3" || true; rm -f "$LOG1" "$LOG2" "$LOG3"; exit 1
}
log "stage 3 PASS (W4-PERF 6 batches)"
# Stage 4 — record state (atomic write; only reached when all stages passed).
HEAD="$(git -C "$ROOT" rev-parse HEAD)"
HEAD_SHORT="$(git -C "$ROOT" rev-parse --short HEAD)"
mkdir -p "$ROOT/.omo"
TMP="$STATE.tmp.$$"; HASH_TMP="$(mktemp /tmp/fm_gate_hashes.XXXXXX)"
for f in "${CUR_FILES[@]}"; do printf '%s %s\n' "$f" "${CUR_HASH[$f]}"; done > "$HASH_TMP"
FM_GATE_HEAD="$HEAD" FM_GATE_HASHES="$HASH_TMP" FM_GATE_F="$P1_FAILED" FM_GATE_P="$P1_PASSED" FM_GATE_E="$P1_ERRORS" python3 - "$TMP" <<'PYEOF'
import datetime, json, os, sys
hashes = {}
for line in open(os.environ["FM_GATE_HASHES"], encoding="utf-8"):
    line = line.strip()
    if line:
        p, h = line.split(None, 1)
        hashes[p] = h
d = {
    "head": os.environ["FM_GATE_HEAD"],
    "hashes": hashes,
    "pytest": {
        "failed": int(os.environ["FM_GATE_F"]),
        "passed": int(os.environ["FM_GATE_P"]),
        "errors": int(os.environ["FM_GATE_E"]),
    },
    "timestamp": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
json.dump(d, open(sys.argv[1], "w", encoding="utf-8"), indent=2, sort_keys=True)
PYEOF
mv "$TMP" "$STATE"
rm -f "$LOG1" "$LOG2" "$LOG3" "$HASH_TMP"
log "gate: PASS — state recorded: $STATE (head=$HEAD_SHORT)"
exit 0
