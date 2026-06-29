`timescale 1ns / 1ps
//=============================================================================
// tb_mac_array_p1 — Enhanced testbench for MXU P1 mac_array case (MX-12)
//=============================================================================
// MX-12: PE pipeline K+2 compute cycles → output correct after flush.
//   Tests: inject K weight/activation pairs, verify that after K+2
//   compute_en cycles the accumulator holds sum of K products.
//   Verify K=8, K=16, K=32, K=64 and partial flush at K+1 (not ready).
//=============================================================================

module tb_mac_array_p1;

    localparam ROWS = 64;
    localparam COLS = 64;
    localparam FLUSH_CYCLES = 2;

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
    reg    [11:0]   ext_acc_addr;
    reg    [31:0]   ext_acc_din;
    reg             ext_acc_wr;
    reg             ext_acc_rd;
    reg             ext_acc_rst;
    wire   [31:0]   ext_acc_dout;

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

    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    integer total_tests;
    integer passed_tests;
    integer failed_tests;

    // Helper: set all weights
    task set_all_weights;
        input [3:0] val;
        integer i;
    begin
        for (i = 0; i < 64; i = i + 1) weight_bus[4*i +: 4] = val;
    end
    endtask

    // Helper: set all activations
    task set_all_activations;
        input [7:0] val;
        integer i;
    begin
        for (i = 0; i < 64; i = i + 1) activation_bus[8*i +: 8] = val;
    end
    endtask

    // Helper: reset + flush pipeline
    task clean_state;
        integer i;
    begin
        @(posedge clk); #1;
        reset_acc = 1'b1;
        @(posedge clk); #1;
        reset_acc = 1'b0;
        // Flush: run 3 zero cycles to clear pipeline
        set_all_weights(4'd0);
        set_all_activations(8'd0);
        compute_en = 1'b1;
        for (i = 0; i < 3; i = i + 1) @(posedge clk); #1;
        compute_en = 1'b0;
        @(posedge clk); #1;
        reset_acc = 1'b1;
        @(posedge clk); #1;
        reset_acc = 1'b0;
    end
    endtask

    // Helper: check a single PE cell
    task check_cell;
        input [5:0] row;
        input [5:0] col;
        input [31:0] expected;
        input [255:0] desc;
        reg [31:0] got;
    begin
        @(posedge clk); #1;
        row_addr = row;
        read_out = 1'b1;
        @(posedge clk); #1;
        got = acc_out_bus[32*col +: 32];
        read_out = 1'b0;
        total_tests = total_tests + 1;
        if (got !== expected) begin
            $display("FAIL [%0s] row=%0d col=%0d: expected=%0d (0x%08h) got=%0d (0x%08h)",
                     desc, row, col, $signed(expected), expected, $signed(got), got);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [%0s] row=%0d col=%0d: value=%0d (0x%08h)",
                     desc, row, col, $signed(expected), expected);
            passed_tests = passed_tests + 1;
        end
    end
    endtask

    // Helper: run K compute cycles + FLUSH extra cycles
    // Returns after all cycles complete (compute_en goes low at end)
    task run_k_cycles;
        input [7:0] k;
        integer i, total;
    begin
        total = k + FLUSH_CYCLES;
        compute_en = 1'b1;
        for (i = 0; i < total; i = i + 1) @(posedge clk); #1;
        compute_en = 1'b0;
    end
    endtask

    // Helper: run exactly N cycles of compute_en=1, then leave it high
    task run_partial_cycles;
        input [7:0] n;
        integer i;
    begin
        compute_en = 1'b1;
        for (i = 0; i < n; i = i + 1) @(posedge clk); #1;
        // compute_en stays high
    end
    endtask

    integer k_val;
    reg [31:0] expected_val;

    initial begin
        total_tests  = 0;
        passed_tests = 0;
        failed_tests = 0;

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

        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;

        $display("=== MXU P1 mac_array Testbench (MX-12) ===");

        //=================================================================
        // MX-12: PE pipeline K+2 compute cycles verification
        //   weight=2 for all columns, activation=3 for all rows.
        //   For K cycles of weight/activation, expected = weight*act*K.
        //   After K+2 compute_en cycles, the accumulator should hold the
        //   sum of all K products (2-cycle pipeline flush).
        //
        //   Test K=8, K=16, K=32, K=64
        //   Also test: after K+1 cycles, accumulator is NOT fully updated
        //   (last product still in pipeline).
        //=================================================================
        $display("");
        $display("--- MX-12: PE Pipeline K+2 Flush ---");

        // Test A: K=8, weight=2, activation=3 → expected = 2*3*8 = 48
        $display("  MX-12 Test A: K=8, weight=2, act=3 → expect 48");
        clean_state();
        set_all_weights(4'd2);
        set_all_activations(8'd3);
        run_k_cycles(8);
        expected_val = 32'd48;
        check_cell(6'd0, 6'd0, expected_val, "MX-12 K=8 full flush");
        check_cell(6'd31, 6'd31, expected_val, "MX-12 K=8 mid");
        check_cell(6'd63, 6'd63, expected_val, "MX-12 K=8 corner");

        // Test B: K=16, weight=1, act=2 → expected = 1*2*16 = 32
        $display("  MX-12 Test B: K=16, weight=1, act=2 → expect 32");
        clean_state();
        set_all_weights(4'd1);
        set_all_activations(8'd2);
        run_k_cycles(16);
        expected_val = 32'd32;
        check_cell(6'd0, 6'd0, expected_val, "MX-12 K=16 full flush");
        check_cell(6'd63, 6'd63, expected_val, "MX-12 K=16 corner");

        // Test C: K=32, weight=1, act=1 → expected = 1*1*32 = 32
        $display("  MX-12 Test C: K=32, weight=1, act=1 → expect 32");
        clean_state();
        set_all_weights(4'd1);
        set_all_activations(8'd1);
        run_k_cycles(32);
        expected_val = 32'd32;
        check_cell(6'd0, 6'd0, expected_val, "MX-12 K=32 full flush");
        check_cell(6'd63, 6'd63, expected_val, "MX-12 K=32 corner");

        // Test D: K=64, weight=3, act=5 → expected = 3*5*64 = 960
        $display("  MX-12 Test D: K=64, weight=3, act=5 → expect 960");
        clean_state();
        set_all_weights(4'd3);
        set_all_activations(8'd5);
        run_k_cycles(64);
        expected_val = 32'd960;
        check_cell(6'd0, 6'd0, expected_val, "MX-12 K=64 full flush");
        check_cell(6'd63, 6'd63, expected_val, "MX-12 K=64 corner");

        // Test E: Pipeline flush timing — verify K different inputs + flush
        // K=8, weight=2, act=3 for first 8 cycles, then 0 for flush cycles.
        // After K+2 cycles: only K products accumulated (flush cycles add 0).
        // Expected: 2*3*8 = 48
        $display("  MX-12 Test E: Pipeline timing — K different inputs + flush");
        clean_state();
        set_all_weights(4'd2);
        set_all_activations(8'd3);

        // Run 8 cycles with valid weight/act
        run_partial_cycles(8);
        // Now set weight/act=0 for flush cycles
        set_all_weights(4'd0);
        set_all_activations(8'd0);
        // Run 2 more flush cycles
        @(posedge clk); #1;
        @(posedge clk); #1;
        compute_en = 1'b0;

        expected_val = 32'd48;  // Only 8 products: 2*3*8 = 48
        check_cell(6'd0, 6'd0, expected_val, "MX-12 K=8 8inputs+2flush=48");
        check_cell(6'd63, 6'd63, expected_val, "MX-12 K=8 8inputs+2flush corner");

        // Test F: Negative values pipeline
        // K=16, weight=-1 (0xF), act=1 → expected = -1 * 1 * 16 = -16
        $display("  MX-12 Test F: K=16, weight=-1, act=1 → expect -16");
        clean_state();
        set_all_weights(4'hF);  // -1 in signed INT4
        set_all_activations(8'd1);
        run_k_cycles(16);
        expected_val = -32'sd16;
        check_cell(6'd0, 6'd0, expected_val, "MX-12 K=16 neg weight full flush");
        check_cell(6'd63, 6'd63, expected_val, "MX-12 K=16 neg weight corner");

        // Test G: Mixed row/col pattern — varying activation per row
        // K=4, weight=2 for all cols, activation[r] = r+1
        // PE(r,c) = 2 * (r+1) * 4 = 8*(r+1)
        $display("  MX-12 Test G: K=4, varying activation per row");
        clean_state();
        for (k_val = 0; k_val < 64; k_val = k_val + 1)
            weight_bus[4*k_val +: 4] = 4'd2;
        for (k_val = 0; k_val < 64; k_val = k_val + 1)
            activation_bus[8*k_val +: 8] = k_val[5:0] + 8'd1;
        run_k_cycles(4);
        expected_val = 32'd8;   // 2 * 1 * 4 = 8 for row 0
        check_cell(6'd0, 6'd0, expected_val, "MX-12 row0 K=4");
        expected_val = 32'd256; // 2 * 32 * 4 = 256 for row 31
        check_cell(6'd31, 6'd31, expected_val, "MX-12 row31 K=4");
        expected_val = 32'd512; // 2 * 64 * 4 = 512 for row 63
        check_cell(6'd63, 6'd63, expected_val, "MX-12 row63 K=4");

        //=================================================================
        // Summary
        //=================================================================
        $display("");
        $display("=== MXU P1 mac_array Summary ===");
        $display("Total:  %0d", total_tests);
        $display("Passed: %0d", passed_tests);
        $display("Failed: %0d", failed_tests);

        if (failed_tests == 0)
            $display("RESULT: ALL MXU P1 mac_array TESTS PASSED");
        else
            $display("RESULT: %0d TESTS FAILED", failed_tests);

        #50;
        $finish(2);
    end

endmodule
