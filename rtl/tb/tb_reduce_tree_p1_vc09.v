//=============================================================================
// VC-09: reduce_tree SUM INT64 accumulation — per-chunk INT64 sum before
// final INT32 saturation. Verifies result64_o holds full INT64 accuracy.
//=============================================================================
`timescale 1ns / 1ps

module tb_reduce_tree_p1_vc09;
    localparam NUM_IN  = 128;
    localparam DATA_W  = 32;
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

    integer errors, i;
    reg signed [63:0] expected_sum64;

    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    task load_const; input integer val; integer i; begin
        for (i=0;i<NUM_IN;i=i+1) data_i[i*DATA_W+:DATA_W] = $signed(val);
    end endtask

    task wait_valid; begin
        wait(valid_o); @(posedge clk); repeat(5) @(posedge clk);
    end endtask

    initial begin
        $display("=== VC-09: reduce_tree SUM INT64 accumulation ===");
        errors=0;
        data_i={NUM_IN{32'd0}}; op=0; valid_i=0; lane_mask={NUM_IN{1'b1}};
        rst_n=0; repeat(4) @(posedge clk); rst_n=1; repeat(2) @(posedge clk);

        // Test 1: 128 × INT32_MAX → result64_o = 128*INT32_MAX (INT64 holds it)
        //   Expected sum: 128 * 2_147_483_647 = 274_877_906_816 = 0x3F_FFFFFF80
        $display("[VC-09] Test 1: 128 INT32_MAX → INT64 accumulation...");
        expected_sum64 = 64'sd2147483647 * 128;  // 274,877,906,816
        $display("[VC-09] Expected INT64 sum = %0d (0x%016h)", expected_sum64, expected_sum64);
        op = 1'b1;  // SUM
        load_const(32'h7FFFFFFF);
        valid_i = 1'b1; @(posedge clk); valid_i = 1'b0;
        wait_valid;

        if (result64_o !== expected_sum64) begin
            $display("  FAIL: result64_o=0x%016h (%0d), expected=0x%016h (%0d)",
                     result64_o, $signed(result64_o), expected_sum64, expected_sum64);
            errors=errors+1;
        end else $display("  PASS: result64_o matches expected INT64 sum");

        if (result_o !== 32'h7FFFFFFF) begin
            $display("  FAIL: result_o=0x%08h, expected=INT32_MAX (saturated)", result_o);
            errors=errors+1;
        end else $display("  PASS: result_o saturated to INT32_MAX");

        // Test 2: Chunk sum that fits in INT32 — verify bit-exact
        //   128 values 1..128 → sum = 8256
        $display("[VC-09] Test 2: 128 sequential values 1..128 → sum=8256...");
        begin
            for (i=0;i<NUM_IN;i=i+1) data_i[i*DATA_W+:DATA_W] = $signed(i+1);
        end
        op = 1'b1; lane_mask={NUM_IN{1'b1}};
        valid_i=1'b1; @(posedge clk); valid_i=1'b0;
        wait_valid;
        if (result64_o !== 64'sd8256) begin
            $display("  FAIL: result64_o=%0d, expected=8256", $signed(result64_o));
            errors=errors+1;
        end else $display("  PASS: result64_o=%0d", $signed(result64_o));
        if (result_o !== 32'sd8256) begin
            $display("  FAIL: result_o=%0d, expected=8256", $signed(result_o));
            errors=errors+1;
        end else $display("  PASS: result_o=%0d", $signed(result_o));

        // Test 3: INT32_MIN × 128 → negative overflow, saturate to INT32_MIN
        $display("[VC-09] Test 3: 128 INT32_MIN → INT32_MIN saturated...");
        expected_sum64 = -64'sd2147483648 * 128;  // -274,877,906,944 = 0xFFFFFFC0_00000080
        $display("[VC-09] Expected INT64 sum = %0d (0x%016h)", expected_sum64, expected_sum64);
        op = 1'b1;
        load_const(32'h80000000);
        valid_i=1'b1; @(posedge clk); valid_i=1'b0;
        wait_valid;
        if (result64_o !== expected_sum64) begin
            $display("  FAIL: result64_o=0x%016h (%0d), expected=0x%016h (%0d)",
                     result64_o, $signed(result64_o), expected_sum64, expected_sum64);
            errors=errors+1;
        end else $display("  PASS: result64_o matches expected INT64 sum");
        if (result_o !== 32'h80000000) begin
            $display("  FAIL: result_o=0x%08h, expected=INT32_MIN", result_o);
            errors=errors+1;
        end else $display("  PASS: result_o saturated to INT32_MIN");

        // Test 4: Mixed large values that cause chunk overflow but different from Test 1
        //   64 × INT32_MAX + 64 × 1 = 64*INT32_MAX + 64
        //   = 137,438,953,408 = just under 2^37, still fits in INT64, saturated at output
        $display("[VC-09] Test 4: 64×INT32_MAX + 64×1...");
        begin
            for (i=0;i<64;i=i+1) data_i[i*DATA_W+:DATA_W] = 32'h7FFFFFFF;
            for (i=64;i<128;i=i+1) data_i[i*DATA_W+:DATA_W] = 32'sd1;
        end
        expected_sum64 = 64'sd2147483647 * 64 + 64;
        $display("[VC-09] Expected INT64 sum = %0d", expected_sum64);
        op = 1'b1;
        valid_i=1'b1; @(posedge clk); valid_i=1'b0;
        wait_valid;
        if (result64_o !== expected_sum64) begin
            $display("  FAIL: result64_o=%0d, expected=%0d", $signed(result64_o), expected_sum64);
            errors=errors+1;
        end else $display("  PASS: result64_o matches (mixed overflow)");
        if (result_o !== 32'h7FFFFFFF) begin
            $display("  FAIL: result_o=0x%08h, expected=INT32_MAX", result_o);
            errors=errors+1;
        end else $display("  PASS: result_o saturated to INT32_MAX");

        // Test 5: Partial lane_mask on overflow test
        //   128 × INT32_MAX with only low 64 lanes enabled → 
        //   result64_o = 64*INT32_MAX, result_o = INT32_MAX (still saturates)
        $display("[VC-09] Test 5: 128×INT32_MAX with only 64 lanes enabled...");
        expected_sum64 = 64'sd2147483647 * 64;
        $display("[VC-09] Expected INT64 sum = %0d", expected_sum64);
        lane_mask = {{64{1'b0}},{64{1'b1}}};
        load_const(32'h7FFFFFFF);
        valid_i=1'b1; @(posedge clk); valid_i=1'b0;
        wait_valid;
        if (result64_o !== expected_sum64) begin
            $display("  FAIL: result64_o=%0d, expected=%0d", $signed(result64_o), expected_sum64);
            errors=errors+1;
        end else $display("  PASS: result64_o=%0d (64 lanes of INT32_MAX)", $signed(result64_o));
        if (result_o !== 32'h7FFFFFFF) begin
            $display("  FAIL: result_o=0x%08h, expected=INT32_MAX", result_o);
            errors=errors+1;
        end else $display("  PASS: result_o saturated to INT32_MAX");

        // Summary
        if (errors==0) $display("PASS: VC-09 all tests passed");
        else $display("FAIL: VC-09 %0d errors", errors);
        if (errors==0) $display("PASS"); else $display("FAIL");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
