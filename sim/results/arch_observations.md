# Architectural Observations Log

## 2026-07-02 22:07 — Iteration 131 — Stable, No Code Issues (27th Clean Run)

### Run Summary
- **Iteration**: 131 (stable since iter 104, 27 consecutive clean runs)
- **E2E**: 24.0 tok/s @ 64×64, target 25 — ❌ NOT MET (−4%)
- **Code issues**: 0 found, 0 fixed
- **All 6 health checks**: ✅ PASS
- **No changes**: identical to iter 129, converged steady state

### Status
Unchanged. M=1 decode at 64×64 remains DRAM-BW-bound (33.0/43.5 GB/s = 76%).
64×256 hits 25.0 tok/s @ 32mm² (+5mm²). All larger arrays hit target.

### Cron Constraint Staleness (ongoing — 24 iterations uncorrected)
Same 3 stale constraints flagged since iter 107:
1. "DRAM BW 不是瓶颈：需求 20.2 < 可用 43.5 GB/s" — 33.0 GB/s actual
2. "batch M≥2 → 31 tok/s" — 12 tok/s raw (inter-op parallelism not implemented)
3. "真正的瓶颈：M=1 decode 的 tiling overhead" — DRAM BW is primary, not tiling

---

## 2026-07-02 18:00 — Iteration 129 — Stable, No Code Issues (26th Clean Run)

### Run Summary
- **Iteration**: 129 (stable since iter 104, 26 consecutive clean runs)
- **E2E**: 24.0 tok/s @ 64×64, target 25 — ❌ NOT MET (−4%)
- **Code issues**: 0 found, 0 fixed
- **All 6 health checks**: ✅ PASS
- **No changes**: identical to iter 128, converged steady state

### Status
Unchanged. M=1 decode at 64×64 remains DRAM-BW-bound (33.0/43.5 GB/s = 76%).
64×256 hits 25.0 tok/s @ 32mm² (+5mm²). All larger arrays hit target.

### Cron Constraint Staleness (ongoing — 22 iterations uncorrected)
Same 3 stale constraints flagged since iter 107:
1. "DRAM BW 不是瓶颈：需求 20.2 < 可用 43.5 GB/s" — 33.0 GB/s actual
2. "batch M≥2 → 31 tok/s" — 12 tok/s raw (inter-op parallelism not implemented)
3. "真正的瓶颈：M=1 decode 的 tiling overhead" — DRAM BW is primary, not tiling

---

## 2026-07-02 14:05 — Iteration 127 — Stable, No Code Issues (24th Clean Run)

### Run Summary
- **Iteration**: 127 (stable since iter 104, 24 consecutive clean runs)
- **E2E**: 24.0 tok/s @ 64×64, target 25 — ❌ NOT MET (−4%)
- **Code issues**: 0 found, 0 fixed
- **All 6 health checks**: ✅ PASS
- **No changes**: identical to iter 126, converged steady state

### Status
Unchanged from iter 126. M=1 decode at 64×64 remains DRAM-BW-bound (33.0/43.5 GB/s = 76%).
64×256 hits 25.0 tok/s @ 32mm² (+5mm²). No new architecture issues.

### Cron Constraint Staleness (ongoing — uncorrected since iter 107)
All 3 stale constraints persist in the cron prompt:
1. "DRAM BW 不是瓶颈：需求 20.2 < 可用 43.5 GB/s" — **Wrong**: 33.0 GB/s actual
2. "batch M≥2 → 31 tok/s" — **Wrong**: 12 tok/s raw (31 requires unimplemented inter-op parallelism)
3. "真正的瓶颈：M=1 decode 的 tiling overhead" — **Misleading**: Primary bottleneck is DRAM BW

---

## 2026-07-02 12:05 — Iteration 126 — Stable, No Code Issues (23rd Clean Run)

### Run Summary
- **Iteration**: 126 (stable since iter 104, 23 consecutive clean runs)
- **E2E**: 24.0 tok/s @ 64×64, target 25 — ❌ NOT MET (−4%)
- **Code issues**: 0 found, 0 fixed
- **All 6 health checks**: ✅ PASS
- **No changes**: identical to iter 123, system converged to steady state

### Status
Unchanged from iter 123. The 4% gap on 64×64 M=1 decode is a DRAM BW ceiling (33.0/43.5 GB/s = 76%), not a code defect. Several alternative configs hit target at modest area cost (64×256 @ 32mm², +5mm²).

---

## 2026-07-02 06:01 — Iteration 123 — Stable, No Code Issues

### Run Summary
- **Iteration**: 123 (stable since iter 104, 20+ consecutive clean runs)
- **E2E**: 24.0 tok/s @ 64×64, target 25 — ❌ NOT MET (−4%)
- **Code issues**: 0 found, 0 fixed
- **All 6 health checks**: ✅ PASS
- **weight_preloaded**: zero residual `=True` in production code (all engines use `=False`)

### Stale Cron Constraints (uncorrected, same as iter 107)
The cron prompt has 3 systematically wrong claims that persist:
1. "DRAM BW 不是瓶颈" — Wrong. 33.0 GB/s demand = 76% of 43.5 GB/s. M=1 IS DRAM-BW-bound.
2. "batch M≥2 → 31 tok/s" — Wrong. Raw M=2 = 12 tok/s. 31 requires unimplemented inter-op parallelism.
3. "真正的瓶颈：M=1 decode 的 tiling overhead" — Misleading. Primary bottleneck is DRAM BW, not tiling.

