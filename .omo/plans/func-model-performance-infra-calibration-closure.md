# Func Model 性能 Spec 验证与后校准接口计划

## TL;DR
> Summary:      在 RTL 开发之前，把 Func Model 建成可验证、可复现、带不确定性声明的性能 spec；当前只验证架构公式、事件/DAG、workload 聚合、趋势和报告，不要求 RTL 周期精确。
> Deliverables: 规范化 performance spec；MMIO 语义事件流；architecture-owned timing providers；独立 hand-derived oracle；Qwen/CV workload；双路径聚合；sweep/不确定性/KPI 报告；未来 RTL 校准接口。
> Effort:       XL
> Risk:         High - 当前 timing stack 与 FuncModel 分离，Qwen 参数/trace 重复且冲突，报告存在 double-count 和陈旧结果，自确认测试不足。

## Scope
### Must have
- `FuncModel/MMIOBridge` 是唯一 semantic performance event source；事件只表达 command acceptance/completion/order，不伪装成硬件实测周期。
- Numerical golden kernels 保持只负责功能语义；独立 architectural timing provider 输出 `estimated_cycles`、assumptions、uncertainty、provider/spec version。
- 建立 spec-owned 参数源，覆盖 MXU、SFU、Vector、DMA、DRAM、NoC、KV Cache；standalone SW overhead 验证公式结构但不进入 canonical total。
- Qwen2.5-3B 以 pinned metadata 为准：`hidden=2048, intermediate=11008, layers=36, heads=16, kv_heads=2, head_dim=128`；建立唯一 17-op workload/DAG。
- 冻结 Qwen block-0、decode、prefill 和 MobileNetV3/ResNet50/YOLOv8n hard-gate workload matrix。
- Structural exactness、provider-vs-independent-oracle `<=10%`、双路径 workload total/breakdown `<=20%`、单调性和 bottleneck transition 全部通过。
- TPS/TTFT/TPOT/ITL/FPS/latency 输出 low/base/high，默认 latency/cycles `±30%`；产品目标比较全部 `report_only=true`。
- 保留未来 RTL 校准需要的 boundary/provider/artifact/provenance schema，但当前 artifact 明确 `calibration_state=uncalibrated`。
- TDD、fresh evidence、anti-vacuous、bug tracking、F1-F4 独立最终复核。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 当前阶段不运行或要求 VCS/RTL、RTL cycle count、RTL calibration/holdout、SoC RTL delta gate、Spike cycle measurement、RTL bug fix、FPGA/silicon correlation。
- 不修改 `rtl/**`，不把历史 RTL report/testcase status/config 注释当作当前 oracle，不称结果为 measured/cycle-accurate/RTL-accurate。
- 不用 provider 自身公式或共享 helper 验证 provider；independent oracle 禁止导入 `sim.models`、`sim.engine`、`sim.timing.providers` 或 timing aggregators。
- 不接受 zero event/cycle、空 breakdown、unknown op 默认 latency、NaN/Inf、单位/边界不匹配、stale generated report、stdout “PASS” 或 grep-only 作为通过。
- 不把 profile-only 36-layer workload 当作功能签收；必须标记 `numerical_execution=false`。
- 不把产品 KPI 是否达到目标作为本阶段 PASS/FAIL，不隐藏超过 uncertainty band 的目标差距。
- 当前 hard-gate matrix 不接受 waiver；失败必须修复或保持 `performance_spec_verified=false`。
- 不修改或提交规划前已有的 `.omo/drafts/arc-model-v3-1-constraint-schema.md`、`.omo/drafts/func-model-functional-signoff-repair.md`、`.omo/plans/arc-model-v3-1-constraint-schema.md`。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD；每个实现 todo 先运行声明的 RED case，确认目标断言失败，再实现并运行 GREEN + adversarial mutation。
- RED command rule: 每个 T1-T24 的第一个 QA command 在实现前原样执行并预期 nonzero，DoneClaim 记录命令、exit code 和缺失/错误行为的具体 assertion ID；实现后原样重跑并预期本文声明的 exit 0/structured verdict。第二个或显式 `negative` command 是 mutation gate；不能用“文件不存在”之外的未分类异常充当有效 RED。
- QA policy: every todo has agent-executed scenarios；Python 3.10 为基线，最终 signoff 不依赖 EDA、网络、secret 或外部下载。
- Evidence: `.omo/evidence/task-<N>-<slug>.<ext>`；完整 run bundle 位于 `.omo/evidence/func-model-perf-spec/<run_id>/`，`run_id=<UTC>-<head12>-<spec_hash12>`。
- Mandatory provenance: HEAD、product source fingerprint、dirty paths、Python/dependency versions、host、seed、argv、spec/config/workload/provider/oracle/report hashes、units、UTC start/end；timestamp 不进入 canonical content hash。
- Mandatory `DoneClaim`: T1-T25 每项写 `todo_id, red_command/result, green_command/result, mutation_command/result, head, source_fingerprint, evidence_path/hash, assertions[], verdict, stale_state, misleading_success_output`；缺失或不匹配使 T25/F1 失败。
- Dirty worktree: runner 先校验下表中由正式计划冻结的 protected baseline，再记录执行开始时的 `.omo/**` allowlist；product fingerprint 排除 `.omo/**`。任何未声明 product dirty path、protected path 类型变化或 SHA-256 前后不一致均失败；active plan 本身不进入自引用 hash。

### Trusted protected-file baseline
| Path | Git state/type | SHA-256 |
| --- | --- | --- |
| `.omo/drafts/arc-model-v3-1-constraint-schema.md` | untracked regular file | `5304262b8a8a797aabe6b21ff12522ea51d011b9d52ec56f4aebb310464b04c9` |
| `.omo/drafts/func-model-functional-signoff-repair.md` | untracked regular file | `5f09d79f29747cece829afba6618b8e99433be8fb9c1467652ecc0a7985e2459` |
| `.omo/plans/arc-model-v3-1-constraint-schema.md` | untracked regular file | `dcddc05b9b302a4f1aa46e6b20dc4bfb6c28cbe03ae233fd37c30030a41c59c4` |

## Frozen spec-stage decisions
### Vocabulary and numeric policy
- 所有周期字段命名 `estimated_cycles`；`measured_cycles` 只允许未来 `calibration_state=rtl_calibrated` artifact 使用，当前 validator 遇到该字段即拒绝纳入 verdict。
- Provider error 使用未舍入值：`abs(provider-oracle)/abs(oracle)*100`。普通 case 在 `oracle>10 cycles` 时每 case `<=10%`，`0<oracle<=10` 时 absolute error `<=1 cycle`；任一普通 case 非正/NaN/Inf 值失败。唯一例外是 matrix 显式声明 `expected_noop=true` 的 case：oracle 与 provider 必须都为 exact integer zero，仍参与 structural coverage，但不进入相对误差、正周期或 workload activity gate；任何非零、NaN/Inf 或未声明 no-op 的 zero 都失败。
- Workload path error 使用未舍入值；total 和每个占 total `>=5%` 的 major breakdown 每 case `<=20%`；小 breakdown 使用 absolute error `<=2 cycles`。
- Integer cycle 使用 ceiling，不允许 banker's rounding；bytes/bits、Hz/MHz/GHz、cycle/ns 转换集中在 typed unit helpers 并检测 overflow。

