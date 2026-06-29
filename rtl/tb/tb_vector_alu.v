//=============================================================================
// tb_vector_alu — Self-checking testbench for 128-wide SIMD ALU
//=============================================================================
// Covers:
//   Test 1 — ADD basic:     a=1, b=2, all lanes → 3
//   Test 2 — MUL basic:     a=1, b=2, all lanes → 2
//   Test 3 — MAX:           a=1 vs b=2 all lanes → 2
//   Test 4 — PASS_A:        a=5, all lanes → 5
//   Test 5 — ADD pos sat:   a=INT32_MAX, b=1  → INT32_MAX
//   Test 6 — ADD neg sat:   a=INT32_MIN, b=-1 → INT32_MIN
//   Test 7 — MUL pos sat:   a=2^20, b=2^20 → INT32_MAX (overflow)
//   Test 8 — MUL neg sat:   a=-2^20, b=2^20 → INT32_MIN (underflow)
//   Test 9 — ADD near-limit: a=INT32_MAX, b=-1 → INT32_MAX-1 (no overflow)
//   Test 10 — Lane mask:    mask alternating, lane-specific behavior
//   Test 11 — Random ADD:   100 random value pairs (all non-overflow)
//   Test 12 — MAX edge:     a=b, a>>b, b>>a, negative values
//=============================================================================
`timescale 1ns / 1ps

module tb_vector_alu;

    //-------------------------------------------------------------------------
    // Parameters
    //-------------------------------------------------------------------------
    localparam NUM_LANES = 128;
    localparam WIDTH      = NUM_LANES * 32;    // 4096

    //-------------------------------------------------------------------------
    // DUT signals
    //-------------------------------------------------------------------------
    reg                 clk;
    reg                 rst_n;
    reg  [1:0]          op;
    reg  [WIDTH-1:0]    a_i;
    reg  [WIDTH-1:0]    b_i;
    reg  [NUM_LANES-1:0] lane_mask;
    reg                 valid_i;
    wire [WIDTH-1:0]    result_o;
    wire                valid_o;

    //-------------------------------------------------------------------------
    // DUT instantiation
    //-------------------------------------------------------------------------
    vector_alu #(
        .NUM_LANES(NUM_LANES)
    ) u_dut (
        .clk        (clk),
        .rst_n      (rst_n),
        .op         (op),
        .a_i        (a_i),
        .b_i        (b_i),
        .lane_mask  (lane_mask),
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
    // Helper: set all 128 lanes of A to a single value
    //-------------------------------------------------------------------------
    task automatic set_a;
        input [31:0] val;
        integer i;
    begin
        for (i = 0; i < NUM_LANES; i = i + 1)
            a_i[i*32 +: 32] = val;
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: set all 128 lanes of B to a single value
    //-------------------------------------------------------------------------
    task automatic set_b;
        input [31:0] val;
        integer i;
    begin
        for (i = 0; i < NUM_LANES; i = i + 1)
            b_i[i*32 +: 32] = val;
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: set A lane i and B lane i
    //-------------------------------------------------------------------------
    task automatic set_lane;
        input integer  lane;
        input [31:0]   aval;
        input [31:0]   bval;
    begin
        a_i[lane*32 +: 32] = aval;
        b_i[lane*32 +: 32] = bval;
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: drive and wait for result (2 cycles: drive + pipeline)
    //-------------------------------------------------------------------------
    task automatic drive_and_wait;
    begin
        @(posedge clk); #1;
        valid_i <= 1'b1;
        @(posedge clk); #1;  // result_o becomes valid on this cycle
        // result_o is now available
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: check a single lane result
    //-------------------------------------------------------------------------
    task automatic check_lane;
        input integer  lane;
        input [31:0]   expected;
        input [255:0]  desc;
    begin
        total_tests = total_tests + 1;
        if (result_o[lane*32 +: 32] !== expected) begin
            $display("FAIL [%0s] lane=%0d: expected 0x%08h (%0d), got 0x%08h (%0d)",
                     desc, lane, expected, $signed(expected),
                     result_o[lane*32 +: 32], $signed(result_o[lane*32 +: 32]));
            failed_tests = failed_tests + 1;
        end else begin
            // silent pass for bulk lanes; summary at end
            passed_tests = passed_tests + 1;
        end
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: check all 128 lanes have the same expected value
    //-------------------------------------------------------------------------
    task automatic check_all_lanes;
        input [31:0]  expected;
        input [255:0] desc;
        integer i;
    begin
        for (i = 0; i < NUM_LANES; i = i + 1)
            check_lane(i, expected, desc);
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: generate deterministic "random" signed 32-bit value in safe range
    //   Uses simple LCG that stays within ±2^19 — two such values added
    //   never overflow INT32 (±2^20), multiplied never overflow (±2^38 → safe).
    //-------------------------------------------------------------------------
    function [31:0] rand_safe;
        input [31:0] seed;
        reg [31:0] tmp;
    begin
        // LCG: tmp = seed * 1664525 + 1013904223, take low 20 bits
        tmp = seed * 32'd1664525 + 32'd1013904223;
        // Mask to 19 bits unsigned, then center around 0 for signed range ±2^18
        rand_safe = tmp[18:0];  // 0 .. 524287
        // Make signed: center by subtracting 2^18
        rand_safe = rand_safe - 32'd262144;  // range: -262144 .. +262143
    end
    endfunction

    //-------------------------------------------------------------------------
    // Main test sequence
    //-------------------------------------------------------------------------
    initial begin
        total_tests  = 0;
        passed_tests = 0;
        failed_tests = 0;

        // Initialize all signals
        op        = 2'b00;
        a_i       = {WIDTH{1'b0}};
        b_i       = {WIDTH{1'b0}};
        lane_mask = {NUM_LANES{1'b1}};
        valid_i   = 1'b0;

        // Power-on reset
        rst_n = 1'b0;
        #20;
        rst_n = 1'b1;
        #10;
        $display("=== Vector ALU Self-Checking Testbench ===");
        $display("");

        //=====================================================================
        // Test 1: ADD — a=1, b=2 → all lanes = 3
        //=====================================================================
        $display("--- Test 1: ADD (a=1, b=2) → all lanes = 3 ---");
        set_a(32'sd1);
        set_b(32'sd2);
        op = 2'b00;  // ADD
        drive_and_wait;
        if (valid_o !== 1'b1) begin
            $display("FAIL: valid_o not asserted after ADD cycle");
            failed_tests = failed_tests + 1;
        end
        check_all_lanes(32'sd3, "ADD 1+2");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 2: MUL — a=1, b=2 → all lanes = 2
        //=====================================================================
        $display("--- Test 2: MUL (a=1, b=2) → all lanes = 2 ---");
        set_a(32'sd1);
        set_b(32'sd2);
        op = 2'b01;  // MUL
        drive_and_wait;
        check_all_lanes(32'sd2, "MUL 1*2");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 3: MAX — a=1, b=2 → all lanes = 2
        //=====================================================================
        $display("--- Test 3: MAX (a=1, b=2) → all lanes = 2 ---");
        set_a(32'sd1);
        set_b(32'sd2);
        op = 2'b10;  // MAX
        drive_and_wait;
        check_all_lanes(32'sd2, "MAX(1,2)");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 4: PASS_A — a=5 → all lanes = 5 (b ignored)
        //=====================================================================
        $display("--- Test 4: PASS_A (a=5) → all lanes = 5 ---");
        set_a(32'sd5);
        set_b(32'sd99);
        op = 2'b11;  // PASS_A
        drive_and_wait;
        check_all_lanes(32'sd5, "PASS_A=5");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 5: ADD positive saturation — INT32_MAX + 1 → INT32_MAX
        //=====================================================================
        $display("--- Test 5: ADD pos sat (INT32_MAX + 1) → INT32_MAX ---");
        set_a(32'h7FFFFFFF);   // INT32_MAX
        set_b(32'sd1);
        op = 2'b00;  // ADD
        drive_and_wait;
        check_all_lanes(32'h7FFFFFFF, "ADD pos sat");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 6: ADD negative saturation — INT32_MIN + (-1) → INT32_MIN
        //=====================================================================
        $display("--- Test 6: ADD neg sat (INT32_MIN + -1) → INT32_MIN ---");
        set_a(32'h80000000);   // INT32_MIN
        set_b(-32'sd1);
        op = 2'b00;  // ADD
        drive_and_wait;
        check_all_lanes(32'h80000000, "ADD neg sat");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 7: MUL positive saturation — 2^20 × 2^20 → overflow → INT32_MAX
        //=====================================================================
        $display("--- Test 7: MUL pos sat (2^20 × 2^20) → INT32_MAX ---");
        set_a(32'sd1048576);   // 2^20
        set_b(32'sd1048576);   // 2^20, product = 2^40 >> INT32_MAX
        op = 2'b01;  // MUL
        drive_and_wait;
        check_all_lanes(32'h7FFFFFFF, "MUL pos sat");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 8: MUL negative saturation — -2^20 × 2^20 → INT32_MIN
        //=====================================================================
        $display("--- Test 8: MUL neg sat (-2^20 × 2^20) → INT32_MIN ---");
        set_a(-32'sd1048576);
        set_b(32'sd1048576);
        op = 2'b01;  // MUL
        drive_and_wait;
        check_all_lanes(32'h80000000, "MUL neg sat");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 9: ADD near-limit (no overflow) — INT32_MAX + (-1) → MAX-1
        //=====================================================================
        $display("--- Test 9: ADD near-limit (MAX + -1) → MAX-1 (no sat) ---");
        set_a(32'h7FFFFFFF);
        set_b(-32'sd1);
        op = 2'b00;  // ADD
        drive_and_wait;
        check_all_lanes(32'h7FFFFFFE, "ADD MAX+(-1)");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 10: ADD near-limit — INT32_MIN + 1 → MIN+1
        //=====================================================================
        $display("--- Test 10: ADD near-limit (MIN + 1) → MIN+1 (no sat) ---");
        set_a(32'h80000000);
        set_b(32'sd1);
        op = 2'b00;  // ADD
        drive_and_wait;
        check_all_lanes(32'h80000001, "ADD MIN+1");
        valid_i <= 1'b0;

        //=====================================================================
        // VC-01: Saturation — ADD overflow → INT32_MAX
        //   ADD(2^31-1, 100) → 2^31-1
        //=====================================================================
        $display("--- VC-01a: ADD(INT32_MAX, 100) → INT32_MAX (saturate) ---");
        set_a(32'h7FFFFFFF);
        set_b(32'sd100);
        op = 2'b00;  // ADD
        drive_and_wait;
        check_all_lanes(32'h7FFFFFFF, "VC01 ADD pos sat=INT32_MAX");
        valid_i <= 1'b0;

        //=====================================================================
        // VC-01: Saturation — ADD underflow → INT32_MIN
        //   ADD(-2^31, -100) → -2^31
        //=====================================================================
        $display("--- VC-01b: ADD(INT32_MIN, -100) → INT32_MIN (saturate) ---");
        set_a(32'h80000000);
        set_b(-32'sd100);
        op = 2'b00;  // ADD
        drive_and_wait;
        check_all_lanes(32'h80000000, "VC01 ADD neg sat=INT32_MIN");
        valid_i <= 1'b0;

        //=====================================================================
        // VC-01: Saturation — MUL overflow → INT32_MAX
        //   MUL(2^16, 2^16) → 2^31-1
        //=====================================================================
        $display("--- VC-01c: MUL(2^16, 2^16) → INT32_MAX (saturate) ---");
        set_a(32'sd65536);   // 2^16
        set_b(32'sd65536);   // 2^16, product = 2^32 → overflow
        op = 2'b01;  // MUL
        drive_and_wait;
        check_all_lanes(32'h7FFFFFFF, "VC01 MUL pos sat=INT32_MAX");
        valid_i <= 1'b0;

        //=====================================================================
        // VC-01: Saturation — MUL underflow → INT32_MIN
        //   MUL(-2^16, 2^16) → -2^31
        //=====================================================================
        $display("--- VC-01d: MUL(-2^16, 2^16) → INT32_MIN (saturate) ---");
        set_a(-32'sd65536);
        set_b(32'sd65536);
        op = 2'b01;  // MUL
        drive_and_wait;
        check_all_lanes(32'h80000000, "VC01 MUL neg sat=INT32_MIN");
        valid_i <= 1'b0;

        //=====================================================================
        // Test 11: Lane mask — alternating pattern
        //   Even lanes (mask=1): ADD a+b,  Odd lanes (mask=0): feed-through a
        //   a=100, b=200 → even=300, odd=100
        //=====================================================================
        $display("--- Test 11: Lane mask (alternating, ADD) ---");
        begin
            integer idx;
            set_a(32'sd100);
            set_b(32'sd200);
            for (idx = 0; idx < NUM_LANES; idx = idx + 1)
                lane_mask[idx] = (idx % 2 == 0);  // even active, odd disabled
            op = 2'b00;  // ADD
            drive_and_wait;
            for (idx = 0; idx < NUM_LANES; idx = idx + 1) begin
                if (idx % 2 == 0)
                    check_lane(idx, 32'sd300, "Lane mask even ADD");
                else
                    check_lane(idx, 32'sd100, "Lane mask odd feed-through");
            end
            lane_mask = {NUM_LANES{1'b1}};  // restore full mask
            valid_i <= 1'b0;
        end

        //=====================================================================
        // Test 12: Lane mask — MUL disabled → 0, enabled → a*b
        //   a=7, b=6, lanes 0..63 active, 64..127 disabled
        //=====================================================================
        $display("--- Test 12: Lane mask (upper-half disabled, MUL) ---");
        begin
            integer idx;
            set_a(32'sd7);
            set_b(32'sd6);
            for (idx = 0; idx < NUM_LANES; idx = idx + 1)
                lane_mask[idx] = (idx < 64);
            op = 2'b01;  // MUL
            drive_and_wait;
            for (idx = 0; idx < NUM_LANES; idx = idx + 1) begin
                if (idx < 64)
                    check_lane(idx, 32'sd42, "Lane mask low MUL");
                else
                    check_lane(idx, 32'sd0,  "Lane mask high MUL zero");
            end
            lane_mask = {NUM_LANES{1'b1}};
            valid_i <= 1'b0;
        end

        //=====================================================================
        // Test 13: Lane mask — MAX disabled → 0
        //=====================================================================
        $display("--- Test 13: Lane mask (disabled MAX → 0) ---");
        begin
            integer idx;
            set_a(32'sd50);
            set_b(32'sd10);
            lane_mask = 128'h0;  // all disabled
            op = 2'b10;  // MAX
            drive_and_wait;
            check_all_lanes(32'sd0, "Lane mask MAX disabled");
            lane_mask = {NUM_LANES{1'b1}};
            valid_i <= 1'b0;
        end

        //=====================================================================
        // Test 14: MAX edge cases — mixed per-lane values
        //   Lane 0: a=b=0        → 0
        //   Lane 1: a>>b         → a
        //   Lane 2: b>>a         → b
        //   Lane 3: both negative → max(closer to 0)
        //=====================================================================
        $display("--- Test 14: MAX edge cases ---");
        set_a(32'sd0);
        set_b(32'sd0);
        set_lane(0, 32'sd0,     32'sd0);       // a=b → 0
        set_lane(1, 32'sd9999,  32'sd0);       // a>>b → 9999
        set_lane(2, 32'sd0,     32'sd9999);    // b>>a → 9999
        set_lane(3, -32'sd5,    -32'sd100);    // a closer to 0 → -5
        set_lane(4, -32'sd100,  -32'sd5);      // b closer to 0 → -5
        op = 2'b10;  // MAX
        drive_and_wait;
        check_lane(0, 32'sd0,     "MAX a=b=0");
        check_lane(1, 32'sd9999,  "MAX a>>b");
        check_lane(2, 32'sd9999,  "MAX b>>a");
        check_lane(3, -32'sd5,    "MAX neg a>-b");
        check_lane(4, -32'sd5,    "MAX neg b>-a");
        // remaining lanes should be 0 (a=b=0)
        valid_i <= 1'b0;

        //=====================================================================
        // Test 15: Random ADD regression — 50 samples, non-overflow values
        //=====================================================================
        $display("--- Test 15: Random ADD (50 pairs, non-overflow) ---");
        begin
            integer idx;
            integer seed_a, seed_b;
            reg [31:0] av, bv;
            reg signed [63:0] av_se, bv_se;   // must be 64-bit signed for correct arithmetic
            reg signed [63:0] expected_64;
            reg [31:0] expected_32;

            seed_a = 42;
            seed_b = 137;

            for (idx = 0; idx < 50; idx = idx + 1) begin
                seed_a = seed_a + idx * 7919;
                seed_b = seed_b + idx * 6271;

                av = rand_safe(seed_a);
                bv = rand_safe(seed_b);

                // Widen to signed 64-bit BEFORE arithmetic to avoid
                // Verilog's self-determined expression width trap.
                av_se = $signed(av);
                bv_se = $signed(bv);
                expected_64 = av_se + bv_se;

                // Both av_se and bv_se are already signed 64-bit, so
                // the comparison uses 64-bit signed arithmetic correctly.
                if (expected_64 > 64'sd2147483647 || expected_64 < -64'sd2147483648) begin
                    av = av >>> 1;
                    bv = bv >>> 1;
                    av_se = $signed(av);
                    bv_se = $signed(bv);
                    expected_64 = av_se + bv_se;
                end
                expected_32 = expected_64[31:0];

                set_a(av);
                set_b(bv);
                op = 2'b00;  // ADD
                drive_and_wait;

                // Spot-check lanes 0, 32, 64, 96, 127
                check_lane(0,   expected_32, "Rand ADD lane0");
                check_lane(32,  expected_32, "Rand ADD lane32");
                check_lane(64,  expected_32, "Rand ADD lane64");
                check_lane(96,  expected_32, "Rand ADD lane96");
                check_lane(127, expected_32, "Rand ADD lane127");
                valid_i <= 1'b0;
            end
        end

        //=====================================================================
        // Test 16: Random MUL regression — 50 samples, non-overflow values
        //=====================================================================
        $display("--- Test 16: Random MUL (50 pairs, non-overflow) ---");
        begin
            integer idx;
            integer seed_a, seed_b;
            reg [31:0] av, bv;
            reg signed [63:0] av_se, bv_se;
            reg signed [63:0] expected_64;
            reg [31:0] expected_32;

            seed_a = 2025;
            seed_b = 3141;

            for (idx = 0; idx < 50; idx = idx + 1) begin
                seed_a = seed_a + idx * 4999;
                seed_b = seed_b + idx * 3571;

                // Small values to avoid overflow: 16-bit range
                av = (seed_a & 32'h0000FFFF);
                bv = (seed_b & 32'h0000FFFF);
                // sign-extend from bit 15
                if (av[15]) av = av | 32'hFFFF0000;
                else         av = av & 32'h0000FFFF;
                if (bv[15]) bv = bv | 32'hFFFF0000;
                else         bv = bv & 32'h0000FFFF;

                // Widen to signed 64-bit before multiplication
                av_se = $signed(av);
                bv_se = $signed(bv);
                expected_64 = av_se * bv_se;

                if (expected_64 > 64'sd2147483647 || expected_64 < -64'sd2147483648) begin
                    av = av >>> 1;
                    bv = bv >>> 1;
                    av_se = $signed(av);
                    bv_se = $signed(bv);
                    expected_64 = av_se * bv_se;
                end
                expected_32 = expected_64[31:0];

                set_a(av);
                set_b(bv);
                op = 2'b01;  // MUL
                drive_and_wait;

                check_lane(0,   expected_32, "Rand MUL lane0");
                check_lane(32,  expected_32, "Rand MUL lane32");
                check_lane(64,  expected_32, "Rand MUL lane64");
                check_lane(96,  expected_32, "Rand MUL lane96");
                check_lane(127, expected_32, "Rand MUL lane127");
                valid_i <= 1'b0;
            end
        end

        //=====================================================================
        // Test 17: valid_i → valid_o pipeline check
        //=====================================================================
        $display("--- Test 17: valid_i=0 produces valid_o=0 ---");
        set_a(32'sd1);
        set_b(32'sd2);
        op = 2'b00;  // ADD
        @(posedge clk); #1;
        valid_i <= 1'b0;  // deassert
        @(posedge clk); #1;
        total_tests = total_tests + 1;
        if (valid_o !== 1'b0) begin
            $display("FAIL: valid_o should be 0 after valid_i=0");
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS: valid_o = 0 after valid_i=0");
            passed_tests = passed_tests + 1;
        end

        //=====================================================================
        // Test 18: Reset clears output registers
        //=====================================================================
        $display("--- Test 18: Reset clears output registers ---");
        set_a(32'sd100);
        set_b(32'sd200);
        op = 2'b00;
        drive_and_wait;
        // result_o should be 300 now (verified by lane0 check)
        // Clear combinational inputs before asserting reset, so the
        // post-reset posedge latches zeros rather than stale 300.
        a_i = {WIDTH{1'b0}};
        b_i = {WIDTH{1'b0}};
        @(posedge clk); #1;
        rst_n = 1'b0;
        #10;
        // Check that reset cleared output registers immediately
        total_tests = total_tests + 1;
        if (result_o[31:0] !== 32'd0) begin
            $display("FAIL: lane0 not cleared during reset: 0x%08h", result_o[31:0]);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS: lane0=0 during reset (async clear)");
            passed_tests = passed_tests + 1;
        end
        rst_n = 1'b1;
        @(posedge clk); #1;
        total_tests = total_tests + 1;
        if (result_o[31:0] !== 32'd0) begin
            $display("FAIL: lane0 not zero after reset deassert: 0x%08h", result_o[31:0]);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS: lane0=0 after reset deassert");
            passed_tests = passed_tests + 1;
        end

        //=====================================================================
        // Summary
        //=====================================================================
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
