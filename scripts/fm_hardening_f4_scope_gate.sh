#!/usr/bin/env bash
# =============================================================================
# fm_hardening_f4_scope_gate.sh — F4: scope fidelity (fm-hardening-phase10)
# Whitelist (paths allowed to differ PLAN_BASE..HEAD):
#   sim/, firmware/, scripts/, docs/, spec/npu_abi.json, gen/ (generated ABI
#   artifacts from scripts/gen_npu_abi.py), build/evidence/ (incl. the
#   sz0001-regenerated w4-perf files), .omo/ (plans/notepads/gate state),
#   rtl/tb/ (testbenches — explicitly allowed by the plan).
# Frozen surface (plan "Must NOT have") — any change in the range diff OR the
# working tree is scope creep:
#   rtl/ (product RTL: mxu/, sfu/, vector/, soc/, cpu/, ip/, wrapper/ — but
#   NOT rtl/tb/, which is whitelisted), sim/arc_model.py,
#   sim/design_space_explorer.py, sim/quantize.py, ggml-npu/,
#   requirements.txt.
# Exit: 0 = in scope; 1 = scope creep; 2 = environment error.
# =============================================================================
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
PLAN_BASE="${FM_PLAN_BASE:-b542cc5b36a80dd2a73ab0fd0fc1ffeb1d99447b}"
git cat-file -e "${PLAN_BASE}^{commit}" 2>/dev/null || { echo "F4: ERROR plan base not in repo"; exit 2; }

whitelisted() {
  case "$1" in
    sim/*|firmware/*|scripts/*|docs/*|build/evidence/*|.omo/*|spec/npu_abi.json|gen/*|firmware_memory_contract.json|rtl/tb/*) return 0 ;;
    *) return 1 ;;
  esac
}
frozen() {
  case "$1" in
    rtl/tb/*) return 1 ;;
    rtl/*|sim/arc_model.py|sim/design_space_explorer.py|sim/quantize.py|ggml-npu/*|requirements.txt) return 0 ;;
    *) return 1 ;;
  esac
}

FAIL=0; count=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  count=$((count+1))
  if frozen "$f"; then echo "F4: FROZEN-FILE-CHANGED: $f"; FAIL=1
  elif ! whitelisted "$f"; then echo "F4: OUT-OF-SCOPE: $f"; FAIL=1
  fi
done < <(git diff --name-only "$PLAN_BASE"..HEAD)

# Frozen files must also be clean in the working tree (uncommitted edits).
# rtl/tb/ testbenches are whitelisted and exempt from the frozen check.
WT="$(git diff --name-only HEAD -- rtl/ sim/arc_model.py sim/design_space_explorer.py \
       sim/quantize.py ggml-npu/ requirements.txt 2>/dev/null | grep -v '^rtl/tb/' || true)"
if [ -n "$WT" ]; then echo "F4: FROZEN-WORKTREE-DIRTY: $WT"; FAIL=1; fi

echo "F4: classified $count changed file(s) since $PLAN_BASE"
echo "F4: whitelist = sim/ firmware/ scripts/ docs/ spec/npu_abi.json gen/ build/evidence/ .omo/ rtl/tb/"
echo "F4: frozen    = rtl/ (excl. rtl/tb/) arc_model design_space_explorer quantize ggml-npu/ requirements.txt"
if [ "$FAIL" -eq 0 ]; then echo "F4: PASS — no scope creep"; exit 0; else echo "F4: FAIL — scope creep detected"; exit 1; fi
