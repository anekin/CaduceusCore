# func-model-gap-closure Issues

## Issue 001: `_create_firmware` default change regression (2026-07-27)

### Symptom
`test_interrupt_driven_chain_dispatch` in `test_func_model_signoff_v3_integration.py` failed with `assert not emu.interrupt_pending`. The test was written for NPUFirmware behavior where `interrupt_pending` is cleared after dispatch. When Task 3 changed the `_create_firmware` default to prefer Spike, SpikeFirmware was selected (since Spike artifacts are available in the dev environment), and its interrupt state management differs from NPUFirmware.

### Root cause
`FuncModel._create_firmware` default `use_spike` logic in Task 3 changed the env-unset path from `use_spike=False` (NPUFirmware) to `use_spike=True` (prefer Spike). This broke all callers that relied on `FuncModel()` resolving to NPUFirmware.

### Fix applied (2026-07-27)
Reverted `_create_firmware` default: when `use_spike=None` and `CADUCEUS_USE_SPIKE` is not set, return NPUFirmware unconditionally. Spike remains opt-in via `use_spike=True` or `CADUCEUS_USE_SPIKE=1`.

### Files changed
- `sim/func_model.py` — `_create_firmware` only (lines 77–97)

### Verification
- `test_npu_firmware_deprecation.py`: 9/9 PASS
- `test_func_model_signoff_v3_integration.py`: 4/4 PASS
- `compileall sim/func_model.py sim/miniv.py`: OK
- `DeprecationWarning` on `NPUFirmware()` construction: fires + exits 0

### Notes
- The original Task 3 plan explicitly marked the default change as optional/low-priority.
- The `DeprecationWarning` in `NPUFirmware.__init__` and the 9 new deprecation tests are preserved intact.
- In environments with Spike artifacts, `FuncModel(use_spike=True)` or `CADUCEUS_USE_SPIKE=1` still select SpikeFirmware correctly.
- `CADUCEUS_USE_SPIKE=0` still forces NPUFirmware.

## Issue 002: Spike mmul_smoke tolerance failure — pre-existing (2026-07-27)

### Symptom
`PYTHONPATH=sim python3 sim/spike_host.py --mode mmul_smoke --model ~/models/qwen2.5-1.5b-instruct-q4_k_m.gguf --layers 1 --ops Q_proj` reports `[FAIL] L0 Q_proj max_diff=1.07e+03`.

### Root cause
Pre-existing numerical tolerance issue between NPU int4 matmul and llama.cpp reference. Confirmed same result on baseline commit `d6b1adc` before gap-closure tasks. Not introduced by Tasks 1-4.

### Status
Not in scope for gap-closure. Documented for awareness.

## Issue 003: INTC PENDING KeyError during Spike forward — pre-existing (2026-07-27)

### Symptom
During `spike_host.py --mode forward`, the MMIO server repeatedly logs `KeyError: 1073766400` at `mmio_bridge.py:590` (`self._status[INTC.BASE + INTC.PENDING] &= ~value`).

### Root cause
`_handle_intc` assumes `self._status[INTC.BASE + INTC.PENDING]` has been initialized (via a write to INTC.PENDING) before INTC.ACK is written. When Spike firmware issues ACK before PENDING register has been touched, `self._status` dict has no key, causing `KeyError` on the `&=` operation.

### Impact
The main forward pass completes normally (deterministic=YES, op coverage correct). The MMIO server thread pool logs errors per-request, and socketserver continues processing.

### Status
Pre-existing. Not in scope for gap-closure. Would be a one-line fix (`self._status.get(INTC.BASE + INTC.PENDING, 0) & ~value`) but the task explicitly forbids modifying engine behavior or data-path logic beyond docstring updates.

## Issue 004: 8 test_engines.py failures — pre-existing assertion drift (2026-07-27)

### Symptom
8 tests in `test_engines.py` fail with engine model assertion drift (e.g., `test_tensor_core_decode: assert 291472 > 393634`).

### Root cause
Engine model timing parameters changed without corresponding test expectation updates. Confirmed on baseline commit `d6b1adc`. Not introduced by Tasks 1-4.

### Status
Pre-existing. Not in scope for gap-closure.
