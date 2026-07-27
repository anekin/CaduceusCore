# Func Model / SoC / FPGA Unified Software Stack — Learnings

## Todo 3: Stable C Host Runtime ABI (2026-07-27)

### Design decisions
- **Vulkan/CUDA/OpenCL convention**: Opaque handles (`cad_device_t`, `cad_buffer_t`, `cad_queue_t`, `cad_command_list_t`, `cad_fence_t`), versioned structs with `struct_size` as first field, explicit create/destroy lifecycle.
- **ABI version negotiation**: Client sets `abi_major`/`abi_minor` in `cad_device_open_info_t`. Runtime checks major match, minor ≤ runtime minor. Returns `CAD_ERROR_INCOMPATIBLE_ABI` on mismatch.
- **URI selection**: `fm://`, `rtl://`, `fpga://`, `mock://` prefixes in the open-info struct select the transport backend.
- **Command list ownership transfer**: On successful `cadQueueSubmit`, ownership of the command list transfers to the queue. On failure, caller retains ownership. This is patterned on Vulkan command buffer submission.
- **Magic-number validation**: Internal handle structs use `uint32_t magic` constants (not `int valid`) to detect use-after-free reliably even when freed memory is reused by the allocator.

### Test coverage
- 15 happy-path tests: struct sizes, field offsets, version negotiation (compatible, older minor, major mismatch), all 4 URIs, buffer lifecycle, command list lifecycle, queue lifecycle, fence lifecycle, caps query, device reset, C++ RAII wrappers, error string lookup.
- 14 negative tests: major mismatch, minor too high, NULL pointers, invalid struct_size, null URI, unsupported URI, use-after-close, null buffer handle, zero-size buffer, consumed command list re-submit, buffer use-after-free, null/invalid fence ops, null open_info, caps query null.

### Build results
- CMake configure: clean
- Compilation: clean (C stubs + C++ tests, no warnings)
- CTest: 2/2 test suites pass (14/14 negative, 15/15 happy)

### Files created
- `software/include/caduceus/runtime.h` — 243 lines, C ABI contract
- `software/include/caduceus/runtime.hpp` — 310 lines, C++ RAII wrapper
- `software/src/runtime_stubs.c` — ~470 lines, minimal stub implementations
- `software/docs/runtime-abi.md` — documentation
- `software/tests/test_runtime_abi.cpp` — 15 tests
- `software/tests/test_runtime_abi_negative.cpp` — 14 tests
- `software/CMakeLists.txt` — build system

### Assumptions made
- ABI starts at major 1, minor 0.
- Device capabilities use static defaults (4096 max buffers, 1 GiB max buffer size, 8 queues, 256 command lists).
- Fence is signalled immediately on submit in the stub (no async execution in this phase).
- Timeouts beyond `CAD_TIMEOUT_IMMEDIATE` in the stub always return timeout if the fence isn't already signalled.
- C++ wrapper `cad::Queue::submit()` calls `release()` on the CommandList only *after* a successful `cadQueueSubmit()`, preserving ownership on failure.
- Build requirement: C11+ for stubs, C++17 for tests.
