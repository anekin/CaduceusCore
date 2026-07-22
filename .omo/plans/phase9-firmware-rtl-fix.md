# phase9-firmware-rtl-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** CaduceusCore 修复固件门铃路径里 M=1 多 tile 矩阵乘的发散（解 5 个 PERF 用例），加上固件 per-K-tile 权重流式重载（解 36 层全量 RTL forward），再下载 Q8_0 模型完成 Phase 6 控制实验。修复后所有性能用例 cos_sim≥0.999，36 层 checkpoint 通过，回归零退化。

**Why this approach:** 先诊断后改——根因尚未 100% 隔离在固件 MMIO 写冗余还是 RTL wrapper 广播计数，所以先只读探针 dump 三档 case 的 MMIO/AXI 序列再按证据决定改固件还是改 RTL。启用 Spike+插件调试路径（Phase 7 已修 ABI）以快速定位 MMIO 序列。固件承担 per-K-tile 权重 DMA 分段，cocotb_bridge.py 保持只读 import 以守住范围边界。

**What it will NOT do:** 不动 Arc Model；不引入新引擎/精度；不改 cocotb_bridge.py；不改 RTL 任何模块（除非诊断指向 wrapper 才动 mxu_soc_wrapper.v）；不用 Q8_0 做非 Phase 6 6b 用途；不重编 Spike 插件；诊断阶段不注入 RTL $display 污染时序。

**Effort:** Large
**Risk:** Medium — 主因诊断结论 (C) 不确定时需用户 checkpoint；Q8_0 外网下载可能失败（有 fallback）。RTL 改动会触发全芯片重编。
**Decisions to sanity-check:** (1) 诊断三档 case 不足以 localize 时是否扩档；(2) branch A 注释三行 MMIO 写是否会破坏其他 wrapper 路径；(3) per-K-tile DMA offset 公式是否与 pack_int4_tile_major 输出一致。

Your next move: 等双 high-accuracy 审阅 OKAY 后 approve, 然后执行 `/start-work phase9-firmware-rtl-fix`. Full execution detail follows below.

---

> TL;DR (machine): Large / Medium — M=1 multi-tile 固件/RTL 发散修复 + per-K-tile 权重流式 + Q8_0 Phase 6 6b，9 todo + F1-F4。

## Scope
### Must have
- **脚本优先原则（用户强制）**: 任何工具调用、EDA 环境变量设置、cocotb 调用、固件重编、SSH 执行都必须封装为 `scripts/p9_*.sh`（或 `scripts/p9_*.py`）脚本；todo 内只允许 `bash scripts/p9_<name>.sh [args]` 一个入口，不允许 inline shell 命令字面量直接传到 SSH。脚本必须可被任意 agent 复用。Wave 0 第 0.5 步先建共用脚本目录 `scripts/p9_lib/`（含 sz0001 SSH wrapper + VCS module load wrapper），后续所有脚本 import/`source` 它。
- **所有验证在 sz0001（用户强制）**: 不调 EDA 的 pytest、固件重编、Q8_0 对比、Python 诊断全部通过 `ssh zhengs@192.168.0.11` 在 sz0001 上执行，保持环境一致。禁止在本机直接跑任何验证命令；所有验证脚本头部 `source scripts/p9_lib/p9_sz0001.sh`（统一 SSH + VCS env + cd repo）。
- M=1 multi-tile firmware/RTL divergence root-caused and fixed (PERF-01/04/05/06/11/13/17 all cos_sim>=0.999)
- Firmware per-K-tile weight streaming for K=2560 Q_proj within 4MB SRAM (unblocks 36-layer)
- 36-layer L0/L10/L20 cos_sim>=0.999 on RTL (sz0001)；L35 cos_sim>=0.997（与 `scripts/run_36layer_checkpoint.py:106-107` 内部阈值一致）
- Fullchain multi-tile (K=256,N=256) cos_sim>=0.999
- Q8_0 GGUF downloaded + Q_proj precision control experiment + Phase 6 6b finalized by threshold
- Full regression after every fix wave: pytest 210+, FM-SOC 33/33, MXU 9/9, SFU 319/319, Vector 63/63 (all on sz0001)
- `rtl/testcase-list-perf.md` all FAIL→PASS sync
- `docs/issues_found.md` Phase 9 Resolution Status + Condition Disposition
- `build/evidence/ph9-closure.txt` with FIXED / REST NOT RESOLVED / Phase 10 forward
- Causality gate evidence `build/evidence/ph9-causality.txt` (PERF-11 K<=64 vs K=512)
- Firmware rebuild gate enforced after any firmware edit
- **Bug-tracking 强制（用户）**: 修复过程中发现的任何固件/RTL/集成 bug，发现即追加到 `docs/bugs/bugs-soc-rtl.md`（沿用现有 `BUG-RTL-SOC-NNN` 模板，含 Symptom/Root Cause/Fix/Verification 块），不攒批不删；**疑似 RTL bug** 额外产独立 bug report 单文件 `docs/bugs/BUG-MXU-P9-NNN-<short-slug>.md` 含 Symptom / Root Cause Hypothesis / Evidence (引用 probe JSON + divergence report) / Repro (脚本路径 + case) / Proposed Fix / Root Cause Verdict 六块，提交到 git；T3 诊断结论为 (B) 或 (C) 时必产独立 bug report；T6 weight streaming 发现的固件/SRAM bug 同样追加。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- No Arc Model / DSE changes
- No new engine types, no Q4_K downgrades, no BF16 support
- No `sim/cocotb_bridge.py` source modification (read-only import only) — Oracle issue 6
- No Phase 8 plan 6b checkbox touched (Phase 6 only)
- No RTL changes outside `rtl/wrapper/mxu_soc_wrapper.v` — and only if T3 concludes branch B
- No Q8_0 used outside Phase 6 6b experiment
- No Spike plugin rebuilt (Phase 7 already fixed ABI; Wave 0 re-verifies only; ABI break = HALT as Phase 7 defect)
- No Wave 1 RTL `$display`/`$strobe` injection that perturbs timing — probes via FSDB/VCD only; `git diff -- rtl/ firmware/` empty after Wave 1
- No Wave 2 execution if T3 concludes (C) inconclusive — must HALT for user checkpoint
- **No inline 直接 SSH/命令字面量在 todo 内**: 所有命令必走 `scripts/p9_*.sh`（违者 todo 视为 NOT-executable，F1 reject）
- **No 在本机（非 sz0001）跑任何验证命令**: 所有验证脚本必须经 `bash scripts/p9_*.sh`（脚本内部 SSH 到 sz0001 或假定已 SSH 进 sz0001 后被调用），F4 守此 gate
- **No 修复 bug 而不登记**: 任何 ProviderError/FIX 后必须先在 `docs/bugs/bugs-soc-rtl.md` 追加条目再 commit；疑似 RTL bug 必产独立 bug report 单文件（违者 F1/F3 reject）
- **No 删除/压缩 bug 报告**: 修复后 Status→Fixed，条目保留历史，bug report 单文件保留

## Verification strategy
> Zero human intervention - all verification is agent-executed, all on sz0001, all via scripts.
- 脚本目录: `scripts/p9_lib/`（共用 SSH+VCS env 包装）+ `scripts/p9_*.sh`（每个 todo 验证动作一个脚本）；脚本头部一律 `source $(dirname $0)/p9_lib/p9_sz0001.sh` 拿到 REPO_ROOT/VCS/SZ0001 变量。任何复盘 bug 的脚本也走同一入口，保证复现一致。
- Test decision: tests-after (fix first, then regression) + pytest for Python, VCS for RTL, huggingface-cli for Q8_0 — all wrapped in `scripts/p9_*.sh`
- Evidence: `build/evidence/ph9-*.txt|jsonl` (pre-check `ls build/evidence/ph9-*` at Wave 0; archive conflicts as `ph9-v0-*`); 写入路径也走脚本统一到 sz0001 的 `build/evidence/`（NFS 共享）
- Bug-tracking: 每个 fix commit 前 `bash scripts/p9_log_bug.sh --id BUG-RTL-SOC-NNN --type <fw|rtl|integ> --symptom ... --root_cause ... --evidence <path> --verdict <resolved|open|rtl-suspect>`；疑似 RTL bug 额外触发 `bash scripts/p9_log_bug.sh --rtl-report <slug>` 产出独立 `docs/bugs/<slug>.md` 文件含六块根因分析。
- Causality gate (Metis G8/G11): PERF-11 K<=64 AND K=512 run after T4 via `bash scripts/p9_causality.sh` to prove doorbell fix is causal, not coincidental with streaming
- Read-only diagnostic gate: `git diff -- rtl/ firmware/ | wc -l` ==0 after Wave 1（在 sz0001 上查）
- Firmware/simv rebuild gate: binary timestamp newer than source after any relevant edit (check 在 sz0001)

## Execution strategy
### Parallel execution waves
- Wave 0 (serial): T1 脚本骨架 + Spike ABI + firmware baseline → T2 诊断 harness
- Wave 1 (serial): T3 divergence sweep (read-only) → 必产 RTL bug report if (B)/(C)
- Wave 2 (serial): T4 branch A or B fix (含 bug-log 步骤) → T5 regression
- Wave 3 (serial): T6 SRAM budget + per-K-tile streaming (含 bug-log) → T7 36-layer checkpoint
- Wave 4 (parallel): T8 full PERF + fullchain multi-tile + docs + closure
- Wave 5 (parallel with Wave 4): T9 Q8_0 + Phase 6 6b (network-independent)
- Final (parallel after T8 AND T9 finished-or-BLOCKED-NETWORK): F1-F4
- **T9 网络失败短路规则**: 若 T9 写 `build/evidence/ph9-q8_0-download-FAILED.txt`，T9 标 BLOCKED-NETWORK 并 commit；F-wave 不再依赖 T9 6b 实测证据，F4 改为只验证"6b 状态字段已按 BLOCKED-NETWORK 规则写入 phase6 plan + docs/issues_found.md"。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 | none | 2,3,9 | — |
| 2 | 1 | 3 | — |
| 3 | 2 | 4 | — |
| 4 | 3 | 5 | — |
| 5 | 4 | 6 | — |
| 6 | 5 | 7 | — |
| 7 | 6 | 8 | — |
| 8 | 7 | F1-F4 | 9 |
| 9 | 1 | F1-F4 (终止条件见上) | 8 |
| F1-F4 | 8, 9 (或 9 BLOCKED-NETWORK) | — | — |

