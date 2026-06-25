`timescale 1ns / 1ps
//=============================================================================
// controller — MXU tile-iteration finite state machine
//=============================================================================
// Manages N/M/K tile iteration for the MXU. Reads dimensions from the mmio_if
// register outputs and sequences the MXU through matrix multiplication tiles.
//
// FSM states:
//   IDLE       — Wait for cmd_start pulse from mmio_if.
//   READ_DIMS  — Capture M, K, N from mmio_if.  Compute tile counts.
//   LOAD_W     — Assert weight_load_en for one cycle.
//   LOAD_A     — Assert activation_load_en for one cycle.
//   COMPUTE    — Strobe compute_en for k_cur+2 cycles (flush pipeline).
//   STORE_OUT  — Sequence row address to read 64×64 result from mac_array.
//   DONE       — Assert status_done + irq for one cycle, return to IDLE.
//
// Tile iteration order: inner K-tile (accumulate) → middle N-tile → outer M-tile.
// MAX_TILE = 64 hardware constraint.  Ceiling division: ceil(X/64).
//
// COMPUTE timing: mac_array needs K+2 compute_en cycles to flush the PE
// pipeline (learnings from Task 4).
//
// Convention: k_cur is 7-bit (1..64).  compute_k output is lower 6 bits,
// where 0 means "full tile of 64 K-elements".
//=============================================================================

module controller (
    input  wire        clk,
    input  wire        rst_n,

    // ── From mmio_if ──────────────────────────────────────────────────
    input  wire        cmd_start,
    input  wire        cmd_abort,
    input  wire [15:0] dim0_m,
    input  wire [15:0] dim0_k,
    input  wire [15:0] dim1_n,
    input  wire        irq_en,

    // ── Status outputs (to mmio_if) ───────────────────────────────────
    output reg         status_busy,
    output reg         status_done,
    output reg         status_error,
    output reg         irq,

    // ── Buffer load strobes ───────────────────────────────────────────
    output reg         weight_load_en,
    output reg         activation_load_en,

    // ── MAC array control ─────────────────────────────────────────────
    output reg         compute_en,
    output reg  [5:0]  compute_k,       // K tile elements (0 = full 64)
    output reg         mac_reset_acc,

    // ── Store output (read from mac_array, write to SRAM) ─────────────
    output reg         store_out,
    output reg  [5:0]  store_row,

    // ── Debug / verification outputs ──────────────────────────────────
    output reg  [3:0]  state,            // current FSM state, registered (no lag)
    output reg  [15:0] tiles_completed   // number of tiles processed
);

    //=========================================================================
    // Parameters / localparams
    //=========================================================================
    localparam MAX_TILE = 16'd64;

    // FSM state encoding
    localparam S_IDLE      = 4'd0;
    localparam S_READ_DIMS = 4'd1;
    localparam S_LOAD_W    = 4'd2;
    localparam S_LOAD_A    = 4'd3;
    localparam S_COMPUTE   = 4'd4;
    localparam S_STORE_OUT = 4'd5;
    localparam S_DONE      = 4'd6;

    //=========================================================================
    // Internal registers
    //=========================================================================
    // Captured dimensions & tile counts
    reg [15:0] M, K, N;
    reg [15:0] m_tiles, k_tiles, n_tiles;
    reg [15:0] m_tile, k_tile, n_tile;

    // Current tile K dimension (1..64, 7-bit to hold 64)
    reg [6:0]  k_cur;

    // Current tile M dimension (1..64, 7-bit to hold 64)
    reg [6:0]  m_cur;

    // Compute phase: remaining cycles counter (k_cur+1 down to 0)
    reg [6:0]  compute_timer;

    // Store phase: row counter (0..m_cur where m_cur max 64)
    reg [6:0]  store_counter;

    // Tile completion counter
    reg [15:0] done_cnt;

    // Temporary computation register (17-bit to avoid overflow)
    reg [16:0] dim_rem;

    //=========================================================================
    // Single unified always block — state transitions + output logic
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state             <= S_IDLE;
            status_busy       <= 1'b0;
            status_done       <= 1'b0;
            status_error      <= 1'b0;
            irq               <= 1'b0;
            weight_load_en    <= 1'b0;
            activation_load_en <= 1'b0;
            compute_en        <= 1'b0;
            compute_k         <= 6'd0;
            mac_reset_acc     <= 1'b0;
            store_out         <= 1'b0;
            store_row         <= 6'd0;
            tiles_completed   <= 16'd0;
            done_cnt          <= 16'd0;
            M                 <= 16'd0;
            K                 <= 16'd0;
            N                 <= 16'd0;
            m_tiles           <= 16'd0;
            k_tiles           <= 16'd0;
            n_tiles           <= 16'd0;
            m_tile            <= 16'd0;
            k_tile            <= 16'd0;
            n_tile            <= 16'd0;
            k_cur             <= 7'd0;
            m_cur             <= 7'd0;
            compute_timer     <= 7'd0;
            store_counter     <= 7'd0;
            dim_rem           <= 17'd0;

        end else begin
            // ── Default: one-shot signals return to 0 each cycle ─────
            weight_load_en    <= 1'b0;
            activation_load_en <= 1'b0;
            mac_reset_acc     <= 1'b0;
            store_out         <= 1'b0;
            status_done       <= 1'b0;
            status_error      <= 1'b0;
            irq               <= 1'b0;
            compute_en        <= 1'b0;

            case (state)

                //=============================================================
                // IDLE — wait for cmd_start
                //=============================================================
                S_IDLE: begin
                    status_busy <= 1'b0;
                    if (cmd_start) begin
                        done_cnt        <= 16'd0;
                        tiles_completed <= 16'd0;
                        state         <= S_READ_DIMS;
                    end
                end

                //=============================================================
                // READ_DIMS — capture M, K, N; compute tile counts
                //=============================================================
                S_READ_DIMS: begin
                    status_busy <= 1'b1;

                    M <= dim0_m;
                    K <= dim0_k;
                    N <= dim1_n;

                    // Ceiling division: ceil(X/64) = (X + 63) / 64
                    m_tiles <= (dim0_m + 16'd63) / MAX_TILE;
                    k_tiles <= (dim0_k + 16'd63) / MAX_TILE;
                    n_tiles <= (dim1_n + 16'd63) / MAX_TILE;

                    m_tile <= 16'd0;
                    k_tile <= 16'd0;
                    n_tile <= 16'd0;

                    if (cmd_abort) begin
                        state       <= S_IDLE;
                        status_busy   <= 1'b0;
                        status_error  <= 1'b1;
                    end else if (dim0_m == 16'd0 || dim0_k == 16'd0 || dim1_n == 16'd0) begin
                        state <= S_DONE;
                    end else begin
                        state <= S_LOAD_W;
                    end
                end

                //=============================================================
                // LOAD_W — assert weight_load_en for one cycle
                //=============================================================
                S_LOAD_W: begin
                    status_busy    <= 1'b1;
                    weight_load_en <= 1'b1;
                    mac_reset_acc <= (k_tile == 16'd0) ? 1'b1 : 1'b0;

                    // k_cur = min(64, K - k_tile*64), 7-bit (1..64)
                    dim_rem = {1'b0, K} - {1'b0, (k_tile * MAX_TILE)};
                    k_cur <= (dim_rem >= 17'd64) ? 7'd64 : dim_rem[6:0];

                    // m_cur = min(64, M - m_tile*64), 7-bit (1..64)
                    // Computed here (NBA) so it's valid on entry to STORE_OUT
                    dim_rem = {1'b0, M} - {1'b0, (m_tile * MAX_TILE)};
                    m_cur <= (dim_rem >= 17'd64) ? 7'd64 : dim_rem[6:0];

                    if (cmd_abort) begin
                        state       <= S_IDLE;
                        status_busy   <= 1'b0;
                        status_error  <= 1'b1;
                    end else begin
                        state <= S_LOAD_A;
                    end
                end

                //=============================================================
                // LOAD_A — assert activation_load_en for one cycle
                //=============================================================
                S_LOAD_A: begin
                    status_busy       <= 1'b1;
                    activation_load_en <= 1'b1;

                    // compute_timer = k_cur + 1; counts down to 0 → k_cur+2 cycles
                    compute_timer <= k_cur + 7'd1;

                    if (cmd_abort) begin
                        state       <= S_IDLE;
                        status_busy   <= 1'b0;
                        status_error  <= 1'b1;
                    end else begin
                        state <= S_COMPUTE;
                    end
                end

                //=============================================================
                // COMPUTE — strobe compute_en for k_cur+2 cycles
                //=============================================================
                S_COMPUTE: begin
                    status_busy <= 1'b1;
                    compute_en  <= 1'b1;
                    compute_k   <= k_cur[5:0];   // 0 = full tile of 64

                    if (cmd_abort) begin
                        state       <= S_IDLE;
                        status_busy   <= 1'b0;
                        status_error  <= 1'b1;
                    end else if (compute_timer == 7'd0) begin
                        // Accumulate across K-tiles: only store after the last
                        // K-tile of this (M,N) tile group.
                        if (k_tile + 16'd1 < k_tiles) begin
                            k_tile  <= k_tile + 16'd1;
                            state <= S_LOAD_W;
                        end else begin
                            state       <= S_STORE_OUT;
                            store_counter <= 7'd0;
                        end
                    end else begin
                        compute_timer <= compute_timer - 7'd1;
                    end
                end

                //=============================================================
                // STORE_OUT — route row addresses 0..m_cur-1
                //=============================================================
                // Entered only after the final K-tile of an (M,N) group, so
                // k_tile is always the last K-tile here.
                S_STORE_OUT: begin
                    status_busy <= 1'b1;
                    store_out   <= 1'b1;

                    if (cmd_abort) begin
                        state       <= S_IDLE;
                        status_busy   <= 1'b0;
                        status_error  <= 1'b1;
                    end else if (store_counter == m_cur) begin
                        done_cnt        <= done_cnt + 16'd1;
                        tiles_completed <= done_cnt + 16'd1;

                        // ── Tile iteration ──────────────────────────
                        k_tile <= 16'd0;
                        n_tile <= n_tile + 16'd1;
                        if (n_tile + 16'd1 < n_tiles) begin
                            state <= S_LOAD_W;
                        end else begin
                            n_tile <= 16'd0;
                            m_tile <= m_tile + 16'd1;
                            if (m_tile + 16'd1 < m_tiles) begin
                                // Next M-tile: dimensions unchanged, skip READ_DIMS
                                state <= S_LOAD_W;
                            end else begin
                                state <= S_DONE;
                            end
                        end
                    end else begin
                        store_row     <= store_counter[5:0];
                        store_counter <= store_counter + 7'd1;
                    end
                end

                //=============================================================
                // DONE — assert status_done + irq for one cycle
                //=============================================================
                S_DONE: begin
                    status_busy  <= 1'b0;
                    status_done  <= 1'b1;
                    irq          <= irq_en;
                    state      <= S_IDLE;
                end

                //=============================================================
                // Unknown state — fallback to IDLE
                //=============================================================
                default: begin
                    state     <= S_IDLE;
                    status_busy <= 1'b0;
                end

            endcase

        end
    end

endmodule
