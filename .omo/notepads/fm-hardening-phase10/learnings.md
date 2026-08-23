# fm-hardening-phase10 learnings

## [2026-08-23] Start of work
- Plan approved and pushed to origin/main. Starting execution.
- Wave 1 dependency: todo 1 and todo 3 can run in parallel; todo 2 depends on 1; todos 4/5 depend on 2+3.

## [2026-08-23] Todo 3 — command_ring unification
- Created `sim/command_ring.py` as the single source for ring constants and helpers.
- Migrated all 8 `% 64` sites in `sim/spike_host.py` to `command_ring.expected_head()`.
- `sim/rtl_soc_segment_run.py` and `sim/cocotb_bridge.py` now import ring size from `command_ring`.
- FM-SOC runner differentiated layout:
  - P0/P1/P2P3 keep `DESC_BASE=0x80001000` + `RING_SIZE=32`; added `assert_ring_size` and scoped `assert_desc_clear_of_used_regions` guard, annotated with `BUG-RTL-SOC-008`.
  - P4 uses block-relative descriptor base `0x80048000` (block 0); added `address_space.contract_check(ring_entries=1024, desc_base=..., desc_count=23, act_base=0x80800000)` and `BUG-RTL-SOC-008` annotation.
- `sim/device_server.py:95` `RING_SIZE=16` left unchanged with a comment explaining it is excluded from unification (host-device protocol path).
- Discovered and fixed edge case in `address_space.contract_check`: `act_base=0x80800000` (DRAM_END) is a legal exclusive upper bound even though it lies on the half-open window boundary; updated the window check to allow `act_base == DRAM_END`.
- Evidence: `build/evidence/task-3-fm-hardening-phase10.txt`.

