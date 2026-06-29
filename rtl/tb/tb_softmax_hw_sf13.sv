// tb_softmax_hw_sf13.sv — SF-13: softmax_hw pipeline latency measurement
//
// Feeds N=128 FP16 elements to softmax_hw, counts cycles from
// first valid_i posedge to first valid_o posedge, and prints
// SOFTMAX_LATENCY=N+<actual> marker for verification.
//
// The pipeline: ST_IN_VECTOR(N) → ST_EXP_START(1) → ST_EXP_RUN(N)
// → ST_RECIP_INIT(1) → ST_RECIP_LOOP(24) → ST_RECIP_NR(4)
// → ST_DIV_START(1) → ST_DIV_RUN → first valid_o
// Expected: ~2N+31 cycles from first valid_i to first valid_o

`timescale 1ns / 1ps

module tb_softmax_hw_sf13;

    localparam N_ELEMENTS = 128;
    localparam MAX_VEC    = 256;

    reg         clk;
    reg         rst_n;
    reg  [15:0] data_i;
    reg         valid_i;
    reg         last_i;
    wire [15:0] data_o;
    wire        valid_o;

    // Cycle counter
    integer cycle;
    integer first_valid_i_cycle;
    integer first_valid_o_cycle;
    integer measured_latency;
    integer i;
    integer error_count;

    // DUT instantiation
    softmax_hw #(
        .VEC_MAX(MAX_VEC)
    ) dut (
        .clk    (clk),
        .rst_n  (rst_n),
        .data_i (data_i),
        .valid_i(valid_i),
        .last_i (last_i),
        .data_o (data_o),
        .valid_o(valid_o)
    );

    // Clock: 100 MHz
    initial clk = 1'b0;
    always #5 clk = ~clk;

    // Cycle counter
    always @(posedge clk) begin
        cycle <= cycle + 1;
    end

    // Track first valid_i and valid_o cycle numbers
    always @(posedge clk) begin
        if (valid_i && first_valid_i_cycle == 0)
            first_valid_i_cycle <= cycle;
        if (valid_o && first_valid_o_cycle == 0)
            first_valid_o_cycle <= cycle;
    end

    // Main stimulus
    initial begin
        cycle               = 0;
        first_valid_i_cycle = 0;
        first_valid_o_cycle = 0;
        measured_latency    = 0;
        error_count         = 0;
        data_i              = 16'd0;
        valid_i             = 1'b0;
        last_i              = 1'b0;

        // Reset
        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;

        $display("[SF-13] Feeding %0d FP16 elements to softmax_hw", N_ELEMENTS);

        // Feed N_ELEMENTS of value 1.0 (FP16 0x3C00)
        for (i = 0; i < N_ELEMENTS; i = i + 1) begin
            @(posedge clk);
            data_i  <= 16'h3C00;
            valid_i <= 1'b1;
            last_i  <= (i == N_ELEMENTS - 1);
        end

        @(posedge clk);
        valid_i <= 1'b0;
        last_i  <= 1'b0;
        data_i  <= 16'd0;

        $display("[SF-13] Input feeding done at cycle %0d", cycle);

        // Wait for all outputs
        i = 0;
        while (i < N_ELEMENTS) begin
            @(posedge clk);
            if (valid_o)
                i = i + 1;
        end

        // Compute measured latency
        if (first_valid_i_cycle > 0 && first_valid_o_cycle > 0) begin
            measured_latency = first_valid_o_cycle - first_valid_i_cycle;
            $display("SOFTMAX_LATENCY=N+%0d (N=%0d, first_valid_i@cycle=%0d, first_valid_o@cycle=%0d, measured=%0d)",
                     measured_latency - N_ELEMENTS, N_ELEMENTS,
                     first_valid_i_cycle, first_valid_o_cycle,
                     measured_latency);
        end else begin
            $display("SOFTMAX_LATENCY=ERROR (failed to capture valid_i or valid_o)");
            error_count = error_count + 1;
        end

        if (error_count == 0)
            $display("[SF-13] ALL CHECKS PASSED");
        else
            $display("[SF-13] %0d CHECK(S) FAILED", error_count);

        #20;
        $finish(2);
    end

    // Timeout watchdog
    initial begin
        #500000;
        $display("[SF-13] TIMEOUT");
        $finish(2);
    end

endmodule
