//=============================================================================
// tb_gelu_hw — self-checking smoke testbench for gelu_hw
//=============================================================================
// Reads input.hex / golden.hex (FP16), drives the 4-stage pipeline, captures
// outputs, writes result.hex, and checks against golden with compare_rtl
// float16 tolerance (abs <= 1e-3 OR rel <= 1e-2).
//
// Plusargs:
//   +INPUT_FILE=<path>   default: CaduceusCore/rtl/test_vectors/sfu/gelu_smoke/input.hex
//   +GOLDEN_FILE=<path>  default: CaduceusCore/rtl/test_vectors/sfu/gelu_smoke/golden.hex
//   +RESULT_FILE=<path>  default: gelu_result.hex
//=============================================================================

`timescale 1ns / 1ps

module tb_gelu_hw;

    localparam MAX_VEC     = 256;
    localparam ABS_TOL_1K  = 1;   // 1e-3 scaled by 1000 for integer compare
    localparam REL_TOL_PCT = 1;   // 1e-2 scaled by 100 for integer percent

    reg         clk;
    reg         rst_n;
    reg  [15:0] data_i;
    reg         valid_i;
    wire [15:0] data_o;
    wire        valid_o;

    reg [15:0] input_vec  [0:MAX_VEC-1];
    reg [15:0] golden_vec [0:MAX_VEC-1];
    reg [15:0] result_vec [0:MAX_VEC-1];

    reg [1023:0] input_path;
    reg [1023:0] golden_path;
    reg [1023:0] result_path;

    integer in_count;
    integer out_count;
    integer i;
    integer errors;
    real    greal;
    real    rreal;
    real    absdiff;
    real    reldiff;

    gelu_hw dut (
        .clk    (clk),
        .rst_n  (rst_n),
        .data_i (data_i),
        .valid_i(valid_i),
        .data_o (data_o),
        .valid_o(valid_o)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

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

    initial begin
        errors    = 0;
        in_count  = 0;
        out_count = 0;
        data_i    = 16'd0;
        valid_i   = 1'b0;

        if (!$value$plusargs("INPUT_FILE=%s", input_path))
            input_path = "CaduceusCore/rtl/test_vectors/sfu/gelu_smoke/input.hex";
        if (!$value$plusargs("GOLDEN_FILE=%s", golden_path))
            golden_path = "CaduceusCore/rtl/test_vectors/sfu/gelu_smoke/golden.hex";
        if (!$value$plusargs("RESULT_FILE=%s", result_path))
            result_path = "gelu_result.hex";

        $readmemh(input_path,  input_vec);
        $readmemh(golden_path, golden_vec);

        for (i = 0; i < MAX_VEC; i = i + 1) begin
            if (input_vec[i] !== 16'hxxxx)
                in_count = in_count + 1;
        end

        $display("[tb_gelu_hw] Loaded %0d input / golden elements", in_count);

        rst_n = 1'b0;
        #30;
        rst_n = 1'b1;
        #10;

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

        begin
            integer fd;
            fd = $fopen(result_path, "w");
            for (i = 0; i < out_count; i = i + 1)
                $fdisplay(fd, "%04h", result_vec[i]);
            $fclose(fd);
        end

        for (i = 0; i < out_count; i = i + 1) begin
            greal = fp16_to_real(golden_vec[i]);
            rreal = fp16_to_real(result_vec[i]);
            absdiff = (greal > rreal) ? (greal - rreal) : (rreal - greal);
            reldiff = absdiff / (((greal) >= 0.0 ? (greal) : -(greal)) + 1e-12);

            if (!((absdiff <= 0.001) || (reldiff <= 0.01))) begin
                $display("  FAIL out[%0d]: golden=%f (0x%04h), result=%f (0x%04h), abs=%f, rel=%f",
                         i, greal, golden_vec[i], rreal, result_vec[i], absdiff, reldiff);
                errors = errors + 1;
            end
        end

        if (errors == 0)
            $display("[tb_gelu_hw] ALL CHECKS PASSED (%0d elements)", out_count);
        else
            $display("[tb_gelu_hw] %0d CHECK(S) FAILED", errors);

        #20;
        $finish;
    end

    initial begin
        #100000;
        $display("[tb_gelu_hw] TIMEOUT");
        $finish;
    end

endmodule
