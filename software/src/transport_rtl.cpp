/*
 * CaduceusCore RTL Transport — C++ implementation (FEASIBILITY-ONLY).
 *
 * Implements the cad_transport_ops_t vtable over the versioned binary
 * device protocol (FlatBuffers + CRC-32) on a Unix domain socket.
 *
 * rtl://mock → connects to a Python mock endpoint for contract validation.
 * rtl://      → checks EDA prerequisites (VCS + simv_soc_top) → NO-GO.
 *
 * Real SoC RTL integration is deferred.
 */

#include "caduceus/transport_rtl.h"

#include "device_protocol_generated.h"

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
#include <vector>

namespace cd = caduceus_device_protocol;

/* ── Protocol constants ─────────────────────────────────────────────────── */

static const uint32_t PROTO_MAGIC = 0x43414455U; /* 'CADU' */
static const uint32_t PROTO_VERSION = 1U;

/* ── Fake-fixture globals (test surface) ─────────────────────────────────── */

static int g_fake_fixture_enabled = 1; /* default: fake fixture ON */
static int g_missing_eda_mode = 0;     /* 0=pass, 1=no-vcs, 2=no-simv, 3=both */

/* ── Submit capture (test surface) ──────────────────────────────────────── */

static int g_rtl_capture_mode = 0;          /* 0=normal, 1=capture-only */
static std::vector<uint8_t> g_rtl_last_submit_blob;
static uint32_t g_rtl_last_submit_cmd_count = 0;

/* ── CRC-32/IEEE ─────────────────────────────────────────────────────────── */

static uint32_t crc32_table[256];
static int crc32_ready = 0;

static void crc32_init(void) {
    if (crc32_ready) return;
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int j = 0; j < 8; j++)
            c = (c & 1U) ? (0xEDB88320U ^ (c >> 1)) : (c >> 1);
        crc32_table[i] = c;
    }
    crc32_ready = 1;
}

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t len) {
    for (size_t i = 0; i < len; i++)
        crc = crc32_table[(crc ^ data[i]) & 0xFFU] ^ (crc >> 8);
    return crc;
}

static uint32_t crc32_compute(const uint8_t *data, size_t len) {
    crc32_init();
    return crc32_update(0xFFFFFFFFU, data, len) ^ 0xFFFFFFFFU;
}

/* ── Transport state ─────────────────────────────────────────────────────── */

typedef struct {
    int sock_fd;
    uint64_t next_request_id;
    char sock_path[108];
} rtl_transport_t;

/* ── URI parsing ─────────────────────────────────────────────────────────── */

static int rtl_parse_uri(const char *uri, char *path_out, size_t path_size,
                         int *is_mock) {
    if (!uri) return CAD_TR_ERR_INVAL;
    *is_mock = 0;

    /* rtl://mock or rtl://mock?sock=... */
    if (strncmp(uri, "rtl://mock", 10) == 0) {
        *is_mock = 1;
        const char *rest = uri + 10;
        if (*rest == '\0') {
            /* bare rtl://mock → default socket */
            strncpy(path_out, CAD_TRANSPORT_RTL_DEFAULT_SOCK_PATH, path_size - 1);
            path_out[path_size - 1] = '\0';
            return CAD_TR_SUCCESS;
        }
        /* rtl://mock?sock=... */
        const char *prefix = "?sock=";
        if (strncmp(rest, prefix, 5) == 0) {
            const char *p = rest + 5;
            size_t len = strlen(p);
            if (len == 0 || len >= path_size) return CAD_TR_ERR_INVAL;
            memcpy(path_out, p, len + 1);
            return CAD_TR_SUCCESS;
        }
        return CAD_TR_ERR_INVAL;
    }

    /* rtl:// (bare RTL) — only the exact prefix */
    if (strcmp(uri, "rtl://") == 0) {
        return CAD_TR_ERR_UNSUP; /* will be handled by preflight in init */
    }

    /* Any other rtl:// prefix that isn't mock → invalid */
    if (strncmp(uri, "rtl://", 6) == 0) {
        return CAD_TR_ERR_INVAL;
    }

    return CAD_TR_ERR_UNSUP;
}

/* ── EDA preflight ───────────────────────────────────────────────────────── */

