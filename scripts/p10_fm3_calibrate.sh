#!/usr/bin/env bash
# =============================================================================
# p10_fm3_calibrate.sh — Phase 10 Todo 16 (Wave 4): FM-3 weight-streaming
# overlap calibration against the RTL measurement from todo 15.
#
# What this script does:
#   Stage 1  Read build/evidence/task-15-phase10-rtl-verification.txt and
#            extract the RTL-measured `overlap_ratio=X.XX`.
#   Stage 2  Run the Func Model benchmark for the same workload
#            (qwen2.5-3b, Q4_K_M INT4xINT8, decode 7-GEMM weighted average)
#            to obtain the FM-predicted `weight_streaming_overlap_ratio`.
#   Stage 3  If |RTL - FM| <= 0.05 already: verify (timing pytest) and PASS
#            with no parameter change.
#   Stage 4  Otherwise run a bounded search over the REAL calibration knobs:
#              - broadcast_sync            (sim/timing/benchmark.py L87)
#              - _accumulate register term (sim/timing/benchmark.py L88-L89)
#              - memory.bandwidth_bytes_per_cycle
#                                          (sim/config/npu_config.yaml L85,
#                                           the DMAModel bw constant used by
#                                           estimate_tile_double_buffer_overlap)
#            evaluated with the exact estimator used by the benchmark
#            (DMAModel.estimate_tile_double_buffer_overlap, 7-GEMM weighted
#            average, dims from model_specs qwen2.5-3b).
#   Stage 5  Apply the best feasible knob edit (closest to baseline wins),
#            re-run the benchmark, confirm |delta| <= 0.05, then re-run the
#            Func Model verification suite (pytest sim/timing/tests).
#   Stage 6  Write build/evidence/task-16-phase10-rtl-verification.txt with
#            rtl_overlap, fm_overlap (before/after), the acceptance token
#            |delta|<=0.05 and the updated parameter names/values.
#
# Exit codes:
#   0  calibration succeeded (|delta| <= 0.05 after verification)
#   1  calibration failed (todo-15 evidence missing/invalid, delta > 0.05
#      unreachable with real knobs, or Func Model verification regressed —
#      edits are reverted in the latter two cases)
#   3  another p10_fm3_calibrate instance is running
#
# Knob policy (todo 16):
#   - weight_streaming_overlap_ratio is DERIVED (not a stored knob) — never
#     edited directly.
#   - cross_engine_gap (sim/perf_tests.py L261) is the FM-1 SAME-ENGINE gap
#     evidence annotation (crossbar_wait=2,sram_stall=1,vcov_bubble=1), a
#     different quantity from overlap; it is reviewed but left unchanged.
#   - No invented parameters (no dma_latency_cycles, no fill_drain_overlap).
#
# Usage:
#   bash scripts/p10_fm3_calibrate.sh
#   RTL_EVIDENCE=/path/evidence.txt EVIDENCE_OUT=/path/out.txt bash \
#     scripts/p10_fm3_calibrate.sh     # test hooks (avoid writing real evidence)
#
# Evidence:
#   build/evidence/task-16-phase10-rtl-verification.txt   (final report)
#   build/evidence/task-16-phase10-calibration.log        (full run log)
# =============================================================================
set -u

source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

# The p10 lib sets `set -euo pipefail`. This runner tracks failures explicitly
# (evidence must be written even when a stage fails), so relax errexit/pipefail.
set +e
set +o pipefail

export LC_ALL=C

ROOT="$REPO_ROOT"
EVIDENCE="$ROOT/build/evidence"
RTL_EV="${RTL_EVIDENCE:-$EVIDENCE/task-15-phase10-rtl-verification.txt}"
OUT_FILE="${EVIDENCE_OUT:-$EVIDENCE/task-16-phase10-rtl-verification.txt}"
RUN_LOG="$(dirname "$OUT_FILE")/task-16-phase10-calibration.log"
mkdir -p "$EVIDENCE"

