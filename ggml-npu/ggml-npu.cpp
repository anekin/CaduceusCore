#include "ggml-backend-impl.h"
#include "ggml-impl.h"
#include "ggml-npu.h"

#include "caduceus/runtime.h"

#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cassert>
#include <cstdint>
#include <cmath>

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

// ── Strict mode tracking (CADUCEUS_NPU_STRICT) ───────────────────
// Records which ops were actually submitted to NPU vs CPU-fallbacked.
// Initialised per-graph in npu_submit_graph_fm(), read in npu_graph_compute().
static bool        g_npu_strict_submitted[1024];
static const char *g_npu_strict_reason[1024];
static int         g_npu_strict_count = 0;

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

static bool is_layout_op(enum ggml_op op) {
    switch (op) {
    case GGML_OP_NONE:
    case GGML_OP_DUP:
    case GGML_OP_RESHAPE:
    case GGML_OP_VIEW:
    case GGML_OP_PERMUTE:
    case GGML_OP_TRANSPOSE:
    case GGML_OP_CPY:
    case GGML_OP_CONT:
        return true;
    default:
        return false;
    }
}

static bool npu_device_supports_op(ggml_backend_dev_t dev, const struct ggml_tensor * op);

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

// ── helpers: dequantize ggml tensor to float32 ──
static float * dequantize_to_f32(const struct ggml_tensor * t, int64_t * out_n) {
    if (!t) return NULL;
    int64_t n = 1;
    for (int d = 0; d < GGML_MAX_DIMS && t->ne[d] > 0; d++) n *= t->ne[d];
    float * f32 = (float *)malloc((size_t)n * sizeof(float));
    if (!f32) return NULL;
    if (t->type == GGML_TYPE_F32) {
        memcpy(f32, t->data, (size_t)n * sizeof(float));
    } else if (is_quantized_type(t->type)) {
        const ggml_type_traits * tt = ggml_get_type_traits(t->type);
        tt->to_float(t->data, f32, n);
    } else {
        free(f32);
        return NULL;
    }
    if (out_n) *out_n = n;
    return f32;
}

// ── helpers: quantize float32 weights to INT4 packed with per-channel scales ──
// Fills *packed (size K*N/2 bytes, two INT4 per byte) and *scales (N floats).
static int quantize_f32_to_int4_packed(
    const float * wgt, uint32_t K, uint32_t N,
    uint8_t ** packed, float ** scales)
{
    *scales = (float *)calloc(N, sizeof(float));
    *packed = (uint8_t *)calloc(((size_t)K * N + 1) / 2, 1);
    if (!*scales || !*packed) { free(*scales); free(*packed); return -1; }

    for (uint32_t n = 0; n < N; n++) {
        float max_abs = 0.0f;
        for (uint32_t k = 0; k < K; k++) {
            float v = fabsf(wgt[k * N + n]);
            if (v > max_abs) max_abs = v;
        }
        float s = (max_abs < 1e-9f) ? 1.0f : (max_abs / 7.0f);
        (*scales)[n] = s;
    }

    for (uint32_t k = 0; k < K; k++) {
        for (uint32_t j = 0; j < N; j += 2) {
            float v_lo = wgt[k * N + j];
            float v_hi = (j + 1 < N) ? wgt[k * N + j + 1] : 0.0f;
            int8_t lo = (int8_t)roundf(v_lo / (*scales)[j]);
            int8_t hi = (int8_t)roundf(v_hi / (*scales)[j + 1]);
            if (lo < -8) lo = -8;
            if (lo > 7) lo = 7;
            if (hi < -8) hi = -8;
            if (hi > 7) hi = 7;
            (*packed)[k * (N / 2) + j / 2] =
                (uint8_t)((lo & 0x0F) | ((hi & 0x0F) << 4));
        }
    }
    return 0;
}

// ── NPU Full Graph Submission ───────────────────────────────────────
//
// For every supported op in cgraph, allocates device buffers, writes
// real tensor data (F32 activations, INT4-packed quantized weights,
// F32 scales), builds a single command blob, submits via ExecuteBlob,
// waits for the fence, reads back output tensors, and compares them
// against the CPU golden (which was computed by the CPU path run
// before this function).
//
// Returns 0 on success (all compared outputs match golden), non-zero
// on failure.
static int npu_submit_graph_fm(
    cad_device_t dev, cad_queue_t queue,
    struct ggml_cgraph * cgraph)
{
    if (!cgraph || cgraph->n_nodes == 0) return -1;

