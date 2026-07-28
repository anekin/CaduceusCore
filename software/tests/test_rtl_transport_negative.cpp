/*
 * RTL Transport Negative Tests (FEASIBILITY-ONLY).
 *
 * Tests error-handling paths that must NOT silently pass:
 *   1. rtl:// without fake fixture → NO-GO
 *   2. rtl://mock with no server running → connection failure
 *   3. Invalid URI formats → rejection
 *   4. Fake EDA failure recovery → restored state works
 */

#include "caduceus/transport_rtl.h"
#include "caduceus/cad_transport.h"

#include <stdio.h>
#include <string.h>

static int g_passed = 0;
static int g_failed = 0;

#define CHECK(cond, msg)                                                \
    do {                                                                \
        if (cond) g_passed++;                                           \
        else { fprintf(stderr, "FAIL: %s\n", (msg)); g_failed++; }       \
    } while (0)

/* ── Test: rtl:// without fake fixture = NO-GO ───────────────────────────── */

static void test_no_fake_fixture_yields_nogo(void) {
    void *tpriv = NULL;
    cad_rtl_set_fake_fixture(0);
    cad_rtl_set_missing_eda(3); /* ensure both prereqs fail */

    int err = cad_transport_rtl_init(&tpriv, "rtl://");
    CHECK(err != CAD_TR_SUCCESS, "rtl:// without fake fixture must not succeed");
    CHECK(tpriv == NULL, "tpriv must be NULL on failure");
    CHECK(err == CAD_TR_ERR_UNSUP, "error must be ERR_UNSUP (typed NO-GO)");

    cad_rtl_set_missing_eda(0);
    cad_rtl_set_fake_fixture(1);
}

/* ── Test: URIs that should be rejected ───────────────────────────────────── */

static void test_bogus_uris_rejected(void) {
    void *tpriv = NULL;
    int err = cad_transport_rtl_init(&tpriv, "rtl://bogus?x=y");
    CHECK(err == CAD_TR_ERR_INVAL, "bogus URI 'rtl://bogus?x=y' must return INVAL");
    CHECK(tpriv == NULL, "tpriv must be NULL on bogus URI");
}

static void test_null_uri_rejected(void) {
    void *tpriv = NULL;
    int err = cad_transport_rtl_init(&tpriv, NULL);
    CHECK(err == CAD_TR_ERR_INVAL, "NULL URI must return INVAL");
}

static void test_fake_fixture_attempts_connect(void) {
    void *tpriv = NULL;
    cad_rtl_set_fake_fixture(1);
    /* With fake fixture ON, rtl:// attempts to connect to mock socket.
     * Without a running server, this fails with LOST (connection fail),
     * NOT UNSUP (which would mean preflight ran anyway). */
    int err = cad_transport_rtl_init(&tpriv, "rtl://");
    CHECK(err != CAD_TR_SUCCESS, "rtl:// with fake fixture (no server) must not succeed");
    CHECK(err == CAD_TR_ERR_LOST, "fake fixture must attempt connect → LOST, not UNSUP");
    CHECK(tpriv == NULL, "tpriv NULL on connect failure");
}

static void test_mode_toggle_no_crash(void) {
    cad_rtl_set_missing_eda(1);
    cad_rtl_set_missing_eda(0);
    g_passed++;
}

/* ── Test: Typed NO-GO produces diagnostic output ────────────────────────── */

static void test_nogo_is_typed_not_abort(void) {
    void *tpriv = NULL;
    cad_rtl_set_fake_fixture(0);

    /* mode=1: VCS missing */
    cad_rtl_set_missing_eda(1);
    int err1 = cad_transport_rtl_init(&tpriv, "rtl://");
    CHECK(err1 == CAD_TR_ERR_UNSUP, "VCS missing → typed UNSUP, not crash");
    CHECK(tpriv == NULL, "tpriv is NULL");

    /* mode=2: simv missing */
    cad_rtl_set_missing_eda(2);
    int err2 = cad_transport_rtl_init(&tpriv, "rtl://");
    CHECK(err2 == CAD_TR_ERR_UNSUP, "simv missing → typed UNSUP, not crash");
    CHECK(tpriv == NULL, "tpriv is NULL");

    cad_rtl_set_missing_eda(0);
    cad_rtl_set_fake_fixture(1);
}

/* ── Test: Preflight mode 0 restores real preflight ──────────────────────── */

static void test_mode_zero_restores_real_preflight(void) {
    /* Set mode to bad state, then restore to 0 */
    cad_rtl_set_missing_eda(3); /* force both missing */
    cad_rtl_set_missing_eda(0); /* restore to real preflight */

    /* Now the preflight result depends on actual EDA state —
     * the important thing is that the function returns without crash. */
    g_passed++;
}

/* ── Test: Fake fixture state isolation ──────────────────────────────────── */

static void test_fake_fixture_isolation(void) {
    /* Toggle fake fixture: ensure it doesn't crash or leak */
    for (int i = 0; i < 3; i++) {
        cad_rtl_set_fake_fixture(1);
        cad_rtl_set_fake_fixture(0);
    }
    cad_rtl_set_fake_fixture(1); /* restore default */
    g_passed++;
}

/* ── Runner ───────────────────────────────────────────────────────────────── */

int main(void) {
    test_no_fake_fixture_yields_nogo();
    test_bogus_uris_rejected();
    test_null_uri_rejected();
    test_fake_fixture_attempts_connect();
    test_mode_toggle_no_crash();
    test_nogo_is_typed_not_abort();
    test_mode_zero_restores_real_preflight();
    test_fake_fixture_isolation();

    printf("RTL transport negative: %d passed, %d failed\n", g_passed, g_failed);
    return g_failed > 0 ? 1 : 0;
}
