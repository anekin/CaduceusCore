# CaduceusCore SoC Verification Gaps — Phase 5 Learnings

## [2026-07-06T06:36:52Z] Task 0.1: env partially verified

Pre-Wave 0.1 EDA environment readiness check executed on sz0001.

### Checks Passed (3/4)
- **VCS**: V-2023.12-SP2_Full64 — confirmed and functional
- **cocotb**: 1.9.0 — confirmed
- **Spike**: spike_src/build/spike (1.1.1-dev) — binary invokes cleanly

### Check Failed (1/4)
- **Firmware build**: FAILED — `riscv64-unknown-elf-gcc` not installed on sz0001. See `.omo/notepads/soc-verification-gaps-phase5/issues.md`.

### Environment Details
- `sim/regression/run_env.sh` sources correctly: VCS, Python 3.11.9, cocotb all load without error
- License override `SNPSLMD_LICENSE_FILE=27020@sz0001` prevents lmstat timeout stalls
- Spike binary at `spike_src/build/spike` is pre-built with CaduceusCore patches

### Blocked By
- RISC-V GNU toolchain (RV32IM) not installed on sz0001
- This blocks firmware build (`make -C firmware`) and all downstream Spike E2E tests

## [2026-07-06T14:40Z] Task 0.1: env fully verified (with workaround)

All four Pre-Wave 0.1 checks pass.

### Resolution
- Firmware built successfully on sz0002 where `riscv64-unknown-elf-gcc` (gcc-10.2.0) is at `/usr/bin/`
- Hex output (`firmware/build/npu_firmware.hex`, 710 lines) is NFS-visible from both machines
- Cross-compile gap noted in issues.md; workaround sufficient to unblock Wave 0 RTL todos

### Gate Status
Pre-Wave 0.1 is CLEAR. RTL verification wave can proceed with the following aware:
- `make -C firmware` must run on sz0002 (or after installing toolchain on sz0001)
- All other EDA tools (VCS, cocotb, Spike) run directly on sz0001

## [2026-07-06T15:00Z] W5.4: AVGPOOL/MAXPOOL/RELU opcode handling complete

23/23 ISA opcodes are now handled in `GoldenExecutor.step()`. The 3 previously
missing opcodes (RELU=0x04, MAXPOOL=0x07, AVGPOOL=0x08) are implemented.

### Implementation Summary
- **RELU**: `GoldenSFU.relu_hw()` — element-wise `max(x, 0)` with FP16 subnormal
  flushing on the SFU datapath. Reads FP16 from SRAM at `sa`, writes to `da`.
  1 cycle per 128 elements (zero-overhead comparator, no LUT).
- **MAXPOOL**: 2×2 max-pool with stride 2. Reads FP16 tensor of shape (H,W)
  from SRAM at `sa`, writes (H/2, W/2) FP16 result to `da`.
- **AVGPOOL**: 2×2 average-pool with stride 2. Same layout as MAXPOOL.
  `np.mean()` per window, FP16 write-back.

### Test Coverage
- `sim/tests/test_pool_relu_opcodes.py`: 15 pytest cases (5 RELU, 4 MAXPOOL,
  6 AVGPOOL) with synthetic tensors (4×4, 6×6, random 128-element vectors).
- FP16 precision accounted for: expected values computed from FP16-quantized
  inputs, tolerances of ~2e-3.
- Regression: 15/15 new tests PASS; 676/676 pre-existing tests still PASS;
  8 pre-existing failures in test_engines/test_soc_fm unchanged.

### SRAM Layout Note
- Pooling ops read from `sa` (typically activation buffer 0x200000) and write
  to `da` (typically SFU I/O buffer 0x2C0000 or vector_io 0x300000).
- Operand sizes: total = H × W FP16 elements (2 bytes each); output =
  (H/2) × (W/2) FP16 elements.

## [2026-07-06T15:30Z] W5.6: Per-Wave Review Gate checklist established; OMO/Atlas readiness verified

Created the reusable Review Gate checklist at `.omo/templates/review-gate-checklist.md`
and verified the local OMO/Atlas environment.

### Checklist Contents
Five gates with explicit PASS/FAIL criteria:
1. **SUMMARY consistency check** — plan, commits, notepad, evidence, and logs align.
2. **FAIL→bug mapping** — every FAIL links to a tracked bug with severity/owner.
3. **Anti-vacuous verification** — tests exercise real paths, assertions active, baselines justified.
4. **Regression baseline check** — prior suite passes, new tests in manifest, metrics stable.
5. **Known gaps update** — unresolved gaps listed with owner and target date.

### Atlas / Final Wave
- Atlas is invoked as an OMO background agent: `task(subagent_type="atlas", ...)`.
- Accepted verdicts: `APPROVE`, `CONDITIONAL`, `REJECT`.
- Oracle fallback documented if OMO < 4.14 or Atlas unavailable.

