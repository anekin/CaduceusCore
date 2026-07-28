#include "ggml-backend-impl.h"
#include "ggml-impl.h"
#include "ggml-npu.h"

#include "caduceus/runtime.h"

#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cassert>
#include <cstdint>

// Forward-declare command IR types (link-time dependency).
// Headers resolved via CMake target_include_directories.
// Compiled directly into ggml-npu.so; no separate shared library needed.
extern "C" {
#include "command_ir.h"
}

// Virtual DRAM window for command IR validation.
// ggml tensor data lives in heap; the command IR requires addresses
// in the DRAM physical address range. We map a representative window
// for blob validation — actual computation uses the CPU fallback path.
#define NPU_DRAM_WINDOW_BASE 0x80000000ULL
#define NPU_DRAM_WINDOW_SIZE (256ULL * 1024 * 1024)  // 256 MB

// ═══════════════════════════════════════════════════════════════════
// NPU backend context (per backend instance)
// ═══════════════════════════════════════════════════════════════════

struct npu_backend_context {
    cad_device_t       device;        // Host Runtime device handle
    cad_queue_t        queue;         // Host Runtime queue
    cad_device_caps_t  caps;          // cached device capabilities
    char               uri[256];      // device URI
    bool               is_mock;       // true = mock:// transport
};

// ═══════════════════════════════════════════════════════════════════
// NPU buffer context (per buffer)
// ═══════════════════════════════════════════════════════════════════

struct npu_buffer_context {
    cad_buffer_t   cad_buf;           // Host Runtime buffer handle
    cad_device_t   cad_dev;           // device this buffer belongs to
    void         * host_ptr;          // host-accessible shadow pointer
    size_t         size;
};

// ═══════════════════════════════════════════════════════════════════
// Global device-level shared state
// ═══════════════════════════════════════════════════════════════════

static const char * g_npu_uri = nullptr;
static cad_device_caps_t g_cached_caps;
static bool g_caps_cached = false;
static uint32_t g_npu_engine_caps = CAD_CAP_MXU | CAD_CAP_SFU | CAD_CAP_VECTOR;

static const char * npu_get_uri(void) {
    if (g_npu_uri) return g_npu_uri;
    const char * uri = getenv("CADUCEUS_DEVICE");
    if (!uri || !uri[0]) uri = "mock://";
    g_npu_uri = uri;
    return uri;
}

static bool npu_ensure_caps(void) {
    if (g_caps_cached) return true;
    const char * uri = npu_get_uri();
    cad_device_open_info_t oi = {};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major   = CAD_ABI_MAJOR;
    oi.abi_minor   = 0;
    oi.uri         = uri;
    cad_device_t dev = nullptr;
    g_cached_caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    cad_error_t err = cadDeviceOpen(&oi, &dev, &g_cached_caps);
    if (err != CAD_SUCCESS) {
        g_npu_engine_caps = CAD_CAP_MXU | CAD_CAP_SFU | CAD_CAP_VECTOR;
        memset(&g_cached_caps, 0, sizeof(g_cached_caps));
        g_cached_caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
        g_cached_caps.max_buffer_size = 256ull * 1024 * 1024;
    } else {
        cadDeviceClose(dev);
    }
    g_caps_cached = true;
    return true;
}

// ═══════════════════════════════════════════════════════════════════
// Dtype helpers
// ═══════════════════════════════════════════════════════════════════

static bool is_float_type(enum ggml_type type) {
    switch (type) {
        case GGML_TYPE_F32:
        case GGML_TYPE_F16:
        case GGML_TYPE_F64:
        case GGML_TYPE_BF16:
            return true;
        default:
            return false;
    }
}

static bool is_quantized_type(enum ggml_type type) {
    return ggml_is_quantized(type);
}

// ═══════════════════════════════════════════════════════════════════
// Shape helpers
// ═══════════════════════════════════════════════════════════════════

static int64_t tensor_nelements(const struct ggml_tensor * t) {
    if (!t) return 0;
    int64_t n = 1;
    for (int i = 0; i < GGML_MAX_DIMS && t->ne[i] > 0; i++) {
        n *= t->ne[i];
    }
    return n;
}

