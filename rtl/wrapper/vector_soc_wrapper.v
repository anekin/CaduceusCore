//=============================================================================
// vector_soc_wrapper — Vector Engine SoC Integration Wrapper
//=============================================================================
// Task 5 of soc-phase3-4 Wave 2.
//
// Wraps vector_top with:
//   • APB slave (MMIO via apb_to_mmio → vector_top mmio_if)
//   • AXI4 master (512-bit) for SoC shared SRAM access
//   • 4096-bit → 512-bit width adapter:
//       Internal pre-fetch buffers: AXI4 reads (8-beat sequential burst)
//       fill 4096-bit buffers before vector_top starts execution.
//       After compute: 4096-bit buffers are written back via AXI4 (8-beat burst).
//
// vector_top's internal FSM reads/writes SRAM in 1 cycle (ST_READ→ST_LATCH,
// ST_BIN_WRITE).  AXI4 burst latency (10+ cycles for 8 beats) requires
// data to be pre-loaded before execution.  The wrapper provides internal
// register-file buffers that are filled from / flushed to AXI4.
//
// Additional MMIO registers (beyond vector_top's native mmio at 0x00-0x1C):
//   Offset  Name            Access  Description
//   0x30    WRP_A_BASE      RW      Operand A base addr in SRAM [31:0]
//   0x34    WRP_B_BASE      RW      Operand B base addr in SRAM [31:0]
//   0x38    WRP_O_BASE      RW      Output base addr in SRAM [31:0]
//   0x3C    WRP_CMD         W       [0]=LOAD_A, [1]=LOAD_B, [2]=STORE_O
//   0x40    WRP_STATUS      R       [0]=READY
//
// Usage flow:
//   1. Write vector_top A_ADDR, B_ADDR, O_ADDR (chunk offsets within buffer)
//   2. Write WRP_A_BASE, WRP_B_BASE, WRP_O_BASE (SRAM base addresses)
//   3. Write WRP_CMD.LOAD_A, LOAD_B → wrapper reads from SRAM into buffers
//   4. Write WRP_CMD.STORE_O (optional, for initial data) or skip
//   5. Write vector_top CMD.START → vector_top runs using internal buffers
//   6. Poll vector_top STATUS.DONE
//   7. Write WRP_CMD.STORE_O → wrapper writes results to SRAM
//
// Must NOT modify vector_top or any engine internals.
//=============================================================================

