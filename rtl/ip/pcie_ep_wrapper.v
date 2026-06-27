//=============================================================================
// pcie_ep_wrapper — PCIe Endpoint Wrapper (pcie_axi_master + APB config)
//=============================================================================
// CaduceusCore SoC Phase 3-4 / Task 8
//
// Wraps pcie_axi_master (alexforencich/verilog-pcie, MIT) with:
//   • TLP-level RX/TX ports for cocotbext-pcie host model (no PHY needed)
//   • AXI4 master port (512-bit, burst) — connects to axi_crossbar master #5
//   • APB slave at 0x4000_4000 (decoder port 4) for PCIe configuration:
//     BAR mapping, MSI-X control, completer ID, interrupt control
//   • pcie_irq output → intc_top source bit 4
//
// BAR mapping (documented in APB registers, enforced by cocotb host model):
//   BAR0 → SRAM (0x2000_0000, 4 MB)
//   BAR1 → DRAM (0x8000_0000, 2 GB)
//
// The pcie_axi_master translates PCIe TLP addresses directly to AXI addresses.
// No BAR address remapping is done in hardware — the cocotbext-pcie host model
// generates TLPs with the correct SoC physical addresses (0x2000_0xxx or
// 0x8000_0xxx) based on the BAR configuration visible in the APB registers.
//
// Must NOT modify vendored verilog-pcie source files.
//=============================================================================

