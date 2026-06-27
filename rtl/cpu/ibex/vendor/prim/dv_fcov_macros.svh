// Stub dv_fcov_macros.svh — coverage macros for VCS simulation
// Minimal definitions for Ibex compilation without OpenTitan DV infrastructure

`ifndef DV_FCOV_MACROS_SVH
`define DV_FCOV_MACROS_SVH

// DV_FCOV_SIGNAL: declare a coverage signal (NOP in non-coverage sim)
`define DV_FCOV_SIGNAL(__type, __name, __cov)

// DV_FCOV_SIGNAL_GEN_IF: generate a coverage signal if condition is true
`define DV_FCOV_SIGNAL_GEN_IF(__type, __name, __gen, __cond)

`endif // DV_FCOV_MACROS_SVH
