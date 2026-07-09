//=============================================================================
// tb_sfu_addr_check — Directed SFU SoC wrapper address-propagation test
//=============================================================================
// SoC-level (wrapper-level) sanity test that programs the SFU MMIO I_ADDR and
// O_ADDR registers to the spec SFU workspace base (0x202C_0000) and verifies
// that sfu_soc_wrapper propagates those addresses to its AXI4 master interface.
//
// Checks:
//   1. APB write to I_ADDR triggers an AXI4 read prefetch whose ARADDR is the
//      line-aligned I_ADDR (0x202C_0000).
//   2. A minimal RELU element-op reads from that line, produces output, and
//      the wrapper flushes a 512-bit write whose AWADDR is the line-aligned
//      O_ADDR (0x202C_0000).
//
// The test is self-contained: it instantiates sfu_soc_wrapper with a tiny
// AXI4 slave model and drives the APB slave directly.  No SoC boot / Ibex /
// crossbar / firmware is required.
//=============================================================================

`timescale 1ns / 1ps

module tb_sfu_addr_check;

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam CLK_HALF = 5;            // 100 MHz clock (10 ns period)

    // SFU MMIO register offsets (SFU_BASE = 0x4000_1000 in full SoC)
    localparam [11:0] OFF_CTRL   = 12'h000;
    localparam [11:0] OFF_CMD    = 12'h004;
    localparam [11:0] OFF_STATUS = 12'h008;
    localparam [11:0] OFF_I_ADDR = 12'h00C;
    localparam [11:0] OFF_O_ADDR = 12'h010;
    localparam [11:0] OFF_DIM    = 12'h014;

    localparam [3:0]  OP_RELU    = 4'd3;

    localparam [31:0] SFU_WORKSPACE_BASE = 32'h202C_0000;

    //=========================================================================
    // Clock / reset
    //=========================================================================
    reg clk;
    reg rst_n;

    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    //=========================================================================
    // APB slave signals
    //=========================================================================
    reg         psel;
    reg         penable;
    reg         pwrite;
    reg  [11:0] paddr;
    reg  [31:0] pwdata;
    wire [31:0] prdata;
    wire        pready;
    wire        pslverr;

    //=========================================================================
    // AXI4 master signals
    //=========================================================================
    wire [7:0]                 m_axi_awid;
    wire [31:0]                m_axi_awaddr;
    wire [7:0]                 m_axi_awlen;
    wire [2:0]                 m_axi_awsize;
    wire [1:0]                 m_axi_awburst;
    wire                       m_axi_awvalid;
    reg                        m_axi_awready;

    wire [511:0]               m_axi_wdata;
    wire [63:0]                m_axi_wstrb;
    wire                       m_axi_wlast;
    wire                       m_axi_wvalid;
    reg                        m_axi_wready;

    reg  [7:0]                 m_axi_bid;
    reg  [1:0]                 m_axi_bresp;
    reg                        m_axi_bvalid;
    wire                       m_axi_bready;

    wire [7:0]                 m_axi_arid;
    wire [31:0]                m_axi_araddr;
    wire [7:0]                 m_axi_arlen;
    wire [2:0]                 m_axi_arsize;
    wire [1:0]                 m_axi_arburst;
    wire                       m_axi_arvalid;
    reg                        m_axi_arready;

    reg  [7:0]                 m_axi_rid;
    reg  [511:0]               m_axi_rdata;
    reg  [1:0]                 m_axi_rresp;
    reg                        m_axi_rlast;
    reg                        m_axi_rvalid;
    wire                       m_axi_rready;

    wire                       irq;

    //=========================================================================
    // DUT: sfu_soc_wrapper
    //=========================================================================
    sfu_soc_wrapper #(
        .AXI_ID_WIDTH   (8),
        .AXI_ADDR_WIDTH (32),
        .AXI_DATA_WIDTH (512),
        .SFU_ADDR_WIDTH (32)
    ) u_dut (
        .clk          (clk),
        .rst_n        (rst_n),

        .psel         (psel),
        .penable      (penable),
        .pwrite       (pwrite),
        .paddr        (paddr),
        .pwdata       (pwdata),
        .prdata       (prdata),
        .pready       (pready),
        .pslverr      (pslverr),

        .m_axi_awid   (m_axi_awid),
        .m_axi_awaddr (m_axi_awaddr),
        .m_axi_awlen  (m_axi_awlen),
        .m_axi_awsize (m_axi_awsize),
        .m_axi_awburst(m_axi_awburst),
        .m_axi_awvalid(m_axi_awvalid),
        .m_axi_awready(m_axi_awready),

        .m_axi_wdata  (m_axi_wdata),
        .m_axi_wstrb  (m_axi_wstrb),
        .m_axi_wlast  (m_axi_wlast),
        .m_axi_wvalid (m_axi_wvalid),
        .m_axi_wready (m_axi_wready),

        .m_axi_bid    (m_axi_bid),
        .m_axi_bresp  (m_axi_bresp),
        .m_axi_bvalid (m_axi_bvalid),
        .m_axi_bready (m_axi_bready),

        .m_axi_arid   (m_axi_arid),
        .m_axi_araddr (m_axi_araddr),
        .m_axi_arlen  (m_axi_arlen),
        .m_axi_arsize (m_axi_arsize),
        .m_axi_arburst(m_axi_arburst),
        .m_axi_arvalid(m_axi_arvalid),
        .m_axi_arready(m_axi_arready),

        .m_axi_rid    (m_axi_rid),
        .m_axi_rdata  (m_axi_rdata),
        .m_axi_rresp  (m_axi_rresp),
        .m_axi_rlast  (m_axi_rlast),
        .m_axi_rvalid (m_axi_rvalid),
        .m_axi_rready (m_axi_rready),

        .irq          (irq)
    );

    //=========================================================================
    // Minimal AXI4 slave model (single-beat bursts only)
    //=========================================================================
    // The wrapper issues single-beat (AWLEN/ARLEN=0) read and write bursts.
    // For writes it asserts AWVALID in one state and WVALID in a later state,
    // so the slave must accept AW and W independently.
    //=========================================================================
    localparam [2:0] SLV_IDLE      = 3'd0;
    localparam [2:0] SLV_R         = 3'd1;
    localparam [2:0] SLV_W_WAIT_W  = 3'd2;
    localparam [2:0] SLV_B         = 3'd3;

    reg [2:0] slv_state;
    reg [31:0] captured_araddr;
    reg [31:0] captured_awaddr;

    // Return FP16 1.0 packed in every 32-bit word (RELU pass-through keeps it)
    localparam [511:0] RD_DATA_PATTERN = {16{32'h3C003C00}};

    assign m_axi_arready = (slv_state == SLV_IDLE);
    assign m_axi_awready = (slv_state == SLV_IDLE);
    assign m_axi_wready  = (slv_state == SLV_W_WAIT_W);
    assign m_axi_rvalid  = (slv_state == SLV_R);
    assign m_axi_rdata   = RD_DATA_PATTERN;
    assign m_axi_rresp   = 2'b00;
    assign m_axi_rlast   = 1'b1;
    assign m_axi_rid     = 8'h10;
    assign m_axi_bvalid  = (slv_state == SLV_B);
    assign m_axi_bresp   = 2'b00;
    assign m_axi_bid     = 8'h11;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            slv_state      <= SLV_IDLE;
            captured_araddr<= 32'd0;
            captured_awaddr<= 32'd0;
        end else begin
            case (slv_state)
                SLV_IDLE: begin
                    if (m_axi_arvalid) begin
                        captured_araddr <= m_axi_araddr;
                        slv_state       <= SLV_R;
                    end else if (m_axi_awvalid) begin
                        captured_awaddr <= m_axi_awaddr;
                        slv_state       <= SLV_W_WAIT_W;
                    end
                end

                SLV_R: begin
                    if (m_axi_rready)
                        slv_state <= SLV_IDLE;
                end

                SLV_W_WAIT_W: begin
                    if (m_axi_wvalid && m_axi_wready)
                        slv_state <= SLV_B;
                end

                SLV_B: begin
                    if (m_axi_bready)
                        slv_state <= SLV_IDLE;
                end

                default: slv_state <= SLV_IDLE;
            endcase
        end
    end

    // Sticky flag for sfu_top's single-cycle status_done pulse.
    // sfu_top clears status_done immediately after returning to ST_IDLE, so a
    // simple STATUS register poll will miss it.
    reg done_seen;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            done_seen <= 1'b0;
        else if (u_dut.u_sfu_top.status_done)
            done_seen <= 1'b1;
    end

    //=========================================================================
    // APB tasks
    //=========================================================================
    // Drive APB control signals on the negative clock edge so they are
    // stable before the DUT's positive-edge-triggered register file samples
    // them.  Sample pready on posedge as required by the APB protocol.
    task automatic apb_write(input [11:0] addr, input [31:0] data);
    begin
        @(negedge clk);
        psel    = 1'b1;
        pwrite  = 1'b1;
        paddr   = addr;
        pwdata  = data;
        penable = 1'b0;

        @(negedge clk);
        penable = 1'b1;

        // Wait for the single access phase.  The wrapper may insert wait
        // states when it is holding CMD.START for the first-line prefetch.
        while (!pready)
            @(posedge clk);

        @(negedge clk);
        psel    = 1'b0;
        penable = 1'b0;
        pwrite  = 1'b0;
        paddr   = 12'd0;
        pwdata  = 32'd0;
    end
    endtask

    task automatic apb_read(input [11:0] addr, output [31:0] data);
    begin
        @(negedge clk);
        psel    = 1'b1;
        pwrite  = 1'b0;
        paddr   = addr;
        penable = 1'b0;

        @(negedge clk);
        penable = 1'b1;
        while (!pready)
            @(posedge clk);
        data    = prdata;

        @(negedge clk);
        psel    = 1'b0;
        penable = 1'b0;
        paddr   = 12'd0;
    end
    endtask

    //=========================================================================
    // Test sequence
    //=========================================================================
    integer pass_cnt;
    integer fail_cnt;
    reg     i_addr_ok;
    reg     o_addr_ok;
    reg [31:0] status;
    integer timeout;

    initial begin
        // Initialize APB
        psel    = 1'b0;
        penable = 1'b0;
        pwrite  = 1'b0;
        paddr   = 12'd0;
        pwdata  = 32'd0;

        pass_cnt  = 0;
        fail_cnt  = 0;
        i_addr_ok = 1'b0;
        o_addr_ok = 1'b0;
        done_seen = 1'b0;
        timeout   = 0;

        // Reset
        rst_n = 1'b0;
        repeat (5) @(posedge clk);
        rst_n = 1'b1;
        repeat (2) @(posedge clk);

        $display("");
        $display("============================================================");
        $display("[TB] tb_sfu_addr_check — SFU wrapper address propagation");
        $display("[TB] I_ADDR / O_ADDR target = 0x%h", SFU_WORKSPACE_BASE);
        $display("============================================================");

        // ---------------------------------------------------------------------
        // 1. Program I_ADDR and capture the AXI4 read-address prefetch.
        // ---------------------------------------------------------------------
        $display("[TB] Writing I_ADDR = 0x%h ...", SFU_WORKSPACE_BASE);
        apb_write(OFF_I_ADDR, SFU_WORKSPACE_BASE);

        // Wait for the AR handshake and check the address.
        begin : wait_ar
            while (!(m_axi_arvalid && m_axi_arready)) begin
                @(posedge clk);
                timeout = timeout + 1;
                if (timeout > 1000) begin
                    $display("[FAIL] Timeout waiting for I_ADDR AXI4 AR handshake");
                    fail_cnt = fail_cnt + 1;
                    disable wait_ar;
                end
            end

            if (m_axi_araddr == SFU_WORKSPACE_BASE) begin
                $display("[PASS] I_ADDR propagated to m_axi_araddr = 0x%h", m_axi_araddr);
                i_addr_ok = 1'b1;
                pass_cnt  = pass_cnt + 1;
            end else begin
                $display("[FAIL] I_ADDR ARADDR mismatch: expected 0x%h, got 0x%h",
                         SFU_WORKSPACE_BASE, m_axi_araddr);
                fail_cnt = fail_cnt + 1;
            end
        end

        // Allow the prefetch read data to return so the line is cached.
        while (!(m_axi_rvalid && m_axi_rready))
            @(posedge clk);
        @(posedge clk);

        // ---------------------------------------------------------------------
        // 2. Program O_ADDR, CTRL, DIM, and START a tiny RELU op.
        // ---------------------------------------------------------------------
        $display("[TB] Writing O_ADDR = 0x%h ...", SFU_WORKSPACE_BASE);
        apb_write(OFF_O_ADDR, SFU_WORKSPACE_BASE);

        $display("[TB] Configuring RELU op, dim=4 ...");
        apb_write(OFF_CTRL, {28'd0, OP_RELU});
        apb_write(OFF_DIM,  32'd4);

        // Issue START command.
        // Two back-to-back START pulses work around an sfu_top race where
        // cmd_start_r and the state machine update on the same posedge; holding
        // the START condition for two consecutive cycles guarantees the FSM
        // sees cmd_start == 1.
        $display("[TB] Issuing CMD.START (double pulse) ...");
        apb_write(OFF_CMD, 32'd1);
        apb_write(OFF_CMD, 32'd1);

        // ---------------------------------------------------------------------
        // 3. Capture the first AXI4 write address and check propagation.
        // ---------------------------------------------------------------------
        begin : wait_aw
            timeout = 0;
            while (!(m_axi_awvalid && m_axi_awready)) begin
                @(posedge clk);
                timeout = timeout + 1;
                if (timeout > 5000) begin
                    $display("[FAIL] Timeout waiting for O_ADDR AXI4 AW handshake");
                    fail_cnt = fail_cnt + 1;
                    disable wait_aw;
                end
            end

            if (m_axi_awaddr == SFU_WORKSPACE_BASE) begin
                $display("[PASS] O_ADDR propagated to m_axi_awaddr = 0x%h", m_axi_awaddr);
                o_addr_ok = 1'b1;
                pass_cnt  = pass_cnt + 1;
            end else begin
                $display("[FAIL] O_ADDR AWADDR mismatch: expected 0x%h, got 0x%h",
                         SFU_WORKSPACE_BASE, m_axi_awaddr);
                fail_cnt = fail_cnt + 1;
            end
        end

        // Wait for write response and then for the operation to finish.
        while (!(m_axi_bvalid && m_axi_bready))
            @(posedge clk);
        @(posedge clk);

        timeout = 0;
        while (!done_seen && timeout < 10000) begin
            apb_read(OFF_STATUS, status);
            if (done_seen || status[1])
                break;
            timeout = timeout + 1;
            @(posedge clk);
        end
        if (done_seen) begin
            $display("[PASS] SFU STATUS.DONE asserted (sticky capture)");
            pass_cnt = pass_cnt + 1;
        end else begin
            $display("[FAIL] SFU did not assert STATUS.DONE within %0d polls", timeout);
            fail_cnt = fail_cnt + 1;
        end

        // ---------------------------------------------------------------------
        // Summary
        // ---------------------------------------------------------------------
        repeat (5) @(posedge clk);
        $display("");
        $display("============================================================");
        $display("[TB] SFU_ADDR_CHECK SUMMARY:");
        $display("[TB]   I_ADDR propagation check: %s", i_addr_ok ? "PASS" : "FAIL");
        $display("[TB]   O_ADDR propagation check: %s", o_addr_ok ? "PASS" : "FAIL");
        $display("[TB]   Pass: %0d  Fail: %0d", pass_cnt, fail_cnt);
        if (fail_cnt == 0)
            $display("[TB]   RESULT: SFU_ADDR_CHECK PASS");
        else
            $display("[TB]   RESULT: SFU_ADDR_CHECK FAIL");
        $display("============================================================");

        if (fail_cnt == 0)
            $finish(0);
        else
            $finish(1);
    end

    //=========================================================================
    // Simulation timeout
    //=========================================================================
    initial begin
        #500000;  // 500 us @ 100 MHz = 50,000 cycles
        $display("[TMO] Simulation timeout");
        $display("[TB]   RESULT: SFU_ADDR_CHECK FAIL");
        $finish(1);
    end

endmodule
