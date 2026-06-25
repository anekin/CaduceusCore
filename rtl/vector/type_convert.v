//=============================================================================
// type_convert — INT32 → FP16 (IEEE 754 half-precision) converter
//=============================================================================
// Task 10 of sfu-vector-phase2 Wave 2.
//
// The MXU→SFU bridge: converts INT32 accumulator output to FP16 for the SFU
// pipeline.  1-cycle registered pipeline: inputs at cycle N → output at N+1.
//
// Algorithm:
//   1. sign  = data_i[31]
//   2. abs   = |data_i| (unsigned 32-bit)
//   3. Special: abs == 0          → 0x0000
//              abs > 65504        → ±0x7BFF (saturate to FP16 max normal)
//              rounding overflow  → ±0x7BFF (saturate)
//              subnormal          → ±0x0001 (defensive; cannot occur for INT32)
//   4. Priority encoder: find highest set bit position (lead_pos).
//   5. Exponent  = 15 + lead_pos (biased; lead_pos is floor(log2(abs))).
//   6. Normalize: shift the absolute value left so the leading 1 sits at bit
//      position 63 of a 64-bit word.
//   7. Mantissa  = bits[62:53] (10 bits after the implicit 1).
//      guard     = bit 52, round = bit 51, sticky = OR of bits 50:0.
//   8. Round-to-nearest-even: round_up = guard && (round | sticky | mantissa[0]).
//   9. Final result = {sign, exponent[4:0], mantissa_rounded[9:0]}.
//
// IEEE 754 FP16 layout: sign(1) | exponent(5) | mantissa(10), bias = 15.
//   Max normal: 0x7BFF = 65504.  Infinity: 0x7C00.  Subnormal minimum: 0x0001.
//=============================================================================

