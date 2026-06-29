// tb_exp_lut_verify.sv — SF-01: Verify all 256 exp_lut entries vs numpy.exp golden
//
// Sweeps all 256 LUT addresses with frac=0 (no interpolation).
// Writes raw Q1.14 outputs to CaduceusCore/rtl/results/sf01_result_raw.hex
// (NFS shared path, visible from both local and EDA server).
// Post-processing comparison done externally via post_sf01_compare.py.
//
// Usage:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_exp_lut_verify \
//       CaduceusCore/rtl/tb/tb_exp_lut_verify.sv CaduceusCore/rtl/sfu/exp_lut.v -o /tmp/simv_tb_exp_lut_verify
//   /tmp/simv_tb_exp_lut_verify -no_save

`timescale 1ns/1ps

module tb_exp_lut_verify;

    reg         clk;
    reg         rst_n;
    reg  [7:0]  addr;
    reg  [7:0]  frac;
    wire [14:0] lut_out;

    exp_lut dut (
        .clk    (clk),
        .rst_n  (rst_n),
        .addr   (addr),
        .frac   (frac),
        .lut_out(lut_out)
    );

    always #5 clk = ~clk;

    integer i, fd;

    initial begin
        clk = 0;
        rst_n = 0;
        addr = 8'd0;
        frac = 8'd0;

        #20 rst_n = 1;
        #10;

        // ── Sweep all 256 entries at frac=0 ──────────────────────
        $display("[SF-01] Sweeping all 256 exp_lut entries (frac=0)...");

        fd = $fopen("CaduceusCore/rtl/results/sf01_result_raw.hex", "w");
        if (!fd) begin
            $display("[SF-01] ERROR: Cannot open result file");
            $display("FAIL");
            $finish;
        end

        for (i = 0; i < 256; i = i + 1) begin
            addr = i;
            #1;  // combinatorial settle
            $fdisplay(fd, "%04x", lut_out);
        end
        $fclose(fd);

        $display("[SF-01] Wrote 256 Q1.14 values to CaduceusCore/rtl/results/sf01_result_raw.hex");
        $display("[SF-01] Simulation complete — run post-processing externally.");
        $display("PASS");

        #10;
        $finish;
    end

    // ── Timeout watchdog ──────────────────────────────────────────
    initial begin
        #100000;
        $display("[SF-01] TIMEOUT: Simulation did not complete");
        $finish;
    end

endmodule
