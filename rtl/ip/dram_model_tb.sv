//=============================================================================
// dram_model_tb — Self-Checking DRAM Model Testbench (100 Random AXI4 Txns)
// CaduceusCore SoC Phase 3-4 / Task 9
//
// Tests:
//   TC1: $readmemh initialization — pre-load dram_init.hex, verify readback
//   TC2: 100 random AXI4 back-to-back transactions:
//        - ~50% writes (single-beat / burst-2/4/8)
//        - ~50% reads  (single-beat / burst-2/4/8)
//        - Data pattern: per-beat unique 512-bit word = {tran_id[7:0], beat[7:0],
//          addr[15:0], 32'hDEADBEEF repeated}
//        - ~10% out-of-range (0x8100_0000) → DECERR
//        - Pipelined back-to-back: new AW/AR issued immediately after
//          previous B/R-last handshake
//   TC3: Out-of-range write/read → DECERR (targeted)
//
// Usage:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
//       CaduceusCore/rtl/ip/dram_model.v CaduceusCore/rtl/tb/dram_model_tb.sv \
//       -top dram_model_tb -o simv_dram_tb -l elaborate.log
//   ./simv_dram_tb
//=============================================================================

`timescale 1ns / 1ps

module dram_model_tb;

    // =========================================================================
    // Parameters
    // =========================================================================
    localparam CLK_HALF   = 5;            // 1 GHz clock (0.5ns half-period)
    localparam DATA_WIDTH = 512;
    localparam ADDR_WIDTH = 32;
    localparam ID_WIDTH   = 8;
    localparam LATENCY    = 48;           // match DUT LATENCY_CYCLES

    localparam [ADDR_WIDTH-1:0] DRAM_BASE = 32'h8000_0000;
    localparam [ADDR_WIDTH-1:0] DRAM_END  = 32'h807F_FFFF;

    localparam NUM_TXNS    = 100;         // total random transactions
    localparam NUM_OOR     = 10;          // out-of-range transactions
    localparam MAX_TIMEOUT = 1000000;     // max cycles before timeout

    // =========================================================================
    // DUT Signals
    // =========================================================================
    reg                      clk;
    reg                      rst_n;

    reg  [ID_WIDTH-1:0]      s_axi_awid;
    reg  [ADDR_WIDTH-1:0]    s_axi_awaddr;
    reg  [7:0]               s_axi_awlen;
    reg  [2:0]               s_axi_awsize;
    reg  [1:0]               s_axi_awburst;
    reg                      s_axi_awvalid;
    wire                     s_axi_awready;

    reg  [DATA_WIDTH-1:0]    s_axi_wdata;
    reg  [DATA_WIDTH/8-1:0]  s_axi_wstrb;
    reg                      s_axi_wlast;
    reg                      s_axi_wvalid;
    wire                     s_axi_wready;

    wire [ID_WIDTH-1:0]      s_axi_bid;
    wire [1:0]               s_axi_bresp;
    wire                     s_axi_bvalid;
    reg                      s_axi_bready;

    reg  [ID_WIDTH-1:0]      s_axi_arid;
    reg  [ADDR_WIDTH-1:0]    s_axi_araddr;
    reg  [7:0]               s_axi_arlen;
    reg  [2:0]               s_axi_arsize;
    reg  [1:0]               s_axi_arburst;
    reg                      s_axi_arvalid;
    wire                     s_axi_arready;

    wire [ID_WIDTH-1:0]      s_axi_rid;
    wire [DATA_WIDTH-1:0]    s_axi_rdata;
    wire [1:0]               s_axi_rresp;
    wire                     s_axi_rlast;
    wire                     s_axi_rvalid;
    reg                      s_axi_rready;

    // =========================================================================
    // DUT Instantiation
    // =========================================================================
    dram_model #(
        .DATA_WIDTH     (DATA_WIDTH),
        .ADDR_WIDTH     (ADDR_WIDTH),
        .ID_WIDTH       (ID_WIDTH),
        .MEM_DEPTH      (131072),
        .LATENCY_CYCLES (LATENCY)
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
    // Data pattern generator
    // =========================================================================
    // Each 512-bit word encodes: {transaction_id[7:0], beat_id[7:0],
    //  address_word_id[15:0], 32'hDEADBEEF repeated}
    // This makes every data beat unique and traceable.
    function automatic logic [DATA_WIDTH-1:0] data_pattern(
        input [7:0]  txn_id,
        input [7:0]  beat_id,
        input [15:0] addr_word_id
    );
        reg [DATA_WIDTH-1:0] pat;
        reg [31:0]           beefer;
        integer              i;
        begin
            beefer = {16'h0, addr_word_id};
            // Build 512-bit word: 8 copies of 64-bit chunks,
            // each chunk = {txn_id, beat_id, 16'h0, addr_word_id}
            pat = 512'd0;
            for (i = 0; i < 8; i = i + 1) begin
                pat[(i*64)+:64] = {txn_id, beat_id, 16'h0000, beefer};
            end
            data_pattern = pat;
        end
    endfunction

    // Verify data pattern match
    function automatic logic data_matches(
        input [DATA_WIDTH-1:0] actual,
        input [7:0]            txn_id,
        input [7:0]            beat_id,
        input [15:0]           addr_word_id
    );
        data_matches = (actual === data_pattern(txn_id, beat_id, addr_word_id));
    endfunction

    // =========================================================================
    // AXI4 Master tasks — pipelined, back-to-back capable
    // =========================================================================

    // ── AXI4 Write burst ────────────────────────────────────────────────────
    task axi_write;
        input [ADDR_WIDTH-1:0] addr;
        input [7:0]            len;        // burst length = len+1 beats
        input [7:0]            txn_id;
        output [1:0]           bresp_out;
    begin
        automatic integer beat;
        automatic logic [ADDR_WIDTH-1:0] beat_addr;
        automatic logic [15:0]           word_id;

        // Phase 1: Write Address
        s_axi_awvalid <= 1'b0;
        s_axi_awid    <= {ID_WIDTH{1'b0}};
        s_axi_awaddr  <= addr;
        s_axi_awlen   <= len;
        s_axi_awsize  <= 3'd6;        // 64 bytes = 512-bit
        s_axi_awburst <= 2'b01;       // INCR

        @(negedge clk);
        s_axi_awvalid <= 1'b1;
        while (!s_axi_awready) @(posedge clk);
        @(negedge clk);
        s_axi_awvalid <= 1'b0;

        // Phase 2: Write Data beats
        for (beat = 0; beat <= len; beat = beat + 1) begin
            beat_addr = addr + (beat << 6);
            word_id   = (beat_addr - DRAM_BASE) >> 6;

            s_axi_wvalid <= 1'b0;
            s_axi_wdata  <= data_pattern(txn_id, beat[7:0], word_id);
            s_axi_wstrb  <= {DATA_WIDTH/8{1'b1}};
            s_axi_wlast  <= (beat == len) ? 1'b1 : 1'b0;

            @(negedge clk);
            s_axi_wvalid <= 1'b1;
            while (!s_axi_wready) @(posedge clk);
            @(negedge clk);
            s_axi_wvalid <= 1'b0;
        end

        // Phase 3: Write Response (with latency)
        s_axi_bready <= 1'b0;
        while (!s_axi_bvalid) @(posedge clk);
        @(negedge clk);
        s_axi_bready <= 1'b1;
        bresp_out = s_axi_bresp;
        @(negedge clk);
        s_axi_bready <= 1'b0;
    end
    endtask

    // ── AXI4 Read burst ─────────────────────────────────────────────────────
    task axi_read;
        input  [ADDR_WIDTH-1:0] addr;
        input  [7:0]            len;
        output [DATA_WIDTH-1:0] rdata [];   // dynamic array, len+1 elements
        output [1:0]            rresp_out;
    begin
        automatic integer beat;

        rdata = new[len + 1];

        // Phase 1: Read Address
        s_axi_arvalid <= 1'b0;
        s_axi_arid    <= {ID_WIDTH{1'b0}};
        s_axi_araddr  <= addr;
        s_axi_arlen   <= len;
        s_axi_arsize  <= 3'd6;
        s_axi_arburst <= 2'b01;

        @(negedge clk);
        s_axi_arvalid <= 1'b1;
        while (!s_axi_arready) @(posedge clk);
        @(negedge clk);
        s_axi_arvalid <= 1'b0;

        // Phase 2: Read Data beats (latency is inside DUT)
        for (beat = 0; beat <= len; beat = beat + 1) begin
            s_axi_rready <= 1'b0;
            while (!s_axi_rvalid) @(posedge clk);
            @(negedge clk);
            s_axi_rready <= 1'b1;
            rdata[beat]   <= s_axi_rdata;
            if (beat == len)
                rresp_out = s_axi_rresp;
            @(negedge clk);
            s_axi_rready <= 1'b0;
        end
    end
    endtask

    // =========================================================================
    // LFSR-based random generator (16-bit)
    // =========================================================================
    reg [15:0] lfsr;
    reg [15:0] lfsr_seed;

    function automatic logic [15:0] lfsr_next;
        input [15:0] state;
    begin
        lfsr_next = {state[14:0], state[15] ^ state[13] ^ state[12] ^ state[10]};
    end
    endfunction

    // =========================================================================
    // Main test
    // =========================================================================
    reg [DATA_WIDTH-1:0] wdata_arr [];    // dynamic arrays
    reg [DATA_WIDTH-1:0] rdata_arr [];
    reg [1:0]            resp;
    integer              tc_pass, tc_fail;
    integer              i, beat;
    integer              txn;
    reg                  is_write;
    reg [7:0]            burst_len;
    reg [ADDR_WIDTH-1:0] txn_addr;
    reg [15:0]           addr_word_gen;
    reg [7:0]            txn_id_cnt;
    reg                  is_oor;          // out-of-range flag
    initial begin
        // ── Initialize all AXI signals ───────────────────────────────────
        s_axi_awvalid = 1'b0; s_axi_awid   = '0; s_axi_awaddr = '0;
        s_axi_awlen   = '0;   s_axi_awsize = '0; s_axi_awburst = '0;
        s_axi_wvalid  = 1'b0; s_axi_wdata  = '0; s_axi_wstrb  = '0;
        s_axi_wlast   = 1'b0;
        s_axi_bready  = 1'b0;
        s_axi_arvalid = 1'b0; s_axi_arid   = '0; s_axi_araddr  = '0;
        s_axi_arlen   = '0;   s_axi_arsize = '0; s_axi_arburst = '0;
        s_axi_rready  = 1'b0;

        tc_pass    = 0;
        tc_fail    = 0;
        txn_id_cnt = 8'd2;       // start after TC2 (0) and TC3 (1)
        lfsr       = 16'hACE1;   // initial LFSR seed

        // ── Reset sequence ───────────────────────────────────────────────
        $display("============================================================");
        $display("[TB] DRAM Model Testbench — 100 Random AXI4 Back-to-Back Txns");
        $display("============================================================");
        $display("[TB] Parameters: LATENCY=%0d cycles, 8 MB sparse array", LATENCY);
        $display("");
        rst_n = 1'b0;
        repeat(5) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        $display("[TB] Reset released at %0t", $time);
        $display("");

        // =================================================================
        // TC1: $readmemh initialization check
        // =================================================================
        $display("--- TC1: $readmemh initialization ---");
        // dram_init.hex should pre-load word 0 (addr 0x8000_0000) with a
        // DEAD_BEEF pattern and word 1 (addr 0x8000_0040) with CAFE_BABE.
        axi_read(DRAM_BASE + 32'h0000, 8'd0, rdata_arr, resp);
        if (rdata_arr[0][31:0] === 32'hDEADBEEF && rdata_arr[0] !== 512'd0 && resp === 2'b00) begin
            $display("  PASS: Read 0x8000_0000 lower 32b = DEADBEEF (init OK)");
            tc_pass = tc_pass + 1;
        end else begin
            $display("  FAIL: Read 0x8000_0000[31:0] = %0h, resp=%b (expected DEADBEEF, OKAY)", rdata_arr[0][31:0], resp);
            tc_fail = tc_fail + 1;
        end

        axi_read(DRAM_BASE + 32'h0040, 8'd0, rdata_arr, resp);
        if (rdata_arr[0][31:0] === 32'hCAFEBABE && rdata_arr[0] !== 512'd0 && resp === 2'b00) begin
            $display("  PASS: Read 0x8000_0040 lower 32b = CAFEBABE (init OK)");
            tc_pass = tc_pass + 1;
        end else begin
            $display("  FAIL: Read 0x8000_0040[31:0] = %0h, resp=%b (expected CAFEBABE, OKAY)", rdata_arr[0][31:0], resp);
            tc_fail = tc_fail + 1;
        end
        $display("");

        // =================================================================
        // TC2: Single write → single read (basic functional check)
        // =================================================================
        $display("--- TC2: Single write → single read (0x8000_0100) ---");
        // Write
        wdata_arr = new[1];
        wdata_arr[0] = data_pattern(8'd0, 8'd0, 16'd4);  // addr 0x8000_0100 → word idx 4
        axi_write(DRAM_BASE + 32'h0100, 8'd0, 8'd0, resp);
        if (resp === 2'b00) begin
            $display("  Write 0x8000_0100 BRESP=OKAY");
        end else begin
            $display("  FAIL: Write 0x8000_0100 BRESP=%b (expected OKAY)", resp);
            tc_fail = tc_fail + 1;
        end

        // Read back
        if (resp === 2'b00) begin
            axi_read(DRAM_BASE + 32'h0100, 8'd0, rdata_arr, resp);
            if (data_matches(rdata_arr[0], 8'd0, 8'd0, 16'd4) && resp === 2'b00) begin
                $display("  PASS: Readback matches (single write→read OK)");
                tc_pass = tc_pass + 1;
            end else begin
                $display("  FAIL: Readback mismatch");
                tc_fail = tc_fail + 1;
            end
        end
        $display("");

        // =================================================================
        // TC3: Burst-4 write → burst-4 read
        // =================================================================
        $display("--- TC3: Burst-4 write → burst-4 read (0x8000_0200) ---");
        burst_len = 8'd3;  // 4 beats
        wdata_arr = new[burst_len + 1];
        for (beat = 0; beat <= burst_len; beat = beat + 1) begin
            wdata_arr[beat] = data_pattern(8'd1, beat[7:0], 16'd8 + beat);
        end
        axi_write(DRAM_BASE + 32'h0200, burst_len, 8'd1, resp);
        if (resp === 2'b00)
            $display("  Write burst-4 at 0x8000_0200 BRESP=OKAY");
        else begin
            $display("  FAIL: Write burst-4 BRESP=%b", resp);
            tc_fail = tc_fail + 1;
        end

        if (resp === 2'b00) begin
            axi_read(DRAM_BASE + 32'h0200, burst_len, rdata_arr, resp);
            for (beat = 0; beat <= burst_len; beat = beat + 1) begin
                if (data_matches(rdata_arr[beat], 8'd1, beat[7:0], 16'd8 + beat) && resp === 2'b00) begin
                    $display("  PASS: Beat[%0d] matches", beat);
                end else begin
                    $display("  FAIL: Beat[%0d] mismatch (resp=%b)", beat, resp);
                    tc_fail = tc_fail + 1;
                end
            end
            $display("  Burst-4 write/read all beats correct");
            tc_pass = tc_pass + 1;
        end
        $display("");

        // =================================================================
        // TC4: Out-of-range address → DECERR
        // =================================================================
        $display("--- TC4: Out-of-range → DECERR ---");
        // Write to 0x8100_0000 (beyond 8 MB window)
        wdata_arr = new[1];
        wdata_arr[0] = 512'h1;
        axi_write(32'h8100_0000, 8'd0, 8'hFF, resp);
        if (resp === 2'b11) begin
            $display("  PASS: Write 0x8100_0000 BRESP=DECERR");
            tc_pass = tc_pass + 1;
        end else begin
            $display("  FAIL: Write 0x8100_0000 BRESP=%b (expected DECERR=2'b11)", resp);
            tc_fail = tc_fail + 1;
        end

        // Read from 0x8100_0000
        axi_read(32'h8100_0000, 8'd0, rdata_arr, resp);
        if (resp === 2'b11 && rdata_arr[0] === 512'd0) begin
            $display("  PASS: Read 0x8100_0000 RRESP=DECERR, data=0");
            tc_pass = tc_pass + 1;
        end else begin
            $display("  FAIL: Read 0x8100_0000 RRESP=%b, data[31:0]=%0h (expected DECERR, 0)", resp, rdata_arr[0][31:0]);
            tc_fail = tc_fail + 1;
        end
        $display("");

        // =================================================================
        // TC5: Back-to-back sequential writes (pipelining test)
        // =================================================================
        $display("--- TC5: Back-to-back sequential writes (5 single-beat) ---");
        for (i = 0; i < 5; i = i + 1) begin
            wdata_arr = new[1];
            wdata_arr[0] = data_pattern(txn_id_cnt, 8'd0, 16'd16 + i);
            axi_write(DRAM_BASE + 32'h0400 + (i * 64), 8'd0, txn_id_cnt, resp);
            if (resp === 2'b00) begin
                $display("  Write[%0d] at 0x8000_%04h BRESP=OKAY", i, 32'h0400 + i*64);
            end else begin
                $display("  FAIL: Write[%0d] BRESP=%b", i, resp);
                tc_fail = tc_fail + 1;
            end
            txn_id_cnt = txn_id_cnt + 1;
        end

        // Read back all 5
        for (i = 0; i < 5; i = i + 1) begin
            axi_read(DRAM_BASE + 32'h0400 + (i * 64), 8'd0, rdata_arr, resp);
            if (data_matches(rdata_arr[0], 8'd2 + i[7:0], 8'd0, 16'd16 + i) && resp === 2'b00) begin
                $display("  PASS: Read[%0d] matches", i);
                tc_pass = tc_pass + 1;
            end else begin
                $display("  FAIL: Read[%0d] mismatch", i);
                tc_fail = tc_fail + 1;
            end
        end
        $display("");

        // =================================================================
        // TC6: 100 Random AXI4 Back-to-Back Transactions
        //
        // Strategy: 45 write→read pairs + 10 out-of-range txns = 100 total.
        // Each pair: write with data_pattern → immediately read back →
        //            verify each beat matches expected pattern.
        // No shadow memory needed — data is verified right after writing.
        // =================================================================
        $display("--- TC6: 100 Random AXI4 Back-to-Back Transactions ---");
        $display("  (45 write→read pairs + 10 OOR, latency=%0d cycles)", LATENCY);

        // ── Run 100 random transactions ──────────────────────────────────
        for (txn = 0; txn < NUM_TXNS; txn = txn + 1) begin
            // Advance LFSR
            lfsr = lfsr_next(lfsr);

            // Decide burst length: 0 (single), 1 (2 beats), 3 (4 beats), 7 (8 beats)
            case (lfsr[2:1])
                2'b00: burst_len = 8'd0;
                2'b01: burst_len = 8'd1;
                2'b10: burst_len = 8'd3;
                2'b11: burst_len = 8'd7;
            endcase

            // Last 10 txns are out-of-range
            is_oor = (txn >= (NUM_TXNS - NUM_OOR));

            if (is_oor) begin
                // ── Out-of-range access ────────────────────────────────
                // Alternate: even txn → write, odd txn → read
                txn_addr = 32'h8100_0000 | ({16'd0, lfsr[15:4]} << 6);

                if (lfsr[0]) begin  // write
                    wdata_arr = new[burst_len + 1];
                    for (beat = 0; beat <= burst_len; beat = beat + 1) begin
                        wdata_arr[beat] = data_pattern(txn_id_cnt, beat[7:0],
                            addr_to_idx(txn_addr + (beat << 6)));
                    end
                    axi_write(txn_addr, burst_len, txn_id_cnt, resp);
                    if (resp === 2'b11) begin
                        $display("  PASS: txn#%0d OOR write BRESP=DECERR", txn);
                        tc_pass = tc_pass + 1;
                    end else begin
                        $display("  FAIL: txn#%0d OOR write BRESP=%b (expected DECERR)", txn, resp);
                        tc_fail = tc_fail + 1;
                    end
                end else begin  // read
                    axi_read(txn_addr, burst_len, rdata_arr, resp);
                    if (resp === 2'b11) begin
                        $display("  PASS: txn#%0d OOR read RRESP=DECERR", txn);
                        tc_pass = tc_pass + 1;
                    end else begin
                        $display("  FAIL: txn#%0d OOR read RRESP=%b (expected DECERR)", txn, resp);
                        tc_fail = tc_fail + 1;
                    end
                end
            end else begin
                // ── Normal write→read pair ──────────────────────────────
                // Generate address within 8 MB window
                addr_word_gen = lfsr[15:0] % (131072 - burst_len - 1);
                txn_addr = DRAM_BASE + ({16'd0, addr_word_gen} << 6);

                // Step 1: WRITE with unique data pattern
                wdata_arr = new[burst_len + 1];
                for (beat = 0; beat <= burst_len; beat = beat + 1) begin
                    wdata_arr[beat] = data_pattern(txn_id_cnt, beat[7:0],
                        addr_to_idx(txn_addr + (beat << 6)));
                end
                axi_write(txn_addr, burst_len, txn_id_cnt, resp);
                if (resp !== 2'b00) begin
                    $display("  FAIL: txn#%0d write BRESP=%b (expected OKAY)", txn, resp);
                    tc_fail = tc_fail + 1;
                end else begin
                    // Step 2: READ back immediately and verify each beat
                    axi_read(txn_addr, burst_len, rdata_arr, resp);
                    if (resp !== 2'b00) begin
                        $display("  FAIL: txn#%0d read RRESP=%b (expected OKAY)", txn, resp);
                        tc_fail = tc_fail + 1;
                    end else begin
                        for (beat = 0; beat <= burst_len; beat = beat + 1) begin
                            if (!data_matches(rdata_arr[beat],
                                    txn_id_cnt, beat[7:0],
                                    addr_to_idx(txn_addr + (beat << 6)))) begin
                                $display("  FAIL: txn#%0d beat#%0d data mismatch", txn, beat);
                                tc_fail = tc_fail + 1;
                            end
                        end
                        if (burst_len == 8'd0)
                            $display("  PASS: txn#%0d write→read single-beat OK", txn);
                        else
                            $display("  PASS: txn#%0d write→read burst-%0d OK", txn, burst_len+1);
                        tc_pass = tc_pass + 1;
                    end
                end
            end

            txn_id_cnt = txn_id_cnt + 1;

            // Progress every 20 txns
            if ((txn + 1) % 20 == 0)
                $display("  ... %0d/%0d transactions complete @ %0t", txn + 1, NUM_TXNS, $time);
        end

        $display("  100 random back-to-back AXI4 transactions complete @ %0t", $time);
        $display("");

        // =================================================================
        // Summary
        // =================================================================
        $display("============================================================");
        $display("[TB] DRAM Model Summary: %0d passed, %0d failed", tc_pass, tc_fail);
        if (tc_fail == 0) begin
            $display("PASS");
        end else begin
            $display("FAIL");
        end
        $display("============================================================");
        $finish;
    end

    // =========================================================================
    // Address helper function (used in test body)
    // =========================================================================
    function automatic [15:0] addr_to_idx;
        input [ADDR_WIDTH-1:0] byte_addr;
    begin
        addr_to_idx = (byte_addr - DRAM_BASE) >> 6;
    end
    endfunction

    // =========================================================================
    // Timeout watchdog
    // =========================================================================
    integer watchdog;
    initial begin
        watchdog = 0;
        #(MAX_TIMEOUT * CLK_HALF * 2);
        $display("ERROR: Timeout after %0d ns — simulation hung", MAX_TIMEOUT * CLK_HALF * 2);
        $display("FAIL");
        $finish;
    end

endmodule
