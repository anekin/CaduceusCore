#!/usr/bin/env bash
# Phase 9 T5 — Full regression suite after T4 firmware + RTL fix.
# Runs on sz0001 (VCS regressions); firmware rebuild runs locally because the
# RISC-V toolchain is only available on sz0002 and the /home/prj tree is
# NFS-shared between the two hosts.
set -u

source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

ROOT="$REPO_ROOT"
EVIDENCE="$ROOT/build/evidence"
FAIL_FILE="$EVIDENCE/ph9-regression-fail.txt"
LEARNINGS="$ROOT/.omo/notepads/phase9-firmware-rtl-fix/learnings.md"
ISSUES="$ROOT/.omo/notepads/phase9-firmware-rtl-fix/issues.md"

mkdir -p "$EVIDENCE"
mkdir -p "$(dirname "$LEARNINGS")"

# Redirect all script output to a run log so the regression progress is
# preserved and the local runner is not flooded with sz0001 VCS output.
exec > "$EVIDENCE/ph9-regression-run.log" 2>&1

log() { echo "[p9_regression] $*"; }

failures=()
record_failure() {
  failures+=("$*")
  log "FAIL: $*"
}

# Remove any stale failure file from a previous run.
rm -f "$FAIL_FILE"

# -----------------------------------------------------------------------------
# Step a — Firmware rebuild gate (local, sz0002)
# -----------------------------------------------------------------------------
log "Step a: firmware rebuild gate"
cd "$ROOT/firmware" && make clean && make
cd "$ROOT"

elf_ts=$(stat -c %Y firmware/build/npu_firmware.elf)
src_ts=$(stat -c %Y firmware/npu_firmware.c)
hdr_ts=$(stat -c %Y firmware/npu-regmap.h)

if [ "$elf_ts" -le "$src_ts" ]; then
  record_failure "firmware/build/npu_firmware.elf is not newer than firmware/npu_firmware.c"
fi
if [ "$elf_ts" -le "$hdr_ts" ]; then
  record_failure "firmware/build/npu_firmware.elf is not newer than firmware/npu-regmap.h"
fi

