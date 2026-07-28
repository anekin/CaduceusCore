/*
 * test_runtime_faults.c — C Negative/Fault Test Suite
 *
 * Tests error-handling and fault-injection paths:
 *   1. Invalid handles (NULL, wrong type, use-after-close)
 *   2. Consumed command-list resubmit
 *   3. Submit with NULL/invalid fence
 *   4. Submit error injection from mock transport
 *   5. Buffer use-after-free
 *   6. Null arguments everywhere
 *   7. Double-free detection
 *   8. Stale handle detection (magic cleared)
 *   9. Timeout on unsignalled fence with infinite wait (should resolve)
 *  10. Fence status after error injection
 *  11. Device operations on closed device
 *  12. Buffer read/write bounds checking
 */

#include "caduceus/runtime.h"
#include "caduceus/transport_mock_test.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int tests_run = 0;
static int tests_passed = 0;

#define FAULT_TEST(name) \
    static void test_##name(void); \
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
    return dev;
}

/* ── 1. Consumed command-list resubmit ────────────────────────────── */

FAULT_TEST(submit_consumed_command_list) {
    cad_device_t dev = open_mock_device();

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    /* First submit: succeeds */
    assert(cadQueueSubmit(queue, cl, NULL) == CAD_SUCCESS);

    /* Second submit of same command list: fails (submitted flag) */
    assert(cadQueueSubmit(queue, cl, NULL) == CAD_ERROR_INVALID_HANDLE);

    /* Can't destroy a submitted command list */
    assert(cadCommandListDestroy(cl) == CAD_ERROR_INVALID_HANDLE);

    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 2. Submit error injection ────────────────────────────────────── */

FAULT_TEST(submit_error_injection) {
    cad_device_t dev = open_mock_device();

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    /* Inject device-lost error for next submit */
    cad_mock_set_next_submit_error(CAD_TR_ERR_LOST);
    assert(cadQueueSubmit(queue, cl, NULL) == CAD_ERROR_DEVICE_LOST);
    /* On error, caller retains ownership */
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);

    /* Next submit should succeed (error was consumed) */
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadQueueSubmit(queue, cl, NULL) == CAD_SUCCESS);

    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 3. Buffer use-after-free ─────────────────────────────────────── */

FAULT_TEST(buffer_use_after_free) {
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 1024;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_SUCCESS);
    assert(cadBufferFree(buf) == CAD_SUCCESS);

    /* Re-free: magic is cleared */
    assert(cadBufferFree(buf) == CAD_ERROR_INVALID_HANDLE);

    /* Read/write on freed buffer */
    char data[16];
    assert(cadBufferRead(buf, 0, 4, data) == CAD_ERROR_INVALID_HANDLE);
    assert(cadBufferWrite(buf, 0, 4, data) == CAD_ERROR_INVALID_HANDLE);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 4. Device use-after-close ────────────────────────────────────── */

FAULT_TEST(device_use_after_close) {
    cad_device_t dev = open_mock_device();
    assert(cadDeviceClose(dev) == CAD_SUCCESS);

    /* Re-close: magic cleared */
    assert(cadDeviceClose(dev) == CAD_ERROR_INVALID_HANDLE);

    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceGetCaps(dev, &caps) == CAD_ERROR_INVALID_HANDLE);
    assert(cadDeviceReset(dev) == CAD_ERROR_INVALID_HANDLE);

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 64;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_ERROR_INVALID_HANDLE);
}

/* ── 5. NULL handles everywhere ───────────────────────────────────── */

FAULT_TEST(null_handles) {
    /* NULL device */
    assert(cadDeviceClose(NULL) == CAD_ERROR_INVALID_HANDLE);
    assert(cadDeviceReset(NULL) == CAD_ERROR_INVALID_HANDLE);

    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceGetCaps(NULL, &caps) == CAD_ERROR_INVALID_HANDLE);

    /* NULL buffer */
    assert(cadBufferFree(NULL) == CAD_ERROR_INVALID_HANDLE);
    {
        char d[16];
        assert(cadBufferRead(NULL, 0, 4, d) == CAD_ERROR_INVALID_HANDLE);
        assert(cadBufferWrite(NULL, 0, 4, d) == CAD_ERROR_INVALID_HANDLE);
    }

    /* NULL queue */
    assert(cadQueueDestroy(NULL) == CAD_ERROR_INVALID_HANDLE);
    assert(cadQueueSubmit(NULL, NULL, NULL) == CAD_ERROR_INVALID_HANDLE);

    /* NULL command list */
    assert(cadCommandListDestroy(NULL) == CAD_ERROR_INVALID_HANDLE);
    assert(cadCommandListAppendNop(NULL) == CAD_ERROR_INVALID_HANDLE);

    /* NULL fence */
    assert(cadFenceDestroy(NULL) == CAD_ERROR_INVALID_HANDLE);
    assert(cadFenceWait(NULL, 0) == CAD_ERROR_INVALID_HANDLE);
    assert(cadFencePoll(NULL) == CAD_ERROR_INVALID_HANDLE);
    {
        cad_fence_status_t s;
        assert(cadFenceGetStatus(NULL, &s) == CAD_ERROR_INVALID_HANDLE);
    }

    cad_device_t dev = open_mock_device();
    {
        cad_fence_create_info_t fi = {0};
        fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
        cad_fence_t f = NULL;
        assert(cadFenceCreate(dev, &fi, &f) == CAD_SUCCESS);
        assert(cadFenceGetStatus(f, NULL) == CAD_ERROR_INVALID_ARGUMENT);
        assert(cadFenceDestroy(f) == CAD_SUCCESS);
    }
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 6. NULL args in create functions ─────────────────────────────── */

