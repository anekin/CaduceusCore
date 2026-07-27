/*
 * CaduceusCore Runtime Stubs — minimal implementations for ABI testing.
 *
 * These stubs implement enough of the C runtime API to allow the ABI
 * contract tests to compile and validate struct sizes, field offsets,
 * version negotiation, and error-code paths.  The actual transport and
 * hardware backends are implemented in Todo 7 (runtime core + mock
 * transport).
 */

#include "caduceus/runtime.h"
#include <stdlib.h>
#include <string.h>

/* ── Magic numbers to prevent use-after-free ────────────────────────
 *
 * Each handle struct begins with a uint32_t magic.  The validation
 * check requires the exact magic constant.  On close/destroy, the magic
 * is cleared before free() so that a dangling pointer cannot pass
 * validation even if the freed memory is immediately reused.
 */

#define CAD_MAGIC_DEVICE       0xCADE0001U
#define CAD_MAGIC_BUFFER       0xCADE0002U
#define CAD_MAGIC_QUEUE        0xCADE0003U
#define CAD_MAGIC_COMMAND_LIST 0xCADE0004U
#define CAD_MAGIC_FENCE        0xCADE0005U
#define CAD_MAGIC_DEAD         0xDEAD0000U

/* ── Internal handle definitions ─────────────────────────────────── */

typedef struct cad_device_impl_t {
    uint32_t magic;
    uint32_t abi_major;
    uint32_t abi_minor;
} cad_device_impl_t;

typedef struct cad_buffer_impl_t {
    uint32_t magic;
    cad_device_t device;
    uint64_t     size;
} cad_buffer_impl_t;

typedef struct cad_queue_impl_t {
    uint32_t magic;
    cad_device_t device;
} cad_queue_impl_t;

typedef struct cad_command_list_impl_t {
    uint32_t magic;
    cad_device_t device;
    uint32_t     max_entries;
    uint32_t     entry_count;  /* how many entries recorded */
    int          submitted;    /* 1 = ownership transferred to queue */
} cad_command_list_impl_t;

typedef struct cad_fence_impl_t {
    uint32_t magic;
    cad_device_t device;
    int          signalled;    /* non-zero when fence is done */
    cad_fence_status_t status;
} cad_fence_impl_t;

/* ── Helpers ─────────────────────────────────────────────────────── */

static int check_struct_size(uint32_t provided, uint32_t minimum) {
    return provided >= minimum ? 1 : 0;
}

static int validate_device(cad_device_t d) {
    return d != NULL && d->magic == CAD_MAGIC_DEVICE;
}

static int validate_buffer(cad_buffer_t b) {
    return b != NULL && b->magic == CAD_MAGIC_BUFFER;
}

static int validate_queue(cad_queue_t q) {
    return q != NULL && q->magic == CAD_MAGIC_QUEUE;
}

static int validate_command_list(cad_command_list_t cl) {
    return cl != NULL
        && cl->magic == CAD_MAGIC_COMMAND_LIST
        && !cl->submitted;
}

static int validate_fence(cad_fence_t f) {
    return f != NULL && f->magic == CAD_MAGIC_FENCE;
}

/* ── ABI version check ───────────────────────────────────────────── */

static cad_error_t check_abi_compat(uint32_t req_major, uint32_t req_minor) {
    if (req_major != CAD_ABI_MAJOR) {
        return CAD_ERROR_INCOMPATIBLE_ABI;
    }
    if (req_minor > CAD_ABI_MINOR) {
        return CAD_ERROR_INCOMPATIBLE_ABI;
    }
    return CAD_SUCCESS;
}

/* ── Error string ────────────────────────────────────────────────── */

