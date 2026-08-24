# fm-soc-datapath-hardening — Learnings

## Todo 10: 28 层 DRAM 布局重排（E2E-04 前置）

### Findings (2026-08-24)
- Modified ONLY test-local constants in `sim/tests/test_soc_fm_long_sequence.py`:
  `_DESC_BASE` 0x8060_0000 → **0x8071_0000** (exactly block 27 end =
  `_CHAIN_BLOCK_BASE + 28×_CHAIN_BLOCK_STRIDE`), `_SCRATCH_MMUL_OUT`
  0x8070_0000 → **0x8072_0000**, `_SCRATCH_SCALE` → 0x8072_4000.
  `sim/address_space.py` and `sim/command_ring.py` untouched — the global
  `assert_desc_clear_of_used_regions` signature is not extended; instead the
  test-local block-overlap loop (the same guard role, P0/P1/P2P3-scoped) now
  iterates `tsf._CHAIN_NUM_BLOCKS` (28) instead of `range(n_layers)` (11), and
  asserts three disjointness pairs: desc-pool vs block, scratch vs block, and
  scratch vs desc pool. Scratch size pinned at 0x10000 (matches the acceptance
  math; the actual MMUL scratch usage is M×N×4 ≤ 0x4000 + scale ≤ 0x100).
- **Why 28 blocks and not `n_layers`**: with `_NUM_LAYERS=11` the old loop only
  covered blocks 0–10, which is why the old `_DESC_BASE=0x8060_0000` never
  tripped — it collides with block **23** (0x805D_0000–0x8061_0000), a region
  the 11-layer test never touches. Covering all 28 fixture blocks makes the
  guard bite at the old layout today (proved by failure-injection sanity: old
  desc pool overlaps block 23 → assert fires) and keeps Todo 11's 28-layer gate
  safe without further layout work.
- Acceptance command prints `LAYOUT OK`, exit 0:
  `_DESC_BASE` = 0x8071_0000 ≥ block0+28×stride; desc pool end 0x8071_84C0
  (531×64) < 0x8080_0000; scratch end 0x8073_0000 < 0x8080_0000;
  `_SCRATCH_MMUL_OUT` ≥ desc pool end.
- `test_soc_fm_long_sequence.py -v` → **2 passed in 32.73s, exit 0**
  (baseline fingerprints and the 208-command persistent-offset gate unchanged —
  layout relocation is behavior-neutral at 11 layers). 5 DeprecationWarnings
  (NPUFirmware) benign, consistent with Todos 1–9.
- **Todo 11 readiness**: block 27's weights live at 0x806D_0000 and the desc
  pool starts at 0x8071_0000, so no descriptor can be clobbered by any block's
  weight/activation writes; results stay at 0x8080_0000. The only remaining
  Todo 11 changes are `_NUM_LAYERS` 11→28 and the ring-wrap assertion
  (531 % 16 = 3, not 0). Evidence in
  `build/evidence/task-10-fm-soc-datapath-hardening.txt`.

## Todo 7: 固件 boot 序列 FM 验证守卫（SOC-18）

### Findings (2026-08-24)
- Added `sim/tests/test_firmware_boot_sequence.py`: 5 tests.
  `PYTHONPATH=sim python -m pytest sim/tests/test_firmware_boot_sequence.py -v`
  → 5 passed, exit 0. Evidence in `build/evidence/task-7-fm-soc-datapath-hardening.txt`.
  Neighboring `test_soc_fm.py -q` → 52 passed (0 new failures).
- **step() genuinely executes the real `npu_firmware.hex`** from the reset
  vector through `startup.S` into `firmware_main()`. Proven not by PC motion
  alone but by firmware-side observables: `INTC.ENABLE==0x1FF` (programmed by
  firmware_main), doorbell `LAST_STATUS==0xAA` (the firmware's own
  completion-ring write/readback self-test) and the `0xDEADBEEF` marker at
  COMPLETION_RING_ADDR (0x80008000) in DRAM. ~2000-4000 steps suffice.
- **Step-driven MMUL dispatch completes end-to-end**: submit via
  `host_write_command` → continue stepping → HOST_HEAD/NPU_HEAD advance to 1,
  `LAST_STATUS==0x2000` (success pattern), and the completion-ring entry
  (cmd_id=0, status=0) overwrites the 0xDEADBEEF debug marker — the marker
  transition is the anti-vacuous proof that `write_completion()` really ran.
