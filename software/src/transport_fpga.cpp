/*
 * CaduceusCore FPGA Transport — Fake-Fixture Implementation
 *
 * Implements the cad_transport_ops_t vtable with fake VFIO, UIO,
 * vendor-plugin, and no-device fixtures.  Each fixture exercises a
 * distinct decision branch in the transport-selection logic.
 *
 * Full Linux userspace FPGA transport (BAR mapping via /sys/bus/pci,
 * VFIO ioctl, UIO mmap, MSI-X interrupts, DMA programming) is
 * deferred.  This implementation uses in-memory fake devices so the
 * conformance contract can be proven without a real board.
 */

#include "caduceus/transport_fpga.h"

#include <stdlib.h>
#include <string.h>

/* ── Fake-fixture globals (test surface) ──────────────────────────── */

static int g_fake_type = -1; /* -1 = auto, 0=VFIO, 1=UIO, 2=VENDOR, 3=NONE */
static uint64_t g_fake_bar_override[CAD_FPGA_MAX_BARS];
static int g_fake_bar_overridden[CAD_FPGA_MAX_BARS];

void cad_fpga_set_fake_type(int type) { g_fake_type = type; }
void cad_fpga_fake_set_bar_size(uint32_t bar_index, uint64_t fake_size) {
    if (bar_index < CAD_FPGA_MAX_BARS) {
        g_fake_bar_override[bar_index] = fake_size;
        /* size=0 clears the override; size>0 sets it */
        g_fake_bar_overridden[bar_index] = (fake_size > 0) ? 1 : 0;
    }
}

/* ── Platform inventory (default CaduceusCore SoC) ───────────────── */

/*
 * Derived from the RTL SoC address map (caduceus_soc_top.v):
 *   BAR0  → SRAM window    (0x2000_0000,  4 MB)
 *   BAR1  → DRAM window    (0x8000_0000,  2 GB)
 *   BAR2  → MMIO registers (0x4000_0000, 64 KB)
 */

static const cad_transport_fpga_inventory_t k_default_inventory = {
    .identity = {
        .pci_domain        = 0x0000,
        .pci_bus           = 0x01,
        .pci_device        = 0x00,
        .pci_function      = 0,
        .vendor_id         = 0xCAFE,
        .device_id         = 0xBEEF,
        .subsystem_vendor  = 0x0000,
        .subsystem_device  = 0x0000,
    },
    .bar_count = 3,
    .bars = {
        {0, 0x00400000ULL, "BAR0-SRAM"},   /* 4 MB minimum */
        {1, 0x80000000ULL, "BAR1-DRAM"},   /* 2 GB minimum */
        {2, 0x00010000ULL, "BAR2-MMIO"},   /* 64 KB minimum */
        {3, 0,            NULL},
        {4, 0,            NULL},
        {5, 0,            NULL},
    },
};

const cad_transport_fpga_inventory_t *cad_transport_fpga_default_inventory(void) {
    return &k_default_inventory;
}

/* ── Fake BAR storage ─────────────────────────────────────────────── */

/*
 * Each fake BAR is backed by a heap allocation up to its configured
 * size (or the inventory minimum).  We cap fake allocations to avoid
 * allocating 2 GB in tests; the fake BAR sees a "reported size" equal
 * to the inventory spec but only allocates a small shadow.
 */
#define FAKE_BAR_SHADOW_SIZE (64 * 1024) /* 64 KB per BAR for test allocation */

typedef struct {
    uint64_t reported_size;   /* size as reported by fake sysfs/probe */
    uint64_t shadow_size;     /* actually allocated (capped at FAKE_BAR_SHADOW_SIZE) */
    uint8_t *data;
} fake_bar_t;

typedef struct {
    cad_transport_fpga_type_t type;
    fake_bar_t bars[CAD_FPGA_MAX_BARS];
    uint32_t bar_count;
    int initialized;
} fpga_device_t;

/* ── Fake BAR helpers ─────────────────────────────────────────────── */

static uint64_t resolve_bar_size(const cad_transport_fpga_bar_spec_t *spec) {
    if (g_fake_bar_overridden[spec->index]) {
        return g_fake_bar_override[spec->index];
    }
    return spec->min_size;
}

static int validate_bar_sizes(const cad_transport_fpga_inventory_t *inv) {
    for (uint32_t i = 0; i < inv->bar_count; i++) {
        const cad_transport_fpga_bar_spec_t *spec = &inv->bars[i];
        if (spec->min_size == 0) continue; /* optional BAR, skip */
        uint64_t reported = resolve_bar_size(spec);
        if (reported < spec->min_size) {
            return -1; /* BAR size too small */
        }
    }
    return 0;
}

