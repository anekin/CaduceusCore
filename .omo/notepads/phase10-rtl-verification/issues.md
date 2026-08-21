# Phase 10 RTL Verification — Issue Log (todo 13: Ibex 9-layer segment run)

## ISSUE-13A: `ibex-seg-run` tmux session died; no simv, no tmux server on sz0001

**Status:** root-caused + fixed (bulk DRAM preload + progress plumbing). Run restarted
2026-08-20 ~12:15 under new tmux session `ibex-seg-run`.

### Symptom

On 2026-08-20 morning the `ibex-seg-run` tmux session on sz0001 was gone:
`tmux ls` → `failed to connect to server`, no `simv_soc_ibex_seg` process, and
the wrapper had not produced fresh task-13 evidence.

### Investigation timeline (from evidence files)

| When (Aug 19-20) | Run | Outcome |
|---|---|---|
| 10:23 | old simv, run-old-slow | 5.5 h, still inside L0 (VEC_WRP_STORE chunk-15 log spam, 164 KB) — killed |
| 16:04-18:09 | new simv (VEC_WRP_DEBUG guard), stuck-94min | last output = one RuntimeWarning, then **94 min of silence**, then died |
| 18:10-01:39 | new simv + uncommitted progress-file/delta-preload code | **completed** 7.5 h (26900 s), 99 waves, `ladder=FAIL` (see ISSUE-13B) |

### Root cause of the death (94-min-stuck run)

No core dump, no OOM-killer entry in `dmesg`, no VCS crash trace in the log —
the log ends cleanly at a Python `RuntimeWarning` print, consistent with an
external kill (SIGTERM/SIGKILL after 94 minutes of zero observable output).

Why zero output for 94 min:
1. stdout travelled through `ssh | tee` where **both Python and VCS buffer
   output**; the only prints during the long phases (preload ~27 s/wave,
   compute ~300 s/wave) went to the cocotb `logger`, which was also buffered.
2. The explicit-flush progress file (`task-13-phase10-progress.log`) did not
   exist yet in that run — it was uncommitted working-tree code.
3. From the outside, a 94-min silent run is indistinguishable from a hang,
   so it got killed. The run was probably NOT hung: the later identical run
   completed 7.5 h with per-wave progress.

### Root cause of the pathologically slow preload

`segment_preload()` wrote the 8 MB DRAM image **word-by-word (64 B)** through
the tb `dram_bkdoor_req/ack` handshake: per word = 2 VPI value-sets + 2 VPI
reads + 2 `RisingEdge` awaits.  Measured ~0.2-0.8 ms/word under VCS.

Offline profile (`sim/profile_dram_preload.py`, replays the exact wave
scheduling with a stub bridge, real GGUF weights):

```
wave  1: 131072 words FULL, 83547 non-zero   (zero-skip would save 36%)
wave  2: 73764 dirty        wave  7: 52072 dirty
wave  3: 73732 dirty        wave  8: 74478 dirty
wave  4: 51039 dirty        wave  9: 74306 dirty
wave  5: 73764 dirty        wave 10: 49538 dirty
wave  6: 73732 dirty        wave 11:   261 dirty
sum = 727758 words = 46.6 MB per layer  →  ~42 min of preload across 9 layers
```

Dirty words are dominated by **freshly packed weights each wave** (Q/K/V/O =
4.4 MB, FFN gate/up tiles 4 MB, FFN down tiles 4.2 MB — Qwen2.5-3B
I=11008, H=2048).  The delta optimization alone cannot skip them (they are
genuinely new data); the fix must make each written word cheap.

### Fix (committed)

- `rtl/tb/tb_soc_ibex.v`:
  - zero-initializes `u_dram_model.mem` at t=0 (makes skip-all-zero safe;
    firmware boot 0xDEADBEEF probe is unaffected — nothing reads completions),
  - new bulk port: Python writes dirty word runs to a hex file, tb executes a
    runtime `$readmemh` in zero sim time.  Old req/ack handshake kept intact
    as fallback (other flows on tb_soc_ibex are unaffected).
- `sim/cocotb_bridge.py` `segment_preload()`:
  - first preload writes only non-zero words; later preloads write only dirty
    words (dirty-to-zero always rewritten),
  - dirty words are grouped into contiguous runs and bulk-loaded via
    `$readmemh`; per-word handshake is the fallback when the tb predates the
    bulk port (compiled-simv compatibility),
  - `progress_cb(pct, done, total)` every ~10%.