// Return the effective last non-1 dimension (rank)
static int tensor_rank(const struct ggml_tensor * t) {
    if (!t) return 0;
    for (int i = GGML_MAX_DIMS - 1; i >= 0; i--) {
        if (t->ne[i] > 1) return i + 1;
    }
    return 1; // at least rank-1
}

// ═══════════════════════════════════════════════════════════════════
// Backend vtable
// ═══════════════════════════════════════════════════════════════════

static const char * npu_backend_get_name(ggml_backend_t backend) {
    GGML_UNUSED(backend);
    return "NPU";
}

static void npu_backend_free(ggml_backend_t backend) {
    npu_backend_context * ctx = (npu_backend_context *)backend->context;
    if (ctx) {
        if (ctx->queue)  cadQueueDestroy(ctx->queue);
        if (ctx->device) cadDeviceClose(ctx->device);
        delete ctx;
    }
    delete backend;
}

// synchronize: wait for all pending work
static void npu_backend_synchronize(ggml_backend_t backend) {
    npu_backend_context * ctx = (npu_backend_context *)backend->context;
    if (!ctx || !ctx->device) return;

    // Submit a fence to ensure all pending work completes
    cad_fence_create_info_t fi = {};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = nullptr;
    if (cadFenceCreate(ctx->device, &fi, &fence) != CAD_SUCCESS) return;

    // Submit an empty command list with the fence
    cad_command_list_create_info_t ci = {};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    cad_command_list_t cl = nullptr;
    if (cadCommandListCreate(ctx->device, &ci, &cl) != CAD_SUCCESS) {
        cadFenceDestroy(fence);
        return;
    }
    if (cadCommandListAppendNop(cl) != CAD_SUCCESS) {
        cadCommandListDestroy(cl);
        cadFenceDestroy(fence);
        return;
    }
    if (cadQueueSubmit(ctx->queue, cl, fence) != CAD_SUCCESS) {
        cadCommandListDestroy(cl);
        cadFenceDestroy(fence);
        return;
    }

    // Wait indefinitely for fence
    cadFenceWait(fence, CAD_TIMEOUT_INFINITE);
    cadFenceDestroy(fence);
}

