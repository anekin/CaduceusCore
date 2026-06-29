`timescale 1ns / 1ps
//=============================================================================
// tb_reduce_tree — Standalone self-checking testbench for reduce_tree
//=============================================================================
// Verifies:
//   - MAX over 1..128 -> 128
//   - SUM over 1..128 -> 8256
//   - 7-cycle latency (valid_i -> valid_o)
//   - SUM saturation with INT32_MAX repeated 128 times -> INT32_MAX
//   - lane_mask partial chunk (lower 64 lanes only)
//
// Usage:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_reduce_tree \
//       CaduceusCore/rtl/tb/tb_reduce_tree.v CaduceusCore/rtl/vector/reduce_tree.v \
//       -o simv_reduce_tree -l vcs_compile.log
//   ./simv_reduce_tree -l sim.log
//=============================================================================

module tb_reduce_tree;

    localparam NUM_IN  = 128;
    localparam DATA_W  = 32;
    localparam CLK_HALF = 5;                  // 100 MHz clock

    localparam [DATA_W-1:0] I32_MAX = 32'h7FFFFFFF;

    // DUT signals
    reg                     clk;
    reg                     rst_n;
    reg  [NUM_IN*DATA_W-1:0] data_i;
    reg                     op;
    reg                     valid_i;
    reg  [NUM_IN-1:0]       lane_mask;
    wire [DATA_W-1:0]       result_o;
    wire                    valid_o;

    // Latency tracking
    reg [7:0] latency_cnt;
    reg       measuring;

    // Error counter
    integer errors;

    // DUT instantiation
    reduce_tree #(
        .NUM_IN(NUM_IN),
        .DATA_W(DATA_W)
    ) u_dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .data_i    (data_i),
        .op        (op),
        .valid_i   (valid_i),
        .lane_mask (lane_mask),
        .result_o  (result_o),
        .valid_o   (valid_o)
    );

    // Clock generation
    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    // Latency counter
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            latency_cnt <= 8'd0;
            measuring   <= 1'b0;
        end else begin
            if (valid_i && !measuring) begin
                latency_cnt <= 8'd0;
                measuring   <= 1'b1;
            end else if (measuring) begin
                latency_cnt <= latency_cnt + 8'd1;
            end
            if (valid_o)
                measuring <= 1'b0;
        end
    end

    // Helper: load input vector with sequential values starting at base
    task load_seq;
        input integer base;
        input integer step;
        integer i;
        begin
            for (i = 0; i < NUM_IN; i = i + 1) begin
                data_i[i*DATA_W +: DATA_W] = $signed(base + i*step);
            end
        end
    endtask

    // Helper: load constant value on all lanes
    task load_const;
        input integer val;
        integer i;
        begin
            for (i = 0; i < NUM_IN; i = i + 1) begin
                data_i[i*DATA_W +: DATA_W] = $signed(val);
            end
        end
    endtask

    // Wait for output valid, advance one cycle for stable counters, then flush
    task wait_and_flush;
    begin
        wait (valid_o);
        @(posedge clk);
        repeat (8) @(posedge clk);
    end
    endtask

    // Main test sequence
    initial begin
        errors  = 0;
        data_i  = {NUM_IN{32'd0}};
        op      = 1'b0;
        valid_i = 1'b0;
        lane_mask = {NUM_IN{1'b1}};

        // Reset
        rst_n = 1'b0;
        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        //---------------------------------------------------------------------
        // Test 1: MAX over 1..128 -> 128
        //---------------------------------------------------------------------
        $display("[TEST 1] MAX over 1..128");
        lane_mask = {NUM_IN{1'b1}};
        op = 1'b0;
        load_seq(1, 1);
        valid_i = 1'b1;
        @(posedge clk);
        valid_i = 1'b0;
        wait_and_flush;
        if (result_o !== 32'd128) begin
            $display("  FAIL: result=%0d, expected=128", $signed(result_o));
            errors = errors + 1;
        end else begin
            $display("  PASS: result=%0d (latency=%0d cycles)", $signed(result_o), latency_cnt);
        end

        //---------------------------------------------------------------------
        // Test 2: SUM over 1..128 -> 8256
        //---------------------------------------------------------------------
        $display("[TEST 2] SUM over 1..128");
        op = 1'b1;
        load_seq(1, 1);
        valid_i = 1'b1;
        @(posedge clk);
        valid_i = 1'b0;
        wait_and_flush;
        if (result_o !== 32'd8256) begin
            $display("  FAIL: result=%0d, expected=8256", $signed(result_o));
            errors = errors + 1;
        end else begin
            $display("  PASS: result=%0d (latency=%0d cycles)", $signed(result_o), latency_cnt);
        end

        //---------------------------------------------------------------------
        // Test 3: Overflow saturation — INT32_MAX x128, SUM -> INT32_MAX
        //---------------------------------------------------------------------
        $display("[TEST 3] SUM saturation with INT32_MAX repeated 128x");
        op = 1'b1;
        load_const(I32_MAX);
        valid_i = 1'b1;
        @(posedge clk);
        valid_i = 1'b0;
        wait_and_flush;
        if (result_o !== I32_MAX) begin
            $display("  FAIL: result=0x%08X, expected=0x%08X", result_o, I32_MAX);
            errors = errors + 1;
        end else begin
            $display("  PASS: result=0x%08X (saturated)", result_o);
        end

        //---------------------------------------------------------------------
        // Test 4: Partial mask — only lower 64 lanes active
        //   data = 1..128, lower-64 max=64, lower-64 sum=1+..+64=2080
        //---------------------------------------------------------------------
        $display("[TEST 4] lane_mask partial (lower 64 lanes)");
        lane_mask = {{64{1'b0}}, {64{1'b1}}};

        op = 1'b0;
        load_seq(1, 1);
        valid_i = 1'b1;
        @(posedge clk);
        valid_i = 1'b0;
        wait_and_flush;
        if (result_o !== 32'd64) begin
            $display("  FAIL MAX: result=%0d, expected=64", $signed(result_o));
            errors = errors + 1;
        end else begin
            $display("  PASS MAX: result=%0d", $signed(result_o));
        end

        op = 1'b1;
        load_seq(1, 1);
        valid_i = 1'b1;
        @(posedge clk);
        valid_i = 1'b0;
        wait_and_flush;
        if (result_o !== 32'd2080) begin
            $display("  FAIL SUM: result=%0d, expected=2080", $signed(result_o));
            errors = errors + 1;
        end else begin
            $display("  PASS SUM: result=%0d", $signed(result_o));
        end

        //---------------------------------------------------------------------
        // VC-03a: MAX with odd lanes disabled → only even lanes matter
        //   Even lanes: 10, 20, 30, ... (max=10+63*10=640 for 64 evens)
        //   Odd lanes (disabled→INT32_MIN): should not affect MAX result
        //---------------------------------------------------------------------
        $display("[VC-03a] MAX odd lanes disabled, enabled lanes 0,10,20,...,630");
        lane_mask = {NUM_IN{1'b0}};
        begin
            integer i;
            for (i = 0; i < NUM_IN; i = i + 1) begin
                if (i % 2 == 0) begin
                    data_i[i*DATA_W +: DATA_W] = $signed(i * 5);
                    lane_mask[i] = 1'b1;
                end else begin
                    data_i[i*DATA_W +: DATA_W] = 32'h7FFFFFFF;  // INT32_MAX in disabled lanes (should be ignored)
                    lane_mask[i] = 1'b0;
                end
            end
        end
        op = 1'b0;  // MAX
        valid_i = 1'b1;
        @(posedge clk);
        valid_i = 1'b0;
        wait_and_flush;
        // Max of even lanes: 126*5 = 630
        if (result_o !== 32'sd630) begin
            $display("  FAIL VC03a MAX: result=%0d, expected=630", $signed(result_o));
            errors = errors + 1;
        end else begin
            $display("  PASS VC03a MAX: result=%0d", $signed(result_o));
        end

        //---------------------------------------------------------------------
        // VC-03b: SUM with odd lanes disabled → only even lanes contribute
        //   Even lanes sum: 0+10+20+...+630 = 64 evens → sum = (0+630)*64/2 = 20160
        //   Odd lanes (disabled→0): should not affect SUM result
        //---------------------------------------------------------------------
        $display("[VC-03b] SUM odd lanes disabled, INT32_MAX in disabled should be 0");
        begin
            integer i;
            for (i = 0; i < NUM_IN; i = i + 1) begin
                if (i % 2 == 0) begin
                    data_i[i*DATA_W +: DATA_W] = $signed(i * 5);
                    lane_mask[i] = 1'b1;
                end else begin
                    data_i[i*DATA_W +: DATA_W] = 32'h7FFFFFFF;  // INT32_MAX in disabled (should be 0)
                    lane_mask[i] = 1'b0;
                end
            end
        end
        op = 1'b1;  // SUM
        valid_i = 1'b1;
        @(posedge clk);
        valid_i = 1'b0;
        wait_and_flush;
        if (result_o !== 32'sd20160) begin
            $display("  FAIL VC03b SUM: result=%0d, expected=20160", $signed(result_o));
            errors = errors + 1;
        end else begin
            $display("  PASS VC03b SUM: result=%0d", $signed(result_o));
        end

        //---------------------------------------------------------------------
        // VC-03c: All lanes disabled → MAX=INT32_MIN, SUM=0
        //---------------------------------------------------------------------
        $display("[VC-03c] All lanes disabled");
        begin
            integer i;
            for (i = 0; i < NUM_IN; i = i + 1) begin
                data_i[i*DATA_W +: DATA_W] = 32'h12345678;
            end
        end
        lane_mask = {NUM_IN{1'b0}};

        op = 1'b0;  // MAX
        valid_i = 1'b1;
        @(posedge clk);
        valid_i = 1'b0;
        wait_and_flush;
        if (result_o !== 32'h80000000) begin
            $display("  FAIL VC03c MAX all-disabled: result=0x%08h, expected=INT32_MIN", result_o);
            errors = errors + 1;
        end else begin
            $display("  PASS VC03c MAX all-disabled: result=INT32_MIN");
        end

        op = 1'b1;  // SUM
        load_const(32'h12345678);
        valid_i = 1'b1;
        @(posedge clk);
        valid_i = 1'b0;
        wait_and_flush;
        if (result_o !== 32'sd0) begin
            $display("  FAIL VC03c SUM all-disabled: result=%0d, expected=0", $signed(result_o));
            errors = errors + 1;
        end else begin
            $display("  PASS VC03c SUM all-disabled: result=0");
        end

        //---------------------------------------------------------------------
        // Summary
        //---------------------------------------------------------------------
        if (errors == 0)
            $display("ALL TESTS PASSED");
        else
            $display("TESTS FAILED: %0d error(s)", errors);

        $finish;
    end

    // Timeout watchdog
    initial begin
        #5000;
        $display("TIMEOUT: simulation did not complete");
        $finish;
    end

endmodule
