//=============================================================================
// mmio_if — MXU MMIO slave register file
//=============================================================================
// Matches CaduceusCore/sim/regmap.py MXU register map exactly.
//
// Interface:
//   clk, rst_n         — clock and active-low async reset
//   cs, we, addr       — MMIO slave: chip-select, write-enable, byte offset
//   wdata, rdata       — write/read data (32-bit)
//   ready              — asserted when transaction accepted (combinational)
//
// Register map (offsets from MXU_BASE=0x4000_0000, within 4KB window):
//   0x00 CTRL      R/W   [1:0]=dtype (0=INT4xINT8, 1=INT8xINT8, 2=BF16)
//   0x04 CMD       W     [0]=START, [1]=ABORT — write-only, pulses on write
//   0x08 STATUS    R     [0]=BUSY, [1]=DONE, [2]=ERROR — from external inputs
//   0x0C DIM0      R/W   [15:0]=M, [31:16]=K
//   0x10 DIM1      R/W   [15:0]=N
//   0x14 I_ADDR    R/W   activation SRAM address
//   0x18 W_ADDR    R/W   weight SRAM address
//   0x1C O_ADDR    R/W   output SRAM address
//   0x20 BIAS_ADDR R/W   bias SRAM address (0 = no bias)
//   0x24 SCALE_ADDR R/W  scale SRAM address (0 = no scale)
//   0x28 IRQ_EN    R/W   [0]=completion irq enable
//
// Write timing: synchronous posedge clk. cs=1 & we=1 → register updated.
// Read timing: combinatorial. cs=1 & we=0 → rdata reflects register value.
// Undefined addresses read back 0.
//=============================================================================

module mmio_if (
    input  wire        clk,
    input  wire        rst_n,

    // MMIO slave interface
    input  wire        cs,
    input  wire        we,
    input  wire [11:0] addr,
    input  wire [31:0] wdata,
    output reg  [31:0] rdata,
    output wire        ready,

    // External status inputs (driven by controller)
    input  wire        status_busy,
    input  wire        status_done,
    input  wire        status_error,

    // Command pulse outputs (single-cycle pulse on CMD write)
    output wire        cmd_start,
    output wire        cmd_abort,

    // Register value outputs for controller
    output wire [1:0]  ctrl_dtype,
    output wire [15:0] dim0_m,
    output wire [15:0] dim0_k,
    output wire [15:0] dim1_n,
    output wire [31:0] i_addr_o,
    output wire [31:0] w_addr_o,
    output wire [31:0] o_addr_o,
    output wire [31:0] bias_addr_o,
    output wire [31:0] scale_addr_o,
    output wire        irq_en_o
);

    //=========================================================================
    // Register file storage
    //=========================================================================
    reg [31:0] ctrl_reg;
    reg [31:0] dim0_reg;
    reg [31:0] dim1_reg;
    reg [31:0] i_addr_reg;
    reg [31:0] w_addr_reg;
    reg [31:0] o_addr_reg;
    reg [31:0] bias_addr_reg;
    reg [31:0] scale_addr_reg;
    reg [31:0] irq_en_reg;

    //=========================================================================
    // Write path — synchronous posedge clk
    //=========================================================================
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            ctrl_reg     <= 32'd0;
            dim0_reg     <= 32'd0;
            dim1_reg     <= 32'd0;
            i_addr_reg   <= 32'd0;
            w_addr_reg   <= 32'd0;
            o_addr_reg   <= 32'd0;
            bias_addr_reg <= 32'd0;
            scale_addr_reg <= 32'd0;
            irq_en_reg   <= 32'd0;
        end else if (cs && we) begin
            case (addr)
                12'h00: ctrl_reg      <= wdata;
                12'h04: ;              // CMD is write-only — handled below
                12'h08: ;              // STATUS is read-only — ignore writes
                12'h0C: dim0_reg      <= wdata;
                12'h10: dim1_reg      <= wdata;
                12'h14: i_addr_reg    <= wdata;
                12'h18: w_addr_reg    <= wdata;
                12'h1C: o_addr_reg    <= wdata;
                12'h20: bias_addr_reg <= wdata;
                12'h24: scale_addr_reg <= wdata;
                12'h28: irq_en_reg    <= wdata;
                default: ;             // undefined — ignore writes
            endcase
        end
    end

    //=========================================================================
    // CMD write: generate single-cycle pulses on cmd_start / cmd_abort
    //=========================================================================
    reg cmd_start_r;
    reg cmd_abort_r;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            cmd_start_r <= 1'b0;
            cmd_abort_r <= 1'b0;
        end else begin
            // Pulse high for exactly one cycle when CMD is written
            // with the respective bit set.
            cmd_start_r <= (cs && we && (addr == 12'h04) && wdata[0]);
            cmd_abort_r <= (cs && we && (addr == 12'h04) && wdata[1]);
        end
    end

    assign cmd_start = cmd_start_r;
    assign cmd_abort = cmd_abort_r;

    //=========================================================================
    // Read path — combinatorial
    //=========================================================================
    always @(*) begin
        rdata = 32'd0;
        if (cs && !we) begin
            case (addr)
                12'h00: rdata = ctrl_reg;
                12'h04: rdata = 32'd0;   // CMD is write-only
                12'h08: rdata = {29'd0, status_error, status_done, status_busy};
                12'h0C: rdata = dim0_reg;
                12'h10: rdata = dim1_reg;
                12'h14: rdata = i_addr_reg;
                12'h18: rdata = w_addr_reg;
                12'h1C: rdata = o_addr_reg;
                12'h20: rdata = bias_addr_reg;
                12'h24: rdata = scale_addr_reg;
                12'h28: rdata = irq_en_reg;
                default: rdata = 32'd0;   // undefined
            endcase
        end
    end

    //=========================================================================
    // Ready — asserted when chip-select is active (transaction accepted)
    //=========================================================================
    assign ready = cs;

    //=========================================================================
    // Register value outputs for controller
    //=========================================================================
    assign ctrl_dtype  = ctrl_reg[1:0];
    assign dim0_m      = dim0_reg[15:0];
    assign dim0_k      = dim0_reg[31:16];
    assign dim1_n      = dim1_reg[15:0];
    assign i_addr_o    = i_addr_reg;
    assign w_addr_o    = w_addr_reg;
    assign o_addr_o    = o_addr_reg;
    assign bias_addr_o = bias_addr_reg;
    assign scale_addr_o = scale_addr_reg;
    assign irq_en_o    = irq_en_reg[0];

endmodule