module type_convert (
    input  wire         clk,
    input  wire         rst_n,         // active-low reset

    input  wire [31:0]  data_i,        // signed INT32
    input  wire         valid_i,

    output wire [15:0]  data_o,        // IEEE 754 FP16
    output wire         valid_o
);

    //-------------------------------------------------------------------------
    // Local parameters
    //-------------------------------------------------------------------------
    localparam [31:0] FP16_MAX_ABS  = 32'd65504;        // 0x7BFF
    localparam [14:0] FP16_MAX_BITS = 15'h7BFF;
    localparam [4:0]  MAX_EXPONENT  = 5'd30;            // biased 30 = actual 15
    localparam [9:0]  MAX_MANTISSA  = 10'h3FF;          // all 1s
    localparam [14:0] SUB_MIN_BITS  = 15'h0001;         // ± smallest subnormal
    localparam [4:0]  BIAS          = 5'd15;

    //=========================================================================
    // Stage 0: Combinational conversion
    //=========================================================================

    // ---- 1. Sign and absolute value ---------------------------------------
    wire        sign;
    wire [31:0] abs_val_u;

    // Two's complement absolute value.
    // For INT32_MIN (0x8000_0000) the result wraps to 0x8000_0000 (unsigned =
    // 2^31), but that is >> 65504 so saturation handles it correctly.
    assign sign      = data_i[31];
    assign abs_val_u = sign ? (~data_i + 1'b1) : data_i;

    // ---- 2. Special-case flags --------------------------------------------
    wire is_zero          = (abs_val_u == 32'd0);
    wire is_greater_65504 = (abs_val_u > FP16_MAX_ABS);

    // ---- 3. Priority encoder: find highest set bit position ---------------
    // Produces 0..31 (0 means abs=1; for abs=0 lead_pos is unused).
    reg [4:0] lead_pos;
    always @(*) begin
        if (abs_val_u[31])      lead_pos = 5'd31;
        else if (abs_val_u[30]) lead_pos = 5'd30;
        else if (abs_val_u[29]) lead_pos = 5'd29;
        else if (abs_val_u[28]) lead_pos = 5'd28;
        else if (abs_val_u[27]) lead_pos = 5'd27;
        else if (abs_val_u[26]) lead_pos = 5'd26;
        else if (abs_val_u[25]) lead_pos = 5'd25;
        else if (abs_val_u[24]) lead_pos = 5'd24;
        else if (abs_val_u[23]) lead_pos = 5'd23;
        else if (abs_val_u[22]) lead_pos = 5'd22;
        else if (abs_val_u[21]) lead_pos = 5'd21;
        else if (abs_val_u[20]) lead_pos = 5'd20;
        else if (abs_val_u[19]) lead_pos = 5'd19;
        else if (abs_val_u[18]) lead_pos = 5'd18;
        else if (abs_val_u[17]) lead_pos = 5'd17;
        else if (abs_val_u[16]) lead_pos = 5'd16;
        else if (abs_val_u[15]) lead_pos = 5'd15;
        else if (abs_val_u[14]) lead_pos = 5'd14;
        else if (abs_val_u[13]) lead_pos = 5'd13;
        else if (abs_val_u[12]) lead_pos = 5'd12;
        else if (abs_val_u[11]) lead_pos = 5'd11;
        else if (abs_val_u[10]) lead_pos = 5'd10;
        else if (abs_val_u[9])  lead_pos = 5'd9;
        else if (abs_val_u[8])  lead_pos = 5'd8;
        else if (abs_val_u[7])  lead_pos = 5'd7;
        else if (abs_val_u[6])  lead_pos = 5'd6;
        else if (abs_val_u[5])  lead_pos = 5'd5;
        else if (abs_val_u[4])  lead_pos = 5'd4;
        else if (abs_val_u[3])  lead_pos = 5'd3;
        else if (abs_val_u[2])  lead_pos = 5'd2;
        else if (abs_val_u[1])  lead_pos = 5'd1;
        else if (abs_val_u[0])  lead_pos = 5'd0;
        else                    lead_pos = 5'd0;   // abs=0 (unused)
    end

    // ---- 4. Exponent (biased) ---------------------------------------------
    // exponent = BIAS + lead_pos   (15 + floor(log2(abs)))
    wire [4:0] exponent_raw;

    assign exponent_raw = BIAS + lead_pos;

    // ---- 5. Normalize (left-align to bit 63 of 64-bit word) ---------------
    // Shift abs_val left by (63 - lead_pos) so the leading 1 lands at bit 63.
    wire [5:0]  shift_amt;
    wire [63:0] abs_ext;       // {32'd0, abs_val_u}
    wire [63:0] normalized;    // abs_ext << shift_amt

    assign shift_amt  = 6'd63 - {1'b0, lead_pos};
    assign abs_ext    = {32'd0, abs_val_u};
    assign normalized = abs_ext << shift_amt;

    // ---- 6. Extract mantissa and rounding signals -------------------------
    // normalized[63] = 1 (implicit leading one — NOT stored in mantissa field)
    // mantissa_raw = normalized[62:53]   (10 bits)
    // guard        = normalized[52]
    // round        = normalized[51]
    // sticky       = OR of normalized[50:0]
    wire [9:0] mantissa_raw;
    wire       guard;
    wire       round_bit;
    wire       sticky;

    assign mantissa_raw = normalized[62:53];
    assign guard        = normalized[52];
    assign round_bit    = normalized[51];
    assign sticky       = |normalized[50:0];

    // ---- 7. Round-to-nearest-even -----------------------------------------
    // Round up when guard=1 AND (round=1 OR sticky=1 OR LSB=1).
    wire round_up;

    assign round_up = guard && (round_bit || sticky || mantissa_raw[0]);

    wire [9:0] mantissa_rounded;
    wire       mantissa_overflow;

    assign mantissa_rounded  = mantissa_raw + {9'd0, round_up};
    // Overflow when mantissa_raw is all-1s and we round up → 10-bit carry
    assign mantissa_overflow = (mantissa_raw == MAX_MANTISSA) && round_up;

    wire [4:0] exponent_final;

    assign exponent_final = exponent_raw + {4'd0, mantissa_overflow};

    // ---- 8. Subnormal detection (defensive) --------------------------------
    // For INT32 inputs the minimum |x| is 1 → lead_pos=0 → exp=15 (normal),
    // so subnormals cannot occur. Included for completeness.
    wire is_subnormal;

    assign is_subnormal = (exponent_final == 5'd0) && (mantissa_rounded != 10'd0);

    // ---- 9. Assemble final result -----------------------------------------
    // Saturation triggers when:
    //   (a) raw abs exceeds FP16 max normal (65504), or
    //   (b) mantissa rounding overflow pushes exponent to 31 (infinity);
    //       per task spec infinity is saturated to ±0x7BFF.
    // Mantissa overflow with exponent < 31 is fine — the exponent simply
    // increments and mantissa becomes 0 (e.g. 32767 → 32768 = 0x7800).
    wire is_saturate;

    assign is_saturate = is_greater_65504 || (mantissa_overflow && (exponent_final >= 5'd31));

    reg [15:0] result_comb;
    always @(*) begin
        if (is_zero)
            result_comb = 16'h0000;
        else if (is_saturate)
            // ±FP16 max normal (0x7BFF / 0xFBFF)
            result_comb = {sign, FP16_MAX_BITS};
        else if (is_subnormal)
            // Flush to ±smallest subnormal (0x0001 / 0x8001)
            result_comb = {sign, SUB_MIN_BITS};
        else
            result_comb = {sign, exponent_final[4:0], mantissa_rounded};
    end

    //=========================================================================
    // Stage 1: Pipeline register (1 cycle latency)
    //=========================================================================
    reg [15:0] data_r;
    reg        valid_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            data_r  <= 16'd0;
            valid_r <= 1'b0;
        end else begin
            data_r  <= result_comb;
            valid_r <= valid_i;
        end
    end

    assign data_o  = data_r;
    assign valid_o = valid_r;

endmodule
