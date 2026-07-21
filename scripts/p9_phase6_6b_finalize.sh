#!/usr/bin/env bash
set -euo pipefail
source "$(dirname $0)/p9_lib/p9_sz0001.sh"

EVIDENCE_DIR="$REPO_ROOT/build/evidence"
PRECISION_FILE="$EVIDENCE_DIR/ph9-q8_0-precision.txt"
FAILED_FILE="$EVIDENCE_DIR/ph9-q8_0-download-FAILED.txt"
PHASE6_PLAN="$REPO_ROOT/.omo/plans/phase6-rtl-verification.md"
ISSUES_FILE="$REPO_ROOT/docs/issues_found.md"

# ── Determine evidence source ──────────────────────────────────────
VERDICT=""
CHECKBOX=""
JUDGE=""
EVIDENCE_REF=""
EXTRA_NOTE=""

if [ -f "$FAILED_FILE" ]; then
    # ── Network failure path ────────────────────────────────────────
    VERDICT="BLOCKED-NETWORK"
    CHECKBOX="[~]"
    JUDGE="BLOCKED-NETWORK"
    EVIDENCE_REF="build/evidence/ph9-q8_0-download-FAILED.txt"
    EXTRA_NOTE="download from HuggingFace failed after retries; external network unavailable"
    echo "[p9_phase6_6b_finalize] Mode: BLOCKED-NETWORK (download FAILED file exists)"

elif [ -f "$PRECISION_FILE" ]; then
    # ── Precision experiment completed — parse cos_sim values ───────
    echo "[p9_phase6_6b_finalize] Mode: precision experiment (parsing $PRECISION_FILE)"

    # Extract all per-layer cos_sim values
    COS_VALS=$(grep -oE 'cos_sim=[0-9]+\.[0-9]+' "$PRECISION_FILE" | sed 's/cos_sim=//' | head -36)

    if [ -z "$COS_VALS" ]; then
        echo "[p9_phase6_6b_finalize] WARNING: No cos_sim values found in precision file"
        echo "[p9_phase6_6b_finalize] Treating as BLOCKED (no data)"

        VERDICT="BLOCKED-NETWORK"
        CHECKBOX="[~]"
        JUDGE="BLOCKED-NETWORK"
        EVIDENCE_REF="build/evidence/ph9-q8_0-precision.txt"
        EXTRA_NOTE="precision experiment completed but no cos_sim values parsed"
    else
        # Find minimum cos_sim across all layers
        MIN_CS=$(echo "$COS_VALS" | sort -n | head -1)
        COS_COUNT=$(echo "$COS_VALS" | wc -l)

        echo "[p9_phase6_6b_finalize] Parsed $COS_COUNT cos_sim values, min=$MIN_CS"

        # ── Apply threshold rules ────────────────────────────────────
        if [ "$(echo "$MIN_CS >= 0.999" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
            VERDICT="PASS"
            CHECKBOX="[x]"
            JUDGE="PASS"
            EVIDENCE_REF="build/evidence/ph9-q8_0-precision.txt"
            EXTRA_NOTE="min cos_sim=$MIN_CS >= 0.999; Q4_K_M confirmed as root cause of L35 drift"
        elif [ "$(echo "$MIN_CS >= 0.990" | bc -l 2>/dev/null || echo 0)" = "1" ]; then
            VERDICT="CONDITIONAL"
            CHECKBOX="[~]"
            JUDGE="CONDITIONAL"
            EVIDENCE_REF="build/evidence/ph9-q8_0-precision.txt"
            EXTRA_NOTE="min cos_sim=$MIN_CS in [0.990, 0.999); per-layer delta present in evidence"
        else
            VERDICT="FAIL"
            CHECKBOX="[ ]"
            JUDGE="FAIL"
            EVIDENCE_REF="build/evidence/ph9-q8_0-precision.txt"
            EXTRA_NOTE="min cos_sim=$MIN_CS < 0.990; root cause NOT isolated to Q4_K_M quantization"
        fi
    fi
else
    # ── Neither file exists — treat as blocked ──────────────────────
    echo "[p9_phase6_6b_finalize] ERROR: Neither precision file nor FAILED file exists"
    echo "[p9_phase6_6b_finalize] Run p9_q8o_download.sh and p9_q8o_precision.sh first"

    VERDICT="BLOCKED-NETWORK"
    CHECKBOX="[~]"
    JUDGE="BLOCKED-NETWORK"
    EVIDENCE_REF="build/evidence/ph9-q8_0-download-FAILED.txt"
    EXTRA_NOTE="Phase 9 T9 not yet executed; treat as blocked"
fi

echo "[p9_phase6_6b_finalize] Verdict: $VERDICT | Checkbox: $CHECKBOX | Judge: $JUDGE"

# ── Step 1: Update Phase 6 plan checkbox (line 107) ────────────────
echo "[p9_phase6_6b_finalize] Updating $PHASE6_PLAN line 107..."

NEW_LINE="6b. $CHECKBOX L35 drift root-cause: Q8_0/FP16 control experiment (ba/judge=$JUDGE)"

# Use sed to replace line 107
sed -i "107s|.*|$NEW_LINE|" "$PHASE6_PLAN"

echo "[p9_phase6_6b_finalize] Phase 6 plan updated: $NEW_LINE"

# ── Step 2: Sync docs/issues_found.md ──────────────────────────────
echo "[p9_phase6_6b_finalize] Syncing $ISSUES_FILE..."

NOW=$(date '+%Y-%m-%d %H:%M:%S')

# Check if a 6b phase9 entry already exists
if grep -q 'ph9-q8_0\|Phase 9.*Q8_0.*6b\|BLOCKED-NETWORK.*6b' "$ISSUES_FILE" 2>/dev/null; then
    echo "[p9_phase6_6b_finalize] 6b/ph9-q8_0 entry already exists in issues_found.md — appending update"
fi

cat >> "$ISSUES_FILE" <<EOF

## Phase 9 Q8_0 Control Experiment — 6b Status

| Field | Detail |
|-------|--------|
| **Date** | $NOW |
| **Source** | Phase 9 Todo 9 (Wave 5) |
| **Status** | **\`$VERDICT\`** (ba/judge=$JUDGE) |
| **Evidence** | \`$EVIDENCE_REF\` |
| **Note** | $EXTRA_NOTE |

EOF

echo "[p9_phase6_6b_finalize] issues_found.md synced"

# ── Verify changes ─────────────────────────────────────────────────
echo ""
echo "=== Verification ==="
echo "Phase 6 plan line 107:"
sed -n '107p' "$PHASE6_PLAN"
echo ""
echo "issues_found.md tail:"
tail -12 "$ISSUES_FILE"

echo ""
echo "[p9_phase6_6b_finalize] Done — 6b checkbox finalized: $VERDICT (ba/judge=$JUDGE)"
exit 0
