/*
 * test_fpga_transport_conformance.cpp — C++ FPGA Transport Conformance
 *
 * Tests the FPGA transport's fake fixtures (VFIO, UIO, vendor, no-device)
 * and verifies:
 *   1. Platform inventory matches expected parameters
 *   2. VFIO path: open, buffer ops, interrupt-based fence
 *   3. UIO path:  open, buffer ops, poll-based fence
 *   4. VENDOR path: same as VFIO semantics
 *   5. NO-DEVICE path: structured NO-GO (not PASS)
 *   6. BAR size validation (too-small BAR rejected)
 *   7. Buffer read-after-write data integrity
 *   8. Device reset zeroes BAR backing store
 *
 * All tests use fake fixtures; no real PCIe hardware is required.
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

#define CONFORMANCE_TEST(name) \
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

/* ── Helpers ─────────────────────────────────────────────────────── */

/* Open transport directly (not through runtime core) */
static int tp_open(void **tpriv, const char *uri) {
    return cad_transport_fpga_init(tpriv, uri);
}

/* Close: call device_fini through vtable, then free allocation */
static void tp_close(void *tpriv) {
    if (tpriv) {
        cad_transport_fpga_ops.device_fini(tpriv);
        free(tpriv);
    }
}

/* ── 1. Platform inventory ──────────────────────────────────────── */

CONFORMANCE_TEST(inventory_identity) {
    const cad_transport_fpga_inventory_t *inv =
        cad_transport_fpga_default_inventory();
    CHECK(inv != NULL);
    CHECK_EQ(inv->identity.vendor_id, 0xCAFE);
    CHECK_EQ(inv->identity.device_id, 0xBEEF);
    CHECK_EQ(inv->identity.pci_bus, 0x01);
    CHECK_EQ(inv->identity.pci_device, 0x00);
}

CONFORMANCE_TEST(inventory_bars) {
    const cad_transport_fpga_inventory_t *inv =
        cad_transport_fpga_default_inventory();
    CHECK(inv->bar_count >= 3);

    CHECK_EQ(inv->bars[0].index, 0);
    CHECK_EQ(inv->bars[0].min_size, 0x00400000ULL);

    CHECK_EQ(inv->bars[1].index, 1);
    CHECK_EQ(inv->bars[1].min_size, 0x80000000ULL);

    CHECK_EQ(inv->bars[2].index, 2);
    CHECK_EQ(inv->bars[2].min_size, 0x00010000ULL);
}

/* ── 2. VFIO path ────────────────────────────────────────────────── */

CONFORMANCE_TEST(vfio_open_and_close) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
    CHECK(tpriv != NULL);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_VFIO);
    tp_close(tpriv);
}

CONFORMANCE_TEST(vfio_buffer_lifecycle) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    const cad_transport_ops_t *ops = &cad_transport_fpga_ops;

    cad_transport_buffer_t *buf = NULL;
    CHECK_EQ(ops->buffer_alloc(tpriv, &buf, 4096), CAD_TR_SUCCESS);
    CHECK(buf != NULL);

    const char *src = "Hello FPGA VFIO!";
    CHECK_EQ(ops->buffer_write(tpriv, buf, 0, strlen(src) + 1, src),
             CAD_TR_SUCCESS);

    char dst[64] = {0};
    CHECK_EQ(ops->buffer_read(tpriv, buf, 0, strlen(src) + 1, dst),
             CAD_TR_SUCCESS);
    CHECK_EQ(strcmp(dst, src), 0);

    CHECK_EQ(ops->buffer_size(tpriv, buf), (uint64_t)4096);

    ops->buffer_free(tpriv, buf);
    tp_close(tpriv);
}

CONFORMANCE_TEST(vfio_fence_interrupt_path) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    const cad_transport_ops_t *ops = &cad_transport_fpga_ops;

    cad_transport_fence_t *fence = NULL;
    CHECK_EQ(ops->fence_create(tpriv, &fence), CAD_TR_SUCCESS);
    CHECK_EQ(ops->fence_status(tpriv, fence), 0);

    CHECK_EQ(ops->submit(tpriv, NULL, 1, fence), CAD_TR_SUCCESS);

    /* VFIO = interrupt path: wait resolves immediately */
    CHECK_EQ(ops->fence_wait(tpriv, fence, 0xFFFFFFFFFFFFFFFFULL),
             CAD_TR_SUCCESS);
    CHECK_EQ(ops->fence_status(tpriv, fence), 1);

    ops->fence_destroy(tpriv, fence);
    tp_close(tpriv);
}

