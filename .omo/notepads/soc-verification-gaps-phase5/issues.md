# Issues Log — CaduceusCore SoC Verification Phase 5

## [2026-07-06T06:36Z] ISSUE: RISC-V toolchain missing on sz0001 (Pre-Wave 0.1)

- **Severity**: BLOCKER
- **Check**: Pre-Wave 0.1 Check 3 (Firmware Build)
- **Symptom**: `make -C firmware` fails with `/usr/bin/riscv64-unknown-elf-gcc: Command not found` (exit 127)
- **Root cause**: The `riscv64-unknown-elf-` GNU cross-compiler toolchain for RV32IM is not installed on sz0001.

### Search Results (all negative)
| Location | Result |
|----------|--------|
| `/usr/bin/riscv64-unknown-elf-gcc` | NOT FOUND |
| `/NAS/Tools/` (recursive) | NOT FOUND |
| `/opt/` (recursive) | NOT FOUND |
| `/usr/local/` (recursive) | NOT FOUND |
| Conda env py3.11 (`/NAS/Tools/anaconda3/envs/py3.11/bin/`) | NOT FOUND |
| Environment Modules (`module avail \| grep riscv`) | No riscv module |
| System GCC | x86_64-redhat-linux (not RISC-V) |

### Impact
- Firmware cannot be built → blocks all Spike-based E2E forward pass tests
- Blocks any simulation requiring boot ROM firmware hex (`firmware/build/npu_firmware.hex`)
- Blocks any SoC-level RTL simulation with the Ibex RISC-V core

### Resolution Options
1. **Install RISC-V GNU toolchain on sz0001** (recommended): `dnf install riscv64-unknown-elf-gcc` or build from source
2. **Use TOOLCHAIN_DIR override**: Install toolchain elsewhere and set `TOOLCHAIN_DIR=/path/to/toolchain` before `make -C firmware`
3. **Cross-compile on sz0002**: Build firmware on sz0002 (which may have the toolchain) and copy hex to sz0001

## [2026-07-06T18:45Z] UPDATE: W1.3 full-chain redo in progress on sz0001

- **Severity**: BLOCKER (until evidence is produced)
- **Task**: W1.3 SoC testbench: proper 3-layer 17-op forward pass on sz0001
- **Status**: IN PROGRESS
- **Changes made**:
  - Rewrote `scripts/gen_qwen25_3b_rtl_vectors.py` to emit a 51-op manifest (17 ops × 3 layers) using real Qwen2.5-3B INT4-per-block weights and W1.2 Func Model hidden states.
  - Added per-op FP32 references to `rtl/test_vectors/soc_e2e/qwen25-3b-3layer-rtl/expected.npz` for MMUL golden comparison.
  - Rewrote `sim/cocotb_bridge.py:test_qwen25_3b_3layer` to drive all 51 ops (MMUL/SFU/Vector) instead of only VRESID.
  - Added `sim/cocotb_bridge.py:_run_streamed_mmul` to stream large K-dim MMULs through the MXU wrapper in 128-element K-blocks + 64-wide N-tiles, because the wrapper buffers only hold two K-tiles.
  - Large transformer weights (gate/up/down ~11 MB packed) are read tile-by-tile from disk instead of preloading full weights into the 4 MB SRAM / 8 MB simulated DRAM.
  - Updated `scripts/run_qwen25_3b_rtl.py` to compare final INT32 VRESID outputs against rounded W1.2 golden using cos_sim >= 0.999 and max_abs_err_vs_rounded <= 1.0.
  - Updated `sim/regression/Makefile` target comment to reflect full-chain intent.
- **Runtime issues found and fixed**:
  - `_run_streamed_mmul` initially used `np.asarray(bytes, dtype=np.uint8)` on backdoor-read bytes, which failed under cocotb; changed to `np.frombuffer(bytes(...), dtype=np.uint8)`.
  - Full-weight preload overflowed SRAM for gate/up/down; switched to on-demand tile read from the weight hex file.
  - Test preloaded activation in tile-major format, but `_run_streamed_mmul` expects dense row-major at `i_addr`; switched to raw dense preload.
