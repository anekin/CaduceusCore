//=============================================================================
// dma_wrapper — axi_cdma DMA Engine Integration Wrapper
//=============================================================================
// Task 11 of soc-phase3-4 Wave 3.
//
// Wraps axi_cdma (alexforencich/verilog-axi, MIT) with:
//   • APB slave (0x4000_3000) — backward-compatible firmware register map
//   • AXI4 master (512-bit, 32-bit addr) → crossbar → SRAM/DRAM
//   • Descriptor translation: firmware CH0_SRC/CH0_DST/CH0_SIZE →
//     axi_cdma descriptor (SRC_ADDR/DST_ADDR/LEN)
//   • Interrupt: transfer done → dma_irq → INTC
//
// Backward Compatible Register Map (matches npu-regmap.h npu_dma_t):
//   0x00: CTRL        [0]=linked_list_en, [1:2]=channel_mode
//   0x04: CMD         bit[0]=START, bit[1]=ABORT
//   0x08: STATUS      bit[0]=BUSY, bit[1]=DONE, [7:4]=active_channel
//   0x0C: _pad0
//   0x10: CH0_SRC     DRAM src addr
//   0x14: CH0_DST     SRAM dst addr
//   0x18: CH0_SIZE    transfer bytes
//   0x1C: CH0_STRIDE  2D stride (reserved)
//   0x20: CH1_SRC     SRAM src addr
//   0x24: CH1_DST     DRAM dst addr
//   0x28: CH1_SIZE    transfer bytes
//   0x2C: CH1_STRIDE  2D stride (reserved)
//   0x30: DESC_ADDR   descriptor chain base (reserved for linked-list)
//   0x34: DESC_CNT    descriptor count (reserved)
//   0x38: IRQ_EN      bit[0]=irq enable
//
// Must NOT modify firmware dispatch logic.
// Leverages verilog-axi built-in cocotb testbench (cocotbext-axi).
//=============================================================================

`timescale 1ns / 1ps
`default_nettype none