/* ── 3. UIO path (poll-based) ────────────────────────────────────── */

CONFORMANCE_TEST(uio_open_and_type) {
    cad_fpga_set_fake_type(CAD_FPGA_UIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_UIO);
    tp_close(tpriv);
}

CONFORMANCE_TEST(uio_poll_unsignalled) {
    cad_fpga_set_fake_type(CAD_FPGA_UIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    const cad_transport_ops_t *ops = &cad_transport_fpga_ops;

    cad_transport_fence_t *fence = NULL;
    CHECK_EQ(ops->fence_create(tpriv, &fence), CAD_TR_SUCCESS);
    CHECK_EQ(ops->submit(tpriv, NULL, 1, fence), CAD_TR_SUCCESS);

    /* UIO = poll mode: immediate poll returns NOT_READY */
    CHECK_EQ(ops->fence_poll(tpriv, fence), CAD_TR_ERR_NOTREADY);
    /* Immediate timeout also returns NOT_READY */
    CHECK_EQ(ops->fence_wait(tpriv, fence, 0), CAD_TR_ERR_NOTREADY);

    ops->fence_destroy(tpriv, fence);
    tp_close(tpriv);
}

CONFORMANCE_TEST(uio_wait_resolves) {
    cad_fpga_set_fake_type(CAD_FPGA_UIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    const cad_transport_ops_t *ops = &cad_transport_fpga_ops;

    cad_transport_fence_t *fence = NULL;
    CHECK_EQ(ops->fence_create(tpriv, &fence), CAD_TR_SUCCESS);
    CHECK_EQ(ops->submit(tpriv, NULL, 1, fence), CAD_TR_SUCCESS);

    /* Non-zero timeout resolves on wait */
    CHECK_EQ(ops->fence_wait(tpriv, fence, 1000000), CAD_TR_SUCCESS);
    CHECK_EQ(ops->fence_status(tpriv, fence), 1);

    ops->fence_destroy(tpriv, fence);
    tp_close(tpriv);
}

/* ── 4. VENDOR path ──────────────────────────────────────────────── */

CONFORMANCE_TEST(vendor_open_and_type) {
    cad_fpga_set_fake_type(CAD_FPGA_VENDOR);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_VENDOR);
    tp_close(tpriv);
}

CONFORMANCE_TEST(vendor_fence_interrupt_like_vfio) {
    cad_fpga_set_fake_type(CAD_FPGA_VENDOR);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    const cad_transport_ops_t *ops = &cad_transport_fpga_ops;

    cad_transport_fence_t *fence = NULL;
    CHECK_EQ(ops->fence_create(tpriv, &fence), CAD_TR_SUCCESS);
    CHECK_EQ(ops->submit(tpriv, NULL, 1, fence), CAD_TR_SUCCESS);

    CHECK_EQ(ops->fence_wait(tpriv, fence, 0xFFFFFFFFFFFFFFFFULL),
             CAD_TR_SUCCESS);

    ops->fence_destroy(tpriv, fence);
    tp_close(tpriv);
}

/* ── 5. NO-DEVICE path ───────────────────────────────────────────── */

CONFORMANCE_TEST(no_device_nogo) {
    cad_fpga_set_fake_type(CAD_FPGA_NONE);
    void *tpriv = NULL;
    int ret = tp_open(&tpriv, "fpga://");

    /* Structured NO-GO: init fails, but priv allocated for query */
    CHECK_NE(ret, CAD_TR_SUCCESS);
    CHECK_EQ(ret, CAD_TR_ERR_UNSUP);
    CHECK(tpriv != NULL);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_NONE);
    tp_close(tpriv);
}

/* ── 6. BAR size validation ──────────────────────────────────────── */

CONFORMANCE_TEST(bar_validation_passes_default_sizes) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
    tp_close(tpriv);
}

CONFORMANCE_TEST(bar_validation_accepts_larger_bars) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    cad_fpga_fake_set_bar_size(0, 0x00800000ULL); /* 8 MB > 4 MB min */
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
    tp_close(tpriv);
}

