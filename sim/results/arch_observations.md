# Architectural Observations Log

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
