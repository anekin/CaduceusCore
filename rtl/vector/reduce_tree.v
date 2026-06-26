`timescale 1ns / 1ps
//=============================================================================
// reduce_tree — 128 → 1 pipelined reduction tree
//=============================================================================
// Supports two operations:
//   op = 0 : MAX (signed INT32)
//   op = 1 : SUM (signed INT32, INT64 internal accumulator, INT32 saturated)
//
// Latency: 7 cycles for 128 inputs (128 -> 64 -> 32 -> 16 -> 8 -> 4 -> 2 -> 1).
// Pipeline registers are inserted between every stage for 1 GHz timing closure.
// lane_mask[127:0] disables invalid lanes for partial chunks.
//=============================================================================

module reduce_tree #(
    parameter NUM_IN = 128,
    parameter DATA_W = 32
)(
    input  wire                        clk,
    input  wire                        rst_n,      // active-low reset
    input  wire [NUM_IN*DATA_W-1:0]    data_i,     // NUM_IN x signed INT32
    input  wire                        op,         // 0=MAX, 1=SUM
    input  wire                        valid_i,    // input valid strobe
    input  wire [NUM_IN-1:0]           lane_mask,  // 1=lane active
    output reg  [DATA_W-1:0]           result_o,
    output reg  [63:0]                result64_o,  // raw INT64 (SUM) or sign-ext. (MAX)
    output reg                         valid_o
);

    //-------------------------------------------------------------------------
    // Constants
    //-------------------------------------------------------------------------
    localparam STAGES     = $clog2(NUM_IN);        // 7 for 128 inputs
    localparam MAX_W      = NUM_IN;                // widest stage = input width
    localparam FLAT_WIDTH = MAX_W * 64;            // flattened 64-bit stage vector

    localparam signed [63:0] INT32_MAX = 64'sh000000007FFFFFFF;
    localparam signed [63:0] INT32_MIN = 64'shFFFFFFFF80000000;

    //-------------------------------------------------------------------------
    // Stage data: packed vectors (one 64-bit slot per lane, sign-extended)
    //-------------------------------------------------------------------------
    reg [FLAT_WIDTH-1:0] stage_data [0:STAGES];
    reg [MAX_W-1:0]      stage_mask [0:STAGES];
    reg [STAGES:0]       op_pipe;
    reg [STAGES:0]       valid_pipe;

    //-------------------------------------------------------------------------
    // Stage 0 — fan out and sign-extend the input vector
    //-------------------------------------------------------------------------
    genvar gi;
    generate
        for (gi = 0; gi < NUM_IN; gi = gi + 1) begin : gen_input
            wire signed [DATA_W-1:0] in_word;
            assign in_word = data_i[gi*DATA_W +: DATA_W];
            always @(*) begin
                stage_data[0][gi*64 +: 64] = {{32{in_word[DATA_W-1]}}, in_word};
            end
        end
    endgenerate

    always @(*) begin
        stage_mask[0] = lane_mask;
        op_pipe[0]    = op;
        valid_pipe[0] = valid_i;
    end

    //-------------------------------------------------------------------------
    // Tree stages — pairwise MAX/SUM nodes with registered outputs
    //-------------------------------------------------------------------------
    genvar s, n;
    generate
        for (s = 0; s < STAGES; s = s + 1) begin : gen_stage
            localparam OUT_W  = NUM_IN >> (s + 1);
            localparam NEXT_W = OUT_W * 64;
            localparam DPAD   = FLAT_WIDTH - NEXT_W;
            localparam MPAD   = MAX_W - OUT_W;

            // Combinational node outputs for this stage
            wire [NEXT_W-1:0] node_data;
            wire [OUT_W-1:0]  node_mask;

            for (n = 0; n < OUT_W; n = n + 1) begin : gen_node
                wire signed [63:0] left  = $signed(stage_data[s][(2*n)*64 +: 64]);
                wire signed [63:0] right = $signed(stage_data[s][(2*n + 1)*64 +: 64]);
                wire               left_mask  = stage_mask[s][2*n];
                wire               right_mask = stage_mask[s][2*n + 1];

                // Identity value for disabled lanes: INT32_MIN for MAX, 0 for SUM
                wire signed [63:0] id_val = op_pipe[s] ? 64'sd0 : INT32_MIN;
                wire signed [63:0] left_val  = left_mask  ? left  : id_val;
                wire signed [63:0] right_val = right_mask ? right : id_val;

                wire signed [63:0] node_sum = left_val + right_val;
                wire signed [63:0] node_max = (left_val >= right_val) ? left_val : right_val;

                assign node_data[n*64 +: 64] = op_pipe[s] ? node_sum : node_max;
                assign node_mask[n]          = left_mask | right_mask;
            end

            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    stage_data[s+1] <= {FLAT_WIDTH{1'b0}};
                    stage_mask[s+1] <= {MAX_W{1'b0}};
                    op_pipe[s+1]    <= 1'b0;
                    valid_pipe[s+1] <= 1'b0;
                end else begin
                    stage_data[s+1] <= {{DPAD{1'b0}}, node_data};
                    stage_mask[s+1] <= {{MPAD{1'b0}}, node_mask};
                    op_pipe[s+1]    <= op_pipe[s];
                    valid_pipe[s+1] <= valid_pipe[s];
                end
            end
        end
    endgenerate

    //-------------------------------------------------------------------------
    // Output — saturate SUM to INT32, MAX returns lower 32 bits
    //-------------------------------------------------------------------------
    wire signed [63:0] final_acc = $signed(stage_data[STAGES][0 +: 64]);

    wire signed [31:0] sum_sat;
    assign sum_sat = (final_acc > INT32_MAX) ? 32'sh7FFFFFFF :
                     (final_acc < INT32_MIN) ? 32'sh80000000 :
                                                final_acc[31:0];

    wire [DATA_W-1:0] max_result = stage_data[STAGES][0 +: DATA_W];

    always @(*) begin
        result_o   = op_pipe[STAGES] ? sum_sat : max_result;
        result64_o = final_acc;
        valid_o    = valid_pipe[STAGES];
    end

endmodule
