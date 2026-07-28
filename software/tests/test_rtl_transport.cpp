/*
 * RTL Transport Conformance — C++ tests (FEASIBILITY-ONLY).
 *
 * Tests the RTL transport skeleton against fake fixtures.
 * Real SoC RTL replay is deferred.
 *
 * Test matrix:
 *   1. preflight_vcs_missing      — rtl:// with VCS absent → NO-GO
 *   2. preflight_simv_missing     — rtl:// with simv absent → NO-GO
 *   3. preflight_both_missing     — both absent → NO-GO
 *   4. mock_connect_requires_server — rtl://mock needs a running endpoint (skip if none)
 *   5. vtable_structure           — vtable has all 14 function pointers
 */

#include "caduceus/transport_rtl.h"
#include "caduceus/cad_transport.h"

#include <stdio.h>
#include <string.h>

static int g_tests_passed = 0;
static int g_tests_failed = 0;

#define TEST(name)                                                      \
    static void test_##name(void);                                      \
    static void test_##name(void)

#define CHECK(cond, msg)                                                \
    do {                                                                \
        if (cond) {                                                     \
            g_tests_passed++;                                           \
        } else {                                                        \
            fprintf(stderr, "FAIL [%s:%d]: %s\n", __func__, __LINE__, (msg)); \
            g_tests_failed++;                                           \
        }                                                               \
    } while (0)

/* ── Preflight tests ──────────────────────────────────────────────────────── */

TEST(preflight_vcs_missing) {
    void *tpriv = NULL;

    /* Disable fake fixture to get real preflight path */
    cad_rtl_set_fake_fixture(0);

    /* Simulate VCS not found */
    cad_rtl_set_missing_eda(1); /* mode=1: no VCS */

    int err = cad_transport_rtl_init(&tpriv, "rtl://");
    CHECK(err == CAD_TR_ERR_UNSUP, "VCS missing should return ERR_UNSUP");
    CHECK(tpriv == NULL, "tpriv should be NULL on NO-GO");

    /* Clean up */
    cad_rtl_set_missing_eda(0); /* restore */
    cad_rtl_set_fake_fixture(1); /* re-enable fake fixture */
}

TEST(preflight_simv_missing) {
    void *tpriv = NULL;

    cad_rtl_set_fake_fixture(0);
    cad_rtl_set_missing_eda(2); /* mode=2: no simv */

    int err = cad_transport_rtl_init(&tpriv, "rtl://");
    CHECK(err == CAD_TR_ERR_UNSUP, "simv missing should return ERR_UNSUP");
    CHECK(tpriv == NULL, "tpriv should be NULL on NO-GO");

    cad_rtl_set_missing_eda(0);
    cad_rtl_set_fake_fixture(1);
}

TEST(preflight_both_missing) {
    void *tpriv = NULL;

    cad_rtl_set_fake_fixture(0);
    cad_rtl_set_missing_eda(3); /* mode=3: both absent */

    int err = cad_transport_rtl_init(&tpriv, "rtl://");
    CHECK(err == CAD_TR_ERR_UNSUP, "both missing should return ERR_UNSUP");
    CHECK(tpriv == NULL, "tpriv should be NULL on NO-GO");

    cad_rtl_set_missing_eda(0);
    cad_rtl_set_fake_fixture(1);
}

TEST(preflight_uri_null) {
    void *tpriv = NULL;
    int err = cad_transport_rtl_init(&tpriv, NULL);
    CHECK(err == CAD_TR_ERR_INVAL, "NULL URI should return ERR_INVAL");
}

TEST(preflight_unsupported_uri) {
    void *tpriv = NULL;
    int err = cad_transport_rtl_init(&tpriv, "rtl://bogus?x=y");
    CHECK(err == CAD_TR_ERR_INVAL, "bogus URI should return ERR_INVAL");
}

/* ── Vtable structure check ───────────────────────────────────────────────── */

