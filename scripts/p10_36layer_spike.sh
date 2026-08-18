#!/usr/bin/env bash
# p10_36layer_spike.sh — todo 12: Spike-first full 36-layer forward pass.
#
# Runs sim/spike_host.py --mode forward --phase10 on sz0001 (Spike + firmware
# + MMIO bridge), saves per-layer hidden states to
# build/evidence/ph10-36layer-spike.npz, compares each layer against the
# Func Model golden with the tolerance ladder (L0-19 >=0.999, L20-29 >=0.998,
# L30-35 >=0.997), and asserts engine=spike.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/p10_lib/p10_sz0001.sh"

EVIDENCE="$REPO_ROOT/build/evidence/task-12-phase10-rtl-verification.txt"
NPZ="$REPO_ROOT/build/evidence/ph10-36layer-spike.npz"
GOLDEN_DIR="$REPO_ROOT/rtl/test_vectors/soc_e2e/qwen25-3b-36layer"
MODEL_PATH="${QWEN3B_GGUF:-$HOME/models/qwen2.5-3b-instruct-q4_k_m.gguf}"
RUN_LOG="$REPO_ROOT/build/evidence/task-12-phase10-run.log"

mkdir -p "$REPO_ROOT/build/evidence"

echo "=== p10_36layer_spike: preflight checks ==="
for l in $(seq 0 35); do
    if [ ! -f "$GOLDEN_DIR/expected_l${l}.npz" ] && [ "$l" -ne 0 ]; then
        echo "ERROR: missing Func Model golden $GOLDEN_DIR/expected_l${l}.npz"
        exit 1
    fi
done
if [ ! -f "$GOLDEN_DIR/expected.npz" ]; then
    echo "ERROR: missing combined golden $GOLDEN_DIR/expected.npz (layer 0 fallback)"
    exit 1
fi
echo "OK: 36 Func Model golden files present under $GOLDEN_DIR"

# Rotate the evidence file (the npz is kept for --resume continuity).
rm -f "$EVIDENCE"
START_TS=$(date +%s)

echo "=== p10_36layer_spike: running 36-layer spike forward on sz0001 ==="
set +e
p10_ssh "cd '$REPO_ROOT' && PYTHONPATH=sim python3 sim/spike_host.py \
  --mode forward --phase10 --layers 36 --token-ids 9707 --seq-len 1 --runs 1 \
  --model '$MODEL_PATH' \
  --save-layer-npz '$NPZ' \
  --golden-dir '$GOLDEN_DIR' \
  --evidence-file '$EVIDENCE' \
  --resume" 2>&1 | tee "$RUN_LOG"
RUN_RC=${PIPESTATUS[0]}
set -e
echo "=== p10_36layer_spike: spike forward exit code = $RUN_RC ==="
if [ "$RUN_RC" -ne 0 ]; then
    echo "FAIL: spike forward exited non-zero"
    exit 1
fi

echo "=== p10_36layer_spike: assertions ==="
FAIL=0
assert() {
    if grep -q "$1" "$EVIDENCE"; then
        echo "ASSERT OK   : $1"
    else
        echo "ASSERT FAIL : $1"
        FAIL=1
    fi
}

if [ ! -f "$EVIDENCE" ]; then
    echo "ASSERT FAIL : evidence file missing: $EVIDENCE"
    exit 1
fi
if [ "$(stat -c %Y "$EVIDENCE")" -lt "$START_TS" ]; then
    echo "ASSERT FAIL : evidence file is stale (not written by this run)"
    exit 1
fi
if [ ! -f "$NPZ" ]; then
    echo "ASSERT FAIL : npz artifact missing: $NPZ"
    exit 1
fi

assert "^engine=spike$"
assert "^layers_run=36$"
assert "^layers_completed=36$"
assert "^fp_window_ok=yes$"
assert "^FP_DRAM_BASE=0x80020000$"
assert "LADDER=PASS"

python3 - "$NPZ" <<'PYEOF'
import json
import sys

import numpy as np

path = sys.argv[1]
with np.load(path, allow_pickle=True) as d:
    meta = json.loads(str(d["metadata"][0]))
    assert meta["engine"] == "spike", f"engine={meta['engine']}"
    assert meta["layers_run"] == 36, f"layers_run={meta['layers_run']}"
    saved = list(meta["layers_saved"])
    assert saved == list(range(36)), f"layers_saved={saved}"
    for L in saved:
        assert f"layer_{L}_output" in d.files, f"missing layer_{L}_output"
        assert f"hw_layer_{L}_output" in d.files, f"missing hw_layer_{L}_output"
print(f"npz OK: engine=spike layers_run=36 layers 0..35 saved ({path})")
PYEOF

N_FAIL=$(grep -c "status=FAIL" "$EVIDENCE" || true)
if [ "$N_FAIL" -ne 0 ]; then
    echo "ASSERT FAIL : $N_FAIL layer(s) below tolerance ladder"
    FAIL=1
else
    echo "ASSERT OK   : no layer below tolerance ladder"
fi

if [ "$FAIL" -ne 0 ]; then
    echo "=== p10_36layer_spike: FAIL ==="
    exit 1
fi
echo "=== p10_36layer_spike: ALL ASSERTIONS PASS ==="
exit 0
