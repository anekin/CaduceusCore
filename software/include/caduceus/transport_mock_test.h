/*
 * CaduceusCore Mock Transport — Public Test API
 *
 * Functions to configure the mock transport's deterministic behavior
 * from test code.  Link against the runtime library to use these.
 */

#ifndef CADUCEUS_TRANSPORT_MOCK_TEST_H
#define CADUCEUS_TRANSPORT_MOCK_TEST_H

#include "caduceus/runtime.h"
#include "caduceus/cad_transport.h"
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Set how many ticks must elapse before a fence signals after submit.
 * 0 = fence signals immediately (default). */
void cad_mock_set_pending_ticks(int n);

/* Set an error code (cad_error_t) to return on the next submit.
 * 0 = no error (default). The error is consumed after one submit. */
void cad_mock_set_next_submit_error(int e);

/* Advance the mock's internal tick counter by n. */
void cad_mock_advance_ticks(int n);

/* Reset mock state to defaults. */
void cad_mock_reset(void);

/* Get current tick counter. */
int cad_mock_get_tick(void);

/* Query the operation log. count_out receives the number of entries. */
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

const mock_op_log_entry_t *cad_mock_get_op_log(cad_device_t device,
                                                 uint32_t *count);
void cad_mock_clear_op_log(cad_device_t device);

/* Return the last serialized payload passed to mock_submit().
 * The returned pointer is valid until the next cad_mock_reset() or
 * mock_submit() call. size receives the payload size in bytes, or 0
 * if no payload has been submitted. */
const void *cad_mock_get_last_submit_payload(uint32_t *size);

#ifdef __cplusplus
}
#endif

#endif /* CADUCEUS_TRANSPORT_MOCK_TEST_H */
