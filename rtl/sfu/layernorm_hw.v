//=============================================================================
// layernorm_hw — 6-stage fixed-point LayerNorm pipeline
//=============================================================================
// Implements: data_o = (data_i - mean) / sqrt(var + eps)
//
// Data format:
//   - Input / output: IEEE-754 FP16 (data_i[15:0], data_o[15:0])
//   - Internal arithmetic: signed fixed-point Q(32-FRAC).FRAC
//   - eps = 1e-5 (hard-coded as a fixed-point constant)
//
// Control flow (the six algorithmic stages):
//   1. PASS1_SUM      — accumulate sum while storing x into internal SRAM
//   2. COMPUTE_MEAN   — mean = round(sum / N)
//   3. PASS2_SUB_SQ   — replay x, compute (x-mean), store it, accumulate squares
//   4. COMPUTE_VAR    — var = round(sum((x-mean)^2) / N)
//   5. SQRT_LOOP      — integer Newton-Raphson sqrt of (var+eps)
//   6. PASS3_NORM     — replay (x-mean) and divide by sqrt(var+eps)
//
// Interface:
//   data_i[15:0] + valid_i are consumed one element per cycle.
//   last_i is asserted on the final element of a vector and triggers passes 2-6.
//   data_o[15:0] + valid_o are produced one element per cycle.
//
// Corner cases:
//   - N == 1          : (x-mean) == 0  => output is forced to 0.
//   - all-equal input : var == 0, (x-mean) == 0 => output is forced to 0.
//   - Tiny var + eps  : eps is always added before sqrt so division is stable.
//
// Limitations (documented in notepad):
//   - FP16 subnormals are flushed to zero on input.
//   - Internal accumulator is 64-bit; full FP16 dynamic range can overflow.
//     Designed for typical transformer hidden-state magnitudes.
//=============================================================================

