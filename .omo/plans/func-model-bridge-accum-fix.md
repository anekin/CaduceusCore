# func-model-bridge-accum-fix - Work Plan

## TL;DR (For humans)

**What you'll get:** 修复 BUG-SOC-FM-005 的最后一个遗留子问题——Bridge 路径 `_run_mxu_compute()` 跨 K-tile 累加停滞。

**已知事实:** Spike 插件 (`spike_src/plugins/npu_mmio_plugin.cc`) 是纯 MMIO 转发代理——将寄存器读写通过 Unix socket 转发给 `MMIOBridge.handle()`，自身不参与 MXU 计算。唯一的 MXU 计算在 Python 侧的 `_run_mxu_compute()` 中。因此 stale read 的根因不在 Spike 侧，而在 bridge 代码路径内部或 bridge 与 Spike 内存空间的交互中。

**方法:** 调查优先 (T0)，确认根因后再制定修复方案 (T1)。

**Effort:** ~1 天。T0 调查 → 根因确认 → 修复 → 回归 → 证据。

---

## Known constraints
1. All work on `main` branch.
2. Bug track lives in `docs/bugs/bugs-soc-func-model.md` — must be updated after fix.
3. EDA server sz0001 (192.168.0.11) required for Spike-related verification.
4. Firmware changes require `make -C firmware` rebuild.
5. Use `scripts/run_fm_env.sh` for sz0001 Python environment.
6. **Do NOT modify `rtl/` or Spike C++ source (`spike_src/*.c/cpp/cc/h/hpp`).** Read-only inspection OK.
7. **MMIO bridge must NOT change behavior for SRAM-direct path** (lines 213-244, used by FuncModel/RISCVMini).
8. **Direct GoldenMXU reference path must remain unaffected.**

---

## Root Cause Analysis

### Evidence (from T2/T6)

Firmware iterates per-K-tile: DMA act+wgt into SRAM → write MXU MMIO regs → read STATUS → bridge intercepts → `_handle_mxu()` → `_run_mxu_compute()`.

Bridge trace (`BBRIDGE_TRACE=1`, n_tile=0, K=1536 → 24 tiles):

| k_block | accumulate | Output[:4] | Assessment |
|---------|:----------:|------------|-----------:|
| 0 | False | [-5.89, 4.39, -3.70, 1.60] | First tile — no accumulation |
| 1 | True  | [-4.11, 2.01, -3.94, 2.84] | Accumulated tile1 onto tile0 ✓ |
| 2 | True  | [-4.11, 2.01, -3.94, 2.84] | **STALE** — identical to k_block=1 |
| 3..23 | True  | [-4.11, 2.01, -3.94, 2.84] | **STALE** — 24 tiles produce same output as 2 tiles |

Direct GoldenMXU simulation with same reordered data: max_diff=9.2e-5 (PASS), confirming Python computation logic is correct.

### Spike Plugin Architecture (verified)

The Spike plugin (`npu_mmio_plugin.cc`) is a pure MMIO forwarding proxy:
- Runs as a shared library loaded by Spike
- Opens a Unix socket to the Python bridge process
- On RISC-V store/load to MMIO address space (0x4000_0000–0x4000_6FFF):
  - Write: serializes addr+data and sends to Python via socket
  - Read: sends addr to Python, waits for response, returns to Spike CPU
- **No MXU computation, no accumulator, no SRAM access logic**

All MMIO reads/writes arrive at `MMIOBridge.handle()` which dispatches to `_handle_mxu()` → `_run_mxu_compute()`.

### Key observation: two separate memory spaces

```
┌─────────────────────────────────┐    ┌───────────────────────────────────┐
│ Spike Simulator (C++)           │    │ Python Bridge Process             │
│                                 │    │                                   │
│  Firmware:                      │    │  MMIOBridge:                      │
│    DMA → writes to Spike SRAM   │    │    _run_mxu_compute()             │
│    MMIO reg write → forwarded ──┼────▶    reads from xbar.sram/dram     │
│    MMIO STATUS read → forwarded─┼────▶    computes in Python             │
│                                 │    │    writes to xbar.sram/dram       │
│  Spike's SRAM (private)         │    │                                   │
│  Spike's DRAM (shared via hex)  │◀───│  CrossbarModel.sram (separate!)   │
│                                 │    │  CrossbarModel.dram (shared?)     │
└─────────────────────────────────┘    └───────────────────────────────────┘
```