`resetall
`timescale 1ns / 1ps
`default_nettype none

module pcie_ep_wrapper #(
    // TLP data width (PCIe Transaction Layer — MUST match AXI_DATA_WIDTH
    // per pcie_axi_master assertion at pcie_axi_master_rd.v:143)
    parameter TLP_DATA_WIDTH   = 512,
    // TLP strobe width (DWORDs)
    parameter TLP_STRB_WIDTH   = TLP_DATA_WIDTH / 32,
    // TLP header width (128-bit per PCIe spec)
    parameter TLP_HDR_WIDTH    = 128,
    // TLP segment count
    parameter TLP_SEG_COUNT    = 1,
    // AXI data bus width (must match SoC crossbar: 512-bit)
    parameter AXI_DATA_WIDTH   = 512,
    // AXI address bus width (must match SoC crossbar: 32-bit)
    parameter AXI_ADDR_WIDTH   = 32,
    // AXI wstrb width
    parameter AXI_STRB_WIDTH   = AXI_DATA_WIDTH / 8,
    // AXI ID width (must match crossbar M_ID_WIDTH: 6)
    parameter AXI_ID_WIDTH     = 6,
    // Max AXI burst length (beats per burst)
    parameter AXI_MAX_BURST_LEN = 256
) (
    input  wire                            clk,
    input  wire                            rst_n,

    // ── TLP RX (request from cocotbext-pcie host model) ────────────────────
    input  wire [TLP_DATA_WIDTH-1:0]       rx_req_tlp_data,
    input  wire [TLP_HDR_WIDTH-1:0]        rx_req_tlp_hdr,
    input  wire                            rx_req_tlp_valid,
    input  wire                            rx_req_tlp_sop,
    input  wire                            rx_req_tlp_eop,
    output wire                            rx_req_tlp_ready,

    // ── TLP TX (completion to cocotbext-pcie host model) ──────────────────
    output wire [TLP_DATA_WIDTH-1:0]       tx_cpl_tlp_data,
    output wire [TLP_STRB_WIDTH-1:0]       tx_cpl_tlp_strb,
    output wire [TLP_HDR_WIDTH-1:0]        tx_cpl_tlp_hdr,
    output wire                            tx_cpl_tlp_valid,
    output wire                            tx_cpl_tlp_sop,
    output wire                            tx_cpl_tlp_eop,
    input  wire                            tx_cpl_tlp_ready,

    // ── AXI4 Master (to axi_crossbar master #5) ────────────────────────────
    // Write Address channel
    output wire [AXI_ID_WIDTH-1:0]         m_axi_awid,
    output wire [AXI_ADDR_WIDTH-1:0]       m_axi_awaddr,
    output wire [7:0]                      m_axi_awlen,
    output wire [2:0]                      m_axi_awsize,
    output wire [1:0]                      m_axi_awburst,
    output wire                            m_axi_awlock,
    output wire [3:0]                      m_axi_awcache,
    output wire [2:0]                      m_axi_awprot,
    output wire                            m_axi_awvalid,
    input  wire                            m_axi_awready,

    // Write Data channel
    output wire [AXI_DATA_WIDTH-1:0]       m_axi_wdata,
    output wire [AXI_STRB_WIDTH-1:0]       m_axi_wstrb,
    output wire                            m_axi_wlast,
    output wire                            m_axi_wvalid,
    input  wire                            m_axi_wready,

    // Write Response channel
    input  wire [AXI_ID_WIDTH-1:0]         m_axi_bid,
    input  wire [1:0]                      m_axi_bresp,
    input  wire                            m_axi_bvalid,
    output wire                            m_axi_bready,

    // Read Address channel
    output wire [AXI_ID_WIDTH-1:0]         m_axi_arid,
    output wire [AXI_ADDR_WIDTH-1:0]       m_axi_araddr,
    output wire [7:0]                      m_axi_arlen,
    output wire [2:0]                      m_axi_arsize,
    output wire [1:0]                      m_axi_arburst,
    output wire                            m_axi_arlock,
    output wire [3:0]                      m_axi_arcache,
    output wire [2:0]                      m_axi_arprot,
    output wire                            m_axi_arvalid,
    input  wire                            m_axi_arready,

    // Read Data channel
    input  wire [AXI_ID_WIDTH-1:0]         m_axi_rid,
    input  wire [AXI_DATA_WIDTH-1:0]       m_axi_rdata,
    input  wire [1:0]                      m_axi_rresp,
    input  wire                            m_axi_rlast,
    input  wire                            m_axi_rvalid,
    output wire                            m_axi_rready,

    // ── APB Slave (from apb_decoder port 4, 0x4000_4000 ~ 0x4000_4FFF) ───
    input  wire                            psel,
    input  wire                            penable,
    input  wire                            pwrite,
    input  wire [31:0]                     paddr,
    input  wire [31:0]                     pwdata,
    output wire [31:0]                     prdata,
    output wire                            pready,
    output wire                            pslverr,

    // ── Interrupt output (to intc_top source bit 4) ───────────────────────
    output wire                            pcie_irq
);

    //=========================================================================
    // Internal signals
    //=========================================================================
    wire                                rst;            // active-high for IP core
    reg  [15:0]                         completer_id_reg;
    reg  [2:0]                          max_payload_size_reg;
    wire                                status_error_cor;
    wire                                status_error_uncor;

    // APB decode and register signals
    wire                                apb_write;      // APB write strobe
    wire                                sel_ctrl;       // offset 0x00
    wire                                sel_status;     // offset 0x04
    wire                                sel_completer;  // offset 0x08
    wire                                sel_bar0_base;  // offset 0x0C
    wire                                sel_bar0_mask;  // offset 0x10
    wire                                sel_bar1_base;  // offset 0x14
    wire                                sel_bar1_mask;  // offset 0x18
    wire                                sel_msix_ctrl;  // offset 0x1C
    wire                                sel_irq_ctrl;   // offset 0x20
    wire                                valid_sel;      // any valid register hit

    // MSI-X and interrupt control registers
    reg                                 msix_enable_reg;
    reg  [7:0]                          msix_vector_reg;
    reg                                 irq_enable_reg;
    reg                                 irq_pending_reg;
    reg                                 err_irq_en_reg;

    //=========================================================================
    // Reset polarity conversion — SoC uses rst_n (active low), IP uses rst
    //=========================================================================
    assign rst = ~rst_n;

    //=========================================================================
    // pcie_axi_master instantiation
    //=========================================================================
    // Parameters are tuned for SoC integration:
    //   AXI_DATA_WIDTH=512, AXI_ADDR_WIDTH=32, AXI_ID_WIDTH=6
    //   TLP_DATA_WIDTH=512 (must match AXI_DATA_WIDTH per assertion)
    //   TLP_SEG_COUNT=1 (single segment per TLP)
    //   AXI_MAX_BURST_LEN=256 (allow large bursts, capped by max_payload_size)
    //   TLP_FORCE_64_BIT_ADDR=0 (use 32-bit addresses when possible)

    pcie_axi_master #(
        .TLP_DATA_WIDTH      (TLP_DATA_WIDTH),
        .TLP_STRB_WIDTH      (TLP_STRB_WIDTH),
        .TLP_HDR_WIDTH       (TLP_HDR_WIDTH),
        .TLP_SEG_COUNT       (TLP_SEG_COUNT),
        .AXI_DATA_WIDTH      (AXI_DATA_WIDTH),
        .AXI_ADDR_WIDTH      (AXI_ADDR_WIDTH),
        .AXI_STRB_WIDTH      (AXI_STRB_WIDTH),
        .AXI_ID_WIDTH        (AXI_ID_WIDTH),
        .AXI_MAX_BURST_LEN   (AXI_MAX_BURST_LEN),
        .TLP_FORCE_64_BIT_ADDR(0)
    ) pcie_axi_master_inst (
        .clk                (clk),
        .rst                (rst),

        // TLP input (request)
        .rx_req_tlp_data    (rx_req_tlp_data),
        .rx_req_tlp_hdr     (rx_req_tlp_hdr),
        .rx_req_tlp_valid   (rx_req_tlp_valid),
        .rx_req_tlp_sop     (rx_req_tlp_sop),
        .rx_req_tlp_eop     (rx_req_tlp_eop),
        .rx_req_tlp_ready   (rx_req_tlp_ready),

        // TLP output (completion)
        .tx_cpl_tlp_data    (tx_cpl_tlp_data),
        .tx_cpl_tlp_strb    (tx_cpl_tlp_strb),
        .tx_cpl_tlp_hdr     (tx_cpl_tlp_hdr),
        .tx_cpl_tlp_valid   (tx_cpl_tlp_valid),
        .tx_cpl_tlp_sop     (tx_cpl_tlp_sop),
        .tx_cpl_tlp_eop     (tx_cpl_tlp_eop),
        .tx_cpl_tlp_ready   (tx_cpl_tlp_ready),

        // AXI Master output
        .m_axi_awid         (m_axi_awid),
        .m_axi_awaddr       (m_axi_awaddr),
        .m_axi_awlen        (m_axi_awlen),
        .m_axi_awsize       (m_axi_awsize),
        .m_axi_awburst      (m_axi_awburst),
        .m_axi_awlock       (m_axi_awlock),
        .m_axi_awcache      (m_axi_awcache),
        .m_axi_awprot       (m_axi_awprot),
        .m_axi_awvalid      (m_axi_awvalid),
        .m_axi_awready      (m_axi_awready),
        .m_axi_wdata        (m_axi_wdata),
        .m_axi_wstrb        (m_axi_wstrb),
        .m_axi_wlast        (m_axi_wlast),
        .m_axi_wvalid       (m_axi_wvalid),
        .m_axi_wready       (m_axi_wready),
        .m_axi_bid          (m_axi_bid),
        .m_axi_bresp        (m_axi_bresp),
        .m_axi_bvalid       (m_axi_bvalid),
        .m_axi_bready       (m_axi_bready),
        .m_axi_arid         (m_axi_arid),
        .m_axi_araddr       (m_axi_araddr),
        .m_axi_arlen        (m_axi_arlen),
        .m_axi_arsize       (m_axi_arsize),
        .m_axi_arburst      (m_axi_arburst),
        .m_axi_arlock       (m_axi_arlock),
        .m_axi_arcache      (m_axi_arcache),
        .m_axi_arprot       (m_axi_arprot),
        .m_axi_arvalid      (m_axi_arvalid),
        .m_axi_arready      (m_axi_arready),
        .m_axi_rid          (m_axi_rid),
        .m_axi_rdata        (m_axi_rdata),
        .m_axi_rresp        (m_axi_rresp),
        .m_axi_rlast        (m_axi_rlast),
        .m_axi_rvalid       (m_axi_rvalid),
        .m_axi_rready       (m_axi_rready),

        // Configuration
        .completer_id       (completer_id_reg),
        .max_payload_size   (max_payload_size_reg),

        // Status
        .status_error_cor   (status_error_cor),
        .status_error_uncor (status_error_uncor)
    );

    //=========================================================================
    // APB Register File — PCIe Configuration at 0x4000_4000
    //=========================================================================
    //
    // Register map (offsets within the 4 KB APB window):
    //   Offset  Access  Name               Description
    //   0x00    RW      PCIE_CTRL          [2:0]=max_payload_size, [3]=enable
    //   0x04    RO      PCIE_STATUS        [0]=error_cor, [1]=error_uncor
    //   0x08    RW      PCIE_COMPLETER_ID  [15:0]=Bus/Dev/Fn ID
    //   0x0C    RO      PCIE_BAR0_BASE     0x2000_0000 (SRAM)
    //   0x10    RO      PCIE_BAR0_MASK     0xFFC0_0000 (4 MB)
    //   0x14    RO      PCIE_BAR1_BASE     0x8000_0000 (DRAM)
    //   0x18    RO      PCIE_BAR1_MASK     0x8000_0000 (2 GB, bit31=writable)
    //   0x1C    RW      PCIE_MSIX_CTRL     [0]=msix_en, [15:8]=vector_num
    //   0x20    RW      PCIE_IRQ_CTRL      [0]=irq_en, [1]=irq_pending(W1C),
    //                                       [2]=err_irq_en
    //
    // All registers: 32-bit.  Zero-wait-state (pready = 1).
    // Out-of-range offsets → pslverr = 1.

    // APB write strobe (penable phase)
    assign apb_write    = psel && penable && pwrite;

    // Address decode — offsets within the 4 KB page (paddr[11:0])
    // Using full paddr[11:0] to avoid alias within the 4KB window
    assign sel_ctrl      = (paddr[11:0] == 12'h000);
    assign sel_status    = (paddr[11:0] == 12'h004);
    assign sel_completer = (paddr[11:0] == 12'h008);
    assign sel_bar0_base = (paddr[11:0] == 12'h00C);
    assign sel_bar0_mask = (paddr[11:0] == 12'h010);
    assign sel_bar1_base = (paddr[11:0] == 12'h014);
    assign sel_bar1_mask = (paddr[11:0] == 12'h018);
    assign sel_msix_ctrl = (paddr[11:0] == 12'h01C);
    assign sel_irq_ctrl  = (paddr[11:0] == 12'h020);

    assign valid_sel = sel_ctrl || sel_status || sel_completer ||
                       sel_bar0_base || sel_bar0_mask ||
                       sel_bar1_base || sel_bar1_mask ||
                       sel_msix_ctrl || sel_irq_ctrl;

    // pready: zero-wait-state for all registers
    assign pready  = psel ? 1'b1 : 1'b0;

    // pslverr: asserted for unmapped offsets within the 4KB window
    assign pslverr = psel && penable && !valid_sel;

    //=========================================================================
    // PCIE_CTRL register (0x00, RW)
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            max_payload_size_reg <= 3'd0;
        end else if (apb_write && sel_ctrl) begin
            max_payload_size_reg <= pwdata[2:0];
        end
    end

    //=========================================================================
    // PCIE_COMPLETER_ID register (0x08, RW)
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            completer_id_reg <= 16'h0000;
        end else if (apb_write && sel_completer) begin
            completer_id_reg <= pwdata[15:0];
        end
    end

    //=========================================================================
    // PCIE_MSIX_CTRL register (0x1C, RW)
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            msix_enable_reg <= 1'b0;
            msix_vector_reg <= 8'd0;
        end else if (apb_write && sel_msix_ctrl) begin
            msix_enable_reg <= pwdata[0];
            msix_vector_reg <= pwdata[15:8];
        end
    end

    //=========================================================================
    // PCIE_IRQ_CTRL register (0x20, RW with W1C on bit[1])
    //=========================================================================
    // irq_pending is set by status_error_uncor (or future MSI-X event).
    // It can be cleared by writing 1 to bit[1] (W1C).
    // irq_enable gates whether pcie_irq is asserted to intc_top.
    // err_irq_en gates whether uncorrectable errors trigger irq_pending.

    wire irq_pending_set;
    // irq_pending is set on rising edge of status_error_uncor when err_irq_en
    reg  status_error_uncor_d;  // edge detection
    wire status_error_uncor_rising;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            status_error_uncor_d <= 1'b0;
        end else begin
            status_error_uncor_d <= status_error_uncor;
        end
    end

    assign status_error_uncor_rising = status_error_uncor && !status_error_uncor_d;
    assign irq_pending_set = status_error_uncor_rising && err_irq_en_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            irq_enable_reg  <= 1'b0;
            irq_pending_reg <= 1'b0;
            err_irq_en_reg  <= 1'b0;
        end else begin
            if (apb_write && sel_irq_ctrl) begin
                irq_enable_reg <= pwdata[0];
                err_irq_en_reg <= pwdata[2];
                // W1C: writing 1 to bit[1] clears irq_pending; writing 0 no-op
                if (pwdata[1])
                    irq_pending_reg <= 1'b0;
                else
                    irq_pending_reg <= irq_pending_reg || irq_pending_set;
            end else begin
                // Accumulate pending from error events
                if (irq_pending_set)
                    irq_pending_reg <= 1'b1;
            end
        end
    end

    //=========================================================================
    // Interrupt output — asserted when enabled and pending
    //=========================================================================
    assign pcie_irq = irq_enable_reg && irq_pending_reg;

    //=========================================================================
    // APB Read Data Mux
    //=========================================================================
    assign prdata = sel_ctrl      ? {28'h0,              max_payload_size_reg, 1'b0} :
                    sel_status    ? {30'h0,              status_error_uncor, status_error_cor} :
                    sel_completer ? {16'h0,              completer_id_reg} :
                    sel_bar0_base ? 32'h2000_0000 :   // SRAM base
                    sel_bar0_mask ? 32'hFFC0_0000 :   // 4 MB mask
                    sel_bar1_base ? 32'h8000_0000 :   // DRAM base
                    sel_bar1_mask ? 32'h8000_0000 :   // 2 GB mask
                    sel_msix_ctrl ? {16'h0, msix_vector_reg, 7'h0, msix_enable_reg} :
                    sel_irq_ctrl  ? {29'h0, err_irq_en_reg, irq_pending_reg, irq_enable_reg} :
                    32'h0;

endmodule

`resetall
