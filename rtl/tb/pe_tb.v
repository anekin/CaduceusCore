//=============================================================================
// pe_tb: Self-checking testbench for the PE Processing Element
//=============================================================================
// Tests:
//   Test 1: weight=7, activation=64, acc_in=100  → mac_out=548
//   Test 2: weight=-8 (0x8), activation=-128, acc_in=0 → mac_out=1024
//   Test 3: acc_in=INT32_MAX, weight=1, activation=1 → mac_out=INT32_MAX (saturation)
//   Test 4: acc_in=INT32_MIN, weight=-1, activation=1 → mac_out=INT32_MIN (negative saturation on close-to-boundary)
//=============================================================================

`timescale 1ns / 1ps

module pe_tb;

    reg                 clk;
    reg                 rst_n;
    reg  signed  [7:0]  activation;
    reg  signed  [3:0]  weight;
    reg  signed [31:0]  acc_in;
    wire signed [31:0]  mac_out;

    //-----------------------------------------------------------------
    // Instantiate DUT
    //-----------------------------------------------------------------
    pe u_pe (
        .clk        (clk),
        .rst_n      (rst_n),
        .activation (activation),
        .weight     (weight),
        .acc_in     (acc_in),
        .mac_out    (mac_out)
    );

    //-----------------------------------------------------------------
    // 100 MHz clock (10 ns period)
    //-----------------------------------------------------------------
    always #5 clk = ~clk;

    //-----------------------------------------------------------------
    // Apply stimulus
    // Stimulus is applied on the negedge to avoid races with the
    // posedge-triggered DUT pipeline register.  Result is checked
    // one full cycle later (next negedge after capture).
    //-----------------------------------------------------------------
    integer failures;
    initial begin
        clk        = 0;
        rst_n      = 0;
        activation = 0;
        weight     = 0;
        acc_in     = 0;
        failures   = 0;

        // Hold reset for a few cycles
        repeat (4) @(posedge clk);
        rst_n = 1;
        @(negedge clk);   // synchronise to negedge

        //--------------------------------------------------------------
        // Test 1: weight=7, activation=64, acc_in=100 → expect 548
        //--------------------------------------------------------------
        $display("[TB] Test 1: weight=7, activation=64, acc_in=100");
        weight     = 4'd7;
        activation = 8'd64;
        acc_in     = 32'd100;
        @(negedge clk);   // wait one full cycle (capture + pipeline)
        if (mac_out !== 32'd548) begin
            $display("  FAIL: expected 548, got %0d (0x%h)", mac_out, mac_out);
            failures = failures + 1;
        end else begin
            $display("  PASS: mac_out = %0d", mac_out);
        end

        //--------------------------------------------------------------
        // Test 2: weight=0x8 (= -8 signed INT4), activation=-128,
        //         acc_in=0 → expect 1024
        //   weight=4'b1000 sign-extends to 8'b1111_1000 = -8
        //   (-8) * (-128) + 0 = 1024
        //--------------------------------------------------------------
        $display("[TB] Test 2: weight=-8 (0x8), activation=-128, acc_in=0");
        weight     = 4'b1000;       // INT4 -8 in two's complement
        activation = 8'b1000_0000;  // INT8 -128 in two's complement
        acc_in     = 32'd0;
        @(negedge clk);
        if (mac_out !== 32'd1024) begin
            $display("  FAIL: expected 1024, got %0d (0x%h)", mac_out, mac_out);
            failures = failures + 1;
        end else begin
            $display("  PASS: mac_out = %0d", mac_out);
        end

        //--------------------------------------------------------------
        // Test 3: acc_in = INT32_MAX, weight=1, activation=1
        //         → positive overflow → saturate to INT32_MAX
        //--------------------------------------------------------------
        $display("[TB] Test 3: positive saturation (acc_in=INT32_MAX, w=1, a=1)");
        weight     = 4'd1;
        activation = 8'd1;
        acc_in     = 32'h7FFFFFFF;   // INT32_MAX = 2^31 - 1
        @(negedge clk);
        if (mac_out !== 32'h7FFFFFFF) begin
            $display("  FAIL: expected 0x7FFFFFFF, got 0x%h", mac_out);
            failures = failures + 1;
        end else begin
            $display("  PASS: mac_out = 0x%h (saturated)", mac_out);
        end

        //--------------------------------------------------------------
        // Test 4: acc_in = INT32_MIN, weight=-1, activation=1
        //         → negative overflow → saturate to INT32_MIN
        //--------------------------------------------------------------
        $display("[TB] Test 4: negative saturation (acc_in=INT32_MIN, w=-1, a=1)");
        weight     = 4'b1111;         // INT4 -1 in two's complement
        activation = 8'd1;
        acc_in     = 32'h80000000;   // INT32_MIN = -2^31
        @(negedge clk);
        if (mac_out !== 32'h80000000) begin
            $display("  FAIL: expected 0x80000000, got 0x%h", mac_out);
            failures = failures + 1;
        end else begin
            $display("  PASS: mac_out = 0x%h (saturated)", mac_out);
        end

        //--------------------------------------------------------------
        // Summary
        //--------------------------------------------------------------
        if (failures == 0) begin
            $display("\n[TB] ALL 4 TESTS PASSED");
        end else begin
            $display("\n[TB] %0d TEST(S) FAILED", failures);
        end

        $finish;
    end

endmodule
