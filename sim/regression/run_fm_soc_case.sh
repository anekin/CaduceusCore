#!/bin/bash
# =============================================================================
# run_fm_soc_case.sh — CaduceusCore Func Model SoC Case Runner
# =============================================================================
# SoC Phase 3-4 / Todo 3 (soc-rtl-substitution)
#
# Wraps the full EDA environment setup, define selection, and Makefile
# invocation for running one FM-SOC-NNN test case against the RTL SoC.
#
# Usage:
#   ./sim/regression/run_fm_soc_case.sh FM-SOC-001
#   ./sim/regression/run_fm_soc_case.sh FM-SOC-003 PCIE
#   ./sim/regression/run_fm_soc_case.sh FM-SOC-013 DMA
#
# Arguments:
#   $1 — case_id (required, e.g., FM-SOC-001)
#   $2 — mixed_mode_module (optional, e.g., PCIE, DMA, MXU, SFU, VECTOR)
#        When specified, adds +define+USE_RTL_<MODULE> for mixed-mode.
#        When omitted, runs full RTL mode (all modules in RTL).
#
# Environment:
#   Sources run_env.sh (module load, cocotb env, PLI table).
#
# Exit codes:
#   0 — case PASSED
#   1 — case FAILED or environment error
# =============================================================================

set -euo pipefail

# ── Argument parsing ────────────────────────────────────────────────────────
CASE_ID="${1:-}"
if [ -z "$CASE_ID" ]; then
    echo "Usage: $0 <case_id> [mixed_mode_module]"
    echo "  case_id: FM-SOC-NNN (e.g., FM-SOC-001)"
    echo "  mixed_mode_module: PCIE|DMA|MXU|SFU|VECTOR (optional)"
    exit 1
fi

MIXED_MODULE="${2:-}"

# ── Path resolution ─────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
MAKEFILE_DIR="$SCRIPT_DIR"

echo ""
echo "============================================================"
echo " CaduceusCore RTL SoC Case Runner"
echo " Case:   $CASE_ID"
echo " Mode:   ${MIXED_MODULE:-full RTL}"
echo " Root:   $REPO_ROOT"
echo "============================================================"
echo ""

# ── EDA environment setup ─────────────────────────────────────────────────
if [ -f "$SCRIPT_DIR/run_env.sh" ]; then
    echo "[ENV] Sourcing run_env.sh..."
    source "$SCRIPT_DIR/run_env.sh"
else
    echo "ERROR: run_env.sh not found at $SCRIPT_DIR/run_env.sh"
    exit 1
fi

# ── Verify prerequisites ───────────────────────────────────────────────────
if ! command -v vcs &>/dev/null; then
    echo "ERROR: vcs not found — is this the EDA server?"
    exit 1
fi

if ! python3 --version &>/dev/null; then
    echo "ERROR: python3 not found in PATH"
    exit 1
fi

echo "[ENV] VCS:   $(which vcs)"
echo "[ENV] Python: $(python3 --version)"

# ── Build EXTRA_DEFINES from mixed_mode_module ─────────────────────────────
EXTRA_DEFINES=""
case "${MIXED_MODULE^^}" in
    PCIE)
        EXTRA_DEFINES="+define+USE_RTL_PCIE"
        ;;
    DMA)
        EXTRA_DEFINES="+define+USE_RTL_DMA"
        ;;
    MXU)
        EXTRA_DEFINES="+define+USE_RTL_MXU"
        ;;
    SFU)
        EXTRA_DEFINES="+define+USE_RTL_SFU"
        ;;
    VECTOR)
        EXTRA_DEFINES="+define+USE_RTL_VECTOR"
        ;;
    "")
        # Full RTL mode — no extra defines
        ;;
    *)
        echo "WARNING: Unknown mixed-mode module '${MIXED_MODULE}'"
        echo "Valid: PCIE, DMA, MXU, SFU, VECTOR"
        ;;
esac

# ── Run the Makefile target ────────────────────────────────────────────────
echo ""
echo "[RUN] Starting VCS compilation + cocotb simulation..."
echo "[RUN] Case: $CASE_ID"
echo "[RUN] Defines: ${EXTRA_DEFINES:-<none, full RTL>}"
echo ""

cd "$REPO_ROOT"

# Build make command
MAKE_CMD="make -C sim/regression run_fm_soc_case CASE_ID=$CASE_ID"
if [ -n "$EXTRA_DEFINES" ]; then
    MAKE_CMD="$MAKE_CMD EXTRA_DEFINES=\"$EXTRA_DEFINES\""
fi

echo "[RUN] Command: $MAKE_CMD"
echo ""

eval "$MAKE_CMD"
MAKE_EXIT=$?

echo ""
if [ $MAKE_EXIT -eq 0 ]; then
    echo "============================================================"
    echo " RESULT: PASS"
    echo " Case:   $CASE_ID"
    echo " Mode:   ${MIXED_MODULE:-full RTL}"
    echo "============================================================"
else
    echo "============================================================"
    echo " RESULT: FAIL (exit code $MAKE_EXIT)"
    echo " Case:   $CASE_ID"
    echo " Mode:   ${MIXED_MODULE:-full RTL}"
    echo " Log:    sim/regression/fm_soc_${CASE_ID}.log"
    echo "============================================================"
fi

exit $MAKE_EXIT
