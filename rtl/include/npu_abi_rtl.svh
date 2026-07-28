// DO NOT EDIT — wraps generated gen/npu_abi_pkg.sv for RTL consumption.
// ABI version: 1.0
//
// Include this file from any RTL module that needs to reference ABI
// address constants, register offsets, or opcodes. The generated
// package (gen/npu_abi_pkg.sv) is the single source of truth.
//
// Usage (SystemVerilog):
//   `include "rtl/include/npu_abi_rtl.svh"
//   // Then use: NPU_MXU_BASE, NPU_MXU_CTRL_OFFSET, etc.
//
// Verification:
//   iverilog -E -I. rtl/include/npu_abi_rtl.svh 2>&1 | grep -c "localparam"
//   or: vppreproc rtl/include/npu_abi_rtl.svh -I.
//

`ifndef NPU_ABI_RTL_SVH
`define NPU_ABI_RTL_SVH

// Import the generated SystemVerilog package
`include "../gen/npu_abi_pkg.sv"

// Re-export package constants into `define macros for convenience
// (some RTL files prefer `define over package::localparam)

`define NPU_MXU_BASE       32'h40000000
`define NPU_SFU_BASE       32'h40001000
`define NPU_VECTOR_BASE    32'h40002000
`define NPU_DMA_BASE       32'h40003000
`define NPU_PCIE_BASE      32'h40004000
`define NPU_DOORBELL_BASE  32'h40005000
`define NPU_INTC_BASE      32'h40006000
`define NPU_PCIE_DMA_BASE  32'h40007000
`define NPU_SRAM_BASE      32'h20000000
`define NPU_DRAM_BASE      32'h80000000

// Doorbell register offsets
`define NPU_DOORBELL_HOST_TAIL_OFFSET  12'h000
`define NPU_DOORBELL_NPU_HEAD_OFFSET   12'h004
`define NPU_DOORBELL_HOST_HEAD_OFFSET  12'h008
`define NPU_DOORBELL_NPU_TAIL_OFFSET   12'h00C
`define NPU_DOORBELL_LAST_STATUS_OFFSET 12'h010

// Compile-time sanity: validate generated constants match local defines
// (non-synthesizable, for preprocessor/debug only)
`ifdef NPU_ABI_CHECK
generate
    if (NPU_DOORBELL_BASE != npu_abi_pkg::NPU_DOORBELL_BASE) begin
        $error("RTL ABI mismatch: DOORBELL base address");
    end
endgenerate
`endif

`endif // NPU_ABI_RTL_SVH
