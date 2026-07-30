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

#include <stdio.h>
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

/* Resolve a URI to transport ops. */
static const cad_transport_reg_t *find_transport(const char *uri) {
    if (!uri) return NULL;
    for (size_t i = 0; i < transport_registry_count; i++) {
        size_t slen = strlen(transport_registry[i].scheme);
        if (strncmp(uri, transport_registry[i].scheme, slen) == 0) {
            return &transport_registry[i];
        }
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
    case CAD_ERROR_UNSUPPORTED:     return "fpga:// transport not yet implemented — no FPGA platform available";
    default:                        return "Unknown error";
    }
}

const char *cadDeviceErrorString(cad_device_t device, cad_error_t error,
                                  char *buf, size_t len) {
    if (!buf || len == 0) return "";
    if (!validate_device(device)) {
        strncpy(buf, cadErrorString(error), len - 1);
        buf[len - 1] = '\0';
        return buf;
    }

    if (device->transport.transportErrorToString) {
        return device->transport.transportErrorToString(
            device->transport_priv, error, buf, len);
    }

    strncpy(buf, cadErrorString(error), len - 1);
    buf[len - 1] = '\0';
    return buf;
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

    /* fpga:// is reserved for a future Linux userspace FPGA transport but is
     * not yet implemented; there is no FPGA platform available in this build. */
    if (strncmp(open_info->uri, "fpga://", 7) == 0) {
        return CAD_ERROR_UNSUPPORTED;
    }

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
        /* Emit a transport-specific diagnostic before returning the
         * generic cad_error_t.  This makes "FM transport" visible in
         * broken-socket tests without changing the public ABI. */
        if (reg->ops->transportErrorToString) {
            char ebuf[256];
            cad_error_t ce = trerr_to_cad(tr_err);
            reg->ops->transportErrorToString(NULL, ce, ebuf, sizeof(ebuf));
            CAD_LOG(CAD_LOG_ERROR, "cadDeviceOpen: %s", ebuf);
        }
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

    CAD_LOG(CAD_LOG_DEBUG, "opened device %s (transport %s)",
            caps->device_name, caps->transport_name);
    return CAD_SUCCESS;
}

cad_error_t cadDeviceClose(cad_device_t device) {
    if (!validate_device(device)) return CAD_ERROR_INVALID_HANDLE;
    CAD_LOG(CAD_LOG_TRACE, "closing device");
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
    CAD_LOG(CAD_LOG_TRACE, "buffer allocated size=%llu",
            (unsigned long long)create_info->size);
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
    if (offset > (uint64_t)SIZE_MAX - size) return CAD_ERROR_INVALID_ARGUMENT;
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
    if (offset > (uint64_t)SIZE_MAX - size) return CAD_ERROR_INVALID_ARGUMENT;
    if (offset + size > buffer->size) return CAD_ERROR_INVALID_ARGUMENT;
    int tr_err = buffer->device->transport.buffer_write(
        buffer->device->transport_priv, buffer->backend_buf,
        offset, size, data);
    return trerr_to_cad(tr_err);
}

cad_error_t cadBufferGetDeviceAddress(cad_buffer_t buffer, uint64_t *addr) {
    if (!validate_buffer(buffer)) return CAD_ERROR_INVALID_HANDLE;
    if (!addr) return CAD_ERROR_INVALID_ARGUMENT;

    /* Only FuncModel transport maps buffers into a device-visible DRAM window. */
    if (strncmp(buffer->device->transport_name, "FuncModel", 9) != 0) {
        return CAD_ERROR_UNSUPPORTED;
    }

    /* FM transport stores the device-physical address as the buffer handle
     * (the value returned by the server's buffer allocator). */
    *addr = *(uint64_t *)buffer->backend_buf;
    return CAD_SUCCESS;
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

    uint32_t max = ci->max_entries > 0 ? ci->max_entries : 65536;

    cad_command_list_impl_t *cl = calloc(1, sizeof(*cl));
    if (!cl) return CAD_ERROR_OUT_OF_MEMORY;
    cl->blob_entries = calloc(max, sizeof(*cl->blob_entries));
    if (!cl->blob_entries) {
        free(cl);
        return CAD_ERROR_OUT_OF_MEMORY;
    }
    cl->magic = CAD_MAGIC_COMMAND_LIST;
    cl->device = device;
    cl->max_entries = max;
    cl->entry_count = 0;
    cl->submitted = 0;
    *cmd_list = cl;
    return CAD_SUCCESS;
}

cad_error_t cadCommandListDestroy(cad_command_list_t cmd_list) {
    if (!validate_command_list(cmd_list)) return CAD_ERROR_INVALID_HANDLE;
    cmd_list->magic = CAD_MAGIC_DEAD;
    free(cmd_list->blob_entries);
    free(cmd_list);
    return CAD_SUCCESS;
}

