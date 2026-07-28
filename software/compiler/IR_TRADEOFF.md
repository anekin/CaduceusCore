# Command IR Technology Trade-off

## Decision

Use a **small, bespoke typed binary IR** for the production command compiler.
MLIR and FlatBuffers were evaluated and rejected for this phase; the bespoke IR
is deterministic, versioned, has no new build-time dependencies, and maps
1-to-1 to the generated ABI descriptors consumed by firmware.

## Candidate technologies evaluated

### 1. MLIR (Apache 2.0 with LLVM exception)

Pros:
- Mature infrastructure for defining dialects, passes, and transformations.
- Can lower through LLVM or directly to device descriptors.
- Well suited for future graph-level optimizations and multi-target lowering.

Cons for CaduceusCore today:
- **No MLIR toolchain in the workspace.** `mlir-opt`, `mlir-tblgen`, and the
  LLVM/MLIR headers are not installed and would add a large dependency to the
  firmware/runtime build.
- The IR we need is a thin command list (MMUL/SFU/Vector/DMA/barrier entries).
  A full MLIR dialect, ODS definitions, and pass pipeline would be
  disproportionate to the immediate requirement of producing versioned
  descriptor tables for firmware.
- Firmware expects a compact, deterministic binary blob, not MLIR bytecode or
  LLVM IR. We would still need a bespoke lowering pass and binary encoder.

Verdict: **Deferred.** Re-evaluate when the stack needs graph-level transforms,
multi-target lowering, or when LLVM/MLIR is already a project dependency.

### 2. FlatBuffers (Apache 2.0)

Pros:
- Compact binary serialization with zero-copy access in C++.
- Strong forwards/backwards compatibility story via schema evolution.
- Supports Python and C/C++ code generation.

Cons for CaduceusCore today:
- **No `flatc` compiler in the workspace.** Adding it would require either a
  system package install or vendoring the FlatBuffers repository, increasing
  build complexity.
- The firmware ABI is already a fixed, generated C structure layout
  (`gen/npu_abi.h`). FlatBuffers would introduce a second, parallel schema for
  the same descriptor fields and require translation into the ABI layout before
  the blob is usable.
- Our blobs must be bit-exact and deterministic across C++ and Python.
  FlatBuffers defaults can vary with schema options; a hand-written encoder is
  simpler to audit for determinism.

Verdict: **Deferred.** Re-evaluate if the project later adopts FlatBuffers for
model weights or runtime messages and the toolchain is already present.

### 3. SPIR-V / StableHLO

Pros:
- Standard ML compute representations.

Cons:
- **Neither is installed.** Both would require substantial toolchain work.
- They target compute kernels, not the CaduceusCore command-ring + descriptor
  model. Mapping them to MMIO descriptors would be indirect and lossy.

Verdict: **Not suitable** for this hardware abstraction level.

### 4. Bespoke typed binary IR

Pros:
- No new dependencies; builds with the existing C11/C++17 toolchain and Python
  standard library.
- Directly encodes the command-ring entry and descriptor layouts defined in
  `gen/npu_abi.h` / `gen/npu_abi.py`.
- Deterministic, versioned, and auditable.
- C API can hide IR internals from framework adapters (llama.cpp, ExecuTorch).

Cons:
- Manual schema evolution; no automatic compatibility machinery like
  FlatBuffers.
- Less ecosystem support for advanced compiler transformations.

Mitigations:
- Blob header carries major/minor version and a magic constant.
- Encoder/decoder round-trip tests in both C++ and Python lock the format.
- Validation layer rejects malformed or version-mismatched blobs.

## Conclusion

The bespoke IR is the pragmatic choice for the current phase. It satisfies the
acceptance criteria (TDD, deterministic versioned blobs, ABI lowering, C/Python
round-trip) without adding build-time dependencies. MLIR or FlatBuffers can be
adopted later when the project is ready to maintain the corresponding toolchain.
