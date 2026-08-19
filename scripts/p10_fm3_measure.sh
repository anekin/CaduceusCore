#!/usr/bin/env bash
# =============================================================================
# p10_fm3_measure.sh — todo 15: FM-3 weight-streaming overlap RTL measurement
# =============================================================================
# Measures the weight-streaming overlap ratio (DMA preload vs MXU compute) on
# RTL using a targeted Ibex SoC VCS run with Q4_K_M weights (default model
# qwen2.5-3b-instruct-q4_k_m.gguf, which is Phase-9-configured; Q8_0 is not
# used because its download failed — this measurement must not depend on it).
#
# The targeted run dispatches ONE weight-streaming MMUL (blk.0.attn_q.weight
# slice, default M=1 K=512 N=256 → 8 K-blocks × 4 N-tiles through the firmware
# ping-pong weight-streaming loop) via the on-chip Ibex firmware, while a
# cycle-accurate sampler records DMA STATUS.BUSY and MXU controller busy with
# per-transfer classification.  It is independent of todo 13/14: it does not
# require the 36-layer segment-run evidence (whose per-wave cycles carry no
# DMA/MXU event detail); if that evidence exists it is recorded as a
# non-gating reference.
#
# Outputs:
#   build/evidence/task-15-phase10-rtl-verification.txt   (overlap_ratio=X.XX)
#   build/evidence/fm3-cycle-trace.csv                    (raw cycle trace)
#   build/evidence/task-15-phase10-run.log                (VCS/cocotb log)
#
# Exit codes:
#   0  measurement succeeded, evidence + trace written and asserted
#   1  preflight failure, VCS run failure, or missing/invalid evidence
#
# Usage (from repo root, on sz0002; drives sz0001 via p10_ssh):
#   bash scripts/p10_fm3_measure.sh
#   QWEN3B_GGUF=/path/to/q4_k_m.gguf FM3_K=1024 FM3_N=256 bash scripts/p10_fm3_measure.sh
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Source Phase-10 sz0001 helpers (defines p10_ssh, p10_chmod, REPO_ROOT)
source "$SCRIPT_DIR/p10_lib/p10_sz0001.sh"

EVIDENCE="$REPO_ROOT/build/evidence/task-15-phase10-rtl-verification.txt"
TRACE="$REPO_ROOT/build/evidence/fm3-cycle-trace.csv"
RUN_LOG="$REPO_ROOT/build/evidence/task-15-phase10-run.log"
T13_EVIDENCE="$REPO_ROOT/build/evidence/task-13-phase10-rtl-verification.txt"
SIMV="$REPO_ROOT/build/ibex_full_rtl/simv_soc_ibex"
FW_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex"
MEAS_PY="$REPO_ROOT/sim/p10_fm3_measure.py"
# Q4_K_M weights (Phase 9 configured); Q8_0 download failed and must NOT gate
# this measurement.
MODEL="${QWEN3B_GGUF:-$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf}"
FM3_K="${FM3_K:-512}"
FM3_N="${FM3_N:-256}"
COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"

mkdir -p "$REPO_ROOT/build/evidence"

echo "=== p10_fm3_measure: preflight checks ==="
[ -f "$MEAS_PY" ] || { echo "ERROR: missing measurement module: $MEAS_PY"; exit 1; }
[ -f "$FW_HEX" ] || { echo "ERROR: missing firmware hex: $FW_HEX (run: make -C firmware)"; exit 1; }
p10_ssh "[ -x '$SIMV' ]" || {
    echo "ERROR: simv not found on sz0001: $SIMV"
    echo "  Run: bash sim/regression/run_ibex_full_rtl.sh FM-SOC-001"
    exit 1
}
p10_ssh "[ -f '$MODEL' ]" || {
    echo "ERROR: Q4_K_M model missing on sz0001: $MODEL"
    echo "  Override with QWEN3B_GGUF=/path/to/qwen2.5-3b-instruct-q4_k_m.gguf"
    exit 1
}
p10_chmod "$MEAS_PY"
echo "OK: simv, firmware hex, Q4_K_M model present on sz0001"
echo "OK: measurement shape M=1 K=$FM3_K N=$FM3_N (blk.0.attn_q.weight slice, Q4_K_M)"
echo "NOTE: targeted measurement is independent of todo 13 (no blocking on segment run)"

rm -f "$EVIDENCE" "$TRACE" "$RUN_LOG"
START_TS=$(date +%s)

