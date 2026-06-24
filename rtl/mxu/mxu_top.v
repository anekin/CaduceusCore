//=============================================================================
// mxu_top — MXU Top-Level Integration
//=============================================================================
// Pure integration of 5 submodules: mmio_if, controller, mac_array,
// weight_buffer, activation_buffer.
//
// The accumulator module is already instantiated inside mac_array (Task 3);
// it must NOT be instantiated directly in mxu_top.
//
// Architecture:
//   ┌───────────────────┐
//   │     mmio_if       │← MMIO slave (cs,we,addr,wdata,rdata,ready)
//   │  + cmd_start/abort├────→ controller (dims, irq_en)
//   │  + status ←───────│←──── controller (busy,done,error)
//   └───────────────────┘
//   ┌───────────────────┐
//   │    controller     │← dims from mmio_if
//   │  FSM: IDLE→...→DONE├────→ mac_array (compute_en, reset_acc,
//   │  load strobes      │        store_out, row_addr)
//   └───────────────────┘
//   ┌───────────────────┐      ┌──────────────────────┐
//   │  weight_buffer    │      │  activation_buffer   │
//   │  wr/rd ↔ SRAM     │      │  wr/rd ↔ SRAM        │
//   └───────────────────┘      └──────────────────────┘
//   ┌─────────────────────────────────────────────────┐
//   │                 mac_array (64×64 PE grid)       │
//   │  weight_bus_i ←── testbench (broadcast)         │
//   │  activation_bus_i ←── testbench (broadcast)     │
//   │  acc_out_bus_o ──→ testbench (read results)     │
//   │  [accumulator instantiated internally]          │
//   └─────────────────────────────────────────────────┘
//
// Phase 1 smoke test: weight_bus_i/activation_bus_i are driven directly
// by the testbench, bypassing buffer read ports.  In a production system
// a DMA engine would sequence buffer reads to assemble the broadcast buses.
//=============================================================================

