//=============================================================================
// rmsnorm_hw — fixed-point two-pass RMSNorm pipeline
//=============================================================================
// Implements: data_o = data_i / sqrt(mean(data_i^2) + eps)
//
// Data format:
//   - Input / output: IEEE-754 FP16 (data_i[15:0], data_o[15:0])
//   - Internal arithmetic: signed fixed-point Q(32-FRAC).FRAC
//   - eps = 1e-5 (hard-coded as a fixed-point constant)
//
// Control flow (two algorithmic passes):
//   1. PASS1           — accumulate sum(x^2) while storing x into internal RAM
//   2. COMPUTE         — mean_sq = round(sum(x^2) / N), add eps, init sqrt
//   3. SQRT_LOOP       — Newton-Raphson sqrt of (mean_sq + eps)
//   4. RECIP_LOOP      — Newton-Raphson reciprocal of sqrt()
//   5. PASS2           — replay x and divide by sqrt(mean_sq + eps)
//
// Interface:
//   data_i[15:0] + valid_i are consumed one element per cycle.
//   last_i is asserted on the final element of a vector and triggers passes 2-5.
//   data_o[15:0] + valid_o are produced one element per cycle.
//
// Corner cases:
//   - N == 1          : output is forced to sign(x) (±1.0 or 0).
//   - all-zero input  : mean_sq == 0, eps prevents division-by-zero, output is 0.
//   - Tiny mean_sq    : eps is always added before sqrt so division is stable.
//
// Limitations (documented in notepad):
//   - FP16 subnormals are flushed to zero on input.
//   - Internal 64-bit accumulators can overflow for full-range FP16 inputs;
//     designed for typical transformer hidden-state magnitudes.
//=============================================================================

