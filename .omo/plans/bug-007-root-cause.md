# bug-007-root-cause - Work Plan

## TL;DR (For humans)
<!-- Plain English for a non-engineer: NO file paths, NO todo numbers, NO wave/agent/tool names. -->

**What you'll get:** 一个遗留缺陷的根因结论与责任归属——三条 attention 指令在三层推理跑批中从未执行（BUG-RTL-SOC-007），到底是硬件、固件、测试驱动还是测试数据的问题——以及缺陷台账的正式处置。

**Why this approach:** 先用代码考古加今日环境重跑，确定当年失败究竟发生在哪种执行模式下（固件在环还是测试直驱），并承认 2026-07-07 的原始证据已被删除，今日 HEAD 不等于当年环境；再用"同一套固件和用例分别跑参考模型与真实硬件"的对照实验切出责任方向，但把"RTL 侧"严格收窄为"RTL ∪ 测试驱动/分配器/数据"，避免误判；之后按结果走假设验证或历史修复归因，避免盲目抓波形。

**What it will NOT do:** 不修复另一个相关的 attention 数据缺陷（只记录关联线索）；不改动冻结规格与第三方 IP；不代替你签收任何"偶发/环境性"定级；不为日常办公新建工作树；不声称"FuncModel PASS 就能证明 RTL 无罪"（参考模型只能覆盖逻辑语义层，驱动时序、状态生命周期、写几何等盲区不可见）。

**Effort:** Medium-Large（考古 + 2-3 次 HEAD EDA 复跑 + 最多 3 次历史 worktree 重编译复跑 + 条件触发的波形/假设实验）
**Risk:** Medium-High —— 原始失败证据已被删除，且当年环境与 HEAD 之间存在 CPU 模型切换（Spike→Ibex）、测试驱动流式化、固件启动序列化等重大漂移；最坏情况只能归因到"某批历史修复/基础设施顺带解决"的粒度，此时走环境性/重建失败定级并需你亲自接受
**Decisions to sanity-check:** (1) 复跑若全通过，能否接受"归因到修复族或基础设施漂移、无法定位单个 commit"的结论；(2) 若走环境性/重建失败定级，处置措辞需你确认；(3) 真实形状测试若暴露新的活缺陷，是否允许本计划内做最小修复（默认：允许且独立验收）

Your next move: `/start-work` 启动执行，或先跑一轮高精度评审。Full execution detail follows below.

---

> TL;DR (machine): Medium-Large effort, Medium-High risk — BUG-007 root-cause investigation with deleted-evidence caveats: mode archaeology + dual-mode HEAD re-run + H0 FuncModel-vs-RTL differential using existing `sim/verification` adapter if possible + falsifiable hypotheses (H1-H4 plus residual driver/status-lifecycle candidates) + bounded flip-commit archaeology across RTL/firmware/testcase-infra fix families + ledger disposition per Blocker-6.

## Scope
### Must have
1. **MODE-ORIG 裁定**：2026-07-07 原始 W1.3 跑批 51 ops，45 PASS / 6 FAIL（`docs/vector-workaround-3layer-issue.md:40-61`），失败到底跑在哪种模式（per-op preload：Python MMIO 直驱、固件 resident 但可能不调度 | firmware-ring chain：固件在环调度）——git 考古证据支撑。
2. **双模式 HEAD 复跑实测**：`run_qwen25_3b_3layer`（per-op 3-layer）与 `ATTN-WEIGHT-CHAIN`（固件链式 17-op 单层）在今日 HEAD 的 per-op cycles 表（op07/24/41 高亮）。
3. **H0 差分对照**（或显式降级/收窄记录）：优先复用现有 `sim/verification/` 框架；同固件同 case 打 FuncModel vs RTL 的 2×2 归属矩阵，**不**把 "FM-PASS+RTL-FAIL" 直接等同于 RTL 有罪。
4. **四假设可证伪实验**（H1 ring/描述符/固件驻留干扰、H2 地址表、H3 START/status-lifecycle 波形、H4 blk0 tiny-K+N=128 形状）——条件触发，任何 skip 必须带 citation（no_silent_skip）。
5. **真实形状覆盖关闭**：区分 3-layer 流式 manifest（op07 M=16/K=16/N=128/tiles=2，MODE-A 已通过 `_run_streamed_mmul` 覆盖）与 blk0 chain manifest（op07 M=32/K=2/N=128 被 clip 成 N=64）；补测 blk0 未 clip 单 op（tiny-K + multi-N 组合）并记录 chain 内支持性结论。
6. **归属判定 + 台账处置**：一句话结论（FuncModel | RTL | 固件 | 测试 case/生成器/驱动 | 环境/重建失败，可组合）+ BUG-007 ledger 处置 + Blocker-6 回填。
7. **全程 provenance 绑定**：每份 evidence 含 git HEAD、firmware hex sha256、向量 manifest hash（生成前+生成后）、simv 标识、VCS 版本、生成器 commit。

