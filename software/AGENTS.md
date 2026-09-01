# software/ — Host runtime, command compiler, transports, ExecuTorch delegate

## OVERVIEW
C11/C++17 host stack that talks to the NPU through URI-selected transports: mock:// (CI default), fm:// (Func Model over Unix socket), rtl://, fpga:// (reserved). Exposes a Vulkan-style stable C ABI in `include/`, a bespoke typed command IR in `compiler/`, and a Python ctypes binding in `python/`.

## STRUCTURE
```
include/caduceus/  public C ABI headers (runtime.h, cad_transport.h, transport_*.h)
src/               runtime_core.c + transports (mock/fm/rtl/fpga) + ABI stubs
compiler/          command IR: C (ir.c/lower.c/blob.c) + Python mirror (command_ir*.py)
schema/            device protocol: FlatBuffers .fbs + protobuf .proto (fm:// wire format)
python/            ctypes binding caduceus_runtime.py + pip setup.py + py tests
tests/             C/C++ doctest suites, positive + *_negative variants
executorch/        ExecuTorch delegate (VENDORED — never edit)
docs/              runtime-abi.md design doc
```

## WHERE TO LOOK
| Task | Location |
|------|----------|
| Stable C host ABI (struct_size, ABI 1.0) | `include/caduceus/runtime.h`; design in `docs/runtime-abi.md` |
| Transport vtable + error mapping | `include/caduceus/cad_transport.h` |
| Runtime core / transports | `src/runtime_core.c`, `transport_mock.c`, `transport_fm.cpp`, `transport_rtl.cpp`, `transport_fpga.cpp` |
| Command IR (C + Python) | `compiler/` — bespoke IR rationale in `compiler/IR_TRADEOFF.md` |
| fm:// wire protocol | `schema/device_protocol.fbs` (+ .proto); generated stubs in `gen/device_protocol` |
| Python binding + conformance | `python/caduceus_runtime.py`, `python/test_*.py` |
| ExecuTorch delegate | `executorch/runtime/caduceus_npu_backend.cpp` (vendored) |
| CI tier jobs (l0..l5, framework) | `.github/workflows/caduceus-core-ci.yml` |

## CONVENTIONS
- CMake libs: `caduceus_runtime_headers` (INTERFACE) / `caduceus_runtime_stubs` / `caduceus_runtime_core` (STATIC) / `caduceus_runtime_shared`, plus `caduceus_command_ir` and `caduceus_et_backend`; options `CADUCEUS_BUILD_TESTS`, `CADUCEUS_BUILD_SHARED`.
- Device URI scheme: mock:// (default), fm:// (Unix socket, CRC32-checked request/response), rtl://, fpga:// — no silent fallback between them.
- Negative tests: `*_negative.cpp` with `ASAN_OPTIONS=halt_on_error=0` (deliberate use-after-free cases); real fm:// hard gates shell out to `scripts/run_mmul_*`, `run_chain_test.sh`, `run_execution_stats_test.sh`.
- ABI evolution: append fields at struct end only; caller sets `struct_size`, callee rejects undersized structs (`CAD_ERROR_INVALID_ARGUMENT`); version is `CAD_ABI_MAJOR 1 / MINOR 0`.
- FlatBuffers toolchain pinned at `/tmp/flatbuffers-25.2.10` (machine-local, not a repo dep); `software/schema/` is source, `gen/device_protocol` is generated.
- Python binding finds the .so via `CADUCEUS_RUNTIME_LIB`, then adjacent lib/, then fallback paths; dev symlink `software/build/libcaduceus_runtime.so` is recreated on every build.

## ANTI-PATTERNS
- fpga:// must fail explicitly as UNSUPPORTED — never degrade to mock (locked by `test_unsupported_uri`).
- Never edit `gen/` generated ABI/protocol stubs; regenerate from `software/schema/` (or `spec/npu_abi.json`).
- Never hand-edit vendored `executorch/` sources.
- Do not introduce MLIR / FlatBuffers-as-IR / SPIR-V dependencies; bespoke IR is the locked choice (determinism, no build deps).
- Cmd blobs must stay bit-exact and deterministic across C++ and Python; round-trip tests are the lock.

## NOTES
- CI is software-stack only (mock://), never VCS/RTL; `l5_fpga_nogo` is `continue-on-error` and expected BLOCKED (no board in CI).
- `release_aggregator` requires all tiers l0-l5,framework with `--allow-stale`; it FAILS if `software/build/libcaduceus_runtime.so` is a symlink instead of a real file.
- fm:// submit carries firmware ring-buffer entries (24 B) + descriptors (60 B) as one `cmd_blob`; fence exec stats (mmul/sfu/vector/dma ops) come from the server.
