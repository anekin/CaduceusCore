/*
 * ExecuTorch NPU Backend Runtime Tests
 *
 * Covers: init, execute, buffer errors, runtime failures, blob validation.
 * Reuses the shared Host Runtime (mock transport) — no second transport stack.
 */

#include "caduceus_npu_backend.h"

#include "command_ir.h"

#include "doctest.h"
#include "caduceus/transport_mock_test.h"

#include <cstring>

/* ── Test helpers ─────────────────────────────────────────────────── */

static cad_device_t open_mock_device(void) {
    cad_device_open_info_t info = {0};
    info.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    info.abi_major = CAD_ABI_MAJOR;
    info.abi_minor = CAD_ABI_MINOR;
    info.uri = "mock://";

    cad_device_caps_t caps = {0};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;

    cad_device_t device = NULL;
    cad_error_t err = cadDeviceOpen(&info, &device, &caps);
    REQUIRE(err == CAD_SUCCESS);
    REQUIRE(device != NULL);

    /* Reset mock state for test isolation */
    cad_mock_reset();
    return device;
}

/* Build a minimal valid blob using the shared command IR */
static void build_test_blob(uint8_t **out_buf, size_t *out_size) {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    REQUIRE(blob != nullptr);

    cad_buffer_declare(blob, 2560, 64, 0x80000000);
    cad_buffer_declare(blob, 12800, 64, 0x80010000);
    cad_buffer_declare(blob, 10240, 64, 0x80020000);
    cad_buffer_declare(blob, 2560, 64, 0x80030000);

    cad_buffer_id_t deps[] = {};
    REQUIRE(cad_op_mmul(blob, 1, 2, 3, 4, 1, 2560, 2560, 0, deps) == 0);

    REQUIRE(cad_command_blob_lower(blob) == CAD_LOWER_OK);

    REQUIRE(cad_command_blob_encode(blob, out_buf, out_size) == 0);
    cad_command_blob_destroy(blob);
}

/* ── Test cases ───────────────────────────────────────────────────── */

TEST_CASE("Backend init succeeds with valid device") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);
    CHECK(cad_et_backend_get_last_error(be) != NULL);

    cad_et_status_t st = cad_et_backend_destroy(be);
    CHECK(st == CAD_ET_OK);
    cadDeviceClose(device);
}

TEST_CASE("Backend init fails with NULL device") {
    cad_et_backend_t be = cad_et_backend_init(NULL);
    CHECK(be == NULL);
}

TEST_CASE("Backend loads and executes a valid blob") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    /* Build a valid blob */
    uint8_t *blob_data = NULL;
    size_t blob_size = 0;
    build_test_blob(&blob_data, &blob_size);
    REQUIRE(blob_data != NULL);

    /* Load the blob */
    cad_et_status_t st = cad_et_backend_load_blob(be, blob_data, blob_size);
    CHECK(st == CAD_ET_OK);

    /* Allocate and bind buffers */
    cad_buffer_t buffers[4] = {NULL};
    for (uint32_t i = 1; i <= 4; i++) {
        cad_buffer_create_info_t binfo = {0};
        binfo.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
        binfo.size = 65536;
        cad_buffer_t buf = NULL;
        REQUIRE(cadBufferAllocate(device, &binfo, &buf) == CAD_SUCCESS);
        REQUIRE(cad_et_backend_bind_buffer(be, i, buf) == CAD_ET_OK);
        buffers[i - 1] = buf;
    }

    /* Execute */
    st = cad_et_backend_execute(be);
    CHECK(st == CAD_ET_OK);

    cad_et_backend_destroy(be);
    for (int i = 0; i < 4; i++) {
        if (buffers[i]) cadBufferFree(buffers[i]);
    }
    cad_command_blob_encoded_free(blob_data);
    cadDeviceClose(device);
}

TEST_CASE("Execute fails when no blob is loaded") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    cad_et_status_t st = cad_et_backend_execute(be);
    CHECK(st == CAD_ET_NOT_INITIALIZED);

    cad_et_backend_destroy(be);
    cadDeviceClose(device);
}

