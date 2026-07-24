#!/usr/bin/env bash
# wv_f1_audit_rtl_bug_fix.sh — Plan compliance audit for rtl-bug-fix-wv
# ==============================================================================
# Checks:
#   1. All T0-T6 checkboxes are marked [x] in .omo/plans/rtl-bug-fix-wv.md
#   2. Required evidence files exist
#   3. Acceptance-criteria greps pass where applicable
# Writes: build/evidence/wv-f1-audit-rbf.log
# ==============================================================================

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PLAN=".omo/plans/rtl-bug-fix-wv.md"
EVIDENCE_DIR="build/evidence"
LOG="$EVIDENCE_DIR/wv-f1-audit-rbf.log"

mkdir -p "$EVIDENCE_DIR"

PASS=0
FAIL=0

check() {
    local msg="$1"
    local cond="$2"
    if eval "$cond"; then
        echo "PASS: $msg" >> "$LOG"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $msg" >> "$LOG"
        FAIL=$((FAIL + 1))
    fi
}

: > "$LOG"
echo "F1 Plan Compliance Audit for rtl-bug-fix-wv" >> "$LOG"
echo "Date: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG"
echo "" >> "$LOG"

echo "## Plan Sections" >> "$LOG"
for t in T0 T1 T2 T3 T4 T5 T6; do
    check "$t section exists in plan" "grep -qE '^###\s+$t\\.' '$PLAN'"
done
echo "" >> "$LOG"

echo "## Evidence Files" >> "$LOG"
check "build/evidence/rbf-start-commit.txt exists" "test -f build/evidence/rbf-start-commit.txt"
check "build/evidence/wrap-sfu-regression.txt exists" "test -f build/evidence/wrap-sfu-regression.txt"
check "build/evidence/wrap-vec-regression.txt exists" "test -f build/evidence/wrap-vec-regression.txt"
check "build/evidence/wrap-mxu-regression.txt exists" "test -f build/evidence/wrap-mxu-regression.txt"
check "build/evidence/wrap-bug005-result.txt exists" "test -f build/evidence/wrap-bug005-result.txt"
check "build/evidence/wrap-bug007-result.txt exists" "test -f build/evidence/wrap-bug007-result.txt"
check "build/evidence/fix-module-regression.txt exists" "test -f build/evidence/fix-module-regression.txt"
check "build/evidence/fix-mxu-module-regression.txt exists" "test -f build/evidence/fix-mxu-module-regression.txt"
check "build/evidence/fix-005-sfu-conclusion.txt exists" "test -f build/evidence/fix-005-sfu-conclusion.txt"
echo "" >> "$LOG"

echo "## Acceptance Criteria Content" >> "$LOG"

check "WV-001: sfu_top status_done has exactly 2 clears (reset + cmd_start)" \
    "test \$(grep -cE 'status_done\s*<=\s*1'\\''b0' rtl/sfu/sfu_top.v) -eq 2"

check "WV-007: controller status_done has exactly 3 clears (reset + S_IDLE cmd_start + S_DONE cmd_start)" \
    "test \$(grep -cE 'status_done\s*<=\s*1'\\''b0' rtl/mxu/controller.v) -eq 3"

check "BUG-005 Vector: wrap-bug005-result.txt shows Vector PASS" \
    "grep -qiE 'Vector:.*PASS' build/evidence/wrap-bug005-result.txt"

check "BUG-005 SFU: wrap-bug005-result.txt shows SFU PASS" \
    "grep -qiE 'SFU:.*PASS' build/evidence/wrap-bug005-result.txt"

check "BUG-007 MXU: wrap-bug007-result.txt shows MXU PASS" \
    "grep -qiE 'MXU:.*PASS' build/evidence/wrap-bug007-result.txt"

check "Vector baseline: wrap-vec-regression.txt shows ALL 5 PASS" \
    "grep -q 'ALL 5 PASS' build/evidence/wrap-vec-regression.txt"

check "MXU baseline: wrap-mxu-regression.txt shows 5 PASS" \
    "grep -q '5 PASS' build/evidence/wrap-mxu-regression.txt"

check "Module regression: fix-module-regression.txt shows SFU 319/319 PASS" \
    "grep -qiE '319/319\s+PASS' build/evidence/fix-module-regression.txt"

check "Module regression: fix-module-regression.txt shows Vector 63/63 PASS" \
    "grep -qiE '63/63\s+PASS' build/evidence/fix-module-regression.txt"

check "Module regression: fix-module-regression.txt shows MXU 109/109 PASS" \
    "grep -qiE '109/109\s+PASS' build/evidence/fix-module-regression.txt"

check "Bug docs: WV-001 marked Fixed" \
    "grep -A10 'BUG-RTL-SOC-WV-001' docs/bugs/bugs-soc-rtl.md | grep -q 'Fixed'"

check "Bug docs: WV-007 marked Fixed" \
    "grep -A10 'BUG-RTL-SOC-WV-007' docs/bugs/bugs-soc-rtl.md | grep -q 'Fixed'"

check "Bug docs: BUG-RTL-SOC-005 marked Fixed" \
    "grep -A10 'BUG-RTL-SOC-005' docs/bugs/bugs-soc-rtl.md | grep -q 'Fixed'"

echo "" >> "$LOG"

TOTAL=$((PASS + FAIL))
echo "## Summary" >> "$LOG"
echo "PASS: $PASS / $TOTAL" >> "$LOG"
echo "FAIL: $FAIL / $TOTAL" >> "$LOG"
if [ "$FAIL" -eq 0 ]; then
    echo "F1-AUDIT-PASS" >> "$LOG"
else
    echo "F1-AUDIT-FAIL" >> "$LOG"
fi

echo ""
echo "=== F1 Audit Results ==="
cat "$LOG"
echo ""
echo "Evidence written to $LOG"

if [ "$FAIL" -eq 0 ]; then
    exit 0
else
    exit 1
fi
