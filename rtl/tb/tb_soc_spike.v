//=============================================================================
// tb_soc_spike — CaduceusCore Full-Chip Cocotb Testbench (Spike CPU)
//=============================================================================
// SoC Phase 3-4 / Task 14 — Spike-RTL integration
//
// Instantiates caduceus_soc_spike_top (no Ibex) with:
//   - 1 GHz clock (0.5 ns half-period)
//   - Reset: 5 cycles low → de-assert
//   - CPU AXI4 master ports (32-bit) driven by cocotbext-axi AxiMaster
//   - CPU APB master ports driven by a simple cocotb Python driver
//   - Backdoor write interfaces for SRAM/DRAM initialization
//   - PCIe TLP ports tied off (not used by P0 cases)
//
// VCS Compile:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
//       -f rtl/ip/verilog-axi.flist -f rtl/ip/verilog-pcie.flist \
//       -f rtl/soc/soc.flist rtl/soc/caduceus_soc_spike_top.v \
//       rtl/tb/tb_soc_spike.v \
//       -top tb_soc_spike -o simv_soc_spike -l elaborate.log
//=============================================================================

`resetall
`timescale 1ns / 1ps
`default_nettype none

module tb_soc_spike;

    //=========================================================================
    // Clock and Reset Parameters
    //=========================================================================
    localparam CLK_HALF       = 0.5;         // 1 GHz clock
    localparam RESET_CYCLES   = 5;

    //=========================================================================
    // DUT Signals
    //=========================================================================
    reg         clk;
    reg         rst_n;
    reg         timer_irq;

    // CPU AXI4 master (driven by cocotbext-axi)
    reg  [3:0]  cpu_m_axi_awid;
    reg  [31:0] cpu_m_axi_awaddr;
    reg  [7:0]  cpu_m_axi_awlen;
    reg  [2:0]  cpu_m_axi_awsize;
    reg  [1:0]  cpu_m_axi_awburst;
    reg         cpu_m_axi_awvalid;
    wire        cpu_m_axi_awready;

    reg  [31:0] cpu_m_axi_wdata;
    reg  [3:0]  cpu_m_axi_wstrb;
    reg         cpu_m_axi_wlast;
    reg         cpu_m_axi_wvalid;
    wire        cpu_m_axi_wready;

    wire [3:0]  cpu_m_axi_bid;
    wire [1:0]  cpu_m_axi_bresp;
    wire        cpu_m_axi_bvalid;
    reg         cpu_m_axi_bready;

    reg  [3:0]  cpu_m_axi_arid;
    reg  [31:0] cpu_m_axi_araddr;
    reg  [7:0]  cpu_m_axi_arlen;
    reg  [2:0]  cpu_m_axi_arsize;
    reg  [1:0]  cpu_m_axi_arburst;
    reg         cpu_m_axi_arvalid;
    wire        cpu_m_axi_arready;

    wire [3:0]  cpu_m_axi_rid;
    wire [31:0] cpu_m_axi_rdata;
    wire [1:0]  cpu_m_axi_rresp;
    wire        cpu_m_axi_rlast;
    wire        cpu_m_axi_rvalid;
    reg         cpu_m_axi_rready;

    // CPU APB master (driven by cocotb)
    reg  [31:0] cpu_apb_paddr;
    reg         cpu_apb_psel;
    reg         cpu_apb_penable;
    reg         cpu_apb_pwrite;
    reg  [31:0] cpu_apb_pwdata;
    wire [31:0] cpu_apb_prdata;
    wire        cpu_apb_pready;
    wire        cpu_apb_pslverr;

    // PCIe TLP — tied off (P0 cases do not use host PCIe)
    reg  [511:0] pcie_rx_req_tlp_data;
    reg  [127:0] pcie_rx_req_tlp_hdr;
    reg          pcie_rx_req_tlp_valid;
    reg          pcie_rx_req_tlp_sop;
    reg          pcie_rx_req_tlp_eop;
    wire         pcie_rx_req_tlp_ready;

    wire [511:0] pcie_tx_cpl_tlp_data;
    wire [15:0]  pcie_tx_cpl_tlp_strb;
    wire [127:0] pcie_tx_cpl_tlp_hdr;
    wire         pcie_tx_cpl_tlp_valid;
    wire         pcie_tx_cpl_tlp_sop;
    wire         pcie_tx_cpl_tlp_eop;
    reg          pcie_tx_cpl_tlp_ready;

    //=========================================================================
    // Test Infrastructure
    //=========================================================================
    reg  [63:0]  sim_cycle;
    reg          sim_done_flag;
    event        sim_done;

    //=========================================================================
    // Cocotb SRAM backdoor write interface
    //=========================================================================
    reg          sram_bkdoor_req;
    reg          sram_bkdoor_ack;
    reg  [15:0]  sram_bkdoor_addr;
    reg  [511:0] sram_bkdoor_wdata;

    initial begin
        sram_bkdoor_req   = 1'b0;
        sram_bkdoor_ack   = 1'b0;
        sram_bkdoor_addr  = 16'd0;
        sram_bkdoor_wdata = 512'd0;
    end

    always @(posedge clk) begin
        if (sram_bkdoor_req && !sram_bkdoor_ack) begin
            u_dut.u_sram_ctrl.mem[sram_bkdoor_addr] <= sram_bkdoor_wdata;
            sram_bkdoor_ack <= 1'b1;
        end else if (!sram_bkdoor_req) begin
            sram_bkdoor_ack <= 1'b0;
        end
    end

    //=========================================================================
    // Cocotb DRAM backdoor write interface
    //=========================================================================
    reg          dram_bkdoor_req;
    reg          dram_bkdoor_ack;
    reg  [17:0]  dram_bkdoor_addr;
    reg  [511:0] dram_bkdoor_wdata;

    initial begin
        dram_bkdoor_req   = 1'b0;
        dram_bkdoor_ack   = 1'b0;
        dram_bkdoor_addr  = 18'd0;
        dram_bkdoor_wdata = 512'd0;
    end

    always @(posedge clk) begin
        if (dram_bkdoor_req && !dram_bkdoor_ack) begin
            u_dut.u_dram_model.mem[dram_bkdoor_addr] <= dram_bkdoor_wdata;
            dram_bkdoor_ack <= 1'b1;
        end else if (!dram_bkdoor_req) begin
            dram_bkdoor_ack <= 1'b0;
        end
    end

    //=========================================================================
    // DUT: caduceus_soc_spike_top (no Ibex)
    //=========================================================================
    caduceus_soc_spike_top #(
        .CROSSBAR_MASTERS (6),
        .SRAM_SIZE        (32'd4194304),
        .DRAM_SIZE        (32'd2147483648)
    ) u_dut (
        .clk                     (clk),
        .rst_n                   (rst_n),

        .pcie_rx_req_tlp_data    (pcie_rx_req_tlp_data),
        .pcie_rx_req_tlp_hdr     (pcie_rx_req_tlp_hdr),
        .pcie_rx_req_tlp_valid   (pcie_rx_req_tlp_valid),
        .pcie_rx_req_tlp_sop     (pcie_rx_req_tlp_sop),
        .pcie_rx_req_tlp_eop     (pcie_rx_req_tlp_eop),
        .pcie_rx_req_tlp_ready   (pcie_rx_req_tlp_ready),

        .pcie_tx_cpl_tlp_data    (pcie_tx_cpl_tlp_data),
        .pcie_tx_cpl_tlp_strb    (pcie_tx_cpl_tlp_strb),
        .pcie_tx_cpl_tlp_hdr     (pcie_tx_cpl_tlp_hdr),
        .pcie_tx_cpl_tlp_valid   (pcie_tx_cpl_tlp_valid),
        .pcie_tx_cpl_tlp_sop     (pcie_tx_cpl_tlp_sop),
        .pcie_tx_cpl_tlp_eop     (pcie_tx_cpl_tlp_eop),
        .pcie_tx_cpl_tlp_ready   (pcie_tx_cpl_tlp_ready),

        .timer_irq_i             (timer_irq),

        .cpu_m_axi_awid          (cpu_m_axi_awid),
        .cpu_m_axi_awaddr        (cpu_m_axi_awaddr),
        .cpu_m_axi_awlen         (cpu_m_axi_awlen),
        .cpu_m_axi_awsize        (cpu_m_axi_awsize),
        .cpu_m_axi_awburst       (cpu_m_axi_awburst),
        .cpu_m_axi_awvalid       (cpu_m_axi_awvalid),
        .cpu_m_axi_awready       (cpu_m_axi_awready),
        .cpu_m_axi_wdata         (cpu_m_axi_wdata),
        .cpu_m_axi_wstrb         (cpu_m_axi_wstrb),
        .cpu_m_axi_wlast         (cpu_m_axi_wlast),
        .cpu_m_axi_wvalid        (cpu_m_axi_wvalid),
        .cpu_m_axi_wready        (cpu_m_axi_wready),
        .cpu_m_axi_bid           (cpu_m_axi_bid),
        .cpu_m_axi_bresp         (cpu_m_axi_bresp),
        .cpu_m_axi_bvalid        (cpu_m_axi_bvalid),
        .cpu_m_axi_bready        (cpu_m_axi_bready),
        .cpu_m_axi_arid          (cpu_m_axi_arid),
        .cpu_m_axi_araddr        (cpu_m_axi_araddr),
        .cpu_m_axi_arlen         (cpu_m_axi_arlen),
        .cpu_m_axi_arsize        (cpu_m_axi_arsize),
        .cpu_m_axi_arburst       (cpu_m_axi_arburst),
        .cpu_m_axi_arvalid       (cpu_m_axi_arvalid),
        .cpu_m_axi_arready       (cpu_m_axi_arready),
        .cpu_m_axi_rid           (cpu_m_axi_rid),
        .cpu_m_axi_rdata         (cpu_m_axi_rdata),
        .cpu_m_axi_rresp         (cpu_m_axi_rresp),
        .cpu_m_axi_rlast         (cpu_m_axi_rlast),
        .cpu_m_axi_rvalid        (cpu_m_axi_rvalid),
        .cpu_m_axi_rready        (cpu_m_axi_rready),

        .cpu_apb_paddr           (cpu_apb_paddr),
        .cpu_apb_psel            (cpu_apb_psel),
        .cpu_apb_penable         (cpu_apb_penable),
        .cpu_apb_pwrite          (cpu_apb_pwrite),
        .cpu_apb_pwdata          (cpu_apb_pwdata),
        .cpu_apb_prdata          (cpu_apb_prdata),
        .cpu_apb_pready          (cpu_apb_pready),
        .cpu_apb_pslverr         (cpu_apb_pslverr),

        .cpu_irq_o               ()
    );

    //=========================================================================
    // Clock Generation (1 GHz)
    //=========================================================================
    initial begin
        clk = 1'b0;
        forever #CLK_HALF clk = ~clk;
    end

    //=========================================================================
    // Cycle Counter
    //=========================================================================
    always @(posedge clk) begin
        if (sim_done_flag)
            sim_cycle <= sim_cycle;
        else
            sim_cycle <= sim_cycle + 1;
    end

    //=========================================================================
    // Reset Sequence
    //=========================================================================
    task automatic apply_reset;
        integer i;
    begin
        rst_n = 1'b0;
        for (i = 0; i < RESET_CYCLES; i = i + 1)
            @(posedge clk);
        rst_n = 1'b1;
    end
    endtask

    //=========================================================================
    // Simulation Initialization
    //=========================================================================
    initial begin
        sim_cycle     = 0;
        sim_done_flag = 0;
        timer_irq     = 1'b0;

        // CPU AXI4 master idle
        cpu_m_axi_awid    = 4'd0;
        cpu_m_axi_awaddr  = 32'd0;
        cpu_m_axi_awlen   = 8'd0;
        cpu_m_axi_awsize  = 3'd0;
        cpu_m_axi_awburst = 2'd0;
        cpu_m_axi_awvalid = 1'b0;
        cpu_m_axi_wdata   = 32'd0;
        cpu_m_axi_wstrb   = 4'd0;
        cpu_m_axi_wlast   = 1'b0;
        cpu_m_axi_wvalid  = 1'b0;
        cpu_m_axi_bready  = 1'b0;
        cpu_m_axi_arid    = 4'd0;
        cpu_m_axi_araddr  = 32'd0;
        cpu_m_axi_arlen   = 8'd0;
        cpu_m_axi_arsize  = 3'd0;
        cpu_m_axi_arburst = 2'd0;
        cpu_m_axi_arvalid = 1'b0;
        cpu_m_axi_rready  = 1'b0;

        // CPU APB master idle
        cpu_apb_paddr   = 32'd0;
        cpu_apb_psel    = 1'b0;
        cpu_apb_penable = 1'b0;
        cpu_apb_pwrite  = 1'b0;
        cpu_apb_pwdata  = 32'd0;

        // PCIe tied off
        pcie_rx_req_tlp_data  = 512'd0;
        pcie_rx_req_tlp_hdr   = 128'd0;
        pcie_rx_req_tlp_valid = 1'b0;
        pcie_rx_req_tlp_sop   = 1'b0;
        pcie_rx_req_tlp_eop   = 1'b0;
        pcie_tx_cpl_tlp_ready = 1'b0;

        apply_reset();

        $display("");
        $display("============================================================");
        $display("[TB] tb_soc_spike — CaduceusCore Spike-CPU Testbench");
        $display("[TB] Clock: 1 GHz (period = 1 ns)");
        $display("[TB] DUT: caduceus_soc_spike_top (no Ibex)");
        $display("============================================================");
        $display("[TB] Reset released at cycle %0d (t=%0t ns)", sim_cycle, $time);
        $display("");

        if ($test$plusargs("COCOTB")) begin
            $display("[TB] COCOTB mode — waiting for Python control...");
            wait (sim_done_flag);
        end else begin
            repeat (500) @(posedge clk);
            $display("[TB] Standalone mode complete");
            $finish;
        end
    end

    //=========================================================================
    // Simulation Timeout (10M cycles)
    //=========================================================================
    initial begin
        #10000000;  // 10 ms = 10M cycles @ 1 GHz
        if (!sim_done_flag) begin
            $display("[TMO] Simulation timeout after 10M cycles");
            $display("FAIL: TIMEOUT");
            $finish;
        end
    end

    //=========================================================================
    // Waveform Dump
    //=========================================================================
`ifdef VCD
    initial begin
        $dumpfile("tb_soc_spike.vcd");
        $dumpvars(0, tb_soc_spike);
    end
`endif

`ifdef FSDB
    initial begin
        $fsdbDumpfile("tb_soc_spike.fsdb");
        $fsdbDumpvars(0, tb_soc_spike);
    end
`endif

endmodule
