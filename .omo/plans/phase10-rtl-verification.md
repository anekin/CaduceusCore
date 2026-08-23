# phase10-rtl-verification - Work Plan

## TL;DR (For humans)
<!-- Fill this LAST, after the detailed plan below is written, so it summarizes the REAL plan. -->
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** A working DMA readback path for the RTL performance harness, a clean M=32 matrix-multiplication result, a 36-layer model forward pass verified against the golden reference — fully on the fast simulator, and on a 9-layer segment run with 5 checkpoint comparisons on the real SoC (the full 36-layer real-SoC run is deferred to the next FPGA phase) — and an updated performance-model calibration for weight-streaming overlap.

**Why this approach:** We fix the prerequisites first — the DMA readback bug and a hidden Ibex smoke failure — before running the long 36-layer simulations, and we diagnose before editing for the M=32 residual to avoid a wrong fix. All work stays on the EDA server with script-based evidence, continuing the discipline from the previous phase.

**What it will NOT do:**
- No new hardware features or changes to the architecture model.
- No firmware changes unless a read-only probe proves the root cause is there.
- No endless retries if the network blocks the optional Q8_0 download.

**Effort:** Large
**Risk:** Medium — the real-SoC 36-layer full run is deferred to the FPGA phase (this phase verifies a 9-layer segment run with 5 checkpoints on the real SoC instead, which keeps the long-run integration risks on the FPGA-phase critical path); the M=32/SFU-wrapper fixes may still touch RTL or firmware.
**Decisions to sanity-check:**
- We will modify the previously read-only bridge file to fix DMA readback.
- We will fix the three SFU wrapper mismatches, not just document them.
- We will keep DRAM accesses inside the 8 MB window rather than expanding the memory model by default.

Your next move: start work now, or run a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): Large effort, medium risk; deliverables: DMA readback fix, PERF-06 M=32 closure, 36-layer forward (Spike full + Ibex 9-layer segment run with 5 checkpoints; full Ibex run deferred to FPGA phase), FM-3 weight-streaming overlap calibration, and SFU/DRAM/MMIO/Q8_0 cleanup.

## Scope
### Must have
- **C1** 修复 `sim/cocotb_bridge.py` 的 CH1 DMA 读回全零（SRAM→DRAM），使 PERF 路径能通过 DMA 读回 MXU 输出做 golden 对比。注意：当前 wrapper 未实现 DMA linked-list 模式，因此修复对象是 **CH0/CH1 寄存器配置（SRC/DST/SIZE/CTRL）**，不是 descriptor chain。
- **C2** 定位并修复 PERF-06 (M=32, K=128, N=128) 残留（cos_sim=0.0535 → ≥0.999），`rtl/testcase-list-perf.md` 20/21 → 21/21。采用假设驱动诊断：先做 read-only 探针确认根因（固件 ring-buffer per-row accumulate reset vs RTL accumulate mode），再修对应侧。
- **C3** 36 层 forward pass：**Spike 全 36 层 + Ibex 段跑 checkpoint 子集（L0 + L9→L10/L19→L20/L29→L30/L34→L35 同会话段跑，共执行 9 层，chain-restart 状态源为 Ibex DRAM）**；全量 Ibex 36 层推迟到下一阶段 FPGA（见 Deferred to next phase）。先修复/解释 `ph9-36layer-checkpoint.txt` 中的 `FM-SOC-001 Ibex RTL Smoke: FAIL` 作为 pre-gate（注意：该 FAIL 是 checkpoint 工具链伪影——标准回归与独立 review gate 中 FM-SOC-001 均为 PASS，需按环境误报处置而非功能缺陷）。Spike-first 调试（全 36 层）、Ibex 权威确认（9 层段跑，4 段真实的 Ibex 路径层间状态传递）；per-layer 门槛采用**容差阶梯**：L0-19 ≥0.999、L20-29 ≥0.998、L30-35 ≥0.997（Phase 5 FM L35 基线为 0.998278，量化漂移在后段层可低于 0.999）；生成 36 层 per-layer cos_sim dump（标注证据来源列：spike / ibex-checkpoint / ibex-segment-run）。注意：Phase 9 的 36-layer checkpoint 是 **Func Model-only** 运行（见 `build/evidence/36layer-review-gate.txt`），不得作为 RTL 证据引用。
- **C4** FM-3 weight-streaming overlap RTL 实测 + Func Model 性能模型校准报告。使用 Q4_K_M 权重（与 Phase 9 一致），Q8_0 仅重试下载、成功才补做，失败按 BLOCKED-NETWORK 短路。
- **C5a 功能 RTL 修复**：SFU 3 个 wrapper 输出 mismatch（gelu / width_converter_32to512 / line_buffer_prefetch）诊断+修复；BUG-RTL-SOC-002 DRAM 8MB 窗口采取低回归风险方案（约束 firmware/test 地址在 8MB 窗口内，必要时再扩展 dram_model.v）。
- **C5b 文档/网络清理**：MMIO spec 文档缺口处置（MXU BIAS/SCALE stub 文档化/实现；wrapper SRAM base；APB→MMIO strobe 文档同步）；Q8_0 下载重试（BLOCKED-NETWORK 短路）；bug 台账 BUG-RTL-SOC-P9-00D 重复条目去重
- 每波修复后全回归：pytest ≥732、FM-SOC 33/33、MXU 9/9、SFU 319/319、Vector 63/63、wrapper 15 功能测试（SFU 5 + Vector 5 + MXU 5；`test_bug005_sfu_nonaligned_xprop` 为 by-design FAIL，仅限 sparse TB，不计入）
- Phase 9 用户强制惯例延续：script-first（`scripts/p10_*.sh` + `scripts/p10_lib/`）、全部验证在 sz0001 (192.168.0.11)、bug-tracking 强制（`docs/bugs/bugs-soc-rtl.md` + 独立证据文件）、主分支推进

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不触碰 Arc Model（已迁 npu_arc_model 仓库；本仓 `sim/design_space_explorer.py` 为遗留副本，冻结不动）
- 不新增 RTL 功能特性——Phase 10 是验证+修复，不是新硬件开发；BIAS/SCALE stub 只做"实现或明确文档化为不适用"二选一，不超出此范围扩展
- 不修改 firmware 功能语义，除非 PERF-06 诊断（read-only 探针）确证根因在固件侧
- `sim/cocotb_bridge.py` 修改仅限 DMA CH0/CH1 配置/读回路径，不动核心模拟逻辑；**升级条款**：若 todo 4 诊断确证根因在 RTL（dma_wrapper FSM / sram_ctrl clear-on-completion），则按计划修订流程开 feature-branch RTL 修复，禁止在 Python 中掩盖 RTL 缺陷
- 不重跑无关历史 phase 脚本；不引入新工具链依赖
- 不在非 sz0001 机器上跑 VCS 验证
- Q8_0 网络失败时按短路规则标记 BLOCKED-NETWORK 即止，不无限重试

