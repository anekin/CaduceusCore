//=============================================================================
// vector_alu — 128-wide SIMD ALU (add/mul/max/pass_a)
//=============================================================================
// Task 4 of sfu-vector-phase2 Wave 1.
//
// 128-element-wide SIMD ALU with saturation clamping (ADD/MUL) and lane mask.
// One-cycle registered pipeline: inputs at cycle N → valid result at cycle N+1.
//
// Operations (op[1:0]):
//   00 = ADD   — saturating element-wise add
//   01 = MUL   — saturating element-wise multiply
//   10 = MAX   — element-wise pairwise maximum
//   11 = PASS_A — passthrough a_i unchanged
//
// ADD / MUL: use 64-bit intermediate to detect overflow; clamp to
//   INT32_MAX (2^31-1) or INT32_MIN (-2^31).  Matches the RTL saturation
//   discipline used in pe.v and accumulator.v — an intentional improvement
//   over the GoldenVector numpy int32 wrap-around.
//
// Lane mask: lane_mask[i]==0 disables lane i:
//   ADD     → disabled lane passes a_i through (feed-through)
//   MUL     → disabled lane outputs 0
//   MAX     → disabled lane outputs 0
//   PASS_A  → disabled lane outputs a_i (equivalent to enabled)
//
// Parameters:
//   NUM_LANES — SIMD width (default 128).  Must match vector_top integration.
//=============================================================================

module vector_alu #(
    parameter integer NUM_LANES = 128
) (
    input  wire                         clk,
    input  wire                         rst_n,          // active-low reset

    input  wire [1:0]                   op,             // 00=ADD, 01=MUL, 10=MAX, 11=PASS_A
    input  wire [NUM_LANES*32-1:0]      a_i,            // packed INT32 vector A
    input  wire [NUM_LANES*32-1:0]      b_i,            // packed INT32 vector B
    input  wire [NUM_LANES-1:0]         lane_mask,      // per-lane enable (1=active)
    input  wire                         valid_i,        // input valid strobe

    output wire [NUM_LANES*32-1:0]      result_o,       // packed INT32 vector output
    output wire                         valid_o         // output valid (registered valid_i)
);

    //-------------------------------------------------------------------------
    // Local parameters
    //-------------------------------------------------------------------------
    localparam signed [63:0] INT32_MAX_64 = 64'sd2147483647;   // 2^31 - 1
    localparam signed [63:0] INT32_MIN_64 = -64'sd2147483648;  // -2^31

    localparam OP_ADD   = 2'b00;
    localparam OP_MUL   = 2'b01;
    localparam OP_MAX   = 2'b10;
    localparam OP_PASS_A= 2'b11;

    //-------------------------------------------------------------------------
    // Unpacked per-lane wires (registered, stage-1)
    //-------------------------------------------------------------------------
    genvar i;
    generate
        for (i = 0; i < NUM_LANES; i = i + 1) begin : gen_lane

            // ---- slice inputs -----------------------------------------------
            wire signed [31:0] a_lane = a_i[i*32 +: 32];
            wire signed [31:0] b_lane = b_i[i*32 +: 32];
            wire               mask   = lane_mask[i];

            // ---- ADD: 64-bit intermediate with saturation -------------------
            // MUST sign-extend operands to 64 bits BEFORE addition.
            // In Verilog "A + B" width = max(width(A), width(B));
            // without explicit widening the sum is computed in 32 bits
            // and wraps before the 64-bit assignment sees it.
            wire signed [63:0] a_se = $signed(a_lane);  // 32→64 sign-ext
            wire signed [63:0] b_se = $signed(b_lane);  // 32→64 sign-ext
            wire signed [63:0] sum_64 = a_se + b_se;    // 64-bit add, no wrap

            wire add_pos_ovf = (sum_64 > INT32_MAX_64);
            wire add_neg_ovf = (sum_64 < INT32_MIN_64);

            wire signed [31:0] add_result;
            assign add_result = add_pos_ovf ? 32'h7FFFFFFF :
                                add_neg_ovf ? 32'h80000000 :
                                              sum_64[31:0];

            // ---- MUL: 64-bit intermediate with saturation -------------------
            // Same issue: multiply in 32 bits would truncate the full product.
            // Explicitly widen to 64 bits first.
            wire signed [63:0] prod_64 = a_se * b_se;    // 64-bit mult, full product

            wire mul_pos_ovf = (prod_64 > INT32_MAX_64);
            wire mul_neg_ovf = (prod_64 < INT32_MIN_64);

            wire signed [31:0] mul_result;
            assign mul_result = mul_pos_ovf ? 32'h7FFFFFFF :
                                mul_neg_ovf ? 32'h80000000 :
                                              prod_64[31:0];

            // ---- MAX: pairwise element-wise ---------------------------------
            wire signed [31:0] max_result;
            assign max_result = (a_lane > b_lane) ? a_lane : b_lane;

            // ---- PASS_A: feed-through ---------------------------------------
            wire signed [31:0] pass_result;
            assign pass_result = a_lane;

            // ---- Op-multiplexed datapath result ----------------------------
            wire signed [31:0] op_result;
            assign op_result = (op == OP_ADD)    ? add_result :
                               (op == OP_MUL)    ? mul_result :
                               (op == OP_MAX)    ? max_result :
                                                   pass_result;  // OP_PASS_A

            // ---- Lane-mask gating ------------------------------------------
            // Disabled lanes:
            //   ADD     → feed-through a_lane
            //   MUL/MAX → 0
            //   PASS_A  → a_lane (same as feed-through)
            wire signed [31:0] lane_result;
            assign lane_result = mask ? op_result :
                                 ((op == OP_MUL) || (op == OP_MAX)) ? 32'sd0 :
                                                                      a_lane;

            // ---- Pipeline register (1 cycle) --------------------------------
            reg signed [31:0] lane_result_r;

            always @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    lane_result_r <= 32'd0;
                else
                    lane_result_r <= lane_result;
            end

            // ---- Drive output slice -----------------------------------------
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
