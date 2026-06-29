// tb_accumulator_mx04.v — MX-04: accumulator saturation clamping
//
// Tests overflow → INT32_MAX clamp (not wrap) and underflow → INT32_MIN.
// Outputs mx04_result.hex for compare_rtl.py verification.

`timescale 1ns/1ps

module tb_accumulator_mx04;

    reg clk, rst_n;
    reg  [11:0] addr;
    reg  [31:0] acc_in;
    wire [31:0] acc_out;
    reg         accumulate, read_out, reset_cmd;

    accumulator u_dut (
        .clk(clk), .rst_n(rst_n),
        .addr(addr), .acc_in(acc_in),
        .acc_out(acc_out),
        .accumulate(accumulate), .read_out(read_out), .reset_cmd(reset_cmd)
    );

    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    integer fd, i;
    reg [31:0] expected;

    initial begin
        fd = $fopen("mx04_result.hex", "w");
        addr = 0; acc_in = 0; accumulate = 0; read_out = 0; reset_cmd = 0;

        rst_n = 0; #20; rst_n = 1; #10;

        $display("=== MX-04: accumulator saturation ===");

        // Test 1: INT32_MAX + 1 → INT32_MAX
        $display("Test 1: MAX+1 → MAX");
        @(posedge clk); #1; addr = 0; reset_cmd = 1;
        @(posedge clk); #1; reset_cmd = 0; acc_in = 32'h7FFFFFFF; accumulate = 1;
        @(posedge clk); #1; acc_in = 32'd1;
        @(posedge clk); #1; accumulate = 0; read_out = 1;
        @(posedge clk); #1; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'h7FFFFFFF) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected MAX, got 0x%08h", acc_out);

        // Test 2: INT32_MIN + (-1) → INT32_MIN
        $display("Test 2: MIN+(-1) → MIN");
        @(posedge clk); #1; addr = 1; reset_cmd = 1;
        @(posedge clk); #1; reset_cmd = 0; acc_in = 32'h80000000; accumulate = 1;
        @(posedge clk); #1; acc_in = -32'sd1;
        @(posedge clk); #1; accumulate = 0; read_out = 1;
        @(posedge clk); #1; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'h80000000) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected MIN, got 0x%08h", acc_out);

        // Test 3: (MAX-1) + 1 → MAX (boundary)
        $display("Test 3: (MAX-1)+1 → MAX");
        @(posedge clk); #1; addr = 2; reset_cmd = 1;
        @(posedge clk); #1; reset_cmd = 0; acc_in = 32'h7FFFFFFE; accumulate = 1;
        @(posedge clk); #1; acc_in = 32'd1;
        @(posedge clk); #1; accumulate = 0; read_out = 1;
        @(posedge clk); #1; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'h7FFFFFFF) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected MAX, got 0x%08h", acc_out);

        // Test 4: (MIN+1) + (-1) → MIN (boundary)
        $display("Test 4: (MIN+1)+(-1) → MIN");
        @(posedge clk); #1; addr = 3; reset_cmd = 1;
        @(posedge clk); #1; reset_cmd = 0; acc_in = 32'h80000001; accumulate = 1;
        @(posedge clk); #1; acc_in = -32'sd1;
        @(posedge clk); #1; accumulate = 0; read_out = 1;
        @(posedge clk); #1; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'h80000000) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected MIN, got 0x%08h", acc_out);

        // Test 5: MAX + 100 → MAX
        $display("Test 5: MAX+100 → MAX");
        @(posedge clk); #1; addr = 4; reset_cmd = 1;
        @(posedge clk); #1; reset_cmd = 0; acc_in = 32'h7FFFFFFF; accumulate = 1;
        @(posedge clk); #1; acc_in = 32'd100;
        @(posedge clk); #1; accumulate = 0; read_out = 1;
        @(posedge clk); #1; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'h7FFFFFFF) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected MAX, got 0x%08h", acc_out);

        // Test 6: 1000 + 2000 → 3000
        $display("Test 6: 1000+2000 → 3000");
        @(posedge clk); #1; addr = 5; reset_cmd = 1;
        @(posedge clk); #1; reset_cmd = 0; acc_in = 32'sd1000; accumulate = 1;
        @(posedge clk); #1; acc_in = 32'sd2000;
        @(posedge clk); #1; accumulate = 0; read_out = 1;
        @(posedge clk); #1; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'sd3000) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected 3000, got 0x%08h (%0d)", acc_out, $signed(acc_out));

        $fclose(fd);

        $display("\n=== MX-04 Summary ===");
        $display("Result hex: mx04_result.hex (6 test values)");
        $display("MX-04: TESTS COMPLETE");

        #20;
        $finish;
    end

endmodule
