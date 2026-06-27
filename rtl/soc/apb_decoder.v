//=============================================================================
// apb_decoder — APB address decoder (1 master → 7 slaves)
//=============================================================================
// Decodes a single APB master bus into 7 APB slave ports, each covering a
// 4 KB window within the MMIO region 0x4000_0000 ~ 0x4000_6FFF.
//
// Slave mapping (per caduceus_soc_top unified address space):
//   slave0 = MXU       0x4000_0000 ~ 0x4000_0FFF
//   slave1 = SFU       0x4000_1000 ~ 0x4000_1FFF
//   slave2 = VECTOR    0x4000_2000 ~ 0x4000_2FFF
//   slave3 = DMA       0x4000_3000 ~ 0x4000_3FFF
//   slave4 = PCIe      0x4000_4000 ~ 0x4000_4FFF
//   slave5 = DOORBELL  0x4000_5000 ~ 0x4000_5FFF
//   slave6 = INTC      0x4000_6000 ~ 0x4000_6FFF
//
// Out-of-range access → pslverr = 1, pready = 1 (terminate transfer with error).
//
// Protocol: AMBA APB v2.0.  Setup phase (psel=1, penable=0) → access phase
// (psel=1, penable=1).  paddr[31:0] is sampled by the decoder; psel_o[x] is
// asserted for the selected slave during both phases.
//=============================================================================

module apb_decoder (
    input  wire        clk,
    input  wire        rst_n,

    // ── APB master (from Ibex or APB bridge) ────────────────────────────
    input  wire        psel,
    input  wire        penable,
    input  wire [31:0] paddr,
    input  wire        pwrite,
    input  wire [31:0] pwdata,

    // ── APB slave ports (to engine/ peripheral wrappers) ────────────────
    output wire  [6:0] psel_o,
    output wire  [6:0] penable_o,
    output wire [31:0] paddr_o,
    output wire        pwrite_o,
    output wire [31:0] pwdata_o,

    // ── Slave response (muxed back to master) ───────────────────────────
    input  wire [6:0]  pready_i,
    input  wire [6:0]  pslverr_i,
    input  wire [31:0] prdata_i [0:6],

    output wire        pready,
    output wire        pslverr,
    output wire [31:0] prdata
);

    //=========================================================================
    // Address decode — one-hot slave select
    //=========================================================================
    // The APB MMIO window occupies  0x4000_0000 ~ 0x4000_6FFF.
    // Decode uses paddr[31:16] for region match and paddr[15:12] for slave.
    // Each slave gets exactly 4 KB (0x1000 bytes).

    wire        region_hit;         // paddr[31:16] == 16'h4000
    wire [3:0]  page;               // paddr[15:12] — selects slave 0..6
    wire        slave_valid;        // page is within 0..6
    wire [6:0]  slave_sel;          // one-hot decode of page

    assign region_hit  = (paddr[31:16] == 16'h4000);
    assign page        = paddr[15:12];
    assign slave_valid = (page <= 4'd6);

    // One-hot decode:  page 0→bit0, 1→bit1, …, 6→bit6
    assign slave_sel[0] = (page == 4'd0);
    assign slave_sel[1] = (page == 4'd1);
    assign slave_sel[2] = (page == 4'd2);
    assign slave_sel[3] = (page == 4'd3);
    assign slave_sel[4] = (page == 4'd4);
    assign slave_sel[5] = (page == 4'd5);
    assign slave_sel[6] = (page == 4'd6);

    // psel_o: asserted only when master is active AND address hits a slave
    assign psel_o = (psel && region_hit && slave_valid) ? slave_sel : 7'h0;

    //=========================================================================
    // Pass-through signals (broadcast to all slaves)
    //=========================================================================
    assign penable_o = {7{penable}};
    assign paddr_o   = paddr;
    assign pwrite_o  = pwrite;
    assign pwdata_o  = pwdata;

    //=========================================================================
    // Response mux — select from the slave that was decoded
    //=========================================================================
    // During access phase (psel=1, penable=1), the selected slave responds.
    // If no slave is selected (out-of-range), the decoder itself generates
    // pslverr and provides zero read data.

    wire        no_slave_selected;
    assign no_slave_selected = (psel_o == 7'h0) && psel;

    // pready: from the selected slave when one is hit; otherwise 1 (terminate).
    // Only the selected slave's pready_i is relevant; mux it with slave_sel.
    wire [6:0] pready_masked;
    assign pready_masked[0] = slave_sel[0] ? pready_i[0] : 1'b0;
    assign pready_masked[1] = slave_sel[1] ? pready_i[1] : 1'b0;
    assign pready_masked[2] = slave_sel[2] ? pready_i[2] : 1'b0;
    assign pready_masked[3] = slave_sel[3] ? pready_i[3] : 1'b0;
    assign pready_masked[4] = slave_sel[4] ? pready_i[4] : 1'b0;
    assign pready_masked[5] = slave_sel[5] ? pready_i[5] : 1'b0;
    assign pready_masked[6] = slave_sel[6] ? pready_i[6] : 1'b0;

    assign pready  = no_slave_selected ? 1'b1 : |pready_masked;

    // pslverr: from the selected slave; decoder generates its own when
    // address is outside the mapped range.
    wire [6:0] pslverr_masked;
    assign pslverr_masked[0] = slave_sel[0] ? pslverr_i[0] : 1'b0;
    assign pslverr_masked[1] = slave_sel[1] ? pslverr_i[1] : 1'b0;
    assign pslverr_masked[2] = slave_sel[2] ? pslverr_i[2] : 1'b0;
    assign pslverr_masked[3] = slave_sel[3] ? pslverr_i[3] : 1'b0;
    assign pslverr_masked[4] = slave_sel[4] ? pslverr_i[4] : 1'b0;
    assign pslverr_masked[5] = slave_sel[5] ? pslverr_i[5] : 1'b0;
    assign pslverr_masked[6] = slave_sel[6] ? pslverr_i[6] : 1'b0;

    assign pslverr = no_slave_selected ? 1'b1 : |pslverr_masked;

    // prdata: mux from the selected slave; 0 when no slave hit.
    assign prdata = slave_sel[0] ? prdata_i[0] :
                    slave_sel[1] ? prdata_i[1] :
                    slave_sel[2] ? prdata_i[2] :
                    slave_sel[3] ? prdata_i[3] :
                    slave_sel[4] ? prdata_i[4] :
                    slave_sel[5] ? prdata_i[5] :
                    slave_sel[6] ? prdata_i[6] :
                    32'h0;

endmodule
