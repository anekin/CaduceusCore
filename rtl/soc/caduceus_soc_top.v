//=============================================================================
// caduceus_soc_top — CaduceusCore NPU SoC Top-Level Integration
//=============================================================================
// SoC Phase 3-4 / Task 13
//
// Instantiated modules (from prior tasks):
//   T1  boot_rom          — inside ibex_wrapper (64KB ROM, 0x0000_0000)
//   T2  sram_ctrl         — AXI4 slave, 4MB SRAM at 0x2000_0000
//   T3  apb_decoder       — 1→7 APB decoder at 0x4000_0000
//   T4  ibex_wrapper      — Ibex RV32IMC core + AXI4/APB masters
//   T5  mxu_soc_wrapper   — MXU engine (APB slave + AXI4 master)
//   T5  sfu_soc_wrapper   — SFU engine (APB slave + AXI4 master)
//   T5  vector_soc_wrapper— Vector engine (APB slave + AXI4 master)
//   T6  intc_top          — 7-source interrupt controller
//   T7  axi_crossbar      — M=6, S=2 AXI4 crossbar
//   T8  pcie_ep_wrapper   — PCIe endpoint (cocotbext-pcie host model)
//   T9  dram_model        — Behavioral DRAM model at 0x8000_0000
//   TD  doorbell          — Host↔NPU ring buffer doorbell
//   T11 dma_wrapper       — axi_cdma DMA engine
//
// Interconnects:
//   - Data (AXI4): 6 masters → crossbar → SRAM(port0) + DRAM(port1)
//   - MMIO (APB):  ibex APB → apb_decoder → 7 slaves
//   - IRQ:         all module irqs → intc_top → cpu_irq → ibex
//   - Clock/Reset: single clk @1GHz, rst_n async assert, sync de-assert
//
// Parameters: CROSSBAR_MASTERS=6, SRAM_SIZE=32'd4194304, DRAM_SIZE=32'd2147483648
//
// VCS compile:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps
//       -f rtl/cpu/ibex.flist -f rtl/ip/verilog-axi.flist
//       -f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist
//       -top caduceus_soc_top -o simv_soc_top -l elaborate.log
// NOTE: ibex.flist must come FIRST (contains ibex_pkg.sv)
//=============================================================================

