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
