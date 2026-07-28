/*
 * CaduceusCore FPGA Transport — Public Interface
 *
 * Defines the platform inventory, transport-selection policy, and
 * public constructors for the FPGA transport.  This header is the
 * contract between the runtime core and the FPGA transport.
 *
 * Full Linux userspace FPGA transport (BAR mapping, DMA, interrupts,
 * real board) is deferred.  This phase delivers fake-fixture validation
 * that exercises every decision branch.
 */

#ifndef CADUCEUS_TRANSPORT_FPGA_H
#define CADUCEUS_TRANSPORT_FPGA_H

#include "caduceus/cad_transport.h"

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── Transport type enum ─────────────────────────────────────────── */

/*
 * Discovered FPGA backend.  The transport probes in priority order:
 *   VFIO  →  UIO  →  vendor-plugin  →  NO-GO.
 *
 * CAD_FPGA_NONE is NOT an error path — it is a structured NO-GO
 * signal that the transport and conformance suite must handle
 * explicitly.
 */
typedef enum cad_transport_fpga_type_t {
    CAD_FPGA_VFIO   = 0,  /* VFIO-based PCIe MMIO + DMA */
    CAD_FPGA_UIO    = 1,  /* UIO-based userspace MMIO */
    CAD_FPGA_VENDOR = 2,  /* Vendor-specific kernel plugin */
    CAD_FPGA_NONE   = 3,  /* No suitable FPGA backend found */
} cad_transport_fpga_type_t;

/* ── Platform inventory ──────────────────────────────────────────── */

/*
 * Hardware identity record used to discover and validate an FPGA
 * board.  All fields must match for the transport to claim a device.
 */
typedef struct cad_transport_fpga_identity_t {
    uint16_t pci_domain;       /* PCI domain (segment) */
    uint8_t  pci_bus;          /* PCI bus number */
    uint8_t  pci_device;       /* PCI device number */
    uint8_t  pci_function;     /* PCI function number */
    uint16_t vendor_id;        /* PCI vendor ID (CaduceusCore: 0xCAFE) */
    uint16_t device_id;        /* PCI device ID (NPU: 0xBEEF) */
    uint16_t subsystem_vendor; /* subsystem vendor, 0 if unspecified */
    uint16_t subsystem_device; /* subsystem device, 0 if unspecified */
} cad_transport_fpga_identity_t;

/*
 * Expected BAR layout.  The transport validates that each BAR's
 * reported PCI resource size meets or exceeds the minimum before
 * mapping.
 */
#define CAD_FPGA_MAX_BARS 6

typedef struct cad_transport_fpga_bar_spec_t {
    uint32_t index;          /* BAR index (0–5) */
    uint64_t min_size;       /* minimum acceptable size, bytes; 0 = optional */
    const char *label;       /* human-readable label, e.g. "BAR0-SRAM" */
} cad_transport_fpga_bar_spec_t;

/*
 * Platform inventory: the expected device identity plus the minimum
 * BAR requirements.  Populated from the CaduceusCore SoC address map.
 */
typedef struct cad_transport_fpga_inventory_t {
    cad_transport_fpga_identity_t identity;
    uint32_t                       bar_count;
    cad_transport_fpga_bar_spec_t  bars[CAD_FPGA_MAX_BARS];
} cad_transport_fpga_inventory_t;

/*
 * Return the default CaduceusCore platform inventory.
 *
 * PCI BDF:   0000:01:00.0  (typical FPGA slot)
 * Vendor ID: 0xCAFE
 * Device ID: 0xBEEF
 * BAR0 (SRAM): min_size 4 MB  (0x00400000)
 * BAR1 (DRAM): min_size 2 GB  (0x80000000)
 * BAR2 (MMIO): min_size 64 KB (0x00010000)
 */
const cad_transport_fpga_inventory_t *cad_transport_fpga_default_inventory(void);

/* ── Fake-fixture control (test surface, not for production) ─────── */

/*
 * Override the backend type that the transport will "discover".
 *
 *  0 → CAD_FPGA_VFIO   — VFIO fake path
 *  1 → CAD_FPGA_UIO    — UIO fake path
 *  2 → CAD_FPGA_VENDOR — vendor-plugin fake path
 *  3 → CAD_FPGA_NONE   — no-device path (NO-GO)
 *
 * Call cad_fpga_set_fake_type(CAD_FPGA_NONE) to simulate a system
 * with no compatible FPGA hardware.
 */
void cad_fpga_set_fake_type(int type);

/*
 * Override the BAR size that the fake fixture reports for a given
 * BAR index.  Set size=0 to restore default.  Used to test BAR
 * size validation.
 */
void cad_fpga_fake_set_bar_size(uint32_t bar_index, uint64_t fake_size);

/* ── Transpo   rt vtable ─────────────────────────────────────────── */

extern const cad_transport_ops_t cad_transport_fpga_ops;

/*
 * Initialize the FPGA transport.
 *
 * Accepted URI forms:
 *   fpga://                    → auto-discover (VFIO → UIO → vendor → none)
 *   fpga://vfio?bdf=01:00.0   → force VFIO path
 *   fpga://uio?uio=0           → force UIO path
 *   fpga://vendor?plugin=X     → force vendor plugin path
 *
 * Returns 0 on success, negative cad_transport error code on failure.
 * When no suitable backend is found, returns CAD_TR_ERR_UNSUP and
 * sets the discovered type to CAD_FPGA_NONE (structured NO-GO).
 */
int cad_transport_fpga_init(void **tpriv, const char *uri);

/*
 * After a successful init, query which backend was actually selected.
 * Returns CAD_FPGA_NONE if the transport is not initialized.
 */
cad_transport_fpga_type_t cad_transport_fpga_get_type(void *tpriv);

#ifdef __cplusplus
}
#endif

#endif /* CADUCEUS_TRANSPORT_FPGA_H */
