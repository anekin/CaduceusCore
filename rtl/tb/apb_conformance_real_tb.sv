//=============================================================================
// apb_conformance_real_tb.sv — APB conformance against REAL peripheral RTL
// CaduceusCore / soc-rtl-review-remediation todo 3 (RED negative test)
//
// Purpose: instantiate the REAL rtl apb_decoder plus the REAL peripheral RTL
// (mxu_soc_wrapper / sfu_soc_wrapper / vector_soc_wrapper / dma_wrapper /
// pcie_ep_wrapper / doorbell / intc_top), wired per rtl/soc/caduceus_soc_top.v
// address map, and drive the SAME register-conformance stimulus as the
// model-slave TB (rtl/tb/apb_register_conformance_tb.sv:100-159). The oracle
// is the model-slave expectation table (that TB's REG_CNT/REG_OFFS/REG_ACC/
// REG_RST, :200-256 — pinned by sim/tests/test_apb_register_conformance.py
// against the Func Model factories), with base addresses transcribed from the
// INDEPENDENT ABI header gen/npu_abi_firmware.h:17-38 (NPU_ABI_* constants).
//
// REAL peripheral behavior that differs from the model-slave expectation
// table is EXPOSED AS FAILURES — that divergence inventory is the RED
// deliverable of this todo. Do NOT modify apb_decoder.v or any peripheral RTL.
//
// Expected RED divergences (predicted from RTL source, exposed at runtime):
//   INTC    THRESHOLD reset value = 1 (model expects 0); ENABLE/THRESHOLD are
//           8/4-bit field masks; ACK readback = 0; PENDING is a live 8-bit
//           status register (sticky, driven by irq sources), not a static 0.
//   DOORBELL real RTL has only 4 RW registers 0x00-0x0C — HOST_HEAD/NPU_TAIL
//           are RW (model expects RO), offsets 0x10/0x14 do not exist
//           (model expects LAST_STATUS/COMPLETION_STATUS rw).
//   PCIE     real pcie_ep_wrapper register layout (CTRL 0x00, STATUS 0x04,
//           COMPLETER_ID 0x08, BAR0_BASE 0x0C ... IRQ_CTRL 0x20) differs from
//           the Func Model factory layout used by the model table; BAR regs
//           are RO constants; unmapped offsets (e.g. 0x24) assert pslverr.
//   MXU/SFU/VECTOR/DMA: CMD is write-only pulse (readback 0 — model w-store
//           expects readback 0x42).
//
// Wiring notes (SoC fidelity):
//   * Peripherals' irq outputs feed intc_top exactly as caduceus_soc_top.v
//     does (mxu->bit0 ... doorbell->bit5). timer_irq (bit6) and pcie_dma_irq
//     (bit7) have no source module in this TB, so they are driven by TB regs
//     (they are SoC-external stimulus by nature).
//   * AXI4 master ports of all peripherals are tied off (ready=0, no slave
//     model). No CMD write in the stimulus has START bit set, so no compute
//     is launched and the tie-off cannot stall APB. (MXU/SFU/VECTOR/DMA
//     pready is unconditional 1'b1; SFU can stall pready only on a real
//     CMD.START hold, which this stimulus never produces.)
//   * PCIe TLP RX idles (valid=0), tx_cpl_tlp_ready=1; doorbell bkdoor_* = 0.
//   * pcie_dma_wrapper (slave 7 @ 0x4000_7000) is NOT instantiated — it is
//     the AXI master M6, outside this APB conformance scope; a live guard
//     counts psel_o[7] assertions (must stay 0).
//
// Usage (EDA server sz0001 only — VCS):
//   source /NAS/Tools/methodology/modules/init/bash
//   module load vcs/vcs_2023.12sp2
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps +v2k +lint=all +warn=all \
//       -f rtl/cpu/ibex.flist -f rtl/ip/verilog-axi.flist \
//       -f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist \
//       rtl/tb/apb_conformance_real_tb.sv \
//       -top apb_conformance_real_tb -o simv_apb_conformance_real -l compile_apb_real.log
//   ./simv_apb_conformance_real | tee run_apb_real.log
// Verdict: "APB_CONFORMANCE_REAL: RED (N divergences)" — RED is EXPECTED.
//=============================================================================

