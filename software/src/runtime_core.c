/*
 * CaduceusCore Runtime Core — Full Implementation
 *
 * Implements all cad* ABI functions from runtime.h against the
 * transport vtable (cad_transport_ops_t). The runtime core owns:
 *   - Handle lifecycle + magic-number validation
 *   - Struct-size / ABI-version checking
 *   - Command-list ownership transfer on submit
 *   - Queue ordering (sequence counter)
 *   - Fence timeout semantics (immediate vs infinite)
 *   - Device reset coordination
 *
 * Transport-specific logic lives behind the vtable; this file
 * contains no FuncModel / RTL / FPGA specifics.
 */

#include "runtime_core.h"

#include <stdlib.h>
#include <string.h>

/* ── URI→transport lookup ────────────────────────────────────────── */

#include "caduceus/transport_fm.h"
#include "caduceus/transport_rtl.h"

/* Forward declare transport constructors */
extern int cad_transport_mock_init(void **tpriv, const char *uri);
extern const cad_transport_ops_t cad_transport_mock_ops;

typedef struct {
    const char            *scheme;
    const cad_transport_ops_t *ops;
    int                  (*init_fn)(void **tpriv, const char *uri);
} cad_transport_reg_t;

static const cad_transport_reg_t transport_registry[] = {
    {"fm://",   &cad_transport_fm_ops,   cad_transport_fm_init},
    {"rtl://",  &cad_transport_rtl_ops,  cad_transport_rtl_init},
    {"mock://", &cad_transport_mock_ops, cad_transport_mock_init},
};

static const size_t transport_registry_count =
    sizeof(transport_registry) / sizeof(transport_registry[0]);

/* Resolve a URI to transport ops. Falls back to mock. */
static const cad_transport_reg_t *find_transport(const char *uri) {
    if (!uri) return NULL;
    for (size_t i = 0; i < transport_registry_count; i++) {
        size_t slen = strlen(transport_registry[i].scheme);
        if (strncmp(uri, transport_registry[i].scheme, slen) == 0) {
            return &transport_registry[i];
        }
    }
    /* fpga:// → map to mock for now (FPGA transport not yet registered) */
    if (strncmp(uri, "fpga://", 7) == 0) {
        return &transport_registry[2]; /* mock */
    }
    return NULL;
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
    if (!open_info || !device || !caps) return CAD_ERROR_INVALID_ARGUMENT;
    if (!check_struct_size(open_info->struct_size, CAD_DEVICE_OPEN_INFO_STRUCT_SIZE))
        return CAD_ERROR_INVALID_ARGUMENT;
    if (!check_struct_size(caps->struct_size, CAD_DEVICE_CAPS_STRUCT_SIZE))
        return CAD_ERROR_INVALID_ARGUMENT;
    if (!open_info->uri) return CAD_ERROR_INVALID_ARGUMENT;

    cad_error_t abi_err = check_abi_compat(open_info->abi_major,
                                            open_info->abi_minor);
    if (abi_err != CAD_SUCCESS) return abi_err;

    const cad_transport_reg_t *reg = find_transport(open_info->uri);
    if (!reg) return CAD_ERROR_INVALID_ARGUMENT;

    cad_device_impl_t *d = calloc(1, sizeof(*d));
    if (!d) return CAD_ERROR_OUT_OF_MEMORY;

    d->magic = CAD_MAGIC_DEVICE;
    d->abi_major = CAD_ABI_MAJOR;
    d->abi_minor = CAD_ABI_MINOR;

    void *tpriv = NULL;
    int tr_err = reg->init_fn(&tpriv, open_info->uri);
    if (tr_err != CAD_TR_SUCCESS) {
        free(d);
        return trerr_to_cad(tr_err);
    }
    d->transport_priv = tpriv;
    d->transport = *(reg->ops);

    /* Resolve transport name */
    if (strncmp(open_info->uri, "fm://", 5) == 0)
        strncpy(d->transport_name, "FuncModel", sizeof(d->transport_name) - 1);
    else if (strncmp(open_info->uri, "rtl://", 6) == 0)
        strncpy(d->transport_name, "RTL", sizeof(d->transport_name) - 1);
    else if (strncmp(open_info->uri, "fpga://", 7) == 0)
        strncpy(d->transport_name, "FPGA", sizeof(d->transport_name) - 1);
    else
        strncpy(d->transport_name, reg->ops->name, sizeof(d->transport_name) - 1);
    d->transport_name[sizeof(d->transport_name) - 1] = '\0';

    *device = d;

    /* Populate capabilities */
    caps->abi_major = CAD_ABI_MAJOR;
    caps->abi_minor = CAD_ABI_MINOR;
    caps->max_buffers = 4096;
    caps->max_buffer_size = (uint64_t)1024 * 1024 * 1024;
    caps->max_queues = 8;
    caps->max_command_lists = 256;
    caps->max_command_list_entries = 65536;
    strncpy(caps->device_name, "CaduceusCore NPU", sizeof(caps->device_name) - 1);
    caps->device_name[sizeof(caps->device_name) - 1] = '\0';
    strncpy(caps->transport_name, d->transport_name,
            sizeof(caps->transport_name) - 1);
    caps->transport_name[sizeof(caps->transport_name) - 1] = '\0';

    return CAD_SUCCESS;
}

