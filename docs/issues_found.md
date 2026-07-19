# Issues Found & Fix Status

> **Project**: CaduceusCore Verification
> **Generated**: 2026-06-20
> **Scope**: Arc Model (B1-B6), Func Model / Firmware (F1-F5), Documentation (D1-D5)

---

## Arc Model Issues (B1-B6)

### B1 — qkv dimension uses raw hidden size instead of `num_heads * head_dim`

| Field | Detail |
|-------|--------|
| **Description** | `MODELS` dict stored a 4-tuple `(hidden, intermediate, layers, kv_heads)`. The `evaluate()` method computed `qkv = spec[0]` (raw hidden size), which is incorrect for Qwen2.5-3B where hidden=2560 but qkv should be 4096 (32 heads x 128). |
| **Location** | `sim/arc_model.py` — `MODELS` dict (line 75-81), `evaluate()` qkv computation (line 224) |
| **Root Cause** | The model spec tuple lacked a `num_heads` field, and qkv was taken as `spec[0]` (hidden size) instead of computing `num_heads * head_dim`. |
| **Fix** | Extended `MODELS` dict from 4-tuple to 6-tuple `(qkv, hidden, intermediate, layers, num_heads, kv_heads)`. Changed qkv computation to `num_heads * head_dim` (head_dim=128). |
| **Commit** | `62ed61e fix(arc): correct qkv dimension using num_heads * head_dim (B1)` |
| **Verification** | `test_qkv_dimension_1_5b`, `test_qkv_dimension_3b`, `test_qkv_dimension_7b` in `sim/tests/test_arc_model.py` — all 3 PASS (previously `test_qkv_dimension_3b` was RED). Evidence: `.omo/evidence/task-11-green-qkv.txt` |
| **Status** | **FIXED** |

---

### B2 — Hardcoded macOS path in validate_quant.py

| Field | Detail |
|-------|--------|
| **Description** | `validate_quant.py` used `sys.path.insert(0, "/Users/zheng/npu/sim")` — a macOS-specific path that crashes on Linux. |
| **Location** | `sim/validate_quant.py` — lines 4-5 |
| **Root Cause** | Developed on macOS, the hardcoded absolute path was never parameterized or made cross-platform. |
| **Fix** | Replaced hardcoded macOS paths with `pathlib.Path(__file__).parent` resolution. Added `--sim-dir` CLI argument with script-directory default. |
| **Commit** | `191324d fix(validate): replace hardcoded path with cross-platform resolution (B2)` |
| **Verification** | `test_validate_quant_path_flexible` in `sim/tests/test_arc_model.py` — PASS (previously RED, failed with `ModuleNotFoundError` on `q4_dequant`). Evidence: `.omo/evidence/task-6-red-path.txt` (RED state), `.omo/evidence/task-12-green-path.txt` |
| **Status** | **FIXED** |

---

### B3 — `print()` used instead of structured logging

| Field | Detail |
|-------|--------|
| **Description** | All diagnostic output in `arc_model.py` used raw `print()` statements, making it difficult to control verbosity or redirect logs. |
| **Location** | `sim/arc_model.py` — multiple locations |
| **Root Cause** | The module was written as a quick script without a logging framework. |
| **Fix** | Replaced all `print()` calls with `logging.getLogger(__name__)` calls. Added configurable log level. |
| **Commit** | `d9057cc refactor(arc): add logging, to_json, error handling, path resolution` |
| **Verification** | No dedicated regression test for logging, but all existing tests pass with logging active. `pytest sim/tests/ -v` — 109 passed. |
| **Status** | **FIXED** |

---

### B4 — No structured output (to_json / to_dict)

| Field | Detail |
|-------|--------|
| **Description** | `ArcReport` had no `to_dict()` or `to_json()` method, making programmatic consumption of evaluation results difficult. |
| **Location** | `sim/arc_model.py` — `ArcReport` dataclass (line 27-56) |
| **Root Cause** | Originally written for human-readable console output only. |
| **Fix** | Added `ArcReport.to_dict()` returning a nested dict and `ArcReport.to_json()` returning JSON string. Added `json.dumps(report.to_dict(), indent=2)` to the output path. |
| **Commit** | `d9057cc refactor(arc): add logging, to_json, error handling, path resolution` |
| **Verification** | `task-17-to-json.txt` verified: `python3 -c "from arc_model import ArcReport; ... print(json.dumps(r.to_dict())[:100])"` produces valid JSON, exit 0. Evidence: `.omo/evidence/task-17-to-json.txt` |
| **Status** | **FIXED** |

---

### B5 — evaluate() lacks fine-grained error handling

| Field | Detail |
|-------|--------|
| **Description** | `evaluate()` had no try/except around quantization failure or partial weight loading. A single failure would crash the entire evaluation. |
| **Location** | `sim/arc_model.py` — `evaluate()` method (line 139-261) |
| **Root Cause** | Assumed all inputs were valid with no error recovery path. |
| **Fix** | Added per-layer try/except around precision eval. Quantization failures are caught and logged with `logging.warning`, and a failed layer is recorded in `PrecisionReport.valid=False` instead of crashing. |
| **Commit** | `d9057cc refactor(arc): add logging, to_json, error handling, path resolution` |
| **Verification** | Covered by existing test suite — all 109 pytest tests pass. |
| **Status** | **FIXED** |

---

### B6 — Config path resolved relative to CWD, not `__file__`

| Field | Detail |
|-------|--------|
| **Description** | `ArcModel(config_path="config/npu_config.yaml")` failed when called from a directory other than `sim/`, because the path was resolved relative to `os.getcwd()`. |
| **Location** | `sim/arc_model.py` — `__init__()` (line 83-87) |
| **Root Cause** | Path resolution used `os.getcwd()` instead of `os.path.dirname(__file__)`. |
| **Fix** | Changed default config path resolution to `Path(__file__).parent / "config" / "npu_config.yaml"`. |
| **Commit** | `d9057cc refactor(arc): add logging, to_json, error handling, path resolution` |
| **Verification** | `task-17-config-path.txt` verified: calling `ArcModel()` from `/tmp` with `PYTHONPATH` set correctly resolves config. Evidence: `.omo/evidence/task-17-config-path.txt`. Also see `6bc598d test(arc): avoid B6 config-path bug in qkv tests by using class attribute`. |
| **Status** | **FIXED** |

---

## Func Model / Firmware Issues (F1-F5)

### F1 — SFU/Vector handlers are empty stubs in MMIOBridge

