/*
 * CaduceusCore Transport Vtable
 *
 * Internal interface that isolates hardware-specific transport
 * (FuncModel, RTL, FPGA, Mock) from the runtime core. Each transport
 * implements this vtable; the runtime core calls only these functions.
 *
 * All functions return 0 on success, negative on error.
 * The transport owns its opaque state pointer (transport_priv).
 */

#ifndef CADUCEUS_CAD_TRANSPORT_H
#define CADUCEUS_CAD_TRANSPORT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Forward declarations for opaque transport-side objects */
typedef struct cad_transport_fence_t   cad_transport_fence_t;
typedef struct cad_transport_buffer_t  cad_transport_buffer_t;

/* ── Transport operation vtable ─────────────────────────────────── */

typedef struct cad_transport_ops_t {
    const char *name; /* e.g. "Mock", "FuncModel", "RTL", "FPGA" */

    /* Device lifecycle */
    int (*device_init)(void *tpriv, const char *uri);
    void (*device_fini)(void *tpriv);

    /* Device reset: abort all in-flight work */
    int (*device_reset)(void *tpriv);

    /* Buffer management. backend_buf is transport-owned. */
    int  (*buffer_alloc)(void *tpriv, cad_transport_buffer_t **backend_buf,
                         uint64_t size);
    void (*buffer_free)(void *tpriv, cad_transport_buffer_t *backend_buf);
    int  (*buffer_read)(void *tpriv, cad_transport_buffer_t *backend_buf,
                        uint64_t offset, uint64_t size, void *dst);
    int  (*buffer_write)(void *tpriv, cad_transport_buffer_t *backend_buf,
                         uint64_t offset, uint64_t size, const void *src);

    /* Get buffer size */
    uint64_t (*buffer_size)(void *tpriv, cad_transport_buffer_t *backend_buf);

    /* Fence creation/destruction */
    int  (*fence_create)(void *tpriv, cad_transport_fence_t **fence_out);
    void (*fence_destroy)(void *tpriv, cad_transport_fence_t *fence);

    /* Fence wait. Returns 0 on signalled, -ETIMEDOUT on timeout, -EAGAIN
     * (or equivalent NOT_READY) for CAD_TIMEOUT_IMMEDIATE with unsignalled
     * fence. timeout_ns uses the CAD_TIMEOUT_* sentinels. */
    int (*fence_wait)(void *tpriv, cad_transport_fence_t *fence,
                      uint64_t timeout_ns);

    /* Fence poll. Returns 0 if signalled, -EAGAIN if not ready. */
    int (*fence_poll)(void *tpriv, cad_transport_fence_t *fence);

    /* Fence status: 0=NOT_READY, 1=COMPLETED, 2=ERROR */
    int (*fence_status)(void *tpriv, cad_transport_fence_t *fence);

    /* Submit a batch of commands to the device. The transport takes
     * ownership of cmd_data on success. On failure, caller retains. */
    int (*submit)(void *tpriv, void *cmd_data, uint32_t cmd_count,
                  cad_transport_fence_t *fence);

} cad_transport_ops_t;

/* ── Transport error → cad_error_t mapping ──────────────────────── */

/* The transport returns negative ints; runtime maps to cad_error_t. */
#define CAD_TR_SUCCESS     0
#define CAD_TR_ERR_UNSUP  (-1)
#define CAD_TR_ERR_NOMEM  (-2)
#define CAD_TR_ERR_INVAL  (-3)
#define CAD_TR_ERR_TIMEDOUT (-4)
#define CAD_TR_ERR_BUSY   (-5)
#define CAD_TR_ERR_LOST   (-6)
#define CAD_TR_ERR_NOTREADY (-7)

#ifdef __cplusplus
}
#endif

#endif /* CADUCEUS_CAD_TRANSPORT_H */