TEST_CASE("Execute fails with unbound buffers") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    uint8_t *blob_data = NULL;
    size_t blob_size = 0;
    build_test_blob(&blob_data, &blob_size);
    REQUIRE(blob_data != NULL);

    cad_et_status_t st = cad_et_backend_load_blob(be, blob_data, blob_size);
    CHECK(st == CAD_ET_OK);

    /* Execute without binding — should fail */
    st = cad_et_backend_execute(be);
    CHECK(st == CAD_ET_BUFFER_ERROR);

    cad_et_backend_destroy(be);
    cad_command_blob_encoded_free(blob_data);
    cadDeviceClose(device);
}

TEST_CASE("Reject blob with bad magic") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    /* Create a blob and then mutate the magic bytes */
    uint8_t *blob_data = NULL;
    size_t blob_size = 0;
    build_test_blob(&blob_data, &blob_size);
    REQUIRE(blob_data != NULL);

    /* Corrupt magic */
    blob_data[0] = 0xDE;
    blob_data[1] = 0xAD;
    blob_data[2] = 0xBE;
    blob_data[3] = 0xEF;

    cad_et_status_t st = cad_et_backend_load_blob(be, blob_data, blob_size);
    CHECK(st == CAD_ET_BLOB_MAGIC_BAD);

    const char *err = cad_et_backend_get_last_error(be);
    CHECK(err != NULL);
    CHECK(strlen(err) > 0);

    cad_et_backend_destroy(be);
    cad_command_blob_encoded_free(blob_data);
    cadDeviceClose(device);
}

TEST_CASE("Reject blob with version mismatch") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    uint8_t *blob_data = NULL;
    size_t blob_size = 0;
    build_test_blob(&blob_data, &blob_size);
    REQUIRE(blob_data != NULL);

    /* Corrupt version to major=99, minor=0 */
    blob_data[4] = 0x63; /* 99 */
    blob_data[5] = 0x00;
    blob_data[6] = 0x00;
    blob_data[7] = 0x00;

    cad_et_status_t st = cad_et_backend_load_blob(be, blob_data, blob_size);
    CHECK((st == CAD_ET_BLOB_VERSION_MISMATCH || st == CAD_ET_BLOB_MAGIC_BAD));

    cad_et_backend_destroy(be);
    cad_command_blob_encoded_free(blob_data);
    cadDeviceClose(device);
}

TEST_CASE("Backend load_blob rejects NULL data") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    cad_et_status_t st = cad_et_backend_load_blob(be, NULL, 100);
    CHECK(st == CAD_ET_INVALID_ARGUMENT);

    cad_et_backend_destroy(be);
    cadDeviceClose(device);
}

TEST_CASE("Backend load_blob rejects zero size") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    uint8_t dummy = 0;
    cad_et_status_t st = cad_et_backend_load_blob(be, &dummy, 0);
    CHECK(st == CAD_ET_INVALID_ARGUMENT);

    cad_et_backend_destroy(be);
    cadDeviceClose(device);
}

TEST_CASE("Status string returns non-null for all codes") {
    for (int i = 0; i <= 10; i++) {
        const char *s = cad_et_status_string((cad_et_status_t)i);
        CHECK(s != NULL);
        CHECK(strlen(s) > 0);
    }
    CHECK(strcmp(cad_et_status_string(CAD_ET_OK), "OK") == 0);
    CHECK(strcmp(cad_et_status_string(CAD_ET_BUFFER_ERROR), "buffer error") == 0);
    CHECK(strcmp(cad_et_status_string(CAD_ET_NOT_INITIALIZED), "not initialized") == 0);
}

TEST_CASE("Backend unbind buffer works") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    /* Load a blob first — binding requires loaded blob */
    uint8_t *blob_data = NULL;
    size_t blob_size = 0;
    build_test_blob(&blob_data, &blob_size);
    REQUIRE(cad_et_backend_load_blob(be, blob_data, blob_size) == CAD_ET_OK);

    /* Allocate and bind */
    cad_buffer_create_info_t binfo = {0};
    binfo.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    binfo.size = 4096;
    cad_buffer_t buf = NULL;
    REQUIRE(cadBufferAllocate(device, &binfo, &buf) == CAD_SUCCESS);

    CHECK(cad_et_backend_bind_buffer(be, 1, buf) == CAD_ET_OK);
    CHECK(cad_et_backend_unbind_buffer(be, 1) == CAD_ET_OK);

    /* Unbind invalid id should not crash */
    CHECK(cad_et_backend_unbind_buffer(be, 0) == CAD_ET_BUFFER_ERROR);

    cad_et_backend_destroy(be);
    cadBufferFree(buf);
    cad_command_blob_encoded_free(blob_data);
    cadDeviceClose(device);
}

