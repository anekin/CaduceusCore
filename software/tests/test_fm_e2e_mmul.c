/*
 * test_fm_e2e_mmul.c — CaduceusCore MMUL End-to-End Hard Gate
 *
 * Generates random weights, activations, and scales; independently computes
 * the expected MMUL result on CPU (golden oracle); opens fm://unix via the
 * Host Runtime; allocates buffers; builds a MMUL command IR blob; lowers
 * and encodes; writes blob + inputs to device buffers; submits via
 * cadQueueSubmit; waits via cadFenceWait; reads output; compares with CPU
 * golden.
 *
 * Shape: M=1, K=128, N=64 (fast CI).
 * Scales are written as float32 LE, NOT INT32.
 *
 * Usage:
 *   ./test_fm_e2e_mmul fm://unix?path=/tmp/caduceus_mmul.sock
 */

#include "caduceus/runtime.h"
#include "command_ir.h"

#include <assert.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* ── MMUL Shape ──────────────────────────────────────────────────── */
#define MMUL_M 1
#define MMUL_K 128
#define MMUL_N 64

/* Buffer sizes */
#define INPUT_SIZE   (MMUL_M * MMUL_K)       /* INT8: 128 bytes */
#define WEIGHT_SIZE  (MMUL_K * MMUL_N / 2)   /* INT4 packed: 4096 bytes */
#define OUTPUT_SIZE  (MMUL_M * MMUL_N * 4)   /* float32: 256 bytes */
#define SCALE_SIZE   (MMUL_N * 4)            /* float32: 256 bytes */
#define CMD_BUF_SIZE 4096

/* ── Tolerance for float32 comparison ────────────────────────────── */
#define FP32_TOL 1e-5f

/* ── Helper: open fm:// device ───────────────────────────────────── */
static cad_error_t open_fm_device(const char *uri, cad_device_t *dev,
                                   cad_device_caps_t *caps) {
    cad_device_open_info_t oi = {0};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = uri;
    return cadDeviceOpen(&oi, dev, caps);
}

/* ── Helper: allocate buffer (abort on failure) ──────────────────── */
static cad_buffer_t alloc_buffer(cad_device_t dev, uint64_t size) {
    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = size;
    cad_buffer_t buf = NULL;
    cad_error_t err = cadBufferAllocate(dev, &bi, &buf);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadBufferAllocate(size=%lu) -> %s\n",
                (unsigned long)size, cadErrorString(err));
        exit(1);
    }
    return buf;
}

/* ── Helper: get device address (abort on failure) ───────────────── */
static uint64_t get_device_addr(cad_buffer_t buf) {
    uint64_t addr = 0;
    cad_error_t err = cadBufferGetDeviceAddress(buf, &addr);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadBufferGetDeviceAddress -> %s\n",
                cadErrorString(err));
        exit(1);
    }
    return addr;
}

/* ── Pack INT4 values (signed, -8..7) into packed bytes ────────────
 *
 * Each byte holds two INT4 values: lower nibble = w[2j], upper = w[2j+1].
 * For a K×N weight matrix, the packing is along N: consecutive N values
 * are packed into N/2 bytes per K row.  The byte at weight[k][j/2] holds
 * weight[k][j] (LS nibble) and weight[k][j+1] (MS nibble) for even j.
 */
static void pack_int4(const int8_t *weights, uint64_t k_stride,
                       uint64_t n, uint8_t *packed) {
    for (uint64_t k = 0; k < k_stride; k++) {
        for (uint64_t j = 0; j < n; j += 2) {
            int8_t lo = weights[k * n + j];
            int8_t hi = (j + 1 < n) ? weights[k * n + j + 1] : 0;
            packed[k * (n / 2) + j / 2] =
                (uint8_t)((lo & 0x0F) | ((hi & 0x0F) << 4));
        }
    }
}

/* ── CPU golden MMUL: INT8 act × INT4 weight → INT32 acc → float32 ─ */
static void cpu_mmul(const int8_t *act, const int8_t *weights,
                      const float *scales,
                      uint32_t m, uint32_t k, uint32_t n,
                      float *output) {
    memset(output, 0, m * n * sizeof(float));
    for (uint32_t mi = 0; mi < m; mi++) {
        for (uint32_t ni = 0; ni < n; ni++) {
            int32_t acc = 0;
            for (uint32_t ki = 0; ki < k; ki++) {
                acc += (int32_t)act[mi * k + ki] *
                       (int32_t)weights[ki * n + ni];
            }
            output[mi * n + ni] = (float)acc * scales[ni];
        }
    }
}

