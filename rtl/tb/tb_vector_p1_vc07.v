//=============================================================================
// VC-07: vector_top DIM=0 — STATUS.DONE within 10 cycles, no SRAM access
// Verifies that DIM=0 causes immediate transition to DONE without accessing SRAM.
// Logs DIM0_DONE_CYCLES=<N> and asserts N <= 10.
//=============================================================================
`timescale 1ns / 1ps

module tb_vector_p1_vc07;
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

    reg [31:0] sram_mem [0:255];
    integer errors, i;
    reg [31:0] stat_val;
    reg [31:0] cycle_cnt;
    reg [31:0] done_cycles;
    reg [31:0] start_cycle;
    reg        sram_accessed;
    reg        sram_a_accessed;
    reg        sram_b_accessed;
    reg        sram_o_accessed;

    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) cycle_cnt <= 0;
        else cycle_cnt <= cycle_cnt + 1;
    end

    always @(*) begin
        sram_a_rdata = {VECTOR_W{1'b0}};
        sram_b_rdata = {VECTOR_W{1'b0}};
        if (sram_a_en) begin sram_a_rdata = {VECTOR_W{1'b1}}; sram_a_accessed = 1'b1; end
        if (sram_b_en) begin sram_b_rdata = {VECTOR_W{1'b1}}; sram_b_accessed = 1'b1; end
    end

    always @(posedge clk) if (sram_o_wen) sram_o_accessed = 1'b1;

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
        $display("=== VC-07: DIM=0 immediate DONE, no SRAM access ===");
        errors=0;
        mmio_cs=0; mmio_we=0; mmio_addr=0; mmio_wdata=0;
        sram_a_accessed=0; sram_b_accessed=0; sram_o_accessed=0;
        for (i=0;i<256;i=i+1) sram_mem[i]=32'd0;
        rst_n=0; repeat(10) @(posedge clk); rst_n=1; @(posedge clk);

        // Run three tests: different ops with DIM=0
        $display("[VC-07] Test 1: ADD with DIM=0...");
        mmio_write(12'h00, 32'd0);       // CTRL: OP=ADD
        mmio_write(12'h0C, 32'h0);       // A_ADDR
        mmio_write(12'h10, 32'h10000);   // B_ADDR
        mmio_write(12'h14, 32'h20000);   // O_ADDR
        mmio_write(12'h18, 32'd0);       // DIM=0
        mmio_write(12'h1C, 32'd1);
        start_cycle = cycle_cnt;
        mmio_write(12'h04, 32'd1);       // CMD START

        // Wait for DONE
        repeat(100) begin
            mmio_read(12'h08, stat_val);
            if (stat_val[1]) begin
                done_cycles = cycle_cnt - start_cycle;
                $display("[VC-07] DIM0_DONE_CYCLES=%0d (ADD)", done_cycles);
                if (done_cycles > 10) begin
                    $display("  FAIL: DONE took %0d cycles, expected <= 10", done_cycles);
                    errors=errors+1;
                end else begin
                    $display("  PASS: DONE <= 10 cycles");
                end
                break;
            end
            @(posedge clk);
        end

        // Verify no SRAM access
        if (sram_a_accessed) begin $display("  FAIL: SRAM port A was accessed"); errors=errors+1; end
        else $display("  PASS: No SRAM port A access");
        if (sram_b_accessed) begin $display("  FAIL: SRAM port B was accessed"); errors=errors+1; end
        else $display("  PASS: No SRAM port B access");
        if (sram_o_accessed) begin $display("  FAIL: SRAM port O was accessed"); errors=errors+1; end
        else $display("  PASS: No SRAM port O access");

        // Reset, test with SUM op
        rst_n=0; sram_a_accessed=0; sram_b_accessed=0; sram_o_accessed=0;
        repeat(5) @(posedge clk); rst_n=1; @(posedge clk);

        $display("[VC-07] Test 2: SUM with DIM=0...");
        mmio_write(12'h00, 32'd3);       // CTRL: OP=SUM
        mmio_write(12'h0C, 32'h0);
        mmio_write(12'h14, 32'h30000);
        mmio_write(12'h18, 32'd0);       // DIM=0
        mmio_write(12'h1C, 32'd1);
        start_cycle = cycle_cnt;
        mmio_write(12'h04, 32'd1);       // CMD START

        repeat(100) begin
            mmio_read(12'h08, stat_val);
            if (stat_val[1]) begin
                done_cycles = cycle_cnt - start_cycle;
                $display("[VC-07] DIM0_DONE_CYCLES=%0d (SUM)", done_cycles);
                if (done_cycles > 10) begin
                    $display("  FAIL: DONE took %0d cycles", done_cycles);
                    errors=errors+1;
                end else $display("  PASS: DONE <= 10 cycles");
                break;
            end
            @(posedge clk);
        end

        if (sram_a_accessed) begin $display("  FAIL: SRAM port A was accessed"); errors=errors+1; end
        if (sram_o_accessed) begin $display("  FAIL: SRAM port O was accessed"); errors=errors+1; end

        // Reset, test with RESID
        rst_n=0; sram_a_accessed=0; sram_b_accessed=0; sram_o_accessed=0;
        repeat(5) @(posedge clk); rst_n=1; @(posedge clk);

        $display("[VC-07] Test 3: RESID with DIM=0...");
        mmio_write(12'h00, 32'd5);       // CTRL: OP=RESID
        mmio_write(12'h0C, 32'h0);
        mmio_write(12'h14, 32'h40000);
        mmio_write(12'h18, 32'd0);       // DIM=0
        mmio_write(12'h1C, 32'd1);
        start_cycle = cycle_cnt;
        mmio_write(12'h04, 32'd1);       // CMD START

        repeat(100) begin
            mmio_read(12'h08, stat_val);
            if (stat_val[1]) begin $display("[VC-07] DIM0_DONE_CYCLES=%0d (RESID)", cycle_cnt - start_cycle); break; end
            @(posedge clk);
        end

        if (errors==0) $display("PASS: VC-07 all tests passed");
        else $display("FAIL: VC-07 %0d errors", errors);
        if (errors==0) $display("PASS");
        else $display("FAIL");
        $finish;
    end

    initial begin #50000; $display("TIMEOUT"); $finish; end
endmodule
