/*
 * CaduceusCore Command IR — Versioned blob encoder/decoder and accessors.
 */

#include "command_ir_internal.h"

#include <stdlib.h>
#include <string.h>

#define HEADER_SIZE 64

static void write_le32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v);
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

static uint32_t read_le32(const uint8_t *p) {
    return ((uint32_t)p[0]) |
           ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) |
           ((uint32_t)p[3] << 24);
}

static void write_le64(uint8_t *p, uint64_t v) {
    for (int i = 0; i < 8; i++) p[i] = (uint8_t)(v >> (8 * i));
}

static uint64_t read_le64(const uint8_t *p) {
    uint64_t v = 0;
    for (int i = 0; i < 8; i++) v |= ((uint64_t)p[i]) << (8 * i);
    return v;
}

static size_t total_blob_size(uint32_t buf_count,
                              uint32_t cmd_count,
                              uint32_t desc_count) {
    return HEADER_SIZE +
           buf_count * 32 +
           cmd_count * CAD_CMD_ENTRY_BYTES +
           desc_count * CAD_DESC_BYTES;
}

int cad_command_blob_encode(const cad_command_blob_t *blob,
                            uint8_t **out_buf,
                            size_t *out_size) {
    if (!blob || !out_buf || !out_size) return -1;
    if (!blob->lowered) return -1;

    uint32_t desc_count = 0;
    for (uint32_t i = 0; i < blob->command_count; i++) {
        if (blob->commands[i].kind != CAD_OPK_BARRIER) desc_count++;
    }

    size_t size = total_blob_size(blob->buffer_count,
                                  blob->command_count,
                                  desc_count);
    uint8_t *buf = malloc(size);
    if (!buf) return -1;

    size_t off = 0;
    write_le32(buf + 0, CAD_BLOB_MAGIC);
    write_le32(buf + 4, (blob->version_major << 16) | blob->version_minor);
    write_le32(buf + 8, HEADER_SIZE);
    write_le32(buf + 12, blob->caps);
    write_le32(buf + 16, blob->buffer_count);
    write_le32(buf + 20, blob->command_count);
    write_le32(buf + 24, desc_count * CAD_DESC_BYTES);
    write_le32(buf + 28, 0); /* descriptor table offset placeholder */
    write_le32(buf + 32, blob->command_count * CAD_CMD_ENTRY_BYTES);
    write_le32(buf + 36, 0); /* command ring offset placeholder */
    write_le32(buf + 40, blob->buffer_count * 32);
    write_le32(buf + 44, 0); /* buffer table offset placeholder */
    write_le32(buf + 48, 0);
    write_le32(buf + 52, 0);
    write_le32(buf + 56, 0);
    write_le32(buf + 60, 0);

    off = HEADER_SIZE;

    size_t buf_table_off = off;
    for (uint32_t i = 0; i < blob->buffer_count; i++) {
        write_le32(buf + off + 0,  (uint32_t)blob->buf_table[i * 4 + 0]);
        write_le32(buf + off + 4,  (uint32_t)blob->buf_table[i * 4 + 1]);
        write_le32(buf + off + 8,  (uint32_t)blob->buf_table[i * 4 + 2]);
        write_le32(buf + off + 12, (uint32_t)(blob->buf_table[i * 4 + 3]));
        write_le32(buf + off + 16, (uint32_t)(blob->buf_table[i * 4 + 3] >> 32));
        write_le32(buf + off + 20, 0);
        write_le32(buf + off + 24, 0);
        write_le32(buf + off + 28, 0);
        off += 32;
    }

    size_t cmd_ring_off = off;
    memcpy(buf + off, blob->cmd_ring,
           blob->command_count * CAD_CMD_ENTRY_BYTES);
    off += blob->command_count * CAD_CMD_ENTRY_BYTES;

    size_t desc_table_off = off;
    uint32_t d = 0;
    for (uint32_t i = 0; i < blob->command_count; i++) {
        if (blob->commands[i].kind == CAD_OPK_BARRIER) continue;
        memcpy(buf + off + d * CAD_DESC_BYTES,
               &blob->descriptors[blob->commands[i].desc_index * CAD_DESC_BYTES],
               CAD_DESC_BYTES);
        d++;
    }

    write_le32(buf + 28, (uint32_t)desc_table_off);
    write_le32(buf + 36, (uint32_t)cmd_ring_off);
    write_le32(buf + 44, (uint32_t)buf_table_off);

    *out_buf = buf;
    *out_size = size;
    return 0;
}

void cad_command_blob_encoded_free(uint8_t *buf) {
    free(buf);
}

