# Func Model Verification Gap Report: BUG-RTL-SOC-008 (DESC_BASE / Command Ring Overlap)

> Date: 2026-08-21
> Audience: engineering team, future verification reviews
> Source: `.omo/notepads/phase10-rtl-verification/issues.md` §ISSUE-13D, `docs/bugs/bugs-soc-rtl.md` §BUG-RTL-SOC-008, `sim/spike_host.py`, `firmware/npu_firmware.c`, `docs/verification_methodology.md`, `docs/func_model_architecture.md`

## 1. Executive summary

BUG-RTL-SOC-008 was a DRAM address-space overlap: the descriptor pool (`DESC_BASE = 0x80001000`) sat inside the command ring's address range, so once a long run wrote enough ring entries, command entries overwrote live descriptors and the firmware silently executed corrupted operations. It escaped Func Model verification not because Func Model is unused, but because the Func Model path never exercises the physical ring layout that made the collision possible. The Python host writes ring entries starting at index 0 for every scheduled chain, short smoke tests keep ring offsets in the single digits, and no address-space contract exists between descriptor, command-ring, and completion-ring regions. This report recaps the bug, explains the specific gap, and proposes concrete modeling, scenario, assertion, trace, and alignment improvements so memory-layout defects surface at the Func Model stage instead of a 7.5 hour RTL segment run.

## 2. BUG-RTL-SOC-008 recap

**Symptom.** In the Ibex 9-layer segment run, L19/L20 checkpoints failed with `cos≈0.031` while L0/L10/L29/L30/L34 passed. The L0→L19 in-session probe reproduced it: after the segment boundary, L19 wave-1 outputs diverge from golden (`residual1 cos=0.031251`, `o_out nan`).

**Root cause.** `DESC_BASE = 0x80001000` maps to command-ring entry 128 (ring base `0x80000000`, 32 B per entry). Descriptors are 64 B, so descriptor `i` occupies ring entries `128+2i` and `128+2i+1`. The segment run's ring offset accumulates across layers (`state["offset"]` in `sim/rtl_soc_segment_run.py`); by L19 the firmware writes commands at ring entries 102-135, and entries 128-135 overlap descriptors 0-7. The descriptor is written first, then the command overwrites it, so the firmware reads a corrupted descriptor for the later waves of L19. Only layers reached after enough cumulative ring entries were corrupted, matching the observed L19/L20 failure.

**Fix.** `fa4ffec` moved `DESC_BASE` from `0x80001000` to `0x80010000`, free: above the 1024-entry command ring (`0x80000000-0x80007FFF`) and completion ring (`0x80008000-0x8000FFFF`), below `P10_ACT_BASE = 0x80020000`. The firmware does not hardcode `DESC_BASE`; it reads the descriptor address from each command entry, so a single Python constant change sufficed. `sim/rtl_soc_mmul_probe.py` was aligned to read `sh.DESC_BASE` instead of a hardcoded address. `e25031d` recorded the bug in the ledger and re-ran the todo-22 completeness check.

## 3. Current Func Model SoC verification scope

Func Model is already treated as early SoC verification, not a mere module golden. What it covers today:

- **Firmware emulation.** The Python `NPUFirmware`/`miniv.py` simulator consumes command-ring entries, reads descriptors, dispatches MMUL/SFU/Vector/DMA ops, and updates the doorbell, mirroring `firmware/npu_firmware.c`.
- **Spike ISA co-simulation.** The real RISC-V firmware ELF runs on Spike with an NPU MMIO device (`npu_device.cc`); MMIO writes from firmware go through the bridge to Func Model handlers, orchestrated by `sim/spike_host.py`.
- **Bit-exact golden reference.** Func Model output is the `$readmemh` golden for RTL comparison; FM-SOC-001..032 plus FM-SOC-10X compare RTL against it (the "model-as-spec" doctrine).
- **Per-layer cos_sim.** The segment run cross-checks Ibex RTL per-layer outputs against Spike/Func Model same-layer outputs (L9/L19/L29/L34) and a tolerance ladder at checkpoints (L0/L10/L20/L30/L35).

