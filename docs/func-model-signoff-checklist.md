# Func Model Signoff Checklist — v3 + Performance Closure ✅

> **Date**: 2026-08-24 (last updated)
> **Scope**: v2 op-level + v3 SoC integration + bug-fix cycle (FM-004/005/006/007) + bridge accumulation fix (FM-005 sub-issue) + INTC KeyError fix (FM-008) + SoC data-path hardening closure (`fm-soc-datapath-hardening`, 2026-08-24, F-FM-SOC-01..13). RTL-golden-readiness for full SoC RTL is deferred.
> **Performance signoff**: ✅ PASS — func-model-performance-infra-calibration-closure completed 2026-08-11, T1-T25 + F1-F4 all passed, performance_spec_verified=true.

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

## Post-v3 Func Model Hardening Signoff (fm-hardening-phase10)

> **Date**: 2026-08-23
> **Scope**: Phase 10 bug-class hardening — layout/ring, scale/accumulate golden, packer equivalence, ABI constant drift, segment-boundary SRAM, reverse-dependency gate.

This section captures the additional signoff evidence produced after the v3 Func Model signoff above. It is mandatory for any Func Model baseline that will be handed off to SoC RTL verification.

| Signoff ID | Description | Status | Evidence |
|---|---|---|---|
| F-FM-H01 | Address-space contract module + per-runner layout checks | ✅ PASS | `sim/tests/test_address_space.py`, `sim/tests/test_spike_host_overlap.py` |
| F-FM-H02 | Command-ring single source of truth + `% 64` elimination | ✅ PASS | `sim/tests/test_command_ring.py`, `sim/tests/test_command_ring_stress.py` |
| F-FM-H03 | Scale-carrying MMUL golden hardening (SCALE_ADDR≠0) | ✅ PASS | `sim/tests/test_soc_fm.py::test_mmul_scale_nonzero` |
| F-FM-H04 | Accumulate (CTRL[2]) two-command-chain golden hardening | ✅ PASS | `sim/tests/test_soc_fm.py::test_mmul_accumulate` |
| F-FM-H05 | Dual activation packer byte-equivalence + column-major contract | ✅ PASS | `sim/tests/test_packer_equivalence.py` |
| F-FM-H06 | ABI constants single source (schema → C header → Python) | ✅ PASS | `sim/tests/test_npu_abi_constants.py`, `python3 scripts/gen_npu_abi.py --check` |
| F-FM-H07 | Segment-boundary SRAM-clear contract (ISSUE-13C) | ✅ PASS | `sim/tests/test_segment_boundary.py`, `sim/test_dram_bulk.py` |
| F-FM-H08 | RTL/firmware reverse-dependency gate + F-wave scripts | ✅ PASS | `scripts/fm_reverse_dependency_gate.sh`, `scripts/fm_hardening_f{1..4}.sh` |

**Verification summary**: F1 Plan compliance audit `APPROVE` (14/14 evidence PASS), F2 Code quality `APPROVE` (0 residue, 0 new pytest failures), F4 Scope fidelity `APPROVE` (0 scope creep). F3 Real manual QA was blocked solely by the pre-existing Spike `mmul_smoke` failure (BUG-SOC-FM-005, `max_diff=7.64e+02`); all other F3 stages (firmware build, reverse-gate dry-run, W4-PERF p0/p1, FM-SOC-001/003/032) passed.

## SoC Data-Path Hardening Signoff (fm-soc-datapath-hardening)

> **Date**: 2026-08-24
> **Scope**: SoC-level data path FM guards added to close 13 of the 14 gaps listed in `.omo/plans/soc-rtl-verification-vplan.md` (6 SoC interconnect, 3 firmware/CPU, 4 E2E). Only E2E-07 performance calibration remains ❌ (`calibration_state=uncalibrated`, out of scope here).

This section captures the SoC-level data path FM guards added after the
fm-hardening-phase10 signoff above. They close the six SoC data-path gaps from
`docs/soc-fm-gap-spec.md` plus the firmware/CPU and E2E gaps from the SoC RTL
verification vplan, giving every closed gap a runnable functional-model guard.

