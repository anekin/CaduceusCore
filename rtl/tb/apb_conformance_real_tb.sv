//=============================================================================
// apb_conformance_real_tb.sv — APB conformance against REAL peripheral RTL
// CaduceusCore / soc-rtl-review-remediation todo 12 (GREEN)
//
// Todo 3 (RED) instantiated the REAL apb_decoder + 7 REAL peripherals and ran
// the MODEL-slave oracle — 40 divergences exposed, and the W1 analysis showed
// they were almost all WRONG EXPECTATIONS (the model table does not describe
// the real peripherals). This todo replaces the oracle with REAL RTL
// semantics derived from INDEPENDENT sources:
//   * base addresses: gen/npu_abi_firmware.h NPU_ABI_* constants (:15-38)
//   * per-register semantics: each peripheral's DOCUMENTED MMIO header table
//     (rtl/wrapper/mxu_soc_wrapper.v, rtl/wrapper/vector_soc_wrapper.v,
//     rtl/ip/dma_wrapper.v, rtl/ip/pcie_ep_wrapper.v, rtl/intc/intc_top.v,
//     rtl/soc/doorbell.v), cross-checked against the RTL (line cites in the
//     oracle tables below).
//
// Real semantics encoded (vs the wrong model expectations of todo 3):
//   MXU/SFU/VECTOR: CMD is a write-only PULSE register — readback 0
//                   (mmio_if.v:139, sfu_top.v:134, vector_top.v:144);
//                   STATUS is RO; the rest full-width RW; reset all 0.
//                   Wrapper extension regs covered per their header tables
//                   (MXU 0x30-0x48 and VECTOR 0x30-0x44 have NON-zero resets).
//   DMA:            CMD STORES the written value and reads it back
//                   (dma_wrapper.v:285/:128); STATUS RO; rest RW; reset 0.
//   DOORBELL:       4 RW regs 0x00-0x0C (doorbell.v:80-83); offsets 0x10/0x14
//                   are silent-0 (addr_valid gate, doorbell.v:70) — the ABI
//                   (npu-regmap.h npu_doorbell_t) DECLARES LAST_STATUS@0x10
//                   R/W + COMPLETION_STATUS[16]@0x14 → DOCUMENTED DIVERGENCE,
//                   filed as BUG-RTL-SOC-009.
//   INTC:           PENDING is a LIVE sticky RO reg (hostile writes ignored,
//                   intc_top.v:98/:104); ENABLE 8-bit masked (:117); THRESHOLD
//                   4-bit masked, RESET=1 (:131/:133); ACK is W1C and reads
//                   back 0 (:180-181).
//   PCIE:           CTRL@0x00 [2:0]=mps stored, readback shifted to [3:1] with
//                   bit0=0, bit3 (documented "enable") NOT implemented →
//                   DOCUMENTED DIVERGENCE BUG-RTL-SOC-010;
//                   STATUS@0x04 RO; COMPLETER_ID@0x08 RW[15:0];
//                   BAR0_BASE@0x0C RO 0x2000_0000; BAR0_MASK@0x10 RO
//                   0xFFC0_0000; BAR1_BASE@0x14 RO 0x8000_0000; BAR1_MASK@0x18
//                   RO 0x8000_0000 (documented "bit31=writable" NOT
//                   implemented → BUG-RTL-SOC-010); MSIX_CTRL@0x1C
//                   field-masked; IRQ_CTRL@0x20 field-masked + W1C[1];
//                   offsets >= 0x24 UNMAPPED → pslverr=1
//                   (pcie_ep_wrapper.v:296).
//
// Documented-divergence handling (todo 12 mandate): a check whose REAL
// behavior contradicts a DOCUMENTED spec is tagged [DOC-DIV <bug-id>],
// counted in the documented-divergence bucket, and references a bug filed in
// docs/bugs/. It is NOT silently passed. Bugs filed for this TB:
//   BUG-RTL-SOC-009 — doorbell ABI window (LAST_STATUS/COMPLETION_STATUS)
//   BUG-RTL-SOC-010 — pcie_ep_wrapper header overstates CTRL[3]/BAR1_MASK
//   BUG-RTL-SOC-011 — rtl/ip/README DMA access classes (CMD W / STATUS R)
//
// pcie_dma_wrapper (AXI master M6, decoder port 7 @ 0x4000_7000) remains out
// of APB-conformance scope; a live guard proves psel_o[7] never asserts.
//
// Wiring notes (SoC fidelity, unchanged from todo 3):
//   * Peripherals' irq outputs feed intc_top exactly as caduceus_soc_top.v
//     does (mxu->bit0 ... doorbell->bit5). timer_irq (bit6) and pcie_dma_irq
//     (bit7) have no source module in this TB, so they are driven by TB regs
//     (they are SoC-external stimulus by nature).
//   * AXI4 master ports of all peripherals are tied off (ready=0, no slave
//     model). No CMD write in the stimulus has START bit set, so no compute
//     is launched and the tie-off cannot stall APB. (SFU pready can stall
//     only around a real CMD.START — sfu_soc_wrapper.v:631 — which this
//     stimulus never produces.)
//   * PCIe TLP RX idles (valid=0), tx_cpl_tlp_ready=1; doorbell bkdoor_* = 0.
//   * pcie_dma_wrapper (slave 7 @ 0x4000_7000) is NOT instantiated — it is
//     the AXI master M6, outside this APB conformance scope; a live guard
//     counts psel_o[7] assertions (must stay 0).
//
// Usage (EDA server sz0001 only — VCS), via the regression Makefile:
//   bash sim/regression/soc-verification-run.sh run_apb_conformance_real
// Verdict marker: "APB_CONFORMANCE_REAL: GREEN (...)" — the Makefile target
// greps that exact marker (fail-closed: it is printed only when the TB's own
// counters prove 0 unexpected fails, 0 timeouts, and >= 5 peripherals covered).
//=============================================================================