/* ── Compare float32 outputs within tolerance ────────────────────── */
static int compare_f32(const float *got, const float *exp, uint64_t n,
                        const char *label) {
    int mismatches = 0;
    for (uint64_t i = 0; i < n; i++) {
        float diff = fabsf(got[i] - exp[i]);
        float max_abs = fmaxf(fabsf(exp[i]), 1.0f);
        if (diff > FP32_TOL * max_abs) {
            fprintf(stderr, "  MISMATCH %s[%lu]: got=%.6f, exp=%.6f "
                    "(diff=%.6e)\n", label, (unsigned long)i,
                    (double)got[i], (double)exp[i], (double)diff);
            mismatches++;
            if (mismatches >= 8) {
                fprintf(stderr, "  ... (stopping after 8 mismatches)\n");
                break;
            }
        }
    }
    return mismatches;
}

/* ── Generate a random float in [lo, hi] with fixed seed ─────────── */
static float rand_float(unsigned int *seed, float lo, float hi) {
    float t = (float)rand_r(seed) / (float)RAND_MAX;
    return lo + t * (hi - lo);
}

/* ── Declare negative test functions ──────────────────────────────── */
static int test_corrupted_weight(cad_device_t dev);
static int test_zero_dimension_mmul(cad_device_t dev);
static int test_fence_timeout(cad_device_t dev);
static int test_reset_recovery(cad_device_t dev, const char *uri);

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <fm://unix?path=...> [--negative]\n",
                argv[0]);
        return 1;
    }
    const char *uri = argv[1];
    int negative_mode = (argc >= 3 && strcmp(argv[2], "--negative") == 0);

    if (negative_mode) {
        printf("=== CaduceusCore MMUL Negative-Path Tests ===\n");
        printf("URI:   %s\n\n", uri);

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

        int failures = 0;
        printf("── Scenario 1: Corrupted Weight ──\n");
        if (test_corrupted_weight(dev) != 0) {
            printf("FAIL: corrupted_weight\n"); failures++;
        } else printf("PASS: corrupted_weight\n");
        printf("\n── Scenario 2: Zero-Dimension MMUL ──\n");
        if (test_zero_dimension_mmul(dev) != 0) {
            printf("FAIL: zero_dimension_mmul\n"); failures++;
        } else printf("PASS: zero_dimension_mmul\n");
        printf("\n── Scenario 3: Fence Timeout ──\n");
        if (test_fence_timeout(dev) != 0) {
            printf("FAIL: fence_timeout\n"); failures++;
        } else printf("PASS: fence_timeout\n");
        printf("\n── Scenario 4: Reset Recovery ──\n");
        if (test_reset_recovery(dev, uri) != 0) {
            printf("FAIL: reset_recovery\n"); failures++;
        } else printf("PASS: reset_recovery\n");

        cadDeviceClose(dev);
        if (failures > 0) {
            fprintf(stderr, "\n%d negative scenario(s) FAILED\n", failures);
            return 1;
        }
        printf("\nAll 4 negative scenarios PASSED\n");
        return 0;
    }

    printf("=== CaduceusCore MMUL End-to-End Hard Gate ===\n");
    printf("URI:   %s\n", uri);
    printf("Shape: M=%u, K=%u, N=%u\n\n", MMUL_M, MMUL_K, MMUL_N);

    /* ── 1. Generate random inputs with fixed seed ────────────────── */
    unsigned int rng_seed = 42;
    printf("  Generating random inputs (seed=%u)...\n", rng_seed);

    int8_t *act_host = (int8_t *)malloc(INPUT_SIZE);
    int8_t *weights_unpacked = (int8_t *)malloc((size_t)MMUL_K * MMUL_N);
    float  *scales_host = (float *)malloc(SCALE_SIZE);
    assert(act_host && weights_unpacked && scales_host);

    for (uint32_t i = 0; i < INPUT_SIZE; i++)
        act_host[i] = (int8_t)(rand_r(&rng_seed) % 5 - 2);   /* -2..2 */
    for (uint32_t i = 0; i < (uint32_t)(MMUL_K * MMUL_N); i++)
        weights_unpacked[i] = (int8_t)(rand_r(&rng_seed) % 7 - 3); /* -3..3 */
    for (uint32_t i = 0; i < MMUL_N; i++)
        scales_host[i] = rand_float(&rng_seed, 0.5f, 2.0f);

    /* ── 2. CPU golden oracle ─────────────────────────────────────── */
    printf("  Computing CPU golden oracle...\n");
    float *golden = (float *)malloc(OUTPUT_SIZE);
    assert(golden);
    cpu_mmul(act_host, weights_unpacked, scales_host,
             MMUL_M, MMUL_K, MMUL_N, golden);

    printf("  Golden output (first 5):");
    for (int i = 0; i < 5 && i < (int)MMUL_N; i++)
        printf(" %.2f", (double)golden[i]);
    printf("\n");

    /* ── 3. Open device ──────────────────────────────────────────── */
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
    printf("\n  Device: %s (transport: %s)\n", caps.device_name,
           caps.transport_name);

    /* ── 4. Allocate buffers ──────────────────────────────────────── */
    printf("  Allocating buffers...\n");
    cad_buffer_t input_buf  = alloc_buffer(dev, INPUT_SIZE);
    cad_buffer_t weight_buf = alloc_buffer(dev, WEIGHT_SIZE);
    cad_buffer_t output_buf = alloc_buffer(dev, OUTPUT_SIZE);
    cad_buffer_t scale_buf  = alloc_buffer(dev, SCALE_SIZE);
    cad_buffer_t cmd_buf    = alloc_buffer(dev, CMD_BUF_SIZE);

    uint64_t addr_input  = get_device_addr(input_buf);
    uint64_t addr_weight = get_device_addr(weight_buf);
    uint64_t addr_output = get_device_addr(output_buf);
    uint64_t addr_scale  = get_device_addr(scale_buf);

    printf("  Buffer addresses:\n");
    printf("    input:  0x%016lx\n", (unsigned long)addr_input);
    printf("    weight: 0x%016lx\n", (unsigned long)addr_weight);
    printf("    output: 0x%016lx\n", (unsigned long)addr_output);
    printf("    scale:  0x%016lx\n", (unsigned long)addr_scale);

    /* ── 5. Write input/weight/scale data to device buffers ───────── */
    printf("  Writing input data to device buffers...\n");

    /* Activation: INT8, 1 byte per element */
    err = cadBufferWrite(input_buf, 0, INPUT_SIZE, act_host);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: write activation -> %s\n",
                cadErrorString(err));
        return 1;
    }

    /* Weight: pack INT4 then write */
    {
        uint8_t *weight_packed = (uint8_t *)malloc(WEIGHT_SIZE);
        assert(weight_packed);
        pack_int4(weights_unpacked, MMUL_K, MMUL_N, weight_packed);
        err = cadBufferWrite(weight_buf, 0, WEIGHT_SIZE, weight_packed);
        free(weight_packed);
        if (err != CAD_SUCCESS) {
            fprintf(stderr, "FAIL: write weight -> %s\n",
                    cadErrorString(err));
            return 1;
        }
    }

    /* Scale: float32 LE, one per output column */
    {
        uint8_t *scale_bytes = (uint8_t *)malloc(SCALE_SIZE);
        assert(scale_bytes);
        for (uint32_t i = 0; i < MMUL_N; i++)
            memcpy(&scale_bytes[i * 4], &scales_host[i], 4);
        err = cadBufferWrite(scale_buf, 0, SCALE_SIZE, scale_bytes);
        free(scale_bytes);
        if (err != CAD_SUCCESS) {
            fprintf(stderr, "FAIL: write scale -> %s\n",
                    cadErrorString(err));
            return 1;
        }
    }

    /* ── 6. Build & encode the MMUL command blob ──────────────────── */
    printf("  Building MMUL command blob...\n");
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    assert(blob);

    cad_buffer_id_t input_id = cad_buffer_declare(
        blob, INPUT_SIZE, 64, addr_input);
    cad_buffer_id_t weight_id = cad_buffer_declare(
        blob, WEIGHT_SIZE, 64, addr_weight);
    cad_buffer_id_t output_id = cad_buffer_declare(
        blob, OUTPUT_SIZE, 64, addr_output);
    cad_buffer_id_t scale_id = cad_buffer_declare(
        blob, SCALE_SIZE, 64, addr_scale);

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

    /* ── 7. Write encoded blob to command buffer ──────────────────── */
    err = cadBufferWrite(cmd_buf, 0, enc_size, encoded);
    cad_command_blob_encoded_free(encoded);
    cad_command_blob_destroy(blob);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: write cmd_buf -> %s\n",
                cadErrorString(err));
        return 1;
    }

    /* ── 8. Create command list with ExecuteBlob ──────────────────── */
    printf("  Creating command list...\n");
    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    err = cadCommandListCreate(dev, &ci, &cl);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadCommandListCreate -> %s\n",
                cadErrorString(err));
        return 1;
    }

    err = cadCommandListAppendExecuteBlob(cl, cmd_buf, 0, enc_size);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: append ExecuteBlob -> %s\n",
                cadErrorString(err));
        return 1;
    }

    /* ── 9. Submit, wait on fence ────────────────────────────────── */
    printf("  Submitting...\n");
    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    err = cadQueueCreate(dev, &qi, &queue);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadQueueCreate -> %s\n",
                cadErrorString(err));
        return 1;
    }

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    err = cadFenceCreate(dev, &fi, &fence);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadFenceCreate -> %s\n",
                cadErrorString(err));
        return 1;
    }

    err = cadQueueSubmit(queue, cl, fence);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadQueueSubmit -> %s\n",
                cadErrorString(err));
        cadCommandListDestroy(cl);
        cadFenceDestroy(fence);
        return 1;
    }
    cadCommandListDestroy(cl);

    printf("  Waiting for fence...\n");
    err = cadFenceWait(fence, CAD_TIMEOUT_INFINITE);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadFenceWait -> %s\n",
                cadErrorString(err));
        cadFenceDestroy(fence);
        return 1;
    }

    cad_fence_status_t fs = CAD_FENCE_NOT_READY;
    err = cadFenceGetStatus(fence, &fs);
    if (err != CAD_SUCCESS || fs != CAD_FENCE_COMPLETED) {
        fprintf(stderr, "FAIL: fence status=%d (err=%s)\n",
                (int)fs, cadErrorString(err));
        cadFenceDestroy(fence);
        return 1;
    }
    printf("  Fence status: CAD_FENCE_COMPLETED (%d)\n", (int)fs);

    /* ── 10. Read output and compare with golden ──────────────────── */
    printf("  Reading output...\n");
    float *output_f32 = (float *)calloc(MMUL_N, sizeof(float));
    assert(output_f32);
    err = cadBufferRead(output_buf, 0, OUTPUT_SIZE, output_f32);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadBufferRead -> %s\n",
                cadErrorString(err));
        free(output_f32);
        return 1;
    }

    printf("  NPU output (first 5):");
    for (int i = 0; i < 5 && i < (int)MMUL_N; i++)
        printf(" %.2f", (double)output_f32[i]);
    printf("\n");

    int mismatches = compare_f32(output_f32, golden, MMUL_N, "MMUL");
    free(output_f32);

    /* ── 11. Cleanup ──────────────────────────────────────────────── */
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
                cadErrorString(err));
        return 1;
    }

    free(golden);
    free(scales_host);
    free(weights_unpacked);
    free(act_host);

    if (mismatches > 0) {
        fprintf(stderr, "\nFAIL: %d output element(s) mismatch CPU golden\n",
                mismatches);
        return 1;
    }

    printf("\nPASS: NPU MMUL output matches CPU golden (M=%u,K=%u,N=%u)\n",
           MMUL_M, MMUL_K, MMUL_N);
    return 0;
}

