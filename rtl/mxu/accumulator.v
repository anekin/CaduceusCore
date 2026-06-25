//===========================================================================
// MXU Accumulator — 64×64 INT32 storage with saturation clamping
//===========================================================================
// Internal submodule instantiated only inside mac_array.
// mxu_top does NOT directly connect to this module.
//
// Interfaces:
//   clk, rst_n         — clock and active-low async reset
//   addr[11:0]          — flattened {row_addr[5:0], col_addr[5:0]}
//   acc_in[31:0]        — signed INT32 partial sum to accumulate
//   acc_out[31:0]       — signed INT32 registered output
//   accumulate           — add acc_in to stored value with saturation
//   read_out             — output stored value to acc_out (next cycle)
//   reset_cmd            — clear stored value for addressed location to 0
//
// Saturation constants match CaduceusCore/sim/golden_executor.py:33-34
//   INT32_MAX = 2^31 - 1 = 32'h7FFFFFFF
//   INT32_MIN = -2^31     = 32'h80000000
//
// Overflow detection uses 33-bit signed arithmetic:
//   sum_wide > 2147483647  → positive overflow → clamp to INT32_MAX
//   sum_wide < -2147483648 → negative overflow → clamp to INT32_MIN
//===========================================================================

module accumulator (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [11:0] addr,        // flattened row/col: {row[5:0], col[5:0]}
    input  wire [31:0] acc_in,      // signed INT32 partial sum input
    output reg  [31:0] acc_out,     // signed INT32 output (registered)
    input  wire        accumulate,   // add acc_in to stored value with saturation
    input  wire        read_out,     // output stored value
    input  wire        reset_cmd     // clear stored value to 0
);

    //---------------------------------------------------------------------------
    // Constants from golden_executor.py:33-34
    //---------------------------------------------------------------------------
    localparam INT32_MAX_32 = 32'h7FFFFFFF;
    localparam INT32_MIN_32 = 32'h80000000;

    // 33-bit signed saturation thresholds
    localparam signed [32:0] THRESH_POS = 33'sd2147483647;   // 2^31 - 1
    localparam signed [32:0] THRESH_NEG = -33'sd2147483648;  // -2^31

    //---------------------------------------------------------------------------
    // Storage array: 64 × 64 = 4096 entries × 32 bits = 128 Kbits
    // Synthesizable as block RAM or register file.
    //---------------------------------------------------------------------------
    reg signed [31:0] acc_mem [0:4095];

    // Simulation-only: zero-initialize memory to avoid x-propagation
    // (synthesis ignores initial blocks; real HW uses reset_cmd per-location)
    integer _init_i;
    initial begin
        for (_init_i = 0; _init_i < 4096; _init_i = _init_i + 1)
            acc_mem[_init_i] = 32'd0;
    end

    //---------------------------------------------------------------------------
    // Combinational: read stored value and compute saturated sum
    //---------------------------------------------------------------------------
    wire signed [31:0] stored = acc_mem[addr];

    // Sign-extend both operands to 33 bits for overflow-safe addition.
    // Use explicit widening: stored is already signed[31:0]; acc_in is unsigned wire,
    // so $signed() is needed to get the signed interpretation before widening.
    wire signed [32:0] stored_se = stored;          // 32→33 sign-extension (stored is signed)
    wire signed [32:0] acc_se    = $signed(acc_in); // unsigned→signed 32, then 32→33 sign-ext
    wire signed [32:0] sum_wide  = stored_se + acc_se;

    // Saturation mux: clamp if overflow detected
    wire signed [31:0] saturated = (sum_wide > THRESH_POS) ? INT32_MAX_32
                                 : (sum_wide < THRESH_NEG) ? INT32_MIN_32
                                 : sum_wide[31:0];

    //---------------------------------------------------------------------------
    // Sequential logic: write (reset/accumulate) and read output register
    //---------------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            acc_out <= 32'd0;
        end else begin
            // Write path: reset takes priority over accumulate
            if (reset_cmd) begin
                acc_mem[addr] <= 32'd0;
            end else if (accumulate) begin
                acc_mem[addr] <= saturated;
            end

            // Read output: if accumulating, output the new value
            if (read_out) begin
                if (reset_cmd) begin
                    acc_out <= 32'd0;
                end else if (accumulate) begin
                    acc_out <= saturated;
                end else begin
                    acc_out <= stored;
                end
            end else begin
                acc_out <= 32'd0;
            end
        end
    end

endmodule