`resetall
`timescale 1ns / 1ps
`default_nettype none

module caduceus_soc_top #(
    parameter int unsigned CROSSBAR_MASTERS = 6,
    parameter int unsigned SRAM_SIZE        = 32'd4194304,   // 4 MB
    parameter int unsigned DRAM_SIZE        = 32'd2147483648 // 2 GB
) (
    // ── Clock / Reset ─────────────────────────────────────────────────────
    input  wire        clk,
    input  wire        rst_n,          // async assert, sync de-assert

    // ── PCIe TLP ports (exposed for cocotbext-pcie host model) ────────────
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

    // ── Timer interrupt (external; tie to 0 if unused) ────────────────────
    input  wire         timer_irq_i
);

    //=========================================================================
    // Local parameters (derived from crossbar)
    //=========================================================================
    localparam int unsigned CB_NUM_M      = 6;
    localparam int unsigned CB_NUM_S      = 2;
    localparam int unsigned CB_DATA_WIDTH = 512;
    localparam int unsigned CB_ADDR_WIDTH = 32;
    localparam int unsigned CB_M_ID_WIDTH = 6;
    localparam int unsigned CB_MSEL_WIDTH = 3;
    localparam int unsigned CB_S_ID_WIDTH = CB_M_ID_WIDTH + CB_MSEL_WIDTH;  // 9

    //=========================================================================
    // AXI4 Crossbar — Master-side packed buses (M=6)
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
    // AXI4 Crossbar — Slave-side packed buses (S=2)
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
    // Ibex AXI4 Master (32-bit) → width adapter input side
    //=========================================================================
    wire [3:0]   ibex_awid;
    wire [31:0]  ibex_awaddr;
    wire [7:0]   ibex_awlen;
    wire [2:0]   ibex_awsize;
    wire [1:0]   ibex_awburst;
    wire         ibex_awvalid;
    wire         ibex_awready;
    wire [31:0]  ibex_wdata;
    wire [3:0]   ibex_wstrb;
    wire         ibex_wlast;
    wire         ibex_wvalid;
    wire         ibex_wready;
    wire [3:0]   ibex_bid;
    wire [1:0]   ibex_bresp;
    wire         ibex_bvalid;
    wire         ibex_bready;
    wire [3:0]   ibex_arid;
    wire [31:0]  ibex_araddr;
    wire [7:0]   ibex_arlen;
    wire [2:0]   ibex_arsize;
    wire [1:0]   ibex_arburst;
    wire         ibex_arvalid;
    wire         ibex_arready;
    wire [3:0]   ibex_rid;
    wire [31:0]  ibex_rdata;
    wire [1:0]   ibex_rresp;
    wire         ibex_rlast;
    wire         ibex_rvalid;
    wire         ibex_rready;

    //=========================================================================
    // AXI4 Width Adapter (ibex 32-bit → crossbar 512-bit) output side
    //=========================================================================
    wire [5:0]   adapt_awid;
    wire [31:0]  adapt_awaddr;
    wire [7:0]   adapt_awlen;
    wire [2:0]   adapt_awsize;
    wire [1:0]   adapt_awburst;
    wire         adapt_awvalid;
    wire         adapt_awready;
    wire [511:0] adapt_wdata;
    wire [63:0]  adapt_wstrb;
    wire         adapt_wlast;
    wire         adapt_wvalid;
    wire         adapt_wready;
    wire [5:0]   adapt_bid;
    wire [1:0]   adapt_bresp;
    wire         adapt_bvalid;
    wire         adapt_bready;
    wire [5:0]   adapt_arid;
    wire [31:0]  adapt_araddr;
    wire [7:0]   adapt_arlen;
    wire [2:0]   adapt_arsize;
    wire [1:0]   adapt_arburst;
    wire         adapt_arvalid;
    wire         adapt_arready;
    wire [5:0]   adapt_rid;
    wire [511:0] adapt_rdata;
    wire [1:0]   adapt_rresp;
    wire         adapt_rlast;
    wire         adapt_rvalid;
    wire         adapt_rready;

    // Adapter tie-offs for optional AXI signals (ibex_wrapper doesn't drive these)
    wire         tie_awlock  = 1'b0;
    wire [3:0]   tie_awcache = 4'h0;
    wire [2:0]   tie_awprot  = 3'h0;
    wire [3:0]   tie_awqos   = 4'h0;
    wire [3:0]   tie_awregion= 4'h0;
    wire         tie_arlock  = 1'b0;
    wire [3:0]   tie_arcache = 4'h0;
    wire [2:0]   tie_arprot  = 3'h0;
    wire [3:0]   tie_arqos   = 4'h0;
    wire [3:0]   tie_arregion= 4'h0;
    // Adapter user/unused output sinks (1-bit when xxxUSER_ENABLE=0)
    wire         adapter_unused_awlock,   adapter_unused_awcache3, adapter_unused_awcache2,
                 adapter_unused_awcache1, adapter_unused_awcache0;
    wire         adapter_unused_awprot2,  adapter_unused_awprot1,  adapter_unused_awprot0;
    wire         adapter_unused_awqos3,   adapter_unused_awqos2,   adapter_unused_awqos1,  adapter_unused_awqos0;
    wire         adapter_unused_awregion3,adapter_unused_awregion2,adapter_unused_awregion1,adapter_unused_awregion0;
    wire         adapter_unused_awuser,   adapter_unused_wuser,    adapter_unused_buser;
    wire         adapter_unused_arlock,   adapter_unused_arcache3, adapter_unused_arcache2,
                 adapter_unused_arcache1, adapter_unused_arcache0;
    wire         adapter_unused_arprot2,  adapter_unused_arprot1,  adapter_unused_arprot0;
    wire         adapter_unused_arqos3,   adapter_unused_arqos2,   adapter_unused_arqos1,  adapter_unused_arqos0;
    wire         adapter_unused_arregion3,adapter_unused_arregion2,adapter_unused_arregion1,adapter_unused_arregion0;
    wire         adapter_unused_aruser,   adapter_unused_ruser;

    //=========================================================================
    // Engine Wrapper AXI4 Master wires (512-bit, ID=8 → truncated to 6)
    //=========================================================================
    // MXU (master 1)
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

    // SFU (master 2)
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

    // Vector (master 3)
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

    // DMA (master 4)
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

    // PCIe (master 5)
    wire [5:0]   pcie_awid_6;   wire [31:0] pcie_awaddr;  wire [7:0]  pcie_awlen;
    wire [2:0]   pcie_awsize;   wire [1:0]  pcie_awburst; wire        pcie_awvalid;
    wire         pcie_ax_awready;wire [511:0]pcie_wdata;   wire [63:0] pcie_wstrb;
    wire         pcie_wlast;    wire        pcie_wvalid;  wire        pcie_ax_wready;
    wire [5:0]   pcie_bid_6;    wire [1:0]  pcie_bresp;   wire        pcie_bvalid;
    wire         pcie_bready;   wire [5:0]  pcie_arid_6;  wire [31:0] pcie_araddr;
    wire [7:0]   pcie_arlen;    wire [2:0]  pcie_arsize;  wire [1:0]  pcie_arburst;
    wire         pcie_arvalid;  wire        pcie_ax_arready;wire [5:0] pcie_rid_6;
    wire [511:0] pcie_rdata;    wire [1:0]  pcie_rresp;   wire        pcie_rlast;
    wire         pcie_rvalid;   wire        pcie_rready;
    // PCIe extra outputs (awlock/cache/prot/arlock/cache/prot — unused by crossbar)
    wire         pcie_awlock_unused, pcie_awcache_unused3, pcie_awcache_unused2,
                 pcie_awcache_unused1, pcie_awcache_unused0;
    wire         pcie_awprot_unused2, pcie_awprot_unused1, pcie_awprot_unused0;
    wire         pcie_arlock_unused, pcie_arcache_unused3, pcie_arcache_unused2,
                 pcie_arcache_unused1, pcie_arcache_unused0;
    wire         pcie_arprot_unused2, pcie_arprot_unused1, pcie_arprot_unused0;

    //=========================================================================
    // APB Bus (ibex APB master → apb_decoder)
    //=========================================================================
    wire [31:0]  apb_m_paddr;
    wire         apb_m_psel;
    wire         apb_m_penable;
    wire         apb_m_pwrite;
    wire [31:0]  apb_m_pwdata;
    wire [31:0]  apb_m_prdata;
    wire         apb_m_pready;
    wire         apb_m_pslverr;

    // APB decoder → slaves (7 ports)
    wire [6:0]   apb_psel_o;
    wire [6:0]   apb_penable_o;
    wire [31:0]  apb_paddr_o;
    wire         apb_pwrite_o;
    wire [31:0]  apb_pwdata_o;

    // APB slave response (per slave)
    wire [6:0]   apb_pready_i;
    wire [6:0]   apb_pslverr_i;
    wire [31:0]  apb_prdata [0:6];  // unpacked array, matching apb_decoder prdata_i

    //=========================================================================
    // Interrupt wires
    //=========================================================================
    wire mxu_irq;
    wire sfu_irq;
    wire vec_irq;
    wire dma_irq;
    wire pcie_irq;
    wire doorbell_irq;
    wire cpu_irq;

    //=========================================================================
    // MXU debug ports (unused at SoC level; tie off for lint cleanliness)
    //=========================================================================
    wire [3:0]  mxu_dbg_state;
    wire        mxu_dbg_compute_en;
    wire        mxu_dbg_weight_load;
    wire        mxu_dbg_activation_load;
    wire        mxu_dbg_store_out;
    wire [5:0]  mxu_dbg_store_row;
    wire [5:0]  mxu_dbg_compute_k;
    wire [15:0] mxu_dbg_tiles_completed;

    //==========================================================================
    //                                INSTANTIATIONS
    //==========================================================================

    //─────────────────────────────────────────────────────────────────────────
    // AXI4 Width Adapter: ibex_wrapper (32-bit) → crossbar (512-bit)
    //─────────────────────────────────────────────────────────────────────────
    // Uses alexforencich/verilog-axi axi_adapter (MIT-licensed).
    // Converts 32-bit Ibex AXI4 to 512-bit for the crossbar.
    // The adapted output connects to crossbar master port 0.
    axi_adapter #(
        .ADDR_WIDTH          (32),
        .S_DATA_WIDTH        (32),
        .S_STRB_WIDTH        (4),
        .M_DATA_WIDTH        (512),
        .M_STRB_WIDTH        (64),
        .ID_WIDTH            (4),
        .AWUSER_ENABLE       (0),
        .WUSER_ENABLE        (0),
        .BUSER_ENABLE        (0),
        .ARUSER_ENABLE       (0),
        .RUSER_ENABLE        (0),
        .CONVERT_BURST       (1),
        .CONVERT_NARROW_BURST(0),
        .FORWARD_ID          (1)  // pass-through ibex ID
    ) u_axi32_to_512 (
        .clk                 (clk),
        .rst                 (~rst_n),        // axi_adapter uses active-high reset

        // Slave (32-bit): ibex_wrapper AXI4 master
        .s_axi_awid          (ibex_awid),
        .s_axi_awaddr        (ibex_awaddr),
        .s_axi_awlen         (ibex_awlen),
        .s_axi_awsize        (ibex_awsize),
        .s_axi_awburst       (ibex_awburst),
        .s_axi_awlock        (tie_awlock),
        .s_axi_awcache       (tie_awcache),
        .s_axi_awprot        (tie_awprot),
        .s_axi_awqos         (tie_awqos),
        .s_axi_awregion      (tie_awregion),
        .s_axi_awuser        (1'b0),
        .s_axi_awvalid       (ibex_awvalid),
        .s_axi_awready       (ibex_awready),
        .s_axi_wdata         (ibex_wdata),
        .s_axi_wstrb         (ibex_wstrb),
        .s_axi_wlast         (ibex_wlast),
        .s_axi_wuser         (1'b0),
        .s_axi_wvalid        (ibex_wvalid),
        .s_axi_wready        (ibex_wready),
        .s_axi_bid           (ibex_bid),
        .s_axi_bresp         (ibex_bresp),
        .s_axi_buser         (),
        .s_axi_bvalid        (ibex_bvalid),
        .s_axi_bready        (ibex_bready),
        .s_axi_arid          (ibex_arid),
        .s_axi_araddr        (ibex_araddr),
        .s_axi_arlen         (ibex_arlen),
        .s_axi_arsize        (ibex_arsize),
        .s_axi_arburst       (ibex_arburst),
        .s_axi_arlock        (tie_arlock),
        .s_axi_arcache       (tie_arcache),
        .s_axi_arprot        (tie_arprot),
        .s_axi_arqos         (tie_arqos),
        .s_axi_arregion      (tie_arregion),
        .s_axi_aruser        (1'b0),
        .s_axi_arvalid       (ibex_arvalid),
        .s_axi_arready       (ibex_arready),
        .s_axi_rid           (ibex_rid),
        .s_axi_rdata         (ibex_rdata),
        .s_axi_rresp         (ibex_rresp),
        .s_axi_rlast         (ibex_rlast),
        .s_axi_ruser         (),
        .s_axi_rvalid        (ibex_rvalid),
        .s_axi_rready        (ibex_rready),

        // Master (512-bit): to crossbar master port 0
        .m_axi_awid          (adapt_awid),
        .m_axi_awaddr        (adapt_awaddr),
        .m_axi_awlen         (adapt_awlen),
        .m_axi_awsize        (adapt_awsize),
        .m_axi_awburst       (adapt_awburst),
        .m_axi_awlock        (adapter_unused_awlock),
        .m_axi_awcache       ({adapter_unused_awcache3, adapter_unused_awcache2,
                               adapter_unused_awcache1, adapter_unused_awcache0}),
        .m_axi_awprot        ({adapter_unused_awprot2, adapter_unused_awprot1,
                               adapter_unused_awprot0}),
        .m_axi_awqos         ({adapter_unused_awqos3, adapter_unused_awqos2,
                               adapter_unused_awqos1, adapter_unused_awqos0}),
        .m_axi_awregion      ({adapter_unused_awregion3, adapter_unused_awregion2,
                               adapter_unused_awregion1, adapter_unused_awregion0}),
        .m_axi_awuser        (adapter_unused_awuser),
        .m_axi_awvalid       (adapt_awvalid),
        .m_axi_awready       (adapt_awready),
        .m_axi_wdata         (adapt_wdata),
        .m_axi_wstrb         (adapt_wstrb),
        .m_axi_wlast         (adapt_wlast),
        .m_axi_wuser         (adapter_unused_wuser),
        .m_axi_wvalid        (adapt_wvalid),
        .m_axi_wready        (adapt_wready),
        .m_axi_bid           (adapt_bid),
        .m_axi_bresp         (adapt_bresp),
        .m_axi_buser         (adapter_unused_buser),
        .m_axi_bvalid        (adapt_bvalid),
        .m_axi_bready        (adapt_bready),
        .m_axi_arid          (adapt_arid),
        .m_axi_araddr        (adapt_araddr),
        .m_axi_arlen         (adapt_arlen),
        .m_axi_arsize        (adapt_arsize),
        .m_axi_arburst       (adapt_arburst),
        .m_axi_arlock        (adapter_unused_arlock),
        .m_axi_arcache       ({adapter_unused_arcache3, adapter_unused_arcache2,
                               adapter_unused_arcache1, adapter_unused_arcache0}),
        .m_axi_arprot        ({adapter_unused_arprot2, adapter_unused_arprot1,
                               adapter_unused_arprot0}),
        .m_axi_arqos         ({adapter_unused_arqos3, adapter_unused_arqos2,
                               adapter_unused_arqos1, adapter_unused_arqos0}),
        .m_axi_arregion      ({adapter_unused_arregion3, adapter_unused_arregion2,
                               adapter_unused_arregion1, adapter_unused_arregion0}),
        .m_axi_aruser        (adapter_unused_aruser),
        .m_axi_arvalid       (adapt_arvalid),
        .m_axi_arready       (adapt_arready),
        .m_axi_rid           (adapt_rid),
        .m_axi_rdata         (adapt_rdata),
        .m_axi_rresp         (adapt_rresp),
        .m_axi_rlast         (adapt_rlast),
        .m_axi_ruser         (adapter_unused_ruser),
        .m_axi_rvalid        (adapt_rvalid),
        .m_axi_rready        (adapt_rready)
    );

    //─────────────────────────────────────────────────────────────────────────
    // AXI4 Crossbar — M=6, S=2, round-robin
    //─────────────────────────────────────────────────────────────────────────
    // Master ports:  0=Ibex  1=MXU  2=SFU  3=Vector  4=DMA  5=PCIe
    // Slave ports:   0=SRAM(0x2000_0000)  1=DRAM(0x8000_0000)

    // --- Map width-adapter output to crossbar master 0 ---
    // Pad 4-bit adapter ID to 6-bit crossbar ID
    assign cb_m_awid[0]    = {2'b0, adapt_awid};
    assign cb_m_awaddr[0]  = adapt_awaddr;
    assign cb_m_awlen[0]   = adapt_awlen;
    assign cb_m_awsize[0]  = adapt_awsize;
    assign cb_m_awburst[0] = adapt_awburst;
    assign cb_m_awvalid[0] = adapt_awvalid;
    assign adapt_awready   = cb_m_awready[0];
    assign cb_m_wdata[0]   = adapt_wdata;
    assign cb_m_wstrb[0]   = adapt_wstrb;
    assign cb_m_wlast[0]   = adapt_wlast;
    assign cb_m_wvalid[0]  = adapt_wvalid;
    assign adapt_wready    = cb_m_wready[0];
    assign adapt_bid       = cb_m_bid[0][3:0];  // only lower 4 bits meaningful
    assign adapt_bresp     = cb_m_bresp[0];
    assign adapt_bvalid    = cb_m_bvalid[0];
    assign cb_m_bready[0]  = adapt_bready;
    assign cb_m_arid[0]    = {2'b0, adapt_arid};
    assign cb_m_araddr[0]  = adapt_araddr;
    assign cb_m_arlen[0]   = adapt_arlen;
    assign cb_m_arsize[0]  = adapt_arsize;
    assign cb_m_arburst[0] = adapt_arburst;
    assign cb_m_arvalid[0] = adapt_arvalid;
    assign adapt_arready   = cb_m_arready[0];
    assign adapt_rid       = cb_m_rid[0][3:0];
    assign adapt_rdata     = cb_m_rdata[0];
    assign adapt_rresp     = cb_m_rresp[0];
    assign adapt_rlast     = cb_m_rlast[0];
    assign adapt_rvalid    = cb_m_rvalid[0];
    assign cb_m_rready[0]  = adapt_rready;

    // --- Map engine wrappers (8-bit ID → 6-bit for crossbar) ---
    // MXU (master 1)
    assign cb_m_awid[1]   = mxu_awid_8[5:0];
    assign cb_m_awaddr[1] = mxu_awaddr;
    assign cb_m_awlen[1]  = mxu_awlen;
    assign cb_m_awsize[1] = mxu_awsize;
    assign cb_m_awburst[1]= mxu_awburst;
    assign cb_m_awvalid[1]= mxu_awvalid;
    assign mxu_awready    = cb_m_awready[1];
    assign cb_m_wdata[1]  = mxu_wdata;
    assign cb_m_wstrb[1]  = mxu_wstrb;
    assign cb_m_wlast[1]  = mxu_wlast;
    assign cb_m_wvalid[1] = mxu_wvalid;
    assign mxu_wready     = cb_m_wready[1];
    assign mxu_bid_8      = {2'b0, cb_m_bid[1]};
    assign mxu_bresp      = cb_m_bresp[1];
    assign mxu_bvalid     = cb_m_bvalid[1];
    assign cb_m_bready[1] = mxu_bready;
    assign cb_m_arid[1]   = mxu_arid_8[5:0];
    assign cb_m_araddr[1] = mxu_araddr;
    assign cb_m_arlen[1]  = mxu_arlen;
    assign cb_m_arsize[1] = mxu_arsize;
    assign cb_m_arburst[1]= mxu_arburst;
    assign cb_m_arvalid[1]= mxu_arvalid;
    assign mxu_arready    = cb_m_arready[1];
    assign mxu_rid_8      = {2'b0, cb_m_rid[1]};
    assign mxu_rdata      = cb_m_rdata[1];
    assign mxu_rresp      = cb_m_rresp[1];
    assign mxu_rlast      = cb_m_rlast[1];
    assign mxu_rvalid     = cb_m_rvalid[1];
    assign cb_m_rready[1] = mxu_rready;

    // SFU (master 2)
    assign cb_m_awid[2]   = sfu_awid_8[5:0];
    assign cb_m_awaddr[2] = sfu_awaddr;
    assign cb_m_awlen[2]  = sfu_awlen;
    assign cb_m_awsize[2] = sfu_awsize;
    assign cb_m_awburst[2]= sfu_awburst;
    assign cb_m_awvalid[2]= sfu_awvalid;
    assign sfu_awready    = cb_m_awready[2];
    assign cb_m_wdata[2]  = sfu_wdata;
    assign cb_m_wstrb[2]  = sfu_wstrb;
    assign cb_m_wlast[2]  = sfu_wlast;
    assign cb_m_wvalid[2] = sfu_wvalid;
    assign sfu_wready     = cb_m_wready[2];
    assign sfu_bid_8      = {2'b0, cb_m_bid[2]};
    assign sfu_bresp      = cb_m_bresp[2];
    assign sfu_bvalid     = cb_m_bvalid[2];
    assign cb_m_bready[2] = sfu_bready;
    assign cb_m_arid[2]   = sfu_arid_8[5:0];
    assign cb_m_araddr[2] = sfu_araddr;
    assign cb_m_arlen[2]  = sfu_arlen;
    assign cb_m_arsize[2] = sfu_arsize;
    assign cb_m_arburst[2]= sfu_arburst;
    assign cb_m_arvalid[2]= sfu_arvalid;
    assign sfu_arready    = cb_m_arready[2];
    assign sfu_rid_8      = {2'b0, cb_m_rid[2]};
    assign sfu_rdata      = cb_m_rdata[2];
    assign sfu_rresp      = cb_m_rresp[2];
    assign sfu_rlast      = cb_m_rlast[2];
    assign sfu_rvalid     = cb_m_rvalid[2];
    assign cb_m_rready[2] = sfu_rready;

    // Vector (master 3)
    assign cb_m_awid[3]   = vec_awid_8[5:0];
    assign cb_m_awaddr[3] = vec_awaddr;
    assign cb_m_awlen[3]  = vec_awlen;
    assign cb_m_awsize[3] = vec_awsize;
    assign cb_m_awburst[3]= vec_awburst;
    assign cb_m_awvalid[3]= vec_awvalid;
    assign vec_awready    = cb_m_awready[3];
    assign cb_m_wdata[3]  = vec_wdata;
    assign cb_m_wstrb[3]  = vec_wstrb;
    assign cb_m_wlast[3]  = vec_wlast;
    assign cb_m_wvalid[3] = vec_wvalid;
    assign vec_wready     = cb_m_wready[3];
    assign vec_bid_8      = {2'b0, cb_m_bid[3]};
    assign vec_bresp      = cb_m_bresp[3];
    assign vec_bvalid     = cb_m_bvalid[3];
    assign cb_m_bready[3] = vec_bready;
    assign cb_m_arid[3]   = vec_arid_8[5:0];
    assign cb_m_araddr[3] = vec_araddr;
    assign cb_m_arlen[3]  = vec_arlen;
    assign cb_m_arsize[3] = vec_arsize;
    assign cb_m_arburst[3]= vec_arburst;
    assign cb_m_arvalid[3]= vec_arvalid;
    assign vec_arready    = cb_m_arready[3];
    assign vec_rid_8      = {2'b0, cb_m_rid[3]};
    assign vec_rdata      = cb_m_rdata[3];
    assign vec_rresp      = cb_m_rresp[3];
    assign vec_rlast      = cb_m_rlast[3];
    assign vec_rvalid     = cb_m_rvalid[3];
    assign cb_m_rready[3] = vec_rready;

    // DMA (master 4)
    assign cb_m_awid[4]   = dma_awid_8[5:0];
    assign cb_m_awaddr[4] = dma_awaddr;
    assign cb_m_awlen[4]  = dma_awlen;
    assign cb_m_awsize[4] = dma_awsize;
    assign cb_m_awburst[4]= dma_awburst;
    assign cb_m_awvalid[4]= dma_awvalid;
    assign dma_awready    = cb_m_awready[4];
    assign cb_m_wdata[4]  = dma_wdata;
    assign cb_m_wstrb[4]  = dma_wstrb;
    assign cb_m_wlast[4]  = dma_wlast;
    assign cb_m_wvalid[4] = dma_wvalid;
    assign dma_wready     = cb_m_wready[4];
    assign dma_bid_8      = {2'b0, cb_m_bid[4]};
    assign dma_bresp      = cb_m_bresp[4];
    assign dma_bvalid     = cb_m_bvalid[4];
    assign cb_m_bready[4] = dma_bready;
    assign cb_m_arid[4]   = dma_arid_8[5:0];
    assign cb_m_araddr[4] = dma_araddr;
    assign cb_m_arlen[4]  = dma_arlen;
    assign cb_m_arsize[4] = dma_arsize;
    assign cb_m_arburst[4]= dma_arburst;
    assign cb_m_arvalid[4]= dma_arvalid;
    assign dma_arready    = cb_m_arready[4];
    assign dma_rid_8      = {2'b0, cb_m_rid[4]};
    assign dma_rdata      = cb_m_rdata[4];
    assign dma_rresp      = cb_m_rresp[4];
    assign dma_rlast      = cb_m_rlast[4];
    assign dma_rvalid     = cb_m_rvalid[4];
    assign cb_m_rready[4] = dma_rready;

    // PCIe (master 5) — ID_WIDTH=6 direct match
    assign cb_m_awid[5]   = pcie_awid_6;
    assign cb_m_awaddr[5] = pcie_awaddr;
    assign cb_m_awlen[5]  = pcie_awlen;
    assign cb_m_awsize[5] = pcie_awsize;
    assign cb_m_awburst[5]= pcie_awburst;
    assign cb_m_awvalid[5]= pcie_awvalid;
    assign pcie_ax_awready   = cb_m_awready[5];
    assign cb_m_wdata[5]  = pcie_wdata;
    assign cb_m_wstrb[5]  = pcie_wstrb;
    assign cb_m_wlast[5]  = pcie_wlast;
    assign cb_m_wvalid[5] = pcie_wvalid;
    assign pcie_ax_wready    = cb_m_wready[5];
    assign pcie_bid_6     = cb_m_bid[5];
    assign pcie_bresp     = cb_m_bresp[5];
    assign pcie_bvalid    = cb_m_bvalid[5];
    assign cb_m_bready[5] = pcie_bready;
    assign cb_m_arid[5]   = pcie_arid_6;
    assign cb_m_araddr[5] = pcie_araddr;
    assign cb_m_arlen[5]  = pcie_arlen;
    assign cb_m_arsize[5] = pcie_arsize;
    assign cb_m_arburst[5]= pcie_arburst;
    assign cb_m_arvalid[5]= pcie_arvalid;
    assign pcie_ax_arready   = cb_m_arready[5];
    assign pcie_rid_6     = cb_m_rid[5];
    assign pcie_rdata     = cb_m_rdata[5];
    assign pcie_rresp     = cb_m_rresp[5];
    assign pcie_rlast     = cb_m_rlast[5];
    assign pcie_rvalid    = cb_m_rvalid[5];
    assign cb_m_rready[5] = pcie_rready;

    // --- Crossbar instantiation ---
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

        // Master ports
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

        // Slave ports
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

    //─────────────────────────────────────────────────────────────────────────
    // SRAM Controller (AXI4 slave at 0x2000_0000, crossbar port S0)
    //─────────────────────────────────────────────────────────────────────────
    sram_ctrl #(
        .DATA_WIDTH (512),
        .ADDR_WIDTH (32),
        .ID_WIDTH   (8)
    ) u_sram_ctrl (
        .clk          (clk),
        .rst_n        (rst_n),

        .s_axi_awid   (cb_s_awid[0][7:0]),
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

        .s_axi_bid    (cb_s_bid[0][7:0]),
        .s_axi_bresp  (cb_s_bresp[0]),
        .s_axi_bvalid (cb_s_bvalid[0]),
        .s_axi_bready (cb_s_bready[0]),

        .s_axi_arid   (cb_s_arid[0][7:0]),
        .s_axi_araddr (cb_s_araddr[0]),
        .s_axi_arlen  (cb_s_arlen[0]),
        .s_axi_arsize (cb_s_arsize[0]),
        .s_axi_arburst(cb_s_arburst[0]),
        .s_axi_arvalid(cb_s_arvalid[0]),
        .s_axi_arready(cb_s_arready[0]),

        .s_axi_rid    (cb_s_rid[0][7:0]),
        .s_axi_rdata  (cb_s_rdata[0]),
        .s_axi_rresp  (cb_s_rresp[0]),
        .s_axi_rlast  (cb_s_rlast[0]),
        .s_axi_rvalid (cb_s_rvalid[0]),
        .s_axi_rready (cb_s_rready[0])
    );

    //─────────────────────────────────────────────────────────────────────────
    // DRAM Behavioral Model (AXI4 slave at 0x8000_0000, crossbar port S1)
    //─────────────────────────────────────────────────────────────────────────
    dram_model #(
        .DATA_WIDTH (512),
        .ADDR_WIDTH (32),
        .ID_WIDTH   (8)
    ) u_dram_model (
        .clk          (clk),
        .rst_n        (rst_n),

        .s_axi_awid   (cb_s_awid[1][7:0]),
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

        .s_axi_bid    (cb_s_bid[1][7:0]),
        .s_axi_bresp  (cb_s_bresp[1]),
        .s_axi_bvalid (cb_s_bvalid[1]),
        .s_axi_bready (cb_s_bready[1]),

        .s_axi_arid   (cb_s_arid[1][7:0]),
        .s_axi_araddr (cb_s_araddr[1]),
        .s_axi_arlen  (cb_s_arlen[1]),
        .s_axi_arsize (cb_s_arsize[1]),
        .s_axi_arburst(cb_s_arburst[1]),
        .s_axi_arvalid(cb_s_arvalid[1]),
        .s_axi_arready(cb_s_arready[1]),

        .s_axi_rid    (cb_s_rid[1][7:0]),
        .s_axi_rdata  (cb_s_rdata[1]),
        .s_axi_rresp  (cb_s_rresp[1]),
        .s_axi_rlast  (cb_s_rlast[1]),
        .s_axi_rvalid (cb_s_rvalid[1]),
        .s_axi_rready (cb_s_rready[1])
    );

    //─────────────────────────────────────────────────────────────────────────
    // APB Decoder (ibex APB master → 7 slaves)
    //─────────────────────────────────────────────────────────────────────────
    apb_decoder u_apb_decoder (
        .clk      (clk),
        .rst_n    (rst_n),

        // APB master from ibex_wrapper
        .psel     (apb_m_psel),
        .penable  (apb_m_penable),
        .paddr    (apb_m_paddr),
        .pwrite   (apb_m_pwrite),
        .pwdata   (apb_m_pwdata),

        // APB slave ports
        .psel_o   (apb_psel_o),
        .penable_o(apb_penable_o),
        .paddr_o  (apb_paddr_o),
        .pwrite_o (apb_pwrite_o),
        .pwdata_o (apb_pwdata_o),

        // Slave response inputs
        .pready_i (apb_pready_i),
        .pslverr_i(apb_pslverr_i),
        .prdata_i (apb_prdata),   // packed 7×32 → unpacked auto-conversion

        // Master response
        .pready   (apb_m_pready),
        .pslverr  (apb_m_pslverr),
        .prdata   (apb_m_prdata)
    );

    //─────────────────────────────────────────────────────────────────────────
    // MXU SoC Wrapper (APB slave 0 at 0x4000_0000, AXI4 master 1)
    //─────────────────────────────────────────────────────────────────────────
    mxu_soc_wrapper u_mxu_wrapper (
        .clk              (clk),
        .rst_n            (rst_n),

        .psel             (apb_psel_o[0]),
        .penable          (apb_penable_o[0]),
        .pwrite           (apb_pwrite_o),
        .paddr            (apb_paddr_o[11:0]),
        .pwdata           (apb_pwdata_o),
        .prdata           (apb_prdata[0]),
        .pready           (apb_pready_i[0]),
        .pslverr          (apb_pslverr_i[0]),

        .m_axi_awid       (mxu_awid_8),
        .m_axi_awaddr     (mxu_awaddr),
        .m_axi_awlen      (mxu_awlen),
        .m_axi_awsize     (mxu_awsize),
        .m_axi_awburst    (mxu_awburst),
        .m_axi_awvalid    (mxu_awvalid),
        .m_axi_awready    (mxu_awready),
        .m_axi_wdata      (mxu_wdata),
        .m_axi_wstrb      (mxu_wstrb),
        .m_axi_wlast      (mxu_wlast),
        .m_axi_wvalid     (mxu_wvalid),
        .m_axi_wready     (mxu_wready),
        .m_axi_bid        (mxu_bid_8),
        .m_axi_bresp      (mxu_bresp),
        .m_axi_bvalid     (mxu_bvalid),
        .m_axi_bready     (mxu_bready),
        .m_axi_arid       (mxu_arid_8),
        .m_axi_araddr     (mxu_araddr),
        .m_axi_arlen      (mxu_arlen),
        .m_axi_arsize     (mxu_arsize),
        .m_axi_arburst    (mxu_arburst),
        .m_axi_arvalid    (mxu_arvalid),
        .m_axi_arready    (mxu_arready),
        .m_axi_rid        (mxu_rid_8),
        .m_axi_rdata      (mxu_rdata),
        .m_axi_rresp      (mxu_rresp),
        .m_axi_rlast      (mxu_rlast),
        .m_axi_rvalid     (mxu_rvalid),
        .m_axi_rready     (mxu_rready),

        .irq              (mxu_irq),

        .dbg_state        (mxu_dbg_state),
        .dbg_compute_en   (mxu_dbg_compute_en),
        .dbg_weight_load  (mxu_dbg_weight_load),
        .dbg_activation_load(mxu_dbg_activation_load),
        .dbg_store_out    (mxu_dbg_store_out),
        .dbg_store_row    (mxu_dbg_store_row),
        .dbg_compute_k    (mxu_dbg_compute_k),
        .dbg_tiles_completed(mxu_dbg_tiles_completed)
    );

    //─────────────────────────────────────────────────────────────────────────
    // SFU SoC Wrapper (APB slave 1 at 0x4000_1000, AXI4 master 2)
    //─────────────────────────────────────────────────────────────────────────
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

    //─────────────────────────────────────────────────────────────────────────
    // Vector SoC Wrapper (APB slave 2 at 0x4000_2000, AXI4 master 3)
    //─────────────────────────────────────────────────────────────────────────
    vector_soc_wrapper u_vector_wrapper (
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

    //─────────────────────────────────────────────────────────────────────────
    // DMA Wrapper (APB slave 3 at 0x4000_3000, AXI4 master 4)
    //─────────────────────────────────────────────────────────────────────────
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

    //─────────────────────────────────────────────────────────────────────────
    // PCIe EP Wrapper (APB slave 4 at 0x4000_4000, AXI4 master 5)
    //─────────────────────────────────────────────────────────────────────────
    pcie_ep_wrapper u_pcie_wrapper (
        .clk               (clk),
        .rst_n             (rst_n),

        // TLP ports (exposed at SoC top for cocotb host model)
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

        // AXI4 master → crossbar master 5
        .m_axi_awid        (pcie_awid_6),
        .m_axi_awaddr      (pcie_awaddr),
        .m_axi_awlen       (pcie_awlen),
        .m_axi_awsize      (pcie_awsize),
        .m_axi_awburst     (pcie_awburst),
        .m_axi_awlock      (pcie_awlock_unused),
        .m_axi_awcache     ({pcie_awcache_unused3, pcie_awcache_unused2,
                             pcie_awcache_unused1, pcie_awcache_unused0}),
        .m_axi_awprot      ({pcie_awprot_unused2, pcie_awprot_unused1,
                             pcie_awprot_unused0}),
        .m_axi_awvalid     (pcie_awvalid),
        .m_axi_awready     (pcie_ax_awready),
        .m_axi_wdata       (pcie_wdata),
        .m_axi_wstrb       (pcie_wstrb),
        .m_axi_wlast       (pcie_wlast),
        .m_axi_wvalid      (pcie_wvalid),
        .m_axi_wready      (pcie_ax_wready),
        .m_axi_bid         (pcie_bid_6),
        .m_axi_bresp       (pcie_bresp),
        .m_axi_bvalid      (pcie_bvalid),
        .m_axi_bready      (pcie_bready),
        .m_axi_arid        (pcie_arid_6),
        .m_axi_araddr      (pcie_araddr),
        .m_axi_arlen       (pcie_arlen),
        .m_axi_arsize      (pcie_arsize),
        .m_axi_arburst     (pcie_arburst),
        .m_axi_arlock      (pcie_arlock_unused),
        .m_axi_arcache     ({pcie_arcache_unused3, pcie_arcache_unused2,
                             pcie_arcache_unused1, pcie_arcache_unused0}),
        .m_axi_arprot      ({pcie_arprot_unused2, pcie_arprot_unused1,
                             pcie_arprot_unused0}),
        .m_axi_arvalid     (pcie_arvalid),
        .m_axi_arready     (pcie_ax_arready),
        .m_axi_rid         (pcie_rid_6),
        .m_axi_rdata       (pcie_rdata),
        .m_axi_rresp       (pcie_rresp),
        .m_axi_rlast       (pcie_rlast),
        .m_axi_rvalid      (pcie_rvalid),
        .m_axi_rready      (pcie_rready),

        // APB slave from apb_decoder port 4
        .psel              (apb_psel_o[4]),
        .penable           (apb_penable_o[4]),
        .pwrite            (apb_pwrite_o),
        .paddr             (apb_paddr_o),      // 32-bit, matches PCIe wrapper port
        .pwdata            (apb_pwdata_o),
        .prdata            (apb_prdata[4]),
        .pready            (apb_pready_i[4]),
        .pslverr           (apb_pslverr_i[4]),

        .pcie_irq          (pcie_irq)
    );

    //─────────────────────────────────────────────────────────────────────────
    // Doorbell (APB slave 5 at 0x4000_5000)
    //─────────────────────────────────────────────────────────────────────────
    doorbell u_doorbell (
        .clk           (clk),
        .rst_n         (rst_n),

        .psel          (apb_psel_o[5]),
        .penable       (apb_penable_o[5]),
        .pwrite        (apb_pwrite_o),
        .paddr         (apb_paddr_o[11:0]),
        .pwdata        (apb_pwdata_o),
        .prdata        (apb_prdata[5]),
        .pready        (apb_pready_i[5]),
        .pslverr       (apb_pslverr_i[5]),

        .doorbell_irq  (doorbell_irq)
    );

    //─────────────────────────────────────────────────────────────────────────
    // INTC (APB slave 6 at 0x4000_6000)
    //─────────────────────────────────────────────────────────────────────────
    intc_top u_intc (
        .clk           (clk),
        .rst_n         (rst_n),

        .mxu_irq       (mxu_irq),
        .sfu_irq       (sfu_irq),
        .vector_irq    (vec_irq),
        .dma_irq       (dma_irq),
        .pcie_irq      (pcie_irq),
        .host_irq      (doorbell_irq),
        .timer_irq     (timer_irq_i),

        .psel          (apb_psel_o[6]),
        .penable       (apb_penable_o[6]),
        .pwrite        (apb_pwrite_o),
        .paddr         (apb_paddr_o[11:0]),
        .pwdata        (apb_pwdata_o),
        .prdata        (apb_prdata[6]),
        .pready        (apb_pready_i[6]),
        .pslverr       (apb_pslverr_i[6]),

        .cpu_irq       (cpu_irq)
    );

    //─────────────────────────────────────────────────────────────────────────
    // Ibex RISC-V Core Wrapper
    //─────────────────────────────────────────────────────────────────────────
    // Instantiates ibex_top (RV32IMC) internally, plus boot_rom.
    // AXI4 master → width adapter → crossbar master 0.
    // APB master → apb_decoder.
    // cpu_irq ← intc_top.
    ibex_wrapper #(
        .AXI_ADDR_WIDTH (32),
        .AXI_DATA_WIDTH (32),
        .AXI_ID_WIDTH   (4)
    ) u_ibex_wrapper (
        .clk            (clk),
        .rst_n          (rst_n),

        .cpu_irq_i      (cpu_irq),

        // AXI4 master — 32-bit → width adapter → crossbar
        .m_axi_awid     (ibex_awid),
        .m_axi_awaddr   (ibex_awaddr),
        .m_axi_awlen    (ibex_awlen),
        .m_axi_awsize   (ibex_awsize),
        .m_axi_awburst  (ibex_awburst),
        .m_axi_awvalid  (ibex_awvalid),
        .m_axi_awready  (ibex_awready),
        .m_axi_wdata    (ibex_wdata),
        .m_axi_wstrb    (ibex_wstrb),
        .m_axi_wlast    (ibex_wlast),
        .m_axi_wvalid   (ibex_wvalid),
        .m_axi_wready   (ibex_wready),
        .m_axi_bid      (ibex_bid),
        .m_axi_bresp    (ibex_bresp),
        .m_axi_bvalid   (ibex_bvalid),
        .m_axi_bready   (ibex_bready),
        .m_axi_arid     (ibex_arid),
        .m_axi_araddr   (ibex_araddr),
        .m_axi_arlen    (ibex_arlen),
        .m_axi_arsize   (ibex_arsize),
        .m_axi_arburst  (ibex_arburst),
        .m_axi_arvalid  (ibex_arvalid),
        .m_axi_arready  (ibex_arready),
        .m_axi_rid      (ibex_rid),
        .m_axi_rdata    (ibex_rdata),
        .m_axi_rresp    (ibex_rresp),
        .m_axi_rlast    (ibex_rlast),
        .m_axi_rvalid   (ibex_rvalid),
        .m_axi_rready   (ibex_rready),

        // APB master → decoder
        .apb_paddr      (apb_m_paddr),
        .apb_psel       (apb_m_psel),
        .apb_penable    (apb_m_penable),
        .apb_pwrite     (apb_m_pwrite),
        .apb_pwdata     (apb_m_pwdata),
        .apb_prdata     (apb_m_prdata),
        .apb_pready     (apb_m_pready),
        .apb_pslverr    (apb_m_pslverr)
    );

endmodule