module dma_wrapper #(
    parameter integer AXI_DATA_WIDTH     = 512,
    parameter integer AXI_ADDR_WIDTH     = 32,
    parameter integer AXI_STRB_WIDTH     = AXI_DATA_WIDTH / 8,
    parameter integer AXI_ID_WIDTH       = 8,
    parameter integer AXI_MAX_BURST_LEN  = 16,
    parameter integer LEN_WIDTH          = 20,
    parameter integer TAG_WIDTH          = 8
) (
    input  wire        clk,
    input  wire        rst_n,

    // ── APB slave (from apb_decoder, slave3 at 0x4000_3000) ──────────────
    input  wire        psel,
    input  wire        penable,
    input  wire        pwrite,
    input  wire [11:0] paddr,
    input  wire [31:0] pwdata,
    output wire [31:0] prdata,
    output wire        pready,
    output wire        pslverr,

    // ── AXI4 master → crossbar ──────────────────────────────────────────
    // Write address channel
    output wire [AXI_ID_WIDTH-1:0]   m_axi_awid,
    output wire [AXI_ADDR_WIDTH-1:0] m_axi_awaddr,
    output wire [7:0]                m_axi_awlen,
    output wire [2:0]                m_axi_awsize,
    output wire [1:0]                m_axi_awburst,
    output wire                      m_axi_awvalid,
    input  wire                      m_axi_awready,
    // Write data channel
    output wire [AXI_DATA_WIDTH-1:0] m_axi_wdata,
    output wire [AXI_STRB_WIDTH-1:0] m_axi_wstrb,
    output wire                      m_axi_wlast,
    output wire                      m_axi_wvalid,
    input  wire                      m_axi_wready,
    // Write response channel
    input  wire [AXI_ID_WIDTH-1:0]   m_axi_bid,
    input  wire [1:0]                m_axi_bresp,
    input  wire                      m_axi_bvalid,
    output wire                      m_axi_bready,
    // Read address channel
    output wire [AXI_ID_WIDTH-1:0]   m_axi_arid,
    output wire [AXI_ADDR_WIDTH-1:0] m_axi_araddr,
    output wire [7:0]                m_axi_arlen,
    output wire [2:0]                m_axi_arsize,
    output wire [1:0]                m_axi_arburst,
    output wire                      m_axi_arvalid,
    input  wire                      m_axi_arready,
    // Read data channel
    input  wire [AXI_ID_WIDTH-1:0]   m_axi_rid,
    input  wire [AXI_DATA_WIDTH-1:0] m_axi_rdata,
    input  wire [1:0]                m_axi_rresp,
    input  wire                      m_axi_rlast,
    input  wire                      m_axi_rvalid,
    output wire                      m_axi_rready,

    // ── Interrupt (to INTC) ──────────────────────────────────────────────
    output wire        dma_irq
);

    //=========================================================================
    // Reset polarity — axi_cdma uses active-high rst, wrapper uses rst_n
    //=========================================================================
    wire cdma_rst = ~rst_n;

    //=========================================================================
    // Register file — 16 × 32-bit registers matching npu_dma_t layout
    //=========================================================================
    // Register index: paddr[5:2] (word-aligned, covers offsets 0x00–0x3C)
    wire [3:0] reg_idx = paddr[5:2];

    // Register write: APB write in access phase (psel=1, penable=1, pwrite=1)
    wire apb_write = psel && penable && pwrite;
    wire apb_read  = psel && !pwrite;

    // Register storage — 15 registers max (0x00–0x38 → indices 0–14)
    reg [31:0] dma_reg [0:14];

    // Write strobes
    wire reg_we = apb_write && (reg_idx <= 4'd14);

    // Special handling: CMD.START is write-only, auto-cleared; STATUS is read-only with side effects
    // CTRL (index 0) — read/write (writable fields only)
    // CMD  (index 1) — write-only (auto-clear after one cycle)
    // STATUS (index 2) — read-only (managed by FSM)
    // _pad0 (index 3) — read/write (unused)

    // Read data mux
    wire [31:0] reg_rdata;
    assign reg_rdata = (reg_idx <= 4'd14) ? dma_reg[reg_idx] : 32'h0;

    //=========================================================================
    // Merged register-write + descriptor FSM
    //=========================================================================
    // Single always block eliminates multi-driver race hazard on dma_reg[1]
    // (CMD) and dma_reg[2] (STATUS). Priority (highest → lowest):
    //   1. APB register write — overrides FSM for CMD
    //   2. FSM state update — manages CMD.START clear, STATUS BUSY/DONE
    //   3. STATUS read-clear — clears DONE on STATUS read
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            // Reset all registers
            dma_reg[0]  <= 32'h0;  // CTRL
            dma_reg[1]  <= 32'h0;  // CMD
            dma_reg[2]  <= 32'h0;  // STATUS
            dma_reg[3]  <= 32'h0;  // _pad0
            dma_reg[4]  <= 32'h0;  // CH0_SRC
            dma_reg[5]  <= 32'h0;  // CH0_DST
            dma_reg[6]  <= 32'h0;  // CH0_SIZE
            dma_reg[7]  <= 32'h0;  // CH0_STRIDE
            dma_reg[8]  <= 32'h0;  // CH1_SRC
            dma_reg[9]  <= 32'h0;  // CH1_DST
            dma_reg[10] <= 32'h0;  // CH1_SIZE
            dma_reg[11] <= 32'h0;  // CH1_STRIDE
            dma_reg[12] <= 32'h0;  // DESC_ADDR
            dma_reg[13] <= 32'h0;  // DESC_CNT
            dma_reg[14] <= 32'h0;  // IRQ_EN

            // Reset FSM state and descriptor latches
            fsm_state     <= FSM_IDLE;
            ch0_src_latch <= {AXI_ADDR_WIDTH{1'b0}};
            ch0_dst_latch <= {AXI_ADDR_WIDTH{1'b0}};
            ch0_len_latch <= {LEN_WIDTH{1'b0}};
            ch1_src_latch <= {AXI_ADDR_WIDTH{1'b0}};
            ch1_dst_latch <= {AXI_ADDR_WIDTH{1'b0}};
            ch1_len_latch <= {LEN_WIDTH{1'b0}};
            ch0_valid     <= 1'b0;
            ch1_valid     <= 1'b0;
        end else begin
            // ── 1. FSM state update (default: retain via case default clause) ──
            case (fsm_state)
                FSM_IDLE: begin
                    if (cmd_start_rise) begin
                        // Clear CMD.START flag (consumed)
                        dma_reg[1][0] <= 1'b0;

                        ch0_src_latch <= dma_reg[4];   // CH0_SRC
                        ch0_dst_latch <= dma_reg[5];   // CH0_DST
                        ch0_len_latch <= dma_reg[6][LEN_WIDTH-1:0];  // CH0_SIZE
                        ch0_valid     <= (dma_reg[6] != 32'h0);       // CH0_SIZE > 0

                        ch1_src_latch <= dma_reg[8];   // CH1_SRC
                        ch1_dst_latch <= dma_reg[9];   // CH1_DST
                        ch1_len_latch <= dma_reg[10][LEN_WIDTH-1:0]; // CH1_SIZE
                        ch1_valid     <= (dma_reg[10] != 32'h0);      // CH1_SIZE > 0

                        // Set BUSY, clear DONE
                        dma_reg[2][0] <= 1'b1;    // BUSY
                        dma_reg[2][1] <= 1'b0;    // DONE

                        if (dma_reg[6] != 32'h0) begin
                            dma_reg[2][7:4] <= 4'd0;  // active_channel = 0
                            fsm_state <= FSM_DESC_CH0;
                        end else if (dma_reg[10] != 32'h0) begin
                            dma_reg[2][7:4] <= 4'd1;  // active_channel = 1
                            fsm_state <= FSM_DESC_CH1;
                        end else begin
                            // No valid transfer — go straight to DONE
                            dma_reg[2][0] <= 1'b0;   // BUSY=0
                            dma_reg[2][1] <= 1'b1;   // DONE=1
                            fsm_state <= FSM_DONE_PULSE;
                        end
                    end
                end

                FSM_DESC_CH0: begin
                    if (cdma_desc_valid && cdma_desc_ready) begin
                        fsm_state <= FSM_WAIT_CH0;
                    end
                end

                FSM_WAIT_CH0: begin
                    if (cdma_status_valid && cdma_status_tag == {TAG_WIDTH{1'b0}}) begin
                        if (ch1_valid) begin
                            dma_reg[2][7:4] <= 4'd1;  // active_channel = 1
                            fsm_state <= FSM_DESC_CH1;
                        end else begin
                            dma_reg[2][0] <= 1'b0;    // BUSY=0
                            dma_reg[2][1] <= 1'b1;    // DONE=1
                            fsm_state <= FSM_DONE_PULSE;
                        end
                    end
                end

                FSM_DESC_CH1: begin
                    if (cdma_desc_valid && cdma_desc_ready) begin
                        fsm_state <= FSM_WAIT_CH1;
                    end
                end

                FSM_WAIT_CH1: begin
                    if (cdma_status_valid && cdma_status_tag[0] == 1'b1) begin
                        dma_reg[2][0] <= 1'b0;    // BUSY=0
                        dma_reg[2][1] <= 1'b1;    // DONE=1
                        fsm_state <= FSM_DONE_PULSE;
                    end
                end

                FSM_DONE_PULSE: begin
                    fsm_state <= FSM_IDLE;
                end

                default: fsm_state <= FSM_IDLE;
            endcase

            // ── 2. APB register writes (higher priority than FSM above) ──
            if (reg_we) begin
                case (reg_idx)
                    4'd1: begin
                        // CMD register: overrides FSM's CMD.START clear in same cycle
                        dma_reg[1] <= pwdata;
                    end
                    4'd2: begin
                        // STATUS is read-only (writes ignored by hardware)
                    end
                    default: begin
                        if (reg_idx != 4'd2) begin
                            dma_reg[reg_idx] <= pwdata;
                        end
                    end
                endcase
            end

            // ── 3. STATUS read-clear (overrides FSM's DONE=1 this cycle) ──
            if (apb_read && reg_idx == 4'd2) begin
                dma_reg[2][1] <= 1'b0;  // clear DONE on read
            end
        end
    end

    //=========================================================================
    // APB response — zero wait state
    //=========================================================================
    assign pready  = 1'b1;
    assign pslverr = 1'b0;
    assign prdata  = apb_read ? reg_rdata : 32'h0;

    //=========================================================================
    // Descriptor launch FSM
    //=========================================================================
    // Detects CMD.START (bit0) rising edge and translates firmware channel
    // registers into axi_cdma streaming descriptor interface.
    //
    // Flow:
    //   IDLE → (CH0_SIZE > 0?) DESCRIPTOR_CH0 → WAIT_CH0 →
    //   (CH1_SIZE > 0?) DESCRIPTOR_CH1 → WAIT_CH1 → DONE → IDLE
    //
    // Each descriptor is submitted via the streaming interface:
    //   s_axis_desc_valid=1 → wait for s_axis_desc_ready=1 → descriptor accepted

    localparam [2:0]
        FSM_IDLE         = 3'd0,
        FSM_DESC_CH0     = 3'd1,  // submit CH0 descriptor to axi_cdma
        FSM_WAIT_CH0     = 3'd2,  // wait for CH0 transfer to complete
        FSM_DESC_CH1     = 3'd3,  // submit CH1 descriptor
        FSM_WAIT_CH1     = 3'd4,  // wait for CH1 transfer to complete
        FSM_DONE_PULSE   = 3'd5;  // one-cycle DONE pulse, then back to IDLE

    reg [2:0] fsm_state, fsm_next;

    // CMD.START edge detection
    reg cmd_start_d;  // delayed, for edge detection
    wire cmd_start_rise;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cmd_start_d <= 1'b0;
        else
            cmd_start_d <= dma_reg[1][0];  // CMD bit[0] = START
    end
    assign cmd_start_rise = dma_reg[1][0] && !cmd_start_d;

    // Descriptor data — latched at START edge
    reg [AXI_ADDR_WIDTH-1:0] ch0_src_latch, ch0_dst_latch;
    reg [LEN_WIDTH-1:0]      ch0_len_latch;
    reg [AXI_ADDR_WIDTH-1:0] ch1_src_latch, ch1_dst_latch;
    reg [LEN_WIDTH-1:0]      ch1_len_latch;
    reg                       ch0_valid, ch1_valid;

    // ── axi_cdma descriptor interface signals ──
    wire [AXI_ADDR_WIDTH-1:0] cdma_desc_read_addr;
    wire [AXI_ADDR_WIDTH-1:0] cdma_desc_write_addr;
    wire [LEN_WIDTH-1:0]      cdma_desc_len;
    wire [TAG_WIDTH-1:0]      cdma_desc_tag;
    wire                      cdma_desc_valid;
    wire                      cdma_desc_ready;

    // ── axi_cdma status interface signals ──
    wire [TAG_WIDTH-1:0]      cdma_status_tag;
    wire [3:0]                cdma_status_error;
    wire                      cdma_status_valid;

    assign cdma_desc_read_addr  = (fsm_state == FSM_DESC_CH0) ? ch0_src_latch : ch1_src_latch;
    assign cdma_desc_write_addr = (fsm_state == FSM_DESC_CH0) ? ch0_dst_latch : ch1_dst_latch;
    assign cdma_desc_len        = (fsm_state == FSM_DESC_CH0) ? ch0_len_latch : ch1_len_latch;
    assign cdma_desc_tag        = (fsm_state == FSM_DESC_CH0) ? {TAG_WIDTH{1'b0}} : {{TAG_WIDTH-1{1'b0}}, 1'b1};
    assign cdma_desc_valid      = (fsm_state == FSM_DESC_CH0) || (fsm_state == FSM_DESC_CH1);

    // (merged into register-write block above — see dma_reg[1]/dma_reg[2] updates)

    //=========================================================================
    // Interrupt generation
    //=========================================================================
    // dma_irq = DONE && IRQ_EN[0]
    // IRQ is one-cycle pulse during FSM_DONE_PULSE (combinational)
    wire irq_fire = ((fsm_state == FSM_DONE_PULSE) && dma_reg[14][0]);  // IRQ_EN bit0

    assign dma_irq = irq_fire;

    //=========================================================================
    // axi_cdma instance
    //=========================================================================
    // alexforencich/verilog-axi Central DMA engine
    // Config: AXI4 512-bit data, 32-bit addr, max burst=16, LEN=20-bit, TAG=8-bit
    axi_cdma #(
        .AXI_DATA_WIDTH    (AXI_DATA_WIDTH),
        .AXI_ADDR_WIDTH    (AXI_ADDR_WIDTH),
        .AXI_STRB_WIDTH    (AXI_STRB_WIDTH),
        .AXI_ID_WIDTH      (AXI_ID_WIDTH),
        .AXI_MAX_BURST_LEN (AXI_MAX_BURST_LEN),
        .LEN_WIDTH         (LEN_WIDTH),
        .TAG_WIDTH         (TAG_WIDTH),
        .ENABLE_UNALIGNED  (0)
    ) u_axi_cdma (
        .clk                        (clk),
        .rst                        (cdma_rst),

        // Descriptor input (from wrapper FSM)
        .s_axis_desc_read_addr      (cdma_desc_read_addr),
        .s_axis_desc_write_addr     (cdma_desc_write_addr),
        .s_axis_desc_len            (cdma_desc_len),
        .s_axis_desc_tag            (cdma_desc_tag),
        .s_axis_desc_valid          (cdma_desc_valid),
        .s_axis_desc_ready          (cdma_desc_ready),

        // Descriptor status output (to wrapper FSM)
        .m_axis_desc_status_tag     (cdma_status_tag),
        .m_axis_desc_status_error   (cdma_status_error),
        .m_axis_desc_status_valid   (cdma_status_valid),

        // AXI4 write master
        .m_axi_awid                 (m_axi_awid),
        .m_axi_awaddr               (m_axi_awaddr),
        .m_axi_awlen                (m_axi_awlen),
        .m_axi_awsize               (m_axi_awsize),
        .m_axi_awburst              (m_axi_awburst),
        .m_axi_awlock               (),
        .m_axi_awcache              (),
        .m_axi_awprot               (),
        .m_axi_awvalid              (m_axi_awvalid),
        .m_axi_awready              (m_axi_awready),
        .m_axi_wdata                (m_axi_wdata),
        .m_axi_wstrb                (m_axi_wstrb),
        .m_axi_wlast                (m_axi_wlast),
        .m_axi_wvalid               (m_axi_wvalid),
        .m_axi_wready               (m_axi_wready),
        .m_axi_bid                  (m_axi_bid),
        .m_axi_bresp                (m_axi_bresp),
        .m_axi_bvalid               (m_axi_bvalid),
        .m_axi_bready               (m_axi_bready),

        // AXI4 read master
        .m_axi_arid                 (m_axi_arid),
        .m_axi_araddr               (m_axi_araddr),
        .m_axi_arlen                (m_axi_arlen),
        .m_axi_arsize               (m_axi_arsize),
        .m_axi_arburst              (m_axi_arburst),
        .m_axi_arlock               (),
        .m_axi_arcache              (),
        .m_axi_arprot               (),
        .m_axi_arvalid              (m_axi_arvalid),
        .m_axi_arready              (m_axi_arready),
        .m_axi_rid                  (m_axi_rid),
        .m_axi_rdata                (m_axi_rdata),
        .m_axi_rresp                (m_axi_rresp),
        .m_axi_rlast                (m_axi_rlast),
        .m_axi_rvalid               (m_axi_rvalid),
        .m_axi_rready               (m_axi_rready),

        // Configuration
        .enable                     (1'b1)   // always enabled
    );

endmodule

`resetall