- **RV32M mul misdecode (emulator gap, unchanged per scope)**: `RISCVMini`
  decodes `mul` (opcode 0x33, funct7=0x01) as `sub`. In the C firmware's MMUL
  copy-back, `desc.output_addr + (m*desc.N + n_start)*4` with m=0 compiles to
  a mul; misdecoded, `0*2` becomes `-2`, shifting the copy-back destination to
  `0x80FFFFF8` instead of `0x81000000`. Verified empirically: the golden value
  (50.0) IS computed correctly and lands at the shifted address. Consequence:
  golden numeric output is intentionally NOT asserted in this guard —
  bit-exact numerics for the same ABI stay in
  `test_soc_fm.py::test_firmware_bootflow` (Python dispatch) and Spike signoff.
  If mul decoding is ever fixed in miniv.py, the copy-back assert could be
  upgraded to golden equality.
- **WFI encoding mismatch (emulator gap)**: real firmware WFI is
  `0x10500073` (funct12=0x105); `RISCVMini.step()` matches funct12==0x305
  (a different SYSTEM instruction). So the doorbell poll loop runs as a busy
  poll in the FM — harmless here because MMIO reads are synchronous and the
  command is observed deterministically. `test_soc_fm.py:605` deliberately
  uses 0x305 for the WFI-wake test; the real-firmware path never hits that
  branch.
- **boot() sp is provisional**: `NPUFirmware.boot()` sets sp=DMEM top
  (0x20000), but `startup.S` overwrites it with the linked `_stack_top`
  symbol (DMEM top minus the 16 KB STACK_SIZE region, 0x14010 in the current
  build). By the time firmware_main polls, sp≈0x13FD0 (a few frames deep).
  The guard asserts sp moved off the provisional value into DMEM rather than
  pinning the build-specific `_stack_top`.
- **Failure injection**: corrupting the loaded image's reset-vector word
  (trap-table padding 0x00000000 → EBREAK 0x00100073) makes `step()` return
  False with PC and `instructions_executed` frozen at 0; healthy control
  advances PC 0→4 and executes 1 instruction. Corrupting word 0 (the
  vectored trap table region) is deterministic — the image at PC=0 is part
  of the loaded ROM.
- **Boot ROM isolation** asserted as Todo 5 prescribed: byte-snapshot of the
  ROM unchanged after DMEM/SRAM/DRAM writes (each write readback-checked to
  prove it landed). The Todo 5 quirk (`_mem_write` allows writing the ROM
  region itself) is untouched.

## Todo 5: Ibex 共享地址空间跨引擎 FM 验证守卫（SOC-16）

- **Routing model (unchanged)**: `RISCVMini` SoC mode decode is: Boot ROM `0x0000_0000–0xFFFF` → `_boot_rom`; DMEM `0x0001_0000–0x1_FFFF` → `self.dmem` (local, NOT on crossbar); MMIO `0x4000_0000–0x7FFF_FFFF` → bridge callback; SRAM/DRAM → `CrossbarModel.read/write` (DECERR via `ValueError` otherwise). `_mem_read/_mem_write` were NOT modified.
- **Cross-engine coherence works in both directions**: Ibex→MXU and MXU→Ibex both go through the same `CrossbarModel` bytearrays, so `emu._mem_write(SRAM)` is immediately visible to `crossbar.read(MASTER_MXU, ...)` and vice versa. Test asserts both directions.
- **Modeling quirk to carry into Todo 7**: `RISCVMini._mem_write` currently ALLOWS writes into the boot ROM region (`self._boot_rom[off:off+4] = data`), unlike the RTL `boot_rom.v` which is read-only. Boot ROM isolation here is therefore tested as "writes to DMEM/SRAM/DRAM do not modify boot ROM" (byte-snapshot compare), NOT "writes to boot ROM are rejected". If Todo 7's boot-sequence failure injection needs a read-only boot ROM, this quirk must be revisited as a separate modeling change.
- **DMEM is invisible to the crossbar**: any read at a DMEM address through `CrossbarModel` raises `ValueError` (DECERR) — used as the isolation assertion.
- **Failure-injection pattern used**: write `0xFEEDFACE` to DMEM[0x100] → read SRAM[0x100] (fresh model, zeroed SRAM) → assert value differs; a decoder aliasing bug would make this test fail. Deterministic because fresh `FuncModel` memories are zeroed.
- **Address quirk**: `FuncModel` defaults `sram_kb=512` (SRAM = 0x2000_0000–0x2008_0000), not the 4MB RTL size; tests use offsets well inside 512KB.
# Todo 4 — APB register conformance replay gate (SOC-15)

