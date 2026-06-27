//=============================================================================
// sram_ctrl.v — 4 MB SRAM Controller (AXI4 Slave, 512-bit Dual-Port)
// CaduceusCore SoC Phase 3-4 / Task 2
//
// Features:
//   - AXI4 slave (aw/w/b/ar/r 5 channels), 512-bit data width
//   - 4 MB = 65,536 words × 512-bit (65,536 × 64 B)
//   - Dual-port: 1 read + 1 write per cycle (independent read/write FSMs)
//   - Handles AXI4 INCR and WRAP burst types
//   - Address: 0x2000_0000 ~ 0x203F_FFFF, out-of-range → DECERR (BRESP/RRESP=2'b11)
//   - $readmemh("sram_init.hex", mem) for simulation initialization
//   - Single clock domain (clk), synchronous reset (rst_n)
//
// Acceptance criteria:
//   - VCS compiles cleanly
//   - Single write → read correct
//   - Burst-4 write → burst-4 read all correct
//   - addr=0x2040_0000 → DECERR on RRESP
//=============================================================================

module sram_ctrl #(
    parameter int unsigned DATA_WIDTH = 512,
    parameter int unsigned ADDR_WIDTH = 32,
    parameter int unsigned ID_WIDTH   = 8
) (
    input  wire                     clk,
    input  wire                     rst_n,

    // ── AXI4 Write Address channel ──────────────────────────────────────────
    input  wire [ID_WIDTH-1:0]      s_axi_awid,
    input  wire [ADDR_WIDTH-1:0]    s_axi_awaddr,
    input  wire [7:0]               s_axi_awlen,
    input  wire [2:0]               s_axi_awsize,
    input  wire [1:0]               s_axi_awburst,
    input  wire                     s_axi_awvalid,
    output wire                     s_axi_awready,

    // ── AXI4 Write Data channel ─────────────────────────────────────────────
    input  wire [DATA_WIDTH-1:0]    s_axi_wdata,
    input  wire [DATA_WIDTH/8-1:0]  s_axi_wstrb,
    input  wire                     s_axi_wlast,
    input  wire                     s_axi_wvalid,
    output wire                     s_axi_wready,

    // ── AXI4 Write Response channel ─────────────────────────────────────────
    output wire [ID_WIDTH-1:0]      s_axi_bid,
    output wire [1:0]               s_axi_bresp,
    output wire                     s_axi_bvalid,
    input  wire                     s_axi_bready,

    // ── AXI4 Read Address channel ───────────────────────────────────────────
    input  wire [ID_WIDTH-1:0]      s_axi_arid,
    input  wire [ADDR_WIDTH-1:0]    s_axi_araddr,
    input  wire [7:0]               s_axi_arlen,
    input  wire [2:0]               s_axi_arsize,
    input  wire [1:0]               s_axi_arburst,
    input  wire                     s_axi_arvalid,
    output wire                     s_axi_arready,

    // ── AXI4 Read Data channel ──────────────────────────────────────────────
    output wire [ID_WIDTH-1:0]      s_axi_rid,
    output wire [DATA_WIDTH-1:0]    s_axi_rdata,
    output wire [1:0]               s_axi_rresp,
    output wire                     s_axi_rlast,
    output wire                     s_axi_rvalid,
    input  wire                     s_axi_rready
);

    // =========================================================================
    // Constants
    // =========================================================================
    // 4 MB SRAM = 65,536 words × 512-bit
    // Word size = DATA_WIDTH/8 = 64 bytes → address shift = log2(64) = 6
    localparam int unsigned           MEM_DEPTH  = 65536;        // 4 MB / 64 B
    localparam logic [ADDR_WIDTH-1:0] SRAM_BASE  = 32'h2000_0000;
    localparam logic [ADDR_WIDTH-1:0] SRAM_MASK  = 32'h003F_FFFF; // 4 MB - 1
    localparam int unsigned           ADDR_SHIFT = $clog2(DATA_WIDTH/8);  // 6

    // =========================================================================
    // Memory array (dual-port: combinational read, synchronous write)
    // =========================================================================
    (* ram_style = "block" *) reg [DATA_WIDTH-1:0] mem [0:MEM_DEPTH-1];

    // =========================================================================
    // Address decode functions
    // =========================================================================

    // Convert byte address → memory word index (0..65535)
    function automatic logic [15:0] addr_to_idx(logic [ADDR_WIDTH-1:0] byte_addr);
        addr_to_idx = (byte_addr - SRAM_BASE) >> ADDR_SHIFT;
    endfunction

    // Check if byte address is within SRAM range 0x2000_0000~0x203F_FFFF
    function automatic logic addr_in_range(logic [ADDR_WIDTH-1:0] byte_addr);
        addr_in_range = (byte_addr >= SRAM_BASE) &&
                         ((byte_addr - SRAM_BASE) <= SRAM_MASK);
    endfunction

    // =========================================================================
    // Burst address calculator — computes beat address for INCR and WRAP
    // =========================================================================
    // Per AXI4 spec:
    //   INCR:  addr_N = addr_0 + N × Transfer_Size
    //   WRAP:  addr_N = Wrap_Boundary(addr_0 + N × Transfer_Size, Total_Bytes)
    //          Total_Bytes = (AWLEN+1) × Transfer_Size
    //          Wrap_Boundary = addr - (addr % Total_Bytes)  for lower bound;
    //                          addr + (Total_Bytes - (addr % Total_Bytes)) for upper
    // For AWSIZE=6 (64 B per beat), Transfer_Size = 64.
    function automatic logic [ADDR_WIDTH-1:0] burst_addr(
        logic [ADDR_WIDTH-1:0] start_addr,
        logic [7:0]            beat_idx,
        logic [2:0]            size,
        logic [1:0]            burst
    );
        reg [ADDR_WIDTH-1:0] offset;
        reg [ADDR_WIDTH-1:0] total_bytes;
        reg [ADDR_WIDTH-1:0] wrap_mask;
        reg [ADDR_WIDTH-1:0] raw_addr;
        begin
            offset = beat_idx << ADDR_SHIFT;  // beat_idx × 64 (for AWSIZE=6)
            raw_addr = start_addr + offset;

            if (burst == 2'b01) begin  // INCR
                burst_addr = raw_addr;
            end else if (burst == 2'b10) begin  // WRAP
                // Total bytes = (AWLEN+1) × transfer_size
                // For AWSIZE=6, transfer_size = 64
                // But we need the total_bytes from the AWLEN context
                // Since we know size is fixed at 64B for this slave,
                // wrap boundary = next power-of-2 ≥ total_bytes.
                // We compute wrap_mask from total_bytes at the caller.
                // For now, use the raw addr; wrap is handled by masking at caller.
                burst_addr = raw_addr;
            end else begin  // FIXED (2'b00) or reserved
                burst_addr = start_addr;  // FIXED: all beats to same address
            end
        end
    endfunction

    // Compute wrap mask for WRAP burst
    // Total bytes = (len+1) × 64 (since size is always 64B for 512-bit)
    // Wrap boundary is aligned to next power-of-2 ≥ total_bytes
    function automatic logic [ADDR_WIDTH-1:0] wrap_mask(
        logic [7:0] awlen
    );
        reg [31:0] total_bytes;
        reg [31:0] mask;
        begin
            total_bytes = ({24'd0, awlen} + 32'd1) << ADDR_SHIFT;
            // Find next power of 2 ≥ total_bytes, then subtract 1 for mask
            mask = total_bytes - 1;
            mask = mask | (mask >> 1);
            mask = mask | (mask >> 2);
            mask = mask | (mask >> 4);
            mask = mask | (mask >> 8);
            mask = mask | (mask >> 16);
            wrap_mask = mask;  // e.g. for total=256: mask=255
        end
    endfunction

    // Apply wrap: addr = (start & ~wrap_mask) | ((start + offset) & wrap_mask)
    function automatic logic [ADDR_WIDTH-1:0] wrap_addr(
        logic [ADDR_WIDTH-1:0] start_addr,
        logic [ADDR_WIDTH-1:0] offset,
        logic [ADDR_WIDTH-1:0] wmask
    );
        begin
            wrap_addr = (start_addr & ~wmask) | ((start_addr + offset) & wmask);
        end
    endfunction

    // =========================================================================
    // Write path FSM (AW → W → B) — independent from read path
    // =========================================================================
    // States: IDLE → accept AW → collect W beats → drive B → IDLE

    reg                   w_active;
    reg [ID_WIDTH-1:0]    w_id;
    reg [ADDR_WIDTH-1:0]  w_start_addr;
    reg [7:0]             w_len;
    reg [2:0]             w_size;
    reg [1:0]             w_burst;
    reg [7:0]             w_beat;
    reg                   w_all_in_range;    // latch: are all beats valid?
    reg [ADDR_WIDTH-1:0]  w_wrap_mask_val;

    wire w_aw_accepted = s_axi_awvalid && s_axi_awready;
    wire w_w_accepted  = s_axi_wvalid  && s_axi_wready;
    wire w_b_accepted  = s_axi_bvalid  && s_axi_bready;

    // AW ready: accept when not currently in a write transaction
    assign s_axi_awready = !w_active;

    // W ready: accept when active and within burst length
    assign s_axi_wready  = w_active && (w_beat <= w_len);

    // W data beat → calculate address and write to memory
    wire [ADDR_WIDTH-1:0] w_beat_offset = w_beat << ADDR_SHIFT;
    wire [ADDR_WIDTH-1:0] w_beat_addr_raw = burst_addr(w_start_addr, w_beat, w_size, w_burst);
    wire [ADDR_WIDTH-1:0] w_beat_addr_wrap = wrap_addr(w_start_addr, w_beat_offset, w_wrap_mask_val);
    wire [ADDR_WIDTH-1:0] w_beat_addr =
        (w_burst == 2'b10) ? w_beat_addr_wrap : w_beat_addr_raw;
    wire                  w_beat_valid = addr_in_range(w_beat_addr);

    // =========================================================================
    // Read path FSM (AR → R) — independent from write path
    // =========================================================================
    // States: IDLE → accept AR → drive R beats → IDLE
    // 1-cycle read latency: address captured cycle N, data valid cycle N+1

    reg                   r_active;
    reg [ID_WIDTH-1:0]    r_id;
    reg [ADDR_WIDTH-1:0]  r_start_addr;
    reg [7:0]             r_len;
    reg [2:0]             r_size;
    reg [1:0]             r_burst;
    reg [7:0]             r_beat;
    reg [ADDR_WIDTH-1:0]  r_wrap_mask_val;
    reg                   r_data_pending;    // flag: first data beat ready next cycle

    wire r_ar_accepted = s_axi_arvalid && s_axi_arready;
    wire r_r_accepted  = s_axi_rvalid  && s_axi_rready;

    // AR ready: accept when not currently in a read transaction
    assign s_axi_arready = !r_active;

    // Calculate current read beat address
    wire [ADDR_WIDTH-1:0] r_beat_offset = r_beat << ADDR_SHIFT;
    wire [ADDR_WIDTH-1:0] r_beat_addr_raw = burst_addr(r_start_addr, r_beat, r_size, r_burst);
    wire [ADDR_WIDTH-1:0] r_beat_addr_wrap = wrap_addr(r_start_addr, r_beat_offset, r_wrap_mask_val);
    wire [ADDR_WIDTH-1:0] r_beat_addr =
        (r_burst == 2'b10) ? r_beat_addr_wrap : r_beat_addr_raw;
    wire                  r_beat_valid = addr_in_range(r_beat_addr);

    // R channel: data presented 1 cycle after AR acceptance
    assign s_axi_rvalid = r_active && r_data_pending && (r_beat <= r_len);
    assign s_axi_rid    = r_id;
    assign s_axi_rlast  = (r_beat == r_len);
    assign s_axi_rresp  = r_beat_valid ? 2'b00 : 2'b11;  // OKAY or DECERR
    assign s_axi_rdata  = r_beat_valid ? mem[addr_to_idx(r_beat_addr)] : {DATA_WIDTH{1'b0}};

    // =========================================================================
    // Write path sequential logic
    // =========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            w_active        <= 1'b0;
            w_id            <= '0;
            w_start_addr    <= '0;
            w_len           <= '0;
            w_size          <= '0;
            w_burst         <= '0;
            w_beat          <= '0;
            w_all_in_range  <= 1'b1;
            w_wrap_mask_val <= '0;
        end else begin
            // ── Accept new write address (AW channel) ───────────────────────
            if (w_aw_accepted && !w_active) begin
                w_active        <= 1'b1;
                w_id            <= s_axi_awid;
                w_start_addr    <= s_axi_awaddr;
                w_len           <= s_axi_awlen;
                w_size          <= s_axi_awsize;
                w_burst         <= s_axi_awburst;
                w_beat          <= '0;
                w_wrap_mask_val <= wrap_mask(s_axi_awlen);
                // Pre-check: is the start address in range?
                w_all_in_range  <= addr_in_range(s_axi_awaddr);
            end

            // ── Accept write data beats (W channel) ─────────────────────────
            if (w_active && w_w_accepted) begin
                // Write data to memory if within range
                if (addr_in_range(w_beat_addr))
                    mem[addr_to_idx(w_beat_addr)] <= s_axi_wdata;
                // Track if any beat goes out of range
                if (!addr_in_range(w_beat_addr))
                    w_all_in_range <= 1'b0;
                w_beat <= w_beat + 1;
            end

            // ── Clear on B handshake ────────────────────────────────────────
            if (w_b_accepted)
                w_active <= 1'b0;
        end
    end

    // B channel: respond after last data beat completed
    assign s_axi_bvalid = w_active && (w_beat > w_len);
    assign s_axi_bid    = w_id;
    assign s_axi_bresp  = w_all_in_range ? 2'b00 : 2'b11;  // OKAY or DECERR

    // =========================================================================
    // Read path sequential logic
    // =========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            r_active        <= 1'b0;
            r_id            <= '0;
            r_start_addr    <= '0;
            r_len           <= '0;
            r_size          <= '0;
            r_burst         <= '0;
            r_beat          <= '0;
            r_wrap_mask_val <= '0;
            r_data_pending  <= 1'b0;
        end else begin
            // ── Accept new read address (AR channel) ────────────────────────
            if (r_ar_accepted && !r_active) begin
                r_active        <= 1'b1;
                r_id            <= s_axi_arid;
                r_start_addr    <= s_axi_araddr;
                r_len           <= s_axi_arlen;
                r_size          <= s_axi_arsize;
                r_burst         <= s_axi_arburst;
                r_beat          <= '0;
                r_wrap_mask_val <= wrap_mask(s_axi_arlen);
                r_data_pending  <= 1'b1;  // first data beat ready next cycle
            end

            // ── Accept read data handshake (R channel) ──────────────────────
            if (r_active && r_r_accepted) begin
                if (r_beat < r_len) begin
                    // More beats to transfer
                    r_beat <= r_beat + 1;
                end else begin
                    // Last beat → transaction complete
                    r_active <= 1'b0;
                    r_data_pending <= 1'b0;
                end
            end
        end
    end

    // =========================================================================
    // Simulation initialization via $readmemh
    // =========================================================================
    // Usage in testbench:
    //   initial $readmemh("sram_init.hex", tb_sram_ctrl.u_dut.mem);
    // Or can be invoked directly in this module for standalone init:
    //   initial $readmemh("sram_init.hex", mem);

`ifdef SIMULATION
    initial begin
        // Load init file if present; $readmemh silently ignores missing files
        $readmemh("sram_init.hex", mem);
    end
`endif

endmodule
