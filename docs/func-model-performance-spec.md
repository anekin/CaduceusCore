# Func Model Performance Spec v1 — Normative Architecture Parameters

**Document status:** Frozen. All parameters use `basis=architecture_assumption`. No RTL-derived values.

**Spec ID:** `func_model_perf_spec_v1`
**Machine-readable:** `config/func_model_perf_spec_v1.json`
**Checker:** `scripts/check_func_model_perf_spec.py`

---

## 1. Overview

This document defines the normative performance parameters for the CaduceusCore Func Model across seven hard-gate domains and standalone software overhead. Every parameter is derived from architectural first principles — no RTL measurement, no calibration data, no empirical tuning.

The frozen hard-gate matrix was established in `.omo/plans/func-model-performance-infra-calibration-closure.md` lines 48-55. This document formalizes those parameters into a signoff-ready spec.

### Architectural Configuration (Baseline)

| Parameter | Value |
|-----------|-------|
| MXU array | 64×64 Block (weight-stationary) |
| Frequency | 1000 MHz (1 ns/cycle) |
| SFU width | 128 elements/cycle |
| Vector width | 128 elements/cycle |
| DMA channels | 2 (round-robin arbitration) |
| DMA burst size | 256 bytes |
| Memory | LPDDR5-6400, 51.2 GB/s |
| DRAM efficiency | 85% (effective BW ~43.52 bytes/cycle) |
| NoC topology | Crossbar (default), 4 ports |
| NoC flit width | 256 bits (32 bytes) |
| NoC hop latency | 3 cycles |
| SRAM L1 | 512 KB |
| KV cache SRAM | 256 KB |
| KV cache DRAM region | 96 MB |
| RISC-V cycle ratio | 5:1 (RISC-V @ 200MHz, MXU @ 1GHz) |

---

## 2. Parameter Domains

### 2.1 MXU (10 parameters)

The MXU domain models Block 64×64 GEMM operations. Parameters are defined for 10 (M,K,N) workload points spanning decode (M=1,4) and prefill (M=32,64,128), small and large matrices.

**Formula:** `ceil(K/array_H) * ceil(N/array_W)` tiles with double-buffer overlap. Per-tile compute = `array_H*(M+1)+array_W` (decode) or `pipeline_fill+drain` (prefill). Per-tile DMA = `(tile_weight_bytes + tile_act_bytes) / effective_bw`.

**Hard-gate rows:**

| ID | M | K | N | Cycles | Description |
|----|---|---|---|--------|-------------|
| mxu_1_64_64 | 1 | 64 | 64 | 241 | 1-tile decode |
| mxu_4_64_64 | 4 | 64 | 64 | 434 | Batch decode |
| mxu_64_64_64 | 64 | 64 | 64 | 465 | Full-array prefill |
| mxu_64_128_64 | 64 | 128 | 64 | 680 | 2 K-tiles |
| mxu_64_64_128 | 64 | 64 | 128 | 722 | 2 N-tiles |
| mxu_32_128_128 | 32 | 128 | 128 | 1158 | Partial M-tile |
| mxu_1_2048_2048 | 1 | 2048 | 2048 | 47104 | Large decode |
| mxu_128_2048_2048 | 128 | 2048 | 2048 | 706560 | Large prefill |
| mxu_1_2048_11008 | 1 | 2048 | 11008 | 122998 | FFN down-projection |
| mxu_128_2048_11008 | 128 | 2048 | 11008 | 1475976 | FFN down prefill |

### 2.2 SFU (24 parameters)

SFU domain models 6 operations (softmax, layernorm, rmsnorm, gelu, silu, rope) across 4 element counts (16, 128, 2048, 11008).

**Formula:** `pipeline_depth * ceil(elements / sfu_width)`. Pipeline depths: softmax=227, layernorm=210, rmsnorm=150, gelu=71, silu=72, rope=82.

### 2.3 Vector (30 parameters)

Vector domain models 6 operations (add, mul, max, sum, conv, resid) across 5 dimension sizes (1, 128, 256, 2048, 11008).

**Formula:** `op_latency * ceil(dim / vector_width)`. Latencies: add=5, mul=5, max=12 (reduce_tree), sum=12 (reduce_tree), conv=260 (type_convert), resid=5.

### 2.4 DMA (10 parameters)

DMA domain models isolated transfers at 5 byte sizes (1, 64, 4096, 65536, 1048576) across 2 channel configurations (1, 4).

**Formula:** `ceil(descriptor_overhead + bytes/bw_bytes_per_cycle + ceil(bytes/burst_size))`.