/*
 * Check if VCS executable is available.  Looks for 'vcs' in PATH
 * and at the standard module-load location.
 */
static int rtl_check_vcs(void) {
    /* Test 1: PATH lookup via popen */
    FILE *fp = popen("which vcs 2>/dev/null", "r");
    if (fp) {
        char buf[256];
        if (fgets(buf, sizeof(buf), fp) && strlen(buf) > 1) {
            pclose(fp);
            return 1;
        }
        pclose(fp);
    }
    /* Test 2: known EDA path (module loaded) */
    fp = popen("test -x \"$VCS_HOME/bin/vcs\" && echo found 2>/dev/null", "r");
    if (fp) {
        char buf[64] = {0};
        if (fgets(buf, sizeof(buf), fp) && strstr(buf, "found")) {
            pclose(fp);
            return 1;
        }
        pclose(fp);
    }
    return 0;
}

/*
 * Check if the SoC RTL simulation executable exists.
 */
static int rtl_check_simv(void) {
    FILE *fp = popen("test -f simv_soc_top && echo found 2>/dev/null", "r");
    if (fp) {
        char buf[64] = {0};
        if (fgets(buf, sizeof(buf), fp) && strstr(buf, "found")) {
            pclose(fp);
            return 1;
        }
        pclose(fp);
    }
    return 0;
}

/*
 * Return a diagnostic string explaining why EDA preflight failed.
 */
static const char *rtl_eda_diagnostic(int vcs_ok, int simv_ok) {
    if (!vcs_ok && !simv_ok) return "EDA preflight: VCS not found AND simv_soc_top absent";
    if (!vcs_ok) return "EDA preflight: VCS not found (module load vcs required)";
    if (!simv_ok) return "EDA preflight: simv_soc_top binary absent (build SoC RTL first)";
    return "EDA preflight passed";
}

/* ── Socket helpers ──────────────────────────────────────────────────────── */

static int rtl_connect(const char *path) {
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

static int rtl_send_all(int fd, const uint8_t *data, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = send(fd, data + sent, len - sent, MSG_NOSIGNAL);
        if (n <= 0) return -1;
        sent += (size_t)n;
    }
    return 0;
}

static int rtl_recv_all(int fd, uint8_t *data, size_t len) {
    size_t rcvd = 0;
    while (rcvd < len) {
        ssize_t n = recv(fd, data + rcvd, len - rcvd, 0);
        if (n <= 0) return -1;
        rcvd += (size_t)n;
    }
    return 0;
}

/* ── FlatBuffers helpers ─────────────────────────────────────────────────── */

static std::vector<uint8_t> rtl_serialize(cd::DeviceMessageT *msg) {
    flatbuffers::FlatBufferBuilder fbb(256);
    auto root = cd::DeviceMessage::Pack(fbb, msg);
    fbb.Finish(root);
    return std::vector<uint8_t>(fbb.GetBufferPointer(),
                                fbb.GetBufferPointer() + fbb.GetSize());
}

static cd::MessageHeader rtl_header_with_crc(const cd::MessageHeader *h,
                                             uint32_t checksum) {
    return cd::MessageHeader(h->magic(), h->protocol_version(),
                             h->request_id(), h->opcode(),
                             h->payload_length(), h->status(), checksum);
}

static uint32_t rtl_checksum_of(cd::DeviceMessageT *msg) {
    cd::MessageHeader zh = rtl_header_with_crc(msg->header.get(), 0);
    cd::DeviceMessageT copy;
    copy.header = std::make_unique<cd::MessageHeader>(zh);
    copy.payload = msg->payload;
    auto wire = rtl_serialize(&copy);
    return crc32_compute(wire.data(), wire.size());
}

static void rtl_set_payload(cd::DeviceMessageT *msg,
                            const flatbuffers::FlatBufferBuilder &inner) {
    const uint8_t *p = inner.GetBufferPointer();
    msg->payload.assign(p, p + inner.GetSize());
}

/* ── Framing ─────────────────────────────────────────────────────────────── */

static int rtl_send_message(int fd, cd::DeviceMessageT *msg) {
    auto zh = rtl_header_with_crc(msg->header.get(), 0);
    cd::DeviceMessageT zero_msg;
    zero_msg.header = std::make_unique<cd::MessageHeader>(zh);
    zero_msg.payload = msg->payload;
    auto wire = rtl_serialize(&zero_msg);
    uint32_t crc = crc32_compute(wire.data(), wire.size());
    msg->header = std::make_unique<cd::MessageHeader>(
        rtl_header_with_crc(msg->header.get(), crc));
    wire = rtl_serialize(msg);
    if (wire.empty()) return CAD_TR_ERR_INVAL;

    uint32_t len_be = htonl((uint32_t)wire.size());
    if (rtl_send_all(fd, (const uint8_t *)&len_be, sizeof(len_be)) < 0)
        return CAD_TR_ERR_LOST;
    if (rtl_send_all(fd, wire.data(), wire.size()) < 0)
        return CAD_TR_ERR_LOST;
    return CAD_TR_SUCCESS;
}

static int rtl_recv_message(int fd, cd::DeviceMessageT *msg) {
    uint32_t len_be = 0;
    if (rtl_recv_all(fd, (uint8_t *)&len_be, sizeof(len_be)) < 0)
        return CAD_TR_ERR_LOST;
    uint32_t len = ntohl(len_be);
    if (len > 16 * 1024 * 1024) return CAD_TR_ERR_INVAL;

    std::vector<uint8_t> wire(len);
    if (rtl_recv_all(fd, wire.data(), len) < 0)
        return CAD_TR_ERR_LOST;

    flatbuffers::Verifier verifier(wire.data(), len);
    if (!cd::VerifyDeviceMessageBuffer(verifier))
        return CAD_TR_ERR_INVAL;

    {
        auto fb_msg = cd::GetDeviceMessage(wire.data());
        auto fb_header = fb_msg->header();
        if (!fb_header) return CAD_TR_ERR_INVAL;
        ptrdiff_t hoff = (const uint8_t *)fb_header - wire.data();
        std::vector<uint8_t> cw = wire;
        memset(&cw[hoff + 28], 0, 4);
        uint32_t computed = crc32_compute(cw.data(), cw.size());
        if (computed != fb_header->checksum()) return CAD_TR_ERR_INVAL;
    }

    auto unpacked = cd::GetDeviceMessage(wire.data())->UnPack();
    *msg = std::move(*unpacked);
    return CAD_TR_SUCCESS;
}

/* ── Response validation ─────────────────────────────────────────────────── */

static int rtl_validate_response(cd::DeviceMessageT *msg) {
    if (!msg->header) return CAD_TR_ERR_INVAL;
    const cd::MessageHeader *h = msg->header.get();
    if (h->magic() != PROTO_MAGIC) return CAD_TR_ERR_INVAL;
    if (h->protocol_version() != PROTO_VERSION) return CAD_TR_ERR_INVAL;
    if (h->payload_length() != msg->payload.size()) return CAD_TR_ERR_INVAL;
    return CAD_TR_SUCCESS;
}

/* ── Request send/exchange ───────────────────────────────────────────────── */

static int rtl_make_request(cd::DeviceMessageT *msg, cd::DeviceOpcode opcode,
                            uint64_t rid,
                            const flatbuffers::FlatBufferBuilder &inner) {
    rtl_set_payload(msg, inner);
    uint32_t plen = (uint32_t)inner.GetSize();
    msg->header = std::make_unique<cd::MessageHeader>(
        PROTO_MAGIC, PROTO_VERSION, rid,
        (uint32_t)opcode, plen,
        (uint32_t)cd::DeviceStatus_STATUS_OK, 0);
    uint32_t crc = rtl_checksum_of(msg);
    msg->header = std::make_unique<cd::MessageHeader>(
        PROTO_MAGIC, PROTO_VERSION, rid,
        (uint32_t)opcode, plen,
        (uint32_t)cd::DeviceStatus_STATUS_OK, crc);
    return CAD_TR_SUCCESS;
}