### Deferred to next phase (FPGA)
- **全量 Ibex 36 层 forward（原方案 A）推迟到 FPGA 阶段执行**。本阶段在 Ibex 上执行 9 层段跑（L0 + L9→L10/L19→L20/L29→L30/L34→L35 同会话段跑，5 个 checkpoint 层做 golden 对比），其余 27 层的 Ibex 验证由 FPGA 阶段兜底。
- **FPGA 阶段前置条件（写入下一阶段计划）**：(1) 必须具备 per-layer 状态导出设施（halt-and-dump 或等价机制），保证逐层 cos_sim 对比与逐层定位能力；(2) FPGA 逐层比对 golden 沿用本计划容差阶梯（L0-19 ≥0.999、L20-29 ≥0.998、L30-35 ≥0.997）；(3) **兜底条款**：若 FPGA 只能比最终输出、无法逐层导出，则该阶段的 FPGA 全量跑不能作为 C3 的替代证据，必须回到 Ibex 全量仿真补齐。
- **本阶段遗留的覆盖缺口（FPGA 阶段兜底）**：Ibex 路径 27 层未覆盖（段跑仅覆盖 L0/L9/L10/L19/L20/L29/L30/L34/L35）；长跑类固件/硬件边界 bug（ring buffer 回绕、DMA descriptor 长链、中断风暴、队列满）无仿真压力测试；容差阶梯深层段的连续 RTL 证据稀疏。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + 全回归框架（pytest、FM-SOC、MXU/SFU/Vector batch、wrapper cocotb、VCS+Spike）
- Evidence: `build/evidence/task-<N>-phase10-rtl-verification.txt`（统一命名，每 todo 一份；脚本执行结果直接落盘）。每份证据必须包含：时间戳、commit、执行的精确命令、PASS/FAIL 状态、关键指标（cos_sim、cycles、overlap_ratio 等）
- 每波门禁：该波全部 acceptance 命令通过 + 全回归无新增 FAIL；回归基线以 `build/evidence/ph9-regression-run.log` 为参照
- 修复型 todo 一律要求"诊断证据 → 修复 → 因果 gate（修复后指标达标且回归干净）"三段式，禁止无诊断直接改代码
- 所有 RTL/firmware 修改走 feature branch（`ph10/<component>`），每次修改后先跑模块级回归，再合并回 main；保留 rollback commit（`git revert`-ready）
- W0 额外验证 sz0001 基础设施：SSH 可达、`vcs/vcs_2023.12sp2` 模块加载、`firmware/build/npu_firmware.elf` 可重建

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 0（骨架+基线，3 todos）**：p10 脚本库骨架（复用 `scripts/p9_lib/p9_sz0001.sh` 的 ssh+env 模式）；sz0001 基础设施检查；sz0001 全回归基线复跑，显式捕获 Phase 9 的 passes 与 known residuals（PERF-06 cs=0.0535、Q8_0 BLOCKED-NETWORK、36L Ibex smoke FAIL）。无依赖。
- **Wave 1（DMA readback fix，3 todos）**：诊断（FM-SOC 路径 vs PERF 路径 **CH0/CH1 寄存器配置**对比，read-only 探针）→ 修复 `sim/cocotb_bridge.py` + bug 登记 → 回归。被 Wave 0 阻塞；阻塞 Wave 3/4。
- **Wave 2（PERF-06，3 todos）**：假设驱动诊断（固件 ring-buffer dispatch per-row accumulate reset 探针；RTL accumulate mode 波形；设计 falsification 实验）→ 按结论修复（固件或 RTL 分支）→ 因果 gate + 21/21 更新。被 Wave 0 阻塞；与 Wave 1 可并行。
- **Wave 3（36-layer forward，5 todos）**：Ibex smoke FAIL pre-gate（修复/解释 `FM-SOC-001`）→ 前置检查 → Spike-first 全 36 层 → Ibex 段跑权威确认（L0 + L9→L10/L19→L20/L29→L30/L34→L35 同会话段跑，9 层）→ per-layer 结果分析。被 Wave 1 阻塞。全量 Ibex 36 层推迟到 FPGA 阶段（见 Deferred to next phase）。
- **Wave 4（FM-3 校准，3 todos）**：overlap RTL 实测 → FM 校准参数更新 → 校准报告。被 Wave 3 阻塞。
- **Wave 5（收尾，5 todos）**：SFU wrapper 3 输出 mismatch 诊断+修复；DRAM 8MB 窗口约束方案；MMIO spec 文档缺口处置；Q8_0 下载重试（BLOCKED-NETWORK 短路）；bug 台账去重。被 Wave 0 阻塞（todo 22 除外：需 5/9/10 完成后启动）；彼此独立可并行。其中 Q8_0 重试与文档类 todo 仅依赖 todo 2（基础设施）即可启动。
- **Final（F1-F4）**：全并行，全部 APPROVE 后交用户签收。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (p10 骨架) | — | 2-22 | — |
| 2 (sz0001 基础设施) | 1 | 3-22 | — |
| 3 (基线复跑) | 2 | 4-22 | — |
| 4 (DMA 诊断) | 3 | 5,6 | 7 |
| 5 (DMA 修复) | 4 | 6, 11, 22 | 8 |
| 6 (W1 回归) | 5 | 10, 11 | 9 |
| 7 (PERF-06 诊断) | 3 | 8 | 4,5 |
| 8 (PERF-06 修复) | 7 | 9 | 5 |
| 9 (因果 gate + 21/21) | 8 | 22 | 6 |
| 10 (Ibex smoke pre-gate) | 6 | 11, 22 | — |
| 11 (36L 前置检查) | 10 | 12,13 | — |
| 12 (Spike-first) | 11 | 14 | — |
| 13 (Ibex 段跑) | 12 | 14 | — |
| 14 (per-layer 分析) | 12,13 | 15 | — |
| 15 (FM-3 实测) | 14 | 16 | — |
| 16 (FM 校准更新) | 15 | 17 | — |
| 17 (校准报告) | 16 | — | — |
| 18 (SFU wrapper) | 3 | 22 | 19,20,21 |
| 19 (DRAM 8MB) | 3 | 22 | 18,20,21 |
| 20 (MMIO spec 文档) | 2 | 22 | 18,19,21 |
| 21 (Q8_0 重试) | 2 | — | 18-20 |
| 22 (bug 台账去重) | 5,9,10,18,19,20 | — | 21 |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->

### Wave 0 — 骨架 + 基线

- [x] 1. 创建 p10 脚本库骨架（`scripts/p10_lib/p10_sz0001.sh` + `scripts/p10_env_check.sh`）
  What to do / Must NOT do: 复用 `scripts/p9_lib/p9_sz0001.sh` 的 ssh+env 封装（`p9_ssh()`、`p9_chmod()`），改为 `p10_ssh()` / `p10_chmod()`；`p10_env_check.sh` 验证 bash 语法、REPO_ROOT 解析、可执行权限。Must NOT 硬编码新密码或改动 p9 脚本。
  Parallelization: Wave 0 | Blocked by: — | Blocks: 2-22
  References (executor has NO interview context - be exhaustive): `scripts/p9_lib/p9_sz0001.sh`（L1-L9 ssh+env 函数）; `scripts/p9_env_check.sh`（Phase 9 环境检查模板）; `sim/regression/run_env.sh`（VCS/firmware 环境）
  Acceptance criteria (agent-executable): `bash scripts/p10_env_check.sh` exit 0 且输出包含 `p10_ssh ready`、`REPO_ROOT=/home/prj/zhengs/caduceuscore/CaduceusCore`。
  QA scenarios (name the exact tool + invocation): happy — `bash scripts/p10_env_check.sh` 通过；failure — 临时把 `SZ0001` 改成无效 IP，脚本应非 0 退出并打印 `SSH unreachable`。Evidence `build/evidence/task-1-phase10-rtl-verification.txt`
  Commit: Y | chore(scripts): add p10 script skeleton and env check

