# Wave 2 Testcase Status — SFU + Vector Module-Level Performance

Last updated: 2026-07-06 18:15:00

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
| SFV-P08 | vector | add | 128 | ✅ PASS | ✅ PASS (5/6, Δ-1) | ✅ PASS | 0.0 | bit-exact |  |
| SFV-P09 | vector | mul | 128 | ✅ PASS | ✅ PASS (5/6, Δ-1) | ✅ PASS | 0.0 | bit-exact |  |
| SFV-P10 | vector | max | 128 | ✅ PASS | ✅ PASS (12/12, Δ0) | ✅ PASS | 0.0 | bit-exact | MAX uses reduce_tree, not ALU (formula corrected) |
| SFV-P11 | vector | sum | 128 | ✅ PASS | ✅ PASS (12/12, Δ0) | ✅ PASS | 0.0 | bit-exact |  |
| SFV-P12 | vector | conv | 128 | ✅ PASS | ✅ PASS (260/261, Δ-1) | ✅ PASS | 0.0 | atol=0.002, rtol=0.01 | CONV=2cyc/ele (formula corrected from 132→259/block) |
| SFV-P13 | vector | resid | 128 | ✅ PASS | ✅ PASS (5/6, Δ-1) | ✅ PASS | 0.0 | bit-exact |  |
| SFV-P14 | vector | mmio | 0 | ✅ PASS | ✅ PASS (BUSY≤2, IRQ ok) | ✅ PASS | N/A | N/A | MMIO timing verified; 6cyc CMD→IRQ |

**P0 Progress**: 14/14 PASS (SFU RTL perf: 7/7 ✅, Vector RTL perf: 7/7 ✅)

## P1: Parameter Sweep Results

Sweep completed: 108 configs (4 ops × 9 dims × 3 runs).
Formula check: 36/36 PASS (all measured cycles within Tier-1 tolerance).
Evidence: `build/evidence/sfv-P1-sweep-summary.json`

### Cycle Formula Summary

| Op | Formula | Tolerance | Max |Δ| | Notes |
|------|---------|-----------|----------|-------|
| softmax | 3N+33 | |Δ|≤5 | 2 | |
| layernorm | 3N+17 | |Δ|≤5 | 1 | |
| rmsnorm | 2N+21 | |Δ|≤5 | 1 | |
| rope | N+19 | |Δ|≤1 | 1 | Per-element threshold exceeded at all N due to +19 overhead; asymptotic slope = 1.0 |

### Per-Op / Per-Dim Cycle Table (mean of 3 runs)

| Op | Dim | Expected | Measured (mean) | Δ | Per-Element | Threshold |
|------|-----|----------|-----------------|---|-------------|-----------|
| softmax | 16 | 81 | 83 | +2 | 5.1875 | ≤8 |
| softmax | 32 | 129 | 131 | +2 | 4.0938 | ≤8 |
| softmax | 64 | 225 | 227 | +2 | 3.5469 | ≤8 |
| softmax | 128 | 417 | 419 | +2 | 3.2734 | ≤8 |
| softmax | 256 | 801 | 803 | +2 | 3.1367 | ≤8 |
| softmax | 512 | 1569 | 1571 | +2 | 3.0684 | ≤8 |
| softmax | 1024 | 3105 | 3107 | +2 | 3.0342 | ≤8 |
| softmax | 2048 | 6177 | 6179 | +2 | 3.0171 | ≤8 |
| softmax | 4096 | 12321 | 12323 | +2 | 3.0085 | ≤8 |
| layernorm | 16 | 65 | 66 | +1 | 4.1250 | ≤8 |
| layernorm | 32 | 113 | 114 | +1 | 3.5625 | ≤8 |
| layernorm | 64 | 209 | 210 | +1 | 3.2812 | ≤8 |
| layernorm | 128 | 401 | 402 | +1 | 3.1406 | ≤8 |
| layernorm | 256 | 785 | 786 | +1 | 3.0703 | ≤8 |
| layernorm | 512 | 1553 | 1554 | +1 | 3.0352 | ≤8 |
| layernorm | 1024 | 3089 | 3090 | +1 | 3.0176 | ≤8 |
| layernorm | 2048 | 6161 | 6162 | +1 | 3.0088 | ≤8 |
| layernorm | 4096 | 12305 | 12306 | +1 | 3.0044 | ≤8 |
| rmsnorm | 16 | 53 | 54 | +1 | 3.3750 | ≤8 |
| rmsnorm | 32 | 85 | 86 | +1 | 2.6875 | ≤8 |
| rmsnorm | 64 | 149 | 150 | +1 | 2.3438 | ≤8 |
| rmsnorm | 128 | 277 | 278 | +1 | 2.1719 | ≤8 |
| rmsnorm | 256 | 533 | 534 | +1 | 2.0859 | ≤8 |
| rmsnorm | 512 | 1045 | 1046 | +1 | 2.0430 | ≤8 |
| rmsnorm | 1024 | 2069 | 2070 | +1 | 2.0215 | ≤8 |
| rmsnorm | 2048 | 4117 | 4118 | +1 | 2.0107 | ≤8 |
| rmsnorm | 4096 | 8213 | 8214 | +1 | 2.0054 | ≤8 |
| rope | 16 | 35 | 34 | -1 | 2.1250 | ≤1 |
| rope | 32 | 51 | 50 | -1 | 1.5625 | ≤1 |
| rope | 64 | 83 | 82 | -1 | 1.2812 | ≤1 |
| rope | 128 | 147 | 146 | -1 | 1.1406 | ≤1 |
| rope | 256 | 275 | 274 | -1 | 1.0703 | ≤1 |
| rope | 512 | 531 | 530 | -1 | 1.0352 | ≤1 |
| rope | 1024 | 1043 | 1042 | -1 | 1.0176 | ≤1 |
| rope | 2048 | 2067 | 2066 | -1 | 1.0088 | ≤1 |
| rope | 4096 | 4115 | 4114 | -1 | 1.0044 | ≤1 |

**P1 Progress**: 36/36 formula PASS; per-element threshold violation for rope at all N due to fixed +19 cycle overhead (asymptotic per-element cycles → 1.0).

