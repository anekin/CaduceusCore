# func-model-gap-closure Learnings

## Task 1: APBPeripheral + APBDecoder (2026-07-26)

### Files created/modified
- **Created**: `sim/models/apb_peripheral.py` — RegisterField dataclass + APBPeripheral base class + 8 factory functions
- **Created**: `sim/tests/test_apb_peripheral.py` — 46 tests covering happy/failure/anti-vacuous/decoder
- **Modified**: `sim/models/crossbar.py` — APBDecoder.SLAVES expanded from 7 to 8 slaves (added PCIE_DMA at index 7), decode() now validates `page <= 7` instead of masking with `0xF`

### Design decisions
- **Access modes**: `rw` (read-write), `r` (read-only, write silently ignored), `w` (write-only, triggers callback), `w1c` (write-1-to-clear, triggers callback). The `w` access mode stores the written value (debatable for pure write-only registers but useful for testing completeness).
- **PCIe peripheral (slave 4)**: Since `sim/regmap.py` does not define a PCIE register class, offsets were derived from `sim/models/pcie.py:PCIeState` fields (completer_id@0x00, max_payload_size@0x04, msix_enable@0x08, msix_vector@0x0C, irq_enable@0x10, irq_pending@0x14, bar0_base@0x18, bar0_mask@0x1C, bar1_base@0x20, bar1_mask@0x24).
- **APBDecoder range check**: Changed from `Addr.INTC_BASE + 0x1000` upper bound to `Addr.PCIE_DMA_BASE + 0x1000` to cover the 8-slave address space. `page & 0xF` replaced with explicit `page > 7` check.
- **Register alignment**: `RegisterField.__post_init__` enforces 32-bit alignment and 4KB window bounds at construction time.

### Verification results
- **New tests**: 46/46 PASS in 0.13s
- **Regression**: `test_mmio_bridge.py` (2 tests) unaffected — PASS
- **compileall**: All 3 files compile clean
- **Pre-existing issues noted**: `test_engines.py::test_tensor_core_decode` fails (TensorCore 291472c vs BlockEngine 393634c — engine model assertion drift, unrelated to this task). 5 cocotb-dependent test files fail to collect on this environment (no cocotb installed).

### Known gaps
- MMIOBridge (`sim/mmio_bridge.py`) still uses `_status[addr]` dict for register storage — not yet migrated to APBPeripheral. Planned for a follow-up task.
- `sim/regmap.py:validate()` does not include PCIE_DMA in its address collision check — cosmetic, but worth noting.

## Task 2: Spike firmware SRAM/DRAM via CrossbarModel (2026-07-26)

### Files modified
- **Modified**: `sim/spike_mmio_server.py` — Removed `_normalize_addr()`; `_handle_request()` now accepts `crossbar` parameter and routes MMIO addresses (0x40000000–0x7FFFFFFF) through `bridge.handle()` while non-MMIO addresses (SRAM/DRAM) go through `crossbar.read/write(MASTER_IBEX, addr, 4)`. `serve()` signature updated to accept `crossbar`; `main()` passes `model.crossbar`.
- **Modified**: `sim/spike_host.py` — `_launch_spike()` L931 now passes `crossbar=model.crossbar` to `serve()`.
- **Modified**: `sim/spike_firmware.py` — `SpikeFirmware.__init__` captures `self.crossbar = sim_modules.get('crossbar')`; L231 `_ensure_spike_running()` passes `crossbar=self.crossbar` to `self._serve_fn()`.
- **Created**: `sim/tests/test_spike_mmio_server.py` — 18 unit tests covering MMIO→bridge, SRAM→crossbar, DRAM→crossbar, crossbar=None→ERR, edge cases.

### Design decisions
- **MMIO range**: All addresses 0x40000000–0x7FFFFFFF are treated as MMIO and routed through `bridge.handle()`. Addresses in the APB decoder range (0x40000000–0x40007FFF) map to real registers; addresses above that map to `_handle_doorbell`/INTC or return 0, matching existing bridge behavior.
- **Non-MMIO range**: SRAM (0x20000000–0x203FFFFF), DRAM (0x80000000+), and any other non-MMIO address go through `crossbar.read/write(MASTER_IBEX, addr, 4)`. The crossbar handles DECERR for unmapped addresses.
- **crossbar=None behavior**: Non-MMIO access with no crossbar returns a clear error string (`ERR crossbar required for SRAM/DRAM access`) rather than silently returning 0 or crashing. There is NO fallback to the old `_normalize_addr()` path.
- **Byte order**: 32-bit values are converted to/from little-endian bytes (RISC-V native byte order) when crossing the crossbar boundary. Short reads are zero-padded on the right.
- **No hardcoded master ID**: Uses `CrossbarModel.MASTER_IBEX` (value 0) constant throughout.

### Verification results
- **New tests**: 18/18 PASS in 0.11s
- **Regression**: `test_apb_peripheral.py` (46 tests) + `test_mmio_bridge.py` (2 tests) — all PASS
- **compileall**: All 4 files compile clean

