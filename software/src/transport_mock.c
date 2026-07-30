/*
 * CaduceusCore Deterministic Mock Transport
 *
 * Implements the cad_transport_ops_t vtable with deterministic,
 * in-process semantics suitable for testing.  The mock transport:
 *   - Stores all buffers as heap-allocated byte arrays (real storage)
 *   - Records every submitted command in an operation log
 *   - Supports configurable "pending ticks" before fence completion
 *   - Supports fault injection: configurable error-on-next-submit
 *   - Fences start unsignalled; become signalled after N "ticks"
 *
 * The mock is NOT a simulator — it does not execute commands.
 * It serves as a deterministic test backend for the runtime core.
 */

#include "caduceus/cad_transport.h"
#include "runtime_core.h"
#include <stdlib.h>
#include <string.h>

/* ── Mock-configurable parameters (global for simplicity) ───────── */
static int g_mock_pending_ticks = 0;  /* ticks before fence signals */
static int g_mock_next_submit_error = 0; /* 0=ok, else cad_error_t */
static int g_mock_tick_counter = 0;

/* Last-submit payload capture (for test verification) */
static void *g_mock_last_cmd_data = NULL;
static uint32_t g_mock_last_cmd_size = 0;

/* Public API to configure mock behavior (called by tests) */
void cad_mock_set_pending_ticks(int n) { g_mock_pending_ticks = n; }
void cad_mock_set_next_submit_error(int e) { g_mock_next_submit_error = e; }
void cad_mock_advance_ticks(int n) { g_mock_tick_counter += n; }
void cad_mock_reset(void) {
    g_mock_pending_ticks = 0;
    g_mock_next_submit_error = 0;
    g_mock_tick_counter = 0;
    free(g_mock_last_cmd_data);
    g_mock_last_cmd_data = NULL;
    g_mock_last_cmd_size = 0;
}
int cad_mock_get_tick(void) { return g_mock_tick_counter; }

/* ── Mock transport fence ───────────────────────────────────────── */
typedef struct {
    int tick_submitted;   /* tick counter at submission (-1 = not submitted) */
    int tick_total;       /* ticks needed before signal */
    int signalled;
    int error;            /* 0=completed, 1=error */
} mock_fence_t;

/* ── Mock transport buffer ──────────────────────────────────────── */
typedef struct {
    uint8_t *data;
    uint64_t size;
} mock_buffer_t;

/* ── Mock transport op log (operation recording) ────────────────── */
#define MOCK_OP_LOG_MAX 1024

typedef enum {
    MOCK_OP_DEVICE_OPEN,
    MOCK_OP_BUFFER_ALLOC,
    MOCK_OP_BUFFER_FREE,
    MOCK_OP_BUFFER_READ,
    MOCK_OP_BUFFER_WRITE,
    MOCK_OP_SUBMIT,
    MOCK_OP_FENCE_CREATE,
    MOCK_OP_FENCE_SIGNAL,
    MOCK_OP_DEVICE_RESET,
} mock_op_type_t;

typedef struct {
    mock_op_type_t type;
    uint64_t param0;
    uint64_t param1;
} mock_op_log_entry_t;

/* ── Mock device state ──────────────────────────────────────────── */
typedef struct {
    mock_op_log_entry_t op_log[MOCK_OP_LOG_MAX];
    uint32_t op_log_count;
    int      initialized;
} mock_device_t;

/* ── Op log helpers ─────────────────────────────────────────────── */
static void log_op(mock_device_t *md, mock_op_type_t type,
                    uint64_t p0, uint64_t p1) {
    if (md->op_log_count < MOCK_OP_LOG_MAX) {
        md->op_log[md->op_log_count].type = type;
        md->op_log[md->op_log_count].param0 = p0;
        md->op_log[md->op_log_count].param1 = p1;
        md->op_log_count++;
    }
}

/* Query the op log (for test verification). Accepts cad_device_t
 * so callers don't need internal structure access. */
const mock_op_log_entry_t *cad_mock_get_op_log(cad_device_t device,
                                                uint32_t *count) {
    if (!validate_device(device)) { if (count) *count = 0; return NULL; }
    mock_device_t *md = (mock_device_t *)device->transport_priv;
    if (count) *count = md->op_log_count;
    return md->op_log;
}
void cad_mock_clear_op_log(cad_device_t device) {
    if (!validate_device(device)) return;
    mock_device_t *md = (mock_device_t *)device->transport_priv;
    md->op_log_count = 0;
}

/* ── Transport vtable implementations ────────────────────────────── */

