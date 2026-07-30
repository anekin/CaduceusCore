/*
 * test_spike_uri.c — Verify fm://spike is accepted (I-009).
 *
 * W1T3: fm://spike must be recognised and routed to the FM transport.
 * fpga:// must remain explicitly rejected (W2-T8 regression guard).
 */

#include "caduceus/runtime.h"

#include <stdio.h>
#include <string.h>

static int check_not_unsupported(const char *uri, const char *label) {
    cad_device_open_info_t oi = {0};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = uri;

    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_device_t dev = NULL;
    cad_error_t err = cadDeviceOpen(&oi, &dev, &caps);

    if (err == CAD_ERROR_UNSUPPORTED) {
        fprintf(stderr, "FAIL [%s]: cadDeviceOpen(\"%s\") returned"
                " CAD_ERROR_UNSUPPORTED (should be accepted)\n",
                label, uri);
        return 1;
    }

    if (err == CAD_SUCCESS) {
        /* Connection succeeded — verify transport name */
        printf("  %-30s -> CAD_SUCCESS, transport=\"%s\"\n",
               label, caps.transport_name);

        /* Must be "FuncModel", NOT "FPGA" */
        if (strcmp(caps.transport_name, "FuncModel") != 0) {
            fprintf(stderr, "FAIL [%s]: expected transport \"FuncModel\", got \"%s\"\n",
                    label, caps.transport_name);
            cadDeviceClose(dev);
            return 1;
        }
        cadDeviceClose(dev);
    } else {
        /* Connection failed (no FM server) — that's expected in CI.
         * The point is the URI was NOT rejected as unsupported. */
        printf("  %-30s -> %s (accepted, connection failed as expected"
               " without server)\n",
               label, cadErrorString(err));
    }

    return 0;
}

static int check_unsupported(const char *uri) {
    cad_device_open_info_t oi = {0};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = uri;

    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_device_t dev = NULL;
    cad_error_t err = cadDeviceOpen(&oi, &dev, &caps);

    if (err != CAD_ERROR_UNSUPPORTED) {
        fprintf(stderr, "FAIL: cadDeviceOpen(\"%s\") should return"
                " CAD_ERROR_UNSUPPORTED, got %d (%s)\n",
                uri, err, cadErrorString(err));
        if (dev) cadDeviceClose(dev);
        return 1;
    }

    printf("  %-30s -> CAD_ERROR_UNSUPPORTED (preserved)\n", uri);

    const char *msg = cadErrorString(CAD_ERROR_UNSUPPORTED);
    if (msg == NULL || strstr(msg, "fpga") == NULL) {
        fprintf(stderr, "FAIL: error string does not mention 'fpga': \"%s\"\n",
                msg ? msg : "(null)");
        return 1;
    }

    if (dev != NULL) {
        fprintf(stderr, "FAIL: device handle should remain NULL for unsupported URI\n");
        return 1;
    }

    return 0;
}

int main(void) {
    int rc = 0;

    printf("=== CaduceusCore FM Spike URI Test (I-009) ===\n\n");

    /* ── Positive: fm://spike must be accepted ─────────────────── */
    rc |= check_not_unsupported("fm://spike", "fm://spike (I-009)");

    /* ── Positive: fm:// (bare) still works ────────────────────── */
    rc |= check_not_unsupported("fm://",       "fm:// (existing)");

    /* ── Positive: fm://python still works ─────────────────────── */
    rc |= check_not_unsupported("fm://python", "fm://python (existing)");

    /* ── Negative: fpga:// must still be rejected (W2-T8) ──────── */
    rc |= check_unsupported("fpga://any/path");

    printf("\n%s\n", rc == 0 ? "PASS" : "FAIL");
    return rc;
}
