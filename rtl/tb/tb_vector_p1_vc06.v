//=============================================================================
// VC-06: vector_top unaligned SRAM address — addr not multiple of 512
// Verifies the DUT handles non-512-aligned addresses correctly: no X-state,
// deterministic behavior (low 9 bits truncated or used as-is).
//=============================================================================
`timescale 1ns / 1ps

module tb_vector_p1_vc06;
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
        $display("=== VC-06: Unaligned SRAM address handling ===");
        errors=0; total=0;
        mmio_cs=0; mmio_we=0; mmio_addr=0; mmio_wdata=0;
        for (i=0;i<2048;i=i+1) sram_mem[i]=32'd0;
        rst_n=0; repeat(10) @(posedge clk); rst_n=1; @(posedge clk);

        // Place data at known locations. Unaligned address 0x100 = 256 bytes = 64 words offset.
        // A_addr=0x100 means reading from words 64+128=192 onward.
        // Place known data at word offsets 64..191 for A (128 elements starting at 0x100).
        $display("[VC-06] Placing data at unaligned A_ADDR=0x100...");
        for (i=0;i<128;i=i+1) sram_mem[64+i] = 32'd10 + i;        // A: offset 64 (=0x100>>2)
        for (i=0;i<128;i=i+1) sram_mem[(32'h10000>>2)+i] = 32'd20 + i; // B: 0x10000 (aligned)

        // Test 1: ADD with unaligned A_ADDR=0x100 (not multiple of 512=0x200)
        $display("[VC-06] Test 1: ADD DIM=64, A_ADDR=0x100 (unaligned)...");
        mmio_write(12'h00, 32'd0);       // CTRL: OP=ADD
        mmio_write(12'h0C, 32'h00000100); // A_ADDR=0x100 (unaligned, not multiple of 512)
        mmio_write(12'h10, 32'h00010000); // B_ADDR=0x10000
        mmio_write(12'h14, 32'h00020000); // O_ADDR=0x20000
        mmio_write(12'h18, 32'd64);       // DIM=64
        mmio_write(12'h1C, 32'd1);
        mmio_write(12'h04, 32'd1);        // CMD START

        repeat(200) begin
            mmio_read(12'h08, stat_val);
            if (stat_val[1]) break;
            @(posedge clk);
        end
        repeat(5) @(posedge clk);

        // Verify: output should be A[0x100:0x100+64*4-1] + B[0x10000:...]
        // A starts at word 64 (0x100>>2), values 10,11,12,...,73
        // B starts at word 0x4000 (=0x10000>>2), values 20,21,22,...,83
        $display("[VC-06] Verifying unaligned ADD results...");
        for (i=0;i<64;i=i+1) begin
            total=total+1;
            if (sram_mem[(32'h20000>>2)+i] !== 32'd10+i + 32'd20+i) begin
                $display("  FAIL idx=%0d: got=%0d expected=%0d",
                         i, $signed(sram_mem[(32'h20000>>2)+i]), (32'd10+i+32'd20+i));
                errors=errors+1;
            end
        end

        // Test 2: ADD with unaligned B_ADDR=0x110 (not multiple of 512)
        // Place new A data at offset 0 (A_ADDR=0x0), B data at unaligned 0x110
        for (i=0;i<128;i=i+1) sram_mem[i] = 32'd100 + i;                // A at 0x0
        for (i=0;i<128;i=i+1) sram_mem[(32'h110>>2)+i] = 32'd200 + i;   // B at unaligned 0x110

        // Reset DUT
        rst_n=0; repeat(5) @(posedge clk); rst_n=1; @(posedge clk);

        $display("[VC-06] Test 2: ADD DIM=32, A_ADDR=0x0, B_ADDR=0x110 (unaligned)...");
        mmio_write(12'h00, 32'd0);
        mmio_write(12'h0C, 32'd0);        // A_ADDR=0
        mmio_write(12'h10, 32'h00000110); // B_ADDR=0x110 (unaligned, not multiple of 512)
        mmio_write(12'h14, 32'h00030000); // O_ADDR
        mmio_write(12'h18, 32'd32);       // DIM=32
        mmio_write(12'h1C, 32'd1);
        mmio_write(12'h04, 32'd1);

        repeat(200) begin
            mmio_read(12'h08, stat_val);
            if (stat_val[1]) break;
            @(posedge clk);
        end
        repeat(5) @(posedge clk);

        for (i=0;i<32;i=i+1) begin
            total=total+1;
            // A[0]=100..131, B at 0x110>>2 = offset 68, values 200,201,...
            if (sram_mem[(32'h30000>>2)+i] !== 32'd100+i + 32'd200+i) begin
                $display("  FAIL idx=%0d: got=%0d expected=%0d",
                         i, $signed(sram_mem[(32'h30000>>2)+i]), (32'd100+i+32'd200+i));
                errors=errors+1;
            end
        end

        // Test 3: Verify unaligned output address
        rst_n=0; repeat(5) @(posedge clk); rst_n=1; @(posedge clk);

        $display("[VC-06] Test 3: ADD with unaligned O_ADDR=0x100...");
        for (i=0;i<128;i=i+1) sram_mem[i] = 32'd5 + i;
        for (i=0;i<128;i=i+1) sram_mem[(32'h10000>>2)+i] = 32'd3 + i;
        // Pre-fill output unaligned area
        for (i=0;i<128;i=i+1) sram_mem[(32'h100>>2)+i] = 32'hDEADBEEF;

        mmio_write(12'h00, 32'd0);
        mmio_write(12'h0C, 32'd0);        // A_ADDR=0
        mmio_write(12'h10, 32'h00010000); // B_ADDR
        mmio_write(12'h14, 32'h00000100); // O_ADDR=0x100 (unaligned)
        mmio_write(12'h18, 32'd64);
        mmio_write(12'h1C, 32'd1);
        mmio_write(12'h04, 32'd1);

        repeat(200) begin
            mmio_read(12'h08, stat_val);
            if (stat_val[1]) break;
            @(posedge clk);
        end
        repeat(5) @(posedge clk);

        for (i=0;i<64;i=i+1) begin
            total=total+1;
            if (sram_mem[(32'h100>>2)+i] !== 32'd5+i + 32'd3+i) begin
                $display("  FAIL idx=%0d: got=%0d expected=%0d",
                         i, $signed(sram_mem[(32'h100>>2)+i]), (32'd5+i+32'd3+i));
                errors=errors+1;
            end
        end

        // Verify no X-state propagation (all results should be deterministic)
        $display("[VC-06] Total checks: %0d, errors: %0d", total, errors);
        if (errors==0) $display("PASS");
        else $display("FAIL");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
