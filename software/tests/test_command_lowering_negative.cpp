/*
 * Negative-path tests for CaduceusCore command IR lowering.
 */

#include "command_ir.h"

#include "doctest.h"

TEST_CASE("Zero dimension MMUL is rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t in = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t w = cad_buffer_declare(blob, 256, 64, 0x80001000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80002000);
    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 0, 256, 256, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_SHAPE);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Unsupported opcode rejected when capability missing") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU); /* no SFU */
    cad_buffer_id_t in = cad_buffer_declare(blob, 512, 64, 0x80000000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 512, 64, 0x80001000);
    REQUIRE(cad_op_sfu(blob, 0, in, out, 256, 0, 0, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_UNSUPPORTED_OP);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Misaligned external address rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    /* alignment=64 but address 0x80000010 is not 64-byte aligned */
    cad_buffer_id_t in = cad_buffer_declare(blob, 256, 64, 0x80000010);
    cad_buffer_id_t w = cad_buffer_declare(blob, 256, 64, 0x80010000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80020000);
    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 1, 16, 16, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_ALIGNMENT);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Overlapping internal scratch buffers rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t a = cad_buffer_declare(blob, 1024, 64, 0);
    cad_buffer_id_t b = cad_buffer_declare(blob, 1024, 64, 0);
    REQUIRE(a != CAD_BUFFER_INVALID);
    REQUIRE(b != CAD_BUFFER_INVALID);
    /* Force b to overlap a after a valid lower() run. */
    REQUIRE(cad_test_set_buffer_phys_addr(blob, b, 0x20000080) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_BUFFER_OVERLAP);
    cad_command_blob_destroy(blob);
}

TEST_CASE("SRAM address overflow rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    /* Internal buffer larger than SRAM */
    cad_buffer_id_t huge = cad_buffer_declare(blob, 0x00500000, 64, 0);
    (void)huge;
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_ADDRESS_OVERFLOW);
    cad_command_blob_destroy(blob);
}

TEST_CASE("DRAM address out of range rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t in = cad_buffer_declare(blob, 256, 64, 0xFFFFFFFF);
    (void)in;
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_ADDRESS_OVERFLOW);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Invalid dependency index rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t in = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t w = cad_buffer_declare(blob, 256, 64, 0x80001000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80002000);
    /* Dependency on command 5 when only one command exists. */
    uint32_t bad_deps[] = {5};
    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 1, 16, 16, 1, bad_deps) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_DEPENDENCY);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Zero-size DMA rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_DMA);
    cad_buffer_id_t src = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t dst = cad_buffer_declare(blob, 256, 64, 0x80001000);
    REQUIRE(cad_op_dma_copy(blob, src, 0, dst, 0, 0, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_SHAPE);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Blob decoder rejects wrong magic") {
    uint8_t garbage[64] = {0};
    CHECK(cad_command_blob_decode(garbage, sizeof(garbage)) == nullptr);
}

TEST_CASE("Blob decoder rejects major version mismatch") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t in = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t w = cad_buffer_declare(blob, 256, 64, 0x80001000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80002000);
    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 1, 16, 16, 0, nullptr) == 0);
    REQUIRE(cad_command_blob_lower(blob) == CAD_LOWER_OK);

    uint8_t *buf = nullptr;
    size_t size = 0;
    REQUIRE(cad_command_blob_encode(blob, &buf, &size) == 0);

    /* Patch major version to 99 */
    buf[5] = 99;
    CHECK(cad_command_blob_decode(buf, size) == nullptr);

    cad_command_blob_encoded_free(buf);
    cad_command_blob_destroy(blob);
}

int main(void) {
    printf("=== CaduceusCore Command Lowering Negative Tests ===\n");
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
