//=============================================================================
// VC-10: resid_add overflow — original=INT32_MAX + delta=1 → INT32_MAX
// (saturated, not wrapped). Also INT32_MIN + (-1) → INT32_MIN.
// Per-lane verification across all 128 lanes.
//=============================================================================
`timescale 1ns / 1ps

module tb_resid_add_p1_vc10;
    localparam NUM_LANES = 128;
    localparam WIDTH     = NUM_LANES * 32;

    reg clk, rst_n;
    reg [WIDTH-1:0] orig_i, delta_i;
    reg valid_i;
    wire [WIDTH-1:0] result_o;
    wire valid_o;

    resid_add #(.NUM_LANES(NUM_LANES)) u_dut (.clk,.rst_n,.orig_i,.delta_i,.valid_i,.result_o,.valid_o);

    integer errors, total, i;
    reg signed [63:0] o64, d64, s64;
    reg signed [31:0] expected_val;

    initial clk = 1'b0;
    always #5 clk = ~clk;

    // Expected saturated result computation matching DUT logic
    function signed [31:0] exp_sat;
        input signed [31:0] o, d;
        reg signed [63:0] s;
    begin
        s = $signed(o) + $signed(d);
        if (s > 64'sd2147483647) exp_sat = 32'h7FFFFFFF;
        else if (s < -64'sd2147483648) exp_sat = 32'h80000000;
        else exp_sat = s[31:0];
    end endfunction

    task set_orig; input [31:0] v; integer k; begin
        for (k=0;k<NUM_LANES;k=k+1) orig_i[k*32+:32] = v;
    end endtask

    task set_delta; input [31:0] v; integer k; begin
        for (k=0;k<NUM_LANES;k=k+1) delta_i[k*32+:32] = v;
    end endtask

    task check_all; input [1023:0] desc; input [31:0] expected; integer k; begin
        for (k=0;k<NUM_LANES;k=k+1) begin
            total=total+1;
            if (result_o[k*32+:32] !== expected) begin
                $display("  FAIL %0s lane=%0d: got=0x%08h (%0d) expected=0x%08h (%0d)",
                         desc, k, result_o[k*32+:32], $signed(result_o[k*32+:32]),
                         expected, $signed(expected));
                errors=errors+1;
                if (errors>5) k=NUM_LANES;
            end
        end
    end endtask

    task run_and_check; input [31:0] o, d; input [1023:0] desc; begin
        valid_i=0; @(posedge clk); #1;
        set_orig(o); set_delta(d);
        valid_i=1; @(posedge clk); #1; valid_i=0;
        if (!valid_o) begin $display("  FAIL valid_o not asserted"); errors=errors+1; end
        else check_all(desc, exp_sat(o,d));
    end endtask

    initial begin
        $display("=== VC-10: resid_add overflow saturation (not wrapped) ===");
        errors=0; total=0;
        valid_i=0; orig_i={WIDTH{1'b0}}; delta_i={WIDTH{1'b0}};
        rst_n=0; repeat(5) @(posedge clk); rst_n=1; repeat(3) @(posedge clk);

        // Test 1: INT32_MAX + 1 → INT32_MAX (saturated, NOT wrap to INT32_MIN)
        $display("[VC-10] Test 1: INT32_MAX + 1 → INT32_MAX (saturated)...");
        run_and_check(32'h7FFFFFFF, 32'sd1, "pos_ovf");

        // Test 2: INT32_MAX + 100 → INT32_MAX (saturated)
        $display("[VC-10] Test 2: INT32_MAX + 100 → INT32_MAX (saturated)...");
        run_and_check(32'h7FFFFFFF, 32'sd100, "pos_ovf2");

        // Test 3: INT32_MIN + (-1) → INT32_MIN (saturated, NOT wrap to INT32_MAX)
        $display("[VC-10] Test 3: INT32_MIN + (-1) → INT32_MIN (saturated)...");
        run_and_check(32'h80000000, -32'sd1, "neg_ovf");

        // Test 4: INT32_MIN + (-100) → INT32_MIN (saturated)
        $display("[VC-10] Test 4: INT32_MIN + (-100) → INT32_MIN (saturated)...");
        run_and_check(32'h80000000, -32'sd100, "neg_ovf2");

        // Test 5: Near-limit no overflow: INT32_MAX + (-1) → INT32_MAX-1
        $display("[VC-10] Test 5: INT32_MAX + (-1) → INT32_MAX-1 (no sat)...");
        run_and_check(32'h7FFFFFFF, -32'sd1, "near_limit");

        // Test 6: Near-limit no underflow: INT32_MIN + 1 → INT32_MIN+1
        $display("[VC-10] Test 6: INT32_MIN + 1 → INT32_MIN+1 (no sat)...");
        run_and_check(32'h80000000, 32'sd1, "near_under");

        // Test 7: Large positive overflow: INT32_MAX + INT32_MAX → INT32_MAX
        $display("[VC-10] Test 7: INT32_MAX + INT32_MAX → INT32_MAX (doubly saturated)...");
        run_and_check(32'h7FFFFFFF, 32'h7FFFFFFF, "double_pos");

        // Test 8: Large negative underflow: INT32_MIN + INT32_MIN → INT32_MIN
        $display("[VC-10] Test 8: INT32_MIN + INT32_MIN → INT32_MIN (doubly saturated)...");
        run_and_check(32'h80000000, 32'h80000000, "double_neg");

        // Test 9: Zero + 0 → 0 (no change)
        $display("[VC-10] Test 9: 0 + 0 → 0...");
        run_and_check(32'sd0, 32'sd0, "zero");

        // Test 10: Mixed per-lane values — some saturate, some don't
        $display("[VC-10] Test 10: Mixed per-lane (some saturate, some not)...");
        begin
            integer k;
            valid_i=0; @(posedge clk); #1;
            for (k=0;k<NUM_LANES;k=k+1) begin
                if (k%4==0) begin orig_i[k*32+:32]=32'h7FFFFFFF; delta_i[k*32+:32]=32'sd1; end
                else if (k%4==1) begin orig_i[k*32+:32]=32'sd100; delta_i[k*32+:32]=32'sd200; end
                else if (k%4==2) begin orig_i[k*32+:32]=32'h80000000; delta_i[k*32+:32]=-32'sd1; end
                else begin orig_i[k*32+:32]=32'sd999; delta_i[k*32+:32]=32'sd1; end
            end
            valid_i=1; @(posedge clk); #1; valid_i=0;
            if (!valid_o) begin $display("  FAIL valid_o not asserted"); errors=errors+1; end
            else begin
                for (k=0;k<NUM_LANES;k=k+1) begin
                    total=total+1;
                    if (k%4==0) expected_val = 32'h7FFFFFFF;           // saturated
                    else if (k%4==1) expected_val = 32'sd300;          // normal
                    else if (k%4==2) expected_val = 32'h80000000;      // saturated
                    else expected_val = 32'sd1000;                     // normal
                    if (result_o[k*32+:32] !== expected_val) begin
                        $display("  FAIL lane=%0d: got=%0d expected=%0d",
                                 k, $signed(result_o[k*32+:32]), $signed(expected_val));
                        errors=errors+1;
                        if (errors>5) k=NUM_LANES;
                    end
                end
            end
        end

        // Test 11: Ensure wrapping does NOT occur (negative check)
        //   INT32_MAX + 1 → if it wrapped, it would be INT32_MIN
        $display("[VC-10] Test 11: Explicit anti-wrap check (INT32_MAX+1 != INT32_MIN)...");
        begin integer k; valid_i=0; @(posedge clk); #1;
            set_orig(32'h7FFFFFFF); set_delta(32'sd1);
            valid_i=1; @(posedge clk); #1; valid_i=0;
            for (k=0;k<NUM_LANES;k=k+1) begin
                total=total+1;
                if (result_o[k*32+:32] === 32'h80000000) begin
                    $display("  FAIL anti-wrap lane=%0d: result wrapped to INT32_MIN!", k);
                    errors=errors+1;
                end else if (result_o[k*32+:32] !== 32'h7FFFFFFF) begin
                    $display("  FAIL anti-wrap lane=%0d: unexpected value 0x%08h", k, result_o[k*32+:32]);
                    errors=errors+1;
                end
            end
        end

        if (errors==0) $display("PASS: VC-10 all tests passed (overflow saturated, no wrap)");
        else $display("FAIL: VC-10 %0d errors in %0d checks", errors, total);
        if (errors==0) $display("PASS"); else $display("FAIL");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
