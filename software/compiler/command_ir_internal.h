/*
 * CaduceusCore Command IR — Internal Definitions
 *
 * NOT part of the public ABI; clients must use command_ir.h only.
 */

#ifndef CADUCEUS_COMMAND_IR_INTERNAL_H
#define CADUCEUS_COMMAND_IR_INTERNAL_H

#include "command_ir.h"

#include <stdint.h>

#define CAD_BLOB_MAGIC 0x43414442U /* "CADB" little-endian */

#define CAD_BLOB_HEADER_SIZE 64

/* Buffer entry in the IR (before and after lowering). */
typedef struct cad_buffer_t {
    cad_buffer_id_t id;
    uint64_t        size;
    uint32_t        alignment;
    uint64_t        host_addr;      /* 0 = internal scratch */
    uint64_t        phys_addr;      /* assigned by lowerer */
    uint32_t        flags;
    int             fixed_addr;     /* test override, do not reassign */
} cad_buffer_t;

/* Operation kind. */
typedef enum cad_op_kind_t {
    CAD_OPK_MMUL,
    CAD_OPK_SFU,
    CAD_OPK_VECTOR,
    CAD_OPK_DMA_COPY,
    CAD_OPK_BARRIER,
} cad_op_kind_t;

/* Typed operation payload. */
typedef struct cad_op_mmul_t {
    uint32_t M, K, N;
} cad_op_mmul_t;

typedef struct cad_op_sfu_t {
    uint32_t sfu_op;
    uint32_t elements;
    uint32_t head_dim;
    uint32_t pos;
} cad_op_sfu_t;

typedef struct cad_op_vector_t {
    uint32_t vec_op;
    uint32_t elements;
} cad_op_vector_t;

typedef struct cad_op_dma_copy_t {
    uint64_t src_offset;
    uint64_t dst_offset;
    uint64_t size;
} cad_op_dma_copy_t;

/* IR command. */
typedef struct cad_command_t {
    uint32_t        opcode;
    cad_op_kind_t   kind;
    cad_buffer_id_t buffers[4];     /* op-specific buffer handles */
    uint32_t        dep_count;
    uint32_t        deps[CAD_MAX_DEPS];
    union {
        cad_op_mmul_t     mmul;
        cad_op_sfu_t      sfu;
        cad_op_vector_t   vector;
        cad_op_dma_copy_t dma;
    } u;
    uint32_t        desc_index;     /* assigned by lowerer */
} cad_command_t;

struct cad_command_blob {
    uint32_t        caps;
    uint32_t        version_major;
    uint32_t        version_minor;
    uint32_t        buffer_count;
    uint32_t        command_count;
    cad_buffer_t    buffers[CAD_MAX_BUFFERS];
    cad_command_t   commands[CAD_MAX_COMMANDS];

    /* Lowered artifacts */
    uint8_t         cmd_ring[CAD_MAX_COMMANDS * CAD_CMD_ENTRY_BYTES];
    uint8_t         descriptors[CAD_MAX_COMMANDS * CAD_DESC_BYTES];
    uint64_t        buf_table[CAD_MAX_BUFFERS * 4];
    uint32_t        lowered;
};

#endif /* CADUCEUS_COMMAND_IR_INTERNAL_H */
