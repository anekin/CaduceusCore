#!/usr/bin/env bash
#===============================================================================
# p10_mmio_doc_sync.sh - Phase 10 task-20: close MMIO spec gaps (docs only)
#
# Scope: rtl-update-plan.md gap closure by documentation update. No RTL
# modification. Verifies that the MMIO/SRAM spec docs reflect the current RTL
# state and writes the disposed-vs-remaining gap ledger to build/evidence.
#
# Gaps addressed (documented resolution):
#   G1  MXU BIAS/SCALE stub      -> mmio-spec §7.2 labelled Phase 1 NOT APPLICABLE
#   G2  Wrapper SRAM base docs   -> sram-map §6.1/6.2 document current RTL
#                                   defaults (MXU perf-test DRAM workaround,
#                                   Vector spec-compliant bases)
#   G3  APB->MMIO strobe sync    -> mmio-spec §1.1 documents cs=psel&&penable
#                                   (RTL already fixed in apb_to_mmio.v)
#   G4  INTC PENDING bit map     -> mmio-spec §6 lists full 8-source RTL map,
#                                   §7.1 marked RESOLVED
#
# Remaining (future-phase, explicitly deferred):
#   F1  BIAS/SCALE functional wiring (controller consumes bias_addr_o/scale_addr_o)
#   F2  mxu_top SRAM-sourced weight/activation (DMA-style buffer fill)
#   F3  Weight Bank B ping-pong preload (single-bank preload in Phase 1)
#   F4  DMA linked-list descriptor chain FSM
#   F5  Token/batch dimension as separate batch_cur register (M-tile reset is
#       sufficient for current semantics)
#===============================================================================
set -euo pipefail
source "$(dirname "$0")/p10_lib/p10_sz0001.sh"

EVIDENCE_DIR="${REPO_ROOT}/build/evidence"
EVIDENCE_FILE="${EVIDENCE_DIR}/task-20-phase10-rtl-verification.txt"
MMIO_SPEC="${REPO_ROOT}/docs/func-model-mmio-spec.md"
SRAM_MAP="${REPO_ROOT}/docs/func-model-sram-map.md"
RTL_APB="${REPO_ROOT}/rtl/wrapper/apb_to_mmio.v"
RTL_MXU_WRAP="${REPO_ROOT}/rtl/wrapper/mxu_soc_wrapper.v"
RTL_VEC_WRAP="${REPO_ROOT}/rtl/wrapper/vector_soc_wrapper.v"
RTL_MXU_TOP="${REPO_ROOT}/rtl/mxu/mxu_top.v"

fail_count=0
pass()  { echo "  PASS: $1"; }
fail()  { echo "  FAIL: $1" >&2; fail_count=$((fail_count+1)); }

echo "=============================================="
echo "p10_mmio_doc_sync - docs-vs-RTL MMIO gap sync"
echo "=============================================="

#------------------------------------------------------------------------------
# 1. RTL state assertions (source of truth; must match docs below)
#------------------------------------------------------------------------------
echo "[1] RTL state assertions"
test -f "$RTL_APB"     || { fail "missing $RTL_APB"; }
test -f "$RTL_MXU_WRAP" || { fail "missing $RTL_MXU_WRAP"; }
test -f "$RTL_VEC_WRAP" || { fail "missing $RTL_VEC_WRAP"; }
test -f "$RTL_MXU_TOP"  || { fail "missing $RTL_MXU_TOP"; }

if grep -q 'assign cs    = psel && penable;' "$RTL_APB"; then
  pass "apb_to_mmio.v gates cs with penable (single access-phase strobe)"
else
  fail "apb_to_mmio.v does NOT gate cs with penable"
fi

if grep -q "wrp_act_base    <= 32'h8001_0000;" "$RTL_MXU_WRAP" \
   && grep -q "wrp_weight_base <= 32'h8002_0000;" "$RTL_MXU_WRAP" \
   && grep -q "wrp_out_base    <= 32'h8003_0000;" "$RTL_MXU_WRAP"; then
  pass "mxu_soc_wrapper reset defaults = perf-test DRAM bases (P9-B workaround)"
