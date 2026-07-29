#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-alloc.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cassert>
#include <cstdint>

#ifdef _WIN32
#include <io.h>
#define dup2 _dup2
#define fileno _fileno
#else
#include <unistd.h>
#endif

#define MMUL_K 128
#define MMUL_N 64
#define MMUL_M 1

static int test_npu_mmul(const char * stderr_capture_path)
{
    /* ── 1. Build ggml graph: F32 activation [K,M] x Q4_0 weight [K,N] ── */
    size_t ctx_size = 16 * 1024 * 1024;
    void * ctx_buf = malloc(ctx_size);
    assert(ctx_buf);

    struct ggml_init_params params = {};
    params.mem_size   = ctx_size;
    params.mem_buffer = ctx_buf;
    params.no_alloc   = true;  /* let backend allocator handle memory */

    struct ggml_context * ctx = ggml_init(params);
    if (!ctx) { fprintf(stdout, "FAIL: ggml_init\n"); free(ctx_buf); return 1; }

    struct ggml_tensor * act = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, MMUL_K, MMUL_M);
    struct ggml_tensor * wgt = ggml_new_tensor_2d(ctx, GGML_TYPE_Q4_0, MMUL_K, MMUL_N);
    ggml_set_name(act, "act");
    ggml_set_name(wgt, "wgt");

    struct ggml_tensor * out = ggml_mul_mat(ctx, wgt, act);
    ggml_set_name(out, "out");

    struct ggml_cgraph * gf = ggml_new_graph(ctx);
    if (!gf) { fprintf(stdout, "FAIL: ggml_new_graph\n"); ggml_free(ctx); free(ctx_buf); return 1; }
    ggml_build_forward_expand(gf, out);

    /* ── 2. Initialize NPU backend ── */
    ggml_backend_load_all();
    ggml_backend_t npu_backend = ggml_backend_init_by_name("NPU", NULL);
    if (!npu_backend) {
        fprintf(stdout, "FAIL: NPU backend init (device server running?)\n");
        ggml_free(ctx);
        free(ctx_buf);
        return 1;
    }

    /* Allocate backend buffer for tensors */
    ggml_backend_buffer_t buf = ggml_backend_alloc_ctx_tensors(ctx, npu_backend);
    if (!buf) {
        fprintf(stdout, "FAIL: ggml_backend_alloc_ctx_tensors\n");
        ggml_backend_free(npu_backend);
        ggml_free(ctx);
        free(ctx_buf);
        return 1;
    }

    /* Fill tensor data now that they are allocated on backend */
    {
        float * d = (float *)act->data;
        for (int i = 0; i < MMUL_K * MMUL_M; i++) d[i] = (float)((i % 5) - 2);
    }
    /* Fill weight with Q4_0 data (scale=1.0, values alternating 1/2/3) */
    {
        uint8_t * d = (uint8_t *)wgt->data;
        int64_t n_el = MMUL_K * MMUL_N;
        // Q4_0: blocks of 32 elements, each block = 2B scale + 16B data
        int64_t n_blocks = (n_el + 31) / 32;
        for (int64_t b = 0; b < n_blocks; b++) {
            // scale = 1.0 in FP16 = 0x3C00
            d[b * 18 + 0] = 0x00;
            d[b * 18 + 1] = 0x3C;
            for (int j = 0; j < 16 && ((int64_t)b * 32 + j * 2) < n_el; j++) {
                // packed 4-bit values: 0x21 = {lo=1, hi=2}
                d[b * 18 + 2 + j] = 0x21;
            }
        }
    }

    /* ── 3. Capture stderr during computation ── */
    fflush(stderr);
    int saved_stderr = dup(fileno(stderr));
    if (saved_stderr < 0) {
        fprintf(stdout, "FAIL: dup(stderr)\n");
        ggml_backend_buffer_free(buf);
        ggml_backend_free(npu_backend);
        ggml_free(ctx);
        free(ctx_buf);
        return 1;
    }
    FILE * capture = fopen(stderr_capture_path, "w");
    if (!capture) {
        fprintf(stdout, "FAIL: fopen(%s)\n", stderr_capture_path);
        close(saved_stderr);
        ggml_backend_buffer_free(buf);
        ggml_backend_free(npu_backend);
        ggml_free(ctx);
        free(ctx_buf);
        return 1;
    }
    dup2(fileno(capture), fileno(stderr));
    fclose(capture);

    /* ── 4. Compute ── */
    enum ggml_status status = ggml_backend_graph_compute(npu_backend, gf);
    ggml_backend_synchronize(npu_backend);

    /* Restore stderr */
    fflush(stderr);
    dup2(saved_stderr, fileno(stderr));
    close(saved_stderr);

    /* ── 5. Cleanup ── */
    ggml_backend_buffer_free(buf);
    ggml_backend_free(npu_backend);
    ggml_free(ctx);
    free(ctx_buf);

    if (status != GGML_STATUS_SUCCESS) {
        fprintf(stdout, "FAIL: graph compute returned %d\n", (int)status);
        return 1;
    }

    /* ── 6. Verify expected log lines ── */
    FILE * log = fopen(stderr_capture_path, "r");
    if (!log) { fprintf(stdout, "FAIL: cannot open %s\n", stderr_capture_path); return 1; }

    char line[4096];
    int found_submitted = 0, found_completed = 0, found_exec_stats_mmul = 0;
    int found_graph_e2e_passed = 0;

    while (fgets(line, sizeof(line), log)) {
        if (strstr(line, "[NPU] Submitted full graph blob"))
            found_submitted = 1;
        if (strstr(line, "[NPU] Fence status: COMPLETED"))
            found_completed = 1;
        if (strstr(line, "[NPU] Execution stats: mmul=1"))
            found_exec_stats_mmul = 1;
        if (strstr(line, "[NPU] Full graph end-to-end validation PASSED"))
            found_graph_e2e_passed = 1;
    }
    fclose(log);

    int failures = 0;
    #define CHECK(flag, name) do { \
        if (!(flag)) { fprintf(stdout, "FAIL: missing log line: %s\n", name); failures++; } \
        else { fprintf(stdout, "PASS: %s\n", name); } \
    } while(0)

    CHECK(found_submitted,        "[NPU] Submitted full graph blob");
    CHECK(found_completed,        "[NPU] Fence status: COMPLETED");
    CHECK(found_exec_stats_mmul,  "[NPU] Execution stats: mmul=1");
    CHECK(found_graph_e2e_passed, "[NPU] Full graph end-to-end validation PASSED");

    if (failures > 0) {
        fprintf(stdout, "\nFull captured stderr:\n");
        log = fopen(stderr_capture_path, "r");
        if (log) { while (fgets(line, sizeof(line), log)) fputs(line, stdout); fclose(log); }
        return 1;
    }

    fprintf(stdout, "\nAll %d NPU execution log checks PASSED\n", 4);
    return 0;
}

int main() {
    const char * log_path = "/tmp/test_npu_single_mmul_stderr.log";
    int rc = test_npu_mmul(log_path);
    if (rc == 0) remove(log_path);
    return rc;
}