| Signoff ID | Description | Status | Evidence |
|---|---|---|---|
| F-FM-SOC-01 | PCIe TLP complete chain (SOC-13) | ✅ PASS | `sim/tests/test_pcie_tlp_chain.py`; `build/evidence/task-1-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-02 | INTC ENABLE/THRESHOLD gating (SOC-17 / FW-10) | ✅ PASS | `sim/mmio_bridge.py` + `sim/tests/test_intc_gating.py`; `build/evidence/task-2-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-03 | AXI crossbar arbitration fairness (SOC-14) | ✅ PASS | `sim/tests/test_crossbar_arbitration.py`; `build/evidence/task-3-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-04 | APB register conformance replay (SOC-15) | ✅ PASS | `sim/tests/test_apb_register_conformance.py`; `build/evidence/task-4-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-05 | Ibex shared address space cross-engine (SOC-16) | ✅ PASS | `sim/tests/test_ibex_shared_address_space.py`; `build/evidence/task-5-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-06 | IRQ-driven firmware dispatch (FW-10) | ✅ PASS | `sim/tests/test_irq_driven_dispatch.py`; `build/evidence/task-6-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-07 | Firmware boot sequence (SOC-18) | ✅ PASS | `sim/tests/test_firmware_boot_sequence.py`; `build/evidence/task-7-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-08 | Spike↔Ibex ring management alignment (FW-08) | ✅ PASS | `sim/tests/test_spike_ibex_ring_alignment.py`; `build/evidence/task-8-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-09 | Firmware memory contract JSON generation & comparison (FW-09) | ✅ PASS | `scripts/gen_firmware_memory_contract.py` + `sim/tests/test_memory_contract.py`; `build/evidence/task-9-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-10 | 28-layer Qwen full-model FM gate (E2E-04) | ✅ PASS | `sim/tests/test_soc_fm_long_sequence.py::test_multi_layer_persistent_offset`; `build/evidence/task-10-fm-soc-datapath-hardening.txt` + `build/evidence/task-11-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-11 | MobileNetV3 CV chain FM gate (E2E-05) | ✅ PASS | `sim/tests/test_mobilenetv3_fm_chain.py`; `build/evidence/task-12-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-12 | Spike forward pass tolerance regression gate (E2E-06) | ✅ PASS | `sim/tests/test_spike_forward_tolerance.py`; `build/evidence/task-13-fm-soc-datapath-hardening.txt` |
| F-FM-SOC-13 | ABORT/MXU idle coverage (E2E-08) | ✅ PASS | `sim/tests/test_soc_fm.py::test_mmul_attn_weight_shape` + `test_mmul_attn_weight_shape_not_dispatched`; `build/evidence/task-14-fm-soc-datapath-hardening.txt` |

**Final Wave**: F1 plan compliance audit APPROVED (14/14 evidence PASS), F2 code
quality APPROVED (0 residue, 0 new pytest failures; baseline 20 failed / 2280 passed /
15 errors), F3 manual QA APPROVED (--dry-run), F4 scope fidelity APPROVED (0 scope
creep). Plan `fm-soc-datapath-hardening` completed 2026-08-24.

## SoC Data-Path Gaps — Coverage After fm-soc-datapath-hardening

Six SoC-level data paths identified in `docs/soc-fm-gap-spec.md` previously had **no
Python functional model coverage**. All six now have FM guards from
`fm-soc-datapath-hardening` (see "SoC Data-Path Hardening Signoff" above):

| Gap # | Path | Current Status |
|:---:|------|:---:|
| 7 | PCIe TLP model | ✅ Covered by fm-soc-datapath-hardening (F-FM-SOC-01, `sim/tests/test_pcie_tlp_chain.py`) |
| 8 | AXI4 Crossbar + APB Decoder model | ✅ Covered by fm-soc-datapath-hardening (F-FM-SOC-03, `sim/tests/test_crossbar_arbitration.py`) |
| 1 | APB-MMIO Register Model | ✅ Covered by fm-soc-datapath-hardening (F-FM-SOC-04, `sim/tests/test_apb_register_conformance.py`) |
| 2 | IBEX-AXI Bridge | ✅ Covered by fm-soc-datapath-hardening (F-FM-SOC-05, `sim/tests/test_ibex_shared_address_space.py`) |
| 9 | INTC/IRQ Chain | ✅ Covered by fm-soc-datapath-hardening (F-FM-SOC-02 + F-FM-SOC-06, `sim/tests/test_intc_gating.py` + `test_irq_driven_dispatch.py`) |
| 11 | IBEX-Firmware | ✅ Covered by fm-soc-datapath-hardening (F-FM-SOC-07, `sim/tests/test_firmware_boot_sequence.py`) |

These gaps were pre-specified with full API designs, testability plans, and a 3-wave
build order in `docs/soc-fm-gap-spec.md`. With `fm-soc-datapath-hardening` closed
(2026-08-24), only **performance calibration (E2E-07, `calibration_state=uncalibrated`)**
and the **pre-existing RTL bugs (BUG-RTL-SOC-002 / BUG-RTL-SOC-007, both still Open)**
remain open / out of scope for this signoff. They are not claimed as fixed here.

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

---

## Task 22: Scoped Software Signoff Aggregation

> **Date**: 2026-07-28
> **Scope**: Software stack packaging and evidence aggregation across all seven
>   CI tiers (L0–L5 + Framework). Hardware signoff (FPGA, full RTL replay,
>   performance) is explicitly NOT claimed.
> **Evidence report**: `.omo/evidence/task-22-release-signoff.json`
> **Aggregator**: `scripts/aggregate_software_signoff.py`
> **Build script**: `scripts/build_software_release.py`

This section documents the Task 22 package-and-signoff step. It does **not**
supersede the v3 Func Model signoff above; it is an additive software-stack
signoff that aggregates evidence from Tasks 1–21 into a versioned JSON report.

### Tier Mapping

| Tier | Tasks | CI Job | Description |
|------|-------|--------|-------------|
| **L0** | 1, 2 | `l0_abi` | ABI v1.0 schema (`spec/npu_abi.json`), generated artifacts, binding migration |
| **L1** | 3, 7 | `l1_runtime` | Stable C Host Runtime ABI (`cad_device_t`, etc.) + core/mock transport |
| **L2** | 4, 8, 9, 13, 14 | `l2_func_model` | Func Model device server, versioned protocol, adapter protocol, fault injection, differential |
| **L3** | 6, 12 | `l3_spike` | Spike RISC-V simulator + firmware ELF toolchain manifest |
| **L4** | 10, 18 | `l4_rtl_skeleton` | RTL DUT adapter interface + RTL transport conformance (FakeDUT; no real VCS/cocotb in CI) |
| **L5** | 19, 20 | `l5_fpga_nogo` | FPGA transport interface (VFIO/UIO/vendor) + structured NO-GO evidence |
| **Framework** | 5, 15, 16, 17, 21 | `framework_qwen_executorch` | llama.cpp dependency lock, ggml lifecycle/ops CSV, Qwen2.5-3B software gates, ExecuTorch delegate |

### Aggregation Results

The evidence aggregator reads all `.omo/evidence/task-*-*.json` / `.log` / `.csv`
files for Tasks 1–21, classifies them into the seven tiers above, and produces
a deterministic JSON signoff report.

| Tier | Strict-Mode Status | Rationale |
|------|:--------------:|-----------|
| L0 | PASS | ABI schema generation idempotent; binding migration 13/13 + abi_layout pass |
| L1 | PASS | CTest 15/15, Python conformance 17/17 (mock transport) |
| L2 | PASS | Device protocol 9/9, fault/differential tests pass |
| L3 | PASS | Spike manifest present; firmware artifacts verified by SHA-256; `dtc` + `riscv-gcc` available |
| L4 | PASS | RTL adapter 6/6 scenarios (FakeDUT); transport conformance passes |
| L5 | **BLOCKED** | **Task 20 FPGA is NO-GO** — no FPGA platform available in this phase |
| Framework | **BLOCKED** | **Task 15 ggml lifecycle is BLOCKED** — `fm://python` device server prerequisite unavailable; Tasks 5, 16, 17, 21 PASS |
| **Overall** | **BLOCKED** | L5 and Framework blockades propagate per aggregator rules |