- [x] 2. sz0001 基础设施检查（SSH/VCS 模块/firmware 可重建/资源余量）
  What to do / Must NOT do: 通过 `p10_ssh` 跑最小命令：检查 `module load vcs/vcs_2023.12sp2` 成功；检查 `firmware/build/npu_firmware.elf` 存在或 `make -C firmware` 可在 sz0001 上编译成功；检查 VCS license 余量、CPU load、`build/` 分区磁盘空间（Ibex checkpoint 子集 VCS 仿真与 FSDB dump 需要；FPGA 阶段全量需求另计）。Must NOT 在非 sz0001 机器跑 VCS。
  Parallelization: Wave 0 | Blocked by: 1 | Blocks: 3-22
  References: `scripts/p9_lib/p9_sz0001.sh` L6-L8; `firmware/Makefile`; `sim/regression/run_env.sh`
  Acceptance criteria: `bash scripts/p10_infra_check.sh` exit 0，输出包含 `vcs version ok`、`firmware build ok`、`license ok`、`cpu ok`、`disk ok`。
  QA scenarios: happy — sz0001 可达且 VCS 模块加载成功、资源余量充足；failure — VCS 模块缺失或资源不足时脚本非 0 退出并提示具体资源。Evidence `build/evidence/task-2-phase10-rtl-verification.txt`
  Commit: Y | chore(scripts): add sz0001 infra and resource check

- [x] 3. Phase 9 全回归基线复跑（捕获 passes + known residuals）
  What to do / Must NOT do: 在 sz0001 上复跑 pytest ≥732、FM-SOC 33/33、MXU 9/9、SFU 319/319、Vector 63/63、wrapper 回归；显式记录已知残留，并**区分两类**：(a) 标准回归残留——PERF-06 cs=0.0535、Q8_0 BLOCKED-NETWORK；(b) checkpoint 工具链伪影——`ph9-36layer-checkpoint.txt` 中的 FM-SOC-001 FAIL（标准回归中 FM-SOC-001 是 PASS，见 `ph9-regression-run.log` `[RUN]/[PASS] FM-SOC-001`）。Must NOT 掩盖或跳过已知失败，Must NOT 把伪影与真实残留混为一谈。
  Parallelization: Wave 0 | Blocked by: 2 | Blocks: 4-22
  References: `build/evidence/ph9-regression-run.log`（FM-SOC-001 PASS 行）; `build/evidence/ph9-closure.txt` L38-L62（pass counts + REST NOT RESOLVED + REMAINING BLOCKERS）; `scripts/p9_regression.sh`; `rtl/testcase-list-perf.md` L77; `build/evidence/36layer-review-gate.txt`（36-layer 为 FM-only 的明确声明）
  Acceptance criteria: `bash scripts/p10_baseline_regression.sh` exit 0，生成 `build/evidence/task-3-phase10-rtl-verification.txt`，其中包含 `pytest_total`/`fm_soc_pass`/`mxu_pass`/`sfu_pass`/`vector_pass`/`wrapper_status` 与 Phase 9 基线一致，并显式列出 (a) 真实残留与 (b) 工具链伪影两组。
  QA scenarios: happy — 全部回归 PASS 数与 Phase 9 一致且两组残留分列正确；failure — 任一回归新增 FAIL 或两组混淆时脚本非 0 退出。Evidence `build/evidence/task-3-phase10-rtl-verification.txt`
  Commit: Y | test(scripts): add Phase 10 baseline regression runner

### Wave 1 — DMA readback fix

- [x] 4. 诊断 DMA CH1 读回全零根因（FM-SOC 路径 vs PERF 路径 CH0/CH1 寄存器配置对比）
  What to do / Must NOT do: read-only 探针：在 `sim/cocotb_bridge.py` 中对 FM-SOC 路径（backdoor SRAM read，33/33 PASS）与 PERF 路径（CH1 DMA SRAM→DRAM）分别打印 CH0/CH1 `src_addr`、`dst_addr`、`transfer_size`、`control` 寄存器值，以及 `sram_ctrl` 完成后的 DRAM 读出值。根因判定须覆盖全部三个已记录假设：`dst_addr/direction 配置错误`、`MXU wrapper 输出 drain 行为`、`sram_ctrl clear-on-completion`。Must NOT 修改 cocotb_bridge.py 逻辑（只加日志/探针）。
  Parallelization: Wave 1 | Blocked by: 3 | Blocks: 5,6
  References: `docs/issues_found.md` L363-L396（DMA readback zeros + next step，含三个根因假设）; `sim/cocotb_bridge.py` 中 `configure_dma()`/`configure_dma_ch1()` 和 CH1 相关代码; `.omo/plans/rtl-update-plan.md` L255-L256（linked-list mode not implemented）; `rtl/ip/dma_wrapper.v`; `rtl/soc/sram_ctrl.v`
  Acceptance criteria: `bash scripts/p10_diag_dma_readback.sh` 生成 `build/evidence/task-4-phase10-rtl-verification.txt`，包含 FM-SOC 与 PERF 路径的 CH0/CH1 寄存器 diff 和根因判定，格式为 `ROOT_CAUSE=<python|rtl>:<具体字段/行为>`。
  QA scenarios: happy — 根因被定位到一个具体寄存器/配置字段或 RTL 行为；failure — diff 为空（说明探针未命中）时脚本非 0 退出。Evidence `build/evidence/task-4-phase10-rtl-verification.txt`
  Commit: Y | docs(evidence): revise DMA readback diagnosis after firmware fix

- [x] 5. 验证/清理：todo 4 探针在固件修复后已不需要默认启用，将其禁用并验证 DMA readback 仍工作
  What to do / Must NOT do: todo 4 的 `ROOT_CAUSE=firmware:npu_firmware.c output DMA row interleave already fixed by commit 7aec7a3`，因此不需要修复 `sim/cocotb_bridge.py` 的 CH1 配置/读回路径，也不需要登记新的 RTL bug。本 todo 改为验证任务：将 `COCOTB_BRIDGE_DIAG_DMA` 默认改为禁用（opt-in），并跑 `test_e2e_dma_load_store` + 一个最小 PERF 样例（如 `test_w4_perf_p0`）确认 DMA readback 仍然 PASS。Must NOT 删除探针代码（保留以便将来复用），只改默认开关。
  Parallelization: Wave 1 | Blocked by: 4 | Blocks: 6, 11, 22
  References: `sim/cocotb_bridge.py`; `build/evidence/task-4-phase10-rtl-verification.txt`; `sim/regression/Makefile` `run_e2e_dma_load`
  Acceptance criteria: `COCOTB_BRIDGE_DIAG_DMA` 默认关闭后，`make run_e2e_dma_load` PASS，且 `TESTCASE=test_w4_perf_p0` 的 PERF 样例 PASS（无需额外 evidence 文件，结果记录在 task-4 evidence notes 或 commit message 中）。
  QA scenarios: happy — 探针关闭后 DMA/perf 仍 PASS；failure — 任一验证 FAIL，则恢复默认开启并重新调查。Evidence: inline in commit + task-4 evidence notes
  Commit: Y | docs(evidence): revise DMA readback diagnosis after firmware fix

