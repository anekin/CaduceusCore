/*
 * CaduceusCore Func Model Transport — C++ implementation.
 *
 * Implements the cad_transport_ops_t vtable over the versioned binary
 * device protocol (FlatBuffers + CRC-32) on a Unix domain socket.
 */

#include "caduceus/transport_fm.h"

#include "device_protocol_generated.h"
#include "caduceus/runtime.h"

#include <arpa/inet.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include <memory>
#include <unordered_map>
#include <vector>

namespace cd = caduceus_device_protocol;

/* ── Protocol constants ─────────────────────────────────────────────────── */

static const uint32_t FM_MAGIC = 0x43414455U;
static const uint32_t FM_PROTOCOL_VERSION = 1U;
static const uint64_t FM_TIMEOUT_INFINITE = 0xFFFFFFFFFFFFFFFFULL;

/* ── CRC-32/IEEE ────────────────────────────────────────────────────────── */

static uint32_t crc32_table[256];
static int crc32_table_initialized = 0;

static void crc32_init(void) {
    if (crc32_table_initialized) return;
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int j = 0; j < 8; j++) {
            c = (c & 1U) ? (0xEDB88320U ^ (c >> 1)) : (c >> 1);
        }
        crc32_table[i] = c;
    }
    crc32_table_initialized = 1;
}

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t len) {
    for (size_t i = 0; i < len; i++) {
        crc = crc32_table[(crc ^ data[i]) & 0xFFU] ^ (crc >> 8);
    }
    return crc;
}

static uint32_t crc32_compute(const uint8_t *data, size_t len) {
    crc32_init();
    return crc32_update(0xFFFFFFFFU, data, len) ^ 0xFFFFFFFFU;
}

/* ── Transport state ────────────────────────────────────────────────────── */

struct fm_exec_stats_t {
    uint32_t mmul_ops = 0;
    uint32_t sfu_ops = 0;
    uint32_t vector_ops = 0;
    uint32_t dma_ops = 0;
    uint64_t dma_bytes_read = 0;
    uint64_t dma_bytes_written = 0;
};

typedef struct {
    int sock_fd;
    uint64_t next_request_id;
    char sock_path[108]; /* sockaddr_un.sun_path max */
    std::unordered_map<uint64_t, fm_exec_stats_t> fence_stats;
} fm_transport_t;

/* ── Socket helpers ─────────────────────────────────────────────────────── */

static int fm_parse_uri(const char *uri, char *path_out, size_t path_size) {
    if (!uri) return CAD_TR_ERR_INVAL;

    /* fm://, fm://python, or fm://spike -> default */
    if (strcmp(uri, "fm://") == 0 || strcmp(uri, "fm://python") == 0
        || strcmp(uri, "fm://spike") == 0) {
        strncpy(path_out, CAD_TRANSPORT_FM_DEFAULT_SOCK_PATH, path_size - 1);
        path_out[path_size - 1] = '\0';
        return CAD_TR_SUCCESS;
    }

    /* fm://unix?path=... */
    const char *prefix = "fm://unix?path=";
    size_t plen = strlen(prefix);
    if (strncmp(uri, prefix, plen) == 0) {
        const char *p = uri + plen;
        size_t len = strlen(p);
        if (len == 0 || len >= path_size) return CAD_TR_ERR_INVAL;
        memcpy(path_out, p, len + 1);
        return CAD_TR_SUCCESS;
    }

    return CAD_TR_ERR_UNSUP;
}

static int fm_connect(const char *path) {
    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return -1;

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);

    if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    return fd;
}

static int fm_send_all(int fd, const uint8_t *data, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = send(fd, data + sent, len - sent, MSG_NOSIGNAL);
        if (n <= 0) return -1;
        sent += (size_t)n;
    }
    return 0;
}

static int fm_recv_all(int fd, uint8_t *data, size_t len) {
    size_t received = 0;
    while (received < len) {
        ssize_t n = recv(fd, data + received, len - received, 0);
        if (n <= 0) return -1;
        received += (size_t)n;
    }
    return 0;
}

/* ── FlatBuffers helpers ────────────────────────────────────────────────── */

