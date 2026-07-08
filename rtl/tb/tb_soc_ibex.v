//=============================================================================
// tb_soc_ibex — CaduceusCore Full-Chip Cocotb Testbench (Ibex RTL CPU)
//=============================================================================
// SoC Phase 4 / Task 12 — Ibex-RTL full SoC regression
//
// Instantiates caduceus_soc_top (from Task 13) with the internal Ibex RISC-V
// core as the active CPU master. Unlike tb_soc_spike, there are no external
// cpu_m_axi / cpu_apb ports; Ibex fetches instructions from boot_rom and
// performs all MMIO/data accesses inside the SoC.
//
// The cocotb Python harness preloads SRAM/DRAM via the backdoor interfaces
// below, writes the doorbell HOST_TAIL register through a VPI backdoor path,
// and monitors NPU_HEAD to detect firmware completion.
//
// VCS Compile:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps \
//       -f rtl/cpu/ibex.flist -f rtl/ip/verilog-axi.flist \
//       -f rtl/ip/verilog-pcie.flist -f rtl/soc/soc.flist \
//       rtl/tb/tb_soc_ibex.v -top tb_soc_ibex -o simv_soc_ibex -l elaborate.log
// NOTE: ibex.flist must come FIRST (ibex_pkg.sv needed before ibex_wrapper)
//=============================================================================

