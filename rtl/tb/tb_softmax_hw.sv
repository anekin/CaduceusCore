// tb_softmax_hw.sv — Smoke testbench for softmax_hw
//
// Reads an FP16 input vector and golden output vector from
// CaduceusCore/rtl/test_vectors/sfu/softmax_smoke/, drives the DUT,
// captures the FP16 outputs, writes result.hex, and checks the sum.

`timescale 1ns / 1ps

module tb_softmax_hw;

    localparam INPUT_FILE   = "CaduceusCore/rtl/test_vectors/sfu/softmax_smoke/input.hex";
    localparam GOLDEN_FILE  = "CaduceusCore/rtl/test_vectors/sfu/softmax_smoke/golden_output.hex";
    localparam RESULT_FILE  = "CaduceusCore/rtl/test_vectors/sfu/softmax_smoke/result.hex";
    localparam MAX_VEC      = 4096;

    reg         clk;
    reg         rst_n;
    reg  [15:0] data_i;
    reg         valid_i;
    reg         last_i;
    wire [15:0] data_o;
    wire        valid_o;

    reg [15:0] input_vec  [0:MAX_VEC-1];
    reg [15:0] golden_vec [0:MAX_VEC-1];
    reg [15:0] result_vec [0:MAX_VEC-1];

    integer in_count;
    integer out_count;
    integer i;
    integer errors;
    real    sum;
    real    val;

    // DUT instantiation
    softmax_hw #(
        .VEC_MAX(64)  // small instance is enough for smoke vectors
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

    // Helper: convert FP16 pattern to real
    function automatic real fp16_to_real;
        input [15:0] h;
        reg [15:0] bytes;
        real sgn;
        real expv;
        real mant;
        begin
            // little-endian interpretation matches compare_rtl.py
            sgn  = h[15] ? -1.0 : 1.0;
            expv = h[14:10];
            mant = h[9:0];
            if (expv == 0)
                fp16_to_real = 0.0;
            else
                fp16_to_real = sgn * (1.0 + mant / 1024.0) * (2.0 ** (expv - 15.0));
        end
    endfunction

    // Main stimulus
    initial begin
        errors    = 0;
        in_count  = 0;
        out_count = 0;
        data_i    = 16'd0;
        valid_i   = 1'b0;
        last_i    = 1'b0;

        // Load vectors
        $readmemh(INPUT_FILE, input_vec);
        $readmemh(GOLDEN_FILE, golden_vec);

        // Count non-X input entries (the smoke vector is 4 elements)
        for (i = 0; i < MAX_VEC; i = i + 1) begin
            if (input_vec[i] !== 16'hxxxx)
                in_count = in_count + 1;
        end

        $display("[tb_softmax_hw] Loaded %0d input elements", in_count);

        // Reset
        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;

        // Feed the vector
        for (i = 0; i < in_count; i = i + 1) begin
            @(posedge clk);
            data_i  <= input_vec[i];
            valid_i <= 1'b1;
            last_i  <= (i == in_count - 1);
        end

        @(posedge clk);
        valid_i <= 1'b0;
        last_i  <= 1'b0;
        data_i  <= 16'd0;

        // Capture outputs
        while (out_count < in_count) begin
            @(posedge clk);
            if (valid_o) begin
                result_vec[out_count] = data_o;
                out_count = out_count + 1;
            end
        end

        // Write result.hex
        begin
            integer fd;
            $display("[tb_softmax_hw] Writing %0d outputs to %s", out_count, RESULT_FILE);
            fd = $fopen(RESULT_FILE, "w");
            for (i = 0; i < out_count; i = i + 1)
                $fdisplay(fd, "%04h", result_vec[i]);
            $fclose(fd);
        end

        // Compute sum of outputs
        sum = 0.0;
        for (i = 0; i < out_count; i = i + 1) begin
            val = fp16_to_real(result_vec[i]);
            sum = sum + val;
            $display("  out[%0d] = 0x%04h  (%f)", i, result_vec[i], val);
        end
        $display("[tb_softmax_hw] Output sum = %f  (expected ~1.0)", sum);

        if (sum > 0.999 && sum < 1.001)
            $display("[tb_softmax_hw] SUM CHECK PASSED");
        else begin
            $display("[tb_softmax_hw] SUM CHECK FAILED");
            errors = errors + 1;
        end

        if (errors == 0)
            $display("[tb_softmax_hw] ALL CHECKS PASSED");
        else
            $display("[tb_softmax_hw] %0d CHECK(S) FAILED", errors);

        #20;
        $finish;
    end

    // Timeout watchdog
    initial begin
        #100000;
        $display("[tb_softmax_hw] TIMEOUT");
        $finish;
    end

endmodule
