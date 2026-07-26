# func-model-signoff-v2 - Work Plan

## TL;DR (For humans)

**What you'll get:** Caduceus Func Model 在 Qwen2.5-3B 上的功能验证达到签收级别——修复 FP16 容差比较器逻辑、用真实 GGUF checkpoint 做 full-shape blk.0 验证（而非截断到单 tile）、补齐 robostness 和文档一致性，最终由独立审计 runner 产出可追溯的 evidence。

**Why this approach:** 1) 容差比较器是所有 SFU/FP16 验证的基础，必须先修（否则 golden reference 不可靠）；2) 用原始 984 行计划已深度分析过的方案——独立 signoff runner + 23-case registry 提供原子化 evidence 和 stale-state 检测，虽然实现工作量最大但审计痕迹最完整。

**What it will NOT do:** 不修 RTL、不修 RTL testbench、不关 SFU RTL batch 526/537、不做性能签收、不做 36 层全量验证（只做 blk.0）。</p>

**Effort:** Large — 14 tasks + 4 final-wave reviewers, 约 2-3 天
**Risk:** Medium — 真实 GGUF 的 full-shape 调用可能暴露 Func Model 本身的设计限制（如 tile scheduler 缺陷、bridge 数值精度不足），需要根据 stop condition 诚实记录而非降低覆盖
**Decisions to sanity-check:** 保留独立 signoff runner（而非标准 pytest）、测试放 `sim/signoff/` 新目录、按 task commit 到 main

Your next move: approve and start execution, or run a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): Large/Medium — 14-task Func Model functional signoff: fix tolerance comparator, build signoff runner, validate real Qwen2.5-3B blk.0 at full shape, robustness + docs consistency, per-task commits to main.

## Scope
### Must have
- Fixed FP16/SFU element-wise tolerance comparator (`np.all((abs_diff <= atol) | (rel_diff <= rtol))`) in `sim/golden_executor.py` and `scripts/verify_w2_2_fm_golden_vectors.py`
- Independent signoff runner `scripts/run_func_model_signoff.py` with atomic evidence, source-fingerprint, stale-HEAD detection, and anti-vacuous guards
- Synthetic 17-op manifest stress gate (direct-MMIO + tiled-MMUL) at declared dimensions
- Real Qwen2.5-3B GGUF blk.0 hard gate at canonical shapes (2048/11008) — direct projections, tiled projections, and connected dataflow with dual oracles
- Scaled/single-tile tests renamed and reclassified as fast regressions (not signoff evidence)
- Robustness coverage: corruption detection, boundary tiles, invalid descriptor/address rejection
- Documentation consistency: `docs/func-model-signoff-checklist.md` created from scratch; status not overclaimed
- Full functional sweep with zero failures/skips/xfails (signoff path; known perf-model and PCIe-DMA tests excluded via `--ignore`)
- All work on `main` branch with per-task commits
- Test files in `sim/signoff/` new directory

