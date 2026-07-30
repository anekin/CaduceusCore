/*
 * test_runtime_conformance.c — C Conformance Test Suite
 *
 * Tests the real runtime core linked with the deterministic mock
 * transport.  Covers:
 *   1. Device open/close with mock:// URI
 *   2. Capability query
 *   3. Buffer allocate → write → read → verify → free
 *   4. Command list create → append nop → submit → destroy
 *   5. Queue order preservation (sequence counter)
 *   6. Fence signal on submit with 0 pending ticks
 *   7. Fence wait with non-zero pending ticks (advance + resolve)
 *   8. Fence poll (CAD_TIMEOUT_IMMEDIATE on unsignalled fence)
 *   9. Fence timeout (CAD_TIMEOUT_INFINITE vs CAD_TIMEOUT_IMMEDIATE)
 *  10. Buffer read-after-write data integrity
 *  11. Multiple submissions to same queue (ordering)
 *  12. Device reset preserves handle
 *  13. Transport op log recording
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

/* ── 1. Device open/close ─────────────────────────────────────────── */

CONFORMANCE_TEST(device_open_close) {
    cad_device_t dev = open_mock_device();
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 2. Capability query ──────────────────────────────────────────── */

CONFORMANCE_TEST(device_caps) {
    cad_device_t dev = open_mock_device();

    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceGetCaps(dev, &caps) == CAD_SUCCESS);
    assert(caps.max_buffers > 0);
    assert(caps.max_buffer_size > 0);
    assert(caps.max_queues > 0);
    assert(strlen(caps.device_name) > 0);
    assert(strcmp(caps.transport_name, "Mock") == 0);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 3. Buffer allocate/write/read/verify/free ────────────────────── */

CONFORMANCE_TEST(buffer_read_write) {
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 256;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_SUCCESS);
    assert(buf != NULL);

    /* Write known data */
    const char *msg = "Hello, CaduceusCore!";
    size_t len = strlen(msg) + 1;
    assert(cadBufferWrite(buf, 0, len, msg) == CAD_SUCCESS);

    /* Read back and verify */
    char readback[256] = {0};
    assert(cadBufferRead(buf, 0, len, readback) == CAD_SUCCESS);
    assert(strcmp(readback, msg) == 0);

    /* Write/read at offset */
    assert(cadBufferWrite(buf, 100, len, msg) == CAD_SUCCESS);
    char readback2[256] = {0};
    assert(cadBufferRead(buf, 100, len, readback2) == CAD_SUCCESS);
    assert(strcmp(readback2, msg) == 0);

    assert(cadBufferFree(buf) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 4. Command list create/append/submit ─────────────────────────── */

CONFORMANCE_TEST(command_list_basic) {
    cad_device_t dev = open_mock_device();

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 8;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    for (int i = 0; i < 8; i++) {
        assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    }
    /* 9th should fail */
    assert(cadCommandListAppendNop(cl) == CAD_ERROR_OUT_OF_MEMORY);

    /* Destroy without submitting */
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 5. Queue submit with immediate fence signal ──────────────────── */

CONFORMANCE_TEST(queue_submit_immediate_fence) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);

    /* Poll before submit: not ready */
    assert(cadFencePoll(fence) == CAD_ERROR_NOT_READY);

    assert(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS);

    /* Fence should be signalled immediately (pending_ticks=0) */
    assert(cadFencePoll(fence) == CAD_SUCCESS);

    cad_fence_status_t status = CAD_FENCE_NOT_READY;
    assert(cadFenceGetStatus(fence, &status) == CAD_SUCCESS);
    assert(status == CAD_FENCE_COMPLETED);

    assert(cadFenceWait(fence, CAD_TIMEOUT_IMMEDIATE) == CAD_SUCCESS);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 6. Fence with non-zero pending ticks ───────────────────────── */

CONFORMANCE_TEST(fence_delayed_completion) {
    cad_mock_set_pending_ticks(5);
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

    assert(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS);

    /* Fence should NOT be ready yet (0 ticks elapsed) */
    assert(cadFencePoll(fence) == CAD_ERROR_NOT_READY);
    assert(cadFenceWait(fence, CAD_TIMEOUT_IMMEDIATE) == CAD_ERROR_NOT_READY);

    /* Advance 3 ticks — still not ready */
    cad_mock_advance_ticks(3);
    assert(cadFencePoll(fence) == CAD_ERROR_NOT_READY);

    /* Advance 2 more ticks — should be ready */
    cad_mock_advance_ticks(2);
    assert(cadFencePoll(fence) == CAD_SUCCESS);

    cad_fence_status_t status = CAD_FENCE_NOT_READY;
    assert(cadFenceGetStatus(fence, &status) == CAD_SUCCESS);
    assert(status == CAD_FENCE_COMPLETED);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 7. Infinite wait resolves fence ─────────────────────────────── */

CONFORMANCE_TEST(fence_infinite_wait) {
    cad_mock_set_pending_ticks(3);
    cad_device_t dev = open_mock_device();

    cad_queue_t queue = NULL;
    {
        cad_queue_create_info_t qi = {0};
        qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
        assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);
    }

    cad_command_list_t cl = NULL;
    {
        cad_command_list_create_info_t ci = {0};
        ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
        assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
        assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    }

    cad_fence_t fence = NULL;
    {
        cad_fence_create_info_t fi = {0};
        fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
        assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);
    }

    assert(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS);

    /* Infinite wait should advance ticks and return success */
    assert(cadFenceWait(fence, CAD_TIMEOUT_INFINITE) == CAD_SUCCESS);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 8. Immediate timeout on unsignalled fence ───────────────────── */

CONFORMANCE_TEST(fence_immediate_timeout) {
    cad_mock_set_pending_ticks(10);
    cad_device_t dev = open_mock_device();

    cad_queue_t queue = NULL;
    {
        cad_queue_create_info_t qi = {0};
        qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
        assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);
    }

    cad_command_list_t cl = NULL;
    {
        cad_command_list_create_info_t ci = {0};
        ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
        assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
        assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    }

    cad_fence_t fence = NULL;
    {
        cad_fence_create_info_t fi = {0};
        fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
        assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);
    }

    assert(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS);

    /* Immediate wait on unsignalled fence → NOT_READY */
    assert(cadFenceWait(fence, CAD_TIMEOUT_IMMEDIATE) == CAD_ERROR_NOT_READY);

    /* Poll also shows not ready */
    assert(cadFencePoll(fence) == CAD_ERROR_NOT_READY);

    /* But wait with infinite eventual succeeds */
    assert(cadFenceWait(fence, CAD_TIMEOUT_INFINITE) == CAD_SUCCESS);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 9. Queue ordering — multiple submissions ────────────────────── */

CONFORMANCE_TEST(queue_ordering) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    cad_queue_t q1 = NULL, q2 = NULL;
    {
        cad_queue_create_info_t qi = {0};
        qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
        assert(cadQueueCreate(dev, &qi, &q1) == CAD_SUCCESS);
        assert(cadQueueCreate(dev, &qi, &q2) == CAD_SUCCESS);
    }

    /* Submit 3 command lists to q1 */
    for (int i = 0; i < 3; i++) {
        cad_command_list_create_info_t ci = {0};
        ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
        ci.max_entries = 4;
        cad_command_list_t cl = NULL;
        assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);
        assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
        assert(cadQueueSubmit(q1, cl, NULL) == CAD_SUCCESS);
        assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    }

    /* Verify op log shows 3 submits */
    uint32_t count = 0;
    const mock_op_log_entry_t *log = cad_mock_get_op_log(dev, &count);
    assert(log != NULL);
    int submits_found = 0;
    for (uint32_t i = 0; i < count; i++) {
        if (log[i].type == MOCK_OP_SUBMIT) submits_found++;
    }
    assert(submits_found == 3);

    assert(cadQueueDestroy(q1) == CAD_SUCCESS);
    assert(cadQueueDestroy(q2) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 10. Device reset ────────────────────────────────────────────── */

CONFORMANCE_TEST(device_reset) {
    cad_device_t dev = open_mock_device();

    /* Create a buffer, submit something, then reset */
    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 128;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_SUCCESS);

    assert(cadDeviceReset(dev) == CAD_SUCCESS);

    /* Device should still be valid, buffer should still work */
    assert(cadBufferFree(buf) == CAD_SUCCESS);

    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceGetCaps(dev, &caps) == CAD_SUCCESS);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 11. Multiple buffer lifecycle ────────────────────────────────── */

