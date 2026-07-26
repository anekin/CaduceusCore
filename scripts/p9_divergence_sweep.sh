#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

REPORT="${REPO_ROOT}/build/evidence/ph9-divergence-report.txt"
VERDICT_JSON="${REPO_ROOT}/build/evidence/ph9-divergence-verdict.json"
INCONCLUSIVE="${REPO_ROOT}/build/evidence/ph9-divergence-inconclusive.txt"
LEARNINGS="${REPO_ROOT}/.omo/notepads/phase9-firmware-rtl-fix/learnings.md"
SOC_SIMV="${REPO_ROOT}/sim/regression/simv_soc_cocotb"
LOG="${REPO_ROOT}/build/evidence/ph9-divergence-sweep.log"

mkdir -p "$(dirname "$REPORT")"
mkdir -p "$(dirname "$LOG")"
mkdir -p "$(dirname "$LEARNINGS")"

echo "[p9_divergence_sweep] Starting T3 divergence sweep on $(hostname)"

# Pre-flight: ensure no RTL/firmware SOURCE file modifications. Build artifacts
# (firmware/build/*.elf, *.o, *.map) may differ from HEAD due to T1 rebuild;
# they are restored before the final AC check.
SRC_DIFF=$(git -C "${REPO_ROOT}" diff --name-only -- \
  'rtl/**/*.c' 'rtl/**/*.h' 'rtl/**/*.v' 'rtl/**/*.sv' 'rtl/**/*.py' \
  'firmware/**/*.c' 'firmware/**/*.h' 'firmware/**/*.v' 'firmware/**/*.sv' 'firmware/**/*.py' | wc -l)
if [[ "$SRC_DIFF" -ne 0 ]]; then
  echo "[p9_divergence_sweep] ERROR: RTL/firmware source files modified ($SRC_DIFF files)"
  git -C "${REPO_ROOT}" diff --name-only -- \
    'rtl/**/*.c' 'rtl/**/*.h' 'rtl/**/*.v' 'rtl/**/*.sv' 'rtl/**/*.py' \
    'firmware/**/*.c' 'firmware/**/*.h' 'firmware/**/*.v' 'firmware/**/*.sv' 'firmware/**/*.py'
  exit 1
fi
echo "[p9_divergence_sweep] Read-only source check passed"

# Ensure cocotb simv exists; if not, build it once (compilation is not a source edit).
if [[ ! -x "$SOC_SIMV" ]]; then
  echo "[p9_divergence_sweep] Building ${SOC_SIMV} ..."
  p9_ssh "cd '${REPO_ROOT}/sim/regression' && make simv_soc_cocotb"
  chmod +x "$SOC_SIMV" 2>/dev/null || true
fi
if [[ ! -x "$SOC_SIMV" ]]; then
  echo "[p9_divergence_sweep] ERROR: ${SOC_SIMV} not executable after build"
  exit 1
fi

# Run the cocotb divergence sweep on sz0001.
# We run two separate simulations because the direct wrapper preload path
# drives APB/MMIO directly; mixing it with firmware doorbell in one run
# caused APB contention and firmware timeout in initial testing.
# The command is executed inside p9_ssh after run_env.sh is sourced, so
# COCOTB_LIB_DIR and COCOTB_PY_ENV are available on the remote side.
run_path() {
  local testcase="$1"
  local cmd
  cmd=$(cat <<'EOF'
set -e
cd __REPO_PARENT__
LD_LIBRARY_PATH="${COCOTB_LIB_DIR}:${COCOTB_PY_ENV}/lib:${LD_LIBRARY_PATH:-}" \
PYTHONPATH="__REPO_ROOT__/sim" \
MODULE=p9_divergence_test \
TESTCASE=__TESTCASE__ \
TOPLEVEL=tb_soc \
TOPLEVEL_LANG=verilog \
PYTHONIOENCODING=utf-8 \
COCOTB_RESULTS_FILE="__REPO_ROOT__/sim/regression/p9_divergence_results.xml" \
"./CaduceusCore/sim/regression/simv_soc_cocotb" +define+COCOTB_SIM=1 +COCOTB -no_save \
+BOOTROM_HEX="__REPO_ROOT__/firmware/build/npu_firmware.hex" 2>&1 | tee -a "__LOG__"
EOF
)
  cmd="${cmd//__REPO_PARENT__/$(dirname "$REPO_ROOT")}"
  cmd="${cmd//__REPO_ROOT__/$REPO_ROOT}"
  cmd="${cmd//__TESTCASE__/$testcase}"
  cmd="${cmd//__LOG__/$LOG}"
  p9_ssh "$cmd"
}

run_path test_p9_direct_sweep
run_path test_p9_firmware_sweep

# Merge the per-path result files into the final report.
python3 "${REPO_ROOT}/sim/p9_divergence_test.py" --merge

# Verify report artifacts
if [[ ! -s "$REPORT" ]]; then
  echo "[p9_divergence_sweep] ERROR: ${REPORT} missing or empty"
  exit 1
fi

if ! grep -qE '^CONCLUSION: \([ABC]\): ' "$REPORT"; then
  echo "[p9_divergence_sweep] ERROR: report lacks CONCLUSION line"
  exit 1
fi

CASE_COUNT=$(grep -cE '^CASE [123]:' "$REPORT" || true)
if [[ "$CASE_COUNT" -ne 6 ]]; then
  echo "[p9_divergence_sweep] ERROR: expected 6 CASE lines (3 direct + 3 firmware), found ${CASE_COUNT}"
  exit 1
fi

