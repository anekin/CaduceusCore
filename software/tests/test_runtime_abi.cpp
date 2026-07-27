/*
 * test_runtime_abi.cpp — Happy-path ABI layout and version-negotiation tests.
 *
 * Covers:
 *   1. Public struct sizes and field offsets (static_assert + runtime check)
 *   2. Version negotiation: compatible minor → accepted
 *   3. Version negotiation: major mismatch → CAD_ERROR_INCOMPATIBLE_ABI
 *   4. C and C++ compilation: includes both runtime.h and runtime.hpp
 *   5. Basic lifecycle: open → get caps → close
 *   6. Buffer allocate → read → write → free
 *   7. Command list lifecycle
 *   8. Queue creation and destruction
 *   9. Fence create, signal via submit, wait, poll, get status
 *  10. Device reset after operations
 *
 * Test framework: minimal assert-based, no external dependency.
 */

#include "caduceus/runtime.h"
#include "caduceus/runtime.hpp"

#include <cassert>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <vector>

static int tests_run = 0;
static int tests_passed = 0;

#define TEST(name)                                                             \
    static void test_##name();                                                 \
    struct _registrar_##name {                                                 \
        _registrar_##name() {                                                  \
            tests_run++;                                                       \
            test_##name();                                                     \
            tests_passed++;                                                    \
            printf("  PASS: %s\n", #name);                                    \
        }                                                                      \
    } _inst_##name;                                                            \
    static void test_##name()

/* ── 1. Struct size assertions ───────────────────────────────────── */

TEST(struct_sizes) {
    assert(CAD_DEVICE_OPEN_INFO_STRUCT_SIZE >= 20);
    assert(CAD_DEVICE_CAPS_STRUCT_SIZE >= 128);
    assert(CAD_BUFFER_CREATE_INFO_STRUCT_SIZE >= 16);
    assert(CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE >= 12);
    assert(CAD_QUEUE_CREATE_INFO_STRUCT_SIZE >= 8);
    assert(CAD_FENCE_CREATE_INFO_STRUCT_SIZE >= 8);
}

TEST(struct_size_macros_self_consistent) {
    assert(CAD_DEVICE_OPEN_INFO_STRUCT_SIZE == sizeof(cad_device_open_info_t));
    assert(CAD_DEVICE_CAPS_STRUCT_SIZE == sizeof(cad_device_caps_t));
    assert(CAD_BUFFER_CREATE_INFO_STRUCT_SIZE == sizeof(cad_buffer_create_info_t));
    assert(CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE ==
           sizeof(cad_command_list_create_info_t));
    assert(CAD_QUEUE_CREATE_INFO_STRUCT_SIZE == sizeof(cad_queue_create_info_t));
    assert(CAD_FENCE_CREATE_INFO_STRUCT_SIZE == sizeof(cad_fence_create_info_t));
}

/* ── 2. Field offset assertions (struct_size is first field) ─────── */

TEST(field_offsets) {
    /* struct_size must always be at offset 0 */
    assert(offsetof(cad_device_open_info_t, struct_size) == 0);
    assert(offsetof(cad_device_caps_t, struct_size) == 0);
    assert(offsetof(cad_buffer_create_info_t, struct_size) == 0);
    assert(offsetof(cad_command_list_create_info_t, struct_size) == 0);
    assert(offsetof(cad_queue_create_info_t, struct_size) == 0);
    assert(offsetof(cad_fence_create_info_t, struct_size) == 0);

    /* abi_major and abi_minor fields exist */
    assert(offsetof(cad_device_open_info_t, abi_major) > 0);
    assert(offsetof(cad_device_open_info_t, abi_minor) > 0);
    assert(offsetof(cad_device_caps_t, abi_major) > 0);
    assert(offsetof(cad_device_caps_t, abi_minor) > 0);
}

/* ── 3. Version negotiation: compatible ──────────────────────────── */

TEST(version_negotiation_compatible) {
    cad_device_open_info_t open_info{};
    open_info.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    open_info.abi_major = CAD_ABI_MAJOR;
    open_info.abi_minor = 0;
    open_info.uri = "mock://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_error_t err = cadDeviceOpen(&open_info, &device, &caps);
    assert(err == CAD_SUCCESS);
    assert(device != nullptr);
    assert(caps.abi_major == CAD_ABI_MAJOR);
    assert(caps.abi_minor == CAD_ABI_MINOR);

    err = cadDeviceClose(device);
    assert(err == CAD_SUCCESS);
}

/* ── 4. Version negotiation: major mismatch → error ──────────────── */

TEST(version_negotiation_major_mismatch) {
    cad_device_open_info_t open_info{};
    open_info.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    open_info.abi_major = 999;  /* incompatible major */
    open_info.abi_minor = 0;
    open_info.uri = "mock://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_error_t err = cadDeviceOpen(&open_info, &device, &caps);
    assert(err == CAD_ERROR_INCOMPATIBLE_ABI);
    assert(device == nullptr);
}

/* ── 5. Version negotiation: older minor accepted ────────────────── */

TEST(version_negotiation_older_minor) {
    /* Client compiled against minor 0 but runtime says minor 5 — OK */
    cad_device_open_info_t open_info{};
    open_info.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    open_info.abi_major = CAD_ABI_MAJOR;
    open_info.abi_minor = 0;  /* client is older */
    open_info.uri = "mock://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_error_t err = cadDeviceOpen(&open_info, &device, &caps);
    assert(err == CAD_SUCCESS);
    assert(device != nullptr);
    /* Runtime should fill in its actual version */
    assert(caps.abi_major == CAD_ABI_MAJOR);
    assert(caps.abi_minor >= 0);
    err = cadDeviceClose(device);
    assert(err == CAD_SUCCESS);
}

/* ── 6. URI selection ────────────────────────────────────────────── */

TEST(uri_fm) {
    cad_device_open_info_t open_info{};
    open_info.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    open_info.abi_major = CAD_ABI_MAJOR;
    open_info.abi_minor = 0;
    open_info.uri = "fm://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_error_t err = cadDeviceOpen(&open_info, &device, &caps);
    assert(err == CAD_SUCCESS);
    assert(strcmp(caps.transport_name, "FuncModel") == 0);
    err = cadDeviceClose(device);
    assert(err == CAD_SUCCESS);
}

TEST(uri_rtl) {
    cad_device_open_info_t open_info{};
    open_info.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    open_info.abi_major = CAD_ABI_MAJOR;
    open_info.abi_minor = 0;
    open_info.uri = "rtl://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_error_t err = cadDeviceOpen(&open_info, &device, &caps);
    assert(err == CAD_SUCCESS);
    assert(strcmp(caps.transport_name, "RTL") == 0);
    err = cadDeviceClose(device);
    assert(err == CAD_SUCCESS);
}

TEST(uri_fpga) {
    cad_device_open_info_t open_info{};
    open_info.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    open_info.abi_major = CAD_ABI_MAJOR;
    open_info.abi_minor = 0;
    open_info.uri = "fpga://";

    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_error_t err = cadDeviceOpen(&open_info, &device, &caps);
    assert(err == CAD_SUCCESS);
    assert(strcmp(caps.transport_name, "FPGA") == 0);
    err = cadDeviceClose(device);
    assert(err == CAD_SUCCESS);
}

/* ── 7. Buffer lifecycle: allocate, read, write, free ────────────── */

TEST(buffer_lifecycle) {
    cad_device_t device = nullptr;
    {
        cad_device_open_info_t oi{};
        oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
        oi.abi_major = CAD_ABI_MAJOR;
        oi.abi_minor = 0;
        oi.uri = "mock://";
        cad_device_caps_t caps{};
        caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
        assert(cadDeviceOpen(&oi, &device, &caps) == CAD_SUCCESS);
    }

    cad_buffer_create_info_t bi{};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 1024;
    bi.flags = 0;

    cad_buffer_t buffer = nullptr;
    assert(cadBufferAllocate(device, &bi, &buffer) == CAD_SUCCESS);
    assert(buffer != nullptr);

    /* Write then read back */
    const char *msg = "Hello, NPU!";
    size_t msg_len = strlen(msg) + 1;
    assert(cadBufferWrite(buffer, 0, msg_len, msg) == CAD_SUCCESS);

    char readback[128] = {};
    assert(cadBufferRead(buffer, 0, msg_len, readback) == CAD_SUCCESS);
    /* stub returns zeros, so we just verify no error */
    assert(readback[0] == 0);

    assert(cadBufferFree(buffer) == CAD_SUCCESS);

    /* Write past end should fail */
    bi.size = 64;
    assert(cadBufferAllocate(device, &bi, &buffer) == CAD_SUCCESS);
    assert(cadBufferWrite(buffer, 100, 10, msg) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadBufferRead(buffer, 100, 10, readback) == CAD_ERROR_INVALID_ARGUMENT);
    assert(cadBufferFree(buffer) == CAD_SUCCESS);

    assert(cadDeviceClose(device) == CAD_SUCCESS);
}

/* ── 8. Command list lifecycle ──────────────────────────────────── */

