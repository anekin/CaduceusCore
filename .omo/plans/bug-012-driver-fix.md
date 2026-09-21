# bug-012-driver-fix - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** BUG-RTL-SOC-012 真正闭环：验证驱动不再把矩阵列数谎报成 64 的倍数，op05 回归从 62/64 失败翻成 64/64 通过，缺陷台账从"已定位未修复"翻成"已修复"，全量回归证明没有现有 workload 被波及。

**Why this approach:** 根因调查已实证 driver 侧 pad-to-64 是唯一触发源、且真实 N 即可让硬件正确 store-out（Phase B 探针 64/64）。所以走台账钦定的低风险 driver-side 路径：只改验证驱动与两处直写测试，不动任何产品 RTL。硬件写回对非 2 的幂列数的硬化（W1/W2）留作后续计划。

**What it will NOT do:** 不改固件（本来就写真实 N）、不改任何 rtl/ 产品代码、不动 gen//config//vendored、不动 7 个并行会话 dirty 文件、不自动 push；单命令非 2 幂列数（如 N=33）仍不受支持（wrapper 约束原样保留并写入台账 residual）。

**Effort:** Short-Medium
**Risk:** Low - 纯验证驱动侧改动（test-infra），产品 RTL 零触碰；最大风险是全量回归暴露未知 N 值依赖 padding，回归矩阵已全覆盖。
**Decisions to sanity-check:** (1) light 路径：只做 driver-side fix，wrapper W1/W2 硬化 defer 到 `.omo/plans/bug-012-fix.md` 剩余 todo；(2) `test_dim1_padding_audit_only_in_rtl_drivers` 契约从"pad 公式仅限 2 个 driver 文件"翻成"sim/ 全域为零"（不翻转则 fix 后 pytest 必挂——重型计划漏项，本计划补上）；(3) 台账 residual 显式记录 wrapper pow2 约束 + W1/W2 follow-up 指针。

Your next move: 计划已按你的指示（"先把 bug012 真正修掉"）生成，Metis 审查后立即派 worker 执行；F1-F4 全 APPROVE 后等你点头再 merge 回 main。

---

> TL;DR (machine): Short-Medium | Low | BUG-012 driver-side RED→GREEN — DIM1=真实 N（cocotb_bridge + diagnose + test 直写 + padding-audit 契约翻转）；回归 = attn_score GREEN + layout + dense varN + 33 FM-SOC + blk0 + op05/07 + W4-PERF + P9 + 全量 pytest；台账 12 Fixed/3 Open + residual pow2 约束；不 push。

## Scope
### Must have
0. **P0 基线**：分支 `bug-012-driver-fix` + provenance + 7-dirty-file 终拍断言
1. **D1 driver DIM1=真实 N**：`sim/cocotb_bridge.py:2100-2107` pad 删除 + `test_e2e_attn_score` 直写（:3958）/docstring（:3916-3922）改真实 N + `sim/diagnose_data_layout.py:151` 同改 + stale 注释清理（:2100-2103、:2426 "PCIe TLP"、:4005-4006 "current driver"措辞）+ **`sim/tests/test_fm_abi_contract.py:117-137` padding-audit 契约翻转**（expected 两文件 → hits==[]）+ py_compile + sz0001 scoped pytest
2. **T5 RED→GREEN**：`run_e2e_attn_score` 64/64 PASS（sz0001 VCS）
3. **全量回归**（sz0001 串行）：layout + dense_varN + 33 FM-SOC（`run_fm_soc_all.sh`）+ blk0 + op05/op07 + W4-PERF batch + P9 causality/sweep + 全量 FM pytest（fmpytest 完整命令）
4. **台账**：BUG-012 → Fixed（driver commit 引证 + residual：wrapper pow2 约束/W1/W2 defer 指针/WRP_DIM_N 几何失效）+ 统计 12 Fixed/3 Open + README 快照 + `docs/xverif-debug-case-bug012.html` 机制勘正（:343 WRP_DIM_N 措辞、:368 状态引证）
5. F1-F4 终审

