//=============================================================================
// apb_to_mmio — APB slave → native MMIO adapter bridge
//=============================================================================
// Converts AMBA APB v2.0 slave signals to the native MMIO interface used by
// CaduceusCore engine register files (mmio_if.v convention).
//
// MMIO interface (ref: CaduceusCore/rtl/mxu/mmio_if.v:44-50):
//   cs    — chip-select (active high)
//   we    — write-enable  (1=write, 0=read)
//   addr  — byte-offset within 4 KB window  [11:0]
//   wdata — write data     [31:0]
//   rdata — read data      [31:0]  (combinational)
//   ready — transaction accepted (combinational, =cs for simple slaves)
//
// APB timing:
//   Setup  phase: psel=1, penable=0 → cs/we/addr/wdata presented to MMIO
//   Access phase: psel=1, penable=1 → prdata sampled, pready asserted
//
// MMIO slaves are always ready (ready=cs).  This bridge presents a zero-
// wait-state APB slave: pready=1'b1, pslverr=1'b0.
//
// Write: MMIO captures wdata on the posedge between setup→access (i.e.
//        the posedge that enters the access phase).  This is a natural
//        fit: during setup (psel=1, we=1) the MMIO register sees cs=1,we=1
//        and captures pwdata on the next clk edge.
// Read:  During setup phase, rdata becomes combinatorially valid.
//        During access phase, prdata = rdata is read by the APB master.
//=============================================================================

module apb_to_mmio (
    input  wire        clk,
    input  wire        rst_n,

    // ── APB slave ───────────────────────────────────────────────────────
    input  wire        psel,
    input  wire        penable,
    input  wire        pwrite,
    input  wire [11:0] paddr,
    input  wire [31:0] pwdata,
    output wire [31:0] prdata,
    output wire        pready,
    output wire        pslverr,

    // ── MMIO master (to engine register file) ───────────────────────────
    output wire        cs,
    output wire        we,
    output wire [11:0] addr,
    output wire [31:0] wdata,
    input  wire [31:0] rdata,
    input  wire        ready
);

    //=========================================================================
    // MMIO drive — feed APB control signals directly to MMIO
    //=========================================================================
    // cs is asserted during both setup and access phases (psel=1).
    // MMIO slaves use cs as their ready indication: ready = cs.
    assign cs    = psel;
    assign we    = pwrite;
    assign addr  = paddr;
    assign wdata = pwdata;

    //=========================================================================
    // APB response
    //=========================================================================
    // pready: always 1 — MMIO slaves never insert wait states.
    // pslverr: always 0 — MMIO slaves always respond correctly.
    // prdata: driven by MMIO rdata (combinational path).  During access
    //         phase the APB master samples prdata when pready=1.

    assign pready  = 1'b1;
    assign pslverr = 1'b0;
    assign prdata  = (psel && !pwrite) ? rdata : 32'h0;

endmodule
