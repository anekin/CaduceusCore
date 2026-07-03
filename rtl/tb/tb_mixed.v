//=============================================================================
// tb_mixed — CaduceusCore Mixed-Mode Testbench
//=============================================================================
// SoC Phase 3-4 / Todo 3-4 (soc-rtl-substitution)
//
// Parameterized testbench that conditionally instantiates RTL sub-modules
// based on +define+ compile-time flags. When a module uses Func Model
// (golden), its RTL is not instantiated and the Python RTLSoCRunner
// emulates it. When all modules are RTL, this behaves identically to
// tb_soc.v.
//
// Mixed-Mode Defines (set via VCS +define+ flag):
//   USE_RTL_PCIE    — Instantiate pcie_ep_wrapper + verilog-pcie
//   USE_RTL_DMA     — Instantiate dma_wrapper + axi_cdma
//   USE_RTL_MXU     — Instantiate mxu_soc_wrapper
//   USE_RTL_SFU     — Instantiate sfu_soc_wrapper
//   USE_RTL_VECTOR  — Instantiate vector_soc_wrapper
//
// Default (no defines): FULL RTL mode — all modules instantiated.
// This is equivalent to tb_soc.v for the common case.
//
// Mixed-mode (some defines missing): missing modules are tied off;
// the cocotb Python side emulates them via Func Model golden reference.
//
// VCS Compile (full RTL, same as tb_soc.v):
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
//       -f rtl/cpu/ibex.flist -f rtl/ip/verilog-axi.flist \
//       -f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist \
//       -top tb_mixed -o simv_mixed -l elaborate.log
// NOTE: ibex.flist must come FIRST (contains ibex_pkg.sv)
//
// VCS Compile (PCIe-only mixed-mode):
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
//       +define+USE_RTL_PCIE \
//       -f rtl/cpu/ibex.flist -f rtl/ip/verilog-axi.flist \
//       -f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist \
//       -top tb_mixed -o simv_mixed -l elaborate.log
//=============================================================================