### Provider hard-gate matrix
- MXU 10 rows `(M,K,N)`：`(1,64,64),(4,64,64),(64,64,64),(64,128,64),(64,64,128),(32,128,128),(1,2048,2048),(128,2048,2048),(1,2048,11008),(128,2048,11008)`。
- SFU 24 rows：`softmax/layernorm/rmsnorm/gelu/silu/rope × elements={16,128,2048,11008}`。
- Vector 30 rows：`add/mul/max/sum/conv/resid × dim={1,128,256,2048,11008}`。
- DMA 10 rows：`bytes={1,64,4096,65536,1048576} × channels={1,4}`；DRAM 10 rows：相同 bytes × `read/write`。
- NoC 8 rows：`topology={crossbar,mesh} × bytes={64,4096} × route={0->1,0->3}`。
- KV 8 rows：access `token_pos={0,1,127,511,2047}` + layer-switch `sram_kb={64,256,512}`，Qwen kv_heads=2/head_dim=128/layers=36；只有 `token_pos=0` 声明 `expected_noop=true` 并执行 exact-zero 规则。
- SW overhead 4 rows：Qwen block-0、Qwen 36-layer decode with DMA chain、同 workload without DMA chain、ResNet50；公式一致性是 hard gate，所有输出标记 assumption-only 且不进入 canonical total。

### Independent oracle
- Normative files：`docs/func-model-performance-spec.md` + `config/func_model_perf_spec_v1.json`。每个参数记录 owner、basis=`architecture_assumption`、units、rationale、uncertainty；历史 “P0 measured/calibrated” 值必须重新分类，不能作为 oracle 来源。
- Hand-derived provider vectors：`config/func_model_perf_oracle_v1.json` 保存输入、逐项 decomposition、expected、手工推导说明和 spec hash；不得由 provider 生成。
- Independent workload Path B 由 `scripts/reduce_func_model_perf_oracle.py` + `config/func_model_workload_oracle_v1.json` 所有：只读取 canonical manifest 与 hand-authored per-op decomposition，独立计算 serialized/overlap critical path，不调用 Path A event/provider/timeline/reducer helper。
- `scripts/verify_func_model_perf_spec.py` 只读取 provider CLI JSON 与 provider oracle vectors；两个 oracle script 均禁止导入 `sim.models`、`sim.engine`、`sim.timing.providers`、`sim.timing.timing_engine`、`sim.npu_sim` 或 Path A aggregator。AST import-policy、Path A reducer mutation 与 Path B decomposition mutation必须证明两条路径不会共同确认同一缺陷。

### Workload hard-gate matrix and independent paths
- Qwen：`qwen25-3b-blk0-decode` 17 semantic ops；`qwen25-3b-decode-c128-g1`、`prefill-16`、`prefill-128` 各 36×17=612 semantic ops；固定 seed=42。
- CV：MobileNetV3-Small 124 trace entries（54 GEMM、42 SFU）；ResNet50 105（54 GEMM、51 SFU）；YOLOv8n 129（63 GEMM、57 SFU）；固定 input shape 1×3×224×224、seed=42。
- Path A：FuncModel semantic events -> provider estimates -> dependency DAG critical-path reducer。
- Path B：canonical workload manifest -> `config/func_model_workload_oracle_v1.json` hand decomposition -> `scripts/reduce_func_model_perf_oracle.py` independent critical-path result；T5 owns schema/reducer/isolation tests，T13/T14 分别 hand-author Qwen/CV entries，T16/T17 只消费并比较两条结果。
- `prefill-2000`、Qwen1.5B/3B/7B 和全部产品目标只作 report/scaling，不加入 workload numeric hard gate。

### DAG, sweeps and uncertainty
- Canonical Qwen layer DAG 固定 17 ops/9 MXU/5 SFU/3 Vector；DMA/NoC/KV 是 expanded provider activities，通过 parent operation ID 关联，不改变 semantic op count。
- Sweep：bandwidth `{6.4,12.8,25.6,51.2,102.4} GB/s`、array `{32,64,128}`、DMA channels `{1,2,4,8}`、prompt `{16,128,512,2000}`、context `{128,512,2048}`、NoC hop `{1,2,4}`；matrix 必须显式包含两个 bottleneck endpoint config。
- Monotonic：对 bandwidth/array/DMA-channel 等 resource 自变量，latency/cycle finite-difference 必须 `<=0`；对 prompt/context/hop/bytes 等 workload-size 自变量，latency/cycle finite-difference 必须 `>=0`。零 derivative 允许但必须报告；反向或 NaN/Inf 失败。Bottleneck share `>=55%` 才分类；`BW=6.4GB/s,array=128` 必须 memory-bound，`BW=102.4GB/s,array=32` 必须 compute-bound。
- Uncertainty 先作用于 cycles/latency：`low=0.7*base, high=1.3*base`；TPS/FPS 等 throughput 使用反比区间 `[base/1.3, base, base/0.7]`。TPS、TTFT、TPOT、ITL、prefill_ms、FPS、inference_latency 全部带 band；utilization/bandwidth breakdown 保持 diagnostic 并携带 assumptions。