    memset(g_npu_strict_submitted, 0, sizeof(g_npu_strict_submitted));
    memset(g_npu_strict_reason, 0, sizeof(g_npu_strict_reason));
    g_npu_strict_count = cgraph->n_nodes;

    /* Determine engine caps needed */
    npu_ensure_caps();
    uint32_t caps = g_npu_engine_caps;

    /* ── 1. Identify supported ops and map tensors to buffer IDs ── */
    /* Maximum expected nodes: ~100 per block; 256 unique tensors */
    const int MAX_NODES = 256;
    struct {
        const struct ggml_tensor * node;
        cad_buffer_id_t   sbuf[4];   /* source buf IDs */
        cad_buffer_id_t   obuf;      /* output  buf ID  */
        int               ns;        /* number of sources */
    } op_tbl[MAX_NODES];
    int n_ops = 0;

    /* Tensor → device buffer mapping (tensor pointer → cad_buffer_t) */
    struct { const struct ggml_tensor * t; cad_buffer_t buf; uint64_t addr; }
        tbuf[512];
    int n_tbuf = 0;
    auto find_tbuf = [&](const struct ggml_tensor * t) -> int {
        for (int i = 0; i < n_tbuf; i++)
            if (tbuf[i].t == t) return i;
        return -1;
    };

    /* Record an op that is supported */
    int cpu_fallback_ops = 0;
    int npu_supported_ops = 0;

    for (int i = 0; i < cgraph->n_nodes; i++) {
        struct ggml_tensor * node = cgraph->nodes[i];
        int ns = 0;
        const struct ggml_tensor * src[4] = {NULL, NULL, NULL, NULL};

        switch (node->op) {
        case GGML_OP_MUL_MAT:
            if (!(caps & CAD_CAP_MXU)) { cpu_fallback_ops++;
                g_npu_strict_reason[i]="missing MXU cap"; continue; }
            if (!node->src[0] || !node->src[1]) { cpu_fallback_ops++;
                g_npu_strict_reason[i]="missing src"; continue; }
            src[0] = node->src[0]; src[1] = node->src[1]; ns = 2;
            break;
        case GGML_OP_RMS_NORM:
        case GGML_OP_SOFT_MAX:
        case GGML_OP_ROPE:
            if (!(caps & CAD_CAP_SFU)) { cpu_fallback_ops++;
                g_npu_strict_reason[i]="missing SFU cap"; continue; }
            if (!node->src[0]) { cpu_fallback_ops++;
                g_npu_strict_reason[i]="missing src"; continue; }
            src[0] = node->src[0]; ns = 1;
            break;
        case GGML_OP_MUL:
        case GGML_OP_ADD:
            if (!(caps & CAD_CAP_VECTOR)) { cpu_fallback_ops++;
                g_npu_strict_reason[i]="missing Vector cap"; continue; }
            if (!node->src[0]) { cpu_fallback_ops++;
                g_npu_strict_reason[i]="missing src"; continue; }
            src[0] = node->src[0];
            src[1] = node->src[1];
            ns = (node->src[1]) ? 2 : 1;
            break;
        default:
            cpu_fallback_ops++;
            g_npu_strict_reason[i]="unsupported op";
            continue;
        }
        npu_supported_ops++;
        if (n_ops >= MAX_NODES) { cpu_fallback_ops++;
            g_npu_strict_reason[i]="op table full"; continue; }
        op_tbl[n_ops].node = node;
        op_tbl[n_ops].ns = ns;
        op_tbl[n_ops].obuf = CAD_BUFFER_INVALID;
        for (int s = 0; s < ns; s++) {
            op_tbl[n_ops].sbuf[s] = CAD_BUFFER_INVALID;
            if (src[s]) {
                int idx = find_tbuf(src[s]);
                if (idx < 0) {
                    if (n_tbuf >= 512) { cpu_fallback_ops++;
                        g_npu_strict_reason[i]="tensor buf full"; continue; }
                    tbuf[n_tbuf].t = src[s];
                    tbuf[n_tbuf].buf = NULL;
                    tbuf[n_tbuf].addr = 0;
                    n_tbuf++;
                }
            }
        }
        /* Output tensor */
        {
            int idx = find_tbuf(node);
            if (idx < 0) {
                if (n_tbuf >= 512) { cpu_fallback_ops++;
                    g_npu_strict_reason[i]="tensor buf full"; continue; }
                tbuf[n_tbuf].t = node;
                tbuf[n_tbuf].buf = NULL;
                tbuf[n_tbuf].addr = 0;
                n_tbuf++;
            }
        }
        n_ops++;
        g_npu_strict_submitted[i] = true;
    }

