# func-model-signoff-v3 Learnings

## 2026-07-25 Session start
- Plan: func-model-signoff-v3 approved, 12 tasks total (T0-T7 + F1-F4)
- Execution waves: T0 → T1-T4 → T5-T6 → T7 → F1-F4
- All work on `main` branch, use scripts for tool/env, all verification on sz0001, bug track in `docs/bugs/bugs-soc-func-model.md`

## 2026-07-25 T0 Complete — V3 Registry + Runner
- Commit: `5df01d9` — `feat(func-model-signoff-v3): add v3 SoC signoff runner cases`
- 11 v3 cases registered in CASE_REGISTRY, separated from v2 via `-v3-` ID marker
- Task-1 spike firmware split into 4 independent cases (1a-1d) to avoid `shell=True` and enable per-mode pass/fail
- `--v3` flag added to validate subcommand; `--all-functional` excludes v3 cases
- Evidence: `.omo/evidence/task-0-signoff-v3-runner.txt` — `verdict: pass` (20/20 tests)
- 20 unit tests in `sim/tests/test_func_model_signoff_v3.py` cover registry integrity, backward compatibility, CLI flag
- Verification: `validate --v3` discovers 11 cases (T0 passes, T1-T7 correctly report MISSING)

## 2026-07-25 T0 Fix — sys.executable normalization for sz0001
- Commit: `70c915b` — `fix(func-model-signoff-v3): normalize 'python3' to sys.executable in run_case`
- sz0001 has no `python3` in default PATH; uses EDA Python 3.10 at `/home/EDA/cadence/DDI22.34/INNOVUS221/tools.lnx86/voltus_components/xp_services/sgui/python3.10/bin/python3.10`
- Fix: in `run_case()`, before `subprocess.run`, replace `"python3"` in argv with `sys.executable`
- Registry argv strings keep `"python3"` (backward-compatible); substitution at spawn time only
- Test file: replaced hardcoded `"python3"` in subprocess calls with `sys.executable`; added recursion guard for subprocess test
- Evidence updated from sz0001: 23/23 passed, `verdict: pass`

## 2026-07-25 T0 Fix v2 — FM_PYTHON env var propagation for run_fm_env.sh
- The argv normalization in `run_case()` only rewrites argv, not the environment that `run_fm_env.sh` sees. Spike+firmware cases use `run_fm_env.sh` which does `exec "$@"`, so the wrapper itself needed a way to override the Python interpreter.
- Mechanism: `FM_PYTHON` env var. When set, `run_fm_env.sh` does exact-match substitution of any `"python3"` arg with `$FM_PYTHON`. When unset, behavior is identical to before (`python3`).
- Two-layer defense: (1) `build_env()` in `run_func_model_signoff.py` sets `FM_PYTHON=sys.executable` for all subprocesses; (2) `run_case()` still does argv normalization as a direct fallback.
- Critical gotcha: bash `${@/python3/...}` does SUBSTRING matching, not exact match. When argv contains full python paths (e.g. `python3.10/bin/python3.10`), substring match causes path duplication/mangling. Must use exact-match loop (`if [ "$_arg" = "python3" ]`) instead.
- Smoke test on sz0001: `FM_PYTHON=... bash run_fm_env.sh -- python3 -c "..."` works correctly.
- T1a Spike case fails on sz0001 due to missing `gguf` module (pre-existing dependency), not Python propagation.
- Evidence: T0 re-run passes, T0 validation passes with fresh fingerprint.
