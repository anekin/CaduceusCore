// tb_rope_sf11.sv — SF-11: rope_hw angle=0 identity test (50 random pairs)
`timescale 1ns / 1ps

module tb_rope_sf11;
    localparam INPUT_FILE  = "CaduceusCore/rtl/test_vectors/sfu/sf11_rope_identity/input.hex";
    localparam GOLDEN_FILE = "CaduceusCore/rtl/test_vectors/sfu/sf11_rope_identity/golden.hex";
    localparam RESULT_FILE = "CaduceusCore/rtl/test_vectors/sfu/sf11_rope_identity/result.hex";
    localparam NUM_TESTS   = 50;
    localparam MAX_TESTS   = 64;
    localparam real TOL_ABS = 5e-3;

    reg clk, rst_n;
    reg [15:0] x_i, y_i, theta_i;
    reg valid_i;
    wire [15:0] x_o, y_o;
    wire valid_o;

    reg [47:0] input_vec [0:MAX_TESTS-1];
    reg [31:0] golden_vec [0:MAX_TESTS-1];
    reg [31:0] result_vec [0:MAX_TESTS-1];

    integer out_count, errors, i;
    real rx, ry, gx, gy, dx, dy;

    rope_hw dut (
        .clk(clk), .rst_n(rst_n),
        .x_i(x_i), .y_i(y_i), .theta_i(theta_i),
        .valid_i(valid_i),
        .x_o(x_o), .y_o(y_o), .valid_o(valid_o)
    );

    initial clk = 1'b0;
    always #5 clk = ~clk;

    function automatic real fp16_to_real;
        input [15:0] h;
        real sgn, expv, mant;
        begin
            sgn = h[15] ? -1.0 : 1.0;
            expv = h[14:10]; mant = h[9:0];
            if (expv == 0) fp16_to_real = 0.0;
            else fp16_to_real = sgn * (1.0 + mant / 1024.0) * (2.0 ** (expv - 15.0));
        end
    endfunction

    initial begin
        errors = 0; out_count = 0;
        x_i = 16'd0; y_i = 16'd0; theta_i = 16'd0; valid_i = 1'b0;

        $readmemh(INPUT_FILE, input_vec);
        $readmemh(GOLDEN_FILE, golden_vec);
        $display("[tb_rope_sf11] Loaded %0d test cases", NUM_TESTS);

        rst_n = 1'b0; #30; rst_n = 1'b1; #10;

        fork
            begin
                for (i = 0; i < NUM_TESTS; i = i + 1) begin
                    @(posedge clk);
                    x_i <= input_vec[i][47:32];
                    y_i <= input_vec[i][31:16];
                    theta_i <= input_vec[i][15:0];
                    valid_i <= 1'b1;
                end
                @(posedge clk);
                valid_i <= 1'b0;
            end
            begin
                while (out_count < NUM_TESTS) begin
                    @(posedge clk);
                    if (valid_o) begin
                        result_vec[out_count] = {x_o, y_o};
                        out_count = out_count + 1;
                    end
                end
            end
        join

        begin
            integer fd;
            fd = $fopen(RESULT_FILE, "w");
            for (i = 0; i < out_count; i = i + 1)
                $fdisplay(fd, "%04h %04h", result_vec[i][31:16], result_vec[i][15:0]);
            $fclose(fd);
        end

        $display("[tb_rope_sf11] Comparing %0d outputs (tolerance = %f)", out_count, TOL_ABS);
        for (i = 0; i < out_count; i = i + 1) begin
            rx = fp16_to_real(result_vec[i][31:16]);
            ry = fp16_to_real(result_vec[i][15:0]);
            gx = fp16_to_real(golden_vec[i][31:16]);
            gy = fp16_to_real(golden_vec[i][15:0]);
            dx = rx - gx; dy = ry - gy;
            if (dx < 0) dx = -dx; if (dy < 0) dy = -dy;
            if (dx > TOL_ABS || dy > TOL_ABS) begin
                $display("[tb_rope_sf11] MISMATCH test[%0d]: in=(%04h,%04h) out=(%04h,%04h) golden=(%04h,%04h) err=(%f,%f)",
                    i, input_vec[i][47:32], input_vec[i][31:16],
                    result_vec[i][31:16], result_vec[i][15:0],
                    golden_vec[i][31:16], golden_vec[i][15:0], dx, dy);
                errors = errors + 1;
            end
        end

        if (errors == 0)
            $display("[tb_rope_sf11] ALL %0d TESTS PASSED", NUM_TESTS);
        else
            $display("[tb_rope_sf11] %0d OF %0d TESTS FAILED", errors, NUM_TESTS);
        #20; $finish;
    end

    initial begin #200000; $display("[tb_rope_sf11] TIMEOUT"); $finish; end
endmodule