- [x] 6. Wave 1 全回归（pytest + FM-SOC + PERF 抽样）
  What to do / Must NOT do: 修复后跑全回归：pytest ≥732、FM-SOC 33/33、MXU 9/9、SFU 319/319、Vector 63/63；再抽跑 PERF-09/10/11/13 至少 3 例，确认 DMA readback 修复未引入性能/正确性退化。Must NOT 只跑模块级而不跑 SoC 级。
  Parallelization: Wave 1 | Blocked by: 5 | Blocks: 10, 11
  References: `scripts/p9_regression.sh`; `scripts/p9_perfect_batch.sh`; `rtl/testcase-list-perf.md` L63-L104
  Acceptance criteria: `bash scripts/p10_w1_regression.sh` exit 0，生成 `build/evidence/task-6-phase10-rtl-verification.txt`，包含所有回归 PASS 数和 PERF 抽样 cos_sim≥0.999。
  QA scenarios: happy — 全绿且 PERF 抽样通过；failure — 任一回归 FAIL 或 PERF 抽样 cs<0.999 时非 0 退出。Evidence `build/evidence/task-6-phase10-rtl-verification.txt`
  Commit: Y | test(regression): Wave 1 full regression after DMA fix

### Wave 2 — PERF-06 M=32

- [x] 7. PERF-06 假设驱动诊断（固件 ring-buffer vs RTL accumulate mode）
  What to do / Must NOT do: read-only 探针 + falsification 实验：在 M=32 运行时抓取 firmware ring-buffer 的 32 次 dispatch 序列、`CTRL[2]` accumulate 位、`accumulator.v` 的 per-row reset 信号；同时跑 M=1 同配置作为对照。Must NOT 改任何代码，只加探针/日志。
  Parallelization: Wave 2 | Blocked by: 3 | Blocks: 8
  References: `build/evidence/ph9-perf-residual.txt`; `build/evidence/ph9-closure.txt` L46-L50（PERF-06 根因假设）; `rtl/testcase-list-perf.md` L77; `firmware/npu_firmware.c` ring-buffer dispatch; `rtl/mxu/accumulator.v`
  Acceptance criteria: `bash scripts/p10_diag_perf06.sh` 生成 `build/evidence/task-7-phase10-rtl-verification.txt`，明确结论为 `ROOT_CAUSE=FIRMWARE` 或 `ROOT_CAUSE=RTL`，并附关键信号 trace。
  QA scenarios: happy — 根因被证据唯一指向 firmware 或 RTL；failure — M=1 与 M=32 信号无差异，无法定位，脚本非 0 退出。Evidence `build/evidence/task-7-phase10-rtl-verification.txt`
  Commit: Y | docs(evidence): PERF-06 hypothesis-driven diagnosis

- [x] 8. 按诊断结论修复 PERF-06（固件或 RTL 分支）
  What to do / Must NOT do: 若 ROOT_CAUSE=FIRMWARE：修 `firmware/npu_firmware.c` 或 Python 模拟层 `sim/miniv.py` 的 M=32 dispatch 逻辑；若 ROOT_CAUSE=RTL：修 `rtl/mxu/accumulator.v` 或 `controller.v` 的 per-row accumulate reset。走 feature branch，先模块级回归再合并。Must NOT 在根因不明时同时改两侧。
  Parallelization: Wave 2 | Blocked by: 7 | Blocks: 9
  References: `build/evidence/task-7-phase10-rtl-verification.txt`; `firmware/npu_firmware.c`; `sim/miniv.py`; `rtl/mxu/accumulator.v`; `rtl/mxu/controller.v`
  Acceptance criteria: `bash scripts/p10_fix_perf06.sh` exit 0，生成 `build/evidence/task-8-phase10-rtl-verification.txt`，其中 PERF-06 `cos_sim>=0.999`。
  QA scenarios: happy — PERF-06 复跑 cos_sim≥0.999；failure — 修复后 cs 仍 <0.999 或 M=1 回归退化，脚本非 0 退出。Evidence `build/evidence/task-8-phase10-rtl-verification.txt`
  Commit: Y | fix(<scope>): PERF-06 M=32 per-row accumulate reset

- [x] 9. PERF-06 因果 gate + testcase-list 21/21 更新
  What to do / Must NOT do: 修复后跑完整 PERF P0-P3 回归，确认 21/21 PASS；更新 `rtl/testcase-list-perf.md` PERF-06 行为 ✅ PASS 并附证据路径，同时**同步 L183 的状态统计行**（现为过期的 `PASS 17 | NOT RESOLVED 2`，应为 `PASS 21 | NOT RESOLVED 0` 或实际数值）。Must NOT 只更新表格不跑回归。
  Parallelization: Wave 2 | Blocked by: 8 | Blocks: 22
  References: `rtl/testcase-list-perf.md` L77（PERF-06 行）与 L183（统计行）; `scripts/p9_perfect_batch.sh`; `build/evidence/w4-perf-p*.txt`
  Acceptance criteria: `bash scripts/p10_perf06_causality_gate.sh` exit 0，生成 `build/evidence/task-9-phase10-rtl-verification.txt`，包含 `PERF-06 cos_sim>=0.999`、`testcase-list: 21/21 PASS` 和 `stats-line-synced: true`。
  QA scenarios: happy — 21/21 PASS 且表格与统计行均已更新；failure — 任一 PERF 项 FAIL 或表格/统计行未更新，脚本非 0 退出。Evidence `build/evidence/task-9-phase10-rtl-verification.txt`
  Commit: Y | docs(rtl): mark PERF-06 PASS and update testcase-list

### Wave 3 — 36-layer forward（Spike 全量 + Ibex 段跑 checkpoint）

- [x] 10. 36-layer Ibex smoke pre-gate（修复/解释 FM-SOC-001 FAIL）
  What to do / Must NOT do: 复现 `ph9-36layer-checkpoint.txt` 中的 `FM-SOC-001 Ibex RTL Smoke: FAIL, cycles:0, error:unknown`。**已知背景**：标准回归（`ph9-regression-run.log` `[PASS] FM-SOC-001`）与独立 review gate（TESTS=1 PASS=1, 787k cycles）中 FM-SOC-001 均 PASS，此 FAIL 极可能是 checkpoint 工具链伪影（缺 `build/ibex_full_rtl/simv_soc_ibex`）。复现后：若是伪影则写解释文档（waiver），若是真实失败则修复；必须让 FM-SOC-001 通过或给出明确 waiver。Must NOT 跳过此失败直接跑全量。
  Parallelization: Wave 3 | Blocked by: 6 | Blocks: 11, 22
  References: `build/evidence/ph9-36layer-checkpoint.txt` L7-L10; `build/evidence/ph9-regression-run.log`（FM-SOC-001 PASS 行）; `build/evidence/36layer-review-gate.txt`（TESTS=1 PASS=1, 787,012 cycles）; `sim/regression/run_fm_soc_all.sh`; `sim/regression/run_ibex_full_rtl.sh`
  Acceptance criteria: `bash scripts/p10_fm_soc_001_smoke.sh` exit 0，生成 `build/evidence/task-10-phase10-rtl-verification.txt`，包含 `FM-SOC-001: PASS` 或 `WAIVED: <reason>`。
  QA scenarios: happy — FM-SOC-001 通过或拿到有证据的 waiver；failure — 仍 FAIL 且无明确 waiver，脚本非 0 退出。Evidence `build/evidence/task-10-phase10-rtl-verification.txt`
  Commit: Y | fix(soc) or docs(soc): resolve or waive FM-SOC-001 Ibex smoke failure

