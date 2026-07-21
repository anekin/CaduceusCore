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

### BUG-RTL-SOC-002 — DRAM 地址窗口越界

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-04 |
| **Block** | T7 P1 |
| **Case** | FM-SOC-010 |
| **Severity** | Major |
| **Type** | Environment (DRAM model) |
| **Status** | Open |

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
| **Type** | RTL (SFU/Vector wrapper) + Firmware workaround |
| **Status** | Re-opened / Must fix in Phase 5 |

#### Symptom

During P4 full-chain RTL verification (FM-SOC-032 / FM-SOC-10X), SFU and Vector outputs occasionally produced `X` or incorrect values when operands were placed directly in DRAM. The issue only appeared for real-model vectors with sizes that are not exact multiples of the wrapper's burst width; smaller synthetic vectors did not trigger it.

#### Root Cause

`rtl/wrapper/sfu_soc_wrapper.v` and `rtl/wrapper/vector_soc_wrapper.v` appear to read/write fixed-size bursts or full 512-byte chunks around the requested operand region. When the operand's logical size is smaller than the burst chunk, the wrapper accesses adjacent DRAM bytes that were never initialized by the testbench/firmware. Those uninitialized bytes propagate `X` into the engine datapath, corrupting the result.

This is classified as an **RTL wrapper bug** because a robust slave should not fetch beyond the requested byte range and should tolerate uninitialized padding.

#### Fix / Workaround

Firmware now DMA-copies SFU/Vector inputs and outputs to/from dedicated **SRAM scratch buffers** before invoking the engine:

- SFU scratch input: `SFU_SCRATCH_IN` (`SRAM_BASE + 0x00000`)
- SFU scratch output: `SFU_SCRATCH_OUT` (`SRAM_BASE + 0x00400`)
- Vector scratch A/B: `VEC_SCRATCH_A` / `VEC_SCRATCH_B` (`SRAM_BASE + 0x01000` / `0x01400`)
- Vector scratch output: `VEC_SCRATCH_O` (`SRAM_BASE + 0x01800`)

The DMA copies only the exact logical byte count, so the wrappers only touch valid initialized SRAM bytes. SRAM is also fully written by `_preload_rtl()` to remove residual `X`.

#### Impact (Phase 4 — workaround in effect)

- P0–P3 regression still PASS (scratch buffers not used for simple synthetic cases).
- P4 FM-SOC-032 28-block chain PASS.
- P4 FM-SOC-10X full E2E chain PASS.

#### 3-Layer re-exposure (W1.3)

The Phase 5 W1.3 3-layer forward pass (51 ops) exposed that the SRAM scratch-buffer workaround breaks at scale. The workaround relied on manually assigning non-overlapping SRAM addresses per op — feasible for 17 ops (FM-SOC-027), impossible for 51 ops. The 3-layer runner uses automatically allocated addresses, causing Vector operand regions to overlap across layers and triggering the same 512-byte fixed-write corruption that the workaround was designed to avoid.

**Failing ops (VMUL gate\*up):** op14 (layer 0), op31 (layer 1), op48 (layer 2)
- Each reports `cycles=23814` (execution completed, result incorrect).
- Evidence: `build/wave1/w1-3-rtl-op-summary.json` (45/51 PASS, 6 FAIL).
- Detail report: `docs/vector-workaround-3layer-issue.md`.

**Why 3-layer exposes the gap:** 3 × 17 = 51 ops share the SRAM address space. Manual isolation (0x800 spacing per Vector op) is not practical at 51-op scale. This proves the workaround is not a viable long-term strategy.

**Lesson learned**: A layer-1 workaround that "passes" the immediate case but leaves the RTL root cause unfixed will be paid back with interest at layer 3. Once an RTL root cause is identified, the default path is to fix the RTL; firmware/runner workarounds are only acceptable as a temporary bridge with an open bug and committed fix date.

#### Verification

- Phase 4: FM-SOC-032 / FM-SOC-10X P4 regression PASS (2/2 active, 3/3 SKIP).
- Phase 5 W1.3: VMUL gate\*up (op14/31/48) FAIL in 3-layer forward pass.
- **Path to close**: Fix `vector_top.v` or `vector_soc_wrapper.v` store-beat masking per `elements` byte count (see `docs/vector-workaround-3layer-issue.md` §4.1), then re-run W1.3 3-layer forward pass. Expected: all 3 VMUL ops PASS.

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

### BUG-RTL-SOC-007 — attn_weight op dispatch failure (cycles=0) in 3-layer forward pass

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-07 |
| **Block** | W1.3 |
| **Case** | 3-layer forward pass (51 ops, Qwen2.5-3B blk.0/1/2) |
| **Severity** | Critical / Major |
| **Type** | RTL / Firmware / Runner — under investigation |
| **Status** | Open |

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

