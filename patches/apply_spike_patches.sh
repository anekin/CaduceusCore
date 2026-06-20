#!/bin/bash
# Apply NPU device patches to Spike RISC-V simulator
# Usage: bash patches/apply_spike_patches.sh [spike_src_dir]

set -euo pipefail
SPIKE_DIR="${1:-spike_src}"
PATCH_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Applying NPU patches to Spike ==="
echo "Target: $SPIKE_DIR"

# 1. Apply diff patch
cd "$SPIKE_DIR"
if git apply --check "$PATCH_DIR/spike_npu.patch" 2>/dev/null; then
    git apply "$PATCH_DIR/spike_npu.patch"
    echo "  [OK] spike_npu.patch applied"
else
    echo "  [SKIP] spike_npu.patch already applied or not applicable"
fi

# 2. Copy new source file
cp "$PATCH_DIR/npu_device.cc" riscv/npu_device.cc
echo "  [OK] npu_device.cc copied"

echo "=== Done ==="
