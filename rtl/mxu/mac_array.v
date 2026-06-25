`timescale 1ns / 1ps
//=============================================================================
// mac_array: 64×64 Broadcast-Based Systolic MAC Array
//=============================================================================
// Instantiates a 64×64 grid of Processing Elements (PE) with per-PE local
// accumulator registers. The accumulator module (Task 3) is instantiated
// internally for external accumulator load/store across tile boundaries.
//
// Broadcast scheme:
//   Weight broadcast: weight_bus entry [c] (INT4) broadcast to all PEs in column c.
//   Activation broadcast: activation_bus entry [r] (INT8) broadcast to all PEs in row r.
//
// Accumulation timing (handles PE 1-cycle pipeline):
//   Each PE computes pe_mac = weight * activation (registered, 1 cycle latency).
//   The mac_array feeds pe_mac through a 1-cycle delay register (pe_d1),
//   then accumulates: local_acc <= local_acc + pe_d1 (with saturation).
//   For N K-cycles, N+2 compute cycles are needed (N inputs + 2 flush).
//
// Compute a single 64×64×64 tile:
//   For K=0..63: broadcast weight[K] and activation[K].
//   PE(r,c) computes: activation[K][r] * weight[K][c].
//   After 66 compute_en cycles: local_acc[r][c] = sum_{K=0}^{63} product.
//
// Does NOT implement N/M/K tile iteration controller (Task 6).
//=============================================================================

module mac_array (
    input  wire                clk,
    input  wire                rst_n,

    // ── Weight/Activation broadcast buses ────────────────────────────
    // weight_bus: 64 × INT4, flattened to [255:0]
    //   weight_bus[4*c +: 4] → weight for column c
    input  wire      [255:0]   weight_bus,

    // activation_bus: 64 × INT8, flattened to [511:0]
    //   activation_bus[8*r +: 8] → activation for row r
    input  wire      [511:0]   activation_bus,

    // ── Control ──────────────────────────────────────────────────────
    input  wire                compute_en,    // PE compute strobe (can be held high)
    input  wire                reset_acc,     // Synchronous clear of all per-PE accumulators
    input  wire                read_out,      // Output selected row's accumulator values
    input  wire        [5:0]   row_addr,      // Row to read (0..63)

    // ── Row-wise accumulator load (for loading initial values) ────────
    input  wire                acc_load,      // Load acc_in_bus into selected row's accumulators
    input  wire     [2047:0]   acc_in_bus,    // 64 × INT32 values for selected row

    // ── Row-wise accumulator output ──────────────────────────────────
    output wire     [2047:0]   acc_out_bus,   // 64 × INT32 values for selected row

    // ── External accumulator module access (for controller multi-tile) ─
    input  wire       [11:0]   ext_acc_addr,  // Flattened {row[5:0], col[5:0]}
    input  wire       [31:0]   ext_acc_din,   // Data to write (accumulate)
    input  wire                ext_acc_wr,    // Assert accumulate in accumulator module
    input  wire                ext_acc_rd,    // Assert read_out in accumulator module
    input  wire                ext_acc_rst,   // Assert reset_cmd in accumulator module
    output wire       [31:0]   ext_acc_dout   // Data read from accumulator module
);

    //=========================================================================
    // Local parameters
    //=========================================================================
    localparam ROWS    = 64;
    localparam COLS    = 64;
    localparam INT32_MAX_32 = 32'h7FFFFFFF;
    localparam INT32_MIN_32 = 32'h80000000;
    localparam signed [32:0] THRESH_POS = 33'sd2147483647;
    localparam signed [32:0] THRESH_NEG = -33'sd2147483648;

    //=========================================================================
    // Unpack weight and activation buses into per-column / per-row wires
    //=========================================================================
    wire signed [3:0]  w_col [0:COLS-1];   // weight for each column
    wire signed [7:0]  a_row [0:ROWS-1];   // activation for each row
    wire signed [31:0] acc_load_val [0:COLS-1];  // per-column acc_in values

    genvar r, c;
    generate
        for (c = 0; c < COLS; c = c + 1) begin : gen_unpack_w
            assign w_col[c] = weight_bus[4*c +: 4];
        end
        for (r = 0; r < ROWS; r = r + 1) begin : gen_unpack_a
            assign a_row[r] = activation_bus[8*r +: 8];
        end
        for (c = 0; c < COLS; c = c + 1) begin : gen_unpack_acc
            assign acc_load_val[c] = acc_in_bus[32*c +: 32];
        end
    endgenerate

    //=========================================================================
    // 64×64 PE grid with per-PE accumulator registers
    //=========================================================================
    // Each PE(r,c):
    //   - Receives a_row[r] (broadcast along row) and w_col[c] (broadcast along column)
    //   - acc_in is tied to 0 (PE computes product only; external accumulation)
    //   - pe_mac: registered product output (1 cycle latency)
    //   - pe_d1: 1-cycle delayed pe_mac (for feedback timing)
    //   - local_acc: per-PE accumulator register
    //
    // Timing:
    //   Cycle N:   PE samples a_row, w_col, acc_in=0
    //   Cycle N+1: pe_mac <= product (registered)
    //              pe_d1  <= pe_mac (old value: previous cycle's product)
    //   Cycle N+2: local_acc <= saturate(local_acc + pe_d1) ← previous product added
    //
    //   So after K weight/act inputs + 2 extra compute cycles:
    //     local_acc = sum of all K products.

    wire signed [31:0] pe_mac    [0:ROWS-1][0:COLS-1];
    reg  signed [31:0] pe_d1     [0:ROWS-1][0:COLS-1];
    reg  signed [31:0] local_acc [0:ROWS-1][0:COLS-1];

    // Simulation: zero-initialize accumulator registers
    integer _ri, _ci;
    initial begin
        for (_ri = 0; _ri < ROWS; _ri = _ri + 1) begin
            for (_ci = 0; _ci < COLS; _ci = _ci + 1) begin
                local_acc[_ri][_ci] = 32'd0;
                pe_d1[_ri][_ci]     = 32'd0;
            end
        end
    end

    generate
        for (r = 0; r < ROWS; r = r + 1) begin : gen_rows
            for (c = 0; c < COLS; c = c + 1) begin : gen_cols

                // ── PE instance ────────────────────────────────────
                // acc_in tied to 0: PE only computes weight * activation.
                // Accumulation is handled externally by local_acc + pe_d1.
                pe u_pe (
                    .clk        (clk),
                    .rst_n      (rst_n),
                    .activation (a_row[r]),      // row-broadcast activation
                    .weight     (w_col[c]),      // column-broadcast weight
                    .acc_in     (32'd0),         // external accumulation
                    .mac_out    (pe_mac[r][c])
                );

                // ── Per-PE accumulation ─────────────────────────────
                // Saturation logic for local_acc + pe_d1
                wire signed [32:0] sum_33;
                wire signed [31:0] saturated;

                assign sum_33    = $signed(local_acc[r][c]) + $signed(pe_d1[r][c]);
                assign saturated = (sum_33 > THRESH_POS) ? INT32_MAX_32
                                 : (sum_33 < THRESH_NEG) ? INT32_MIN_32
                                 : sum_33[31:0];

                always @(posedge clk or negedge rst_n) begin
                    if (!rst_n) begin
                        local_acc[r][c] <= 32'd0;
                        pe_d1[r][c]     <= 32'd0;
                    end else begin
                        if (reset_acc) begin
                            local_acc[r][c] <= 32'd0;
                            pe_d1[r][c]     <= 32'd0;
                        end else if (acc_load && (row_addr == r[5:0])) begin
                            local_acc[r][c] <= acc_load_val[c];
                            pe_d1[r][c]     <= 32'd0;
                        end else if (compute_en) begin
                            // Accumulate: add previous PE output
                            local_acc[r][c] <= saturated;
                            // Capture current PE output for next cycle's accumulation
                            pe_d1[r][c]     <= pe_mac[r][c];
                        end
                    end
                end

            end  // gen_cols
        end  // gen_rows

        // ── Row-wise readout mux ─────────────────────────────────────
        for (c = 0; c < COLS; c = c + 1) begin : gen_out_mux
            assign acc_out_bus[32*c +: 32] = read_out ? local_acc[row_addr][c] : 32'd0;
        end
    endgenerate

    //=========================================================================
    // Internal accumulator module instantiation (Task 3)
    //=========================================================================
    // Provides an addressable 64×64 INT32 storage for external access.
    // The controller (Task 6) uses this for loading/storing accumulator data
    // across tile boundaries. During mac_array compute, the per-PE local_acc
    // registers handle all accumulation — this module is for the external
    // interface only.

    accumulator u_accumulator (
        .clk        (clk),
        .rst_n      (rst_n),
        .addr       (ext_acc_addr),
        .acc_in     (ext_acc_din),
        .acc_out    (ext_acc_dout),
        .accumulate (ext_acc_wr),
        .read_out   (ext_acc_rd),
        .reset_cmd  (ext_acc_rst)
    );

endmodule
