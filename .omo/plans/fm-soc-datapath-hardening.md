# fm-soc-datapath-hardening - Work Plan

## TL;DR (For humans)

**What you'll get:** 在 Func Model 层面补齐 SoC 数据通路的验证守卫，让 PCIe TLP 路由错误、中断链断裂、仲裁不公、寄存器语义分歧、固件控制流不对齐、多层累积偏移碰撞等问题在纯 Python 秒级暴露，不用等 7.5h RTL 段跑。同时把 FM 全模型链从 11 层扩展到 28 层，并新增 MobileNetV3 CV 链 FM gate。

**Why this approach:** 探索发现 6 条 SoC 数据通路 gap 的**模型层代码已经存在**（`sim/models/pcie.py`、`crossbar.py`、`apb_peripheral.py`、`miniv.py` SoC mode），缺的不是建模型而是**验证深度**——现有测试只覆盖了"能跑通"，缺少失败注入、并发公平性、ENABLE/THRESHOLD 门控、ring-size 对齐等守卫。因此本计划聚焦"加 FM 层验证用例 + 扩展现有测试"，不是 greenfield 建设。

**What it will NOT do:** 不改 RTL 逻辑；不改 Arc Model / `quantize.py` / `ggml-npu/`；不修复 BUG-RTL-SOC-002/007（RTL 侧排除）；不做性能 calibration（本质需 RTL 实测数据）；不重构 `MMIOBridge.handle` 到 `APBPeripheral`（用 conformance gate 替代，降低风险）。

**Effort:** Medium
**Risk:** Low — 模型层已存在，新增的是 pytest 守卫和测试扩展；唯一结构性变更是 28 层 DRAM 布局重排和 INTC ENABLE/THRESHOLD 门控建模。
**Decisions to sanity-check:** (1) 28 层扩展需要重排 `_DESC_BASE`/`_SCRATCH` 避免与 block 24-27 碰撞；(2) INTC 门控用 conformance gate 而非重构 bridge；(3) Gap #10 ring-size 对齐用相同 208 命令跑两条路径比对。

Your next move: approve, or run a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): Medium effort, low risk; 14 todos across 4 waves adding FM-layer verification guards for 13 SoC data-path gaps (models already exist) + extending FM chain to 28 layers + MobileNetV3 CV chain.

## Scope
### Must have
- **FM 层验证守卫（11 项）**：为 PCIe TLP 完整链、IRQ 链 ENABLE/THRESHOLD 门控、中断驱动调度、AXI 仲裁公平性、APB 寄存器 conformance、Ibex 共享地址空间跨引擎、固件 boot 序列、Spike↔Ibex ring 对齐、`firmware_memory_contract.json` 生成与比对、Spike forward tolerance 回归——每项新增 pytest 用例含 happy + failure 注入。
- **FM 全模型链扩展（2 项）**：28 层 Qwen full-model FM gate（从 todo 5 的 11 层扩展）+ MobileNetV3 CV chain FM gate（复用 `sim/cv/` 基础设施）。
- **INTC 门控建模**：`mmio_bridge._set_irq` 补充 `popcount(PENDING & ENABLE) >= THRESHOLD` 评估逻辑。

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不修改任何 RTL 功能逻辑（`rtl/` 下零改动）。
- 不改 Arc Model（`sim/arc_model.py`、`sim/design_space_explorer.py` 冻结）、`sim/quantize.py`、`ggml-npu/`、`requirements.txt`。
- 不重构 `MMIOBridge.handle` 到 `APBPeripheral`——用 conformance replay gate 替代（降低 bridge 行为变更风险）。
- 不做性能 calibration（`calibration_state=uncalibrated` 需 RTL 实测数据，不可 FM 独立完成）。
- 不修复 BUG-RTL-SOC-002（DRAM 窗口）和 BUG-RTL-SOC-007（attn_weight RTL dispatch）——RTL 侧排除。
- 不删除 `NPUFirmware` deprecated 路径；新增验证与现有路径并存。

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + pytest 框架（`sim/tests/`）；新增断言按当前正确状态编写，每项含 failure 注入用例证明门禁真实。
- Evidence: `build/evidence/task-<N>-fm-soc-datapath-hardening.txt`（与 fm-hardening-phase10 保持一致的 `build/evidence/` 路径）
- 全量回归基线：pytest ≥ 现有基线（19 failed / 2198 passed / 13 errors 为已知遗留，新增 0 失败 0 错误）。

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave. Fewer than 3 (except the final) means you under-split.