static int mock_device_init(void *tpriv, const char *uri) {
    (void)uri;
    mock_device_t *md = (mock_device_t *)tpriv;
    if (md->initialized) return CAD_TR_ERR_BUSY;
    md->initialized = 1;
    md->op_log_count = 0;
    log_op(md, MOCK_OP_DEVICE_OPEN, 0, 0);
    return CAD_TR_SUCCESS;
}

static void mock_device_fini(void *tpriv) {
    mock_device_t *md = (mock_device_t *)tpriv;
    md->initialized = 0;
    free(tpriv);
}

static int mock_device_reset(void *tpriv) {
    mock_device_t *md = (mock_device_t *)tpriv;
    log_op(md, MOCK_OP_DEVICE_RESET, 0, 0);
    return CAD_TR_SUCCESS;
}

static int mock_buffer_alloc(void *tpriv, cad_transport_buffer_t **out,
                              uint64_t size) {
    mock_device_t *md = (mock_device_t *)tpriv;
    mock_buffer_t *mb = calloc(1, sizeof(*mb));
    if (!mb) return CAD_TR_ERR_NOMEM;
    mb->data = calloc(1, (size_t)size);
    if (!mb->data) { free(mb); return CAD_TR_ERR_NOMEM; }
    mb->size = size;
    log_op(md, MOCK_OP_BUFFER_ALLOC, size, 0);
    *out = (cad_transport_buffer_t *)mb;
    return CAD_TR_SUCCESS;
}

static void mock_buffer_free(void *tpriv, cad_transport_buffer_t *bf) {
    mock_device_t *md = (mock_device_t *)tpriv;
    mock_buffer_t *mb = (mock_buffer_t *)bf;
    log_op(md, MOCK_OP_BUFFER_FREE, mb->size, 0);
    free(mb->data);
    free(mb);
}

static int mock_buffer_read(void *tpriv, cad_transport_buffer_t *bf,
                             uint64_t offset, uint64_t size, void *dst) {
    (void)tpriv;
    mock_buffer_t *mb = (mock_buffer_t *)bf;
    if (offset + size > mb->size) return CAD_TR_ERR_INVAL;
    memcpy(dst, mb->data + offset, (size_t)size);
    return CAD_TR_SUCCESS;
}

static int mock_buffer_write(void *tpriv, cad_transport_buffer_t *bf,
                              uint64_t offset, uint64_t size,
                              const void *src) {
    mock_device_t *md = (mock_device_t *)tpriv;
    mock_buffer_t *mb = (mock_buffer_t *)bf;
    if (offset + size > mb->size) return CAD_TR_ERR_INVAL;
    memcpy(mb->data + offset, src, (size_t)size);
    log_op(md, MOCK_OP_BUFFER_WRITE, offset, size);
    return CAD_TR_SUCCESS;
}

static uint64_t mock_buffer_size(void *tpriv, cad_transport_buffer_t *bf) {
    (void)tpriv;
    return ((mock_buffer_t *)bf)->size;
}

static int mock_fence_create(void *tpriv, cad_transport_fence_t **out) {
    mock_device_t *md = (mock_device_t *)tpriv;
    mock_fence_t *f = calloc(1, sizeof(*f));
    if (!f) return CAD_TR_ERR_NOMEM;
    f->tick_submitted = -1; /* not yet submitted */
    log_op(md, MOCK_OP_FENCE_CREATE, 0, 0);
    *out = (cad_transport_fence_t *)f;
    return CAD_TR_SUCCESS;
}

static void mock_fence_destroy(void *tpriv, cad_transport_fence_t *f) {
    (void)tpriv;
    free(f);
}

static int mock_fence_check(mock_fence_t *f, int check_error) {
    if (f->tick_submitted < 0) return CAD_TR_ERR_NOTREADY;
    int elapsed = g_mock_tick_counter - f->tick_submitted;
    if (elapsed >= f->tick_total) {
        if (f->error) return check_error ? CAD_TR_ERR_LOST : CAD_TR_SUCCESS;
        return CAD_TR_SUCCESS;
    }
    return CAD_TR_ERR_NOTREADY;
}

