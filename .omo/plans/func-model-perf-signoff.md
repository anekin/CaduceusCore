# func-model-perf-signoff - Work Plan

## TL;DR (For humans)

**What you'll get:** Caduceus Func Model 的性能模型达到签收级别——8 个 timing model 公式正确性已验证，CoreTimeline 重叠/串行/非双重计数无缺陷，Spike+firmware 调度链 cycle 开销已测量，Metrics（TPS/TTFT/TPOT）派生路径可信。Func Model 自此可以作为 SoC RTL 的性能 golden reference。

**Why this approach:** Func Model 是先于 RTL 存在的 spec——性能验证不依赖 RTL 仿真，而是对照架构设计意图（tile 数学、pipeline depth、DMA burst 公式）和自我一致性（sweep/scaling/瓶颈转移）。两条路径并行：benchmark.py 的纯分析路径覆盖引擎公式，Spike+firmware 路径覆盖调度开销。E2E 分析已发现并修复 5 个 bug（双重计数、DMA effective 误入 wall_keys 等），验证基础设施（60+ pytest）已存在，以补全覆盖面和签收证据为主。

**What it will NOT do:** 不跑 RTL VCS 仿真做 cycle 校准，不修 RTL 端 DMA readback 硬件 bug，不定义 RTL-numeric 性能阈值（性能是 architectural-spec-accurate）。不重复 func-model-signoff-v2 已覆盖的功能正确性验证。SWOverheadModel 暂不集成进主 timing pipeline。

**Effort:** Large — 16 tasks across 7 components + CV + Prefill, 双路径并行, 约 3-4 天
**Risk:** Medium — Spike+firmware 路径的 perf measurement 基础设施需要新建；SWOverheadModel 未独立测试；DMAModel tile double-buffer overlap 已有测试但需扩展到更多维度；NoC/DMA breakdown-only 行为边界已知但未形式化验证
**Decisions to sanity-check:** 两条路径都做（用户已选）；沿用 func-model-signoff runner + atomic evidence 模式；精度标准 = architectural-spec-accurate

## Scope
### Must have
- 8 个 timing model 的 estimate() 公式与架构设计规格一致性验证 (BlockEngine, SFUModel, VectorModel, DMAModel, NoCModel, KVCacheModel, DRAMModel, SWOverheadModel)
- CoreTimeline overlap/串行/非双重计数正确性验证——DMA/NoC breakdown-only 不推进 wall clock，wall_keys 只含 mxu/sfu/vector/kv + FM-1 cross-engine gap (crossbar_wait/sram_stall/vcov_bubble)
- Spike+firmware 路径——firmware (npu_firmware.c) 在 Spike 上执行的 MMIO dispatch chain + weight management (per-K-tile 流式重载) + 调度链 cycle 开销与 SWOverheadModel 的一致性
- Metrics 派生路径验证——TPS/TTFT/TPOT/ITL/DMA overlap ratio/BW utilization/NoC latency/MAC utilization
- 跨模型一致性——DMA channel sweep (1/2/4/8) TPS 单调变化；NoC topology sweep；模型 scaling (1.5B→3B→7B) cycles 合理增长；瓶颈转移方向正确 (BW-bound vs compute-bound)
- 跨路径一致性——分析路径 (benchmark.py) 与 firmware 路径 (Spike+SWOverhead) 同一配置下 cycle 差异可解释
- CV 模型性能签收——5 个注册 CV 模型 (mobilenetv3-small/resnet18/resnet50/yolov8n/vit-b16) 的 FPS / inference_latency 验证 + im2col→GEMM 开销合理性
- Prefill 路径泛化——将 `simulate_prefill` 的硬编码 Qwen2.5-3B trace 替换为 `_build_llm_trace(model_spec, m)` 通用 trace 构建，验证 1.5B/3B/7B 不同 prefill_len 的 TTFT 一致性
- 性能签收 checklist + evidence runner 框架
- 所有工作在 `main` 分支上推进

### Must NOT have
- NO RTL VCS 仿真 / RTL cycle count 校准
- NO RTL 端 bug 修复（DMA output readback zero、M=32 doorbell stale、SFU/Vector module-level perf testbench）
- NO 36 层全模型 RTL forward pass
- NO RTL-numeric 性能精度阈值——性能是 architectural-spec-accurate，不是 RTL-accurate
- NO config-driven SWOverheadModel integration — verify standalone model; main timing pipeline integration deferred
- Binding constraints (verbatim from user):
  1. "以后设计验证的工作都在main分支上推进"
  2. "涉及到工具调用，环境变量设置，都用脚本方式"
  3. "所有验证都在sz0001上进行" — EDA/VCS on sz0001; Python pytest runs locally
  4. "对于bug，一定要记录到bug track文件" — 性能模型bug记录到 `docs/bugs/bugs-soc-func-model.md`（和功能模型bug同文件）