- **Wave 1（验证守卫基础，5 todos）**：todo 1 PCIe TLP 完整链验证、todo 2 INTC ENABLE/THRESHOLD 门控建模+验证、todo 3 AXI 仲裁公平性验证、todo 4 APB 寄存器 conformance gate、todo 5 Ibex 共享地址空间跨引擎验证。todo 1/3/4/5 无依赖可并行；todo 2 需改 `_set_irq` 逻辑。
- **Wave 2（中断与固件，4 todos）**：todo 6 中断驱动调度验证（依赖 todo 2）、todo 7 固件 boot 序列验证（依赖 todo 5）、todo 8 Spike↔Ibex ring 对齐验证、todo 9 `firmware_memory_contract.json` 生成与比对。todo 8/9 可与 todo 6/7 并行。
- **Wave 3（全模型扩展，3 todos）**：todo 10 28 层 DRAM 布局重排、todo 11 28 层 FM full-model gate（依赖 todo 10）、todo 12 MobileNetV3 CV chain FM gate。
- **Wave 4（收尾，2 todos）**：todo 13 Spike forward tolerance 回归 gate、todo 14 文档同步 + 反向依赖门禁更新。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (PCIe TLP 验证) | — | — | 3, 4, 5 |
| 2 (INTC 门控建模) | — | 6 | 1, 3, 4, 5 |
| 3 (AXI 仲裁验证) | — | — | 1, 4, 5 |
| 4 (APB conformance) | — | — | 1, 3, 5 |
| 5 (Ibex 共享地址) | — | 7 | 1, 3, 4 |
| 6 (中断驱动调度) | 2 | — | 7, 8, 9 |
| 7 (固件 boot 序列) | 5 | — | 6, 8, 9 |
| 8 (Spike↔Ibex 对齐) | — | — | 6, 7, 9 |
| 9 (memory contract JSON) | — | — | 6, 7, 8 |
| 10 (28 层 DRAM 布局) | — | 11 | 12, 13 |
| 11 (28 层 FM gate) | 10 | — | 12, 13 |
| 12 (MobileNetV3 CV chain) | — | — | 10, 11, 13 |
| 13 (Spike tolerance 回归) | — | — | 10, 11, 12 |
| 14 (文档同步) | 1-13 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.
<!-- APPEND TASK BATCHES BELOW THIS LINE WITH edit/apply_patch - never rewrite the headers above. -->
- [x] 1. PCIe TLP 完整链 FM 验证守卫（SOC-13）
  What to do / Must NOT do: 新增 `sim/tests/test_pcie_tlp_chain.py`：验证 `PCIeModel.tlp_write` → BAR 路由 → crossbar → SRAM/DRAM 写入 → `tlp_read` 读回 bit-exact；验证 4KB 大载荷按 MPS=256B 分裂为多 TLP；验证 BAR 路由隔离（写 SRAM 不影响 DRAM、反之）；failure 注入：篡改 TLP 载荷 → readback 不匹配。复用 `sim/models/pcie.py` 现有实现，不改模型代码。Must NOT 改 `sim/models/pcie.py`。
  Parallelization: Wave 1 | Blocked by: — | Blocks: —
  References: `sim/models/pcie.py:28-174`（PCIeModel tlp_write/tlp_read/_resolve_bar/send_msi）；`sim/func_model.py:39-41,135-196`（host_write_* 路由到 tlp_write）；`sim/tests/test_soc_fm.py:32-229`（现有 PCIe smoke 测试模式）；`docs/soc-fm-gap-spec.md:193-199`（testability plan）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_pcie_tlp_chain.py -v` exit 0，≥5 个测试含 happy + failure 注入
  QA scenarios: happy — 4KB tlp_write → tlp_read bit-exact；failure — 篡改 1 byte 载荷 → readback 不匹配断言失败。Evidence `build/evidence/task-1-fm-soc-datapath-hardening.txt`
  Commit: Y | test(sim): PCIe TLP complete chain FM verification guard

- [x] 2. INTC ENABLE/THRESHOLD 门控建模 + 验证（SOC-17 / FW-10）
  What to do / Must NOT do: (a) `sim/mmio_bridge.py` `_set_irq()` 补充 `popcount(PENDING & ENABLE) >= THRESHOLD` 评估逻辑（当前无条件通知 CPU）；(b) 新增 `sim/tests/test_intc_gating.py`：验证单源 IRQ、ENABLE 屏蔽、THRESHOLD 门控、ACK 清除、WFI 唤醒、多源并发；failure 注入：ENABLE=0 时不应触发 cpu_irq。Must NOT 改 `rtl/intc/intc_top.v`。
  Parallelization: Wave 1 | Blocked by: — | Blocks: 6
  References: `sim/mmio_bridge.py:764-769`（_set_irq 当前实现）；`sim/miniv.py:276-322`（WFI/set_interrupt_pending/_handle_irq）；`docs/soc-fm-gap-spec.md:691-837`（IRQ 链 6 步设计 + testability plan）；`rtl/intc/intc_top.v:1-189`（popcount/threshold RTL 参考）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_intc_gating.py -v` exit 0，≥6 个测试；`PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm.py -q` 无新增失败
  QA scenarios: happy — MXU IRQ + ENABLE + THRESHOLD=1 → cpu_irq=1 → CPU trap；failure — ENABLE=0 → cpu_irq=0 不论 PENDING。Evidence `build/evidence/task-2-fm-soc-datapath-hardening.txt`
  Commit: Y | fix(sim): INTC ENABLE/THRESHOLD gating + verification

