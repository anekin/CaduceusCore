/*
 * test_unsupported_uri.c — Verify fpga:// is rejected explicitly.
 *
 * W2-T8: fpga:// must not silently fall back to mock. Opening it returns
 * CAD_ERROR_UNSUPPORTED, and the error string mentions "fpga".
 */

#include "caduceus/runtime.h"

#include <stdio.h>
#include <string.h>

int main(void) {
    int rc = 0;

    printf("=== CaduceusCore Unsupported URI Test ===\n");

    cad_device_open_info_t oi = {0};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "fpga://any/path";

    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_device_t dev = NULL;
    cad_error_t err = cadDeviceOpen(&oi, &dev, &caps);

    if (err != CAD_ERROR_UNSUPPORTED) {
        fprintf(stderr, "FAIL: expected CAD_ERROR_UNSUPPORTED, got %d\n", err);
        rc = 1;
    } else {
        printf("  cadDeviceOpen(\"fpga://...\") -> CAD_ERROR_UNSUPPORTED\n");
    }

    const char *msg = cadErrorString(CAD_ERROR_UNSUPPORTED);
    if (msg == NULL || strstr(msg, "fpga") == NULL) {
        fprintf(stderr, "FAIL: error string does not mention 'fpga': \"%s\"\n",
                msg ? msg : "(null)");
        rc = 1;
    } else {
        printf("  cadErrorString -> \"%s\"\n", msg);
    }

    if (dev != NULL) {
        fprintf(stderr, "FAIL: device handle should remain NULL\n");
        rc = 1;
    }

    printf("%s\n", rc == 0 ? "PASS" : "FAIL");
    return rc;
}
