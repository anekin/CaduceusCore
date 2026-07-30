/*
 * test_fm_transport_blob.cpp — FM Transport cmd_blob Forwarding Test
 *
 * Verifies the command serialization pipeline (W2-T7) correctly reaches
 * the transport layer via the Runtime API.  Uses the mock transport to
 * capture and inspect the serialized submit payload.
 *
 * The fm_submit() fix (W2-T9) is verified at the integration level by
 * test_submit_with_blob in sim/tests/test_device_protocol_cpp.py, which
 * exercises the full FM transport over a Unix socket with the Python
 * device server.
 */

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "caduceus/runtime.h"
#include "caduceus/transport_mock_test.h"

int main() {
    printf("=== CaduceusCore FM Transport cmd_blob Test ===\n\n");

    cad_mock_reset();
    cad_mock_set_pending_ticks(0);

    cad_device_open_info_t oi = {0};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    cad_device_t dev = nullptr;
    cad_error_t err = cadDeviceOpen(&oi, &dev, &caps);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadDeviceOpen -> %s\n", cadErrorString(err));
        return 1;
    }

    cad_buffer_create_info_t bi = {0};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 256;
    cad_buffer_t buf = nullptr;
    err = cadBufferAllocate(dev, &bi, &buf);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadBufferAllocate -> %s\n", cadErrorString(err));
        cadDeviceClose(dev); return 1;
    }

    uint8_t data[256];
    for (int i = 0; i < 256; i++) data[i] = (uint8_t)(i + 1);
    err = cadBufferWrite(buf, 0, 256, data);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadBufferWrite -> %s\n", cadErrorString(err));
        cadBufferFree(buf); cadDeviceClose(dev); return 1;
    }

    cad_command_list_create_info_t ci = {0};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = nullptr;
    err = cadCommandListCreate(dev, &ci, &cl);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadCommandListCreate -> %s\n", cadErrorString(err));
        cadBufferFree(buf); cadDeviceClose(dev); return 1;
    }

    /* 2 NOPs + 1 ExecuteBlob(offset=10, size=50) */
    if (cadCommandListAppendNop(cl) != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: appendNop\n"); return 1;
    }
    if (cadCommandListAppendNop(cl) != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: appendNop\n"); return 1;
    }
    if (cadCommandListAppendExecuteBlob(cl, buf, 10, 50) != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: appendExecuteBlob\n"); return 1;
    }

    cad_queue_create_info_t qi = {0};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = nullptr;
    err = cadQueueCreate(dev, &qi, &queue);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadQueueCreate -> %s\n", cadErrorString(err));
        cadCommandListDestroy(cl); cadBufferFree(buf); cadDeviceClose(dev); return 1;
    }

    cad_fence_create_info_t fi = {0};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = nullptr;
    err = cadFenceCreate(dev, &fi, &fence);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadFenceCreate -> %s\n", cadErrorString(err));
        cadQueueDestroy(queue); cadCommandListDestroy(cl);
        cadBufferFree(buf); cadDeviceClose(dev); return 1;
    }

    /* Critical: cadQueueSubmit serializes and calls transport.submit().
     * The mock captures the payload for inspection. */
    err = cadQueueSubmit(queue, cl, fence);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadQueueSubmit -> %s\n", cadErrorString(err));
        cadCommandListDestroy(cl); cadFenceDestroy(fence);
        cadQueueDestroy(queue); cadBufferFree(buf); cadDeviceClose(dev); return 1;
    }

    cadCommandListDestroy(cl);

    /* Verify captured payload */
    uint32_t size = 0;
    const uint8_t *payload =
        (const uint8_t *)cad_mock_get_last_submit_payload(&size);
    if (!payload || size == 0) {
        fprintf(stderr, "FAIL: no submit payload captured\n"); return 1;
    }
    if (size != 12 + 50) {
        fprintf(stderr, "FAIL: expected payload size %d, got %u\n", 12 + 50, size);
        return 1;
    }

    const uint32_t *hdr = (const uint32_t *)payload;
    if (hdr[0] != 2) { fprintf(stderr, "FAIL: nop_count %u != 2\n", hdr[0]); return 1; }
    if (hdr[1] != 1) { fprintf(stderr, "FAIL: blob_count %u != 1\n", hdr[1]); return 1; }
    if (hdr[2] != 3) { fprintf(stderr, "FAIL: total_cmd %u != 3\n", hdr[2]); return 1; }

    const uint8_t *blob = payload + 12;
    for (int i = 0; i < 50; i++) {
        if (blob[i] != data[10 + i]) {
            fprintf(stderr, "FAIL: blob[%d]=%02x != expected %02x\n",
                    i, blob[i], data[10 + i]);
            return 1;
        }
    }

    err = cadFenceWait(fence, CAD_TIMEOUT_INFINITE);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "FAIL: cadFenceWait -> %s\n", cadErrorString(err)); return 1;
    }

    cad_fence_status_t fs = CAD_FENCE_NOT_READY;
    cadFenceGetStatus(fence, &fs);
    if (fs != CAD_FENCE_COMPLETED) {
        fprintf(stderr, "FAIL: fence status=%d\n", (int)fs); return 1;
    }

    cadFenceDestroy(fence);
    cadQueueDestroy(queue);
    cadBufferFree(buf);
    cadDeviceClose(dev);

    printf("PASS: submit payload captured — nop=2 blob=1 total=3, "
           "blob bytes verified, fence completed\n");
    return 0;
}