### Must NOT have (guardrails, anti-slop, scope boundaries)
1. **不改冻结面**：`config/func_model_perf_*`、`sim/arc_model.py`、`sim/quantize.py`、vendored IP（`rtl/cpu/ibex/`、`rtl/ip/verilog-*/`、`llama_ref/`、`spike_src/`、`software/executorch/`）、`gen/`（只经 `spec/npu_abi.json` 重生成）。
2. **rtl/ 与 firmware/ 产品代码默认只读**；仅 todo 8 处置分支 (a) 证实活缺陷时允许最小修复（gated、独立验收）。
3. **sim/ 测试基建允许新增 case/driver**；每个有界任务独立受 cap：todo 2 与 todo 6 各自 instrument cap = 1 worker session / 4 wall-clock hours / ≤3 个新增文件——任一先到即降级。
4. **BUG-012 不并案**：不改 op05 相关源码、不改 BUG-012 ledger 状态，仅 cross-ref 注记。
5. **不建主工作 worktree**（用户规则）；历史 commit 复跑仅限 /tmp 只读临时 worktree（先例：soc-rtl-review-remediation todo-14 A/B），≤3 次。
6. **no_silent_skip**：所有条件跳过必须显式记录原因与 citation；禁止 DRY-RUN/DEFERRED/旧 evidence 冒充新跑。
7. **VCS 仅 sz0001**；`soc-verification-run.sh` 只能透传单一 make target，**不能**透传 `CASE_ID=...` 等 make 变量。多变量目标必须直接在 sz0001 执行 `make -C sim/regression ...`。
8. **处置 (b) 的用户接受不可代签**；并行会话 7 个 dirty 文件（`.omo/evidence/task-0-signoff-v3-runner.txt`、`task-20-uncertainty-kpis.json`、`task-23-perf-spec-ci.txt`、`.omo/notepads/fm-e2e-qwen-cv-software-stack/learnings.md`、`phase6-rtl-verification/learnings.md`、`build/evidence/fm-cv-chain.txt`、`w3-4-mobilenetv3-fm.txt`）out-of-scope 不动不提交；`.omo/plans/bug-007-root-cause.md` 是本计划新增产物。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: **tests-after**（调查型计划：每个实验/新增 focused case 即测试；负向验收=复现本身，不预写 RED 测试）。框架：既有 cocotb 回归（`sim/regression/Makefile`）+ FSDB 波形（verdi-fsdb skill）+ git 考古（log/show/-S）。
- 每份 evidence 必须含：**provenance 块**（git HEAD / `sha256sum firmware/build/npu_firmware.hex` / 向量 manifest hash（生成前+后） / simv 标识 / VCS 版本 / 生成器 commit）+ **grep-able 判定行**：`MODE-ORIG:` / `PRIMARY-VERDICT:` / `MODE-A:` / `MODE-B:` / `NEW-DEFECT-NON-CORRESPONDING-MODE:` / `H0-INSTRUMENT:` / `H0-MATRIX:` / `H1-VERDICT:` / `H2-VERDICT:` / `H3-VERDICT:` / `H4-N128-BLK0-SINGLE:` / `H4-N128-CHAIN:` / `FLIP:` / `ATTRIBUTION:`。
- Evidence: `.omo/evidence/task-<N>-bug-007-root-cause.txt`（随 todo commit 入库）。

## Execution strategy
### Parallel execution waves
- **Wave 1（基线+复现）**：todo 0 → todo 1；todo 2 的 instrument 审计部分可与 todo 1 并行（执行依赖 todo 1 的 MODE-ORIG）。
- **Wave 2（假设验证，条件触发）**：todo 1 产出两个独立 verdict（MODE-A、MODE-B）和 `MODE-ORIG`；以 **MODE-ORIG 对应模式** 的 verdict 为主 verdict。主 verdict REPRO-FAIL → todo 3/4/5；主 verdict REPRO-CLEAR → todo 7。若 `MODE-ORIG == unknown`，任一模式 REPRO-FAIL 即跑 todo 3-5；双 CLEAR 则跑 todo 7（以 MODE-A 为主考古入口）。非对应模式的失败作为 `NEW-DEFECT-NON-CORRESPONDING-MODE` 记录，不混入 BUG-007 归因（TL;DR decision #3：用户决定是否单独最小修复）。todo 6 **无条件**（Wave 1 后期即可并行启动 focused case 构建），但 todo 8 对其为软依赖：H4 构造失败降级为 `H4-N128-BLK0-SINGLE: construction-failed` 并流入 RESIDUAL-CANDIDATES，不阻塞归因。todo 7 的历史 simv 编译可与 Wave 2 其他工作并行预 stage。
- **Wave 3（处置）**：todo 8 → todo 9。
- **终审波**：F1-F4 并行，全 APPROVE + 用户 explicit okay。

### 主 verdict 定义
- `MODE-ORIG ∈ {per-op-preload, firmware-ring-chain}`：主 verdict = 对应模式的 verdict（REPRO-FAIL / REPRO-CLEAR）。
- `MODE-ORIG == unknown`：原始执行模式无法从 git 证据中恢复。此时主 verdict 采用保守规则：
  - **REPRO-FAIL** 当且仅当 MODE-A 或 MODE-B 任一 FAIL；
  - **REPRO-CLEAR** 当且仅当 MODE-A 与 MODE-B 双 CLEAR。
  H1-H4 以实际 FAIL 的模式为场景；若双 FAIL，优先以 MODE-A 为主场景并完整记录 MODE-B 证据。

