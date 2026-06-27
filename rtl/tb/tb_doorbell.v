`timescale 1ns / 1ps
//=============================================================================
// tb_doorbell — Self-checking testbench for doorbell.v
//=============================================================================
// Tests:
//   1. Reset initialization — all regs 0, doorbell_irq=0
//   2. Write HOST_TAIL=42, read back → doorbell_irq=1
//   3. Write NPU_HEAD=42 → doorbell_irq=0
//   4. Write HOST_TAIL=99, doorbell_irq=1, write NPU_HEAD=99 → irq=0
//   5. Write/read all 4 registers independently, no cross-interference
//   6. APB handshake: pready=1 during access phase
//   7. psel=0 → prdata=0
//=============================================================================

module tb_doorbell;

    //-------------------------------------------------------------------------
    // DUT signals
    //-------------------------------------------------------------------------
    reg         clk;
    reg         rst_n;
    reg         psel;
    reg         penable;
    reg  [11:0] paddr;
    reg         pwrite;
    reg  [31:0] pwdata;
    wire [31:0] prdata;
    wire        pready;
    wire        pslverr;
    wire        doorbell_irq;

    //-------------------------------------------------------------------------
    // DUT instantiation
    //-------------------------------------------------------------------------
    doorbell u_dut (
        .clk          (clk),
        .rst_n        (rst_n),
        .psel         (psel),
        .penable      (penable),
        .paddr        (paddr),
        .pwrite       (pwrite),
        .pwdata       (pwdata),
        .prdata       (prdata),
        .pready       (pready),
        .pslverr      (pslverr),
        .doorbell_irq (doorbell_irq)
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
    // APB access tasks (AMBA APB v2.0)
    //-------------------------------------------------------------------------

    // APB write: setup phase (psel=1, penable=0) → access phase (penable=1)
    task automatic apb_write;
        input [11:0] a;
        input [31:0] d;
    begin
        // Setup phase
        @(posedge clk); #1;
        psel    = 1'b1;
        penable = 1'b0;
        pwrite  = 1'b1;
        paddr   = a;
        pwdata  = d;
        // Access phase
        @(posedge clk); #1;
        penable = 1'b1;
        // Capture happens on this posedge; deassert next
        @(posedge clk); #1;
        psel    = 1'b0;
        penable = 1'b0;
        pwrite  = 1'b0;
    end
    endtask

    // APB read: setup phase → access phase → sample prdata
    task automatic apb_read;
        input  [11:0] a;
        output [31:0] d;
    begin
        // Setup phase
        @(posedge clk); #1;
        psel    = 1'b1;
        penable = 1'b0;
        pwrite  = 1'b0;
        paddr   = a;
        // Access phase
        @(posedge clk); #1;
        penable = 1'b1;
        #1;
        d = prdata;
        @(posedge clk); #1;
        psel    = 1'b0;
        penable = 1'b0;
    end
    endtask

    //-------------------------------------------------------------------------
    // Shortcut: write then read back a register
    //-------------------------------------------------------------------------
    task automatic reg_write_read;
        input [11:0] a;
        input [31:0] wval;
        output [31:0] rval;
    begin
        apb_write(a, wval);
        apb_read(a, rval);
    end
    endtask

    //-------------------------------------------------------------------------
    // Register address aliases
    //-------------------------------------------------------------------------
    localparam ADDR_HOST_TAIL = 12'h00;
    localparam ADDR_NPU_HEAD  = 12'h04;
    localparam ADDR_HOST_HEAD = 12'h08;
    localparam ADDR_NPU_TAIL  = 12'h0C;

    //-------------------------------------------------------------------------
    // Test sequence
    //-------------------------------------------------------------------------
    reg [31:0] rd;

    initial begin
        total_tests  = 0;
        passed_tests = 0;
        failed_tests = 0;

        // Defaults
        psel    = 1'b0;
        penable = 1'b0;
        pwrite  = 1'b0;
        paddr   = 12'd0;
        pwdata  = 32'd0;

        // --- Power-on reset ---
        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;
        $display("=== DOORBELL Self-Checking Testbench ===");
        $display("");

        //===============================================================
        // Test 1: Reset state — all registers zero, doorbell_irq=0
        //===============================================================
        $display("--- Test 1: Post-reset defaults ---");

        // Read all registers
        apb_read(ADDR_HOST_TAIL, rd);
        check32(rd, 32'd0, "HOST_TAIL after reset");
        apb_read(ADDR_NPU_HEAD, rd);
        check32(rd, 32'd0, "NPU_HEAD after reset");
        apb_read(ADDR_HOST_HEAD, rd);
        check32(rd, 32'd0, "HOST_HEAD after reset");
        apb_read(ADDR_NPU_TAIL, rd);
        check32(rd, 32'd0, "NPU_TAIL after reset");

        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b0, "doorbell_irq=0 after reset (TAIL==HEAD==0)");

        //===============================================================
        // Test 2: Write HOST_TAIL=42 → doorbell_irq=1
        //===============================================================
        $display("--- Test 2: HOST_TAIL write triggers irq ---");

        apb_write(ADDR_HOST_TAIL, 32'd42);
        apb_read(ADDR_HOST_TAIL, rd);
        check32(rd, 32'd42, "HOST_TAIL readback after write(42)");

        // doorbell_irq should now be 1 (HOST_TAIL=42, NPU_HEAD=0)
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b1, "doorbell_irq=1 (HOST_TAIL=42 != NPU_HEAD=0)");

        // Read NPU_HEAD to verify it is still 0
        apb_read(ADDR_NPU_HEAD, rd);
        check32(rd, 32'd0, "NPU_HEAD still 0");

        //===============================================================
        // Test 3: Write NPU_HEAD=42 → doorbell_irq=0
        //           (simulates firmware consuming commands)
        //===============================================================
        $display("--- Test 3: NPU_HEAD write clears irq ---");

        apb_write(ADDR_NPU_HEAD, 32'd42);
        apb_read(ADDR_NPU_HEAD, rd);
        check32(rd, 32'd42, "NPU_HEAD readback after write(42)");

        // doorbell_irq should now be 0 (HOST_TAIL=42 == NPU_HEAD=42)
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b0, "doorbell_irq=0 (HOST_TAIL=42 == NPU_HEAD=42)");

        //===============================================================
        // Test 4: Second round-trip with different values
        //===============================================================
        $display("--- Test 4: Second round-trip (value=99) ---");

        // Reset to known state
        apb_write(ADDR_HOST_TAIL, 32'd0);
        apb_write(ADDR_NPU_HEAD, 32'd0);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b0, "doorbell_irq=0 after reset to 0/0");

        // Host enqueues commands, writes HOST_TAIL=99
        apb_write(ADDR_HOST_TAIL, 32'd99);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b1, "doorbell_irq=1 (HOST_TAIL=99 != NPU_HEAD=0)");

        // Firmware consumes commands, writes NPU_HEAD=99
        apb_write(ADDR_NPU_HEAD, 32'd99);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b0, "doorbell_irq=0 (HOST_TAIL=99 == NPU_HEAD=99)");

        //===============================================================
        // Test 5: Asymmetric values — HOST_TAIL=100, NPU_HEAD=50
        //===============================================================
        $display("--- Test 5: Asymmetric tails (100 vs 50) ---");

        apb_write(ADDR_NPU_HEAD, 32'd50);
        apb_write(ADDR_HOST_TAIL, 32'd100);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b1, "doorbell_irq=1 (HOST_TAIL=100 != NPU_HEAD=50)");

        // Partial consumption: NPU_HEAD=75
        apb_write(ADDR_NPU_HEAD, 32'd75);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b1, "doorbell_irq=1 (HOST_TAIL=100 != NPU_HEAD=75)");

        // Full consumption: NPU_HEAD=100
        apb_write(ADDR_NPU_HEAD, 32'd100);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b0, "doorbell_irq=0 (HOST_TAIL=100 == NPU_HEAD=100)");

        //===============================================================
        // Test 6: Write/read all 4 registers, no cross-interference
        //===============================================================
        $display("--- Test 6: All 4 registers independent ---");

        apb_write(ADDR_HOST_TAIL, 32'hAAAAAAAA);
        apb_write(ADDR_NPU_HEAD,  32'hBBBBBBBB);
        apb_write(ADDR_HOST_HEAD, 32'hCCCCCCCC);
        apb_write(ADDR_NPU_TAIL,  32'hDDDDDDDD);

        apb_read(ADDR_HOST_TAIL, rd);
        check32(rd, 32'hAAAAAAAA, "HOST_TAIL=0xAAAAAAAA intact");
        apb_read(ADDR_NPU_HEAD, rd);
        check32(rd, 32'hBBBBBBBB, "NPU_HEAD=0xBBBBBBBB intact");
        apb_read(ADDR_HOST_HEAD, rd);
        check32(rd, 32'hCCCCCCCC, "HOST_HEAD=0xCCCCCCCC intact");
        apb_read(ADDR_NPU_TAIL, rd);
        check32(rd, 32'hDDDDDDDD, "NPU_TAIL=0xDDDDDDDD intact");

        // Read back in reverse order to further verify no interference
        apb_read(ADDR_NPU_TAIL, rd);
        check32(rd, 32'hDDDDDDDD, "NPU_TAIL (re-read) intact");
        apb_read(ADDR_NPU_HEAD, rd);
        check32(rd, 32'hBBBBBBBB, "NPU_HEAD (re-read) intact");
        apb_read(ADDR_HOST_HEAD, rd);
        check32(rd, 32'hCCCCCCCC, "HOST_HEAD (re-read) intact");
        apb_read(ADDR_HOST_TAIL, rd);
        check32(rd, 32'hAAAAAAAA, "HOST_TAIL (re-read) intact");

        // Reset to clean state
        apb_write(ADDR_HOST_TAIL, 32'd0);
        apb_write(ADDR_NPU_HEAD,  32'd0);
        apb_write(ADDR_HOST_HEAD, 32'd0);
        apb_write(ADDR_NPU_TAIL,  32'd0);

        //===============================================================
        // Test 7: APB handshake — pready during access phase
        //===============================================================
        $display("--- Test 7: APB handshake ---");

        // pready should be 0 when no transfer active
        @(posedge clk); #1;
        check_bit(pready, 1'b0, "pready=0 when no transfer");

        // Setup phase: psel=1, penable=0 → pready=0
        @(posedge clk); #1;
        psel    = 1'b1;
        penable = 1'b0;
        pwrite  = 1'b1;
        paddr   = ADDR_HOST_TAIL;
        pwdata  = 32'd7;
        #1;
        check_bit(pready, 1'b0, "pready=0 during setup phase");

        // Access phase: psel=1, penable=1 → pready=1
        @(posedge clk); #1;
        penable = 1'b1;
        #1;
        check_bit(pready, 1'b1, "pready=1 during access phase");

        // Clean up
        @(posedge clk); #1;
        psel    = 1'b0;
        penable = 1'b0;
        pwrite  = 1'b0;

        // pslverr is always 0 for doorbell
        @(posedge clk); #1;
        check_bit(pslverr, 1'b0, "pslverr always 0");

        //===============================================================
        // Test 8: psel=0 → prdata=0
        //===============================================================
        $display("--- Test 8: prdata=0 when psel=0 ---");

        // Write a known value first
        apb_write(ADDR_HOST_TAIL, 32'hDEADBEEF);
        apb_read(ADDR_HOST_TAIL, rd);
        check32(rd, 32'hDEADBEEF, "HOST_TAIL=0xDEADBEEF when accessed");

        // Now check prdata when psel=0 (should be 0)
        @(posedge clk); #1;
        psel    = 1'b0;
        penable = 1'b0;
        paddr   = ADDR_HOST_TAIL;
        pwrite  = 1'b0;
        #1;
        check32(prdata, 32'd0, "prdata=0 when psel=0");

        //===============================================================
        // Test 9: Firmware main loop simulation
        //           (matches npu_firmware.c:294-324 polling behavior)
        //===============================================================
        $display("--- Test 9: Firmware main loop simulation ---");

        // Initialize: NPU_HEAD = 0 (line 296)
        apb_write(ADDR_NPU_HEAD, 32'd0);
        apb_write(ADDR_HOST_TAIL, 32'd0);

        // Simulate host writing 3 commands (HOST_TAIL=3)
        apb_write(ADDR_HOST_TAIL, 32'd3);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b1, "irq=1 after host enqueues 3 cmds");

        // Firmware polls: reads HOST_TAIL and NPU_HEAD
        apb_read(ADDR_HOST_TAIL, rd);
        check32(rd, 32'd3, "fw reads HOST_TAIL=3");
        apb_read(ADDR_NPU_HEAD, rd);
        check32(rd, 32'd0, "fw reads NPU_HEAD=0");

        // Firmware consumes commands one at a time
        // After cmd 0:
        apb_write(ADDR_NPU_HEAD, 32'd1);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b1, "irq=1 after consuming cmd 0 (NPU_HEAD=1)");

        // After cmd 1:
        apb_write(ADDR_NPU_HEAD, 32'd2);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b1, "irq=1 after consuming cmd 1 (NPU_HEAD=2)");

        // After cmd 2 (all done):
        apb_write(ADDR_NPU_HEAD, 32'd3);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b0, "irq=0 after consuming all cmds (NPU_HEAD=3)");

        //===============================================================
        // Test 10: Overflow values (32-bit wrap-around)
        //===============================================================
        $display("--- Test 10: 32-bit values ---");

        apb_write(ADDR_HOST_TAIL, 32'hFFFFFFFF);
        apb_write(ADDR_NPU_HEAD,  32'd0);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b1, "irq=1 with HOST_TAIL=0xFFFFFFFF, NPU_HEAD=0");

        apb_write(ADDR_NPU_HEAD, 32'hFFFFFFFF);
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b0, "irq=0 with both 0xFFFFFFFF");

        //===============================================================
        // Test 11: irq is combinational (no pipeline delay)
        //===============================================================
        $display("--- Test 11: Combinational irq ---");

        apb_write(ADDR_HOST_TAIL, 32'd1);
        apb_write(ADDR_NPU_HEAD, 32'd0);

        // After write, irq should be 1 immediately (next cycle due to register)
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b1, "combinational irq=1");

        apb_write(ADDR_NPU_HEAD, 32'd1);

        // After write, irq should clear immediately
        @(posedge clk); #1;
        check_bit(doorbell_irq, 1'b0, "combinational irq=0 after match");

        //===============================================================
        // Test 12: Read/write unknown address returns 0, no side effects
        //===============================================================
        $display("--- Test 12: Unknown addresses ---");

        // Read undefined offset 0x10
        apb_read(12'h10, rd);
        check32(rd, 32'd0, "read undefined 0x10 returns 0");

        // Write to undefined offset should not affect any register
        apb_write(ADDR_HOST_TAIL, 32'd42);
        apb_write(12'h10, 32'hDEADBEEF);  // write to undefined
        apb_read(ADDR_HOST_TAIL, rd);
        check32(rd, 32'd42, "HOST_TAIL unchanged after write to undefined addr");

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