### Phase 9 脚本清单（所有 todo 入口必走脚本，全部在 sz0001 上执行）
> T1 step 0 创建此清单的脚手架；T1 之后所有 todo 的 What-to-do 只允许出现 `bash scripts/p9_<name>.sh [args]` 一行入口；脚本内部由 `source $(dirname $0)/p9_lib/p9_sz0001.sh` 拿到 `REPO_ROOT`、`SZ0001`、`ZHENGS`、VCS env、`p9_ssh()` 函数。脚本本身在本地 repo 提交、由 sz0001 上的 git pull/checkout 同步；剧本内禁止任何 inline `ssh ...` 或 `module load` 字面量传过 todo 文本之外。

`scripts/p9_bootstrap_scaffold.sh` — **唯一 bootstrap 脚本**（T1 step 0 调用）：创建 `scripts/p9_lib/`、`scripts/p9_env_check.sh`、`scripts/p9_fw_rebuild.sh`、`scripts/p9_spike_chain.sh`、`scripts/p9_log_bug.sh`、`scripts/p9_f1_audit.sh`、`scripts/p9_f2_code_quality.sh`、`scripts/p9_f3_manual_qa.sh`、`scripts/p9_f4_scope_gate.sh`，全部 `chmod +x`，并写 `build/evidence/ph9-base-commit.txt`（`git rev-parse HEAD`，作为 F2/F4 scope diff 的 baseline）。此脚本本身由 executor 在 T1 前一次性创建（内容见本清单下方的 "Bootstrap script content" 代码块），创建后 T1 只通过 `bash scripts/p9_bootstrap_scaffold.sh` 一个入口执行；T1 之后所有 todo 不再允许描述性创建步骤。

`scripts/p9_lib/p9_sz0001.sh` — 共用 wrapper，导出 `REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)`、`SZ0001=192.168.0.11`、`ZHENGS=zhengs`，函数 `p9_ssh()`：内部 `ssh "${ZHENGS}@${SZ0001}" "set -e; source /NAS/Tools/methodology/modules/init/bash; module load vcs/vcs_2023.12sp2; cd '${REPO_ROOT}' && ${1-}"`；提供 `p9_chmod` 辅助函数确保新生成脚本能执行。所有 `scripts/p9_*.sh` 头部 `source "$(dirname "$0")/p9_lib/p9_sz0001.sh"`。

`scripts/p9_env_check.sh` — Phase 9 环境前置检查；脚本经 p9_ssh 在 sz0001 上检查 evidence 目录、VCS 可执行性、firmware ELF 存在性与 git 状态；冲突文件 archive 到 `ph9-v0-*`，写到 `.omo/notepads/phase9-firmware-rtl-fix/learnings.md`。

`scripts/p9_fw_rebuild.sh` — 在 sz0001 上重编 firmware，校验 elf 新于源，写 firmware md5 与基线 commit 到 `build/evidence/ph9-firmware-baseline.txt`。

`scripts/p9_spike_chain.sh` — 在 sz0001 上跑 Spike chain mode ABI 复测，结果写入 `build/evidence/ph9-spike-abi.txt`。

`scripts/p9_log_bug.sh` — bug-tracking 入口；`--id BUG-RTL-SOC-NNN --type <fw|rtl|integ> --symptom <txt> --root_cause <txt> --evidence <path> --verdict <resolved|open|rtl-suspect>`，追加到 `docs/bugs/bugs-soc-rtl.md`；`--rtl-report <slug>` 生成 `docs/bugs/<slug>.md`；the caller provides the complete ID (e.g., `BUG-MXU-P9-001-doorbell-divergence`)；`--help` 打印说明（必须含 `--rtl-report` 字样）。脚本本身只编辑 docs/ 不改源码。

`scripts/p9_diag_harness.sh` — 创建并 AST 校验 `sim/diagnose_mmu_path.py`（内容由脚本写盘，避免 todo 内 inline python heredoc）。

`scripts/p9_divergence_sweep.sh` — 跑 T3 三档 case，写 `build/evidence/ph9-divergence-report.txt`；内部在结论为 (B)/(C) 时调 `p9_log_bug.sh --rtl-report BUG-MXU-P9-001-doorbell-divergence`。

`scripts/p9_fix_branch_a.sh` — T4 branch A：编辑 firmware 注释三行 MMIO、重编、跑 directed testcase、跑 causality gate；commit 前由脚本内部调用 p9_log_bug 记录 BUG-RTL-SOC-P9-00A。

`scripts/p9_fix_branch_b.sh` — T4 branch B：改 RTL wrapper、全芯片重编、跑 directed test、跑 causality gate；commit 前由脚本内部调用 p9_log_bug 生成 BUG-MXU-P9-00B-broadcast-multitile 独立报告。VCS 编译命令：`vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -f rtl/cpu/ibex.flist -f rtl/ip/verilog-axi.flist -f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist -top tb_soc rtl/tb/tb_soc.v +define+COCOTB_SIM=1 +vpi -P sim/regression/pli.tab -load $(cocotb-config --lib-name-path vpi vcs) -o build/p9_simv_soc_top -l build/p9_soc_elaborate.log`，校验 exit code=0；过程 log 入 `build/evidence/ph9-t4b-elapsed.txt`（含 `VCS_EXIT_CODE=0`）。

`scripts/p9_regression.sh` — T5 全模块回归入口（pytest + FM-SOC + MXU + SFU + Vector），tee 到 `build/evidence/ph9-regression-*.log`。

`scripts/p9_sram_budget.sh` — T6 step 1：写 `build/evidence/ph9-sram-budget.txt`，断言 peak SRAM < 4MB。

`scripts/p9_weight_streaming.sh` — T6 step 2-4：改写 firmware 的 per-K-tile DMA 流程并跑 K=512 directed run；commit 前由脚本内部条件调用 p9_log_bug 记录 BUG-RTL-SOC-P9-00C；脚本必须写 `build/evidence/ph9-t6-no-new-rtl.txt` 与 `build/evidence/ph9-t6-perf-tests-layout.txt`。

`scripts/p9_36layer.sh` — T7：调已有 36-layer checkpoint 脚本跑指定 4 层，把硬编码输出复制为 `build/evidence/ph9-36layer-checkpoint.txt` 并追加 Phase 9 头与 timestamp。

`scripts/p9_perfect_batch.sh` — T8：跑全 PERF 批次并覆盖 stale-state 字段；新增 `test_w4_perf_fullchain_multitile` 到 `sim/perf_tests.py`；同步 testcase-list-perf.md、docs/issues_found.md 并生成 closure。

`scripts/p9_q8o_download.sh` — T9 step 1：下载 Q8_0 GGUF，带重试与失败兜底；内部使用 `timeout 600 huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q8_0.gguf --local-dir ~/models --local-dir-use-symlinks False`，最多重试 3 次；失败时写 `build/evidence/ph9-q8_0-download-FAILED.txt`。

`scripts/p9_q8o_precision.sh` — T9 step 2：调已有无 CLI 的 precision 脚本，把硬编码输出复制为 `build/evidence/ph9-q8_0-precision.txt`。

`scripts/p9_phase6_6b_finalize.sh` — T9 step 3-4：按阈值改 `phase6-rtl-verification.md:107` 的 6b checkbox 状态 + 同步 `docs/issues_found.md`。

`scripts/p9_f1_audit.sh` — F1 plan compliance audit：检查所有 todo checkbox 为 `[x]`；读取每条 acceptance criterion 并执行 grep/test；输出 `build/evidence/f1-audit.log`，含 `CHECKBOX_OK`、`F1-AUDIT-PASS` 或 `FAIL:<line>:<AC>`；失败时写 `build/evidence/f1-fail-summary.txt`。

`scripts/p9_f2_code_quality.sh` — F2 code quality review：以 `build/evidence/ph9-base-commit.txt` 为 baseline 执行 `git diff --name-only`，校验只有白名单文件被改、bridge 未动、`sim/perf_tests.py` / `sim/diagnose_mmu_path.py` AST OK；输出 `build/evidence/f2-file-diff.txt`（含 `BRIDGE_UNCHANGED=1`、`SCOPE_CREEP=0`）和 `build/evidence/f2-ast.txt`（含 `AST_OK=1`）。

`scripts/p9_f3_manual_qa.sh` — F3 real manual QA：检查 `build/evidence/ph9-causality.txt` 含 `K<=64:` 与 `K=512:` 且 K<=64 cos_sim>=0.999；检查 RTL bug report 的 `Root Cause Verdict` 块（若存在）；检查 `build/evidence/ph9-fullchain-multitile.txt` hex 非全零；输出 `build/evidence/f3-checklist.txt` 含 `CAUSALITY_OK=1`、`HEX_NONZERO=1`、`BUG_VERDICT_OK=(1|N/A)`。

