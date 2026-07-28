/*
 * CaduceusCore RTL Transport — Public Interface
 *
 * FEASIBILITY-ONLY phase: defines the transport interface, skeleton
 * implementation, and fake-fixture validation surface for SoC RTL.
 * Full RTL conformance/replay is deferred.
 *
 * The RTL transport implements the same binary device protocol
 * (FlatBuffers DeviceMessage over Unix socket) as the Func Model
 * transport.  rtl://mock connects to a Python mock endpoint for
 * contract validation; rtl:// checks EDA prerequisites and returns
 * a structured NO-GO when VCS or simv_soc_top are absent.
 */

#ifndef CADUCEUS_TRANSPORT_RTL_H
#define CADUCEUS_TRANSPORT_RTL_H

#include "caduceus/cad_transport.h"

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ── Default Unix socket path for rtl://mock endpoint ─────────────── */

#define CAD_TRANSPORT_RTL_DEFAULT_SOCK_PATH "/tmp/caduceus_rtl_mock.sock"

/* ── Transport vtable ─────────────────────────────────────────────── */

extern const cad_transport_ops_t cad_transport_rtl_ops;

/*
 * Initialize the RTL transport.
 *
 * Accepted URI forms:
 *   rtl://                   → check EDA prereqs (VCS + simv_soc_top)
 *                               → NO-GO if either is missing
 *   rtl://mock               → connect to mock Unix socket endpoint
 *   rtl://mock?sock=path     → connect to explicit socket path
 *
 * Returns 0 on success, negative CAD_TR_ERR_* on failure.
 * When EDA prerequisites are absent, returns CAD_TR_ERR_UNSUP
 * with a diagnostic message that distinguishes VCS-missing from
 * simv_soc_top-missing.
 */
int cad_transport_rtl_init(void **tpriv, const char *uri);

/* ── Fake-fixture control (test surface, not for production) ──────── */

/*
 * Enable or disable the fake-fixture mode.
 *
 * When enabled (non-zero), rtl://mock connects to a mock socket
 * endpoint regardless of EDA state.  When disabled (0), standard
 * preflight checks apply.
 *
 * Default: enabled (1) — because the real RTL path is not yet
 * implemented.
 */
void cad_rtl_set_fake_fixture(int enabled);

/*
 * Force a specific EDA prerequisite check to fail.
 *
 *   mode == 0 → both VCS and simv_soc_top preflight pass
 *   mode == 1 → VCS not found (NO-GO)
 *   mode == 2 → simv_soc_top not found (NO-GO)
 *   mode == 3 → both VCS and simv_soc_top absent (NO-GO)
 *
 * For negative/preflight testing only.  Setting mode to 0 restores
 * real preflight behaviour.
 */
void cad_rtl_set_missing_eda(int mode);

/*
 * Enable or disable capture-only mode for submit.
 *
 * When enabled (non-zero), rtl_submit() populates cmd_blob but returns
 * CAD_TR_SUCCESS *without* sending over the socket.  The last submit's
 * cmd_blob bytes can be read via cad_rtl_get_last_submit_blob().
 *
 * Default: disabled (0).  For unit/integration testing only.
 */
void cad_rtl_set_capture_mode(int enabled);

/*
 * Return the last submit's cmd_blob bytes captured in capture mode.
 * size receives the byte count (or 0 if no submission has occurred).
 * Returns NULL if no blob has been captured.
 */
const void *cad_rtl_get_last_submit_blob(uint32_t *size);

#ifdef __cplusplus
}
#endif

#endif /* CADUCEUS_TRANSPORT_RTL_H */