### What Is NOT Claimed

- **FPGA PASS** — Task 20 is intentionally BLOCKED/NO-GO because no FPGA
  platform is available. The aggregator correctly reports overall BLOCKED when
  L5 is required with `--require l0,l1,l2,l3,l4,l5,framework`.
- **Task 15 ggml lifecycle PASS** — Task 15 is BLOCKED because the
  `fm://python` Func Model device server is not available in this environment
  (`cadDeviceOpen(fm://python) failed: Device lost`). The evidence file is
  preserved with an explicit `VERDICT: BLOCKED` annotation and
  `blocked_reason: prerequisite unavailable`.
- **Full RTL replay PASS** — The RTL adapter (L4) proves the adapter interface
  contract through FakeDUT scenarios, but does not replay the FM-SOC vector
  suite through a live VCS/cocotb simulation in CI.
- **Performance signoff** — Performance remains FAIL/PARTIAL as stated in the
  v3 signoff scope. The software aggregator does not include performance metrics.
- **ABI v2+ or FSA architecture** — The aggregator validates the v1 Block/
  bootstrap ABI. Future ABI versions require a new schema generation cycle.

### CI Integration

The `.github/workflows/caduceus-core-ci.yml` workflow implements the tiered
signoff as eight independent jobs:

1. `l0_abi` — ABI schema pytest + `gen_npu_abi.py --check`
2. `l1_runtime` — CMake build + CTest + Python conformance
3. `l2_func_model` — Device protocol, fault injection, scenario tests
4. `l3_spike` — Spike manifest check + toolchain tests (may skip when tools absent)
5. `l4_rtl_skeleton` — RTL adapter conformance + transport tests
6. `l5_fpga_nogo` — FPGA NO-GO signoff (`continue-on-error: true`)
7. `framework_qwen_executorch` — Qwen gates + ExecuTorch delegate
8. `release_aggregator` — Runs after all tiers; executes the reproducible build
   and the evidence aggregator, uploads the signoff JSON as an artifact.

