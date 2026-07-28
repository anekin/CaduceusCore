/*
 * test_runtime_conformance.cpp — C++ Conformance Tests (doctest)
 *
 * Tests the same conformance matrix as the C tests, but using the
 * C++ RAII wrappers from runtime.hpp.  Uses doctest for assertions.
 * Replace software/tests/doctest.h with the real doctest.h later.
 */

#include "doctest.h"
#include "caduceus/runtime.h"
#include "caduceus/runtime.hpp"
#include "caduceus/transport_mock_test.h"

#include <cstring>

/* Reset mock state before each test case */

static void setup(void) {
    cad_mock_reset();
    cad_mock_set_pending_ticks(0);
}

static cad::Device open_mock(void) {
    cad_mock_reset();
    cad_device_open_info_t oi = {};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major = CAD_ABI_MAJOR;
    oi.abi_minor = 0;
    oi.uri = "mock://";
    return cad::Device(oi);
}

/* ── Device ──────────────────────────────────────────────────────── */

TEST_CASE("device open and close") {
    setup();
    {
        cad::Device dev = open_mock();
        CHECK(!!dev);
        CHECK(dev.caps().max_buffers > 0);
        CHECK_EQ(std::strcmp(dev.caps().transport_name, "Mock"), 0);
    }
}

TEST_CASE("device caps re-query") {
    cad::Device dev = open_mock();
    cad_device_caps_t caps = dev.getCaps();
    CHECK_EQ(caps.abi_major, CAD_ABI_MAJOR);
    CHECK(caps.max_queues > 0);
}

TEST_CASE("device reset") {
    cad::Device dev = open_mock();
    dev.deviceReset();
    /* Still valid after reset */
    cad_device_caps_t caps = dev.getCaps();
    CHECK(caps.max_buffers > 0);
}

/* ── Buffer ──────────────────────────────────────────────────────── */

TEST_CASE("buffer write read") {
    setup();
    cad::Device dev = open_mock();

    cad_buffer_create_info_t bi = {};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 256;
    cad::Buffer buf(dev.get(), bi);
    CHECK(!!buf);

    const char *msg = "Hello from C++!";
    buf.write(0, std::strlen(msg) + 1, msg);

    char readback[256] = {};
    buf.read(0, std::strlen(msg) + 1, readback);
    CHECK_EQ(std::strcmp(readback, msg), 0);
}

TEST_CASE("buffer large write read") {
    setup();
    cad::Device dev = open_mock();

    cad_buffer_create_info_t bi = {};
    bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    bi.size = 4096;
    cad::Buffer buf(dev.get(), bi);

    char data[4096];
    for (int i = 0; i < 4096; i++) data[i] = (char)(i & 0xFF);
    buf.write(0, 4096, data);

    char readback[4096] = {};
    buf.read(0, 4096, readback);
    CHECK_EQ(std::memcmp(data, readback, 4096), 0);
}

/* ── Command list ────────────────────────────────────────────────── */

TEST_CASE("command list append nop") {
    setup();
    cad::Device dev = open_mock();

    cad_command_list_create_info_t ci = {};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 16;
    cad::CommandList cl(dev.get(), ci);

    for (int i = 0; i < 16; i++) {
        cl.appendNop();
    }
    /* 17th should throw */
    bool threw = false;
    try { cl.appendNop(); } catch (...) { threw = true; }
    CHECK(threw);
}

TEST_CASE("command list max entries default") {
    setup();
    cad::Device dev = open_mock();

    cad_command_list_create_info_t ci = {};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 0; /* use default */
    cad::CommandList cl(dev.get(), ci);
    CHECK(!!cl);
}

/* ── Queue + Fence ───────────────────────────────────────────────── */

TEST_CASE("queue submit with fence immediate") {
    setup();
    cad_mock_set_pending_ticks(0);
    cad::Device dev = open_mock();

    cad_queue_create_info_t qi = {};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad::Queue queue(dev.get(), qi);

    cad_command_list_create_info_t ci = {};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad::CommandList cl(dev.get(), ci);
    cl.appendNop();

    cad_fence_create_info_t fi = {};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad::Fence fence(dev.get(), fi);

    CHECK_FALSE(fence.poll());

    queue.submit(cl, fence.get());
    /* cl ownership was consumed */
    CHECK_FALSE(cl);

    CHECK(fence.poll());
    CHECK_EQ(fence.getStatus(), CAD_FENCE_COMPLETED);
    fence.wait(CAD_TIMEOUT_IMMEDIATE);
}

TEST_CASE("fence delayed completion") {
    setup();
    cad::Device dev = open_mock();
    cad_mock_set_pending_ticks(5);

    cad_queue_create_info_t qi = {};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad::Queue queue(dev.get(), qi);

    cad_command_list_create_info_t ci = {};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad::CommandList cl(dev.get(), ci);
    cl.appendNop();

    cad_fence_create_info_t fi = {};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad::Fence fence(dev.get(), fi);

    queue.submit(cl, fence.get());

    /* Not ready at 0 ticks, ready after 5 */
    CHECK_FALSE(fence.poll());
    cad_mock_advance_ticks(3);
    CHECK_FALSE(fence.poll());
    cad_mock_advance_ticks(2);
    CHECK(fence.poll());
    CHECK_EQ(fence.getStatus(), CAD_FENCE_COMPLETED);
}

TEST_CASE("fence infinite wait") {
    setup();
    cad::Device dev = open_mock();
    cad_mock_set_pending_ticks(3);

    cad_queue_create_info_t qi = {};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad::Queue queue(dev.get(), qi);

    cad_command_list_create_info_t ci = {};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad::CommandList cl(dev.get(), ci);
    cl.appendNop();

    cad_fence_create_info_t fi = {};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad::Fence fence(dev.get(), fi);

    queue.submit(cl, fence.get());
    fence.wait(CAD_TIMEOUT_INFINITE);
    CHECK(fence.poll());
}

TEST_CASE("fence immediate timeout") {
    setup();
    cad::Device dev = open_mock();
    cad_mock_set_pending_ticks(10);

    cad_queue_create_info_t qi = {};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad::Queue queue(dev.get(), qi);

    cad_command_list_create_info_t ci = {};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad::CommandList cl(dev.get(), ci);
    cl.appendNop();

    cad_fence_create_info_t fi = {};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad::Fence fence(dev.get(), fi);

    queue.submit(cl, fence.get());

    /* Immediate wait should fail with timeout → not ready */
    CHECK_FALSE(fence.poll());
    /* Infinite wait resolves */
    fence.wait(CAD_TIMEOUT_INFINITE);
    CHECK(fence.poll());
}

/* ── Error injection ─────────────────────────────────────────────── */

TEST_CASE("submit error injection") {
    setup();
    cad::Device dev = open_mock();

    cad_queue_create_info_t qi = {};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad::Queue queue(dev.get(), qi);

    cad_command_list_create_info_t ci = {};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad::CommandList cl(dev.get(), ci);
    cl.appendNop();

    /* Inject device-lost on next submit */
    cad_mock_set_next_submit_error(CAD_TR_ERR_LOST);

    bool threw = false;
    try {
        cad_fence_create_info_t fi = {};
        fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
        cad::Fence fence(dev.get(), fi);
        queue.submit(cl, fence.get());
    } catch (const cad::RuntimeError &e) {
        CHECK_EQ(e.code(), CAD_ERROR_DEVICE_LOST);
        threw = true;
    }
    CHECK(threw);
}

/* ── Main (doctest-style) ────────────────────────────────────────── */

int main(void) {
    printf("=== CaduceusCore Runtime C++ Conformance (doctest) ===\n");
    printf("ABI: %d.%d\n\n", CAD_ABI_MAJOR, CAD_ABI_MINOR);

    auto &ctx = doctest::Context::instance();
    /* Test cases registered via constructors; they've already run */
    printf("\n==========================================\n");
    printf("Tests: %d/%d passed, %d/%d asserts passed\n",
           ctx.tests_passed, ctx.tests_run,
           ctx.asserts_passed, ctx.asserts_total);
    printf("==========================================\n");

    if (ctx.tests_passed < ctx.tests_run ||
        ctx.asserts_passed < ctx.asserts_total) {
        printf("=== FAILURES DETECTED ===\n");
        return 1;
    }
    return 0;
}