static int rtl_send_request(rtl_transport_t *tr, cd::DeviceOpcode opcode,
                            const flatbuffers::FlatBufferBuilder &inner,
                            cd::DeviceMessageT *response) {
    if (tr->sock_fd < 0) return CAD_TR_ERR_LOST;

    uint64_t rid = tr->next_request_id++;
    cd::DeviceMessageT req;
    int err = rtl_make_request(&req, opcode, rid, inner);
    if (err != CAD_TR_SUCCESS) return err;

    err = rtl_send_message(tr->sock_fd, &req);
    if (err != CAD_TR_SUCCESS) return err;

    err = rtl_recv_message(tr->sock_fd, response);
    if (err != CAD_TR_SUCCESS) return err;

    err = rtl_validate_response(response);
    if (err != CAD_TR_SUCCESS) return err;

    if (response->header->request_id() != rid) return CAD_TR_ERR_INVAL;

    uint32_t st = response->header->status();
    if (st != (uint32_t)cd::DeviceStatus_STATUS_OK) {
        switch ((cd::DeviceStatus)st) {
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

    if (response->header->opcode() != (uint32_t)opcode)
        return CAD_TR_ERR_INVAL;

    return CAD_TR_SUCCESS;
}

/* ── Vtable: device lifecycle ────────────────────────────────────────────── */

static int rtl_device_init(void *tpriv, const char *uri) {
    rtl_transport_t *tr = (rtl_transport_t *)calloc(1, sizeof(*tr));
    if (!tr) return CAD_TR_ERR_NOMEM;
    tr->sock_fd = -1;

    int is_mock = 0;
    int err = rtl_parse_uri(uri, tr->sock_path, sizeof(tr->sock_path), &is_mock);

    if (err == CAD_TR_ERR_UNSUP && !is_mock) {
        /* rtl:// (bare RTL) — EDA preflight */
        if (g_fake_fixture_enabled) {
            /* Under fake fixture, treat rtl:// like rtl://mock */
            is_mock = 1;
            strncpy(tr->sock_path, CAD_TRANSPORT_RTL_DEFAULT_SOCK_PATH,
                    sizeof(tr->sock_path) - 1);
            tr->sock_path[sizeof(tr->sock_path) - 1] = '\0';
        } else {
            int vcs_ok = rtl_check_vcs();
            int simv_ok = rtl_check_simv();

            /* Apply fake EDA failure injection */
            if (g_missing_eda_mode == 1) vcs_ok = 0;
            else if (g_missing_eda_mode == 2) simv_ok = 0;
            else if (g_missing_eda_mode == 3) { vcs_ok = 0; simv_ok = 0; }

            if (!vcs_ok || !simv_ok) {
                fprintf(stderr, "RTL transport: %s\n",
                        rtl_eda_diagnostic(vcs_ok, simv_ok));
                free(tr);
                return CAD_TR_ERR_UNSUP;
            }
            /* Real RTL path deferred — still return UNSUP for now */
            fprintf(stderr, "RTL transport: real SoC RTL path deferred\n");
            free(tr);
            return CAD_TR_ERR_UNSUP;
        }
    } else if (err != CAD_TR_SUCCESS) {
        free(tr);
        return err;
    }

    /* Mock path: connect to Unix socket */
    if (is_mock) {
        tr->sock_fd = rtl_connect(tr->sock_path);
        if (tr->sock_fd < 0) {
            fprintf(stderr, "RTL transport: cannot connect to %s\n",
                    tr->sock_path);
            free(tr);
            return CAD_TR_ERR_LOST;
        }
    }
    tr->next_request_id = 1;

    *(rtl_transport_t **)tpriv = tr;
    return CAD_TR_SUCCESS;
}

static void rtl_device_fini(void *tpriv) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    if (!tr) return;
    if (tr->sock_fd >= 0) close(tr->sock_fd);
    free(tr);
}

static int rtl_device_reset(void *tpriv) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    cd::DeviceResetRequestT req;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::DeviceResetRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    return rtl_send_request(tr, cd::DeviceOpcode_OPCODE_DEVICE_RESET,
                            inner, &resp);
}

/* ── Vtable: buffer management ───────────────────────────────────────────── */

static int rtl_buffer_alloc(void *tpriv, cad_transport_buffer_t **out,
                            uint64_t size) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    cd::BufferAllocRequestT req;
    req.size = size;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::BufferAllocRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    int err = rtl_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_ALLOC,
                               inner, &resp);
    if (err != CAD_TR_SUCCESS) return err;

    auto result = flatbuffers::GetRoot<cd::BufferAllocResponse>(
        resp.payload.data())->UnPack();
    uint64_t *handle = (uint64_t *)malloc(sizeof(uint64_t));
    if (!handle) return CAD_TR_ERR_NOMEM;
    *handle = result->handle;
    *out = (cad_transport_buffer_t *)handle;
    return CAD_TR_SUCCESS;
}