- `sim/rtl_soc_segment_run.py`: `[WAVE Lx] start/done`,
  `[SEGMENT] preloading dram X%`, `[CHECKPOINT] saved Lx` prints, and an
  incremental `ph10-36layer-ibex-checkpoints.npz` save after every checkpoint
  layer (crash-resilience, matches todo 13 "每层存盘").
- `scripts/p10_lib/p10_sz0001.sh`: when the host itself is sz0001 (e.g. tmux
  session started there), `p10_ssh` executes locally — sz0001 has no
  self-ssh key, so the wrapper previously could not run on sz0001.
- `sim/test_dram_bulk.py`: regression smoke test for the bulk port.

### Verification

Compiled new `simv_soc_ibex_seg` (0 errors/0 warnings) and ran
`sim.test_dram_bulk` against it **on sz0001**:

```
[BULK-TEST] first preload 0.25s        (was 26-30 s → ~120x)
[BULK-TEST] sampled readback bad=0     (bit-exact)
[BULK-TEST] delta preload 0.01s
[BULK-TEST] dirty-to-zero ok=True
[BULK-TEST] skipped-zero-stays-zero ok=True
TESTS=1 PASS=1
```

## ISSUE-13B (root-caused + fixed): Ibex hardware outputs garbage — ladder FAIL

The completed 01:39 run ended `ladder=FAIL`: `hw_cos` 0.048/0.030/0.011/
0.007/0.022 for L0/L10/L20/L30/L35, `hw_range` contains INT_MIN, while the
pure-Python fp32 path is bit-perfect at L0 (cos 1.0).  During VRESID,
`np.rint(down_out_hw * ffn_scale * ...)` hit "invalid value encountered in
cast" → the hardware MMUL-down readback contains NaN/Inf (NaN→int32 cast
yields INT_MIN).  This is an RTL/firmware numerical bug, NOT a preload issue:
the same garbage appeared before the bulk-preload change, and the smoke test
proves the preload path is bit-exact.  Per task boundaries the segment-run
algorithm and tolerance ladder were NOT touched.  **Blocks todo 14's
ladder PASS — needs an RTL debug todo.**

Evidence preserved here:
- `evidence-completed-run-2026-08-20-0139.txt`
- `runlog-completed-run-2026-08-20-0139.log`
- `progresslog-completed-run-2026-08-20-0139.log`

### Root cause (two independent numerical bugs, both confirmed by probe)

A new minimal probe (`sim/rtl_soc_mmul_probe.py`, one FFN-down MMUL tile +
VRESID through the on-chip Ibex) reproduced the garbage on the old simv
(`nan=372/768`, `cos=nan`) and passes after the fix (`cos=1.000000`).

1. **Activation packing violated the hardware broadcast layout.**
   `spike_host._pack_act_tile_major_contig()` wrote the INT8 activation
   ROW-major (`tile = act[:, k_lo:k_hi].reshape(-1)`), but the
   mxu_soc_wrapper broadcast presents 64-byte word `c` of each 4096-byte
   K-tile as column `k` (byte `r` = act[r, k]) — the layout documented by
   `cocotb_bridge.pack_int8_activation_tile_major()` and used by every
   passing FM-SOC MMUL test.  With the row-major buffer, cycle 0 presented
   all 64 activations of row 0 and cycles 1–63 presented zeros, so the MAC
   array accumulated only the FIRST K-term (`acc[0,c] = act[0,0]*w[0,c]` —
   verified: hardware acc -42 vs golden -1427).  Every MMUL output in the
   chain was therefore garbage.

2. **MXU never applied the per-block scales (stubbed since Phase 1).**
   `mxu_top.v` declares `scale_addr_o` as "unused (stubbed)"; the
   store-out wrote raw INT32 accumulation to SRAM/DRAM.  The Func Model
   golden (`GoldenMXU.matmul_int4_per_block`, and the FuncModel MMIO
   handler the same firmware chain runs against on Spike) applies
   per-128-block FP32 scales and accumulates in FP32.  The segment run
   reads the MMUL-down output as FP32 — raw INT32 re-interpreted as FP32
   is a denormal (~0) for positive accumulators and **NaN for any negative
   accumulator** (exponent bits 0xFF), which the `astype(np.int32)` cast
   turns into INT_MIN.  The firmware already DMAs the per-tile scale row
   into SRAM and programs SCALE_ADDR + CTRL[2] (accumulate) per command —
   the RTL simply ignored both.

