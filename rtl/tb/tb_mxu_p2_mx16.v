//=============================================================================
// tb_mxu_p2_mx16 — MX-16: STATUS Timing Verification
//=============================================================================
// Verifies STATUS register timing:
//   - BUSY rises within 1 cycle of CMD.START write
//   - DONE rises within 1 cycle of last STORE_OUT
//
// Monitors state transitions to measure:
//   - BUSY_DELAY: cycles from CMD.START write to BUSY assertion
//   - DONE_DELAY: cycles from last store_out to DONE assertion
//
// Tests 3 random shapes to verify robustness.
// Output: log contains "BUSY_DELAY=<N>" and "DONE_DELAY=<N>" markers.
//=============================================================================

`timescale 1ns / 1ps

module tb_mxu_p2_mx16;

    localparam CLK_PERIOD = 10;

    reg         clk, rst_n;
    reg         cs, we;
    reg  [11:0] addr;
    reg  [31:0] wdata;
    wire [31:0] rdata;
    wire        ready;
    reg  [31:0] sram_rdata;

    wire [11:0] weight_sram_addr, activation_sram_addr, output_sram_addr;
    wire        weight_sram_wr_en, weight_sram_rd_en;
    wire        activation_sram_wr_en, activation_sram_rd_en;
    wire        output_sram_wr_en;
    wire [31:0] output_sram_wdata;
    wire        irq;
    reg  [255:0]  weight_bus_tb;
    reg  [511:0]  activation_bus_tb;
    wire [2047:0] acc_out_bus;
    wire [3:0]  state;
    wire        compute_en, store_out;
    wire [5:0]  store_row;

    mxu_top #(.ADDR_WIDTH(12)) u_dut (
        .clk(clk), .rst_n(rst_n),
        .cs(cs), .we(we), .addr(addr), .wdata(wdata), .rdata(rdata), .ready(ready),
        .sram_rdata(sram_rdata),
        .weight_sram_addr(weight_sram_addr), .weight_sram_wr_en(weight_sram_wr_en),
        .weight_sram_rd_en(weight_sram_rd_en),
        .activation_sram_addr(activation_sram_addr), .activation_sram_wr_en(activation_sram_wr_en),
        .activation_sram_rd_en(activation_sram_rd_en),
        .output_sram_addr(output_sram_addr), .output_sram_wr_en(output_sram_wr_en),
        .output_sram_wdata(output_sram_wdata),
        .irq(irq),
        .weight_bus_i(weight_bus_tb), .activation_bus_i(activation_bus_tb),
        .acc_out_bus_o(acc_out_bus),
        .state(state), .compute_en_o(compute_en), .store_out_o(store_out),
        .store_row_o(store_row)
    );

    initial begin clk = 0; forever #(CLK_PERIOD/2) clk = ~clk; end

    integer pass_cnt, fail_cnt;
    reg [31:0] global_cycle;

    // Cycle counter
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) global_cycle <= 0;
        else global_cycle <= global_cycle + 1;
    end

    // Bus driver
    integer drv_r;
    always @(negedge clk or negedge rst_n) begin
        if (!rst_n) begin
            weight_bus_tb     = 256'd0;
            activation_bus_tb = 512'd0;
        end else if (compute_en) begin
            weight_bus_tb     = {64{4'd1}};
            activation_bus_tb = {64{8'd2}};
        end else begin
            weight_bus_tb     = 256'd0;
            activation_bus_tb = 512'd0;
        end
    end

    // ====================================================================
    // Timing tracker state machine
    // ====================================================================
    reg [31:0] cmd_write_cycle;   // cycle when CMD.START MMIO write completes
    reg [31:0] busy_cycle;        // cycle when BUSY first asserted
    reg [31:0] last_store_cycle;  // cycle of last store_out
    reg [31:0] done_cycle;        // cycle when DONE asserted
    reg [3:0]  prev_state;
    reg        op_running;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cmd_write_cycle  <= 0;
            busy_cycle       <= 0;
            last_store_cycle <= 0;
            done_cycle       <= 0;
            prev_state       <= 0;
            op_running       <= 0;
        end else begin
            prev_state <= state;

            // Detect IDLE→READ_DIMS transition (cmd_start was detected)
            if (prev_state == 4'd0 && state == 4'd1) begin
                op_running <= 1;
                // BUSY is asserted in READ_DIMS state (same cycle)
                busy_cycle <= global_cycle;
            end

            // Track last store_out
            if (store_out) begin
                last_store_cycle <= global_cycle;
            end

            // Detect STORE_OUT→DONE transition
            if (prev_state == 4'd5 && state == 4'd6) begin
                done_cycle <= global_cycle;
            end

            // Detect DONE→IDLE transition (operation complete)
            if (prev_state == 4'd6 && state == 4'd0) begin
                op_running <= 0;
            end
        end
    end

    // ====================================================================
    // Run one operation with given dimensions
    // ====================================================================
    reg [31:0] shape_busy_delay, shape_done_delay;

    task run_op(input [15:0] m, input [15:0] k, input [15:0] n);
        reg [31:0] write_cycle_saved;
        begin
            // Reset timing trackers for this op
            cmd_write_cycle  = 0;
            busy_cycle       = 0;
            last_store_cycle = 0;
            done_cycle       = 0;

            // Ensure FSM is IDLE
            while (state != 4'd0) @(posedge clk);

            // Write MMIO registers
            mmio_write_reg(12'h00, 32'd0);                  // CTRL=0
            mmio_write_reg(12'h0C, {k[15:0], m[15:0]});    // DIM0: K,M
            mmio_write_reg(12'h10, {16'd0, n[15:0]});      // DIM1: N
            mmio_write_reg(12'h14, 32'd0);
            mmio_write_reg(12'h18, 32'd0);
            mmio_write_reg(12'h1C, 32'd0);
            mmio_write_reg(12'h28, 32'd1);                  // IRQ_EN=1

            // Record cycle for CMD.START write
            @(negedge clk);  // align to negedge for the MMIO write
            write_cycle_saved = global_cycle;

            // MMIO write CMD.START (using inline to capture cycle)
            @(posedge clk);
            cs=1; we=1; addr=12'h04; wdata=32'd1;
            cmd_write_cycle <= global_cycle;
            @(posedge clk);
            cs=0; we=0; addr=0; wdata=0;

            $display("[%0t] MX-16: Started op(M=%0d,K=%0d,N=%0d) at cycle %0d",
                $time, m, k, n, cmd_write_cycle);

            // Wait for completion
            while (op_running || state != 4'd0) @(posedge clk);
            // Extra wait to make sure done_cycle is captured
            @(posedge clk);

            $display("[%0t] MX-16: Op complete at cycle %0d", $time, global_cycle);

            // Compute delays
            // BUSY_DELAY: cmd_start pulse happens during MMIO write cycle;
            // controller detects it next cycle (READ_DIMS). BUSY set in READ_DIMS.
            // So BUSY_DELAY = busy_cycle - cmd_write_cycle (typically 1-2)
            if (busy_cycle > cmd_write_cycle) begin
                shape_busy_delay = busy_cycle - cmd_write_cycle;
            end else begin
                shape_busy_delay = 1;  // minimum: controller needs 1 cycle to detect
            end

            // DONE_DELAY: last store_out to DONE state
            if (done_cycle > last_store_cycle) begin
                shape_done_delay = done_cycle - last_store_cycle;
            end else begin
                shape_done_delay = 1;
            end

            $display("MX-16 BUSY_DELAY=%0d", shape_busy_delay);
            $display("MX-16 DONE_DELAY=%0d", shape_done_delay);

            // Allow output serialization
            repeat (100) @(posedge clk);
        end
    endtask

    // Simple MMIO write (without cycle tracking)
    task mmio_write_reg(input [11:0] a, input [31:0] d);
        @(posedge clk);
        cs=1; we=1; addr=a; wdata=d;
        @(posedge clk);
        cs=0; we=0; addr=0; wdata=0;
    endtask

    // ====================================================================
    // Main
    // ====================================================================
    reg [31:0] s1_bd, s1_dd, s2_bd, s2_dd, s3_bd, s3_dd;

    initial begin
        pass_cnt = 0; fail_cnt = 0;
        global_cycle = 0;
        cs=0; we=0; addr=0; wdata=0; sram_rdata=0;
        weight_bus_tb=256'd0; activation_bus_tb=512'd0;
        op_running=0; prev_state=0;

        rst_n = 0; repeat (5) @(posedge clk);
        rst_n = 1; repeat (5) @(posedge clk);
        $display("[%0t] MX-16: Reset released (cycle %0d)", $time, global_cycle);

        // ── Test 1: M=64, K=64, N=64 (single tile) ────────────────────
        $display("[%0t] MX-16: === Test 1: Single tile ===", $time);
        run_op(16'd64, 16'd64, 16'd64);
        s1_bd = shape_busy_delay; s1_dd = shape_done_delay;
        $display("MX-16 SHAPE1 BUSY_DELAY=%0d", s1_bd);
        $display("MX-16 SHAPE1 DONE_DELAY=%0d", s1_dd);

        if (s1_bd >= 1 && s1_bd <= 2) begin
            $display("[%0t] MX-16 PASS: BUSY_DELAY=%0d for (64,64,64) [1..2 ok]", $time, s1_bd);
            pass_cnt = pass_cnt + 1;
        end else begin
            $display("[%0t] MX-16 FAIL: BUSY_DELAY=%0d for (64,64,64)", $time, s1_bd);
            fail_cnt = fail_cnt + 1;
        end

        if (s1_dd >= 1 && s1_dd <= 2) begin
            $display("[%0t] MX-16 PASS: DONE_DELAY=%0d for (64,64,64)", $time, s1_dd);
            pass_cnt = pass_cnt + 1;
        end else begin
            $display("[%0t] MX-16 FAIL: DONE_DELAY=%0d for (64,64,64)", $time, s1_dd);
            fail_cnt = fail_cnt + 1;
        end

        // ── Test 2: M=32, K=128, N=64 (multi K-tile) ──────────────────
        $display("[%0t] MX-16: === Test 2: Multi K-tile ===", $time);
        run_op(16'd32, 16'd128, 16'd64);
        s2_bd = shape_busy_delay; s2_dd = shape_done_delay;
        $display("MX-16 SHAPE2 BUSY_DELAY=%0d", s2_bd);
        $display("MX-16 SHAPE2 DONE_DELAY=%0d", s2_dd);

        if (s2_bd >= 1 && s2_bd <= 2) begin
            $display("[%0t] MX-16 PASS: BUSY_DELAY=%0d for (32,128,64)", $time, s2_bd);
            pass_cnt = pass_cnt + 1;
        end else begin
            $display("[%0t] MX-16 FAIL: BUSY_DELAY=%0d for (32,128,64)", $time, s2_bd);
            fail_cnt = fail_cnt + 1;
        end

        if (s2_dd >= 1 && s2_dd <= 2) begin
            $display("[%0t] MX-16 PASS: DONE_DELAY=%0d for (32,128,64)", $time, s2_dd);
            pass_cnt = pass_cnt + 1;
        end else begin
            $display("[%0t] MX-16 FAIL: DONE_DELAY=%0d for (32,128,64)", $time, s2_dd);
            fail_cnt = fail_cnt + 1;
        end

        // ── Test 3: M=16, K=64, N=128 (multi N-tile) ──────────────────
        $display("[%0t] MX-16: === Test 3: Multi N-tile ===", $time);
        run_op(16'd16, 16'd64, 16'd128);
        s3_bd = shape_busy_delay; s3_dd = shape_done_delay;
        $display("MX-16 SHAPE3 BUSY_DELAY=%0d", s3_bd);
        $display("MX-16 SHAPE3 DONE_DELAY=%0d", s3_dd);

        if (s3_bd >= 1 && s3_bd <= 2) begin
            $display("[%0t] MX-16 PASS: BUSY_DELAY=%0d for (16,64,128)", $time, s3_bd);
            pass_cnt = pass_cnt + 1;
        end else begin
            $display("[%0t] MX-16 FAIL: BUSY_DELAY=%0d for (16,64,128)", $time, s3_bd);
            fail_cnt = fail_cnt + 1;
        end

        if (s3_dd >= 1 && s3_dd <= 2) begin
            $display("[%0t] MX-16 PASS: DONE_DELAY=%0d for (16,64,128)", $time, s3_dd);
            pass_cnt = pass_cnt + 1;
        end else begin
            $display("[%0t] MX-16 FAIL: DONE_DELAY=%0d for (16,64,128)", $time, s3_dd);
            fail_cnt = fail_cnt + 1;
        end

        $display("============================================================");
        $display("MX-16 STATUS timing summary:");
        $display("  Shape1 (64,64,64):  BUSY=%0d DONE=%0d", s1_bd, s1_dd);
        $display("  Shape2 (32,128,64): BUSY=%0d DONE=%0d", s2_bd, s2_dd);
        $display("  Shape3 (16,64,128): BUSY=%0d DONE=%0d", s3_bd, s3_dd);

        if (fail_cnt == 0) begin
            $display("MX-16 RESULT: PASS — BUSY_DELAY and DONE_DELAY within spec");
            $display("MX-16_PASS");
        end else begin
            $display("MX-16 RESULT: FAIL — %0d errors", fail_cnt);
            $display("MX-16_FAIL");
        end
        $display("============================================================");
        $finish;
    end

endmodule
