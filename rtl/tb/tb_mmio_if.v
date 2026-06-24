`timescale 1ns / 1ps
//=============================================================================
// tb_mmio_if — Self-checking testbench for mmio_if register file
//=============================================================================
// Tests:
//   1. Reset initialization — all outputs zero
//   2. Write CTRL, read back
//   3. Write CMD.START, verify cmd_start pulse
//   4. Write CMD.ABORT, verify cmd_abort pulse
//   5. Write CMD (both bits), verify both pulses
//   6. Read CMD — returns 0 (write-only)
//   7. Read STATUS with externally driven busy/done/error
//   8. Write DIM0={M=64, K=64}, read back, verify field outputs
//   9. Write DIM1={N=64}, read back
//  10. Write/read all address registers (I/W/O/BIAS/SCALE)
//  11. Write/read IRQ_EN
//  12. Write to undefined address, read back 0
//  13. cs=0 → rdata=0
//=============================================================================

module tb_mmio_if;

    //-------------------------------------------------------------------------
    // DUT signals
    //-------------------------------------------------------------------------
    reg         clk;
    reg         rst_n;
    reg         cs;
    reg         we;
    reg  [11:0] addr;
    reg  [31:0] wdata;
    wire [31:0] rdata;
    wire        ready;
    reg         status_busy;
    reg         status_done;
    reg         status_error;
    wire        cmd_start;
    wire        cmd_abort;
    wire [1:0]  ctrl_dtype;
    wire [15:0] dim0_m;
    wire [15:0] dim0_k;
    wire [15:0] dim1_n;
    wire [31:0] i_addr_o;
    wire [31:0] w_addr_o;
    wire [31:0] o_addr_o;
    wire [31:0] bias_addr_o;
    wire [31:0] scale_addr_o;
    wire        irq_en_o;

    //-------------------------------------------------------------------------
    // DUT instantiation
    //-------------------------------------------------------------------------
    mmio_if u_dut (
        .clk         (clk),
        .rst_n       (rst_n),
        .cs          (cs),
        .we          (we),
        .addr        (addr),
        .wdata       (wdata),
        .rdata       (rdata),
        .ready       (ready),
        .status_busy (status_busy),
        .status_done (status_done),
        .status_error(status_error),
        .cmd_start   (cmd_start),
        .cmd_abort   (cmd_abort),
        .ctrl_dtype  (ctrl_dtype),
        .dim0_m      (dim0_m),
        .dim0_k      (dim0_k),
        .dim1_n      (dim1_n),
        .i_addr_o    (i_addr_o),
        .w_addr_o    (w_addr_o),
        .o_addr_o    (o_addr_o),
        .bias_addr_o (bias_addr_o),
        .scale_addr_o(scale_addr_o),
        .irq_en_o    (irq_en_o)
    );

    //-------------------------------------------------------------------------
    // Clock generation: 10 ns period (100 MHz)
    //-------------------------------------------------------------------------
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    //-------------------------------------------------------------------------
    // Test accounting
    //-------------------------------------------------------------------------
    integer total_tests;
    integer passed_tests;
    integer failed_tests;

    //-------------------------------------------------------------------------
    // Helper: check 32-bit value
    //-------------------------------------------------------------------------
    task automatic check32;
        input [31:0] actual;
        input [31:0] expected;
        input [255:0] desc;
    begin
        total_tests = total_tests + 1;
        if (actual !== expected) begin
            $display("FAIL [%0s]: expected=0x%08h got=0x%08h",
                     desc, expected, actual);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [%0s]: value=0x%08h", desc, expected);
            passed_tests = passed_tests + 1;
        end
    end
    endtask

    //-------------------------------------------------------------------------
    // Helper: check 1-bit signal
    //-------------------------------------------------------------------------
    task automatic check_bit;
        input        actual;
        input        expected;
        input [255:0] desc;
    begin
        total_tests = total_tests + 1;
        if (actual !== expected) begin
            $display("FAIL [%0s]: expected=%0b got=%0b",
                     desc, expected, actual);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [%0s]: value=%0b", desc, expected);
            passed_tests = passed_tests + 1;
        end
    end
    endtask

    //-------------------------------------------------------------------------
    // MMIO access tasks — drives happen after posedge; #1 defers past active
    // region so DUT always block reads correct (current) values.
    //-------------------------------------------------------------------------

    // Write wdata to addr. Data is latched on next posedge.
    task automatic mmio_write;
        input [11:0] a;
        input [31:0] d;
    begin
        @(posedge clk); #1;
        cs    = 1'b1;
        we    = 1'b1;
        addr  = a;
        wdata = d;
        @(posedge clk); #1;
        cs    = 1'b0;
        we    = 1'b0;
    end
    endtask

    // Read from addr. Returns combinatorial value after #1 delay.
    task automatic mmio_read;
        input  [11:0] a;
        output [31:0] d;
    begin
        @(posedge clk); #1;
        cs   = 1'b1;
        we   = 1'b0;
        addr = a;
        #1;
        d = rdata;
        @(posedge clk); #1;
        cs   = 1'b0;
    end
    endtask

    //-------------------------------------------------------------------------
    // Test sequence
    //-------------------------------------------------------------------------
    reg [31:0] rd;

    initial begin
        total_tests  = 0;
        passed_tests = 0;
        failed_tests = 0;

        // Defaults
        cs           = 1'b0;
        we           = 1'b0;
        addr         = 12'd0;
        wdata        = 32'd0;
        status_busy  = 1'b0;
        status_done  = 1'b0;
        status_error = 1'b0;

        // --- Power-on reset ---
        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;
        $display("=== MMIO Register File Self-Checking Testbench ===");
        $display("");

        //===============================================================
        // Test 1: Reset state — rdata=0, all outputs zero
        //===============================================================
        $display("--- Test 1: Post-reset defaults ---");
        // rdata should be 0 when cs=0
        @(posedge clk); #1;
        check32(rdata, 32'd0, "rdata after reset (cs=0)");
        check_bit(ready, 1'b0, "ready=0 when cs=0");
        check32({30'd0, ctrl_dtype}, 32'd0, "ctrl_dtype=0");
        check32({16'd0, dim0_m}, 32'd0, "dim0_m=0");
        check32({16'd0, dim0_k}, 32'd0, "dim0_k=0");
        check32({16'd0, dim1_n}, 32'd0, "dim1_n=0");
        check32(i_addr_o, 32'd0, "i_addr_o=0");
        check32(w_addr_o, 32'd0, "w_addr_o=0");
        check32(o_addr_o, 32'd0, "o_addr_o=0");
        check32(bias_addr_o, 32'd0, "bias_addr_o=0");
        check32(scale_addr_o, 32'd0, "scale_addr_o=0");
        check_bit(irq_en_o, 1'b0, "irq_en_o=0");
        check_bit(cmd_start, 1'b0, "cmd_start=0");
        check_bit(cmd_abort, 1'b0, "cmd_abort=0");

        //===============================================================
        // Test 2: Write CTRL, read back
        //===============================================================
        $display("--- Test 2: CTRL write/read ---");

        mmio_write(12'h00, 32'd1);  // dtype=INT4xINT8 (0) + unused bits
        mmio_read(12'h00, rd);
        check32(rd, 32'd1, "CTRL after write(1)");
        check32({30'd0, ctrl_dtype}, 32'd1, "ctrl_dtype output=1");

        mmio_write(12'h00, 32'd2);  // dtype=INT8xINT8 (1)
        mmio_read(12'h00, rd);
        check32(rd, 32'd2, "CTRL after write(2)");
        check32({30'd0, ctrl_dtype}, 32'd2, "ctrl_dtype output=2");

        mmio_write(12'h00, 32'd0);  // back to 0
        mmio_read(12'h00, rd);
        check32(rd, 32'd0, "CTRL after write(0)");

        //===============================================================
        // Test 3: Write CMD.START, verify cmd_start pulse
        //===============================================================
        $display("--- Test 3: CMD.START pulse ---");

        // cmd_start should be 0 before write
        check_bit(cmd_start, 1'b0, "cmd_start before CMD write");

        @(posedge clk); #1;
        cs    = 1'b1;
        we    = 1'b1;
        addr  = 12'h04;
        wdata = 32'd1;    // bit[0]=START=1, bit[1]=ABORT=0

        // After this posedge, cmd_start should pulse
        @(posedge clk); #1;
        cs    = 1'b0;
        we    = 1'b0;
        check_bit(cmd_start, 1'b1, "cmd_start pulsed after CMD.START");
        check_bit(cmd_abort, 1'b0, "cmd_abort NOT pulsed");

        // Next cycle: pulse should clear
        @(posedge clk); #1;
        check_bit(cmd_start, 1'b0, "cmd_start cleared next cycle");

        //===============================================================
        // Test 4: Write CMD.ABORT, verify cmd_abort pulse
        //===============================================================
        $display("--- Test 4: CMD.ABORT pulse ---");

        @(posedge clk); #1;
        cs    = 1'b1;
        we    = 1'b1;
        addr  = 12'h04;
        wdata = 32'd2;    // bit[1]=ABORT=1

        @(posedge clk); #1;
        cs    = 1'b0;
        we    = 1'b0;
        check_bit(cmd_abort, 1'b1, "cmd_abort pulsed after CMD.ABORT");
        check_bit(cmd_start, 1'b0, "cmd_start NOT pulsed");

        @(posedge clk); #1;
        check_bit(cmd_abort, 1'b0, "cmd_abort cleared next cycle");

        //===============================================================
        // Test 5: Write CMD with both bits set → both pulses
        //===============================================================
        $display("--- Test 5: CMD both bits ---");

        @(posedge clk); #1;
        cs    = 1'b1;
        we    = 1'b1;
        addr  = 12'h04;
        wdata = 32'd3;    // bit[0]=START=1, bit[1]=ABORT=1

        @(posedge clk); #1;
        cs    = 1'b0;
        we    = 1'b0;
        check_bit(cmd_start, 1'b1, "cmd_start pulsed (both bits)");
        check_bit(cmd_abort, 1'b1, "cmd_abort pulsed (both bits)");

        @(posedge clk); #1;
        check_bit(cmd_start, 1'b0, "cmd_start cleared");
        check_bit(cmd_abort, 1'b0, "cmd_abort cleared");

        //===============================================================
        // Test 6: Read CMD — returns 0 (write-only)
        //===============================================================
        $display("--- Test 6: CMD read returns 0 ---");
        mmio_read(12'h04, rd);
        check32(rd, 32'd0, "CMD read returns 0");

        //===============================================================
        // Test 7: STATUS register — driven by external inputs
        //===============================================================
        $display("--- Test 7: STATUS register ---");

        // All inputs = 0
        status_busy  = 1'b0;
        status_done  = 1'b0;
        status_error = 1'b0;
        mmio_read(12'h08, rd);
        check32(rd, 32'd0, "STATUS all zero");

        // Set busy
        @(posedge clk); #1;
        status_busy = 1'b1;
        mmio_read(12'h08, rd);
        check32(rd, 32'd1, "STATUS busy=1");

        // Set done, clear busy
        @(posedge clk); #1;
        status_busy = 1'b0;
        status_done = 1'b1;
        mmio_read(12'h08, rd);
        check32(rd, 32'd2, "STATUS done=1");

        // Set error, clear done
        @(posedge clk); #1;
        status_done  = 1'b0;
        status_error = 1'b1;
        mmio_read(12'h08, rd);
        check32(rd, 32'd4, "STATUS error=1");

        // Busy + Done + Error simultaneously
        @(posedge clk); #1;
        status_busy  = 1'b1;
        status_done  = 1'b1;
        status_error = 1'b1;
        mmio_read(12'h08, rd);
        check32(rd, 32'd7, "STATUS all=1");

        // Clear all
        @(posedge clk); #1;
        status_busy  = 1'b0;
        status_done  = 1'b0;
        status_error = 1'b0;

        // Verify STATUS is read-only (write has no effect)
        mmio_write(12'h08, 32'hFFFFFFFF);
        mmio_read(12'h08, rd);
        check32(rd, 32'd0, "STATUS read-only (writes ignored)");

        //===============================================================
        // Test 8: Write DIM0, read back
        //===============================================================
        $display("--- Test 8: DIM0 write/read ---");

        mmio_write(12'h0C, {16'd64, 16'd64});  // M=64, K=64
        mmio_read(12'h0C, rd);
        check32(rd, {16'd64, 16'd64}, "DIM0={M=64, K=64}");
        check32({16'd0, dim0_m}, 32'd64, "dim0_m=64");
        check32({16'd0, dim0_k}, 32'd64, "dim0_k=64");

        // Different values — register layout: [31:16]=K, [15:0]=M
        mmio_write(12'h0C, {16'd256, 16'd128});  // K=256 in [31:16], M=128 in [15:0]
        mmio_read(12'h0C, rd);
        check32(rd, {16'd256, 16'd128}, "DIM0={M=128, K=256}");
        check32({16'd0, dim0_m}, 32'd128, "dim0_m=128");
        check32({16'd0, dim0_k}, 32'd256, "dim0_k=256");

        //===============================================================
        // Test 9: Write DIM1, read back
        //===============================================================
        $display("--- Test 9: DIM1 write/read ---");

        mmio_write(12'h10, {16'd0, 16'd64});  // N=64 (upper bits reserved)
        mmio_read(12'h10, rd);
        check32(rd, {16'd0, 16'd64}, "DIM1={N=64}");
        check32({16'd0, dim1_n}, 32'd64, "dim1_n=64");

        mmio_write(12'h10, {16'd0, 16'd128});
        mmio_read(12'h10, rd);
        check32(rd, {16'd0, 16'd128}, "DIM1={N=128}");
        check32({16'd0, dim1_n}, 32'd128, "dim1_n=128");

        //===============================================================
        // Test 10: Write/read address registers
        //===============================================================
        $display("--- Test 10: Address register write/read ---");

        mmio_write(12'h14, 32'hDEADBEEF);
        mmio_read(12'h14, rd);
        check32(rd, 32'hDEADBEEF, "I_ADDR=0xDEADBEEF");
        check32(i_addr_o, 32'hDEADBEEF, "i_addr_o output");

        mmio_write(12'h18, 32'hCAFEBABE);
        mmio_read(12'h18, rd);
        check32(rd, 32'hCAFEBABE, "W_ADDR=0xCAFEBABE");
        check32(w_addr_o, 32'hCAFEBABE, "w_addr_o output");

        mmio_write(12'h1C, 32'h12345678);
        mmio_read(12'h1C, rd);
        check32(rd, 32'h12345678, "O_ADDR=0x12345678");
        check32(o_addr_o, 32'h12345678, "o_addr_o output");

        mmio_write(12'h20, 32'hABCDEF00);
        mmio_read(12'h20, rd);
        check32(rd, 32'hABCDEF00, "BIAS_ADDR=0xABCDEF00");
        check32(bias_addr_o, 32'hABCDEF00, "bias_addr_o output");

        mmio_write(12'h24, 32'h11223344);
        mmio_read(12'h24, rd);
        check32(rd, 32'h11223344, "SCALE_ADDR=0x11223344");
        check32(scale_addr_o, 32'h11223344, "scale_addr_o output");

        // BIAS_ADDR=0 (no bias)
        mmio_write(12'h20, 32'd0);
        mmio_read(12'h20, rd);
        check32(rd, 32'd0, "BIAS_ADDR=0 (no bias)");
        check32(bias_addr_o, 32'd0, "bias_addr_o=0");

        // SCALE_ADDR=0 (no scale)
        mmio_write(12'h24, 32'd0);
        mmio_read(12'h24, rd);
        check32(rd, 32'd0, "SCALE_ADDR=0 (no scale)");

        //===============================================================
        // Test 11: IRQ_EN write/read
        //===============================================================
        $display("--- Test 11: IRQ_EN write/read ---");

        mmio_write(12'h28, 32'd1);
        mmio_read(12'h28, rd);
        check32(rd, 32'd1, "IRQ_EN=1");
        check_bit(irq_en_o, 1'b1, "irq_en_o=1");

        mmio_write(12'h28, 32'd0);
        mmio_read(12'h28, rd);
        check32(rd, 32'd0, "IRQ_EN=0");
        check_bit(irq_en_o, 1'b0, "irq_en_o=0");

        // Upper bits should also be stored
        mmio_write(12'h28, 32'hFFFFFFFF);
        mmio_read(12'h28, rd);
        check32(rd, 32'hFFFFFFFF, "IRQ_EN=all 1s");
        check_bit(irq_en_o, 1'b1, "irq_en_o=1 (LSB)");

        mmio_write(12'h28, 32'd0);

        //===============================================================
        // Test 12: Write to undefined address → read back 0
        //===============================================================
        $display("--- Test 12: Undefined addresses ---");

        mmio_write(12'h2C, 32'hFFFFFFFF);
        mmio_read(12'h2C, rd);
        check32(rd, 32'd0, "Undefined 0x2C read=0");

        mmio_write(12'hFF, 32'hDEADBEEF);
        mmio_read(12'hFF, rd);
        check32(rd, 32'd0, "Undefined 0xFF read=0");

        mmio_write(12'h100, 32'hCAFEBABE);
        mmio_read(12'h100, rd);
        check32(rd, 32'd0, "Undefined 0x100 read=0");

        //===============================================================
        // Test 13: cs=0 → rdata=0 even with valid addr
        //===============================================================
        $display("--- Test 13: cs=0 behavior ---");

        @(posedge clk); #1;
        cs   = 1'b0;
        we   = 1'b0;
        addr = 12'h00;   // valid address
        #1;
        check32(rdata, 32'd0, "rdata=0 when cs=0");
        check_bit(ready, 1'b0, "ready=0 when cs=0");

        //===============================================================
        // Test 14: ready asserted when cs=1
        //===============================================================
        $display("--- Test 14: ready signal ---");

        @(posedge clk); #1;
        cs   = 1'b1;
        we   = 1'b0;
        addr = 12'h00;
        #1;
        check_bit(ready, 1'b1, "ready=1 during read");

        @(posedge clk); #1;
        cs   = 1'b1;
        we   = 1'b1;
        addr = 12'h00;
        #1;
        check_bit(ready, 1'b1, "ready=1 during write");

        @(posedge clk); #1;
        cs   = 1'b0;
        #1;
        check_bit(ready, 1'b0, "ready=0 after cs deassert");

        //===============================================================
        // Test 15: Write to CMD with START=0 → no pulse
        //===============================================================
        $display("--- Test 15: CMD write with START=0 ---");

        @(posedge clk); #1;
        cs    = 1'b1;
        we    = 1'b1;
        addr  = 12'h04;
        wdata = 32'd0;    // both START and ABORT = 0

        @(posedge clk); #1;
        cs    = 1'b0;
        we    = 1'b0;
        check_bit(cmd_start, 1'b0, "cmd_start=0 when START bit not set");
        check_bit(cmd_abort, 1'b0, "cmd_abort=0 when ABORT bit not set");

        //===============================================================
        // Test 16: Write then read all registers in sequence
        //           (verify no cross-register interference)
        //===============================================================
        $display("--- Test 16: No cross-register interference ---");

        mmio_write(12'h00, 32'h00000001);  // CTRL
        mmio_write(12'h0C, {16'd32, 16'd32});  // DIM0
        mmio_write(12'h10, {16'd0, 16'd32});   // DIM1
        mmio_write(12'h14, 32'h00001000);  // I_ADDR
        mmio_write(12'h18, 32'h00002000);  // W_ADDR
        mmio_write(12'h1C, 32'h00003000);  // O_ADDR
        mmio_write(12'h20, 32'h00004000);  // BIAS_ADDR
        mmio_write(12'h24, 32'h00005000);  // SCALE_ADDR
        mmio_write(12'h28, 32'd1);         // IRQ_EN

        // Read all back in random order
        mmio_read(12'h1C, rd);
        check32(rd, 32'h00003000, "O_ADDR intact");
        mmio_read(12'h00, rd);
        check32(rd, 32'h00000001, "CTRL intact");
        mmio_read(12'h0C, rd);
        check32(rd, {16'd32, 16'd32}, "DIM0 intact");
        mmio_read(12'h14, rd);
        check32(rd, 32'h00001000, "I_ADDR intact");
        mmio_read(12'h28, rd);
        check32(rd, 32'd1, "IRQ_EN intact");
        mmio_read(12'h10, rd);
        check32(rd, {16'd0, 16'd32}, "DIM1 intact");
        mmio_read(12'h18, rd);
        check32(rd, 32'h00002000, "W_ADDR intact");
        mmio_read(12'h20, rd);
        check32(rd, 32'h00004000, "BIAS_ADDR intact");
        mmio_read(12'h24, rd);
        check32(rd, 32'h00005000, "SCALE_ADDR intact");

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