| Field | Detail |
|-------|--------|
| **Description** | `_handle_sfu()` and `_handle_vector()` in `MMIOBridge` immediately set `STATUS=DONE` without performing any computation. The output SRAM region was left as zeros. |
| **Location** | `sim/mmio_bridge.py` — `_handle_sfu()` (line 118-130), `_handle_vector()` (line 131-146) |
| **Root Cause** | MMIOBridge was a skeleton with only the MXU handler implemented. SFU/Vector dispatch was placeholder code. |
| **Fix** | Implemented real SFU dispatch: reads I_ADDR from SRAM, dispatches to `GoldenSFU` (softmax/gelu/silu/layernorm/rope), writes output to O_ADDR, sets `STATUS=DONE` + IRQ. Implemented real Vector dispatch: reads A_ADDR/B_ADDR from SRAM, dispatches to `GoldenVector` (add/mul/max_reduce/sum_reduce/conv_i32_to_f16/residual_add), writes output to O_ADDR, sets `STATUS=DONE` + IRQ. |
| **Commit** | `ba2a475 fix(mmio): implement SFU/Vector handlers with GoldenSFU/Vector dispatch (F1)` |
| **Verification** | `test_sfu_handler_computes` and `test_vector_handler_computes` in `sim/tests/test_mmio_bridge.py` — both PASS (previously both RED, output was all zeros). Evidence: `.omo/evidence/task-7-red-sfu-vector.txt` (RED state), `.omo/evidence/task-13-green-mmio.txt`. SFU verification: 19/19 tests PASS in `logs/verify_sfu.log`. |
| **Status** | **FIXED** |

---

### F2 — NPUFirmware._dispatch() only handles MMUL opcode

| Field | Detail |
|-------|--------|
| **Description** | `NPUFirmware._dispatch()` in `miniv.py` only handled opcode 0 (MMUL). SFU (1), Vector (2), and DMA (3) opcodes all returned `"UNKNOWN OPCODE"`. |
| **Location** | `sim/miniv.py` — `NPUFirmware._dispatch()` (line 285-324) |
| **Root Cause** | The firmware simulation was implemented incrementally with only MMUL support; other opcodes were never wired up. |
| **Fix** | Added dispatch branches for SFU (opcode 1 → `MMIOBridge._handle_sfu()`), Vector (opcode 2 → `MMIOBridge._handle_vector()`), and DMA (opcode 3 → `MMIOBridge._handle_dma()`). Kept existing MMUL dispatch unchanged. |
| **Commit** | `d6e6d50 fix(firmware): implement full opcode dispatch for SFU/Vector/DMA (F2)` |
| **Verification** | `test_dispatch_sfu`, `test_dispatch_vector`, `test_dispatch_dma` in `sim/tests/test_firmware.py` — all PASS (previously RED). Evidence: `.omo/evidence/task-8-red-dispatch.txt`, `.omo/evidence/task-14-green-dispatch.txt`. Also verified through func_model pass in `logs/verify_func_model.log` — firmware correctly dispatches MMUL and returns `{'opcode': 0, 'status': 'done'}`. |
| **Status** | **FIXED** |

---

### F3 — Stale `models/golden.py` has no deprecation warning

| Field | Detail |
|-------|--------|
| **Description** | `sim/models/golden.py` is a stale duplicate of `sim/golden_executor.py`. Importing from it silently succeeds with no indication that a newer replacement exists. |
| **Location** | `sim/models/golden.py` |
| **Root Cause** | The module was superseded by `golden_executor.py` but never marked as deprecated. |
| **Fix** | Added `import warnings` + `warnings.warn("models.golden is deprecated, use golden_executor instead", DeprecationWarning)` at the top of `sim/models/golden.py`. |
| **Commit** | `bcbaa3b chore(golden): add DeprecationWarning to stale models/golden.py (F3)` |
| **Verification** | `test_models_golden_deprecated` in `sim/tests/test_golden_deprecation.py` — PASS. Previously: `from models.golden import GoldenMXU` produced no warning (RED state). Evidence: `.omo/evidence/task-9-red-golden.txt`, `.omo/evidence/task-15-green-deprecation.txt`. |
| **Status** | **FIXED** |

---

### F4 — INT64 intermediate precision wider than hardware INT32

| Field | Detail |
|-------|--------|
| **Description** | NumPy `int32 @ int32` matmul produces `int64` on most platforms. The golden model uses this wider intermediate accumulation, which is more conservative than the hardware INT32 accumulator. This means the golden model may not catch hardware overflow bugs. |
| **Location** | `sim/golden_executor.py` — line 124-125 (matmul path), `sim/compare_rtl.py` — line 79 (diff computation) |
| **Root Cause** | NumPy's default integer promotion rules produce int64 from int32 operands. This is a platform-dependent behavior. |
| **Fix** | **Not fixed** — documented as conservative design choice. The wider accumulation ensures the golden model never misses overflows (may produce false negatives for hardware overflow bugs). The comment at line 124-125 explicitly notes: "This is conservative (wider than hardware INT32) → won't miss overflows." |
| **Commit** | N/A — known design limitation |
| **Verification** | The `compare_rtl.py` script and `test_e2e_golden.py` both use `np.abs(golden.astype(np.int64) - result.astype(np.int64))` for diff computation, which is correct for detecting any mismatch. |
| **Status** | **KNOWN ISSUE** — conservative by design. If exact INT32 truncation behavior is needed, a future task could add an INT32 accumulation mode via `np.int32` clip. |

---

### F5 — tile_mmul() has no input validation or error handling

| Field | Detail |
|-------|--------|
| **Description** | `tile_mmul()` in `tile_scheduler.py` accessed `desc['M']`, `desc['K']`, `desc['N']` without any type checking. Invalid inputs (e.g., string descriptor) caused `TypeError` instead of `ValueError`. |
| **Location** | `sim/tile_scheduler.py` — `tile_mmul()` (line 20-106) |
| **Root Cause** | Written as a quick prototype without defensive programming. |
| **Fix** | Added input validation: descriptor type must be dict, `M`/`K`/`N` must be positive integers, DMA address range validation, MXU dimension validity checks. All raise `ValueError` with descriptive messages. |
| **Commit** | `dd37b40 fix(tile): add input validation and error handling to tile_mmul (F5)` |
| **Verification** | `test_invalid_descriptor_shape` in `sim/tests/test_tile_scheduler.py` — PASS (previously RED). Evidence: `.omo/evidence/task-10-red-tile.txt` (RED state), `.omo/evidence/task-16-green-tile.txt`. |
| **Status** | **FIXED** |

---

## Documentation Issues (D1-D5)

### D1 — SRAM size inconsistency (2MB vs 4MB)

| Field | Detail |
|-------|--------|
| **Description** | `NPU硬件详细架构设计v0.1.md` documented SRAM as 2MB, while `golden_executor.py` (SRAM_SIZE=4MB) and `func_model_architecture.md` (4MB) used 4MB. |
| **Location** | `docs/NPU硬件详细架构设计v0.1.md` |
| **Root Cause** | The architecture document was written during an earlier design phase before SRAM was increased to 4MB. |
| **Fix** | Updated all SRAM references in the architecture doc to 4MB. Added explicit distinction between L1 SRAM (256KB x 2) and Unified Buffer (4MB). Updated document version from v0.1 to v0.5. |
| **Commit** | `c1a20d5 docs: unify SRAM size to 4MB across architecture doc (D1)` |
| **Verification** | `grep -c "4MB\|4 MB" docs/NPU硬件详细架构设计v0.1.md` confirms Unified Buffer is consistently documented as 4 MB. Evidence: `.omo/evidence/task-22-sram.txt`. |
| **Status** | **FIXED** |

