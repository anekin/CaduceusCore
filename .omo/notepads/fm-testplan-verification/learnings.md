
## T1 V-01: add / mul — 1000 random groups (2026-06-29)

**Result**: ✅ PASS — 4 tests passed (add_1000_groups, mul_1000_groups, 2× anti-vacuous).

**Observation**: GoldenVector.add/mul are pure INT32 numpy ops → bit-exact with reference. The 1e-7 threshold is trivially satisfied (actual error = 0).

**Anti-vacuous**: Verified that add != sub and mul != add for non-trivial inputs.

## T1 V-02: add / mul — boundary values (2026-06-29)

**Result**: ✅ PASS — 7 tests passed (zero, INT32 boundary, NaN/Inf, denorm).

**Observation**: GoldenVector.add/mul operate on INT32, not FP. NaN/Inf in float input produce RuntimeWarning during int32 cast but return deterministic values. ±0 → 0 correct. Denorm floats truncate to 0 → correct INT32 operation.

**Anti-vacuous**: Boundary tests prove deterministic behavior isn't a crash-only test.
