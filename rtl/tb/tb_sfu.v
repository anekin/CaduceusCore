//=============================================================================
// tb_sfu — Self-Checking SFU Top-Level Testbench
//=============================================================================
// Reads test vectors from +testdir+, drives sfu_top through MMIO, provides a
// simple 32-bit SRAM model, captures sram_wdata writes, and compares against
// golden_output.hex using sim/compare_rtl.py float16 tolerance.
//
// Usage:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_sfu \
//       CaduceusCore/rtl/tb/tb_sfu.v CaduceusCore/rtl/sfu/*.v -o simv_tb_sfu
//   ./simv_tb_sfu +testdir=CaduceusCore/rtl/test_vectors/sfu/softmax_smoke \
//                 +scenario=softmax_smoke
//=============================================================================

`timescale 1ns / 1ps

module tb_sfu;

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam CLK_HALF     = 5;                 // 100 MHz
    localparam MAX_DIM      = 4096;              // max supported elements/pairs
    localparam MAX_ELEMS    = MAX_DIM * 2;       // enough for RoPE pair elements
    localparam SRAM_WORDS   = 16384;             // 64 KB SRAM, word addressable
    localparam ADDR_WIDTH   = 32;

    // SFU MMIO offsets (from regmap.py)
    localparam [11:0] OFF_CTRL    = 12'h000;
    localparam [11:0] OFF_CMD     = 12'h004;
    localparam [11:0] OFF_STATUS  = 12'h008;
    localparam [11:0] OFF_I_ADDR  = 12'h00C;
    localparam [11:0] OFF_O_ADDR  = 12'h010;
    localparam [11:0] OFF_DIM     = 12'h014;
    localparam [11:0] OFF_POS     = 12'h018;
    localparam [11:0] OFF_IRQ_EN  = 12'h01C;

    // OP encoding
    localparam [3:0] OP_SOFTMAX   = 4'd0;
    localparam [3:0] OP_LAYERNORM = 4'd1;
    localparam [3:0] OP_GELU      = 4'd2;
    localparam [3:0] OP_RELU      = 4'd3;
    localparam [3:0] OP_SILU      = 4'd4;
    localparam [3:0] OP_ROPE      = 4'd5;
    localparam [3:0] OP_RMSNORM   = 4'd6;

    //=========================================================================
    // DUT Signals
    //=========================================================================
    reg                  clk;
    reg                  rst_n;

    // MMIO slave
    reg                  mmio_cs;
    reg                  mmio_we;
    reg  [11:0]          mmio_addr;
    reg  [31:0]          mmio_wdata;
    wire [31:0]          mmio_rdata;
    wire                 mmio_ready;

    // SRAM ports
    wire [ADDR_WIDTH-1:0] sram_raddr;
    wire                  sram_ren;
    wire [ADDR_WIDTH-1:0] sram_waddr;
    wire [31:0]           sram_wdata;
    wire                  sram_wen;
    reg  [31:0]           sram_rdata;

    // Interrupt
    wire                  irq;

    //=========================================================================
    // DUT Instantiation
    //=========================================================================
    sfu_top #(.ADDR_WIDTH(ADDR_WIDTH)) u_dut (
        .clk        (clk),
        .rst_n      (rst_n),
        .mmio_cs    (mmio_cs),
        .mmio_we    (mmio_we),
        .mmio_addr  (mmio_addr),
        .mmio_wdata (mmio_wdata),
        .mmio_rdata (mmio_rdata),
        .mmio_ready (mmio_ready),
        .sram_rdata (sram_rdata),
        .sram_raddr (sram_raddr),
        .sram_ren   (sram_ren),
        .sram_waddr (sram_waddr),
        .sram_wdata (sram_wdata),
        .sram_wen   (sram_wen),
        .irq        (irq)
    );

    //=========================================================================
    // SRAM Model — combinational read, synchronous write
    //=========================================================================
    reg [31:0] sram_mem [0:SRAM_WORDS-1];

    always @(*) begin
        if (sram_ren)
            sram_rdata = sram_mem[sram_raddr[$clog2(SRAM_WORDS)+1:2]];
        else
            sram_rdata = 32'd0;
    end

    always @(posedge clk) begin
        if (sram_wen)
            sram_mem[sram_waddr[$clog2(SRAM_WORDS)+1:2]] <= sram_wdata;
    end

    //=========================================================================
    // Clock and Reset
    //=========================================================================
    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    //=========================================================================
    // MMIO Helpers
    //=========================================================================
    task mmio_write;
        input [11:0] reg_addr;
        input [31:0] value;
    begin
        @(negedge clk);
        mmio_cs   = 1'b1;
        mmio_we   = 1'b1;
        mmio_addr = reg_addr;
        mmio_wdata = value;
        @(negedge clk);
        mmio_cs   = 1'b0;
        mmio_we   = 1'b0;
        mmio_addr = 12'd0;
        mmio_wdata = 32'd0;
    end
    endtask

    task mmio_read;
        input  [11:0] reg_addr;
        output [31:0] value;
    begin
        @(negedge clk);
        mmio_cs   = 1'b1;
        mmio_we   = 1'b0;
        mmio_addr = reg_addr;
        @(negedge clk);
        value = mmio_rdata;
        mmio_cs   = 1'b0;
        mmio_addr = 12'd0;
    end
    endtask

    //=========================================================================
    // Test Scenario State
    //=========================================================================
    reg [1023:0] test_dir;
    reg [1023:0] scenario_name;
    reg [1023:0] result_path;
    reg [4095:0] compare_cmd;
    reg [1023:0] mkdir_cmd;

    reg [31:0]   dim_elems;
    reg [31:0]   head_dim;
    reg [31:0]   pos_val;
    reg [3:0]    op_code;
    reg          is_rope;

    reg [15:0]   elem_mem   [0:MAX_ELEMS-1];   // input elements from input.hex
    reg [15:0]   out_elems  [0:MAX_ELEMS-1];   // unpacked output elements
    reg [31:0]   out_words  [0:MAX_DIM-1];     // captured 32-bit writes

    reg [31:0]   input_words;
    reg [31:0]   output_words;
    reg [31:0]   output_elems;

    integer      out_wcount;
    integer      out_ecount;

    //=========================================================================
    // MAIN TEST SEQUENCE
    //=========================================================================
    integer      fd, code, i, j, status;
    reg [31:0]   stat_val;
    reg [1023:0] line_buf;
    reg [7:0]    ch;

    initial begin
        // ── Initialize MMIO signals ────────────────────────────────────────
        mmio_cs    = 1'b0;
        mmio_we    = 1'b0;
        mmio_addr  = 12'd0;
        mmio_wdata = 32'd0;

        // ── Reset sequence ─────────────────────────────────────────────────
        rst_n = 1'b0;
        repeat(5) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        $display("[TB] Reset released at %0t", $time);

        // ── Step 1: Read plusargs ──────────────────────────────────────────
        if (!$value$plusargs("testdir=%s", test_dir)) begin
            $display("[TB] ERROR: +testdir=<path> plusarg not provided");
            $finish;
        end
        $display("[TB] testdir = %0s", test_dir);

        if (!$value$plusargs("scenario=%s", scenario_name)) begin
            // Fallback to last component of test_dir
            begin
                integer si, ls, sj;
                reg [7:0] db [0:127];
                for (si = 0; si < 128; si = si + 1)
                    db[si] = test_dir[si*8 +: 8];
                ls = 0;
                for (si = 0; si < 128; si = si + 1) begin
                    if (db[si] == 8'h2F) ls = si + 1;  // '/'
                    if (db[si] == 8'h00) break;
                end
                sj = 0;
                for (si = ls; si < 128; si = si + 1) begin
                    if (db[si] == 8'h00) break;
                    scenario_name[sj*8 +: 8] = db[si];
                    sj = sj + 1;
                end
                if (sj == 0) scenario_name = "unknown";
            end
        end
        $display("[TB] scenario = %0s", scenario_name);

        // ── Step 2: Parse params.txt ───────────────────────────────────────
        begin
            reg [1023:0] params_path;
            reg [127:0]  op_token;

            $sformat(params_path, "%0s/params.txt", test_dir);
            fd = $fopen(params_path, "r");
            if (!fd) begin
                $display("[TB] ERROR: Cannot open %0s", params_path);
                $finish;
            end

            dim_elems = 32'd0;
            head_dim  = 32'd0;
            pos_val   = 32'd0;
            op_code   = 4'd0;
            op_token  = 128'd0;
            is_rope   = 1'b0;

            // Fixed-order parser; params.txt is under testbench control.
            code = $fscanf(fd, "OP=%s\n", op_token);
            code = $fscanf(fd, "DIM=%d\n", dim_elems);
            code = $fscanf(fd, "POS=%d\n", pos_val);
            code = $fscanf(fd, "HEAD_DIM=%d\n", head_dim);
            $fclose(fd);
            op_code = op_token_to_code(op_token);
        end

        is_rope = (op_code == OP_ROPE);
        if (head_dim == 32'd0) head_dim = dim_elems;

        $display("[TB] Parsed: OP=%0d (%0s), DIM=%0d, POS=%0d, HEAD_DIM=%0d",
                 op_code, op_token_to_str(op_code), dim_elems, pos_val, head_dim);

        if (dim_elems == 32'd0) begin
            $display("[TB] ERROR: DIM=0 is not valid");
            $finish;
        end

        // Compute word counts
        if (is_rope) begin
            input_words  = dim_elems;          // one pair per word
            output_words = dim_elems;
            output_elems = dim_elems * 2;      // x,y per pair
        end else begin
            input_words  = (dim_elems + 31'd1) >> 1;  // ceil(DIM/2)
            output_words = (dim_elems + 31'd1) >> 1;
            output_elems = dim_elems;
        end

        // ── Step 3: Load input.hex ─────────────────────────────────────────
        begin
            reg [1023:0] input_path;
            integer elem_cnt;
            $sformat(input_path, "%0s/input.hex", test_dir);
            fd = $fopen(input_path, "r");
            if (!fd) begin
                $display("[TB] ERROR: Cannot open %0s", input_path);
                $finish;
            end
            elem_cnt = 0;
            while (!$feof(fd) && elem_cnt < MAX_ELEMS) begin
                reg [15:0] v;
                code = $fscanf(fd, "%h", v);
                if (code == 1) begin
                    elem_mem[elem_cnt] = v;
                    elem_cnt = elem_cnt + 1;
                end else begin
                    // skip blank/comment lines
                    code = $fgets(line_buf, fd);
                    if (code == 0) break;
                end
            end
            $fclose(fd);
            $display("[TB] Loaded %0d elements from %0s", elem_cnt, input_path);

            if (is_rope && elem_cnt < dim_elems * 2) begin
                $display("[TB] ERROR: RoPE needs %0d input elements but found %0d",
                         dim_elems * 2, elem_cnt);
                $finish;
            end else if (!is_rope && elem_cnt < dim_elems) begin
                $display("[TB] ERROR: Expected %0d input elements but found %0d",
                         dim_elems, elem_cnt);
                $finish;
            end
        end

        // Pack input elements into SRAM words
        for (i = 0; i < SRAM_WORDS; i = i + 1)
            sram_mem[i] = 32'd0;

        if (is_rope) begin
            for (i = 0; i < dim_elems; i = i + 1)
                sram_mem[i] = {elem_mem[i*2 + 1], elem_mem[i*2]}; // {y, x}
        end else begin
            for (i = 0; i < dim_elems; i = i + 2) begin
                if (i + 1 < dim_elems)
                    sram_mem[i >> 1] = {elem_mem[i+1], elem_mem[i]};
                else
                    sram_mem[i >> 1] = {16'd0, elem_mem[i]};
            end
        end

        // ── Step 4: Configure MMIO registers ───────────────────────────────
        mmio_write(OFF_CTRL,   {28'd0, op_code});
        mmio_write(OFF_I_ADDR, 32'd0);
        mmio_write(OFF_O_ADDR, 32'd10000); // output at word offset 10000/4 = 2500
        mmio_write(OFF_DIM,    {head_dim[15:0], dim_elems[15:0]});
        if (is_rope)
            mmio_write(OFF_POS, pos_val);
        mmio_write(OFF_IRQ_EN, 32'd1);
        $display("[TB] MMIO configured");

        // ── Step 5: Start operation ────────────────────────────────────────
        out_wcount = 0;
        mmio_write(OFF_CMD, 32'd1);
        $display("[TB] Wrote CMD=START at %0t", $time);

        // ── Step 6: Wait for STATUS.DONE or IRQ ────────────────────────────
        fork : wait_done
            begin
                repeat(1000000) begin
                    mmio_read(OFF_STATUS, stat_val);
                    if (stat_val[1]) begin // DONE bit
                        $display("[TB] STATUS.DONE asserted at %0t", $time);
                        disable wait_done;
                    end
                    @(posedge clk);
                end
                $display("[TB] ERROR: Timeout waiting for STATUS.DONE");
                $finish;
            end
            begin
                @(posedge irq);
                $display("[TB] IRQ asserted at %0t", $time);
                disable wait_done;
            end
        join

        repeat(10) @(posedge clk); // let trailing writes complete

        // ── Step 7: Capture output from sram_wdata port ────────────────────
        // The capture block below samples on posedge clk when sram_wen is high.
        // out_wcount is updated by the always block; read it here.
        $display("[TB] Captured %0d output words (expected %0d)",
                 out_wcount, output_words);

        if (out_wcount == 0) begin
            $display("[TB] ERROR: No output words captured");
            $display("FAIL");
            $finish;
        end

        // Unpack output words into elements
        out_ecount = 0;
        if (is_rope) begin
            for (i = 0; i < out_wcount && i < MAX_DIM; i = i + 1) begin
                out_elems[out_ecount] = out_words[i][15:0];  // x
                out_ecount = out_ecount + 1;
                out_elems[out_ecount] = out_words[i][31:16]; // y
                out_ecount = out_ecount + 1;
            end
        end else begin
            for (i = 0; i < out_wcount && i < MAX_DIM; i = i + 1) begin
                out_elems[out_ecount] = out_words[i][15:0];  // elem0
                out_ecount = out_ecount + 1;
                if (out_ecount < output_elems) begin
                    out_elems[out_ecount] = out_words[i][31:16]; // elem1
                    out_ecount = out_ecount + 1;
                end
            end
        end

        $display("[TB] Unpacked %0d output elements (expected %0d)",
                 out_ecount, output_elems);

        // ── Step 8: Write result file ──────────────────────────────────────
        $sformat(mkdir_cmd, "mkdir -p /home/prj/zhengs/caduceuscore/CaduceusCore/rtl/results");
        status = $system(mkdir_cmd);

        $sformat(result_path, "/home/prj/zhengs/caduceuscore/CaduceusCore/rtl/results/sfu_%0s.hex", scenario_name);
        $display("[TB] Writing result to %0s", result_path);

        fd = $fopen(result_path, "w");
        if (!fd) begin
            $display("[TB] ERROR: Cannot open result file %0s", result_path);
            $display("FAIL");
            $finish;
        end
        for (i = 0; i < output_elems && i < out_ecount; i = i + 1) begin
            $fdisplay(fd, "%04h", out_elems[i]);
        end
        $fclose(fd);

        // ── Step 9: Compare with golden using compare_rtl.py ───────────────
        $sformat(compare_cmd,
            "cd /home/prj/zhengs/caduceuscore/CaduceusCore && PYTHONPATH=sim /NAS/Tools/anaconda3/bin/python3 -c \"import sys; from pathlib import Path; from sim.compare_rtl import compare_test; r=compare_test('%0s', {'default': Path('%0s')})[0]; print('INLINE_COMPARE:', 'PASS' if r.passed else 'FAIL'); sys.exit(0 if r.passed else 1)\"",
            test_dir, result_path);
        $display("[TB] Running inline compare...");
        status = $system(compare_cmd);

        if (status == 0 || ((status >> 8) & 8'hFF) == 0) begin
            $display("[TB] All outputs match golden within tolerance");
            $display("PASS");
        end else begin
            $display("[TB] Comparison failed (status=%0d)", status);
            $display("FAIL");
        end

        $finish;
    end

    //=========================================================================
    // Output capture: sample sram_wdata on every sram_wen cycle
    //=========================================================================
    always @(posedge clk) begin
        if (sram_wen && out_wcount < MAX_DIM) begin
            out_words[out_wcount] <= sram_wdata;
            out_wcount <= out_wcount + 1;
        end
    end

    //=========================================================================
    // OP token helpers
    //=========================================================================
    function automatic [3:0] op_token_to_code;
        input [127:0] token;
        reg [7:0] c0, c1, c2, c3, c4, c5, c6, c7;
        begin
            // $fscanf stores the string byte-reversed in the reg; detect
            // length, reverse only the populated bytes, and right-align.
            begin
                reg [127:0] tok_rev;
                integer     str_len, k;
                str_len = 0;
                for (k = 15; k >= 0; k = k - 1) begin
                    if (token[k*8 +: 8] != 8'h00) begin
                        str_len = k + 1;
                        break;
                    end
                end
                tok_rev = 128'd0;
                for (k = 0; k < str_len; k = k + 1)
                    tok_rev[k*8 +: 8] = token[(str_len-1-k)*8 +: 8];
                c0 = tok_rev[7:0];
                c1 = tok_rev[15:8];
                c2 = tok_rev[23:16];
                c3 = tok_rev[31:24];
                c4 = tok_rev[39:32];
                c5 = tok_rev[47:40];
                c6 = tok_rev[55:48];
                c7 = tok_rev[63:56];
            end

            if (c0 == 8'h73 && c1 == 8'h6F && c2 == 8'h66 && c3 == 8'h74)
                op_token_to_code = OP_SOFTMAX;
            else if (c0 == 8'h6C && c1 == 8'h61 && c2 == 8'h79 && c3 == 8'h65)
                op_token_to_code = OP_LAYERNORM;
            else if (c0 == 8'h67 && c1 == 8'h65 && c2 == 8'h6C && c3 == 8'h75)
                op_token_to_code = OP_GELU;
            else if (c0 == 8'h72 && c1 == 8'h65 && c2 == 8'h6C && c3 == 8'h75)
                op_token_to_code = OP_RELU;
            else if (c0 == 8'h73 && c1 == 8'h69 && c2 == 8'h6C && c3 == 8'h75)
                op_token_to_code = OP_SILU;
            else if (c0 == 8'h72 && c1 == 8'h6F && c2 == 8'h70 && c3 == 8'h65)
                op_token_to_code = OP_ROPE;
            else if (c0 == 8'h72 && c1 == 8'h6D && c2 == 8'h73 && c3 == 8'h6E)
                op_token_to_code = OP_RMSNORM;
            else
                op_token_to_code = 4'd0;
        end
    endfunction

    function automatic [1023:0] op_token_to_str;
        input [3:0] code;
        begin
            case (code)
                OP_SOFTMAX:   op_token_to_str = "softmax";
                OP_LAYERNORM: op_token_to_str = "layernorm";
                OP_GELU:      op_token_to_str = "gelu";
                OP_RELU:      op_token_to_str = "relu";
                OP_SILU:      op_token_to_str = "silu";
                OP_ROPE:      op_token_to_str = "rope";
                OP_RMSNORM:   op_token_to_str = "rmsnorm";
                default:      op_token_to_str = "unknown";
            endcase
        end
    endfunction

endmodule
