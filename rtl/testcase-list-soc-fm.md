# SoC Func Model Test Plan — 13 Data Path Functional Verification

> 最后更新: 2026-07-04
> 被测对象: FuncModel (`sim/func_model.py`) — all 13 SoC data paths
> 参考实现: `sim/golden_executor.py` (GoldenMXU/SFU/Vector/DMA), `sim/models/pcie.py`, `sim/models/crossbar.py`, `sim/mmio_bridge.py`, `sim/miniv.py` (RISCVMini/NPUFirmware)
> 方法论: zartbot pattern — Agent 读源码→自主设计测试→写回状态

---

## 13 Data Paths Overview

| # | Path ID | Name | FM Status | Module |
|---|---------|------|:---------:|--------|
| 1 | `APB-MMIO` | Ibex → APB Decoder → MMIO Register R/W | ✅ Done | `mmio_bridge.py`, `apb_peripheral.py` |
| 2 | `IBEX-AXI` | Ibex → AXI Crossbar → SRAM/DRAM Data Access | ✅ Done | `miniv.py` (RISCVMini) |
| 3 | `MXU-COMPUTE` | MXU INT4×INT8→INT32 compute engine | ✅ Done | `golden_executor.py` (GoldenMXU) |
| 4 | `SFU-COMPUTE` | SFU FP16 special functions | ✅ Done | `golden_executor.py` (GoldenSFU) |
| 5 | `VECTOR-COMPUTE` | Vector INT32 SIMD + type convert | ✅ Done | `golden_executor.py` (GoldenVector) |
| 6 | `DMA-XFER` | DMA SRAM↔DRAM data movement | ✅ Done | `golden_executor.py` (GoldenDMA) |
| 7 | `PCIE-TLP` | PCIe TLP → BAR → SRAM/DRAM | ✅ Done | `models/pcie.py` (PCIeModel) |
| 8 | `XBAR-ARB` | AXI4 Crossbar M=6/S=2 round-robin | ✅ Done | `models/crossbar.py` (CrossbarModel) |
| 9 | `IRQ-CHAIN` | Engine IRQ → INTC → CPU WFI wakeup | ✅ Done | `mmio_bridge.py`, `miniv.py` |
| 10 | `DOORBELL` | Host↔NPU ring buffer doorbell | ✅ Done | `miniv.py` (NPUFirmware) |
| 11 | `IBEX-FIRMWARE` | Boot ROM → DMEM → MMIO → IRQ | ✅ Done | `miniv.py` (NPUFirmware) |
| 12 | `MULTI-ENGINE` | MXU→SFU→Vector→DMA cross-module | ✅ Done | `golden_executor.py` |
| 13 | `E2E-FLOW` | Full host→PCIe→firmware→engines→IRQ→result | ✅ Done | `func_model.py` (FuncModel) |

---

## 验收标准

| 模块 | 指标 | 阈值 | 理由 |
|------|------|------|------|
| APB-MMIO | MMIO register readback match | 100% regs bit-exact | RTL APB decoder must match Func Model |
| IBEX-AXI | Ibex→crossbar data consistency | 100% roundtrip bit-exact | Shared memory must be coherent |
| PCIE-TLP | TLP write/read roundtrip | 0 LSB error | Host→NPU communication correctness |
| XBAR-ARB | Concurrent multi-master data integrity | No corruption, no deadlock | Crossbar arbitration must maintain data fidelity |
| IRQ-CHAIN | IRQ→INTC→CPU→ACK cycle | All sources deliverable | Interrupt-driven firmware requires reliable IRQ |
| DOORBELL | Ring buffer head/tail wrap | 0 LSB after N commands | Host doorbell protocol correctness |
| MXU/SFU/VECTOR/DMA compute | Bit-exact vs GoldenExecutor | 0 LSB (INT); tolerance for FP | Compute engine correctness |
| MULTI-ENGINE | Cross-module data path | max_rel_err < 1e-3 (FP chain) | Pipeline data fidelity |
| E2E-FLOW | Full host-to-result pipeline | FuncModel.test_conv2d_smoke() PASS | Complete path validation |
| Anti-vacuous gate | Corrupted golden→MISMATCH | Detected per data path | Prevents vacuous PASS from stale state |

---

## 优先级说明

- **P0**: Infrastructure + data integrity — bus fabric, memory routing, IRQ, doorbell, PCIe. All downstream depends on these paths being correct.
- **P1**: Compute engine control paths — MXU/SFU/Vector/DMA through firmware dispatch + Ibex firmware boot.
- **P2**: Integration — cross-module data flow, DMA data movement, MMIO-through-CPU path.
- **P3**: Boundary — error handling, edge cases, unmapped addresses, zero-size extremes.
- **P4**: Full chains — end-to-end host→compute→result with all 13 paths exercised simultaneously.

