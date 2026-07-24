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

### Fix: Ancestor-HEAD staleness check
- **Problem**: After committing evidence + code to `main`, HEAD advances. The original
  `validate_case()` rejected any HEAD mismatch, making `--all-functional` validation fail
  after every commit (evidence can never be generated at a HEAD that already contains it).
- **Fix**: When evidence HEAD differs from current HEAD, use `git merge-base --is-ancestor`
  to check whether the recorded commit is an ancestor of the current commit. If it is,
  allow the evidence (source_fingerprint and command_hash are still checked for staleness).
  Only fail when the recorded HEAD is NOT an ancestor (branch switched, history rewritten).
- **Exception**: `task-1-comparator-red` remains intentionally allowed to be stale.