- [x] 11. 36-layer全量前置检查（descriptor chain、streaming、BUG-RTL-SOC-007、DRAM 窗口、运行时长）
  What to do / Must NOT do: 检查 36 层每层权重 DMA preload 的**固件侧** op descriptor/命令序列是否可正确遍历生成（**注意：硬件 DMA linked-list 模式未实现**——见 C1 与 `rtl-update-plan.md` L255-L256，此检查针对 firmware 逐层命令序列，不是硬件 descriptor chain）、SRAM 预算是否足够、Spike 插件路径可用；新增三项检查：(1) `attn_weight_dispatch_ok`——BUG-RTL-SOC-007（Critical, Open，`docs/bugs/bugs-soc-rtl.md` L325-360）曾报告 3 层链中全部 `attn_weight` op cycles=0（op 从未执行），而 36 层每层都含 `attn_weight`，必须验证 36 层固件流中 attn_weight 实际执行（cycles>0）；(2) `dram_window_ok`——BUG-RTL-SOC-002 曾报固件地址 0x81FFFFC0≈+32MB 超出 8MB 模型，**若检查失败：按 todo 19 的方案立即施加地址约束（拉前该工作）后再继续，不得以检查失败为由死锁**；(3) **运行时长外推与 checkpoint 计划**——以 FM-SOC-001 smoke 的 787,012 cycles 为基准外推 36 层 VCS 总 wall-time（**仅供兜底条款触发时的全量 Ibex VCS 规划参考，低置信度，需标注外推假设**），并准备 **Ibex checkpoint 段跑计划：L0 + L9→L10/L19→L20/L29→L30/L34→L35 共 9 层，每个段在同一仿真会话内连续执行、层间状态留在 DRAM，按层存盘、失败可续跑**。Must NOT 直接启动 36 层 Ibex 全量长仿真（全量已推迟到 FPGA 阶段）。
  Parallelization: Wave 3 | Blocked by: 10 | Blocks: 12,13
  References: `scripts/run_36layer_checkpoint.py`（仅作参考——注意其为 FM-only 实现）; `firmware/npu_firmware.c` descriptor struct; `docs/issues_found.md` L332-L335; `build/evidence/ph9-sram-budget.txt`; `docs/bugs/bugs-soc-rtl.md` L325-L360（BUG-RTL-SOC-007）与 BUG-RTL-SOC-002 条目; `build/evidence/36layer-review-gate.txt`（smoke 787,012 cycles）; `rtl/testcase-list-perf.md` L148（Q_proj 单 MMUL ~3.38M cycles 预测）
  Acceptance criteria: `bash scripts/p10_36layer_preflight.sh` exit 0，生成 `build/evidence/task-11-phase10-rtl-verification.txt`，包含 `descriptor_chain_ok`、`sram_budget_ok`、`spike_path_ok`、`attn_weight_dispatch_ok`、`dram_window_ok`、`runtime_estimate_ok`（含 checkpoint/restart 计划）。
  QA scenarios: happy — 预检全部通过且 BUG-RTL-SOC-007 在 36 层流中确认不复发；failure — 任一项不通过或 attn_weight cycles=0，脚本非 0 退出。Evidence `build/evidence/task-11-phase10-rtl-verification.txt`
  Commit: Y | chore(scripts): add 36-layer forward preflight

- [x] 12. Spike-first 36 层全量 forward
  What to do / Must NOT do: 在 sz0001 用 Spike + firmware + MMIO bridge 跑 36 层 forward，**必须用 `sim/spike_host.py --mode forward`**；生成 per-layer hidden state dump（npz 格式）。**注意：当前 `sim/spike_host.py` 不保存 per-layer npz——需本 todo 扩展或包装它**（复用 `run_forward_pass` 返回的 layer_outputs）以产出 `build/evidence/ph10-36layer-spike.npz`（供 todo 13 交叉核对）；比对 Func Model golden。**证据完整性要求**：证据文件必须带 `engine=spike` 字段，脚本 `p10_36layer_spike.sh` 必须断言该字段（防止误用 FM-only 的 `run_36layer_checkpoint.py` 冒充 RTL 证据）。per-layer 门槛用**容差阶梯**：L0-19 ≥0.999、L20-29 ≥0.998、L30-35 ≥0.997。Must NOT 用 FM-only checkpoint 脚本充当本任务证据，Must NOT 未跑 checkpoint 就先跑全量。
  Parallelization: Wave 3 | Blocked by: 11 | Blocks: 14
  References: `sim/spike_host.py`（--mode forward --layers 36）; `build/evidence/ph9-36layer-checkpoint.txt` L25-L28（注意：这是 FM-only 数值，仅作阈值参考，勿引用为 RTL 证据）; `build/evidence/36layer-review-gate.txt`（"The actual 36-layer forward pass was executed in the Func Model… not as a full 36-layer RTL simulation"）; `scripts/run_36layer_checkpoint.py`（明确其 FM-only 实现，作为反例参考）
  Acceptance criteria: `bash scripts/p10_36layer_spike.sh` exit 0，生成 `build/evidence/task-12-phase10-rtl-verification.txt`，包含 `layers_run=36`、`engine=spike`、容差阶梯逐层判定结果，以及 `build/evidence/ph10-36layer-spike.npz`。
  QA scenarios: happy — 36 层跑完、`engine=spike` 断言通过、阶梯阈值达标；failure — 任一层低于其阶梯阈值、或 `engine` 字段非 spike，脚本非 0 退出。Evidence `build/evidence/task-12-phase10-rtl-verification.txt`
  Commit: Y | feat(scripts): Spike-first 36-layer RTL forward pass

