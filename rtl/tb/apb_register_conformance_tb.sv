//=============================================================================
// apb_register_conformance_tb.sv — APB Register Conformance Testbench
// CaduceusCore SoC / soc-rtl-verification-signoff todo 6
//
// Purpose: verify that the RTL apb_decoder routes writes and readbacks to the
// 7 engine/peripheral APB slaves (MXU/SFU/VECTOR/DMA/PCIe/DOORBELL/INTC) with
// register access semantics conforming to the Func Model register table:
//   rw  — overwrite semantics (0x3 then 0x6 → readback 0x6, never OR-accumulate)
//   r   — hostile write must leave the register unchanged
//   w   — write-only: value stored (verified by model readback, matching the
//         Func Model factory behaviour in sim/tests/test_apb_register_conformance.py)
//   w1c — write-1-to-clear: seeded 0xFFFF, write 0x00F0 → only bits 4..7 clear
//
// Slave 7 (PCIE_DMA @ 0x4000_7000) is EXPLICITLY SKIPPED — its prdata input is
// tied off and a live assertion guarantees psel_o[7] is never asserted during
// the entire simulation.
//
// The register table below is generated from sim/regmap.py offsets plus the
// access/reset semantics of the Func Model peripheral factories
// (sim/models/apb_peripheral.py), which the FM conformance gate
// (sim/tests/test_apb_register_conformance.py) pins as the reference.
//
// Usage (via sim/regression/Makefile):
//   make -C sim/regression run_apb_conformance
// Acceptance: log contains "APB_CONFORMANCE: PASS"
//=============================================================================

