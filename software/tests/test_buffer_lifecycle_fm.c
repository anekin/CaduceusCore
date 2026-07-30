/*
 * test_buffer_lifecycle_fm.c — Buffer Lifecycle Edge Case Tests
 *
 * Covers 4 edge cases in buffer lifecycle via the Host Runtime:
 *   1. Use-after-free: cadBufferFree then cadBufferRead → CAD_ERROR_INVALID_HANDLE
 *   2. Offset+size overflow: cadBufferRead(buf, size-1, 2, ...) → CAD_ERROR_INVALID_ARGUMENT
 *   3. Double free: second cadBufferFree after first → CAD_ERROR_INVALID_HANDLE
 *   4. Submit-with-freed-blob: cmd list references freed buffer → submit fails
 *
 * All error paths are caught at the runtime validation layer (magic
 * number check, bounds check) before any transport interaction; the
 * mock:// transport exercises the same code paths as fm://.
 *
 * Usage:
 *   ./test_buffer_lifecycle_fm                  # uses mock://
 *   ./test_buffer_lifecycle_fm mock://           # explicit mock
 *   ./test_buffer_lifecycle_fm fm://unix?path=/tmp/caduceus.sock  # FM transport
 */

#include "caduceus/runtime.h"
#include "caduceus/transport_mock_test.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int g_tests_run = 0;
static int g_tests_passed = 0;
static int g_tests_failed = 0;

#define RUN_TEST(name, result_expr) do { \
    printf("  TEST: %-55s ... ", name); \
    g_tests_run++; \
    if (result_expr) { \
        g_tests_passed++; \
        printf("PASS\n"); \
    } else { \
        g_tests_failed++; \
        printf("FAIL\n"); \
    } \
} while (0)

/* ── Test setup / teardown ─────────────────────────────────────────── */

static cad_device_t open_device(const char *uri) {
    cad_device_open_info_t oi = {0};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = uri;

    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    cad_device_t dev = NULL;
    cad_error_t err = cadDeviceOpen(&oi, &dev, &caps);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "\nFATAL: cadDeviceOpen(%s) -> %s\n",
                uri, cadErrorString(err));
        return NULL;
    }
    return dev;
}

static cad_buffer_t alloc_buffer(cad_device_t dev, uint64_t size) {
    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = size;
    cad_buffer_t buf = NULL;
    if (cadBufferAllocate(dev, &bi, &buf) != CAD_SUCCESS)
        return NULL;
    return buf;
}

/* ── 1. Use-after-free: read on a freed buffer ─────────────────────── */

static int test_use_after_free(const char *uri) {
    cad_device_t dev = open_device(uri);
    if (!dev) return 0;

    cad_buffer_t buf = alloc_buffer(dev, 1024);
    if (!buf) { cadDeviceClose(dev); return 0; }

    /* Free the buffer — magic is now CAD_MAGIC_DEAD */
    assert(cadBufferFree(buf) == CAD_SUCCESS);

    /* Read on freed buffer must return CAD_ERROR_INVALID_HANDLE */
    uint8_t data[16];
    assert(cadBufferRead(buf, 0, 4, data) == CAD_ERROR_INVALID_HANDLE);
    (void)data;

    /* Write on freed buffer must also return CAD_ERROR_INVALID_HANDLE */
    assert(cadBufferWrite(buf, 0, 4, data) == CAD_ERROR_INVALID_HANDLE);
    (void)data;

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
    return 1;
}

/* ── 2. Offset+size overflow ──────────────────────────────────────── */

