/*
 * test_fm_e2e_submit.c — CaduceusCore End-to-End FM Submit Test
 *
 * Opens fm://unix via the Host Runtime, builds a valid MMUL command blob,
 * submits it, waits for the fence, and verifies the output.
 *
 * Usage:
 *   ./test_fm_e2e_submit fm://unix?path=/tmp/caduceus_w3t1.sock
 *
 * Depends on the device server: sim/device_server.py --socket <path>
 */

#include "caduceus/runtime.h"
#include "command_ir.h"

#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ── Device-server buffer allocator constants ───────────────────────
 *
 * The device server allocates buffers sequentially from DRAM_BUFFER_BASE
 * (0x80100000, 1 MiB above DRAM base) using first-fit.  On a fresh
 * connection these addresses are deterministic.  We compute addresses
 * BEFORE allocating so the blob builder can declare the correct host_addr
 * for each buffer.
 */
#define DRAM_BUF_BASE 0x80100000ULL

#define INPUT_SIZE   64    /* INT8: M*K = 1*64 */
#define WEIGHT_SIZE  2048  /* INT4 packed: K*N/2 = 64*64/2 */
#define OUTPUT_SIZE  256   /* INT32: M*N*4 = 1*64*4 */
#define SCALE_SIZE   256   /* INT32: N*4 = 64*4 */
#define CMD_BUF_SIZE 4096

/* Pre-computed consecutive addresses on a fresh device connection. */
#define ADDR_INPUT   DRAM_BUF_BASE
#define ADDR_WEIGHT  (ADDR_INPUT  + INPUT_SIZE)
#define ADDR_OUTPUT  (ADDR_WEIGHT + WEIGHT_SIZE)
#define ADDR_SCALE   (ADDR_OUTPUT + OUTPUT_SIZE)
#define ADDR_CMD     (ADDR_SCALE  + SCALE_SIZE)

/* MMUL shape: small tile for fast functional smoke. */
#define MMUL_M 1
#define MMUL_K 64
#define MMUL_N 64

static cad_error_t open_fm_device(const char *uri, cad_device_t *dev,
                                   cad_device_caps_t *caps) {
    cad_device_open_info_t oi = {0};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = uri;
    return cadDeviceOpen(&oi, dev, caps);
}

static const char *fm_error_string(cad_device_t dev, cad_error_t err) {
    static char buf[256];
    return cadDeviceErrorString(dev, err, buf, sizeof(buf));
}

static cad_buffer_t alloc_buffer(cad_device_t dev, uint64_t size) {
    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = size;
    cad_buffer_t buf = NULL;
    cad_error_t err = cadBufferAllocate(dev, &bi, &buf);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadBufferAllocate(size=%lu) -> %s\n",
                (unsigned long)size, fm_error_string(dev, err));
        exit(1);
    }
    return buf;
}

