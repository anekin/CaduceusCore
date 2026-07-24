## Wave 0: Signoff Evidence Runner — Design Decisions

### Architecture
- **Self-contained script**: Runner at `scripts/run_func_model_signoff.py` with argparse subcommands
  (`run` / `validate`). No external dependencies beyond stdlib.
- **Static case registry**: 23-case `CASE_REGISTRY` dict maps case IDs to `CaseDef` dataclasses
  with argv, evidence path, expected exit behavior, metric requirements, and fingerprint globs.
- **Env isolation**: `PYTHONPATH=sim` and `QWEN3B_GGUF` default are set by `build_env()`
  internally; the `run_fm_env.sh` wrapper provides the same for direct invocations.

### Key Design Decisions
- **JUnit XML parsing over text inference**: pytest cases always emit `--junitxml=<tmpfile>`
  and parse XML for collected/passed/failed counts. Never infer PASS from stdout text.
- **Atomic evidence writes**: Evidence is written to a temp file then `os.rename()`-d to the
  final path. Failed commands still produce FAIL evidence.
- **Source fingerprint**: SHA-256 over sorted (relative path + content SHA-256) of all in-scope
  files matching case globs, excluding evidence, caches, and generated artifacts.
- **Recursion guard**: The runner sets `_FM_SIGNOFF_RECURSE_GUARD=1` in subprocess env to
  prevent infinite spawning when the test suite itself invokes the runner.
- **Verdict metric ordering**: `evidence.verdict` is added as a placeholder before calling
  `_determine_verdict()`, then updated after, so the required-metrics check sees it.
- **task-1-comparator-red** is the sole expected-failure case with pattern matching on stderr.

### Verification
- 51/51 unit tests pass (10 categories: success, failure, expected-RED, zero-test, skip/xfail,
  missing-metric, stale-HEAD, stale-fingerprint, stale-command, atomic-write)
- `run --case task-0a-signoff-runner` exits 0 with PASS evidence
- `validate --case task-0a-signoff-runner` confirms evidence is current and valid

## Wave 1 T1 (RED): Comparator Tests — Lessons

### Bug exposed
- `GoldenSFU.compare_hw_vs_ref()` uses `np.all(abs_diff < tol_abs) or np.all(rel_diff < tol_rel)`
  — the `or` is at the global level: either all elements pass abs OR all pass rel.
  Correct behavior: each element individually must pass EITHER abs OR rel.
- Same bug exists in `scripts/verify_w2_2_fm_golden_vectors.py:225`.

### Test design
- `test_compare_mixed_abs_rel_pass` (the RED test): fp16 arrays where element 0 passes
  only abs tolerance and element 1 passes only rel tolerance. Asserts `within_tolerance=True`
  with message containing `mixed abs/rel must pass element-wise`.
- `test_compare_exact_boundary`: also exposes `<` vs `≤` boundary behavior bug.
- 5 tests total: 2 fail (RED), 3 pass (genuine out-of-tolerance/NAN/Inf cases).

### Runner integration
- Runner case `task-1-comparator-red` runs only the mixed test with `expected_exit=1` and
  `expected_failure_pattern="mixed.*abs.*rel"`. Pattern check is against stdout (pytest
  prints to stdout), not stderr.
- Tests must be top-level functions (not class methods) for pytest `::selector` syntax.

### Fix: Ancestor-HEAD staleness check
- **Problem**: After committing evidence + code to `main`, HEAD advances. The original
  `validate_case()` rejected any HEAD mismatch, making `--all-functional` validation fail
  after every commit (evidence can never be generated at a HEAD that already contains it).
- **Fix**: When evidence HEAD differs from current HEAD, use `git merge-base --is-ancestor`
  to check whether the recorded commit is an ancestor of the current commit. If it is,
  allow the evidence (source_fingerprint and command_hash are still checked for staleness).
  Only fail when the recorded HEAD is NOT an ancestor (branch switched, history rewritten).
- **Exception**: `task-1-comparator-red` remains intentionally allowed to be stale.

## Wave 1: Synthetic + Real-GGUF Provenance Preflight (T0B)

### Architecture
- **Three-file deliverable**: `sim/qwen_blk0_synthetic_vectors.py` (library), two signoff test files under
  `sim/signoff/`.
- **Library module** provides manifest loading, integrity verification (SHA-256 of all 46 hex files),
  DRAM window layout (17 ops × 1 MB non-overlapping windows in 256 MB DRAM), and overlap assertion.
- **Synthetic test** validates 17 ops, 46 files, SHA-256 integrity, synthetic dims (2560/9728 ≠ canonical
  2048/11008), FuncModel(dram_mb=256) initialization, and non-overlapping DRAM windows.
- **Real GGUF test** validates file provenance (exact SHA-256, 2.1 GB size), GGUF metadata (36 layers,
  hidden=2048, intermediate=11008, 16 heads, 2 KV heads, head_dim=128), and layer-0 tensor shapes
  (Q/O/K/V/gate/up/down) without dequantizing all 36 layers — reads only header metadata via gguf-py.

