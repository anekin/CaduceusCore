//=============================================================================
// VC-13: vector_top back-to-back ops — address registers persist between ops
// Runs ADD then MUL without reset, verifies both outputs independently.
// Uses $display markers: BACK2BACK_OP1_PASS, BACK2BACK_OP2_PASS
//=============================================================================
`timescale 1ns / 1ps

module tb_vector_p2_vc13;
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
    integer errors, i;
    reg [31:0] stat_val;
    reg [31:0] cycle_cnt;

    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    always @(posedge clk or negedge rst_n)
        if (!rst_n) cycle_cnt <= 0; else cycle_cnt <= cycle_cnt + 1;

    always @(*) begin
        sram_a_rdata={VECTOR_W{1'b0}}; sram_b_rdata={VECTOR_W{1'b0}};
        if (sram_a_en) for (i=0;i<NUM_LANES;i=i+1) sram_a_rdata[i*DATA_W+:DATA_W]=sram_mem[(sram_a_addr>>2)+i];
        if (sram_b_en) for (i=0;i<NUM_LANES;i=i+1) sram_b_rdata[i*DATA_W+:DATA_W]=sram_mem[(sram_b_addr>>2)+i];
    end
    always @(posedge clk)
        if (sram_o_wen) for (i=0;i<NUM_LANES;i=i+1) if (sram_o_wstrb[i*4]) sram_mem[(sram_o_addr>>2)+i]<=sram_o_wdata[i*DATA_W+:DATA_W];

    task mmio_write; input [11:0] a; input [31:0] d; begin
        @(negedge clk); mmio_cs=1;mmio_we=1;mmio_addr=a;mmio_wdata=d;
        @(negedge clk); mmio_cs=0;mmio_we=0;mmio_addr=0;mmio_wdata=0;
    end endtask
    task mmio_read; input [11:0] a; output [31:0] v; begin
        @(negedge clk); mmio_cs=1;mmio_we=0;mmio_addr=a;
        @(negedge clk); v=mmio_rdata; mmio_cs=0;mmio_addr=0;
    end endtask

    // Wait for DONE and verify output
    task wait_done_and_check;
        input [31:0] output_base;
        input [31:0] num_elements;
        input [1023:0] op_name;
        integer op_errors, j;
        begin
            op_errors = 0;
            repeat(500) begin
                mmio_read(12'h08, stat_val);
                if (stat_val[1]) begin
                    $display("[VC-13] %0s DONE at cycle %0d", op_name, cycle_cnt);
                    break;
                end
                @(posedge clk);
            end
            repeat(5) @(posedge clk);

            // Verify results
            for (j=0;j<num_elements;j=j+1) begin
                if (op_name == "OP1-ADD") begin
                    // Output[j] = A[j]+B[j] where A data at 0x00000, B at 0x10000
                    if (sram_mem[(output_base>>2)+j] !== sram_mem[((32'h00000)>>2)+j] + sram_mem[((32'h10000)>>2)+j]) begin
                        $display("  FAIL %0s idx=%0d: got=0x%08h expected=0x%08h",
                                 op_name, j,
                                 sram_mem[(output_base>>2)+j],
                                 sram_mem[((32'h00000)>>2)+j] + sram_mem[((32'h10000)>>2)+j]);
                        op_errors=op_errors+1;
                        if (op_errors>=5) j=num_elements;
                    end
                end else begin // OP2-MUL
                    // Output[j] = A[j]*B[j] where A data at 0x00400, B at 0x10400
                    if (sram_mem[(output_base>>2)+j] !== sram_mem[((32'h00400)>>2)+j] * sram_mem[((32'h10400)>>2)+j]) begin
                        $display("  FAIL %0s idx=%0d: got=0x%08h expected=0x%08h",
                                 op_name, j,
                                 sram_mem[(output_base>>2)+j],
                                 sram_mem[((32'h00400)>>2)+j] * sram_mem[((32'h10400)>>2)+j]);
                        op_errors=op_errors+1;
                        if (op_errors>=5) j=num_elements;
                    end
                end
            end
            if (op_errors==0) begin
                $display("  PASS: %0s all %0d elements correct", op_name, num_elements);
                if (op_name == "OP1-ADD") $display("BACK2BACK_OP1_PASS");
                if (op_name == "OP2-MUL") $display("BACK2BACK_OP2_PASS");
            end
            errors = errors + op_errors;
        end
    endtask

    initial begin
        $display("=== VC-13: vector_top back-to-back ops (address register persistence) ===");
        errors=0;
        mmio_cs=0; mmio_we=0; mmio_addr=0; mmio_wdata=0;
        for (i=0;i<131072;i=i+1) sram_mem[i]=32'd0;

        rst_n=0; repeat(10) @(posedge clk); rst_n=1; @(posedge clk);

        // ── Pre-fill SRAM ──────────────────────────────────────────────────
        // OP1 (ADD): A at 0x00000, B at 0x10000, output at 0x20000
        // OP2 (MUL): A at 0x00400, B at 0x10400, output at 0x20400
        $display("[VC-13] Filling SRAM with test vectors...");

        // OP1-ADD data: A=1..128, B=100..227
        for (i=0;i<128;i=i+1) begin
            sram_mem[(32'h00000>>2)+i] = 32'd1 + i;
            sram_mem[(32'h10000>>2)+i] = 32'd100 + i;
        end

        // OP2-MUL data: A=2..129, B=3..130
        for (i=0;i<128;i=i+1) begin
            sram_mem[(32'h00400>>2)+i] = 32'd2 + i;
            sram_mem[(32'h10400>>2)+i] = 32'd3 + i;
        end

        // ── OP1: ADD(128 elements) ─────────────────────────────────────────
        $display("[VC-13] === OP1: ADD DIM=128 ===");
        $display("[VC-13]   A=0x00000, B=0x10000, O=0x20000");
        mmio_write(12'h00, 32'd0);          // CTRL: OP=ADD
        mmio_write(12'h0C, 32'd0);          // A_ADDR=0x00000
        mmio_write(12'h10, 32'h00010000);   // B_ADDR=0x10000
        mmio_write(12'h14, 32'h00020000);   // O_ADDR=0x20000
        mmio_write(12'h18, 32'd128);        // DIM=128
        mmio_write(12'h1C, 32'd1);          // IRQ_EN=1
        mmio_write(12'h04, 32'd1);          // CMD START

        wait_done_and_check(32'h00020000, 32'd128, "OP1-ADD");

        // ── OP2: MUL(128 elements) — no reset ─────────────────────────────
        $display("[VC-13] === OP2: MUL DIM=128 (back-to-back, no reset) ===");
        $display("[VC-13]   A=0x00400, B=0x10400, O=0x20400");
        mmio_write(12'h00, 32'd1);          // CTRL: OP=MUL
        mmio_write(12'h0C, 32'h00000400);    // A_ADDR=0x00400
        mmio_write(12'h10, 32'h00010400);   // B_ADDR=0x10400
        mmio_write(12'h14, 32'h00020400);   // O_ADDR=0x20400
        mmio_write(12'h18, 32'd128);        // DIM=128
        mmio_write(12'h1C, 32'd1);          // IRQ_EN=1
        mmio_write(12'h04, 32'd1);          // CMD START

        wait_done_and_check(32'h00020400, 32'd128, "OP2-MUL");

        // ── Verify OP1 output region wasn't corrupted ─────────────────────
        $display("[VC-13] Verifying OP1 output region not corrupted by OP2...");
        for (i=0;i<128;i=i+1) begin
            if (sram_mem[(32'h20000>>2)+i] !== 32'd1+i + 32'd100+i) begin
                $display("  FAIL OP1 corr idx=%0d: got=0x%08h expected=0x%08h",
                         i, sram_mem[(32'h20000>>2)+i], 32'd1+i+32'd100+i);
                errors=errors+1;
                if (errors>=10) i=128;
            end
        end

        // ── Verify OP2 output region is correct ────────────────────────────
        $display("[VC-13] Verifying OP2 output region...");
        for (i=0;i<128;i=i+1) begin
            if (sram_mem[(32'h20400>>2)+i] !== (32'd2+i) * (32'd3+i)) begin
                $display("  FAIL OP2 idx=%0d: got=0x%08h expected=0x%08h",
                         i, sram_mem[(32'h20400>>2)+i], (32'd2+i)*(32'd3+i));
                errors=errors+1;
                if (errors>=10) i=128;
            end
        end

        // ── Verify A/B input regions not corrupted ─────────────────────────
        $display("[VC-13] Verifying input regions not corrupted...");
        for (i=0;i<10;i=i+1) begin
            if (sram_mem[(32'h00000>>2)+i] !== 32'd1+i) begin
                $display("  FAIL A1 region idx=%0d corrupted", i); errors=errors+1; end
            if (sram_mem[(32'h10000>>2)+i] !== 32'd100+i) begin
                $display("  FAIL B1 region idx=%0d corrupted", i); errors=errors+1; end
            if (sram_mem[(32'h00400>>2)+i] !== 32'd2+i) begin
                $display("  FAIL A2 region idx=%0d corrupted", i); errors=errors+1; end
            if (sram_mem[(32'h10400>>2)+i] !== 32'd3+i) begin
                $display("  FAIL B2 region idx=%0d corrupted", i); errors=errors+1; end
        end

        // Summary
        $display("=== VC-13 Summary: %0d errors ===", errors);
        if (errors==0) $display("PASS");
        else $display("FAIL");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