static int mock_fence_wait(void *tpriv, cad_transport_fence_t *f,
                            uint64_t timeout_ns) {
    (void)tpriv;
    mock_fence_t *mf = (mock_fence_t *)f;
    if (mf->signalled) return CAD_TR_SUCCESS;

    if (timeout_ns == 0) { /* CAD_TIMEOUT_IMMEDIATE */
        int r = mock_fence_check(mf, 0);
        if (r == CAD_TR_SUCCESS) {
            mf->signalled = 1;
            return CAD_TR_SUCCESS;
        }
        return CAD_TR_ERR_NOTREADY;
    }

    /* For non-zero timeout (including infinite), advance ticks */
    /* Deterministic: advance by pending_ticks so fence resolves */
    if (timeout_ns == ((uint64_t)(-1))) { /* CAD_TIMEOUT_INFINITE */
        g_mock_tick_counter = mf->tick_submitted + mf->tick_total;
    } else {
        int ticks_needed = mf->tick_submitted + mf->tick_total - g_mock_tick_counter;
        if (ticks_needed > 0) g_mock_tick_counter += ticks_needed;
    }

    int r = mock_fence_check(mf, 0);
    if (r == CAD_TR_SUCCESS) {
        mf->signalled = 1;
        return CAD_TR_SUCCESS;
    }
    return CAD_TR_ERR_TIMEDOUT;
}

static int mock_fence_poll(void *tpriv, cad_transport_fence_t *f) {
    (void)tpriv;
    mock_fence_t *mf = (mock_fence_t *)f;
    if (mf->signalled) return CAD_TR_SUCCESS;
    int r = mock_fence_check(mf, 0);
    if (r == CAD_TR_SUCCESS) {
        mf->signalled = 1;
        return CAD_TR_SUCCESS;
    }
    return CAD_TR_ERR_NOTREADY;
}

static int mock_fence_status(void *tpriv, cad_transport_fence_t *f) {
    (void)tpriv;
    mock_fence_t *mf = (mock_fence_t *)f;
    if (mf->signalled) return mf->error ? 2 : 1;
    int r = mock_fence_check(mf, 1);
    if (r == CAD_TR_SUCCESS) {
        mf->signalled = 1;
        return 1;
    }
    if (r == CAD_TR_ERR_LOST) {
        mf->signalled = 1;
        return 2;
    }
    return 0;
}

static int mock_submit(void *tpriv, void *cmd_data, uint32_t cmd_count,
                        cad_transport_fence_t *fence) {
    mock_device_t *md = (mock_device_t *)tpriv;

    /* Capture serialized payload for test verification */
    free(g_mock_last_cmd_data);
    g_mock_last_cmd_data = NULL;
    g_mock_last_cmd_size = 0;
    if (cmd_data && cmd_count > 0) {
        g_mock_last_cmd_data = malloc(cmd_count);
        if (g_mock_last_cmd_data) {
            memcpy(g_mock_last_cmd_data, cmd_data, cmd_count);
            g_mock_last_cmd_size = cmd_count;
        }
    }

    /* Check fault injection */
    if (g_mock_next_submit_error != 0) {
        int err = g_mock_next_submit_error;
        g_mock_next_submit_error = 0;
        return err;
    }

    log_op(md, MOCK_OP_SUBMIT, cmd_count, (uint64_t)(uintptr_t)fence);

    /* Configure fence with tick-based completion */
    if (fence) {
        mock_fence_t *mf = (mock_fence_t *)fence;
        mf->tick_submitted = g_mock_tick_counter;
        mf->tick_total = g_mock_pending_ticks;
        mf->signalled = (g_mock_pending_ticks == 0);
    }

    return CAD_TR_SUCCESS;
}

/* Retrieve the last captured submit payload (test-only) */
const void *cad_mock_get_last_submit_payload(uint32_t *size) {
    if (size) *size = g_mock_last_cmd_size;
    return g_mock_last_cmd_data;
}

/* ── Transport constructor ──────────────────────────────────────── */

int cad_transport_mock_init(void **tpriv, const char *uri) {
    (void)uri;
    mock_device_t *md = calloc(1, sizeof(*md));
    if (!md) return CAD_TR_ERR_NOMEM;
    *tpriv = md;
    return mock_device_init(md, uri);
}

const cad_transport_ops_t cad_transport_mock_ops = {
    .name          = "Mock",
    .device_init   = mock_device_init,
    .device_fini   = mock_device_fini,
    .device_reset  = mock_device_reset,
    .buffer_alloc  = mock_buffer_alloc,
    .buffer_free   = mock_buffer_free,
    .buffer_read   = mock_buffer_read,
    .buffer_write  = mock_buffer_write,
    .buffer_size   = mock_buffer_size,
    .fence_create  = mock_fence_create,
    .fence_destroy = mock_fence_destroy,
    .fence_wait    = mock_fence_wait,
    .fence_poll    = mock_fence_poll,
    .fence_status  = mock_fence_status,
    .submit        = mock_submit,
    .fence_get_exec_stats = NULL,  /* mock has no execution stats */
};