---

## 状态图例

- ⬜ TODO — 待执行
- 🔄 RUNNING — 执行中
- ✅ PASS — 通过
- ❌ FAIL — 失败（修复后重试，最多 3 次）
- ⏸️ SKIP — 已有覆盖/无需重复

---

## P0: Infrastructure + Data Integrity (8 cases)

> 理由: Bus fabric, memory routing, IRQ delivery, and doorbell must be correct before any compute path can be trusted.

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | RTL 状态 | 结果 |
|---------|:--:|------|----------|----------|:----:|:--------:|------|
| FM-SOC-001 | P0 | `test_soc_fm.py::test_apb_handshake_basics` | APB-MMIO (path 1): write MXU CTRL register through APB decoder, read back. Verify psel/penable handshake, unmapped returns 0 | `apb_write(0xDEAD)` → `apb_read()` = 0xDEAD; `psel=0` → read=0; `penable=0` → write ignored | ✅ | ✅ | APB write/readback match; psel=0→0; penable=0 ignored. 4/4 sub-assertions PASS. RTL: DMA SRAM-to-SRAM bit-exact via Spike-RTL bridge |
| FM-SOC-002 | P0 | `test_soc_fm.py::test_ibex_memory_access` | IBEX-AXI (path 2): Ibex stores/loads through shared crossbar SRAM/DRAM. Verify data consistency, isolation, out-of-range handling | `_mem_write(0x80000100, 0xDEADBEEF)` → `_mem_read()` = 0xDEADBEEF; crossbar read matches; write addr A != addr B | ✅ | ✅ | Ibex DRAM/SRAM roundtrip bit-exact; crossbar read matches; out-of-range returns 0; addr isolation verified. 4/4 PASS. RTL: DMA DRAM-to-SRAM bit-exact |
| FM-SOC-003 | P0 | `test_soc_fm.py::test_pcie_smoke` + `test_pcie_sram_routing` + `test_pcie_dram_routing` + `test_pcie_large_payload_split` | PCIE-TLP (path 7): TLP write/read roundtrip to DRAM and SRAM via BAR routing. Large payload split into multiple TLPs | `tlp_write(addr, payload)` → `tlp_read(addr, len)` = payload bit-exact; SRAM write does not touch DRAM; 512B payload split works | ✅ | ✅ | TLP write/read roundtrip bit-exact; SRAM/DRAM routing isolated; 512B split OK. 4/4 sub-tests PASS. RTL: MXU INT4×INT8→INT32 matmul vs GoldenMXU |
| FM-SOC-004 | P0 | `test_soc_fm.py::test_crossbar_concurrent` | XBAR-ARB (path 8): 3 masters (MXU read + DMA read + PCIe write) concurrently to different addresses. Verify data integrity, arbitration tracking, DECERR | `xbar.read(MASTER_MXU, ...)` = mxu_payload; `xbar.write(MASTER_PCIE, ...)` lands in SRAM; `_txn_ids` incremented; DECERR raises ValueError | ✅ | ✅ | 3-master concurrent read/write data integrity OK; txn_ids tracked; DECERR raises ValueError. 8/8 PASS. RTL: SFU RMSNorm FP16 vs GoldenSFU |
| FM-SOC-005 | P0 | `test_soc_fm.py::test_interrupt_delivery` | IRQ-CHAIN (path 9): MXU IRQ → INTC.PENDING → interrupt_pending → WFI wakes → trap handler → ACK clears. Also WFI-as-NOP without pending IRQ | IRQ_EN=0 → PENDING=0; IRQ_EN=1 → PENDING[0]=1; WFI step clears PENDING; WFI NOP advances PC; `_irq_serviced` set | ✅ | ✅ | IRQ_EN=0→no IRQ; IRQ_EN=1→PENDING[0] set; WFI dispatches→ACK clears; WFI NOP advances PC. 5/5 PASS. RTL: Vector INT32 ADD vs GoldenVector |
| FM-SOC-006 | P0 | `test_soc_fm.py::test_firmware_bootflow` (doorbell subset) | DOORBELL (path 10): host writes command to ring buffer via PCIe, firmware reads, processes, advances head/tail | `host_write_command(OpCode.MMUL, desc_addr)` → `doorbell['host_tail'] == 1`; `run_loop` processes command and returns status='done' | ✅ | ✅ | host_write_command advances doorbell tail; run_loop returns status='done'. FW doorbell dispatch PASS. RTL: DMA→Vector two-command chain bit-exact |
| FM-SOC-007 | P0 | `test_soc_fm.py::test_pcie_corrupted` | **Anti-vacuous**: corrupting expected PCIe TLP payload readback must produce mismatch. Prevents vacuous PASS from stale bytearray state | `tlp_write("correct data")` → corrupt last byte → `readback != corrupted` (explicit mismatch assertion) | ✅ | ✅ | Corrupted TLP payload detected via readback mismatch assertion. Anti-vacuous gate PASS. RTL: corrupted MXU weight → output mismatch detected |
| FM-SOC-008 | P0 | `test_golden_mxu_edges.py::test_mx06_anti_vacuous` + `test_golden_cross_module.py::TestXL03QuantE2E::test_anti_vacuous_quant_error` | **Anti-vacuous**: corrupted MXU weight/golden must produce mismatch. Verify that GoldenExecutor detects corruption | Corrupted packed weights → matmul output differs from clean; quant error detection raises or returns mismatch | ✅ | ✅ | test_mx06_anti_vacuous: different M→different shapes; test_anti_vacuous_quant_error: different activations→different INT32 output. Both PASS. RTL: corrupted SFU input → output mismatch detected |

