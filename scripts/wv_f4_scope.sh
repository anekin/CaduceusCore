#!/usr/bin/env bash
# wv_f4_scope.sh — Final Wave F4: Scope Gate (RTL/FW/Bridge/SoC-instantiation)
#
# Checks:
#   1. No RTL source files modified in the last 4 commits (Wave 0-3)
#      — checks rtl/wrapper/, rtl/sfu/, rtl/vector/, rtl/mxu/, rtl/soc/,
#        rtl/cpu/, rtl/intc/, rtl/ip/ (exception: rtl/tb/ is allowed)
#   2. No firmware/ files modified
#   3. Bridge files (cocotb_bridge.py, rtl_soc_runner.py, spike_rtl_bridge.py) unchanged
#   4. New wrapper testbenches do not instantiate SoC-level modules
#   5. Writes build/evidence/wv-f4-gate.txt with pass/fail flags
#
set -euo pipefail
source "$(dirname "$0")/p9_lib/p9_sz0001.sh"

EVIDENCE_DIR="${REPO_ROOT}/build/evidence"
mkdir -p "${EVIDENCE_DIR}"

# ------------------------------------------------------------------
# Step 1: Determine changed files in the last 4 commits (Wave 0-3)
# ------------------------------------------------------------------
CHANGED_FILES=""
if git diff --name-only HEAD~4..HEAD > /dev/null 2>&1; then
  CHANGED_FILES=$(git diff --name-only HEAD~4..HEAD)
fi

if [ -z "${CHANGED_FILES}" ]; then
  # Fallback: diff against merge-base with main/master
  MERGE_BASE=$(git merge-base HEAD main 2>/dev/null || git merge-base HEAD master 2>/dev/null || echo "")
  if [ -n "${MERGE_BASE}" ]; then
    CHANGED_FILES=$(git diff --name-only "${MERGE_BASE}"..HEAD)
  fi
fi

echo "=== Changed files (last 4 commits) ==="
echo "${CHANGED_FILES}"
echo ""

# ------------------------------------------------------------------
# Step 2: Check RTL unchanged (rtl/tb/ is allowed; source dirs are not)
# ------------------------------------------------------------------
RTL_UNCHANGED=1
RTL_DETAILS=""

RTL_DIRS=(
  'rtl/wrapper/'
  'rtl/sfu/'
  'rtl/vector/'
  'rtl/mxu/'
  'rtl/soc/'
  'rtl/cpu/'
  'rtl/intc/'
  'rtl/ip/'
)

while IFS= read -r f; do
  [ -z "${f}" ] && continue
  for dir in "${RTL_DIRS[@]}"; do
    if [[ "${f}" == "${dir}"* ]]; then
      RTL_UNCHANGED=0
      RTL_DETAILS+="RTL CHANGED: ${f}"$'\n'
    fi
  done
done <<< "${CHANGED_FILES}"

# ------------------------------------------------------------------
# Step 3: Check firmware unchanged
# ------------------------------------------------------------------
FIRMWARE_UNCHANGED=1
FW_DETAILS=""

while IFS= read -r f; do
  [ -z "${f}" ] && continue
  if [[ "${f}" == firmware/* ]]; then
    FIRMWARE_UNCHANGED=0
    FW_DETAILS+="FIRMWARE CHANGED: ${f}"$'\n'
  fi
done <<< "${CHANGED_FILES}"

# ------------------------------------------------------------------
# Step 4: Check bridge files unchanged
# ------------------------------------------------------------------
BRIDGE_UNCHANGED=1
BRIDGE_DETAILS=""

BRIDGE_FILES=(
  'sim/cocotb_bridge.py'
  'sim/rtl_soc_runner.py'
  'sim/spike_rtl_bridge.py'
)

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
# Step 5: Check wrapper testbenches for SoC module instantiation
# ------------------------------------------------------------------
NO_SOC_INSTANTIATION=1
SOC_INST_DETAILS=""

# Forbidden module names (SoC/crossbar/DRAM/CPU — NOT wrapper modules)
FORBIDDEN_MODULES=(
  'caduceus_soc_top'
  'axi_crossbar'
  'sram_ctrl'
  'dram_model'
  'ibex_wrapper'
)

# Collect wrapper testbench files from changed files
WRAPPER_TB_FILES=""
while IFS= read -r f; do
  [ -z "${f}" ] && continue
  if [[ "${f}" =~ ^rtl/tb/tb_.*_wrapper.*\.v$ ]]; then
    WRAPPER_TB_FILES+="${REPO_ROOT}/${f}"$'\n'
  fi
done <<< "${CHANGED_FILES}"

# If no wrapper TBs in changed files, scan all matching (fallback)
if [ -z "${WRAPPER_TB_FILES}" ]; then
  while IFS= read -r tb_file; do
    WRAPPER_TB_FILES+="${tb_file}"$'\n'
  done < <(find "${REPO_ROOT}/rtl/tb" -name 'tb_*_wrapper*.v' -type f 2>/dev/null | sort)
fi

while IFS= read -r tb_file; do
  [ -z "${tb_file}" ] && continue
  for mod in "${FORBIDDEN_MODULES[@]}"; do
    # Match Verilog module instantiation: whitespace then module name,
    # followed by #( (parameterized) or word-char (named instance)
    if grep -qE "^\s*${mod}\s+#\(" "${tb_file}" 2>/dev/null || \
       grep -qE "^\s*${mod}\s+[a-zA-Z]" "${tb_file}" 2>/dev/null; then
      NO_SOC_INSTANTIATION=0
      fname="${tb_file#${REPO_ROOT}/}"
      SOC_INST_DETAILS+="SOC INST: ${fname} instantiates ${mod}"$'\n'
    fi
  done
done <<< "${WRAPPER_TB_FILES}"

# ------------------------------------------------------------------
# Step 6: Write evidence file
# ------------------------------------------------------------------
{
  echo "WV-F4-SCOPE"
  echo "==========="
  echo ""

  if [ "${RTL_UNCHANGED}" = "1" ]; then
    echo "RTL_UNCHANGED=1"
  else
    echo "RTL_UNCHANGED=0"
    echo -n "${RTL_DETAILS}"
  fi

  if [ "${FIRMWARE_UNCHANGED}" = "1" ]; then
    echo "FIRMWARE_UNCHANGED=1"
  else
    echo "FIRMWARE_UNCHANGED=0"
    echo -n "${FW_DETAILS}"
  fi

  if [ "${BRIDGE_UNCHANGED}" = "1" ]; then
    echo "BRIDGE_UNCHANGED=1"
  else
    echo "BRIDGE_UNCHANGED=0"
    echo -n "${BRIDGE_DETAILS}"
  fi

  if [ "${NO_SOC_INSTANTIATION}" = "1" ]; then
    echo "NO_SOC_INSTANTIATION=1"
  else
    echo "NO_SOC_INSTANTIATION=0"
    echo -n "${SOC_INST_DETAILS}"
  fi
} > "${EVIDENCE_DIR}/wv-f4-gate.txt"

# ------------------------------------------------------------------
# Step 7: Summary
# ------------------------------------------------------------------
echo "=== WV-F4 Scope Gate Summary ==="
echo "  RTL_UNCHANGED=${RTL_UNCHANGED}"
echo "  FIRMWARE_UNCHANGED=${FIRMWARE_UNCHANGED}"
echo "  BRIDGE_UNCHANGED=${BRIDGE_UNCHANGED}"
echo "  NO_SOC_INSTANTIATION=${NO_SOC_INSTANTIATION}"
echo ""
echo "Evidence: ${EVIDENCE_DIR}/wv-f4-gate.txt"

exit 0