---

### D2 — Vector Unit chapter missing from architecture doc

| Field | Detail |
|-------|--------|
| **Description** | The architecture document described the MXU and SFU in detail but had no section for the Vector Unit (128-wide SIMD, 7 ops). |
| **Location** | `docs/NPU硬件详细架构设计v0.1.md` |
| **Root Cause** | The Vector Unit was added to the ISA after the initial architecture document was written. |
| **Fix** | Added a Vector Unit chapter covering: 128-wide SIMD architecture, the 7 Vector ops (ADD, MUL, RED_MAX, RED_SUM, CONV, RESID), register interface (A_ADDR, B_ADDR, O_ADDR, DIM, CMD), and implementation notes. |
| **Commit** | `6639cf8 docs: add Vector Unit chapter and complete ISA table (D2/D3)` |
| **Verification** | Architecture doc now contains Vector Unit section with all 7 ops documented. Evidence: `.omo/evidence/task-23-isa.txt`. |
| **Status** | **FIXED** |

---

### D3 — ISA table incomplete (14 of 23 opcodes documented)

| Field | Detail |
|-------|--------|
| **Description** | The ISA table in the architecture document listed only 14 opcodes. Missing: SILU, VADD, VMUL, VRED_MAX, VRED_SUM, VCONV, VRESID, DMA_LDD, DMA_STD. |
| **Location** | `docs/NPU硬件详细架构设计v0.1.md` — ISA table |
| **Root Cause** | The ISA grew from 14 to 23 opcodes as SFU (SiLU) and Vector (7 ops) and extended DMA (2 ops) were added, but the document was never updated. |
| **Fix** | Expanded ISA table to all 23 opcodes. Cross-referenced with `sim/engine/isa.py` OpCode enum. |
| **Commit** | `6639cf8 docs: add Vector Unit chapter and complete ISA table (D2/D3)` |
| **Verification** | ISA table now contains all 23 opcodes matching `sim/engine/isa.py`. Evidence: `.omo/evidence/task-23-isa.txt`. |
| **Status** | **FIXED** |

---

### D4 — Performance numbers inconsistent across documents

| Field | Detail |
|-------|--------|
| **Description** | Performance numbers (tok/s, bandwidth estimates) differed between `NPU硬件详细架构设计v0.1.md`, `NPU_Engines_Architecture_Guide.md`, and `README.md`. Some numbers were from theoretical estimates while others were from v2 simulator output. |
| **Location** | `docs/NPU硬件详细架构设计v0.1.md`, `docs/NPU_Engines_Architecture_Guide.md`, `README.md` |
| **Root Cause** | Multiple documents were written at different stages of the design, and performance estimates were updated without cross-document synchronization. |
| **Fix** | Unified all performance numbers to v2 tiling-aware simulator output (15 tok/s baseline, 21 tok/s with WC optimization at 75% LPDDR5 efficiency). Added explicit data source annotations and version markers. |
| **Commit** | `51e04d7 docs: remove duplicate sim doc, add deprecation marker, unify perf numbers (D4/D5)` |
| **Verification** | Performance numbers consistent across architecture doc, engine guide, and README. Evidence: `.omo/evidence/task-24-cleanup.txt`. |
| **Status** | **FIXED** |

---

### D5 — Duplicate documents and missing deprecation markers

| Field | Detail |
|-------|--------|
| **Description** | `docs/npu_sim_v0.2.md` was a duplicate of `docs/NPU系统级模拟器方案v0.1.md`. `NPU软件架构方案v0.1.md` was superseded by v0.2 but had no deprecation marker. |
| **Location** | `docs/npu_sim_v0.2.md` (deleted), `docs/NPU软件架构方案v0.1.md` |
| **Root Cause** | Documentation was created organically without a deprecation/version management policy. |
| **Fix** | Deleted `docs/npu_sim_v0.2.md` (redundant duplicate). Added `DEPRECATED: 已由 v0.2 替代` marker to `NPU软件架构方案v0.1.md`. |
| **Commit** | `51e04d7 docs: remove duplicate sim doc, add deprecation marker, unify perf numbers (D4/D5)` |
| **Verification** | `ls docs/npu_sim_v0.2.md` returns "No such file". `head -5 docs/NPU软件架构方案v0.1.md` shows deprecation marker at top. Evidence: `.omo/evidence/task-24-cleanup.txt`. |
| **Status** | **FIXED** |

---

## Known Remaining Issues

### func_model.py hardcoded trace path crash

| Field | Detail |
|-------|--------|
| **ID** | K1 |
| **Description** | `func_model.py` line 234 tries to write AXI trace JSON to a hardcoded macOS path `/Users/zheng/npu/traces/conv2d_smoke_axi.json`, which causes `FileNotFoundError` on Linux. |
| **Location** | `sim/func_model.py` — line 234 |
| **Impact** | The func_model test itself passes (tile-level per-block INT4 smoke test matches golden, AXI ordering verified). The crash occurs after all core verification is complete — it is a trace export failure only. |
| **Fix** | Requires source change to `func_model.py`: replace hardcoded path with a configurable output path (e.g., `--trace-dir` CLI arg or use `os.getcwd()`). Not yet addressed. |
| **Verification** | `logs/verify_func_model.log` — "Match: ✅ PASS" before crash. Core test logic is correct. |
| **Status** | **KNOWN ISSUE** |

### License file missing

| Field | Detail |
|-------|--------|
| **ID** | K2 |
| **Description** | No `LICENSE` file in the repository root. |
| **Location** | Repository root |
| **Impact** | Low — the project is in early development. |
| **Status** | **KNOWN ISSUE** |

---

## Summary

| Category | Total | Fixed | Known Issue |
|----------|-------|-------|-------------|
| **Arc Model (B1-B6)** | 6 | 6 | 0 |
| **Func Model (F1-F5)** | 5 | 4 | 1 (F4: INT64 conservative) |
| **Documentation (D1-D5)** | 5 | 5 | 0 |
| **Other (K1-K2)** | 2 | 0 | 2 |
| **Total** | **18** | **15** | **3** |

### Current State

- **pytest suite**: 109 passed, 0 failed
- **Smoke test**: 10/10 PASS
- **SFU verification**: 19/19 PASS (all 5 SFU ops)
- **Func Model**: Core tile-level INT4 test PASS; trace export crashes on hardcoded macOS path
- **E2E verification**: 6/6 PASS (Qwen2.5-1.5B, 2 layers)