`resetall
`timescale 1ns / 1ps
`default_nettype none

//=============================================================================
// Reduced mixed-mode DUT
//=============================================================================
// Instantiated inside tb_mixed when any USE_RTL_* mixed-mode define is present.
// Conditionally instantiates the requested RTL sub-modules (PCIe/DMA/MXU/SFU/
// Vector) while keeping the remaining engine APB slaves as behavioral stubs.
// The minimal infrastructure (crossbar, SRAM, DRAM, APB decoder, INTC,
// doorbell) and an APB master stub named u_ibex_wrapper are always present so
// CocotbBridge's _apb_write/_apb_read helpers keep working.
//=============================================================================
`ifdef USE_RTL_PCIE
module caduceus_pcie_mixed_dut #(
    parameter int unsigned CROSSBAR_MASTERS = 6,
    parameter int unsigned SRAM_SIZE        = 32'd4194304,
    parameter int unsigned DRAM_SIZE        = 32'd2147483648
) (
    input  wire        clk,
    input  wire        rst_n,

    // PCIe TLP ports
    input  wire [511:0] pcie_rx_req_tlp_data,
    input  wire [127:0] pcie_rx_req_tlp_hdr,
    input  wire         pcie_rx_req_tlp_valid,
    input  wire         pcie_rx_req_tlp_sop,
    input  wire         pcie_rx_req_tlp_eop,
    output wire         pcie_rx_req_tlp_ready,

    output wire [511:0] pcie_tx_cpl_tlp_data,
    output wire [15:0]  pcie_tx_cpl_tlp_strb,
    output wire [127:0] pcie_tx_cpl_tlp_hdr,
    output wire         pcie_tx_cpl_tlp_valid,
    output wire         pcie_tx_cpl_tlp_sop,
    output wire         pcie_tx_cpl_tlp_eop,
    input  wire         pcie_tx_cpl_tlp_ready,

    input  wire         timer_irq_i
);

    //=========================================================================
    // Crossbar parameters
    //=========================================================================
    localparam int unsigned CB_NUM_M      = 6;
    localparam int unsigned CB_NUM_S      = 2;
    localparam int unsigned CB_DATA_WIDTH = 512;
    localparam int unsigned CB_ADDR_WIDTH = 32;
    localparam int unsigned CB_M_ID_WIDTH = 6;
    localparam int unsigned CB_MSEL_WIDTH = 3;
    localparam int unsigned CB_S_ID_WIDTH = CB_M_ID_WIDTH + CB_MSEL_WIDTH;

    //=========================================================================
    // Crossbar master-side packed buses
    //=========================================================================
    wire [CB_NUM_M-1:0][CB_M_ID_WIDTH-1:0]     cb_m_awid;
    wire [CB_NUM_M-1:0][CB_ADDR_WIDTH-1:0]     cb_m_awaddr;
    wire [CB_NUM_M-1:0][7:0]                   cb_m_awlen;
    wire [CB_NUM_M-1:0][2:0]                   cb_m_awsize;
    wire [CB_NUM_M-1:0][1:0]                   cb_m_awburst;
    wire [CB_NUM_M-1:0]                        cb_m_awvalid;
    wire [CB_NUM_M-1:0]                        cb_m_awready;

    wire [CB_NUM_M-1:0][CB_DATA_WIDTH-1:0]     cb_m_wdata;
    wire [CB_NUM_M-1:0][CB_DATA_WIDTH/8-1:0]   cb_m_wstrb;
    wire [CB_NUM_M-1:0]                        cb_m_wlast;
    wire [CB_NUM_M-1:0]                        cb_m_wvalid;
    wire [CB_NUM_M-1:0]                        cb_m_wready;

    wire [CB_NUM_M-1:0][CB_M_ID_WIDTH-1:0]     cb_m_bid;
    wire [CB_NUM_M-1:0][1:0]                   cb_m_bresp;
    wire [CB_NUM_M-1:0]                        cb_m_bvalid;
    wire [CB_NUM_M-1:0]                        cb_m_bready;

    wire [CB_NUM_M-1:0][CB_M_ID_WIDTH-1:0]     cb_m_arid;
    wire [CB_NUM_M-1:0][CB_ADDR_WIDTH-1:0]     cb_m_araddr;
    wire [CB_NUM_M-1:0][7:0]                   cb_m_arlen;
    wire [CB_NUM_M-1:0][2:0]                   cb_m_arsize;
    wire [CB_NUM_M-1:0][1:0]                   cb_m_arburst;
    wire [CB_NUM_M-1:0]                        cb_m_arvalid;
    wire [CB_NUM_M-1:0]                        cb_m_arready;

    wire [CB_NUM_M-1:0][CB_M_ID_WIDTH-1:0]     cb_m_rid;
    wire [CB_NUM_M-1:0][CB_DATA_WIDTH-1:0]     cb_m_rdata;
    wire [CB_NUM_M-1:0][1:0]                   cb_m_rresp;
    wire [CB_NUM_M-1:0]                        cb_m_rlast;
    wire [CB_NUM_M-1:0]                        cb_m_rvalid;
    wire [CB_NUM_M-1:0]                        cb_m_rready;

    //=========================================================================
    // Crossbar slave-side packed buses
    //=========================================================================
    wire [CB_NUM_S-1:0][CB_S_ID_WIDTH-1:0]     cb_s_awid;
    wire [CB_NUM_S-1:0][CB_ADDR_WIDTH-1:0]     cb_s_awaddr;
    wire [CB_NUM_S-1:0][7:0]                   cb_s_awlen;
    wire [CB_NUM_S-1:0][2:0]                   cb_s_awsize;
    wire [CB_NUM_S-1:0][1:0]                   cb_s_awburst;
    wire [CB_NUM_S-1:0]                        cb_s_awvalid;
    wire [CB_NUM_S-1:0]                        cb_s_awready;

    wire [CB_NUM_S-1:0][CB_DATA_WIDTH-1:0]     cb_s_wdata;
    wire [CB_NUM_S-1:0][CB_DATA_WIDTH/8-1:0]   cb_s_wstrb;
    wire [CB_NUM_S-1:0]                        cb_s_wlast;
    wire [CB_NUM_S-1:0]                        cb_s_wvalid;
    wire [CB_NUM_S-1:0]                        cb_s_wready;

    wire [CB_NUM_S-1:0][CB_S_ID_WIDTH-1:0]     cb_s_bid;
    wire [CB_NUM_S-1:0][1:0]                   cb_s_bresp;
    wire [CB_NUM_S-1:0]                        cb_s_bvalid;
    wire [CB_NUM_S-1:0]                        cb_s_bready;

    wire [CB_NUM_S-1:0][CB_S_ID_WIDTH-1:0]     cb_s_arid;
    wire [CB_NUM_S-1:0][CB_ADDR_WIDTH-1:0]     cb_s_araddr;
    wire [CB_NUM_S-1:0][7:0]                   cb_s_arlen;
    wire [CB_NUM_S-1:0][2:0]                   cb_s_arsize;
    wire [CB_NUM_S-1:0][1:0]                   cb_s_arburst;
    wire [CB_NUM_S-1:0]                        cb_s_arvalid;
    wire [CB_NUM_S-1:0]                        cb_s_arready;

    wire [CB_NUM_S-1:0][CB_S_ID_WIDTH-1:0]     cb_s_rid;
    wire [CB_NUM_S-1:0][CB_DATA_WIDTH-1:0]     cb_s_rdata;
    wire [CB_NUM_S-1:0][1:0]                   cb_s_rresp;
    wire [CB_NUM_S-1:0]                        cb_s_rlast;
    wire [CB_NUM_S-1:0]                        cb_s_rvalid;
    wire [CB_NUM_S-1:0]                        cb_s_rready;

    //=========================================================================
    // PCIe AXI4 master wires
    //=========================================================================
    wire [5:0]   pcie_awid;
    wire [31:0]  pcie_awaddr;
    wire [7:0]   pcie_awlen;
    wire [2:0]   pcie_awsize;
    wire [1:0]   pcie_awburst;
    wire         pcie_awvalid;
    wire         pcie_awready;
    wire [511:0] pcie_wdata;
    wire [63:0]  pcie_wstrb;
    wire         pcie_wlast;
    wire         pcie_wvalid;
    wire         pcie_wready;
    wire [5:0]   pcie_bid;
    wire [1:0]   pcie_bresp;
    wire         pcie_bvalid;
    wire         pcie_bready;
    wire [5:0]   pcie_arid;
    wire [31:0]  pcie_araddr;
    wire [7:0]   pcie_arlen;
    wire [2:0]   pcie_arsize;
    wire [1:0]   pcie_arburst;
    wire         pcie_arvalid;
    wire         pcie_arready;
    wire [5:0]   pcie_rid;
    wire [511:0] pcie_rdata;
    wire [1:0]   pcie_rresp;
    wire         pcie_rlast;
    wire         pcie_rvalid;
    wire         pcie_rready;

    //=========================================================================
    // Engine / DMA AXI4 master wires (declared unconditionally; used when the
    // corresponding USE_RTL_* define is present)
    //=========================================================================
    wire [7:0]   mxu_awid_8;    wire [31:0] mxu_awaddr;   wire [7:0]  mxu_awlen;
    wire [2:0]   mxu_awsize;    wire [1:0]  mxu_awburst;  wire        mxu_awvalid;
    wire         mxu_awready;   wire [511:0]mxu_wdata;    wire [63:0] mxu_wstrb;
    wire         mxu_wlast;     wire        mxu_wvalid;   wire        mxu_wready;
    wire [7:0]   mxu_bid_8;     wire [1:0]  mxu_bresp;    wire        mxu_bvalid;
    wire         mxu_bready;    wire [7:0]  mxu_arid_8;   wire [31:0] mxu_araddr;
    wire [7:0]   mxu_arlen;     wire [2:0]  mxu_arsize;   wire [1:0]  mxu_arburst;
    wire         mxu_arvalid;   wire        mxu_arready;  wire [7:0]  mxu_rid_8;
    wire [511:0] mxu_rdata;     wire [1:0]  mxu_rresp;    wire        mxu_rlast;
    wire         mxu_rvalid;    wire        mxu_rready;

    wire [7:0]   sfu_awid_8;    wire [31:0] sfu_awaddr;   wire [7:0]  sfu_awlen;
    wire [2:0]   sfu_awsize;    wire [1:0]  sfu_awburst;  wire        sfu_awvalid;
    wire         sfu_awready;   wire [511:0]sfu_wdata;    wire [63:0] sfu_wstrb;
    wire         sfu_wlast;     wire        sfu_wvalid;   wire        sfu_wready;
    wire [7:0]   sfu_bid_8;     wire [1:0]  sfu_bresp;    wire        sfu_bvalid;
    wire         sfu_bready;    wire [7:0]  sfu_arid_8;   wire [31:0] sfu_araddr;
    wire [7:0]   sfu_arlen;     wire [2:0]  sfu_arsize;   wire [1:0]  sfu_arburst;
    wire         sfu_arvalid;   wire        sfu_arready;  wire [7:0]  sfu_rid_8;
    wire [511:0] sfu_rdata;     wire [1:0]  sfu_rresp;    wire        sfu_rlast;
    wire         sfu_rvalid;    wire        sfu_rready;

    wire [7:0]   vec_awid_8;    wire [31:0] vec_awaddr;   wire [7:0]  vec_awlen;
    wire [2:0]   vec_awsize;    wire [1:0]  vec_awburst;  wire        vec_awvalid;
    wire         vec_awready;   wire [511:0]vec_wdata;    wire [63:0] vec_wstrb;
    wire         vec_wlast;     wire        vec_wvalid;   wire        vec_wready;
    wire [7:0]   vec_bid_8;     wire [1:0]  vec_bresp;    wire        vec_bvalid;
    wire         vec_bready;    wire [7:0]  vec_arid_8;   wire [31:0] vec_araddr;
    wire [7:0]   vec_arlen;     wire [2:0]  vec_arsize;   wire [1:0]  vec_arburst;
    wire         vec_arvalid;   wire        vec_arready;  wire [7:0]  vec_rid_8;
    wire [511:0] vec_rdata;     wire [1:0]  vec_rresp;    wire        vec_rlast;
    wire         vec_rvalid;    wire        vec_rready;

    wire [7:0]   dma_awid_8;    wire [31:0] dma_awaddr;   wire [7:0]  dma_awlen;
    wire [2:0]   dma_awsize;    wire [1:0]  dma_awburst;  wire        dma_awvalid;
    wire         dma_awready;   wire [511:0]dma_wdata;    wire [63:0] dma_wstrb;
    wire         dma_wlast;     wire        dma_wvalid;   wire        dma_wready;
    wire [7:0]   dma_bid_8;     wire [1:0]  dma_bresp;    wire        dma_bvalid;
    wire         dma_bready;    wire [7:0]  dma_arid_8;   wire [31:0] dma_araddr;
    wire [7:0]   dma_arlen;     wire [2:0]  dma_arsize;   wire [1:0]  dma_arburst;
    wire         dma_arvalid;   wire        dma_arready;  wire [7:0]  dma_rid_8;
    wire [511:0] dma_rdata;     wire [1:0]  dma_rresp;    wire        dma_rlast;
    wire         dma_rvalid;    wire        dma_rready;

    //=========================================================================
    // APB bus
    //=========================================================================
    wire [31:0]  apb_m_paddr;
    wire         apb_m_psel;
    wire         apb_m_penable;
    wire         apb_m_pwrite;
    wire [31:0]  apb_m_pwdata;
    wire [31:0]  apb_m_prdata;
    wire         apb_m_pready;
    wire         apb_m_pslverr;

    wire [6:0]   apb_psel_o;
    wire [6:0]   apb_penable_o;
    wire [31:0]  apb_paddr_o;
    wire         apb_pwrite_o;
    wire [31:0]  apb_pwdata_o;

    wire [6:0]   apb_pready_i;
    wire [6:0]   apb_pslverr_i;
    wire [31:0]  apb_prdata [0:6];

    //=========================================================================
    // Interrupt wires
    //=========================================================================
    wire pcie_irq;
    wire doorbell_irq;
    wire cpu_irq;

    //=========================================================================
    // Crossbar master tie-offs / connections
    //=========================================================================
    // Master 0 is not used in this reduced DUT (no Ibex AXI4 path).
    assign cb_m_awid[0]     = 6'd0;
    assign cb_m_awaddr[0]   = 32'd0;
    assign cb_m_awlen[0]    = 8'd0;
    assign cb_m_awsize[0]   = 3'd0;
    assign cb_m_awburst[0]  = 2'd0;
    assign cb_m_awvalid[0]  = 1'b0;
    assign cb_m_wdata[0]    = 512'd0;
    assign cb_m_wstrb[0]    = 64'd0;
    assign cb_m_wlast[0]    = 1'b0;
    assign cb_m_wvalid[0]   = 1'b0;
    assign cb_m_bready[0]   = 1'b1;
    assign cb_m_arid[0]     = 6'd0;
    assign cb_m_araddr[0]   = 32'd0;
    assign cb_m_arlen[0]    = 8'd0;
    assign cb_m_arsize[0]   = 3'd0;
    assign cb_m_arburst[0]  = 2'd0;
    assign cb_m_arvalid[0]  = 1'b0;
    assign cb_m_rready[0]   = 1'b1;

    // Master 1: MXU wrapper when USE_RTL_MXU, otherwise tied off
