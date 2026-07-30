/*
 * CaduceusCore Host Runtime — Stable C ABI
 *
 * Design follows Vulkan/CUDA/OpenCL host API conventions:
 *   - Opaque handles (cad_device_t, cad_buffer_t, …)
 *   - Typed versioned structs with struct_size + abi_major/abi_minor
 *   - Explicit create/destroy lifecycle, no implicit allocation
 *   - URI-based device selection (fm://, rtl://, mock://; fpga:// is reserved
 *     for a future Linux userspace FPGA transport but not yet available)
 *   - Extension query pattern via capability structs
 *
 * ABI Version: Major 1, Minor 0
 */

#ifndef CADUCEUS_RUNTIME_H
#define CADUCEUS_RUNTIME_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── ABI version constants ───────────────────────────────────────── */

#define CAD_ABI_MAJOR 1
#define CAD_ABI_MINOR 0

/* ── Timeout sentinels ───────────────────────────────────────────── */

#define CAD_TIMEOUT_IMMEDIATE ((uint64_t)0)
#define CAD_TIMEOUT_INFINITE  ((uint64_t)(-1))

/* ── Error codes ─────────────────────────────────────────────────── */

typedef enum cad_error_t {
    CAD_SUCCESS              = 0,
    CAD_ERROR_INCOMPATIBLE_ABI = 1,
    CAD_ERROR_INVALID_HANDLE   = 2,
    CAD_ERROR_INVALID_ARGUMENT = 3,
    CAD_ERROR_TIMEOUT          = 4,
    CAD_ERROR_DEVICE_LOST      = 5,
    CAD_ERROR_OUT_OF_MEMORY    = 6,
    CAD_ERROR_NOT_READY        = 7,
    CAD_ERROR_DEVICE_BUSY      = 8,
    CAD_ERROR_UNSUPPORTED      = 9,
} cad_error_t;

/* ── Fence status ────────────────────────────────────────────────── */

typedef enum cad_fence_status_t {
    CAD_FENCE_NOT_READY = 0,
    CAD_FENCE_COMPLETED = 1,
    CAD_FENCE_ERROR     = 2,
} cad_fence_status_t;

/* ── Opaque handles (pointer-to-incomplete-type pattern) ──────────── */

typedef struct cad_device_impl_t        *cad_device_t;
typedef struct cad_buffer_impl_t        *cad_buffer_t;
typedef struct cad_queue_impl_t         *cad_queue_t;
typedef struct cad_command_list_impl_t  *cad_command_list_t;
typedef struct cad_fence_impl_t         *cad_fence_t;

/*
 * ABI rule: the caller must zero-initialise every struct before setting
 * struct_size.  The runtime may reject a struct whose struct_size is smaller
 * than the minimum it requires, returning CAD_ERROR_INVALID_ARGUMENT.
 *
 * New fields are added at the end so a newer client compiled against a larger
 * struct can still talk to an older runtime as long as the runtime sees a
 * struct_size it understands.
 */

/* ── Device open ─────────────────────────────────────────────────── */

#define CAD_DEVICE_OPEN_INFO_STRUCT_SIZE (sizeof(cad_device_open_info_t))

typedef struct cad_device_open_info_t {
    uint32_t    struct_size;
    uint32_t    abi_major;           /* client-requested major version */
    uint32_t    abi_minor;           /* client-requested minor version */
    const char *uri;                 /* "fm://", "rtl://", "mock://";
                                      * "fpga://" is reserved and returns
                                      * CAD_ERROR_UNSUPPORTED. */
} cad_device_open_info_t;

/* ── Device capabilities ─────────────────────────────────────────── */

#define CAD_DEVICE_CAPS_STRUCT_SIZE (sizeof(cad_device_caps_t))

typedef struct cad_device_caps_t {
    uint32_t struct_size;
    uint32_t abi_major;              /* runtime-supported major version */
    uint32_t abi_minor;              /* runtime-supported minor version */
    uint32_t max_buffers;            /* maximum concurrently allocated buffers */
    uint64_t max_buffer_size;        /* maximum size of a single buffer, in bytes */
    uint32_t max_queues;             /* maximum concurrently created queues */
    uint32_t max_command_lists;      /* maximum concurrently created command lists */
    uint32_t max_command_list_entries; /* maximum entries per command list */
    char     device_name[64];        /* human-readable device name */
    char     transport_name[32];     /* "FuncModel", "RTL", "FPGA", "Mock" */
} cad_device_caps_t;

/* ── Buffer create info ──────────────────────────────────────────── */

#define CAD_BUFFER_CREATE_INFO_STRUCT_SIZE (sizeof(cad_buffer_create_info_t))

typedef struct cad_buffer_create_info_t {
    uint32_t struct_size;
    uint64_t size;                   /* requested size in bytes */
    uint32_t flags;                  /* reserved, must be 0 */
} cad_buffer_create_info_t;

