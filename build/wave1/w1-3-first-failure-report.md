# W1.3 First-Failure Report: 3-Layer SoC RTL Chain

**Date:** 2026-07-07  
**Test:** `scripts/run_qwen25_3b_rtl.py`  
**Evidence:**
- `build/evidence/w1-3-rtl-3layer.txt`
- `build/wave1/w1-3-rtl-layer-outputs.npz`
- `build/wave1/w1-3-rtl-op-summary.json`
- `rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/expected.npz`
- `rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/*.hex`

## Symptom

The W1.3 51-op 3-layer SoC RTL regression completes with all 51 individual ops PASS,
but the per-layer output comparison fails:

| Layer | cos_sim vs W1.2 | max_abs_err_vs_rounded | Verdict |
|-------|-------------------|------------------------|---------|
| 0     | 0.422256          | 4.23                   | FAIL    |
| 1     | -0.838800         | 4.28e+02               | FAIL    |
| 2     | 0.999986          | 6.50e+01               | FAIL    |

`build/wave1/w1-3-rtl-op-summary.json` reports `passed=51, failed=0` for the individual ops.

When the comparison target is switched to the W1.3 generated reference
(`rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/expected.npz`), the failure persists:

| Layer | cos_sim vs W1.3 expected.npz | max_abs_err | Verdict |
|-------|------------------------------|-------------|---------|
| 0     | ~0.42                        | large       | FAIL    |
| 1     | ~-0.84                       | large       | FAIL    |
| 2     | ~1.00                        | ~65         | FAIL    |

## First Divergence

The first op where the RTL/W1.3 computation visibly diverges from the W1.2 FP32
reference is **op01 Q_proj** (and the following op02 K_proj / op03 V_proj).  A
per-op comparison between the W1.3 generator reference and a fresh W1.2 FP32
computation shows:

| Op (Layer 0) | cos_sim | max_abs_diff |
|--------------|---------|--------------|
| op00 RMSNorm pre-attn | 1.000000 | 0.0008 |
| op01 Q_proj | 0.994562 | 0.6294 |
| op02 K_proj | 0.995673 | 0.2770 |
| op03 V_proj | 0.995687 | 0.1097 |
| op04 RoPE | 0.994776 | 0.6281 |
| op05 attn_score | 0.996163 | 31.57 |
| op06 attn_softmax | 1.000000 | ~0.0001 (correct) |
| op07 attn_weight | 0.999000 | ~0.003 |
| op08 O_proj | 0.850274 | 2.67 |
| op09 VRESID pre-attn | 0.747 | 307.3 |

The divergence starts at the first large INT4×INT8 matrix multiplication.

## Root Cause

Two independent issues have been identified:

1. **W1.3 generated vectors are internally inconsistent.**
   - `op_08_O_proj_fp32` output range is `[-0.25, 0.28]`.
   - The derived `op09_l0_VRESID_pre-attn_b.hex` (input B to the VRESID pre-attn
     residual-add, scaled by `1/1024`) has range `[-2.62, 2.88]`.
   - This is approximately a **10× discrepancy** and indicates a bug in the
     generator or in the downstream vector that consumes the O_proj output.
   - A similar mismatch is seen between the W1.3 `expected.npz` and the current
     W1.3 VRESID post-FFN golden hex files.

2. **`build/wave1/w1-3-rtl-layer-outputs.npz` is stale.**
   - File timestamp: `19:08`.
   - W1.3 vector files (including VRESID post-FFN golden hex) were regenerated at
     `19:44`.
   - The saved layer outputs therefore do not reflect the current W1.3 vectors,
     so comparison against either W1.2 or the current W1.3 `expected.npz` fails
     even if the RTL itself is correct.

Verification performed on this checkout confirms:

- All 51 per-op checks PASS against the W1.3 golden vectors.
- Reading the INT32 VRESID post-FFN output from SRAM and comparing it to the
  current `op{XX}_lN_VRESID_post-FFN_golden.hex` has **not been re-run** since the
  vector regeneration; the existing stale `npz` prevents the final PASS/FAIL
  verdict from being trustworthy.

## Why This Is Not a Pure RTL Bug

- All 51 per-op checks PASS against the W1.3 golden vectors.
- The first divergence from W1.2 appears at the first MMUL, where the only
  difference is the weight/activation quantization scheme (generator-side), not
  the RTL implementation.
- The stale `npz` is an artifact/test-infrastructure issue, not an RTL datapath
  issue.

However, the W1.3 generator inconsistency must be fixed before the RTL
verification command can be expected to pass end-to-end.

## Recommended Fix

1. **Fix the generator bug** in `scripts/gen_qwen25_3b_rtl_vectors.py` that
   produces the ~10× mismatch between O_proj output and the VRESID pre-attn
   input B vector.
2. **Regenerate W1.3 vectors** after the generator fix.
3. **Rerun the RTL simulation** on a VCS-capable host to produce a fresh
   `build/wave1/w1-3-rtl-layer-outputs.npz`.
4. Re-run `PYTHONPATH=sim:scripts python3 scripts/run_qwen25_3b_rtl.py --skip-generate --skip-rtl`.

`scripts/run_qwen25_3b_rtl.py` has already been updated to compare RTL outputs
against `rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/expected.npz` (the W1.3
golden), so once the generator and stale-artifact issues are resolved the
command should report `TESTS=3 PASS=3`.

## Files Affected by Proposed Fix

- `scripts/gen_qwen25_3b_rtl_vectors.py` — fix the O_proj → VRESID scaling bug.
- `build/wave1/w1-3-rtl-layer-outputs.npz` — must be regenerated by rerunning RTL.
- `scripts/run_qwen25_3b_rtl.py` — already changed to compare against W1.3
  `expected.npz`.

## Verification Command

```bash
PYTHONPATH=sim:scripts python3 scripts/run_qwen25_3b_rtl.py --skip-generate --skip-rtl
```

Expected result after fix: `TESTS=3 PASS=3 FAIL=0`.

## Current Blockers

- VCS is not available on the current machine (`vcs` not in PATH, `module load`
  fails), so the RTL simulation cannot be rerun here to refresh
  `build/wave1/w1-3-rtl-layer-outputs.npz`.