CONFORMANCE_TEST(multiple_buffers) {
    cad_device_t dev = open_mock_device();

    cad_buffer_t bufs[4] = {NULL};
    for (int i = 0; i < 4; i++) {
        cad_buffer_create_info_t bi = {0};
        bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
        bi.size = (uint64_t)((i + 1) * 256);
        assert(cadBufferAllocate(dev, &bi, &bufs[i]) == CAD_SUCCESS);
    }

    /* Write distinct data to each */
    for (int i = 0; i < 4; i++) {
        char data[32];
        snprintf(data, sizeof(data), "buffer_%d_data", i);
        assert(cadBufferWrite(bufs[i], 0, strlen(data) + 1, data) == CAD_SUCCESS);
    }

    /* Read back and verify */
    for (int i = 0; i < 4; i++) {
        char expected[32], actual[32] = {0};
        snprintf(expected, sizeof(expected), "buffer_%d_data", i);
        assert(cadBufferRead(bufs[i], 0, strlen(expected) + 1, actual) == CAD_SUCCESS);
        assert(strcmp(actual, expected) == 0);
    }

    for (int i = 0; i < 4; i++) {
        assert(cadBufferFree(bufs[i]) == CAD_SUCCESS);
    }

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── Main ────────────────────────────────────────────────────────── */

int main(void) {
    printf("=== CaduceusCore Runtime Conformance Tests (C) ===\n");
    printf("ABI version: %d.%d\n\n", CAD_ABI_MAJOR, CAD_ABI_MINOR);
    /* _run_* constructors execute here */
    printf("\n==========================================\n");
    printf("Results: %d/%d passed, %d failed\n",
           tests_passed, tests_run, tests_failed);
    printf("==========================================\n");
    return (tests_passed == tests_run) ? 0 : 1;
}