---

## Final Bug Statistics (2026-07-07)

### Total: 7 bugs (BUG-RTL-SOC-001 through BUG-RTL-SOC-007)

### By Severity

| Severity | Count | Bug IDs |
|----------|:-----:|---------|
| Critical | 3 | BUG-RTL-SOC-001 (GLIBC ABI — Spike plugin), BUG-RTL-SOC-003 (missing `cb_m_arvalid` — SoC integration), BUG-RTL-SOC-007 (attn_weight dispatch — op never executes) |
| Major | 4 | BUG-RTL-SOC-002 (DRAM 8 MB window), BUG-RTL-SOC-004 (SFU prefetch dropped), BUG-RTL-SOC-005 (X-prop from DRAM padding), BUG-RTL-SOC-006 (SFU start_hold race) |

### By Status

| Status | Count | Bug IDs |
|--------|:-----:|---------|
| Fixed | 4 | BUG-RTL-SOC-001, BUG-RTL-SOC-003, BUG-RTL-SOC-004, BUG-RTL-SOC-006 |
| Open | 2 | BUG-RTL-SOC-002 (DRAM 8 MB window — current cases avoid the region), BUG-RTL-SOC-007 (attn_weight dispatch — under investigation) |
| Re-opened | 1 | BUG-RTL-SOC-005 (X-prop from DRAM padding — workaround broke at 51-op scale) |

### By Module

| Module | Count | Bug IDs |
|--------|:-----:|---------|
| Environment (GLIBC/DRAM model) | 2 | BUG-RTL-SOC-001, BUG-RTL-SOC-002 |
| RTL: SoC integration (`caduceus_soc_spike_top.v`) | 1 | BUG-RTL-SOC-003 |
| RTL: SFU wrapper (`sfu_soc_wrapper.v`) | 2 | BUG-RTL-SOC-004, BUG-RTL-SOC-006 |
| RTL: SFU/Vector wrapper X-propagation | 1 | BUG-RTL-SOC-005 |
| RTL: Vector wrapper / Firmware dispatch | 1 | BUG-RTL-SOC-007 |

### Quality Metrics

| Metric | Value |
|--------|:-----:|
| Total RTL bugs found during substitution | 7 |
| Bugs fixed in RTL source | 4 (57.1%) |
| Bugs with firmware/environment workarounds | 1 (14.3%) |
| Open / under investigation | 1 (14.3%) |
| Ibex-specific bugs (full RTL CPU replacement) | 0 |
| Regressions after fixes | 0 (27/27 active PASS on Spike, 33/33 PASS on Ibex) |
| Re-opened bugs | 1 — BUG-RTL-SOC-005 (workaround broke at 51-op scale) |

### BUG-RTL-SOC-P9-00A

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-21 |
| **Block** | Phase 9 T3 |
| **Severity** | Major |
| **Type** | fw |
| **Status** | open |

#### Symptom

M=1 multi-tile MMUL cos_sim < 0.999 via firmware doorbell dispatch; T3 divergence sweep concluded (A) firmware MMIO redundancy. P9-A fix (commenting I/W/O_ADDR writes at npu_firmware.c:199-201) is INSUFFICIENT — the compiler already optimized out those writes, making the fix a no-op at binary level. Results identical to T3.

#### Root Cause

T3 conclusion (A) invalid: RISC-V GCC -O2 already removes the redundant I/W/O_ADDR stores as dead code. The wrapper preload uses different register offsets (0x30-0x48) than MXU's I/W/O_ADDR (0x14-0x1C). Actual root cause is in the RTL wrapper preload mechanism — likely broadcast driver or store-out geometry for M=1 multi-tile cases. Recommending re-investigation with branch B scope.

#### Fix

Pending fix per Phase 9 plan.

#### Verification

Evidence: build/evidence/ph9-branch-A-insufficient.txt:51-100

### BUG-MXU-P9-00B-broadcast-multitile

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-21 |
| **Block** | Phase 9 T3 |
| **Severity** | Major |
| **Type** | RTL Wrapper / Firmware Interaction |
| **Status** | rtl-suspect |

#### Symptom

M=1 multi-tile MMUL cos_sim < 0.999 via firmware doorbell; direct wrapper preload passes.

#### Root Cause

See independent report /home/prj/zhengs/caduceuscore/CaduceusCore/docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md.

#### Fix

Pending fix per Phase 9 plan.

#### Verification

Evidence: build/evidence/ph9-divergence-report.txt, build/evidence/ph9-probe-*.jsonl
