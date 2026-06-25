`timescale 1ns / 1ps
//=============================================================================
// tb_mac_array: Self-checking testbench for 64×64 MAC Array
//=============================================================================
// Tests:
//   T1: Single-cycle accumulation — weight=1, act=2 → all PEs = 2
//   T2: Reset accumulator — accumulate then reset, verify cleared
//   T3: Full tile (constant) — weight=2, act=3, 66 cycles → all PEs = 384
//   T4: All-ones weight + all-twos activation, 66 cycles → all PEs = 128
//   T5: Varying activation per row — weight=1, act[r]=r+1, 66 cycles → PE(r,c)=64*(r+1)
//   T6: Column-varying weight — weight[c]=(c+1)&7, act=1, 66 cycles → PE(r,c)=64*((c+1)&7)
//   T7: Negative weight (all -1, act=1) — 66 cycles → all PEs = -64
//   T8: Accumulator load — write via acc_load, read back, verify
//   T9: External accumulator module — write/read via ext_acc_* ports
//  T10: Full-row read — constant tile, verify all 64x64 cells
//=============================================================================

module tb_mac_array;

    // ── Parameters ────────────────────────────────────────────────────
    localparam ROWS = 64;
    localparam COLS = 64;
    localparam FLUSH_CYCLES = 2;  // pipeline drain cycles after last weight/act input

    // ── DUT signals ──────────────────────────────────────────────────
    reg             clk;
    reg             rst_n;
    reg   [255:0]   weight_bus;
    reg   [511:0]   activation_bus;
    reg             compute_en;
    reg             reset_acc;
    reg             read_out;
    reg     [5:0]   row_addr;
    reg             acc_load;
    reg  [2047:0]   acc_in_bus;
    wire [2047:0]   acc_out_bus;

    // External accumulator access
    reg    [11:0]   ext_acc_addr;
    reg    [31:0]   ext_acc_din;
    reg             ext_acc_wr;
    reg             ext_acc_rd;
    reg             ext_acc_rst;
    wire   [31:0]   ext_acc_dout;

    // ── Test tracking ─────────────────────────────────────────────────
    integer total_tests;
    integer passed_tests;
    integer failed_tests;

    // ── DUT instantiation ────────────────────────────────────────────
    mac_array u_dut (
        .clk            (clk),
        .rst_n          (rst_n),
        .weight_bus     (weight_bus),
        .activation_bus (activation_bus),
        .compute_en     (compute_en),
        .reset_acc      (reset_acc),
        .read_out       (read_out),
        .row_addr       (row_addr),
        .acc_load       (acc_load),
        .acc_in_bus     (acc_in_bus),
        .acc_out_bus    (acc_out_bus),
        .ext_acc_addr   (ext_acc_addr),
        .ext_acc_din    (ext_acc_din),
        .ext_acc_wr     (ext_acc_wr),
        .ext_acc_rd     (ext_acc_rd),
        .ext_acc_rst    (ext_acc_rst),
        .ext_acc_dout   (ext_acc_dout)
    );

    // ── Clock generation ─────────────────────────────────────────────
    initial begin
        clk = 0;
        forever #5 clk = ~clk;   // 100 MHz
    end

    // ── Helper: set all 64 weight values at once ──────────────────────
    task set_all_weights;
        input [3:0] val;
        integer i;
    begin
        for (i = 0; i < 64; i = i + 1) begin
            weight_bus[4*i +: 4] = val;
        end
    end
    endtask

    // ── Helper: set weight for a specific column ───────────────────────
    task set_weight_col;
        input [5:0] col;
        input [3:0] val;
    begin
        weight_bus[4*col +: 4] = val;
    end
    endtask

    // ── Helper: set all 64 activation values at once ──────────────────
    task set_all_activations;
        input [7:0] val;
        integer i;
    begin
        for (i = 0; i < 64; i = i + 1) begin
            activation_bus[8*i +: 8] = val;
        end
    end
    endtask

    // ── Helper: set activation for a specific row ──────────────────────
    task set_act_row;
        input [5:0] row;
        input [7:0] val;
    begin
        activation_bus[8*row +: 8] = val;
    end
    endtask

    // ── Helper: run N compute cycles (compute_en=1 for N+FLUSH cycles) ─
    task run_compute_cycles;
        input [7:0] num_weights;   // number of K-cycles with valid weight/act
        integer i, total_cycles;
    begin
        total_cycles = num_weights + FLUSH_CYCLES;
        compute_en = 1'b1;
        for (i = 0; i < total_cycles; i = i + 1) begin
            @(posedge clk); #1;
        end
        compute_en = 1'b0;
    end
    endtask

    // ── Helper: flush stale PE pipeline after reset_acc ────────────────
    // After reset_acc, PE mac_out_r still holds old values. We must run
    // 3 zero-weight compute cycles to flush stale data through the 2-deep
    // accumulation pipeline, then reset_acc again to clear the flushed junk.
    task flush_pipeline_after_reset;
        integer i;
    begin
        // Run 3 zero cycles: cycle1 captures stale pe_mac into pe_d1,
        // cycle2 accumulates into local_acc, cycle3 moves through.
        set_all_weights(4'd0);
        set_all_activations(8'd0);
        compute_en = 1'b1;
        for (i = 0; i < 3; i = i + 1) begin
            @(posedge clk); #1;
        end
        compute_en = 1'b0;
        // Now reset to clear the flushed junk out of local_acc/pe_d1
        @(posedge clk); #1;
        reset_acc = 1'b1;
        @(posedge clk); #1;
        reset_acc = 1'b0;
    end
    endtask

    // ── Helper: clean reset (pulse rst_n + flush) ─────────────────────
    task clean_state;
    begin
        // Already have clean state at power-on. Use reset_acc + flush.
        @(posedge clk); #1;
        reset_acc = 1'b1;
        @(posedge clk); #1;
        reset_acc = 1'b0;
        @(posedge clk); #1;
        flush_pipeline_after_reset();
    end
    endtask

    // ── Helper: run N compute cycles with weight/act applied each cycle ─
    task run_pattern_compute;
        input [7:0] num_weights;   // number of K-cycles
        integer i;
    begin
        compute_en = 1'b1;
        // Apply weight/act for each K-cycle (must be set before calling)
        for (i = 0; i < num_weights; i = i + 1) begin
            @(posedge clk); #1;
        end
        // Flush cycles
        for (i = 0; i < FLUSH_CYCLES; i = i + 1) begin
            @(posedge clk); #1;
        end
        compute_en = 1'b0;
    end
    endtask

    // ── Helper: read and check one row ────────────────────────────────
    task check_row;
        input [5:0] row;
        input [31:0] expected [0:63];
        input [255:0] desc;
        integer c;
        reg [31:0] captured [0:63];
    begin
        // Initiate read
        @(posedge clk); #1;
        row_addr = row;
        read_out = 1'b1;
        @(posedge clk); #1;
        // Capture all column values BEFORE clearing read_out
        for (c = 0; c < 64; c = c + 1) begin
            captured[c] = acc_out_bus[32*c +: 32];
        end
        read_out = 1'b0;
        // Now check
        for (c = 0; c < 64; c = c + 1) begin
            total_tests = total_tests + 1;
            if (captured[c] !== expected[c]) begin
                $display("FAIL [%0s] row=%0d col=%0d: expected=%0d (0x%08h) got=%0d (0x%08h)",
                         desc, row, c, $signed(expected[c]), expected[c],
                         $signed(captured[c]), captured[c]);
                failed_tests = failed_tests + 1;
            end else begin
                passed_tests = passed_tests + 1;
            end
        end
    end
    endtask

    // ── Helper: check single cell value ───────────────────────────────
    task check_cell;
        input [5:0] row;
        input [5:0] col;
        input [31:0] expected;
        input [255:0] desc;
        reg [31:0] got;
    begin
        // Initiate read
        @(posedge clk); #1;
        row_addr = row;
        read_out = 1'b1;
        @(posedge clk); #1;
        // Capture BEFORE clearing read_out
        got = acc_out_bus[32*col +: 32];
        read_out = 1'b0;
        total_tests = total_tests + 1;
        if (got !== expected) begin
            $display("FAIL [%0s] row=%0d col=%0d: expected=%0d (0x%08h) got=%0d (0x%08h)",
                     desc, row, col, $signed(expected), expected,
                     $signed(got), got);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [%0s] row=%0d col=%0d: value=%0d (0x%08h)",
                     desc, row, col, $signed(expected), expected);
            passed_tests = passed_tests + 1;
        end
    end
    endtask

    // ── Helper: compute expected value for tests ──────────────────────
    function [31:0] expect_const;
        input [31:0] product_per_k;
        input [7:0]  k_count;
    begin
        expect_const = product_per_k * {24'd0, k_count};
    end
    endfunction

    // ═══════════════════════════════════════════════════════════════════
    //  TEST SEQUENCE
    // ═══════════════════════════════════════════════════════════════════
    integer k, r, c, ii;
    reg [31:0] expected_row [0:63];
    reg [31:0] exp_val;

    initial begin
        total_tests  = 0;
        passed_tests = 0;
        failed_tests = 0;

        // Initialize all control signals
        compute_en = 1'b0;
        reset_acc  = 1'b0;
        read_out   = 1'b0;
        row_addr   = 6'd0;
        acc_load   = 1'b0;
        acc_in_bus = 2048'd0;
        weight_bus = 256'd0;
        activation_bus = 512'd0;
        ext_acc_addr = 12'd0;
        ext_acc_din  = 32'd0;
        ext_acc_wr   = 1'b0;
        ext_acc_rd   = 1'b0;
        ext_acc_rst  = 1'b0;

        // ── Power-on reset ──────────────────────────────────────────
        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;

        $display("╔═══════════════════════════════════════════════════════╗");
        $display("║   MAC ARRAY (64×64) SELF-CHECKING TESTBENCH          ║");
        $display("╚═══════════════════════════════════════════════════════╝");
        $display("");

        //===============================================================
        // T1: Single-cycle accumulation
        //     weight=1 for all columns, activation=2 for all rows
        //     After 1 weight input + 2 flush cycles: each PE = 1*2 = 2
        //===============================================================
        $display("─── T1: Single-cycle accumulation (1×2=2) ───");

        set_all_weights(4'd1);
        set_all_activations(8'd2);
        run_compute_cycles(1);  // 1 input + 2 flush = 3 cycles

        // Check row 0, col 0
        check_cell(6'd0, 6'd0, 32'd2, "T1 single-cycle");

        //===============================================================
        // T2: Reset and re-check
        //===============================================================
        $display("─── T2: Reset accumulator ───");

        @(posedge clk); #1;
        reset_acc = 1'b1;
        @(posedge clk); #1;
        reset_acc = 1'b0;

        // All accumulators should be 0
        check_cell(6'd0, 6'd0, 32'd0, "T2 after reset");
        check_cell(6'd63, 6'd63, 32'd0, "T2 after reset corner");

        // Flush PE pipeline after T2 reset (stale pe_mac from T1)
        flush_pipeline_after_reset();

        //===============================================================
        // T3: Full tile with constant values
        //     weight=2, activation=3, 64 K-cycles
        //     Expected: 2*3*64 = 384 per PE
        //===============================================================
        $display("─── T3: Full tile constant (2×3×64=384) ───");

        set_all_weights(4'd2);
        set_all_activations(8'd3);
        run_compute_cycles(64);  // 64 input + 2 flush = 66 cycles

        check_cell(6'd0,  6'd0,  32'd384, "T3 full-tile constant");
        check_cell(6'd31, 6'd31, 32'd384, "T3 full-tile constant mid");
        check_cell(6'd63, 6'd63, 32'd384, "T3 full-tile constant corner");

        // Clean state for next test
        clean_state();

        //===============================================================
        // T4: All-ones weight + all-twos activation (Task acceptance test)
        //     weight=1, activation=2, 64 K-cycles
        //     Expected: 1*2*64 = 128 per PE
        //===============================================================
        $display("─── T4: All-ones weight + all-twos activation (1×2×64=128) ───");

        set_all_weights(4'd1);
        set_all_activations(8'd2);
        run_compute_cycles(64);

        exp_val = 32'd128;
        check_cell(6'd0,  6'd0,  exp_val, "T4 all-ones/all-twos");
        check_cell(6'd63, 6'd63, exp_val, "T4 all-ones/all-twos corner");

        clean_state();

        //===============================================================
        // T5: Varying activation per row
        //     weight=1 for all columns, activation[r]=r+1 (varying per row)
        //     64 K-cycles (same weight/act each cycle)
        //     PE(r,c) = 64 * 1 * (r+1) = 64*(r+1)
        //===============================================================
        $display("─── T5: Varying activation per row (result[r][c] = 64*(r+1)) ───");

        set_all_weights(4'd1);
        for (r = 0; r < 64; r = r + 1) begin
            // activation[r] = r + 1, but must fit in INT8 [-128, 127]
            // r+1 for r=0..63 → 1..64, fits in INT8
            set_act_row(r[5:0], r[5:0] + 8'd1);
        end
        run_compute_cycles(64);

        // Check several rows across columns to verify row-broadcast:
        // all columns in a row must have the same value.
        for (c = 0; c < 64; c = c + 1) begin
            exp_val = 64 * 1;      // 64 * (0+1) = 64
            check_cell(6'd0, c[5:0], exp_val, "T5 row0 all cols");
        end
        for (c = 0; c < 64; c = c + 1) begin
            exp_val = 64 * 32;     // 64 * (31+1) = 2048
            check_cell(6'd31, c[5:0], exp_val, "T5 row31 all cols");
        end
        for (c = 0; c < 64; c = c + 1) begin
            exp_val = 64 * 64;     // 64 * (63+1) = 4096
            check_cell(6'd63, c[5:0], exp_val, "T5 row63 all cols");
        end

        clean_state();

        //===============================================================
        // T6: Column-varying weight
        //     weight[c]=(c+1)&7, activation[r]=1
        //     64 K-cycles (same weight/act each cycle)
        //     PE(r,c) = 64 * ((c+1)&7) * 1 = 64*((c+1)&7)
        //===============================================================
        $display("─── T6: Column-varying weight (result = 64*((c+1)&7)) ───");

        for (c = 0; c < 64; c = c + 1) begin
            set_weight_col(c[5:0], (c + 1) & 4'd7);
        end
        set_all_activations(8'd1);
        run_compute_cycles(64);

        // Check several columns across rows to verify column-broadcast:
        // all rows in a column must have the same value.
        for (r = 0; r < 64; r = r + 1) begin
            exp_val = 64 * 1;       // 64 * ((0+1)&7) = 64
            check_cell(r[5:0], 6'd0, exp_val, "T6 col0 all rows");
        end
        for (r = 0; r < 64; r = r + 1) begin
            exp_val = 64 * ((31 + 1) & 7);  // 64 * (32&7) = 0
            check_cell(r[5:0], 6'd31, exp_val, "T6 col31 all rows");
        end
        for (r = 0; r < 64; r = r + 1) begin
            exp_val = 64 * ((63 + 1) & 7);  // 64 * (64&7) = 0
            check_cell(r[5:0], 6'd63, exp_val, "T6 col63 all rows");
        end

        clean_state();

        //===============================================================
        // T7: Mixed sign weight pattern
        //     weight[c] = -1 (0xF in INT4, which is -1), activation[r]=1
        //     64 K-cycles → result = 64 * (-1) * 1 = -64 per PE
        //===============================================================
        $display("─── T7: Negative weight (all -1, act=1) → result=-64 ───");

        set_all_weights(4'hF);   // 0xF = -1 in signed INT4
        set_all_activations(8'd1);
        run_compute_cycles(64);

        exp_val = -32'sd64;  // 64 * (-1) = -64
        check_cell(6'd0, 6'd0, exp_val, "T7 negative weight");

        clean_state();

        //===============================================================
        // T8: Accumulator load port
        //     Load row 5 via acc_load, read back, verify
        //===============================================================
        $display("─── T8: Accumulator load port ───");

        // Load row 5: set each column to (col * 100)
        row_addr = 6'd5;
        for (c = 0; c < 64; c = c + 1) begin
            acc_in_bus[32*c +: 32] = c * 100;
        end
        acc_load = 1'b1;
        @(posedge clk); #1;
        acc_load = 1'b0;
        @(posedge clk); #1;

        // Read back row 5
        check_cell(6'd5, 6'd10, 32'd1000, "T8 acc_load row5 col10");
        check_cell(6'd5, 6'd63, 32'd6300, "T8 acc_load row5 col63");
        // Row 0 should still be 0
        check_cell(6'd0, 6'd0, 32'd0, "T8 other row untouched");

        //===============================================================
        // T9: External accumulator module access
        //     Write value 0x12345678 to address {row=3, col=7}
        //     Read it back via ext_acc_dout
        //===============================================================
        $display("─── T9: External accumulator module ───");

        // Write: accumulate value at addr = {row=3, col=7} = {6'd3, 6'd7} = 12'd199
        @(posedge clk); #1;
        ext_acc_addr = {6'd3, 6'd7};  // row=3, col=7
        ext_acc_din  = 32'h12345678;
        ext_acc_wr   = 1'b1;
        ext_acc_rd   = 1'b0;
        ext_acc_rst  = 1'b0;
        @(posedge clk); #1;
        ext_acc_wr   = 1'b0;
        @(posedge clk); #1;

        // Read back
        ext_acc_addr = {6'd3, 6'd7};
        ext_acc_rd   = 1'b1;
        @(posedge clk); #1;
        ext_acc_rd   = 1'b0;
        // ext_acc_dout is registered — available this cycle (accumulator outputs at posedge)
        total_tests = total_tests + 1;
        if (ext_acc_dout !== 32'h12345678) begin
            $display("FAIL [T9 ext acc] got=0x%08h expected=0x12345678", ext_acc_dout);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [T9 ext acc] read=0x%08h", ext_acc_dout);
            passed_tests = passed_tests + 1;
        end

        // Accumulate another value: 0x11111111 + 0x2222 = 0x11113333
        @(posedge clk); #1;
        ext_acc_addr = {6'd3, 6'd7};
        ext_acc_din  = 32'h2222;
        ext_acc_wr   = 1'b1;
        @(posedge clk); #1;
        ext_acc_wr   = 1'b0;
        ext_acc_rd   = 1'b1;
        @(posedge clk); #1;
        ext_acc_rd   = 1'b0;

        total_tests = total_tests + 1;
        if (ext_acc_dout !== 32'h1234789A) begin
            $display("FAIL [T9 ext acc accumulate] got=0x%08h expected=0x1234789A", ext_acc_dout);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [T9 ext acc accumulate] sum=0x%08h", ext_acc_dout);
            passed_tests = passed_tests + 1;
        end

        // Reset that address via ext_acc_rst
        @(posedge clk); #1;
        ext_acc_addr = {6'd3, 6'd7};
        ext_acc_rst  = 1'b1;
        @(posedge clk); #1;
        ext_acc_rst  = 1'b0;
        ext_acc_rd   = 1'b1;
        @(posedge clk); #1;
        ext_acc_rd   = 1'b0;

        total_tests = total_tests + 1;
        if (ext_acc_dout !== 32'd0) begin
            $display("FAIL [T9 ext acc reset] got=0x%08h expected=0", ext_acc_dout);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [T9 ext acc reset] value=0");
            passed_tests = passed_tests + 1;
        end

        //===============================================================
        // T10: Full-row read — verify all 64 columns
        //      Re-run T3 (constant 384), read all rows
        //===============================================================
        $display("─── T10: Full-row read across all 64 columns ───");

        clean_state();

        set_all_weights(4'd3);
        set_all_activations(8'd5);
        run_compute_cycles(64);  // Expected: 3*5*64 = 960

        exp_val = 32'd960;
        for (r = 0; r < 64; r = r + 1) begin
            for (c = 0; c < 64; c = c + 1) begin
                expected_row[c] = exp_val;
            end
            check_row(r[5:0], expected_row, "T10 full read");
        end

        //===============================================================
        // Summary
        //===============================================================
        $display("");
        $display("═══════════════════════════════════════════════════════");
        $display("  TEST SUMMARY");
        $display("  Total:  %0d", total_tests);
        $display("  Passed: %0d", passed_tests);
        $display("  Failed: %0d", failed_tests);
        $display("═══════════════════════════════════════════════════════");

        if (failed_tests == 0) begin
            $display("RESULT: ALL TESTS PASSED");
        end else begin
            $display("RESULT: %0d TESTS FAILED", failed_tests);
        end

        #50;
        $finish;
    end

endmodule
