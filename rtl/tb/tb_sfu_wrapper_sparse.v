//=============================================================================
// tb_sfu_wrapper_sparse — SFU Wrapper BUG-005 X-propagation Testbench
//=============================================================================
// Task: wrapper-level-verification / T5 (Wave 2)
//
// Instantiates sfu_soc_wrapper + axi_sparse_slave.v with a mux so that
// either the wrapper's m_axi_* (sparse_sel=0) or an external cocotb
// AxiMaster (sparse_sel=1) drives the sparse slave.
//
// cocotb test flow:
//   1. sparse_sel=1 → AxiMaster writes valid data to specific bytes
//   2. sparse_sel=0 → wrapper reads from sparse slave, X in uninit bytes
//   3. APB: configure and start SFU, check output for X propagation
//=============================================================================

`timescale 1ns / 1ps

module tb_sfu_wrapper_sparse;

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam integer AXI_ID_WIDTH   = 8;
    localparam integer AXI_ADDR_WIDTH = 32;
    localparam integer AXI_DATA_WIDTH = 512;
    localparam integer SFU_ADDR_WIDTH = 32;

    //=========================================================================
    // Clock and Reset
    //=========================================================================
    reg clk;
    reg rst_n;

    initial clk = 1'b0;
    always #5 clk = ~clk;

    initial begin
        rst_n = 1'b0;
        #20 rst_n = 1'b1;
    end

    //=========================================================================
    // APB slave interface
    //=========================================================================
    wire        apb_psel;
    wire        apb_penable;
    wire        apb_pwrite;
    wire [11:0] apb_paddr;
    wire [31:0] apb_pwdata;
    wire [31:0] apb_prdata;
    wire        apb_pready;
    wire        apb_pslverr;
    wire [3:0]  apb_pstrb;

    assign apb_pstrb = 4'b0;

    //=========================================================================
    // Wrapper AXI4 master signals
    //=========================================================================
    wire [AXI_ID_WIDTH-1:0]    w_axi_awid;
    wire [AXI_ADDR_WIDTH-1:0]  w_axi_awaddr;
    wire [7:0]                 w_axi_awlen;
    wire [2:0]                 w_axi_awsize;
    wire [1:0]                 w_axi_awburst;
    wire                       w_axi_awvalid;
    wire                       w_axi_awready;

    wire [AXI_DATA_WIDTH-1:0]  w_axi_wdata;
    wire [AXI_DATA_WIDTH/8-1:0] w_axi_wstrb;
    wire                       w_axi_wlast;
    wire                       w_axi_wvalid;
    wire                       w_axi_wready;

    wire [AXI_ID_WIDTH-1:0]    w_axi_bid;
    wire [1:0]                 w_axi_bresp;
    wire                       w_axi_bvalid;
    wire                       w_axi_bready;

    wire [AXI_ID_WIDTH-1:0]    w_axi_arid;
    wire [AXI_ADDR_WIDTH-1:0]  w_axi_araddr;
    wire [7:0]                 w_axi_arlen;
    wire [2:0]                 w_axi_arsize;
    wire [1:0]                 w_axi_arburst;
    wire                       w_axi_arvalid;
    wire                       w_axi_arready;

    wire [AXI_ID_WIDTH-1:0]    w_axi_rid;
    wire [AXI_DATA_WIDTH-1:0]  w_axi_rdata;
    wire [1:0]                 w_axi_rresp;
    wire                       w_axi_rlast;
    wire                       w_axi_rvalid;
    wire                       w_axi_rready;

    //=========================================================================
    // External AXI4 master signals (cocotb AxiMaster for preload, "e_axi_*")
    //=========================================================================
    wire                       e_axi_awvalid;
    wire                       e_axi_awready;
    wire [AXI_ID_WIDTH-1:0]    e_axi_awid;
    wire [AXI_ADDR_WIDTH-1:0]  e_axi_awaddr;
    wire [7:0]                 e_axi_awlen;
    wire [2:0]                 e_axi_awsize;
    wire [1:0]                 e_axi_awburst;

    wire                       e_axi_wvalid;
    wire                       e_axi_wready;
    wire [AXI_DATA_WIDTH-1:0]  e_axi_wdata;
    wire [AXI_DATA_WIDTH/8-1:0] e_axi_wstrb;
    wire                       e_axi_wlast;

    wire                       e_axi_bvalid;
    wire                       e_axi_bready;
    wire [AXI_ID_WIDTH-1:0]    e_axi_bid;
    wire [1:0]                 e_axi_bresp;

    wire                       e_axi_arvalid;
    wire                       e_axi_arready;
    wire [AXI_ID_WIDTH-1:0]    e_axi_arid;
    wire [AXI_ADDR_WIDTH-1:0]  e_axi_araddr;
    wire [7:0]                 e_axi_arlen;
    wire [2:0]                 e_axi_arsize;
    wire [1:0]                 e_axi_arburst;

    wire                       e_axi_rvalid;
    wire                       e_axi_rready;
    wire [AXI_ID_WIDTH-1:0]    e_axi_rid;
    wire [AXI_DATA_WIDTH-1:0]  e_axi_rdata;
    wire [1:0]                 e_axi_rresp;
    wire                       e_axi_rlast;

    //=========================================================================
    // Mux control: sparse_sel=1 routes ext_axi to slave; 0 routes wrapper
    //=========================================================================
    wire sparse_sel;

    //=========================================================================
    // Muxed slave-side AXI signals (driven to axi_sparse_slave s_axi_*)
    //=========================================================================
    wire [AXI_ID_WIDTH-1:0]    s_axi_awid;
    wire [AXI_ADDR_WIDTH-1:0]  s_axi_awaddr;
    wire [7:0]                 s_axi_awlen;
    wire [2:0]                 s_axi_awsize;
    wire [1:0]                 s_axi_awburst;
    wire                       s_axi_awvalid;
    wire                       s_axi_awready;

    wire [AXI_DATA_WIDTH-1:0]  s_axi_wdata;
    wire [AXI_DATA_WIDTH/8-1:0] s_axi_wstrb;
    wire                       s_axi_wlast;
    wire                       s_axi_wvalid;
    wire                       s_axi_wready;

    wire [AXI_ID_WIDTH-1:0]    s_axi_bid;
    wire [1:0]                 s_axi_bresp;
    wire                       s_axi_bvalid;
    wire                       s_axi_bready;

    wire [AXI_ID_WIDTH-1:0]    s_axi_arid;
    wire [AXI_ADDR_WIDTH-1:0]  s_axi_araddr;
    wire [7:0]                 s_axi_arlen;
    wire [2:0]                 s_axi_arsize;
    wire [1:0]                 s_axi_arburst;
    wire                       s_axi_arvalid;
    wire                       s_axi_arready;

    wire [AXI_ID_WIDTH-1:0]    s_axi_rid;
    wire [AXI_DATA_WIDTH-1:0]  s_axi_rdata;
    wire [1:0]                 s_axi_rresp;
    wire                       s_axi_rlast;
    wire                       s_axi_rvalid;
    wire                       s_axi_rready;

    //── Master-to-Slave mux (wrapper or ext_axi drives slave inputs) ────────
    assign s_axi_awid    = sparse_sel ? e_axi_awid    : w_axi_awid;
    assign s_axi_awaddr  = sparse_sel ? e_axi_awaddr  : w_axi_awaddr;
    assign s_axi_awlen   = sparse_sel ? e_axi_awlen   : w_axi_awlen;
    assign s_axi_awsize  = sparse_sel ? e_axi_awsize  : w_axi_awsize;
    assign s_axi_awburst = sparse_sel ? e_axi_awburst : w_axi_awburst;
    assign s_axi_awvalid = sparse_sel ? e_axi_awvalid : w_axi_awvalid;

    assign s_axi_wdata   = sparse_sel ? e_axi_wdata   : w_axi_wdata;
    assign s_axi_wstrb   = sparse_sel ? e_axi_wstrb   : w_axi_wstrb;
    assign s_axi_wlast   = sparse_sel ? e_axi_wlast   : w_axi_wlast;
    assign s_axi_wvalid  = sparse_sel ? e_axi_wvalid  : w_axi_wvalid;

    assign s_axi_bready  = sparse_sel ? e_axi_bready  : w_axi_bready;
    assign s_axi_arid    = sparse_sel ? e_axi_arid    : w_axi_arid;
    assign s_axi_araddr  = sparse_sel ? e_axi_araddr  : w_axi_araddr;
    assign s_axi_arlen   = sparse_sel ? e_axi_arlen   : w_axi_arlen;
    assign s_axi_arsize  = sparse_sel ? e_axi_arsize  : w_axi_arsize;
    assign s_axi_arburst = sparse_sel ? e_axi_arburst : w_axi_arburst;
    assign s_axi_arvalid = sparse_sel ? e_axi_arvalid : w_axi_arvalid;
    assign s_axi_rready  = sparse_sel ? e_axi_rready  : w_axi_rready;

    //── Slave-to-Master mux (slave outputs route to active master) ──────────
    assign w_axi_awready = sparse_sel ? 1'b0          : s_axi_awready;
    assign w_axi_wready  = sparse_sel ? 1'b0          : s_axi_wready;
    assign w_axi_bid     = sparse_sel ? 8'd0          : s_axi_bid;
    assign w_axi_bresp   = sparse_sel ? 2'd0          : s_axi_bresp;
    assign w_axi_bvalid  = sparse_sel ? 1'b0          : s_axi_bvalid;
    assign w_axi_arready = sparse_sel ? 1'b0          : s_axi_arready;
    assign w_axi_rid     = sparse_sel ? 8'd0          : s_axi_rid;
    assign w_axi_rdata   = sparse_sel ? 512'd0        : s_axi_rdata;
    assign w_axi_rresp   = sparse_sel ? 2'd0          : s_axi_rresp;
    assign w_axi_rlast   = sparse_sel ? 1'b0          : s_axi_rlast;
    assign w_axi_rvalid  = sparse_sel ? 1'b0          : s_axi_rvalid;

    assign e_axi_awready = sparse_sel ? s_axi_awready : 1'b0;
    assign e_axi_wready  = sparse_sel ? s_axi_wready  : 1'b0;
    assign e_axi_bid     = sparse_sel ? s_axi_bid     : 8'd0;
    assign e_axi_bresp   = sparse_sel ? s_axi_bresp   : 2'd0;
    assign e_axi_bvalid  = sparse_sel ? s_axi_bvalid  : 1'b0;
    assign e_axi_arready = sparse_sel ? s_axi_arready : 1'b0;
    assign e_axi_rid     = sparse_sel ? s_axi_rid     : 8'd0;
    assign e_axi_rdata   = sparse_sel ? s_axi_rdata   : 512'd0;
    assign e_axi_rresp   = sparse_sel ? s_axi_rresp   : 2'd0;
    assign e_axi_rlast   = sparse_sel ? s_axi_rlast   : 1'b0;
    assign e_axi_rvalid  = sparse_sel ? s_axi_rvalid  : 1'b0;

    //=========================================================================
    // DUT: sfu_soc_wrapper
    //=========================================================================
    wire irq;
    sfu_soc_wrapper #(
        .AXI_ID_WIDTH  (AXI_ID_WIDTH),
        .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
        .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
        .SFU_ADDR_WIDTH(SFU_ADDR_WIDTH)
    ) u_wrapper (
        .clk          (clk),
        .rst_n        (rst_n),
        .psel         (apb_psel),
        .penable      (apb_penable),
        .pwrite       (apb_pwrite),
        .paddr        (apb_paddr),
        .pwdata       (apb_pwdata),
        .prdata       (apb_prdata),
        .pready       (apb_pready),
        .pslverr      (apb_pslverr),
        .m_axi_awid   (w_axi_awid),
        .m_axi_awaddr (w_axi_awaddr),
        .m_axi_awlen  (w_axi_awlen),
        .m_axi_awsize (w_axi_awsize),
        .m_axi_awburst(w_axi_awburst),
        .m_axi_awvalid(w_axi_awvalid),
        .m_axi_awready(w_axi_awready),
        .m_axi_wdata  (w_axi_wdata),
        .m_axi_wstrb  (w_axi_wstrb),
        .m_axi_wlast  (w_axi_wlast),
        .m_axi_wvalid (w_axi_wvalid),
        .m_axi_wready (w_axi_wready),
        .m_axi_bid    (w_axi_bid),
        .m_axi_bresp  (w_axi_bresp),
        .m_axi_bvalid (w_axi_bvalid),
        .m_axi_bready (w_axi_bready),
        .m_axi_arid   (w_axi_arid),
        .m_axi_araddr (w_axi_araddr),
        .m_axi_arlen  (w_axi_arlen),
        .m_axi_arsize (w_axi_arsize),
        .m_axi_arburst(w_axi_arburst),
        .m_axi_arvalid(w_axi_arvalid),
        .m_axi_arready(w_axi_arready),
        .m_axi_rid    (w_axi_rid),
        .m_axi_rdata  (w_axi_rdata),
        .m_axi_rresp  (w_axi_rresp),
        .m_axi_rlast  (w_axi_rlast),
        .m_axi_rvalid (w_axi_rvalid),
        .m_axi_rready (w_axi_rready),
        .irq          (irq)
    );

    //=========================================================================
    // Sparse AXI4 slave (uninitialized memory → X propagation)
    //=========================================================================
    axi_sparse_slave #(
        .DATA_W(AXI_DATA_WIDTH),
        .ADDR_W(AXI_ADDR_WIDTH),
        .ID_W  (AXI_ID_WIDTH)
    ) u_sparse (
        .clk           (clk),
        .rst_n         (rst_n),
        .s_axi_awid    (s_axi_awid),
        .s_axi_awaddr  (s_axi_awaddr),
        .s_axi_awlen   (s_axi_awlen),
        .s_axi_awsize  (s_axi_awsize),
        .s_axi_awburst (s_axi_awburst),
        .s_axi_awvalid (s_axi_awvalid),
        .s_axi_awready (s_axi_awready),
        .s_axi_wdata   (s_axi_wdata),
        .s_axi_wstrb   (s_axi_wstrb),
        .s_axi_wlast   (s_axi_wlast),
        .s_axi_wvalid  (s_axi_wvalid),
        .s_axi_wready  (s_axi_wready),
        .s_axi_bid     (s_axi_bid),
        .s_axi_bresp   (s_axi_bresp),
        .s_axi_bvalid  (s_axi_bvalid),
        .s_axi_bready  (s_axi_bready),
        .s_axi_arid    (s_axi_arid),
        .s_axi_araddr  (s_axi_araddr),
        .s_axi_arlen   (s_axi_arlen),
        .s_axi_arsize  (s_axi_arsize),
        .s_axi_arburst (s_axi_arburst),
        .s_axi_arvalid (s_axi_arvalid),
        .s_axi_arready (s_axi_arready),
        .s_axi_rid     (s_axi_rid),
        .s_axi_rdata   (s_axi_rdata),
        .s_axi_rresp   (s_axi_rresp),
        .s_axi_rlast   (s_axi_rlast),
        .s_axi_rvalid  (s_axi_rvalid),
        .s_axi_rready  (s_axi_rready)
    );

endmodule
