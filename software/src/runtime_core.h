/*
 * CaduceusCore Runtime Core — Internal Header
 *
 * Defines opaque handle structs, magic-number validation, and internal
 * state tracking.  NOT part of the public ABI.
 */

#ifndef CADUCEUS_RUNTIME_CORE_H
#define CADUCEUS_RUNTIME_CORE_H

#include "caduceus/runtime.h"
#include "caduceus/cad_transport.h"

#include <stddef.h>
#include <stdint.h>

/* ── Magic numbers (must match runtime_stubs.c for compat) ────────── */
#define CAD_MAGIC_DEVICE       0xCADE0001U
#define CAD_MAGIC_BUFFER       0xCADE0002U
#define CAD_MAGIC_QUEUE        0xCADE0003U
#define CAD_MAGIC_COMMAND_LIST 0xCADE0004U
#define CAD_MAGIC_FENCE        0xCADE0005U
#define CAD_MAGIC_DEAD         0xDEAD0000U

/* ── Internal handle definitions ─────────────────────────────────── */

typedef struct cad_device_impl_t {
    uint32_t              magic;
    void                 *transport_priv;   /* opaque transport state */
    cad_transport_ops_t   transport;
    uint32_t              abi_major;
    uint32_t              abi_minor;
    char                  transport_name[32];
} cad_device_impl_t;

typedef struct cad_buffer_impl_t {
    uint32_t               magic;
    cad_device_t           device;
    uint64_t               size;
    cad_transport_buffer_t *backend_buf;    /* transport-owned */
} cad_buffer_impl_t;

typedef struct cad_queue_impl_t {
    uint32_t     magic;
    cad_device_t device;
    uint32_t     seq_counter;              /* monotonic submit counter */
} cad_queue_impl_t;

/* Opaque blob reference stored per command-list entry.
 * The runtime does NOT interpret blob contents. */
typedef struct cad_blob_entry_t {
    cad_buffer_t blob_buf;
    uint64_t     offset;
    uint64_t     size;
} cad_blob_entry_t;

typedef struct cad_command_list_impl_t {
    uint32_t         magic;
    cad_device_t     device;
    uint32_t         max_entries;
    uint32_t         entry_count;
    int              submitted;     /* 1 = ownership transferred to queue */
    cad_blob_entry_t *blob_entries; /* array of max_entries, allocated on create */
} cad_command_list_impl_t;

typedef struct cad_fence_impl_t {
    uint32_t                magic;
    cad_device_t            device;
    int                     signalled;
    cad_fence_status_t      status;
    cad_transport_fence_t  *backend_fence; /* transport-owned */
} cad_fence_impl_t;

/* ── Validation helpers ──────────────────────────────────────────── */

static inline int validate_device(cad_device_t d) {
    return d != NULL && d->magic == CAD_MAGIC_DEVICE;
}

static inline int validate_buffer(cad_buffer_t b) {
    return b != NULL && b->magic == CAD_MAGIC_BUFFER;
}

static inline int validate_queue(cad_queue_t q) {
    return q != NULL && q->magic == CAD_MAGIC_QUEUE;
}

static inline int validate_command_list(cad_command_list_t cl) {
    return cl != NULL
        && cl->magic == CAD_MAGIC_COMMAND_LIST
        && !cl->submitted;
}

static inline int validate_fence(cad_fence_t f) {
    return f != NULL && f->magic == CAD_MAGIC_FENCE;
}

/* ── Struct-size check ───────────────────────────────────────────── */
static inline int check_struct_size(uint32_t provided, uint32_t minimum) {
    return provided >= minimum ? 1 : 0;
}

/* ── ABI compat check ────────────────────────────────────────────── */
static inline cad_error_t check_abi_compat(uint32_t req_major,
                                           uint32_t req_minor) {
    if (req_major != CAD_ABI_MAJOR) return CAD_ERROR_INCOMPATIBLE_ABI;
    if (req_minor > CAD_ABI_MINOR)  return CAD_ERROR_INCOMPATIBLE_ABI;
    return CAD_SUCCESS;
}

/* ── Transport error → cad_error_t mapping ───────────────────────── */
static inline cad_error_t trerr_to_cad(int tr_err) {
    switch (tr_err) {
    case CAD_TR_SUCCESS:     return CAD_SUCCESS;
    case CAD_TR_ERR_NOMEM:   return CAD_ERROR_OUT_OF_MEMORY;
    case CAD_TR_ERR_INVAL:   return CAD_ERROR_INVALID_ARGUMENT;
    case CAD_TR_ERR_TIMEDOUT:return CAD_ERROR_TIMEOUT;
    case CAD_TR_ERR_BUSY:    return CAD_ERROR_DEVICE_BUSY;
    case CAD_TR_ERR_LOST:    return CAD_ERROR_DEVICE_LOST;
    case CAD_TR_ERR_NOTREADY:return CAD_ERROR_NOT_READY;
    case CAD_TR_ERR_UNSUP:   return CAD_ERROR_UNSUPPORTED;
    default:                 return CAD_ERROR_UNSUPPORTED;
    }
}

#endif /* CADUCEUS_RUNTIME_CORE_H */
