# Phase 10 RTL Verification — Experience Guide

> Extracted from `phase10-rtl-verification` (work ID `4b4b4835`).  
> Use this as a checklist and methodology reference for future RTL debug / long-run verification phases.

---

## How to use this guide

Each lesson follows `Event → Consequence → Principle → Actionable advice`.  
Checklists are ordered by **return on effort**: cheap, high-yield checks first.

---

## L1 — Prove the path, not just the block

**Event:** DMA CH1 readback returned zeros in the PERF path but the FM-SOC DMA roundtrip was PASS.  
**Consequence:** Time was spent instrumenting the DMA controller before realizing the issue was firmware output interleave, already fixed by another commit.  
**Principle:** When two flows share the same RTL block but differ in behavior, compare their register configurations and data paths first.  
**Actionable advice:**
- Before changing RTL, capture CH0/CH1 `src/dst/size/ctrl` for both the PASS and FAIL paths.
- Print the first 32 bytes of SRAM source and DRAM destination.
- If the controller registers are identical and one path works, the bug is upstream (firmware/data layout) or downstream (golden comparison), not in the DMA FSM.

---

## L2 — One `cos_sim` failure can hide multiple bugs

**Event:** PERF-06 M=32 stayed at `cos_sim≈0.05` even after the firmware tile-major offset was corrected.  
**Consequence:** A second bug — output DMA row interleave overlapping n_tile regions — was only found after the first fix.  
**Principle:** Fix the first-order bug, re-run the full suite cleanly, and only then judge closure.  
**Actionable advice:**
- After each RTL/firmware fix, run the smallest case that previously failed plus one adjacent case.
- Inspect per-row or per-tile residuals, not only the aggregate `cos_sim`.
- If rows 0 are perfect and rows 1..M-1 are wrong, suspect store-region overlap or output DMA interleave.

---

## L3 — Long-run corruption is usually state, not logic

**Event:** Ibex segment run produced `cos≈0.031` at L19 after a boundary; L19 standalone passed.  
**Consequence:** Three rounds of full segment runs (each ~7.5 h) were consumed before isolating stale SRAM, then DESC_BASE overlap.  
**Principle:** If a layer passes in isolation but fails after prior layers, audit state left behind by the prior layers before re-running hours of simulation.  
**Actionable advice:**
- At every segment boundary, snapshot DRAM, SRAM, and key engine-wrapper staging registers.
- Test boundary reset by running layer N standalone vs layer N after layer N-1 with identical inputs.
- If standalone passes and chained fails, clear SRAM and rerun one segment before suspecting logic.

---

## L4 — Address-space layout is a first-class correctness contract

**Event:** `DESC_BASE=0x80001000` mapped into command-ring entry 128; 64 B descriptors collided with 32 B ring entries once the ring offset exceeded 128.  
**Consequence:** A single constant caused a 7.5 h run to fail; the bug was invisible to Func Model because Python uses a flat `bytearray`.  
**Principle:** Ring buffers, descriptor pools, and data regions must have a verified, disjoint address-space contract.  
**Actionable advice:**
- Add an `AddressSpaceContract` check that asserts `[DESC_BASE, DESC_BASE + max_descriptors*DESC_STRIDE)` does not overlap `[RING_BASE, RING_BASE + RING_SIZE*ENTRY_SIZE)`.
- Add a ring-stress Func Model scenario that accumulates offsets across the full ring size (seconds to run).
- Dump a `firmware_memory_contract.json` from Func Model and diff it against RTL firmware traces before long runs.

---

## L5 — Make long runs observable

**Event:** The first Ibex segment run was killed after 94 minutes of silence because output was buffered and no progress file existed.  
**Consequence:** A likely-good run was lost; the fix required adding progress plumbing and bulk preload.  
**Principle:** A long-running job without incremental progress is indistinguishable from a hang.  
**Actionable advice:**
- Emit a progress line at every ≥10% milestone and after every long phase (preload, compute, checkpoint save).
- Write progress to a file that is flushed immediately, not only to stdout/tee.
- Save checkpoints incrementally (e.g., `ph10-36layer-ibex-checkpoints.npz` after every layer) so a crash can be resumed.

---

## L6 — Calibrate the model constant, not the derived metric

**Event:** Func Model reported `weight_streaming_overlap_ratio=0.98` while RTL measured `0.00`.  
**Consequence:** Early confusion about whether a stored knob existed delayed the fix.  
**Principle:** Derived metrics should match by adjusting the underlying model assumptions, not by inventing a new knob.  
**Actionable advice:**
- List the actual parameters that feed the derived metric (e.g., `mxu.double_buffer`, `broadcast_sync`, `_accumulate_reg`, `bw_bytes_per_cycle`).
- Search the codebase for the metric name; if it is never assigned, it is computed.
- Run a small sensitivity grid over the real parameters, then pick the combination that minimizes `|RTL - FM|`.

---

## L7 — Final gates must be allowed to fail and be fixed

