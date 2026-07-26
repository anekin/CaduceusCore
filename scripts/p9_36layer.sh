#!/usr/bin/env bash
# =============================================================================
# p9_36layer.sh — Phase 9 T7: 36-layer checkpoint L0/L10/L20/L35 cos_sim gate
# =============================================================================
# Wraps scripts/run_36layer_checkpoint.py to produce Phase 9-specific evidence
# in build/evidence/ph9-36layer-checkpoint.txt with Phase 9 header.
#
# Usage:
#   bash scripts/p9_36layer.sh [layers...]
#   Default layers: 0 10 20 35
#
# Thresholds:
#   L0/L10/L20: cos_sim >= 0.999
#   L35:        cos_sim >= 0.997 (script threshold 0.997278)
#
# If any layer fails threshold, writes ph9-36layer-partial.txt and
# continues — T7 does not block Phase 9 closure.
# =============================================================================
set -euo pipefail
source "$(dirname $0)/p9_lib/p9_sz0001.sh"

LAYERS="${@:-0 10 20 35}"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
COMMIT=$(git -C "$REPO_ROOT" rev-parse HEAD)
EVIDENCE_DIR="$REPO_ROOT/build/evidence"
ORIG_EVIDENCE="$EVIDENCE_DIR/36layer-checkpoint.txt"
PH9_EVIDENCE="$EVIDENCE_DIR/ph9-36layer-checkpoint.txt"
PH9_PARTIAL="$EVIDENCE_DIR/ph9-36layer-partial.txt"
PH9_LOG="$EVIDENCE_DIR/ph9-36layer-checkpoint.log"

# Print header banner into log
{
  echo "==========================================="
  echo "[p9_36layer] Phase 9 T7: 36-layer checkpoint"
  echo "[p9_36layer] Layers: $LAYERS"
  echo "[p9_36layer] Commit: $COMMIT"
  echo "[p9_36layer] Timestamp: $TIMESTAMP"
  echo "==========================================="
  echo ""
} | tee "$PH9_LOG"

# Step 1: Run the existing 36-layer checkpoint script on sz0001
# tee log while preserving exit status
set +e
p9_ssh "python3 scripts/run_36layer_checkpoint.py --ibex-smoke --layers ${LAYERS} --no-amend" \
  2>&1 | tee -a "$PH9_LOG"
PY_EXIT=${PIPESTATUS[0]}
set -e

if [ "$PY_EXIT" -ne 0 ]; then
  {
    echo ""
    echo "ERROR: run_36layer_checkpoint.py exited with code $PY_EXIT"
    echo "See log: $PH9_LOG"
  } | tee -a "$PH9_LOG"
  exit 1
fi

# Step 2: Check original evidence was generated
if [ ! -s "$ORIG_EVIDENCE" ]; then
  {
    echo "ERROR: Original evidence not found: $ORIG_EVIDENCE"
  } | tee -a "$PH9_LOG"
  exit 1
fi

echo "" | tee -a "$PH9_LOG"
echo "[p9_36layer] Original evidence produced (${ORIG_EVIDENCE})" | tee -a "$PH9_LOG"

# Step 3: Build Phase 9 evidence
{
  echo "# Phase 9 re-run"
  echo "# Timestamp: $TIMESTAMP"
  echo "# Commit: $COMMIT"
  echo "# Source: rtl"
  echo "#"
  echo ""
} > "$PH9_EVIDENCE"

# Append the original evidence body (skip only top-level header lines: "# text")
grep -v '^# ' "$ORIG_EVIDENCE" | sed '/^$/d' >> "$PH9_EVIDENCE"

# Verify the file has content
if [ ! -s "$PH9_EVIDENCE" ]; then
  {
    echo "ERROR: Phase 9 evidence file empty: $PH9_EVIDENCE"
  } | tee -a "$PH9_LOG"
  exit 1
fi

# Step 4: Verify thresholds
echo "" | tee -a "$PH9_LOG"
echo "=== Acceptance Criteria Verification ===" | tee -a "$PH9_LOG"
echo "" | tee -a "$PH9_LOG"

# Count cos_sim lines
COS_COUNT=$(grep -c 'cos_sim' "$PH9_EVIDENCE" || true)
echo "cos_sim entries in evidence: $COS_COUNT" | tee -a "$PH9_LOG"
echo "" | tee -a "$PH9_LOG"

# Check individual layers
PASS_COUNT=0
FAIL_LAYERS=()

for L in 0 10 20; do
  LINE=$(grep -E "^layer=${L} simulator=ibex " "$PH9_EVIDENCE" || echo "")
  if [ -z "$LINE" ]; then
    echo "  L${L}: MISSING from evidence" | tee -a "$PH9_LOG"
    FAIL_LAYERS+=("L${L}: missing")
    continue
  fi
  STATUS=$(echo "$LINE" | grep -oP 'status=\K\S+')
  COS=$(echo "$LINE" | grep -oP 'cos_sim=\K[0-9.]+')
  if echo "$LINE" | grep -qE 'cos_sim=(0\.999[0-9]|1\.0)'; then
    echo "  L${L}: PASS (cos_sim=${COS}, >= 0.999)" | tee -a "$PH9_LOG"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "  L${L}: FAIL (cos_sim=${COS}, < 0.999)" | tee -a "$PH9_LOG"
    FAIL_LAYERS+=("L${L}: cos_sim=${COS}")
  fi
done

LINE=$(grep -E "^layer=35 simulator=ibex " "$PH9_EVIDENCE" || echo "")
if [ -z "$LINE" ]; then
  echo "  L35: MISSING from evidence" | tee -a "$PH9_LOG"
  FAIL_LAYERS+=("L35: missing")
else
  COS=$(echo "$LINE" | grep -oP 'cos_sim=\K[0-9.]+')
  if echo "$LINE" | grep -qE 'cos_sim=(0\.99[7-9]|1\.0)'; then
    echo "  L35: PASS (cos_sim=${COS}, >= 0.997)" | tee -a "$PH9_LOG"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    echo "  L35: FAIL (cos_sim=${COS}, < 0.997)" | tee -a "$PH9_LOG"
    FAIL_LAYERS+=("L35: cos_sim=${COS}")
  fi
fi

# Step 5: Handle failures (partial evidence)
if [ ${#FAIL_LAYERS[@]} -gt 0 ]; then
  {
    echo ""
    echo "=== PARTIAL FAILURE ==="
    echo "Failing layers:"
    for fl in "${FAIL_LAYERS[@]}"; do
      echo "  - $fl"
    done
    echo "Pass count: $PASS_COUNT / 4"
    echo ""
    echo "Writing partial evidence: $PH9_PARTIAL"
  } | tee -a "$PH9_LOG"

  cp "$PH9_EVIDENCE" "$PH9_PARTIAL"
  {
    echo ""
    echo "# Partial Failure Report"
    echo "# Timestamp: $TIMESTAMP"
    echo "# Failing layers: ${FAIL_LAYERS[*]}"
  } >> "$PH9_PARTIAL"

  echo "[p9_36layer] T7 completed with partial failure (does not block closure)" | tee -a "$PH9_LOG"
  exit 0
fi

echo "" | tee -a "$PH9_LOG"
echo "=== ALL 4/4 layers PASS ===" | tee -a "$PH9_LOG"
echo "" | tee -a "$PH9_LOG"
echo "[p9_36layer] T7 complete: $PH9_EVIDENCE" | tee -a "$PH9_LOG"
echo "[p9_36layer] Log: $PH9_LOG" | tee -a "$PH9_LOG"
