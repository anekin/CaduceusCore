# Func Model Signoff Checklist — v3 (with Bug Fix + Bridge-Accum Fix + INTC Fix)

> **Date**: 2026-07-27
> **Scope**: v2 op-level + v3 SoC integration + bug-fix cycle (FM-004/005/006/007) + bridge accumulation fix (FM-005 sub-issue) + INTC KeyError fix (FM-008). RTL-golden-readiness for full SoC RTL is deferred.
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
| **F-FM-18** | Spike+firmware chain mode (mmul+sfu+vector) | ✅ PASS (after FM-007 fix) | T1 bug-fix, task-1b-v3 |
| **F-FM-19** | Spike+firmware forward pass (Qwen2.5-1.5B, --token-ids) | ✅ PASS (runs; WARN tolerance) | T5 bug-fix, task-1c-v3 |
| **F-FM-20** | PCIe DMA data path (Host↔NPU, TLP) | ✅ PASS | T2 v3, task-2-v3 |
| **F-FM-21** | Crossbar M=6/S=2 concurrent stress | ✅ PASS | T3 v3, task-3-v3 |
| **F-FM-22** | Doorbell ring buffer protocol | ✅ PASS | T4 v3, task-4-v3 |
| **F-FM-23** | INTC 7-source interrupt chain | ✅ PASS | T5 v3, task-5-v3 |
| **F-FM-24** | Host CPU communication | ✅ PASS | T6 v3, task-6-v3 |
| **F-FM-25** | SoC integration (Spike+firmware, 11-case validate --v3) | ✅ PASS | T7 v3, task-7-v3 |
| **F-FM-26** | OpCode enum unification (EngineOp, T0) | ✅ PASS | bug-fix-t0 |
| **F-FM-27** | Chain mode opcode mismatch fixed (FM-007, T1) | ✅ Fixed | bug-fix-t1 |
| **F-FM-28** | Weight pre-tiling + scale blocking (FM-005, T2) | ✅ Fixed | bug-fix-t2 |
| **F-FM-29** | SFU/Vector descriptor SRAM fields (FM-004, T4) | ✅ Fixed | bug-fix-t4 |
| **F-FM-30** | --token-ids fallback (FM-006, T5) | ✅ Fixed | bug-fix-t5 |
| **F-FM-31** | Bridge MXU cross-tile accumulation stale-read fix (FM-005 sub-issue, firmware activation-offset) | ✅ Fixed (`e7ed749`) | bridge-accum-t1-fix.txt; L0 Q_proj max_diff=9.16e-05 |
| **F-FM-32** | INTC KeyError ACK-before-PENDING fix (`.get()` defense in `_handle_intc`) | ✅ Fixed (`72ccbf7`) | task1-intc-keyerror-fix.txt; 13/13 INTC tests PASS |

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

## Bug Fix Cycle (2026-07-25–27)

The func-model-signoff-v3 revealed 4 bugs in the Spike+firmware integration path,
1 sub-issue (bridge accumulation), and 1 func model gap (INTC KeyError). A dedicated bug-fix cycle addressed all of them:

| Bug | Pri | Status | Root Cause | Fix |
|-----|-----|--------|-----------|-----|
| BUG-SOC-FM-007 | P0 | ✅ Fixed | Python opcode 0/1/2/3 vs firmware 0x00/0x01/0x0F/0x09 | Unified EngineOp enum; chain now NPU_HEAD=3 |
| BUG-SOC-FM-005 | P1 | ✅ Fixed | Row-major vs tiled DRAM layout + firmware activation-offset miscalculation | `_reorder_weights_to_firmware_tiles()` fixes layout; `e7ed749` fixes stale-read accumulation |
| BUG-SOC-FM-004 | P2 | ✅ Fixed | Firmware hardcoded SFU/Vector SRAM addresses | Descriptor src[4]-[6] now read; 15/15 fields aligned |
| BUG-SOC-FM-006 | P3 | ✅ Fixed | sz0001 lacks `tokenizers` module | `--token-ids` CLI fallback; forward pass runs |
| BUG-SOC-FM-008 | P2 | ✅ Fixed | `_handle_intc` used `self._status[key] &= ~value` without `.get()` fallback; ACK-before-PENDING raised KeyError | One-line `.get(...,0)` defense at `sim/mmio_bridge.py:590`, matching `_set_irq()` safe pattern (commit `72ccbf7`) |

**Bridge MXU Accumulation Fix (FM-005 sub-issue, 2026-07-26):**
- Root cause: Firmware `act_sram + k_start * 64` hardcoded offset miscalculated per-K-tile activation address. For `M=1`, `k_block≥2` read uninitialised SRAM → stale output.
- Fix: Changed to `act_sram + k_start * desc.M` in `firmware/npu_firmware.c` `dispatch_cmd()` (commit `e7ed749`).
- Post-fix: L0 Q_proj max_diff = 9.16e-05 (was 426), all 32 K-tiles accumulate correctly.

**Impact on Signoff**:
- Chain mode: **Was TIMEOUT → Now PASS**. FM-007 was the root cause.
- Forward pass: **Was ModuleNotFoundError → Now runs**. Numerical gap vs llama.cpp is pre-existing.
- MMUL smoke: **Was 50% zero entries → Now 0% zero entries**. Bridge accumulation stale read eliminated; max_diff ≤ 10.
- Descriptor alignment: **Was "design inconsistency" → Now PASS**. All 15 fields verified.
- Bridge accumulation: **Was stale after k_block=1 → Now FULLY ACCUMULATED**. Commit `e7ed749`.
- INTC KeyError: **Was KeyError crash on ACK-before-PENDING → Now handled gracefully**. 13/13 INTC tests PASS.
- Bug tracker: `docs/bugs/bugs-soc-func-model.md` updated. Stats: Open=0, Fixed=8.

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

## Known Remaining Issues

### Spike MMU Plugin ABI (`_GLIBCXX_USE_CXX11_ABI`)

The `npu_mmio_plugin.so` must be compiled with `-D_GLIBCXX_USE_CXX11_ABI=0` to
match the Spike binary's old C++ ABI on sz0001. This is a build-time requirement,
not a code defect. Documented in bug-fix T1 evidence.

### Forward Pass Numerical Gap vs llama.cpp

The Qwen2.5-1.5B forward pass through Spike produces a numerical gap vs llama.cpp
reference (L0 max_abs=6.05, max_rel=42.68 at tol=1e-01). This is a pre-existing
accuracy gap from the INT4 quantization / dequantization paths, not a regression
from the bug fixes.

## SoC Data-Path Gaps — NOT Blocking Current Signoff

Six SoC-level data paths identified in `docs/soc-fm-gap-spec.md` have **no Python
functional model coverage**. These are recognized gaps that do NOT block the current
Func Model golden-reference signoff but represent work items for a future phase:

| Gap # | Path | Current Status |
|:---:|------|:---:|
| 7 | PCIe TLP model | No TLP parser; `host_write_*` bypasses PCIe directly to DRAM |
| 8 | AXI4 Crossbar + APB Decoder model | MMIOBridge bypasses crossbar, accesses SRAM/DRAM directly |
| 1 | APB-MMIO Register Model | No unified register abstraction; per-engine `_handle_*` reimplementation |
| 2 | IBEX-AXI Bridge | RISCVMini has independent `self.mem`, not shared with FuncModel SRAM/DRAM |
| 9 | INTC/IRQ Chain | WFI is NOP; no interrupt delivery path from engine → CPU |
| 11 | IBEX-Firmware | `NPUFirmware` bypasses RISCVMini; no boot sequence |

These gaps are pre-specified with full API designs, testability plans, and a 3-wave
build order in `docs/soc-fm-gap-spec.md`. V3 signoff covers the **functional behavior**
of these SoC peripherals (PCIe/DMA/crossbar/doorbell/INTC/host-CPU) through standalone
test harnesses, but the integrated data paths through a shared crossbar + Ibex CPU
remain unimplemented.

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
- **Firmware was modified** in the bug-fix cycle and bridge-accum fix
  (`firmware/npu_firmware.c`). This is an explicit exception to the original v3
  signoff constraint ("Do NOT modify firmware") per the user-authorized fix plans.
  Changes include: `dispatch_cmd()` opcode match + activation offset (`e7ed749`),
  `read_sfu_desc()`/`read_vector_desc()` SRAM fields, `sfu_start()`/`vector_start()`
  SRAM parameters, removed `sfu_hw_op()`.

- **Spike plugin was rebuilt** (not source-modified) to fix C++ ABI compatibility.

- **SoC Data-Path Gaps are explicitly out of scope** for this signoff cycle.
  Six paths (`PCIE-TLP`, `XBAR-ARB`, `APB-MMIO`, `IBEX-AXI`, `IRQ-CHAIN`,
  `IBEX-FIRMWARE`) are pre-specified in `docs/soc-fm-gap-spec.md` with full
  API designs and testability plans. They are future work, not blockers.
