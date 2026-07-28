/*
 * CaduceusCore Command IR — IR builder and helpers.
 */

#include "command_ir_internal.h"

#include <stdlib.h>
#include <string.h>

static cad_buffer_t *find_buffer(cad_command_blob_t *blob,
                                 cad_buffer_id_t id) {
    for (uint32_t i = 0; i < blob->buffer_count; i++) {
        if (blob->buffers[i].id == id) return &blob->buffers[i];
    }
    return NULL;
}

static int valid_buffer(cad_command_blob_t *blob, cad_buffer_id_t id) {
    return id != CAD_BUFFER_INVALID && find_buffer(blob, id) != NULL;
}

static int append_command(cad_command_blob_t *blob, cad_command_t *cmd) {
    if (blob->command_count >= CAD_MAX_COMMANDS) return -1;
    blob->commands[blob->command_count++] = *cmd;
    return 0;
}

static int copy_deps(cad_command_t *cmd,
                     uint32_t dep_count,
                     const cad_buffer_id_t *deps) {
    if (dep_count > CAD_MAX_DEPS) return -1;
    cmd->dep_count = dep_count;
    for (uint32_t i = 0; i < dep_count; i++) {
        cmd->deps[i] = deps[i];
    }
    return 0;
}

cad_command_blob_t *cad_command_blob_create(uint32_t caps) {
    cad_command_blob_t *blob = calloc(1, sizeof(*blob));
    if (!blob) return NULL;
    blob->caps = caps;
    blob->version_major = CAD_COMMAND_BLOB_MAJOR;
    blob->version_minor = CAD_COMMAND_BLOB_MINOR;
    return blob;
}

void cad_command_blob_destroy(cad_command_blob_t *blob) {
    free(blob);
}

cad_buffer_id_t cad_buffer_declare(cad_command_blob_t *blob,
                                   uint64_t size,
                                   uint32_t alignment,
                                   uint64_t host_addr) {
    if (!blob || size == 0 || alignment == 0 || (alignment & (alignment - 1)))
        return CAD_BUFFER_INVALID;
    if (blob->buffer_count >= CAD_MAX_BUFFERS)
        return CAD_BUFFER_INVALID;

    cad_buffer_t *buf = &blob->buffers[blob->buffer_count];
    buf->id = (cad_buffer_id_t)(blob->buffer_count + 1);
    buf->size = size;
    buf->alignment = alignment;
    buf->host_addr = host_addr;
    buf->phys_addr = 0;
    buf->flags = host_addr ? 1U : 0U;
    blob->buffer_count++;
    return buf->id;
}

int cad_op_mmul(cad_command_blob_t *blob,
                cad_buffer_id_t input,
                cad_buffer_id_t weight,
                cad_buffer_id_t output,
                cad_buffer_id_t scale,
                uint32_t M, uint32_t K, uint32_t N,
                uint32_t dep_count,
                const cad_buffer_id_t *deps) {
    if (!blob || !valid_buffer(blob, input) || !valid_buffer(blob, weight) ||
        !valid_buffer(blob, output))
        return -1;
    if (scale != CAD_BUFFER_INVALID && !valid_buffer(blob, scale))
        return -1;

    cad_command_t cmd = {0};
    cmd.opcode = CAD_OP_MMUL;
    cmd.kind = CAD_OPK_MMUL;
    cmd.buffers[0] = input;
    cmd.buffers[1] = weight;
    cmd.buffers[2] = output;
    cmd.buffers[3] = scale;
    cmd.u.mmul.M = M;
    cmd.u.mmul.K = K;
    cmd.u.mmul.N = N;
    if (copy_deps(&cmd, dep_count, deps)) return -1;
    return append_command(blob, &cmd);
}

