#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIB="$ROOT/scripts/p9_lib"
NOTEPAD="$ROOT/.omo/notepads/phase9-firmware-rtl-fix"
mkdir -p "$LIB" "$NOTEPAD" "$ROOT/build/evidence"

cat > "$LIB/p9_sz0001.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export SZ0001="192.168.0.11"
export ZHENGS="zhengs"
p9_ssh() {
  ssh "${ZHENGS}@${SZ0001}" "set -e; source /NAS/Tools/methodology/modules/init/bash; module load vcs/vcs_2023.12sp2; cd '${REPO_ROOT}' && source sim/regression/run_env.sh && ${1-}"
}
p9_chmod() { chmod +x "$@"; }
EOF
chmod +x "$LIB/p9_sz0001.sh"

write_script() {
  local name="$1" content="$2"
  cat > "$ROOT/scripts/$name" <<EOF
#!/usr/bin/env bash
set -euo pipefail
source "\$(dirname \$0)/p9_lib/p9_sz0001.sh"
${content}
EOF
  chmod +x "$ROOT/scripts/$name"
}

# shared scripts
write_script p9_env_check.sh 'echo "[p9_env_check] placeholder"; p9_ssh "ls build/evidence/ph9-* 2>/dev/null || true; which vcs; test -f firmware/build/npu_firmware.elf; git status --short | head"'
write_script p9_fw_rebuild.sh 'p9_ssh "cd firmware && make clean && make"; elf_ts=$(p9_ssh "stat -c %Y firmware/build/npu_firmware.elf"); src_ts=$(p9_ssh "stat -c %Y firmware/npu_firmware.c"); test "$elf_ts" -gt "$src_ts"; p9_ssh "md5sum firmware/build/npu_firmware.elf > build/evidence/ph9-firmware-baseline.txt && git rev-parse HEAD >> build/evidence/ph9-firmware-baseline.txt"'
write_script p9_spike_chain.sh 'p9_ssh "PYTHONPATH=sim python3 -m sim.spike_host --mode chain --ops mmul,sfu,vector,dma_copy 2>&1 | tee build/evidence/ph9-spike-abi.txt"'
write_script p9_log_bug.sh 'case "${1-}" in --help|-h) echo "Usage: p9_log_bug.sh [--id ID --type <fw|rtl|integ> --symptom TXT --root_cause TXT --evidence PATH --verdict <resolved|open|rtl-suspect>] | [--rtl-report SLUG ...]"; echo "Options include: --id, --type, --symptom, --root_cause, --evidence, --verdict, --rtl-report"; exit 0 ;; esac; echo "[p9_log_bug] placeholder -- parses args and appends docs/bugs/bugs-soc-rtl.md"'

# final-wave audit scripts
write_script p9_f1_audit.sh 'echo "[p9_f1_audit] placeholder -- audits plan ACs"'
write_script p9_f2_code_quality.sh 'echo "[p9_f2_code_quality] placeholder"'
write_script p9_f3_manual_qa.sh 'echo "[p9_f3_manual_qa] placeholder"'
write_script p9_f4_scope_gate.sh 'echo "[p9_f4_scope_gate] placeholder"'

# per-todo scripts (stubs; executor fills implementation per plan descriptions)
write_script p9_diag_harness.sh 'echo "[p9_diag_harness] placeholder -- create sim/diagnose_mmu_path.py"'
write_script p9_divergence_sweep.sh 'echo "[p9_divergence_sweep] placeholder -- T3 divergence sweep"'
write_script p9_fix_branch_a.sh 'echo "[p9_fix_branch_a] placeholder -- T4 branch A firmware fix"'
write_script p9_fix_branch_b.sh 'echo "[p9_fix_branch_b] placeholder -- T4 branch B RTL wrapper fix"'
write_script p9_regression.sh 'echo "[p9_regression] placeholder -- T5 full regression"'
write_script p9_sram_budget.sh 'echo "[p9_sram_budget] placeholder -- T6 SRAM budget"'
write_script p9_weight_streaming.sh 'echo "[p9_weight_streaming] placeholder -- T6 weight streaming; must write build/evidence/ph9-t6-no-new-rtl.txt with T6_NO_NEW_RTL=1 and build/evidence/ph9-t6-perf-tests-layout.txt"'
write_script p9_36layer.sh 'echo "[p9_36layer] placeholder -- T7 36-layer checkpoint"'
write_script p9_perfect_batch.sh 'echo "[p9_perfect_batch] placeholder -- T8 PERF batch + fullchain multitile"'
write_script p9_q8o_download.sh 'echo "[p9_q8o_download] placeholder -- T9 Q8_0 download"'
write_script p9_q8o_precision.sh 'echo "[p9_q8o_precision] placeholder -- T9 Q8_0 precision"'
write_script p9_phase6_6b_finalize.sh 'echo "[p9_phase6_6b_finalize] placeholder -- T9 Phase 6 6b finalize"'

git -C "$ROOT" rev-parse HEAD > "$ROOT/build/evidence/ph9-base-commit.txt"
echo "Phase 9 scaffold created; base commit: $(cat "$ROOT/build/evidence/ph9-base-commit.txt")"
