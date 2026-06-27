// Stub prim_util_memload.svh — memory load utility for simulation
// Called from RAM primitives to initialize memory from a file ($readmemh)

`ifdef SIMULATION
  if (MemInitFile != "") begin
    $readmemh(MemInitFile, mem);
  end
`endif