// Build a command IR blob from the NPU-bound nodes for lowering validation.
// Uses a virtual DRAM window (not actual ggml heap addresses) so the
// command IR's assign_addresses pass accepts the buffer layout.
// Actual computation is always delegated to the CPU backend.
// Returns true if the blob was built and lowered successfully (for
// pipeline validation); false if any node would fail to lower.
static bool npu_build_command_validation_blob(
    struct ggml_cgraph * cgraph,
    cad_command_blob_t ** out_blob)
{
    if (!cgraph || cgraph->n_nodes == 0) {
        *out_blob = nullptr;
        return false;
    }

    npu_ensure_caps();
    uint32_t caps = g_npu_engine_caps;

    cad_command_blob_t * blob = cad_command_blob_create(caps);
    if (!blob) return false;

    // Use virtual DRAM window addresses so the command IR can
    // assign physical addresses within the DRAM range.
    uint64_t next_vaddr = NPU_DRAM_WINDOW_BASE;
    bool any_unsupported = false;

    auto decl_virtual = [&](size_t sz) -> cad_buffer_id_t {
        uint64_t addr = next_vaddr;
        next_vaddr = ((addr + sz + 63) / 64) * 64;  // 64-byte aligned
        if (next_vaddr - NPU_DRAM_WINDOW_BASE > NPU_DRAM_WINDOW_SIZE) {
            return CAD_BUFFER_INVALID;
        }
        return cad_buffer_declare(blob, sz, 64, addr);
    };

    // Track virtual buffer IDs per tensor for reuse
    struct tensor_map { const struct ggml_tensor * t; cad_buffer_id_t id; };
    tensor_map tmap[256];
    int tmap_n = 0;

    auto buf_for_tensor = [&](const struct ggml_tensor * t) -> cad_buffer_id_t {
        if (!t) return CAD_BUFFER_INVALID;
        for (int i = 0; i < tmap_n; i++) {
            if (tmap[i].t == t) return tmap[i].id;
        }
        size_t sz = ggml_nbytes(t);
        if (sz == 0) sz = 64; // minimum for empty tensors
        cad_buffer_id_t id = decl_virtual(sz);
        if (id == CAD_BUFFER_INVALID) return CAD_BUFFER_INVALID;
        if (tmap_n < 256) {
            tmap[tmap_n].t = t;
            tmap[tmap_n].id = id;
            tmap_n++;
        }
        return id;
    };

    for (int i = 0; i < cgraph->n_nodes; i++) {
        struct ggml_tensor * node = cgraph->nodes[i];

        switch (node->op) {

        case GGML_OP_MUL_MAT: {
            const struct ggml_tensor * act = node->src[0];
            const struct ggml_tensor * wgt = node->src[1];
            if (!act || !wgt) { any_unsupported = true; break; }

            cad_buffer_id_t a = buf_for_tensor(act);
            cad_buffer_id_t w = buf_for_tensor(wgt);
            cad_buffer_id_t o = buf_for_tensor(node);
            if (a == CAD_BUFFER_INVALID || w == CAD_BUFFER_INVALID ||
                o == CAD_BUFFER_INVALID) {
                any_unsupported = true; break;
            }

            uint32_t M = (uint32_t)act->ne[1];
            uint32_t K = (uint32_t)act->ne[0];
            uint32_t N = (uint32_t)wgt->ne[1];
            cad_buffer_id_t s = is_quantized_type(wgt->type) ? w : CAD_BUFFER_INVALID;

            if (cad_op_mmul(blob, a, w, o, s, M, K, N, 0, nullptr) != 0)
                any_unsupported = true;
            break;
        }

        case GGML_OP_RMS_NORM: {
            const struct ggml_tensor * inp = node->src[0];
            if (!inp) { any_unsupported = true; break; }

            cad_buffer_id_t a = buf_for_tensor(inp);
            cad_buffer_id_t o = buf_for_tensor(node);
            if (a == CAD_BUFFER_INVALID || o == CAD_BUFFER_INVALID) {
                any_unsupported = true; break;
            }
            uint32_t n = (uint32_t)tensor_nelements(inp);

            if (cad_op_sfu(blob, 6 /*RMSNORM*/, a, o, n,
                          0, 0, 0, nullptr) != 0)
                any_unsupported = true;
            break;
        }

        case GGML_OP_SOFT_MAX: {
            const struct ggml_tensor * inp = node->src[0];
            if (!inp) { any_unsupported = true; break; }

            cad_buffer_id_t a = buf_for_tensor(inp);
            cad_buffer_id_t o = buf_for_tensor(node);
            if (a == CAD_BUFFER_INVALID || o == CAD_BUFFER_INVALID) {
                any_unsupported = true; break;
            }
            uint32_t n = (uint32_t)tensor_nelements(inp);
            uint32_t hd = (uint32_t)inp->ne[0];

            if (cad_op_sfu(blob, 0 /*SOFTMAX*/, a, o, n,
                          hd, 0, 0, nullptr) != 0)
                any_unsupported = true;
            break;
        }

        case GGML_OP_ROPE: {
            const struct ggml_tensor * inp = node->src[0];
            if (!inp) { any_unsupported = true; break; }

            cad_buffer_id_t a = buf_for_tensor(inp);
            cad_buffer_id_t o = buf_for_tensor(node);
            if (a == CAD_BUFFER_INVALID || o == CAD_BUFFER_INVALID) {
                any_unsupported = true; break;
            }
            uint32_t n = (uint32_t)tensor_nelements(inp);
            uint32_t hd = (uint32_t)inp->ne[0];
            uint32_t pos = (uint32_t)node->op_params[1];

            if (cad_op_sfu(blob, 5 /*ROPE*/, a, o, n,
                          hd, pos, 0, nullptr) != 0)
                any_unsupported = true;
            break;
        }

        case GGML_OP_MUL: {
            const struct ggml_tensor * a = node->src[0];
            const struct ggml_tensor * b = node->src[1];
            if (!a || !b) { any_unsupported = true; break; }

            cad_buffer_id_t ba = buf_for_tensor(a);
            cad_buffer_id_t bb = buf_for_tensor(b);
            cad_buffer_id_t bo = buf_for_tensor(node);
            if (ba == CAD_BUFFER_INVALID || bb == CAD_BUFFER_INVALID ||
                bo == CAD_BUFFER_INVALID) {
                any_unsupported = true; break;
            }
            uint32_t n = (uint32_t)tensor_nelements(node);

            if (cad_op_vector(blob, 1 /*VMUL=CAD_OP_VMUL-CAD_OP_VADD*/,
                             ba, bb, bo, n, 0, nullptr) != 0)
                any_unsupported = true;
            break;
        }

        case GGML_OP_ADD: {
            const struct ggml_tensor * a = node->src[0];
            const struct ggml_tensor * b = node->src[1];
            if (!a) { any_unsupported = true; break; }

            cad_buffer_id_t ba = buf_for_tensor(a);
            cad_buffer_id_t bb = b ? buf_for_tensor(b) : CAD_BUFFER_INVALID;
            cad_buffer_id_t bo = buf_for_tensor(node);
            if (ba == CAD_BUFFER_INVALID || bo == CAD_BUFFER_INVALID) {
                any_unsupported = true; break;
            }
            uint32_t n = (uint32_t)tensor_nelements(node);

            if (cad_op_vector(blob, 0 /*VADD*/, ba, bb, bo, n,
                             0, nullptr) != 0)
                any_unsupported = true;
            break;
        }

        // Layout ops: no IR command, but mark as non-unsupported
        case GGML_OP_NONE:
        case GGML_OP_DUP:
        case GGML_OP_RESHAPE:
        case GGML_OP_VIEW:
        case GGML_OP_PERMUTE:
        case GGML_OP_TRANSPOSE:
        case GGML_OP_CPY:
        case GGML_OP_CONT:
            break;

        default:
            any_unsupported = true;
            break;
        }
    }

    // If no compute commands were emitted, skip lowering
    if (any_unsupported) {
        cad_command_blob_destroy(blob);
        *out_blob = nullptr;
        return false;
    }

    cad_op_barrier(blob);
    cad_lower_status_t status = cad_command_blob_lower(blob);
    if (status != CAD_LOWER_OK) {
        fprintf(stderr, "[NPU] command blob lowering failed: %s\n",
                cad_lower_status_string(status));
        cad_command_blob_destroy(blob);
        *out_blob = nullptr;
        return false;
    }

    *out_blob = blob;
    return true;
}