# Single-instance guard: two concurrent runners would corrupt each other's
# parameter edits and evidence. Fail fast (exit 3) if another runner is active.
LOCK_FILE="$EVIDENCE/task-16.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[p10_fm3] ABORT: another p10_fm3_calibrate instance holds $LOCK_FILE (pid $(cat "$LOCK_FILE" 2>/dev/null || echo unknown))"
  exit 3
fi
echo "$$" > "$LOCK_FILE"

# log() prints to stdout AND appends to the run log directly (no tee: GNU tee
# fully buffers file output, which would hide progress from pollers).
log() { echo "[p10_fm3] $*"; echo "[p10_fm3] $*" >> "$RUN_LOG"; }
ts()  { date -u +%Y-%m-%dT%H:%M:%SZ; }

COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo "?")"
TS_START="$(ts)"

failures=()
record_failure() { failures+=("$*"); log "FAIL: $*"; }

# Scratch dir for benchmark output / search script — always cleaned up.
SCRATCH="$(mktemp -d "$ROOT/build/fm3-calib.XXXXXX")" || SCRATCH="$(mktemp -d /tmp/fm3-calib.XXXXXX)"

# Trap: guarantee the evidence file exists even if the script is interrupted,
# and that no stale scratch dir survives.
EVIDENCE_WRITTEN=0
trap 'rm -rf "$SCRATCH"
if [ "$EVIDENCE_WRITTEN" = "0" ]; then
  {
    echo "Task 16 - Phase 10 RTL Verification: INCOMPLETE"
    echo "=============================================="
    echo "Timestamp : $(ts)"
    echo "Commit    : ${COMMIT:-?}"
    echo "Status    : interrupted before final evidence write"
    echo "Run log   : task-16-phase10-calibration.log"
  } > "${OUT_FILE}" 2>/dev/null || true
fi' EXIT

# =============================================================================
# Calibration target files (edited only when delta > 0.05 and a feasible knob
# combination exists; reverted if the re-run or verification regresses).
# =============================================================================
BENCHMARK_PY="$ROOT/sim/timing/benchmark.py"
CONFIG_YAML="$ROOT/sim/config/npu_config.yaml"
EDITED_FILES=""

# =============================================================================
# Stage 1 — RTL measurement from todo 15
# =============================================================================
RTL_RAW=""
if [ ! -f "$RTL_EV" ]; then
  record_failure "todo 15 evidence missing: $RTL_EV (todo 16 is blocked by todo 15 — run scripts/p10_fm3_measure.sh first)"
  verdict_fail=1
else
  RTL_RAW=$(grep -oE 'overlap_ratio=[0-9]+\.[0-9]+' "$RTL_EV" | head -1 | cut -d= -f2)
  if [ -z "$RTL_RAW" ]; then
    record_failure "no 'overlap_ratio=X.XX' field found in $RTL_EV"
    verdict_fail=1
  else
    RTL_OK=$(python3 -c "import sys; v=float(sys.argv[1]); sys.exit(0 if 0.0 <= v <= 1.0 else 1)" "$RTL_RAW" 2>/dev/null; echo $?)
    if [ "$RTL_OK" != "0" ]; then
      record_failure "overlap_ratio=$RTL_RAW out of range [0,1] in $RTL_EV"
      verdict_fail=1
    else
      log "Stage 1: RTL overlap_ratio=$RTL_RAW (from $RTL_EV)"
    fi
  fi
fi

# =============================================================================
# Stage 2 — dirty-tree guard: never clobber uncommitted work in the files this
# script may edit.
# =============================================================================
if [ "${verdict_fail:-0}" = "0" ]; then
  if ! git -C "$ROOT" diff --quiet -- sim/timing/benchmark.py sim/config/npu_config.yaml sim/models/dma.py sim/perf_tests.py; then
    record_failure "calibration target files have uncommitted changes (git diff non-empty); abort to avoid clobbering concurrent work"
    verdict_fail=1
  else
    log "Stage 2: calibration target files clean (git diff empty)"
  fi