- [x] 3. AXI 仲裁公平性 FM 验证守卫（SOC-14）
  What to do / Must NOT do: 新增 `sim/tests/test_crossbar_arbitration.py`：利用 `CrossbarModel._aw_grants`/`_ar_grants` 历史记录，验证多 master 并发下 round-robin 交替公平；验证 DECERR 地址正确拒绝；验证 AXI ID 路由保持 master_id<<8|txn_id。复用 `sim/models/crossbar.py` 现有实现。Must NOT 改 `sim/models/crossbar.py`。
  Parallelization: Wave 1 | Blocked by: — | Blocks: —
  References: `sim/models/crossbar.py:19-178`（CrossbarModel read/write/_grant/_next_axi_id）；`sim/tests/test_soc_fm.py:184-450`（现有 crossbar 测试）；`docs/soc-fm-gap-spec.md:210-437`
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_crossbar_arbitration.py -v` exit 0，≥4 个测试含公平性 + DECERR + failure 注入
  QA scenarios: happy — 3 master 并发 100 事务 → grant 历史交替均匀；failure — 篡改 grant 历史 → 公平性断言失败。Evidence `build/evidence/task-3-fm-soc-datapath-hardening.txt`
  Commit: Y | test(sim): AXI crossbar arbitration fairness guard

- [x] 4. APB 寄存器 conformance replay gate（SOC-15）
  What to do / Must NOT do: 新增 `sim/tests/test_apb_register_conformance.py`：对每个 peripheral 工厂（`make_mxu/sfu/vector/dma/pcie/doorbell/intc/pcie_dma_peripheral`），replay 一组 write→readback 序列，断言寄存器语义（rw/r/w/w1c）与 `regmap.py` 一致；failure 注入：写只读寄存器 → 值不变。Must NOT 重构 `MMIOBridge.handle` 到 `APBPeripheral`。
  Parallelization: Wave 1 | Blocked by: — | Blocks: —
  References: `sim/models/apb_peripheral.py:68-395`（APBPeripheral + 8 factories）；`sim/regmap.py:96-181`（Addr/MXU/SFU/VECTOR/DMA/DOORBELL/INTC）；`sim/tests/test_soc_fm.py`（现有 APB 测试模式）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_apb_register_conformance.py -v` exit 0，≥8 个 peripheral 各一组 readback + 语义断言
  QA scenarios: happy — write CTRL=0x3 → readback CTRL=0x3；failure — write 只读 STATUS → readback 不变。Evidence `build/evidence/task-4-fm-soc-datapath-hardening.txt`
  Commit: Y | test(sim): APB register conformance replay gate

