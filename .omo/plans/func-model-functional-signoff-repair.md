# Func Model Functional Signoff Repair Plan

**Branch:** `feat_func_model`
**Baseline:** `773e773` / `origin/feat_func_model`
**Created:** 2026-07-22
**Revised after plan reviews:** 2026-07-23 (second review incorporated)
**Planning source:** `.omo/drafts/func-model-functional-signoff-repair.md`
**Scope:** Func Model functional validation only
**Out of scope:** RTL implementation, RTL testbench repair, SFU RTL batch closure, performance signoff

## 1. Objective

Make the Func Model functional validation signoff-grade for the project target workload: Qwen 3B.

The current scaled/single-tile Qwen coverage is not sufficient to claim Qwen 3B design-target functionality. This plan closes the Func Model-only gaps by:

1. fixing FP16/SFU tolerance comparison semantics;
2. preserving scaled/single-tile Qwen tests as fast bring-up regressions, not final signoff evidence;
3. validating both the legacy synthetic manifest and the real Qwen2.5-3B GGUF provenance before execution;
4. retaining the legacy synthetic 17-op manifest as an oversized topology/stress regression, not real-model evidence;
5. separating direct full-matrix and tiled-scheduler coverage;
6. adding a true-shape, real-weight, real-scale Qwen2.5-3B blk.0 hard gate from the GGUF checkpoint;
7. broadening Func Model negative, boundary, and anti-vacuous coverage around both paths;
8. reconciling Func Model signoff checklist and status documentation.

## 2. Approved decisions

| Decision | Selected policy |
|---|---|
| F-FM-13 Qwen scope | Use a real-checkpoint Qwen2.5-3B blk.0 at GGUF-derived canonical shapes as this plan’s hard functional signoff gate. Scaled/single-tile and legacy synthetic-shape tests are not sufficient. |
| Func Model validation depth | Comprehensive validation. Func Model runs are fast enough that final signoff should run broad tests, not smoke only. |
| RTL scope | Defer RTL, RTL testbench, and SFU RTL batch `526/537` closure to a later RTL-golden-readiness phase. |
| F-FM-03 test policy | TDD. Add failing comparator boundary tests before patching tolerance logic. |
| Performance scope | Defer performance signoff to a separate plan. This plan may clarify status wording but must not claim performance PASS. |

## 2.1 Plan-review findings incorporated

The 2026-07-23 plan review found that the original direction was valid but not yet decision-complete. This revision incorporates the following verified facts:

- `rtl/test_vectors/qwen_blk0/blk0_manifest.json` exists and describes a 17-op, 46-file full-size synthetic workload with Q_proj `1x2560x4096` and down `1x9728x2560`.
- `scripts/gen_blk0_golden.py` proves those files use fixed random seeds and synthetic weights/activations. They are useful stress vectors but are not real Qwen2.5-3B checkpoint evidence.
- The available real model is `/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf`, size `2104932768` bytes, SHA-256 `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d`.
- GGUF metadata gives the canonical target: 36 layers, hidden 2048, intermediate 11008, 16 attention heads, 2 KV heads, head dimension 128. Layer-0 projection shapes are Q/O `2048x2048`, K/V `2048x256`, gate/up `2048x11008`, and down `11008x2048`.
- Existing `llama_ref/refs` payloads report 1536/8960 tensors and are therefore stale 1.5B evidence; they must not be used as the 3B hard-gate oracle.
- The vector directory is approximately 140 MB; the largest individual weight file is approximately 37 MB.
- `FuncModel()` defaults to 64 MB DRAM and 512 KB SRAM in `sim/func_model.py`; the full-shape tests must construct `FuncModel(dram_mb=256)` and use explicit DRAM address windows.
- `sim/tile_scheduler.py` consumes tile-major weights/scales and uses a 256 KB internal SRAM map. This is a distinct validation path from direct MMIO full-matrix execution.
- `sim/tests/test_soc_fm.py` currently caps multiple Qwen paths with `min(..., 64)`.
- `sim/tests/test_engines.py` contains known performance-model failures and must not be part of a functional-only PASS gate.
- No pytest marker configuration currently registers `qwen3b_full`; this plan uses concrete pytest node IDs instead.
- The current functional sweep command in Task 7 was rerun during the second plan review and completed `638 passed, 0 skipped, 0 xfailed` with five warnings in 101.59 seconds. The final case must collect at least this baseline count plus newly added tests and must keep skips/xfails at zero.
- `sim/mmio_bridge.py:159-167` applies per-block weight scales but does not restore the activation scale. The real-model runner must multiply the MXU FP32 result by the recorded activation scale before applying FP32 bias.
- `sim/golden_executor.py:515-530` normalizes RMS values but does not apply the learned RMSNorm weight. The connected runner must perform and verify the learned gamma multiply as a distinct vector step.
- `sim/qwen25_forward.py:223-236` defines the target GQA and attention semantics: repeat 2 KV heads across 16 query heads, divide scores by `sqrt(128)`, and softmax over the key-sequence axis. For the selected one-token decode workload that axis has length one and each per-head probability is exactly 1.
- Plain pytest output does not create the declared `.omo/evidence/*.txt` artifacts. This revision adds one authoritative signoff runner that captures commands, parses test outcomes, validates required metrics, and writes evidence atomically.

Dirty-worktree boundary:

- In scope: `.omo/drafts/func-model-functional-signoff-repair.md`, this plan, and `docs/func-model-signoff-checklist.md`.
- Unrelated and must remain untouched: `.omo/drafts/arc-model-v3-1-constraint-schema.md` and `.omo/plans/arc-model-v3-1-constraint-schema.md`.

Primary implementation anchors:

- `sim/golden_executor.py:650` — `GoldenSFU.compare_hw_vs_ref()`.
- `scripts/verify_w2_2_fm_golden_vectors.py:225` — duplicated FP16 pass/fail semantics.
- `sim/func_model.py:27` — default DRAM/SRAM sizing.
- `sim/tile_scheduler.py:20` — tile scheduler entrypoint and tile-major contract.
- `sim/tests/test_soc_fm.py:1613` — current capped MMUL helper.
- `sim/tests/test_soc_fm.py:1812` — current scaled single-tile 17-op replay.
- `sim/tests/test_soc_fm.py:2541` — current scaled 28-block path.
- `sim/tests/test_soc_fm.py:2812` — current scaled host/PCIe/firmware path.
- `sim/e2e_llamacpp.py:27` — existing row-major to tile-major packing pattern to validate, not blindly duplicate.
- `scripts/gen_blk0_golden.py:3` — provenance of the legacy synthetic manifest.
- `sim/qwen25_forward.py:201` — real-checkpoint blk.0 forward with intermediate capture.
- `ggml-npu/q4_dequant.py:171` — current all-tensor GGUF loader to extend with selective loading.
- `sim/arc_model.py:123` — existing INT4 cosine threshold contract.
- `scripts/gen_qwen25_3b_rtl_vectors.py:241` — existing real-weight INT4 group-128 quantization and scale generation pattern.
- `scripts/gen_qwen25_3b_rtl_vectors.py:304` — existing activation-scale restoration and post-MMUL bias ordering.
- `sim/spike_host.py:423` — existing real-weight MMUL quantization and full-shape scheduling helpers to reuse or factor without invoking RTL.
- `rtl/test_vectors/qwen_blk0/blk0_manifest.json` — authoritative only for the legacy synthetic stress workload.
- `/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf` — authoritative checkpoint for the F-FM-13 real-model gate on this host.

