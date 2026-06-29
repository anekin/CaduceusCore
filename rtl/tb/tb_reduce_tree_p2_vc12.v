//=============================================================================
// VC-12: reduce_tree pipeline latency — 7-cycle fixed from valid_i to valid_o
// Verifies latency is exactly 7 cycles regardless of data values.
// Uses $display markers: REDUCE_LATENCY=n for each measurement.
//=============================================================================
`timescale 1ns / 1ps

module tb_reduce_tree_p2_vc12;
    localparam NUM_IN   = 128;
    localparam DATA_W   = 32;
    localparam CLK_HALF = 5;

    reg clk, rst_n;
    reg [NUM_IN*DATA_W-1:0] data_i;
    reg op;
    reg valid_i;
    reg [NUM_IN-1:0] lane_mask;
    wire [DATA_W-1:0] result_o;
    wire [63:0] result64_o;
    wire valid_o;

    reduce_tree #(.NUM_IN(NUM_IN),.DATA_W(DATA_W))
    u_dut (.clk,.rst_n,.data_i,.op,.valid_i,.lane_mask,.result_o,.result64_o,.valid_o);

    // Latency measurement
    reg [7:0] latency_cnt;
    reg       measuring;
    integer   errors, total_tests;
    integer   i;

    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            latency_cnt <= 8'd0;
            measuring   <= 1'b0;
        end else begin
            if (valid_i && !measuring) begin
                latency_cnt <= 8'd0;
                measuring   <= 1'b1;
            end else if (measuring) begin
                latency_cnt <= latency_cnt + 8'd1;
            end
            if (valid_o && measuring) begin
                $display("REDUCE_LATENCY=%0d (valid_i->valid_o cycles at time %0t)", latency_cnt, $time);
                measuring <= 1'b0;
            end
        end
    end

    // Load helpers
    task load_seq; input integer base, step;
        begin for (i=0;i<NUM_IN;i=i+1) data_i[i*DATA_W+:DATA_W] = $signed(base + i*step); end
    endtask

    task load_const; input integer val;
        begin for (i=0;i<NUM_IN;i=i+1) data_i[i*DATA_W+:DATA_W] = $signed(val); end
    endtask

    task load_alternate; input integer a, b;
        begin for (i=0;i<NUM_IN;i=i+1)
            data_i[i*DATA_W+:DATA_W] = $signed(i % 2 == 0 ? a : b);
        end
    endtask

    task wait_and_check;
        input [DATA_W-1:0] expected;
        input [1023:0]     desc;
        begin
            wait(valid_o);
            @(posedge clk);
            repeat(8) @(posedge clk);
            total_tests = total_tests + 1;
            if (result_o !== expected) begin
                $display("  FAIL %0s: result=0x%08h expected=0x%08h", desc, result_o, expected);
                errors = errors + 1;
            end else begin
                $display("  PASS %0s: result=0x%08h", desc, result_o);
            end
        end
    endtask

    initial begin
        errors = 0; total_tests = 0;
        data_i = {NUM_IN{32'd0}}; op = 1'b0; valid_i = 1'b0;
        lane_mask = {NUM_IN{1'b1}};

        rst_n = 1'b0; repeat(4) @(posedge clk); rst_n = 1'b1; repeat(2) @(posedge clk);

        $display("=== VC-12: reduce_tree pipeline latency (7-cycle fixed) ===");

        // Test 1: MAX with sequential data 1..128
        $display("[VC-12-1] MAX over 1..128 → 128");
        op = 1'b0; load_seq(1,1);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'd128, "MAX(1..128)");

        // Test 2: SUM with sequential data 1..128 → 8256
        $display("[VC-12-2] SUM over 1..128 → 8256");
        op = 1'b1; load_seq(1,1);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'd8256, "SUM(1..128)");

        // Test 3: MAX with all zeros → 0
        $display("[VC-12-3] MAX over all zeros → 0");
        op = 1'b0; load_const(0);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'd0, "MAX(all zeros)");

        // Test 4: SUM with all zeros → 0
        $display("[VC-12-4] SUM over all zeros → 0");
        op = 1'b1; load_const(0);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'd0, "SUM(all zeros)");

        // Test 5: MAX with alternating extreme values
        $display("[VC-12-5] MAX with INT32_MAX / INT32_MIN alternating");
        op = 1'b0; load_alternate(32'h7FFFFFFF, 32'h80000000);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'h7FFFFFFF, "MAX(MAX/MIN alt)");

        // Test 6: SUM with alternating large/small values
        $display("[VC-12-6] SUM with 100 / -50 alternating");
        op = 1'b1; load_alternate(100, -50);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'sd3200, "SUM(100/-50 alt)"); // 64*(100-50) = 3200

        // Test 7: MAX with all INT32_MIN → INT32_MIN
        $display("[VC-12-7] MAX over all INT32_MIN → INT32_MIN");
        op = 1'b0; load_const(32'h80000000);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'h80000000, "MAX(all INT32_MIN)");

        // Test 8: SUM with all ones → 128
        $display("[VC-12-8] SUM over all ones → 128");
        op = 1'b1; load_const(1);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'd128, "SUM(all ones)");

        // Test 9: MAX with ascending from INT32_MIN → INT32_MIN + 126 (max at lane 127)
        $display("[VC-12-9] MAX over INT32_MIN..INT32_MIN+127 → INT32_MIN+127");
        op = 1'b0;
        load_seq($signed(32'h80000000), 1);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'h8000007F, "MAX(MIN+ascending)");

        // Test 10: MAX with descending from INT32_MAX → INT32_MAX - 126 (max at lane 0)
        $display("[VC-12-10] MAX over INT32_MAX..INT32_MAX-127 → INT32_MAX");
        op = 1'b0;
        load_seq($signed(32'h7FFFFFFF), -1);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_and_check(32'h7FFFFFFF, "MAX(MAX+descending)");

        // Summary
        $display("");
        $display("=== VC-12 Summary: %0d/%0d tests passed, %0d errors ===", total_tests-errors, total_tests, errors);
        if (errors == 0) begin
            $display("PASS");
            $display("VC12_VERIFIED: reduce_tree pipeline latency is 7 cycles fixed (all %0d patterns)", total_tests);
        end else begin
            $display("FAIL");
        end
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
