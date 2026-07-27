# func-model-remaining-fixes Learnings

## 2026-07-27 Task 1: INTC ACK-before-PENDING KeyError Fix

**Bug:** `_handle_intc` at `sim/mmio_bridge.py:590` used `self._status[key] &= ~value`, 
which raises `KeyError` when INTC.ACK is written before any INTC.PENDING write 
has populated the key in `self._status` (a dict that starts empty per `__post_init__`).

**Fix:** One-line change — replaced `&= ~value` with `self._status.get(key, 0) & ~value`,
mirroring the safe `.get()` pattern already used in `_set_irq` (lines 625-626).

**Test results:** 4 new unit tests + 9 existing INTC regression tests = 13/13 PASS.
`compileall` zero errors.

**Pattern:** When using `&=`, `|=`, or any in-place operator on a dict value that 
may not exist, always default-initialize. The safe pattern is:
  `self._status[key] = self._status.get(key, 0) & ~value`
  `self._status[key] = self._status.get(key, 0) | value`

**Verification:** Pre-fix, `test_ack_before_pending_no_crash` crashed with 
`KeyError: 1073766400` (= INTC.BASE + INTC.PENDING = 0x40006000). 
Post-fix, all tests pass.