## 3. Current blockers to close

### F-FM-03: FP16/SFU tolerance semantics

Current implementations use array-wide tolerance semantics:

- `sim/golden_executor.py`: `np.all(abs_diff < tol_abs) or np.all(rel_diff < tol_rel)`
- `scripts/verify_w2_2_fm_golden_vectors.py`: same pattern

Required semantics:

```python
np.all((abs_diff <= atol) | (rel_diff <= rtol))
```

Each element must pass either absolute or relative tolerance. The full tensor passes only if every element passes its per-element criterion.

### F-FM-13: Qwen 3B coverage

Current Qwen tests use Qwen-like topology and checked-in vectors but cap shapes to scaled/single-tile sizes in multiple paths, for example `M_eff/K_eff/N_eff = min(..., 64)` in `sim/tests/test_soc_fm.py`.

Required signoff evidence:

- real-checkpoint Qwen2.5-3B blk.0 at GGUF-derived canonical shapes;
- real layer-0 weights, deterministic prompt-derived activation, hardware INT4 group-128 scales, and recorded checkpoint SHA-256;
- all blk.0 op classes and connected data dependencies, not isolated projection-only testing;
- no hidden `min(..., 64)` cap in the signoff path;
- full projection tiling/chunking and output stitching;
- independent golden output comparison;
- anti-vacuous corruption check.

The legacy synthetic manifest is retained as additional oversized 17-op stress coverage. It cannot independently close F-FM-13 because its dimensions and data provenance do not match the real GGUF target.

### F-FM-16: status/evidence consistency

Current docs mix several scopes:

- Func Model Python test status;
- FM-SOC RTL wrapper evidence;
- module-level SFU RTL batch evidence;
- skipped/superseded historical testcase counts;
- performance validation status.

Required outcome:

- Func Model functional signoff status is independent from RTL-golden-readiness and performance signoff.
- Scaled/single-tile Qwen coverage is not described as full-shape coverage.
- RTL/SFU batch issues are recorded as downstream RTL work, not current Func Model blockers.
- Performance remains explicitly not signed off by this plan.

### Signoff evidence and oracle integrity

Every PASS claim must be backed by an evidence artifact produced by the command that actually ran. Test output, subagent summaries, and grep matches are claims until the evidence validator confirms the exact command, non-zero collected test count, result counts, required metrics, and exit status.

The real-model hard gate uses two intentionally separate oracle classes:

1. **Local operator oracle:** an independent NumPy implementation receives the exact quantized input seen by the Func Model and verifies one operator without inheriting upstream model drift. It must not call `GoldenMXU`, `GoldenSFU`, `GoldenVector`, `MMIOBridge`, `tile_mmul()`, or the runner implementation under test.
2. **Float32 model oracle:** `Qwen25Layer.forward_with_intermediates()` uses real dequantized GGUF tensors to measure projection and final-layer quality drift. It is used for cosine thresholds, not for strict SFU/vector operator tolerances after upstream quantization.

No intermediate may pass merely because it is close to the float32 model path if it fails its same-input local operator oracle.

## 4. Work plan

Per-task commit policy: `Commit: No` for every task in this section. This plan authorizes working-tree changes and evidence generation only; it does not authorize staging or committing.

### Task 0A: Add the authoritative Func Model signoff evidence runner

Files expected to change:

- new `scripts/run_func_model_signoff.py`;
- new `sim/tests/test_func_model_signoff_runner.py`.

Implementation requirements:

- Define a static case registry mapping every task/final-gate case ID in this plan to:
  - an argv list executed with `subprocess` and `shell=False`;
  - the exact evidence path;
  - expected exit behavior;
  - minimum collected/passed test counts;
  - whether skips, xfails, or deselection are forbidden;
  - exact tracked/untracked source dependency paths or explicit repository-relative globs used for `source_fingerprint`;
  - required JSON `SIGNOFF_METRIC` records.
- For pytest cases, emit a temporary JUnit XML report and parse it rather than inferring PASS from terminal text.
- Record case ID, UTC start/end, elapsed time, current branch/HEAD, dirty-worktree summary, exact argv, relevant non-secret environment, process exit code, collected/passed/failed/skipped/xfailed counts, required metrics, and final verdict.
- Because this plan does not authorize commits, HEAD alone cannot detect stale evidence. Compute `source_fingerprint` as SHA-256 over the sorted relative path plus content SHA-256 of every in-scope tracked or untracked source/test/script/doc consumed by the case. Record the file list and fingerprint in evidence.
- Exclude generated evidence, temporary JUnit XML, `.pytest_cache`, `__pycache__`, and other runtime caches from `source_fingerprint`; otherwise running a case would invalidate its own evidence.
- Write evidence through a temporary file and atomically rename it on completion. Failed commands must still produce a `FAIL` evidence artifact and return non-zero.
- The comparator RED case is the only expected-failure case: it passes the runner only when the designated mixed abs/rel test fails before Task 2 with the expected assertion signature.
- A hard-gate case fails if it collects zero tests, deselects a named node, skips/xfails any named node, omits a required metric, reports a synthetic/wrong-model identity, or exits non-zero.
- Tests emit machine-readable metrics one per line as `SIGNOFF_METRIC {"case":"<case-id>","key":"<key>","value":<json-value>}`. Reject malformed JSON, wrong case IDs, duplicate keys with conflicting values, non-finite numeric values, or text-only substitutes for required metrics.
- Metric keys are stable and case-sensitive:
  - global identity uses `model.sha256`, `model.hidden`, `model.intermediate`, `model.num_heads`, `model.num_kv_heads`, and `model.head_dim`;
  - projection gates use `projection.<q|k|v|o|gate|up|down>.<m|k|n|input_source|activation_scale|activation_saturation|weight_scale_shape|bias_applied|max_abs|max_rel|cosine|verdict>`; tiled cases additionally require `tile_count`, `remainder_tiles`, and `stitching_verdict`;
  - connected gates use `op.<zero-padded-index>.<name|surface|shape|dtype|input_digest|output_digest|scale|saturation|comparator|local_verdict|model_cosine>` plus `connected.forwarded_output_chain=true`, `attention.kv_repeat=8`, `attention.softmax_axis=sequence`, and `connected.final_cosine`;
  - evidence closure uses `tests.collected`, `tests.passed`, `tests.failed`, `tests.skipped`, `tests.xfailed`, and `evidence.verdict`.
- Provide `validate --case <id>` and `validate --all-functional` modes that re-read evidence, verify the schema and required fields, and reject stale evidence whose recorded HEAD, source fingerprint, or command hash differs from current state.
- The sole source-fingerprint exception is `task-1-comparator-red`, which is intentionally historical TDD proof. Final validation must require that its timestamp precedes Task 2 green evidence, its failure signature is exactly the mixed abs/rel assertion, its command hash still matches the registry, and Task 2 records both the pre-fix and post-fix comparator source hashes. No other stale evidence is allowed.
- The W2.2 case must preserve its existing `build/evidence/w2-2-fm-golden-vectors.md` output and additionally capture the command result in this plan’s `.omo/evidence/` path.

Authoritative case-registry payloads:

| Case ID | Exact payload / required result |
|---|---|
| `task-0a-signoff-runner` | `python3 -m pytest sim/tests/test_func_model_signoff_runner.py -q`; all runner behavior tests pass. |
| `task-0b-qwen3b-synthetic-and-real-preflight` | pytest nodes `sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_assets_preflight` and `sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_model_provenance_and_shapes`; require synthetic op/file counts, GGUF hash, canonical dimensions, and DRAM-layout metrics. |
| `task-1-comparator-red` | node `sim/tests/test_golden_sfu_compare.py::test_compare_mixed_abs_rel_pass`; require the designated assertion failure before Task 2. |
| `task-2-comparator-green` | full `test_golden_sfu_compare.py`, `test_golden_sfu.py`, and `test_golden_sfu_gaps.py`; zero failures/skips/xfails. |
| `task-2-w2-2-golden-vectors` | `python3 scripts/verify_w2_2_fm_golden_vectors.py --skip-dry-run`; require `14/14 PASS`. |
| `task-3-scaled-qwen-regressions` | nodes `sim/tests/test_soc_fm.py::test_blk0_scaled_single_tile_manifest_replay`, `sim/tests/test_soc_fm.py::test_28block_scaled_chain`, and `sim/tests/test_soc_fm.py::test_e2e_host_pcie_doorbell_firmware_scaled_blk0`, plus `python3 scripts/check_func_model_signoff_docs.py --check-scaled-labels`; require all pass and no capped path is currently labeled full-shape. |
| `task-4a-qwen3b-direct-mmio` | node `sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_direct_mmio_17op_replay`; require `op_count=17`, `passed_ops=17`, and `data_provenance=synthetic`. |
| `task-4b-qwen3b-tiled-mmul` | node `sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_tiled_mmul_manifest_ops`; require every manifest MMUL, expected tile counts, and stitched output metrics. |
| `task-4c1-qwen25-3b-selective-load-and-reference-inputs` | node `sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_selective_loading_and_reference_inputs`; require exact GGUF hash, loaded tensor list, canonical shapes, named projection-input shapes, and unchanged float forward hash. |
| `task-4c2-qwen25-3b-real-direct-projections` | node `sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_blk0_direct_projections`; require seven projection records, each with activation scale, saturation, bias count, local tolerance verdict, and cosine. |
| `task-4c3-qwen25-3b-real-tiled-projections` | node `sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_blk0_tiled_projections`; require seven projection records, direct/tiled agreement, tile counts, remainder/stitching verdicts, and cosine. |
| `task-4c4-qwen25-3b-real-connected-blk0` | node `sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_blk0_connected_func_model`; require the ordered op list, local-oracle result per op, gamma/bias/GQA/softmax/bridge metrics, no oracle-value forwarding, and required cosines. |
| `task-5-qwen3b-robustness` | nodes `sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_validation_rejects_corruption`, `sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_validation_rejects_invalid_descriptor`, `sim/signoff/test_qwen_blk0_synthetic_stress.py::test_qwen_blk0_synthetic_tiled_boundary_coverage`, `sim/signoff/test_qwen25_3b_real_blk0.py::test_qwen25_3b_real_blk0_rejects_corruption_and_shape_substitution`, and `sim/tests/test_soc_fm.py`; all intentional corruptions must be detected. |
| `task-6-signoff-doc-consistency` | `python3 -m pytest sim/tests/test_func_model_signoff_docs.py -q` followed by `python3 scripts/check_func_model_signoff_docs.py`; require semantic scope/status assertions, not grep-count success. |
| `task-7-functional-selected-regression` | the 11 explicitly listed functional test files from this plan’s Task 7 minimum set; zero failures/skips/xfails. |
| `task-7-functional-full-sweep` | `python3 -m pytest sim/tests/ -q --ignore=sim/tests/test_soc_pcie_dma.py --ignore=sim/tests/test_engines.py`; collect at least 638 baseline tests plus all newly added `sim/tests/` tests, with zero failures/skips/xfails. |
| `task-7-qwen3b-synthetic-stress-gates` | all Task 0B/4A/4B/5 synthetic nodes; require provenance remains synthetic and every hard node executes. |
| `task-7-qwen25-3b-real-blk0-hard-gate` | real provenance plus Tasks 4C1–4C4 and the real corruption node; require exact checkpoint identity and no skipped/deselected node. |
| `task-7-w2-2-golden-vectors` | W2.2 command above; require `14/14 PASS`. |
| `final-plan-compliance` | runner `validate --all-functional` plus acceptance-to-case mapping; require every criterion covered by current evidence. |
| `final-code-quality` | compile every changed in-scope Python file, then run `sim/tests/test_func_model_signoff_runner.py`, `sim/tests/test_func_model_signoff_docs.py`, `sim/tests/test_golden_sfu_compare.py`, and the four Task 4C1–4C4 nodes; require clean completion and the oracle forbidden-import/call guard. |
| `final-real-qa` | execute the four Task 4C1–4C4 pytest nodes as one fresh composite payload without overwriting task evidence; require fresh timestamps and all required real-model metrics. |
| `final-scope-fidelity` | baseline/worktree and semantic-doc scope checker; require no prohibited RTL/performance/unrelated-file changes. |

Concrete verification:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-0a-signoff-runner
```

Expected result:

- success, failure, expected-RED, zero-test, skip/xfail, missing-metric, stale-HEAD, stale-source-fingerprint, stale-command, and atomic-write unit cases pass;
- no task can claim PASS merely because a log contains the word `PASS`.

Evidence:

- `.omo/evidence/task-0a-signoff-runner.txt`

### Task 0B: Add synthetic-vector and real-GGUF provenance, capacity, and environment preflight

Files expected to change:

- new `sim/qwen_blk0_synthetic_vectors.py` for focused legacy-manifest loading and tile-layout helpers;
- new `sim/signoff/test_qwen_blk0_synthetic_stress.py` for explicit synthetic stress gates;
- new `sim/signoff/test_qwen25_3b_real_blk0.py` for the true-model hard gate.

Implementation requirements:

- Load `rtl/test_vectors/qwen_blk0/blk0_manifest.json` only as the legacy synthetic stress source; do not label it real-model data.
- Assert its model label, layer `blk.0`, `num_ops == 17`, and 17 manifest ops.
- Assert all 46 manifest file entries exist, their declared format is supported, and every SHA-256 matches.
- Assert every synthetic op resolves exactly one required golden file and all required input/weight/extra operands; ambiguous filename-prefix lookup is a failure.
- Assert the synthetic stress dimensions are preserved, including:
  - Q_proj `M=1, K=2560, N=4096`;
  - O_proj `M=1, K=4096, N=2560`;
  - gate/up `M=1, K=2560, N=9728`;
  - down `M=1, K=9728, N=2560`.
- Resolve the real model from required environment variable `QWEN3B_GGUF`; the execution environment for this plan sets it to `/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf`.
- Assert the real GGUF size and SHA-256 are exactly `2104932768` bytes and `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d`.
- Assert GGUF metadata and layer-0 tensors match:
  - layers 36, hidden 2048, intermediate 11008, heads 16, KV heads 2, head dimension 128;
  - Q/O `K=2048, N=2048`;
  - K/V `K=2048, N=256`;
  - gate/up `K=2048, N=11008`;
  - down `K=11008, N=2048`.
- Assert original GGUF types: Q/K/O/gate/up `Q4_K`, V/down `Q6_K`, norms and Q/K/V biases `F32`.
- Fail rather than falling back to the 1.5B model or to synthetic vectors.
- Define non-overlapping, reusable DRAM windows for activation, row-major weight, tile-major weight, scales, and output within a 256 MB DRAM instance.
- Assert each op’s input, weight, scale, and output byte range fits its assigned window before writing.
- Construct full-shape test models with `FuncModel(dram_mb=256)`; do not change the product default merely to make tests pass.
- Keep all commands under the repository’s required `PYTHONPATH=sim` environment.

Concrete verification:

```bash
QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf \
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-0b-qwen3b-synthetic-and-real-preflight
```

Expected result:

- synthetic metadata/files/hashes, real GGUF identity/shapes, and DRAM layout all pass;
- a missing/corrupt asset or overlapping/out-of-range DRAM window fails deterministically before any compute is run.

Evidence:

- `.omo/evidence/task-0b-qwen3b-synthetic-and-real-preflight.txt`

### Task 1: Add F-FM-03 comparator regression tests first

Files expected to change:

- new `sim/tests/test_golden_sfu_compare.py`

Add tests for:

1. mixed abs/rel pass:
   - at least one element passes only `atol`;
   - at least one element passes only `rtol`;
   - full array should pass under element-wise semantics.
   - use test node `test_compare_mixed_abs_rel_pass` and assertion message `mixed abs/rel must pass element-wise`; Task 0A matches this exact signature for the RED evidence.
2. out-of-tolerance fail:
   - one element exceeds both `atol` and `rtol`;
   - full array must fail.
3. NaN/Inf mismatch fail:
   - any NaN must fail;
   - same-position, same-sign infinities pass;
   - opposite-sign or finite-vs-infinite values fail.
4. exact-boundary behavior:
   - error exactly equal to tolerance should pass using `<=`.

Verification before implementation:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-1-comparator-red
```

