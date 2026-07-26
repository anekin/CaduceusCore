# func-model-signoff-v3 - Work Plan

## TL;DR (For humans)

**What you'll get:** Caduceus Func Model 的 SoC 集成路径签收——从 func-model-signoff-v2 没覆盖的 10 个差距中挑选最关键的 SoC 数据通路做验证：Spike+真实固件的调度链、PCIe DMA 数据通路（Host↔NPU）、Crossbar 多主并发、Doorbell 门铃协议、INTC 中断链。Func Model 自此可以作为 SoC 关键集成路径的功能 golden reference。不覆盖 APB decoder 功能模型、Boot ROM 行为、Ibex AXI adapter 地址路由——这些通路通过集成测试间接覆盖但无独立签收。

**Why this approach:** func-model-signoff-v2 验证了"算子对不对"（MXU/SFU/Vector 的数学正确性）。v3 验证"整个 SoC 调得通调不对"——Spike+固件能不能正确 dispatch 一个算子链、Host CPU 能不能通过 PCIe 把数据写进 NPU DRAM 再读回来、多个 master 同时访问 Crossbar 有没有数据损坏。这是 func-model-signoff-v2 有意识不碰的集成验证层。基于已有差距分析（`.omo/drafts/func-model-perf-signoff.md` 附带的 SoC gap analysis），每个任务都有明确的参照物和已有测试。

**What it will NOT do:** 不重复 v2 的算子数学正确性，不跑 RTL VCS 仿真，不做性能签收，不修固件/RTL bug（但会记录发现的新 bug 到 bug track）。不验证 `test_soc_pcie_dma.py`（那是 RTL+Cocotb 测试，不在 Func Model Python 层）。

**Effort:** Standard — ~10 tasks, 约 1-2 天
**Risk:** Low — 所有验证目标都有现有的 Func Model Python 测试做基础（`test_soc_fm.py` 已有 crossbar/doorbell/interrupt 测试；`test_pcie_dma_fm.py` 已有 DmaEngine 7 个测试；`spike_host.py` 已有 4 种模式）。v3 的工作是补签收证据、补覆盖率、确保 Spike+固件路径稳定可复现。

## Scope
### Must have
- **Spike+firmware 调度链功能验证**：`spike_host.py` 四种模式全部跑通并签收——mmul_smoke（单算子），chain（混链 mmul+sfu+vector+dma_copy），forward（多层 forward pass + golden 对比），pcie_dma（PCIe DMA 调度）
- **PCIe DMA 数据通路签收**：`DmaEngine` 的 TLP 构造/拆分/错误注入/描述符转译全覆盖（已有 7 个 `test_pcie_dma_fm.py` 测试，补签收证据）。Host→NPU MWr 写 + NPU→Host MRd 读 + 描述符完成中断
- **Crossbar 并发验证**：6 个 master 并发访问 2 个 slave，验证仲裁公平性、地址冲突处理、数据完整性
- **Doorbell 门铃协议签收**：Ring buffer HOST_TAIL/NPU_HEAD 协议、多命令队列、环回绕、描述符错误检测
- **INTC 中断链签收**：7 源中断（MXU/SFU/Vector/DMA/PCIeDMA/HostDoorbell）全链路——PENDING→ENABLE→THRESHOLD→ACK→WFI 唤醒
- **Host CPU 通信签收**：`FuncModel.host_write_command()` + `host_write_data()` + `host_read_data()` ——通过 PCIe TLP 写描述符、写权重数据、读回输出，端到端验证位一致性
- **Spike 稳定性**：确保 `spike_host.py`+`npu_mmio_plugin.so` 可复现运行（已知 ABI mismatch 已在 Phase 7 修复）
- 所有工作在 `main` 分支上推进