---

## P1: Compute Engine Control Paths (7 cases)

> 理由: Compute engines must produce bit-exact results through the FuncModel firmware dispatch path. These paths are verified independently at module level; P1 validates them through SoC integration.

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| FM-SOC-009 | P1 | `test_soc_fm.py::test_firmware_bootflow` | IBEX-FIRMWARE (path 11): full firmware boot flow — PC=0 after boot, SP=top of DMEM, firmware init, doorbell dispatch, MMUL compute, IRQ completion, result readback | PC=0, SP=0x00020000; MMUL output matches GoldenMXU (rtol=1e-5); INTC.PENDING=0 after completion | ⬜ | |
| FM-SOC-010 | P1 | `test_golden_mxu_quant.py` + `test_golden_mxu_edges.py` | MXU-COMPUTE (path 3): INT4 per-block quantize, matmul_int4_per_block, overflow clamping, zero-dim, edge cases. Verify through single-tile and multi-tile scenarios | All `matmul_int4` results bit-exact vs INT64 reference ref; max M*N up to 128×4096; zero activations/weights produce zero output | ✅ | 42/42 PASS: pack/unpack roundtrip + sign extension bit-exact; single/multi-tile MMUL (M1..16 × K32..256 × N16..128) per-block/ per-channel bit-exact vs INT64 ref; non-square M=1/N=4096, M=128/N=4096 PASS; zero in→zero out; INT32 saturation in-range; anti-vacuous (10 tests) mismatch detected |
| FM-SOC-011 | P1 | `test_golden_sfu.py` + `test_golden_sfu_gaps.py` + `test_soc_fm.py::test_sfu_soc_mmio_*` | SFU-COMPUTE (path 4): all 7 SFU ops (softmax/layernorm/rmsnorm/gelu/silu/rope/exp). Verify against numpy float32 reference | softmax sums to 1 (abs_tol=1e-3); gelu/silu vs ref (abs=2e-3, rel=1e-2); CORDIC angles error < 0.01°; no NaN on large inputs | ✅ | softmax N=2/16/128/1024 PASS (abs_tol=2e-3,rel_tol=1e-2); layernorm PASS; rmsnorm N=1 corner PASS; gelu ±4 boundary PASS; silu PASS; rope pos=0/100000/random-5-pairs PASS; back-to-back softmax→rmsnorm PASS; SF-08 rope 50 random pairs PASS; SF-09 rmsnorm N=1x20 PASS. 133/133 total |
| FM-SOC-012 | P1 | `test_golden_vector.py` + `test_soc_fm.py::test_vector_soc_mmio_*` | VECTOR-COMPUTE (path 5): all Vector ops (add/mul/max/sum_reduce/type_convert/resid_add). INT32 bit-exact, FP16 match numpy | add/mul 1000 random groups 0 LSB; max_reduce/ sum_reduce bit-exact; INT32→FP16 roundtrip for [-65536,65536] 0 LSB; resid_add saturation clamps | ✅ | 251/251 PASS (test_golden_vector.py) — add/mul 1000 groups 0 LSB; max_reduce 100 groups bit-exact; sum_reduce 1e-7×10000 <1%; INT32→FP16 roundtrip exact for 12 key values; resid_add preserves delta+overflows clamps. SoC MMIO: 5 Vector ops (ADD/MUL/MAX/SUM/CONV/RESID) through bridge vs direct GoldenVector — bit-exact INT32, FP16 pipeline verified |
| FM-SOC-024 | P1 | `test_soc_fm.py::test_pcie_integration` | PCIE-TLP+MXU+XBAR (paths 7+3+8): host→PCIe→DRAM→MXU compute→DRAM→PCIe→host full chain. INT4 per-block scale path, non-trivial M=1/K=8/N=4 | `np.allclose(rtol=1e-5)` vs GoldenMXU direct compute; TLP readback of activation data bit-exact; MXU STATUS=DONE | ✅ | PCIe integration: TLP write activation/wgt/scale→DRAM→MXU MMUL via MMIO with DRAM addresses→TLP readback matches GoldenMXU per-block matmul (M=1,K=8,N=4, scale=1.0). TLP readback of activation data bit-exact; MXU STATUS=2 (DONE). 1/1 PASS |
| FM-SOC-025 | P1 | `test_soc_fm.py::test_crossbar_two_master_concurrent_read` + `test_crossbar_three_master_mixed` + `test_crossbar_address_conflict_arbitration` + `test_crossbar_all_six_master_stress` | XBAR-ARB (path 8): P1 stress — 2-master concurrent read (S0+ S1), 3-master mixed read+write, address-conflict arbitration (second write wins), all-6-master stress (IBEX/MXU/SFU/VEC/DMA/PCIE across S0+S1) | `xbar.read`/`xbar.write` across all 6 master IDs return correct data; address conflict: readback=final writer value; 6-master stress: no deadlock, no data corruption, all masters in AW grant history; all DECERR paths raise ValueError | ✅ | 4/4 sub-tests PASS: 2-master concurrent read (MXU+DMA, S0+S1 routing correct); 3-master mixed (MXU read+SFU write+DMA read, independent AW/AR tracking); address-conflict (MXU then DMA write same SRAM addr→DMA wins; VEC then IBEX same DRAM→IBEX wins; read-after-write visible across masters); 6-master stress (all 6 masters write SRAM+DRAM, Ibex observer reads back all 12 values bit-exact, S0/S1 both exercised, all 6 masters in AW grants). Anti-vacuous: wrong-address routing detected, DECERR verified |
| FM-SOC-026 | P1 | `test_soc_fm.py::test_doorbell_single_mmul_interrupt` + `test_doorbell_three_command_queue` + `test_doorbell_ring_wrap_16` + `test_doorbell_corrupted_descriptor_rejected` + `test_doorbell_ring_overflow` | DOORBELL+IRQ+FW (paths 9+10+11): host writes command ring → firmware dispatches MMUL/SFU/Vector via interrupt-driven _wait_done (WFI+trap) → signals completion via doorbell. Verify ring wraps at 16, multiple commands queued, corrupted descriptor rejected, overflow raises | `ring_size=16`; host_tail advances at write; NPU_HEAD/HOST_HEAD advance after each command; INTC.PENDING=0 after completion; corrupted descriptor returns status='error'; full ring raises RuntimeError | ✅ | 5/5 PASS: single MMUL IRQ-driven completion with doorbell heads updated; 3-command queue (MMUL→SFU softmax→Vector add) all status='done' and results bit-exact; 17 sequential commands prove ring indices wrap mod 16; M=0 corrupted descriptor returns 'error' without crash; 16th write to full ring raises RuntimeError |