`timescale 1ns / 1ps

module apb_conformance_real_tb;

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam CLK_HALF = 5;               // 100 MHz

    // Access codes (same encoding as apb_register_conformance_tb.sv:37-41)
    localparam [1:0] ACC_RW  = 2'd0;       // read-write, overwrite semantics
    localparam [1:0] ACC_R   = 2'd1;       // read-only, writes ignored
    localparam [1:0] ACC_W   = 2'd2;       // write-only (FM model style)
    localparam [1:0] ACC_W1C = 2'd3;       // write-1-to-clear

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
    integer write_timeouts;
    integer read_timeouts;
    integer s, r;
    reg [31:0] rd;
    reg        rd_err;

    // Live guard: PCIE_DMA (slave 7) must NEVER be selected
    integer pcie_dma_sel_cnt;
    initial pcie_dma_sel_cnt = 0;
    always @(posedge clk) begin
        if (psel_o[7]) pcie_dma_sel_cnt = pcie_dma_sel_cnt + 1;
    end

    //=========================================================================
    // Oracle — model-slave expectation table, transcribed VERBATIM from
    // rtl/tb/apb_register_conformance_tb.sv:200-256 (REG_CNT / REG_OFFS /
    // REG_ACC / REG_RST). That table is pinned by
    // sim/tests/test_apb_register_conformance.py against the Func Model
    // peripheral factories (sim/models/apb_peripheral.py) — it is the
    // declared reference for expected semantics. Base addresses are the
    // NPU_ABI_* constants above (independent ABI source).
    //=========================================================================
    localparam MAX_REGS = 15;

    localparam [31:0] REG_CNT [0:6] = '{32'd11, 32'd8, 32'd8, 32'd14, 32'd10, 32'd6, 32'd4};

    localparam [11:0] REG_OFFS [0:6][0:MAX_REGS-1] = '{
        // MXU (slave 0)
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h20, 12'h24, 12'h28, 12'h0, 12'h0, 12'h0, 12'h0},
        // SFU (slave 1)
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0},
        // VECTOR (slave 2)
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0},
        // DMA (slave 3)
        '{12'h00, 12'h04, 12'h08, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h20, 12'h24, 12'h28, 12'h2C, 12'h30, 12'h34, 12'h38, 12'h0},
        // PCIe (slave 4)
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h20, 12'h24, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0},
        // DOORBELL (slave 5)
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h10, 12'h14, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0},
        // INTC (slave 6)
        '{12'h00, 12'h04, 12'h08, 12'h0C, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0, 12'h0}
    };

    localparam [1:0] REG_ACC [0:6][0:MAX_REGS-1] = '{
        // MXU: CTRL rw, CMD w, STATUS r, rest rw
        '{ACC_RW, ACC_W, ACC_R, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // SFU: CTRL rw, CMD w, STATUS r, rest rw
        '{ACC_RW, ACC_W, ACC_R, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // VECTOR: CTRL rw, CMD w, STATUS r, rest rw
        '{ACC_RW, ACC_W, ACC_R, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // DMA: CTRL rw, CMD w, STATUS r, rest rw
        '{ACC_RW, ACC_W, ACC_R, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // PCIe: all rw (Func Model factory declares 10 rw registers)
        '{ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // DOORBELL: HOST_TAIL w, NPU_HEAD rw, HOST_HEAD r, NPU_TAIL r, LAST_STATUS rw, COMPLETION_STATUS rw
        '{ACC_W, ACC_RW, ACC_R, ACC_R, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW},
        // INTC: PENDING r, ENABLE rw, THRESHOLD rw, ACK w1c
        '{ACC_R, ACC_RW, ACC_RW, ACC_W1C, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW, ACC_RW}
    };

    localparam [31:0] REG_RST [0:6][0:MAX_REGS-1] = '{
        // MXU — all reset 0
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // SFU — all reset 0
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // VECTOR — all reset 0
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // DMA — all reset 0
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // PCIe — COMPLETER_ID=0x0001, MAX_PAYLOAD_SIZE=3, BAR0_BASE=0x2000_0000,
        // BAR0_MASK=0x003F_FFFF, BAR1_BASE=0x8000_0000, BAR1_MASK=0x7FFF_FFFF
        '{32'h0000_0001, 32'h0000_0003, 32'd0, 32'd0, 32'd0, 32'd0, 32'h2000_0000, 32'h003F_FFFF, 32'h8000_0000, 32'h7FFF_FFFF, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // DOORBELL — all reset 0
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0},
        // INTC — all reset 0
        '{32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0, 32'd0}
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

    //=========================================================================
    // Check helpers
    //=========================================================================
    task automatic check;
        input [31:0] actual;
        input [31:0] expected;
        input string  desc;
    begin
        test_num = test_num + 1;
        if (actual !== expected) begin
            $display("  [FAIL] %0s — got 0x%08h, expected 0x%08h", desc, actual, expected);
            fail_cnt = fail_cnt + 1;
        end else begin
            $display("  [PASS] %0s (0x%08h)", desc, actual);
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
        test_num       = 0;
        pass_cnt       = 0;
        fail_cnt       = 0;
        write_timeouts = 0;
        read_timeouts  = 0;

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
        $display("  ORACLE: model-slave expectation table (apb_register_conformance_tb.sv");
        $display("          :200-256) + NPU_ABI_* bases (gen/npu_abi_firmware.h:17-38)");
        $display("=====================================================\n");

        // ── Phase 1: reset-value conformance (rw + r + w1c registers) ─────
        $display("--- Phase 1: reset values vs model oracle ---\n");
        for (s = 0; s < 7; s = s + 1) begin
            for (r = 0; r < REG_CNT[s]; r = r + 1) begin
                if (REG_ACC[s][r] == ACC_W)
                    continue;   // write-only: no readable reset value
                apb_read(slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                if (rd_err)
                    $display("  [INFO] pslverr=1 on read %s +0x%03X", itoa_slv(s), REG_OFFS[s][r]);
                check(rd, REG_RST[s][r],
                      {"reset ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
            end
        end

        // ── Phase 2: per-register access conformance (write→readback) ─────
        $display("\n--- Phase 2: write -> readback conformance ---\n");
        for (s = 0; s < 7; s = s + 1) begin
            for (r = 0; r < REG_CNT[s]; r = r + 1) begin
                case (REG_ACC[s][r])
                    ACC_RW: begin
                        // Overwrite semantics: 0x3 then 0x6 → readback 0x6
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h3);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'h3,
                              {"rw-w0x3 ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h6);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'h6,
                              {"rw-ovrw ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_R: begin
                        // Hostile write must not change the value
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'hFFFF_FFFF);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, REG_RST[s][r],
                              {"r-hostile ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_W: begin
                        // Write-only: store + readback (Func Model semantics)
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h42);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'h42,
                              {"w-store ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_W1C: begin
                        // INTC.ACK — seed PENDING through REAL sources, then
                        // write ACK=0x00F0 → only bits 4..7 may clear (model
                        // oracle expects a 16-bit seed readback 0xFF0F; the
                        // real INTC is an 8-bit live sticky register — the
                        // divergence is the RED we want).
                        seed_intc_pending_real();
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h00F0);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'h0000_FF0F,
                              {"w1c-clr ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                        // re-seed; confirm unrelated bits survive a 0 write
                        seed_intc_pending_real();
                        apb_write(slave_base(s) + REG_OFFS[s][r], 32'h0000);
                        apb_read (slave_base(s) + REG_OFFS[s][r], rd, rd_err);
                        check(rd, 32'h0000_FFFF,
                              {"w1c-hold ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                endcase
            end
        end

        // ── Phase 3: decoder routing + out-of-range pslverr ────────────────
        $display("\n--- Phase 3: decoder routing & error path ---\n");
        begin
            reg got_err;
            got_err = 1'b0;
            @(posedge clk); #1;
            psel    = 1'b1;
            penable = 1'b0;
            paddr   = 32'h4000_8000;
            pwrite  = 1'b1;
            pwdata  = 32'hDEAD_BEEF;
            @(posedge clk); #1;
            penable = 1'b1;
            #1;
            got_err = pslverr;
            @(posedge clk); #1;
            psel    = 1'b0;
            penable = 1'b0;
            check({31'd0, got_err}, 32'h1, "out-of-range 0x4000_8000 → pslverr=1");
        end

        // ── Phase 4: PCIE_DMA (slave 7) never selected ─────────────────────
        $display("\n--- Phase 4: PCIE_DMA skip guard ---\n");
        check(pcie_dma_sel_cnt, 32'd0, "psel_o[7] (PCIE_DMA) never asserted");
        $display("  [INFO] pcie_dma_wrapper (slave 7 @ 0x4000_7000) NOT instantiated");

        // ── Phase 5: real-W1C demonstration (informational, not oracle) ────
        $display("\n--- Phase 5: real INTC W1C demonstration (INFO) ---\n");
        begin
            seed_intc_pending_real();
            apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
            $display("  [INFO] INTC.PENDING after real-source seed = 0x%08h", rd);
            release_intc_sources();
            apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
            $display("  [INFO] INTC.PENDING after source release (sticky, no ACK) = 0x%08h", rd);
            apb_write(ABI_INTC_BASE + 32'h0C, 32'hE0);   // ACK bits 5..7
            apb_read(ABI_INTC_BASE + 32'h00, rd, rd_err);
            $display("  [INFO] INTC.PENDING after ACK=0xE0 = 0x%08h (0 => real W1C clear works)", rd);
        end

        // ── Phase 6: peripheral irq observation (INFO) ─────────────────────
        $display("\n--- Phase 6: final irq levels (INFO) ---\n");
        $display("  [INFO] mxu_irq=%0b sfu_irq=%0b vec_irq=%0b dma_irq=%0b pcie_irq=%0b doorbell_irq=%0b cpu_irq=%0b",
                 mxu_irq, sfu_irq, vec_irq, dma_irq, pcie_irq, doorbell_irq, cpu_irq);
        $display("  [INFO] mxu_dbg_state=0x%0h (0 = IDLE; no compute launched)", mxu_dbg_state);

        // ── Final report ───────────────────────────────────────────────────
        $display("\n=====================================================");
        $display(" apb_conformance_real_tb — Final Report");
        $display("=====================================================");
        $display("  Total checks : %0d", test_num);
        $display("  Oracle passes: %0d", pass_cnt);
        $display("  Divergences  : %0d   (real RTL != model-slave expectation)", fail_cnt);
        $display("  Write timeouts: %0d   Read timeouts: %0d",
                 write_timeouts, read_timeouts);
        $display("=====================================================");

        if (fail_cnt > 0) begin
            $display("APB_CONFORMANCE_REAL: RED (%0d divergences vs model-slave oracle)",
                     fail_cnt);
            $display("TASK-3 RESULT: RED (expected)");
        end else if ((write_timeouts > 0) || (read_timeouts > 0)) begin
            $display("APB_CONFORMANCE_REAL: RED (APB timeout)");
            $display("TASK-3 RESULT: RED (expected)");
        end else begin
            $display("APB_CONFORMANCE_REAL: GREEN — no divergence found (unexpected)");
            $display("TASK-3 RESULT: GREEN (unexpected — real RTL matches model oracle)");
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
