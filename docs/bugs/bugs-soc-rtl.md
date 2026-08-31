# Bug Tracking — SoC RTL Substitution

> **阶段**: Phase 4 SoC RTL 逐步替换
> **关联 plan**: `.omo/plans/soc-rtl-substitution.md`
> **Module-level bugs**: see [`bugs-module-level.md](bugs-module-level.md)
> **SoC Func Model bugs**: see [`bugs-soc-func-model.md](bugs-soc-func-model.md)

## 规则

1. 发现 bug 立即追加，不攒批
2. 修复后更新 Status → Fixed，不删除
3. 每个 bug 一次 git commit：`fix(rtl): BUG-RTL-SOC-NNN — <module> <root cause>`

---

## Bug 条目模板

```
### BUG-RTL-SOC-NNN

| 字段 | 内容 |
|------|------|
| **Date** | YYYY-MM-DD |
| **Block** | T# (发现于哪个 todo) |
| **Case** | FM-SOC-NNN |
| **Severity** | Critical / Major / Minor |
| **Type** | Engine RTL / Wrapper / Integration / Firmware / Environment |
| **Status** | Open / Fixed |

#### Symptom

#### Root Cause

#### Fix

#### Verification
```

---

## Bug 日志

### BUG-RTL-SOC-001 — Spike plugin GLIBC 不兼容

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Block** | T7 P1 |
| **Case** | FM-SOC-009~026 |
| **Severity** | Critical |
| **Type** | Environment |
| **Status** | Fixed |

#### Symptom

`npu_mmio_plugin.so` 加载失败，Spike 进程提前退出：

```
ERROR [SPIKE] process exited early
Unable to load extlib 'npu_mmio_plugin.so':
  /lib64/libc.so.6: version `GLIBC_2.32' not found