static int allocate_fake_bars(fpga_device_t *dev,
                               const cad_transport_fpga_inventory_t *inv) {
    dev->bar_count = inv->bar_count;
    for (uint32_t i = 0; i < inv->bar_count; i++) {
        const cad_transport_fpga_bar_spec_t *spec = &inv->bars[i];
        if (spec->min_size == 0) continue; /* optional */
        fake_bar_t *bar = &dev->bars[i];
        bar->reported_size = resolve_bar_size(spec);
        bar->shadow_size = FAKE_BAR_SHADOW_SIZE;
        bar->data = (uint8_t *)calloc(1, (size_t)bar->shadow_size);
        if (!bar->data) return CAD_TR_ERR_NOMEM;
    }
    return CAD_TR_SUCCESS;
}

static void free_fake_bars(fpga_device_t *dev) {
    for (uint32_t i = 0; i < dev->bar_count; i++) {
        free(dev->bars[i].data);
        dev->bars[i].data = NULL;
    }
}

/* ── Fake device discovery ────────────────────────────────────────── */

/*
 * Priority-ordered discovery:
 *   VFIO  →  UIO  →  vendor-plugin  →  NO-GO
 *
 * When g_fake_type is set (≥0), the discovery returns that type
 * unconditionally.  When g_fake_type is -1 (auto), the fake sysfs
 * probe simulates a system where VFIO is available (VFIO takes
 * priority).
 */
static cad_transport_fpga_type_t fake_discover(void) {
    if (g_fake_type >= 0) {
        return (cad_transport_fpga_type_t)g_fake_type;
    }
    /* Auto: VFIO is preferred if available */
    return CAD_FPGA_VFIO;
}

/* ── URI parsing ──────────────────────────────────────────────────── */

static int fpga_parse_uri(const char *uri,
                           cad_transport_fpga_type_t *out_type) {
    if (!uri) return CAD_TR_ERR_INVAL;

    /* fpga:// → auto */
    if (strcmp(uri, "fpga://") == 0) {
        *out_type = CAD_FPGA_NONE; /* will be overridden by discover */
        return CAD_TR_SUCCESS;
    }

    /* fpga://vfio?bdf=... */
    if (strncmp(uri, "fpga://vfio", 11) == 0) {
        *out_type = CAD_FPGA_VFIO;
        return CAD_TR_SUCCESS;
    }

    /* fpga://uio?... */
    if (strncmp(uri, "fpga://uio", 10) == 0) {
        *out_type = CAD_FPGA_UIO;
        return CAD_TR_SUCCESS;
    }

    /* fpga://vendor?... */
    if (strncmp(uri, "fpga://vendor", 13) == 0) {
        *out_type = CAD_FPGA_VENDOR;
        return CAD_TR_SUCCESS;
    }

    return CAD_TR_ERR_UNSUP;
}

/* ── Transport vtable: device lifecycle ───────────────────────────── */

static int fpga_device_init(void *tpriv, const char *uri) {
    fpga_device_t *dev = (fpga_device_t *)tpriv;
    if (dev->initialized) return CAD_TR_ERR_BUSY;

    const cad_transport_fpga_inventory_t *inv =
        cad_transport_fpga_default_inventory();

    cad_transport_fpga_type_t uri_type;
    int err = fpga_parse_uri(uri, &uri_type);
    if (err != CAD_TR_SUCCESS) return err;

    if (uri_type == CAD_FPGA_NONE) {
        dev->type = fake_discover();
    } else {
        dev->type = uri_type;
    }

    /* NO-GO: no suitable backend → structured failure */
    if (dev->type == CAD_FPGA_NONE) {
        dev->initialized = 1;
        return CAD_TR_ERR_UNSUP;
    }

    /* Validate BAR sizes before any allocation */
    if (validate_bar_sizes(inv) != 0) {
        return CAD_TR_ERR_INVAL;
    }

    /* Allocate fake BAR backing store */
    err = allocate_fake_bars(dev, inv);
    if (err != CAD_TR_SUCCESS) return err;

    dev->initialized = 1;
    return CAD_TR_SUCCESS;
}

static void fpga_device_fini(void *tpriv) {
    fpga_device_t *dev = (fpga_device_t *)tpriv;
    if (!dev) return;
    free_fake_bars(dev);
    dev->initialized = 0;
}

static int fpga_device_reset(void *tpriv) {
    fpga_device_t *dev = (fpga_device_t *)tpriv;
    (void)dev;
    /* Fake: reset just zeroes BAR[N] shadow data */
    for (uint32_t i = 0; i < dev->bar_count; i++) {
        if (dev->bars[i].data) {
            memset(dev->bars[i].data, 0, (size_t)dev->bars[i].shadow_size);
        }
    }
    return CAD_TR_SUCCESS;
}

/* ── Transport vtable: buffer management ──────────────────────────── */