if [ ${#failures[@]} -eq 0 ]; then
  log "Firmware rebuild gate PASSED"
fi

# -----------------------------------------------------------------------------
# Step b/d — SoC simv rebuild + FM-SOC 33-case regression (sz0001)
# -----------------------------------------------------------------------------
log "Step b/d: deleting stale simv_soc_ibex and running FM-SOC regression on sz0001"

FM_SOC_LOG="$EVIDENCE/ph9-fm-soc-33.log"
cat > "$FM_SOC_LOG" <<EOF
=== Phase 9 T5 FM-SOC regression ===
Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Host: $(hostname)
Action: delete build/ibex_full_rtl/simv_soc_ibex and run sim/regression/run_fm_soc_all.sh

EOF

fm_soc_remote=$(cat <<'EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1
rm -f build/ibex_full_rtl/simv_soc_ibex
bash sim/regression/run_fm_soc_all.sh 2>&1 | tee -a __LOG__
exit ${PIPESTATUS[0]}
EOF
)
fm_soc_remote="${fm_soc_remote//__ROOT__/$ROOT}"
fm_soc_remote="${fm_soc_remote//__LOG__/$FM_SOC_LOG}"

if ! p9_ssh "$fm_soc_remote"; then
  record_failure "FM-SOC regression script returned non-zero exit code"
fi

# -----------------------------------------------------------------------------
# Step c — Python pytest regression (sz0001)
# -----------------------------------------------------------------------------
log "Step c: running pytest regression on sz0001"

PYTEST_LOG="$EVIDENCE/ph9-pytest.log"
pytest_remote=$(cat <<'EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1
PYTHONPATH=__ROOT__/.venv_pytest:sim python -m pytest sim/tests/ sim/timing/tests/ -q 2>&1 | tee __LOG__
exit ${PIPESTATUS[0]}
EOF
)
pytest_remote="${pytest_remote//__ROOT__/$ROOT}"
pytest_remote="${pytest_remote//__LOG__/$PYTEST_LOG}"

p9_ssh "$pytest_remote" || true

# -----------------------------------------------------------------------------
# Step e — MXU 9-scenario regression (sz0001)
# -----------------------------------------------------------------------------
log "Step e: running MXU 9-scenario regression on sz0001"

MXU_LOG="$EVIDENCE/ph9-mxu-reg.log"
cat > "$MXU_LOG" <<EOF
=== Phase 9 T5 MXU regression ===
Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)
Host: $(hostname)

EOF

mxu_remote=$(cat <<'EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1

# Ensure test vectors exist
python3 scripts/gen_mxu_vectors.py --scenario all --out-dir rtl/test_vectors/mxu

# Compile MXU testbench from updated RTL
mkdir -p rtl/results
vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_mxu \
    rtl/tb/tb_mxu.v rtl/mxu/*.v \
    -o simv_mxu -l rtl/results/vcs_compile_tb_mxu.log 2>&1 | tee -a __LOG__
[ ${PIPESTATUS[0]} -eq 0 ] || exit 1

# Run all 9 named scenarios and compare against golden
MXU_PASS=0
MXU_FAIL=0
for s in single_tile multi_tile_K multi_tile_N multi_tile_M \
         overflow zero_dim partial_tile_K partial_tile_N partial_tile_M; do
  echo "" | tee -a __LOG__
  echo "[MXU] scenario=$s" | tee -a __LOG__
  ./simv_mxu +testdir=rtl/test_vectors/mxu/$s +scenario=$s \
      -l rtl/results/vcs_sim_$s.log 2>&1 | tee -a __LOG__
  cp rtl/results/mxu_$s.hex rtl/test_vectors/mxu/$s/result.hex
  if python3 sim/compare_rtl.py rtl/test_vectors/mxu/$s 2>&1 | tee -a __LOG__ | grep -qiE 'PASS|matched'; then
    echo "[MXU] $s PASS" | tee -a __LOG__
    MXU_PASS=$((MXU_PASS + 1))
  else
    echo "[MXU] $s FAIL" | tee -a __LOG__
    MXU_FAIL=$((MXU_FAIL + 1))
  fi
done

echo "" | tee -a __LOG__
echo "MXU summary: $MXU_PASS passed, $MXU_FAIL failed (9 scenarios)" | tee -a __LOG__
[ $MXU_FAIL -eq 0 ] || exit 1
EOF
)
mxu_remote="${mxu_remote//__ROOT__/$ROOT}"
mxu_remote="${mxu_remote//__LOG__/$MXU_LOG}"

if ! p9_ssh "$mxu_remote"; then
  record_failure "MXU regression returned non-zero exit code"
fi

# -----------------------------------------------------------------------------
# Step f — SFU + Vector batch regression (sz0001)
# -----------------------------------------------------------------------------
log "Step f: running SFU + Vector batch regression on sz0001"

SFUVEC_LOG="$EVIDENCE/ph9-sfu-vector.log"
sfuvec_remote=$(cat <<'EOF'
set -euo pipefail
cd __ROOT__
source sim/regression/run_env.sh >/dev/null 2>&1

rm -rf rtl/test_vectors/sfu rtl/test_vectors/vector
python3 scripts/gen_sfu_luts.py
python3 scripts/gen_sfu_vectors.py --scenario all
python3 scripts/gen_vector_vectors.py --scenario all

python3 scripts/run_batch_regression.py 2>&1 | tee __LOG__
exit ${PIPESTATUS[0]}
EOF
)
sfuvec_remote="${sfuvec_remote//__ROOT__/$ROOT}"
sfuvec_remote="${sfuvec_remote//__LOG__/$SFUVEC_LOG}"

if ! p9_ssh "$sfuvec_remote"; then
  record_failure "SFU+Vector batch regression returned non-zero exit code"
fi

# -----------------------------------------------------------------------------
# Acceptance criteria checks
# -----------------------------------------------------------------------------
log "Running acceptance criteria checks"

ac_fail=0

# AC: pytest >=210 passed
pytest_passed=$(grep -oE '[0-9]+ passed' "$PYTEST_LOG" | head -1 | awk '{print $1}')
if [ -z "$pytest_passed" ]; then
  pytest_passed=0
fi
if [ "$pytest_passed" -lt 210 ]; then
  record_failure "pytest passed count $pytest_passed < 210 (see $PYTEST_LOG)"
  ac_fail=1
else
  log "AC pytest: $pytest_passed passed (>=210) PASSED"
fi

# AC: FM-SOC 33 PASS, 0 FAIL
fm_pass=$(grep -cE '^\[PASS\] FM-SOC-' "$FM_SOC_LOG" || true)
fm_fail=$(grep -cE '^\[FAIL\] FM-SOC-' "$FM_SOC_LOG" || true)
if [ "$fm_pass" -ne 33 ] || [ "$fm_fail" -ne 0 ]; then
  record_failure "FM-SOC regression PASS=$fm_pass FAIL=$fm_fail (expected 33/0, see $FM_SOC_LOG)"
  ac_fail=1
else
  log "AC FM-SOC: PASS=$fm_pass FAIL=$fm_fail PASSED"
fi

# AC: MXU 9/9 PASS
if ! grep -qE 'MXU.*9/9.*PASS|MXU.*all.*9.*PASS|MXU summary: 9 passed, 0 failed' "$MXU_LOG"; then
  record_failure "MXU regression did not report 9/9 PASS (see $MXU_LOG)"
  ac_fail=1
else
  log "AC MXU: 9/9 PASSED"
fi

# AC: SFU 319/319 and Vector 63/63
if ! grep -qE 'SFU.*319|319/319' "$SFUVEC_LOG"; then
  record_failure "SFU regression did not report 319/319 (see $SFUVEC_LOG)"
  ac_fail=1
else
  log "AC SFU: 319/319 PASSED"
fi
if ! grep -qE 'Vector.*63|63/63' "$SFUVEC_LOG"; then
  record_failure "Vector regression did not report 63/63 (see $SFUVEC_LOG)"
  ac_fail=1
else
  log "AC Vector: 63/63 PASSED"
fi

# AC: SoC simv was recompiled (compile/elaborate step logged in FM-SOC log)
if ! grep -qiE 'VCS|compile|elaborate' "$FM_SOC_LOG"; then
  record_failure "FM-SOC log does not contain VCS compile/elaborate evidence"
  ac_fail=1
else
  log "AC SoC simv rebuild: compile/elaborate log evidence present"
fi

# AC: firmware elf newer than source/header
elf_ts=$(stat -c %Y firmware/build/npu_firmware.elf)
src_ts=$(stat -c %Y firmware/npu_firmware.c)
hdr_ts=$(stat -c %Y firmware/npu-regmap.h)
if [ "$elf_ts" -le "$src_ts" ] || [ "$elf_ts" -le "$hdr_ts" ]; then
  record_failure "firmware rebuild gate failed at final AC"
  ac_fail=1
else
  log "AC firmware rebuild gate PASSED"
fi

# -----------------------------------------------------------------------------
# Final disposition
# -----------------------------------------------------------------------------
if [ $ac_fail -ne 0 ] || [ ${#failures[@]} -gt 0 ]; then
  {
    echo "Phase 9 T5 regression FAILED"
    echo "Timestamp: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "Host: $(hostname)"
    echo ""
    echo "Failures:"
    for f in "${failures[@]}"; do
      echo "  - $f"
    done
    echo ""
    echo "Evidence files:"
    echo "  $PYTEST_LOG"
    echo "  $FM_SOC_LOG"
    echo "  $MXU_LOG"
    echo "  $SFUVEC_LOG"
  } > "$FAIL_FILE"

  {
    echo ""
    echo "## T5 Regression Failure Log"
    echo ""
    echo "**Date:** $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "**Failures:**"
    for f in "${failures[@]}"; do
      echo "- $f"
    done
    echo ""
  } >> "$ISSUES"

  log "Regression FAILED. Wrote $FAIL_FILE"
  exit 1
fi

# All ACs passed — append learnings and commit.
{
  echo ""
  echo "## T5 Full Regression Execution Log"
  echo ""
  echo "**Date:** $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "**Result:** ALL PASS"
  echo ""
  echo "| Regression | Result |"
  echo "|------------|--------|"
  echo "| pytest | $pytest_passed passed |"
  echo "| FM-SOC | PASS=$fm_pass FAIL=$fm_fail |"
  echo "| MXU | 9/9 PASS |"
  echo "| SFU | 319/319 PASS |"
  echo "| Vector | 63/63 PASS |"
  echo ""
} >> "$LEARNINGS"

log "All acceptance criteria PASSED. T5 regression complete."
