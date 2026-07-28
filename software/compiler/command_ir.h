/*
 * CaduceusCore Production Command IR / Lowering / Blob C API
 *
 * Framework-neutral typed command IR for MMUL, SFU, Vector, DMA, barriers,
 * buffers, and dependencies.  Lowering produces command-ring entries and
 * descriptor tables compatible with gen/npu_abi.h.
 *
 * Design rules:
 *   - Physical addresses are internal; adapters see only buffer handles.
 *   - Command blobs are versioned and deterministic.
 *   - The C API is stable for llama.cpp dynamic lowering and ExecuTorch AOT.
 */

#ifndef CADUCEUS_COMMAND_IR_H
#define CADUCEUS_COMMAND_IR_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── Blob version ───────────────────────────────────────────────── */

#define CAD_COMMAND_BLOB_MAJOR 1
#define CAD_COMMAND_BLOB_MINOR 0

/* ── Opaque handle ──────────────────────────────────────────────── */

typedef struct cad_command_blob cad_command_blob_t;

/* ── Buffer handle (virtual ID, never a physical address) ───────── */

typedef uint32_t cad_buffer_id_t;
#define CAD_BUFFER_INVALID ((cad_buffer_id_t)0)

/* ── Lowering status codes ──────────────────────────────────────── */

typedef enum cad_lower_status_t {
    CAD_LOWER_OK = 0,
    CAD_LOWER_INVALID_SHAPE = 1,
    CAD_LOWER_INVALID_ALIGNMENT = 2,
    CAD_LOWER_BUFFER_OVERLAP = 3,
    CAD_LOWER_ADDRESS_OVERFLOW = 4,
    CAD_LOWER_UNSUPPORTED_OP = 5,
    CAD_LOWER_BAD_TILE = 6,
    CAD_LOWER_INVALID_DEPENDENCY = 7,
    CAD_LOWER_OUT_OF_MEMORY = 8,
    CAD_LOWER_INVALID_BLOB = 9,
} cad_lower_status_t;

/* ── Capability flags (mirror NPU_CAP_* from npu_abi.h) ─────────── */

#define CAD_CAP_MXU     (1U << 0)
#define CAD_CAP_SFU     (1U << 1)
#define CAD_CAP_VECTOR  (1U << 2)
#define CAD_CAP_DMA     (1U << 3)
#define CAD_CAP_PCIE    (1U << 4)

/* ── Engine opcodes (mirror NPU_ENGINE_OP_* from npu_abi.h) ─────── */

#define CAD_OP_MMUL          0x00
#define CAD_OP_SFU_SOFTMAX   0x01
#define CAD_OP_SFU_LAYERNORM 0x02
#define CAD_OP_SFU_GELU      0x03
#define CAD_OP_SFU_RELU      0x04
#define CAD_OP_ROPE          0x05
#define CAD_OP_SFU_SILU      0x06
#define CAD_OP_PCIE_DMA      0x07
#define CAD_OP_DMA_COPY      0x09
#define CAD_OP_DMA_ST        0x0a
#define CAD_OP_VADD          0x0f
#define CAD_OP_VMUL          0x10
#define CAD_OP_VRED_MAX      0x11
#define CAD_OP_VRED_SUM      0x12
#define CAD_OP_VCONV         0x13
#define CAD_OP_VRESID        0x14
#define CAD_OP_DMA_COPY_LDD  0x15
#define CAD_OP_DMA_COPY_STD  0x16
#define CAD_OP_SFU_RMSNORM   0x17
#define CAD_OP_BARRIER       0xff

/* ── Configuration limits ───────────────────────────────────────── */

#define CAD_MAX_BUFFERS 256
#define CAD_MAX_COMMANDS 1024
#define CAD_MAX_DEPS 32
#define CAD_DESC_WORDS 15
#define CAD_DESC_BYTES (CAD_DESC_WORDS * 4)
#define CAD_CMD_ENTRY_WORDS 8
#define CAD_CMD_ENTRY_BYTES (CAD_CMD_ENTRY_WORDS * 4)

