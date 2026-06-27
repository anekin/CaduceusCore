//=============================================================================
// dram_model.v — Behavioral DRAM Model (AXI4 Slave, LiteDRAM Fallback)
// CaduceusCore SoC Phase 3-4 / Task 9
//
// LiteDRAM decision (Task 0a): FALLBACK to behavioral.
// LiteDRAM is a Python/Migen framework generating 100+ Verilog files;
// this behavioral model provides equivalent AXI4 slave functionality for
// functional SoC verification.
//
// Features:
//   - AXI4 slave at 0x8000_0000 (2 GB addressable window)
//   - Sparse 8 MB storage: reg [511:0] mem [0:131071] (131,072 × 512-bit)
//   - Fixed DDR latency: tRC = 48 ns = 48 cycles @ 1 GHz (parameter LATENCY_CYCLES)
//   - Back-to-back read/write: pipelined AW→W→B and AR→R channels
//   - Out-of-range address → DECERR on RRESP/BRESP (AXI4 RESP = 2'b11)
//   - $readmemh("dram_init.hex", mem) for simulation initialization
//   - `ifdef USE_LITEDRAM skeleton for future full LiteDRAM core instantiation
//   - Single clock domain (clk), synchronous reset (rst_n)
//
// Usage:
//   vcs -full64 -sverilog rtl/ip/dram_model.v rtl/tb/dram_model_tb.sv \
//       -top dram_model_tb -o simv_dram
//   ./simv_dram
//=============================================================================

module dram_model #(
    parameter int unsigned DATA_WIDTH     = 512,
    parameter int unsigned ADDR_WIDTH     = 32,
    parameter int unsigned ID_WIDTH       = 8,
    parameter int unsigned MEM_DEPTH      = 131072,   // 8 MB / 64 B per word
    parameter int unsigned LATENCY_CYCLES = 48         // tRC @ 1 GHz (48 ns)
) (
    input  wire                     clk,
    input  wire                     rst_n,

    // AXI4 write address channel
    input  wire [ID_WIDTH-1:0]      s_axi_awid,
    input  wire [ADDR_WIDTH-1:0]    s_axi_awaddr,
    input  wire [7:0]               s_axi_awlen,
    input  wire [2:0]               s_axi_awsize,
    input  wire [1:0]               s_axi_awburst,
    input  wire                     s_axi_awvalid,
    output wire                     s_axi_awready,

    // AXI4 write data channel
    input  wire [DATA_WIDTH-1:0]    s_axi_wdata,
    input  wire [DATA_WIDTH/8-1:0]  s_axi_wstrb,
    input  wire                     s_axi_wlast,
    input  wire                     s_axi_wvalid,
    output wire                     s_axi_wready,

    // AXI4 write response channel
    output wire [ID_WIDTH-1:0]      s_axi_bid,
    output wire [1:0]               s_axi_bresp,
    output wire                     s_axi_bvalid,
    input  wire                     s_axi_bready,

    // AXI4 read address channel
    input  wire [ID_WIDTH-1:0]      s_axi_arid,
    input  wire [ADDR_WIDTH-1:0]    s_axi_araddr,
    input  wire [7:0]               s_axi_arlen,
    input  wire [2:0]               s_axi_arsize,
    input  wire [1:0]               s_axi_arburst,
    input  wire                     s_axi_arvalid,
    output wire                     s_axi_arready,

    // AXI4 read data channel
    output wire [ID_WIDTH-1:0]      s_axi_rid,
    output wire [DATA_WIDTH-1:0]    s_axi_rdata,
    output wire [1:0]               s_axi_rresp,
    output wire                     s_axi_rlast,
    output wire                     s_axi_rvalid,
    input  wire                     s_axi_rready
);

`ifdef USE_LITEDRAM
    // =========================================================================
    // Full LiteDRAM path (future, placeholder)
    // =========================================================================
    // When USE_LITEDRAM is defined, this module becomes a wrapper around
    // the vendored LiteDRAM core (enjoy-digital/litedram).
    //
    // Expected instantiation (requires litedram.flist + core Verilog):
    //   litedram_core #(
    //       .DATA_WIDTH(DATA_WIDTH),
    //       .ADDR_WIDTH(ADDR_WIDTH)
    //   ) u_litedram (
    //       .clk            (clk),
    //       .rst_n          (rst_n),
    //       .s_axi_awid     (s_axi_awid),
    //       .s_axi_awaddr   (s_axi_awaddr),
    //       // ... full AXI4 port map ...
    //   );
    //
    // Behavioral model below is NOT compiled when USE_LITEDRAM is defined.
    // The LiteDRAM core provides its own memory array, address decode, and
    // DDR PHY modeling (delay, refresh, etc.).
    //
    // Placeholder: currently routes to behavioral model below as fallback.
    // Remove the `else below when LiteDRAM is fully vendored.

    // For now, USE_LITEDRAM falls through to behavioral; add real
    // instantiation when LiteDRAM is vendored.
    initial begin
        $display("WARNING: USE_LITEDRAM defined but LiteDRAM core not vendored;");
        $display("         falling back to behavioral DRAM model.");
    end