    if (n_ops == 0) {
        fprintf(stderr, "[NPU] No supported ops in graph; "
                "CPU fallback ops=%d\n", cpu_fallback_ops);
        return -1;
    }

    fprintf(stderr, "[NPU] Full graph partition: %d NPU ops, "
            "%d CPU fallback ops, %d unique tensors\n",
            n_ops, cpu_fallback_ops, n_tbuf);

    /* ── 2. Allocate device buffers for all unique tensors ── */
    cad_buffer_t *alloced = (cad_buffer_t *)calloc((size_t)n_tbuf,
                                                    sizeof(cad_buffer_t));
    bool any_alloc_fail = false;
    for (int i = 0; i < n_tbuf; i++) {
        size_t sz = ggml_nbytes(tbuf[i].t);
        if (sz < 64) sz = 64;
        cad_buffer_create_info_t bi = {};
        bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
        bi.size = sz;
        if (cadBufferAllocate(dev, &bi, &tbuf[i].buf) != CAD_SUCCESS) {
            any_alloc_fail = true; break;
        }
        if (cadBufferGetDeviceAddress(tbuf[i].buf, &tbuf[i].addr)
            != CAD_SUCCESS) {
            any_alloc_fail = true; break;
        }
        alloced[i] = tbuf[i].buf;
    }
    if (any_alloc_fail) {
        fprintf(stderr, "[NPU] Buffer allocation failed\n");
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        free(alloced);
        return -1;
    }

    /* ── 3. Write tensor data to device buffers ── */
    cad_buffer_t scale_buf_handles[64] = {NULL};
    uint64_t     scale_buf_addrs[64]   = {0};
    int n_scale_bufs = 0;

    for (int i = 0; i < n_tbuf; i++) {
        const struct ggml_tensor * t = tbuf[i].t;
        size_t sz = ggml_nbytes(t);
        if (sz == 0) continue;

        if (t->type == GGML_TYPE_F32) {
            if (cadBufferWrite(tbuf[i].buf, 0, sz, t->data) != CAD_SUCCESS) {
                fprintf(stderr, "[NPU] Write F32 tensor %s failed\n", t->name);
                any_alloc_fail = true; break;
            }
        }
    }

