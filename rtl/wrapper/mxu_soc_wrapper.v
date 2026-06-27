//=============================================================================
// mxu_soc_wrapper — MXU SoC Integration Wrapper
//=============================================================================
// Task 5 of soc-phase3-4 Wave 2.
//
// Wraps mxu_top with:
//   • APB slave (MMIO via apb_to_mmio → mxu_top mmio_if) for register access
//   • AXI4 master (512-bit burst) for reading/writing SoC shared SRAM
//   • Internal broadcast bus sequencer: reads weight/activation tiles from
//     SRAM → deserializes → drives weight_bus_i / activation_bus_i during
//     compute; serializes acc_out_bus_o → writes back to SRAM.
//
// Internal SRAM addresses are offsets from 0x2000_0000 (SoC SRAM base).
//
// Additional MMIO registers (beyond mxu_top's native mmio_if at 0x00-0x28):
//   Offset  Name            Access  Description
//   0x30    WRP_WEIGHT_BASE RW      Weight tile base addr in SRAM [31:0]
//   0x34    WRP_ACT_BASE    RW      Activation tile base addr in SRAM [31:0]
//   0x38    WRP_OUT_BASE    RW      Output tile base addr in SRAM [31:0]
//   0x3C    WRP_CMD         W       [0]=TRIG_LOAD: start pre-load from SRAM
//   0x40    WRP_STATUS      R       [0]=LOAD_DONE: pre-load complete
//
// Usage flow:
//   1. DMA copies weight/activation data to SRAM
//   2. Write WRP_WEIGHT_BASE, WRP_ACT_BASE, WRP_OUT_BASE
//   3. Write WRP_CMD[0]=1 → wrapper reads data from SRAM into internal buffers
//   4. Poll WRP_STATUS[0] → 1
//   5. Write mxu MMIO (DIM0/DIM1/CTRL etc.)
//   6. Write CMD.START → controller runs, wrapper drives broadcast buses
//   7. Poll STATUS.DONE → read results from SRAM at WRP_OUT_BASE
//
// Must NOT modify mxu_top or any engine internals.
// Preserves native debug ports for per-IP unit test.
//=============================================================================