CONFORMANCE_TEST(bar_validation_rejects_undersized) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    cad_fpga_fake_set_bar_size(0, 0x00100000ULL); /* 1 MB < 4 MB min */
    void *tpriv = NULL;
    CHECK_NE(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);
}

/* ── 7. Device reset ─────────────────────────────────────────────── */

CONFORMANCE_TEST(device_reset_zeroes_bars) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    const cad_transport_ops_t *ops = &cad_transport_fpga_ops;

    cad_transport_buffer_t *buf = NULL;
    CHECK_EQ(ops->buffer_alloc(tpriv, &buf, 256), CAD_TR_SUCCESS);

    const char *data = "before-reset";
    CHECK_EQ(ops->buffer_write(tpriv, buf, 0, strlen(data) + 1, data),
             CAD_TR_SUCCESS);

    char pre[256] = {0};
    CHECK_EQ(ops->buffer_read(tpriv, buf, 0, (uint64_t)strlen(data) + 1, pre),
             CAD_TR_SUCCESS);
    CHECK_EQ(pre[0], 'b');

    CHECK_EQ(ops->device_reset(tpriv), CAD_TR_SUCCESS);

    char post[256] = {0};
    memset(post, 0xFF, sizeof(post));
    CHECK_EQ(ops->buffer_read(tpriv, buf, 0, 256, post), CAD_TR_SUCCESS);
    CHECK_EQ(post[0], '\0');

    ops->buffer_free(tpriv, buf);
    tp_close(tpriv);
}

/* ── 8. Multiple submissions ────────────────────────────────────── */

CONFORMANCE_TEST(multiple_submits) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://"), CAD_TR_SUCCESS);

    const cad_transport_ops_t *ops = &cad_transport_fpga_ops;

    for (int i = 0; i < 5; i++) {
        cad_transport_fence_t *fence = NULL;
        CHECK_EQ(ops->fence_create(tpriv, &fence), CAD_TR_SUCCESS);
        CHECK_EQ(ops->submit(tpriv, NULL, (uint32_t)(i + 1), fence),
                 CAD_TR_SUCCESS);
        CHECK_EQ(ops->fence_wait(tpriv, fence, 0xFFFFFFFFFFFFFFFFULL),
                 CAD_TR_SUCCESS);
        ops->fence_destroy(tpriv, fence);
    }

    tp_close(tpriv);
}

/* ── 9. Transport metadata ──────────────────────────────────────── */

CONFORMANCE_TEST(transport_name_is_fpga) {
    CHECK_EQ(strcmp(cad_transport_fpga_ops.name, "FPGA"), 0);
}

CONFORMANCE_TEST(get_type_null_returns_none) {
    CHECK_EQ(cad_transport_fpga_get_type(NULL), CAD_FPGA_NONE);
}

/* ── 10. URI variants ────────────────────────────────────────────── */

CONFORMANCE_TEST(uri_vfio_explicit) {
    cad_fpga_set_fake_type(CAD_FPGA_VFIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://vfio?bdf=01:00.0"), CAD_TR_SUCCESS);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_VFIO);
    tp_close(tpriv);
}

CONFORMANCE_TEST(uri_uio_explicit) {
    cad_fpga_set_fake_type(CAD_FPGA_UIO);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://uio?uio=0"), CAD_TR_SUCCESS);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_UIO);
    tp_close(tpriv);
}

CONFORMANCE_TEST(uri_vendor_explicit) {
    cad_fpga_set_fake_type(CAD_FPGA_VENDOR);
    void *tpriv = NULL;
    CHECK_EQ(tp_open(&tpriv, "fpga://vendor?plugin=fake"),
             CAD_TR_SUCCESS);
    CHECK_EQ(cad_transport_fpga_get_type(tpriv), CAD_FPGA_VENDOR);
    tp_close(tpriv);
}

/* ── Runner ─────────────────────────────────────────────────────── */

int main(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("=== FPGA Transport Conformance Suite ===\n\n");
    printf("\n=== Results: %d/%d passed, %d failed ===\n",
           tests_passed, tests_run, tests_failed);
    return tests_failed > 0 ? 1 : 0;
}