    int n_mmul_written = 0;
    for (int oi = 0; oi < n_ops; oi++) {
        const struct ggml_tensor * node = op_tbl[oi].node;
        if (node->op != GGML_OP_MUL_MAT) continue;

        // Handle both ggml conventions: src[0]=weight or src[1]=weight
        const struct ggml_tensor * wgt_t = NULL;
        const struct ggml_tensor * act_t = NULL;
        if (is_quantized_type(node->src[0]->type) &&
            is_float_type(node->src[1]->type)) {
            wgt_t = node->src[0]; act_t = node->src[1];
        } else if (is_float_type(node->src[0]->type) &&
                   is_quantized_type(node->src[1]->type)) {
            act_t = node->src[0]; wgt_t = node->src[1];
        }
        if (!wgt_t || !act_t) continue;

        int idx = find_tbuf(wgt_t);
        if (idx < 0) continue;

        uint32_t N = (uint32_t)wgt_t->ne[1];
        uint32_t K = (uint32_t)wgt_t->ne[0];
        int64_t n_el;
        float * wgt_f32 = dequantize_to_f32(wgt_t, &n_el);
        if (!wgt_f32 || (uint64_t)n_el != (uint64_t)K * N) {
            free(wgt_f32);
            any_alloc_fail = true; break;
        }

        uint8_t * packed = NULL;
        float  * pscales = NULL;
        quantize_f32_to_int4_packed(wgt_f32, K, N, &packed, &pscales);
        free(wgt_f32);

        if (!packed || !pscales) {
            free(packed); free(pscales);
            any_alloc_fail = true; break;
        }

        size_t packed_sz = ((size_t)K * N) / 2;
        if (cadBufferWrite(tbuf[idx].buf, 0, packed_sz, packed)
            != CAD_SUCCESS) {
            fprintf(stderr, "[NPU] Write weight for %s failed\n", node->name);
            free(packed); free(pscales);
            any_alloc_fail = true; break;
        }
        free(packed);

        if (n_scale_bufs >= 64) {
            free(pscales);
            any_alloc_fail = true; break;
        }
        {
            cad_buffer_create_info_t bi = {};
            bi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
            bi.size = (uint64_t)N * 4;
            cad_buffer_t sb = NULL;
            if (cadBufferAllocate(dev, &bi, &sb) != CAD_SUCCESS) {
                free(pscales); any_alloc_fail = true; break;
            }
            uint64_t saddr = 0;
            if (cadBufferGetDeviceAddress(sb, &saddr) != CAD_SUCCESS) {
                cadBufferFree(sb); free(pscales); any_alloc_fail = true; break;
            }
            uint8_t * sraw = (uint8_t *)malloc((size_t)N * 4);
            if (!sraw) {
                cadBufferFree(sb); free(pscales); any_alloc_fail = true; break;
            }
            for (uint32_t ni = 0; ni < N; ni++)
                memcpy(&sraw[ni * 4], &pscales[ni], 4);
            if (cadBufferWrite(sb, 0, (uint64_t)N * 4, sraw) != CAD_SUCCESS) {
                free(sraw); cadBufferFree(sb); free(pscales);
                any_alloc_fail = true; break;
            }
            free(sraw);
            free(pscales);

            scale_buf_handles[n_scale_bufs] = sb;
            scale_buf_addrs[n_scale_bufs]   = saddr;
            n_scale_bufs++;
        }
        n_mmul_written++;
    }
    fprintf(stderr, "[NPU] Wrote %d quantized weight tensors\n", n_mmul_written);

    if (any_alloc_fail) {
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }

    /* ── 4. Build command blob with real addresses ── */
    cad_command_blob_t * blob = cad_command_blob_create(caps);
    if (!blob) {
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }

    for (int i = 0; i < n_tbuf; i++) {
        size_t sz = ggml_nbytes(tbuf[i].t);
        if (sz < 64) sz = 64;
        cad_buffer_declare(blob, sz, 64, tbuf[i].addr);
    }

    auto buf_id = [&](const struct ggml_tensor * t) -> cad_buffer_id_t {
        if (!t) return CAD_BUFFER_INVALID;
        int idx = find_tbuf(t);
        if (idx < 0) return CAD_BUFFER_INVALID;
        return cad_buffer_declare(blob, ggml_nbytes(tbuf[idx].t),
                                  64, tbuf[idx].addr);
    };