**DRAM** is potentially shared (host writes to FuncModel DRAM, Spike loads DRAM from same hex file). **SRAM** is NOT shared — Spike has its own internal SRAM that firmware DMA writes to; the bridge reads from CrossbarModel.sram (a separate Python bytearray).

### Candidate Root Causes

The investigation will test these hypotheses in priority order:

| # | Hypothesis | What would explain stale read? | How to test |
|---|-----------|-------------------------------|------------|
| **H1** | **Bridge reads from crossbar SRAM, but firmware DMA writes to Spike's own SRAM (different memory)**. After k_block=0, the bridge writes accumulated output to `xbar.sram`, but on k_block=1 the firmware DMA overwrites Spike's SRAM copy with fresh tile data. The bridge reads from `xbar.sram` which is NOT updated by Spike DMA. | k_block=1 reads stale act/wgt (from k_block=0 tile), producing same-ish computation as k_block=0, and accumulation gives result close to k_block=1. k_block=2 reads same stale data → identical result. | Log raw_i/raw_w addresses AND i_abs/w_abs translated addresses AND act/wgt first bytes per k_block. Confirm: (a) raw_i/raw_w change each tile (firmware is updating pointers), BUT (b) act/wgt bytes read from crossbar are identical across k_block≥1. Both must hold to confirm H1. |
| **H2** | **`_to_crossbar_addr(raw_o)` translates to different physical addresses per k_block**. Firmware changes output address each tile; bridge writes to address A on tile1 but reads from address B on tile2. | Accumulation read misses the previous write, gets zeros or unrelated data. | Log `o_abs` value per k_block. If it changes, H2 confirmed. |
| **H3** | **Scale address (`raw_s`/`s_abs`) is wrong for k_block≥2**. Firmware uses same scale address but DMA overwrites it with tile0's scale; bridge reads wrong scale → computation wrong for all tiles ≥1. | k_block=1 and k_block=2 both compute with same (wrong) scales → identical output. | Log scales per k_block. If scales identical across k_block≥1 (despite firmware having different scale data for each tile), H3 confirmed. |
| **H4** | **`_to_crossbar_addr()` maps the output address to DRAM, but the firmware DMAs within SRAM**. Crossbar `_decode()` routes SRAM and DRAM to different bytearrays. If data is in one space but bridge reads from the other → wrong data. | Bridge writes to DRAM but firmware DMA writes to SRAM; reads hit different memory region → stale/zero. | Inspect `_to_crossbar_addr()` to determine address space. Check whether the firmware's MMIO register values for I/W/O addresses fall in SRAM range (0x2000_0000) or DRAM range (0x8000_0000). |
| **H5** | **Firmware or bridge fails to update `raw_i`/`raw_w` pointers for k_block≥2**. The firmware intends to iterate K-tiles but the MMIO register values for input/weight addresses do not advance past k_block=1. This directly explains why k_block≥2 produces identical output to k_block=1: the bridge reads the same act/wgt data, computes the same result, and accumulates on top of the same base → identical output. | k_block=1 works because it's the first time the "stuck" pointers are used with new DMA'd data. k_block≥2 reads the same stale register values → identical computation. | Log raw_i/raw_w values from MMIO register reads per k_block (intercept in `_handle_mxu` before calling `_run_mxu_compute`). If raw_i and raw_w are IDENTICAL for k_block≥2, H5 confirmed (firmware pointer stagnation). |
| **H6** | **Firmware activation-address miscalculation** (discovered during T0). In `firmware/npu_firmware.c` `dispatch_cmd()`, the per-K-tile activation offset is computed as `act_offset = act_sram + k_start * 64`. For `M=1`, `k_start = k_block * 64`, so each tile is offset by `k_block * 4096` bytes, but the host supplies a contiguous `M*K` activation. Thus `k_block=0` reads valid data, `k_block=1` reads the wrong SRAM region, and `k_block≥2` reads zeros — freezing accumulation. | Correct offset should be `act_sram + k_start * desc.M` (bytes per K index). For `M=1` this is `k_block * 64`, matching the contiguous activation layout. | Confirmed by T0 logs: `act_head` is zero for `k_block≥2` while `raw_i` advances, and all other hypotheses are eliminated. |