## Findings (2026-08-24)
- Added `sim/tests/test_apb_register_conformance.py`: 28 tests / 8 peripheral
  coverage groups (MXU/SFU/VECTOR/DMA/PCIe/DOORBELL/INTC/PCIE_DMA).
  `PYTHONPATH=sim python -m pytest sim/tests/test_apb_register_conformance.py -v`
  → 28 passed, exit 0. Neighboring `test_apb_peripheral.py` unaffected (46 passed).
- **regmap gap**: `regmap.py` has NO PCIe register class — only `Addr.PCIE`.
  PCIe offsets live solely in `make_pcie_peripheral()` (mirrors `pcie_ep_wrapper`).
  Conformance gate therefore skips the offsets cross-check for PCIe and instead
  asserts base addr + unique offsets within the 4 KB window. If PCIe registers
  are ever promoted to `spec/npu_abi.json`, extend the gate to cross-check them.
- **'w' fields are readable in the model**: `APBPeripheral.write` stores values
  for `access='w'` (e.g. DOORBELL.HOST_TAIL, CMD registers), confirmed by
  `test_doorbell_host_tail_write_only`. Gate asserts stored value to match the
  implementation, not the abstract write-only semantics.
- **w1c has no public set path**: INTC.ACK defaults to 0 and only clears bits;
  the gate seeds `_values[ACK]=0xFFFF` (white-box) to observe clear semantics.
  Note: the model has no API for hardware to assert PENDING before firmware
  ACKs — a known modelling gap if interrupt flow is ever replayed here.
- All read-only registers (STATUS/RD_ERR_CODE/…) default to 0, so the
  failure-injection check is "hostile write → readback unchanged".
- Replay proves overwrite semantics for 'rw' (0x3 then 0x6, catches
  OR-accumulate or stuck-bit bugs) and w1c bit-exactness (0x00F0 → 0xFF0F).
# Todo 3 — AXI 仲裁公平性 FM 验证守卫（SOC-14）

## Findings (2026-08-24)
- Added `sim/tests/test_crossbar_arbitration.py`: 5 tests (AW fairness,
  AR fairness, DECERR, AXI ID routing, failure injection).
  `PYTHONPATH=sim python -m pytest sim/tests/test_crossbar_arbitration.py -v`
  → 5 passed, exit 0. Evidence in `build/evidence/task-3-fm-soc-datapath-hardening.txt`.
- **Model semantics (unchanged)**: `CrossbarModel._grant` records every request
  as `(slave_idx, master_id)` and always returns True — the functional model has
  no contention, so fairness is verified through the *grant history* (equal
  per-master counts + strict alternation + `_aw/_ar_last_granted` pointer), not
  through model-enforced arbitration. Any future model change that drops,
  reorders, or starves grants breaks the guard.
- **DECERR ordering quirk (useful, not a bug)**: `_decode` runs before
  `_next_axi_id` and `_grant`, so a DECERR transaction consumes neither an
  arbitration grant nor an AXI transaction ID. Turned into a guard assertion
  (`_aw_grants/_ar_grants/_txn_ids` unchanged after hostile addresses).
- **Test-authoring bug (caught by roundtrip check)**: first version addressed
  transactions as `addr_base + i*16 + m*4` using the raw sparse master IDs
  {1,4,5} — rounds overlapped (txn i=1,m=1 collided with i=0,m=5). Lesson:
  derive per-master slots from `MASTERS.index(m)`, never from the raw AXI
  master ID, when IDs are sparse. Fixed with 16B slots × 64B rounds.
- **Failure-injection variants**: (1) pad one master with phantom grants → count
  skew; (2) drop all grants of one master → missing share; (3) batch-per-master
  history (fair by count, never alternating) → proves the count-fairness and
  strict-alternation guards are independent and both bite.