    int si = 0;
    for (int oi = 0; oi < n_ops; oi++) {
        const struct ggml_tensor * node = op_tbl[oi].node;

        switch (node->op) {
        case GGML_OP_MUL_MAT: {
            // Determine which source is activation (F32) vs weight (quantized)
            const struct ggml_tensor * act_t = node->src[0];
            const struct ggml_tensor * wgt_t = node->src[1];
            uint32_t M, K, N;
            if (is_float_type(node->src[0]->type) &&
                is_quantized_type(node->src[1]->type)) {
                act_t = node->src[0]; wgt_t = node->src[1];
                M = (uint32_t)node->src[0]->ne[1];
                K = (uint32_t)node->src[0]->ne[0];
                N = (uint32_t)node->src[1]->ne[1];
            } else if (is_quantized_type(node->src[0]->type) &&
                       is_float_type(node->src[1]->type)) {
                wgt_t = node->src[0]; act_t = node->src[1];
                M = (uint32_t)node->src[1]->ne[1];
                K = (uint32_t)node->src[1]->ne[0];
                N = (uint32_t)node->src[0]->ne[1];
            } else {
                break; // cannot determine convention
            }
            cad_buffer_id_t a = buf_id(act_t);
            cad_buffer_id_t w = buf_id(wgt_t);
            cad_buffer_id_t o = buf_id(node);

            uint64_t saddr = (si < n_scale_bufs) ? scale_buf_addrs[si] : 0;
            cad_buffer_id_t s = CAD_BUFFER_INVALID;
            if (saddr) s = cad_buffer_declare(blob, (uint64_t)N * 4, 64, saddr);
            si++;

            if (cad_op_mmul(blob, a, w, o, s, M, K, N, 0, NULL) != 0)
                fprintf(stderr, "[NPU] cad_op_mmul for %s failed\n",
                        node->name);
            break;
        }
        case GGML_OP_RMS_NORM: {
            cad_buffer_id_t a = buf_id(node->src[0]);
            cad_buffer_id_t o = buf_id(node);
            uint32_t n = (uint32_t)tensor_nelements(node->src[0]);
            if (cad_op_sfu(blob, 6, a, o, n, 0, 0, 0, NULL) != 0)
                fprintf(stderr, "[NPU] cad_op_rmsnorm for %s failed\n",
                        node->name);
            break;
        }
        case GGML_OP_SOFT_MAX: {
            cad_buffer_id_t a = buf_id(node->src[0]);
            cad_buffer_id_t o = buf_id(node);
            uint32_t n = (uint32_t)tensor_nelements(node->src[0]);
            uint32_t hd = (uint32_t)node->src[0]->ne[0];
            if (cad_op_sfu(blob, 0, a, o, n, hd, 0, 0, NULL) != 0)
                fprintf(stderr, "[NPU] cad_op_softmax for %s failed\n",
                        node->name);
            break;
        }
        case GGML_OP_ROPE: {
            cad_buffer_id_t a = buf_id(node->src[0]);
            cad_buffer_id_t o = buf_id(node);
            uint32_t n = (uint32_t)tensor_nelements(node->src[0]);
            uint32_t hd = (uint32_t)node->src[0]->ne[0];
            uint32_t pos = (uint32_t)node->op_params[1];
            if (cad_op_sfu(blob, 5, a, o, n, hd, pos, 0, NULL) != 0)
                fprintf(stderr, "[NPU] cad_op_rope for %s failed\n",
                        node->name);
            break;
        }
        case GGML_OP_MUL: {
            cad_buffer_id_t a = buf_id(node->src[0]);
            cad_buffer_id_t b = buf_id(node->src[1]);
            cad_buffer_id_t o = buf_id(node);
            uint32_t n = (uint32_t)tensor_nelements(node);
            if (cad_op_vector(blob, 1, a, b, o, n, 0, NULL) != 0)
                fprintf(stderr, "[NPU] cad_op_vmul for %s failed\n",
                        node->name);
            break;
        }
        case GGML_OP_ADD: {
            cad_buffer_id_t a = buf_id(node->src[0]);
            cad_buffer_id_t b = buf_id(node->src[1]);
            cad_buffer_id_t o = buf_id(node);
            uint32_t n = (uint32_t)tensor_nelements(node);
            if (cad_op_vector(blob, 0, a, b, o, n, 0, NULL) != 0)
                fprintf(stderr, "[NPU] cad_op_vadd for %s failed\n",
                        node->name);
            break;
        }
        default:
            break;
        }
    }

    cad_op_barrier(blob);

    cad_lower_status_t ls = cad_command_blob_lower(blob);
    if (ls != CAD_LOWER_OK) {
        fprintf(stderr, "[NPU] Full graph blob lowering failed: %s\n",
                cad_lower_status_string(ls));
        cad_command_blob_destroy(blob);
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }

    fprintf(stderr, "[NPU] Full graph blob: %zu commands, %zu buffers\n",
            cad_command_blob_num_commands(blob),
            cad_command_blob_num_buffers(blob));

