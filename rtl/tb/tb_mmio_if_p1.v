`timescale 1ns / 1ps
//=============================================================================
// tb_mmio_if_p1 — Enhanced testbench for MXU P1 mmio_if cases (MX-06..08)
//=============================================================================
// MX-06: Reserved register access — read=0, write no-op
// MX-07: CMD register — read=0 (write-only), START/ABORT single-cycle pulse
// MX-08: Unaligned address access — addr & 3 != 0
//=============================================================================

module tb_mmio_if_p1;

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

    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    integer total_tests;
    integer passed_tests;
    integer failed_tests;

    task automatic check32;
        input [31:0] actual;
        input [31:0] expected;
        input [255:0] desc;
    begin
        total_tests = total_tests + 1;
        if (actual !== expected) begin
            $display("FAIL [%0s]: expected=0x%08h got=0x%08h", desc, expected, actual);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [%0s]: value=0x%08h", desc, expected);
            passed_tests = passed_tests + 1;
        end
    end
    endtask

    task automatic check_bit;
        input        actual;
        input        expected;
        input [255:0] desc;
    begin
        total_tests = total_tests + 1;
        if (actual !== expected) begin
            $display("FAIL [%0s]: expected=%0b got=%0b", desc, expected, actual);
            failed_tests = failed_tests + 1;
        end else begin
            $display("PASS [%0s]: value=%0b", desc, expected);
            passed_tests = passed_tests + 1;
        end
    end
    endtask

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

    reg [31:0] rd;

    initial begin
        total_tests  = 0;
        passed_tests = 0;
        failed_tests = 0;

        cs           = 1'b0;
        we           = 1'b0;
        addr         = 12'd0;
        wdata        = 32'd0;
        status_busy  = 1'b0;
        status_done  = 1'b0;
        status_error = 1'b0;

        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;
        $display("=== MXU P1 mmio_if Testbench (MX-06..08) ===");

        //=================================================================
        // MX-06: Reserved register access
        //   Registers defined at 0x00..0x28. Addresses >= 0x2C are reserved.
        //   Read reserved → 0; write reserved → no-op on known registers.
        //=================================================================
        $display("");
        $display("--- MX-06: Reserved Register Access ---");

        // Setup known register values
        mmio_write(12'h00, 32'hA5A5A5A5);  // CTRL
        mmio_write(12'h0C, {16'd128, 16'd256});  // DIM0: M=128, K=256
        mmio_write(12'h10, {16'd0, 16'd64});     // DIM1: N=64

        // Read reserved addresses — must return 0
        mmio_read(12'h2C, rd);
        check32(rd, 32'd0, "MX-06 reserved 0x2C read=0");
        mmio_read(12'h30, rd);
        check32(rd, 32'd0, "MX-06 reserved 0x30 read=0");
        mmio_read(12'h34, rd);
        check32(rd, 32'd0, "MX-06 reserved 0x34 read=0");
        mmio_read(12'hFF, rd);
        check32(rd, 32'd0, "MX-06 reserved 0xFF read=0");
        mmio_read(12'h100, rd);
        check32(rd, 32'd0, "MX-06 reserved 0x100 read=0");

        // Write to reserved addresses — must not affect known registers
        mmio_write(12'h2C, 32'hFFFFFFFF);
        mmio_write(12'h30, 32'hDEADBEEF);
        mmio_write(12'hFF, 32'hCAFEBABE);

        // Verify known registers unchanged
        mmio_read(12'h00, rd);
        check32(rd, 32'hA5A5A5A5, "MX-06 CTRL unchanged after reserved write");
        mmio_read(12'h0C, rd);
        check32(rd, {16'd128, 16'd256}, "MX-06 DIM0 unchanged after reserved write");
        mmio_read(12'h10, rd);
        check32(rd, {16'd0, 16'd64}, "MX-06 DIM1 unchanged after reserved write");

        //=================================================================
        // MX-07: CMD register behavior
        //   CMD read returns 0 (write-only). START/ABORT are single-cycle pulses.
        //=================================================================
        $display("");
        $display("--- MX-07: CMD Register ---");

        // CMD read returns 0
        mmio_read(12'h04, rd);
        check32(rd, 32'd0, "MX-07 CMD read returns 0 (write-only)");

        // Verify START single-cycle pulse
        check_bit(cmd_start, 1'b0, "MX-07 cmd_start=0 before write");
        @(posedge clk); #1;
        cs    = 1'b1; we = 1'b1; addr = 12'h04; wdata = 32'd1;  // START=1
        @(posedge clk); #1;
        cs    = 1'b0; we = 1'b0;
        check_bit(cmd_start, 1'b1, "MX-07 cmd_start pulsed (cycle 1)");
        // Verify pulse is exactly one cycle
        @(posedge clk); #1;
        check_bit(cmd_start, 1'b0, "MX-07 cmd_start cleared (single-cycle)");

        // Verify ABORT single-cycle pulse
        check_bit(cmd_abort, 1'b0, "MX-07 cmd_abort=0 before write");
        @(posedge clk); #1;
        cs    = 1'b1; we = 1'b1; addr = 12'h04; wdata = 32'd2;  // ABORT=1
        @(posedge clk); #1;
        cs    = 1'b0; we = 1'b0;
        check_bit(cmd_abort, 1'b1, "MX-07 cmd_abort pulsed (cycle 1)");
        @(posedge clk); #1;
        check_bit(cmd_abort, 1'b0, "MX-07 cmd_abort cleared (single-cycle)");

        // Both bits: both pulses
        @(posedge clk); #1;
        cs    = 1'b1; we = 1'b1; addr = 12'h04; wdata = 32'd3;  // START+ABORT
        @(posedge clk); #1;
        cs    = 1'b0; we = 1'b0;
        check_bit(cmd_start, 1'b1, "MX-07 both bits: cmd_start=1");
        check_bit(cmd_abort, 1'b1, "MX-07 both bits: cmd_abort=1");
        @(posedge clk); #1;
        check_bit(cmd_start, 1'b0, "MX-07 both bits: cmd_start cleared");
        check_bit(cmd_abort, 1'b0, "MX-07 both bits: cmd_abort cleared");

        // Bit not set → no pulse
        @(posedge clk); #1;
        cs    = 1'b1; we = 1'b1; addr = 12'h04; wdata = 32'd0;
        @(posedge clk); #1;
        cs    = 1'b0; we = 1'b0;
        check_bit(cmd_start, 1'b0, "MX-07 START=0: cmd_start=0");
        check_bit(cmd_abort, 1'b0, "MX-07 ABORT=0: cmd_abort=0");

        //=================================================================
        // MX-08: Unaligned address access
        //   Byte addresses not multiple of 4 (addr[1:0] != 0).
        //   Since case() uses exact address matches, unaligned accesses
        //   should fall through to default: rdata=0, write ignored.
        //   No X-propagation allowed.
        //=================================================================
        $display("");
        $display("--- MX-08: Unaligned Address Access ---");

        // Set up known register
        mmio_write(12'h00, 32'h12345678);
        mmio_read(12'h00, rd);
        check32(rd, 32'h12345678, "MX-08 CTRL=0x12345678 baseline");

        // Read unaligned 0x01 — should return 0 (falls default, not partial read)
        mmio_read(12'h01, rd);
        check32(rd, 32'd0, "MX-08 unaligned addr 0x01 read=0");
        check32(rdata, 32'd0, "MX-08 unaligned 0x01: no X on rdata");

        // Read unaligned 0x02
        mmio_read(12'h02, rd);
        check32(rd, 32'd0, "MX-08 unaligned addr 0x02 read=0");

        // Read unaligned 0x03
        mmio_read(12'h03, rd);
        check32(rd, 32'd0, "MX-08 unaligned addr 0x03 read=0");

        // Read unaligned 0x05 (near CMD register 0x04)
        mmio_read(12'h05, rd);
        check32(rd, 32'd0, "MX-08 unaligned addr 0x05 read=0");

        // Write to unaligned address — must be no-op
        mmio_write(12'h01, 32'hFFFFFFFF);
        mmio_write(12'h02, 32'hDEADBEEF);
        mmio_write(12'h03, 32'hCAFEBABE);

        // Verify CTRL unchanged after unaligned writes
        mmio_read(12'h00, rd);
        check32(rd, 32'h12345678, "MX-08 CTRL unchanged after unaligned writes");

        // Verify no X propagation on any output
        check_bit(^rdata === 1'bx, 1'b0, "MX-08 rdata no X (^rdata known)");
        check_bit(ready, 1'b0, "MX-08 ready=0 when cs=0");

        // Aligned reads still work after unaligned accesses
        mmio_read(12'h0C, rd);
        check32(rd, {16'd128, 16'd256}, "MX-08 aligned read DIM0 intact");

        //=================================================================
        // Summary
        //=================================================================
        $display("");
        $display("=== MXU P1 mmio_if Summary ===");
        $display("Total:  %0d", total_tests);
        $display("Passed: %0d", passed_tests);
        $display("Failed: %0d", failed_tests);

        if (failed_tests == 0)
            $display("RESULT: ALL MXU P1 mmio_if TESTS PASSED");
        else
            $display("RESULT: %0d TESTS FAILED", failed_tests);

        #20;
        $finish(2);   // Exit immediately, no interactive prompt
    end

endmodule