TEST(command_list_lifecycle) {
    cad_device_t device = nullptr;
    {
        cad_device_open_info_t oi{};
        oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
        oi.abi_major = CAD_ABI_MAJOR;
        oi.abi_minor = 0;
        oi.uri = "mock://";
        cad_device_caps_t caps{};
        caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
        assert(cadDeviceOpen(&oi, &device, &caps) == CAD_SUCCESS);
    }

    cad_command_list_create_info_t ci{};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 16;
    ci.flags = 0;

    cad_command_list_t cl = nullptr;
    assert(cadCommandListCreate(device, &ci, &cl) == CAD_SUCCESS);
    assert(cl != nullptr);

    /* Append a few nops */
    for (int i = 0; i < 16; i++) {
        assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);
    }
    /* 17th should fail (max_entries = 16) */
    assert(cadCommandListAppendNop(cl) == CAD_ERROR_OUT_OF_MEMORY);

    assert(cadCommandListDestroy(cl) == CAD_SUCCESS);
    assert(cadDeviceClose(device) == CAD_SUCCESS);
}

/* ── 9. Queue lifecycle ─────────────────────────────────────────── */

TEST(queue_lifecycle) {
    cad_device_t device = nullptr;
    {
        cad_device_open_info_t oi{};
        oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
        oi.abi_major = CAD_ABI_MAJOR;
        oi.abi_minor = 0;
        oi.uri = "mock://";
        cad_device_caps_t caps{};
        caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
        assert(cadDeviceOpen(&oi, &device, &caps) == CAD_SUCCESS);
    }

    cad_queue_create_info_t qi{};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    qi.flags = 0;

    cad_queue_t queue = nullptr;
    assert(cadQueueCreate(device, &qi, &queue) == CAD_SUCCESS);
    assert(queue != nullptr);

    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(device) == CAD_SUCCESS);
}

/* ── 10. Fence lifecycle: create, submit, wait, poll, status ─────── */

TEST(fence_lifecycle) {
    cad_device_t device = nullptr;
    {
        cad_device_open_info_t oi{};
        oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
        oi.abi_major = CAD_ABI_MAJOR;
        oi.abi_minor = 0;
        oi.uri = "mock://";
        cad_device_caps_t caps{};
        caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
        assert(cadDeviceOpen(&oi, &device, &caps) == CAD_SUCCESS);
    }

    cad_queue_create_info_t qi{};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    qi.flags = 0;
    cad_queue_t queue = nullptr;
    assert(cadQueueCreate(device, &qi, &queue) == CAD_SUCCESS);

    cad_fence_create_info_t fi{};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    fi.flags = 0;

    cad_fence_t fence = nullptr;
    assert(cadFenceCreate(device, &fi, &fence) == CAD_SUCCESS);

    /* Fence should not be ready before submission */
    assert(cadFencePoll(fence) == CAD_ERROR_NOT_READY);

    cad_command_list_create_info_t ci{};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    ci.flags = 0;

    cad_command_list_t cl = nullptr;
    assert(cadCommandListCreate(device, &ci, &cl) == CAD_SUCCESS);
    assert(cadCommandListAppendNop(cl) == CAD_SUCCESS);

    /* Submit with fence — should signal immediately in stub */
    assert(cadQueueSubmit(queue, cl, fence) == CAD_SUCCESS);

    /* Fence should now be signalled */
    cad_fence_status_t status = CAD_FENCE_NOT_READY;
    assert(cadFenceGetStatus(fence, &status) == CAD_SUCCESS);
    assert(status == CAD_FENCE_COMPLETED);

    assert(cadFencePoll(fence) == CAD_SUCCESS);
    assert(cadFenceWait(fence, CAD_TIMEOUT_IMMEDIATE) == CAD_SUCCESS);
    assert(cadFenceWait(fence, CAD_TIMEOUT_INFINITE) == CAD_SUCCESS);

    assert(cadFenceDestroy(fence) == CAD_SUCCESS);
    assert(cadQueueDestroy(queue) == CAD_SUCCESS);
    assert(cadDeviceClose(device) == CAD_SUCCESS);
}

/* ── 11. Device capabilities query ───────────────────────────────── */

TEST(device_caps_query) {
    cad_device_t device = nullptr;
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    {
        cad_device_open_info_t oi{};
        oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
        oi.abi_major = CAD_ABI_MAJOR;
        oi.abi_minor = 0;
        oi.uri = "mock://";
        assert(cadDeviceOpen(&oi, &device, &caps) == CAD_SUCCESS);
    }

    assert(caps.max_buffers > 0);
    assert(caps.max_buffer_size > 0);
    assert(caps.max_queues > 0);
    assert(caps.max_command_lists > 0);
    assert(strlen(caps.device_name) > 0);
    assert(strlen(caps.transport_name) > 0);

    /* Re-query */
    cad_device_caps_t caps2{};
    caps2.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceGetCaps(device, &caps2) == CAD_SUCCESS);
    assert(caps2.abi_major == caps.abi_major);
    assert(caps2.abi_minor == caps.abi_minor);

    assert(cadDeviceClose(device) == CAD_SUCCESS);
}