`timescale 1ns / 1ps

module vector_soc_wrapper #(
    parameter integer AXI_ID_WIDTH   = 8,
    parameter integer AXI_ADDR_WIDTH = 32,
    parameter integer AXI_DATA_WIDTH = 512,
    parameter integer VECTOR_W       = 4096,  // 128 × 32-bit
    parameter integer NUM_LANES      = 128,
    parameter integer DATA_W         = 32,
    // Maximum chunks per operand (128 elements = 1 chunk)
    // Phase 1 default: 1 chunk = 128 elements per operand
    parameter integer CHUNKS_MAX     = 1
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
    output wire        irq
);

    //=========================================================================
    // Constants
    //=========================================================================
    localparam integer CHUNK_BYTES = NUM_LANES * (DATA_W / 8);  // 512 bytes
    localparam integer BEATS_PER_CHUNK = VECTOR_W / AXI_DATA_WIDTH;  // 8
    localparam integer ADDR_W = AXI_ADDR_WIDTH;

    //=========================================================================
    // APB → MMIO bridge (for vector_top base MMIO at offsets 0x00-0x1C)
    //=========================================================================
    wire        vec_mmio_cs, vec_mmio_we;
    wire [11:0] vec_mmio_addr;
    wire [31:0] vec_mmio_wdata, vec_mmio_rdata;
    wire        vec_mmio_ready;

    wire apb_to_vec_mmio = (paddr <= 12'h01C);

    wire [31:0] apb_prdata;

    apb_to_mmio u_apb_to_mmio (
        .clk     (clk),
        .rst_n   (rst_n),
        .psel    (psel && apb_to_vec_mmio),
        .penable (penable && apb_to_vec_mmio),
        .pwrite  (pwrite),
        .paddr   (paddr),
        .pwdata  (pwdata),
        .prdata  (apb_prdata),
        .pready  (),
        .pslverr (),
        .cs      (vec_mmio_cs),
        .we      (vec_mmio_we),
        .addr    (vec_mmio_addr),
        .wdata   (vec_mmio_wdata),
        .rdata   (vec_mmio_rdata),
        .ready   (vec_mmio_ready)
    );

    //=========================================================================
    // Wrapper-specific MMIO registers (offsets 0x30-0x40)
    //=========================================================================
    localparam [11:0] OFF_WRP_A_BASE = 12'h030;
    localparam [11:0] OFF_WRP_B_BASE = 12'h034;
    localparam [11:0] OFF_WRP_O_BASE = 12'h038;
    localparam [11:0] OFF_WRP_CMD    = 12'h03C;
    localparam [11:0] OFF_WRP_STATUS = 12'h040;

    reg [ADDR_W-1:0] wrp_a_base;
    reg [ADDR_W-1:0] wrp_b_base;
    reg [ADDR_W-1:0] wrp_o_base;
    reg              wrp_ready;         // WRP_STATUS[0]

    wire wrp_cs       = psel && (paddr >= 12'h030) && (paddr <= 12'h040);
    wire wrp_load_a   = wrp_cs && pwrite && penable && (paddr == OFF_WRP_CMD) && pwdata[0];
    wire wrp_load_b   = wrp_cs && pwrite && penable && (paddr == OFF_WRP_CMD) && pwdata[1];
    wire wrp_store_o  = wrp_cs && pwrite && penable && (paddr == OFF_WRP_CMD) && pwdata[2];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wrp_a_base <= {ADDR_W{1'b0}};
            wrp_b_base <= {ADDR_W{1'b0}};
            wrp_o_base <= {ADDR_W{1'b0}};
        end else if (wrp_cs && pwrite) begin
            case (paddr)
                OFF_WRP_A_BASE: wrp_a_base <= pwdata;
                OFF_WRP_B_BASE: wrp_b_base <= pwdata;
                OFF_WRP_O_BASE: wrp_o_base <= pwdata;
                OFF_WRP_CMD:    ;   // pulse handled above
                default: ;
            endcase
        end
    end

    wire [31:0] wrp_prdata;
    assign wrp_prdata = (paddr == OFF_WRP_A_BASE)  ? wrp_a_base :
                        (paddr == OFF_WRP_B_BASE)  ? wrp_b_base :
                        (paddr == OFF_WRP_O_BASE)  ? wrp_o_base :
                        (paddr == OFF_WRP_CMD)     ? 32'd0     :
                        (paddr == OFF_WRP_STATUS)  ? {31'd0, wrp_ready} : 32'd0;

    //=========================================================================
    // APB response mux
    //=========================================================================
    assign prdata  = apb_to_vec_mmio ? apb_prdata   : (wrp_cs ? wrp_prdata  : 32'd0);
    assign pready  = 1'b1;
    assign pslverr = 1'b0;

    //=========================================================================
    // Internal buffer arrays (register-file, one chunk per entry)
    //=========================================================================
    (* ram_style = "block" *) reg [VECTOR_W-1:0]   buf_a [0:CHUNKS_MAX-1];
    (* ram_style = "block" *) reg [VECTOR_W-1:0]   buf_b [0:CHUNKS_MAX-1];
    (* ram_style = "block" *) reg [VECTOR_W-1:0]   buf_o [0:CHUNKS_MAX-1];

    //=========================================================================
    // AXI4 Pre-fetch / Write-back Sequencer FSM
    //=========================================================================
    // Handles LOAD_A, LOAD_B, STORE_O commands.
    // Each command issues CHUNKS_MAX × 8-beat AXI4 bursts.

    localparam [3:0] SEQ_IDLE       = 4'd0;
    localparam [3:0] SEQ_LOAD_A_AR  = 4'd1;
    localparam [3:0] SEQ_LOAD_A_R   = 4'd2;
    localparam [3:0] SEQ_LOAD_B_AR  = 4'd3;
    localparam [3:0] SEQ_LOAD_B_R   = 4'd4;
    localparam [3:0] SEQ_STORE_AW   = 4'd5;
    localparam [3:0] SEQ_STORE_W    = 4'd6;
    localparam [3:0] SEQ_DONE       = 4'd7;

    reg [3:0]  seq_state;
    reg [7:0]  seq_chunk;       // current chunk index (0..CHUNKS_MAX-1)
    reg [2:0]  seq_beat;        // beat within current chunk (0..BEATS_PER_CHUNK-1)
    reg [ADDR_W-1:0] seq_base_addr; // base address for current operation
    reg        seq_load_a_pending;
    reg        seq_load_b_pending;
    reg        seq_store_pending;

    // AXI4 write burst data assembly
    reg [AXI_DATA_WIDTH-1:0] seq_wdata;
    reg [AXI_DATA_WIDTH/8-1:0] seq_wstrb;

    // Compute beat address: base + chunk*CHUNK_BYTES + beat*64
    wire [ADDR_W-1:0] seq_beat_addr;
    assign seq_beat_addr = seq_base_addr +
        (seq_chunk * CHUNK_BYTES) +
        (seq_beat * (AXI_DATA_WIDTH / 8));

    // Capture trigger pulses (these are single-cycle from WRP_CMD write)
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            seq_state          <= SEQ_IDLE;
            seq_chunk          <= 8'd0;
            seq_beat           <= 3'd0;
            seq_base_addr      <= {ADDR_W{1'b0}};
            seq_load_a_pending <= 1'b0;
            seq_load_b_pending <= 1'b0;
            seq_store_pending  <= 1'b0;
            wrp_ready          <= 1'b1;
        end else begin
            case (seq_state)
                SEQ_IDLE: begin
                    wrp_ready <= 1'b1;
                    if (wrp_load_a || seq_load_a_pending) begin
                        seq_state     <= SEQ_LOAD_A_AR;
                        seq_chunk     <= 8'd0;
                        seq_beat      <= 3'd0;
                        seq_base_addr <= wrp_a_base;
                        seq_load_a_pending <= 1'b0;
                        wrp_ready     <= 1'b0;
                    end else if (wrp_load_b || seq_load_b_pending) begin
                        seq_state     <= SEQ_LOAD_B_AR;
                        seq_chunk     <= 8'd0;
                        seq_beat      <= 3'd0;
                        seq_base_addr <= wrp_b_base;
                        seq_load_b_pending <= 1'b0;
                        wrp_ready     <= 1'b0;
                    end else if (wrp_store_o || seq_store_pending) begin
                        seq_state     <= SEQ_STORE_AW;
                        seq_chunk     <= 8'd0;
                        seq_beat      <= 3'd0;
                        seq_base_addr <= wrp_o_base;
                        seq_store_pending <= 1'b0;
                        wrp_ready     <= 1'b0;
                    end
                end

                // ── LOAD_A: read operand A from SRAM ──────────────────
                SEQ_LOAD_A_AR: begin
                    if (m_axi_arvalid && m_axi_arready)
                        seq_state <= SEQ_LOAD_A_R;
                end

                SEQ_LOAD_A_R: begin
                    if (m_axi_rvalid && m_axi_rready) begin
                        // Assemble 4096-bit chunk from 8 × 512-bit beats
                        buf_a[seq_chunk][seq_beat * AXI_DATA_WIDTH +: AXI_DATA_WIDTH] <= m_axi_rdata;
                        if (m_axi_rlast) begin
                            // This chunk done
                            if (seq_chunk == CHUNKS_MAX - 1) begin
                                // All chunks loaded
                                // Check for pending LOAD_B or STORE_O
                                if (seq_load_b_pending) begin
                                    seq_state     <= SEQ_LOAD_B_AR;
                                    seq_chunk     <= 8'd0;
                                    seq_beat      <= 3'd0;
                                    seq_base_addr <= wrp_b_base;
                                    seq_load_b_pending <= 1'b0;
                                end else if (seq_store_pending) begin
                                    seq_state     <= SEQ_STORE_AW;
                                    seq_chunk     <= 8'd0;
                                    seq_beat      <= 3'd0;
                                    seq_base_addr <= wrp_o_base;
                                    seq_store_pending <= 1'b0;
                                end else begin
                                    seq_state <= SEQ_IDLE;
                                    wrp_ready <= 1'b1;
                                end
                            end else begin
                                // Next chunk: issue new AR
                                seq_chunk <= seq_chunk + 8'd1;
                                seq_beat  <= 3'd0;
                                seq_state <= SEQ_LOAD_A_AR;
                            end
                        end else begin
                            seq_beat <= seq_beat + 3'd1;
                        end
                    end
                end

                // ── LOAD_B: read operand B from SRAM ──────────────────
                SEQ_LOAD_B_AR: begin
                    if (m_axi_arvalid && m_axi_arready)
                        seq_state <= SEQ_LOAD_B_R;
                end

                SEQ_LOAD_B_R: begin
                    if (m_axi_rvalid && m_axi_rready) begin
                        buf_b[seq_chunk][seq_beat * AXI_DATA_WIDTH +: AXI_DATA_WIDTH] <= m_axi_rdata;
                        if (m_axi_rlast) begin
                            if (seq_chunk == CHUNKS_MAX - 1) begin
                                if (seq_store_pending) begin
                                    seq_state     <= SEQ_STORE_AW;
                                    seq_chunk     <= 8'd0;
                                    seq_beat      <= 3'd0;
                                    seq_base_addr <= wrp_o_base;
                                    seq_store_pending <= 1'b0;
                                end else begin
                                    seq_state <= SEQ_IDLE;
                                    wrp_ready <= 1'b1;
                                end
                            end else begin
                                seq_chunk <= seq_chunk + 8'd1;
                                seq_beat  <= 3'd0;
                                seq_state <= SEQ_LOAD_B_AR;
                            end
                        end else begin
                            seq_beat <= seq_beat + 3'd1;
                        end
                    end
                end

                // ── STORE_O: write results to SRAM ─────────────────────
                SEQ_STORE_AW: begin
                    if (m_axi_awvalid && m_axi_awready)
                        seq_state <= SEQ_STORE_W;
                end

                SEQ_STORE_W: begin
                    if (m_axi_wvalid && m_axi_wready) begin
                        if (m_axi_wlast) begin
                            // Current chunk done
                            if (seq_chunk == CHUNKS_MAX - 1) begin
                                seq_state <= SEQ_IDLE;
                                wrp_ready <= 1'b1;
                            end else begin
                                seq_chunk <= seq_chunk + 8'd1;
                                seq_beat  <= 3'd0;
                                seq_state <= SEQ_STORE_AW;
                            end
                        end else begin
                            seq_beat <= seq_beat + 3'd1;
                        end
                    end
                end

                default: seq_state <= SEQ_IDLE;
            endcase

            // Latch new trigger pulses during non-IDLE states (chain operations)
            if (wrp_load_a && (seq_state != SEQ_IDLE))
                seq_load_a_pending <= 1'b1;
            if (wrp_load_b && (seq_state != SEQ_IDLE))
                seq_load_b_pending <= 1'b1;
            if (wrp_store_o && (seq_state != SEQ_IDLE))
                seq_store_pending <= 1'b1;
        end
    end

    //=========================================================================
    // AXI4 Read Address channel (driven by sequencer)
    //=========================================================================
    wire seq_reading_a = (seq_state == SEQ_LOAD_A_AR) || (seq_state == SEQ_LOAD_A_R);
    wire seq_reading_b = (seq_state == SEQ_LOAD_B_AR) || (seq_state == SEQ_LOAD_B_R);

    assign m_axi_arid    = 8'h20;
    assign m_axi_araddr  = (seq_state == SEQ_LOAD_A_AR) ? seq_beat_addr :
                           (seq_state == SEQ_LOAD_B_AR) ? seq_beat_addr : 32'd0;
    assign m_axi_arlen   = BEATS_PER_CHUNK - 1;  // 7 = 8 beats
    assign m_axi_arsize  = 3'd6;                  // 64 bytes
    assign m_axi_arburst = 2'd1;                  // INCR
    assign m_axi_arvalid = (seq_state == SEQ_LOAD_A_AR) || (seq_state == SEQ_LOAD_B_AR);
    assign m_axi_rready  = (seq_state == SEQ_LOAD_A_R) || (seq_state == SEQ_LOAD_B_R);

    //=========================================================================
    // AXI4 Write Address channel
    //=========================================================================
    assign m_axi_awid    = 8'h21;
    assign m_axi_awaddr  = (seq_state == SEQ_STORE_AW) ? seq_beat_addr : 32'd0;
    assign m_axi_awlen   = BEATS_PER_CHUNK - 1;
    assign m_axi_awsize  = 3'd6;
    assign m_axi_awburst = 2'd1;
    assign m_axi_awvalid = (seq_state == SEQ_STORE_AW);

    //=========================================================================
    // AXI4 Write Data channel
    //=========================================================================
    // Slice the 4096-bit buffer into 512-bit beats
    wire [VECTOR_W-1:0] cur_o_buf = buf_o[seq_chunk];
    assign m_axi_wdata  = cur_o_buf[seq_beat * AXI_DATA_WIDTH +: AXI_DATA_WIDTH];
    assign m_axi_wstrb  = {AXI_DATA_WIDTH/8{1'b1}};
    assign m_axi_wlast  = (seq_beat == BEATS_PER_CHUNK - 1);
    assign m_axi_wvalid = (seq_state == SEQ_STORE_W);

    //=========================================================================
    // AXI4 Write Response channel
    //=========================================================================
    assign m_axi_bready = 1'b1;

    //=========================================================================
    // vector_top instantiation
    //=========================================================================
    // vector_top's SRAM ports connect to our internal buffers.
    // Address mapping: a_addr → chunk index = (a_addr - wrp_a_base) / CHUNK_BYTES
    // For Phase 1 with CHUNKS_MAX=1: chunk index always 0.
    wire        vec_irq;

    wire [ADDR_W-1:0] vec_a_addr, vec_b_addr, vec_o_addr;
    wire              vec_a_en, vec_b_en, vec_o_wen;
    wire [VECTOR_W-1:0] vec_o_wdata;
    wire [511:0]      vec_o_wstrb;

    // Compute chunk index from address offset relative to base.
    // For CHUNKS_MAX=1: always 0.
    wire [7:0] a_chunk_idx, b_chunk_idx, o_chunk_idx;
    assign a_chunk_idx = (CHUNKS_MAX == 1) ? 8'd0 :
                         ((vec_a_addr - wrp_a_base) / CHUNK_BYTES);
    assign b_chunk_idx = (CHUNKS_MAX == 1) ? 8'd0 :
                         ((vec_b_addr - wrp_b_base) / CHUNK_BYTES);
    assign o_chunk_idx = (CHUNKS_MAX == 1) ? 8'd0 :
                         ((vec_o_addr - wrp_o_base) / CHUNK_BYTES);

    // Read data from internal buffers (combinatorial for 1-cycle SRAM timing)
    wire [VECTOR_W-1:0] sram_a_rdata_int, sram_b_rdata_int;
    assign sram_a_rdata_int = buf_a[a_chunk_idx];
    assign sram_b_rdata_int = buf_b[b_chunk_idx];

    vector_top #(
        .NUM_LANES(NUM_LANES),
        .DATA_W(DATA_W),
        .VECTOR_W(VECTOR_W),
        .ADDR_W(ADDR_W)
    ) u_vector_top (
        .clk          (clk),
        .rst_n        (rst_n),
        .mmio_cs      (vec_mmio_cs),
        .mmio_we      (vec_mmio_we),
        .mmio_addr    (vec_mmio_addr),
        .mmio_wdata   (vec_mmio_wdata),
        .mmio_rdata   (vec_mmio_rdata),
        .mmio_ready   (vec_mmio_ready),
        .sram_a_addr  (vec_a_addr),
        .sram_a_en    (vec_a_en),
        .sram_a_rdata (sram_a_rdata_int),
        .sram_b_addr  (vec_b_addr),
        .sram_b_en    (vec_b_en),
        .sram_b_rdata (sram_b_rdata_int),
        .sram_o_addr  (vec_o_addr),
        .sram_o_wdata (vec_o_wdata),
        .sram_o_wen   (vec_o_wen),
        .sram_o_wstrb (vec_o_wstrb),
        .irq          (vec_irq)
    );

    // Capture output writes into internal buffer
    // vector_top asserts sram_o_wen for 1 cycle per chunk write
    always @(posedge clk) begin
        if (vec_o_wen) begin
            buf_o[o_chunk_idx] <= vec_o_wdata;
        end
    end

    assign irq = vec_irq;

endmodule
