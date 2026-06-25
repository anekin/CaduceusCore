// tb_silu_hw.sv — Self-checking testbench for silu_hw
//
// Loads input.hex / golden.hex from
// CaduceusCore/rtl/test_vectors/sfu/silu_smoke/, drives the DUT one element
// per cycle, captures FP16 outputs concurrently, writes result.hex, and
// compares against the golden reference using compare_rtl.py float16 tolerance
// (abs <= 1e-3 OR rel <= 1e-2).

`timescale 1ns / 1ps

module tb_silu_hw;

    localparam INPUT_FILE  = "CaduceusCore/rtl/test_vectors/sfu/silu_smoke/input.hex";
    localparam GOLDEN_FILE = "CaduceusCore/rtl/test_vectors/sfu/silu_smoke/golden.hex";
    localparam RESULT_FILE = "CaduceusCore/rtl/test_vectors/sfu/silu_smoke/result.hex";
    localparam MAX_VEC     = 2048;

    reg         clk;
    reg         rst_n;
    reg  [15:0] data_i;
    reg         valid_i;
    wire [15:0] data_o;
    wire        valid_o;

    reg [15:0] input_vec  [0:MAX_VEC-1];
    reg [15:0] golden_vec [0:MAX_VEC-1];
    reg [15:0] result_vec [0:MAX_VEC-1];

    integer in_count;
    integer out_count;
    integer i;
    integer errors;
    real    g, r, abs_err, rel_err;

    // DUT instantiation
    silu_hw dut (
        .clk    (clk),
        .rst_n  (rst_n),
        .data_i (data_i),
        .valid_i(valid_i),
        .data_o (data_o),
        .valid_o(valid_o)
    );

    // Clock: 100 MHz
    initial clk = 1'b0;
    always #5 clk = ~clk;

    // Helper: convert FP16 pattern to real
    function automatic real fp16_to_real;
        input [15:0] h;
        real sgn;
        real expv;
        real mant;
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

    // Main stimulus
    initial begin
        errors    = 0;
        in_count  = 0;
        out_count = 0;
        data_i    = 16'd0;
        valid_i   = 1'b0;

        // Load vectors
        $readmemh(INPUT_FILE, input_vec);
        $readmemh(GOLDEN_FILE, golden_vec);

        // Count non-X input entries
        for (i = 0; i < MAX_VEC; i = i + 1) begin
            if (input_vec[i] !== 16'hxxxx)
                in_count = in_count + 1;
        end

        $display("[tb_silu_hw] Loaded %0d input elements", in_count);

        // Reset
        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;

        // Drive inputs and capture outputs concurrently
        fork
            begin
                for (i = 0; i < in_count; i = i + 1) begin
                    @(posedge clk);
                    data_i  <= input_vec[i];
                    valid_i <= 1'b1;
                end
                @(posedge clk);
                valid_i <= 1'b0;
                data_i  <= 16'd0;
            end
            begin
                while (out_count < in_count) begin
                    @(posedge clk);
                    if (valid_o) begin
                        result_vec[out_count] = data_o;
                        out_count = out_count + 1;
                    end
                end
            end
        join

        $display("[tb_silu_hw] Captured %0d outputs", out_count);

        // Write result.hex
        begin
            integer fd;
            $display("[tb_silu_hw] Writing outputs to %s", RESULT_FILE);
            fd = $fopen(RESULT_FILE, "w");
            for (i = 0; i < out_count; i = i + 1)
                $fdisplay(fd, "%04h", result_vec[i]);
            $fclose(fd);
        end

        // Compare against golden with compare_rtl.py float16 tolerance
        for (i = 0; i < out_count; i = i + 1) begin
            g = fp16_to_real(golden_vec[i]);
            r = fp16_to_real(result_vec[i]);
            abs_err = (g > r) ? (g - r) : (r - g);
            rel_err = abs_err / ((g > 0 ? g : -g) + 1e-12);

            if (!((abs_err <= 1e-3) || (rel_err <= 1e-2))) begin
                $display("  MISMATCH[%0d]: in=0x%04h golden=0x%04h (%f) result=0x%04h (%f) abs=%f rel=%f",
                         i, input_vec[i], golden_vec[i], g, result_vec[i], r, abs_err, rel_err);
                errors = errors + 1;
            end
        end

        $display("[tb_silu_hw] Compared %0d elements, %0d mismatches", out_count, errors);

        if (errors == 0)
            $display("[tb_silu_hw] ALL CHECKS PASSED");
        else
            $display("[tb_silu_hw] %0d CHECK(S) FAILED", errors);

        #20;
        $finish;
    end

    // Timeout watchdog (generous for 1007-element vector at 100 MHz)
    initial begin
        #20000000;
        $display("[tb_silu_hw] TIMEOUT");
        $finish;
    end

endmodule
