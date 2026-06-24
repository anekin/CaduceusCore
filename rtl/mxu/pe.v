`timescale 1ns / 1ps
//=============================================================================
// PE: Processing Element — INT4 weight × INT8 activation → INT32 MAC
//=============================================================================
// Single pipelined MAC unit with saturation clamping.
//   Pipeline: 1 register stage (output appears 1 cycle after inputs valid).
//   Sign extension: weight[3] (sign bit) replicated to bits [7:4] for INT8.
//   Saturation: sum clamped to [INT32_MIN, INT32_MAX] on overflow.
//   Pure datapath — no state machines, counters, or MMIO.
//=============================================================================

module pe (
    input  wire                 clk,
    input  wire                 rst_n,          // active-low reset
    input  wire signed  [7:0]   activation,     // signed INT8
    input  wire signed  [3:0]   weight,         // signed INT4
    input  wire signed [31:0]   acc_in,         // signed INT32 accumulator input
    output wire signed [31:0]   mac_out         // signed INT32 MAC output
);

    //---------------------------------------------------------------------
    // Sign-extend INT4 weight to INT8
    // weight[3] is the sign bit; replicate to bits [7:4].
    //---------------------------------------------------------------------
    wire signed [7:0] weight_se;
    assign weight_se = {{4{weight[3]}}, weight};

    //---------------------------------------------------------------------
    // Signed multiply: INT8 × INT8 → INT16
    //---------------------------------------------------------------------
    wire signed [15:0] product;
    assign product = weight_se * activation;

    //---------------------------------------------------------------------
    // Sign-extend to 33 bits for overflow-safe accumulation
    // Implicit sign extension via assignment to wider signed wire.
    //---------------------------------------------------------------------
    wire signed [32:0] product_33;
    wire signed [32:0] acc_33;
    wire signed [32:0] sum_33;

    assign product_33 = product;    // sign-extend 16 → 33 bits
    assign acc_33     = acc_in;     // sign-extend 32 → 33 bits
    assign sum_33     = product_33 + acc_33;

    //---------------------------------------------------------------------
    // Overflow detection: bit[32] != bit[31] means value does not fit
    // in 32-bit two's complement.
    //---------------------------------------------------------------------
    wire overflow;
    wire pos_ovf;   // positive overflow → clamp to INT32_MAX
    wire neg_ovf;   // negative overflow → clamp to INT32_MIN

    assign overflow = (sum_33[32] != sum_33[31]);
    assign pos_ovf  = overflow && !sum_33[32];
    assign neg_ovf  = overflow &&  sum_33[32];

    //---------------------------------------------------------------------
    // Saturation mux
    //---------------------------------------------------------------------
    wire signed [31:0] mac_comb;
    assign mac_comb = pos_ovf ? 32'h7FFFFFFF :
                      neg_ovf ? 32'h80000000 :
                                sum_33[31:0];

    //---------------------------------------------------------------------
    // Pipeline register (1 stage)
    //---------------------------------------------------------------------
    reg signed [31:0] mac_out_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            mac_out_r <= 32'h0;
        else
            mac_out_r <= mac_comb;
    end

    assign mac_out = mac_out_r;

endmodule