static std::vector<uint8_t> fm_serialize_message(cd::DeviceMessageT *msg) {
    flatbuffers::FlatBufferBuilder fbb(256);
    auto root = cd::DeviceMessage::Pack(fbb, msg);
    fbb.Finish(root);
    return std::vector<uint8_t>(fbb.GetBufferPointer(),
                                fbb.GetBufferPointer() + fbb.GetSize());
}

static cd::MessageHeader fm_header_with_checksum(const cd::MessageHeader *h,
                                                 uint32_t checksum) {
    return cd::MessageHeader(
        h->magic(),
        h->protocol_version(),
        h->request_id(),
        h->opcode(),
        h->payload_length(),
        h->status(),
        checksum);
}

static uint32_t fm_checksum_of_message(cd::DeviceMessageT *msg) {
    cd::MessageHeader zero_checksum = fm_header_with_checksum(msg->header.get(), 0);
    cd::DeviceMessageT copy;
    copy.header = std::make_unique<cd::MessageHeader>(zero_checksum);
    copy.payload = msg->payload;
    auto wire = fm_serialize_message(&copy);
    return crc32_compute(wire.data(), wire.size());
}

static void fm_set_payload(cd::DeviceMessageT *msg,
                           const flatbuffers::FlatBufferBuilder &inner_fbb) {
    const uint8_t *p = inner_fbb.GetBufferPointer();
    msg->payload.assign(p, p + inner_fbb.GetSize());
}

/* ── Framing ────────────────────────────────────────────────────────────── */

static int fm_send_message(int fd, cd::DeviceMessageT *msg) {
    auto zero_header = fm_header_with_checksum(msg->header.get(), 0);
    cd::DeviceMessageT zero_msg;
    zero_msg.header = std::make_unique<cd::MessageHeader>(zero_header);
    zero_msg.payload = msg->payload;
    auto wire = fm_serialize_message(&zero_msg);
    uint32_t crc = crc32_compute(wire.data(), wire.size());
    msg->header = std::make_unique<cd::MessageHeader>(
        fm_header_with_checksum(msg->header.get(), crc));
    wire = fm_serialize_message(msg);
    if (wire.empty()) return CAD_TR_ERR_INVAL;

    uint32_t len_be = htonl((uint32_t)wire.size());
    if (fm_send_all(fd, (const uint8_t *)&len_be, sizeof(len_be)) < 0) {
        return CAD_TR_ERR_LOST;
    }
    if (fm_send_all(fd, wire.data(), wire.size()) < 0) {
        return CAD_TR_ERR_LOST;
    }
    return CAD_TR_SUCCESS;
}

static int fm_recv_message(int fd, cd::DeviceMessageT *msg) {
    uint32_t len_be = 0;
    if (fm_recv_all(fd, (uint8_t *)&len_be, sizeof(len_be)) < 0) {
        return CAD_TR_ERR_LOST;
    }
    uint32_t len = ntohl(len_be);
    if (len > 16 * 1024 * 1024) return CAD_TR_ERR_INVAL;

    std::vector<uint8_t> wire(len);
    if (fm_recv_all(fd, wire.data(), len) < 0) {
        return CAD_TR_ERR_LOST;
    }

    flatbuffers::Verifier verifier(wire.data(), len);
    if (!cd::VerifyDeviceMessageBuffer(verifier)) {
        return CAD_TR_ERR_INVAL;
    }

    /* Validate checksum over the raw wire bytes: zero the checksum field
       (last 4 bytes of the 32-byte inline MessageHeader struct) then CRC. */
    {
        auto fb_msg = cd::GetDeviceMessage(wire.data());
        auto fb_header = fb_msg->header();
        if (!fb_header) return CAD_TR_ERR_INVAL;
        ptrdiff_t header_off = (const uint8_t *)fb_header - wire.data();
        uint32_t claimed = fb_header->checksum();
        std::vector<uint8_t> check_wire = wire; /* mutable copy */
        memset(&check_wire[header_off + 28], 0, 4);
        uint32_t computed = crc32_compute(check_wire.data(), check_wire.size());
        if (computed != claimed) return CAD_TR_ERR_INVAL;
    }

    std::unique_ptr<cd::DeviceMessageT> unpacked(
        cd::GetDeviceMessage(wire.data())->UnPack());
    *msg = std::move(*unpacked);
    return CAD_TR_SUCCESS;
}

/* ── Checksum / header validation ───────────────────────────────────────── */