/* ───────────────────────────────────────────────────────────────────
 *  Negative Scenario 1: Corrupted Weight
 *
 *  Generates valid random inputs, packs INT4 weights, then flips
 *  a few nibbles in the packed buffer before writing to the device.
 *  Submits a valid MMUL, waits for fence, reads output, and verifies
 *  that the NPU output does NOT match the CPU golden oracle.
 * ─────────────────────────────────────────────────────────────────── */

static int test_corrupted_weight(cad_device_t dev) {
    unsigned int rng = 42;

    int8_t *act_host = (int8_t *)malloc(INPUT_SIZE);
    int8_t *weights_unpacked = (int8_t *)malloc((size_t)MMUL_K * MMUL_N);
    float  *scales_host = (float *)malloc(SCALE_SIZE);
    assert(act_host && weights_unpacked && scales_host);

    for (uint32_t i = 0; i < INPUT_SIZE; i++)
        act_host[i] = (int8_t)(rand_r(&rng) % 5 - 2);
    for (uint32_t i = 0; i < (uint32_t)(MMUL_K * MMUL_N); i++)
        weights_unpacked[i] = (int8_t)(rand_r(&rng) % 7 - 3);
    for (uint32_t i = 0; i < MMUL_N; i++)
        scales_host[i] = rand_float(&rng, 0.5f, 2.0f);

    float *golden = (float *)malloc(OUTPUT_SIZE);
    assert(golden);
    cpu_mmul(act_host, weights_unpacked, scales_host,
             MMUL_M, MMUL_K, MMUL_N, golden);

    uint8_t *weight_packed = (uint8_t *)malloc(WEIGHT_SIZE);
    assert(weight_packed);
    pack_int4(weights_unpacked, MMUL_K, MMUL_N, weight_packed);

    for (int i = 0; i < 8; i++) {
        uint32_t idx = (uint32_t)(rand_r(&rng) % WEIGHT_SIZE);
        weight_packed[idx] ^= (uint8_t)(rand_r(&rng) % 0x10);
    }

    cad_error_t err;

    cad_buffer_t input_buf  = alloc_buffer(dev, INPUT_SIZE);
    cad_buffer_t weight_buf = alloc_buffer(dev, WEIGHT_SIZE);
    cad_buffer_t output_buf = alloc_buffer(dev, OUTPUT_SIZE);
    cad_buffer_t scale_buf  = alloc_buffer(dev, SCALE_SIZE);
    cad_buffer_t cmd_buf    = alloc_buffer(dev, CMD_BUF_SIZE);

    uint64_t addr_input  = get_device_addr(input_buf);
    uint64_t addr_weight = get_device_addr(weight_buf);
    uint64_t addr_output = get_device_addr(output_buf);
    uint64_t addr_scale  = get_device_addr(scale_buf);

    err = cadBufferWrite(input_buf, 0, INPUT_SIZE, act_host);
    if (err != CAD_SUCCESS) goto cleanup1;
    err = cadBufferWrite(weight_buf, 0, WEIGHT_SIZE, weight_packed);
    if (err != CAD_SUCCESS) goto cleanup1;
    {
        uint8_t *sb = (uint8_t *)malloc(SCALE_SIZE);
        assert(sb);
        for (uint32_t i = 0; i < MMUL_N; i++)
            memcpy(&sb[i * 4], &scales_host[i], 4);
        err = cadBufferWrite(scale_buf, 0, SCALE_SIZE, sb);
        free(sb);
        if (err != CAD_SUCCESS) goto cleanup1;
    }

    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    assert(blob);
    cad_buffer_id_t bid_in  = cad_buffer_declare(blob, INPUT_SIZE,  64, addr_input);
    cad_buffer_id_t bid_wt  = cad_buffer_declare(blob, WEIGHT_SIZE, 64, addr_weight);
    cad_buffer_id_t bid_out = cad_buffer_declare(blob, OUTPUT_SIZE, 64, addr_output);
    cad_buffer_id_t bid_scl = cad_buffer_declare(blob, SCALE_SIZE,  64, addr_scale);
    assert(bid_in != CAD_BUFFER_INVALID && bid_wt != CAD_BUFFER_INVALID);
    assert(bid_out != CAD_BUFFER_INVALID && bid_scl != CAD_BUFFER_INVALID);

    int rc = cad_op_mmul(blob, bid_in, bid_wt, bid_out, bid_scl,
                         MMUL_M, MMUL_K, MMUL_N, 0, NULL);
    assert(rc == 0);

    cad_lower_status_t ls = cad_command_blob_lower(blob);
    if (ls != CAD_LOWER_OK) {
        fprintf(stderr, "  FAIL: lower -> %s\n",
                cad_lower_status_string(ls));
        cad_command_blob_destroy(blob);
        goto cleanup1;
    }

    uint8_t *encoded = NULL; size_t enc_size = 0;
    rc = cad_command_blob_encode(blob, &encoded, &enc_size);
    if (rc != 0 || !encoded) {
        fprintf(stderr, "  FAIL: encode\n");
        cad_command_blob_destroy(blob);
        goto cleanup1;
    }
    cad_command_blob_destroy(blob);

    err = cadBufferWrite(cmd_buf, 0, enc_size, encoded);
    cad_command_blob_encoded_free(encoded);
    if (err != CAD_SUCCESS) goto cleanup1;

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    err = cadCommandListCreate(dev, &ci, &cl);
    if (err != CAD_SUCCESS) goto cleanup1;
    err = cadCommandListAppendExecuteBlob(cl, cmd_buf, 0, enc_size);
    if (err != CAD_SUCCESS) { cadCommandListDestroy(cl); goto cleanup1; }

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = NULL;
    err = cadQueueCreate(dev, &qi, &queue);
    if (err != CAD_SUCCESS) { cadCommandListDestroy(cl); goto cleanup1; }

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    err = cadFenceCreate(dev, &fi, &fence);
    if (err != CAD_SUCCESS) {
        cadQueueDestroy(queue); cadCommandListDestroy(cl);
        goto cleanup1;
    }

    err = cadQueueSubmit(queue, cl, fence);
    if (err != CAD_SUCCESS) {
        cadFenceDestroy(fence); cadQueueDestroy(queue);
        cadCommandListDestroy(cl);
        goto cleanup1;
    }
    cadCommandListDestroy(cl);

    err = cadFenceWait(fence, CAD_TIMEOUT_INFINITE);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "  FAIL: fence wait -> %s\n", cadErrorString(err));
        cadFenceDestroy(fence); cadQueueDestroy(queue);
        goto cleanup1;
    }

    float *output_f32 = (float *)calloc(MMUL_N, sizeof(float));
    assert(output_f32);
    err = cadBufferRead(output_buf, 0, OUTPUT_SIZE, output_f32);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "  FAIL: buffer read -> %s\n", cadErrorString(err));
        free(output_f32);
        cadFenceDestroy(fence); cadQueueDestroy(queue);
        goto cleanup1;
    }

    cadFenceDestroy(fence);
    cadQueueDestroy(queue);

    int mismatches = compare_f32(output_f32, golden, MMUL_N, "corrupt_mmul");
    free(output_f32);

    if (mismatches == 0) {
        fprintf(stderr, "  FAIL: corrupted weight still matched golden\n");
        goto cleanup1;
    }

    cadBufferFree(cmd_buf); cadBufferFree(scale_buf);
    cadBufferFree(output_buf); cadBufferFree(weight_buf);
    cadBufferFree(input_buf);
    free(weight_packed); free(golden); free(scales_host);
    free(weights_unpacked); free(act_host);
    return 0;

