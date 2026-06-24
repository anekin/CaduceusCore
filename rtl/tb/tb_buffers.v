// tb_buffers.v — Self-checking testbench for weight_buffer + activation_buffer
//
// Tests:
//   1. Weight buffer round-trip: 64 INT4 weights (8 × 32-bit words), verify 2:1 packing
//   2. Activation buffer round-trip: 64 INT8 activations (16 × 32-bit words)
//   3. Out-of-range write: no effect on valid addresses
//   4. Out-of-range read: returns 0

`timescale 1ns/1ps

module tb_buffers;

    // ── DUT parameters ──────────────────────────────────────────────
    localparam WB_DEPTH      = 512;
    localparam WB_ADDR_WIDTH = 10;
    localparam AB_DEPTH      = 1024;
    localparam AB_ADDR_WIDTH = 11;

    // ── Signals ─────────────────────────────────────────────────────
    reg clk;
    reg rst_n;

    // Weight buffer
    reg                     w_wr_en;
    reg  [WB_ADDR_WIDTH-1:0] w_wr_addr;
    reg  [31:0]             w_wr_data;
    reg                     w_rd_en;
    reg  [WB_ADDR_WIDTH-1:0] w_rd_addr;
    wire [31:0]             w_rd_data;

    // Activation buffer
    reg                     a_wr_en;
    reg  [AB_ADDR_WIDTH-1:0] a_wr_addr;
    reg  [31:0]             a_wr_data;
    reg                     a_rd_en;
    reg  [AB_ADDR_WIDTH-1:0] a_rd_addr;
    wire [31:0]             a_rd_data;

    // ── DUT instances ───────────────────────────────────────────────
    weight_buffer #(
        .DEPTH(WB_DEPTH),
        .ADDR_WIDTH(WB_ADDR_WIDTH)
    ) wbuf (
        .clk(clk),
        .rst_n(rst_n),
        .wr_en(w_wr_en),
        .wr_addr(w_wr_addr),
        .wr_data(w_wr_data),
        .rd_en(w_rd_en),
        .rd_addr(w_rd_addr),
        .rd_data(w_rd_data)
    );

    activation_buffer #(
        .DEPTH(AB_DEPTH),
        .ADDR_WIDTH(AB_ADDR_WIDTH)
    ) abuf (
        .clk(clk),
        .rst_n(rst_n),
        .wr_en(a_wr_en),
        .wr_addr(a_wr_addr),
        .wr_data(a_wr_data),
        .rd_en(a_rd_en),
        .rd_addr(a_rd_addr),
        .rd_data(a_rd_data)
    );

    // ── Clock generator (10ns period = 100MHz) ─────────────────────
    initial clk = 0;
    always #5 clk = ~clk;

    // ── Test state ──────────────────────────────────────────────────
    integer pass_count;
    integer fail_count;
    integer i;
    reg [31:0] expected;
    reg [31:0] observed;

    // ── Helper: build expected weight word (2:1 packed INT4) ────────
    // Given base index N, build word for weights 8N..8N+7:
    //   byte0 = {weight[8N+1], weight[8N+0]}
    //   byte1 = {weight[8N+3], weight[8N+2]}
    //   byte2 = {weight[8N+5], weight[8N+4]}
    //   byte3 = {weight[8N+7], weight[8N+6]}
    function [31:0] weight_word(input [WB_ADDR_WIDTH-1:0] base);
        integer b;
        integer idx_even, idx_odd;
        reg [3:0] lo, hi;
        begin
            weight_word = 32'd0;
            for (b = 0; b < 4; b = b + 1) begin
                // byte b: bits [(b*8)+7 : b*8]
                // low nibble = weight[base*8 + b*2], high nibble = weight[base*8 + b*2 + 1]
                idx_even = base * 8 + b * 2;
                idx_odd  = base * 8 + b * 2 + 1;
                lo = idx_even[3:0];
                hi = idx_odd[3:0];
                weight_word = weight_word | ({28'd0, hi, lo} << (b * 8));
            end
        end
    endfunction

    // ── Helper: build expected activation word ──────────────────────
    // Given base index N, build word for activations 4N..4N+3:
    //   byte0 = activation[4N+0], byte1 = activation[4N+1], ...
    function [31:0] activation_word(input [AB_ADDR_WIDTH-1:0] base);
        reg [7:0] b0, b1, b2, b3;
        begin
            b0 = base * 4 + 0;
            b1 = base * 4 + 1;
            b2 = base * 4 + 2;
            b3 = base * 4 + 3;
            activation_word = {b3, b2, b1, b0};
        end
    endfunction

    // ── File output for evidence capture ─────────────────────────────
    integer log_fd;

    // ── Check macro ─────────────────────────────────────────────────
    task check_equal(input string label, input [31:0] exp, input [31:0] obs);
        begin
            if (exp === obs) begin
                pass_count = pass_count + 1;
                $display("[PASS] %0s: expected=0x%08h, got=0x%08h", label, exp, obs);
                $fdisplay(log_fd, "[PASS] %0s: expected=0x%08h, got=0x%08h", label, exp, obs);
            end else begin
                fail_count = fail_count + 1;
                $display("[FAIL] %0s: expected=0x%08h, got=0x%08h", label, exp, obs);
                $fdisplay(log_fd, "[FAIL] %0s: expected=0x%08h, got=0x%08h", label, exp, obs);
            end
        end
    endtask

    // ── Main test sequence ──────────────────────────────────────────
    initial begin
        log_fd = $fopen("tb_results.txt", "w");
        $display("DEBUG: tb_buffers initial block started at time %0t", $time);
        $fdisplay(log_fd, "tb_buffers initial block started at time %0t", $time);
        pass_count = 0;
        fail_count = 0;

        // Initialize
        w_wr_en = 0; w_wr_addr = 0; w_wr_data = 0;
        w_rd_en = 0; w_rd_addr = 0;
        a_wr_en = 0; a_wr_addr = 0; a_wr_data = 0;
        a_rd_en = 0; a_rd_addr = 0;

        // Reset sequence
        rst_n = 0;
        repeat(3) @(posedge clk);
        rst_n = 1;
        repeat(2) @(posedge clk);   // Wait for reset release

        $display("══════════════════════════════════════════════════════");
        $display("TEST 1: Weight buffer round-trip (64 INT4 weights)");
        $display("══════════════════════════════════════════════════════");

        // Write 8 words (64 weights) with incremental pattern
        for (i = 0; i < 8; i = i + 1) begin
            @(posedge clk);
            w_wr_en   = 1;
            w_wr_addr = i[WB_ADDR_WIDTH-1:0];
            w_wr_data = weight_word(i[WB_ADDR_WIDTH-1:0]);
        end
        @(posedge clk);
        w_wr_en = 0;
        w_wr_data = 32'd0;

        // Read back and verify (1-cycle latency)
        for (i = 0; i < 8; i = i + 1) begin
            @(posedge clk);
            w_rd_en   = 1;
            w_rd_addr = i[WB_ADDR_WIDTH-1:0];
            expected  = weight_word(i[WB_ADDR_WIDTH-1:0]);
            @(posedge clk);     // Data available after 1 cycle
            w_rd_en   = 0;
            observed  = w_rd_data;
            check_equal($sformatf("Weight[%0d:%0d]", i*8+0, i*8+7), expected, observed);
        end

        $display("");
        $display("══════════════════════════════════════════════════════");
        $display("TEST 2: Activation buffer round-trip (64 INT8 acts)");
        $display("══════════════════════════════════════════════════════");

        // Write 16 words (64 activations) with incremental pattern
        for (i = 0; i < 16; i = i + 1) begin
            @(posedge clk);
            a_wr_en   = 1;
            a_wr_addr = i[AB_ADDR_WIDTH-1:0];
            a_wr_data = activation_word(i[AB_ADDR_WIDTH-1:0]);
        end
        @(posedge clk);
        a_wr_en = 0;
        a_wr_data = 32'd0;

        // Read back and verify
        for (i = 0; i < 16; i = i + 1) begin
            @(posedge clk);
            a_rd_en   = 1;
            a_rd_addr = i[AB_ADDR_WIDTH-1:0];
            expected  = activation_word(i[AB_ADDR_WIDTH-1:0]);
            @(posedge clk);     // Data available after 1 cycle
            a_rd_en   = 0;
            observed  = a_rd_data;
            check_equal($sformatf("Activation[%0d:%0d]", i*4+0, i*4+3), expected, observed);
        end

        $display("");
        $display("══════════════════════════════════════════════════════");
        $display("TEST 3: Out-of-range write (weight buffer)");
        $display("══════════════════════════════════════════════════════");

        // Verify existing data at addr 0 is intact
        @(posedge clk);
        w_rd_en   = 1;
        w_rd_addr = 0;
        expected  = weight_word(0);
        @(posedge clk);
        w_rd_en   = 0;
        observed  = w_rd_data;
        check_equal("Weight[0] before bad write", expected, observed);

        // Attempt write to out-of-range address
        @(posedge clk);
        w_wr_en   = 1;
        w_wr_addr = WB_DEPTH;         // Out of range (max valid = DEPTH-1)
        w_wr_data = 32'hDEAD_BEEF;
        @(posedge clk);
        w_wr_en   = 0;
        w_wr_data = 32'd0;

        // Verify addr 0 still has original data
        @(posedge clk);
        w_rd_en   = 1;
        w_rd_addr = 0;
        expected  = weight_word(0);
        @(posedge clk);
        w_rd_en   = 0;
        observed  = w_rd_data;
        check_equal("Weight[0] after bad write", expected, observed);

        // Read from out-of-range address → should return 0
        @(posedge clk);
        w_rd_en   = 1;
        w_rd_addr = WB_DEPTH + 5;     // Out of range
        expected  = 32'd0;
        @(posedge clk);
        w_rd_en   = 0;
        observed  = w_rd_data;
        check_equal("Weight out-of-range read", expected, observed);

        $display("");
        $display("══════════════════════════════════════════════════════");
        $display("TEST 4: Out-of-range write (activation buffer)");
        $display("══════════════════════════════════════════════════════");

        // Verify existing data at addr 0 is intact
        @(posedge clk);
        a_rd_en   = 1;
        a_rd_addr = 0;
        expected  = activation_word(0);
        @(posedge clk);
        a_rd_en   = 0;
        observed  = a_rd_data;
        check_equal("Activation[0] before bad write", expected, observed);

        // Attempt write to out-of-range address
        @(posedge clk);
        a_wr_en   = 1;
        a_wr_addr = AB_DEPTH;         // Out of range
        a_wr_data = 32'hCAFE_BABE;
        @(posedge clk);
        a_wr_en   = 0;
        a_wr_data = 32'd0;

        // Verify addr 0 still has original data
        @(posedge clk);
        a_rd_en   = 1;
        a_rd_addr = 0;
        expected  = activation_word(0);
        @(posedge clk);
        a_rd_en   = 0;
        observed  = a_rd_data;
        check_equal("Activation[0] after bad write", expected, observed);

        // Read from out-of-range address → should return 0
        @(posedge clk);
        a_rd_en   = 1;
        a_rd_addr = AB_DEPTH + 10;    // Out of range
        expected  = 32'd0;
        @(posedge clk);
        a_rd_en   = 0;
        observed  = a_rd_data;
        check_equal("Activation out-of-range read", expected, observed);

        // ── Summary ─────────────────────────────────────────────────
        $display("");
        $display("══════════════════════════════════════════════════════");
        $display("RESULTS: %0d passed, %0d failed", pass_count, fail_count);
        $display("══════════════════════════════════════════════════════");

        if (fail_count == 0) begin
            $display("OVERALL: PASSED");
            $fdisplay(log_fd, "OVERALL: PASSED (%0d checks)", pass_count);
        end else begin
            $display("OVERALL: FAILED");
            $fdisplay(log_fd, "OVERALL: FAILED (%0d/%0d checks failed)", fail_count, pass_count + fail_count);
            $fatal(2, "Testbench FAILED with %0d failures", fail_count);
        end

        $fclose(log_fd);
        $finish;
    end

endmodule
