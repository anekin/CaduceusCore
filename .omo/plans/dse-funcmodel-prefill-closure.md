# DSE ↔ Func Model Prefill/TTFT 闭环计划

## TL;DR
> Summary:      关闭 Arc DSE 与 Func Model 在 prefill/TTFT 上的循环：验证公式修正后的 prefill 证据正确性，修复 DSE 中 TTFT 未真实建模的问题，建立 DSE TTFT 目标并写入验证规格，最终全量签收并提交。
> Deliverables: DSE 真实 prefill/TTFT 输出；Block 64×64 TTFT 目标值；更新后的 Func Model 性能验证规格与报告；全量 signoff 通过。
> Effort:       M
> Risk:         Medium - DSE trace 固定 batch_m=1 且 CLI 限制 [1,2]，simulate_layer 与 evaluate_config 需最小侵入改造；规格/报告存在公式修正前的陈旧数值需同步刷新。

## Scope
### Must have
- 验证 Func Model prefill 证据在公式修正后仍然自洽、compute-bound 分类成立、与 BlockEngine broadcast 模型一致。
- 修复 `sim/design_space_explorer.py`：`simulate_layer()` 按 `batch_m` 重新生成 trace（不再复用模块级 `_LLM_TRACE`）；CLI `--batch-m` 放宽至任意正整数；新增/暴露 prefill TTFT 输出。
- 运行 Block 64×64 配置在 `batch_m=128` 与 `batch_m=2000` 的 DSE prefill，得到 TTFT 目标值（ms）。
- 在 `.omo/notes/func-model-perf-verification-spec.md` 增加 Gate 1b：DSE TTFT 一致性（Func Model TTFT vs DSE TTFT，PASS 区间 0.5×–2.0×）。
- 刷新 `reports/func-model-perf-verification-report.md` 中公式修正前的陈旧数值，保持与 `.omo/evidence/task-20-uncertainty-kpis.json` 一致。
- TDD：每个实现 todo 先 RED 后 GREEN，再跑 mutation/回归。
- 保留现有 signoff 流程可复现：`scripts/run_func_model_perf_signoff.py run --all-spec` 继续通过。

### Must NOT have (guardrails)
- 不修改 RTL、不跑 VCS、不做 RTL 校准。
- 不重构 DSE 引擎库/面积模型/PPA 结构；TTFT 修复必须最小侵入。
- 不引入新依赖；只使用仓库已有的 Python 标准库和已声明依赖。
- 不扩展 CV/S1 迁移/P1 项到本计划。
- 不接受 stdout "PASS" 或 grep-only 作为验证通过；必须有结构化证据和退出码。

## Verification strategy
- **TDD**: 每个实现 todo 先运行声明的 RED case（确认目标断言失败），再实现并运行 GREEN + mutation。
- **RED command rule**: 每个 T1–T4 的第一个 QA command 在实现前原样执行并预期 nonzero；DoneClaim 记录命令、exit code 和缺失行为。实现后原样重跑并预期 exit 0。
- **QA policy**: 所有验证命令 agent-executed，Python 3.10 基线，不依赖 EDA/网络/secret。
- **Evidence**: `.omo/evidence/task-<N>-<slug>.<ext>`。
- **DoneClaim**: 每项记录 `todo_id, red_command/result, green_command/result, mutation_command/result, head, evidence_path/hash, verdict`。

## Frozen spec-stage decisions
- DSE TTFT PASS 区间：Func Model TTFT ∈ [0.5× DSE_TTFT, 2.0× DSE_TTFT]。
- DSE prefill 模型与 Func Model 使用同一 `BlockEngine.estimate()` 路径（DSE 内部已复用 `engine/mac_engine.py`），因此核心 compute/dma 模型一致；差距来源只能是 trace 结构（7-op layer vs 17-op DAG）和层内并行假设。
- TTFT 定义：`prefill_layer_cycles × num_layers / freq_mhz` [ms]，不包含首 token decode（与 Func Model uncertainty-kpis 中的 `prefill_ms` 对齐）。

## Execution strategy

## TODOs

- [x] 1. 验证并刷新 prefill 证据与报告
  What to do: 读取 `.omo/evidence/task-20-uncertainty-kpis.json` 提取 Qwen2.5-3B prefill-2000 的 `prefill_cycles`、`prefill_ms`、`ttft_ms`；交叉验证 `task-16-qwen-spec-gates.json` 中 prefill-128 的 Path A/B total；修正 `reports/func-model-perf-verification-report.md` 第 8.3 节/结论中的陈旧数值（`60,223 ms` → `63,923 ms`，`TTFT ~60.2 s` → `~63.9 s`）；确认规格中 Gate 3 prefill 端点 compute-bound 描述与当前数值一致。
  Acceptance criteria: `grep "60,223" reports/func-model-perf-verification-report.md` 返回空；`python3 scripts/run_func_model_perf_signoff.py run --reports uncertainty-kpis --cases qwen-prefill-2000` 成功。
  RED: `grep "60,223" reports/func-model-perf-verification-report.md` 命中陈旧值（非零退出）。
  GREEN: 上述 grep 返回空，signoff 成功。
  Mutation: 临时把 report 中数值改成 `99,999 ms`，确认 signoff/report 一致性检查失败。
  Evidence: `.omo/evidence/task-1-prefill-evidence-audit.txt`