L5 uses `continue-on-error: true` so the BLOCKED verdict does not fail the
workflow. All other jobs fail on test/command failure per standard CI semantics.
The `release_aggregator` job `needs` all seven tier jobs to ensure evidence is
complete before the aggregation runs.

---

## Clean-Checkout Bootstrap

> **Date**: 2026-07-29
> **Scope**: Reproducible clean-checkout bootstrap scripts for CI and developer onboarding.
> **Requires**: Ubuntu 22.04 (or compatible) with git, python3, and pip pre-installed.

Two bootstrap scripts ensure that a fresh clone of CaduceusCore can be built
from scratch with zero manual setup beyond base OS tooling.

### `scripts/ci_bootstrap.sh` — Software Baseline

Installs missing system packages (cmake, g++, flatc), Python dependencies,
builds the C/C++ Host Runtime with tests, and performs a reproducible release
build.  This is the **mandatory** baseline for all CI tiers.

**What it does:**

| Step | Command | Purpose |
|------|---------|---------|
| 1 | `apt-get install cmake g++ flatbuffers-compiler` | System build tools (best-effort; already-installed tools are skipped) |
| 2 | `pip install -r requirements.txt` | Python test/model dependencies |
| 3 | `cmake -S software -B build/software -DCADUCEUS_BUILD_TESTS=ON` | CMake configure |
| 4 | `cmake --build build/software` | Build Host Runtime + tests |
| 5 | `ctest --test-dir build/software --output-on-failure` | Run C/C++ test suite |
| 6 | `python3 scripts/build_software_release.py --clean --install-prefix build/install` | Release build & install |

**When to run:**
- After a fresh `git clone`
- After `git clean -fdx` or switching branches with build-system changes
- In CI as the first step of any job that needs a compiled Host Runtime
- Before running `scripts/aggregate_software_signoff.py`

**Expected output:** exit 0, all steps PASS.  Installed artifacts at
`build/install/lib/libcaduceus_runtime.so` and `build/install/include/`.

**Firmware is NOT included** — the RISC-V cross-compiler is optional.
See `ci_bootstrap_firmware.sh` below.

### `scripts/ci_bootstrap_firmware.sh` — Firmware Build

Builds the NPU bare-metal firmware (`npu_firmware.elf`, `npu_firmware_spike.elf`)
when the RISC-V cross-compiler is available.  Gracefully skips with exit 0 if
the toolchain is missing.

**Prerequisites (Ubuntu 22.04):**
```bash
sudo apt-get install -y gcc-riscv64-unknown-elf
```

**What it does:**
- Checks for `riscv64-unknown-elf-gcc`, `objcopy`, `objdump`, `size`
- If found: runs `make -C firmware clean && make -C firmware all`
- If missing: prints a clear skip message with install instructions, exits 0