- [x] 5. Ibex 共享地址空间跨引擎 FM 验证守卫（SOC-16）
  What to do / Must NOT do: 新增 `sim/tests/test_ibex_shared_address_space.py`：验证 Ibex 通过 crossbar 写 SRAM → MXU 通过 bridge 读同一 SRAM 地址 → 结果一致；验证 DMEM 隔离（写 DMEM 不影响 SRAM/DRAM）；验证 boot ROM 隔离。复用 `RISCVMini` SoC mode 现有实现。Must NOT 改 `sim/miniv.py` 的 `_mem_read/_mem_write` 逻辑。
  Parallelization: Wave 1 | Blocked by: — | Blocks: 7
  References: `sim/miniv.py:58-162`（RISCVMini SoC mode _mem_read/_mem_write crossbar 路由）；`sim/func_model.py:72-77`（RISCVMini 构造）；`sim/tests/test_soc_fm.py:481-595`（现有 Ibex 内存测试）；`docs/soc-fm-gap-spec.md:564-688`
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_ibex_shared_address_space.py -v` exit 0，≥4 个测试含跨引擎 + 隔离 + failure 注入
  QA scenarios: happy — Ibex 写 SRAM[0x100] → MXU 读 SRAM[0x100] 一致；failure — 写 DMEM → 读 SRAM → 值不同。Evidence `build/evidence/task-5-fm-soc-datapath-hardening.txt`
  Commit: Y | test(sim): Ibex shared address space cross-engine guard

- [ ] 6. 中断驱动 firmware 调度 FM 验证守卫（FW-10）
  What to do / Must NOT do: 新增 `sim/tests/test_irq_driven_dispatch.py`：多命令流中 op N 完成由 IRQ 信号驱动（非 STATUS 轮询）→ firmware 调度 op N+1；验证无轮询 fallback（当 riscv bound 时）；failure 注入：抑制 IRQ → firmware 不前进。依赖 todo 2 的 ENABLE/THRESHOLD 门控。Must NOT 改 `firmware/npu_firmware.c` 控制流。
  Parallelization: Wave 2 | Blocked by: 2 | Blocks: —
  References: `sim/miniv.py:490-519,720-735`（NPUFirmware.run_loop/_wait_done 中断驱动）；`sim/miniv.py:521-546`（dispatch_interrupt）；`sim/func_model.py:79-83`（riscv.irq_handler + firmware.bind_riscv）；`docs/soc-fm-gap-spec.md:1005`（test_firmware_interrupt_dispatch 设计）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_irq_driven_dispatch.py -v` exit 0，≥3 个测试含 happy + failure 注入
  QA scenarios: happy — 3 命令流全由 IRQ 驱动完成；failure — monkeypatch 抑制 IRQ → firmware 停在第 1 命令。Evidence `build/evidence/task-6-fm-soc-datapath-hardening.txt`
  Commit: Y | test(sim): IRQ-driven firmware dispatch guard

- [ ] 7. 固件 boot 序列 FM 验证守卫（SOC-18）
  What to do / Must NOT do: 新增 `sim/tests/test_firmware_boot_sequence.py`：验证 `NPUFirmware.boot()` 设置 PC=0、sp=DMEM top、加载 hex → `RISCVMini.step()` 从 reset 执行进到 firmware main → doorbell poll → 首条命令完成；验证 boot ROM 隔离（DMEM 写不影响 boot ROM）。复用 `NPUFirmware.boot` + `RISCVMini.load_hex` 现有实现。Must NOT 改 `RISCVMini` 指令集。
  Parallelization: Wave 2 | Blocked by: 5 | Blocks: —
  References: `sim/miniv.py:477-488`（NPUFirmware.boot）；`sim/miniv.py:349-374`（RISCVMini.load_hex）；`sim/func_model.py:84-86`（firmware.boot 调用）；`sim/tests/test_soc_fm.py:687`（现有 test_firmware_bootflow）；`docs/soc-fm-gap-spec.md:839-1016`
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_firmware_boot_sequence.py -v` exit 0，≥3 个测试
  QA scenarios: happy — boot → step() → doorbell → MMUL dispatch → done；failure — boot ROM 损坏 → step() 不前进。Evidence `build/evidence/task-7-fm-soc-datapath-hardening.txt`
  Commit: Y | test(sim): firmware boot sequence FM guard

- [ ] 8. Spike↔Ibex ring 管理对齐 FM 验证守卫（FW-08）
  What to do / Must NOT do: 新增 `sim/tests/test_spike_ibex_ring_alignment.py`：同一组 208 命令分别通过 Spike 路径（`spike_host.schedule_chain` + `_launch_spike`）和 NPUFirmware 路径（`host_write_command` + `run_loop`）执行，交叉比对 NPU_HEAD/HOST_HEAD/COMPLETION_STATUS 和 ring wrap 行为一致；failure 注入：篡改一条路径的 ring_size → 行为分歧。Must NOT 改 `spike_host.py` 或 `miniv.py` 的 ring 管理逻辑。
  Parallelization: Wave 2 | Blocked by: — | Blocks: —
  References: `sim/spike_host.py:160-205`（schedule_chain/poll_completion）；`sim/miniv.py:490-519`（NPUFirmware.run_loop）；`sim/command_ring.py:13-70`（shared constants/helpers）；`sim/tests/test_soc_fm_long_sequence.py`（208 命令模式）；`sim/tests/test_npu_firmware_deprecation.py`
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_spike_ibex_ring_alignment.py -v` exit 0，≥3 个测试含对齐 + failure 注入
  QA scenarios: happy — 208 命令两路径 NPU_HEAD 序列一致；failure — NPUFirmware ring_size=16 vs Spike 1024 → wrap 行为分歧断言。Evidence `build/evidence/task-8-fm-soc-datapath-hardening.txt`
  **环境依赖**: 测试需要 Spike binary + `firmware/build/npu_firmware_spike.elf`（注意：不是 `npu_firmware.elf`，`sim/spike_firmware.py:28` 定义 `FIRMWARE_ELF` 指向 spike 版本）。缺失时测试必须 `pytest.skip(reason="Spike binary not available")`，复用 `sim/spike_firmware._is_spike_available()` 检测模式。acceptance 命令在无 Spike 环境下 exit 0（0 collected / all skipped）。
  Commit: Y | test(sim): Spike↔Ibex ring alignment guard

