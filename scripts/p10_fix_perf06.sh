#!/usr/bin/env bash
# =============================================================================
# p10_fix_perf06.sh — Phase 10 Todo 8: PERF-06 firmware offset fix + verification
# =============================================================================
# Fix: firmware ring-buffer dispatch now uses the tile-major K-tile stride
#      (act_offset = k_start * TILE_H) instead of k_start * M.
#
# This runner:
#   1. Rebuilds the firmware ELF/hex (local riscv64 toolchain; sz0001 has none).
#   2. Runs PERF-05 (M=1) + PERF-06 (M=32) + M=64 control on sz0001 against the
#      rebuilt firmware, asserting cos_sim >= 0.999.
#   3. Re-runs the MXU module regression (9 scenarios) — no collateral damage.
#   4. Re-runs the FM-SOC regression (33 cases) — firmware change must not
#      break SoC cases (FM-SOC-10X is a known pre-existing residual).
#   5. Writes build/evidence/task-8-phase10-rtl-verification.txt.
#
# Usage:
#   bash scripts/p10_fix_perf06.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Source Phase-10 sz0001 helpers (defines p10_ssh)
source "$REPO_ROOT/scripts/p10_lib/p10_sz0001.sh"

SIMV="$REPO_ROOT/build/ibex_full_rtl/simv_soc_ibex"
BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex"
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
EVIDENCE_FILE="$EVIDENCE_DIR/task-8-phase10-rtl-verification.txt"
RUN_LOG="$EVIDENCE_DIR/task-8-phase10-regression-run.log"
mkdir -p "$EVIDENCE_DIR"

# Pre-fix baselines (todo 7 evidence, commit d51c3e2)
BEFORE_M1="0.554298"
BEFORE_M32="0.019153"

ts() { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "[p10_fix_perf06] $*" | tee -a "$RUN_LOG"; }