### Mixed-mode routing table（Wave-2 入口）
| MODE-ORIG | 对应模式 | 主 verdict REPRO-FAIL | 主 verdict REPRO-CLEAR | 非对应模式 FAIL |
| --- | --- | --- | --- | --- |
| per-op-preload | MODE-A | 跑 todo 3/4/5（以 MODE-A 为失败场景） | 跑 todo 7（以 MODE-A 为考古入口） | 记为 NEW-DEFECT-NON-CORRESPONDING-MODE；不阻塞 BUG-007 路径 |
| firmware-ring-chain | MODE-B | 跑 todo 3/4/5（以 MODE-B 为失败场景） | 跑 todo 7（以 MODE-B 为考古入口） | 同上 |
| unknown | n/a | 任一模式 FAIL 即跑 todo 3/4/5（优先以 FAIL 模式为场景；双 FAIL 时优先 MODE-A） | 双 CLEAR 才跑 todo 7 | n/a（无主/非对应之分） |

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 0 | — | 1,2,6 | — |
| 1 | 0 | 2(执行),3,4,5,7,8 | 2(审计), 6(构建) |
| 2 | 0(审计)/1(执行) | 8 | 1(审计), 3,4,5,6,7 |
| 3 | 1(主 verdict REPRO-FAIL) | 8 | 4,5,6 |
| 4 | 1(主 verdict REPRO-FAIL) | 8 | 3,5,6 |
| 5 | 1(主 verdict REPRO-FAIL) | 8 | 3,4,6 |
| 6 | 0 | 8（软：construction-failed 仍可流入 8） | 1,2,3,4,5,7 |
| 7 | 1(主 verdict REPRO-CLEAR) | 8 | 2,6 |
| 8 | 2,3,4,5,7 + 6 的结果/evidence | 9, F1-F4 | — |
| 9 | 8 | F1-F4 | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 0. P0 工作区 + 产物新鲜度基线（分支、固件 hex、向量、provenance）
  What to do / Must NOT do: (1) `git checkout -b bug-007-root-cause main`（当前目录，禁止新建 worktree）；porcelain 快照确认仅含 7 个已知并行会话 dirty 文件（见 Scope #8），不动不提交；本计划文件 `.omo/plans/bug-007-root-cause.md` 为新增产物。(2) `make -C firmware` 重建固件 hex，`sha256sum firmware/build/npu_firmware.hex` 记录（8 周 ABI/opcode 演进后旧 hex 会污染复现基线）。(3) **记录提交态 manifest**：记录当前 `rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/manifest.json` 的 sha256 与 op07/24/41 dimensions（应为 M=16/K=16/N=128/tiles=2）。(4) **生成器漂移探测**：运行 `python3 scripts/gen_qwen25_3b_rtl_vectors.py`（写 `rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/`），记录生成后 manifest hash 并与提交态比较；生成器 commit 固定为 `b6b0a89`（2026-07-08，自该日后无修改），记录该事实。**决策规则**：若生成后 hash 与提交态不同，立即停止复跑准备，恢复提交态向量（`git checkout -- rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/manifest.json` 及关联 hex），把差异记录为 `GENERATOR-DRIFT: yes` 并纳入 todo 4/H2 审计；若相同则保留生成结果（等价于提交态）。(5) provenance 基线块：git HEAD、生成器 commit、提交态与生成后 manifest hash、simv 标识（存在则记 mtime/sha）、VCS 版本（sz0001 `vcs -ID`）。Must NOT：不 stash/不 commit 并行文件；不碰冻结面与 gen/；不在复跑前用漂移后的向量覆盖提交态。
  Parallelization: Wave 1 | Blocked by: none | Blocks: 1,2,6
  References (executor has NO interview context - be exhaustive): `sim/regression/Makefile:434-470`（run_qwen25_3b_3layer 目标）；`scripts/gen_qwen25_3b_rtl_vectors.py`（向量生成器，commit b6b0a89 后无修改）；`scripts/run_qwen25_3b_rtl.py:38-41`（缺失时自动重生成逻辑）；`firmware/Makefile`；AGENTS.md NOTES（opcode 演进：SFU 0x01+desc.sfu_op、ROPE 0x05、Vector 0x0F-0x14——旧产物 staleness 风险来源）
  Acceptance criteria (agent-executable): `git branch --show-current` == `bug-007-root-cause`；evidence 含 provenance 块（`grep -c "sha256" .omo/evidence/task-0-bug-007-root-cause.txt` ≥ 1 且含 git HEAD 行）；`git status --porcelain` 相对 main 仅多本计划产物与 Scope #8 列出的 7 个已知并行会话 dirty 文件，且这些 dirty 文件未被修改/提交；生成前后 manifest hash 均落档。
  QA scenarios (name the exact tool + invocation): happy=四项基线齐全落档；failure=固件重建或向量生成失败 → 根因记录（生成器 8 周漂移？）并 STOP 上报，不得静默跳过。Evidence `.omo/evidence/task-0-bug-007-root-cause.txt`
  Commit: Y | chore(bug007): P0 baseline — branch + fresh firmware hex/vectors + provenance

- [x] 1. W1.3 原始模式考古 + 双模式 HEAD 复跑（复现判定）
  What to do / Must NOT do: (a) **模式考古**：`git log --oneline -- sim/cocotb_bridge.py` + `git log -S "w1-3-rtl-op-summary" --all --oneline` 定位 2026-07-07 前后生成 per-op summary 的代码与当时 `test_qwen25_3b_3layer` 形态；用 `git show <sha>:sim/cocotb_bridge.py` 只读查看（不切分支）；裁定 `MODE-ORIG ∈ {per-op-preload（Python MMIO 直驱，固件 resident/idle）| firmware-ring-chain（固件在环调度）| unknown}`。同时记录关键环境漂移：`09f753e2`（2026-07-06）把 SoC TB CPU 从 Spike 换成 Ibex RTL，失败发生在切换后一天；`79654175`（2026-07-08）是首个提交的三层 testcase，因此 07-07 的 testcase 可能是未提交祖先——必须在 evidence 中标记此重建不确定性。(b) **生成器 provenance 考古**：`git log --oneline -- scripts/gen_qwen25_3b_rtl_vectors.py` 自 2026-07-01 起；验证生成器 commit `b6b0a89`（07-08）之前是否有未提交修改（结论通常只能是"不可验证"）；记录结论。(c) **双模式复跑**（sz0001；向量必须是 todo 0 恢复后的提交态/等价态）：模式 A 执行 `make -C sim/regression run_qwen25_3b_3layer`（per-op preload 3-layer——`cocotb_bridge.py:5261` docstring 明示每 op 由 hex 预载、Python `bridge.run_step` 驱动，固件加载后 idle，**不得称为链式**）；模式 B 直接在 sz0001 执行 `make -C sim/regression run_fm_soc_case CASE_ID=ATTN-WEIGHT-CHAIN`（固件 ring 链式 17-op 单层，IbexRunner 路由 `rtl_soc_runner.py:1081-1088`；`soc-verification-run.sh` 不能透传 `CASE_ID`，必须直接 make；确切命令先例=`build/evidence/task-15-soc-rtl-verification-signoff.txt:13-16`）。(d) 提取 per-op cycles 表（A=51 op / B=26 命令），attn_weight 行高亮；A 可另跑 `make -C sim/regression run_w17_intermediate_compare`（W1.7）取逐层 cos。(e) 按 **Execution strategy / Mixed-mode routing table** 裁定主 verdict 与 Wave-2 入口；非对应模式若 FAIL，记录为 `NEW-DEFECT-NON-CORRESPONDING-MODE` 并由用户在终审时决定是否单独跟踪。Must NOT：DRY-RUN/旧 evidence 冒充；把模式 A 称为链式；用 wrapper 传 make 变量。
  Parallelization: Wave 1 | Blocked by: 0 | Blocks: 2(执行),3,4,5,7,8
  References (executor has NO interview context - be exhaustive): `docs/bugs/bugs-soc-rtl.md:328-367`（BUG-007 条目：op07/24/41 cycles=0、三假设、ABI-1024 排除）；`docs/vector-workaround-3layer-issue.md:40-61`（51-op 结果分布：45 PASS / 6 FAIL + §2.3）；`.omo/plans/soc-verification-gaps-phase5.md:186-193`（per-op 模式关闭、true chain deferred）；`sim/cocotb_bridge.py:5261-5283`（模式 A per-op preload + load_firmware 事实）；`sim/cocotb_bridge.py:1912-2055`（`_run_streamed_mmul` N-tile 流式 + attn_weight probe block）；`sim/regression/Makefile:437-470`（两目标定义）；`build/evidence/task-15-soc-rtl-verification-signoff.txt`（模式 B 全程先例）
  Acceptance criteria (agent-executable): evidence 含判定行：`MODE-ORIG: <per-op-preload|firmware-ring-chain|unknown>`（附 git 证据 sha 引用）、`PRIMARY-VERDICT: <REPRO-FAIL|REPRO-CLEAR>`（按 Execution strategy 主 verdict 定义）、`MODE-A(run_qwen25_3b_3layer): REPRO-FAIL|REPRO-CLEAR`（含 op07/24/41 实测 cycles）、`MODE-B(ATTN-WEIGHT-CHAIN): REPRO-FAIL|REPRO-CLEAR`、`NEW-DEFECT-NON-CORRESPONDING-MODE: <none|mode-B-FAIL|mode-A-FAIL|both>`（仅已知 MODE-ORIG 时适用；unknown 无主/非对应之分，此处填 n/a）、`RECONSTRUCTION-UNCERTAINTY: <low|medium|high>`（基于 09f753e2/79654175/b6b0a89 日期与证据删除事实）。
  QA scenarios: happy=两模式均产出实测 cycles 表；failure=某模式无法运行（testcase/生成器漂移）→ 记录根因 STOP 上报（no_silent_skip）。Evidence `.omo/evidence/task-1-bug-007-root-cause.txt`
  Commit: Y | test(bug007): W1.3 mode archaeology + dual-mode HEAD re-run

