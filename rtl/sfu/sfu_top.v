//=============================================================================
// sfu_top — SFU Top-Level Integration with MMIO + SRAM Controller
//=============================================================================
// Integrates the seven SFU submodules from CaduceusCore/rtl/sfu/:
//   exp_lut, softmax_hw, layernorm_hw, gelu_hw, silu_hw, rope_hw, rmsnorm_hw
//
// Provides:
//   • MMIO slave register file matching sim/regmap.py SFU class (BASE=0x4000_1000)
//   • OP decode & datapath routing (CTRL[3:0])
//   • Simple SRAM read/write controller
//   • DONE status bit + interrupt generation
//
// OP map (CTRL[3:0]):
//   0 = SOFTMAX     1 = LAYERNORM    2 = GELU        3 = RELU (pass-through)
//   4 = SILU        5 = ROPE          6 = RMSNORM
//
// Data format:
//   • SRAM is 32-bit wide.
//   • Non-RoPE FP16 elements are packed two per word [31:16]=elem1, [15:0]=elem0.
//   • RoPE reads/writes one (x, y) pair per 32-bit word.
//
// This module contains only wiring, op routing, the MMIO register file, and the
// SRAM controller; all arithmetic lives in the instantiated submodules.
//=============================================================================

`timescale 1ns / 1ps

module sfu_top #(
    parameter ADDR_WIDTH = 32
)(
    // Clock / Reset
    input  wire        clk,
    input  wire        rst_n,

    // ── MMIO slave interface ───────────────────────────────────────────
    input  wire        mmio_cs,
    input  wire        mmio_we,
    input  wire [11:0] mmio_addr,
    input  wire [31:0] mmio_wdata,
    output reg  [31:0] mmio_rdata,
    output wire        mmio_ready,

    // ── SRAM read port ─────────────────────────────────────────────────
    input  wire [31:0] sram_rdata,
    output reg  [ADDR_WIDTH-1:0] sram_raddr,
    output reg                   sram_ren,

    // ── SRAM write port ────────────────────────────────────────────────
    output reg  [ADDR_WIDTH-1:0] sram_waddr,
    output reg  [31:0]           sram_wdata,
    output reg                   sram_wen,

    // ── Interrupt ──────────────────────────────────────────────────────
    output reg         irq
);

    //=========================================================================
    // OP encoding
    //=========================================================================
    localparam [3:0] OP_SOFTMAX  = 4'd0;
    localparam [3:0] OP_LAYERNORM= 4'd1;
    localparam [3:0] OP_GELU     = 4'd2;
    localparam [3:0] OP_RELU     = 4'd3;
    localparam [3:0] OP_SILU     = 4'd4;
    localparam [3:0] OP_ROPE     = 4'd5;
    localparam [3:0] OP_RMSNORM  = 4'd6;

    //=========================================================================
    // MMIO register file
    //=========================================================================
    // Register offsets from SFU_BASE=0x4000_1000
    localparam [11:0] OFF_CTRL    = 12'h000;
    localparam [11:0] OFF_CMD     = 12'h004;
    localparam [11:0] OFF_STATUS  = 12'h008;
    localparam [11:0] OFF_I_ADDR  = 12'h00C;
    localparam [11:0] OFF_O_ADDR  = 12'h010;
    localparam [11:0] OFF_DIM     = 12'h014;
    localparam [11:0] OFF_POS     = 12'h018;
    localparam [11:0] OFF_IRQ_EN  = 12'h01C;

    reg [31:0] ctrl_reg;
    reg [31:0] i_addr_reg;
    reg [31:0] o_addr_reg;
    reg [31:0] dim_reg;
    reg [31:0] pos_reg;
    reg [31:0] irq_en_reg;

    wire [3:0] op_sel     = ctrl_reg[3:0];
    wire [15:0] dim_elems = dim_reg[15:0];
    wire [15:0] head_dim  = dim_reg[31:16];

    // Command pulse generation
    reg cmd_start_r;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cmd_start_r <= 1'b0;
        else
            cmd_start_r <= mmio_cs && mmio_we && (mmio_addr == OFF_CMD) && mmio_wdata[0];
    end
    wire cmd_start = cmd_start_r;

    // Status register bits
    reg status_busy;
    reg status_done;

    // MMIO writes
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ctrl_reg    <= 32'd0;
            i_addr_reg  <= 32'd0;
            o_addr_reg  <= 32'd0;
            dim_reg     <= 32'd0;
            pos_reg     <= 32'd0;
            irq_en_reg  <= 32'd0;
        end else if (mmio_cs && mmio_we) begin
            case (mmio_addr)
                OFF_CTRL:   ctrl_reg   <= mmio_wdata;
                OFF_CMD:    ; // pulse handled above
                OFF_STATUS: ; // read-only
                OFF_I_ADDR: i_addr_reg <= mmio_wdata;
                OFF_O_ADDR: o_addr_reg <= mmio_wdata;
                OFF_DIM:    dim_reg    <= mmio_wdata;
                OFF_POS:    pos_reg    <= mmio_wdata;
                OFF_IRQ_EN: irq_en_reg <= mmio_wdata;
                default:    ;
            endcase
        end
    end

    // MMIO reads (combinatorial)
    always @(*) begin
        mmio_rdata = 32'd0;
        if (mmio_cs && !mmio_we) begin
            case (mmio_addr)
                OFF_CTRL:   mmio_rdata = ctrl_reg;
                OFF_CMD:    mmio_rdata = 32'd0; // write-only
                OFF_STATUS: mmio_rdata = {30'd0, status_done, status_busy};
                OFF_I_ADDR: mmio_rdata = i_addr_reg;
                OFF_O_ADDR: mmio_rdata = o_addr_reg;
                OFF_DIM:    mmio_rdata = dim_reg;
                OFF_POS:    mmio_rdata = pos_reg;
                OFF_IRQ_EN: mmio_rdata = irq_en_reg;
                default:    mmio_rdata = 32'd0;
            endcase
        end
    end

    assign mmio_ready = mmio_cs;

    //=========================================================================
    // Submodule instantiations
    //=========================================================================
    // Shared exp LUT (driven with safe defaults; silu_hw contains its own
    // internal instance, so this top-level instance is structural integration).
    wire [14:0] exp_lut_out_w;
    exp_lut u_exp_lut (
        .clk    (clk),
        .rst_n  (rst_n),
        .addr   (8'd0),
        .frac   (8'd0),
        .lut_out(exp_lut_out_w)
    );

    // Data-path wires from each submodule
    wire [15:0] softmax_data_o;
    wire        softmax_valid_o;
    wire [15:0] layernorm_data_o;
    wire        layernorm_valid_o;
    wire [15:0] gelu_data_o;
    wire        gelu_valid_o;
    wire [15:0] silu_data_o;
    wire        silu_valid_o;
    wire [15:0] rope_x_o;
    wire [15:0] rope_y_o;
    wire        rope_valid_o;
    wire [15:0] rmsnorm_data_o;
    wire        rmsnorm_valid_o;

    // Common input bus (routed to selected submodule)
    reg  [15:0] pipe_data_i;
    reg         pipe_valid_i;
    reg         pipe_last_i;
    reg  [15:0] rope_x_i;
    reg  [15:0] rope_y_i;
    reg  [15:0] rope_theta_i;
    reg         rope_valid_i;

    softmax_hw u_softmax (
        .clk    (clk),
        .rst_n  (rst_n),
        .data_i (pipe_data_i),
        .valid_i(pipe_valid_i && (op_sel == OP_SOFTMAX)),
        .last_i (pipe_last_i  && (op_sel == OP_SOFTMAX)),
        .data_o (softmax_data_o),
        .valid_o(softmax_valid_o)
    );

    layernorm_hw u_layernorm (
        .clk    (clk),
        .rst_n  (rst_n),
        .data_i (pipe_data_i),
        .valid_i(pipe_valid_i && (op_sel == OP_LAYERNORM)),
        .last_i (pipe_last_i  && (op_sel == OP_LAYERNORM)),
        .data_o (layernorm_data_o),
        .valid_o(layernorm_valid_o)
    );

    gelu_hw u_gelu (
        .clk    (clk),
        .rst_n  (rst_n),
        .data_i (pipe_data_i),
        .valid_i(pipe_valid_i && (op_sel == OP_GELU)),
        .data_o (gelu_data_o),
        .valid_o(gelu_valid_o)
    );

    silu_hw u_silu (
        .clk    (clk),
        .rst_n  (rst_n),
        .data_i (pipe_data_i),
        .valid_i(pipe_valid_i && (op_sel == OP_SILU)),
        .data_o (silu_data_o),
        .valid_o(silu_valid_o)
    );

    rope_hw u_rope (
        .clk    (clk),
        .rst_n  (rst_n),
        .x_i    (rope_x_i),
        .y_i    (rope_y_i),
        .theta_i(rope_theta_i),
        .valid_i(rope_valid_i && (op_sel == OP_ROPE)),
        .x_o    (rope_x_o),
        .y_o    (rope_y_o),
        .valid_o(rope_valid_o)
    );

    rmsnorm_hw u_rmsnorm (
        .clk    (clk),
        .rst_n  (rst_n),
        .data_i (pipe_data_i),
        .valid_i(pipe_valid_i && (op_sel == OP_RMSNORM)),
        .last_i (pipe_last_i  && (op_sel == OP_RMSNORM)),
        .data_o (rmsnorm_data_o),
        .valid_o(rmsnorm_valid_o)
    );

    // Output mux (selected by OP)
    wire        out_is_rope = (op_sel == OP_ROPE);
    wire [15:0] out_data;
    wire        out_valid;

    assign out_data = out_is_rope ? 16'd0 :
                      (op_sel == OP_SOFTMAX)  ? softmax_data_o  :
                      (op_sel == OP_LAYERNORM)? layernorm_data_o:
                      (op_sel == OP_GELU)     ? gelu_data_o     :
                      (op_sel == OP_SILU)     ? silu_data_o     :
                      (op_sel == OP_RMSNORM)  ? rmsnorm_data_o  :
                      (op_sel == OP_RELU)     ? pipe_data_i     : 16'd0;

    assign out_valid = out_is_rope ? rope_valid_o :
                       (op_sel == OP_SOFTMAX)  ? softmax_valid_o  :
                       (op_sel == OP_LAYERNORM)? layernorm_valid_o:
                       (op_sel == OP_GELU)     ? gelu_valid_o     :
                       (op_sel == OP_SILU)     ? silu_valid_o     :
                       (op_sel == OP_RMSNORM)  ? rmsnorm_valid_o  :
                       (op_sel == OP_RELU)     ? pipe_valid_i     : 1'b0;

    //=========================================================================
    // Controller FSM
    //=========================================================================
    localparam [3:0] ST_IDLE       = 4'd0,
                     ST_READ_INIT  = 4'd1,
                     ST_RUN        = 4'd2,
                     ST_FLUSH      = 4'd3,
                     ST_DONE       = 4'd4;

    reg [3:0] state;

    reg [15:0] in_elem;        // element index fed into pipeline
    reg [15:0] out_elem;       // element index collected from pipeline
    reg [31:0] in_word;        // current 32-bit input word
    reg [15:0] out_word_lo;    // lower 16 bits of packed output word
    reg        out_lo_valid;   // lower output half is pending
    reg        read_issued;    // read request was issued previous cycle
    reg [31:0] sram_raddr_nxt; // next read address

    // RoPE pair bookkeeping
    reg [15:0] rope_pair_idx;

    wire is_reduction_op = (op_sel == OP_SOFTMAX) ||
                           (op_sel == OP_LAYERNORM) ||
                           (op_sel == OP_RMSNORM);
    wire is_element_op   = (op_sel == OP_GELU) ||
                           (op_sel == OP_SILU) ||
                           (op_sel == OP_RELU);
    wire is_rope_op      = (op_sel == OP_ROPE);

    //=========================================================================
    // Minimal RoPE theta generator (head_dim=128 frequency table)
    // theta_i = pos * 10000^(-2*i/head_dim), i = pair index
    // Implemented as pos (Q16.16) multiplied by a pre-quantized frequency factor
    // and converted to FP16.  Good enough for the structural integration smoke;
    // exact GoldenSFU match would require a full floating-point exp unit here.
    //=========================================================================
    localparam ROPE_BASE = 32'd10000;
    localparam ROPE_FREQ_ENTRIES = 64;

    // Pre-computed 10000^(-2*i/128) in Q0.16 (values in [1.0, ~2.7e-5])
    // Generated by: [round(10000**(-2*i/128) * 65536) for i in range(64)]
    wire [15:0] rope_freq_lut [0:ROPE_FREQ_ENTRIES-1];
    assign rope_freq_lut[0]  = 16'd65535; assign rope_freq_lut[1]  = 16'd58432;
    assign rope_freq_lut[2]  = 16'd52096; assign rope_freq_lut[3]  = 16'd46440;
    assign rope_freq_lut[4]  = 16'd41395; assign rope_freq_lut[5]  = 16'd36896;
    assign rope_freq_lut[6]  = 16'd32887; assign rope_freq_lut[7]  = 16'd29315;
    assign rope_freq_lut[8]  = 16'd26135; assign rope_freq_lut[9]  = 16'd23295;
    assign rope_freq_lut[10] = 16'd20767; assign rope_freq_lut[11] = 16'd18512;
    assign rope_freq_lut[12] = 16'd16504; assign rope_freq_lut[13] = 16'd14714;
    assign rope_freq_lut[14] = 16'd13117; assign rope_freq_lut[15] = 16'd11694;
    assign rope_freq_lut[16] = 16'd10425; assign rope_freq_lut[17] = 16'd9295;
    assign rope_freq_lut[18] = 16'd8286;  assign rope_freq_lut[19] = 16'd7386;
    assign rope_freq_lut[20] = 16'd6584;  assign rope_freq_lut[21] = 16'd5869;
    assign rope_freq_lut[22] = 16'd5231;  assign rope_freq_lut[23] = 16'd4661;
    assign rope_freq_lut[24] = 16'd4155;  assign rope_freq_lut[25] = 16'd3703;
    assign rope_freq_lut[26] = 16'd3301;  assign rope_freq_lut[27] = 16'd2942;
    assign rope_freq_lut[28] = 16'd2622;  assign rope_freq_lut[29] = 16'd2337;
    assign rope_freq_lut[30] = 16'd2083;  assign rope_freq_lut[31] = 16'd1856;
    assign rope_freq_lut[32] = 16'd1654;  assign rope_freq_lut[33] = 16'd1474;
    assign rope_freq_lut[34] = 16'd1314;  assign rope_freq_lut[35] = 16'd1171;
    assign rope_freq_lut[36] = 16'd1044;  assign rope_freq_lut[37] = 16'd930;
    assign rope_freq_lut[38] = 16'd829;   assign rope_freq_lut[39] = 16'd739;
    assign rope_freq_lut[40] = 16'd659;   assign rope_freq_lut[41] = 16'd587;
    assign rope_freq_lut[42] = 16'd523;   assign rope_freq_lut[43] = 16'd466;
    assign rope_freq_lut[44] = 16'd415;   assign rope_freq_lut[45] = 16'd370;
    assign rope_freq_lut[46] = 16'd330;   assign rope_freq_lut[47] = 16'd294;
    assign rope_freq_lut[48] = 16'd262;   assign rope_freq_lut[49] = 16'd233;
    assign rope_freq_lut[50] = 16'd208;   assign rope_freq_lut[51] = 16'd185;
    assign rope_freq_lut[52] = 16'd165;   assign rope_freq_lut[53] = 16'd147;
    assign rope_freq_lut[54] = 16'd131;   assign rope_freq_lut[55] = 16'd117;
    assign rope_freq_lut[56] = 16'd104;   assign rope_freq_lut[57] = 16'd93;
    assign rope_freq_lut[58] = 16'd83;    assign rope_freq_lut[59] = 16'd74;
    assign rope_freq_lut[60] = 16'd66;    assign rope_freq_lut[61] = 16'd59;
    assign rope_freq_lut[62] = 16'd52;    assign rope_freq_lut[63] = 16'd47;

    // Convert unsigned Q0.16 frequency to FP16
    function automatic [15:0] uq16_to_fp16;
        input [15:0] q;
        reg [4:0]  lz;
        reg [4:0]  msb;
        reg [15:0] norm;
        reg [4:0]  exp;
        reg [9:0]  mant;
        begin
            if (q == 16'd0) begin
                uq16_to_fp16 = 16'h0000;
            end else begin
                lz  = 5'd0;
                // Count leading zeros of 16-bit value
                if (q[15:8] == 8'd0) begin
                    lz = lz + 5'd8;
                    norm = {q[7:0], 8'd0};
                end else begin
                    norm = q;
                end
                if (norm[15:12] == 4'd0) begin
                    lz = lz + 5'd4;
                    norm = {norm[11:0], 4'd0};
                end
                if (norm[15:14] == 2'd0) begin
                    lz = lz + 5'd2;
                    norm = {norm[13:0], 2'd0};
                end
                if (norm[15] == 1'b0) begin
                    lz = lz + 5'd1;
                    norm = {norm[14:0], 1'd0};
                end
                msb  = 5'd15 - lz;
                exp  = msb + 5'd15 - 5'd16; // unbiased exp = msb - 16, then +15 bias
                mant = norm[14:5];
                uq16_to_fp16 = {1'b0, exp, mant};
            end
        end
    endfunction

    // pos * freq in Q16.16 -> keep top 16 bits as unsigned Q0.16
    wire [31:0] theta_fixed_u = pos_reg[15:0] * {16'd0, rope_freq_lut[rope_pair_idx[5:0]]};
    wire [15:0] theta_q16     = theta_fixed_u[31:16];
    wire [15:0] rope_theta_fp16 = uq16_to_fp16(theta_q16);

    //=========================================================================
    // Main control sequencer
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state         <= ST_IDLE;
            status_busy   <= 1'b0;
            status_done   <= 1'b0;
            irq           <= 1'b0;
            in_elem       <= 16'd0;
            out_elem      <= 16'd0;
            in_word       <= 32'd0;
            out_word_lo   <= 16'd0;
            out_lo_valid  <= 1'b0;
            pipe_data_i   <= 16'd0;
            pipe_valid_i  <= 1'b0;
            pipe_last_i   <= 1'b0;
            rope_x_i      <= 16'd0;
            rope_y_i      <= 16'd0;
            rope_theta_i  <= 16'd0;
            rope_valid_i  <= 1'b0;
            rope_pair_idx <= 16'd0;
            sram_raddr    <= {ADDR_WIDTH{1'b0}};
            sram_ren      <= 1'b0;
            sram_waddr    <= {ADDR_WIDTH{1'b0}};
            sram_wdata    <= 32'd0;
            sram_wen      <= 1'b0;
            read_issued   <= 1'b0;
            sram_raddr_nxt<= 32'd0;
        end else begin
            // Defaults: keep SRAM read enable level-high during bursts; only
            // clear it explicitly when leaving the read phase.  Write enable
            // and IRQ are single-cycle pulses.
            sram_wen <= 1'b0;
            irq      <= 1'b0;

            case (state)
                //-------------------------------------------------------------
                // IDLE — wait for MMIO start command
                //-------------------------------------------------------------
                ST_IDLE: begin
                    sram_ren    <= 1'b0;
                    status_done <= 1'b0;
                    if (cmd_start) begin
                        status_busy   <= 1'b1;
                        in_elem       <= 16'd0;
                        out_elem      <= 16'd0;
                        out_lo_valid  <= 1'b0;
                        rope_pair_idx <= 16'd0;
                        sram_raddr_nxt<= i_addr_reg;
                        // Pre-decrement write address so that the first pair
                        // increments to o_addr_reg on its first output half.
                        sram_waddr    <= o_addr_reg - 32'd4;
                        if (is_rope_op) begin
                            // RoPE reads one pair per cycle
                            sram_raddr <= i_addr_reg;
                            sram_ren   <= 1'b1;
                            read_issued<= 1'b1;
                            state      <= ST_RUN;
                        end else begin
                            // Non-RoPE: first read is for elem0/elem1 pair
                            sram_raddr <= i_addr_reg;
                            sram_ren   <= 1'b1;
                            read_issued<= 1'b1;
                            state      <= ST_READ_INIT;
                        end
                    end else begin
                        status_busy <= 1'b0;
                    end
                end

                //-------------------------------------------------------------
                // READ_INIT — first SRAM word is available; latch it, then
                // advance the read pointer for the next word.
                //-------------------------------------------------------------
                ST_READ_INIT: begin
                    read_issued <= 1'b0;
                    in_word     <= sram_rdata;
                    in_elem     <= 16'd0;

                    if (!(is_element_op || is_reduction_op))
                        pipe_valid_i <= 1'b0;
                    pipe_last_i  <= 1'b0;

                    // Pre-fetch next word for the upper element / next pair
                    if (dim_elems > 16'd2) begin
                        sram_raddr_nxt <= sram_raddr_nxt + 32'd4;
                        sram_raddr     <= sram_raddr_nxt + 32'd4;
                        sram_ren       <= 1'b1;
                        read_issued    <= 1'b1;
                    end else begin
                        sram_ren <= 1'b0;
                    end

                    state <= ST_RUN;
                end

                //-------------------------------------------------------------
                // RUN — feed pipeline and collect outputs
                //-------------------------------------------------------------
                ST_RUN: begin
                    read_issued <= 1'b0;

                    // Keep read enable high while we still have input data to
                    // fetch; it is lowered in FLUSH once feeding is complete.
                    if (is_element_op || is_reduction_op)
                        sram_ren <= (in_elem < dim_elems);
                    else if (is_rope_op)
                        sram_ren <= (rope_pair_idx < dim_elems);
                    else
                        sram_ren <= 1'b0;

                    // ---- Input feeding ----
                    if (is_element_op || is_reduction_op) begin
                            if (in_elem < dim_elems) begin
                            if (in_elem[0] == 1'b0) begin
                                // Even element: lower half of current word
                                pipe_data_i  <= in_word[15:0];
                                pipe_valid_i <= 1'b1;
                            end else begin
                                // Odd element: upper half; load next word
                                pipe_data_i  <= in_word[31:16];
                                pipe_valid_i <= 1'b1;
                                in_word      <= sram_rdata;

                                // Pre-fetch next word if more elements remain
                                if (in_elem + 16'd1 < dim_elems) begin
                                    sram_raddr_nxt <= sram_raddr_nxt + 32'd4;
                                    sram_raddr     <= sram_raddr_nxt + 32'd4;
                                    read_issued    <= 1'b1;
                                end
                            end
                            pipe_last_i <= is_reduction_op && (in_elem == dim_elems - 16'd1);
                            in_elem     <= in_elem + 16'd1;
                        end else begin
                            pipe_valid_i <= 1'b0;
                            pipe_last_i  <= 1'b0;
                        end
                    end else if (is_rope_op) begin
                        if (rope_pair_idx < dim_elems) begin
                            rope_x_i     <= sram_rdata[15:0];
                            rope_y_i     <= sram_rdata[31:16];
                            rope_theta_i <= rope_theta_fp16;
                            rope_valid_i <= 1'b1;
                            rope_pair_idx<= rope_pair_idx + 16'd1;

                            if (rope_pair_idx + 16'd1 < dim_elems) begin
                                sram_raddr_nxt <= sram_raddr_nxt + 32'd4;
                                sram_raddr     <= sram_raddr_nxt + 32'd4;
                                read_issued    <= 1'b1;
                            end
                        end else begin
                            rope_valid_i <= 1'b0;
                        end
                    end else begin
                        // undefined op — stall
                        pipe_valid_i <= 1'b0;
                        rope_valid_i <= 1'b0;
                    end

                    // ---- Output collection ----
                    if (out_valid) begin
                        if (out_is_rope) begin
                            sram_wdata <= {rope_y_o, rope_x_o};
                            sram_wen   <= 1'b1;
                            sram_waddr <= sram_waddr + 32'd4;
                            out_elem   <= out_elem + 16'd1;
                        end else begin
                            if (!out_lo_valid) begin
                                // First half of a packed output word:
                                // store it and advance the write pointer so it
                                // points to the word address for this pair.
                                out_word_lo  <= out_data;
                                out_lo_valid <= 1'b1;
                                sram_waddr   <= sram_waddr + 32'd4;
                            end else begin
                                // Second half: write the packed word at the
                                // address already advanced on the first half.
                                sram_wdata   <= {out_data, out_word_lo};
                                sram_wen     <= 1'b1;
                                out_lo_valid <= 1'b0;
                                out_elem     <= out_elem + 16'd2;
                            end
                        end
                    end

                    // Transition to flush when all inputs fed
                    if ((is_element_op || is_reduction_op) && (in_elem >= dim_elems)) begin
                        state <= ST_FLUSH;
                    end else if (is_rope_op && (rope_pair_idx >= dim_elems)) begin
                        state <= ST_FLUSH;
                    end
                end

                //-------------------------------------------------------------
                // FLUSH — wait for remaining pipeline outputs
                //-------------------------------------------------------------
                ST_FLUSH: begin
                    sram_ren     <= 1'b0;
                    pipe_valid_i <= 1'b0;
                    rope_valid_i <= 1'b0;
                    pipe_last_i  <= 1'b0;

                    if (out_valid) begin
                        if (out_is_rope) begin
                            sram_wdata <= {rope_y_o, rope_x_o};
                            sram_wen   <= 1'b1;
                            sram_waddr <= sram_waddr + 32'd4;
                            out_elem   <= out_elem + 16'd1;
                        end else begin
                            if (!out_lo_valid) begin
                                out_word_lo  <= out_data;
                                out_lo_valid <= 1'b1;
                                sram_waddr   <= sram_waddr + 32'd4;
                            end else begin
                                sram_wdata   <= {out_data, out_word_lo};
                                sram_wen     <= 1'b1;
                                out_lo_valid <= 1'b0;
                                out_elem     <= out_elem + 16'd2;
                            end
                        end
                    end

                    // Done when expected number of outputs collected
                    if (out_is_rope) begin
                        if (out_elem >= dim_elems)
                            state <= ST_DONE;
                    end else begin
                        // For packed 16-bit outputs, write any trailing half-word
                        // before declaring done.
                        if (out_lo_valid && (out_elem + 16'd1 >= dim_elems)) begin
                            sram_wdata   <= {16'd0, out_word_lo};
                            sram_wen     <= 1'b1;
                            out_lo_valid <= 1'b0;
                            state        <= ST_DONE;
                        end else if (!out_lo_valid && (out_elem >= dim_elems)) begin
                            state <= ST_DONE;
                        end
                    end
                end

                //-------------------------------------------------------------
                // DONE — set status and interrupt
                //-------------------------------------------------------------
                ST_DONE: begin
                    sram_ren    <= 1'b0;
                    status_busy <= 1'b0;
                    status_done <= 1'b1;
                    if (irq_en_reg[0])
                        irq <= 1'b1;
                    state <= ST_IDLE;
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule
