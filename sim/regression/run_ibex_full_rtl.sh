#!/bin/bash
# =============================================================================
# run_ibex_full_rtl.sh — FM-SOC-001..032 + 10X RTL + Ibex CPU regression
# =============================================================================
# Runs the full 33-case SoC FM regression against the RTL SoC with the internal
# Ibex RISC-V core as the active CPU.  The testbench writes doorbell HOST_TAIL
# and polls NPU_HEAD through a VPI backdoor path so it never conflicts with
# Ibex's live APB master.
#
# Usage:
#   cd CaduceusCore
#   bash sim/regression/run_ibex_full_rtl.sh [case_id]
#
# If case_id is omitted, all 33 cases (FM-SOC-001..032 and FM-SOC-10X) are run
# sequentially against the same compiled simv.
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# RTL $readmemh paths are relative to the repository parent directory.
RUN_DIR="$(cd "$REPO_ROOT/.." && pwd)"
cd "$REPO_ROOT"

# Source EDA environment (VCS, cocotb Python)
source "$REPO_ROOT/sim/regression/run_env.sh"

# Build directories
BUILD_DIR="$REPO_ROOT/build/ibex_full_rtl"
SIMV="$BUILD_DIR/simv_soc_ibex"
mkdir -p "$BUILD_DIR"