### Bottleneck Status (Unchanged)
M=1 decode (64×64) remains DRAM-bandwidth-bound at 76% utilization.
- 64×256: 25.0 tok/s @ 32mm² — pragmatic target
- 128×256: 25.0 tok/s @ 42mm²
- 256×256: 25.0 tok/s @ 108mm² — expensive

---

## 2026-07-01 14:10 — Iteration 112 — Stable, No Code Issues

### Run Summary
- **Iteration**: 112 (stable since iter 104, 9+ consecutive clean runs)
- **E2E**: 24.0 tok/s @ 64×64, target 25 — ❌ NOT MET (−4%)
- **Code issues**: 0 found, 0 fixed
- **All 6 health checks**: ✅ PASS
- **Tooling reflexivity audit**: ✅ PASS (overnight_loop.py defended against all known patterns)

### Preflight Hits and Actions
Preflight warned about `stale-cron-user-constraints` [high] — confirmed in user prompt:
- Prompt claim "batch M≥2 → 31 tok/s" vs actual 12 tok/s (2.6× gap)
- Prompt claim "DRAM 20.2 GB/s" vs actual 33.0 GB/s (1.6× gap)
Both stale claims are already corrected in auto-generated morning_summary.md (fixed in iter 109).

### Bottleneck Status (Unchanged)
M=1 decode (64×64) remains DRAM-bandwidth-bound at 76% utilization. Several configs hit target:
- 64×256: 25 tok/s @ 32mm² — pragmatic target
- 128×256: 25 tok/s @ 42mm²
- 256×256: 25 tok/s @ 108mm² — expensive

### Architecture Q
Should we switch default config to 64×256 (hits 25 tok/s target at 32mm²) or stay at 64×64 (misses by 4% but smaller area)?

---

## 2026-06-29 10:06 — Stable Confirmed (iter 109)

Stable for 6 iterations (104-109). No code issues. All 6 health checks pass.

**Key fix this iteration**: `generate_summary()` in overnight_loop.py now includes stale-constraint table
at generation time. No more Post-Analysis annotations lost on regeneration. Also counts stable iterations
and displays gap percentage when target not met.

Same ground truth as iter 107:
- M=1 (64×64): 24.3 tok/s, -3% vs 25 target
- 64×256: 25.0 tok/s @ 32mm² (pragmatic M=1 target)
- Batch M=2 raw: 11.8 tok/s (inter-op not implemented)
- DRAM demand: 33.0 GB/s, 76% utilization

## 2026-06-29 06:00 — Stable State Confirmed (iter 107)

### Ground Truth (simulator v2 tiling-aware)

| Metric | Measured | User Constraint | Delta |
|--------|----------|-----------------|-------|
| M=1 tok/s (64×64) | 24.3 | 25 target | -3% |
| DRAM demand | 33.0 GB/s | 20.2 GB/s | +63% |
| Batch M=2 raw | 12 tok/s | 31 tok/s (projected) | -61% |
| BW utilization | 76% | ~46% (implied by 20.2/43.5) | +30pp |

### Stale User Constraints (corrections needed)

1. **"DRAM BW 不是瓶颈：需求 20.2 < 可用 43.5 GB/s"** — WRONG. DRAM demand is 33.0 GB/s, 76% of available 43.5 GB/s. M=1 decode is DRAM-BW-bound (all array sizes converge to ~25 tok/s), not compute-bound.

2. **"达标方案：batch M≥2 → 31 tok/s"** — MISLEADING. Raw batch M=2 throughput is 12 tok/s. The "31 tok/s" number comes from inter-op parallelism math (projected 47-76 tok/s), which is NOT implemented in the simulator. Without inter-op parallelism, batch decode is WORSE than M=1 (12 vs 24 tok/s).

3. **"真正的瓶颈：M=1 decode 的 tiling overhead"** — PARTIALLY CORRECT. M=1 is systolic-array-utilization bound, but this manifests as DRAM BW demand, not compute. Per-tile compute (192 cycles) << per-tile DMA (48 cycles). Aggregate BW demand across 840 tiles dominates.

### What's Actually Working

- **64×256, M=1**: 25.0 tok/s @ 32mm² — ✅ meets target, reasonable area
- **128×256, M=1**: 25.0 tok/s @ 42mm² — ✅ meets target
- **256×256, M=1**: 25.0 tok/s @ 108mm² — ✅ meets target (large area)
- All 4 health checks pass: weight_preloaded clean, dram_efficiency=0.85, v2 MXU, engines v2-compliant
- No code issues for 4+ iterations (104-107)

### Inter-op Parallelism Gap

Raw batch decode: 12 tok/s. With inter-op parallelism (2-core pipeline): projected 47 tok/s.
This 4× improvement requires: batch scheduler, kernel fusion, DMA double-buffering.
NOT implemented in current simulator. The "31 tok/s" user constraint is aspirational.

### Recommendation

- Remove or annotate the 3 stale user constraints in the cron prompt
- Target 64×256 (25 tok/s @ 32mm²) as the pragmatic M=1 decode config
- If batch decode is desired, implement inter-op parallelism in simulator before claiming batch performance numbers
