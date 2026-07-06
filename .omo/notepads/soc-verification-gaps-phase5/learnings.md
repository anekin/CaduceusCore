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
