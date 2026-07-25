# func-model-signoff-v3 Issues

## Active
- **T1a-mmul-precision-gap**: MMUL golden comparison fails (max_diff ~400, rtol=1e-5 required). Bridge's FuncModel `_run_mxu_compute` produces numerically different output from `GoldenMXU.matmul_int4_per_block`. Root cause: different quantization/dequantization paths in bridge DMA→SRAM→MXU vs direct golden.
- **T1c-forward-missing-tokenizers**: Cannot run forward pass — `tokenizers` Python module not available on sz0001. Requires internet access to install or pre-built wheel.

## Resolved
- **T1-plugin-abi**: Rebuilt `npu_mmio_plugin.so` with old C++ ABI (`-D_GLIBCXX_USE_CXX11_ABI=0`). Plugin was compiled with Ubuntu GCC 11 (CXX11 ABI) while Spike binary uses GCC 4.8 (old ABI).
- **T1-libstdcpp**: Added LD_LIBRARY_PATH pointing to Cadence CEREBRUS libstdc++ for CXXABI_1.3.9+ compatibility.
- **T1-wrapper-regs**: Added MXU/VECTOR wrapper register support to `mmio_bridge.py` (WRP_CMD at 0x3C, WRP_STATUS at 0x40). Firmware uses SOC wrapper registers that differ from engine-level regmap.
