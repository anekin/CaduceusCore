# Func Model Signoff Checklist — v2

> **Date**: 2026-07-24
> **Scope**: Func Model functional signoff only. RTL-golden-readiness for full SoC RTL is deferred.
> **Performance signoff**: FAIL/PARTIAL — tracked separately. Do NOT claim performance pass.

This document reconciles all signoff evidence gathered across tasks T0B–T5 and codifies
the evidence chain, provenance rules, and classification boundaries that the semantic
checker (`scripts/check_func_model_signoff_docs.py`) enforces.

---

## Signoff Status Summary

| Signoff ID | Description | Status | Evidence |
|---|---|---|---|
| **F-FM-01** | GoldenMXU INT4 matmul bit-exact vs INT64 reference | ✅ PASS | T0–T2, 42/42 scenarios |
| **F-FM-02** | GoldenSFU 7 ops vs numpy float32 reference | ✅ PASS | T0–T2, 133/133 scenarios |
| **F-FM-03** | FP16/SFU element-wise tolerance comparator (RED→GREEN) | ✅ PASS | T1→T2; fixed in `sim/golden_executor.py` + `scripts/verify_w2_2_fm_golden_vectors.py` |
| **F-FM-04** | GoldenVector 6 ops INT32 bit-exact | ✅ PASS | T0–T2, 251/251 scenarios |
| **F-FM-05** | Synthetic manifest integrity (SHA-256, dims, DRAM layout) | ✅ PASS | T0B synthetic preflight, 46 hex files, dims 2560/9728 |
| **F-FM-06** | Real GGUF provenance (SHA-256, metadata, layer-0 shapes) | ✅ PASS | T0B real GGUF; `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d` |
| **F-FM-07** | Synthetic tiled-MMUL scheduler stress (5922 tiles, unity scales) | ✅ PASS | T4B, 9 MMUL ops, bit-exact vs manifest golden |
| **F-FM-08** | Synthetic direct-MMIO 17-op stress | ✅ PASS | T4A, all 17 ops (MXU×9, SFU×5, VECTOR×3) |
| **F-FM-09** | Selective real-GGUF loading (layer-0 only, 13 tensors) | ✅ PASS | T4C1, no layer-1+ leakage |
| **F-FM-10** | Real-GGUF direct-MMIO projection (7 projections, independent oracle) | ✅ PASS | T4C2, all cosines ≥0.97 |
| **F-FM-11** | Real-GGUF tiled-scheduler projection (4704 tiles, oracle agreement) | ✅ PASS | T4C3, bit-exact direct-MMIO agreement |
| **F-FM-12** | Real-GGUF connected blk.0 dual-oracle hard gate (21 boundaries) | ✅ PASS | T4C4 v3, all cosines ≥0.976, final cosine ≥0.988 |
| **F-FM-13** | Real Qwen2.5-3B blk.0 full-shape functional signoff | ✅ PASS | Derived: T4A + T4B + T4C1 + T4C2 + T4C3 + T4C4 all PASS |
| **F-FM-14** | Golden vectors W2.2 verification (14/14 scenarios) | ✅ PASS | T2, post-comparator-fix re-verification |
| **F-FM-15** | Comparator RED→GREEN closed (2 failing tests → 5 passing) | ✅ PASS | T1→T2, element-wise `|` semantics applied |
| **F-FM-16** | Qwen 3B robustness (synthetic corruption, descriptor, boundary) | ✅ PASS | T5, 4/4 tests |
| **F-FM-17** | Scaled/single-tile fast regressions (not signoff evidence) | ✅ PASS | T3, reclassified as regressions, not signoff |

---

## Provenance Rules

### Real-Model Evidence

- **Model**: Qwen2.5-3B-Instruct Q4_K_M GGUF
- **SHA-256**: `626b4a6678b86442240e33df819e00132d3ba7dddfe1cdc4fbb18e0a9615c62d`
- **Size**: 2,104,932,768 bytes
- **Canonical dimensions**: `hidden=2048`, `intermediate=11008`
- **Scope**: blk.0 only (single transformer layer)
- **Activation**: Token 9707 (`"Hello"`), position=0
- **Evidence files**: `test_qwen25_3b_real_blk0.py` (T0B, T4C1, T4C2, T4C3, T4C4)