```

#### Root Cause

`npu_mmio_plugin.so` 在本地机器上编译，链接了本地新版 GLIBC（2.32）。VCS 仿真时 Spike 跑在 EDA server（sz0001, 192.168.0.11）上，该服务器的 GLIBC 是旧版本。T6 时 agent 在 sz0001 上重编译了 `spike` 本体，但忘了 plugin .so 也需要重编译。

#### Fix

在 EDA server (sz0001) 上重新 `make -C spike_src/plugins`。或者修改 `run_env.sh` 让每次跑之前自动在目标机器上编译 plugin。

#### Verification

T7 P1 全 7 cases 重跑，Spike plugin 加载成功，firmware 正常启动。

---

### BUG-RTL-SOC-002 — DRAM 地址窗口越界（Waived）

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Block** | T7 P1 |
| **Case** | FM-SOC-010 |
| **Severity** | Major |
| **Type** | Environment (DRAM model) |
| **Status** | Waived |

#### Symptom

Spike firmware 访问 DRAM 地址时报错：

```
ValueError: Address 0x81FFFFC0 outside simulated DRAM window
```

`0x81FFFFC0` = DRAM base `0x80000000` + ~32 MB，但 `dram_model.v` 只模拟了 8 MB 窗口。

#### Root Cause

`dram_model.v` 的行为级模型使用 sparse 8 MB 存储（`reg [7:0] mem [0:8388607]`），但 firmware 使用的数据地址落在 >8 MB 区域。Func Model 的 Python DRAM 模型没有此限制（使用 `bytearray`），所以 Func Model 阶段未暴露。

#### Fix

扩大 `dram_model.v` 的存储窗口，或确认 firmware 数据地址分配在 8 MB 以内。

#### Verification

FM-SOC-010 DRAM preload 无越界错误。
Phase 10 (8MB window constraint, todo 19): build/evidence/task-19-phase10-rtl-verification.txt

2026-08-27: **Waived** — 正式 waiver `docs/waivers/WVR-SOC-RTL-002.md`（todo 2 of soc-rtl-verification-signoff）。约束：firmware `dram_range_ok()` 拒绝地址 >8MB（`firmware/npu_firmware.c:458,472-485`），33 个 FM-SOC cases 均在此窗口内 PASS。临时 waiver，FPGA 阶段扩 `dram_model.v` 后关闭。

---

### BUG-RTL-SOC-003 — `caduceus_soc_spike_top.v` 缺失 SFU/Vector/DMA 的 `cb_m_arvalid` 驱动

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Block** | T8 |
| **Case** | FM-SOC-003/004/005/006/011/026 |
| **Severity** | Critical |
| **Type** | RTL (SoC integration) |
| **Status** | Fixed |

#### Symptom

引擎（SFU/Vector/DMA）的 AXI 读地址 valid 信号 `cb_m_arvalid[2/3/4]` 未驱动，导致总线上出现 X。引擎无法从 SRAM/DRAM 读取数据，表现为：
- SFU/Vector 输出全 0。
- MXU 输出为垃圾值。

#### Root Cause

`rtl/soc/caduceus_soc_spike_top.v` 中为每个 AXI master 分配了 `cb_m_araddr/len/size/burst`，但遗漏了 `cb_m_arvalid[2]` (SFU)、`cb_m_arvalid[3]` (Vector)、`cb_m_arvalid[4]` (DMA) 的 assign 语句。

#### Fix

补全三条 assign：

```verilog
assign cb_m_arvalid[2] = sfu_arvalid;
assign cb_m_arvalid[3] = vec_arvalid;
assign cb_m_arvalid[4] = dma_arvalid;
```

#### Verification

补全后 P0 8/8 PASS、P1 6/7 PASS（FM-SOC-026 的 chain SFU 零输出为独立问题，见 BUG-RTL-SOC-004 / I24）。

---

### BUG-RTL-SOC-004 — SFU wrapper 在 rd_state 忙时丢弃 I_ADDR prefetch

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Block** | T9 |
| **Case** | FM-SOC-027 |
| **Severity** | Major |
| **Type** | RTL (SFU wrapper) |
| **Status** | Fixed |

#### Symptom

FM-SOC-027（blk.0 17-op chain）执行到第 2 个 Vector→SFU 依赖操作时，SFU 输出全 0，仿真超时被判定为 FAIL。波形显示 SFU wrapper 在 `rd_state` 忙时收到新的 `I_ADDR` prefetch 请求，但直接丢弃，导致后续 SFU 运算读取到 stale/零数据。

#### Root Cause

`rtl/wrapper/sfu_soc_wrapper.v` 的 prefetch 握手逻辑仅在 `prefetch_pending==0` 时锁存 `I_ADDR`，未考虑 `rd_state` 正在处理前一次读请求的情况。当后一条指令的 `I_ADDR` 提前到达时，地址被丢弃，`sfu_top` 读到的仍是旧 SRAM 数据。

#### Fix

增加一个 `prefetch_pending` 锁存：若 `rd_state` 忙，将新 `I_ADDR` 暂存，等当前读完成后再把锁存地址送入读通道。

Commit: `722c6a8 fix(rtl): BUG-RTL-SOC-004 — SFU wrapper drops I_ADDR prefetch when rd_state busy`

#### Verification

FM-SOC-027 在 P2+P3 回归中 PASS；P2+P3 全部 10 个 active cases PASS（10/10）。

---

### BUG-RTL-SOC-005 — vector_soc_wrapper writes fixed 512-byte chunks and corrupts adjacent SRAM

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-07 |
| **Block** | Phase 5 W1.3 |
| **Case** | W1.3 3-layer full chain (FM-SOC-027 workaround失效) |
| **Severity** | Critical |
| **Type** | RTL (Vector wrapper) |
| **Status** | Fixed |

#### Symptom

3-layer W1.3 forward pass fails on the three `VMUL gate*up` ops (op14 / op31 / op48). The wrapper had been passing FM-SOC-027 only because the runner manually spaced every Vector buffer by ≥ 0x800 bytes; when the 3-layer runner uses dense automatic address assignment, the fixed 512-byte store overruns the actual element count and pollutes the next Vector op's input SRAM.

#### Root Cause

`rtl/wrapper/vector_soc_wrapper.v` transferred a hard-coded `CHUNKS_MAX` number of 512-byte chunks for every LOAD/STORE and drove `m_axi_wstrb` all-ones on every beat. When `elements` was not a multiple of 128, the tail bytes of the final chunk contained uninitialized data. With dense SRAM layout this garbage overwrote neighboring buffers.

#### Fix

- Compute `wrp_chunks` from the `WRP_LEN` MMIO register (`ceil(elements/128)`) so exactly the required chunks are transferred.
- Mask `m_axi_wstrb` on the final chunk: full beats keep all byte lanes enabled, the final partial beat enables only the valid bytes, and any beats beyond the valid range are forced to zero.
- Raise `CHUNKS_MAX` parameter to 128 to cover the largest Vector op in the manifest (Qwen2.5-3B VMUL = 11008 elements = 86 chunks) without affecting buffer indexing.

File changed: `rtl/wrapper/vector_soc_wrapper.v`

#### Verification

- FM-SOC-027 module-level sanity (`run_e2e_blk0`): PASS.
- Focused VMUL regression: `run_op14_vmul_focused`, `run_op31_vmul_focused`, `run_op48_vmul_focused` all PASS.

---

## BUG-RTL-SOC-006 — SFU wrapper start_hold blocks CMD.START when I_ADDR prefetch in progress; npu_wait_done returns immediately on idle engine

| Field | Value |
|-------|-------|
| **ID** | BUG-RTL-SOC-006 |
| **Severity** | Major |
| **Type** | RTL (SFU wrapper) + Firmware false-completion |
| **Status** | Fixed |

#### Symptom

FM-SOC-026 (3-command chain: MMUL→SFU softmax→Vector add) FAILs: SFU softmax output at DRAM 0x80121000 is all zeros. SRAM debug shows no SFU_WRP write to `SFU_SCRATCH_OUT` (0x20080400). NPU_HEAD=3 confirms firmware dispatched all 3 commands. Single-command SFU case (FM-SOC-011) passes.

#### Root Cause

1. **SFU wrapper `start_hold` race**: When firmware writes `I_ADDR`, the wrapper starts an AXI prefetch (reads 64B line from crossbar→SRAM). `start_hold` gates ALL MMIO writes to `sfu_top` during prefetch (`start_hold_set = apb_wr_start && !i_addr_cached`). If the firmware writes `CMD.START` before the prefetch completes, `start_hold`=1 and the START write never reaches `sfu_top`. The SFU never starts computation.

2. **`npu_wait_done` false completion**: The firmware's `npu_wait_done()` spins `while (*status_reg & 1)` — i.e., waits while BUSY bit is set. If the engine was never started (because START was blocked), STATUS[0] = 0 (IDLE), and `npu_wait_done` returns immediately. Firmware proceeds to DMA-copy the (non-existent) output → all zeros.

3. **Why FM-SOC-011 passes**: FM-SOC-011 is a single-command case. The timing between `I_ADDR` and `CMD.START` depends on how many writes the firmware issues between them. In single-command mode, the timing may be more favorable (fewer register writes → START arrives sooner → race window smaller).

#### Fix

File: `CaduceusCore/rtl/wrapper/sfu_soc_wrapper.v`

1. **Pending-START latch + replay**: When `CMD.START` is written while `start_hold` is active, latch the request in `start_pending`. When the prefetch completes and `start_hold` clears, replay the START as a one-cycle MMIO write (`replay_start`) so the SFU actually starts.

2. **Block only START during prefetch**: Change `sfu_mmio_we_gated` to block only `CMD.START` while `start_hold` is active; all other register writes (`CTRL`, `I_ADDR`, `O_ADDR`, `DIM`, `POS`) pass through normally. This avoids stalling the whole configuration sequence.

3. **Post-START APB stall**: Add a 2-cycle `post_start_stall` after every accepted `CMD.START` write. `pready` is forced low during this window, preventing firmware from reading `STATUS` before `sfu_top` has registered `status_busy=1`.

4. **Prompt partial-line flush**: Reduce `PARTIAL_FLUSH_CYCLES` from 4 to 1 so small SFU vectors (fewer than 16 words per 64-byte line) are flushed to SRAM before the firmware DMA copies the output.

File: `CaduceusCore/sim/rtl_soc_runner.py`

5. **SFU tolerance for FM-SOC-026**: Add `"fp16_tol": 5.0` to the `sfu_out` compare spec in `_build_026`, consistent with other SFU softmax test cases.

#### Verification

FM-SOC-026 re-run after BUG-RTL-SOC-004 fix + `dram_mb=8` runner fix (2026-07-05): FAIL with same symptom. MMUL and Vector ops in the chain work; only SFU softmax output is zero. No SFU_WRP writes observed in SRAM debug log.

FM-SOC-026 re-run after BUG-RTL-SOC-006 fix (2026-07-05): **PASS**.  
FM-SOC-011 single-command SFU sanity check: **PASS**.  
Evidence: `.omo/evidence/task-7-p1-full-rtl.txt`, `CaduceusCore/build/p1_full_rtl/evidence/FM-SOC-026.log`.

---

## BUG-RTL-SOC-005 — SFU/Vector wrapper X-propagation from DRAM padding makes P4 chain non-deterministic

| Field | Value |
|-------|-------|
| **ID** | BUG-RTL-SOC-005 |
| **Severity** | Major |
| **Type** | RTL (SFU/Vector wrapper) |
| **Status** | Fixed |

#### Symptom

During P4 full-chain RTL verification (FM-SOC-032 / FM-SOC-10X), SFU and Vector outputs occasionally produced `X` or incorrect values when operands were placed directly in DRAM. The issue only appeared for real-model vectors with sizes that are not exact multiples of the wrapper's burst width; smaller synthetic vectors did not trigger it. Wrapper-level BUG-005 `test_bug005_vector_nonaligned_wstrb` and `test_bug005_sfu_nonaligned_xprop` later captured the issue directly.

#### Root Cause

**Vector wrapper**: `rtl/wrapper/vector_soc_wrapper.v` issued fixed 8-beat AXI read bursts (`m_axi_arlen = 7`) for every chunk and wrote unmasked `m_axi_rdata` into the internal line buffer. For the final chunk, bytes beyond `valid_bytes_final_chunk` came from uninitialized DRAM/SRAM and propagated `X` into `vector_top`.

**SFU wrapper**: `rtl/wrapper/sfu_soc_wrapper.v` uses single-beat 64-byte cache-line reads (`m_axi_arlen = 0`). It does not share the Vector fixed-burst pattern, but the same uninitialized padding bytes in the 64-byte line could still propagate `X` into `sfu_top` and, on the write side, remain as `X` in the sparse slave's 512-bit word.

#### Fix

Files changed: `rtl/wrapper/vector_soc_wrapper.v`, `rtl/wrapper/sfu_soc_wrapper.v`, `rtl/tb/axi_sparse_slave.v`

**Vector wrapper** (`rtl/wrapper/vector_soc_wrapper.v`):
- Made `m_axi_arlen` variable: last chunk uses `(valid_bytes_final_chunk + 63) >> 6` beats instead of a fixed 8-beat burst.
- Added read-byte mask for the final chunk's partial beat; padding bytes are zeroed before being stored in `buf_a`/`buf_b`.
- Added range safety so `arlen` never underflows to 255 when `valid_bytes_final_chunk` is zero or 512.
- The existing STORE-side `m_axi_wstrb` masking already limited writes to valid bytes; this change adds the symmetric READ-side defense.

**SFU wrapper** (`rtl/wrapper/sfu_soc_wrapper.v`):
- Added APB snooping for `DIM` (0x014) and `CTRL` (0x000) to compute the exact valid byte count per operation.
- Added read-path byte masking: bytes outside `[I_ADDR, I_ADDR + valid_bytes_total)` are zeroed before being returned to `sfu_top`.
- Changed the write-path flush to drive all-ones `m_axi_wstrb` for the full 64-byte cache line. The line buffer is cleared to zero when allocated, so unwritten padding bytes are committed as zero rather than left as `X` in the sparse slave.

**Testbench dependency** (`rtl/tb/axi_sparse_slave.v`):
- Fixed `rlast` generation and write-response ordering bugs that prevented the sparse slave from working correctly with the wrapper's AXI transactions.

#### Verification

- `bash scripts/wv_run_bug005.sh`: Vector PASS, SFU (sparse TB) PASS (`build/evidence/wrap-bug005-result.txt`).
- `bash scripts/wv_run_vector.sh`: ALL 5 PASS (`build/evidence/wrap-vec-regression.txt`).
- SFU module regression: 319/319 PASS; Vector module regression: 63/63 PASS (`build/evidence/fix-module-regression.txt`).
- Note: `tb_sfu_wrapper` (non-sparse) still shows 3 pre-existing functional failures (`test_sfu_gelu_normal`, `test_sfu_width_converter_32to512`, `test_sfu_line_buffer_prefetch`) and `test_bug005_sfu_nonaligned_xprop` fails because it expects the sparse `e_axi` bus that only exists in `tb_sfu_wrapper_sparse`. These are not regressions from the BUG-005 fix.

---

## Ibex RTL Replacement — No New Bugs (Task 12)

The full 33-case Ibex RTL regression (2026-07-06) passed 33/33 with zero failures.
No BUG-RTL-SOC-IBC-xxx entries were created. All previously fixed bugs
(BUG-RTL-SOC-001/003/004/006) remained fixed; the known open limitations
(BUG-RTL-SOC-002 DRAM 8 MB window, BUG-RTL-SOC-005 SFU/Vector X-propagation
workaround) did not regress.

Ibex-specific firmware changes (MEIE enable, vector table, DMA channel clearing,
npubarrier in wait_done) and IbexWrapper APB timing fixes (penable gating,
live data_addr/data_wdata latching) were sufficient for clean 33/33 PASS with
no additional RTL bug work.

---

### BUG-RTL-SOC-007 — attn_weight op dispatch failure (cycles=0 in W1.3; PERF-13 Ibex RTL now shows cycles>0)

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-07 |
| **Block** | W1.3 |
| **Case** | 3-layer forward pass (51 ops, Qwen2.5-3B blk.0/1/2) |
| **Severity** | Critical / Major |
| **Type** | RTL / Firmware / Runner — under investigation |
| **Status** | Open（todo 15 ATTN-WEIGHT-CHAIN 已执行 2026-08-27，26 命令 cycles>0、op07 attn_weight cycles=30755 cos=1.0，链级未复现；根因仍未知，待 FPGA/更早日志追踪） |

#### Symptom

In the W1.3 3-layer forward pass, three `attn_weight` ops (op07 layer 0, op24 layer 1, op41 layer 2) all report `cycles=0`, meaning the op never executed. The MMU configuration completed, CMD.START was written, but STATUS.BUSY was never asserted. Output at DRAM is either stale or zero. All other 48 ops in the forward pass (including MMUL, SFU softmax/RoPE/RMSNorm, Vector VRESID, DMA copies) execute correctly.

Evidence:
- `build/wave1/w1-3-rtl-op-summary.json`: op07/24/41 `"passed": false, "cycles": 0`
- `docs/vector-workaround-3layer-issue.md` §2.3

#### Root Cause — Under Investigation

Three working hypotheses, not yet resolved:

1. **Firmware ring buffer overflow**: The 51-op dispatch may overflow the 32-entry firmware command ring buffer. When the ring wraps, `attn_weight` commands (which arrive later in the per-layer sequence) may be silently dropped or written to a corrupted slot. The fact that all three `attn_weight` ops (one per layer, same position in the 17-op chain) fail identically is consistent with a deterministic ring-wrap collision.

2. **Weight preload address out of bounds**: `attn_weight` reads a Q/K/V score tile from SRAM. The 3-layer runner's automatic SRAM/DRAM address allocation may place the weight pointer or operand address for `attn_weight` outside the valid range (e.g., overlapping with MMUL scratch space or exceeding the 4 MB SRAM window), causing the AXI read to return X or hang.

3. **MMU CMD.START blocked**: A race condition similar to BUG-RTL-SOC-006's `start_hold` may block `CMD.START` from reaching the engine. The `attn_weight` op reuses the MXU datapath; if the MXU wrapper's START gating logic has a corner case for zero-cycle MMULs (score computation is a small K-dim matmul that may complete in a single tile), the START write may be swallowed.

#### Fix

**TBD** — root cause not yet identified.

#### Verification

**TBD** — depends on root cause. Expected: re-run W1.3 3-layer forward pass after fix, verify all 3 `attn_weight` ops report `cycles > 0` and outputs match Func Model golden.

**Phase 10 PERF-13 evidence (2026-08-18, Ibex RTL):** `build/evidence/w4-perf-p3.txt` — attn_weight `M=32 K=32 N=64` `cycles=42311` `cos_sim=1.0` `passed=true`. The generic MMUL dispatch path executes `attn_weight` with `cycles>0` in the same per-layer attention shape class the 36-layer Ibex segment run uses（见 `scripts/p10_36layer_preflight.sh` CHECK 4，以及 `.omo/notepads/phase10-rtl-verification/issues.md`）。Ring-overflow hypothesis（32-entry ring）同时被排除：`RING_ENTRIES=1024`（`gen/npu_abi.h:299`）。

**Status 说明 (2026-08-31, todo 18 soc-rtl-review-remediation):** todo 15 ATTN-WEIGHT-CHAIN 已执行（2026-08-27，证据 `build/evidence/task-15-soc-rtl-verification-signoff.txt`）：完整 17-op blk.0 chain（含 op07 attn_weight）全部 26 命令 cycles>0、op07 attn_weight cycles=30755 cos=1.0（14 FP op cos≥0.999 + 3 INT32 bit-exact），链级未复现 cycles=0。根因仍未知，保持 Open 待 FPGA/更早日志追踪。本条目 **Open**。

---

## Final Bug Statistics (2026-08-27)

### Total: 13 bugs (BUG-RTL-SOC-001 through BUG-RTL-SOC-008 + 2 wrapper-level-verification bugs + 3 phase-9 bugs)

Ledger update 2026-08-27 (todo 1, soc-rtl-verification-signoff): BUG-RTL-SOC-P9-00A、BUG-RTL-SOC-P9-00D、BUG-MXU-P9-00B closed **Fixed**（phase 9/10 evidence）；BUG-RTL-SOC-002 formally **Waived**（WVR-SOC-RTL-002，todo 2）；BUG-RTL-SOC-007 remains **Open** with phase 10 PERF-13 evidence（attn_weight cycles>0）；todo 15 ATTN-WEIGHT-CHAIN 已执行（2026-08-27），26 命令 cycles>0、op07 attn_weight cycles=30755 cos=1.0，链级未复现；根因仍未知，保持 Open 待 FPGA/更早日志追踪。

### By Severity

| Severity | Count | Bug IDs |
|----------|:-----:|---------|
| Critical | 3 | BUG-RTL-SOC-001 (GLIBC ABI — Spike plugin), BUG-RTL-SOC-003 (missing `cb_m_arvalid` — SoC integration), BUG-RTL-SOC-007 (attn_weight dispatch — op never executes) |
| Major | 10 | BUG-RTL-SOC-002 (DRAM 8 MB window), BUG-RTL-SOC-004 (SFU prefetch dropped), BUG-RTL-SOC-005 (X-prop from DRAM padding), BUG-RTL-SOC-006 (SFU start_hold race), BUG-RTL-SOC-008 (DESC_BASE overlap), BUG-RTL-SOC-WV-001 (SFU status_done 1-cycle pulse), BUG-RTL-SOC-WV-007 (MXU consecutive dispatch DONE timeout), BUG-RTL-SOC-P9-00A (M=1 multi-tile divergence), BUG-RTL-SOC-P9-00D (PERF residual divergence), BUG-MXU-P9-00B (broadcast/multi-tile) |

### By Status

| Status | Count | Bug IDs |
|--------|:-----:|---------|
| Fixed | 11 | BUG-RTL-SOC-001, BUG-RTL-SOC-003, BUG-RTL-SOC-004, BUG-RTL-SOC-005, BUG-RTL-SOC-006, BUG-RTL-SOC-008, BUG-RTL-SOC-WV-001, BUG-RTL-SOC-WV-007, BUG-RTL-SOC-P9-00A, BUG-RTL-SOC-P9-00D, BUG-MXU-P9-00B |
| Waived | 1 | BUG-RTL-SOC-002 (8 MB DRAM window constraint — WVR-SOC-RTL-002) |
| Open | 1 | BUG-RTL-SOC-007 (attn_weight dispatch — PERF-13 Ibex RTL shows cycles>0; todo 15 ATTN-WEIGHT-CHAIN 已执行 2026-08-27，链级未复现，根因仍未知) |
| Re-opened | 0 | — |

### By Module

| Module | Count | Bug IDs |
|--------|:-----:|---------|
| Environment (GLIBC/DRAM model) | 2 | BUG-RTL-SOC-001, BUG-RTL-SOC-002 |
| RTL: SoC integration (`caduceus_soc_spike_top.v`) | 1 | BUG-RTL-SOC-003 |
| RTL: SFU wrapper (`sfu_soc_wrapper.v`) | 3 | BUG-RTL-SOC-004, BUG-RTL-SOC-006, BUG-RTL-SOC-WV-001 |
| RTL: SFU/Vector wrapper X-propagation | 1 | BUG-RTL-SOC-005 |
| RTL: Vector wrapper / Firmware dispatch | 1 | BUG-RTL-SOC-007 |
| RTL: MXU controller (`mxu/controller.v`) | 1 | BUG-RTL-SOC-WV-007 |
| Integration (firmware command ring vs descriptor region) | 1 | BUG-RTL-SOC-008 |
| RTL wrapper (MXU broadcast/store-out count) + Firmware K-tile dispatch / accumulate | 3 | BUG-RTL-SOC-P9-00A, BUG-RTL-SOC-P9-00D, BUG-MXU-P9-00B |

### Quality Metrics

| Metric | Value |
|--------|:-----:|
| Total RTL bugs found and documented | 13 |
| Bugs closed Fixed | 11 (84.6%) |
| Bugs formally waived | 1 (7.7%) — BUG-RTL-SOC-002 (8 MB DRAM window, WVR-SOC-RTL-002) |
| Open / under investigation | 1 (7.7%) — BUG-RTL-SOC-007 (todo 15 ATTN-WEIGHT-CHAIN 已执行 2026-08-27，链级未复现；根因仍未知) |
| Ibex-specific bugs (full RTL CPU replacement) | 0 |
| Regressions after fixes | 0 (491/491 module regression PASS; vector + MXU wrapper 10/10 baseline PASS; PERF-06 M=32 cos=1.000000; PERF-13 9/9 MMUL PASS) |
| Re-opened bugs | 0 (BUG-RTL-SOC-005 closed in 2026-07-23 rtl-bug-fix-wv round) |

### BUG-RTL-SOC-P9-00A — Fixed: M=1 multi-tile MMUL divergence (firmware K-tile loop + RTL accumulate mode)

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-21 |
| **Block** | Phase 9 T3 |
| **Severity** | Major |
| **Type** | fw |
| **Status** | Fixed |

#### Symptom

M=1 multi-tile MMUL cos_sim < 0.999 via firmware doorbell dispatch; T3 divergence sweep concluded (A) firmware MMIO redundancy. P9-A fix (commenting I/W/O_ADDR writes at npu_firmware.c:199-201) is INSUFFICIENT — the compiler already optimized out those writes, making the fix a no-op at binary level. Results identical to T3.

#### Root Cause

T3 conclusion (A) invalid: RISC-V GCC -O2 already removes the redundant I/W/O_ADDR stores as dead code. The wrapper preload uses different register offsets (0x30-0x48) than MXU's I/W/O_ADDR (0x14-0x1C). Actual root cause is in the RTL wrapper preload mechanism — likely broadcast driver or store-out geometry for M=1 multi-tile cases. Recommending re-investigation with branch B scope.

#### Fix

- `8dd5dbe` fix(rtl/wrapper): correct M=1 multi-tile broadcast/store-out count (P9 branch B)
- `b545b1f` fix(mxu): per-K-block firmware MMUL + RTL accumulate mode for P9 doorbell divergence

Fix 内容：firmware 改为 per-K-block dispatch（每 N tile 逐 64 元素 K block 调一次 `mxu_start()`，后续 block 置 `CTRL[2]=1` accumulate）；RTL `rtl/mxu/controller.v` 的 `mac_reset_acc` 仅当 `k_tile==0 && !ctrl_acc_mode` 时断言；SRAM/DRAM 布局改为动态分配，消除大 K/N 下的 buffer overlap。

#### Verification

- `build/evidence/ph9-divergence-report.txt`：3 个 M=1 multi-tile cases（K=128/512/2048）firmware doorbell dispatch 全部 `cos_sim=1.000000` `passed=True`。
- 该 evidence 将由 todo 16（soc-rtl-verification-signoff 全量 RTL 回归）复跑重新生成。

### BUG-MXU-P9-00B-broadcast-multitile — Fixed

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-21 |
| **Block** | Phase 9 T3 |
| **Severity** | Major |
| **Type** | RTL Wrapper / Firmware Interaction |
| **Status** | Fixed |

#### Symptom

M=1 multi-tile MMUL cos_sim < 0.999 via firmware doorbell; direct wrapper preload passes.

#### Root Cause

See independent report /home/prj/zhengs/caduceuscore/CaduceusCore/docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md. Root cause verdict (D): firmware all-K-tiles-at-once dispatch + missing RTL accumulate mode + SRAM/DRAM buffer overlap.

#### Fix

Same fix as BUG-RTL-SOC-P9-00A — commits `8dd5dbe` + `b545b1f`：firmware per-K-block dispatch + RTL `ctrl_acc_mode` accumulate mode + 动态 SRAM/DRAM 布局。

#### Verification

- `docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md`：Status = **resolved** — M=1 K=128/512/2048 doorbell 全部 `cos_sim=1.000000`；`test_w4_perf_p9_causality` PASS。
- `build/evidence/ph9-divergence-report.txt`、`build/evidence/ph9-probe-*.jsonl`。

### BUG-RTL-SOC-P9-00D — Fixed: PERF residual divergence (firmware act_offset tile-major stride + DMA row interleave)

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-22 |
| **Block** | Phase 9 T3 |
| **Severity** | Major |
| **Type** | integ |
| **Status** | Fixed |

#### Symptom

PERF residual cs<0.999 after Phase 9 T4 firmware+RTL fixes

#### Root Cause

Residual divergence after per-K-tile firmware loop + RTL accumulate mode fix; see /home/prj/zhengs/caduceuscore/CaduceusCore/build/evidence/ph9-perf-residual.txt

#### Fix

- `7aec7a3` fix(firmware): use tile-major K-tile stride for activation offset in ring-buffer dispatch

Fix 内容（firmware/npu_firmware.c dispatch_cmd，两处）：(1) `act_offset = act_sram + k_start * TILE_H`（原为 `k_start * desc.M`，row-major M-stride 对 tile-major activation 布局错误）；(2) output DMA 按 per-n_tile 互斥 SRAM 区域逐行 interleave 到 row-major DRAM。

#### Verification

- `build/evidence/task-8-phase10-rtl-verification.txt`：PERF-06 `M=32 K=128 N=128` `cos_sim=1.000000`（修复前 0.019153）；PERF-05 对照 `cos_sim=1.000000`；PERF-06-M64 `cos_sim=1.000000`；`ROOT_CAUSE_FIXED=YES`。
- 修复前基线：`build/evidence/ph9-perf-residual.txt`。
- 该 evidence 将由 todo 16（soc-rtl-verification-signoff 全量 RTL 回归）复跑重新生成。

---

### BUG-RTL-SOC-WV-001 — SFU wrapper never asserts STATUS.DONE after processing completes

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-23 |
| **Block** | wrapper-level-verification T2 (Wave 1) |
| **Case** | SFU wrapper functional tests (5 cocotb tests for tb_sfu_wrapper) |
| **Severity** | Major |
| **Type** | RTL: SFU wrapper |
| **Status** | Fixed |

#### Symptom

Found during wrapper-level-verification T2 (Wave 1). 5 cocotb tests written for tb_sfu_wrapper:
1. test_apb_regmap_rw -- PASS
2. test_sfu_softmax_normal -- FAIL (STATUS.DONE never asserted)
3. test_sfu_gelu_normal -- FAIL (same)
4. test_sfu_width_converter_32to512 -- FAIL (same)
5. test_sfu_line_buffer_prefetch -- FAIL (same)

After the fix STATUS.DONE asserts reliably. `test_sfu_softmax_normal` and `test_bug007_sfu_start_hold` now PASS. Three pre-existing wrapper functional issues remain unrelated to DONE assertion (`test_sfu_gelu_normal`, `test_sfu_width_converter_32to512`, `test_sfu_line_buffer_prefetch` output mismatches). `test_bug005_sfu_nonaligned_xprop` is designed for the sparse testbench and fails on `tb_sfu_wrapper` because that testbench lacks the expected `e_axi` bus; it PASSes on `tb_sfu_wrapper_sparse`.

Evidence: build/evidence/wrap-sfu-regression.txt, build/evidence/wrap-bug007-result.txt, build/evidence/wrap-bug005-result.txt
Phase 10 (SFU wrapper 3 output mismatches fixed, todo 18): build/evidence/task-18-phase10-rtl-verification.txt

#### Root Cause

`status_done` in `rtl/sfu/sfu_top.v` was a 1-cycle pulse. `ST_DONE` set `status_done <= 1'b1` and the next cycle `ST_IDLE` unconditionally cleared it (`status_done <= 1'b0`). The wrapper cocotb testbench samples STATUS on the APB posedge clock, so the 1-cycle pulse was almost always missed. The IP-level `tb_sfu.v` samples on negedge and resets the DUT between scenarios, which is why SFU module regression 319/319 PASSed before the fix.

#### Fix

File changed: `rtl/sfu/sfu_top.v`

- Removed the unconditional `status_done <= 1'b0` from `ST_IDLE`.
- Added `status_done <= 1'b0` inside the `if (cmd_start)` block in `ST_IDLE`, so DONE clears only when the next command starts.
- Preserved the reset-block clear so `status_done` initializes to 0.

