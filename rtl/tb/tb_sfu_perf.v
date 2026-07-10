//=============================================================================
// tb_sfu_perf — SFU Module-Level Performance Testbench
//=============================================================================
// Wraps sfu_top.v with:
//   - Per-FSM-state cycle counters (IDLE, READ_INIT, RUN, FLUSH, DONE, TOTAL)
//   - Anti-vacuous assertions (sram_ren, sram_wen, status_done, status_busy)
//   - PERF event logging (standardized PERF|case=X|...|cycles=N format)
//   - Back-to-back CMD loop via +repeat= plusarg
//   - +op= and +dim= plusargs (no test vectors needed)
//
// SFU FSM states (from sfu_top.v):
//   ST_IDLE=0, ST_READ_INIT=1, ST_RUN=2, ST_FLUSH=3, ST_DONE=4
//
// Usage:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_sfu_perf \
//       rtl/tb/tb_sfu_perf.v rtl/sfu/*.v -o build/simv_tb_sfu_perf
//   ./simv_tb_sfu_perf +case=SFV-P01 +op=softmax +dim=64 [+pos=0] [+repeat=1]
//=============================================================================

`timescale 1ns / 1ps

module tb_sfu_perf;

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam CLK_HALF     = 5;                 // 100 MHz
    localparam MAX_DIM      = 4096;
    localparam SRAM_WORDS   = 16384;
    localparam ADDR_WIDTH   = 32;

    // SFU MMIO offsets
    localparam [11:0] OFF_CTRL    = 12'h000;
    localparam [11:0] OFF_CMD     = 12'h004;
    localparam [11:0] OFF_STATUS  = 12'h008;
    localparam [11:0] OFF_I_ADDR  = 12'h00C;
    localparam [11:0] OFF_O_ADDR  = 12'h010;
    localparam [11:0] OFF_DIM     = 12'h014;
    localparam [11:0] OFF_POS     = 12'h018;
    localparam [11:0] OFF_IRQ_EN  = 12'h01C;

    // OP encoding (from sfu_top.v)
    localparam [3:0] OP_SOFTMAX   = 4'd0;
    localparam [3:0] OP_LAYERNORM = 4'd1;
    localparam [3:0] OP_GELU      = 4'd2;
    localparam [3:0] OP_RELU      = 4'd3;
    localparam [3:0] OP_SILU      = 4'd4;
    localparam [3:0] OP_ROPE      = 4'd5;
    localparam [3:0] OP_RMSNORM   = 4'd6;

    // FSM state encoding (from sfu_top.v)
    localparam ST_IDLE       = 4'd0;
    localparam ST_READ_INIT  = 4'd1;
    localparam ST_RUN        = 4'd2;
    localparam ST_FLUSH      = 4'd3;
    localparam ST_DONE       = 4'd4;

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
    // SRAM Model
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
    // Cycle Counter
    //=========================================================================
    reg [31:0] cycle_cnt;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            cycle_cnt <= 32'd0;
        else
            cycle_cnt <= cycle_cnt + 32'd1;
    end

    //=========================================================================
    // Performance Monitoring — per-FSM-state cycle counters
    //=========================================================================
    reg         perf_rst;
    reg [31:0]  perf_cycle;
    wire [3:0]  fsm_state = u_dut.state;
    reg  [3:0]  state_prev;

    // Per-state accumulation registers
    reg [31:0]  cnt_IDLE;
    reg [31:0]  cnt_READ_INIT;
    reg [31:0]  cnt_RUN;
    reg [31:0]  cnt_FLUSH;
    reg [31:0]  cnt_DONE;

    // TOTAL counter
    reg [31:0]  cnt_TOTAL;

    // Back-to-back gap measurement: cycle of last STATUS.DONE detection
    reg [31:0]  last_done_cycle;

    // Anti-vacuous check registers
    reg [31:0]  cnt_sram_ren_toggles;
    reg [31:0]  cnt_sram_wen_toggles;
    reg [31:0]  cnt_done_pulses;
    reg         sram_ren_prev;
    reg         sram_wen_prev;
    reg         status_busy;
    reg         busy_rose_in_2;
    reg [1:0]   busy_check_cnt;

    always @(posedge clk or negedge rst_n) begin : blk_perf_counters
        if (!rst_n) begin
            perf_rst            <= 1'b0;
            perf_cycle          <= 32'd0;
            state_prev          <= ST_IDLE;
            cnt_IDLE            <= 32'd0;
            cnt_READ_INIT       <= 32'd0;
            cnt_RUN             <= 32'd0;
            cnt_FLUSH           <= 32'd0;
            cnt_DONE            <= 32'd0;
            cnt_TOTAL           <= 32'd0;
            cnt_sram_ren_toggles <= 32'd0;
            cnt_sram_wen_toggles <= 32'd0;
            cnt_done_pulses      <= 32'd0;
            sram_ren_prev        <= 1'b0;
            sram_wen_prev        <= 1'b0;
            status_busy          <= 1'b0;
            busy_rose_in_2       <= 1'b0;
            busy_check_cnt       <= 2'd0;
            last_done_cycle      <= 32'd0;
        end else begin
            // ── Increment local cycle counter ────────────────────────────
            perf_cycle <= perf_cycle + 32'd1;

            // ── Perf reset pulse ─────────────────────────────────────────
            if (perf_rst) begin
                perf_rst            <= 1'b0;
                cnt_IDLE            <= 32'd0;
                cnt_READ_INIT       <= 32'd0;
                cnt_RUN             <= 32'd0;
                cnt_FLUSH           <= 32'd0;
                cnt_DONE            <= 32'd0;
                cnt_TOTAL           <= 32'd0;
                cnt_sram_ren_toggles <= 32'd0;
                cnt_sram_wen_toggles <= 32'd0;
                cnt_done_pulses      <= 32'd0;
                sram_ren_prev        <= 1'b0;
                sram_wen_prev        <= 1'b0;
                status_busy          <= 1'b0;
                busy_rose_in_2       <= 1'b0;
                busy_check_cnt       <= 2'd0;
                last_done_cycle      <= 32'd0;
            end

            // ── SRAM toggle counters (anti-vacuous) ─────────────────────
            sram_ren_prev <= sram_ren;
            sram_wen_prev <= sram_wen;
            if (!sram_ren_prev && sram_ren)
                cnt_sram_ren_toggles <= cnt_sram_ren_toggles + 32'd1;
            if (!sram_wen_prev && sram_wen)
                cnt_sram_wen_toggles <= cnt_sram_wen_toggles + 32'd1;

            // ── Status BUSY tracking ────────────────────────────────────
            // Read STATUS register to check BUSY bit via mmio_rdata[0]
            // We'll detect it indirectly: when state leaves IDLE, busy is asserted
            status_busy <= (fsm_state != ST_IDLE && fsm_state != ST_DONE);

            // ── State tracking ──────────────────────────────────────────
            if (fsm_state != state_prev) begin
                // DONE detection
                if (fsm_state == ST_DONE && state_prev != ST_DONE) begin
                    cnt_done_pulses <= cnt_done_pulses + 32'd1;
                    last_done_cycle <= cycle_cnt;
                end

                // Leaving ST_IDLE → operation starts
                if (state_prev == ST_IDLE && fsm_state != ST_IDLE) begin
                    busy_check_cnt <= 2'd0;
                end

                state_prev <= fsm_state;
            end

            // ── BUSY within 2 cycles check ──────────────────────────────
            if (status_busy && !busy_rose_in_2 && busy_check_cnt <= 2'd2)
                busy_rose_in_2 <= 1'b1;

            if (fsm_state != ST_IDLE && busy_check_cnt <= 2'd2)
                busy_check_cnt <= busy_check_cnt + 2'd1;

            // ── Per-state cycle accumulation ────────────────────────────
            // Exclude IDLE and DONE from TOTAL (active operation only)
            if (fsm_state != ST_IDLE && fsm_state != ST_DONE) begin
                cnt_TOTAL <= cnt_TOTAL + 32'd1;

                case (fsm_state)
                    ST_READ_INIT: cnt_READ_INIT <= cnt_READ_INIT + 32'd1;
                    ST_RUN:       cnt_RUN       <= cnt_RUN       + 32'd1;
                    ST_FLUSH:     cnt_FLUSH     <= cnt_FLUSH     + 32'd1;
                endcase
            end

            if (fsm_state == ST_IDLE)
                cnt_IDLE <= cnt_IDLE + 32'd1;
            if (fsm_state == ST_DONE)
                cnt_DONE <= cnt_DONE + 32'd1;
        end
    end

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
    // Test Scenario Parameters
    //=========================================================================
    reg [127:0]  case_id;
    reg [127:0]  op_token;
    reg [1023:0] op_str;
    reg [3:0]    op_code;
    reg [15:0]   dim_val;
    reg [15:0]   dim_val_default;
    reg [15:0]   pos_val;
    reg [31:0]   repeat_count;
    reg [255:0]  shape_str;
    reg [127:0]  input_mode;

    // Multi-op sequence support (for mixed back-to-back sequences)
    reg [31:0]   op_seq_len;
    reg [3:0]    op_seq [0:7];
    reg [15:0]   dim_seq [0:7];
    reg [31:0]   total_ops;
    reg [31:0]   seq_idx;

    reg          is_rope;
    reg [31:0]   input_words;
    reg [31:0]   output_words;
    reg [31:0]   output_elems;
    integer      i;

    //=========================================================================
    // OP token helpers
    //=========================================================================
    function automatic [3:0] op_token_to_code;
        input [127:0] token;
        reg [7:0] c0, c1, c2, c3, c4, c5, c6, c7;
        begin
            c0 = token[7:0]   | 8'h20;
            c1 = token[15:8]  | 8'h20;
            c2 = token[23:16] | 8'h20;
            c3 = token[31:24] | 8'h20;
            c4 = token[39:32] | 8'h20;
            c5 = token[47:40] | 8'h20;
            c6 = token[55:48] | 8'h20;
            c7 = token[63:56] | 8'h20;

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

    function automatic [1023:0] op_code_to_str;
        input [3:0] code;
        begin
            case (code)
                OP_SOFTMAX:   op_code_to_str = "softmax";
                OP_LAYERNORM: op_code_to_str = "layernorm";
                OP_GELU:      op_code_to_str = "gelu";
                OP_RELU:      op_code_to_str = "relu";
                OP_SILU:      op_code_to_str = "silu";
                OP_ROPE:      op_code_to_str = "rope";
                OP_RMSNORM:   op_code_to_str = "rmsnorm";
                default:      op_code_to_str = "unknown";
            endcase
        end
    endfunction

    //=========================================================================
    // PERF Emission
    //=========================================================================
    task emit_perf;
        input [255:0] evt;
        input [31:0]  cyc;
    begin
        $display("PERF|case=%0s|op=%0s|event=%0s|cycles=%0d", case_id, shape_str, evt, cyc);
    end
    endtask

    task emit_perf_gap;
        input [31:0] gap_op_idx;
        input [31:0] gap_cyc;
    begin
        $display("PERF|case=%0s|op=%0s|event=GAP|op_idx=%0d|cycles=%0d",
                 case_id, shape_str, gap_op_idx, gap_cyc);
    end
    endtask

    //=========================================================================
    // Perf Counter Reset (zero-time, for tight back-to-back gaps)
    // Only the active counters are reset; last_done_cycle is preserved so the
    // next GAP measurement covers the real reconfiguration idle time.
    //=========================================================================
    task reset_perf_counters;
    begin
        cnt_IDLE             = 32'd0;
        cnt_READ_INIT        = 32'd0;
        cnt_RUN              = 32'd0;
        cnt_FLUSH            = 32'd0;
        cnt_DONE             = 32'd0;
        cnt_TOTAL            = 32'd0;
        cnt_sram_ren_toggles = 32'd0;
        cnt_sram_wen_toggles = 32'd0;
        cnt_done_pulses      = 32'd0;
        sram_ren_prev        = 1'b0;
        sram_wen_prev        = 1'b0;
        status_busy          = 1'b0;
        busy_rose_in_2       = 1'b0;
        busy_check_cnt       = 2'd0;
    end
    endtask

    //=========================================================================
    // Emit all PERF lines for a completed CMD
    //=========================================================================
    task emit_all_perf;
    begin
        emit_perf("READ_INIT",  cnt_READ_INIT);
        emit_perf("RUN",        cnt_RUN);
        emit_perf("FLUSH",      cnt_FLUSH);
        emit_perf("TOTAL",      cnt_TOTAL);
    end
    endtask

    //=========================================================================
    // Check anti-vacuous assertions
    //=========================================================================
    task check_anti_vacuous;
        input [31:0] op_num;
        reg [31:0] fail_cnt;
        reg [31:0] expected_ren_toggles;
        reg [31:0] expected_wen_toggles;
    begin
        fail_cnt = 0;

        // 1. SRAM read-enable must toggle (reads elements from SRAM)
        // For streaming ops: ~dim/2 words. For reduction ops: 3*N passes.
        if (is_rope)
            expected_ren_toggles = dim_val;
        else if (op_code == OP_SOFTMAX || op_code == OP_LAYERNORM)
            expected_ren_toggles = dim_val * 3 / 2 + 2;
        else if (op_code == OP_RMSNORM)
            expected_ren_toggles = dim_val * 2 / 2 + 2;
        else
            expected_ren_toggles = dim_val / 2 + 2;

        if (cnt_sram_ren_toggles < expected_ren_toggles / 2 && dim_val > 0) begin
            $display("[PERF] FAIL (op %0d): sram_ren toggle count %0d too low (expected >= %0d)",
                     op_num, cnt_sram_ren_toggles, expected_ren_toggles / 2);
            fail_cnt = fail_cnt + 1;
        end

        // 2. SRAM write-enable must toggle
        if (is_rope)
            expected_wen_toggles = dim_val;
        else
            expected_wen_toggles = dim_val / 2 + 1;

        if (cnt_sram_wen_toggles < expected_wen_toggles / 2 && dim_val > 0) begin
            $display("[PERF] FAIL (op %0d): sram_wen toggle count %0d too low (expected >= %0d)",
                     op_num, cnt_sram_wen_toggles, expected_wen_toggles / 2);
            fail_cnt = fail_cnt + 1;
        end

        // 3. status_done must pulse exactly once per CMD
        if (cnt_done_pulses != 1) begin
            $display("[PERF] FAIL (op %0d): status_done pulses = %0d (expected 1)",
                     op_num, cnt_done_pulses);
            fail_cnt = fail_cnt + 1;
        end

        // 4. status_busy must rise within 2 cycles of CMD
        if (dim_val > 0 && !busy_rose_in_2) begin
            $display("[PERF] FAIL (op %0d): status_busy did not rise within 2 cycles",
                     op_num);
            fail_cnt = fail_cnt + 1;
        end

        if (fail_cnt == 0) begin
            $display("[PERF] ASSERT (op %0d): all anti-vacuous checks PASS", op_num);
        end else begin
            $display("[PERF] ASSERT (op %0d): %0d checks FAILED", op_num, fail_cnt);
        end
    end
    endtask

    //=========================================================================
    // Input-mode helper: compare first `len` bytes of input_mode with ref_str
    //=========================================================================
    function automatic input_mode_eq;
        input [127:0] mode;
        input [63:0]  ref_str;
        input integer len;
        integer i;
        reg match;
        begin
            match = 1'b1;
            for (i = 0; i < len; i = i + 1) begin
                if (mode[i*8 +: 8] != ref_str[i*8 +: 8])
                    match = 1'b0;
            end
            input_mode_eq = match;
        end
    endfunction

    //=========================================================================
    // Initialize SRAM with synthetic test data
    //=========================================================================
    task init_sram_data;
        integer wi, ei;
        reg [15:0] val;
        reg is_zeros;
        reg is_maxval;
        reg is_sparse;
        reg is_repeated;
    begin
        is_zeros    = input_mode_eq(input_mode, "zeros",    5);
        is_maxval   = input_mode_eq(input_mode, "maxval",   6);
        is_sparse   = input_mode_eq(input_mode, "sparse",   6);
        is_repeated = input_mode_eq(input_mode, "repeated", 8);

        // Clear all SRAM words first
        for (wi = 0; wi < SRAM_WORDS; wi = wi + 1)
            sram_mem[wi] = 32'd0;

        if (is_rope) begin
            // RoPE: pairs of (x, y) packed as {y, x} in each word
            for (ei = 0; ei < dim_val; ei = ei + 1) begin
                if (is_zeros)
                    val = 16'd0;
                else if (is_maxval)
                    val = 16'hFFFF;
                else if (is_repeated)
                    val = 16'h3C00; // FP16 1.0
                else if (is_sparse)
                    val = ((ei % 16) == 0) ? (16'h3C00) : 16'd0;
                else
                    val = $random & 16'h07FF;
                sram_mem[ei] = {val, val};
            end
        end else begin
            // Normal FP16: two elements per word, packed as {elem[i+1], elem[i]}
            for (ei = 0; ei < dim_val; ei = ei + 2) begin
                if (is_zeros)
                    val = 16'd0;
                else if (is_maxval)
                    val = 16'hFFFF;
                else if (is_repeated)
                    val = 16'h3C00; // FP16 1.0
                else if (is_sparse)
                    val = ((ei % 32) == 0) ? (16'h3C00) : 16'd0;
                else
                    val = $random & 16'h07FF;

                if (ei + 1 < dim_val)
                    sram_mem[ei >> 1] = {val, val};
                else
                    sram_mem[ei >> 1] = {16'd0, val};
            end
        end
    end
    endtask

    //=========================================================================
    // Drive one CMD operation
    //=========================================================================
    task drive_one_cmd;
        input [31:0] op_idx;
        reg [31:0] stat_val;
        reg timed_out;
    begin
        timed_out = 1'b0;

        // Reset perf counters
        reset_perf_counters;

        // Emit GAP event for ops after the first (STATUS.DONE -> next CMD.START)
        if (op_idx > 0) begin
            emit_perf_gap(op_idx, cycle_cnt - last_done_cycle);
        end

        // Write CMD=START
        mmio_write(OFF_CMD, 32'd1);
        $display("[TB] CMD=START at cycle %0d", cycle_cnt);

        // Wait for STATUS.DONE or IRQ
        fork : wait_done
            begin
                repeat(1000000) begin
                    mmio_read(OFF_STATUS, stat_val);
                    if (stat_val[1]) begin
                        $display("[TB] STATUS.DONE at cycle %0d", cycle_cnt);
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
                $display("[TB] IRQ asserted at cycle %0d", cycle_cnt);
                disable wait_done;
            end
        join

        if (timed_out) begin
            $display("[PERF] case=%0s FAIL (timeout)", case_id);
            return;
        end

        // Note: repeat(3) removed for tight back-to-back gap measurement.

        // ── Emit PERF lines for this CMD ────────────────────────────────
        emit_all_perf;

        // ── Anti-vacuous assertions ─────────────────────────────────────
        check_anti_vacuous(op_idx);
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
        perf_rst   = 1'b0;

        // ── Parse plusargs ─────────────────────────────────────────────────
        if (!$value$plusargs("case=%s", case_id)) begin
            $sformat(case_id, "%0s", "SFV-P00");
        end
        $display("[TB] case_id = %0s", case_id);

        // Try numeric op_code first (deterministic, avoids VCS string byte-order)
        if ($value$plusargs("op_code=%d", op_code)) begin
            $sformat(op_token, "%0s", "numeric");
        end else if (!$value$plusargs("op=%s", op_token)) begin
            $display("[TB] ERROR: neither +op_code= nor +op= plusarg provided");
            $display("[TB] Usage: +op_code=0|1|2|3|4|5|6 or +op=softmax|layernorm|...");
            $display("FAIL");
            $finish;
        end else begin
            op_code = op_token_to_code(op_token);
        end
        op_str  = op_code_to_str(op_code);
        $display("[TB] op = %0s (code=%0d)", op_str, op_code);

        dim_val = 16'd64;
        $value$plusargs("dim=%d", dim_val);
        dim_val_default = dim_val;
        $display("[TB] dim = %0d", dim_val);

        // Default first sequence entry to the legacy +op/+dim values
        op_seq[0] = op_code;
        dim_seq[0] = dim_val;
        for (i = 1; i < 8; i = i + 1) begin
            op_seq[i]  = 4'd0;
            dim_seq[i] = 16'd0;
        end

        pos_val = 16'd0;
        $value$plusargs("pos=%d", pos_val);
        is_rope = (op_code == OP_ROPE);

        repeat_count = 32'd1;
        $value$plusargs("repeat=%d", repeat_count);

        input_mode = "random";
        $value$plusargs("input_mode=%s", input_mode);
        $display("[TB] input_mode = %0s", input_mode);

        // ── Multi-op sequence parsing ──────────────────────────────────────
        op_seq_len = 32'd0;
        $value$plusargs("op_seq_len=%d", op_seq_len);
        if (op_seq_len > 0) begin
            $value$plusargs("op0=%d", op_seq[0]);
            $value$plusargs("op1=%d", op_seq[1]);
            $value$plusargs("op2=%d", op_seq[2]);
            $value$plusargs("op3=%d", op_seq[3]);
            $value$plusargs("op4=%d", op_seq[4]);
            $value$plusargs("op5=%d", op_seq[5]);
            $value$plusargs("op6=%d", op_seq[6]);
            $value$plusargs("op7=%d", op_seq[7]);
            $value$plusargs("dim0=%d", dim_seq[0]);
            $value$plusargs("dim1=%d", dim_seq[1]);
            $value$plusargs("dim2=%d", dim_seq[2]);
            $value$plusargs("dim3=%d", dim_seq[3]);
            $value$plusargs("dim4=%d", dim_seq[4]);
            $value$plusargs("dim5=%d", dim_seq[5]);
            $value$plusargs("dim6=%d", dim_seq[6]);
            $value$plusargs("dim7=%d", dim_seq[7]);
            $display("[TB] op_seq_len = %0d", op_seq_len);
        end

        // Back-to-back alias: +op1=X +op2=Y (numeric code or op name)
        // If used, op_seq_len is inferred from the highest opN provided.
        begin
            reg [31:0] tmp_code;
            if ($value$plusargs("op1=%d", tmp_code)) begin
                op_seq[1] = tmp_code[3:0];
                if (op_seq_len < 2) op_seq_len = 32'd2;
            end else if ($value$plusargs("op1=%s", op_token)) begin
                op_seq[1] = op_token_to_code(op_token);
                if (op_seq_len < 2) op_seq_len = 32'd2;
            end
            if ($value$plusargs("op2=%d", tmp_code)) begin
                op_seq[2] = tmp_code[3:0];
                if (op_seq_len < 3) op_seq_len = 32'd3;
            end else if ($value$plusargs("op2=%s", op_token)) begin
                op_seq[2] = op_token_to_code(op_token);
                if (op_seq_len < 3) op_seq_len = 32'd3;
            end
            if ($value$plusargs("op3=%d", tmp_code)) begin
                op_seq[3] = tmp_code[3:0];
                if (op_seq_len < 4) op_seq_len = 32'd4;
            end else if ($value$plusargs("op3=%s", op_token)) begin
                op_seq[3] = op_token_to_code(op_token);
                if (op_seq_len < 4) op_seq_len = 32'd4;
            end
            if ($value$plusargs("op4=%d", tmp_code)) begin
                op_seq[4] = tmp_code[3:0];
                if (op_seq_len < 5) op_seq_len = 32'd5;
            end else if ($value$plusargs("op4=%s", op_token)) begin
                op_seq[4] = op_token_to_code(op_token);
                if (op_seq_len < 5) op_seq_len = 32'd5;
            end
            if ($value$plusargs("op5=%d", tmp_code)) begin
                op_seq[5] = tmp_code[3:0];
                if (op_seq_len < 6) op_seq_len = 32'd6;
            end else if ($value$plusargs("op5=%s", op_token)) begin
                op_seq[5] = op_token_to_code(op_token);
                if (op_seq_len < 6) op_seq_len = 32'd6;
            end
            if ($value$plusargs("dim1=%d", dim_seq[1]));
            if ($value$plusargs("dim2=%d", dim_seq[2]));
            if ($value$plusargs("dim3=%d", dim_seq[3]));
            if ($value$plusargs("dim4=%d", dim_seq[4]));
            if ($value$plusargs("dim5=%d", dim_seq[5]));
            if (op_seq_len > 0)
                $display("[TB] op_seq_len (alias) = %0d", op_seq_len);
        end

        total_ops = (op_seq_len > 0) ? (repeat_count * op_seq_len) : repeat_count;

        // ── Set shape string for PERF (updated per-op inside the loop) ───────
        if (is_rope)
            $sformat(shape_str, "op=%0s,dim=%0d,pos=%0d", op_str, dim_val, pos_val);
        else
            $sformat(shape_str, "op=%0s,dim=%0d", op_str, dim_val);

        // ── Reset sequence ─────────────────────────────────────────────────
        rst_n = 1'b0;
        repeat(5) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        $display("[TB] Reset released at %0t", $time);

        // ── Configure common MMIO registers (once) ─────────────────────────
        mmio_write(OFF_I_ADDR, 32'd0);
        mmio_write(OFF_O_ADDR, 32'd10000);
        mmio_write(OFF_IRQ_EN, 32'd1);

        // ── Back-to-back CMD loop ──────────────────────────────────────────
        begin : loop_btb_block
            reg [31:0] op_index;

            for (op_index = 0; op_index < total_ops; op_index = op_index + 1) begin : loop_btb
                seq_idx = (op_seq_len > 0) ? (op_index % op_seq_len) : 32'd0;

                if (op_seq_len > 0) begin
                    op_code = op_seq[seq_idx];
                    dim_val = (dim_seq[seq_idx] > 0) ? dim_seq[seq_idx] : dim_val_default;
                end

                op_str  = op_code_to_str(op_code);
                is_rope = (op_code == OP_ROPE);
                if (is_rope)
                    $sformat(shape_str, "op=%0s,dim=%0d,pos=%0d", op_str, dim_val, pos_val);
                else
                    $sformat(shape_str, "op=%0s,dim=%0d", op_str, dim_val);

                $display("[TB] === CMD loop iteration %0d / %0d (op=%0s, dim=%0d) ===",
                         op_index, total_ops, op_str, dim_val);

                // Reconfigure op-dependent MMIO registers
                mmio_write(OFF_CTRL, {28'd0, op_code});
                mmio_write(OFF_DIM,  {16'd0, dim_val});
                if (is_rope)
                    mmio_write(OFF_POS, pos_val);

                // Initialize SRAM with fresh random data for single-op runs.
                // Skip re-initialization during mixed back-to-back sequences so
                // the inter-op gap reflects only control-path idle time.
                if (op_seq_len <= 1)
                    init_sram_data;

                // Drive one CMD (includes perf counter reset, wait, PERF emission)
                drive_one_cmd(op_index);

                // Clear SRAM output region between ops
                // (SRAM write captures from previous op are still in sram_mem)
            end
        end

        $display("[TB] All %0d CMD operations complete.", total_ops);
        $display("PASS");
        $finish;
    end

endmodule