typedef struct {
    uint64_t bar_addr;    /* BAR-local address */
    uint64_t size;        /* allocated size */
    uint8_t  bar_index;   /* which BAR this buffer lives in */
} fpga_buffer_t;

static int fpga_buffer_alloc(void *tpriv, cad_transport_buffer_t **out,
                              uint64_t size) {
    fpga_device_t *dev = (fpga_device_t *)tpriv;
    fpga_buffer_t *fb = (fpga_buffer_t *)calloc(1, sizeof(*fb));
    if (!fb) return CAD_TR_ERR_NOMEM;

    /* Allocate in BAR0 (SRAM) by default; fake: just track the handle */
    fb->bar_index = 0;
    fb->bar_addr  = 0;
    fb->size      = size;

    /* Validate the buffer fits within BAR0 */
    if (size > dev->bars[0].reported_size) {
        free(fb);
        return CAD_TR_ERR_NOMEM;
    }

    *out = (cad_transport_buffer_t *)fb;
    return CAD_TR_SUCCESS;
}

static void fpga_buffer_free(void *tpriv, cad_transport_buffer_t *bf) {
    (void)tpriv;
    free(bf);
}

static int fpga_buffer_read(void *tpriv, cad_transport_buffer_t *bf,
                             uint64_t offset, uint64_t size, void *dst) {
    fpga_device_t *dev = (fpga_device_t *)tpriv;
    fpga_buffer_t *fb = (fpga_buffer_t *)bf;
    if (!fb || !dst) return CAD_TR_ERR_INVAL;
    if (offset + size > fb->size) return CAD_TR_ERR_INVAL;

    fake_bar_t *bar = &dev->bars[fb->bar_index];
    if (!bar->data) return CAD_TR_ERR_LOST;

    /*
     * Fake: the "shadow" region covers the first FAKE_BAR_SHADOW_SIZE
     * bytes of the BAR.  Reads beyond the shadow return zero
     * (as-if reading unmapped MMIO that hasn't been written).
     */
    uint64_t bar_off = fb->bar_addr + offset;
    if (bar_off + size > bar->reported_size) return CAD_TR_ERR_INVAL;

    uint64_t shadow_end = bar_off + size;
    if (shadow_end <= bar->shadow_size) {
        /* Fully within shadow: copy from backing store */
        memcpy(dst, bar->data + bar_off, (size_t)size);
    } else if (bar_off >= bar->shadow_size) {
        /* Fully beyond shadow: return zeroes */
        memset(dst, 0, (size_t)size);
    } else {
        /* Split: partial shadow, partial zero */
        uint64_t in_shadow = bar->shadow_size - bar_off;
        memcpy(dst, bar->data + bar_off, (size_t)in_shadow);
        memset((uint8_t *)dst + in_shadow, 0, (size_t)(size - in_shadow));
    }
    return CAD_TR_SUCCESS;
}

static int fpga_buffer_write(void *tpriv, cad_transport_buffer_t *bf,
                              uint64_t offset, uint64_t size,
                              const void *src) {
    fpga_device_t *dev = (fpga_device_t *)tpriv;
    fpga_buffer_t *fb = (fpga_buffer_t *)bf;
    if (!fb || !src) return CAD_TR_ERR_INVAL;
    if (offset + size > fb->size) return CAD_TR_ERR_INVAL;

    fake_bar_t *bar = &dev->bars[fb->bar_index];
    if (!bar->data) return CAD_TR_ERR_LOST;

    uint64_t bar_off = fb->bar_addr + offset;
    if (bar_off + size > bar->reported_size) return CAD_TR_ERR_INVAL;

    /* Only write into the shadow region */
    if (bar_off < bar->shadow_size) {
        uint64_t writable = bar->shadow_size - bar_off;
        if (size < writable) writable = size;
        memcpy(bar->data + bar_off, src, (size_t)writable);
    }
    return CAD_TR_SUCCESS;
}

static uint64_t fpga_buffer_size(void *tpriv, cad_transport_buffer_t *bf) {
    (void)tpriv;
    fpga_buffer_t *fb = (fpga_buffer_t *)bf;
    return fb ? fb->size : 0;
}

/* ── Transport vtable: fences ─────────────────────────────────────── */

typedef struct {
    int  submitted;   /* 0=not yet submitted, 1=in-flight, 2=completed */
    int  error;       /* 0=completed, 1=error */
    int  mode;        /* 0=poll, 1=interrupt */
} fpga_fence_t;

static int fpga_fence_create(void *tpriv, cad_transport_fence_t **out) {
    (void)tpriv;
    fpga_fence_t *f = (fpga_fence_t *)calloc(1, sizeof(*f));
    if (!f) return CAD_TR_ERR_NOMEM;
    *out = (cad_transport_fence_t *)f;
    return CAD_TR_SUCCESS;
}

