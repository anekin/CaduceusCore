/*
 * test_runtime_abi_negative.cpp — Negative-path tests for the runtime ABI.
 *
 * Covers:
 *   1. Major version mismatch returns CAD_ERROR_INCOMPATIBLE_ABI
 *   2. Minor version too high returns CAD_ERROR_INCOMPATIBLE_ABI
 *   3. Invalid handles (NULL, stale, wrong type) rejected
 *   4. Invalid struct sizes rejected
 *   5. NULL output pointers rejected
 *   6. Unsupported URI schemes rejected
 *   7. Double-free detection
 *   8. Use-after-close on device
 *   9. Use-after-free on buffer
 *  10. Zero-size buffer allocation rejected
 *  11. Command list double-submit
 *  12. Submit with invalid/sentinel fence
 */

#include "caduceus/runtime.h"

#include <cassert>
#include <cstdio>
#include <cstring>

static int tests_run = 0;
static int tests_passed = 0;

#define NEG_TEST(name)                                                         \
    static void neg_##name();                                                  \
    struct _reg_##name {                                                       \
        _reg_##name() {                                                        \
            tests_run++;                                                       \
            neg_##name();                                                      \
            tests_passed++;                                                    \
            printf("  PASS: %s\n", #name);                                    \
        }                                                                      \
    } _inst_##name;                                                            \
    static void neg_##name()

/* ── 1. Major version mismatch ───────────────────────────────────── */

NEG_TEST(major_version_mismatch) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR + 1;  /* too high */
    oi.abi_minor = 0;
    oi.uri = "mock://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_error_t err = cadDeviceOpen(&oi, &device, &caps);
    assert(err == CAD_ERROR_INCOMPATIBLE_ABI);
    assert(device == nullptr);
}

/* ── 2. Minor version too high ───────────────────────────────────── */

NEG_TEST(minor_version_too_high) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = CAD_ABI_MINOR + 99;  /* higher than runtime */
    oi.uri = "mock://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_error_t err = cadDeviceOpen(&oi, &device, &caps);
    assert(err == CAD_ERROR_INCOMPATIBLE_ABI);
    assert(device == nullptr);
}

/* ── 3. NULL device pointer ──────────────────────────────────────── */

NEG_TEST(null_device_ptr) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";

    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    /* NULL device out-pointer */
    assert(cadDeviceOpen(&oi, nullptr, &caps) == CAD_ERROR_INVALID_ARGUMENT);

    /* NULL caps out-pointer */
    cad_device_t dev = nullptr;
    assert(cadDeviceOpen(&oi, &dev, nullptr) == CAD_ERROR_INVALID_ARGUMENT);
    assert(dev == nullptr);
}

/* ── 4. Invalid struct_size (too small) ──────────────────────────── */

NEG_TEST(invalid_struct_size) {
    /* Pass a struct_size smaller than expected */
    cad_device_open_info_t oi{};
    oi.struct_size = 4;  /* way too small */
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    assert(cadDeviceOpen(&oi, &device, &caps) == CAD_ERROR_INVALID_ARGUMENT);
    assert(device == nullptr);
}

/* ── 5. NULL uri ─────────────────────────────────────────────────── */

NEG_TEST(null_uri) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = nullptr;

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    assert(cadDeviceOpen(&oi, &device, &caps) == CAD_ERROR_INVALID_ARGUMENT);
    assert(device == nullptr);
}

/* ── 6. Unsupported URI scheme ───────────────────────────────────── */

NEG_TEST(unsupported_uri_scheme) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "pcie://";  /* not one of fm://, rtl://, fpga://, mock:// */

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    assert(cadDeviceOpen(&oi, &device, &caps) == CAD_ERROR_INVALID_ARGUMENT);
    assert(device == nullptr);
}

/* ── 7. Use-after-close ──────────────────────────────────────────── */

NEG_TEST(use_after_close) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    assert(cadDeviceOpen(&oi, &device, &caps) == CAD_SUCCESS);
    assert(cadDeviceClose(device) == CAD_SUCCESS);

    /* Re-close should fail (already freed) */
    assert(cadDeviceClose(device) == CAD_ERROR_INVALID_HANDLE);

    /* Any operation on closed device should fail */
    assert(cadDeviceGetCaps(device, &caps) == CAD_ERROR_INVALID_HANDLE);
    assert(cadDeviceReset(device) == CAD_ERROR_INVALID_HANDLE);

    cad_buffer_create_info_t bi{};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 64;
    cad_buffer_t buf = nullptr;
    assert(cadBufferAllocate(device, &bi, &buf) == CAD_ERROR_INVALID_HANDLE);
}

/* ── 8. NULL/NULL-like buffer handle ─────────────────────────────── */

NEG_TEST(null_buffer_handle) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad_device_t dev = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceOpen(&oi, &dev, &caps) == CAD_SUCCESS);

    assert(cadBufferFree(nullptr) == CAD_ERROR_INVALID_HANDLE);

    char buf[16];
    assert(cadBufferRead(nullptr, 0, 4, buf) == CAD_ERROR_INVALID_HANDLE);
    assert(cadBufferWrite(nullptr, 0, 4, buf) == CAD_ERROR_INVALID_HANDLE);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 9. Zero-size buffer ─────────────────────────────────────────── */