/* ── Command list create info ────────────────────────────────────── */

#define CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE \
    (sizeof(cad_command_list_create_info_t))

typedef struct cad_command_list_create_info_t {
    uint32_t struct_size;
    uint32_t max_entries;            /* 0 = use device default */
    uint32_t flags;                  /* reserved, must be 0 */
} cad_command_list_create_info_t;

/* ── Fence create info ───────────────────────────────────────────── */

#define CAD_FENCE_CREATE_INFO_STRUCT_SIZE (sizeof(cad_fence_create_info_t))

typedef struct cad_fence_create_info_t {
    uint32_t struct_size;
    uint32_t flags;                  /* reserved, must be 0 */
} cad_fence_create_info_t;

/* ── Queue create info ───────────────────────────────────────────── */

#define CAD_QUEUE_CREATE_INFO_STRUCT_SIZE (sizeof(cad_queue_create_info_t))

typedef struct cad_queue_create_info_t {
    uint32_t struct_size;
    uint32_t flags;                  /* reserved, must be 0 */
} cad_queue_create_info_t;

/* ── Device lifecycle ────────────────────────────────────────────── */

/*
 * Open a device at the given URI.
 *
 *  open_info  [in]  must have struct_size and uri set.
 *                   abi_major/abi_minor specify the ABI the client expects.
 *  device    [out]  receives the opaque device handle.
 *  caps      [out]  receives populated capabilities, including the actual
 *                   ABI version the runtime supports.
 *
 * Returns:
 *   CAD_SUCCESS               – device opened, caps populated.
 *   CAD_ERROR_INCOMPATIBLE_ABI – major version mismatch, or client minor
 *                                is higher than runtime minor.
 *   CAD_ERROR_INVALID_ARGUMENT – struct_size too small, NULL uri, or
 *                                unsupported URI scheme.
 *   CAD_ERROR_OUT_OF_MEMORY    – allocation failed.
 */
cad_error_t cadDeviceOpen(const cad_device_open_info_t *open_info,
                           cad_device_t *device,
                           cad_device_caps_t *caps);

/* Close the device and free all associated resources.  Any remaining
 * buffers, queues, command lists, and fences must have been individually
 * freed before this call. */
cad_error_t cadDeviceClose(cad_device_t device);

/* Re-query device capabilities (same struct returned by cadDeviceOpen). */
cad_error_t cadDeviceGetCaps(cad_device_t device,
                              cad_device_caps_t *caps);

/* Reset the device — abort all in-flight work, return all fences to
 * error state, reclaim any pending resources.  The device handle remains
 * valid after reset. */
cad_error_t cadDeviceReset(cad_device_t device);

/* ── Buffer lifecycle ────────────────────────────────────────────── */

/* Allocate a device-side buffer.  The buffer is owned by the device and
 * freed via cadBufferFree. */
cad_error_t cadBufferAllocate(cad_device_t device,
                               const cad_buffer_create_info_t *create_info,
                               cad_buffer_t *buffer);

/* Free a device-side buffer.  The buffer must not be in use by any
 * in-flight command list. */
cad_error_t cadBufferFree(cad_buffer_t buffer);

/* Read data from a device buffer into host memory.  offset + size must
 * not exceed the allocated buffer size. */
cad_error_t cadBufferRead(cad_buffer_t buffer,
                           uint64_t offset,
                           uint64_t size,
                           void *data);

/* Write data from host memory into a device buffer.  offset + size must
 * not exceed the allocated buffer size. */
cad_error_t cadBufferWrite(cad_buffer_t buffer,
                            uint64_t offset,
                            uint64_t size,
                            const void *data);

/*
 * Return the device-visible physical address of a buffer.
 *
 * For `fm://` devices, the address is the DRAM window address assigned
 * by the device server (starting at 0x80100000).  This address can be
 * passed as the host_addr parameter to cad_buffer_declare().
 *
 * Returns:
 *   CAD_SUCCESS             – addr populated with device physical address
 *   CAD_ERROR_INVALID_HANDLE – buffer is NULL, invalid (freed), or mock
 *   CAD_ERROR_INVALID_ARGUMENT – addr is NULL
 *   CAD_ERROR_UNSUPPORTED   – transport does not expose device addresses
 *                             (e.g. mock, fpga, or a transport with no
 *                             DRAM window mapping)
 */
cad_error_t cadBufferGetDeviceAddress(cad_buffer_t buffer,
                                       uint64_t *addr);

/* ── Command list lifecycle ──────────────────────────────────────── */

/* Create a command list.  Command lists are recording-only; they do not
 * execute until submitted to a queue. */
cad_error_t cadCommandListCreate(cad_device_t device,
                                  const cad_command_list_create_info_t *create_info,
                                  cad_command_list_t *cmd_list);

/* Destroy a command list.  The command list must not be in-flight.
 * If it was submitted to a queue, the fence associated with the
 * submission must have signalled completion first. */