`scripts/p9_f4_scope_gate.sh` — F4 scope fidelity gate：以 `build/evidence/ph9-base-commit.txt` 为 baseline，检查 RTL 只改 `rtl/wrapper/mxu_soc_wrapper.v`、Phase6 6b judge 字段存在、Spike plugin 未重建、BLOCKED-NETWORK 短路规则；输出 `build/evidence/f4-gate.txt` 含 `RTL_SCOPE_OK=1`、`Q8O_JUDGE_OK=1`、`SPIKE_PLUGIN_UNCHANGED=1`。

### Bootstrap script content（`scripts/p9_bootstrap_scaffold.sh` 一次性创建内容）
> 以下代码块是 T1 step 0 要创建的脚本内容。executor 把此内容写入 `scripts/p9_bootstrap_scaffold.sh` 并 `chmod +x`，然后运行 `bash scripts/p9_bootstrap_scaffold.sh`。该脚本负责创建所有共用脚本与 lib，并写 `build/evidence/ph9-base-commit.txt`。

```bash
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
```

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 0 — Diagnose setup (serial)

- [x] 1. 共用脚本骨架 + bug logger 建立（script-first / all-on-sz0001 / bug-tracking 三条用户约束的脚手架）+ Spike ABI re-verify + firmware rebuild baseline hash + evidence naming pre-check
  What to do:
    0. **Bootstrap（唯一允许的脚本创建步骤）**: 把 "Phase 9 脚本清单" 中 "Bootstrap script content" 代码块内容写入 `scripts/p9_bootstrap_scaffold.sh`，然后 `bash scripts/p9_bootstrap_scaffold.sh`。该脚本负责创建全部共用脚本、lib 目录与基线提交 marker。
    1. `bash scripts/p9_env_check.sh`（Phase 9 环境前置检查；内部在 sz0001 上 `ls build/evidence/ph9-*`；冲突文件 archive 为 `ph9-v0-*` 并写 `.omo/notepads/phase9-firmware-rtl-fix/learnings.md`）。
    2. `bash scripts/p9_fw_rebuild.sh`（重编 firmware；记录 md5 + git HEAD 到 `build/evidence/ph9-firmware-baseline.txt`）。
    3. `bash scripts/p9_spike_chain.sh`（Spike chain mode ABI re-verify；tee `build/evidence/ph9-spike-abi.txt`）。
    4. 若 ABI mismatch 持续：写 `build/evidence/ph9-abi-mismatch.txt` 并 HALT — 这是 Phase 7 缺陷，不许继续 Wave 1。
  Must NOT do:
    - 不修改 `spike_src/plugins/npu_mmio_plugin.so` (Phase 7 已修)
    - 不修改 RTL/firmware 源（rebuild 只重编现有源）
    - 不跳过 baseline hash 记录
    - 不在 todo 内写 inline `ssh`/`module load`/`make` 字面量（违反 SCRIPT-FIRST）
  Parallelization: Wave 0 | Blocked by: none | Blocks: 2,3,9 | Can parallelize with: none
  References:
    - `firmware/npu_firmware.c`（源）
    - `firmware/Makefile`（rebuild）
    - `spike_src/plugins/npu_mmio_plugin.so`（Phase 7 ABI）
    - `.omo/plans/phase7-blocker-fix.md`（Phase 7 Spike 修复记录）
    - `build/evidence/ph7-spike-fixed.txt`（Phase 7 ABI 修复证据）
    - `docs/bugs/bugs-soc-rtl.md`（bug logger 追加目标，模板复用）
  Acceptance criteria (agent-executable):
    - `test -x scripts/p9_bootstrap_scaffold.sh -a -x scripts/p9_lib/p9_sz0001.sh -a -x scripts/p9_log_bug.sh -a -x scripts/p9_env_check.sh -a -x scripts/p9_fw_rebuild.sh -a -x scripts/p9_spike_chain.sh -a -x scripts/p9_f1_audit.sh -a -x scripts/p9_f2_code_quality.sh -a -x scripts/p9_f3_manual_qa.sh -a -x scripts/p9_f4_scope_gate.sh`（bootstrap 与共用脚本骨架已建且可执行）
    - `test -s build/evidence/ph9-base-commit.txt`
    - `bash scripts/p9_log_bug.sh --help 2>&1 | grep -q -- '--rtl-report'`
    - `grep -qE 'p9_ssh\(\)|SZ0001=' scripts/p9_lib/p9_sz0001.sh`（lib 文件含 SSH wrapper 函数与 sz0001 主机变量）
    - `test -s build/evidence/ph9-firmware-baseline.txt`
    - `grep -qE '^[a-f0-9]{32}  firmware/build/npu_firmware\.elf' build/evidence/ph9-firmware-baseline.txt`（md5sum 格式正确）
    - `grep -qi 'chain' build/evidence/ph9-spike-abi.txt`
    - `! grep -qiE 'ABI|undefined symbol|mismatch' build/evidence/ph9-spike-abi.txt`
  QA scenarios:
    - Happy: 重编成功，Spike chain 模式跑通无 ABI error；evidence 命名无冲突；`p9_log_bug.sh --help` 含 `--rtl-report`。
    - Failure: ABI mismatch 持续 → 写 `build/evidence/ph9-abi-mismatch.txt`，标 Phase 7 缺陷，HALT。
    - Evidence: `build/evidence/ph9-firmware-baseline.txt`, `build/evidence/ph9-spike-abi.txt`
  Commit: Y | `diag(phase9): script scaffold + Spike ABI re-verify + firmware baseline hash`

- [x] 2. Build read-only diagnostic harness `sim/diagnose_mmu_path.py`
  What to do:
    1. `bash scripts/p9_diag_harness.sh`（内部创建 `sim/diagnose_mmu_path.py` 并 AST 校验；脚本内容见 References 下方的 "Diagnostic harness signals" 列表；脚本负责避免 todo 内 inline python heredoc）。
    2. `bash scripts/p9_diag_harness.sh --verify-readonly`（内部检查 `git diff -- rtl/ firmware/` 为空；检查 `sim/diagnose_mmu_path.py` 含 `fsdbDumpvars` / `backdoor` / `cocotb` 关键字）。
  Diagnostic harness signals (script must dump these probes to `build/evidence/ph9-probe-<case>.jsonl`):
    - 固件 MMIO 写序列：`npu_firmware.c:199-206` 写 `I_ADDR/W_ADDR/O_ADDR/SCALE_ADDR/CTRL/DIM0/DIM1/CMD` 的每一次 APB 写地址+值
    - wrapper 预加载寄存器：`mxu_soc_wrapper.v:165-169` `wrp_weight_base/wrp_act_base/wrp_out_base/wrp_k_tiles/wrp_n` + `wrp_trigger` (`mxu_soc_wrapper.v:173`) + `wrp_load_done` (`:170`)
    - preload FSM 状态：`pl_state` (`mxu_soc_wrapper.v:289`) + `pl_beat_cnt` (`:290`) + `pl_k_tile_cnt` (`:291`) + `pl_cur_addr` (`:292`)
    - broadcast driver：`tile_cycle` (`:412`), `tile_active` (`:413`), `tile_k_cur` (`:414`), `burst_cnt` (`:415`)
    - store-out FIFO：`so_fifo_wr_ptr` (`:509`), `so_capture_row` (`:501`, `wire`), `so_state` 声明在 `:525`（FSM 常量 `:477-479`），`so_base_addr` (`:575`), `so_beats` (`:578`)
    - AXI AR 通道：`m_axi_araddr/arlen/arvalid` (`assign` 在 `:392/:394/:398`；port 声明在 `:90/:91/:94`)
    - AXI W/AW 通道：`m_axi_awaddr/awlen/wvalid` (`assign` 在 `:591/:592/:617`；port 声明在 `:68/:69/:79`)
  Must NOT do:
    - 不修改 `rtl/` 或 `firmware/` 任何源文件
    - 不注入 RTL `$display`/`$strobe`（会改时序，污染观测）
    - 不在诊断阶段动 `sim/perf_tests.py` 主路径
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 3 | Can parallelize with: none
  References:
    - `firmware/npu_firmware.c:179-208` (`mxu_wrapper_preload`, `mxu_start`)
    - `rtl/wrapper/mxu_soc_wrapper.v:154-170` (APB 寄存器), `:289-362` (preload FSM), `:402-467` (broadcast driver), `:477-631` (store-out FSM + AXI)
    - `vcs` skill (FSDB dump)
    - `verdi-fsdb` skill (NPI 波形读取)
    - `sim/rtl_soc_runner.py` (FM-SOC cocotb backdoor 读信号参考)
  Acceptance criteria (agent-executable):
    - `test -s sim/diagnose_mmu_path.py`
    - `python3 -c "import ast; ast.parse(open('sim/diagnose_mmu_path.py').read()); print('AST OK')"`
    - `git diff -- rtl/ firmware/ | wc -l` 等于 0
    - `grep -q 'fsdbDumpvars\|backdoor\|cocotb' sim/diagnose_mmu_path.py` (确认走信号访问而非 RTL 注入)
  QA scenarios:
    - Happy: 脚本 AST OK，git diff RTL/firmware 空探针走 FSDB。
    - Failure: 若任何探针必须改 RTL 才能取 → 标诊断"被污染"，写 `build/evidence/ph9-diagnostic-contaminated.txt` 解释，降级证据为 advisory only。
    - Evidence: `sim/diagnose_mmu_path.py`, `build/evidence/ph9-probe-*.jsonl`
  Commit: Y | `diag(phase9): read-only M=1 multi-tile MMIO/wrapper diagnostic harness`

### Wave 1 — Fail-first divergence isolation (serial)

