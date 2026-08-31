//=============================================================================
// axi_crossbar_fairness_tb.sv — AXI4 Crossbar Round-Robin Fairness Testbench
// CaduceusCore SoC / soc-rtl-verification-signoff todo 5
//
// Purpose: verify strict round-robin arbitration fairness of the RTL crossbar
// (M=7, S=2) using single-beat transactions only. Bursts are deliberately NOT
// used: a burst holds the slave grant until B/RLAST, which breaks the
// alternating grant semantics under test.
//
// STIMULUS MODEL — sequential rotation (RTL protocol constraint):
//   The crossbar accepts ANY master's AR/AW whenever the target slave is free
//   (m_arready_o = !m_ar_active && (!m_ar_hit || !ar_busy)). If two masters
//   assert ARVALID/AWVALID during the same slave-free cycle, BOTH are
//   accepted (m_ar_active/m_aw_active set) but only ONE is granted by the
//   round-robin arbiter. The ungranted master then waits forever for a
//   response that only the granted master can receive — a permanent
//   phantom-accept deadlock (see learnings notepad). This RTL property is out
//   of scope to fix, so this TB drives the 7 masters one at a time in
//   round-robin order (0->1->2->3->4->5->6->0...), each issuing one
//   single-beat read and one single-beat write per rotation, deasserting
//   ARVALID/AWVALID after each handshake so no two masters ever overlap
//   assertion during a slave-free cycle. Each request is the only requester,
//   so the arbiter grants it immediately; the resulting grant sequence is
//   0,1,2,...,6,0,1,... — strictly alternating. The sole exception is Phase
//   P4, which deliberately violates the sequential-rotation model to expose
//   the phantom-accept deadlock (RED by design until the RTL fix).
//
// Phases:
//   P1 Fairness window: >=100 cycles (5 rotations, ~350 cycles). Per-channel
//      (AR and AW) per-master grant counts must be within +/-1 of each other,
//      no master granted twice in a row, and every transaction completes
//      OKAY (no B/R starvation).
//   P2 DECERR read : while master 1's read is in flight (ar_busy occupied),
//      master 3 issues a single-beat read to an unmapped address. The AR must
//      be accepted immediately (DECERR bypasses the slave grant credit),
//      return RRESP=DECERR (2'b11) with RLAST=1, consume no AR grant, leave
//      master 1's transaction unaffected, and master 3's next mapped read
//      must complete OKAY.
//   P3 DECERR write: while master 2's write is in flight (aw_busy occupied),
//      master 4 issues a single-beat write to an unmapped address. AW accepted
//      immediately, W absorbed, BRESP=DECERR (2'b11), no AW grant consumed,
//      master 2's write unaffected, master 4's next mapped write OKAY.
//   P4 Real contention (RED test for the phantom-accept deadlock): all 7
//      masters assert ARVALID and AWVALID in the SAME slave-free cycle and
//      KEEP CONTENDING for slave 0 (SRAM) continuously for >=10,000 cycles
//      (P4_CONTEND_CYCLES). On the UNMODIFIED crossbar (accept decoupled from
//      grant) every master is accepted while the slave is free but only one
//      is granted — the ungranted masters wait forever for a response that
//      never comes, and the per-transaction watchdog (WDT_LIMIT cycles from
//      VALID-accept to R/B completion; normally <100) prints exactly
//      "FAIRNESS: FAIL" and $finishes → expected RED. After the todo-7 fix
//      (accept coupled to grant), the same phase runs to completion and
//      asserts: per-master grant-count difference <=1 across the window,
//      every master completes >=P4_MIN_TXN reads and writes, and all
//      responses are OKAY → GREEN.
//
// Probes (hierarchical references into rtl/soc/axi_crossbar.v):
//   u_dut.ar_granted[0] / u_dut.aw_granted[0] — per-slave granted master index
//   u_dut.ar_busy[0]    / u_dut.aw_busy[0]    — slave grant credit occupied
//   u_dut.m_rvalid_o[3] / u_dut.m_bvalid_o[4] — per-master response valid
//
// FM algorithm reference: sim/tests/test_crossbar_arbitration.py (algorithm
// only — the FuncModel `_aw_last_granted` attribute does NOT exist in RTL).
//
// Usage (via sim/regression/Makefile):
//   make -C sim/regression run_crossbar_fairness
// Acceptance: log contains "FAIRNESS: PASS" (fixed crossbar, todo 7). On the
// unmodified crossbar P4 reproduces the phantom-accept deadlock and the log
// shows the watchdog marker "FAIRNESS: FAIL" — expected RED for todo 2.
//=============================================================================

