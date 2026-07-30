/*
 * test_execute_blob_conformance.c — ExecuteBlob Conformance Test Suite
 *
 * Tests cadCommandListAppendExecuteBlob() against the mock transport.
 * Covers:
 *   1. Happy path: append blob → submit → fence completes
 *   2. NULL buffer returns CAD_ERROR_INVALID_ARGUMENT
 *   3. Exceeding max_entries returns CAD_ERROR_OUT_OF_MEMORY
 *   4. Double-submit blocked: cannot append after submission
 *
 * All tests run against the mock transport with cad_mock_reset()
 * between each test for isolation.
 */

#include "caduceus/runtime.h"
#include "caduceus/transport_mock_test.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>

static int tests_run = 0;
static int tests_passed = 0;
static int tests_failed = 0;

#define CONFORMANCE_TEST(name) \
    static void test_##name(void); \
    static struct { int dummy; } _reg_##name __attribute__((unused)); \
    __attribute__((constructor)) static void _run_##name(void) { \
        tests_run++; \
        cad_mock_reset(); \
        printf("  TEST: %s ... ", #name); \
        test_##name(); \
        tests_passed++; \
        printf("PASS\n"); \
    } \
    static void test_##name(void)

/* ── Helpers ─────────────────────────────────────────────────────── */

static cad_device_t open_mock_device(void) {
    cad_device_open_info_t oi = {0};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    cad_device_t dev = NULL;
    assert(cadDeviceOpen(&oi, &dev, &caps) == CAD_SUCCESS);
    assert(dev != NULL);
    return dev;
}

/* ── 1. Happy path: append blob → submit → fence completes ────────── */

CONFORMANCE_TEST(execute_blob_happy_path) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    /* Allocate a buffer to reference as the blob */
    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 1024;
    cad_buffer_t blob_buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &blob_buf) == CAD_SUCCESS);

    /* Create command list */
    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    /* Mix NOPs with ExecuteBlob entries */
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendExecuteBlob(cl, blob_buf, 0, 512) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendExecuteBlob(cl, blob_buf, 512, 256) == CAD_SUCCESS);

    /* Create queue + fence */
    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);

    /* Submit should succeed */
    assert(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS);

    /* Fence should be signalled immediately (pending_ticks=0) */
    assert(cadFencePoll(fence) == CAD_SUCCESS);
    assert(cadFenceWait(fence, CAD_TIMEOUT_IMMEDIATE) == CAD_SUCCESS);

    cad_fence_status_t status = CAD_FENCE_NOT_READY;
    assert(cadFenceGetStatus(fence, &status) == CAD_SUCCESS);
    assert(status == CAD_FENCE_COMPLETED);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadBufferFree(blob_buf) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 2. NULL buffer returns CAD_ERROR_INVALID_ARGUMENT ─────────────── */

CONFORMANCE_TEST(execute_blob_null_buffer) {
    cad_device_t dev = open_mock_device();

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 8;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    /* NULL buffer with valid offset/size */
    assert(cadCommandListAppendExecuteBlob(cl, NULL, 0, 512)
           == CAD_ERROR_INVALID_ARGUMENT);

    /* NULL buffer with zero offset/size */
    assert(cadCommandListAppendExecuteBlob(cl, NULL, 0, 0)
           == CAD_ERROR_INVALID_ARGUMENT);

    /* entry_count should not increment on failure */
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 3. Exceeding max_entries returns CAD_ERROR_OUT_OF_MEMORY ──────── */

CONFORMANCE_TEST(execute_blob_exceed_max) {
    cad_device_t dev = open_mock_device();

    /* Allocate a buffer for blob references */
    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 256;
    cad_buffer_t blob_buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &blob_buf) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    /* Fill all 4 slots with a mix of NOPs and ExecuteBlobs */
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendExecuteBlob(cl, blob_buf, 0, 64) == CAD_SUCCESS);
    assert(cadCommandListAppendExecuteBlob(cl, blob_buf, 64, 64) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    /* 5th entry should fail */
    assert(cadCommandListAppendExecuteBlob(cl, blob_buf, 128, 64)
           == CAD_ERROR_OUT_OF_MEMORY);

    /* Also check that a NOP also fails after full */
    assert(cadCommandListAppendNop(cl) == CAD_ERROR_OUT_OF_MEMORY);

    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadBufferFree(blob_buf) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 4. Double-submit blocked: cannot append after submission ──────── */

CONFORMANCE_TEST(execute_blob_double_submit_blocked) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 512;
    cad_buffer_t blob_buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &blob_buf) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 8;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    assert(cadCommandListAppendExecuteBlob(cl, blob_buf, 0, 128) == CAD_SUCCESS);

    /* Submit — transfers ownership */
    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);

    assert(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS);

    /* After submission, further appends on the same command list
     * must fail because cl->submitted == 1 */
    assert(cadCommandListAppendExecuteBlob(cl, blob_buf, 0, 64)
           == CAD_ERROR_INVALID_HANDLE);
    assert(cadCommandListAppendNop(cl) == CAD_ERROR_INVALID_HANDLE);

    /* Fence cleanup */
    assert(cadFenceWait(fence, CAD_TIMEOUT_INFINITE) == CAD_SUCCESS);
    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadBufferFree(blob_buf) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── Main ────────────────────────────────────────────────────────── */

int main(void) {
    printf("=== CaduceusCore ExecuteBlob Conformance Tests (C) ===\n");
    printf("ABI version: %d.%d\n\n", CAD_ABI_MAJOR, CAD_ABI_MINOR);
    /* _run_* constructors execute here */
    printf("\n==========================================\n");
    printf("Results: %d/%d passed, %d failed\n",
           tests_passed, tests_run, tests_failed);
    printf("==========================================\n");
    return (tests_passed == tests_run) ? 0 : 1;
}
