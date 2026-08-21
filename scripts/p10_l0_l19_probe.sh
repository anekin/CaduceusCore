#!/usr/bin/env bash
# p10_l0_l19_probe.sh — todo 13 L19 root-cause probe wrapper.
#
# Runs the L0 -> L19 in-one-session probe (sim/rtl_soc_l0_l19_probe.py) on
# sz0001 with per-wave DRAM readbacks, then asserts the probe evidence file
# was produced.  Uses its own simv (simv_soc_ibex_probe) and never touches a
# concurrently running full segment run (simv_soc_ibex_seg).
#
# Usage:
#   bash scripts/p10_l0_l19_probe.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/p10_lib/p10_sz0001.sh"

EVIDENCE="$REPO_ROOT/build/evidence/l0l19-probe-evidence.txt"
JSON="$REPO_ROOT/build/evidence/l0l19-probe.json"
PROGRESS="$REPO_ROOT/build/evidence/l0l19-probe-progress.log"
RUN_LOG="$REPO_ROOT/build/evidence/task-13-l0l19-probe-run.log"

mkdir -p "$REPO_ROOT/build/evidence"

echo "=== p10_l0_l19_probe: preflight checks ==="
[ -f "$REPO_ROOT/build/evidence/ph10-36layer-spike.npz" ] || \
    { echo "ERROR: missing spike npz"; exit 1; }
[ -f "$REPO_ROOT/firmware/build/npu_firmware.hex" ] || \
    { echo "ERROR: missing firmware hex"; exit 1; }
[ -f "$REPO_ROOT/build/evidence/ph10-36layer-ibex-checkpoints-run1.npz" ] || \
    echo "WARNING: run1 checkpoint npz missing (cos_vs_run1_garbage will be nan)"
echo "OK: spike npz + firmware hex present"

rm -f "$EVIDENCE" "$JSON" "$PROGRESS"
START_TS=$(date +%s)

echo "=== p10_l0_l19_probe: running probe on sz0001 (compile + run) ==="
set +e
p10_ssh "MODULE=sim.rtl_soc_l0_l19_probe TESTCASE=test_l0_l19_probe \
QWEN3B_GGUF=$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf \
bash sim/regression/run_l0_l19_probe.sh" 2>&1 | tee "$RUN_LOG"
RUN_RC=${PIPESTATUS[0]}
set -e

echo "=== p10_l0_l19_probe: runner exit code = $RUN_RC ==="
if [ "$RUN_RC" -ne 0 ]; then
    echo "FAIL: probe runner exited non-zero (log: $RUN_LOG)"
    exit 1
fi

echo "=== p10_l0_l19_probe: assertions ==="
FAIL=0
[ -f "$EVIDENCE" ] || { echo "ASSERT FAIL : evidence file missing: $EVIDENCE"; FAIL=1; }
[ "$(stat -c %Y "$EVIDENCE" 2>/dev/null || echo 0)" -ge "$START_TS" ] || \
    { echo "ASSERT FAIL : evidence file stale"; FAIL=1; }
[ -f "$JSON" ] || { echo "ASSERT FAIL : per-wave json missing: $JSON"; FAIL=1; }
grep -qF "PROBE-RUN-COMPLETE" "$EVIDENCE" 2>/dev/null && \
    echo "ASSERT OK   : PROBE-RUN-COMPLETE in evidence" || \
    { echo "ASSERT FAIL : PROBE-RUN-COMPLETE missing"; FAIL=1; }

echo "--- per-wave L19 readback cos summary ---"
grep -E "^wave=" "$EVIDENCE" 2>/dev/null || echo "(none)"
echo "--- final L19 comparisons ---"
grep -E "l_out vs" "$EVIDENCE" 2>/dev/null || echo "(none)"

if [ "$FAIL" -ne 0 ]; then
    echo "=== p10_l0_l19_probe: FAIL ==="
    exit 1
fi
echo "=== p10_l0_l19_probe: COMPLETE ==="
exit 0