- **Current run**: VCS simulation executing on sz0001 (PID 109217). Progress: op12_up (layer 0 up projection, K=2048, N=11008) at ~7.3 ms simulation time. Expected runtime: tens of minutes due to 2752 partial tiles per large MMUL.
- **Evidence pending**: `build/wave1/w1-3-rtl-layer-outputs.npz`, `build/wave1/w1-3-rtl-op-summary.json`, `build/evidence/w1-3-rtl-3layer.txt`.
- **Known RTL gap**: Even if all 51 ops pass individually, the chain is currently an op-by-op regression (each op's input is preloaded from hex), not a true end-to-end forward pass where each op's output feeds the next. Inter-op dtype conversion (INT32→FP16/INT8) and quantization-scale bookkeeping are not implemented in RTL, so true numerical propagation cannot be verified without RTL/datapath changes.

## [2026-07-06T17:05Z] ISSUE: W1.3 implementation mismatch — VRESID replay substituted for full 3-layer forward pass

- **Severity**: BLOCKER (blocks W1.4 and W1.5)
- **Task**: W1.3 SoC testbench: 3-layer forward pass on sz0001
- **Symptom**: Evidence file `build/evidence/w1-3-rtl-3layer.txt` reports `TESTS=3 PASS=3`, but:
  - Layer 0 cos_sim=0.891900, Layer 1 cos_sim=0.990331, Layer 2 cos_sim=0.999985 (required: ≥ 0.999)
  - Only Layer 2 meets plan acceptance threshold
  - The implemented test only exercises `VECTOR_RESID` ops, not the full 17-op per-layer chain
- **Root cause**: The subagent simplified W1.3 to a residual-add replay (`layer_output = prev_hidden + delta`) using W1.2 golden hidden states, instead of driving the full INT4×INT8 transformer layer (MMUL, SFU, Vector) on RTL.
- **Impact**: W1.3 does not verify the actual multi-layer forward pass on RTL; W1.4 cannot be scaled from this infrastructure.
- **Resolution**: Redo W1.3 with proper full-layer replay:
  - Generate per-op vectors for the 17-op blk.0 chain (or reuse FM-SOC-027 structure)
  - Drive each op through the RTL SoC (MXU/SFU/Vector/DMA)
  - Compare per-layer hidden states against W1.2 Func Model golden with cos_sim ≥ 0.999
  - If any layer fails threshold, root-cause RTL/TB bug within INT4×INT8 scope and fix
- **Evidence**: `build/evidence/w1-3-rtl-3layer.txt`, `scripts/run_qwen25_3b_rtl.py`, `scripts/gen_qwen25_3b_rtl_vectors.py`, `sim/cocotb_bridge.py:test_qwen25_3b_3layer`

### Related Checks (PASS)
- VCS: V-2023.12-SP2_Full64 ✅
- cocotb: 1.9.0 ✅
- Spike: spike_src/build/spike (1.1.1-dev) ✅

## [2026-07-06T14:40Z] UPDATE: Workaround identified for RISC-V toolchain gap

The `riscv64-unknown-elf-gcc` toolchain is available on sz0002 at `/usr/bin/riscv64-unknown-elf-gcc` (gcc-10.2.0, full suite with objcopy/objdump/etc). The firmware build succeeds on sz0002, and the resulting hex files are NFS-visible from both machines (same path: `/home/prj/zhengs/caduceuscore/CaduceusCore/firmware/build/`).

### Current Status
- **Severity**: Downgraded to WARNING (workaround available)
- **Firmware hex**: Produced at `firmware/build/npu_firmware.hex` (710 lines, 6390 bytes)
- **Path**: `/home/prj/zhengs/caduceuscore/CaduceusCore/firmware/build/npu_firmware.hex`
- **Workaround**: Build firmware on sz0002 before running E2E tests on sz0001

### Permanent Fix Still Needed
- Install `riscv64-unknown-elf-gcc` on sz0001 to enable self-contained firmware builds

## [2026-07-06T14:45Z] RESOLVED: Firmware hex "all zeros" — test expectation bug, not build issue

- **Severity**: RESOLVED (was BLOCKER, downgraded to WARNING after diagnosis)
- **Check**: `sim/tests/test_soc_fm.py::test_boot_rom_loading`
- **Symptom**: Test assertion `first_word != 0` at address 0x0 failed
- **Root cause**: The firmware hex IS valid — the linker script (`firmware/link.ld`) reserves 0x00-0x7f for a vectored trap table (FILL(0x00)). Ibex reset vector is at `boot_addr + 0x80`. The test checked the wrong offset.
- **Fix**: Updated test to check 0x0 for zero trap table and 0x80 for non-zero `_start` instructions. Test now passes.
- **No firmware rebuild needed**: The original hex with 710 lines (6390 bytes) is correct and contains valid RISC-V code starting at offset 0x80.

## [2026-07-07] FORMALIZED: W1.3 3-layer bugs recorded in bug tracker

- **Action**: Wave 1 (W1.3) findings from `docs/vector-workaround-3layer-issue.md` and `build/wave1/w1-3-rtl-op-summary.json` have been formally recorded.
- **BUG-RTL-SOC-005**: Status changed to "Re-opened / Must fix in Phase 5". 3-Layer re-exposure subsection documents op14/31/48 VMUL gate\*up failures (workaround broke at 51-op scale).
- **BUG-RTL-SOC-007**: New entry for attn_weight dispatch failures (op07/24/41, cycles=0). Status: Open, under investigation.
- **Source bug file**: `docs/bugs/bugs-soc-rtl.md`
