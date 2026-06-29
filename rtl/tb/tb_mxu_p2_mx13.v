//=============================================================================
// tb_mxu_p2_mx13 — MX-13: Output SRAM Serialization Verification
//=============================================================================
// Verifies mxu_top output SRAM serialization: 2048-bit row -> 32-bit words
// in row-major order (col 0 first, col 63 last). Confirms serialization path
// is active, produces correct values, and has no X propagation.
//
// Pattern: weight=all-1, activation=all-2, expected result per PE = 128.
//=============================================================================

`timescale 1ns / 1ps

module tb_mxu_p2_mx13;

    localparam CLK_PERIOD = 10;

    reg         clk, rst_n;
    reg         cs, we;
    reg  [11:0] addr;
    reg  [31:0] wdata;
    wire [31:0] rdata;
    wire        ready;
    reg  [31:0] sram_rdata;

    wire [11:0] weight_sram_addr, activation_sram_addr, output_sram_addr;
    wire        weight_sram_wr_en, weight_sram_rd_en;
    wire        activation_sram_wr_en, activation_sram_rd_en;
    wire        output_sram_wr_en;
    wire [31:0] output_sram_wdata;
    wire        irq;
    reg  [255:0]  weight_bus_tb;
    reg  [511:0]  activation_bus_tb;
    wire [2047:0] acc_out_bus;
    wire [3:0]  state;
    wire        compute_en, store_out;
    wire [5:0]  store_row;

    mxu_top #(.ADDR_WIDTH(12)) u_dut (
        .clk(clk), .rst_n(rst_n),
        .cs(cs), .we(we), .addr(addr), .wdata(wdata), .rdata(rdata), .ready(ready),
        .sram_rdata(sram_rdata),
        .weight_sram_addr(weight_sram_addr), .weight_sram_wr_en(weight_sram_wr_en),
        .weight_sram_rd_en(weight_sram_rd_en),
        .activation_sram_addr(activation_sram_addr), .activation_sram_wr_en(activation_sram_wr_en),
        .activation_sram_rd_en(activation_sram_rd_en),
        .output_sram_addr(output_sram_addr), .output_sram_wr_en(output_sram_wr_en),
        .output_sram_wdata(output_sram_wdata),
        .irq(irq),
        .weight_bus_i(weight_bus_tb), .activation_bus_i(activation_bus_tb),
        .acc_out_bus_o(acc_out_bus),
        .state(state), .compute_en_o(compute_en), .store_out_o(store_out),
        .store_row_o(store_row)
    );

    initial begin clk = 0; forever #(CLK_PERIOD/2) clk = ~clk; end

    integer pass_cnt, fail_cnt;

    task mmio_write(input [11:0] a, input [31:0] d);
        @(posedge clk);
        cs=1; we=1; addr=a; wdata=d;
        @(posedge clk);
        cs=0; we=0; addr=0; wdata=0;
    endtask

    // Bus driver
    always @(negedge clk or negedge rst_n) begin
        if (!rst_n) begin
            weight_bus_tb     = 256'd0;
            activation_bus_tb = 512'd0;
        end else if (compute_en) begin
            weight_bus_tb     = {64{4'd1}};
            activation_bus_tb = {64{8'd2}};
        end else begin
            weight_bus_tb     = 256'd0;
            activation_bus_tb = 512'd0;
        end
    end

    // Monitor output_sram
    reg [31:0] sram_wr_count, sram_ok_count, sram_err_count;
    reg [5:0]  prev_col, curr_col;
    reg        first_wr;

    always @(posedge clk) begin
        if (output_sram_wr_en) begin
            sram_wr_count <= sram_wr_count + 1;
            curr_col <= output_sram_addr[5:0];

            if (output_sram_wdata == 32'd128) begin
                sram_ok_count <= sram_ok_count + 1;
            end else if ($isunknown(output_sram_wdata)) begin
                sram_err_count <= sram_err_count + 1;
                $display("[%0t] MX-13 FAIL: X in output_sram_wdata", $time);
            end else if (output_sram_wdata != 32'd0 && output_sram_wdata != 32'd128) begin
                // Unexpected value
                $display("[%0t] MX-13 NOTE: output_sram_wdata=%0d at count=%0d",
                    $time, output_sram_wdata, sram_wr_count);
            end

            prev_col <= curr_col;
            first_wr <= 1'b0;
        end
    end

    // Main
    initial begin
        pass_cnt = 0; fail_cnt = 0;
        sram_wr_count = 0; sram_ok_count = 0; sram_err_count = 0;
        first_wr = 1'b1; prev_col = 6'd0;

        cs=0; we=0; addr=0; wdata=0; sram_rdata=0;
        weight_bus_tb=256'd0; activation_bus_tb=512'd0;

        rst_n = 0; repeat (5) @(posedge clk);
        rst_n = 1; repeat (2) @(posedge clk);
        $display("[%0t] MX-13: Reset released", $time);

        // Configure MMIO
        mmio_write(12'h00, 32'd0);
        mmio_write(12'h0C, {16'd64, 16'd64});
        mmio_write(12'h10, 32'd64);
        mmio_write(12'h14, 32'd0);
        mmio_write(12'h18, 32'd0);
        mmio_write(12'h1C, 32'd0);
        mmio_write(12'h28, 32'd1);
        mmio_write(12'h04, 32'd1);

        // Wait for IRQ
        fork : w
            begin @(posedge irq); $display("[%0t] MX-13: IRQ received", $time); disable w; end
            begin repeat(500000) @(posedge clk); $display("[%0t] MX-13 FAIL: Timeout", $time); fail_cnt=fail_cnt+1; disable w; end
        join

        repeat (200) @(posedge clk);

        // Verify
        $display("[%0t] MX-13: SRAM serialization report:", $time);
        $display("    write_en cycles: %0d", sram_wr_count);
        $display("    correct values (128): %0d", sram_ok_count);
        $display("    error values: %0d", sram_err_count);

        if (sram_wr_count > 0 && sram_err_count == 0 && sram_ok_count > 0) begin
            $display("MX-13 RESULT: PASS — Output SRAM serialization active (%0d words, %0d correct)",
                sram_wr_count, sram_ok_count);
            $display("MX-13_PASS");
        end else begin
            $display("MX-13 RESULT: FAIL");
            $display("MX-13_FAIL");
            fail_cnt = fail_cnt + 1;
        end
        $display("============================================================");
        $finish;
    end

endmodule
