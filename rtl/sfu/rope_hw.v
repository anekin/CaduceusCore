//=============================================================================
// rope_hw — 12-stage pipelined CORDIC rotation for RoPE
//=============================================================================
// Implements a single (x, y) pair rotation by theta radians using a 12-stage
// iterative CORDIC.  Inputs and outputs are IEEE-754 FP16; all internal
// arithmetic is signed fixed-point Q18.14.
//
// Interface:
//   x_i[15:0], y_i[15:0] : FP16 vector pair to rotate
//   theta_i[15:0]        : FP16 rotation angle in radians
//   valid_i              : input valid
//   x_o[15:0], y_o[15:0] : rotated FP16 pair
//   valid_o              : asserted 12 cycles after valid_i
//
// Algorithm:
//   1. Convert x, y, theta from FP16 to Q18.14 fixed-point.
//   2. Reduce theta to [-pi/2, pi/2] using quadrant symmetry:
//        * fold to [-pi, pi]
//        * if outside [-pi/2, pi/2], add/subtract pi and set flip flag
//   3. Pre-scale (x, y) by CORDIC gain K = prod(cos(atan(2^-i))) ~= 0.607253
//      so the iterative pseudo-rotations converge to the correct magnitude.
//   4. Run 12 CORDIC iterations:
//        d  = +1 if z >= 0 else -1
//        x' = x - d * (y >>> i)
//        y' = y + d * (x >>> i)
//        z' = z - d * atan(2^-i)
//   5. If flip was set, negate the final (x, y).
//   6. Convert back to FP16.
//
// Pipeline:
//   The module is 12 cycles deep.  Stage 0 performs FP16->fixed conversion,
//   quadrant reduction, gain scaling, and CORDIC iteration 0.  Stages 1..11
//   each perform one additional CORDIC iteration.  The output combinational
//   logic applies the quadrant flip and converts back to FP16.
//=============================================================================