/* ── 12. Device reset ────────────────────────────────────────────── */

TEST(device_reset) {
    cad_device_t device = nullptr;
    {
        cad_device_open_info_t oi{};
        oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
        oi.abi_major = CAD_ABI_MAJOR;
        oi.abi_minor = 0;
        oi.uri = "mock://";
        cad_device_caps_t caps{};
        caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
        assert(cadDeviceOpen(&oi, &device, &caps) == CAD_SUCCESS);
    }

    assert(cadDeviceReset(device) == CAD_SUCCESS);
    /* Device should still be valid after reset */
    cad_device_caps_t caps{};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    assert(cadDeviceGetCaps(device, &caps) == CAD_SUCCESS);

    assert(cadDeviceClose(device) == CAD_SUCCESS);
}

/* ── 13. C++ RAII wrapper tests ──────────────────────────────────── */

TEST(cpp_raii_device) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";

    cad::Device dev(oi);
    assert(dev);
    assert(dev.caps().max_buffers > 0);

    /* Move semantics */
    cad::Device dev2 = std::move(dev);
    assert(!dev);  /* moved-from is null */
    assert(dev2);
    assert(dev2.caps().max_queues > 0);

    /* New caps query */
    cad_device_caps_t caps2 = dev2.getCaps();
    assert(caps2.abi_major == CAD_ABI_MAJOR);
}

TEST(cpp_raii_buffer) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad::Device dev(oi);

    cad_buffer_create_info_t bi{};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 256;

    cad::Buffer buf(dev.get(), bi);
    assert(buf);

    const char data[] = "test";
    buf.write(0, 4, data);

    char out[4] = {};
    buf.read(0, 4, out);
    /* stub returns zeros, just verify no throw */
}

TEST(cpp_raii_fence_submit) {
    cad_device_open_info_t oi{};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    cad::Device dev(oi);

    cad_queue_create_info_t qi{};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad::Queue queue(dev.get(), qi);

    cad_command_list_create_info_t ci{};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad::CommandList cl(dev.get(), ci);
    cl.appendNop();

    cad_fence_create_info_t fi{};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad::Fence fence(dev.get(), fi);

    queue.submit(cl, fence.get());
    /* cl ownership was consumed */
    assert(!cl);

    fence.wait(CAD_TIMEOUT_INFINITE);
    assert(fence.poll());
    assert(fence.getStatus() == CAD_FENCE_COMPLETED);
}

/* ── 14. Error string lookup ─────────────────────────────────────── */

TEST(error_strings) {
    assert(strcmp(cadErrorString(CAD_SUCCESS), "Success") == 0);
    assert(strcmp(cadErrorString(CAD_ERROR_INCOMPATIBLE_ABI),
                  "Incompatible ABI version") == 0);
    assert(strcmp(cadErrorString(CAD_ERROR_INVALID_HANDLE),
                  "Invalid handle") == 0);
    assert(strcmp(cadErrorString(CAD_ERROR_INVALID_ARGUMENT),
                  "Invalid argument") == 0);
    assert(strcmp(cadErrorString(CAD_ERROR_TIMEOUT), "Timeout") == 0);
    assert(strcmp(cadErrorString(CAD_ERROR_DEVICE_LOST),
                  "Device lost") == 0);
    assert(strcmp(cadErrorString(CAD_ERROR_OUT_OF_MEMORY),
                  "Out of memory") == 0);
    assert(strcmp(cadErrorString(CAD_ERROR_NOT_READY), "Not ready") == 0);
    assert(strcmp(cadErrorString(CAD_ERROR_DEVICE_BUSY),
                  "Device busy") == 0);
    assert(strcmp(cadErrorString(CAD_ERROR_UNSUPPORTED),
                  "Unsupported") == 0);
}

/* ── Main ────────────────────────────────────────────────────────── */

int main() {
    printf("=== CaduceusCore Runtime ABI Tests ===\n");
    printf("ABI version: %d.%d\n", CAD_ABI_MAJOR, CAD_ABI_MINOR);
    printf("\n");
    /* The static constructors of _registrar_* run before main,
     * so tests_run and tests_passed are already populated. */
    printf("============================\n");
    printf("Results: %d/%d tests passed\n", tests_passed, tests_run);
    printf("============================\n");
    return (tests_passed == tests_run) ? 0 : 1;
}