static int fm_validate_response_header(cd::DeviceMessageT *msg) {
    if (!msg->header) return CAD_TR_ERR_INVAL;
    const cd::MessageHeader *h = msg->header.get();
    if (h->magic() != FM_MAGIC) return CAD_TR_ERR_INVAL;
    if (h->protocol_version() != FM_PROTOCOL_VERSION) return CAD_TR_ERR_INVAL;
    if (h->payload_length() != msg->payload.size()) return CAD_TR_ERR_INVAL;
    /* Checksum already validated over raw wire bytes in fm_recv_message. */
    return CAD_TR_SUCCESS;
}

/* ── Request building / exchange ────────────────────────────────────────── */

static int fm_build_request(cd::DeviceMessageT *msg, cd::DeviceOpcode opcode,
                            uint64_t request_id,
                            const flatbuffers::FlatBufferBuilder &inner_fbb) {
    fm_set_payload(msg, inner_fbb);
    msg->header = std::make_unique<cd::MessageHeader>(
        FM_MAGIC,
        FM_PROTOCOL_VERSION,
        request_id,
        (uint32_t)opcode,
        (uint32_t)inner_fbb.GetSize(),
        (uint32_t)cd::DeviceStatus_STATUS_OK,
        0);
    uint32_t crc = fm_checksum_of_message(msg);
    msg->header = std::make_unique<cd::MessageHeader>(
        FM_MAGIC,
        FM_PROTOCOL_VERSION,
        request_id,
        (uint32_t)opcode,
        (uint32_t)inner_fbb.GetSize(),
        (uint32_t)cd::DeviceStatus_STATUS_OK,
        crc);
    return CAD_TR_SUCCESS;
}

static int fm_send_request(fm_transport_t *tr, cd::DeviceOpcode opcode,
                           const flatbuffers::FlatBufferBuilder &inner_fbb,
                           cd::DeviceMessageT *response) {
    if (tr->sock_fd < 0) return CAD_TR_ERR_LOST;

    uint64_t rid = tr->next_request_id++;
    cd::DeviceMessageT req;
    int err = fm_build_request(&req, opcode, rid, inner_fbb);
    if (err != CAD_TR_SUCCESS) return err;

    err = fm_send_message(tr->sock_fd, &req);
    if (err != CAD_TR_SUCCESS) return err;

    err = fm_recv_message(tr->sock_fd, response);
    if (err != CAD_TR_SUCCESS) return err;

    err = fm_validate_response_header(response);
    if (err != CAD_TR_SUCCESS) return err;

    if (response->header->request_id() != rid) return CAD_TR_ERR_INVAL;

    if (response->header->status() != (uint32_t)cd::DeviceStatus_STATUS_OK) {
        std::unique_ptr<cd::ErrorResponseT> err_msg(
            flatbuffers::GetRoot<cd::ErrorResponse>(response->payload.data())->UnPack());
        (void)err_msg;
        switch ((cd::DeviceStatus)response->header->status()) {
        case cd::DeviceStatus_STATUS_OUT_OF_MEMORY: return CAD_TR_ERR_NOMEM;
        case cd::DeviceStatus_STATUS_TIMEOUT: return CAD_TR_ERR_TIMEDOUT;
        case cd::DeviceStatus_STATUS_NOT_READY: return CAD_TR_ERR_NOTREADY;
        case cd::DeviceStatus_STATUS_BUSY: return CAD_TR_ERR_BUSY;
        case cd::DeviceStatus_STATUS_INVALID_HANDLE:
        case cd::DeviceStatus_STATUS_INVALID_ARGUMENT:
        case cd::DeviceStatus_STATUS_INVALID_MESSAGE:
        case cd::DeviceStatus_STATUS_CHECKSUM_MISMATCH:
        case cd::DeviceStatus_STATUS_VERSION_MISMATCH:
        case cd::DeviceStatus_STATUS_REQUEST_OUT_OF_ORDER:
        case cd::DeviceStatus_STATUS_UNKNOWN_OPCODE:
            return CAD_TR_ERR_INVAL;
        default: return CAD_TR_ERR_LOST;
        }
    }

    if (response->header->opcode() != (uint32_t)opcode) {
        return CAD_TR_ERR_INVAL;
    }

    return CAD_TR_SUCCESS;
}