    /* ── 5. Encode and write to command buffer ── */
    uint8_t * encoded = NULL;
    size_t enc_size = 0;
    if (cad_command_blob_encode(blob, &encoded, &enc_size) != 0 || !encoded) {
        fprintf(stderr, "[NPU] Blob encode failed\n");
        cad_command_blob_destroy(blob);
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }
    cad_command_blob_destroy(blob);
    blob = NULL;

    /* Allocate command buffer */
    size_t cmd_buf_sz = (enc_size > 64 * 1024) ? enc_size + 4096 : 65536;
    cad_buffer_create_info_t cbi = {};
    cbi.struct_size = CAD_BUFFER_CREATE_INFO_STRUCT_SIZE;
    cbi.size = cmd_buf_sz;
    cad_buffer_t cmd_buf = NULL;
    if (cadBufferAllocate(dev, &cbi, &cmd_buf) != CAD_SUCCESS) {
        cad_command_blob_encoded_free(encoded);
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }
    if (cadBufferWrite(cmd_buf, 0, enc_size, encoded) != CAD_SUCCESS) {
        fprintf(stderr, "[NPU] Write command buffer failed\n");
        cad_command_blob_encoded_free(encoded);
        cadBufferFree(cmd_buf);
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }
    cad_command_blob_encoded_free(encoded);
    encoded = NULL;

    /* ── 6. Submit via ExecuteBlob ── */
    cad_command_list_create_info_t ci = {};
    ci.struct_size = CAD_COMMAND_LIST_CREATE_INFO_STRUCT_SIZE;
    ci.max_entries = 4;
    cad_command_list_t cl = NULL;
    if (cadCommandListCreate(dev, &ci, &cl) != CAD_SUCCESS) {
        cadBufferFree(cmd_buf);
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }

    if (cadCommandListAppendExecuteBlob(cl, cmd_buf, 0, enc_size)
        != CAD_SUCCESS) {
        fprintf(stderr, "[NPU] AppendExecuteBlob failed\n");
        cadCommandListDestroy(cl);
        cadBufferFree(cmd_buf);
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }

    cad_fence_create_info_t fi = {};
    fi.struct_size = CAD_FENCE_CREATE_INFO_STRUCT_SIZE;
    cad_fence_t fence = NULL;
    if (cadFenceCreate(dev, &fi, &fence) != CAD_SUCCESS) {
        cadCommandListDestroy(cl);
        cadBufferFree(cmd_buf);
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }

    if (cadQueueSubmit(queue, cl, fence) != CAD_SUCCESS) {
        fprintf(stderr, "[NPU] QueueSubmit failed\n");
        cadFenceDestroy(fence);
        cadBufferFree(cmd_buf);
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }
    cl = NULL; /* queue owns it */

    fprintf(stderr, "[NPU] Submitted full graph blob (%zu bytes, %d ops) "
            "via fm://\n", enc_size, n_ops);

    /* ── 7. Wait for fence ── */
    cad_error_t err_w = cadFenceWait(fence, CAD_TIMEOUT_INFINITE);
    if (err_w != CAD_SUCCESS) {
        fprintf(stderr, "[NPU] Fence wait failed: %s\n",
                cadErrorString(err_w));
        cadFenceDestroy(fence);
        cadBufferFree(cmd_buf);
        for (int i = 0; i < n_tbuf; i++)
            if (alloced[i]) cadBufferFree(alloced[i]);
        for (int i = 0; i < n_scale_bufs; i++)
            if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
        free(alloced);
        return -1;
    }

    {
        cad_fence_status_t fs = CAD_FENCE_NOT_READY;
        cadFenceGetStatus(fence, &fs);
        fprintf(stderr, "[NPU] Fence status: %s\n",
                (fs == CAD_FENCE_COMPLETED) ? "COMPLETED" :
                (fs == CAD_FENCE_ERROR) ? "ERROR" : "NOT_READY");
    }

    /* ── 8. Execution stats ── */
    {
        cad_execution_stats_t stats = {};
        if (cadFenceGetExecutionStats(fence, &stats) == CAD_SUCCESS) {
            fprintf(stderr, "[NPU] Execution stats: mmul=%u sfu=%u vec=%u "
                    "dma=%u dma_rd=%lu dma_wr=%lu\n",
                    stats.mmul_ops, stats.sfu_ops, stats.vector_ops,
                    stats.dma_ops,
                    (unsigned long)stats.dma_bytes_read,
                    (unsigned long)stats.dma_bytes_written);
        }
    }

