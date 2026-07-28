/*
 * CaduceusCore Command IR — Lowering pass.
 *
 * Validates IR, assigns deterministic physical addresses, computes tiling,
 * and builds command-ring entries + descriptors.
 */

#include "command_ir_internal.h"

#include "npu_abi.h"

#include <string.h>

#define TILE_H 64
#define TILE_W 64
#define SRAM_BASE NPU_SRAM_BASE
#define SRAM_SIZE NPU_SRAM_SIZE
#define DRAM_BASE NPU_DRAM_BASE
#define DRAM_SIZE NPU_DRAM_SIZE
#define DESC_ALIGN 64

static uint64_t align_up(uint64_t v, uint32_t a) {
    return (v + a - 1) & ~((uint64_t)a - 1);
}

static int supported(uint32_t caps, uint32_t opcode) {
    switch (opcode) {
    case CAD_OP_MMUL:          return (caps & CAD_CAP_MXU) != 0;
    case CAD_OP_SFU_SOFTMAX:
    case CAD_OP_SFU_LAYERNORM:
    case CAD_OP_SFU_GELU:
    case CAD_OP_SFU_RELU:
    case CAD_OP_ROPE:
    case CAD_OP_SFU_SILU:
    case CAD_OP_SFU_RMSNORM:   return (caps & CAD_CAP_SFU) != 0;
    case CAD_OP_VADD:
    case CAD_OP_VMUL:
    case CAD_OP_VRED_MAX:
    case CAD_OP_VRED_SUM:
    case CAD_OP_VCONV:
    case CAD_OP_VRESID:        return (caps & CAD_CAP_VECTOR) != 0;
    case CAD_OP_DMA_COPY:
    case CAD_OP_DMA_ST:
    case CAD_OP_DMA_COPY_LDD:
    case CAD_OP_DMA_COPY_STD:  return (caps & CAD_CAP_DMA) != 0;
    case CAD_OP_PCIE_DMA:      return (caps & CAD_CAP_PCIE) != 0;
    case CAD_OP_BARRIER:       return 1;
    }
    return 0;
}

static cad_lower_status_t validate_deps(cad_command_blob_t *blob,
                                        uint32_t idx) {
    cad_command_t *cmd = &blob->commands[idx];
    for (uint32_t d = 0; d < cmd->dep_count; d++) {
        uint32_t dep = cmd->deps[d];
        if (dep == 0 || dep > idx || dep > blob->command_count)
            return CAD_LOWER_INVALID_DEPENDENCY;
    }
    return CAD_LOWER_OK;
}

static cad_lower_status_t assign_addresses(cad_command_blob_t *blob) {
    uint64_t next_sram = 0;
    for (uint32_t i = 0; i < blob->buffer_count; i++) {
        cad_buffer_t *buf = &blob->buffers[i];
        if (buf->fixed_addr) {
            if ((buf->phys_addr & (buf->alignment - 1)) != 0)
                return CAD_LOWER_INVALID_ALIGNMENT;
            continue;
        }
        if (buf->host_addr) {
            buf->phys_addr = buf->host_addr;
            if (buf->phys_addr < DRAM_BASE ||
                buf->phys_addr + buf->size > DRAM_BASE + DRAM_SIZE)
                return CAD_LOWER_ADDRESS_OVERFLOW;
        } else {
            next_sram = align_up(next_sram, buf->alignment);
            if (next_sram + buf->size > SRAM_SIZE)
                return CAD_LOWER_ADDRESS_OVERFLOW;
            buf->phys_addr = SRAM_BASE + next_sram;
            next_sram += buf->size;
        }
        if ((buf->phys_addr & (buf->alignment - 1)) != 0)
            return CAD_LOWER_INVALID_ALIGNMENT;
    }
    return CAD_LOWER_OK;
}

static cad_lower_status_t check_overlap(cad_command_blob_t *blob) {
    for (uint32_t i = 0; i < blob->buffer_count; i++) {
        cad_buffer_t *a = &blob->buffers[i];
        if (a->host_addr) continue; /* external DRAM may alias intentionally */
        for (uint32_t j = i + 1; j < blob->buffer_count; j++) {
            cad_buffer_t *b = &blob->buffers[j];
            if (b->host_addr) continue;
            uint64_t a0 = a->phys_addr, a1 = a0 + a->size;
            uint64_t b0 = b->phys_addr, b1 = b0 + b->size;
            if (a0 < b1 && b0 < a1) return CAD_LOWER_BUFFER_OVERLAP;
        }
    }
    return CAD_LOWER_OK;
}