- [x] 3. 3-case M=1 divergence sweep: direct preload vs firmware doorbell
  What to do:
    1. `bash scripts/p9_divergence_sweep.sh`（脚本在 sz0001 上跑 3 个 M=1 发散用例：CASE 1 (K=128,N=64)、CASE 2 (K=512,N=128)、CASE 3 (K=2048,N=256)；每个 case 对比 direct wrapper preload 与 firmware doorbell 两条路径，写 `build/evidence/ph9-divergence-report.txt`）。
    2. 报告必须以单独一行给出 EXACTLY ONE 结论，行格式为 `CONCLUSION: (A|B|C): <text>`，引用至少一条 `file:line`：
       - (A) 根因是固件 MMIO 写冗余/冲突：引用 `npu_firmware.c:199-201` 与 wrapper 内部 buffer 地址不一致的具体证据
       - (B) 根因是 RTL wrapper broadcast/store-out 计数错：引用 `mxu_soc_wrapper.v` 的 `burst_cnt/tile_k_cur/so_beats` 实际值 vs 期望值
       - (C) 证据不足：另写 `build/evidence/ph9-divergence-inconclusive.txt`，列排序假设与深探方向，HALT 等用户 checkpoint，**不进 Wave 2**
    3. **Bug-tracking 强制（Scope line 36）**: 若结论为 (B) 或 (C)，由 `p9_divergence_sweep.sh` 内部统一调用 `p9_log_bug.sh` 生成 `docs/bugs/BUG-MXU-P9-001-doorbell-divergence.md` 并追加到 `docs/bugs/bugs-soc-rtl.md`。
    4. 若 3 个 case 发散模式一致（相同 cs 跌幅 + 相同 beat 模式）→ 单一 case 已足够 localize；若不一致 → 报告本身就 localize 了 bug（K 依赖 vs N 依赖）。
  Must NOT do:
    - 不在 (C) 结论后私自继续 Wave 2
    - 不改任何源码（`git diff -- rtl/ firmware/` 必须为 0）
    - 不接受 grep-only 结论 — 报告必须含具体探针数值（每个 case 至少 5 个信号采样 + cos_sim）
    - 不在结论 (B)/(C) 时跳过 `p9_log_bug.sh --rtl-report`
  Parallelization: Wave 1 | Blocked by: 2 | Blocks: 4 | Can parallelize with: none
  References:
    - `build/evidence/ph8-diagnostic.txt` (direct preload cs=1.0 baseline)
    - `build/evidence/w4-perf-p3.txt` (Phase 8 P3 cs 矩阵)
    - `firmware/npu_firmware.c:395-456` (`dispatch_cmd` MMUL 循环)
    - `rtl/wrapper/mxu_soc_wrapper.v:289-362` (preload FSM), `:402-467` (broadcast), `:477-631` (store-out)
    - `sim/diagnose_mmu_path.py`（T2 产物）
    - `docs/bugs/bugs-soc-rtl.md`（追加目标）
  Acceptance criteria (agent-executable):
    - `test -s build/evidence/ph9-divergence-report.txt`
    - `grep -qE '^CONCLUSION: \((A|B|C)\): ' build/evidence/ph9-divergence-report.txt`（恰好一个结论行）
    - `grep -cE '^CASE [123]:' build/evidence/ph9-divergence-report.txt` 等于 3（3 case 都有）
    - `grep -cE 'npu_firmware.c:[0-9]+|mxu_soc_wrapper.v:[0-9]+' build/evidence/ph9-divergence-report.txt` ≥ 1（引用 file:line）
    - `git diff -- rtl/ firmware/ | wc -l` 等于 0（诊断全程只读）
    - 若 report 含 `CONCLUSION: (B)` 或 `(C)`：`test -f docs/bugs/BUG-MXU-P9-001-doorbell-divergence.md && grep -q 'Root Cause Verdict' docs/bugs/BUG-MXU-P9-001-doorbell-divergence.md`（独立 bug report 已生成）
    - `grep -cE 'cos_sim=[0-9]\.[0-9]+' build/evidence/ph9-divergence-report.txt` ≥ 3（每 case 至少一个数值化的 cos_sim）
    - `test -n "$(ls build/evidence/ph9-probe-*.jsonl 2>/dev/null)"`（至少一个 probe JSONL 产物已生成——T2 harness 的 FSDB 探针在 T3 divergence sweep 时实际触发生成）
  QA scenarios:
    - Happy: 3 case 发散模式一致，结论 (A) 或 (B) 明确，引用 file:line 不少于 1 条；若 (B)/(C) 则独立 bug report 已落盘。
    - Failure(C): 写 inconclusive 报告 + 深探假设列表 + 独立 bug report，HALT 等用户 checkpoint。
    - Evidence: `build/evidence/ph9-divergence-report.txt` (+ `build/evidence/ph9-divergence-inconclusive.txt` in fallback), `docs/bugs/BUG-MXU-P9-001-doorbell-divergence.md` (fallback if B/C)
  Commit: Y | `diag(phase9): M=1 multi-tile divergence isolated to exact root cause`

### Wave 2 — Fix per diagnostic conclusion (serial, branch by T3 conclusion)

- [x] 4. FIX per T3 conclusion — branch A (firmware) OR branch B (RTL wrapper) [RESOLVED: compiler-stable MMIO base pointers in npu-regmap.h + per-K-block firmware dispatch + RTL ctrl_acc_mode accumulate mode + dynamic SRAM/DRAM layout; all 3 sweep cases cos_sim=1.000000]
  Execution condition: T3 报告含 `CONCLUSION: (A)` 或 `CONCLUSION: (B)`；若 `(C)` 则 HALT 不执行本 todo。
  What to do (branch A, 当 T3 报告含 `CONCLUSION: (A)`):
    1. `bash scripts/p9_fix_branch_a.sh`（脚本内部完成以下全部 substep）：
       a. 编辑 `firmware/npu_firmware.c:199-201`，注释 `mxu_start()` 中的 I/W/O_ADDR 三行（每行注释后加 `// P9-A` 标记），保留 SCALE/CTRL/DIM/CMD 写；
       b. 重编固件并校验 `firmware/build/npu_firmware.elf` 新于源码；
       c. 新增 cocotb 定向用例 `test_w4_perf_p9_directed_sweep` 到 `sim/perf_tests.py`，确保编译产物存在后跑此用例，结果写入 `build/evidence/ph9-t4a-directed.log`；
       d. Causality gate：跑 PERF-11 K<=64 与 K=512 两路，结果写入 `build/evidence/ph9-causality.txt`；若 K<=64 的 cos_sim>=0.999 则因果成立；
       e. **Bug-tracking 强制**: commit 前由脚本内部调用 `p9_log_bug.sh` 记录 `BUG-RTL-SOC-P9-00A` 并追加到 `docs/bugs/bugs-soc-rtl.md`。
  Must NOT do:
    - 不改 RTL
    - 不动 `sim/cocotb_bridge.py`
    - 不跳过固件重编 gate
    - 不跳过 bug-logging step（违反 Must NOT have line 50）
  Parallelization: Wave 2 | Blocked by: 3 | Blocks: 5 | Can parallelize with: none（与 branch B 互斥，实际只跑一支）
  References:
    - `firmware/npu_firmware.c:179-208` (`mxu_wrapper_preload`, `mxu_start`)
    - `firmware/Makefile`
    - `build/evidence/ph9-divergence-report.txt`（T3 结论 A）
    - `.omo/notepads/phase8-perf-harness-fix/issues.md:11-15`（PERF-01 root cause 怀疑 MMIO 冲突）
    - `docs/bugs/bugs-soc-rtl.md`（bug logger 追加目标）
    - `sim/perf_tests.py:195-306`（既有 testcase 形式参考）
  Acceptance criteria (agent-executable):
    - `grep -q '^async def test_w4_perf_p9_directed_sweep' sim/perf_tests.py`（新函数已加）
    - `git diff firmware/npu_firmware.c | grep -cE '^\-.*mxu->I_ADDR|^\-.*mxu->W_ADDR|^\-.*mxu->O_ADDR'` ≥ 3（至少三行注释了 I/W/O_ADDR MMIO 写）
    - `git diff firmware/npu_firmware.c | grep -cE '^\+.*// P9-A'` ≥ 3（至少三行加了 `// P9-A` 标记）
    - `test firmware/build/npu_firmware.elf -nt firmware/npu_firmware.c`
    - `grep -qE 'cos_sim=(0\.999[0-9]|1\.0)' build/evidence/ph9-t4a-directed.log`
    - `test -s build/evidence/ph9-causality.txt`
    - `grep -q '^K<=64:' build/evidence/ph9-causality.txt && grep -q '^K=512:' build/evidence/ph9-causality.txt`
    - `grep -A5 'BUG-RTL-SOC-P9-00A' docs/bugs/bugs-soc-rtl.md | grep -q 'verdict=resolved'`（bug 已登记且 verdict 为 resolved）
  QA scenarios:
    - Happy: 注释三行后 directed cos_sim>=0.999，固件重编成功，bug 已 resolved 登记，causality gate K<=64 cs>=0.999。
    - Failure: 改注释后仍 cs<0.999 → 写 `build/evidence/ph9-branch-A-insufficient.txt` 并把 bug verdict 改 `open`，退回 T3 复核。
    - Evidence: `firmware/npu_firmware.c` diff, `build/evidence/ph9-causality.txt`, `build/evidence/ph9-t4a-directed.log`, `docs/bugs/bugs-soc-rtl.md` BUG-P9-00A 条目
  Commit: Y | `fix(firmware): remove redundant I_ADDR/W_ADDR/O_ADDR MMIO writes in mxu_start (P9 branch A)`

  What to do (branch B, 当 T3 报告含 `CONCLUSION: (B)`):
    1. `bash scripts/p9_fix_branch_b.sh`（内部执行全部 substep）：
       a. 编辑 `rtl/wrapper/mxu_soc_wrapper.v` 按 T3 报告引用的具体 `file:line`：修正 broadcast driver `burst_cnt` (`:415`)、`tile_k_cur` (`:414`) 对 M=1 多 tile 的复位/计数，或修正 store-out `so_beats` (`:578`)/`so_base_addr` (`:575`) 对 M=1 行数的几何；确切改字句由脚本读 `build/evidence/ph9-divergence-report.txt` 中的 `file:line` 行决定，禁扩大改动；
       b. `bash scripts/p9_fix_branch_b.sh` 内部完成 VCS 全芯片重编 + cocotb VPI，输出 `build/p9_simv_soc_top`，写 `build/evidence/ph9-t4b-elapsed.txt`（含 `VCS_EXIT_CODE=0`）；
       c. `sim/perf_tests.py` 需要 `test_w4_perf_p9_directed_sweep` 函数；本脚本注入该函数（与 branch A 相同定义；若 branch A 已执行则函数已存在，脚本幂等复用）；跑此 testcase，用 `build/p9_simv_soc_top`；
       d. Causality gate: 跑 PERF-11 K<=64 AND K=512，写 `build/evidence/ph9-causality.txt` 两行；
       e. **Bug-tracking 强制**: commit 前 `bash scripts/p9_log_bug.sh --rtl-report BUG-MXU-P9-00B-broadcast-multitile --type rtl --symptom "M=1 multi-tile broadcast/store-out geometry error" --evidence build/evidence/ph9-t4b-directed.log --verdict resolved`，产出 `docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md` 含六块根因分析。
  Must NOT do:
    - 不改 `rtl/mxu/`, `rtl/sfu/`, `rtl/vector/`, `rtl/soc/`, `rtl/ip/` 其他文件
    - 不改固件（如 T3 说根因在 RTL，固件保持不变）
    - 不动 `sim/cocotb_bridge.py`
    - 不跳过 bug-logging step
    - 不在 todo 内写 inline `ssh`/`vcs`/`make` 字面量
  Parallelization: Wave 2 | Blocked by: 3 | Blocks: 5 | Can parallelize with: none（与 branch A 互斥）
  References:
    - `rtl/wrapper/mxu_soc_wrapper.v:289-362` (preload FSM), `:402-467` (broadcast), `:477-631` (store-out)
    - `build/evidence/ph9-divergence-report.txt`（T3 结论 B 的具体 `file:line`）
    - `build/evidence/ph8-diagnostic.txt`（direct preload cs=1.0 ref）
    - `rtl/soc/soc.flist`, `rtl/cpu/ibex.flist`, `rtl/ip/verilog-*.flist`（VCS 编译）
    - `docs/bugs/bugs-soc-rtl.md`（bug logger 追加目标）
  Acceptance criteria (agent-executable):
    - `grep -q '^async def test_w4_perf_p9_directed_sweep' sim/perf_tests.py`（新函数已加，与 branch A 共享）
    - `git diff --name-only -- rtl/ | sort -u | grep -v '^rtl/wrapper/mxu_soc_wrapper.v$' | wc -l` 等于 0（只动 wrapper）
    - `test -s build/p9_simv_soc_top && test -s build/evidence/ph9-t4b-elapsed.txt && grep -q '^VCS_EXIT_CODE=0' build/evidence/ph9-t4b-elapsed.txt`（VCS elaborate 成功）
    - `test -s build/p9_soc_elaborate.log`
    - `grep -qE 'cos_sim=(0\.999[0-9]|1\.0)' build/evidence/ph9-t4b-directed.log`
    - `test -s build/evidence/ph9-causality.txt && grep -q '^K<=64:' build/evidence/ph9-causality.txt && grep -q '^K=512:' build/evidence/ph9-causality.txt`
    - `test -f docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md && grep -q 'Root Cause Verdict' docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md && grep -q 'verdict=resolved' docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md`
    - `grep -q 'BUG-MXU-P9-00B' docs/bugs/bugs-soc-rtl.md`（追加条目）
  QA scenarios:
    - Happy: wrapper 改动后 PERF-13 cos_sim>=0.999，elaboration exit 0，bug report 已落盘。
    - Failure: 改后仍 cs<0.999 → 写 `build/evidence/ph9-branch-B-insufficient.txt` 并把 bug verdict 改 `open`，退回 T3。
    - Evidence: `rtl/wrapper/mxu_soc_wrapper.v` diff, `build/p9_soc_elaborate.log`, `build/evidence/ph9-causality.txt`, `docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md`
  Commit: Y | `fix(rtl/wrapper): correct M=1 multi-tile broadcast/store-out count (P9 branch B)`