static void write_pattern(cad_buffer_t buf, uint64_t offset,
                           uint64_t size, uint8_t val) {
    uint8_t *data = (uint8_t *)malloc(size);
    assert(data);
    memset(data, val, size);
    cad_error_t err = cadBufferWrite(buf, offset, size, data);
    free(data);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadBufferWrite -> %s\n", cadErrorString(err));
        exit(1);
    }
}

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <fm://unix?path=...>\n", argv[0]);
        return 1;
    }
    const char *uri = argv[1];
    printf("=== CaduceusCore End-to-End FM Submit Test ===\n");
    printf("URI: %s\n\n", uri);

    /* ── 1. Open device ───────────────────────────────────────────── */
    cad_device_t dev = NULL;
    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    cad_error_t err = open_fm_device(uri, &dev, &caps);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadDeviceOpen -> %s\n"
                "      Is the device server running?\n",
                cadErrorString(err));
        return 1;
    }
    printf("  Device: %s (transport: %s)\n", caps.device_name,
           caps.transport_name);

    /* ── 2. Allocate buffers (order must match pre-computed addresses) */
    printf("  Allocating buffers...\n");
    cad_buffer_t input_buf  = alloc_buffer(dev, INPUT_SIZE);
    cad_buffer_t weight_buf = alloc_buffer(dev, WEIGHT_SIZE);
    cad_buffer_t output_buf = alloc_buffer(dev, OUTPUT_SIZE);
    cad_buffer_t scale_buf  = alloc_buffer(dev, SCALE_SIZE);
    cad_buffer_t cmd_buf    = alloc_buffer(dev, CMD_BUF_SIZE);

    /* ── 3. Write input/weight/scale data ─────────────────────────── */
    printf("  Writing input data...\n");
    /* Input: all 1s (INT8) → each byte = 0x01 */
    write_pattern(input_buf, 0, INPUT_SIZE, 0x01);
    /* Weight: all 1s (INT4 packed) → nibbles 0x1,0x1 → byte 0x11 */
    write_pattern(weight_buf, 0, WEIGHT_SIZE, 0x11);
    /* Scale: all 1.0f (float32 LE) → bytes [0x00, 0x00, 0x80, 0x3F] */
    {
        uint8_t *scale_data = (uint8_t *)malloc(SCALE_SIZE);
        assert(scale_data);
        for (uint64_t i = 0; i < SCALE_SIZE; i += 4) {
            float one = 1.0f;
            memcpy(&scale_data[i], &one, 4);
        }
        err = cadBufferWrite(scale_buf, 0, SCALE_SIZE, scale_data);
        free(scale_data);
        if (err != CAD_SUCCESS) {
            fprintf(stderr, "FAIL: scale write -> %s\n",
                    fm_error_string(dev, err));
            return 1;
        }
    }

    /* ── 4. Build & encode the MMUL command blob ──────────────────── */
    printf("  Building MMUL blob (M=%u, K=%u, N=%u)...\n",
           MMUL_M, MMUL_K, MMUL_N);
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    assert(blob);

    cad_buffer_id_t input_id = cad_buffer_declare(
        blob, INPUT_SIZE, 64, ADDR_INPUT);
    cad_buffer_id_t weight_id = cad_buffer_declare(
        blob, WEIGHT_SIZE, 64, ADDR_WEIGHT);
    cad_buffer_id_t output_id = cad_buffer_declare(
        blob, OUTPUT_SIZE, 64, ADDR_OUTPUT);
    cad_buffer_id_t scale_id = cad_buffer_declare(
        blob, SCALE_SIZE, 64, ADDR_SCALE);

    assert(input_id != CAD_BUFFER_INVALID);
    assert(weight_id != CAD_BUFFER_INVALID);
    assert(output_id != CAD_BUFFER_INVALID);
    assert(scale_id != CAD_BUFFER_INVALID);

    int rc = cad_op_mmul(blob, input_id, weight_id, output_id, scale_id,
                         MMUL_M, MMUL_K, MMUL_N, 0, NULL);
    assert(rc == 0);

    cad_lower_status_t ls = cad_command_blob_lower(blob);
    if (ls != CAD_LOWER_OK) {
        fprintf(stderr, "FAIL: blob lower -> %s\n",
                cad_lower_status_string(ls));
        cad_command_blob_destroy(blob);
        return 1;
    }

    uint8_t *encoded = NULL;
    size_t enc_size = 0;
    rc = cad_command_blob_encode(blob, &encoded, &enc_size);
    if (rc != 0 || !encoded || enc_size == 0) {
        fprintf(stderr, "FAIL: blob encode\n");
        cad_command_blob_destroy(blob);
        return 1;
    }
    printf("  Encoded blob: %zu bytes (commands=%zu, buffers=%zu)\n",
           enc_size,
           cad_command_blob_num_commands(blob),
           cad_command_blob_num_buffers(blob));

    /* ── 5. Write encoded blob to command buffer ──────────────────── */
    err = cadBufferWrite(cmd_buf, 0, enc_size, encoded);
    cad_command_blob_encoded_free(encoded);
    cad_command_blob_destroy(blob);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cmd_buf write -> %s\n",
                fm_error_string(dev, err));
        return 1;
    }

    /* ── 6. Create command list with ExecuteBlob ──────────────────── */
    printf("  Creating command list...\n");
    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    err = cadCommandListCreate(dev, &ci, &cl);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadCommandListCreate -> %s\n",
                fm_error_string(dev, err));
        return 1;
    }

    err = cadCommandListAppendExecuteBlob(cl, cmd_buf, 0, enc_size);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: append ExecuteBlob -> %s\n",
                fm_error_string(dev, err));
        return 1;
    }

    /* ── 7. Submit, wait on fence ─────────────────────────────────── */
    printf("  Submitting...\n");
    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    err = cadQueueCreate(dev, &qi, &queue);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadQueueCreate -> %s\n",
                fm_error_string(dev, err));
        return 1;
    }

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    err = cadFenceCreate(dev, &fi, &fence);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadFenceCreate -> %s\n",
                fm_error_string(dev, err));
        return 1;
    }

    err = cadQueueSubmit(queue, cl, fence);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadQueueSubmit -> %s\n",
                fm_error_string(dev, err));
        cadFenceDestroy(fence);
        return 1;
    }

    printf("  Waiting for fence...\n");
    err = cadFenceWait(fence, CAD_TIMEOUT_INFINITE);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadFenceWait -> %s\n",
                fm_error_string(dev, err));
        cadFenceDestroy(fence);
        return 1;
    }

    cad_fence_status_t fs = CAD_FENCE_NOT_READY;
    err = cadFenceGetStatus(fence, &fs);
    if (err != CAD_SUCCESS || fs != CAD_FENCE_COMPLETED) {
        fprintf(stderr, "FAIL: fence status=%d (err=%s)\n",
                (int)fs, fm_error_string(dev, err));
        cadFenceDestroy(fence);
        return 1;
    }
    printf("  Fence status: CAD_FENCE_COMPLETED (%d)\n", (int)fs);

    /* ── 8. Read and verify output ────────────────────────────────── */
    printf("  Reading output...\n");
    uint8_t *output = (uint8_t *)calloc(OUTPUT_SIZE, 1);
    assert(output);
    err = cadBufferRead(output_buf, 0, OUTPUT_SIZE, output);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadBufferRead -> %s\n",
                fm_error_string(dev, err));
        free(output);
        return 1;
    }

    /* Check output is non-zero. */
    int non_zero = 0;
    for (uint64_t i = 0; i < OUTPUT_SIZE; i++) {
        if (output[i] != 0) {
            non_zero = 1;
            break;
        }
    }
    if (!non_zero) {
        fprintf(stderr, "FAIL: output buffer is all zeros\n");
        free(output);
        return 1;
    }

    /* Print first few output values (float32). */
    const float *out_f32 = (const float *)output;
    printf("  Output float32 (first 4):");
    for (int i = 0; i < 4 && i < (int)(OUTPUT_SIZE / 4); i++) {
        printf(" %.1f", (double)out_f32[i]);
    }
    printf("\n");

    free(output);

    /* ── 9. Cleanup ───────────────────────────────────────────────── */
    cadFenceDestroy(fence);
    cadQueueDestroy(queue);
    cadBufferFree(cmd_buf);
    cadBufferFree(scale_buf);
    cadBufferFree(output_buf);
    cadBufferFree(weight_buf);
    cadBufferFree(input_buf);
    err = cadDeviceClose(dev);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadDeviceClose -> %s\n",
                fm_error_string(dev, err));
        return 1;
    }

    printf("\nPASS: End-to-end MMUL submit via fm://unix completed successfully\n");
    return 0;
}
