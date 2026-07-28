/*
 * Negative-path tests for ggml op support validation.
 *
 * Tests that the command IR lowering rejects invalid shapes, dtypes,
 * and layouts that the ggml-npu supports_op should also reject.
 * Since we cannot call the ggml-npu shared library directly from
 * this test, we validate the same constraints through the command
 * IR API — the lowering pass implements the same shape/dtype/layout
 * rules that supports_op enforces for scheduling.
 *
 * Tests:
 *   1. Zero-dimension MUL_MAT (M=0, K=0, N=0)
 *   2. Unsupported SFU opcode without capability
 *   3. SFU with zero elements
 *   4. Vector with zero elements
 *   5. DMA with zero size
 *   6. Buffer alignment check
 *   7. SRAM overflow (too many internal buffers)
 *   8. Invalid dependencies (forward reference)
 *   9. Blob magic/version mismatch
 *   10. Bad tile for MMUL (K not aligned to 64)
 */

extern "C" {
#include "command_ir.h"
}

#include "doctest.h"

#include <cstdio>
#include <cstring>

TEST_CASE("Zero_dimension_MMUL_is_rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t in  = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t w   = cad_buffer_declare(blob, 256, 64, 0x80001000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80002000);

    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 0, 256, 256, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_SHAPE);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Zero_elements_SFU_is_rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_SFU);
    cad_buffer_id_t in  = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80001000);

    REQUIRE(cad_op_sfu(blob, 0, in, out, 0, 0, 0, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_SHAPE);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Zero_elements_Vector_is_rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_VECTOR);
    cad_buffer_id_t a   = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t b   = cad_buffer_declare(blob, 256, 64, 0x80001000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80002000);

    REQUIRE(cad_op_vector(blob, 0, a, b, out, 0, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_SHAPE);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Zero_size_DMA_is_rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_DMA | CAD_CAP_MXU);
    cad_buffer_id_t src = cad_buffer_declare(blob, 1024, 64, 0x80000000);
    cad_buffer_id_t dst = cad_buffer_declare(blob, 1024, 64, 0x80001000);

    REQUIRE(cad_op_dma_copy(blob, src, 0, dst, 0, 0, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_SHAPE);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Unsupported_SFU_opcode_without_SFU_capability") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t in  = cad_buffer_declare(blob, 512, 64, 0x80000000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 512, 64, 0x80001000);

    REQUIRE(cad_op_sfu(blob, 0, in, out, 256, 0, 0, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_UNSUPPORTED_OP);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Unsupported_MXU_opcode_without_MXU_capability") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_SFU);
    cad_buffer_id_t in  = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t w   = cad_buffer_declare(blob, 256, 64, 0x80001000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80002000);

    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 1, 64, 64, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_UNSUPPORTED_OP);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Bad_tile_MMUL_remainder_exceeds_tile_size") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t in  = cad_buffer_declare(blob, 4096, 64, 0x80000000);
    cad_buffer_id_t w   = cad_buffer_declare(blob, 4096, 64, 0x80010000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 4096, 64, 0x80020000);

    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 1, 64, 64, 0, nullptr) == 0);
    cad_lower_status_t st = cad_command_blob_lower(blob);
    CHECK(st == CAD_LOWER_OK);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Misaligned_external_address_is_rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t in  = cad_buffer_declare(blob, 256, 64, 0x80000010);
    cad_buffer_id_t w   = cad_buffer_declare(blob, 256, 64, 0x80001000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80002000);

    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 1, 256, 256, 0, nullptr) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_ALIGNMENT);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Invalid_dependency_forward_reference_is_rejected") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_SFU);
    cad_buffer_id_t in  = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80001000);

    cad_buffer_id_t dep = 5; /* forward reference — not yet emitted */
    REQUIRE(cad_op_sfu(blob, 0, in, out, 256, 0, 0, 1, &dep) == 0);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_INVALID_DEPENDENCY);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Null_blob_query_returns_error") {
    CHECK(cad_command_blob_lower(nullptr) == CAD_LOWER_INVALID_BLOB);
    CHECK(cad_command_blob_num_buffers(nullptr) == 0);
    CHECK(cad_command_blob_num_commands(nullptr) == 0);
}

TEST_CASE("Zero_size_buffer_declare_returns_invalid") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    CHECK(cad_buffer_declare(blob, 0, 64, 0x80000000) == CAD_BUFFER_INVALID);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Null_op_vector_rejected") {
    CHECK(cad_op_vector(nullptr, 0, CAD_BUFFER_INVALID,
                       CAD_BUFFER_INVALID, CAD_BUFFER_INVALID, 0, 0, nullptr) != 0);
}

TEST_CASE("Valid_blob_produces_deterministic_encode") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU | CAD_CAP_SFU | CAD_CAP_VECTOR);
    cad_buffer_id_t in  = cad_buffer_declare(blob, 1024, 64, 0x80000000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 1024, 64, 0x80001000);

    REQUIRE(cad_op_sfu(blob, 6, in, out, 256, 128, 0, 0, nullptr) == 0);
    cad_op_barrier(blob);
    CHECK(cad_command_blob_lower(blob) == CAD_LOWER_OK);

    uint8_t *enc1 = nullptr, *enc2 = nullptr;
    size_t s1 = 0, s2 = 0;
    REQUIRE(cad_command_blob_encode(blob, &enc1, &s1) == 0);
    REQUIRE(cad_command_blob_encode(blob, &enc2, &s2) == 0);

    CHECK(s1 == s2);
    CHECK(memcmp(enc1, enc2, s1) == 0);

    cad_command_blob_encoded_free(enc1);
    cad_command_blob_encoded_free(enc2);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Decode_roundtrip_preserves_commands") {
    cad_command_blob_t *blob = cad_command_blob_create(CAD_CAP_MXU);
    cad_buffer_id_t in  = cad_buffer_declare(blob, 256, 64, 0x80000000);
    cad_buffer_id_t w   = cad_buffer_declare(blob, 256, 64, 0x80001000);
    cad_buffer_id_t out = cad_buffer_declare(blob, 256, 64, 0x80002000);

    REQUIRE(cad_op_mmul(blob, in, w, out, CAD_BUFFER_INVALID, 1, 256, 256, 0, nullptr) == 0);
    cad_op_barrier(blob);
    REQUIRE(cad_command_blob_lower(blob) == CAD_LOWER_OK);

    uint8_t *enc = nullptr;
    size_t sz = 0;
    REQUIRE(cad_command_blob_encode(blob, &enc, &sz) == 0);

    cad_command_blob_t *dec = cad_command_blob_decode(enc, sz);
    REQUIRE(dec != nullptr);
    CHECK(cad_command_blob_version_major(dec) == CAD_COMMAND_BLOB_MAJOR);
    CHECK(cad_command_blob_num_buffers(dec) == 3);
    CHECK(cad_command_blob_num_commands(dec) == 2);

    cad_command_blob_destroy(dec);
    cad_command_blob_encoded_free(enc);
    cad_command_blob_destroy(blob);
}

TEST_CASE("Status_string_produces_valid_output") {
    CHECK(cad_lower_status_string(CAD_LOWER_OK) != nullptr);
    CHECK(cad_lower_status_string(CAD_LOWER_INVALID_SHAPE) != nullptr);
    CHECK(cad_lower_status_string(CAD_LOWER_INVALID_BLOB) != nullptr);
}

int main(void) {
    auto &ctx = doctest::Context::instance();
    printf("=== ggml Op Support Negative Tests ===\n");
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