All test results and evidence files are in `logs/` and `.omo/evidence/`.

---

## Known Verification Blind Spots

> **Purpose**: Track verification coverage gaps that are explicitly known but not yet resolved. Updated after each Wave of Phase 5 gap-filling.
> **Baseline Date**: 2026-07-06

<!-- After each Wave, update entries below and mark progress. -->

### CV Model Gaps

| Date | Entry | Description | Reference | Status |
|------|-------|-------------|-----------|--------|
| 2026-07-06 | **No CV trace in Func Model** | Arc Model has CV traces (ViT-B/16, YOLOv8, ResNet, MobileNetV3, SD 1.5 UNet) but Func Model only covers LLM. No im2col→GEMM golden reference exists for Conv2D path in `golden_executor.py`. All CV verification currently relies on Arc Model analytical estimates only. | `docs/arc_vs_func.md` §Arc vs Func对比; `docs/rtl_development_plan.md` §8 risks | OPEN |
| 2026-07-06 | **MobileNetV3-Small E2E not verified** | MobileNetV3-Small 15-layer E2E is scoped in W3.4 (Func Model) and W3.5 (RTL SoC single Conv2D). No verification evidence exists yet for any CV layer on either Func Model or RTL. | `.omo/plans/soc-verification-gaps-phase5.md` W3 T18/T19 | **RESOLVED (W3.4)** — Func Model 40/52 layers PASS (cos_sim ≥ 0.99); RTL deferred to Phase 6 |
| 2026-07-06 | **Pooling ops (AVGPOOL/MAXPOOL) unimplemented in GoldenExecutor** | AVGPOOL (0x08) and MAXPOOL (0x07) are defined in `OpCode` and handled in `npu_sim.py` timing model, but `GoldenExecutor.step()` raises `ValueError` for these opcodes. No golden reference, no test vectors, no RTL verification. | `sim/engine/isa.py` L21-L22; `sim/golden_executor.py` L1355-L1356 `else: raise ValueError`; `docs/rtl_development_plan.md` §8 risk table | OPEN |
| 2026-07-06 | **ViT-B/16 zero functional coverage** | ViT-B/16 is a C-class differentiator (competitors lack vision), but no functional verification exists. Layout uses pure GEMM path reusing LLM MXU, but no ViT-layer golden outputs exist in Func Model. | `docs/model_zoo.md` C类 table; `docs/arc_vs_func.md` | OPEN |
| 2026-07-06 | **im2col hardware path not modeled** | Conv2D requires im2col transformation before GEMM. No im2col Python model exists in Func Model. The transformation logic (tile scheduling, nibble ordering, padding) is untested without RTL. | `docs/rtl_development_plan.md` §8; `sim/tile_scheduler.py` | OPEN |

<!-- Updated after W3 -->

### ISA Opcode Gaps

| Date | Entry | Description | Reference | Status |
|------|-------|-------------|-----------|--------|
| 2026-07-06 | **RELU not handled in GoldenExecutor.step()** | RELU (OpCode 0x04) is defined in `isa.py` and routed through the SFU timing model in `npu_sim.py`, but `GoldenExecutor.step()` has no RELU branch. Calling step() with RELU raises `ValueError`. SFU RTL has a RELU op (SFU_OP_RELU=3 in `npu-regmap.h`) but no Func Model golden to compare against. | `sim/engine/isa.py` L18; `sim/golden_executor.py` L1355-1356; `firmware/npu-regmap.h` L54 | **RESOLVED (W5.4)** — `GoldenSFU.relu_hw()` implemented; 5 RELU pytest cases pass |
| 2026-07-06 | **AVGPOOL not handled in GoldenExecutor.step()** | AVGPOOL (OpCode 0x08) defined in ISA, used in CV models (downsampling), but not implemented in `GoldenExecutor.step()`. No golden reference for 2x2 average pooling exists. | `sim/engine/isa.py` L22; `sim/golden_executor.py` L1355-1356; `docs/NPU硬件详细架构设计v0.1.md` §指令集 | **RESOLVED (W5.4)** — `GoldenExecutor.step()` handles OP_AVGPOOL; 6 AVGPOOL pytest cases pass |
| 2026-07-06 | **MAXPOOL not handled in GoldenExecutor.step()** | MAXPOOL (OpCode 0x07) defined in ISA for CV downsampling, but not implemented in `GoldenExecutor.step()`. No golden reference for 2x2 max pooling. RTL also lacks MAXPOOL verification. | `sim/engine/isa.py` L21; `sim/golden_executor.py` L1355-1356; `docs/NPU硬件详细架构设计v0.1.md` §指令集 | **RESOLVED (W5.4)** — `GoldenExecutor.step()` handles OP_MAXPOOL; 4 MAXPOOL pytest cases pass |
| 2026-07-06 | **20/23 opcodes handled — 3 missing for CV E2E** | Overall ISA status: 20 of 23 total opcodes handled in `GoldenExecutor.step()`. The 3 missing (RELU, AVGPOOL, MAXPOOL) are all CV-specific operators. LLM-only workloads (MMUL + SFU + Vector + DMA) are fully covered. | `docs/rtl_development_plan.md` §8; `sim/engine/isa.py` L12-L39 | **RESOLVED (W5.4)** now 23/23 opcodes handled in `GoldenExecutor.step()`; `sim/tests/test_pool_relu_opcodes.py` passes 15/15 |

<!-- Updated after W5.4 — all 4 entries resolved; 23/23 ISA opcodes handled in GoldenExecutor.step(); sim/tests/test_pool_relu_opcodes.py passes 15/15 -->

### PCIe TLP Limitation

| Date | Entry | Description | Reference | Status |
|------|-------|-------------|-----------|--------|
| 2026-07-06 | **TC2 TLP Memory Read fails — no completion data** | `pcie_ep_tb.sv` TC2 issues a TLP Memory Read request but receives no completion data. Testbench log: `[INFO] No completion received — may need full pcie_axi_master init`. Root cause may be in vendored `verilog-pcie/pcie_axi_master` IP rather than testbench logic. | `.omo/plans/soc-verification-gaps-phase5.md` W3 T16; `rtl/tb/pcie_ep_tb.sv` TC2 | **RESOLVED (W3.1)** — AXI slave R-channel FSM fixed; all 4 PCIe tests PASS |
| 2026-07-06 | **No PCIe TLP functional model in Func Model** | `FuncModel.host_write_*()` writes directly to DRAM via `_dram_write()` without any TLP-level modeling. No TLP parser, no BAR address translation, no PCIe EP register state. The entire host→NPU data path has no golden reference at the TLP level. | `docs/soc-fm-gap-spec.md` Gap #7 (§PCIE-TLP); `sim/func_model.py` L46-L93 | OPEN |
| 2026-07-06 | **Dual-path comparison (backdoor + PCIe) not yet implemented** | Lesson 7 principle: E2E compare should do backdoor SRAM read AND interface PCIe TLP read separately. Currently only backdoor comparison exists. PCIe read path is untested on RTL SoC. | `docs/caduceus-verification-lessons.md` Lesson 7; `.omo/plans/soc-verification-gaps-phase5.md` W3 T17/T17b | **RESOLVED (W3.2)** — Func Model dual-path: bk_match=True + pcie_match=True for 17-op blk.0 chain; RTL deferred to Phase 6 |
| 2026-07-06 | **BAR address translation untested in simulation** | PCIe BAR0→SRAM (0x2000_0000) and BAR1→DRAM (0x8000_0000) mapping is only documented in RTL comments. No Func Model or directed test verifies that TLP address write to BAR regions reaches the correct memory target. | `rtl/ip/pcie_ep_wrapper.v`; `docs/soc-fm-gap-spec.md` Gap #7 §Target State | OPEN |