- **AXI ID verified**: `(master_id<<8)|txn_id` composition, per-master counters
  advance independently, 8-bit wrap 0xFF→0x00, and `_last_axi_id` field
  separation via `>>8` / `&0xFF`.
# Todo 1 — PCIe TLP complete chain FM verification guard (SOC-13)

## Findings (2026-08-24)
- Added `sim/tests/test_pcie_tlp_chain.py`: 10 tests (4KB roundtrip, MPS=256B
  split, unaligned-payload padding, BAR routing SRAM+DRAM, bidirectional BAR
  isolation, tamper injection, out-of-BAR rejection, MSI state).
  `PYTHONPATH=sim python -m pytest sim/tests/test_pcie_tlp_chain.py -v`
  → 10 passed, exit 0. Evidence in `build/evidence/task-1-fm-soc-datapath-hardening.txt`.
- **Design choice**: reuse `PCIeModel` as-is (`sim/models/pcie.py` unchanged);
  all tests drive `FuncModel().pcie` + `FuncModel().crossbar` (the same wiring
  `func_model.py:39-41` uses). MPS splitting asserted on both sides via
  `last_tx_headers`/`last_rx_headers` (4KB → 16×3-DW headers, length=64 DWs,
  DW2 addresses contiguous); crossbar involvement proven via
  `_aw_grants`/`_txn_ids[MASTER_PCIE]` — a memory-only assertion would be vacuous.
- **Failure injection**: tamper 1 byte of `model.dram` (equivalent to a corrupted
  TLP landing) → assert readback != original AND readback matches the tampered
  byte only — guard detects corruption, not mere write echo.
- **Padding gotcha**: `tlp_write` pads the final chunk to a 4-byte DW boundary
  and advances the address by the PADDED length → padding zeros land in memory
  and appear in a longer readback. Locked in by the 1001-byte payload test.
- **Partial-write gotcha (not fixed, model frozen)**: `tlp_write`/`tlp_read`
  validate only the START address via `_resolve_bar`; a chunked write whose
  later chunks cross a region end raises `ValueError` from the crossbar
  MID-write, leaving earlier chunks committed. Tests use safe margins.
- **BAR0 range quirk**: `_resolve_bar` checks `len(crossbar.sram)` = FuncModel
  default 512 KB, NOT the 4 MB `PCIeState.bar0_mask`. Addresses ≥ 0x2008_0000
  fall into the BAR hole and raise `ValueError` (used as positive assertion).
- `FuncModel()` emits a DeprecationWarning for `NPUFirmware` (10 warnings in the
  run) — benign, expected per the repo's Spike migration.
# Learnings — fm-soc-datapath-hardening

## Todo 2 — INTC ENABLE/THRESHOLD gating (SOC-17 / FW-10)

### What changed
`sim/mmio_bridge.py::MMIOBridge._set_irq()` now gates the CPU notification:

```
cpu_irq = |(PENDING & ENABLE) and popcount(PENDING & ENABLE) >= THRESHOLD
```

mirroring `rtl/intc/intc_top.v:159`. PENDING still accumulates every fired
source bit — ENABLE gates the cpu_irq assertion, not the pending register
(matches existing `test_soc_boundary.py::test_enable_register_masks_interrupts`
contract).

### Key design decision — FM open-by-default vs RTL closed-by-default
- RTL resets `ENABLE=0` / `THRESHOLD=1`: cpu_irq can never fire until firmware
  programs ENABLE.
- The FM had *no* gating and several pinned tests rely on interrupt delivery
  with ENABLE never programmed (`test_interrupt_delivery`,
  `test_doorbell_single_mmul_interrupt`,
  `test_e2e_host_pcie_doorbell_firmware_scaled_blk0`). A strict RTL reset would
  break them, and they are out of scope to modify.
- Resolution: `_set_irq` treats an **unprogrammed ENABLE as 0x1FF (all 9 FM
  sources enabled)** — legacy open-default preserved — while any explicit
  ENABLE write activates real masking. With the `|(PENDING&ENABLE)` guard,
  explicit ENABLE=0 keeps cpu_irq low regardless of PENDING, even at
  THRESHOLD=0 (anti-vacuous corner, `test_failure_enable_zero_*` covers both
  threshold values).