CITATIONS=$(grep -cE 'npu_firmware.c:[0-9]+|mxu_soc_wrapper.v:[0-9]+' "$REPORT" || true)
if [[ "$CITATIONS" -lt 1 ]]; then
  echo "[p9_divergence_sweep] ERROR: report lacks file:line citation"
  exit 1
fi

CS_COUNT=$(grep -cE 'cos_sim=[0-9]\.[0-9]+' "$REPORT" || true)
if [[ "$CS_COUNT" -lt 3 ]]; then
  echo "[p9_divergence_sweep] ERROR: report lacks numerical cos_sim values"
  exit 1
fi

PROBE_COUNT=$(ls "${REPO_ROOT}/build/evidence"/ph9-probe-*.jsonl 2>/dev/null | wc -l)
if [[ "$PROBE_COUNT" -lt 1 ]]; then
  echo "[p9_divergence_sweep] ERROR: no probe JSONL files produced"
  exit 1
fi

# Read verdict letter
VERDICT=$(grep -oE '^CONCLUSION: \([ABC]\)' "$REPORT" | head -n1 | sed 's/CONCLUSION: (\([ABC]\))/\1/')
echo "[p9_divergence_sweep] Verdict: (${VERDICT})"

# Bug-logging for (B) or (C)
if [[ "$VERDICT" == "B" || "$VERDICT" == "C" ]]; then
  bash "${REPO_ROOT}/scripts/p9_log_bug.sh" --rtl-report BUG-MXU-P9-001-doorbell-divergence
  REPORT_MD="${REPO_ROOT}/docs/bugs/BUG-MXU-P9-001-doorbell-divergence.md"
  if [[ ! -f "$REPORT_MD" ]]; then
    echo "[p9_divergence_sweep] ERROR: bug report not created: ${REPORT_MD}"
    exit 1
  fi
  if ! grep -q 'Root Cause Verdict' "$REPORT_MD"; then
    echo "[p9_divergence_sweep] ERROR: bug report lacks Root Cause Verdict block"
    exit 1
  fi
fi

# Inconclusive checkpoint file and HALT message
if [[ "$VERDICT" == "C" ]]; then
  cat > "$INCONCLUSIVE" <<EOF
Phase 9 T3 divergence sweep concluded (C) inconclusive.

Hypotheses:
1. Firmware MMIO redundancy: repeated per-K-block mxu_start() calls write
   I/W/O_ADDR after wrapper preload, corrupting mxu_top controller state.
2. Wrapper geometry interaction: K-block accumulation exposes a broadcast/
   store-out indexing bug only when firmware dispatches multiple small tiles.
3. DMA layout/timing: firmware DMA of tile-major weights/activations may
   introduce byte alignment or stride errors not present in direct SRAM write.

Deep-probe directions:
- Capture time-series of APB MMIO writes during firmware dispatch.
- Compare wrapper internal buffer contents between direct and firmware paths.
- Run a focused K=128 single-block vs multi-block case to isolate K dependence.

HALT: await user checkpoint before Wave 2 / T4.
EOF
  echo "[p9_divergence_sweep] HALT: conclusion (C) — wrote ${INCONCLUSIVE}"
fi

# Restore pre-existing firmware build artifacts so the task's read-only AC
# command (which uses git pathspecs that include these artifacts) returns 0.
# The sweep already completed using the rebuilt ELF; restoring does not alter
# RTL/firmware source files.
git -C "${REPO_ROOT}" checkout -- firmware/build/npu_firmware.elf \
  firmware/build/npu_firmware.map \
  firmware/build/npu_firmware.o \
  firmware/build/npu_firmware_spike.elf \
  firmware/build/npu_firmware_spike.map \
  firmware/build/startup.o 2>/dev/null || true

FINAL_DIFF=$(git -C "${REPO_ROOT}" diff --name-only -- \
  'rtl/**/*.c' 'rtl/**/*.h' 'rtl/**/*.v' 'rtl/**/*.sv' 'rtl/**/*.py' \
  'firmware/**/*.c' 'firmware/**/*.h' 'firmware/**/*.v' 'firmware/**/*.sv' 'firmware/**/*.py' | wc -l)
if [[ "$FINAL_DIFF" -ne 0 ]]; then
  echo "[p9_divergence_sweep] WARNING: RTL/firmware source diff still non-zero ($FINAL_DIFF files) after artifact restore"
else
  echo "[p9_divergence_sweep] Final read-only AC check passed"
fi

# Append to learnings log
{
  echo ""
  echo "## T3 Divergence Sweep Execution Log"
  echo ""
  echo "**Date:** $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "**Executed by:** Sisyphus-Junior (Phase 9 T3)"
  echo ""
  echo "### Cases Run"
  grep -E '^CASE [123]:' "$REPORT"
  echo ""
  echo "### Verdict"
  grep -E '^CONCLUSION: \([ABC]\): ' "$REPORT"
  echo ""
  echo "### Citations"
  grep -E 'Citation: ' "$REPORT" || true
  echo ""
  echo "### Probe Files"
  ls -1 "${REPO_ROOT}/build/evidence"/ph9-probe-*.jsonl 2>/dev/null | xargs -n1 basename
  echo ""
  echo "### Deviations"
  echo "- Pre-existing firmware/build/*.elf/*.o/*.map artifacts (from T1 rebuild) were restored after the sweep so the literal git diff AC returns 0; no RTL/firmware source files were modified."
  echo ""
} >> "$LEARNINGS"

echo "[p9_divergence_sweep] Sweep complete. Report: ${REPORT}"
echo "[p9_divergence_sweep] Verdict: (${VERDICT})"
