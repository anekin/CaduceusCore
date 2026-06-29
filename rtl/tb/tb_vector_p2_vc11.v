//=============================================================================
// VC-11: vector_top IRQ timing — IRQ rises after last chunk write complete
// Verifies IRQ asserts within ≤2 cycles of the last SRAM write (sram_o_wen)
// for a 2-chunk ADD operation (256 elements).
// Uses $display markers: IRQ_AFTER_WRITE_DELAY=n
//=============================================================================
`timescale 1ns / 1ps

module tb_vector_p2_vc11;
    localparam NUM_LANES = 128;
    localparam DATA_W    = 32;
    localparam VECTOR_W  = NUM_LANES * DATA_W;
    localparam ADDR_W    = 32;
    localparam CLK_HALF  = 5;

    reg clk, rst_n;
    reg mmio_cs, mmio_we;
    reg [11:0] mmio_addr;
    reg [31:0] mmio_wdata;
    wire [31:0] mmio_rdata;
    wire mmio_ready;

    wire [ADDR_W-1:0] sram_a_addr;
    wire sram_a_en;
    reg [VECTOR_W-1:0] sram_a_rdata;
    wire [ADDR_W-1:0] sram_b_addr;
    wire sram_b_en;
    reg [VECTOR_W-1:0] sram_b_rdata;
    wire [ADDR_W-1:0] sram_o_addr;
    wire [VECTOR_W-1:0] sram_o_wdata;
    wire sram_o_wen;
    wire [511:0] sram_o_wstrb;
    wire irq;

    vector_top #(.NUM_LANES(NUM_LANES),.DATA_W(DATA_W),.VECTOR_W(VECTOR_W),.FP16_W(16),.ADDR_W(ADDR_W))
    u_dut (.clk,.rst_n,.mmio_cs,.mmio_we,.mmio_addr,.mmio_wdata,.mmio_rdata,.mmio_ready,
           .sram_a_addr,.sram_a_en,.sram_a_rdata,.sram_b_addr,.sram_b_en,.sram_b_rdata,
           .sram_o_addr,.sram_o_wen,.sram_o_wdata,.sram_o_wstrb,.irq);

    // Wide SRAM model (131K entries, covers 0x00000..0x20000+ range)
    reg [31:0] sram_mem [0:131071];
    integer errors, i, j;
    reg [31:0] stat_val;

    // Cycle counter and IRQ timing
    reg [31:0] cycle_cnt;
    reg [31:0] last_write_cycle;
    reg [31:0] irq_rise_cycle;
    reg        irq_prev;
    reg        write_seen;

    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    // Cycle counter
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cycle_cnt <= 32'd0;
        else
            cycle_cnt <= cycle_cnt + 32'd1;
    end

    // IRQ timing monitor
    always @(posedge clk) begin
        irq_prev <= irq;
    end

    // SRAM model
    always @(*) begin
        sram_a_rdata = {VECTOR_W{1'b0}};
        sram_b_rdata = {VECTOR_W{1'b0}};
        if (sram_a_en) for (i=0;i<NUM_LANES;i=i+1) sram_a_rdata[i*DATA_W+:DATA_W]=sram_mem[(sram_a_addr>>2)+i];
        if (sram_b_en) for (i=0;i<NUM_LANES;i=i+1) sram_b_rdata[i*DATA_W+:DATA_W]=sram_mem[(sram_b_addr>>2)+i];
    end

    always @(posedge clk) begin
        if (sram_o_wen) begin
            for (i=0;i<NUM_LANES;i=i+1) if (sram_o_wstrb[i*4]) sram_mem[(sram_o_addr>>2)+i]<=sram_o_wdata[i*DATA_W+:DATA_W];
            // Record the cycle of every write (last one wins)
            last_write_cycle <= cycle_cnt;
            write_seen <= 1'b1;
        end
    end

    // Detect IRQ rising edge
    always @(posedge clk) begin
        if (irq && !irq_prev && write_seen) begin
            irq_rise_cycle <= cycle_cnt;
            $display("IRQ_AFTER_WRITE_DELAY=%0d (last_write_cycle=%0d, irq_rise_cycle=%0d)",
                     cycle_cnt - last_write_cycle, last_write_cycle, cycle_cnt);
        end
    end

    // MMIO helpers
    task mmio_write; input [11:0] a; input [31:0] d; begin
        @(negedge clk); mmio_cs=1;mmio_we=1;mmio_addr=a;mmio_wdata=d;
        @(negedge clk); mmio_cs=0;mmio_we=0;mmio_addr=0;mmio_wdata=0;
    end endtask

    task mmio_read; input [11:0] a; output [31:0] v; begin
        @(negedge clk); mmio_cs=1;mmio_we=0;mmio_addr=a;
        @(negedge clk); v=mmio_rdata; mmio_cs=0;mmio_addr=0;
    end endtask

    // Main test
    initial begin
        $display("=== VC-11: vector_top IRQ timing (after last chunk write) ===");
        errors=0;
        mmio_cs=0; mmio_we=0; mmio_addr=0; mmio_wdata=0;
        for (i=0;i<131072;i=i+1) sram_mem[i]=32'd0;
        irq_prev = 1'b0;
        write_seen = 1'b0;
        last_write_cycle = 32'd0;
        irq_rise_cycle = 32'd0;

        rst_n=0; repeat(10) @(posedge clk); rst_n=1; @(posedge clk);

        // Step 1: Fill SRAM with known data for ADD 256 elements
        // A region (0x00000): elements 1..256
        // B region (0x10000): elements 100..355
        $display("[VC-11] Filling SRAM with 256-element ADD vectors...");
        for (i=0;i<256;i=i+1) begin
            sram_mem[i] = 32'd1 + i;               // A: 1..256
            sram_mem[(32'h10000>>2)+i] = 32'd100 + i; // B: 100..355
        end

        // Also fill the output region with sentinels to verify wstrb
        for (i=0;i<512;i=i+1) begin
            sram_mem[(32'h20000>>2)+i] = 32'hDEAD0000 + i;
        end

        // Step 2: Configure and start ADD operation with DIM=256
        $display("[VC-11] Configuring ADD DIM=256...");
        mmio_write(12'h00, 32'd0);          // CTRL: OP=ADD
        mmio_write(12'h0C, 32'd0);          // A_ADDR=0x00000
        mmio_write(12'h10, 32'h00010000);   // B_ADDR=0x10000
        mmio_write(12'h14, 32'h00020000);   // O_ADDR=0x20000
        mmio_write(12'h18, 32'd256);        // DIM=256
        mmio_write(12'h1C, 32'd1);          // IRQ_EN=1

        $display("[VC-11] Starting ADD DIM=256 at cycle %0d...", cycle_cnt);
        mmio_write(12'h04, 32'd1);          // CMD START

        // Step 3: Wait for DONE
        repeat(500) begin
            mmio_read(12'h08, stat_val);
            if (stat_val[1]) begin
                $display("[VC-11] STATUS.DONE at cycle %0d", cycle_cnt);
                break;
            end
            @(posedge clk);
        end
        repeat(5) @(posedge clk);

        // Step 4: Verify output values
        $display("[VC-11] Verifying output values...");
        for (i=0;i<256;i=i+1) begin
            if (sram_mem[(32'h20000>>2)+i] !== 32'd1+i + 32'd100+i) begin
                $display("  FAIL idx=%0d: got=0x%08h expected=0x%08h",
                         i, sram_mem[(32'h20000>>2)+i], 32'd1+i+32'd100+i);
                errors=errors+1;
                if (errors>=5) i=256; // break
            end
        end

        // Step 5: Verify IRQ timing
        $display("[VC-11] IRQ timing report:");
        $display("  last_write_cycle=%0d, irq_rise_cycle=%0d", last_write_cycle, irq_rise_cycle);
        if (irq_rise_cycle > last_write_cycle && (irq_rise_cycle - last_write_cycle) <= 2) begin
            $display("  PASS: IRQ delay = %0d cycle(s) ≤ 2 cycles", irq_rise_cycle - last_write_cycle);
        end else if (irq_rise_cycle == last_write_cycle) begin
            $display("  PASS: IRQ same cycle as last write (delay=0)");
        end else begin
            $display("  FAIL: IRQ delay = %0d cycle(s) > 2 cycles", irq_rise_cycle - last_write_cycle);
            errors=errors+1;
        end

        // Step 6: Verify sentinel values beyond DIM are preserved
        $display("[VC-11] Verifying sentinels beyond DIM=256...");
        for (i=256;i<268;i=i+1) begin
            if (sram_mem[(32'h20000>>2)+i] !== 32'hDEAD0000 + i) begin
                $display("  FAIL sentinel idx=%0d corrupted: got=0x%08h expected=0x%08h",
                         i, sram_mem[(32'h20000>>2)+i], 32'hDEAD0000+i);
                errors=errors+1;
            end
        end

        // Summary
        $display("=== VC-11 Summary: %0d errors ===", errors);
        if (errors==0) $display("PASS");
        else $display("FAIL");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