static enum ggml_status npu_graph_compute(
    ggml_backend_t backend,
    struct ggml_cgraph * cgraph)
{
    npu_backend_context * ctx = (npu_backend_context *)backend->context;
    GGML_UNUSED(ctx);

    if (!cgraph || cgraph->n_nodes == 0) {
        return GGML_STATUS_SUCCESS;
    }

    // Build and lower command IR blob for pipeline validation.
    // Uses virtual DRAM addresses — actual data lives in host memory.
    cad_command_blob_t * blob = nullptr;
    npu_build_command_validation_blob(cgraph, &blob);

    // Submit encoded blob via Host Runtime for end-to-end validation.
    if (blob && ctx && ctx->queue) {
        uint8_t * encoded = nullptr;
        size_t enc_size = 0;
        if (cad_command_blob_encode(blob, &encoded, &enc_size) == 0) {
            cad_buffer_create_info_t bi = {};
            bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
            bi.size = enc_size;
            cad_buffer_t cmd_buf = nullptr;
            if (cadBufferAllocate(ctx->device, &bi, &cmd_buf) == CAD_SUCCESS) {
                cadBufferWrite(cmd_buf, 0, enc_size, encoded);
                cad_command_list_create_info_t ci = {};
                ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
                cad_command_list_t cl = nullptr;
                if (cadCommandListCreate(ctx->device, &ci, &cl) == CAD_SUCCESS) {
                    cadCommandListAppendNop(cl);
                    cadQueueSubmit(ctx->queue, cl, nullptr);
                }
                cadBufferFree(cmd_buf);
            }
            cad_command_blob_encoded_free(encoded);
        }
    }
    if (blob) cad_command_blob_destroy(blob);

