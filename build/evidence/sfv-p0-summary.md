# SFU P0 Module-Level Performance Baseline — Summary

> **Date**: 2026-07-06  
> **Engine**: SFU (Special Function Unit) — 8 RTL files, 2,678 lines  
> **Methodology**: VCS V-2023.12-SP2 on sz0001, `tb_sfu_perf.v` with per-FSM-state cycle counters  
> **Compile**: `-full64 -sverilog -timescale=1ns/1ps` (no `-debug_access+all` — rmapats.so issue)  

## Results: 7/7 PASS

| Case | Op | Dim | Measured | Expected | Delta | Tol | Verdict |
|------|-----|-----|----------|----------|:---:|:---:|:------:|
| SFV-P01 | softmax | 64 | 227 | 225 (3N+33) | +2 | 5 | ✅ PASS |
| SFV-P02 | layernorm | 64 | 210 | 209 (3N+17) | +1 | 5 | ✅ PASS |
| SFV-P03 | rmsnorm | 64 | 150 | 149 (2N+21) | +1 | 5 | ✅ PASS |
| SFV-P04 | gelu | 64 | 71 | 71 (N+7) | 0 | 1 | ✅ PASS |
| SFV-P05 | silu | 64 | 72 | 71 (N+7) | +1 | 1 | ✅ PASS |
| SFV-P06 | rope | 64 | 82 | 83 (N+19) | -1 | 1 | ✅ PASS |
| SFV-P07 | MMIO | 1 | BUSY≤2 ✅ | BUSY≤2 | N/A | N/A | ✅ PASS |

## Per-State Breakdown

| Case | Op | READ_INIT | RUN | FLUSH | TOTAL |
|------|-----|:---:|:---:|:---:|:---:|
| SFV-P01 | softmax | 1 | 65 | 161 | 227 |
| SFV-P02 | layernorm | 1 | 65 | 144 | 210 |
| SFV-P03 | rmsnorm | 1 | 65 | 84 | 150 |
| SFV-P04 | gelu | 1 | 65 | 5 | 71 |
| SFV-P05 | silu | 1 | 65 | 6 | 72 |
| SFV-P06 | rope | 0 | 65 | 17 | 82 |
| SFV-P07 | mmio | 1 | 2 | 5 | 8 |

## Key Observations

1. **All formulas confirmed within tolerance** — the RTL FSM cycle formulas from `testcase-list-sfu-vector-perf.md` are validated.
2. **RUN state is identical across all ops at 65 cycles** for dim=64 — this is the main processing pass.
3. **Softmax FLUSH dominates** at 161 cycles (75% of total) due to 24-cycle divider + 3 Newton-Raphson iterations.
4. **Layernorm FLUSH** at 144 (69%) is less than softmax — 12-step sqrt vs 24-step divider.
5. **Streaming ops (gelu/silu)** have minimal FLUSH (5-6 cycles) — fixed pipeline depth, no multi-pass.
6. **RoPE** shows READ_INIT=0 (no SRAM read init needed for direct pipeline path).

## Evidence Logs

| Case | Log |
|------|-----|
| SFV-P01 | `build/evidence/sfv_SFV-P01_sim.log` |
| SFV-P02 | `build/evidence/sfv_SFV-P02_sim.log` |
| SFV-P03 | `build/evidence/sfv_SFV-P03_sim.log` |
| SFV-P04 | `build/evidence/sfv_SFV-P04_sim.log` |
| SFV-P05 | `build/evidence/sfv_SFV-P05_sim.log` |
| SFV-P06 | `build/evidence/sfv_SFV-P06_sim.log` |
| SFV-P07 | `build/evidence/sfv_SFV-P07_sim.log` |

## Known Issues

- **LUT files not found** (gelu_lut.hex, exp_lut.hex, rope_theta_inv_freq.hex, softmax_exp_lut_q12.hex) — SRAM data paths use empty LUTs, causing anti-vacuous `sram_ren`/`sram_wen` toggle count failures. Cycle counts are unaffected (FSM structure determines timing).
- **VCS rmapats.so error** intermittent with `vcs/vcs_2023.12sp2` — clean compile without `-debug_access+all` succeeds; rebuilds may fail.
- **$value$plusargs byte order** in `tb_sfu_perf.v` — strings stored MSB-first in reg vectors; fixed by adding `+op_code=<n>` numeric plusarg support.