### T0 exit criteria

After T0, exactly ONE of these outcomes:

- **OUTCOME A — root cause confirmed**: One hypothesis (H1-H6) matches observed data logs. Proceed to T1 with targeted fix.
- **OUTCOME B — all hypotheses eliminated**: None of H1-H6 explain the symptom. **STOP AND REPLAN.** Do NOT proceed to T1 — the stale read has an unrecognized root cause that needs a new analysis.

---

## Task decomposition

### [x] T0. Investigation — Identify root cause (GATE: must PASS before T1)

- **Why:** The stale read has multiple candidate causes; fix cannot be designed without knowing which one.
- **Plan:**
  1. **Add diagnostic logging** to `_run_mxu_compute()` (crossbar path only):
     - Before computation: print k_block index, `o_abs` address, `i_abs` address, `w_abs` address, `s_abs` address, first 8 bytes of act data, first 8 bytes of wgt data.
     - During accumulation: print first 4 values read from `existing` (existing output).
     - After computation: print first 4 values of `result` and whether it was written.
     - Guard with `BBRIDGE_TRACE >= 2` so existing behavior is unchanged at TRACE=0/1.
  2. **Run isolated small test**: `spike_host.py --mode mmul_smoke` with K=128 (2 K-tiles), N=128, M=1. Verify the same stale-read pattern appears with minimal tiles.
  3. **Test H1 (SRAM divergence)**: Log raw_i/raw_w addresses AND i_abs/w_abs AND act/wgt first bytes per k_block. Confirm: (a) raw_i/raw_w change each tile, AND (b) act/wgt bytes are identical across k_block≥1. Both (a) and (b) must hold → H1 confirmed. If only (a) holds but (b) does not (act/wgt bytes differ) → H1 eliminated.
  4. **Test H2 (address change)**: Log `o_abs` for every k_block. If `o_abs` changes across tiles → H2 confirmed. If stable → H2 eliminated.
  5. **Test H3 (scale stale)**: Log scale bytes for k_block≥0. If scales match between tile0 and tile1+ → H3 confirmed.
  6. **Test H4 (address space mismatch)**: Inspect `_to_crossbar_addr()` definition. Check whether raw_i/w/o/s map to SRAM (0x2000_0000) or DRAM (0x8000_0000). If output is in DRAM but data is in SRAM → H4 confirmed.
  7. **Test H5 (raw_i/raw_w pointer stagnation)**: Log raw_i/raw_w values from MMIO register reads per k_block (intercept in `_handle_mxu` before calling `_run_mxu_compute`). If raw_i and raw_w are IDENTICAL for k_block≥2 → H5 confirmed (firmware/plugin pointer not advancing). If they change → H5 eliminated.
  8. **Crossbar DRAM sharing check**: If addresses are in DRAM space, verify that `xbar.dram` is the same Python object as Spike's DRAM image. If not → document as contributing factor.
- **Acceptance:** EXACTLY ONE of:
  - **PASS → T1**: Diagnostic log confirms ONE hypothesis (H1-H6). Evidence documents which hypothesis, with log excerpts. `.omo/evidence/bridge-accum-t0-investigation.txt` contains `evidence.verdict: pass` and `root_cause_confirmed: <H#>`. T0 confirmed H6 (firmware activation-address miscalculation), so T1 is the H6 firmware fix.
  - **STOP_AND_REPLAN**: All hypotheses eliminated. Notify orchestrator, document findings, request replan. Evidence file contains `evidence.verdict: replan_required`.
- **Evidence:** `.omo/evidence/bridge-accum-t0-investigation.txt`
- **Commit:** N (diagnostic logging is temporary)