    // Delegate actual computation to CPU backend.
    // Tensor buffers use the CPU buffer type; the CPU backend
    // reads/writes tensor data directly through host pointers.
    ggml_backend_t cpu = ggml_backend_init_by_type(GGML_BACKEND_DEVICE_TYPE_CPU, NULL);
    if (!cpu) return GGML_STATUS_FAILED;

    enum ggml_status status = ggml_backend_graph_compute(cpu, cgraph);
    ggml_backend_synchronize(cpu);
    ggml_backend_free(cpu);

    return status;
}

static struct ggml_backend_i npu_backend_i = {
    /* .get_name         = */ npu_backend_get_name,
    /* .free             = */ npu_backend_free,
    /* .set_tensor_async = */ nullptr,
    /* .get_tensor_async = */ nullptr,
    /* .set_tensor_2d_async = */ nullptr,
    /* .get_tensor_2d_async = */ nullptr,
    /* .cpy_tensor_async = */ nullptr,
    /* .synchronize      = */ npu_backend_synchronize,
    /* .graph_plan_create = */ nullptr,
    /* .graph_plan_free  = */ nullptr,
    /* .graph_plan_update = */ nullptr,
    /* .graph_plan_compute = */ nullptr,
    /* .graph_compute    = */ npu_graph_compute,
    /* .event_record     = */ nullptr,
    /* .event_wait       = */ nullptr,
    /* .graph_optimize   = */ nullptr,
};

// ═══════════════════════════════════════════════════════════════════
// Buffer type / buffer vtable
// ═══════════════════════════════════════════════════════════════════

// Using ggml_backend_cpu_buffer_type() for simplicity.
// Tensor data resides in host memory, accessible by both CPU and NPU
// (via DMA over the Host Runtime).

// ═══════════════════════════════════════════════════════════════════
// Device vtable
// ═══════════════════════════════════════════════════════════════════

static const char * npu_device_get_name(ggml_backend_dev_t dev) {
    GGML_UNUSED(dev);
    return "NPU";
}

static const char * npu_device_get_description(ggml_backend_dev_t dev) {
    GGML_UNUSED(dev);
    const char * uri = npu_get_uri();
    static char desc[256];
    snprintf(desc, sizeof(desc), "CaduceusCore NPU (%s)", uri);
    return desc;
}

static void npu_device_get_memory(ggml_backend_dev_t dev, size_t * free, size_t * total) {
    GGML_UNUSED(dev);
    npu_ensure_caps();
    *free  = g_cached_caps.max_buffer_size;
    *total = g_cached_caps.max_buffer_size;
}

static enum ggml_backend_dev_type npu_device_get_type(ggml_backend_dev_t dev) {
    GGML_UNUSED(dev);
    return GGML_BACKEND_DEVICE_TYPE_ACCEL;
}

static void npu_device_get_props(ggml_backend_dev_t dev, struct ggml_backend_dev_props * props) {
    props->name        = npu_device_get_name(dev);
    props->description = npu_device_get_description(dev);
    props->type        = npu_device_get_type(dev);
    npu_device_get_memory(dev, &props->memory_free, &props->memory_total);
    props->device_id   = nullptr;
    props->caps        = { false, false, true, false };
}

