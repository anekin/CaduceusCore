// Stub prim_util_pkg.sv — utility functions for OpenTitan
// Minimal definitions for Ibex compilation
package prim_util_pkg;

  // vbits: return number of bits needed to represent (value-1) distinct values
  // Equivalent to $clog2(value) for value > 0, else 1
  function automatic integer vbits(integer value);
    if (value <= 1)
      return 1;
    else
      return $clog2(value);
  endfunction

endpackage