### [x] T1. Implement fix (depends on T0 outcome; DO NOT execute before T0 PASS)

- **Why:** Apply targeted fix for the confirmed root cause.
- **Plan (branched by T0 outcome):**

  **If H1 (SRAM divergence) confirmed — Fix: Route reads through shared memory**
  
  The bridge reads act/wgt/output from `xbar.sram` but firmware DMA went to Spike's private SRAM. Fix options:
  - **T1-H1-A: DISABLED by Constraint #6.** Adding a `peek_sram` RPC to the plugin protocol requires modifying `spike_src/plugins/npu_mmio_plugin.cc` (C++). Not allowed under current scope.
  - **T1-H1-B:** Use `_translate_addr()` to convert raw addresses to offsets into the Spike hex-loaded SRAM image, then read/write that bytearray directly. Requires Spike's SRAM to be exported/accessible to Python — verify during T0.
  - **T1-H1-C (preferred if precondition met):** Modify `_run_mxu_compute` to use the SRAM-direct path (lines 213-244) instead of the crossbar path when running under Spike. The SRAM-direct path reads from `self.modules['sram']`. **Precondition (verify in T0):** `self.modules['sram']` must be the same Python bytearray object as Spike's SRAM backing store. If they are different bytearrays, T1-H1-C is also ineffective and T1-H1-B should be used.
  
  **If H2 (address change) confirmed — Fix: Stabilize o_abs across tiles**
  
  The firmware changes `raw_o` per tile. Fix: In `_handle_mxu()`, when accumulate=True, override `raw_o` (or `o_abs`) to always use the SAME output address as the first tile.
  ```python
  if accumulate and self._mxu_first_o_abs is not None:
      o_abs = self._mxu_first_o_abs  # force same address
  else:
      self._mxu_first_o_abs = o_abs
  ```
  
  **If H3 (scale stale) confirmed — Fix: Cache scales on first tile**
  
  Read scales once on k_block=0, reuse for all subsequent tiles. Or: force bridge to use the scales from the firmware's scale address register, not from SRAM.

  **If H4 (address space mismatch) confirmed — Fix: Correct address routing**
  
  If `_to_crossbar_addr()` maps to DRAM but data is in SRAM: fix the mapping. If maps to SRAM but crossbar's SRAM diverges from Spike's: fix the crossbar initialization to share memory.

  **If H5 (raw_i/raw_w pointer stagnation) confirmed — Fix: Correct pointer advancement**
  
  The firmware writes the same `raw_i`/`raw_w` values to MXU registers for k_block≥2. Fix: identify where the firmware or plugin fails to advance the pointer.
  - If firmware bug: fix in `firmware/npu_firmware.c` `dispatch_cmd()` — the tile loop should increment `raw_i`/`raw_w` per k_block.
  - If plugin forwarding bug: the plugin socket may be dropping or caching register writes. Inspect plugin socket protocol.
  - If bridge caching: the bridge may cache the first register write and return the same value on subsequent STATUS reads. Check `_handle_mxu` and `self._status` caching.

  **If H6 (firmware activation-address miscalculation) confirmed — Fix firmware offset**
  
  In `firmware/npu_firmware.c` `dispatch_cmd()`, change:
  ```c
  uint32_t act_offset = act_sram + k_start * 64;
  ```
  to:
  ```c
  uint32_t act_offset = act_sram + k_start * desc.M;
  ```
  This makes the per-K-tile activation stride equal to `M` bytes per K index, matching the contiguous `M*K` activation DMA'd by the host. Rebuild firmware with `make -C firmware`. Verify with `BBRIDGE_TRACE=2` that `act_head` is non-zero for all k_blocks and the accumulated result changes every tile.

- **Acceptance:** `task-1a-v3-spike-mmul-smoke` shows:
  - k_block=0 through k_block=23 output values ALL DIFFERENT (no stale repeats)
  - max_diff between bridge path and GoldenMXU reference is documented and converged (not limited to first 2 tiles)
  - L0 Q_proj max_diff significantly reduced from baseline (426 → target ≤ 10)