- [x] 2. 修复 DSE TTFT 模型
  What to do: 修改 `sim/design_space_explorer.py`：删除模块级 `_LLM_TRACE` 初始化，改为 `_DEFAULT_LLM_SPEC`；`simulate_layer(config, batch_m=None)` 按 batch_m 重新生成 trace；新增 `simulate_prefill()` 与 `ttft_ms_from_prefill()`；CLI `--batch-m` 放宽为 `type=int` 并校验 `>=1`；`evaluate_config` 输出增加 `ttft_ms` 字段（非 CV 模式）；新增/更新 `sim/tests/test_design_space_explorer.py` 中 TTFT 单元测试。
  Acceptance criteria: `python3 -m pytest sim/tests/test_design_space_explorer.py -q` 通过；`cd sim && PYTHONPATH=. python design_space_explorer.py --quick --batch-m 128 --model-spec qwen2.5-3b --output /tmp/dse_ttft_m128.json` 成功且 JSON 中含 `ttft_ms`。
  RED: `python3 -m pytest sim/tests/test_design_space_explorer.py::test_prefill_ttft -x` 因函数/字段不存在而失败。
  GREEN: pytest 通过；DSE CLI `--batch-m 128` 成功。
  Mutation: 临时把 `--batch-m` 上限改回 [1,2]，确认 `128` 被拒绝。
  Evidence: `.omo/evidence/task-2-dse-ttft-model.txt`

- [x] 3. 建立 DSE TTFT 目标并更新验证规格
  What to do: 运行 DSE 获取 Block 64×64 TTFT 目标（`--batch-m 128` 和 `--batch-m 2000`）；更新 `.omo/notes/func-model-perf-verification-spec.md`：Gate 1 拆分为 1a (TPS) 和 1b (TTFT)，记录 DSE TTFT 目标值/Func Model 实测值/PASS 区间，清理 stale 的 "canonical TPS=10.99" 闭环记录并新增 "DSE TTFT model fixed"。
  Acceptance criteria: 规格文件包含 Gate 1b，且 DSE 目标值与 Func Model 实测值落在 [0.5×, 2.0×] 区间。
  RED: `grep "Gate 1b\|DSE TTFT" .omo/notes/func-model-perf-verification-spec.md` 未命中（实现前）。
  GREEN: 规格文件包含 Gate 1b 且数值落在 PASS 区间。
  Mutation: 临时把 DSE 目标改成 Func Model 值的 10 倍，确认一致性脚本报告 FAIL。
  Evidence: `.omo/evidence/task-3-dse-ttft-target.json`

- [x] 4. 更新验证报告
  What to do: 在 `reports/func-model-perf-verification-report.md` 中确认 3.2 节数值；新增 3.5 节 DSE TTFT 一致性结果；清理 7.2/8.1/8.3/10 节陈旧数值；更新 "限制与后续工作" 措辞。
  Acceptance criteria: `grep "60,223" reports/func-model-perf-verification-report.md` 返回空；报告与 task-20 证据一致。
  RED: `grep "60,223" reports/func-model-perf-verification-report.md` 命中。
  GREEN: grep 为空，signoff 通过。
  Mutation: 临时把报告中的 prefill_ms 改成 `99,999 ms`，确认 signoff 报告一致性检查失败。
  Evidence: `.omo/evidence/task-4-report-update.txt`

- [x] 5. 全量回归、signoff 与提交
  What to do: 运行 `python3 scripts/run_func_model_perf_signoff.py run --all-spec`；运行 `PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q`；运行 DSE signoff 生成 M=128/2000 证据；生成 DoneClaim bundle；单 commit 提交。
  Acceptance criteria: `--all-spec` 和 pytest 全部通过；DSE TTFT 证据已生成；git status 干净或仅有预期文件。
  RED: 实现前 `--all-spec` 因 DSE 目标缺失/不一致而失败。
  GREEN: 全部通过。
  Mutation: 临时破坏 `simulate_layer` 中 batch_m 传递，确认 DSE TTFT 测试失败。
  Evidence: `.omo/evidence/task-5-full-signoff.txt`, `.omo/evidence/dse-funcmodel-prefill-closure/doneclaims.json`

## Final Verification Wave

- [x] F1. Plan compliance audit
  What to do: 验证 `.omo/plans/dse-funcmodel-prefill-closure.md` 中 TODOs 全部 `- [x]`；每个 todo 都有 DoneClaim 记录且 red/green/mutation 命令与结果匹配。
  Command: `python3 scripts/audit_plan_compliance.py --plan .omo/plans/dse-funcmodel-prefill-closure.md --evidence-dir .omo/evidence/dse-funcmodel-prefill-closure/`

- [x] F2. Architecture / code quality audit
  What to do: `lsp_diagnostics` on `sim/design_space_explorer.py` → zero errors；检查 `simulate_layer`/`simulate_prefill` 不引入全局可变状态；CLI 参数校验合理；TTFT 输出单位正确；pytest 全量通过。
  Command: `PYTHONPATH=sim python -m pytest sim/tests/ sim/timing/tests/ -q`

- [x] F3. Real agent QA
  What to do: 请独立 agent 检查报告中是否还有公式修正前的陈旧数值、规格 Gate 1b 是否清晰可执行、DSE TTFT 输出是否真正来自 `batch_m>1` 的 trace。
  Command: `python3 scripts/real_qa_check.py --mode dse-ttft --evidence .omo/evidence/task-dse-ttft-m128.json`

- [x] F4. Scope fidelity
  What to do: 确认未修改 RTL/未引入 VCS/未新增依赖；git diff 只包含 DSE、规格、报告、测试、证据。
  Command: `git diff --stat` + `git status --short`
