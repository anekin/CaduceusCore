`timescale 1ns / 1ps
//=============================================================================
// f16_to_i32 — IEEE-754 FP16 → INT32 converter
//=============================================================================
// Inverse of type_convert.v: converts FP16 activations to INT32 for the
// Vector/MXU datapath. 1-cycle registered pipeline.
//
// Semantics:
//   - Round toward zero (truncate fractional part).
//   - FP16 zero and subnormals flush to INT32 0.
//   - FP16 ±Inf and NaN saturate sign-aware to INT32_MAX / INT32_MIN.
//   - Normal FP16 values are at most ±65504, so they fit in INT32 without
//     saturation; the result is the truncated integer magnitude with sign.
//
// FP16 layout: sign(1) | exponent(5) | mantissa(10), bias = 15.
//=============================================================================

module f16_to_i32 (
    input  wire         clk,
    input  wire         rst_n,

    input  wire [15:0]  data_i,       // IEEE-754 FP16
    input  wire         valid_i,

    output wire [31:0]  data_o,       // signed INT32
    output wire         valid_o
);

    //-------------------------------------------------------------------------
    // Local parameters
    //-------------------------------------------------------------------------
    localparam [31:0] INT32_MAX_VAL = 32'sh7FFFFFFF;
    localparam [31:0] INT32_MIN_VAL = 32'sh80000000;
    localparam [4:0]  BIAS          = 5'd15;

    //-------------------------------------------------------------------------
    // Stage 0: Combinational conversion
    //-------------------------------------------------------------------------

    // ---- 1. Decompose FP16 fields -----------------------------------------
    wire        sign;
    wire [4:0]  exp;
    wire [9:0]  mant;

    assign sign = data_i[15];
    assign exp  = data_i[14:10];
    assign mant = data_i[9:0];

    // ---- 2. Special-case detection ----------------------------------------
    wire is_zero     = (exp == 5'd0) && (mant == 10'd0);
    wire is_subnorm  = (exp == 5'd0) && (mant != 10'd0);
    wire is_inf_nan  = (exp == 5'd31);

    // ---- 3. Normal-path magnitude computation -----------------------------
    // value = (-1)^sign * 2^(exp-15) * (1 + mant/1024)
    //       = (-1)^sign * (1024 + mant) * 2^(exp - 25)
    // For exp < 25 the right shift discards fractional bits (truncate toward
    // zero). For exp >= 25 the left shift produces the integer magnitude.
    wire [10:0] significand;
    wire signed [5:0] shift;
    reg  [31:0] int_mag;

    assign significand = {1'b1, mant};           // 11 bits: 1024..2047
    assign shift       = {1'b0, exp} - 6'sd25;   // signed shift: -24..+5

    always @(*) begin
        if (shift < 6'sd0) begin
            // Fractional result: truncate toward zero
            int_mag = {21'd0, significand >> (-shift)};
        end else begin
            // Integer result: pad to 32 bits before shifting to avoid
            // Verilog variable-shift width truncation.
            int_mag = ({21'd0, significand} << shift[4:0]);
        end
    end

    // ---- 4. Apply sign and saturate ---------------------------------------
    reg [31:0] result_comb;

    always @(*) begin
        if (is_zero || is_subnorm) begin
            result_comb = 32'd0;
        end else if (is_inf_nan) begin
            // Sign-aware saturation for ±Inf and NaN
            result_comb = sign ? INT32_MIN_VAL : INT32_MAX_VAL;
        end else begin
            // Normal number: apply sign to truncated magnitude.
            // int_mag holds the full unsigned magnitude (max 65504).
            if (sign)
                result_comb = -int_mag;
            else
                result_comb = int_mag;
        end
    end

    //-------------------------------------------------------------------------
    // Stage 1: Pipeline register (1 cycle latency)
    //-------------------------------------------------------------------------
    reg [31:0] data_r;
    reg        valid_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_r  <= 32'd0;
            valid_r <= 1'b0;
        end else begin
            data_r  <= result_comb;
            valid_r <= valid_i;
        end
    end

    assign data_o  = data_r;
    assign valid_o = valid_r;

endmodule
