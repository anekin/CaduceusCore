//=============================================================================
// pcie_dma_tb — Standalone Testbench for pcie_dma_wrapper
//=============================================================================
// CaduceusCore SoC Phase 4 / Task T2.2
//
// Verifies APB descriptor adapter + dma_if_pcie + dma_if_axi integration in
// isolation (no SoC crossbar).  All BFMs and the AXI slave memory model are
// inline.
//
// Test cases:
//   TC1: APB register write/readback
//   TC2: Read descriptor emits PCIe MRd TLP
//   TC3: PCIe CplD → AXI write to NPU memory
//   TC4: Write descriptor emits PCIe MWr TLP
//   TC5: Completion UR error propagates to RD_ERR_CODE / STATUS.error
//
// Usage on sz0001:
//   source /NAS/Tools/methodology/modules/init/bash
//   module load vcs/vcs_vW-2024.09-SP2_P
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
//       -f rtl/ip/verilog-pcie.flist rtl/ip/pcie_dma_wrapper.v \
//       rtl/ip/pcie_dma_tb.sv -top pcie_dma_tb -o simv_pcie_dma_tb
//   ./simv_pcie_dma_tb
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module pcie_dma_tb;

    // -------------------------------------------------------------------------
    // Parameters
    // -------------------------------------------------------------------------
    localparam CLK_HALF      = 0.5;          // 1 GHz
    localparam TLP_DATA_W    = 512;
    localparam TLP_STRB_W    = TLP_DATA_W / 32;
    localparam TLP_HDR_W     = 128;
    localparam TLP_SEG_COUNT = 1;
    localparam AXI_DATA_W    = 512;
    localparam AXI_ADDR_W    = 32;
    localparam AXI_STRB_W    = AXI_DATA_W / 8;
    localparam AXI_ID_W      = 6;
    localparam LEN_W         = 16;
    localparam TAG_W         = 8;
    localparam PCIE_ADDR_W   = 64;
    localparam MAX_TIMEOUT   = 200000;

    // APB register offsets (plan C5)
    localparam [11:0] REG_CTRL         = 12'h000;
    localparam [11:0] REG_STATUS       = 12'h004;
    localparam [11:0] REG_PCIE_ADDR_LO = 12'h008;
    localparam [11:0] REG_PCIE_ADDR_HI = 12'h00C;
    localparam [11:0] REG_AXI_ADDR     = 12'h010;
    localparam [11:0] REG_LEN          = 12'h014;
    localparam [11:0] REG_TAG          = 12'h018;
    localparam [11:0] REG_RD_ERR_CODE  = 12'h01C;
    localparam [11:0] REG_WR_ERR_CODE  = 12'h020;

    // -------------------------------------------------------------------------
    // DUT Signals
    // -------------------------------------------------------------------------
    reg                       clk;
    reg                       rst_n;

    // TLP RX (completion from host)
    reg  [TLP_DATA_W-1:0]     rx_cpl_tlp_data;
    reg  [TLP_HDR_W-1:0]      rx_cpl_tlp_hdr;
    reg  [3:0]                rx_cpl_tlp_error;
    reg                       rx_cpl_tlp_valid;
    reg                       rx_cpl_tlp_sop;
    reg                       rx_cpl_tlp_eop;
    wire                      rx_cpl_tlp_ready;

    // TLP TX (read request to host)
    wire [TLP_HDR_W-1:0]      tx_rd_req_tlp_hdr;
    wire [4:0]                tx_rd_req_tlp_seq;
    wire                      tx_rd_req_tlp_valid;
    wire                      tx_rd_req_tlp_sop;
    wire                      tx_rd_req_tlp_eop;
    reg                       tx_rd_req_tlp_ready;

    // TLP TX (write request to host)
    wire [TLP_DATA_W-1:0]     tx_wr_req_tlp_data;
    wire [TLP_STRB_W-1:0]     tx_wr_req_tlp_strb;
    wire [TLP_HDR_W-1:0]      tx_wr_req_tlp_hdr;
    wire [4:0]                tx_wr_req_tlp_seq;
    wire                      tx_wr_req_tlp_valid;
    wire                      tx_wr_req_tlp_sop;
    wire                      tx_wr_req_tlp_eop;
    reg                       tx_wr_req_tlp_ready;

    // AXI4 Master (DUT → behavioral slave memory)
    wire [AXI_ID_W-1:0]       m_axi_awid;
    wire [AXI_ADDR_W-1:0]     m_axi_awaddr;
    wire [7:0]                m_axi_awlen;
    wire [2:0]                m_axi_awsize;
    wire [1:0]                m_axi_awburst;
    wire                      m_axi_awlock;
    wire [3:0]                m_axi_awcache;
    wire [2:0]                m_axi_awprot;
    wire                      m_axi_awvalid;
    reg                       m_axi_awready;
    wire [AXI_DATA_W-1:0]     m_axi_wdata;
    wire [AXI_STRB_W-1:0]     m_axi_wstrb;
    wire                      m_axi_wlast;
    wire                      m_axi_wvalid;
    reg                       m_axi_wready;
    reg  [AXI_ID_W-1:0]       m_axi_bid;
    reg  [1:0]                m_axi_bresp;
    reg                       m_axi_bvalid;
    wire                      m_axi_bready;
    wire [AXI_ID_W-1:0]       m_axi_arid;
    wire [AXI_ADDR_W-1:0]     m_axi_araddr;
    wire [7:0]                m_axi_arlen;
    wire [2:0]                m_axi_arsize;
    wire [1:0]                m_axi_arburst;
    wire                      m_axi_arlock;
    wire [3:0]                m_axi_arcache;
    wire [2:0]                m_axi_arprot;
    wire                      m_axi_arvalid;
    reg                       m_axi_arready;
    reg  [AXI_ID_W-1:0]       m_axi_rid;
    reg  [AXI_DATA_W-1:0]     m_axi_rdata;
    reg  [1:0]                m_axi_rresp;
    reg                       m_axi_rlast;
    reg                       m_axi_rvalid;
    wire                      m_axi_rready;

    // APB Slave
    reg                       psel;
    reg                       penable;
    reg                       pwrite;
    reg  [31:0]               paddr;
    reg  [31:0]               pwdata;
    wire [31:0]               prdata;
    wire                      pready;
    wire                      pslverr;
    wire                      pcie_dma_irq;

    // -------------------------------------------------------------------------
    // DUT Instantiation
    // -------------------------------------------------------------------------
    pcie_dma_wrapper #(
        .TLP_DATA_WIDTH      (TLP_DATA_W),
        .TLP_STRB_WIDTH      (TLP_STRB_W),
        .TLP_HDR_WIDTH       (TLP_HDR_W),
        .TLP_SEG_COUNT       (TLP_SEG_COUNT),
        .PCIE_ADDR_WIDTH     (PCIE_ADDR_W),
        .PCIE_TAG_COUNT      (256),
        .READ_OP_TABLE_SIZE  (256),
        .WRITE_OP_TABLE_SIZE (256),
        .READ_TX_LIMIT       (128),
        .WRITE_TX_LIMIT      (128),
        .READ_CPLH_FC_LIMIT  (64),
        .READ_CPLD_FC_LIMIT  (256),
        .IMM_ENABLE          (0),
        .IMM_WIDTH           (32),
        .AXI_DATA_WIDTH      (AXI_DATA_W),
        .AXI_ADDR_WIDTH      (AXI_ADDR_W),
        .AXI_STRB_WIDTH      (AXI_STRB_W),
        .AXI_ID_WIDTH        (AXI_ID_W),
        .AXI_MAX_BURST_LEN   (256),
        .RAM_SEL_WIDTH       (2),
        .RAM_ADDR_WIDTH      (16),
        .RAM_SEG_COUNT       (2),
        .LEN_WIDTH           (LEN_W),
        .TAG_WIDTH           (TAG_W),
        .TX_SEQ_NUM_COUNT    (1),
        .TX_SEQ_NUM_WIDTH    (5),
        .TX_SEQ_NUM_ENABLE   (0),
        .TLP_FORCE_64_BIT_ADDR(0),
        .CHECK_BUS_NUMBER    (1)
    ) u_dut (
        .clk                    (clk),
        .rst_n                  (rst_n),
        .rx_cpl_tlp_data        (rx_cpl_tlp_data),
        .rx_cpl_tlp_hdr         (rx_cpl_tlp_hdr),
        .rx_cpl_tlp_error       (rx_cpl_tlp_error),
        .rx_cpl_tlp_valid       (rx_cpl_tlp_valid),
        .rx_cpl_tlp_sop         (rx_cpl_tlp_sop),
        .rx_cpl_tlp_eop         (rx_cpl_tlp_eop),
        .rx_cpl_tlp_ready       (rx_cpl_tlp_ready),
        .tx_rd_req_tlp_hdr      (tx_rd_req_tlp_hdr),
        .tx_rd_req_tlp_seq      (tx_rd_req_tlp_seq),
        .tx_rd_req_tlp_valid    (tx_rd_req_tlp_valid),
        .tx_rd_req_tlp_sop      (tx_rd_req_tlp_sop),
        .tx_rd_req_tlp_eop      (tx_rd_req_tlp_eop),
        .tx_rd_req_tlp_ready    (tx_rd_req_tlp_ready),
        .tx_wr_req_tlp_data     (tx_wr_req_tlp_data),
        .tx_wr_req_tlp_strb     (tx_wr_req_tlp_strb),
        .tx_wr_req_tlp_hdr      (tx_wr_req_tlp_hdr),
        .tx_wr_req_tlp_seq      (tx_wr_req_tlp_seq),
        .tx_wr_req_tlp_valid    (tx_wr_req_tlp_valid),
        .tx_wr_req_tlp_sop      (tx_wr_req_tlp_sop),
        .tx_wr_req_tlp_eop      (tx_wr_req_tlp_eop),
        .tx_wr_req_tlp_ready    (tx_wr_req_tlp_ready),
        .m_axi_awid             (m_axi_awid),
        .m_axi_awaddr           (m_axi_awaddr),
        .m_axi_awlen            (m_axi_awlen),
        .m_axi_awsize           (m_axi_awsize),
        .m_axi_awburst          (m_axi_awburst),
        .m_axi_awlock           (m_axi_awlock),
        .m_axi_awcache          (m_axi_awcache),
        .m_axi_awprot           (m_axi_awprot),
        .m_axi_awvalid          (m_axi_awvalid),
        .m_axi_awready          (m_axi_awready),
        .m_axi_wdata            (m_axi_wdata),
        .m_axi_wstrb            (m_axi_wstrb),
        .m_axi_wlast            (m_axi_wlast),
        .m_axi_wvalid           (m_axi_wvalid),
        .m_axi_wready           (m_axi_wready),
        .m_axi_bid              (m_axi_bid),
        .m_axi_bresp            (m_axi_bresp),
        .m_axi_bvalid           (m_axi_bvalid),
        .m_axi_bready           (m_axi_bready),
        .m_axi_arid             (m_axi_arid),
        .m_axi_araddr           (m_axi_araddr),
        .m_axi_arlen            (m_axi_arlen),
        .m_axi_arsize           (m_axi_arsize),
        .m_axi_arburst          (m_axi_arburst),
        .m_axi_arlock           (m_axi_arlock),
        .m_axi_arcache          (m_axi_arcache),
        .m_axi_arprot           (m_axi_arprot),
        .m_axi_arvalid          (m_axi_arvalid),
        .m_axi_arready          (m_axi_arready),
        .m_axi_rid              (m_axi_rid),
        .m_axi_rdata            (m_axi_rdata),
        .m_axi_rresp            (m_axi_rresp),
        .m_axi_rlast            (m_axi_rlast),
        .m_axi_rvalid           (m_axi_rvalid),
        .m_axi_rready           (m_axi_rready),
        .psel                   (psel),
        .penable                (penable),
        .pwrite                 (pwrite),
        .paddr                  (paddr),
        .pwdata                 (pwdata),
        .prdata                 (prdata),
        .pready                 (pready),
        .pslverr                (pslverr),
        .pcie_dma_irq           (pcie_dma_irq)
    );

    // -------------------------------------------------------------------------
    // Clock & Reset
    // -------------------------------------------------------------------------
    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    // -------------------------------------------------------------------------
    // APB BFM
    // -------------------------------------------------------------------------
    task apb_idle;
    begin
        psel    <= 1'b0;
        penable <= 1'b0;
        pwrite  <= 1'b0;
        paddr   <= 32'd0;
        pwdata  <= 32'd0;
    end
    endtask

    task apb_write;
        input [31:0] addr;
        input [31:0] data;
    begin
        @(negedge clk);
        psel    <= 1'b1;
        penable <= 1'b0;
        pwrite  <= 1'b1;
        paddr   <= addr;
        pwdata  <= data;
        @(negedge clk);
        penable <= 1'b1;
        while (!pready) @(posedge clk);
        @(negedge clk);
        apb_idle();
    end
    endtask

    task apb_read;
        input  [31:0] addr;
        output [31:0] data;
    begin
        @(negedge clk);
        psel    <= 1'b1;
        penable <= 1'b0;
        pwrite  <= 1'b0;
        paddr   <= addr;
        @(negedge clk);
        penable <= 1'b1;
        while (!pready) @(posedge clk);
        data = prdata;
        @(negedge clk);
        apb_idle();
    end
    endtask

    // -------------------------------------------------------------------------
    // AXI4 Slave Memory Model
    // -------------------------------------------------------------------------
    localparam int MEM_DEPTH = 4096;
    reg [AXI_DATA_W-1:0] mem [0:MEM_DEPTH-1];

    function automatic [$clog2(MEM_DEPTH)-1:0] axi_addr_to_idx;
        input [AXI_ADDR_W-1:0] byte_addr;
    begin
        axi_addr_to_idx = byte_addr[$clog2(MEM_DEPTH)+5:6];
    end
    endfunction

    // Simple AXI slave FSMs — enough for single-beat 64B transactions
    reg aw_active;
    reg [AXI_ADDR_W-1:0] aw_addr;
    reg [AXI_ID_W-1:0]   aw_id;
    reg ar_active;
    reg [AXI_ADDR_W-1:0] ar_addr;
    reg [AXI_ID_W-1:0]   ar_id;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_active     <= 1'b0;
            aw_addr       <= '0;
            aw_id         <= '0;
            ar_active     <= 1'b0;
            ar_addr       <= '0;
            ar_id         <= '0;
            m_axi_awready <= 1'b1;
            m_axi_wready  <= 1'b0;
            m_axi_bvalid  <= 1'b0;
            m_axi_bid     <= '0;
            m_axi_bresp   <= 2'b00;
            m_axi_arready <= 1'b1;
            m_axi_rvalid  <= 1'b0;
            m_axi_rid     <= '0;
            m_axi_rdata   <= '0;
            m_axi_rresp   <= 2'b00;
            m_axi_rlast   <= 1'b0;
        end else begin
            // Write address
            if (m_axi_awvalid && m_axi_awready && !aw_active) begin
                aw_active     <= 1'b1;
                aw_addr       <= m_axi_awaddr;
                aw_id         <= m_axi_awid;
                m_axi_awready <= 1'b0;
                m_axi_wready  <= 1'b1;
            end

            // Write data
            if (aw_active && m_axi_wvalid && m_axi_wready) begin
                mem[axi_addr_to_idx(aw_addr)] <= m_axi_wdata;
                aw_active     <= 1'b0;
                m_axi_wready  <= 1'b0;
                m_axi_bvalid  <= 1'b1;
                m_axi_bid     <= aw_id;
                m_axi_bresp   <= 2'b00;
            end

            // Write response
            if (m_axi_bvalid && m_axi_bready) begin
                m_axi_bvalid  <= 1'b0;
                m_axi_awready <= 1'b1;
            end

            // Read address
            if (m_axi_arvalid && m_axi_arready && !ar_active) begin
                ar_active     <= 1'b1;
                ar_addr       <= m_axi_araddr;
                ar_id         <= m_axi_arid;
                m_axi_arready <= 1'b0;
            end

            // Read data
            if (ar_active) begin
                m_axi_rvalid  <= 1'b1;
                m_axi_rid     <= ar_id;
                m_axi_rdata   <= mem[axi_addr_to_idx(ar_addr)];
                m_axi_rresp   <= 2'b00;
                m_axi_rlast   <= 1'b1;
                if (m_axi_rvalid && m_axi_rready) begin
                    ar_active     <= 1'b0;
                    m_axi_rvalid  <= 1'b0;
                    m_axi_arready <= 1'b1;
                end
            end
        end
    end

    // -------------------------------------------------------------------------
    // TLP BFM helpers
    // -------------------------------------------------------------------------
    // MRd/MWr headers generated by dma_if_pcie use the layout below
    // (matching dma_if_pcie_rd.v / dma_if_pcie_wr.v)
    //   [127:125] Fmt
    //   [124:120] Type
    //   [105:96]  Length (DW)
    //   [95:80]   Requester ID
    //   [79:72]   Tag (PCIe tag, allocated by dma_if_pcie_rd)
    //   [63:2]    Address[63:2] (3-DW header, addr bits [31:2])
    //   [63:34]   Address[63:2] (4-DW header, addr bits [63:34])
    //   [33:32]   Address[33:32] (4-DW header)

    // Build a Completion with Data (CplD) header — 3-DW header + zero DW3
    function automatic [127:0] tlp_cpld_hdr;
        input [9:0]  length_dw;
        input [2:0]  status;
        input [12:0] byte_count;
        input [15:0] requester_id;
        input [9:0]  tag;
        input [6:0]  lower_addr;
    begin
        tlp_cpld_hdr = {
            3'b010,              // Fmt = 3-DW with data
            5'b01010,            // Type = Completion with Data
            1'b0,                // T9
            3'b000,              // TC
            1'b0,                // T8
            1'b0,                // attr
            1'b0,                // LN
            1'b0,                // TH
            1'b0,                // TD
            1'b0,                // EP
            2'b00,               // attr
            2'b00,               // AT (2 bits)
            length_dw[9:0],      // Length
            16'h0000,            // Completer ID
            status[2:0],         // Completion Status
            1'b0,                // BCM
            byte_count[11:0],    // Byte Count (12-bit field; 0 => 4096)
            requester_id[15:0],  // Requester ID
            tag[7:0],            // Tag[7:0]
            1'b0,                // R
            lower_addr[6:0],     // Lower Address
            32'h0000_0000        // DW3 (reserved)
        };
    end
    endfunction

    // Build an error completion header (no data) — 3-DW header + zero DW3
    function automatic [127:0] tlp_cpl_err_hdr;
        input [2:0]  status;
        input [15:0] requester_id;
        input [9:0]  tag;
        input [6:0]  lower_addr;
    begin
        tlp_cpl_err_hdr = {
            3'b000,              // Fmt = 3-DW no data
            5'b01010,            // Type = Completion
            1'b0,                // T9
            3'b000,              // TC
            1'b0,                // T8
            1'b0,                // attr
            1'b0,                // LN
            1'b0,                // TH
            1'b0,                // TD
            1'b0,                // EP
            2'b00,               // attr
            2'b00,               // AT (2 bits)
            10'd0,               // Length = 0
            16'h0000,            // Completer ID
            status[2:0],         // Completion Status
            1'b0,                // BCM
            12'd0,               // Byte Count = 0
            requester_id[15:0],  // Requester ID
            tag[7:0],            // Tag[7:0]
            1'b0,                // R
            lower_addr[6:0],     // Lower Address
            32'h0000_0000        // DW3 (reserved)
        };
    end
    endfunction

    function automatic [511:0] cpld_data_pattern;
        input [31:0] base;
        input [7:0]  tag;
    begin
        cpld_data_pattern = {16{32'h0000_0000}};
        cpld_data_pattern[31:0]   = {base[31:0] ^ {24'h00, tag}};
        cpld_data_pattern[63:32]  = {base[31:0] + 32'h1111_1111};
        cpld_data_pattern[95:64]  = {base[31:0] + 32'h2222_2222};
        cpld_data_pattern[127:96] = {base[31:0] + 32'h3333_3333};
        cpld_data_pattern[159:128]= {base[31:0] + 32'h4444_4444};
        cpld_data_pattern[191:160]= {base[31:0] + 32'h5555_5555};
        cpld_data_pattern[223:192]= {base[31:0] + 32'h6666_6666};
        cpld_data_pattern[255:224]= {base[31:0] + 32'h7777_7777};
        cpld_data_pattern[287:256]= {base[31:0] + 32'h8888_8888};
        cpld_data_pattern[319:288]= {base[31:0] + 32'h9999_9999};
        cpld_data_pattern[351:320]= {base[31:0] + 32'hAAAA_AAAA};
        cpld_data_pattern[383:352]= {base[31:0] + 32'hBBBB_BBBB};
        cpld_data_pattern[415:384]= {base[31:0] + 32'hCCCC_CCCC};
        cpld_data_pattern[447:416]= {base[31:0] + 32'hDDDD_DDDD};
        cpld_data_pattern[479:448]= {base[31:0] + 32'hEEEE_EEEE};
        cpld_data_pattern[511:480]= {base[31:0] + 32'hFFFF_FFFF};
    end
    endfunction

    // -------------------------------------------------------------------------
    // TLP BFM Tasks
    // -------------------------------------------------------------------------
    task tlp_idle;
    begin
        rx_cpl_tlp_valid <= 1'b0;
        rx_cpl_tlp_sop   <= 1'b0;
        rx_cpl_tlp_eop   <= 1'b0;
        rx_cpl_tlp_error <= 4'h0;
        tx_rd_req_tlp_ready <= 1'b1;
        tx_wr_req_tlp_ready <= 1'b1;
    end
    endtask

    // Extract 32-bit PCIe address from MRd/MWr header based on Fmt
    function automatic [31:0] tlp_hdr_addr32;
        input [TLP_HDR_W-1:0] hdr;
    begin
        case (hdr[127:125])
            3'b000, 3'b010: tlp_hdr_addr32 = {hdr[63:34], 2'b00}; // 3-DW header
            3'b001, 3'b011: tlp_hdr_addr32 = {hdr[63:2],  2'b00}; // 4-DW header
            default:        tlp_hdr_addr32 = 32'hFFFF_FFFF;
        endcase
    end
    endfunction

    // Receive an MRd TLP from the DUT and capture the PCIe tag
    task tlp_recv_mrd;
        output [TLP_HDR_W-1:0] hdr;
        output [7:0]           pcie_tag;
        output [31:0]          addr_lo;
    begin
        while (!tx_rd_req_tlp_valid) @(posedge clk);
        @(negedge clk);
        hdr      = tx_rd_req_tlp_hdr;
        pcie_tag = hdr[79:72];
        addr_lo  = tlp_hdr_addr32(hdr);
        @(negedge clk);
    end
    endtask

    // Receive an MWr TLP from the DUT and capture header + data
    task tlp_recv_mwr;
        output [TLP_HDR_W-1:0] hdr;
        output [TLP_DATA_W-1:0] data;
        output [7:0]           pcie_tag;
        output [31:0]          addr_lo;
    begin
        while (!tx_wr_req_tlp_valid) @(posedge clk);
        @(negedge clk);
        hdr      = tx_wr_req_tlp_hdr;
        data     = tx_wr_req_tlp_data;
        pcie_tag = hdr[79:72];
        addr_lo  = tlp_hdr_addr32(hdr);
        @(negedge clk);
    end
    endtask

    // Send a single-segment CplD to the DUT
    task tlp_send_cpld;
        input [127:0] hdr;
        input [511:0] data;
    begin
        @(negedge clk);
        rx_cpl_tlp_hdr   <= hdr;
        rx_cpl_tlp_data  <= data;
        rx_cpl_tlp_error <= 4'h0;
        rx_cpl_tlp_valid <= 1'b1;
        rx_cpl_tlp_sop   <= 1'b1;
        rx_cpl_tlp_eop   <= 1'b1;
        while (!rx_cpl_tlp_ready) @(posedge clk);
        @(negedge clk);
        rx_cpl_tlp_valid <= 1'b0;
        rx_cpl_tlp_sop   <= 1'b0;
        rx_cpl_tlp_eop   <= 1'b0;
    end
    endtask

    // -------------------------------------------------------------------------
    // Test Sequence
    // -------------------------------------------------------------------------
    integer pass_cnt, fail_cnt;
    integer tc;
    reg [31:0] rdata;
    reg [127:0] cap_hdr;
    reg [511:0] cap_data;
    reg [7:0]   cap_tag;
    reg [31:0]  cap_addr;
    reg [31:0]  exp_data;
    reg [AXI_DATA_W-1:0] axi_wdata;
    reg [AXI_DATA_W-1:0] axi_rdata;
    reg [AXI_DATA_W-1:0] exp_axi_data;

    initial begin
        pass_cnt = 0;
        fail_cnt = 0;

        // Initialize
        clk = 1'b0;
        rst_n = 1'b0;
        tlp_idle();
        apb_idle();
        repeat (10) @(posedge clk);
        rst_n = 1'b1;
        repeat (300) @(posedge clk); // wait for dma_if_pcie tag FIFO init

        $display("============================================================");
        $display("[TB] pcie_dma_wrapper Standalone Testbench (T2.2)");
        $display("============================================================");

        // =====================================================================
        // TC1 — APB register readback
        // =====================================================================
        $display("\n--- TC1: APB register write/readback ---");
        begin
            reg [31:0] rb;
            reg ok;
            ok = 1;
            apb_write({20'h40004, REG_PCIE_ADDR_LO}, 32'hA000_0000);
            apb_write({20'h40004, REG_PCIE_ADDR_HI}, 32'h0000_0001);
            apb_write({20'h40004, REG_AXI_ADDR},     32'h2000_0100);
            apb_write({20'h40004, REG_LEN},          32'h0000_0040); // 64 bytes
            apb_write({20'h40004, REG_TAG},          32'h0000_00AB);

            apb_read({20'h40004, REG_PCIE_ADDR_LO}, rb);
            if (rb !== 32'hA000_0000) begin $display("[FAIL] PCIE_ADDR_LO readback mismatch: got %08h", rb); ok = 0; end
            apb_read({20'h40004, REG_PCIE_ADDR_HI}, rb);
            if (rb !== 32'h0000_0001) begin $display("[FAIL] PCIE_ADDR_HI readback mismatch: got %08h", rb); ok = 0; end
            apb_read({20'h40004, REG_AXI_ADDR}, rb);
            if (rb !== 32'h2000_0100) begin $display("[FAIL] AXI_ADDR readback mismatch: got %08h", rb); ok = 0; end
            apb_read({20'h40004, REG_LEN}, rb);
            if (rb !== 32'h0000_0040) begin $display("[FAIL] LEN readback mismatch: got %08h", rb); ok = 0; end
            apb_read({20'h40004, REG_TAG}, rb);
            if (rb !== 32'h0000_00AB) begin $display("[FAIL] TAG readback mismatch: got %08h", rb); ok = 0; end

            if (ok) begin
                $display("PCIE_DMA_TEST: PASS (TC1 APB register readback)");
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("PCIE_DMA_TEST: FAIL (TC1 APB register readback)");
                fail_cnt = fail_cnt + 1;
            end
        end

        // =====================================================================
        // TC2 — Read descriptor emits PCIe MRd
        // =====================================================================
        $display("\n--- TC2: Read descriptor emits PCIe MRd ---");
        begin
            reg ok;
            ok = 1;
            // Set up a 64-byte host->NPU read descriptor (use < 4 GB => 3-DW header)
            apb_write({20'h40004, REG_PCIE_ADDR_LO}, 32'h3000_0000);
            apb_write({20'h40004, REG_PCIE_ADDR_HI}, 32'h0000_0000);
            apb_write({20'h40004, REG_AXI_ADDR},     32'h2000_0200);
            apb_write({20'h40004, REG_LEN},          32'h0000_0040);
            apb_write({20'h40004, REG_TAG},          32'h0000_0055);
            apb_write({20'h40004, REG_CTRL},         32'h0000_0001); // start_rd

            tlp_recv_mrd(cap_hdr, cap_tag, cap_addr);

            // Verify MRd Fmt+Type = 3-DW without data => 0x00
            if (cap_hdr[127:120] !== 8'h00) begin
                $display("[FAIL] MRd Fmt+Type mismatch: expected 0x00, got %02h", cap_hdr[127:120]);
                ok = 0;
            end
            if (cap_hdr[105:96] !== 10'd16) begin
                $display("[FAIL] MRd length mismatch: expected 16 DW, got %0d", cap_hdr[105:96]);
                ok = 0;
            end
            if (cap_addr !== 32'h3000_0000) begin
                $display("[FAIL] MRd address mismatch: expected 0x3000_0000, got %08h", cap_addr);
                ok = 0;
            end

            // Drain the descriptor so the FSM can proceed; we will complete it in TC3
            // (the MRd header is consumed, DUT waits for CplD)
            if (ok) begin
                $display("PCIE_DMA_TEST: PASS (TC2 read descriptor emits MRd, tag=0x%02h)", cap_tag);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("PCIE_DMA_TEST: FAIL (TC2 read descriptor emits MRd)");
                fail_cnt = fail_cnt + 1;
            end
        end

        // =====================================================================
        // TC3 — PCIe CplD → AXI write
        // =====================================================================
        $display("\n--- TC3: PCIe CplD drives AXI write ---");
        begin
            reg ok;
            ok = 1;
            // Drive the completion for the MRd captured in TC2
            exp_axi_data = cpld_data_pattern(32'h3000_0000, 8'h55);
            tlp_send_cpld(tlp_cpld_hdr(10'd16, 3'b000, 13'd64, 16'h0001,
                                       {2'b00, cap_tag}, 7'h00),
                          exp_axi_data);

            // Wait for AXI write to complete
            tc = 0;
            while (!(m_axi_bvalid && m_axi_bready) && tc < 5000) begin
                @(posedge clk);
                tc = tc + 1;
            end
            repeat (5) @(posedge clk);

            if (m_axi_awaddr !== 32'h2000_0200) begin
                $display("[FAIL] AXI write address mismatch: expected 0x2000_0200, got %08h", m_axi_awaddr);
                ok = 0;
            end
            axi_wdata = mem[axi_addr_to_idx(32'h2000_0200)];
            if (axi_wdata !== exp_axi_data) begin
                $display("[FAIL] AXI write data mismatch");
                $display("       expected: %032h", exp_axi_data);
                $display("       got:      %032h", axi_wdata);
                ok = 0;
            end

            // Also check APB status: rd_done should be set
            apb_read({20'h40004, REG_STATUS}, rdata);
            if (rdata[2] !== 1'b1) begin
                $display("[FAIL] STATUS.rd_done not set after CplD; STATUS=%08h", rdata);
                ok = 0;
            end

            if (ok) begin
                $display("PCIE_DMA_TEST: PASS (TC3 CplD -> AXI write)");
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("PCIE_DMA_TEST: FAIL (TC3 CplD -> AXI write)");
                fail_cnt = fail_cnt + 1;
            end
        end

        // =====================================================================
        // TC4 — Write descriptor emits PCIe MWr
        // =====================================================================
        $display("\n--- TC4: Write descriptor emits PCIe MWr ---");
        begin
            reg ok;
            ok = 1;
            // Pre-fill the AXI slave memory at the source address with a pattern
            exp_axi_data = cpld_data_pattern(32'h4000_0000, 8'h66);
            mem[axi_addr_to_idx(32'h2000_0300)] = exp_axi_data;

            // Set up a 64-byte NPU->host write descriptor (use < 4 GB => 3-DW header)
            apb_write({20'h40004, REG_PCIE_ADDR_LO}, 32'h4000_0000);
            apb_write({20'h40004, REG_PCIE_ADDR_HI}, 32'h0000_0000);
            apb_write({20'h40004, REG_AXI_ADDR},     32'h2000_0300);
            apb_write({20'h40004, REG_LEN},          32'h0000_0040);
            apb_write({20'h40004, REG_TAG},          32'h0000_0066);
            apb_write({20'h40004, REG_CTRL},         32'h0000_0002); // start_wr

            // Wait for AXI read request then provide data (slave model does it automatically)
            tc = 0;
            while (!(m_axi_rvalid && m_axi_rready) && tc < 5000) begin
                @(posedge clk);
                tc = tc + 1;
            end

            // Wait for MWr TLP
            tlp_recv_mwr(cap_hdr, cap_data, cap_tag, cap_addr);

            // MWr Fmt+Type: 3-DW with data => 0x40
            if (cap_hdr[127:120] !== 8'h40) begin
                $display("[FAIL] MWr Fmt+Type mismatch: expected 0x40, got %02h", cap_hdr[127:120]);
                ok = 0;
            end
            if (cap_addr !== 32'h4000_0000) begin
                $display("[FAIL] MWr address mismatch: expected 0x4000_0000, got %08h", cap_addr);
                ok = 0;
            end
            if (cap_data !== exp_axi_data) begin
                $display("[FAIL] MWr data mismatch");
                $display("       expected: %032h", exp_axi_data);
                $display("       got:      %032h", cap_data);
                ok = 0;
            end

            // Check APB status: wr_done should be set
            apb_read({20'h40004, REG_STATUS}, rdata);
            if (rdata[3] !== 1'b1) begin
                $display("[FAIL] STATUS.wr_done not set after MWr; STATUS=%08h", rdata);
                ok = 0;
            end

            if (ok) begin
                $display("PCIE_DMA_TEST: PASS (TC4 write descriptor emits MWr)");
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("PCIE_DMA_TEST: FAIL (TC4 write descriptor emits MWr)");
                fail_cnt = fail_cnt + 1;
            end
        end

        // =====================================================================
        // TC5 — Completion error propagation
        // =====================================================================
        $display("\n--- TC5: Completion UR error propagation ---");
        begin
            reg ok;
            ok = 1;
            // Clear prior status via abort then start a fresh read descriptor
            apb_write({20'h40004, REG_CTRL},         32'h0000_0004); // abort
            repeat (10) @(posedge clk);

            apb_write({20'h40004, REG_PCIE_ADDR_LO}, 32'h5000_0000);
            apb_write({20'h40004, REG_PCIE_ADDR_HI}, 32'h0000_0000);
            apb_write({20'h40004, REG_AXI_ADDR},     32'h2000_0400);
            apb_write({20'h40004, REG_LEN},          32'h0000_0040);
            apb_write({20'h40004, REG_TAG},          32'h0000_0077);
            apb_write({20'h40004, REG_CTRL},         32'h0000_0001); // start_rd

            tlp_recv_mrd(cap_hdr, cap_tag, cap_addr);

            // Drive a UR completion (no data)
            tlp_send_cpld(tlp_cpl_err_hdr(3'b001, 16'h0001, {2'b00, cap_tag}, 7'h00),
                          512'd0);

            // Wait for error to propagate
            tc = 0;
            while (tc < 5000) begin
                apb_read({20'h40004, REG_RD_ERR_CODE}, rdata);
                if (rdata[3:0] !== 4'h0) tc = 5000;
                else begin
                    @(posedge clk);
                    tc = tc + 1;
                end
            end

            apb_read({20'h40004, REG_RD_ERR_CODE}, rdata);
            if (rdata[3:0] === 4'h0) begin
                $display("[FAIL] RD_ERR_CODE still zero after UR completion");
                ok = 0;
            end else begin
                $display("[INFO] RD_ERR_CODE = 0x%01h (non-zero as expected)", rdata[3:0]);
            end

            apb_read({20'h40004, REG_STATUS}, rdata);
            if (rdata[4] !== 1'b1) begin
                $display("[FAIL] STATUS.error not set after UR completion; STATUS=%08h", rdata);
                ok = 0;
            end

            if (ok) begin
                $display("PCIE_DMA_TEST: PASS (TC5 UR error propagation)");
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("PCIE_DMA_TEST: FAIL (TC5 UR error propagation)");
                fail_cnt = fail_cnt + 1;
            end
        end

        // =====================================================================
        // Summary
        // =====================================================================
        $display("\n============================================================");
        $display("[TB] Summary: %0d passed, %0d failed", pass_cnt, fail_cnt);
        if (fail_cnt == 0)
            $display("PCIE_DMA_TEST: ALL PASS");
        else
            $display("PCIE_DMA_TEST: FAIL");
        $display("============================================================");
        $finish;
    end

    // -------------------------------------------------------------------------
    // Timeout watchdog
    // -------------------------------------------------------------------------
    initial begin
        #(MAX_TIMEOUT * 2);
        $display("[ERROR] Timeout — simulation did not finish");
        $display("PCIE_DMA_TEST: FAIL");
        $finish;
    end

endmodule

`resetall