### Must NOT have
- NO RTL VCS 仿真 / Cocotb 测试（`test_soc_pcie_dma.py` 排除）
- NO RTL 端 bug 修复
- NO NPU 引擎算子数学正确性重复验证（v2 已签收）
- NO 性能签收（那是 perf-signoff 的事）
- NO 固件功能扩展（只验证现有固件调度路径，不修 weight-buffer 限制等已知问题）
- NO `spike_host.py` 代码修改（只跑测试，不重构）
- Binding constraints (verbatim):
  1. "以后设计验证的工作都在main分支上推进"
  2. "涉及到工具调用，环境变量设置，都用脚本方式"
  3. "所有验证都在sz0001上进行"
  4. "对于bug，一定要记录到bug track文件"——所有新发现的集成问题记录到 `docs/bugs/bugs-soc-func-model.md`

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- **测试框架**: pytest + 沿用 func-model-signoff-v2 的 signoff runner 模式（扩展 `scripts/run_func_model_signoff.py` 的 case registry 或新建 parallel runner）
- **Spike 路径**: 通过 `bash scripts/run_fm_env.sh -- python3 sim/spike_host.py --mode <mode>` 执行
- **Python 路径**: `python3 -m pytest` 调用现有 `test_soc_fm.py` / `test_pcie_dma_fm.py` / 部分新建测试
- **Evidence**: `.omo/evidence/task-<id>-func-model-signoff-v3.txt`，atomic write，source-fingerprint，stale-HEAD detection
- **Bug tracking**: Any discrepancy → `docs/bugs/bugs-soc-func-model.md`

## Execution strategy
### Parallel execution waves
1. **Wave 0:** T0 (signoff runner extension for v3 cases)
2. **Wave 1** (after T0): T1 + T2 + T3 + T4 — parallel (Spike modes + PCIe DMA + Crossbar + Doorbell)
3. **Wave 2** (after T1-T4): T5 + T6 — parallel (INTC + Host CPU)
4. **Wave 3** (after T5+T6): T7 — Full SoC integration chain
5. **Final wave:** F1-F4

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| T0 | none | all evidence tasks | — |
| T1 | T0 | T7 | T2, T3, T4 |
| T2 | T0 | T7 | T1, T3, T4 |
| T3 | T0 | T7 | T1, T2, T4 |
| T4 | T0 | T7 | T1, T2, T3 |
| T5 | T0 | T7 | T6 |
| T6 | T0 | T7 | T5 (注：T6 使用已有的 `host_write_command/read_data` API，不依赖 T3/T4 的 edge-case 测试) |
| T7 | T1-T6 | final wave | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

- [x] 0. Signoff runner extension for v3 cases (T0)
  What to do: Extend `scripts/run_func_model_signoff.py` CASE_REGISTRY to add v3 performance-agnostic functional cases covering Spike+firmware, PCIe DMA, Crossbar, Doorbell, INTC, and Host CPU. Each case: argv list (may include bash wrapper for Spike modes), evidence path, expected exit, required SIGNOFF_METRIC records, source-fingerprint. Create `sim/tests/test_func_model_signoff_v3.py` — unit cases verify v3 registry entries are structurally valid. Ensure backward compat with existing v2 cases.
  Must NOT do: Do NOT duplicate v2 cases. Do NOT use shell=True.
  Parallelization: Wave 0 | Blocked by: none | Blocks: all evidence tasks
  References: `scripts/run_func_model_signoff.py:331-732` (existing case registry), `sim/tests/test_func_model_signoff_runner.py` (unit test pattern)
  Acceptance criteria: `validate --v3` finds 7+ cases; runner smoke test passes. Evidence: `.omo/evidence/task-0-signoff-v3-runner.txt`
  QA scenarios: Happy = all v3 cases discoverable, validate works. Failure = case collision with v2 → renumber. Evidence: as above.
  Commit: Y | feat(func-model-signoff-v3): add v3 SoC signoff runner cases

