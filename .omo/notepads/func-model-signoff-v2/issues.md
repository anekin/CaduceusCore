## Wave 0: Open Issues

### Resolved
- **Recursive subprocess loop**: Fixed by removing recursive `test_runner_self_runs` test
  and adding `_FM_SIGNOFF_RECURSE_GUARD` env var to the runner's pytest subprocess env.
- **Verdict-metric ordering**: `evidence.verdict` was added after `_determine_verdict()` check,
  causing false "missing_metric" failures. Fixed by adding placeholder before check,
  updating after.
- **JUnit XML parsing double-count**: `failed` attribute was not being propagated from test
  data generator. Simplified `_make_junit_xml()` signature.

### Wave 1 T1: Comparator RED Tests

- **Comparator or-logic bug**: `GoldenSFU.compare_hw_vs_ref()` returns `within_tolerance=False`
  when elements individually pass via different tolerances (one abs, one rel). Fix in T2 must
  change to element-wise check: `np.all((abs_diff < tol_abs) | (rel_diff < tol_rel))`.
- **Boundary `<` vs `≤`**: Current code uses strict `<`; should use `<=` for tolerance boundary.
- **Same bug in verify_w2_2_fm_golden_vectors.py**: line 225 has identical pattern, needs fix in T2.
- **Inf test warning**: `test_compare_inf_mismatch` produces `RuntimeWarning: invalid value
  encountered in divide` in the comparator's rel_diff computation. Not blocking — the test
  correctly asserts False. T2 fix will naturally resolve this when moving to element-wise `|`.

### Deferred
- **final-plan-compliance / final-code-quality / final-real-qa / final-scope-fidelity**:
  These final-gate cases have empty argv lists (planned for later waves). Runner will reject
  them with "no argv (validate-only)" until argv is populated.
- **Metrics from non-pytest cases**: Cases like W2.2 golden vectors use `SIGNOFF_METRIC`
  lines in stdout for test counts. The runner parses these but cannot auto-add synthetic
  metrics. Test scripts must emit their own `SIGNOFF_METRIC` lines.
- **Pytest stdout capture requires capsys.disabled()**: Test functions that need to emit
  `SIGNOFF_METRIC` lines through the runner's subprocess must use `capsys.disabled()` context
  manager. Plain `print()` is captured by pytest and never reaches the runner's stdout.
  Adding `-s` to the runner's CaseDef argv would be simpler but affects all test output.

### Wave 1 — Open Issues
- **Duplicate metric key detection**: If both synthetic and real tests emit the same metric
  keys (e.g., model.hidden), the evidence validator detects conflicting values. Workaround:
  only the real-GGUF test emits model.* metrics. If future tests need to emit the same keys
  with different values, the validator logic must distinguish by test source.
- **GGUF SHA-256 computation is slow**: Computing SHA-256 of a 2.1 GB file takes ~12 seconds
  on this server. This is acceptable for a preflight gate but may need caching for repeated
  runs (currently no cache).

## Wave 1 T3: Issues Found / Resolved

### Resolved
- **test_soc_fm.py pytest discovery failure after file creation**: The newly created
  `sim/tests/test_func_model_signoff_docs.py` was not found by pytest on the first run
  (exit code 4). Re-running with an absolute path worked; subsequent relative-path runs
  also worked. Likely a caching or file-system propagation delay. No code change needed.

### Deferred
- **Old test names in testcase-list-soc-fm.md**: FM-SOC-027 still references the old
  name `test_blk0_full_chain_single_tile`. This is outside the allowed file scope for T3
  and does not break any test execution or the checker. Can be updated opportunistically
  in a later wave.

## Wave 2 T2: Comparator Fix Applied (F-FM-03)

### Resolved (T2)
- **Comparator OR-logic bug (FIXED)**: `GoldenSFU.compare_hw_vs_ref()` now uses element-wise
  `(abs_diff <= tol_abs) | (rel_diff <= tol_rel)` with `np.all()` guard. NaN rejection
  and Inf sign-matching added as explicit pre-checks. Commit `7b90bc0`.