- [x] 13. Ibex 权威确认 36 层 checkpoint 子集（段跑 L0 + L9→L10/L19→L20/L29→L30/L34→L35）
  What to do / Must NOT do: 在 sz0001 用 Ibex SoC VCS 仿真执行 **9 层段跑**：L0 从初始输入起跑；对每个 checkpoint，在**同一个仿真会话内**连续执行前层与 checkpoint 层（L9→L10、L19→L20、L29→L30、L34→L35），前层的 hidden state 留在 DRAM 中直接作为下一层输入，**不得从外部加载状态作为续跑源**。**段的初始输入**：L9/L19/L29/L34 段的初始输入（即 L8/L18/L28/L33 的 hidden state）从 `build/evidence/ph10-36layer-spike.npz` 对应层读取——这是段的初始条件，不是 checkpoint 层的续跑源。段跑覆盖 4 段真实的 Ibex 路径层间状态传递；每层存盘、失败可断点续跑。生成 per-layer dump（cocotb 控制层在每层完成后经 DRAM 读回该层 hidden state 落盘 npz；仅 checkpoint 层 L0/L10/L20/L30/L35 与 golden 比对）。门槛与 todo 12 相同的**容差阶梯**：L0/L10 属 L0-19 档 ≥0.999、L20 属 L20-29 档 ≥0.998、L30/L35 属 L30-35 档 ≥0.997（与 C3 一致，不要用统一的 ≥0.999）。**Spike 前层状态仅用于交叉核对**（进入 checkpoint 层前，核对 Ibex 前层输出与 Spike 同层输出一致；核对阈值沿用该前层所属阶梯档位，不一致记 `cross_check_mismatch=<layer>`，不 gate PASS，留 todo 14 分析），**不作为续跑源**。**明确边界**：其余 27 层的 Ibex 验证推迟到 FPGA 阶段（见 Deferred to next phase），本 todo 不得私自扩大为全量。Must NOT 在 Spike 未通过时跑 Ibex，Must NOT 用 Spike/npz 状态注入代替同会话段跑（那会使 Ibex 层间状态传递零覆盖）。
  Parallelization: Wave 3 | Blocked by: 12 | Blocks: 14
  References: `sim/regression/run_fm_soc_all.sh`（单 case 触发方式）; `rtl/tb/tb_soc.v` + `sim/cocotb_bridge.py`（段跑实现模式：扩展 cocotb 控制层在同会话连续触发两层，层间状态留在 DRAM 不重置——该机制当前不存在，需本 todo 实现）; `rtl/soc/caduceus_soc_top.v`; `build/evidence/task-11-phase10-rtl-verification.txt`（runtime_estimate 与 checkpoint/restart 计划）; `build/evidence/36layer-review-gate.txt`（smoke 787,012 cycles 基准）; `build/evidence/ph10-36layer-spike.npz`（Spike 前层 hidden state，仅供交叉核对，不作续跑源）
  Acceptance criteria: `bash scripts/p10_36layer_ibex.sh` exit 0，生成 `build/evidence/task-13-phase10-rtl-verification.txt`，包含 `ibex_executed=L0,L9,L10,L19,L20,L29,L30,L34,L35`、`checkpoints=L0,L10,L20,L30,L35`、`chain_restart=true`、`chain_restart_state_source=ibex_dram`（每个 checkpoint 记录其段跑会话与 DRAM 状态传递方式）、`segment_input_source=spike_npz`（L9/L19/L29/L34 段的初始输入来源）、`engine=ibex`、按容差阶梯逐 checkpoint 判定，以及 `build/evidence/ph10-36layer-ibex-checkpoints.npz`。
  QA scenarios: happy — 9 层段跑完成、5 个 checkpoint 按阶梯达标、`chain_restart_state_source=ibex_dram` 断言通过；failure — 任一 checkpoint 低于其阶梯阈值、`chain_restart_state_source != ibex_dram`（续跑源非 Ibex DRAM）或 `engine` 非 ibex，脚本非 0 退出。注：`chain_restart_state_source` 为脚本自报字段，其真实性由每个 checkpoint 的段跑会话记录 + F3 独立复现兜底。Evidence `build/evidence/task-13-phase10-rtl-verification.txt`
  Commit: Y | feat(scripts): Ibex 36-layer checkpoint-subset segment-run RTL forward pass

- [x] 14. 36 层 per-layer 结果分析报告
  What to do / Must NOT do: 汇总 Spike 36 层与 Ibex 9 层段跑（5 个 checkpoint 层做对比）的 per-layer cos_sim、per-layer cycles、总 cycles；按**容差阶梯**（L0-19 ≥0.999、L20-29 ≥0.998、L30-35 ≥0.997）标注达标层与异常层；**每层明确标注证据来源**（spike / ibex-checkpoint / ibex-segment-run / 两者）；cycle 数据**标注引擎归属**（Spike host-cycles vs Ibex VCS cycles，禁止混用）；生成 `ibex_uncovered_layers` 清单（27 层：L1-L8、L11-L18、L21-L28、L31-L33）并写入下一阶段 FPGA 计划的前置条件记录；生成 Markdown 报告。Must NOT 只列数字不做分析，Must NOT 把 Spike-only 层的证据伪装成 Ibex 证据。
  Status: COMPLETE — `build/evidence/task-13-phase10-rtl-verification.txt` 已产出，5/5 Ibex checkpoint 通过阶梯，报告和 task-14 evidence 已更新为 FINAL。
  Parallelization: Wave 3 | Blocked by: 12,13 | Blocks: 15
  References: `build/evidence/task-12-phase10-rtl-verification.txt`; `build/evidence/task-13-phase10-rtl-verification.txt`; `docs/mxu-perf-calibration.md`
  Acceptance criteria: `bash scripts/p10_36layer_report.sh` 生成 `build/evidence/task-14-phase10-rtl-verification.txt` 和 `build/evidence/ph10-36layer-report.md`，包含 36 层 cos_sim 表（带阶梯阈值判定**与证据来源列**）、cycle 表（**每列标注引擎归属**）、Spike/Ibex 差异说明、`ibex_uncovered_layers=L1-L8,L11-L18,L21-L28,L31-L33` 清单。
  QA scenarios: happy — 报告完整、证据来源与 cycle 引擎标注正确且所有层指标按其阶梯达标；failure — 报告缺失、证据来源混淆、cycle 未标注引擎或异常层未标注，脚本非 0 退出。Evidence `build/evidence/task-14-phase10-rtl-verification.txt` + `build/evidence/ph10-36layer-report.md`
  Commit: Y | docs(evidence): 36-layer per-layer analysis report

### Wave 4 — FM-3 校准

- [x] 15. FM-3 weight-streaming overlap RTL 实测
  What to do / Must NOT do: 在 W3 的 per-layer cycle 数据基础上，专项测量 weight streaming 场景中 DMA preload 与 MXU compute 的 overlap ratio；使用 Q4_K_M 权重（Phase 9 成功配置）。Must NOT 因 Q8_0 未下载而阻塞本项。
  Parallelization: Wave 4 | Blocked by: 14 | Blocks: 16
  References: `docs/func-model-mmio-spec.md`; `docs/mxu-perf-calibration.md`; `sim/models/dma.py`; `sim/models/mxu.py`
  Acceptance criteria: `bash scripts/p10_fm3_measure.sh` exit 0，生成 `build/evidence/task-15-phase10-rtl-verification.txt`，包含 `overlap_ratio=X.XX`、原始 cycle trace 路径。
  QA scenarios: happy — 测得有效 overlap ratio；failure — RTL trace 缺少 DMA/MXU 事件，脚本非 0 退出。Evidence `build/evidence/task-15-phase10-rtl-verification.txt`
  Commit: Y | test(scripts): FM-3 overlap RTL measurement

- [x] 16. FM 校准参数更新
  What to do / Must NOT do: 对比 RTL 实测 overlap_ratio 与 Func Model 预测值；调整**实际存在的**校准参数：`weight_streaming_overlap_ratio` 是派生量（非存储旋钮），真正的调节杆在 `estimate_tile_double_buffer_overlap()` 内部的模型常数（`sim/models/dma.py` L225-L347，如 DMAModel 配置的 `bw_bytes_per_cycle`）以及 `sim/timing/benchmark.py` L87 `broadcast_sync = 2`、L88-90 `_accumulate`——调整这些使计算值逼近 RTL 实测；报告参数在 `sim/timing/dashboard.py` L119/L408 的**参数签名中体现（非可调旋钮）**；`cross_engine_gap` 是 `sim/perf_tests.py` L261 的已校准证据标注（FM-1 已校准为 4），RTL 实测若不同则更新该标注值。使 delta≤0.05。Must NOT 调整参数后不复跑 Func Model 验证，Must NOT 引用不存在的参数名（如 `dma_latency_cycles`、`fill_drain_overlap`——本仓库无此参数）。
  Parallelization: Wave 4 | Blocked by: 15 | Blocks: 17
  References: `sim/timing/benchmark.py` L67（计算函数）与 L162（传入报告）; `sim/models/dma.py` L225-L347（底层估计函数）; `sim/timing/dashboard.py` L119/L408（参数定义）; `sim/perf_tests.py` L261（cross_engine_gap 证据标注）; `docs/mxu-perf-calibration.md`
  Acceptance criteria: `bash scripts/p10_fm3_calibrate.sh` exit 0，生成 `build/evidence/task-16-phase10-rtl-verification.txt`，包含 `rtl_overlap=X.XX`、`fm_overlap=Y.YY`、`|delta|<=0.05`、更新后的参数名与数值。
  QA scenarios: happy — delta 在阈值内；failure — delta>0.05 或 Func Model 复跑失败，脚本非 0 退出。Evidence `build/evidence/task-16-phase10-rtl-verification.txt`
  Commit: Y | fix(sim): calibrate MXU overlap parameters against RTL