- [x] 1. Spike+firmware dispatch chain functional verification (T1)
  What to do: Verify ALL FOUR spike_host.py modes run correctly with the existing firmware ELF. (a) `--mode mmul_smoke`: single MMUL op dispatch, verify golden compare PASS, output bit-exact; (b) `--mode chain`: mixed chain (mmul+sfu+vector+dma_copy), verify each op produces non-zero output and no crash; (c) `--mode forward`: multi-layer forward pass with reference comparison, verify cos_sim ≥ 0.99 per layer; (d) `--mode pcie_dma`: PCIe DMA opcode 7 dispatch test, verify firmware processes opcode without crash and NPU_HEAD advances correctly (data roundtrip verification is in T2). Run each mode under `bash scripts/run_fm_env.sh -- python3 sim/spike_host.py --mode <mode>`. Record per-mode exit code, duration, and key metrics (cos_sim or equivalent tolerance). Add SIGNOFF_METRIC records: `spike.mode`, `spike.exit_code`, `spike.tolerance_result`, `spike.elapsed_s`. Note: forward mode currently uses `abs_tol=1e-1` (not cos_sim); T1 evidence MUST compute cos_sim from the npz comparison output or document the equivalence rationale.
  Must NOT do: Do NOT modify spike_host.py. Do NOT rebuild firmware if it already compiles. Do NOT fix firmware bugs — just report them.
  Parallelization: Wave 1 | Blocked by: T0 | Blocks: T7 | Can parallelize with: T2, T3, T4
  References: `sim/spike_host.py:1117-1289` (mode dispatch), `sim/spike_host.py:37-40` (binary paths), `firmware/npu_firmware.c`, `spike_src/plugins/npu_mmio_plugin.cc`
  Acceptance criteria: All 4 modes exit 0; mmul_smoke golden compare PASS (rtol=1e-5); chain PASS; forward abs_tol=1e-1 satisfied (cos_sim ≥ 0.99 when computed from npz output); pcie_dma runs without crash, NPU_HEAD advances. Evidence: registry cases `task-1a-v3-spike-mmul-smoke` → `.omo/evidence/task-1a-spike-mmul-smoke.txt`, `task-1b-v3-spike-chain` → `.omo/evidence/task-1b-spike-chain.txt`, `task-1c-v3-spike-forward` → `.omo/evidence/task-1c-spike-forward.txt`, `task-1d-v3-spike-pcie-dma` → `.omo/evidence/task-1d-spike-pcie-dma.txt`
  QA scenarios: Happy = 4/4 modes pass. Failure = any mode crashes → check Spike plugin build, firmware ELF availability, GGUF path. Evidence: as above.
  Commit: Y | test(func-model-signoff-v3): Spike+firmware dispatch chain verification

- [x] 2. PCIe DMA pathway functional verification (T2)
  What to do: Verify `DmaEngine` model (already unit-tested by `test_pcie_dma_fm.py` with 7 tests) at the signoff level. (a) Run existing 7 `test_pcie_dma_fm.py` tests via signoff runner, collect per-test PASS/FAIL and SIGNOFF_METRIC records; (b) Add 3 integration-level tests: `test_pcie_dma_host_to_npu_mwr` — host writes 256B via PCIe TLP → crossbar → DRAM, verify data integrity; `test_pcie_dma_npu_to_host_mrd` — NPU reads 512B from host via MRd+CplD, verify reassembled data; `test_pcie_dma_descriptor_irq_chain` — 3 descriptors submitted, verify each completes and fires IRQ; (c) Verify DmaEngine tag pool does not leak (256 tags, allocate→use→complete→reuse cycle). Create `sim/tests/test_func_model_signoff_v3_pcie.py` for the new integration tests.
  Must NOT do: Do NOT test Cocotb/RTL PCIe (test_soc_pcie_dma.py excluded). Do NOT test `PCIeModel` TLP roundtrip (already in test_soc_fm.py).
  Parallelization: Wave 1 | Blocked by: T0 | Blocks: T7 | Can parallelize with: T1, T3, T4
  References: `sim/models/pcie.py:175-770` (DmaEngine), `sim/tests/test_pcie_dma_fm.py` (7 existing tests), `sim/tests/test_soc_fm.py:106` (test_pcie_integration for pattern)
  Acceptance criteria: 7 existing tests + 3 new tests all PASS; tag pool 256→0→256 validate; descriptor IRQ chain fires 3/3. Evidence: `.omo/evidence/task-2-pcie-dma.txt`
  QA scenarios: Happy = 10/10 PCIe DMA tests pass. Failure = tag leak, wrong TLP header, split completion data corruption → check DmaEngine tag lifecycle. Evidence: as above.
  Commit: Y | test(func-model-signoff-v3): PCIe DMA pathway functional verification

