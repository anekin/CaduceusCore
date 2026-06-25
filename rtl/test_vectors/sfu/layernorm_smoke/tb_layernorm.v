//=============================================================================
// tb_layernorm — Standalone smoke testbench for layernorm_hw
//=============================================================================
// Reads input.hex / golden.hex from the same directory, streams one 4096-
// element FP16 vector through layernorm_hw, captures the output, and writes
// result.hex for compare_rtl.py.
//
// Usage:
//   cd CaduceusCore/rtl/test_vectors/sfu/layernorm_smoke
//   vcs -full64 -sverilog -timescale=1ns/1ps -top tb_layernorm \
//       ../../../sfu/layernorm_hw.v tb_layernorm.v -o simv_ln -l compile.log
//   ./simv_ln -l sim.log
//   python3 ../../../../sim/compare_rtl.py .
//=============================================================================

`timescale 1ns / 1ps

module tb_layernorm;

    localparam N       = 4096;
    localparam CLK_HALF= 5;      // 100 MHz

    reg         clk;
    reg         rst_n;
    reg  [15:0] data_i;
    reg         valid_i;
    reg         last_i;
    wire [15:0] data_o;
    wire        valid_o;

    reg [15:0] input_mem  [0:N-1];
    reg [15:0] golden_mem [0:N-1];
    reg [15:0] result_mem [0:N-1];

    integer in_idx;
    integer out_idx;
    integer errors;
    integer cycle_cnt;

    //=========================================================================
    // DUT
    //=========================================================================
    layernorm_hw u_dut (
        .clk     (clk),
        .rst_n   (rst_n),
        .data_i  (data_i),
        .valid_i (valid_i),
        .last_i  (last_i),
        .data_o  (data_o),
        .valid_o (valid_o)
    );

    //=========================================================================
    // Clock / reset
    //=========================================================================
    initial begin
        clk = 1'b0;
        forever #CLK_HALF clk = ~clk;
    end

    initial begin
        rst_n   = 1'b0;
        data_i  = 16'h0000;
        valid_i = 1'b0;
        last_i  = 1'b0;
        in_idx  = 0;
        out_idx = 0;
        errors  = 0;
        cycle_cnt = 0;

        // Load test vectors
        $readmemh("input.hex",  input_mem);
        $readmemh("golden.hex", golden_mem);

        // Reset pulse
        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);

        // Stream input vector
        for (in_idx = 0; in_idx < N; in_idx = in_idx + 1) begin
            @(posedge clk);
            data_i  <= input_mem[in_idx];
            valid_i <= 1'b1;
            last_i  <= (in_idx == N - 1) ? 1'b1 : 1'b0;
        end

        @(posedge clk);
        valid_i <= 1'b0;
        last_i  <= 1'b0;

        // Wait for output with a generous timeout
        while (out_idx < N) begin
            @(negedge clk);
            cycle_cnt = cycle_cnt + 1;
            if (valid_o) begin
                result_mem[out_idx] = data_o;
                out_idx = out_idx + 1;
            end
            if (cycle_cnt > N * 10) begin
                $display("ERROR: timeout waiting for outputs (got %0d / %0d)", out_idx, N);
                $finish(1);
            end
        end

        // Write result file for compare_rtl.py
        $writememh("result.hex", result_mem);

        $display("layernorm_smoke: streamed %0d inputs, captured %0d outputs", N, out_idx);
        $display("Result written to result.hex");
        $finish(0);
    end

endmodule