`endif  // USE_LITEDRAM

    // =========================================================================
    // Behavioral model — constants
    // =========================================================================
    localparam logic [ADDR_WIDTH-1:0] DRAM_BASE  = 32'h8000_0000;
    localparam logic [ADDR_WIDTH-1:0] DRAM_MASK  = 32'h007F_FFFF;  // 8 MB - 1
    localparam int unsigned           ADDR_SHIFT = $clog2(DATA_WIDTH/8);  // 6

    // =========================================================================
    // Memory array
    // =========================================================================
    (* ram_style = "block" *) reg [DATA_WIDTH-1:0] mem [0:MEM_DEPTH-1];

    // =========================================================================
    // Address decode helpers
    // =========================================================================
    function automatic logic [31:0] addr_to_idx(logic [ADDR_WIDTH-1:0] byte_addr);
        addr_to_idx = (byte_addr - DRAM_BASE) >> ADDR_SHIFT;
    endfunction

    function automatic logic addr_valid(logic [ADDR_WIDTH-1:0] byte_addr);
        addr_valid = (byte_addr >= DRAM_BASE) &&
                     ((byte_addr - DRAM_BASE) <= DRAM_MASK);
    endfunction

    // =========================================================================
    // Write path (AXI4 AW → W → B)
    // =========================================================================
    // FSM: IDLE → AW_ACCEPTED → W_BEATS → LATENCY_WAIT → B_DRIVE → IDLE
    // Back-to-back: new AW accepted when previous B handshake completed.
    //
    // Latency: After the LAST data beat (wlast=1), the B response is delayed
    // by LATENCY_CYCLES to model DDR tRC (row cycle time).

    reg                   w_active;         // write transaction in progress
    reg [ID_WIDTH-1:0]    w_id;
    reg [ADDR_WIDTH-1:0]  w_start_addr;
    reg [7:0]             w_len;
    reg [7:0]             w_beat;           // current beat index (0..len)
    reg                   w_last_done;       // all data beats accepted
    reg [$clog2(LATENCY_CYCLES+1)-1:0] w_latency_cnt;  // latency countdown
    reg                   w_all_in_range;   // all beats within valid range

    wire w_aw_accepted = s_axi_awvalid && s_axi_awready;
    wire w_w_accepted  = s_axi_wvalid  && s_axi_wready;
    wire w_b_accepted  = s_axi_bvalid  && s_axi_bready;

    // AW ready: accept when idle, or when previous transaction's B is done
    assign s_axi_awready = !w_active || w_b_accepted;

    // W ready: accept data beats when active and within burst length
    assign s_axi_wready  = w_active && !w_last_done && (w_beat <= w_len);

    // Check validity of current write beat address
    wire [ADDR_WIDTH-1:0] w_beat_addr = w_start_addr + (w_beat << ADDR_SHIFT);

    // B channel
    assign s_axi_bvalid = w_active && w_last_done && (w_latency_cnt == 0);
    assign s_axi_bid    = w_id;
    assign s_axi_bresp  = w_all_in_range ? 2'b00 : 2'b11;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            w_active       <= 1'b0;
            w_id           <= '0;
            w_start_addr   <= '0;
            w_len          <= '0;
            w_beat         <= '0;
            w_last_done    <= 1'b0;
            w_latency_cnt  <= '0;
            w_all_in_range <= 1'b1;
        end else begin
            // ── Accept AW ─────────────────────────────────────────────────
            if (w_aw_accepted && !w_active) begin
                w_active       <= 1'b1;
                w_id           <= s_axi_awid;
                w_start_addr   <= s_axi_awaddr;
                w_len          <= s_axi_awlen;
                w_beat         <= '0;
                w_last_done    <= 1'b0;
                w_latency_cnt  <= '0;
                w_all_in_range <= addr_valid(s_axi_awaddr);
            end

            // ── Accept W beats ────────────────────────────────────────────
            if (w_active && !w_last_done && w_w_accepted) begin
                // Write data to memory if within range
                if (addr_valid(w_beat_addr))
                    mem[addr_to_idx(w_beat_addr)] <= s_axi_wdata;
                else
                    w_all_in_range <= 1'b0;

                // Check if this was the last beat
                if (s_axi_wlast || (w_beat == w_len)) begin
                    w_last_done   <= 1'b1;
                    w_latency_cnt <= LATENCY_CYCLES;  // start latency countdown
                end else begin
                    w_beat <= w_beat + 1;
                end
            end

            // ── Latency countdown (only after all beats done) ─────────────
            if (w_active && w_last_done && (w_latency_cnt > 0))
                w_latency_cnt <= w_latency_cnt - 1;

            // ── B handshake → transaction complete ────────────────────────
            if (w_b_accepted)
                w_active <= 1'b0;
        end
    end

    // =========================================================================
    // Read path (AXI4 AR → R)
    // =========================================================================
    // FSM: IDLE → AR_ACCEPTED → LATENCY_WAIT → R_BEATS → IDLE
    // Back-to-back: new AR accepted when previous R transaction done.
    //
    // Latency: Data is presented LATENCY_CYCLES after AR acceptance.
    // Between consecutive read beats within a burst, additional latency of
    // LATENCY_CYCLES is added to model DDR CAS-to-CAS timing.

    reg                   r_active;
    reg [ID_WIDTH-1:0]    r_id;
    reg [ADDR_WIDTH-1:0]  r_start_addr;
    reg [7:0]             r_len;
    reg [7:0]             r_beat;
    reg                   r_data_valid;     // set when latency expires
    reg [$clog2(LATENCY_CYCLES+1)-1:0] r_latency_cnt;

    wire r_ar_accepted = s_axi_arvalid && s_axi_arready;
    wire r_r_accepted  = s_axi_rvalid  && s_axi_rready;

    // AR ready: accept when idle, or when previous transaction's last R done
    assign s_axi_arready = !r_active || (r_r_accepted && (r_beat > r_len));

    // Current read beat address
    wire [ADDR_WIDTH-1:0] r_beat_addr = r_start_addr + (r_beat << ADDR_SHIFT);
    wire                  r_in_range  = addr_valid(r_beat_addr);

    // R channel
    assign s_axi_rvalid = r_active && r_data_valid && (r_beat <= r_len);
    assign s_axi_rid    = r_id;
    assign s_axi_rlast  = (r_beat == r_len);
    assign s_axi_rresp  = r_in_range ? 2'b00 : 2'b11;
    assign s_axi_rdata  = r_in_range ? mem[addr_to_idx(r_beat_addr)]
                                     : {DATA_WIDTH{1'b0}};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            r_active       <= 1'b0;
            r_id           <= '0;
            r_start_addr   <= '0;
            r_len          <= '0;
            r_beat         <= '0;
            r_data_valid   <= 1'b0;
            r_latency_cnt  <= '0;
        end else begin
            // ── Accept AR ─────────────────────────────────────────────────
            if (r_ar_accepted && !r_active) begin
                r_active       <= 1'b1;
                r_id           <= s_axi_arid;
                r_start_addr   <= s_axi_araddr;
                r_len          <= s_axi_arlen;
                r_beat         <= '0;
                r_data_valid   <= 1'b0;
                r_latency_cnt  <= LATENCY_CYCLES;  // start initial latency
            end

            // ── Latency countdown → data becomes valid ────────────────────
            if (r_active && !r_data_valid && (r_latency_cnt > 0)) begin
                r_latency_cnt <= r_latency_cnt - 1;
                if (r_latency_cnt == 1)
                    r_data_valid <= 1'b1;
            end

            // ── R handshake: advance to next beat ─────────────────────────
            if (r_active && r_r_accepted) begin
                if (r_beat < r_len) begin
                    // More beats: restart latency counter
                    r_beat         <= r_beat + 1;
                    r_data_valid   <= 1'b0;
                    r_latency_cnt  <= LATENCY_CYCLES;
                end else begin
                    // Last beat → transaction complete
                    r_active       <= 1'b0;
                    r_data_valid   <= 1'b0;
                end
            end
        end
    end

    // =========================================================================
    // Simulation initialization via $readmemh
    // =========================================================================
    // The testbench or simulator host can pre-load DRAM contents from a hex
    // file. This initial block loads dram_init.hex at time 0 (silently
    // ignores missing file).
    //
    // Each line in dram_init.hex: up to 128 hex chars (512-bit word).
    // Format: $readmemh standard — whitespace-separated hex values, one per
    // memory word. Comments start with // or /* */.

`ifdef SIMULATION
    initial begin
        $readmemh("dram_init.hex", mem);
    end
`endif

endmodule