### Synthetic-Manifest Evidence

- **Source**: `rtl/test_vectors/qwen_blk0/blk0_manifest.json`
- **Dimensions**: `hidden=2560`, `intermediate=9728` — **synthetic, NOT canonical**
- **Label**: Synthetic manifest data is explicitly synthetic and never presented as real-model evidence.
- **Evidence files**: `test_qwen_blk0_synthetic_stress.py` (T0B synthetic preflight, T4B, T4A, T5)

### Scaled / Single-Tile Tests

The following tests use scaled dimensions or single-tile configurations and are classified as
**fast regressions**, NOT signoff evidence:

- `test_blk0_scaled_single_tile_manifest_replay`
- `test_28block_scaled_chain`
- `test_e2e_host_pcie_doorbell_firmware_scaled_blk0`

These tests provide quick sanity checks on the toolchain but do not constitute signoff.
The authoritative signoff chain is T4A→T4B→T4C1→T4C2→T4C3→T4C4, which uses
the canonical 2048/11008 real-GGUF dimensions.

---

## Key Resolved Issues

### F-FM-03: Comparator OR-Logic Bug (RED→GREEN)

**Before** (T1 RED): `GoldenSFU.compare_hw_vs_ref()` used global OR:
```python
np.all(abs_diff < tol) or np.all(rel_diff < tol)
```
ALL elements must pass abs OR ALL must pass rel. Elements individually passing different
tolerances would be incorrectly rejected.

**After** (T2 GREEN): Element-wise check:
```python
np.all((abs_diff <= tol_abs) | (rel_diff <= tol_rel))
```
Each element passes if it satisfies EITHER abs OR rel tolerance.

**Files fixed**:
- `sim/golden_executor.py` — comparator expression
- `scripts/verify_w2_2_fm_golden_vectors.py:225` — same pattern

**Verification**: 2 RED tests → 5 GREEN; SFU/Vector regression 110/110; W2.2 golden vectors 14/14.

---

## T4C4 v3: Fixed-Point Vector Scaling

The T4C4 connected blk.0 dual-oracle hard gate achieved all projection cosines ≥0.976
and final cosine ≥0.988 using `_T4C4_VEC_SCALE = 4096` fixed-point scaling for Vector
RESID/VMUL operations.

The INT32 Vector datapath uses 2^12 fractional-bit scaling to preserve precision
across the FP32→INT32→FP32 bridge, modeling the fixed-point interface that real
hardware would use between floating-point and integer domains.

Nibble ordering contract: low nibble = first/even weight, high nibble = second/odd weight.

---

## Classification Boundaries (Enforced by Semantic Checker)

The semantic checker (`scripts/check_func_model_signoff_docs.py`) enforces:

1. **No scaled/single_tile test described as "full-shape"**: Any markdown line that
   contains a test name with `scaled` or `single_tile` must NOT contain `full-shape`.
   This prevents misrepresenting scaled regressions as signoff evidence.

2. **Provenance labels**: Synthetic data must be labeled synthetic. Real GGUF data
   must be labeled with the pinned SHA-256.

3. **Performance signoff**: Tracked separately. This checklist covers functional
   signoff only. Performance remains FAIL/PARTIAL.

---

## Scope Limitations

- **RTL-golden-readiness for full SoC RTL is deferred**. This signoff covers the
  Func Model's functional correctness as a golden reference, not the RTL's
  readiness for production tape-out.
- **Multi-layer / full-model signoff is NOT claimed**. Only blk.0 (single transformer
  layer) is covered by this evidence chain.
- **Performance signoff remains FAIL/PARTIAL** and is tracked in a separate
  document. Do NOT infer performance from functional correctness.
- **The Func Model implements the v1 Block/bootstrap architecture link**. It does
  not directly verify the v2+ FSA recommendation from Arc Model DSE. See
  `docs/arc_vs_func.md` for the architecture pipeline.