- [x] 3. Crossbar concurrent multi-master verification (T3)
  What to do: Verify CrossbarModel M=6/S=2 arbitration correctness at the signoff level. (a) Run existing crossbar tests from `test_soc_fm.py` (lines 182-420: concurrent 3-master, 2-master concurrent read, 3-master mixed, address conflict, all 6-master stress) via signoff runner; (b) Add 2 new scenarios: `test_crossbar_concurrent_real_engines` — simulate MXU computing while SFU writes output and DMA loads next tile simultaneously through crossbar (a closer-to-real scenario than direct xbar.read() calls); `test_crossbar_round_robin_fairness` — verify that over 100 random 6-master 2-slave accesses, per-master grant count is within ±20% of expected (100/6 ≈ 16.7, range [13, 20]). (c) Verify data integrity: no torn reads, no data from wrong slave, no address aliasing across BAR boundaries.
  Must NOT do: Do NOT add cycle-level timing to CrossbarModel. Do NOT require RTL crossbar simulation.
  Parallelization: Wave 1 | Blocked by: T0 | Blocks: T7 | Can parallelize with: T1, T2, T4
  References: `sim/models/crossbar.py:1-243` (CrossbarModel), `sim/tests/test_soc_fm.py:182-420` (existing crossbar tests), `sim/mmio_bridge.py` (MMIO routing)
  Acceptance criteria: Existing 5 tests + 2 new tests all PASS; round-robin fairness within ±20%; no data integrity violation. Evidence: `.omo/evidence/task-3-crossbar.txt`
  QA scenarios: Happy = 7/7 crossbar tests pass, fairness OK. Failure = grant count skewed >20% → check _aw_last_granted state; data corruption → check BAR decode. Evidence: as above.
  Commit: Y | test(func-model-signoff-v3): crossbar concurrent multi-master verification

- [x] 4. Doorbell ring buffer protocol verification (T4)
  What to do: Verify the doorbell ring buffer protocol in Func Model matches `rtl/soc/doorbell.v` semantics. (a) Run existing doorbell tests from `test_soc_fm.py` (lines 1377-1580: single mmul dispatch, 3-command queue, ring wrap at 16, corrupted descriptor rejection, overflow detection); (b) Add 2 edge case tests: `test_doorbell_empty_ring_noop` — NPU reads HOST_TAIL == NPU_HEAD, should not dispatch; `test_doorbell_concurrent_push_poll` — host pushes 3 commands while NPU is processing previous ones, verify correct interleaving. (c) Verify that `FuncModel.host_write_command()` produces correct ring buffer byte layout matching `mmul_desc_t` struct (see `spike_host.py:46` for the 15-field descriptor format).
  Must NOT do: Do NOT create a separate DoorbellModel class (keep inline in mmio_bridge.py/func_model.py). Do NOT test RTL doorbell.v.
  Parallelization: Wave 1 | Blocked by: T0 | Blocks: T7 | Can parallelize with: T1, T2, T3
  References: `sim/tests/test_soc_fm.py:1377-1580` (doorbell tests), `sim/func_model.py:117-143` (host_write_command), `sim/mmio_bridge.py:503-515` (_handle_doorbell), `rtl/soc/doorbell.v` (reference RTL semantics)
  Acceptance criteria: 5 existing + 2 new doorbell tests all PASS; ring buffer descriptor layout verified; empty ring correctly noops. Evidence: `.omo/evidence/task-4-doorbell.txt`
  QA scenarios: Happy = 7/7 doorbell protocol tests pass. Failure = ring wrap at wrong boundary → check modulo arithmetic; descriptor misalignment → check struct format. Evidence: as above.
  Commit: Y | test(func-model-signoff-v3): doorbell ring buffer protocol verification

- [x] 5. INTC interrupt delivery chain verification (T5)
  What to do: Verify the 7-source interrupt controller chain in Func Model. (a) Run existing `test_interrupt_delivery()` from `test_soc_fm.py:593` (MXU→INTC→WFI→ACK cycle); (b) Add per-source interrupt tests: verify each of the 7 sources (MXU/SFU/Vector/DMA/PCIeDMA/HostDoorbell) individually triggers PENDING, passes ENABLE mask, meets THRESHOLD, asserts IRQ line, wakes WFI, and gets ACK'd in the handler. (c) Verify interrupt priority/latency: when multiple sources assert simultaneously, lower-numbered source is serviced first (matches `rtl/intc/intc_top.v` priority-encoder). (d) Verify interrupt handler correctly identifies source via INTC.PENDING read and dispatches to correct handler (`NPUFirmware.dispatch_interrupt()` in `miniv.py`).
  Must NOT do: Do NOT test RTL INTC or Spike interrupt handling (only Func Model Python layer).
  Parallelization: Wave 2 | Blocked by: T0 | Blocks: T7 | Can parallelize with: T6
  References: `sim/tests/test_soc_fm.py:593-622` (test_interrupt_delivery), `sim/mmio_bridge.py:536-547` (_set_irq), `sim/miniv.py:434-552` (NPUFirmware + dispatch_interrupt), `rtl/intc/intc_top.v` (reference RTL behavior)
  Acceptance criteria: 1 existing + 7 new per-source + 1 priority test = 9 total PASS; all 7 sources trigger identifiably; priority encoder correct. Evidence: `.omo/evidence/task-5-intc.txt`
  QA scenarios: Happy = 9/9 INTC tests pass. Failure = wrong source identified → check _set_irq bit mapping; WFI not waking → check interrupt_pending flag. Evidence: as above.
  Commit: Y | test(func-model-signoff-v3): INTC interrupt delivery chain verification