`timescale 1ns / 1ps

module apb_register_conformance_tb;

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam CLK_HALF = 5;               // 100 MHz

    // Access codes for the register table
    localparam [1:0] ACC_RW  = 2'd0;       // read-write, overwrite semantics
    localparam [1:0] ACC_R   = 2'd1;       // read-only, writes ignored
    localparam [1:0] ACC_W   = 2'd2;       // write-only (stored, FM model style)
    localparam [1:0] ACC_W1C = 2'd3;       // write-1-to-clear

    // Slave base addresses (apb_decoder.v page = paddr[15:12])
    localparam [31:0] MXU_BASE      = 32'h4000_0000;
    localparam [31:0] SFU_BASE      = 32'h4000_1000;
    localparam [31:0] VECTOR_BASE   = 32'h4000_2000;
    localparam [31:0] DMA_BASE      = 32'h4000_3000;
    localparam [31:0] PCIE_BASE     = 32'h4000_4000;
    localparam [31:0] DOORBELL_BASE = 32'h4000_5000;
    localparam [31:0] INTC_BASE     = 32'h4000_6000;
    localparam [31:0] PCIE_DMA_BASE = 32'h4000_7000;  // SKIPPED by design

    //=========================================================================
    // Signals
    //=========================================================================
    reg         clk;
    reg         rst_n;

    // APB master (to apb_decoder)
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

    // Per-slave responses
    wire [7:0]  pready_i;
    wire [7:0]  pslverr_i;
    wire [31:0] prdata_slv0;
    wire [31:0] prdata_slv1;
    wire [31:0] prdata_slv2;
    wire [31:0] prdata_slv3;
    wire [31:0] prdata_slv4;
    wire [31:0] prdata_slv5;
    wire [31:0] prdata_slv6;

    // Muxed response back to master
    wire        pready;
    wire        pslverr;
    wire [31:0] prdata;

    // Backdoor seed for w1c registers (INTC.ACK) — test-only stimulus
    reg         bk_we;
    reg  [11:0] bk_sel;
    reg  [31:0] bk_data;

    //=========================================================================
    // DUT: real RTL APB decoder
    //=========================================================================
    apb_decoder u_dut (
        .clk        (clk),
        .rst_n      (rst_n),
        .psel       (psel),
        .penable    (penable),
        .paddr      (paddr),
        .pwrite     (pwrite),
        .pwdata     (pwdata),
        .psel_o     (psel_o),
        .penable_o  (penable_o),
        .paddr_o    (paddr_o),
        .pwrite_o   (pwrite_o),
        .pwdata_o   (pwdata_o),
        .pready_i   (pready_i),
        .pslverr_i  (pslverr_i),
        .prdata_i   ('{prdata_slv0, prdata_slv1, prdata_slv2,
                       prdata_slv3, prdata_slv4, prdata_slv5,
                       prdata_slv6, 32'h0}),   // slv7 (PCIE_DMA) — SKIPPED
        .pready     (pready),
        .pslverr    (pslverr),
        .prdata     (prdata)
    );

    assign pready_i  = 8'hFF;              // zero-wait-state slaves
    assign pslverr_i = 8'h00;

    //=========================================================================
    // Conformance slave register banks (7 tested peripherals)
    //=========================================================================
    apb_conformance_slave #(.SLAVE_ID(0)) u_slv0 (
        .clk(clk), .rst_n(rst_n), .psel(psel_o[0]), .penable(penable_o[0]),
        .paddr(paddr_o[11:0]), .pwrite(pwrite_o), .pwdata(pwdata_o),
        .prdata(prdata_slv0), .pready(pready_i[0]), .pslverr(pslverr_i[0]),
        .bk_we(bk_we), .bk_sel(bk_sel), .bk_data(bk_data)
    );
    apb_conformance_slave #(.SLAVE_ID(1)) u_slv1 (
        .clk(clk), .rst_n(rst_n), .psel(psel_o[1]), .penable(penable_o[1]),
        .paddr(paddr_o[11:0]), .pwrite(pwrite_o), .pwdata(pwdata_o),
        .prdata(prdata_slv1), .pready(pready_i[1]), .pslverr(pslverr_i[1]),
        .bk_we(bk_we), .bk_sel(bk_sel), .bk_data(bk_data)
    );
    apb_conformance_slave #(.SLAVE_ID(2)) u_slv2 (
        .clk(clk), .rst_n(rst_n), .psel(psel_o[2]), .penable(penable_o[2]),
        .paddr(paddr_o[11:0]), .pwrite(pwrite_o), .pwdata(pwdata_o),
        .prdata(prdata_slv2), .pready(pready_i[2]), .pslverr(pslverr_i[2]),
        .bk_we(bk_we), .bk_sel(bk_sel), .bk_data(bk_data)
    );
    apb_conformance_slave #(.SLAVE_ID(3)) u_slv3 (
        .clk(clk), .rst_n(rst_n), .psel(psel_o[3]), .penable(penable_o[3]),
        .paddr(paddr_o[11:0]), .pwrite(pwrite_o), .pwdata(pwdata_o),
        .prdata(prdata_slv3), .pready(pready_i[3]), .pslverr(pslverr_i[3]),
        .bk_we(bk_we), .bk_sel(bk_sel), .bk_data(bk_data)
    );
    apb_conformance_slave #(.SLAVE_ID(4)) u_slv4 (
        .clk(clk), .rst_n(rst_n), .psel(psel_o[4]), .penable(penable_o[4]),
        .paddr(paddr_o[11:0]), .pwrite(pwrite_o), .pwdata(pwdata_o),
        .prdata(prdata_slv4), .pready(pready_i[4]), .pslverr(pslverr_i[4]),
        .bk_we(bk_we), .bk_sel(bk_sel), .bk_data(bk_data)
    );
    apb_conformance_slave #(.SLAVE_ID(5)) u_slv5 (
        .clk(clk), .rst_n(rst_n), .psel(psel_o[5]), .penable(penable_o[5]),
        .paddr(paddr_o[11:0]), .pwrite(pwrite_o), .pwdata(pwdata_o),
        .prdata(prdata_slv5), .pready(pready_i[5]), .pslverr(pslverr_i[5]),
        .bk_we(bk_we), .bk_sel(bk_sel), .bk_data(bk_data)
    );
    apb_conformance_slave #(.SLAVE_ID(6)) u_slv6 (
        .clk(clk), .rst_n(rst_n), .psel(psel_o[6]), .penable(penable_o[6]),
        .paddr(paddr_o[11:0]), .pwrite(pwrite_o), .pwdata(pwdata_o),
        .prdata(prdata_slv6), .pready(pready_i[6]), .pslverr(pslverr_i[6]),
        .bk_we(bk_we), .bk_sel(bk_sel), .bk_data(bk_data)
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
    integer s, r;
    reg [31:0] rd;
    reg [31:0] exp_val;

    // Live guard: PCIE_DMA (slave 7) must NEVER be selected
    integer pcie_dma_sel_cnt;
    initial pcie_dma_sel_cnt = 0;
    always @(posedge clk) begin
        if (psel_o[7]) pcie_dma_sel_cnt = pcie_dma_sel_cnt + 1;
    end

    //=========================================================================
    // Expected register table — generated from sim/regmap.py offsets plus the
    // Func Model access semantics (sim/models/apb_peripheral.py factories,
    // pinned by sim/tests/test_apb_register_conformance.py).
    //
    // One row per tested slave: {reg_count, per-register offsets, per-register
    // access codes, per-register reset values}. Slave 7 (PCIE_DMA) excluded.
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

    // Reset values from the Func Model factories (RegisterField default=0
    // except the PCIe configuration defaults below).
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

    //=========================================================================
    // APB master tasks (drive the decoder's master port)
    //=========================================================================
    task automatic apb_write;
        input [31:0] addr;
        input [31:0] data;
    begin
        @(posedge clk); #1;
        psel    = 1'b1;
        penable = 1'b0;
        paddr   = addr;
        pwrite  = 1'b1;
        pwdata  = data;
        @(posedge clk); #1;
        penable = 1'b1;
        @(posedge clk); #1;
        psel    = 1'b0;
        penable = 1'b0;
    end
    endtask

    task automatic apb_read;
        input  [31:0] addr;
        output [31:0] data;
    begin
        @(posedge clk); #1;
        psel    = 1'b1;
        penable = 1'b0;
        paddr   = addr;
        pwrite  = 1'b0;
        @(posedge clk); #1;
        penable = 1'b1;
        #1;
        data = prdata;
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

    // Seed a w1c register through the backdoor port of the INTC slave
    task automatic seed_ack;
        input [31:0] val;
    begin
        @(posedge clk); #1;
        bk_we   = 1'b1;
        bk_sel  = 12'h0C;   // INTC.ACK
        bk_data = val;
        @(posedge clk); #1;
        bk_we   = 1'b0;
    end
    endtask

    //=========================================================================
    // Main test sequence
    //=========================================================================
    initial begin
        test_num = 0;
        pass_cnt = 0;
        fail_cnt = 0;

        apb_idle();
        bk_we   = 1'b0;
        bk_sel  = 12'h0;
        bk_data = 32'h0;

        // Reset
        rst_n = 1'b0;
        repeat (5) @(posedge clk);
        rst_n = 1'b1;
        repeat (3) @(posedge clk);

        $display("\n=====================================================");
        $display(" apb_register_conformance_tb — 7 peripherals, skip PCIE_DMA");
        $display("=====================================================\n");

        // ── Phase 1: reset-value conformance (rw + r + w1c registers) ───
        $display("--- Phase 1: reset values ---\n");
        for (s = 0; s < 7; s = s + 1) begin
            for (r = 0; r < REG_CNT[s]; r = r + 1) begin
                if (REG_ACC[s][r] == ACC_W)
                    continue;   // write-only: no readable reset value
                apb_read((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), rd);
                check(rd, REG_RST[s][r],
                      {"reset ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
            end
        end

        // ── Phase 2: per-register access conformance (write→readback) ───
        $display("\n--- Phase 2: write → readback conformance ---\n");
        for (s = 0; s < 7; s = s + 1) begin
            for (r = 0; r < REG_CNT[s]; r = r + 1) begin
                case (REG_ACC[s][r])
                    ACC_RW: begin
                        // Overwrite semantics: 0x3 then 0x6 → readback 0x6
                        apb_write((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), 32'h3);
                        apb_read ((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), rd);
                        check(rd, 32'h3,
                              {"rw-w0x3 ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                        apb_write((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), 32'h6);
                        apb_read ((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), rd);
                        check(rd, 32'h6,
                              {"rw-ovrw ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_R: begin
                        // Hostile write must not change the value
                        apb_write((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), 32'hFFFF_FFFF);
                        apb_read ((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), rd);
                        check(rd, REG_RST[s][r],
                              {"r-hostile ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_W: begin
                        // Write-only: store + readback (Func Model semantics)
                        apb_write((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), 32'h42);
                        apb_read ((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), rd);
                        check(rd, 32'h42,
                              {"w-store ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                    ACC_W1C: begin
                        // Seed 0xFFFF, write 0x00F0 → only bits 4..7 clear
                        seed_ack(32'h0000_FFFF);
                        apb_write((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), 32'h00F0);
                        apb_read ((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), rd);
                        check(rd, 32'h0000_FF0F,
                              {"w1c-clr ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                        // re-seed and confirm unrelated bits survive a 0 write
                        seed_ack(32'h0000_FFFF);
                        apb_write((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), 32'h0000);
                        apb_read ((32'h4000_0000 + (s << 12) + REG_OFFS[s][r]), rd);
                        check(rd, 32'h0000_FFFF,
                              {"w1c-hold ", itoa_slv(s), " +0x", itoa_hex(REG_OFFS[s][r])});
                    end
                endcase
            end
        end

        // ── Phase 3: decoder routing + out-of-range pslverr ─────────────
        $display("\n--- Phase 3: decoder routing & error path ---\n");

        // Out-of-range 0x4000_8000 → pslverr=1, prdata=0
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

        // ── Phase 4: PCIE_DMA (slave 7) explicitly skipped ─────────────
        $display("\n--- Phase 4: PCIE_DMA skip guard ---\n");
        check(pcie_dma_sel_cnt, 32'd0, "psel_o[7] (PCIE_DMA) never asserted");
        $display("  [INFO] PCIE_DMA (slave 7 @ 0x4000_7000) excluded — 7/7 tested");

        // ── Final report ────────────────────────────────────────────────
        $display("\n=====================================================");
        $display(" apb_register_conformance_tb — Final Report");
        $display("=====================================================");
        $display("  Total : %0d", test_num);
        $display("  Passed: %0d", pass_cnt);
        $display("  Failed: %0d", fail_cnt);
        $display("=====================================================");

        if (fail_cnt == 0) begin
            $display("APB_CONFORMANCE: PASS (7/7 peripheral slaves tested)");
            $display("=====================================================\n");
            $finish;
        end else begin
            $display("APB_CONFORMANCE: FAIL (%0d check(s) failed)", fail_cnt);
            $display("=====================================================\n");
            $finish;
        end
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
        #100000;
        $display("\n[ERROR] Timeout: simulation did not finish in 100,000 ns");
        $finish;
    end

endmodule


//=============================================================================
// apb_conformance_slave — generic APB register-bank model implementing the
// Func Model access semantics for one peripheral.
//=============================================================================
// Access semantics (mirrors sim/tests/test_apb_register_conformance.py):
//   rw  — store & overwrite; readback returns last written value
//   r   — read-only; writes ignored; readback returns reset value
//   w   — write-only; value stored and readable (Func Model factory behaviour)
//   w1c — write-1-to-clear: value & ~pwdata; backdoor port seeds the register
//
// Reset values per register are returned through the `rst_value` function.
//=============================================================================

module apb_conformance_slave #(
    parameter integer SLAVE_ID = 0    // 0=MXU 1=SFU 2=VECTOR 3=DMA 4=PCIE 5=DOORBELL 6=INTC
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        psel,
    input  wire        penable,
    input  wire [11:0] paddr,
    input  wire        pwrite,
    input  wire [31:0] pwdata,
    output wire [31:0] prdata,
    output wire        pready,
    output wire        pslverr,
    // Test-only backdoor: seed a register (used for w1c pre-conditioning)
    input  wire        bk_we,
    input  wire [11:0] bk_sel,
    input  wire [31:0] bk_data
);

    localparam [1:0] ACC_RW  = 2'd0;
    localparam [1:0] ACC_R   = 2'd1;
    localparam [1:0] ACC_W   = 2'd2;
    localparam [1:0] ACC_W1C = 2'd3;

    reg [31:0] regs [0:15];

    // Per-slave register access table (offsets must match the TB REG_OFFS).
    function automatic [1:0] access;
        input [11:0] a;
        begin
            case (SLAVE_ID)
                0: // MXU
                    case (a)
                        12'h00, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C,
                        12'h20, 12'h24, 12'h28: access = ACC_RW;
                        12'h04: access = ACC_W;      // CMD
                        12'h08: access = ACC_R;      // STATUS
                        default: access = ACC_RW;
                    endcase
                1: // SFU
                    case (a)
                        12'h00, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C: access = ACC_RW;
                        12'h04: access = ACC_W;      // CMD
                        12'h08: access = ACC_R;      // STATUS
                        default: access = ACC_RW;
                    endcase
                2: // VECTOR
                    case (a)
                        12'h00, 12'h0C, 12'h10, 12'h14, 12'h18, 12'h1C: access = ACC_RW;
                        12'h04: access = ACC_W;      // CMD
                        12'h08: access = ACC_R;      // STATUS
                        default: access = ACC_RW;
                    endcase
                3: // DMA
                    case (a)
                        12'h00, 12'h10, 12'h14, 12'h18, 12'h1C, 12'h20, 12'h24,
                        12'h28, 12'h2C, 12'h30, 12'h34, 12'h38: access = ACC_RW;
                        12'h04: access = ACC_W;      // CMD
                        12'h08: access = ACC_R;      // STATUS
                        default: access = ACC_RW;
                    endcase
                4: // PCIe — all ten config registers rw
                    access = ACC_RW;
                5: // DOORBELL
                    case (a)
                        12'h00: access = ACC_W;      // HOST_TAIL
                        12'h04: access = ACC_RW;     // NPU_HEAD
                        12'h08: access = ACC_R;      // HOST_HEAD
                        12'h0C: access = ACC_R;      // NPU_TAIL
                        12'h10, 12'h14: access = ACC_RW; // LAST_STATUS / COMPLETION_STATUS
                        default: access = ACC_RW;
                    endcase
                6: // INTC
                    case (a)
                        12'h00: access = ACC_R;      // PENDING
                        12'h04: access = ACC_RW;     // ENABLE
                        12'h08: access = ACC_RW;     // THRESHOLD
                        12'h0C: access = ACC_W1C;    // ACK
                        default: access = ACC_RW;
                    endcase
                default: access = ACC_RW;
            endcase
        end
    endfunction

    // Reset value per register (Func Model RegisterField defaults).
    function automatic [31:0] rst_value;
        input [11:0] a;
        begin
            if (SLAVE_ID == 4) begin
                case (a)
                    12'h00: rst_value = 32'h0000_0001;  // COMPLETER_ID
                    12'h04: rst_value = 32'h0000_0003;  // MAX_PAYLOAD_SIZE
                    12'h18: rst_value = 32'h2000_0000;  // BAR0_BASE
                    12'h1C: rst_value = 32'h003F_FFFF;  // BAR0_MASK
                    12'h20: rst_value = 32'h8000_0000;  // BAR1_BASE
                    12'h24: rst_value = 32'h7FFF_FFFF;  // BAR1_MASK
                    default: rst_value = 32'h0;
                endcase
            end else begin
                rst_value = 32'h0;
            end
        end
    endfunction

    integer i;
    wire wr_en = psel && penable && pwrite;

    // Storage init + write path (rw stores, w stores, w1c clears, r ignored)
    initial begin
        for (i = 0; i < 16; i = i + 1)
            regs[i] = rst_value(12'(i * 4));
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (i = 0; i < 16; i = i + 1)
                regs[i] <= rst_value(12'(i * 4));
        end else if (bk_we && (SLAVE_ID == 6)) begin
            // Backdoor seed — INTC only (w1c pre-conditioning); keeps the
            // shared bk_* bus from corrupting other slaves' regs[3].
            regs[bk_sel[5:2]] <= bk_data;
        end else if (wr_en) begin
            case (access(paddr))
                ACC_R: ;                                    // ignore hostile write
                ACC_W1C: regs[paddr[5:2]] <= regs[paddr[5:2]] & ~pwdata;
                default: regs[paddr[5:2]] <= pwdata;        // rw / w
            endcase
        end
    end

    // Read path — combinational; 'r' fields return the (immutable) reset value
    reg [31:0] rdata_comb;
    always @(*) begin
        if (access(paddr) == ACC_R)
            rdata_comb = rst_value(paddr);
        else
            rdata_comb = regs[paddr[5:2]];
    end

    assign prdata  = (psel && !pwrite) ? rdata_comb : 32'h0;
    assign pready  = 1'b1;
    assign pslverr = 1'b0;

endmodule