- [x] 17. FM-3 校准报告
  What to do / Must NOT do: 撰写独立报告 `build/evidence/ph10-fm3-calibration-report.md`（或更新 `docs/mxu-perf-calibration.md`），说明测量方法、RTL vs FM 数据、参数变更、残余误差。Must NOT 只写结论不写方法论。
  Parallelization: Wave 4 | Blocked by: 16 | Blocks: —
  References: `build/evidence/task-15-phase10-rtl-verification.txt`; `build/evidence/task-16-phase10-rtl-verification.txt`; `docs/func_model_performance_analysis.md`
  Acceptance criteria: 报告文件存在，包含 Method/Measurement/Calibration/Residual Error 四节，且被 `scripts/p10_fm3_report.sh` 验证（检查章节标题存在）。
  QA scenarios: happy — 报告完整；failure — 缺少任一节，脚本非 0 退出。Evidence `build/evidence/task-17-phase10-rtl-verification.txt` + `build/evidence/ph10-fm3-calibration-report.md`
  Commit: Y | docs(sim): FM-3 weight-streaming overlap calibration report

### Wave 5 — 收尾（功能 RTL + 文档/网络）

- [x] 18. SFU wrapper 3 个输出 mismatch 诊断+修复
  What to do / Must NOT do: 复跑 `sim/tests/wrapper/test_sfu_wrapper.py` 中 `test_sfu_gelu_normal`、`test_sfu_width_converter_32to512`、`test_sfu_line_buffer_prefetch`；加 read-only 探针定位是 wrapper 配置、数据宽度转换还是 line buffer 预取问题；修复后回归。**wrapper 基线定义**：15 个功能测试（SFU 5 + Vector 5 + MXU 5），另 `test_bug005_sfu_nonaligned_xprop` 为 **by-design FAIL**（bug 回归测试，仅在 sparse TB `tb_sfu_wrapper_sparse` 上通过，`docs/bugs/bugs-soc-rtl.md` L532 已记录），不计入功能测试数。走 feature branch。Must NOT 修改 SFU 模块已验证的 319/319 batch。
  Parallelization: Wave 5 | Blocked by: 3 | Blocks: 22
  References: `docs/bugs/bugs-soc-rtl.md` L512-L534; `sim/tests/wrapper/test_sfu_wrapper.py`; `rtl/wrapper/sfu_soc_wrapper.v`; `rtl/sfu/sfu_top.v`; `build/evidence/wrap-regression-summary.txt`
  Acceptance criteria: `bash scripts/p10_sfu_wrapper_fix.sh` exit 0，生成 `build/evidence/task-18-phase10-rtl-verification.txt`，包含修复前三项 FAIL、修复后三项 PASS、`sfu_wrapper functional: 5/5 PASS`（3 项 FAIL→PASS + 2 项原本 PASS），以及 SFU batch 319/319 仍 PASS。
  QA scenarios: happy — 3/3 wrapper 测试由 FAIL→PASS；failure — 修复引入 SFU 319 回归退化，脚本非 0 退出。Evidence `build/evidence/task-18-phase10-rtl-verification.txt`
  Commit: Y | fix(rtl/sfu): resolve wrapper output mismatches

- [x] 19. DRAM 8MB 窗口约束方案
  What to do / Must NOT do: 对 BUG-RTL-SOC-002 采取低回归风险方案：检查并约束 firmware/test 地址映射在 8MB 窗口内；若必须扩展 dram_model.v，则先评估对所有 SoC 回归的影响。**若约束已由 todo 11 拉前施加**（dram_window_ok 失败触发），本 todo 只验证因果 gate（FM-SOC 33/33 仍 PASS），`dram_window_constraint_applied` 记为 `already-applied-by-todo-11`。**并行窗口协调**：本 todo 仅依赖 todo 3，可能与 todo 11 的拉前操作并发——开始修改前先检查 git log/evidence 是否已有 task-11 或 task-19 的约束 commit，避免冲突或重复修改。Must NOT 直接无条件扩展 dram_model.v。
  Parallelization: Wave 5 | Blocked by: 3 | Blocks: 22
  References: `docs/bugs/bugs-soc-rtl.md` 中 BUG-RTL-SOC-002; `rtl/ip/dram_model.v`; `firmware/npu_firmware.c` DRAM 地址定义
  Acceptance criteria: `bash scripts/p10_dram_8mb.sh` exit 0，生成 `build/evidence/task-19-phase10-rtl-verification.txt`，包含 `dram_window_constraint_applied`（或 `already-applied-by-todo-11`）、FM-SOC 33/33 仍 PASS。
  QA scenarios: happy — 约束后 36 层/SoC 回归仍通过；failure — 地址越界或 SoC 回归退化，脚本非 0 退出。Evidence `build/evidence/task-19-phase10-rtl-verification.txt`
  Commit: Y | fix(firmware/soc): constrain DRAM accesses within 8MB window

### 文档/网络清理（Wave 5 后半）

- [x] 20. MMIO spec 文档缺口处置
  What to do / Must NOT do: 依据 `rtl-update-plan.md` 逐项处置：MXU BIAS/SCALE stub 明确文档化为 Phase 1 不适用或实现最小逻辑；wrapper SRAM base 更新文档；APB→MMIO strobe 文档与 RTL 状态同步。只改文档，不改 RTL，除非 todo 18 需要。
  Parallelization: Wave 5 | Blocked by: 2 | Blocks: 22
  References: `.omo/plans/rtl-update-plan.md` L7-L11、L51-L73、L182-L222、L252-L281
  Acceptance criteria: `bash scripts/p10_mmio_doc_sync.sh` exit 0，生成 `build/evidence/task-20-phase10-rtl-verification.txt`，列出已处置缺口和仍保留的 future-phase 缺口。
  QA scenarios: happy — 所有 Phase 10 相关缺口已文档化；failure — 未覆盖 L7-L11 缺口，脚本非 0 退出。Evidence `build/evidence/task-20-phase10-rtl-verification.txt`
  Commit: Y | docs(spec): close MMIO spec gaps in rtl-update-plan

- [x] 21. Q8_0 下载重试（BLOCKED-NETWORK 短路）
  What to do / Must NOT do: 复用 `scripts/p9_q8o_download.sh` 重试下载 `Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q8_0.gguf`；成功则跑 Q8_0 6b 精度实验（实验结果记录为 INFO 级，不 gate PASS）；失败则写 `build/evidence/ph10-q8_0-download-FAILED.txt` 并标记 BLOCKED-NETWORK，F 波不依赖其实测数据。Must NOT 无限重试。
  Parallelization: Wave 5 | Blocked by: 2 | Blocks: —
  References: `scripts/p9_q8o_download.sh` L5-L81; `build/evidence/ph9-q8_0-download-FAILED.txt`
  Acceptance criteria: `bash scripts/p10_q8o_retry.sh` exit 0，生成 `build/evidence/task-21-phase10-rtl-verification.txt`，包含 `DOWNLOAD=SUCCESS/FAIL`、`BLOCKED-NETWORK` 标记（如失败）、以及 `ph10-q8_0-download-FAILED.txt` 路径（如失败）。终态映射（供 F1）：`PASS` = DOWNLOAD=SUCCESS；`BLOCKED-NETWORK` = DOWNLOAD=FAIL 短路。
  QA scenarios: happy — 下载成功并完成可选 6b 实验（结果 INFO 级）；failure — 下载超时/失败时正确标记 BLOCKED-NETWORK 并退出 0（这是期望的短路行为）。Evidence `build/evidence/task-21-phase10-rtl-verification.txt`
  Commit: Y | chore(scripts): retry Q8_0 download with BLOCKED-NETWORK short-circuit