- [x] 5. Regression suite after T4 fix + (firmware OR simv) rebuild gate
  What to do:
    1. `bash scripts/p9_regression.sh`（脚本内部执行全部 substep）：
       a. Rebuild gate：若 T4 走 branch A 则重编固件并校验产物新于源码；若 branch B 则校验 SoC 仿真产物存在，固件用 Phase 8 baseline；
       b. 跑 Python pytest 回归，结果写入 `build/evidence/ph9-pytest.log`；
       c. 跑 FM-SOC 全量回归（branch B 时：`p9_regression.sh` 必须设置 `SOC_SIMV=build/p9_simv_soc_top` 并将此设定 log 入 `build/evidence/ph9-fm-soc-33.log`；同时必须先删除 `build/ibex_full_rtl/simv_soc_ibex` 确保从更新后的 RTL 重编译），结果写入 `build/evidence/ph9-fm-soc-33.log`；
       d. 跑 MXU 9 场景回归，结果写入 `build/evidence/ph9-mxu-reg.log`；
       e. 跑 SFU + Vector 批量回归，结果写入 `build/evidence/ph9-sfu-vector.log`。
    2. 所有 PASS 才进 Wave 3。
  Must NOT do:
    - 不跳过任一 module 回归
    - 不接受部分 PASS 冒充整体
    - 不跳过 rebuild gate（避免测旧 binary）
    - 不在 todo 内 inline 命令字面量
  Parallelization: Wave 2 | Blocked by: 4 | Blocks: 6 | Can parallelize with: none
  References:
    - `firmware/build/npu_firmware.elf` 或 `build/p9_simv_soc_top`（T4 重编产物）
    - `sim/regression/run_fm_soc_all.sh`, `scripts/run_batch_regression.py`, `scripts/run_task17_regression.py`
    - `rtl/mxu/README.md`, `rtl/sfu/README.md`, `rtl/vector/README.md`
  Acceptance criteria (agent-executable):
    - branch A: `test firmware/build/npu_firmware.elf -nt firmware/npu_firmware.c`
    - branch B: `test -s build/p9_simv_soc_top && test -s build/evidence/ph9-t4b-elapsed.txt`
    - branch B: `test -s build/ibex_full_rtl/simv_soc_ibex && head -5 build/evidence/ph9-fm-soc-33.log | grep -qiE 'VCS|compile|elaborate'`
    - `grep -oE '[0-9]+ passed' build/evidence/ph9-pytest.log | head -1 | awk '{v=int($1); exit (v<210)}'`（pytest ≥210 passed；awk 退出码 0 当且仅当数值≥210）
    - `grep -cE '^\[PASS\] FM-SOC-' build/evidence/ph9-fm-soc-33.log` = 33
    - `grep -cE '^\[FAIL\] FM-SOC-' build/evidence/ph9-fm-soc-33.log` = 0
    - `grep -qE 'PASS: 33' build/evidence/ph9-fm-soc-33.log && grep -qE 'FAIL: 0' build/evidence/ph9-fm-soc-33.log`
    - `grep -qE 'MXU.*9/9.*PASS|MXU.*all.*9.*PASS' build/evidence/ph9-mxu-reg.log`
    - `grep -qE 'SFU.*319|319/319' build/evidence/ph9-sfu-vector.log && grep -qE 'Vector.*63|63/63' build/evidence/ph9-sfu-vector.log`
  QA scenarios:
    - Happy: 全回归同 Phase 8 baseline (pytest 210+, FM-SOC 33/33, MXU 9/9, SFU 319/319, Vector 63/63)。
    - Failure: 任一退化 → revert T4，记 `build/evidence/ph9-regression-fail.txt`，回 T4。
    - Evidence: `build/evidence/ph9-pytest.log`, `build/evidence/ph9-fm-soc-33.log`, `build/evidence/ph9-mxu-reg.log`, `build/evidence/ph9-sfu-vector.log`
  Commit: Y | `test(phase9): full regression after doorbell-path fix passes`

### Wave 3 — Firmware per-K-tile weight streaming (serial)