# Determine case list
if [ $# -ge 1 ]; then
    CASES="$1"
else
    CASES="FM-SOC-001 FM-SOC-002 FM-SOC-003 FM-SOC-004 FM-SOC-005 FM-SOC-006 FM-SOC-007 FM-SOC-008 FM-SOC-009 FM-SOC-010 FM-SOC-011 FM-SOC-012 FM-SOC-013 FM-SOC-014 FM-SOC-015 FM-SOC-016 FM-SOC-017 FM-SOC-018 FM-SOC-019 FM-SOC-020 FM-SOC-021 FM-SOC-022 FM-SOC-023 FM-SOC-024 FM-SOC-025 FM-SOC-026 FM-SOC-027 FM-SOC-028 FM-SOC-029 FM-SOC-030 FM-SOC-031 FM-SOC-032 FM-SOC-10X"
fi

# Cocotb module discovery
export PYTHONPATH="${PYTHONPATH:-}:$REPO_ROOT"
export MODULE=sim.rtl_soc_runner
export TOPLEVEL=tb_soc_ibex
export TOPLEVEL_LANG=verilog
export FM_SOC_RTL_MODE=ibex
export TESTCASE=test_soc_ibex_full

# Compile simv if not present
if [ ! -x "$SIMV" ]; then
    echo "[INFO] Compiling simv_soc_ibex ..."
    vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
        -kdb \
        -Mdir="$BUILD_DIR/csrc" \
        -f "$REPO_ROOT/rtl/cpu/ibex.flist" \
        -f "$REPO_ROOT/rtl/ip/verilog-axi.flist" \
        -f "$REPO_ROOT/rtl/ip/verilog-pcie.flist" \
        -f "$REPO_ROOT/rtl/soc/soc.flist" \
        "$REPO_ROOT/rtl/tb/tb_soc_ibex.v" \
        -top tb_soc_ibex \
        -o "$SIMV" \
        -l "$BUILD_DIR/elaborate.log" \
        +vpi \
        -P "$PLI_TAB" \
        -load "$COCOTB_VPI_LIB"
    echo "[INFO] Compile complete: $SIMV"
else
    echo "[INFO] Reusing existing simv: $SIMV"
fi

# Run cases sequentially
EVIDENCE_DIR="$BUILD_DIR/evidence"
mkdir -p "$EVIDENCE_DIR"

# ── Provenance binding (todo 11) ─────────────────────────────────────────
# Snapshot timing contract: captured AFTER the firmware is rebuilt and the
# simv is compiled above, but BEFORE the case loop starts — every case log
# below inherits the same hash-bound header (simv/flist/driver/firmware/
# golden/checkpoint sha256 + tool versions + git commit + dirty state).
RUN_ID="${RUN_ID:-$(date +%Y%m%dT%H%M%S)-$$}"
export IBEX_RUN_ID="$RUN_ID"
export IBEX_SIMV="$SIMV"
PROVENANCE_FILE="$EVIDENCE_DIR/provenance-$RUN_ID.txt"
python3 "$REPO_ROOT/scripts/gen_evidence_provenance.py" \
    --run-id "$RUN_ID" \
    --simv "$SIMV" \
    --flist "$REPO_ROOT/rtl/soc/soc.flist" \
    --driver "$REPO_ROOT/sim/rtl_soc_runner.py" \
    --firmware "$REPO_ROOT/firmware/build/npu_firmware.hex" \
    --golden "$REPO_ROOT/rtl/test_vectors/soc_e2e" \
    --checkpoint "$REPO_ROOT/build/evidence/task-14-soc-rtl-verification-checkpoints.npz" \
    --out "$PROVENANCE_FILE" \
    || echo "[WARN] provenance generation failed (case logs will lack hash binding)"
echo "[INFO] Provenance (hash-bound evidence header, todo 11):"
sed -e 's/^/    | /' "$PROVENANCE_FILE" 2>/dev/null || true

PASS=0
FAIL=0
SKIP=0
TIMEOUT=0
for CASE in $CASES; do
    echo ""
    echo "============================================================"
    echo "[RUN] $CASE"
    echo "============================================================"
    export FM_SOC_CASE_ID="$CASE"
    CASE_LOG="$EVIDENCE_DIR/${CASE}.log"
    PROV_HEADER="${PROVENANCE_FILE:-$EVIDENCE_DIR/provenance-${RUN_ID:-run}.txt}"
    if [ -f "$PROV_HEADER" ]; then
        sed -e 's/^/provenance| /' "$PROV_HEADER" > "$CASE_LOG"
    else
        : > "$CASE_LOG"
    fi
    set +e
    (cd "$RUN_DIR" && "$SIMV" +COCOTB +FM_SOC_CASE_ID="$CASE" \
        +BOOTROM_HEX="$REPO_ROOT/firmware/build/npu_firmware.hex") >> "$CASE_LOG" 2>&1
    RUN_RC=$?
    set -e
    if [ "$RUN_RC" -eq 124 ] || [ "$RUN_RC" -eq 137 ]; then
        echo "[TIMEOUT] $CASE (simulator exit $RUN_RC; log: $CASE_LOG)"
        printf 'runner_classification=TIMEOUT exit_code=%s\n' "$RUN_RC" >> "$CASE_LOG"
        TIMEOUT=$((TIMEOUT + 1))
    elif [ "$RUN_RC" -ne 0 ]; then
        echo "[FAIL] $CASE (simulator exit $RUN_RC; log: $CASE_LOG)"
        printf 'runner_classification=FAIL exit_code=%s\n' "$RUN_RC" >> "$CASE_LOG"
        FAIL=$((FAIL + 1))
    elif grep -qE 'superseded by FM-SOC-027/032/10X' "$CASE_LOG" || \
         grep -qE 'skipped: direct APB/AXI case not applicable to Ibex RTL mode' "$CASE_LOG"; then
        echo "[SKIP] $CASE"
        printf 'runner_classification=SKIP\n' >> "$CASE_LOG"
        SKIP=$((SKIP + 1))
    elif grep -qE 'TESTS=1 PASS=1 FAIL=0 SKIP=0' "$CASE_LOG"; then
        echo "[PASS] $CASE"
        printf 'runner_classification=PASS\n' >> "$CASE_LOG"
        PASS=$((PASS + 1))
    else
        echo "[FAIL] $CASE (no cocotb PASS summary; log: $CASE_LOG)"
        printf 'runner_classification=FAIL reason=no_summary\n' >> "$CASE_LOG"
        FAIL=$((FAIL + 1))
    fi
done

TOTAL=$((PASS + SKIP + FAIL + TIMEOUT))
N_CASES=$(echo "$CASES" | wc -w)

echo ""
echo "============================================================"
echo "[SUMMARY] Full RTL + Ibex (FM-SOC regression)"
echo "  PASS:    $PASS"
echo "  SKIP:    $SKIP"
echo "  FAIL:    $FAIL"
echo "  TIMEOUT: $TIMEOUT"
echo "[SUMMARY] PASS=$PASS SKIP=$SKIP FAIL=$FAIL TIMEOUT=$TIMEOUT TOTAL=$TOTAL"
echo "============================================================"

if [ "$TOTAL" -ne "$N_CASES" ]; then
    echo "[ERROR] case accounting mismatch: classified $TOTAL of $N_CASES cases"
    exit 1
fi
if [ "$FAIL" -ne 0 ] || [ "$TIMEOUT" -ne 0 ]; then
    exit 1
fi
