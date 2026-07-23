//=============================================================================
// tb_mxu_wrapper — MXU SoC Wrapper Cocotb Testbench
//=============================================================================
// Task: wrapper-level-verification / T1 scaffolding
//
// Instantiates mxu_soc_wrapper (which contains apb_to_mmio → mxu_top).
// TB exposes APB slave signals with apb_* prefix for cocotbext-axi ApbBus.
// AXI4 master ports are exposed as m_axi_* for cocotb AxiBus.from_prefix().
// Debug ports are exposed at TB top level for cocotb monitoring.
//
// apb_pstrb is tied to 4'b0 (wrapper APB port does not handle strobe,
// but cocotbext-axi ApbBus requires the signal).
//
// Must NOT instantiate apb_to_mmio separately — it is inside the wrapper.
// Must NOT instantiate crossbar, DRAM, CPU, or any SoC component.
//=============================================================================

`timescale 1ns / 1ps

module tb_mxu_wrapper;

    //=========================================================================
    // Parameters (match mxu_soc_wrapper defaults)
    //=========================================================================
    localparam integer AXI_ID_WIDTH   = 8;
    localparam integer AXI_ADDR_WIDTH = 32;
    localparam integer AXI_DATA_WIDTH = 512;
    localparam integer K_TILE_MAX     = 64;
    localparam integer W_BUF_DEPTH    = 64;
    localparam integer A_BUF_DEPTH    = 128;

    //=========================================================================
    // Clock and Reset
    //=========================================================================
    reg clk;
    reg rst_n;

    initial clk = 1'b0;
    always #5 clk = ~clk;  // 100 MHz

    initial begin
        rst_n = 1'b0;
        #20 rst_n = 1'b1;
    end

    //=========================================================================
    // APB slave interface (apb_* prefix for cocotbext-axi ApbBus)
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
    // AXI4 master interface (m_axi_* prefix for cocotbext-axi AxiBus)
    //=========================================================================
    // Write Address
    wire [AXI_ID_WIDTH-1:0]    m_axi_awid;
    wire [AXI_ADDR_WIDTH-1:0]  m_axi_awaddr;
    wire [7:0]                 m_axi_awlen;
    wire [2:0]                 m_axi_awsize;
    wire [1:0]                 m_axi_awburst;
    wire                       m_axi_awvalid;
    wire                       m_axi_awready;

    // Write Data
    wire [AXI_DATA_WIDTH-1:0]  m_axi_wdata;
    wire [AXI_DATA_WIDTH/8-1:0] m_axi_wstrb;
    wire                       m_axi_wlast;
    wire                       m_axi_wvalid;
    wire                       m_axi_wready;

    // Write Response
    wire [AXI_ID_WIDTH-1:0]    m_axi_bid;
    wire [1:0]                 m_axi_bresp;
    wire                       m_axi_bvalid;
    wire                       m_axi_bready;

    // Read Address
    wire [AXI_ID_WIDTH-1:0]    m_axi_arid;
    wire [AXI_ADDR_WIDTH-1:0]  m_axi_araddr;
    wire [7:0]                 m_axi_arlen;
    wire [2:0]                 m_axi_arsize;
    wire [1:0]                 m_axi_arburst;
    wire                       m_axi_arvalid;
    wire                       m_axi_arready;

    // Read Data
    wire [AXI_ID_WIDTH-1:0]    m_axi_rid;
    wire [AXI_DATA_WIDTH-1:0]  m_axi_rdata;
    wire [1:0]                 m_axi_rresp;
    wire                       m_axi_rlast;
    wire                       m_axi_rvalid;
    wire                       m_axi_rready;

    //=========================================================================
    // Interrupt
    //=========================================================================
    wire irq;

    //=========================================================================
    // Debug ports (exposed for cocotb monitoring)
    //=========================================================================
    wire [3:0]  dbg_state;
    wire        dbg_compute_en;
    wire        dbg_weight_load;
    wire        dbg_activation_load;
    wire        dbg_store_out;
    wire [5:0]  dbg_store_row;
    wire [5:0]  dbg_compute_k;
    wire [15:0] dbg_tiles_completed;

    //=========================================================================
    // DUT: mxu_soc_wrapper
    //=========================================================================
    mxu_soc_wrapper #(
        .AXI_ID_WIDTH  (AXI_ID_WIDTH),
        .AXI_ADDR_WIDTH(AXI_ADDR_WIDTH),
        .AXI_DATA_WIDTH(AXI_DATA_WIDTH),
        .K_TILE_MAX    (K_TILE_MAX),
        .W_BUF_DEPTH   (W_BUF_DEPTH),
        .A_BUF_DEPTH   (A_BUF_DEPTH)
    ) u_dut (
        .clk               (clk),
        .rst_n             (rst_n),
        .psel              (apb_psel),
        .penable           (apb_penable),
        .pwrite            (apb_pwrite),
        .paddr             (apb_paddr),
        .pwdata            (apb_pwdata),
        .prdata            (apb_prdata),
        .pready            (apb_pready),
        .pslverr           (apb_pslverr),
        .m_axi_awid        (m_axi_awid),
        .m_axi_awaddr      (m_axi_awaddr),
        .m_axi_awlen       (m_axi_awlen),
        .m_axi_awsize      (m_axi_awsize),
        .m_axi_awburst     (m_axi_awburst),
        .m_axi_awvalid     (m_axi_awvalid),
        .m_axi_awready     (m_axi_awready),
        .m_axi_wdata       (m_axi_wdata),
        .m_axi_wstrb       (m_axi_wstrb),
        .m_axi_wlast       (m_axi_wlast),
        .m_axi_wvalid      (m_axi_wvalid),
        .m_axi_wready      (m_axi_wready),
        .m_axi_bid         (m_axi_bid),
        .m_axi_bresp       (m_axi_bresp),
        .m_axi_bvalid      (m_axi_bvalid),
        .m_axi_bready      (m_axi_bready),
        .m_axi_arid        (m_axi_arid),
        .m_axi_araddr      (m_axi_araddr),
        .m_axi_arlen       (m_axi_arlen),
        .m_axi_arsize      (m_axi_arsize),
        .m_axi_arburst     (m_axi_arburst),
        .m_axi_arvalid     (m_axi_arvalid),
        .m_axi_arready     (m_axi_arready),
        .m_axi_rid         (m_axi_rid),
        .m_axi_rdata       (m_axi_rdata),
        .m_axi_rresp       (m_axi_rresp),
        .m_axi_rlast       (m_axi_rlast),
        .m_axi_rvalid      (m_axi_rvalid),
        .m_axi_rready      (m_axi_rready),
        .irq               (irq),
        .dbg_state         (dbg_state),
        .dbg_compute_en    (dbg_compute_en),
        .dbg_weight_load   (dbg_weight_load),
        .dbg_activation_load(dbg_activation_load),
        .dbg_store_out     (dbg_store_out),
        .dbg_store_row     (dbg_store_row),
        .dbg_compute_k     (dbg_compute_k),
        .dbg_tiles_completed(dbg_tiles_completed)
    );

endmodule
