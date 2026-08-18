#!/usr/bin/env bash
# =============================================================================
# p10_perf06_causality_gate.sh — Phase 10 Todo 9: PERF-06 causality gate
# =============================================================================
# After the PERF-06 firmware offset fix (todo 8, commit 7aec7a3), re-run the
# full PERF regression on sz0001 and gate on 21/21 PASS:
#
#   6 batches (same set as p9_perfect_batch.sh):
#     test_w4_perf_p0        -> w4-perf-p0.txt        (PERF-01..04)
#     test_w4_perf_p1        -> w4-perf-p1.txt        (PERF-05..08)
#     test_w4_perf_p2        -> w4-perf-p2.txt        (PERF-09..12)
#     test_w4_perf_p3        -> w4-perf-p3.txt        (PERF-13..16)
#     test_w4_perf_p4        -> w4-perf-p4.txt        (PERF-17..20)
#     test_w4_perf_fullchain -> fullchain-pipeline.txt(FULLCHAIN)
#
# Gate conditions (all must hold, else non-zero exit and NO list update):
#   - all 21 cases present in evidence with status=PASS
#   - every cos_sim-bearing case cos_sim >= 0.999 (PERF-06 explicitly)
#
# On green: updates rtl/testcase-list-perf.md (PERF-06 row -> PASS, stats
# line -> PASS 21 | NOT RESOLVED 0), writes
# build/evidence/task-9-phase10-rtl-verification.txt and commits.
#
# Exit codes:
#   0  all 21 PASS, testcase-list synced, evidence written, committed
#   1  precondition failure (ssh/toolchain/simv/firmware missing)
#   2  regression failure (any case FAIL or PERF-06 cos_sim < 0.999)
#   3  testcase-list sync/verification failure
#
# Usage:
#   bash scripts/p10_perf06_causality_gate.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Phase-10 sz0001 helpers (defines p10_ssh: module load vcs + run_env.sh)
source "$REPO_ROOT/scripts/p10_lib/p10_sz0001.sh"

SIMV="$REPO_ROOT/build/ibex_full_rtl/simv_soc_ibex"
FW_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
GATE_EVIDENCE="$EVIDENCE_DIR/task-9-phase10-rtl-verification.txt"
TC_LIST="$REPO_ROOT/rtl/testcase-list-perf.md"
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo "?")"
STAGE_TIMEOUT="${P10_GATE_STAGE_TIMEOUT:-2400}"
TMPDIR="$(mktemp -d /tmp/p10_perf06_gate.XXXXXX)"
trap 'rm -rf "$TMPDIR"' EXIT

mkdir -p "$EVIDENCE_DIR"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[p10_perf06_gate] $*"; }

# Batch table: testcase | evidence file | description
BATCHES=(
  "test_w4_perf_p0|w4-perf-p0.txt|P0 Infrastructure (PERF-01..04)"
  "test_w4_perf_p1|w4-perf-p1.txt|P1 Multi-Tile Baseline (PERF-05..08)"
  "test_w4_perf_p2|w4-perf-p2.txt|P2 Weight Streaming (PERF-09..12)"
  "test_w4_perf_p3|w4-perf-p3.txt|P3 All MMULs + Chain (PERF-13..16)"
  "test_w4_perf_p4|w4-perf-p4.txt|P4 Deep Analysis (PERF-17..20)"
  "test_w4_perf_fullchain|fullchain-pipeline.txt|FULLCHAIN pipeline"
)

