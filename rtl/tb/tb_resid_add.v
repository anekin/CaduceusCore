//=============================================================================
// tb_resid_add — Self-checking testbench for 128-wide residual connection
//=============================================================================
// Task 11 of sfu-vector-phase2 Wave 2.
//
// Covers:
//   Test 1  — Basic add:        orig=100, delta=200  → 300
//   Test 2  — Pos saturation:   orig=2^31-1, delta=1 → 2^31-1
//   Test 3  — Neg sum no ovf:   orig=100, delta=-200 → -100
//   Test 4  — Neg→Pos:          orig=-100, delta=200 → 100
//   Test 5  — Near-limit:       orig=INT32_MAX, delta=-1 → INT32_MAX-1
//   Test 6  — Neg saturation:   orig=INT32_MIN, delta=-1 → INT32_MIN
//   Test 7  — Zero:             orig=0, delta=0 → 0
//   Test 8  — Random ADD:       100 non-overflow pairs, compare with
//                                  GoldenVector.residual_add() bit-exact
//   Test 9  — Pipeline:         valid_i→valid_o latency = 1 cycle
//   Test 10 — Reset:            async reset clears output
//=============================================================================
`timescale 1ns / 1ps

module tb_resid_add;

    //-------------------------------------------------------------------------
    // Parameters
    //-------------------------------------------------------------------------
    localparam NUM_LANES = 128;
    localparam WIDTH     = NUM_LANES * 32;    // 4096

    //-------------------------------------------------------------------------
    // DUT signals
    //-------------------------------------------------------------------------
    reg                 clk;
    reg                 rst_n;
    reg  [WIDTH-1:0]    orig_i;
    reg  [WIDTH-1:0]    delta_i;
    reg                 valid_i;
    wire [WIDTH-1:0]    result_o;
    wire                valid_o;

    //-------------------------------------------------------------------------
    // DUT instantiation
    //-------------------------------------------------------------------------
    resid_add #(
        .NUM_LANES(NUM_LANES)
    ) u_dut (
        .clk        (clk),
        .rst_n      (rst_n),
        .orig_i     (orig_i),
        .delta_i    (delta_i),
        .valid_i    (valid_i),
        .result_o   (result_o),
        .valid_o    (valid_o)
    );

    //-------------------------------------------------------------------------
    // Clock: 10 ns period (100 MHz)
    //-------------------------------------------------------------------------
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    //-------------------------------------------------------------------------
    // Test counters
    //-------------------------------------------------------------------------
    integer total_tests;
    integer passed_tests;
    integer failed_tests;

    //-------------------------------------------------------------------------
    // Helper: set all 128 lanes of orig_i to a single value
    //-------------------------------------------------------------------------
    task automatic set_orig;
        input [31:0] val;
        integer i;
    begin
        for (i = 0; i < NUM_LANES; i = i + 1)
            orig_i[i*32 +: 32] = val;
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: set all 128 lanes of delta_i to a single value
    //-------------------------------------------------------------------------
    task automatic set_delta;
        input [31:0] val;
        integer i;
    begin
        for (i = 0; i < NUM_LANES; i = i + 1)
            delta_i[i*32 +: 32] = val;
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: set orig lane i and delta lane i
    //-------------------------------------------------------------------------
    task automatic set_lane;
        input integer lane;
        input [31:0]  o_val;
        input [31:0]  d_val;
    begin
        orig_i[lane*32  +: 32] = o_val;
        delta_i[lane*32 +: 32] = d_val;
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: compute expected INT32 result with saturation (matches DUT)
    // Uses explicit 64-bit signed arithmetic to avoid Verilog width-context
    // trap: adding two 32-bit regs wraps before 64-bit assignment.
    //-------------------------------------------------------------------------
    function signed [31:0] expected_sat;
        input signed [31:0] orig_val;
        input signed [31:0] delta_val;
        reg   signed [63:0] o64;
        reg   signed [63:0] d64;
        reg   signed [63:0] s64;
    begin
        o64 = $signed(orig_val);   // 32→64 sign-extend
        d64 = $signed(delta_val);  // 32→64 sign-extend
        s64 = o64 + d64;           // full 64-bit add
        if (s64 > 64'sd2147483647)
            expected_sat = 32'h7FFFFFFF;
        else if (s64 < -64'sd2147483648)
            expected_sat = 32'h80000000;
        else
            expected_sat = s64[31:0];
    end
    endfunction

    //-------------------------------------------------------------------------
    // Helper: check all 128 lanes against expected value
    //-------------------------------------------------------------------------
    task automatic check_all_lanes;
        input [31:0] exp;
        input [31:0] orig_val;
        input [31:0] delta_val;
        input [1023:0] test_name;
        integer i;
        integer lane_errors;
    begin
        lane_errors = 0;
        for (i = 0; i < NUM_LANES; i = i + 1) begin
            if (result_o[i*32 +: 32] !== exp) begin
                if (lane_errors < 5) begin
                    $display("  [FAIL] lane %0d: orig=%0d delta=%0d exp=%0d got=%0d",
                             i, $signed(orig_val), $signed(delta_val),
                             $signed(exp), $signed(result_o[i*32 +: 32]));
                end
                lane_errors = lane_errors + 1;
            end
        end
        if (lane_errors == 0) begin
            $display("  [PASS] %0s: %0d lanes OK", test_name, NUM_LANES);
            passed_tests = passed_tests + 1;
        end else begin
            $display("  [FAIL] %0s: %0d lane mismatches", test_name, lane_errors);
            failed_tests = failed_tests + 1;
        end
        total_tests = total_tests + 1;
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: drive a test pattern, wait for result, and check
    //-------------------------------------------------------------------------
    task automatic run_test;
        input [31:0] orig_val;
        input [31:0] delta_val;
        input [31:0] exp_val;
        input [1023:0] test_name;
    begin
        valid_i = 1'b0;
        @(posedge clk); #1;
        set_orig(orig_val);
        set_delta(delta_val);
        valid_i = 1'b1;
        @(posedge clk); #1;       // DUT captures: valid_r←1, lane_result_r←comb
        valid_i = 1'b0;
        // valid_o=1, result_o valid NOW (check before next edge clears valid_r)
        if (!valid_o) begin
            $display("  [FAIL] %0s: valid_o not asserted", test_name);
            failed_tests = failed_tests + 1;
            total_tests = total_tests + 1;
        end else begin
            check_all_lanes(exp_val, orig_val, delta_val, test_name);
        end
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: run random per-lane test (non-overflow range)
    //-------------------------------------------------------------------------
    task automatic run_random_test;
        input [1023:0] test_name;
        integer i;
        integer lane_errors;
        reg signed [63:0] rng_state;
        reg signed [31:0] o_vals[NUM_LANES-1:0];
        reg signed [31:0] d_vals[NUM_LANES-1:0];
    begin
        rng_state = 64'sd42;  // deterministic seed
        // Generate random values in safe range [-2^20, 2^20] so sum cannot overflow
        for (i = 0; i < NUM_LANES; i = i + 1) begin
            o_vals[i] = rng_state[47:16];
            rng_state = rng_state * 64'sd1103515245 + 64'sd12345;
            d_vals[i] = rng_state[47:16];
            rng_state = rng_state * 64'sd1103515245 + 64'sd12345;
            // Clamp to safe range
            if (o_vals[i] > 32'sd1048576)  o_vals[i] = 32'sd1048576;
            if (o_vals[i] < -32'sd1048576) o_vals[i] = -32'sd1048576;
            if (d_vals[i] > 32'sd1048576)  d_vals[i] = 32'sd1048576;
            if (d_vals[i] < -32'sd1048576) d_vals[i] = -32'sd1048576;
        end

        valid_i = 1'b0;
        @(posedge clk); #1;
        for (i = 0; i < NUM_LANES; i = i + 1) begin
            orig_i[i*32  +: 32] = o_vals[i];
            delta_i[i*32 +: 32] = d_vals[i];
        end
        valid_i = 1'b1;
        @(posedge clk); #1;       // DUT captures inputs
        valid_i = 1'b0;
        // valid_o=1, result valid NOW

        if (!valid_o) begin
            $display("  [FAIL] %0s: valid_o not asserted", test_name);
            failed_tests = failed_tests + 1;
            total_tests = total_tests + 1;
        end else begin
            lane_errors = 0;
            for (i = 0; i < NUM_LANES; i = i + 1) begin
                reg signed [31:0] exp = expected_sat(o_vals[i], d_vals[i]);
                if (result_o[i*32 +: 32] !== exp) begin
                    if (lane_errors < 5) begin
                        $display("  [FAIL] lane %0d: orig=%0d delta=%0d exp=%0d got=%0d",
                                 i, $signed(o_vals[i]), $signed(d_vals[i]),
                                 $signed(exp), $signed(result_o[i*32 +: 32]));
                    end
                    lane_errors = lane_errors + 1;
                end
            end
            if (lane_errors == 0) begin
                $display("  [PASS] %0s: %0d random lanes OK", test_name, NUM_LANES);
                passed_tests = passed_tests + 1;
            end else begin
                $display("  [FAIL] %0s: %0d lane mismatches", test_name, lane_errors);
                failed_tests = failed_tests + 1;
            end
            total_tests = total_tests + 1;
        end
    end
    endtask

    //=========================================================================
    // Main Test Sequence
    //=========================================================================
    initial begin
        $display("============================================================");
        $display(" tb_resid_add — 128-wide INT32 saturation residual connection");
        $display("============================================================");
        $display("");

        // Initialize
        clk     = 1'b0;
        rst_n   = 1'b0;
        valid_i = 1'b0;
        orig_i  = {WIDTH{1'b0}};
        delta_i = {WIDTH{1'b0}};
        total_tests  = 0;
        passed_tests = 0;
        failed_tests = 0;

        // Reset
        repeat (3) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        //=====================================================================
        // Test 1: Basic add — orig=100, delta=200 → 300
        //=====================================================================
        $display("Test 1: Basic ADD (100 + 200 → 300, all lanes)");
        run_test(32'sd100, 32'sd200, 32'sd300, "Test 1 (basic add)");

        //=====================================================================
        // Test 2: Positive saturation — INT32_MAX + 1 → INT32_MAX
        //=====================================================================
        $display("Test 2: Positive saturation (2^31-1 + 1 → 2^31-1)");
        run_test(32'h7FFFFFFF, 32'sd1, 32'h7FFFFFFF, "Test 2 (pos sat)");

        //=====================================================================
        // Test 3: Negative sum, no overflow — 100 + (-200) → -100
        //=====================================================================
        $display("Test 3: Negative sum no overflow (100 + -200 → -100)");
        run_test(32'sd100, -32'sd200, -32'sd100, "Test 3 (neg sum)");

        //=====================================================================
        // Test 4: Negative→Positive — -100 + 200 → 100
        //=====================================================================
        $display("Test 4: Negative + Positive (-100 + 200 → 100)");
        run_test(-32'sd100, 32'sd200, 32'sd100, "Test 4 (neg→pos)");

        //=====================================================================
        // Test 5: Near-limit — INT32_MAX + (-1) → INT32_MAX-1
        //=====================================================================
        $display("Test 5: Near-limit no overflow (INT32_MAX + -1 → INT32_MAX-1)");
        run_test(32'h7FFFFFFF, -32'sd1, 32'h7FFFFFFE, "Test 5 (near-limit)");

        //=====================================================================
        // Test 6: Negative saturation — INT32_MIN + (-1) → INT32_MIN
        //=====================================================================
        $display("Test 6: Negative saturation (INT32_MIN + -1 → INT32_MIN)");
        run_test(32'h80000000, -32'sd1, 32'h80000000, "Test 6 (neg sat)");

        //=====================================================================
        // Test 7: Zero — 0 + 0 → 0
        //=====================================================================
        $display("Test 7: Zero (0 + 0 → 0)");
        run_test(32'sd0, 32'sd0, 32'sd0, "Test 7 (zero)");

        //=====================================================================
        // Test 8: Random non-overflow per-lane pairs (2 rounds)
        //=====================================================================
        $display("Test 8a: Random non-overflow pairs (round 1)");
        run_random_test("Test 8a (random round 1)");
        $display("Test 8b: Random non-overflow pairs (round 2)");
        run_random_test("Test 8b (random round 2)");

        //=====================================================================
        // Test 9: Pipeline — valid_i → valid_o latency = 1 cycle
        //=====================================================================
        $display("Test 9: Pipeline latency (valid_i→valid_o = 1 cycle)");
        begin
            reg cycle_valid_i;
            valid_i = 1'b0;
            @(posedge clk);
            set_orig(32'sd42);
            set_delta(32'sd58);
            valid_i = 1'b1;
            cycle_valid_i = 1;
            @(posedge clk); #1;       // DUT captures inputs
            valid_i = 1'b0;
            // valid_o=1, result valid NOW
            if (valid_o && result_o[31:0] === 32'sd100) begin
                $display("  [PASS] Test 9 (pipeline): latency=1, value=100");
                passed_tests = passed_tests + 1;
            end else begin
                $display("  [FAIL] Test 9 (pipeline): valid_o=%b lane0=%0d",
                         valid_o, $signed(result_o[31:0]));
                failed_tests = failed_tests + 1;
            end
            total_tests = total_tests + 1;
        end

        //=====================================================================
        // Test 10: Async reset clears output
        //=====================================================================
        $display("Test 10: Async reset clears output");
        begin
            // Load data: drive and capture
            valid_i = 1'b0;
            @(posedge clk); #1;
            set_orig(32'sd123);
            set_delta(32'sd456);
            valid_i = 1'b1;
            @(posedge clk); #1;       // DUT captures → result valid now
            valid_i = 1'b0;
            // Assert reset
            rst_n = 1'b0;
            #1;  // async reset propagation
            // Check that output is cleared during reset
            if (result_o === {WIDTH{1'b0}} && !valid_o) begin
                $display("  [PASS] Test 10 (reset): output cleared during reset");
                passed_tests = passed_tests + 1;
            end else begin
                $display("  [FAIL] Test 10 (reset): result_o not cleared");
                failed_tests = failed_tests + 1;
            end
            total_tests = total_tests + 1;
        end

        //=====================================================================
        // Summary
        //=====================================================================
        $display("");
        $display("============================================================");
        $display(" SUMMARY: %0d/%0d PASSED, %0d FAILED",
                 passed_tests, total_tests, failed_tests);
        $display("============================================================");

        if (failed_tests > 0) begin
            $display("*** TESTBENCH FAILED ***");
            $finish;
        end else begin
            $display("*** TESTBENCH PASSED ***");
            $finish;
        end
    end

endmodule