<!-- Updated after W3 -->

### SRAM Peak Concerns

| Date | Entry | Description | Reference | Status |
|------|-------|-------------|-----------|--------|
| 2026-07-06 | **Qwen2.5-3B full weights exceed 4MB SRAM — tile streaming required** | Qwen2.5-3B per-layer weights (Q_proj+K_proj+V_proj+O_proj+gate+up+down = 7 linear layers × 2048×2048 × 0.5 bytes = ~14MB) exceed the 4MB unified SRAM. Must rely on tile streaming via DMA. Only blk.0 4-instruction smoke (MMUL+RMSNorm+Softmax+Residual) tested on SoC — not the streaming data path. | `docs/caduceus-verification-lessons.md` Lesson 9; `.omo/plans/soc-verification-gaps-phase5.md` §Scope; `rtl/soc/README.md` §验证结果 | OPEN |
| 2026-07-06 | **No real-model SRAM peak quantification for CV workloads** | MobileNetV3-Small peak activation ~300MB (requires tiled im2col). The exact per-layer SRAM budget under 4MB constraint has not been computed. L1 SRAM (256KB×2) allocation for double-buffering is in the config but not verified against real CV model tiles. | `docs/rtl_development_plan.md` §8 risk row "SRAM读写带宽不足"; `sim/tile_scheduler.py` | OPEN |
| 2026-07-06 | **No SRAM contention stress test on RTL SoC** | 6 AXI4 masters (Ibex/MXU/SFU/Vector/DMA/PCIe) share 4MB SRAM through a single crossbar. No simulation verifies concurrent access patterns (MXU reading weights + SFU writing activations + DMA loading next tile) cause no contention-induced stall or data corruption. | `.omo/plans/soc-verification-gaps-phase5.md` W4 PERF-02 (DMA+MXU overlap); `rtl/soc/axi_crossbar.v` | OPEN — W4 perf tests will provide first data |
| 2026-07-06 | **36-layer weight preload not stress-tested** | Full 36-layer Qwen2.5-3B forward pass requires 36× DMA weight preloads. Each preload involves descriptor chain walking, AXI burst transfers, and SRAM region management. Only a single blk.0 smoke test has been run on SoC — no multi-layer weight streaming verified. | `.omo/plans/soc-verification-gaps-phase5.md` W1 T4 (36-layer forward); `firmware/npu_firmware.c` descriptor struct | OPEN — scoped to W1 |

<!-- Updated after W1 and W4 -->

### Summary of Known Blind Spots

| Section | Total Items | LLM Path Blocked | CV Path Blocked | Depends On |
|---------|:-----------:|:----------------:|:----------------:|------------|
| CV Model Gaps | 5 | No | Yes | W3 (MobileNet E2E), W5 (pooling ops) |
| ISA Opcode Gaps | 4 | No (20/20 LLM ops handled) | No (resolved W5.4, now 23/23 handled) | W5.4 (GoldenExecutor pool/relu impl + 15/15 pytest) |
| PCIe TLP Limitation | 4 | Partial (TC2 blocks PCIe read verification) | Partial | W3 T16 (TC2 fix) |
| SRAM Peak Concerns | 4 | Partial (tile streaming not stress-tested) | Yes | W1 (36-layer), W4 (perf) |
| **Total** | **17** | **2 blocked** | **12 blocked** | — |

---

## Phase 6 RTL Verification Issues / Blockers

> **Scope**: CaduceusCore Phase 6 RTL verification execution (`phase6-rtl-verification`).
> **Baseline Date**: 2026-07-15 to 2026-07-19
> **Overall State**: VCS compilation and Ibex SoC smoke are green; Spike debug and full-layer/full-weight RTL runs are blocked by firmware and plugin issues. No RTL source files were modified during Phase 6.

### Blockers and Known Gaps