Expected result before patch: at least the mixed abs/rel test fails against current implementation.

Evidence:

- `.omo/evidence/task-1-comparator-red.txt`

### Task 2: Fix FP16 tolerance semantics

Files expected to change:

- `sim/golden_executor.py`
- `scripts/verify_w2_2_fm_golden_vectors.py`

Implementation requirements:

- Use element-wise `(abs_diff <= atol) | (rel_diff <= rtol)`.
- Use `np.all(...)` only after the per-element OR.
- Reject any NaN.
- Accept same-position, same-sign infinities; reject opposite-sign and finite-vs-infinite mismatches.
- Keep existing metrics such as `max_abs_err`, `mean_abs_err`, and `max_rel_err`.
- Avoid broad refactoring unless it materially reduces duplicated comparator semantics without import-cycle risk.
- Read the pre-fix comparator source hash from Task 1 RED evidence and record both pre-fix and post-fix hashes in Task 2 green evidence so the final validator can preserve the TDD lineage without accepting arbitrary stale evidence.

Focused verification:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-2-comparator-green
```

Golden-vector verification:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-2-w2-2-golden-vectors
```

Expected result:

- comparator tests pass;
- SFU/gap tests pass;
- W2.2 golden vectors remain `14/14 PASS`.

Evidence:

- `.omo/evidence/task-2-comparator-green.txt`
- `.omo/evidence/task-2-w2-2-golden-vectors.txt`

### Task 3: Reclassify scaled/single-tile Qwen tests

Files expected to inspect/change:

- `sim/tests/test_soc_fm.py`
- `sim/gen_soc_rtl_vectors.py`
- `rtl/testcase-list-soc-fm.md`
- `docs/func-model-signoff-checklist.md`
- new `scripts/check_func_model_signoff_docs.py`
- new `sim/tests/test_func_model_signoff_docs.py`

Implementation requirements:

- Do not remove scaled/single-tile tests; keep them as fast regressions.
- Rename and document the current capped paths so their scope is explicit:
  - `test_blk0_full_chain_single_tile` → `test_blk0_scaled_single_tile_manifest_replay`;
  - `test_28block_chain` → `test_28block_scaled_chain`;
  - `test_e2e_host_pcie_doorbell_firmware_compute` → `test_e2e_host_pcie_doorbell_firmware_scaled_blk0`.
- Update any direct node-ID callers for these renamed tests.
- Update testcase descriptions that claim “Full Qwen2.5-3B blk.0” when actual dimensions are capped.
- Preserve historical RTL evidence, but label it as scaled/single-tile or RTL-specific as applicable.
- Mark F-FM-13 as not closed until full-shape Qwen 3B blk.0 evidence exists.

Verification:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-3-scaled-qwen-regressions
```

The registered case must run the three concrete renamed pytest nodes and a semantic documentation checker. The checker, not raw `rg` exit status, must fail if a current signoff claim calls a capped path full-shape; retained historical matches are allowed only when explicitly labeled historical/scaled.

Expected result:

- Any remaining “full” wording is accurate.
- Shape caps are explicit fast-regression behavior, not signoff evidence.

Evidence:

- `.omo/evidence/task-3-scaled-qwen-regressions.txt`

### Task 4A: Add the legacy synthetic 17-op direct-MMIO stress gate

Files expected to change:

- `sim/qwen_blk0_synthetic_vectors.py`;
- `sim/signoff/test_qwen_blk0_synthetic_stress.py`.

Gate definition:

- Execute all 17 legacy synthetic manifest ops at their declared dimensions through `FuncModel`/`MMIOBridge`.
- Use the manifest’s recorded synthetic input/weight files for each op and compare against the manifest’s pre-generated golden output file.
- Replay is op-by-op with recorded per-op inputs. It validates the complete synthetic 17-op manifest and numerical implementation, but must not be described as real-checkpoint or connected host/firmware dataflow evidence.
- Use explicit DRAM addresses for full-shape MMUL activation, weight, and output buffers; do not enlarge SRAM or cap dimensions.
- MMUL and integer/vector outputs compare exactly to manifest golden data.
- SFU FP16 outputs use the corrected element-wise comparator with `atol=2e-3`, `rtol=1e-2`.
- Assert the programmed/executed M/K/N or element count equals the manifest value for every op.
- Record model, layer, op index/name/opcode, dimensions, dtype/quantization, golden filename/hash, comparator, and elapsed time in the evidence output.
- Do not call `GoldenMXU`, `GoldenSFU`, or `GoldenVector` to generate the expected result inside this gate; the checked-in manifest golden vectors are the independent oracle.

Concrete verification:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-4a-qwen3b-direct-mmio
```

Expected result:

- exactly 17/17 manifest ops execute and pass;
- no executed dimension is less than its manifest dimension;
- a missing golden file, unsupported opcode, or status other than DONE fails the gate;
- the evidence identifies this as legacy synthetic direct-MMIO replay, not real-model, tiled-scheduler, or connected firmware coverage.

Evidence:

- `.omo/evidence/task-4a-qwen3b-direct-mmio.txt`

### Task 4B: Add a separate legacy synthetic tiled-MMUL scheduler stress gate

Files expected to inspect/change:

- `sim/qwen_blk0_synthetic_vectors.py`;
- `sim/signoff/test_qwen_blk0_synthetic_stress.py`;
- `sim/tile_scheduler.py` only if the full-shape tests expose a real scheduler correctness defect.

Gate definition:

- Run every MMUL op in the legacy synthetic 17-op manifest through the existing firmware descriptor / `tile_mmul()` path at its declared M/K/N.
- Convert checked-in row-major packed INT4 weights to the tile-major layout required by `sim/tile_scheduler.py`; do not reinterpret the row-major file as tile-major.
- Generate explicit unity FP32 scales because the current manifest golden MMUL outputs are raw INT32 and the manifest has no scale files.
- Compare scheduler FP32 output against the manifest INT32 golden converted to FP32 using fixed `atol=1e-4`, `rtol=1e-5`.
- Verify the scheduler’s first, middle, last, and remainder tiles whenever the op has those cases.
- Verify final DRAM output stitching for the full N dimension.
- Assert tile counts from execution equal `ceil(K/128) * ceil(N/128)` and match the intended full-shape coverage; no `min(..., 64)` or first-tile-only path is allowed.
- Preserve the direct-MMIO gate as an independent gate. A pass in Task 4A cannot substitute for Task 4B.
- Neither Task 4A nor Task 4B can substitute for the real-GGUF hard gates in Tasks 4C1–4C4.

Concrete verification:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-4b-qwen3b-tiled-mmul
```

Expected result:

- all manifest MMUL ops pass through the tiled scheduler at full dimensions;
- output stitching covers the complete declared N dimension;
- tile-major conversion and unity-scale semantics are explicit in evidence;
- any scheduler limitation is reported as a Func Model signoff blocker, not bypassed by falling back to direct MMIO.

Evidence:

- `.omo/evidence/task-4b-qwen3b-tiled-mmul.txt`

### Task 4C1: Add selective real-GGUF loading and deterministic reference inputs

Files expected to inspect/change:

- `ggml-npu/q4_dequant.py`;
- `sim/qwen25_forward.py`;
- new `sim/qwen25_func_model.py`;
- new `sim/signoff/test_qwen25_3b_real_blk0.py`.

Implementation requirements:

- Source model is `QWEN3B_GGUF` with the size/hash validated in Task 0B.
- Workload is deterministic decode-token blk.0: prompt `"Hello"`, token id `9707`, batch/sequence `M=1`, position `0`.
- Add `load_selected_weights_from_gguf(path, tensor_names)` and `load_tensor_row_from_gguf(path, tensor_name, row_index)` without changing existing `load_weights_from_gguf()` behavior.
- Load only layer-0 Q/K/V/O/gate/up/down weights, Q/K/V biases, both RMSNorm weights, and token-embedding row 9707. Do not dequantize all 36 layers or the full embedding table.
- Use two explicit memory phases:
  - reference phase: selectively load all layer-0 tensors needed by `Qwen25Layer`, run `forward_with_intermediates()` once, retain only the named FP32 inputs/outputs/biases/norms/embedding, then release the projection matrices;
  - hardware phase in Tasks 4C2–4C4: reload/dequantize one projection at a time, produce its packed INT4/scales and required float32 comparison, then release that FP32 matrix before the next projection.
- Extend `Qwen25Layer.forward_with_intermediates()` without changing `forward()` results so it also exposes the exact projection inputs:
  - Q/K/V input: `attn_norm`;
  - O input: `attn_concat`;
  - gate/up input: `ffn_norm`;
  - down input: `ffn_hidden`.
- Preserve Q/K/V post-bias values and expose Q/K rotations, repeated K/V heads, per-head scores/probabilities, and attention concatenation so later gates never infer an input from an output.
- Model dimensions and tensor shapes come from GGUF metadata/tensors, never the legacy manifest or hard-coded 2560/9728 dimensions.
- Preserve and report original GGUF quantization type separately from the hardware INT4 re-quantization.

Concrete verification:

```bash
QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf \
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-4c1-qwen25-3b-selective-load-and-reference-inputs
```

Expected result:

- only the required tensors/row are loaded;
- every canonical tensor and named projection input has the expected shape;
- the extended intermediate capture leaves the existing `forward()` final output bit-identical.

Evidence:

- `.omo/evidence/task-4c1-qwen25-3b-selective-load-and-reference-inputs.txt`

### Task 4C2: Add an independent quantized oracle and real direct-MMIO projection gate

Files expected to inspect/change:

- `sim/qwen25_func_model.py`;
- new `sim/qwen25_signoff_oracle.py`;
- `sim/signoff/test_qwen25_3b_real_blk0.py`;
- `scripts/gen_qwen25_3b_rtl_vectors.py:304-330` and `sim/mmio_bridge.py:141-181` as contracts to verify, not oracle code to call.

Quantized data-domain contract:

- Re-quantize each real projection weight to signed INT4, group size 128:
  - logical direct-MMIO matrix is row-major `(K, N)`;
  - packed values are two signed nibbles per byte;
  - scales are FP32 with logical shape `(ceil(K/128), N)`.
- For each named reference input `x`, compute `activation_scale = max(abs(x)) / 127` when non-zero, otherwise `1.0`.
- Compute `act_int8 = clip(round(x / activation_scale), -128, 127).astype(int8)` using NumPy round-to-nearest-even. Record pre-clip extrema and saturation count.
- The independent oracle must unpack INT4 itself and compute each K-group with INT32 products/accumulation, then apply that group’s FP32 weight scale and accumulate FP32 groups.
- `MMIOBridge` output with `SCALE_ADDR != 0` is the weight-scaled value only. Restore the activation domain exactly once:

  ```text
  restored = mmio_weight_scaled_output * activation_scale
  projection = restored + bias_fp32  # Q/K/V only; no bias on O/gate/up/down
  ```

- Bias is applied in FP32 after activation-scale restoration. It must never be folded into INT32 accumulation or applied twice.
- The independent oracle must not call `GoldenMXU`, `MMIOBridge`, `tile_mmul()`, or any implementation helper that performs the same unpack/matmul path.
- Keep the local oracle in `sim/qwen25_signoff_oracle.py`. It may import NumPy and shared immutable constants only; it must not import `qwen25_func_model`, `golden_executor`, `mmio_bridge`, `tile_scheduler`, or call production packing/unpacking helpers. Add a source-level import/call guard test.

Gate definition:

- Execute Q/K/V/O/gate/up/down at canonical full shapes through direct MMIO using the exact named inputs from Task 4C1.
- Compare restored pre-bias and post-bias outputs against the independent quantized oracle using element-wise `atol=1e-4`, `rtol=1e-5`.
- Compare each final quantized projection against the corresponding float32 real-weight projection using cosine similarity `>= 0.96`.
- Emit one metric record per projection containing input source, M/K/N, original GGUF type, hardware packed type, activation scale, scale tensor shape, saturation count, bias presence, numerical errors, cosine, and elapsed time.

Concrete verification:

```bash
QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf \
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-4c2-qwen25-3b-real-direct-projections
```

Expected result:

- all seven direct-MMIO projections execute at canonical shape;
- activation scale is restored once and Q/K/V bias is applied once;
- local-oracle tolerances and all seven projection cosine thresholds pass.

Evidence:

- `.omo/evidence/task-4c2-qwen25-3b-real-direct-projections.txt`

### Task 4C3: Add the real-GGUF tiled-scheduler projection gate

Files expected to inspect/change:

- `sim/qwen25_func_model.py`;
- `sim/qwen25_signoff_oracle.py`;
- `sim/signoff/test_qwen25_3b_real_blk0.py`;
- `sim/tile_scheduler.py` only if the gate exposes a real scheduler correctness defect.

Gate definition:

- Convert each Task 4C2 logical `(K,N)` packed weight into the scheduler’s `(n_tile, k_block)` 128×128 tile-major byte layout, padding only the final K/N tile.
- Convert direct scales `(ceil(K/128), N)` into scheduler tile-major scale blocks. Do not reinterpret direct-layout bytes as scheduler-layout bytes.
- Use the same `act_int8`, activation scale, biases, and independent quantized oracle as Task 4C2.
- Execute Q/K/V/O/gate/up/down through `tile_mmul()` at canonical full shapes.
- Require executed tile count `ceil(K/128) * ceil(N/128)`, verify first/middle/last tiles, verify the down/gate/up N remainder, and verify complete output stitching.
- Restore activation scale once after the stitched scheduler output, then apply Q/K/V bias once.
- Compare tiled output to the independent oracle and direct-MMIO output with `atol=1e-4`, `rtol=1e-5`; require the same per-projection cosine `>= 0.96`.
- A scheduler fallback to direct MMIO, hidden dimension cap, first-tile-only comparison, or missing remainder metric fails.

Concrete verification:

```bash
QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf \
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-4c3-qwen25-3b-real-tiled-projections
```

Expected result:

- all seven tiled projections pass the local oracle, direct-path agreement, full tile-count/stitching checks, and cosine thresholds.

Evidence:

- `.omo/evidence/task-4c3-qwen25-3b-real-tiled-projections.txt`

### Task 4C4: Add the connected real-GGUF blk.0 dual-oracle hard gate

Files expected to inspect/change:

- `sim/qwen25_func_model.py`;
- `sim/qwen25_signoff_oracle.py`;
- `sim/signoff/test_qwen25_3b_real_blk0.py`;
- `sim/qwen25_forward.py` only for the additive intermediate capture defined in Task 4C1.

Connected execution contract:

- Execute actual outputs as subsequent inputs through:
  RMSNorm → learned gamma multiply → Q/K/V → FP32 bias → RoPE → GQA attention score → per-head sequence softmax → attention/value → O → residual bridge → RMSNorm → learned gamma multiply → gate/up → SiLU → Vector multiply bridge → down → residual bridge.
- Classify and record every chain step by execution surface:
  - `MXU_MMIO`: quantized projections and attention MMULs;
  - `SFU_MMIO`: RMSNorm core, RoPE, softmax, and SiLU;
  - `VECTOR_MMIO`: supported INT32 ADD/MUL/RESID/CONV operations;
  - `FUNC_BRIDGE`: activation-scale restoration, FP32 bias, learned gamma, reshape/repeat, and score scaling that have no standalone MMIO opcode.
- `FUNC_BRIDGE` operations are explicit parts of `qwen25_func_model.py`, take the prior Func Model output, emit a recorded tensor, and are checked against `qwen25_signoff_oracle.py`. They must not be hidden inside a test assertion.
- A NumPy-computed oracle value must never replace the value forwarded to the next step. The next step receives the actual `MXU_MMIO`, `SFU_MMIO`, `VECTOR_MMIO`, or declared `FUNC_BRIDGE` output.
- Apply the Task 4C2 activation-scale restoration and bias ordering at every projection.
- The seven canonical Q/K/V/O/gate/up/down projections in the connected chain must use the validated Task 4C3 tiled-scheduler path. The small attention-score/value MMULs may use direct MMIO but must still pass an independent same-input quantized oracle. Direct full-matrix fallback for a canonical projection is forbidden.
- RMSNorm local semantics are `x / sqrt(mean(x^2) + eps)` followed by a separate element-wise learned-gamma multiply. `GoldenSFU.rmsnorm_hw()` does not implicitly apply gamma.
- Qwen2.5 GQA semantics are fixed:
  - reshape Q as 16×128 and K/V as 2×128;
  - repeat each KV head eight times to align with 16 query heads;
  - divide each score by `sqrt(128)`;
  - softmax over the key-sequence axis independently per head.
- For this `M=1`, position-0 workload the key-sequence axis length is one, so every per-head attention probability must equal 1 within the SFU local tolerance. A softmax across the 16 head scores is a deterministic failure.
- Exercise the actual Vector MMIO data types from `sim/mmio_bridge.py:362-407`:
  - ADD/MUL operands and results are INT32 with saturation;
  - RESID consumes FP16 original plus INT32 delta and returns INT32;
  - CONV converts INT32 to FP16 before the next SFU/activation boundary.
- Do not relabel residual or VMUL as `FUNC_BRIDGE`: those operations have Vector MMIO contracts and must use them. Do not introduce an unmodeled FP32 residual/VMUL bypass or arbitrary rescaling solely to improve cosine. If the current Vector contract causes the real connected layer to miss quality thresholds, record it as a Func Model design/signoff blocker.

Dual-oracle verification:

- For every MXU/SFU/Vector operation, compare against an independent same-input local NumPy oracle:
  - quantized MXU: `atol=1e-4`, `rtol=1e-5`;
  - FP16 SFU: element-wise `atol=2e-3`, `rtol=1e-2`;
  - FP32 `FUNC_BRIDGE` scale/bias/gamma/score operations: element-wise `atol=1e-6`, `rtol=1e-6`;
  - bridge reshape/repeat operations: exact shape and value equality;
  - INT32 Vector ADD/MUL/RESID and conversion input: exact equality, including saturation;
  - FP16 conversion output: exact FP16 bit equality.
- Local operator comparisons consume the actual hardware-model input at that operation. They must not compare a quantized-chain intermediate directly to a float32 reference intermediate using strict tolerances.
- Separately compare named dequantized projection outputs and final blk.0 output to `Qwen25Layer.forward_with_intermediates()` using cosine similarity `>= 0.96`.
- Do not use stale `llama_ref/refs` data. Record every boundary’s shape, dtype, activation/weight/fixed-point scale where applicable, saturation count, local comparator, local verdict, and model-quality cosine where applicable.
- A missing gamma/bias, wrong softmax axis, wrong GQA repeat, unsupported true shape, scheduler fallback, skipped op, oracle-value forwarding, or synthetic-data substitution fails the gate.

Concrete verification:

```bash
QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf \
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-4c4-qwen25-3b-real-connected-blk0
```

Expected result:

- every connected operator passes its same-input local oracle;
- all required GQA/attention and bridge metrics are present;
- every required projection/final cosine passes, otherwise F-FM-13 remains blocked with the exact failing bridge/op identified.

Evidence:

- `.omo/evidence/task-4c4-qwen25-3b-real-connected-blk0.txt`

### Task 5: Add broad Qwen 3B Func Model robustness coverage

Files expected to inspect/change:

- `sim/qwen_blk0_synthetic_vectors.py`
- `sim/signoff/test_qwen_blk0_synthetic_stress.py`
- `sim/signoff/test_qwen25_3b_real_blk0.py`
- `sim/tests/test_soc_fm.py`

Coverage requirements:

- op classes used by Qwen 3B blk.0:
  - q/k/v/o projection;
  - gate/up/down MLP projection;
  - RMSNorm;
  - softmax;
  - RoPE;
  - activation such as SiLU/GELU if used by the selected model path;
  - residual/vector ops;
  - DMA/host path if used by the Func Model chain.
- negative/anti-vacuous checks:
  - corrupt one weight slice in memory, without modifying checked-in assets, and prove comparison fails;
  - corrupt one activation slice in memory, without modifying checked-in assets, and prove comparison fails;
  - wrong descriptor dimension must fail or raise a deterministic error;
  - wrong output address must fail or be detected;
  - tolerance-exceeding FP16 output must fail.
- boundary checks:
  - canonical hidden 2048 and intermediate 11008;
  - 16 query heads, 2 KV heads, head dimension 128;
  - decode sequence length 1 at position 0;
  - synthetic scheduler helper case `M=1, K=129, N=130` to force both K and N remainder tiles;
  - first, middle, last, and remainder tile behavior.

Verification:

```bash
QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf \
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-5-qwen3b-robustness
```

Expected result:

- existing SoC Func Model tests remain green;
- synthetic stress, real-model, and negative tests pass;
- failures are deterministic and not dependent on RTL/VCS.

Evidence:

- `.omo/evidence/task-5-qwen3b-robustness.txt`

### Task 6: Reconcile checklist and evidence documentation

Files expected to change:

- `docs/func-model-signoff-checklist.md`
- `rtl/testcase-list-soc-fm.md` only for scope wording, not RTL closure
- `scripts/check_func_model_signoff_docs.py`
- `sim/tests/test_func_model_signoff_docs.py`

Documentation requirements:

- Functional Func Model signoff reflects actual evidence after tests run.
- F-FM-13 only becomes PASS if Tasks 4C1–4C4 pass, in addition to the Task 4A/4B synthetic stress gates.
- State explicitly that Task 4A/4B use fixed-seed synthetic data and non-canonical 2560/9728 dimensions, while Tasks 4C1–4C4 use the real checkpoint and canonical 2048/11008 dimensions.
- State that Task 4A is op-by-op replay with recorded inputs, Task 4B validates synthetic scheduler tiling/stitching, Task 4C2/4C3 validate real direct/tiled projections, and Task 4C4 validates connected real blk.0 dataflow with dual oracles.
- RTL-golden-readiness remains deferred/partial because RTL/SFU batch is out of scope.
- Performance signoff remains FAIL/PARTIAL and explicitly separate.
- Historical evidence is dated and scoped.
- No statement implies scaled/single-tile validates full Qwen 3B behavior.
- No statement describes the legacy synthetic manifest as real Qwen weights or canonical Qwen2.5-3B shape evidence.

Consistency checks:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-6-signoff-doc-consistency
```

