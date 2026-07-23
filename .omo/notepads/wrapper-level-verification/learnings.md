
## [2026-07-23 08:10] T1 scaffold

### Width/port decisions

- **APB ports**: Wrapper uses bare APB names (`psel`, `penable`, etc.). TB maps them to `apb_*` prefix for cocotbext-axi `ApbBus.from_prefix(dut, "apb")`. `apb_pstrb[3:0]` is declared and tied to `4'b0` at TB top level because cocotbext-axi `ApbBus._signals` requires `pstrb` even when the APB slave (wrapper) does not handle byte strobes.

- **AXI4 master ports**: Wrapper already uses `m_axi_*` prefix for all AXI4 master channels (AWID, AWADDR, AWLEN, AWSIZE, AWBURST, AWVALID, AWREADY, WDATA, WSTRB, WLAST, WVALID, WREADY, BID, BRESP, BVALID, BREADY, ARID, ARADDR, ARLEN, ARSIZE, ARBURST, ARVALID, ARREADY, RID, RDATA, RRESP, RLAST, RVALID, RREADY). TB passes them through as top-level ports so `AxiBus.from_prefix(dut, "m_axi")` works.

- **Wrapper parameters**: All three wrappers use `AXI_ID_WIDTH=8`, `AXI_ADDR_WIDTH=32`, `AXI_DATA_WIDTH=512` as defaults. SFU adds `SFU_ADDR_WIDTH=32`. Vector adds `VECTOR_W=4096, NUM_LANES=128, DATA_W=32, CHUNKS_MAX=128`. MXU adds `K_TILE_MAX=64, W_BUF_DEPTH=64, A_BUF_DEPTH=128`. TBs mirror these defaults.

- **MXU debug ports**: `mxu_soc_wrapper` exposes 8 debug outputs (`dbg_state[3:0]`, `dbg_compute_en`, `dbg_weight_load`, `dbg_activation_load`, `dbg_store_out`, `dbg_store_row[5:0]`, `dbg_compute_k[5:0]`, `dbg_tiles_completed[15:0]`). These are exposed at TB top level for cocotb monitoring in T4/T6.

- **Flie list**: `rtl/tb/wrapper.flist` lists all 3 wrappers + `apb_to_mmio` + all 3 engine RTL sets (SFU: 8 files, Vector: 7 files including `f16_to_i32.v`, MXU: 8 files) + `axi_sparse_slave.v`. `f16_to_i32.v` was initially missed and caused `[CFCILFBI]` during Vector compilation — added on second iteration.

### Compilation results

- All 3 TBs elaborate successfully with VCS V-2023.12-SP2 + cocotb VPI on sz0001.
- SFU: 6 modules recompiled, ~1.3s compile + 0.75s elab
- Vector: full compile (no incremental), ~0.77s
- MXU: 4/5 modules incremental, ~21.6s compile + 1.4s elab (pe.v dominates)

## [2026-07-23 10:30] T2 SFU wrapper functional tests

### Test coverage

5 cocotb tests written for `tb_sfu_wrapper`:
1. `test_apb_regmap_rw` — PASS
2. `test_sfu_softmax_normal` — FAIL (STATUS.DONE never asserted)
3. `test_sfu_gelu_normal` — FAIL (same)
4. `test_sfu_width_converter_32to512` — FAIL (same)
5. `test_sfu_line_buffer_prefetch` — FAIL (same)

### Root cause analysis

After extensive debug (AXI AR/R/AW channel monitoring, APB trace analysis),
the following was observed:

- APB register reads/writes work correctly (test_apb_regmap_rw PASS).
- AXI reads work: wrapper issues 64-byte cache-line prefetch reads,
  AxiRam responds with correct data. Verified via AR/R channel monitoring.
- AXI writes work: wrapper's write FIFO drains to AxiRam via AW/W/B
  channels. All 8 output lines for DIM=256 softmax are written by ~10us.
- SFU starts processing: STATUS.BUSY transitions to 1 after CMD.START.
- SFU produces output: all output data appears as AXI write bursts.
- **Critical**: STATUS.DONE is never asserted after output completion,
  even when waiting 5M cycles (50ms simulation time). This causes
  `wait_done` to time out for all SFU operation tests.

The SFU processes data and writes output correctly (verified by AXI AW
bursts at O_ADDR), but the internal DONE state transition never occurs.
This was verified for DIM=16, 25, 64, and 256 with both softmax and GELU.

The one exception: test_sfu_line_buffer_prefetch with DIM=25 did see
STATUS.DONE=1 in one test run, but the output data comparison failed
(max_abs_err=0.49, suggesting AxiRam returned zeros for some reads).

### Key findings

1. `wrapper_common.py` uses `apb._bus.clk` to access clock — but
   cocotbext-axi's `ApbMaster` does not expose `_bus` as a public
   attribute. Fixed by adding an optional `clk` parameter to `wait_done`
   and using `dut.clk` in `_sfu_configure_and_start`.

2. `wrapper_common.py` was updated with `clk` parameter to `wait_done`
   (backward-compatible — defaults to `apb._bus.clk` if not provided).

3. The wrapper's `start_hold` mechanism blocks CMD.START if I_ADDR is
   not yet cached. Writing CMD.START=1 in `test_apb_regmap_rw` was
   changed to CMD=0 to avoid triggering start_hold (which would hang
   the TB indefinitely without a valid I_ADDR cached).

4. 100-cycle delay added between I_ADDR write and CMD.START to ensure
   the cache-line prefetch completes before START is issued, avoiding
   the start_hold replay path which may not be correctly interpreted.