- Divergence to keep in mind: FM ENABLE read-back of an unprogrammed register
  is 0 (`_status.get` default) while `_set_irq` gating treats it as all-on.
  Documented in the `_set_irq` docstring. If todo 6 (IRQ-driven dispatch)
  wants default-closed semantics, it must program ENABLE explicitly.

### Verification numbers
- Baseline characterization: 3/3 pinning old unconditional notify (pre-change).
- New test file: 10 tests covering single-source, ENABLE mask, THRESHOLD
  gating (2 and 3 sources), ACK clear + re-assert, WFI wake, WFI no-wake when
  masked, multi-source concurrency, failure injection (ENABLE=0, THRESHOLD
  0 and 1, 4 fired sources).
- `test_soc_fm.py -q`: 52 passed (0 new failures).
- INTC-adjacent files pre/post: 95 → 95 passed (0 new failures).
- Full sim/tests: 19 failed / 1482 passed / 1 skipped / 13 errors — matches
  known legacy baseline; zero INTC-related failures.
# Todo 8 — Spike↔Ibex ring 管理对齐 FM 验证守卫（FW-08）

## Findings (2026-08-24)
- Added `sim/tests/test_spike_ibex_ring_alignment.py`: 3 tests (happy head
  alignment, ring_size=16 wrap divergence failure injection, COMPLETION_STATUS
  + HOST_HEAD mirror). `PYTHONPATH=sim python -m pytest
  sim/tests/test_spike_ibex_ring_alignment.py -v` → **3 passed, exit 0, 3.3s**
  (Spike stack present on this host). Evidence in
  `build/evidence/task-8-fm-soc-datapath-hardening.txt`.
- **Two paths, one drain model**: the real firmware (`firmware_main`) drains
  ALL pending commands in a single Spike boot (`while (npu_head != host_tail)`)
  from the 1024-entry ring (`NPU_ABI_RING_ENTRIES`), writing
  `COMPLETION_STATUS[head]` per dispatch via doorbell MMIO — so the completion
  index set IS the dispatch-time head sequence (0..207 for 208 commands). The
  Python NPUFirmware advances head per `run_loop(1)` call. Happy test drives
  NPUFirmware with `FuncModel(ring_size=1024)` (same trick as the ring-stress
  tests) so both paths end at NPU_HEAD=HOST_HEAD=208 with zero wraps.
- **Spike-path trace is final-state + completion-array only**: the bridge
  `_status` exposes the post-drain NPU_HEAD/HOST_HEAD (written once at the end
  of the firmware drain loop) and the per-index COMPLETION_STATUS array; the
  DRAM completion ring written by Spike is NOT readable post-hoc (Spike memory
  is discarded on process exit), so the per-command head progression on the
  Spike side is recovered from the COMPLETION_STATUS index set, not from DRAM.
- **Byte-identical ring entries cross-check**: `spike_host.write_cmd_entry`
  (`<8I` fmt) and `FuncModel.host_write_command` (`<IQI8x`, 24 B + zero pad)
  produce byte-identical 32 B entries for the same (opcode, desc_addr, flags)
  because desc_addr < 2^32 — asserted as `ring_bytes` equality between paths.
- **Op class choice**: SFU softmax (ring op 0x01, sub-op 0) and Vector VADD
  (0x0F) are the proven-completing intersection of both dispatchers
  (`test_runtime_real_firmware.py` for the C firmware; long-sequence gate for
  NPUFirmware). MMUL (tile_mmul) and DMA_COPY were avoided: slow on the
  NPUFirmware path / descriptor-semantics mismatch (C DMA_COPY vs Python
  DMA_LD), respectively.