/* ── Vtable: device lifecycle ───────────────────────────────────────────── */

static int fm_device_init(void *tpriv, const char *uri) {
    fm_transport_t *tr = new fm_transport_t();
    tr->sock_fd = -1;

    int err = fm_parse_uri(uri, tr->sock_path, sizeof(tr->sock_path));
    if (err != CAD_TR_SUCCESS) {
        delete tr;
        return err;
    }

    tr->sock_fd = fm_connect(tr->sock_path);
    if (tr->sock_fd < 0) {
        delete tr;
        return CAD_TR_ERR_LOST;
    }
    tr->next_request_id = 1;

    *(fm_transport_t **)tpriv = tr;
    return CAD_TR_SUCCESS;
}

static void fm_device_fini(void *tpriv) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!tr) return;
    if (tr->sock_fd >= 0) close(tr->sock_fd);
    delete tr;
}

static int fm_device_reset(void *tpriv) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    cd::DeviceResetRequestT req;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::DeviceResetRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    return fm_send_request(tr, cd::DeviceOpcode_OPCODE_DEVICE_RESET, inner_fbb, &resp);
}

/* ── Vtable: buffer management ───────────────────────────────────────────── */

static int fm_buffer_alloc(void *tpriv, cad_transport_buffer_t **out,
                           uint64_t size) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    cd::BufferAllocRequestT req;
    req.size = size;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::BufferAllocRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    int err = fm_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_ALLOC, inner_fbb, &resp);
    if (err != CAD_TR_SUCCESS) return err;

    std::unique_ptr<cd::BufferAllocResponseT> inner(
        flatbuffers::GetRoot<cd::BufferAllocResponse>(resp.payload.data())->UnPack());

    uint64_t *handle = (uint64_t *)malloc(sizeof(uint64_t));
    if (!handle) return CAD_TR_ERR_NOMEM;
    *handle = inner->handle;
    *out = (cad_transport_buffer_t *)handle;
    return CAD_TR_SUCCESS;
}

static void fm_buffer_free(void *tpriv, cad_transport_buffer_t *bf) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!bf) return;
    cd::BufferFreeRequestT req;
    req.handle = *(uint64_t *)bf;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::BufferFreeRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    (void)fm_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_FREE, inner_fbb, &resp);
    free(bf);
}

static int fm_buffer_read(void *tpriv, cad_transport_buffer_t *bf,
                          uint64_t offset, uint64_t size, void *dst) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!bf || !dst) return CAD_TR_ERR_INVAL;

    cd::BufferReadRequestT req;
    req.handle = *(uint64_t *)bf;
    req.offset = offset;
    req.size = size;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::BufferReadRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    int err = fm_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_READ, inner_fbb, &resp);
    if (err != CAD_TR_SUCCESS) return err;

    std::unique_ptr<cd::BufferReadResponseT> inner(
        flatbuffers::GetRoot<cd::BufferReadResponse>(resp.payload.data())->UnPack());
    if (inner->data.size() != size) return CAD_TR_ERR_INVAL;
    memcpy(dst, inner->data.data(), size);
    return CAD_TR_SUCCESS;
}

static int fm_buffer_write(void *tpriv, cad_transport_buffer_t *bf,
                           uint64_t offset, uint64_t size, const void *src) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!bf || !src) return CAD_TR_ERR_INVAL;

    cd::BufferWriteRequestT req;
    req.handle = *(uint64_t *)bf;
    req.offset = offset;
    req.data.assign((const uint8_t *)src, (const uint8_t *)src + size);
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::BufferWriteRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    return fm_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_WRITE, inner_fbb, &resp);
}

static uint64_t fm_buffer_size(void *tpriv, cad_transport_buffer_t *bf) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!bf) return 0;

    cd::BufferSizeRequestT req;
    req.handle = *(uint64_t *)bf;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::BufferSizeRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    if (fm_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_SIZE, inner_fbb, &resp)
        != CAD_TR_SUCCESS) {
        return 0;
    }
    std::unique_ptr<cd::BufferSizeResponseT> inner(
        flatbuffers::GetRoot<cd::BufferSizeResponse>(resp.payload.data())->UnPack());
    return inner->size;
}

/* ── Vtable: fences ─────────────────────────────────────────────────────── */

