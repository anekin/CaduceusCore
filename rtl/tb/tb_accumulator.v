//===========================================================================
// Self-checking testbench for accumulator module
//===========================================================================
`timescale 1ns / 1ps

module tb_accumulator;

    //---------------------------------------------------------------------------
    // DUT signals
    //---------------------------------------------------------------------------
    reg         clk;
    reg         rst_n;
    reg  [11:0] addr;
    reg  [31:0] acc_in;
    wire [31:0] acc_out;
    reg         accumulate;
    reg         read_out;
    reg         reset_cmd;

    //---------------------------------------------------------------------------
    // DUT instantiation
    //---------------------------------------------------------------------------
    accumulator u_dut (
        .clk        (clk),
        .rst_n      (rst_n),
        .addr       (addr),
        .acc_in     (acc_in),
        .acc_out    (acc_out),
        .accumulate (accumulate),
        .read_out   (read_out),
        .reset_cmd  (reset_cmd)
    );

    //---------------------------------------------------------------------------
    // Clock generation: 10 ns period (100 MHz)
    //---------------------------------------------------------------------------
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    //---------------------------------------------------------------------------
    // Helper task: drive DUT inputs, wait one cycle, then read output
    //---------------------------------------------------------------------------
    integer total_tests;
    integer passed_tests;
    integer failed_tests;

    task automatic check_output;
        input [31:0] expected;
        input [255:0] desc;
    begin
        total_tests = total_tests + 1;
        if (acc_out !== expected) begin
            $display("FAIL [%0s]: addr=%0d expected=0x%08h (%0d) got=0x%08h (%0d)",
                     desc, addr, expected, $signed(expected),
                     acc_out, $signed(acc_out));
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [%0s]: addr=%0d value=0x%08h (%0d)",
                     desc, addr, expected, $signed(expected));
            passed_tests = passed_tests + 1;
        end
    end
    endtask

    //---------------------------------------------------------------------------
    // Test sequence — all DUT signal drives use @(posedge clk); #1;
    // The #1 delay defers assignments past the active region so the DUT's
    // always block (triggered by the same posedge) always reads the *current*
    // stable values before they are updated for the next cycle.
    //---------------------------------------------------------------------------
    initial begin
        total_tests  = 0;
        passed_tests = 0;
        failed_tests = 0;

        // Initialize all control signals
        addr       = 12'd0;
        acc_in     = 32'd0;
        accumulate = 1'b0;
        read_out   = 1'b0;
        reset_cmd  = 1'b0;

        // --- Power-on reset ---
        rst_n = 1'b0;
        #20;
        rst_n = 1'b1;
        #10;
        $display("=== Accumulator Self-Checking Testbench ===");
        $display("");

        //===============================================================
        // Test 1: Basic accumulate — 1000 + 2000 = 3000
        //===============================================================
        $display("--- Test 1: Basic accumulate (1000 + 2000) ---");

        // Cycle 1: accumulate 1000 at addr 0
        @(posedge clk); #1;
        addr       = 12'd0;
        acc_in     = 32'sd1000;
        accumulate = 1'b1;
        read_out   = 1'b0;
        reset_cmd  = 1'b0;

        // Cycle 2: accumulate 2000 at addr 0
        @(posedge clk); #1;
        acc_in     = 32'sd2000;

        // Cycle 3: read_out at addr 0
        @(posedge clk); #1;
        accumulate = 1'b0;
        read_out   = 1'b1;

        // Cycle 4: check output (registered: appears 1 cycle after read_out)
        @(posedge clk); #1;
        read_out   = 1'b0;
        check_output(32'sd3000, "Basic 1000+2000");

        //===============================================================
        // Test 2: Positive overflow — INT32_MAX + 1 → INT32_MAX
        //===============================================================
        $display("--- Test 2: Positive overflow (INT32_MAX + 1) ---");

        // Reset addr 1 first
        @(posedge clk); #1;
        addr       = 12'd1;
        reset_cmd  = 1'b1;

        // Cycle: write INT32_MAX to addr 1
        @(posedge clk); #1;
        reset_cmd  = 1'b0;
        acc_in     = 32'h7FFFFFFF;  // INT32_MAX
        accumulate = 1'b1;

        // Cycle: add 1 — should saturate
        @(posedge clk); #1;
        acc_in     = 32'sd1;

        // Cycle: read_out
        @(posedge clk); #1;
        accumulate = 1'b0;
        read_out   = 1'b1;

        // Cycle: check
        @(posedge clk); #1;
        read_out   = 1'b0;
        check_output(32'h7FFFFFFF, "Pos overflow MAX+1");

        //===============================================================
        // Test 3: Negative overflow — INT32_MIN + (-1) → INT32_MIN
        //===============================================================
        $display("--- Test 3: Negative overflow (INT32_MIN + -1) ---");

        // Reset addr 2 first
        @(posedge clk); #1;
        addr       = 12'd2;
        reset_cmd  = 1'b1;

        // Cycle: write INT32_MIN to addr 2
        @(posedge clk); #1;
        reset_cmd  = 1'b0;
        acc_in     = 32'h80000000;  // INT32_MIN
        accumulate = 1'b1;

        // Cycle: add -1 — should saturate
        @(posedge clk); #1;
        acc_in     = -32'sd1;

        // Cycle: read_out
        @(posedge clk); #1;
        accumulate = 1'b0;
        read_out   = 1'b1;

        // Cycle: check
        @(posedge clk); #1;
        read_out   = 1'b0;
        check_output(32'h80000000, "Neg overflow MIN+(-1)");

        //===============================================================
        // Test 4: Accumulate-then-read on same cycle
        //===============================================================
        $display("--- Test 4: Accumulate+read same cycle ---");

        // Reset addr 3
        @(posedge clk); #1;
        addr       = 12'd3;
        reset_cmd  = 1'b1;

        // Write INT32_MAX
        @(posedge clk); #1;
        reset_cmd  = 1'b0;
        acc_in     = 32'h7FFFFFFF;
        accumulate = 1'b1;

        // Accumulate -1 AND read_out — should output new value (saturated)
        @(posedge clk); #1;
        acc_in     = -32'sd1;
        read_out   = 1'b1;
        // accumulate stays 1

        // Check: acc_out = INT32_MAX - 1 = 0x7FFFFFFE
        @(posedge clk); #1;
        accumulate = 1'b0;
        read_out   = 1'b0;
        check_output(32'h7FFFFFFE, "Accum+read same cyc");

        //===============================================================
        // Test 5: Reset clears value
        //===============================================================
        $display("--- Test 5: Reset clears value ---");

        // addr 3 holds 0x7FFFFFFE. Reset it.
        @(posedge clk); #1;
        addr       = 12'd3;
        reset_cmd  = 1'b1;

        // Read back: reset_cmd also clears acc_out (read_out unconditional)
        @(posedge clk); #1;
        reset_cmd  = 1'b0;
        read_out   = 1'b1;

        @(posedge clk); #1;
        read_out   = 1'b0;
        check_output(32'd0, "Reset clears to 0");

        //===============================================================
        // Test 6: Multi-location accumulation (3 rounds, 4 addresses)
        //===============================================================
        $display("--- Test 6: Multi-location (3 rounds across 4 addrs) ---");

        // Round 1: accumulate initial values
        @(posedge clk); #1;
        addr       = 12'd10;
        acc_in     = 32'sd100;
        accumulate = 1'b1;

        @(posedge clk); #1;
        addr       = 12'd20;
        acc_in     = 32'sd200;

        @(posedge clk); #1;
        addr       = 12'd30;
        acc_in     = -32'sd50;

        @(posedge clk); #1;
        addr       = 12'd40;
        acc_in     = 32'h40000000;  // 1073741824

        // Round 2: add more
        @(posedge clk); #1;
        addr       = 12'd10;
        acc_in     = 32'sd200;      // 100+200=300

        @(posedge clk); #1;
        addr       = 12'd20;
        acc_in     = 32'sd300;      // 200+300=500

        @(posedge clk); #1;
        addr       = 12'd30;
        acc_in     = 32'sd100;      // -50+100=50

        @(posedge clk); #1;
        addr       = 12'd40;
        acc_in     = 32'h40000000;  // 1073741824*2=2147483648 (overflow by 1)

        // Round 3: final additions
        @(posedge clk); #1;
        addr       = 12'd10;
        acc_in     = 32'sd300;      // 300+300=600

        @(posedge clk); #1;
        addr       = 12'd20;
        acc_in     = -32'sd250;     // 500-250=250

        @(posedge clk); #1;
        addr       = 12'd30;
        acc_in     = -32'sd200;     // 50-200=-150

        @(posedge clk); #1;
        addr       = 12'd40;
        acc_in     = 32'sd1;        // already saturated to MAX, +1 stays MAX

        // Now read all back
        accumulate = 1'b0;

        // Read addr 10
        @(posedge clk); #1;
        addr     = 12'd10;
        read_out = 1'b1;
        @(posedge clk); #1;
        read_out = 1'b0;
        check_output(32'sd600, "Multi addr10=600");

        // Read addr 20
        @(posedge clk); #1;
        addr     = 12'd20;
        read_out = 1'b1;
        @(posedge clk); #1;
        read_out = 1'b0;
        check_output(32'sd250, "Multi addr20=250");

        // Read addr 30
        @(posedge clk); #1;
        addr     = 12'd30;
        read_out = 1'b1;
        @(posedge clk); #1;
        read_out = 1'b0;
        check_output(-32'sd150, "Multi addr30=-150");

        // Read addr 40 (should be INT32_MAX due to saturation)
        @(posedge clk); #1;
        addr     = 12'd40;
        read_out = 1'b1;
        @(posedge clk); #1;
        read_out = 1'b0;
        check_output(32'h7FFFFFFF, "Multi addr40=INT32_MAX");

        //===============================================================
        // Test 7: Global reset clears output register
        //===============================================================
        $display("--- Test 7: Global reset clears output register ---");
        @(posedge clk); #1;
        addr     = 12'd10;
        read_out = 1'b1;
        @(posedge clk); #1;
        read_out = 1'b0;
        check_output(32'sd600, "Before global rst, addr10");

        // Assert global reset
        @(posedge clk); #1;
        rst_n = 1'b0;
        #10;
        rst_n = 1'b1;

        // acc_out should be 0 after async reset
        @(posedge clk); #1;
        check_output(32'd0, "After global rst, acc_out=0");

        //===============================================================
        // Test 8: Near-limit values (no overflow)
        //===============================================================
        $display("--- Test 8: Near-limit no-overflow ---");
        @(posedge clk); #1;
        addr       = 12'd50;
        reset_cmd  = 1'b1;
        @(posedge clk); #1;
        reset_cmd  = 1'b0;
        acc_in     = 32'h7FFFFFFE;   // INT32_MAX - 1
        accumulate = 1'b1;
        @(posedge clk); #1;
        acc_in     = 32'sd1;         // (MAX-1)+1=MAX (no overflow)
        @(posedge clk); #1;
        accumulate = 1'b0;
        read_out   = 1'b1;
        @(posedge clk); #1;
        read_out   = 1'b0;
        check_output(32'h7FFFFFFF, "Near-limit MAX-1+1=MAX");

        //===============================================================
        // Summary
        //===============================================================
        $display("");
        $display("=== Test Summary ===");
        $display("Total:  %0d", total_tests);
        $display("Passed: %0d", passed_tests);
        $display("Failed: %0d", failed_tests);

        if (failed_tests == 0) begin
            $display("RESULT: ALL TESTS PASSED");
        end else begin
            $display("RESULT: %0d TESTS FAILED", failed_tests);
        end

        #20;
        $finish;
    end

endmodule