`timescale 1ns / 1ps

module mxu_soc_wrapper #(
    parameter integer AXI_ID_WIDTH   = 8,
    parameter integer AXI_ADDR_WIDTH = 32,
    parameter integer AXI_DATA_WIDTH = 512,
    // Max K-tile elements (0 = full 64 means 64 compute cycles)
    parameter integer K_TILE_MAX     = 64,
    // Weight buffer depth (K_TILE_MAX / 2 words of 512-bit)
    // Each 512-bit word = 2 weight_bus cycles (2 × 256-bit = 512-bit)
    parameter integer W_BUF_DEPTH    = 32,   // K_TILE_MAX/2
    // Activation buffer depth (K_TILE_MAX words of 512-bit)
    parameter integer A_BUF_DEPTH    = 64    // K_TILE_MAX
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
    // Write Address channel
    output wire [AXI_ID_WIDTH-1:0]    m_axi_awid,
    output wire [AXI_ADDR_WIDTH-1:0]  m_axi_awaddr,
    output wire [7:0]                 m_axi_awlen,
    output wire [2:0]                 m_axi_awsize,
    output wire [1:0]                 m_axi_awburst,
    output wire                       m_axi_awvalid,
    input  wire                       m_axi_awready,

    // Write Data channel
    output wire [AXI_DATA_WIDTH-1:0]  m_axi_wdata,
    output wire [AXI_DATA_WIDTH/8-1:0] m_axi_wstrb,
    output wire                       m_axi_wlast,
    output wire                       m_axi_wvalid,
    input  wire                       m_axi_wready,

    // Write Response channel
    input  wire [AXI_ID_WIDTH-1:0]    m_axi_bid,
    input  wire [1:0]                 m_axi_bresp,
    input  wire                       m_axi_bvalid,
    output wire                       m_axi_bready,

    // Read Address channel
    output wire [AXI_ID_WIDTH-1:0]    m_axi_arid,
    output wire [AXI_ADDR_WIDTH-1:0]  m_axi_araddr,
    output wire [7:0]                 m_axi_arlen,
    output wire [2:0]                 m_axi_arsize,
    output wire [1:0]                 m_axi_arburst,
    output wire                       m_axi_arvalid,
    input  wire                       m_axi_arready,

    // Read Data channel
    input  wire [AXI_ID_WIDTH-1:0]    m_axi_rid,
    input  wire [AXI_DATA_WIDTH-1:0]  m_axi_rdata,
    input  wire [1:0]                 m_axi_rresp,
    input  wire                       m_axi_rlast,
    input  wire                       m_axi_rvalid,
    output wire                       m_axi_rready,

    // ── Interrupt (to INTC) ────────────────────────────────────────────────
    output wire        irq,

    // ── Native debug ports (preserved for unit test) ───────────────────────
    output wire [3:0]  dbg_state,
    output wire        dbg_compute_en,
    output wire        dbg_weight_load,
    output wire        dbg_activation_load,
    output wire        dbg_store_out,
    output wire [5:0]  dbg_store_row,
    output wire [5:0]  dbg_compute_k,
    output wire [15:0] dbg_tiles_completed
);

    //=========================================================================
    // APB → MMIO bridge (for mxu_top base MMIO at offsets 0x00-0x28)
    //=========================================================================
    wire        mmio_cs, mmio_we;
    wire [11:0] mmio_addr;
    wire [31:0] mmio_wdata;

    // Separate wires for APB → MMIO and MMIO → APB paths to avoid
    // multiple-driver conflicts (mxu_top.rdata drives into apb_to_mmio.rdata,
    // apb_to_mmio.prdata drives the APB response mux).
    wire [31:0] mxu_mmio_rdata;       // mxu_top MMIO read data → apb_to_mmio
    wire [31:0] apb_mmio_prdata;      // apb_to_mmio PRDATA → APB response mux

    // Only route APB transactions at offsets < 0x30 to mxu_top MMIO
    wire apb_to_mxu_mmio = (paddr < 12'h030);

    apb_to_mmio u_apb_to_mmio (
        .clk    (clk),
        .rst_n  (rst_n),
        .psel   (psel && apb_to_mxu_mmio),
        .penable(penable && apb_to_mxu_mmio),
        .pwrite (pwrite),
        .paddr  (paddr),
        .pwdata (pwdata),
        .prdata (apb_mmio_prdata),
        .pready (),
        .pslverr(),
        .cs     (mmio_cs),
        .we     (mmio_we),
        .addr   (mmio_addr),
        .wdata  (mmio_wdata),
        .rdata  (mxu_mmio_rdata),
        .ready  ()
    );

    //=========================================================================
    // Wrapper-specific MMIO registers (offsets 0x30-0x40)
    //=========================================================================
    localparam [11:0] OFF_WRP_WEIGHT_BASE = 12'h030;
    localparam [11:0] OFF_WRP_ACT_BASE    = 12'h034;
    localparam [11:0] OFF_WRP_OUT_BASE    = 12'h038;
    localparam [11:0] OFF_WRP_CMD         = 12'h03C;
    localparam [11:0] OFF_WRP_STATUS      = 12'h040;

    reg [31:0] wrp_weight_base;
    reg [31:0] wrp_act_base;
    reg [31:0] wrp_out_base;
    reg        wrp_load_done;      // WRP_STATUS[0]

    wire       wrp_cs     = psel && (paddr >= 12'h030) && (paddr <= 12'h040);
    wire       wrp_trigger = wrp_cs && pwrite && penable && (paddr == OFF_WRP_CMD) && pwdata[0];

    // Wrapper register writes
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wrp_weight_base <= 32'h2000_0000;
            wrp_act_base    <= 32'h2000_1000;
            wrp_out_base    <= 32'h2000_2000;
        end else if (wrp_cs && pwrite) begin
            case (paddr)
                OFF_WRP_WEIGHT_BASE: wrp_weight_base <= pwdata;
                OFF_WRP_ACT_BASE:    wrp_act_base    <= pwdata;
                OFF_WRP_OUT_BASE:    wrp_out_base    <= pwdata;
                OFF_WRP_CMD:         ; // handled by wrp_trigger
                default: ;
            endcase
        end
    end

    // Wrapper register reads
    wire [31:0] wrp_prdata;
    assign wrp_prdata = (paddr == OFF_WRP_WEIGHT_BASE) ? wrp_weight_base :
                        (paddr == OFF_WRP_ACT_BASE)    ? wrp_act_base    :
                        (paddr == OFF_WRP_OUT_BASE)    ? wrp_out_base    :
                        (paddr == OFF_WRP_CMD)         ? 32'd0           :
                        (paddr == OFF_WRP_STATUS)      ? {31'd0, wrp_load_done} : 32'd0;

    //=========================================================================
    // APB response mux — combine mxu MMIO and wrapper MMIO
    //=========================================================================
    assign prdata  = apb_to_mxu_mmio ? apb_mmio_prdata : (wrp_cs ? wrp_prdata : 32'd0);
    assign pready  = 1'b1;
    assign pslverr = 1'b0;

    //=========================================================================
    // mxu_top instantiation
    //=========================================================================
    wire [31:0] mxu_sram_rdata;

    wire [11:0] mxu_w_sram_addr, mxu_a_sram_addr, mxu_o_sram_addr;
    wire        mxu_w_sram_wr_en, mxu_a_sram_wr_en, mxu_o_sram_wr_en;
    wire        mxu_w_sram_rd_en, mxu_a_sram_rd_en;
    wire [31:0] mxu_o_sram_wdata;

    wire [255:0]  mxu_weight_bus;
    wire [511:0]  mxu_activation_bus;
    wire [2047:0] mxu_acc_out_bus;

    // Tie off internal SRAM interfaces (unused in SoC mode — data comes
    // from AXI4 through the wrapper buffers)
    mxu_top #(
        .ADDR_WIDTH(12)
    ) u_mxu_top (
        .clk                 (clk),
        .rst_n               (rst_n),
        .cs                  (mmio_cs),
        .we                  (mmio_we),
        .addr                (mmio_addr),
        .wdata               (mmio_wdata),
        .rdata               (mxu_mmio_rdata),
        .ready               (),
        .sram_rdata          (mxu_sram_rdata),
        .weight_sram_addr    (mxu_w_sram_addr),
        .weight_sram_wr_en   (mxu_w_sram_wr_en),
        .weight_sram_rd_en   (mxu_w_sram_rd_en),
        .activation_sram_addr(mxu_a_sram_addr),
        .activation_sram_wr_en(mxu_a_sram_wr_en),
        .activation_sram_rd_en(mxu_a_sram_rd_en),
        .output_sram_addr    (mxu_o_sram_addr),
        .output_sram_wr_en   (mxu_o_sram_wr_en),
        .output_sram_wdata   (mxu_o_sram_wdata),
        .irq                 (irq),
        .weight_bus_i        (mxu_weight_bus),
        .activation_bus_i    (mxu_activation_bus),
        .acc_out_bus_o       (mxu_acc_out_bus),
        .state               (dbg_state),
        .compute_en_o        (dbg_compute_en),
        .weight_load_en_o    (dbg_weight_load),
        .activation_load_en_o(dbg_activation_load),
        .store_out_o         (dbg_store_out),
        .store_row_o         (dbg_store_row),
        .compute_k_o         (dbg_compute_k),
        .tiles_completed_o   (dbg_tiles_completed)
    );

    // Internal SRAM interfaces are tied off in SoC mode — the wrapper
    // feeds broadcast buses directly from its own AXI4-backed buffers.
    assign mxu_sram_rdata = 32'd0;

    //=========================================================================
    // Internal buffer arrays (register-based for simulation; infer BRAM
    // in synthesis via ram_style attribute)
    //=========================================================================
    (* ram_style = "block" *) reg [AXI_DATA_WIDTH-1:0] weight_buf     [0:W_BUF_DEPTH-1];
    (* ram_style = "block" *) reg [AXI_DATA_WIDTH-1:0] activation_buf [0:A_BUF_DEPTH-1];

    //=========================================================================
    // AXI4 Pre-load Sequencer FSM
    //=========================================================================
    // Reads weight and activation tiles from SoC SRAM into internal buffers
    // before compute starts.  The controller (mxu_top) does not wait for
    // external data — so pre-load must complete before CMD.START.

    localparam [3:0] PL_IDLE       = 4'd0;
    localparam [3:0] PL_LOAD_W_AR  = 4'd1;   // issue AR for weight burst
    localparam [3:0] PL_LOAD_W_R   = 4'd2;   // collect R beats for weight
    localparam [3:0] PL_LOAD_A_AR  = 4'd3;   // issue AR for activation burst
    localparam [3:0] PL_LOAD_A_R   = 4'd4;   // collect R beats for activation
    localparam [3:0] PL_READY      = 4'd5;   // pre-load complete

    reg [3:0]  pl_state;
    reg [7:0]  pl_beat_cnt;       // beat counter within burst
    reg [7:0]  pl_total_beats;    // total beats for current burst
    reg [31:0] pl_cur_addr;       // current burst start address

    // Compute beat counts from K-tile size
    // weight: K_TILE × 64 int4 / 2 (packed) / 64 bytes_per_beat = K_TILE / 2 beats
    // activation: K_TILE × 64 int8 / 64 bytes_per_beat = K_TILE beats
    wire [7:0] weight_beats;
    wire [7:0] act_beats;

    // Use MAX_K for full-tile pre-load; partial tiles handled by compute_k
    assign weight_beats = 8'd32;   // K=64 → 32 beats (packed weight)
    assign act_beats    = 8'd64;   // K=64 → 64 beats

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pl_state       <= PL_IDLE;
            pl_beat_cnt    <= 8'd0;
            pl_total_beats <= 8'd0;
            pl_cur_addr    <= 32'd0;
            wrp_load_done  <= 1'b0;
        end else begin
            case (pl_state)
                PL_IDLE: begin
                    wrp_load_done <= 1'b0;
                    if (wrp_trigger) begin
                        pl_state       <= PL_LOAD_W_AR;
                        pl_beat_cnt    <= 8'd0;
                        pl_total_beats <= weight_beats;
                        pl_cur_addr    <= wrp_weight_base;
                    end
                end

                PL_LOAD_W_AR: begin
                    if (m_axi_arvalid && m_axi_arready) begin
                        pl_state <= PL_LOAD_W_R;
                    end
                end

                PL_LOAD_W_R: begin
                    if (m_axi_rvalid && m_axi_rready) begin
                        weight_buf[pl_beat_cnt] <= m_axi_rdata;
                        pl_beat_cnt <= pl_beat_cnt + 8'd1;
                        if (m_axi_rlast) begin
                            pl_state       <= PL_LOAD_A_AR;
                            pl_beat_cnt    <= 8'd0;
                            pl_total_beats <= act_beats;
                            pl_cur_addr    <= wrp_act_base;
                        end
                    end
                end

                PL_LOAD_A_AR: begin
                    if (m_axi_arvalid && m_axi_arready) begin
                        pl_state <= PL_LOAD_A_R;
                    end
                end

                PL_LOAD_A_R: begin
                    if (m_axi_rvalid && m_axi_rready) begin
                        activation_buf[pl_beat_cnt] <= m_axi_rdata;
                        pl_beat_cnt <= pl_beat_cnt + 8'd1;
                        if (m_axi_rlast) begin
                            pl_state      <= PL_READY;
                            wrp_load_done <= 1'b1;
                        end
                    end
                end

                PL_READY: begin
                    // stay ready until next trigger
                    if (wrp_trigger) begin
                        wrp_load_done  <= 1'b0;
                        pl_state       <= PL_LOAD_W_AR;
                        pl_beat_cnt    <= 8'd0;
                        pl_total_beats <= weight_beats;
                        pl_cur_addr    <= wrp_weight_base;
                    end
                end

                default: pl_state <= PL_IDLE;
            endcase
        end
    end

    //=========================================================================
    // AXI4 Read Address channel (driven by pre-load sequencer)
    //=========================================================================
    wire pl_issuing_w_ar = (pl_state == PL_LOAD_W_AR);
    wire pl_issuing_a_ar = (pl_state == PL_LOAD_A_AR);

    assign m_axi_arid    = 8'h00;
    assign m_axi_araddr  = pl_issuing_w_ar ? pl_cur_addr :
                           pl_issuing_a_ar ? pl_cur_addr : 32'd0;
    assign m_axi_arlen   = (pl_issuing_w_ar ? (weight_beats - 8'd1) :
                            pl_issuing_a_ar ? (act_beats - 8'd1)    : 8'd0);
    assign m_axi_arsize  = 3'd6;    // 64 bytes per beat (2^6 = 64)
    assign m_axi_arburst = 2'd1;    // INCR
    assign m_axi_arvalid = pl_issuing_w_ar || pl_issuing_a_ar;
    assign m_axi_rready  = (pl_state == PL_LOAD_W_R) || (pl_state == PL_LOAD_A_R);

    //=========================================================================
    // Broadcast Bus Driver
    //=========================================================================
    // During compute, drive weight_bus_i and activation_bus_i from the
    // internal buffers, cycling through entries synchronized with
    // compute_en.  Each 512-bit weight_buf entry provides 2 × 256-bit
    // weight_bus cycles; each 512-bit activation_buf entry provides
    // 1 × 512-bit activation_bus cycle.

    reg [7:0]  comp_cycle;      // compute cycle counter (increments on compute_en)
    reg        comp_active;     // high during compute (latched from compute_en rising)
    reg        comp_active_d1;

    // Detect compute phase start
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            comp_active    <= 1'b0;
            comp_active_d1 <= 1'b0;
            comp_cycle     <= 8'd0;
        end else begin
            comp_active_d1 <= dbg_compute_en;

            if (!comp_active && dbg_compute_en && !comp_active_d1) begin
                // Rising edge of compute_en → start of compute phase
                comp_active <= 1'b1;
                comp_cycle  <= 8'd0;
            end else if (comp_active) begin
                if (dbg_compute_en) begin
                    // Each compute_en pulse = one K-element
                    comp_cycle <= comp_cycle + 8'd1;
                end else begin
                    // compute_en de-asserted → end of compute phase for this tile
                    comp_active <= 1'b0;
                end
            end
        end
    end

    // Drive broadcast buses from internal buffers
    // weight_buf[comp_cycle/2]: 512-bit → split into two 256-bit halves
    //   Even cycles: drive lower 256 bits
    //   Odd  cycles: drive upper 256 bits
    // activation_buf[comp_cycle]: drive directly (512-bit)
    wire [7:0] w_buf_idx = comp_cycle[7:1];       // comp_cycle / 2
    wire       w_use_hi  = comp_cycle[0];          // 1 = use upper half

    assign mxu_weight_bus = comp_active ?
        (w_use_hi ? weight_buf[w_buf_idx][511:256] : weight_buf[w_buf_idx][255:0]) :
        256'd0;

    assign mxu_activation_bus = comp_active ?
        activation_buf[comp_cycle] : 512'd0;

    //=========================================================================
    // AXI4 Store-Out Sequencer
    //=========================================================================
    // When the controller asserts store_out, latch acc_out_bus_o and write
    // it to SRAM via AXI4 write burst.  acc_out_bus_o is 2048 bits = 4 AXI4
    // beats (512-bit each).

    localparam [2:0] SO_IDLE     = 3'd0;
    localparam [2:0] SO_LATCH    = 3'd1;   // detect store_out rising edge
    localparam [2:0] SO_WRITE_AW = 3'd2;   // issue AW
    localparam [2:0] SO_WRITE_W  = 3'd3;   // issue W beats

    reg [2:0]    so_state;
    reg [2047:0] so_acc_data;       // latched acc_out_bus_o
    reg [5:0]    so_row;            // latched store_row
    reg          so_trigger;        // store_out rising edge detected
    reg          store_out_d1;
    reg [1:0]    so_w_beat;         // W beat index 0..3

    // Detect store_out rising edge
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            store_out_d1 <= 1'b0;
            so_trigger   <= 1'b0;
        end else begin
            store_out_d1 <= dbg_store_out;
            so_trigger   <= dbg_store_out && !store_out_d1;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            so_state    <= SO_IDLE;
            so_acc_data <= 2048'd0;
            so_row      <= 6'd0;
            so_w_beat   <= 2'd0;
        end else begin
            case (so_state)
                SO_IDLE: begin
                    if (so_trigger) begin
                        so_acc_data <= mxu_acc_out_bus;
                        so_row      <= dbg_store_row;
                        so_w_beat   <= 2'd0;
                        so_state    <= SO_WRITE_AW;
                    end
                end

                SO_WRITE_AW: begin
                    if (m_axi_awvalid && m_axi_awready)
                        so_state <= SO_WRITE_W;
                end

                SO_WRITE_W: begin
                    if (m_axi_wvalid && m_axi_wready) begin
                        if (m_axi_wlast)
                            so_state <= SO_IDLE;
                        else
                            so_w_beat <= so_w_beat + 2'd1;
                    end
                end

                default: so_state <= SO_IDLE;
            endcase
        end
    end

    // Store-out AXI4 AW channel
    // Address: wrp_out_base + (store_row × 64 × 4 bytes per row)
    // 64 elements × 4 bytes/elem = 256 bytes per row
    // Aligned to 512-bit (64-byte) boundary
    wire [31:0] so_row_offset = {24'd0, so_row, 8'd0};   // so_row × 256
    wire [31:0] so_base_addr  = wrp_out_base + so_row_offset;

    assign m_axi_awid    = 8'h01;
    assign m_axi_awaddr  = so_base_addr;
    assign m_axi_awlen   = 8'd3;    // 4 beats (0..3)
    assign m_axi_awsize  = 3'd6;    // 64 bytes per beat
    assign m_axi_awburst = 2'd1;    // INCR
    assign m_axi_awvalid = (so_state == SO_WRITE_AW);

    // Store-out AXI4 W channel
    assign m_axi_wdata  = so_acc_data[so_w_beat * 512 +: 512];
    assign m_axi_wstrb  = {AXI_DATA_WIDTH/8{1'b1}};
    assign m_axi_wlast  = (so_w_beat == 2'd3);
    assign m_axi_wvalid = (so_state == SO_WRITE_W);

    // Store-out AXI4 B channel (fire-and-forget — always ready)
    assign m_axi_bready = 1'b1;

endmodule
