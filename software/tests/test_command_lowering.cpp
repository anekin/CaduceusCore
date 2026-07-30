/*
 * Valid-path tests for CaduceusCore command IR lowering.
 */

#include "command_ir.h"

#include "doctest.h"

#include <cstring>

TEST_CASE("MMUL lowers to a 60-byte descriptor with correct fields") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    REQUIRE(blob != nullptr);

    cad_buffer_id_t in = cad_buffer_declare(blob, 2560, 64, 0x80000000);
    cad_buffer_id_t w = cad_buffer_declare(blob, 20480, 64, 0x80010000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 10240, 64, 0x80020000);
    cad_buffer_id_t scale = cad_buffer_declare(blob, 2560, 64, 0x80030000);
    REQUIRE(in != CAD_BUFFER_INVALID);
    REQUIRE(w != CAD_BUFFER_INVALID);
    REQUIRE(out != CAD_BUFFER_INVALID);
    REQUIRE(scale != CAD_BUFFER_INVALID);

    int rc = cad_op_mmul(blob, in, w, out, scale, 1, 2560, 2560, 0, nullptr);
    REQUIRE(rc == 0);

    cad_lower_status_t st = cad_command_blob_lower(blob);
    REQUIRE(st == CAD_LOWER_OK);

    size_t desc_size = 0;
    const uint8_t *descs = cad_command_blob_descriptors(blob, &desc_size);
    REQUIRE(desc_size == 60);

    const uint32_t *d = reinterpret_cast<const uint32_t *>(descs);
    CHECK(d[0] == 0x80000000); /* input_addr */
    CHECK(d[1] == 0x80010000); /* weight_addr */
    CHECK(d[2] == 0x80020000); /* output_addr */
    CHECK(d[3] == 0x80030000); /* scale_addr */
    CHECK(d[12] == 1);
    CHECK(d[13] == 2560);
    CHECK(d[14] == 2560);

    size_t ring_size = 0;
    const uint8_t *ring = cad_command_blob_command_ring(blob, &ring_size);
    REQUIRE(ring_size == 32);
    const uint32_t *entry = reinterpret_cast<const uint32_t *>(ring);
    CHECK(entry[0] == CAD_OP_MMUL);
    CHECK(entry[1] == 0); /* descriptor offset */

    cad_command_blob_destroy(blob);
}

TEST_CASE("SFU descriptor carries dim, pos and sfu_op") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_SFU);
    cad_buffer_id_t in = cad_buffer_declare(blob, 5120, 64, 0x80000000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 5120, 64, 0x80001000);
    REQUIRE(cad_op_sfu(blob, CAD_OP_SFU_RMSNORM, in, out, 2560, 0, 0, 0, nullptr) == 0);

    REQUIRE(cad_command_blob_lower(blob) == CAD_LOWER_OK);

    size_t desc_size = 0;
    const uint32_t *d = reinterpret_cast<const uint32_t *>(
        cad_command_blob_descriptors(blob, &desc_size));
    CHECK(d[0] == 0x80000000);
    CHECK(d[2] == 0x80001000);
    CHECK(d[8] == 2560);
    CHECK(d[9] == 0);
    CHECK(d[10] == 6); /* RMSNORM */

    cad_command_blob_destroy(blob);
}

TEST_CASE("Vector descriptor carries operand addresses and element count") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_VECTOR);
    cad_buffer_id_t a = cad_buffer_declare(blob, 1024, 64, 0x80000000);
    cad_buffer_id_t b = cad_buffer_declare(blob, 1024, 64, 0x80001000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 1024, 64, 0x80002000);
    REQUIRE(cad_op_vector(blob, 0, a, b, out, 256, 0, nullptr) == 0);

    REQUIRE(cad_command_blob_lower(blob) == CAD_LOWER_OK);

    size_t desc_size = 0;
    const uint32_t *d = reinterpret_cast<const uint32_t *>(
        cad_command_blob_descriptors(blob, &desc_size));
    CHECK(d[0] == 0x80000000);
    CHECK(d[1] == 0x80001000);
    CHECK(d[2] == 0x80002000);
    CHECK(d[8] == 256);

    cad_command_blob_destroy(blob);
}