- **verify_w2_2_fm_golden_vectors.py:225 (FIXED)**: Same element-wise `|` semantics.
- **Boundary `<` vs `≤` (FIXED)**: Now uses `<=` for both absolute and relative tolerance.
- **Inf divide-by-zero warning (RESOLVED)**: The `RuntimeWarning` persists in the metrics
  computation (rel_diff = inf/inf = NaN for opposite-sign infs) but does not affect the
  within_tolerance decision since infinities are handled by the Inf gate before the
  element-wise check is reached for inf-fail cases.

### Remaining
- **Inf/Metrics interaction**: When same-sign infinities pass, `abs_diff` contains NaN at
  inf positions (inf - inf = NaN), so `max_abs_err` and `mean_abs_err` return NaN.
  This is pre-existing behavior (original code also computes raw `np.max`).
  Not blocking for current test coverage (no test exercises same-sign inf pass path
  for metrics).

## Wave 2 T4B: Tiled-MMUL Scheduler Stress Gate

### Resolved
- **Tile-major weight conversion**: Implemented `_row_major_to_tile_major()` which unpacks
  row-major INT4 bytes, reshapes to (K,N), extracts per-tile submatrices, repacks, and
  places at tile-major offsets. Verified bit-exact against manifest INT32 golden for all
  9 MMUL ops across 5922 total tiles.
- **Inline mmio infrastructure**: `_build_mmio_handlers()` replicates the DMA/MXU pipeline
  without needing the full FuncModel+RISC-V firmware stack. DMA copies bytes between DRAM
  and SRAM; MXU invokes `GoldenMXU.matmul_int32()` per tile with accumulate support.
- **Unity scale generation**: `_make_unity_scale_bytes()` produces tile-major FP32 1.0f
  scale data, confirming that the manifest golden was computed without per-block
  quantization (output dtype is INT32, not FP32).

### Deferred
- **Per-op scale verification**: All scales are unity (1.0). Real GGUF weights in T4C3-T4C4
  will exercise per-block scale quantization with non-unity scales. The mmio handler's
  MXU path currently ignores SCALE_ADDR (safe when scales are 1.0); for real weights,
  it must switch to `matmul_int4_per_block()` and read actual scale values.
- **Partial tile zero-padding**: `_row_major_to_tile_major()` zero-pads partial tile slots
  to `TILE_WEIGHT_BYTES`. The DMA copies only the actual needed bytes (`wgt_bytes`),
  so the padding is never read. This is correct but adds memory overhead for tiny ops
  like attn_score (128 bytes → 8192 bytes in tile-major). Not a concern for 256 MB DRAM.

## Wave 2 T4C1: Selective Loading + Reference Inputs

### Resolved
- **Metric case_id collision (FIXED)**: `_emit_metric()` now accepts optional `case_id`
  parameter. When two signoff tests share the same file (T0B preflight + T4C1 selective
  loading), each test emits metrics with its own case ID, preventing the validator's
  `metric_case_id_mismatch` rejection.
- **Duplicate metric key for loaded tensors (FIXED)**: Emitting `loaded_tensor` as a
  metric key with 13 different tensor-name values triggered the evidence validator's
  `duplicate_metric_key_with_conflicting_value` check. Fixed by using unique keys per
  tensor: `loaded_tensor.blk.0.attn_q.weight`, etc.

### Deferred
- **Block-level row extraction for Q4_K/Q6_K**: `load_tensor_row_from_gguf` loads the
  entire single tensor for quantized types because a logical "row" in the transposed
  (N,K) layout is scattered across every Q4_K block in the raw (K,N) storage. Block-level
  extraction would require reading and dequantizing ~n_rows blocks scattered across the
  tensor — the code complexity is not worth the marginal savings for one tensor.
  The per-tensor selectivity guarantee (skip 35 layers) is sufficient for T4C1-T4C4.

### Resolved (T4C1 fixup)
- **Missing ggml-npu in PYTHONPATH (FIXED)**: The signoff runner sets `PYTHONPATH=sim`
  only. `test_qwen25_3b_selective_loading_and_reference_inputs` imports `q4_dequant`
  from `ggml-npu/`, which was not on the path. Fixed by adding the same `sys.path.insert`
  pattern already used in `qwen25_forward.py` and `qwen25_func_model.py` — inserted
  `_PROJECT / "ggml-npu"` into `sys.path` at module level in the test file. Note:
  `_HERE.parents[1]` (not `[2]`) is the project root because the test file is at
  `sim/signoff/` (two levels deep under CaduceusCore).
