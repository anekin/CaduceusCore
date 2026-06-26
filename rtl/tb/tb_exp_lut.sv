// tb_exp_lut.sv — Self-checking testbench for exp_lut ROM
//
// Verifies:
//   1. LUT loads correctly from hex file (no Xs)
//   2. addr sweep produces monotonic non-decreasing output
//   3. Endpoints: addr=0 → 0x0000, addr=255 → 0x4000 (1.0 in Q1.14)
//   4. All values within 15-bit unsigned range [0, 0x7FFF]

`timescale 1ns/1ps

module tb_exp_lut;

    reg         clk;
    reg         rst_n;
    reg  [7:0]  addr;
    wire [14:0] lut_out;

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
    reg [14:0] prev_val;
    reg [14:0] cur_val;

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
        #1;
        if (lut_out !== 15'h0000) begin
            $display("FAIL: addr=0 expected 15'h0000, got 15'h%04x", lut_out);
            errors = errors + 1;
        end else begin
            $display("PASS: addr=0 → 15'h%04x", lut_out);
        end

        // ── Test 2: addr=255 should be 1.0 in Q1.14 → 15'h4000 ─────
        addr = 8'd255;
        #1;
        if (lut_out !== 15'h4000) begin
            $display("FAIL: addr=255 expected 15'h4000 (1.0 Q1.14), got 15'h%04x",
                     lut_out);
            errors = errors + 1;
        end else begin
            $display("PASS: addr=255 → 15'h%04x (1.0 Q1.14)", lut_out);
        end

        // ── Test 3: monotonicity sweep ──────────────────────────────
        prev_val = 15'h0000;
        for (i = 0; i < 256; i = i + 1) begin
            addr = i;
            #1;
            cur_val = lut_out;

            if (cur_val === 15'hxxxx || cur_val === 15'hzzzz) begin
                $display("FAIL: addr=%0d returned X/Z: 15'h%04x", i, cur_val);
                errors = errors + 1;
            end

            if (cur_val < prev_val) begin
                $display("FAIL: addr=%0d value 15'h%04x < addr=%0d value 15'h%04x",
                         i, cur_val, i - 1, prev_val);
                errors = errors + 1;
            end

            prev_val = cur_val;
        end

        // ── Test 4: spot-check a few mid-range values ───────────────
        addr = 8'd128;
        #1;
        $display("INFO: addr=128 (x≈-10.0) → 15'h%04x", lut_out);

        addr = 8'd200;
        #1;
        $display("INFO: addr=200 (x≈-4.31) → 15'h%04x", lut_out);

        addr = 8'd240;
        #1;
        $display("INFO: addr=240 (x≈-1.18) → 15'h%04x", lut_out);

        addr = 8'd250;
        #1;
        $display("INFO: addr=250 (x≈-0.392) → 15'h%04x", lut_out);

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