static void rtl_buffer_free(void *tpriv, cad_transport_buffer_t *bf) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    if (!bf) return;
    cd::BufferFreeRequestT req;
    req.handle = *(uint64_t *)bf;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::BufferFreeRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    (void)rtl_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_FREE,
                           inner, &resp);
    free(bf);
}

static int rtl_buffer_read(void *tpriv, cad_transport_buffer_t *bf,
                           uint64_t offset, uint64_t size, void *dst) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    if (!bf || !dst) return CAD_TR_ERR_INVAL;

    cd::BufferReadRequestT req;
    req.handle = *(uint64_t *)bf;
    req.offset = offset;
    req.size = size;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::BufferReadRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    int err = rtl_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_READ,
                               inner, &resp);
    if (err != CAD_TR_SUCCESS) return err;

    auto result = flatbuffers::GetRoot<cd::BufferReadResponse>(
        resp.payload.data())->UnPack();
    if (result->data.size() != size) return CAD_TR_ERR_INVAL;
    memcpy(dst, result->data.data(), size);
    return CAD_TR_SUCCESS;
}

static int rtl_buffer_write(void *tpriv, cad_transport_buffer_t *bf,
                            uint64_t offset, uint64_t size, const void *src) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    if (!bf || !src) return CAD_TR_ERR_INVAL;

    cd::BufferWriteRequestT req;
    req.handle = *(uint64_t *)bf;
    req.offset = offset;
    req.data.assign((const uint8_t *)src, (const uint8_t *)src + size);
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::BufferWriteRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    return rtl_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_WRITE,
                            inner, &resp);
}

static uint64_t rtl_buffer_size(void *tpriv, cad_transport_buffer_t *bf) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    if (!bf) return 0;

    cd::BufferSizeRequestT req;
    req.handle = *(uint64_t *)bf;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::BufferSizeRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    if (rtl_send_request(tr, cd::DeviceOpcode_OPCODE_BUFFER_SIZE,
                         inner, &resp) != CAD_TR_SUCCESS)
        return 0;
    auto result = flatbuffers::GetRoot<cd::BufferSizeResponse>(
        resp.payload.data())->UnPack();
    return result->size;
}

/* ── Vtable: fences ──────────────────────────────────────────────────────── */

static int rtl_fence_create(void *tpriv, cad_transport_fence_t **out) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    cd::FenceCreateRequestT req;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::FenceCreateRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    int err = rtl_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_CREATE,
                               inner, &resp);
    if (err != CAD_TR_SUCCESS) return err;

    auto result = flatbuffers::GetRoot<cd::FenceCreateResponse>(
        resp.payload.data())->UnPack();
    uint64_t *handle = (uint64_t *)malloc(sizeof(uint64_t));
    if (!handle) return CAD_TR_ERR_NOMEM;
    *handle = result->handle;
    *out = (cad_transport_fence_t *)handle;
    return CAD_TR_SUCCESS;
}

static void rtl_fence_destroy(void *tpriv, cad_transport_fence_t *fence) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    if (!fence) return;
    cd::FenceDestroyRequestT req;
    req.handle = *(uint64_t *)fence;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::FenceDestroyRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    (void)rtl_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_DESTROY,
                           inner, &resp);
    free(fence);
}

static int rtl_fence_wait(void *tpriv, cad_transport_fence_t *fence,
                          uint64_t timeout_ns) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    if (!fence) return CAD_TR_ERR_INVAL;

    cd::FenceWaitRequestT req;
    req.handle = *(uint64_t *)fence;
    req.timeout_ns = timeout_ns;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::FenceWaitRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    int err = rtl_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_WAIT,
                               inner, &resp);
    if (err == CAD_TR_ERR_TIMEDOUT) return CAD_TR_ERR_TIMEDOUT;
    if (err != CAD_TR_SUCCESS) return err;

    auto result = flatbuffers::GetRoot<cd::FenceWaitResponse>(
        resp.payload.data())->UnPack();
    return result->signalled ? CAD_TR_SUCCESS : CAD_TR_ERR_NOTREADY;
}