- [x] 2. H0 差分对照（FuncModel vs RTL，同固件同 case）+ instrument 审计
  What to do / Must NOT do: (a) **instrument 审计**：优先检查现有 `sim/verification/fm_adapter.py` + `dut_adapter.py` + `scenario.py` 框架：其 `FuncModelAdapter` 已支持 `python`（miniv.NPUFirmware）和 `spike`（真实 firmware 编译为 `npu_firmware_spike.elf`）两种模式，具备共享 DUTAdapter 契约与 backdoor 观测 tagging。判定其能否直接 dispatch 含 attn_weight 的 17-op 描述符流（通过 `mmio_bridge._run_mxu_compute`），并在 evidence 中记录 `H0-INSTRUMENT: fm_adapter-python|fm_adapter-spike|built(bounded)|missing-degraded`。(b) 若复用框架不可行，则**有界新建**（扩展 spike_host chain 模式或新增 verification scenario），effort cap = 1 worker session **且** 4 wall-clock hours **且** ≤3 个新增文件——任一先到即降级 `H0-INSTRUMENT: missing-degraded` 并显式记录边界（op 级 golden 一致性已被现有证据覆盖：PERF-13 cos=1.0、ATTN-WEIGHT-CHAIN cos=1.0）。(c) 若 `MODE-ORIG == per-op-preload`：H0 的固件调度判别力不适用，但**固件驻留干扰**仍可通过"加载固件 vs halt-only stub" cheap differential 部分判别；显式记录 `H0-MATRIX: n/a-firmware-not-in-loop` 并收窄为 manifest/golden + 驱动层审计。(d) 跑得动则执行差分并填 2×2，**路由规则（全单元格）**：REPRO-FAIL+FM-PASS→**narrowed-to {RTL ∪ testcase/allocator/driver}**（进入 H1-H4 + 残差候选）；REPRO-FAIL+FM-FAIL→先隔离固件/case/生成器缺陷再回 RTL 复跑；REPRO-CLEAR+FM-PASS→todo 7 考古（若主 verdict CLEAR）；REPRO-CLEAR+FM-FAIL→STOP 上报 testcase/vector 漂移，**不得声称 BUG-007 cleared**；若 H1-H4 全部 refute，在 `H0-MATRIX` 中显式记录残差候选（cocotb-driver back-to-back write timing、STATUS.DONE lifecycle stale-DONE）并带入 todo 8 ATTRIBUTION。**注意**：这里的 REPRO-FAIL/REPRO-CLEAR 指主 verdict（对应模式），非对应模式失败按 Mixed-mode routing table 处理。Must NOT：H0 循环论证（golden 本身产自 FuncModel——H0 只判别固件/dispatch/驱动层，不重验数值语义）；仪器超限无限扩建。
  Parallelization: Wave 1 | Blocked by: 0(审计)/1(执行) | Blocks: 8
  References (executor has NO interview context - be exhaustive): `sim/verification/fm_adapter.py` + `dut_adapter.py` + `scenario.py`（现有 FuncModelAdapter/python+spike 模式）；`sim/spike_host.py`（chain 模式 + `DESC_BASE=0x80010000` + attention 在 numpy）；`sim/miniv.py:566`（DEPRECATED 标记）；`sim/mmio_bridge.py`（FuncModel MMIO handler，`_run_mxu_compute`）；`sim/qwen25_forward.py`（纯 Python 证据：只 import numpy+q4_dequant）；`build/evidence/w4-perf-p3.txt` + `task-15 evidence`（op 级 golden 一致性既有证据）
  Acceptance criteria (agent-executable): evidence 含 `H0-INSTRUMENT: <mode>` + 审计结论 + `H0-MATRIX: <FM-PASS|FM-FAIL|n/a-firmware-not-in-loop> × <REPO verdict>` 单元格判定、路由走向、残差候选清单。
  QA scenarios: happy=差分跑出 FM 侧 per-op 结果并落 2×2；failure=仪器缺口触发降级路径且被显式记录（非静默）。Evidence `.omo/evidence/task-2-bug-007-root-cause.txt`
  Commit: Y | test(bug007): H0 differential FuncModel-vs-RTL (instrument audited)