static void write_mmul_desc(uint8_t *desc,
                            const cad_command_t *cmd,
                            const cad_buffer_t *bufs) {
    uint32_t *d = (uint32_t *)desc;
    uint32_t M = cmd->u.mmul.M;
    uint32_t K = cmd->u.mmul.K;
    uint32_t N = cmd->u.mmul.N;
    d[0] = (uint32_t)bufs[0].phys_addr;
    d[1] = (uint32_t)bufs[1].phys_addr;
    d[2] = (uint32_t)bufs[2].phys_addr;
    d[3] = (cmd->buffers[3] != CAD_BUFFER_INVALID)
              ? (uint32_t)bufs[3].phys_addr : 0;
    d[4] = 0; d[5] = 0; d[6] = 0; d[7] = 0;
    d[8]  = M * K;
    d[9]  = K * N / 2;          /* INT4 weights packed */
    d[10] = M * N * 4;
    d[11] = (cmd->buffers[3] != CAD_BUFFER_INVALID) ? N * 4 : 0;
    d[12] = M;
    d[13] = K;
    d[14] = N;
}

static void write_sfu_desc(uint8_t *desc, const cad_command_t *cmd,
                           const cad_buffer_t *bufs) {
    uint32_t *d = (uint32_t *)desc;
    d[0] = (uint32_t)bufs[0].phys_addr;
    d[1] = 0;
    d[2] = (uint32_t)bufs[1].phys_addr;
    d[3] = 0; d[4] = 0; d[5] = 0; d[6] = 0; d[7] = 0;
    d[8] = (cmd->u.sfu.head_dim << 16) | (cmd->u.sfu.elements & 0xFFFF);
    d[9] = cmd->u.sfu.pos;
    d[10] = cmd->u.sfu.sfu_op;
    d[11] = d[12] = d[13] = d[14] = 0;
}

static void write_vector_desc(uint8_t *desc, const cad_command_t *cmd,
                              const cad_buffer_t *bufs) {
    uint32_t *d = (uint32_t *)desc;
    d[0] = (uint32_t)bufs[0].phys_addr;
    d[1] = (cmd->buffers[1] != CAD_BUFFER_INVALID)
              ? (uint32_t)bufs[1].phys_addr : 0;
    d[2] = (uint32_t)bufs[2].phys_addr;
    d[3] = 0; d[4] = 0; d[5] = 0; d[6] = 0; d[7] = 0;
    d[8] = cmd->u.vector.elements;
    d[9] = d[10] = d[11] = d[12] = d[13] = d[14] = 0;
}

static void write_dma_copy_desc(uint8_t *desc, const cad_command_t *cmd,
                                const cad_buffer_t *bufs) {
    uint32_t *d = (uint32_t *)desc;
    uint64_t src = bufs[0].phys_addr + cmd->u.dma.src_offset;
    uint64_t dst = bufs[1].phys_addr + cmd->u.dma.dst_offset;
    d[0] = (uint32_t)src;
    d[1] = 0;
    d[2] = (uint32_t)dst;
    d[3] = 0; d[4] = 0; d[5] = 0; d[6] = 0; d[7] = 0;
    d[8] = (uint32_t)cmd->u.dma.size;
    d[9] = d[10] = d[11] = d[12] = d[13] = d[14] = 0;
}

static cad_lower_status_t lower_mmul(cad_command_blob_t *blob,
                                     uint32_t idx,
                                     uint32_t desc_idx) {
    cad_command_t *cmd = &blob->commands[idx];
    uint32_t M = cmd->u.mmul.M;
    uint32_t K = cmd->u.mmul.K;
    uint32_t N = cmd->u.mmul.N;
    if (M == 0 || K == 0 || N == 0) return CAD_LOWER_INVALID_SHAPE;

    uint32_t num_k = (K + TILE_H - 1) / TILE_H;
    uint32_t num_n = (N + TILE_W - 1) / TILE_W;
    uint32_t last_k = K - (num_k - 1) * TILE_H;
    uint32_t last_n = N - (num_n - 1) * TILE_W;
    if (last_k == 0 || last_k > TILE_H) return CAD_LOWER_BAD_TILE;
    if (last_n == 0 || last_n > TILE_W) return CAD_LOWER_BAD_TILE;

    cad_buffer_t bufs[4];
    for (int i = 0; i < 4; i++) {
        bufs[i] = (cmd->buffers[i] != CAD_BUFFER_INVALID)
                      ? blob->buffers[cmd->buffers[i] - 1]
                      : (cad_buffer_t){0};
    }

    write_mmul_desc(&blob->descriptors[desc_idx * CAD_DESC_BYTES], cmd, bufs);
    return CAD_LOWER_OK;
}