The gap is not that Func Model fails to model SoC behavior; it models engine numerics bit-exactly. It does not model the memory layout constraints the numerics depend on.

## 4. Why Func Model did not catch this bug

Four concrete reasons, each verifiable in the current code.

**4.1 Func Model does not model the physical command ring layout.**
In `sim/spike_host.py`, `write_cmd_entry()` computes `addr = FIRMWARE_RING_BASE + ring_index * CMD_ENTRY_SIZE`, and `schedule_chain()` passes `ring_index = i` for each op in the current chain, always starting from 0. The Python DRAM is a flat `bytearray`; there is no notion of "this address is ring entry N", no entry-size/ring-size accounting, no wrap-around state. Descriptors at `DESC_BASE + i * DESC_STRIDE` and command entries at `FIRMWARE_RING_BASE + i * CMD_ENTRY_SIZE` are two unrelated byte blobs that happen to collide. Func Model cannot detect that a command entry and a descriptor share a physical address; both writes succeed and the model "works".

**4.2 The Spike/MMIO-bridge path intercepts MMIO but not DRAM ring placement.**
Spike firmware writes go through the NPU MMIO device only for register-space accesses. Ring entries, descriptors, and tensors are ordinary DRAM loads/stores hitting the Python `bytearray` directly. The bridge never emulates the Verilog ring-buffer placement or the doorbell's pointer arithmetic against a real address space. Whatever the firmware reads back from a corrupted descriptor address is what Func Model returns, because the descriptor and the overwriting command were both "validly" written to the same bytes.

**4.3 DESC_BASE is a Python constant with no address-space overlap check.**
`DESC_BASE`, `FIRMWARE_RING_BASE`, and `P10_ACT_BASE` are independent module-level constants. Nothing verifies that `[DESC_BASE, DESC_BASE + max_descriptors*64)` is disjoint from the command ring, the completion ring, or the activation/weight regions. The post-fix check in ISSUE-13D ("verified against spike_host.py region constants") was done by hand during the fix, exactly the check that should have existed as a test or runtime assertion before any long run.

**4.4 Existing tests and smokes never push the ring offset into the collision zone.**
Every pre-existing consumer of the ring path is single-command or short-sequence:

- `sim/rtl_soc_runner.py` FM-SOC-001..008 uses `RING_SIZE = 32` and never reaches entry 128.
- Standalone helpers and perf scripts (`spike_host.py` mmul smoke at ring entry 0, `p10_fm3_measure.py`, `perf_tests.py`) issue one command.
- The Func Model forward path (`spike_host.py` `forward` mode) resets `NPU_HEAD`/`HOST_TAIL` per layer and reschedules each layer from ring entry 0, so no cumulative ring offset exists there.
- Only the RTL segment run (`sim/rtl_soc_segment_run.py`) keeps a persistent `ring_offset` across layers, reaching entries 102-135 at L19. That is the single scenario where the bug manifests, and it is an RTL gate, not a Func Model gate.

In short: the bug needs a persistent ring offset crossing entry 128 and an address-space model to be observable. Func Model had neither.

## 5. Methodological improvement proposals

Categorized, actionable items. The goal: a memory-layout defect must fail fast at Func Model speed (seconds), not after a 7.5 hour VCS run.

**Modeling**

- M1. Add a `CommandRing` model to Func Model / the bridge that tracks ring base, entry size, ring size, head, tail, and cumulative offset, matching `firmware/npu_firmware.c` (`RING_ENTRIES=1024`, 32 B entries, completion ring immediately after). Both the Python firmware emulation and the Spike path should drive commands through it rather than raw `host_write_data` calls. **（已落地为 fm-hardening-phase10 todo 3）**
- M2. Add an `AddressSpaceContract` module that owns the DRAM region table (command ring, completion ring, descriptor pool, activations, weights, outputs) and answers "does region A overlap region B". Check it on every run start, not only after a bug is found. **（已落地为 fm-hardening-phase10 todo 1）**

**Scenario**