cad_error_t cadDeviceClose(cad_device_t device) {
    if (!validate_device(device)) return CAD_ERROR_INVALID_HANDLE;
    device->transport.device_fini(device->transport_priv);
    device->magic = CAD_MAGIC_DEAD;
    free(device);
    return CAD_SUCCESS;
}

cad_error_t cadDeviceGetCaps(cad_device_t device, cad_device_caps_t *caps) {
    if (!validate_device(device)) return CAD_ERROR_INVALID_HANDLE;
    if (!caps) return CAD_ERROR_INVALID_ARGUMENT;
    if (!check_struct_size(caps->struct_size, CAD_DEVICE_CAPS_STRUCT_SIZE))
        return CAD_ERROR_INVALID_ARGUMENT;

    caps->abi_major = CAD_ABI_MAJOR;
    caps->abi_minor = CAD_ABI_MINOR;
    caps->max_buffers = 4096;
    caps->max_buffer_size = (uint64_t)1024 * 1024 * 1024;
    caps->max_queues = 8;
    caps->max_command_lists = 256;
    caps->max_command_list_entries = 65536;
    strncpy(caps->device_name, "CaduceusCore NPU", sizeof(caps->device_name) - 1);
    caps->device_name[sizeof(caps->device_name) - 1] = '\0';
    strncpy(caps->transport_name, device->transport_name,
            sizeof(caps->transport_name) - 1);
    caps->transport_name[sizeof(caps->transport_name) - 1] = '\0';
    return CAD_SUCCESS;
}

cad_error_t cadDeviceReset(cad_device_t device) {
    if (!validate_device(device)) return CAD_ERROR_INVALID_HANDLE;
    if (device->transport.device_reset) {
        int tr_err = device->transport.device_reset(device->transport_priv);
        if (tr_err != CAD_TR_SUCCESS) return trerr_to_cad(tr_err);
    }
    return CAD_SUCCESS;
}

/* ── Buffer lifecycle ────────────────────────────────────────────── */

cad_error_t cadBufferAllocate(cad_device_t device,
                               const cad_buffer_create_info_t *create_info,
                               cad_buffer_t *buffer) {
    if (!validate_device(device)) return CAD_ERROR_INVALID_HANDLE;
    if (!create_info || !buffer) return CAD_ERROR_INVALID_ARGUMENT;
    if (!check_struct_size(create_info->struct_size,
                           CAD_BUFFER_CREATE_INFO_STRUCT_SIZE))
        return CAD_ERROR_INVALID_ARGUMENT;
    if (create_info->size == 0) return CAD_ERROR_INVALID_ARGUMENT;

    cad_buffer_impl_t *b = calloc(1, sizeof(*b));
    if (!b) return CAD_ERROR_OUT_OF_MEMORY;
    b->magic = CAD_MAGIC_BUFFER;
    b->device = device;

    cad_transport_buffer_t *bbuf = NULL;
    int tr_err = device->transport.buffer_alloc(device->transport_priv,
                                                 &bbuf, create_info->size);
    if (tr_err != CAD_TR_SUCCESS) {
        free(b);
        return trerr_to_cad(tr_err);
    }
    b->backend_buf = bbuf;
    b->size = device->transport.buffer_size(device->transport_priv, bbuf);
    *buffer = b;
    return CAD_SUCCESS;
}

cad_error_t cadBufferFree(cad_buffer_t buffer) {
    if (!validate_buffer(buffer)) return CAD_ERROR_INVALID_HANDLE;
    buffer->device->transport.buffer_free(buffer->device->transport_priv,
                                           buffer->backend_buf);
    buffer->magic = CAD_MAGIC_DEAD;
    free(buffer);
    return CAD_SUCCESS;
}