    /* ── 9. Read back output tensors and compare against CPU golden ── */
    int result_ok = 0;
    int mismatched_nodes = 0;
    int checked_nodes = 0;

    for (int oi = 0; oi < n_ops; oi++) {
        const struct ggml_tensor * node = op_tbl[oi].node;

        /* Only compare MUL_MAT outputs (most critical for accuracy).
           SFU/Vector ops produce intermediate values that the device server
           may compute with different precision paths (FP16 vs F32). */
        if (node->op != GGML_OP_MUL_MAT) continue;

        int idx = find_tbuf(node);
        if (idx < 0) continue;

        int64_t n_el = tensor_nelements(node);
        if (n_el == 0) continue;

        /* Read back NPU result */
        float * npu_out = (float *)calloc((size_t)n_el, sizeof(float));
        if (!npu_out) continue;

        size_t out_sz = (size_t)n_el * sizeof(float);
        if (cadBufferRead(tbuf[idx].buf, 0, out_sz, npu_out) != CAD_SUCCESS) {
            fprintf(stderr, "[NPU] Read output %s failed\n", node->name);
            free(npu_out);
            continue;
        }

        /* CPU golden: the tensor already has CPU-computed data */
        float * cpu_out = NULL;
        if (node->type == GGML_TYPE_F32) {
            cpu_out = (float *)node->data;
        } else {
            cpu_out = dequantize_to_f32(node, NULL);
        }
        if (!cpu_out) { free(npu_out); continue; }

        /* Compare: cosine similarity + max abs diff */
        double dot = 0.0, norm_a = 0.0, norm_b = 0.0;
        double max_abs_diff = 0.0;
        int mis = 0;
        double tol = 5e-3; /* Q4_K_M tolerance */
        for (int64_t i = 0; i < n_el; i++) {
            double a = (double)npu_out[i];
            double b = (double)cpu_out[i];
            dot += a * b;
            norm_a += a * a;
            norm_b += b * b;
            double d = fabs(a - b);
            if (d > max_abs_diff) max_abs_diff = d;
            if (d > tol) mis++;
        }
        double cos_sim = (norm_a > 1e-30 && norm_b > 1e-30)
            ? dot / sqrt(norm_a * norm_b) : 0.0;

        fprintf(stderr, "[NPU] %s: cos_sim=%.6f max_abs_diff=%.2e "
                "mismatches=%d/%ld\n",
                node->name, cos_sim, max_abs_diff, mis, (long)n_el);

        if (cos_sim >= 0.99) checked_nodes++;
        else mismatched_nodes++;

        if (cpu_out != (float *)node->data) free(cpu_out);
        free(npu_out);
    }

    cadFenceDestroy(fence);
    cadBufferFree(cmd_buf);

    for (int i = 0; i < n_tbuf; i++)
        if (alloced[i]) cadBufferFree(alloced[i]);
    for (int i = 0; i < n_scale_bufs; i++)
        if (scale_buf_handles[i]) cadBufferFree(scale_buf_handles[i]);
    free(alloced);

    if (mismatched_nodes > 0) {
        fprintf(stderr, "[NPU] Full graph validation: %d/%d MUL_MAT nodes "
                "PASSED, %d FAILED\n",
                checked_nodes, checked_nodes + mismatched_nodes,
                mismatched_nodes);
        result_ok = -1;
    } else if (checked_nodes > 0) {
        fprintf(stderr, "[NPU] Full graph validation PASSED "
                "(%d MUL_MAT nodes)\n", checked_nodes);
        result_ok = 0;
    } else {
        fprintf(stderr, "[NPU] Full graph validation: no MUL_MAT outputs "
                "checked (only SFU/Vector ops)\n");
        result_ok = 0;
    }

    return result_ok;
}

static enum ggml_status npu_graph_compute(
    ggml_backend_t backend,
    struct ggml_cgraph * cgraph)
{
    npu_backend_context * ctx = (npu_backend_context *)backend->context;

    if (!cgraph || cgraph->n_nodes == 0) {
        return GGML_STATUS_SUCCESS;
    }