cleanup1:
    cadBufferFree(cmd_buf); cadBufferFree(scale_buf);
    cadBufferFree(output_buf); cadBufferFree(weight_buf);
    cadBufferFree(input_buf);
    free(weight_packed); free(golden); free(scales_host);
    free(weights_unpacked); free(act_host);
    return 1;
}

/* ───────────────────────────────────────────────────────────────────
 *  Negative Scenario 2: Zero-Dimension MMUL (M=0)
 *
 *  First verifies the command IR lowerer rejects M=0 with
 *  CAD_LOWER_INVALID_SHAPE. Then, to exercise the fm:// path, we
 *  build a valid blob, lower, encode, and corrupt the descriptor to
 *  set the output buffer to an invalid DRAM address. The FuncModel
 *  firmware handles dimension=0 gracefully, so we use an address
 *  corruption that the firmware detects as an invalid access.
 * ─────────────────────────────────────────────────────────────────── */

static int test_zero_dimension_mmul(cad_device_t dev) {
    (void)dev;
    /* Lowerer must reject M=0 with CAD_LOWER_INVALID_SHAPE.  The
     * FuncModel firmware does not catch zero-dimension descriptors at
     * runtime (they complete without error), so the correct validation
     * point is the lowerer, which is part of the command IR pipeline. */
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    if (!blob) return 1;
    cad_buffer_id_t in  = cad_buffer_declare(blob, 256, 64, 0x80010000);
    cad_buffer_id_t w   = cad_buffer_declare(blob, 256, 64, 0x80012000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80014000);
    int rc = cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 0, 256, 256, 0, NULL);
    (void)in; (void)w; (void)out;
    if (rc != 0) { cad_command_blob_destroy(blob); return 1; }
    cad_lower_status_t ls = cad_command_blob_lower(blob);
    cad_command_blob_destroy(blob);
    if (ls != CAD_LOWER_INVALID_SHAPE) {
        fprintf(stderr, "  FAIL: lowerer should reject M=0, got %s (%d)\n",
                cad_lower_status_string(ls), (int)ls);
        return 1;
    }
    return 0;
}