- [x] 3. [SKIPPED-INAPPLICABLE — PRIMARY-VERDICT=REPRO-CLEAR, firmware not in loop] H1 ring/描述符完整性 + 固件驻留干扰 cheap differential + 环大小考古
  What to do / Must NOT do: (a) backdoor dump 命令 ring（0x80000000 区）+ descriptor 区（`spike_host.py DESC_BASE=0x80010000`）在每层执行前后；**证伪不变量**：与 staged cmds/描述符逐字节一致；任何错位/覆写 → `H1-VERDICT: confirmed`；全一致 → refuted。(b) **固件驻留干扰 cheap differential**（尤其 MODE-A per-op preload 中固件加载后 idle）：在 sz0001 跑 MODE-A 一次用正常 firmware hex，一次用临时 halt-only BOOTROM stub（在 `/tmp/bug007-haltstub/` 新建一个仅 `wfi` 或 `j .` 的 RISC-V 程序并用 firmware 工具链编译为 hex，**不得修改 firmware/ 产品源码**），比较 op07 cycles；若 stub 下通过而正常固件下失败 → `H1-FIRMWARE-RESIDENT: confirmed`；无明显差异 → `H1-FIRMWARE-RESIDENT: refuted`；若无法构造 stub，改为 backdoor dump DRAM ring 区 + Python-staged SRAM 在固件 boot 前后是否被改写。(c) **环大小考古**：`git log -S "NPU_RING_ENTRIES" -- gen/npu_abi.h`（追溯 ABI header 层面的常量变更）+ `git show <07-07-sha>:firmware/npu_firmware.c | grep -n "RING_ENTRIES"` 确认 2026-07-07 时点固件视角的环大小。  注意：`spec/npu_abi.json` 与 `gen/npu_abi.h` 中的 `ring_entries`/`NPU_RING_ENTRIES` 直到 2026-07-28 的 `b0096d0c` 才首次入库，因此**不能作为 07-07 之前 commit 的引用来源**；对最接近可 rerun 点 `79654175` 的 `firmware/npu_firmware.c` 验证已得 `#define RING_ENTRIES 1024`，故 ring-overflow 假设仍被排除，仅需把台账原引用 `gen/npu_abi.h:299` 替换为 `firmware/npu_firmware.c:<line>`。若目标 commit 无 `firmware/npu_firmware.c`，则记 `H1-RING-SIZE: n/a-no-source` 并引用本考古说明。**不再引用 P0SpikeRunner RING_SIZE=32**（那只是 Python runner 的 staging 常量，ATTN-WEIGHT-CHAIN 实际用 IbexRunner）。**Skip rule（显式）**：若主 verdict 的 REPRO-FAIL 模式中固件既不在调度环也不在驻留路径 → 标记 [x] SKIPPED-INAPPLICABLE 并引用 todo 1 证据（不算 silent skip）。
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 8
  References (executor has NO interview context - be exhaustive): `gen/npu_abi.h:299`（RING_ENTRIES=1024）；`spec/npu_abi.json`（ring_entries 字段）；`sim/rtl_soc_runner.py:1081-1088`（IbexRunner chain 路由，非 P0SpikeRunner）；`sim/spike_host.py:223`（DESC_BASE 常量）；`docs/bugs/bugs-soc-rtl.md:601-648`（BUG-008 ring/描述符重叠先例——dump 方法论可借鉴）
  Acceptance criteria (agent-executable): evidence 含 `H1-VERDICT: confirmed|refuted|skipped-inapplicable` + `H1-FIRMWARE-RESIDENT: confirmed|refuted|n/a` + dump 对照表（或 skip citation 行）。
  QA scenarios: happy=dump 完整可比对；failure=backdoor 读不通 → 记录并改用波形侧观察 ring 写入。Evidence `.omo/evidence/task-3-bug-007-root-cause.txt`
  Commit: Y | test(bug007): H1 ring/descriptor integrity + firmware-residency diff + ring-size archaeology

- [x] 4. [SKIPPED-INAPPLICABLE — PRIMARY-VERDICT=REPRO-CLEAR] H2 地址表审计（两套分配器分别审计）+ 生成器漂移检查
  What to do / Must NOT do: (a) per-op 模式：审计**生成器**分配——读 `scripts/gen_qwen25_3b_rtl_vectors.py` 地址分配逻辑，重算 51-op 地址表（SRAM/DRAM）。(b) chain 模式：审计 `sim/rtl_soc_runner.py` `_build_block`/`_build_attn_weight_chain`（:3696-3775）运行时分配。(c) **证伪三查**：①任意两 buffer 重叠；②越 `[SRAM_BASE, +4MB)` 或 `[DRAM_BASE, +8MB)`（WVR-SOC-RTL-002 窗口）；③任一后续消费的输入区落在前序 op 512B-chunk 写污染半径内（BUG-005 类几何）→ 任一命中 = `H2-VERDICT: confirmed`（附命中明细），全不命中 = refuted。(d) **生成器漂移检查**：用 `git log -p --since=2026-07-01 -- scripts/gen_qwen25_3b_rtl_vectors.py` 确认分配逻辑自 07-07 以来未变；若发现变化，记录为 `H2-GENERATOR-DRIFT` 并纳入 ATTRIBUTION。Must NOT：不改分配器（调查只审计；修复归 todo 8 处置分支）。
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 8
  References (executor has NO interview context - be exhaustive): `scripts/gen_qwen25_3b_rtl_vectors.py`（生成器分配逻辑，commit b6b0a89）；`sim/rtl_soc_runner.py:3696-3775`（chain 分配）；`docs/vector-workaround-3layer-issue.md:49-53`（dense 分配暴露 BUG-005 的先例）；`docs/waivers/WVR-SOC-RTL-002.md`（8MB 窗口约束）；`rtl/wrapper/vector_soc_wrapper.v`（512B chunk 写几何历史）
  Acceptance criteria (agent-executable): evidence 含 51-op 地址表（模式对应）+ 三查逐项结论 + `H2-VERDICT: confirmed|refuted`（命中时列重叠/越界/污染半径明细）+ `H2-GENERATOR-DRIFT: yes|no`。
  QA scenarios: happy=地址表完整可静态复核；failure=分配逻辑不可静态重算 → 改运行时 dump 实际地址后比对（记录方法切换）。Evidence `.omo/evidence/task-4-bug-007-root-cause.txt`
  Commit: Y | test(bug007): H2 address-table audit + generator drift check