static int fm_fence_create(void *tpriv, cad_transport_fence_t **out) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    cd::FenceCreateRequestT req;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::FenceCreateRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    int err = fm_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_CREATE, inner_fbb, &resp);
    if (err != CAD_TR_SUCCESS) return err;

    std::unique_ptr<cd::FenceCreateResponseT> inner(
        flatbuffers::GetRoot<cd::FenceCreateResponse>(resp.payload.data())->UnPack());

    uint64_t *handle = (uint64_t *)malloc(sizeof(uint64_t));
    if (!handle) return CAD_TR_ERR_NOMEM;
    *handle = inner->handle;
    *out = (cad_transport_fence_t *)handle;
    return CAD_TR_SUCCESS;
}

static void fm_fence_destroy(void *tpriv, cad_transport_fence_t *fence) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!fence) return;
    cd::FenceDestroyRequestT req;
    req.handle = *(uint64_t *)fence;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::FenceDestroyRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    (void)fm_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_DESTROY, inner_fbb, &resp);
    free(fence);
}

static int fm_fence_wait(void *tpriv, cad_transport_fence_t *fence,
                         uint64_t timeout_ns) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!fence) return CAD_TR_ERR_INVAL;

    cd::FenceWaitRequestT req;
    req.handle = *(uint64_t *)fence;
    req.timeout_ns = timeout_ns;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::FenceWaitRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    int err = fm_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_WAIT, inner_fbb, &resp);
    if (err == CAD_TR_ERR_TIMEDOUT) return CAD_TR_ERR_TIMEDOUT;
    if (err != CAD_TR_SUCCESS) return err;

    std::unique_ptr<cd::FenceWaitResponseT> inner(
        flatbuffers::GetRoot<cd::FenceWaitResponse>(resp.payload.data())->UnPack());
    return inner->signalled ? CAD_TR_SUCCESS : CAD_TR_ERR_NOTREADY;
}

static int fm_fence_poll(void *tpriv, cad_transport_fence_t *fence) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!fence) return CAD_TR_ERR_INVAL;

    cd::FencePollRequestT req;
    req.handle = *(uint64_t *)fence;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::FencePollRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    int err = fm_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_POLL, inner_fbb, &resp);
    if (err == CAD_TR_ERR_NOTREADY) return CAD_TR_ERR_NOTREADY;
    if (err != CAD_TR_SUCCESS) return err;

    std::unique_ptr<cd::FencePollResponseT> inner(
        flatbuffers::GetRoot<cd::FencePollResponse>(resp.payload.data())->UnPack());
    return inner->signalled ? CAD_TR_SUCCESS : CAD_TR_ERR_NOTREADY;
}

static int fm_fence_status(void *tpriv, cad_transport_fence_t *fence) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!fence) return CAD_TR_ERR_INVAL;

    cd::FenceStatusRequestT req;
    req.handle = *(uint64_t *)fence;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::FenceStatusRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    int err = fm_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_STATUS, inner_fbb, &resp);
    if (err != CAD_TR_SUCCESS) return 2; /* error */

    std::unique_ptr<cd::FenceStatusResponseT> inner(
        flatbuffers::GetRoot<cd::FenceStatusResponse>(resp.payload.data())->UnPack());
    return (int)inner->status;
}

/* ── Vtable: submit ─────────────────────────────────────────────────────── */

static int fm_submit(void *tpriv, void *cmd_data, uint32_t cmd_count,
                     cad_transport_fence_t *fence) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;

    cd::SubmitRequestT req;
    req.cmd_count = cmd_count;
    if (cmd_data && cmd_count > 0) {
        const uint8_t *src = (const uint8_t *)cmd_data;
        req.cmd_blob.assign(src, src + cmd_count);
    }
    req.fence_handle = fence ? *(uint64_t *)fence : 0;
    flatbuffers::FlatBufferBuilder inner_fbb;
    auto root = cd::SubmitRequest::Pack(inner_fbb, &req);
    inner_fbb.Finish(root);
    cd::DeviceMessageT resp;
    int err = fm_send_request(tr, cd::DeviceOpcode_OPCODE_SUBMIT, inner_fbb, &resp);
    if (err != CAD_TR_SUCCESS) return err;

    /* Parse exec_stats from SubmitResponse and cache on the fence. */
    if (fence && resp.payload.size() > 0) {
        auto sr = flatbuffers::GetRoot<cd::SubmitResponse>(resp.payload.data());
        auto es = sr->exec_stats();
        if (es) {
            fm_exec_stats_t stats;
            stats.mmul_ops = es->mmul_ops();
            stats.sfu_ops = es->sfu_ops();
            stats.vector_ops = es->vector_ops();
            stats.dma_ops = es->dma_ops();
            stats.dma_bytes_read = es->dma_bytes_read();
            stats.dma_bytes_written = es->dma_bytes_written();
            tr->fence_stats[*(uint64_t *)fence] = stats;
        }
    }

    return CAD_TR_SUCCESS;
}

