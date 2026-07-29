/*
 * test_execution_stats.c — CaduceusCore Execution Stats Test
 *
 * Verifies:
 *   1. MMUL submit returns mmul_ops >= 1, dma_bytes > 0.
 *   2. NOP-only submit returns all-zero stats.
 *   3. Invalid fence returns CAD_ERROR_INVALID_HANDLE.
 *   4. NULL stats returns CAD_ERROR_INVALID_ARGUMENT.
 *
 * Uses a single device connection for FM tests (I-007 workaround).
 *
 * Usage:
 *   ./test_execution_stats fm://unix?path=/tmp/caduceus_stats.sock
 */

#include "caduceus/runtime.h"
#include "command_ir.h"

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MMUL_M 1
#define MMUL_K 64
#define MMUL_N 64

#define INPUT_SIZE   64
#define WEIGHT_SIZE  2048
#define OUTPUT_SIZE  256
#define SCALE_SIZE   256
#define CMD_BUF_SIZE 4096

static int g_passed = 0;
static int g_failed = 0;

#define TASSERT(cond, msg) do { \
    if (!(cond)) { fprintf(stderr, "FAIL: %s\n", msg); g_failed++; } \
    else { g_passed++; } \
} while (0)

static cad_error_t open_fm_device(const char *uri, cad_device_t *dev,
                                   cad_device_caps_t *caps) {
    cad_device_open_info_t oi = {0};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = uri;
    return cadDeviceOpen(&oi, dev, caps);
}

static cad_buffer_t alloc_buf(cad_device_t dev, uint64_t size) {
    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = size;
    cad_buffer_t buf = NULL;
    cad_error_t err = cadBufferAllocate(dev, &bi, &buf);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FATAL: cadBufferAllocate(%lu) -> %s\n",
                (unsigned long)size, cadErrorString(err));
        exit(1);
    }
    return buf;
}

/* ── Test 1: MMUL submit returns mmul_ops >= 1, dma_bytes > 0 ──── */