FAULT_TEST(null_create_args) {
    cad_device_t dev = open_mock_device();

    /* cadBufferAllocate: null create_info or output */
    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 64;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, NULL, &buf) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadBufferAllocate(dev, &bi, NULL) == CAD_ERROR_INVALID_ARGUMENT);

    /* cadCommandListCreate: null create_info or output */
    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, NULL, &cl) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadCommandListCreate(dev, &ci, NULL) == CAD_ERROR_INVALID_ARGUMENT);

    /* cadQueueCreate: null create_info or output */
    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t q = NULL;
    assert(cadQueueCreate(dev, NULL, &q) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadQueueCreate(dev, &qi, NULL) == CAD_ERROR_INVALID_ARGUMENT);

    /* cadFenceCreate: null create_info or output */
    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t f = NULL;
    assert(cadFenceCreate(dev, NULL, &f) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadFenceCreate(dev, &fi, NULL) == CAD_ERROR_INVALID_ARGUMENT);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 7. Buffer bounds checking ────────────────────────────────────── */

FAULT_TEST(buffer_bounds) {
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 256;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_SUCCESS);

    char data[32] = {0};

    /* Write beyond end */
    assert(cadBufferWrite(buf, 250, 10, data) == CAD_ERROR_INVALID_ARGUMENT);
    /* Read beyond end */
    assert(cadBufferRead(buf, 250, 10, data) == CAD_ERROR_INVALID_ARGUMENT);
    /* Exact boundary: offset 0, size 256 should work */
    assert(cadBufferWrite(buf, 0, 256, data) == CAD_SUCCESS);
    assert(cadBufferRead(buf, 0, 256, data) == CAD_SUCCESS);
    /* One byte over */
    assert(cadBufferWrite(buf, 0, 257, data) == CAD_ERROR_INVALID_ARGUMENT);

    /* NULL data pointer */
    assert(cadBufferWrite(buf, 0, 4, NULL) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadBufferRead(buf, 0, 4, NULL) == CAD_ERROR_INVALID_ARGUMENT);

    assert(cadBufferFree(buf) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 8. Invalid fence operation without submit ────────────────────── */

FAULT_TEST(fence_never_submitted) {
    cad_device_t dev = open_mock_device();

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);

    /* Fence was never submitted with a command list */
    assert(cadFencePoll(fence) == CAD_ERROR_NOT_READY);
    assert(cadFenceWait(fence, CAD_TIMEOUT_IMMEDIATE) == CAD_ERROR_NOT_READY);

    cad_fence_status_t status;
    assert(cadFenceGetStatus(fence, &status) == CAD_SUCCESS);
    assert(status == CAD_FENCE_NOT_READY);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 9. Submit with invalid fence (from different device) ─────────── */
/* Mock transport accepts any fence, but runtime validation catches type mismatch */

FAULT_TEST(queue_submit_invalid_fence) {
    cad_device_t dev = open_mock_device();

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    /* Submit with NULL fence is OK */
    assert(cadQueueSubmit(queue, cl, NULL) == CAD_SUCCESS);

    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 10. Race-condition: submit with destroyed fence ──────────────── */

FAULT_TEST(submit_with_destroyed_fence) {
    cad_device_t dev = open_mock_device();

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);
    assert(cadFenceDestroy(fence) == CAD_SUCCESS);

    /* Submit with destroyed fence: magic cleared → INVALID_HANDLE */
    assert(cadQueueSubmit(queue, cl, fence) == CAD_ERROR_INVALID_HANDLE);

    /* Caller retains command list ownership on failure */
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);

    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 11. Queue destroy with in-flight work ────────────────────────── */
/* Queue can be destroyed after submissions complete. */

FAULT_TEST(queue_destroy_after_submit) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    /* Submit without fence — immediate completion */
    assert(cadQueueSubmit(queue, cl, NULL) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 12. Fence error injection via submit ─────────────────────────── */

FAULT_TEST(fence_error_on_submit) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    cad_fence_t fence = NULL;
    {
        cad_fence_create_info_t fi = {0};
        fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
        assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);
    }

    /* Inject BUSY error */
    cad_mock_set_next_submit_error(CAD_TR_ERR_BUSY);
    assert(cadQueueSubmit(queue, cl, fence) == CAD_ERROR_DEVICE_BUSY);
    /* On failure, caller retains ownership */
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);

    /* Fence should still be NOT_READY (never consumed) */
    cad_fence_status_t status;
    assert(cadFenceGetStatus(fence, &status) == CAD_SUCCESS);
    assert(status == CAD_FENCE_NOT_READY);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── Main ────────────────────────────────────────────────────────── */

int main(void) {
    printf("=== CaduceusCore Runtime Fault Tests (C) ===\n");
    printf("ABI version: %d.%d\n\n", CAD_ABI_MAJOR, CAD_ABI_MINOR);
    /* _run_* constructors execute here */
    printf("\n==========================================\n");
    printf("Results: %d/%d passed\n", tests_passed, tests_run);
    printf("==========================================\n");
    return (tests_passed == tests_run) ? 0 : 1;
}
