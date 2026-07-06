#!/bin/bash
# =============================================================================
# run_fm_soc_all.sh — Full 33-case SoC RTL regression
# =============================================================================
# Runs the complete FM-SOC-001..032 + FM-SOC-10X RTL regression against the
# RTL SoC with the internal Ibex RISC-V core.  This is a thin wrapper around
# run_ibex_full_rtl.sh, which already covers all 33 cases end-to-end.
#
# Usage:
#   cd CaduceusCore
#   bash sim/regression/run_fm_soc_all.sh [case_id]
#
# If case_id is omitted, all 33 cases are run sequentially.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

# Source EDA environment (VCS, cocotb Python)
source "$REPO_ROOT/sim/regression/run_env.sh"

# Delegate to the Ibex full-RTL regression script, which covers all 33 cases.
exec bash "$REPO_ROOT/sim/regression/run_ibex_full_rtl.sh" "$@"