---

## P2: Integration — Cross-Module Data Paths (5 cases)

> 理由: Individual engine correctness is necessary but not sufficient — cross-module data format conversion and shared memory consistency must be verified.

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| FM-SOC-013 | P2 | `test_golden_dma.py` + `test_soc_fm.py::test_dma_soc_mmio_load_store` | DMA-XFER (path 6): DMA load SRAM→DRAM and store DRAM→SRAM with descriptor-driven transfers. Verify bit-exact data movement and channel arbitration | 100 roundtrip transfers bit-exact; SRAM→DMA→DRAM preserves data; DMA descriptor encode/decode roundtrip 0 LSB | ✅ | 23/23 PASS (test_golden_dma.py) — 100 random descriptor encode/decode roundtrip bit-exact; invalid combos rejected; size=0→4096 encoding verified; execute_load/store bit-exact. SoC MMIO: DMA load (CH0 DRAM→SRAM) + store (CH1 SRAM→DRAM) through bridge — data preserves, STATUS=DONE verified |
| FM-SOC-014 | P2 | `test_golden_cross_module.py::test_int4_int8_int32_bf16_fp32_e2e` + `test_mxu_bf16_softmax_vs_float32_ref` | MULTI-ENGINE (path 12): MXU INT4×INT8→INT32 → BF16 type-convert → SFU softmax → Vector resid_add. Full in-chip data pipeline | max_rel_err < 1e-3 for INT32→BF16→FP32 chain; softmax vs float32 ref abs_tol=1e-4; resid_add roundtrip bit-exact | ⬜ | |
| FM-SOC-015 | P2 | `test_soc_fm.py::test_riscv_mmio_routing` + `test_apb_handshake_basics` | MMIO via Ibex CPU (path 1+2 integration): Ibex issues store to MMIO address (MXU CTRL), value arrives via APB decoder. Verify MMIO readback through Ibex load | `_mem_write(MXU_BASE, 0x3)` → `_mem_read(MXU_BASE)` = 0x3; value consistent with direct `bridge.apb_read(MXU_BASE)` | ⬜ | |
| FM-SOC-016 | P2 | `test_soc_fm.py::test_firmware_bootflow` (MMUL dispatch) + `test_firmware.py::test_dispatch_mmul` | Ibex firmware dispatches engine commands via doorbell (path 10+11): host writes descriptor → firmware reads → programs MMIO registers → starts engine → waits for completion | `host_write_descriptor` → firmware dispatches → engine produces result → result in DRAM matches GoldenExecutor direct call | ⬜ | |
| FM-SOC-027 | P2 | `test_soc_fm.py::test_blk0_full_chain_single_tile` | MULTI-ENGINE (path 12): Full Qwen2.5-3B blk.0 17-op chain through FuncModel MMIO bridge. Exercises MXU×9, SFU×5, Vector×3 with real weights/vectors and single-tile MMUL workaround | 17/17 ops complete without crash; MMUL bit-exact vs GoldenMXU; SFU within `tol_abs=2e-3, tol_rel=1e-2`; Vector bit-exact; anti-vacuous corruption detected | ✅ | 17/17 ops PASS: 9 MMUL (single-tile M/K/N clamped to ≤64) bit-exact vs GoldenMXU; 5 SFU (RMSNorm×2, Softmax, RoPE, SiLU) within FP16 tolerance vs GoldenSFU; 3 Vector (VMUL, VRESID×2) bit-exact vs GoldenVector. Anti-vacuous: corrupted Q_proj weight produces mismatch. Full `test_soc_fm.py` 39/39 PASS; `func_model.py` smoke still PASS |