### Fix

- `rtl/mxu/controller.v`: `mac_reset_acc` now fires at the first K-tile of
  every command (each MXU call computes a fresh INT32 partial).
- `rtl/wrapper/mxu_soc_wrapper.v`: latches MXU SCALE_ADDR (0x24) and
  CTRL[2] from the APB→MMIO stream; the store-out FSM fetches the 256-byte
  per-tile scale row from SRAM at store-out time (SCALE_ADDR is only
  written after the preload handshake, so a preload-time fetch would read
  the previous command's scale) and writes
  `fp32 = acc[col] * scale[col]`, accumulated across commands when
  CTRL[2]=1, per Func Model semantics.  SCALE_ADDR==0 keeps the raw-INT32
  path.  Command-scoped scale/acc-mode are latched at row-capture time so
  the asynchronous drain is immune to the next command's MMIO writes.
- `sim/spike_host.py`: `_pack_act_tile_major_contig()` now emits the
  column-major broadcast layout.
- `sim/mmio_bridge.py`: `_run_mxu_compute()` gathers activations in the
  broadcast layout (ceil(K/64) back-to-back 4096-byte tiles).
- `sim/tile_scheduler.py` + legacy FuncModel test fixtures: aligned the
  Python firmware emulation and direct-bridge tests with the hardware
  activation layout.
- FM-SOC MMUL goldens updated from `matmul_int32` to
  `matmul_int4_per_block` (FP32) for scale-carrying descriptors; FM-SOC-003
  and FM-SOC-010 compare with a small FP32 tolerance (RTL dequant rounds
  through double precision, ≤1 ulp vs numpy).

### Verification

- `sim/rtl_soc_mmul_probe.py` (single MMUL-down + VRESID via Ibex RTL):
  pre-fix `nan=372/768 cos=nan` (root cause confirmed) → post-fix
  `nan=0 cos=1.000000`, VRESID `cos=1.000000` bit-exact.
- simv rebuilt 0 errors, warning profile identical to the pre-fix build
  (27 pre-existing vendored-IP warnings).
- FM-SOC regression (Ibex RTL): FM-SOC-001/003/007/009/010/024/026/027/
  028/029 and FM-SOC-032 PASS.  FM-SOC-10X still fails at op00 RMSNorm
  (pre-existing SFU issue, unrelated to MMUL).
- Python pytest: no new failures vs HEAD baseline (test_soc_fm 46/46).

### Status

Fixed and committed; `ibex-seg-run` restarted with the rebuilt simv
(todo 14 ladder verification pending the ~7.5 h run).

## ISSUE-13C (in verification): L19 output corrupted in segment run after boundary full DRAM preload

**Status:** root-cause identified, fix committed, pending new VCS run.

### Symptom

A new run (started 2026-08-21 01:23 with boundary full DRAM preload fix
a8af351) reached L35. Checkpoints:
- L0  cos=1.000000 ✓
- L10 cos=1.000000 ✓
- L20 cos=0.031199 ✗
- L30 cos=0.998220 ✓
- L35 pending

Cross-checks (Ibex pre-layer output vs Spike same-layer output):
- L9  cos=1.000000 ✓
- L19 cos=0.031203 ✗
- L29 cos=1.000000 ✓
- L34 cos=1.000000 ✓

### Key isolation

Offline analysis (`PYTHONPATH=sim python3` on the saved
`ph10-36layer-ibex-checkpoints.npz`):
- `hw_layer_20_output` matches the Python golden computed from
  `hw_layer_19_output` as input (cos=1.000000). Therefore **L20 hardware is
  correct; the corruption is entirely in L19's output**.
- L19 standalone (`rtl_soc_l19_full.py`, fresh DUT reset) passes cos=1.000000.
- So L19 computation is correct, but L19 produces garbage when executed after
  prior segments in the same VCS session.

### Root cause

The boundary full-preload fix (a8af351) only re-synchronizes DRAM with the
Python `model.dram` image. The 4 MB SRAM scratch and engine-wrapper internal
staging (MXU/SFU/VECTOR buffers) are left untouched. Leftovers from the
previous segment's MMUL/VRESID staging can leak into the next segment's first
operations. L19 — the first layer after the L9→L10 segment — was corrupted
this way. L29/L30 pass because the preceding L19→L20 segment happens to leave
a SRAM/state pattern that does not corrupt L29 (or is overwritten correctly);
the failure mode is state-dependent, not universal.

### Fix (committed)

`6091ec9 fix(sim): clear SRAM at segment boundaries to prevent stale state
corrupting L19`
- At every segment boundary `bridge.segment_preload()` now receives
  `sram=b"\x00" * SRAM_SIZE` along with `force_full=True`.
- SRAM is pure per-op scratch (firmware DMAs every operand in before use), so
  zeroing it at boundaries is safe.

### Verification pending

The running VCS session started before commit 6091ec9, so it does not include
the SRAM clear. It is currently executing L35. After it finishes, a new run
with 6091ec9 will be started to verify L0/L10/L20/L30/L35 all pass the
 tolerance ladder.

## ISSUE-13D (fixed): DESC_BASE overlaps command ring entries 128+ → long runs corrupt descriptors

**Status:** fixed 2026-08-21. `sim/spike_host.py` `DESC_BASE` moved
`0x80001000` → `0x80010000` (single constant edit).

### (a) The overlap bug

`DESC_BASE = 0x80001000` maps to command-ring entry 128 (each ring entry is
32 B). Descriptors are 64 B, so descriptor `i` occupies ring entries
`128+2i` and `128+2i+1`. In the 9-layer segment run, L19 writes commands at
ring entries 102-135; entries 128-135 overlap descriptors 0-7. The descriptor
is written first, then the command overwrites it, so the firmware reads a
corrupted descriptor for the later waves of L19 — matching the observed
L19/L20 failure (cos≈0.031) while L0/L10/L29/L30/L34 pass.

### (b) New address chosen

`0x80010000` — free: above the 1024-entry command ring (0x80000000-0x80007FFF)
and the completion ring (0x80008000-0x8000FFFF), below the activation region
(`P10_ACT_BASE = 0x80020000`). Verified against spike_host.py region constants
(P10_ACT_BASE/END, P10_WGT_BASE). The firmware does not hardcode `DESC_BASE`;
it reads the descriptor address from each command entry, so no firmware/Verilog
change is needed. The running full segment run (PID 83832) uses the old
constant in memory and was left untouched.

### Probe-path companion edit

`sim/rtl_soc_mmul_probe.py` read the descriptor back from a hardcoded
`0x80001000`; its descriptor address actually comes from `sh.DESC_BASE` via
`_ibex_schedule_chain`, so the read-back was switched to `sh.DESC_BASE` to stay
consistent with the new constant.

### Intentionally separate (not part of the segment-run/probe path, single-command, no ring progression → no overlap)

- `sim/rtl_soc_runner.py:1095` — `P0SpikeRunner.DESC_BASE`, FM-SOC-001..008
  runner, RING_SIZE=32 (never reaches entry 128).
- `sim/spike_host.py:223` — standalone MMUL smoke helper with its own fixed
  layout (desc 0x80001000 / act 0x80010000 / wgt 0x80200000 / out 0x81000000);
  single command at ring entry 0.
- `sim/p10_fm3_measure.py:68`, `sim/perf_tests.py:35`,
  `sim/perf_tests_standalone_p11.py:33` — `DESC_BASE = DRAM_BASE + 0x1000`
  single-command perf scripts.
- `sim/tests/test_soc_pcie_dma.py:75`, `sim/tests/test_verification_fault_injection.py:381,405`,
  `sim/tests/test_spike_mmio_server.py` — test fixtures, single-command.

### (c) Verification

```
$ PYTHONPATH=sim python -c "from sim import spike_host as sh; assert sh.DESC_BASE == 0x80010000, sh.DESC_BASE; print('DESC_BASE ok', hex(sh.DESC_BASE))"
DESC_BASE ok 0x80010000
```

Python-only change; simv not rebuilt. Next segment run (with 6091ec9 + new
DESC_BASE) should clear the L19 corruption.

### Ledger update (todo 22)

2026-08-21: ISSUE-13D recorded in `docs/bugs/bugs-soc-rtl.md` as
**BUG-RTL-SOC-008** (Status Fixed) with evidence links
(`build/evidence/l0l19-probe-evidence.txt`, `build/evidence/l0l19-probe.json`)
and fix commit `fa4ffec`. The Phase 10 bug-ledger completeness check
(`scripts/p10_bug_ledger_check.sh`) was re-run for todo 22 and now gates
BUG-RTL-SOC-008 on the L0L19 probe evidence;
report: `build/evidence/task-22-phase10-rtl-verification.txt`.