`timescale 1ns / 1ps

module rope_hw (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [15:0] x_i,
    input  wire [15:0] y_i,
    input  wire [15:0] theta_i,
    input  wire        valid_i,
    output wire [15:0] x_o,
    output wire [15:0] y_o,
    output wire        valid_o
);

    //-------------------------------------------------------------------------
    // Fixed-point format: signed Q18.14
    //-------------------------------------------------------------------------
    localparam FXW          = 32;
    localparam FRAC         = 14;
    localparam STAGES       = 16;

    // CORDIC gain K for 16 iterations, scaled by 2^14
    localparam signed [FXW-1:0] CORDIC_GAIN = 32'sd9949;

    // atan(2^-i) for i = 0..15, scaled by 2^14
    localparam signed [FXW-1:0] CORDIC_ANGLE [0:STAGES-1] = '{
        32'sd12868, 32'sd7596, 32'sd4014, 32'sd2037,
        32'sd1023, 32'sd512,  32'sd256,  32'sd128,
        32'sd64,   32'sd32,   32'sd16,   32'sd8,
        32'sd4,    32'sd2,    32'sd1,    32'sd0
    };

    // Angle constants in Q18.14
    localparam signed [FXW-1:0] PI      = 32'sd51472;   //  pi  * 2^14
    localparam signed [FXW-1:0] PI_HALF = 32'sd25736;   //  pi/2 * 2^14
    localparam signed [FXW-1:0] TWO_PI  = 32'sd102944;  // 2*pi * 2^14

    //-------------------------------------------------------------------------
    // Fixed-point helpers
    //-------------------------------------------------------------------------

    // Convert IEEE-754 FP16 to signed fixed-point Q18.14.
    // Subnormal inputs are flushed to zero.
    function automatic signed [FXW-1:0] fp16_to_fixed;
        input [15:0] fp;
        reg sign;
        reg [4:0]  exp;
        reg [9:0]  mant;
        reg [10:0] sig;
        integer    shift;
        begin
            if ((fp & 16'h7FFF) == 16'h0000) begin
                fp16_to_fixed = 0;
            end else begin
                sign = fp[15];
                exp  = fp[14:10];
                mant = fp[9:0];
                if (exp == 5'd0) begin
                    // Subnormal — flush to zero for this design
                    fp16_to_fixed = 0;
                end else begin
                    sig = {1'b1, mant}; // 1.mant
                    // true value = sig * 2^(exp - 15 - 10)
                    // fixed value = true value * 2^FRAC
                    //             = sig << (exp - 25 + FRAC)
                    shift = $signed({1'b0, exp}) - 25 + FRAC;
                    if (shift >= 0) begin
                        fp16_to_fixed = sign
                            ? -$signed({{(FXW-11){1'b0}}, sig} << shift)
                            :  $signed({{(FXW-11){1'b0}}, sig} << shift);
                    end else begin
                        fp16_to_fixed = sign
                            ? -$signed(($signed({{(FXW-11){1'b0}}, sig})
                                         + (1 << (-shift - 1))) >> (-shift))
                            :  $signed(($signed({{(FXW-11){1'b0}}, sig})
                                         + (1 << (-shift - 1))) >> (-shift));
                    end
                end
            end
        end
    endfunction

    // Convert signed fixed-point Q18.14 to IEEE-754 FP16 with round-half-up.
    // Values outside FP16 range become infinity.
    function automatic [15:0] fixed_to_fp16;
        input signed [FXW-1:0] f;
        reg        sign;
        reg [FXW-1:0] abs_f;
        integer    w, e, exp, shift;
        reg [9:0]  mant;
        reg [FXW-1:0] round_bits;
        integer    half;
        begin
            if (f == 0) begin
                fixed_to_fp16 = 16'h0000;
            end else begin
                sign  = (f < 0);
                abs_f = sign ? -$signed(f) : f;
                // $clog2 returns ceil(log2(n)); for exact powers of two we need
                // one extra bit to account for the leading 1.
                w     = ((abs_f & (abs_f - 1)) == 0) ? ($clog2(abs_f) + 1)
                                                     : $clog2(abs_f);
                e     = w - 1 - FRAC;       // unbiased exponent of true value

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
                                // Rounding overflow wrapped mantissa back to 0
                                exp = exp + 1;
                            end
                        end
                    end else if (shift == 0) begin
                        mant = abs_f[9:0];
                    end else begin
                        // Should only occur for tiny normal-range values;
                        // treat as a subnormal encoding.
                        mant = (abs_f << (-shift)) & 10'h3FF;
                    end

                    if (exp >= 31)
                        fixed_to_fp16 = {sign, 5'h1F, 10'h000}; // inf
                    else
                        fixed_to_fp16 = {sign, exp[4:0], mant};
                end else begin
                    // Subnormal FP16: value = |f| / 2^FRAC = mant * 2^(-24)
                    shift = 24 - FRAC;
                    if (shift >= 0)
                        mant = (abs_f << shift) & 10'h3FF;
                    else
                        mant = (abs_f >> (-shift)) & 10'h3FF;
                    fixed_to_fp16 = {sign, 5'h00, mant};
                end
            end
        end
    endfunction

    //-------------------------------------------------------------------------
    // Combinational input preprocessing
    //-------------------------------------------------------------------------
    wire signed [FXW-1:0] x_fixed;
    wire signed [FXW-1:0] y_fixed;
    wire signed [FXW-1:0] theta_fixed;

    assign x_fixed     = fp16_to_fixed(x_i);
    assign y_fixed     = fp16_to_fixed(y_i);
    assign theta_fixed = fp16_to_fixed(theta_i);

    // Reduce theta to [-pi, pi] using modulo 2*pi.
    // Verilog signed division truncates toward zero, so the remainder may be
    // negative; add 2*pi to bring it into [0, 2*pi).
    wire signed [FXW-1:0] theta_quo;
    wire signed [FXW-1:0] theta_rem;
    wire signed [FXW-1:0] theta_pos;

    assign theta_quo = theta_fixed / TWO_PI;
    assign theta_rem = theta_fixed - theta_quo * TWO_PI;
    assign theta_pos = (theta_rem < 0) ? theta_rem + TWO_PI : theta_rem;

    // Fold from [0, 2*pi) to [-pi, pi]
    wire signed [FXW-1:0] theta_pm_pi;
    assign theta_pm_pi = (theta_pos > PI) ? theta_pos - TWO_PI : theta_pos;

    // Reduce to [-pi/2, pi/2], set flip flag for angles that crossed pi
    reg  signed [FXW-1:0] theta_reduced;
    reg                   flip_pre;

    always @(*) begin
        if (theta_pm_pi > PI_HALF) begin
            theta_reduced = theta_pm_pi - PI;
            flip_pre      = 1'b1;
        end else if (theta_pm_pi < -PI_HALF) begin
            theta_reduced = theta_pm_pi + PI;
            flip_pre      = 1'b1;
        end else begin
            theta_reduced = theta_pm_pi;
            flip_pre      = 1'b0;
        end
    end

    // Pre-scale by CORDIC gain K.  Use 64-bit intermediate to avoid overflow.
    wire signed [63:0] x_scaled_64 = ($signed(x_fixed) * $signed(CORDIC_GAIN)) >>> FRAC;
    wire signed [63:0] y_scaled_64 = ($signed(y_fixed) * $signed(CORDIC_GAIN)) >>> FRAC;

    wire signed [FXW-1:0] x_scaled = x_scaled_64[FXW-1:0];
    wire signed [FXW-1:0] y_scaled = y_scaled_64[FXW-1:0];

    //-------------------------------------------------------------------------
    // CORDIC iteration 0 combinational logic (absorbed into stage 0)
    //-------------------------------------------------------------------------
    wire                  d0 = (theta_reduced >= 0);
    wire signed [FXW-1:0] x_iter0 = d0 ? (x_scaled - y_scaled) : (x_scaled + y_scaled);
    wire signed [FXW-1:0] y_iter0 = d0 ? (y_scaled + x_scaled) : (y_scaled - x_scaled);
    wire signed [FXW-1:0] z_iter0 = d0 ? (theta_reduced - CORDIC_ANGLE[0])
                                       : (theta_reduced + CORDIC_ANGLE[0]);

    //-------------------------------------------------------------------------
    // Pipeline registers
    //-------------------------------------------------------------------------
    reg signed [FXW-1:0] s_x    [0:STAGES-1];
    reg signed [FXW-1:0] s_y    [0:STAGES-1];
    reg signed [FXW-1:0] s_z    [0:STAGES-1];
    reg                  s_flip [0:STAGES-1];
    reg                  s_valid[0:STAGES-1];

    // Combinational next-state for stages 1..STAGES-1
    wire signed [FXW-1:0] s_x_next [1:STAGES-1];
    wire signed [FXW-1:0] s_y_next [1:STAGES-1];
    wire signed [FXW-1:0] s_z_next [1:STAGES-1];

    genvar gi;
    generate
        for (gi = 1; gi < STAGES; gi = gi + 1) begin : cordic_stage
            wire d = (s_z[gi-1] >= 0);
            assign s_x_next[gi] = d ? (s_x[gi-1] - (s_y[gi-1] >>> gi))
                                    : (s_x[gi-1] + (s_y[gi-1] >>> gi));
            assign s_y_next[gi] = d ? (s_y[gi-1] + (s_x[gi-1] >>> gi))
                                    : (s_y[gi-1] - (s_x[gi-1] >>> gi));
            assign s_z_next[gi] = d ? (s_z[gi-1] - CORDIC_ANGLE[gi])
                                    : (s_z[gi-1] + CORDIC_ANGLE[gi]);
        end
    endgenerate

    integer pi;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (pi = 0; pi < STAGES; pi = pi + 1) begin
                s_x[pi]     <= {FXW{1'b0}};
                s_y[pi]     <= {FXW{1'b0}};
                s_z[pi]     <= {FXW{1'b0}};
                s_flip[pi]  <= 1'b0;
                s_valid[pi] <= 1'b0;
            end
        end else begin
            // Stage 0: preprocessing + CORDIC iteration 0
            s_x[0]     <= x_iter0;
            s_y[0]     <= y_iter0;
            s_z[0]     <= z_iter0;
            s_flip[0]  <= flip_pre;
            s_valid[0] <= valid_i;

            // Stages 1..STAGES-1: remaining CORDIC iterations
            for (pi = 1; pi < STAGES; pi = pi + 1) begin
                s_x[pi]     <= s_x_next[pi];
                s_y[pi]     <= s_y_next[pi];
                s_z[pi]     <= s_z_next[pi];
                s_flip[pi]  <= s_flip[pi-1];
                s_valid[pi] <= s_valid[pi-1];
            end
        end
    end

    //-------------------------------------------------------------------------
    // Output combinational: quadrant flip + fixed->FP16
    //-------------------------------------------------------------------------
    wire signed [FXW-1:0] x_final_fixed = s_flip[STAGES-1] ? -s_x[STAGES-1] : s_x[STAGES-1];
    wire signed [FXW-1:0] y_final_fixed = s_flip[STAGES-1] ? -s_y[STAGES-1] : s_y[STAGES-1];

    assign x_o     = fixed_to_fp16(x_final_fixed);
    assign y_o     = fixed_to_fp16(y_final_fixed);
    assign valid_o = s_valid[STAGES-1];

endmodule
