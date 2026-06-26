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
    // NOTE: silu_hw contains its own internal exp_lut instance; this top-level
    // no longer instantiates a redundant shared exp_lut.

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
    // RoPE theta generator (head_dim=128 frequency table)
    // theta_i = pos * 10000^(-2*i/head_dim), i = pair index
    // Load inv_freq from a 128-entry Q0.30 ROM, multiply by pos in 64-bit,
    // reduce to [-pi/2, pi/2] with quadrant flip, and convert to FP16.
    // The flip is applied to the (x,y) inputs so rope_hw stays unchanged.
    //=========================================================================
    localparam ROPE_HEAD_DIM        = 128;
    localparam ROPE_INV_FRAC        = 30;
    localparam ROPE_FIXED_FRAC      = 14;   // rope_hw internal Q18.14

    localparam signed [63:0] ROPE_TWO_PI_Q30 = 64'sd6746518852;
    localparam signed [63:0] ROPE_PI_Q30     = 64'sd3373259426;
    localparam signed [63:0] ROPE_PI_HALF_Q30= 64'sd1686629713;

    // 64-entry inv_freq ROM: 10000^(-2*i/128) in Q0.30 (head_dim/2 pairs)
    reg signed [31:0] rope_inv_freq_rom [0:ROPE_HEAD_DIM/2-1];
    initial begin
        $readmemh("CaduceusCore/rtl/test_vectors/sfu/luts/rope_theta_inv_freq.hex", rope_inv_freq_rom);
    end

    // 64-bit product gives theta in Q0.30
    wire signed [63:0] pos_ext        = $signed(pos_reg);
    wire signed [63:0] inv_freq_q30   = $signed(rope_inv_freq_rom[rope_pair_idx[5:0]]);
    wire signed [63:0] theta_full_q30 = pos_ext * inv_freq_q30;

    // Reduce theta to [-pi, pi]
    wire signed [63:0] theta_quo  = theta_full_q30 / ROPE_TWO_PI_Q30;
    wire signed [63:0] theta_rem  = theta_full_q30 - theta_quo * ROPE_TWO_PI_Q30;
    wire signed [63:0] theta_pm_pi = (theta_rem > ROPE_PI_Q30)  ? (theta_rem - ROPE_TWO_PI_Q30) :
                                     (theta_rem < -ROPE_PI_Q30) ? (theta_rem + ROPE_TWO_PI_Q30) : theta_rem;

    // Reduce to [-pi/2, pi/2] and record the quadrant flip
    reg                rope_flip;
    reg  signed [63:0] theta_reduced_q30;
    always @(*) begin
        if (theta_pm_pi > ROPE_PI_HALF_Q30) begin
            theta_reduced_q30 = theta_pm_pi - ROPE_PI_Q30;
            rope_flip         = 1'b1;
        end else if (theta_pm_pi < -ROPE_PI_HALF_Q30) begin
            theta_reduced_q30 = theta_pm_pi + ROPE_PI_Q30;
            rope_flip         = 1'b1;
        end else begin
            theta_reduced_q30 = theta_pm_pi;
            rope_flip         = 1'b0;
        end
    end

    // Convert reduced angle Q0.30 -> Q18.14 -> FP16 for rope_hw
    wire signed [31:0] theta_fixed = theta_reduced_q30 >>> (ROPE_INV_FRAC - ROPE_FIXED_FRAC);

    function automatic [15:0] fixed_q1814_to_fp16;
        input signed [31:0] f;
        reg        sign;
        reg [31:0] abs_f;
        integer    w, e, exp, shift;
        reg [9:0]  mant;
        reg [31:0] round_bits;
        integer    half;
        begin
            if (f == 0) begin
                fixed_q1814_to_fp16 = 16'h0000;
            end else begin
                sign  = (f < 0);
                abs_f = sign ? -$signed(f) : f;
                w     = ((abs_f & (abs_f - 1)) == 0) ? ($clog2(abs_f) + 1)
                                                     : $clog2(abs_f);
                e     = w - 1 - ROPE_FIXED_FRAC;
                if (e >= -14) begin
                    exp   = e + 15;
                    shift = w - 11;
                    if (shift > 0) begin
                        mant       = (abs_f >> shift) & 10'h3FF;
                        round_bits = abs_f & ((1 << shift) - 1);
                        half       = 1 << (shift - 1);
                        if (round_bits >= half) begin
                            mant = mant + 1;
                            if (mant == 10'd0) begin
                                exp = exp + 1;
                            end
                        end
                    end else if (shift == 0) begin
                        mant = abs_f[9:0];
                    end else begin
                        mant = (abs_f << (-shift)) & 10'h3FF;
                    end
                    if (exp >= 31)
                        fixed_q1814_to_fp16 = {sign, 5'h1F, 10'h000};
                    else
                        fixed_q1814_to_fp16 = {sign, exp[4:0], mant};
                end else begin
                    shift = 24 - ROPE_FIXED_FRAC;
                    if (shift >= 0)
                        mant = (abs_f << shift) & 10'h3FF;
                    else
                        mant = (abs_f >> (-shift)) & 10'h3FF;
                    fixed_q1814_to_fp16 = {sign, 5'h00, mant};
                end
            end
        end
    endfunction

    wire [15:0] rope_theta_fp16 = fixed_q1814_to_fp16(theta_fixed);

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
                            // Apply the quadrant flip to the input pair so the
                            // reduced angle stays inside [-pi/2, pi/2]; rope_hw
                            // then rotates without its own pi flip.
                            rope_x_i     <= {sram_rdata[15] ^ rope_flip, sram_rdata[14:0]};
                            rope_y_i     <= {sram_rdata[31] ^ rope_flip, sram_rdata[30:16]};
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