TEST_CASE("Backend execute propagates runtime submit error") {
    cad_device_t device = open_mock_device();
    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    uint8_t *blob_data = NULL;
    size_t blob_size = 0;
    build_test_blob(&blob_data, &blob_size);
    REQUIRE(blob_data != NULL);

    CHECK(cad_et_backend_load_blob(be, blob_data, blob_size) == CAD_ET_OK);

    /* Allocate and bind buffers */
    cad_buffer_t buffers[4] = {NULL};
    for (uint32_t i = 1; i <= 4; i++) {
        cad_buffer_create_info_t binfo = {0};
        binfo.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
        binfo.size = 65536;
        cad_buffer_t buf = NULL;
        REQUIRE(cadBufferAllocate(device, &binfo, &buf) == CAD_SUCCESS);
        REQUIRE(cad_et_backend_bind_buffer(be, i, buf) == CAD_ET_OK);
        buffers[i - 1] = buf;
    }

    /* Inject a fault: next submit returns DEVICE_BUSY */
    cad_mock_set_next_submit_error(CAD_ERROR_DEVICE_BUSY);

    cad_et_status_t st = cad_et_backend_execute(be);
    CHECK(st == CAD_ET_EXECUTE_ERROR);

    const char *err = cad_et_backend_get_last_error(be);
    CHECK(err != NULL);

    cad_et_backend_destroy(be);
    for (int i = 0; i < 4; i++) {
        if (buffers[i]) cadBufferFree(buffers[i]);
    }
    cad_command_blob_encoded_free(blob_data);
    cadDeviceClose(device);
}

TEST_CASE("Backend re-uses shared Runtime — proved by mock op log") {
    cad_device_t device = open_mock_device();
    cad_mock_reset();

    cad_et_backend_t be = cad_et_backend_init(device);
    REQUIRE(be != NULL);

    uint8_t *blob_data = NULL;
    size_t blob_size = 0;
    build_test_blob(&blob_data, &blob_size);
    REQUIRE(cad_et_backend_load_blob(be, blob_data, blob_size) == CAD_ET_OK);

    /* Bind buffers */
    cad_buffer_t buffers[4] = {NULL};
    for (uint32_t i = 1; i <= 4; i++) {
        cad_buffer_create_info_t binfo = {0};
        binfo.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
        binfo.size = 65536;
        cad_buffer_t buf = NULL;
        REQUIRE(cadBufferAllocate(device, &binfo, &buf) == CAD_SUCCESS);
        REQUIRE(cad_et_backend_bind_buffer(be, i, buf) == CAD_ET_OK);
        buffers[i - 1] = buf;
    }

    REQUIRE(cad_et_backend_execute(be) == CAD_ET_OK);

    /* Verify mock op log records the submit */
    uint32_t log_len = 0;
    const mock_op_log_entry_t *log = cad_mock_get_op_log(device, &log_len);
    REQUIRE(log != NULL);
    CHECK(log_len > 0);

    /* Should contain an "open" record (device) and "submit" for our execution */
    int has_open = 0, has_submit = 0;
    for (uint32_t i = 0; i < log_len; i++) {
        if (log[i].type == MOCK_OP_DEVICE_OPEN) has_open = 1;
        if (log[i].type == MOCK_OP_SUBMIT) has_submit = 1;
    }
    CHECK(has_open);
    CHECK(has_submit);

    cad_et_backend_destroy(be);
    for (int i = 0; i < 4; i++) {
        if (buffers[i]) cadBufferFree(buffers[i]);
    }
    cad_command_blob_encoded_free(blob_data);
    cadDeviceClose(device);
}

int main(void) {
    printf("=== CaduceusCore ExecuTorch Backend Tests ===\n");
    auto &ctx = doctest::Context::instance();
    printf("\n==========================================\n");
    printf("Tests: %d/%d passed, %d/%d asserts passed\n",
           ctx.tests_passed, ctx.tests_run,
           ctx.asserts_passed, ctx.asserts_total);
    printf("==========================================\n");
    if (ctx.tests_passed < ctx.tests_run ||
        ctx.asserts_passed < ctx.asserts_total) {
        return 1;
    }
    return 0;
}
