#include "ggml-backend-impl.h"
#include "ggml-impl.h"
#include "ggml-npu.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>
#include <vector>
#include <atomic>
#include <unistd.h>
#include <sys/stat.h>

#define NPU_STIMULUS_DIR "/tmp/npu_stimulus"

// ─── Hex I/O for float32 ──────────────────────────

static void f32_to_hex_str(float v, char * out) {
    uint32_t bits;
    memcpy(&bits, &v, 4);
    snprintf(out, 10, "%08x\n", bits);
}

static void write_f32_hex(const char * path, const float * data, size_t n) {
    FILE * f = fopen(path, "w");
    if (!f) return;
    char buf[10];
    for (size_t i = 0; i < n; i++) {
        f32_to_hex_str(data[i], buf);
        fwrite(buf, 1, 9, f);
    }
    fclose(f);
}

static size_t read_f32_hex(const char * path, float * buf, size_t max_n) {
    FILE * f = fopen(path, "r");
    if (!f) return 0;
    size_t i = 0;
    unsigned int bits;
    while (i < max_n && fscanf(f, "%x", &bits) == 1) {
        memcpy(&buf[i], &bits, 4);
        i++;
    }
    fclose(f);
    return i;
}

static void write_sentinel(const char * path) {
    FILE * f = fopen(path, "w");
    if (f) fclose(f);
}

static bool file_exists(const char * path) {
    struct stat st;
    return stat(path, &st) == 0;
}

static void ensure_dir(const char * path) { mkdir(path, 0755); }

// ─── Phase 3: Hex-file batch compute ───────────────

struct MulMatTask {
    struct ggml_tensor * tensor;
    int64_t M, K, N;
    size_t act_bytes, out_bytes;
};

static std::atomic<int> g_batch_id{0};

static bool npu_compute_batch_hex(const std::vector<MulMatTask> & tasks) {
    if (tasks.empty()) return true;

    int bid = g_batch_id.fetch_add(1);
    char dir[256];
    snprintf(dir, sizeof(dir), NPU_STIMULUS_DIR "/batch_%05d", bid);
    ensure_dir(dir);

    // ── Write manifest.json ──
    char mpath[512];
    snprintf(mpath, sizeof(mpath), "%s/manifest.json", dir);
    FILE * mf = fopen(mpath, "w");
    if (!mf) return false;
    fprintf(mf, "{\"batch_id\":%d,\"ops\":[\n", bid);

    // ── Write activation hex files ──
    for (size_t i = 0; i < tasks.size(); i++) {
        if (i > 0) fprintf(mf, ",\n");

        char act_path[512], out_path[512];
        snprintf(act_path, sizeof(act_path), "%s/act_%zu.hex", dir, i);
        snprintf(out_path, sizeof(out_path), "%s/out_%zu.hex", dir, i);

        // Write activation data as float32 hex
        const float * act = (const float *)tasks[i].tensor->src[1]->data;
        size_t n_floats = tasks[i].act_bytes / sizeof(float);
        write_f32_hex(act_path, act, n_floats);

        const char * tname = tasks[i].tensor->src[0]->name;
        if (!tname || !tname[0]) tname = "";

        fprintf(mf, "  {\"name\":\"%s\",\"M\":%lld,\"K\":%lld,\"N\":%lld,"
                "\"act_file\":\"act_%zu.hex\",\"out_file\":\"out_%zu.hex\","
                "\"out_bytes\":%zu}",
                tname,
                (long long)tasks[i].M, (long long)tasks[i].K, (long long)tasks[i].N,
                i, i, tasks[i].out_bytes);
    }

    fprintf(mf, "\n]}\n");
    fclose(mf);

    // ── Write READY sentinel ──
    char ready_path[512];
    snprintf(ready_path, sizeof(ready_path), "%s/READY", dir);
    write_sentinel(ready_path);

    if (tasks.size() <= 3)
        fprintf(stderr, "[NPU] wrote batch %d (%zu ops) to %s/\n", bid, tasks.size(), dir);

    // ── Poll for DONE ──
    char done_path[512];
    snprintf(done_path, sizeof(done_path), "%s/DONE", dir);

    int timeout = 30000;  // 30s timeout (long for first dequant)
    int waited = 0;
    while (!file_exists(done_path)) {
        usleep(10000);  // 10ms
        waited += 10;
        if (waited > timeout) {
            fprintf(stderr, "[NPU] timeout waiting for DONE in %s/\n", dir);
            return false;
        }
    }

    // ── Read results ──
    for (size_t i = 0; i < tasks.size(); i++) {
        char out_path[512];
        snprintf(out_path, sizeof(out_path), "%s/out_%zu.hex", dir, i);
        float * dst = (float *)tasks[i].tensor->data;
        size_t n_out = tasks[i].out_bytes / sizeof(float);
        size_t nread = read_f32_hex(out_path, dst, n_out);
        if (nread != n_out) {
            fprintf(stderr, "[NPU] short read %s: %zu/%zu floats\n",
                    out_path, nread, n_out);
            // Zero-fill remainder
            if (nread < n_out)
                memset(dst + nread, 0, (n_out - nread) * sizeof(float));
        }
    }

    return true;
}

