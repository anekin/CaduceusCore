#!/usr/bin/env bash
# p10_36layer_ibex.sh — todo 13: Ibex 36-layer checkpoint-subset segment run.
#
# Runs the 9-layer Ibex subset (L0 | L9->L10 | L19->L20 | L29->L30 | L34->L35)
# in one VCS session on sz0001.  Consecutive layers in a segment chain their
# hidden state through Ibex DRAM (chain_restart_state_source=ibex_dram); only a
# segment's first-layer input is loaded from the Spike npz
# (segment_input_source=spike_npz).  Checkpoint layers L0/L10/L20/L30/L35 are
# compared against the Func Model golden with the tolerance ladder.
#
# The compile + run is delegated to sim/regression/run_ibex_segment_run.sh
# (executed on sz0001 via p10_ssh); this script performs preflight and the
# evidence assertions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/p10_lib/p10_sz0001.sh"

EVIDENCE="$REPO_ROOT/build/evidence/task-13-phase10-rtl-verification.txt"
NPZ="$REPO_ROOT/build/evidence/ph10-36layer-ibex-checkpoints.npz"
SPIKE_NPZ="$REPO_ROOT/build/evidence/ph10-36layer-spike.npz"
GOLDEN_DIR="$REPO_ROOT/rtl/test_vectors/soc_e2e/qwen25-3b-36layer"
MODEL_PATH="${QWEN3B_GGUF:-$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf}"
RUN_LOG="$REPO_ROOT/build/evidence/task-13-phase10-run.log"

mkdir -p "$REPO_ROOT/build/evidence"

echo "=== p10_36layer_ibex: preflight checks ==="
[ -f "$SPIKE_NPZ" ] || { echo "ERROR: missing spike npz: $SPIKE_NPZ"; exit 1; }
[ -f "$REPO_ROOT/firmware/build/npu_firmware.hex" ] || { echo "ERROR: missing firmware hex"; exit 1; }
for L in 0 10 20 30 35; do
    if [ "$L" -eq 0 ]; then G="$GOLDEN_DIR/expected.npz"; else G="$GOLDEN_DIR/expected_l${L}.npz"; fi
    [ -f "$G" ] || { echo "ERROR: missing golden $G"; exit 1; }
done
echo "OK: spike npz, firmware hex, golden checkpoints present"
echo "OK: model path = $MODEL_PATH"

rm -f "$EVIDENCE" "$NPZ" "$RUN_LOG"
START_TS=$(date +%s)

echo "=== p10_36layer_ibex: running segment run on sz0001 ==="
set +e
p10_ssh "bash sim/regression/run_ibex_segment_run.sh" 2>&1 | tee "$RUN_LOG"
RUN_RC=${PIPESTATUS[0]}
set -e
echo "=== p10_36layer_ibex: runner exit code = $RUN_RC ==="
if [ "$RUN_RC" -ne 0 ]; then
    echo "FAIL: segment-run runner exited non-zero (log: $RUN_LOG)"
    exit 1
fi

echo "=== p10_36layer_ibex: assertions ==="
FAIL=0
assert() {
    if grep -qF "$1" "$EVIDENCE" 2>/dev/null; then
        echo "ASSERT OK   : $1"
    else
        echo "ASSERT FAIL : $1"
        FAIL=1
    fi
}

[ -f "$EVIDENCE" ] || { echo "ASSERT FAIL : evidence file missing: $EVIDENCE"; exit 1; }
[ "$(stat -c %Y "$EVIDENCE")" -ge "$START_TS" ] || { echo "ASSERT FAIL : evidence file stale"; exit 1; }
[ -f "$NPZ" ] || { echo "ASSERT FAIL : npz artifact missing: $NPZ"; exit 1; }

assert "engine=ibex"
assert "ibex_executed=L0,L9,L10,L19,L20,L29,L30,L34,L35"
assert "checkpoints=L0,L10,L20,L30,L35"
assert "chain_restart=true"
assert "chain_restart_state_source=ibex_dram"
assert "segment_input_source=spike_npz"
assert "LADDER=PASS"

if grep -E "engine=ibex cos_sim=.*status=FAIL" "$EVIDENCE" >/dev/null; then
    echo "ASSERT FAIL : a checkpoint is below its tolerance ladder threshold"
    FAIL=1
else
    echo "ASSERT OK   : all checkpoints pass tolerance ladder"
fi

echo "--- cross-checks (non-gating, recorded) ---"
grep -E "ibex_vs_spike_cos_sim=" "$EVIDENCE" || echo "(none)"

python3 - "$NPZ" <<'PYEOF'
import json
import sys

import numpy as np

path = sys.argv[1]
expect = [0, 9, 10, 19, 20, 29, 30, 34, 35]
with np.load(path, allow_pickle=True) as d:
    meta = json.loads(str(d["metadata"][0]))
    assert meta["engine"] == "ibex", f"engine={meta['engine']}"
    assert meta["chain_restart"] is True, "chain_restart not True"
    assert meta["chain_restart_state_source"] == "ibex_dram"
    assert meta["segment_input_source"] == "spike_npz"
    assert meta["layers_saved"] == expect, f"layers_saved={meta['layers_saved']}"
    for L in expect:
        assert f"layer_{L}_output" in d.files, f"missing layer_{L}_output"
        assert f"hw_layer_{L}_output" in d.files, f"missing hw_layer_{L}_output"
print(f"npz OK: engine=ibex layers_saved={expect} chain_restart_state_source=ibex_dram ({path})")
PYEOF

if [ "$FAIL" -ne 0 ]; then
    echo "=== p10_36layer_ibex: FAIL ==="
    exit 1
fi
echo "=== p10_36layer_ibex: ALL ASSERTIONS PASS ==="
exit 0