`timescale 1ns / 1ps

module axi_crossbar_fairness_tb;

    // =========================================================================
    // Parameters (must match axi_crossbar.v defaults)
    // =========================================================================
    localparam int unsigned DATA_WIDTH  = 512;
    localparam int unsigned ADDR_WIDTH  = 32;
    localparam int unsigned M_ID_WIDTH  = 6;
    localparam int unsigned MSEL_WIDTH  = 3;
    localparam int unsigned NUM_M       = 7;
    localparam int unsigned NUM_S       = 2;
    localparam int unsigned S_ID_WIDTH  = M_ID_WIDTH + MSEL_WIDTH;  // 9
    localparam CLK_HALF = 5;  // 100 MHz, 10ns period

    localparam [ADDR_WIDTH-1:0] SRAM_BASE   = 32'h2000_0000;
    localparam [ADDR_WIDTH-1:0] UNMAPPED_R  = 32'h1000_0000;  // DECERR read
    localparam [ADDR_WIDTH-1:0] UNMAPPED_W  = 32'h5000_0000;  // DECERR write
    localparam int unsigned     NUM_ROTATIONS = 5;   // 5 x 7 masters x (rd+wr)
    localparam int unsigned     MIN_GRANTS    = 28;  // anti-vacuous floor
                                                      // (>= 4 rotations worth)
    // P4 real-contention phase (RED test — phantom-accept deadlock)
    localparam int unsigned     P4_CONTEND_CYCLES = 11000;  // >= 10,000-cycle
                                                            // contention window
    localparam int unsigned     WDT_LIMIT         = 10000;  // per-transaction
                                                            // watchdog cycles
    localparam int unsigned     P4_MIN_TXN        = 20;     // non-vacuous
                                                            // completion floor
                                                            // per master/ch

    // =========================================================================
    // Clock and Reset
    // =========================================================================
    reg clk;
    reg rst_n;

    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    // =========================================================================
    // Crossbar Master-Side Signals
    // =========================================================================
    reg  [NUM_M-1:0][M_ID_WIDTH-1:0]     m_awid;
    reg  [NUM_M-1:0][ADDR_WIDTH-1:0]     m_awaddr;
    reg  [NUM_M-1:0][7:0]                m_awlen;
    reg  [NUM_M-1:0][2:0]                m_awsize;
    reg  [NUM_M-1:0][1:0]                m_awburst;
    reg  [NUM_M-1:0]                     m_awvalid;
    wire [NUM_M-1:0]                     m_awready;

    reg  [NUM_M-1:0][DATA_WIDTH-1:0]     m_wdata;
    reg  [NUM_M-1:0][DATA_WIDTH/8-1:0]   m_wstrb;
    reg  [NUM_M-1:0]                     m_wlast;
    reg  [NUM_M-1:0]                     m_wvalid;
    wire [NUM_M-1:0]                     m_wready;

    wire [NUM_M-1:0][M_ID_WIDTH-1:0]     m_bid;
    wire [NUM_M-1:0][1:0]                m_bresp;
    wire [NUM_M-1:0]                     m_bvalid;
    reg  [NUM_M-1:0]                     m_bready;

    reg  [NUM_M-1:0][M_ID_WIDTH-1:0]     m_arid;
    reg  [NUM_M-1:0][ADDR_WIDTH-1:0]     m_araddr;
    reg  [NUM_M-1:0][7:0]                m_arlen;
    reg  [NUM_M-1:0][2:0]                m_arsize;
    reg  [NUM_M-1:0][1:0]                m_arburst;
    reg  [NUM_M-1:0]                     m_arvalid;
    wire [NUM_M-1:0]                     m_arready;

    wire [NUM_M-1:0][M_ID_WIDTH-1:0]     m_rid;
    wire [NUM_M-1:0][DATA_WIDTH-1:0]     m_rdata;
    wire [NUM_M-1:0][1:0]                m_rresp;
    wire [NUM_M-1:0]                     m_rlast;
    wire [NUM_M-1:0]                     m_rvalid;
    reg  [NUM_M-1:0]                     m_rready;

    // =========================================================================
    // Crossbar Slave-Side Signals
    // =========================================================================
    wire [NUM_S-1:0][S_ID_WIDTH-1:0]     s_awid;
    wire [NUM_S-1:0][ADDR_WIDTH-1:0]     s_awaddr;
    wire [NUM_S-1:0][7:0]                s_awlen;
    wire [NUM_S-1:0][2:0]                s_awsize;
    wire [NUM_S-1:0][1:0]                s_awburst;
    wire [NUM_S-1:0]                     s_awvalid;
    reg  [NUM_S-1:0]                     s_awready;

    wire [NUM_S-1:0][DATA_WIDTH-1:0]     s_wdata;
    wire [NUM_S-1:0][DATA_WIDTH/8-1:0]   s_wstrb;
    wire [NUM_S-1:0]                     s_wlast;
    wire [NUM_S-1:0]                     s_wvalid;
    reg  [NUM_S-1:0]                     s_wready;

    reg  [NUM_S-1:0][S_ID_WIDTH-1:0]     s_bid;
    reg  [NUM_S-1:0][1:0]                s_bresp;
    reg  [NUM_S-1:0]                     s_bvalid;
    wire [NUM_S-1:0]                     s_bready;

    wire [NUM_S-1:0][S_ID_WIDTH-1:0]     s_arid;
    wire [NUM_S-1:0][ADDR_WIDTH-1:0]     s_araddr;
    wire [NUM_S-1:0][7:0]                s_arlen;
    wire [NUM_S-1:0][2:0]                s_arsize;
    wire [NUM_S-1:0][1:0]                s_arburst;
    wire [NUM_S-1:0]                     s_arvalid;
    reg  [NUM_S-1:0]                     s_arready;

    reg  [NUM_S-1:0][S_ID_WIDTH-1:0]     s_rid;
    reg  [NUM_S-1:0][DATA_WIDTH-1:0]     s_rdata;
    reg  [NUM_S-1:0][1:0]                s_rresp;
    reg  [NUM_S-1:0]                     s_rlast;
    reg  [NUM_S-1:0]                     s_rvalid;
    wire [NUM_S-1:0]                     s_rready;

    // =========================================================================
    // DUT: AXI4 Crossbar
    // =========================================================================
    axi_crossbar #(
        .DATA_WIDTH (DATA_WIDTH),
        .ADDR_WIDTH (ADDR_WIDTH),
        .M_ID_WIDTH (M_ID_WIDTH),
        .MSEL_WIDTH (MSEL_WIDTH),
        .NUM_M      (NUM_M),
        .NUM_S      (NUM_S)
    ) u_dut (
        .clk           (clk),
        .rst_n         (rst_n),
        .m_awid_i      (m_awid),
        .m_awaddr_i    (m_awaddr),
        .m_awlen_i     (m_awlen),
        .m_awsize_i    (m_awsize),
        .m_awburst_i   (m_awburst),
        .m_awvalid_i   (m_awvalid),
        .m_awready_o   (m_awready),
        .m_wdata_i     (m_wdata),
        .m_wstrb_i     (m_wstrb),
        .m_wlast_i     (m_wlast),
        .m_wvalid_i    (m_wvalid),
        .m_wready_o    (m_wready),
        .m_bid_o       (m_bid),
        .m_bresp_o     (m_bresp),
        .m_bvalid_o    (m_bvalid),
        .m_bready_i    (m_bready),
        .m_arid_i      (m_arid),
        .m_araddr_i    (m_araddr),
        .m_arlen_i     (m_arlen),
        .m_arsize_i    (m_arsize),
        .m_arburst_i   (m_arburst),
        .m_arvalid_i   (m_arvalid),
        .m_arready_o   (m_arready),
        .m_rid_o       (m_rid),
        .m_rdata_o     (m_rdata),
        .m_rresp_o     (m_rresp),
        .m_rlast_o     (m_rlast),
        .m_rvalid_o    (m_rvalid),
        .m_rready_i    (m_rready),
        .s_awid_o      (s_awid),
        .s_awaddr_o    (s_awaddr),
        .s_awlen_o     (s_awlen),
        .s_awsize_o    (s_awsize),
        .s_awburst_o   (s_awburst),
        .s_awvalid_o   (s_awvalid),
        .s_awready_i   (s_awready),
        .s_wdata_o     (s_wdata),
        .s_wstrb_o     (s_wstrb),
        .s_wlast_o     (s_wlast),
        .s_wvalid_o    (s_wvalid),
        .s_wready_i    (s_wready),
        .s_bid_i       (s_bid),
        .s_bresp_i     (s_bresp),
        .s_bvalid_i    (s_bvalid),
        .s_bready_o    (s_bready),
        .s_arid_o      (s_arid),
        .s_araddr_o    (s_araddr),
        .s_arlen_o     (s_arlen),
        .s_arsize_o    (s_arsize),
        .s_arburst_o   (s_arburst),
        .s_arvalid_o   (s_arvalid),
        .s_arready_i   (s_arready),
        .s_rid_i       (s_rid),
        .s_rdata_i     (s_rdata),
        .s_rresp_i     (s_rresp),
        .s_rlast_i     (s_rlast),
        .s_rvalid_i    (s_rvalid),
        .s_rready_o    (s_rready)
    );

    // =========================================================================
    // Behavioral single-beat slaves (both ports identical)
    //   - AW: always ready (crossbar latches and presents once)
    //   - W : always ready; on W accept latch the ID and return one B
    //   - AR: accept when idle; return exactly one R beat (RLAST=1, OKAY)
    //   - R data is deterministic (no memory model needed — fairness only
    //     checks grant counts and response codes, never payload data)
    // =========================================================================
    reg                          slv_b_pend [0:NUM_S-1];
    reg [S_ID_WIDTH-1:0]         slv_b_id   [0:NUM_S-1];
    reg                          slv_r_active [0:NUM_S-1];
    reg [S_ID_WIDTH-1:0]         slv_r_id   [0:NUM_S-1];

    genvar gs;
    generate
        for (gs = 0; gs < NUM_S; gs = gs + 1) begin : gen_slv
            always @(posedge clk or negedge rst_n) begin
                if (!rst_n) begin
                    s_awready[gs]    <= 1'b1;
                    s_wready[gs]     <= 1'b1;  // always accept the single beat
                    s_bvalid[gs]     <= 1'b0;
                    s_bid[gs]        <= '0;
                    s_bresp[gs]      <= 2'b00;
                    slv_b_pend[gs]   <= 1'b0;
                    slv_b_id[gs]     <= '0;
                    s_arready[gs]    <= 1'b1;
                    s_rvalid[gs]     <= 1'b0;
                    s_rid[gs]        <= '0;
                    s_rdata[gs]      <= '0;
                    s_rresp[gs]      <= 2'b00;
                    s_rlast[gs]      <= 1'b0;
                    slv_r_active[gs] <= 1'b0;
                    slv_r_id[gs]     <= '0;
                end else begin
                    // ── Write path ──────────────────────────────────────────
                    if (s_wvalid[gs] && s_wready[gs]) begin
                        // W accepted: latch the write ID, present B next cycle
                        slv_b_pend[gs] <= 1'b1;
                        slv_b_id[gs]   <= s_awid[gs];
                    end
                    if (slv_b_pend[gs] && s_bvalid[gs] && s_bready[gs]) begin
                        slv_b_pend[gs] <= 1'b0;
                        s_bvalid[gs]   <= 1'b0;
                    end
                    if (slv_b_pend[gs] && !s_bvalid[gs]) begin
                        s_bvalid[gs] <= 1'b1;
                        s_bid[gs]    <= slv_b_id[gs];
                        s_bresp[gs]  <= 2'b00;  // OKAY
                    end

                    // ── Read path ────────────────────────────────────────────
                    if (s_arvalid[gs] && s_arready[gs] && !slv_r_active[gs]) begin
                        slv_r_active[gs] <= 1'b1;
                        slv_r_id[gs]     <= s_arid[gs];
                        s_arready[gs]    <= 1'b0;
                    end
                    if (slv_r_active[gs]) begin
                        if (s_rvalid[gs] && s_rready[gs]) begin
                            slv_r_active[gs] <= 1'b0;
                            s_rvalid[gs]     <= 1'b0;
                            s_arready[gs]    <= 1'b1;
                        end else if (!s_rvalid[gs]) begin
                            // Single-beat response: RLAST=1 on the only beat
                            s_rvalid[gs] <= 1'b1;
                            s_rid[gs]    <= slv_r_id[gs];
                            s_rresp[gs]  <= 2'b00;  // OKAY
                            s_rlast[gs]  <= 1'b1;
                            s_rdata[gs]  <= {DATA_WIDTH/16{16'h5A5A}};
                        end
                    end
                end
            end
        end
    endgenerate

    // =========================================================================
    // Hierarchical probes into axi_crossbar.v internals
    // =========================================================================
    // Per-slave master-index grant arrays (axi_crossbar.v:174/178)
    wire [MSEL_WIDTH-1:0] h_ar_granted = u_dut.ar_granted[0];
    wire [MSEL_WIDTH-1:0] h_aw_granted = u_dut.aw_granted[0];
    // Per-slave grant-credit busy flags
    wire                  h_ar_busy    = u_dut.ar_busy[0];
    wire                  h_aw_busy    = u_dut.aw_busy[0];
    // Per-master response valid (DECERR response capture)
    wire                  h_m3_rvalid  = u_dut.m_rvalid_o[3];
    wire                  h_m4_bvalid  = u_dut.m_bvalid_o[4];

    // Master 3/4 address mapped-ness (grant-credit-violation detection:
    // a grant to a master whose current address is unmapped can never be a
    // legitimate slave grant — DECERR must not consume the grant credit)
    wire m3_ar_mapped = (m_araddr[3][31:22] == 10'b0010000000) &&
                        (m_araddr[3][21:0]  <= 22'h3F_FFFF);
    wire m4_aw_mapped = (m_awaddr[4][31:22] == 10'b0010000000) &&
                        (m_awaddr[4][21:0]  <= 22'h3F_FFFF);

    // =========================================================================
    // Grant counting / fairness monitoring
    // =========================================================================
    reg  [31:0] ar_grant_cnt [0:NUM_M-1];
    reg  [31:0] aw_grant_cnt [0:NUM_M-1];

    reg  ar_busy_q, aw_busy_q;
    wire ar_grant_event = h_ar_busy && !ar_busy_q;
    wire aw_grant_event = h_aw_busy && !aw_busy_q;

    reg  ar_win, aw_win;   // counting windows (driven by the test sequencer)
    reg  dec_ar_grant_violation, dec_aw_grant_violation;
    reg  ar_has_prev, aw_has_prev;
    reg  [MSEL_WIDTH-1:0] ar_prev, aw_prev;
    reg  ar_alt_violation, aw_alt_violation;

    integer gmi;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ar_busy_q <= 1'b0;
            aw_busy_q <= 1'b0;
            dec_ar_grant_violation <= 1'b0;
            dec_aw_grant_violation <= 1'b0;
            ar_has_prev <= 1'b0;
            aw_has_prev <= 1'b0;
            ar_prev <= '0;
            aw_prev <= '0;
            ar_alt_violation <= 1'b0;
            aw_alt_violation <= 1'b0;
            for (gmi = 0; gmi < NUM_M; gmi = gmi + 1) begin
                ar_grant_cnt[gmi] <= '0;
                aw_grant_cnt[gmi] <= '0;
            end
        end else begin
            ar_busy_q <= h_ar_busy;
            aw_busy_q <= h_aw_busy;

            // ── AR grant events ─────────────────────────────────────────────
            if (ar_grant_event) begin
                if (ar_win) begin
                    ar_grant_cnt[h_ar_granted] <= ar_grant_cnt[h_ar_granted] + 1;
                    if (ar_has_prev && (h_ar_granted == ar_prev))
                        ar_alt_violation <= 1'b1;
                    ar_prev     <= h_ar_granted;
                    ar_has_prev <= 1'b1;
                end
                // DECERR must never consume a slave grant credit
                if ((h_ar_granted == 3'd3) && !m3_ar_mapped)
                    dec_ar_grant_violation <= 1'b1;
            end

            // ── AW grant events ─────────────────────────────────────────────
            if (aw_grant_event) begin
                if (aw_win) begin
                    aw_grant_cnt[h_aw_granted] <= aw_grant_cnt[h_aw_granted] + 1;
                    if (aw_has_prev && (h_aw_granted == aw_prev))
                        aw_alt_violation <= 1'b1;
                    aw_prev     <= h_aw_granted;
                    aw_has_prev <= 1'b1;
                end
                if ((h_aw_granted == 3'd4) && !m4_aw_mapped)
                    dec_aw_grant_violation <= 1'b1;
            end
        end
    end

    // =========================================================================
    // Per-transaction watchdog (phantom-accept anti-hang probe)
    // =========================================================================
    // From the cycle a transaction's VALID is accepted until its R/B response
    // completes (normally <100 cycles): if not complete within WDT_LIMIT
    // cycles, print exactly "FAIRNESS: FAIL" and $finish. The Makefile target
    // greps for "FAIRNESS: PASS" — its absence fails the target — so the
    // marker is the RED detector ($finish alone exits 0 and proves nothing).
    // Per-transaction only: a global "N cycles without ANY completion"
    // watchdog is deliberately NOT implemented because it would conflict
    // with the >=10,000-cycle contention window of P4.
    reg  [63:0]              wdt_cycle;
    reg  [NUM_M-1:0]         wdt_ar_pend, wdt_aw_pend;
    reg  [63:0]              wdt_ar_start [0:NUM_M-1];
    reg  [63:0]              wdt_aw_start [0:NUM_M-1];
    integer                  wdt_i;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wdt_cycle <= '0;
            wdt_ar_pend <= '0;
            wdt_aw_pend <= '0;
            for (wdt_i = 0; wdt_i < NUM_M; wdt_i = wdt_i + 1) begin
                wdt_ar_start[wdt_i] <= '0;
                wdt_aw_start[wdt_i] <= '0;
            end
        end else begin
            wdt_cycle <= wdt_cycle + 1;
            for (wdt_i = 0; wdt_i < NUM_M; wdt_i = wdt_i + 1) begin
                // ── AR accept → R completion ────────────────────────────────
                if (m_arvalid[wdt_i] && m_arready[wdt_i] && !wdt_ar_pend[wdt_i]) begin
                    wdt_ar_pend[wdt_i]  <= 1'b1;
                    wdt_ar_start[wdt_i] <= wdt_cycle;
                end
                if (wdt_ar_pend[wdt_i] && m_rvalid[wdt_i] && m_rready[wdt_i] &&
                    m_rlast[wdt_i])
                    wdt_ar_pend[wdt_i] <= 1'b0;
                if (wdt_ar_pend[wdt_i] &&
                    (wdt_cycle - wdt_ar_start[wdt_i]) >= WDT_LIMIT) begin
                    $display("FAIRNESS: FAIL");
                    $display("[TB] WATCHDOG: master %0d AR accepted at cycle %0d, R not complete within %0d cycles (phantom-accept deadlock)",
                        wdt_i, wdt_ar_start[wdt_i], WDT_LIMIT);
                    $finish;
                end

                // ── AW accept → B completion ────────────────────────────────
                if (m_awvalid[wdt_i] && m_awready[wdt_i] && !wdt_aw_pend[wdt_i]) begin
                    wdt_aw_pend[wdt_i]  <= 1'b1;
                    wdt_aw_start[wdt_i] <= wdt_cycle;
                end
                if (wdt_aw_pend[wdt_i] && m_bvalid[wdt_i] && m_bready[wdt_i])
                    wdt_aw_pend[wdt_i] <= 1'b0;
                if (wdt_aw_pend[wdt_i] &&
                    (wdt_cycle - wdt_aw_start[wdt_i]) >= WDT_LIMIT) begin
                    $display("FAIRNESS: FAIL");
                    $display("[TB] WATCHDOG: master %0d AW accepted at cycle %0d, B not complete within %0d cycles (phantom-accept deadlock)",
                        wdt_i, wdt_aw_start[wdt_i], WDT_LIMIT);
                    $finish;
                end
            end
        end
    end

    // =========================================================================
    // P4 grant/completion counters (gated by p4_win, driven by the sequencer)
    // =========================================================================
    reg  [31:0] p4_ar_grant_cnt [0:NUM_M-1];
    reg  [31:0] p4_aw_grant_cnt [0:NUM_M-1];
    reg  [31:0] p4_r_done_cnt   [0:NUM_M-1];
    reg  [31:0] p4_b_done_cnt   [0:NUM_M-1];
    reg  [31:0] p4_r_bad, p4_b_bad;
    reg         p4_win;
    reg         p4_ar_busy_q, p4_aw_busy_q;
    integer     p4_i;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            p4_ar_busy_q <= 1'b0;
            p4_aw_busy_q <= 1'b0;
            p4_r_bad     <= '0;
            p4_b_bad     <= '0;
            for (p4_i = 0; p4_i < NUM_M; p4_i = p4_i + 1) begin
                p4_ar_grant_cnt[p4_i] <= '0;
                p4_aw_grant_cnt[p4_i] <= '0;
                p4_r_done_cnt[p4_i]   <= '0;
                p4_b_done_cnt[p4_i]   <= '0;
            end
        end else begin
            p4_ar_busy_q <= h_ar_busy;
            p4_aw_busy_q <= h_aw_busy;
            if (p4_win) begin
                // Grant events: rising edge of the slave grant-credit busy.
                if (h_ar_busy && !p4_ar_busy_q)
                    p4_ar_grant_cnt[h_ar_granted] <= p4_ar_grant_cnt[h_ar_granted] + 1;
                if (h_aw_busy && !p4_aw_busy_q)
                    p4_aw_grant_cnt[h_aw_granted] <= p4_aw_grant_cnt[h_aw_granted] + 1;
                // Response-completion events per master (single-beat only).
                for (p4_i = 0; p4_i < NUM_M; p4_i = p4_i + 1) begin
                    if (m_rvalid[p4_i] && m_rready[p4_i]) begin
                        p4_r_done_cnt[p4_i] <= p4_r_done_cnt[p4_i] + 1;
                        if (m_rresp[p4_i] != 2'b00)
                            p4_r_bad <= p4_r_bad + 1;
                    end
                    if (m_bvalid[p4_i] && m_bready[p4_i]) begin
                        p4_b_done_cnt[p4_i] <= p4_b_done_cnt[p4_i] + 1;
                        if (m_bresp[p4_i] != 2'b00)
                            p4_b_bad <= p4_b_bad + 1;
                    end
                end
            end
        end
    end

    // =========================================================================
    // Master driver tasks (single-beat; deassert after every handshake so no
    // two masters ever overlap assertion during a slave-free cycle)
    // =========================================================================

    // ── Single-beat read ────────────────────────────────────────────────────
    task automatic do_read(
        input int                    mi,
        input [ADDR_WIDTH-1:0]      addr,
        output [1:0]                rresp_out,
        output                      rlast_out
    );
        begin
            m_arvalid[mi] = 1'b0;
            @(negedge clk);
            m_arid[mi]    = mi[5:0];
            m_araddr[mi]  = addr;
            m_arlen[mi]   = 8'd0;
            m_arsize[mi]  = 3'd6;   // 64 bytes per beat (512-bit bus)
            m_arburst[mi] = 2'b01;  // INCR, single beat
            m_arvalid[mi] = 1'b1;
            while (!m_arready[mi]) @(posedge clk);
            @(negedge clk);
            m_arvalid[mi] = 1'b0;
            // R phase (m_rready held constant 1)
            while (!m_rvalid[mi]) @(posedge clk);
            rresp_out = m_rresp[mi];
            rlast_out = m_rlast[mi];
            @(negedge clk);
        end
    endtask

    // ── Single-beat write ───────────────────────────────────────────────────
    task automatic do_write(
        input int                    mi,
        input [ADDR_WIDTH-1:0]      addr,
        output [1:0]                bresp_out
    );
        begin
            m_awvalid[mi] = 1'b0;
            m_wvalid[mi]  = 1'b0;
            @(negedge clk);
            m_awid[mi]    = mi[5:0];
            m_awaddr[mi]  = addr;
            m_awlen[mi]   = 8'd0;
            m_awsize[mi]  = 3'd6;
            m_awburst[mi] = 2'b01;
            m_awvalid[mi] = 1'b1;
            while (!m_awready[mi]) @(posedge clk);
            @(negedge clk);
            m_awvalid[mi] = 1'b0;
            // W phase (single beat)
            m_wdata[mi]  = {DATA_WIDTH/8{8'hC0 + mi[7:0]}};
            m_wstrb[mi]  = {DATA_WIDTH/8{1'b1}};
            m_wlast[mi]  = 1'b1;
            m_wvalid[mi] = 1'b1;
            while (!m_wready[mi]) @(posedge clk);
            @(negedge clk);
            m_wvalid[mi] = 1'b0;
            // B phase (m_bready held constant 1)
            while (!m_bvalid[mi]) @(posedge clk);
            bresp_out = m_bresp[mi];
            @(negedge clk);
        end
    endtask

    // =========================================================================
    // Test sequencer
    // =========================================================================
    reg [1:0] t_rresp, t_bresp;
    reg       t_rlast;
    integer   txn_errors;

    initial begin
        integer i, iter, d;
        reg [31:0] cnt_min, cnt_max, cnt_sum;
        reg [63:0] win_start, win_end;
        reg [1:0]  dec_rresp, dec_bresp;
        reg [1:0]  m1_rresp, m2_bresp;
        reg [1:0]  m3_post_rresp, m4_post_bresp;
        reg        dec_rlast;
        reg        dec_ar_accepted, dec_aw_accepted;
        reg        dec_inj_ar_busy, dec_inj_aw_busy;
        reg        p4_all_done;
        integer    checks_pass, checks_fail;

        $display("============================================================");
        $display("[TB] AXI4 Crossbar Fairness Testbench (round-robin, M=7 S=2)");
        $display("[TB] Sequential-rotation stimulus, single-beat reads+writes");
        $display("============================================================");

        checks_pass = 0;
        checks_fail = 0;
        txn_errors  = 0;

        // Counting windows start disabled (avoids X before Phase 1)
        ar_win = 1'b0;
        aw_win = 1'b0;
        p4_win = 1'b0;

        // ── Idle all master channels ────────────────────────────────────────
        for (i = 0; i < NUM_M; i = i + 1) begin
            m_awvalid[i] = 1'b0; m_awid[i] = '0; m_awaddr[i] = '0;
            m_awlen[i] = '0; m_awsize[i] = '0; m_awburst[i] = '0;
            m_wvalid[i] = 1'b0; m_wdata[i] = '0; m_wstrb[i] = '0; m_wlast[i] = 1'b0;
            m_bready[i] = 1'b1;   // always ready to accept write responses
            m_arvalid[i] = 1'b0; m_arid[i] = '0; m_araddr[i] = '0;
            m_arlen[i] = '0; m_arsize[i] = '0; m_arburst[i] = '0;
            m_rready[i] = 1'b1;   // always ready to accept read responses
        end

        // ── Reset ───────────────────────────────────────────────────────────
        rst_n = 1'b0;
        repeat(5) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        $display("[TB] Reset released at %0t ns", $time);
        repeat(10) @(posedge clk);  // settle

        // =====================================================================
        // PHASE 1 — sequential-rotation fairness window (>=100 cycles)
        // =====================================================================
        $display("");
        $display("--- P1: fairness window (%0d rotations x 7 masters x rd+wr) ---",
            NUM_ROTATIONS);
        win_start = $time;
        ar_win = 1'b1;
        aw_win = 1'b1;
        for (iter = 0; iter < NUM_ROTATIONS; iter = iter + 1) begin
            for (i = 0; i < NUM_M; i = i + 1) begin
                // Single-beat read to a per-master SRAM address
                do_read(i, SRAM_BASE + 32'h1000 + (i * 64), t_rresp, t_rlast);
                if ((t_rresp !== 2'b00) || (t_rlast !== 1'b1)) begin
                    $display("[TB]   ERROR: master %0d read resp=%b rlast=%b",
                        i, t_rresp, t_rlast);
                    txn_errors = txn_errors + 1;
                end
                // Single-beat write to a per-master SRAM address
                do_write(i, SRAM_BASE + 32'h2000 + (i * 64), t_bresp);
                if (t_bresp !== 2'b00) begin
                    $display("[TB]   ERROR: master %0d write resp=%b", i, t_bresp);
                    txn_errors = txn_errors + 1;
                end
            end
        end
        ar_win = 1'b0;
        aw_win = 1'b0;
        @(posedge clk);  // settle final non-blocking updates
        win_end = $time;
        $display("[TB]   Window: %0d cycles", (win_end - win_start) / 10);

        // ── P1 check: AR grant counts within +/-1, strict alternation ──────
        cnt_min = ar_grant_cnt[0];
        cnt_max = ar_grant_cnt[0];
        cnt_sum = 0;
        for (i = 0; i < NUM_M; i = i + 1) begin
            if (ar_grant_cnt[i] < cnt_min) cnt_min = ar_grant_cnt[i];
            if (ar_grant_cnt[i] > cnt_max) cnt_max = ar_grant_cnt[i];
            cnt_sum = cnt_sum + ar_grant_cnt[i];
        end
        $display("[TB]   AR grants per master: %0d %0d %0d %0d %0d %0d %0d (total %0d)",
            ar_grant_cnt[0], ar_grant_cnt[1], ar_grant_cnt[2], ar_grant_cnt[3],
            ar_grant_cnt[4], ar_grant_cnt[5], ar_grant_cnt[6], cnt_sum);
        if ((cnt_max - cnt_min) <= 1) begin
            $display("[TB]   CHECK AR fairness max-min=%0d <= 1: PASS", cnt_max - cnt_min);
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK AR fairness max-min=%0d > 1: FAIL", cnt_max - cnt_min);
            checks_fail = checks_fail + 1;
        end
        if (cnt_sum >= MIN_GRANTS) begin
            $display("[TB]   CHECK AR grant total %0d >= %0d (non-vacuous): PASS",
                cnt_sum, MIN_GRANTS);
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK AR grant total %0d < %0d: FAIL", cnt_sum, MIN_GRANTS);
            checks_fail = checks_fail + 1;
        end
        if (!ar_alt_violation) begin
            $display("[TB]   CHECK AR strict alternation (no back-to-back same master): PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK AR strict alternation: FAIL (same master granted twice in a row)");
            checks_fail = checks_fail + 1;
        end

        // ── P1 check: AW grant counts within +/-1, no repeats ───────────────
        cnt_min = aw_grant_cnt[0];
        cnt_max = aw_grant_cnt[0];
        cnt_sum = 0;
        for (i = 0; i < NUM_M; i = i + 1) begin
            if (aw_grant_cnt[i] < cnt_min) cnt_min = aw_grant_cnt[i];
            if (aw_grant_cnt[i] > cnt_max) cnt_max = aw_grant_cnt[i];
            cnt_sum = cnt_sum + aw_grant_cnt[i];
        end
        $display("[TB]   AW grants per master: %0d %0d %0d %0d %0d %0d %0d (total %0d)",
            aw_grant_cnt[0], aw_grant_cnt[1], aw_grant_cnt[2], aw_grant_cnt[3],
            aw_grant_cnt[4], aw_grant_cnt[5], aw_grant_cnt[6], cnt_sum);
        if ((cnt_max - cnt_min) <= 1) begin
            $display("[TB]   CHECK AW fairness max-min=%0d <= 1: PASS", cnt_max - cnt_min);
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK AW fairness max-min=%0d > 1: FAIL", cnt_max - cnt_min);
            checks_fail = checks_fail + 1;
        end
        if (cnt_sum >= MIN_GRANTS) begin
            $display("[TB]   CHECK AW grant total %0d >= %0d (non-vacuous): PASS",
                cnt_sum, MIN_GRANTS);
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK AW grant total %0d < %0d: FAIL", cnt_sum, MIN_GRANTS);
            checks_fail = checks_fail + 1;
        end
        if (!aw_alt_violation) begin
            $display("[TB]   CHECK AW strict alternation (no back-to-back same master): PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK AW strict alternation: FAIL (same master granted twice in a row)");
            checks_fail = checks_fail + 1;
        end
        if (txn_errors == 0) begin
            $display("[TB]   CHECK all %0d reads+writes completed OKAY: PASS",
                NUM_ROTATIONS * NUM_M * 2);
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK transaction completion: FAIL (%0d bad responses)", txn_errors);
            checks_fail = checks_fail + 1;
        end

        // =====================================================================
        // PHASE 2 — DECERR read (master 3) while master 1's read is in flight
        // =====================================================================
        $display("");
        $display("--- P2: DECERR read (master 3 -> unmapped 0x%0h) ---", UNMAPPED_R);

        // Start master 1's mapped read; stop right after its AR handshake so
        // the SRAM grant credit (ar_busy) is occupied.
        m_arvalid[1] = 1'b0;
        @(negedge clk);
        m_arid[1]    = 6'h01;
        m_araddr[1]  = SRAM_BASE + 32'h1000 + (1 * 64);
        m_arlen[1]   = 8'd0;
        m_arsize[1]  = 3'd6;
        m_arburst[1] = 2'b01;
        m_arvalid[1] = 1'b1;
        while (!m_arready[1]) @(posedge clk);
        @(negedge clk);
        m_arvalid[1] = 1'b0;
        dec_inj_ar_busy = h_ar_busy;   // expect 1: grant credit now occupied

        // Inject master 3's unmapped read while ar_busy is occupied.
        m_araddr[3]  = UNMAPPED_R;
        m_arid[3]    = 6'h03;
        m_arlen[3]   = 8'd0;
        m_arsize[3]  = 3'd6;
        m_arburst[3] = 2'b01;
        m_arvalid[3] = 1'b1;
        @(posedge clk);
        dec_ar_accepted = (m_arvalid[3] && m_arready[3]);
        @(negedge clk);
        m_arvalid[3] = 1'b0;

        // DECERR R response (generated by the crossbar, no slave involved)
        while (!(h_m3_rvalid && m_rready[3])) @(posedge clk);
        dec_rresp = m_rresp[3];
        dec_rlast = m_rlast[3];

        // Master 1's in-flight read must complete OKAY (unaffected).
        while (!(m_rvalid[1] && m_rready[1])) @(posedge clk);
        m1_rresp = m_rresp[1];

        // Master 3's next mapped read must complete OKAY (credit untouched).
        do_read(3, SRAM_BASE + 32'h1000 + (3 * 64), m3_post_rresp, t_rlast);

        $display("[TB]   DECERR AR accepted in %0d cycle(s)", dec_ar_accepted ? 1 : 2);
        if (dec_inj_ar_busy === 1'b1) begin
            $display("[TB]   CHECK injected while ar_busy occupied: PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK injected while ar_busy occupied: FAIL (busy=%b)",
                dec_inj_ar_busy);
            checks_fail = checks_fail + 1;
        end
        if (dec_ar_accepted === 1'b1) begin
            $display("[TB]   CHECK AR accepted immediately (no slave credit wait): PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK AR accepted immediately: FAIL");
            checks_fail = checks_fail + 1;
        end
        if ((dec_rresp === 2'b11) && (dec_rlast === 1'b1)) begin
            $display("[TB]   CHECK RRESP=DECERR(2'b11) RLAST=1: PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK RRESP/RLAST: FAIL (rresp=%b rlast=%b)",
                dec_rresp, dec_rlast);
            checks_fail = checks_fail + 1;
        end
        if (m1_rresp === 2'b00) begin
            $display("[TB]   CHECK in-flight master 1 read completed OKAY: PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK in-flight master 1 read: FAIL (rresp=%b)", m1_rresp);
            checks_fail = checks_fail + 1;
        end
        if (m3_post_rresp === 2'b00) begin
            $display("[TB]   CHECK post-DECERR mapped read completes OKAY: PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK post-DECERR mapped read: FAIL (rresp=%b)", m3_post_rresp);
            checks_fail = checks_fail + 1;
        end

        // =====================================================================
        // PHASE 3 — DECERR write (master 4) while master 2's write in flight
        // =====================================================================
        $display("");
        $display("--- P3: DECERR write (master 4 -> unmapped 0x%0h) ---", UNMAPPED_W);

        // Start master 2's mapped write; stop right after its AW handshake so
        // the SRAM grant credit (aw_busy) is occupied, and present its W beat.
        m_awvalid[2] = 1'b0;
        m_wvalid[2]  = 1'b0;
        @(negedge clk);
        m_awid[2]    = 6'h02;
        m_awaddr[2]  = SRAM_BASE + 32'h2000 + (2 * 64);
        m_awlen[2]   = 8'd0;
        m_awsize[2]  = 3'd6;
        m_awburst[2] = 2'b01;
        m_awvalid[2] = 1'b1;
        while (!m_awready[2]) @(posedge clk);
        @(negedge clk);
        m_awvalid[2] = 1'b0;
        m_wdata[2]   = {DATA_WIDTH/8{8'hC2}};
        m_wstrb[2]   = {DATA_WIDTH/8{1'b1}};
        m_wlast[2]   = 1'b1;
        m_wvalid[2]  = 1'b1;
        dec_inj_aw_busy = h_aw_busy;   // expect 1: grant credit now occupied

        // Inject master 4's unmapped write while aw_busy is occupied.
        m_awaddr[4]  = UNMAPPED_W;
        m_awid[4]    = 6'h04;
        m_awlen[4]   = 8'd0;
        m_awsize[4]  = 3'd6;
        m_awburst[4] = 2'b01;
        m_awvalid[4] = 1'b1;
        @(posedge clk);   // master 2's W handshake + master 4's AW accept
        dec_aw_accepted = (m_awvalid[4] && m_awready[4]);
        @(negedge clk);
        m_awvalid[4] = 1'b0;

        // Present master 4's W immediately (absorbed by the DECERR path) so
        // that its DECERR B and master 2's OKAY B arrive in the same cycle.
        m_wdata[4]   = {DATA_WIDTH/8{8'hC4}};
        m_wstrb[4]   = {DATA_WIDTH/8{1'b1}};
        m_wlast[4]   = 1'b1;
        m_wvalid[4]  = 1'b1;

        // Both B responses are valid in the same cycle: capture both.
        while (!((m_bvalid[2] && m_bready[2]) || (h_m4_bvalid && m_bready[4])))
            @(posedge clk);
        if (m_bvalid[2] && m_bready[2]) m2_bresp = m_bresp[2];
        if (h_m4_bvalid && m_bready[4]) dec_bresp = m_bresp[4];
        @(negedge clk);
        m_wvalid[4]  = 1'b0;
        m_wvalid[2]  = 1'b0;

        // Master 4's next mapped write must complete OKAY.
        do_write(4, SRAM_BASE + 32'h2000 + (4 * 64), m4_post_bresp);

        if (dec_inj_aw_busy === 1'b1) begin
            $display("[TB]   CHECK injected while aw_busy occupied: PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK injected while aw_busy occupied: FAIL (busy=%b)",
                dec_inj_aw_busy);
            checks_fail = checks_fail + 1;
        end
        if (dec_aw_accepted === 1'b1) begin
            $display("[TB]   CHECK AW accepted immediately (no slave credit wait): PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK AW accepted immediately: FAIL");
            checks_fail = checks_fail + 1;
        end
        if (dec_bresp === 2'b11) begin
            $display("[TB]   CHECK BRESP=DECERR(2'b11): PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK BRESP: FAIL (bresp=%b)", dec_bresp);
            checks_fail = checks_fail + 1;
        end
        if (m2_bresp === 2'b00) begin
            $display("[TB]   CHECK in-flight master 2 write completed OKAY: PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK in-flight master 2 write: FAIL (bresp=%b)", m2_bresp);
            checks_fail = checks_fail + 1;
        end
        if (m4_post_bresp === 2'b00) begin
            $display("[TB]   CHECK post-DECERR mapped write completes OKAY: PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK post-DECERR mapped write: FAIL (bresp=%b)", m4_post_bresp);
            checks_fail = checks_fail + 1;
        end

        // =====================================================================
        // Grant-credit violation checks (sticky, sampled throughout the run)
        // =====================================================================
        @(posedge clk);
        if (!dec_ar_grant_violation) begin
            $display("[TB]   CHECK DECERR read consumed no AR grant credit: PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK DECERR read consumed an AR grant: FAIL");
            checks_fail = checks_fail + 1;
        end
        if (!dec_aw_grant_violation) begin
            $display("[TB]   CHECK DECERR write consumed no AW grant credit: PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK DECERR write consumed an AW grant: FAIL");
            checks_fail = checks_fail + 1;
        end

        // =====================================================================
        // PHASE 4 — all-master real contention for the same slave (RED test)
        // =====================================================================
        $display("");
        $display("--- P4: all %0d masters contend for slave 0 for %0d cycles ---",
            NUM_M, P4_CONTEND_CYCLES);

        // Contention must begin in a single slave-free cycle (both channels).
        repeat(5) @(posedge clk);   // settle after P3
        $display("[TB]   slave state at P4 start: ar_busy=%b aw_busy=%b",
            h_ar_busy, h_aw_busy);

        // Configure all masters for single-beat mapped reads+writes to SRAM
        // (slave 0) and assert every ARVALID/AWVALID/WVALID in the SAME
        // cycle. VALIDs stay asserted for the whole contention window.
        @(negedge clk);
        for (i = 0; i < NUM_M; i = i + 1) begin
            m_arid[i]    = i[5:0];
            m_araddr[i]  = SRAM_BASE + 32'h3000 + (i * 64);
            m_arlen[i]   = 8'd0;
            m_arsize[i]  = 3'd6;
            m_arburst[i] = 2'b01;
            m_arvalid[i] = 1'b1;
            m_awid[i]    = i[5:0];
            m_awaddr[i]  = SRAM_BASE + 32'h4000 + (i * 64);
            m_awlen[i]   = 8'd0;
            m_awsize[i]  = 3'd6;
            m_awburst[i] = 2'b01;
            m_awvalid[i] = 1'b1;
            m_wdata[i]   = {DATA_WIDTH/8{8'hC0 + i[7:0]}};
            m_wstrb[i]   = {DATA_WIDTH/8{1'b1}};
            m_wlast[i]   = 1'b1;
            m_wvalid[i]  = 1'b1;
        end
        p4_win = 1'b1;
        @(posedge clk);   // all VALIDs asserted together in the slave-free cycle

        // Keep contending continuously. On the UNMODIFIED crossbar the
        // per-transaction watchdog fires "FAIRNESS: FAIL" inside this window
        // (phantom-accept deadlock reproduced — expected RED). On the fixed
        // crossbar the window runs to completion and the P4 checks below
        // evaluate.
        repeat(P4_CONTEND_CYCLES) @(posedge clk);

        // Drain: deassert all VALIDs, wait for the last in-flight
        // transactions to complete (bounded).
        @(negedge clk);
        for (i = 0; i < NUM_M; i = i + 1) begin
            m_arvalid[i] = 1'b0;
            m_awvalid[i] = 1'b0;
            m_wvalid[i]  = 1'b0;
        end
        d = 0;
        while ((d < 200) && (h_ar_busy || h_aw_busy)) begin
            @(posedge clk);
            d = d + 1;
        end
        p4_win = 1'b0;
        @(posedge clk);   // settle final non-blocking counter updates

        // ── P4 check 1: per-master AR grant-count difference <= 1 ───────────
        cnt_min = p4_ar_grant_cnt[0];
        cnt_max = p4_ar_grant_cnt[0];
        cnt_sum = 0;
        for (i = 0; i < NUM_M; i = i + 1) begin
            if (p4_ar_grant_cnt[i] < cnt_min) cnt_min = p4_ar_grant_cnt[i];
            if (p4_ar_grant_cnt[i] > cnt_max) cnt_max = p4_ar_grant_cnt[i];
            cnt_sum = cnt_sum + p4_ar_grant_cnt[i];
        end
        $write("[TB]   P4 AR grants per master:");
        for (i = 0; i < NUM_M; i = i + 1) $write(" %0d", p4_ar_grant_cnt[i]);
        $display(" (total %0d)", cnt_sum);
        if ((cnt_max - cnt_min) <= 1) begin
            $display("[TB]   CHECK P4 AR grant fairness max-min=%0d <= 1: PASS",
                cnt_max - cnt_min);
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK P4 AR grant fairness max-min=%0d > 1: FAIL",
                cnt_max - cnt_min);
            checks_fail = checks_fail + 1;
        end

        // ── P4 check 2: per-master AW grant-count difference <= 1 ───────────
        cnt_min = p4_aw_grant_cnt[0];
        cnt_max = p4_aw_grant_cnt[0];
        cnt_sum = 0;
        for (i = 0; i < NUM_M; i = i + 1) begin
            if (p4_aw_grant_cnt[i] < cnt_min) cnt_min = p4_aw_grant_cnt[i];
            if (p4_aw_grant_cnt[i] > cnt_max) cnt_max = p4_aw_grant_cnt[i];
            cnt_sum = cnt_sum + p4_aw_grant_cnt[i];
        end
        $write("[TB]   P4 AW grants per master:");
        for (i = 0; i < NUM_M; i = i + 1) $write(" %0d", p4_aw_grant_cnt[i]);
        $display(" (total %0d)", cnt_sum);
        if ((cnt_max - cnt_min) <= 1) begin
            $display("[TB]   CHECK P4 AW grant fairness max-min=%0d <= 1: PASS",
                cnt_max - cnt_min);
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK P4 AW grant fairness max-min=%0d > 1: FAIL",
                cnt_max - cnt_min);
            checks_fail = checks_fail + 1;
        end

        // ── P4 check 3: every master completes >= P4_MIN_TXN reads+writes ──
        p4_all_done = 1'b1;
        for (i = 0; i < NUM_M; i = i + 1) begin
            if ((p4_r_done_cnt[i] < P4_MIN_TXN) || (p4_b_done_cnt[i] < P4_MIN_TXN))
                p4_all_done = 1'b0;
        end
        $write("[TB]   P4 R completions per master:");
        for (i = 0; i < NUM_M; i = i + 1) $write(" %0d", p4_r_done_cnt[i]);
        $display("");
        $write("[TB]   P4 B completions per master:");
        for (i = 0; i < NUM_M; i = i + 1) $write(" %0d", p4_b_done_cnt[i]);
        $display("");
        if (p4_all_done) begin
            $display("[TB]   CHECK P4 every master completed >= %0d reads+writes (no starvation): PASS",
                P4_MIN_TXN);
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK P4 completion floor: FAIL (some master completed < %0d reads or writes)",
                P4_MIN_TXN);
            checks_fail = checks_fail + 1;
        end

        // ── P4 check 4: all P4 responses OKAY ───────────────────────────────
        if ((p4_r_bad == 0) && (p4_b_bad == 0)) begin
            $display("[TB]   CHECK P4 all responses OKAY (0 bad R, 0 bad B): PASS");
            checks_pass = checks_pass + 1;
        end else begin
            $display("[TB]   CHECK P4 all responses OKAY: FAIL (%0d bad R, %0d bad B)",
                p4_r_bad, p4_b_bad);
            checks_fail = checks_fail + 1;
        end

        // =====================================================================
        // Summary
        // =====================================================================
        $display("");
        $display("============================================================");
        $display("[TB] Summary: %0d passed, %0d failed", checks_pass, checks_fail);
        $display("[TB] Total simulation time: %0t ns", $time);
        if (checks_fail == 0) begin
            $display("FAIRNESS: PASS");
        end else begin
            $display("FAIRNESS: FAIL");
        end
        $display("============================================================");
        $finish;
    end

endmodule