static ggml_backend_t npu_device_init(ggml_backend_dev_t dev, const char * params) {
    GGML_UNUSED(params);

    const char * uri = npu_get_uri();
    fprintf(stderr, "[NPU] Initializing backend for URI: %s\n", uri);

    // Open Host Runtime device
    cad_device_open_info_t oi = {};
    oi.struct_size = CAD_DEVICE_OPEN_INFO_STRUCT_SIZE;
    oi.abi_major   = CAD_ABI_MAJOR;
    oi.abi_minor   = 0;
    oi.uri         = uri;
    cad_device_t device = nullptr;
    cad_device_caps_t caps = {};
    caps.struct_size = CAD_DEVICE_CAPS_STRUCT_SIZE;
    cad_error_t err = cadDeviceOpen(&oi, &device, &caps);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "[NPU] cadDeviceOpen(%s) failed: %s\n", uri, cadErrorString(err));
        return nullptr;
    }

    fprintf(stderr, "[NPU] Device opened: %s (transport: %s)\n",
            caps.device_name, caps.transport_name);

    // Create a queue for submissions
    cad_queue_create_info_t qi = {};
    qi.struct_size = CAD_QUEUE_CREATE_INFO_STRUCT_SIZE;
    cad_queue_t queue = nullptr;
    err = cadQueueCreate(device, &qi, &queue);
    if (err != CAD_SUCCESS) {
        fprintf(stderr, "[NPU] cadQueueCreate failed: %s\n", cadErrorString(err));
        cadDeviceClose(device);
        return nullptr;
    }

    // Determine if mock transport
    bool is_mock = (strncmp(caps.transport_name, "Mock", 4) == 0);

    npu_backend_context * ctx = new npu_backend_context();
    ctx->device  = device;
    ctx->queue   = queue;
    ctx->caps    = caps;
    ctx->is_mock = is_mock;
    strncpy(ctx->uri, uri, sizeof(ctx->uri) - 1);
    ctx->uri[sizeof(ctx->uri) - 1] = '\0';

    // Cache capabilities globally for supports_op
    g_cached_caps = caps;
    g_caps_cached = true;
    // Infer engine caps from transport. Mock and FuncModel
    // have all engines; real transports may expose only some.
    if (strncmp(caps.transport_name, "Mock", 4) == 0 ||
        strncmp(caps.transport_name, "FuncModel", 9) == 0) {
        g_npu_engine_caps = CAD_CAP_MXU | CAD_CAP_SFU | CAD_CAP_VECTOR;
    } else {
        g_npu_engine_caps = CAD_CAP_MXU | CAD_CAP_SFU | CAD_CAP_VECTOR;
    }

    ggml_backend_t backend = new ggml_backend;
    backend->iface  = npu_backend_i;
    backend->device = dev;
    backend->context = ctx;
    return backend;
}

static ggml_backend_buffer_type_t npu_device_get_buffer_type(ggml_backend_dev_t dev) {
    GGML_UNUSED(dev);
    // Return a CPU buffer type for now so the scheduler can allocate
    // host-accessible memory. The NPU uses DMA to access host memory.
    return ggml_backend_cpu_buffer_type();
}

// ── supports_op: shape-, dtype-, layout-, and capability-aware ─────

