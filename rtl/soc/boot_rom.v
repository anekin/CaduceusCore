// Boot ROM — 64KB instruction ROM at 0x0000_0000
//
// Initialized via $readmemh from firmware/build/npu_firmware.hex.
// Single-cycle read: 32-bit instruction word output.
//
// Address space:   0x0000_0000 .. 0x0000_FFFF (64 KB)
// Depth:           16384 words × 32-bit
// Hex format:      one 8-char hex word per line (little-endian word order)

module boot_rom (
    input  wire        clk,
    input  wire        rst_n,

    // Instruction fetch — 1-cycle read
    input  wire [13:0] addr_i,      // word address (0..16383)
    output wire [31:0] instr_o,

    // Data read port — 1-cycle read (for data loads from 0x0000_0000)
    input  wire [13:0] data_addr_i,
    output wire [31:0] data_o
);

    // ── 64KB ROM = 16384 × 32-bit ─────────────────────────────────
    reg [31:0] mem [0:16383];

    // ── $readmemh initialization ───────────────────────────────────
    // The hex file path resolves relative to the simulation workdir.
    // Each line is one 8-char hex word (e.g. `ff010113`), directly
    // compatible with $readmemh for a 32-bit wide memory.
    initial begin
        $readmemh("firmware/build/npu_firmware.hex", mem);
    end

    // ── 1-cycle synchronous read (instruction port) ────────────────
    reg [31:0] instr_r;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            instr_r <= 32'h00000013;  // nop (addi x0, x0, 0)
        else
            instr_r <= mem[addr_i];
    end

    assign instr_o = instr_r;

    // ── 1-cycle synchronous read (data port) ────────────────────────
    reg [31:0] data_r;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            data_r <= 32'h0;
        else
            data_r <= mem[data_addr_i];
    end

    assign data_o = data_r;

endmodule
