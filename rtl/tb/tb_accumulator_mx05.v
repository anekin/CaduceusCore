// tb_accumulator_mx05.v — MX-05: accumulator address conflict
//
// Tests simultaneous accumulate + read_out on the same address.
// Per RTL: accumulate takes priority (writes new value), then read_out
// outputs the NEW value in the same cycle.
// Outputs mx05_result.hex for compare_rtl.py verification.

`timescale 1ns/1ps

module tb_accumulator_mx05;

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

    integer fd;

    initial begin
        fd = $fopen("mx05_result.hex", "w");
        addr = 0; acc_in = 0; accumulate = 0; read_out = 0; reset_cmd = 0;

        rst_n = 0; #20; rst_n = 1; #10;

        $display("=== MX-05: accumulator address conflict ===");

        // Test 1: accumulate(100) + read_out simultaneously → acc_out = 100
        $display("Test 1: accum(100)+read → 100");
        @(posedge clk); #1; addr = 0; reset_cmd = 1;
        @(posedge clk); #1; reset_cmd = 0; acc_in = 32'sd100; accumulate = 1; read_out = 1;
        @(posedge clk); #1; accumulate = 0; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'sd100) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected 100, got 0x%08h", acc_out);

        // Test 2: accumulate(200) + read_out → acc_out = 300 (100+200)
        $display("Test 2: accum(200)+read → 300");
        @(posedge clk); #1; addr = 0; acc_in = 32'sd200; accumulate = 1; read_out = 1;
        @(posedge clk); #1; accumulate = 0; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'sd300) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected 300, got 0x%08h", acc_out);

        // Test 3: accumulate(INT32_MAX) + read_out → acc_out = INT32_MAX (saturated)
        $display("Test 3: accum(MAX)+read → MAX");
        @(posedge clk); #1; addr = 1; reset_cmd = 1;
        @(posedge clk); #1; reset_cmd = 0; acc_in = 32'h7FFFFFFF; accumulate = 1; read_out = 1;
        @(posedge clk); #1; accumulate = 0; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'h7FFFFFFF) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected MAX, got 0x%08h", acc_out);

        // Test 4: accumulate(-50) + read_out → acc_out = INT32_MAX - 50
        $display("Test 4: accum(-50)+read → MAX-50");
        @(posedge clk); #1; addr = 1; acc_in = -32'sd50; accumulate = 1; read_out = 1;
        @(posedge clk); #1; accumulate = 0; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'h7FFFFFCD) $display("  PASS: 0x%08h (%0d)", acc_out, $signed(acc_out));
        else $display("  FAIL: expected MAX-50 (0x7FFFFFCD), got 0x%08h", acc_out);

        // Test 5: reset_cmd + read_out simultaneously → acc_out = 0
        $display("Test 5: reset+read → 0");
        @(posedge clk); #1; addr = 1; reset_cmd = 1; read_out = 1;
        @(posedge clk); #1; reset_cmd = 0; read_out = 0;
        $fwrite(fd, "%08h\n", acc_out);
        if (acc_out === 32'd0) $display("  PASS: 0x%08h", acc_out);
        else $display("  FAIL: expected 0, got 0x%08h", acc_out);

        $fclose(fd);

        $display("\n=== MX-05 Summary ===");
        $display("Result hex: mx05_result.hex (5 test values)");
        $display("MX-05: TESTS COMPLETE");

        #20;
        $finish;
    end

endmodule
