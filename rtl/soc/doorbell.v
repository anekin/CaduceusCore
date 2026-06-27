//=============================================================================
// doorbell — Host↔NPU ring buffer pointer doorbell (APB slave at 0x4000_5000)
//=============================================================================
// Four 32-bit registers track ring buffer head/tail pointers for the
// Host→NPU command ring and NPU→Host completion ring.
//
// Register map:
//   0x00  HOST_TAIL  (RW)  Host writes after enqueuing new commands
//   0x04  NPU_HEAD   (RW)  NPU firmware writes after consuming commands
//   0x08  HOST_HEAD  (RW)  Host completion ring head pointer
//   0x0C  NPU_TAIL   (RW)  NPU completion ring tail pointer
//
// Interrupt:
//   doorbell_irq = (HOST_TAIL != NPU_HEAD)
//   → asserts when the host has enqueued commands the NPU has not yet consumed.
//   → compatible with npu_firmware.c main loop polling (reads HOST_TAIL/NPU_HEAD,
//     writes NPU_HEAD after dispatch, then irq clears automatically).
//
// APB protocol: AMBA APB v2.0, zero-wait-state.
//   Write: data captured on posedge clk when psel=1, penable=1, pwrite=1.
//   Read:  prdata driven combinatorially when psel=1, penable=1, pwrite=0.
//=============================================================================

module doorbell (
    input  wire        clk,
    input  wire        rst_n,

    // ── APB slave ───────────────────────────────────────────────────────
    input  wire        psel,
    input  wire        penable,
    input  wire [11:0] paddr,
    input  wire        pwrite,
    input  wire [31:0] pwdata,
    output wire [31:0] prdata,
    output wire        pready,
    output wire        pslverr,

    // ── Doorbell interrupt (to INTC source bit 5: host) ─────────────────
    output wire        doorbell_irq
);

    //=========================================================================
    // Register storage
    //=========================================================================
    reg [31:0] host_tail_reg;
    reg [31:0] npu_head_reg;
    reg [31:0] host_head_reg;
    reg [31:0] npu_tail_reg;

    //=========================================================================
    // Address decode — paddr[3:2] selects 32-bit-aligned register
    //=========================================================================
    // Register index (0..3):
    //   2'b00 → HOST_TAIL    2'b01 → NPU_HEAD
    //   2'b10 → HOST_HEAD    2'b11 → NPU_TAIL
    //
    // Only addresses 0x00 ~ 0x0F are valid (4 × 4-byte registers).
    // Accesses to paddr[11:4] != 0 are unmapped and return 0 / are ignored.
    wire [1:0] reg_sel;
    wire       addr_valid;
    assign reg_sel    = paddr[3:2];
    assign addr_valid = (paddr[11:4] == 8'h00);

    //=========================================================================
    // Write logic — registers captured on APB access-phase posedge
    //=========================================================================
    wire write_en;
    assign write_en = psel && penable && pwrite && addr_valid;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            host_tail_reg <= 32'd0;
            npu_head_reg  <= 32'd0;
            host_head_reg <= 32'd0;
            npu_tail_reg  <= 32'd0;
        end else if (write_en) begin
            case (reg_sel)
                2'b00: host_tail_reg <= pwdata;
                2'b01: npu_head_reg  <= pwdata;
                2'b10: host_head_reg <= pwdata;
                2'b11: npu_tail_reg  <= pwdata;
                default: ;
            endcase
        end
    end

    //=========================================================================
    // Read mux — combinational read data
    //=========================================================================
    wire read_en;
    assign read_en = psel && penable && !pwrite && addr_valid;

    wire [31:0] reg_rdata;
    assign reg_rdata = (reg_sel == 2'b00) ? host_tail_reg :
                       (reg_sel == 2'b01) ? npu_head_reg  :
                       (reg_sel == 2'b10) ? host_head_reg :
                       (reg_sel == 2'b11) ? npu_tail_reg  :
                       32'd0;

    assign prdata  = read_en ? reg_rdata : 32'd0;

    //=========================================================================
    // APB handshake — zero wait states, no error
    //=========================================================================
    assign pready  = psel && penable;
    assign pslverr = 1'b0;

    //=========================================================================
    // Doorbell interrupt — combinational, HOST_TAIL != NPU_HEAD
    //=========================================================================
    assign doorbell_irq = (host_tail_reg != npu_head_reg);

endmodule
