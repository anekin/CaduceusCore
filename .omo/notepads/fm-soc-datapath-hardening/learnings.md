# fm-soc-datapath-hardening — Learnings

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