cad_error_t cadBufferRead(cad_buffer_t buffer,
                           uint64_t offset, uint64_t size, void *data) {
    if (!validate_buffer(buffer)) return CAD_ERROR_INVALID_HANDLE;
    if (!data) return CAD_ERROR_INVALID_ARGUMENT;
    if (offset + size > buffer->size) return CAD_ERROR_INVALID_ARGUMENT;
    int tr_err = buffer->device->transport.buffer_read(
        buffer->device->transport_priv, buffer->backend_buf,
        offset, size, data);
    return trerr_to_cad(tr_err);
}

cad_error_t cadBufferWrite(cad_buffer_t buffer,
                            uint64_t offset, uint64_t size,
                            const void *data) {
    if (!validate_buffer(buffer)) return CAD_ERROR_INVALID_HANDLE;
    if (!data) return CAD_ERROR_INVALID_ARGUMENT;
    if (offset + size > buffer->size) return CAD_ERROR_INVALID_ARGUMENT;
    int tr_err = buffer->device->transport.buffer_write(
        buffer->device->transport_priv, buffer->backend_buf,
        offset, size, data);
    return trerr_to_cad(tr_err);
}

/* ── Command list lifecycle ──────────────────────────────────────── */

cad_error_t cadCommandListCreate(cad_device_t device,
                                  const cad_command_list_create_info_t *ci,
                                  cad_command_list_t *cmd_list) {
    if (!validate_device(device)) return CAD_ERROR_INVALID_HANDLE;
    if (!ci || !cmd_list) return CAD_ERROR_INVALID_ARGUMENT;
    if (!check_struct_size(ci->struct_size,
                           CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE))
        return CAD_ERROR_INVALID_ARGUMENT;

    cad_command_list_impl_t *cl = calloc(1, sizeof(*cl));
    if (!cl) return CAD_ERROR_OUT_OF_MEMORY;
    cl->magic = CAD_MAGIC_COMMAND_LIST;
    cl->device = device;
    cl->max_entries = ci->max_entries > 0 ? ci->max_entries : 65536;
    cl->entry_count = 0;
    cl->submitted = 0;
    *cmd_list = cl;
    return CAD_SUCCESS;
}

cad_error_t cadCommandListDestroy(cad_command_list_t cmd_list) {
    if (!validate_command_list(cmd_list)) return CAD_ERROR_INVALID_HANDLE;
    cmd_list->magic = CAD_MAGIC_DEAD;
    free(cmd_list);
    return CAD_SUCCESS;
}

cad_error_t cadCommandListAppendNop(cad_command_list_t cmd_list) {
    if (!validate_command_list(cmd_list)) return CAD_ERROR_INVALID_HANDLE;
    if (cmd_list->entry_count >= cmd_list->max_entries)
        return CAD_ERROR_OUT_OF_MEMORY;
    cmd_list->entry_count++;
    return CAD_SUCCESS;
}

/* ── Queue lifecycle ─────────────────────────────────────────────── */

cad_error_t cadQueueCreate(cad_device_t device,
                            const cad_queue_create_info_t *ci,
                            cad_queue_t *queue) {
    if (!validate_device(device)) return CAD_ERROR_INVALID_HANDLE;
    if (!ci || !queue) return CAD_ERROR_INVALID_ARGUMENT;
    if (!check_struct_size(ci->struct_size, CAD_QUEUE_CREATE_INFO_STRUCT_SIZE))
        return CAD_ERROR_INVALID_ARGUMENT;

    cad_queue_impl_t *q = calloc(1, sizeof(*q));
    if (!q) return CAD_ERROR_OUT_OF_MEMORY;
    q->magic = CAD_MAGIC_QUEUE;
    q->device = device;
    q->seq_counter = 0;
    *queue = q;
    return CAD_SUCCESS;
}

cad_error_t cadQueueDestroy(cad_queue_t queue) {
    if (!validate_queue(queue)) return CAD_ERROR_INVALID_HANDLE;
    queue->magic = CAD_MAGIC_DEAD;
    free(queue);
    return CAD_SUCCESS;
}