**Event:** F1, F2, F3, and F4 each failed on the first attempt for legitimate reasons (missing evidence, outdated whitelist, test-format drift, hardcoded debug pattern).  
**Consequence:** ~half a day was spent re-running gates, but the final closure is trustworthy.  
**Principle:** A gate that never fails is either too weak or not being used honestly.  
**Actionable advice:**
- Write the gate script to fail loudly and produce a concrete reason, not just `PASS/FAIL`.
- When a gate fails, decide whether the gate or the code is wrong before overriding it.
- Update gate whitelists with authorizing issue/commit references so the deviation is transparent.

---

## L8 — Distinguish toolchain artifacts from RTL bugs

**Event:** `ph9-36layer-checkpoint.txt` reported `FM-SOC-001: FAIL, cycles=0, error=unknown`.  
**Consequence:** A full simulation was almost launched to debug a case that was already passing.  
**Principle:** Status-extraction logic in runner scripts is itself code and can have bugs.  
**Actionable advice:**
- Reproduce any FAIL in isolation with the actual runner before declaring an RTL bug.
- Check pass-detection strings against the real runner stdout (e.g., `FAIL=0` vs `FAIL: 0`).
- Verify cycle-count parsing against the actual log format.

---

## L9 — Debug probes must compare against hardware-expected output, not only semantic golden

**Event:** L0L19 intermediate MMUL readbacks showed low `cos_sim` against FP32 golden even though final `l_out` matched.  
**Consequence:** Time was spent suspecting readback corruption until comparison targets were aligned with descriptor layout and dequantized hardware output.  
**Principle:** Intermediate probe values must use the same layout, dtype, and scale as the hardware path consumes them.  **Actionable advice:**
- For MMUL readbacks, compute expected values with the same weight orientation and per-block scales the descriptor uses.
- Read SFU outputs as `float16` if the hardware writes FP16.
- Use separate staging addresses for consumer-op inputs so golden staging does not overwrite producer outputs in DRAM.

---

## Checklists

### Before changing any RTL/firmware for a `cos_sim` failure

- [ ] Reproduce the failure in isolation with one exact command.
- [ ] Capture per-row / per-tile residuals to see if the error is structural.
- [ ] Add read-only probes for control signals (`CTRL[2]`, `mac_reset_acc`, state FSM).
- [ ] Run the smallest passing control case (e.g., M=1) side-by-side with the failing case.
- [ ] Document the three most likely root causes and one falsification experiment per cause.
- [ ] If the bug could be firmware, confirm with a probe before editing RTL.

### Before launching a >1 h VCS run

- [ ] Confirm ring / descriptor / completion / data regions do not overlap.
- [ ] Confirm SRAM budget for the largest layer shape.
- [ ] Add progress logging flushed to a file every ~10%.
- [ ] Enable incremental checkpoint saves.
- [ ] Run the smallest representative case end-to-end first.
- [ ] Verify no other `simv` process is using the license on the target host.

### When a layer passes standalone but fails in a chain

- [ ] Compare inputs at the boundary bit-exactly.
- [ ] Clear SRAM and rerun the segment.
- [ ] Clear engine-wrapper internal staging if accessible.
- [ ] Audit DRAM addresses for ring-descriptor overlap.
- [ ] Check that segment inputs are from the same engine source as the chain (not injected from a different simulator).

### After a fix that touches store-out / scale / activation layout

- [ ] Re-run the module-level regression.
- [ ] Re-run FM-SOC cases that exercise the changed engine.
- [ ] Re-run PERF cases, not only the original failing case.
- [ ] Check Python test fixtures that synthesize scale/activation buffers for format mismatches (FP16 vs FP32, layout order).
- [ ] Update golden reference generation if the bit-exact semantics changed.

### Final-wave readiness

- [ ] All `task-N` evidence files exist and contain `PASS` (or the plan-allowed `WAIVED`/`BLOCKED-NETWORK`).
- [ ] `git diff` of changed files is consistent with the plan's scope whitelist.
- [ ] No new `TODO/FIXME/HACK/XXX` residues on added lines.
- [ ] No suspicious hardcoded debug values on added lines.
- [ ] Pytest delta vs baseline is zero new failures/errors.
- [ ] At least one key evidence can be independently reproduced from a clean state.

---

## Methodology: long-run RTL debug loop

1. **Reproduce** — one minimal command, one exact commit.
2. **Probe** — add read-only instrumentation; do not edit logic yet.
3. **Falsify** — run a mini-model or isolated case that disproves the leading hypothesis.
4. **Fix** — edit the smallest layer (firmware > test > RTL) that the probe implicates.
5. **Causal gate** — re-run the failing case and one neighbor; confirm no regression.
6. **Full regression** — run the relevant regression surface.
7. **Independent QA** — reproduce the key evidence from a fresh checkout/build.
8. **Document** — evidence file, bug ledger entry, and notepad note for the next phase.

---

## Short-term follow-ups

- Add an `AddressSpaceContract` pytest that fails on the old `DESC_BASE=0x80001000` value.
- Add a Func Model ring-stress scenario that accumulates offsets across ≥128 ring entries.
- Commit the three remaining untracked debug probes (`rtl_soc_l19_full.py`, `rtl_soc_l19_probe.py`, `rtl_soc_state_probe.py`) or remove them before branch close.
- Add a nightly/CI check that `dram_range_ok` never rejects a command in the standard regression.
