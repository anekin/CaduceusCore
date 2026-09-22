//=============================================================================
// doorbell — Host↔NPU ring buffer pointer doorbell (APB slave at 0x4000_5000)
//=============================================================================
// Four 32-bit registers track ring buffer head/tail pointers for the
// Host→NPU command ring and NPU→Host completion ring, plus the ABI-declared
// status window used by the firmware to publish command progress.
//
// Register map (0x00-0x50 = 21 valid words; 0x54 and above are unmapped:
// read returns 0 / writes are ignored, pslverr stays 0):
//   0x00  HOST_TAIL        (RW)  Host writes after enqueuing new commands
//   0x04  NPU_HEAD         (RW)  NPU firmware writes after consuming commands
//   0x08  HOST_HEAD        (RW)  Host completion ring head pointer
//   0x0C  NPU_TAIL         (RW)  NPU completion ring tail pointer
//   0x10  LAST_STATUS      (RW)  Last command status published by firmware
//   0x14-0x50 COMPLETION_STATUS[16] (RW)  Per-slot completion mirror written
//                                by firmware (cmd_id is clamped to [0,15]);
//                                the lossless 1024-entry record stays in DRAM
//   0x54+ unmapped: read 0 / write ignored
//
// Interrupt:
//   doorbell_irq = (HOST_TAIL != NPU_HEAD)
//   → asserts when the host has enqueued commands the NPU has not yet consumed.
//   → compatible with npu_firmware.c main loop polling (reads HOST_TAIL/NPU_HEAD,
//     writes NPU_HEAD after dispatch, then irq clears automatically).
//   → depends ONLY on the two pointer registers; the status window is inert
//     w.r.t. the interrupt.
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
    output wire        doorbell_irq,

    // ── Cocotb backdoor (no VPI debug access needed; host writes HOST_TAIL
    //    and reads NPU_HEAD through these ports so the tb can drive them
    //    without -debug_access+all on the whole design) ─────────────────
    input  wire        bkdoor_we,
    input  wire [1:0]  bkdoor_sel,
    input  wire [31:0] bkdoor_wdata,
    output wire [31:0] bkdoor_rdata
);

    //=========================================================================
    // Register storage
    //=========================================================================
    reg [31:0] host_tail_reg;
    reg [31:0] npu_head_reg;
    reg [31:0] host_head_reg;
    reg [31:0] npu_tail_reg;
    reg [31:0] last_status_reg;
    reg [31:0] completion_status_reg [0:15];

    integer i;  // reset-loop index for the COMPLETION_STATUS array

    //=========================================================================
    // Address decode — word index = paddr[6:2] selects the 32-bit-aligned word
    //=========================================================================
    // Word index (0..20 valid):
    //   0 → HOST_TAIL   1 → NPU_HEAD   2 → HOST_HEAD   3 → NPU_TAIL
    //   4 → LAST_STATUS 5..20 → COMPLETION_STATUS[0..15]
    //
    // Valid window is 0x00 ~ 0x50 (21 words: 4 pointers + LAST_STATUS@0x10 +
    // 16 completion slots @0x14-0x50).  Accesses with paddr[11:7] != 0 or
    // word_idx > 20 are unmapped and return 0 / are ignored.
    //
    // NOTE: paddr[1:0] takes no part in the decode (same style as the original
    // 4-register decoder), so 0x51-0x53 alias to the 0x50 slot.
    wire [1:0] reg_sel;
    wire [4:0] word_idx;
    wire       addr_valid;
    // reg_sel kept for historical reference only — it cannot address the
    // extended window (paddr[3:2] wraps at word 4) and is NOT used below.
    assign reg_sel    = paddr[3:2];
    assign word_idx   = paddr[6:2];
    assign addr_valid = (paddr[11:7] == 5'd0) && (word_idx <= 5'd20);

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
            last_status_reg <= 32'd0;
            for (i = 0; i < 16; i = i + 1)
                completion_status_reg[i] <= 32'd0;
        end else if (bkdoor_we) begin
            case (bkdoor_sel)
                2'b00: host_tail_reg <= bkdoor_wdata;
                2'b01: npu_head_reg  <= bkdoor_wdata;
                2'b10: host_head_reg <= bkdoor_wdata;
                2'b11: npu_tail_reg  <= bkdoor_wdata;
                default: ;
            endcase
        end else if (write_en) begin
            case (word_idx)
                5'd0:  host_tail_reg <= pwdata;
                5'd1:  npu_head_reg  <= pwdata;
                5'd2:  host_head_reg <= pwdata;
                5'd3:  npu_tail_reg  <= pwdata;
                5'd4:  last_status_reg <= pwdata;
                5'd5,  5'd6,  5'd7,  5'd8,  5'd9,  5'd10, 5'd11, 5'd12,
                5'd13, 5'd14, 5'd15, 5'd16, 5'd17, 5'd18, 5'd19, 5'd20:
                       completion_status_reg[word_idx - 5'd5] <= pwdata;
                default: ;
            endcase
        end
    end

    //=========================================================================
    // Read mux — combinational read data (keyed on word_idx)
    //=========================================================================
    wire read_en;
    assign read_en = psel && penable && !pwrite && addr_valid;

    wire [31:0] reg_rdata;
    assign reg_rdata = (word_idx == 5'd0)  ? host_tail_reg :
                       (word_idx == 5'd1)  ? npu_head_reg  :
                       (word_idx == 5'd2)  ? host_head_reg :
                       (word_idx == 5'd3)  ? npu_tail_reg  :
                       (word_idx == 5'd4)  ? last_status_reg :
                       ((word_idx >= 5'd5) && (word_idx <= 5'd20))
                                           ? completion_status_reg[word_idx - 5'd5] :
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

    //=========================================================================
    // Cocotb backdoor read — combinational register mux
    //=========================================================================
    assign bkdoor_rdata = (bkdoor_sel == 2'b00) ? host_tail_reg :
                          (bkdoor_sel == 2'b01) ? npu_head_reg  :
                          (bkdoor_sel == 2'b10) ? host_head_reg :
                          (bkdoor_sel == 2'b11) ? npu_tail_reg  :
                          32'd0;

endmodule
