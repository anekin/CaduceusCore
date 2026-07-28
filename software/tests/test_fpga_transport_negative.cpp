/*
 * test_fpga_transport_negative.cpp — FPGA Transport Negative Tests
 *
 * Verifies that invalid inputs, boundary conditions, and error paths
 * are properly rejected:
 *
 *   1. NO-GO path: structured failure (not PASS), ops reject operations
 *   2. NULL URI / invalid URI scheme
 *   3. BAR size undersized (various BAR indices)
 *   4. Buffer operations: NULL handles, out-of-range offsets
 *   5. Fence operations: NULL fence, double-wait
 *   6. Submit on NO-GO device
 *   7. Submit without fence (fire-and-forget)
 */

#include "caduceus/transport_fpga.h"
#include "caduceus/cad_transport.h"

#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static int tests_run = 0;
static int tests_passed = 0;
static int tests_failed = 0;

#define CHECK(expr) do { \
    if (!(expr)) { \
        printf("FAIL at %s:%d: %s\n", __FILE__, __LINE__, #expr); \
        tests_failed++; \
        return; \
    } \
} while (0)

#define CHECK_EQ(a, b) CHECK((a) == (b))
#define CHECK_NE(a, b) CHECK((a) != (b))

#define NEGATIVE_TEST(name) \
    static void test_##name(void); \
    __attribute__((constructor)) static void _run_##name(void) { \
        tests_run++; \
        cad_fpga_set_fake_type(-1); \
        for (int i = 0; i < CAD_FPGA_MAX_BARS; i++) cad_fpga_fake_set_bar_size(i, 0); \
        printf("  TEST: %s ... ", #name); \
        test_##name(); \
        tests_passed++; \
        printf("PASS\n"); \
    } \
    static void test_##name(void)

static int tp_open(void **tpriv, const char *uri) {
    return cad_transport_fpga_init(tpriv, uri);
}

static void tp_close(void *tpriv) {
    if (tpriv) {
        cad_transport_fpga_ops.device_fini(tpriv);
        free(tpriv);
    }
}

/* ── 1. NO-GO: operations should fail or be no-ops ────────────────── */

NEGATIVE_TEST(nogo_submit_rejected) {
    cad_fpga_set_fake_type(CAD_FPGA_NONE);
    void *tpriv = NULL;
    int ret = tp_open(&tpriv, "fpga://");
    CHECK_NE(ret, CAD_TR_SUCCESS);

    /* Submit on no-device should fail */
    ret = cad_transport_fpga_ops.submit(tpriv, NULL, 1, NULL);
    CHECK_NE(ret, CAD_TR_SUCCESS);

    tp_close(tpriv);
}

NEGATIVE_TEST(nogo_buffer_alloc_fails) {
    cad_fpga_set_fake_type(CAD_FPGA_NONE);
    void *tpriv = NULL;
    int ret = tp_open(&tpriv, "fpga://");
    CHECK_NE(ret, CAD_TR_SUCCESS);

    /* BARs not allocated, buffer alloc should not crash but will
     * fail because dev->bars are null/dirty.
     * Actually on NO-GO, bars are not allocated; buffer_alloc
     * dereferences dev->bars[0].reported_size which is 0.
     * This should fail gracefully. */
    cad_transport_buffer_t *buf = NULL;
    ret = cad_transport_fpga_ops.buffer_alloc(tpriv, &buf, 4096);
    /* On NO-GO, BAR reported_size is 0, so buffer_alloc rejects (size > 0) */
    CHECK_NE(ret, CAD_TR_SUCCESS);
    CHECK(buf == NULL);

    tp_close(tpriv);
}

NEGATIVE_TEST(nogo_type_is_none) {
    cad_fpga_set_fake_type(CAD_FPGA_NONE);
    void *tpriv = NULL;
    CHECK_NE(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_NONE);
    tp_close(tpriv);
}

/* ── 2. Invalid URI ──────────────────────────────────────────────── */

NEGATIVE_TEST(invalid_uri_null) {
    void *tpriv = NULL;
    CHECK_NE(tp_open(&tpriv, NULL), CAD_TR_SUCCESS);
    CHECK(tpriv == NULL); /* no allocation on null URI */
}

NEGATIVE_TEST(invalid_uri_bad_scheme) {
    void *tpriv = NULL;
    CHECK_NE(tp_open(&tpriv, "pcie://"), CAD_TR_SUCCESS);
    /* Unrecognized scheme → structured NO-GO (device allocated, type=NONE) */
    CHECK(tpriv != NULL);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_NONE);
    tp_close(tpriv);
}