### Key Design Decisions
- **Standalone test functions** (not class methods) so the CaseDef argv `::function_name` selector works
  with pytest. Class-based tests require the full `::ClassName::function_name` node ID.
- **capsys.disabled() for SIGNOFF_METRIC emission**: pytest captures stdout by default, so plain
  `print()` output is swallowed. Using `capsys.disabled()` context manager bypasses capture and writes
  directly to the subprocess stdout, where the signoff runner's `parse_metrics_from_stdout()` picks it up.
- **Leading newline in metric lines** (`print(f"\nSIGNOFF_METRIC ...")`): pytest's progress dots (`.`) are
  written to the same output stream and can concatenate with metric lines. The leading newline ensures
  `SIGNOFF_METRIC` always starts at column 0 so the regex `^SIGNOFF_METRIC\s+(.+)$` matches.
- **Model metrics from real GGUF only**: Emitting the same metric keys from both synthetic and real tests
  would cause duplicate-key conflicts in the evidence validator. Only `test_qwen25_3b_real_blk0.py`
  emits the authoritative model.* metrics; the synthetic test focuses on manifest integrity.
- **No dequantization of all 36 layers**: The real GGUF test reads tensor shapes from GGUF header
  metadata via `gguf.GGUFReader`, which is O(tensors) and takes ~1 second. Full dequantization is
  deferred to T4C1-T4C4.

### Fix: GGUF field value extraction
- **Problem**: The gguf-py library stores fields as `ReaderField` objects with parts (memmap arrays)
  and data (indices). The raw field value is at `field.parts[field.data[-1]]`.
- **Fix**: Created `_get_field_value()` helper that extracts the last data element and converts
  numpy arrays to Python ints. Handles both uint32 scalars and the memmap-wrapped numpy representation.

### Verification
- Both tests pass individually: 2/2, ~13 s (12 s for SHA-256 of 2.1 GB file)
- `python3 scripts/run_func_model_signoff.py run --case task-0b-qwen3b-synthetic-and-real-preflight` exits 0
- Evidence at `.omo/evidence/task-0b-qwen3b-synthetic-and-real-preflight.txt` contains all required metrics
- `python3 scripts/run_func_model_signoff.py validate --case task-0b-...` passes
- Standalone pytest: `python3 -m pytest sim/signoff/ -q` passes (2/2)

## Wave 1 T3: Scaled/Single-Tile Test Reclassification

### Decision
- The three scaled/single-tile Qwen tests are reclassified as fast regressions, not signoff
  evidence. The runner's case registry already uses the new names; only the function
  definitions in `test_soc_fm.py` needed renaming.
- A docs consistency checker (`check_func_model_signoff_docs.py`) ensures no test
  containing `scaled` or `single_tile` in its name is ever described as `full-shape`
  in the signoff checklist or testcase list.

### Verification
- `task-3-scaled-qwen-regressions` runner case exits 0 with 3/3 collected and passed
- `task-6-signoff-doc-consistency` runner case exits 0 with 2/2 passed
- Checker handles the absence of `docs/func-model-signoff-checklist.md` gracefully
  (created in T6)

## Wave 2 T2 (GREEN): FP16/SFU Comparator Fix — Lessons

### Bug fixed
- **Comparator OR-logic**: `GoldenSFU.compare_hw_vs_ref()` used `np.all(abs_diff < tol) or np.all(rel_diff < tol)`
  (global OR: ALL elements must pass abs OR ALL must pass rel). Fixed to element-wise
  `np.all((abs_diff <= tol_abs) | (rel_diff <= tol_rel))`.
- **Same bug in verify script**: `scripts/verify_w2_2_fm_golden_vectors.py:225` had identical pattern.
- **Boundary behavior**: Changed `<` to `<=` for tolerance boundary semantics.

### NaN/Inf handling added
- NaNs in either array → immediate reject (before tolerances computed).
- Same-position same-sign infinities → accept.
- Opposite-sign infinities or finite-vs-infinite → reject.
- All three test cases (`test_compare_nan_mismatch`, `test_compare_inf_mismatch`,
  `test_compare_exact_boundary`) already existed as RED tests from T1 and now pass.

### Implementation details
- Three-phase gate: NaN gate → Inf gate → finite element-wise check.
- Metrics (`max_abs_err`, `mean_abs_err`, `max_rel_err`) unchanged.
- `verify_w2_2_fm_golden_vectors.py` fix is minimal: only the comparator expression changed.
  No NaN/Inf pre-checks added there (golden vectors are known-clean fp16 values).

### Source hashes
- Pre-fix comparator: `885d67fea97c9fe6a19ff7b3e54b9721420b1640d82531b85fed7653a9a7a2bf`
- Post-fix comparator: `771967d9f1090461bb774f68662bf056e101c34c768963690c32cb8c4296125a`
- Pre-fix verify line: `a403436b73200c2aa8eabddb2d16665fa9a4ab6a57392f06c6caca56545ab49f`
- Post-fix verify line: `b8f3b8a9aaadf1942fb9b7d657187b5f7ba4c1df9195d233f0d5bcbe35df73db`
- Commit: `7b90bc0e80d7c1380cc1383bf215695b297ad35d`

