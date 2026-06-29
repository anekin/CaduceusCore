//=============================================================================
// VC-05: vector_top SRAM wstrb per-byte write masking
// Verifies partial write via strb pattern — bytes beyond strobed region
// retain their original values while strobed bytes are updated.
//=============================================================================
`timescale 1ns / 1ps

module tb_vector_p1_vc05;
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

    reg [31:0] sram_mem [0:131071];  // 128K entries, cover 0x20000+ range
    integer errors, total, i, j, k;
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
        $display("=== VC-05: SRAM wstrb per-byte write masking ===");
        errors=0; total=0;
        mmio_cs=0; mmio_we=0; mmio_addr=0; mmio_wdata=0;
        for (i=0;i<512;i=i+1) sram_mem[i]=32'd0;  // only zero first 512 entries
        rst_n=0; repeat(10) @(posedge clk); rst_n=1; @(posedge clk);

        // Step 1: Fill SRAM with known pattern (0xDEADBEEF at A region, 0xCAFEBABE at B region,
        // and 0xABCD0001..0xABCD0080 at the output region — includes bytes beyond the partial write)
        $display("[VC-05] Filling SRAM...");
        for (i=0;i<128;i=i+1) sram_mem[i] = 32'd1 + i;       // A input: 1,2,...,128
        for (i=0;i<128;i=i+1) sram_mem[(32'h10000>>2)+i] = 32'd100 + i; // B input: 100,101,...,227
        // Pre-fill output region (0x20000) with sentinel pattern 0xDEAD0000 + index
        for (i=0;i<128;i=i+1) sram_mem[(32'h20000>>2)+i] = 32'hDEAD0000 + i;

        // Step 2: Configure and start ADD operation with DIM=64 (partial chunk)
        //   A=0x00000, B=0x10000, O=0x20000
        $display("[VC-05] Configuring ADD DIM=64...");
        mmio_write(12'h00, 32'd0);  // CTRL: OP=ADD
        mmio_write(12'h0C, 32'd0);  // A_ADDR
        mmio_write(12'h10, 32'h00010000); // B_ADDR
        mmio_write(12'h14, 32'h00020000); // O_ADDR
        mmio_write(12'h18, 32'd64); // DIM=64 (128 B, partial chunk)
        mmio_write(12'h1C, 32'd1);  // IRQ_EN

        $display("[VC-05] Starting operation...");
        mmio_write(12'h04, 32'd1);  // CMD START

        // Step 3: Wait for DONE
        repeat(200) begin
            mmio_read(12'h08, stat_val);
            if (stat_val[1]) begin
                $display("[VC-05] STATUS.DONE at time %0t", $time);
                break;
            end
            @(posedge clk);
        end
        repeat(5) @(posedge clk);

        // Step 4: Verify results — first 64 elements should be ADD results,
        // elements 64-127 should retain sentinel values (unchanged by partial wstrb)
        $display("[VC-05] Verifying wstrb write masking...");
        for (i=0;i<64;i=i+1) begin
            total=total+1;
            if (sram_mem[(32'h20000>>2)+i] !== 32'd1 + i + 32'd100 + i) begin
                $display("  FAIL idx=%0d: got=0x%08h expected=0x%08h",
                         i, sram_mem[(32'h20000>>2)+i], 32'd1+i+32'd100+i);
                errors=errors+1;
            end
        end
        // Elements 64-127 must still be sentinel values (0xDEAD0040..0xDEAD007F)
        for (i=64;i<128;i=i+1) begin
            total=total+1;
            if (sram_mem[(32'h20000>>2)+i] !== 32'hDEAD0000 + i) begin
                $display("  FAIL sentinel idx=%0d: got=0x%08h expected=0x%08h (wstrb leaked beyond partial chunk)",
                         i, sram_mem[(32'h20000>>2)+i], 32'hDEAD0000+i);
                errors=errors+1;
            end
        end

        // Also verify A input region not modified
        for (i=0;i<10;i=i+1) begin
            total=total+1;
            if (sram_mem[i] !== 32'd1 + i) begin
                $display("  FAIL A region idx=%0d corrupted", i);
                errors=errors+1;
            end
        end

        // Summary
        $display("=== VC-05 Summary: %0d/%0d checks, %0d errors ===", total-errors, total, errors);
        if (errors==0) $display("PASS");
        else $display("FAIL");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
