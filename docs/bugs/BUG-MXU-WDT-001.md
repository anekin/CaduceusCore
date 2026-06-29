# BUG-MXU-WDT-001: Controller Watchdog Timer Missing

**Case**: MX-10
**Severity**: Medium (functional gap, not correctness bug)
**Status**: Open
**Date**: 2026-06-29

## Description

The `controller.v` FSM has no watchdog timer. If the mac_array or buffer modules
fail to respond (e.g., stuck in COMPUTE), the controller has no mechanism to
detect the stall and raise `STATUS.ERROR`. Currently `STATUS.ERROR` can only be
set by `cmd_abort` in specific FSM states.

## Current Behavior

- Controller progresses purely on internal cycle counters (`compute_timer`, `store_counter`)
- No external stall detection exists
- `STATUS.ERROR` only transitions via `cmd_abort` in READ_DIMS/LOAD_W/LOAD_A/COMPUTE/STORE_OUT

## Expected Behavior

A watchdog counter should:
1. Increment when the FSM stays in the same state beyond expected cycles
2. Set `STATUS.ERROR=1` when a threshold is exceeded (e.g., N stuck cycles)
3. Reset when FSM transitions normally or on explicit clear

## Verification

MX-10 test confirmed: normal path ERROR=0 (correct). Timeout path cannot be tested
because the watchdog mechanism does not exist.

## Impact

- HW fault detection gap: silent hang can occur without ERROR flag
- Compliance gap: watchdog is specified in `rtl/testplan.md` MX-10
