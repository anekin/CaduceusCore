//=============================================================================
// tb_rmsnorm_hw — Standalone self-checking testbench for rmsnorm_hw
//=============================================================================
// Reads input.hex / golden.hex from the same directory, streams one 4096-
// element FP16 vector through rmsnorm_hw, captures the output, and writes
// result.hex for compare_rtl.py.  Also verifies the N==1 corner case
// (output = sign(x)).
//
// Usage:
//   cd CaduceusCore/rtl/test_vectors/sfu/rmsnorm_smoke
//   vcs -full64 -sverilog -timescale=1ns/1ps -top tb_rmsnorm_hw \
//       ../../../sfu/rmsnorm_hw.v tb_rmsnorm_hw.v -o simv_rmsnorm -l compile.log
//   ./simv_rmsnorm -l sim.log
//   python3 ../../../../sim/compare_rtl.py .
//=============================================================================

`timescale 1ns / 1ps

module tb_rmsnorm_hw;

    localparam N        = 4096;
    localparam CLK_HALF = 5;      // 100 MHz

    reg         clk;
    reg         rst_n;
    reg  [15:0] data_i;
    reg         valid_i;
    reg         last_i;
    wire [15:0] data_o;
    wire        valid_o;

    reg [15:0] input_mem  [0:N-1];
    reg [15:0] golden_mem [0:N-1];
    reg [15:0] result_mem [0:N-1];

    integer in_idx;
    integer out_idx;
    integer errors;
    integer cycle_cnt;
    real    rms;
    real    val;

    //=========================================================================
    // DUT
    //=========================================================================
    rmsnorm_hw u_dut (
        .clk     (clk),
        .rst_n   (rst_n),
        .data_i  (data_i),
        .valid_i (valid_i),
        .last_i  (last_i),
        .data_o  (data_o),
        .valid_o (valid_o)
    );

    //=========================================================================
    // Helpers
    //=========================================================================
    function automatic real fp16_to_real;
        input [15:0] h;
        real sgn, expv, mant;
        begin
            sgn  = h[15] ? -1.0 : 1.0;
            expv = h[14:10];
            mant = h[9:0];
            if (expv == 0)
                fp16_to_real = 0.0;
            else
                fp16_to_real = sgn * (1.0 + mant / 1024.0) * (2.0 ** (expv - 15.0));
        end
    endfunction

    // Stream a vector of length vec_n into the DUT.
    // If input_mem is used, reads from input_mem[0..vec_n-1];
    // otherwise drives the supplied constant value.
    task automatic feed_vector;
        input integer vec_n;
        input use_const;
        input [15:0] const_val;
        integer i;
        begin
            for (i = 0; i < vec_n; i = i + 1) begin
                @(posedge clk);
                data_i  <= use_const ? const_val : input_mem[i];
                valid_i <= 1'b1;
                last_i  <= (i == vec_n - 1) ? 1'b1 : 1'b0;
            end
            @(posedge clk);
            valid_i <= 1'b0;
            last_i  <= 1'b0;
            data_i  <= 16'h0000;
        end
    endtask

    // Wait for vec_n valid outputs, store them in result_mem.
    task automatic collect_outputs;
        input integer vec_n;
        integer i;
        begin
            i = 0;
            cycle_cnt = 0;
            while (i < vec_n) begin
                @(negedge clk);
                cycle_cnt = cycle_cnt + 1;
                if (valid_o) begin
                    result_mem[i] = data_o;
                    i = i + 1;
                end
                if (cycle_cnt > vec_n * 10 + 1000) begin
                    $display("ERROR: timeout waiting for outputs (got %0d / %0d)", i, vec_n);
                    $finish(1);
                end
            end
        end
    endtask

    //=========================================================================
    // Clock / reset
    //=========================================================================
    initial begin
        clk = 1'b0;
        forever #CLK_HALF clk = ~clk;
    end

    initial begin
        rst_n   = 1'b0;
        data_i  = 16'h0000;
        valid_i = 1'b0;
        last_i  = 1'b0;
        in_idx  = 0;
        out_idx = 0;
        errors  = 0;
        cycle_cnt = 0;

        // Load test vectors
        $readmemh("input.hex",  input_mem);
        $readmemh("golden.hex", golden_mem);

        // Reset pulse
        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);

        //---------------------------------------------------------------
        // Test 1: 4096-element smoke vector against golden reference
        //---------------------------------------------------------------
        $display("[tb_rmsnorm_hw] Test 1: 4096-element smoke vector");
        feed_vector(N, 0, 16'h0000);
        collect_outputs(N);

        $writememh("result.hex", result_mem);
        $display("[tb_rmsnorm_hw] Captured %0d outputs, wrote result.hex", N);

        // Compute output RMS
        rms = 0.0;
        for (in_idx = 0; in_idx < N; in_idx = in_idx + 1) begin
            val = fp16_to_real(result_mem[in_idx]);
            rms = rms + val * val;
        end
        rms = $sqrt(rms / N);
        $display("[tb_rmsnorm_hw] Output RMS = %f  (expected ~1.0)", rms);
        if (rms < 0.98 || rms > 1.02) begin
            $display("[tb_rmsnorm_hw] RMS CHECK FAILED");
            errors = errors + 1;
        end else begin
            $display("[tb_rmsnorm_hw] RMS CHECK PASSED");
        end

        //---------------------------------------------------------------
        // Test 2: N==1 corner case — output must equal sign(x)
        //---------------------------------------------------------------
        $display("[tb_rmsnorm_hw] Test 2: N==1 corner cases");

        // positive x -> +1.0 (0x3C00)
        feed_vector(1, 1, 16'h4000); // 2.0
        collect_outputs(1);
        if (result_mem[0] !== 16'h3C00) begin
            $display("[tb_rmsnorm_hw] N=1 positive FAILED: expected 0x3C00, got 0x%04h", result_mem[0]);
            errors = errors + 1;
        end else begin
            $display("[tb_rmsnorm_hw] N=1 positive PASSED");
        end

        // negative x -> -1.0 (0xBC00)
        feed_vector(1, 1, 16'hC000); // -2.0
        collect_outputs(1);
        if (result_mem[0] !== 16'hBC00) begin
            $display("[tb_rmsnorm_hw] N=1 negative FAILED: expected 0xBC00, got 0x%04h", result_mem[0]);
            errors = errors + 1;
        end else begin
            $display("[tb_rmsnorm_hw] N=1 negative PASSED");
        end

        // x = 0 -> 0
        feed_vector(1, 1, 16'h0000);
        collect_outputs(1);
        if (result_mem[0] !== 16'h0000) begin
            $display("[tb_rmsnorm_hw] N=1 zero FAILED: expected 0x0000, got 0x%04h", result_mem[0]);
            errors = errors + 1;
        end else begin
            $display("[tb_rmsnorm_hw] N=1 zero PASSED");
        end

        //---------------------------------------------------------------
        // Final status
        //---------------------------------------------------------------
        if (errors == 0)
            $display("[tb_rmsnorm_hw] ALL CHECKS PASSED");
        else
            $display("[tb_rmsnorm_hw] %0d CHECK(S) FAILED", errors);

        $finish(errors == 0 ? 0 : 1);
    end

endmodule