cad_error_t cadCommandListDestroy(cad_command_list_t cmd_list);

/* Append a no-op marker to the command list (for testing fence
 * signalling without hardware work). */
cad_error_t cadCommandListAppendNop(cad_command_list_t cmd_list);

/* Append an opaque ExecuteBlob entry to the command list.
 *
 * The runtime records a reference to (blob_buffer, blob_offset, blob_size)
 * without interpreting the blob contents.  The blob is consumed by the
 * firmware at submission time.
 *
 * Returns:
 *   CAD_SUCCESS               – entry recorded
 *   CAD_ERROR_INVALID_HANDLE  – cmd_list is NULL, invalid, or already submitted
 *   CAD_ERROR_INVALID_ARGUMENT – blob_buffer is NULL
 *   CAD_ERROR_OUT_OF_MEMORY   – command list is full (entry_count >= max_entries) */
cad_error_t cadCommandListAppendExecuteBlob(cad_command_list_t cmd_list,
                                            cad_buffer_t blob_buffer,
                                            uint64_t blob_offset,
                                            uint64_t blob_size);

/* ── Queue lifecycle ─────────────────────────────────────────────── */

cad_error_t cadQueueCreate(cad_device_t device,
                            const cad_queue_create_info_t *create_info,
                            cad_queue_t *queue);

cad_error_t cadQueueDestroy(cad_queue_t queue);

/* Submit a command list to a queue.  The runtime takes ownership of
 * cmd_list — the caller must not use or destroy it after a successful
 * submission.  On failure the caller retains ownership.
 *
 * If fence is non-NULL, it is signalled when the command list completes
 * (or on error). */
cad_error_t cadQueueSubmit(cad_queue_t queue,
                            cad_command_list_t cmd_list,
                            cad_fence_t fence);

/* ── Fence lifecycle ─────────────────────────────────────────────── */

cad_error_t cadFenceCreate(cad_device_t device,
                            const cad_fence_create_info_t *create_info,
                            cad_fence_t *fence);

cad_error_t cadFenceDestroy(cad_fence_t fence);

/*
 * Block until the fence is signalled or timeout expires.
 *
 * timeout_ns:
 *   CAD_TIMEOUT_IMMEDIATE  — return immediately (0)
 *   CAD_TIMEOUT_INFINITE   — block indefinitely (UINT64_MAX)
 *
 * Returns:
 *   CAD_SUCCESS          – fence signalled (completed or error).
 *   CAD_ERROR_TIMEOUT    – timeout expired before signal.
 */
cad_error_t cadFenceWait(cad_fence_t fence, uint64_t timeout_ns);

/*
 * Non-blocking check.  Equivalent to cadFenceWait(fence, CAD_TIMEOUT_IMMEDIATE).
 * Returns CAD_SUCCESS if signalled, CAD_ERROR_NOT_READY otherwise.
 */
cad_error_t cadFencePoll(cad_fence_t fence);

/* Query the fence's current status. */
cad_error_t cadFenceGetStatus(cad_fence_t fence,
                               cad_fence_status_t *status);

/* ── Execution statistics ────────────────────────────────────────── */

#define CAD_EXECUTION_STATS_STRUCT_SIZE (sizeof(cad_execution_stats_t))

typedef struct cad_execution_stats_t {
    uint32_t struct_size;        /* must be set to CAD_EXECUTION_STATS_STRUCT_SIZE */
    uint32_t mmul_ops;           /* number of MMUL operations executed */
    uint32_t sfu_ops;            /* number of SFU operations executed */
    uint32_t vector_ops;         /* number of Vector operations executed */
    uint32_t dma_ops;            /* number of DMA operations executed */
    uint64_t dma_bytes_read;     /* total bytes read by DMA */
    uint64_t dma_bytes_written;  /* total bytes written by DMA */
} cad_execution_stats_t;

/*
 * Retrieve execution statistics for a fence.
 *
 * Stats are populated by the transport during submit response processing
 * and reflect the actual commands submitted (not hardcoded constants).
 *
 * Returns:
 *   CAD_SUCCESS              – stats populated
 *   CAD_ERROR_NOT_READY       – fence is valid but no stats available
 *                               (e.g., NOP-only submit, mock transport)
 *   CAD_ERROR_INVALID_HANDLE  – fence is NULL, invalid, or freed
 *   CAD_ERROR_INVALID_ARGUMENT – stats ptr is NULL or struct_size too small
 */
cad_error_t cadFenceGetExecutionStats(cad_fence_t fence,
                                       cad_execution_stats_t *stats);

/* ── Error information ───────────────────────────────────────────── */

/* Return a human-readable string for an error code.  The returned pointer
 * is valid for the lifetime of the process. */
const char *cadErrorString(cad_error_t error);

#ifdef __cplusplus
}
#endif

#endif /* CADUCEUS_RUNTIME_H */
