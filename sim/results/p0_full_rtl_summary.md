# P0 Full-RTL SoC Regression Summary — Engine RTL + Spike CPU

**Date:** 2026-07-04
**Environment:** EDA server sz0001 (192.168.0.11), Synopsys VCS V-2023.12-SP2
**SoC variant:** `caduceus_soc_spike_top.v` (Ibex-free, Spike-driven AXI4/APB masters)
**Regression script:** `sim/regression/run_p0_full_rtl.sh`

## Result

| Case | Description | RTL Status |
|------|-------------|:----------:|
| FM-SOC-001 | DMA SRAM-to-SRAM transfer | PASS |
| FM-SOC-002 | DMA DRAM-to-SRAM transfer | PASS |
| FM-SOC-003 | MXU INT4×INT8→INT32 matmul vs GoldenMXU | PASS |
| FM-SOC-004 | SFU RMSNorm FP16 vs GoldenSFU | PASS |
| FM-SOC-005 | Vector INT32 ADD vs GoldenVector | PASS |
| FM-SOC-006 | DMA→Vector two-command chain | PASS |
| FM-SOC-007 | Anti-vacuous: corrupted MXU weight → mismatch | PASS |
| FM-SOC-008 | Anti-vacuous: corrupted SFU input → mismatch | PASS |

**P0 PASS rate: 8/8 (100%)**

## Key Infrastructure

- `rtl/soc/caduceus_soc_spike_top.v` — Ibex-free SoC top exposing 32-bit AXI4/APB master ports.
- `rtl/tb/tb_soc_spike.v` — cocotb testbench with backdoor SRAM/DRAM preload interfaces.
- `sim/spike_rtl_bridge.py` — threaded Unix-socket server + `SimpleAPBMaster` + `RTLMMIOBridge` using `@cocotb.function`.
- `sim/rtl_soc_runner.py` — `P0SpikeRunner` class and `test_soc_spike_p0` cocotb entry point.
- `sim/regression/run_p0_full_rtl.sh` — compile-once / run-sequential wrapper.
- `rtl/ip/dram_model.v` — enlarged to 16 MB sparse to accommodate P0 descriptor/output regions.

## Fixes Applied

1. Rebuilt `spike_src/build/spike` from source with devtoolset-9 so it runs on CentOS 7 glibc 2.17.
2. Added `LD_LIBRARY_PATH` to `sim/regression/run_env.sh` for the Anaconda py3.11 lib directory.

## Notes

- No RTL source files under `rtl/mxu/`, `rtl/sfu/`, or `rtl/vector/` were modified.
- Golden references (`sim/golden_executor.py`) were not modified to match RTL behavior.
- Anti-vacuous cases 007 and 008 explicitly detect output mismatches on corrupted inputs.