- [x] 9. `firmware_memory_contract.json` 生成与比对（FW-09）
  What to do / Must NOT do: 新增 `scripts/gen_firmware_memory_contract.py`：从 `sim/address_space.py` REGIONS + `sim/command_ring.py` 常量 + 实际 run 的 descriptor range/max ring offset 生成 JSON 契约文件；新增 `sim/tests/test_memory_contract.py`：验证 FM 生成的 JSON 与 `spec/npu_abi.json` 常量一致；failure 注入：篡改 JSON 中 RING_ENTRIES → 比对失败。Must NOT 把 JSON 作为新事实源（事实源仍是 `address_space.py`/`command_ring.py`/`spec/npu_abi.json`）。
  Parallelization: Wave 2 | Blocked by: — | Blocks: —
  References: `sim/address_space.py:61-149`（REGIONS/contract_check）；`sim/command_ring.py:13-70`（constants/guards）；`spec/npu_abi.json:1435,1579-1582`（rings.configuration）；`sim/tests/test_npu_abi_constants.py`（现有常量比对模式）
  Acceptance criteria (agent-executable): `python3 scripts/gen_firmware_memory_contract.py --check` exit 0；`PYTHONPATH=sim python -m pytest sim/tests/test_memory_contract.py -v` exit 0
  QA scenarios: happy — JSON 与 address_space/command_ring 数值一致；failure — 篡改 JSON RING_ENTRIES=512 → 比对失败。Evidence `build/evidence/task-9-fm-soc-datapath-hardening.txt`
  Commit: Y | feat(scripts): firmware memory contract JSON generation + verification