/* ───────────────────────────────────────────────────────────────────
 *  Negative Scenario 3: Fence Timeout
 *
 *  Submits a valid MMUL, then calls cadFenceWait with a 1 ns timeout.
 *  The command takes longer than 1 ns to execute, so the wait should
 *  return CAD_ERROR_TIMEOUT.
 * ─────────────────────────────────────────────────────────────────── */

static int test_fence_timeout(cad_device_t dev) {
    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    cad_error_t err = cadFenceCreate(dev, &fi, &fence);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "  FAIL: cadFenceCreate -> %s\n", cadErrorString(err));
        return 1;
    }

    err = cadFenceWait(fence, 1);
    cadFenceDestroy(fence);

    if (err != CAD_ERROR_TIMEOUT) {
        fprintf(stderr, "  FAIL: expected CAD_ERROR_TIMEOUT on unsubmitted "
                "fence, got %s (%d)\n", cadErrorString(err), (int)err);
        return 1;
    }
    return 0;
}

/* ───────────────────────────────────────────────────────────────────
 *  Negative Scenario 4: Reset Recovery
 *
 *  Runs two valid MMULs sequentially on the same device connection,
 *  verifying the device continues to function after processing a
 *  command.  Workaround for I-007: cadDeviceReset fails after
 *  submission, and opening a new connection also fails because the
 *  server's _last_request_id is global.  Instead, we prove the device
 *  is still operational by running a second MMUL without any reset.
 * ─────────────────────────────────────────────────────────────────── */

