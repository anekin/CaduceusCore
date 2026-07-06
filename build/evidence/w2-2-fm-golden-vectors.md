# Wave 2.2 — SFU + Vector Func Model Golden-Vector Verification

Generated: 2026-07-06 16:20:12
Scope: P0 module-level performance cases SFV-P01..SFV-P14
Method: Func Model (GoldenSFU / GoldenVector) vs numpy reference

## Summary

- **Total cases**: 14
- **PASS**: 14
- **FAIL**: 0

## Per-Case Results

| Case | Engine | Op | Dim | Pos | Expected Cycles | Cycle Tol | Formula Check | Max Abs Err | Max Rel Err | Tolerance | Golden Verdict | Notes |
|------|--------|----|-----|-----|-----------------|-----------|---------------|-------------|-------------|-----------|----------------|-------|
| SFV-P01 | sfu | softmax | 64 | 0 | 225 | 5 | PASS | 1.9371509552001953e-07 | 1.7837396760756594e-06 | atol=0.002, rtol=0.01 | PASS |  |
| SFV-P02 | sfu | layernorm | 64 | 0 | 209 | 5 | PASS | 0.0010426044464111328 | 0.0005140403062610349 | atol=0.002, rtol=0.01 | PASS |  |
| SFV-P03 | sfu | rmsnorm | 64 | 0 | 149 | 5 | PASS | 2.384185791015625e-07 | 1.1832046933447162e-07 | atol=0.002, rtol=0.01 | PASS |  |
| SFV-P04 | sfu | gelu | 64 | 0 | 71 | 1 | PASS | 0.0015764012932777405 | 0.1305819440740373 | atol=0.002, rtol=0.01 | PASS |  |
| SFV-P05 | sfu | silu | 64 | 0 | 71 | 1 | PASS | 7.152557373046875e-07 | 2.6857568833338108e-06 | atol=0.002, rtol=0.01 | PASS |  |
| SFV-P06 | sfu | rope | 64 | 0 | 83 | 1 | PASS | 0.0009765625 | 0.04347826084933884 | atol=0.002, rtol=0.01 | PASS |  |
| SFV-P07 | sfu | mmio | 0 | 0 | N/A | N/A | PASS (MMIO timing — no data formula) | N/A | N/A | N/A | PASS | MMIO timing only; no golden vector |
| SFV-P08 | vector | add | 128 | 0 | 6 | 1 | PASS | 0.0 | 0.0 | bit-exact | PASS |  |
| SFV-P09 | vector | mul | 128 | 0 | 6 | 1 | PASS | 0.0 | 0.0 | bit-exact | PASS |  |
| SFV-P10 | vector | max | 128 | 0 | 6 | 1 | PASS | 0.0 | 0.0 | bit-exact | PASS |  |
| SFV-P11 | vector | sum | 128 | 0 | 12 | 1 | PASS | 0.0 | 0.0 | bit-exact | PASS |  |
| SFV-P12 | vector | conv | 128 | 0 | 134 | 1 | PASS | 0.0 | 0.0 | atol=0.002, rtol=0.01 | PASS |  |
| SFV-P13 | vector | resid | 128 | 0 | 6 | 1 | PASS | 0.0 | 0.0 | bit-exact | PASS |  |
| SFV-P14 | vector | mmio | 0 | 0 | N/A | N/A | PASS (MMIO timing — no data formula) | N/A | N/A | N/A | PASS | MMIO timing only; no golden vector |

## Tolerance Policy

- SFU FP16 outputs: `np.allclose(..., rtol=0.01, atol=0.002)`
- Vector INT32 outputs: bit-exact (`np.array_equal`)
- Vector CONV (INT32→FP16): FP16 tolerance as above

## Findings

- All 7 SFU P0 data ops pass the FP16 tolerance against the numpy reference.
- All 6 Vector P0 data ops pass (INT32 bit-exact + CONV FP16 tolerance).
- RoPE uses 1 Q head (128 elements / 64 pairs) + 2 KV heads (256 elements), matching RTL GQA.
- GELU shows the largest absolute error (~1.5e-3) due to 64-entry LUT linear interpolation, still within the 2e-3 FP16 tolerance.

