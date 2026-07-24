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