### Must NOT have (guardrails, anti-slop, scope boundaries)
- NO RTL implementation changes (`rtl/wrapper/*`, `rtl/sfu/*`, `rtl/mxu/*`, `rtl/soc/*`, `rtl/cpu/*`, `rtl/ip/*`)
- NO RTL testbench repair (`rtl/tb/*`)
- NO SFU RTL batch `526/537` closure
- NO FM-SOC RTL VCS rerun
- NO performance signoff
- NO full multi-layer or full 36-layer Qwen 3B signoff (blk.0 only)
- NO changes to `sim/tests/test_engines.py` (known perf-model failures, excluded from functional gate)
- NO `min(..., 64)` dimension cap in the signoff path
- NO synthetic data labeled as real-model evidence
- NO oracle value forwarding (NumPy oracle value must not replace the forwarded MMIO/SFU/Vector output)
- NO stale `llama_ref/refs` data used as 3B evidence (stale 1.5B evidence)
- Binding constraints (verbatim from user):
  1. "以后设计验证的工作都在main分支上推进"
  2. "涉及到工具调用，环境变量设置，都用脚本方式"
  3. "所有验证都在sz0001上进行" — 本研究在本地跑 Python pytest，VCS 验证在 sz0001
  4. "对于bug，一定要记录到bug track文件"

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: TDD for comparator (RED→GREEN); tests-after for non-comparator tasks
- Framework: pytest with PYTHONPATH=sim
- Evidence: `.omo/evidence/task-<N>-func-model-signoff-v2.txt` (atomic writes via signoff runner)
- Real model: `QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf` (SHA-256 `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d`, 2104932768 bytes)
- GGUF canonical shapes: hidden=2048, intermediate=11008, 16 heads, 2 KV heads, head_dim=128
- Real-model workload: deterministic decode-token blk.0, prompt="Hello", token_id=9707, M=1, position=0, DRAM 256 MB (`FuncModel(dram_mb=256)`)
- Synthetic manifest: `rtl/test_vectors/qwen_blk0/blk0_manifest.json` — 17 ops, 46 files, synthetic dims 2560/9728
- Regression baseline: `sim/tests/` full functional sweep ≥ 638 passed (from 2026-07-22 review rerun)
- Environment variable encapsulation (binding constraint #2): The signoff runner `scripts/run_func_model_signoff.py` sets `PYTHONPATH=sim` and `QWEN3B_GGUF` internally from a default-or-override configuration block. Workers should invoke `python3 scripts/run_func_model_signoff.py run --case <id>` without inline `PYTHONPATH=` or `QWEN3B_GGUF=` prefixes. For direct pytest invocations (e.g., during TDD RED/GREEN), use `scripts/run_fm_env.sh -- pytest <args>` which sets the same environment.

## Execution strategy
### Parallel execution waves
1. **Wave 0:** T0A (signoff runner framework)
2. **Wave 1** (after T0A): T1 (comparator RED), T0B (preflight), T3 (reclassify)
3. **Wave 2** (after T1): T2 (comparator fix) + T4B (synthetic tiled) + T4C1 (selective loading) — parallel
4. **Wave 3** (after T2+T0B): T4A (synthetic direct) + T4C2 (real direct) — parallel
5. **Wave 4** (after T4C2): T4C3 (real tiled)
6. **Wave 5** (after T4C3): T4C4 (connected dual-oracle)
7. **Wave 6** (after T4C4+T4A+T4B): T5 (robustness)
8. **Wave 7** (after T3+T4C4): T6 (docs)
9. **Wave 8** (after all): T7 (full sweep + signoff gates)
10. **Final wave** (after T7): F1-F4 parallel

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T0A | none | all evidence tasks | — |
| T0B | T0A | T4A, T4B, T4C1, T5 | T1, T3 |
| T1 | T0A | T2 | T0B, T3 |
| T2 | T1 | T4A, T4C4, T5, T7 | T4B, T4C1 |
| T3 | T0A | T6 | T0B, T1 |
| T4A | T0A, T0B, T2 | T5, T6, T7 | T4C2 |
| T4B | T0A, T0B | T5, T6, T7 | T2, T4C1 |
| T4C1 | T0A, T0B | T4C2, T4C3, T4C4 | T2, T4B |
| T4C2 | T4C1 | T4C3, T4C4 | T4A |
| T4C3 | T4C2 | T4C4, T5 | — |
| T4C4 | T2, T4C1, T4C2, T4C3 | T5, T6, T7 | — (critical path: wave 5, single-task) |
| T5 | T2, T4A, T4B, T4C4 | T6, T7 | — |
| T6 | T3, T4A, T4B, T4C1-T4C4, T5 | T7 | — |
| T7 | T0A-T6 | final wave | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- Full task details follow. Each task draws from the detailed analysis in the original plan draft `.omo/plans/func-model-functional-signoff-repair.md` (lines 1-984), which the executor MUST read for complete implementation specs. -->

- [x] 0. Draft approval gate passed.

- [x] 1. Signoff evidence runner framework (T0A)
  What to do: Create `scripts/run_func_model_signoff.py` — an authoritative signoff runner with a static case registry. Each case maps to: argv list (subprocess, shell=False), evidence path, expected exit, min collected/passed counts, skip/xfail prohibition, source-fingerprint (SHA-256 over sorted in-scope source files), and required `SIGNOFF_METRIC` records. Parse JUnit XML for pytest cases. Atomic evidence writes (temp file + rename). Failed commands must still produce FAIL evidence. Provide `validate --case <id>` and `validate --all-functional` modes. Create `scripts/run_fm_env.sh` — a wrapper that sets `PYTHONPATH=sim` and `QWEN3B_GGUF` (default-or-override) then execs its arguments. Create `sim/tests/test_func_model_signoff_runner.py` to test the runner itself (success, failure, expected-RED, zero-test, skip/xfail, missing-metric, stale-HEAD, stale-source-fingerprint, stale-command, atomic-write unit cases). See original plan `.omo/plans/func-model-functional-signoff-repair.md` lines 148-220 for the complete case-registry table (23 cases) and metric-key schemas.
  Must NOT do: Do NOT use `shell=True`. Do NOT infer PASS from terminal text. Do NOT accept zero-test as PASS. Do NOT skip the source-fingerprint (HEAD alone cannot detect stale evidence since this plan authorizes no commits during framework dev).
  Parallelization: Wave 0 | Blocked by: none | Blocks: all evidence-producing tasks
  References: `.omo/plans/func-model-functional-signoff-repair.md:148-220` (case registry), `:170-179` (source-fingerprint spec), `:173-178` (metric keys)
  Acceptance criteria: `PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run --case task-0a-signoff-runner` exits 0; `.omo/evidence/task-0a-signoff-runner.txt` exists and contains `evidence.verdict: pass`; `python3 -m pytest sim/tests/test_func_model_signoff_runner.py -q` passes.
  QA scenarios: Happy = runner correctly validates all unit cases (success/fail/RED/zero/skip/stale). Failure = malformed metric JSON rejected, zero-test detected as FAIL, stale fingerprint rejected. Evidence: `.omo/evidence/task-0a-signoff-runner.txt`
  Commit: Y | feat(func-model-signoff): add authoritative signoff evidence runner with case registry

- [x] 2. FP16 comparator regression tests — RED phase (T1)
  What to do: Create `sim/tests/test_golden_sfu_compare.py` with TDD RED tests: (a) mixed abs/rel pass — one element passes only atol, one passes only rtol, full array should UNDER current impl FAIL (assertion message `mixed abs/rel must pass element-wise`); (b) out-of-tolerance fail; (c) NaN/Inf mismatch; (d) exact-boundary (`<=` behavior). These tests are EXPECTED TO FAIL against current implementation — that is the RED phase. See original plan lines 277-311.
  Must NOT do: Do NOT modify `sim/golden_executor.py` yet. Do NOT make tests pass by weakening assertions.
  Parallelization: Wave 1 | Blocked by: T0A | Blocks: T2 | Can parallelize with: T0B, T3
  References: `sim/golden_executor.py:649-662` (current comparator with old `or` semantics), `scripts/verify_w2_2_fm_golden_vectors.py:225`
  Acceptance criteria: `PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run --case task-1-comparator-red` produces evidence with `expected_failure` verdict. `test_compare_mixed_abs_rel_pass` FAILED with assertion containing `mixed abs/rel must pass element-wise`.
  QA scenarios: Happy (RED) = at least the mixed abs/rel test fails against current `np.all(...) or np.all(...)` impl. Failure = test passes (meaning RED is wrong — check assertion). Evidence: `.omo/evidence/task-1-comparator-red.txt`
  Commit: Y | test(func-model-signoff): add comparator RED tests (F-FM-03 TDD phase 1)

- [x] 3. Synthetic + real-GGUF provenance preflight (T0B)
  What to do: Create `sim/qwen_blk0_synthetic_vectors.py` (manifest loading + tile-layout helpers), `sim/signoff/test_qwen_blk0_synthetic_stress.py` (preflight assertions), `sim/signoff/test_qwen25_3b_real_blk0.py` (real GGUF preflight). Validate: synthetic manifest has 17 ops / 46 files / all SHA-256 match / dims are 2560/9728 (NOT canonical). Real GGUF: exact hash `626b4a...c62d`, size `2104932768`, metadata (36 layers, hidden 2048, intermediate 11008, 16 heads, 2 KV heads, head_dim 128). Assert layer-0 tensor shapes: Q/O 2048x2048, K/V 2048x256, gate/up 2048x11008, down 11008x2048. Define non-overlapping DRAM windows in 256 MB. See original plan lines 226-275.
  Must NOT do: Do NOT fall back to 1.5B model or synthetic if real GGUF missing. Do NOT use `FuncModel()` default 64 MB — use `FuncModel(dram_mb=256)`.
  Parallelization: Wave 1 | Blocked by: T0A | Blocks: T4A, T4B, T4C1, T5 | Can parallelize with: T1, T3
  References: `rtl/test_vectors/qwen_blk0/blk0_manifest.json`, `sim/func_model.py:27` (FuncModel defaults), `ggml-npu/q4_dequant.py:171`
  Acceptance criteria: `QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run --case task-0b-qwen3b-synthetic-and-real-preflight` exits 0. Evidence has `model.sha256`, `model.hidden=2048`, `model.intermediate=11008`.
  QA scenarios: Happy = both synthetic and real assets validated. Failure = missing asset, wrong hash, or overlapping DRAM windows → FAIL before compute. Evidence: `.omo/evidence/task-0b-qwen3b-synthetic-and-real-preflight.txt`
  Commit: Y | feat(func-model-signoff): add synthetic + real-GGUF provenance preflight

- [x] 4. Fix FP16 tolerance semantics — GREEN phase (T2)
  What to do: Fix comparator in `sim/golden_executor.py:649-662` and `scripts/verify_w2_2_fm_golden_vectors.py:225` to use element-wise `np.all((abs_diff <= atol) | (rel_diff <= rtol))`. Reject any NaN. Accept same-position same-sign infinities; reject opposite-sign and finite-vs-infinite. Record pre-fix and post-fix source hashes in evidence. Run `test_golden_sfu_compare.py`, `test_golden_sfu.py`, `test_golden_sfu_gaps.py`, and W2.2 golden vectors — all must pass. See original plan lines 313-353.
  Must NOT do: Do NOT broaden refactoring beyond comparator. Do NOT change existing metrics (`max_abs_err`, `mean_abs_err`, `max_rel_err`).
  Parallelization: Wave 2 | Blocked by: T1 | Blocks: T4A, T4C4, T5, T7 | Can parallelize with: T4B, T4C1
  References: `sim/golden_executor.py:649-662`, `scripts/verify_w2_2_fm_golden_vectors.py:225`, `.omo/plans/func-model-functional-signoff-repair.md:313-353`
  Acceptance criteria: `PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run --case task-2-comparator-green` passes. `task-2-w2-2-golden-vectors` shows 14/14 PASS. `test_golden_sfu_compare.py` all GREEN.
  QA scenarios: Happy = comparator tests pass element-wise, W2.2 14/14 PASS. Failure = any SFU test fails after fix → check NaN/Inf handling. Evidence: `.omo/evidence/task-2-comparator-green.txt` + `.omo/evidence/task-2-w2-2-golden-vectors.txt`
  Commit: Y | fix(func-model-signoff): element-wise FP16/SFU tolerance comparator (F-FM-03)

- [x] 5. Reclassify scaled/single-tile Qwen tests (T3)
  What to do: Rename in `sim/tests/test_soc_fm.py`: `test_blk0_full_chain_single_tile` → `test_blk0_scaled_single_tile_manifest_replay` (line 1812), `test_28block_chain` → `test_28block_scaled_chain` (line 2541), `test_e2e_host_pcie_doorbell_firmware_compute` → `test_e2e_host_pcie_doorbell_firmware_scaled_blk0` (line 2812). Update all callers. Create `scripts/check_func_model_signoff_docs.py` and `sim/tests/test_func_model_signoff_docs.py` to assert no capped path is labeled full-shape. See original plan lines 355-394.
  Must NOT do: Do NOT remove scaled tests — keep as fast regressions. Do NOT change test logic, only rename + relabel.
  Parallelization: Wave 1 | Blocked by: T0A | Blocks: T6 | Can parallelize with: T0B, T1
  References: `sim/tests/test_soc_fm.py:1812,2541,2812`, `rtl/testcase-list-soc-fm.md`
  Acceptance criteria: `PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run --case task-3-scaled-qwen-regressions` passes. Renamed tests still pass. `check_func_model_signoff_docs.py --check-scaled-labels` finds no capped path labeled full-shape.
  QA scenarios: Happy = 3 tests renamed and still PASS. Failure = caller breaks after rename → fix all node-ID references. Evidence: `.omo/evidence/task-3-scaled-qwen-regressions.txt`
  Commit: Y | refactor(func-model-signoff): reclassify scaled/single-tile Qwen tests as fast regressions

- [x] 6. Synthetic direct-MMIO 17-op stress gate (T4A)
  What to do: Execute all 17 legacy synthetic manifest ops through `FuncModel`/`MMIOBridge` at declared dimensions (no cap). Compare against checked-in golden files. SFU FP16 outputs use corrected element-wise comparator (atol=2e-3, rtol=1e-2). Assert M/K/N equals manifest value per op. Record model/layer/op/dims/dtype/golden-hash/comparator. Label as synthetic, NOT real-model. See original plan lines 396-431.
  Must NOT do: Do NOT call `GoldenMXU`/`GoldenSFU`/`GoldenVector` to generate expected results inside this gate — checked-in manifest golden is the oracle. Do NOT cap dimensions.
  Parallelization: Wave 3 | Blocked by: T0A, T0B, T2 | Blocks: T5, T6, T7 | Can parallelize with: T4C2
  References: `rtl/test_vectors/qwen_blk0/blk0_manifest.json`, `sim/qwen_blk0_synthetic_vectors.py` (from T0B)
  Acceptance criteria: `--case task-4a-qwen3b-direct-mmio` → 17/17 ops pass, no executed dim < manifest dim, evidence labels `data_provenance=synthetic`.
  QA scenarios: Happy = 17/17 PASS. Failure = missing golden or opcode unsupported → FAIL. Evidence: `.omo/evidence/task-4a-qwen3b-direct-mmio.txt`
  Commit: Y | test(func-model-signoff): synthetic 17-op direct-MMIO stress gate

- [x] 7. Synthetic tiled-MMUL scheduler stress gate (T4B)
  What to do: Run every manifest MMUL through `tile_mmul()` at full dims. Convert row-major packed INT4 to tile-major `(n_tile, k_block)` layout. Generate unity FP32 scales. Compare against manifest INT32 golden→FP32 with atol=1e-4, rtol=1e-5. Verify tile count = ceil(K/128)*ceil(N/128), first/middle/last/remainder tiles, and full N output stitching. See original plan lines 433-469.
  Must NOT do: Do NOT reinterpret row-major file as tile-major. Do NOT cap dims. Do NOT fall back to direct MMIO. Report scheduler limitation as blocker, not bypass.
  Parallelization: Wave 2 | Blocked by: T0A, T0B | Blocks: T5, T6, T7 | Can parallelize with: T2, T4C1
  References: `sim/tile_scheduler.py:20` (`tile_mmul`), `sim/qwen_blk0_synthetic_vectors.py`
  Acceptance criteria: `--case task-4b-qwen3b-tiled-mmul` → all manifest MMULs pass, tile counts verified, stitching verified.
  QA scenarios: Happy = all tiled MMULs pass full-shape. Failure = scheduler cannot consume tile-major → report as signoff blocker. Evidence: `.omo/evidence/task-4b-qwen3b-tiled-mmul.txt`
  Commit: Y | test(func-model-signoff): synthetic tiled-MMUL scheduler stress gate

- [x] 8. Selective real-GGUF loading and reference inputs (T4C1)
  What to do: Extend `ggml-npu/q4_dequant.py` with `load_selected_weights_from_gguf(path, tensor_names)` and `load_tensor_row_from_gguf(path, name, row)`. Load only layer-0 Q/K/V/O/gate/up/down + biases + RMSNorm weights + token-embedding row 9707. Extend `sim/qwen25_forward.py:201` `forward_with_intermediates()` to expose exact projection inputs (x_norm, ffn_norm, attn_concat). Create `sim/qwen25_func_model.py`. Create `sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_selective_loading_and_reference_inputs`. Assert float32 forward hash unchanged after extension. See original plan lines 471-514.
  Must NOT do: Do NOT dequantize all 36 layers. Do NOT use `load_weights_from_gguf()` for signoff (it loads everything). Do NOT change existing `forward()` results.
  Parallelization: Wave 2 | Blocked by: T0A, T0B | Blocks: T4C2, T4C3, T4C4 | Can parallelize with: T2, T4B
  References: `ggml-npu/q4_dequant.py:171`, `sim/qwen25_forward.py:201`, `scripts/gen_qwen25_3b_rtl_vectors.py:241,304`
  Acceptance criteria: `--case task-4c1-qwen25-3b-selective-load-and-reference-inputs` → exact GGUF hash, loaded tensor list, canonical shapes, unchanged forward hash.
  QA scenarios: Happy = selective load + reference inputs validated. Failure = wrong tensor shape or forward hash changed → check intermediate capture additions. Evidence: `.omo/evidence/task-4c1-qwen25-3b-selective-load-and-reference-inputs.txt`
  Commit: Y | feat(func-model-signoff): selective real-GGUF loading + reference input exposure

- [x] 9. Real direct-MMIO projection gate with independent oracle (T4C2)
  What to do: Create `sim/qwen25_signoff_oracle.py` — independent NumPy oracle that unpacks INT4 itself, computes K-group INT32 products, applies FP32 group scales, accumulates. Must NOT import/call `GoldenMXU`, `MMIOBridge`, `tile_mmul`, or production helpers. Add import/call guard test. Execute Q/K/V/O/gate/up/down at canonical full shapes via direct MMIO. Restore activation scale once (`restored = mmio_output * act_scale`), apply bias once (Q/K/V only). Compare vs oracle (atol=1e-4, rtol=1e-5) and vs float32 model (cosine ≥ 0.96). See original plan lines 516-568.
  Must NOT do: Do NOT fold bias into INT32 accumulation. Do NOT apply activation scale twice. Do NOT forward oracle value instead of MMIO output. Do NOT import or call `golden_executor.py`, `mmio_bridge.py`, `tile_scheduler.py`, or any module whose name starts with `golden_` or `mmio_` — the oracle's computation path (INT4 repacking, INT32 matmul, FP32 group scale application, RMSNorm, softmax, SiLU) must be independently implemented.
  Oracle data boundary: The oracle MAY share `ggml-npu/q4_dequant.py` for GGUF parsing only (reading Q4_K/Q6_K tensors from the GGUF file and dequantizing to float32). This is data extraction, not computation. Everything downstream of the float32 weight tensor — re-quantization to INT4 group-128, INT4 nibble packing, INT32 accumulation, scale application — must be implemented independently in the oracle. The nibble ordering contract is: **low nibble = first weight (lower/even index), high nibble = second weight (higher/odd index)**, matching the hardware MXU packing (confirmed: `weight_buffer.v:2`, `golden_executor.py:57-69`, `quantize.py:43`, `cocotb_bridge.py:193`). Document this constant in the oracle header so both the oracle and the import-guard test can reference it.
  Parallelization: Wave 3 | Blocked by: T4C1 | Blocks: T4C3, T4C4 | Can parallelize with: T4A
  References: `sim/qwen25_func_model.py` (from T4C1), `sim/mmio_bridge.py:141-181` (contract reference), `scripts/gen_qwen25_3b_rtl_vectors.py:304-330`
  Acceptance criteria: `QWEN3B_GGUF=... --case task-4c2-qwen25-3b-real-direct-projections` → 7 projections pass oracle + cosine. Record per-projection: M/K/N, activation_scale, saturation_count, max_abs, max_rel, cosine, verdict. Graded cosine policy: cosine ≥ 0.97 → PASS; cosine 0.96 ≤ c < 0.97 → PASS with WARNING evidence (investigate but not a blocker); cosine < 0.96 → FAIL. Rationale: Arc Model INT4 per-block analysis showed mean_cos=0.9903, min=0.9707. The 0.97 threshold matches the observed minimum; 0.96 is an 8× headroom margin for re-quantization (GGUF Q4_K → float32 → HW INT4).
  QA scenarios: Happy = all 7 direct projections pass dual oracle + cosine. Failure = activation scale restored twice or bias applied twice → check bridge contract. Evidence: `.omo/evidence/task-4c2-qwen25-3b-real-direct-projections.txt`
  Commit: Y | test(func-model-signoff): real-GGUF direct-MMIO projection gate with independent oracle

- [x] 10. Real tiled-scheduler projection gate (T4C3)
  What to do: Convert each Task 4C2 packed weight to tile-major `(n_tile, k_block)` 128×128 byte layout. Convert scales to tile-major blocks. Execute Q/K/V/O/gate/up/down through `tile_mmul()` at canonical shapes. Verify tile count = ceil(K/128)*ceil(N/128), first/middle/last/remainder tiles, full N stitching. Compare tiled vs oracle (atol=1e-4, rtol=1e-5) and vs direct-MMIO output (agreement). Same cosine ≥ 0.96. See original plan lines 570-604.
  Must NOT do: Do NOT reinterpret direct-layout bytes as scheduler-layout bytes. Do NOT fall back to direct MMIO for canonical projections. Do NOT cap dimensions.
  Parallelization: Wave 4 | Blocked by: T4C2 | Blocks: T4C4, T5 | Can parallelize with: —
  References: `sim/tile_scheduler.py:20`, `sim/qwen25_signoff_oracle.py`, `sim/qwen25_func_model.py`
  Acceptance criteria: `QWEN3B_GGUF=... --case task-4c3-qwen25-3b-real-tiled-projections` → 7 tiled projections pass oracle + direct agreement + tile/stitching checks with graded cosine policy (≥ 0.97 PASS, 0.96 ≤ c < 0.97 PASS+WARN, < 0.96 FAIL).
  QA scenarios: Happy = all 7 tiled projections pass. Failure = scheduler cannot handle full-shape tile-major → report as blocker. Evidence: `.omo/evidence/task-4c3-qwen25-3b-real-tiled-projections.txt`
  Commit: Y | test(func-model-signoff): real-GGUF tiled-scheduler projection gate

- [x] 11. Connected real-GGUF blk.0 dual-oracle hard gate (T4C4)
  What to do: Execute full connected dataflow: RMSNorm → gamma → Q/K/V → bias → RoPE → GQA(2→16 repeat) → score/sqrt(128) → softmax(seq axis) → O → residual → RMSNorm → gamma → gate/up → SiLU → Vector VMUL → down → residual. Every operator passes same-input local oracle (quantized MXU atol=1e-4, FP16 SFU atol=2e-3/rtol=1e-2, INT32 Vector exact, FUNC_BRIDGE atol=1e-6). Separate float32 model cosine ≥ 0.96 for projections and final output. Record every boundary's shape/dtype/scale/saturation/comparator/verdict/cosine. See original plan lines 606-671.
  Must NOT do: Do NOT forward NumPy oracle value to next step — next step receives actual MMIO/SFU/Vector/FUNC_BRIDGE output. Do NOT relabel residual/VMUL as FUNC_BRIDGE. Do NOT add unmodeled FP32 bypass to improve cosine. Do NOT use stale `llama_ref/refs`. If Vector bridge contract misses cosine gate, record as blocker.
  Parallelization: Wave 5 | Blocked by: T2, T4C1, T4C2, T4C3 | Blocks: T5, T6, T7
  References: `sim/qwen25_forward.py:223-236` (GQA/attention semantics), `sim/mmio_bridge.py:159-167` (scale/bias), `sim/qwen25_func_model.py`, `sim/qwen25_signoff_oracle.py`
  Acceptance criteria: `QWEN3B_GGUF=... --case task-4c4-qwen25-3b-real-connected-blk0` → every connected op passes local oracle; required GQA/softmax/bridge metrics present; graded cosine policy: projection/final cosine ≥ 0.97 → PASS; 0.96 ≤ c < 0.97 → PASS with WARNING; < 0.96 → FAIL.
  QA scenarios: Happy = full connected chain passes dual oracle. Failure = any operator misses oracle or cosine → record exact failing bridge/op as blocker (F-FM-13 remains blocked). Evidence: `.omo/evidence/task-4c4-qwen25-3b-real-connected-blk0.txt`
  Commit: Y | test(func-model-signoff): connected real-GGUF blk.0 dual-oracle hard gate

- [x] 12. Qwen 3B robustness coverage (T5)
  What to do: Add anti-vacuous and boundary tests: corrupt one weight/activation slice in memory (without modifying checked-in assets) → comparison must fail. **Corrupt one FP32 group-128 scale value** in the INT4 scale tensor (at a separate DRAM address from weight tiles) → MMIO bridge output must change and comparison must fail. **Omit RMSNorm gamma multiply** in the FUNC_BRIDGE step (set gamma to all-ones) → final cosine must drop below 0.97 and a warning must be emitted (detects gamma omission before it silently degrades quality). Wrong descriptor dims → fail. Wrong output address → fail. Tolerance-exceeding FP16 → fail. Boundary: K=129/N=130 forces K+N remainder tiles. First/middle/last/remainder tile behavior. All intentional corruptions must be detected. See original plan lines 673-722.
  Must NOT do: Do NOT modify checked-in asset files — corrupt in memory only.
  Parallelization: Wave 6 | Blocked by: T2, T4A, T4B, T4C4 | Blocks: T6, T7
  References: `sim/signoff/test_qwen_blk0_synthetic_stress.py`, `sim/signoff/test_qwen25_3b_real_blk0.py`, `sim/tests/test_soc_fm.py`
  Acceptance criteria: `QWEN3B_GGUF=... --case task-5-qwen3b-robustness` → all existing SoC FM tests stay green, synthetic/real/negative tests pass, failures are deterministic.
  QA scenarios: Happy = corruptions detected, boundary tiles pass. Failure = corruption undetected → anti-vacuous gap. Evidence: `.omo/evidence/task-5-qwen3b-robustness.txt`
  Commit: Y | test(func-model-signoff): broad Qwen 3B robustness coverage

- [x] 13. Documentation + checklist reconciliation (T6)
  What to do: Create `docs/func-model-signoff-checklist.md` FROM SCRATCH (does not exist — verified). Document: F-FM-03 fixed, F-FM-13 PASS only if T4A/T4B/T4C1-T4C4 pass, scaled tests labeled as fast regressions, synthetic vs real data distinction, RTL-golden-readiness deferred, performance FAIL/PARTIAL and separate. Update `rtl/testcase-list-soc-fm.md` scope wording only. Run `check_func_model_signoff_docs.py` semantic checker. See original plan lines 724-762.
  Must NOT do: Do NOT overclaim RTL or performance signoff. Do NOT describe scaled as full-shape. Do NOT describe synthetic as real.
  Parallelization: Wave 7 | Blocked by: T3, T4A, T4B, T4C1-T4C4, T5 | Blocks: T7
  References: `rtl/testcase-list-soc-fm.md`, `scripts/check_func_model_signoff_docs.py`, `sim/tests/test_func_model_signoff_docs.py`
  Acceptance criteria: `--case task-6-signoff-doc-consistency` passes. `check_func_model_signoff_docs.py` finds no overclaimed status. Any `526/537` occurrence clearly labeled downstream RTL.
  QA scenarios: Happy = docs consistent with evidence. Failure = overclaim detected → fix wording. Evidence: `.omo/evidence/task-6-signoff-doc-consistency.txt`
  Commit: Y | docs(func-model-signoff): create checklist + reconcile signoff documentation

- [x] 14. Comprehensive Func Model signoff sweep (T7)
  What to do: Run all signoff gates through the runner: Task 7 selected regression (11 test files), full functional sweep (`pytest sim/tests/ -q --ignore=sim/tests/test_soc_pcie_dma.py --ignore=sim/tests/test_engines.py` — ≥ 638 baseline), synthetic stress gates (T0B/T4A/T4B/T5 nodes), real-blk0 hard gate (T4C1-T4C4 + corruption), W2.2 golden vectors (14/14). Run `validate --all-functional`. All env vars (`PYTHONPATH`, `QWEN3B_GGUF`) set by the runner script, not inline. See original plan lines 764-837.
  Must NOT do: Do NOT include `test_engines.py` (known perf failures). Do NOT include PCIe SoC tests. Do NOT accept skips/xfails in hard gates.
  Parallelization: Wave 8 | Blocked by: T0A-T6 | Blocks: final wave
  References: `.omo/plans/func-model-functional-signoff-repair.md:764-837`
  Acceptance criteria: 5 evidence files created (selected regression, full sweep, synthetic stress, real blk0, w2.2 golden). `validate --all-functional` finds no stale/missing/unexecuted hard gates. Full sweep ≥ 638 + new tests, zero failures/skips/xfails.
  QA scenarios: Happy = all gates pass. Failure = any gate fails → report exact blocker. Evidence: `.omo/evidence/task-7-*.txt` (5 files)
  Commit: Y | test(func-model-signoff): comprehensive Func Model signoff sweep

## Final verification wave
> Runs in parallel after ALL todos (T0A-T7). ALL must APPROVE.
- [x] F1. Plan compliance audit: `validate --all-functional` + acceptance-to-case mapping. Reject missing/stale/skipped/zero-test gates.
  Acceptance: `.omo/evidence/final-plan-compliance.txt` has `evidence.verdict: pass`
  QA scenarios: Happy = runner `validate --all-functional` returns 0, every task acceptance criterion maps to a case ID with current evidence. Failure = evidence missing (e.g., `task-4c4` evidence absent), evidence stale (source fingerprint mismatch), evidence shows skipped/xfailed node. Evidence: `.omo/evidence/final-plan-compliance.txt`
  Commit: N
- [x] F2. Code quality review: `python3 -m compileall -q` on all changed Python files + signoff runner/comparator/real-model unit tests pass + oracle forbidden-import guard verified.
  Acceptance: `.omo/evidence/final-code-quality.txt` has `evidence.verdict: pass`
  QA scenarios: Happy = `compileall` clean, all unit tests pass, `sim/tests/test_qwen25_3b_real_blk0.py` import guard test confirms `qwen25_signoff_oracle.py` does NOT import `golden_executor` or `mmio_bridge`. Failure = compile error in changed file, forbidden import detected in oracle, unit test fails. Evidence: `.omo/evidence/final-code-quality.txt`
  Commit: N
- [x] F3. Real manual QA: Execute T4C1-T4C4 pytest nodes as fresh composite run. Assert GGUF hash, canonical dims, 7 projections, activation scale, bias/gamma ordering, GQA/softmax, tile counts, cosines.
  Acceptance: `.omo/evidence/final-real-qa.txt` has `evidence.verdict: pass`
  QA scenarios: Happy = `PYTHONPATH=sim python3 -m pytest sim/signoff/test_qwen25_3b_real_blk0.py -q` with `QWEN3B_GGUF` set by runner; all 4 T4C1-T4C4 nodes pass with fresh timestamps; GGUF hash `626b4a...c62d`, hidden=2048, intermediate=11008; 7 projections each with cosine record; graded cosine >= 0.96. Failure = missing GGUF, wrong hash, any projection cosine < 0.96, connection chain fails. Evidence: `.omo/evidence/final-real-qa.txt`
  Commit: N
- [x] F4. Scope fidelity: Compare worktree against start commit. Reject RTL/performance changes. Allow `rtl/testcase-list-soc-fm.md` scope wording only.
  Acceptance: `.omo/evidence/final-scope-fidelity.txt` has `evidence.verdict: pass`
  QA scenarios: Happy = `git diff --name-only <start_commit>..HEAD` shows only `sim/`, `scripts/`, `docs/func-model-signoff-checklist.md`, and `rtl/testcase-list-soc-fm.md`; no `rtl/mxu/`, `rtl/sfu/`, `rtl/wrapper/`, `rtl/soc/`, `rtl/cpu/`, `rtl/ip/`, or `rtl/tb/` changes. Failure = RTL file modified, performance file changed, new file outside authorized scope. Evidence: `.omo/evidence/final-scope-fidelity.txt`
  Commit: N

## Commit strategy
| Task | Commit | Message |
|------|--------|---------|
| T0A | Y | feat(func-model-signoff): add authoritative signoff evidence runner |
| T1 | Y | test(func-model-signoff): comparator RED tests (F-FM-03 TDD phase 1) |
| T2 | Y | fix(func-model-signoff): element-wise FP16/SFU tolerance comparator |
| T3 | Y | refactor(func-model-signoff): reclassify scaled Qwen tests |
| T4A | Y | test(func-model-signoff): synthetic direct-MMIO stress gate |
| T4B | Y | test(func-model-signoff): synthetic tiled-MMUL stress gate |
| T4C1 | Y | feat(func-model-signoff): selective real-GGUF loading |
| T4C2 | Y | test(func-model-signoff): real direct-MMIO projection gate |
| T4C3 | Y | test(func-model-signoff): real tiled-scheduler projection gate |
| T4C4 | Y | test(func-model-signoff): connected real blk.0 dual-oracle gate |
| T5 | Y | test(func-model-signoff): Qwen 3B robustness coverage |
| T6 | Y | docs(func-model-signoff): create checklist + reconcile docs |
| T7 | Y | test(func-model-signoff): comprehensive signoff sweep |
| F1-F4 | N | evidence only |

All commits on `main` branch. Each task commit independent.

## Stop conditions (report exact blocker, do NOT weaken coverage)
1. Manifest file missing or SHA-256 mismatch
2. Real GGUF missing, wrong hash, or non-canonical metadata/tensor shapes
3. Full-shape DRAM layout cannot fit in 256 MB without overlap
4. Any SRAM buffer allocation (activation tile, weight tile, scale tile, output accumulator) exceeds the tile scheduler's 256KB (`_SRAM_SIZE = 0x40000`) capacity or the allocations overlap
5. Opcode cannot execute at its manifest dimension
6. Tiled scheduler cannot consume correctly converted tile-major representation
7. Real path falls back to synthetic vectors, wrong 2560/9728 dims, or 1.5B checkpoint
8. Activation scale omitted, restored twice, or bias/gamma ordering ambiguous
9. Independent oracle value forwarded instead of declared MMIO/SFU/Vector/FUNC_BRIDGE output
10. Same-input local operator oracle and float32 model-quality oracle cannot be kept independent
11. Vector MMIO bridge contract misses the cosine gate → record exact bridge/op as blocker
12. Required evidence missing, stale, zero-test, skipped/xfailed, or lacks required metrics
13. Anti-vacuous mutation fails to change the validation result
14. Any bug discovered during signoff MUST be recorded to `docs/bugs/bugs-soc-func-model.md` (binding constraint #4)

## Bug tracking requirement
Any Func Model bug discovered during execution (comparator defects, scheduler limitations, bridge contract gaps, numerical precision issues) MUST be immediately recorded to `docs/bugs/bugs-soc-func-model.md` using the existing bug entry format. Do not accumulate or batch bug entries.

## Git coordination for parallel commits
Since tasks may execute in parallel waves and commit to `main`, each agent MUST `git pull --rebase origin main` before staging and committing. If a conflict arises, resolve by keeping both changes if possible, or report to orchestrator. Never force-push.

## Success criteria
1. F-FM-03 fixed: element-wise tolerance with NaN/Inf/boundary behavior verified RED→GREEN
2. Signoff runner produces atomic, schema-validated, stale-detecting evidence
3. Synthetic 17-op direct-MMIO + tiled-MMUL stress gates pass at declared dimensions
4. Real Qwen2.5-3B GGUF blk.0 7 projections pass at canonical shapes via direct + tiled paths
5. Connected blk.0 dataflow passes dual-oracle (local operator + float32 graded cosine: ≥ 0.97 PASS, 0.96 ≤ c < 0.97 PASS+WARN, < 0.96 FAIL)
6. Scaled tests renamed, not overclaimed as full-shape
7. Robustness: corruption/boundary/negative coverage deterministic
8. Documentation consistent, not overclaiming RTL or performance
9. Full functional sweep ≥ 638 + new tests, zero failures/skips/xfails
10. W2.2 golden vectors 14/14 PASS
11. All work on main branch with per-task commits
12. F1-F4 Final Wave all APPROVE