# ── remote stage runner (setsid + detached watchdog, like p10_fix_perf06) ──
run_remote_stage() {
  local name="$1" timeout_s="$2" logfile="$3" body="$4"
  # stdout of this function is captured by callers; keep it to the exit code
  log "Stage ${name}: start ($(ts), timeout=${timeout_s}s)" 1>&2
  local t_start
  t_start=$(date +%s)
  body=${body//__ROOT__/$REPO_ROOT}
  local remote_cmd
  remote_cmd="set +e
TMPSTAGE=/tmp/p10gate_${name}_\$\$.sh
cat > \"\$TMPSTAGE\" <<'STAGE_EOF'
${body}
STAGE_EOF
setsid bash \"\$TMPSTAGE\" &
SPID=\$!
setsid bash -c 'sleep ${timeout_s}; kill -TERM -\$1 2>/dev/null; sleep 10; kill -KILL -\$1 2>/dev/null' _ \$SPID </dev/null >/dev/null 2>&1 &
KILLER=\$!
wait \$SPID
rc=\$?
kill -TERM -\$KILLER 2>/dev/null; sleep 1; kill -KILL -\$KILLER 2>/dev/null
echo \"STAGE_EXIT=\$rc\"
rm -f \"\$TMPSTAGE\"
exit 0"
  p10_ssh "$remote_cmd" > "$logfile" 2>&1 || true
  local stage_rc
  stage_rc=$(grep -oE '^STAGE_EXIT=[0-9]+' "$logfile" | tail -1 | cut -d= -f2)
  [ -n "$stage_rc" ] || stage_rc="ssh-error"
  local t_end
  t_end=$(date +%s)
  log "Stage ${name}: done ($(ts), STAGE_EXIT=${stage_rc}, elapsed=$((t_end - t_start))s)" 1>&2
  printf '%s' "$stage_rc"
}

# ── batch body factory (__TESTCASE__ / __ROOT__ substituted later) ─────────
make_batch_body() {
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
echo "[p10_gate] Running __TESTCASE__ ..."
"__ROOT__/build/ibex_full_rtl/simv_soc_ibex" +COCOTB +BOOTROM_HEX="$BOOTROM_HEX"
echo "[p10_gate] __TESTCASE__ done."
STAGE_EOF
}

# ── parse all 21 expected case entries from evidence files ─────────────────
# Output: TSV rows "case_id<TAB>status<TAB>cos_sim<TAB>cycles<TAB>file" to
# $TMPDIR/results.tsv. Exits 1 if any case is missing/FAIL/cos_sim<0.999.
parse_all() {
  P10_GATE_EVID_DIR="$EVIDENCE_DIR" \
  P10_GATE_RESULTS="$TMPDIR/results.tsv" python3 <<'PYEOF'
import json
import os
import sys

EVID = os.environ["P10_GATE_EVID_DIR"]
OUT = os.environ["P10_GATE_RESULTS"]

expected = {
    "w4-perf-p0.txt": ["PERF-01", "PERF-02", "PERF-03", "PERF-04"],
    "w4-perf-p1.txt": ["PERF-05", "PERF-06", "PERF-07", "PERF-08"],
    "w4-perf-p2.txt": ["PERF-09", "PERF-10", "PERF-11", "PERF-12"],
    "w4-perf-p3.txt": ["PERF-13", "PERF-14", "PERF-15", "PERF-16"],
    "w4-perf-p4.txt": ["PERF-17", "PERF-18", "PERF-19", "PERF-20"],
    "fullchain-pipeline.txt": ["FULLCHAIN"],
}

rows = []
problems = []
for fname, cids in expected.items():
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

print(f"parsed {len(rows)} cases")
if problems:
    print("PROBLEMS:")
    for p in problems:
        print("  " + p)
    sys.exit(1)
print("all parsed cases PASS")
PYEOF
}

# ── write failure verdict and exit (regression not green: no list update) ──
fail_gate() {
  local code="$1" reason="$2"
  {
    echo "# Phase 10 Todo 9 — PERF-06 Causality Gate: FAILED"
    echo "# Generated: ${TIMESTAMP}"
    echo "# Commit: ${COMMIT}"
    echo "# Script: scripts/p10_perf06_causality_gate.sh"
    echo ""
    echo "verdict: FAIL"
    echo "reason: ${reason}"
    echo "PERF-06 cos_sim>=0.999: FAIL"
    echo "testcase-list: NOT updated (regression not green)"
    echo "stats-line-synced: false"
    echo ""
    echo "## Parsed results"
    if [ -f "$TMPDIR/results.tsv" ]; then
      while IFS=$'\t' read -r cid st cs cyc fname; do
        printf '  %-14s %-8s cos_sim=%-10s cycles=%-8s evidence=%s\n' \
               "$cid" "$st" "$cs" "$cyc" "$fname"
      done < "$TMPDIR/results.tsv"
    fi
    echo ""
    echo "## Stage logs"
    for lf in "$TMPDIR"/stage-*.log; do
      [ -f "$lf" ] || continue
      echo "  $(basename "$lf") -> removed with temp dir (rerun to reproduce)"
    done
  } > "$GATE_EVIDENCE"
  log "FAIL: ${reason}"
  exit "$code"
}

# ═════════════════════════════════════════════════════════════════════════════
# Step 0 — Preconditions
# ═════════════════════════════════════════════════════════════════════════════
echo "=== Phase 10 Todo 9: PERF-06 Causality Gate ==="
echo "Commit: ${COMMIT}"
echo "Timestamp: ${TIMESTAMP}"
echo ""

log "[0/5] Preconditions..."
[ -f "$FW_HEX" ] || { log "FATAL: firmware hex missing at $FW_HEX"; exit 1; }
FW_HEX_MD5=$(md5sum "$FW_HEX" | awk '{print $1}')
log "  firmware.hex: OK (md5=${FW_HEX_MD5})"
if ! p10_ssh "test -x '${SIMV}'" 2>/dev/null; then
    log "  simv_soc_ibex MISSING on sz0001 — rebuilding via run_ibex_full_rtl.sh..."
    p10_ssh "bash sim/regression/run_ibex_full_rtl.sh FM-SOC-001" || {
        log "FATAL: cannot compile simv_soc_ibex"; exit 1; }
    p10_ssh "test -x '${SIMV}'" || { log "FATAL: simv still missing after rebuild"; exit 1; }
    log "  simv_soc_ibex: REBUILT OK"
else
    log "  simv_soc_ibex: OK (sz0001)"
fi

# ═════════════════════════════════════════════════════════════════════════════
# Step 1 — Run the 6 PERF batches on sz0001
# ═════════════════════════════════════════════════════════════════════════════
log "[1/5] Running 6 PERF batches on sz0001..."
STAGE_RCS=""
for BATCH_ENTRY in "${BATCHES[@]}"; do
    IFS='|' read -r TESTCASE EVFILE DESC <<< "${BATCH_ENTRY}"
    echo ""
    echo "── ${TESTCASE} — ${DESC} ──"
    BODY="$(make_batch_body)"
    BODY=${BODY//__TESTCASE__/$TESTCASE}
    STAGELOG="$TMPDIR/stage-${TESTCASE}.log"
    RC=$(run_remote_stage "${TESTCASE}" "$STAGE_TIMEOUT" "$STAGELOG" "$BODY")
    STAGE_RCS="${STAGE_RCS} ${TESTCASE}=${RC}"
    if [ "$RC" != "0" ]; then
        log "  stage ${TESTCASE} failed (rc=${RC}); see log tail:"
        tail -15 "$STAGELOG" || true
        fail_gate 2 "stage ${TESTCASE} exited ${RC}"
    fi
    sleep 3  # let NFS flush evidence files before parsing
done

# ═════════════════════════════════════════════════════════════════════════════
# Step 2 — Parse + causality checks (all 21 PASS, PERF-06 cos_sim >= 0.999)
# ═════════════════════════════════════════════════════════════════════════════
log "[2/5] Parsing evidence and running causality checks..."
if ! parse_all; then
    fail_gate 2 "one or more PERF cases FAIL / missing / cos_sim<0.999"
fi
TOTAL=$(wc -l < "$TMPDIR/results.tsv" | tr -d ' ')
[ "$TOTAL" -eq 21 ] || fail_gate 2 "expected 21 cases, parsed ${TOTAL}"

PERF06_CS=$(awk -F'\t' '$1=="PERF-06" {print $3}' "$TMPDIR/results.tsv")
PERF06_ST=$(awk -F'\t' '$1=="PERF-06" {print $2}' "$TMPDIR/results.tsv")
if [ -z "${PERF06_CS}" ] || [ "${PERF06_ST}" != "PASS" ] \
   || ! python3 -c "import sys; sys.exit(0 if float('${PERF06_CS}') >= 0.999 else 1)"; then
    fail_gate 2 "PERF-06 cos_sim=${PERF06_CS:-parse-error} (need >= 0.999)"
fi
log "  PERF-06 cos_sim=${PERF06_CS} -> PASS"
log "  All ${TOTAL}/21 cases PASS"

# ═════════════════════════════════════════════════════════════════════════════
# Step 3 — Sync rtl/testcase-list-perf.md (PERF-06 row + stats line)
# ═════════════════════════════════════════════════════════════════════════════
log "[3/5] Syncing ${TC_LIST}..."
cp "$TC_LIST" "${TC_LIST}.bak.$$"
P10_GATE_TC_LIST="$TC_LIST" P10_GATE_TS="$TIMESTAMP" P10_GATE_CS="$PERF06_CS" python3 <<'PYEOF'
import os
import re
import sys

path = os.environ["P10_GATE_TC_LIST"]
ts = os.environ["P10_GATE_TS"]
cs = os.environ["P10_GATE_CS"]

with open(path, encoding="utf-8") as f:
    content = f.read()

# 1) PERF-06 row -> PASS with evidence path (keep method/acceptance columns)
new_row = ("| PERF-06 | P1 | `test_perf_mmul_2x2` (cocotb, K=128,N=128,M=32) | "
           "M=32 multi-tile (M-tile=1, K-tile=2, N-tile=2)，验证 M 维 tile loop | "
           "4/4 tile PASS, per-M-row 结果 bit-exact | ✅ PASS | "
           f"Phase 10: cos_sim={cs} (firmware tile-major act_offset + output row interleave fix, todo 8). "
           "Evidence: w4-perf-p1.txt, task-9-phase10-rtl-verification.txt |")
pat = re.compile(r"^\| PERF-06 \|.*$", re.M)
matches = pat.findall(content)
assert len(matches) == 1, f"PERF-06 row: expected exactly 1 match, got {len(matches)}"
content, n = pat.subn(lambda m: new_row, content, count=1)
assert n == 1, "PERF-06 row replacement failed"

# 2) stats line (L183)
old_stats = "Phase 9 状态: PASS 17 | NOT RESOLVED 2 | analytical 8 (subset of PASS)"
new_stats = "Phase 10 状态: PASS 21 | NOT RESOLVED 0 | analytical 8 (subset of PASS)"
if new_stats in content:
    print("stats line already synced")
elif old_stats in content:
    content = content.replace(old_stats, new_stats, 1)
else:
    raise SystemExit("stats line not found (neither old nor new form)")

# 3) last-updated stamp (non-fatal if already updated elsewhere)
old_ts = "> 最后更新: 2026-07-22T02:00:41Z"
if old_ts in content:
    content = content.replace(old_ts, f"> 最后更新: {ts}", 1)

with open(path, "w", encoding="utf-8") as f:
    f.write(content)
print("testcase-list updated (PERF-06 row, stats line, timestamp)")
PYEOF

# ── verify the sync (stats line must match actual row counts) ──────────────
PASS_ROWS=$(grep -c '| ✅ PASS |' "$TC_LIST" || true)
NR_ROWS=$(grep -c '| 🔶 NOT RESOLVED |' "$TC_LIST" || true)
log "  table counts: PASS rows=${PASS_ROWS}, NOT RESOLVED rows=${NR_ROWS}"
if [ "$PASS_ROWS" -ne 21 ] || [ "$NR_ROWS" -ne 0 ]; then
    fail_gate 3 "stats mismatch: table has PASS=${PASS_ROWS} NOT_RESOLVED=${NR_ROWS} (expected 21/0)"
fi
if ! grep -q 'Phase 10 状态: PASS 21 | NOT RESOLVED 0' "$TC_LIST"; then
    fail_gate 3 "stats line not synced in ${TC_LIST}"
fi
log "  stats-line-synced: true"
rm -f "${TC_LIST}.bak.$$"

# ═════════════════════════════════════════════════════════════════════════════
# Step 4 — Write gate evidence
# ═════════════════════════════════════════════════════════════════════════════
log "[4/5] Writing ${GATE_EVIDENCE}..."
{
  echo "# Phase 10 Todo 9 — PERF-06 Causality Gate: full PERF regression + testcase-list sync"
  echo "# Generated: ${TIMESTAMP}"
  echo "# Commit: ${COMMIT}"
  echo "# Script: scripts/p10_perf06_causality_gate.sh"
  echo "# Firmware: ${FW_HEX} (md5=${FW_HEX_MD5})"
  echo "# Simulator: ${SIMV} (sz0001, module vcs/vcs_2023.12sp2)"
  echo ""
  echo "## Regression results (${TOTAL}/21 PASS)"
  echo ""
  while IFS=$'\t' read -r cid st cs cyc fname; do
    printf '%-14s %-6s cos_sim=%-10s cycles=%-8s evidence=%s\n' \
           "$cid" "$st" "$cs" "$cyc" "$fname"
  done < "$TMPDIR/results.tsv"
  echo ""
  echo "PERF-06 cos_sim>=0.999: PASS (cos_sim=${PERF06_CS})"
  echo "testcase-list: 21/21 PASS"
  echo "stats-line-synced: true"
  echo "stats-line: Phase 10 状态: PASS 21 | NOT RESOLVED 0 | analytical 8 (subset of PASS)"
  echo ""
  echo "## Testcase-list changes (rtl/testcase-list-perf.md)"
  echo "  PERF-06 row: 🔶 NOT RESOLVED -> ✅ PASS (evidence w4-perf-p1.txt + this file)"
  echo "  stats line:  'Phase 9 状态: PASS 17 | NOT RESOLVED 2 | ...'"
  echo "               -> 'Phase 10 状态: PASS 21 | NOT RESOLVED 0 | ...'"
  echo "  last-updated: ${TIMESTAMP}"
  echo ""
  echo "## Stage exit codes (sz0001)"
  for rc in ${STAGE_RCS}; do
    echo "  ${rc}"
  done
  echo ""
  echo "## Note (out of gate scope, pre-existing)"
  echo "  test_w4_perf_fullchain_sfu_vector (5-op FULLCHAIN-SFU-VEC variant) is not part of this"
  echo "  gate: current firmware ring dispatch has no SFU/Vector opcode cases (since the"
  echo "  b0096d0 software-stack rewrite), matching the p9_perfect_batch.sh fullchain batch"
  echo "  which uses test_w4_perf_fullchain (MMUL segment). Row FULLCHAIN-SFU-VEC keeps its"
  echo "  Phase 8 PASS evidence; firmware SFU/Vector ring dispatch is tracked separately."
} > "$GATE_EVIDENCE"
log "  evidence written"

# ═════════════════════════════════════════════════════════════════════════════
# Step 5 — Cleanup + commit
# ═════════════════════════════════════════════════════════════════════════════
log "[5/5] Cleanup + commit..."
rm -f "$TMPDIR"/stage-*.log
rm -rf "$TMPDIR"
log "  temp PERF logs removed (${TMPDIR})"

git -C "$REPO_ROOT" add \
  scripts/p10_perf06_causality_gate.sh \
  rtl/testcase-list-perf.md
# build/evidence/ is gitignored; the repo convention commits gate evidence with -f
# (plain `git add` exits 1 on ignored-dir paths even for tracked files)
git -C "$REPO_ROOT" add -f \
  build/evidence/task-9-phase10-rtl-verification.txt \
  build/evidence/w4-perf-p0.txt build/evidence/w4-perf-p1.txt \
  build/evidence/w4-perf-p2.txt build/evidence/w4-perf-p3.txt \
  build/evidence/w4-perf-p4.txt build/evidence/fullchain-pipeline.txt

if git -C "$REPO_ROOT" diff --cached --quiet; then
  log "  nothing to commit (already committed)"
else
  git -C "$REPO_ROOT" commit -m "docs(rtl): mark PERF-06 PASS and update testcase-list"
  log "  committed: $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
fi

# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo "============================================================"
echo "[P10 PERF-06 CAUSALITY GATE] PASS"
echo "  PERF regression : ${TOTAL}/21 PASS (PERF-06 cos_sim=${PERF06_CS})"
echo "  testcase-list   : 21/21 PASS, stats line synced"
echo "  evidence        : ${GATE_EVIDENCE}"
echo "============================================================"
exit 0