`ifdef USE_RTL_MXU
    assign cb_m_awid[1]    = mxu_awid_8[5:0];
    assign cb_m_awaddr[1]  = mxu_awaddr;
    assign cb_m_awlen[1]   = mxu_awlen;
    assign cb_m_awsize[1]  = mxu_awsize;
    assign cb_m_awburst[1] = mxu_awburst;
    assign cb_m_awvalid[1] = mxu_awvalid;
    assign mxu_awready     = cb_m_awready[1];
    assign cb_m_wdata[1]   = mxu_wdata;
    assign cb_m_wstrb[1]   = mxu_wstrb;
    assign cb_m_wlast[1]   = mxu_wlast;
    assign cb_m_wvalid[1]  = mxu_wvalid;
    assign mxu_wready      = cb_m_wready[1];
    assign mxu_bid_8       = {2'b0, cb_m_bid[1]};
    assign mxu_bresp       = cb_m_bresp[1];
    assign mxu_bvalid      = cb_m_bvalid[1];
    assign cb_m_bready[1]  = mxu_bready;
    assign cb_m_arid[1]    = mxu_arid_8[5:0];
    assign cb_m_araddr[1]  = mxu_araddr;
    assign cb_m_arlen[1]   = mxu_arlen;
    assign cb_m_arsize[1]  = mxu_arsize;
    assign cb_m_arburst[1] = mxu_arburst;
    assign cb_m_arvalid[1] = mxu_arvalid;
    assign mxu_arready     = cb_m_arready[1];
    assign mxu_rid_8       = {2'b0, cb_m_rid[1]};
    assign mxu_rdata       = cb_m_rdata[1];
    assign mxu_rresp       = cb_m_rresp[1];
    assign mxu_rlast       = cb_m_rlast[1];
    assign mxu_rvalid      = cb_m_rvalid[1];
    assign cb_m_rready[1]  = mxu_rready;
`else
    assign cb_m_awid[1]     = 6'd0;
    assign cb_m_awaddr[1]   = 32'd0;
    assign cb_m_awlen[1]    = 8'd0;
    assign cb_m_awsize[1]   = 3'd0;
    assign cb_m_awburst[1]  = 2'd0;
    assign cb_m_awvalid[1]  = 1'b0;
    assign cb_m_wdata[1]    = 512'd0;
    assign cb_m_wstrb[1]    = 64'd0;
    assign cb_m_wlast[1]    = 1'b0;
    assign cb_m_wvalid[1]   = 1'b0;
    assign cb_m_bready[1]   = 1'b1;
    assign cb_m_arid[1]     = 6'd0;
    assign cb_m_araddr[1]   = 32'd0;
    assign cb_m_arlen[1]    = 8'd0;
    assign cb_m_arsize[1]   = 3'd0;
    assign cb_m_arburst[1]  = 2'd0;
    assign cb_m_arvalid[1]  = 1'b0;
    assign cb_m_rready[1]   = 1'b1;
