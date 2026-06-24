//===========================================================================
// MXU Accumulator — 64×64 INT32 storage with saturation clamping
//===========================================================================
// Internal submodule instantiated only inside mac_array.
// mxu_top does NOT directly connect to this module.
//
// Interfaces:
//   clk, rst_n         — clock and active-low async reset
//   addr[11:0]          — flattened {row_addr[5:0], col_addr[5:0]}
//   acc_in[31:0]        — signed INT32 partial sum to accumulate
//   acc_out[31:0]       — signed INT32 registered output
//   accumulate           — add acc_in to stored value with saturation
//   read_out             — output stored value to acc_out (next cycle)
//   reset_cmd            — clear stored value for addressed location to 0
//
// Saturation constants match CaduceusCore/sim/golden_executor.py:33-34
//   INT32_MAX = 2^31 - 1 = 32'h7FFFFFFF
//   INT32_MIN = -2^31     = 32'h80000000
//
// Overflow detection uses 33-bit signed arithmetic:
//   sum_wide > 2147483647  → positive overflow → clamp to INT32_MAX
//   sum_wide < -2147483648 → negative overflow → clamp to INT32_MIN
//===========================================================================

module accumulator (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [11:0] addr,        // flattened row/col: {row[5:0], col[5:0]}
    input  wire [31:0] acc_in,      // signed INT32 partial sum input
    output reg  [31:0] acc_out,     // signed INT32 output (registered)
    input  wire        accumulate,   // add acc_in to stored value with saturation
    input  wire        read_out,     // output stored value
    input  wire        reset_cmd     // clear stored value to 0
);

    //---------------------------------------------------------------------------
    // Constants from golden_executor.py:33-34
    //---------------------------------------------------------------------------
    localparam INT32_MAX_32 = 32'h7FFFFFFF;
    localparam INT32_MIN_32 = 32'h80000000;

    // 33-bit signed saturation thresholds
    localparam signed [32:0] THRESH_POS = 33'sd2147483647;   // 2^31 - 1
    localparam signed [32:0] THRESH_NEG = -33'sd2147483648;  // -2^31

    //---------------------------------------------------------------------------
    // Storage array: 64 × 64 = 4096 entries × 32 bits = 128 Kbits
    // Synthesizable as block RAM or register file.
    //---------------------------------------------------------------------------
    reg signed [31:0] acc_mem [0:4095];

    // Simulation-only: zero-initialize memory to avoid x-propagation
    // (synthesis ignores initial blocks; real HW uses reset_cmd per-location)
    integer _init_i;
    initial begin
        for (_init_i = 0; _init_i < 4096; _init_i = _init_i + 1)
            acc_mem[_init_i] = 32'd0;
    end

    //---------------------------------------------------------------------------
    // Combinational: read stored value and compute saturated sum
    //---------------------------------------------------------------------------
    wire signed [31:0] stored = acc_mem[addr];

    // Sign-extend both operands to 33 bits for overflow-safe addition.
    // Use explicit widening: stored is already signed[31:0]; acc_in is unsigned wire,
    // so $signed() is needed to get the signed interpretation before widening.
    wire signed [32:0] stored_se = stored;          // 32→33 sign-extension (stored is signed)
    wire signed [32:0] acc_se    = $signed(acc_in); // unsigned→signed 32, then 32→33 sign-ext
    wire signed [32:0] sum_wide  = stored_se + acc_se;

    // Saturation mux: clamp if overflow detected
    wire signed [31:0] saturated = (sum_wide > THRESH_POS) ? INT32_MAX_32
                                 : (sum_wide < THRESH_NEG) ? INT32_MIN_32
                                 : sum_wide[31:0];

    //---------------------------------------------------------------------------
    // Sequential logic: write (reset/accumulate) and read output register
    //---------------------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            acc_out <= 32'd0;
        end else begin
            // Write path: reset takes priority over accumulate
            if (reset_cmd) begin
                acc_mem[addr] <= 32'd0;
            end else if (accumulate) begin
                acc_mem[addr] <= saturated;
            end

            // Read output: if accumulating, output the new value
            if (read_out) begin
                if (reset_cmd) begin
                    acc_out <= 32'd0;
                end else if (accumulate) begin
                    acc_out <= saturated;
                end else begin
                    acc_out <= stored;
                end
            end else begin
                acc_out <= 32'd0;
            end
        end
    end

endmodule


//===========================================================================
// Self-checking testbench for accumulator module
//===========================================================================

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