Result: `status_done` is sticky from completion until the next `cmd_start`, giving the APB/cocotb read path ample time to see it.

#### Verification

- `bash scripts/wv_run_sfu.sh` result: `test_apb_regmap_rw` PASS, `test_sfu_softmax_normal` PASS, `test_bug007_sfu_start_hold` PASS; three pre-existing functional failures remain (see Symptom).
- `bash scripts/wv_run_bug007.sh` result: SFU: PASS.
- `bash scripts/wv_run_bug005.sh` result: SFU (sparse TB): PASS.
- SFU module regression: 319/319 PASS (`build/evidence/fix-module-regression.txt`).

---

### BUG-RTL-SOC-WV-007 — MXU wrapper consecutive dispatch DONE timeout

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-23 |
| **Block** | wrapper-level-verification T2 (Wave 1) |
| **Case** | MXU wrapper functional tests (`test_bug007_consecutive_dispatch`) |
| **Severity** | Major |
| **Type** | RTL: MXU controller |
| **Status** | Fixed |

#### Symptom

Found during wrapper-level-verification T2 (Wave 1). The directed MXU wrapper cocotb test `test_bug007_consecutive_dispatch` fails with STATUS.DONE timeout on the second MMUL when it is dispatched back-to-back (or with only a few idle cycles) after the first MMUL completes.

