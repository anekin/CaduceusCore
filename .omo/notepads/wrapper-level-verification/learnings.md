
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

### Script conventions

- All `wv_*.sh` scripts start with `#!/usr/bin/env bash` and `source "$(dirname "$0")/p9_lib/p9_sz0001.sh"` (reusing Phase 9 SSH wrapper).
- `wv_compile.sh` compiles via `p9_ssh` which does `cd $REPO_ROOT` before executing the remote command, so all paths in the remote command are relative to REPO_ROOT.
- `wv_bootstrap.sh` idempotent (skips existing skeletons).
- `build/evidence/wv-compile.log` captures the full compilation output.
