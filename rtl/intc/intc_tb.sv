//=============================================================================
// tb_intc — Self-Checking Interrupt Controller Testbench
// CaduceusCore SoC Phase 3-4 / Task 6
//
// Tests:
//   TC1: APB write ENABLE=0x7F → readback = 0x7F
//   TC2: APB write THRESHOLD=2 → readback = 2
//   TC3: mxu_irq=1, ENABLE=0x01 → cpu_irq=1, PENDING[0]=1
//   TC4: ACK=0x01 (source low) → PENDING[0]=0, cpu_irq=0
//   TC5: ENABLE=0x00 → cpu_irq=0 (mask blocks irq)
//   TC6: THRESHOLD gate — threshold=2, only 1 src → cpu_irq=0
//   TC7: Multiple irq sources (mxu+sfu+vector+dma) → popcount=4 ≥ threshold=3 → cpu_irq=1
//
// Usage:
//   vcs -full64 -sverilog -timescale=1ns/1ps -top tb_intc \
//       CaduceusCore/rtl/tb/tb_intc.v CaduceusCore/rtl/intc/intc_top.v \
//       -o simv_tb_intc
//   ./simv_tb_intc
//=============================================================================

`timescale 1ns / 1ps

module tb_intc;

    // =========================================================================
    // Parameters
    // =========================================================================
    localparam CLK_PERIOD   = 1;          // 1 GHz clock (1 ns period)
    localparam RESET_CYCLES = 5;

    // =========================================================================
    // DUT Signals
    // =========================================================================
    reg         clk;
    reg         rst_n;

    reg         mxu_irq;
    reg         sfu_irq;
    reg         vector_irq;
    reg         dma_irq;
    reg         pcie_irq;
    reg         host_irq;
    reg         timer_irq;

    reg         psel;
    reg         penable;
    reg         pwrite;
    reg  [11:0] paddr;
    reg  [31:0] pwdata;
    wire [31:0] prdata;
    wire        pready;
    wire        pslverr;

    wire        cpu_irq;

    // =========================================================================
    // DUT Instantiation
    // =========================================================================
    intc_top u_dut (
        .clk         (clk),
        .rst_n       (rst_n),
        .mxu_irq     (mxu_irq),
        .sfu_irq     (sfu_irq),
        .vector_irq  (vector_irq),
        .dma_irq     (dma_irq),
        .pcie_irq    (pcie_irq),
        .host_irq    (host_irq),
        .timer_irq   (timer_irq),
        .psel        (psel),
        .penable     (penable),
        .pwrite      (pwrite),
        .paddr       (paddr),
        .pwdata      (pwdata),
        .prdata      (prdata),
        .pready      (pready),
        .pslverr     (pslverr),
        .cpu_irq     (cpu_irq)
    );

    // =========================================================================
    // Clock
    // =========================================================================
    initial begin
        clk = 1'b0;
        forever #(CLK_PERIOD / 2.0) clk = ~clk;
    end

    // =========================================================================
    // Test orchestration
    // =========================================================================
    integer pass_cnt, fail_cnt;

    initial begin
        pass_cnt = 0;
        fail_cnt = 0;

        // Init all registers
        rst_n    = 1'b0;
        mxu_irq  = 1'b0;
        sfu_irq  = 1'b0;
        vector_irq = 1'b0;
        dma_irq  = 1'b0;
        pcie_irq = 1'b0;
        host_irq = 1'b0;
        timer_irq = 1'b0;
        psel     = 1'b0;
        penable  = 1'b0;
        pwrite   = 1'b0;
        paddr    = 12'h0;
        pwdata   = 32'h0;

        // Hold reset
        repeat (RESET_CYCLES) @(negedge clk);
        rst_n = 1'b1;
        repeat (2) @(negedge clk);

        $display("========================================");
        $display("  INTC TESTBENCH START");
        $display("========================================");

        // Run tests
        tc1_reg_enable_rw();
        tc2_reg_threshold_rw();
        tc3_mxu_irq_pending_cpu_irq();
        tc4_ack_clear_pending();
        tc5_enable_mask_blocks_irq();
        tc6_threshold_gate();
        tc7_multi_source_popcount();

        // Summary
        $display("\n========================================");
        $display("  INTC TESTBENCH SUMMARY");
        $display("  PASS: %0d / %0d", pass_cnt, pass_cnt + fail_cnt);
        $display("  FAIL: %0d", fail_cnt);
        $display("========================================");
        if (fail_cnt == 0)
            $display("[INTC_TEST] PASS");
        else
            $display("[INTC_TEST] FAIL");
        $finish;
    end

    // =========================================================================
    // APB helper tasks
    // =========================================================================

    task apb_write;
        input [11:0] addr;
        input [31:0] data;
        begin
            @(negedge clk);
            psel    = 1'b1;
            penable = 1'b0;
            pwrite  = 1'b1;
            paddr   = addr;
            pwdata  = data;
            @(negedge clk);
            penable = 1'b1;
            @(negedge clk);
            psel    = 1'b0;
            penable = 1'b0;
            pwrite  = 1'b0;
        end
    endtask

    task apb_read;
        input  [11:0] addr;
        output [31:0] data;
        reg    [31:0] tmp;
        begin
            @(negedge clk);
            psel    = 1'b1;
            penable = 1'b0;
            pwrite  = 1'b0;
            paddr   = addr;
            @(negedge clk);
            penable = 1'b1;
            @(negedge clk);
            tmp     = prdata;
            psel    = 1'b0;
            penable = 1'b0;
            data    = tmp;
        end
    endtask

    task wait_clks;
        input integer n;
        integer i;
        begin
            for (i = 0; i < n; i = i + 1)
                @(negedge clk);
        end
    endtask

    // =========================================================================
    // Test Cases
    // =========================================================================

    // TC1: Write ENABLE → readback matches
    task tc1_reg_enable_rw;
        reg [31:0] rd;
        begin
            $display("\n-- TC1: ENABLE RW --");
            apb_write(12'h004, 32'h0000_007F);
            apb_read (12'h004, rd);
            if (rd[6:0] === 7'h7F) begin
                $display("  [PASS] ENABLE readback = 0x%08x", rd);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] ENABLE readback = 0x%08x (expected 0x0000007F)", rd);
                fail_cnt = fail_cnt + 1;
            end
        end
    endtask

    // TC2: Write THRESHOLD → readback matches
    task tc2_reg_threshold_rw;
        reg [31:0] rd;
        begin
            $display("\n-- TC2: THRESHOLD RW --");
            apb_write(12'h008, 32'h0000_0002);
            apb_read (12'h008, rd);
            if (rd[2:0] === 3'd2) begin
                $display("  [PASS] THRESHOLD readback = 0x%08x", rd);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] THRESHOLD readback = 0x%08x (expected 0x00000002)", rd);
                fail_cnt = fail_cnt + 1;
            end
        end
    endtask

    // TC3: mxu_irq=1, ENABLE=0x01 → cpu_irq=1, PENDING[0]=1
    task tc3_mxu_irq_pending_cpu_irq;
        reg [31:0] rd;
        begin
            $display("\n-- TC3: mxu_irq -> PENDING + cpu_irq --");
            mxu_irq = 1'b0;
            wait_clks(2);
            apb_write(12'h004, 32'h0000_0001);
            apb_write(12'h008, 32'h0000_0001);

            mxu_irq = 1'b1;
            wait_clks(2);

            apb_read(12'h000, rd);
            if (rd[0] === 1'b1) begin
                $display("  [PASS] PENDING[0] = 1, PENDING=0x%08x", rd);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] PENDING[0] = %0b, PENDING=0x%08x", rd[0], rd);
                fail_cnt = fail_cnt + 1;
            end

            if (cpu_irq === 1'b1) begin
                $display("  [PASS] cpu_irq = 1, cpu_irq=%0b", cpu_irq);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] cpu_irq = %0b (expected 1)", cpu_irq);
                fail_cnt = fail_cnt + 1;
            end
        end
    endtask

    // TC4: ACK=0x01 (source low) → PENDING[0]=0, cpu_irq=0
    task tc4_ack_clear_pending;
        reg [31:0] rd;
        begin
            $display("\n-- TC4: ACK clear PENDING --");
            mxu_irq = 1'b0;
            wait_clks(2);
            apb_write(12'h00C, 32'h0000_0001);
            wait_clks(2);

            apb_read(12'h000, rd);
            if (rd[0] === 1'b0) begin
                $display("  [PASS] PENDING[0] = 0 after ACK, PENDING=0x%08x", rd);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] PENDING[0] = %0b after ACK, PENDING=0x%08x", rd[0], rd);
                fail_cnt = fail_cnt + 1;
            end

            if (cpu_irq === 1'b0) begin
                $display("  [PASS] cpu_irq = 0 after ACK, cpu_irq=%0b", cpu_irq);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] cpu_irq = %0b after ACK (expected 0)", cpu_irq);
                fail_cnt = fail_cnt + 1;
            end
        end
    endtask

    // TC5: ENABLE=0 → cpu_irq=0 even with source high
    task tc5_enable_mask_blocks_irq;
        reg [31:0] rd;
        begin
            $display("\n-- TC5: ENABLE=0 blocks cpu_irq --");
            apb_write(12'h004, 32'h0000_0000);
            apb_write(12'h008, 32'h0000_0001);
            mxu_irq = 1'b0;
            wait_clks(2);
            apb_write(12'h00C, 32'h0000_007F);
            wait_clks(2);

            mxu_irq = 1'b1;
            wait_clks(2);

            apb_read(12'h000, rd);
            if (rd[0] === 1'b1) begin
                $display("  [PASS] PENDING[0]=1 (source active), PENDING=0x%08x", rd);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] PENDING[0]=%0b, PENDING=0x%08x", rd[0], rd);
                fail_cnt = fail_cnt + 1;
            end

            if (cpu_irq === 1'b0) begin
                $display("  [PASS] cpu_irq=0 (ENABLE=0 masks), cpu_irq=%0b", cpu_irq);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] cpu_irq=%0b (expected 0, ENABLE=0)", cpu_irq);
                fail_cnt = fail_cnt + 1;
            end

            // Cleanup
            mxu_irq = 1'b0;
            apb_write(12'h00C, 32'h0000_007F);
            wait_clks(2);
        end
    endtask

    // TC6: THRESHOLD gate
    task tc6_threshold_gate;
        begin
            $display("\n-- TC6: THRESHOLD gate (threshold=2, 1 src) --");
            mxu_irq = 1'b0;
            sfu_irq = 1'b0;
            apb_write(12'h00C, 32'h0000_007F);
            wait_clks(2);
            apb_write(12'h004, 32'h0000_007F);
            apb_write(12'h008, 32'h0000_0002);

            mxu_irq = 1'b1;
            wait_clks(2);
            if (cpu_irq === 1'b0) begin
                $display("  [PASS] cpu_irq=0 (1<2, below threshold), cpu_irq=%0b", cpu_irq);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] cpu_irq=%0b (expected 0, popcount=1 < threshold=2)", cpu_irq);
                fail_cnt = fail_cnt + 1;
            end

            sfu_irq = 1'b1;
            wait_clks(2);
            if (cpu_irq === 1'b1) begin
                $display("  [PASS] cpu_irq=1 (2>=2, meets threshold), cpu_irq=%0b", cpu_irq);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] cpu_irq=%0b (expected 1, popcount=2 >= threshold=2)", cpu_irq);
                fail_cnt = fail_cnt + 1;
            end

            // Cleanup
            mxu_irq = 1'b0;
            sfu_irq = 1'b0;
            apb_write(12'h00C, 32'h0000_007F);
            wait_clks(2);
        end
    endtask

    // TC7: Multiple irq sources
    task tc7_multi_source_popcount;
        reg [31:0] rd;
        begin
            $display("\n-- TC7: multi-source (4 irqs, threshold=3) --");
            apb_write(12'h004, 32'h0000_007F);
            apb_write(12'h008, 32'h0000_0003);
            mxu_irq = 1'b0; sfu_irq = 1'b0; vector_irq = 1'b0;
            dma_irq = 1'b0; pcie_irq = 1'b0;
            apb_write(12'h00C, 32'h0000_007F);
            wait_clks(2);

            mxu_irq    = 1'b1;
            sfu_irq    = 1'b1;
            vector_irq = 1'b1;
            dma_irq    = 1'b1;
            wait_clks(2);

            apb_read(12'h000, rd);
            if (rd[3:0] === 4'b1111) begin
                $display("  [PASS] PENDING[3:0] = 4'b1111, PENDING=0x%08x", rd);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] PENDING[3:0] = 4'b%04b, PENDING=0x%08x", rd[3:0], rd);
                fail_cnt = fail_cnt + 1;
            end

            if (cpu_irq === 1'b1) begin
                $display("  [PASS] cpu_irq=1 (4>=3), cpu_irq=%0b", cpu_irq);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] cpu_irq=%0b (expected 1, popcount=4 >= threshold=3)", cpu_irq);
                fail_cnt = fail_cnt + 1;
            end

            // Add pcie_irq
            pcie_irq = 1'b1;
            wait_clks(2);
            apb_read(12'h000, rd);
            if (rd[4:0] === 5'b11111) begin
                $display("  [PASS] PENDING[4:0] = 5'b11111, PENDING=0x%08x", rd);
                pass_cnt = pass_cnt + 1;
            end else begin
                $display("  [FAIL] PENDING[4:0] = 5'b%05b, PENDING=0x%08x", rd[4:0], rd);
                fail_cnt = fail_cnt + 1;
            end

            // Cleanup
            mxu_irq = 1'b0; sfu_irq = 1'b0; vector_irq = 1'b0;
            dma_irq = 1'b0; pcie_irq = 1'b0;
            apb_write(12'h00C, 32'h0000_007F);
            wait_clks(2);
        end
    endtask

endmodule
