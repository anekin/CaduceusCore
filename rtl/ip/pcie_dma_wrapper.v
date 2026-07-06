//=============================================================================
// pcie_dma_wrapper — NPU-initiated PCIe DMA Engine Wrapper
//=============================================================================
// CaduceusCore SoC Phase 4 / Task T2.1
//
// Wraps dma_if_pcie + dma_if_axi (alexforencich/verilog-pcie, MIT) with:
//   • dma_if_pcie  — PCIe TLP ↔ RAM buffer DMA engine (MRd/MWr generation)
//   • dma_if_axi   — RAM buffer ↔ AXI4 DMA engine (read/write NPU memory)
//   • Cross-connected RAM buffer (two dma_psdpram instances)
//   • APB slave register file with 2-phase descriptor FSM
//   • pcie_dma_irq output on descriptor completion (gated by irq_en)
//
// Data flow:
//   HOST→NPU (read):  PCIe MRd → CPLD data → RAM(pcie2axi) → AXI write
//   NPU→HOST (write): AXI read → RAM(axi2pcie) → PCIe MWr
//
// APB slave at 0x4000_4000 (SoC unified address space, port 4).
//
// Must NOT modify vendored verilog-pcie source files.
//=============================================================================

`resetall
`timescale 1ns / 1ps
`default_nettype none

module pcie_dma_wrapper #(
    // ── TLP parameters (PCIe Transaction Layer) ────────────────────────────
    parameter TLP_DATA_WIDTH      = 512,
    parameter TLP_STRB_WIDTH      = TLP_DATA_WIDTH / 32,    // 16
    parameter TLP_HDR_WIDTH       = 128,
    parameter TLP_SEG_COUNT       = 1,

    // ── PCIe DMA engine parameters ─────────────────────────────────────────
    parameter PCIE_ADDR_WIDTH     = 64,
    parameter PCIE_TAG_COUNT      = 256,
    parameter READ_OP_TABLE_SIZE  = 256,
    parameter WRITE_OP_TABLE_SIZE = 256,
    parameter READ_TX_LIMIT       = 128,
    parameter WRITE_TX_LIMIT      = 128,
    parameter READ_CPLH_FC_LIMIT  = 64,
    parameter READ_CPLD_FC_LIMIT  = 256,
    parameter IMM_ENABLE          = 0,
    parameter IMM_WIDTH           = 32,

    // ── AXI4 master parameters ─────────────────────────────────────────────
    parameter AXI_DATA_WIDTH      = 512,
    parameter AXI_ADDR_WIDTH      = 32,
    parameter AXI_STRB_WIDTH      = AXI_DATA_WIDTH / 8,    // 64
    parameter AXI_ID_WIDTH        = 6,
    parameter AXI_MAX_BURST_LEN   = 256,

    // ── RAM buffer parameters (shared between dma_if_pcie and dma_if_axi) ──
    parameter RAM_SEL_WIDTH       = 2,
    parameter RAM_ADDR_WIDTH      = 16,
    parameter RAM_SEG_COUNT       = 2,
    parameter RAM_SEG_DATA_WIDTH  = AXI_DATA_WIDTH * 2 / RAM_SEG_COUNT,  // 512
    parameter RAM_SEG_BE_WIDTH    = RAM_SEG_DATA_WIDTH / 8,              // 64
    parameter RAM_SEG_ADDR_WIDTH  = RAM_ADDR_WIDTH - $clog2(RAM_SEG_COUNT * RAM_SEG_BE_WIDTH),  // 9

    // ── Descriptor parameters ──────────────────────────────────────────────
    parameter LEN_WIDTH           = 16,
    parameter TAG_WIDTH           = 8,
    parameter TX_SEQ_NUM_COUNT    = 1,
    parameter TX_SEQ_NUM_WIDTH    = 5,
    parameter TX_SEQ_NUM_ENABLE   = 0,
    parameter TLP_FORCE_64_BIT_ADDR = 0,
    parameter CHECK_BUS_NUMBER    = 1,

    // ── RAM buffer sizing (for dma_psdpram instantiation) ──────────────────
    parameter RAM_BUF_SIZE        = RAM_SEG_COUNT * RAM_SEG_BE_WIDTH * (2 ** RAM_SEG_ADDR_WIDTH)  // 65536
) (
    input  wire                                    clk,
    input  wire                                    rst_n,

    // ── TLP RX (completion from host) ──────────────────────────────────────
    input  wire [TLP_DATA_WIDTH-1:0]               rx_cpl_tlp_data,
    input  wire [TLP_SEG_COUNT*TLP_HDR_WIDTH-1:0]  rx_cpl_tlp_hdr,
    input  wire [TLP_SEG_COUNT*4-1:0]               rx_cpl_tlp_error,
    input  wire [TLP_SEG_COUNT-1:0]                 rx_cpl_tlp_valid,
    input  wire [TLP_SEG_COUNT-1:0]                 rx_cpl_tlp_sop,
    input  wire [TLP_SEG_COUNT-1:0]                 rx_cpl_tlp_eop,
    output wire                                     rx_cpl_tlp_ready,

    // ── TLP TX (read request to host) ──────────────────────────────────────
    output wire [TLP_SEG_COUNT*TLP_HDR_WIDTH-1:0]  tx_rd_req_tlp_hdr,
    output wire [TLP_SEG_COUNT*TX_SEQ_NUM_WIDTH-1:0] tx_rd_req_tlp_seq,
    output wire [TLP_SEG_COUNT-1:0]                 tx_rd_req_tlp_valid,
    output wire [TLP_SEG_COUNT-1:0]                 tx_rd_req_tlp_sop,
    output wire [TLP_SEG_COUNT-1:0]                 tx_rd_req_tlp_eop,
    input  wire                                     tx_rd_req_tlp_ready,

    // ── TLP TX (write request to host) ─────────────────────────────────────
    output wire [TLP_DATA_WIDTH-1:0]                tx_wr_req_tlp_data,
    output wire [TLP_STRB_WIDTH-1:0]                tx_wr_req_tlp_strb,
    output wire [TLP_SEG_COUNT*TLP_HDR_WIDTH-1:0]  tx_wr_req_tlp_hdr,
    output wire [TLP_SEG_COUNT*TX_SEQ_NUM_WIDTH-1:0] tx_wr_req_tlp_seq,
    output wire [TLP_SEG_COUNT-1:0]                 tx_wr_req_tlp_valid,
    output wire [TLP_SEG_COUNT-1:0]                 tx_wr_req_tlp_sop,
    output wire [TLP_SEG_COUNT-1:0]                 tx_wr_req_tlp_eop,
    input  wire                                     tx_wr_req_tlp_ready,

    // ── AXI4 Master interface ──────────────────────────────────────────────
    output wire [AXI_ID_WIDTH-1:0]                  m_axi_awid,
    output wire [AXI_ADDR_WIDTH-1:0]                m_axi_awaddr,
    output wire [7:0]                               m_axi_awlen,
    output wire [2:0]                               m_axi_awsize,
    output wire [1:0]                               m_axi_awburst,
    output wire                                     m_axi_awlock,
    output wire [3:0]                               m_axi_awcache,
    output wire [2:0]                               m_axi_awprot,
    output wire                                     m_axi_awvalid,
    input  wire                                     m_axi_awready,
    output wire [AXI_DATA_WIDTH-1:0]                m_axi_wdata,
    output wire [AXI_STRB_WIDTH-1:0]                m_axi_wstrb,
    output wire                                     m_axi_wlast,
    output wire                                     m_axi_wvalid,
    input  wire                                     m_axi_wready,
    input  wire [AXI_ID_WIDTH-1:0]                  m_axi_bid,
    input  wire [1:0]                               m_axi_bresp,
    input  wire                                     m_axi_bvalid,
    output wire                                     m_axi_bready,
    output wire [AXI_ID_WIDTH-1:0]                  m_axi_arid,
    output wire [AXI_ADDR_WIDTH-1:0]                m_axi_araddr,
    output wire [7:0]                               m_axi_arlen,
    output wire [2:0]                               m_axi_arsize,
    output wire [1:0]                               m_axi_arburst,
    output wire                                     m_axi_arlock,
    output wire [3:0]                               m_axi_arcache,
    output wire [2:0]                               m_axi_arprot,
    output wire                                     m_axi_arvalid,
    input  wire                                     m_axi_arready,
    input  wire [AXI_ID_WIDTH-1:0]                  m_axi_rid,
    input  wire [AXI_DATA_WIDTH-1:0]                m_axi_rdata,
    input  wire [1:0]                               m_axi_rresp,
    input  wire                                     m_axi_rlast,
    input  wire                                     m_axi_rvalid,
    output wire                                     m_axi_rready,

    // ── APB Slave (from apb_decoder port 4, 0x4000_4000 ~ 0x4000_4FFF) ────
    input  wire                                     psel,
    input  wire                                     penable,
    input  wire                                     pwrite,
    input  wire [31:0]                              paddr,
    input  wire [31:0]                              pwdata,
    output wire [31:0]                              prdata,
    output wire                                     pready,
    output wire                                     pslverr,

    // ── Interrupt output (to intc_top source bit 4) ───────────────────────
    output wire                                     pcie_dma_irq
);

    //=========================================================================
    // SECTION 1: Reset conversion and APB decode wires
    //=========================================================================
    wire rst;
    assign rst = ~rst_n;

    wire        apb_write;
    wire        sel_ctrl;
    wire        sel_status;
    wire        sel_pcie_addr_lo;
    wire        sel_pcie_addr_hi;
    wire        sel_axi_addr;
    wire        sel_len;
    wire        sel_tag;
    wire        sel_rd_err;
    wire        sel_wr_err;
    wire        valid_sel;

    assign apb_write        = psel && penable && pwrite;
    assign sel_ctrl         = (paddr[11:0] == 12'h000);
    assign sel_status       = (paddr[11:0] == 12'h004);
    assign sel_pcie_addr_lo = (paddr[11:0] == 12'h008);
    assign sel_pcie_addr_hi = (paddr[11:0] == 12'h00C);
    assign sel_axi_addr     = (paddr[11:0] == 12'h010);
    assign sel_len          = (paddr[11:0] == 12'h014);
    assign sel_tag          = (paddr[11:0] == 12'h018);
    assign sel_rd_err       = (paddr[11:0] == 12'h01C);
    assign sel_wr_err       = (paddr[11:0] == 12'h020);
    assign valid_sel = sel_ctrl || sel_status || sel_pcie_addr_lo ||
                       sel_pcie_addr_hi || sel_axi_addr ||
                       sel_len || sel_tag || sel_rd_err || sel_wr_err;
    assign pready  = psel ? 1'b1 : 1'b0;
    assign pslverr = psel && penable && !valid_sel;

    // ── APB write pulse edges ──────────────────────────────────────────────
    wire start_rd_pulse  = apb_write && sel_ctrl && pwdata[0];
    wire start_wr_pulse  = apb_write && sel_ctrl && pwdata[1];
    wire abort_pulse     = apb_write && sel_ctrl && pwdata[2];

    //=========================================================================
    // SECTION 2: Descriptor status signals (from sub-modules)
    //=========================================================================
    wire [TAG_WIDTH-1:0] pcie_read_desc_status_tag;
    wire [3:0]           pcie_read_desc_status_error;
    wire                 pcie_read_desc_status_valid;
    wire [TAG_WIDTH-1:0] pcie_write_desc_status_tag;
    wire [3:0]           pcie_write_desc_status_error;
    wire                 pcie_write_desc_status_valid;
    wire [TAG_WIDTH-1:0] axi_read_desc_status_tag;
    wire [3:0]           axi_read_desc_status_error;
    wire                 axi_read_desc_status_valid;
    wire [TAG_WIDTH-1:0] axi_write_desc_status_tag;
    wire [3:0]           axi_write_desc_status_error;
    wire                 axi_write_desc_status_valid;

    // ── Descriptor ready signals (from sub-modules) ────────────────────────
    wire pcie_read_desc_ready;
    wire pcie_write_desc_ready;
    wire axi_read_desc_ready;
    wire axi_write_desc_ready;

    // ── Busy status from dma_if_pcie ───────────────────────────────────────
    wire rd_busy;
    wire wr_busy;

    //=========================================================================
    // SECTION 3: APB Register File (sequential)
    //=========================================================================
    reg  [3:0]  ctrl_reg;
    reg  [31:0] pcie_addr_lo_reg;
    reg  [31:0] pcie_addr_hi_reg;
    reg  [31:0] axi_addr_reg;
    reg  [15:0] len_reg;
    reg  [7:0]  tag_reg;
    reg  [3:0]  rd_err_code_reg;
    reg  [3:0]  wr_err_code_reg;
    reg         rd_done_reg;
    reg         wr_done_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ctrl_reg <= 4'h0;
        end else if (apb_write && sel_ctrl) begin
            // irq_en is persistent; start/abort are pulse bits, store 0
            ctrl_reg[3]   <= pwdata[3];
            ctrl_reg[2:0] <= 3'h0;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pcie_addr_lo_reg <= 32'h0;
        end else if (apb_write && sel_pcie_addr_lo) begin
            pcie_addr_lo_reg <= pwdata;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pcie_addr_hi_reg <= 32'h0;
        end else if (apb_write && sel_pcie_addr_hi) begin
            pcie_addr_hi_reg <= pwdata;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            axi_addr_reg <= 32'h0;
        end else if (apb_write && sel_axi_addr) begin
            axi_addr_reg <= pwdata;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            len_reg <= 16'h0;
        end else if (apb_write && sel_len) begin
            len_reg <= pwdata[15:0];
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            tag_reg <= 8'h0;
        end else if (apb_write && sel_tag) begin
            tag_reg <= pwdata[7:0];
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_err_code_reg <= 4'h0;
        end else if (pcie_read_desc_status_valid) begin
            rd_err_code_reg <= pcie_read_desc_status_error;
        end else if (axi_write_desc_status_valid) begin
            rd_err_code_reg <= axi_write_desc_status_error;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_err_code_reg <= 4'h0;
        end else if (axi_read_desc_status_valid) begin
            wr_err_code_reg <= axi_read_desc_status_error;
        end else if (pcie_write_desc_status_valid) begin
            wr_err_code_reg <= pcie_write_desc_status_error;
        end
    end

    //=========================================================================
    // SECTION 4: APB Read Data Mux (combinational)
    //=========================================================================
    wire has_error;
    assign has_error = (pcie_write_desc_status_error != 4'h0) ||
                       (pcie_read_desc_status_error != 4'h0) ||
                       (axi_write_desc_status_error != 4'h0) ||
                       (axi_read_desc_status_error != 4'h0);

    wire [4:0] status_bits;
    assign status_bits = {has_error, wr_done_reg, rd_done_reg, wr_busy, rd_busy};

    assign prdata = sel_ctrl         ? {28'h0,                ctrl_reg} :
                    sel_status       ? {27'h0,                status_bits} :
                    sel_pcie_addr_lo ? pcie_addr_lo_reg :
                    sel_pcie_addr_hi ? pcie_addr_hi_reg :
                    sel_axi_addr     ? axi_addr_reg :
                    sel_len          ? {16'h0,                len_reg} :
                    sel_tag          ? {24'h0,                tag_reg} :
                    sel_rd_err       ? {28'h0,                rd_err_code_reg} :
                    sel_wr_err       ? {28'h0,                wr_err_code_reg} :
                    32'h0;

    //=========================================================================
    // SECTION 5: Descriptor FSM (2-phase pipeline)
    //=========================================================================
    //
    // States:
    //   IDLE          — waiting for start_rd or start_wr
    //   RD_PCIE_ISSUE — driving s_axis_read_desc_valid to dma_if_pcie
    //   RD_AXI_ISSUE  — driving s_axis_write_desc_valid to dma_if_axi
    //   WR_AXI_ISSUE  — driving s_axis_read_desc_valid to dma_if_axi
    //   WR_PCIE_ISSUE — driving s_axis_write_desc_valid to dma_if_pcie
    //
    // HOST→NPU (read):
    //   IDLE → RD_PCIE_ISSUE (issue MRd to host via dma_if_pcie)
    //   → IDLE (wait for pcie_read_desc_status_valid)
    //   → RD_AXI_ISSUE (issue AXI write descriptor to dma_if_axi)
    //   → IDLE (wait for axi_write_desc_status_valid → set rd_done)
    //
    // NPU→HOST (write):
    //   IDLE → WR_AXI_ISSUE (issue AXI read descriptor to dma_if_axi)
    //   → IDLE (wait for axi_read_desc_status_valid)
    //   → WR_PCIE_ISSUE (issue MWr descriptor to dma_if_pcie)
    //   → IDLE (wait for pcie_write_desc_status_valid → set wr_done)

    localparam FSM_IDLE          = 3'd0;
    localparam FSM_RD_PCIE_ISSUE = 3'd1;
    localparam FSM_RD_AXI_ISSUE  = 3'd2;
    localparam FSM_WR_AXI_ISSUE  = 3'd3;
    localparam FSM_WR_PCIE_ISSUE = 3'd4;

    reg  [2:0]  fsm_state;

    // ── Descriptor values (latched from APB registers at start time) ───────
    reg  [PCIE_ADDR_WIDTH-1:0]  desc_pcie_addr;
    reg  [AXI_ADDR_WIDTH-1:0]   desc_axi_addr;
    reg  [LEN_WIDTH-1:0]        desc_len;
    reg  [TAG_WIDTH-1:0]        desc_tag;

    // ── Phase completion flags ─────────────────────────────────────────────
    reg pcie_rd_phase_done;
    reg axi_wr_phase_done;
    reg axi_rd_phase_done;
    reg pcie_wr_phase_done;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            fsm_state          <= FSM_IDLE;
            desc_pcie_addr     <= {PCIE_ADDR_WIDTH{1'b0}};
            desc_axi_addr      <= {AXI_ADDR_WIDTH{1'b0}};
            desc_len           <= {LEN_WIDTH{1'b0}};
            desc_tag           <= {TAG_WIDTH{1'b0}};
            pcie_rd_phase_done <= 1'b0;
            axi_wr_phase_done  <= 1'b0;
            axi_rd_phase_done  <= 1'b0;
            pcie_wr_phase_done <= 1'b0;
            rd_done_reg        <= 1'b0;
            wr_done_reg        <= 1'b0;
        end else begin
            // ── Abort clears all tracking and done flags ───────────────────
            if (abort_pulse) begin
                fsm_state          <= FSM_IDLE;
                pcie_rd_phase_done <= 1'b0;
                axi_wr_phase_done  <= 1'b0;
                axi_rd_phase_done  <= 1'b0;
                pcie_wr_phase_done <= 1'b0;
                rd_done_reg        <= 1'b0;
                wr_done_reg        <= 1'b0;
            end else begin
                case (fsm_state)
                    FSM_IDLE: begin
                        if (start_rd_pulse) begin
                            desc_pcie_addr     <= {pcie_addr_hi_reg, pcie_addr_lo_reg};
                            desc_axi_addr      <= axi_addr_reg;
                            desc_len           <= len_reg;
                            desc_tag           <= tag_reg;
                            pcie_rd_phase_done <= 1'b0;
                            axi_wr_phase_done  <= 1'b0;
                            rd_done_reg        <= 1'b0;
                            fsm_state          <= FSM_RD_PCIE_ISSUE;
                        end else if (start_wr_pulse) begin
                            desc_pcie_addr     <= {pcie_addr_hi_reg, pcie_addr_lo_reg};
                            desc_axi_addr      <= axi_addr_reg;
                            desc_len           <= len_reg;
                            desc_tag           <= tag_reg;
                            axi_rd_phase_done  <= 1'b0;
                            pcie_wr_phase_done <= 1'b0;
                            wr_done_reg        <= 1'b0;
                            fsm_state          <= FSM_WR_AXI_ISSUE;
                        end
                    end

                    FSM_RD_PCIE_ISSUE: begin
                        if (pcie_read_desc_ready) begin
                            pcie_rd_phase_done <= 1'b1;
                            fsm_state          <= FSM_IDLE;
                        end
                    end

                    FSM_WR_AXI_ISSUE: begin
                        if (axi_read_desc_ready) begin
                            axi_rd_phase_done <= 1'b1;
                            fsm_state         <= FSM_IDLE;
                        end
                    end

                    FSM_RD_AXI_ISSUE: begin
                        if (axi_write_desc_ready) begin
                            axi_wr_phase_done <= 1'b1;
                            fsm_state         <= FSM_IDLE;
                        end
                    end

                    FSM_WR_PCIE_ISSUE: begin
                        if (pcie_write_desc_ready) begin
                            pcie_wr_phase_done <= 1'b1;
                            fsm_state          <= FSM_IDLE;
                        end
                    end

                    default: fsm_state <= FSM_IDLE;
                endcase

                // ── Read pipeline: Phase1 done → start Phase2 ──────────────
                if (pcie_rd_phase_done && pcie_read_desc_status_valid &&
                    fsm_state == FSM_IDLE && !axi_wr_phase_done) begin
                    fsm_state <= FSM_RD_AXI_ISSUE;
                end

                // ── Read pipeline: Phase2 done → set rd_done ───────────────
                if (axi_wr_phase_done && axi_write_desc_status_valid) begin
                    rd_done_reg <= 1'b1;
                end

                // ── Write pipeline: Phase1 done → start Phase2 ─────────────
                if (axi_rd_phase_done && axi_read_desc_status_valid &&
                    fsm_state == FSM_IDLE && !pcie_wr_phase_done) begin
                    fsm_state <= FSM_WR_PCIE_ISSUE;
                end

                // ── Write pipeline: Phase2 done → set wr_done ──────────────
                if (pcie_wr_phase_done && pcie_write_desc_status_valid) begin
                    wr_done_reg <= 1'b1;
                end
            end
        end
    end

    // ── Descriptor valid outputs (FSM drives these combinational) ──────────
    wire pcie_read_desc_valid;
    wire pcie_write_desc_valid;
    wire axi_read_desc_valid;
    wire axi_write_desc_valid;

    assign pcie_read_desc_valid  = (fsm_state == FSM_RD_PCIE_ISSUE);
    assign pcie_write_desc_valid = (fsm_state == FSM_WR_PCIE_ISSUE);
    assign axi_read_desc_valid   = (fsm_state == FSM_WR_AXI_ISSUE);
    assign axi_write_desc_valid  = (fsm_state == FSM_RD_AXI_ISSUE);

    // ── IRQ output ─────────────────────────────────────────────────────────
    assign pcie_dma_irq = ctrl_reg[3] && (rd_done_reg || wr_done_reg);

    //=========================================================================
    // SECTION 6: RAM Buffer Cross-Connect Wires
    //=========================================================================
    wire [RAM_SEG_COUNT*RAM_SEG_BE_WIDTH-1:0]     pcie_ram_wr_be;
    wire [RAM_SEG_COUNT*RAM_SEG_ADDR_WIDTH-1:0]   pcie_ram_wr_addr;
    wire [RAM_SEG_COUNT*RAM_SEG_DATA_WIDTH-1:0]   pcie_ram_wr_data;
    wire [RAM_SEG_COUNT-1:0]                       pcie_ram_wr_valid;
    wire [RAM_SEG_COUNT-1:0]                       pcie_ram_wr_ready;
    wire [RAM_SEG_COUNT-1:0]                       pcie_ram_wr_done;
    wire [RAM_SEG_COUNT*RAM_SEG_ADDR_WIDTH-1:0]   pcie_ram_rd_addr;
    wire [RAM_SEG_COUNT-1:0]                       pcie_ram_rd_valid;
    wire [RAM_SEG_COUNT-1:0]                       pcie_ram_rd_ready;
    wire [RAM_SEG_COUNT*RAM_SEG_DATA_WIDTH-1:0]   pcie_ram_rd_data;
    wire [RAM_SEG_COUNT-1:0]                       pcie_ram_rd_resp_valid;
    wire [RAM_SEG_COUNT-1:0]                       pcie_ram_rd_resp_ready;

    wire [RAM_SEG_COUNT*RAM_SEG_BE_WIDTH-1:0]     axi_ram_wr_be;
    wire [RAM_SEG_COUNT*RAM_SEG_ADDR_WIDTH-1:0]   axi_ram_wr_addr;
    wire [RAM_SEG_COUNT*RAM_SEG_DATA_WIDTH-1:0]   axi_ram_wr_data;
    wire [RAM_SEG_COUNT-1:0]                       axi_ram_wr_valid;
    wire [RAM_SEG_COUNT-1:0]                       axi_ram_wr_ready;
    wire [RAM_SEG_COUNT-1:0]                       axi_ram_wr_done;
    wire [RAM_SEG_COUNT*RAM_SEG_ADDR_WIDTH-1:0]   axi_ram_rd_addr;
    wire [RAM_SEG_COUNT-1:0]                       axi_ram_rd_valid;
    wire [RAM_SEG_COUNT-1:0]                       axi_ram_rd_ready;
    wire [RAM_SEG_COUNT*RAM_SEG_DATA_WIDTH-1:0]   axi_ram_rd_data;
    wire [RAM_SEG_COUNT-1:0]                       axi_ram_rd_resp_valid;
    wire [RAM_SEG_COUNT-1:0]                       axi_ram_rd_resp_ready;

    //=========================================================================
    // SECTION 7: Instantiation — dma_if_pcie
    //=========================================================================
    // Parameters per plan C2:
    //   TLP_DATA_WIDTH=512, TLP_HDR_WIDTH=128, TLP_SEG_COUNT=1
    //   PCIE_ADDR_WIDTH=64, PCIE_TAG_COUNT=256
    //   READ_OP_TABLE_SIZE=256, WRITE_OP_TABLE_SIZE=256
    //   READ_TX_LIMIT=128, WRITE_TX_LIMIT=128
    //   READ_CPLH_FC_LIMIT=64, READ_CPLD_FC_LIMIT=256, IMM_ENABLE=0

    dma_if_pcie #(
        .TLP_DATA_WIDTH      (TLP_DATA_WIDTH),
        .TLP_HDR_WIDTH       (TLP_HDR_WIDTH),
        .TLP_SEG_COUNT       (TLP_SEG_COUNT),
        .TX_SEQ_NUM_COUNT    (TX_SEQ_NUM_COUNT),
        .TX_SEQ_NUM_WIDTH    (TX_SEQ_NUM_WIDTH),
        .TX_SEQ_NUM_ENABLE   (TX_SEQ_NUM_ENABLE),
        .RAM_SEL_WIDTH       (RAM_SEL_WIDTH),
        .RAM_ADDR_WIDTH      (RAM_ADDR_WIDTH),
        .RAM_SEG_COUNT       (RAM_SEG_COUNT),
        .PCIE_ADDR_WIDTH     (PCIE_ADDR_WIDTH),
        .PCIE_TAG_COUNT      (PCIE_TAG_COUNT),
        .IMM_ENABLE          (IMM_ENABLE),
        .IMM_WIDTH           (IMM_WIDTH),
        .LEN_WIDTH           (LEN_WIDTH),
        .TAG_WIDTH           (TAG_WIDTH),
        .READ_OP_TABLE_SIZE  (READ_OP_TABLE_SIZE),
        .READ_TX_LIMIT       (READ_TX_LIMIT),
        .READ_CPLH_FC_LIMIT  (READ_CPLH_FC_LIMIT),
        .READ_CPLD_FC_LIMIT  (READ_CPLD_FC_LIMIT),
        .WRITE_OP_TABLE_SIZE (WRITE_OP_TABLE_SIZE),
        .WRITE_TX_LIMIT      (WRITE_TX_LIMIT),
        .TLP_FORCE_64_BIT_ADDR(TLP_FORCE_64_BIT_ADDR),
        .CHECK_BUS_NUMBER    (CHECK_BUS_NUMBER)
    ) dma_if_pcie_inst (
        .clk                                    (clk),
        .rst                                    (rst),

        // TLP input (completion)
        .rx_cpl_tlp_data                        (rx_cpl_tlp_data),
        .rx_cpl_tlp_hdr                         (rx_cpl_tlp_hdr),
        .rx_cpl_tlp_error                       (rx_cpl_tlp_error),
        .rx_cpl_tlp_valid                       (rx_cpl_tlp_valid),
        .rx_cpl_tlp_sop                         (rx_cpl_tlp_sop),
        .rx_cpl_tlp_eop                         (rx_cpl_tlp_eop),
        .rx_cpl_tlp_ready                       (rx_cpl_tlp_ready),

        // TLP output (read request)
        .tx_rd_req_tlp_hdr                      (tx_rd_req_tlp_hdr),
        .tx_rd_req_tlp_seq                      (tx_rd_req_tlp_seq),
        .tx_rd_req_tlp_valid                    (tx_rd_req_tlp_valid),
        .tx_rd_req_tlp_sop                      (tx_rd_req_tlp_sop),
        .tx_rd_req_tlp_eop                      (tx_rd_req_tlp_eop),
        .tx_rd_req_tlp_ready                    (tx_rd_req_tlp_ready),

        // TLP output (write request)
        .tx_wr_req_tlp_data                     (tx_wr_req_tlp_data),
        .tx_wr_req_tlp_strb                     (tx_wr_req_tlp_strb),
        .tx_wr_req_tlp_hdr                      (tx_wr_req_tlp_hdr),
        .tx_wr_req_tlp_seq                      (tx_wr_req_tlp_seq),
        .tx_wr_req_tlp_valid                    (tx_wr_req_tlp_valid),
        .tx_wr_req_tlp_sop                      (tx_wr_req_tlp_sop),
        .tx_wr_req_tlp_eop                      (tx_wr_req_tlp_eop),
        .tx_wr_req_tlp_ready                    (tx_wr_req_tlp_ready),

        // Transmit sequence number (tie to 0 — unused)
        .s_axis_rd_req_tx_seq_num               ({TX_SEQ_NUM_COUNT*TX_SEQ_NUM_WIDTH{1'b0}}),
        .s_axis_rd_req_tx_seq_num_valid         ({TX_SEQ_NUM_COUNT{1'b0}}),
        .s_axis_wr_req_tx_seq_num               ({TX_SEQ_NUM_COUNT*TX_SEQ_NUM_WIDTH{1'b0}}),
        .s_axis_wr_req_tx_seq_num_valid         ({TX_SEQ_NUM_COUNT{1'b0}}),

        // Read descriptor input (driven by FSM)
        .s_axis_read_desc_pcie_addr             (desc_pcie_addr),
        .s_axis_read_desc_ram_sel               ({RAM_SEL_WIDTH{1'b0}}),
        .s_axis_read_desc_ram_addr              ({RAM_ADDR_WIDTH{1'b0}}),
        .s_axis_read_desc_len                   (desc_len),
        .s_axis_read_desc_tag                   (desc_tag),
        .s_axis_read_desc_valid                 (pcie_read_desc_valid),
        .s_axis_read_desc_ready                 (pcie_read_desc_ready),

        // Read descriptor status
        .m_axis_read_desc_status_tag            (pcie_read_desc_status_tag),
        .m_axis_read_desc_status_error          (pcie_read_desc_status_error),
        .m_axis_read_desc_status_valid          (pcie_read_desc_status_valid),

        // Write descriptor input (driven by FSM)
        .s_axis_write_desc_pcie_addr            (desc_pcie_addr),
        .s_axis_write_desc_ram_sel              ({RAM_SEL_WIDTH{1'b0}}),
        .s_axis_write_desc_ram_addr             ({RAM_ADDR_WIDTH{1'b0}}),
        .s_axis_write_desc_imm                  ({IMM_WIDTH{1'b0}}),
        .s_axis_write_desc_imm_en               (1'b0),
        .s_axis_write_desc_len                  (desc_len),
        .s_axis_write_desc_tag                  (desc_tag),
        .s_axis_write_desc_valid                (pcie_write_desc_valid),
        .s_axis_write_desc_ready                (pcie_write_desc_ready),

        // Write descriptor status
        .m_axis_write_desc_status_tag           (pcie_write_desc_status_tag),
        .m_axis_write_desc_status_error         (pcie_write_desc_status_error),
        .m_axis_write_desc_status_valid         (pcie_write_desc_status_valid),

        // RAM interface → cross-connected to dma_if_axi via dma_psdpram
        .ram_rd_cmd_sel                         (),
        .ram_rd_cmd_addr                        (pcie_ram_rd_addr),
        .ram_rd_cmd_valid                       (pcie_ram_rd_valid),
        .ram_rd_cmd_ready                       (pcie_ram_rd_ready),
        .ram_rd_resp_data                       (pcie_ram_rd_data),
        .ram_rd_resp_valid                      (pcie_ram_rd_resp_valid),
        .ram_rd_resp_ready                      (pcie_ram_rd_resp_ready),
        .ram_wr_cmd_sel                         (),
        .ram_wr_cmd_be                          (pcie_ram_wr_be),
        .ram_wr_cmd_addr                        (pcie_ram_wr_addr),
        .ram_wr_cmd_data                        (pcie_ram_wr_data),
        .ram_wr_cmd_valid                       (pcie_ram_wr_valid),
        .ram_wr_cmd_ready                       (pcie_ram_wr_ready),
        .ram_wr_done                            (pcie_ram_wr_done),

        // Configuration
        .read_enable                            (1'b1),
        .write_enable                           (1'b1),
        .ext_tag_enable                         (1'b1),
        .rcb_128b                               (1'b0),
        .requester_id                           (16'h0001),
        .max_read_request_size                  (3'b010),
        .max_payload_size                       (3'b001),

        // Status
        .status_rd_busy                         (rd_busy),
        .status_wr_busy                         (wr_busy),
        .status_error_cor                       (),
        .status_error_uncor                     (),

        // Statistics (unused)
        .stat_rd_op_start_tag                   (),
        .stat_rd_op_start_len                   (),
        .stat_rd_op_start_valid                 (),
        .stat_rd_op_finish_tag                  (),
        .stat_rd_op_finish_status               (),
        .stat_rd_op_finish_valid                (),
        .stat_rd_req_start_tag                  (),
        .stat_rd_req_start_len                  (),
        .stat_rd_req_start_valid                (),
        .stat_rd_req_finish_tag                 (),
        .stat_rd_req_finish_status              (),
        .stat_rd_req_finish_valid               (),
        .stat_rd_req_timeout                    (),
        .stat_rd_op_table_full                  (),
        .stat_rd_no_tags                        (),
        .stat_rd_tx_limit                       (),
        .stat_rd_tx_stall                       (),
        .stat_wr_op_start_tag                   (),
        .stat_wr_op_start_len                   (),
        .stat_wr_op_start_valid                 (),
        .stat_wr_op_finish_tag                  (),
        .stat_wr_op_finish_status               (),
        .stat_wr_op_finish_valid                (),
        .stat_wr_req_start_tag                  (),
        .stat_wr_req_start_len                  (),
        .stat_wr_req_start_valid                (),
        .stat_wr_req_finish_tag                 (),
        .stat_wr_req_finish_status              (),
        .stat_wr_req_finish_valid               (),
        .stat_wr_op_table_full                  (),
        .stat_wr_tx_limit                       (),
        .stat_wr_tx_stall                       ()
    );

    //=========================================================================
    // SECTION 8: Instantiation — dma_if_axi
    //=========================================================================
    // Parameters per plan C3:
    //   AXI_DATA_WIDTH=512, AXI_ADDR_WIDTH=32, AXI_ID_WIDTH=6
    //   AXI_MAX_BURST_LEN=256, RAM_SEG_COUNT=2

    dma_if_axi #(
        .AXI_DATA_WIDTH      (AXI_DATA_WIDTH),
        .AXI_ADDR_WIDTH      (AXI_ADDR_WIDTH),
        .AXI_ID_WIDTH        (AXI_ID_WIDTH),
        .AXI_MAX_BURST_LEN   (AXI_MAX_BURST_LEN),
        .RAM_SEL_WIDTH       (RAM_SEL_WIDTH),
        .RAM_ADDR_WIDTH      (RAM_ADDR_WIDTH),
        .RAM_SEG_COUNT       (RAM_SEG_COUNT),
        .IMM_ENABLE          (IMM_ENABLE),
        .IMM_WIDTH           (IMM_WIDTH),
        .LEN_WIDTH           (LEN_WIDTH),
        .TAG_WIDTH           (TAG_WIDTH),
        .READ_OP_TABLE_SIZE  (2 ** AXI_ID_WIDTH),
        .WRITE_OP_TABLE_SIZE (2 ** AXI_ID_WIDTH),
        .READ_USE_AXI_ID     (1'b0),
        .WRITE_USE_AXI_ID    (1'b1)
    ) dma_if_axi_inst (
        .clk                                    (clk),
        .rst                                    (rst),

        // AXI4 Master
        .m_axi_awid                             (m_axi_awid),
        .m_axi_awaddr                           (m_axi_awaddr),
        .m_axi_awlen                            (m_axi_awlen),
        .m_axi_awsize                           (m_axi_awsize),
        .m_axi_awburst                          (m_axi_awburst),
        .m_axi_awlock                           (m_axi_awlock),
        .m_axi_awcache                          (m_axi_awcache),
        .m_axi_awprot                           (m_axi_awprot),
        .m_axi_awvalid                          (m_axi_awvalid),
        .m_axi_awready                          (m_axi_awready),
        .m_axi_wdata                            (m_axi_wdata),
        .m_axi_wstrb                            (m_axi_wstrb),
        .m_axi_wlast                            (m_axi_wlast),
        .m_axi_wvalid                           (m_axi_wvalid),
        .m_axi_wready                           (m_axi_wready),
        .m_axi_bid                              (m_axi_bid),
        .m_axi_bresp                            (m_axi_bresp),
        .m_axi_bvalid                           (m_axi_bvalid),
        .m_axi_bready                           (m_axi_bready),
        .m_axi_arid                             (m_axi_arid),
        .m_axi_araddr                           (m_axi_araddr),
        .m_axi_arlen                            (m_axi_arlen),
        .m_axi_arsize                           (m_axi_arsize),
        .m_axi_arburst                          (m_axi_arburst),
        .m_axi_arlock                           (m_axi_arlock),
        .m_axi_arcache                          (m_axi_arcache),
        .m_axi_arprot                           (m_axi_arprot),
        .m_axi_arvalid                          (m_axi_arvalid),
        .m_axi_arready                          (m_axi_arready),
        .m_axi_rid                              (m_axi_rid),
        .m_axi_rdata                            (m_axi_rdata),
        .m_axi_rresp                            (m_axi_rresp),
        .m_axi_rlast                            (m_axi_rlast),
        .m_axi_rvalid                           (m_axi_rvalid),
        .m_axi_rready                           (m_axi_rready),

        // Read descriptor input (driven by FSM)
        .s_axis_read_desc_axi_addr              (desc_axi_addr),
        .s_axis_read_desc_ram_sel               ({RAM_SEL_WIDTH{1'b0}}),
        .s_axis_read_desc_ram_addr              ({RAM_ADDR_WIDTH{1'b0}}),
        .s_axis_read_desc_len                   (desc_len),
        .s_axis_read_desc_tag                   (desc_tag),
        .s_axis_read_desc_valid                 (axi_read_desc_valid),
        .s_axis_read_desc_ready                 (axi_read_desc_ready),

        // Read descriptor status
        .m_axis_read_desc_status_tag            (axi_read_desc_status_tag),
        .m_axis_read_desc_status_error          (axi_read_desc_status_error),
        .m_axis_read_desc_status_valid          (axi_read_desc_status_valid),

        // Write descriptor input (driven by FSM)
        .s_axis_write_desc_axi_addr             (desc_axi_addr),
        .s_axis_write_desc_ram_sel              ({RAM_SEL_WIDTH{1'b0}}),
        .s_axis_write_desc_ram_addr             ({RAM_ADDR_WIDTH{1'b0}}),
        .s_axis_write_desc_imm                  ({IMM_WIDTH{1'b0}}),
        .s_axis_write_desc_imm_en               (1'b0),
        .s_axis_write_desc_len                  (desc_len),
        .s_axis_write_desc_tag                  (desc_tag),
        .s_axis_write_desc_valid                (axi_write_desc_valid),
        .s_axis_write_desc_ready                (axi_write_desc_ready),

        // Write descriptor status
        .m_axis_write_desc_status_tag           (axi_write_desc_status_tag),
        .m_axis_write_desc_status_error         (axi_write_desc_status_error),
        .m_axis_write_desc_status_valid         (axi_write_desc_status_valid),

        // RAM interface → cross-connected to dma_if_pcie via dma_psdpram
        .ram_wr_cmd_sel                         (),
        .ram_wr_cmd_be                          (axi_ram_wr_be),
        .ram_wr_cmd_addr                        (axi_ram_wr_addr),
        .ram_wr_cmd_data                        (axi_ram_wr_data),
        .ram_wr_cmd_valid                       (axi_ram_wr_valid),
        .ram_wr_cmd_ready                       (axi_ram_wr_ready),
        .ram_wr_done                            (axi_ram_wr_done),
        .ram_rd_cmd_sel                         (),
        .ram_rd_cmd_addr                        (axi_ram_rd_addr),
        .ram_rd_cmd_valid                       (axi_ram_rd_valid),
        .ram_rd_cmd_ready                       (axi_ram_rd_ready),
        .ram_rd_resp_data                       (axi_ram_rd_data),
        .ram_rd_resp_valid                      (axi_ram_rd_resp_valid),
        .ram_rd_resp_ready                      (axi_ram_rd_resp_ready),

        // Configuration
        .read_enable                            (1'b1),
        .write_enable                           (1'b1),

        // Status (unused — busy tracked by dma_if_pcie)
        .status_rd_busy                         (),
        .status_wr_busy                         (),

        // Statistics (unused)
        .stat_rd_op_start_tag                   (),
        .stat_rd_op_start_len                   (),
        .stat_rd_op_start_valid                 (),
        .stat_rd_op_finish_tag                  (),
        .stat_rd_op_finish_status               (),
        .stat_rd_op_finish_valid                (),
        .stat_rd_req_start_tag                  (),
        .stat_rd_req_start_len                  (),
        .stat_rd_req_start_valid                (),
        .stat_rd_req_finish_tag                 (),
        .stat_rd_req_finish_status              (),
        .stat_rd_req_finish_valid               (),
        .stat_rd_op_table_full                  (),
        .stat_rd_tx_stall                       (),
        .stat_wr_op_start_tag                   (),
        .stat_wr_op_start_len                   (),
        .stat_wr_op_start_valid                 (),
        .stat_wr_op_finish_tag                  (),
        .stat_wr_op_finish_status               (),
        .stat_wr_op_finish_valid                (),
        .stat_wr_req_start_tag                  (),
        .stat_wr_req_start_len                  (),
        .stat_wr_req_start_valid                (),
        .stat_wr_req_finish_tag                 (),
        .stat_wr_req_finish_status              (),
        .stat_wr_req_finish_valid               (),
        .stat_wr_op_table_full                  (),
        .stat_wr_tx_stall                       ()
    );

    //=========================================================================
    // SECTION 9: RAM Buffer — cross-connect dma_if_pcie ↔ dma_if_axi
    //=========================================================================
    //
    //   ram_pcie_to_axi: dma_if_pcie writes → dma_if_axi reads
    //     (PCIe CPLD data → RAM buffer → AXI write to NPU memory)
    //
    //   ram_axi_to_pcie: dma_if_axi writes → dma_if_pcie reads
    //     (AXI read from NPU memory → RAM buffer → PCIe MWr to host)

    dma_psdpram #(
        .SIZE            (RAM_BUF_SIZE),
        .SEG_COUNT       (RAM_SEG_COUNT),
        .SEG_DATA_WIDTH  (RAM_SEG_DATA_WIDTH),
        .PIPELINE        (2)
    ) ram_pcie_to_axi (
        .clk             (clk),
        .rst             (rst),
        .wr_cmd_be       (pcie_ram_wr_be),
        .wr_cmd_addr     (pcie_ram_wr_addr),
        .wr_cmd_data     (pcie_ram_wr_data),
        .wr_cmd_valid    (pcie_ram_wr_valid),
        .wr_cmd_ready    (pcie_ram_wr_ready),
        .wr_done         (pcie_ram_wr_done),
        .rd_cmd_addr     (axi_ram_rd_addr),
        .rd_cmd_valid    (axi_ram_rd_valid),
        .rd_cmd_ready    (axi_ram_rd_ready),
        .rd_resp_data    (axi_ram_rd_data),
        .rd_resp_valid   (axi_ram_rd_resp_valid),
        .rd_resp_ready   (axi_ram_rd_resp_ready)
    );

    dma_psdpram #(
        .SIZE            (RAM_BUF_SIZE),
        .SEG_COUNT       (RAM_SEG_COUNT),
        .SEG_DATA_WIDTH  (RAM_SEG_DATA_WIDTH),
        .PIPELINE        (2)
    ) ram_axi_to_pcie (
        .clk             (clk),
        .rst             (rst),
        .wr_cmd_be       (axi_ram_wr_be),
        .wr_cmd_addr     (axi_ram_wr_addr),
        .wr_cmd_data     (axi_ram_wr_data),
        .wr_cmd_valid    (axi_ram_wr_valid),
        .wr_cmd_ready    (axi_ram_wr_ready),
        .wr_done         (axi_ram_wr_done),
        .rd_cmd_addr     (pcie_ram_rd_addr),
        .rd_cmd_valid    (pcie_ram_rd_valid),
        .rd_cmd_ready    (pcie_ram_rd_ready),
        .rd_resp_data    (pcie_ram_rd_data),
        .rd_resp_valid   (pcie_ram_rd_resp_valid),
        .rd_resp_ready   (pcie_ram_rd_resp_ready)
    );

endmodule

`resetall