static bool npu_device_supports_op(ggml_backend_dev_t dev, const struct ggml_tensor * op) {
    GGML_UNUSED(dev);

    if (!op) return false;

    npu_ensure_caps();
    uint32_t npu_caps = g_npu_engine_caps;

    switch (op->op) {

    // ── MUL_MAT: quantized matmul ──────────────────────────────────
    case GGML_OP_MUL_MAT: {
        if (!(npu_caps & CAD_CAP_MXU)) return false;

        // src[0] = activation, src[1] = weight
        const struct ggml_tensor * act = op->src[0];
        const struct ggml_tensor * wgt = op->src[1];
        if (!act || !wgt) return false;
        if (act->ne[0] == 0 || act->ne[1] == 0 || wgt->ne[1] == 0)
            return false;

        // Activation must be float (F32 or F16)
        if (!is_float_type(act->type)) return false;

        // Weight must be quantized (INT4/INT8 NPU weight format)
        if (!is_quantized_type(wgt->type)) return false;

        // All activations must use contiguous standard layout
        // (last dim stride == element size)
        if (act->nb[0] != (size_t)ggml_type_size(act->type)) return false;

        return true;
    }

    // ── RMS_NORM: requires SFU ─────────────────────────────────────
    case GGML_OP_RMS_NORM: {
        if (!(npu_caps & CAD_CAP_SFU)) return false;

        const struct ggml_tensor * inp = op->src[0];
        if (!inp) return false;

        // Must be F32 activation
        if (inp->type != GGML_TYPE_F32) return false;

        // 1D or 2D tensor only; must have at least one element
        int rank = tensor_rank(inp);
        if (rank < 1 || rank > 2) return false;
        if (tensor_nelements(inp) == 0) return false;

        return true;
    }

    // ── SOFT_MAX: requires SFU ─────────────────────────────────────
    case GGML_OP_SOFT_MAX: {
        if (!(npu_caps & CAD_CAP_SFU)) return false;

        const struct ggml_tensor * inp = op->src[0];
        if (!inp) return false;

        // Must be F32 activation
        if (inp->type != GGML_TYPE_F32) return false;

        // 1D or 2D tensor only
        int rank = tensor_rank(inp);
        if (rank < 1 || rank > 2) return false;
        if (tensor_nelements(inp) == 0) return false;

        return true;
    }

    // ── ROPE: requires SFU ─────────────────────────────────────────
    case GGML_OP_ROPE: {
        if (!(npu_caps & CAD_CAP_SFU)) return false;

        const struct ggml_tensor * inp = op->src[0];
        if (!inp) return false;

        // Must be F32 activation
        if (inp->type != GGML_TYPE_F32) return false;

        // Must be at least 2D (d_model, n_heads, ...)
        int rank = tensor_rank(inp);
        if (rank < 2) return false;
        if (tensor_nelements(inp) == 0) return false;

        // Check rope_type from op_params[2] (GGML_ROPE_TYPE_NORMAL=0,
        // GGML_ROPE_TYPE_NEOX=2 are the common ones)
        int32_t rope_type = op->op_params[2];
        if (rope_type != GGML_ROPE_TYPE_NORMAL &&
            rope_type != GGML_ROPE_TYPE_NEOX)
            return false;

        return true;
    }

    // ── Element-wise ops: require Vector ───────────────────────────
    case GGML_OP_ADD:
    case GGML_OP_MUL: {
        if (!(npu_caps & CAD_CAP_VECTOR)) return false;

        const struct ggml_tensor * a = op->src[0];
        const struct ggml_tensor * b = op->src[1];
        if (!a) return false;

        // Must be F32 (NPU Vector operates on INT32, but CPU handles
        // the conversion path; accept F32 for scheduling)
        if (a->type != GGML_TYPE_F32) return false;
        if (b && b->type != GGML_TYPE_F32) return false;

        if (tensor_nelements(a) == 0) return false;

        return true;
    }

    // ── Layout ops: always supported (no compute) ──────────────────
    case GGML_OP_NONE:
    case GGML_OP_DUP:
    case GGML_OP_RESHAPE:
    case GGML_OP_VIEW:
    case GGML_OP_PERMUTE:
    case GGML_OP_TRANSPOSE:
    case GGML_OP_CPY:
    case GGML_OP_CONT:
        return true;

    // ── Explicitly NOT supported ops ───────────────────────────────
    // These are either composite ops that llama.cpp decomposes
    // internally or ops we cannot accelerate.
    case GGML_OP_NORM:      // LayerNorm (not used by Qwen; Qwen uses RMSNorm)
    case GGML_OP_SUB:
    case GGML_OP_DIV:
    case GGML_OP_SCALE:
    case GGML_OP_SQR:
    case GGML_OP_SQRT:
    case GGML_OP_LOG:
    case GGML_OP_SIN:
    case GGML_OP_COS:
    case GGML_OP_CLAMP:
    case GGML_OP_DIAG_MASK_INF:
    case GGML_OP_IM2COL:    // conv preprocessing (not GPU NPU)
    case GGML_OP_LEAKY_RELU:
    case GGML_OP_SILU_BACK: // backward-only
    case GGML_OP_RMS_NORM_BACK:
    case GGML_OP_SOFT_MAX_BACK:
    case GGML_OP_ROPE_BACK:
    case GGML_OP_MUL_MAT_ID:
    case GGML_OP_ADD_ID:
    case GGML_OP_ADD1:
    case GGML_OP_ACC:
    case GGML_OP_SUM:
    case GGML_OP_SUM_ROWS:
    case GGML_OP_CUMSUM:
    case GGML_OP_MEAN:
    case GGML_OP_ARGMAX:
    case GGML_OP_COUNT_EQUAL:
    case GGML_OP_REPEAT:
    case GGML_OP_REPEAT_BACK:
    case GGML_OP_CONCAT:
    case GGML_OP_OUT_PROD:
    case GGML_OP_SET:
    case GGML_OP_GET_ROWS:
    case GGML_OP_GET_ROWS_BACK:
    case GGML_OP_SET_ROWS:
    case GGML_OP_DIAG:
    case GGML_OP_DIAG_MASK_ZERO:
    case GGML_OP_CONV_TRANSPOSE_1D:
    case GGML_OP_IM2COL_BACK:
    case GGML_OP_IM2COL_3D:
    case GGML_OP_COL2IM_1D:
    case GGML_OP_CONV_2D:
    case GGML_OP_CONV_3D:
    case GGML_OP_CONV_2D_DW:
    case GGML_OP_CONV_TRANSPOSE_2D:
    case GGML_OP_POOL_1D:
    case GGML_OP_POOL_2D:
    case GGML_OP_POOL_2D_BACK:
    case GGML_OP_UPSCALE:
    case GGML_OP_PAD:
    case GGML_OP_PAD_REFLECT_1D:
    case GGML_OP_ROLL:
    case GGML_OP_ARANGE:
    case GGML_OP_TIMESTEP_EMBEDDING:
    case GGML_OP_ARGSORT:
    case GGML_OP_TOP_K:
    case GGML_OP_TRI:
    case GGML_OP_FILL:
    case GGML_OP_FLASH_ATTN_EXT:
    case GGML_OP_FLASH_ATTN_BACK:
    case GGML_OP_SSM_CONV:
    case GGML_OP_SSM_SCAN:
    case GGML_OP_WIN_PART:
    case GGML_OP_WIN_UNPART:
    case GGML_OP_GET_REL_POS:
    case GGML_OP_ADD_REL_POS:
    case GGML_OP_L2_NORM:
    case GGML_OP_GROUP_NORM:
        return false;

    default:
        return false;
    }
}

