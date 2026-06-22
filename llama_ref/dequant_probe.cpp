// Minimal C++ probe: read raw Q4_K/Q6_K block bytes from stdin and dequantize
// using logic copied from llama.cpp ggml-quants.c (commit 59778f0).
//
// Usage: ./dequant_probe <type> <n_bytes>
//   type: q4_k or q6_k
//   n_bytes: number of raw bytes to read
// Prints float32 values one per line.

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#define QK_K 256
#define K_SCALE_SIZE 12

struct block_q4_K {
    uint16_t d;
    uint16_t dmin;
    uint8_t scales[K_SCALE_SIZE];
    uint8_t qs[QK_K / 2];
};

struct block_q6_K {
    uint8_t ql[QK_K / 2];
    uint8_t qh[QK_K / 4];
    int8_t  scales[QK_K / 16];
    uint16_t d;
};

static inline uint16_t from_le16(const uint8_t *p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static inline float fp16_to_fp32(uint16_t h) {
    uint32_t sign = (h >> 15) & 1;
    int32_t  exp  = (h >> 10) & 0x1F;
    uint32_t mant = h & 0x3FF;
    float s = sign ? -1.0f : 1.0f;
    if (exp == 0) {
        if (mant == 0) return 0.0f;
        return s * (1.0f / 16384.0f) * (mant / 1024.0f);
    }
    if (exp == 31) {
        if (mant == 0) return s * INFINITY;
        return NAN;
    }
    union { uint32_t u; float f; } conv;
    conv.u = (sign << 31) | ((uint32_t)(exp - 15 + 127) << 23) | (mant << 13);
    return conv.f;
}

static inline void get_scale_min_k4(int j, const uint8_t *q, uint8_t *d, uint8_t *m) {
    if (j < 4) {
        *d = q[j] & 63;
        *m = q[j + 4] & 63;
    } else {
        *d = (q[j + 4] & 0xF) | ((q[j - 4] >> 6) << 4);
        *m = (q[j + 4] >>  4) | ((q[j - 0] >> 6) << 4);
    }
}

static void dequantize_row_q4_K(const block_q4_K *x, float *y, int64_t k) {
    const int nb = k / QK_K;
    for (int i = 0; i < nb; i++) {
        const uint8_t *q = x[i].qs;
        const float d   = fp16_to_fp32(x[i].d);
        const float min = fp16_to_fp32(x[i].dmin);
        int is = 0;
        uint8_t sc, m;
        for (int j = 0; j < QK_K; j += 64) {
            get_scale_min_k4(is + 0, x[i].scales, &sc, &m);
            const float d1 = d * sc; const float m1 = min * m;
            get_scale_min_k4(is + 1, x[i].scales, &sc, &m);
            const float d2 = d * sc; const float m2 = min * m;
            for (int l = 0; l < 32; ++l) *y++ = d1 * (q[l] & 0xF) - m1;
            for (int l = 0; l < 32; ++l) *y++ = d2 * (q[l] >> 4) - m2;
            q += 32; is += 2;
        }
    }
}

static void dequantize_row_q6_K(const block_q6_K *x, float *y, int64_t k) {
    const int nb = k / QK_K;
    for (int i = 0; i < nb; i++) {
        const float d = fp16_to_fp32(x[i].d);
        const uint8_t *ql = x[i].ql;
        const uint8_t *qh = x[i].qh;
        const int8_t  *sc = x[i].scales;
        for (int n = 0; n < QK_K; n += 128) {
            for (int l = 0; l < 32; ++l) {
                int is = l / 16;
                const int8_t q1 = (int8_t)((ql[l +  0] & 0xF) | (((qh[l] >> 0) & 3) << 4)) - 32;
                const int8_t q2 = (int8_t)((ql[l + 32] & 0xF) | (((qh[l] >> 2) & 3) << 4)) - 32;
                const int8_t q3 = (int8_t)((ql[l +  0]  >> 4) | (((qh[l] >> 4) & 3) << 4)) - 32;
                const int8_t q4 = (int8_t)((ql[l + 32]  >> 4) | (((qh[l] >> 6) & 3) << 4)) - 32;
                y[l +  0] = d * sc[is + 0] * q1;
                y[l + 32] = d * sc[is + 2] * q2;
                y[l + 64] = d * sc[is + 4] * q3;
                y[l + 96] = d * sc[is + 6] * q4;
            }
            y  += 128;
            ql += 64;
            qh += 32;
            sc += 8;
        }
    }
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "Usage: %s <q4_k|q6_k> <n_bytes>\n", argv[0]);
        return 1;
    }
    const char *type = argv[1];
    size_t n_bytes = (size_t)strtoull(argv[2], nullptr, 10);

    std::vector<uint8_t> raw(n_bytes);
    size_t read = fread(raw.data(), 1, n_bytes, stdin);
    if (read != n_bytes) {
        fprintf(stderr, "Expected %zu bytes, got %zu\n", n_bytes, read);
        return 1;
    }

    if (strcmp(type, "q4_k") == 0) {
        if (n_bytes % sizeof(block_q4_K) != 0) {
            fprintf(stderr, "Q4_K bytes not aligned\n");
            return 1;
        }
        int64_t k = (n_bytes / sizeof(block_q4_K)) * QK_K;
        std::vector<float> y(k);
        dequantize_row_q4_K((const block_q4_K *)raw.data(), y.data(), k);
        for (float v : y) printf("%.9g\n", v);
    } else if (strcmp(type, "q6_k") == 0) {
        if (n_bytes % sizeof(block_q6_K) != 0) {
            fprintf(stderr, "Q6_K bytes not aligned\n");
            return 1;
        }
        int64_t k = (n_bytes / sizeof(block_q6_K)) * QK_K;
        std::vector<float> y(k);
        dequantize_row_q6_K((const block_q6_K *)raw.data(), y.data(), k);
        for (float v : y) printf("%.9g\n", v);
    } else {
        fprintf(stderr, "Unknown type %s\n", type);
        return 1;
    }
    return 0;
}