static void test_mmul_stats(cad_device_t dev) {
    printf("--- Test 1: MMUL submit returns real execution stats ---\n");

    cad_buffer_t input_buf  = alloc_buf(dev, INPUT_SIZE);
    cad_buffer_t weight_buf = alloc_buf(dev, WEIGHT_SIZE);
    cad_buffer_t output_buf = alloc_buf(dev, OUTPUT_SIZE);
    cad_buffer_t scale_buf  = alloc_buf(dev, SCALE_SIZE);
    cad_buffer_t cmd_buf    = alloc_buf(dev, CMD_BUF_SIZE);

    uint64_t addr_input, addr_weight, addr_output, addr_scale;
    TASSERT(cadBufferGetDeviceAddress(input_buf, &addr_input) == CAD_SUCCESS,
            "get device address input");
    TASSERT(cadBufferGetDeviceAddress(weight_buf, &addr_weight) == CAD_SUCCESS,
            "get device address weight");
    TASSERT(cadBufferGetDeviceAddress(output_buf, &addr_output) == CAD_SUCCESS,
            "get device address output");
    TASSERT(cadBufferGetDeviceAddress(scale_buf, &addr_scale) == CAD_SUCCESS,
            "get device address scale");

    uint8_t *data = (uint8_t *)malloc(WEIGHT_SIZE);
    memset(data, 0x01, INPUT_SIZE);
    TASSERT(cadBufferWrite(input_buf, 0, INPUT_SIZE, data) == CAD_SUCCESS,
            "write input");
    memset(data, 0x11, WEIGHT_SIZE);
    TASSERT(cadBufferWrite(weight_buf, 0, WEIGHT_SIZE, data) == CAD_SUCCESS,
            "write weight");
    for (uint64_t i = 0; i < SCALE_SIZE; i += 4) {
        float one = 1.0f;
        memcpy(&data[i], &one, 4);
    }
    TASSERT(cadBufferWrite(scale_buf, 0, SCALE_SIZE, data) == CAD_SUCCESS,
            "write scale");
    free(data);

    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    TASSERT(blob != NULL, "create blob");

    cad_buffer_id_t bi = cad_buffer_declare(blob, INPUT_SIZE, 64, addr_input);
    (void)bi;
    cad_buffer_id_t bw = cad_buffer_declare(blob, WEIGHT_SIZE, 64, addr_weight);
    (void)bw;
    cad_buffer_id_t bo = cad_buffer_declare(blob, OUTPUT_SIZE, 64, addr_output);
    (void)bo;
    cad_buffer_id_t bs = cad_buffer_declare(blob, SCALE_SIZE, 64, addr_scale);
    (void)bs;

    TASSERT(bi != CAD_BUFFER_INVALID && bw != CAD_BUFFER_INVALID &&
            bo != CAD_BUFFER_INVALID && bs != CAD_BUFFER_INVALID,
            "declare buffers");

    int rc = cad_op_mmul(blob, bi, bw, bo, bs,
                         MMUL_M, MMUL_K, MMUL_N, 0, NULL);
    TASSERT(rc == 0, "cad_op_mmul");

    TASSERT(cad_command_blob_lower(blob) == CAD_LOWER_OK, "lower blob");

    uint8_t *encoded = NULL;
    size_t enc_size = 0;
    TASSERT(cad_command_blob_encode(blob, &encoded, &enc_size) == 0,
            "encode blob");

    TASSERT(cadBufferWrite(cmd_buf, 0, enc_size, encoded) == CAD_SUCCESS,
            "write cmd buf");
    cad_command_blob_encoded_free(encoded);
    cad_command_blob_destroy(blob);

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    TASSERT(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS, "create cmd list");
    TASSERT(cadCommandListAppendExecuteBlob(cl, cmd_buf, 0, enc_size) == CAD_SUCCESS,
            "append execute blob");

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    TASSERT(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS, "create queue");

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    TASSERT(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS, "create fence");

    TASSERT(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS, "queue submit");
    TASSERT(cadFenceWait(fence, CAD_TIMEOUT_INFINITE) == CAD_SUCCESS,
            "fence wait");

    cad_fence_status_t fs = CAD_FENCE_NOT_READY;
    TASSERT(cadFenceGetStatus(fence, &fs) == CAD_SUCCESS, "get status");
    TASSERT(fs == CAD_FENCE_COMPLETED, "fence completed");

    cad_execution_stats_t stats;
    memset(&stats, 0, sizeof(stats));
    cad_error_t s_err = cadFenceGetExecutionStats(fence, &stats);
    TASSERT(s_err == CAD_SUCCESS, "get execution stats succeeds");
    printf("  mmul_ops=%u sfu_ops=%u vector_ops=%u dma_ops=%u\n",
           stats.mmul_ops, stats.sfu_ops, stats.vector_ops, stats.dma_ops);
    printf("  dma_bytes_read=%lu dma_bytes_written=%lu\n",
           (unsigned long)stats.dma_bytes_read,
           (unsigned long)stats.dma_bytes_written);
    TASSERT(stats.mmul_ops >= 1, "mmul_ops >= 1");
    TASSERT(stats.dma_bytes_read > 0, "dma_bytes_read > 0");
    TASSERT(stats.dma_bytes_written > 0, "dma_bytes_written > 0");

    cadFenceDestroy(fence);
    cadQueueDestroy(queue);
    cadBufferFree(cmd_buf);
    cadBufferFree(scale_buf);
    cadBufferFree(output_buf);
    cadBufferFree(weight_buf);
    cadBufferFree(input_buf);
}

/* ── Test 2: NOP-only submit returns all-zero stats ──────────────── */