/* ── Lifecycle ──────────────────────────────────────────────────── */

cad_command_blob_t *cad_command_blob_create(uint32_t caps);
void cad_command_blob_destroy(cad_command_blob_t *blob);

/* ── Buffer declarations ────────────────────────────────────────── */

/*
 * Declare a buffer.  size must be > 0.  alignment must be a power of two
 * >= 1.  host_addr is the external DRAM address seen by the framework;
 * pass 0 for internal scratch buffers whose address is assigned by the
 * lowerer.
 */
cad_buffer_id_t cad_buffer_declare(cad_command_blob_t *blob,
                                   uint64_t size,
                                   uint32_t alignment,
                                   uint64_t host_addr);

/* ── Operations ─────────────────────────────────────────────────── */

int cad_op_mmul(cad_command_blob_t *blob,
                cad_buffer_id_t input,
                cad_buffer_id_t weight,
                cad_buffer_id_t output,
                cad_buffer_id_t scale,
                uint32_t M, uint32_t K, uint32_t N,
                uint32_t dep_count,
                const cad_buffer_id_t *deps);

int cad_op_sfu(cad_command_blob_t *blob,
               uint32_t sfu_op,
               cad_buffer_id_t input,
               cad_buffer_id_t output,
               uint32_t elements,
               uint32_t head_dim,
               uint32_t pos,
               uint32_t dep_count,
               const cad_buffer_id_t *deps);

int cad_op_vector(cad_command_blob_t *blob,
                  uint32_t vec_op,
                  cad_buffer_id_t a,
                  cad_buffer_id_t b,
                  cad_buffer_id_t output,
                  uint32_t elements,
                  uint32_t dep_count,
                  const cad_buffer_id_t *deps);

int cad_op_dma_copy(cad_command_blob_t *blob,
                    cad_buffer_id_t src,
                    uint64_t src_offset,
                    cad_buffer_id_t dst,
                    uint64_t dst_offset,
                    uint64_t size,
                    uint32_t dep_count,
                    const cad_buffer_id_t *deps);

int cad_op_barrier(cad_command_blob_t *blob);

/* ── Lowering ───────────────────────────────────────────────────── */

cad_lower_status_t cad_command_blob_lower(cad_command_blob_t *blob);

/* ── Encoding / decoding ────────────────────────────────────────── */

int cad_command_blob_encode(const cad_command_blob_t *blob,
                            uint8_t **out_buf,
                            size_t *out_size);

void cad_command_blob_encoded_free(uint8_t *buf);

cad_command_blob_t *cad_command_blob_decode(const uint8_t *buf,
                                            size_t size);

/* ── Introspection (valid after lower) ──────────────────────────── */

uint32_t cad_command_blob_version_major(const cad_command_blob_t *blob);
uint32_t cad_command_blob_version_minor(const cad_command_blob_t *blob);
size_t cad_command_blob_num_buffers(const cad_command_blob_t *blob);
size_t cad_command_blob_num_commands(const cad_command_blob_t *blob);

const uint8_t *cad_command_blob_command_ring(const cad_command_blob_t *blob,
                                             size_t *out_size);

const uint8_t *cad_command_blob_descriptors(const cad_command_blob_t *blob,
                                            size_t *out_size);

const uint64_t *cad_command_blob_buffer_table(const cad_command_blob_t *blob,
                                              size_t *out_size);

const char *cad_lower_status_string(cad_lower_status_t status);

/* Test-only: override the physical address assigned to a buffer.  Used by
 * negative-path tests to inject invalid layouts and verify validation. */
int cad_test_set_buffer_phys_addr(cad_command_blob_t *blob,
                                  cad_buffer_id_t id,
                                  uint64_t addr);

#ifdef __cplusplus
}
#endif

#endif /* CADUCEUS_COMMAND_IR_H */