fi

# =============================================================================
# Stage 3 — Func Model baseline: benchmark the same workload (qwen2.5-3b)
# =============================================================================
FM_BEFORE=""
DELTA_BEFORE=""
if [ "${verdict_fail:-0}" = "0" ]; then
  log "Stage 3: Func Model baseline benchmark start ($(ts))"
  ( cd "$ROOT" && PYTHONPATH=sim python -m sim.timing.benchmark \
      --model qwen2.5-3b --output "$SCRATCH/bench" ) > "$SCRATCH/bench.log" 2>&1
  BENCH_RC=$?
  log "Stage 3: benchmark rc=$BENCH_RC"
  if [ "$BENCH_RC" -ne 0 ]; then
    record_failure "Func Model benchmark failed (rc=$BENCH_RC) — see $SCRATCH/bench.log"
    verdict_fail=1
  else
    BENCH_JSON="$SCRATCH/bench/qwen2.5-3b.json"
    FM_BEFORE=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['weight_streaming_overlap_ratio'])" "$BENCH_JSON" 2>/dev/null)
    if [ -z "$FM_BEFORE" ]; then
      record_failure "weight_streaming_overlap_ratio missing from benchmark JSON ($BENCH_JSON)"
      verdict_fail=1
    else
      DELTA_BEFORE=$(python3 -c "import sys; print(f'{abs(float(sys.argv[1])-float(sys.argv[2])):.4f}')" "$RTL_RAW" "$FM_BEFORE")
      log "Stage 3: fm_overlap_before=$FM_BEFORE rtl_overlap=$RTL_RAW delta_before=$DELTA_BEFORE"
    fi
  fi
fi

# =============================================================================
# Verification helper: Func Model verification suite (timing tests).
# Returns 0 on pass. $1 = log file.
# =============================================================================
run_verification() {
  local vlog="$1"
  ( cd "$ROOT" && PYTHONPATH=sim python -m pytest sim/timing/tests/ -q ) > "$vlog" 2>&1
  local rc=$?
  [ $rc -ne 0 ] && return 1
  ! grep -qE '[0-9]+ failed' "$vlog" || return 1
  grep -qE '[0-9]+ passed' "$vlog"
}

pytest_summary() {
  local p f
  p=$(grep -oE '[0-9]+ passed' "$1" | tail -1)
  f=$(grep -oE '[0-9]+ failed' "$1" | tail -1)
  if [ -n "$f" ]; then
    echo "$f, $p"
  else
    echo "$p"
  fi
}

PYTEST_COUNT=""
VERIFY_RC=1

# =============================================================================
# Stage 4 — delta already within tolerance: no parameter change needed.
# =============================================================================
if [ "${verdict_fail:-0}" = "0" ]; then
  if awk -v d="$DELTA_BEFORE" -v t=0.05 'BEGIN { exit !(d <= t) }'; then
    log "Stage 4: |delta|=$DELTA_BEFORE <= 0.05 — no parameter change needed; running verification"
    run_verification "$SCRATCH/pytest.log"
    VERIFY_RC=$?
    PYTEST_COUNT=$(pytest_summary "$SCRATCH/pytest.log")
    if [ "$VERIFY_RC" -ne 0 ]; then
      record_failure "Func Model verification failed with NO parameter change (pre-existing regression): $PYTEST_COUNT — see $SCRATCH/pytest.log"
      verdict_fail=1
    else
      log "Stage 4: verification PASS ($PYTEST_COUNT)"
    fi
  fi
fi

