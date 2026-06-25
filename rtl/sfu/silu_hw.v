// silu_hw.v — 4-stage SiLU pipeline (x * sigmoid(x))
//
// Reuses the shared Task-1 exp_lut module (CaduceusCore/rtl/sfu/exp_lut.v).
//
// Algorithm:
//   sigmoid(x) = 1 / (1 + exp(-x))
//   For x >= 0:  sigmoid = 1        / (1 + exp(-x))
//   For x <  0:  sigmoid = exp(x)   / (1 + exp(x))
//   Because exp(-x) = exp(-|x|) for both signs, a single LUT lookup of
//   exp(-|x|) feeds a sign-aware numerator select and a fixed-point reciprocal.
//   SiLU output = x * sigmoid(x).
//
// Fixed-point formats:
//   x                  : signed Q19.12 (FP16 normal numbers only, subnormals flushed)
//   exp_lut output     : unsigned Q8.4 (reused shared LUT)
//   denominator        : unsigned Q8.4  (= 1.0 + exp(-|x|), raw in [16, 32])
//   reciprocal / y     : unsigned Q0.16
//   sigmoid            : unsigned Q0.16
//   final product      : signed Q19.28
//
// Division is performed with 3 Newton-Raphson iterations seeded by the linear
// approximation y0 = 1.5 - 0.5*d, all in Q0.16 fixed-point.
//
// Pipeline stages (4-cycle latency):
//   S1: FP16 -> fixed, exp LUT address + lookup
//   S2: denominator, numerator select, reciprocal seed
//   S3: Newton-Raphson iteration 1
//   S4: Newton-Raphson iterations 2+3, sigmoid, final FP16 multiply
//
// Interface:
//   data_i[15:0] : FP16 input element
//   valid_i      : input valid (one element per cycle, no back-pressure)
//   data_o[15:0] : FP16 SiLU output
//   valid_o      : output valid (4-cycle latency)
//
// NOTE: This module instantiates the existing shared exp_lut (Q8.4, 4 fraction
// bits). The coarse quantization may not satisfy the compare_rtl float16
// tolerance over the full [-10, 10] range; if so, this is documented in
// .omo/notepads/sfu-vector-phase2/issues.md per the task instructions.

