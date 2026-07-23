//=============================================================================
// axi_sparse_slave — Behavioral AXI4 Slave with Uninitialized Memory
//=============================================================================
// Task: wrapper-level-verification / T1 scaffolding
//
// Parameterized 512-bit AXI4 slave with register-file memory (reg array).
// The memory is deliberately NOT zeroed with an initial block, so Verilog
// simulation naturally returns X for uninitialized addresses. This is used
// for BUG-005 X-propagation directed tests — cocotbext-axi AxiRam Python
// model returns 0 for sparse/unwritten regions, hiding X-propagation bugs.
//
// Supports: INCR burst on both read and write channels.
// Data width: 512-bit (parameterizable).  Address width: 32-bit.
// DEPTH parameter controls the number of 512-bit words (default 4096 = 2MB).
//
// No DPI, no initial $display, no tasks driving outputs in procedural blocks
// that violate AXI protocol timing. All outputs are driven in always blocks
// as required for synthesizable-style behavioral code.
//=============================================================================

`timescale 1ns / 1ps

module axi_sparse_slave #(
    parameter integer DEPTH           = 4096,   // 4096 × 512-bit = 256KB
    parameter integer ADDR_W          = 32,
    parameter integer DATA_W          = 512,
    parameter integer ID_W            = 8,
    localparam integer ADDR_IDX_W     = $clog2(DEPTH),
    localparam integer STRB_W         = DATA_W / 8
) (
    input  wire                 clk,
    input  wire                 rst_n,

    // ── AXI4 Write Address ──────────────────────────────────────────────────
    input  wire [ID_W-1:0]     s_axi_awid,
    input  wire [ADDR_W-1:0]   s_axi_awaddr,
    input  wire [7:0]          s_axi_awlen,
    input  wire [2:0]          s_axi_awsize,
    input  wire [1:0]          s_axi_awburst,
    input  wire                s_axi_awvalid,
    output reg                 s_axi_awready,

    // ── AXI4 Write Data ─────────────────────────────────────────────────────
    input  wire [DATA_W-1:0]   s_axi_wdata,
    input  wire [STRB_W-1:0]   s_axi_wstrb,
    input  wire                s_axi_wlast,
    input  wire                s_axi_wvalid,
    output reg                 s_axi_wready,

    // ── AXI4 Write Response ─────────────────────────────────────────────────
    output reg  [ID_W-1:0]     s_axi_bid,
    output reg  [1:0]          s_axi_bresp,
    output reg                 s_axi_bvalid,
    input  wire                s_axi_bready,

    // ── AXI4 Read Address ───────────────────────────────────────────────────
    input  wire [ID_W-1:0]     s_axi_arid,
    input  wire [ADDR_W-1:0]   s_axi_araddr,
    input  wire [7:0]          s_axi_arlen,
    input  wire [2:0]          s_axi_arsize,
    input  wire [1:0]          s_axi_arburst,
    input  wire                s_axi_arvalid,
    output reg                 s_axi_arready,

    // ── AXI4 Read Data ──────────────────────────────────────────────────────
    output reg  [ID_W-1:0]     s_axi_rid,
    output reg  [DATA_W-1:0]   s_axi_rdata,
    output reg  [1:0]          s_axi_rresp,
    output reg                 s_axi_rlast,
    output reg                 s_axi_rvalid,
    input  wire                s_axi_rready
);

    //=========================================================================
    // Memory array — NOT initialized (X propagation for structural tests)
    //=========================================================================
    // reg [DATA_W-1:0] mem [0:DEPTH-1];  — no initial block; defaults to X
    reg [DATA_W-1:0] mem [0:DEPTH-1];

    //=========================================================================
    // Write channel state
    //=========================================================================
    reg [7:0]                wr_beat_cnt;
    reg [ADDR_IDX_W-1:0]    wr_addr;
    reg [ID_W-1:0]          wr_id;
    reg                     wr_active;

    // Compute byte address from index (each word is DATA_W/8 bytes)
    wire [ADDR_W-1:0] aw_addr_aligned;
    assign aw_addr_aligned = {s_axi_awaddr[ADDR_W-1:ADDR_IDX_W], {ADDR_IDX_W{1'b0}}};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s_axi_awready <= 1'b0;
            s_axi_wready  <= 1'b0;
            wr_beat_cnt   <= 8'd0;
            wr_addr       <= 0;
            wr_id         <= 0;
            wr_active     <= 1'b0;
        end else begin
            // Accept write address once per burst
            if (s_axi_awvalid && s_axi_awready) begin
                s_axi_awready <= 1'b0;
                wr_active     <= 1'b1;
                wr_beat_cnt   <= 8'd0;
                wr_addr       <= s_axi_awaddr[ADDR_IDX_W-1:0];
                wr_id         <= s_axi_awid;
            end else if (!wr_active && s_axi_awvalid) begin
                s_axi_awready <= 1'b1;
            end

            // Accept write data beats
            s_axi_wready <= wr_active;

            if (wr_active && s_axi_wvalid && s_axi_wready) begin
                // Write data into memory, per-byte strobe
                if (wr_addr < DEPTH[ADDR_IDX_W-1:0]) begin
                    integer b;
                    for (b = 0; b < STRB_W; b = b + 1) begin
                        if (s_axi_wstrb[b])
                            mem[wr_addr][b*8 +: 8] <= s_axi_wdata[b*8 +: 8];
                    end
                end

                if (s_axi_wlast) begin
                    wr_active     <= 1'b0;
                    s_axi_awready <= 1'b1;  // ready for next burst
                end else begin
                    wr_addr <= wr_addr + 1'b1;
                end
                wr_beat_cnt <= wr_beat_cnt + 8'd1;
            end
        end
    end

    //=========================================================================
    // Write response channel
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s_axi_bvalid <= 1'b0;
            s_axi_bid    <= 0;
            s_axi_bresp  <= 2'b00;
        end else begin
            // Issue B response after last beat accepted
            if (wr_active && s_axi_wvalid && s_axi_wready && s_axi_wlast) begin
                s_axi_bvalid <= 1'b1;
                s_axi_bid    <= wr_id;
                s_axi_bresp  <= 2'b00;  // OKAY
            end else if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end
        end
    end

    //=========================================================================
    // Read channel state
    //=========================================================================
    reg [7:0]                rd_beat_cnt;
    reg [7:0]                rd_beat_total;
    reg [ADDR_IDX_W-1:0]    rd_addr;
    reg [ID_W-1:0]          rd_id;
    reg                     rd_active;

    wire rd_addr_in_range;
    assign rd_addr_in_range = (rd_addr < DEPTH[ADDR_IDX_W-1:0]);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s_axi_arready   <= 1'b0;
            s_axi_rvalid    <= 1'b0;
            s_axi_rid       <= 0;
            s_axi_rdata     <= 0;
            s_axi_rresp     <= 2'b00;
            s_axi_rlast     <= 1'b0;
            rd_beat_cnt     <= 8'd0;
            rd_beat_total   <= 8'd0;
            rd_addr         <= 0;
            rd_id           <= 0;
            rd_active       <= 1'b0;
        end else begin
            // Accept read address
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_arready <= 1'b0;
                rd_active     <= 1'b1;
                rd_beat_cnt   <= 8'd0;
                rd_beat_total <= s_axi_arlen;
                rd_addr       <= s_axi_araddr[ADDR_IDX_W-1:0];
                rd_id         <= s_axi_arid;
            end else if (!rd_active && s_axi_arvalid) begin
                s_axi_arready <= 1'b1;
            end

            // Issue read data beats (1 beat after address acceptance)
            if (rd_active) begin
                if (s_axi_rvalid && s_axi_rready) begin
                    // Previous beat accepted, advance or finish
                    if (s_axi_rlast || rd_beat_total == 8'd0) begin
                        rd_active     <= 1'b0;
                        s_axi_rvalid  <= 1'b0;
                        s_axi_arready <= 1'b1;  // ready for next burst
                    end else begin
                        rd_addr     <= rd_addr + 1'b1;
                        rd_beat_cnt <= rd_beat_cnt + 8'd1;
                        // Present next beat
                        s_axi_rlast <= (rd_beat_cnt == rd_beat_total);
                        if (rd_addr_in_range)
                            s_axi_rdata <= mem[rd_addr + 1'b1];
                        else
                            s_axi_rdata <= {DATA_W{1'b0}};
                    end
                end else if (!s_axi_rvalid) begin
                    // Present first beat
                    s_axi_rvalid <= 1'b1;
                    s_axi_rid    <= rd_id;
                    s_axi_rresp  <= 2'b00;
                    s_axi_rlast  <= (rd_beat_total == 8'd0);
                    if (rd_addr_in_range)
                        s_axi_rdata <= mem[rd_addr];
                    else
                        s_axi_rdata <= {DATA_W{1'b0}};
                end
            end
        end
    end

endmodule
