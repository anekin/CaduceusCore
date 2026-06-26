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
//   ./simv_tb_sfu +batchfile=/tmp/sfu_batch.txt
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
    string       test_dir;
    string       scenario_name;
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

    reg [31:0]   out_wcount;
    reg [31:0]   out_ecount;

    // Batch-mode state
    reg [1023:0] batchfile_path;
    integer      batch_fd;
    integer      scenarios;
    integer      failures;
    reg          scenario_pass;

    // Shared loop/scenario variables (used by tasks below)
    integer      fd, code, i, j, status;
    reg [31:0]   stat_val;
    reg [1023:0] line_buf;
    reg [7:0]    ch;

    //=========================================================================
    // String helper: derive scenario name from last path component
    //=========================================================================
    function automatic string derive_scenario;
        input string path;
        integer si, last_slash;
        begin
            last_slash = -1;
            for (si = path.len() - 1; si >= 0; si = si - 1) begin
                if (path.getc(si) == "/") begin
                    last_slash = si;
                    break;
                end
            end
            if (last_slash >= 0 && last_slash + 1 < path.len())
                derive_scenario = path.substr(last_slash + 1, path.len() - 1);
            else if (path.len() > 0)
                derive_scenario = path;
            else
                derive_scenario = "unknown";
        end
    endfunction

    //=========================================================================
    // Reset DUT and clear SRAM/state before the next scenario
    //=========================================================================
    task reset_dut_and_state;
    begin
        rst_n = 1'b0;
        out_wcount = 32'd0;
        for (i = 0; i < SRAM_WORDS; i = i + 1)
            sram_mem[i] = 32'd0;
        for (i = 0; i < MAX_DIM; i = i + 1)
            out_words[i] = 32'd0;
        repeat(10) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        $display("[TB] Reset released at %0t", $time);
    end
    endtask

    //=========================================================================
    // MAIN TEST SEQUENCE
    //=========================================================================

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

        // ── Batch mode or single scenario ──────────────────────────────────
        if ($value$plusargs("batchfile=%s", batchfile_path)) begin
            batch_fd = $fopen(batchfile_path, "r");
            if (!batch_fd) begin
                $display("[TB] ERROR: Cannot open batchfile %0s", batchfile_path);
                $display("FAIL");
                $finish;
            end

            scenarios = 0;
            failures  = 0;
            while (!$feof(batch_fd)) begin
                code = $fgets(line_buf, batch_fd);
                if (code == 0) break;
                // Parse first whitespace-delimited token; blank lines are ignored
                code = $sscanf(line_buf, "%s", test_dir);
                if (code != 1) continue;

                scenarios = scenarios + 1;
                scenario_name = derive_scenario(test_dir);
                $display("[BATCH] Running scenario %0s (testdir=%0s)", scenario_name, test_dir);

                if (scenarios > 1)
                    reset_dut_and_state();

                run_one_scenario(scenario_pass);
                if (scenario_pass) begin
                    $display("[BATCH] %0s PASS", scenario_name);
                end else begin
                    $display("[BATCH] %0s FAIL", scenario_name);
                    failures = failures + 1;
                end
            end
            $fclose(batch_fd);

            $display("[BATCH] Summary: %0d passed, %0d failed out of %0d",
                     scenarios - failures, failures, scenarios);
            if (failures == 0) $display("PASS");
            else               $display("FAIL");
            $finish;
        end else begin
            // ── Step 1: Read plusargs ──────────────────────────────────────
            if (!$value$plusargs("testdir=%s", test_dir)) begin
                $display("[TB] ERROR: +testdir=<path> plusarg not provided");
                $display("FAIL");
                $finish;
            end
            $display("[TB] testdir = %0s", test_dir);

            if (!$value$plusargs("scenario=%s", scenario_name))
                scenario_name = derive_scenario(test_dir);
            $display("[TB] scenario = %0s", scenario_name);

            run_one_scenario(scenario_pass);
            if (scenario_pass) $display("PASS");
            else               $display("FAIL");
            $finish;
        end
    end

    //=========================================================================
    // Run one scenario (used by both single and batch modes)
    //=========================================================================
    task run_one_scenario;
        output reg pass;
        reg timed_out;
    begin
        timed_out = 1'b0;

        // ── Step 2: Parse params.txt ───────────────────────────────────────
        begin
            reg [1023:0] params_path;
            reg [127:0]  op_token;

            $sformat(params_path, "%0s/params.txt", test_dir);
            fd = $fopen(params_path, "r");
            if (!fd) begin
                $display("[TB] ERROR: Cannot open %0s", params_path);
                pass = 1'b0;
                return;
            end

            dim_elems = 32'd0;
            head_dim  = 32'd0;
            pos_val   = 32'd0;
            op_code   = 4'd0;
            op_token  = 128'd0;
            is_rope   = 1'b0;

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
            pass = 1'b0;
            return;
        end

        // Compute word counts
        if (is_rope) begin
            input_words  = dim_elems;
            output_words = dim_elems;
            output_elems = dim_elems * 2;
        end else begin
            input_words  = (dim_elems + 31'd1) >> 1;
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
                pass = 1'b0;
                return;
            end
            elem_cnt = 0;
            while (!$feof(fd) && elem_cnt < MAX_ELEMS) begin
                reg [15:0] v;
                code = $fscanf(fd, "%h", v);
                if (code == 1) begin
                    elem_mem[elem_cnt] = v;
                    elem_cnt = elem_cnt + 1;
                end else begin
                    code = $fgets(line_buf, fd);
                    if (code == 0) break;
                end
            end
            $fclose(fd);
            $display("[TB] Loaded %0d elements from %0s", elem_cnt, input_path);

            if (is_rope && elem_cnt < dim_elems * 2) begin
                $display("[TB] ERROR: RoPE needs %0d input elements but found %0d",
                         dim_elems * 2, elem_cnt);
                pass = 1'b0;
                return;
            end else if (!is_rope && elem_cnt < dim_elems) begin
                $display("[TB] ERROR: Expected %0d input elements but found %0d",
                         dim_elems, elem_cnt);
                pass = 1'b0;
                return;
            end
        end

        // Pack input elements into SRAM words
        for (i = 0; i < SRAM_WORDS; i = i + 1)
            sram_mem[i] = 32'd0;

        if (is_rope) begin
            for (i = 0; i < dim_elems; i = i + 1)
                sram_mem[i] = {elem_mem[i*2 + 1], elem_mem[i*2]};
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
        mmio_write(OFF_O_ADDR, 32'd10000);
        mmio_write(OFF_DIM,    {head_dim[15:0], dim_elems[15:0]});
        if (is_rope)
            mmio_write(OFF_POS, pos_val);
        mmio_write(OFF_IRQ_EN, 32'd1);
        $display("[TB] MMIO configured");

        // ── Step 5: Start operation ────────────────────────────────────────
        out_wcount = 32'd0;
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
                timed_out = 1'b1;
                disable wait_done;
            end
            begin
                @(posedge irq);
                $display("[TB] IRQ asserted at %0t", $time);
                disable wait_done;
            end
        join

        if (timed_out) begin
            pass = 1'b0;
            return;
        end

        repeat(10) @(posedge clk);

        // ── Step 7: Capture output from sram_wdata port ────────────────────
        $display("[TB] Captured %0d output words (expected %0d)",
                 out_wcount, output_words);

        if (out_wcount == 0) begin
            $display("[TB] ERROR: No output words captured");
            pass = 1'b0;
            return;
        end

        // Unpack output words into elements
        out_ecount = 0;
        if (is_rope) begin
            for (i = 0; i < out_wcount && i < MAX_DIM; i = i + 1) begin
                out_elems[out_ecount] = out_words[i][15:0];
                out_ecount = out_ecount + 1;
                out_elems[out_ecount] = out_words[i][31:16];
                out_ecount = out_ecount + 1;
            end
        end else begin
            for (i = 0; i < out_wcount && i < MAX_DIM; i = i + 1) begin
                out_elems[out_ecount] = out_words[i][15:0];
                out_ecount = out_ecount + 1;
                if (out_ecount < output_elems) begin
                    out_elems[out_ecount] = out_words[i][31:16];
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
            pass = 1'b0;
            return;
        end
        for (i = 0; i < output_elems && i < out_ecount; i = i + 1) begin
            $fdisplay(fd, "%04h", out_elems[i]);
        end
        $fclose(fd);

        // ── Step 9: Compare with golden using compare_rtl.py helpers ───────
        // Use a slightly looser absolute tolerance (2e-3) because FP16 trig
        // approximations in RoPE can differ by ~1 ULP from the float64 golden.
        $sformat(compare_cmd,
            "cd /home/prj/zhengs/caduceuscore && PYTHONPATH=/home/prj/zhengs/caduceuscore/CaduceusCore /NAS/Tools/anaconda3/bin/python3 CaduceusCore/scripts/compare_sfu.py %0s %0s",
            test_dir, result_path);
        $display("[TB] Running inline compare...");
        status = $system(compare_cmd);

        if (status == 0) begin
            $display("[TB] All outputs match golden within tolerance");
            $display("PASS");
            pass = 1'b1;
        end else begin
            $display("[TB] Comparison failed (status=%0d)", status);
            $display("FAIL");
            pass = 1'b0;
        end
    end
    endtask

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

            c0 = c0 | 8'h20;
            c1 = c1 | 8'h20;
            c2 = c2 | 8'h20;
            c3 = c3 | 8'h20;
            c4 = c4 | 8'h20;
            c5 = c5 | 8'h20;
            c6 = c6 | 8'h20;
            c7 = c7 | 8'h20;

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