cad_error_t cadCommandListAppendNop(cad_command_list_t cmd_list) {
    if (!validate_command_list(cmd_list)) return CAD_ERROR_INVALID_HANDLE;
    if (cmd_list->submitted) return CAD_ERROR_INVALID_HANDLE;
    if (cmd_list->entry_count >= cmd_list->max_entries)
        return CAD_ERROR_OUT_OF_MEMORY;
    cmd_list->entry_count++;
    return CAD_SUCCESS;
}

cad_error_t cadCommandListAppendExecuteBlob(cad_command_list_t cmd_list,
                                            cad_buffer_t blob_buffer,
                                            uint64_t blob_offset,
                                            uint64_t blob_size) {
    if (!validate_command_list(cmd_list)) return CAD_ERROR_INVALID_HANDLE;
    if (cmd_list->submitted) return CAD_ERROR_INVALID_HANDLE;
    if (!blob_buffer) return CAD_ERROR_INVALID_ARGUMENT;
    if (cmd_list->entry_count >= cmd_list->max_entries)
        return CAD_ERROR_OUT_OF_MEMORY;
    cad_blob_entry_t *entry = &cmd_list->blob_entries[cmd_list->entry_count];
    entry->blob_buf = blob_buffer;
    entry->offset = blob_offset;
    entry->size = blob_size;
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
    if (cmd_list->submitted) return CAD_ERROR_INVALID_HANDLE;
    if (fence && !validate_fence(fence)) return CAD_ERROR_INVALID_HANDLE;

    /* Resolve transport fence if provided */
    cad_transport_fence_t *tr_fence = NULL;
    if (fence) tr_fence = fence->backend_fence;

    uint32_t nop_count = 0;
    uint32_t blob_count = 0;
    uint64_t total_blob_bytes = 0;

    for (uint32_t i = 0; i < cmd_list->entry_count; i++) {
        cad_blob_entry_t *e = &cmd_list->blob_entries[i];
        if (e->blob_buf == NULL) {
            nop_count++;
        } else {
            if (!validate_buffer(e->blob_buf)) {
                return CAD_ERROR_INVALID_HANDLE;
            }
            blob_count++;
            total_blob_bytes += e->size;
        }
    }

    CAD_LOG(CAD_LOG_TRACE,
            "submit entries=%u nop=%u blob=%u total_blob_bytes=%llu",
            cmd_list->entry_count, nop_count, blob_count,
            (unsigned long long)total_blob_bytes);

    if (total_blob_bytes > (uint64_t)(UINT32_MAX - 12)) {
        return CAD_ERROR_OUT_OF_MEMORY;
    }
    uint32_t ser_size = (uint32_t)(12 + total_blob_bytes);
    uint8_t *ser = malloc(ser_size);
    if (!ser) return CAD_ERROR_OUT_OF_MEMORY;

    /* Serialized format: nop_count | blob_count | total_cmd_count | raw blobs */
    uint32_t *hdr = (uint32_t *)ser;
    hdr[0] = nop_count;
    hdr[1] = blob_count;
    hdr[2] = cmd_list->entry_count;

    uint8_t *blob_dst = ser + 12;
    for (uint32_t i = 0; i < cmd_list->entry_count; i++) {
        cad_blob_entry_t *e = &cmd_list->blob_entries[i];
        if (e->blob_buf != NULL) {
            cad_error_t rerr = cadBufferRead(e->blob_buf, e->offset,
                                              e->size, blob_dst);
            (void)rerr;
            blob_dst += e->size;
        }
    }

    int tr_err = queue->device->transport.submit(
        queue->device->transport_priv,
        ser,
        ser_size,
        tr_fence);

    free(ser);  /* always freed — no leak on success or failure */

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

cad_error_t cadFenceGetExecutionStats(cad_fence_t fence,
                                       cad_execution_stats_t *stats) {
    if (!validate_fence(fence)) return CAD_ERROR_INVALID_HANDLE;
    if (!stats) return CAD_ERROR_INVALID_ARGUMENT;
    if (!check_struct_size(stats->struct_size,
                           CAD_EXECUTION_STATS_STRUCT_SIZE))
        return CAD_ERROR_INVALID_ARGUMENT;

    int (*fn)(void *, cad_transport_fence_t *,
              uint32_t *, uint32_t *, uint32_t *, uint32_t *,
              uint64_t *, uint64_t *) =
        fence->device->transport.fence_get_exec_stats;

    if (!fn) {
        return CAD_ERROR_NOT_READY;
    }

    int err = fn(fence->device->transport_priv,
                 fence->backend_fence,
                 &stats->mmul_ops,
                 &stats->sfu_ops,
                 &stats->vector_ops,
                 &stats->dma_ops,
                 &stats->dma_bytes_read,
                 &stats->dma_bytes_written);
    if (err != 0) {
        return CAD_ERROR_NOT_READY;
    }
    return CAD_SUCCESS;
}
