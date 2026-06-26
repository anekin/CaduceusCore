// silu_hw.v — 4-stage SiLU pipeline (x * sigmoid(x))
//
// Reuses the shared exp_lut module (CaduceusCore/rtl/sfu/exp_lut.v)
// with linear interpolation for LUT precision.
//
// LUT address computation:
//   idx_8_8 = delta * 51 >> 6  →  addr = idx_8_8[15:8], frac = idx_8_8[7:0]
//   where delta = max(0, min(RANGE_FIXED, RANGE_FIXED - |x_fixed|))
//   This is equivalent to idx = delta * 255 / RANGE_FIXED with 8 fractional bits.

`timescale 1ns / 1ps

module silu_hw (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [15:0] data_i,
    input  wire        valid_i,
    output reg  [15:0] data_o,
    output reg         valid_o
);

    localparam X_FRAC   = 12;
    localparam E_ONE    = 15'd16384;
    localparam P_FRAC   = X_FRAC + 16;        // 28
    localparam [5:0] P_FRAC_W = 6'd28;
    localparam [31:0] RANGE_FIXED = 32'd20 << X_FRAC;

    // ── helpers ────────────────────────────────────────────────────

    function automatic [6:0] clz64;
        input [63:0] x;
        integer i;
        begin
            clz64 = 7'd64;
            for (i = 63; i >= 0; i = i - 1)
                if (x[i] && (clz64 == 7'd64))
                    clz64 = 7'd63 - i[6:0];
        end
    endfunction

    function automatic signed [31:0] fp16_to_fixed;
        input [15:0] fp; input [4:0] fb;
        reg sign; reg [4:0] exp; reg [9:0] mant;
        reg signed [31:0] val; reg signed [31:0] shift;
        begin
            sign = fp[15]; exp = fp[14:10]; mant = fp[9:0];
            if (exp == 0)
                fp16_to_fixed = 32'sd0;
            else begin
                val = (32'sd1 << 10) | mant;
                if (sign) val = -val;
                shift = exp - 5'd15 - 5'd10 + fb;
                fp16_to_fixed = (shift >= 0) ? (val <<< shift) : (val >>> (-shift));
            end
        end
    endfunction

    function automatic [15:0] fixed_s64_to_fp16;
        input signed [63:0] val; input [5:0] fb;
        reg sign; reg [63:0] absv; reg [6:0] lz; reg [5:0] msb;
        reg [63:0] norm; reg signed [5:0] exp_i; reg [10:0] mant;
        reg round_bit; reg sticky;
        begin
            if (val == 0)
                fixed_s64_to_fp16 = 16'h0000;
            else begin
                sign = val[63];
                absv = sign ? -val : val;
                lz   = clz64(absv);
                msb  = 6'd63 - lz[5:0];
                norm = absv << (63 - msb);
                exp_i = $signed(msb) - $signed(fb) + 6'sd15;
                mant  = norm[62:53];
                round_bit = norm[52];
                sticky    = |norm[51:0];
                if (round_bit && (sticky || mant[0]))
                    mant = mant + 11'd1;
                if (mant[10]) begin
                    mant  = 11'd0;
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

    // ── shared exp_lut instantiation ─────────────────────────────
    wire [7:0]  exp_lut_addr;
    wire [7:0]  exp_lut_frac;
    wire [14:0] exp_lut_out;

    exp_lut u_exp_lut (
        .clk(clk), .rst_n(rst_n),
        .addr(exp_lut_addr), .frac(exp_lut_frac),
        .lut_out(exp_lut_out)
    );

    // ── pipeline registers ───────────────────────────────────────
    reg          s1_valid;
    reg          s1_sign;
    reg signed [31:0] s1_x_fixed;
    reg [7:0]    s1_addr;
    reg [7:0]    s1_frac;

    reg          s2_valid;
    reg signed [31:0] s2_x_fixed;
    reg [16:0]   s2_sig;

    reg          s3_valid;
    reg signed [63:0] s3_prod;

    reg          s4_valid;
    reg [15:0]   s4_data;

    // ── combinational S1: LUT address with fractional part ───────
    // idx_8_8[15:8] = integer index, [7:0] = fractional part
    // idx_f = delta * 255 / RANGE_FIXED
    // In 8.8 fixed-point: idx_8_8 = delta * 51 / 64   (since 255/20*256=3264, /4096*51/64)
    reg signed [31:0] x_abs, delta;
    reg [15:0] idx_8_8;

    always @(*) begin
        x_abs = fp16_to_fixed(data_i, X_FRAC);
        x_abs = x_abs[31] ? -x_abs : x_abs;
        delta = $signed(RANGE_FIXED) - x_abs;
        if (delta < 32'sd0)
            delta = 32'sd0;
        else if (delta > $signed(RANGE_FIXED))
            delta = $signed(RANGE_FIXED);
        // idx_8_8 = (delta * 255 * 256) / 81920 = delta * 51 >> 6
        idx_8_8 = (delta * 32'd51) >> 6;
    end

    // Drive exp_lut with registered addr and frac
    assign exp_lut_addr = s1_addr;
    assign exp_lut_frac = s1_frac;

    // ── combinational S2: NR3 reciprocal + sigmoid ───────────────
    wire [14:0] e_raw_w;
    wire [15:0] sum16;
    wire [17:0] D;
    wire [16:0] sig_c;

    assign e_raw_w = exp_lut_out;
    assign sum16 = {1'b0, E_ONE} + {1'b0, e_raw_w};
    assign D = {sum16, 2'b0};

    function automatic [16:0] nr3;
        input [17:0] denom;
        reg [34:0] dy;
        reg [16:0] y;
        integer i;
        begin
            y = (17'd3 << 15) - (denom >> 1);
            for (i = 0; i < 3; i = i + 1) begin
                dy = denom * y;
                y  = (y * ((18'd2 << 16) - (dy >> 16))) >> 16;
            end
            nr3 = y;
        end
    endfunction

    wire [16:0] y_recip = nr3(D);
    wire [31:0] num_prod = e_raw_w * y_recip;
    assign sig_c = s1_sign ? (num_prod >> 14) : y_recip;

    // ── combinational S3: x_fixed * sig ──────────────────────────
    wire signed [63:0] prod_c;
    assign prod_c = $signed(s2_x_fixed) * $signed({47'b0, s2_sig});

    // ── combinational S4: fixed-to-FP16 ──────────────────────────
    wire [15:0] data_o_c;
    assign data_o_c = fixed_s64_to_fp16(s3_prod, P_FRAC_W);

    // ── sequential pipeline ──────────────────────────────────────
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            s1_valid <= 0;  s1_sign  <= 0;  s1_x_fixed <= 0;  s1_addr <= 0;  s1_frac <= 0;
            s2_valid <= 0;  s2_x_fixed <= 0;  s2_sig  <= 0;
            s3_valid <= 0;  s3_prod  <= 0;
            s4_valid <= 0;  s4_data  <= 0;
            data_o   <= 0;  valid_o  <= 0;
        end else begin
            s1_valid   <= valid_i;
            s1_sign    <= data_i[15];
            s1_x_fixed <= fp16_to_fixed(data_i, X_FRAC);
            s1_addr    <= idx_8_8[15:8];
            s1_frac    <= idx_8_8[7:0];

            s2_valid   <= s1_valid;
            s2_x_fixed <= s1_x_fixed;
            s2_sig     <= sig_c;

            s3_valid <= s2_valid;
            s3_prod  <= prod_c;

            s4_valid <= s3_valid;
            s4_data  <= data_o_c;

            data_o  <= s4_data;
            valid_o <= s4_valid;
        end
    end

endmodule
