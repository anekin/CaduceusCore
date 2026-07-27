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
Resolved — numerical tolerance bug (max_diff=1.07e+03) was fixed by BUG-SOC-FM-005 (commit 67de684 weight tile layout + commit 78a3a37 firmware activation offset), post-fix max_diff=9.16e-05. Note: F3 Spike integration test mmul_smoke is SKIPPED on EDA server because the 1.5B model file is missing (environment issue), not a numerical regression.

## Issue 003: INTC PENDING KeyError during Spike forward — pre-existing (2026-07-27)

### Symptom
During `spike_host.py --mode forward`, the MMIO server repeatedly logs `KeyError: 1073766400` at `mmio_bridge.py:590` (`self._status[INTC.BASE + INTC.PENDING] &= ~value`).

### Root cause
`_handle_intc` assumes `self._status[INTC.BASE + INTC.PENDING]` has been initialized (via a write to INTC.PENDING) before INTC.ACK is written. When Spike firmware issues ACK before PENDING register has been touched, `self._status` dict has no key, causing `KeyError` on the `&=` operation.

### Impact
The main forward pass completes normally (deterministic=YES, op coverage correct). The MMIO server thread pool logs errors per-request, and socketserver continues processing.

### Status
Fixed by func-model-remaining-fixes Task 1: one-line .get() fix in _handle_intc, ACK-before-PENDING defense closed. The previous 'Not in scope for gap-closure' restriction is lifted by this plan.

## Issue 004: 8 test_engines.py failures — pre-existing assertion drift (2026-07-27)

### Symptom
8 tests in `test_engines.py` fail with engine model assertion drift (e.g., `test_tensor_core_decode: assert 291472 > 393634`).

### Root cause
Engine model timing parameters changed without corresponding test expectation updates. Confirmed on baseline commit `d6b1adc`. Not introduced by Tasks 1-4.

### Status
Documented, not fixed — DSE timing model bugs, do not affect Func Model golden reference or RTL verification. Block Engine is currently selected and DSE will not be re-run in the short term. See `reports/dse-engine-model-bugs-2026-07-27.md` for per-bug fix plan.

## Issue 005: FM-SOC-10X SFU RMSNORM mismatch — pre-existing (2026-07-27)

### Symptom
`bash sim/regression/run_fm_soc_all.sh FM-SOC-10X` fails: `FM-SOC-10X failed: op00 RMSNORM pre-attn: SFU mismatch max_abs=2.95e+00`. The test compares RTL SFU RMSNORM output (via PCIe TLP readback) against `GoldenSFU.rmsnorm_hw()` golden reference with `tol_abs=2e-3, tol_rel=1e-2`. The max absolute error of 2.95 far exceeds the 2e-3 absolute tolerance.

### Root cause
Pre-existing SFU RMSNORM implementation mismatch between RTL hardware and the Python golden model (`GoldenSFU.rmsnorm_hw()` at `sim/golden_executor.py:515`). Confirmed on baseline commit `d6b1adc` (before gap-closure Tasks 1-4) — same error. The mismatch is likely in the hardware RMSNORM computation (floating-point rounding, subnormal handling, or sqrt precision) but the root cause requires RTL-level analysis.

### Impact
- FM-SOC-10X is a P4 integration test covering PCIe TLP → DRAM → firmware → 17-op blk.0 chain → DRAM → PCIe → host readback. The first op (RMSNORM pre-attn) fails, blocking subsequent op checks.
- 31/33 other FM-SOC cases pass, including SFU-heavy cases (Softmax, GELU, SiLU, LayerNorm).
- `golden_executor.py` was not modified by Tasks 1-4; the RTL simv binary is unchanged from Jul 22.

### Status
Pre-existing. NOT introduced by gap-closure Tasks 1-4.

## Issue 006: FM-SOC-032 stuck/hang during F3 regression (2026-07-27)

### Symptom
During Wave F3 FM-SOC regression, `FM-SOC-032` appeared to hang — the simv process consumed 98% CPU for 15+ minutes without completing. Per the testcase list, FM-SOC-032 normally completes in ~416 seconds (~7 min). The simv was manually killed at ~15 min.

### Root cause
Unknown. Hypotheses: (1) resource contention on sz0001, (2) regression from gap-closure Python changes affecting P4SpikeRunner._build_032(), (3) infinite loop in RTL hardware simulation.

### Status
Inconclusive. Needs re-investigation on clean sz0001 environment. Not confirmed as gap-closure regression.