5. Per-test reset is added (`dut.rst_n` pulsed low for 10 cycles) to
   ensure clean SFU state across cocotb tests.

6. Unresolved issue: SFU completes processing and writes output
   but never asserts STATUS.DONE. This appears to be a wrapper/SFU
   integration issue. The SFU IP-level testbench (319/319 PASS)
   works correctly, suggesting the wrapper's APB/AXI glue layer
   introduces a timing or state-machine interaction that prevents
   the DONE transition. **Recommend**: log as `BUG-RTL-SOC-WV-001`
   and investigate in a dedicated bug-fix iteration.

### Script conventions

- All `wv_*.sh` scripts start with `#!/usr/bin/env bash` and `source "$(dirname "$0")/p9_lib/p9_sz0001.sh"` (reusing Phase 9 SSH wrapper).
- `wv_compile.sh` compiles via `p9_ssh` which does `cd $REPO_ROOT` before executing the remote command, so all paths in the remote command are relative to REPO_ROOT.
- `wv_bootstrap.sh` idempotent (skips existing skeletons).
- `build/evidence/wv-compile.log` captures the full compilation output.

## [2026-07-23 08:44] T4 MXU wrapper functional tests

### Test results

- **All 5 tests PASS**: `test_apb_regmap_rw`, `test_mxu_preload_single_tile`, `test_mxu_single_tile_compute`, `test_mxu_store_out_burst`, `test_mxu_accumulate_mode`.
- Evidence: `build/evidence/wrap-mxu-regression.txt` shows 5 PASS, 0 FAIL.

### Key implementation details

- **Data layout for AXI4 preload**: The wrapper reads weight and activation data from AxiRam via AXI4. Data must be formatted in K-step (broadcast cycle) order:
  - Weight (KxN INT4): `GoldenMXU.pack_int4(w.flatten()).tobytes()` -- row-major pack produces correct K-step order because `pack_int4` packs consecutively, and the 256-bit weight bus reads 64 4-bit weights per half-beat, matching 32 sequential packed bytes.
  - Activation (MxK INT8): Must be TRANSPOSED to K-step order. `a.T.astype(np.int8).tobytes()` gives K-step-major ordering where each 512-bit beat carries 64 activations (rows 0-63) for one K-step. Without transposition, the broadcast bus would receive wrong row assignments.
- **GoldenMXU integration**: `GoldenMXU.matmul_from_sram()` expects row-major activation layout (MxK) in a flat SRAM byte array with activations at offset 0 and packed weights at offset M*K. This matches the original module-level test vector format. The transposition is only needed for the AXI4-to-broadcast-bus data path in the wrapper tests.
- **Store-out geometry confirmed**: 2048-bit internal accumulator row splits into 4x512-bit AXI4 write beats. For N=64, awlen=3 (4 beats), awsize=6 (64B). Output bytes at OUT_BASE + r*256 contain row r's 64 INT32 values in contiguous LE order.
- **Accumulate mode**: K=128 with CTRL[2]=1 (acc_mode) produces identical results to single-shot GoldenMXU(K=128). Confirms cross-tile accumulator does NOT reset between K-tiles.
- **Preload FSM**: `WRP_STATUS.LOAD_DONE` asserts after weight+activation preload. `dbg_state` stays IDLE(0) during and after preload.

### Issues encountered and fixed

- **Module import path**: cocotb resolves `MODULE=sim.tests.wrapper.test_mxu_wrapper` from CWD (REPO_ROOT). Internal imports must use `from sim.tests.wrapper.wrapper_common import ...` -- relative `from wrapper_common import ...` fails because Python resolves it at the top level, not relative to the current package. Added try/except fallback for non-package execution.
- **`_write_to_ram` must be synchronous**: `AxiRam.write()` is a synchronous method, not a coroutine. Using `async def` caused `RuntimeWarning: coroutine was never awaited` and data was never written. Changed to plain `def`.
- **Unicode characters in strings**: cocotb on sz0001 uses ASCII locale. EN DASH (U+2013, `\u2013`) and MULTIPLICATION SIGN (U+00D7, `\xd7`) in docstrings/log strings cause `UnicodeEncodeError` during `logging.StreamHandler.emit()`. Replaced all with ASCII equivalents (`-` and `x`).
- **VCS daidir on NFS**: Incremental compilation daidir stored on NFS can cause `Failed to make rmapats.so` linking errors. Solution: `rm -rf <simv>.daidir <simv>` on remote side (`p9_ssh` command) before compilation ensures fresh build. Using `/tmp` daidir (`-Mdir`) failed because the simv binary hardcodes a relative path to the daidir and `cp` from /tmp to NFS was unreliable.
- **Grep pattern sensitivity**: The cocotb regression summary always contains "FAIL=0" even when all tests pass. The run script grep must match `TEST.*PASS` and `TEST.*FAIL` patterns specifically, not just any occurrence of "FAIL".

### Script patterns

- `wv_run_mxu.sh` compiles `tb_mxu_wrapper` with cocotb VPI if simv is missing (idempotent via `rm -rf` before compile).
- Individual test runs (one `simv` invocation per `TESTCASE`) for clean PASS/FAIL tracking.
- AxiRam `size=2**24` (16MB) used for addresses 0x00010000-0x0005FFFF.
- Wrapper MMIO base address registers (0x30/0x34/0x38) overridden via APB writes to use AxiRam-friendly addresses (not DRAM 0x8000xxxx).