- [x] 6. Host CPU communication verification (T6)
  What to do: Verify the complete Host CPU→NPU→Host CPU data path in Func Model. (a) Test `FuncModel.host_write_command()` — host writes a valid mmul descriptor array to DRAM via PCIe TLP, rings doorbell; verify NPU reads descriptor correctly and dispatches the right op; (b) Test `FuncModel.host_write_data()` — host writes weight/activation data via PCIe TLP to DRAM; verify NPU can read the data through crossbar and it matches the written bytes; (c) Test NPU→host readback — NPU writes output to DRAM through `model.crossbar.write()`, host reads back via `model.pcie.tlp_read(addr, size)`; verify bit-exact roundtrip (FuncModel does not have a standalone `host_read_data` method; use `pcie.tlp_read` directly); (d) Test full end-to-end: host writes command + data → NPU executes (MMUL+SFU+Vector) → host reads output → compare against GoldenExecutor. This is the SoC-level equivalent of what FM-SOC-10X tests on RTL, but purely in Func Model Python. Create `sim/tests/test_func_model_signoff_v3_host.py`.
  Must NOT do: Do NOT run Spike for these tests (use FuncModel Python API directly). Do NOT test RTL paths.
  Parallelization: Wave 2 | Blocked by: T0 | Blocks: T7 | Can parallelize with: T5
  References: `sim/func_model.py:117-143` (host_write_command), `sim/func_model.py:176-192` (host_write_data), `sim/models/pcie.py:130-173` (tlp_read for host readback), `sim/golden_executor.py` (GoldenMXU/SFU/Vector), `sim/tests/test_soc_fm.py:106` (test_pcie_integration pattern)
  Acceptance criteria: 4 host CPU scenarios all PASS; data roundtrip bit-exact; doorbell correctly triggers dispatch; end-to-end golden compare matches. Evidence: `.omo/evidence/task-6-host-cpu.txt`
  QA scenarios: Happy = 4/4 host CPU scenarios pass, bit-exact roundtrip. Failure = host data mismatched after PCIe TLP → check BAR routing or TLP byte layout; doorbell not triggering → check MMIO ring buffer addresses. Evidence: as above.
  Commit: Y | test(func-model-signoff-v3): host CPU communication pathway verification

- [x] 7. Full SoC integration chain verification (T7)
  What to do: Run the complete SoC data path as an integrated chain. (a) Host CPU writes descriptor + weight data via PCIe TLP → NPU Spike boots firmware → firmware reads descriptor from ring buffer → dispatches MXU+SFU+Vector+DMA chain → engines compute → output written to DRAM → host reads output via PCIe TLP → compare against GoldenExecutor; (b) Verify concurrent operation: while Spike+firmware is processing one command chain, host writes the next chain's descriptors via PCIe and rings doorbell — verify no data race; (c) Verify interrupt-driven dispatch: MXU completes → INTC fires → firmware WFI wakes → handler dispatches next op in chain — verify the full async dispatch model works; (d) Run the full chain 3 times back-to-back to verify state reset and repeatability.
  Must NOT do: Do NOT require Spike modifications — use existing ppike_host.py modes. Do NOT fix firmware bugs found — report them.
  Parallelization: Wave 3 | Blocked by: T1-T6 | Blocks: final wave | Can parallelize with: —
  References: All T1-T6 artifacts; `sim/spike_host.py:1181-1270` (forward mode), `sim/spike_host.py:1135-1155` (mmul_smoke mode)
  Acceptance criteria: SoC integration chain runs end-to-end without crash; output matches GoldenExecutor; concurrent host+NPU operation produces correct results; 3-repeat consistency verified. Evidence: `.omo/evidence/task-7-soc-integration.txt`
  QA scenarios: Happy = full chain passes, concurrent operation clean, repeatable. Failure = Spike crash → check firmware ELF; data race → check doorbell/ring buffer synchronization; output mismatch → check each layer's golden compare. Evidence: as above.
  Commit: Y | test(func-model-signoff-v3): full SoC integration chain verification

