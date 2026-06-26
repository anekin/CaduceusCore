// exp_lut.v — 256-entry exp(x) lookup table, Q1.14 fixed-point
// WITH linear interpolation using adjacent entries.
//
// Domain: x ∈ [-20, 0], 256 linearly-spaced entries.
// Entry index: addr[7:0] selects the base entry.
// Fraction: frac[7:0] = 0..255, weight for next entry.
// Result = lut[addr] * (256-frac)/256 + lut[addr+1] * frac/256
// Special-case: addr=255 uses lut[255] for both (clamped).
//
// Matches GoldenSFU._build_exp_lut / _exp_hw (golden_executor.py).
// Pure combinatorial read — no pipeline registers.
// Hex file loaded via $readmemh.

`timescale 1ns / 1ps

module exp_lut (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [7:0]  addr,      // base LUT address (0..255)
    input  wire [7:0]  frac,      // fractional weight 0..255 for interpolation
    output wire [14:0] lut_out    // Q1.14 fixed-point LUT output (combinatorial)
);

    // ── LUT storage: 256 × 15-bit ──────────────────────────────────
    reg [14:0] lut [0:255];

    // ── Load hex file at simulation time 0 ─────────────────────────
    initial begin
        $readmemh("CaduceusCore/rtl/test_vectors/sfu/luts/exp_lut.hex", lut);
    end

    // ── Combinatorial dual-read + interpolation ────────────────────
    wire [14:0] lut_lo = lut[addr];

    // Next entry: saturate at 255 (so we don't read past the array)
    wire [7:0]  addr_hi = (addr == 8'd255) ? addr : (addr + 8'd1);
    wire [14:0] lut_hi = lut[addr_hi];

    // Linear interpolation:
    // result = (lut_lo * (256 - frac) + lut_hi * frac) >> 8
    // Multiply widths: 15-bit × 8-bit = 23-bit; add → 24-bit; >> 8 → 16-bit
    wire [23:0] interpolated = (lut_lo * (16'd256 - frac)) + (lut_hi * frac);
    assign lut_out = interpolated[22:8];  // >> 8, keep lower 15 bits

endmodule