- [ ] 10. 28 层 DRAM 布局重排（E2E-04 前置）
  What to do / Must NOT do: 修改 `sim/tests/test_soc_fm_long_sequence.py` 的 DRAM 布局常量。当前布局在 28 层时碰撞分析如下：
  - block 23 范围 `0x805D_0000–0x8061_0000`，`_DESC_BASE=0x8060_0000` + 531×64=`0x8060_84C0` 落在 block 23 内部 → **descriptor pool 与 block 23 碰撞**。
  - block 27 范围 `0x806D_0000–0x8071_0000`，`_SCRATCH_MMUL_OUT=0x8070_0000` 落在 block 27 内部 → **scratch 与 block 27 碰撞**。
  - block 27（0-indexed，即第 28 个 block）结束地址 = `0x8001_0000 + 28×0x40000 = 0x8071_0000`。
  重排方案（精确地址表）：
  | 区域 | 当前地址 | 重排后地址 | 大小 | 说明 |
  |------|---------|-----------|------|------|
  | block 0–27 | 0x80010000–0x80710000 | 不变 | 28×0x40000=0x700000 | 7 MB，在 8MB 窗口内 |
  | desc pool | 0x80600000 | **0x80710000** | 531×64=0x84C0 ≈ 34KB | 紧接 block 28 之后 |
  | desc pool end | 0x806084C0 | **0x807184C0** | — | < 0x80800000 ✓ |
  | scratch | 0x80700000 | **0x80720000** | ~64KB | desc pool 之后 |
  | scratch end | 0x80710000 | **0x80730000** | — | < 0x80800000 ✓ |
  | act/results | 0x80800000 | 不变 | — | DRAM 窗口末端 |
  更新 `assert_desc_clear_of_used_regions` 检查范围以覆盖 block 0–27 + desc pool + scratch。Must NOT 改 `sim/address_space.py` 或 `sim/command_ring.py` 的全局常量（只改测试局部常量）。
  Parallelization: Wave 3 | Blocked by: — | Blocks: 11
  References: `sim/tests/test_soc_fm_long_sequence.py:59-66`（_NUM_LAYERS/_DESC_BASE/_SCRATCH_MMUL_OUT）；`sim/tests/test_soc_fm_long_sequence.py:165-171`（desc overlap assertion）；`sim/tests/test_soc_fm.py:2630-2632`（_CHAIN_BLOCK_BASE/STRIDE/RESULT_BASE）；计算：28 blocks span 0x80010000-0x80710000，desc pool 531*64=0x84C0 需在 0x80710000 之后）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -c "from sim.tests.test_soc_fm_long_sequence import _DESC_BASE, _SCRATCH_MMUL_OUT; from sim.tests.test_soc_fm import _CHAIN_BLOCK_BASE, _CHAIN_BLOCK_STRIDE; assert _DESC_BASE >= _CHAIN_BLOCK_BASE + 28*_CHAIN_BLOCK_STRIDE; assert _DESC_BASE + 531*64 < 0x80800000; assert _SCRATCH_MMUL_OUT + 0x10000 < 0x80800000; assert _SCRATCH_MMUL_OUT >= _DESC_BASE + 531*64; print('LAYOUT OK')"` 打印 LAYOUT OK exit 0
  QA scenarios: happy — 28 blocks + desc pool 不重叠且在 [0x80000000,0x80800000) 内；failure — 故意设 _DESC_BASE=0x80600000 → overlap assertion 抛错。Evidence `build/evidence/task-10-fm-soc-datapath-hardening.txt`
  Commit: Y | fix(sim): relocate DRAM layout for 28-layer FM chain

- [ ] 11. 28 层 Qwen full-model FM gate（E2E-04）
  What to do / Must NOT do: 修改 `sim/tests/test_soc_fm_long_sequence.py`：`_NUM_LAYERS` 从 11 改为 28（531 命令），调整 ring wrap 断言（531 % 16 = 3，不再整除 → 改为 wrap_count >= 33 而非 `total_cmds % 16 == 0`）；验证每层输出与 direct-path golden bit-exact、末层 cos >= 0.999；failure 注入保留 layer 5 op14 VMUL 描述符地址篡改。依赖 todo 10 布局重排。Must NOT 改调度算法。
  Parallelization: Wave 3 | Blocked by: 10 | Blocks: —
  References: `sim/tests/test_soc_fm_long_sequence.py:59,147-374,420-467`（现有 11 层测试结构）；`sim/tests/test_soc_fm.py:2453-2764`（28-block scaled chain 模式）；`sim/spike_host.py:1317-1451`（Phase-10 36 层 forward 路径参考）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_soc_fm_long_sequence.py::test_multi_layer_persistent_offset -v` exit 0，运行时间 < 5 分钟
  QA scenarios: happy — 28 层 531 命令持久偏移跑通、末层 cos >= 0.999；failure — layer 5 op14 篡改 → layer 5 输出失配。Evidence `build/evidence/task-11-fm-soc-datapath-hardening.txt`
  Commit: Y | test(sim): 28-layer full-model FM persistent-offset gate