`timescale 1ns / 1ps

module silu_hw (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [15:0] data_i,    // FP16 input
    input  wire        valid_i,
    output reg  [15:0] data_o,    // FP16 output
    output reg         valid_o
);

    // ── fixed-point parameters ──────────────────────────────────────
    localparam X_FRAC   = 12;               // fraction bits for x fixed-point
    localparam E_ONE    = 12'd16;           // 1.0 in Q8.4
    localparam SIG_FRAC = 16;               // sigmoid / reciprocal fraction bits
    localparam P_FRAC   = X_FRAC + SIG_FRAC;// final product fraction bits
    localparam [5:0] P_FRAC_W = 6'd28;      // width-safe constant for converter

    localparam signed [31:0] XMIN_FIXED  = -32'sd20 <<< X_FRAC; // -20 in Q19.12
    localparam        [31:0] RANGE_FIXED =  32'd20  <<< X_FRAC; //  20 in Q19.12

    // ── helper functions ────────────────────────────────────────────

    // Count leading zeros of a 64-bit unsigned value
    function automatic [6:0] clz64;
        input [63:0] x;
        integer i;
        begin
            clz64 = 7'd64;
            for (i = 63; i >= 0; i = i - 1) begin
                if (x[i] && (clz64 == 7'd64))
                    clz64 = 7'd63 - i[6:0];
            end
        end
    endfunction

    // Convert IEEE-754 FP16 to signed fixed-point with `fb` fraction bits.
    function automatic signed [31:0] fp16_to_fixed;
        input [15:0] fp;
        input [4:0]  fb;
        reg sign;
        reg [4:0] exp;
        reg [9:0] mant;
        reg signed [31:0] val;
        reg signed [31:0] shift;
        begin
            sign = fp[15];
            exp  = fp[14:10];
            mant = fp[9:0];
            if (exp == 5'd0) begin
                fp16_to_fixed = 32'sd0;
            end else begin
                val = (32'sd1 << 10) | mant;
                if (sign)
                    val = -val;
                shift = exp - 5'd15 - 5'd10 + fb;
                if (shift >= 0)
                    fp16_to_fixed = val <<< shift;
                else
                    fp16_to_fixed = val >>> (-shift);
            end
        end
    endfunction

    // Convert signed fixed-point (value = val / 2^fb) to FP16.
    function automatic [15:0] fixed_s64_to_fp16;
        input signed [63:0] val;
        input [5:0]         fb;
        reg        sign;
        reg [63:0] absv;
        reg [6:0]  lz;
        reg [5:0]  msb;
        reg [63:0] norm;
                reg signed [5:0] exp_i;
                reg [10:0] mant;
                reg        round_bit;
                begin
                    if (val == 64'sd0) begin
                        fixed_s64_to_fp16 = 16'h0000;
                    end else begin
                        sign = val[63];
                        absv = sign ? -val : val;
                        lz   = clz64(absv);
                        msb  = 6'd63 - lz[5:0];
                        norm = absv << (63 - msb);
                        exp_i = $signed(msb) - $signed(fb) + 6'sd15;
                        mant  = norm[62:53];
                        round_bit = norm[52];
                        if (round_bit)
                            mant = mant + 11'd1;
                        if (mant[10]) begin
                            mant  = mant >> 1;
                            exp_i = exp_i + 6'sd1;
                        end
                        if (exp_i >= 6'sd31)
                            fixed_s64_to_fp16 = sign ? 16'hFC00 : 16'h7C00;
                        else if (exp_i <= 6'sd0)
                            fixed_s64_to_fp16 = 16'h0000;
                        else
                            fixed_s64_to_fp16 = {sign, exp_i[4:0], mant[9:0]};
                    end
                end
    endfunction

    // Newton-Raphson reciprocal iteration: y' = y * (2 - D*y) in Q0.16.
    // D is the denominator scaled to Q0.16 (D = denom_raw << 12).
    function automatic [16:0] nr_iter;
        input [17:0] D;
        input [16:0] Y;
        reg [33:0] dy_full;
        reg [17:0] dy;
        reg [17:0] two_minus;
        reg [34:0] prod;
        begin
            dy_full   = D * Y;
            dy        = dy_full[33:16];            // D*y in Q0.16
            two_minus = (18'd2 << 16) - dy;        // 2.0 - D*y
            prod      = Y * two_minus;
            nr_iter   = prod[33:16];               // y * (2 - D*y)
        end
    endfunction

    // ── shared exp_lut instantiation ────────────────────────────────
    wire [7:0]  exp_lut_addr;
    wire [11:0] exp_lut_out;

    exp_lut u_exp_lut (
        .clk    (clk),
        .rst_n  (rst_n),
        .addr   (exp_lut_addr),
        .lut_out(exp_lut_out)
    );

    // ── pipeline registers ──────────────────────────────────────────

    // Stage 1 (input conversion + exp LUT lookup)
    reg        s1_valid;
    reg        s1_sign;
    reg signed [31:0] s1_x_fixed;
    reg [11:0] s1_e_raw;

    // Stage 2 (denominator + reciprocal seed)
    reg        s2_valid;
    reg        s2_sign;
    reg signed [31:0] s2_x_fixed;
    reg [11:0] s2_num_raw;
    reg [11:0] s2_denom_raw;
    reg [17:0] s2_D;
    reg [16:0] s2_y;

    // Stage 3 (Newton-Raphson iteration 1)
    reg        s3_valid;
    reg        s3_sign;
    reg signed [31:0] s3_x_fixed;
    reg [11:0] s3_num_raw;
    reg [11:0] s3_denom_raw;
    reg [17:0] s3_D;
    reg [16:0] s3_y;

    // ── combinational stage logic ───────────────────────────────────
    reg        s1_valid_next, s2_valid_next, s3_valid_next;
    reg        s1_sign_next,  s2_sign_next,  s3_sign_next;
    reg signed [31:0] s1_x_fixed_next, s2_x_fixed_next, s3_x_fixed_next;
    reg [11:0] s1_e_raw_next;
    reg [7:0]  s1_addr_next;
    reg [11:0] s2_num_raw_next, s2_denom_raw_next;
    reg [17:0] s2_D_next;
    reg [16:0] s2_y_next;
    reg [11:0] s3_num_raw_next, s3_denom_raw_next;
    reg [17:0] s3_D_next;
    reg [16:0] s3_y_next;
    reg [15:0] data_o_next;
    reg        valid_o_next;

    reg signed [31:0] x_abs_fixed;
    reg signed [31:0] x_neg_fixed;
    reg signed [31:0] delta;
    reg [31:0] idx_full;
    reg [16:0] y_iter2;
    reg [16:0] y_iter3;
    reg [16:0] sig_raw;
    reg signed [63:0] prod;

    always @(*) begin
        // Stage 1: input conversion and exp LUT address
        s1_valid_next   = valid_i;
        s1_sign_next    = data_i[15];
        s1_x_fixed_next = fp16_to_fixed(data_i, X_FRAC);

        x_abs_fixed = s1_x_fixed_next[31] ? (-s1_x_fixed_next) : s1_x_fixed_next;
        x_neg_fixed = -x_abs_fixed;

        delta = x_neg_fixed - XMIN_FIXED;
        if (delta < 32'sd0)
            delta = 32'sd0;
        else if (delta > $signed(RANGE_FIXED))
            delta = $signed(RANGE_FIXED);

        idx_full      = ($unsigned(delta) * 32'd255) / RANGE_FIXED;
        s1_addr_next  = idx_full[7:0];
        s1_e_raw_next = exp_lut_out;

        // Stage 2: denominator and seed reciprocal
        s2_valid_next     = s1_valid;
        s2_sign_next      = s1_sign;
        s2_x_fixed_next   = s1_x_fixed;
        s2_denom_raw_next = E_ONE + s1_e_raw;
        s2_num_raw_next   = s1_sign ? s1_e_raw : E_ONE;
        s2_D_next         = {1'b0, s2_denom_raw_next} << 12;
        s2_y_next         = (17'd3 << 15) - (s2_D_next >> 1);

        // Stage 3: Newton-Raphson iteration 1
        s3_valid_next     = s2_valid;
        s3_sign_next      = s2_sign;
        s3_x_fixed_next   = s2_x_fixed;
        s3_num_raw_next   = s2_num_raw;
        s3_denom_raw_next = s2_denom_raw;
        s3_D_next         = s2_D;
        s3_y_next         = nr_iter(s2_D, s2_y);

        // Stage 4 (output): NR2 + NR3 + sigmoid + final multiply
        y_iter2 = nr_iter(s3_D, s3_y);
        y_iter3 = nr_iter(s3_D, y_iter2);
        if (s3_sign == 1'b0)
            sig_raw = y_iter3;
        else
            sig_raw = (s3_num_raw * y_iter3) >> 4;

        prod        = $signed(s3_x_fixed) * $signed({47'b0, sig_raw});
        data_o_next = fixed_s64_to_fp16(prod, P_FRAC_W);
        valid_o_next= s3_valid;
    end

    assign exp_lut_addr = s1_addr_next;

    // ── sequential update ───────────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s1_valid   <= 1'b0;
            s1_sign    <= 1'b0;
            s1_x_fixed <= 32'sd0;
            s1_e_raw   <= 12'd0;

            s2_valid     <= 1'b0;
            s2_sign      <= 1'b0;
            s2_x_fixed   <= 32'sd0;
            s2_num_raw   <= 12'd0;
            s2_denom_raw <= 12'd0;
            s2_D         <= 18'd0;
            s2_y         <= 17'd0;

            s3_valid     <= 1'b0;
            s3_sign      <= 1'b0;
            s3_x_fixed   <= 32'sd0;
            s3_num_raw   <= 12'd0;
            s3_denom_raw <= 12'd0;
            s3_D         <= 18'd0;
            s3_y         <= 17'd0;

            data_o  <= 16'd0;
            valid_o <= 1'b0;
        end else begin
            s1_valid   <= s1_valid_next;
            s1_sign    <= s1_sign_next;
            s1_x_fixed <= s1_x_fixed_next;
            s1_e_raw   <= s1_e_raw_next;

            s2_valid     <= s2_valid_next;
            s2_sign      <= s2_sign_next;
            s2_x_fixed   <= s2_x_fixed_next;
            s2_num_raw   <= s2_num_raw_next;
            s2_denom_raw <= s2_denom_raw_next;
            s2_D         <= s2_D_next;
            s2_y         <= s2_y_next;

            s3_valid     <= s3_valid_next;
            s3_sign      <= s3_sign_next;
            s3_x_fixed   <= s3_x_fixed_next;
            s3_num_raw   <= s3_num_raw_next;
            s3_denom_raw <= s3_denom_raw_next;
            s3_D         <= s3_D_next;
            s3_y         <= s3_y_next;

            data_o  <= data_o_next;
            valid_o <= valid_o_next;
        end
    end

endmodule