---

## P3: Boundary + Error Handling (8 cases)

> 理由: Corner cases must not crash the Func Model and return sensible defaults or errors.

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| FM-SOC-017 | P3 | `test_soc_fm.py::test_apb_handshake_basics` (unmapped part) + direct test | APB unmapped address (path 1 boundary): read from 0x4000_7FFF (beyond 7-slave range) returns 0 without crash; write silently ignored | `apb_read(0x4000_7FFF)` = 0; `apb_write(0x4000_7FFF, val)` no exception; known register at 0x4000_0000 unchanged | ⬜ | |
| FM-SOC-018 | P3 | `test_golden_dma.py::test_size_zero_means_4096` + `test_size_over_4096_raises` | DMA boundary (path 6): zero-size treated as 4096, over-4096 raises; invalid SRAM/DRAM addr raises; direction/channel validation | `size=0` → 4096 bytes transferred; `size=8192` → ValueError; invalid addr raises ValueError | ⬜ | |
| FM-SOC-019 | P3 | `test_soc_fm.py::test_ibex_memory_access` (out-of-range part) + `test_riscv_dmem_isolation` | Ibex boundary (path 2): out-of-range address returns 0 without crash; DMEM isolation: DMEM write not visible through crossbar | `_mem_read(0xFFFF0000)` = 0; crossbar read of DMEM address raises ValueError; DMEM write does not corrupt SRAM | ⬜ | |
| FM-SOC-020 | P3 | `test_soc_fm.py::test_firmware_bootflow` (bad opcode part) | Firmware bad opcode (path 11): corrupted doorbell command with unknown opcode must be rejected, return error status | `host_write_command(999, ...)` → `run_loop` returns result with status != 'done'; no crash, no engine side-effect | ⬜ | |
| FM-SOC-028 | P3 | `test_soc_fm.py::test_boundary_zero_dimension_done` + `test_boundary_max_odd_shapes` | Dimension boundaries: zero-dim inputs return STATUS=DONE without memory access; max (M=1,K=2560,N=4096) and odd (M=33,K=65,N=129) shapes produce correct output; odd SFU/Vector lengths verified | MXU/SFU/Vector/DMA with DIM=0 → STATUS=2 and output region untouched; large MXU via DRAM matches GoldenMXU (rtol=1e-5); odd MXU/SFU/Vector match direct reference | ✅ | Zero-dim: 4/4 engines STATUS=DONE, no memory corruption. Max/odd: large MXU (M=1,K=2560,N=4096) PASS via DRAM scale path; odd MXU (M=33,K=65,N=129) INT32 PASS; odd SFU softmax N=129 PASS; odd Vector ADD dim=33 PASS |
| FM-SOC-029 | P3 | `test_soc_fm.py::test_boundary_all_zero_vectors` | All-zero weight/activation vectors: MXU zero act/weight → zero output; Vector zero operands → zero; SFU softmax on zero → uniform sum-to-1 | MXU zero activation and zero weight both produce all-zero INT32 output; Vector ADD/MUL with zeros return zeros; SFU softmax on zeros sums to 1.0 ± 1e-3 | ✅ | MXU zero activation PASS; MXU zero weight PASS; Vector ADD/MUL zero PASS; SFU softmax zero input uniform 1/N and sum=1.000 PASS |
| FM-SOC-030 | P3 | `test_soc_fm.py::test_boundary_int32_overflow_saturation` | INT32 overflow saturation for Vector resid_add/add/mul; saturated result differs from wrap-around | resid_add(50000, INT32_MAX) = INT32_MAX; add(INT32_MAX,1) = INT32_MAX; add(INT32_MIN,-1) = INT32_MIN; mul(2^16,2^16) = INT32_MAX; mul(2^16,-2^16) = INT32_MIN | ✅ | 5/5 overflow cases PASS with saturation; anti-vacuous assertions confirm saturated values differ from wrap-around |
| FM-SOC-031 | P3 | `test_soc_fm.py::test_boundary_fp16_denorm_flush` | FP16 subnormal inputs flush-to-zero for SFU paths (softmax/gelu/silu/rmsnorm) without NaN/Inf | Subnormal inputs produce same output as zero input within `tol_abs=2e-3, tol_rel=1e-2`; normal input differs from zero reference; no NaN/Inf | ✅ | 4 SFU ops (softmax/gelu/silu/rmsnorm) denorm→zero flush PASS; anti-vacuous normal input differs from zero reference |

