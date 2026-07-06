# Wave 2 Testcase Status — SFU + Vector Module-Level Performance

Last updated: 2026-07-06 17:35:00

## P0: Baseline Cases

| Case | Engine | Op | Dim | Formula Status | RTL Perf | Golden Status | Max Error | Tolerance | Notes |
|------|--------|----|-----|----------------|----------|---------------|-----------|-----------|-------|
| SFV-P01 | sfu | softmax | 64 | ✅ PASS | ✅ PASS (227/225, Δ+2) | ✅ PASS | 1.9371509552001953e-07 | atol=0.002, rtol=0.01 |  |
| SFV-P02 | sfu | layernorm | 64 | ✅ PASS | ✅ PASS (210/209, Δ+1) | ✅ PASS | 0.0010426044464111328 | atol=0.002, rtol=0.01 |  |
| SFV-P03 | sfu | rmsnorm | 64 | ✅ PASS | ✅ PASS (150/149, Δ+1) | ✅ PASS | 2.384185791015625e-07 | atol=0.002, rtol=0.01 |  |
| SFV-P04 | sfu | gelu | 64 | ✅ PASS | ✅ PASS (71/71, Δ0) | ✅ PASS | 0.0015764012932777405 | atol=0.002, rtol=0.01 |  |
| SFV-P05 | sfu | silu | 64 | ✅ PASS | ✅ PASS (72/71, Δ+1) | ✅ PASS | 7.152557373046875e-07 | atol=0.002, rtol=0.01 |  |
| SFV-P06 | sfu | rope | 64 | ✅ PASS | ✅ PASS (82/83, Δ-1) | ✅ PASS | 0.0009765625 | atol=0.002, rtol=0.01 |  |
| SFV-P07 | sfu | mmio | 0 | ✅ PASS | ✅ PASS (BUSY≤2) | ✅ PASS | N/A | N/A | MMIO timing only; anti-vacuous checks all PASS |
| SFV-P08 | vector | add | 128 | ✅ PASS | ✅ PASS | ✅ PASS | 0.0 | bit-exact |  |
| SFV-P09 | vector | mul | 128 | ✅ PASS | ✅ PASS | ✅ PASS | 0.0 | bit-exact |  |
| SFV-P10 | vector | max | 128 | ✅ PASS | ✅ PASS | ✅ PASS | 0.0 | bit-exact |  |
| SFV-P11 | vector | sum | 128 | ✅ PASS | ✅ PASS | ✅ PASS | 0.0 | bit-exact |  |
| SFV-P12 | vector | conv | 128 | ✅ PASS | ✅ PASS | ✅ PASS | 0.0 | atol=0.002, rtol=0.01 |  |
| SFV-P13 | vector | resid | 128 | ✅ PASS | ✅ PASS | ✅ PASS | 0.0 | bit-exact |  |
| SFV-P14 | vector | mmio | 0 | ✅ PASS | ✅ PASS | ✅ PASS | N/A | N/A | MMIO timing only; no golden vector |

**P0 Progress**: 14/14 PASS (SFU RTL perf: 7/7 ✅)