- **Failure injection is the real wrap divergence**: NPUFirmware(ring_size=16)
  produces heads `(k+1)%16` — 13 wraps, final head 0 — vs firmware 208. The
  guard asserts the exact wrap pattern, the wrap count (208//16), AND the
  divergence from the spike reference (heads[15]==0 vs 16, final 0 vs 208).
- **Env/robustness notes**: skip via `spike_firmware._is_spike_available()`
  (checks `spike_src/build/spike` + `npu_mmio_plugin.so` +
  `firmware/build/npu_firmware_spike.elf`, NOT `npu_firmware.elf`); skip
  behavior verified by monkeypatching `_is_spike_available` → `pytest.skip`
  raises `Skipped`. `sram_kb=4096` is required — the firmware's
  SFU_SCRATCH (0x20080000) / VEC_SCRATCH (0x20300000) exceed the default
  512 KB SRAM. Spike launch/cleanup is wrapped in try/finally with
  `_cleanup_spike` so a drain timeout never leaks the process or the
  `/tmp/npu_mmio.sock` socket. Module-scoped `spike_trace` fixture runs the
  real firmware path exactly once per session (≈3 s total).
- `FuncModel` DeprecationWarning (NPUFirmware) fires 3× per run — benign,
  consistent with the rest of the suite.
# Todo 9 — firmware_memory_contract.json 生成与比对（FW-09）

## Findings (2026-08-24)
- Added `scripts/gen_firmware_memory_contract.py` + `sim/tests/test_memory_contract.py`
  + committed `firmware_memory_contract.json` (REPO root).
  `python3 scripts/gen_firmware_memory_contract.py --check` → exit 0;
  `PYTHONPATH=sim python -m pytest sim/tests/test_memory_contract.py -v` →
  9 passed, exit 0. Evidence in `build/evidence/task-9-fm-soc-datapath-hardening.txt`.
- **JSON is a derived artifact, never a truth source**: `build_contract()` assembles
  `regions` (address_space.REGIONS), `ring` (command_ring constants +
  COMPLETION_ENTRY_SIZE), and `run` (observed from an actual minimal FuncModel
  doorbell MMUL run — descriptor range used, firmware_ring_size, observed max
  ring offset, final heads). `compare_contract()` re-derives everything from
  address_space/command_ring; the pytest adds the third source
  (spec/npu_abi.json rings.configuration).
- **"Actual run" is real, not hardcoded**: the generator executes 16 tiny
  MMUL commands (M=1,K=4,N=2) through `host_write_command`/`run_loop` and
  records doorbell bookkeeping (max_ring_offset_observed=15, final heads=0 mod
  the FuncModel firmware ring_size=16). Mirrors `test_doorbell_ring_wrap_16`
  semantics; no test-module imports, so todo 10's layout relocation can't
  silently corrupt this contract.
- **Import quirk**: `scripts/gen_firmware_memory_contract.py` must insert BOTH
  REPO and REPO/sim into sys.path — `func_model` imports `gen.npu_abi`
  (REPO-level package), and the acceptance command runs without PYTHONPATH.
- **Failure injection**: deep-copy JSON → `ring.RING_ENTRIES = 512` →
  `compare_contract` returns a mismatch naming RING_ENTRIES (test asserts
  non-empty + key mention + disagreement with `command_ring.RING_ENTRIES`).
  A second injection (region base tamper) guards the regions path.
- **contract_check interplay**: with the tampered RING_ENTRIES=512 the
  completion-ring end moves to 0x80004000, so contract_check alone would NOT
  catch the tamper (desc_base 0x80010000 still clears it) — the direct
  constant comparison is what bites. Both guards live in compare_contract.
- Determinism: JSON serialized with sort_keys + 2-space indent; `--check`
  regenerates (incl. the FM run) and diffs against the on-disk file, matching
  the `gen_npu_abi.py --check` regenerate-and-compare pattern.
# Todo 6 — IRQ-driven firmware dispatch FM guard (FW-10)

## Findings (2026-08-24)
- Added `sim/tests/test_irq_driven_dispatch.py`: 4 tests (happy 3-command
  stream, no-polling contrast, monkeypatch stall, INTC ENABLE=0 stall).
  `PYTHONPATH=sim python -m pytest sim/tests/test_irq_driven_dispatch.py -v`
  → 4 passed, exit 0. Evidence in
  `build/evidence/task-6-fm-soc-datapath-hardening.txt`.
  Adjacent regression: `test_intc_gating.py + test_soc_fm.py` → 62 passed,
  0 failed. No `firmware/npu_firmware.c` or `sim/mmio_bridge.py` changes.
- **Dispatch mechanism (unchanged)**: `NPUFirmware._wait_done` is IRQ-driven
  exactly when `self.riscv is not None` — it spins on `_irq_serviced`, which
  only `dispatch_interrupt` (the RISC-V trap handler) sets. The STATUS-poll
  branch (`while bridge.handle('read', STATUS) & 1`) is dead code while bound.
  Guard proves both directions: bound run = zero STATUS reads (spy on both
  `bridge.handle` AND `riscv.mmio_callback` — the callback is captured at
  `FuncModel.__init__`, so patching `bridge.handle` alone is NOT enough to
  observe CPU-side reads); unbound run = STATUS reads appear (anti-vacuous).
- **Failure injection (monkeypatch)**: suppress `bridge.irq_notify_callback`
  → `_set_irq` still accumulates INTC.PENDING, but the CPU never wakes.
  Firmware consumes command 1 and spins in `_wait_done`; the engine STATUS
  is DONE yet no completion is signaled — the stall assertion
  (NPU_HEAD=1, HOST_HEAD=0, engine done, trap handler never ran) fails if
  anyone adds a polling fallback to the IRQ path. Restoring notify + setting
  `riscv.interrupt_pending=True` wakes the spinner and drains the remaining
  commands — deterministic, bounded, no infinite loop.
- **Failure injection (Todo 2 gating)**: explicit INTC ENABLE=0 produces the
  identical stall through the real `_set_irq` gate (`notified == []` proves
  the gate blocked, not a monkeypatch). This is the end-to-end tie-in of
  Todo 2's `cpu_irq = |(PENDING&ENABLE) and popcount(...) >= THRESHOLD`.
- **Deterministic-bounded stall pattern**: `run_loop` runs in a daemon
  thread; a `_dispatch` spy records the first command entry, main thread
  waits on that, asserts the stall after a 0.2 s grace, then restores IRQ
  and joins. No wall-clock flakiness — the stall is a pure flag wait.
- **MMUL dispatch raises multiple IRQs**: tile_mmul waits (DMA act/wgt/scale/
  out + MXU compute) each complete via their own IRQ trap, so the serviced
  source list for one MMUL is [3,3,3,0,3]; assert `{0,1,2,3,8} ⊆ serviced`
  (engine sources + doorbell bit 8), not exact sequences.
- **Explicit ENABLE programming**: all tests program INTC ENABLE=0x1FF /
  THRESHOLD=1 up front (Todo 2's open-default would work, but explicit
  programming makes the gating semantics deterministic and documents that
  the happy path runs under a programmed gate).
- **INTC PENDING accumulates host bits**: `host_write_command` raises source
  bit 8 per command; `_handle_irq` services the LOWEST pending bit, so
  engine bits (0–3) always win over the doorbell bit during `_wait_done`,
  and leftover bit 8 is drained at the next run_loop iteration top.
- NPUFirmware DeprecationWarning ×4 per run — benign, matches Todos 1–5.
# Todo 12 — MobileNetV3 CV chain FM gate（E2E-05）

## Findings (2026-08-24)
- Added `sim/tests/test_mobilenetv3_fm_chain.py`: 4 tests (op-dict conversion,
  full-chain ring vs golden, determinism, weight-address tamper injection).
  `PYTHONPATH=sim python -m pytest sim/tests/test_mobilenetv3_fm_chain.py -v`
  → 4 passed, exit 0, ~58 s. Evidence in
  `build/evidence/task-12-fm-soc-datapath-hardening.txt`.
  Neighboring `sim/cv/tests/test_cv_traces.py` → 7 passed (sim/cv untouched).
- **Gate substance**: the full MobileNetV3-Small graph (124 nodes: 52 convs +
  11 depthwise + 2 Gemm classifier heads + SE blocks + residuals) runs through
  the doorbell ring: 8137 MMUL ring commands, 508 ring wraps (16-entry ring,
  persistent offset), every command drained by `NPUFirmware._dispatch` →
  `tile_mmul` → bridge `_run_mxu_compute`. All 54 GEMM layers bit-exact vs
  `GoldenMXU.matmul_int4_per_block`; 50/52 conv layers cos_sim = 1.0,
  2 layers are degenerate zero-vs-zero (see below). Chain wall ≈ 12 s.
- **Pre-existing FM gap found (worked around, NOT fixed — scope)**: `tile_mmul`
  computes the output-tile offset as `out_sram + n_start*4` (column stride
  128 floats), which matches the tile size only for M == 1. For M > 1 with
  N > 128 the second N-tile overlaps the first and clobbers it (SRAM
  accumulator AND DRAM output). All ring MMULs here are therefore chunked to
  M ≤ 64 AND N ≤ 128 per command; the golden reference is the same
  matmul_int4_per_block math so chunking is row/column-independent and
  bit-exactness holds. The M ≤ 64 limit is the broadcast activation layout
  (64 K-indices per 4096-byte tile, 64 M-rows per tile); the N ≤ 128 limit is
  purely the tile_mmul stride bug. Worth a dedicated FM bug ticket (impact:
  any M>1 MMUL with N>128 through the Python firmware emulator is silently
  wrong — the todo-11 ring MMULs escaped because their outputs went to
  unverified scratch).
- **Firmware data layouts (verified bit-exact against `_run_mxu_compute`)**:
  (1) weights: per (n_tile, k_block) FIXED 8192-byte slots at
  `(n_tile*num_blocks+k_block)*TILE_WEIGHT_BYTES` — a dense repack (no slot
  padding) reads garbage for k_block ≥ 1; (2) block scales: fixed 512-byte
  slots, first `tile_width*4` bytes = `scales[k_block, n_tile]`;
  (3) activations: `pack_int8_activation_tile_major` broadcast layout, and the
  descriptor `input_size` must be the FULL packed size (`ceil(K/64)*4096`),
  not M*K — otherwise the DMA under-copies and the bridge reads stale SRAM.
- **Activation scale folding**: the ring MMUL has no activation-scale path
  (tile_mmul always applies FP32 block scales), so the INT8 activation scale
  is folded into the weight block scales (`scales_eff = w_scales * a_s`).
  Golden uses the same folded scales → bit-exact.
- **Depthwise decomposition**: ONNX depthwise weights are `(C_out, 1, kH, kW)`
  with `groups == in_channels` — `groups == w.shape[1]` is FALSE; the
  importer's `type == "depthwise_conv"` tag is the reliable detector.
  Scheduled per input channel (K = kh*kw, N = 1), the same decomposition as
  the W3.4 golden. The block-diagonal single-GEMM alternative overflows the
  64 KB activation SRAM region for 5×5 depthwise with C ≥ 41 (K = 25*C).
- **Chain wiring**: conv/Gemm layers execute on the ring; non-conv ops
  (HardSwish/HardSigmoid/Relu/SE ReduceMean+Mul/residual Add/head
  ReduceMean+Reshape) are identical numpy on both paths and feed the next conv
  the same value — only GEMM layers are verifiable against the matmul golden.
  Real ONNX initializers (weights + bias) make it a genuine MobileNetV3 chain;
  bias is added host-side to the chained value (raw ring-vs-golden compare is
  bias-free). Residual Adds consume `getitem_*` tensors — wired via node
  input/output names, not last-output heuristics. The head `Concat` mixes a
  runtime Shape output with a constant initializer — lookup must fall back to
  init_map. `ReduceMean.keepdims` must be read by attribute NAME (attribute[0]
  is `noop_with_empty_axes`).
- **Degenerate zero-vs-zero layers (legitimate)**: with the random N(0,1)
  input, the first SE reduce conv (`node_conv2d_3` / fc2, and one more later)
  sees a ReLU that zeroes its input → both ring and golden outputs are exactly
  zero. cos of zero vectors is undefined (0.0 by convention here); the gate
  asserts bit-exactness for these and cos ≥ 0.99 for the other 50 layers.
  Not a modeling bug — data-dependent ReLU saturation.
- **Failure injection**: the weight address of one mid-chain pointwise conv
  (chosen deterministically at conv index 52//2 → `node_Conv_542`) is shifted
  +64 bytes into its own weight buffer → that layer's cos drops to 0.031
  while all other 53 layers stay bit-exact (downstream layers chain from the
  corrupted value in BOTH paths, so they still match their golden — exactly
  one layer diverges, which is the point).
- **Determinism**: two independent ring-chain runs produce identical per-layer
  outputs (asserted), pinning the doorbell/IRQ/tile-mmul path stability.
- Env notes: module-level skip when `assets/mobilenetv3_small.onnx` (or its
  `.data` external-data companion) is missing → "MobileNetV3 ONNX model not
  found". NPUFirmware DeprecationWarning ×5 per run — benign.