module mxu_top #(
    parameter ADDR_WIDTH = 12   // external SRAM address width
) (
    input  wire        clk,
    input  wire        rst_n,

    // ── MMIO slave interface ──────────────────────────────────────────
    input  wire        cs,
    input  wire        we,
    input  wire [11:0] addr,
    input  wire [31:0] wdata,
    output wire [31:0] rdata,
    output wire        ready,

    // ── Shared SRAM read data (common bus for all SRAMs) ──────────────
    input  wire [31:0] sram_rdata,

    // ── Weight SRAM interface ─────────────────────────────────────────
    output wire [ADDR_WIDTH-1:0] weight_sram_addr,
    output wire                  weight_sram_wr_en,
    output wire                  weight_sram_rd_en,

    // ── Activation SRAM interface ─────────────────────────────────────
    output wire [ADDR_WIDTH-1:0] activation_sram_addr,
    output wire                  activation_sram_wr_en,
    output wire                  activation_sram_rd_en,

    // ── Output SRAM interface (32-bit, serialized) ────────────────────
    output wire [ADDR_WIDTH-1:0] output_sram_addr,
    output wire                  output_sram_wr_en,
    output wire [31:0]           output_sram_wdata,

    // ── Interrupt ─────────────────────────────────────────────────────
    output wire        irq,

    // ── MAC array broadcast buses (driven by testbench / DMA) ─────────
    // Phase 1: testbench drives these directly during compute.
    // weight_bus: 64 × INT4 = 256 bits
    // activation_bus: 64 × INT8 = 512 bits
    input  wire [255:0]  weight_bus_i,
    input  wire [511:0]  activation_bus_i,

    // ── MAC array output bus (full row, 64 × INT32 = 2048 bits) ───────
    // Phase 1: testbench reads this during store_out for result checks.
    output wire [2047:0] acc_out_bus_o,

    // ── Debug / status outputs ────────────────────────────────────────
    output wire [3:0]   state,
    output wire         compute_en_o,
    output wire         weight_load_en_o,
    output wire         activation_load_en_o,
    output wire         store_out_o,
    output wire [5:0]   store_row_o
);

    //=========================================================================
    // Internal wires — mmio_if ↔ controller
    //=========================================================================
    wire        cmd_start;
    wire        cmd_abort;
    wire [1:0]  ctrl_dtype;       // unused at top level
    wire [15:0] dim0_m;
    wire [15:0] dim0_k;
    wire [15:0] dim1_n;
    wire [31:0] i_addr_o;         // unused at top level
    wire [31:0] w_addr_o;         // unused at top level
    wire [31:0] o_addr_o;         // unused at top level
    wire [31:0] bias_addr_o;      // unused (stubbed)
    wire [31:0] scale_addr_o;      // unused (stubbed)
    wire        irq_en;

    wire        status_busy;
    wire        status_done;
    wire        status_error;

    //=========================================================================
    // Internal wires — controller → mac_array / buffers
    //=========================================================================
    wire        weight_load_en;
    wire        activation_load_en;
    wire        compute_en;
    wire [5:0]  compute_k;        // controller output, mac_array doesn't consume
    wire        mac_reset_acc;
    wire        store_out;
    wire [5:0]  store_row;
    wire [15:0] tiles_completed;  // debug

    //=========================================================================
    // Buffer SRAM address wires (shared write/read address per buffer)
    //=========================================================================
    wire [9:0]  weight_buf_wr_addr, weight_buf_rd_addr;
    wire [10:0] activation_buf_wr_addr, activation_buf_rd_addr;

    //=========================================================================
    // 1. mmio_if — MMIO register file
    //=========================================================================
    mmio_if u_mmio_if (
        .clk           (clk),
        .rst_n         (rst_n),
        .cs            (cs),
        .we            (we),
        .addr          (addr),
        .wdata         (wdata),
        .rdata         (rdata),
        .ready         (ready),
        .status_busy   (status_busy),
        .status_done   (status_done),
        .status_error  (status_error),
        .cmd_start     (cmd_start),
        .cmd_abort     (cmd_abort),
        .ctrl_dtype    (ctrl_dtype),
        .dim0_m        (dim0_m),
        .dim0_k        (dim0_k),
        .dim1_n        (dim1_n),
        .i_addr_o      (i_addr_o),
        .w_addr_o      (w_addr_o),
        .o_addr_o      (o_addr_o),
        .bias_addr_o   (bias_addr_o),
        .scale_addr_o  (scale_addr_o),
        .irq_en_o      (irq_en)
    );

    //=========================================================================
    // 2. controller — tile-iteration FSM
    //=========================================================================
    controller u_controller (
        .clk               (clk),
        .rst_n             (rst_n),
        .cmd_start         (cmd_start),
        .cmd_abort         (cmd_abort),
        .dim0_m            (dim0_m),
        .dim0_k            (dim0_k),
        .dim1_n            (dim1_n),
        .irq_en            (irq_en),
        .status_busy       (status_busy),
        .status_done       (status_done),
        .status_error      (status_error),
        .irq               (irq),
        .weight_load_en    (weight_load_en),
        .activation_load_en(activation_load_en),
        .compute_en        (compute_en),
        .compute_k         (compute_k),
        .mac_reset_acc     (mac_reset_acc),
        .store_out         (store_out),
        .store_row         (store_row),
        .state             (state),
        .tiles_completed   (tiles_completed)
    );

    //=========================================================================
    // 3. weight_buffer — 64×64 INT4 SRAM (packed 2:1)
    //=========================================================================
    // Write port: external SRAM write
    // Read port:  external SRAM read (Phase 1: data path bypassed)
    //
    // Use lower ADDR_WIDTH bits for buffer internal addressing; the buffer
    // module's OOB detection will gate out-of-range addresses.
    assign weight_buf_wr_addr = weight_sram_addr[9:0];
    assign weight_buf_rd_addr = weight_sram_addr[9:0];

    weight_buffer #(
        .DATA_WIDTH(32),
        .DEPTH(512),
        .ADDR_WIDTH(10)
    ) u_weight_buffer (
        .clk     (clk),
        .rst_n   (rst_n),
        .wr_en   (weight_sram_wr_en),
        .wr_addr (weight_buf_wr_addr),
        .wr_data (sram_rdata),
        .rd_en   (weight_sram_rd_en),
        .rd_addr (weight_buf_rd_addr),
        .rd_data ()                     // unused: testbench feeds mac_array directly
    );

    //=========================================================================
    // 4. activation_buffer — 64×64 INT8 SRAM
    //=========================================================================
    assign activation_buf_wr_addr = activation_sram_addr[10:0];
    assign activation_buf_rd_addr = activation_sram_addr[10:0];

    activation_buffer #(
        .DATA_WIDTH(32),
        .DEPTH(1024),
        .ADDR_WIDTH(11)
    ) u_activation_buffer (
        .clk     (clk),
        .rst_n   (rst_n),
        .wr_en   (activation_sram_wr_en),
        .wr_addr (activation_buf_wr_addr),
        .wr_data (sram_rdata),
        .rd_en   (activation_sram_rd_en),
        .rd_addr (activation_buf_rd_addr),
        .rd_data ()                     // unused: testbench feeds mac_array directly
    );

    //=========================================================================
    // 5. mac_array — 64×64 PE grid (accumulator instantiated internally)
    //=========================================================================
    mac_array u_mac_array (
        .clk            (clk),
        .rst_n          (rst_n),
        .weight_bus     (weight_bus_i),
        .activation_bus (activation_bus_i),
        .compute_en     (compute_en),
        .reset_acc      (mac_reset_acc),
        .read_out       (store_out),
        .row_addr       (store_row),
        .acc_load       (1'b0),        // not used in Phase 1 smoke test
        .acc_in_bus     (2048'd0),     // tie off
        .acc_out_bus    (acc_out_bus_o),
        .ext_acc_addr   (12'd0),       // tie off — external accumulator not used
        .ext_acc_din    (32'd0),
        .ext_acc_wr     (1'b0),
        .ext_acc_rd     (1'b0),
        .ext_acc_rst    (1'b0),
        .ext_acc_dout   ()             // unused
    );

    //=========================================================================
    // Output SRAM serialization
    //=========================================================================
    // The mac_array outputs a full row (64 × INT32 = 2048 bits) on
    // acc_out_bus_o when store_out is active.  We serialize through a
    // 32-bit output_sram port by cycling through columns 0..63.
    //
    // For the Phase 1 smoke test, the testbench reads acc_out_bus_o
    // directly during store_out; output_sram provides the serialized
    // path for structural completeness.

    reg [5:0]  out_col_counter;        // 0..63 column within current row
    reg        out_latched;            // acc_out_bus_o captured for this row
    reg [2047:0] out_row_data;         // latched row data

    wire       out_new_row;            // store_out rising edge → new row
    reg        store_out_d1;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            store_out_d1    <= 1'b0;
            out_latched     <= 1'b0;
            out_col_counter <= 6'd0;
            out_row_data    <= 2048'd0;
        end else begin
            store_out_d1 <= store_out;

            // Detect rising edge of store_out → latch new row data
            if (store_out && !store_out_d1) begin
                out_row_data    <= acc_out_bus_o;
                out_latched     <= 1'b1;
                out_col_counter <= 6'd0;
            end else if (out_latched) begin
                if (out_col_counter == 6'd63) begin
                    out_latched     <= 1'b0;
                    out_col_counter <= 6'd0;
                end else begin
                    out_col_counter <= out_col_counter + 6'd1;
                end
            end
        end
    end

    // Output SRAM signals
    assign output_sram_wr_en  = out_latched;
    assign output_sram_addr   = {store_row, out_col_counter};
    assign output_sram_wdata  = out_row_data[32*out_col_counter +: 32];

    //=========================================================================
    // Debug outputs (for testbench synchronization)
    //=========================================================================
    assign compute_en_o         = compute_en;
    assign weight_load_en_o     = weight_load_en;
    assign activation_load_en_o = activation_load_en;
    assign store_out_o          = store_out;
    assign store_row_o          = store_row;

endmodule