/* ── Vtable export (C linkage for runtime core) ──────────────────────────── */

extern "C" {

static int fm_fence_get_exec_stats_fn(void *tpriv,
    cad_transport_fence_t *fence,
    uint32_t *mmul_ops,
    uint32_t *sfu_ops,
    uint32_t *vector_ops,
    uint32_t *dma_ops,
    uint64_t *dma_bytes_read,
    uint64_t *dma_bytes_written) {
    fm_transport_t *tr = (fm_transport_t *)tpriv;
    if (!fence) return -1;
    auto it = tr->fence_stats.find(*(uint64_t *)fence);
    if (it == tr->fence_stats.end()) return -1;
    if (mmul_ops)         *mmul_ops         = it->second.mmul_ops;
    if (sfu_ops)          *sfu_ops          = it->second.sfu_ops;
    if (vector_ops)       *vector_ops       = it->second.vector_ops;
    if (dma_ops)          *dma_ops          = it->second.dma_ops;
    if (dma_bytes_read)   *dma_bytes_read   = it->second.dma_bytes_read;
    if (dma_bytes_written)*dma_bytes_written= it->second.dma_bytes_written;
    return 0;
}

/* ── Transport error → string ─────────────────────────────────────── */

static const char *fm_transportErrorToString(void *tpriv, int error,
                                              char *buf, size_t len) {
    (void)tpriv;
    const char *base = cadErrorString((cad_error_t)error);
    switch (error) {
    case CAD_ERROR_DEVICE_LOST:
        snprintf(buf, len, "FM transport: socket write failed (%s)", base);
        break;
    case CAD_ERROR_TIMEOUT:
        snprintf(buf, len, "FM transport: timeout (%s)", base);
        break;
    case CAD_ERROR_INVALID_ARGUMENT:
        snprintf(buf, len, "FM transport: invalid protocol message (%s)", base);
        break;
    case CAD_ERROR_OUT_OF_MEMORY:
        snprintf(buf, len, "FM transport: out of memory (%s)", base);
        break;
    case CAD_ERROR_NOT_READY:
        snprintf(buf, len, "FM transport: device not ready (%s)", base);
        break;
    case CAD_ERROR_DEVICE_BUSY:
        snprintf(buf, len, "FM transport: device busy (%s)", base);
        break;
    case CAD_ERROR_UNSUPPORTED:
        snprintf(buf, len, "FM transport: unsupported operation (%s)", base);
        break;
    default:
        snprintf(buf, len, "FM transport: %s", base);
        break;
    }
    return buf;
}

const cad_transport_ops_t cad_transport_fm_ops = {
    .name          = "FuncModel",
    .device_init   = fm_device_init,
    .device_fini   = fm_device_fini,
    .device_reset  = fm_device_reset,
    .buffer_alloc  = fm_buffer_alloc,
    .buffer_free   = fm_buffer_free,
    .buffer_read   = fm_buffer_read,
    .buffer_write  = fm_buffer_write,
    .buffer_size   = fm_buffer_size,
    .fence_create  = fm_fence_create,
    .fence_destroy = fm_fence_destroy,
    .fence_wait    = fm_fence_wait,
    .fence_poll    = fm_fence_poll,
    .fence_status  = fm_fence_status,
    .submit        = fm_submit,
    .fence_get_exec_stats = fm_fence_get_exec_stats_fn,
    .transportErrorToString = fm_transportErrorToString,
};

int cad_transport_fm_init(void **tpriv, const char *uri) {
    return fm_device_init(tpriv, uri);
}

} /* extern "C" */
