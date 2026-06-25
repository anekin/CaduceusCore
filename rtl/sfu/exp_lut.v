// exp_lut.v — 256-entry exp(x) lookup table, Q8.4 fixed-point
//
// Domain: x ∈ [-20, 0], 256 linearly-spaced entries.
//   entry[i] = exp(x_i) where x_i = -20 + i * (20 / 255)
//   entry[0]   = exp(-20) ≈ 0
//   entry[255] = exp(0)   = 1.0 → 0x0010 (Q8.4)
//
// Matches GoldenSFU._build_exp_lut (golden_executor.py:307-319).
// Pure combinatorial read — no pipeline registers.
// Hex file loaded via $readmemh from CaduceusCore/rtl/test_vectors/sfu/luts/exp_lut.hex.
//
// Precision: Q8.4 unsigned (8 integer + 4 fraction bits, 12-bit total).
//   Value = raw / 16.  Range: [0, 255.9375].
//   Fraction resolution: 0.0625 (1/16).

`timescale 1ns/1ps

module exp_lut (
    input  wire         clk,       // clock (present for interface consistency)
    input  wire         rst_n,     // async reset (active low)
    input  wire [7:0]   addr,      // LUT address (0..255)
    output wire [11:0]  lut_out    // Q8.4 fixed-point LUT output (combinatorial)
);

    // ── LUT storage: 256 × 12-bit ──────────────────────────────────
    reg [11:0] lut [0:255];

    // ── Load hex file at simulation time 0 ─────────────────────────
    // Path relative to VCS working directory (project root).
    // If file not found, LUT entries remain X — but synthesis would
    // handle this with a ROM compiler or explicit default.
    initial begin
        $readmemh("CaduceusCore/rtl/test_vectors/sfu/luts/exp_lut.hex", lut);
    end

    // ── Combinatorial read ─────────────────────────────────────────
    assign lut_out = lut[addr];

endmodule