static int test_offset_overflow(const char *uri) {
    cad_device_t dev = open_device(uri);
    if (!dev) return 0;

    cad_buffer_t buf = alloc_buffer(dev, 256);
    if (!buf) { cadDeviceClose(dev); return 0; }

    uint8_t data[16];

    /* Read with offset+size > buffer_size (offset=255, size=2 → 257 > 256) */
    assert(cadBufferRead(buf, 255, 2, data) == CAD_ERROR_INVALID_ARGUMENT);
    (void)data;

    /* Write with offset+size > buffer_size */
    assert(cadBufferWrite(buf, 254, 3, data) == CAD_ERROR_INVALID_ARGUMENT);

    /* Large overflow: offset = UINT64_MAX */
    assert(cadBufferRead(buf, UINT64_MAX, 1, data) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadBufferWrite(buf, UINT64_MAX, 1, data) == CAD_ERROR_INVALID_ARGUMENT);

    /* Overflow in offset+size itself (100 + (UINT64_MAX - 50) wraps) */
    assert(cadBufferRead(buf, 100, UINT64_MAX - 50, data) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadBufferWrite(buf, 100, UINT64_MAX - 50, data) == CAD_ERROR_INVALID_ARGUMENT);

    /* Exact boundary should still work */
    assert(cadBufferRead(buf, 0, 256, data) == CAD_SUCCESS);
    assert(cadBufferWrite(buf, 0, 256, data) == CAD_SUCCESS);
    assert(cadBufferRead(buf, 255, 1, data) == CAD_SUCCESS);
    assert(cadBufferWrite(buf, 255, 1, data) == CAD_SUCCESS);

    assert(cadBufferFree(buf) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
    return 1;
}

/* ── 3. Double free ────────────────────────────────────────────────── */

static int test_double_free(const char *uri) {
    cad_device_t dev = open_device(uri);
    if (!dev) return 0;

    cad_buffer_t buf = alloc_buffer(dev, 512);
    if (!buf) { cadDeviceClose(dev); return 0; }

    /* First free succeeds */
    assert(cadBufferFree(buf) == CAD_SUCCESS);

    /* Second free must return CAD_ERROR_INVALID_HANDLE (magic is DEAD) */
    assert(cadBufferFree(buf) == CAD_ERROR_INVALID_HANDLE);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
    return 1;
}

/* ── 4. Submit with freed blob referencing a freed buffer ──────────── */

static int test_submit_with_freed_blob(const char *uri) {
    cad_device_t dev = open_device(uri);
    if (!dev) return 0;

    /* Allocate a buffer and write blob payload into it */
    cad_buffer_t blob_buf = alloc_buffer(dev, 128);
    if (!blob_buf) { cadDeviceClose(dev); return 0; }

    /* Create command list with an ExecuteBlob referencing this buffer */
    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 8;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
    (void)ci; (void)cl;

    assert(cadCommandListAppendExecuteBlob(cl, blob_buf, 0, 64) == CAD_SUCCESS);

    /* Free the blob buffer — command list still references it */
    assert(cadBufferFree(blob_buf) == CAD_SUCCESS);

    /* Create queue and fence */
    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);
    (void)qi; (void)queue;

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);
    (void)fi; (void)fence;

    /* Submit must detect freed blob buffer via validate_buffer() */
    assert(cadQueueSubmit(queue, cl, fence) == CAD_ERROR_INVALID_HANDLE);

    /* On submit failure, caller retains ownership of cmd_list and fence */
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
    return 1;
}

/* ── Main ──────────────────────────────────────────────────────────── */

int main(int argc, char *argv[]) {
    const char *uri = (argc >= 2) ? argv[1] : "mock://";

    /* Reset mock state before any tests run (no-op for non-mock URIs) */
    cad_mock_reset();

    printf("=== CaduceusCore Buffer Lifecycle Edge Case Tests ===\n");
    printf("URI: %s\n", uri);
    printf("ABI version: %d.%d\n\n", CAD_ABI_MAJOR, CAD_ABI_MINOR);

    RUN_TEST("use_after_free",         test_use_after_free(uri));
    cad_mock_reset();

    RUN_TEST("offset_overflow",         test_offset_overflow(uri));
    cad_mock_reset();

    RUN_TEST("double_free",             test_double_free(uri));
    cad_mock_reset();

    RUN_TEST("submit_with_freed_blob",  test_submit_with_freed_blob(uri));
    cad_mock_reset();

    printf("\n==========================================\n");
    printf("Results: %d/%d passed, %d failed\n",
           g_tests_passed, g_tests_run, g_tests_failed);
    printf("==========================================\n");

    return (g_tests_passed == g_tests_run) ? 0 : 1;
}
