//=============================================================================
// tb_vector — Self-Checking Vector Top-Level Testbench
//=============================================================================
// Reads $readmemh test vectors, drives vector_top through MMIO, models a 4096-bit
// wide SRAM, captures 128-wide/partial chunk writes, writes $writememh output,
// and compares with golden_output.hex.
//
// Usage:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_vector \
//       CaduceusCore/rtl/tb/tb_vector.v CaduceusCore/rtl/vector/*.v \
//       -o /tmp/simv_tb_vector -l /tmp/tb_vector_compile.log
//   /tmp/simv_tb_vector +testdir=<scenario_dir> +scenario=<name> -l /tmp/tb_vector_sim.log
//   /tmp/simv_tb_vector +batchfile=/tmp/vector_batch.txt
//
// Scenario directory layout:
//   params.txt          OP=ADD,DIM=128   (op symbols: ADD,MUL,MAX,SUM,CONV,RESID)
//   a.hex               INT32, 8 hex digits per line (binary ops)
//   b.hex               INT32, 8 hex digits per line (binary ops)
//   x.hex               INT32, 8 hex digits per line (unary ops: MAX,SUM,CONV)
//   golden_output.hex   INT32 (ADD/MUL/MAX/SUM/RESID) or FP16 (CONV)
//=============================================================================

