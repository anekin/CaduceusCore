//=============================================================================
// tb_vector_perf — Vector Module-Level Performance Testbench
//=============================================================================
// Wraps vector_top.v with:
//   - Per-FSM-state cycle counters (13 states + TOTAL)
//   - Block/chunk iteration counter
//   - Anti-vacuous assertions (sram_a_en, sram_o_wen, status_done, status_busy)
//   - PERF event logging (standardized PERF|case=X|...|cycles=N format)
//   - Back-to-back CMD loop via +repeat= plusarg
//   - +op= and +dim= plusargs (no test vectors needed)
//
// Vector FSM states (from vector_top.v):
//   ST_IDLE=0, ST_READ=1, ST_BIN_EXEC=2, ST_BIN_WRITE=3,
//   ST_REDUCE_FEED=4, ST_REDUCE_WAIT=5, ST_REDUCE_ACC=6,
//   ST_REDUCE_WRITE=7, ST_CONV_FEED=8, ST_CONV_CAPTURE=9,
//   ST_CONV_WRITE=10, ST_DONE=11, ST_LATCH=12
//
// Usage:
//   vcs -full64 -sverilog -debug_access+all -timescale=1ns/1ps -top tb_vector_perf \
//       rtl/tb/tb_vector_perf.v rtl/vector/*.v -o build/simv_tb_vector_perf
//   ./simv_tb_vector_perf +case=SFV-P08 +op=add +dim=128 [+repeat=1]
//=============================================================================