TEST_CASE("DMA_COPY descriptor uses physical addresses with offsets") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_DMA);
    cad_buffer_id_t src = cad_buffer_declare(blob, 4096, 64, 0x80000000);
    cad_buffer_id_t dst = cad_buffer_declare(blob, 4096, 64, 0);
    REQUIRE(cad_op_dma_copy(blob, src, 128, dst, 256, 512, 0, nullptr) == 0);

    REQUIRE(cad_command_blob_lower(blob) == CAD_LOWER_OK);

    size_t desc_size = 0;
    const uint32_t *d = reinterpret_cast<const uint32_t *>(
        cad_command_blob_descriptors(blob, &desc_size));
    CHECK(d[0] == 0x80000080); /* src + offset */
    CHECK(d[2] == 0x20000100); /* dst SRAM base + offset */
    CHECK(d[8] == 512);

    cad_command_blob_destroy(blob);
}

TEST_CASE("Internal scratch buffers are allocated deterministically") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t a = cad_buffer_declare(blob, 1024, 256, 0);
    cad_buffer_id_t b = cad_buffer_declare(blob, 512, 64, 0);
    REQUIRE(a != CAD_BUFFER_INVALID);
    REQUIRE(b != CAD_BUFFER_INVALID);

    REQUIRE(cad_command_blob_lower(blob) == CAD_LOWER_OK);

    size_t bt_size = 0;
    const uint64_t *bt = cad_command_blob_buffer_table(blob, &bt_size);
    REQUIRE(bt_size == 2 * 4 * sizeof(uint64_t));
    /* id, size, alignment, phys_addr */
    CHECK(bt[3] == 0x20000000);          /* a at SRAM base, aligned to 256 */
    CHECK(bt[7] == 0x20000400);          /* b after a (size 1024), aligned to 64 */

    cad_command_blob_destroy(blob);
}

TEST_CASE("Encoded blob round-trips through decoder") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU | CAD_CAP_SFU);
    cad_buffer_id_t in = cad_buffer_declare(blob, 2560, 64, 0x80000000);
    cad_buffer_id_t w = cad_buffer_declare(blob, 20480, 64, 0x80010000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 10240, 64, 0x80020000);
    cad_buffer_id_t tmp = cad_buffer_declare(blob, 5120, 64, 0);
    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 1, 2560, 2560, 0, nullptr) == 0);
    REQUIRE(cad_op_sfu(blob, CAD_OP_SFU_RMSNORM, out, tmp, 2560, 0, 0, 0, nullptr) == 0);
    REQUIRE(cad_op_barrier(blob) == 0);

    REQUIRE(cad_command_blob_lower(blob) == CAD_LOWER_OK);

    uint8_t *buf = nullptr;
    size_t size = 0;
    REQUIRE(cad_command_blob_encode(blob, &buf, &size) == 0);

    cad_command_blob_t *decoded = cad_command_blob_decode(buf, size);
    REQUIRE(decoded != nullptr);
    CHECK(cad_command_blob_version_major(decoded) == CAD_COMMAND_BLOB_MAJOR);
    CHECK(cad_command_blob_version_minor(decoded) == CAD_COMMAND_BLOB_MINOR);
    CHECK(cad_command_blob_num_commands(decoded) == 3);

    size_t desc_size = 0;
    const uint8_t *descs = cad_command_blob_descriptors(decoded, &desc_size);
    REQUIRE(desc_size == 2 * 60);
    CHECK(std::memcmp(descs,
                      cad_command_blob_descriptors(blob, &desc_size),
                      desc_size) == 0);

    cad_command_blob_encoded_free(buf);
    cad_command_blob_destroy(decoded);
    cad_command_blob_destroy(blob);
}

int main(void) {
    printf("=== CaduceusCore Command Lowering Valid Tests ===\n");
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
