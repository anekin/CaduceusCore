//=============================================================================
// sfu_soc_wrapper — SFU SoC Integration Wrapper
//=============================================================================
// Task 5 of soc-phase3-4 Wave 2.
//
// Wraps sfu_top with:
//   • APB slave (MMIO via apb_to_mmio → sfu_top mmio_if)
//   • AXI4 master (512-bit data) for SoC shared SRAM access
//   • 32-bit → 512-bit width converter:
//       Read:  64-byte cache-line prefetch (1 × 512-bit AXI read → 16 × 32-bit SFU reads)
//       Write: write-gathering buffer (16 × 32-bit SFU writes → 1 × 512-bit AXI write)
//
// sfu_top uses 32-bit SRAM ports (sram_rdata, sram_wdata).  The SoC SRAM
// controller uses 512-bit AXI4 channels.  This wrapper converts between
// the two widths by maintaining read/write line buffers.
//
// Must NOT modify sfu_top or any engine internals.
//=============================================================================

`timescale 1ns / 1ps

module sfu_soc_wrapper #(
    parameter integer AXI_ID_WIDTH   = 8,
    parameter integer AXI_ADDR_WIDTH = 32,
    parameter integer AXI_DATA_WIDTH = 512,
    parameter integer SFU_ADDR_WIDTH = 32
) (
    input  wire        clk,
    input  wire        rst_n,

    // ── APB slave (from apb_decoder) ───────────────────────────────────────
    input  wire        psel,
    input  wire        penable,
    input  wire        pwrite,
    input  wire [11:0] paddr,
    input  wire [31:0] pwdata,
    output wire [31:0] prdata,
    output wire        pready,
    output wire        pslverr,

    // ── AXI4 master (to crossbar → SRAM) ───────────────────────────────────
    output wire [AXI_ID_WIDTH-1:0]    m_axi_awid,
    output wire [AXI_ADDR_WIDTH-1:0]  m_axi_awaddr,
    output wire [7:0]                 m_axi_awlen,
    output wire [2:0]                 m_axi_awsize,
    output wire [1:0]                 m_axi_awburst,
    output wire                       m_axi_awvalid,
    input  wire                       m_axi_awready,

    output wire [AXI_DATA_WIDTH-1:0]  m_axi_wdata,
    output wire [AXI_DATA_WIDTH/8-1:0] m_axi_wstrb,
    output wire                       m_axi_wlast,
    output wire                       m_axi_wvalid,
    input  wire                       m_axi_wready,

    input  wire [AXI_ID_WIDTH-1:0]    m_axi_bid,
    input  wire [1:0]                 m_axi_bresp,
    input  wire                       m_axi_bvalid,
    output wire                       m_axi_bready,

    output wire [AXI_ID_WIDTH-1:0]    m_axi_arid,
    output wire [AXI_ADDR_WIDTH-1:0]  m_axi_araddr,
    output wire [7:0]                 m_axi_arlen,
    output wire [2:0]                 m_axi_arsize,
    output wire [1:0]                 m_axi_arburst,
    output wire                       m_axi_arvalid,
    input  wire                       m_axi_arready,

    input  wire [AXI_ID_WIDTH-1:0]    m_axi_rid,
    input  wire [AXI_DATA_WIDTH-1:0]  m_axi_rdata,
    input  wire [1:0]                 m_axi_rresp,
    input  wire                       m_axi_rlast,
    input  wire                       m_axi_rvalid,
    output wire                       m_axi_rready,

    // ── Interrupt (to INTC) ────────────────────────────────────────────────
    output wire        irq
);

    //=========================================================================
    // APB → MMIO bridge (sfu_top already has its own MMIO file)
    //=========================================================================
    wire        sfu_mmio_cs, sfu_mmio_we;
    wire [11:0] sfu_mmio_addr;
    wire [31:0] sfu_mmio_wdata, sfu_mmio_rdata;
    wire        sfu_mmio_ready;

    wire [31:0] apb_prdata;

    apb_to_mmio u_apb_to_mmio (
        .clk     (clk),
        .rst_n   (rst_n),
        .psel    (psel),
        .penable (penable),
        .pwrite  (pwrite),
        .paddr   (paddr),
        .pwdata  (pwdata),
        .prdata  (apb_prdata),
        .pready  (),
        .pslverr (),
        .cs      (sfu_mmio_cs),
        .we      (sfu_mmio_we),
        .addr    (sfu_mmio_addr),
        .wdata   (sfu_mmio_wdata),
        .rdata   (sfu_mmio_rdata),
        .ready   (sfu_mmio_ready)
    );

    //=========================================================================
    // sfu_top SRAM interface wires
    //=========================================================================
    wire [31:0]               sfu_rdata_to_top;
    wire [SFU_ADDR_WIDTH-1:0] sfu_raddr;
    wire                      sfu_ren;
    wire [31:0]               sfu_wdata_from_top;
    wire [SFU_ADDR_WIDTH-1:0] sfu_waddr;
    wire                      sfu_wen;

    //=========================================================================
    // Read line buffer — 64-byte double-buffered cache-line prefetch
    //=========================================================================
    // sfu_top reads sequentially within a 64-byte line (addr increments by 4).
    // A single line buffer cannot hide the 2-3 cycle AXI latency on the first
    // read of a new line, so the engine was consuming zero placeholders as
    // real data and corrupting the pipeline output.  We keep a current line
    // plus a prefetched next line.  When a read hits the current line and is
    // near the end of that line, we issue an AXI4 read for the following line.
    //
    // Additionally, we snoop APB writes to sfu_top's I_ADDR register (offset
    // 0x0C) and prefetch that line as soon as the software programs the input
    // base address.  This removes the initial-miss bubble before CMD.START.

    reg [AXI_DATA_WIDTH-1:0]   rd_line_buf;     // current line
    reg [AXI_ADDR_WIDTH-1:0]   rd_line_addr;
    reg                        rd_line_valid;

    reg [AXI_DATA_WIDTH-1:0]   rd_next_buf;     // prefetched next line
    reg [AXI_ADDR_WIDTH-1:0]   rd_next_addr;
    reg                        rd_next_valid;

    localparam [1:0] RD_IDLE    = 2'd0;
    localparam [1:0] RD_AR      = 2'd1;
    localparam [1:0] RD_R       = 2'd2;

    reg [1:0] rd_state;
    reg       rd_prefetch_next;                 // request in flight is for next line

    // Snoop APB I_ADDR writes so we can prefetch the first line early.
    localparam [11:0] SFU_I_ADDR_OFF = 12'h00C;
    localparam [11:0] SFU_CMD_OFF    = 12'h004;
    reg [AXI_ADDR_WIDTH-1:0]   apb_i_addr;
    wire apb_wr_i_addr = sfu_mmio_cs && sfu_mmio_we &&
                         (sfu_mmio_addr == SFU_I_ADDR_OFF);
    wire apb_wr_start  = sfu_mmio_cs && sfu_mmio_we &&
                         (sfu_mmio_addr == SFU_CMD_OFF) && sfu_mmio_wdata[0];

    wire cur_hit  = rd_line_valid && sfu_ren &&
                    ({sfu_raddr[31:6], 6'd0} == rd_line_addr);
    wire next_hit = rd_next_valid && sfu_ren &&
                    ({sfu_raddr[31:6], 6'd0} == rd_next_addr);

    wire rd_hit = cur_hit || next_hit;

    wire [31:0] cur_rdata  = rd_line_buf [sfu_raddr[5:2] * 32 +: 32];
    wire [31:0] next_rdata = rd_next_buf[sfu_raddr[5:2] * 32 +: 32];

    // Start prefetching the next line once we read word 10 or later in the
    // current line.  That leaves 6 cycles of margin before the engine reaches
    // the next 64-byte boundary, comfortably covering crossbar/DRAM latency.
    wire near_end = cur_hit && (sfu_raddr[5:2] >= 4'd10) &&
                    !rd_next_valid && !rd_prefetch_next &&
                    (rd_state == RD_IDLE);

    // On a next-line hit we swap the buffers so the prefetched line becomes
    // current and the old current line is discarded.
    wire do_swap = next_hit;

    // APB-triggered prefetch: fetch the line containing the just-written
    // I_ADDR into the current slot, unless it is already cached.
    // Use the data currently on the APB write bus when I_ADDR is being
    // written, so the prefetch starts for the new address in the same cycle.
    wire [AXI_ADDR_WIDTH-1:0] apb_i_addr_eff = apb_wr_i_addr ?
                                               sfu_mmio_wdata[AXI_ADDR_WIDTH-1:0] :
                                               apb_i_addr;
    wire [AXI_ADDR_WIDTH-1:0] apb_i_line = {apb_i_addr_eff[31:6], 6'd0};
    wire i_addr_cached = rd_line_valid && (rd_line_addr == apb_i_line);
    wire apb_prefetch  = apb_wr_i_addr && !i_addr_cached && (rd_state == RD_IDLE);

    // Hold the APB CMD.START write until the first line is in the cache.
    // sfu_top expects single-cycle SRAM read latency; we must hide the AXI
    // latency by having the data ready before the engine starts.
    reg start_hold;
    wire start_hold_set = apb_wr_start && !i_addr_cached;
    wire start_hold_clr = i_addr_cached;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_state          <= RD_IDLE;
            rd_line_buf       <= {AXI_DATA_WIDTH{1'b0}};
            rd_line_addr      <= {AXI_ADDR_WIDTH{1'b0}};
            rd_line_valid     <= 1'b0;
            rd_next_buf       <= {AXI_DATA_WIDTH{1'b0}};
            rd_next_addr      <= {AXI_ADDR_WIDTH{1'b0}};
            rd_next_valid     <= 1'b0;
            rd_prefetch_next  <= 1'b0;
            apb_i_addr        <= {AXI_ADDR_WIDTH{1'b0}};
            start_hold        <= 1'b0;
        end else begin
            // Capture the programmed input base address.
            if (apb_wr_i_addr)
                apb_i_addr <= sfu_mmio_wdata[AXI_ADDR_WIDTH-1:0];

            // Start-hold state machine: assert when software writes START
            // before the first line is cached; clear once the line arrives.
            if (start_hold_set)
                start_hold <= 1'b1;
            else if (start_hold_clr)
                start_hold <= 1'b0;

            // Swap next line into current line.  This also clears the next
            // line slot so a new prefetch can be triggered.
            if (do_swap) begin
                rd_line_buf      <= rd_next_buf;
                rd_line_addr     <= rd_next_addr;
                rd_line_valid    <= rd_next_valid;
                rd_next_buf      <= {AXI_DATA_WIDTH{1'b0}};
                rd_next_addr     <= {AXI_ADDR_WIDTH{1'b0}};
                rd_next_valid    <= 1'b0;
                rd_prefetch_next <= 1'b0;
            end

            case (rd_state)
                RD_IDLE: begin
                    if (apb_prefetch) begin
                        // Software just wrote I_ADDR: prefetch the first line.
                        rd_state         <= RD_AR;
                        rd_line_addr     <= apb_i_line;
                        rd_line_valid    <= 1'b0;
                        rd_next_valid    <= 1'b0;
                        rd_prefetch_next <= 1'b0;
                    end else if (sfu_ren && !rd_hit) begin
                        // Non-sequential miss: fetch the requested line into
                        // the current slot and discard any stale prefetch.
                        rd_state         <= RD_AR;
                        rd_line_addr     <= {sfu_raddr[31:6], 6'd0};
                        rd_line_valid    <= 1'b0;
                        rd_next_valid    <= 1'b0;
                        rd_prefetch_next <= 1'b0;
                    end else if (near_end && !do_swap) begin
                        // Sequential read approaching line boundary: prefetch
                        // the following line while the engine finishes this one.
                        rd_state         <= RD_AR;
                        rd_next_addr     <= {sfu_raddr[31:6] + 1'b1, 6'd0};
                        rd_prefetch_next <= 1'b1;
                    end
                end

                RD_AR: begin
                    if (m_axi_arvalid && m_axi_arready)
                        rd_state <= RD_R;
                end

                RD_R: begin
                    if (m_axi_rvalid && m_axi_rready) begin
                        if (rd_prefetch_next) begin
                            rd_next_buf   <= m_axi_rdata;
                            rd_next_valid <= 1'b1;
                        end else begin
                            rd_line_buf   <= m_axi_rdata;
                            rd_line_valid <= 1'b1;
                        end
                        rd_prefetch_next <= 1'b0;
                        rd_state         <= RD_IDLE;
                    end
                end

                default: rd_state <= RD_IDLE;
            endcase
        end
    end

    // AXI4 Read Address
    assign m_axi_arid    = 8'h10;
    assign m_axi_araddr  = (rd_state == RD_AR) ?
                           (rd_prefetch_next ? rd_next_addr : rd_line_addr) : 32'd0;
    assign m_axi_arlen   = 8'd0;
    assign m_axi_arsize  = 3'd6;
    assign m_axi_arburst = 2'd1;
    assign m_axi_arvalid = (rd_state == RD_AR);
    assign m_axi_rready  = (rd_state == RD_R);

    // Drive read data to sfu_top from whichever buffer hit.  Misses still
    // return 0, but sequential accesses should hit the prefetched next line.
    assign sfu_rdata_to_top = cur_hit  ? cur_rdata  :
                              next_hit ? next_rdata : 32'd0;

    //=========================================================================
    // Write path — FIFO + line buffer
    //=========================================================================
    // sfu_top issues one 32-bit write every ~2 cycles.  The AXI4 write burst
    // can take multiple cycles (AW/W handshakes + crossbar arbitration).  The
    // original line buffer dropped writes that arrived while it was in WR_ARB
    // or WR_DATA.  A small FIFO decouples the engine from AXI timing so no
    // writes are lost.
    //
    // FIFO entries are drained into a 64-byte line buffer.  When the line is
    // full, or when the next FIFO entry belongs to a different line, the line
    // is flushed as a single 512-bit AXI4 write.

    // 8192 entries: worst-case backlog for ROPE (2176 output words) plus
    // multiple AXI line-flush latencies.  The original 1024-entry depth
    // silently dropped writes for large SFU outputs (Issue SOC-009).
    localparam WR_FIFO_DEPTH = 8192;
    localparam WR_FIFO_AW    = $clog2(WR_FIFO_DEPTH);

    reg [63:0]                 wr_fifo_mem [0:WR_FIFO_DEPTH-1];
    reg [WR_FIFO_AW:0]         wr_fifo_wr_ptr;
    reg [WR_FIFO_AW:0]         wr_fifo_rd_ptr;

    wire                       wr_fifo_empty = (wr_fifo_wr_ptr == wr_fifo_rd_ptr);
    wire                       wr_fifo_full  =
        (wr_fifo_wr_ptr[WR_FIFO_AW] != wr_fifo_rd_ptr[WR_FIFO_AW]) &&
        (wr_fifo_wr_ptr[WR_FIFO_AW-1:0] == wr_fifo_rd_ptr[WR_FIFO_AW-1:0]);
    wire [WR_FIFO_AW-1:0]      wr_fifo_wr_idx = wr_fifo_wr_ptr[WR_FIFO_AW-1:0];
    wire [WR_FIFO_AW-1:0]      wr_fifo_rd_idx = wr_fifo_rd_ptr[WR_FIFO_AW-1:0];

    // Push every sfu_top write into the FIFO (depth is large enough to absorb
    // the AXI flush latency).
    always @(posedge clk) begin
        if (sfu_wen && !wr_fifo_full)
            wr_fifo_mem[wr_fifo_wr_idx] <= {sfu_waddr, sfu_wdata_from_top};
        if (sfu_wen && wr_fifo_full)
            $warning("[sfu_soc_wrapper] WRITE DROPPED: wr_fifo_full at addr %h data %h (time %t)",
                     sfu_waddr, sfu_wdata_from_top, $time);
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            wr_fifo_wr_ptr <= '0;
        else if (sfu_wen && !wr_fifo_full)
            wr_fifo_wr_ptr <= wr_fifo_wr_ptr + 1'b1;
    end

    // FIFO read side: entry at the head waiting to enter the line buffer
    wire [31:0]                fifo_waddr  = wr_fifo_mem[wr_fifo_rd_idx][63:32];
    wire [31:0]                fifo_wdata  = wr_fifo_mem[wr_fifo_rd_idx][31:0];
    wire [AXI_ADDR_WIDTH-1:0]  fifo_line   = {fifo_waddr[31:6], 6'd0};
    wire [3:0]                 fifo_word   = fifo_waddr[5:2];

    // Line buffer state
    reg [AXI_DATA_WIDTH-1:0]   wr_line_buf;
    reg [AXI_ADDR_WIDTH-1:0]   wr_line_addr;
    reg [63:0]                 wr_byte_strb;
    reg                        wr_line_dirty;

    localparam [1:0] WR_IDLE    = 2'd0;
    localparam [1:0] WR_ARB     = 2'd1;
    localparam [1:0] WR_DATA    = 2'd2;

    reg [1:0] wr_state;

    wire req_same_line = (wr_line_dirty == 1'b0) || (fifo_line == wr_line_addr);
    wire req_new_word  = (wr_byte_strb[fifo_word * 4 +: 4] == 4'h0);

    // Pop the FIFO when the line buffer accepts the entry into the current line.
    // When the FIFO head belongs to a different line, the line is flushed first
    // and the entry is applied (popped) in the next WR_IDLE cycle after
    // wr_line_dirty has been cleared.
    wire fifo_pop = (wr_state == WR_IDLE && !wr_fifo_empty && req_same_line);

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wr_state       <= WR_IDLE;
            wr_line_buf    <= {AXI_DATA_WIDTH{1'b0}};
            wr_line_addr   <= {AXI_ADDR_WIDTH{1'b0}};
            wr_byte_strb   <= 64'd0;
            wr_line_dirty  <= 1'b0;
            wr_fifo_rd_ptr <= '0;
        end else begin
            if (fifo_pop)
                wr_fifo_rd_ptr <= wr_fifo_rd_ptr + 1'b1;

            case (wr_state)
                WR_IDLE: begin
                    if (!wr_fifo_empty) begin
                        if (req_same_line) begin
                            // Allocate new line on first write
                            if (!wr_line_dirty) begin
                                wr_line_addr  <= fifo_line;
                                wr_line_dirty <= 1'b1;
                                wr_line_buf   <= {AXI_DATA_WIDTH{1'b0}};
                                wr_byte_strb  <= 64'd0;
                            end
                            wr_line_buf[fifo_word * 32 +: 32] <= fifo_wdata;
                            wr_byte_strb[fifo_word * 4 +: 4] <= 4'hF;

                            // Flush immediately when this entry fills the line
                            if (req_new_word &&
                                ((wr_byte_strb | ({60'd0, 4'hF} << (fifo_word * 4)))
                                 == 64'hFFFFFFFFFFFFFFFF)) begin
                                wr_state <= WR_ARB;
                            end
                        end else begin
                            // Next entry is for a different line: flush current line.
                            // Keep the FIFO entry; it will be applied after flush.
                            wr_state <= WR_ARB;
                        end
                    end
                end

                WR_ARB: begin
                    if (m_axi_awvalid && m_axi_awready)
                        wr_state <= WR_DATA;
                end

                WR_DATA: begin
                    if (m_axi_wvalid && m_axi_wready) begin
                        // Line flush complete
                        wr_state      <= WR_IDLE;
                        wr_line_buf   <= {AXI_DATA_WIDTH{1'b0}};
                        wr_byte_strb  <= 64'd0;
                        wr_line_dirty <= 1'b0;

                        // Apply the held FIFO entry that caused the different-line
                        // flush, if any.  At this point wr_line_dirty is cleared,
                        // so req_same_line is true and the entry allocates a new line
                        // in the next WR_IDLE cycle.
                    end
                end

                default: wr_state <= WR_IDLE;
            endcase
        end
    end

    // AXI4 Write Address
    assign m_axi_awid    = 8'h11;
    assign m_axi_awaddr  = wr_line_addr;
    assign m_axi_awlen   = 8'd0;
    assign m_axi_awsize  = 3'd6;
    assign m_axi_awburst = 2'd1;
    assign m_axi_awvalid = (wr_state == WR_ARB);

    // AXI4 Write Data
    assign m_axi_wdata  = wr_line_buf;
    assign m_axi_wstrb  = wr_byte_strb;
    assign m_axi_wlast  = 1'b1;
    assign m_axi_wvalid = (wr_state == WR_DATA);

    // AXI4 Write Response
    assign m_axi_bready = 1'b1;

    //=========================================================================
    // sfu_top instantiation
    //=========================================================================
    wire        sfu_irq;

    // Gate MMIO write-enable to sfu_top while we are holding CMD.START.
    // All other APB writes (including CTRL/I_ADDR/O_ADDR/DIM) pass through.
    wire sfu_mmio_we_gated = sfu_mmio_we && !start_hold;

    sfu_top #(
        .ADDR_WIDTH(SFU_ADDR_WIDTH)
    ) u_sfu_top (
        .clk         (clk),
        .rst_n       (rst_n),
        .mmio_cs     (sfu_mmio_cs),
        .mmio_we     (sfu_mmio_we_gated),
        .mmio_addr   (sfu_mmio_addr),
        .mmio_wdata  (sfu_mmio_wdata),
        .mmio_rdata  (sfu_mmio_rdata),
        .mmio_ready  (sfu_mmio_ready),
        .sram_rdata  (sfu_rdata_to_top),
        .sram_raddr  (sfu_raddr),
        .sram_ren    (sfu_ren),
        .sram_waddr  (sfu_waddr),
        .sram_wdata  (sfu_wdata_from_top),
        .sram_wen    (sfu_wen),
        .irq         (sfu_irq)
    );

    assign irq = sfu_irq;

    // APB response: insert wait states while holding START for prefetch.
    assign prdata  = apb_prdata;
    assign pready  = !start_hold;
    assign pslverr = 1'b0;

endmodule