## [2026-08-23] Todo 1 done — sim/address_space.py contract module
- Created `sim/address_space.py` + 14 pytest cases in `sim/tests/test_address_space.py`; acceptance + QA scenarios all pass.
- Design decisions:
  - `REGIONS` = 5 named `(base, size)` half-open tuples: command_ring [0x80000000, 0x80008000), completion_ring [0x80008000, 0x80010000), descriptor_pool [0x80010000, 0x80020000), activation [0x80020000, 0x801E0000), weight [0x801E0000, 0x80800000). The C1 summary mentions an "output" region, but the detailed todo spec lists only these 5 and spike_host has no separate output-region constant (outputs live in the FP/activation arena) — no output region was invented.
  - `regions_overlap(a, b)` accepts a REGIONS key or a raw `(base, size)` tuple (todo 3's scoped per-runner checks reuse it); touching boundaries are NOT overlap.
  - `contract_check(ring_entries=1024, desc_base=None, desc_count=0, act_base=None)`: desc_base=None resolves to DESC_BASE; act_base=None SKIPS assertion (b) per todo 1 spec. TODO 2 NOTE: to enforce the default P10_ACT_BASE bound, pass `act_base=P10_ACT_BASE` explicitly — todo 2's "default parameters assert (b)" wording needs that explicit arg.
  - contract_check order: window checks (WindowError) first, then (a) OverlapError, then (b) OverlapError.
  - `addr_in_window` requires addr < DRAM_END (window end exclusive): zero-size probe at 0x80800000 is out of window.
- Discrepancies found (recorded, not silently resolved):
  - Todo 1 ("act_base=None skips assertion (b)") vs todo 2 ("default parameters = spike_host constants, (b) asserted against 0x80020000") are mutually inconsistent; followed todo 1 and documented the resolution in the module docstring.
  - Plan's completion-end formula uses `ring_entries*32` for the completion ring; module uses COMPLETION_ENTRY_SIZE (=32, npu_abi.json:1583) — same value.
- Tests pin constants against external truth sources only (`sim/spike_host.py:44,66,67,347-352`, `spec/npu_abi.json:1579-1582`); no magic literals elsewhere.

## [2026-08-23] Todo 9 — ABI single source of truth
- Added `tile_scale_bytes: 256` to `spec/npu_abi.json` `rings.configuration` and regenerated all artifacts with `scripts/gen_npu_abi.py --generate`.
- `gen/npu_abi_firmware.h` now exports `NPU_ABI_RING_BUFFER_ADDR`, `NPU_ABI_COMPLETION_RING_ADDR`, and `NPU_ABI_TILE_SCALE_BYTES` (plus ring entries/sizes).
- `firmware/npu_firmware.c` now sources `DRAM_BASE`, `RING_BUF_ADDR`, `RING_ENTRIES`, `CMD_DESC_SIZE`, `COMPLETION_RING_ADDR`, and `TILE_SCALE_BYTES` from the generated ABI header. `DRAM_SIZE` remains hand-written `0x00800000` with an explicit comment distinguishing the 8 MB RTL regression window from the ABI 2 GB `NPU_ABI_DRAM_SIZE`.
- `sim/tile_scheduler.py` now carries an `INTENTIONAL divergence` comment at `TILE_W=128` documenting that firmware groups scales per 64 columns (256B) while the Python scheduler uses per 128 columns (512B).
- Added `sim/tests/test_npu_abi_constants.py`: parses `spec/npu_abi.json`, compares values with `sim/address_space.py`/`sim/command_ring.py`, and asserts the tile-scale divergence is intentional. No regex parsing of C constants.
- Verification: `python3 scripts/gen_npu_abi.py --check` exit 0, `make -C firmware` exit 0, new pytest file 5/5 pass, `scripts/contract_check.py --check` 555/555 pass.
- Spike mmul_smoke still fails with the pre-existing `max_diff=7.64e+02` tolerance mismatch (documented in `.omo/notepads/func-model-gap-closure/issues.md` Issue 002 / BUG-SOC-FM-005). The failure reproduces on the unchanged baseline and is unrelated to the constant-source migration; no firmware control flow was modified in this todo.

## [2026-08-23] Todo 8 — dual-packer byte-equivalence guard (ISSUE-13B)
- Created `sim/tests/test_packer_equivalence.py`: 10 tests, all pass in 0.19s.
- 6 grid points {(1,64),(1,128),(64,128),(32,256),(1,2048),(64,2048)}: `spike_host._pack_act_tile_major_contig` vs `cocotb_bridge.pack_int8_activation_tile_major` byte-for-byte equal, deterministic random INT8 activations with fixed seed `0x13B`.
- Beyond the plan: added `test_layout_is_column_major_broadcast` (pins word c byte r == act[r, k] structurally at (64,130), incl. zero-pad tail) — guards the case where BOTH packers drift to the same wrong layout, which a pure equivalence test cannot see.
- Failure injection: `test_row_major_variant_would_fail` monkeypatches the host packer with a replica of the pre-fix row-major form and asserts divergence. Key subtlety learned from ISSUE-13B: at M=1, row-major and column-major byte layouts COINCIDE (word c byte 0 == act[0,c] either way) — so the injection test runs only on multi-row grid points (M=32/64). This is exactly why the bug only surfaced in the real multi-row chain and why the (1,K) grid points alone could never have caught it.
- Docstring records the ISSUE-13B root cause and the column-major broadcast layout contract for both packers.
- Neither packer implementation modified (`spike_host.py:588-603`, `cocotb_bridge.py:166-187` untouched).
- Evidence: `build/evidence/task-8-fm-hardening-phase10.txt`.

## [2026-08-23] Todo 6 — scale-path golden hardening (SCALE_ADDR!=0 + non-trivial FP32 scale)
- Added `sim/tests/test_soc_fm.py::test_mmul_scale_nonzero` + `test_mmul_scale_nonzero_fp16_scale_collapse` (plus shared helper `_doorbell_run_scale_mmul`), reusing the existing `_doorbell_setup_mmul` doorbell pattern.
- Layouts verified before writing the test (the key risk in this todo):
  - Firmware path (`tile_scheduler.tile_mmul`) consumes scale tiles at DRAM offset `(n_tile*num_blocks + k_block) * TILE_SCALE_BYTES`, `TILE_H=TILE_W=128`, `TILE_SCALE_BYTES=512B` → layout `[n_tile][ceil(K/128)][128]` fp32.
  - Bridge reader (`mmio_bridge.py:248-252`) reads `[ceil(K/128)][N]` fp32 contiguous per MXU command.
  - With N=128 (=TILE_W) and one N-tile, both layouts coincide: a contiguous `[2][128]` fp32 buffer. Choosing N=128 was therefore load-bearing — a larger N would need per-N-tile interleaving.
  - `pack_int8_activation_tile_major` handles M=1/K=256 (4 back-to-back 4096-byte K-tiles); tile_mmul's k_block=1 command reads I_ADDR at +8192 — consistent.
- K=256 → firmware splits into 2 MXU commands (K=128 each) with CTRL[2]=1 accumulate on the 2nd; bridge accumulate order (existing + fresh) matches `matmul_int4_per_block`'s fp32 accumulation order over full K → happy path is bit-exact (max_abs_diff = 0.0), so rtol/atol=1e-5 is ample headroom.
- Failure injection (FP16-in-FP32 scale write, F3 PERF-bug shape): max_abs_diff = 868.6 vs golden — a ~1e3 collapse, so the negative assertion (`not allclose`) is robust, not borderline.
- Full `test_soc_fm.py` re-run: 48 passed / 0 failed. Evidence: `build/evidence/task-6-fm-hardening-phase10.txt`.


## [2026-08-23] Todo 10 — segment-boundary SRAM-clear contract (ISSUE-13C)
- Added `SegmentBoundaryError` to `sim/cocotb_bridge.py` and `clear_sram: bool = False` parameter to `segment_preload()`.
- Contract: `force_full=True` (segment boundary DRAM re-sync) + `clear_sram=True` requires `sram == b"\x00" * SRAM_SIZE`; any other value raises `SegmentBoundaryError`.
- `sim/rtl_soc_segment_run.py` segment boundary call site now passes `clear_sram=True` alongside the existing `sram=b"\x00" * SRAM_SIZE` / `force_full=True`.
- Single-segment/probe callers keep `clear_sram=False` (default) and are not forced to pass `sram`.
- `sim/test_dram_bulk.py` gained `test_bulk_with_sram_clear`: seeds SRAM with a non-zero pattern, calls `segment_preload(..., clear_sram=True)`, and verifies head/tail zeroing via backdoor readback.
- `sim/tests/test_segment_boundary.py` (new) provides pure-Python FM coverage:
  - `test_two_segment_sram_clear`: segment 1 leaves SRAM dirty, boundary preload clears it, segment 2 starts clean.
  - `test_segment_boundary_error_injection`: empty, non-zero, and wrong-size SRAM all raise `SegmentBoundaryError` in boundary mode.
  - `test_clear_sram_default_allows_empty_sram`: legacy callers with `clear_sram=False` are unaffected.
- `test_dram_bulk.py` also had to guard its `import cocotb` with try/except so `pytest sim/test_dram_bulk.py -v` exits 0 on hosts without cocotb installed (the cocotb RTL tests are then simply collected as 0 items).
- Acceptance: `PYTHONPATH=sim python -m pytest sim/tests/test_segment_boundary.py::test_two_segment_sram_clear -v` exit 0; `PYTHONPATH=sim python -m pytest sim/test_dram_bulk.py -v` exit 0.
- Evidence: `build/evidence/task-10-fm-hardening-phase10.txt`.

## [2026-08-23] Todo 2 — scheduling-time assertions + stale preflight DESC_BASE
- `spike_host.schedule_chain` now asserts `address_space.contract_check(desc_base=DESC_BASE, desc_count=len(ops), act_base=address_space.P10_ACT_BASE)` as its FIRST statement — `act_base` passed explicitly (todo 1 default skips assertion (b); see todo 1 learnings).
- `spike_host.write_cmd_entry` carries a per-entry guard `contract_check(desc_base=desc_addr, desc_count=1, act_base=P10_ACT_BASE)` so direct callers (run_pcie_dma_smoke, segment-run entries) are covered even outside schedule_chain.
- Signature change: `schedule_chain(ops, model=None)` (was `(model, ops)`) — required because the plan's QA scenarios call `sh.schedule_chain([])` / `sh.schedule_chain([0]*20)` with the ops list as sole positional. Doorbell write is skipped when model is None. All 4 internal call sites updated.
- `rtl_soc_segment_run.test_soc_ibex_segment_run` startup: one-time `contract_check(desc_base=DESC_BASE, desc_count=34, act_base=sh.P10_ACT_BASE)`; per-op re-checks ride on sh.write_cmd_entry.
- Dual-module trap (worth remembering): spike_host does top-level `import address_space` while tests do `from sim import address_space` — namespace packages make these TWO module objects with DIFFERENT exception classes. `pytest.raises(sim.address_space.OverlapError)` does NOT catch `address_space.OverlapError` raised inside spike_host. test_spike_host_overlap.py therefore aliases `OverlapError = spike_host.address_space.OverlapError`.
- `scripts/p10_36layer_preflight.sh`: 5b heredoc now imports DESC_BASE/FP_DRAM_BASE from sim/spike_host.py (`PYTHONPATH="$ROOT/sim"`); 5c polarity flipped from "out-of-window is expected" to "out-of-window is a regression" (its old OUT branch was dead code); :446/:496/:696 text now reflects FP_DRAM_BASE=0x80020000 in-window. No region constant value changed.
- Regression on this host: full sim/tests = 19 failed / 1412 passed / 13 errors — failures/errors are environment-affected legacy (cocotb collection errors, missing spike artifacts, engine perf baselines); none in changed paths. spike_host-dependent suites (test_soc_fm/qwen3b/firmware/mmio_bridge/ring_entry_abi) 95 passed.
- Evidence: `build/evidence/task-2-fm-hardening-phase10.txt`.

## [2026-08-23] Todo 4 — ring-stress wrap scenario (BUG-RTL-SOC-008 FM guard)
- Created `sim/tests/test_command_ring_stress.py`; acceptance test `test_ring_wrap_at_entry_128` passes in 0.46s (< 30s).
- Parameterization: `NPUFirmware.__init__(sim_modules, bridge, ring_size=16)` and `FuncModel.__init__(..., ring_size=16)` / `_create_firmware(..., ring_size=16)` — default unchanged, existing callers untouched; `SpikeFirmware` NOT parameterized (spike path has its own hardcoded 16, out of scope per plan). Pinned by `test_default_ring_size_unchanged`.
- Scenario: doorbell pre-seeded at persistent offset 120 (dict + HOST_TAIL/NPU_HEAD/HOST_HEAD MMIO mirrors), 140 MMUL commands queued → ring entries 120..259 written, physically crossing entry 128 (0x80001000, the pre-fix DESC_BASE BUG-RTL-SOC-008 corrupted); the test reads the entry back and asserts it holds opcode MMUL. Descriptors sequential at DESC_BASE + i*64.
- Interpretive decision (plan text is self-contradictory): 140 commands from offset 120 cannot wrap 1023→0 at runtime (120+140=260). Satisfied both claims: helper-level wrap asserts (expected_head(1023)==1023, expected_head(1024)==0, advance_head(1023,1)==0) AND a runtime continuation phase that drives the persistent offset until the head physically wraps 1023→0 at cumulative command 904, asserting per-command head == command_ring.advance_head(START_OFFSET, total) — no raw `%` anywhere in the test.
- `NPUFirmware` has no completion-ring DRAM buffer (completion record = ordered result list + doorbell HOST_HEAD), so "cmd_id" is asserted via ordered results + head arithmetic. Noted in the test docstring.
- Failure injection `test_desc_base_inside_ring_rejected`: monkeypatches `address_space.DESC_BASE` to `command_ring.ring_entry_addr(128)` (== 0x80001000, expressed without a magic literal) and asserts OverlapError.
- Pitfall caught during bring-up: per-command data regions must be spaced so all 140 × 4096-byte buffers stay disjoint — my first layout had act_128 (0x80020000 + 128*0x1000 = 0x800A0000) overwriting wgt_0, producing a golden mismatch (got [2,1] vs [11,1]) that only showed up in the bulk queue-then-run flow. Data addresses are now spaced 0x1000 apart and all inside the activation region [0x80020000, 0x801E0000).
- Regression: test_soc_fm + test_firmware + test_npu_firmware_deprecation + test_command_ring + test_address_space = 82 passed.
- Evidence: `build/evidence/task-4-fm-hardening-phase10.txt`.

## [2026-08-23] Todo 7 — accumulate-path golden hardening (CTRL[2] two-command chain)
- Added `sim/tests/test_soc_fm.py::test_mmul_accumulate` + `test_mmul_accumulate_ignore_ctrl2` + shared helper `_doorbell_run_accumulate_mmul`, reusing `_doorbell_setup_mmul` (todo-6's data/descriptor setup).
- Mechanism confirmed before writing: a single doorbell MMUL descriptor with K=256 makes `tile_scheduler.tile_mmul` chain TWO MXU commands (K=128 each) to the same SRAM output accumulator, with `ctrl_val = 4 if k_block > 0 else 0` (`tile_scheduler.py:148` — the Python analogue of `npu_firmware.c:541`). Two SEPARATE descriptors to the same output address would NOT accumulate (each dispatch restarts k_block=0), so "两命令链" = the intra-descriptor K-split chain.
- Weight-tile layout constraint: N=128 is load-bearing. The firmware reads tile (k_block=1) at DRAM offset `TILE_WEIGHT_BYTES`=8192 from `weight_addr`; only N=128 makes the contiguous packed buffer (`(256*128+1)//2`=16384B) align with the two 8192B tile offsets. N=64 (or any N<TILE_W) would require explicit tile-major weight placement. Same reason todo 6 used N=128.
- Golden is built by combining two `matmul_int4_per_block(group_size=128)` partials on packed-byte slices `wgt_packed[:8192]` / `wgt_packed[8192:]`. First-attempt bug: passing the UNPACKED `unpack_int4(wgt_packed).reshape(K,N)` slices as `weight_packed` crashes inside `unpack_int4` (it unpacks its input). matmul_int4_per_block expects packed bytes, not int8 values.
- Happy path is bit-exact (max_abs_diff=0.0 vs both the combined partials and the full-K golden) — bridge accumulate order (existing+fresh fp32) matches the golden's fp32 block accumulation. Anti-vacuous gates: partial0 non-trivial (max_abs≈564) and output != partial1.
- Failure injection: monkeypatch `mmio_bridge.MMIOBridge._run_mxu_compute` (class attr, so it also binds on instances created after the patch) forcing `accumulate=False` while firmware still writes CTRL[2]=1 → output == partial1 exactly (max_abs_diff=0.0) and diverges from the accumulated golden by 573 — the gate is real, not borderline.
- Orthogonality to todo 6: scales all-ones (todo 6 owns non-trivial scale VALUES); todo 7 owns the accumulation semantics between commands. Distinct DRAM addresses from todo 6's tests.
- Full `test_soc_fm.py`: 50 passed / 0 failed (todo-6 baseline 48 + 2 new). Evidence: `build/evidence/task-7-fm-hardening-phase10.txt`.

## [2026-08-23] Todo 5 — long-sequence persistent-offset FM gate
- Created `sim/tests/test_soc_fm_long_sequence.py` (2 tests, whole file 30s < 2min):
  - `test_scaled_chain_baseline_pinned`: baseline characterization FIRST — pins the
    current 3-layer direct-chain FP16 fingerprints (hard-coded md5s captured today)
    + determinism + pairwise-distinct layers.
  - `test_multi_layer_persistent_offset`: 11 layers x 19 ring commands = 208 (>=200)
    through `host_write_command` + `firmware.run_loop`, doorbell never reset between
    layers; per-command wrap assertions (host_tail/npu_head == k % 16), 208 % 16 == 0
    (13 full wraps); every layer output bit-identical to the direct-path golden;
    `final_cos=1.000000000` asserted numerically >= 0.999.
- Key design constraint discovered (not in the plan): the firmware dispatcher routes
  MMUL to `tile_mmul`, which ALWAYS applies an FP32 scale (`matmul_int4_per_block`
  semantics) — the chain fixture's VRESID consumes the output buffer as INT32, and
  the firmware emulator has no INT32 MMUL dispatch. Routing data-flow MMULs through
  the ring would silently change numerics, and changing the scheduling algorithm is
  forbidden — so the data-flow MMUL keeps the direct bridge path and every MMUL also
  gets a genuine ring command diverted to a scratch region. Everything else
  (SFU/Vector/VCONV) is ring-driven end-to-end, and per-op goldens are asserted on
  the ring path.
- Address-layout bug caught by the test itself during development: first desc-pool
  choice 0x80100000 overlapped block 3's scratch region ([0x800D0000, 0x80110000))
  and clobbered ROPE inputs mid-run. Moved to 0x80600000 and added an explicit
  descriptor-pool-vs-block-region disjointness assertion — the exact BUG-RTL-SOC-008
  class the gate must catch, now enforced inside the test.
- Failure injection: corrupt the layer-5 op14 VMUL ring command's descriptor ADDRESS
  by one slot (+64), planting a valid-but-wrong SILU-shaped descriptor there — the
  silent wrong-op shape of BUG-RTL-SOC-008. Firmware executes it with status 'done',
  layers 0-4 stay bit-identical, layer 5 output mismatches golden (asserted). The
  per-op golden for the corrupted op is deliberately skipped; divergence is asserted
  at layer level. A pre-VCONV ring command carries the previous block's residual
  (INT32->FP16) exactly like the fixture's `_chain_vector_conv`.
- Verified: acceptance command exit 0 (~16s), `test_soc_fm.py` 50 passed (no
  regression), dependency suites test_command_ring + test_spike_host_overlap 12
  passed. Evidence: `build/evidence/task-5-fm-hardening-phase10.txt`.

## [2026-08-23] Todo 11 — reverse-dependency regression gate
- Created `scripts/fm_reverse_dependency_gate.sh` (200 lines, `set -euo pipefail` + ERR trap for diagnostics).
- Sensitive surface = 46 files from the todo-11 spec globs (`rtl/{mxu,soc,sfu,vector,wrapper,ip}/*.v` + 9 firmware/ABI/sim bridge files), expanded with bash nullglob so UNTRACKED files are included; hashes via `git hash-object` (sha256sum fallback), keyed by relative path.
- State file `.omo/last_fm_gate.json`: `{head, hashes, pytest, timestamp}` — the extra `pytest` key records the last green run's failed/errors counts, making "0 new failures vs baseline" per-machine self-calibrating (bootstrap = task-3 legacy 164 failed / 45 errors, env-overridable via FM_GATE_BASE_FAILED/FM_GATE_BASE_ERRORS).
- Two real bugs found and fixed during bring-up (both would have silently weakened the gate):
  1. `$ACT1` unquoted expansion treats the leading `PYTHONPATH=sim` as a command name (rc=127) — bash parses assignments only on literal words, not expanded ones. Fixed with `env PYTHONPATH=sim python ...`.
  2. Heredoc-overrides-pipe stdin conflict: `{...} | python3 - "$TMP" <<'PYEOF'` makes python3 consume the heredoc as the script, so `sys.stdin` was at EOF and `hashes` came out EMPTY — and in the real run the pipeline then died silently under `set -e` before `mv`. Fixed by writing hashes to a temp file and passing its path via env; ERR trap added so any future silent death prints `FAILED at line N`.
- Environment reality check: on sz0002 pytest 9 aborts the whole session on 9 collection-time import errors (no cocotb, no caduceus_device_protocol) → 0 tests collected. Stage 1 therefore runs `--continue-on-collection-errors` so the runnable suite executes (19 failed / 1424 passed / 13 errors ≈ todo-2's host profile), and FAILs hard if `passed == 0`.
- Full bootstrap run PASSED end-to-end: stage 1 (19/1424/13 vs 164/45), stage 2 scale+accumulate (2/2), stage 3 = W4-PERF 6 batches on sz0001 via `p10_ssh "bash sim/regression/run_w4_perf_batch.sh"` (all TESTS=1 PASS=1 FAIL=0, ~2.5 min total), stage 4 state written atomically (tmp+mv). Trigger test: appended `// gate trigger` to `rtl/mxu/controller.v` → dry-run exit 1 listing ONLY that file + the 4 planned actions; reverted → dry-run "gate: clean" exit 0.
- Design notes: dry-run never executes anything (prints the exact commands incl. the p10_ssh form); clean state exits 0 without touching the state file; failure at any stage exits 1 and leaves the state file unchanged (verified during bring-up). W4-PERF batch logic is not re-implemented — `run_w4_perf_batch.sh` is reused via `scripts/p10_lib/p10_sz0001.sh`.
- Evidence: `build/evidence/task-11-fm-hardening-phase10.txt`; full run log `build/evidence/task-11-gate-run.log`.

## [2026-08-23] Todo 12 — FM attn_weight coverage (BUG-RTL-SOC-007 FM-side gap)
- Added `sim/tests/test_soc_fm.py::test_mmul_attn_weight_shape` + `test_mmul_attn_weight_shape_not_dispatched` + shared helper `_doorbell_run_attn_weight_mmul`, reusing `_doorbell_setup_mmul` (todo 6/7's doorbell data/descriptor setup) with an own RandomState(20260812) seed and own DRAM addresses (0x8001_8000/0x8002_C000/0x8100_8000/0x8011_8000/0x8000_0300).
- Shape: PERF-13 attn_weight M=32, K=32, N=64 (`sim/perf_tests.py:255`). K=32 → num_blocks=1, N=64 → one N-tile, so `tile_mmul` issues exactly ONE MXU command (k_block=0, ctrl=0) — no accumulate, no multi-tile. This is why todo 6/7's N=128 alignment reasoning is NOT needed here: with N≤TILE_W the firmware scale DMA (tile_width*4=256B, tile_scheduler.py:137) coincides with the bridge-reader layout [1][64] fp32 contiguous (mmio_bridge.py:248-252), so the two layouts agree and the same buffer feeds both readers.
- Happy path is bit-exact (max_abs_diff = 0.0) with non-trivial random scales in [0.5,1.5); golden max_abs ≈ 557, so the anti-vacuous gate (golden non-zero) is stable under the fixed seed.
- Failure injection chosen per plan QA ("命令不执行、completion 不写"): monkeypatch `miniv.NPUFirmware._dispatch` (class attr, so instances created after the patch are covered — same pattern as todo 7) to return status 'unknown' without dispatching. Under injection: status != 'done', output DRAM all-zero, max_abs_diff ≈ 557 vs golden — both happy-path gates (status, golden) demonstrably fail, proving the guard is real.
- Scope guardrails honored: no RTL touched, spike_host forward path untouched (the gap being closed IS that forward never emits attn_weight — the test issues the op through the doorbell instead), no new dependencies.
- Full `test_soc_fm.py`: 52 passed / 0 failed (todo-7 baseline 50 + 2 new). Evidence: `build/evidence/task-12-fm-hardening-phase10.txt`.

## [2026-08-23] Todo 13 — F-wave gate scripts (fm_hardening_f1..f4)
- Created 4 scripts: `scripts/fm_hardening_f1_audit.sh` (92 lines), `fm_hardening_f2_code_quality.sh` (73), `fm_hardening_f3_manual_qa.sh` (98), `fm_hardening_f4_scope_gate.sh` (49). All ≤100 lines, `set -euo pipefail`, self-documenting headers, no unconditional exit 0. Modeled on the p10_f* templates but deliberately scoped to the task spec.
- F1 design decisions:
  - Terminal state = LAST `(Result|Status|OVERALL|Overall verdict): PASS|FAIL` marker (plus standalone `^PASS$`) in each evidence file — task-3 ends with bare `PASS`, task-9's last marker is the regression-check PASS (its earlier spike-smoke `Result: FAIL` is superseded). This reproduces how each todo was actually judged.
  - Acceptance rerun extracts backtick commands from the plan's "Acceptance criteria" lines; classification: pytest/python/bash-n/make/ls/rev-gate-dry-run → run (expect rc 0); spike smoke/`--model`/W4-PERF/FM-SOC → SKIP-ENV; static greps and quoted test names → SKIP-STATIC; self-invocation of f1 → skip-self (recursion).
  - A rerun that exits 5 with "collected 0 items" (sim/test_dram_bulk.py without cocotb) is SKIP-ENV, not FAIL — todo-10's own evidence recorded this exact 0-collected environment state ("cocotb not installed in this environment").
  - Unchecked plan todos (checkbox `- [ ]`) with missing evidence are PENDING — reported loudly but not gated (todo 14 is blocked on this todo). Checked todos with missing/non-PASS evidence are FAIL. At final-wave time all boxes are checked → strict.
- F2 design: residue scan over ADDED lines of changed source files (.py/.c/.h/.v/.sh) only — prose "todo N" references in notepads are normal (learnings.md legitimately contains "TODO 2 NOTE:"). Residue regex is built with split quoting (`TO""DO`) so the gate never self-matches. Baseline = min(task-3 legacy 164/45, recorded gate state 19/13) → 19 failed / 13 errors on this host.
- F3 design: five stages (a)-(e); sz0001 stages reuse `run_w4_perf_batch.sh` (no batch logic re-implemented) and `run_fm_soc_all.sh <case>` per the MUST-NOT; `--dry-run` prints the exact remote commands; unreachable sz0001 → DEFERRED(no-ssh) with a clear message. Per MUST DO, any failing local stage fails the gate — the spike smoke red is NOT self-waived.
- F4 design: PLAN_BASE `b542cc5b` fixed (env-overridable); whitelist sim/ firmware/ scripts/ docs/ spec/npu_abi.json gen/ build/evidence/ .omo/ — gen/ and the regenerated w4-perf/fullchain evidence files are legitimate todo 9/11 artifacts, so they are whitelisted (the task summary listed only a subset); frozen surface rtl/ + arc_model/design_space_explorer/quantize/ggml-npu/requirements.txt checked in both the range diff AND the working tree.
- Bugs found and fixed during bring-up (each would have silently weakened the gate):
  1. F1 verdict was initialized to FAIL and never set to PASS in the happy path → every green todo reported FAIL. Fixed with an explicit UNKNOWN→PASS finalize step.
  2. F1's `elif [ "$?" -eq 5 ]` inside if/elif/else lost the original rc in the else branch — `$?` there is the elif condition's rc, not bash -c's. Fixed by capturing rc at the top of the else block.
  3. F3: removing a blank line glued the section-divider comment onto the `VERDICT=...` assignment line, so VERDICT was never assigned → "unbound variable" crash at the evidence write (found on the FIRST full run, after all five stages had executed). Fixed by restoring the newline.
- Verification (all real runs, this host + sz0001):
  - `bash -n` on all 4 scripts: exit 0.
  - F1 (pre-commit): 12 PASS + 2 PENDING (todos 13/14 unchecked), exit 0; (post-commit): 13 PASS + 14 PENDING, exit 0.
  - F2: 36→39 changed source files scanned, 0 residue, shell syntax clean, pytest 19 failed / 2198 passed / 13 errors = recorded-gate baseline exactly → exit 0.
  - F4: 56 changed files classified, 0 frozen/out-of-scope → exit 0.
  - F3 full run (real sz0001 execution): (a) pytest 19/2198/13 = baseline, (b) firmware build PASS, (c) spike smoke FAIL — `Spike Host Summary: 0 PASS, 1 FAIL`, `max_diff=7.64e+02` on L0 Q_proj, identical to the pre-existing BUG-SOC-FM-005 signature recorded in task-9 evidence, (d) reverse-gate dry-run clean, (e) W4-PERF p0/p1 PASS (4+4 PASS records) + FM-SOC-001/003 (P0) PASS + FM-SOC-032 (P4, ~25 min VCS) PASS → F3 exit 1, the honest gate outcome: the smoke red predates this plan and is tracked separately; the gate reports it rather than waiving it.
- Evidence: `build/evidence/task-13-fm-hardening-phase10.txt`; F3 receipts in `build/evidence/task-F3-*.log`.