- [ ] 12. MobileNetV3 CV chain FM gate（E2E-05）
  What to do / Must NOT do: 新增 `sim/tests/test_mobilenetv3_fm_chain.py`：用 `sim/cv/cv_trace.py:generate_mobilenetv3_trace(onnx_path)` 或 `sim/cv/onnx_importer.py:import_mobilenetv3` 获取层列表，转换为 `{mmul,sfu,vector}` op dict，通过 `FuncModel.host_write_command` + `firmware.run_loop` 调度为 ring 命令；验证每层输出与 `GoldenMXU.matmul_int4_per_block` golden cos_sim >= 0.99。复用 `sim/cv/cv_command_ir.py` 转换模式和 `sim/tests/test_cv_mobilenetv3.py` golden 比对模式。Must NOT 改 `sim/cv/` 模块代码。
  Parallelization: Wave 3 | Blocked by: — | Blocks: —
  References: `sim/cv/cv_trace.py:175`（generate_mobilenetv3_trace）；`sim/cv/onnx_importer.py`（import_mobilenetv3）；`sim/cv/cv_command_ir.py`（convert_layer_list/convert_mobilenetv3_graph）；`sim/tests/test_cv_mobilenetv3.py`（W3.4 golden 比对模式）；`sim/tests/test_soc_fm_long_sequence.py:131-145`（_issue ring 调度模式）
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_mobilenetv3_fm_chain.py -v` exit 0，≥15 conv 层 cos_sim >= 0.99
  QA scenarios: happy — MobileNetV3 全链通过 doorbell ring 调度、输出与 golden 对齐；failure — 篡改某层权重地址 → 输出失配。Evidence `build/evidence/task-12-fm-soc-datapath-hardening.txt`
  **环境依赖**: 测试需要 `assets/mobilenetv3_small.onnx`。缺失时测试必须 `pytest.skip(reason="MobileNetV3 ONNX model not found")`，用 `pathlib.Path("assets/mobilenetv3_small.onnx").is_file()` 检测。acceptance 命令在无 ONNX 环境下 exit 0（0 collected / all skipped）。
  Commit: Y | test(sim): MobileNetV3 CV chain FM gate

- [ ] 13. Spike forward pass tolerance 回归 gate（E2E-06）
  What to do / Must NOT do: 新增 `sim/tests/test_spike_forward_tolerance.py`：固定当前 acceptance 阈值为回归基线（2 层 max_abs < 1e-1；36 层 cos_sim ladder 0.999/0.998/0.997），跑 `spike_host.run_forward_pass()` 并断言 `result["ok"] == True`（2层）或标注为 residual tolerance divergence（BUG-SOC-FM-005 已 Fixed，但 36 层 ladder 的 cos_sim 阈值是预期的量化精度带，不是 bug 残留）。**注意**: `run_forward_pass()` 返回 dict（字段 `ok`/`errors`/`layer_outputs`），无 `tolerance_result` 字段——测试需包装返回值或直接断言 `result["ok"]` 与每层 ladder 阈值。Must NOT 修复数值 gap（超出范围，属量化精度本质）。
  Parallelization: Wave 4 | Blocked by: — | Blocks: —
  References: `sim/spike_host.py:788-1017`（run_forward_pass tolerance）；`sim/spike_host.py:1317-1451`（Phase-10 36 层 ladder）；`sim/spike_host.py:377-390`（P10_LADDER 阈值）；`docs/bugs/bugs-soc-func-model.md:305-388`（BUG-SOC-FM-005）；`docs/func-model-golden-tolerance.md`
  Acceptance criteria (agent-executable): `PYTHONPATH=sim python -m pytest sim/tests/test_spike_forward_tolerance.py -v` exit 0；测试体内断言 `result["ok"] == True`（2层）；36 层 ladder 断言 cos_sim 逐层 ≥ P10_LADDER 阈值（residual tolerance divergence，非 bug）
  QA scenarios: happy — 2 层 forward max_abs < 1e-1 → result["ok"]=True → PASS；failure — 阈值收紧到 1e-5 → result["ok"]=False → FAIL（已知 gap）。Evidence `build/evidence/task-13-fm-soc-datapath-hardening.txt`
  **环境依赖**: 测试需要 Spike binary + `firmware/build/npu_firmware_spike.elf`（注意：不是 `npu_firmware.elf`，`sim/spike_firmware.py:28` 定义 `FIRMWARE_ELF` 指向 spike 版本）+ GGUF 模型（`$HOME/models/qwen2.5-1.5b-instruct-q4_k_m.gguf`）。缺失时测试必须 `pytest.skip(reason="Spike/GGUF not available")`，复用 `sim/spike_firmware._is_spike_available()` + `pathlib.Path(model_path).is_file()` 检测。acceptance 命令在无 Spike/GGUF 环境下 exit 0（all skipped）。
  Commit: Y | test(sim): Spike forward pass tolerance regression gate

- [ ] 14. 文档同步 + 反向依赖门禁更新
  What to do / Must NOT do: (a) 更新 `docs/verification_methodology.md` FM → RTL 交付门禁表，把新增 11 项守卫纳入门禁清单；(b) 更新 `.omo/plans/soc-rtl-verification-vplan.md` 中 14 项缺口的状态：在每行缺口描述旁插入 feature ID 标记（`SOC-13`/`SOC-14`/`SOC-15`/`SOC-16`/`SOC-17`/`SOC-18`/`FW-08`/`FW-09`/`FW-10`/`E2E-04`/`E2E-05`/`E2E-06`/`E2E-08`），并将状态从 ❌/⚠️ 改为 ✅（E2E-07 性能 calibration 仍 ❌）；(c) 更新 `scripts/fm_reverse_dependency_gate.sh` 敏感文件清单，加入新增测试文件。Must NOT 改其他文档。
  Parallelization: Wave 4 | Blocked by: 1-13 | Blocks: —
  References: `docs/verification_methodology.md:131-150`（FM → RTL 交付门禁表）；`.omo/plans/soc-rtl-verification-vplan.md`（vplan 缺口状态）；`scripts/fm_reverse_dependency_gate.sh:36-41`（敏感文件清单）
  Acceptance criteria (agent-executable): `grep -c "test_pcie_tlp_chain\|test_intc_gating\|test_crossbar_arbitration" docs/verification_methodology.md` ≥3；vplan 中 13 个指定缺口行状态从 ❌/⚠️ 变为 ✅，用 `grep -E 'SOC-1[3-8]|FW-0[89]|FW-10|E2E-0[4568]' .omo/plans/soc-rtl-verification-vplan.md | grep -c '✅'` ≥13 验证（worker 需先在 vplan 对应行插入这些 feature ID 标记）
  QA scenarios: happy — 门禁表含新增守卫 + vplan 状态更新；failure — 删除新增行 → grep 失败。Evidence `build/evidence/task-14-fm-soc-datapath-hardening.txt`
  Commit: Y | docs(verify): integrate FM SoC datapath hardening into methodology + vplan

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [ ] F1. Plan compliance audit
  What: 逐条核对 14 个 todo 的 evidence 文件存在、acceptance 命令可复跑通过。
  Command: 需更新 `scripts/fm_hardening_f1_audit.sh` 使其接受计划名参数（`FM_PLAN_NAME` 环境变量，默认 `fm-hardening-phase10`），或新增 `scripts/fm_soc_datapath_f1_audit.sh` 检查 `build/evidence/task-{1..14}-fm-soc-datapath-hardening.txt` 存在且终态 PASS；重跑各 todo 的 pytest acceptance 命令并比对退出码。不得无条件 exit 0。
  Pass: 全部 evidence 存在、14/14 acceptance 复跑通过。

- [ ] F2. Code quality review
  What: 无 TODO/FIXME/HACK 残留、无新增 pytest 失败、`bash -n` 通过。
  Command: `bash scripts/fm_hardening_f2_code_quality.sh`
  Pass: 0 残留、0 新增失败、所有 shell 语法通过。

- [ ] F3. Real manual QA
  What: 全量 pytest + `make -C firmware` + 反向依赖门禁 dry-run + 28 层 FM gate + MobileNetV3 FM chain spot check。
  Command: `bash scripts/fm_hardening_f3_manual_qa.sh`
  Pass: 五项全过（Spike smoke 已知问题标 known-issue）。

- [ ] F4. Scope fidelity
  What: `git diff` 只含 sim/tests/ 新增、sim/mmio_bridge.py INTC 门控、scripts/、docs/、.omo/；`rtl/` 零改动；冻结文件零改动。
  Command: `bash scripts/fm_hardening_f4_scope_gate.sh`
  Pass: 无越界文件、冻结文件零改动。

## Commit strategy
- 每个 todo 一个原子 commit（类型按 todo 的 Commit 行）；证据文件随 todo 一并提交到 `build/evidence/`。
- todo 2 修改 `sim/mmio_bridge.py` 是唯一行为变更，commit message 注明 INTC 门控逻辑变更。
- todo 10 修改测试局部常量，不改全局常量。
- Wave 4 结束后总结合并提交：`docs(fm-soc-datapath): closure note for fm-soc-datapath-hardening`。

## Success criteria
- 13 项 SoC 数据通路 FM 验证守卫全部就位，每项含 happy + failure 注入用例。
- INTC ENABLE/THRESHOLD 门控逻辑建模并验证。
- 28 层 Qwen full-model FM gate 通过（531 命令，末层 cos >= 0.999）。
- MobileNetV3 CV chain FM gate 通过（≥15 conv 层 cos_sim >= 0.99）。
- `firmware_memory_contract.json` 生成与比对可用。
- vplan 中 14 项缺口状态更新：13 项 → ✅，1 项（性能 calibration）仍 ❌。
- 全量 pytest 无新增失败（相对现有基线 19 failed / 2198 passed / 13 errors）。
- F1-F4 全 APPROVE。