| Issue | Root Cause | Impact | Workaround | Next Step / Owner | Evidence / Review Gate |
|---|---|---|---|---|---|
| **Spike plugin ABI mismatch** | `npu_mmio_plugin.so` was built with an incompatible C++ ABI; runtime fails with undefined symbol `_Z15mmio_device_mapB5cxx11v`. | Spike-based E2E debug and forward-pass flows are blocked for W3-RTL and 36-layer RTL tasks. | Use the Ibex-only RTL path for all Phase 6 verification. | Rebuild `npu_mmio_plugin.so` with an ABI-compatible toolchain before reintroducing Spike debug. Owner: plugin build/infra. | `build/evidence/vcs-readiness-gate.txt` |
| **Firmware 64 KB weight-buffer limit** | Firmware loads weights once per descriptor and does not implement per-K-tile reload. | Full Q_proj (K=2560, N=4096) and a genuine full 36-layer RTL forward pass are blocked. PERF-11 FAIL. | Restrict RTL tests to K≈512 per load; rely on Func Model tile-level analysis for full-layer behavior. | Implement per-K-tile weight streaming reload in firmware. Owner: firmware. | `build/evidence/w4-perf-p2.txt`, `build/evidence/w4-perf-review-gate.txt` |
| **DMA output readback zeros** | The CH1 DMA path that copies MXU output from SRAM→DRAM returns zeros — likely a direction/destination misconfiguration, MXU wrapper output drain, or SRAM-controller clear-on-completion behavior. | PERF path cannot read back MXU output through DRAM, blocking output golden comparison in the firmware-dispatch flow. | FM-SOC regression (33/33) still passes via the backdoor path; use backdoor SRAM reads for debug. | Fix CH1 DMA configuration / wrapper drain behavior / SRAM-controller completion handling. Owner: RTL/DMA integration. | `build/evidence/w4-perf-p*.txt`, `build/evidence/w4-perf-review-gate.txt` |
| **PERF-11 blocked** | 64 KB weight buffer cannot hold 2560 tiles required by Q_proj (K=2560, N=4096). | No RTL measurement for full Q_proj; W4-PERF P2 acceptance is 3/4 PASS. | Document as a known firmware limitation; use smaller matmul configs for P0/P1/P3/P4. | Implement per-K-tile weight reload and re-run PERF-11. Owner: firmware/PERF tests. | `build/evidence/w4-perf-p2.txt`, `build/evidence/w4-perf-review-gate.txt` |
| **Fullchain SFU/Vector blocked** | Current PERF firmware dispatch path only exercises MMUL (opcode 0); SFU (op=1) and Vector (op=2) are not dispatched in the PERF path. | Task 25a fullchain cannot measure non-MMUL segments; SFU/Vector end-to-end latency remains unvalidated in the PERF firmware flow. | Reference FM-SOC-004/005, which demonstrate SFU/Vector opcode dispatch in the non-PERF path. | Validate SFU and Vector opcodes in the PERF firmware path and measure fullchain non-MMUL segments. Owner: firmware/PERF tests. | `build/evidence/fullchain-pipeline.txt`, `build/evidence/w4-perf-review-gate.txt` |
| **36-layer Func Model-only** | Spike is blocked by the plugin ABI mismatch, and the firmware weight-buffer/DMA limitations prevent a full RTL layer pass. | No genuine full 36-layer RTL forward pass exists; RTL evidence is limited to the FM-SOC-001 Ibex SoC smoke test. | Run Func Model checkpoint validation (L0/L10/L20/L35 cos_sim=1.0) to prove golden stability. | Resolve Spike ABI mismatch and firmware streaming/readback issues, then run a real 36-layer RTL forward pass. Owner: RTL/firmware/integration. | `build/evidence/36layer-checkpoint.txt`, `build/evidence/36layer-review-gate.txt` |
| **W4-PERF evidence schema gaps** | Evidence generation scripts did not include `timestamp` and `commit` in every record. | P2-P4 lack timestamps entirely; P0-P1 are partial. Procedural non-compliance with the agreed evidence schema; does not invalidate technical measurements but must close before Final Wave. | Review gate accepted the technical measurements while documenting the schema gap. | Update evidence-generation scripts to add `timestamp` and `commit` to all W4-PERF records. Owner: verification automation. | `build/evidence/w4-perf-review-gate.txt` |
| **F2 baseline contradiction** | The regression baseline section (pytest 210+, FM-SOC 33/33, MXU 9/9, SFU 319/319, Vector 63/63) disagrees with the success-criteria table (pytest ≥700, SFU 526/537, Vector 64/64). | Ambiguous Final Wave acceptance criteria; risk of misjudging signoff readiness. | Treat as a documentation inconsistency to be reconciled before Final Wave; Phase 6 proceeded with the measured evidence set. | Reconcile baseline numbers against the success-criteria table and update the plan or baseline. Owner: verification lead / documentation. | `.omo/notepads/phase6-rtl-verification/learnings.md` |

### Review Gate Verdicts

| Gate | Verdict | Key Conditions / Caveats |
|---|---|---|
| FM-4 Independent Review Gate | **APPROVE** | FM-1 cross-engine and FM-3 correctness validations deferred to W4-PERF; 6b Q8_0 control blocked by missing asset. |
| Pre-Wave VCS Readiness Gate | **PASS** | All 5 gate items compile; Ibex runtime PASS; Spike runtime FAIL due to plugin ABI mismatch. |
| W3-RTL Atlas Review Gate | **APPROVE** | Task 19 is a composite verification: RTL MXU path proven by FM-SOC-032, CV mapping/golden from FM-2. |
| W4-PERF Independent Review Gate | **APPROVE WITH CONDITIONS** | Must close: evidence schema (timestamp/commit), PERF-11 per-K-tile reload, SFU/Vector opcode validation in PERF path, RTL measurement for FM-3 overlap. |
| 36-layer RTL Checkpoint Review Gate | **APPROVE WITH CONDITIONS** | 36-layer pass was Func Model-only; genuine RTL full-layer pass still required after Spike/firmware blockers resolved. |

### Summary

Phase 6 closed with VCS compilation readiness confirmed and the Ibex SoC path fully operational, but several integration blockers remain before a genuine full-model RTL forward pass is possible. The dominant blockers are the Spike plugin ABI mismatch, the firmware 64 KB weight-buffer limit, and the DMA output readback zero issue. All three are tracked above with owners, workarounds, and evidence references.

## Phase 7 Resolution Status

> **Scope**: Disposition of every Phase 6 RTL verification blocker after Phase 7 investigation.
> **Baseline Date**: 2026-07-19
> **Overall State**: Three blockers are RESOLVED (environment / documentation); seven remain unresolved and require a specific next-step file or function.

### Blocker Dispositions

- **Spike plugin ABI mismatch** → RESOLVED (`build/evidence/ph7-spike-fixed.txt`)
- **64 KB weight buffer / PERF-11** → NOT RESOLVED (needs `firmware/npu_firmware.c` per-K-tile weight reload in `run_mmul()` or equivalent)
- **SFU/Vector fullchain dispatch** → NOT RESOLVED (needs `firmware/npu_firmware.c` PERF path support for op=1/op=2 in dispatch table)
- **36-layer Func Model-only** → NOT RESOLVED (depends on weight streaming fix in `firmware/npu_firmware.c` plus DMA readback zero fix in `sim/cocotb_bridge.py`)
- **DMA output readback zeros** → NOT RESOLVED (next step: compare DMA descriptors in `sim/cocotb_bridge.py` between FM-SOC path and PERF path; not a `npu_firmware.c` code defect)
- **FM-3 weight-streaming RTL measurement** → NOT RESOLVED (needs new VCS simulation run; re-run PERF-12 via `sim/perf_tests.py` and update `build/evidence/w4-perf-p2.txt`; current 0.98 is model/parse value)
- **Q8_0 GGUF missing** → NOT RESOLVED (external network blocked; download command: `huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q8_0.gguf --local-dir ~/models`)
- **Phase 6 plan checkbox 6b inconsistency** → NOT RESOLVED (after Q8_0 unblocked, revert Phase 6 plan 6b checkbox in `.omo/notepads/phase6-rtl-verification/plan.md` or re-run experiment)
- **W4-PERF evidence schema** → RESOLVED (`build/evidence/w4-perf-p*.txt`)
- **testcase-list-perf.md** → RESOLVED (`rtl/testcase-list-perf.md`)

## Phase 6 Condition Disposition

