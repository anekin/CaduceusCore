// tb_rope_hw_sf14.sv — SF-14: rope_hw pipeline latency measurement
//
// Feeds NUM_PAIRS=32 (x, y, theta) triples to rope_hw, counts cycles
// from first valid_i to first valid_o, and verifies one output pair
// per cycle after initial delay. Prints ROPE_LATENCY=16 marker.
//
// rope_hw is a 16-stage CORDIC pipeline: valid_i → valid_o = 16 cycles.

`timescale 1ns / 1ps

module tb_rope_hw_sf14;

    localparam NUM_PAIRS = 32;
    localparam STAGES    = 16;

    reg         clk;
    reg         rst_n;
    reg  [15:0] x_i;
    reg  [15:0] y_i;
    reg  [15:0] theta_i;
    reg         valid_i;
    wire [15:0] x_o;
    wire [15:0] y_o;
    wire        valid_o;

    // Cycle counter
    integer cycle;
    integer first_valid_i_cycle;
    integer first_valid_o_cycle;
    integer measured_latency;
    integer prev_valid_o_cycle;
    integer gap_cycles;
    integer max_gap;
    integer output_count;
    integer i;
    integer error_count;

    // DUT instantiation
    rope_hw dut (
        .clk    (clk),
        .rst_n  (rst_n),
        .x_i    (x_i),
        .y_i    (y_i),
        .theta_i(theta_i),
        .valid_i(valid_i),
        .x_o    (x_o),
        .y_o    (y_o),
        .valid_o(valid_o)
    );

    // Clock: 100 MHz
    initial clk = 1'b0;
    always #5 clk = ~clk;

    // Cycle counter
    always @(posedge clk) begin
        cycle <= cycle + 1;
    end

    // Track first valid_i and valid_o cycles
    always @(posedge clk) begin
        if (valid_i && first_valid_i_cycle == 0)
            first_valid_i_cycle <= cycle;
        if (valid_o && first_valid_o_cycle == 0)
            first_valid_o_cycle <= cycle;
    end

    // Track output gap (should be 1 cycle between consecutive outputs)
    always @(posedge clk) begin
        if (valid_o) begin
            if (prev_valid_o_cycle > 0) begin
                gap_cycles = cycle - prev_valid_o_cycle;
                if (gap_cycles > max_gap)
                    max_gap = gap_cycles;
            end
            prev_valid_o_cycle <= cycle;
            output_count <= output_count + 1;
        end
    end

    // Main stimulus
    initial begin
        cycle                = 0;
        first_valid_i_cycle  = 0;
        first_valid_o_cycle  = 0;
        measured_latency     = 0;
        prev_valid_o_cycle   = 0;
        gap_cycles           = 0;
        max_gap              = 0;
        output_count         = 0;
        error_count          = 0;
        x_i                  = 16'd0;
        y_i                  = 16'd0;
        theta_i              = 16'd0;
        valid_i              = 1'b0;

        // Reset
        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;

        $display("[SF-14] Feeding %0d (x, y, theta) pairs to rope_hw", NUM_PAIRS);

        // Feed pairs: x=1.0 (0x3C00), y=2.0 (0x4000), theta=0.5 (0x3800)
        for (i = 0; i < NUM_PAIRS; i = i + 1) begin
            @(posedge clk);
            x_i     <= 16'h3C00;   // 1.0
            y_i     <= 16'h4000;   // 2.0
            theta_i <= 16'h3800;   // 0.5
            valid_i <= 1'b1;
        end

        @(posedge clk);
        valid_i <= 1'b0;
        x_i     <= 16'd0;
        y_i     <= 16'd0;
        theta_i <= 16'd0;

        $display("[SF-14] Input feeding done at cycle %0d", cycle);

        // Wait for all outputs
        while (output_count < NUM_PAIRS) begin
            @(posedge clk);
        end

        // Compute measured latency
        if (first_valid_i_cycle > 0 && first_valid_o_cycle > 0) begin
            measured_latency = first_valid_o_cycle - first_valid_i_cycle;
            $display("ROPE_LATENCY=%0d (first_valid_i@cycle=%0d, first_valid_o@cycle=%0d, measured=%0d)",
                     measured_latency,
                     first_valid_i_cycle, first_valid_o_cycle,
                     measured_latency);

            // Verify 16-cycle delay
            if (measured_latency >= STAGES - 1 && measured_latency <= STAGES + 1)
                $display("[SF-14] ROPE LATENCY CHECK: %0d cycles (expected %0d) — PASS",
                         measured_latency, STAGES);
            else begin
                $display("[SF-14] ROPE LATENCY CHECK: %0d cycles (expected %0d) — FAIL",
                         measured_latency, STAGES);
                error_count = error_count + 1;
            end
        end else begin
            $display("ROPE_LATENCY=ERROR (failed to capture valid_i or valid_o)");
            error_count = error_count + 1;
        end

        // Verify one output per cycle (max gap = 1)
        if (max_gap <= 1)
            $display("[SF-14] OUTPUT GAP CHECK: max_gap=%0d (expected 1) — PASS", max_gap);
        else begin
            $display("[SF-14] OUTPUT GAP CHECK: max_gap=%0d (expected 1) — FAIL", max_gap);
            error_count = error_count + 1;
        end

        // Verify all outputs received
        if (output_count == NUM_PAIRS)
            $display("[SF-14] OUTPUT COUNT CHECK: %0d pairs (expected %0d) — PASS",
                     output_count, NUM_PAIRS);
        else begin
            $display("[SF-14] OUTPUT COUNT CHECK: %0d pairs (expected %0d) — FAIL",
                     output_count, NUM_PAIRS);
            error_count = error_count + 1;
        end

        if (error_count == 0)
            $display("[SF-14] ALL CHECKS PASSED");
        else
            $display("[SF-14] %0d CHECK(S) FAILED", error_count);

        #20;
        $finish(2);
    end

    // Timeout watchdog
    initial begin
        #500000;
        $display("[SF-14] TIMEOUT");
        $finish(2);
    end

endmodule