- [x] 6. SRAM budget pre-check + firmware per-K-tile weight DMA segment chain
  What to do:
    1. `bash scripts/p9_sram_budget.sh`（内部计算 Q_proj K=2560,N=4096,INT4 的 peak SRAM：per-K-tile weight 2048B、activation tile 64×64=4096B、output tile 64×4=256B、ping-pong double-buffered weight 2×2048B；assert `2*2048 + 4096 + 256 + scale_tile(256) < 4*1024*1024`，写 `build/evidence/ph9-sram-budget.txt`；超 → 写 `build/evidence/ph9-sram-overflow.txt` 并 HALT）。
    2. `bash scripts/p9_weight_streaming.sh`（内部执行 substep b–e）：
       a. 编辑 `firmware/npu_firmware.c:425-451` 的 K-block for 循环（循环头 line 425 到闭括号 line 451）：per-K-tile DMA weight 到 ping-pong `wbuf[k_block%2]`，确认 `desc.weight_addr` 在 DRAM 是 flat contiguous weight blob（由 `pack_int4_tile_major` 产出）；**不**改 `sim/cocotb_bridge.py`（Oracle 6 决定）；
       b. 若实测 `pack_int4_tile_major` 输出 layout 与 firmware per-K-tile offset 公式 `(n_tile*num_blocks + k_block)*TILE_WEIGHT_BYTES` 不一致 → 在本脚本内修 `sim/perf_tests.py` 的 weight DRAM 写入 offset（允许改 `sim/perf_tests.py`，禁止改 bridge）；
        c. 重编固件校验 `firmware/build/npu_firmware.elf -nt firmware/npu_firmware.c`；
        d. 跑 K=512 partial Q_proj (PERF-11) 预期 cos_sim>=0.999；log tee `build/evidence/ph9-p2-k512.log`，并把 cos_sim 写 JSON-line 到 `build/evidence/ph9-t6-p2-k512.txt`（含 `source="rtl"` 与 timestamp/commit）；
        e. **Bug-tracking 条件**：若实测发现 SRAM/weight-staging bug，commit 前由脚本内部调用 `p9_log_bug.sh --id BUG-RTL-SOC-P9-00C --type fw --symptom "per-K-tile weight DMA produces wrong SRAM layout or stale weight buffer" --evidence build/evidence/ph9-p2-k512.log --verdict resolved`。
  Must NOT do:
    - 不改 `sim/cocotb_bridge.py`
    - 不改 RTL（此 Wave 只动固件 + 可选 `sim/perf_tests.py`；wrapper 改动只属于 T4-B）
    - 不跳过 SRAM budget pre-check
    - 不跳过 bug-logging step（若发现 bug）
    - 不在 todo 内 inline `python3 - <<` / `make` 字面量
  Parallelization: Wave 3 | Blocked by: 5 | Blocks: 7 | Can parallelize with: none
  References:
    - `firmware/npu_firmware.c:395-456` (run_mmul)
    - `firmware/npu_firmware.c:128-138` (DMA CH0/CH1)
    - `sim/perf_tests.py:72-144` (`PR.mmul`, pack 调用)
    - `sim/cocotb_bridge.py`（`pack_int4_tile_major` 只读 import）
    - `.omo/evidence/` Phase 8 weight layout note
    - `docs/bugs/bugs-soc-rtl.md`（bug logger 追加目标）
  Acceptance criteria (agent-executable):
    - `test -s build/evidence/ph9-sram-budget.txt && grep -qE 'PASS|< 4MB' build/evidence/ph9-sram-budget.txt`
    - `git diff --name-only -- sim/cocotb_bridge.py | wc -l` 等于 0（bridge 未改）
    - `test -f build/evidence/ph9-t6-no-new-rtl.txt` 且 `grep -q 'T6_NO_NEW_RTL=1' build/evidence/ph9-t6-no-new-rtl.txt`（脚本写 marker 文件含此 key/value，表明 T6 未引入新 RTL 改动；T4-B 既有 wrapper 改不计入）
    - `grep -qE 'cos_sim=(0\.999[0-9]|1\.0)' build/evidence/ph9-p2-k512.log`
    - `grep -qE '"cos_sim": (0\.999[0-9]|1\.0)' build/evidence/ph9-t6-p2-k512.txt`（K=512 流式结果 JSON 含 `"cos_sim"` 数值）
    - `test firmware/build/npu_firmware.elf -nt firmware/npu_firmware.c`
    - `test -f build/evidence/ph9-t6-perf-tests-layout.txt`（脚本在修改 `sim/perf_tests.py` weight offset 时写此 marker；若 layout 一致无需修改，则写 `NO_LAYOUT_CHANGE=1`；由 `p9_weight_streaming.sh` 统一输出，避免 AC 条件分支）
  QA scenarios:
    - Happy: SRAM budget PASS，K=512 cos_sim>=0.999，bridge 未改，firmware 重编成功。
    - Failure(SRAM overflow): 写 overflow 报告，HALT Wave 3，escalate 架构决策。
    - Failure(layout mismatch): `sim/perf_tests.py` weight offset 已修；若仍 cs<0.999 → 写 `build/evidence/ph9-streaming-insufficient.txt`，bug 标 `open`。
    - Evidence: `build/evidence/ph9-sram-budget.txt`, `build/evidence/ph9-t6-p2-k512.txt`, `build/evidence/ph9-p2-k512.log`
  Commit: Y | `fix(firmware): per-K-tile weight streaming for K=2560 Q_proj within 4MB SRAM (P9)`

- [x] 7. 36-layer checkpoint L0/L10/L20/L35 cos_sim gate (L35 阈值 0.997)
  What to do:
    1. `bash scripts/p9_36layer.sh 0 10 20 35`（脚本内部调已有 36-layer checkpoint 脚本，把硬编码输出复制为 `build/evidence/ph9-36layer-checkpoint.txt` 并加 Phase 9 头与 timestamp；log 写入 `build/evidence/ph9-36layer-checkpoint.log`）。
    2. 每层 cos_sim 阈值：L0/L10/L20 ≥ 0.999；L35 ≥ 0.997（按脚本内 L35 baseline 0.998278、tolerance ±0.001 而定）。
  Must NOT do:
    - 不跑全 36-layer RTL forward（本 todo 只 4 个 checkpoint；全量留给后续）
    - 不改 RTL
    - 不把 L35 阈值硬设为 0.999（与脚本阈值不一致会导致脚本 PASS 但 AC FAIL）
  Parallelization: Wave 3 | Blocked by: 6 | Blocks: 8 | Can parallelize with: none
  References:
    - `scripts/run_36layer_checkpoint.py`（实际 CLI 参见 `:197-202`：`--ibex-smoke / --layers <int...> / --model <str> / --no-amend`）
    - `scripts/run_36layer_checkpoint.py:104-107`（L35 阈值 0.997278）
    - `build/evidence/36layer-checkpoint.txt`（Phase 6/8 历史）
    - `sim/qwen25_forward.py`（golden forward pass）
  Acceptance criteria (agent-executable):
    - `test -s build/evidence/ph9-36layer-checkpoint.txt`
    - `grep -c 'cos_sim' build/evidence/ph9-36layer-checkpoint.txt` ≥ 4
    - `grep -cE 'layer=(0|10|20) simulator=ibex status=PASS' build/evidence/ph9-36layer-checkpoint.txt` = 3（L0/L10/L20 均 PASS）
    - `grep -qE 'layer=35 simulator=ibex status=PASS' build/evidence/ph9-36layer-checkpoint.txt`（L35 PASS）
    - `grep -E 'layer=(0|10|20) simulator=ibex status=PASS' build/evidence/ph9-36layer-checkpoint.txt | grep -cE 'cos_sim=(0\.999[0-9]|1\.0)'` = 3（L0/L10/L20 cos_sim>=0.999）
    - `grep -E 'layer=35 simulator=ibex status=PASS' build/evidence/ph9-36layer-checkpoint.txt | grep -E 'cos_sim=(0\.99[7-9]|1\.0)'`（L35 cos_sim>=0.997）
  QA scenarios:
    - Happy: 4 checkpoint 全 PASS。
    - Failure: 某 cs 跨阈值即电梯门铃外溢 → 记 NOT RESOLVED + 假设写 `build/evidence/ph9-36layer-partial.txt`，不阻塞 closure。
    - Evidence: `build/evidence/ph9-36layer-checkpoint.txt`, `build/evidence/ph9-36layer-checkpoint.log`
  Commit: Y | `test(phase9): 36-layer L0/L10/L20/L35 checkpoint cos_sim gate (L35>=0.997)`

### Wave 4 — Full re-run + documentation (parallel after Wave 3)