NEG_TEST(zero_size_buffer) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad_device_t dev = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceOpen(&oi, &dev, &caps) == CAD_SUCCESS);

    cad_buffer_create_info_t bi{};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 0;  /* zero size */
    bi.flags = 0;

    cad_buffer_t buf = nullptr;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_ERROR_INVALID_ARGUMENT);
    assert(buf == nullptr);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 10. Submit consumed command list again ──────────────────────── */

NEG_TEST(submit_consumed_command_list) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad_device_t dev = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceOpen(&oi, &dev, &caps) == CAD_SUCCESS);

    cad_queue_create_info_t qi{};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = nullptr;
    assert(cadQueueCreate(dev, &qi, &queue) == CAD_SUCCESS);

    cad_command_list_create_info_t ci{};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = nullptr;
    assert(cadCommandListCreate(dev, &ci, &cl) == CAD_SUCCESS);

    cad_fence_create_info_t fi{};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = nullptr;
    assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);

    /* First submit: succeeds, ownership transferred */
    assert(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS);

    /* Second submit with same (now submitted) command list: must fail */
    assert(cadQueueSubmit(queue, cl, nullptr) == CAD_ERROR_INVALID_HANDLE);

    /* After W3T9 lifecycle fix, destroying a submitted command list succeeds. */
    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 11. Buffer use-after-free ───────────────────────────────────── */

NEG_TEST(buffer_use_after_free) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad_device_t dev = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceOpen(&oi, &dev, &caps) == CAD_SUCCESS);

    cad_buffer_create_info_t bi{};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 1024;
    cad_buffer_t buf = nullptr;
    assert(cadBufferAllocate(dev, &bi, &buf) == CAD_SUCCESS);
    assert(cadBufferFree(buf) == CAD_SUCCESS);

    /* Re-free should fail */
    assert(cadBufferFree(buf) == CAD_ERROR_INVALID_HANDLE);

    /* Read/write on freed buffer */
    char data[16];
    assert(cadBufferRead(buf, 0, 4, data) == CAD_ERROR_INVALID_HANDLE);
    assert(cadBufferWrite(buf, 0, 4, data) == CAD_ERROR_INVALID_HANDLE);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 12. Fence operations on NULL/invalid fence ──────────────────── */

NEG_TEST(null_fence_ops) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad_device_t dev = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceOpen(&oi, &dev, &caps) == CAD_SUCCESS);

    cad_fence_create_info_t fi{};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = nullptr;
    assert(cadFenceCreate(dev, &fi, &fence) == CAD_SUCCESS);

    /* Unsignalled fence: immediate wait should return not-ready */
    assert(cadFenceWait(fence, CAD_TIMEOUT_IMMEDIATE) == CAD_ERROR_NOT_READY);
    assert(cadFencePoll(fence) == CAD_ERROR_NOT_READY);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);

    /* Operations on freed fence */
    assert(cadFenceWait(fence, 0) == CAD_ERROR_INVALID_HANDLE);
    assert(cadFencePoll(fence) == CAD_ERROR_INVALID_HANDLE);

    cad_fence_status_t status;
    assert(cadFenceGetStatus(fence, &status) == CAD_ERROR_INVALID_HANDLE);

    /* Operations on NULL fence */
    assert(cadFenceWait(nullptr, 0) == CAD_ERROR_INVALID_HANDLE);
    assert(cadFencePoll(nullptr) == CAD_ERROR_INVALID_HANDLE);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── 13. Invalid NULL open_info ──────────────────────────────────── */

NEG_TEST(null_open_info) {
    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceOpen(nullptr, &device, &caps) == CAD_ERROR_INVALID_ARGUMENT);
    assert(device == nullptr);
}

/* ── 14. Capability query with NULL pointer ──────────────────────── */

NEG_TEST(caps_query_null) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad_device_t dev = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceOpen(&oi, &dev, &caps) == CAD_SUCCESS);

    assert(cadDeviceGetCaps(dev, nullptr) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadDeviceGetCaps(nullptr, &caps) == CAD_ERROR_INVALID_HANDLE);

    /* Also test with too-small struct */
    cad_device_caps_t small_caps{};
    small_caps.struct_size = 4;
    assert(cadDeviceGetCaps(dev, &small_caps) == CAD_ERROR_INVALID_ARGUMENT);

    assert(cadDeviceClose(dev) == CAD_SUCCESS);
}

/* ── Main ────────────────────────────────────────────────────────── */

int main() {
    printf("=== CaduceusCore Runtime ABI — Negative Tests ===\n");
    printf("ABI version: %d.%d\n", CAD_ABI_MAJOR, CAD_ABI_MINOR);
    printf("\n");
    printf("============================\n");
    printf("Results: %d/%d tests passed\n", tests_passed, tests_run);
    printf("============================\n");
    return (tests_passed == tests_run) ? 0 : 1;
}