`timescale 1ns / 1ps

module rmsnorm_hw (
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
    // eps = 1e-5 rounded to the chosen fixed-point scale.
    localparam EPS_FX       = ((1 << FRAC) + (100000 / 2)) / 100000;
    localparam SQRT_ITER    = 8;            // Newton-Raphson sqrt iterations
    localparam RECIP_ITER   = 8;            // Newton-Raphson reciprocal iterations

    //=========================================================================
    // Internal memories and accumulators
    //=========================================================================
    // Single 4096-entry buffer stores the input vector as fixed-point x.
    reg signed [FXW-1:0] x_buf [0:MAX_LEN-1];

    reg signed [63:0] sq_acc;    // pass-1 sum(x^2) accumulator

    reg [LN:0]   n;              // captured vector length (needs LN+1 bits for MAX_LEN)
    reg [LN-1:0] cnt;            // shared address / element counter

    reg signed [FXW-1:0] mean_sq_f; // fixed-point mean(x^2)

    // sqrt working registers
    reg [63:0] sqrt_y;
    reg [63:0] sqrt_r;
    reg [3:0]  sqrt_iter;

    // reciprocal working registers
    reg [63:0] recip_d;
    reg [63:0] recip_y;
    reg [3:0]  recip_iter;

    //=========================================================================
    // FSM
    //=========================================================================
    localparam [2:0] ST_IDLE        = 3'd0,
                     ST_PASS1       = 3'd1,
                     ST_COMPUTE     = 3'd2,
                     ST_SQRT_LOOP   = 3'd3,
                     ST_INIT_RECIP  = 3'd4,
                     ST_RECIP_LOOP  = 3'd5,
                     ST_PASS2       = 3'd6,
                     ST_DONE        = 3'd7;

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

    // Signed round-to-nearest division by a positive divisor.
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
            state      <= ST_IDLE;
            valid_o    <= 1'b0;
            data_o     <= 16'h0000;
            sq_acc     <= 64'd0;
            n          <= {LN{1'b0}};
            cnt        <= {LN{1'b0}};
            mean_sq_f  <= {FXW{1'b0}};
            sqrt_y     <= 64'd0;
            sqrt_r     <= 64'd1;
            sqrt_iter  <= 4'd0;
            recip_d    <= 64'd1;
            recip_y    <= 64'd0;
            recip_iter <= 4'd0;
        end else begin
            valid_o <= 1'b0; // default

            case (state)
                //--------------------------------------------------------------
                // ST_IDLE / ST_PASS1: accumulate x^2, store x
                //--------------------------------------------------------------
                ST_IDLE: begin
                    if (valid_i) begin
                        sq_acc      <= 64'd0;
                        cnt         <= {LN{1'b0}};
                        x_buf[0]    <= fp16_to_fixed(data_i);
                        sq_acc      <= $signed(fp16_to_fixed(data_i)) *
                                       $signed(fp16_to_fixed(data_i));
                        if (last_i) begin
                            n     <= {{(LN-11){1'b0}}, 1'd1};
                            state <= ST_COMPUTE;
                        end else begin
                            state <= ST_PASS1;
                            cnt   <= 1;
                        end
                    end
                end

                ST_PASS1: begin
                    if (valid_i) begin
                        x_buf[cnt] <= fp16_to_fixed(data_i);
                        sq_acc     <= sq_acc + ($signed(fp16_to_fixed(data_i)) *
                                                $signed(fp16_to_fixed(data_i)));
                        if (last_i) begin
                            n     <= {1'b0, cnt} + 1;
                            cnt   <= {LN{1'b0}};
                            state <= ST_COMPUTE;
                        end else begin
                            cnt <= cnt + 1;
                        end
                    end
                end

                //--------------------------------------------------------------
                // ST_COMPUTE: mean_sq = round(sum(x^2)/N), add eps, init sqrt
                //--------------------------------------------------------------
                ST_COMPUTE: begin
                    begin
                        reg signed [63:0] q;
                        reg signed [FXW-1:0] msq_f;
                        reg [63:0] S;
                        q = sq_acc / $signed({1'b0, n});
                        msq_f = (q + (1 << (FRAC - 1))) >>> FRAC;
                        S = ($signed(msq_f) + $signed(EPS_FX));
                        mean_sq_f <= msq_f;
                        sqrt_y    <= S << FRAC;
                        sqrt_r    <= 64'd1 << (($clog2(S << FRAC) + 1) >> 1);
                        sqrt_iter <= 4'd0;
                        state     <= ST_SQRT_LOOP;
                    end
                end

                //--------------------------------------------------------------
                // ST_SQRT_LOOP: integer Newton-Raphson sqrt
                //--------------------------------------------------------------
                ST_SQRT_LOOP: begin
                    sqrt_r <= (sqrt_r + (sqrt_y / sqrt_r)) >> 1;
                    if (sqrt_iter == SQRT_ITER - 1)
                        state <= ST_INIT_RECIP;
                    else
                        sqrt_iter <= sqrt_iter + 1;
                end

                //--------------------------------------------------------------
                // ST_INIT_RECIP: seed reciprocal of sqrt_r
                //--------------------------------------------------------------
                ST_INIT_RECIP: begin
                    recip_d    <= sqrt_r;
                    recip_y    <= 64'd1 << (36 - $clog2(sqrt_r));
                    recip_iter <= 4'd0;
                    state      <= ST_RECIP_LOOP;
                end

                //--------------------------------------------------------------
                // ST_RECIP_LOOP: Newton-Raphson reciprocal of sqrt_r
                //   y_{n+1} = y * (2 - d*y)  scaled so result = 2^36 / d
                //--------------------------------------------------------------
                ST_RECIP_LOOP: begin
                    recip_y <= (recip_y * ((64'd1 << 37) - (recip_d * recip_y))) >> 36;
                    if (recip_iter == RECIP_ITER - 1)
                        state <= ST_PASS2;
                    else
                        recip_iter <= recip_iter + 1;
                end

                //--------------------------------------------------------------
                // ST_PASS2: output = x / sqrt(mean_sq + eps)
                //--------------------------------------------------------------
                ST_PASS2: begin
                    if (n == 1) begin
                        // Corner case: output sign(x)
                        if ($signed(x_buf[cnt]) > 0)
                            data_o <= 16'h3C00;      // +1.0
                        else if ($signed(x_buf[cnt]) < 0)
                            data_o <= 16'hBC00;      // -1.0
                        else
                            data_o <= 16'h0000;      // 0
                    end else begin
                        data_o <= fixed_to_fp16(round_div($signed(x_buf[cnt]) * $signed(recip_y),
                                                          (1 << FRAC)));
                    end
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