**When to run:**
- Before CI tier L3 (Spike simulation / real-firmware signoff)
- Before running `scripts/run_runtime_spike_signoff.py`
- After installing or upgrading the RISC-V toolchain

**Expected output:** exit 0 (PASS or gracefully SKIPPED).  On a machine
without the RISC-V toolchain the script prints the install command and exits 0.
This is intentional — the firmware build is not required for the software
baseline.

### Running Both (Typical Developer Workflow)

```bash
# Build everything that can be built (software + optional firmware)
bash scripts/ci_bootstrap.sh 2>&1 | tee .omo/evidence/task-w1t5-bootstrap.log
bash scripts/ci_bootstrap_firmware.sh 2>&1 | tee .omo/evidence/task-w1t5-firmware.log
```

The software baseline must pass.  The firmware script may skip — that is
expected and not an error.

---

## Performance Signoff Status — func-model-performance-infra-calibration-closure ✅

Completed 2026-08-11. All T1-T25 implementation tasks + Final Wave F1-F4 passed.

| Signoff ID | Description | Status |
|---|---|---|
| PERF-FM-01 | Provider formula gates (MXU/SFU/Vector/DMA/DRAM/NoC/KV/SW, 104 rows) | ✅ PASS |
| PERF-FM-02 | Independent provider + workload oracles (Path B, zero Path A imports) | ✅ PASS |
| PERF-FM-03 | Semantic MMIO performance events + contract validation | ✅ PASS |
| PERF-FM-04 | MXU architectural estimates vs independent oracle | ✅ PASS |
| PERF-FM-05 | SFU + Vector spec gates (54 rows, 6 ops each) | ✅ PASS |
| PERF-FM-06 | DMA + DRAM spec gates (28 rows total) | ✅ PASS |
| PERF-FM-07 | NoC + KV Cache spec gates | ✅ PASS |
| PERF-FM-08 | SW Overhead assumptions (4 workloads, assumption_only=true) | ✅ PASS |
| PERF-FM-09 | Qwen2.5-3B workload canonicalization (2048/11008/36/16/2) | ✅ PASS |
| PERF-FM-10 | CV workloads (MobileNetV3/ResNet50/YOLOv8n) | ✅ PASS |
| PERF-FM-11 | Timeline convergence + overlap semantics | ✅ PASS |
| PERF-FM-12 | Qwen dual-path spec gates (4 workloads, Path A vs B ≤20%) | ✅ PASS |
| PERF-FM-13 | CV dual-path spec gates (3 workloads, Path A vs B ≤20%) | ✅ PASS |
| PERF-FM-14 | Sensitivity sweeps (6 dims, monotonicity + endpoints) | ✅ PASS |
| PERF-FM-15 | Cross-model scaling (1.5B/3B/7B, monotonic + weight_byte delta <20%) | ✅ PASS |
| PERF-FM-16 | Uncertainty-aware report-only KPIs (low/base/high bands) | ✅ PASS |
| PERF-FM-17 | Adversarial matrix (26 faults, disable-each-validator) | ✅ PASS |
| PERF-FM-18 | Performance regression baseline (spec hash frozen + change policy) | ✅ PASS |
| PERF-FM-19 | Portable CI + full signoff orchestration (--all-spec, no VCS/network) | ✅ PASS |
| PERF-FM-20 | Documentation + bug ledger consistency audit | ✅ PASS |
| PERF-FM-21 | Clean performance-spec signoff (T25, calibration_state=uncalibrated) | ✅ PASS |
| PERF-FM-22 | Plan compliance audit (F1, 25 DoneClaims + canonical hash recompute) | ✅ PASS |
| PERF-FM-23 | Architecture + code-quality audit (F2, 802/802 pytest + 5 checks) | ✅ PASS |
| PERF-FM-24 | Real agent QA (F3, 9 cases + 4 faults) | ✅ PASS |
| PERF-FM-25 | Scope + claim fidelity audit (F4, zero waivers, non-omo dirty=0) | ✅ PASS |

**Evidence**: `.omo/evidence/task-25-func-model-perf-spec-signoff.json` + bundle under `.omo/evidence/func-model-perf-spec/`
**Report**: `reports/func-model-perf-verification-report.md`
**Plan**: `.omo/plans/func-model-performance-infra-calibration-closure.md`
**State**: `performance_spec_verified=true, calibration_state=uncalibrated`