The registered semantic checker may use `rg` for discovery, but its PASS/FAIL decision must parse each match’s section/scope. A raw zero/non-zero `rg` status is not signoff evidence.

Expected result:

- no stale status count is presented as current Func Model signoff;
- any `526/537` occurrence is clearly downstream RTL evidence;
- performance status is not upgraded by this functional plan.

Evidence:

- `.omo/evidence/task-6-signoff-doc-consistency.txt`

### Task 7: Run comprehensive Func Model signoff gates

The selected functional regression case must execute exactly:

- `sim/tests/test_golden_smoke.py`;
- `sim/tests/test_golden_sfu_compare.py`;
- `sim/tests/test_golden_sfu.py`;
- `sim/tests/test_golden_sfu_gaps.py`;
- `sim/tests/test_golden_vector.py`;
- `sim/tests/test_golden_mxu_quant.py`;
- `sim/tests/test_golden_mxu_edges.py`;
- `sim/tests/test_golden_dma.py`;
- `sim/tests/test_pcie_dma_fm.py`;
- `sim/tests/test_tile_scheduler.py`;
- `sim/tests/test_soc_fm.py`.

Minimum final verification:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-7-functional-selected-regression
```

Full functional sweep, explicitly excluding the known unrelated PCIe SoC test and performance-engine tests:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-7-functional-full-sweep
```