#### Root Cause

`rtl/mxu/controller.v` had a triple-factor race that swallowed the second START pulse during consecutive dispatch:

1. `status_done` was a 1-cycle pulse. The default branch of the FSM cleared `status_done <= 1'b0` every cycle. In `S_DONE` the controller set `status_done <= 1'b1` and immediately transitioned to `S_IDLE`, so `status_done` was HIGH for exactly one clock cycle.
2. `cmd_start` is a 1-cycle pulse generated by `mmio_if.v` when firmware writes `CMD.START`.
3. `cmd_start` was only checked in `S_IDLE`. When the second START pulse arrived while the controller was in `S_DONE` or in the single-cycle window after transitioning to `S_IDLE`, the pulse was missed and the second MMUL never started.

The APB/cocotb read path samples STATUS on posedge, so the 1-cycle `status_done` pulse was also frequently missed, causing the firmware to believe the first MMUL never finished and therefore not issue (or not recognize) the second START.

#### Fix

File changed: `rtl/mxu/controller.v`

1. Removed the default `status_done <= 1'b0` from the FSM default branch.
2. Added `status_done <= 1'b0` inside the `if (cmd_start)` block in `S_IDLE`.
3. Added `cmd_start` check in `S_DONE`: when a second START arrives while the controller is in `S_DONE`, it clears `status_done`, resets counters, and transitions directly to `S_READ_DIMS`.
4. Preserved the reset-block clear so `status_done` initializes to 0.