static void fpga_fence_destroy(void *tpriv, cad_transport_fence_t *f) {
    (void)tpriv;
    free(f);
}

static int fpga_fence_wait(void *tpriv, cad_transport_fence_t *f,
                            uint64_t timeout_ns) {
    (void)tpriv;
    fpga_fence_t *ff = (fpga_fence_t *)f;
    if (!ff) return CAD_TR_ERR_INVAL;

    if (ff->submitted == 2) {
        return ff->error ? CAD_TR_ERR_LOST : CAD_TR_SUCCESS;
    }

    /* Fake: interrupt path signals immediately on wait */
    if (ff->submitted == 1) {
        /* poll mode: CAD_TIMEOUT_IMMEDIATE on unsignalled → not ready */
        if (timeout_ns == 0 && ff->mode == 0) {
            return CAD_TR_ERR_NOTREADY;
        }
        /* Interrupt mode or non-zero timeout: signal */
        ff->submitted = 2;
        return ff->error ? CAD_TR_ERR_LOST : CAD_TR_SUCCESS;
    }

    return CAD_TR_ERR_NOTREADY;
}

static int fpga_fence_poll(void *tpriv, cad_transport_fence_t *f) {
    (void)tpriv;
    fpga_fence_t *ff = (fpga_fence_t *)f;
    if (!ff) return CAD_TR_ERR_INVAL;

    if (ff->submitted == 2) {
        return ff->error ? CAD_TR_ERR_LOST : CAD_TR_SUCCESS;
    }

    /* Poll mode: only signalled if completed */
    return CAD_TR_ERR_NOTREADY;
}

static int fpga_fence_status(void *tpriv, cad_transport_fence_t *f) {
    (void)tpriv;
    fpga_fence_t *ff = (fpga_fence_t *)f;
    if (!ff) return 2;
    if (ff->submitted == 2) return ff->error ? 2 : 1;
    return 0; /* NOT_READY */
}

/* ── Transport vtable: submit ─────────────────────────────────────── */

static int fpga_submit(void *tpriv, void *cmd_data, uint32_t cmd_count,
                        cad_transport_fence_t *fence) {
    (void)cmd_data;
    fpga_device_t *dev = (fpga_device_t *)tpriv;
    if (dev->type == CAD_FPGA_NONE) return CAD_TR_ERR_UNSUP;

    if (fence) {
        fpga_fence_t *ff = (fpga_fence_t *)fence;
        ff->submitted = 1;
        /* VFIO/VENDOR → interrupt path; UIO → poll path */
        ff->mode = (dev->type == CAD_FPGA_UIO) ? 0 : 1;
        ff->error = 0;
    }

    (void)cmd_count;
    return CAD_TR_SUCCESS;
}

/* ── Query selected backend ───────────────────────────────────────── */

cad_transport_fpga_type_t cad_transport_fpga_get_type(void *tpriv) {
    if (!tpriv) return CAD_FPGA_NONE;
    fpga_device_t *dev = (fpga_device_t *)tpriv;
    return dev->type;
}

/* ── Vtable export (C linkage for runtime core) ──────────────────── */

extern "C" {

const cad_transport_ops_t cad_transport_fpga_ops = {
    .name          = "FPGA",
    .device_init   = fpga_device_init,
    .device_fini   = fpga_device_fini,
    .device_reset  = fpga_device_reset,
    .buffer_alloc  = fpga_buffer_alloc,
    .buffer_free   = fpga_buffer_free,
    .buffer_read   = fpga_buffer_read,
    .buffer_write  = fpga_buffer_write,
    .buffer_size   = fpga_buffer_size,
    .fence_create  = fpga_fence_create,
    .fence_destroy = fpga_fence_destroy,
    .fence_wait    = fpga_fence_wait,
    .fence_poll    = fpga_fence_poll,
    .fence_status  = fpga_fence_status,
    .submit        = fpga_submit,
};

int cad_transport_fpga_init(void **tpriv, const char *uri) {
    fpga_device_t *dev = (fpga_device_t *)calloc(1, sizeof(*dev));
    if (!dev) return CAD_TR_ERR_NOMEM;
    int ret = fpga_device_init(dev, uri);
    if (ret != CAD_TR_SUCCESS) {
        /* NO-GO is a structured signal: allocate the device so the
         * caller can inspect the discovered type, but mark as failed. */
        if (ret == CAD_TR_ERR_UNSUP) {
            dev->initialized = 1;
            dev->type = CAD_FPGA_NONE;
            *tpriv = dev;
            return ret;
        }
        free(dev);
        return ret;
    }
    *tpriv = dev;
    return CAD_TR_SUCCESS;
}

} /* extern "C" */