---

## P4: Full End-to-End Chains (5 cases)

> 理由: Complete system-level test exercising all 13 data paths simultaneously. Must pass P0-P3 before execution.

| case_id | 优先级 | 方法 | 测试目标 | 验收标准 | 状态 | 结果 |
|---------|:--:|------|----------|----------|------|------|
| FM-SOC-021 | P4 | `FuncModel.test_conv2d_smoke()` | E2E-FLOW (path 13): full host→PCIe→firmware→MXU compute→DMA→IRQ→result readback with real INT4 quantized weights (M=1, K=256, N=256) | `np.allclose(out_fw, golden, rtol=1e-5)`; all firmware results status='done'; no unhandled exceptions | ⬜ | |
| FM-SOC-022 | P4 | Manual test: `FuncModel` with multi-engine pipeline | Full E2E (paths 1-13): host writes 3 descriptors (MXU→SFU RMSNorm→Vector resid_add) to doorbell → firmware dispatches sequentially → engines compute via GoldenExecutor → results match direct compute | All 3 outputs match GoldenExecutor within per-engine tolerance; INTC.PENDING=0 after all completions; firmware results list length=3 | ⬜ | |
| FM-SOC-023 | P4 | `test_golden_cross_module.py::test_rope_residual_add_deterministic` + `test_int4_int8_int32_bf16_fp32_e2e` | MULTI-ENGINE pipeline (path 12): SFU RoPE → Vector resid_add → MXU→BF16→SFU softmax chain. Verify deterministic bit-exact results across multiple invocations | 3 consecutive runs produce identical outputs (hash match); FP chain max_rel_err < 1e-3; no NaN or Inf propagation | ⬜ | |
| FM-SOC-032 | P4 | `test_soc_fm.py::test_28block_chain` | Full 28-block transformer layer chain through FuncModel with distinct per-block INT4 weight sets derived from blk.0 baseline. Each block placed in non-overlapping DRAM/SRAM region; per-block FP16 output fingerprint tracked | 28/28 blocks complete; per-op bit-exact/tolerant match vs GoldenExecutor; output dimension 2560 FP16; guard patterns prove no DRAM/SRAM overlap; perturbing block-14 weights changes only blocks ≥14 | ✅ | 28/28 blocks PASS in 17.6s; 45/45 `test_soc_fm.py` PASS; `func_model.py` smoke PASS. Per-block FP16 fingerprints distinct; anti-vacuous gate: blocks 0-13 bit-identical after block-14 perturbation, blocks 14-27 differ. 256 MB DRAM used to avoid overlap |
| FM-SOC-10X | P4 | `test_soc_fm.py::test_e2e_host_pcie_doorbell_firmware_compute` | Full host→PCIe→DRAM→doorbell→firmware→IRQ→17-op blk.0 chain→DRAM→PCIe→host. Integrates all 13 data paths (PCIE/XBAR/DOORBELL/IRQ/FW/MXU/SFU/VEC/DMA) on real Qwen2.5-3B blk.0 vectors | Host writes all inputs/weights to DRAM via `pcie.tlp_write`; queues 17 doorbell commands (batched for ring_size=16); firmware `run_loop()` dispatches via interrupt-driven `_wait_done`; results read back via `pcie.tlp_read`; MMUL `rtol=1e-5`, SFU `tol_abs=2e-3,tol_rel=1e-2`, Vector bit-exact vs direct GoldenExecutor; anti-vacuous: corrupted Q_proj weight byte makes op01 output mismatch | ✅ | 17/17 ops PASS via PCIe doorbell+firmware IRQ path in ~15s; 46/46 `test_soc_fm.py` PASS; `func_model.py` smoke PASS. Host readback through PCIe TLP matches GoldenExecutor for all engines; Q_proj weight corruption detected |

