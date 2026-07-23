#!/usr/bin/env bash
# Log a wrapper-level-verification bug entry to docs/bugs/bugs-soc-rtl.md
set -euo pipefail
source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

BUG_FILE="${REPO_ROOT}/docs/bugs/bugs-soc-rtl.md"

# Defaults — all empty, validated after parsing
ID=""
DATE=""
BLOCK=""
CASE=""
SEVERITY=""
TYPE=""
STATUS=""
SUMMARY=""
SYMPTOM=""
ROOT_CAUSE=""
FIX=""
VERIFICATION=""

usage() {
    cat >&2 <<EOF
Usage: $(basename "$0") --id BUG-RTL-SOC-WV-NNN --date YYYY-MM-DD --block "..." \\
    --case "..." --severity Major --type "..." --status Open \\
    --summary "..." --symptom "..." --root-cause "..." \\
    --fix "..." --verification "..."

All flags are required. Appends a formatted bug entry to ${BUG_FILE}.
If the bug ID already exists, prints a warning and exits without appending.
EOF
    exit 1
}

# Parse long options
while [[ $# -gt 0 ]]; do
    case "$1" in
        --id)            shift; ID="$1"            ;;
        --date)          shift; DATE="$1"          ;;
        --block)         shift; BLOCK="$1"         ;;
        --case)          shift; CASE="$1"          ;;
        --severity)      shift; SEVERITY="$1"      ;;
        --type)          shift; TYPE="$1"          ;;
        --status)        shift; STATUS="$1"        ;;
        --summary)       shift; SUMMARY="$1"       ;;
        --symptom)       shift; SYMPTOM="$1"       ;;
        --root-cause)    shift; ROOT_CAUSE="$1"    ;;
        --fix)           shift; FIX="$1"           ;;
        --verification)  shift; VERIFICATION="$1"  ;;
        --help|-h)       usage                     ;;
        *) echo "ERROR: Unknown option: $1" >&2; usage ;;
    esac
    shift
done

# Validate required fields
missing=0
for field in ID DATE BLOCK CASE SEVERITY TYPE STATUS SUMMARY SYMPTOM ROOT_CAUSE FIX VERIFICATION; do
    if [[ -z "${!field}" ]]; then
        # Convert to --flag name for error message
        flag="--$(echo "$field" | tr '[:upper:]' '[:lower:]' | tr '_' '-')"
        echo "ERROR: ${flag} is required" >&2
        missing=1
    fi
done
[[ $missing -eq 1 ]] && usage

# Check for duplicate
if grep -q "^### ${ID}\b" "$BUG_FILE" 2>/dev/null; then
    echo "WARNING: Bug ${ID} already exists in ${BUG_FILE}. Skipping append." >&2
    exit 0
fi

# Append formatted bug entry — follows the Chinese-field-label format
# matching BUG-RTL-SOC-007 and the template in docs/bugs/bugs-soc-rtl.md
{
    echo ""
    echo "---"
    echo ""
    echo "### ${ID} — ${SUMMARY}"
    echo ""
    echo "| 字段 | 内容 |"
    echo "|------|------|"
    echo "| **Date** | ${DATE} |"
    echo "| **Block** | ${BLOCK} |"
    echo "| **Case** | ${CASE} |"
    echo "| **Severity** | ${SEVERITY} |"
    echo "| **Type** | ${TYPE} |"
    echo "| **Status** | ${STATUS} |"
    echo ""
    echo "#### Symptom"
    echo ""
    echo "${SYMPTOM}"
    echo ""
    echo "#### Root Cause"
    echo ""
    echo "${ROOT_CAUSE}"
    echo ""
    echo "#### Fix"
    echo ""
    echo "${FIX}"
    echo ""
    echo "#### Verification"
    echo ""
    echo "${VERIFICATION}"
    echo ""
} >> "$BUG_FILE"

echo "Bug ${ID} appended to ${BUG_FILE}"
