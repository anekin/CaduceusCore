/*
 * CaduceusCore Func Model Transport
 *
 * C-compatible constructor and vtable for the binary device protocol
 * client transport.  The implementation is C++ because it links against
 * FlatBuffers generated code; the public symbols are C-linkable so
 * the runtime core (C11) can register the transport.
 */

#ifndef CADUCEUS_TRANSPORT_FM_H
#define CADUCEUS_TRANSPORT_FM_H

#include "caduceus/cad_transport.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Default Unix socket path used by fm://python and bare fm:// */
#define CAD_TRANSPORT_FM_DEFAULT_SOCK_PATH "/tmp/caduceus_fm.sock"

/* Transport vtable exposed to the runtime core. */
extern const cad_transport_ops_t cad_transport_fm_ops;

/*
 * Initialize the Func Model transport.
 *
 * Accepted URI forms:
 *   fm://                -> default Unix socket
 *   fm://python          -> default Unix socket
 *   fm://unix?path=...   -> explicit Unix socket path
 *
 * Returns 0 on success, negative cad_transport error code on failure.
 */
int cad_transport_fm_init(void **tpriv, const char *uri);

#ifdef __cplusplus
}
#endif

#endif /* CADUCEUS_TRANSPORT_FM_H */