## Verification strategy
> **"Architectural-spec-accurate" 操作化定义**：对每个模型，PASS 意味着以下三者全部成立：①公式可独立核算（外部参照物与代码结果一致）；②在 sweep/scaling 下行为单调合理（没有负 TPS、越少通道越快的悖论）；③与跨路径/跨模型的其他模块联合运行时无双重计数或矛盾。FAIL 的具体条件：公式核算偏差 > 20%、sweep 方向反向（TPS 降序）、wall_keys 含 breakdown-only key（双重计数回归）、跨路径 engine delta > 10%。对于 F1-F4 reviewer，每个 evidence 文件中 `SIGNOFF_METRIC` 记录必须包含核实的公式核算结果（如 `tile_base=68, source=arch_pipeline_depth`），仅 "证据存在" 不构成通过。
> **外部参照物（打破循环验证）**：每个 timing model 的公式不是与自身代码对照，而是与**可独立核算的架构事实**对照：
> - **MXU**: per-tile formula `H + broadcast_sync(2) + accumulate(2) = 68` — 从 `mxu-perf-calibration.md` 的 18 行 RTL 校准表中提取（是已验证的架构事实，不是公式重述）
> - **SFU**: 7 ops 的 latency 与**RTL pipeline depth** 对照——`sfu_top.v` 中每个模块的 FSM stage 数（softmax=8 stages, layernorm=6, gelu=4, silu=4, rmsnorm=6, rope=16, exp=12）——这是架构结构，不是仿真结果
> - **Vector**: type_convert(260) 从**INT32→FP16 转换 pipeline** 逐阶段分解：sign_extract + exp_denorm + normalize + round + pack = 5 stages, per-128-batch 流水线填充 + flush = 2×(ceil(128/width)+pipeline_depth), 核算得 260 合理
> - **DMA**: `estimate_transfer(bytes)` 与 `bytes / config_bw` 的**简单带宽上限**对照（DMA 开销应 ≥ 纯带宽延迟）
> - **NoC**: crossbar(1-hop) + serialization + arbitration——与 `npu_config.yaml` interconnect 参数核算
> - **KV Cache**: 80-cycle DRAM miss 从 DRAMModel.tRC + tCAS 可独立推算
> - **DRAM**: tRFC/tREFI 与 JEDEC LPDDR5 标准对照
> - **SWOverhead**: 与 `npu_firmware.c` 的**静态指令计数**（`riscv64-unknown-elf-objdump -d`）对照，乘以 CPI=1.2 独立验证
> 
> 这些参照物是**可独立核查的架构事实**，不依赖 RTL 仿真，但也不是代码自身的公式复述。
> Zero human intervention - all verification is agent-executed.
- **分析路径**: pytest for timing model unit tests (60+ existing in `sim/timing/tests/`, 补充覆盖率缺口); signoff runner `scripts/run_func_model_perf_signoff.py` with perf case registry, atomic evidence (`.omo/evidence/perf-*.txt`), `validate` command
- **Firmware 路径**: Spike + firmware 在 Func Model 内执行，采集 cycle 数据。SWOverheadModel 预测 vs 实测 delta。需新建 Spike perf measurement harness (`sim/spike_perf_runner.py`)
- **跨路径对比**: 分析路径 TPS/TTFT vs firmware 路径 TPS/TTFT 的差异分析脚本
- **Sweep/Scaling**: DMA/NoC sweep via benchmark.py CLI, model scaling via `--all` family
- **CV 路径**: benchmark.py CV mode — 5 个注册 CV 模型的 trace 生成 → FPS/inference_latency 验证 + MXU/DMA/NoC 公式在 CV 上下文的一致性
- **Prefill 泛化**: 重构 `npu_sim.py:simulate_prefill` → 调用 `_build_llm_trace()`，使 prefill 支持任意 ModelSpec，验证不同 model/prompt_len 的 TTFT 自洽
- **Evidence**: atomic write, source-fingerprint, stale-HEAD detection
- **Bug tracking**: Any discrepancy found recorded to `docs/bugs/bugs-soc-func-model.md` (binding constraint #4)

## Execution strategy
### Parallel execution waves
1. **Wave 0:** T0A (perf signoff runner framework) + T0B (Spike+firmware perf harness)
2. **Wave 1** (after T0A): T1-T4 = MXU/SFU/Vector/DMA formula verification — parallel
3. **Wave 2** (after T1-T4): T5-T7 = NoC/KVCache/DRAM formula verification — parallel
4. **Wave 3** (after T0B): T8 = SWOverheadModel + firmware dispatch chain verification
5. **Wave 4** (after T8): T9 = CoreTimeline overlap/concurrency verification
6. **Wave 5** (after T1-T7): T10 = Metrics derivation verification
7. **Wave 6** (after T10+T8): T11 = Cross-path consistency (analysis vs firmware)
8. **Wave 7** (after T12+T10): T14 + T15 = CV perf signoff + Prefill generalization — parallel
9. **Wave 8** (after T9+T10+T11+T12+T14+T15): T13 = Perf signoff checklist + docs
10. **Final wave** (after T13): F1-F4 parallel

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T0A | none | all evidence tasks | T0B |
| T0B | none | T8 | T0A |
| T1 | T0A | T9, T10, T14 | T2, T3, T4 |
| T2 | T0A | T9, T10, T14 | T1, T3, T4 |
| T3 | T0A | T9, T10, T14 | T1, T2, T4 |
| T4 | T0A | T9, T10, T12, T14 | T1, T2, T3 |
| T5 | T0A | T9, T10, T14 | T6, T7 |
| T6 | T0A | T9, T10, T14 | T5, T7 |
| T7 | T0A | T9, T10, T14 | T5, T6 |
| T8 | T0B | T11 | T1-T7 |
| T9 | T1-T7 | T11, T13 | — |
| T10 | T1-T7 | T12, T13, T15 | — |
| T11 | T8, T9, T10 | T13 | — |
| T12 | T4, T10 | T13, T14, T15 | — |
| T14 | T1-T7, T12 | T13 | T15 |
| T15 | T10, T12 | T13 | T14 |
| T13 | T9, T10, T11, T12, T14, T15 | final wave | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [ ] 0. Perf signoff evidence runner framework (T0A)
  What to do: Extend `scripts/run_func_model_signoff.py` (or create parallel runner `scripts/run_func_model_perf_signoff.py`) to handle performance evidence cases. Each perf case maps to: argv list (subprocess, shell=False), evidence path under `.omo/evidence/perf-*.txt`, expected exit, required `SIGNOFF_METRIC` records (including `timing.tps`, `timing.ttft`, `timing.tpot`, `module_breakdown.{mxu,sfu,vector,dma_weight,dma_effective,kv_cache,noc_latency,noc_contention}`), source-fingerprint, stale-HEAD detection. Atomic evidence writes (temp file + rename). Metric validation: verify wall-clock modules (mxu+sfu+vector+kv) sum ≤ total_cycles; verify dma_effective / dma_weight ≤ total_cycles; verify negative-cycle rejection. Create `sim/tests/test_func_model_perf_signoff_runner.py` — unit cases: PASS metric, missing metric, stale HEAD, stale fingerprint, negative cycle, wall-clock overflow.
  Must NOT do: Do NOT use `shell=True`. Do NOT infer PASS from terminal text. Do NOT accept zero-test as PASS.
  Parallelization: Wave 0 | Blocked by: none | Blocks: all evidence-producing tasks
  References: `scripts/run_func_model_signoff.py` (func-model-signoff-v2 runner pattern), `sim/timing/types.py:15-18` (ModuleBreakdown keys), `sim/timing/timing_engine.py:17` (MODULE_KEYS list), `sim/timing/metrics.py` (MetricsCollector)
  Acceptance criteria: `PYTHONPATH=sim python3 scripts/run_func_model_perf_signoff.py run --case t0a-runner-smoke` exits 0; `.omo/evidence/perf-t0a-runner-smoke.txt` contains `evidence.verdict: pass`; `python3 -m pytest sim/tests/test_func_model_perf_signoff_runner.py -q` passes 6+ unit cases.
  QA scenarios: Happy = runner validates all unit cases. Failure = metric missing → FAIL, zero test → FAIL, stale fingerprint → FAIL. Evidence: `.omo/evidence/perf-t0a-runner-smoke.txt`
  Commit: Y | feat(func-model-perf-signoff): add perf signoff evidence runner framework

- [ ] 1. Spike+firmware perf measurement harness (T0B)
  What to do: Create `sim/spike_perf_runner.py`. **IMPORTANT**: `spike_host.py` currently has ZERO cycle measurement capability — it launches Spike with no `--log-commits`, no `mcycle` CSR tracking, no perf plugin. T0B must add at least one of: (a) modify the Spike MMIO plugin to log per-operation instruction counts; (b) instrument firmware to read `mcycle` CSR at MMIO boundaries; or (c) use a static instruction-count analysis of the compiled firmware (riscv64-objdump) as fallback — count instructions in the dispatch loop and multiply by CPI=1.2. The fallback path (c) is the **recommended primary approach**: it avoids Spike plugin modifications and provides a structural validation of SWOverheadModel's per-op counts. The dynamic path (a/b) can be added if time permits. Create `sim/tests/test_spike_perf_harness.py` — validate the static analysis produces non-zero instruction counts that are geometrically consistent with firmware structure (e.g., MMIO write sequence ~N instructions per op).
  Must NOT do: Do NOT simulate on RTL/Ibex — use Spike only. Do NOT require full 36-layer forward pass.
  Parallelization: Wave 0 | Blocked by: none | Blocks: T8 | Can parallelize with: T0A
  References: `sim/spike_host.py` (Spike integration), `firmware/npu_firmware.c` (firmware dispatch), `sim/models/sw_overhead.py` (SWOverheadModel), `patches/` (Spike patches)
  Acceptance criteria: `PYTHONPATH=sim python3 sim/spike_perf_runner.py --workload mmul_smoke` exits 0 and outputs JSON with `total_riscv_cycles > 0, engine_busy_cycles > 0`. Unit tests pass.
  QA scenarios: Happy = harness returns structured cycle data. Failure = Spike ABI crash or zero-cycle output → FAIL (check plugin build). Evidence: `.omo/evidence/perf-t0b-spike-harness.txt`
  Commit: Y | feat(func-model-perf-signoff): add Spike+firmware perf measurement harness

- [ ] 2. MXU/BlockEngine cycle formula verification (T1)
  What to do: Verify `sim/engine/block_engine.py:BlockEngine.estimate()` cycle formulas against architectural design. Check: (a) tile_base = H + broadcast_sync(2) + accumulate(2) = 68 for H=64 — confirmed against `test_engines.py` and `mxu-perf-calibration.md` line 48 ("per-tile cycle formula matches RTL exactly"); (b) ping-pong total = first_cold + (ceil(N/H)-1) * max(compute, dma); (c) weight_cache_pair gate+up merge correctly computes 2× tiles with shared fill; (d) M=1 decode: only 1 row active — tile_base unchanged but utilization < 1% is correct architectural fact; (e) M=prefill (M>8): includes fill+drain overhead correctly. Cross-check per-tile cycle against `test_engines.py` baseline and `mxu-perf-calibration.md` 18-row table for delta = 0 on compute-only cases. Verify that P2 measured data (weight_streaming_overlap_ratio=0.95 vs predicted 0.98, 3.1% delta) is within architectural tolerance for analytical model.
  Must NOT do: Do NOT reimplement the engine; verify existing code. Do NOT require RTL VCS simulation — architectural verification only.
  Parallelization: Wave 1 | Blocked by: T0A | Blocks: T9, T10 | Can parallelize with: T2, T3, T4
  References: `sim/engine/block_engine.py:36-284` (BlockEngine.estimate), `sim/engine/mac_engine.py` (MACEngine base), `docs/mxu-perf-calibration.md:1-69` (per-tile formula validation), `build/evidence/w4-perf-p2.txt` (PERF-12 overlap ratio)
  Acceptance criteria: Per-tile formula verification produces evidence with: tile_base=68 (H=64), ping-pong model verified, weight_cache_pair model verified, M=1 utilization < 1% documented. Evidence: `.omo/evidence/perf-t1-mxu-formula.txt`
  QA scenarios: Happy = tile_base matches architectural spec, ping-pong formula matches config, weight_cache_pair correctly adds 2× tiles. Failure = tile_base disagrees with spec → check BlockEngine code. Evidence: `.omo/evidence/perf-t1-mxu-formula.txt`
  Commit: Y | test(func-model-perf-signoff): verify MXU BlockEngine cycle formulas

- [ ] 3. SFUModel cycle formula verification (T2)
  What to do: Verify `sim/models/sfu.py:SFUModel.estimate()` cycle formulas. Check: (a) batch = ceil(elements/width) × latency[op] — width=16 (FP16), latencies: softmax=8, exp=12, div=16, layernorm=6, rmsnorm=6, gelu=4, silu=4, rope=12; (b) element count derivation for each op (e.g., softmax: N × seq_len loads = ceil(input_elements/16) batches, plus RSQRT + multiply; rmsnorm: two-pass: square_sum + normalize); (c) verify that SFU is ALWAYS serial after MXU (data-dependent: softmax needs O_proj output, rmsnorm needs input_embedding output); (d) verify per-op breakdown agrees with NPUSimulator trace (per-GEMM SFU events in simulation loop). Cross-check: `test_golden_sfu_gaps.py` asserts SFU latencies are positive and element counts match manifest op descriptions.
  Must NOT do: Do NOT verify SFU mathematical correctness (covered by func-model-signoff-v2 F-FM-02). Do NOT require RTL SFU perf testbench (tb_sfu_perf.v).
  Parallelization: Wave 1 | Blocked by: T0A | Blocks: T9, T10 | Can parallelize with: T1, T3, T4
  References: `sim/models/sfu.py:13-138`, `sim/npu_sim.py` (SFU/Vector in decode/prefill loops), `sim/tests/test_golden_sfu_gaps.py`, `docs/func-model-golden-tolerance.md`
  Acceptance criteria: SFU latency table verified against ops (7 ops × correct latency), element count derivation correct, serial-after-MXU confirmed. Evidence: `.omo/evidence/perf-t2-sfu-formula.txt`
  QA scenarios: Happy = all 7 ops' batch×latency matches expected. Failure = latency off by >1 cycle → check SFUModel code. Evidence: `.omo/evidence/perf-t2-sfu-formula.txt`
  Commit: Y | test(func-model-perf-signoff): verify SFUModel cycle formulas

- [ ] 4. VectorModel cycle formula verification (T3)
  What to do: Verify `sim/models/vector.py:VectorModel.estimate()` cycle formulas. Check: (a) batch = ceil(elements/width) × latency[op] — width=128, latencies: add/mul/scale=1, max/sum/reduce=12, type_convert(conv_f16_i32)=260, resid_add=5; (b) the 260-cycle type_convert latency is the largest single-op cost in Vector — verify it reflects INT32→FP16 conversion pipeline depth (include load INT32→convert→store FP16 stages); (c) element count derivation for each op (e.g., resid_add: hidden elements = 2048, ceil(2048/128) = 16 batches × 5 = 80 cycles); (d) verify per-op breakdown in NPUSimulator trace; (e) verify that Vector is serial after SFU (with 4-cycle FM-1 gap).
  Must NOT do: Do NOT verify Vector mathematical correctness (covered by func-model-signoff-v2 F-FM-04). Do NOT require RTL Vector perf testbench (tb_vector_perf.v).
  Parallelization: Wave 1 | Blocked by: T0A | Blocks: T9, T10 | Can parallelize with: T1, T2, T4
  References: `sim/models/vector.py:12-82`, `sim/npu_sim.py` (SFU/Vector loops), `docs/func-model-golden-tolerance.md`
  Acceptance criteria: Vector latency table verified (6 ops), type_convert 260-cycle justification documented, per-op batch count matches element shapes. Evidence: `.omo/evidence/perf-t3-vector-formula.txt`
  QA scenarios: Happy = all 6 ops match expected latency × batch. Failure = type_convert 260 unexplained → investigate pipeline depth rationale. Evidence: `.omo/evidence/perf-t3-vector-formula.txt`
  Commit: Y | test(func-model-perf-signoff): verify VectorModel cycle formulas

- [ ] 5. DMAModel cycle formula + tile double-buffer overlap verification (T4)
  What to do: Verify `sim/models/dma.py:DMAModel.estimate_transfer()` and `estimate_tile_double_buffer_overlap()`. Check: (a) estimate_transfer: bytes/eff_bw + burst_overhead(cycles_per_burst × num_bursts) + descriptor_overhead + FIFO backpressure; (b) tile double-buffer overlap: first_tile_cold + (N_tiles-1) × max(compute, dma) — verify compute = tile_base (from T1), dma = weight_bytes/burst_bw; (c) per-K-tile weight streaming reload: verify total = first_K_tile_load + (K_tiles-1) × max(compute, dma_reload); (d) DMA overlap ratio = dma_effective / (dma_weight + dma_effective) — verify that PERF-12 ratio 0.95 vs predicted 0.98 (3.1% delta) is within architectural tolerance; (e) multi-channel arbitration (round_robin/fixed_priority): verify channel selection affects total bandwidth but NOT overlap ratio. Extend `sim/timing/tests/test_tile_double_buffer.py` to cover K-tile reload scenarios (currently 7 tests for 7 GEMM types).
  Must NOT do: Do NOT verify against RTL DMA simulation. Do NOT fix RTL DMA readback zero bug. — covered by func-model-signoff-v2
  Parallelization: Wave 1 | Blocked by: T0A | Blocks: T9, T10, T12 | Can parallelize with: T1, T2, T3
  References: `sim/models/dma.py:33-338` (DMAModel), `sim/timing/tests/test_tile_double_buffer.py` (existing 7 tests), `build/evidence/w4-perf-p2.txt` (PERF-12 overlap), `sim/config/npu_config.yaml` (DMA channels, burst size)
  Acceptance criteria: Tile double-buffer model verified for all 7 GEMM shapes (K=2048,N=2048 etc.), per-K-tile reload modeled, overlap ratio formula verified. Test count ≥ 14 (7 existing + 7 new K-tile reload cases). Evidence: `.omo/evidence/perf-t4-dma-formula.txt`
  QA scenarios: Happy = all 14 tile DB tests pass, overlap ratios in [0, 1], no negative cycles. Failure = overlap > 1 (counting bug) or dma_effective > total (breakdown error). Evidence: `.omo/evidence/perf-t4-dma-formula.txt`
  Commit: Y | test(func-model-perf-signoff): verify DMAModel cycle formulas + K-tile reload

- [ ] 6. NoCModel cycle formula + FM-1 overhead verification (T5)
  What to do: Verify `sim/models/noc.py:NoCModel.estimate_transfer()` and overhead methods. Check: (a) serial_cycles = ceil(bytes / flit_capacity) × flit_time; (b) hop_latency = hops × per_hop_cycles (crossbar hops=1, mesh hops=√ports); (c) arbitration penalty when concurrent accesses (contention = wait_for_grant × concurrent_requesters); (d) FM-1 overhead: crossbar_wait(2) + sram_stall(1) + vcov_bubble(1) = 4 cycles per engine switch — verified in `CoreTimeline` and confirmed by W4 PERF-16 (cross_engine_gap=4); (e) NoC is BREAKDOWN ONLY — `add_noc()` records events without advancing wall clock (verify in CoreTimeline); (f) self-transfer (src=0,dst=0) in single-core: `noc_contention=0` because no concurrent requesters. Cross-check: `sim/timing/tests/test_cross_engine.py`.
  Must NOT do: Do NOT require multi-master concurrent RTL simulation. Do NOT modify NoCModel — verify existing code.
  Parallelization: Wave 2 | Blocked by: T0A | Blocks: T9, T10 | Can parallelize with: T6, T7
  References: `sim/models/noc.py:30-222`, `sim/timing/tests/test_cross_engine.py`, `sim/engine/timeline.py:130-144` (FM-1 injection), `build/evidence/w4-perf-review-gate.txt:92-98`
  Acceptance criteria: NoC basic transfer formula verified; FM-1 4-cycle gap breakdown confirmed; breakdown-only behavior verified (wall clock unchanged by add_noc); contention=0 for single-core confirmed. Evidence: `.omo/evidence/perf-t5-noc-formula.txt`
  QA scenarios: Happy = NoC formula matches config (flit_width, bw, topology), FM-1 matches PERF-16. Failure = noc_contention > 0 in single-core → check event attribution. Evidence: `.omo/evidence/perf-t5-noc-formula.txt`
  Commit: Y | test(func-model-perf-signoff): verify NoCModel cycle formulas + FM-1 overhead

- [ ] 7. KVCacheModel cycle formula verification (T6)
  What to do: Verify `sim/models/kv_cache.py:KVCacheModel.estimate_per_decode()`. Check: (a) SRAM hit = 2 cycles per KV entry, DRAM miss = 80 cycles; (b) amortized formula: hit ≥ 85% → 20 cycles/layer (0.85×2 + 0.15×80 ≈ 13.7, rounded up with buffer); (c) layer_switch_cost = sram_bytes/bw × 0.3 — verify sram_bytes = model.hidden × model.kv_heads × 2 × sizeof(FP16) and bw from config; (d) per-layer total = per_GEMM_cost + layer_switch — verify no double-counting across layers; (e) KV Cache is serial with compute (add_kv advances wall clock).
  Must NOT do: Do NOT verify multi-layer functional correctness (covered by func-model). Do NOT require RTL KV simulation.
  Parallelization: Wave 2 | Blocked by: T0A | Blocks: T9, T10 | Can parallelize with: T5, T7
  References: `sim/models/kv_cache.py:14-144`, `sim/model_specs.py` (ModelSpec for KV sizes), `sim/npu_sim.py` (add_kv in simulation loop)
  Acceptance criteria: SRAM hit/miss latencies verified, amortized formula produces 20 cycles/layer, layer_switch_cost documented. Evidence: `.omo/evidence/perf-t6-kvcache-formula.txt`
  QA scenarios: Happy = KVCache formulas match architectural spec. Failure = layer_switch negative or > expected → check bytes/bw derivation. Evidence: `.omo/evidence/perf-t6-kvcache-formula.txt`
  Commit: Y | test(func-model-perf-signoff): verify KVCacheModel cycle formulas

- [ ] 8. DRAMModel cycle formula verification (T7)
  What to do: Verify `sim/models/dram.py:DRAMModel.effective_bandwidth()`. Check: (a) raw_bw from config (e.g., LPDDR5-6400 51.2 GB/s at 1 GHz = 51.2 bytes/cycle); (b) refresh overhead = tRFC / tREFI — verify against JEDEC LPDDR5 spec: tRFC=210ns, tREFI=3.9μs → 210/3900 = 5.38%; (c) row conflict penalty = 15% probability × 30% extra latency; (d) effective_bw = raw_bw × (1 - refresh_overhead) × (1 - row_conflict × 0.30); (e) verify that DRAMModel produces non-zero, non-negative effective_bw for all supported memory types (LPDDR5-32b through HBM3).
  Must NOT do: Do NOT verify against RTL DRAM simulation. Do NOT modify DRAM model — verify existing.
  Parallelization: Wave 2 | Blocked by: T0A | Blocks: T9, T10 | Can parallelize with: T5, T6
  References: `sim/models/dram.py:6-91`, `sim/config/npu_config.yaml` (memory section), JEDEC LPDDR5 spec (tRFC/tREFI)
  Acceptance criteria: Refresh overhead formula verified (5.4%), row conflict verified, effective_bw produced for all memory types. Evidence: `.omo/evidence/perf-t7-dram-formula.txt`
  QA scenarios: Happy = DRAM effective_bw matches architectural formula. Failure = effective_bw > raw_bw (overflow) or zero → check formula. Evidence: `.omo/evidence/perf-t7-dram-formula.txt`
  Commit: Y | test(func-model-perf-signoff): verify DRAMModel cycle formulas

- [ ] 9. SWOverheadModel + firmware dispatch chain verification (T8)
  What to do: Verify `sim/models/sw_overhead.py:SWOverheadModel` against Spike+firmware measurement data (from T0B). **IMPORTANT**: SWOverheadModel is currently a STANDALONE analytical model — it is NOT instantiated in `NPUSimulator`, NOT integrated into `TimingEngine`/`ModuleBreakdown`/`wall_keys`, and does NOT affect reported TPS/TTFT/TPOT/ITL. This task verifies it as a standalone model. Check: (a) RISC-V @ 1/5× MXU freq (hardcoded cycle_ratio=5, not reading `npu_config.yaml:riscv:`); (b) CPI=1.2 hardcoded (not config-driven); (c) fixed init + submit overhead = 200 RISC-V cycles (`fixed_init=80` + `fixed_submit=120` per `sim/models/sw_overhead.py:52-53`, MXU-equivalent = 200 × cycle_ratio = 1000); (d) per-layer barrier = 18 cycles (15 inst × CPI 1.2); (e) per-ISA-inst dispatch = 4.8 cycles; (f) compare against Spike measurement from T0B for MMUL chain workload. Delta ≤ 50% acceptable. Document that SWOverheadModel integration into the main timing pipeline is deferred (requires changes to NPUSimulator, TimingEngine, ModuleBreakdown, metrics).
  Must NOT do: Do NOT rewrite firmware — verify existing. Do NOT require full 36-layer pass.
  Parallelization: Wave 3 | Blocked by: T0B | Blocks: T11 | Can parallelize with: T1-T7
  References: `sim/models/sw_overhead.py:41-150`, `sim/spike_perf_runner.py` (T0B output), `firmware/npu_firmware.c` (dispatch loop), `firmware/build/npu_firmware.elf` (objdump for static instruction count), `sim/spike_host.py`
  Acceptance criteria: SWOverheadModel CPI/multiplier verified; firmware MMIO dispatch cycles measured and within 50% of prediction; per-layer barrier cost verified. Evidence: `.omo/evidence/perf-t8-swoverhead.txt`
  QA scenarios: Happy = Spike measurement vs SWOverhead delta ≤ 50%. Failure = delta > 100% or zero cycles from Spike → check Spike perf harness or SWOverhead CPI assumptions. Evidence: `.omo/evidence/perf-t8-swoverhead.txt`
  Commit: Y | test(func-model-perf-signoff): verify SWOverheadModel + firmware dispatch chain

- [ ] 10. CoreTimeline overlap/concurrency verification (T9)
  What to do: Verify `sim/engine/timeline.py:CoreTimeline` event scheduling correctness. Check: (a) wall_keys set = (mxu, sfu, vector, kv_cache, crossbar_wait, sram_stall, vcov_bubble) — callout: `sim/timing/timing_engine.py:107-108` already includes ALL 7, NOT just 4 compute modules. These are the ONLY modules that advance wall clock. Verify that crossbar_wait(2) + sram_stall(1) + vcov_bubble(1) = 4 cycle overhead per engine switch is injected at `timeline.py:130-144`. (b) DMA/NoC are BREAKDOWN ONLY: add_dma_parallel / add_noc → record event, restore _current_cycle to MXU end — verify that total_cycles does NOT include dma_weight/dma_effective/noc_latency/noc_contention. (c) DMAModel overlap: when DMA cycles ≤ mxu_busy_until - start, event is overlapped; otherwise extends wall clock (line 218-219); (d) SFU/Vector serial after MXU; (e) double-counting prevention: total_cycles = sum(wall_keys ONLY). Verify the 5 E2E bugs are fixed. Run: `PYTHONPATH=sim python -m pytest sim/timing/tests/test_timing_engine.py::test_total_equals_wall_clock_sum -v` and verify wall_keys sum / total_cycles ∈ [0.95, 1.05].
  Must NOT do: Do NOT modify timeline — verify existing code. Do NOT add RTL comparison.
  Parallelization: Wave 4 | Blocked by: T1-T7 | Blocks: T11, T13 | Can parallelize with: —
  References: `sim/engine/timeline.py:120-254` (CoreTimeline), `sim/timing/timing_engine.py:86-109` (wall_keys), `docs/func-model-e2e-performance-analysis.md:464-518` (5 bugs), `sim/npu_sim.py` (simulation loop)
  Acceptance criteria: Wall clock = sum(wall_keys) only; DMA/NoC breakdown-only confirmed; SFU/Vector serial + FM-1 gap confirmed; 5 E2E bugs verified fixed; wall_keys sum / total_cycles ∈ [0.95, 1.05]. Evidence: `.omo/evidence/perf-t9-timeline.txt`
  QA scenarios: Happy = wall clock matches sum of wall_keys, dma/noc excluded. Failure = total_cycles includes dma_effective → Bug 4 regression, report BLOCKER. Evidence: `.omo/evidence/perf-t9-timeline.txt`
  Commit: Y | test(func-model-perf-signoff): verify CoreTimeline overlap + concurrency correctness

- [ ] 11. Metrics derivation verification (T10)
  What to do: Verify `sim/timing/metrics.py:MetricsCollector` and `sim/timing/dashboard.py:Dashboard` metric formulas. Check: (a) TPS = freq_mhz × 1e6 / total_decode_cycles — verify decode_cycles comes from TimingEngine wall clock, not from user input; (b) TTFT = (prefill_cycles + first_decode_cycles) / (freq_mhz × 1e3) ms — verify prefill and first decode are separately measured; (c) TPOT = mean(decode_cycles[1:]) / freq_mhz μs — verify excludes token 0; (d) ITL = [cycles[i] / freq_mhz for i in 1..N-1] μs — verify list length = gen_len; (e) DMA overlap ratio = dma_effective / (dma_weight + dma_effective) — verify denominator ≠ 0; (f) BW utilization % = (dma_weight + dma_effective) / total_cycles × 100 — verify not > 100%; (g) MAC utilization < 1% for M=1 decode (known architectural fact, document as expected, not FAIL); (h) verify `sim/timing/dashboard.py` JSON output includes all required keys: tps, ttft_ms, tpot_us, itl_us_list, module_breakdown, dma_overlap_ratio, bandwidth_utilization_pct, noc_latency_us, noc_contention_pct. Cross-check: existing 60+ tests in `sim/timing/tests/`.
  Must NOT do: Do NOT redefine metrics — verify existing. Do NOT change dashboard output schema.
  Parallelization: Wave 5 | Blocked by: T1-T7 | Blocks: T12, T13 | Can parallelize with: —
  References: `sim/timing/metrics.py:19-134`, `sim/timing/dashboard.py:30-353`, `sim/timing/tests/test_metrics.py`, `sim/timing/tests/test_dashboard.py`
  Acceptance criteria: All 8 metric formulas verified; JSON schema keys present; existing 60+ tests pass; MAC utilization < 1% documented as architectural fact. Evidence: `.omo/evidence/perf-t10-metrics.txt`
  QA scenarios: Happy = metrics match architectural formulas, all tests green. Failure = TPS negative, TTFT missing token 0, DMA overlap > 1. Evidence: `.omo/evidence/perf-t10-metrics.txt`
  Commit: Y | test(func-model-perf-signoff): verify Metrics derivation for TPS/TTFT/TPOT

- [ ] 12. Cross-path consistency — analysis vs firmware (T11)
  What to do: Run the same workload configuration through BOTH paths and compare. Path A (analysis): `benchmark.py --model qwen2.5-3b --prompt-len 1 --gen-len 1` → get TPS, TTFT, module breakdown. Path B (firmware): `spike_perf_runner.py --workload mmul_chain_single_layer` → get total Spike cycles + SWOverheadModel prediction. Compare: (a) engine cycles (MXU+SFU+Vector) should agree within 10% — same compute, different counting granularity. This 10% derives from: MXU tile_base=68 is exact; SFU/Vector latency tables are exact from config; the only variance comes from timeline rounding (±1 cycle per event, ~128 events per layer = ~128 cycles out of ~15,000 = <1%). 10% is generous. (b) wall-clock delta ≤ 50% — driven entirely by SW overhead which SWOverheadModel captures at ±50% precision (T8). Cross-check that differences are attributable to: SW dispatch overhead, Spike vs analytical timing granularity, DMA descriptor overhead in firmware path not in analysis path. Record per-module delta table.
  Must NOT do: Do NOT require RTL comparison. Do NOT force exact match — document difference root cause.
  Parallelization: Wave 6 | Blocked by: T8, T9, T10 | Blocks: T13 | Can parallelize with: —
  References: `sim/timing/benchmark.py` (analysis path), `sim/spike_perf_runner.py` (T0B harness), `sim/models/sw_overhead.py` (SW overhead model), `docs/testcase-list_methodology.md:188-209` (Tier 2: record gap, no hard PASS/FAIL)
  Acceptance criteria: Cross-path comparison table produced; engine cycles delta ≤ 10%; wall-clock delta ≤ 50%; all differences documented with root cause. Evidence: `.omo/evidence/perf-t11-cross-path.txt`
  QA scenarios: Happy = engine cycles ≤ 10% delta, differences explained. Failure = engine delta > 50% or Spike returns zero cycles → investigate measurement path. Evidence: `.omo/evidence/perf-t11-cross-path.txt`
  Commit: Y | test(func-model-perf-signoff): cross-verify analysis vs firmware path consistency

- [ ] 13. Cross-model consistency — sweep/scaling/bottleneck (T12)
  What to do: Run multi-dimensional consistency checks. (a) DMA channel sweep: `--sweep-dma-channels 1,2,4,8` on Qwen2.5-3B → verify TPS increases monotonically with channel count (2 channels ≈ 2× BW, diminishing returns after 4); (b) NoC topology sweep: `--sweep-noc-topology crossbar,mesh --sweep-noc-ports 2,4,8` → verify crossbar_latency < mesh_latency (1 hop vs √ports hops); (c) model scaling: benchmark `qwen2.5-1.5b` (28 layers, hidden=1536), `qwen2.5-3b` (36 layers, hidden=2048), `qwen2.5-7b` (28 layers, hidden=3584) → verify per-layer decode_cycles / (hidden × intermediate) ratio is consistent (±15%) across models — normalize by compute-per-layer since dimensions differ; (d) bottleneck shift: compare LPDDR5-6400 config vs hypothetical high-BW config (e.g., bandwidth 930 GB/s, as in 3D DRAM) → verify bottleneck shifts from BW-bound (DMA stall > 50%) to compute-bound (MXU > SFU+Vector). **Caveat**: DRAMModel hard-codes LPDDR5 tRFC/tREFI refresh parameters; the high-BW config only changes bandwidth, not refresh model — document this limitation; (e) MAC utilization sanity: for M=1 decode, verify MAC utilization < 1% for all model sizes; for prefill (M=128), verify MAC utilization rises to ~50%.
  Must NOT do: Do NOT produce absolute performance claims — verify CONSISTENCY and direction only. Do NOT benchmark against real silicon or RTL.
  Parallelization: Wave 7 | Blocked by: T4, T10 | Blocks: T13, T14, T15 | Can parallelize with: —
  References: `sim/timing/benchmark.py` (sweep modes), `sim/model_specs.py` (ModelSpec sizes), `sim/config/npu_config.yaml` (hardware configs), `docs/func_model_performance_analysis.md:274-287` (bottleneck analysis)
  Acceptance criteria: DMA sweep shows monotonic TPS growth; NoC crossbar < mesh; per-layer normalized cycles consistent (±15%) across model sizes; bottleneck shift direction correct; 3D DRAM refresh model limitation documented; MAC utilization ranges documented. Evidence: `.omo/evidence/perf-t12-cross-model.txt`
  QA scenarios: Happy = all scaling checks pass directionally. Failure = TPS decreases with more DMA channels → report as potential model bug. Evidence: `.omo/evidence/perf-t12-cross-model.txt`
  Commit: Y | test(func-model-perf-signoff): cross-model consistency sweep/scaling verification

- [ ] 14. CV model performance signoff (T14)
  What to do: Verify the 5 registered CV models (mobilenetv3-small/resnet18/resnet50/yolov8n/vit-b16) produce valid trace → FPS/inference_latency through benchmark.py CV mode. Check: (a) each CV model's `cv_trace_module` loads and `generate_<alias>_trace()` returns non-empty trace (verified by `--model <cv_alias>` benchmark run); (b) Dashboard JSON in CV mode includes `fps`, `inference_latency_us`, `module_breakdown` keys — verify `fps` > 0 and module breakdown percentages sum to ~100%; (c) MXU cycle formulas verified from T1 apply correctly to CV GEMM shapes (im2col→GEMM: K=3×3×C, N=H_out×W_out, M=C_out); (d) DMA/NoC formulas verified from T4/T5 apply in CV context (weight preload, activation read once); (e) CV inference latency < LLM decode latency per layer (fewer layers, more MACs per layer — verify this relationship). Create `sim/timing/tests/test_cv_perf_signoff.py` — validate FPS/latency keys present, non-zero, and module breakdown consistent across 5 models.
  Must NOT do: Do NOT verify CV functional correctness (covered by W3 FM-2). Do NOT generate new ONNX/conversion — use existing assets. Do NOT benchmark against real hardware.
  Parallelization: Wave 7 | Blocked by: T1-T7, T12 | Blocks: T13 | Can parallelize with: T15
  References: `sim/timing/benchmark.py:37-64` (CV trace generation), `sim/timing/dashboard.py:213-216` (CV mode dashboard), `sim/cv/traces/` (CV trace generators), `sim/model_specs.py` (CV ModelSpec aliases)
  Acceptance criteria: All 5 CV models produce non-zero FPS/latency; module breakdown consistent; MXU/DMA/NoC formulas in CV context verified. Evidence: `.omo/evidence/perf-t14-cv-signoff.txt`
  QA scenarios: Happy = 5/5 CV models produce valid FPS + breakdown. Failure = any CV model returns zero FPS or missing module keys → check trace generation. Evidence: `.omo/evidence/perf-t14-cv-signoff.txt`
  Commit: Y | test(func-model-perf-signoff): CV model performance signoff verification

- [ ] 15. Prefill path generalization (T15)
  What to do: Refactor `sim/npu_sim.py:simulate_prefill()` to use `_build_llm_trace(model_spec, m)` from `sim/timing/timing_engine.py:22-45` instead of hard-coded `generate_qwen3b_trace(prompt_len=M)`. Steps: (a) add `model_spec` parameter to `simulate_prefill` signature; (b) replace `generate_qwen3b_trace(prompt_len)` with `_build_llm_trace(model_spec, prompt_len)`; (c) verify the code path: SFU/Vector post-processing handle correctly in per-GEMM loop for arbitrary M; (d) run prefill benchmark on qwen2.5-1.5b (28 layers, hidden=1536), qwen2.5-3b (36 layers, hidden=2048), qwen2.5-7b (28 layers, hidden=3584) at prompt_len=16 and prompt_len=128 — verify TTFT scales ~linearly with prompt_len × hidden × layers; (e) verify prefill vs decode cycle ratio is consistent: prefill_cycles / prompt_len ≈ decode_cycles for same model (prefill does M parallel rows, decode does 1). Create `sim/timing/tests/test_prefill_generalization.py` — test that `simulate_prefill(model_spec=qwen2.5-3b, prompt_len=8)` callable; assert TTFT(128)/TTFT(16) ≈ 8.0 ± 2.0 (overhead for init).
  Must NOT do: Do NOT change the prefill compute formula — only generalize trace construction. Do NOT break existing hard-coded prefill API downstream callers — add new parameter with default for backward compat.
  Parallelization: Wave 7 | Blocked by: T10, T12 | Blocks: T13 | Can parallelize with: T14
  References: `sim/npu_sim.py:411-502` (simulate_prefill), `sim/timing/timing_engine.py:22-45` (_build_llm_trace), `sim/timing/tests/test_benchmark.py`, `sim/model_specs.py` (ModelSpec for 1.5B/3B/7B)
  Acceptance criteria: `simulate_prefill(model_spec=..., prompt_len=8)` call succeeds for 1.5B/3B/7B; TTFT ratios consistent within ±25%; prefill/decode cycle ratio sensible; existing callers (timing_engine.py) not broken. Evidence: `.omo/evidence/perf-t15-prefill-generalization.txt`
  QA scenarios: Happy = prefill works for all 3 model sizes, TTFT ratios consistent. Failure = `simulate_prefill` fails with new model_spec → check SFU/Vector per-loop integration. Evidence: `.omo/evidence/perf-t15-prefill-generalization.txt`
  Commit: Y | test(func-model-perf-signoff): generalize prefill path to use _build_llm_trace

- [ ] 16. Perf signoff checklist + documentation (T13)
  What to do: Create `docs/func-model-perf-signoff-checklist.md` FROM SCRATCH. Document: (a) 8 timing models status (PERF-FM-01..08); (b) CoreTimeline status (PERF-FM-09); (c) Metrics status (PERF-FM-10); (d) Cross-path consistency (PERF-FM-11); (e) Cross-model consistency (PERF-FM-12); (f) Spike+firmware path (PERF-FM-13); (g) CV model perf status (PERF-FM-14); (h) Prefill generalization (PERF-FM-15); (i) explicit scope boundaries. Create semantic checker `scripts/check_func_model_perf_signoff_docs.py`. Run validate equivalent for perf evidence.
  Must NOT do: Do NOT claim RTL-level accuracy. Do NOT claim full-chip or multi-layer perf coverage. Do NOT delete func-model-signoff-checklist.md.
  Parallelization: Wave 8 | Blocked by: T9, T10, T11, T12, T14, T15 | Blocks: final wave | Can parallelize with: —
  References: `docs/func-model-signoff-checklist.md` (template pattern), `scripts/check_func_model_signoff_docs.py` (semantic checker pattern), `rtl/testcase-list-perf.md` (perf test case list), `build/evidence/w4-perf-review-gate.txt` (conditions for reference)
  Acceptance criteria: Checklist created with 15 PERF-FM items, semantic checker passes, validate command succeeds on all perf evidence. Evidence: `.omo/evidence/perf-t13-checklist.txt`
  QA scenarios: Happy = checklist matches evidence, checker passes, validate OK. Failure = checklist overclaims → semantic checker rejects. Evidence: `.omo/evidence/perf-t13-checklist.txt`
  Commit: Y | docs(func-model-perf-signoff): create perf signoff checklist + reconcile docs

## Final verification wave
> Runs in parallel after ALL todos (T0A-T15). ALL must APPROVE.
- [ ] F1. Plan compliance audit: `validate --perf` equivalent finds all 16 evidence cases OK, stale detection clean, no missing metrics.
  Acceptance: `.omo/evidence/perf-final-plan-compliance.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/perf-final-plan-compliance.txt`
  Commit: N

- [ ] F2. Code quality review: compileall on all changed Python files, perf timing tests (60+) pass, no forbidden imports, no RTL dependency in perf-signoff harness.
  Acceptance: `.omo/evidence/perf-final-code-quality.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/perf-final-code-quality.txt`
  Commit: N

- [ ] F3. Real manual QA: (1) `PYTHONPATH=sim python -m sim.timing.benchmark --model qwen2.5-3b --prompt-len 1 --gen-len 1 --output /tmp/perf-f3` → assert exit 0, JSON `tps > 0, ttft_ms > 0`; (2) `PYTHONPATH=sim python sim/spike_perf_runner.py --workload mmul_chain_single_layer` → assert exit 0, output JSON with `engine_busy_cycles > 0, mmio_calls`; (3) `grep -n 'wall_keys' sim/timing/timing_engine.py | grep 'dma_effective'` → assert NO match (double-counting regression check); (4) `PYTHONPATH=sim python -m sim.timing.benchmark --sweep-dma-channels 1,2,4,8 --model qwen2.5-3b` → assert exit 0, TPS 2ch > 1ch; (5) verify cross-path engine cycle delta ≤ 10% from T11 evidence.
  Acceptance: `.omo/evidence/perf-final-real-qa.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/perf-final-real-qa.txt`
  Commit: N

- [ ] F4. Scope fidelity: Compare worktree against signoff start commit. Reject RTL changes. Reject any VCS/Spike plugin/RTL simulation path changes. Allow only `sim/`, `scripts/`, `docs/`, `firmware/` (firmware is Func Model artifact), `.omo/`.
  Acceptance: `.omo/evidence/perf-final-scope-fidelity.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/perf-final-scope-fidelity.txt`
  Commit: N

## Commit strategy
| Task | Commit | Message |
|------|--------|---------|
| T0A | Y | feat(func-model-perf-signoff): add perf signoff evidence runner |
| T0B | Y | feat(func-model-perf-signoff): add Spike+firmware perf harness |
| T1 | Y | test(func-model-perf-signoff): verify MXU BlockEngine cycle formulas |
| T2 | Y | test(func-model-perf-signoff): verify SFUModel cycle formulas |
| T3 | Y | test(func-model-perf-signoff): verify VectorModel cycle formulas |
| T4 | Y | test(func-model-perf-signoff): verify DMAModel formulas + K-tile reload |
| T5 | Y | test(func-model-perf-signoff): verify NoCModel + FM-1 overhead |
| T6 | Y | test(func-model-perf-signoff): verify KVCacheModel cycle formulas |
| T7 | Y | test(func-model-perf-signoff): verify DRAMModel cycle formulas |
| T8 | Y | test(func-model-perf-signoff): verify SWOverheadModel + firmware chain |
| T9 | Y | test(func-model-perf-signoff): verify CoreTimeline overlap + concurrency |
| T10 | Y | test(func-model-perf-signoff): verify Metrics derivation |
| T11 | Y | test(func-model-perf-signoff): cross-path consistency verification |
| T12 | Y | test(func-model-perf-signoff): cross-model consistency sweep/scaling |
| T14 | Y | test(func-model-perf-signoff): CV model performance signoff verification |
| T15 | Y | test(func-model-perf-signoff): generalize prefill path to _build_llm_trace |
| T13 | Y | docs(func-model-perf-signoff): create perf checklist + reconcile docs |
| F1-F4 | N | evidence only |

All commits on `main` branch. Each task commit independent.

## Success criteria
1. All 8 timing model formulas verified against architectural design spec — each model's estimate() produces cycle counts matching documented formula (not RTL)
2. CoreTimeline overlap/concurrency verified — wall clock only advances on wall_keys (mxu/sfu/vector/kv/overheads); DMA/NoC breakdown-only; FM-1 4-cycle gap confirmed; no double-counting (5 E2E bugs remain fixed)
3. Spike+firmware dispatch chain measured and verified — firmware MMIO overhead within 50% of SWOverheadModel prediction; weight streaming per-K-tile reload modeled
4. Metrics derivation verified — TPS/TTFT/TPOT/ITL/DMA overlap/BW util/NoC latency formulas confirmed
5. Cross-path consistency verified — analysis vs firmware engine cycles within 10% delta; wall-clock delta within 50% with documented root cause
6. Cross-model consistency verified — DMA sweep monotonic, NoC topology differences correct, per-layer cycle constant across model sizes, bottleneck shift direction correct, MAC utilization ranges documented
7. CV model performance verified — all 5 registered CV models produce valid FPS/latency; MXU/DMA/NoC formulas consistent in CV GEMM context; CV inference latency < LLM decode per layer (fewer layers, more MAC/layer)
8. Prefill path generalized — `simulate_prefill` supports arbitrary ModelSpec via `_build_llm_trace`; TTFT scales with prompt_len × hidden × layers; prefill/decode cycle ratio sensible
9. Perf signoff checklist created with 15 PERF-FM items, semantic checker passes
10. All 16 perf evidence cases pass `validate --perf`
11. All work on main branch with per-task commits
12. F1-F4 Final Wave all APPROVE