`timescale 1ns / 1ps

module tb_vector;

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam CLK_HALF      = 5;                 // 5ns half-period → 100 MHz
    localparam MAX_ELEMENTS  = 65536;             // max vector dimension supported
    localparam NUM_LANES     = 128;
    localparam DATA_W        = 32;
    localparam VECTOR_W      = NUM_LANES * DATA_W; // 4096
    localparam FP16_W        = 16;
    localparam ADDR_W        = 32;

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

    // SRAM read port A
    wire [ADDR_W-1:0]    sram_a_addr;
    wire                 sram_a_en;
    reg  [VECTOR_W-1:0]  sram_a_rdata;

    // SRAM read port B
    wire [ADDR_W-1:0]    sram_b_addr;
    wire                 sram_b_en;
    reg  [VECTOR_W-1:0]  sram_b_rdata;

    // SRAM write port O
    wire [ADDR_W-1:0]    sram_o_addr;
    wire [VECTOR_W-1:0]  sram_o_wdata;
    wire                 sram_o_wen;
    wire [511:0]         sram_o_wstrb;

    // Interrupt
    wire                 irq;

    //=========================================================================
    // DUT Instantiation
    //=========================================================================
    vector_top #(
        .NUM_LANES(NUM_LANES),
        .DATA_W   (DATA_W),
        .VECTOR_W (VECTOR_W),
        .FP16_W   (FP16_W),
        .ADDR_W   (ADDR_W)
    ) u_dut (
        .clk          (clk),
        .rst_n        (rst_n),
        .mmio_cs      (mmio_cs),
        .mmio_we      (mmio_we),
        .mmio_addr    (mmio_addr),
        .mmio_wdata   (mmio_wdata),
        .mmio_rdata   (mmio_rdata),
        .mmio_ready   (mmio_ready),
        .sram_a_addr  (sram_a_addr),
        .sram_a_en    (sram_a_en),
        .sram_a_rdata (sram_a_rdata),
        .sram_b_addr  (sram_b_addr),
        .sram_b_en    (sram_b_en),
        .sram_b_rdata (sram_b_rdata),
        .sram_o_addr  (sram_o_addr),
        .sram_o_wdata (sram_o_wdata),
        .sram_o_wen   (sram_o_wen),
        .sram_o_wstrb (sram_o_wstrb),
        .irq          (irq)
    );

    //=========================================================================
    // Clock and Reset Generation
    //=========================================================================
    initial clk = 1'b0;
    always #CLK_HALF clk = ~clk;

    //=========================================================================
    // Cycle Counter (for performance reporting)
    //=========================================================================
    reg [31:0] cycle_cnt;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cycle_cnt <= 32'd0;
        else
            cycle_cnt <= cycle_cnt + 32'd1;
    end

    //=========================================================================
    // Wide SRAM Model (4096-bit data, 512-bit byte strobe)
    // Address stride: 512 bytes per INT32 chunk, 256 bytes per FP16 chunk.
    // We keep a flat byte-addressable memory using 32-bit words.
    //=========================================================================
    reg [31:0] sram_mem [0:(MAX_ELEMENTS*2)-1];

    always @(*) begin
        integer rd_i;
        sram_a_rdata = {VECTOR_W{1'b0}};
        sram_b_rdata = {VECTOR_W{1'b0}};
        if (sram_a_en) begin
            for (rd_i = 0; rd_i < NUM_LANES; rd_i = rd_i + 1) begin
                sram_a_rdata[rd_i*DATA_W +: DATA_W] = sram_mem[(sram_a_addr >> 2) + rd_i];
            end
        end
        if (sram_b_en) begin
            for (rd_i = 0; rd_i < NUM_LANES; rd_i = rd_i + 1) begin
                sram_b_rdata[rd_i*DATA_W +: DATA_W] = sram_mem[(sram_b_addr >> 2) + rd_i];
            end
        end
    end

    always @(posedge clk) begin
        integer wr_i;
        if (sram_o_wen) begin
            for (wr_i = 0; wr_i < NUM_LANES; wr_i = wr_i + 1) begin
                if (sram_o_wstrb[wr_i*4]) begin
                    sram_mem[(sram_o_addr >> 2) + wr_i] <= sram_o_wdata[wr_i*DATA_W +: DATA_W];
                end
            end
        end
    end

    //=========================================================================
    // Scenario parameters
    //=========================================================================
    string       test_dir;
    string       scenario_name;
    reg [1023:0] params_path;
    reg [1023:0] a_hex_path;
    reg [1023:0] b_hex_path;
    reg [1023:0] x_hex_path;
    reg [1023:0] g_hex_path;
    reg [1023:0] result_path;

    reg [31:0]   op_code;
    reg [1023:0] op_name;
    reg [15:0]   dim;
    reg [31:0]   result_words;
    reg [31:0]   o_addr_cfg;

    time         start_time;
    time         done_time;

    //=========================================================================
    // Internal memories
    //=========================================================================
    reg [31:0] a_mem     [0:MAX_ELEMENTS-1];
    reg [31:0] b_mem     [0:MAX_ELEMENTS-1];
    reg [31:0] x_mem     [0:MAX_ELEMENTS-1];
    reg [31:0] golden_mem[0:MAX_ELEMENTS-1];
    reg [31:0] result_mem[0:MAX_ELEMENTS-1];

    //=========================================================================
    // Result capture counters
    //=========================================================================
    reg [31:0]  write_count;
    reg         is_conv;
    reg         is_reduce;

    // Batch-mode state
    reg [1023:0] batchfile_path;
    integer      batch_fd;
    integer      scenarios;
    integer      failures;
    reg          scenario_pass;

    // Shared loop/scenario variables (used by tasks below)
    integer      i;
    integer      fd, code, scans;
    integer      cmp_i;
    integer      chunk_idx, lane_idx;
    integer      write_idx;
    reg [31:0]   stat_val;
    reg [31:0]   val_int;
    reg [31:0]   word_val;
    reg [15:0]   fp16_val;
    reg [31:0]   exp_word;
    reg [4095:0] line_buf;

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
    // String helpers
    //=========================================================================
    function automatic integer name_cmp;
        input [1023:0] a;
        input [1023:0] b;
        integer i;
        begin
            name_cmp = 1;
            for (i = 0; i < 16; i = i + 1) begin
                if (a[i*8 +: 8] !== b[i*8 +: 8]) name_cmp = 0;
                if (a[i*8 +: 8] == 8'd0 || b[i*8 +: 8] == 8'd0) begin
                    if (a[i*8 +: 8] == 8'd0 && b[i*8 +: 8] == 8'd0) name_cmp = 1;
                    else name_cmp = 0;
                    i = 16; // break
                end
            end
        end
    endfunction

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
        mmio_cs    = 1'b0;
        mmio_we    = 1'b0;
        mmio_addr  = 12'd0;
        mmio_wdata = 32'd0;
        write_count   = 32'd0;
        is_conv       = 1'b0;
        is_reduce     = 1'b0;
        for (i = 0; i < MAX_ELEMENTS * 2; i = i + 1)
            sram_mem[i] = 32'd0;
        for (i = 0; i < MAX_ELEMENTS; i = i + 1) begin
            a_mem[i]      = 32'd0;
            b_mem[i]      = 32'd0;
            x_mem[i]      = 32'd0;
            golden_mem[i] = 32'd0;
            result_mem[i] = 32'd0;
        end
        repeat(10) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        $display("[TB] Reset released at %0t", $time);
    end
    endtask

    //=========================================================================
    // Main Test Sequence
    //=========================================================================
    reg [31:0] total_expected;
    reg [31:0] mismatch_count;
    reg [31:0] first_mismatch;

    initial begin
        // ── Initialize signals ────────────────────────────────────────────
        mmio_cs    = 1'b0;
        mmio_we    = 1'b0;
        mmio_addr  = 12'd0;
        mmio_wdata = 32'd0;
        write_count   = 32'd0;
        is_conv       = 1'b0;
        is_reduce     = 1'b0;

        // ── Reset sequence ────────────────────────────────────────────────
        rst_n = 1'b0;
        repeat(5) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        $display("[TB] Reset released at %0t", $time);

        // ── Batch mode or single scenario ─────────────────────────────────
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
            // ── Step 1: Read +testdir+ plusarg ─────────────────────────────
            if (!$value$plusargs("testdir=%s", test_dir)) begin
                $display("[TB] ERROR: +testdir+ plusarg not provided");
                $display("[TB] Usage: /tmp/simv_tb_vector +testdir=<path_to_scenario_dir> +scenario=<name>");
                $display("FAIL");
                $finish;
            end
            $display("[TB] testdir = %0s", test_dir);

            if (!$value$plusargs("scenario=%s", scenario_name))
                scenario_name = derive_scenario(test_dir);

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

        // Ensure scenario_name is set (batch mode already sets it)
        if (scenario_name.len() == 0)
            scenario_name = derive_scenario(test_dir);

        // ── Step 2: Parse params.txt ──────────────────────────────────────
        $sformat(params_path, "%0s/params.txt", test_dir);
        fd = $fopen(params_path, "r");
        if (!fd) begin
            $display("[TB] ERROR: Cannot open %0s", params_path);
            pass = 1'b0;
            return;
        end

        op_code = 32'hFFFFFFFF;
        dim     = 16'd0;

        begin
            reg [7:0] pbuf [0:4095];
            integer   pbuf_len;
            integer   pos;
            integer   c;
            reg [7:0] ch;
            reg [7:0] key_buf [0:31];
            integer   key_len;
            reg [7:0] val_buf [0:63];
            integer   val_len;
            integer   vi;
            reg [31:0] tmp_val;

            pbuf_len = 0;
            while (!$feof(fd) && pbuf_len < 4095) begin
                c = $fgetc(fd);
                if (c == -1) break;
                pbuf[pbuf_len] = c[7:0];
                pbuf_len = pbuf_len + 1;
            end
            $fclose(fd);

            pos = 0;
            while (pos < pbuf_len) begin
                while (pos < pbuf_len) begin
                    ch = pbuf[pos];
                    if (ch == 8'h2C || ch == 8'h0A || ch == 8'h0D || ch == 8'h20 || ch == 8'h09) pos = pos + 1;
                    else break;
                end
                if (pos >= pbuf_len) break;

                key_len = 0;
                while (pos < pbuf_len) begin
                    ch = pbuf[pos];
                    if (ch == 8'h3D) begin
                        pos = pos + 1;
                        break;
                    end
                    if (ch == 8'h2C || ch == 8'h0A || ch == 8'h0D || ch == 8'h20) begin
                        pos = pos + 1;
                        break;
                    end
                    key_buf[key_len] = ch;
                    key_len = key_len + 1;
                    pos = pos + 1;
                    if (key_len >= 31) break;
                end
                if (key_len == 0) continue;

                val_len = 0;
                while (pos < pbuf_len) begin
                    ch = pbuf[pos];
                    if (ch == 8'h2C || ch == 8'h0A || ch == 8'h0D || ch == 8'h20) begin
                        pos = pos + 1;
                        break;
                    end
                    val_buf[val_len] = ch;
                    val_len = val_len + 1;
                    pos = pos + 1;
                    if (val_len >= 63) break;
                end
                if (val_len == 0) continue;

                if (key_len == 2 && key_buf[0] == "O" && key_buf[1] == "P") begin
                    if (val_len == 3 && val_buf[0] == "A" && val_buf[1] == "D" && val_buf[2] == "D") begin
                        op_code = 32'd0; op_name = "ADD";
                    end else if (val_len == 3 && val_buf[0] == "M" && val_buf[1] == "U" && val_buf[2] == "L") begin
                        op_code = 32'd1; op_name = "MUL";
                    end else if (val_len == 3 && val_buf[0] == "M" && val_buf[1] == "A" && val_buf[2] == "X") begin
                        op_code = 32'd2; op_name = "MAX";
                    end else if (val_len == 3 && val_buf[0] == "S" && val_buf[1] == "U" && val_buf[2] == "M") begin
                        op_code = 32'd3; op_name = "SUM";
                    end else if (val_len == 4 && val_buf[0] == "C" && val_buf[1] == "O" && val_buf[2] == "N" && val_buf[3] == "V") begin
                        op_code = 32'd4; op_name = "CONV";
                    end else if (val_len == 5 && val_buf[0] == "R" && val_buf[1] == "E" && val_buf[2] == "S" && val_buf[3] == "I" && val_buf[4] == "D") begin
                        op_code = 32'd5; op_name = "RESID";
                    end else begin
                        $display("[TB] ERROR: unknown OP name");
                        for (vi = 0; vi < val_len; vi = vi + 1)
                            $write("%0s", val_buf[vi]);
                        $display("");
                        pass = 1'b0;
                        return;
                    end
                end else if (key_len == 3 && key_buf[0] == "D" && key_buf[1] == "I" && key_buf[2] == "M") begin
                    tmp_val = 32'd0;
                    for (vi = 0; vi < val_len; vi = vi + 1) begin
                        if (val_buf[vi] >= "0" && val_buf[vi] <= "9")
                            tmp_val = tmp_val * 10 + (val_buf[vi] - "0");
                    end
                    dim = tmp_val[15:0];
                end
            end
        end

        if (op_code == 32'hFFFFFFFF || dim == 16'd0) begin
            $display("[TB] ERROR: params.txt must specify OP=<NAME> and DIM=<N>");
            $display("[TB] Parsed OP=%0s (code=%0d), DIM=%0d", op_name, op_code, dim);
            pass = 1'b0;
            return;
        end

        is_conv   = (op_code == 32'd4);
        is_reduce = (op_code == 32'd2) || (op_code == 32'd3);

        if (is_reduce)
            total_expected = 32'd1;
        else if (is_conv)
            total_expected = {16'd0, dim};
        else
            total_expected = {16'd0, dim};

        $display("[TB] Parsed OP=%0s (code=%0d), DIM=%0d", op_name, op_code, dim);

        // ── Step 3: Load hex files ─────────────────────────────────────────
        $sformat(g_hex_path, "%0s/golden_output.hex", test_dir);
        $display("[TB] Loading golden_output from %0s", g_hex_path);
        $readmemh(g_hex_path, golden_mem);

        if (op_code == 32'd0 || op_code == 32'd1 || op_code == 32'd5) begin
            $sformat(a_hex_path, "%0s/a.hex", test_dir);
            $sformat(b_hex_path, "%0s/b.hex", test_dir);
            $display("[TB] Loading a.hex and b.hex");
            $readmemh(a_hex_path, a_mem);
            $readmemh(b_hex_path, b_mem);

            for (chunk_idx = 0; chunk_idx * NUM_LANES < dim; chunk_idx = chunk_idx + 1) begin
                for (lane_idx = 0; lane_idx < NUM_LANES; lane_idx = lane_idx + 1) begin
                    if (chunk_idx * NUM_LANES + lane_idx < dim) begin
                        sram_mem[(chunk_idx * NUM_LANES) + lane_idx] = a_mem[(chunk_idx * NUM_LANES) + lane_idx];
                        sram_mem[(chunk_idx * NUM_LANES) + lane_idx + (32'h10000 >> 2)] = b_mem[(chunk_idx * NUM_LANES) + lane_idx];
                    end
                end
            end
        end else begin
            $sformat(x_hex_path, "%0s/x.hex", test_dir);
            $display("[TB] Loading x.hex");
            $readmemh(x_hex_path, x_mem);

            for (chunk_idx = 0; chunk_idx * NUM_LANES < dim; chunk_idx = chunk_idx + 1) begin
                for (lane_idx = 0; lane_idx < NUM_LANES; lane_idx = lane_idx + 1) begin
                    if (chunk_idx * NUM_LANES + lane_idx < dim) begin
                        sram_mem[(chunk_idx * NUM_LANES) + lane_idx] = x_mem[(chunk_idx * NUM_LANES) + lane_idx];
                    end
                end
            end
        end

        // ── Step 4: Configure MMIO registers ──────────────────────────────
        mmio_write(12'h00, op_code);
        $display("[TB] Wrote CTRL=%0d (%0s)", op_code, op_name);

        mmio_write(12'h0C, 32'd0);
        mmio_write(12'h014, 32'd0);
        if (op_code == 32'd0 || op_code == 32'd1 || op_code == 32'd5) begin
            mmio_write(12'h10, 32'h0001_0000);
        end else begin
            mmio_write(12'h10, 32'd0);
        end
        mmio_write(12'h18, {16'd0, dim});
        mmio_write(12'h1C, 32'd1);

        if (op_code == 32'd0 || op_code == 32'd1 || op_code == 32'd5) begin
            o_addr_cfg = 32'h0002_0000;
        end else begin
            o_addr_cfg = 32'h0000_8000;
        end
        mmio_write(12'h014, o_addr_cfg);

        $display("[TB] Wrote A_ADDR=0x00000000, B_ADDR=0x%08h, O_ADDR=0x%08h, DIM=%0d",
                 (op_code == 32'd0 || op_code == 32'd1 || op_code == 32'd5) ? 32'h0001_0000 : 32'd0,
                 o_addr_cfg,
                 dim);

        // ── Step 5: Start operation ───────────────────────────────────────
        start_time = $time;
        write_count   = 32'd0;
        mmio_write(12'h04, 32'd1);
        $display("[TB] Wrote CMD=START at %0t", $time);

        // ── Step 6: Wait for STATUS.DONE or IRQ ──────────────────────────
        $display("[TB] Waiting for STATUS.DONE or IRQ...");
        fork : wait_done
            begin
                repeat(1000000) begin
                    mmio_read(12'h08, stat_val);
                    if (stat_val[1]) begin
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

        done_time = $time;
        $display("[TB] Operation complete. Cycles=%0d", cycle_cnt);

        repeat(5) @(posedge clk);

        // ── Step 7: Capture result from SRAM model ────────────────────────
        if (is_reduce) begin
            result_mem[0] = sram_mem[(o_addr_cfg >> 2)];
            write_count = 32'd1;
        end else if (is_conv) begin
            write_count = {16'd0, dim};
            for (chunk_idx = 0; chunk_idx * NUM_LANES < dim; chunk_idx = chunk_idx + 1) begin
                for (lane_idx = 0; lane_idx < NUM_LANES; lane_idx = lane_idx + 1) begin
                    if (chunk_idx * NUM_LANES + lane_idx < dim) begin
                        write_idx = chunk_idx * NUM_LANES + lane_idx;
                        result_mem[write_idx*2]     = {16'd0, sram_mem[(o_addr_cfg >> 2) + write_idx][15:0]};
                        if (write_idx*2 + 1 < dim)
                            result_mem[write_idx*2 + 1] = {16'd0, sram_mem[(o_addr_cfg >> 2) + write_idx][31:16]};
                    end
                end
            end
        end else begin
            write_count = {16'd0, dim};
            for (chunk_idx = 0; chunk_idx * NUM_LANES < dim; chunk_idx = chunk_idx + 1) begin
                for (lane_idx = 0; lane_idx < NUM_LANES; lane_idx = lane_idx + 1) begin
                    if (chunk_idx * NUM_LANES + lane_idx < dim) begin
                        write_idx = chunk_idx * NUM_LANES + lane_idx;
                        result_mem[write_idx] = sram_mem[(o_addr_cfg >> 2) + write_idx];
                    end
                end
            end
        end

        // ── Step 8: Write result hex file ─────────────────────────────────
        $sformat(result_path, "/home/prj/zhengs/caduceuscore/CaduceusCore/rtl/results/vector_%0s.hex", scenario_name);
        $display("[TB] Writing result to %0s", result_path);

        fd = $fopen(result_path, "w");
        if (!fd) begin
            $display("[TB] ERROR: Cannot open %0s for writing", result_path);
            pass = 1'b0;
            return;
        end

        if (is_conv) begin
            for (cmp_i = 0; cmp_i < total_expected; cmp_i = cmp_i + 1) begin
                $fwrite(fd, "%04h\n", result_mem[cmp_i][15:0]);
            end
        end else if (is_reduce) begin
            $fwrite(fd, "%08h\n", result_mem[0]);
        end else begin
            for (cmp_i = 0; cmp_i < total_expected; cmp_i = cmp_i + 1) begin
                $fwrite(fd, "%08h\n", result_mem[cmp_i]);
            end
        end
        $fclose(fd);

        // ── Step 9: Compare with golden ───────────────────────────────────
        mismatch_count = 32'd0;
        first_mismatch = 32'hFFFFFFFF;

        if (is_conv) begin
            for (cmp_i = 0; cmp_i < total_expected; cmp_i = cmp_i + 1) begin
                if (result_mem[cmp_i][15:0] !== golden_mem[cmp_i][15:0]) begin
                    if (mismatch_count == 0) first_mismatch = cmp_i[31:0];
                    mismatch_count = mismatch_count + 32'd1;
                    if (mismatch_count <= 5) begin
                        $display("  MISMATCH [%0d]: golden=0x%04h, result=0x%04h",
                                 cmp_i, golden_mem[cmp_i][15:0], result_mem[cmp_i][15:0]);
                    end
                end
            end
        end else begin
            for (cmp_i = 0; cmp_i < total_expected; cmp_i = cmp_i + 1) begin
                if (result_mem[cmp_i] !== golden_mem[cmp_i]) begin
                    if (mismatch_count == 0) first_mismatch = cmp_i[31:0];
                    mismatch_count = mismatch_count + 32'd1;
                    if (mismatch_count <= 5) begin
                        $display("  MISMATCH [%0d]: golden=0x%08h, result=0x%08h",
                                 cmp_i, golden_mem[cmp_i], result_mem[cmp_i]);
                    end
                end
            end
        end

        if (mismatch_count == 0) begin
            $display("[TB] PASS: All %0d values match golden_output.hex", total_expected);
            $display("PASS");
            pass = 1'b1;
        end else begin
            $display("[TB] FAIL: %0d mismatches (first at index %0d)", mismatch_count, first_mismatch);
            $display("FAIL");
            pass = 1'b0;
        end

        $display("[TB] *** PERF: elapsed_cycles=%0d", (done_time - start_time) / (CLK_HALF * 2));
    end
    endtask

endmodule