Note: Single isolated transfers use exactly 1 channel regardless of total channel count. Channel count affects throughput when multiple concurrent transfers are present.

### 2.5 DRAM (10 parameters)

DRAM domain models read/write access at 5 byte sizes.

**Formula:** `tRCD + tCAS + ceil(bytes/burst_size)*tBURST [+ tWR for write]`. tRCD=18, tCAS=14, tBURST=4, tWR=16, burst_size=256.

### 2.6 NoC (8 parameters)

NoC domain models crossbar and mesh topologies at 2 byte sizes (64, 4096) and 2 routes (0→1, 0→3).

**Formula:** `hop_latency*hop_count + ceil(bytes/flit_bytes) + arbitration + buffer_depth*hop_count`. Crossbar has fixed 1-hop. Mesh uses XY routing with Manhattan distance.

### 2.7 KV Cache (8 parameters)

KV Cache domain models token-position access (0, 1, 127, 511, 2047) and layer-switch cost (SRAM 64KB, 256KB, 512KB).

**Formula for access:** `num_kv_entries = token_pos`. SRAM hits use 2 cycles/entry. DRAM misses use 80 cycles/entry. SRAM window ≈ 512 tokens per layer (Qwen, kv_heads=2, head_dim=128).

**Formula for layer switch:** `sram_bytes / bw_bytes_per_cycle * (1 - overlap_ratio)` with overlap_ratio=0.7.

**No-op:** `token_pos=0` is declared `expected_noop=true` with exact-zero cycles.

### 2.8 SW Overhead (4 parameters)

Software overhead models 4 workload scenarios: Qwen2.5-3B block-0, 36-layer decode with DMA chain, 36-layer decode without DMA chain, and ResNet50.

**Formula:** `(fixed + per_layer_barrier*num_layers + per_layer_desc*num_layers + per_isa_inst*isa_instructions + [per_tile*tiles if no chain]) * cycle_ratio`.

All SW overhead outputs are marked assumption-only and do not enter canonical total.

---

## 3. Monotonicity Annotation Schema (for T18)

Every parameter carries `monotonicity_annotations` with:

- **sweep_dimensions:** Maps each sweep dimension to its expected monotonic direction and rationale.
  - Resource dimensions (bandwidth, array size, DMA channels): expected mono-decreasing (more resource → fewer cycles).
  - Workload dimensions (bytes, elements, tokens, M/K/N): expected mono-increasing (more work → more cycles).
- **expected_zero_derivatives:** List of sweep dimensions where zero derivative is expected (not anomalous). Each entry names the dimension and the condition.
- **saturation_annotations:** Documents where a dimension saturates (e.g., M saturates at array height, bandwidth saturates for compute-bound workloads).

**Classification contract (from plan line 73):**
- Zero derivatives classified as `expected_zero` (physically should not change, e.g., decode fixed-context) vs `actual_zero` (saturation point).
- Classification derived from spec annotations only; runner must cross-check computed zero-slope against annotations.
- Reverse or NaN/Inf derivatives fail.
- Bottleneck share ≥55% to classify.

---

## 4. Uncertainty Model

- **Latency/cycles:** `[0.7*base, 1.3*base]` (±30% band)
- **Throughput (TPS, FPS):** Inverted bands: `[base/1.3, base/0.7]`
- **Utilization/bandwidth breakdowns:** Diagnostic only, no uncertainty gate

---

## 5. Validation Contract

The checker (`scripts/check_func_model_perf_spec.py`) enforces:

1. **Schema:** All required fields present, all parameter IDs unique.
2. **Basis gate:** Only `architecture_assumption` allowed; `rtl_measurement` rejected.
3. **Units gate:** Only approved units (cycles, bytes, GB/s, etc.).
4. **NaN/Inf gate:** Any NaN or Inf in `estimated_cycles` rejected.
5. **Non-negative gate:** Negative cycles rejected (zero allowed only for `expected_noop`).
6. **Owner gate:** Non-empty owner field required.
7. **Content hash:** Deterministic SHA-256 over content (excludes timestamps).

---

## 6. Cross-References

- Frozen policies: `.omo/plans/func-model-performance-infra-calibration-closure.md` lines 41-79
- Machine-readable spec: `config/func_model_perf_spec_v1.json`
- Checker: `scripts/check_func_model_perf_spec.py`
- Tests: `sim/timing/tests/test_perf_spec_config.py`
- Negative fixtures: `config/tests/perf_spec_bad_units.json`, `config/tests/perf_spec_rtl_basis.json`