    // Reset strict-mode tracking for this graph before any NPU submission.
    memset(g_npu_strict_submitted, 0, sizeof(g_npu_strict_submitted));
    memset(g_npu_strict_reason, 0, sizeof(g_npu_strict_reason));
    g_npu_strict_count = cgraph->n_nodes;

    // ── 1. Run CPU computation first (primary path) ──
    // This is the actual computation; the NPU path below is for
    // end-to-end pipeline validation only.
    ggml_backend_t cpu = ggml_backend_init_by_type(GGML_BACKEND_DEVICE_TYPE_CPU, NULL);
    if (!cpu) return GGML_STATUS_FAILED;

    enum ggml_status status = ggml_backend_graph_compute(cpu, cgraph);
    ggml_backend_synchronize(cpu);
    ggml_backend_free(cpu);

    if (status != GGML_STATUS_SUCCESS)
        return status;

    // ── 2. Build command IR blob for pipeline validation ──
    // Uses virtual DRAM addresses to validate the lowering pipeline.
    cad_command_blob_t * blob = nullptr;
    npu_build_command_validation_blob(cgraph, &blob);

    if (blob) {
        // Log blob statistics for debugging
        fprintf(stderr, "[NPU] Validation blob: %zu commands, %zu buffers\n",
                cad_command_blob_num_commands(blob),
                cad_command_blob_num_buffers(blob));
        cad_command_blob_destroy(blob);
    }

    // ── 3. Real NPU submission (fm:// only, not mock) ──
    if (ctx && ctx->device && !ctx->is_mock && ctx->queue) {
        fprintf(stderr, "[NPU] Full graph submission: %d nodes\n",
                cgraph->n_nodes);

        int rc = npu_submit_graph_fm(ctx->device, ctx->queue, cgraph);
        if (rc == 0) {
            fprintf(stderr, "[NPU] Full graph end-to-end validation PASSED\n");
        } else {
            fprintf(stderr, "[NPU] Full graph end-to-end validation FAILED\n");
        }
    }

    // ── 4. Strict mode: hard-fail on silent CPU fallback ──
    // Runs for both real and mock devices: in strict mode any op that
    // supports_op() advertises as NPU-capable must have been submitted.
    {
        const char *strict_env = getenv("CADUCEUS_NPU_STRICT");
        bool strict_mode = (strict_env && strcmp(strict_env, "0") != 0);

        if (strict_mode) {
            int strict_fail = 0;
            for (int i = 0; i < cgraph->n_nodes && i < g_npu_strict_count; i++) {
                struct ggml_tensor * node = cgraph->nodes[i];
                if (is_layout_op(node->op)) continue;
                if (!npu_device_supports_op(NULL, node)) continue;
                if (g_npu_strict_submitted[i]) continue;

                const char *reason = g_npu_strict_reason[i];
                if (!reason) reason = "not in NPU command blob";
                fprintf(stderr, "[NPU] STRICT: op %s node %d (%s) "
                        "claimed NPU-supported but fell back: %s\n",
                        ggml_op_name(node->op), i, node->name, reason);
                strict_fail++;
            }
            if (strict_fail > 0) {
                fprintf(stderr, "[NPU] STRICT: %d ops claimed NPU-supported "
                        "but fell back to CPU. Hard fail.\n", strict_fail);
                return GGML_STATUS_FAILED;
            }
        }
    }

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

        // ggml convention is variable: either source may be the float
        // activation and the other the quantized weight. Identify them
        // by dtype rather than by src index.
        const struct ggml_tensor * src0 = op->src[0];
        const struct ggml_tensor * src1 = op->src[1];
        if (!src0 || !src1) return false;

        const struct ggml_tensor * act = nullptr;
        const struct ggml_tensor * wgt = nullptr;
        if (is_float_type(src0->type) && is_quantized_type(src1->type)) {
            act = src0; wgt = src1;
        } else if (is_quantized_type(src0->type) && is_float_type(src1->type)) {
            act = src1; wgt = src0;
        } else {
            return false;
        }

        if (act->ne[0] == 0 || act->ne[1] == 0 || wgt->ne[1] == 0)
            return false;

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