### OMO Readiness Verification
- `opencode plugin list` executed; output archived in `build/evidence/omo-atlas-readiness.txt`.
- Installed `oh-my-openagent` version: **4.15.1** (>= 4.14) — PASS.
- Atlas agent is defined in `~/.config/opencode/oh-my-openagent.json` — PASS.
- No standalone `atlas` binary exists in PATH; this is expected because Atlas is an OMO subagent.

### Evidence
- `.omo/templates/review-gate-checklist.md`
- `build/evidence/omo-atlas-readiness.txt`

## [2026-07-06] W5.3-followup: caduceus-verification-lessons.md committed to git

Brought the untracked file `docs/caduceus-verification-lessons.md` into version
control — it was referenced by the plan and the W5.3 audit but never committed.
Commit `e121a96` adds the 205-line document with all 14 lessons and appendices.

## Pre-Wave 0.2 Fix: Correct build/ path resolution to repo root (2026-07-06)

**Root cause:** The initial `/tmp` → `build/` migration introduced path resolution bugs in two scripts:
- `run_batch_regression.py` used `REPO_ROOT = Path(__file__).resolve().parent.parent.parent` (3 levels up = `/home/prj/zhengs/caduceuscore/`), with `CaduceusCore/` prefixes on all sub-paths. Build artifacts landed at `/home/prj/zhengs/caduceuscore/build/` instead of the repo's `build/`.
- `run_mxu_perf_case.py` used `REPO_ROOT = CADUCEUS_CORE.parent`, same effect.

**Fix:**
- `run_batch_regression.py`: Changed `REPO_ROOT` to 2 levels up (`parent.parent` = CaduceusCore dir), removed `CaduceusCore/` prefixes from `SFU_ROOT`, `VECTOR_ROOT`, `RESULTS_DIR`, and `compile_simv()` VCS command paths.
- `run_mxu_perf_case.py`: Changed `REPO_ROOT = CADUCEUS_CORE.parent` to `REPO_ROOT = CADUCEUS_CORE`, removed `CaduceusCore/` prefix from VCS compile command RTL paths.

**Verification:** All `build/`, `.omo/`, and `rtl/` paths now start with `/home/prj/zhengs/caduceuscore/CaduceusCore/`. py_compile passes on both scripts.

## [2026-07-06T14:45Z] Task 0.1-fix: firmware hex non-zero issue resolved

### Diagnosis
The `test_boot_rom_loading` test failed because it checked for non-zero code at address 0x0, but the linker script (`firmware/link.ld`) intentionally zero-pads addresses 0x00-0x7f for the RISC-V vectored trap table. The actual firmware entry point `_start` begins at offset 0x80 (Ibex reset vector = boot_addr + 0x80).

### Root Cause
- **Not a firmware build bug**: The hex file correctly contains non-zero instructions at offset 0x80 (`00010297` = `auipc t0,0x10`, the first instruction of `_start`)
- **Test expectation mismatch**: The test read at `0x00000000` expecting non-zero, but the correct checks are at `0x00000000` (zero for trap table) and `0x00000080` (non-zero for `_start`)

### Fix
- Updated `sim/tests/test_soc_fm.py::test_boot_rom_loading`: now checks address 0x0 for zero trap table and address 0x80 for non-zero firmware instructions
- Test passes: `1 passed in 0.31s`

### Files Changed
- `sim/tests/test_soc_fm.py` — updated `test_boot_rom_loading` assertion
- `build/evidence/firmware-hex-fix.txt` — evidence with hex words and disassembly

### Verification
- First 8 hex words at 0x80: `00010297 f8028293 00010317 f7830313 00001397 a8838393 0062dc63 0003ae03`
- Matches `_start` disassembly exactly

## [2026-07-06] W1.1: Qwen2.5-3B 36-layer forward test spec created

Created `docs/qwen25-3b-forward-spec.md` defining the full 36-layer forward pass
test specification.

### Key findings
- Canonical Qwen2.5-3B parameters: hidden=2048, intermediate=11008, heads=16,
  kv_heads=16, 36 layers, vocab_size=151936. Note: the codebase
  `blk0_manifest.json` uses hidden=2560, intermediate=9728, heads=32, kv_heads=2
  which matches Qwen2.5-7B not 3B — this is a known artifact from early
  prototyping.
- Per-layer 17-op chain (building block FM-SOC-027): 9 MMUL + 5 SFU + 3 Vector,
  21,712 tiles per layer, 781,632 tiles across all 36 layers.
- Golden reference path: `rtl/test_vectors/soc_e2e/qwen25-3b-36layer/expected.npz`
  (does not exist yet; must be generated).
- Tolerance: cos_sim >= 0.999 per layer, max_rel_err <= 1e-4 per op.
  End-to-end (layer 35) tolerance relaxed to cos_sim >= 0.995 and
  max_rel_err <= 1e-2 to account for FP16 accumulation across 36 layers.
- Each layer's 7 weight tensors total ~49 MB packed (INT4 + FP32 scales),
  confirming tile streaming via DMA is mandatory (4 MB SRAM).
- Two new FM-SOC cases proposed: FM-SOC-036 (36-layer forward) and FM-SOC-037
  (36-layer anti-vacuous with corrupted layer 17 weight).