static int rtl_fence_poll(void *tpriv, cad_transport_fence_t *fence) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    if (!fence) return CAD_TR_ERR_INVAL;

    cd::FencePollRequestT req;
    req.handle = *(uint64_t *)fence;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::FencePollRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    int err = rtl_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_POLL,
                               inner, &resp);
    if (err == CAD_TR_ERR_NOTREADY) return CAD_TR_ERR_NOTREADY;
    if (err != CAD_TR_SUCCESS) return err;

    auto result = flatbuffers::GetRoot<cd::FencePollResponse>(
        resp.payload.data())->UnPack();
    return result->signalled ? CAD_TR_SUCCESS : CAD_TR_ERR_NOTREADY;
}

static int rtl_fence_status(void *tpriv, cad_transport_fence_t *fence) {
    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    if (!fence) return CAD_TR_ERR_INVAL;

    cd::FenceStatusRequestT req;
    req.handle = *(uint64_t *)fence;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::FenceStatusRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    int err = rtl_send_request(tr, cd::DeviceOpcode_OPCODE_FENCE_STATUS,
                               inner, &resp);
    if (err != CAD_TR_SUCCESS) return 2;
    auto result = flatbuffers::GetRoot<cd::FenceStatusResponse>(
        resp.payload.data())->UnPack();
    return (int)result->status;
}

/* ── Vtable: submit ──────────────────────────────────────────────────────── */

static int rtl_submit(void *tpriv, void *cmd_data, uint32_t cmd_count,
                      cad_transport_fence_t *fence) {
    cd::SubmitRequestT req;
    req.cmd_count = cmd_count;
    req.fence_handle = fence ? *(uint64_t *)fence : 0;

    if (cmd_data) {
        uint8_t *bytes = (uint8_t *)cmd_data;
        req.cmd_blob.assign(bytes, bytes + cmd_count);
    }

    /* Capture for test verification — return early without socket I/O */
    if (g_rtl_capture_mode) {
        g_rtl_last_submit_blob = req.cmd_blob;
        g_rtl_last_submit_cmd_count = req.cmd_count;
        return CAD_TR_SUCCESS;
    }

    rtl_transport_t *tr = (rtl_transport_t *)tpriv;
    flatbuffers::FlatBufferBuilder inner;
    auto root = cd::SubmitRequest::Pack(inner, &req);
    inner.Finish(root);
    cd::DeviceMessageT resp;
    return rtl_send_request(tr, cd::DeviceOpcode_OPCODE_SUBMIT,
                            inner, &resp);
}

/* ── Public API — fake fixture control ───────────────────────────────────── */

extern "C" {

void cad_rtl_set_fake_fixture(int enabled) {
    g_fake_fixture_enabled = enabled ? 1 : 0;
}

void cad_rtl_set_missing_eda(int mode) {
    g_missing_eda_mode = mode;
}

void cad_rtl_set_capture_mode(int enabled) {
    g_rtl_capture_mode = enabled ? 1 : 0;
}

const void *cad_rtl_get_last_submit_blob(uint32_t *size) {
    if (size) *size = g_rtl_last_submit_cmd_count;
    return g_rtl_last_submit_blob.empty() ? NULL : g_rtl_last_submit_blob.data();
}

int cad_transport_rtl_init(void **tpriv, const char *uri) {
    return rtl_device_init(tpriv, uri);
}

/* ── Vtable export (C linkage) ───────────────────────────────────────────── */

const cad_transport_ops_t cad_transport_rtl_ops = {
    .name          = "RTL",
    .device_init   = rtl_device_init,
    .device_fini   = rtl_device_fini,
    .device_reset  = rtl_device_reset,
    .buffer_alloc  = rtl_buffer_alloc,
    .buffer_free   = rtl_buffer_free,
    .buffer_read   = rtl_buffer_read,
    .buffer_write  = rtl_buffer_write,
    .buffer_size   = rtl_buffer_size,
    .fence_create  = rtl_fence_create,
    .fence_destroy = rtl_fence_destroy,
    .fence_wait    = rtl_fence_wait,
    .fence_poll    = rtl_fence_poll,
    .fence_status  = rtl_fence_status,
    .submit        = rtl_submit,
    .fence_get_exec_stats = NULL,
};

} /* extern "C" */