`timescale 1ns / 1ps

module layernorm_hw (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [15:0] data_i,
    input  wire        valid_i,
    input  wire        last_i,
    output reg  [15:0] data_o,
    output reg         valid_o
);

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam MAX_LEN      = 4096;         // maximum vector length
    localparam LN           = 12;           // $clog2(MAX_LEN)
    localparam FRAC         = 18;           // fixed-point fractional bits
    localparam FXW          = 32;           // fixed-point word width
    // eps = 1e-5 = 1/100000, rounded to the chosen fixed-point scale.
    localparam EPS_FX       = ((1 << FRAC) + (100000 / 2)) / 100000;
    localparam SQRT_ITER    = 12;           // Newton-Raphson sqrt iterations

    //=========================================================================
    // Internal memories and accumulators
    //=========================================================================
    // One dual-port logical view implemented as a register array.  We never
    // read and write different addresses simultaneously in the same pass, so
    // a single-port array is sufficient.
    reg signed [FXW-1:0] dx_buf [0:MAX_LEN-1];

    reg signed [63:0] sum_acc;   // pass-1 sum accumulator
    reg signed [63:0] sq_acc;    // pass-2 square accumulator

    reg [LN:0]   n;              // vector length captured at last_i (needs LN+1 bits for MAX_LEN)
    reg [LN-1:0] cnt;            // shared address / element counter

    reg signed [FXW-1:0] mean_f; // fixed-point mean
    reg signed [FXW-1:0] dxf;    // current (x - mean)

    // sqrt working registers
    reg [63:0] sqrt_y;
    reg [31:0] sqrt_r;
    reg [3:0]  sqrt_iter;

    //=========================================================================
    // FSM
    //=========================================================================
    localparam [2:0] ST_IDLE         = 3'd0,
                     ST_PASS1_SUM    = 3'd1,
                     ST_COMPUTE_MEAN = 3'd2,
                     ST_PASS2_SUB_SQ = 3'd3,
                     ST_COMPUTE_VAR  = 3'd4,
                     ST_SQRT_LOOP    = 3'd5,
                     ST_PASS3_NORM   = 3'd6,
                     ST_DONE         = 3'd7;

    reg [2:0] state;

    //=========================================================================
    // Fixed-point helpers
    //=========================================================================

    // Convert IEEE-754 FP16 to signed fixed-point Q(32-FRAC).FRAC.
    // Subnormal inputs are flushed to zero.
    function automatic signed [FXW-1:0] fp16_to_fixed;
        input [15:0] a;
        reg sign;
        reg [4:0]  exp;
        reg [9:0]  mant;
        reg [10:0] sig;
        integer    shift;
        begin
            if ((a & 16'h7FFF) == 16'h0000) begin
                fp16_to_fixed = 0;
            end else begin
                sign = a[15];
                exp  = a[14:10];
                mant = a[9:0];
                if (exp == 5'd0) begin
                    // Subnormal / denormal — flush to zero for this design
                    fp16_to_fixed = 0;
                end else begin
                    sig = {1'b1, mant}; // 1.mant
                    // true value = sig * 2^(exp - 15 - 10)
                    // fixed value = true value * 2^FRAC
                    //             = sig << (exp - 25 + FRAC)
                    // Extend sig to the full fixed-point width before shifting
                    shift = $signed({1'b0, exp}) - 25 + FRAC;
                    if (shift >= 0) begin
                        fp16_to_fixed = sign
                            ? -$signed({{(FXW-11){1'b0}}, sig} << shift)
                            :  $signed({{(FXW-11){1'b0}}, sig} << shift);
                    end else begin
                        fp16_to_fixed = sign
                            ? -$signed(($signed({{(FXW-11){1'b0}}, sig}) + (1 << (-shift - 1))) >> (-shift))
                            :  $signed(($signed({{(FXW-11){1'b0}}, sig}) + (1 << (-shift - 1))) >> (-shift));
                    end
                end
            end
        end
    endfunction

    // Convert signed fixed-point Q(32-FRAC).FRAC to IEEE-754 FP16 with
    // round-to-nearest-even.  Values outside FP16 range become infinity.
    function automatic [15:0] fixed_to_fp16;
        input signed [FXW-1:0] f;
        reg        sign;
        reg [FXW-1:0] abs_f;
        integer    w, e, exp, shift, s;
        reg [9:0]  mant;
        reg [FXW-1:0] round_bits;
        integer    half;
        begin
            if (f == 0) begin
                fixed_to_fp16 = 16'h0000;
            end else begin
                sign  = (f < 0);
                abs_f = sign ? -$signed(f) : f;
                w     = $clog2(abs_f);      // bit-length of |f|
                e     = w - 1 - FRAC;       // unbiased exponent of true value

                if (e >= -14) begin
                    exp   = e + 15;
                    shift = w - 11;

                    if (shift > 0) begin
                        mant       = (abs_f >> shift) & 10'h3FF;
                        round_bits = abs_f & ((1 << shift) - 1);
                        half       = 1 << (shift - 1);
                        if ((round_bits > half) ||
                            ((round_bits == half) && (mant[0] == 1'b1))) begin
                            mant = mant + 1;
                            if (mant == 10'd0) begin
                                // Overflow wrapped mantissa back to 0
                                exp = exp + 1;
                            end
                        end
                        // Exact powers of two have no mantissa bits and no
                        // round bits; the implicit leading 1 is one position
                        // too low, so bump the exponent to normalize correctly.
                        if ((mant == 10'd0) && (round_bits == 0)) begin
                            exp = exp + 1;
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
                    s    = 24 - FRAC;
                    if (s >= 0)
                        mant = (abs_f << s) & 10'h3FF;
                    else
                        mant = (abs_f >> (-s)) & 10'h3FF;
                    fixed_to_fp16 = {sign, 5'h00, mant};
                end
            end
        end
    endfunction

    // Signed round-to-nearest integer division by a positive divisor.
    function automatic signed [FXW-1:0] round_div;
        input signed [63:0] a;
        input [31:0]        b;
        reg signed [63:0] q, r;
        reg signed [63:0] b_ext;
        begin
            b_ext = $signed({1'b0, b});
            q = a / b_ext;
            r = a - q * b_ext;
            if (($signed(2) * r) >= b_ext)
                q = q + 1;
            else if (($signed(2) * r) <= -b_ext)
                q = q - 1;
            round_div = q[FXW-1:0];
        end
    endfunction

    //=========================================================================
    // Sequential logic
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state    <= ST_IDLE;
            valid_o  <= 1'b0;
            data_o   <= 16'h0000;
            sum_acc  <= 64'd0;
            sq_acc   <= 64'd0;
            n        <= {LN{1'b0}};
            cnt      <= {LN{1'b0}};
            mean_f   <= {FXW{1'b0}};
            dxf      <= {FXW{1'b0}};
            sqrt_y   <= 64'd0;
            sqrt_r   <= 32'd1;
            sqrt_iter<= 4'd0;
        end else begin
            valid_o <= 1'b0; // default

            case (state)
                //--------------------------------------------------------------
                // ST_IDLE / ST_PASS1_SUM: accumulate sum, store original x
                //--------------------------------------------------------------
                ST_IDLE: begin
                    if (valid_i) begin
                        sum_acc <= 64'd0;
                        sq_acc  <= 64'd0;
                        cnt     <= {LN{1'b0}};
                        sum_acc <= $signed(fp16_to_fixed(data_i));
                        dx_buf[0] <= fp16_to_fixed(data_i);
                        if (last_i) begin
                            n <= 13'd1;
                            state <= ST_COMPUTE_MEAN;
                        end else begin
                            state <= ST_PASS1_SUM;
                            cnt <= 1;
                        end
                    end
                end

                ST_PASS1_SUM: begin
                    if (valid_i) begin
                        sum_acc <= sum_acc + $signed(fp16_to_fixed(data_i));
                        dx_buf[cnt] <= fp16_to_fixed(data_i);
                        if (last_i) begin
                            n     <= {1'b0, cnt} + 1;
                            cnt   <= {LN{1'b0}};
                            state <= ST_COMPUTE_MEAN;
                        end else begin
                            cnt <= cnt + 1;
                        end
                    end
                end

                //--------------------------------------------------------------
                // ST_COMPUTE_MEAN: mean = round(sum / N)
                //--------------------------------------------------------------
                ST_COMPUTE_MEAN: begin
                    mean_f <= round_div(sum_acc, n);
                    sq_acc <= 64'd0;
                    cnt    <= {LN{1'b0}};
                    state  <= ST_PASS2_SUB_SQ;
                end

                //--------------------------------------------------------------
                // ST_PASS2_SUB_SQ: subtract mean, store dx, accumulate dx^2
                //--------------------------------------------------------------
                ST_PASS2_SUB_SQ: begin
                    dxf    <= dx_buf[cnt] - mean_f;
                    dx_buf[cnt] <= dx_buf[cnt] - mean_f;
                    sq_acc <= sq_acc + ($signed(dx_buf[cnt] - mean_f) *
                                        $signed(dx_buf[cnt] - mean_f));
                    if (cnt == n - 1) begin
                        cnt   <= {LN{1'b0}};
                        state <= ST_COMPUTE_VAR;
                    end else begin
                        cnt <= cnt + 1;
                    end
                end

                //--------------------------------------------------------------
                // ST_COMPUTE_VAR: var = round(sum(dx^2) / N), add eps, init sqrt
                //--------------------------------------------------------------
                ST_COMPUTE_VAR: begin
                    begin
                        reg signed [63:0] q;
                        reg signed [FXW-1:0] var_f;
                        reg [63:0] S;
                        q = sq_acc / $signed({1'b0, n});
                        var_f = (q + (1 << (FRAC - 1))) >>> FRAC;
                        S = ($signed(var_f) + $signed(EPS_FX));
                        if ($signed(S) <= 0) begin
                            sqrt_r <= 32'd1;
                            state  <= ST_PASS3_NORM;
                        end else begin
                            sqrt_y <= S << FRAC;
                            sqrt_r <= 32'd1 << (($clog2(S << FRAC) + 1) >> 1);
                            sqrt_iter <= 4'd0;
                            state     <= ST_SQRT_LOOP;
                        end
                    end
                end

                //--------------------------------------------------------------
                // ST_SQRT_LOOP: integer Newton-Raphson sqrt
                //--------------------------------------------------------------
                ST_SQRT_LOOP: begin
                    sqrt_r <= (sqrt_r + (sqrt_y / sqrt_r)) >> 1;
                    if (sqrt_iter == SQRT_ITER - 1)
                        state <= ST_PASS3_NORM;
                    else
                        sqrt_iter <= sqrt_iter + 1;
                end

                //--------------------------------------------------------------
                // ST_PASS3_NORM: output = round(dx * 2^FRAC / sqrt(var+eps))
                //--------------------------------------------------------------
                ST_PASS3_NORM: begin
                    data_o  <= fixed_to_fp16(round_div($signed({{32{dx_buf[cnt][FXW-1]}}, dx_buf[cnt]}) <<< FRAC,
                                                       sqrt_r));
                    valid_o <= 1'b1;
                    if (cnt == n - 1) begin
                        cnt   <= {LN{1'b0}};
                        state <= ST_DONE;
                    end else begin
                        cnt <= cnt + 1;
                    end
                end

                //--------------------------------------------------------------
                // ST_DONE: single-cycle pulse, then wait for next vector
                //--------------------------------------------------------------
                ST_DONE: begin
                    state <= ST_IDLE;
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule
