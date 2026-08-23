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
