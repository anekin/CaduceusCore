#!/usr/bin/env bash
# fm_hardening_f3_manual_qa.sh — F3: real manual QA (fm-hardening-phase10)
# (a) full pytest (0 new failures/errors vs recorded/task-3 baseline),
# (b) make -C firmware, (c) Spike mmul_smoke (SKIP only if spike/model absent;
# a failing smoke FAILs the gate), (d) fm_reverse_dependency_gate.sh --dry-run
# clean, (e) sz0001 via scripts/p10_lib/p10_sz0001.sh: W4-PERF batches
# (sim/regression/run_w4_perf_batch.sh, p0/p1 verified) + FM-SOC-001/003 (P0)
# and FM-SOC-032 (P4) via run_fm_soc_all.sh.  --dry-run: local stages execute,
# sz0001 stages print commands and record DEFERRED(DRY-RUN); unreachable EDA
# host => DEFERRED(no-ssh).  Exit 0 = all executed stages PASS; 1 = FAIL.
# Report: build/evidence/task-F3-fm-hardening-phase10.txt
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
source "$ROOT/scripts/p10_lib/p10_sz0001.sh"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
EVID="build/evidence"; mkdir -p "$EVID"
MODEL="$HOME/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"; SPIKE="$ROOT/spike_src/build/spike"
FAIL=0; say() { echo "[F3] $*"; }; fault() { FAIL=1; say "FAIL: $*"; }
B_F=164; B_E=45
if [ -f .omo/last_fm_gate.json ]; then
  gf="$(python3 -c 'import json;print(json.load(open(".omo/last_fm_gate.json")).get("pytest",{}).get("failed",164))' 2>/dev/null || echo 164)"
  ge="$(python3 -c 'import json;print(json.load(open(".omo/last_fm_gate.json")).get("pytest",{}).get("errors",45))' 2>/dev/null || echo 45)"
  B_F=$((gf < B_F ? gf : B_F)); B_E=$((ge < B_E ? ge : B_E))
fi
# ── (a) full pytest ────────────────────────────────────────────────────────
say "(a) full pytest..."
L="$(mktemp)"; set +e
PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q --continue-on-collection-errors > "$L" 2>&1; rc=$?
set -e
S="$(grep -E '[0-9]+ failed, [0-9]+ passed' "$L" | tail -1 || true)"
fc="$(echo "$S" | awk '{print $1}')"; ec="$(echo "$S" | grep -oE '[0-9]+ errors?' | grep -oE '[0-9]+' || echo 0)"
if ! [[ "$fc" =~ ^[0-9]+$ ]]; then A="UNPARSED(rc=$rc)"; fault "(a) pytest unparsable"; tail -12 "$L"
else A="$S"; say "(a) pytest: $S  baseline=$B_F/$B_E"
  [ "$fc" -le "$B_F" ] || fault "(a) $((fc-B_F)) NEW failures"
  [ "$ec" -le "$B_E" ] || fault "(a) $((ec-B_E)) NEW errors"
fi
rm -f "$L"
# ── (b) firmware build ─────────────────────────────────────────────────────
say "(b) make -C firmware..."
if make -C firmware > "$EVID/task-F3-firmware-build.log" 2>&1; then B="PASS"; else B="FAIL rc=$?"; fault "(b) firmware build"; fi
# ── (c) Spike smoke ────────────────────────────────────────────────────────
say "(c) Spike mmul_smoke..."
if [ ! -x "$SPIKE" ] || [ ! -f "$MODEL" ]; then
  C="SKIP-ENV"; say "(c) SKIP: spike or model missing"
else
  set +e
  PYTHONPATH=sim python3 sim/spike_host.py --mode mmul_smoke --model "$MODEL" --layers 1 --ops Q_proj \
    > "$EVID/task-F3-spike-smoke.log" 2>&1; rc=$?
  set -e
  if [ "$rc" -eq 0 ] && grep -q 'Spike Host Summary: 1 PASS, 0 FAIL' "$EVID/task-F3-spike-smoke.log"; then
    C="PASS"
  else C="FAIL rc=$rc"; fault "(c) spike smoke rc=$rc"; grep -E 'Spike Host Summary|max_diff' "$EVID/task-F3-spike-smoke.log" | tail -3 || true
  fi