TEST(vtable_has_all_functions) {
    CHECK(cad_transport_rtl_ops.name != NULL, "vtable name not NULL");
    CHECK(strcmp(cad_transport_rtl_ops.name, "RTL") == 0, "vtable name is 'RTL'");
    CHECK(cad_transport_rtl_ops.device_init != NULL, "device_init set");
    CHECK(cad_transport_rtl_ops.device_fini != NULL, "device_fini set");
    CHECK(cad_transport_rtl_ops.device_reset != NULL, "device_reset set");
    CHECK(cad_transport_rtl_ops.buffer_alloc != NULL, "buffer_alloc set");
    CHECK(cad_transport_rtl_ops.buffer_free != NULL, "buffer_free set");
    CHECK(cad_transport_rtl_ops.buffer_read != NULL, "buffer_read set");
    CHECK(cad_transport_rtl_ops.buffer_write != NULL, "buffer_write set");
    CHECK(cad_transport_rtl_ops.buffer_size != NULL, "buffer_size set");
    CHECK(cad_transport_rtl_ops.fence_create != NULL, "fence_create set");
    CHECK(cad_transport_rtl_ops.fence_destroy != NULL, "fence_destroy set");
    CHECK(cad_transport_rtl_ops.fence_wait != NULL, "fence_wait set");
    CHECK(cad_transport_rtl_ops.fence_poll != NULL, "fence_poll set");
    CHECK(cad_transport_rtl_ops.fence_status != NULL, "fence_status set");
    CHECK(cad_transport_rtl_ops.submit != NULL, "submit set");
}

TEST(fake_fixture_toggle) {
    /* Verify that the fake fixture control functions exist and can be called
     * without crashing.  These are global-state, so just exercise them. */
    cad_rtl_set_fake_fixture(1);
    cad_rtl_set_fake_fixture(0);
    cad_rtl_set_missing_eda(0);
    cad_rtl_set_missing_eda(1);
    cad_rtl_set_missing_eda(2);
    cad_rtl_set_missing_eda(3);
    cad_rtl_set_missing_eda(0); /* restore */
    cad_rtl_set_fake_fixture(1);
    g_tests_passed++; /* if we get here without crash, it's a pass */
}

/* ── Submit blob-forwarding test ──────────────────────────────────────────── */

TEST(submit_populates_cmd_blob) {
    cad_rtl_set_capture_mode(1);

    uint8_t payload[28];
    uint32_t *hdr = (uint32_t *)payload;
    hdr[0] = 2;
    hdr[1] = 1;
    hdr[2] = 3;
    for (int i = 12; i < 28; i++) {
        payload[i] = (uint8_t)i;
    }

    int err = cad_transport_rtl_ops.submit(
        (void *)&err /* any non-NULL tpriv is safe in capture mode */,
        payload, 28, NULL);
    CHECK(err == CAD_TR_SUCCESS, "submit in capture mode returns SUCCESS");

    uint32_t blob_size = 0;
    const uint8_t *blob = (const uint8_t *)cad_rtl_get_last_submit_blob(&blob_size);
    CHECK(blob != NULL, "captured blob not NULL");
    CHECK(blob_size == 28, "captured blob size = 28");

    int match = (blob != NULL) ? (memcmp(blob, payload, 28) == 0) : 0;
    CHECK(match, "captured blob bytes match payload");

    if (blob) {
        const uint32_t *chdr = (const uint32_t *)blob;
        CHECK(chdr[0] == 2, "captured nop_count = 2");
        CHECK(chdr[1] == 1, "captured blob_count = 1");
        CHECK(chdr[2] == 3, "captured total_cmd_count = 3");
    } else {
        g_tests_failed += 3;
    }

    cad_rtl_set_capture_mode(0);
}

/* ── Runner ───────────────────────────────────────────────────────────────── */

#include <stdint.h>

int main(void) {
    test_preflight_vcs_missing();
    test_preflight_simv_missing();
    test_preflight_both_missing();
    test_preflight_uri_null();
    test_preflight_unsupported_uri();
    test_vtable_has_all_functions();
    test_fake_fixture_toggle();
    test_submit_populates_cmd_blob();

    printf("RTL transport conformance: %d passed, %d failed\n",
           g_tests_passed, g_tests_failed);
    return g_tests_failed > 0 ? 1 : 0;
}
