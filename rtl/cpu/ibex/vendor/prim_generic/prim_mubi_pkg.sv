// Stub prim_mubi_pkg.sv — multi-bit encoded types for OpenTitan
// Minimal definitions for Ibex compilation
package prim_mubi_pkg;
  typedef logic [3:0] mubi4_t;
  typedef logic [7:0] mubi8_t;

  // Return 1 if mubi4 value is the "true" encoding (4'b0101), else 0
  function automatic logic mubi4_test_true_strict(mubi4_t val);
    return (val == 4'b0101);
  endfunction

  // Return 1 if mubi4 value is the "false" encoding (4'b1010), else 0
  function automatic logic mubi4_test_false_strict(mubi4_t val);
    return (val == 4'b1010);
  endfunction
endpackage
