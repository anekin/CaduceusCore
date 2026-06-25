//=============================================================================
// tb_layernorm_corner — Corner-case check for layernorm_hw
//=============================================================================
// Drives two tiny vectors and verifies the hardware forces the output to 0
// when (x - mean) == 0 (N == 1 and all-equal inputs).
//
// Usage:
//   cd CaduceusCore/rtl/test_vectors/sfu/layernorm_smoke
//   vcs -full64 -sverilog -timescale=1ns/1ps -top tb_layernorm_corner \
//       ../../../sfu/layernorm_hw.v tb_layernorm_corner.v -o simv_ln_corner
//   ./simv_ln_corner
//=============================================================================

`timescale 1ns / 1ps

module tb_layernorm_corner;

    localparam CLK_HALF = 5;

    reg         clk;
    reg         rst_n;
    reg  [15:0] data_i;
    reg         valid_i;
    reg         last_i;
    wire [15:0] data_o;
    wire        valid_o;

    integer     errors;

    layernorm_hw u_dut (
        .clk     (clk),
        .rst_n   (rst_n),
        .data_i  (data_i),
        .valid_i (valid_i),
        .last_i  (last_i),
        .data_o  (data_o),
        .valid_o (valid_o)
    );

    initial begin
        clk = 1'b0;
        forever #CLK_HALF clk = ~clk;
    end

    // Helper: drive one vector, expect every output to be 0.
    task drive_and_check;
        input [31:0] n;
        input [15:0] value;
        input [255:0] name;
        integer k;
        integer got;
        begin
            got = 0;
            for (k = 0; k < n; k = k + 1) begin
                @(posedge clk);
                data_i  <= value;
                valid_i <= 1'b1;
                last_i  <= (k == n - 1) ? 1'b1 : 1'b0;
            end
            @(posedge clk);
            valid_i <= 1'b0;
            last_i  <= 1'b0;

            // Wait for outputs
            for (k = 0; k < n; k = k + 1) begin
                @(negedge clk);
                while (!valid_o) @(negedge clk);
                got = got + 1;
                if (data_o !== 16'h0000) begin
                    $display("FAIL %0s: output[%0d] = %h (expected 0)", name, k, data_o);
                    errors = errors + 1;
                end
            end
            $display("%0s: got %0d outputs", name, got);
        end
    endtask

    initial begin
        rst_n   = 1'b0;
        data_i  = 16'h0000;
        valid_i = 1'b0;
        last_i  = 1'b0;
        errors  = 0;

        repeat (4) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);

        // 1.5 in FP16 = 0x3E00
        drive_and_check(1, 16'h3E00, "N=1");
        // 2.5 in FP16 = 0x4100
        drive_and_check(16, 16'h4100, "all-equal");

        if (errors == 0)
            $display("layernorm_corner: PASS");
        else
            $display("layernorm_corner: FAIL with %0d errors", errors);
        $finish(errors != 0);
    end

endmodule
