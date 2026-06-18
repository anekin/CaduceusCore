#include "ggml-backend-impl.h"
#include "ggml-impl.h"
#include "ggml-npu.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
#include <fstream>
#include <vector>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <sys/stat.h>

#define NPU_SOCK_PATH "/tmp/ggml-npu.sock"
#define NPU_STIMULUS_DIR "/tmp/npu_stimulus"

// ─── Hex dump ─────────────────────────────────────

static void ensure_dir(const char * path) { mkdir(path, 0755); }

static void dump_hex(const char * filename, const void * data, size_t n_bytes, int type_size) {
    std::ofstream f(filename);
    if (!f.is_open()) return;
    const uint8_t * bytes = (const uint8_t *)data;
    for (size_t i = 0; i < n_bytes; i += type_size) {
        uint32_t val = 0;
        for (int j = 0; j < type_size && (i + j) < n_bytes; j++)
            val |= ((uint32_t)bytes[i + j]) << (j * 8);
        char buf[16];
        snprintf(buf, sizeof(buf), "%0*x ", type_size * 2, val);
        f << buf;
        if ((i / type_size + 1) % 16 == 0) f << "\n";
    }
    f << "\n";
    f.close();
}

// ─── Socket ───────────────────────────────────────

static int npu_sock = -1;

