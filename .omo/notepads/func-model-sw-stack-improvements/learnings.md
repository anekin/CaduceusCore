## I-012: Canonical/build source unification (ggml-npu)

**Problem**: `ggml-npu/ggml-npu.cpp` (canonical) required manual copy to
`third_party/llama.cpp/ggml/src/ggml-npu/ggml-npu.cpp` for the build to pick
up changes. The fetch script (`scripts/fetch_llama_cpp.py`) handled this
on initial setup, but iterative development required either re-running fetch
or manual `cp`.

**Solution**: Symlink approach — `third_party/llama.cpp/ggml/src/ggml-npu/ggml-npu.cpp`
is now a relative symlink `→ ../../../../../ggml-npu/ggml-npu.cpp`.

**Implementation**:
1. `scripts/fetch_llama_cpp.py`: `integrate_backend()` now skips `ggml-npu.cpp`
   in the bulk-copy loop and creates a symlink via `Path.symlink_to()` instead.
2. The symlink is relative (`os.path.relpath`) so the tree can be relocated.

**Verification**:
- `diff ggml-npu/ggml-npu.cpp third_party/.../ggml-npu.cpp` → identical (symlink resolves)
- Edit canonical source → `cmake --build build/llama --target ggml-npu` → recompiles
- Marker string in binary confirmed: `I-012_CANONICAL_PROPAGATION_OK`
- After revert, build succeeds cleanly

**Tradeoff**: Symlink means the third_party tree has a dangling symlink if
the canonical source is removed, but that's a natural failure mode (no silent
stale copy). The fetch script's `verify_state` continues to check
CMakeLists.txt hash, which remains a regular copy.

## DEV-001: Global _last_request_id breaks client reconnect and reset cycles (2026-07-30)

**Problem**: `_last_request_id` was a single `int` shared across all connections.
When client A disconnected and reconnected, or when a client called `cadDeviceReset()`,
the client's next request ID (often resetting to 0 or 1) could be ≤ the global
last-seen ID, causing "request out of order" rejections.

**Fix**: Replaced the single `_last_request_id` with:
- `_next_conn_id: int` — monotonically increasing connection counter
- `_per_conn_last_id: dict[int, int]` — per-connection last request ID
- `_next_request_id_ok(conn_id, req_id)` — checks against the per-connection counter
- `_allocate_conn_id()` / `_release_conn_id()` — lifecycle hooks in `_FmRequestHandler`

On `_do_device_reset` only the **calling connection's** counter is reset to 0,
so other connections are unaffected.

**Files**:
- `sim/device_server.py`: core fix
- `sim/tests/test_device_server_per_conn.py`: 6 new tests (close-reopen,
  reset-after-submit, two-conn independence, out-of-order rejection,
  three sequential connections, many-reqs-reset-more)