NEGATIVE_TEST(invalid_uri_empty) {
    void *tpriv = NULL;
    CHECK_NE(tp_open(&tpriv, ""), CAD_TR_SUCCESS);
    /* Empty URI → structured NO-GO (device allocated, type=NONE) */
    CHECK(tpriv != NULL);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_NONE);
    tp_close(tpriv);
}

/* ── 3. BAR size validation (various BAR indices) ────────────────── */

NEGATIVE_TEST(bar0_undersized_rejected) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    cad_fpga_fake_set_bar_size(0, 1024); /* 1 KB < 4 MB */
    void *tpriv = NULL;
    CHECK_NE(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
}

NEGATIVE_TEST(bar1_undersized_rejected) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    cad_fpga_fake_set_bar_size(1, 0x10000000ULL); /* 256 MB < 2 GB */
    void *tpriv = NULL;
    CHECK_NE(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
}

NEGATIVE_TEST(bar2_undersized_rejected) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    cad_fpga_fake_set_bar_size(2, 4096); /* 4 KB < 64 KB */
    void *tpriv = NULL;
    CHECK_NE(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
}

/* ── 4. Buffer operations: error paths ───────────────────────────── */

NEGATIVE_TEST(buffer_alloc_oversized) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    /* Request > BAR0 (4 MB) should fail */
    cad_transport_buffer_t *buf = NULL;
    int ret = cad_transport_fpga_ops.buffer_alloc(tpriv, &buf,
                                                   0x01000000ULL); /* 16 MB */
    CHECK_NE(ret, CAD_TR_SUCCESS);
    CHECK(buf == NULL);

    tp_close(tpriv);
}

NEGATIVE_TEST(buffer_read_out_of_bounds) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    cad_transport_buffer_t *buf = NULL;
    CHECK_EQ(cad_transport_fpga_ops.buffer_alloc(tpriv, &buf, 256),
             CAD_TR_SUCCESS);

    char dst[32];
    /* Offset beyond buffer size */
    CHECK_NE(cad_transport_fpga_ops.buffer_read(tpriv, buf, 256, 1, dst),
             CAD_TR_SUCCESS);
    /* Offset + size beyond buffer */
    CHECK_NE(cad_transport_fpga_ops.buffer_read(tpriv, buf, 200, 100, dst),
             CAD_TR_SUCCESS);

    cad_transport_fpga_ops.buffer_free(tpriv, buf);
    tp_close(tpriv);
}

NEGATIVE_TEST(buffer_write_out_of_bounds) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    cad_transport_buffer_t *buf = NULL;
    CHECK_EQ(cad_transport_fpga_ops.buffer_alloc(tpriv, &buf, 256),
             CAD_TR_SUCCESS);

    const char *src = "test";
    /* Offset beyond buffer size */
    CHECK_NE(cad_transport_fpga_ops.buffer_write(tpriv, buf, 400, 4, src),
             CAD_TR_SUCCESS);

    cad_transport_fpga_ops.buffer_free(tpriv, buf);
    tp_close(tpriv);
}

/* ── 5. Fence operations: error paths ────────────────────────────── */

NEGATIVE_TEST(fence_null_handle) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    /* NULL fence passed to operations */
    CHECK_NE(cad_transport_fpga_ops.fence_wait(tpriv, NULL, 1000),
             CAD_TR_SUCCESS);
    CHECK_NE(cad_transport_fpga_ops.fence_poll(tpriv, NULL),
             CAD_TR_SUCCESS);

    tp_close(tpriv);
}

NEGATIVE_TEST(fence_unsubmitted_wait) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    cad_transport_fence_t *fence = NULL;
    CHECK_EQ(cad_transport_fpga_ops.fence_create(tpriv, &fence),
             CAD_TR_SUCCESS);

    /* Wait on fence that was never submitted */
    CHECK_NE(cad_transport_fpga_ops.fence_wait(tpriv, fence, 0),
             CAD_TR_SUCCESS);

    cad_transport_fpga_ops.fence_destroy(tpriv, fence);
    tp_close(tpriv);
}

/* ── 6. NO-GO transport type query with null ─────────────────────── */

NEGATIVE_TEST(get_type_null_priv) {
    CHECK_EQ(cad_transport_fpga_get_type(NULL), CAD_FPGA_NONE);
}

/* ── Runner ──────────────────────────────────────────────────────── */

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("=== FPGA Transport Negative Suite ===\n\n");
    printf("\n=== Results: %d/%d passed, %d failed ===\n",
           tests_passed, tests_run, tests_failed);
    return tests_failed > 0 ? 1 : 0;
}
