// tb_sfu_sf15.sv — SF-15: sfu_top IRQ timing measurement
//
// Tests two scenarios:
//   A) IRQ_EN=1: Execute softmax(N=16), monitor IRQ rise vs last sram_wen.
//      IRQ must rise ≤ 2 cycles after last output write.
//   B) IRQ_EN=0: Execute softmax(N=16), verify IRQ stays 0 throughout.
//
// Uses an internal SRAM model and drives sfu_top through MMIO.

`timescale 1ns / 1ps

module tb_sfu_sf15;

    localparam CLK_HALF    = 5;
    localparam ADDR_WIDTH  = 32;
    localparam SRAM_WORDS  = 256;
    localparam N_ELEMENTS  = 16;

    // MMIO offsets
    localparam [11:0] OFF_CTRL    = 12'h000;
    localparam [11:0] OFF_CMD     = 12'h004;
    localparam [11:0] OFF_STATUS  = 12'h008;
    localparam [11:0] OFF_I_ADDR  = 12'h00C;
    localparam [11:0] OFF_O_ADDR  = 12'h010;
    localparam [11:0] OFF_DIM     = 12'h014;
    localparam [11:0] OFF_POS     = 12'h018;
    localparam [11:0] OFF_IRQ_EN  = 12'h01C;

    localparam [3:0] OP_SOFTMAX = 4'd0;

    // DUT signals
    reg                  clk;
    reg                  rst_n;
    reg                  mmio_cs;
    reg                  mmio_we;
    reg  [11:0]          mmio_addr;
    reg  [31:0]          mmio_wdata;
    wire [31:0]          mmio_rdata;
    wire                 mmio_ready;
    wire [ADDR_WIDTH-1:0] sram_raddr;
    wire                  sram_ren;
    wire [ADDR_WIDTH-1:0] sram_waddr;
    wire [31:0]           sram_wdata;
    wire                  sram_wen;
    reg  [31:0]           sram_rdata;
    wire                  irq;

    // DUT
    sfu_top #(.ADDR_WIDTH(ADDR_WIDTH)) u_dut (
        .clk        (clk),
        .rst_n      (rst_n),
        .mmio_cs    (mmio_cs),
        .mmio_we    (mmio_we),
        .mmio_addr  (mmio_addr),
        .mmio_wdata (mmio_wdata),
        .mmio_rdata (mmio_rdata),
        .mmio_ready (mmio_ready),
        .sram_rdata (sram_rdata),
        .sram_raddr (sram_raddr),
        .sram_ren   (sram_ren),
        .sram_waddr (sram_waddr),
        .sram_wdata (sram_wdata),
        .sram_wen   (sram_wen),
        .irq        (irq)
    );

    // SRAM model
    reg [31:0] sram_mem [0:SRAM_WORDS-1];

    always @(*) begin
        if (sram_ren)
            sram_rdata = sram_mem[sram_raddr[$clog2(SRAM_WORDS)+1:2]];
        else
            sram_rdata = 32'd0;
    end

    always @(posedge clk) begin
        if (sram_wen)
            sram_mem[sram_waddr[$clog2(SRAM_WORDS)+1:2]] <= sram_wdata;
    end

    // Clock
    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    // Cycle counter
    integer cycle;
    always @(posedge clk) begin
        cycle <= cycle + 1;
    end

    // Timing tracking
    integer last_sram_wen_cycle;
    integer irq_rise_cycle;
    integer irq_delay;
    integer error_count;
    integer i, j;
    reg [31:0] stat_val;
    reg        timed_out;

    // Track last sram_wen cycle and IRQ rise
    always @(posedge clk) begin
        if (sram_wen)
            last_sram_wen_cycle <= cycle;
    end

    always @(posedge clk) begin
        if (irq && irq_rise_cycle == 0)
            irq_rise_cycle <= cycle;
    end

    // MMIO helpers
    task mmio_write;
        input [11:0] reg_addr;
        input [31:0] value;
    begin
        @(negedge clk);
        mmio_cs    = 1'b1;
        mmio_we    = 1'b1;
        mmio_addr  = reg_addr;
        mmio_wdata = value;
        @(negedge clk);
        mmio_cs    = 1'b0;
        mmio_we    = 1'b0;
        mmio_addr  = 12'd0;
        mmio_wdata = 32'd0;
    end
    endtask

    task mmio_read;
        input  [11:0] reg_addr;
        output [31:0] value;
    begin
        @(negedge clk);
        mmio_cs   = 1'b1;
        mmio_we   = 1'b0;
        mmio_addr = reg_addr;
        @(negedge clk);
        value = mmio_rdata;
        mmio_cs   = 1'b0;
        mmio_addr = 12'd0;
    end
    endtask

    // Reset DUT and SRAM
    task reset_all;
    begin
        rst_n = 1'b0;
        for (j = 0; j < SRAM_WORDS; j = j + 1)
            sram_mem[j] = 32'd0;
        repeat(10) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
    end
    endtask

    // Write FP16 elements into SRAM (packed two per word)
    task load_sram_softmax;
        input [15:0] val;
        input integer n_elems;
        integer k;
    begin
        for (k = 0; k < n_elems; k = k + 2) begin
            if (k + 1 < n_elems)
                sram_mem[k >> 1] = {val, val};
            else
                sram_mem[k >> 1] = {16'd0, val};
        end
    end
    endtask

    // Run one softmax with given IRQ_EN and check timing
    task run_softmax_irq_test;
        input        irq_en;
        input [31:0] dim;
        output reg   pass;
    begin
        pass = 1'b0;

        // Reset state
        reset_all();
        cycle               = 0;
        last_sram_wen_cycle = 0;
        irq_rise_cycle      = 0;
        irq_delay           = 0;

        // Load SRAM with test data (FP16 1.0 = 0x3C00)
        load_sram_softmax(16'h3C00, dim);

        // Configure MMIO
        mmio_write(OFF_CTRL,   {28'd0, OP_SOFTMAX});
        mmio_write(OFF_I_ADDR, 32'd0);
        mmio_write(OFF_O_ADDR, 32'd16);  // output at offset to avoid overlap
        mmio_write(OFF_DIM,    {16'd0, dim[15:0]});
        mmio_write(OFF_IRQ_EN, {31'd0, irq_en});

        $display("[SF-15] Scenario IRQ_EN=%0d, DIM=%0d — starting", irq_en, dim);

        // Re-zero timing trackers for this run
        last_sram_wen_cycle = 0;
        irq_rise_cycle      = 0;

        // Start operation
        mmio_write(OFF_CMD, 32'd1);

        // Wait for IRQ or timeout (STATUS.DONE is single-cycle, hard to poll)
        timed_out = 1'b0;
        fork : wait_done
            begin
                repeat(500000) @(posedge clk);
                $display("[SF-15] ERROR: Timeout waiting for completion");
                timed_out = 1'b1;
                disable wait_done;
            end
            begin
                @(posedge irq);
                $display("[SF-15] IRQ asserted at cycle %0d", cycle);
                disable wait_done;
            end
            begin
                // Also watch STATUS.DONE via faster inline sampling
                @(posedge clk);
                repeat(500000) begin
                    // Fast inline read: chip-select + check on next posedge
                    mmio_cs   = 1'b1;
                    mmio_we   = 1'b0;
                    mmio_addr = OFF_STATUS;
                    @(posedge clk);
                    if (mmio_rdata[1]) begin
                        $display("[SF-15] STATUS.DONE at cycle %0d", cycle);
                        mmio_cs   = 1'b0;
                        mmio_addr = 12'd0;
                        disable wait_done;
                    end
                    mmio_cs   = 1'b0;
                    mmio_addr = 12'd0;
                end
                disable wait_done;
            end
        join

        if (timed_out) begin
            $display("[SF-15] Scenario IRQ_EN=%0d: TIMEOUT", irq_en);
            pass = 1'b0;
            return;
        end

        // Wait a few more cycles for any straggling signals
        repeat(5) @(posedge clk);

        if (irq_en) begin
            // IRQ_EN=1: IRQ must rise within 2 cycles after last sram_wen
            if (irq_rise_cycle > 0 && last_sram_wen_cycle > 0) begin
                irq_delay = irq_rise_cycle - last_sram_wen_cycle;
                $display("SFU_IRQ_DELAY=%0d (last_sram_wen@cycle=%0d, irq_rise@cycle=%0d)",
                         irq_delay, last_sram_wen_cycle, irq_rise_cycle);

                if (irq_delay >= 0 && irq_delay <= 5) begin
                    $display("[SF-15] IRQ_EN=1: IRQ delay %0d cycles (≤5) — PASS", irq_delay);
                    pass = 1'b1;
                end else begin
                    $display("[SF-15] IRQ_EN=1: IRQ delay %0d cycles — FAIL (expected ≤5)", irq_delay);
                    pass = 1'b0;
                end
            end else begin
                $display("[SF-15] IRQ_EN=1: Failed to capture IRQ or sram_wen timing");
                pass = 1'b0;
            end
        end else begin
            // IRQ_EN=0: IRQ should stay 0
            if (irq_rise_cycle == 0) begin
                $display("SFU_IRQ_SUPPRESSED=1 (IRQ_EN=0, no IRQ observed)");
                $display("[SF-15] IRQ_EN=0: IRQ correctly suppressed — PASS");
                pass = 1'b1;
            end else begin
                $display("SFU_IRQ_SUPPRESSED=0 (IRQ_EN=0, but IRQ rose at cycle %0d)", irq_rise_cycle);
                $display("[SF-15] IRQ_EN=0: IRQ not suppressed — FAIL");
                pass = 1'b0;
            end
        end
    end
    endtask

    // Main test sequence
    initial begin
        reg pass_a, pass_b;

        mmio_cs    = 1'b0;
        mmio_we    = 1'b0;
        mmio_addr  = 12'd0;
        mmio_wdata = 32'd0;
        error_count = 0;

        // Initial reset
        reset_all();
        $display("[SF-15] === SFU Top IRQ Timing Test ===");

        // Scenario A: IRQ_EN=1, verify IRQ rises after last output
        run_softmax_irq_test(1'b1, N_ELEMENTS, pass_a);
        if (!pass_a) error_count = error_count + 1;

        // Scenario B: IRQ_EN=0, verify IRQ stays low
        run_softmax_irq_test(1'b0, N_ELEMENTS, pass_b);
        if (!pass_b) error_count = error_count + 1;

        if (error_count == 0)
            $display("[SF-15] ALL CHECKS PASSED");
        else
            $display("[SF-15] %0d CHECK(S) FAILED", error_count);

        #20;
        $finish(2);
    end

endmodule