- [x] 5. [SKIPPED-INAPPLICABLE — PRIMARY-VERDICT=REPRO-CLEAR] H3 MXU wrapper START/status-lifecycle 波形（BUG-006 类比 + stale-DONE 残差）
  What to do / Must NOT do: **前置条件**：需一次独立的 FSDB-enabled simv 编译——`tb_soc.v:543` 将 FSDB dump  gate 在 `` `ifdef FSDB ``，标准回归目标未定义该宏，因此 H3 前必须用 `+define+FSDB` 重新编译 simv_soc_cocotb（或等效 Makefile 变体），记录该 simv 的 sha/mtime 进 provenance。sz0001 上用此 FSDB-enabled simv 跑失败场景并抓**失败 op 的 dispatch 窗口**：APB `CMD.START` 写（per-op 模式写序见 `cocotb_bridge.py:1650 run_step`）、`rtl/wrapper/mxu_soc_wrapper.v` 的 start/hold/gating 信号、**`STATUS.DONE` 在 START 之前的值**、`STATUS.BUSY` 置位/保持、DONE/completion stamp。**契约裁定**：`spec/npu_abi.json` 的 START 语义 = "start computation"（无软件等待前置义务）→ 有效配置后的 START 脉冲被吞、BUSY 从未置位 = `H3-VERDICT: confirmed-rtl`；若 START 正常到达但 DONE 在 START 前已为高且硬件未清零 → `H3-VERDICT: confirmed-status-lifecycle`；若契约为软件需等待 ready 则 `confirmed-firmware-driver`；波形显示 START 正常到达、DONE 在 START 前为低、BUSY 置位但后续异常 → `H3-VERDICT: refuted`（转 H2/H4/残差）。**关键**：把台账中"STATUS.BUSY was never asserted"作为 `to-verify` 项在 evidence 中记录，而非默认前提。Must NOT：不改 rtl/（调查只抓波形；修复归 todo 8）；不在本地跑 VCS。
  Parallelization: Wave 2 | Blocked by: 1 | Blocks: 8
  References (executor has NO interview context - be exhaustive): `rtl/wrapper/mxu_soc_wrapper.v`（MMIO 门控/prefetch——先读代码确定信号名）；`docs/bugs/bugs-soc-rtl.md:220-263`（BUG-006 start_hold 吞 START + npu_wait_done 立即返回的**同构签名**先例）；`spec/npu_abi.json`（START/BUSY/DONE 契约文本，STATUS 为 RO）；`sim/cocotb_bridge.py:1650`（run_step：MMIO config → CMD.START → poll DONE）
  Acceptance criteria (agent-executable): evidence 含信号时序摘录（FSDB 信号名+周期数）+ `H3-VERDICT: confirmed-rtl|confirmed-status-lifecycle|confirmed-firmware-driver|refuted`（附 `spec/npu_abi.json` 契约条款引用）。
  QA scenarios: happy=波形窗口完整覆盖 START 前 DONE → START → BUSY → DONE；failure=wrapper 内部信号不可观测 → 用边界信号+BUSY/DONE 计数旁证并记录。Evidence `.omo/evidence/task-5-bug-007-root-cause.txt`
  Commit: Y | test(bug007): H3 START/status-lifecycle waveform + ABI contract attribution

- [x] 6. H4 blk0 tiny-K + N=128 multi-tile 补测（强制——skip 条件不可达）
  What to do / Must NOT do: 区分两个 manifest：**3-layer manifest** op07 = M=16/K=16/N=128/tiles=2，已通过 MODE-A `_run_streamed_mmul` 流式覆盖（cocotb_bridge.py:1959 N-tile loop + :1987-2025 attn_weight probe block），因此不是未覆盖缺口；**blk0 chain manifest** op07 = M=32/K=2/N=128 被 `_build_block` clip 成 N=64（单 tile，`task-15 evidence:64-68`），这是真正未以未 clip 形态跑过的 tiny-K + multi-N 组合。(a) 新增 focused case 在 RTL 跑 blk0 未 clip 的 M=32/K=2/N=128 单 op；判定 cycles>0 + cos 达标。记录为 `H4-N128-BLK0-SINGLE: PASS|FAIL`。(b) chain 内 N=128：若 `_build_block` 支持则跑，否则记录 `H4-N128-CHAIN: unsupported-documented`（clipping 为当前唯一支持路径 = 正式覆盖缺口记录）。(c) op05（BUG-012, N=2 tiles=2）**交叉观察仅记录**。(d) 若 focused case 构造遇阻，记录为 `H4-N128-BLK0-SINGLE: construction-failed`（附障碍详情），该结果作为 **RESIDUAL-CANDIDATES** 流入 todo 8，**不 STOP 整个计划**。Must NOT：不 clip 形状冒充覆盖；不改 op05 源码、不改 BUG-012 ledger 状态（仅 cross-ref 注记，落在 todo 9）。
  Parallelization: Wave 2 | Blocked by: 0（focused case 构建可与 todo 1 并行启动）| Blocks: 8
  References (executor has NO interview context - be exhaustive): `build/evidence/task-15-soc-rtl-verification-signoff.txt:60-68`（blk0 chain clip 事实+"REAL blk.0 shape"表述：manifest M=32/K=2/N=128）；`rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/manifest.json`（3-layer op07 M=16/K=16/N=128/tiles=2——已覆盖）；`sim/cocotb_bridge.py:1959-2055`（`_run_streamed_mmul` N-tile 流式 + attn_weight probe）；`docs/bugs/bugs-soc-rtl.md:801-843`（BUG-012：op05 N=2 tiles=2 只 drain 第一 tile——multi-tile 共因线索）；`sim/regression/Makefile`（focused target 惯例）
  Acceptance criteria (agent-executable): evidence 含 `H4-N128-BLK0-SINGLE: PASS|FAIL|construction-failed (cycles=, cos=, 或障碍详情)` + `H4-N128-CHAIN: PASS|FAIL|unsupported-documented` + 两个 manifest 的 op07 dimensions 快照；`construction-failed` 时必须附障碍描述并显式记录其流入 `RESIDUAL-CANDIDATES`。
  QA scenarios: happy=blk0 unclipped 形状真实跑出实测结果；failure=focused case 构造遇阻 → 记录为 `H4-N128-BLK0-SINGLE: construction-failed` 并继续流入 todo 8 `RESIDUAL-CANDIDATES`（不 STOP，非 silent skip）。Evidence `.omo/evidence/task-6-bug-007-root-cause.txt`
  Commit: Y | test(bug007): H4 blk0 tiny-K + N=128 multi-tile coverage

- [x] 7. [条件: MODE-ORIG 对应模式在 HEAD 上 REPRO-CLEAR] git 考古归因（翻转 commit 定位）
  What to do / Must NOT do: 在 /tmp 只读临时 worktree 复跑失败场景（≤3 次，硬上限；先例=soc-rtl-review-remediation todo-14 A/B）。**关键约束**：07-07 的 3-layer testcase 未提交（首个提交 `79654175` 在 07-08），因此历史复跑本质上是"重建"而非"精确复现"；每次历史复跑必须在目标 commit 的 worktree 内：①用该 commit 的 testcase；②用该 commit 的生成器重生成向量；③用该 commit 的 firmware 源码重建 hex；④用该 commit 的 RTL 重编译 simv（每个 worktree 独立编译，耗时 tens of minutes，可与 Wave 2 其他工作并行预 stage）。**任何在 79654175 之前的 commit 都没有 3-layer testcase，因此不能作为 rerun 点**——只能做 static diff（归入 `RECONSTRUCTION-UNCERTAINTY`）。
  **预选候选 family**（按优先级；只保留含 ≥79654175 可 rerun commit 的 family）：(i) sim-infra / testcase 漂移族：`79654175`（三层 testcase 首次提交，作为最早 FAIL 锚点）、`14c27c9`（segment-boundary SRAM-clear）、`a8af351`（force-DRAM-preload）、`b5f3b32`（command-ring unification）；(ii) RTL wrapper 修复族：`ef090b13`（BUG-005 wstrb masking + WV-001/007）；(iii) START/status 修复族：BUG-006 start_hold（定位具体修复 commit）；(iv) 固件调度族：`7aec7a3`+`b545b1f`（per-K-block dispatch）；(v) crossbar-fairness fix；(vi) BUG-008 DESC_BASE fix。**CPU 模型切换族 `09f753e2` 不 rerun**，仅 static-diff 记录其对 07-07 环境的意义。
  **默认 point-selection 规则（≤3 点）**：point 1 = `79654175`（最早可 rerun 点，验证 testcase 首次提交时是否已 FAIL）；point 2 = family (i) 的中点/末点（如 `a8af351` 或 `b5f3b32`），测试 testcase/infra 漂移是否带来 CLEAR；point 3 = family (ii) 或 (iii) 的 before/after 对中较近的那个（如 `ef090b13` 之前一个 commit 或 BUG-006 start_hold 修复 commit 之前一个 commit），测试 wrapper/START 修复族。若某点 testcase 不存在或 build 失败，记录 `FLIP-POINT-<sha>: n/a-no-testcase|build-failed` 并消耗 budget。
  **决策规则**：≤3 点无法保证同一 family 内相邻 commit 对，因此本地化粒度为 **family / commit 区间** 而非单个 commit。记录形式：
  - 同一 family 内存在 `FAIL → CLEAR` 区间（point_n FAIL, point_m CLEAR, n<m, 且两点同属一个 family）→ `FLIP: localized-to-family(<family-name>, interval <sha1>..<sha2>)`。
  - 跨 family 的 FAIL→CLEAR（如 point 1 FAIL, point 2 CLEAR 但两点属不同 family）→ `FLIP: localized-to-interval(<sha1>..<sha2>, candidate families: <list>)`；此时不能 claim 单个 family，需在 ATTRIBUTION 中列出候选修复族。
  - ≤3 点全部 FAIL（无 CLEAR 翻转可观察）= `FLIP: no-clear-observed`；在 ATTRIBUTION 中说明无法定位修复区间，通常并入 reconstruction-failed 路径。
  - ≤3 点全部 CLEAR = `FLIP: reconstruction-failed`（→处置 (b) 并带 caveat，**不等同于 environmental**）。
  - families (v) crossbar-fairness 与 (vi) BUG-008 DESC_BASE 优先级低于前四个 family；默认 3 点预算通常无法覆盖，若 point 1/2/3 已提供足够信息则不必强跑，但必须在候选 family 表中注明未覆盖原因。
  Must NOT：主工作区不切历史 commit；历史 worktree 用后即删；不 rerun 79654175 之前的 commit 却声称其 FAIL。
  Parallelization: Wave 2 | Blocked by: 1(REPRO-CLEAR) | Blocks: 8
  References (executor has NO interview context - be exhaustive): `docs/bugs/bugs-soc-rtl.md:186-310`（BUG-005 两代修复 + BUG-006）；`docs/bugs/bugs-soc-rtl.md:421-506`（P9-00A/00B/00D 修复族）；`docs/bugs/bugs-soc-rtl.md:601-648`（BUG-008 DESC_BASE）；`git log --oneline --since=2026-07-01 -- sim/cocotb_bridge.py scripts/gen_qwen25_3b_rtl_vectors.py rtl/soc/caduceus_soc_top.v`（环境漂移族）；`.omo/plans/soc-rtl-review-remediation.md`（todo-14 A/B 临时 worktree 先例）
  Acceptance criteria (agent-executable): evidence 含候选 family 表（sha+一句话作用+优先级）+ 每次 rerun verdict + 重建假设声明 + `FLIP: localized-to-family(<family-name>, interval <sha1>..<sha2>)|localized-to-interval(<sha1>..<sha2>, candidate families: <list>)|no-clear-observed|reconstruction-failed`。
  QA scenarios: happy=某 family 出现 FAIL→CLEAR 翻转；failure=≤3 点全 FAIL → 显式标记 `FLIP: no-clear-observed` 并说明无法定位修复区间；failure=≤3 点全 CLEAR 或 build-failed → 显式标记 reconstruction-failed 并说明缺失的 family。Evidence `.omo/evidence/task-7-bug-007-root-cause.txt`
  Commit: Y | test(bug007): bounded flip-commit archaeology with reconstruction caveats

- [x] 8. 归因结论 + BUG-007 ledger 处置 + Blocker-6 回填
  What to do / Must NOT do: 汇总 todo 1-7 → `ATTRIBUTION:` 一句话归属判定（FuncModel | RTL | 固件 | 测试 case/生成器/驱动 | 环境/重建失败，可组合）+ 证据链。必须显式列出所有**残差候选**（cocotb-driver back-to-back write timing、STATUS.DONE lifecycle、跨引擎状态泄漏、crossbar fairness、重建不确定性），并说明哪些被排除、哪些因证据删除无法排除。按 **Blocker-6 关闭条件**处置（`docs/soc-rtl-review-remediation-blockers.md:66-73` 原文）：(a) 根因定位 + 修复（历史修复族已合入 HEAD 的，修复即该 commit 族；活缺陷仍在的则做**最小修复**）+ **重跑 3-layer forward 全部 51 op cycles>0 且 golden 匹配** → Status=Fixed，Root Cause 字段写明修复族/commit；(b) 环境性/不可归因/重建失败 → 定级 + 复现条件 + 影响面 + 未排除残差清单，**需用户在终审后亲自接受**。更新 `docs/bugs/bugs-soc-rtl.md` BUG-007（Root Cause/Verification 字段，:328-367）+ `docs/soc-rtl-review-remediation-blockers.md` Blocker 6 行（解除证据路径+日期；走 (b) 且用户未接受 → 记 disposition-pending-user）。Must NOT：代用户接受 (b)；证据不足时 claim Fixed。
  Parallelization: Wave 3 | Blocked by: 2,3,4,5,6,7 | Blocks: 9, F1-F4
  References (executor has NO interview context - be exhaustive): `docs/soc-rtl-review-remediation-blockers.md:66-73`（Blocker 6 关闭条件原文）；`docs/bugs/bugs-soc-rtl.md:328-367`（待更新条目）；`reports/CaduceusCore-review-report-2026-08-28.md:218-220`（§4.5 评审要求）
  Acceptance criteria (agent-executable): `grep -E "BUG-RTL-SOC-007.*Status|Status.*(Fixed|disposition-pending-user)" docs/bugs/bugs-soc-rtl.md` 命中；blockers doc Blocker 6 行含解除证据路径；evidence 含 `ATTRIBUTION:` 行 + `RESIDUAL-CANDIDATES:` 行。
  QA scenarios: happy=三分支之一完整落地；failure=证据不足以支撑任一分支 → 显式记录 open-questions 并 STOP 呈报（不硬写结论）。Evidence `.omo/evidence/task-8-bug-007-root-cause.txt`
  Commit: Y | docs(bugs): BUG-RTL-SOC-007 root cause + disposition (Blocker-6)

- [x] 9. 交叉记录 + notepad learnings + 证据汇总表
  What to do / Must NOT do: (a) BUG-012 cross-ref 注记：若 H4 结果指向 multi-tile 共因，仅在 BUG-012 条目（`bugs-soc-rtl.md:801-843`）追加 cross-ref 行——**不改其 Status、不写 Fix**；(b) `.omo/notepads/bug-007-root-cause/learnings.md` 记录方法论收获（模式考古/differential/证伪判据设计/删除证据下的重建不确定性）；(c) 汇总表：模式×假设×结论×证据路径（含全部 SKIPPED 行的 citation 与残差候选）。Must NOT：并案修复 BUG-012。
  Parallelization: Wave 3 | Blocked by: 8 | Blocks: F1-F4
  References (executor has NO interview context - be exhaustive): `docs/bugs/bugs-soc-rtl.md:801-843`（BUG-012 条目——只加注不改状态）；`.omo/notepads/` 惯例（参考 soc-rtl-review-remediation notepad 结构）
  Acceptance criteria (agent-executable): evidence task-9 含汇总表（每个执行过的 todo 一行+结论+路径+残差清单）+ `test -f .omo/notepads/bug-007-root-cause/learnings.md` 且 ≥1 条记录。
  QA scenarios: happy=表覆盖全部 todo 含 skip citation 与残差；failure=发现无 citation 的跳过 → 补齐后才算完成。Evidence `.omo/evidence/task-9-bug-007-root-cause.txt`
  Commit: Y | docs(bug007): cross-findings + learnings + evidence summary table

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit — 10 todos 逐条核对：evidence 存在、判定行满足、commit 已做、全部条件 skip 均有 citation（no_silent_skip 抽查）、残差候选清单非空。
- [x] F2. Code quality review — 本计划新增/修改的 sim/ 测试基建（focused case、H0 instrument）质量 + 越界检查；若 todo 8 走了修复分支则审查 rtl/firmware 改动的最小性。
- [x] F3. Real manual QA — **agent 可执行**：重跑 MODE-A（`make -C sim/regression run_qwen25_3b_3layer`）与 MODE-B（`make -C sim/regression run_fm_soc_case CASE_ID=ATTN-WEIGHT-CHAIN`）各一次，核对 evidence provenance 块齐全（git HEAD/hex sha/manifest pre+post hash/simv/VCS）、判定行与日志逐字一致；**用户门禁仅保留给处置 (b) 的接受**（agent 不代签）。
- [ ] F4. Scope fidelity — Must NOT 清单逐项核对：冻结面零 diff（`git diff main --stat -- config/ sim/arc_model.py sim/quantize.py rtl/cpu/ibex/ rtl/ip/ gen/` 为空）、BUG-012 状态未动、无主工作 worktree、并行 dirty 文件未入库、历史 worktree 已清理。

## Commit strategy
- 一个 todo 一个原子 commit：`type(scope): summary`（本计划 types：test / docs / chore；todo 8 修复分支用 fix）。
- evidence 随对应 todo commit 入库（`.omo/evidence/` 为 tracked）；`.omo/plans/bug-007-root-cause.md` 与 `.omo/drafts/bug-007-root-cause.md` 在最终通过后一并 commit（或按 todo 0 首次 commit 即入库）。
- 历史考古只读操作（`git log/show/-S`）不产生 commit；/tmp 临时 worktree 用后即删，不留痕迹。

## Success criteria
1. `MODE-ORIG:` 裁定落档（git 考古证据引用支撑）——当年失败究竟跑在哪种执行模式；`RECONSTRUCTION-UNCERTAINTY:` 明确标注低/中/高。
2. `ATTRIBUTION:` 一句话归属结论 + 证据链 + `RESIDUAL-CANDIDATES:` 未排除残差清单 —— 对"是 Func Model 还是 RTL/固件/case/驱动问题"的正式回答（用户问题的正式交付物）。
3. BUG-007 ledger 离开 Open：`grep -E "BUG-RTL-SOC-007.*Status|Status.*(Fixed|disposition-pending-user)" docs/bugs/bugs-soc-rtl.md` 命中且值非 Open；Blocker 6 行回填解除证据路径（走 (b) 且用户未接受时记 pending-user）。
4. 真实形状覆盖关闭：`H4-N128-BLK0-SINGLE` 有实测值，`H4-N128-CHAIN` 有结论（PASS/FAIL/unsupported-documented），且两个 manifest 的 op07 dimensions 已记录。
5. F1-F4 全 APPROVE + 用户 explicit okay（处置走 (b) 时含用户对定级的亲自接受）。