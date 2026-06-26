`timescale 1ns / 1ps
//=============================================================================
// vector_top — Vector Unit Top-Level Integration
//=============================================================================
// Task 13 of sfu-vector-phase2 Wave 3.
//
// Pure integration / dispatch wrapper for the four Vector submodules:
//   vector_alu  (128-wide INT32 add/mul/max/pass_a, 1-cycle latency)
//   reduce_tree (128->1 max/sum reduction, 7-cycle latency)
//   type_convert (INT32 -> IEEE-754 FP16, 1-cycle latency)
//   resid_add   (128-wide INT32 residual add, 1-cycle latency)
//
// Provides a 4KB MMIO slave matching CaduceusCore/sim/regmap.py VECTOR class:
//   BASE = 0x4000_2000
//   CTRL[3:0]  = OP (0=ADD,1=MUL,2=MAX,3=SUM,4=CONV,5=RESID)
//   CMD[0]     = START  (write-only pulse)
//   STATUS[0]  = BUSY, STATUS[1] = DONE
//   A_ADDR/B_ADDR/O_ADDR = byte addresses in external SRAM
//   DIM[15:0]  = element count
//   IRQ_EN[0]  = completion interrupt enable
//
// External SRAM is assumed to be wide enough to supply a 128-element chunk
// per read cycle (4096 bits for INT32 lanes).  The write port carries the
// same 4096-bit width plus a per-byte write-strobe.
//
// Processing model:
//   - Binary vector ops (ADD/MUL/RESID): read A/B chunks, feed submodule,
//     capture 128-wide INT32 result after 1 cycle, write back to SRAM.
//   - Reduction ops (MAX/SUM): read A chunks, feed reduce_tree with a
//     lane_mask that disables invalid lanes in the final partial chunk,
//     accumulate chunk scalar results, produce final INT32 scalar.
//   - CONV: read INT32 chunks, stream elements one-per-cycle through
//     type_convert, collect FP16 results, write packed FP16 chunk.
//
// No arithmetic computation is performed in this file beyond address
// incrementing, chunk counting, and final SUM saturation.
//=============================================================================

module vector_top #(
    parameter integer NUM_LANES = 128,
    parameter integer DATA_W    = 32,
    parameter integer VECTOR_W  = NUM_LANES * DATA_W,   // 4096
    parameter integer FP16_W    = 16,
    parameter integer ADDR_W    = 32
) (
    input  wire                  clk,
    input  wire                  rst_n,

    // ── MMIO slave interface ──────────────────────────────────────────
    input  wire                  mmio_cs,
    input  wire                  mmio_we,
    input  wire [11:0]           mmio_addr,
    input  wire [31:0]           mmio_wdata,
    output reg  [31:0]           mmio_rdata,
    output wire                  mmio_ready,

    // ── SRAM read port A ──────────────────────────────────────────────
    output reg  [ADDR_W-1:0]     sram_a_addr,
    output reg                   sram_a_en,
    input  wire [VECTOR_W-1:0]   sram_a_rdata,

    // ── SRAM read port B ──────────────────────────────────────────────
    output reg  [ADDR_W-1:0]     sram_b_addr,
    output reg                   sram_b_en,
    input  wire [VECTOR_W-1:0]   sram_b_rdata,

    // ── SRAM write port O ─────────────────────────────────────────────
    output reg  [ADDR_W-1:0]     sram_o_addr,
    output reg  [VECTOR_W-1:0]   sram_o_wdata,
    output reg                   sram_o_wen,
    output reg  [511:0]          sram_o_wstrb,   // one bit per byte

    // ── Interrupt ─────────────────────────────────────────────────────
    output wire                  irq
);

    //=========================================================================
    // Local parameters / constants
    //=========================================================================
    localparam [3:0] OP_ADD   = 4'd0;
    localparam [3:0] OP_MUL   = 4'd1;
    localparam [3:0] OP_MAX   = 4'd2;
    localparam [3:0] OP_SUM   = 4'd3;
    localparam [3:0] OP_CONV  = 4'd4;
    localparam [3:0] OP_RESID = 4'd5;

    localparam integer CHUNK_BYTES_INT32 = NUM_LANES * 4;  // 512
    localparam integer CHUNK_BYTES_FP16  = NUM_LANES * 2;  // 256

    localparam signed [63:0] INT32_MAX_64 = 64'sh000000007FFFFFFF;
    localparam signed [63:0] INT32_MIN_64 = 64'shFFFFFFFF80000000;

    //=========================================================================
    // MMIO register file
    //=========================================================================
    reg [31:0] ctrl_reg;
    reg [31:0] a_addr_reg;
    reg [31:0] b_addr_reg;
    reg [31:0] o_addr_reg;
    reg [31:0] dim_reg;
    reg [31:0] irq_en_reg;

    reg        status_busy;
    reg        status_done;

    // CMD pulse generation
    reg        cmd_start_r;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cmd_start_r <= 1'b0;
        else
            cmd_start_r <= mmio_cs && mmio_we && (mmio_addr == 12'h004) && mmio_wdata[0];
    end

    // Synchronous writes
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ctrl_reg   <= 32'd0;
            a_addr_reg <= 32'd0;
            b_addr_reg <= 32'd0;
            o_addr_reg <= 32'd0;
            dim_reg    <= 32'd0;
            irq_en_reg <= 32'd0;
        end else if (mmio_cs && mmio_we) begin
            case (mmio_addr)
                12'h000: ctrl_reg   <= mmio_wdata;
                12'h00C: a_addr_reg <= mmio_wdata;
                12'h010: b_addr_reg <= mmio_wdata;
                12'h014: o_addr_reg <= mmio_wdata;
                12'h018: dim_reg    <= mmio_wdata;
                12'h01C: irq_en_reg <= mmio_wdata;
                default: ;
            endcase
        end
    end

    // Combinatorial reads
    always @(*) begin
        mmio_rdata = 32'd0;
        if (mmio_cs && !mmio_we) begin
            case (mmio_addr)
                12'h000: mmio_rdata = ctrl_reg;
                12'h004: mmio_rdata = 32'd0;                       // CMD write-only
                12'h008: mmio_rdata = {30'd0, status_done, status_busy};
                12'h00C: mmio_rdata = a_addr_reg;
                12'h010: mmio_rdata = b_addr_reg;
                12'h014: mmio_rdata = o_addr_reg;
                12'h018: mmio_rdata = dim_reg;
                12'h01C: mmio_rdata = irq_en_reg;
                default: mmio_rdata = 32'd0;
            endcase
        end
    end

    assign mmio_ready = mmio_cs;
    assign irq        = status_done && irq_en_reg[0];

    //=========================================================================
    // Controller FSM
    //=========================================================================
    localparam [3:0] ST_IDLE          = 4'd0;
    localparam [3:0] ST_READ          = 4'd1;
    localparam [3:0] ST_BIN_EXEC      = 4'd2;
    localparam [3:0] ST_BIN_WRITE     = 4'd3;
    localparam [3:0] ST_REDUCE_FEED   = 4'd4;
    localparam [3:0] ST_REDUCE_WAIT   = 4'd5;
    localparam [3:0] ST_REDUCE_ACC    = 4'd6;
    localparam [3:0] ST_REDUCE_WRITE  = 4'd7;
    localparam [3:0] ST_CONV_FEED     = 4'd8;
    localparam [3:0] ST_CONV_CAPTURE  = 4'd9;
    localparam [3:0] ST_CONV_WRITE    = 4'd10;
    localparam [3:0] ST_DONE          = 4'd11;
    localparam [3:0] ST_LATCH         = 4'd12;

    reg [3:0]  state;
    reg [3:0]  op_reg;
    reg [15:0] remaining;
    reg [ADDR_W-1:0] a_addr;
    reg [ADDR_W-1:0] b_addr;
    reg [ADDR_W-1:0] o_addr;
    reg [VECTOR_W-1:0] a_chunk;
    reg [VECTOR_W-1:0] b_chunk;
    reg [7:0]  chunk_count;
    reg [127:0] lane_mask;

    reg        reduce_running_max;
    reg signed [63:0] reduce_sum_acc;
    reg [31:0] reduce_max_acc;
    reg [3:0]  reduce_wait_cnt;

    reg [7:0]  conv_idx;
    reg [NUM_LANES*FP16_W-1:0] conv_out_vector;
    reg [DATA_W-1:0] conv_in_data;

    //=========================================================================
    // Helper: mask for a partial chunk
    //=========================================================================
    function [NUM_LANES-1:0] mask_for_count;
        input [7:0] cnt;
        begin
            if (cnt >= NUM_LANES)
                mask_for_count = {NUM_LANES{1'b1}};
            else if (cnt == 0)
                mask_for_count = {NUM_LANES{1'b0}};
            else
                mask_for_count = (({NUM_LANES{1'b0}} + 1'b1) << cnt) - 1'b1;
        end
    endfunction

    //=========================================================================
    // Submodule instances
    //=========================================================================
    wire [VECTOR_W-1:0] alu_result;

    vector_alu #(
        .NUM_LANES(NUM_LANES)
    ) u_vector_alu (
        .clk       (clk),
        .rst_n     (rst_n),
        .op        ( (op_reg == OP_MUL) ? 2'b01 : 2'b00 ),  // ADD/MUL only
        .a_i       (a_chunk),
        .b_i       (b_chunk),
        .lane_mask (lane_mask),
        .valid_i   ( (state == ST_BIN_EXEC) &&
                     ((op_reg == OP_ADD) || (op_reg == OP_MUL)) ),
        .result_o  (alu_result)
    );

    wire [VECTOR_W-1:0] resid_result;

    resid_add #(
        .NUM_LANES(NUM_LANES)
    ) u_resid_add (
        .clk      (clk),
        .rst_n    (rst_n),
        .orig_i   (a_chunk),
        .delta_i  (b_chunk),
        .valid_i  ( (state == ST_BIN_EXEC) && (op_reg == OP_RESID) ),
        .result_o (resid_result)
    );

    wire [DATA_W-1:0] reduce_result;
    wire signed [63:0] reduce_result64;
    wire              reduce_valid;

    reduce_tree #(
        .NUM_IN(NUM_LANES),
        .DATA_W(DATA_W)
    ) u_reduce_tree (
        .clk       (clk),
        .rst_n     (rst_n),
        .data_i    (a_chunk),
        .op        ( (op_reg == OP_SUM) ? 1'b1 : 1'b0 ),  // 0=MAX,1=SUM
        .valid_i   ( (state == ST_REDUCE_FEED) ),
        .lane_mask (lane_mask),
        .result_o  (reduce_result),
        .result64_o(reduce_result64),
        .valid_o   (reduce_valid)
    );

    wire [15:0] tc_data_o;
    wire        tc_valid_o;

    type_convert u_type_convert (
        .clk     (clk),
        .rst_n   (rst_n),
        .data_i  (conv_in_data),
        .valid_i ( (state == ST_CONV_FEED) ),
        .data_o  (tc_data_o),
        .valid_o (tc_valid_o)
    );

    // Combinatorial slice into the current conv input element
    always @(*) begin
        conv_in_data = a_chunk[conv_idx*DATA_W +: DATA_W];
    end

    //=========================================================================
    // FSM sequential + datapath
    //=========================================================================
    integer i;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state            <= ST_IDLE;
            status_busy      <= 1'b0;
            status_done      <= 1'b0;
            op_reg           <= 4'd0;
            remaining        <= 16'd0;
            a_addr           <= {ADDR_W{1'b0}};
            b_addr           <= {ADDR_W{1'b0}};
            o_addr           <= {ADDR_W{1'b0}};
            a_chunk          <= {VECTOR_W{1'b0}};
            b_chunk          <= {VECTOR_W{1'b0}};
            chunk_count      <= 8'd0;
            lane_mask        <= {NUM_LANES{1'b0}};
            reduce_running_max <= 1'b0;
            reduce_sum_acc   <= 64'sd0;
            reduce_max_acc   <= 32'sh80000000;
            reduce_wait_cnt  <= 4'd0;
            conv_idx         <= 8'd0;
            conv_out_vector  <= {NUM_LANES*FP16_W{1'b0}};
            sram_a_addr      <= {ADDR_W{1'b0}};
            sram_a_en        <= 1'b0;
            sram_b_addr      <= {ADDR_W{1'b0}};
            sram_b_en        <= 1'b0;
            sram_o_addr      <= {ADDR_W{1'b0}};
            sram_o_wdata     <= {VECTOR_W{1'b0}};
            sram_o_wen       <= 1'b0;
            sram_o_wstrb     <= 512'd0;
        end else begin
            // Default: de-assert single-cycle strobes
            sram_a_en    <= 1'b0;
            sram_b_en    <= 1'b0;
            sram_o_wen   <= 1'b0;
            sram_o_wstrb <= 512'd0;

            case (state)
                ST_IDLE: begin
                    if (cmd_start_r) begin
                        op_reg      <= ctrl_reg[3:0];
                        remaining   <= dim_reg[15:0];
                        a_addr      <= a_addr_reg;
                        b_addr      <= b_addr_reg;
                        o_addr      <= o_addr_reg;
                        status_busy <= 1'b1;
                        status_done <= 1'b0;
                        reduce_sum_acc <= 64'sd0;
                        reduce_max_acc <= 32'sh80000000;
                        reduce_running_max <= 1'b0;
                        if (dim_reg[15:0] == 16'd0)
                            state <= ST_DONE;
                        else
                            state <= ST_READ;
                    end
                end

                ST_READ: begin
                    // Set up addresses and read enables for this chunk
                    chunk_count <= (remaining >= NUM_LANES) ? 8'd128 : remaining[7:0];
                    lane_mask   <= mask_for_count((remaining >= NUM_LANES) ? 8'd128 : remaining[7:0]);
                    sram_a_addr <= a_addr;
                    sram_a_en   <= 1'b1;
                    if ((op_reg == OP_ADD) || (op_reg == OP_MUL) || (op_reg == OP_RESID)) begin
                        sram_b_addr <= b_addr;
                        sram_b_en   <= 1'b1;
                    end
                    state <= ST_LATCH;
                end

                ST_LATCH: begin
                    // SRAM data is now stable; capture the chunk(s)
                    a_chunk <= sram_a_rdata;
                    b_chunk <= sram_b_rdata;
                    case (op_reg)
                        OP_ADD, OP_MUL, OP_RESID: state <= ST_BIN_EXEC;
                        OP_MAX, OP_SUM:           state <= ST_REDUCE_FEED;
                        OP_CONV:                  state <= ST_CONV_FEED;
                        default:                  state <= ST_DONE;
                    endcase
                end

                // ── Binary vector ops (ADD/MUL/RESID) ──────────────────────
                ST_BIN_EXEC: begin
                    // Submodule valid_i asserted combinationally above
                    state <= ST_BIN_WRITE;
                end

                ST_BIN_WRITE: begin
                    sram_o_addr  <= o_addr;
                    sram_o_wdata <= (op_reg == OP_RESID) ? resid_result : alu_result;
                    if (chunk_count >= NUM_LANES)
                        sram_o_wstrb <= {512{1'b1}};
                    else
                        sram_o_wstrb <= ({512'h1 << (chunk_count * 4)}) - 512'h1;
                    sram_o_wen   <= 1'b1;

                    // Advance pointers
                    a_addr <= a_addr + CHUNK_BYTES_INT32[ADDR_W-1:0];
                    b_addr <= b_addr + CHUNK_BYTES_INT32[ADDR_W-1:0];
                    o_addr <= o_addr + CHUNK_BYTES_INT32[ADDR_W-1:0];
                    remaining <= remaining - {8'd0, chunk_count};

                    if (remaining <= NUM_LANES)
                        state <= ST_DONE;
                    else
                        state <= ST_READ;
                end

                // ── Reduction ops (MAX/SUM) ────────────────────────────────
                ST_REDUCE_FEED: begin
                    reduce_wait_cnt <= 4'd5;
                    state           <= ST_REDUCE_WAIT;
                end

                ST_REDUCE_WAIT: begin
                    if (reduce_wait_cnt == 4'd0)
                        state <= ST_REDUCE_ACC;
                    else
                        reduce_wait_cnt <= reduce_wait_cnt - 4'd1;
                end

                ST_REDUCE_ACC: begin
                    if (reduce_valid) begin
                        if (op_reg == OP_MAX) begin
                            reduce_running_max <= 1'b1;
                            if ($signed(reduce_result) > $signed(reduce_max_acc))
                                reduce_max_acc <= reduce_result;
                        end else begin
                            reduce_sum_acc <= reduce_sum_acc + reduce_result64;
                        end
                    end

                    a_addr <= a_addr + CHUNK_BYTES_INT32[ADDR_W-1:0];
                    remaining <= remaining - {8'd0, chunk_count};

                    if (remaining <= NUM_LANES)
                        state <= ST_REDUCE_WRITE;
                    else
                        state <= ST_READ;
                end

                ST_REDUCE_WRITE: begin
                    begin
                        reg signed [31:0] sum_sat;
                        if (reduce_sum_acc > INT32_MAX_64)
                            sum_sat = 32'sh7FFFFFFF;
                        else if (reduce_sum_acc < INT32_MIN_64)
                            sum_sat = 32'sh80000000;
                        else
                            sum_sat = reduce_sum_acc[31:0];

                        sram_o_addr  <= o_addr;
                        sram_o_wdata <= { {VECTOR_W-DATA_W{1'b0}},
                                          (op_reg == OP_MAX) ? reduce_max_acc : sum_sat };
                        sram_o_wstrb <= ({512'h1 << 4}) - 512'h1;  // lower 4 bytes
                        sram_o_wen   <= 1'b1;
                    end
                    state <= ST_DONE;
                end

                // ── CONV (INT32 -> FP16) ───────────────────────────────────
                ST_CONV_FEED: begin
                    // type_convert valid_i asserted combinationally
                    state <= ST_CONV_CAPTURE;
                end

                ST_CONV_CAPTURE: begin
                    if (tc_valid_o) begin
                        conv_out_vector[(conv_idx)*FP16_W +: FP16_W] <= tc_data_o;
                    end

                    if (conv_idx + 1 >= chunk_count) begin
                        state <= ST_CONV_WRITE;
                    end else begin
                        conv_idx <= conv_idx + 8'd1;
                        state    <= ST_CONV_FEED;
                    end
                end

                ST_CONV_WRITE: begin
                    sram_o_addr  <= o_addr;
                    sram_o_wdata <= { {VECTOR_W - NUM_LANES*FP16_W{1'b0}}, conv_out_vector };
                    if (chunk_count >= NUM_LANES)
                        sram_o_wstrb <= {512{1'b1}};
                    else
                        sram_o_wstrb <= ({512'h1 << (chunk_count * 2)}) - 512'h1;
                    sram_o_wen   <= 1'b1;

                    // Reset conv index for next chunk
                    conv_idx <= 8'd0;

                    a_addr <= a_addr + CHUNK_BYTES_INT32[ADDR_W-1:0];
                    o_addr <= o_addr + CHUNK_BYTES_FP16[ADDR_W-1:0];
                    remaining <= remaining - {8'd0, chunk_count};

                    if (remaining <= NUM_LANES)
                        state <= ST_DONE;
                    else
                        state <= ST_READ;
                end

                ST_DONE: begin
                    status_busy <= 1'b0;
                    status_done <= 1'b1;
                    state       <= ST_IDLE;
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule
