#pragma once
#ifdef __cplusplus
extern "C" {
#endif

/*
 * ggml_backend_npu_reg() — returns the NPU backend registry.
 *
 * The backend discovers a CaduceusCore device via the CADUCEUS_DEVICE
 * environment variable. Supported URIs:
 *   - mock://       — Self-contained mock device (no external dependencies)
 *   - fm://python   — Func Model via FlatBuffers protocol
 *   - fm://         — Alias for fm://python
 *
 * All device, buffer, queue, and fence operations go through the
 * CaduceusCore Host Runtime C API (caduceus/runtime.h).
 */
struct ggml_backend_reg * ggml_backend_npu_reg(void);

#ifdef __cplusplus
}
#endif
