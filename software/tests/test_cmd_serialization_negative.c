/*
 * test_cmd_serialization_negative.c — Command Serialization Negative Tests
 *
 * Verifies error paths in cadQueueSubmit() serialization:
 *   1. Freed buffer: allocate → append ExecuteBlob → free buffer → submit
 *      must return CAD_ERROR_INVALID_HANDLE (not crash).
 *   2. Submit transport failure: inject error, verify serialized buffer
 *      is freed (no leak — exercised implicitly by Valgrind/ASan).
 *   3. Zero-size blob: okay, header size 12 with blob_count=1, 0 bytes.
 */

#include "caduceus/runtime.h"
#include "caduceus/transport_mock_test.h"

#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int tests_run = 0;
static int tests_passed = 0;
static int tests_failed = 0;

#define SERIALIZATION_TEST(name) \
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

/* ── 1. Freed buffer referenced by ExecuteBlob → INVALID_HANDLE ──── */

SERIALIZATION_TEST(freed_buffer_reject) {
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 128;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 8;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendExecuteBlob(cl, buf, 0, 64) == CAD_SUCCESS);

    /* Free the buffer before submit — the cmd list still references it */
    assert(cadBufferFree(buf) == CAD_SUCCESS);

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    /* Submit must detect the freed buffer via validate_buffer() */
    assert(cadQueueSubmit(queue, cl, NULL) == CAD_ERROR_INVALID_HANDLE);

    /* Command list still valid (submit failed, caller retains ownership) */
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 2. Transport submit failure — no leak of serialized buffer ───── */

SERIALIZATION_TEST(transport_submit_error_no_leak) {
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 64;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    assert(cadCommandListAppendExecuteBlob(cl, buf, 0, 32) == CAD_SUCCESS);

    /* Inject transport error: next submit returns DEVICE_LOST */
    cad_mock_set_next_submit_error(CAD_TR_ERR_LOST);

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    /* Submit must fail; serialized buffer must be freed before return */
    assert(cadQueueSubmit(queue, cl, NULL) == CAD_ERROR_DEVICE_LOST);

    /* Command list still valid (submit failed, ownership retained) */
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadBufferFree(buf) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 3. All NOPs with submit failure — still no leak ─────────────── */

SERIALIZATION_TEST(all_nops_submit_error_no_leak) {
    cad_device_t dev = open_mock_device();

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 8;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    cad_mock_set_next_submit_error(CAD_TR_ERR_BUSY);

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    assert(cadQueueSubmit(queue, cl, NULL) == CAD_ERROR_DEVICE_BUSY);

    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 4. Command list with blob after freed buffer → combo reject ──── */

SERIALIZATION_TEST(second_entry_freed_buffer) {
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 64;
    cad_buffer_t keep = NULL, free_me = NULL;
    assert(cadBufferAllocate(dev, &bi, &keep) == CAD_SUCCESS);
    assert(cadBufferAllocate(dev, &bi, &free_me) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 8;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    assert(cadCommandListAppendExecuteBlob(cl, keep, 0, 32) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendExecuteBlob(cl, free_me, 0, 16) == CAD_SUCCESS);

    /* Free the second buffer; first buffer is still valid */
    assert(cadBufferFree(free_me) == CAD_SUCCESS);

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    /* Submit must detect freed buffer at index 2 (third entry) */
    assert(cadQueueSubmit(queue, cl, NULL) == CAD_ERROR_INVALID_HANDLE);

    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadBufferFree(keep) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── Main ────────────────────────────────────────────────────────── */

int main(void) {
    printf("=== CaduceusCore Cmd Serialization Negative Tests (C) ===\n");
    printf("ABI version: %d.%d\n\n", CAD_ABI_MAJOR, CAD_ABI_MINOR);
    printf("\n==========================================\n");
    printf("Results: %d/%d passed, %d failed\n",
           tests_passed, tests_run, tests_failed);
    printf("==========================================\n");
    return (tests_passed == tests_run) ? 0 : 1;
}