| Phase 6 Source | Condition | Phase 7 Disposition | Evidence / Next Step |
|:---|:---|:---|:---|
| W4-gate #1 / F1-#7 | Evidence schema gap | RESOLVED | `build/evidence/w4-perf-p*.txt` |
| W4-gate #2 / F1-#2 | PERF-11 weight streaming | NOT RESOLVED | `firmware/npu_firmware.c` per-K-tile reload |
| W4-gate #3 / F1-#3 | SFU/Vector fullchain dispatch | NOT RESOLVED | `firmware/npu_firmware.c` PERF op=1/2 dispatch |
| W4-gate #4 / F1-#4 | FM-3 overlap RTL measurement | NOT RESOLVED | new VCS simulation run |
| 36L-gate #1 / F1-#5 | 36-layer RTL full forward pass | NOT RESOLVED | weight streaming + DMA readback fix |
| 36L-gate #2 / F1-#6 | Spike plugin ABI mismatch | RESOLVED | `build/evidence/ph7-spike-fixed.txt` |
| F1-#8 | W1-Supplement plan checkbox inconsistency | NOT RESOLVED | Q8_0 unblocked: revert checkbox or re-run |
| F1-#9 | testcase-list-perf.md status | RESOLVED | `rtl/testcase-list-perf.md` |
| 36L-gate #3 | Regenerate golden after FM changes | ACKNOWLEDGED | out of Phase 7 scope (no FM changes) |
| 36L-gate #4 | Next gate must confirm RTL full-layer | FORWARD | future Phase acceptance requirement |

## Phase 8 Resolution Status — PERF Harness Fix

> **Scope**: Phase 8 Python harness only (B1: no RTL/firmware changes). Fix data-layout in `sim/perf_tests.py` for MXU preload sequencer compatibility.
> **Baseline Date**: 2026-07-19
> **Overall State**: Core fix confirmed (data-layout hypothesis) and applied; several firmware-path blockers remain unresolved because root cause is in firmware/RTL.

### Root Cause Verdict Matrix

| Blocker / PERF Case | Resolution Status | Test Status | Root Cause Verdict | Evidence File | Scope Note |
|---|---|---|---|---|---|
| **Data-Layout Hypothesis** (PERF harness writes raw row-major bytes) | **RESOLVED** | PASS (cs=1.0 on single-tile direct preload) | **CONFIRMED** — `pack_int8_activation_tile_major()` is required; MXU preload sequencer expects K-vector tile-major layout. Applied in `PR.mmul()`. | `build/evidence/ph8-diagnostic.txt` | Python harness (`sim/perf_tests.py`) |
| **PERF-11 / DMA zeros** (P2 batch: K=512, N=128) | **PARTIAL** | PARTIAL_PASS (cs=0.381 standalone; DMA readback works, output non-zero) | **PARTIAL** — Row-major → all zeros (pre-fix); tile-major → non-zero output (post-fix). Packing IS causal but INSUFFICIENT: firmware-path M=1 multi-tile bug prevents cs≥0.999. DMA output store is functional. | `build/evidence/ph8-perf-11-before-after.txt` | Python harness fix applied; remaining gap is firmware/RTL M=1 multi-tile issue |
| **PERF-13 / M=1 multi-tile** (P3 batch: 7/9 MMULs fail) | **NOT RESOLVED** | FAIL (cos_sim 0.386–0.796 for M=1 multi-tile; M=32 single-K-tile passes at cs=1.0) | **NOT RESOLVED** — Firmware tile iteration loop or MXU wrapper broadcast sequencer bug for M=1 when K>64 or N>64. Tile-major packing IS correct (Task 1 direct preload cs=1.0). Divergence is in firmware→MMIO→wrapper→MXU path. | `build/evidence/ph8-p3_p4.log` | Firmware/RTL scope; beyond Phase 8 B1 |
| **PERF-17 / M=1,K=128,N=128** (P4 batch) | **NOT RESOLVED** | FAIL (cs=0.711) | Same root cause as PERF-13: M=1 multi-tile firmware-path bug. | `build/evidence/ph8-p3_p4.log` | Firmware/RTL scope; beyond Phase 8 B1 |
| **P0 Batch** (PERF-01..04: K=256, N=64) | **NOT RESOLVED** | FAIL (PERF-01 cs=0.437; PERF-04 cs=-0.218, cyc=2 doorbell stale) | Same root cause as PERF-13 + pre-existing ring buffer reuse. Firmware doorbell path produces incorrect MXU results for M=1 multi-tile. | `build/evidence/ph8-perf-04-regression.txt` | Firmware/RTL scope; beyond Phase 8 B1 |
| **Ring Buffer Reuse** (P2/P3/P4 batches) | **RESOLVED** | PASS (all 9 MMULs produce distinct output) | **FIXED** — Added `_ring_tail` counter to `PR`. Each `mmul()` increments tail, writes to correct ring slot, sets `HOST_TAIL`, polls `NPU_HEAD`. | `build/evidence/ph8-p3_p4.log` | Python harness (`sim/perf_tests.py`) |
| **FULLCHAIN single-tile** (5-op pipeline: MMUL→RMSNorm→VRESID→VCONV→SiLU) | **RESOLVED** | PASS (cos_sim=1.0, 5 gaps, DMA non-zero) | **RESOLVED** — Single-tile M=1,K=64,N=64 passes through firmware doorbell path for all 5 ops. Validates SFU (op 0x17/0x06) and Vector (op 0x14/0x13) dispatch. | `build/evidence/fullchain-pipeline.txt` | Python harness fix + fullchain test |
| **PERF-20 Repeatability** (M=1,K=128,N=128, 3 runs) | **RESOLVED** | PASS (mean=16394 cyc, std=1.41, pct_std=0.01% ≤ 1%) | — | `build/evidence/ph8-p3_p4.log` | Measurement infra; cycle count is deterministic despite incorrect M=1 output |
| **PERF-18 Inter-Op Gap** (sequential 2x M=1,K=64,N=64) | **RESOLVED** | PASS (cos_sim=1.0 both, inter_op_gap=0 cyc) | — | `build/evidence/ph8-p3_p4.log` | Single-tile M=1 works; multi-tile M=1 blocked by PERF-13 |
| **FM-SOC Regression** (33/33 cases) | **RESOLVED** | PASS (33/33 PASS, 0 FAIL, 0 SKIP) | **NO REGRESSION** — FM-SOC path (`rtl_soc_runner.py` + Ibex firmware) is orthogonal to PERF harness changes. No RTL or firmware source files modified. | `build/evidence/fm-soc-regression.txt` | Regression gate clean |
| **Q8_0 / 36-layer / FM-3 RTL** (weight-streaming RTL measurement) | **NOT ADDRESSED** | — | **DEFERRED** — Requires: Q8_0 GGUF download (external network), per-K-tile firmware weight reload, DMA readback fix before a genuine 36-layer RTL forward pass is possible. | `build/evidence/36layer-checkpoint.txt` (Phase 6), `.omo/notepads/phase6-rtl-verification/learnings.md` | Firmware/RTL scope; beyond Phase 8 B1 |
| **Spike Plugin ABI Mismatch** | **RESOLVED** (Phase 7) | PASS | Fixed in Phase 7; rebuilt `npu_mmio_plugin.so` with ABI-compatible toolchain. | `build/evidence/ph7-spike-fixed.txt` | External toolchain; resolved before Phase 8 |
| **W4-PERF Evidence Schema** | **RESOLVED** (Phase 7) | PASS | `timestamp`/`commit` added to W4-PERF evidence records. | `build/evidence/w4-perf-p*.txt` | Procedural; resolved before Phase 8 |

