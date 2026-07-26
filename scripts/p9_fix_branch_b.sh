#!/usr/bin/env bash
set -euo pipefail
source "$(dirname $0)/p9_lib/p9_sz0001.sh"

# ── Paths ───────────────────────────────────────────────────────────
WRAPPER="${REPO_ROOT}/rtl/wrapper/mxu_soc_wrapper.v"
SOC_SIMV="${REPO_ROOT}/build/p9_simv_soc_top"
ELAPSED="${REPO_ROOT}/build/evidence/ph9-t4b-elapsed.txt"
ELAB_LOG="${REPO_ROOT}/build/p9_soc_elaborate.log"
DIRECTED_LOG="${REPO_ROOT}/build/evidence/ph9-t4b-directed.log"
CAUSALITY_LOG="${REPO_ROOT}/build/evidence/ph9-causality.txt"
LEARNINGS="${REPO_ROOT}/.omo/notepads/phase9-firmware-rtl-fix/learnings.md"

echo "=== P9 T4 Branch B Fix ==="
echo "Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ── Step (a): Verify RTL fix applied ────────────────────────────────
if ! grep -q 'wrp_k_tiles_derived' "$WRAPPER"; then
    echo "[a] ERROR: P9-B fix not in ${WRAPPER}"
    exit 1
fi
echo "[a] P9-B RTL fix confirmed"

# ── Step (b): VCS compile on sz0001 ────────────────────────────────
echo "[b] VCS compile building ${SOC_SIMV} ..."
mkdir -p "$(dirname "$SOC_SIMV")" "$(dirname "$ELAPSED")" "$(dirname "$ELAB_LOG")"

T0=$(date +%s)

COMPILE_CMD="set -e; cd '${REPO_ROOT}'; vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -f rtl/cpu/ibex.flist -f rtl/ip/verilog-axi.flist -f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist -top tb_soc rtl/tb/tb_soc.v +define+COCOTB_SIM=1 +vpi -P sim/regression/pli.tab -load \$(cocotb-config --lib-name-path vpi vcs) -o build/p9_simv_soc_top -l build/p9_soc_elaborate.log"

p9_ssh "$COMPILE_CMD"
EC=$?
T1=$(date +%s)
DURATION=$((T1 - T0))

echo "VCS_EXIT_CODE=${EC}" > "$ELAPSED"
echo "ELAPSED_SEC=${DURATION}" >> "$ELAPSED"
echo "[b] VCS compile exit=${EC} elapsed=${DURATION}s"

test -x "$SOC_SIMV" || { echo "[b] ERROR: simv not built"; exit 1; }
test "$EC" -eq 0 || { echo "[b] ERROR: VCS exit code ${EC}"; exit 1; }
test -s "$ELAB_LOG" || { echo "[b] ERROR: elaboration log empty"; exit 1; }
echo "[b] Simv: ${SOC_SIMV} ($(stat -c %s "$SOC_SIMV") bytes)"

# ── Helper: run cocotb test on sz0001 ──────────────────────────────
run_cocotb() {
    local testcase="$1" log_label="$2"
    local cmd
    cmd="set -e; cd '${REPO_ROOT}'; LD_LIBRARY_PATH=\"\${COCOTB_LIB_DIR}:\${COCOTB_PY_ENV}/lib:\${LD_LIBRARY_PATH:-}\" PYTHONPATH=\"${REPO_ROOT}\" MODULE=sim.perf_tests TESTCASE=${testcase} TOPLEVEL=tb_soc TOPLEVEL_LANG=verilog PYTHONIOENCODING=utf-8 COCOTB_RESULTS_FILE=\"${REPO_ROOT}/sim/regression/p9_t4b_results.xml\" \"./build/p9_simv_soc_top\" +define+COCOTB_SIM=1 +COCOTB -no_save +BOOTROM_HEX=\"${REPO_ROOT}/firmware/build/npu_firmware.hex\" 2>&1 | tee -a \"${log_label}\""
    p9_ssh "$cmd"
}

# ── Step (c): Rebuild firmware ─────────────────────────────────────
echo "[c] Rebuilding firmware ..."
cd "$REPO_ROOT/firmware" && make clean && make
cd "$REPO_ROOT"
test -s firmware/build/npu_firmware.hex || { echo "[c] ERROR: hex missing"; exit 1; }
echo "[c] Firmware rebuilt"

# ── Step (d): Directed sweep ────────────────────────────────────────
echo "[d] Running directed sweep ..."
mkdir -p "$(dirname "$DIRECTED_LOG")"
run_cocotb test_w4_perf_p9_directed_sweep "${REPO_ROOT}/build/evidence/ph9-t4b-run.log"

test -s "$DIRECTED_LOG" || { echo "[d] ERROR: directed log missing"; exit 1; }
if grep -qE '"cos_sim":\s*(0\.999[0-9]*|1\.0[0-9]*)' "$DIRECTED_LOG"; then
    echo "[d] cos_sim >= 0.999 PASS"
else
    echo "[d] ERROR: cos_sim < 0.999"; grep -oE '"cos_sim": [0-9.]+' "$DIRECTED_LOG"; exit 1
fi

# ── Step (e): Causality gate ────────────────────────────────────────
echo "[e] Running causality gate ..."
run_cocotb test_w4_perf_p9_causality "${REPO_ROOT}/build/evidence/ph9-causality-run.log"

test -s "$CAUSALITY_LOG" || { echo "[e] ERROR: causality log missing"; exit 1; }
grep -q '^K<=64:' "$CAUSALITY_LOG" || { echo "[e] ERROR: K<=64 missing"; exit 1; }
grep -q '^K=512:' "$CAUSALITY_LOG" || { echo "[e] ERROR: K=512 missing"; exit 1; }
grep -qE '^K<=64:.*cos_sim=(0\.999|1\.0)' "$CAUSALITY_LOG" || { echo "[e] ERROR: K<=64 cos_sim < 0.999"; cat "$CAUSALITY_LOG"; exit 1; }
echo "[e] Causality PASS:"; cat "$CAUSALITY_LOG"

# ── Step (f): Bug logging ───────────────────────────────────────────
echo "[f] Logging BUG-MXU-P9-00B-broadcast-multitile ..."
bash "${REPO_ROOT}/scripts/p9_log_bug.sh" \
    --rtl-report BUG-MXU-P9-00B-broadcast-multitile \
    --type rtl \
    --symptom "M=1 multi-tile broadcast/store-out geometry error" \
    --evidence build/evidence/ph9-t4b-directed.log \
    --verdict resolved

echo "[f] Bug logged"

# ── Append learnings ────────────────────────────────────────────────
{
    echo ""
    echo "## T4 Branch B Execution Log"
    echo ""
    echo "**Date:** $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "**Pivot from:** Branch A (insufficient, compiler no-op)"
    echo "**RTL Fix:** mxu_soc_wrapper.v:186-205 — latch K/N from MXU DIM0/DIM1 MMIO"
    echo "**Root cause:** GCC -O2 misroutes WRP_K_TILES/DIM_N writes to DMA space"
    echo ""
} >> "$LEARNINGS"

echo ""
echo "=== P9 T4 Branch B Fix COMPLETE ==="
echo "End: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
