#!/bin/bash
# =============================================================================
# run_env.sh — CaduceusCore SoC EDA Environment Setup
# =============================================================================
# Sources all EDA tool modules and sets up the cocotb Python environment.
# This script is sourced (not executed) by all subsequent automation scripts.
#
# Usage:
#   source sim/regression/run_env.sh
#   which vcs          # should return /NAS/Tools/EDA/.../bin/vcs
#   python3 --version  # should print Python 3.11.x
#
# Requirements:
#   - EDA server (sz0001, 192.168.0.11) or equivalent with Synopsys tools
#   - Anaconda Python 3.11 env with cocotb, cocotbext-axi, cocotbext-pcie
# =============================================================================

# ── EDA Module Initialization ──────────────────────────────────────────────
if [ -f /NAS/Tools/EDA/env/modules.bash ]; then
    source /NAS/Tools/EDA/env/modules.bash
else
    echo "ERROR: /NAS/Tools/EDA/env/modules.bash not found — is this the EDA server?"
    return 1 2>/dev/null || exit 1
fi

# ── VCS (Synopsys Verilog Compiler Simulator) ─────────────────────────────
# Version: V-2023.12-SP2
# Provides: vcs, vlogan, urg, dve
module load vcs/vcs_2023.12sp2 2>/dev/null
if ! which vcs &>/dev/null; then
    echo "ERROR: vcs not found after module load — check license and module availability"
    return 1 2>/dev/null || exit 1
fi

# ── Cocotb Python Environment ─────────────────────────────────────────────
# Pre-installed Anaconda Python 3.11 with cocotb + cocotbext-axi + cocotbext-pcie
COCOTB_PY_ENV="/NAS/Tools/anaconda3/envs/py3.11"
if [ ! -d "$COCOTB_PY_ENV" ]; then
    echo "WARNING: COCOTB_PY_ENV ($COCOTB_PY_ENV) not found"
    echo "         Install: conda create -n py3.11 python=3.11 && pip install cocotb cocotbext-axi cocotbext-pcie"
else
    export PATH="$COCOTB_PY_ENV/bin:$PATH"
fi
export COCOTB_PY_ENV

# ── Cocotb VPI Library ────────────────────────────────────────────────────
if command -v cocotb-config &>/dev/null; then
    COCOTB_VPI_LIB=$(cocotb-config --lib-name-path vpi vcs 2>/dev/null || echo "")
    COCOTB_LIB_DIR="$COCOTB_PY_ENV/lib/python3.11/site-packages/cocotb/libs"
    export COCOTB_VPI_LIB COCOTB_LIB_DIR
fi

# ── PLI Table (VPI access permissions for cocotb) ─────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLI_TAB="$SCRIPT_DIR/pli.tab"
if [ ! -f "$PLI_TAB" ]; then
    echo "acc+=rw,wn:*" > "$PLI_TAB"
fi
export PLI_TAB

# ── License (override default multi-server list to local-only) ────────────
# The default SNPSLMD_LICENSE_FILE includes 4 unreachable 172.16.x.x IPs.
# Override with the local license daemon to avoid lmstat timeout stalls.
if [ -z "$SNPSLMD_LICENSE_FILE" ]; then
    export SNPSLMD_LICENSE_FILE="27020@sz0001"
fi

# ── Verify ─────────────────────────────────────────────────────────────────
echo "=== CaduceusCore EDA Environment ==="
echo "VCS:         $(which vcs)"
echo "VCS_HOME:    ${VCS_HOME:-<not set>}"
echo "Python:      $(python3 --version 2>/dev/null || echo '<not found>')"
echo "PLI_TAB:     $PLI_TAB"
echo "LICENSE:     $SNPSLMD_LICENSE_FILE"
echo "===================================="