### Verification
- RED→GREEN: 2 FAIL → 5 PASS on `test_golden_sfu_compare.py`
- SFU/Vector regression: 110/110 pass
- Golden vectors: 14/14 PASS (`--skip-dry-run`)
- Signoff runner: `task-2-comparator-green` PASS, `task-2-w2-2-golden-vectors` PASS

## Wave 2 T4B: Synthetic Tiled-MMUL Scheduler Stress Gate

### Architecture
- **Single test function**: `test_qwen_blk0_synthetic_tiled_mmul_manifest_ops` in
  `sim/signoff/test_qwen_blk0_synthetic_stress.py`.
- **Inline mmio infrastructure**: `_build_mmio_handlers()` creates `mmio_write`/`mmio_read`/
  `wait_done` callbacks backed by `bytearray` DRAM and SRAM, with a `regfile` dict for
  register storage. DMA copies bytes between DRAM and SRAM on CMD write; MXU invokes
  `GoldenMXU.matmul_int32()` per tile with accumulate support (CTRL bit[2]).
- **Layout conversion**: `_row_major_to_tile_major()` unpacks row-major INT4 bytes into a
  full (K,N) matrix via `_unpack_int4_raw()`, extracts per-tile submatrices, repacks them
  with `_pack_int4_raw()`, and places at `(n_tile * num_blocks + k_block) * 8192` offsets.
  Partial edge tiles are zero-padded to the full 8192-byte slot.
- **Unity scales**: `_make_unity_scale_bytes()` generates tile-major FP32 scale data with
  all 1.0f values (0x3F800000), one per output column per tile, matching the
  `TILE_SCALE_BYTES = 512` stride.
- **Fixed DRAM layout**: Input at `0x80000000`, scales at `0x80010000`, weights at
  `0x80200000`, output at `0x81000000`. Reused per op since execution is sequential.
  Largest weight tile array: ~12.5 MB (gate/up/down: K=2560, N=9728 or vice versa).

### Key Design Decisions
- **GoldenMXU.matmul_int32 (no scales)**: Since scales are unity (1.0), per-block
  quantization is identity and the plain INT4×INT8→INT32 matmul produces the same
  result as the manifest golden (which is stored as INT32, not FP32).
- **DMA channel tracking**: `_last_dma_ch[0]` tracks whether CH0 or CH1 registers
  were last written to, since `tile_mmul` writes to the same CMD register for both
  channels. CH0 handles activation/weight/scale transfers; CH1 handles output transfers.
- **Synchronous execution**: CMD write triggers immediate DMA/MXU execution, then
  clears STATUS. `wait_done` is a no-op — STATUS is already 0.
- **Tile count tracking**: `_mxu_invocations[0]` increments on each MXU CMD write.
  Total tile count = 5922 across 9 MMUL ops (sum of ceil(K/128)*ceil(N/128)).
- **Separate CASE_ID**: `_T4B_CASE_ID = "task-4b-qwen3b-tiled-mmul"` uses a distinct
  `_t4b_emit_metric()` to avoid metric key collisions with T0B's preflight metrics.

### Tile Count Breakdown
| Op | Name | K | N | num_blocks | num_tiles | Tiles |
|---|---|---|---|---|---|---|
| 01 | Q_proj | 2560 | 4096 | 20 | 32 | 640 |
| 02 | K_proj | 2560 | 256 | 20 | 2 | 40 |
| 03 | V_proj | 2560 | 256 | 20 | 2 | 40 |
| 05 | attn_score | 128 | 2 | 1 | 1 | 1 |
| 07 | attn_weight | 2 | 128 | 1 | 1 | 1 |
| 08 | O_proj | 4096 | 2560 | 32 | 20 | 640 |
| 11 | gate | 2560 | 9728 | 20 | 76 | 1520 |
| 12 | up | 2560 | 9728 | 20 | 76 | 1520 |
| 15 | down | 9728 | 2560 | 76 | 20 | 1520 |
| **Total** | | | | | | **5922** |

### Edge Cases Exercised
- **Partial K tiles**: op07 (K=2 < 128) → 1 block of height 2
- **Partial N tiles**: op05 (N=2 < 128) → 1 tile of width 2
- **M > 1**: ops 05 and 07 (M=32) exercise multi-row activation
- **Large dimensions**: gate/up/down (N=9728) exercise 76 N-tiles
- **Remainder/accumulate**: Single-block ops (05, 07) test non-accumulate path
- **Multi-block accumulate**: ops 01/02/03/08/11/12/15 (multiple k_blocks per n_tile)
  test CTRL.ACCUMULATE path

### Verification
- `test_qwen_blk0_synthetic_tiled_mmul_manifest_ops` passes: 1/1, 17.5s
- Runner `run --case task-4b-qwen3b-tiled-mmul` exits 0 with PASS
- `validate --case task-4b-qwen3b-tiled-mmul` confirms evidence is current and valid
- Full `sim/signoff/test_qwen_blk0_synthetic_stress.py`: 2/2 pass (preflight + tiled MMUL)
- SIGNOFF_METRIC lines: tests.collected=1, tests.passed=1, tile_count=5922,
  data_provenance=synthetic
