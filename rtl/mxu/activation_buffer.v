// activation_buffer.v — 64×64 INT8 SRAM (4096 bytes)
// Dual-port: write port (DMA load), read port (MAC array).
// Synchronous read with 1-cycle latency.

`timescale 1ns/1ps

module activation_buffer #(
    parameter DATA_WIDTH = 32,
    parameter DEPTH       = 1024,   // 4096 bytes / 4 bytes per word
    parameter ADDR_WIDTH  = 11      // $clog2(1024)+1 = 11 (extra bit for OOB detection)
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

    // SRAM storage: 1024 × 32-bit = 4096 bytes = 4096 INT8 activations
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