### Must NOT have (guardrails, anti-slop, scope boundaries)
- **firmware/ 零改动**（`npu_firmware.c:273` 已写真实 N）；**rtl/ 零改动**（含 wrapper W1/W2——defer 到 `.omo/plans/bug-012-fix.md` todo 2/3 作后续）；gen/、config/、vendored 零改动
- `rtl/test_vectors/qwen_blk0/` **零净 diff**
- **7 个并行会话 dirty 文件全程不动不提交**：`.omo/evidence/task-0-signoff-v3-runner.txt`、`.omo/evidence/task-20-uncertainty-kpis.json`、`.omo/evidence/task-23-perf-spec-ci.txt`、`.omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md`、`.omo/notepads/phase6-rtl-verification/learnings.md`、`build/evidence/fm-cv-chain.txt`、`build/evidence/w3-4-mobilenetv3-fm.txt`；**绝不 `git add .`/`-A`/`commit -a`**
- 不改 `_run_tiled_mmul`/`_run_streamed_mmul`/`_mxu_preload`/`_configure_engine_regs` 其他分支（`sim/cocotb_bridge.py` :192/:215-216/:1726/:1839-1841/:1951/:4438/:4711/:4873 等均为合法 k/n/m tiles ceil 除法，不误报）
- VCS 仅 sz0001 经 `sim/regression/soc-verification-run.sh`；**sz0001 VCS 运行串行**（todo 2 → 3 顺序，不并发）
- 不 push（用户明示后才 push）
- no_silent_skip：任一回归 FAIL → 签名落档 STOP 上报，不得改弱测试/不得 DRY-RUN 计 PASS

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after**（先改实现，后以 RED anchor 翻绿 + 全量回归验收；`test_e2e_attn_score` 本身就是 RED anchor）——框架：cocotb（sz0001 VCS）+ pytest（sz0001 fmpytest venv + readline shim + AGENTS.md ignore 清单）+ grep 审计
- Evidence: `.omo/evidence/task-{0..4}-bug-012-driver-fix.txt`（随对应 todo commit 入库）；每份 sz0001 evidence 必含 **provenance 块**（git HEAD / simv 标识 + VCS 版本 / make target 全命令）+ **grep-able 判定行**：`D-DIM1-WRITERS:` / `T5-ATTN-SCORE:` / `REG-LAYOUT:` / `REG-DENSE-VARN:` / `REG-FMSOC:` / `REG-E2E:` / `REG-W4:` / `REG-P9:` / `PYTEST:` / `LEDGER-STATUS:` / `STATS:`

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means under-split.

- **Wave 1**：todo 0（P0 基线，阻塞全部——单 todo 波沿用 bug-012-fix 计划的显式豁免：P0 必须先行且阻塞一切）
- **Wave 2**：todo 1（本地编辑 + sz0001 scoped pytest sanity）
- **Wave 3**（sz0001 VCS 串行）：todo 2（T5 attn_score GREEN 证据）→ todo 3（全量回归）
- **Wave 4**：todo 4（台账 + README + HTML，需 2/3 证据）
- **终审波**：F1-F4 并行评审

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 0 | — | 1,2,3,4 | — |
| 1 | 0 | 2,3,4 | — |
| 2 | 1 | 4 | —（sz0001 与 3 串行） |
| 3 | 1 | 4 | —（sz0001 与 2 串行） |
| 4 | 2,3 | F1-F4 | — |
| F1-F4 | 4 | merge gate | 彼此并行 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [ ] 0. P0 基线：分支 + provenance + pathspec 提交 + 7 行终拍断言
  What to do / Must NOT do: (1) `git checkout -b bug-012-driver-fix main`（当前目录，**禁止新建 worktree**；确认 `git branch --show-current` == bug-012-driver-fix）。(2) **provenance 块**：git HEAD sha + branch；`sha256sum firmware/build/npu_firmware.hex`（只记录现有 hex、**不重建固件**）；`find rtl/test_vectors/qwen_blk0 -type f | sort | xargs sha256sum` 目录哈希快照（本计划零改动 qwen_blk0，快照为 F4 零净 diff 口径）。(3) 全部落档 `.omo/evidence/task-0-bug-012-driver-fix.txt`。(4) **pathspec 提交** plan（`.omo/plans/bug-012-driver-fix.md`）+ draft（`.omo/drafts/bug-012-driver-fix.md`，gitignored 需 `git add -f`）+ 本 evidence。(5) **全部提交完成后拍 `git status --porcelain` 终拍快照（最后一步）**：输出必须**恰好包含**以下 7 行 M 状态 dirty 文件（任何其他新增 M/A 行 → STOP 上报；`.omo/plans/` 与 `.omo/tmp/` 下既有 `??` untracked 行可原样存在、不新增其他）：` M .omo/evidence/task-0-signoff-v3-runner.txt`、` M .omo/evidence/task-20-uncertainty-kpis.json`、` M .omo/evidence/task-23-perf-spec-ci.txt`、` M .omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md`、` M .omo/notepads/phase6-rtl-verification/learnings.md`、` M build/evidence/fm-cv-chain.txt`、` M build/evidence/w3-4-mobilenetv3-fm.txt`。Must NOT：不动/不提交 7 个 dirty 文件；不用 `git add .`/`-A`/`commit -a`；不重建固件。
  Parallelization: Wave 1 | Blocked by: none | Blocks: 1,2,3,4
  References (executor has NO interview context - be exhaustive): 7 个 dirty 文件清单见 Scope Must-NOT；P0 先例 `.omo/evidence/task-0-bug-012-root-cause.txt`（provenance 块格式）；`git log --oneline main -3`（当前 main HEAD = 931d359 docs(bug012) share page 或其后）
  Acceptance criteria (agent-executable): `git branch --show-current` == `bug-012-driver-fix`；evidence 含 HEAD 行 + firmware hex sha256 行 + qwen_blk0 目录哈希 ≥3 行；`git status --porcelain` 的 M 行恰好 7 行且逐行 ⊆ Must-NOT 清单（提交后拍）。
  QA scenarios (name the exact tool + invocation): happy=provenance 齐全落档 + 7 行快照 PASS；failure=快照出现额外 M/A 行（并行会话新产物？）→ STOP 记录根因。Evidence `.omo/evidence/task-0-bug-012-driver-fix.txt`
  Commit: Y | chore(omo): P0 baseline — branch + provenance snapshot (bug-012-driver-fix)