fi
# ── (d) reverse-dependency gate dry-run ────────────────────────────────────
say "(d) reverse gate --dry-run..."
if ./scripts/fm_reverse_dependency_gate.sh --dry-run > "$EVID/task-F3-reverse-gate-dryrun.log" 2>&1; then
  D="PASS(clean)"
else D="FAIL rc=$?"; fault "(d) reverse gate dry-run not clean"; cat "$EVID/task-F3-reverse-gate-dryrun.log"
fi
# ── (e) sz0001 W4-PERF + FM-SOC spot checks ────────────────────────────────
say "(e) sz0001 spot checks..."
E="DEFERRED"
if ! ssh -o ConnectTimeout=8 -o BatchMode=yes "${ZHENGS}@${SZ0001}" "echo f3-ok" >/dev/null 2>&1; then
  say "(e) SKIP: sz0001 unreachable — W4-PERF/FM-SOC deferred (EDA host required)"
elif [ "$DRY" = "1" ]; then
  say "(e) DRY-RUN — would execute via p10_ssh (${ZHENGS}@${SZ0001}):"
  say "    p10_ssh \"bash sim/regression/run_w4_perf_batch.sh\""
  for c in FM-SOC-001 FM-SOC-003 FM-SOC-032; do say "    p10_ssh \"bash sim/regression/run_fm_soc_all.sh $c\""; done
else
  say "(e) W4-PERF batches (run_w4_perf_batch.sh)..."
  if p10_ssh "bash sim/regression/run_w4_perf_batch.sh" > "$EVID/task-F3-w4-perf.log" 2>&1; then
    p0="$(grep -c '"status": "PASS"' "$EVID/w4-perf-p0.txt" 2>/dev/null || echo 0)"
    p1="$(grep -c '"status": "PASS"' "$EVID/w4-perf-p1.txt" 2>/dev/null || echo 0)"
    if [ "$p0" -ge 4 ] && [ "$p1" -ge 4 ]; then E="PASS"; say "(e) W4-PERF p0/p1: PASS (p0=$p0 p1=$p1)"
    else fault "(e) W4-PERF p0/p1 evidence weak (p0=$p0 p1=$p1)"; fi
  else fault "(e) W4-PERF batch failed"; tail -8 "$EVID/task-F3-w4-perf.log" || true
  fi
  for c in FM-SOC-001 FM-SOC-003 FM-SOC-032; do
    say "(e) $c via run_fm_soc_all.sh..."
    if p10_ssh "bash sim/regression/run_fm_soc_all.sh $c" > "$EVID/task-F3-${c}.log" 2>&1 \
       && grep -q "\[PASS\] $c" "$EVID/task-F3-${c}.log"; then
      say "(e) $c: PASS"
    else fault "(e) $c FAIL"; grep -E '\[(PASS|FAIL|SKIP|SUMMARY)\]' "$EVID/task-F3-${c}.log" | tail -4 || true
    fi
  done
fi
# ── verdict + evidence ─────────────────────────────────────────────────────
VERDICT="PASS"; [ "$FAIL" -eq 0 ] || VERDICT="FAIL"
{
  echo "FM-hardening F3 — real manual QA: ${VERDICT}"
  echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)  host: $(hostname)  commit: $(git rev-parse HEAD)"
  echo "Baseline: ${B_F} failed / ${B_E} errors (min of task-3 legacy + recorded gate state)"
  echo "  (a) pytest: $A | (b) firmware: $B | (c) spike: $C"
  echo "  (d) reverse-gate dry-run: $D | (e) sz0001 W4-PERF p0/p1 + FM-SOC-001/003/032: $E (dry-run=$DRY)"
} > "$EVID/task-F3-fm-hardening-phase10.txt"
cat "$EVID/task-F3-fm-hardening-phase10.txt"
if [ "$VERDICT" = "PASS" ]; then say "F3 overall: PASS"; exit 0; else say "F3 overall: FAIL"; exit 1; fi
