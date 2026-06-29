//=============================================================================
// VC-08: vector_top elements not multiple of 128 — last chunk lane_mask correct
// DIM=200 (1 full chunk of 128 + 1 partial of 72 with correct lane_mask + wstrb)
//=============================================================================
`timescale 1ns / 1ps

module tb_vector_p1_vc08;
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

    reg [31:0] sram_mem [0:131071];
    integer errors, total, i, j;
    reg [31:0] stat_val;

    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    always @(*) begin
        sram_a_rdata = {VECTOR_W{1'b0}};
        sram_b_rdata = {VECTOR_W{1'b0}};
        if (sram_a_en) for (i=0;i<NUM_LANES;i=i+1) sram_a_rdata[i*DATA_W+:DATA_W]=sram_mem[(sram_a_addr>>2)+i];
        if (sram_b_en) for (i=0;i<NUM_LANES;i=i+1) sram_b_rdata[i*DATA_W+:DATA_W]=sram_mem[(sram_b_addr>>2)+i];
    end

    always @(posedge clk) begin
        if (sram_o_wen) for (i=0;i<NUM_LANES;i=i+1) if (sram_o_wstrb[i*4]) sram_mem[(sram_o_addr>>2)+i]<=sram_o_wdata[i*DATA_W+:DATA_W];
    end

    task mmio_write; input [11:0] a; input [31:0] d; begin
        @(negedge clk); mmio_cs=1;mmio_we=1;mmio_addr=a;mmio_wdata=d;
        @(negedge clk); mmio_cs=0;mmio_we=0;mmio_addr=0;mmio_wdata=0;
    end endtask

    task mmio_read; input [11:0] a; output [31:0] v; begin
        @(negedge clk); mmio_cs=1;mmio_we=0;mmio_addr=a;
        @(negedge clk); v=mmio_rdata; mmio_cs=0;mmio_addr=0;
    end endtask

    initial begin
        #1;
        $display("=== VC-08: Elements not multiple of 128 (DIM=200) ===");
        errors=0; total=0;
        mmio_cs=0; mmio_we=0; mmio_addr=0; mmio_wdata=0;
        for (i=0;i<1024;i=i+1) sram_mem[i]=32'd0;
        rst_n=0; repeat(10) @(posedge clk); rst_n=1; @(posedge clk);

        // Place data: 200 known values for A, 200 for B
        // A in SRAM [0..199]: value=index (0,1,...,199)
        // B in SRAM at 0x10000: value=1000+index (1000,1001,...,1199)
        // Expected: result[i] = A[i] + B[i] = i + (1000+i) = 1000 + 2*i
        $display("[VC-08] Loading 200 elements...");
        for (i=0;i<200;i=i+1) sram_mem[i] = i;                     // A[0..199]
        for (i=0;i<200;i=i+1) sram_mem[(32'h10000>>2)+i] = 1000 + i; // B[0..199]

        // Pre-fill output region (0x20000) with sentinel — cover 256 entries (2 full chunks)
        for (i=0;i<256;i=i+1) sram_mem[(32'h20000>>2)+i] = 32'hDEADBEEF;

        // Configure ADD with DIM=200
        $display("[VC-08] Configuring ADD DIM=200...");
        mmio_write(12'h00, 32'd0);       // CTRL: OP=ADD
        mmio_write(12'h0C, 32'd0);       // A_ADDR
        mmio_write(12'h10, 32'h00010000); // B_ADDR
        mmio_write(12'h14, 32'h00020000); // O_ADDR
        mmio_write(12'h18, 32'd200);      // DIM=200
        mmio_write(12'h1C, 32'd1);
        mmio_write(12'h04, 32'd1);       // CMD START

        // Wait for DONE
        repeat(500) begin
            mmio_read(12'h08, stat_val);
            if (stat_val[1]) begin $display("[VC-08] STATUS.DONE at time %0t", $time); break; end
            @(posedge clk);
        end
        repeat(5) @(posedge clk);

        // Verify: Chunk 0 (128 elements: 0..127)
        //   A[i] = i, B[i] = 1000+i, result = 1000+2*i
        $display("[VC-08] Verifying chunk 0 (elements 0..127)...");
        for (i=0;i<128;i=i+1) begin
            total=total+1;
            if (sram_mem[(32'h20000>>2)+i] !== 1000 + 2*i) begin
                $display("  FAIL chunk0 idx=%0d: got=%0d expected=%0d",
                         i, $signed(sram_mem[(32'h20000>>2)+i]), 1000+2*i);
                errors=errors+1;
                if (errors>5) i=128; // break early
            end
        end

        // Verify: Chunk 1 (72 elements: 128..199)
        //   A starts at word 128 (A_ADDR + 512), B starts at offset 0x10000+512
        //   Chunk 1 write addr = 0x20000 + 512 → word offset 128
        $display("[VC-08] Verifying chunk 1 (elements 128..199, partial)...");
        for (i=128;i<200;i=i+1) begin
            total=total+1;
            if (sram_mem[(32'h20000>>2)+i] !== 1000 + 2*i) begin
                $display("  FAIL chunk1 idx=%0d: got=%0d expected=%0d",
                         i, $signed(sram_mem[(32'h20000>>2)+i]), 1000+2*i);
                errors=errors+1;
                if (errors>5) i=200;
            end
        end

        // Verify: Elements 200-255 (beyond DIM, within last chunk 128-lane width)
        // should NOT be modified (wstrb should be 0 for lanes 72-127 of chunk 1)
        $display("[VC-08] Verifying sentinels beyond DIM (elements 200-255)...");
        // Chunk 1 occupies SRAM words 128..255. Elements 200..255 should be untouched sentinel.
        // The DUT writes to O_ADDR (0x20000) + chunk_offset:
        //   chunk 0 output at 0x20000 (words 0..127)
        //   chunk 1 output at 0x20000+512 (=0x20200) (words 128..255) but only first 72 elements strobed
        // Actually: o_addr starts at 0x20000. After chunk 0, o_addr becomes 0x20000+512=0x20200.
        // Chunk 1 write to 0x20200 with wstrb for 72 elements = 72*4=288 bytes → strobe for first 72 words.
        // Words 128+72=200 through 255 should retain sentinel.
        for (i=200;i<200+20;i=i+1) begin
            total=total+1;
            if (sram_mem[(32'h20000>>2)+i] !== 32'hDEADBEEF) begin
                $display("  FAIL sentinel idx=%0d: got=0x%08h expected=0xDEADBEEF (wstrb leaked beyond DIM)",
                         i, sram_mem[(32'h20000>>2)+i]);
                errors=errors+1;
            end
        end

        $display("[VC-08] Total checks: %0d, errors: %0d", total, errors);
        if (errors==0) $display("PASS");
        else $display("FAIL");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
