#!/usr/bin/env bash
set -euo pipefail
# p9_sram_budget.sh — SRAM budget pre-check for K=2560 Q_proj within 4MB
# Phase 9 Todo 6 step 1. Computes peak SRAM usage per the firmware dynamic layout
# and asserts total < 4MB. Writes PASS/FAIL to build/evidence/ph9-sram-budget.txt.
source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

EVIDENCE="${REPO_ROOT}/build/evidence/ph9-sram-budget.txt"
OVERFLOW="${REPO_ROOT}/build/evidence/ph9-sram-overflow.txt"
mkdir -p "$(dirname "$EVIDENCE")"

echo "[p9_sram_budget] Computing SRAM budget for Q_proj K=2560..."

# ── Constants from firmware/npu_firmware.c ──────────────────────────────
TILE_H=64
TILE_W=64
TILE_WEIGHT_BYTES=$((TILE_H * TILE_W / 2))   # 2048B INT4 packed
TILE_SCALE_BYTES=$((TILE_W * 4))              # 256B FP16
SRAM_ALIGN=64
SRAM_SIZE=$((4 * 1024 * 1024))                # 4MB

# ── Per-K-tile buffers (ping-pong double-buffered) ─────────────────────
WBUF_DOUBLE=$((2 * TILE_WEIGHT_BYTES))        # 4096B
SBUF_DOUBLE=$((2 * TILE_SCALE_BYTES))         # 512B

# ── Q_proj dimensions: K=2560, N=4096, M=1 (single token) ─────────────
K=2560
N=4096
M=1

# Activation: M*K INT8 bytes
ACT_BYTES=$((M * K))
ACT_ALIGNED=$(((ACT_BYTES + SRAM_ALIGN - 1) / SRAM_ALIGN * SRAM_ALIGN))

# Weight base region: act_end + ping-pong
WEIGHT_BASE=$((ACT_ALIGNED + WBUF_DOUBLE))
SCALE_BASE=$(((WEIGHT_BASE + SRAM_ALIGN - 1) / SRAM_ALIGN * SRAM_ALIGN))
SCALE_END=$((SCALE_BASE + SBUF_DOUBLE))
OUT_BASE=$(((SCALE_END + SRAM_ALIGN - 1) / SRAM_ALIGN * SRAM_ALIGN))

# Output per N-tile: M * tile_width * 4 (INT32)
TILE_WIDTH=$((TILE_W < N ? TILE_W : N))   # min(64, N) for partial tile
OUT_BYTES=$((M * TILE_WIDTH * 4))

PEAK=$((OUT_BASE + OUT_BYTES))

echo "[p9_sram_budget] act=${ACT_BYTES}B (aligned ${ACT_ALIGNED})"
echo "[p9_sram_budget] wbuf_double=${WBUF_DOUBLE}B sbuf_double=${SBUF_DOUBLE}B"
echo "[p9_sram_budget] out_base=0x$(printf '%x' "$OUT_BASE") out_bytes=${OUT_BYTES}B"
echo "[p9_sram_budget] PEAK=${PEAK}B / ${SRAM_SIZE}B (4MB)"

# Also verify worst-case: K=2560 with larger M that could fit in SRAM
# Find max M that fits: act = M*2560, rest ≈ 5120B overhead
OVERHEAD=$((WBUF_DOUBLE + SBUF_DOUBLE + (TILE_WIDTH * 4)))  # ~4864B base overhead
AVAIL_FOR_ACT=$((SRAM_SIZE - OVERHEAD))
MAX_M=$((AVAIL_FOR_ACT / K))

{
  echo "=== CaduceusCore Phase 9 T6 SRAM Budget Pre-Check ==="
  echo "Date: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo ""

  if ((PEAK < SRAM_SIZE)); then
    echo "PASS: peak SRAM ${PEAK}B < 4MB (${SRAM_SIZE}B)"
    echo "K=2560, N=4096, M=1: peak=${PEAK}B, headroom=$((SRAM_SIZE - PEAK))B"
    echo "Maximum M for K=2560 that fits in 4MB: ${MAX_M}"
    echo "overhead_breakdown: wbuf_double=${WBUF_DOUBLE}B sbuf_double=${SBUF_DOUBLE}B output_per_tile=${OUT_BYTES}B"
  else
    echo "FAIL: peak SRAM ${PEAK}B >= 4MB (${SRAM_SIZE}B)"
    touch "$OVERFLOW"
    echo "SRAM_OVERFLOW=1 peak=${PEAK}B limit=${SRAM_SIZE}B" >> "$OVERFLOW"
    exit 1
  fi
} > "$EVIDENCE"

echo "[p9_sram_budget] PASS: evidence written to ${EVIDENCE}"
exit 0
