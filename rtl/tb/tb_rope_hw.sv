// tb_rope_hw.sv — Self-checking testbench for rope_hw
//
// Reads FP16 (x, y, theta) triples from input.hex and golden (x, y) pairs
// from golden.hex, drives the DUT, captures outputs 12 cycles later, and
// checks against the golden values with a 1e-1 absolute tolerance.

`timescale 1ns / 1ps

module tb_rope_hw;

    localparam INPUT_FILE   = "CaduceusCore/rtl/test_vectors/sfu/rope_smoke/input.hex";
    localparam GOLDEN_FILE  = "CaduceusCore/rtl/test_vectors/sfu/rope_smoke/golden.hex";
    localparam RESULT_FILE  = "CaduceusCore/rtl/test_vectors/sfu/rope_smoke/result.hex";
    localparam NUM_TESTS    = 7;
    localparam MAX_TESTS    = 32;
    localparam LATENCY      = 12;
    localparam real TOL_ABS = 0.1;

    reg         clk;
    reg         rst_n;
    reg  [15:0] x_i;
    reg  [15:0] y_i;
    reg  [15:0] theta_i;
    reg         valid_i;
    wire [15:0] x_o;
    wire [15:0] y_o;
    wire        valid_o;

    reg [47:0] input_vec  [0:MAX_TESTS-1];
    reg [31:0] golden_vec [0:MAX_TESTS-1];
    reg [31:0] result_vec [0:MAX_TESTS-1];

    integer out_count;
    integer errors;
    integer i;
    real    rx, ry, gx, gy, dx, dy;

    // DUT instantiation
    rope_hw dut (
        .clk    (clk),
        .rst_n  (rst_n),
        .x_i    (x_i),
        .y_i    (y_i),
        .theta_i(theta_i),
        .valid_i(valid_i),
        .x_o    (x_o),
        .y_o    (y_o),
        .valid_o(valid_o)
    );

    // Clock: 100 MHz
    initial clk = 1'b0;
    always #5 clk = ~clk;

    // Helper: convert FP16 pattern to real
    function automatic real fp16_to_real;
        input [15:0] h;
        real sgn;
        real expv;
        real mant;
        begin
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
        out_count = 0;
        x_i       = 16'd0;
        y_i       = 16'd0;
        theta_i   = 16'd0;
        valid_i   = 1'b0;

        $display("[tb_rope_hw] Loading test vectors from %s", INPUT_FILE);
        $readmemh(INPUT_FILE,  input_vec);
        $readmemh(GOLDEN_FILE, golden_vec);

        $display("[tb_rope_hw] Loaded %0d test cases", NUM_TESTS);

        // Reset
        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;

        // Feed test cases back-to-back
        for (i = 0; i < NUM_TESTS; i = i + 1) begin
            @(posedge clk);
            x_i     <= input_vec[i][47:32];
            y_i     <= input_vec[i][31:16];
            theta_i <= input_vec[i][15:0];
            valid_i <= 1'b1;
        end

        @(posedge clk);
        valid_i <= 1'b0;
        x_i     <= 16'd0;
        y_i     <= 16'd0;
        theta_i <= 16'd0;

        // Capture outputs
        while (out_count < NUM_TESTS) begin
            @(posedge clk);
            if (valid_o) begin
                result_vec[out_count] = {x_o, y_o};
                out_count = out_count + 1;
            end
        end

        // Write result.hex
        begin
            integer fd;
            $display("[tb_rope_hw] Writing %0d outputs to %s", out_count, RESULT_FILE);
            fd = $fopen(RESULT_FILE, "w");
            for (i = 0; i < out_count; i = i + 1)
                $fdisplay(fd, "%04h %04h", result_vec[i][31:16], result_vec[i][15:0]);
            $fclose(fd);
        end

        // Compare with golden
        $display("[tb_rope_hw] Comparing outputs (tolerance = %f)", TOL_ABS);
        for (i = 0; i < out_count; i = i + 1) begin
            rx = fp16_to_real(result_vec[i][31:16]);
            ry = fp16_to_real(result_vec[i][15:0]);
            gx = fp16_to_real(golden_vec[i][31:16]);
            gy = fp16_to_real(golden_vec[i][15:0]);
            dx = rx - gx;
            dy = ry - gy;
            if (dx < 0) dx = -dx;
            if (dy < 0) dy = -dy;

            $display("  test[%0d] in=(%04h,%04h,%04h) out=(%04h,%04h) golden=(%04h,%04h) real=(%f,%f) golden_real=(%f,%f) err=(%f,%f)",
                     i,
                     input_vec[i][47:32], input_vec[i][31:16], input_vec[i][15:0],
                     result_vec[i][31:16], result_vec[i][15:0],
                     golden_vec[i][31:16], golden_vec[i][15:0],
                     rx, ry, gx, gy, dx, dy);

            if (dx > TOL_ABS || dy > TOL_ABS) begin
                $display("[tb_rope_hw] MISMATCH at test %0d", i);
                errors = errors + 1;
            end
        end

        if (errors == 0)
            $display("[tb_rope_hw] ALL %0d TESTS PASSED", NUM_TESTS);
        else
            $display("[tb_rope_hw] %0d OF %0d TESTS FAILED", errors, NUM_TESTS);

        #20;
        $finish;
    end

    // Timeout watchdog
    initial begin
        #100000;
        $display("[tb_rope_hw] TIMEOUT");
        $finish;
    end

endmodule