## Final verification wave
> Runs in parallel after ALL todos (T0-T7). ALL must APPROVE.

- [x] F1. Plan compliance audit: `validate --v3` finds all 8 evidence cases OK, stale detection clean.
  Acceptance: `.omo/evidence/v3-final-plan-compliance.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/v3-final-plan-compliance.txt`
  Commit: N

- [x] F2. Code quality review: compileall on all changed Python files, new test files pass, no forbidden imports, no RTL dependency in v3 signoff harness.
  Acceptance: `.omo/evidence/v3-final-code-quality.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/v3-final-code-quality.txt`
  Commit: N

- [x] F3. Real manual QA: (1) `bash scripts/run_fm_env.sh -- python3 sim/spike_host.py --mode mmul_smoke` → assert exit 0, golden PASS; (2) `PYTHONPATH=sim python3 -m pytest sim/tests/test_func_model_signoff_v3_host.py -v` → assert all PASS; (3) verify Spike plugin ABI: `ldd spike_src/plugins/npu_mmio_plugin.so` → no undefined symbols; (4) verify firmware ELF exists: `file firmware/build/npu_firmware_spike.elf` → ELF 32-bit RISC-V.
  Acceptance: `.omo/evidence/v3-final-real-qa.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/v3-final-real-qa.txt`
  Commit: N

- [x] F4. Scope fidelity: Compare worktree against signoff start commit. Reject RTL changes. Reject any VCS/Spike plugin C++ changes. Allow only `sim/`, `scripts/`, `.omo/`, `docs/bugs/`. Note: firmware (`firmware/`) and Spike (`spike_src/`) are referenced but NOT modified.
  Acceptance: `.omo/evidence/v3-final-scope-fidelity.txt` has `evidence.verdict: pass`
  Evidence: `.omo/evidence/v3-final-scope-fidelity.txt`
  Commit: N

## Commit strategy
| Task | Commit | Message |
|------|--------|---------|
| T0 | Y | feat(func-model-signoff-v3): add v3 SoC signoff runner cases |
| T1 | Y | test(func-model-signoff-v3): Spike+firmware dispatch chain verification |
| T2 | Y | test(func-model-signoff-v3): PCIe DMA pathway functional verification |
| T3 | Y | test(func-model-signoff-v3): crossbar concurrent multi-master verification |
| T4 | Y | test(func-model-signoff-v3): doorbell ring buffer protocol verification |
| T5 | Y | test(func-model-signoff-v3): INTC interrupt delivery chain verification |
| T6 | Y | test(func-model-signoff-v3): host CPU communication pathway verification |
| T7 | Y | test(func-model-signoff-v3): full SoC integration chain verification |
| F1-F4 | N | evidence only |

All commits on `main` branch. Each task commit independent. Per-task commit.

## Success criteria
1. All 4 spike_host.py modes (mmul_smoke, chain, forward, pcie_dma) pass with golden compare
2. PCIe DMA DmaEngine verified — 10/10 tests pass, tag pool management correct, descriptor IRQ chain works
3. Crossbar M=6/S=2 verified — 7/7 tests pass, round-robin fairness within ±20%, no data integrity violations
4. Doorbell ring buffer protocol verified — 7/7 tests pass, descriptor layout matches hardware struct
5. INTC 7-source interrupt chain verified — 9/9 tests pass, per-source identification correct, priority encoder works
6. Host CPU communication verified — 4/4 scenarios pass, data roundtrip bit-exact
7. Full SoC integration chain runs end-to-end 3/3 times with correct output and no data races
8. All 8 v3 evidence cases pass `validate --v3`
9. All new bugs recorded to `docs/bugs/bugs-soc-func-model.md`
10. F1-F4 Final Wave all APPROVE