- [x] 22. Bug 台账去重 + Phase 10 完整性检查
  What to do / Must NOT do: 清理 `docs/bugs/bugs-soc-rtl.md` 中重复的 `BUG-RTL-SOC-P9-00D` 条目；检查所有 Phase 10 新增/更新 bug 条目有对应证据路径；生成 bug 台账摘要。Must NOT 删除未解决的 bug。
  Parallelization: Wave 5 | Blocked by: 5,9,10,18,19,20 | Blocks: —
  References: `docs/bugs/bugs-soc-rtl.md` L458-L508（重复条目）; `build/evidence/task-5-phase10-rtl-verification.txt`、`task-8-phase10-rtl-verification.txt`、`task-10-phase10-rtl-verification.txt`、`task-18-phase10-rtl-verification.txt`、`task-19-phase10-rtl-verification.txt`（各 bug 条目的证据路径）
  Acceptance criteria: `bash scripts/p10_bug_ledger_check.sh` exit 0，生成 `build/evidence/task-22-phase10-rtl-verification.txt`，包含 `duplicate_count=0`、`open_bugs` 列表、`closed_bugs` 列表。
  QA scenarios: happy — 无重复、所有 open/closed bug 都有证据；failure — 发现重复或证据缺失，脚本非 0 退出。Evidence `build/evidence/task-22-phase10-rtl-verification.txt`
  Commit: Y | docs(bugs): deduplicate bug ledger and verify completeness

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.

- [x] F1. Plan compliance audit
  What: 逐条检查 22 个 todo 的 evidence 文件是否都存在、Acceptance criteria 是否全部通过、依赖矩阵是否一致、是否有 todo 被跳过或缩水。**终态接受规则**（按 todo 映射）：todo 10 接受 `PASS` 或 `WAIVED`；todo 21 接受 `PASS` 或 `BLOCKED-NETWORK`；其余 20 个 todo 只接受 `PASS`。
  Command: `bash scripts/p10_f1_audit.sh`（检查 `build/evidence/task-{1..22}-phase10-rtl-verification.txt` 存在且终态符合上述映射；检查 git log 与 plan 对应）
  Pass: 所有 evidence 存在、终态符合映射、无未解释 SKIP、AC 全通过。

- [x] F2. Code quality review
  What: 审查所有 RTL/firmware/Python 改动：无 TODO/FIXME/HACK 残留、无硬编码调试值、遵循现有代码风格、lsp_diagnostics/sim 无新增错误、测试覆盖新增分支。
  Command: `bash scripts/p10_f2_code_quality.sh`（grep TODO/FIXME/HACK；运行 `pytest sim/tests/ sim/timing/tests/`；检查 pylint/flake8 无新增警告）
  Pass: 0 新增 lint 错误、0 新增 pytest 失败、无可疑硬编码。

- [x] F3. Real manual QA
  What: 在 sz0001 上独立复跑关键路径：DMA readback fix（todo 5）、PERF-06 causality gate（todo 9）、FM-3 calibration（todo 16）；36-layer Ibex（todo 13）按计划只覆盖 9 层段跑（L0 + L9→L10/L19→L20/L29→L30/L34→L35，5 个 checkpoint），F3 验证其段跑可独立复现（含 `chain_restart_state_source=ibex_dram` 断言）+ 对全部 ph10 evidence 文件做哈希校验（防止证据被事后修改）。全量 Ibex 36 层不在本阶段范围内（推迟到 FPGA 阶段，见 Deferred to next phase）。
  Command: `bash scripts/p10_f3_manual_qa.sh`（ssh 到 sz0001，分别触发上述关键脚本；对 `build/evidence/task-*-phase10-rtl-verification.txt` 做 sha256 清单校验）
  Pass: 三项关键证据可被独立复现，checkpoint 子集达标，哈希清单无差异。

- [x] F4. Scope fidelity
  What: 确认交付物与计划 Scope IN/OUT 一致：没有新增 RTL 功能、没有改 Arc Model、没有引入新依赖、所有 out-of-scope 项都有明确记录。
  Command: `bash scripts/p10_f4_scope_gate.sh`（检查 git diff 不在 rtl/soc/caduceus_soc_top.v 做功能性添加；检查 sim/design_space_explorer.py 未改动；检查 requirements.txt 未新增依赖）
  Pass: 无 scope creep、无未批准的 out-of-scope 规避。

## Commit strategy
- 每个 todo 完成后一个原子 commit；若 todo 只有证据/文档改动也单独 commit。
- RTL/firmware 改动必须在 feature branch `ph10/<component>` 上进行，合并前通过模块级 + SoC 回归；保留 `git revert` 能力。
- 证据文件统一入 `build/evidence/` 并 commit，F 波审计依赖它们。
- Wave 5 结束后做一次总结合并提交：`docs(ph10): closure report and updated testcase-list/bug ledger`。
- Final Wave 全 APPROVE 后，由 Atlas 标记计划完成，不自动执行后续 Phase。

## Success criteria
- C1: `sim/cocotb_bridge.py` CH1 DMA 读回修复后，`test_e2e_dma_load_store` PASS 且 PERF 路径可非零读回 MXU 输出（证据 task-5/6）。
- C2: PERF-06 M=32 `cos_sim>=0.999`，`rtl/testcase-list-perf.md` 21/21 PASS（证据 task-9）。
- C3: Spike 全 36 层 forward 跑通（证据须含 `engine=spike`，36 层按**容差阶梯**达标）；Ibex 段跑 checkpoint 子集跑通（证据须含 `engine=ibex`、`ibex_executed=L0,L9,L10,L19,L20,L29,L30,L34,L35`、`chain_restart=true`、`chain_restart_state_source=ibex_dram`，checkpoint 层按阶梯达标）；per-layer 分析报告含证据来源列、cycle 引擎标注与 `ibex_uncovered_layers`（27 层）清单（证据 task-12/13/14）。全量 Ibex 36 层推迟到 FPGA 阶段，其前置条件见 Deferred to next phase。
- C4: FM-3 weight-streaming overlap RTL 实测完成，RTL vs Func Model overlap ratio delta≤0.05，校准报告产出（证据 task-15/16/17）。
- C5a: SFU wrapper 3 个输出 mismatch 修复后 `sfu_wrapper functional: 5/5 PASS`（`test_bug005_sfu_nonaligned_xprop` by-design FAIL，sparse-TB only，不计入）；DRAM 8MB 窗口约束后 FM-SOC 33/33 仍 PASS（证据 task-18/19）。
- C5b: MMIO spec 文档缺口处置完成；Q8_0 下载重试结果明确（SUCCESS 或 BLOCKED-NETWORK）；bug 台账无重复（证据 task-20/21/22）。
- 全回归：pytest ≥732、FM-SOC 33/33、MXU 9/9、SFU 319/319、Vector 63/63、wrapper 15（含 by-design 排除）无新增 FAIL。
- F1-F4 全 APPROVE。