# =============================================================================
# Stage 5 — delta > 0.05: bounded search over the REAL calibration knobs,
# evaluated with the exact estimator the benchmark uses.
# =============================================================================
SEARCH_RAN=0
BEST_FEASIBLE=""
BEST_BW="" BEST_BS="" BEST_REG=""
if [ "${verdict_fail:-0}" = "0" ] && awk -v d="$DELTA_BEFORE" -v t=0.05 'BEGIN { exit !(d > t) }'; then
  SEARCH_RAN=1
  log "Stage 5: |delta|=$DELTA_BEFORE > 0.05 — searching real calibration knobs"
  # The search mirrors benchmark.py `_compute_weight_streaming_overlap_ratio`
  # exactly: same estimator, same 7-GEMM weighted average, same model dims,
  # same rounding. Knob grid:
  #   broadcast_sync in {0..4}              (benchmark.py L87)
  #   _accumulate register term in {0..2}   (benchmark.py L89, the "+1")
  #   bandwidth_bytes_per_cycle grid        (npu_config.yaml L85 — the DMAModel
  #                                          bw constant inside
  #                                          estimate_tile_double_buffer_overlap)
  cat > "$SCRATCH/search.py" <<'PYEOF'
import math
import sys

sys.path.insert(0, "sim")
from model_specs import get_spec
from models.dma import DMAModel
from timing.timing_engine import TimingEngine

rtl = float(sys.argv[1])
engine = TimingEngine("sim/config/npu_config.yaml")
cfg = engine.config
mxu = cfg.get("mxu", {})
tile_H = int(mxu.get("array_height", 64))
tile_W = int(mxu.get("array_width", 64))
wb = int(mxu.get("weight_precision_bits", 4))
ab = int(mxu.get("activation_precision_bits", 8))

spec = get_spec("qwen2.5-3b")
hidden = spec.hidden
inter = spec.intermediate
qkv = spec.qkv_dim
kv = spec.kv_heads * spec.head_dim
gems = [
    (1, hidden, qkv),        # Q_proj
    (1, hidden, kv),         # K_proj
    (1, hidden, kv),         # V_proj
    (1, qkv, hidden),        # O_proj
    (1, hidden, inter),      # FFN_gate
    (1, hidden, inter),      # FFN_up
    (1, inter, hidden),      # FFN_down
]
weights = [math.ceil(K * N * wb / 8) for _M, K, N in gems]