# ── remote stage runner (setsid + detached watchdog, like p10_baseline) ──────
run_remote_stage() {
  local name="$1" timeout_s="$2" logfile="$3" body="$4"
  local remote_cmd stage_rc
  log "Stage ${name}: start ($(ts))"
  local t_start=$(date +%s)
  body=${body//__ROOT__/$REPO_ROOT}
  remote_cmd="set +e
TMPSTAGE=/tmp/p10fixperf06_${name}_\$\$.sh
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
  local stage_rc=$(grep -oE '^STAGE_EXIT=[0-9]+' "$logfile" | tail -1 | cut -d= -f2)
  [ -n "$stage_rc" ] || stage_rc="ssh-error"
  local t_end=$(date +%s)
  log "Stage ${name}: done ($(ts), STAGE_EXIT=${stage_rc}, elapsed=$((t_end - t_start))s, log=$logfile)"
  echo "$stage_rc"
}

# ═════════════════════════════════════════════════════════════════════════════
# Step 1 — Rebuild firmware (local toolchain; NFS-shared with sz0001)
# ═════════════════════════════════════════════════════════════════════════════
log "Rebuilding firmware (make -C firmware)"
if ! command -v riscv64-unknown-elf-gcc >/dev/null 2>&1; then
    log "[ERROR] riscv64-unknown-elf-gcc not found locally; sz0001 has no riscv toolchain"
    exit 1
fi
make -C firmware clean >/dev/null
make -C firmware >/dev/null
if [ ! -f "$BOOTROM_HEX" ]; then
    log "[ERROR] firmware hex not produced at $BOOTROM_HEX"
    exit 1
fi
FW_HEX_MD5=$(md5sum "$BOOTROM_HEX" | awk '{print $1}')
log "firmware hex rebuilt: $BOOTROM_HEX (md5=$FW_HEX_MD5)"

if [ ! -x "$SIMV" ]; then
    log "[ERROR] simv_soc_ibex not found at $SIMV"
    log "  Run: bash sim/regression/run_ibex_full_rtl.sh FM-SOC-001"
    exit 1
fi

# ═════════════════════════════════════════════════════════════════════════════
# Step 2 — PERF-05 / PERF-06 / M=64 control with rebuilt firmware (sz0001)
# ═════════════════════════════════════════════════════════════════════════════
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
PERF_LOG="$EVIDENCE_DIR/p10-fix-perf06.log"
PERF_BODY=$(cat <<'STAGE_EOF'
set -euo pipefail
export PYTHONPATH="${PYTHONPATH:-}:__ROOT__"
export MODULE=sim.p10_verify_perf06
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export BOOTROM_HEX="__ROOT__/firmware/build/npu_firmware.hex"
export TESTCASE=test_perf06_fixed
cd "$(cd __ROOT__/.. && pwd)"
"__ROOT__/build/ibex_full_rtl/simv_soc_ibex" +COCOTB \
    +BOOTROM_HEX="$BOOTROM_HEX"
STAGE_EOF
)
PERF_RC=$(run_remote_stage perf 2400 "$PERF_LOG" "$PERF_BODY")
log "Stage perf: rc=${PERF_RC}"

if [ ! -f "$EVIDENCE_FILE" ]; then
    log "[ERROR] PERF evidence not written (see $PERF_LOG)"
    tail -40 "$PERF_LOG" || true
    exit 1
fi

# ═════════════════════════════════════════════════════════════════════════════
# Step 3 — MXU module regression (9 scenarios; firmware-independent, guard)
# ═════════════════════════════════════════════════════════════════════════════
MXU_LOG="$EVIDENCE_DIR/task-8-mxu.log"
MXU_BODY=$(cat <<'STAGE_EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1

python3 scripts/gen_mxu_vectors.py --scenario all --out-dir rtl/test_vectors/mxu

# Reuse the prebuilt tb_mxu simv (RTL unchanged in this task).
if [ ! -x simv_mxu ]; then
  mkdir -p rtl/results
  vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_mxu \
      rtl/tb/tb_mxu.v rtl/mxu/*.v \
      -o simv_mxu -l rtl/results/vcs_compile_tb_mxu.log
fi

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
log "Stage mxu: rc=${MXU_RC}"

# ═════════════════════════════════════════════════════════════════════════════
# Step 4 — FM-SOC regression (33 cases; FM-SOC-10X = known pre-existing residual)
# ═════════════════════════════════════════════════════════════════════════════
FM_SOC_LOG="$EVIDENCE_DIR/task-8-fm-soc.log"
FM_SOC_BODY=$(cat <<'STAGE_EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1
bash sim/regression/run_fm_soc_all.sh
STAGE_EOF
)
FM_SOC_RC=$(run_remote_stage fmsoc 9900 "$FM_SOC_LOG" "$FM_SOC_BODY")
log "Stage fmsoc: rc=${FM_SOC_RC}"

# ═════════════════════════════════════════════════════════════════════════════
# Step 5 — Assemble final evidence
# ═════════════════════════════════════════════════════════════════════════════
{
    echo ""
    echo "## Cross-checks (todo 8)"
    echo "  Firmware hex md5: $FW_HEX_MD5"
    echo "  Before fix (todo 7): M=1 cos_sim=$BEFORE_M1, M=32 cos_sim=$BEFORE_M32"
    grep -E '^  PERF-0[567]' "$EVIDENCE_FILE" || true
    echo ""
    mxu_line=$(grep -E '^MXU summary:' "$MXU_LOG" | tail -1)
    echo "  MXU module regression: ${mxu_line:-UNPARSED (see task-8-mxu.log)}"
    echo ""
    echo "  FM-SOC regression:"
    grep -cE '^\[PASS\] FM-SOC-' "$FM_SOC_LOG" | sed 's/^/    PASS: /'
    grep -cE '^\[FAIL\] FM-SOC-' "$FM_SOC_LOG" | sed 's/^/    FAIL: /'
    grep -cE '^\[SKIP\] FM-SOC-' "$FM_SOC_LOG" | sed 's/^/    SKIP: /'
    grep -E '^\[FAIL\] FM-SOC-' "$FM_SOC_LOG" || echo "    (no failures)"
    grep -E '\[SUMMARY\]' -A 4 "$FM_SOC_LOG" | tail -5
} > "$EVIDENCE_DIR/task-8-crosschecks.tmp"
cat "$EVIDENCE_DIR/task-8-crosschecks.tmp" >> "$EVIDENCE_FILE"
rm -f "$EVIDENCE_DIR/task-8-crosschecks.tmp"

echo ""
echo "============================================================"
echo "[P10-FIX] Done"
echo "  Evidence: $EVIDENCE_FILE"
echo "  PERF log: $PERF_LOG"
echo "  MXU log:  $MXU_LOG"
echo "  FM log:   $FM_SOC_LOG"
echo "============================================================"
cat "$EVIDENCE_FILE"
exit 0