static cad_lower_status_t lower_one(cad_command_blob_t *blob,
                                    uint32_t idx,
                                    uint32_t desc_idx) {
    cad_command_t *cmd = &blob->commands[idx];
    if (!supported(blob->caps, cmd->opcode))
        return CAD_LOWER_UNSUPPORTED_OP;

    cad_lower_status_t st = validate_deps(blob, idx);
    if (st != CAD_LOWER_OK) return st;

    uint32_t *entry = (uint32_t *)&blob->cmd_ring[idx * CAD_CMD_ENTRY_BYTES];
    entry[0] = cmd->opcode;
    entry[1] = desc_idx * CAD_DESC_BYTES;
    entry[2] = 0;
    for (uint32_t d = 0; d < cmd->dep_count && d < 32; d++) {
        if (cmd->deps[d] > 0 && cmd->deps[d] <= 32)
            entry[2] |= (1U << (cmd->deps[d] - 1));
    }
    entry[3] = 0;
    entry[4] = entry[5] = entry[6] = entry[7] = 0;

    switch (cmd->kind) {
    case CAD_OPK_MMUL:      return lower_mmul(blob, idx, desc_idx);
    case CAD_OPK_SFU: {
        cad_buffer_t bufs[2] = {
            blob->buffers[cmd->buffers[0] - 1],
            blob->buffers[cmd->buffers[1] - 1],
        };
        if (cmd->u.sfu.elements == 0) return CAD_LOWER_INVALID_SHAPE;
        write_sfu_desc(&blob->descriptors[desc_idx * CAD_DESC_BYTES], cmd, bufs);
        return CAD_LOWER_OK;
    }
    case CAD_OPK_VECTOR: {
        cad_buffer_t bufs[3] = {0};
        for (int i = 0; i < 3; i++) {
            if (cmd->buffers[i] != CAD_BUFFER_INVALID)
                bufs[i] = blob->buffers[cmd->buffers[i] - 1];
        }
        if (cmd->u.vector.elements == 0) return CAD_LOWER_INVALID_SHAPE;
        write_vector_desc(&blob->descriptors[desc_idx * CAD_DESC_BYTES], cmd, bufs);
        return CAD_LOWER_OK;
    }
    case CAD_OPK_DMA_COPY: {
        cad_buffer_t bufs[2] = {
            blob->buffers[cmd->buffers[0] - 1],
            blob->buffers[cmd->buffers[1] - 1],
        };
        if (cmd->u.dma.size == 0) return CAD_LOWER_INVALID_SHAPE;
        if (cmd->u.dma.size > 0xFFFFFFFFU) return CAD_LOWER_ADDRESS_OVERFLOW;
        write_dma_copy_desc(&blob->descriptors[desc_idx * CAD_DESC_BYTES], cmd, bufs);
        return CAD_LOWER_OK;
    }
    case CAD_OPK_BARRIER:
        entry[1] = 0; /* no descriptor */
        return CAD_LOWER_OK;
    }
    return CAD_LOWER_UNSUPPORTED_OP;
}

cad_lower_status_t cad_command_blob_lower(cad_command_blob_t *blob) {
    if (!blob) return CAD_LOWER_INVALID_BLOB;
    if (blob->lowered) return CAD_LOWER_OK;

    cad_lower_status_t st = assign_addresses(blob);
    if (st != CAD_LOWER_OK) return st;
    st = check_overlap(blob);
    if (st != CAD_LOWER_OK) return st;

    uint32_t desc_idx = 0;
    for (uint32_t i = 0; i < blob->command_count; i++) {
        st = lower_one(blob, i, desc_idx);
        if (st != CAD_LOWER_OK) return st;
        if (blob->commands[i].kind != CAD_OPK_BARRIER) {
            blob->commands[i].desc_index = desc_idx;
            desc_idx++;
        }
    }

    for (uint32_t i = 0; i < blob->buffer_count; i++) {
        blob->buf_table[i * 4 + 0] = blob->buffers[i].id;
        blob->buf_table[i * 4 + 1] = blob->buffers[i].size;
        blob->buf_table[i * 4 + 2] = blob->buffers[i].alignment;
        blob->buf_table[i * 4 + 3] = blob->buffers[i].phys_addr;
    }

    blob->lowered = 1;
    return CAD_LOWER_OK;
}