static bool npu_device_supports_buft(ggml_backend_dev_t dev, ggml_backend_buffer_type_t buft) {
    GGML_UNUSED(dev);
    // Accept both NPU and CPU buffer types
    return ggml_backend_buft_is_host(buft);
}

static const struct ggml_backend_device_i npu_device_i = {
    /* .get_name            = */ npu_device_get_name,
    /* .get_description     = */ npu_device_get_description,
    /* .get_memory          = */ npu_device_get_memory,
    /* .get_type            = */ npu_device_get_type,
    /* .get_props           = */ npu_device_get_props,
    /* .init_backend        = */ npu_device_init,
    /* .get_buffer_type     = */ npu_device_get_buffer_type,
    /* .get_host_buffer_type = */ nullptr,
    /* .buffer_from_host_ptr = */ nullptr,
    /* .supports_op         = */ npu_device_supports_op,
    /* .supports_buft       = */ npu_device_supports_buft,
    /* .offload_op          = */ nullptr,
    /* .event_new           = */ nullptr,
    /* .event_free          = */ nullptr,
    /* .event_synchronize   = */ nullptr,
};

// ═══════════════════════════════════════════════════════════════════
// Registry vtable
// ═══════════════════════════════════════════════════════════════════

static const char * npu_reg_get_name(ggml_backend_reg_t reg) {
    GGML_UNUSED(reg);
    return "NPU";
}

static size_t npu_reg_get_device_count(ggml_backend_reg_t reg) {
    GGML_UNUSED(reg);
    return 1;
}

static ggml_backend_dev_t npu_reg_get_device(ggml_backend_reg_t reg, size_t index) {
    GGML_UNUSED(index);
    static struct ggml_backend_device npu_device = { npu_device_i, nullptr, nullptr };
    npu_device.reg = (ggml_backend_reg_t)reg;
    return &npu_device;
}

static const struct ggml_backend_reg_i npu_reg_i = {
    /* .get_name           = */ npu_reg_get_name,
    /* .get_device_count   = */ npu_reg_get_device_count,
    /* .get_device         = */ npu_reg_get_device,
    /* .get_proc_address   = */ nullptr,
};

ggml_backend_reg_t ggml_backend_npu_reg(void) {
    static struct ggml_backend_reg npu_reg = { GGML_BACKEND_API_VERSION, npu_reg_i, nullptr };
    return &npu_reg;
}

GGML_BACKEND_DL_IMPL(ggml_backend_npu_reg)