- S1. Make long-sequence / segment-run command generation a standard Func Model gate, not an RTL-only gate. A Func Model run scheduling many layers back-to-back with a persistent ring offset, at Python speed, would have reached entry 128 in seconds. **（已落地为 fm-hardening-phase10 todo 5）**
- S2. Add a ring-stress scenario that deliberately drives cumulative ring offsets across the full ring size, including wrap-around and the boundary region (entries 128+), as a first-class regression case. **（已落地为 fm-hardening-phase10 todo 4）**

**Assertion**

- A1. Runtime assertions when scheduling commands in Func Model: descriptor base region disjoint from ring region; command count within ring size; wrap-around detection with an explicit warning or failure. **（已落地为 fm-hardening-phase10 todo 2）**
- A2. On descriptor write, assert `DESC_BASE + i * DESC_STRIDE` does not fall inside `[RING_BASE, RING_BASE + RING_SIZE * 32)`. This check alone would have flagged BUG-RTL-SOC-008 at the first multi-descriptor schedule. **（已落地为 fm-hardening-phase10 todo 2）**

**Trace**

- T1. Generate a `firmware_memory_contract.json` from Func Model runs: ring base/size, descriptor range used, completion ring range, and observed max ring offset. Persist it per scenario so RTL firmware traces can be diffed against it. **（deferred，未纳入本计划）**
- T2. In the RTL segment run, dump the same contract from the firmware's actual DRAM usage and compare it to the Func Model contract before the numeric ladder runs. A mismatch here is cheaper to debug than a `cos≈0.031` at L19. **（deferred，未纳入本计划）**

**Alignment**

- AL1. Ensure the Func Model command scheduler matches `firmware/npu_firmware.c` ring management exactly: same ring size, same wrap-around, same completion placement. Today the Python forward path resets the ring per layer while the RTL segment path accumulates, an inconsistency that hid the bug. **（deferred，未纳入本计划）**
- AL2. Where a constant exists in both Python and C (ring base, entry size, descriptor stride), add a cross-language consistency test. **（已落地为 fm-hardening-phase10 todo 9）**

## 6. Short-term vs long-term action items

> 状态注记（2026-08-23）：本节 action items 已按 §5 提案的状态处置，不再单独立项：ST1（M2/A2）→ fm-hardening-phase10 todo 1/2，ST2（S2）→ todo 4，ST3 即本文档与验证方法论文档；LT1（M1）→ todo 3，LT4（S1 类）→ todo 5，LT3 的 AL2 部分 → todo 9；LT2（T1/T2）与 LT3 的 AL1 部分 deferred，未纳入本计划。

**Short-term (before the next segment run)**

- ST1. Land the `AddressSpaceContract` overlap check (M2) and the descriptor-vs-ring assertion (A2) as a pytest that fails on the old `DESC_BASE` value. Engineering team.
- ST2. Add the ring-stress Func Model scenario (S2) that schedules enough commands to cross ring entry 128 with wrap-around. Engineering team.
- ST3. Record this gap in `docs/verification_methodology.md` or the phase-10 notepad so future reviews know the ring layout is now a covered contract. Engineering team.

**Long-term**

- LT1. Implement the `CommandRing` model (M1) and route the Spike path through it, so firmware emulation and co-simulation share one ring implementation.
- LT2. Generate and compare `firmware_memory_contract.json` (T1/T2) as a standard pre-ladder gate in the RTL segment run.
- LT3. Enforce the Func Model↔C ring-management alignment check (AL1/AL2) in CI.
- LT4. Treat persistent-offset long runs as a Func Model regression class with the same frequency as single-command smokes.

## 7. Conclusion

BUG-RTL-SOC-008 was a layout bug, and layout bugs are invisible to a model that does not model layout. Func Model already proves numerics bit-exactly and already plays the early-SoC-verification role; what it lacks is the address-space layer between the host command protocol and the engine golden. The fix was one constant, but the guard should be a permanent contract: region ownership, ring geometry, and overlap checks, exercised by long-sequence scenarios at Func Model speed. With M1/M2 and S1/S2 in place, a regression of this class fails in seconds during Func Model verification, before any RTL cycle is spent. The single most important follow-up is A2 plus S2: schedule many descriptors at a persistent ring offset and assert descriptor addresses stay out of the ring region, the exact scenario that exposed the bug.
