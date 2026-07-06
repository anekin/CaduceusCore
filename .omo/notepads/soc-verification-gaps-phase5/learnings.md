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