else
  fail "mxu_soc_wrapper reset defaults do not match expected DRAM bases"
fi

if grep -q "wrp_a_base <= 32'h2030_0000;" "$RTL_VEC_WRAP" \
   && grep -q "wrp_b_base <= 32'h2030_0000;" "$RTL_VEC_WRAP" \
   && grep -q "wrp_o_base <= 32'h2034_0000;" "$RTL_VEC_WRAP"; then
  pass "vector_soc_wrapper reset defaults = spec SRAM bases"
else
  fail "vector_soc_wrapper reset defaults do not match spec SRAM bases"
fi

if grep -q "bias_addr_o;      // unused (stubbed)" "$RTL_MXU_TOP" \
   && grep -q "scale_addr_o;      // unused (stubbed)" "$RTL_MXU_TOP"; then
  pass "mxu_top BIAS/SCALE still stubbed (Phase 1 not applicable)"
else
  fail "mxu_top BIAS/SCALE stub annotation not found (RTL changed?)"
fi

#------------------------------------------------------------------------------
# 2. Documentation sync assertions (G1-G4)
#------------------------------------------------------------------------------
echo "[2] Documentation sync assertions"
test -f "$MMIO_SPEC" || { fail "missing $MMIO_SPEC"; }
test -f "$SRAM_MAP"  || { fail "missing $SRAM_MAP"; }

# G1: BIAS/SCALE explicitly labelled Phase 1 NOT APPLICABLE
if grep -q "Phase 1: NOT APPLICABLE" "$MMIO_SPEC" \
   && grep -q "rtl-update-plan.md" "$MMIO_SPEC"; then
  pass "G1 mmio-spec §7.2 labels BIAS/SCALE Phase 1 NOT APPLICABLE"
else
  fail "G1 mmio-spec §7.2 does not carry the Phase 1 NOT APPLICABLE label"
fi

# G2: wrapper SRAM base docs match RTL defaults
if grep -q '`0x8002_0000`.*Weight tile base (perf-test DRAM layout)' "$SRAM_MAP" \
   && grep -q '`0x8001_0000`.*Activation tile base (perf-test DRAM layout)' "$SRAM_MAP" \
   && grep -q '`0x8003_0000`.*Output tile base (perf-test DRAM layout)' "$SRAM_MAP"; then
  pass "G2 sram-map §6.1 documents MXU perf-test DRAM reset bases"
else
  fail "G2 sram-map §6.1 MXU reset bases do not match RTL"
fi

if grep -q '`0x2030_0000`.*Operand A base (Vector Workspace)' "$SRAM_MAP" \
   && grep -q '`0x2034_0000`.*Output base (Scratch / Dtype-Convert)' "$SRAM_MAP"; then
  pass "G2 sram-map §6.2 documents Vector spec-compliant bases"
else
  fail "G2 sram-map §6.2 Vector bases do not match RTL"
fi

# G3: APB->MMIO strobe synced with RTL
if grep -q 'cs = psel && penable' "$MMIO_SPEC" \
   && grep -q 'apb_to_mmio.v' "$MMIO_SPEC"; then
  pass "G3 mmio-spec §1.1 documents access-phase-only cs strobe"
else
  fail "G3 mmio-spec §1.1 does not document the bridge strobe"
fi

# G4: INTC PENDING full 8-source map
if grep -q 'bit\[5\] = HOST doorbell' "$MMIO_SPEC" \
   && grep -q 'bit\[4\] = PCIe' "$MMIO_SPEC" \
   && grep -q 'bit\[6\] = Timer' "$MMIO_SPEC" \
   && grep -q 'bit\[7\] = PCIe DMA' "$MMIO_SPEC" \
   && grep -q 'RESOLVED in docs (rtl-update-plan Phase 10)' "$MMIO_SPEC"; then
  pass "G4 mmio-spec §6/§7.1 reflect full 8-source INTC map"