Legacy synthetic stress gates, selected by concrete node ID rather than an unregistered marker:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-7-qwen3b-synthetic-stress-gates
```

Real-checkpoint Qwen2.5-3B blk.0 hard gate:

```bash
QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf \
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-7-qwen25-3b-real-blk0-hard-gate
```

Golden-vector gate:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
  --case task-7-w2-2-golden-vectors
```

Evidence closure:

```bash
PYTHONPATH=sim python3 scripts/run_func_model_signoff.py validate \
  --all-functional
```

Expected signoff result:

- no unexplained Func Model functional failures;
- legacy synthetic stress gates pass without being misreported as real-model coverage;
- real-checkpoint, canonical-shape Qwen2.5-3B blk.0 passes both projection and connected-dataflow gates;
- negative tests prove the validation is non-vacuous;
- performance failures, if still present, are explicitly excluded from this functional signoff and left for the later performance plan.

Evidence:

- `.omo/evidence/task-7-functional-selected-regression.txt`
- `.omo/evidence/task-7-functional-full-sweep.txt`
- `.omo/evidence/task-7-qwen3b-synthetic-stress-gates.txt`
- `.omo/evidence/task-7-qwen25-3b-real-blk0-hard-gate.txt`
- `.omo/evidence/task-7-w2-2-golden-vectors.txt`

### Final OMO review wave

Run after Tasks 0A–7. All four lanes must execute independently and approve before the checklist is marked PASS:

1. Plan compliance audit:

   ```bash
   PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
     --case final-plan-compliance
   ```

   The case must map every acceptance criterion to a current-HEAD/current-source-fingerprint evidence artifact, run `validate --all-functional`, and reject missing, stale, skipped, xfailed, synthetic-substituted, zero-test, or unexecuted hard gates.

2. Code-quality review:

   ```bash
   PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
     --case final-code-quality
   ```

   The case must run `python3 -m compileall -q` on every changed in-scope Python path, the evidence-runner unit suite, comparator suite, selective-loader tests, and focused direct/tiled/connected helper tests. No project type-checker or linter is configured, so this plan does not invent a non-authoritative lint gate; review must still reject duplicated/self-confirming oracle logic through focused source assertions and tests.

3. Real manual QA:

   ```bash
   QWEN3B_GGUF=/home/zhengs/models/qwen2.5-3b-instruct-q4_k_m.gguf \
   PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
     --case final-real-qa --force
   ```

   This case must execute the exact underlying pytest nodes from Tasks 4C1–4C4 as one fresh composite run and assert the exact GGUF hash, canonical dimensions, seven direct and tiled projection results, activation-scale restoration, bias/gamma ordering, GQA/softmax semantics, tile counts, saturation counts, local-oracle verdicts, and final cosine. It writes only `.omo/evidence/final-real-qa.txt`; it must not overwrite Task 4C evidence while the parallel compliance lane reads it.

4. Scope-fidelity audit:

   ```bash
   PYTHONPATH=sim python3 scripts/run_func_model_signoff.py run \
     --case final-scope-fidelity
   ```

   Compare the worktree against baseline `773e773`; reject changes to RTL implementation/testbench files or performance-model behavior, allow `rtl/testcase-list-soc-fm.md` only for scope wording, verify unrelated pre-existing untracked OMO files remain untouched, and reject documentation that upgrades RTL or performance signoff. Ignore only generated `.omo/evidence/`, temporary JUnit XML, `.pytest_cache`, and `__pycache__` paths in this scope diff.

Evidence:

- `.omo/evidence/final-plan-compliance.txt`
- `.omo/evidence/final-code-quality.txt`
- `.omo/evidence/final-real-qa.txt`
- `.omo/evidence/final-scope-fidelity.txt`

### Execution dependencies and stop conditions

Parallel execution waves:

1. Wave 0: Task 0A evidence runner.
2. Wave 1 after 0A: Tasks 0B, 1, and 3 in parallel.
3. Wave 2 after Wave 1 prerequisites: Tasks 2, 4B, and 4C1 in parallel.
4. Wave 3: Tasks 4A and 4C2 in parallel.
5. Wave 4: Task 4C3.
6. Wave 5: Task 4C4.
7. Wave 6: Task 5.
8. Wave 7: Task 6.
9. Wave 8: Task 7.
10. Final wave: all four final OMO review cases in parallel.

The real-model critical path is `0A → 0B → 4C1 → 4C2 → 4C3 → 4C4 → 5 → 6 → 7 → final review`. The single-task real-model waves are intentional because each consumes the prior gate’s validated representation and oracle contract.

| Task | Depends on | Blocks |
|---|---|---|
| Task 0A evidence runner | none | all evidence-producing tasks |
| Task 0B preflight | Task 0A | Tasks 4A, 4B, 4C1, 5 |
| Task 1 comparator RED tests | Task 0A | Task 2 |
| Task 2 comparator fix | Task 1 | Tasks 4A, 4C4, 5, 7 |
| Task 3 scaled-test reclassification | Task 0A | Task 6 |
| Task 4A synthetic direct-MMIO gate | Tasks 0A, 0B, 2 | Tasks 5, 6, 7 |
| Task 4B synthetic tiled-MMUL gate | Tasks 0A, 0B | Tasks 5, 6, 7 |
| Task 4C1 selective real-data/reference inputs | Tasks 0A, 0B | Tasks 4C2, 4C3, 4C4 |
| Task 4C2 real direct projections | Task 4C1 | Tasks 4C3, 4C4, 5, 6, 7 |
| Task 4C3 real tiled projections | Task 4C2 | Tasks 4C4, 5, 6, 7 |
| Task 4C4 connected dual-oracle gate | Tasks 2, 4C1, 4C2, 4C3 | Tasks 5, 6, 7 |
| Task 5 robustness coverage | Tasks 2, 4A, 4B, 4C4 | Tasks 6, 7 |
| Task 6 documentation | Tasks 3, 4A, 4B, 4C1–4C4, 5 | Task 7 |
| Task 7 final gates | Tasks 0A–6 | final OMO review wave |
| Final OMO review wave | Task 7 | signoff decision |

Stop and report the exact blocker instead of weakening coverage when:

- a manifest file is missing or its SHA-256 does not match;
- the required real GGUF is missing, has the wrong hash, or reports non-canonical metadata/tensor shapes;
- the full-shape DRAM layout cannot fit in 256 MB without overlap;
- an opcode cannot execute at its manifest dimension;
- the tiled scheduler cannot consume a correctly converted tile-major representation;
- the real path falls back to synthetic vectors, wrong 2560/9728 dimensions, or a 1.5B checkpoint;
- activation scale is omitted, restored twice, or bias/gamma ordering is ambiguous;
- an independent oracle value is forwarded instead of the declared MMIO or `FUNC_BRIDGE` output;
- a same-input local operator oracle and float32 model-quality oracle cannot be kept independent;
- the Vector MMIO bridge contract misses the cosine gate; report the exact bridge/op rather than adding an unmodeled FP32 bypass;
- required evidence is missing, stale by source fingerprint/command hash, zero-test, skipped/xfailed, or lacks required metrics;
- an anti-vacuous mutation fails to change the validation result.

## 5. Acceptance criteria

Func Model functional signoff can be marked PASS only when all are true:

1. Task 0A provides an authoritative runner that writes atomic, current-HEAD/current-source-fingerprint, schema-validated evidence and rejects misleading PASS output, zero tests, skips, xfails, missing metrics, and stale source/command definitions.
2. Task 0B validates the legacy synthetic manifest/files as synthetic stress assets and independently validates the exact real Qwen2.5-3B GGUF hash, canonical shapes, and non-overlapping 256 MB DRAM layout.
3. F-FM-03 RED tests fail before the patch and pass after the implementation uses per-element abs-or-rel semantics with the defined NaN/Inf/boundary behavior.
4. Scaled/single-tile Qwen tests remain green and are accurately labeled as fast regressions.
5. The synthetic direct-MMIO gate executes exactly 17/17 manifest ops at declared dimensions and is labeled synthetic stress coverage.
6. The synthetic tiled scheduler gate executes every manifest MMUL, verifies tile-major conversion/tile count/output stitching, and is labeled synthetic stress coverage.
7. Task 4C1 proves selective real-GGUF loading and deterministic named projection inputs without changing existing float32 forward results.
8. Task 4C2 executes Q/K/V/O/gate/up/down through direct MMIO with the exact activation-scale restoration and bias ordering, independent quantized-oracle tolerances, and per-projection cosine thresholds.
9. Task 4C3 executes all seven real projections through tiled scheduling with explicit tile-major weight/scale conversion, full tile counts/remainders/stitching, direct-path agreement, and the same local/model thresholds.
10. Task 4C4 executes all connected dependencies using actual Func Model outputs and MMIO data types; every operator passes its same-input local oracle and every required projection/final output passes the separate float32-model cosine gate.
11. Full-shape validation includes deterministic corruption, wrong-checkpoint/shape rejection, invalid descriptor/address, FP16 tolerance, first/middle/last/remainder tile, saturation reporting, and anti-vacuous coverage.
12. The selected functional regression and full functional sweep pass with no unexplained functional failures.
13. W2.2 golden-vector verification remains `14/14 PASS`.
14. Documentation/checklist status is consistent, evidence-scoped, and only marks F-FM-13 PASS after Tasks 4A, 4B, and 4C1–4C4 all pass.
15. All four final OMO review cases rerun/validate their assigned surface and approve at the current HEAD.
16. RTL-golden-readiness and performance signoff are not overclaimed.

## 6. Deferred work

The following are explicitly not part of this plan:

1. SFU RTL batch `526/537` closure.
2. RTL testbench hardcoded path cleanup.
3. FM-SOC RTL VCS rerun.
4. Performance signoff.
5. Full decode-token or full multi-layer Qwen 3B signoff beyond blk.0.

These should be planned after Func Model functional signoff is stable.

## 7. Execution notes

- Use TDD for comparator semantics.
- Use minimal implementation changes for the comparator.
- Do not weaken tests to pass.
- Do not mark full-shape coverage PASS if any helper silently caps dimensions.
- Do not call the 2560/9728 fixed-seed manifest canonical Qwen2.5-3B or real-model data.
- Do not mark F-FM-13 PASS without the exact real GGUF hash and canonical 2048/11008 evidence from Tasks 4C1–4C4.
- Keep explicit signoff tests under `sim/signoff/` so the normal fast regression remains usable; Task 7 runs signoff nodes explicitly.
- Keep user/unrelated worktree changes intact.
- If synthetic or real-model assets are missing or cannot be validated, stop and report the exact missing artifact rather than substituting scaled or synthetic coverage.

## 8. Commit strategy

This plan does not authorize commits. The executor must leave implementation and evidence changes uncommitted unless the user explicitly requests git commits later.