- [x] 8. Full PERF re-run + fullchain multi-tile + testcase-list sync + issues_found Phase 9 + closure
  What to do:
    1. `bash scripts/p9_perfect_batch.sh`（脚本内部执行全部 substep）：
       a. 新增 `test_w4_perf_fullchain_multitile` 到 `sim/perf_tests.py`；
       b. 在 sz0001 上重跑全 PERF 批次，结果写入 `build/evidence/ph9-perf-batch.log`，并写 `build/evidence/w4-perf-p{0,1,2,3,4}.txt` 与 `build/evidence/fullchain-pipeline.txt`；
       c. **Stale-state 防御**: 脚本在写每个 w4-perf 文件与 fullchain-pipeline 文件时，必须在首行写入 Phase 9 re-run 头、timestamp、commit 与 `source="rtl"` 字段（强制覆盖 Phase 8 写入）；不写则视为 stale；
       d. 校验 PERF-01/04/05/06 (P0+P1), PERF-11 (P2), PERF-13/17 (P3+P4) 全 cos_sim>=0.999；合成条目标 `source="analytical"`；
       e. 多 tile fullchain 跑同 testcase，log 写入 `build/evidence/ph9-fullchain-multitile.log`，结果 JSON 写入 `build/evidence/ph9-fullchain-multitile.txt`；
       f. 同步 `rtl/testcase-list-perf.md`：所有 FAIL→PASS；
       g. 在 `docs/issues_found.md` 追加 Phase 9 Resolution Status 与 Condition Disposition；
       h. 生成 `build/evidence/ph9-closure.txt` 列 FIXED / REST NOT RESOLVED / Phase 10 forward。
  Must NOT do:
    - 不接受 grep-only PASS — 必须有完整 cos_sim 数值
    - 不擅自把 NOT RESOLVED 改 RESOLVED — 必须有实测
    - 不跳过 stale-state 防御（每个 w4-perf-p*.txt 必须含 `# Phase 9 re-run` 首行）
    - 不在 todo 内 inline `ssh`/`make` 等字面量
  Parallelization: Wave 4 | Blocked by: 7 | Blocks: F1-F4 | Can parallelize with: 9
  References:
    - `sim/perf_tests.py:195-306`（既有 testcase 函数模式，新增 multitile 函数参照）
    - `build/evidence/w4-perf-p*.txt`（Phase 8 baseline，本 todo 必须覆盖）
    - `rtl/testcase-list-perf.md`
    - `docs/issues_found.md` Phase 8 章节（镜像表结构）
    - `build/run_ph8_perf11_standalone.sh`（standalone 模板，若有；若已是 stale reference 则复用 `sim/regression/run_w4_perf_batch.sh`）
  Acceptance criteria (agent-executable):
    - `grep -q '^async def test_w4_perf_fullchain_multitile' sim/perf_tests.py`（新函数已加）
    - `test -s build/evidence/ph9-perf-batch.log`
    - 对每个 p in 0 1 2 3 4：`head -1 build/evidence/w4-perf-p${p}.txt | grep -q '^# Phase 9 re-run'`（stale-state 守护）
    - `head -1 build/evidence/fullchain-pipeline.txt | grep -q '^# Phase 9 re-run'`
    - `grep -qE '"case_id": "PERF-0[14]".*"status": "PASS".*"cos_sim": (0\.999[0-9]|1\.0)' build/evidence/w4-perf-p0.txt`
    - `grep -qE '"case_id": "PERF-0[56]".*"status": "PASS".*"cos_sim": (0\.999[0-9]|1\.0)' build/evidence/w4-perf-p0.txt`
    - `grep -qE '"case_id": "PERF-11".*"status": "PASS".*"cos_sim": (0\.999[0-9]|1\.0)' build/evidence/w4-perf-p2.txt`
    - `grep -qE '"case_id": "PERF-13".*"status": "PASS".*"cos_sim": (0\.999[0-9]|1\.0)' build/evidence/w4-perf-p3.txt`
    - `grep -qE '"case_id": "PERF-17".*"status": "PASS".*"cos_sim": (0\.999[0-9]|1\.0)' build/evidence/w4-perf-p4.txt`
    - `grep -qE '"cos_sim": (0\.999[0-9]|1\.0)' build/evidence/ph9-fullchain-multitile.txt`
    - `grep -qE '"DMA_wr_bytes"|"DMA_rd_bytes"|"axi_[a-z_]*_bytes"|"nonzero_traffic": 1' build/evidence/ph9-fullchain-multitile.txt`（确认 fullchain 多 tile 产生 DMA/AXI 非零流量）
    - `grep -c '| ✅ PASS |' rtl/testcase-list-perf.md` ≥ 20（当前表格共 21 行；T8 必须把所有 FAIL 行改为 PASS；允许最多 1 行保留 SKIP/NOT RESOLVED 等例外状态）
    - `grep -q 'Phase 9 Resolution Status' docs/issues_found.md && grep -q 'Phase 9 Condition Disposition' docs/issues_found.md`
    - `grep -qE 'REST NOT RESOLVED|Phase 10 forward|NO REMAINING' build/evidence/ph9-closure.txt`
  QA scenarios:
    - Happy: 全 PERF PASS，fullchain 多 tile PASS，testcase 全 PASS，docs 更新，closure 生成；w4-perf-* 含 `# Phase 9 re-run` 头。
    - Failure: 任一 PERF 仍 cs<0.999 → `p9_perfect_batch.sh` 必须调 `bash scripts/p9_log_bug.sh --id BUG-RTL-SOC-P9-00D --type integ --symptom "PERF residual cs<0.999 after T4 fix" --evidence build/evidence/ph9-perf-residual.txt --verdict open`，再写 `build/evidence/ph9-perf-residual.txt`，标 NOT RESOLVED in `docs/issues_found.md`，不阻塞 closure 但记 Phase 10 forward。
    - Evidence: `build/evidence/ph9-perf-batch.log`, `build/evidence/w4-perf-p*.txt`, `ph9-fullchain-multitile.txt`, `ph9-closure.txt`, `docs/bugs/bugs-soc-rtl.md` BUG-P9-00D 条目
  Commit: Y | `test(phase9): full PERF re-run + fullchain multi-tile + docs/closure (P9)`

### Wave 5 — Q8_0 + Phase 6 6b (parallel with Wave 4)

- [x] 9. Q8_0 download (retry/fallback) + Q_proj precision + Phase 6 6b finalize
  What to do:
    1. `bash scripts/p9_q8o_download.sh`（下载 Q8_0 GGUF 到 `~/models/qwen2.5-3b-instruct-q8_0.gguf`，最多重试 3 次；任一次在 600s 内成功即继续；若 3 次均失败或超时，写 `build/evidence/ph9-q8_0-download-FAILED.txt` 并 commit 后退出 0）。
    2. **仅当下载成功**: `bash scripts/p9_q8o_precision.sh`（脚本内部调已有无 CLI 的 precision 脚本，把硬编码输出复制为 `build/evidence/ph9-q8_0-precision.txt`，并写 log）。
    3. `bash scripts/p9_phase6_6b_finalize.sh`（脚本根据 precision 结果或 FAILED 状态，按阈值改 `.omo/plans/phase6-rtl-verification.md:107` 的 6b checkbox 并在对应行旁追加 `ba/judge=<verdict>` 字样——该前缀表示 judge 字段绑定在 6b checkbox 同一行，再同步 `docs/issues_found.md` 的 6b 行）。阈值规则：
       - 网络失败 → 写 `BLOCKED-NETWORK` 字样并把 6b 状态从 `[x]` 改为 `[~] CONDITIONAL`，evidence 写 `ph9-q8_0-download-FAILED.txt` 路径；
       - cs>=0.999 → 保持 `[x]` PASS；
       - 0.990 ≤ cs < 0.999 → `[~]` (CONDITIONAL)，precision 文件含 per-layer delta；
       - cs<0.990 → `[ ]` (FAIL)，写 root-cause 假设。
  Must NOT do:
    - 不用 Q8_0 做任何非 6b 用途
    - 不在下载失败时阻塞主线工作
    - 不擅自定 6b 状态 — 严格按阈值
    - 不传不支持的 `--model` / `--out` 给 `run_w1_6b_q8o_control.py`
    - 不在 todo 内 inline `huggingface-cli` / `python3` 等字面量
  Parallelization: Wave 5 | Blocked by: 1（下载独立）| Blocks: F1-F4（短路规则见 Wave 5）| Can parallelize with: 8（与 Wave 4 并行）
  References:
    - `.omo/plans/phase6-rtl-verification.md:107-113`（6b checkbox + evidence）
    - `build/evidence/w1-6b-q8o.txt`（脚本硬编码输出，由 `run_w1_6b_q8o_control.py:27`）
    - `scripts/run_w1_6b_q8o_control.py:26-27, 290-301`（实际入口：无 CLI args，硬编码模型路径）
    - `sim/e2e_llamacpp.py`（Func Model Q_proj 内部用）
    - `~/models/qwen2.5-3b-instruct-q8_0.gguf`（target）
  Acceptance criteria (agent-executable):
    - 下载成功路径: `test -s build/evidence/ph9-q8_0-precision.txt`
    - 下载失败路径: `test -s build/evidence/ph9-q8_0-download-FAILED.txt && grep -qE 'BLOCKED-NETWORK|exit_code|huggingface-cli' build/evidence/ph9-q8_0-download-FAILED.txt`
    - precision 存在时: `grep -cE 'cos_sim=[0-9]\.[0-9]+' build/evidence/ph9-q8_0-precision.txt` ≥ 36
    - 阈值规则已应用: `grep -qE '^6b\. \[(x|~| )\].*ba/judge=(PASS|CONDITIONAL|FAIL|BLOCKED-NETWORK)' .omo/plans/phase6-rtl-verification.md`（judge 字样必须追加在 6b 复选框同一行）
    - `grep -q 'ph9-q8_0\|BLOCKED-NETWORK' docs/issues_found.md`（6b 行已同步）
  QA scenarios:
    - Happy: Q8_0 下载成功，Q_proj cos_sim>=0.999，6b 保持 PASS，precision 文件 36 cos_sim，judge=PASS。
    - Failure(network): 下载失败 3 次 → 写 FAILED，6b 标 BLOCKED-NETWORK（judge=BLOCKED-NETWORK），不阻塞主线，T9 commit。
    - Failure(precision low): 按阈值标 CONDITIONAL 或 FAIL，judge=CONDITIONAL/FAIL。
    - Evidence: `build/evidence/ph9-q8_0-precision.txt` 或 `build/evidence/ph9-q8_0-download-FAILED.txt`, `.omo/plans/phase6-rtl-verification.md:107` diff, `docs/issues_found.md` 6b 行
  Commit: Y | `test(phase9): Q8_0 Q_proj control experiment + Phase 6 6b finalized`

## Final verification wave
> Runs in parallel after ALL todos (T1-T9 全 `[x]` 或 T9 BLOCKED-NETWORK)。ALL必须 APPROVE。Surface results and wait for the user's explicit okay before declaring complete。

- [x] F1. Plan compliance audit: all todo checkboxes `[x]`; evidence files match plan acceptance criteria
  What to do:
    1. `bash scripts/p9_f1_audit.sh`（T1 bootstrap 创建的脚本）：内部检查所有 todo checkbox 为 `[x]`，读取每条 acceptance criterion 并执行 grep/test，输出 `build/evidence/f1-audit.log`，格式 `F1-AUDIT-PASS` 或 `FAIL:<line>:<AC>`；T9 BLOCKED-NETWORK 时只核 T9 的 BLOCKED-NETWORK 路径 AC，跳过 precision 路径。
  Must NOT do:
    - 不接受 grep-only PASS — 必须有完整 cos_sim 数值
    - 不擅自把 NOT RESOLVED 改 RESOLVED — 必须有实测
    - F1 What-to-do 不出现 inline `for...grep` 循环（已封装进脚本）
  Acceptance criteria (agent-executable):
    - `test -s build/evidence/f1-audit.log`
    - `grep -qE '^CHECKBOX_OK' build/evidence/f1-audit.log`
    - `! grep -qE '^FAIL:' build/evidence/f1-audit.log`
  QA scenarios:
    - Happy: 全 todo [x] 勾选，所有 AC grep PASS。
    - Failure: 任一 AC FAIL → `p9_f1_audit.sh` 写 `build/evidence/f1-fail-summary.txt` 列具体 todo+line+AC，反馈给 todo 复检。
    - Evidence: `build/evidence/f1-audit.log`, `build/evidence/f1-fail-summary.txt` (fallback)
  Commit: N（审计）

