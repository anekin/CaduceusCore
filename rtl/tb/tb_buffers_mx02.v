// tb_buffers_mx02.v — MX-02: weight_buffer multi-cycle write burst
//
// Writes all 512 addresses (first pass), then immediately writes all 512
// again (second pass) with no read interleaving. 1024 back-to-back writes.
// Reads back all addresses after burst; final values = second-pass values.

`timescale 1ns/1ps

module tb_buffers_mx02;

    localparam WB_DEPTH      = 512;
    localparam WB_ADDR_WIDTH = 10;

    reg clk, rst_n;
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

    integer i, fd;
    integer pass_count, fail_count, burst_count;
    reg [31:0] pass1_word, pass2_word;

    function [31:0] build_pass1_word(input [9:0] addr);
        begin
            build_pass1_word = {8'hAA, 8'hBB, 8'hCC, addr[7:0]};
        end
    endfunction

    function [31:0] build_pass2_word(input [9:0] addr);
        reg [7:0] b0, b1, b2, b3;
        begin
            b0 = (addr + 8'h10) & 8'hFF;
            b1 = (addr + 8'h20) & 8'hFF;
            b2 = (addr + 8'h40) & 8'hFF;
            b3 = (addr + 8'h80) & 8'hFF;
            build_pass2_word = {b3, b2, b1, b0};
        end
    endfunction

    initial begin
        pass_count = 0; fail_count = 0; burst_count = 0;
        w_wr_en = 0; w_wr_addr = 0; w_wr_data = 0; w_rd_en = 0; w_rd_addr = 0;

        rst_n = 0;
        repeat(3) @(posedge clk);
        rst_n = 1;
        repeat(2) @(posedge clk);

        $display("=== MX-02: weight_buffer multi-cycle write burst ===");

        // Pass 1: write all 512 addresses
        $display("Pass 1: writing 512 addresses...");
        for (i = 0; i < WB_DEPTH; i = i + 1) begin
            @(posedge clk);
            w_wr_en   = 1;
            w_wr_addr = i[WB_ADDR_WIDTH-1:0];
            w_wr_data = build_pass1_word(i[WB_ADDR_WIDTH-1:0]);
            burst_count = burst_count + 1;
        end
        $display("Pass 1 complete: %0d writes", burst_count);

        // Pass 2: back-to-back, no read interleaving — write all 512 again
        $display("Pass 2: back-to-back write burst (512 addresses)...");
        for (i = 0; i < WB_DEPTH; i = i + 1) begin
            @(posedge clk);
            w_wr_en   = 1;
            w_wr_addr = i[WB_ADDR_WIDTH-1:0];
            w_wr_data = build_pass2_word(i[WB_ADDR_WIDTH-1:0]);
            burst_count = burst_count + 1;
        end
        @(posedge clk);
        w_wr_en = 0;
        $display("Total writes: %0d (no read interleaving)", burst_count);

        // Read back all addresses — expect pass2 values
        fd = $fopen("mx02_result.hex", "w");
        $display("Reading back all 512 addresses...");
        for (i = 0; i < WB_DEPTH; i = i + 1) begin
            @(posedge clk);
            w_rd_en   = 1;
            w_rd_addr = i[WB_ADDR_WIDTH-1:0];
            pass2_word = build_pass2_word(i[WB_ADDR_WIDTH-1:0]);
            @(posedge clk);
            w_rd_en   = 0;
            $fwrite(fd, "%08h\n", w_rd_data);
            if (w_rd_data === pass2_word) begin
                pass_count = pass_count + 1;
            end else begin
                fail_count = fail_count + 1;
                pass1_word = build_pass1_word(i[WB_ADDR_WIDTH-1:0]);
                $display("[FAIL] addr=%0d: got=0x%08h, expected_pass2=0x%08h, pass1=0x%08h",
                    i, w_rd_data, pass2_word, pass1_word);
            end
        end
        $fclose(fd);

        $display("\n=== MX-02 Summary ===");
        $display("Total burst writes: %0d (512 + 512, no reads between)", burst_count);
        $display("Read-back checks: %0d passed, %0d failed", pass_count, fail_count);
        $display("Result hex: mx02_result.hex (%0d words)", WB_DEPTH);

        if (fail_count == 0)
            $display("\nMX-02: PASSED");
        else begin
            $display("\nMX-02: FAILED (%0d mismatches)", fail_count);
            $fatal(2, "MX-02 FAILED");
        end
        $finish;
    end

endmodule