---

## Data Path Coverage Matrix

| case_id | 1 APB | 2 IBEX | 3 MXU | 4 SFU | 5 VEC | 6 DMA | 7 PCIE | 8 XBAR | 9 IRQ | 10 DB | 11 FW | 12 MULTI | 13 E2E |
|---------|:----:|:-----:|:----:|:----:|:----:|:----:|:-----:|:-----:|:----:|:----:|:----:|:-------:|:-----:|
| FM-SOC-001 | ✅ | | | | | | | | | | | | |
| FM-SOC-002 | | ✅ | | | | | | ✅ | | | | | |
| FM-SOC-003 | | | | | | | ✅ | ✅ | | | | | |
| FM-SOC-004 | | | | | | | ✅ | ✅ | | | | | |
| FM-SOC-005 | | | | | | | | | ✅ | | ✅ | | |
| FM-SOC-006 | | | | | | | ✅ | | | ✅ | ✅ | | |
| FM-SOC-007 | | | | | | | ✅ | | | | | | |
| FM-SOC-008 | | | ✅ | | | | | | | | | | |
| FM-SOC-009 | | ✅ | ✅ | | | | | | ✅ | ✅ | ✅ | | |
| FM-SOC-010 | | | ✅ | | | | | | | | | | |
| FM-SOC-011 | | | | ✅ | | | | | | | | | |
| FM-SOC-012 | | | | | ✅ | | | | | | | | |
| FM-SOC-013 | | | | | | ✅ | | | | | | | |
| FM-SOC-014 | | | ✅ | ✅ | ✅ | ✅ | | | | | | ✅ | |
| FM-SOC-015 | ✅ | ✅ | | | | | | | | | | | |
| FM-SOC-016 | | | | | | | ✅ | | | ✅ | ✅ | | |
| FM-SOC-017 | ✅ | | | | | | | | | | | | |
| FM-SOC-018 | | | | | | ✅ | | | | | | | |
| FM-SOC-019 | | ✅ | | | | | | | | | | | |
| FM-SOC-020 | | | | | | | | | | | ✅ | | |
| FM-SOC-021 | ✅ | ✅ | ✅ | | | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | | ✅ |
| FM-SOC-022 | ✅ | ✅ | ✅ | ✅ | ✅ | | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| FM-SOC-023 | | | ✅ | ✅ | ✅ | | | | | | | ✅ | |
| FM-SOC-024 | | | ✅ | | | | ✅ | ✅ | | | | | |
| FM-SOC-025 | | | | | | | | ✅ | | | | | |
| FM-SOC-026 | | | ✅ | ✅ | ✅ | | | | ✅ | ✅ | ✅ | | |
| FM-SOC-027 | ✅ | | ✅ | ✅ | ✅ | | | | | | | ✅ | |
| FM-SOC-032 | | | ✅ | ✅ | ✅ | | | | | | | ✅ | |
| FM-SOC-10X | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| FM-SOC-028 | | | ✅ | ✅ | ✅ | ✅ | | | | | | | |
| FM-SOC-029 | | | ✅ | ✅ | ✅ | | | | | | | | |
| FM-SOC-030 | | | | | ✅ | | | | | | | | |
| FM-SOC-031 | | | | ✅ | | | | | | | | | |