// ─── Backend ──────────────────────────────────────

static const char * npu_backend_get_name(ggml_backend_t backend) {
    GGML_UNUSED(backend); return "NPU";
}

static void npu_backend_free(ggml_backend_t backend) {
    delete backend;
}

static enum ggml_status npu_backend_graph_compute(ggml_backend_t backend, struct ggml_cgraph * cgraph) {
    static int call_count = 0;
    call_count++;

    if (call_count <= 3) {
        fprintf(stderr, "[NPU] graph_compute #%d: %d nodes (Phase 3 hex)\n",
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
        task.act_bytes = t->ne[1] * t->src[1]->ne[0] * ggml_type_size(t->src[1]->type);  // M * K * sizeof
        task.out_bytes = t->ne[1] * t->ne[0] * ggml_type_size(t->type);  // M * N * sizeof
        tasks.push_back(task);
    }

    // Batch compute via hex files
    bool ok = npu_compute_batch_hex(tasks);
    if (tasks.size() > 0 && call_count <= 3)
        fprintf(stderr, "[NPU] batch %zu MUL_MAT: %s\n", tasks.size(), ok ? "OK" : "FAIL");

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
static const char * npu_device_get_description(ggml_backend_dev_t dev)  { GGML_UNUSED(dev); return "CaduceusCore NPU (Phase 3 hex)"; }
static void npu_device_get_memory(ggml_backend_dev_t dev, size_t * f, size_t * t) {
    GGML_UNUSED(dev);
    // Report host memory so the scheduler will allocate tensors on NPU.
    // NPU uses CPU-side buffers (shared memory), so this is the system's
    // available RAM, not dedicated NPU memory.
    *f = 16ull * 1024 * 1024 * 1024;  // 16 GB free
    *t = 32ull * 1024 * 1024 * 1024;  // 32 GB total
}
static enum ggml_backend_dev_type npu_device_get_type(ggml_backend_dev_t dev) { GGML_UNUSED(dev); return GGML_BACKEND_DEVICE_TYPE_GPU; }

static void npu_device_get_props(ggml_backend_dev_t dev, struct ggml_backend_dev_props * props) {
    props->name = npu_device_get_name(dev);
    props->description = npu_device_get_description(dev);
    props->type = npu_device_get_type(dev);
    npu_device_get_memory(dev, &props->memory_free, &props->memory_total);
    props->caps = { false, false, true, false };
}

static ggml_backend_t npu_device_init(ggml_backend_dev_t dev, const char * params) {
    GGML_UNUSED(params);
    fprintf(stderr, "[NPU] Phase 3 device initialized\n");
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