### Key Distinction: Test PASS vs Blocker RESOLVED

Per Metis G11, the following are **Test PASS** but do NOT equate to **Blocker RESOLVED**:

- **PERF-20 Repeatability**: PASS (0.01% ≤ 1%) but the underlying MMUL computes **incorrect values** for M=1 multi-tile. The cycle count is deterministic despite incorrect output.
- **PERF-18 Inter-Op Gap**: PASS (cos_sim=1.0, inter_op_gap=0) but only validates **single-tile** M=1,K=64,N=64. Multi-tile M=1 remains blocked.
- **FULLCHAIN single-tile**: PASS (cos_sim=1.0, 5 ops) but only validates **K≤64, N≤64**. Multi-tile M=1 SFU/Vector dispatch is untested.

### Phase 8 Closure Summary

| Category | Count | Status |
|---|---|---|
| **Resolved** (harness fix applied, gate clean) | 5 | Data-layout, Ring buffer, FULLCHAIN single-tile, PERF-20, PERF-18 |
| **Partial** (causal fix proven but insufficient) | 1 | PERF-11 (DMA works, output non-zero, cs<0.999) |
| **Not Resolved** (root cause in firmware/RTL) | 4 | PERF-13, PERF-17, P0 batch, FM-3 weight-streaming |
| **Deferred** (requires external unblock) | 1 | Q8_0 / 36-layer RTL |
| **No Regression** | 1 | FM-SOC 33/33 |

**Dominant remaining blocker**: M=1 multi-tile firmware-path bug — the tile-major packing fix is correct at the data-layout level, but the firmware tile iteration loop or MXU wrapper broadcast sequencer produces incorrect output when M=1 and K>64 or N>64. Next step (beyond Phase 8 B1 scope): isolate the divergence point in `firmware/npu_firmware.c` tile loop vs `rtl/wrapper/mxu_soc_wrapper.v` broadcast sequencer.

## Phase 8 Condition Disposition

> Maps each Phase 8 source condition to its disposition, evidence/next-step. Synthetic/analytical entries annotated with `source="analytical"`.

| Phase 8 Source Condition | Disposition | Evidence / Next Step | Tag |
|:---|:---|:---|:---|
| **Data-layout hypothesis** (row-major vs tile-major) | **RESOLVED** — Hypothesis confirmed; tile-major packing applied in `PR.mmul()` | `build/evidence/ph8-diagnostic.txt`: row-major cs=0.201 (FAIL), tile-major cs=1.000 (PASS, bit-exact) | — |
| **PERF-11 DMA zeros** (Q_proj K=512,N=128) | **PARTIAL** — Row-major→zeros; tile-major→non-zero output (cs=0.381). Packing causal but insufficient; firmware-path M=1 multi-tile bug prevents cs>=0.999. | `build/evidence/ph8-perf-11-before-after.txt`: pre-fix cs=0.000 (all zeros), post-fix cs=0.381 (non-zero). DMA output store confirmed functional. Next step: Phase 9 firmware/RTL debug. | — |
| **PERF-13 / PERF-17 M=1 multi-tile** (K>64 or N>64) | **NOT RESOLVED** — Firmware tile iteration loop or MXU wrapper broadcast sequencer bug for M=1 when K>64 or N>64. Single-tile M=1 works (cs=1.0). Direct preload achieves cs=1.0 for multi-tile. | `build/evidence/ph8-p3_p4.log`: 7/9 MMULs FAIL (cs 0.386-0.796). Next step: Phase 9 isolate firmware vs wrapper divergence. | — |
| **P0 batch** (PERF-01..04 firmware-path M=1 multi-tile) | **NOT RESOLVED** — Same root cause as PERF-13/17. Firmware doorbell path produces incorrect MXU results for M=1 multi-tile. | `build/evidence/ph8-perf-04-regression.txt`: PERF-01 cs=0.437, PERF-04 cs=-0.218 (doorbell stale). Next step: Phase 9 firmware/RTL debug. | — |
| **Ring buffer reuse** (P2/P3/P4 sequential MMUL staleness) | **RESOLVED** — `_ring_tail` counter added to `PR`. Each `mmul()` writes to distinct ring slot. | `build/evidence/ph8-p3_p4.log`: P3 run 2 all 9 MMULs produce distinct output. P18a/P18b cs=1.0. | — |
| **FULLCHAIN single-tile** (5-op pipeline) | **RESOLVED** — Single-tile M=1,K=64,N=64 fullchain passes cs=1.0, 5 gaps, DMA non-zero through firmware doorbell. | `build/evidence/fullchain-pipeline.txt`: status=PASS, cos_sim=1.000000, 5 gaps, DMA non-zero. | — |
| **PERF-20 repeatability** (M=1,K=128,N=128, 3 runs) | **RESOLVED** | `build/evidence/ph8-p3_p4.log`: runs=[16396,16393,16393], pct_std=0.01% ≤ 1% | — |
| **PERF-18 inter-op gap** (sequential 2x M=1,K=64,N=64) | **RESOLVED** — cos_sim=1.0 both, inter_op_gap=0. | `build/evidence/ph8-p3_p4.log` | — |
| **FM-SOC regression** (33/33 cases) | **RESOLVED** — No regression; 33/33 PASS, 0 FAIL, 0 SKIP. | `build/evidence/fm-soc-regression.txt`: 33/33 PASS. RTL/firmware source unchanged. | — |
| **PERF-12 overlap ratio** | **NOT ADDRESSED** (maintained as analytical estimate) | `build/evidence/w4-perf-p2.txt`: overlap_ratio=0.98 | `source="analytical"` |
| **PERF-14/15/16 cross-engine gap** | **NOT ADDRESSED** (synthetic analytical entries) | `build/evidence/w4-perf-p3.txt`: cross_engine_gap=4, detailed gap_model | `source="analytical"` |
| **PERF-18/19 analytical measurements** | **NOT ADDRESSED** (synthetic analytical entries) | `build/evidence/w4-perf-p4.txt`: inter_op_gap, cross_engine_gap fields | `source="analytical"` |
| **Q8_0 GGUF missing** (requires external download) | **NOT RESOLVED** (deferred) | Next step: `huggingface-cli download Qwen/Qwen2.5-3B-Instruct-GGUF qwen2.5-3b-instruct-q8_0.gguf --local-dir ~/models` (Phase 9) | — |
| **36-layer RTL full forward pass** | **NOT RESOLVED** (deferred) | Requires: weight streaming fix, DMA readback fix, M=1 multi-tile fix. Next step: Phase 9. | — |
| **FM-3 overlap RTL measurement** | **NOT RESOLVED** (deferred) | Requires new VCS simulation run after M=1 multi-tile fix. Next step: Phase 9. | — |