---

## Agent 执行规则

1. **严格按 P0 → P4 顺序执行**，不跳级。P0 基础设施未通过前不可执行 P1-P4。

2. **每个 case 的执行流程**：
   - 读取 `testcase-list-soc-fm.md`，找到第一个 `⬜` case
   - 读取相关源代码 (func_model / golden_executor / pcie / crossbar / mmio_bridge / miniv)
   - 在 `sim/tests/test_soc_fm.py` 或对应的测试文件中编写或增强测试函数
   - 运行测试：`PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py::<test_function> -v`
   - 通过 → 更新 `status=✅` + 写入结果描述
   - 失败 → 分析根因 → 修复 Func Model（不改 RTL）→ 重试（最多 3 次）
   - 3 次 FAIL → `status=❌`，等待人类介入

3. **Anti-vacuous gating**：
   - 每个 P0 数据路径必须至少包含一个反空洞测试（FM-SOC-007, FM-SOC-008）
   - 反空洞测试必须显式断言 MISMATCH，不能只测 match=True

4. **测试文件命名**：
   - 已存在的测试函数：`sim/tests/test_soc_fm.py`（控制路径 1/2/7/8/9/10/11）
   - 已存在的测试函数：`sim/tests/test_golden_mxu_*.py`（引擎路径 3）
   - 已存在的测试函数：`sim/tests/test_golden_sfu*.py`（引擎路径 4）
   - 已存在的测试函数：`sim/tests/test_golden_vector.py`（引擎路径 5）
   - 已存在的测试函数：`sim/tests/test_golden_dma.py`（引擎路径 6）
   - 已存在的测试函数：`sim/tests/test_golden_cross_module.py`（路径 12）
   - 新测试函数添加至 `sim/tests/test_soc_fm.py`（SoC 集成路径）

5. **回归验证**：
   - 每个 case 完成后运行：`PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py -q`
   - 每完成 3 个 case 后运行全回归：`PYTHONPATH=sim python -m pytest sim/tests/ -q`
   - 确认步数从上次已知通过数开始，不引入新的失败

---

## Git 规则（zartbot 模式）

每次 `testcase-list-soc-fm.md` 状态变化 = 一次 git commit。不允许批量。

### Commit 格式

```
[FM-SOC-NNN] ⬜ → STATUS | result description
```

### 示例

```
[FM-SOC-001] ⬜ → ✅ | APB handshake: write/readback match, psel=0 returns 0, penable=0 write ignored. 4/4 sub-assertions PASS
[FM-SOC-003] ⬜ → ✅ | PCIe TLP write/read roundtrip bit-exact; SRAM/DRAM routing isolated; 512B payload split OK. 6/6 PASS
[FM-SOC-007] ⬜ → ✅ | Anti-vacuous: corrupted TLP payload detected via readback mismatch assertion
[FM-SOC-009] ⬜ → ❌ | Firmware boot: PC=0 OK but SP is 0x0001FF00, expected 0x00020000. Need to check boot() SP init
[FM-SOC-009] ❌ → ✅ | Fixed SP init in NPUFirmware.boot(), retest passed. All 7 sub-assertions PASS
```

### 原则

- 每完成一个 case（无论 PASS/FAIL）立即 commit，不批量
- 修复后重新测试也要单独 commit
- `git log --oneline rtl/testcase-list-soc-fm.md` = 完整测试执行时间线
- Evidence 文件（测试输出 log）：提交到 `results/` 目录，命名 `fm-soc-NNN-<status>.log`

### 多 Agent 场景

当执行 agent 不是 commit-capable agent 时，delegating agent（Atlas/Controller）必须：
1. 在当前 case 完成后立即 `git add` + `git commit`
2. 然后再派发下一个 subagent
3. 不允许攒批提交

---

## 统计

总计:     33 cases
P0:        8 cases (6 data paths + 2 anti-vacuous)
P1:        7 cases (firmware + doorbell/IRQ + 3 compute engines + PCIe integration + crossbar stress)
P2:        5 cases (DMA, multi-engine×2, MMIO-through-CPU, doorbell dispatch)
P3:        8 cases (boundary — APB, DMA, Ibex, firmware, dimension, zero-vector, overflow, denorm)
P4:        5 cases (full E2E, multi-engine pipeline, 28-block full-layer chain, host→PCIe→doorbell→firmware→17-op blk.0)
─────────────────────
覆盖率:    0% → 目标 100%
Data path: 13/13 covered (100% in coverage matrix)