static void test_nop_zero_stats(cad_device_t dev) {
    printf("--- Test 2: NOP-only submit returns all-zero stats ---\n");

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    TASSERT(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS, "create cmd list");
    TASSERT(cadCommandListAppendNop(cl) == CAD_SUCCESS, "append nop 1");
    TASSERT(cadCommandListAppendNop(cl) == CAD_SUCCESS, "append nop 2");

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    TASSERT(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS, "create queue");

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    TASSERT(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS, "create fence");

    TASSERT(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS, "queue submit");
    TASSERT(cadFenceWait(fence, CAD_TIMEOUT_INFINITE) == CAD_SUCCESS,
            "fence wait");

    cad_execution_stats_t stats;
    memset(&stats, 0xFF, sizeof(stats));
    cad_error_t s_err = cadFenceGetExecutionStats(fence, &stats);
    printf("  err=%s mmul=%u sfu=%u vec=%u dma=%u dma_r=%lu dma_w=%lu\n",
           cadErrorString(s_err),
           stats.mmul_ops, stats.sfu_ops, stats.vector_ops, stats.dma_ops,
           (unsigned long)stats.dma_bytes_read,
           (unsigned long)stats.dma_bytes_written);
    TASSERT(s_err == CAD_ERROR_NOT_READY || stats.mmul_ops == 0, "nop mmul_ops == 0 or NOT_READY");
    TASSERT(s_err == CAD_ERROR_NOT_READY || stats.sfu_ops == 0, "nop sfu_ops == 0 or NOT_READY");
    TASSERT(s_err == CAD_ERROR_NOT_READY || stats.vector_ops == 0, "nop vector_ops == 0 or NOT_READY");
    TASSERT(s_err == CAD_ERROR_NOT_READY || stats.dma_ops == 0, "nop dma_ops == 0 or NOT_READY");
    TASSERT(s_err == CAD_ERROR_NOT_READY || stats.dma_bytes_read == 0, "nop dma_bytes_read == 0 or NOT_READY");
    TASSERT(s_err == CAD_ERROR_NOT_READY || stats.dma_bytes_written == 0, "nop dma_bytes_written == 0 or NOT_READY");

    cadFenceDestroy(fence);
    cadQueueDestroy(queue);
}

/* ── Test 3: Invalid fence returns CAD_ERROR_INVALID_HANDLE ──────── */

static void test_invalid_fence(void) {
    printf("--- Test 3: Invalid fence returns CAD_ERROR_INVALID_HANDLE ---\n");

    cad_execution_stats_t stats;
    cad_error_t err = cadFenceGetExecutionStats(NULL, &stats);
    TASSERT(err == CAD_ERROR_INVALID_HANDLE, "NULL fence -> INVALID_HANDLE");
}

/* ── Test 4: NULL stats returns CAD_ERROR_INVALID_ARGUMENT ───────── */

static void test_null_stats(cad_device_t dev) {
    printf("--- Test 4: NULL stats returns CAD_ERROR_INVALID_ARGUMENT ---\n");

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    TASSERT(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS, "create fence");

    cad_error_t err = cadFenceGetExecutionStats(fence, NULL);
    TASSERT(err == CAD_ERROR_INVALID_ARGUMENT, "NULL stats -> INVALID_ARGUMENT");

    cadFenceDestroy(fence);
}

int main(int argc, char *argv[]) {
    const char *uri = "fm://unix?path=/tmp/caduceus_stats.sock";
    if (argc >= 2) uri = argv[1];

    printf("=== CaduceusCore Execution Stats Test ===\n");
    printf("URI: %s\n\n", uri);

    /* Test 3: no device needed (NULL fence). */
    test_invalid_fence();

    /* Single device connection for all FM tests (I-007 workaround). */
    cad_device_t dev = NULL;
    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    cad_error_t err = open_fm_device(uri, &dev, &caps);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "SKIP: cadDeviceOpen -> %s (server not running?)\n",
                cadErrorString(err));
        printf("\n=== Results: %d passed, %d failed ===\n", g_passed, g_failed);
        return g_failed > 0 ? 1 : 0;
    }
    printf("  Device: %s (transport: %s)\n\n", caps.device_name,
           caps.transport_name);

    test_null_stats(dev);
    test_nop_zero_stats(dev);
    test_mmul_stats(dev);

    cadDeviceClose(dev);

    printf("\n=== Results: %d passed, %d failed ===\n", g_passed, g_failed);
    return g_failed > 0 ? 1 : 0;
}