echo "=== p10_fm3_measure: running targeted Ibex VCS measurement on sz0001 ==="
# RTL $readmemh paths resolve relative to the repository parent directory.
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
set +e
p10_ssh "
export PYTHONPATH=\"\${PYTHONPATH:-}:$REPO_ROOT\"
export MODULE=sim.p10_fm3_measure
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export BOOTROM_HEX=\"$FW_HEX\"
export TESTCASE=test_fm3_overlap_measure
export QWEN3B_GGUF=\"$MODEL\"
export FM3_K=\"$FM3_K\"
export FM3_N=\"$FM3_N\"
export IBEX_COMMIT=\"$COMMIT\"
cd \"$RUN_DIR\"
\"$SIMV\" +COCOTB +BOOTROM_HEX=\"$FW_HEX\" -l \"$RUN_LOG\" > \"$RUN_LOG\" 2>&1
" 2>&1 | tail -40
RUN_RC=${PIPESTATUS[0]}
set -e
echo "=== p10_fm3_measure: runner exit code = $RUN_RC ==="
# cocotb test failures do not propagate to simv's exit code; gate on the summary line instead.
if [ "$RUN_RC" -ne 0 ] || ! grep -qE 'TESTS=1 PASS=1 FAIL=0' "$RUN_LOG" 2>/dev/null; then
    echo "FAIL: targeted measurement run failed (exit=$RUN_RC, log: $RUN_LOG)"
    tail -40 "$RUN_LOG" 2>/dev/null || true
    exit 1
fi

echo "=== p10_fm3_measure: assertions ==="
[ -f "$EVIDENCE" ] || { echo "ASSERT FAIL : evidence missing: $EVIDENCE"; exit 1; }
[ "$(stat -c %Y "$EVIDENCE")" -ge "$START_TS" ] || { echo "ASSERT FAIL : evidence stale"; exit 1; }
[ -f "$TRACE" ] || { echo "ASSERT FAIL : raw cycle trace missing: $TRACE"; exit 1; }

grep -qE '^overlap_ratio=[0-9]+\.[0-9]{2}$' "$EVIDENCE" \
    || { echo "ASSERT FAIL : overlap_ratio=X.XX line missing/invalid"; exit 1; }
grep -qE '^raw_trace=.*fm3-cycle-trace\.csv$' "$EVIDENCE" \
    || { echo "ASSERT FAIL : raw trace path missing in evidence"; exit 1; }
grep -qE '^engine=ibex$' "$EVIDENCE" \
    || { echo "ASSERT FAIL : engine=ibex missing"; exit 1; }
grep -qE '^weight_quant=Q4_K_M$' "$EVIDENCE" \
    || { echo "ASSERT FAIL : weight_quant=Q4_K_M missing"; exit 1; }

# Ratio must be a valid probability in [0, 1].
RATIO="$(grep -E '^overlap_ratio=' "$EVIDENCE" | head -1 | cut -d= -f2)"
echo "$RATIO" | awk '{ if ($1 < 0 || $1 > 1) exit 1 }' \
    || { echo "ASSERT FAIL : overlap_ratio out of range: $RATIO"; exit 1; }
echo "OK: overlap_ratio=$RATIO"

# Failure mode per plan: RTL trace missing DMA/MXU events → exit 1.
grep -qE '^[0-9]+,1,[01],' "$TRACE" \
    || { echo "ASSERT FAIL : trace lacks DMA busy events"; exit 1; }
grep -qE '^[0-9]+,[01],1,' "$TRACE" \
    || { echo "ASSERT FAIL : trace lacks MXU busy events"; exit 1; }
grep -qE '^[0-9]+,1,[01],weight,' "$TRACE" \
    || { echo "ASSERT FAIL : trace lacks weight-preload DMA events"; exit 1; }
echo "OK: trace contains DMA + MXU + weight-preload events ($(wc -l < "$TRACE") lines)"

# Non-gating: record todo 13 segment-run reference if it already exists.
echo "--- segment-run reference (todo 13, non-gating) ---"
if [ -f "$T13_EVIDENCE" ]; then
    {
        echo ""
        echo "Segment-run reference (todo 13, non-gating context):"
        grep -E '^(engine=|ibex_executed=|commands_dispatched=|elapsed_s=)' "$T13_EVIDENCE" \
            | sed 's/^/  /' || true
    } >> "$EVIDENCE"
else
    echo "  (todo 13 evidence not yet available — FM-3 measurement performed independently)"
fi

echo "=== p10_fm3_measure: ALL ASSERTIONS PASS ==="
echo "  evidence : $EVIDENCE"
echo "  raw trace: $TRACE"
echo "  run log  : $RUN_LOG"
exit 0