### Known gaps
- Spike E2E regression (`spike_host.py --mode mmul_smoke`) requires built Spike binary + firmware ELF — not runnable on this dev environment.
- The `_handle_request` function now has a 3-argument signature; any external callers (none known outside these 3 files) would need updating.

## Task 3: NPUFirmware DeprecationWarning (2026-07-26)

### Files modified
- **Modified**: `sim/miniv.py` — `NPUFirmware.__init__` now emits `DeprecationWarning` at construction (not at module import); `NPUFirmware._dispatch` docstring updated with `DEPRECATED` marker pointing to `sim/spike_host.py`.
- **Modified**: `sim/func_model.py` — `_create_firmware` now prefers Spike by default when `use_spike is None` (checked via `CADUCEUS_USE_SPIKE` env var: unset or `1`/`true`/`yes` → Spike; `0`/`false`/`no` → NPUFirmware).
- **Created**: `sim/tests/test_npu_firmware_deprecation.py` — 9 tests covering warning emission, `stacklevel=2`, method callability, docstring content, and subprocess CLI equivalent.

### Design decisions
- **Warning on construction, not import**: The `DeprecationWarning` fires when `NPUFirmware.__init__` is called, not at module import time. This matches the standard Python deprecation pattern (the warning is about using the class, not importing it) and avoids alerting on every `from sim.miniv import NPUFirmware` that never constructs.
- **stacklevel=2**: Points to the caller of `NPUFirmware(...)`, so traceback shows the call site, not the `warnings.warn()` line inside `__init__`.
- **`_create_firmware` default change**: The env-var override is preserved (`CADUCEUS_USE_SPIKE=0` forces NPUFirmware), but the unset default now prefers Spike. This means existing test files using `FuncModel()` without a `use_spike` argument will try Spike by default — they need `use_spike=False` or `CADUCEUS_USE_SPIKE=0` when Spike artifacts are absent.

### Verification results
- **New tests**: 9/9 PASS in 0.40s
- **Regression**: `test_apb_peripheral.py` (46) + `test_spike_mmio_server.py` (18) + `test_mmio_bridge.py` (2) = 66/66 PASS in 0.15s
- **CLI check**: `python3 -W default::DeprecationWarning -c "from sim.miniv import NPUFirmware; NPUFirmware(...)"` → `DeprecationWarning` emitted as expected
- **compileall**: All 3 files compile clean

### Known gaps
- Tests using `FuncModel()` without `use_spike=` argument will now default to Spike; if Spike artifacts aren't available, they'll raise `RuntimeError`. Existing callers should either pass `use_spike=False` explicitly or set `CADUCEUS_USE_SPIKE=0` in their environment.

## Task 4: Full Regression Gate + CrossbarModel Audit (2026-07-27)

### Files modified
- **Modified**: `sim/mmio_bridge.py` — `_get_mem()` and `_translate_addr()` docstrings updated to mark as "Fallback-only" / DEPRECATED in favor of CrossbarModel routing.

### CrossbarModel routing audit
All four engine data paths in `sim/mmio_bridge.py` correctly route through CrossbarModel when available, with fallback to `_get_mem`/`_translate_addr` when crossbar is None:
- `_run_mxu_compute` (L184-243): `xbar.read/write(MASTER_MXU, ...)` ✅
- `_run_sfu_compute` (L318-328): `xbar.read/write(MASTER_SFU, ...)` ✅
- `_run_vector_compute` (L416-427): `xbar.read/write(MASTER_VEC, ...)` ✅
- `_run_dma_transfer` (L520-525): `xbar.read/write(MASTER_DMA, ...)` ✅

### Regression results
- **pytest**: 866 passed, 8 failed (all pre-existing `test_engines.py` assertion drift), 5 cocotb collection errors (pre-existing, no cocotb installed). No new regressions.
- **FuncModel assertion**: `fm.crossbar is not None` and `fm.pcie is not None` — PASS
- **Spike mmul_smoke**: FAIL (tolerance max_diff=1.07e+03) — pre-existing; confirmed same result on baseline commit `d6b1adc`
- **Spike forward**: Completes without crash; WARN on tolerance, deterministic=YES. Pre-existing INTC KeyError (`_handle_intc` line 590) when Spike firmware ACKs IRQ before PENDING is initialized.

### Known gaps
- mmul_smoke tolerance failure (L0 Q_proj max_diff=1.07e+03) is pre-existing and unrelated to gap-closure changes.
- INTC KeyError (`self._status[INTC.BASE + INTC.PENDING] &= ~value` at line 590) is a pre-existing edge case: `_handle_intc` assumes PENDING has been initialized before ACK is written. Not in scope for this plan.
- 8 `test_engines.py` failures are pre-existing engine model assertion drifts (timing model parameters changed without test updates).
- Evidence files: `build/evidence/task4-pytest-summary.txt`, `build/evidence/task4-crossbar-audit.txt`, `build/evidence/task4-funcmodel-assert.txt`, `build/evidence/task4-spike-e2e.txt`