`endif

    // Master 2: SFU wrapper when USE_RTL_SFU, otherwise tied off
`ifdef USE_RTL_SFU
    assign cb_m_awid[2]    = sfu_awid_8[5:0];
    assign cb_m_awaddr[2]  = sfu_awaddr;
    assign cb_m_awlen[2]   = sfu_awlen;
    assign cb_m_awsize[2]  = sfu_awsize;
    assign cb_m_awburst[2] = sfu_awburst;
    assign cb_m_awvalid[2] = sfu_awvalid;
    assign sfu_awready     = cb_m_awready[2];
    assign cb_m_wdata[2]   = sfu_wdata;
    assign cb_m_wstrb[2]   = sfu_wstrb;
    assign cb_m_wlast[2]   = sfu_wlast;
    assign cb_m_wvalid[2]  = sfu_wvalid;
    assign sfu_wready      = cb_m_wready[2];
    assign sfu_bid_8       = {2'b0, cb_m_bid[2]};
    assign sfu_bresp       = cb_m_bresp[2];
    assign sfu_bvalid      = cb_m_bvalid[2];
    assign cb_m_bready[2]  = sfu_bready;
    assign cb_m_arid[2]    = sfu_arid_8[5:0];
    assign cb_m_araddr[2]  = sfu_araddr;
    assign cb_m_arlen[2]   = sfu_arlen;
    assign cb_m_arsize[2]  = sfu_arsize;
    assign cb_m_arburst[2] = sfu_arburst;
    assign cb_m_arvalid[2] = sfu_arvalid;
    assign sfu_arready     = cb_m_arready[2];
    assign sfu_rid_8       = {2'b0, cb_m_rid[2]};
    assign sfu_rdata       = cb_m_rdata[2];
    assign sfu_rresp       = cb_m_rresp[2];
    assign sfu_rlast       = cb_m_rlast[2];
    assign sfu_rvalid      = cb_m_rvalid[2];
    assign cb_m_rready[2]  = sfu_rready;
`else
    assign cb_m_awid[2]     = 6'd0;
    assign cb_m_awaddr[2]   = 32'd0;
    assign cb_m_awlen[2]    = 8'd0;
    assign cb_m_awsize[2]   = 3'd0;
    assign cb_m_awburst[2]  = 2'd0;
    assign cb_m_awvalid[2]  = 1'b0;
    assign cb_m_wdata[2]    = 512'd0;
    assign cb_m_wstrb[2]    = 64'd0;
    assign cb_m_wlast[2]    = 1'b0;
    assign cb_m_wvalid[2]   = 1'b0;
    assign cb_m_bready[2]   = 1'b1;
    assign cb_m_arid[2]     = 6'd0;
    assign cb_m_araddr[2]   = 32'd0;
    assign cb_m_arlen[2]    = 8'd0;
    assign cb_m_arsize[2]   = 3'd0;
    assign cb_m_arburst[2]  = 2'd0;
    assign cb_m_arvalid[2]  = 1'b0;
    assign cb_m_rready[2]   = 1'b1;
`endif

    // Master 3: Vector wrapper when USE_RTL_VECTOR, otherwise tied off
`ifdef USE_RTL_VECTOR
    assign cb_m_awid[3]    = vec_awid_8[5:0];
    assign cb_m_awaddr[3]  = vec_awaddr;
    assign cb_m_awlen[3]   = vec_awlen;
    assign cb_m_awsize[3]  = vec_awsize;
    assign cb_m_awburst[3] = vec_awburst;
    assign cb_m_awvalid[3] = vec_awvalid;
    assign vec_awready     = cb_m_awready[3];
    assign cb_m_wdata[3]   = vec_wdata;
    assign cb_m_wstrb[3]   = vec_wstrb;
    assign cb_m_wlast[3]   = vec_wlast;
    assign cb_m_wvalid[3]  = vec_wvalid;
    assign vec_wready      = cb_m_wready[3];
    assign vec_bid_8       = {2'b0, cb_m_bid[3]};
    assign vec_bresp       = cb_m_bresp[3];
    assign vec_bvalid      = cb_m_bvalid[3];
    assign cb_m_bready[3]  = vec_bready;
    assign cb_m_arid[3]    = vec_arid_8[5:0];
    assign cb_m_araddr[3]  = vec_araddr;
    assign cb_m_arlen[3]   = vec_arlen;
    assign cb_m_arsize[3]  = vec_arsize;
    assign cb_m_arburst[3] = vec_arburst;
    assign cb_m_arvalid[3] = vec_arvalid;
    assign vec_arready     = cb_m_arready[3];
    assign vec_rid_8       = {2'b0, cb_m_rid[3]};
    assign vec_rdata       = cb_m_rdata[3];
    assign vec_rresp       = cb_m_rresp[3];
    assign vec_rlast       = cb_m_rlast[3];
    assign vec_rvalid      = cb_m_rvalid[3];
    assign cb_m_rready[3]  = vec_rready;
`else
    assign cb_m_awid[3]     = 6'd0;
    assign cb_m_awaddr[3]   = 32'd0;
    assign cb_m_awlen[3]    = 8'd0;
    assign cb_m_awsize[3]   = 3'd0;
    assign cb_m_awburst[3]  = 2'd0;
    assign cb_m_awvalid[3]  = 1'b0;
    assign cb_m_wdata[3]    = 512'd0;
    assign cb_m_wstrb[3]    = 64'd0;
    assign cb_m_wlast[3]    = 1'b0;
    assign cb_m_wvalid[3]   = 1'b0;
    assign cb_m_bready[3]   = 1'b1;
    assign cb_m_arid[3]     = 6'd0;
    assign cb_m_araddr[3]   = 32'd0;
    assign cb_m_arlen[3]    = 8'd0;
    assign cb_m_arsize[3]   = 3'd0;
    assign cb_m_arburst[3]  = 2'd0;
    assign cb_m_arvalid[3]  = 1'b0;
    assign cb_m_rready[3]   = 1'b1;
`endif

    // Master 4: DMA wrapper when USE_RTL_DMA, otherwise tied off
`ifdef USE_RTL_DMA
    assign cb_m_awid[4]    = dma_awid_8[5:0];
    assign cb_m_awaddr[4]  = dma_awaddr;
    assign cb_m_awlen[4]   = dma_awlen;
    assign cb_m_awsize[4]  = dma_awsize;
    assign cb_m_awburst[4] = dma_awburst;
    assign cb_m_awvalid[4] = dma_awvalid;
    assign dma_awready     = cb_m_awready[4];
    assign cb_m_wdata[4]   = dma_wdata;
    assign cb_m_wstrb[4]   = dma_wstrb;
    assign cb_m_wlast[4]   = dma_wlast;
    assign cb_m_wvalid[4]  = dma_wvalid;
    assign dma_wready      = cb_m_wready[4];
    assign dma_bid_8       = {2'b0, cb_m_bid[4]};
    assign dma_bresp       = cb_m_bresp[4];
    assign dma_bvalid      = cb_m_bvalid[4];
    assign cb_m_bready[4]  = dma_bready;
    assign cb_m_arid[4]    = dma_arid_8[5:0];
    assign cb_m_araddr[4]  = dma_araddr;
    assign cb_m_arlen[4]   = dma_arlen;
    assign cb_m_arsize[4]  = dma_arsize;
    assign cb_m_arburst[4] = dma_arburst;
    assign cb_m_arvalid[4] = dma_arvalid;
    assign dma_arready     = cb_m_arready[4];
    assign dma_rid_8       = {2'b0, cb_m_rid[4]};
    assign dma_rdata       = cb_m_rdata[4];
    assign dma_rresp       = cb_m_rresp[4];
    assign dma_rlast       = cb_m_rlast[4];
    assign dma_rvalid      = cb_m_rvalid[4];
    assign cb_m_rready[4]  = dma_rready;
`else
    assign cb_m_awid[4]     = 6'd0;
    assign cb_m_awaddr[4]   = 32'd0;
    assign cb_m_awlen[4]    = 8'd0;
    assign cb_m_awsize[4]   = 3'd0;
    assign cb_m_awburst[4]  = 2'd0;
    assign cb_m_awvalid[4]  = 1'b0;
    assign cb_m_wdata[4]    = 512'd0;
    assign cb_m_wstrb[4]    = 64'd0;
    assign cb_m_wlast[4]    = 1'b0;
    assign cb_m_wvalid[4]   = 1'b0;
    assign cb_m_bready[4]   = 1'b1;
    assign cb_m_arid[4]     = 6'd0;
    assign cb_m_araddr[4]   = 32'd0;
    assign cb_m_arlen[4]    = 8'd0;
    assign cb_m_arsize[4]   = 3'd0;
    assign cb_m_arburst[4]  = 2'd0;
    assign cb_m_arvalid[4]  = 1'b0;
    assign cb_m_rready[4]   = 1'b1;
`endif

    //=========================================================================
    // PCIe → crossbar master 5
    //=========================================================================
    assign cb_m_awid[5]    = pcie_awid;
    assign cb_m_awaddr[5]  = pcie_awaddr;
    assign cb_m_awlen[5]   = pcie_awlen;
    assign cb_m_awsize[5]  = pcie_awsize;
    assign cb_m_awburst[5] = pcie_awburst;
    assign cb_m_awvalid[5] = pcie_awvalid;
    assign pcie_awready    = cb_m_awready[5];
    assign cb_m_wdata[5]   = pcie_wdata;
    assign cb_m_wstrb[5]   = pcie_wstrb;
    assign cb_m_wlast[5]   = pcie_wlast;
    assign cb_m_wvalid[5]  = pcie_wvalid;
    assign pcie_wready     = cb_m_wready[5];
    assign pcie_bid        = cb_m_bid[5];
    assign pcie_bresp      = cb_m_bresp[5];
    assign pcie_bvalid     = cb_m_bvalid[5];
    assign cb_m_bready[5]  = pcie_bready;
    assign cb_m_arid[5]    = pcie_arid;
    assign cb_m_araddr[5]  = pcie_araddr;
    assign cb_m_arlen[5]   = pcie_arlen;
    assign cb_m_arsize[5]  = pcie_arsize;
    assign cb_m_arburst[5] = pcie_arburst;
    assign cb_m_arvalid[5] = pcie_arvalid;
    assign pcie_arready    = cb_m_arready[5];
    assign pcie_rid        = cb_m_rid[5];
    assign pcie_rdata      = cb_m_rdata[5];
    assign pcie_rresp      = cb_m_rresp[5];
    assign pcie_rlast      = cb_m_rlast[5];
    assign pcie_rvalid     = cb_m_rvalid[5];
    assign cb_m_rready[5]  = pcie_rready;

    //=========================================================================
    // AXI4 Crossbar
    //=========================================================================
    axi_crossbar #(
        .DATA_WIDTH (CB_DATA_WIDTH),
        .ADDR_WIDTH (CB_ADDR_WIDTH),
        .M_ID_WIDTH (CB_M_ID_WIDTH),
        .MSEL_WIDTH (CB_MSEL_WIDTH),
        .NUM_M      (CB_NUM_M),
        .NUM_S      (CB_NUM_S)
    ) u_axi_crossbar (
        .clk          (clk),
        .rst_n        (rst_n),

        .m_awid_i     (cb_m_awid),
        .m_awaddr_i   (cb_m_awaddr),
        .m_awlen_i    (cb_m_awlen),
        .m_awsize_i   (cb_m_awsize),
        .m_awburst_i  (cb_m_awburst),
        .m_awvalid_i  (cb_m_awvalid),
        .m_awready_o  (cb_m_awready),
        .m_wdata_i    (cb_m_wdata),
        .m_wstrb_i    (cb_m_wstrb),
        .m_wlast_i    (cb_m_wlast),
        .m_wvalid_i   (cb_m_wvalid),
        .m_wready_o   (cb_m_wready),
        .m_bid_o      (cb_m_bid),
        .m_bresp_o    (cb_m_bresp),
        .m_bvalid_o   (cb_m_bvalid),
        .m_bready_i   (cb_m_bready),
        .m_arid_i     (cb_m_arid),
        .m_araddr_i   (cb_m_araddr),
        .m_arlen_i    (cb_m_arlen),
        .m_arsize_i   (cb_m_arsize),
        .m_arburst_i  (cb_m_arburst),
        .m_arvalid_i  (cb_m_arvalid),
        .m_arready_o  (cb_m_arready),
        .m_rid_o      (cb_m_rid),
        .m_rdata_o    (cb_m_rdata),
        .m_rresp_o    (cb_m_rresp),
        .m_rlast_o    (cb_m_rlast),
        .m_rvalid_o   (cb_m_rvalid),
        .m_rready_i   (cb_m_rready),

        .s_awid_o     (cb_s_awid),
        .s_awaddr_o   (cb_s_awaddr),
        .s_awlen_o    (cb_s_awlen),
        .s_awsize_o   (cb_s_awsize),
        .s_awburst_o  (cb_s_awburst),
        .s_awvalid_o  (cb_s_awvalid),
        .s_awready_i  (cb_s_awready),
        .s_wdata_o    (cb_s_wdata),
        .s_wstrb_o    (cb_s_wstrb),
        .s_wlast_o    (cb_s_wlast),
        .s_wvalid_o   (cb_s_wvalid),
        .s_wready_i   (cb_s_wready),
        .s_bid_i      (cb_s_bid),
        .s_bresp_i    (cb_s_bresp),
        .s_bvalid_i   (cb_s_bvalid),
        .s_bready_o   (cb_s_bready),
        .s_arid_o     (cb_s_arid),
        .s_araddr_o   (cb_s_araddr),
        .s_arlen_o    (cb_s_arlen),
        .s_arsize_o   (cb_s_arsize),
        .s_arburst_o  (cb_s_arburst),
        .s_arvalid_o  (cb_s_arvalid),
        .s_arready_i  (cb_s_arready),
        .s_rid_i      (cb_s_rid),
        .s_rdata_i    (cb_s_rdata),
        .s_rresp_i    (cb_s_rresp),
        .s_rlast_i    (cb_s_rlast),
        .s_rvalid_i   (cb_s_rvalid),
        .s_rready_o   (cb_s_rready)
    );

    //=========================================================================
    // SRAM Controller (S0)
    //=========================================================================
    sram_ctrl #(
        .DATA_WIDTH (512),
        .ADDR_WIDTH (32),
        .ID_WIDTH   (CB_S_ID_WIDTH)
    ) u_sram_ctrl (
        .clk          (clk),
        .rst_n        (rst_n),
        .s_axi_awid   (cb_s_awid[0]),
        .s_axi_awaddr (cb_s_awaddr[0]),
        .s_axi_awlen  (cb_s_awlen[0]),
        .s_axi_awsize (cb_s_awsize[0]),
        .s_axi_awburst(cb_s_awburst[0]),
        .s_axi_awvalid(cb_s_awvalid[0]),
        .s_axi_awready(cb_s_awready[0]),
        .s_axi_wdata  (cb_s_wdata[0]),
        .s_axi_wstrb  (cb_s_wstrb[0]),
        .s_axi_wlast  (cb_s_wlast[0]),
        .s_axi_wvalid (cb_s_wvalid[0]),
        .s_axi_wready (cb_s_wready[0]),
        .s_axi_bid    (cb_s_bid[0]),
        .s_axi_bresp  (cb_s_bresp[0]),
        .s_axi_bvalid (cb_s_bvalid[0]),
        .s_axi_bready (cb_s_bready[0]),
        .s_axi_arid   (cb_s_arid[0]),
        .s_axi_araddr (cb_s_araddr[0]),
        .s_axi_arlen  (cb_s_arlen[0]),
        .s_axi_arsize (cb_s_arsize[0]),
        .s_axi_arburst(cb_s_arburst[0]),
        .s_axi_arvalid(cb_s_arvalid[0]),
        .s_axi_arready(cb_s_arready[0]),
        .s_axi_rid    (cb_s_rid[0]),
        .s_axi_rdata  (cb_s_rdata[0]),
        .s_axi_rresp  (cb_s_rresp[0]),
        .s_axi_rlast  (cb_s_rlast[0]),
        .s_axi_rvalid (cb_s_rvalid[0]),
        .s_axi_rready (cb_s_rready[0])
    );

    //=========================================================================
    // DRAM Behavioral Model (S1)
    //=========================================================================
    dram_model #(
        .DATA_WIDTH (512),
        .ADDR_WIDTH (32),
        .ID_WIDTH   (CB_S_ID_WIDTH)
    ) u_dram_model (
        .clk          (clk),
        .rst_n        (rst_n),
        .s_axi_awid   (cb_s_awid[1]),
        .s_axi_awaddr (cb_s_awaddr[1]),
        .s_axi_awlen  (cb_s_awlen[1]),
        .s_axi_awsize (cb_s_awsize[1]),
        .s_axi_awburst(cb_s_awburst[1]),
        .s_axi_awvalid(cb_s_awvalid[1]),
        .s_axi_awready(cb_s_awready[1]),
        .s_axi_wdata  (cb_s_wdata[1]),
        .s_axi_wstrb  (cb_s_wstrb[1]),
        .s_axi_wlast  (cb_s_wlast[1]),
        .s_axi_wvalid (cb_s_wvalid[1]),
        .s_axi_wready (cb_s_wready[1]),
        .s_axi_bid    (cb_s_bid[1]),
        .s_axi_bresp  (cb_s_bresp[1]),
        .s_axi_bvalid (cb_s_bvalid[1]),
        .s_axi_bready (cb_s_bready[1]),
        .s_axi_arid   (cb_s_arid[1]),
        .s_axi_araddr (cb_s_araddr[1]),
        .s_axi_arlen  (cb_s_arlen[1]),
        .s_axi_arsize (cb_s_arsize[1]),
        .s_axi_arburst(cb_s_arburst[1]),
        .s_axi_arvalid(cb_s_arvalid[1]),
        .s_axi_arready(cb_s_arready[1]),
        .s_axi_rid    (cb_s_rid[1]),
        .s_axi_rdata  (cb_s_rdata[1]),
        .s_axi_rresp  (cb_s_rresp[1]),
        .s_axi_rlast  (cb_s_rlast[1]),
        .s_axi_rvalid (cb_s_rvalid[1]),
        .s_axi_rready (cb_s_rready[1])
    );

    //=========================================================================
    // APB Decoder
    //=========================================================================
    apb_decoder u_apb_decoder (
        .clk       (clk),
        .rst_n     (rst_n),
        .psel      (apb_m_psel),
        .penable   (apb_m_penable),
        .paddr     (apb_m_paddr),
        .pwrite    (apb_m_pwrite),
        .pwdata    (apb_m_pwdata),
        .psel_o    (apb_psel_o),
        .penable_o (apb_penable_o),
        .paddr_o   (apb_paddr_o),
        .pwrite_o  (apb_pwrite_o),
        .pwdata_o  (apb_pwdata_o),
        .pready_i  (apb_pready_i),
        .pslverr_i (apb_pslverr_i),
        .prdata_i  (apb_prdata),
        .pready    (apb_m_pready),
        .pslverr   (apb_m_pslverr),
        .prdata    (apb_m_prdata)
    );

    //=========================================================================
    // APB master stub (named u_ibex_wrapper for CocotbBridge hierarchical path)
    //=========================================================================
    // CocotbBridge drives these APB master signals directly via VPI.
    // The stub simply exposes them to the apb_decoder.
    // AXI master outputs are tied off because the reduced DUT does not use
    // the Ibex AXI4 path.
    //=========================================================================
    ibex_apb_master_stub u_ibex_wrapper (
        .clk         (clk),
        .rst_n       (rst_n),

        // APB master (driven by cocotb)
        .apb_paddr   (apb_m_paddr),
        .apb_psel    (apb_m_psel),
        .apb_penable (apb_m_penable),
        .apb_pwrite  (apb_m_pwrite),
        .apb_pwdata  (apb_m_pwdata),
        .apb_prdata  (apb_m_prdata),
        .apb_pready  (apb_m_pready),
        .apb_pslverr (apb_m_pslverr),

        .cpu_irq_i   (cpu_irq)
    );

    //=========================================================================
    // APB slave 0: MXU wrapper (RTL) or stub (Func Model)
    //=========================================================================
    wire mxu_irq;
`ifdef USE_RTL_MXU
    mxu_soc_wrapper #(
        .W_BUF_DEPTH(5120),
        .A_BUF_DEPTH(10240)
    ) u_mxu_wrapper (
        .clk                 (clk),
        .rst_n               (rst_n),
        .psel                (apb_psel_o[0]),
        .penable             (apb_penable_o[0]),
        .pwrite              (apb_pwrite_o),
        .paddr               (apb_paddr_o[11:0]),
        .pwdata              (apb_pwdata_o),
        .prdata              (apb_prdata[0]),
        .pready              (apb_pready_i[0]),
        .pslverr             (apb_pslverr_i[0]),
        .m_axi_awid          (mxu_awid_8),
        .m_axi_awaddr        (mxu_awaddr),
        .m_axi_awlen         (mxu_awlen),
        .m_axi_awsize        (mxu_awsize),
        .m_axi_awburst       (mxu_awburst),
        .m_axi_awvalid       (mxu_awvalid),
        .m_axi_awready       (mxu_awready),
        .m_axi_wdata         (mxu_wdata),
        .m_axi_wstrb         (mxu_wstrb),
        .m_axi_wlast         (mxu_wlast),
        .m_axi_wvalid        (mxu_wvalid),
        .m_axi_wready        (mxu_wready),
        .m_axi_bid           (mxu_bid_8),
        .m_axi_bresp         (mxu_bresp),
        .m_axi_bvalid        (mxu_bvalid),
        .m_axi_bready        (mxu_bready),
        .m_axi_arid          (mxu_arid_8),
        .m_axi_araddr        (mxu_araddr),
        .m_axi_arlen         (mxu_arlen),
        .m_axi_arsize        (mxu_arsize),
        .m_axi_arburst       (mxu_arburst),
        .m_axi_arvalid       (mxu_arvalid),
        .m_axi_arready       (mxu_arready),
        .m_axi_rid           (mxu_rid_8),
        .m_axi_rdata         (mxu_rdata),
        .m_axi_rresp         (mxu_rresp),
        .m_axi_rlast         (mxu_rlast),
        .m_axi_rvalid        (mxu_rvalid),
        .m_axi_rready        (mxu_rready),
        .irq                 (mxu_irq),
        .dbg_state           (),
        .dbg_compute_en      (),
        .dbg_weight_load     (),
        .dbg_activation_load (),
        .dbg_store_out       (),
        .dbg_store_row       (),
        .dbg_compute_k       (),
        .dbg_tiles_completed ()
    );
`else
    apb_slave_stub u_mxu_stub (
        .clk     (clk),
        .rst_n   (rst_n),
        .psel    (apb_psel_o[0]),
        .penable (apb_penable_o[0]),
        .pwrite  (apb_pwrite_o),
        .paddr   (apb_paddr_o[11:0]),
        .pwdata  (apb_pwdata_o),
        .prdata  (apb_prdata[0]),
        .pready  (apb_pready_i[0]),
        .pslverr (apb_pslverr_i[0])
    );
    assign mxu_irq = 1'b0;
`endif

    //=========================================================================
    // APB slave 1: SFU wrapper (RTL) or stub (Func Model)
    //=========================================================================
    wire sfu_irq;
`ifdef USE_RTL_SFU
    sfu_soc_wrapper u_sfu_wrapper (
        .clk           (clk),
        .rst_n         (rst_n),
        .psel          (apb_psel_o[1]),
        .penable       (apb_penable_o[1]),
        .pwrite        (apb_pwrite_o),
        .paddr         (apb_paddr_o[11:0]),
        .pwdata        (apb_pwdata_o),
        .prdata        (apb_prdata[1]),
        .pready        (apb_pready_i[1]),
        .pslverr       (apb_pslverr_i[1]),
        .m_axi_awid    (sfu_awid_8),
        .m_axi_awaddr  (sfu_awaddr),
        .m_axi_awlen   (sfu_awlen),
        .m_axi_awsize  (sfu_awsize),
        .m_axi_awburst (sfu_awburst),
        .m_axi_awvalid (sfu_awvalid),
        .m_axi_awready (sfu_awready),
        .m_axi_wdata   (sfu_wdata),
        .m_axi_wstrb   (sfu_wstrb),
        .m_axi_wlast   (sfu_wlast),
        .m_axi_wvalid  (sfu_wvalid),
        .m_axi_wready  (sfu_wready),
        .m_axi_bid     (sfu_bid_8),
        .m_axi_bresp   (sfu_bresp),
        .m_axi_bvalid  (sfu_bvalid),
        .m_axi_bready  (sfu_bready),
        .m_axi_arid    (sfu_arid_8),
        .m_axi_araddr  (sfu_araddr),
        .m_axi_arlen   (sfu_arlen),
        .m_axi_arsize  (sfu_arsize),
        .m_axi_arburst (sfu_arburst),
        .m_axi_arvalid (sfu_arvalid),
        .m_axi_arready (sfu_arready),
        .m_axi_rid     (sfu_rid_8),
        .m_axi_rdata   (sfu_rdata),
        .m_axi_rresp   (sfu_rresp),
        .m_axi_rlast   (sfu_rlast),
        .m_axi_rvalid  (sfu_rvalid),
        .m_axi_rready  (sfu_rready),
        .irq           (sfu_irq)
    );
`else
    apb_slave_stub u_sfu_stub (
        .clk     (clk),
        .rst_n   (rst_n),
        .psel    (apb_psel_o[1]),
        .penable (apb_penable_o[1]),
        .pwrite  (apb_pwrite_o),
        .paddr   (apb_paddr_o[11:0]),
        .pwdata  (apb_pwdata_o),
        .prdata  (apb_prdata[1]),
        .pready  (apb_pready_i[1]),
        .pslverr (apb_pslverr_i[1])
    );
    assign sfu_irq = 1'b0;
`endif

    //=========================================================================
    // APB slave 2: Vector wrapper (RTL) or stub (Func Model)
    //=========================================================================
    wire vec_irq;
`ifdef USE_RTL_VECTOR
    vector_soc_wrapper #(
        .CHUNKS_MAX(80)
    ) u_vector_wrapper (
        .clk           (clk),
        .rst_n         (rst_n),
        .psel          (apb_psel_o[2]),
        .penable       (apb_penable_o[2]),
        .pwrite        (apb_pwrite_o),
        .paddr         (apb_paddr_o[11:0]),
        .pwdata        (apb_pwdata_o),
        .prdata        (apb_prdata[2]),
        .pready        (apb_pready_i[2]),
        .pslverr       (apb_pslverr_i[2]),
        .m_axi_awid    (vec_awid_8),
        .m_axi_awaddr  (vec_awaddr),
        .m_axi_awlen   (vec_awlen),
        .m_axi_awsize  (vec_awsize),
        .m_axi_awburst (vec_awburst),
        .m_axi_awvalid (vec_awvalid),
        .m_axi_awready (vec_awready),
        .m_axi_wdata   (vec_wdata),
        .m_axi_wstrb   (vec_wstrb),
        .m_axi_wlast   (vec_wlast),
        .m_axi_wvalid  (vec_wvalid),
        .m_axi_wready  (vec_wready),
        .m_axi_bid     (vec_bid_8),
        .m_axi_bresp   (vec_bresp),
        .m_axi_bvalid  (vec_bvalid),
        .m_axi_bready  (vec_bready),
        .m_axi_arid    (vec_arid_8),
        .m_axi_araddr  (vec_araddr),
        .m_axi_arlen   (vec_arlen),
        .m_axi_arsize  (vec_arsize),
        .m_axi_arburst (vec_arburst),
        .m_axi_arvalid (vec_arvalid),
        .m_axi_arready (vec_arready),
        .m_axi_rid     (vec_rid_8),
        .m_axi_rdata   (vec_rdata),
        .m_axi_rresp   (vec_rresp),
        .m_axi_rlast   (vec_rlast),
        .m_axi_rvalid  (vec_rvalid),
        .m_axi_rready  (vec_rready),
        .irq           (vec_irq)
    );
`else
    apb_slave_stub u_vector_stub (
        .clk     (clk),
        .rst_n   (rst_n),
        .psel    (apb_psel_o[2]),
        .penable (apb_penable_o[2]),
        .pwrite  (apb_pwrite_o),
        .paddr   (apb_paddr_o[11:0]),
        .pwdata  (apb_pwdata_o),
        .prdata  (apb_prdata[2]),
        .pready  (apb_pready_i[2]),
        .pslverr (apb_pslverr_i[2])
    );
    assign vec_irq = 1'b0;
`endif

    //=========================================================================
    // APB slave 3: DMA wrapper (RTL) or stub (Func Model)
    //=========================================================================
    wire dma_irq;
`ifdef USE_RTL_DMA
    dma_wrapper u_dma_wrapper (
        .clk           (clk),
        .rst_n         (rst_n),
        .psel          (apb_psel_o[3]),
        .penable       (apb_penable_o[3]),
        .pwrite        (apb_pwrite_o),
        .paddr         (apb_paddr_o[11:0]),
        .pwdata        (apb_pwdata_o),
        .prdata        (apb_prdata[3]),
        .pready        (apb_pready_i[3]),
        .pslverr       (apb_pslverr_i[3]),
        .m_axi_awid    (dma_awid_8),
        .m_axi_awaddr  (dma_awaddr),
        .m_axi_awlen   (dma_awlen),
        .m_axi_awsize  (dma_awsize),
        .m_axi_awburst (dma_awburst),
        .m_axi_awvalid (dma_awvalid),
        .m_axi_awready (dma_awready),
        .m_axi_wdata   (dma_wdata),
        .m_axi_wstrb   (dma_wstrb),
        .m_axi_wlast   (dma_wlast),
        .m_axi_wvalid  (dma_wvalid),
        .m_axi_wready  (dma_wready),
        .m_axi_bid     (dma_bid_8),
        .m_axi_bresp   (dma_bresp),
        .m_axi_bvalid  (dma_bvalid),
        .m_axi_bready  (dma_bready),
        .m_axi_arid    (dma_arid_8),
        .m_axi_araddr  (dma_araddr),
        .m_axi_arlen   (dma_arlen),
        .m_axi_arsize  (dma_arsize),
        .m_axi_arburst (dma_arburst),
        .m_axi_arvalid (dma_arvalid),
        .m_axi_arready (dma_arready),
        .m_axi_rid     (dma_rid_8),
        .m_axi_rdata   (dma_rdata),
        .m_axi_rresp   (dma_rresp),
        .m_axi_rlast   (dma_rlast),
        .m_axi_rvalid  (dma_rvalid),
        .m_axi_rready  (dma_rready),
        .dma_irq       (dma_irq)
    );
`else
    apb_slave_stub u_dma_stub (
        .clk     (clk),
        .rst_n   (rst_n),
        .psel    (apb_psel_o[3]),
        .penable (apb_penable_o[3]),
        .pwrite  (apb_pwrite_o),
        .paddr   (apb_paddr_o[11:0]),
        .pwdata  (apb_pwdata_o),
        .prdata  (apb_prdata[3]),
        .pready  (apb_pready_i[3]),
        .pslverr (apb_pslverr_i[3])
    );
    assign dma_irq = 1'b0;
`endif

    //=========================================================================
    // PCIe EP Wrapper (RTL)
    //=========================================================================
    pcie_ep_wrapper u_pcie_wrapper (
        .clk               (clk),
        .rst_n             (rst_n),
        .rx_req_tlp_data   (pcie_rx_req_tlp_data),
        .rx_req_tlp_hdr    (pcie_rx_req_tlp_hdr),
        .rx_req_tlp_valid  (pcie_rx_req_tlp_valid),
        .rx_req_tlp_sop    (pcie_rx_req_tlp_sop),
        .rx_req_tlp_eop    (pcie_rx_req_tlp_eop),
        .rx_req_tlp_ready  (pcie_rx_req_tlp_ready),
        .tx_cpl_tlp_data   (pcie_tx_cpl_tlp_data),
        .tx_cpl_tlp_strb   (pcie_tx_cpl_tlp_strb),
        .tx_cpl_tlp_hdr    (pcie_tx_cpl_tlp_hdr),
        .tx_cpl_tlp_valid  (pcie_tx_cpl_tlp_valid),
        .tx_cpl_tlp_sop    (pcie_tx_cpl_tlp_sop),
        .tx_cpl_tlp_eop    (pcie_tx_cpl_tlp_eop),
        .tx_cpl_tlp_ready  (pcie_tx_cpl_tlp_ready),

        .m_axi_awid        (pcie_awid),
        .m_axi_awaddr      (pcie_awaddr),
        .m_axi_awlen       (pcie_awlen),
        .m_axi_awsize      (pcie_awsize),
        .m_axi_awburst     (pcie_awburst),
        .m_axi_awlock      (),
        .m_axi_awcache     (),
        .m_axi_awprot      (),
        .m_axi_awvalid     (pcie_awvalid),
        .m_axi_awready     (pcie_awready),
        .m_axi_wdata       (pcie_wdata),
        .m_axi_wstrb       (pcie_wstrb),
        .m_axi_wlast       (pcie_wlast),
        .m_axi_wvalid      (pcie_wvalid),
        .m_axi_wready      (pcie_wready),
        .m_axi_bid         (pcie_bid),
        .m_axi_bresp       (pcie_bresp),
        .m_axi_bvalid      (pcie_bvalid),
        .m_axi_bready      (pcie_bready),
        .m_axi_arid        (pcie_arid),
        .m_axi_araddr      (pcie_araddr),
        .m_axi_arlen       (pcie_arlen),
        .m_axi_arsize      (pcie_arsize),
        .m_axi_arburst     (pcie_arburst),
        .m_axi_arlock      (),
        .m_axi_arcache     (),
        .m_axi_arprot      (),
        .m_axi_arvalid     (pcie_arvalid),
        .m_axi_arready     (pcie_arready),
        .m_axi_rid         (pcie_rid),
        .m_axi_rdata       (pcie_rdata),
        .m_axi_rresp       (pcie_rresp),
        .m_axi_rlast       (pcie_rlast),
        .m_axi_rvalid      (pcie_rvalid),
        .m_axi_rready      (pcie_rready),

        .psel              (apb_psel_o[4]),
        .penable           (apb_penable_o[4]),
        .pwrite            (apb_pwrite_o),
        .paddr             (apb_paddr_o),
        .pwdata            (apb_pwdata_o),
        .prdata            (apb_prdata[4]),
        .pready            (apb_pready_i[4]),
        .pslverr           (apb_pslverr_i[4]),

        .pcie_irq          (pcie_irq)
    );

    //=========================================================================
    // Doorbell
    //=========================================================================
    doorbell u_doorbell (
        .clk          (clk),
        .rst_n        (rst_n),
        .psel         (apb_psel_o[5]),
        .penable      (apb_penable_o[5]),
        .pwrite       (apb_pwrite_o),
        .paddr        (apb_paddr_o[11:0]),
        .pwdata       (apb_pwdata_o),
        .prdata       (apb_prdata[5]),
        .pready       (apb_pready_i[5]),
        .pslverr      (apb_pslverr_i[5]),
        .doorbell_irq (doorbell_irq)
    );

    //=========================================================================
    // Interrupt Controller
    //=========================================================================
    intc_top u_intc (
        .clk        (clk),
        .rst_n      (rst_n),
        .mxu_irq    (mxu_irq),
        .sfu_irq    (sfu_irq),
        .vector_irq (vec_irq),
        .dma_irq    (dma_irq),
        .pcie_irq   (pcie_irq),
        .host_irq   (doorbell_irq),
        .timer_irq  (timer_irq_i),
        .psel       (apb_psel_o[6]),
        .penable    (apb_penable_o[6]),
        .pwrite     (apb_pwrite_o),
        .paddr      (apb_paddr_o[11:0]),
        .pwdata     (apb_pwdata_o),
        .prdata     (apb_prdata[6]),
        .pready     (apb_pready_i[6]),
        .pslverr    (apb_pslverr_i[6]),
        .cpu_irq    (cpu_irq)
    );

endmodule

//=============================================================================
// APB master stub — exposes APB master signals for cocotb VPI driving
//=============================================================================
module ibex_apb_master_stub (
    input  wire        clk,
    input  wire        rst_n,

    // APB master (driven from outside via VPI)
    output wire [31:0] apb_paddr,
    output wire        apb_psel,
    output wire        apb_penable,
    output wire        apb_pwrite,
    output wire [31:0] apb_pwdata,
    input  wire [31:0] apb_prdata,
    input  wire        apb_pready,
    input  wire        apb_pslverr,

    input  wire        cpu_irq_i
);
    // Signals are driven directly by cocotb; default to idle.
    reg [31:0] apb_paddr_reg;
    reg        apb_psel_reg;
    reg        apb_penable_reg;
    reg        apb_pwrite_reg;
    reg [31:0] apb_pwdata_reg;

    initial begin
        apb_paddr_reg  = 32'd0;
        apb_psel_reg   = 1'b0;
        apb_penable_reg= 1'b0;
        apb_pwrite_reg = 1'b0;
        apb_pwdata_reg = 32'd0;
    end

    assign apb_paddr   = apb_paddr_reg;
    assign apb_psel    = apb_psel_reg;
    assign apb_penable = apb_penable_reg;
    assign apb_pwrite  = apb_pwrite_reg;
    assign apb_pwdata  = apb_pwdata_reg;
endmodule

//=============================================================================
// Behavioral APB slave stub — returns zero, no wait states, no errors
//=============================================================================
module apb_slave_stub (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        psel,
    input  wire        penable,
    input  wire        pwrite,
    input  wire [11:0] paddr,
    input  wire [31:0] pwdata,
    output wire [31:0] prdata,
    output wire        pready,
    output wire        pslverr
);
    assign prdata  = 32'd0;
    assign pready  = 1'b1;
    assign pslverr = 1'b0;
endmodule

`endif // USE_RTL_PCIE


module tb_mixed;

    //=========================================================================
    // Clock and Reset Parameters
    //=========================================================================
    localparam CLK_HALF       = 0.5;         // 1 GHz clock
    localparam RESET_CYCLES   = 5;

    //=========================================================================
    // DUT Signals
    //=========================================================================
    reg         clk;
    reg         rst_n;
    reg         timer_irq;

    // PCIe TLP RX (request from host to DUT)
    reg  [511:0] pcie_rx_req_tlp_data;
    reg  [127:0] pcie_rx_req_tlp_hdr;
    reg          pcie_rx_req_tlp_valid;
    reg          pcie_rx_req_tlp_sop;
    reg          pcie_rx_req_tlp_eop;
    wire         pcie_rx_req_tlp_ready;

    // PCIe TLP TX (completion from DUT)
    wire [511:0] pcie_tx_cpl_tlp_data;
    wire [15:0]  pcie_tx_cpl_tlp_strb;
    wire [127:0] pcie_tx_cpl_tlp_hdr;
    wire         pcie_tx_cpl_tlp_valid;
    wire         pcie_tx_cpl_tlp_sop;
    wire         pcie_tx_cpl_tlp_eop;
    reg          pcie_tx_cpl_tlp_ready;

    //=========================================================================
    // Test Infrastructure
    //=========================================================================
    reg  [63:0]  sim_cycle;
    integer      pass_cnt;
    integer      fail_cnt;
    event        sim_done;
    reg          sim_done_flag;

    //=========================================================================
    // Cocotb SRAM backdoor write interface
    //=========================================================================
    reg          sram_bkdoor_req;
    reg          sram_bkdoor_ack;
    reg  [15:0]  sram_bkdoor_addr;
    reg  [511:0] sram_bkdoor_wdata;

    initial begin
        sram_bkdoor_req   = 1'b0;
        sram_bkdoor_ack   = 1'b0;
        sram_bkdoor_addr  = 16'd0;
        sram_bkdoor_wdata = 512'd0;
    end

    always @(posedge clk) begin
        if (sram_bkdoor_req && !sram_bkdoor_ack) begin
            u_dut.u_sram_ctrl.mem[sram_bkdoor_addr] <= sram_bkdoor_wdata;
            sram_bkdoor_ack <= 1'b1;
        end else if (!sram_bkdoor_req) begin
            sram_bkdoor_ack <= 1'b0;
        end
    end

    //=========================================================================
    // Cocotb DRAM backdoor write interface
    //=========================================================================
    reg          dram_bkdoor_req;
    reg          dram_bkdoor_ack;
    reg  [16:0]  dram_bkdoor_addr;
    reg  [511:0] dram_bkdoor_wdata;

    initial begin
        dram_bkdoor_req   = 1'b0;
        dram_bkdoor_ack   = 1'b0;
        dram_bkdoor_addr  = 17'd0;
        dram_bkdoor_wdata = 512'd0;
    end

    always @(posedge clk) begin
        if (dram_bkdoor_req && !dram_bkdoor_ack) begin
            u_dut.u_dram_model.mem[dram_bkdoor_addr] <= dram_bkdoor_wdata;
            dram_bkdoor_ack <= 1'b1;
        end else if (!dram_bkdoor_req) begin
            dram_bkdoor_ack <= 1'b0;
        end
    end

    //=========================================================================
    // DUT: full SoC or reduced PCIe-only mixed-mode DUT
    //=========================================================================
`ifdef USE_RTL_PCIE
    caduceus_pcie_mixed_dut #(
        .CROSSBAR_MASTERS (6),
        .SRAM_SIZE        (32'd4194304),
        .DRAM_SIZE        (32'd2147483648)
    ) u_dut (
        .clk                     (clk),
        .rst_n                   (rst_n),

        // PCIe TLP ports
        .pcie_rx_req_tlp_data    (pcie_rx_req_tlp_data),
        .pcie_rx_req_tlp_hdr     (pcie_rx_req_tlp_hdr),
        .pcie_rx_req_tlp_valid   (pcie_rx_req_tlp_valid),
        .pcie_rx_req_tlp_sop     (pcie_rx_req_tlp_sop),
        .pcie_rx_req_tlp_eop     (pcie_rx_req_tlp_eop),
        .pcie_rx_req_tlp_ready   (pcie_rx_req_tlp_ready),

        .pcie_tx_cpl_tlp_data    (pcie_tx_cpl_tlp_data),
        .pcie_tx_cpl_tlp_strb    (pcie_tx_cpl_tlp_strb),
        .pcie_tx_cpl_tlp_hdr     (pcie_tx_cpl_tlp_hdr),
        .pcie_tx_cpl_tlp_valid   (pcie_tx_cpl_tlp_valid),
        .pcie_tx_cpl_tlp_sop     (pcie_tx_cpl_tlp_sop),
        .pcie_tx_cpl_tlp_eop     (pcie_tx_cpl_tlp_eop),
        .pcie_tx_cpl_tlp_ready   (pcie_tx_cpl_tlp_ready),

        .timer_irq_i             (timer_irq)
    );
`else
    caduceus_soc_top #(
        .CROSSBAR_MASTERS (6),
        .SRAM_SIZE        (32'd4194304),
        .DRAM_SIZE        (32'd2147483648)
    ) u_dut (
        .clk                     (clk),
        .rst_n                   (rst_n),

        // PCIe TLP ports
        .pcie_rx_req_tlp_data    (pcie_rx_req_tlp_data),
        .pcie_rx_req_tlp_hdr     (pcie_rx_req_tlp_hdr),
        .pcie_rx_req_tlp_valid   (pcie_rx_req_tlp_valid),
        .pcie_rx_req_tlp_sop     (pcie_rx_req_tlp_sop),
        .pcie_rx_req_tlp_eop     (pcie_rx_req_tlp_eop),
        .pcie_rx_req_tlp_ready   (pcie_rx_req_tlp_ready),

        .pcie_tx_cpl_tlp_data    (pcie_tx_cpl_tlp_data),
        .pcie_tx_cpl_tlp_strb    (pcie_tx_cpl_tlp_strb),
        .pcie_tx_cpl_tlp_hdr     (pcie_tx_cpl_tlp_hdr),
        .pcie_tx_cpl_tlp_valid   (pcie_tx_cpl_tlp_valid),
        .pcie_tx_cpl_tlp_sop     (pcie_tx_cpl_tlp_sop),
        .pcie_tx_cpl_tlp_eop     (pcie_tx_cpl_tlp_eop),
        .pcie_tx_cpl_tlp_ready   (pcie_tx_cpl_tlp_ready),

        .timer_irq_i             (timer_irq)
    );
`endif

    //=========================================================================
    // Clock Generation (1 GHz)
    //=========================================================================
    initial begin
        clk = 1'b0;
        forever #CLK_HALF clk = ~clk;
    end

    //=========================================================================
    // Cycle Counter
    //=========================================================================
    always @(posedge clk) begin
        if (sim_done_flag)
            sim_cycle <= sim_cycle;
        else
            sim_cycle <= sim_cycle + 1;
    end

    //=========================================================================
    // Reset Sequence
    //=========================================================================
    task automatic apply_reset;
        integer i;
    begin
        rst_n = 1'b0;
        for (i = 0; i < RESET_CYCLES; i = i + 1)
            @(posedge clk);
        rst_n = 1'b1;
    end
    endtask

    //=========================================================================
    // Mixed-mode define reporting (visible in simulation log)
    //=========================================================================
`ifdef USE_RTL_PCIE
    wire _mixed_pcie = 1'b1;
`else
    wire _mixed_pcie = 1'b0;
`endif
`ifdef USE_RTL_DMA
    wire _mixed_dma = 1'b1;
`else
    wire _mixed_dma = 1'b0;
`endif
`ifdef USE_RTL_MXU
    wire _mixed_mxu = 1'b1;
`else
    wire _mixed_mxu = 1'b0;
`endif
`ifdef USE_RTL_SFU
    wire _mixed_sfu = 1'b1;
`else
    wire _mixed_sfu = 1'b0;
`endif
`ifdef USE_RTL_VECTOR
    wire _mixed_vector = 1'b1;
`else
    wire _mixed_vector = 1'b0;
`endif

    //=========================================================================
    // Simulation Initialization
    //=========================================================================
    initial begin
        sim_cycle     = 0;
        sim_done_flag = 0;
        pass_cnt      = 0;
        fail_cnt      = 0;
        timer_irq     = 1'b0;

        // PCIe TLP RX — idle
        pcie_rx_req_tlp_data  = 512'd0;
        pcie_rx_req_tlp_hdr   = 128'd0;
        pcie_rx_req_tlp_valid = 1'b0;
        pcie_rx_req_tlp_sop   = 1'b0;
        pcie_rx_req_tlp_eop   = 1'b0;

        // PCIe TLP TX — always ready
        pcie_tx_cpl_tlp_ready = 1'b1;

`ifndef USE_RTL_PCIE
        // Zero-initialize Ibex DMEM (only present in full SoC)
        begin
            integer dmem_i;
            for (dmem_i = 0; dmem_i < 16384; dmem_i = dmem_i + 1)
                u_dut.u_ibex_wrapper.dmem[dmem_i] = 32'h0;
        end

        // Zero-initialize engine wrapper buffers (only present in full SoC)
        begin
            integer buf_i;
            for (buf_i = 0; buf_i < 32; buf_i = buf_i + 1)
                u_dut.u_mxu_wrapper.weight_buf[buf_i] = 512'h0;
            for (buf_i = 0; buf_i < 64; buf_i = buf_i + 1)
                u_dut.u_mxu_wrapper.activation_buf[buf_i] = 512'h0;
            u_dut.u_sfu_wrapper.rd_line_buf = 512'h0;
            u_dut.u_sfu_wrapper.wr_line_buf = 512'h0;
            u_dut.u_vector_wrapper.buf_a[0] = 4096'h0;
            u_dut.u_vector_wrapper.buf_b[0] = 4096'h0;
            u_dut.u_vector_wrapper.buf_o[0] = 4096'h0;
        end
`else
        // Zero-initialize SRAM/DRAM in reduced PCIe-only DUT so that
        // cocotb backdoor reads/writes never encounter X bits.
        begin
            integer sram_i;
            for (sram_i = 0; sram_i < 65536; sram_i = sram_i + 1)
                u_dut.u_sram_ctrl.mem[sram_i] = 512'h0;
        end
        begin
            integer dram_i;
            for (dram_i = 0; dram_i < 131072; dram_i = dram_i + 1)
                u_dut.u_dram_model.mem[dram_i] = 512'h0;
        end
`endif

        // Apply reset
        apply_reset();

        // Print configuration
        $display("");
        $display("============================================================");
        $display("[TB] tb_mixed — CaduceusCore Mixed-Mode Testbench");
        $display("[TB] Clock: 1 GHz (period = 1 ns)");
`ifdef USE_RTL_PCIE
        $display("[TB] DUT: caduceus_pcie_mixed_dut (PCIe-only RTL mode)");
`else
        $display("[TB] DUT: caduceus_soc_top (full SoC)");
`endif
        $display("[TB] Mixed-mode:");
        $display("[TB]   PCIe   RTL: %b", _mixed_pcie);
        $display("[TB]   DMA    RTL: %b", _mixed_dma);
        $display("[TB]   MXU    RTL: %b", _mixed_mxu);
        $display("[TB]   SFU    RTL: %b", _mixed_sfu);
        $display("[TB]   Vector RTL: %b", _mixed_vector);
        $display("[TB] When all are 1, this is identical to tb_soc.v");
        $display("============================================================");

        // Bootstrap check
        @(posedge clk);
        @(negedge clk);
        if (clk === 1'b0) begin
            $display("[PASS] Clock running at 1 GHz (t=%0t)", $time);
            pass_cnt = pass_cnt + 1;
        end else begin
            $display("[FAIL] Clock not toggling");
            fail_cnt = fail_cnt + 1;
        end

        if (rst_n === 1'b1) begin
            $display("[PASS] rst_n de-asserted after %0d cycles", RESET_CYCLES);
            pass_cnt = pass_cnt + 1;
        end else begin
            $display("[FAIL] rst_n still low after release");
            fail_cnt = fail_cnt + 1;
        end
        $display("");

        // Warm-up
        repeat (200) @(posedge clk);
        $display("[TB] SoC warm-up: %0d cycles elapsed", sim_cycle);
        $display("");

        // Cocotb or standalone mode
        if ($test$plusargs("COCOTB")) begin
            $display("[TB] COCOTB mode — waiting for Python control...");
            wait (sim_done_flag);
        end else begin
            repeat (500) @(posedge clk);
            $display("[TB] Standalone mode — basic sanity complete");
            $display("[TB] SIMULATION SUMMARY:");
            $display("[TB]   Passed: %0d  Failed: %0d  Cycles: %0d",
                     pass_cnt, fail_cnt, sim_cycle);
            $display("[TB]   RESULT: %s", fail_cnt == 0 ? "PASS" : "FAIL");
            $finish;
        end
    end

    //=========================================================================
    // Simulation Timeout (100,000,000 ns = 100M cycles @ 1 GHz)
    //=========================================================================
    initial begin
        #100000000;
        if (!sim_done_flag) begin
            $display("[TMO] Simulation timeout after 100,000,000 ns");
            $display("FAIL: TIMEOUT");
            $finish;
        end
    end

    //=========================================================================
    // Waveform Dump
    //=========================================================================
`ifdef FSDB
    initial begin
        $fsdbDumpfile("tb_mixed.fsdb");
        $fsdbDumpvars(0, tb_mixed);
    end
`endif

endmodule