- **Evidence:** `.omo/evidence/bridge-accum-t1-fix.txt`
- **Commit:** Y — actual message depends on which root cause; template: `fix(bridge-accum): <brief description of fix>`

### [x] T2. Full regression — Re-run all Spike tasks on sz0001

- **Why:** After the fix, re-verify all three Spike tasks. Fix must not regress chain mode or forward pass.
- **Plan:**
  1. `python3 scripts/run_func_model_signoff.py run --case task-1a-v3-spike-mmul-smoke`
  2. `python3 scripts/run_func_model_signoff.py run --case task-1b-v3-spike-chain`
  3. `python3 scripts/run_func_model_signoff.py run --case task-1c-v3-spike-forward` (with `--token-ids`)
  4. Verify no regressions.
- **Acceptance:**
  - task-1a (mmul_smoke): max_diff converged, all K-tiles accumulate correctly.
  - task-1b (chain): PASS, NPU_HEAD=3, all 3 ops PASS.
  - task-1c (forward): runs without `ModuleNotFoundError`, deterministic=YES.
  - `validate --v3`: no newly introduced failures vs T6 baseline.
- **Evidence:** `.omo/evidence/bridge-accum-t2-regression.txt`
- **Commit:** N (evidence only)

### [x] T3. Update bug tracker

- **Why:** BUG-SOC-FM-005 status must reflect fix completion.
- **Plan:**
  1. Edit `docs/bugs/bugs-soc-func-model.md` BUG-SOC-FM-005 section:
     - Change Status from "Partial fix implemented" → "Fixed (T2 weight pre-tiling + bridge accumulation fix)".
     - Update root cause table: row 3 (Bridge accumulation) → **Fixed** with commit reference.
     - Update Stats: Open=4→3 (or recalculate based on actual open count).
  2. If any residual gap remains after fix (e.g., bridge path still has numerical gap vs GoldenMXU for a documented reason), add a "Known Limitation" note.
- **Acceptance:** Bug tracker accurately reflects post-fix state.
- **Evidence:** Git diff of `docs/bugs/bugs-soc-func-model.md`
- **Commit:** Y — `docs(bugs): update FM-005 status after bridge accumulation fix`

---

## Execution waves

| Wave | Tasks | Depends on | Notes |
|------|-------|------------|-------|
| Wave 0 | T0 (investigation) | — | **GATE**: must PASS before Wave 1. If T0 returns STOP_AND_REPLAN, halt and notify. |
| Wave 1 | T1 (fix) | T0=PASS | Fix strategy depends on which root cause T0 confirms |
| Wave 2 | T2 (regression) + T3 (bug tracker) | T1 | Can run in parallel |

---

## Final verification wave

- [x] F1. Code quality: compileall on changed Python files; firmware compiles zero errors; no RTL/Spike C++ source modifications.
  Acceptance: `.omo/evidence/bridge-accum-final-code-quality.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/bridge-accum-final-code-quality.txt`
  Commit: N

- [x] F2. Improved MMUL golden comparison on sz0001:
  (1) task-1a-v3-spike-mmul-smoke shows ALL K-tiles accumulate (no stale repeats after k_block=1)
  (2) L0 Q_proj max_diff ≤ 10 (was 426) or documented residual gap with clear explanation
  (3) GoldenMXU direct path (already PASS at 9.2e-5) is unchanged
  Acceptance: `.omo/evidence/bridge-accum-final-real-qa.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/bridge-accum-final-real-qa.txt`
  Commit: N

- [x] F3. Scope fidelity: changed files limited to `sim/mmio_bridge.py` (primary), `docs/bugs/`, `.omo/`. Reject RTL/Spike C++ changes.
  Acceptance: `.omo/evidence/bridge-accum-final-scope-fidelity.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/bridge-accum-final-scope-fidelity.txt`
  Commit: N

---

## Commit strategy

| Task | Commit | Message |
|------|--------|---------|
| T1 | Y | Depends on root cause — e.g. `fix(bridge-accum): sync Spike SRAM reads through plugin MMIO proxy` |
| T3 | Y | `docs(bugs): update FM-005 status after bridge accumulation fix` |
| T0, T2 | N | Evidence only |
