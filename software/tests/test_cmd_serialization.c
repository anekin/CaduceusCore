/*
 * test_cmd_serialization.c — Command-Data Serialization Conformance
 *
 * Verifies that cadQueueSubmit() serializes command-list entries into a
 * {nop_count, blob_count, total_cmd_count, raw_blobs...} buffer and
 * forwards it to the transport.  Tests run against the mock transport
 * and verify the captured payload via cad_mock_get_last_submit_payload().
 *
 * Covers:
 *   1. Mixed NOPs + ExecuteBlob — correct header counts + blob bytes
 *   2. All-NOP command list — zero blob_count
 *   3. All-ExecuteBlob command list — zero nop_count
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

/* ── 1. Mixed NOPs + ExecuteBlob — header + raw bytes match ─────── */

SERIALIZATION_TEST(mixed_nops_and_blobs) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 256;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_SUCCESS);

    /* Write known pattern into the buffer */
    uint8_t pattern[256];
    for (int i = 0; i < 256; i++) pattern[i] = (uint8_t)(i + 1);
    assert(cadBufferWrite(buf, 0, 256, pattern) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 8;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    /* 2 NOPs + 1 ExecuteBlob(offset=10, size=50) + 1 NOP */
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendExecuteBlob(cl, buf, 10, 50) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);

    assert(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS);

    /* Verify captured payload */
    uint32_t size = 0;
    const uint8_t *payload = cad_mock_get_last_submit_payload(&size);
    assert(payload != NULL);
    assert(size == 12 + 50);  /* header + blob bytes */

    const uint32_t *hdr = (const uint32_t *)payload;
    assert(hdr[0] == 3);  /* nop_count: 3 NOPs */
    assert(hdr[1] == 1);  /* blob_count: 1 ExecuteBlob */
    assert(hdr[2] == 4);  /* total_cmd_count: 4 entries */

    /* Verify raw blob bytes match pattern[10..59] */
    const uint8_t *blob = payload + 12;
    for (int i = 0; i < 50; i++) {
        assert(blob[i] == pattern[10 + i]);
    }

    assert(cadFenceWait(fence, CAD_TIMEOUT_INFINITE) == CAD_SUCCESS);
    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadBufferFree(buf) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 2. All-NOP command list — zero blob_count ──────────────────── */

SERIALIZATION_TEST(all_nops_zero_blobs) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 8;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    assert(cadQueueSubmit(queue, cl, NULL) == CAD_SUCCESS);

    uint32_t size = 0;
    const uint8_t *payload = cad_mock_get_last_submit_payload(&size);
    assert(payload != NULL);
    assert(size == 12);  /* header only, no blob bytes */

    const uint32_t *hdr = (const uint32_t *)payload;
    assert(hdr[0] == 3);  /* nop_count */
    assert(hdr[1] == 0);  /* blob_count */
    assert(hdr[2] == 3);  /* total_cmd_count */

    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 3. All-ExecuteBlob command list — zero nop_count ────────────── */

SERIALIZATION_TEST(all_blobs_zero_nops) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 200;
    cad_buffer_t buf = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_SUCCESS);

    uint8_t data1[100];
    uint8_t data2[100];
    memset(data1, 0xAA, 100);
    memset(data2, 0xBB, 100);
    assert(cadBufferWrite(buf, 0, 100, data1) == CAD_SUCCESS);
    assert(cadBufferWrite(buf, 100, 100, data2) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    assert(cadCommandListAppendExecuteBlob(cl, buf, 0, 100) == CAD_SUCCESS);
    assert(cadCommandListAppendExecuteBlob(cl, buf, 100, 50) == CAD_SUCCESS);

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    assert(cadQueueSubmit(queue, cl, NULL) == CAD_SUCCESS);

    uint32_t size = 0;
    const uint8_t *payload = cad_mock_get_last_submit_payload(&size);
    assert(payload != NULL);
    assert(size == 12 + 100 + 50);

    const uint32_t *hdr = (const uint32_t *)payload;
    assert(hdr[0] == 0);  /* nop_count */
    assert(hdr[1] == 2);  /* blob_count */
    assert(hdr[2] == 2);  /* total_cmd_count */

    const uint8_t *blob = payload + 12;
    assert(memcmp(blob, data1, 100) == 0);
    assert(memcmp(blob + 100, data2, 50) == 0);

    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadBufferFree(buf) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 4. Multiple blobs from separate buffers ────────────────────── */

SERIALIZATION_TEST(multiple_buffers) {
    cad_mock_set_pending_ticks(0);
    cad_device_t dev = open_mock_device();

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 64;
    cad_buffer_t buf_a = NULL, buf_b = NULL;
    assert(cadBufferAllocate(dev, &bi, &buf_a) == CAD_SUCCESS);
    assert(cadBufferAllocate(dev, &bi, &buf_b) == CAD_SUCCESS);

    uint8_t pa[64]; memset(pa, 0x11, 64);
    uint8_t pb[64]; memset(pb, 0x22, 64);
    assert(cadBufferWrite(buf_a, 0, 64, pa) == CAD_SUCCESS);
    assert(cadBufferWrite(buf_b, 0, 64, pb) == CAD_SUCCESS);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    assert(cadCommandListAppendExecuteBlob(cl, buf_a, 0, 32) == CAD_SUCCESS);
    assert(cadCommandListAppendExecuteBlob(cl, buf_b, 0, 48) == CAD_SUCCESS);

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    assert(cadQueueSubmit(queue, cl, NULL) == CAD_SUCCESS);

    uint32_t size = 0;
    const uint8_t *payload = cad_mock_get_last_submit_payload(&size);
    assert(payload != NULL);
    assert(size == 12 + 32 + 48);

    const uint32_t *hdr = (const uint32_t *)payload;
    assert(hdr[0] == 0);
    assert(hdr[1] == 2);
    assert(hdr[2] == 2);

    const uint8_t *blob = payload + 12;
    assert(memcmp(blob, pa, 32) == 0);
    assert(memcmp(blob + 32, pb, 48) == 0);

    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadBufferFree(buf_a) == CAD_SUCCESS);
    assert(cadBufferFree(buf_b) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── Main ────────────────────────────────────────────────────────── */

int main(void) {
    printf("=== CaduceusCore Command Serialization Tests (C) ===\n");
    printf("ABI version: %d.%d\n\n", CAD_ABI_MAJOR, CAD_ABI_MINOR);
    printf("\n==========================================\n");
    printf("Results: %d/%d passed, %d failed\n",
           tests_passed, tests_run, tests_failed);
    printf("==========================================\n");
    return (tests_passed == tests_run) ? 0 : 1;
}