### Future calibration-ready contract
- Provider artifact 必须含 `schema_version, provider_id/version, basis, calibration_state, boundary_id, supported_domain, spec/config hash, assumptions, units, uncertainty`。
- 当前只允许 `basis=architectural_formula, calibration_state=uncalibrated`。未来可选字段 `rtl_head, eda_version, testbench_hash, raw_log_hash, fit_matrix_hash` 仅做 schema compatibility synthetic test，不进入当前 evidence/verdict。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. < 3 per wave (except the final) = under-splitting.
Wave 1: T1 normative spec；T2 typed contracts；T3 frozen matrices；T4 evidence runner；T5 independent oracle。
Wave 2: T6 MMIO events；T7 provider registry/future hook；T8 MXU；T9 SFU/Vector；T10 DMA/DRAM。
Wave 3: T11 NoC/KV；T12 SW overhead；T13 Qwen workload；T14 CV workloads；T15 timeline/report convergence。
Wave 4: T16 Qwen workload gates；T17 CV workload gates；T18 sweeps/bottleneck；T19 model scaling；T20 uncertainty/KPI reports。
Wave 5: T21 adversarial matrix；T22 regression baseline policy；T23 CI/signoff orchestration；T24 docs/bugs；T25 fresh closure run。
Critical path: T1-T5 -> T6/T7 -> T8-T15 -> T16-T20 -> T21-T25 -> F1-F4。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T1 | none | T3,T5,T7-T12,T20 | T2,T4 |
| T2 | none | T6,T7,T13-T15,T21 | T1,T3-T5 |
| T3 | T1 | T8-T14,T16-T20 | T4,T5 |
| T4 | none | T6,T7,T16-T25 | T1-T3,T5 |
| T5 | T1,T3 | T8-T14,T16,T17 | none |
| T6 | T2-T4 | T15-T17,T21 | T7-T10 |
| T7 | T1,T2,T4 | T8-T12,T15,T20 | T6 |
| T8 | T3,T5,T7 | T15,T16,T18 | T9,T10 |
| T9 | T3,T5,T7 | T15-T18 | T8,T10 |
| T10 | T3,T5,T7 | T15-T18 | T8,T9 |
| T11 | T3,T5,T7 | T15,T16,T18 | T12-T14 |
| T12 | T1,T3,T5,T7 | T20 | T11,T13,T14 |
| T13 | T2,T3,T5 | T15,T16,T19,T20 | T11,T12,T14 |
| T14 | T2,T3,T5 | T15,T17,T20 | T11-T13 |
| T15 | T2,T6-T11,T13,T14 | T16-T21 | none |
| T16 | T4,T5,T6,T8-T11,T13,T15 | T19-T25 | T17,T18 |
| T17 | T4,T5,T6,T8-T11,T14,T15 | T20-T25 | T16,T18 |
| T18 | T3,T4,T8-T11,T15 | T20-T25 | T16,T17,T19 |
| T19 | T3,T13,T16 | T20,T22-T25 | T18 |
| T20 | T1,T4,T7,T12,T16-T19 | T22-T25 | T21 |
| T21 | T2,T4,T6,T15-T20 | T23,T25 | T20,T22 |
| T22 | T4,T16-T20 | T23-T25 | T21 |
| T23 | T4,T16-T22 | T25 | T24 |
| T24 | T16-T22 | T25 | T23 |
| T25 | T21-T24 | F1-F4 | none |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [ ] 1. Establish the normative Func Model performance spec
  What to do: RED first, then create `docs/func-model-performance-spec.md` and `config/func_model_perf_spec_v1.json`. Own every parameter/formula ID/unit/assumption/uncertainty for seven hard-gate domains and standalone SW overhead. Reclassify historical RTL-derived comments as legacy/untrusted; do not copy measured numbers as oracle facts.
  Parallelization: Y | Wave 1 | Blocks T3,T5,T7-T12,T20 | Blocked by none
  References: `sim/config/npu_config.yaml:30-61` mixed ownership；`sim/models/dram.py:7-49` architecture assumptions；`sim/models/sw_overhead.py:1-70` assumption-heavy constants；`spec/soc_golden_contract.md` current performance exclusion。
  Acceptance criteria: RED `PYTHONPATH=sim python3 -m pytest sim/timing/tests/test_perf_spec_config.py -q` fails on missing normative spec；GREEN passes schema/units/owner/basis checks and rejects basis=`rtl_measurement`；canonical serialization is byte-stable.
  QA scenarios: `python3 scripts/check_func_model_perf_spec.py --spec config/func_model_perf_spec_v1.json` GREEN exits 0. `python3 scripts/check_func_model_perf_spec.py --negative-fixtures config/tests/perf_spec_bad_units.json,config/tests/perf_spec_rtl_basis.json` exits 0 only with `rejected=2,accepted=0`. Evidence `.omo/evidence/task-1-perf-spec.txt`.
  Commit: Y | feat(perf-spec): define architecture-owned performance spec | Files normative docs/config, checker, tests

- [ ] 2. Define typed event, provider, report and future-calibration contracts
  What to do: RED first, then add `sim/timing/perf_contract.py` with strict event IDs, semantic start/complete, typed units/errors, estimated result/report schemas and calibration-ready artifact fields. Unknown versions/ops/shapes/units, nonpositive values, NaN/Inf, duplicates and missing pairs fail closed.
  Parallelization: Y | Wave 1 | Blocks T6,T7,T13-T15,T21 | Blocked by none
  References: `sim/engine/timeline.py:7-68` underspecified events；`sim/timing/types.py:7-48` current reports；`sim/axi_tracer.py:13-51` host-time tracer unsuitable for cycles。
  Acceptance criteria: RED/GREEN `PYTHONPATH=sim python3 -m pytest sim/timing/tests/test_perf_contract.py -q`；round-trip/hash/version/unit/error cases pass；current artifact accepts only uncalibrated architecture basis while synthetic future schema parses but is ineligible for verdict.
  QA scenarios: `python3 -m sim.timing.perf_contract --self-check` GREEN exits 0. `python3 -m sim.timing.perf_contract --negative-fixtures config/tests/perf_contract_measured_cycles.json,config/tests/perf_contract_bad_unit.json,config/tests/perf_contract_nan.json` exits 0 only with `rejected=3,accepted=0`. Evidence `.omo/evidence/task-2-perf-contract.txt`.
  Commit: Y | feat(timing): add performance spec contracts | Files contract/tests

- [ ] 3. Freeze provider, workload, sweep and runtime matrices
  What to do: RED first, then create `config/func_model_perf_matrix_v1.json` and loader. Encode every frozen row/count/seed/error policy/sweep/runtime limit from this plan. Provider case <=30s, workload <=120s, full signoff <=1800s, peak RSS <=4GB; no case may silently skip.
  Parallelization: Y | Wave 1 | Blocks T8-T14,T16-T20 | Blocked by T1
  References: this plan's frozen matrices；`sim/model_specs.py:26-42` model registry；CV trace generators；`sim/timing/benchmark.py:40-92` current dynamic dispatch。
  Acceptance criteria: matrix test reports 10/24/30/10/10/8/8/4 provider rows and 7 hard workloads, exact sweep grids, no duplicate IDs, fixed seed=42, all runtime/memory fields present.
  QA scenarios: `python3 scripts/check_func_model_perf_spec.py --matrix config/func_model_perf_matrix_v1.json` GREEN exits 0. `python3 scripts/check_func_model_perf_spec.py --negative-fixtures config/tests/perf_matrix_duplicate.json,config/tests/perf_matrix_missing.json,config/tests/perf_matrix_skip.json,config/tests/perf_matrix_missing_6p4_endpoint.json` exits 0 only with `rejected=4,accepted=0`. Evidence `.omo/evidence/task-3-perf-matrix.txt`.
  Commit: Y | feat(perf-spec): freeze verification matrices | Files matrix/loader/tests

