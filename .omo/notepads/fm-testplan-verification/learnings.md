
## T1 V-01: add / mul — 1000 random groups (2026-06-29)

**Result**: ✅ PASS — 4 tests passed (add_1000_groups, mul_1000_groups, 2× anti-vacuous).

**Observation**: GoldenVector.add/mul are pure INT32 numpy ops → bit-exact with reference. The 1e-7 threshold is trivially satisfied (actual error = 0).

**Anti-vacuous**: Verified that add != sub and mul != add for non-trivial inputs.