const char *cadErrorString(cad_error_t error) {
    switch (error) {
    case CAD_SUCCESS:               return "Success";
    case CAD_ERROR_INCOMPATIBLE_ABI:return "Incompatible ABI version";
    case CAD_ERROR_INVALID_HANDLE:  return "Invalid handle";
    case CAD_ERROR_INVALID_ARGUMENT:return "Invalid argument";
    case CAD_ERROR_TIMEOUT:         return "Timeout";
    case CAD_ERROR_DEVICE_LOST:     return "Device lost";
    case CAD_ERROR_OUT_OF_MEMORY:   return "Out of memory";
    case CAD_ERROR_NOT_READY:       return "Not ready";
    case CAD_ERROR_DEVICE_BUSY:     return "Device busy";
    case CAD_ERROR_UNSUPPORTED:     return "Unsupported";
    default:                        return "Unknown error";
    }
}

/* ── Device lifecycle ────────────────────────────────────────────── */

cad_error_t cadDeviceOpen(const cad_device_open_info_t *open_info,
                           cad_device_t *device,
                           cad_device_caps_t *caps) {
    if (!open_info || !device || !caps) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (!check_struct_size(open_info->struct_size, CAD_DEVICE_OPEN_INFO_STRUCT_SIZE)) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (!check_struct_size(caps->struct_size, CAD_DEVICE_CAPS_STRUCT_SIZE)) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (!open_info->uri) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }

    cad_error_t abi_err = check_abi_compat(open_info->abi_major, open_info->abi_minor);
    if (abi_err != CAD_SUCCESS) {
        return abi_err;
    }

    /* Validate URI scheme */
    if (strncmp(open_info->uri, "fm://", 5) != 0 &&
        strncmp(open_info->uri, "rtl://", 6) != 0 &&
        strncmp(open_info->uri, "fpga://", 7) != 0 &&
        strncmp(open_info->uri, "mock://", 7) != 0) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }

    cad_device_impl_t *d = calloc(1, sizeof(*d));
    if (!d) {
        return CAD_ERROR_OUT_OF_MEMORY;
    }
    d->magic = CAD_MAGIC_DEVICE;
    d->abi_major = CAD_ABI_MAJOR;
    d->abi_minor = CAD_ABI_MINOR;

    *device = d;

    /* Populate capabilities */
    caps->abi_major = CAD_ABI_MAJOR;
    caps->abi_minor = CAD_ABI_MINOR;
    caps->max_buffers = 4096;
    caps->max_buffer_size = (uint64_t)1024 * 1024 * 1024;  /* 1 GiB */
    caps->max_queues = 8;
    caps->max_command_lists = 256;
    caps->max_command_list_entries = 65536;
    strncpy(caps->device_name, "CaduceusCore NPU", sizeof(caps->device_name) - 1);
    caps->device_name[sizeof(caps->device_name) - 1] = '\0';

    if (strncmp(open_info->uri, "fm://", 5) == 0) {
        strncpy(caps->transport_name, "FuncModel", sizeof(caps->transport_name) - 1);
    } else if (strncmp(open_info->uri, "rtl://", 6) == 0) {
        strncpy(caps->transport_name, "RTL", sizeof(caps->transport_name) - 1);
    } else if (strncmp(open_info->uri, "fpga://", 7) == 0) {
        strncpy(caps->transport_name, "FPGA", sizeof(caps->transport_name) - 1);
    } else {
        strncpy(caps->transport_name, "Mock", sizeof(caps->transport_name) - 1);
    }
    caps->transport_name[sizeof(caps->transport_name) - 1] = '\0';

    return CAD_SUCCESS;
}