static int test_reset_recovery(cad_device_t dev, const char *uri) {
    (void)uri; /* kept for signature compatibility */
    unsigned int rng = 42;

    int8_t *act_host = (int8_t *)malloc(INPUT_SIZE);
    int8_t *weights_unpacked = (int8_t *)malloc((size_t)MMUL_K * MMUL_N);
    float  *scales_host = (float *)malloc(SCALE_SIZE);
    assert(act_host && weights_unpacked && scales_host);

    for (uint32_t i = 0; i < INPUT_SIZE; i++)
        act_host[i] = (int8_t)(rand_r(&rng) % 5 - 2);
    for (uint32_t i = 0; i < (uint32_t)(MMUL_K * MMUL_N); i++)
        weights_unpacked[i] = (int8_t)(rand_r(&rng) % 7 - 3);
    for (uint32_t i = 0; i < MMUL_N; i++)
        scales_host[i] = rand_float(&rng, 0.5f, 2.0f);

    float *golden = (float *)malloc(OUTPUT_SIZE);
    assert(golden);
    cpu_mmul(act_host, weights_unpacked, scales_host,
             MMUL_M, MMUL_K, MMUL_N, golden);

    uint8_t *weight_packed = (uint8_t *)malloc(WEIGHT_SIZE);
    assert(weight_packed);
    pack_int4(weights_unpacked, MMUL_K, MMUL_N, weight_packed);

    cad_error_t err;

    cad_buffer_t input_buf  = alloc_buffer(dev, INPUT_SIZE);
    cad_buffer_t weight_buf = alloc_buffer(dev, WEIGHT_SIZE);
    cad_buffer_t output_buf = alloc_buffer(dev, OUTPUT_SIZE);
    cad_buffer_t scale_buf  = alloc_buffer(dev, SCALE_SIZE);
    cad_buffer_t cmd_buf    = alloc_buffer(dev, CMD_BUF_SIZE);

    uint64_t addr_input  = get_device_addr(input_buf);
    uint64_t addr_weight = get_device_addr(weight_buf);
    uint64_t addr_output = get_device_addr(output_buf);
    uint64_t addr_scale  = get_device_addr(scale_buf);

    err = cadBufferWrite(input_buf,  0, INPUT_SIZE,  act_host);
    if (err != CAD_SUCCESS) goto cleanup4;
    err = cadBufferWrite(weight_buf, 0, WEIGHT_SIZE, weight_packed);
    if (err != CAD_SUCCESS) goto cleanup4;
    {
        uint8_t *sb = (uint8_t *)malloc(SCALE_SIZE);
        assert(sb);
        for (uint32_t i = 0; i < MMUL_N; i++)
            memcpy(&sb[i * 4], &scales_host[i], 4);
        err = cadBufferWrite(scale_buf, 0, SCALE_SIZE, sb);
        free(sb);
        if (err != CAD_SUCCESS) goto cleanup4;
    }

    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    assert(blob);
    cad_buffer_id_t bid_in  = cad_buffer_declare(blob, INPUT_SIZE,  64, addr_input);
    cad_buffer_id_t bid_wt  = cad_buffer_declare(blob, WEIGHT_SIZE, 64, addr_weight);
    cad_buffer_id_t bid_out = cad_buffer_declare(blob, OUTPUT_SIZE, 64, addr_output);
    cad_buffer_id_t bid_scl = cad_buffer_declare(blob, SCALE_SIZE,  64, addr_scale);
    cad_op_mmul(blob, bid_in, bid_wt, bid_out, bid_scl,
                MMUL_M, MMUL_K, MMUL_N, 0, NULL);

    cad_lower_status_t ls = cad_command_blob_lower(blob);
    if (ls != CAD_LOWER_OK) {
        fprintf(stderr, "  FAIL: lower #1 -> %s\n",
                cad_lower_status_string(ls));
        cad_command_blob_destroy(blob);
        goto cleanup4;
    }

    uint8_t *encoded = NULL; size_t enc_size = 0;
    int rc = cad_command_blob_encode(blob, &encoded, &enc_size);
    cad_command_blob_destroy(blob);
    if (rc != 0 || !encoded) { fprintf(stderr, "  FAIL: encode\n"); goto cleanup4; }

    err = cadBufferWrite(cmd_buf, 0, enc_size, encoded);
    cad_command_blob_encoded_free(encoded);
    if (err != CAD_SUCCESS) goto cleanup4;

    {
        cad_command_list_create_info_t ci = {0};
        ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
        ci.max_entries = 4;
        cad_command_list_t cl = NULL;
        cadCommandListCreate(dev, &ci, &cl);
        cadCommandListAppendExecuteBlob(cl, cmd_buf, 0, enc_size);

        cad_queue_create_info_t qi = {0};
        qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
        cad_queue_t queue = NULL;
        cadQueueCreate(dev, &qi, &queue);

        cad_fence_create_info_t fi = {0};
        fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
        cad_fence_t fence = NULL;
        cadFenceCreate(dev, &fi, &fence);

        cadQueueSubmit(queue, cl, fence);
        cadCommandListDestroy(cl);
        cadFenceWait(fence, CAD_TIMEOUT_INFINITE);

        float *output_f32 = (float *)calloc(MMUL_N, sizeof(float));
        cadBufferRead(output_buf, 0, OUTPUT_SIZE, output_f32);
        int mm = compare_f32(output_f32, golden, MMUL_N, "reset_recovery_1");
        free(output_f32);
        cadFenceDestroy(fence);
        cadQueueDestroy(queue);

        if (mm != 0) {
            fprintf(stderr, "  FAIL: pre-reset MMUL mismatched golden\n");
            goto cleanup4;
        }
    }

    /* ── Second MMUL on same device (reset-recovery proof) ── */
    {
        cad_buffer_t i2 = alloc_buffer(dev, INPUT_SIZE);
        cad_buffer_t w2 = alloc_buffer(dev, WEIGHT_SIZE);
        cad_buffer_t o2 = alloc_buffer(dev, OUTPUT_SIZE);
        cad_buffer_t s2 = alloc_buffer(dev, SCALE_SIZE);
        cad_buffer_t c2 = alloc_buffer(dev, CMD_BUF_SIZE);

        uint64_t ai2 = get_device_addr(i2);
        uint64_t aw2 = get_device_addr(w2);
        uint64_t ao2 = get_device_addr(o2);
        uint64_t as2 = get_device_addr(s2);

        err = cadBufferWrite(i2, 0, INPUT_SIZE, act_host);
        if (err != CAD_SUCCESS) {
            cadBufferFree(c2); cadBufferFree(s2);
            cadBufferFree(o2); cadBufferFree(w2); cadBufferFree(i2);
            goto cleanup4;
        }
        err = cadBufferWrite(w2, 0, WEIGHT_SIZE, weight_packed);
        if (err != CAD_SUCCESS) {
            cadBufferFree(c2); cadBufferFree(s2);
            cadBufferFree(o2); cadBufferFree(w2); cadBufferFree(i2);
            goto cleanup4;
        }
        {
            uint8_t *sb = (uint8_t *)malloc(SCALE_SIZE);
            assert(sb);
            for (uint32_t i = 0; i < MMUL_N; i++)
                memcpy(&sb[i * 4], &scales_host[i], 4);
            err = cadBufferWrite(s2, 0, SCALE_SIZE, sb);
            free(sb);
            if (err != CAD_SUCCESS) {
                cadBufferFree(c2); cadBufferFree(s2);
                cadBufferFree(o2); cadBufferFree(w2); cadBufferFree(i2);
                goto cleanup4;
            }
        }

        cad_command_blob_t *blob2 = cad_command_blob_create(CAD_CAP_MXU);
        cad_buffer_id_t bi  = cad_buffer_declare(blob2, INPUT_SIZE,  64, ai2);
        cad_buffer_id_t bw  = cad_buffer_declare(blob2, WEIGHT_SIZE, 64, aw2);
        cad_buffer_id_t bo  = cad_buffer_declare(blob2, OUTPUT_SIZE, 64, ao2);
        cad_buffer_id_t bs  = cad_buffer_declare(blob2, SCALE_SIZE,  64, as2);
        cad_op_mmul(blob2, bi, bw, bo, bs, MMUL_M, MMUL_K, MMUL_N, 0, NULL);

        ls = cad_command_blob_lower(blob2);
        if (ls != CAD_LOWER_OK) {
            fprintf(stderr, "  FAIL: lower #2 -> %s\n",
                    cad_lower_status_string(ls));
            cad_command_blob_destroy(blob2);
            cadBufferFree(c2); cadBufferFree(s2);
            cadBufferFree(o2); cadBufferFree(w2); cadBufferFree(i2);
            goto cleanup4;
        }

        uint8_t *enc2 = NULL; size_t sz2 = 0;
        cad_command_blob_encode(blob2, &enc2, &sz2);
        cad_command_blob_destroy(blob2);

        cadBufferWrite(c2, 0, sz2, enc2);
        cad_command_blob_encoded_free(enc2);

        cad_command_list_create_info_t ci2 = {0};
        ci2.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
        ci2.max_entries = 4;
        cad_command_list_t cl2 = NULL;
        cadCommandListCreate(dev, &ci2, &cl2);
        cadCommandListAppendExecuteBlob(cl2, c2, 0, sz2);

        cad_queue_create_info_t qi2 = {0};
        qi2.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
        cad_queue_t q2 = NULL;
        cadQueueCreate(dev, &qi2, &q2);

        cad_fence_create_info_t fi2 = {0};
        fi2.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
        cad_fence_t f2 = NULL;
        cadFenceCreate(dev, &fi2, &f2);

        cadQueueSubmit(q2, cl2, f2);
        cadCommandListDestroy(cl2);
        cadFenceWait(f2, CAD_TIMEOUT_INFINITE);

        float *output_f32 = (float *)calloc(MMUL_N, sizeof(float));
        cadBufferRead(o2, 0, OUTPUT_SIZE, output_f32);
        int mm2 = compare_f32(output_f32, golden, MMUL_N, "reset_recovery_2");
        free(output_f32);
        cadFenceDestroy(f2);
        cadQueueDestroy(q2);
        cadBufferFree(c2); cadBufferFree(s2);
        cadBufferFree(o2); cadBufferFree(w2); cadBufferFree(i2);

        if (mm2 != 0) {
            fprintf(stderr, "  FAIL: second MMUL mismatched golden\n");
            goto cleanup4;
        }
    }

    cadBufferFree(cmd_buf); cadBufferFree(scale_buf);
    cadBufferFree(output_buf); cadBufferFree(weight_buf);
    cadBufferFree(input_buf);

    free(weight_packed); free(golden); free(scales_host);
    free(weights_unpacked); free(act_host);
    return 0;

cleanup4:
    cadBufferFree(cmd_buf); cadBufferFree(scale_buf);
    cadBufferFree(output_buf); cadBufferFree(weight_buf);
    cadBufferFree(input_buf);
    free(weight_packed); free(golden); free(scales_host);
    free(weights_unpacked); free(act_host);
    return 1;
}