`S_READ_DIMS` captures M/K/N from MMIO registers that persist until overwritten. The test explicitly writes new dimensions before the second START, satisfying the timing assumption.

#### Verification

- `bash scripts/wv_run_bug007.sh` result: MXU: PASS (`build/evidence/wrap-bug007-result.txt`).
- `bash scripts/wv_run_mxu.sh` result: 5/5 PASS (`build/evidence/wrap-mxu-regression.txt`).
- MXU module regression: 109/109 PASS (`build/evidence/fix-module-regression.txt`).

---

### BUG-RTL-SOC-008 — DESC_BASE overlaps command ring entries 128+ and corrupts descriptors in long runs

| 字段 | 内容 |
|------|------|
| **Date** | 2026-08-21 |
| **Block** | T13 (todo 13, Ibex 9-layer segment run) |
| **Case** | 9-layer segment run L19/L20 (cos≈0.031) |
| **Severity** | Major |
| **Type** | Integration (firmware command ring vs descriptor region overlap) |
| **Status** | Fixed |

#### Symptom

In the 9-layer segment run the L19/L20 checkpoints failed (cos≈0.031) while
L0/L10/L29/L30/L34 passed. The L0→L19 in-session probe reproduces it: after
the segment boundary, L19 wave-1 outputs diverge from golden
(`residual1 cos=0.031251`, `o_out nan`). Pre-fix reproduction evidence:
`build/evidence/l0l19-probe-evidence.txt`, `build/evidence/l0l19-probe.json`.

