// tb_buffers_mx01.v — MX-01: weight_buffer nibble ordering verification
//
// Writes known byte patterns to all 512 weight_buffer addresses.
// Each 32-bit word contains 8 INT4 weights packed 2:1 (low nibble=even index).
// Reads back all addresses and dumps to result.hex for compare_rtl.py verification.

`timescale 1ns/1ps

module tb_buffers_mx01;

    localparam WB_DEPTH      = 512;
    localparam WB_ADDR_WIDTH = 10;

    reg clk;
    reg rst_n;
    reg                     w_wr_en;
    reg  [WB_ADDR_WIDTH-1:0] w_wr_addr;
    reg  [31:0]             w_wr_data;
    reg                     w_rd_en;
    reg  [WB_ADDR_WIDTH-1:0] w_rd_addr;
    wire [31:0]             w_rd_data;

    weight_buffer #(.DEPTH(WB_DEPTH), .ADDR_WIDTH(WB_ADDR_WIDTH)) wbuf (
        .clk(clk), .rst_n(rst_n),
        .wr_en(w_wr_en), .wr_addr(w_wr_addr), .wr_data(w_wr_data),
        .rd_en(w_rd_en), .rd_addr(w_rd_addr), .rd_data(w_rd_data)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    integer i;
    integer fd;
    integer pass_count, fail_count;
    reg [31:0] expected_word;

    function [31:0] build_mx01_word(input [9:0] addr);
        reg [3:0] b0_lo, b0_hi, b1_lo, b1_hi, b2_lo, b2_hi, b3_lo, b3_hi;
        begin
            b0_lo = (addr + 0) & 4'hF;
            b0_hi = (addr + 1) & 4'hF;
            b1_lo = (addr + 2) & 4'hF;
            b1_hi = (addr + 3) & 4'hF;
            b2_lo = (addr + 4) & 4'hF;
            b2_hi = (addr + 5) & 4'hF;
            b3_lo = (addr + 6) & 4'hF;
            b3_hi = (addr + 7) & 4'hF;
            build_mx01_word = {b3_hi, b3_lo, b2_hi, b2_lo, b1_hi, b1_lo, b0_hi, b0_lo};
        end
    endfunction

    initial begin
        pass_count = 0; fail_count = 0;
        w_wr_en = 0; w_wr_addr = 0; w_wr_data = 0; w_rd_en = 0; w_rd_addr = 0;

        rst_n = 0;
        repeat(3) @(posedge clk);
        rst_n = 1;
        repeat(2) @(posedge clk);

        $display("=== MX-01: weight_buffer nibble ordering ===");

        // Write all 512 addresses with known nibble patterns
        for (i = 0; i < WB_DEPTH; i = i + 1) begin
            @(posedge clk);
            w_wr_en   = 1;
            w_wr_addr = i[WB_ADDR_WIDTH-1:0];
            w_wr_data = build_mx01_word(i[WB_ADDR_WIDTH-1:0]);
        end
        @(posedge clk);
        w_wr_en = 0;

        // Open result file for compare_rtl.py
        fd = $fopen("mx01_result.hex", "w");

        // Read back all addresses and dump to hex
        for (i = 0; i < WB_DEPTH; i = i + 1) begin
            @(posedge clk);
            w_rd_en   = 1;
            w_rd_addr = i[WB_ADDR_WIDTH-1:0];
            expected_word = build_mx01_word(i[WB_ADDR_WIDTH-1:0]);
            @(posedge clk);  // 1-cycle latency
            w_rd_en   = 0;
            $fwrite(fd, "%08h\n", w_rd_data);
            if (w_rd_data === expected_word) begin
                pass_count = pass_count + 1;
            end else begin
                fail_count = fail_count + 1;
                $display("[FAIL] addr=%0d: expected=0x%08h, got=0x%08h", i, expected_word, w_rd_data);
            end
        end

        $fclose(fd);

        $display("\n=== MX-01 Summary ===");
        $display("Checks: %0d passed, %0d failed", pass_count, fail_count);
        $display("Result hex: mx01_result.hex (%0d words)", WB_DEPTH);

        // Also verify individual nibble positions on first few addresses
        $display("\n=== Nibble position spot-check ===");
        for (i = 0; i < 4; i = i + 1) begin
            @(posedge clk);
            w_rd_en = 1;
            w_rd_addr = i[WB_ADDR_WIDTH-1:0];
            @(posedge clk);
            w_rd_en = 0;
            $display("addr=%0d: 0x%08h (bytes: [%02x][%02x][%02x][%02x])",
                i, w_rd_data,
                w_rd_data[7:0], w_rd_data[15:8], w_rd_data[23:16], w_rd_data[31:24]);
            $display("  byte0: lo-nibble(weight[%0d])=%0d, hi-nibble(weight[%0d])=%0d",
                i*8+0, w_rd_data[3:0], i*8+1, w_rd_data[7:4]);
            $display("  byte1: lo-nibble(weight[%0d])=%0d, hi-nibble(weight[%0d])=%0d",
                i*8+2, w_rd_data[11:8], i*8+3, w_rd_data[15:12]);
            $display("  byte2: lo-nibble(weight[%0d])=%0d, hi-nibble(weight[%0d])=%0d",
                i*8+4, w_rd_data[19:16], i*8+5, w_rd_data[23:20]);
            $display("  byte3: lo-nibble(weight[%0d])=%0d, hi-nibble(weight[%0d])=%0d",
                i*8+6, w_rd_data[27:24], i*8+7, w_rd_data[31:28]);
        end

        if (fail_count == 0)
            $display("\nMX-01: PASSED");
        else begin
            $display("\nMX-01: FAILED (%0d mismatches)", fail_count);
            $fatal(2, "MX-01 FAILED");
        end
        $finish;
    end

endmodule