def weighted(bw: float, bs: int, reg: int) -> float:
    """Mirror of benchmark._compute_weight_streaming_overlap_ratio."""
    c = dict(cfg)
    c["memory"] = dict(cfg.get("memory", {}))
    c["memory"]["bandwidth_bytes_per_cycle"] = float(bw)
    dma = DMAModel(c)
    acc = max(1, min(3, (wb + ab) // 8 + reg))
    per_tile = tile_H + bs + acc
    tot = 0.0
    wsum = 0.0
    for (M, K, N), w in zip(gems, weights):
        r = dma.estimate_tile_double_buffer_overlap(
            M, K, N, tile_H, tile_W, wb, ab, per_tile)
        wsum += r * w
        tot += w
    return round(wsum / tot, 2) if tot > 0 else 0.0

BASE_BW = float(cfg["memory"]["bandwidth_bytes_per_cycle"])
BASE_BS = 2
BASE_REG = 1
base_fm = weighted(BASE_BW, BASE_BS, BASE_REG)
print(f"SEARCH_BASE bw={BASE_BW} bs={BASE_BS} reg={BASE_REG} "
      f"fm={base_fm:.2f} delta={abs(rtl - base_fm):.4f}")

# bw grid in bytes/cycle (51.2 = LPDDR5-6400 raw; 43.52 = raw*0.85 efficiency
# floor; lower values probe the effective DMA-path bandwidth the RTL actually
# achieves — flagged for todo-17 review when a large excursion is selected).
BW_GRID = [51.2, 48.0, 45.0, 43.52, 40.0, 36.0, 32.0, 30.0, 28.0, 26.0,
           25.6, 24.0, 22.0, 20.0, 18.0]
rows = []
for bw in BW_GRID:
    for bs in range(0, 5):
        for reg in range(0, 3):
            fm = weighted(bw, bs, reg)
            rows.append((abs(rtl - fm), bw, bs, reg, fm))
rows.sort()
feasible = [r for r in rows if r[0] <= 0.05 + 1e-9]
if feasible:
    # Best fit to the RTL ground truth first; among equally-fitting candidates,
    # the minimal excursion from the baseline knob values wins.
    feasible.sort(key=lambda r: (r[0],
                                 abs(r[1] - BASE_BW) / BASE_BW
                                 + abs(r[2] - BASE_BS) / 5.0
                                 + abs(r[3] - BASE_REG) / 3.0))
    d, bw, bs, reg, fm = feasible[0]
    print(f"SEARCH_BEST feasible=1 bw={bw} bs={bs} reg={reg} "
          f"fm={fm:.2f} delta={d:.4f}")
else:
    d, bw, bs, reg, fm = rows[0]
    print(f"SEARCH_BEST feasible=0 bw={bw} bs={bs} reg={reg} "
          f"fm={fm:.2f} delta={d:.4f} (best-effort)")
print("SEARCH_TOP10")
for d, bw, bs, reg, fm in rows[:10]:
    print(f"  bw={bw:<5} bs={bs} reg={reg} fm={fm:.2f} delta={d:.4f}")
PYEOF
  ( cd "$ROOT" && PYTHONPATH=sim python3 "$SCRATCH/search.py" "$RTL_RAW" > "$SCRATCH/search.out" 2>&1 )
  SEARCH_RC=$?
  if [ "$SEARCH_RC" -ne 0 ]; then
    record_failure "knob search failed (rc=$SEARCH_RC) — see $SCRATCH/search.out"
    verdict_fail=1
  else
    BEST_LINE=$(grep '^SEARCH_BEST' "$SCRATCH/search.out" | head -1)
    BEST_FEASIBLE=$(echo "$BEST_LINE" | grep -oE 'feasible=[01]' | cut -d= -f2)
    BEST_BW=$(echo "$BEST_LINE" | grep -oE 'bw=[0-9.]+' | cut -d= -f2)
    BEST_BS=$(echo "$BEST_LINE" | grep -oE 'bs=[0-9]+' | cut -d= -f2)
    BEST_REG=$(echo "$BEST_LINE" | grep -oE 'reg=[0-9]+' | cut -d= -f2)
    log "Stage 5: $BEST_LINE"
    if [ "$BEST_FEASIBLE" = "0" ]; then
      record_failure "no feasible knob combination reaches |delta|<=0.05 (best-effort delta in search table below); real knobs saturate — a structural model revision is needed, not forced parameters"
      verdict_fail=1
    fi
  fi
fi

# =============================================================================
# Stage 6 — apply the winning edit (closest-to-baseline feasible combo).
# =============================================================================
PARAM_CHANGES=""
APPLY_ATTEMPTED=0
if [ "${verdict_fail:-0}" = "0" ] && [ "$SEARCH_RAN" = "1" ]; then
  APPLY_ATTEMPTED=1
  log "Stage 6: applying edits bw=$BEST_BW bs=$BEST_BS reg=$BEST_REG"
  APPLY_OUT=$(cd "$ROOT" && python3 - "$BEST_BW" "$BEST_BS" "$BEST_REG" <<'PYEOF'
import re, sys
bw = float(sys.argv[1]); bs = int(sys.argv[2]); reg = int(sys.argv[3])
edits = []
def replace(path, old, new):
    with open(path, encoding="utf-8") as f:
        src = f.read()
    n = src.count(old)
    if n != 1:
        sys.stderr.write(f"{path}: expected exactly 1 occurrence of target, found {n}\n")
        sys.exit(2)
    with open(path, "w", encoding="utf-8") as f:
        f.write(src.replace(old, new))
    edits.append(path)

# Current values (robust against a prior committed calibration)
bench = open("sim/timing/benchmark.py", encoding="utf-8").read()
m = re.search(r"broadcast_sync = (\d+)", bench)
cur_bs = int(m.group(1)) if m else 2
m = re.search(r"def _accumulate\(wb: int, ab: int\) -> int:\n\s*return max\(1, min\(3, \(wb \+ ab\) // 8 \+ (\d+)\)\)", bench)
cur_reg = int(m.group(1)) if m else 1
yaml_src = open("sim/config/npu_config.yaml", encoding="utf-8").read()
m = re.search(r"^  bandwidth_bytes_per_cycle: ([0-9.]+) ", yaml_src, re.M)
cur_bw = float(m.group(1)) if m else 51.2

if bs != cur_bs:
    replace("sim/timing/benchmark.py",
            f"    broadcast_sync = {cur_bs}",
            f"    broadcast_sync = {bs}")
if reg != cur_reg:
    replace("sim/timing/benchmark.py",
            f"        return max(1, min(3, (wb + ab) // 8 + {cur_reg}))",
            f"        return max(1, min(3, (wb + ab) // 8 + {reg}))")
if abs(bw - cur_bw) > 1e-9:
    replace("sim/config/npu_config.yaml",
            f"  bandwidth_bytes_per_cycle: {cur_bw}  # 51.2 GB/s @ 1GHz = 51.2 bytes/cycle",
            f"  bandwidth_bytes_per_cycle: {bw}  # FM-3 calibrated from RTL overlap (todo 16); was {cur_bw}")
print("EDITED:" + ",".join(edits) if edits else "EDITED:NONE")
print(f"CUR bs={cur_bs} reg={cur_reg} bw={cur_bw}")
PYEOF
)
  APPLY_RC=$?
  if [ "$APPLY_RC" -ne 0 ]; then
    record_failure "parameter edit failed (rc=$APPLY_RC): $APPLY_OUT"
    verdict_fail=1
  else
    EDITED_FILES=$(echo "$APPLY_OUT" | grep '^EDITED:' | sed 's/^EDITED://')
    CUR_INFO=$(echo "$APPLY_OUT" | grep '^CUR ' || true)
    CUR_BS_OLD=$(echo "$CUR_INFO" | grep -oE 'bs=[0-9]+' | cut -d= -f2)
    CUR_REG_OLD=$(echo "$CUR_INFO" | grep -oE 'reg=[0-9]+' | cut -d= -f2)
    CUR_BW_OLD=$(echo "$CUR_INFO" | grep -oE 'bw=[0-9.]+' | cut -d= -f2)
    PARAM_CHANGES="  before: broadcast_sync=${CUR_BS_OLD:-2}, _accumulate_reg=${CUR_REG_OLD:-1}, bw=${CUR_BW_OLD:-51.2}
  after : broadcast_sync=$BEST_BS, _accumulate_reg=$BEST_REG, bw=$BEST_BW"
    log "Stage 6: applied ($EDITED_FILES) [$CUR_INFO]"
  fi
fi

# =============================================================================
# Stage 7 — re-run Func Model after adjustment; confirm delta <= 0.05.
# =============================================================================
FM_AFTER=""
DELTA_AFTER=""
if [ "${verdict_fail:-0}" = "0" ] && [ "$SEARCH_RAN" = "1" ]; then
  log "Stage 7: re-run benchmark after adjustment ($(ts))"
  ( cd "$ROOT" && PYTHONPATH=sim python -m sim.timing.benchmark \
      --model qwen2.5-3b --output "$SCRATCH/bench_after" ) > "$SCRATCH/bench_after.log" 2>&1
  BENCH_RC=$?
  if [ "$BENCH_RC" -ne 0 ]; then
    record_failure "re-run benchmark failed (rc=$BENCH_RC)"
    verdict_fail=1
  else
    FM_AFTER=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['weight_streaming_overlap_ratio'])" "$SCRATCH/bench_after/qwen2.5-3b.json" 2>/dev/null)
    DELTA_AFTER=$(python3 -c "import sys; print(f'{abs(float(sys.argv[1])-float(sys.argv[2])):.4f}')" "$RTL_RAW" "$FM_AFTER")
    log "Stage 7: fm_overlap_after=$FM_AFTER delta_after=$DELTA_AFTER"
    if ! awk -v d="$DELTA_AFTER" -v t=0.05 'BEGIN { exit !(d <= t) }'; then
      record_failure "post-adjustment delta=$DELTA_AFTER still > 0.05 (fm=$FM_AFTER vs rtl=$RTL_RAW)"
      verdict_fail=1
    fi
  fi
fi

# =============================================================================
# Stage 8 — Func Model verification after adjustment (and no-change path).
# =============================================================================
if [ "${verdict_fail:-0}" = "0" ]; then
  if [ "$SEARCH_RAN" = "1" ] || [ -z "$PYTEST_COUNT" ]; then
    log "Stage 8: Func Model verification (pytest sim/timing/tests)"
    run_verification "$SCRATCH/pytest_after.log"
    VERIFY_RC=$?
    PYTEST_COUNT=$(pytest_summary "$SCRATCH/pytest_after.log")
    if [ "$VERIFY_RC" -ne 0 ]; then
      record_failure "Func Model verification regressed after adjustment: $PYTEST_COUNT — see $SCRATCH/pytest_after.log"
      verdict_fail=1
    else
      log "Stage 8: verification PASS ($PYTEST_COUNT)"
    fi
  fi
fi

# =============================================================================
# Revert on failure — never leave calibrated-but-broken parameters behind.
# Restore is safe even after a partial apply: the Stage 2 dirty-tree guard
# guarantees both target files were clean (committed baseline) before edits.
# =============================================================================
if [ "${verdict_fail:-0}" -ne 0 ] && [ "$APPLY_ATTEMPTED" = "1" ]; then
  log "Reverting parameter edits (calibration did not verify)"
  git -C "$ROOT" restore -- sim/timing/benchmark.py sim/config/npu_config.yaml
  log "Revert done: $(git -C "$ROOT" diff --quiet -- sim/timing/benchmark.py sim/config/npu_config.yaml && echo clean || echo DIRTY)"
fi

# =============================================================================
# Evidence file
# =============================================================================
TS_END="$(ts)"
VERDICT="PASS"
[ "${verdict_fail:-0}" -ne 0 ] && VERDICT="FAIL"

FM_REPORT="${FM_AFTER:-$FM_BEFORE}"
DELTA_REPORT="${DELTA_AFTER:-$DELTA_BEFORE}"
[ -z "$FM_REPORT" ] && FM_REPORT="unavailable"
[ -z "$DELTA_REPORT" ] && DELTA_REPORT="unavailable"

# fmt2: 2-decimal formatting for evidence fields; falls back to the raw value
# when the field is not numeric (e.g. "unavailable" on early failure).
fmt2() { python3 -c "import sys; print(f'{float(sys.argv[1]):.2f}')" "$1" 2>/dev/null || echo "$1"; }

{
  echo "Task 16 - Phase 10 RTL Verification: FM-3 weight-streaming overlap calibration"
  echo "==============================================================================="
  echo "Timestamp start : ${TS_START}"
  echo "Timestamp end   : ${TS_END}"
  echo "Commit          : ${COMMIT}"
  echo "Driver host     : $(hostname) — Func Model benchmark + verification run locally"
  echo "                  (pure Python; no VCS required; p10_lib sourced for REPO_ROOT)"
  echo ""
  echo "Workload        : qwen2.5-3b (Q4_K_M, INT4xINT8, decode 7-GEMM weighted"
  echo "                  average — same workload as todo 15 FM-3 measurement)"
  echo "RTL source      : ${RTL_EV}"
  echo ""
  echo "rtl_overlap=$(fmt2 "$RTL_RAW")"
  echo "fm_overlap_before=$(fmt2 "$FM_BEFORE")"
  echo "fm_overlap=$(fmt2 "$FM_REPORT")"
  echo "|delta|=$(fmt2 "$DELTA_REPORT") (acceptance: |delta|<=0.05)"
  echo ""
  echo "Updated parameters (real knobs only):"
  if [ "$SEARCH_RAN" = "1" ] && [ "$VERDICT" = "PASS" ]; then
    echo "$PARAM_CHANGES"
    echo "  files touched  : ${EDITED_FILES:-none}"
  elif [ "$SEARCH_RAN" = "1" ]; then
    echo "  none retained — search ran but no verified adjustment was applied"
    echo "  (either infeasible with real knobs, or reverted after a regression)"
  elif [ "$VERDICT" = "PASS" ]; then
    echo "  none — baseline FM prediction already satisfies |delta|<=0.05"
    echo "  (knobs reviewed: broadcast_sync=2, _accumulate_reg=1,"
    echo "   memory.bandwidth_bytes_per_cycle=51.2 — all unchanged)"
  else
    echo "  none — calibration did not reach the parameter-adjustment stage"
    echo "  (see failures below)"
  fi
  echo "  cross_engine_gap annotation (sim/perf_tests.py L261): unchanged — FM-1"
  echo "  same-engine gap basis (crossbar_wait=2, sram_stall=1, vcov_bubble=1);"
  echo "  a different quantity from weight-streaming overlap, not an overlap knob."
  echo ""
  echo "Commands executed (exact):"
  echo "  PYTHONPATH=sim python -m sim.timing.benchmark --model qwen2.5-3b --output <scratch>/bench"
  if [ "$SEARCH_RAN" = "1" ]; then
    echo "  (knob search: PYTHONPATH=sim python3 <scratch>/search.py $RTL_RAW)"
    echo "  PYTHONPATH=sim python -m sim.timing.benchmark --model qwen2.5-3b --output <scratch>/bench_after"
  fi
  echo "  PYTHONPATH=sim python -m pytest sim/timing/tests/ -q"
  echo ""
  echo "Func Model verification:"
  echo "  pytest sim/timing/tests: ${PYTEST_COUNT:-not run}"
  if [ "$SEARCH_RAN" = "1" ] && [ -s "$SCRATCH/search.out" ]; then
    echo ""
    echo "Knob sensitivity (top-10 of bw x broadcast_sync x _accumulate_reg grid):"
    sed -n '/^SEARCH_TOP10$/,$p' "$SCRATCH/search.out" | tail -n +2
  fi
  echo ""
  echo "Verification:"
  if [ "$VERDICT" = "FAIL" ]; then
    echo "  FAIL — one or more gates did not pass:"
    for f in "${failures[@]}"; do
      echo "    - $f"
    done
  else
    echo "  PASS — |RTL-FM delta| <= 0.05 after Func Model verification."
  fi
  echo ""
  echo "Result: ${VERDICT}"
  echo ""
  echo "Run log: task-16-phase10-calibration.log"
} > "$OUT_FILE"
EVIDENCE_WRITTEN=1

log "Evidence written: $OUT_FILE"
cat "$OUT_FILE" >> "$RUN_LOG"

# Preserve scratch logs for post-mortem when the gate failed (the scratch dir
# itself is removed by the EXIT trap).
if [ "$VERDICT" = "FAIL" ]; then
  RUN_KEEP="$(dirname "$OUT_FILE")/task-16-phase10-run"
  rm -rf "$RUN_KEEP"
  mkdir -p "$RUN_KEEP"
  cp -a "$SCRATCH"/. "$RUN_KEEP"/ 2>/dev/null || true
  log "Scratch logs preserved: $RUN_KEEP"
fi

if [ "${verdict_fail:-0}" -ne 0 ]; then
  log "FM-3 calibration gate FAILED — see failures above. Exit 1."
  exit 1
fi

log "FM-3 calibration succeeded: rtl_overlap=$RTL_RAW fm_overlap=$FM_REPORT |delta|=$DELTA_REPORT. Exit 0."
exit 0