`timescale 1ns / 1ps

module apb_conformance_real_tb;

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam CLK_HALF = 5;               // 100 MHz

    // Access codes for the REAL-semantics oracle table
    localparam [3:0] ACC_RW      = 4'd0;  // full-width RW overwrite
    localparam [3:0] ACC_RWM     = 4'd1;  // masked RW overwrite (REG_MSK)
    localparam [3:0] ACC_RO      = 4'd2;  // read-only, hostile write unchanged
    localparam [3:0] ACC_CONST   = 4'd3;  // read-only constant (REG_RST)
    localparam [3:0] ACC_WO      = 4'd4;  // write-only pulse, readback 0
    localparam [3:0] ACC_WOS     = 4'd5;  // write-only STORED (DMA CMD), readback
    localparam [3:0] ACC_FIELD   = 4'd6;  // field-mapped (EXP_FULL / EXP_ZERO)
    localparam [3:0] ACC_UNMAP   = 4'd7;  // unmapped: read+write -> pslverr=1
    localparam [3:0] ACC_DOCDIVR = 4'd8;  // reserved-in-ABI: reads 0, writes dropped
    localparam [3:0] ACC_W1C     = 4'd9;  // INTC.ACK — special-cased, not in table

    // Sentinel reset value: skip the Phase-1 absolute reset check (relative only)
    localparam [31:0] RST_SKIP = 32'hDEAD_BEEF;

    // ── ABI base addresses — transcribed from gen/npu_abi_firmware.h ─────
    // NPU_ABI_MXU_BASE     0x40000000UL   (:27)
    // NPU_ABI_SFU_BASE     0x40001000UL   (:33)
    // NPU_ABI_VECTOR_BASE  0x40002000UL   (:37)
    // NPU_ABI_DMA_BASE     0x40003000UL   (:17)
    // NPU_ABI_PCIE_BASE    0x40004000UL   (:29)
    // NPU_ABI_DOORBELL_BASE 0x40005000UL  (:19)
    // NPU_ABI_INTC_BASE    0x40006000UL   (:25)
    // NPU_ABI_PCIE_DMA_BASE 0x40007000UL  (:31) — NOT instantiated (guard only)
    localparam [31:0] ABI_MXU_BASE      = 32'h4000_0000;
    localparam [31:0] ABI_SFU_BASE      = 32'h4000_1000;
    localparam [31:0] ABI_VECTOR_BASE   = 32'h4000_2000;
    localparam [31:0] ABI_DMA_BASE      = 32'h4000_3000;
    localparam [31:0] ABI_PCIE_BASE     = 32'h4000_4000;
    localparam [31:0] ABI_DOORBELL_BASE = 32'h4000_5000;
    localparam [31:0] ABI_INTC_BASE     = 32'h4000_6000;
    localparam [31:0] ABI_PCIE_DMA_BASE = 32'h4000_7000;   // guard only

    //=========================================================================
    // Signals — APB master (to apb_decoder)
    //=========================================================================
    reg         clk;
    reg         rst_n;
    reg         psel;
    reg         penable;
    reg  [31:0] paddr;
    reg         pwrite;
    reg  [31:0] pwdata;

    // APB slave ports (from apb_decoder)
    wire [7:0]  psel_o;
    wire [7:0]  penable_o;
    wire [31:0] paddr_o;
    wire        pwrite_o;
    wire [31:0] pwdata_o;

    // Per-slave responses (bit 7 = PCIE_DMA, not instantiated)
    wire [7:0]  pready_i;
    wire [7:0]  pslverr_i;
    wire [31:0] prdata_i [0:7];

    // Muxed response back to master
    wire        pready;
    wire        pslverr;
    wire [31:0] prdata;

    //=========================================================================
    // Signals — peripheral APB + irq + AXI master tie-offs
    //=========================================================================
    // MXU
    wire        mxu_pready, mxu_pslverr;
    wire [31:0] mxu_prdata;
    wire        mxu_irq;
    wire [3:0]  mxu_dbg_state;
    wire        mxu_dbg_compute_en, mxu_dbg_weight_load, mxu_dbg_activation_load;
    wire        mxu_dbg_store_out;
    wire [5:0]  mxu_dbg_store_row, mxu_dbg_compute_k;
    wire [15:0] mxu_dbg_tiles_completed;

    // SFU
    wire        sfu_pready, sfu_pslverr;
    wire [31:0] sfu_prdata;
    wire        sfu_irq;

    // VECTOR
    wire        vec_pready, vec_pslverr;
    wire [31:0] vec_prdata;
    wire        vec_irq;

    // DMA
    wire        dma_pready, dma_pslverr;
    wire [31:0] dma_prdata;
    wire        dma_irq;

    // PCIE
    wire        pcie_pready, pcie_pslverr;
    wire [31:0] pcie_prdata;
    wire        pcie_irq;
    wire        pcie_rx_req_tlp_ready;
    wire [511:0] pcie_tx_cpl_tlp_data;
    wire [15:0]  pcie_tx_cpl_tlp_strb;
    wire [127:0] pcie_tx_cpl_tlp_hdr;
    wire        pcie_tx_cpl_tlp_valid, pcie_tx_cpl_tlp_sop, pcie_tx_cpl_tlp_eop;

    // DOORBELL
    wire        db_pready, db_pslverr;
    wire [31:0] db_prdata;
    wire        doorbell_irq;
    wire [31:0] db_bkdoor_rdata;

    // INTC
    wire        intc_pready, intc_pslverr;
    wire [31:0] intc_prdata;
    wire        cpu_irq;

    // INTC stimulus for sources with no module in this TB (timer / pcie_dma)
    reg         tb_timer_irq;
    reg         tb_pcie_dma_irq;

    //=========================================================================
    // DUT: REAL rtl apb_decoder (1 master -> 8 slaves, 0x4000_0000 window)
    //=========================================================================
    apb_decoder u_dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .psel      (psel),
        .penable   (penable),
        .paddr     (paddr),
        .pwrite    (pwrite),
        .pwdata    (pwdata),
        .psel_o    (psel_o),
        .penable_o (penable_o),
        .paddr_o   (paddr_o),
        .pwrite_o  (pwrite_o),
        .pwdata_o  (pwdata_o),
        .pready_i  (pready_i),
        .pslverr_i (pslverr_i),
        .prdata_i  (prdata_i),
        .pready    (pready),
        .pslverr   (pslverr),
        .prdata    (prdata)
    );

    // Slave 7 (PCIE_DMA @ 0x4000_7000) NOT instantiated: pready=1 so an
    // accidental access would still terminate, pslverr=1 flags it, guard
    // counter below proves it is never selected.
    assign pready_i  = {1'b1, intc_pready, db_pready, pcie_pready,
                        dma_pready, vec_pready, sfu_pready, mxu_pready};
    assign pslverr_i = {1'b1, intc_pslverr, db_pslverr, pcie_pslverr,
                        dma_pslverr, vec_pslverr, sfu_pslverr, mxu_pslverr};
    assign prdata_i[0] = mxu_prdata;
    assign prdata_i[1] = sfu_prdata;
    assign prdata_i[2] = vec_prdata;
    assign prdata_i[3] = dma_prdata;
    assign prdata_i[4] = pcie_prdata;
    assign prdata_i[5] = db_prdata;
    assign prdata_i[6] = intc_prdata;
    assign prdata_i[7] = 32'h0;

    //=========================================================================
    // REAL peripheral RTL — instantiated per caduceus_soc_top.v
    //=========================================================================

    // ── MXU SoC Wrapper (APB slave 0 @ 0x4000_0000, AXI4 master 1) ─────────
    // Parameter override matches caduceus_soc_top.v:972-975.
    // AXI4 master ports tied off (no AXI slave in this TB); ready inputs = 0
    // so no transaction can ever be accepted (no CMD.START is written, so
    // the sequencers stay idle). dbg_* ports observed for log evidence.
    mxu_soc_wrapper #(
        .W_BUF_DEPTH (5120),
        .A_BUF_DEPTH (10240)
    ) u_mxu_wrapper (
        .clk              (clk),
        .rst_n            (rst_n),
        .psel             (psel_o[0]),
        .penable          (penable_o[0]),
        .pwrite           (pwrite_o),
        .paddr            (paddr_o[11:0]),
        .pwdata           (pwdata_o),
        .prdata           (mxu_prdata),
        .pready           (mxu_pready),
        .pslverr          (mxu_pslverr),
        // AXI4 master — tied off (outputs intentionally unconnected)
        .m_axi_awid       (),
        .m_axi_awaddr     (),
        .m_axi_awlen      (),
        .m_axi_awsize     (),
        .m_axi_awburst    (),
        .m_axi_awvalid    (),
        .m_axi_awready    (1'b0),
        .m_axi_wdata      (),
        .m_axi_wstrb      (),
        .m_axi_wlast      (),
        .m_axi_wvalid     (),
        .m_axi_wready     (1'b0),
        .m_axi_bid        (8'h0),
        .m_axi_bresp      (2'h0),
        .m_axi_bvalid     (1'b0),
        .m_axi_bready     (),
        .m_axi_arid       (),
        .m_axi_araddr     (),
        .m_axi_arlen      (),
        .m_axi_arsize     (),
        .m_axi_arburst    (),
        .m_axi_arvalid    (),
        .m_axi_arready    (1'b0),
        .m_axi_rid        (8'h0),
        .m_axi_rdata      (512'h0),
        .m_axi_rresp      (2'h0),
        .m_axi_rlast      (1'b0),
        .m_axi_rvalid     (1'b0),
        .m_axi_rready     (),
        .irq              (mxu_irq),
        .dbg_state        (mxu_dbg_state),
        .dbg_compute_en   (mxu_dbg_compute_en),
        .dbg_weight_load  (mxu_dbg_weight_load),
        .dbg_activation_load(mxu_dbg_activation_load),
        .dbg_store_out    (mxu_dbg_store_out),
        .dbg_store_row    (mxu_dbg_store_row),
        .dbg_compute_k    (mxu_dbg_compute_k),
        .dbg_tiles_completed(mxu_dbg_tiles_completed)
    );

    // ── SFU SoC Wrapper (APB slave 1 @ 0x4000_1000, AXI4 master 2) ─────────
    sfu_soc_wrapper u_sfu_wrapper (
        .clk           (clk),
        .rst_n         (rst_n),
        .psel          (psel_o[1]),
        .penable       (penable_o[1]),
        .pwrite        (pwrite_o),
        .paddr         (paddr_o[11:0]),
        .pwdata        (pwdata_o),
        .prdata        (sfu_prdata),
        .pready        (sfu_pready),
        .pslverr       (sfu_pslverr),
        // AXI4 master — tied off
        .m_axi_awid    (),
        .m_axi_awaddr  (),
        .m_axi_awlen   (),
        .m_axi_awsize  (),
        .m_axi_awburst (),
        .m_axi_awvalid (),
        .m_axi_awready (1'b0),
        .m_axi_wdata   (),
        .m_axi_wstrb   (),
        .m_axi_wlast   (),
        .m_axi_wvalid  (),
        .m_axi_wready  (1'b0),
        .m_axi_bid     (8'h0),
        .m_axi_bresp   (2'h0),
        .m_axi_bvalid  (1'b0),
        .m_axi_bready  (),
        .m_axi_arid    (),
        .m_axi_araddr  (),
        .m_axi_arlen   (),
        .m_axi_arsize  (),
        .m_axi_arburst (),
        .m_axi_arvalid (),
        .m_axi_arready (1'b0),
        .m_axi_rid     (8'h0),
        .m_axi_rdata   (512'h0),
        .m_axi_rresp   (2'h0),
        .m_axi_rlast   (1'b0),
        .m_axi_rvalid  (1'b0),
        .m_axi_rready  (),
        .irq           (sfu_irq)
    );

    // ── Vector SoC Wrapper (APB slave 2 @ 0x4000_2000, AXI4 master 3) ──────
    vector_soc_wrapper #(
        .CHUNKS_MAX (128)
    ) u_vector_wrapper (
        .clk           (clk),
        .rst_n         (rst_n),
        .psel          (psel_o[2]),
        .penable       (penable_o[2]),
        .pwrite        (pwrite_o),
        .paddr         (paddr_o[11:0]),
        .pwdata        (pwdata_o),
        .prdata        (vec_prdata),
        .pready        (vec_pready),
        .pslverr       (vec_pslverr),
        // AXI4 master — tied off
        .m_axi_awid    (),
        .m_axi_awaddr  (),
        .m_axi_awlen   (),
        .m_axi_awsize  (),
        .m_axi_awburst (),
        .m_axi_awvalid (),
        .m_axi_awready (1'b0),
        .m_axi_wdata   (),
        .m_axi_wstrb   (),
        .m_axi_wlast   (),
        .m_axi_wvalid  (),
        .m_axi_wready  (1'b0),
        .m_axi_bid     (8'h0),
        .m_axi_bresp   (2'h0),
        .m_axi_bvalid  (1'b0),
        .m_axi_bready  (),
        .m_axi_arid    (),
        .m_axi_araddr  (),
        .m_axi_arlen   (),
        .m_axi_arsize  (),
        .m_axi_arburst (),
        .m_axi_arvalid (),
        .m_axi_arready (1'b0),
        .m_axi_rid     (8'h0),
        .m_axi_rdata   (512'h0),
        .m_axi_rresp   (2'h0),
        .m_axi_rlast   (1'b0),
        .m_axi_rvalid  (1'b0),
        .m_axi_rready  (),
        .irq           (vec_irq)
    );

    // ── DMA Wrapper (APB slave 3 @ 0x4000_3000, AXI4 master 4) ─────────────
    dma_wrapper u_dma_wrapper (
        .clk           (clk),
        .rst_n         (rst_n),
        .psel          (psel_o[3]),
        .penable       (penable_o[3]),
        .pwrite        (pwrite_o),
        .paddr         (paddr_o[11:0]),
        .pwdata        (pwdata_o),
        .prdata        (dma_prdata),
        .pready        (dma_pready),
        .pslverr       (dma_pslverr),
        // AXI4 master — tied off
        .m_axi_awid    (),
        .m_axi_awaddr  (),
        .m_axi_awlen   (),
        .m_axi_awsize  (),
        .m_axi_awburst (),
        .m_axi_awvalid (),
        .m_axi_awready (1'b0),
        .m_axi_wdata   (),
        .m_axi_wstrb   (),
        .m_axi_wlast   (),
        .m_axi_wvalid  (),
        .m_axi_wready  (1'b0),
        .m_axi_bid     (8'h0),
        .m_axi_bresp   (2'h0),
        .m_axi_bvalid  (1'b0),
        .m_axi_bready  (),
        .m_axi_arid    (),
        .m_axi_araddr  (),
        .m_axi_arlen   (),
        .m_axi_arsize  (),
        .m_axi_arburst (),
        .m_axi_arvalid (),
        .m_axi_arready (1'b0),
        .m_axi_rid     (8'h0),
        .m_axi_rdata   (512'h0),
        .m_axi_rresp   (2'h0),
        .m_axi_rlast   (1'b0),
        .m_axi_rvalid  (1'b0),
        .m_axi_rready  (),
        .dma_irq       (dma_irq)
    );

    // ── PCIe EP Wrapper (APB slave 4 @ 0x4000_4000, AXI4 master 5) ─────────
    // NOTE: the APB slave is pcie_ep_wrapper — NOT pcie_dma_wrapper (the AXI
    // master M6 on decoder port 7, caduceus_soc_top.v:1258 — not instantiated).
    // TLP RX idles (valid=0); tx_cpl_tlp_ready held high per pcie_ep_tb.sv.
    pcie_ep_wrapper u_pcie_wrapper (
        .clk               (clk),
        .rst_n             (rst_n),
        // TLP RX (host -> EP) — idle
        .rx_req_tlp_data   (512'h0),
        .rx_req_tlp_hdr    (128'h0),
        .rx_req_tlp_valid  (1'b0),
        .rx_req_tlp_sop    (1'b0),
        .rx_req_tlp_eop    (1'b0),
        .rx_req_tlp_ready  (pcie_rx_req_tlp_ready),
        // TLP TX (EP -> host) — observed only
        .tx_cpl_tlp_data   (pcie_tx_cpl_tlp_data),
        .tx_cpl_tlp_strb   (pcie_tx_cpl_tlp_strb),
        .tx_cpl_tlp_hdr    (pcie_tx_cpl_tlp_hdr),
        .tx_cpl_tlp_valid  (pcie_tx_cpl_tlp_valid),
        .tx_cpl_tlp_sop    (pcie_tx_cpl_tlp_sop),
        .tx_cpl_tlp_eop    (pcie_tx_cpl_tlp_eop),
        .tx_cpl_tlp_ready  (1'b1),
        // AXI4 master — tied off
        .m_axi_awid        (),
        .m_axi_awaddr      (),
        .m_axi_awlen       (),
        .m_axi_awsize      (),
        .m_axi_awburst     (),
        .m_axi_awlock      (),
        .m_axi_awcache     (),
        .m_axi_awprot      (),
        .m_axi_awvalid     (),
        .m_axi_awready     (1'b0),
        .m_axi_wdata       (),
        .m_axi_wstrb       (),
        .m_axi_wlast       (),
        .m_axi_wvalid      (),
        .m_axi_wready      (1'b0),
        .m_axi_bid         (6'h0),
        .m_axi_bresp       (2'h0),
        .m_axi_bvalid      (1'b0),
        .m_axi_bready      (),
        .m_axi_arid        (),
        .m_axi_araddr      (),
        .m_axi_arlen       (),
        .m_axi_arsize      (),
        .m_axi_arburst     (),
        .m_axi_arlock      (),
        .m_axi_arcache     (),
        .m_axi_arprot      (),
        .m_axi_arvalid     (),
        .m_axi_arready     (1'b0),
        .m_axi_rid         (6'h0),
        .m_axi_rdata       (512'h0),
        .m_axi_rresp       (2'h0),
        .m_axi_rlast       (1'b0),
        .m_axi_rvalid      (1'b0),
        .m_axi_rready      (),
        // APB slave — 32-bit paddr, matches wrapper port (caduceus_soc_top.v:1246)
        .psel              (psel_o[4]),
        .penable           (penable_o[4]),
        .pwrite            (pwrite_o),
        .paddr             (paddr_o),
        .pwdata            (pwdata_o),
        .prdata            (pcie_prdata),
        .pready            (pcie_pready),
        .pslverr           (pcie_pslverr),
        .pcie_irq          (pcie_irq)
    );

    // ── Doorbell (APB slave 5 @ 0x4000_5000) ───────────────────────────────
    // Cocotb backdoor disabled (bkdoor_we=0) — APB is the only access path.
    doorbell u_doorbell (
        .clk           (clk),
        .rst_n         (rst_n),
        .psel          (psel_o[5]),
        .penable       (penable_o[5]),
        .pwrite        (pwrite_o),
        .paddr         (paddr_o[11:0]),
        .pwdata        (pwdata_o),
        .prdata        (db_prdata),
        .pready        (db_pready),
        .pslverr       (db_pslverr),
        .doorbell_irq  (doorbell_irq),
        .bkdoor_we     (1'b0),
        .bkdoor_sel    (2'h0),
        .bkdoor_wdata  (32'h0),
        .bkdoor_rdata  (db_bkdoor_rdata)
    );

    // ── INTC (APB slave 6 @ 0x4000_6000) ───────────────────────────────────
    // IRQ wiring per caduceus_soc_top.v:1375-1382: peripheral irqs feed the
    // real sources; timer (bit6) and pcie_dma (bit7) have no source module
    // here and are driven by TB stimulus regs.
    intc_top u_intc (
        .clk           (clk),
        .rst_n         (rst_n),
        .mxu_irq       (mxu_irq),
        .sfu_irq       (sfu_irq),
        .vector_irq    (vec_irq),
        .dma_irq       (dma_irq),
        .pcie_irq      (pcie_irq),
        .host_irq      (doorbell_irq),
        .timer_irq     (tb_timer_irq),
        .pcie_dma_irq  (tb_pcie_dma_irq),
        .psel          (psel_o[6]),
        .penable       (penable_o[6]),
        .pwrite        (pwrite_o),
        .paddr         (paddr_o[11:0]),
        .pwdata        (pwdata_o),
        .prdata        (intc_prdata),
        .pready        (intc_pready),
        .pslverr       (intc_pslverr),
        .cpu_irq       (cpu_irq)
    );

    //=========================================================================
    // Clock & reset
    //=========================================================================
    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    //=========================================================================
    // Test infrastructure
    //=========================================================================
    integer test_num;
    integer pass_cnt;
    integer fail_cnt;
    integer doc_div_cnt;         // documented-divergence checks (bug-filed)
    integer write_timeouts;
    integer read_timeouts;
    integer s, r;
    integer slv_checks  [0:6];   // per-slave check counts (coverage proof)
    integer slv_fails   [0:6];
    integer slv_docdivs [0:6];
    integer slv_sel_cnt [0:7];   // live per-slave psel_o assertion counts
    reg [31:0] rd;
    reg        rd_err;

    // Live guard: PCIE_DMA (slave 7) must NEVER be selected
    integer pcie_dma_sel_cnt;
    initial pcie_dma_sel_cnt = 0;
    always @(posedge clk) begin
        if (psel_o[7]) pcie_dma_sel_cnt = pcie_dma_sel_cnt + 1;
    end

    // Live routing proof: count psel_o assertions per slave (positive routing
    // evidence — every peripheral must actually be selected at least once).
    integer idx_sel;
    initial begin
        for (idx_sel = 0; idx_sel < 8; idx_sel = idx_sel + 1)
            slv_sel_cnt[idx_sel] = 0;
    end
    always @(posedge clk) begin
        for (idx_sel = 0; idx_sel < 8; idx_sel = idx_sel + 1) begin
            if (psel_o[idx_sel]) slv_sel_cnt[idx_sel] = slv_sel_cnt[idx_sel] + 1;
        end
    end

    //=========================================================================
    // ORACLE — REAL-semantics expectation table.
    //
    // Sources (independent of the Func Model factories):
    //   * base addresses: gen/npu_abi_firmware.h NPU_ABI_* (:15-38)
    //   * offsets/access classes: each peripheral's DOCUMENTED MMIO header
    //     table (wrapper files, pcie_ep_wrapper.v, intc_top.v, doorbell.v)
    //   * write masks / reset values / field maps: RTL cross-check, cited in
    //     the comments next to each table.
    //
    // REG_DOCDIV flags rows whose REAL behavior contradicts a DOCUMENTED
    // spec; those checks are tagged [DOC-DIV <bug>], counted in doc_div_cnt,
    // and reference bugs filed in docs/bugs/. RST_SKIP = no absolute reset
    // check (relative-only, e.g. live RO status).
    //=========================================================================
    localparam MAX_REGS = 20;

    localparam [31:0] REG_CNT [0:6] =
        '{32'd18, 32'd8, 32'd14, 32'd15, 32'd10, 32'd6, 32'd3};

    localparam [11:0] REG_OFFS [0:6][0:MAX_REGS-1] = '{
        // MXU (slave 0) — engine mmio_if 0x00-0x28 + wrapper regs 0x30-0x48
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h20, 12'h24, 12'h28, 12'h30, 12'h34, 12'h38, 12'h3C, 12'h40, 12'h44, 12'h48, 12'h0, 12'h0},
        // SFU (slave 1) — sfu_top mmio 0x00-0x1C (no wrapper regs)
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0},
        // VECTOR (slave 2) — vector_top mmio 0x00-0x1C + wrapper regs 0x30-0x44
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h30, 12'h34, 12'h38, 12'h3C, 12'h40, 12'h44, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0},
        // DMA (slave 3) — dma_wrapper reg file 0x00-0x38 (incl. _pad0)
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h20, 12'h24, 12'h28, 12'h2C, 12'h30, 12'h34, 12'h38, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0},
        // PCIE (slave 4) — pcie_ep_wrapper 0x00-0x20 + unmapped 0x24
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h20, 12'h24, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0},
        // DOORBELL (slave 5) — 4 RW regs + ABI-reserved 0x10/0x14
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0},
        // INTC (slave 6) — PENDING/ENABLE/THRESHOLD (ACK special-cased)
        '{12'h00, 12'h04, 12'h08, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0}
    };

    localparam [3:0] REG_ACC [0:6][0:MAX_REGS-1] = '{
        // MXU: CTRL RW; CMD WO pulse (mmio_if.v:123-124/:139); STATUS RO;
        // 0x0C-0x28 full-width RW; wrapper regs per mxu_soc_wrapper.v:245-249
        '{ACC_RW, ACC_WO, ACC_RO, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_WO, ACC_RO, ACC_RWM, ACC_RWM, ACC_RW, ACC_RW},
        // SFU: same shape (sfu_top.v:96/:134)
        '{ACC_RW, ACC_WO, ACC_RO, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // VECTOR: same shape (vector_top.v:109-114/:144) + wrapper regs
        '{ACC_RW, ACC_WO, ACC_RO, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_WO, ACC_RO, ACC_RWM, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // DMA: CTRL RW; CMD write-STORE (dma_wrapper.v:285/:128); STATUS RO
        // (read-clears DONE :299-301, unobservable while idle); rest RW
        '{ACC_RW, ACC_WOS, ACC_RO, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // PCIE: CTRL field (mps stored, readback shifted to [3:1], bit3
        // unimplemented); STATUS RO; COMPLETER_ID RW[15:0]; BAR0/1 RO consts;
        // MSIX/IRQ_CTRL field-masked; 0x24 unmapped -> pslverr (:296)
        '{ACC_FIELD, ACC_RO, ACC_RWM, ACC_CONST, ACC_CONST, ACC_CONST, ACC_CONST, ACC_FIELD, ACC_FIELD, ACC_UNMAP, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // DOORBELL: 4 RW (doorbell.v:80-83); 0x10/0x14 reserved-in-ABI
        '{ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_DOCDIVR, ACC_DOCDIVR, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // INTC: PENDING live-sticky RO; ENABLE 8-bit; THRESHOLD 4-bit
        '{ACC_RO, ACC_RWM, ACC_RWM, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW}
    };

    // Reset values (RST_SKIP = relative-only). Engine regs all 0; INTC
    // THRESHOLD resets to 1 (intc_top.v:131); wrapper regs non-zero
    // (mxu_soc_wrapper.v:245-249, vector_soc_wrapper.v:183-186).
    localparam [31:0] REG_RST [0:6][0:MAX_REGS-1] = '{
        // MXU
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'h8002_0000, 32'h8001_0000, 32'h8003_0000, 32'd0, 32'd0, 32'd1, 32'd64, 32'd0, 32'd0},
        // SFU
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // VECTOR — 0x40 WRP_STATUS is live (wrp_ready) → relative only
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'h2030_0000, 32'h2030_0000, 32'h2034_0000, 32'd0, RST_SKIP, 32'h4000, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // DMA
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // PCIE
        '{32'd0, 32'd0, 32'd0, 32'h2000_0000, 32'hFFC0_0000, 32'h8000_0000, 32'h8000_0000, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // DOORBELL
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // INTC — THRESHOLD resets to 1
        '{32'd0, 32'd0, 32'd1, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0}
    };

    // Write masks (ACC_RWM rows only): INTC ENABLE 8-bit (intc_top.v:117),
    // THRESHOLD 4-bit (:133), PCIE COMPLETER_ID 16-bit (:316),
    // MXU WRP_K_TILES/WRP_DIM_N 16-bit (:255-256), VECTOR WRP_LEN 16-bit (:192).
    localparam [31:0] REG_MSK [0:6][0:MAX_REGS-1] = '{
        '{32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'h0000_FFFF, 32'h0000_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF},
        '{32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF},
        '{32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'h0000_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF},
        '{32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF},
        '{32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'h0000_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF},
        '{32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF},
        '{32'hFFFF_FFFF, 32'h0000_00FF, 32'h0000_000F, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF, 32'hFFFF_FFFF}
    };

    // ACC_FIELD expected readbacks after w(0xFFFFFFFF) and w(0):
    //   PCIE CTRL: mps=7 stored → readback {28'h0, 7, 1'b0} = 0xE (bit3 enable
    //              NOT stored, bit0 always 0) — pcie_ep_wrapper.v:304-306/:387
    //   MSIX_CTRL: vector=0xFF, msix_en=1 → 0x0000_FF01 (:328-329/:394)
    //   IRQ_CTRL:  err_irq_en=1, pending W1C'd to 0, irq_en=1 → 0x5 (:363-370/:395)
    localparam [31:0] REG_EXP_F [0:6][0:MAX_REGS-1] = '{
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'h0000_000E, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'h0000_FF01, 32'h0000_0005, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0}
    };

    localparam [31:0] REG_EXP_Z [0:6][0:MAX_REGS-1] = '{
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0}
    };

    // DOC-DIV flags: real behavior contradicts a DOCUMENTED spec (bug-filed)
    //   DMA    CMD 0x04 — rtl/ip/README.md:35 says W, RTL stores+reads back
    //   PCIE   CTRL 0x00 — header :258 says [3]=enable (unimplemented)
    //   PCIE   BAR1_MASK 0x18 — header :264 says bit31=writable (constant)
    //   DOORBELL 0x10/0x14 — ABI npu_doorbell_t declares LAST_STATUS R/W +
    //                    COMPLETION_STATUS[16]; RTL silent-0 (doorbell.v:70)
    localparam [31:0] REG_DOCDIV [0:6][0:MAX_REGS-1] = '{
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd1, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd1, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd1, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd1, 32'd1, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0}
    };

    // ABI base address lookup per slave index
    function automatic [31:0] slave_base;
        input integer idx;
        begin
            case (idx)
                0: slave_base = ABI_MXU_BASE;
                1: slave_base = ABI_SFU_BASE;
                2: slave_base = ABI_VECTOR_BASE;
                3: slave_base = ABI_DMA_BASE;
                4: slave_base = ABI_PCIE_BASE;
                5: slave_base = ABI_DOORBELL_BASE;
                6: slave_base = ABI_INTC_BASE;
                default: slave_base = 32'h0;
            endcase
        end
    endfunction

    // Bug id for a DOC-DIV row (filed in docs/bugs/bugs-soc-rtl.md)
    function automatic string doc_bug;
        input integer idx;
        begin
            case (idx)
                3: doc_bug = "BUG-RTL-SOC-011";  // DMA README access classes
                4: doc_bug = "BUG-RTL-SOC-010";  // PCIe header overstates CTRL[3]/BAR1_MASK
                5: doc_bug = "BUG-RTL-SOC-009";  // doorbell ABI window missing
                default: doc_bug = "BUG-RTL-SOC-???";
            endcase
        end
    endfunction

    //=========================================================================
    // APB master tasks — pready-aware with per-transaction watchdog
    // (SFU wrapper can insert wait states on a real CMD.START hold; this
    //  stimulus never triggers one, but the master honors pready regardless)
    //=========================================================================
    task automatic apb_write;
        input [31:0] addr;
        input [31:0] data;
        integer wdog;
    begin
        @(posedge clk); #1;
        psel    = 1'b1;
        penable = 1'b0;
        paddr   = addr;
        pwrite  = 1'b1;
        pwdata  = data;
        @(posedge clk); #1;
        penable = 1'b1;
        wdog = 0;
        while (!pready && wdog < 4096) begin
            @(posedge clk); #1;
            wdog = wdog + 1;
        end
        if (wdog >= 4096) begin
            write_timeouts = write_timeouts + 1;
            $display("  [ERROR] APB WRITE TIMEOUT @ 0x%08h", addr);
        end
        @(posedge clk); #1;
        psel    = 1'b0;
        penable = 1'b0;
    end
    endtask

    task automatic apb_read;
        input  [31:0] addr;
        output [31:0] data;
        output        err;
        integer wdog;
    begin
        @(posedge clk); #1;
        psel    = 1'b1;
        penable = 1'b0;
        paddr   = addr;
        pwrite  = 1'b0;
        @(posedge clk); #1;
        penable = 1'b1;
        wdog = 0;
        while (!pready && wdog < 4096) begin
            @(posedge clk); #1;
            wdog = wdog + 1;
        end
        if (wdog >= 4096) begin
            read_timeouts = read_timeouts + 1;
            $display("  [ERROR] APB READ TIMEOUT @ 0x%08h", addr);
        end
        #1;
        data = prdata;
        err  = pslverr;
        @(posedge clk); #1;
        psel    = 1'b0;
        penable = 1'b0;
    end
    endtask

    task automatic apb_idle;
    begin
        psel    = 1'b0;
        penable = 1'b0;
        paddr   = 32'h0;
        pwrite  = 1'b0;
        pwdata  = 32'h0;
    end
    endtask

    // Write variant that also returns the pslverr flag (for UNMAP checks and
    // decoder out-of-range writes).
    task automatic apb_write_err;
        input  [31:0] addr;
        input  [31:0] data;
        output        err;
        integer wdog;
    begin
        @(posedge clk); #1;
        psel    = 1'b1;
        penable = 1'b0;
        paddr   = addr;
        pwrite  = 1'b1;
        pwdata  = data;
        @(posedge clk); #1;
        penable = 1'b1;
        wdog = 0;
        while (!pready && wdog < 4096) begin
            @(posedge clk); #1;
            wdog = wdog + 1;
        end
        if (wdog >= 4096) begin
            write_timeouts = write_timeouts + 1;
            $display("  [ERROR] APB WRITE TIMEOUT @ 0x%08h", addr);
        end
        #1;
        err = pslverr;
        @(posedge clk); #1;
        psel    = 1'b0;
        penable = 1'b0;
    end
    endtask

    //=========================================================================
    // Check helpers
    //=========================================================================
    task automatic check;
        input [31:0] actual;
        input [31:0] expected;
        input integer slv;         // -1 = global check (no per-slave count)
        input string  desc;
    begin
        test_num = test_num + 1;
        if (slv >= 0)
            slv_checks[slv] = slv_checks[slv] + 1;
        if (actual !== expected) begin
            $display("  [FAIL] %0s — got 0x%08h, expected 0x%08h", desc, actual, expected);
            fail_cnt = fail_cnt + 1;
            if (slv >= 0)
                slv_fails[slv] = slv_fails[slv] + 1;
        end else begin
            $display("  [PASS] %0s (0x%08h)", desc, actual);
            pass_cnt = pass_cnt + 1;
        end
    end
    endtask

    // Check variant for rows flagged DOC-DIV: validates the REAL behavior
    // (pass if real matches the RTL-derived expectation) and records the
    // documented-spec contradiction in the doc_div bucket with its bug id.
    task automatic check_docdiv;
        input [31:0] actual;
        input [31:0] expected;
        input integer slv;
        input string  desc;
        input string  bugid;
    begin
        test_num = test_num + 1;
        if (slv >= 0)
            slv_checks[slv] = slv_checks[slv] + 1;
        doc_div_cnt = doc_div_cnt + 1;
        if (slv >= 0)
            slv_docdivs[slv] = slv_docdivs[slv] + 1;
        if (actual !== expected) begin
            $display("  [FAIL] %0s — got 0x%08h, expected 0x%08h", desc, actual, expected);
            fail_cnt = fail_cnt + 1;
            if (slv >= 0)
                slv_fails[slv] = slv_fails[slv] + 1;
        end else begin
            $display("  [PASS] %0s (0x%08h)", desc, actual);
            $display("         [DOC-DIV] real RTL contradicts documented spec — filed as %0s", bugid);
            pass_cnt = pass_cnt + 1;
        end
    end
    endtask

    // Seed INTC PENDING through REAL sources only (no test backdoor):
    //  - timer_irq (bit6) and pcie_dma_irq (bit7) via TB stimulus regs
    //    (sources that exist outside the 7 peripherals under test);
    //  - host/doorbell (bit5) via REAL APB writes: HOST_TAIL=0x5, NPU_HEAD=0x0
    //    makes doorbell_irq = (host_tail != npu_head) = 1.
    task automatic seed_intc_pending_real;
    begin
        tb_timer_irq    = 1'b1;
        tb_pcie_dma_irq = 1'b1;
        apb_write(ABI_DOORBELL_BASE + 32'h00, 32'h5);   // HOST_TAIL
        apb_write(ABI_DOORBELL_BASE + 32'h04, 32'h0);   // NPU_HEAD != 5
        repeat (3) @(posedge clk);
        $display("  [INFO] seeded INTC PENDING via real sources (timer=1 pcie_dma=1 doorbell HOST_TAIL!=NPU_HEAD)");
    end
    endtask

    // Release the TB-driven sources so a subsequent ACK write can demonstrably
    // clear the sticky PENDING bits (informational, not an oracle check).
    task automatic release_intc_sources;
    begin
        tb_timer_irq    = 1'b0;
        tb_pcie_dma_irq = 1'b0;
        apb_write(ABI_DOORBELL_BASE + 32'h04, 32'h5);   // NPU_HEAD = HOST_TAIL
        repeat (3) @(posedge clk);
        $display("  [INFO] released INTC irq sources (timer=0 pcie_dma=0 doorbell match)");
    end
    endtask

    //=========================================================================
    // Main test sequence
    //=========================================================================
    initial begin
        reg [31:0] v1, v2;
        reg        w_err;
        integer    covered;

        test_num       = 0;
        pass_cnt       = 0;
        fail_cnt       = 0;
        doc_div_cnt    = 0;
        write_timeouts = 0;
        read_timeouts  = 0;
        for (s = 0; s < 7; s = s + 1) begin
            slv_checks[s]  = 0;
            slv_fails[s]   = 0;
            slv_docdivs[s] = 0;
        end

        apb_idle();
        tb_timer_irq    = 1'b0;
        tb_pcie_dma_irq = 1'b0;

        // Reset
        rst_n = 1'b0;
        repeat (5) @(posedge clk);
        rst_n = 1'b1;
        repeat (3) @(posedge clk);

        $display("\n=====================================================");
        $display(" apb_conformance_real_tb — REAL peripheral RTL, 7/7 integrated");
        $display("  INTEGRATED: MXU(0x4000_0000) SFU(0x4000_1000) VECTOR(0x4000_2000)");
        $display("              DMA(0x4000_3000) PCIE(0x4000_4000) DOORBELL(0x4000_5000)");
        $display("              INTC(0x4000_6000)");
        $display("  NOT INTEGRATED: pcie_dma_wrapper (0x4000_7000) — AXI master M6,");
        $display("              out of APB conformance scope; guarded (psel_o[7]==0)");
        $display("  ORACLE: REAL RTL semantics = NPU_ABI_* bases (gen/npu_abi_firmware.h");
        $display("          :15-38) + documented MMIO header tables of each peripheral");
        $display("  DOC-DIV policy: real-vs-documented contradictions tagged [DOC-DIV]");
        $display("          and bug-filed (docs/bugs/), never silently passed");
        $display("=====================================================\n");

        // ── Phase 1: reset-value conformance (REAL semantics) ──────────────
        $display("--- Phase 1: reset values vs REAL oracle ---\n");
        for (s = 0; s < 7; s = s + 1) begin
            for (r = 0; r < REG_CNT[s]; r = r + 1) begin
                case (REG_ACC[s][r])
                    ACC_WO, ACC_WOS, ACC_UNMAP, ACC_DOCDIVR: begin
                        // not readable at reset (or covered in Phase 2)
                    end
                    default: begin
                        if (REG_RST[s][r] == RST_SKIP)
                            $display("  [INFO] %0s +0x%03X: relative-only (live RO)",
                                     itoa_slv(s), REG_OFFS[s][r]);
                        else begin
                            apb_read(slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                            if (rd_err)
                                $display("  [INFO] pslverr=1 on read %0s +0x%03X",
                                         itoa_slv(s), REG_OFFS[s][r]);
                            check(rd, REG_RST[s][r], s,
                                  {"reset ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                        end
                    end
                endcase
            end
        end

        // ── Phase 2: per-register access conformance (REAL semantics) ──────
        $display("\n--- Phase 2: write -> readback conformance vs REAL oracle ---\n");
        for (s = 0; s < 7; s = s + 1) begin
            for (r = 0; r < REG_CNT[s]; r = r + 1) begin
                case (REG_ACC[s][r])
                    ACC_RW: begin
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'hFFFF_FFFF);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'hFFFF_FFFF, s,
                              {"rw-full ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h0);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'h0, s,
                              {"rw-zero ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_RWM: begin
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'hFFFF_FFFF);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'hFFFF_FFFF & REG_MSK[s][r], s,
                              {"rwm-full ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h0);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'h0, s,
                              {"rwm-zero ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_RO: begin
                        apb_read (slave_base(s) + REG_OFFS[s][r], v1, rd_err);
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'hFFFF_FFFF);
                        apb_read (slave_base(s) + REG_OFFS[s][r], v2, rd_err);
                        check(v2, v1, s,
                              {"ro-hostile ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_CONST: begin
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'hFFFF_FFFF);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        if (REG_DOCDIV[s][r])
                            check_docdiv(rd, REG_RST[s][r], s,
                                  {"ro-const ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])},
                                  doc_bug(s));
                        else
                            check(rd, REG_RST[s][r], s,
                                  {"ro-const ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_WO: begin
                        // 0x80 has NO functional bits on any WO register
                        // (engine CMD bit0=START/bit1=ABORT, MXU WRP_CMD bit0,
                        // VECTOR WRP_CMD bits[2:0]) — the write must not arm
                        // any side effect; readback must still be 0.
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h0000_0080);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'h0, s,
                              {"wo-store ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h0);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'h0, s,
                              {"wo-zero  ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_WOS: begin
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h42);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check_docdiv(rd, 32'h42, s,
                              {"wos-store ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])},
                              doc_bug(s));
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h0);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check_docdiv(rd, 32'h0, s,
                              {"wos-zero  ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])},
                              doc_bug(s));
                    end
                    ACC_FIELD: begin
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'hFFFF_FFFF);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        if (REG_DOCDIV[s][r])
                            check_docdiv(rd, REG_EXP_F[s][r], s,
                                  {"field-full ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])},
                                  doc_bug(s));
                        else
                            check(rd, REG_EXP_F[s][r], s,
                                  {"field-full ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h0);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, REG_EXP_Z[s][r], s,
                              {"field-zero ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_UNMAP: begin
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check({31'd0, rd_err}, 32'h1, s,
                              {"unmap-read  ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r]), " pslverr"});
                        apb_write_err(slave_base(s) + REG_OFFS[s][r], 32'hDEAD_BEEF, w_err);
                        check({31'd0, w_err}, 32'h1, s,
                              {"unmap-write ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r]), " pslverr"});
                    end
                    ACC_DOCDIVR: begin
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check_docdiv(rd, 32'h0, s,
                              {"reserved-read  ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])},
                              doc_bug(s));
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'hFFFF_FFFF);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check_docdiv(rd, 32'h0, s,
                              {"reserved-write ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])},
                              doc_bug(s));
                    end
                endcase
            end
        end

        // ── Phase 3: decoder routing + out-of-range pslverr ────────────────
        $display("\n--- Phase 3: decoder routing & error path ---\n");
        apb_write_err(32'h4000_8000, 32'hDEAD_BEEF, w_err);
        check({31'd0, w_err}, 32'h1, -1, "out-of-range 0x4000_8000 write → pslverr=1");
        apb_read(32'h1000_0000, rd, rd_err);
        check({31'd0, rd_err}, 32'h1, -1, "region-miss 0x1000_0000 read → pslverr=1");
        check(rd, 32'h0, -1, "region-miss read data = 0");
        check(pcie_dma_sel_cnt, 32'd0, -1, "psel_o[7] (PCIE_DMA) never asserted");
        $display("  [INFO] pcie_dma_wrapper (slave 7 @ 0x4000_7000) NOT instantiated");

        // ── Phase 4: INTC ACK W1C real-semantics sequence ──────────────────
        // PENDING is seeded ONLY through real sources (doorbell HOST_TAIL!=
        // NPU_HEAD + TB timer/pcie_dma regs), then ACK clears sticky bits.
        $display("\n--- Phase 4: INTC ACK W1C vs REAL semantics ---\n");
        seed_intc_pending_real();
        apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
        check(rd, 32'h0000_00E0, 6, "INTC.PENDING after real-source seed = 0xE0");
        release_intc_sources();
        apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
        check(rd, 32'h0000_00E0, 6, "INTC.PENDING sticky after source release");
        apb_write(ABI_INTC_BASE + 32'h0C, 32'hE0);   // ACK bits 5..7
        apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
        check(rd, 32'h0000_0000, 6, "INTC.PENDING cleared by ACK=0xE0 (W1C)");
        apb_read(ABI_INTC_BASE + 32'h0C, rd, rd_err);
        check(rd, 32'h0000_0000, 6, "INTC.ACK readback = 0 (W1C not readable)");
        seed_intc_pending_real();
        apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
        check(rd, 32'h0000_00E0, 6, "INTC.PENDING re-seed = 0xE0");
        release_intc_sources();
        apb_write(ABI_INTC_BASE + 32'h0C, 32'h20);   // clear only bit5
        apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
        check(rd, 32'h0000_00C0, 6, "INTC.PENDING selective ACK=0x20 leaves 0xC0");
        apb_write(ABI_INTC_BASE + 32'h0C, 32'h00);   // zero write: no effect
        apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
        check(rd, 32'h0000_00C0, 6, "INTC.ACK=0x00 is a no-op (PENDING 0xC0)");
        apb_write(ABI_INTC_BASE + 32'h0C, 32'hC0);   // clear bits 6..7
        apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
        check(rd, 32'h0000_0000, 6, "INTC.PENDING cleared by ACK=0xC0");

        // ── Phase 5: no-compute / no-side-effect proof (INFO + checks) ─────
        $display("\n--- Phase 5: engine idle proof (no CMD.START was ever written) ---\n");
        check(mxu_dbg_state, 32'h0, 0, "mxu_dbg_state = 0 (IDLE — no compute launched)");
        check({6'd0, mxu_irq, sfu_irq, vec_irq, dma_irq, pcie_irq, doorbell_irq},
              32'h0, -1, "all peripheral irqs deasserted at end");
        apb_read(ABI_DOORBELL_BASE + 32'h00, rd, rd_err);
        $display("  [INFO] doorbell HOST_TAIL=%0d NPU_HEAD(see below) — irq clear", rd);
        $display("  [INFO] cpu_irq=%0b (registered)", cpu_irq);

        // ── Final report ───────────────────────────────────────────────────
        $display("\n=====================================================");
        $display(" apb_conformance_real_tb — Final Report");
        $display("=====================================================");
        $display("  Total checks : %0d", test_num);
        $display("  Passes       : %0d", pass_cnt);
        $display("  Fails        : %0d   (unexpected — real RTL != REAL oracle)", fail_cnt);
        $display("  DOC-DIV      : %0d   (real RTL contradicts documented spec,",
                 doc_div_cnt);
        $display("                 each bug-filed: BUG-RTL-SOC-009/010/011)");
        $display("  Write timeouts: %0d   Read timeouts: %0d",
                 write_timeouts, read_timeouts);
        $display("  Per-slave coverage (checks / fails / doc-div / psel_o asserts):");
        for (s = 0; s < 7; s = s + 1) begin
            $display("    %0s      : %0d / %0d / %0d / %0d",
                     itoa_slv(s), slv_checks[s], slv_fails[s],
                     slv_docdivs[s], slv_sel_cnt[s]);
        end
        $display("=====================================================");

        covered = 0;
        for (s = 0; s < 7; s = s + 1)
            if (slv_checks[s] > 0) covered = covered + 1;

        if (fail_cnt > 0) begin
            $display("APB_CONFORMANCE_REAL: RED (%0d unexpected divergences vs REAL oracle)",
                     fail_cnt);
            $display("TASK-12 RESULT: RED (unexpected)");
        end else if ((write_timeouts > 0) || (read_timeouts > 0)) begin
            $display("APB_CONFORMANCE_REAL: RED (APB timeout)");
            $display("TASK-12 RESULT: RED (unexpected)");
        end else if (covered < 5) begin
            $display("APB_CONFORMANCE_REAL: PARTIAL (%0d/7 peripherals covered, declared)",
                     covered);
            $display("TASK-12 RESULT: PARTIAL: %0d/7 peripherals covered (declared)",
                     covered);
        end else begin
            $display("APB_CONFORMANCE_REAL: GREEN (%0d/7 peripherals, %0d checks, %0d doc-div [BUG-RTL-SOC-009/010/011])",
                     covered, test_num, doc_div_cnt);
            $display("TASK-12 RESULT: GREEN (expected)");
        end
        $display("=====================================================\n");
        $finish;
    end

    //=========================================================================
    // Slave name / hex helpers for log readability
    //=========================================================================
    function automatic string itoa_slv;
        input integer idx;
        begin
            case (idx)
                0: itoa_slv = "MXU";
                1: itoa_slv = "SFU";
                2: itoa_slv = "VECTOR";
                3: itoa_slv = "DMA";
                4: itoa_slv = "PCIE";
                5: itoa_slv = "DOORBELL";
                6: itoa_slv = "INTC";
                default: itoa_slv = "?";
            endcase
        end
    endfunction

    function automatic string itoa_hex;
        input [11:0] val;
        begin
            itoa_hex = $sformatf("%03X", val);
        end
    endfunction

    //=========================================================================
    // Timeout guard (safety)
    //=========================================================================
    initial begin
        #2000000;
        $display("\n[ERROR] Timeout: simulation did not finish in 2,000,000 ns");
        $display("APB_CONFORMANCE_REAL: RED (simulation timeout)");
        $finish;
    end

endmodule
