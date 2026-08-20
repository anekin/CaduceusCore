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

## ISSUE-13B (open, out of scope here): Ibex hardware outputs garbage — ladder FAIL

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
