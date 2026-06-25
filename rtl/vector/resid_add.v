//=============================================================================
// resid_add — 128-wide INT32 saturation residual connection
//=============================================================================
// Task 11 of sfu-vector-phase2 Wave 2.
//
// Simple 128-element-wide residual adder: result = original + delta, both INT32,
// with saturation to INT32 range on overflow/underflow.
//
// Uses INT64 intermediate per lane to detect overflow before clamping — same
// discipline as vector_alu (Task 4), pe.v, and accumulator.v.  This is an
// intentional improvement over the GoldenVector.residual_add() numpy int32
// wrap-around behaviour.
//
// Latency: 1 cycle (registered output).  Inputs at cycle N → valid result at
// cycle N+1.
//
// No lane_mask needed — this is a pure arithmetic block; masking is handled by
// the upstream ALU or the vector_top dispatch.
//
// Parameters:
//   NUM_LANES — SIMD width (default 128).
//=============================================================================

module resid_add #(
    parameter integer NUM_LANES = 128
) (
    input  wire                         clk,
    input  wire                         rst_n,          // active-low reset

    input  wire [NUM_LANES*32-1:0]      orig_i,         // packed original INT32 vector
    input  wire [NUM_LANES*32-1:0]      delta_i,        // packed delta INT32 vector
    input  wire                         valid_i,        // input valid strobe

    output wire [NUM_LANES*32-1:0]      result_o,       // packed saturated INT32 vector
    output wire                         valid_o         // output valid (registered valid_i)
);

    //-------------------------------------------------------------------------
    // Local parameters
    //-------------------------------------------------------------------------
    localparam signed [63:0] INT32_MAX_64 = 64'sd2147483647;   // 2^31 - 1
    localparam signed [63:0] INT32_MIN_64 = -64'sd2147483648;  // -2^31

    //-------------------------------------------------------------------------
    // Per-lane saturation datapath with pipeline register
    //-------------------------------------------------------------------------
    genvar i;
    generate
        for (i = 0; i < NUM_LANES; i = i + 1) begin : gen_lane

            // ---- slice inputs -------------------------------------------------
            wire signed [31:0]     orig_lane  = orig_i[i*32  +: 32];
            wire signed [31:0]     delta_lane = delta_i[i*32 +: 32];

            // ---- 64-bit intermediate: sign-extend BEFORE arithmetic -----------
            // MUST widen to 64 bits before addition.  Verilog determines
            // expression width from operand widths (self-determined context),
            // NOT from the LHS target width.  Adding two 32-bit operands
            // produces a 32-bit result that wraps before the 64-bit wire
            // ever sees it, defeating overflow detection.
            wire signed [63:0]     orig_se  = $signed(orig_lane);   // 32→64
            wire signed [63:0]     delta_se = $signed(delta_lane);  // 32→64
            wire signed [63:0]     sum_64   = orig_se + delta_se;   // full 64-bit

            // ---- saturation detection -----------------------------------------
            wire pos_ovf = (sum_64 > INT32_MAX_64);
            wire neg_ovf = (sum_64 < INT32_MIN_64);

            // ---- saturated result ---------------------------------------------
            wire signed [31:0] comb_result;
            assign comb_result = pos_ovf ? 32'h7FFFFFFF :
                                 neg_ovf ? 32'h80000000 :
                                           sum_64[31:0];

            // ---- pipeline register (1 cycle) ----------------------------------
            reg signed [31:0] lane_result_r;

            always @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    lane_result_r <= 32'd0;
                else
                    lane_result_r <= comb_result;
            end

            // ---- drive output slice -------------------------------------------
            assign result_o[i*32 +: 32] = lane_result_r;

        end
    endgenerate

    //-------------------------------------------------------------------------
    // valid_i → valid_o pipeline register (1 cycle)
    //-------------------------------------------------------------------------
    reg valid_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            valid_r <= 1'b0;
        else
            valid_r <= valid_i;
    end

    assign valid_o = valid_r;

endmodule
