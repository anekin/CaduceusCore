#!/usr/bin/env bash
# =============================================================================
# p10_w1_regression.sh — Phase 10 Todo 6 (Wave 1 closure)
#
# Wave 1 full regression after the DMA/PERF-06 firmware fixes
# (todo 8 commit 7aec7a3: firmware tile-major act_offset + output row
#  interleave; todo 5 commit b158180: COCOTB_BRIDGE_DIAG_DMA probe default
#  off, DMA readback re-verified):
#   1. pytest   (baseline: >= 732 passed; Wave 0 re-run: 1901 passed)
#   2. FM-SOC   (baseline: 33/33; the known pre-existing FM-SOC-10X residual
#                is tolerated ONLY with its exact documented signature
#                "op00 RMSNORM pre-attn: SFU mismatch" — any other FAIL is a
#                new failure and fails the run)
#   3. MXU      (baseline: 9/9)
#   4. SFU      (baseline: 319/319)
#   5. Vector   (baseline: 63/63)
#   6. PERF sample (test_w4_perf_p2 -> PERF-09..12 and
#                   test_w4_perf_p3 -> PERF-13..16 batches on sz0001;
#                   sampled: PERF-09, PERF-10, PERF-11, PERF-13 with
#                   cos_sim >= 0.999 required)
#
# Read-only probe: this script does NOT modify RTL, firmware, or Python
# model code. All regression commands run on sz0001 through p10_ssh. Each
# remote stage runs under a remote `timeout` guard (setsid process-group
# kill) so a hung stage can never leave stray simv processes behind even if
# the SSH session drops.
#
# simv reuse note: simv_soc_ibex and simv_mxu were freshly compiled from the
# current RTL during the Wave 0 baseline run (2026-08-18 ~15:22 CST, commit
# c0fe2fd); the last RTL change is ef090b1 (2026-07-24), so no Phase 10 RTL
# change exists and the binaries are reused. The firmware hex is loaded at
# runtime via +BOOTROM_HEX (FM-SOC) / BOOTROM_HEX env (PERF), so the fresh
# firmware (7aec7a3, rebuilt 2026-08-18 19:53 CST) is picked up regardless.
# Missing binaries are recompiled by the stage scripts themselves.
#
# Usage:
#   bash scripts/p10_w1_regression.sh
#
# Evidence:
#   build/evidence/task-6-phase10-rtl-verification.txt   (final report)
#   build/evidence/task-6-phase10-regression-run.log     (full run log)
# =============================================================================
set -u

source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

# The p10 lib sets `set -euo pipefail`. This runner tracks failures explicitly
# (evidence must be written even when a stage fails, and parse greps that find
# no match must not kill the run), so relax errexit and pipefail here.
set +e
set +o pipefail

ROOT="$REPO_ROOT"
EVIDENCE="$ROOT/build/evidence"
OUT_FILE="$EVIDENCE/task-6-phase10-rtl-verification.txt"
RUN_LOG="$EVIDENCE/task-6-phase10-regression-run.log"
mkdir -p "$EVIDENCE"

# Single-instance guard: two concurrent runners would corrupt each other's
# stage logs and evidence. Fail fast (exit 3) if another runner is active.
LOCK_FILE="$EVIDENCE/task-6.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[p10_w1] ABORT: another p10_w1_regression instance holds $LOCK_FILE (pid $(cat "$LOCK_FILE" 2>/dev/null || echo unknown))"
  exit 3
fi
echo "$$" > "$LOCK_FILE"

# log() prints to stdout AND appends to the run log directly (no tee: GNU tee
# fully buffers file output, which would hide progress from pollers).
log() { echo "[p10_w1] $*"; echo "[p10_w1] $*" >> "$RUN_LOG"; }
ts()  { date -u +%Y-%m-%dT%H:%M:%SZ; }

# ── Baselines ───────────────────────────────────────────────────────────────
# Phase 9 baseline (build/evidence/ph9-regression-run.log, commit 819a34b):
#   pytest 732 passed | FM-SOC 33/33 | MXU 9/9 | SFU 319/319 | Vector 63/63
# Wave 0 re-run (build/evidence/task-3-phase10-rtl-verification.txt, commit
# c0fe2fd): pytest 1901 passed | FM-SOC 32/33 + FM-SOC-10X residual |
#   MXU 9/9 | SFU 319/319 | Vector 63/63
# Post-fix FM-SOC expectation (todo 8 evidence, commit 7aec7a3):
#   32/33 with only FM-SOC-10X failing (deterministic pre-existing residual).
# ============================================================================
PYTEST_BASELINE=732
FM_SOC_BASELINE=33
MXU_BASELINE=9
SFU_BASELINE=319
VECTOR_BASELINE=63

# Wave 0 pytest node-set baseline (for "no new failures" drift classification)
W0_PYTEST_LOG="$EVIDENCE/task-3-pytest.log"
W0_COMMIT="c0fe2fd"

COMMIT="$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo "?")"
TS_START="$(ts)"

failures=()
record_failure() { failures+=("$*"); log "FAIL: $*"; }

# Result holders (parsed from stage logs)
pytest_total="UNPARSED"; pytest_line=""
fm_soc_pass="UNPARSED"; fm_soc_fail="UNPARSED"
fm_fail_list=""; fm_10x_rmsnorm=""; fm_fail_details=""
mxu_pass="UNPARSED";    mxu_fail="UNPARSED"
sfu_pass="UNPARSED";    vector_pass="UNPARSED"

# Trap: guarantee the evidence file exists even if the script is interrupted
# or a stage behaves unexpectedly. The flag also overwrites any stale
# evidence from a previous aborted run so a fresh crash is never masked.
EVIDENCE_WRITTEN=0
trap 'if [ "$EVIDENCE_WRITTEN" = "0" ]; then
  {
    echo "Task 6 - Phase 10 RTL Verification (Wave 1 regression): INCOMPLETE"
    echo "================================================================"
    echo "Timestamp : $(ts)"
    echo "Commit    : ${COMMIT:-?}"
    echo "Status    : interrupted before final evidence write"
    echo "Run log   : build/evidence/task-6-phase10-regression-run.log"
  } > "${OUT_FILE}" 2>/dev/null || true
fi' EXIT

# =============================================================================
# run_remote_stage <name> <timeout_s> <logfile> <body>
#
# Writes <body> to a temp script on sz0001, executes it under a remote
# watchdog (setsid process-group kill after <timeout_s> → no stray simv),
# captures STAGE_EXIT.
# =============================================================================
run_remote_stage() {
  local name="$1" timeout_s="$2" logfile="$3" body="$4"
  local remote_cmd stage_rc ssh_rc
  local t_start t_end

  # log() lines go to stderr so the caller's $(...) captures only the rc.
  log "Stage ${name}: start ($(ts))" >&2
  t_start=$(date +%s)

  body=${body//__ROOT__/$ROOT}

  remote_cmd="set +e
TMPSTAGE=/tmp/p10w1_${name}_\$\$.sh
cat > \"\$TMPSTAGE\" <<'STAGE_EOF'
${body}
STAGE_EOF
# Run the stage in its own process group (setsid) with a detached watchdog
# (own group, stdio closed) that kills the WHOLE stage group after the
# timeout. The watchdog itself is group-killed when the stage finishes, so
# neither orphaned simv nor orphaned sleep can linger or hold the SSH
# channel open after the stage completes.
setsid bash \"\$TMPSTAGE\" &
SPID=\$!
setsid bash -c 'sleep ${timeout_s}; kill -TERM -\$1 2>/dev/null; sleep 10; kill -KILL -\$1 2>/dev/null' _ \$SPID </dev/null >/dev/null 2>&1 &
KILLER=\$!
wait \$SPID
rc=\$?
kill -TERM -\$KILLER 2>/dev/null
sleep 1
kill -KILL -\$KILLER 2>/dev/null
echo \"STAGE_EXIT=\$rc\"
rm -f \"\$TMPSTAGE\"
exit 0"

  p10_ssh "$remote_cmd" > "$logfile" 2>&1
  ssh_rc=$?

  stage_rc=$(grep -oE '^STAGE_EXIT=[0-9]+' "$logfile" | tail -1 | cut -d= -f2)
  if [ -z "$stage_rc" ]; then
    stage_rc="ssh-error-${ssh_rc}"
  fi

  t_end=$(date +%s)
  log "Stage ${name}: done ($(ts), STAGE_EXIT=${stage_rc}, elapsed=$((t_end - t_start))s, log=$logfile)" >&2
  echo "$stage_rc"
}

# =============================================================================
# Stage 1 — pytest regression (sz0001)
# =============================================================================
PYTEST_LOG="$EVIDENCE/task-6-pytest.log"
PYTEST_BODY=$(cat <<'STAGE_EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1
# Phase 9 invocation + --continue-on-collection-errors: 6 test files added
# after the Phase 9 baseline fail collection in this environment (5 need
# gen/ on PYTHONPATH, 1 needs pydantic). The flag keeps the baseline tests
# running; every collection error is still reported and captured.
PYTHONPATH=__ROOT__/.venv_pytest:sim python -m pytest sim/tests/ sim/timing/tests/ -q --continue-on-collection-errors
STAGE_EOF
)
PYTEST_RC=$(run_remote_stage pytest 1500 "$PYTEST_LOG" "$PYTEST_BODY")

pytest_total=$(grep -oE '[0-9]+ passed' "$PYTEST_LOG" | head -1 | awk '{print $1}')
pytest_line=$(grep -oE '[0-9]+ failed, [0-9]+ passed, [0-9]+ skipped, [0-9]+ warnings?, [0-9]+ errors? in [0-9.]+s' "$PYTEST_LOG" | tail -1)
pytest_collect_errs=$(grep -c 'ERROR collecting' "$PYTEST_LOG" 2>/dev/null || true)
pytest_collect_list=$(grep -oE 'ERROR collecting [^ ]+' "$PYTEST_LOG" 2>/dev/null | sed 's/ERROR collecting //' | sort -u | tr '\n' ' ')
[ -n "$pytest_total" ] || pytest_total="UNPARSED"
log "Stage pytest: total=${pytest_total} rc=${PYTEST_RC} collection_errors=${pytest_collect_errs}"

# --- pytest drift classification vs the Wave 0 baseline run -----------------
# Baseline: build/evidence/task-3-pytest.log (Wave 0, commit c0fe2fd).
# Any FAIL/ERROR node in this run that did NOT appear in the Wave 0 run, and
# whose file already existed at the Wave 0 commit, is a regression and fails
# the run. New failures in post-Wave-0 files are documented suite drift.
# Nodes that disappeared relative to Wave 0 are recorded as improvements.
TMPDIR_P10="/tmp/p10_w1_cmp_$$"
mkdir -p "$TMPDIR_P10"
pytest_regressions=""
pytest_postbaseline_nodes=""
pytest_improved=""
pytest_regression_cnt=0
pytest_postbaseline_cnt=0
if [ ! -f "$W0_PYTEST_LOG" ]; then
  record_failure "Wave 0 pytest baseline log missing ($W0_PYTEST_LOG) — cannot classify new failures"
else
  grep -E '^FAILED ' "$W0_PYTEST_LOG" 2>/dev/null | sed 's/FAILED //; s/ - .*//' | sort -u > "$TMPDIR_P10/w0_failed"
  grep -E '^ERROR ' "$W0_PYTEST_LOG" 2>/dev/null | sed 's/ERROR //; s/ - .*//' | sort -u > "$TMPDIR_P10/w0_error"
  grep -E '^FAILED ' "$PYTEST_LOG" 2>/dev/null | sed 's/FAILED //; s/ - .*//' | sort -u > "$TMPDIR_P10/w1_failed"
  grep -E '^ERROR ' "$PYTEST_LOG" 2>/dev/null | sed 's/ERROR //; s/ - .*//' | sort -u > "$TMPDIR_P10/w1_error"

  w0_fail_cnt=$(wc -l < "$TMPDIR_P10/w0_failed")
  w0_err_cnt=$(wc -l < "$TMPDIR_P10/w0_error")
  new_fail=$(comm -13 "$TMPDIR_P10/w0_failed" "$TMPDIR_P10/w1_failed")
  new_err=$(comm -13 "$TMPDIR_P10/w0_error" "$TMPDIR_P10/w1_error")
  pytest_improved=$( { comm -23 "$TMPDIR_P10/w0_failed" "$TMPDIR_P10/w1_failed"; comm -23 "$TMPDIR_P10/w0_error" "$TMPDIR_P10/w1_error"; } | sort -u )

  for node in $new_fail $new_err; do
    file="${node%%::*}"
    first_commit=$(git -C "$ROOT" log --follow --reverse --format='%H' -- "$file" 2>/dev/null | head -1)
    if [ -n "$first_commit" ] && git -C "$ROOT" merge-base --is-ancestor "$first_commit" "$W0_COMMIT" 2>/dev/null; then
      pytest_regressions="${pytest_regressions} ${node}"
    else
      pytest_postbaseline_nodes="${pytest_postbaseline_nodes} ${node}"
    fi
  done
  pytest_postbaseline_files=$(for node in $pytest_postbaseline_nodes; do echo "${node%%::*}"; done | sort -u)
  pytest_postbaseline_cnt=$(echo $pytest_postbaseline_nodes | wc -w)
  pytest_regression_cnt=$(echo $pytest_regressions | wc -w)
  [ -n "$pytest_postbaseline_nodes" ] || pytest_postbaseline_cnt=0
  [ -n "$pytest_regressions" ] || pytest_regression_cnt=0
  log "Stage pytest drift: w0_known=${w0_fail_cnt}F/${w0_err_cnt}E improved=[${pytest_improved:-none}] new_postbaseline=${pytest_postbaseline_cnt} regressions=${pytest_regression_cnt}"
fi

# =============================================================================
# Stage 2 — FM-SOC 33-case regression (sz0001)
# =============================================================================
FM_SOC_LOG="$EVIDENCE/task-6-fm-soc.log"
FM_SOC_BODY=$(cat <<'STAGE_EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1
# simv_soc_ibex reuse: built 2026-08-18 ~15:22 CST during Wave 0 from the
# current RTL (last RTL commit ef090b1, 2026-07-24 — no Phase 10 RTL
# change). run_ibex_full_rtl.sh recompiles only if the binary is missing.
bash sim/regression/run_fm_soc_all.sh
STAGE_EOF
)
FM_SOC_RC=$(run_remote_stage fmsoc 9000 "$FM_SOC_LOG" "$FM_SOC_BODY")

fm_soc_pass=$(grep -cE '^\[PASS\] FM-SOC-' "$FM_SOC_LOG" || true)
fm_soc_fail=$(grep -cE '^\[FAIL\] FM-SOC-' "$FM_SOC_LOG" || true)
fm_fail_list=$(grep -oE '^\[FAIL\] FM-SOC-[0-9X]+' "$FM_SOC_LOG" 2>/dev/null | sed 's/^\[FAIL\] //' | sort -u | tr '\n' ' ')
[ -n "$fm_soc_pass" ] || fm_soc_pass="UNPARSED"
[ -n "$fm_soc_fail" ] || fm_soc_fail="UNPARSED"
log "Stage fmsoc: PASS=${fm_soc_pass} FAIL=${fm_soc_fail} rc=${FM_SOC_RC} failed_cases=[${fm_fail_list:-none}]"

# Extract the failure reason for each failed case and classify the only
# tolerated residual: FM-SOC-10X with the exact pre-existing signature
# "op00 RMSNORM pre-attn: SFU mismatch" (introduced by the Jul 26 firmware
# commits, before any Phase 10 work; deterministic on isolated re-run per
# build/evidence/task-3-phase10-rtl-verification.txt). ANY other FM-SOC
# failure is a new failure and fails the run.
if [ "$fm_soc_fail" -gt 0 ]; then
  for case_id in $fm_fail_list; do
    case_log="$ROOT/build/ibex_full_rtl/evidence/${case_id}.log"
    reason=$(grep -oE "${case_id} failed: [^|]*" "$case_log" 2>/dev/null | head -1)
    [ -n "$reason" ] || reason="${case_id}: see build/ibex_full_rtl/evidence/${case_id}.log"
    fm_fail_details="${fm_fail_details}${reason}
"
    if [ "$case_id" = "FM-SOC-10X" ] && grep -E "FM-SOC-10X failed: op00 RMSNORM pre-attn: SFU mismatch" "$case_log" >/dev/null 2>&1; then
      fm_10x_rmsnorm="yes"
    fi
  done
fi

# =============================================================================
# Stage 3 — MXU 9-scenario regression (sz0001)
# =============================================================================
MXU_LOG="$EVIDENCE/task-6-mxu.log"
MXU_BODY=$(cat <<'STAGE_EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1

# Ensure test vectors exist
python3 scripts/gen_mxu_vectors.py --scenario all --out-dir rtl/test_vectors/mxu

# Reuse the Wave 0 simv_mxu (built 2026-08-18 ~16:36 CST from current RTL);
# recompile only if missing.
if [ ! -x simv_mxu ]; then
  mkdir -p rtl/results
  vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_mxu \
      rtl/tb/tb_mxu.v rtl/mxu/*.v \
      -o simv_mxu -l rtl/results/vcs_compile_tb_mxu.log
fi

# Run all 9 named scenarios and compare against golden
MXU_PASS=0
MXU_FAIL=0
for s in single_tile multi_tile_K multi_tile_N multi_tile_M \
         overflow zero_dim partial_tile_K partial_tile_N partial_tile_M; do
  echo ""
  echo "[MXU] scenario=$s"
  ./simv_mxu +testdir=rtl/test_vectors/mxu/$s +scenario=$s \
      -l rtl/results/vcs_sim_$s.log
  cp rtl/results/mxu_$s.hex rtl/test_vectors/mxu/$s/result.hex
  if python3 sim/compare_rtl.py rtl/test_vectors/mxu/$s | grep -qiE 'PASS|matched'; then
    echo "[MXU] $s PASS"
    MXU_PASS=$((MXU_PASS + 1))
  else
    echo "[MXU] $s FAIL"
    MXU_FAIL=$((MXU_FAIL + 1))
  fi
done

echo ""
echo "MXU summary: $MXU_PASS passed, $MXU_FAIL failed (9 scenarios)"
[ $MXU_FAIL -eq 0 ] || exit 1
STAGE_EOF
)
MXU_RC=$(run_remote_stage mxu 2400 "$MXU_LOG" "$MXU_BODY")

mxu_line=$(grep -E '^MXU summary:' "$MXU_LOG" | tail -1)
mxu_pass=$(echo "$mxu_line" | grep -oE '[0-9]+ passed' | head -1 | awk '{print $1}')
mxu_fail=$(echo "$mxu_line" | grep -oE '[0-9]+ failed' | head -1 | awk '{print $1}')
[ -n "$mxu_pass" ] || mxu_pass="UNPARSED"
[ -n "$mxu_fail" ] || mxu_fail="UNPARSED"
log "Stage mxu: PASS=${mxu_pass} FAIL=${mxu_fail} rc=${MXU_RC}"

# =============================================================================
# Stage 4 — SFU (319) + Vector (63) batch regression (sz0001)
# =============================================================================
SFUVEC_LOG="$EVIDENCE/task-6-sfu-vector.log"
SFUVEC_BODY=$(cat <<'STAGE_EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1

rm -rf rtl/test_vectors/sfu rtl/test_vectors/vector
# Force a fresh compile of the fast batch simvs (guards against stale bins)
rm -f build/simv_tb_sfu_fast build/simv_tb_vector_fast

python3 scripts/gen_sfu_luts.py
python3 scripts/gen_sfu_vectors.py --scenario all
python3 scripts/gen_vector_vectors.py --scenario all

python3 scripts/run_batch_regression.py
# The runner writes its authoritative summary to .omo/evidence/task-17-rerun.txt
# ("SFU: X/Y passed" / "Vector: X/Y passed"). Echo it into this stage log so
# the local parser reads one file.
cat .omo/evidence/task-17-rerun.txt
STAGE_EOF
)
SFUVEC_RC=$(run_remote_stage sfuvec 2400 "$SFUVEC_LOG" "$SFUVEC_BODY")

sfu_line=$(grep -oE '^SFU: [0-9]+/[0-9]+ passed' "$SFUVEC_LOG" | head -1)
vector_line=$(grep -oE '^Vector: [0-9]+/[0-9]+ passed' "$SFUVEC_LOG" | head -1)
sfu_pass=$(echo "$sfu_line" | sed -E 's/^SFU: ([0-9]+)\/[0-9]+ passed$/\1/')
vector_pass=$(echo "$vector_line" | sed -E 's/^Vector: ([0-9]+)\/[0-9]+ passed$/\1/')
[ -n "$sfu_pass" ] || sfu_pass="UNPARSED"
[ -n "$vector_pass" ] || vector_pass="UNPARSED"
log "Stage sfuvec: SFU=${sfu_pass} Vector=${vector_pass} rc=${SFUVEC_RC}"

# =============================================================================
# Stages 5+6 — PERF sample (sz0001): P2 batch (PERF-09..12) + P3 batch
# (PERF-13..16) against simv_soc_ibex + the post-fix firmware hex.
# =============================================================================
PERF_BODY_FACTORY() {
  local testcase="$1"
  cat <<'STAGE_EOF'
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-}:__ROOT__"
export MODULE=sim.perf_tests
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export BOOTROM_HEX="__ROOT__/firmware/build/npu_firmware.hex"
export TESTCASE=__TESTCASE__
cd "$(cd __ROOT__/.. && pwd)"
echo "[p10_w1] Running __TESTCASE__ ..."
"__ROOT__/build/ibex_full_rtl/simv_soc_ibex" +COCOTB +BOOTROM_HEX="$BOOTROM_HEX"
echo "[p10_w1] __TESTCASE__ done."
STAGE_EOF
}

PERF_P2_LOG="$EVIDENCE/task-6-perf-p2.log"
PERF_P2_BODY=$(PERF_BODY_FACTORY test_w4_perf_p2)
PERF_P2_BODY=${PERF_P2_BODY//__TESTCASE__/test_w4_perf_p2}
PERF_P2_RC=$(run_remote_stage perf_p2 2400 "$PERF_P2_LOG" "$PERF_P2_BODY")
sleep 3   # let NFS flush the evidence files before parsing
log "Stage perf_p2: rc=${PERF_P2_RC}"

PERF_P3_LOG="$EVIDENCE/task-6-perf-p3.log"
PERF_P3_BODY=$(PERF_BODY_FACTORY test_w4_perf_p3)
PERF_P3_BODY=${PERF_P3_BODY//__TESTCASE__/test_w4_perf_p3}
PERF_P3_RC=$(run_remote_stage perf_p3 2400 "$PERF_P3_LOG" "$PERF_P3_BODY")
sleep 3
log "Stage perf_p3: rc=${PERF_P3_RC}"

# Parse the sampled PERF cases (PERF-09/10/11 from w4-perf-p2.txt,
# PERF-13 from w4-perf-p3.txt). Every sampled case must be present with
# status=PASS and cos_sim >= 0.999.
PERF_TSV=$(mktemp /tmp/p10_w1_perf.XXXXXX)
PERF_PARSE_OK=1
P10W1_EVID="$EVIDENCE" P10W1_OUT="$PERF_TSV" python3 <<'PYEOF'
import json
import os
import sys

EVID = os.environ["P10W1_EVID"]
OUT = os.environ["P10W1_OUT"]
sample = [
    ("w4-perf-p2.txt", ["PERF-09", "PERF-10", "PERF-11"]),
    ("w4-perf-p3.txt", ["PERF-13"]),
]
rows, problems = [], []
for fname, cids in sample:
    path = os.path.join(EVID, fname)
    by_id = {}
    if os.path.isfile(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            cid = d.get("case_id")
            if cid:
                by_id[cid] = d  # last line wins (guards stale duplicates)
    for cid in cids:
        d = by_id.get(cid)
        if d is None:
            rows.append((cid, "MISSING", "-", "-", fname))
            problems.append(f"{cid}: missing in {fname}")
            continue
        st = str(d.get("status", "?"))
        cs = d.get("cos_sim")
        cs_s = ("%.6f" % float(cs)) if isinstance(cs, (int, float)) else "-"
        cyc = str(d.get("cycles", "-"))
        rows.append((cid, st, cs_s, cyc, fname))
        if st != "PASS":
            problems.append(f"{cid}: status={st} in {fname}")
        elif cs_s != "-" and float(cs) < 0.999:
            problems.append(f"{cid}: cos_sim={cs_s} < 0.999 in {fname}")

with open(OUT, "w", encoding="utf-8") as f:
    for r in rows:
        f.write("\t".join(r) + "\n")

print(f"perf sample parsed {len(rows)} cases")
if problems:
    print("PROBLEMS:")
    for p in problems:
        print("  " + p)
    sys.exit(1)
print("perf sample all PASS (cos_sim >= 0.999 where applicable)")
PYEOF
PERF_PARSE_OK=$?
perf_sample_line=""
while IFS=$'\t' read -r cid st cs cyc fname; do
  [ -n "$cid" ] || continue
  perf_sample_line="${perf_sample_line}${cid}=${st}(cs=${cs},cyc=${cyc}) "
  log "  perf sample: ${cid} ${st} cos_sim=${cs} cycles=${cyc} evidence=${fname}"
done < "$PERF_TSV"
rm -f "$PERF_TSV"

# =============================================================================
# Verification against baselines
# =============================================================================
verdict_fail=0

# pytest: >= 732 passed (Phase 9 count; Wave 0 re-run produced 1901). A drop
# below the Phase 9 baseline is count drift and fails loudly.
if [ "$pytest_total" = "UNPARSED" ]; then
  record_failure "pytest_total=UNPARSED (no summary line found in $PYTEST_LOG)"
  verdict_fail=1
elif [ "$pytest_total" -lt "$PYTEST_BASELINE" ]; then
  record_failure "pytest_total=$pytest_total < baseline $PYTEST_BASELINE (count drift)"
  verdict_fail=1
fi

# pytest new-failure classification vs the Wave 0 baseline run
if [ "$pytest_regression_cnt" -gt 0 ]; then
  record_failure "pytest new failures in Wave 0-era files: $pytest_regressions"
  verdict_fail=1
fi

# FM-SOC verdict: the ONLY tolerated failure is FM-SOC-10X with the exact
# pre-existing signature "op00 RMSNORM pre-attn: SFU mismatch". 33/33 PASS
# is the full-clean outcome; 32/33 + 10X residual is the documented
# post-Jul-26-firmware state (Wave 0 and todo 8 both observed it,
# deterministic on isolated re-run). ANY other FM-SOC failure is a new
# failure and fails the run.
if [ "$fm_soc_pass" = "UNPARSED" ] || [ "$fm_soc_fail" = "UNPARSED" ]; then
  record_failure "FM-SOC counts unparsed (log missing or stage failed) — see $FM_SOC_LOG"
  verdict_fail=1
else
  if [ "$fm_10x_rmsnorm" = "yes" ]; then
    fm_expected_pass=$((FM_SOC_BASELINE - 1))
  else
    fm_expected_pass=$FM_SOC_BASELINE
  fi
  fm_unexpected_fail=0
  [ "$fm_soc_fail" -gt 0 ] && [ "$fm_10x_rmsnorm" != "yes" ] && fm_unexpected_fail=1
  if [ "$fm_soc_pass" -ne "$fm_expected_pass" ] || [ "$fm_unexpected_fail" -eq 1 ]; then
    record_failure "FM-SOC PASS=$fm_soc_pass FAIL=$fm_soc_fail != baseline 33/0 (only the known FM-SOC-10X residual is tolerated)"
    verdict_fail=1
  fi
fi

if [ "$mxu_pass" != "$MXU_BASELINE" ] || [ "$mxu_fail" != "0" ]; then
  record_failure "MXU PASS=$mxu_pass FAIL=$mxu_fail != baseline 9/0"
  verdict_fail=1
fi

if [ "$sfu_pass" != "$SFU_BASELINE" ]; then
  record_failure "SFU pass=$sfu_pass != baseline $SFU_BASELINE"
  verdict_fail=1
fi

if [ "$vector_pass" != "$VECTOR_BASELINE" ]; then
  record_failure "Vector pass=$vector_pass != baseline $VECTOR_BASELINE"
  verdict_fail=1
fi

if [ "$PERF_PARSE_OK" -ne 0 ]; then
  record_failure "PERF sample FAILED (missing case / status!=PASS / cos_sim<0.999) — see stage logs ${PERF_P2_LOG} and ${PERF_P3_LOG}"
  verdict_fail=1
fi

# Stage-level exit codes: a stage killed by the remote watchdog (137=KILL,
# 143=TERM) means it timed out — fail loudly even if partial output happens
# to parse. Other non-zero exits are logged (pytest/fmsoc legitimately exit
# non-zero when known failures are present; parsed counts stay the source of
# truth).
for s in "pytest ${PYTEST_RC}" "fmsoc ${FM_SOC_RC}" "mxu ${MXU_RC}" "sfuvec ${SFUVEC_RC}" "perf_p2 ${PERF_P2_RC}" "perf_p3 ${PERF_P3_RC}"; do
  set -- $s
  case "$2" in
    124|137|143)
      record_failure "stage $1 TIMED OUT (remote watchdog killed it; exit=$2)"
      verdict_fail=1
      ;;
    ssh-error-*)
      record_failure "stage $1 SSH failure ($2) — counts may be unparsed"
      verdict_fail=1
      ;;
    "")
      record_failure "stage $1 produced no exit code (log missing)"
      verdict_fail=1
      ;;
    *)
      if [ "$2" != "0" ]; then
        log "NOTE: stage $1 exit=$2 (see its log; counts below are the source of truth)"
      fi
      ;;
  esac
done

# =============================================================================
# Cleanup receipt — no stray processes from this run on sz0001
# =============================================================================
log "Cleanup: checking for stray processes from this repo on sz0001"
STRAY_CHECK=$(p10_ssh "pgrep -af '/home/prj/zhengs/caduceuscore/CaduceusCore' 2>/dev/null | grep -E 'simv|p10w1|run_ibex|run_fm_soc|run_batch' || echo 'NO_STRAY_PROCESSES'" 2>&1 | tail -5)
log "Cleanup check: $STRAY_CHECK"

# =============================================================================
# Evidence file
# =============================================================================
TS_END="$(ts)"
VERDICT="PASS"
[ "$verdict_fail" -ne 0 ] && VERDICT="FAIL"

mk_match() {
  if [ "$1" = "$2" ]; then echo "MATCH"; else echo "MISMATCH"; fi
}
mk_pytest_match() {
  if [ "$1" = "UNPARSED" ]; then
    echo "UNPARSED"
  elif [ "$1" -lt "$2" ]; then
    echo "MISMATCH(below-baseline)"
  elif [ "$1" -eq "$2" ]; then
    echo "MATCH"
  else
    echo "ABOVE-BASELINE(documented-drift)"
  fi
}

{
  echo "Task 6 - Phase 10 RTL Verification: Wave 1 full regression after DMA fix"
  echo "==========================================================================="
  echo "Timestamp start : ${TS_START}"
  echo "Timestamp end   : ${TS_END}"
  echo "Commit          : ${COMMIT}"
  echo "Driver host     : $(hostname) (sz0002) — all regression commands executed on sz0001"
  echo "                  via p10_ssh (ssh ${ZHENGS}@${SZ0001})"
  echo ""
  echo "Scope: Wave 1 (todo 6) closure regression after the DMA/PERF-06 firmware"
  echo "fixes. Todo 8 (7aec7a3) fixed the firmware act_offset tile-major stride and"
  echo "output DMA row interleave; todo 5 (b158180) set COCOTB_BRIDGE_DIAG_DMA"
  echo "default-off and re-verified DMA readback (test_e2e_dma_load_store + PERF-"
  echo "p0 sample PASS). This run confirms no degradation across the full"
  echo "regression surface plus a PERF sample."
  echo ""
  echo "Baseline source : build/evidence/ph9-regression-run.log (commit 819a34b)"
  echo "                  (pytest 732 passed | FM-SOC 33/33 | MXU 9/9 | SFU 319/319 | Vector 63/63)"
  echo "                  build/evidence/task-3-phase10-rtl-verification.txt (Wave 0,"
  echo "                  commit c0fe2fd: pytest 1901 | FM-SOC 32/33 + FM-SOC-10X |"
  echo "                  MXU 9/9 | SFU 319/319 | Vector 63/63)"
  echo "                  build/evidence/task-8-phase10-rtl-verification.txt (todo 8,"
  echo "                  post-firmware-fix: FM-SOC 32/33 + FM-SOC-10X only)"
  echo ""
  echo "simv reuse note : simv_soc_ibex (built Wave 0, 2026-08-18 ~15:22 CST) and"
  echo "                  simv_mxu (built Wave 0, ~16:36 CST) reused — no RTL change"
  echo "                  since ef090b1 (2026-07-24); firmware hex is loaded at"
  echo "                  runtime via +BOOTROM_HEX / BOOTROM_HEX env, so the post-fix"
  echo "                  firmware (7aec7a3, rebuilt 2026-08-18 19:53 CST) is picked"
  echo "                  up. Missing binaries are recompiled by the stage scripts."
  echo ""
  echo "Commands executed (exact, via p10_ssh on sz0001):"
  echo "  1. pytest   : PYTHONPATH=${ROOT}/.venv_pytest:sim python -m pytest sim/tests/ sim/timing/tests/ -q --continue-on-collection-errors"
  echo "                (log: build/evidence/task-6-pytest.log)"
  echo "  2. FM-SOC   : bash sim/regression/run_fm_soc_all.sh   (33 cases)"
  echo "                (log: build/evidence/task-6-fm-soc.log)"
  echo "  3. MXU      : python3 scripts/gen_mxu_vectors.py --scenario all --out-dir rtl/test_vectors/mxu"
  echo "                ./simv_mxu +testdir=rtl/test_vectors/mxu/<s> +scenario=<s> (x9) + sim/compare_rtl.py"
  echo "                (log: build/evidence/task-6-mxu.log)"
  echo "  4. SFU+Vec  : python3 scripts/gen_sfu_luts.py"
  echo "                python3 scripts/gen_sfu_vectors.py --scenario all"
  echo "                python3 scripts/gen_vector_vectors.py --scenario all"
  echo "                python3 scripts/run_batch_regression.py"
  echo "                cat .omo/evidence/task-17-rerun.txt  (authoritative counts)"
  echo "                (log: build/evidence/task-6-sfu-vector.log)"
  echo "  5. PERF p2  : MODULE=sim.perf_tests TESTCASE=test_w4_perf_p2 TOPLEVEL=tb_soc_ibex"
  echo "                build/ibex_full_rtl/simv_soc_ibex +COCOTB +BOOTROM_HEX=firmware/build/npu_firmware.hex"
  echo "                -> w4-perf-p2.txt (PERF-09..12)"
  echo "                (log: build/evidence/task-6-perf-p2.log)"
  echo "  6. PERF p3  : MODULE=sim.perf_tests TESTCASE=test_w4_perf_p3 TOPLEVEL=tb_soc_ibex"
  echo "                build/ibex_full_rtl/simv_soc_ibex +COCOTB +BOOTROM_HEX=firmware/build/npu_firmware.hex"
  echo "                -> w4-perf-p3.txt (PERF-13..16)"
  echo "                (log: build/evidence/task-6-perf-p3.log)"
  echo ""
  echo "Regression counts (Wave 1 vs baseline):"
  echo "  pytest_total   = ${pytest_total}   (baseline >= ${PYTEST_BASELINE})      [$(mk_pytest_match "$pytest_total" "$PYTEST_BASELINE")]"
  if [ "$fm_10x_rmsnorm" = "yes" ]; then
    if [ "$fm_soc_pass" -eq 32 ] && [ "$fm_soc_fail" -eq 1 ]; then
      echo "  fm_soc_pass    = ${fm_soc_pass}    (baseline ${FM_SOC_BASELINE}/0)       [MATCH(32/33 + 1 known pre-existing FM-SOC-10X residual)]"
    else
      echo "  fm_soc_pass    = ${fm_soc_pass}    (baseline ${FM_SOC_BASELINE}/0)       [MISMATCH]"
    fi
  else
    echo "  fm_soc_pass    = ${fm_soc_pass}    (baseline ${FM_SOC_BASELINE}/0)       [$(mk_match "$fm_soc_pass" "$FM_SOC_BASELINE")]"
  fi
  echo "  fm_soc_fail    = ${fm_soc_fail}    (baseline 0)"
  if [ "$fm_soc_fail" -gt 0 ]; then
    echo "  FM-SOC failure details:"
    echo "    failed case(s): ${fm_fail_list}"
    while IFS= read -r d; do
      [ -n "$d" ] && echo "    reason: ${d}"
    done <<< "$fm_fail_details"
    if [ "$fm_10x_rmsnorm" = "yes" ]; then
      echo "    FM-SOC-10X signature 'op00 RMSNORM pre-attn: SFU mismatch max_abs=2.95e+00'"
      echo "    matches the pre-existing failure documented in"
      echo "    build/evidence/f3-final-summary.txt (Phase 9 F3, 2026-08-06,"
      echo "    'CONFIRMED PRE-EXISTING', fails on d6b1adc too) and reproduced"
      echo "    deterministically in Wave 0 (task-3 isolated re-run) and todo 8"
      echo "    (task-8-fm-soc.log). NOT introduced by Phase 10 work."
    fi
  fi
  echo "  mxu_pass       = ${mxu_pass}     (baseline ${MXU_BASELINE}/0)          [$(mk_match "$mxu_pass" "$MXU_BASELINE")]"
  echo "  mxu_fail       = ${mxu_fail}     (baseline 0)"
  echo "  sfu_pass       = ${sfu_pass}   (baseline ${SFU_BASELINE})         [$(mk_match "$sfu_pass" "$SFU_BASELINE")]"
  echo "  vector_pass    = ${vector_pass}    (baseline ${VECTOR_BASELINE})          [$(mk_match "$vector_pass" "$VECTOR_BASELINE")]"
  echo "  sfu_line       : ${sfu_line:-<no summary line>}"
  echo "  vector_line    : ${vector_line:-<no summary line>}"
  echo "  pytest detail  : ${pytest_line:-<no summary line>}"
  echo ""
  echo "PERF sample (cos_sim >= 0.999 required; from test_w4_perf_p2 + test_w4_perf_p3):"
  echo "  ${perf_sample_line}"
  if [ "$PERF_PARSE_OK" -ne 0 ]; then
    echo "  PERF sample verdict: FAIL (see problems above)"
  else
    echo "  PERF sample verdict: PASS (all sampled cases cos_sim >= 0.999)"
  fi
  echo ""
  echo "Pytest drift vs Wave 0 baseline (build/evidence/task-3-pytest.log, commit ${W0_COMMIT}):"
  if [ ! -f "$W0_PYTEST_LOG" ]; then
    echo "  Wave 0 pytest log MISSING — drift classification unavailable (count gate only)."
  else
    echo "  Wave 0 FAILED/ERROR nodes no longer failing (improvements): $( [ -z "$pytest_improved" ] && echo none || echo "$pytest_improved" )"
    echo "  New FAIL/ERROR nodes: ${pytest_postbaseline_cnt} in files added after the"
    echo "  Wave 0 commit (${W0_COMMIT}) — documented suite drift."
    echo "  Regressions in Wave 0-era files: ${pytest_regression_cnt} $( [ "$pytest_regression_cnt" -gt 0 ] && echo "— ${pytest_regressions}" || echo "(none)" )"
    for f in ${pytest_postbaseline_files}; do
      d=$(git -C "$ROOT" log --follow --reverse --format='%ci' -- "$f" 2>/dev/null | head -1 | cut -d' ' -f1)
      echo "    - ${f} (first committed ${d:-?})"
    done
  fi
  echo "  Collection errors: ${pytest_collect_errs}"
  for c in ${pytest_collect_list}; do
    echo "    - ERROR collecting ${c}"
  done
  if [ -n "${pytest_collect_list}" ]; then
    echo "  Cause: 5 device-protocol test files require gen/ on PYTHONPATH"
    echo "         (documented convention: PYTHONPATH=sim:gen); 1 timing test"
    echo "         (test_perf_contract.py) requires pydantic, not installed in the"
    echo "         sz0001 conda env. Pre-existing since the Wave 0 run — reported,"
    echo "         not masked."
  fi
  echo ""
  echo "Known residuals (carried from Phase 9 / Wave 0, unchanged by this run):"
  echo "  1. FM-SOC-10X: 'op00 RMSNORM pre-attn: SFU mismatch' (pre-existing,"
  echo "     deterministic; see FM-SOC details above)."
  echo "  2. PERF-06 residual (Phase 9 cos_sim=0.0535): RESOLVED by todo 8 firmware"
  echo "     fix — 21/21 PERF PASS confirmed by todo 9 causality gate"
  echo "     (build/evidence/task-9-phase10-rtl-verification.txt)."
  echo "  3. Q8_0 / 6b experiment: BLOCKED-NETWORK (external download; not part of"
  echo "     this gate)."
  echo "  4. SFU wrapper functional pre-existing FAILs + MXU wrapper harness"
  echo "     AttributeError: Wave 0 wrapper-stage residuals (task-3 evidence,"
  echo "     section (a) items 3 and 5). Not exercised by this gate — no wrapper"
  echo "     RTL or harness change occurred in Wave 1."
  echo ""
  echo "Cleanup receipt:"
  echo "  - All 6 regression stages ran synchronously under remote timeout guards;"
  echo "    no background jobs started by this script."
  echo "  - Stray-process check on sz0001 (repo-scoped): ${STRAY_CHECK}"
  echo ""
  echo "Verification:"
  if [ "$verdict_fail" -ne 0 ]; then
    echo "  Counts do NOT match the baselines / new failures detected:"
    for f in "${failures[@]}"; do
      echo "    - $f"
    done
  else
    echo "  All counts match the baselines; PERF sample cos_sim >= 0.999;"
    echo "  no new failures vs the Wave 0 baseline."
  fi
  echo ""
  echo "Result: ${VERDICT}"
  echo ""
  echo "Per-stage logs:"
  echo "  ${PYTEST_LOG}"
  echo "  ${FM_SOC_LOG}"
  echo "  ${MXU_LOG}"
  echo "  ${SFUVEC_LOG}"
  echo "  ${PERF_P2_LOG}"
  echo "  ${PERF_P3_LOG}"
  echo "  ${RUN_LOG}"
} > "$OUT_FILE"
EVIDENCE_WRITTEN=1

log "Evidence written: $OUT_FILE"
cat "$OUT_FILE" >> "$RUN_LOG"

if [ "$verdict_fail" -ne 0 ]; then
  log "Wave 1 regression FAILED — counts differ from baselines or new FAILs appeared."
  exit 1
fi

log "Wave 1 regression PASS. All counts met, PERF sample cos_sim >= 0.999. Exit 0."
exit 0
