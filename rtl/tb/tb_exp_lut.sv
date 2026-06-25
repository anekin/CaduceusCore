// tb_exp_lut.sv — Self-checking testbench for exp_lut ROM
//
// Verifies:
//   1. LUT loads correctly from hex file (no Xs)
//   2. addr sweep produces monotonic non-decreasing output
//   3. Endpoints: addr=0 → 0x000, addr=255 → 0x010 (1.0 in Q8.4)
//   4. All values within 12-bit unsigned range [0, 0xFFF]

`timescale 1ns/1ps

module tb_exp_lut;

    reg         clk;
    reg         rst_n;
    reg  [7:0]  addr;
    wire [11:0] lut_out;

    // Instantiate DUT
    exp_lut dut (
        .clk    (clk),
        .rst_n  (rst_n),
        .addr   (addr),
        .lut_out(lut_out)
    );

    // ── Clock generation ───────────────────────────────────────────
    always #5 clk = ~clk;  // 100 MHz

    // ── Test variables ──────────────────────────────────────────────
    integer i;
    integer errors;
    reg [11:0] prev_val;
    reg [11:0] cur_val;

    // ── Main test sequence ──────────────────────────────────────────
    initial begin
        errors = 0;

        // Init
        clk = 0;
        rst_n = 0;
        addr = 8'd0;

        // Release reset
        #20 rst_n = 1;
        #10;  // allow any initialization to settle

        // ── Test 1: addr=0 should be 0 ─────────────────────────────
        addr = 8'd0;
        #1;  // combinatorial read settles
        if (lut_out !== 12'h000) begin
            $display("FAIL: addr=0 expected 12'h000, got 12'h%03x", lut_out);
            errors = errors + 1;
        end else begin
            $display("PASS: addr=0 → 12'h%03x", lut_out);
        end

        // ── Test 2: addr=255 should be 1.0 in Q8.4 → 12'h010 ──────
        addr = 8'd255;
        #1;
        if (lut_out !== 12'h010) begin
            $display("FAIL: addr=255 expected 12'h010 (1.0 Q8.4), got 12'h%03x",
                     lut_out);
            errors = errors + 1;
        end else begin
            $display("PASS: addr=255 → 12'h%03x (1.0 Q8.4)", lut_out);
        end

        // ── Test 3: monotonicity sweep ──────────────────────────────
        prev_val = 12'h000;
        for (i = 0; i < 256; i = i + 1) begin
            addr = i;
            #1;
            cur_val = lut_out;

            // Check for X or Z
            if (cur_val === 12'hxxx || cur_val === 12'hzzz) begin
                $display("FAIL: addr=%0d returned X/Z: 12'h%03x", i, cur_val);
                errors = errors + 1;
            end

            // Check monotonic (non-decreasing)
            if (cur_val < prev_val) begin
                $display("FAIL: addr=%0d value 12'h%03x < addr=%0d value 12'h%03x",
                         i, cur_val, i - 1, prev_val);
                errors = errors + 1;
            end

            prev_val = cur_val;
        end

        // ── Test 4: spot-check a few mid-range values ───────────────
        // addr=128 corresponds to x≈-10.0, exp(-10)≈4.54e-5 → Q8.4=0
        addr = 8'd128;
        #1;
        $display("INFO: addr=128 (x≈-10.0) → 12'h%03x", lut_out);

        // addr=200 corresponds to x≈-4.31, exp(-4.31)≈0.0134 → Q8.4≈0
        addr = 8'd200;
        #1;
        $display("INFO: addr=200 (x≈-4.31) → 12'h%03x", lut_out);

        // addr=240 corresponds to x≈-1.18, exp(-1.18)≈0.308 → Q8.4≈5 (0.3125)
        addr = 8'd240;
        #1;
        $display("INFO: addr=240 (x≈-1.18) → 12'h%03x", lut_out);

        // addr=250 corresponds to x≈-0.392, exp(-0.392)≈0.676 → Q8.4≈11 (0.6875)
        addr = 8'd250;
        #1;
        $display("INFO: addr=250 (x≈-0.392) → 12'h%03x", lut_out);

        // ── Report ──────────────────────────────────────────────────
        #10;
        if (errors == 0) begin
            $display("tb_exp_lut: ALL TESTS PASSED (3 checks, 256-addr monotonic sweep)");
        end else begin
            $display("tb_exp_lut: %0d TEST(S) FAILED", errors);
        end

        $finish;
    end

    // ── Timeout watchdog ────────────────────────────────────────────
    initial begin
        #100000;
        $display("TIMEOUT: Simulation did not finish within 100 us");
        $finish;
    end

endmodule
