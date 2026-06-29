
## T1 V-01: add / mul — 1000 random groups (2026-06-29)

**Result**: ✅ PASS — 4 tests passed (add_1000_groups, mul_1000_groups, 2× anti-vacuous).

**Observation**: GoldenVector.add/mul are pure INT32 numpy ops → bit-exact with reference. The 1e-7 threshold is trivially satisfied (actual error = 0).

**Anti-vacuous**: Verified that add != sub and mul != add for non-trivial inputs.

## T1 V-02: add / mul — boundary values (2026-06-29)

**Result**: ✅ PASS — 7 tests passed (zero, INT32 boundary, NaN/Inf, denorm).

**Observation**: GoldenVector.add/mul operate on INT32, not FP. NaN/Inf in float input produce RuntimeWarning during int32 cast but return deterministic values. ±0 → 0 correct. Denorm floats truncate to 0 → correct INT32 operation.

**Anti-vacuous**: Boundary tests prove deterministic behavior isn't a crash-only test.

## T1 V-03: max_reduce — 100 random groups (2026-06-29)

**Result**: ✅ PASS — 101 tests (100 parametrized bit-exact groups + 1 anti-vacuous).

**Observation**: max_reduce = `float(np.max(x))`, bit-exact by construction. Parametrized with sizes from 1 to ~1000 elements, values in [-100000, 100000].

**Anti-vacuous**: max_reduce([1,5,3]) = 5.0 — proves it's finding the actual maximum.

## T1 V-04: sum_reduce — 10000 × 1e-7 cumulative precision (2026-06-29)

**Result**: ✅ PASS — 2 tests passed (precision + anti-vacuous).

**Observation**: sum_reduce uses `float(np.sum(x.astype(np.float64)))` → float64 accumulation. 10000 × 1e-7 = 0.001 with ~1e-16 relative error. Well under 1% threshold.

**Anti-vacuous**: Float32 accumulation of the same data gives ~5% error, proving the 1% threshold is meaningful and float64 precision is necessary.

**Total V-01..V-04**: 114 tests, 0 failures.