- [x] F2. Code quality review: 只允许以下文件改动：`firmware/npu_firmware.c`、`rtl/wrapper/mxu_soc_wrapper.v`、`sim/perf_tests.py`、`sim/diagnose_mmu_path.py`、`scripts/p9_*.sh`、`scripts/p9_lib/*.sh`、`docs/bugs/*.md`、`docs/issues_found.md`、`rtl/testcase-list-perf.md`、`.omo/plans/phase6-rtl-verification.md`、`.omo/notepads/phase9-firmware-rtl-fix/*.md`、`build/evidence/ph9-*`、`build/evidence/w4-perf-p*.txt`、`build/evidence/fullchain-pipeline.txt`、`build/evidence/f{1,2,3,4}-*`、`build/evidence/36layer-checkpoint.txt`；不动 `sim/cocotb_bridge.py`
  What to do:
    1. `bash scripts/p9_f2_code_quality.sh`（T1 bootstrap 创建）：脚本以 `build/evidence/ph9-base-commit.txt` 为 baseline，检查新增/修改文件落在上述白名单内、bridge 未改动、相关 Python 文件 AST OK；输出 `build/evidence/f2-file-diff.txt` 与 `build/evidence/f2-ast.txt`。
  Must NOT do:
    - 不在 F2 内出现 inline 命令字面量（已封装进脚本）
    - 不放宽白名单
  Acceptance criteria (agent-executable):
    - `test -s build/evidence/f2-file-diff.txt`
    - `grep -q '^BRIDGE_UNCHANGED=1' build/evidence/f2-file-diff.txt`
    - `grep -q '^SCOPE_CREEP=0' build/evidence/f2-file-diff.txt`
    - `grep -q '^AST_OK=1' build/evidence/f2-ast.txt`
  QA scenarios:
    - Happy: 改动只在白名单文件内，bridge 未动，AST OK。
    - Failure: 任意超出白名单或 AST 失败 → `p9_f2_code_quality.sh` 写 `build/evidence/f2-scope-creep.txt` 列清单。
    - Evidence: `build/evidence/f2-file-diff.txt`, `build/evidence/f2-ast.txt`
  Commit: N（审计）

- [x] F3. Real manual QA: causality gate、root-cause verdict matrix、fullchain 多 tile hex 非零
  What to do:
    1. `bash scripts/p9_f3_manual_qa.sh`（T1 bootstrap 创建）：脚本检查 `build/evidence/ph9-causality.txt` 的 K<=64 与 K=512 两路结果及 K<=64 的 cos_sim 阈值；检查 BUG-MXU-P9-00B 独立报告（若存在）含 Root Cause Verdict 块；检查 fullchain 多 tile 结果文件 hex 非全零；输出 `build/evidence/f3-checklist.txt`。
  Must NOT do:
    - 不在 F3 内出现 inline 命令字面量（已封装进脚本）
    - 不弱化 K<=64 cos_sim>=0.999 的因果门标准
  Acceptance criteria (agent-executable):
    - `test -s build/evidence/f3-checklist.txt`
    - `grep -q '^CAUSALITY_OK=1' build/evidence/f3-checklist.txt`
    - `grep -q '^HEX_NONZERO=1' build/evidence/f3-checklist.txt`
    - `grep -qE '^BUG_VERDICT_OK=(1|N/A)' build/evidence/f3-checklist.txt`（N/A 表示未触发 RTL bug report）
  QA scenarios:
    - Happy: causality、bug report verdict、hex 全通过。
    - Failure: 任一缺失 → `p9_f3_manual_qa.sh` 写 `build/evidence/f3-fail.txt` 列具体失败项。
    - Evidence: `build/evidence/f3-checklist.txt`, `build/evidence/f3-fail.txt` (fallback)
  Commit: N（审计）

- [x] F4. Scope fidelity: 多 guardrail 终查
  What to do:
    1. `bash scripts/p9_f4_scope_gate.sh`（T1 bootstrap 创建）：内部以 `cat build/evidence/ph9-base-commit.txt` 为 baseline，检查 (a) RTL 只改 `rtl/wrapper/mxu_soc_wrapper.v`（其余 rtl/ 文件无 diff）；(b) `.omo/plans/phase6-rtl-verification.md` 含 `ba/judge=(PASS|CONDITIONAL|FAIL|BLOCKED-NETWORK)`；(c) commit 历史无 `spike_src/plugins/npu_mmio_plugin` 改动；(d) 若 T9 BLOCKED-NETWORK 则 judge 字段必为 `BLOCKED-NETWORK` 且不存在 `build/evidence/ph9-q8_0-precision.txt`。脚本输出 `build/evidence/f4-gate.txt` 含 `RTL_SCOPE_OK=1`、`Q8O_JUDGE_OK=1`、`SPIKE_PLUGIN_UNCHANGED=1`。
  Must NOT do:
    - 不在 F4 内 inline `git diff` / `git log` / `grep` 命令字面量（已封装）
    - 不放宽 scope 白名单
  Acceptance criteria (agent-executable):
    - `test -s build/evidence/f4-gate.txt`
    - `grep -q '^RTL_SCOPE_OK=1' build/evidence/f4-gate.txt`
    - `grep -q '^Q8O_JUDGE_OK=1' build/evidence/f4-gate.txt`
    - `grep -q '^SPIKE_PLUGIN_UNCHANGED=1' build/evidence/f4-gate.txt`
  QA scenarios:
    - Happy: 全 guardrail 通过，marker 文件三行 OK。
    - Failure: 任一 guardrail 破 → `p9_f4_scope_gate.sh` 写 `build/evidence/f4-violation.txt` 列清单 + 涉及 commit，HALT 等用户决策。
    - Evidence: `build/evidence/f4-gate.txt`, `build/evidence/f4-violation.txt` (fallback)
  Commit: N（审计）

## Commit strategy
- 每个 todo 完成立即 commit; Commit message 格式 `type(scope): summary`
- 类型: `diag` (T1,T2,T3), `fix` (T4, T6), `test` (T5, T7, T8), `chore` (T8 closure), `docs` (T9 Phase 6 6b)
- Fallback evidence 文件按 fail-first 触发: `ph9-abi-mismatch.txt`, `ph9-divergence-inconclusive.txt`, `ph9-diagnostic-contaminated.txt`, `ph9-sram-overflow.txt`, `ph9-q8_0-download-FAILED.txt`, `ph9-perf-residual.txt` 各自 commit
- F1-F4 不 commit (审计)

## Success criteria

| 指标 | 阈值 |
|:---|:---:|
| 共用脚本骨架 | `scripts/p9_lib/p9_sz0001.sh` + `scripts/p9_log_bug.sh --help` 含 `--rtl-report` |
| 全 todo 仅有 `bash scripts/p9_*.sh` 入口 | F1 grep 无 `ssh zhengs@` / inline `make` / `python3 - <<` 字面量违反 SCRIPT-FIRST |
| M=1 multi-tile 发散根因 | T3 报告含 `CONCLUSION: (A|B|C):` 引用 file:line |
| RTL bug report (T3=B/C 或 T4-B) | `docs/bugs/BUG-MXU-P9-001-doorbell-divergence.md` / `BUG-MXU-P9-00B-broadcast-multitile.md` 含六块根因分析 |
| Bug-tracking 落地 | 所有 fix commit 前 `scripts/p9_log_bug.sh` 已追加条目到 `docs/bugs/bugs-soc-rtl.md` |
| T4 fix 后 directed sweep（对应 PERF-13 场景） | cos_sim≥0.999 |
| Causality gate | `build/evidence/ph9-causality.txt` 同时含 `^K<=64:` 且 `^K=512:` 行 |
| 全 module 回归 | pytest 210+, FM-SOC 33/33, MXU 9/9, SFU 319/319, Vector 63/63 |
| 36-layer checkpoint | L0/L10/L20 cos_sim≥0.999；L35 cos_sim≥0.997（按脚本阈值） |
| Fullchain 多 tile | cos_sim≥0.999 + DMA/AXI non-zero traffic + 5 op chain (MMUL→SFU→Vector→DMA→Residual) 全部参与 |
| testcase-list-perf.md | `| ✅ PASS |` ≥20（与 T8 AC 一致；当前表格共 21 行，所有 FAIL 行必须改为 PASS（允许最多 1 行保留 SKIP/NOT RESOLVED 等例外状态）） |
| issues_found.md | `## Phase 9 Resolution Status` + `## Phase 9 Condition Disposition` 表 |
| Stale-state 防御 | 每个 `w4-perf-p*.txt` 头部含 `# Phase 9 re-run` 行 |
| T7 证据路径 | `build/evidence/ph9-36layer-checkpoint.txt` 存在（脚本 cp 而非依赖 `--out`） |
| T9 Q8_0 (若下载成功) | per-layer cos_sim ≥36 条；`.omo/plans/phase6-rtl-verification.md` 含 `ba/judge=PASS|CONDITIONAL|FAIL` |
| T9 Q8_0 (若下载失败) | `ph9-q8_0-download-FAILED.txt` 存在；6b 标 `BLOCKED-NETWORK`；T9 commit；不阻塞 F-wave |
| 固件 rebuild gate | `test .elf -nt .c` 通过 |
| Read-only 诊断 gate | Wave 1 后 `git diff -- rtl/ firmware/` 空 |
| F1-F4 完整结构 | 各 wave 含 What-to-do / Acceptance / QA / Commit-N |
