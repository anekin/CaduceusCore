//=============================================================================
// intc_top — 7-source interrupt controller with APB slave interface
//=============================================================================
// CaduceusCore SoC Phase 3-4 / Task 6
//
// Collects up to 7 level-sensitive interrupt sources and produces a single
// cpu_irq to the Ibex RISC-V core.  The cpu_irq is asserted when:
//   - At least one enabled source is pending, AND
//   - The number of enabled pending sources (popcount) meets THRESHOLD.
//
// Source bit mapping (per caduceus_soc_top unified interrupt map):
//   bit0 = mxu         (MXU engine)
//   bit1 = sfu         (SFU engine)
//   bit2 = vector      (Vector engine)
//   bit3 = dma         (DMA engine)
//   bit4 = pcie        (PCIe EP)
//   bit5 = host        (Host doorbell)
//   bit6 = timer_irq   (Timer)
//
// Registers (APB slave at 0x4000_6000, 4 KB window):
//   Offset  Access  Name         Description
//   0x00    RO      PENDING      Pending bits — set by irq sources, cleared by ACK
//   0x04    RW      ENABLE       Interrupt enable mask
//   0x08    RW      THRESHOLD    Minimum popcount to assert cpu_irq
//   0x0C    W1C     ACK          Write-1-to-clear — clears corresponding PENDING bit
//
// Protocol: AMBA APB v2.0.  Zero-wait-state slave (pready always 1).
//=============================================================================

module intc_top (
    input  wire        clk,
    input  wire        rst_n,

    // ── Interrupt source inputs (level-sensitive, active-high) ────────────
    input  wire        mxu_irq,
    input  wire        sfu_irq,
    input  wire        vector_irq,
    input  wire        dma_irq,
    input  wire        pcie_irq,
    input  wire        host_irq,
    input  wire        timer_irq,

    // ── APB slave interface (to apb_decoder port 6) ───────────────────────
    input  wire        psel,
    input  wire        penable,
    input  wire        pwrite,
    input  wire [11:0] paddr,
    input  wire [31:0] pwdata,
    output wire [31:0] prdata,
    output wire        pready,
    output wire        pslverr,

    // ── CPU interrupt output (to Ibex) ────────────────────────────────────
    output wire        cpu_irq
);

    //=========================================================================
    // Local signals
    //=========================================================================
    wire [6:0] irq_src;         // packed interrupt source vector
    wire       apb_write;       // APB write strobe (access phase)
    wire       sel_pending;     // address decode — PENDING
    wire       sel_enable;      // address decode — ENABLE
    wire       sel_threshold;   // address decode — THRESHOLD
    wire       sel_ack;         // address decode — ACK
    wire [6:0] ack_clear;       // per-bit ACK clear strobe
    wire [6:0] enabled_pending; // PENDING & ENABLE
    wire [2:0] pcnt;            // popcount(enabled_pending)

    //=========================================================================
    // Interrupt source packing
    //=========================================================================
    // bit6=MSB (timer), bit0=LSB (mxu) — see bit mapping above.
    assign irq_src = {timer_irq, host_irq, pcie_irq, dma_irq,
                      vector_irq, sfu_irq, mxu_irq};

    //=========================================================================
    // APB control and address decode
    //=========================================================================
    assign apb_write     = psel && penable && pwrite;
    assign sel_pending   = (paddr[11:0] == 12'h000);
    assign sel_enable    = (paddr[11:0] == 12'h004);
    assign sel_threshold = (paddr[11:0] == 12'h008);
    assign sel_ack       = (paddr[11:0] == 12'h00C);

    //=========================================================================
    // PENDING register — Read-Only
    //=========================================================================
    // PENDING bits are set when the corresponding irq source is high.
    // Writing 1 to ACK[i] clears pending_reg[i].  If the source is still
    // high after the ACK, the bit re-sets on the next cycle.
    //
    // Equivalent:  pending_reg <= (pending_reg & ~ack_clear) | irq_src
    reg [6:0] pending_reg;

    assign ack_clear = (apb_write && sel_ack) ? pwdata[6:0] : 7'h0;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pending_reg <= 7'h0;
        end else begin
            pending_reg <= (pending_reg & ~ack_clear) | irq_src;
        end
    end

    //=========================================================================
    // ENABLE register — Read/Write
    //=========================================================================
    reg [6:0] enable_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            enable_reg <= 7'h0;
        end else if (apb_write && sel_enable) begin
            enable_reg <= pwdata[6:0];
        end
    end

    //=========================================================================
    // THRESHOLD register — Read/Write
    //=========================================================================
    // Holds a 3-bit count threshold (0-7).  Default = 1 so a single enabled
    // pending source fires cpu_irq.  Writing 0 effectively disables the
    // threshold gate (popcount >= 0 always true).
    reg [2:0] threshold_reg;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            threshold_reg <= 3'd1;
        end else if (apb_write && sel_threshold) begin
            threshold_reg <= pwdata[2:0];
        end
    end

    //=========================================================================
    // Interrupt assertion logic
    //=========================================================================
    assign enabled_pending = pending_reg & enable_reg;

    // Popcount of 7-bit vector (combinational)
    function [2:0] popcount;
        input [6:0] in;
        integer i;
        begin
            popcount = 3'd0;
            for (i = 0; i < 7; i = i + 1) begin
                popcount = popcount + {2'd0, in[i]};
            end
        end
    endfunction

    assign pcnt = popcount(enabled_pending);

    // cpu_irq = |(PENDING & ENABLE) when popcount >= THRESHOLD else 0
    // Registered to avoid combinational popcount glitch
    reg cpu_irq_reg;
    wire cpu_irq_comb = (|enabled_pending) && (pcnt >= threshold_reg);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cpu_irq_reg <= 1'b0;
        else
            cpu_irq_reg <= cpu_irq_comb;
    end
    assign cpu_irq = cpu_irq_reg;

    //=========================================================================
    // APB read-data mux
    //=========================================================================
    reg [31:0] prdata_reg;

    always @(*) begin
        if (sel_pending)
            prdata_reg = {25'h0, pending_reg};
        else if (sel_enable)
            prdata_reg = {25'h0, enable_reg};
        else if (sel_threshold)
            prdata_reg = {29'h0, threshold_reg};
        else
            prdata_reg = 32'h0;
    end

    // Gate read data: only valid during read transactions.
    assign prdata  = (psel && !pwrite) ? prdata_reg : 32'h0;

    // Zero-wait-state APB slave — never inserts wait states or errors.
    assign pready  = 1'b1;
    assign pslverr = 1'b0;

endmodule