cad_command_blob_t *cad_command_blob_decode(const uint8_t *buf,
                                            size_t size) {
    if (!buf || size < HEADER_SIZE) return NULL;
    if (read_le32(buf + 0) != CAD_BLOB_MAGIC) return NULL;

    uint32_t version = read_le32(buf + 4);
    uint32_t major = version >> 16;
    uint32_t minor = version & 0xFFFF;
    if (major != CAD_COMMAND_BLOB_MAJOR) return NULL;
    if (minor > CAD_COMMAND_BLOB_MINOR) return NULL;

    uint32_t buf_count = read_le32(buf + 16);
    uint32_t cmd_count = read_le32(buf + 20);
    uint32_t desc_size  = read_le32(buf + 24);
    uint32_t desc_off   = read_le32(buf + 28);
    uint32_t cmd_size   = read_le32(buf + 32);
    uint32_t cmd_off    = read_le32(buf + 36);
    uint32_t bt_size    = read_le32(buf + 40);
    uint32_t bt_off     = read_le32(buf + 44);

    if (buf_count > CAD_MAX_BUFFERS || cmd_count > CAD_MAX_COMMANDS)
        return NULL;
    if (desc_off + desc_size > size || cmd_off + cmd_size > size ||
        bt_off + bt_size > size)
        return NULL;
    if (cmd_size != cmd_count * CAD_CMD_ENTRY_BYTES) return NULL;

    cad_command_blob_t *blob = cad_command_blob_create(read_le32(buf + 12));
    if (!blob) return NULL;
    blob->version_major = major;
    blob->version_minor = minor;
    blob->buffer_count = buf_count;
    blob->command_count = cmd_count;
    blob->lowered = 1;

    for (uint32_t i = 0; i < buf_count; i++) {
        const uint8_t *p = buf + bt_off + i * 32;
        blob->buffers[i].id = read_le32(p + 0);
        blob->buffers[i].size = read_le32(p + 4);
        blob->buffers[i].alignment = read_le32(p + 8);
        blob->buffers[i].phys_addr = read_le64(p + 12);
        blob->buffers[i].host_addr = blob->buffers[i].phys_addr;
        blob->buf_table[i * 4 + 0] = blob->buffers[i].id;
        blob->buf_table[i * 4 + 1] = blob->buffers[i].size;
        blob->buf_table[i * 4 + 2] = blob->buffers[i].alignment;
        blob->buf_table[i * 4 + 3] = blob->buffers[i].phys_addr;
    }

    memcpy(blob->cmd_ring, buf + cmd_off, cmd_size);

    uint32_t d = 0;
    for (uint32_t i = 0; i < cmd_count; i++) {
        uint32_t op = read_le32(buf + cmd_off + i * CAD_CMD_ENTRY_BYTES);
        if (op == CAD_OP_BARRIER) {
            blob->commands[i].opcode = CAD_OP_BARRIER;
            blob->commands[i].kind = CAD_OPK_BARRIER;
            continue;
        }
        memcpy(&blob->descriptors[d * CAD_DESC_BYTES],
               buf + desc_off + d * CAD_DESC_BYTES,
               CAD_DESC_BYTES);
        blob->commands[i].opcode = op;
        blob->commands[i].desc_index = d;
        d++;
    }

    return blob;
}

uint32_t cad_command_blob_version_major(const cad_command_blob_t *blob) {
    return blob ? blob->version_major : 0;
}

uint32_t cad_command_blob_version_minor(const cad_command_blob_t *blob) {
    return blob ? blob->version_minor : 0;
}

size_t cad_command_blob_num_buffers(const cad_command_blob_t *blob) {
    return blob ? blob->buffer_count : 0;
}

size_t cad_command_blob_num_commands(const cad_command_blob_t *blob) {
    return blob ? blob->command_count : 0;
}

const uint8_t *cad_command_blob_command_ring(const cad_command_blob_t *blob,
                                             size_t *out_size) {
    if (!blob || !out_size) return NULL;
    *out_size = blob->command_count * CAD_CMD_ENTRY_BYTES;
    return blob->cmd_ring;
}

const uint8_t *cad_command_blob_descriptors(const cad_command_blob_t *blob,
                                            size_t *out_size) {
    if (!blob || !out_size) return NULL;
    uint32_t desc_count = 0;
    for (uint32_t i = 0; i < blob->command_count; i++) {
        if (blob->commands[i].kind != CAD_OPK_BARRIER) desc_count++;
    }
    *out_size = desc_count * CAD_DESC_BYTES;
    return blob->descriptors;
}

const uint64_t *cad_command_blob_buffer_table(const cad_command_blob_t *blob,
                                              size_t *out_size) {
    if (!blob || !out_size) return NULL;
    *out_size = blob->buffer_count * 4 * sizeof(uint64_t);
    return blob->buf_table;
}
