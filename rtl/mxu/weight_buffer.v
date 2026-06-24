// weight_buffer.v — 64×64 INT4 SRAM (4096 weights, 2048 bytes)
// Packed 2 weights per byte: low nibble = even index, high nibble = odd index.
// Matches GoldenMXU.unpack_int4() in golden_executor.py:56-73.
// Dual-port: write port (DMA load), read port (MAC array).
// Synchronous read with 1-cycle latency.

`timescale 1ns/1ps

module weight_buffer #(
    parameter DATA_WIDTH = 32,
    parameter DEPTH       = 512,    // 2048 bytes / 4 bytes per word
    parameter ADDR_WIDTH  = 10      // $clog2(512)+1 = 10 (extra bit for OOB detection)
) (
    input  wire                     clk,
    input  wire                     rst_n,

    // Write port (DMA load)
    input  wire                     wr_en,
    input  wire [ADDR_WIDTH-1:0]   wr_addr,
    input  wire [DATA_WIDTH-1:0]   wr_data,

    // Read port (MAC array) — synchronous, 1-cycle latency
    input  wire                     rd_en,
    input  wire [ADDR_WIDTH-1:0]   rd_addr,
    output reg  [DATA_WIDTH-1:0]   rd_data
);

    // SRAM storage: 512 × 32-bit = 2048 bytes = 4096 INT4 weights
    reg [DATA_WIDTH-1:0] mem [0:DEPTH-1];

    // ── Synchronous write (gated by address range) ──────────────────
    always @(posedge clk) begin
        if (wr_en && (wr_addr < DEPTH))
            mem[wr_addr] <= wr_data;
    end

    // ── Synchronous read (1-cycle latency) ──────────────────────────
    // Address sampled on posedge, data available at same posedge (NBA).
    // Out-of-range read returns 0.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_data <= {DATA_WIDTH{1'b0}};
        end else if (rd_en) begin
            if (rd_addr < DEPTH)
                rd_data <= mem[rd_addr];
            else
                rd_data <= {DATA_WIDTH{1'b0}};
        end
    end

endmodule