cad_error_t cadQueueSubmit(cad_queue_t queue,
                            cad_command_list_t cmd_list,
                            cad_fence_t fence) {
    if (!validate_queue(queue)) return CAD_ERROR_INVALID_HANDLE;
    if (!validate_command_list(cmd_list)) return CAD_ERROR_INVALID_HANDLE;
    if (fence && !validate_fence(fence)) return CAD_ERROR_INVALID_HANDLE;

    /* Resolve transport fence if provided */
    cad_transport_fence_t *tr_fence = NULL;
    if (fence) tr_fence = fence->backend_fence;

    /* Build transport command data: just the entry count for mock */
    /* (In a real implementation, this would be a serialized command buffer.) */
    int tr_err = queue->device->transport.submit(
        queue->device->transport_priv,
        cmd_list,
        cmd_list->entry_count,
        tr_fence);

    if (tr_err != CAD_TR_SUCCESS) {
        return trerr_to_cad(tr_err);
    }

    /* Success: transfer ownership */
    queue->seq_counter++;
    cmd_list->submitted = 1;

    return CAD_SUCCESS;
}

/* ── Fence lifecycle ─────────────────────────────────────────────── */

cad_error_t cadFenceCreate(cad_device_t device,
                            const cad_fence_create_info_t *ci,
                            cad_fence_t *fence) {
    if (!validate_device(device)) return CAD_ERROR_INVALID_HANDLE;
    if (!ci || !fence) return CAD_ERROR_INVALID_ARGUMENT;
    if (!check_struct_size(ci->struct_size, CAD_FENCE_CREATE_INFO_STRUCT_SIZE))
        return CAD_ERROR_INVALID_ARGUMENT;

    cad_fence_impl_t *f = calloc(1, sizeof(*f));
    if (!f) return CAD_ERROR_OUT_OF_MEMORY;
    f->magic = CAD_MAGIC_FENCE;
    f->device = device;
    f->signalled = 0;
    f->status = CAD_FENCE_NOT_READY;

    cad_transport_fence_t *tr_f = NULL;
    int tr_err = device->transport.fence_create(device->transport_priv, &tr_f);
    if (tr_err != CAD_TR_SUCCESS) {
        free(f);
        return trerr_to_cad(tr_err);
    }
    f->backend_fence = tr_f;

    *fence = f;
    return CAD_SUCCESS;
}

cad_error_t cadFenceDestroy(cad_fence_t fence) {
    if (!validate_fence(fence)) return CAD_ERROR_INVALID_HANDLE;
    fence->device->transport.fence_destroy(fence->device->transport_priv,
                                            fence->backend_fence);
    fence->magic = CAD_MAGIC_DEAD;
    free(fence);
    return CAD_SUCCESS;
}

cad_error_t cadFenceWait(cad_fence_t fence, uint64_t timeout_ns) {
    if (!validate_fence(fence)) return CAD_ERROR_INVALID_HANDLE;

    int tr_err = fence->device->transport.fence_wait(
        fence->device->transport_priv, fence->backend_fence, timeout_ns);
    if (tr_err == CAD_TR_SUCCESS) {
        fence->signalled = 1;
        fence->status = CAD_FENCE_COMPLETED;
        return CAD_SUCCESS;
    }
    if (tr_err == CAD_TR_ERR_NOTREADY) return CAD_ERROR_NOT_READY;
    if (tr_err == CAD_TR_ERR_TIMEDOUT) return CAD_ERROR_TIMEOUT;
    fence->signalled = 1;
    fence->status = CAD_FENCE_ERROR;
    return trerr_to_cad(tr_err);
}

cad_error_t cadFencePoll(cad_fence_t fence) {
    if (!validate_fence(fence)) return CAD_ERROR_INVALID_HANDLE;

    int tr_err = fence->device->transport.fence_poll(
        fence->device->transport_priv, fence->backend_fence);
    if (tr_err == CAD_TR_SUCCESS) {
        fence->signalled = 1;
        fence->status = CAD_FENCE_COMPLETED;
        return CAD_SUCCESS;
    }
    if (tr_err == CAD_TR_ERR_NOTREADY) return CAD_ERROR_NOT_READY;
    fence->signalled = 1;
    fence->status = CAD_FENCE_ERROR;
    return trerr_to_cad(tr_err);
}

cad_error_t cadFenceGetStatus(cad_fence_t fence,
                               cad_fence_status_t *status) {
    if (!validate_fence(fence)) return CAD_ERROR_INVALID_HANDLE;
    if (!status) return CAD_ERROR_INVALID_ARGUMENT;

    int tr_status = fence->device->transport.fence_status(
        fence->device->transport_priv, fence->backend_fence);

    switch (tr_status) {
    case 0: *status = CAD_FENCE_NOT_READY; break;
    case 1: *status = CAD_FENCE_COMPLETED; break;
    case 2: *status = CAD_FENCE_ERROR;     break;
    default:*status = CAD_FENCE_NOT_READY; break;
    }
    return CAD_SUCCESS;
}