- [ ] 1. D1 driver DIM1=真实 N 对齐 ABI + padding-audit 契约翻转（sim/，本地编辑 + sz0001 scoped pytest）
  What to do / Must NOT do: (1) `sim/cocotb_bridge.py:2100-2107`（`_configure_engine_regs` MMUL 分支）：删除 `engine_n = ((instr.dim_n + 63) // 64) * 64`（:2104）与 :2100-2103 stale 注释（"MXU engine controller requires DIM1 ... multiple of 64 ... WRP_DIM_N still uses actual N for correct store-out"），替换为 `engine_n = instr.dim_n` + 单行注释 `# DIM1 = actual N per spec/npu_abi.json DIM1 ("[15:0]=N columns").`（历史：f9a32204 出生时注释为真、8dd5dbe 将 wrapper 切到 wrp_n_derived 后 staled——新注释不引用旧神话；DIM1 写入行 :2107 与 readback :2113 保持引用 engine_n 不动）。(2) `test_e2e_attn_score` 直写路径 `:3958`：`await bridge._apb_write(base + 0x10, 64)` → `await bridge._apb_write(base + 0x10, N)`；docstring `:3916-3922`（"The engine is configured with dim_n=64 so it computes one full 64-wide output tile, while the wrapper is told WRP_DIM_N=2 so only the first two INT32 columns per row are stored back"）改为 `DIM1 is programmed with the actual N (=2); the wrapper's store-out width follows the latched DIM1, producing a dense M×N output.`。(3) `test_e2e_attn_score_layout` docstring `:4005-4006` Phase A 措辞 `reproduces the current driver programming model` → `reproduces the legacy (pre-fix) padded programming model`（仅措辞，**零断言改动**——Phase A 手写 DIM1=64 复现旧几何，不依赖 driver）。(4) 两处 stale docstring 清理：`_read_sram_output` `:2426` stale `PCIe TLP` → `host_read_sram (with DUT present the real path is _sram_backdoor_read VPI)`；**`_mxu_preload` `:2259-2262` stale 神话（Metis #2 折入）**——docstring 末句 "and WRP_DIM_N to the output N dimension so the store-out sequencer writes the correct number of bytes per row" 改为 `WRP_DIM_N is written for backward compatibility but is geometrically inactive when the latched DIM1 is non-zero — store-out row width follows the latched MXU DIM1 (see mxu_soc_wrapper.v:221 wrp_n_derived)`。(5) `sim/diagnose_data_layout.py:151`：`engine_n = ((N + 63) // 64) * 64  # padded N for the controller` → `engine_n = N  # DIM1 = actual N per ABI`（:154 写 DIM1 行不动）。(6) **`sim/tests/test_fm_abi_contract.py:117-137` `test_dim1_padding_audit_only_in_rtl_drivers` 契约翻转（关键——不翻转则 fix 后此 pytest 必挂，fm-audit 遗留契约以"pad 公式存在于 2 个 driver 文件"为预期）**：`expected = {os.path.join("sim", "cocotb_bridge.py"), os.path.join("sim", "diagnose_data_layout.py")}` 删除，断言改为 `assert hits == []`（pad-up 公式 sim/ 全域为零）；docstring → `Pad-up formula fully removed — DIM1 is always the real N (ABI-aligned).`；:113-115 节头注释 "(c) FM-domain zero-padding audit" → "(c) sim-wide zero-padding audit"；**模块头注释 `:12-14`（Metis #5 折入）**——"(c) Zero-padding audit: the engine-N pad-up formula ... exists ONLY in the RTL-driver files sim/cocotb_bridge.py and sim/diagnose_data_layout.py — the FM domain must have zero hits." → `"(c) sim-wide zero-padding audit: the engine-N pad-up formula has zero hits across all sim/ Python files — DIM1 is always the real N (ABI-aligned)."`。(7) **审计判定（Metis #1 BLOCKER 折入——裸 grep 会误伤）**：`sim/golden_executor.py:1630/1639/1643/1645` 的 `((length * 2 + 63) // 64) * 64` 是 scratch 地址对齐、非 DIM1 padding——**禁止使用裸 `\+ 63\) // 64\) \* 64` 形态的 grep**（必误报）。**权威判定 = scoped pytest 的 `test_dim1_padding_audit_only_in_rtl_drivers` 通过（翻转后断言 hits==[]）**；辅助 grep 用与 PAD_PATTERN（`sim/tests/test_fm_abi_contract.py:35-36`）同义的正则：`grep -rnP "\(\([^)]*(dim_n|\bN\b)\s*\+\s*63\)\s*//\s*64\)\s*\*\s*64" sim/` 零命中；`grep -n "multiple of 64" sim/cocotb_bridge.py` 零命中；`grep -n "engine_n" sim/cocotb_bridge.py` 仅剩 :2104 附近 `engine_n = instr.dim_n` 形态与 diagnose 一处；判定行 `D-DIM1-WRITERS: 0-padded`。(8) `PYTHONPATH=sim python -m py_compile sim/cocotb_bridge.py sim/diagnose_data_layout.py sim/tests/test_fm_abi_contract.py` 零错误。(9) **sz0001 scoped pytest sanity**：AGENTS.md COMMANDS 的 fmpytest 命令（`env PATH=/home/zhengs/venvs/fmpytest/bin:... PYTHONPATH=sim:gen:/tmp/fmpytest-shim ... python -m pytest sim/tests/test_fm_abi_contract.py -q`，readline shim 先建 `/tmp/fmpytest-shim/readline.py`）→ 全 PASS（含翻转后的 audit 测试 + N=2/33/64 参数化 dense 测试）。Must NOT：不改 `_run_tiled_mmul`/`_run_streamed_mmul`/`_mxu_preload`/`_configure_engine_regs` 其他分支（:192/:215-216/:1726/:1839-1841/:1951/:2032/:4438/:4711/:4873/:6755/:6767 均为合法 ceil 除法/beat 计算，不误报）；不动 RTL/firmware；不跑 VCS（todo 2/3）；不动 7 dirty。
  Parallelization: Wave 2 | Blocked by: 0 | Blocks: 2,3,4
  References: `sim/cocotb_bridge.py:2096-2116`（MMUL 分支现状）、`:3914-3997`（test_e2e_attn_score 全文）、`:4000-4017`（layout docstring）、`:2424-2438`（_read_sram_output docstring）、`:2255-2273`（_mxu_preload docstring——stale 神话清理点）；`sim/diagnose_data_layout.py:140-160`；`sim/tests/test_fm_abi_contract.py:1-40`（模块头 :12-14 + PAD_PATTERN :35-36）与 `:113-137`（audit 测试全文）；`sim/golden_executor.py:1630-1645`（scratch 对齐表达式——审计 grep 误伤反例）；`spec/npu_abi.json`（DIM1 定义 "[15:0]=N columns"）；`firmware/npu_firmware.c:273`（固件已写真实 N 的语义基准）；`.omo/evidence/task-2-bug-012-root-cause.txt:114-121`（注释出生证明）；AGENTS.md COMMANDS（fmpytest 完整环境命令）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m py_compile <三文件>` 零错误；**scoped pytest `test_dim1_padding_audit_only_in_rtl_drivers` 通过（hits==[]，权威判定）**；`grep -rnP "\(\([^)]*(dim_n|\bN\b)\s*\+\s*63\)\s*//\s*64\)\s*\*\s*64" sim/` 零输出（PAD_PATTERN 同义正则，禁裸 grep）；`grep -n "multiple of 64" sim/cocotb_bridge.py` 零输出；scoped pytest 全 PASS 落档（判定行 `D-DIM1-WRITERS: 0-padded`）；`git diff HEAD^ HEAD --name-only` ⊆ {sim/cocotb_bridge.py, sim/diagnose_data_layout.py, sim/tests/test_fm_abi_contract.py} + 本 todo evidence。
  QA scenarios: happy=py_compile 通过 + 三组 grep 审计零残留 + scoped pytest 绿 + diff 白名单；failure=py_compile 失败或 grep 命中 padding 残留或 scoped pytest 挂 → 修正后重跑（audit 测试若挂先核对是否翻转到位）。Evidence `.omo/evidence/task-1-bug-012-driver-fix.txt`
  Commit: Y | fix(sim): program MXU DIM1 with actual N — drop 64-multiple padding (ABI-aligned)

- [ ] 2. T5 RED→GREEN：run_e2e_attn_score 64/64 PASS（sz0001 VCS，串行首个）
  What to do / Must NOT do: `bash sim/regression/soc-verification-run.sh run_e2e_attn_score 2>&1 | tee <run log>`（Makefile:673；脚本默认 CLEAN=1 强制重建 simv——保证 todo 1 的 driver 改动编入）。判定（Metis #4 折入——**双门**）：日志同时含 make 门形态 `test_e2e_attn_score.*PASS`（`sim/regression/Makefile:683` 的 grep 口径，决定 target 成败）与测试自身 PASS 行 `[e2e_attn_score] PASS in`（cocotb_bridge.py:3994，证据行）**且** 日志**零条** `Total INT32 mismatches:` 行 **且** 零条 `First mismatch @ byte[` 行（INT32 分支原文 :2540-2543/:2556-2559；PASS 时该两行不打印——以"不存在"为判据，不 grep 0/64 字面串）；判定行 `T5-ATTN-SCORE: pass-64-64`。evidence 含 provenance 块（git HEAD / simv 标识 + VCS 版本 / make target 全命令）+ 判定行 + PASS 行原文摘录。Must NOT：不改产品代码；不并发其他 sz0001 任务；若 Makefile:683 的 PASS 门模式与 cocotb 实际输出不匹配导致 make 误报 FAIL 而测试真 PASS → 仅允许对齐 Makefile 门模式（sim/regression/Makefile 一处）并落档原因，禁止改测试断言；不得因 62/64 旧签名而"预期失败"绕过。
  Parallelization: Wave 3 | Blocked by: 1 | Blocks: 4 | sz0001 与 3 串行
  References: `sim/regression/Makefile:673-686`（run_e2e_attn_score target :673 + PASS 门 :683 `test_e2e_attn_score.*PASS`）；`sim/cocotb_bridge.py:3994`（测试 PASS 行）、`:2540-2559`（mismatch 打印行——PASS 判据的反面）；`sim/regression/soc-verification-run.sh`（auto-SSH + CLEAN 语义）；`.omo/evidence/task-14-blk0-investigation.txt:52-73`（旧 62/64 签名基准——fix 后必须消失）；`.omo/evidence/task-3-bug-012-root-cause.txt`（ISO-VERDICT 复现证据）
  Acceptance criteria (agent-executable): run 日志同时含 make 门行（`test_e2e_attn_score.*PASS` 匹配）与测试 PASS 行（`[e2e_attn_score] PASS in`）+ 零 `Total INT32 mismatches:` 行 + 零 `First mismatch @ byte[` 行；evidence 含 provenance 块 + 判定行 `T5-ATTN-SCORE: pass-64-64` + 双 PASS 行原文摘录。
  QA scenarios: happy=64/64 PASS 落档；failure=仍 FAIL/62-64 → 签名落档 STOP（回 todo 1 复核改动是否完整编入，no_silent_skip）。Evidence `.omo/evidence/task-2-bug-012-driver-fix.txt`
  Commit: Y | test(bug012): RED→GREEN — run_e2e_attn_score PASS 64/64 evidence

- [ ] 3. 全量回归（sz0001 VCS 串行，todo 2 之后顺序执行）：layout + dense varN + 33 FM-SOC + blk0 + op05/07 + W4-PERF + P9 + 全量 pytest
  What to do / Must NOT do: **串行顺序**：(1) `bash sim/regression/soc-verification-run.sh run_e2e_attn_score_layout` → 判定行 `REG-LAYOUT: pass`（Phase A 手写 DIM1=64 复现旧几何、Phase B DIM1=2 dense——均不依赖 driver，确认不受波及；门模式 `test_e2e_attn_score_layout.*PASS`）。(2) `bash sim/regression/soc-verification-run.sh run_e2e_mmul_dense_layout` → `REG-DENSE-VARN: pass`（N=2/32/64 手动直写 DIM1）。(3) `bash sim/regression/run_fm_soc_all.sh` → `REG-FMSOC: 33-pass`（或实际计数如实落档；**重点：任何非 2 幂 N 的 driver 路径 case 若因 pow2 约束失败 → STOP 上报**——这正是本回归要暴露的风险面）。(4) `bash sim/regression/soc-verification-run.sh run_e2e_blk0` → `REG-E2E: blk0-pass`（重点：op05 应 PASS，日志 grep `BLK0.*PASS\|all 17 ops`）。(5) `bash sim/regression/soc-verification-run.sh run_e2e_mxu_op05` → `REG-E2E: op05-pass`；`run_e2e_mxu_op07` → `REG-E2E: op07-pass`。(6) W4-PERF：`bash sim/regression/run_w4_perf_batch.sh` → `REG-W4: <PASS/FAIL 计数>`（脚本自判 `TESTS=1 PASS=1 FAIL=0` 或 `Evidence written to`，见 :62-88）；P9 定向（不在 batch 内，手动）：**env 块照抄 run_w4_perf_batch.sh:1-37**（LD_LIBRARY_PATH/PYTHONPATH/RUN_DIR/SIMV/BOOTROM_HEX，从 repo parent 启动），`export TESTCASE=test_w4_perf_p9_causality; (cd "$RUN_DIR" && "$SIMV" +COCOTB +BOOTROM_HEX="$BOOTROM_HEX" -l p9_causality.log > p9_causality.log 2>&1) || true`，随后 `grep -qE 'TESTS=1 PASS=1 FAIL=0' p9_causality.log`——grep 不命中 ⇒ FAIL（非静默，签名落档）；同法 `test_w4_perf_p9_directed_sweep`；判定行 `REG-P9: causality-pass` / `REG-P9: sweep-pass`。(7) **全量 FM pytest**：AGENTS.md COMMANDS 完整 fmpytest 命令（`env PATH=/home/zhengs/venvs/fmpytest/bin:/usr/bin:/bin PYTHONPATH=sim:gen:/tmp/fmpytest-shim /home/zhengs/venvs/fmpytest/bin/python -m pytest sim/tests/ sim/timing/tests/ -q --continue-on-collection-errors --ignore=<AGENTS.md 全部 16 项>`）→ 判定行 `PYTEST: <实际计数>-passed`（记录实际值；任何 FAIL → 判定是否本变更集引入：是 → STOP；否（并行会话既有）→ 落档签名注明与本 fix 无关并上报）。每项 evidence 含 provenance + PASS/FAIL 汇总。Must NOT：并发两个 sz0001 VCS 任务；改任何测试断言；跳过任何 case；旧证据冒充新跑。
  Parallelization: Wave 3 | Blocked by: 1（执行顺序在 todo 2 之后，sz0001 串行） | Blocks: 4
  References: `sim/regression/Makefile:417-428`（blk0 target + PASS 门）、`:687-699`（attn_score_layout 门 `test_e2e_attn_score_layout.*PASS`）、`:701-713`（dense varN target + 门 `test_e2e_mmul_dense_layout_varN.*PASS`）、`:715-728`（op05 target + 门）、`:729-741`（**op07 target + PASS 门**——Metis #3 折入，原引用范围 :701-728 截断了 op07 门）；`sim/regression/run_fm_soc_all.sh`（33-case 全量入口）；`sim/regression/run_w4_perf_batch.sh:44-88`（batch 清单 + 判定逻辑）；`sim/perf_tests.py:413-446`（P9 两 testcase 断言与证据路径）；AGENTS.md COMMANDS + NOTES（fmpytest 完整命令、readline shim、16 项 ignore 清单、sz0001-only 政策）
  Acceptance criteria (agent-executable): evidence 含 7 组判定行全部 `*-pass`（或如实 FAIL + 签名 STOP）：`REG-LAYOUT:` / `REG-DENSE-VARN:` / `REG-FMSOC:` / `REG-E2E:`（blk0+op05+op07 三行）/ `REG-W4:` / `REG-P9:` 两行 / `PYTEST:`；blk0 日志含 `all 17 ops` 类 PASS 行；无测试被跳过/改弱。
  QA scenarios: happy=7 组全绿落档；failure=任一 FAIL → 日志签名落档 STOP（回 todo 1 归因：重点核对是否有非 2 幂 N 的 driver 路径 case 触发 wrapper pow2 约束——若属实则本 light 路径不足以闭环，升级上报用户决策是否转重型 W1/W2）。Evidence `.omo/evidence/task-3-bug-012-driver-fix.txt`
  Commit: Y | test(bug012): FM-SOC 33 + e2e + W4-PERF regression evidence (driver DIM1 fix)

- [ ] 4. 台账 BUG-012 → Fixed + 统计 12/3 + README + HTML 机制勘正（本地，待 2/3 证据）
  What to do / Must NOT do: (1) `docs/bugs/bugs-soc-rtl.md` BUG-012 条目（:826-964）：**Status → Fixed**（保留 ATTRIBUTION/根因段原文）；Fix 段改写：driver-side commit 引证（todo 1 的 commit sha + message `fix(sim): program MXU DIM1 with actual N...`）+ 机制一句话（driver DIM1=真实 N 对齐 ABI；wrapper store-out 跟随锁存 DIM1，与固件路径语义一致）+ **Residual constraints 段**：(a) wrapper store-out FSM 仍要求 N≤64 时 N*4 为 2 的幂——非 2 幂 N（如 33）单命令不支持，硬化（W1/W2：N 来源硬化 + beat/WSTRB 几何泛化）defer 至 `.omo/plans/bug-012-fix.md` todo 2/3 作后续计划；(b) 单命令 N>64 仍 producer-must-tile（cap 256 保留）；(c) WRP_DIM_N 寄存器仍被 `_mxu_preload` 写入但几何上失效（`wrp_n_derived` 中 dim1_n 优先，`mxu_soc_wrapper.v:221`）。Verification 段补 task-2/3 证据路径 + `T5-ATTN-SCORE: pass-64-64` 原文。(2) By-Status 表（:411 Fixed 行、:415 Open 行）：Fixed 11→12（+BUG-RTL-SOC-012）、Open 4→3；**:404 是 By-Severity Major 行——BUG-012 仍为 Major、计数 12 不变，勿改**（重型计划 Oracle 勘正）。(3) Quality Metrics（:435-444）：`Bugs closed Fixed | 11 (64.7%)` → `12 (70.6%)`；`Open / under investigation | 4 (23.5%)` → `3 (17.6%)`（百分比随计数重算）。(4) `README.md` 状态快照行（Bug 台账 SoC RTL 行）：`17 = 11 Fixed / 1 Pending（waiver 待签）/ 1 Accepted（reconstruction-failure）/ 4 Open` → `17 = 12 Fixed / 1 Pending（waiver 待签）/ 1 Accepted（reconstruction-failure）/ 3 Open`。(5) `docs/xverif-debug-case-bug012.html` 两处勘正：:343 `wrapper 的 WRP_DIM_N 继续负责 store-out 的真实宽度控制` → `wrapper 的 store-out 宽度跟随锁存的 DIM1（= 真实 N），WRP_DIM_N 不参与几何`（与根因机制一致）；:368 修复状态 callout 补 fix commit 引证 + `.omo/evidence/task-2-bug-012-driver-fix.txt` 路径。(6) 判定行 `LEDGER-STATUS: Fixed` / `STATS: 12-fixed-3-open`。Must NOT：无 todo 2/3 证据不得 claim Fixed；改 BUG-002/007/009/010/011/WDT-001 任何字段；改 docs/ 其他文件；改 config/gen。
  Parallelization: Wave 4 | Blocked by: 2,3 | Blocks: F1-F4
  References: `docs/bugs/bugs-soc-rtl.md:826-964`（BUG-012 现条目全文）、`:400-444`（三张统计表——:401-405 By-Severity、:407-416 By-Status、:433-444 Quality Metrics）；`README.md` 状态快照表（Bug 台账 SoC RTL 行）；`docs/xverif-debug-case-bug012.html:338-369`（第 9 节 + 修复状态 callout）；todo 1/2/3 的 Commit: 行与 evidence 路径
  Acceptance criteria (agent-executable): `grep -n "LEDGER-STATUS: Fixed" .omo/evidence/task-4-bug-012-driver-fix.txt` ≥ 1；台账 BUG-012 段含 Status Fixed + Fix commit 引证 + Residual constraints 三条；`grep -n "12 Fixed" docs/bugs/bugs-soc-rtl.md` ≥ 1 且 `grep -n "Bugs closed Fixed | 12" docs/bugs/bugs-soc-rtl.md` ≥ 1 且 `grep -n "Open / under investigation | 3" docs/bugs/bugs-soc-rtl.md` ≥ 1；README 快照行含 `12 Fixed` 与 `3 Open`；HTML :343 不再含 `WRP_DIM_N 继续负责`；`git diff main..HEAD --name-only -- docs/ README.md` ⊆ {docs/bugs/bugs-soc-rtl.md, README.md, docs/xverif-debug-case-bug012.html}。
  QA scenarios: happy=判定行 + 台账五要素（Status/Fix 引证/Residual/Verification/统计）+ 计数一致 + HTML 勘正落档；failure=计数遗漏（grep 旧值仍命中）→ 补齐后复跑 grep。Evidence `.omo/evidence/task-4-bug-012-driver-fix.txt`
  Commit: Y | docs(bugs): BUG-RTL-SOC-012 Fixed — driver DIM1 ABI alignment (driver-side)

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit — todos 0-4 逐条：evidence 文件存在（task-{0..4}-bug-012-driver-fix.txt）、acceptance 断言复跑（`git log --oneline main..HEAD` 按预声明 message 定位各 todo commit，HEAD 相关断言用该 sha 定向执行）、判定行（D-DIM1-WRITERS/T5-ATTN-SCORE/REG-LAYOUT/REG-DENSE-VARN/REG-FMSOC/REG-E2E/REG-W4/REG-P9/PYTEST/LEDGER-STATUS/STATS）齐全对照 evidence、无 silent skip（每个条件分支有判定行或 citation）。
- [ ] F2. Code quality review — driver diff 恰好限定 `_configure_engine_regs` MMUL 分支（:2100-2107）+ `test_e2e_attn_score` 直写/docstring + layout docstring 措辞 + `_read_sram_output` docstring + `diagnose_data_layout.py` 一行 + audit 测试翻转（`_run_tiled_mmul`/`_run_streamed_mmul`/`_mxu_preload` 零改动——diff 逐 hunk 核对）；audit 测试翻转语义正确（hits==[]，walk 逻辑保留）；台账不 overclaim（Fixed 措辞有 commit + 证据链 + residual 三条约束）；HTML 机制行与根因一致（store-out 跟随 latched DIM1，非 WRP_DIM_N）。
- [ ] F3. Real manual QA — fresh 独立复跑关键命令并对照 evidence：`bash sim/regression/soc-verification-run.sh run_e2e_attn_score`（PASS 行存在 + 零 mismatch 行）+ scoped pytest 复跑（`sim/tests/test_fm_abi_contract.py` 全 PASS）；输出逐项与 task-1/2 evidence 对照。**前置条件：sz0001 可达——不可达 ⇒ F3 判 BLOCK 并上报用户，不得降级 APPROVE。**
- [ ] F4. Scope fidelity — `git diff $(git merge-base main HEAD) HEAD --name-only` 变更集 ⊆ {`sim/cocotb_bridge.py`, `sim/diagnose_data_layout.py`, `sim/tests/test_fm_abi_contract.py`, `docs/bugs/bugs-soc-rtl.md`, `README.md`, `docs/xverif-debug-case-bug012.html`, `.omo/*`}；**`rtl/test_vectors/qwen_blk0/` 零净 diff**（对照 todo 0 哈希快照）；firmware/、rtl/、gen/、config/、vendored 零命中；每个提交 `git show --name-only` 均不含 7 个 dirty 文件；untracked/dirty 用 todo 0 快照口径复核（非 diff）；未 push；分支纪律（当前目录单一 worktree、分支 bug-012-driver-fix）。

## Commit strategy
- 一个 todo 一个原子 commit（type: fix/test/docs/chore），message 预声明于各 todo `Commit:` 行；evidence 随对应 todo 一并入库（`.omo/evidence/task-N-bug-012-driver-fix.txt`）
- staging 纪律：只允许逐 todo 显式路径 `git add` / `git add -f`（.omo 下 gitignored 的 plan/draft/evidence 用 add -f）；**禁止 `git add .`、`git add -A`、`git commit -a`**
- sz0001 VCS 串行（todo 2 → 3），防止共享 simv 重建竞争
- F1-F4 全 APPROVE + 用户 explicit okay 后 `--no-ff` merge 回 main；**不自动 push**

## Success criteria
1. `D-DIM1-WRITERS: 0-padded` —— sim/ 内零 pad-up DIM1 writer；py_compile 零错误；旧神话注释（"multiple of 64"）清除；padding-audit 契约翻转后 scoped pytest 绿
2. `T5-ATTN-SCORE: pass-64-64` —— 原 RED anchor 翻绿，`First mismatch @ byte[8]` 与 `Total INT32 mismatches: 62/64` 签名消失
3. 回归全 PASS：`REG-LAYOUT` / `REG-DENSE-VARN` / `REG-FMSOC: 33-pass` / `REG-E2E: blk0+op05+op07` / `REG-W4` / `REG-P9: causality+sweep` / `PYTEST: <实际计数>-passed`（无 fix 引入的失败）
4. 台账 BUG-012 **Fixed**（driver commit 引证 + Residual constraints 三条：wrapper pow2 约束 + W1/W2 defer 指针、N>64 producer-must-tile、WRP_DIM_N 几何失效）+ 统计 12 Fixed / 3 Open + README 快照同步 + HTML 机制勘正；`LEDGER-STATUS: Fixed` / `STATS: 12-fixed-3-open` 落档
5. F1-F4 全 APPROVE + 用户 okay → `--no-ff` merge 回 main；变更集 ⊆ 白名单且 qwen_blk0 零净 diff；每提交无 7 个 dirty 文件；未 push