else
  fail "G4 mmio-spec §6/§7.1 INTC bit map not synced"
fi

#------------------------------------------------------------------------------
# 3. Evidence ledger
#------------------------------------------------------------------------------
echo "[3] Writing evidence ledger"
mkdir -p "$EVIDENCE_DIR"
{
  cat <<EOF
# Phase 10 T20: MMIO spec gap closure (docs only)
# Generated: $(date '+%Y-%m-%d %H:%M:%S')
# Host: $(hostname)
# Plan reference: .omo/plans/rtl-update-plan.md (top-level conclusion L10)
# Status: DOCS-SYNC=$([ "$fail_count" -eq 0 ] && echo PASS || echo FAIL)
#
# Gap disposition: DISPOSED (documented resolution, no RTL change)
#   G1  MXU BIAS/SCALE stub
#       -> docs/func-model-mmio-spec.md §7.2: Phase 1 NOT APPLICABLE
#          (mmio_if.v offsets 0x20/0x24 exist and are writable; mxu_top.v
#           ties off bias_addr_o/scale_addr_o; controller FSM never consumes
#           them; Phase 1 testbenches drive broadcast buses directly)
#   G2  Wrapper SRAM base documentation
#       -> docs/func-model-sram-map.md §6.1: MXU reset defaults documented as
#          perf-test DRAM bases 0x8001_0000/0x8002_0000/0x8003_0000
#          (P9-B workaround for GCC -O2 APB misroute); production SRAM bases
#          0x2000_0000/0x2020_0000/0x2028_0000 noted as firmware-programmed.
#       -> §6.2: Vector wrapper defaults 0x2030_0000/0x2030_0000/0x2034_0000
#          documented (spec-compliant, matches rtl-update-plan §7.4).
#   G3  APB->MMIO write-strobe documentation sync
#       -> docs/func-model-mmio-spec.md §1.1: cs = psel && penable (single
#          latch at end of access phase). RTL rtl/wrapper/apb_to_mmio.v
#          already implements the fix; docs now match.
#   G4  INTC PENDING bit map (spec said HOST at bit[8]; RTL has bit[5])
#       -> docs/func-model-mmio-spec.md §6: full 8-source map
#          bit[0]=MXU bit[1]=SFU bit[2]=Vector bit[3]=DMA bit[4]=PCIe
#          bit[5]=HOST bit[6]=Timer bit[7]=PCIe-DMA; §7.1 marked RESOLVED.
#       -> No RTL change; documentation-only gap per rtl-update-plan §8.2.
#
# Gap disposition: REMAINING (future phase, explicitly deferred):
#   F1  BIAS/SCALE functional wiring (controller reads bias/scale SRAM and
#       applies during compute). Future phase when a golden reference requires
#       it. Registers already reserved.
#   F2  mxu_top SRAM-sourced weight/activation (DMA-style buffer fill and
#       controller address sequencing). Deferred to Phase 1.5/SoC-only change;
#       keeps existing tb_mxu.v broadcast path intact.
#   F3  Weight Bank B ping-pong preload (wrapper currently single-bank
#       contiguous preload). Requires firmware/DMA refresh or a Bank B base
#       register. Sufficient for current Phase 1/SoC tests.
#   F4  DMA linked-list descriptor chain traversal FSM (DESC_ADDR/DESC_CNT
#       reserved in register file, FSM not implemented). Future phase.
#   F5  Separate batch dimension (token count != M) as batch_cur register.
#       Current m_tile outer loop + k_tile==0 reset gives per-token reset.
#
# RTL verification checks: see script output above.
# No RTL files were modified by this task.
EOF
} > "$EVIDENCE_FILE"

if [ "$fail_count" -eq 0 ]; then
  echo "[4] RESULT: PASS - all doc/RTL assertions hold"
  echo "    Evidence: ${EVIDENCE_FILE}"
  exit 0
else
  echo "[4] RESULT: FAIL - ${fail_count} assertion(s) failed" >&2
  exit 1
fi
