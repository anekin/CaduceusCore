/*
 * CaduceusCore ExecuTorch NPU Backend — Runtime Header
 *
 * Provides the C API for loading preprocessed blobs, binding buffers,
 * and executing NPU commands through the shared Host Runtime.
 *
 * Pin: ExecuTorch v1.2.0
 * Reuses: Todo 11 command blob + Todo 3/7 Host Runtime (no second transport)
 */

#ifndef CADUCEUS_EXECUTORCH_BACKEND_H
#define CADUCEUS_EXECUTORCH_BACKEND_H

#include <stddef.h>
#include <stdint.h>

#include "caduceus/runtime.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ── Backend status codes ─────────────────────────────────────────── */

typedef enum cad_et_status_t {
    CAD_ET_OK               = 0,
    CAD_ET_INVALID_BLOB      = 1,
    CAD_ET_BLOB_VERSION_MISMATCH = 2,
    CAD_ET_BLOB_MAGIC_BAD    = 3,
    CAD_ET_RUNTIME_ERROR     = 4,
    CAD_ET_BUFFER_ERROR      = 5,
    CAD_ET_EXECUTE_ERROR     = 6,
    CAD_ET_OUT_OF_MEMORY     = 7,
    CAD_ET_NOT_INITIALIZED   = 8,
    CAD_ET_INVALID_ARGUMENT  = 9,
    CAD_ET_UNSUPPORTED_OP    = 10,
} cad_et_status_t;

/* ── Opaque backend handle ────────────────────────────────────────── */

typedef struct cad_et_backend_impl_t *cad_et_backend_t;

/* ── Backend lifecycle ────────────────────────────────────────────── */

/*
 * Initialize the NPU backend.
 *
 * device     [in]  an already-opened cad_device_t from the Host Runtime.
 *                   The backend does NOT own the device; the caller retains
 *                   ownership and must close it after cad_et_backend_destroy.
 *
 * Returns NULL on failure (invalid device, allocation failure).
 * Check cad_et_backend_get_last_error() for details.
 */
cad_et_backend_t cad_et_backend_init(cad_device_t device);

/*
 * Destroy the backend.  All bound buffers must have been unbound first.
 * The device handle is NOT closed — that is the caller's responsibility.
 */
cad_et_status_t cad_et_backend_destroy(cad_et_backend_t backend);

/* ── Blob loading ─────────────────────────────────────────────────── */

/*
 * Load a preprocessed (AOT) command blob into the backend.
 *
 * The blob is the output of the AOT preprocessor (Todo 11 encoded format).
 * The backend validates magic, version, and internal consistency.
 *
 * A backend can hold exactly one loaded blob at a time.  Calling
 * cad_et_backend_load_blob again replaces the previous one.
 */
cad_et_status_t cad_et_backend_load_blob(cad_et_backend_t backend,
                                         const uint8_t *blob_data,
                                         size_t blob_size);

/*
 * Unload the current blob, releasing internal state.
 * Idempotent — safe to call when no blob is loaded.
 */
cad_et_status_t cad_et_backend_unload_blob(cad_et_backend_t backend);

/* ── Buffer binding ───────────────────────────────────────────────── */

/*
 * Bind a buffer ID (from the blob's buffer table) to a cad_buffer_t.
 *
 * The buffer must be allocated via cadBufferAllocate on the same device.
 * All buffers referenced by the loaded blob must be bound before execute.
 */
cad_et_status_t cad_et_backend_bind_buffer(cad_et_backend_t backend,
                                           uint32_t buffer_id,
                                           cad_buffer_t buffer);

/*
 * Unbind a previously bound buffer.
 */
cad_et_status_t cad_et_backend_unbind_buffer(cad_et_backend_t backend,
                                             uint32_t buffer_id);

/* ── Execution ────────────────────────────────────────────────────── */

/*
 * Execute the loaded blob on the NPU through the Host Runtime.
 *
 * Internally:
 *   1. Creates a command list
 *   2. Appends the decoded commands (descriptor-based)
 *   3. Submits to the default queue with a fence
 *   4. Waits for the fence
 *
 * Returns CAD_ET_OK on success.
 * Returns CAD_ET_EXECUTE_ERROR if the Runtime reports an error.
 * Returns CAD_ET_NOT_INITIALIZED if no blob is loaded.
 * Returns CAD_ET_BUFFER_ERROR if required buffers are not bound.
 */
cad_et_status_t cad_et_backend_execute(cad_et_backend_t backend);

/* ── Error reporting ──────────────────────────────────────────────── */

/* Return a human-readable description of the last error.
 * The returned pointer is valid until the next backend call. */
const char *cad_et_backend_get_last_error(cad_et_backend_t backend);

/* Return the last internal cad_error_t from the Host Runtime.
 * Only meaningful when cad_et_backend_get_last_error() indicates
 * CAD_ET_RUNTIME_ERROR. */
cad_error_t cad_et_backend_get_runtime_error(cad_et_backend_t backend);

/* Return a human-readable string for a status code. */
const char *cad_et_status_string(cad_et_status_t status);

#ifdef __cplusplus
}
#endif

#endif /* CADUCEUS_EXECUTORCH_BACKEND_H */