`resetall
`timescale 1ns / 1ps
`default_nettype none

module tb_soc_ibex;

    //=========================================================================
    // Clock and Reset Parameters
    //=========================================================================
    localparam CLK_HALF       = 0.5;         // 1 GHz clock (1 ns period)
    localparam RESET_CYCLES   = 5;           // 5 cycles low before de-assert

    //=========================================================================
    // DUT Signals
    //=========================================================================
    reg         clk;
    reg         rst_n;
    reg         timer_irq;

    // PCIe TLP RX (request completion to DUT)
    reg  [511:0] pcie_rx_req_tlp_data;
    reg  [127:0] pcie_rx_req_tlp_hdr;
    reg          pcie_rx_req_tlp_valid;
    reg          pcie_rx_req_tlp_sop;
    reg          pcie_rx_req_tlp_eop;
    wire         pcie_rx_req_tlp_ready;

    // PCIe TLP TX (completion from DUT)
    wire [511:0] pcie_tx_cpl_tlp_data;
    wire [15:0]  pcie_tx_cpl_tlp_strb;
    wire [127:0] pcie_tx_cpl_tlp_hdr;
    wire         pcie_tx_cpl_tlp_valid;
    wire         pcie_tx_cpl_tlp_sop;
    wire         pcie_tx_cpl_tlp_eop;
    reg          pcie_tx_cpl_tlp_ready;

    // PCIe DMA TLP RX (completion to DMA master)
    reg  [511:0] pcie_dma_rx_cpl_tlp_data;
    reg  [127:0] pcie_dma_rx_cpl_tlp_hdr;
    reg  [3:0]   pcie_dma_rx_cpl_tlp_error;
    reg          pcie_dma_rx_cpl_tlp_valid;
    reg          pcie_dma_rx_cpl_tlp_sop;
    reg          pcie_dma_rx_cpl_tlp_eop;
    wire         pcie_dma_rx_cpl_tlp_ready;

    // PCIe DMA TLP TX (read request from DMA master)
    wire [127:0] pcie_dma_tx_rd_req_tlp_hdr;
    wire [4:0]   pcie_dma_tx_rd_req_tlp_seq;
    wire         pcie_dma_tx_rd_req_tlp_valid;
    wire         pcie_dma_tx_rd_req_tlp_sop;
    wire         pcie_dma_tx_rd_req_tlp_eop;
    reg          pcie_dma_tx_rd_req_tlp_ready;

    // PCIe DMA TLP TX (write request from DMA master)
    wire [511:0] pcie_dma_tx_wr_req_tlp_data;
    wire [15:0]  pcie_dma_tx_wr_req_tlp_strb;
    wire [127:0] pcie_dma_tx_wr_req_tlp_hdr;
    wire [4:0]   pcie_dma_tx_wr_req_tlp_seq;
    wire         pcie_dma_tx_wr_req_tlp_valid;
    wire         pcie_dma_tx_wr_req_tlp_sop;
    wire         pcie_dma_tx_wr_req_tlp_eop;
    reg          pcie_dma_tx_wr_req_tlp_ready;

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
    // DUT: caduceus_soc_top (Ibex RTL CPU inside)
    //=========================================================================
    caduceus_soc_top #(
        .CROSSBAR_MASTERS (7),
        .SRAM_SIZE        (32'd4194304),
        .DRAM_SIZE        (32'd2147483648)
    ) u_dut (
        .clk                     (clk),
        .rst_n                   (rst_n),

        // PCIe TLP ports — exposed for cocotbext-pcie host model
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

        // PCIe DMA TLP ports — exposed for host RC DMA completion/request streams
        .pcie_dma_rx_cpl_tlp_data   (pcie_dma_rx_cpl_tlp_data),
        .pcie_dma_rx_cpl_tlp_hdr    (pcie_dma_rx_cpl_tlp_hdr),
        .pcie_dma_rx_cpl_tlp_error  (pcie_dma_rx_cpl_tlp_error),
        .pcie_dma_rx_cpl_tlp_valid  (pcie_dma_rx_cpl_tlp_valid),
        .pcie_dma_rx_cpl_tlp_sop    (pcie_dma_rx_cpl_tlp_sop),
        .pcie_dma_rx_cpl_tlp_eop    (pcie_dma_rx_cpl_tlp_eop),
        .pcie_dma_rx_cpl_tlp_ready  (pcie_dma_rx_cpl_tlp_ready),

        .pcie_dma_tx_rd_req_tlp_hdr (pcie_dma_tx_rd_req_tlp_hdr),
        .pcie_dma_tx_rd_req_tlp_seq (pcie_dma_tx_rd_req_tlp_seq),
        .pcie_dma_tx_rd_req_tlp_valid(pcie_dma_tx_rd_req_tlp_valid),
        .pcie_dma_tx_rd_req_tlp_sop (pcie_dma_tx_rd_req_tlp_sop),
        .pcie_dma_tx_rd_req_tlp_eop (pcie_dma_tx_rd_req_tlp_eop),
        .pcie_dma_tx_rd_req_tlp_ready(pcie_dma_tx_rd_req_tlp_ready),

        .pcie_dma_tx_wr_req_tlp_data(pcie_dma_tx_wr_req_tlp_data),
        .pcie_dma_tx_wr_req_tlp_strb(pcie_dma_tx_wr_req_tlp_strb),
        .pcie_dma_tx_wr_req_tlp_hdr (pcie_dma_tx_wr_req_tlp_hdr),
        .pcie_dma_tx_wr_req_tlp_seq (pcie_dma_tx_wr_req_tlp_seq),
        .pcie_dma_tx_wr_req_tlp_valid(pcie_dma_tx_wr_req_tlp_valid),
        .pcie_dma_tx_wr_req_tlp_sop (pcie_dma_tx_wr_req_tlp_sop),
        .pcie_dma_tx_wr_req_tlp_eop (pcie_dma_tx_wr_req_tlp_eop),
        .pcie_dma_tx_wr_req_tlp_ready(pcie_dma_tx_wr_req_tlp_ready),

        .timer_irq_i             (timer_irq)
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

        // PCIe TLP idle
        pcie_rx_req_tlp_data  = 512'd0;
        pcie_rx_req_tlp_hdr   = 128'd0;
        pcie_rx_req_tlp_valid = 1'b0;
        pcie_rx_req_tlp_sop   = 1'b0;
        pcie_rx_req_tlp_eop   = 1'b0;
        pcie_tx_cpl_tlp_ready = 1'b0;

        // PCIe DMA TLP idle / ready
        pcie_dma_rx_cpl_tlp_data  = 512'd0;
        pcie_dma_rx_cpl_tlp_hdr   = 128'd0;
        pcie_dma_rx_cpl_tlp_error = 4'd0;
        pcie_dma_rx_cpl_tlp_valid = 1'b0;
        pcie_dma_rx_cpl_tlp_sop   = 1'b0;
        pcie_dma_rx_cpl_tlp_eop   = 1'b0;
        pcie_dma_tx_rd_req_tlp_ready = 1'b0;
        pcie_dma_tx_wr_req_tlp_ready = 1'b0;

        // Zero-initialize Ibex DMEM to avoid X-propagation on data reads
        begin
            integer dmem_i;
            for (dmem_i = 0; dmem_i < 16384; dmem_i = dmem_i + 1)
                u_dut.u_ibex_wrapper.dmem[dmem_i] = 32'h0;
        end

        // Zero-initialize engine wrapper internal buffers so compute starts
        // from known data when the cocotb test does not explicitly preload.
        begin
            integer buf_i;
            for (buf_i = 0; buf_i < u_dut.u_mxu_wrapper.W_BUF_DEPTH; buf_i = buf_i + 1)
                u_dut.u_mxu_wrapper.weight_buf[buf_i] = {u_dut.u_mxu_wrapper.AXI_DATA_WIDTH{1'b0}};
            for (buf_i = 0; buf_i < u_dut.u_mxu_wrapper.A_BUF_DEPTH; buf_i = buf_i + 1)
                u_dut.u_mxu_wrapper.activation_buf[buf_i] = {u_dut.u_mxu_wrapper.AXI_DATA_WIDTH{1'b0}};
            u_dut.u_sfu_wrapper.rd_line_buf = {u_dut.u_sfu_wrapper.AXI_DATA_WIDTH{1'b0}};
            u_dut.u_sfu_wrapper.wr_line_buf = {u_dut.u_sfu_wrapper.AXI_DATA_WIDTH{1'b0}};
            for (buf_i = 0; buf_i < u_dut.u_vector_wrapper.CHUNKS_MAX; buf_i = buf_i + 1) begin
                u_dut.u_vector_wrapper.buf_a[buf_i] = {u_dut.u_vector_wrapper.VECTOR_W{1'b0}};
                u_dut.u_vector_wrapper.buf_b[buf_i] = {u_dut.u_vector_wrapper.VECTOR_W{1'b0}};
                u_dut.u_vector_wrapper.buf_o[buf_i] = {u_dut.u_vector_wrapper.VECTOR_W{1'b0}};
            end
        end

        apply_reset();

        $display("");
        $display("============================================================");
        $display("[TB] tb_soc_ibex — CaduceusCore Ibex-RTL Testbench");
        $display("[TB] Clock: 1 GHz (period = 1 ns)");
        $display("[TB] DUT: caduceus_soc_top with internal Ibex RV32IMC");
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
    // Simulation Timeout (50M cycles)
    //=========================================================================
    initial begin
        #50000000;  // 50 ms = 50M cycles @ 1 GHz
        if (!sim_done_flag) begin
            $display("[TMO] Simulation timeout after 50M cycles");
            $display("FAIL: TIMEOUT");
            $finish;
        end
    end

    //=========================================================================
    // Waveform Dump
    //=========================================================================
`ifdef VCD
    initial begin
        $dumpfile("tb_soc_ibex.vcd");
        $dumpvars(0, tb_soc_ibex);
    end
`endif

`ifdef FSDB
    initial begin
        $fsdbDumpfile("tb_soc_ibex.fsdb");
        $fsdbDumpvars(0, tb_soc_ibex);
    end
`endif

endmodule