`timescale 1ns / 1ps

module tb_vector_perf;

    //=========================================================================
    // Parameters
    //=========================================================================
    localparam CLK_HALF      = 5;
    localparam MAX_ELEMENTS  = 65536;
    localparam NUM_LANES     = 128;
    localparam DATA_W        = 32;
    localparam VECTOR_W      = NUM_LANES * DATA_W;  // 4096
    localparam FP16_W        = 16;
    localparam ADDR_W        = 32;

    // Vector OP encoding (from vector_top.v)
    localparam [3:0] OP_ADD      = 4'd0;
    localparam [3:0] OP_MUL      = 4'd1;
    localparam [3:0] OP_MAX      = 4'd2;
    localparam [3:0] OP_SUM      = 4'd3;
    localparam [3:0] OP_CONV     = 4'd4;
    localparam [3:0] OP_RESID    = 4'd5;
    localparam [3:0] OP_F16_I32  = 4'd6;

    // FSM state encoding (from vector_top.v)
    localparam ST_IDLE         = 4'd0;
    localparam ST_READ         = 4'd1;
    localparam ST_BIN_EXEC     = 4'd2;
    localparam ST_BIN_WRITE    = 4'd3;
    localparam ST_REDUCE_FEED  = 4'd4;
    localparam ST_REDUCE_WAIT  = 4'd5;
    localparam ST_REDUCE_ACC   = 4'd6;
    localparam ST_REDUCE_WRITE = 4'd7;
    localparam ST_CONV_FEED    = 4'd8;
    localparam ST_CONV_CAPTURE = 4'd9;
    localparam ST_CONV_WRITE   = 4'd10;
    localparam ST_DONE            = 4'd11;
    localparam ST_LATCH           = 4'd12;
    localparam ST_F16_I32_FEED    = 4'd13;
    localparam ST_F16_I32_CAPT    = 4'd14;
    localparam ST_F16_I32_WRITE   = 4'd15;

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
    // Wide SRAM Model (4096-bit data, 512-bit byte strobe)
    //=========================================================================
    reg [31:0] sram_mem [0:(MAX_ELEMENTS*2)-1];

    always @(*) begin
        integer rd_i;
        sram_a_rdata = {VECTOR_W{1'b0}};
        sram_b_rdata = {VECTOR_W{1'b0}};
        if (sram_a_en) begin
            for (rd_i = 0; rd_i < NUM_LANES; rd_i = rd_i + 1)
                sram_a_rdata[rd_i*DATA_W +: DATA_W] = sram_mem[(sram_a_addr >> 2) + rd_i];
        end
        if (sram_b_en) begin
            for (rd_i = 0; rd_i < NUM_LANES; rd_i = rd_i + 1)
                sram_b_rdata[rd_i*DATA_W +: DATA_W] = sram_mem[(sram_b_addr >> 2) + rd_i];
        end
    end

    always @(posedge clk) begin
        integer wr_i;
        if (sram_o_wen) begin
            for (wr_i = 0; wr_i < NUM_LANES; wr_i = wr_i + 1) begin
                if (sram_o_wstrb[wr_i*4])
                    sram_mem[(sram_o_addr >> 2) + wr_i] <= sram_o_wdata[wr_i*DATA_W +: DATA_W];
            end
        end
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

    // Per-state accumulation registers (13 states)
    reg [31:0]  cnt_IDLE;
    reg [31:0]  cnt_READ;
    reg [31:0]  cnt_BIN_EXEC;
    reg [31:0]  cnt_BIN_WRITE;
    reg [31:0]  cnt_REDUCE_FEED;
    reg [31:0]  cnt_REDUCE_WAIT;
    reg [31:0]  cnt_REDUCE_ACC;
    reg [31:0]  cnt_REDUCE_WRITE;
    reg [31:0]  cnt_CONV_FEED;
    reg [31:0]  cnt_CONV_CAPTURE;
    reg [31:0]  cnt_CONV_WRITE;
    reg [31:0]  cnt_F16_I32_FEED;
    reg [31:0]  cnt_F16_I32_CAPT;
    reg [31:0]  cnt_F16_I32_WRITE;
    reg [31:0]  cnt_DONE;
    reg [31:0]  cnt_LATCH;

    // TOTAL counter
    reg [31:0]  cnt_TOTAL;

    // Block/chunk loop counter
    reg [31:0]  cnt_chunks_processed;
    reg         chunk_start_trig;

    // Anti-vacuous check registers
    reg [31:0]  cnt_sram_a_en_toggles;
    reg [31:0]  cnt_sram_o_wen_toggles;
    reg [31:0]  cnt_done_pulses;
    reg         sram_a_en_prev;
    reg         sram_o_wen_prev;
    reg         status_busy_av;
    reg         busy_rose_in_2;
    reg [1:0]   busy_check_cnt;

    // TOTAL counting trigger — start on CMD.START write, stop at DONE
    reg         perf_counting;

    always @(posedge clk or negedge rst_n) begin : blk_perf_counters
        if (!rst_n) begin
            perf_rst              <= 1'b0;
            perf_cycle            <= 32'd0;
            state_prev            <= ST_IDLE;
            cnt_IDLE              <= 32'd0;
            cnt_READ              <= 32'd0;
            cnt_BIN_EXEC          <= 32'd0;
            cnt_BIN_WRITE         <= 32'd0;
            cnt_REDUCE_FEED       <= 32'd0;
            cnt_REDUCE_WAIT       <= 32'd0;
            cnt_REDUCE_ACC        <= 32'd0;
            cnt_REDUCE_WRITE      <= 32'd0;
            cnt_CONV_FEED         <= 32'd0;
            cnt_CONV_CAPTURE      <= 32'd0;
            cnt_CONV_WRITE        <= 32'd0;
            cnt_F16_I32_FEED      <= 32'd0;
            cnt_F16_I32_CAPT      <= 32'd0;
            cnt_F16_I32_WRITE     <= 32'd0;
            cnt_DONE              <= 32'd0;
            cnt_LATCH             <= 32'd0;
            cnt_TOTAL             <= 32'd0;
            cnt_chunks_processed  <= 32'd0;
            chunk_start_trig      <= 1'b0;
            cnt_sram_a_en_toggles <= 32'd0;
            cnt_sram_o_wen_toggles <= 32'd0;
            cnt_done_pulses       <= 32'd0;
            sram_a_en_prev        <= 1'b0;
            sram_o_wen_prev       <= 1'b0;
            status_busy_av        <= 1'b0;
            busy_rose_in_2        <= 1'b0;
            busy_check_cnt        <= 2'd0;
            perf_counting         <= 1'b0;
        end else begin
            // ── Increment local cycle counter ────────────────────────────
            perf_cycle <= perf_cycle + 32'd1;

            // ── Perf reset pulse ─────────────────────────────────────────
            if (perf_rst) begin
                perf_rst              <= 1'b0;
                cnt_IDLE              <= 32'd0;
                cnt_READ              <= 32'd0;
                cnt_BIN_EXEC          <= 32'd0;
                cnt_BIN_WRITE         <= 32'd0;
                cnt_REDUCE_FEED       <= 32'd0;
                cnt_REDUCE_WAIT       <= 32'd0;
                cnt_REDUCE_ACC        <= 32'd0;
                cnt_REDUCE_WRITE      <= 32'd0;
                cnt_CONV_FEED         <= 32'd0;
                cnt_CONV_CAPTURE      <= 32'd0;
                cnt_CONV_WRITE        <= 32'd0;
                cnt_F16_I32_FEED      <= 32'd0;
                cnt_F16_I32_CAPT      <= 32'd0;
                cnt_F16_I32_WRITE     <= 32'd0;
                cnt_DONE              <= 32'd0;
                cnt_LATCH             <= 32'd0;
                cnt_TOTAL             <= 32'd0;
                cnt_chunks_processed  <= 32'd0;
                chunk_start_trig      <= 1'b0;
                cnt_sram_a_en_toggles <= 32'd0;
                cnt_sram_o_wen_toggles <= 32'd0;
                cnt_done_pulses       <= 32'd0;
                sram_a_en_prev        <= 1'b0;
                sram_o_wen_prev       <= 1'b0;
                status_busy_av        <= 1'b0;
                busy_rose_in_2        <= 1'b0;
                busy_check_cnt        <= 2'd0;
                perf_counting         <= 1'b0;
            end

            // ── CMD.START detection ─────────────────────────────────────
            // When MMIO writes CMD=START (addr=0x04, data[0]=1), begin
            // counting TOTAL cycles.  This captures the ~2-cycle overhead
            // between CMD.START and the FSM leaving IDLE.
            if (mmio_cs && mmio_we && mmio_addr == 12'h04 && mmio_wdata[0])
                perf_counting <= 1'b1;

            // ── SRAM toggle counters ────────────────────────────────────
            sram_a_en_prev <= sram_a_en;
            sram_o_wen_prev <= sram_o_wen;
            if (!sram_a_en_prev && sram_a_en)
                cnt_sram_a_en_toggles <= cnt_sram_a_en_toggles + 32'd1;
            if (!sram_o_wen_prev && sram_o_wen)
                cnt_sram_o_wen_toggles <= cnt_sram_o_wen_toggles + 32'd1;

            // ── Chunk/block counter ─────────────────────────────────────
            // Count when entering a chunk-processing state from READ
            if (fsm_state == ST_BIN_EXEC && state_prev == ST_READ)
                cnt_chunks_processed <= cnt_chunks_processed + 32'd1;
            else if (fsm_state == ST_REDUCE_FEED && state_prev == ST_READ)
                cnt_chunks_processed <= cnt_chunks_processed + 32'd1;
            else if (fsm_state == ST_CONV_FEED && state_prev == ST_READ)
                cnt_chunks_processed <= cnt_chunks_processed + 32'd1;
            else if (fsm_state == ST_F16_I32_FEED && state_prev == ST_READ)
                cnt_chunks_processed <= cnt_chunks_processed + 32'd1;

            // ── Status BUSY ────────────────────────────────────────────
            status_busy_av <= (fsm_state != ST_IDLE && fsm_state != ST_DONE);

            // ── State tracking ──────────────────────────────────────────
            if (fsm_state != state_prev) begin
                if (fsm_state == ST_DONE && state_prev != ST_DONE) begin
                    cnt_done_pulses <= cnt_done_pulses + 32'd1;
                    perf_counting <= 1'b0;  // Stop TOTAL counting at DONE
                end

                if (state_prev == ST_IDLE && fsm_state != ST_IDLE)
                    busy_check_cnt <= 2'd0;

                state_prev <= fsm_state;
            end

            if (status_busy_av && !busy_rose_in_2 && busy_check_cnt <= 2'd2)
                busy_rose_in_2 <= 1'b1;

            if (fsm_state != ST_IDLE && busy_check_cnt <= 2'd2)
                busy_check_cnt <= busy_check_cnt + 2'd1;

            // ── Per-state cycle accumulation ────────────────────────────
            if (perf_counting && fsm_state != ST_DONE) begin
                cnt_TOTAL <= cnt_TOTAL + 32'd1;

                case (fsm_state)
                    ST_READ:         cnt_READ         <= cnt_READ         + 32'd1;
                    ST_BIN_EXEC:     cnt_BIN_EXEC     <= cnt_BIN_EXEC     + 32'd1;
                    ST_BIN_WRITE:    cnt_BIN_WRITE    <= cnt_BIN_WRITE    + 32'd1;
                    ST_REDUCE_FEED:  cnt_REDUCE_FEED  <= cnt_REDUCE_FEED  + 32'd1;
                    ST_REDUCE_WAIT:  cnt_REDUCE_WAIT  <= cnt_REDUCE_WAIT  + 32'd1;
                    ST_REDUCE_ACC:   cnt_REDUCE_ACC   <= cnt_REDUCE_ACC   + 32'd1;
                    ST_REDUCE_WRITE: cnt_REDUCE_WRITE <= cnt_REDUCE_WRITE + 32'd1;
                    ST_CONV_FEED:    cnt_CONV_FEED    <= cnt_CONV_FEED    + 32'd1;
                    ST_CONV_CAPTURE: cnt_CONV_CAPTURE <= cnt_CONV_CAPTURE + 32'd1;
                    ST_CONV_WRITE:   cnt_CONV_WRITE   <= cnt_CONV_WRITE   + 32'd1;
                    ST_F16_I32_FEED: cnt_F16_I32_FEED <= cnt_F16_I32_FEED + 32'd1;
                    ST_F16_I32_CAPT: cnt_F16_I32_CAPT <= cnt_F16_I32_CAPT + 32'd1;
                    ST_F16_I32_WRITE:cnt_F16_I32_WRITE<= cnt_F16_I32_WRITE+ 32'd1;
                    ST_LATCH:        cnt_LATCH        <= cnt_LATCH        + 32'd1;
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
    reg [31:0]   repeat_count;
    reg [255:0]  shape_str;

    reg          is_binary;
    reg          is_reduce;
    reg          is_conv;
    integer      i;

    //=========================================================================
    // OP token helpers
    //=========================================================================
    function automatic [3:0] op_token_to_code;
        input [127:0] token;
        reg [31:0] lower3, lower4;
        reg [63:0] lower5;
        reg [55:0] lower7;
        begin
            // $value$plusargs stores the string bytes at LSB:
            // "mul"  → token[23:16]='m', [15:8]='u', [7:0]='l'
            // "conv" → token[31:24]='c', [23:16]='o', [15:8]='n', [7:0]='v'
            // "resid"→ token[39:32]='r', [31:24]='e', etc.
            // "f16_i32"→ token[55:0] = "f16_i32", token[63:56]=0
            // Case-insensitive comparison: OR 0x20 with each byte.

            lower3 = token[23:0] | 24'h20_20_20;
            lower4 = token[31:0] | 32'h20_20_20_20;
            lower5 = token[39:0] | 40'h20_20_20_20_20;
            lower7 = token[55:0] | 56'h20_20_20_20_20_20_20;

            // 3-char ops: byte 3 (token[31:24]) must be zero
            if (token[31:24] == 8'h00) begin
                case (lower3)
                    24'h61_64_64: op_token_to_code = OP_ADD;   // "add"
                    24'h6D_75_6C: op_token_to_code = OP_MUL;   // "mul"
                    24'h6D_61_78: op_token_to_code = OP_MAX;   // "max"
                    24'h73_75_6D: op_token_to_code = OP_SUM;   // "sum"
                    default:      op_token_to_code = OP_ADD;
                endcase
            end
            // 4-char ops: byte 4 (token[39:32]) must be zero
            else if (token[39:32] == 8'h00) begin
                if (lower4 == 32'h63_6F_6E_76)        // "conv"
                    op_token_to_code = OP_CONV;
                else
                    op_token_to_code = OP_ADD;
            end
            // 5-char ops
            else if (token[47:40] == 8'h00) begin
                if (lower5 == 40'h72_65_73_69_64)     // "resid"
                    op_token_to_code = OP_RESID;
                else
                    op_token_to_code = OP_ADD;
            end
            // 7-char op: f16_i32
            else if (token[63:56] == 8'h00) begin
                if (lower7 == 56'h66_31_36_7F_69_33_32) // "f16_i32" (underscore|0x20=0x7f)
                    op_token_to_code = OP_F16_I32;
                else
                    op_token_to_code = OP_ADD;
            end
            else begin
                op_token_to_code = OP_ADD;
            end
        end
    endfunction

    function automatic [1023:0] op_code_to_str;
        input [3:0] code;
        begin
            case (code)
                OP_ADD:   op_code_to_str = "add";
                OP_MUL:   op_code_to_str = "mul";
                OP_MAX:   op_code_to_str = "max";
                OP_SUM:   op_code_to_str = "sum";
                OP_CONV:   op_code_to_str = "conv";
                OP_RESID:  op_code_to_str = "resid";
                OP_F16_I32:op_code_to_str = "f16_i32";
                default:   op_code_to_str = "unknown";
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
    // Perf Counter Reset
    //=========================================================================
    task reset_perf_counters;
    begin
        perf_rst = 1'b1;
        @(negedge clk);
        @(negedge clk);
    end
    endtask

    //=========================================================================
    // Emit all PERF lines for a completed CMD
    //=========================================================================
    task emit_all_perf;
    begin
        emit_perf("READ",         cnt_READ);
        emit_perf("LATCH",        cnt_LATCH);
        emit_perf("BIN_EXEC",     cnt_BIN_EXEC);
        emit_perf("BIN_WRITE",    cnt_BIN_WRITE);
        emit_perf("REDUCE_FEED",  cnt_REDUCE_FEED);
        emit_perf("REDUCE_WAIT",  cnt_REDUCE_WAIT);
        emit_perf("REDUCE_ACC",   cnt_REDUCE_ACC);
        emit_perf("REDUCE_WRITE", cnt_REDUCE_WRITE);
        emit_perf("CONV_FEED",    cnt_CONV_FEED);
        emit_perf("CONV_CAPTURE", cnt_CONV_CAPTURE);
        emit_perf("CONV_WRITE",   cnt_CONV_WRITE);
        emit_perf("F16_I32_FEED", cnt_F16_I32_FEED);
        emit_perf("F16_I32_CAPT", cnt_F16_I32_CAPT);
        emit_perf("F16_I32_WRITE",cnt_F16_I32_WRITE);
        emit_perf("TOTAL",        cnt_TOTAL);
        emit_perf("CHUNKS",       cnt_chunks_processed);
    end
    endtask

    //=========================================================================
    // Check anti-vacuous assertions
    //=========================================================================
    task check_anti_vacuous;
        input [31:0] op_num;
        reg [31:0] fail_cnt;
        reg [31:0] expected_a_en;
        reg [31:0] expected_o_wen;
    begin
        fail_cnt = 0;

        // Compute expected chunk count
        expected_a_en = (dim_val + NUM_LANES - 1) / NUM_LANES + 1;
        if (is_binary)
            expected_a_en = expected_a_en * 2;  // A and B ports
        if (is_conv)
            expected_o_wen = (dim_val + NUM_LANES - 1) / NUM_LANES + 1;
        else if (is_reduce)
            expected_o_wen = 1;
        else
            expected_o_wen = (dim_val + NUM_LANES - 1) / NUM_LANES + 1;

        // 1. sram_a_en must toggle enough times
        if (cnt_sram_a_en_toggles < expected_a_en / 2 && dim_val > 0) begin
            $display("[PERF] FAIL (op %0d): sram_a_en toggle count %0d too low (expected >= %0d)",
                     op_num, cnt_sram_a_en_toggles, expected_a_en / 2);
            fail_cnt = fail_cnt + 1;
        end

        // 2. sram_o_wen must toggle (except for reduce which writes once)
        if (cnt_sram_o_wen_toggles < expected_o_wen / 2 && dim_val > 0 && !is_reduce) begin
            $display("[PERF] FAIL (op %0d): sram_o_wen toggle count %0d too low (expected >= %0d)",
                     op_num, cnt_sram_o_wen_toggles, expected_o_wen / 2);
            fail_cnt = fail_cnt + 1;
        end

        // 3. status_done must pulse exactly once per CMD
        if (cnt_done_pulses != 1) begin
            $display("[PERF] FAIL (op %0d): status_done pulses = %0d (expected 1)",
                     op_num, cnt_done_pulses);
            fail_cnt = fail_cnt + 1;
        end

        // 4. status_busy must rise within 2 cycles
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
    // Initialize SRAM with synthetic test data
    //=========================================================================
    task init_sram_data;
        integer ei;
    begin
        for (ei = 0; ei < MAX_ELEMENTS * 2; ei = ei + 1)
            sram_mem[ei] = $random;

        // Keep values in reasonable INT32 range for non-overflow ops
        for (ei = 0; ei < dim_val; ei = ei + 1) begin
            // Clamp to [-2^20, 2^20] range for safe arithmetic
            if (sram_mem[ei][31:20] != 12'd0 && !sram_mem[ei][31])
                sram_mem[ei] = {12'd0, sram_mem[ei][19:0]};
            else if (sram_mem[ei][31])
                sram_mem[ei] = {12'hFFF, sram_mem[ei][19:0]};
        end
    end
    endtask

    //=========================================================================
    // Drive one CMD operation
    //=========================================================================
    reg [31:0] stat_val;
    reg timed_out;

    task drive_one_cmd;
        input [31:0] op_idx;
        reg [31:0] stat_val_local;
        reg timed_out_local;
    begin
        timed_out_local = 1'b0;

        // Reset perf counters
        reset_perf_counters;

        // Write CMD=START
        mmio_write(12'h04, 32'd1);
        $display("[TB] CMD=START at cycle %0d", cycle_cnt);

        // Wait for STATUS.DONE or IRQ
        fork : wait_done
            begin
                repeat(1000000) begin
                    mmio_read(12'h08, stat_val_local);
                    if (stat_val_local[1]) begin
                        $display("[TB] STATUS.DONE at cycle %0d", cycle_cnt);
                        disable wait_done;
                    end
                    @(posedge clk);
                end
                $display("[TB] ERROR: Timeout waiting for STATUS.DONE");
                timed_out_local = 1'b1;
                disable wait_done;
            end
            begin
                @(posedge irq);
                $display("[TB] IRQ asserted at cycle %0d", cycle_cnt);
                disable wait_done;
            end
        join

        if (timed_out_local) begin
            $display("[PERF] case=%0s FAIL (timeout)", case_id);
            timed_out = 1'b1;
            return;
        end

        repeat(3) @(posedge clk);

        // ── Emit PERF lines ─────────────────────────────────────────────
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
        timed_out  = 1'b0;

        // ── Parse plusargs ─────────────────────────────────────────────────
        if (!$value$plusargs("case=%s", case_id)) begin
            $sformat(case_id, "%0s", "SFV-P00");
        end
        $display("[TB] case_id = %0s", case_id);

        if (!$value$plusargs("op=%s", op_token)) begin
            $display("[TB] ERROR: +op= plusarg not provided");
            $display("[TB] Usage: +op=add|mul|max|sum|conv|resid|f16_i32 [+dim=N]");
            $display("FAIL");
            $finish;
        end
        op_code = op_token_to_code(op_token);
        op_str  = op_code_to_str(op_code);
        $display("[TB] op = %0s (code=%0d)", op_str, op_code);

        dim_val = 16'd128;
        $value$plusargs("dim=%d", dim_val);
        $display("[TB] dim = %0d", dim_val);

        repeat_count = 32'd1;
        $value$plusargs("repeat=%d", repeat_count);

        is_binary  = (op_code == OP_ADD || op_code == OP_MUL || op_code == OP_RESID);
        is_reduce  = (op_code == OP_MAX || op_code == OP_SUM);
        is_conv    = (op_code == OP_CONV);

        // ── Set shape string for PERF ──────────────────────────────────────
        $sformat(shape_str, "op=%0s,dim=%0d", op_str, dim_val);

        // ── Reset sequence ─────────────────────────────────────────────────
        rst_n = 1'b0;
        repeat(5) @(posedge clk);
        rst_n = 1'b1;
        @(posedge clk);
        $display("[TB] Reset released at %0t", $time);

        // ── Configure MMIO registers (once) ────────────────────────────────
        mmio_write(12'h00, op_code);
        $display("[TB] Wrote CTRL=%0d (%0s)", op_code, op_str);

        mmio_write(12'h0C, 32'd0);
        if (is_binary)
            mmio_write(12'h10, 32'h0001_0000);
        else
            mmio_write(12'h10, 32'd0);
        mmio_write(12'h18, {16'd0, dim_val});
        mmio_write(12'h1C, 32'd1);

        if (is_binary)
            mmio_write(12'h14, 32'h0002_0000);
        else
            mmio_write(12'h14, 32'h0000_8000);

        $display("[TB] MMIO configured");

        // ── Back-to-back CMD loop ──────────────────────────────────────────
        begin : loop_btb_block
            reg [31:0] op_index;
            for (op_index = 0; op_index < repeat_count; op_index = op_index + 1) begin : loop_btb
                $display("[TB] === CMD loop iteration %0d / %0d ===", op_index, repeat_count);

                init_sram_data;

                drive_one_cmd(op_index);

                if (timed_out) begin
                    $display("FAIL");
                    $finish;
                end
            end
        end

        $display("[TB] All %0d CMD operations complete.", repeat_count);
        $display("PASS");
        $finish;
    end

endmodule