- [ ] 4. Build a no-RTL, fail-closed evidence and DoneClaim runner
  What to do: RED first, then create `scripts/run_func_model_perf_signoff.py` with `run/validate/audit/negative/rerun/baseline` and run-ID bundles. Record current-stage provenance/DoneClaims, atomic writes and source/report freshness. Parse the formal plan's protected baseline table before any task, verify path/git-state/type/SHA-256 before and after the run, and reject any evidence source path under live `rtl/**` before opening or hashing it. Evidence vocabulary excludes RTL/VCS/Spike cycles；stdout text never determines verdict.
  Parallelization: Y | Wave 1 | Blocks T6,T7,T16-T25 | Blocked by none
  References: `scripts/run_func_model_signoff.py:1008-1296` fingerprint/verdict pattern；`scripts/aggregate_software_signoff.py:41-135` stale checks；existing dirty worktree paths。
  Acceptance criteria: 15+ runner tests cover stale HEAD/source/report, missing claim, zero tests, collision, deterministic hash, live-RTL path refusal and protected pre-run/post-run drift；same inputs produce same canonical content hash despite timestamps.
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py negative --self-test --faults stale-head,stale-source,stale-report,missing-claim,zero-tests,collision,rtl-path,protected-pre-drift,protected-post-drift,pass-text` exits 0 only when structured JSON reports all 10 faults `rejected=true`；any `accepted>0` exits nonzero. Evidence `.omo/evidence/task-4-perf-runner.txt`.
  Commit: Y | feat(signoff): add performance spec evidence runner | Files runner/tests

- [ ] 5. Create genuinely independent provider and workload oracles
  What to do: RED first, then create `config/func_model_perf_oracle_v1.json`, `config/func_model_workload_oracle_v1.json`, `scripts/verify_func_model_perf_spec.py` and `scripts/reduce_func_model_perf_oracle.py`. Fill every provider row with manual decomposition/spec references；define Path B schema/reducer using hand-authored per-op costs and dependency edges plus synthetic serialized/overlap fixtures. Provider verifier consumes provider CLI JSON only；workload reducer never imports or calls Path A. Add formula/ceiling/unit/constant, Path A reducer and Path B decomposition mutations.
  Parallelization: N | Wave 1 | Blocks T8-T14,T16,T17 | Blocked by T1,T3
  References: provider model files under `sim/models/`；`sim/timing/tests/test_cross_engine.py:53-78` self-confirming pattern to replace；`scripts/analyze_perf.py:32-65` example closed-form decomposition but not an oracle source。
  Acceptance criteria: provider oracle has complete row/decomposition coverage；workload oracle reducer produces exact synthetic serialized/overlap critical paths；both have stable hashes, no generated marker and zero forbidden imports；mutating either path alone makes the cross-path test fail.
  QA scenarios: `python3 scripts/verify_func_model_perf_spec.py --oracle config/func_model_perf_oracle_v1.json --self-check --mutations ceiling,constant,units,noop-nonzero` exits 0 only when every mutation is rejected. `python3 scripts/reduce_func_model_perf_oracle.py --oracle config/func_model_workload_oracle_v1.json --self-check --mutations path-a-reducer,path-b-decomposition,dependency-edge` exits 0 only with `verdict=pass,rejected_mutations=3`. Evidence `.omo/evidence/task-5-independent-oracle.txt`.
  Commit: Y | test(perf-spec): add independent formula oracles | Files oracle/verifier/import-policy tests

- [ ] 6. Emit semantic performance events from FuncModel/MMIOBridge
  What to do: RED first, then add opt-in `PerformanceSession` at MMIO command acceptance/completion seams. Events carry semantic sequence, programmed shape and parent workload IDs; provider timeline is separate. Functional mode executes kernels；profile-only drives the same register/START path but skips numerical kernels and is never functional evidence.
  Parallelization: Y | Wave 2 | Blocks T15-T17,T21 | Blocked by T2-T4
  References: `sim/func_model.py:24-75` wiring；`sim/mmio_bridge.py:118-165,286-315,382-413,489-517` synchronous command paths；`scripts/compare_firmware_equivalence.py:125-165` observer seam。
  Acceptance criteria: event pairing/order/shape tests pass；timing disabled/enabled functional runs have identical output/MMIO/status/IRQ hashes；profile-only evidence says `numerical_execution=false` and cannot satisfy functional gate.
  QA scenarios: `PYTHONPATH=sim python3 -m pytest sim/tests/test_mmio_perf_events.py -q` exits 0. `python3 scripts/run_func_model_perf_signoff.py negative --case mmio-events --faults duplicate-start,missing-completion,wrong-shape` exits 0 only with structured `rejected=3,accepted=0`. Evidence `.omo/evidence/task-6-mmio-events.json`.
  Commit: Y | feat(func-model): emit semantic performance events | Files FuncModel/bridge/session/tests

- [ ] 7. Implement architectural provider registry and calibration-ready artifacts
  What to do: RED first, then add `sim/timing/providers.py` and `config/perf_providers/spec-block64-v1.json`. Providers read normative spec, return typed estimates/errors and declare domain/boundary/uncertainty. Current artifact remains uncalibrated；future RTL fields are schema-only synthetic fixtures.
  Parallelization: Y | Wave 2 | Blocks T8-T12,T15,T20 | Blocked by T1,T2,T4
  References: `sim/models/*` current estimators；`sim/golden_executor.py` legacy counters that must remain non-authoritative；`scripts/extract_func_model_cycles.py` legacy bypass。
  Acceptance criteria: explicit activation/rollback by provider ID；unsupported/out-of-domain/legacy source fails；no numerical kernel imports providers；current verdict rejects calibrated/RTL evidence.
  QA scenarios: `PYTHONPATH=sim python3 -m pytest sim/timing/tests/test_perf_providers.py -q` exits 0. `python3 scripts/run_func_model_perf_signoff.py negative --case provider-registry --faults unknown-op,out-of-domain,rtl-labeled-artifact` exits 0 only with `rejected=3,accepted=0`. Evidence `.omo/evidence/task-7-provider-registry.txt`.
  Commit: Y | feat(timing): add architectural provider registry | Files provider registry/artifact/tests

- [ ] 8. Verify MXU architectural estimates against the independent oracle
  What to do: RED provider mutations, then adapt Block/MXU estimator to normative spec and typed provider. Run all 10 rows, emit tile/state decomposition and assumptions. Do not import `analyze_perf.py` or historical RTL tables as oracle.
  Parallelization: Y | Wave 2 | Blocks T15,T16,T18 | Blocked by T3,T5,T7
  References: `sim/engine/block_engine.py` and `sim/models/mxu.py` estimators；`docs/mxu-perf-calibration.md` historical evidence explicitly non-oracle。
  Acceptance criteria: every row positive/finite/in-domain and meets <=10% or small absolute rule；tile count and ceiling invariants exact；mutated tile-base/axis order fails.
  QA scenarios: `python3 scripts/verify_func_model_perf_spec.py --domain mxu` GREEN exits 0 with `rows=10,failed=0`. `python3 scripts/verify_func_model_perf_spec.py --domain mxu --mutations mkn-swap,tile-base,axis-order` exits 0 only with `rejected_mutations=3`. Evidence `.omo/evidence/task-8-mxu-spec.json`.
  Commit: Y | test(perf-spec): verify MXU estimates | Files MXU provider/models/tests

- [ ] 9. Verify SFU and Vector architectural estimates
  What to do: RED mutations, then align all six SFU and six Vector ops to spec-owned parameters. Remove unknown-op defaults and ambiguous element/block counts. Run 24+30 oracle rows.
  Parallelization: Y | Wave 2 | Blocks T15-T18 | Blocked by T3,T5,T7
  References: `sim/models/sfu.py:1-80` current defaults；`sim/models/vector.py` block formulas；`sim/config/npu_config.yaml:30-61` historical labels to reclassify。
  Acceptance criteria: every row meets formula gate；unsupported op/dim<=0 fails typed；linear/block-boundary slopes and ceiling points exact；mutations are detected.
  QA scenarios: `python3 scripts/verify_func_model_perf_spec.py --domain sfu,vector` GREEN exits 0 with `rows=54,failed=0`. `python3 scripts/verify_func_model_perf_spec.py --domain sfu,vector --mutations unknown-default,off-by-one,wrong-block-size` exits 0 only with `rejected_mutations=3`. Evidence `.omo/evidence/task-9-sfu-vector-spec.json`.
  Commit: Y | test(perf-spec): verify SFU and Vector estimates | Files models/providers/tests

- [ ] 10. Verify DMA and DRAM architectural estimates
  What to do: RED mutations, then align bandwidth, burst, setup, refresh, row-conflict and read/write units to normative spec. Run both 10-row matrices; zero/negative transfer remains safe library behavior only, never signoff-valid.
  Parallelization: Y | Wave 2 | Blocks T15-T18 | Blocked by T3,T5,T7
  References: `sim/models/dma.py:1-125` DMA formulas；`sim/models/dram.py:7-91` DRAM assumptions；`sim/timing/tests/test_metrics.py` zero-safe behavior。
  Acceptance criteria: all rows meet gate；estimated cycles respect bytes/channels monotonicity；refresh/access units validated；zero/negative signoff requests fail.
  QA scenarios: `python3 scripts/verify_func_model_perf_spec.py --domain dma,dram` GREEN exits 0 with `rows=20,failed=0`. `python3 scripts/verify_func_model_perf_spec.py --domain dma,dram --mutations gbps-unit,floor-rounding,zero-size` exits 0 only with `rejected_mutations=3`. Evidence `.omo/evidence/task-10-memory-spec.json`.
  Commit: Y | test(perf-spec): verify DMA and DRAM estimates | Files memory models/providers/tests

- [ ] 11. Verify NoC and KV Cache architectural estimates
  What to do: RED mutations, then align topology/hop/serialization/contention and KV capacity/hit/miss/layer-switch assumptions to spec. Run 8+8 oracle rows using kv_heads=2.
  Parallelization: Y | Wave 3 | Blocks T15,T16,T18 | Blocked by T3,T5,T7
  References: `sim/models/noc.py:1-178` NoC；`sim/models/kv_cache.py:14-144` KV assumptions；Qwen pinned metadata。
  Acceptance criteria: all non-noop rows meet numeric gate；more bytes/hops/context cannot reduce relevant cycles；`token_pos=0` has `expected_noop=true` and exact oracle/provider zero, is excluded only from positive/error/activity gates；wrong kv_heads or nonzero no-op mutation fails.
  QA scenarios: `python3 scripts/verify_func_model_perf_spec.py --domain noc,kv --mutations route,hit-rate,kv-heads,noop-nonzero` exits 0 only with all 16 rows valid and `rejected_mutations=4`；any accepted mutation exits nonzero. Evidence `.omo/evidence/task-11-noc-kv-spec.json`.
  Commit: Y | test(perf-spec): verify NoC and KV estimates | Files models/providers/tests

- [ ] 12. Verify standalone SW overhead assumptions without integrating them
  What to do: RED mutations, then validate four hand-decomposed SW rows. Label results `assumption_only=true, included_in_canonical_total=false, uncertainty_pct>=30`. Correct stale default layer/model assumptions but do not add SW cycles to main totals.
  Parallelization: Y | Wave 3 | Blocks T20 | Blocked by T1,T3,T5,T7
  References: `sim/models/sw_overhead.py:1-150` standalone model；old plan SW integration deferral。
  Acceptance criteria: formula structure meets gate；DMA-chain/no-chain ordering holds；any report including SW in canonical total fails until a future explicit spec revision.
  QA scenarios: `python3 scripts/verify_func_model_perf_spec.py --domain sw_overhead` GREEN exits 0 with `rows=4,failed=0`. `python3 scripts/verify_func_model_perf_spec.py --domain sw_overhead --mutations include-in-total,stale-28-layers` exits 0 only with `rejected_mutations=2`. Evidence `.omo/evidence/task-12-sw-overhead-spec.json`.
  Commit: Y | test(perf-spec): verify standalone software overhead | Files SW model/provider/tests

- [ ] 13. Canonicalize Qwen2.5-3B workload and remove duplicate builders
  What to do: RED stale-metadata tests, then create `sim/timing/workloads.py` and `config/workloads/qwen25_3b_perf_spec_v1.json`. Correct `model_specs.py`/`npu_sim.py` kv_heads and dimensions；update non-RTL timing consumers/docs；hand-author the four Qwen Path B entries in `config/func_model_workload_oracle_v1.json`. The checker rejects any declared source path under `rtl/**` before file access and never reads/hashes live RTL artifacts.
  Parallelization: Y | Wave 3 | Blocks T15,T16,T19,T20 | Blocked by T2,T3,T5
  References: pinned `sim/signoff/test_qwen25_3b_real_blk0.py:44-52`；`sim/model_specs.py:28` conflict；`sim/npu_sim.py:43-75` duplicate；`sim/timing/timing_engine.py:22-45` duplicate；`scripts/extract_func_model_cycles.py:56-196` non-authoritative direct-model bypass。
  Acceptance criteria: 17-op/612-op counts, shapes, DAG and hashes exact；all authoritative consumers use one builder；Qwen Path B entries are hand-authored and pass T5 import/isolation policy；repository checker rejects old dims/kv_heads, duplicate authoritative builders and any RTL source declaration without opening it.
  QA scenarios: `python3 scripts/check_perf_workloads.py --workload qwen25-3b --oracle config/func_model_workload_oracle_v1.json` exits 0 with four exact workloads. `python3 scripts/check_perf_workloads.py --negative-fixtures config/tests/qwen_old_dims.json,config/tests/qwen_7gemm.json,config/tests/qwen_rtl_source.json` exits 0 only with `rejected=3,rtl_files_opened=0`. Evidence `.omo/evidence/task-13-qwen-workload.txt`.
  Commit: Y | fix(workload): canonicalize Qwen performance workload | Files non-RTL workload/model/timing/scripts/docs/tests

- [ ] 14. Freeze canonical CV workloads
  What to do: RED count/schema mutations, then normalize MobileNetV3, ResNet50 and YOLOv8n traces into the shared workload contract with fixed input/seed/hashes. Unknown/misc ops must map explicitly or fail, never default silently.
  Parallelization: Y | Wave 3 | Blocks T15,T17,T20 | Blocked by T2,T3,T5
  References: `sim/cv/cv_trace.py:175-270` MobileNet；`sim/cv/traces/resnet50_trace.py:134-212`；`sim/cv/traces/yolov8n_trace.py:195-303`。
  Acceptance criteria: exact 124/105/129 entry counts and declared GEMM/SFU counts；stable hashes；all entries have typed engine/op/shape/dependency or explicit host-only classification；three hand-authored CV Path B entries pass T5 isolation policy.
  QA scenarios: `python3 scripts/check_perf_workloads.py --workload mobilenetv3,resnet50,yolov8n --oracle config/func_model_workload_oracle_v1.json` exits 0 with exact counts/hashes. `python3 scripts/check_perf_workloads.py --negative-fixtures config/tests/cv_dropped_layer.json,config/tests/cv_unknown_op.json,config/tests/cv_bad_shape.json` exits 0 only with `rejected=3,accepted=0`. Evidence `.omo/evidence/task-14-cv-workloads.txt`.
  Commit: Y | feat(workload): freeze representative CV traces | Files CV adapters/manifests/tests

- [ ] 15. Converge timeline, overlap and report semantics
  What to do: RED hand-DAG cases, then make TimingEngine/NPUSimulator consume shared events/providers/workloads. Define canonical critical path and hidden/exposed DMA/NoC fields；remove manual rewinds/double-count and require layer/event reducers agree.
  Parallelization: N | Wave 3 | Blocks T16-T21 | Blocked by T2,T6-T11,T13,T14
  References: `sim/engine/timeline.py:184-231` overlap ambiguity；`sim/npu_sim.py:158-175,430-449` rewinds；`sim/timing/timing_engine.py:94-122` wall keys；`sim/timing/dashboard.py:120-127` double-count risk。
  Acceptance criteria: serialized/overlap/contention hand DAGs exact；Path A report has explicit critical path；no sum-of-breakdowns total；zero-event signoff rejected while library-safe zero helpers remain.
  QA scenarios: `PYTHONPATH=sim python3 -m pytest sim/timing/tests/test_perf_timeline.py -q` exits 0 with exact serialized/overlap/contention assertions. `python3 scripts/run_func_model_perf_signoff.py negative --case path-a-timeline --faults duplicate-dma,removed-dependency,empty-events` exits 0 only with `rejected=3,accepted=0`. Evidence `.omo/evidence/task-15-timeline-report.txt`.
  Commit: Y | refactor(timing): converge event timeline and reports | Files timing stack/tests

- [ ] 16. Close Qwen workload structural and dual-path spec gates
  What to do: Run four hard Qwen cases through Path A and Path B. Block-0 may run functional mode；36-layer cases use profile-only. Compare total/major breakdown under <=20% rule after exact structural/DAG checks.
  Parallelization: Y | Wave 4 | Blocks T19-T25 | Blocked by T4-T6,T8-T11,T13,T15
  References: T13 workload；T15 reducers；`docs/qwen25-3b-forward-spec.md` op-order source after correction。
  Acceptance criteria: exact 17/612 semantic counts, matching hashes/units, positive expected activity；T5-owned independent reducer consumes T13 hand-authored entries；every workload total/major breakdown within gate；profile-only cannot claim functional pass.
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py run --cases qwen-blk0,qwen-decode-c128-g1,qwen-prefill-16,qwen-prefill-128 --compare-paths a,b` exits 0 with `passed=4`. `python3 scripts/run_func_model_perf_signoff.py negative --case qwen-paths --faults missing-attention,path-a-double-count,path-b-decomposition` exits 0 only with `rejected=3,accepted=0`. Evidence `.omo/evidence/task-16-qwen-spec-gates.json`.
  Commit: Y | test(perf-spec): close Qwen workload gates | Files integration tests/minimal fixes

- [ ] 17. Close representative CV dual-path spec gates
  What to do: Run three CV workloads through both independent paths and compare total/major breakdown. Validate im2col/DMA/MXU/SFU mapping and host-only exclusions explicitly.
  Parallelization: Y | Wave 4 | Blocks T20-T25 | Blocked by T4,T5,T6,T8-T11,T14,T15
  References: T14 manifests；`sim/cv/conv_mapper.py` mapping；timing benchmark CV path。
  Acceptance criteria: all structural counts exact, nonzero accelerator coverage；T5-owned independent reducer consumes T14 hand-authored entries；Path A/B total and major breakdown <=20%；no unknown op or silent host exclusion.
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py run --cases mobilenetv3,resnet50,yolov8n --compare-paths a,b` exits 0 with `passed=3`. `python3 scripts/run_func_model_perf_signoff.py negative --case cv-paths --faults im2col-bytes-x8,dropped-depthwise,unknown-op,path-b-decomposition` exits 0 only with `rejected=4,accepted=0`. Evidence `.omo/evidence/task-17-cv-spec-gates.json`.
  Commit: Y | test(perf-spec): close CV workload gates | Files CV integration tests/minimal fixes

- [ ] 18. Close monotonicity and bottleneck-transition sweeps
  What to do: Execute every frozen resource/workload sweep with deterministic config clones. Assert monotonic relationships, diminishing-return sanity and the two required memory/compute bottleneck endpoints；emit sensitivity derivatives.
  Parallelization: Y | Wave 4 | Blocks T20-T25 | Blocked by T3,T4,T8-T11,T15
  References: `sim/timing/benchmark.py` sweep paths；`sim/timing/tests/test_tile_double_buffer.py` existing trends；normative spec bottleneck definitions。
  Acceptance criteria: all signed derivative assertions pass, zero slopes are reported, NaN/Inf or wrong-direction slopes fail；both explicit endpoint configs are present and bottleneck share >=55%；same config rerun byte-stable.
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py run --sweeps bandwidth,array,dma-channels,prompt,context,noc-hop --require-endpoints memory,compute` exits 0 with all grids present. `python3 scripts/run_func_model_perf_signoff.py negative --case sweeps --faults resource-positive-slope,workload-negative-slope,nan-slope,missing-6p4-endpoint,unreachable-transition` exits 0 only with `rejected=5,accepted=0`. Evidence `.omo/evidence/task-18-sensitivity.json`.
  Commit: Y | test(perf-spec): verify scaling and bottleneck transitions | Files sweep runner/tests

- [ ] 19. Verify cross-model scaling without product signoff
  What to do: Generate Qwen1.5B/3B/7B decode report-only workloads from one builder. Assert weight bytes and total decode estimates increase with model size and memory-bound cycles/weight-byte normalize within 20%；do not gate absolute TPS targets.
  Parallelization: Y | Wave 4 | Blocks T20,T22-T25 | Blocked by T3,T13,T16
  References: `sim/model_specs.py:26-30` model specs；existing benchmark family reports。
  Acceptance criteria: 1.5B<3B<7B weight/total cycles, normalized memory-bound ratio within 20%, all reports carry assumptions and `report_only=true`.
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py run --cases qwen-scaling-1p5b-3b-7b --report-only` exits 0 with ordered weights/cycles and normalized delta <=20%. `python3 scripts/run_func_model_perf_signoff.py negative --case model-scaling --faults swapped-model-params,kpi-target-gate` exits 0 only with `rejected=2,accepted=0`. Evidence `.omo/evidence/task-19-model-scaling.json`.
  Commit: Y | test(perf-spec): verify cross-model scaling | Files workload/report tests

- [ ] 20. Produce uncertainty-aware report-only KPIs
  What to do: Update metrics/dashboard/summary/benchmark for canonical total and frozen uncertainty transforms. Generate Qwen prefill-2000, model family and registered CV KPI reports. Separate volatile run metadata from canonical content；remove constant-prefill/stale notes.
  Parallelization: Y | Wave 4 | Blocks T22-T25 | Blocked by T1,T4,T7,T12,T16-T19
  References: `sim/timing/metrics.py:10-200` metrics；`sim/timing/dashboard.py:120-232` zero/timestamp behavior；`sim/timing/generate_summary.py:109-112` false constant note；product target docs。
  Acceptance criteria: all required latency/throughput bands follow frozen formulas；diagnostics carry assumptions；product target miss remains report-only；canonical JSON byte-stable and nonzero.
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py run --reports uncertainty-kpis --cases qwen-prefill-2000,qwen-model-family,mobilenetv3,resnet50,yolov8n` exits 0 with all required low/base/high fields. `python3 scripts/run_func_model_perf_signoff.py negative --case uncertainty-kpis --faults timestamp-in-hash,direct-throughput-band,empty-report,kpi-gating` exits 0 only with `rejected=4,accepted=0`. Evidence `.omo/evidence/task-20-uncertainty-kpis.json`.
  Commit: Y | fix(timing): publish uncertainty-aware KPI reports | Files metrics/dashboard/summary/benchmark/tests/results

- [ ] 21. Add adversarial and anti-vacuous performance-spec matrix
  What to do: Add one negative per provider/workload plus stale source/report, duplicate/missing events, wrong units/hash/seed, zero activity, self-importing oracle, RTL-labeled evidence, profile-only overclaim and misleading PASS output.
  Parallelization: Y | Wave 5 | Blocks T23,T25 | Blocked by T2,T4,T6,T15-T20
  References: `sim/tests/test_soc_fm.py` functional negative patterns；runner and oracle contracts。
  Acceptance criteria: declared mutation set 100% detected；bundle records `stale_state.tested=true,rejected=true` and `misleading_success_output.tested=true,rejected=true`；no bad fixture creates PASS index.
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py negative --matrix all --self-test-disable-each-validator` GREEN exits 0 only with `declared_faults=detected_faults,accepted=0` and every disabled validator causing its paired test to fail. Evidence `.omo/evidence/task-21-adversarial.json`.
  Commit: Y | test(signoff): add performance spec adversarial matrix | Files fixtures/tests/minimal validators

- [ ] 22. Establish performance-spec regression baseline and change policy
  What to do: Create versioned canonical baseline from fresh current results, keyed by spec/workload/provider hashes. Structural/formula/workload/invariant gates are absolute；KPI drift only raises report diff. Baseline updates require changed spec version/rationale, never “accept current output.”
  Parallelization: Y | Wave 5 | Blocks T23-T25 | Blocked by T4,T16-T20
  References: `results/timing/*` stale current outputs；existing stale-fingerprint runner patterns。
  Acceptance criteria: unchanged inputs reproduce baseline hash；source/spec change makes it stale；KPI-only delta is report diff while hard-gate regression fails；baseline cannot self-update in validate mode.
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py baseline create --from-latest-fresh --output config/baselines/func_model_perf_spec_v1.json` GREEN exits 0. `python3 scripts/run_func_model_perf_signoff.py baseline validate --baseline config/baselines/func_model_perf_spec_v1.json --require-fresh` exits 0. `python3 scripts/run_func_model_perf_signoff.py negative --case baseline --faults accept-current,stale-spec,hidden-hard-gate` exits 0 only with `rejected=3,accepted=0`. Evidence `.omo/evidence/task-22-regression-baseline.json`.
  Commit: Y | test(perf-spec): add versioned performance baseline | Files baseline manifest/validator/tests

- [ ] 23. Add portable CI and full signoff orchestration
  What to do: Add Python 3.10 CI job for all hard gates and no-RTL evidence validation. Implement `run --all-spec`, `validate --require-fresh`, `audit`, `rerun`; no EDA host labels/modules. Pin dependency install command and enforce full runtime/RSS limits.
  Parallelization: Y | Wave 5 | Blocks T25 | Blocked by T4,T16-T22
  References: `.github/workflows/caduceus-core-ci.yml:28-157` current jobs；`requirements.txt`；new runner。
  Acceptance criteria: exact CI commands pass locally and in workflow-compatible environment；scan proves no VCS/RTL command in job；stale/timeout/RSS breach fails nonzero.
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py run --all-spec --ci-mode` exits 0 within frozen limits. `python3 scripts/run_func_model_perf_signoff.py validate --require-fresh --require-done-claims 1-22` exits 0. `python3 scripts/run_func_model_perf_signoff.py negative --case ci --faults vcs-command,rtl-path,previous-head,timeout,rss-limit` exits 0 only with `rejected=5,accepted=0`. Evidence `.omo/evidence/task-23-perf-spec-ci.txt`.
  Commit: Y | ci(perf-spec): add portable performance spec gates | Files workflow/runner/tests

- [ ] 24. Reconcile documentation and performance-model bug governance
  What to do: Update performance docs/checklists to say spec-stage/uncalibrated/estimated, correct Qwen parameters and generated results, document assumptions/uncertainty/future RTL calibration phase. Record every current hard-gate defect in `docs/bugs/bugs-soc-func-model.md` with severity/owner/evidence；zero waivers.
  Parallelization: Y | Wave 5 | Blocks T25 | Blocked by T16-T22
  References: `docs/arc_vs_func.md` cycle-accurate overclaim；`docs/func_model_performance_analysis.md` stale dimensions；`docs/func-model-e2e-performance-analysis.md` corrected history；signoff checklist/bug ledger。
  Acceptance criteria: semantic checker rejects cycle-accurate/RTL-calibrated claims, stale Qwen parameters, KPI-as-gate and missing assumptions；all hard-gate bugs closed or signoff remains false.
  QA scenarios: `python3 scripts/check_func_model_perf_docs.py --spec config/func_model_perf_spec_v1.json --bugs docs/bugs/bugs-soc-func-model.md` exits 0 with zero blocking open defects. `python3 scripts/check_func_model_perf_docs.py --negative-fixtures config/tests/docs_cycle_accurate.md,config/tests/docs_old_qwen.md,config/tests/docs_kpi_gate.md` exits 0 only with `rejected=3,accepted=0`. Evidence `.omo/evidence/task-24-doc-consistency.txt`.
  Commit: Y | docs(perf-spec): publish Func Model performance spec status | Files non-RTL docs/checkers/bug ledger

- [ ] 25. Execute one clean, fresh performance-spec signoff
  What to do: Run baseline capture, provider/formula gates, Qwen/CV dual paths, sweeps, scaling, uncertainty reports, adversarial matrix and docs validation under one new run ID. Validate T1-T25 DoneClaims and exact HEAD/hashes；do not edit evidence to repair failure.
  Parallelization: N | Wave 5 | Blocks F1-F4 | Blocked by T21-T24
  References: all prior artifacts；`scripts/run_func_model_perf_signoff.py`。
  Acceptance criteria: `python3 scripts/run_func_model_perf_signoff.py run --all-spec` and `python3 scripts/run_func_model_perf_signoff.py validate --require-fresh --require-done-claims 1-25 --protected-baseline-from-plan .omo/plans/func-model-performance-infra-calibration-closure.md` exit 0；structural exactness, every formula row, seven workload cases, all sweeps and adversarial outcomes pass；three protected files match frozen pre/post SHA-256；product KPIs report-only；`calibration_state=uncalibrated`。
  QA scenarios: `python3 scripts/run_func_model_perf_signoff.py validate --require-fresh --require-done-claims 1-25 --repeat 2` exits 0 with identical canonical hashes. `python3 scripts/run_func_model_perf_signoff.py negative --case final-bundle --faults source,spec,oracle,workload,report,claim,protected-file` exits 0 only with `rejected=7,accepted=0`. Evidence `.omo/evidence/task-25-func-model-perf-spec-signoff.json` plus bundle.
  Commit: N | verification-only

## Final verification wave (after ALL todos)
> Runs in parallel. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
  Run `python3 scripts/run_func_model_perf_signoff.py audit --run-id-from .omo/evidence/task-25-func-model-perf-spec-signoff.json --plan .omo/plans/func-model-performance-infra-calibration-closure.md --require-done-claims 1-25 --recompute --evidence .omo/evidence/final-perf-spec-plan-compliance.md`. Expected exit 0, `verdict=approve`, 25 valid claims and independently recomputed gates.
- [ ] F2. Architecture and code-quality audit
  Run `PYTHONPATH=sim python3 -m pytest sim/timing/tests sim/tests/test_mmio_perf_events.py -q`. Run `python3 scripts/run_func_model_perf_signoff.py audit --checks event-source,numerical-separation,oracle-independence,no-rtl,typed-errors --evidence .omo/evidence/final-perf-spec-architecture.md`. Expected both exit 0 and the audit records `verdict=approve`；one event source, no provider/oracle import leak, no live RTL read/hash/dependency or fake measured cycles.
- [ ] F3. Real agent QA
  Run `python3 scripts/run_func_model_perf_signoff.py rerun --cases mxu-spec,sfu-vector-spec,dma-dram-spec,noc-kv-spec,qwen-blk0,qwen-prefill-128,mobilenetv3,resnet50,yolov8n --faults stale-state,misleading-success-output,zero-event,rtl-evidence --evidence .omo/evidence/final-perf-spec-real-qa.json`. Expected exit 0, all nine positives fresh-pass and all four faults `rejected=true`.
- [ ] F4. Scope and claim fidelity audit
  Run `python3 scripts/run_func_model_perf_signoff.py audit --checks scope,provenance,uncertainty,report-only,dirty-worktree --require-zero-waivers --protected-baseline-from-plan .omo/plans/func-model-performance-infra-calibration-closure.md --evidence .omo/evidence/final-perf-spec-scope-fidelity.md`. Expected exit 0 and `verdict=approve`；no `rtl/**` read/hash/edit or VCS execution, all KPI targets non-gating, protected files match frozen SHA-256 before/after, state remains uncalibrated.

## Commit strategy
- Work on `main` under established project constraint；one atomic conventional commit per T1-T24，implementation + tests together；T25 verification-only。
- Stage only each todo's declared non-RTL files；before commit record `git status --short` and staged list；never stage unrelated `.omo` files。
- Generated canonical reports may be tracked only with generator command/spec/workload/provider hashes；volatile evidence remains under run-ID bundle。
- Spec/provider revisions are explicit and reversible by version ID；never mutate a released spec artifact in place。

## Success criteria
- Func Model performance result is formally `performance_spec_verified=true, calibration_state=uncalibrated`，没有 cycle-accurate/RTL-calibrated overclaim。
- MMIO semantic events、architectural providers、independent oracle、canonical workloads和critical-path reports形成单一可追溯链路。
- Provider matrix每 case通过 `<=10%`/small absolute gate，七个 hard workload 的 total/major breakdown通过 `<=20%`，所有 structural/sweep/bottleneck gates通过。
- Qwen workload统一为 2048/11008/36/16/2，CV workload counts稳定，无重复 authoritative builder。
- KPI low/base/high正确、目标差距透明且 report-only；SW overhead不进入 canonical total。
- 当前签收不依赖 RTL/VCS/Spike/网络，未来 RTL calibration schema已预留但不参与 verdict。
- Fresh T25 bundle、全部 adversarial outcomes、25 DoneClaims和F1-F4均通过，且由用户最终确认后才宣布阶段关闭。