cad_error_t cadDeviceClose(cad_device_t device) {
    if (!validate_device(device)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    device->magic = CAD_MAGIC_DEAD;
    free(device);
    return CAD_SUCCESS;
}

cad_error_t cadDeviceGetCaps(cad_device_t device, cad_device_caps_t *caps) {
    if (!validate_device(device)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (!caps) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (!check_struct_size(caps->struct_size, CAD_DEVICE_CAPS_STRUCT_SIZE)) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    caps->abi_major = CAD_ABI_MAJOR;
    caps->abi_minor = CAD_ABI_MINOR;
    caps->max_buffers = 4096;
    caps->max_buffer_size = (uint64_t)1024 * 1024 * 1024;
    caps->max_queues = 8;
    caps->max_command_lists = 256;
    caps->max_command_list_entries = 65536;
    strncpy(caps->device_name, "CaduceusCore NPU", sizeof(caps->device_name) - 1);
    caps->device_name[sizeof(caps->device_name) - 1] = '\0';
    strncpy(caps->transport_name, "Mock", sizeof(caps->transport_name) - 1);
    caps->transport_name[sizeof(caps->transport_name) - 1] = '\0';
    return CAD_SUCCESS;
}

cad_error_t cadDeviceReset(cad_device_t device) {
    if (!validate_device(device)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    return CAD_SUCCESS;
}

/* ── Buffer lifecycle ────────────────────────────────────────────── */

cad_error_t cadBufferAllocate(cad_device_t device,
                               const cad_buffer_create_info_t *create_info,
                               cad_buffer_t *buffer) {
    if (!validate_device(device)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (!create_info || !buffer) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (!check_struct_size(create_info->struct_size, CAD_BUFFER_CREATE_INFO_STRUCT_SIZE)) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (create_info->size == 0) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }

    cad_buffer_impl_t *b = calloc(1, sizeof(*b));
    if (!b) {
        return CAD_ERROR_OUT_OF_MEMORY;
    }
    b->magic = CAD_MAGIC_BUFFER;
    b->device = device;
    b->size = create_info->size;

    *buffer = b;
    return CAD_SUCCESS;
}

cad_error_t cadBufferFree(cad_buffer_t buffer) {
    if (!validate_buffer(buffer)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    buffer->magic = CAD_MAGIC_DEAD;
    free(buffer);
    return CAD_SUCCESS;
}

cad_error_t cadBufferRead(cad_buffer_t buffer,
                           uint64_t offset,
                           uint64_t size,
                           void *data) {
    if (!validate_buffer(buffer)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (!data) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (offset + size > buffer->size) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    /* stub: read returns zeros */
    memset(data, 0, size);
    return CAD_SUCCESS;
}

cad_error_t cadBufferWrite(cad_buffer_t buffer,
                            uint64_t offset,
                            uint64_t size,
                            const void *data) {
    if (!validate_buffer(buffer)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (!data) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (offset + size > buffer->size) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    /* stub: write is a no-op (data accepted, not stored) */
    return CAD_SUCCESS;
}

/* ── Command list lifecycle ──────────────────────────────────────── */

cad_error_t cadCommandListCreate(cad_device_t device,
                                  const cad_command_list_create_info_t *create_info,
                                  cad_command_list_t *cmd_list) {
    if (!validate_device(device)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (!create_info || !cmd_list) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (!check_struct_size(create_info->struct_size,
                           CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE)) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }

    cad_command_list_impl_t *cl = calloc(1, sizeof(*cl));
    if (!cl) {
        return CAD_ERROR_OUT_OF_MEMORY;
    }
    cl->magic = CAD_MAGIC_COMMAND_LIST;
    cl->device = device;
    cl->max_entries = create_info->max_entries > 0
                          ? create_info->max_entries
                          : 65536;
    cl->entry_count = 0;
    cl->submitted = 0;

    *cmd_list = cl;
    return CAD_SUCCESS;
}

cad_error_t cadCommandListDestroy(cad_command_list_t cmd_list) {
    if (!validate_command_list(cmd_list)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    cmd_list->magic = CAD_MAGIC_DEAD;
    free(cmd_list);
    return CAD_SUCCESS;
}

cad_error_t cadCommandListAppendNop(cad_command_list_t cmd_list) {
    if (!validate_command_list(cmd_list)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (cmd_list->entry_count >= cmd_list->max_entries) {
        return CAD_ERROR_OUT_OF_MEMORY;
    }
    cmd_list->entry_count++;
    return CAD_SUCCESS;
}

/* ── Queue lifecycle ─────────────────────────────────────────────── */

cad_error_t cadQueueCreate(cad_device_t device,
                            const cad_queue_create_info_t *create_info,
                            cad_queue_t *queue) {
    if (!validate_device(device)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (!create_info || !queue) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (!check_struct_size(create_info->struct_size,
                           CAD_QUEUE_CREATE_INFO_STRUCT_SIZE)) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }

    cad_queue_impl_t *q = calloc(1, sizeof(*q));
    if (!q) {
        return CAD_ERROR_OUT_OF_MEMORY;
    }
    q->magic = CAD_MAGIC_QUEUE;
    q->device = device;

    *queue = q;
    return CAD_SUCCESS;
}

cad_error_t cadQueueDestroy(cad_queue_t queue) {
    if (!validate_queue(queue)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    queue->magic = CAD_MAGIC_DEAD;
    free(queue);
    return CAD_SUCCESS;
}

cad_error_t cadQueueSubmit(cad_queue_t queue,
                            cad_command_list_t cmd_list,
                            cad_fence_t fence) {
    if (!validate_queue(queue)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (!validate_command_list(cmd_list)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (fence && !validate_fence(fence)) {
        return CAD_ERROR_INVALID_HANDLE;
    }

    /* Transfer ownership: mark command list as submitted */
    cmd_list->submitted = 1;

    /* Signal fence if provided */
    if (fence) {
        fence->signalled = 1;
        fence->status = CAD_FENCE_COMPLETED;
    }

    return CAD_SUCCESS;
}

/* ── Fence lifecycle ─────────────────────────────────────────────── */

cad_error_t cadFenceCreate(cad_device_t device,
                            const cad_fence_create_info_t *create_info,
                            cad_fence_t *fence) {
    if (!validate_device(device)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (!create_info || !fence) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    if (!check_struct_size(create_info->struct_size,
                           CAD_FENCE_CREATE_INFO_STRUCT_SIZE)) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }

    cad_fence_impl_t *f = calloc(1, sizeof(*f));
    if (!f) {
        return CAD_ERROR_OUT_OF_MEMORY;
    }
    f->magic = CAD_MAGIC_FENCE;
    f->device = device;
    f->signalled = 0;
    f->status = CAD_FENCE_NOT_READY;

    *fence = f;
    return CAD_SUCCESS;
}

cad_error_t cadFenceDestroy(cad_fence_t fence) {
    if (!validate_fence(fence)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    fence->magic = CAD_MAGIC_DEAD;
    free(fence);
    return CAD_SUCCESS;
}

cad_error_t cadFenceWait(cad_fence_t fence, uint64_t timeout_ns) {
    if (!validate_fence(fence)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (fence->signalled) {
        return CAD_SUCCESS;
    }
    if (timeout_ns == CAD_TIMEOUT_IMMEDIATE) {
        return CAD_ERROR_NOT_READY;
    }
    /* Stub: fence is signalled immediately on submit, so
     * if we get here with a real timeout, it was never submitted. */
    return CAD_ERROR_TIMEOUT;
}

cad_error_t cadFencePoll(cad_fence_t fence) {
    if (!validate_fence(fence)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (fence->signalled) {
        return CAD_SUCCESS;
    }
    return CAD_ERROR_NOT_READY;
}

cad_error_t cadFenceGetStatus(cad_fence_t fence, cad_fence_status_t *status) {
    if (!validate_fence(fence)) {
        return CAD_ERROR_INVALID_HANDLE;
    }
    if (!status) {
        return CAD_ERROR_INVALID_ARGUMENT;
    }
    *status = fence->signalled ? fence->status : CAD_FENCE_NOT_READY;
    return CAD_SUCCESS;
}