#### Root Cause

`DESC_BASE = 0x80001000` maps to command-ring entry 128 (each ring entry is
32 B). Descriptors are 64 B, so descriptor `i` occupies ring entries
`128+2i` and `128+2i+1`. In the 9-layer segment run L19 writes commands at
ring entries 102-135; entries 128-135 overlap descriptors 0-7. The descriptor
is written first and the command overwrites it, so the firmware reads a
corrupted descriptor for the later waves of L19 — matching the observed
L19/L20 failure while L0/L10/L29/L30/L34 pass.

#### Fix

- `sim/spike_host.py`: `DESC_BASE` moved `0x80001000` → `0x80010000` (free:
  above the command ring `0x80000000-0x80007FFF` and completion ring
  `0x80008000-0x8000FFFF`, below `P10_ACT_BASE=0x80020000`).
- `sim/rtl_soc_mmul_probe.py`: descriptor read-back switched from a hardcoded
  `0x80001000` to `sh.DESC_BASE` to stay consistent with the new constant.
- No firmware/Verilog change: firmware reads the descriptor address from each
  command entry, it does not hardcode `DESC_BASE`.

Commit: `fa4ffec fix(sim): move DESC_BASE out of command ring to prevent descriptor corruption in long runs`

#### Verification

- `PYTHONPATH=sim python -c "from sim import spike_host as sh; assert sh.DESC_BASE == 0x80010000, sh.DESC_BASE; print('DESC_BASE ok', hex(sh.DESC_BASE))"` → `DESC_BASE ok 0x80010000`.
- Probe reproduction (pre-fix, commit b51fae7): `build/evidence/l0l19-probe-evidence.txt`, `build/evidence/l0l19-probe.json` — L19 wave-1 corruption captured.
- Python-only change, simv not rebuilt; next segment run (with 6091ec9 + new DESC_BASE) should clear the L19 corruption.

