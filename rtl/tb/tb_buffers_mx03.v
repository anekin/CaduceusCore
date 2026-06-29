// tb_buffers_mx03.v — MX-03: activation_buffer concurrent read-write
//
// Tests simultaneous wr_en + rd_en on the same address.
// On concurrent cycle: rd_data = OLD value (NBA: write happens after read sample).
// Next cycle: rd_data = NEW value (write committed).
// Final: writes golden-matching values to all 1024 addresses, dumps result.hex.

`timescale 1ns/1ps

module tb_buffers_mx03;

    localparam AB_DEPTH      = 1024;
    localparam AB_ADDR_WIDTH = 11;

    reg clk, rst_n;
    reg                     a_wr_en;
    reg  [AB_ADDR_WIDTH-1:0] a_wr_addr;
    reg  [31:0]             a_wr_data;
    reg                     a_rd_en;
    reg  [AB_ADDR_WIDTH-1:0] a_rd_addr;
    wire [31:0]             a_rd_data;

    activation_buffer #(.DEPTH(AB_DEPTH), .ADDR_WIDTH(AB_ADDR_WIDTH)) abuf (
        .clk(clk), .rst_n(rst_n),
        .wr_en(a_wr_en), .wr_addr(a_wr_addr), .wr_data(a_wr_data),
        .rd_en(a_rd_en), .rd_addr(a_rd_addr), .rd_data(a_rd_data)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    integer i, fd;
    integer pass_count, fail_count;
    reg [31:0] old_val, new_val;

    function [31:0] build_final_word(input [10:0] addr);
        reg [7:0] b0, b1, b2, b3;
        begin
            b0 = (addr + 8'hAB) & 8'hFF;
            b1 = (addr + 8'hCD) & 8'hFF;
            b2 = (addr + 8'hEF) & 8'hFF;
            b3 = (addr + 8'h12) & 8'hFF;
            build_final_word = {b3, b2, b1, b0};
        end
    endfunction

    initial begin
        pass_count = 0; fail_count = 0;
        a_wr_en = 0; a_wr_addr = 0; a_wr_data = 0; a_rd_en = 0; a_rd_addr = 0;

        rst_n = 0;
        repeat(3) @(posedge clk);
        rst_n = 1;
        repeat(2) @(posedge clk);

        $display("=== MX-03: activation_buffer concurrent read-write ===");

        // Write known "old" values
        for (i = 0; i < 16; i = i + 1) begin
            @(posedge clk);
            a_wr_en   = 1;
            a_wr_addr = i[AB_ADDR_WIDTH-1:0];
            a_wr_data = 32'hAAAAAAAA | i[AB_ADDR_WIDTH-1:0];
        end
        @(posedge clk);
        a_wr_en = 0;

        // Concurrent read-write on addresses 0..7
        for (i = 0; i < 8; i = i + 1) begin
            @(posedge clk);
            a_wr_en   = 1;
            a_wr_addr = i[AB_ADDR_WIDTH-1:0];
            a_wr_data = 32'hBBBBBBBB | i[AB_ADDR_WIDTH-1:0];
            a_rd_en   = 1;
            a_rd_addr = i[AB_ADDR_WIDTH-1:0];
            old_val   = 32'hAAAAAAAA | i[AB_ADDR_WIDTH-1:0];
            @(posedge clk);
            a_wr_en   = 0;
            a_rd_en   = 0;
            if (a_rd_data === old_val) begin
                pass_count = pass_count + 1;
                $display("[PASS] addr=%0d concurrent: rd=0x%08h (old)", i, a_rd_data);
            end else begin
                fail_count = fail_count + 1;
                $display("[FAIL] addr=%0d concurrent: exp=0x%08h got=0x%08h", i, old_val, a_rd_data);
            end
        end

        // Verify new values committed
        for (i = 0; i < 8; i = i + 1) begin
            @(posedge clk);
            a_rd_en   = 1;
            a_rd_addr = i[AB_ADDR_WIDTH-1:0];
            new_val   = 32'hBBBBBBBB | i[AB_ADDR_WIDTH-1:0];
            @(posedge clk);
            a_rd_en   = 0;
            if (a_rd_data === new_val) begin
                pass_count = pass_count + 1;
                $display("[PASS] addr=%0d post-write: 0x%08h", i, a_rd_data);
            end else begin
                fail_count = fail_count + 1;
                $display("[FAIL] addr=%0d post-write: exp=0x%08h got=0x%08h", i, new_val, a_rd_data);
            end
        end

        // Final: write all addresses with golden-matching pattern
        $display("Writing final values to all 1024 addresses...");
        for (i = 0; i < AB_DEPTH; i = i + 1) begin
            @(posedge clk);
            a_wr_en   = 1;
            a_wr_addr = i[AB_ADDR_WIDTH-1:0];
            a_wr_data = build_final_word(i[AB_ADDR_WIDTH-1:0]);
        end
        @(posedge clk);
        a_wr_en = 0;

        // Read back for compare_rtl.py
        fd = $fopen("mx03_result.hex", "w");
        for (i = 0; i < AB_DEPTH; i = i + 1) begin
            @(posedge clk);
            a_rd_en   = 1;
            a_rd_addr = i[AB_ADDR_WIDTH-1:0];
            @(posedge clk);
            a_rd_en   = 0;
            $fwrite(fd, "%08h\n", a_rd_data);
        end
        $fclose(fd);

        $display("\n=== MX-03 Summary ===");
        $display("Concurrent checks: %0d passed, %0d failed", pass_count, fail_count);
        $display("Result hex: mx03_result.hex (%0d words)", AB_DEPTH);

        if (fail_count == 0)
            $display("\nMX-03: PASSED");
        else begin
            $display("\nMX-03: FAILED");
            $fatal(2, "MX-03 FAILED");
        end
        $finish;
    end

endmodule
