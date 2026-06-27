//=============================================================================
// tb_sram_ctrl — Self-Checking SRAM Controller Testbench
// CaduceusCore SoC Phase 3-4 / Task 2
//
// Tests:
//   TC1: $readmemh initialization — pre-load sram_init.hex, verify readback
//   TC2: Single write → single read (AXI4 INCR, len=0)
//   TC3: Burst-4 write → burst-4 read (AXI4 INCR, len=3)
//   TC4: Out-of-range address 0x2040_0000 → DECERR (RRESP=2'b11)
//
// Usage:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_sram_ctrl \
//       CaduceusCore/rtl/tb/tb_sram_ctrl.v CaduceusCore/rtl/soc/sram_ctrl.v \
//       -o simv_tb_sram_ctrl
//   ./simv_tb_sram_ctrl
//=============================================================================

`timescale 1ns / 1ps

module tb_sram_ctrl;

    // =========================================================================
    // Parameters
    // =========================================================================
    localparam CLK_HALF   = 5;            // 100 MHz (1 GHz = 0.5ns half-period)
    localparam DATA_WIDTH = 512;
    localparam ADDR_WIDTH = 32;
    localparam ID_WIDTH   = 8;

    localparam [ADDR_WIDTH-1:0] SRAM_BASE = 32'h2000_0000;
    localparam [ADDR_WIDTH-1:0] SRAM_END  = 32'h203F_FFFF;

    // =========================================================================
    // DUT Signals
    // =========================================================================
    reg                      clk;
    reg                      rst_n;

    // AXI4 Write Address
    reg  [ID_WIDTH-1:0]      s_axi_awid;
    reg  [ADDR_WIDTH-1:0]    s_axi_awaddr;
    reg  [7:0]               s_axi_awlen;
    reg  [2:0]               s_axi_awsize;
    reg  [1:0]               s_axi_awburst;
    reg                      s_axi_awvalid;
    wire                     s_axi_awready;

    // AXI4 Write Data
    reg  [DATA_WIDTH-1:0]    s_axi_wdata;
    reg  [DATA_WIDTH/8-1:0]  s_axi_wstrb;
    reg                      s_axi_wlast;
    reg                      s_axi_wvalid;
    wire                     s_axi_wready;

    // AXI4 Write Response
    wire [ID_WIDTH-1:0]      s_axi_bid;
    wire [1:0]               s_axi_bresp;
    wire                     s_axi_bvalid;
    reg                      s_axi_bready;

    // AXI4 Read Address
    reg  [ID_WIDTH-1:0]      s_axi_arid;
    reg  [ADDR_WIDTH-1:0]    s_axi_araddr;
    reg  [7:0]               s_axi_arlen;
    reg  [2:0]               s_axi_arsize;
    reg  [1:0]               s_axi_arburst;
    reg                      s_axi_arvalid;
    wire                     s_axi_arready;

    // AXI4 Read Data
    wire [ID_WIDTH-1:0]      s_axi_rid;
    wire [DATA_WIDTH-1:0]    s_axi_rdata;
    wire [1:0]               s_axi_rresp;
    wire                     s_axi_rlast;
    wire                     s_axi_rvalid;
    reg                      s_axi_rready;

    // =========================================================================
    // DUT Instantiation
    // =========================================================================
    sram_ctrl #(
        .DATA_WIDTH (DATA_WIDTH),
        .ADDR_WIDTH (ADDR_WIDTH),
        .ID_WIDTH   (ID_WIDTH)
    ) u_dut (
        .clk            (clk),
        .rst_n          (rst_n),
        .s_axi_awid     (s_axi_awid),
        .s_axi_awaddr   (s_axi_awaddr),
        .s_axi_awlen    (s_axi_awlen),
        .s_axi_awsize   (s_axi_awsize),
        .s_axi_awburst  (s_axi_awburst),
        .s_axi_awvalid  (s_axi_awvalid),
        .s_axi_awready  (s_axi_awready),
        .s_axi_wdata    (s_axi_wdata),
        .s_axi_wstrb    (s_axi_wstrb),
        .s_axi_wlast    (s_axi_wlast),
        .s_axi_wvalid   (s_axi_wvalid),
        .s_axi_wready   (s_axi_wready),
        .s_axi_bid      (s_axi_bid),
        .s_axi_bresp    (s_axi_bresp),
        .s_axi_bvalid   (s_axi_bvalid),
        .s_axi_bready   (s_axi_bready),
        .s_axi_arid     (s_axi_arid),
        .s_axi_araddr   (s_axi_araddr),
        .s_axi_arlen    (s_axi_arlen),
        .s_axi_arsize   (s_axi_arsize),
        .s_axi_arburst  (s_axi_arburst),
        .s_axi_arvalid  (s_axi_arvalid),
        .s_axi_arready  (s_axi_arready),
        .s_axi_rid      (s_axi_rid),
        .s_axi_rdata    (s_axi_rdata),
        .s_axi_rresp    (s_axi_rresp),
        .s_axi_rlast    (s_axi_rlast),
        .s_axi_rvalid   (s_axi_rvalid),
        .s_axi_rready   (s_axi_rready)
    );

    // =========================================================================
    // Clock and Reset
    // =========================================================================
    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    // =========================================================================
    // AXI4 Master tasks
    // =========================================================================

    // ── AXI4 Write burst ────────────────────────────────────────────────────
    task axi_write;
        input [ADDR_WIDTH-1:0] addr;
        input [7:0]            len;       // burst length = len+1 beats
        input [1:0]            burst_type;
        input [DATA_WIDTH-1:0] data [];   // dynamic array of write data per beat
        output [1:0]           bresp_out;
    begin
        automatic integer beat;
        automatic reg [ADDR_WIDTH-1:0] beat_addr;

        // ── Phase 1: Write Address (AW) ─────────────────────────────────
        s_axi_awvalid = 1'b0;
        s_axi_awid    = 8'h00;
        s_axi_awaddr  = addr;
        s_axi_awlen   = len;
        s_axi_awsize  = 3'd6;     // 64 bytes = 512-bit
        s_axi_awburst = burst_type;

        @(negedge clk);
        s_axi_awvalid = 1'b1;
        while (!s_axi_awready) @(posedge clk);
        @(negedge clk);
        s_axi_awvalid = 1'b0;

        // ── Phase 2: Write Data (W) ─────────────────────────────────────
        for (beat = 0; beat <= len; beat = beat + 1) begin
            s_axi_wvalid = 1'b0;
            s_axi_wdata  = data[beat];
            s_axi_wstrb  = {DATA_WIDTH/8{1'b1}};  // all bytes valid
            s_axi_wlast  = (beat == len) ? 1'b1 : 1'b0;

            @(negedge clk);
            s_axi_wvalid = 1'b1;
            while (!s_axi_wready) @(posedge clk);
            @(negedge clk);
            s_axi_wvalid = 1'b0;
        end

        // ── Phase 3: Write Response (B) ─────────────────────────────────
        s_axi_bready = 1'b0;
        while (!s_axi_bvalid) @(posedge clk);
        @(negedge clk);
        s_axi_bready = 1'b1;
        bresp_out = s_axi_bresp;
        @(negedge clk);
        s_axi_bready = 1'b0;
    end
    endtask

    // ── AXI4 Read burst ─────────────────────────────────────────────────────
    task axi_read;
        input  [ADDR_WIDTH-1:0] addr;
        input  [7:0]            len;       // burst length = len+1 beats
        input  [1:0]            burst_type;
        output [DATA_WIDTH-1:0] rdata [];  // dynamic array, populated by caller
        output [1:0]            rresp_out;
    begin
        automatic integer beat;
        automatic integer idx;

        // Allocate the output array to len+1 elements
        rdata = new[len + 1];

        // ── Phase 1: Read Address (AR) ──────────────────────────────────
        s_axi_arvalid = 1'b0;
        s_axi_arid    = 8'h00;
        s_axi_araddr  = addr;
        s_axi_arlen   = len;
        s_axi_arsize  = 3'd6;
        s_axi_arburst = burst_type;

        @(negedge clk);
        s_axi_arvalid = 1'b1;
        while (!s_axi_arready) @(posedge clk);
        @(negedge clk);
        s_axi_arvalid = 1'b0;

        // ── Phase 2: Read Data (R) ──────────────────────────────────────
        for (beat = 0; beat <= len; beat = beat + 1) begin
            s_axi_rready = 1'b0;
            while (!s_axi_rvalid) @(posedge clk);
            @(negedge clk);
            s_axi_rready = 1'b1;
            rdata[beat]  = s_axi_rdata;
            if (beat == len)
                rresp_out = s_axi_rresp;
            @(negedge clk);
            s_axi_rready = 1'b0;
        end
    end
    endtask

    // =========================================================================
    // Test orchestration
    // =========================================================================
    reg [DATA_WIDTH-1:0] wdata_arr [];      // dynamic array
    reg [DATA_WIDTH-1:0] rdata_arr [];      // dynamic array
    reg [1:0]            resp;
    integer              tc_pass, tc_fail;
    integer              i;

    initial begin
        // ── Initialize AXI signals ──────────────────────────────────────────
        s_axi_awvalid = 1'b0; s_axi_awid = '0; s_axi_awaddr = '0;
        s_axi_awlen   = '0;   s_axi_awsize = '0; s_axi_awburst = '0;
        s_axi_wvalid  = 1'b0; s_axi_wdata = '0; s_axi_wstrb = '0; s_axi_wlast = 1'b0;
        s_axi_bready  = 1'b0;
        s_axi_arvalid = 1'b0; s_axi_arid = '0; s_axi_araddr = '0;
        s_axi_arlen   = '0;   s_axi_arsize = '0; s_axi_arburst = '0;
        s_axi_rready  = 1'b0;

        tc_pass = 0;
        tc_fail = 0;

        // ── Reset sequence (5 cycles low → de-assert) ───────────────────────
        $display("============================================================");
        $display("[TB] SRAM Controller Testbench");
        $display("============================================================");
        rst_n = 1'b0;
        repeat(5) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        $display("[TB] Reset released at %0t", $time);
        $display("");

        // =====================================================================
        // TC1: $readmemh initialization check
        // =====================================================================
        $display("--- TC1: $readmemh initialization ---");
        // sram_init.hex should pre-load word 0 with DEAD_BEEF pattern
        // and word 1 with CAFE_BABE pattern
        axi_read(SRAM_BASE + 32'h0000, 8'd0, 2'b01, rdata_arr, resp);
        // Verify lower 32 bits = DEADBEEF and data is non-zero
        if (rdata_arr[0][31:0] === 32'hDEADBEEF && rdata_arr[0] !== 512'd0 && resp === 2'b00) begin
            $display("  PASS: Read 0x2000_0000 lower 32b = DEADBEEF (init OK)");
            tc_pass = tc_pass + 1;
        end else begin
            $display("  FAIL: Read 0x2000_0000[31:0] = %0h, resp=%b (expected 32'hDEADBEEF, 00)", rdata_arr[0][31:0], resp);
            tc_fail = tc_fail + 1;
        end

        axi_read(SRAM_BASE + 32'h0040, 8'd0, 2'b01, rdata_arr, resp);
        if (rdata_arr[0][31:0] === 32'hCAFEBABE && rdata_arr[0] !== 512'd0 && resp === 2'b00) begin
            $display("  PASS: Read 0x2000_0040 lower 32b = CAFEBABE (init OK)");
            tc_pass = tc_pass + 1;
        end else begin
            $display("  FAIL: Read 0x2000_0040[31:0] = %0h, resp=%b (expected 32'hCAFEBABE, 00)", rdata_arr[0][31:0], resp);
            tc_fail = tc_fail + 1;
        end
        $display("");

        // =====================================================================
        // TC2: Single write → single read (AXI4 INCR, len=0 = 1 beat)
        // =====================================================================
        $display("--- TC2: Single write → single read ---");
        // Write 0xABCD_0000... to address 0x2000_0100
        wdata_arr = new[1];
        wdata_arr[0] = 512'hABCD0000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000001;
        axi_write(SRAM_BASE + 32'h0100, 8'd0, 2'b01, wdata_arr, resp);
        if (resp === 2'b00) begin
            $display("  PASS: Write 0x2000_0100 BRESP=OKAY");
        end else begin
            $display("  FAIL: Write 0x2000_0100 BRESP=%b (expected OKAY)", resp);
            tc_fail = tc_fail + 1;
            $display("");
        end

        // Read back from 0x2000_0100
        if (resp === 2'b00) begin
            axi_read(SRAM_BASE + 32'h0100, 8'd0, 2'b01, rdata_arr, resp);
            if (rdata_arr[0] === wdata_arr[0] && resp === 2'b00) begin
                $display("  PASS: Read 0x2000_0100 = %0h (matches)", rdata_arr[0]);
                tc_pass = tc_pass + 1;
            end else begin
                $display("  FAIL: Read 0x2000_0100 = %0h, resp=%b (expected %0h, OKAY)", rdata_arr[0], resp, wdata_arr[0]);
                tc_fail = tc_fail + 1;
            end
        end
        $display("");

        // =====================================================================
        // TC3: Burst-4 write → burst-4 read (AXI4 INCR, len=3 = 4 beats)
        // =====================================================================
        $display("--- TC3: Burst-4 write → burst-4 read ---");
        // Write 4 beats starting at 0x2000_0200 (increment by 64B each)
        wdata_arr = new[4];
        wdata_arr[0] = 512'h00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000001;
        wdata_arr[1] = 512'h00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000002;
        wdata_arr[2] = 512'h00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000003;
        wdata_arr[3] = 512'h00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000000_00000004;

        axi_write(SRAM_BASE + 32'h0200, 8'd3, 2'b01, wdata_arr, resp);
        if (resp === 2'b00)
            $display("  Write burst-4 at 0x2000_0200 BRESP=OKAY");
        else begin
            $display("  FAIL: Write burst-4 BRESP=%b", resp);
            tc_fail = tc_fail + 1;
            $display("");
        end

        // Read back burst-4 starting at 0x2000_0200
        if (resp === 2'b00) begin
            axi_read(SRAM_BASE + 32'h0200, 8'd3, 2'b01, rdata_arr, resp);
            for (i = 0; i < 4; i = i + 1) begin
                if (rdata_arr[i] === wdata_arr[i] && resp === 2'b00) begin
                    $display("  PASS: Beat[%0d] 0x2000_%04h = %0h (matches)", i, 32'h0200 + (i*64), rdata_arr[i]);
                end else begin
                    $display("  FAIL: Beat[%0d] 0x2000_%04h = %0h (expected %0h)", i, 32'h0200 + (i*64), rdata_arr[i], wdata_arr[i]);
                    tc_fail = tc_fail + 1;
                end
            end
            if (resp === 2'b00) begin
                $display("  Burst-4 write/read all beats correct");
                tc_pass = tc_pass + 1;
            end
        end
        $display("");

        // =====================================================================
        // TC4: Out-of-range address → DECERR
        // =====================================================================
        $display("--- TC4: Out-of-range → DECERR ---");
        // Write to 0x2040_0000 (beyond SRAM range)
        wdata_arr = new[1];
        wdata_arr[0] = 512'hFFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF_FFFF;
        axi_write(32'h2040_0000, 8'd0, 2'b01, wdata_arr, resp);
        if (resp === 2'b11) begin
            $display("  PASS: Write 0x2040_0000 BRESP=DECERR (2'b11)");
            tc_pass = tc_pass + 1;
        end else begin
            $display("  FAIL: Write 0x2040_0000 BRESP=%b (expected DECERR=2'b11)", resp);
            tc_fail = tc_fail + 1;
        end

        // Read from 0x2040_0000 (beyond SRAM range)
        axi_read(32'h2040_0000, 8'd0, 2'b01, rdata_arr, resp);
        if (resp === 2'b11) begin
            $display("  PASS: Read 0x2040_0000 RRESP=DECERR (2'b11)");
            tc_pass = tc_pass + 1;
        end else begin
            $display("  FAIL: Read 0x2040_0000 RRESP=%b (expected DECERR=2'b11)", resp);
            tc_fail = tc_fail + 1;
        end
        $display("");

        // =====================================================================
        // Summary
        // =====================================================================
        $display("============================================================");
        $display("[TB] Summary: %0d passed, %0d failed", tc_pass, tc_fail);
        if (tc_fail == 0) begin
            $display("PASS");
        end else begin
            $display("FAIL");
        end
        $display("============================================================");
        $finish;
    end

endmodule