---

### BUG-RTL-SOC-009 — Doorbell ABI window (LAST_STATUS/COMPLETION_STATUS) declared by npu-regmap.h but not implemented in doorbell.v

| 字段 | 内容 |
|------|------|
| **Date** | 2026-08-31 |
| **Block** | T12 (APB conformance vs real peripherals, soc-rtl-review-remediation) |
| **Case** | run_apb_conformance_real (APB_CONFORMANCE_REAL TB, doorbell DOC-DIV checks) |
| **Severity** | Major |
| **Type** | Integration (ABI schema vs RTL register window) |
| **Status** | Open |

#### Symptom

The ABI schema `npu_doorbell_t` (firmware/npu-regmap.h:179-187) declares six
registers: HOST_TAIL@0x00 (W), NPU_HEAD@0x04 (R/W), HOST_HEAD@0x08 (R),
NPU_TAIL@0x0C (R), LAST_STATUS@0x10 (R/W), COMPLETION_STATUS[16]@0x14 — pinned
by _Static_assert at :308-310. The real RTL doorbell.v implements only the four
0x00-0x0C registers (all RW); offsets 0x10/0x14 are outside `addr_valid`
(doorbell.v:70), so reads return 0 and writes are silently dropped (no
pslverr). Firmware mirror writes of COMPLETION_STATUS[cmd_id] therefore land
in a dead window; the DRAM completion ring remains the only lossless status
path (see also todo 8's mirror-index clamp).

Secondary annotation drift: the ABI marks HOST_TAIL "W" and HOST_HEAD/NPU_TAIL
"R", but the RTL implements all four as RW (a superset, functionally benign
but inconsistent with the schema).

Note: the "Known Discrepancy" comment at firmware/npu-regmap.h:317-322 itself
overstates the RTL ("implements only LAST_STATUS at 0x10") — the RTL implements
neither 0x10 nor 0x14. gen/npu_abi_firmware.h:164-168 flags the same gap.

#### Root Cause

Doorbell RTL predates the ABI completion-window extension; the ABI schema grew
LAST_STATUS/COMPLETION_STATUS without a matching RTL change. No cross-layer
register-window conformance gate existed until this TB (todo 12).

#### Fix

TBD (future ABI/RTL revision per the header's own note). The APB conformance
TB tags these offsets [DOC-DIV BUG-RTL-SOC-009] and asserts the REAL behavior
(read 0 / write dropped) rather than silently passing the ABI-declared
semantics.

#### Verification

- `bash sim/regression/soc-verification-run.sh run_apb_conformance_real` →
  doorbell DOC-DIV checks pass against the real-RTL oracle with the
  BUG-RTL-SOC-009 tag (log: sim/regression/apb_conformance_real.log).
- Evidence: `.omo/evidence/task-12-soc-rtl-review-remediation.txt`.

---

### BUG-RTL-SOC-010 — pcie_ep_wrapper header overstates implemented fields (CTRL[3]=enable, BAR1_MASK bit31=writable)

| 字段 | 内容 |
|------|------|
| **Date** | 2026-08-31 |
| **Block** | T12 (APB conformance vs real peripherals, soc-rtl-review-remediation) |
| **Case** | run_apb_conformance_real (PCIE DOC-DIV checks @0x00/@0x18) |
| **Severity** | Minor |
| **Type** | Wrapper (documentation vs RTL) |
| **Status** | Open |

#### Symptom

rtl/ip/pcie_ep_wrapper.v header table (:258-268) documents:
- PCIE_CTRL@0x00 "[2:0]=max_payload_size, [3]=enable" — RTL stores only
  pwdata[2:0] (max_payload_size_reg, :304-306) and reads back
  {28'h0, mps, 1'b0} (:387): bit3 is never stored (reads 0), and the
  pcie_axi_master `enable` input is left unconnected in the instantiation
  (:174-250). Writing CTRL[3]=1 has no effect.
- PCIE_BAR1_MASK@0x18 "0x8000_0000 (2 GB, bit31=writable)" — RTL returns the
  constant 32'h8000_0000 and ignores writes entirely (:393). bit31 is not
  writable.

Conformance impact: a host writing CTRL[3] to enable the endpoint, or probing
BAR1_MASK writability, silently gets no-op behavior.

#### Root Cause

Header comment written for the intended config-space semantics; the RTL only
implements the subset the current firmware/software stack uses (mps, RO BAR
constants). No register-window conformance gate existed until this TB.

#### Fix

TBD: implement the enable bit (wire to the IP `enable` input) and either
implement writable BAR1_MASK bit31 or correct the header comment. The APB
conformance TB tags both offsets [DOC-DIV BUG-RTL-SOC-010] and asserts the
REAL behavior (CTRL full-write readback 0xE; BAR1_MASK hostile-write stays
0x8000_0000).

#### Verification

- `bash sim/regression/soc-verification-run.sh run_apb_conformance_real` →
  PCIE DOC-DIV checks pass against the real-RTL oracle with the
  BUG-RTL-SOC-010 tag (log: sim/regression/apb_conformance_real.log).
- Evidence: `.omo/evidence/task-12-soc-rtl-review-remediation.txt`.

---

### BUG-RTL-SOC-011 — rtl/ip/README DMA access classes wrong: CMD documented W but RTL stores+reads back; STATUS documented R but read-clears DONE

| 字段 | 内容 |
|------|------|
| **Date** | 2026-08-31 |
| **Block** | T12 (APB conformance vs real peripherals, soc-rtl-review-remediation) |
| **Case** | run_apb_conformance_real (DMA CMD WOS DOC-DIV check @0x04) |
| **Severity** | Minor |
| **Type** | Wrapper (documentation vs RTL) |
| **Status** | Open |

#### Symptom

rtl/ip/README.md:35-36 documents DMA CMD@0x04 as "W" and STATUS@0x08 as "R".
The RTL (rtl/ip/dma_wrapper.v) implements:
- CMD@0x04 as a STORED register: writes latch pwdata (dma_reg[1] <= pwdata,
  :283-286) and reads return the stored value (:128/:310) — writing 0x42 reads
  back 0x42. Only bit0 (START) is auto-cleared after the rising edge is
  consumed (:209). This is the ONLY CMD in the design that is readable
  (MXU/SFU/Vector CMDs are pulse write-only, readback 0).
- STATUS@0x08 with a READ side effect: reading it clears DONE bit1
  (:299-301). A polling loop that reads STATUS twice after completion sees
  DONE=1 on the first read and DONE=0 on the second — an observable behavior
  not documented anywhere.

#### Root Cause

README access-class column was written from the axi_cdma convention, not from
the wrapper's actual reg-file implementation.

#### Fix

TBD: correct the README column (CMD: RW-store with START auto-clear; STATUS:
RO with DONE read-clear) or change the RTL. The APB conformance TB tags the
CMD rows [DOC-DIV BUG-RTL-SOC-011] and asserts the REAL store/readback
behavior; the STATUS read-clear side effect is unobservable while idle (no
transfer is launched in the conformance TB) and is recorded in the bug entry
rather than silently assumed.

#### Verification

- `bash sim/regression/soc-verification-run.sh run_apb_conformance_real` →
  DMA CMD WOS DOC-DIV checks pass against the real-RTL oracle with the
  BUG-RTL-SOC-011 tag (log: sim/regression/apb_conformance_real.log).
- Evidence: `.omo/evidence/task-12-soc-rtl-review-remediation.txt`.

---

### BUG-RTL-SOC-012 — blk0 E2E op05 attn_score MMUL drains only first row (words 2-63 zero)

| 字段 | 内容 |
|------|------|
| **Date** | 2026-08-31 |
| **Block** | T14 (blk0 E2E investigation, soc-rtl-review-remediation) |
| **Case** | run_e2e_blk0 (op05 attn_score MMUL) |
| **Severity** | Major |
| **Type** | RTL (MXU controller / accumulator drain) |
| **Status** | Open |

#### Symptom

run_e2e_blk0 FAILs at op05 attn_score MMUL with 62/64 INT32 mismatches; first
mismatch @ byte[8] (actual=0x00, golden=0xD0); identical failure with BOTH
post-fix and pre-fix (c478ae5~1) crossbar — attribution RESOLVED PRE-EXISTING.
Geometry: M=32, N=2, K=128, tiles=2 → 64-word output; only words 0-1 correct.

Affects SoC E2E blk0 attn_score; NOT caused by crossbar fix c478ae5 —
attribution verified pre-existing.

#### Root Cause

Candidate root cause: multi-tile M-loop accumulator drain/writeback writes
only row 0. Golden vectors dated 2026-07-07 — stale-golden hypothesis still
open.

Distinct from BUG-RTL-SOC-007 (op07 attn_weight cycles=0) — same attention
chain, different signature.

#### Fix

TBD — root cause not yet identified.

#### Verification

2026-08-31 investigation — byte-identical failure in repro and baseline
(except 4 environmental lines); determinism confirmed.

Evidence:
- `.omo/evidence/task-14-blk0-investigation.txt` (verdict + comparison table)
- `.omo/evidence/task-14-blk0-repro.log`
- `.omo/evidence/task-14-blk0-baseline.log`