static bool sock_connect() {
    if (npu_sock >= 0) return true;
    npu_sock = socket(AF_UNIX, SOCK_STREAM, 0);
    if (npu_sock < 0) return false;
    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    strncpy(addr.sun_path, NPU_SOCK_PATH, sizeof(addr.sun_path) - 1);
    if (connect(npu_sock, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(npu_sock); npu_sock = -1; return false;
    }
    return true;
}

static void sock_send(const void * data, size_t len) {
    if (!sock_connect()) return;
    uint32_t hdr = (uint32_t)len;
    write(npu_sock, &hdr, 4);
    write(npu_sock, data, len);
}

static bool sock_recv_exact(void * buf, size_t n) {
    if (npu_sock < 0) return false;
    size_t got = 0;
    while (got < n) {
        ssize_t r = read(npu_sock, (char*)buf + got, n - got);
        if (r <= 0) return false;
        got += r;
    }
    return true;
}

// ─── Phase 2: Batched compute ─────────────────────

struct MulMatTask {
    struct ggml_tensor * tensor;
    int64_t M, K, N;
    size_t act_bytes, out_bytes;
};

static bool npu_compute_batch(const std::vector<MulMatTask> & tasks) {
    if (tasks.empty()) return true;
    if (!sock_connect()) return false;

    // Build JSON batch request
    std::string json = "{\"type\":\"batch\",\"n_ops\":";
    json += std::to_string(tasks.size());
    json += ",\"ops\":[";

    size_t total_act_bytes = 0;
    for (size_t i = 0; i < tasks.size(); i++) {
        if (i > 0) json += ",";
        char buf[256];
        snprintf(buf, sizeof(buf),
            "{\"M\":%lld,\"K\":%lld,\"N\":%lld,\"act_bytes\":%zu,\"out_bytes\":%zu}",
            (long long)tasks[i].M, (long long)tasks[i].K, (long long)tasks[i].N,
            tasks[i].act_bytes, tasks[i].out_bytes);
        json += buf;
        total_act_bytes += tasks[i].act_bytes;
    }
    json += "]}";

    // Send JSON header
    sock_send(json.c_str(), json.size());

    // Send all activation data concatenated
    for (size_t i = 0; i < tasks.size(); i++) {
        const void * act = tasks[i].tensor->src[1]->data;
        if (write(npu_sock, act, tasks[i].act_bytes) != (ssize_t)tasks[i].act_bytes)
            return false;
    }

    // Receive response: first 4B = total output bytes, then all output data
    uint32_t total_out;
    if (!sock_recv_exact(&total_out, 4)) return false;

    // Receive all output data into one buffer, then split per task
    std::vector<uint8_t> all_out(total_out);
    if (!sock_recv_exact(all_out.data(), total_out)) return false;

    size_t offset = 0;
    for (size_t i = 0; i < tasks.size(); i++) {
        if (offset + tasks[i].out_bytes > total_out) return false;
        memcpy(tasks[i].tensor->data, all_out.data() + offset, tasks[i].out_bytes);
        offset += tasks[i].out_bytes;
    }

    return true;
}

// ─── Backend ──────────────────────────────────────

static const char * npu_backend_get_name(ggml_backend_t backend) {
    GGML_UNUSED(backend); return "NPU";
}

static void npu_backend_free(ggml_backend_t backend) {
    if (npu_sock >= 0) { uint32_t z=0; write(npu_sock, &z, 4); close(npu_sock); npu_sock = -1; }
    delete backend;
}

static enum ggml_status npu_backend_graph_compute(ggml_backend_t backend, struct ggml_cgraph * cgraph) {
    static int call_count = 0;
    call_count++;

    if (call_count <= 3) {
        fprintf(stderr, "[NPU] graph_compute #%d: %d nodes (Phase 2 batched)\n",
                call_count, cgraph->n_nodes);
        if (call_count == 3) fprintf(stderr, "[NPU] (further logs suppressed)\n");
    }

    // Collect all MUL_MAT tasks
    std::vector<MulMatTask> tasks;
    for (int i = 0; i < cgraph->n_nodes; i++) {
        struct ggml_tensor * t = cgraph->nodes[i];
        if (t->op != GGML_OP_MUL_MAT) continue;
        if (!t->src[0] || !t->src[1] || !t->data) continue;
        if (!t->src[0]->data || !t->src[1]->data) continue;

        MulMatTask task;
        task.tensor = t;
        task.M = t->ne[1];  // rows (1 for decode)
        task.N = t->ne[0];  // cols (output dim)
        task.K = t->src[0]->ne[0];  // inner dim
        task.act_bytes = ggml_nbytes(t->src[1]);
        task.out_bytes = ggml_nbytes(t);
        tasks.push_back(task);
    }

    // Batch compute
    bool ok = npu_compute_batch(tasks);
    if (call_count <= 3)
        fprintf(stderr, "[NPU] batch %zu MUL_MAT: %s\n", tasks.size(), ok ? "OK" : "FAIL");

    // Dump stimulus hex on first call
    if (call_count == 1) {
        ensure_dir(NPU_STIMULUS_DIR);
        std::string meta = "{\"ops\":[";
        int dumped = 0;
        for (int i = 0; i < cgraph->n_nodes && dumped < 60; i++) {
            struct ggml_tensor * t = cgraph->nodes[i];
            if (t->op != GGML_OP_MUL_MAT) continue;
            if (!t->src[0] || !t->src[1] || !t->data) continue;
            if (!t->src[0]->data || !t->src[1]->data) continue;

            int tsz = (int)ggml_type_size(t->src[0]->type);
            int asz = (int)ggml_type_size(t->src[1]->type);
            int osz = (int)ggml_type_size(t->type);
            if (tsz <= 0) tsz = 4;

            size_t wb = ggml_nbytes(t->src[0]), ab = ggml_nbytes(t->src[1]), ob = ggml_nbytes(t);
            char pw[256], pa[256], po[256];
            snprintf(pw, sizeof(pw), "%s/op%02d_weight.hex", NPU_STIMULUS_DIR, dumped);
            snprintf(pa, sizeof(pa), "%s/op%02d_act.hex",   NPU_STIMULUS_DIR, dumped);
            snprintf(po, sizeof(po), "%s/op%02d_golden.hex", NPU_STIMULUS_DIR, dumped);

            dump_hex(pw, t->src[0]->data, wb, tsz);
            dump_hex(pa, t->src[1]->data, ab, asz);
            dump_hex(po, t->data, ob, osz);

            if (dumped > 0) meta += ",";
            char mb[512];
            snprintf(mb, sizeof(mb),
                "{\"id\":%d,\"op\":\"MUL_MAT\",\"M\":%lld,\"K\":%lld,\"N\":%lld,"
                "\"weight_type\":\"%s\",\"act_type\":\"%s\",\"out_type\":\"%s\","
                "\"weight_bytes\":%zu,\"act_bytes\":%zu,\"out_bytes\":%zu,"
                "\"files\":{\"weight\":\"op%02d_weight.hex\",\"activation\":\"op%02d_act.hex\",\"golden\":\"op%02d_golden.hex\"}}",
                dumped, (long long)t->ne[0], (long long)t->ne[1],
                (long long)(t->src[1] ? t->src[1]->ne[0] : 1),
                ggml_type_name(t->src[0]->type), ggml_type_name(t->src[1]->type), ggml_type_name(t->type),
                wb, ab, ob, dumped, dumped, dumped);
            meta += mb;
            dumped++;
        }
        meta += "]}";
        std::string mp = std::string(NPU_STIMULUS_DIR) + "/manifest.json";
        std::ofstream mf(mp); mf << meta; mf.close();
        fprintf(stderr, "[NPU] dumped %d MUL_MAT to %s/\n", dumped, NPU_STIMULUS_DIR);

        std::string msg = "{\"type\":\"stimulus\",\"manifest\":\"" + mp + "\",\"n_ops\":" + std::to_string(dumped) + "}";
        sock_send(msg.c_str(), msg.size());
    }

    // Monitoring trace
    if (call_count == 1) {
        std::string j = "{\"type\":\"graph_compute\",\"call\":1,\"n_nodes\":" + std::to_string(cgraph->n_nodes) + ",\"ops\":[]}";
        sock_send(j.c_str(), j.size());
    }

    GGML_UNUSED(backend);
    return GGML_STATUS_SUCCESS;
}

static struct ggml_backend_i npu_backend_i = {
    npu_backend_get_name, npu_backend_free,
    nullptr, nullptr, nullptr, nullptr, nullptr, nullptr,
    nullptr, nullptr, nullptr, nullptr,
    npu_backend_graph_compute,
    nullptr, nullptr, nullptr,
};

// ─── Device ──────────────────────────────────────

static const char * npu_device_get_name(ggml_backend_dev_t dev)     { GGML_UNUSED(dev); return "NPU0"; }
static const char * npu_device_get_description(ggml_backend_dev_t dev)  { GGML_UNUSED(dev); return "CaduceusCore NPU (Phase 2)"; }
static void npu_device_get_memory(ggml_backend_dev_t dev, size_t * f, size_t * t) { GGML_UNUSED(dev); *f=0; *t=0; }
static enum ggml_backend_dev_type npu_device_get_type(ggml_backend_dev_t dev) { GGML_UNUSED(dev); return GGML_BACKEND_DEVICE_TYPE_ACCEL; }

static void npu_device_get_props(ggml_backend_dev_t dev, struct ggml_backend_dev_props * props) {
    props->name = npu_device_get_name(dev);
    props->description = npu_device_get_description(dev);
    props->type = npu_device_get_type(dev);
    npu_device_get_memory(dev, &props->memory_free, &props->memory_total);
    props->caps = { false, false, true, false };
}

static ggml_backend_t npu_device_init(ggml_backend_dev_t dev, const char * params) {
    GGML_UNUSED(params);
    fprintf(stderr, "[NPU] Phase 2 device initialized\n");
    ggml_backend_t backend = new ggml_backend;
    backend->iface = npu_backend_i;
    backend->device = dev;
    backend->context = nullptr;
    return backend;
}

static ggml_backend_buffer_type_t npu_device_get_buffer_type(ggml_backend_dev_t dev) {
    GGML_UNUSED(dev); return ggml_backend_cpu_buffer_type();
}

static ggml_backend_buffer_t npu_device_buffer_from_host_ptr(
        ggml_backend_dev_t dev, void * ptr, size_t size, size_t max_tensor_size) {
    GGML_UNUSED(dev); GGML_UNUSED(max_tensor_size);
    return ggml_backend_cpu_buffer_from_ptr(ptr, size);
}

static bool npu_device_supports_op(ggml_backend_dev_t dev, const struct ggml_tensor * op) {
    GGML_UNUSED(dev);
    switch (op->op) {
        case GGML_OP_MUL_MAT:   return true;
        case GGML_OP_NONE:
        case GGML_OP_RESHAPE:
        case GGML_OP_VIEW:
        case GGML_OP_PERMUTE:
        case GGML_OP_TRANSPOSE:
        case GGML_OP_CPY:       return true;
        default:                return false;
    }
}

static bool npu_device_supports_buft(ggml_backend_dev_t dev, ggml_backend_buffer_type_t buft) {
    GGML_UNUSED(dev); return ggml_backend_buft_is_host(buft);
}

static const struct ggml_backend_device_i npu_device_i = {
    npu_device_get_name, npu_device_get_description,
    npu_device_get_memory, npu_device_get_type, npu_device_get_props,
    npu_device_init, npu_device_get_buffer_type, nullptr, npu_device_buffer_from_host_ptr,
    npu_device_supports_op, npu_device_supports_buft,
    nullptr, nullptr, nullptr, nullptr,
};

static const char * npu_reg_get_name(ggml_backend_reg_t reg) { GGML_UNUSED(reg); return "NPU"; }
static size_t npu_reg_get_device_count(ggml_backend_reg_t reg) { GGML_UNUSED(reg); return 1; }
static ggml_backend_dev_t npu_reg_get_device(ggml_backend_reg_t reg, size_t index) {
    GGML_UNUSED(index);
    static struct ggml_backend_device npu_device = { npu_device_i, nullptr, nullptr };
    npu_device.reg = (ggml_backend_reg_t)reg;
    return &npu_device;
}
static const struct ggml_backend_reg_i npu_reg_i = {
    npu_reg_get_name, npu_reg_get_device_count, npu_reg_get_device, nullptr,
};

ggml_backend_reg_t ggml_backend_npu_reg(void) {
    static struct ggml_backend_reg npu_reg = { GGML_BACKEND_API_VERSION, npu_reg_i, nullptr };
    return &npu_reg;
}