int cad_op_sfu(cad_command_blob_t *blob,
               uint32_t sfu_op,
               cad_buffer_id_t input,
               cad_buffer_id_t output,
               uint32_t elements,
               uint32_t head_dim,
               uint32_t pos,
               uint32_t dep_count,
               const cad_buffer_id_t *deps) {
    if (!blob || !valid_buffer(blob, input) || !valid_buffer(blob, output))
        return -1;

    cad_command_t cmd = {0};
    static const uint32_t sfu_op_map[] = {
        CAD_OP_SFU_SOFTMAX, CAD_OP_SFU_LAYERNORM, CAD_OP_SFU_GELU,
        CAD_OP_SFU_RELU, CAD_OP_SFU_SILU, CAD_OP_ROPE, CAD_OP_SFU_RMSNORM,
    };
    if (sfu_op >= (sizeof(sfu_op_map) / sizeof(sfu_op_map[0]))) return -1;
    cmd.opcode = sfu_op_map[sfu_op];
    cmd.kind = CAD_OPK_SFU;
    cmd.buffers[0] = input;
    cmd.buffers[1] = output;
    cmd.u.sfu.sfu_op = sfu_op;
    cmd.u.sfu.elements = elements;
    cmd.u.sfu.head_dim = head_dim;
    cmd.u.sfu.pos = pos;
    if (copy_deps(&cmd, dep_count, deps)) return -1;
    return append_command(blob, &cmd);
}

int cad_op_vector(cad_command_blob_t *blob,
                  uint32_t vec_op,
                  cad_buffer_id_t a,
                  cad_buffer_id_t b,
                  cad_buffer_id_t output,
                  uint32_t elements,
                  uint32_t dep_count,
                  const cad_buffer_id_t *deps) {
    if (!blob || !valid_buffer(blob, a) || !valid_buffer(blob, output))
        return -1;
    if (b != CAD_BUFFER_INVALID && !valid_buffer(blob, b))
        return -1;

    cad_command_t cmd = {0};
    cmd.opcode = CAD_OP_VADD + vec_op;
    cmd.kind = CAD_OPK_VECTOR;
    cmd.buffers[0] = a;
    cmd.buffers[1] = b;
    cmd.buffers[2] = output;
    cmd.u.vector.vec_op = vec_op;
    cmd.u.vector.elements = elements;
    if (copy_deps(&cmd, dep_count, deps)) return -1;
    return append_command(blob, &cmd);
}

int cad_op_dma_copy(cad_command_blob_t *blob,
                    cad_buffer_id_t src,
                    uint64_t src_offset,
                    cad_buffer_id_t dst,
                    uint64_t dst_offset,
                    uint64_t size,
                    uint32_t dep_count,
                    const cad_buffer_id_t *deps) {
    if (!blob || !valid_buffer(blob, src) || !valid_buffer(blob, dst))
        return -1;

    cad_command_t cmd = {0};
    cmd.opcode = CAD_OP_DMA_COPY;
    cmd.kind = CAD_OPK_DMA_COPY;
    cmd.buffers[0] = src;
    cmd.buffers[1] = dst;
    cmd.u.dma.src_offset = src_offset;
    cmd.u.dma.dst_offset = dst_offset;
    cmd.u.dma.size = size;
    if (copy_deps(&cmd, dep_count, deps)) return -1;
    return append_command(blob, &cmd);
}

int cad_op_barrier(cad_command_blob_t *blob) {
    if (!blob) return -1;
    cad_command_t cmd = {0};
    cmd.opcode = CAD_OP_BARRIER;
    cmd.kind = CAD_OPK_BARRIER;
    return append_command(blob, &cmd);
}

const char *cad_lower_status_string(cad_lower_status_t status) {
    switch (status) {
    case CAD_LOWER_OK:                   return "ok";
    case CAD_LOWER_INVALID_SHAPE:        return "invalid shape";
    case CAD_LOWER_INVALID_ALIGNMENT:    return "invalid alignment";
    case CAD_LOWER_BUFFER_OVERLAP:       return "buffer overlap";
    case CAD_LOWER_ADDRESS_OVERFLOW:     return "address overflow";
    case CAD_LOWER_UNSUPPORTED_OP:       return "unsupported op";
    case CAD_LOWER_BAD_TILE:             return "bad tile";
    case CAD_LOWER_INVALID_DEPENDENCY:   return "invalid dependency";
    case CAD_LOWER_OUT_OF_MEMORY:        return "out of memory";
    case CAD_LOWER_INVALID_BLOB:         return "invalid blob";
    }
    return "unknown";
}

int cad_test_set_buffer_phys_addr(cad_command_blob_t *blob,
                                  cad_buffer_id_t id,
                                  uint64_t addr) {
    if (!blob || id == CAD_BUFFER_INVALID) return -1;
    cad_buffer_t *buf = find_buffer(blob, id);
    if (!buf) return -1;
    buf->phys_addr = addr;
    buf->fixed_addr = 1;
    return 0;
}
