// tb_sfu_back2back.v — SF-12: sfu_top back-to-back ops
`timescale 1ns / 1ps

module tb_sfu_back2back;
    localparam CLK_HALF = 5;
    localparam MAX_DIM = 128;
    localparam SRAM_WORDS = 16384;
    localparam ADDR_WIDTH = 32;

    localparam [11:0] OFF_CTRL=12'h000, OFF_CMD=12'h004, OFF_STATUS=12'h008;
    localparam [11:0] OFF_I_ADDR=12'h00C, OFF_O_ADDR=12'h010, OFF_DIM=12'h014;
    localparam [11:0] OFF_POS=12'h018, OFF_IRQ_EN=12'h01C;
    localparam [3:0] OP_SOFTMAX=4'd0, OP_RMSNORM=4'd6;

    reg clk, rst_n;
    reg mmio_cs, mmio_we;
    reg [11:0] mmio_addr;
    reg [31:0] mmio_wdata;
    wire [31:0] mmio_rdata;
    wire mmio_ready;
    wire [ADDR_WIDTH-1:0] sram_raddr, sram_waddr;
    wire sram_ren, sram_wen;
    wire [31:0] sram_wdata;
    reg [31:0] sram_rdata;
    wire irq;

    sfu_top #(.ADDR_WIDTH(ADDR_WIDTH)) u_dut (
        .clk(clk), .rst_n(rst_n), .mmio_cs(mmio_cs), .mmio_we(mmio_we),
        .mmio_addr(mmio_addr), .mmio_wdata(mmio_wdata), .mmio_rdata(mmio_rdata),
        .mmio_ready(mmio_ready), .sram_rdata(sram_rdata),
        .sram_raddr(sram_raddr), .sram_ren(sram_ren),
        .sram_waddr(sram_waddr), .sram_wdata(sram_wdata), .sram_wen(sram_wen),
        .irq(irq)
    );

    reg [31:0] sram_mem [0:SRAM_WORDS-1];
    always @(*) begin
        if (sram_ren) sram_rdata = sram_mem[sram_raddr[15:2]];
        else sram_rdata = 32'd0;
    end
    always @(posedge clk) begin
        if (sram_wen) sram_mem[sram_waddr[15:2]] <= sram_wdata;
    end

    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    reg [31:0] out_words1 [0:MAX_DIM-1];
    reg [31:0] out_words2 [0:MAX_DIM-1];
    reg [31:0] out_wcount1, out_wcount2;
    reg capturing_op1, capturing_op2;

    always @(posedge clk) begin
        if (sram_wen) begin
            if (capturing_op1 && out_wcount1 < MAX_DIM) begin
                out_words1[out_wcount1] <= sram_wdata;
                out_wcount1 <= out_wcount1 + 1;
            end
            if (capturing_op2 && out_wcount2 < MAX_DIM) begin
                out_words2[out_wcount2] <= sram_wdata;
                out_wcount2 <= out_wcount2 + 1;
            end
        end
    end

    task mmio_write; input [11:0] a; input [31:0] v;
    begin @(negedge clk); mmio_cs=1'b1; mmio_we=1'b1; mmio_addr=a; mmio_wdata=v;
          @(negedge clk); mmio_cs=1'b0; mmio_we=1'b0; mmio_addr=12'd0; mmio_wdata=32'd0;
    end endtask

    task mmio_read; input [11:0] a; output [31:0] v;
    begin @(negedge clk); mmio_cs=1'b1; mmio_we=1'b0; mmio_addr=a;
          @(negedge clk); v=mmio_rdata; mmio_cs=1'b0; mmio_addr=12'd0;
    end endtask

    integer i, errors, fd, status;
    reg [31:0] stat_val;
    reg [15:0] vecA [0:15];
    reg [15:0] vecB [0:15];

    initial begin
        mmio_cs=0; mmio_we=0; mmio_addr=0; mmio_wdata=0;
        out_wcount1=0; out_wcount2=0; capturing_op1=0; capturing_op2=0;

        rst_n=1'b0; repeat(5) @(posedge clk); rst_n=1'b1; @(posedge clk);
        $display("[tb_sfu_bb] Reset released at %0t", $time);

        // Load vecA into SRAM: {1.0, 2.0, 0.5, -1.0, 0.0, 3.0, -2.0, 0.25}
        vecA[0]=16'h3C00; vecA[1]=16'h4000; vecA[2]=16'h3800; vecA[3]=16'hBC00;
        vecA[4]=16'h0000; vecA[5]=16'h4200; vecA[6]=16'hC000; vecA[7]=16'h3400;
        for (i=0;i<8;i=i+2) sram_mem[i>>1] = {vecA[i+1],vecA[i]};

        // Load vecB at word 128: {0.5,-0.5,1.0,-1.0,2.0,-2.0,3.0,0.0}
        vecB[0]=16'h3800; vecB[1]=16'hB800; vecB[2]=16'h3C00; vecB[3]=16'hBC00;
        vecB[4]=16'h4000; vecB[5]=16'hC000; vecB[6]=16'h4200; vecB[7]=16'h0000;
        for (i=0;i<8;i=i+2) sram_mem[128+(i>>1)] = {vecB[i+1],vecB[i]};

        // ---- Op1: SOFTMAX ----
        mmio_write(OFF_CTRL, {28'd0, OP_SOFTMAX});
        mmio_write(OFF_I_ADDR, 32'd0);
        mmio_write(OFF_O_ADDR, 32'd128);
        mmio_write(OFF_DIM, {16'd0, 16'd8});
        mmio_write(OFF_IRQ_EN, 32'd1);

        // Check STATUS before start
        mmio_read(OFF_STATUS, stat_val);
        $display("[tb_sfu_bb] STATUS before START op1: 0x%08h (BUSY=%0d DONE=%0d)", stat_val, stat_val[0], stat_val[1]);

        capturing_op1 = 1;
        mmio_write(OFF_CMD, 32'd1);
        $display("[tb_sfu_bb] Wrote CMD=START for op1 at %0t", $time);

        // Check STATUS after start
        mmio_read(OFF_STATUS, stat_val);
        $display("[tb_sfu_bb] STATUS after START op1: 0x%08h (BUSY=%0d DONE=%0d)", stat_val, stat_val[0], stat_val[1]);

        // Wait for DONE
        repeat(100000) begin
            @(posedge clk);
            if (irq) begin
                $display("[tb_sfu_bb] IRQ op1 at %0t", $time);
                disable wait_op1_done;
            end
        end
        begin : wait_op1_done
            $display("[tb_sfu_bb] Op1 DONE (IRQ) at %0t, captured %0d words", $time, out_wcount1);
        end

        capturing_op1 = 0;
        repeat(10) @(posedge clk);

        // Check STATUS after op1
        mmio_read(OFF_STATUS, stat_val);
        $display("[tb_sfu_bb] STATUS after op1 DONE: 0x%08h", stat_val);

        // ---- Op2: RMSNORM (no reset) ----
        mmio_write(OFF_CTRL, {28'd0, OP_RMSNORM});
        mmio_write(OFF_I_ADDR, 32'd512);   // 128 * 4 = byte addr 512
        mmio_write(OFF_O_ADDR, 32'd640);   // 160 * 4 = byte addr 640

        mmio_read(OFF_STATUS, stat_val);
        $display("[tb_sfu_bb] STATUS before START op2: 0x%08h", stat_val);

        capturing_op2 = 1;
        mmio_write(OFF_CMD, 32'd1);
        $display("[tb_sfu_bb] Wrote CMD=START for op2 at %0t", $time);

        // Wait for DONE op2
        repeat(100000) begin
            @(posedge clk);
            if (irq) begin
                $display("[tb_sfu_bb] IRQ op2 at %0t", $time);
                disable wait_op2_done;
            end
        end
        begin : wait_op2_done
            $display("[tb_sfu_bb] Op2 DONE (IRQ) at %0t, captured %0d words", $time, out_wcount2);
        end
        capturing_op2 = 0;
        repeat(10) @(posedge clk);

        // Write results
        fd = $fopen("CaduceusCore/rtl/test_vectors/sfu/sf12_back2back/op1_result.hex", "w");
        for (i=0;i<out_wcount1 && i<MAX_DIM;i=i+1) begin
            if (i*2<8) $fdisplay(fd, "%04h", out_words1[i][15:0]);
            if (i*2+1<8) $fdisplay(fd, "%04h", out_words1[i][31:16]);
        end
        $fclose(fd);

        fd = $fopen("CaduceusCore/rtl/test_vectors/sfu/sf12_back2back/op2_result.hex", "w");
        for (i=0;i<out_wcount2 && i<MAX_DIM;i=i+1) begin
            if (i*2<8) $fdisplay(fd, "%04h", out_words2[i][15:0]);
            if (i*2+1<8) $fdisplay(fd, "%04h", out_words2[i][31:16]);
        end
        $fclose(fd);

        // Compare
        status=$system("cd /home/prj/zhengs/caduceuscore && PYTHONPATH=/home/prj/zhengs/caduceuscore/CaduceusCore /NAS/Tools/anaconda3/bin/python3 CaduceusCore/scripts/compare_sfu.py CaduceusCore/rtl/test_vectors/sfu/sf12_back2back/op1 CaduceusCore/rtl/test_vectors/sfu/sf12_back2back/op1_result.hex");
        $display("[tb_sfu_bb] Op1 compare: %s", status==0?"PASS":"FAIL");
        status=$system("cd /home/prj/zhengs/caduceuscore && PYTHONPATH=/home/prj/zhengs/caduceuscore/CaduceusCore /NAS/Tools/anaconda3/bin/python3 CaduceusCore/scripts/compare_sfu.py CaduceusCore/rtl/test_vectors/sfu/sf12_back2back/op2 CaduceusCore/rtl/test_vectors/sfu/sf12_back2back/op2_result.hex");
        $display("[tb_sfu_bb] Op2 compare: %s", status==0?"PASS":"FAIL");

        $display("[tb_sfu_bb] SF-12 complete");
        #20; $finish;
    end
endmodule
