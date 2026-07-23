#!/usr/bin/env bash
# wv_f2_scope_gate.sh — Final Wave F2: Scope Gate + AST integrity check
#
# Checks:
#   1. Only whitelisted files changed in last 4 commits (Wave 0-3)
#   2. No bridge/runner files modified
#   3. All Python files under sim/tests/wrapper/ pass AST parsing
#
set -euo pipefail
source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

EVIDENCE_DIR="${REPO_ROOT}/build/evidence"
mkdir -p "${EVIDENCE_DIR}"

# ------------------------------------------------------------------
# Step 1: Determine changed files
# ------------------------------------------------------------------
# Last 4 commits are Wave 0-3 of this plan
CHANGED_FILES=""
if git diff --name-only HEAD~4..HEAD > /dev/null 2>&1; then
  CHANGED_FILES=$(git diff --name-only HEAD~4..HEAD)
else
  # Fallback: git log of last 4 commits
  CHANGED_FILES=$(git log --format="" --name-only HEAD~4..HEAD 2>/dev/null | sort -u | grep -v '^$')
fi

if [ -z "${CHANGED_FILES}" ]; then
  # Last-resort fallback: diff against the merge-base with main
  MERGE_BASE=$(git merge-base HEAD main 2>/dev/null || git merge-base HEAD master 2>/dev/null || echo "")
  if [ -n "${MERGE_BASE}" ]; then
    CHANGED_FILES=$(git diff --name-only "${MERGE_BASE}"..HEAD)
  fi
fi

echo "=== Changed files ==="
echo "${CHANGED_FILES}"
echo ""

# ------------------------------------------------------------------
# Step 2: Whitelist scope check
# ------------------------------------------------------------------
SCOPE_CREEP=0
SCOPE_CREEP_DETAILS=""

# Whitelist patterns (anchored to repo root)
WHITELIST_PATTERNS=(
  '^rtl/tb/tb_[a-z]*_wrapper[a-z_]*\.v$'
  '^rtl/tb/axi_sparse_slave\.v$'
  '^rtl/tb/wrapper\.flist$'
  '^sim/tests/wrapper/.*\.py$'
  '^scripts/wv_.*\.sh$'
  '^docs/issues_found\.md$'
  '^docs/bugs/bugs-soc-rtl\.md$'
  '^sim/regression/Makefile$'
  '^\.omo/notepads/wrapper-level-verification/.*\.md$'
  '^\.omo/plans/wrapper-level-verification\.md$'
)

check_whitelist() {
  local file="$1"
  for pat in "${WHITELIST_PATTERNS[@]}"; do
    if [[ "${file}" =~ ${pat} ]]; then
      return 0
    fi
  done
  return 1
}

while IFS= read -r f; do
  [ -z "${f}" ] && continue
  if ! check_whitelist "${f}"; then
    SCOPE_CREEP=1
    SCOPE_CREEP_DETAILS+="NOT WHITELISTED: ${f}"$'\n'
  fi
done <<< "${CHANGED_FILES}"

# ------------------------------------------------------------------
# Step 3: Bridge file unchanged check
# ------------------------------------------------------------------
BRIDGE_UNCHANGED=1
BRIDGE_FILES=(
  'sim/cocotb_bridge.py'
  'sim/rtl_soc_runner.py'
  'sim/spike_rtl_bridge.py'
)

BRIDGE_DETAILS=""
while IFS= read -r f; do
  [ -z "${f}" ] && continue
  for bf in "${BRIDGE_FILES[@]}"; do
    if [ "${f}" = "${bf}" ]; then
      BRIDGE_UNCHANGED=0
      BRIDGE_DETAILS+="BRIDGE CHANGED: ${f}"$'\n'
    fi
  done
done <<< "${CHANGED_FILES}"

# ------------------------------------------------------------------
# Step 4: Write code-quality evidence
# ------------------------------------------------------------------
{
  echo "WV-F2-CODE-QUALITY"
  echo "=================="
  echo ""

  if [ "${SCOPE_CREEP}" = "0" ] && [ "${BRIDGE_UNCHANGED}" = "1" ]; then
    echo "BRIDGE_UNCHANGED=1"
    echo "SCOPE_CREEP=0"
  else
    if [ "${BRIDGE_UNCHANGED}" = "1" ]; then
      echo "BRIDGE_UNCHANGED=1"
    else
      echo "BRIDGE_UNCHANGED=0"
      echo "${BRIDGE_DETAILS}"
    fi
    if [ "${SCOPE_CREEP}" = "0" ]; then
      echo "SCOPE_CREEP=0"
    else
      echo "SCOPE_CREEP=1"
      echo "${SCOPE_CREEP_DETAILS}"
    fi
  fi
} > "${EVIDENCE_DIR}/wv-f2-code-quality.txt"

echo "=== Code Quality ==="
echo "BRIDGE_UNCHANGED=${BRIDGE_UNCHANGED}"
echo "SCOPE_CREEP=${SCOPE_CREEP}"
echo ""

# ------------------------------------------------------------------
# Step 5: AST checks on sim/tests/wrapper/*.py
# ------------------------------------------------------------------
AST_OK=1
AST_FAILURES=""
WRAPPER_PY_DIR="${REPO_ROOT}/sim/tests/wrapper"

if [ -d "${WRAPPER_PY_DIR}" ]; then
  while IFS= read -r pyfile; do
    [ -z "${pyfile}" ] && continue
    fname="${pyfile#${REPO_ROOT}/}"
    if python3 -c "
import ast, sys
try:
    with open('${pyfile}') as f:
        ast.parse(f.read(), filename='${pyfile}')
    sys.exit(0)
except SyntaxError as e:
    print(f'SYNTAX ERROR: ${fname}: {e}')
    sys.exit(1)
except Exception as e:
    print(f'ERROR: ${fname}: {e}')
    sys.exit(1)
"; then
      echo "  AST OK: ${fname}"
    else
      AST_OK=0
      AST_FAILURES+="AST FAIL: ${fname}"$'\n'
    fi
  done < <(find "${WRAPPER_PY_DIR}" -name '*.py' -type f | sort)
else
  AST_OK=0
  AST_FAILURES="DIR NOT FOUND: ${WRAPPER_PY_DIR}"
fi

# ------------------------------------------------------------------
# Step 6: Write AST evidence
# ------------------------------------------------------------------
{
  echo "WV-F2-AST"
  echo "========="
  echo ""
  if [ "${AST_OK}" = "1" ]; then
    echo "AST_OK=1"
    echo "All Python files under sim/tests/wrapper/ pass AST parsing."
  else
    echo "AST_OK=0"
    echo "${AST_FAILURES}"
  fi
} > "${EVIDENCE_DIR}/wv-f2-ast.txt"

echo ""
echo "=== AST Check ==="
echo "AST_OK=${AST_OK}"
echo ""

# ------------------------------------------------------------------
# Step 7: Summary
# ------------------------------------------------------------------
echo "=== WV-F2 Scope Gate Summary ==="
echo "  BRIDGE_UNCHANGED=${BRIDGE_UNCHANGED}"
echo "  SCOPE_CREEP=${SCOPE_CREEP}"
echo "  AST_OK=${AST_OK}"
echo ""
echo "Evidence files:"
echo "  ${EVIDENCE_DIR}/wv-f2-code-quality.txt"
echo "  ${EVIDENCE_DIR}/wv-f2-ast.txt"

exit 0
