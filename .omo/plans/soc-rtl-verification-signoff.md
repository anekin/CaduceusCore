# soc-rtl-verification-signoff - Work Plan

## TL;DR (For humans)

**What you'll get:** 把 SoC RTL 验证的 feature coverage 从 79%（52/66）推进到全部 blocking 缺口闭环——13 个 Func Model 守卫对应的 RTL 回归测试就位、5 个过期 bug 台账更新（含 1 个正式 waiver）、Ibex 36 层 checkpoint 从 5 个扩展到 8 个、MobileNetV3 CV 链 RTL 首跑、FM-SOC-10X RMSNorm 失败修复——最终在 EDA 服务器上跑通全量 RTL 回归。

**Why this approach:** Func Model 层已 100%就位（fm-soc-datapath-hardening 完成），但 RTL 层仍有 9 个"部分覆盖"和 2 个"无覆盖"缺口。本计划按"先补 bug 台账+waiver → 再补 RTL 测试 → 再跑 E2E 扩展 → 最后全量回归+文档同步"的波次推进，每波独立可并行，全量回归在所有新测试就位后才跑。

**What it will NOT do:**
- 不做性能 calibration（E2E-07，依赖流片/FPGA 实测，deferred）
- 不扩 `dram_model.v`（保持 phase10 固件约束方案，只补正式 waiver）
- 不碰 Arc Model / quantize / ggml-npu / requirements.txt
- 不修改 firmware 功能语义（除非 FM-SOC-10X 诊断确证根因在固件侧）
- 不在非 sz0001 机器上跑 VCS
- F-FM-SOC-09（memory contract JSON）是静态 artifact 检查，不涉及 RTL 仿真，N/A
- MobileNetV3 RTL 首跑范围限定为"现有 sim/cv golden 通过 MXU wrapper"——如果需要新增 CV 算子（im2col engine / pool），必须先获得用户批准
- FM-SOC-10X 诊断 timebox 1 个工作日；若未定位，转 waiver 而非无限深挖

**Effort:** XL
**Risk:** High — 36 层 checkpoint 扩展 + MobileNetV3 RTL 首跑 + FM-SOC-10X 诊断均有较大不确定性，已加 timebox
**Decisions to sanity-check:**
- Ibex 36 层用扩展 checkpoint（8 个点，~16h）而非全量 36 层（~30h）
- FM-SOC-10X RMSNorm 包含在本计划修复（而非 defer）
- BUG-RTL-SOC-002 用正式 waiver（而非扩 DRAM 模型）

Your next move: approve, or run a high-accuracy review first. Full execution detail follows below.

---

> TL;DR (machine): XL effort, high risk; deliverables: 13 FM 守卫→RTL 回归映射, 5 bug 台账更新+1 waiver, Ibex 8-checkpoint 扩展, MobileNetV3 RTL 首跑, FM-SOC-10X 修复, 全量 RTL 回归, vplan/signoff 同步

## Scope

### Must have
- **C1 Bug 台账更新 + 正式 waiver**：更新 `docs/bugs/bugs-soc-rtl.md` 中 5 个过期条目（P9-00A/P9-00D/MXU-P9-00B → Fixed；BUG-RTL-SOC-002 → Waived）；为 BUG-RTL-SOC-002 8MB DRAM 约束提交正式 waiver 文件
- **C2 RTL 回归测试补全**：为 13 个 F-FM-SOC 守卫中 9 个"部分覆盖"和 2 个"无覆盖"创建或扩展 RTL 级（VCS/Cocotb）测试
- **C3 Ibex 36 层 checkpoint 扩展**：从 5 个 checkpoint（L0/L10/L20/L30/L35）扩展到 8 个（加 L5/L15/L25），~16h VCS
- **C4 MobileNetV3 RTL 首跑**：使用现有 `sim/cv` golden，通过 MXU wrapper 跑 MobileNetV3 CV 链
- **C5 FM-SOC-10X RMSNorm 修复**：诊断 op00 RMSNorm 失败根因，修复，验证 FM-SOC-10X PASS
- **C6 BUG-RTL-SOC-007 chain-level 诊断**：跑 17-op block 级 attn_weight dispatch，确认 cycles>0（不只用 shape case）
- **C7 全量 RTL 回归**：33 FM-SOC cases + 新测试 + 扩展 checkpoint 在 sz0001 上跑通
- **C8 文档同步**：更新 vplan 汇总表 + signoff checklist

### Must NOT have (guardrails, anti-slop, scope boundaries)
- 不碰 `sim/arc_model.py` / `sim/design_space_explorer.py` / `sim/quantize.py` / `ggml-npu/` / `requirements.txt`
- 不扩 `rtl/ip/dram_model.v`（保持固件约束 + waiver）
- 不做性能 calibration（E2E-07）
- 不修改 firmware 功能语义，除非 todo 11 诊断确证根因在固件侧
- 不在非 sz0001 机器上跑 VCS 验证
- F-FM-SOC-09（memory contract JSON）是静态 artifact 检查，不涉及 RTL 仿真，N/A
- MobileNetV3 RTL 首跑范围限定为"现有 sim/cv golden 通过 MXU wrapper"——如果需要新增 CV 算子（im2col engine / pool），必须先获得明确批准
- FM-SOC-10X 诊断 timebox 1 个工作日；若未定位，转 waiver 而非无限深挖

## Verification strategy
> Zero human intervention - all verification is agent-executed.
- Test decision: tests-after + VCS/Cocotb 回归在 sz0001 + evidence 文件落盘
- Evidence: `build/evidence/task-{N}-soc-rtl-verification-signoff.txt`（统一命名，每 todo 一份；必须包含时间戳、commit、精确命令、PASS/FAIL 状态）
- 每波门禁：该波全部 acceptance 命令通过 + 已有 FM-SOC 回归无新增 FAIL
- 修复型 todo 一律要求"诊断证据 → 修复 → 因果 gate"三段式
- RTL/firmware 修改走 feature branch，每次修改后先跑模块级回归再合并
- p10 脚本库（`scripts/p10_lib/p10_sz0001.sh`）复用，所有 VCS 在 sz0001 (192.168.0.11)

## Execution strategy
### Parallel execution waves
> Target 5-8 todos per wave.

- **Wave 1（bug 台账 + waiver + RTL gap tests part 1，5 todos）**：bug 台账/waiver 独立；PCIe/INTC/crossbar/APB 测试创建独立可并行。无依赖。
- **Wave 2（RTL gap tests part 2 + SFU fix，6 todos）**：Ibex 共享地址空间/IRQ stall/boot 断言/ring-wrap/SFU 修复独立可并行。无依赖（除 todo 11 依赖 sz0001 基础设施）。
- **Wave 3（E2E RTL 扩展，4 todos）**：corrupted-descriptor/MobileNetV3/checkpoint 扩展/attn_weight chain 独立可并行。todo 14 与 todo 15 均被 todo 11 阻塞（FM-SOC-10X 未修复前，长跑这些 chain 会浪费）。
- **Wave 4（全量回归 + 文档同步，2 todos）**：被 Wave 1-3 全部阻塞。
- **Final（F1-F4）**：全并行，全部 APPROVE 后交用户签收。

### Dependency matrix
| Todo | Depends on | Blocks | Can parallelize with |
| --- | --- | --- | --- |
| 1 (bug 台账) | — | 17 | 2-5 |
| 2 (waiver) | 1 | 17 | 3-5 |
| 3 (PCIe TLP) | — | 16 | 1-2,4-5 |
| 4 (INTC THRESHOLD) | — | 16 | 1-3,5 |
| 5 (crossbar fairness) | — | 16 | 1-4 |
| 6 (APB conformance) | — | 16 | 7-11 |
| 7 (Ibex shared addr) | — | 16 | 6,8-11 |
| 8 (IRQ stall) | — | 16 | 6-7,9-11 |
| 9 (boot assertions) | — | 16 | 6-8,10-11 |
| 10 (ring-wrap stress) | — | 16 | 6-9,11 |
| 11 (FM-SOC-10X fix) | — | 14,15,16 | 6-10,12-13 |
| 12 (corrupted-desc) | — | 16 | 6-11,13 |
| 13 (MobileNetV3 RTL) | — | 16 | 6-12 |
| 14 (Ibex checkpoint) | 11 | 16 | 6-13 |
| 15 (attn_weight chain) | 11 | 16 | 6-14 |
| 16 (full regression) | 1-15 | 17 | — |
| 17 (doc sync) | 16 | — | — |

## Todos
> Implementation + Test = ONE todo. Never separate.

### Wave 1 — Bug 台账 + Waiver + RTL gap tests part 1

- [x] 1. 更新 bug 台账：P9-00A/P9-00D/MXU-P9-00B → Fixed，BUG-RTL-SOC-002 → Waived（附证据链接）
  What to do / Must NOT do: 更新 `docs/bugs/bugs-soc-rtl.md`：(a) BUG-RTL-SOC-P9-00A Status open→Fixed，Fix 引用 `8dd5dbe`+`b545b1f`，Verification 引用 `build/evidence/ph9-divergence-report.txt`（该 evidence 将在 todo 16 全量回归中复跑生成；若当前不存在，以 phase10 notepad 中记录的 3 cases cos=1.0 为 transitional evidence）；(b) BUG-RTL-SOC-P9-00D Status open→Fixed，Fix 引用 phase10 task-8 commit `7aec7a3`，Verification 引用 `build/evidence/task-8-phase10-rtl-verification.txt`（同前，待 todo 16 复跑生成；transitional evidence 为 PERF-06 cos=1.000000）；(c) BUG-MXU-P9-00B Status rtl-suspect→Fixed，Fix 引用同 P9-00A，Verification 引用 `docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md`（Status=resolved）；(d) BUG-RTL-SOC-007 Status 保持 Open，但在 Verification 字段追加 phase10 PERF-13 证据（attn_weight cycles=42311 cos=1.0）+ todo 15 重跑引用，标注"chain-level RTL reproduction 仍待 todo 15"；(e) BUG-RTL-SOC-002 Status Open→Waived，引用 todo 2 的 waiver 文件。同步更新 Final Bug Statistics 段（L363-405）的 By Status 表。Must NOT 在没有证据的情况下改状态；Must NOT 删除任何 bug 条目。
  Parallelization: Wave 1 | Blocked by: — | Blocks: 17
  References: `docs/bugs/bugs-soc-rtl.md:78-110`（BUG-002），`:324-359`（BUG-007），`:407-431`（P9-00A），`:459-484`（P9-00D），`:433-457`（MXU-P9-00B），`:363-405`（统计段）；`docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md`；`.omo/notepads/phase10-rtl-verification/issues.md:182`（FM-SOC-10X 失败记录）
  Acceptance criteria (agent-executable): `grep -A2 'BUG-RTL-SOC-P9-00A' docs/bugs/bugs-soc-rtl.md | grep -c 'Fixed'` exit 0；`grep -A2 'BUG-RTL-SOC-P9-00D' docs/bugs/bugs-soc-rtl.md | grep -c 'Fixed'` exit 0；`grep -A2 'BUG-MXU-P9-00B' docs/bugs/bugs-soc-rtl.md | grep -c 'Fixed'` exit 0；`grep -A2 'BUG-RTL-SOC-002' docs/bugs/bugs-soc-rtl.md | grep -c 'Waived'` exit 0；`grep -A5 'BUG-RTL-SOC-007' docs/bugs/bugs-soc-rtl.md | grep -E 'PERF-13|cycles>0' | wc -l` 输出 ≥1
  QA scenarios: happy — grep 显示 4 个 Fixed + 1 个 Waived + 1 个 Open(带新证据)；failure — 统计段未同步或 BUG-007 无 PERF-13/cycles>0 引用。Evidence `build/evidence/task-1-soc-rtl-verification-signoff.txt`
  Commit: Y | docs(bugs): update SoC RTL bug ledger with phase9/10 evidence + waiver

- [x] 2. 提交正式 waiver WVR-SOC-RTL-002：BUG-RTL-SOC-002 8MB DRAM 窗口约束
  What to do / Must NOT do: (a) `mkdir -p docs/waivers`；(b) 新建 `docs/waivers/WVR-SOC-RTL-002.md`：Bug ID = BUG-RTL-SOC-002；约束描述 = firmware `dram_range_ok()` 拒绝地址 >8MB（`firmware/npu_firmware.c:458,472-485`）；影响 = 大模型（>8MB 权重）需分段预载或后续扩 `dram_model.v`；临时/永久 = 临时（FPGA 阶段扩 DRAM 模型后关闭）；证据 = todo 16 全量 RTL 回归中 33 FM-SOC cases PASS within 8MB（transitional evidence 见 phase10 notepad 与 `firmware/npu_firmware.c` 当前代码）；(c) 签字栏留空（用户签）。Must NOT 修改 `rtl/ip/dram_model.v` 或 `firmware/npu_firmware.c`。
  Parallelization: Wave 1 | Blocked by: 1 | Blocks: 17
  References: `docs/bugs/bugs-soc-rtl.md:78-110`（BUG-002）；`firmware/npu_firmware.c:458,472-485`（`dram_range_ok`）；`.omo/notepads/phase10-rtl-verification/issues.md`（transitional evidence）
  Acceptance criteria: `test -f docs/waivers/WVR-SOC-RTL-002.md && grep -c 'BUG-RTL-SOC-002' docs/waivers/WVR-SOC-RTL-002.md` exit 0
  QA scenarios: happy — waiver 文件存在且包含 bug ID + 约束描述 + evidence 引用；failure — 文件缺失或无 evidence 引用。Evidence `build/evidence/task-2-soc-rtl-verification-signoff.txt`
  Commit: Y | docs(waiver): WVR-SOC-RTL-002 — 8MB DRAM window constraint

- [x] 3. 创建 cocotb PCIe TLP chain 测试：MPS-split + BAR 路由（host model 级）
  What to do / Must NOT do: 在 `sim/cocotb_bridge.py` 新增 `test_e2e_pcie_tlp_chain`：(a) 4KB TLP write to BAR0（SRAM base 0x2000_0000）→ readback bit-exact；(b) 4KB TLP write to BAR1（DRAM base 0x8000_0000）→ readback bit-exact；(c) MPS=256B split 验证：4KB 写产生 16 个 3-DW TLP headers，地址连续；(d) BAR 路由隔离：写 BAR0 不污染 BAR1 区域，反之亦然；(e) out-of-BAR：host model 返回 UR（不 assert RTL DUT 拒绝——`pcie_ep_wrapper.v` 注释说明 BAR enforcement 在 host model 侧）。Must NOT 期望 RTL 硬件拒绝 out-of-BAR；Must NOT 测 MSI-X。
  Parallelization: Wave 1 | Blocked by: — | Blocks: 16
  References: `sim/cocotb_bridge.py:804-1131`（PCIe TLP send/recv helpers）；`rtl/ip/pcie_ep_wrapper.v:13-20`（BAR mapping comments）；`sim/rtl_soc_runner.py:868-917`（`_pcie_tlp_write`/`_pcie_tlp_read`）；`sim/tests/test_pcie_tlp_chain.py:62-95`（MPS-split FM guard 参考）；`sim/regression/Makefile:283-297`（`run_qwen_e2e` cocotb 模式参考）
  Acceptance criteria: `make -C sim/regression run_e2e_pcie_tlp_chain` exit 0（需新增 Makefile target）；log 含 `test_e2e_pcie_tlp_chain.*PASS`
  QA scenarios: happy — 4KB MPS-split write→readback bit-exact + BAR 隔离；failure — BAR0 写污染 BAR1 或 MPS split 地址不连续 → FAIL。Evidence `build/evidence/task-3-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): add cocotb PCIe TLP chain MPS-split + BAR routing test

- [x] 4. 扩展 cocotb INTC 测试：THRESHOLD>1 popcount 门控 + ENABLE=0 负面
  What to do / Must NOT do: 扩展 `sim/cocotb_bridge.py` 中 `test_e2e_intc_irq`（:3813-3858）：(a) 新增 THRESHOLD=2 场景：PENDING=0x55, ENABLE=0x55 → popcount=4 ≥2 → cpu_irq assert；THRESHOLD=5 → popcount=4 <5 → cpu_irq deassert；(b) 新增 ENABLE=0 负面：PENDING=0x55, ENABLE=0x00 → cpu_irq 保持低；(c) 验证 ACK 清除后 PENDING 仍保留（匹配 `rtl/intc/intc_top.v:159`）。Must NOT 修改 `intc_top.v`；Must NOT 测 WFI 唤醒（已有 `test_e2e_intc_irq` 覆盖 THRESHOLD=1 路径）。
  Parallelization: Wave 1 | Blocked by: — | Blocks: 16
  References: `sim/cocotb_bridge.py:3813-3858`（`test_e2e_intc_irq`）；`rtl/intc/intc_top.v:159`（`cpu_irq = popcount(PENDING&ENABLE)>=THRESHOLD`）；`sim/tests/test_intc_gating.py:109-117`（FM THRESHOLD popcount guard 参考）；`sim/regmap.py`（INTC ENABLE/THRESHOLD/ACK 偏移）
  Acceptance criteria: `make -C sim/regression run_e2e_intc_irq` exit 0；log 含 `THRESHOLD=2.*PASS` 和 `ENABLE=0.*PASS`
  QA scenarios: happy — THRESHOLD=2/5 门控正确 + ENABLE=0 屏蔽；failure — THRESHOLD=5 仍 assert cpu_irq → FAIL。Evidence `build/evidence/task-4-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): extend INTC cocotb test with THRESHOLD>1 popcount gating

- [x] 5. 创建 RTL crossbar 公平性 testbench：strict round-robin 验证
  What to do / Must NOT do: 新建 `rtl/tb/axi_crossbar_fairness_tb.sv`：(a) RTL crossbar 实际有 7 个 AXI master（Ibex/MXU/SFU/Vector/DMA/PCIe/PCIe_DMA），测试任选 6 个（或全部 7 个）发等长 single-beat read（非 burst，避免 grant 保留）；(b) 通过 hierarchical reference 探测 `rtl/soc/axi_crossbar.v` 内部的 per-slave master-index grant 数组 `aw_granted`/`ar_granted`（:174/:178 附近）以及 per-master response valid `m_bvalid_o`/`m_rvalid_o`；(c) 断言在 ≥100 cycles 窗口内每个被测 master 的 grant 计数差 ≤1；(d) DECERR 场景：无效地址返回 SLVERR/ERROR 响应，但不消耗被测 master 的数据 phase credit。Must NOT 用 burst 事务（grant 保留到 B/RLAST 会破坏交替语义）；Must NOT 修改 `axi_crossbar.v`。
  Parallelization: Wave 1 | Blocked by: — | Blocks: 16
  References: `rtl/soc/axi_crossbar.v`（round-robin grant 逻辑，per-slave `aw_granted`/`ar_granted`）；`sim/regression/Makefile:192-205`（`run_crossbar_stress` 参考）；`sim/tests/test_crossbar_arbitration.py:69-237`（FM 公平性算法参考，注意其 `_aw_last_granted` 是 FuncModel Python 属性，RTL 无此信号名）；`rtl/soc/axi_crossbar_tb.sv`（现有 stress TB）
  Acceptance criteria: `make -C sim/regression run_crossbar_fairness` exit 0（需新增 Makefile target）；log 含 `FAIRNESS: PASS`（被测 master grant 计数差 ≤1）
  QA scenarios: happy — 6/7 master 严格交替 + DECERR 不消耗数据 phase；failure — 某 master 连续 2 次 grant → FAIL。Evidence `build/evidence/task-5-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): add AXI crossbar fairness testbench with strict round-robin assertion

### Wave 2 — RTL gap tests part 2 + SFU fix

- [x] 6. 创建 RTL APB 寄存器 conformance testbench：8 slave 中测 7 个外设
  What to do / Must NOT do: 新建 `rtl/tb/apb_register_conformance_tb.sv`：(a) RTL `apb_decoder.v` 实际有 8 个 slave（MXU/SFU/VECTOR/DMA/PCIe/DOORBELL/INTC/PCIE_DMA），本测试只覆盖前 7 个 engine/peripheral slave（slave index 0-6），显式跳过 PCIE_DMA（slave 7）；(b) 从 `sim/regmap.py` 生成期望寄存器表（offset/access/reset/w1c）；(c) 对 7 个被测 APB slave 逐偏移 write→readback；(d) rw 验证 overwrite 语义（0x3→0x6 不 OR-accumulate）；(e) r（read-only）验证 hostile write 不变；(f) w1c 验证 ACK 0x00F0 → 0xFF0F；(g) w（write-only）验证 stored value 匹配实现。Must NOT 修改 `regmap.py` 或 `apb_decoder.v`；Must NOT 包含 PCIE_DMA slave。
  Parallelization: Wave 2 | Blocked by: — | Blocks: 16
  References: `sim/regmap.py`（寄存器定义）；`rtl/soc/apb_decoder.v:8-16`（8-slave decode）；`sim/tests/test_apb_register_conformance.py:137-241`（FM conformance guard 参考）；`sim/regression/Makefile:129-142`（`run_apb_smoke` 参考）
  Acceptance criteria: `make -C sim/regression run_apb_conformance` exit 0（需新增 Makefile target）；log 含 `APB_CONFORMANCE: PASS`（7/7 tested peripheral）
  QA scenarios: happy — 7 个 slave 全寄存器 readback 语义匹配；failure — STATUS 写入后值改变 → FAIL。Evidence `build/evidence/task-6-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): add APB register conformance testbench for 7 peripherals

- [x] 7. 创建 RTL Ibex 共享地址空间 coherence 测试
  What to do / Must NOT do: 在 `sim/cocotb_bridge.py` 新增 `test_e2e_ibex_shared_address_space`：(a) Ibex 经 crossbar 写 pattern 到 SRAM 0x2000_1000；(b) MXU engine 经 crossbar 读同一地址 → 数据匹配；(c) 反向：MXU 写 → Ibex 读匹配；(d) DMEM 隔离：写 DMEM 0x0001_0100 不影响 SRAM；(e) boot ROM 隔离：写 SRAM/DRAM 不改变 boot ROM 内容（backdoor snapshot 对比）。Must NOT 修改 `caduceus_soc_top.v` 或 `ibex_wrapper.v`。
  Parallelization: Wave 2 | Blocked by: — | Blocks: 16
  References: `sim/cocotb_bridge.py`（backdoor SRAM/DRAM access :632-802；APB/doorbell :2212-2407）；`sim/rtl_soc_runner.py:3309-3435`（IbexRunner case dispatch）；`sim/tests/test_ibex_shared_address_space.py:30-181`（FM guard 参考）；`rtl/soc/caduceus_soc_top.v`（地址映射）
  Acceptance criteria: `make -C sim/regression run_e2e_ibex_shared_addr` exit 0（需新增 Makefile target）；log 含 `SHARED_ADDR: PASS`
  QA scenarios: happy — Ibex↔MXU SRAM 双向一致 + DMEM/boot ROM 隔离；failure — DMEM 写污染 SRAM → FAIL。Evidence `build/evidence/task-7-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): add Ibex shared address space coherence cocotb test

- [x] 8. 创建 RTL IRQ 驱动 dispatch stall 测试：ENABLE=0 → NPU_HEAD 停滞
  What to do / Must NOT do: 在 `sim/cocotb_bridge.py` 新增 `test_e2e_irq_dispatch_stall`：(a) firmware `npu_wait_done()` 是 polling（spin on STATUS），但主循环 idle 时使用 WFI (`firmware/npu_firmware.c:664`)。测试分两种配置：THRESHOLD=1 且 INTC.ENABLE=0 时，提交 3 条命令 → cpu_irq 保持低（mask 验证）；如果能在 WFI 路径上构造 stall，则额外断言 NPU_HEAD 在 ≥10000 cycles 内不前进；(b) 无论 firmware 是 WFI 还是 polling，都必须验证 INTC.ENABLE=0 能屏蔽 cpu_irq；(c) 若 polling 导致无法 stall，测试 rename 为 `test_e2e_irq_mask_no_cpu_irq`， acceptance 相应调整。Must NOT 修改 firmware。
  Parallelization: Wave 2 | Blocked by: — | Blocks: 16
  References: `firmware/npu_firmware.c:664`（WFI）与多处 `npu_wait_done()` 调用；`sim/cocotb_bridge.py:3813-3858`（`test_e2e_intc_irq`）；`sim/tests/test_irq_driven_dispatch.py:377-420`（FM stall guard 参考）；`rtl/intc/intc_top.v:159`（cpu_irq gating）
  Acceptance criteria: `make -C sim/regression run_e2e_irq_stall` exit 0（需新增 Makefile target）；log 含 `IRQ_MASK: PASS`；若 WFI stall 可构造，额外含 `IRQ_STALL: PASS`
  QA scenarios: happy — ENABLE=0 后 cpu_irq 低（+ NPU_HEAD 停滞 if WFI path）；failure — ENABLE=0 仍 assert cpu_irq → FAIL。Evidence `build/evidence/task-8-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): add IRQ-driven dispatch stall cocotb test

- [x] 9. 扩展 rtl_soc_runner.py boot 断言：PC/sp 初始化 + boot ROM 隔离
  What to do / Must NOT do: 扩展 `sim/rtl_soc_runner.py` 中 `IbexRunner._run_spike`（:3350-3381）的 boot 验证：(a) 复位后 Ibex PC=0x0000_0000（通过现有 `pc_id`/`exc_id` probe 信号 :3361-3363）；(b) firmware 进入 main 后 sp 在 DMEM 范围内（非 provisional 0x20000）；(c) boot ROM 内容在 firmware 执行后不变（backdoor snapshot）。Must NOT 修改 `ibex_wrapper.v` 或 `boot_rom.v`；Must NOT 硬编码 `_stack_top` 值。
  Parallelization: Wave 2 | Blocked by: — | Blocks: 16
  References: `sim/rtl_soc_runner.py:3309-3435`（IbexRunner），`:3350-3381`（`_run_spike` + PC/exc probe），`:3361-3363`（probe 信号）；`sim/tests/test_firmware_boot_sequence.py:97-274`（FM boot guard 参考）；`rtl/cpu/ibex_wrapper.v`；`rtl/soc/boot_rom.v`
  Acceptance criteria: `make -C sim/regression run_fm_soc_case CASE_ID=FM-SOC-009` exit 0；log 含 `BOOT_ASSERT: PC=0 PASS` 和 `SP_INIT: PASS`
  QA scenarios: happy — PC=0 + sp in DMEM + boot ROM 不变；failure — PC≠0 或 boot ROM 被改写 → FAIL。Evidence `build/evidence/task-9-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): extend Ibex runner with boot sequence assertions

- [x] 10. 创建 RTL ring-wrap stress case：>1024 SFU/VADD 命令
  What to do / Must NOT do: 在 `sim/rtl_soc_runner.py` 新增 case builder `_build_ring_wrap_stress`：(a) 发 1100 条 SFU RMSNorm + Vector VADD 交替命令（超过 1024-entry ring，强制 wrap）；(b) 断言 NPU_HEAD 单调递增到 1100；(c) 全部命令在 timeout_cycles 内完成；(d) COMPLETION_STATUS 数组只有 16 entry（`gen/npu_abi.h:142` `COMPLETION_STATUS[16]`）——若 wrap 后索引 >15 覆盖旧状态，记录为 known behavior（不阻断 PASS，但 evidence 中标注）。Must NOT 修改 `command_ring.py` 或 ABI header；Must NOT 期望 COMPLETION_STATUS 数组 >16。
  Parallelization: Wave 2 | Blocked by: — | Blocks: 16
  References: `sim/command_ring.py:14`（`RING_ENTRIES = 1024`）；`gen/npu_abi.h:142`（`COMPLETION_STATUS[16]`），`:299`（`NPU_RING_ENTRIES 1024`）；`sim/rtl_soc_runner.py:1092-1346`（P0SpikeRunner builders）；`sim/tests/test_spike_ibex_ring_alignment.py:194-288`（FM ring alignment guard 参考）
  Acceptance criteria: `make -C sim/regression run_fm_soc_case CASE_ID=RING-WRAP-STRESS` exit 0；log 含 `NPU_HEAD=1100 PASS`
  QA scenarios: happy — 1100 命令全部完成 + head 递增到 1100；failure — head 卡在 <1100 或超时 → FAIL。Evidence `build/evidence/task-10-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): add ring-wrap stress case for >1024 command dispatch

- [x] 11. 修复 FM-SOC-10X op00 RMSNorm 失败：诊断 + 修复（timebox 1 工作日）
  What to do / Must NOT do: (a) 先跑 `make -C sim/regression run_fm_soc_case CASE_ID=FM-SOC-10X` 在 sz0001 上，捕获 op00 RMSNorm 失败 log；(b) 假设驱动诊断：探查 SFU wrapper RMSNorm datapath（`rtl/wrapper/sfu_soc_wrapper.v` + `rtl/sfu/rmsnorm_hw.v`），对比 FM-SOC-004（单 RMSNorm PASS）与 FM-SOC-10X（chain 中 RMSNorm FAIL）差异——可能原因：wrapper 内部状态残留 / SRAM scratch 污染 / preload 时机；(c) 按诊断结论修复（RTL 或 firmware，走 feature branch）；(d) 验证 FM-SOC-10X PASS + SFU 模块级 319/319 + FM-SOC-004/027 回归。**Timebox：1 个工作日内定位根因；若未定位，转 waiver + 标记 FM-SOC-10X 为 partial**。Must NOT 在根因不明时同时改 RTL+firmware；Must NOT 跳出 SFU wrapper 范围修其他模块。
  Parallelization: Wave 2 | Blocked by: — | Blocks: 14,15,16
  References: `sim/rtl_soc_runner.py:3161-3252`（`_run_10X`）；`rtl/wrapper/sfu_soc_wrapper.v`；`rtl/sfu/rmsnorm_hw.v`；`.omo/notepads/phase10-rtl-verification/issues.md:182`（FM-SOC-10X op00 RMSNorm FAIL 记录）；`sim/regression/Makefile:860-893`（`run_fm_soc_case`）
  Acceptance criteria: `make -C sim/regression run_fm_soc_case CASE_ID=FM-SOC-10X` exit 0；log 含 `FM-SOC-10X.*PASS`；SFU batch 319/319 PASS
  QA scenarios: happy — op00 RMSNorm 输出匹配 golden + FM-SOC-10X 全链 PASS；failure — op00 仍 mismatch 或 SFU 回归退化 → FAIL。Evidence `build/evidence/task-11-soc-rtl-verification-signoff.txt`
  Commit: Y | fix(rtl/sfu): FM-SOC-10X op00 RMSNorm failure — <root cause>

### Wave 3 — E2E RTL 扩展

- [x] 12. 为 FM-SOC-032 添加 corrupted-descriptor 变体
  What to do / Must NOT do: 在 `sim/rtl_soc_runner.py` 扩展 `_run_032`（:3068-3096）：(a) 新增 `_run_032_corrupted_desc` 变体：在第 5 个 block 的第 14 个 op（VMUL gate*up），将描述符地址偏移 +64B（模拟 FM guard `test_soc_fm_long_sequence.py:472-484`）；(b) 断言该 block 输出与 golden 不匹配，其余 block 仍 bit-exact；(c) 定义预期行为：RTL 不 hang，返回错误状态或确定性输出失配（不期望 firmware abort）。Must NOT 修改 `_run_032` 原有 baseline 逻辑。
  Parallelization: Wave 3 | Blocked by: — | Blocks: 16
  References: `sim/rtl_soc_runner.py:3068-3096`（`_run_032`）；`sim/tests/test_soc_fm_long_sequence.py:472-484`（FM corruption guard 参考）；`sim/gen_soc_rtl_vectors.py:1902`（FM-SOC-032 generator）
  Acceptance criteria: `make -C sim/regression run_fm_soc_case CASE_ID=FM-SOC-032-CORRUPT` exit 0；log 含 `CORRUPT: block5 mismatch, others PASS`
  QA scenarios: happy — block 5 输出失配 + 其余 bit-exact + 无 hang；failure — 全链输出一致（corruption 未生效）或 hang → FAIL。Evidence `build/evidence/task-12-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): add corrupted-descriptor variant to FM-SOC-032

- [x] 13. 创建 RTL MobileNetV3 CV chain cocotb case（scope: 现有 golden via MXU wrapper）
  What to do / Must NOT do: 在 `sim/cocotb_bridge.py` 新增 `test_e2e_mobilenetv3_chain`：(a) 使用 `sim/cv/cv_trace.py` 或 `scripts/gen_cv_golden.py` 生成 MobileNetV3 层列表（如果 golden 已存在则直接用）；(b) 每层 chunked M≤64/N≤128（绕开 `tile_mmul` M>1/N>128 clobber，见 `sim/tests/test_mobilenetv3_fm_chain.py:374-386`）；(c) 通过 doorbell ring 发 MMUL 命令到 RTL SoC；(d) 逐层 cos_sim ≥ 0.99（golden = `GoldenMXU.matmul_int4_per_block`）；RTL 不追求 bit-exact，只要求 cos≥0.99；(e) DRAM 预载预算 < 8MB。**若需要新增 CV 算子（im2col engine/pool），必须先获得用户批准——本计划范围限定为"现有 golden 通过 MXU wrapper"**。Must NOT 修改 `sim/cv/` 模块代码；Must NOT 修改 RTL 引擎。
  Parallelization: Wave 3 | Blocked by: — | Blocks: 16
  References: `sim/cv/cv_trace.py`（`generate_mobilenetv3_trace`）；`scripts/gen_cv_golden.py`（CV golden 生成）；`sim/tests/test_mobilenetv3_fm_chain.py:558-645`（FM guard 参考）；`sim/cocotb_bridge.py:1295-1678`（`_run_tiled_mmul`/`_run_streamed_mmul`）
  Acceptance criteria: `make -C sim/regression run_e2e_mobilenetv3` exit 0（需新增 Makefile target）；log 含 `MOBILENETV3: PASS`（per-layer cos≥0.99 for ≥50/52 convs）
  QA scenarios: happy — MobileNetV3 chain 通过 RTL + per-layer cos≥0.99；failure — 某 layer cos<0.99 或 DRAM 越窗 → FAIL。Evidence `build/evidence/task-13-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): add MobileNetV3 CV chain RTL cocotb case

- [x] 14. 扩展 Ibex 36 层 checkpoint：加 L5/L15/L25 → 8 个 checkpoint（~16h VCS）
  What to do / Must NOT do: 扩展 `sim/rtl_soc_segment_run.py` 的 SEGMENTS 列表（:112-118）：新增 (4,5)、(14,15)、(24,25) 三个段，使 checkpoint 覆盖 L0/L5/L10/L15/L20/L25/L30/L35（8 个点）；(a) 每段在同一 VCS 会话内连续执行，层间状态留 DRAM；(b) 每段边界 `segment_preload(force_full=True, clear_sram=True, sram=b"\x00"*SRAM_SIZE)`（ISSUE-13C fix :191-247）；(c) per-checkpoint cos_sim ≥ ladder（L0-19 ≥0.999, L20-29 ≥0.998, L30-35 ≥0.997）；(d) **Timebox：总 VCS wall-time cap 24h；若超时，已完成 checkpoint 的 evidence 保留，未完成的标 PENDING**。Must NOT 启动全量 36 层连续仿真（全量 deferred 到 FPGA）；Must NOT 修改容差阶梯。
  Parallelization: Wave 3 | Blocked by: 11 | Blocks: 16
  References: `sim/rtl_soc_segment_run.py:112-118`（SEGMENTS），`:119`（CHECKPOINTS），`:376`（ladder thresholds）；`.omo/notepads/phase10-rtl-verification/issues.md:191-247`（ISSUE-13C SRAM clear fix）；`scripts/p10_lib/p10_sz0001.sh`（sz0001 SSH 封装）；`sim/regression/run_ibex_segment_run.sh`
  Acceptance criteria: `bash sim/regression/run_ibex_segment_run.sh` 生成 evidence 含 `checkpoints_passed=8/8`（或已完成的子集 + PENDING 标注）
  QA scenarios: happy — L5/L15/L25 cos≥ladder + 原有 5 个仍 PASS；failure — 新 checkpoint cos<ladder 或段边界状态泄漏 → FAIL。Evidence `build/evidence/task-14-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): expand Ibex 36-layer checkpoints to 8 (L5/L15/L25)

- [x] 15. 创建 RTL attn_weight full-chain dispatch 测试（17-op block，非单 shape）
  What to do / Must NOT do: 在 `sim/rtl_soc_runner.py` 新增 case builder `_build_attn_weight_chain`：(a) 跑完整 17-op blk.0 chain（含 op07 attn_weight M=32/K=32/N=64），非单 op shape；(b) 断言全部 17 op cycles>0（特别是 attn_weight op cycles>0——这是 BUG-RTL-SOC-007 的 chain-level 复现）；(c) 输出 vs golden cos_sim ≥ 0.999（或 bit-exact for INT32 path）；(d) 若 cycles=0 复现，记录为 BUG-RTL-SOC-007 未修，保留 Open；若 cycles>0，关联 todo 1 台账证据。Must NOT 只跑单 op shape（shape case 无法复现 chain dispatch bug）。
  Parallelization: Wave 3 | Blocked by: 11 | Blocks: 16
  References: `sim/rtl_soc_runner.py:3068-3096`（`_run_032` 28-block chain 参考）；`sim/tests/test_soc_fm.py:1849-1918`（FM attn_weight shape guard 参考）；`docs/bugs/bugs-soc-rtl.md:324-359`（BUG-RTL-SOC-007）
  Acceptance criteria: `make -C sim/regression run_fm_soc_case CASE_ID=ATTN-WEIGHT-CHAIN` exit 0；log 含 `attn_weight cycles>0 PASS` + `cos_sim>=0.999`
  QA scenarios: happy — 17 op 全部 cycles>0 + 输出匹配 golden；failure — attn_weight cycles=0 → FAIL（BUG-RTL-SOC-007 未修，保留 Open）。Evidence `build/evidence/task-15-soc-rtl-verification-signoff.txt`
  Commit: Y | test(soc): add attn_weight full-chain dispatch RTL test

### Wave 4 — 全量回归 + 文档同步

- [x] 16. 在 sz0001 上跑全量 RTL 回归：33 FM-SOC + 新测试 + 扩展 checkpoint
  What to do / Must NOT do: (a) `bash sim/regression/run_ibex_full_rtl.sh`（33 cases FM-SOC-001..032+10X，含修复后的 FM-SOC-10X）；(b) 逐个跑新 Makefile target（todo 3-10,12-15 新增的 test/case）；(c) 跑扩展 checkpoint（todo 14）；(d) 汇总 PASS/FAIL/SKIP 计数，任何 FAIL 生成 triage note（spawn fix todo 或 waiver）。Must NOT 在非 sz0001 上跑 VCS；Must NOT 跳过未通过的 case。
  Parallelization: Wave 4 | Blocked by: 1-15 | Blocks: 17
  References: `sim/regression/run_ibex_full_rtl.sh`（33-case regression）；`sim/regression/Makefile:860-893`（`run_fm_soc_case`）；`sim/regression/run_ibex_segment_run.sh`（checkpoint run）；`scripts/p10_lib/p10_sz0001.sh`（SSH 封装）
  Acceptance criteria: 全量回归 summary 含 `FM-SOC: 33/33 PASS`（或 32/33 + 1 waived）+ 新测试全 PASS + checkpoint 8/8（或已完成子集 + PENDING）；evidence 文件包含 per-case PASS/FAIL
  QA scenarios: happy — 全量 PASS；failure — 任一 case FAIL → triage note + 阻塞 signoff。Evidence `build/evidence/task-16-soc-rtl-verification-signoff.txt`
  Commit: Y | test(regression): full SoC RTL regression with new tests + expanded checkpoints

- [x] 17. 更新 vplan 汇总表 + signoff checklist
  What to do / Must NOT do: (a) 更新 `.omo/plans/soc-rtl-verification-vplan.md` 第 1 节汇总表：SoC 互联 67%→计算新覆盖率（6 个 gap 行已有 RTL 测试后改 ✅ RTL）；固件/CPU 70%→新覆盖率；E2E 38%→新覆盖率；合计 79%→新数字；(b) 更新第 8 节差距总结：划掉已闭环项；(c) 更新 `docs/func-model-signoff-checklist.md` SoC Data-Path Hardening 段：F-FM-SOC-01..13 行追加 RTL evidence 引用（`build/evidence/task-{N}-soc-rtl-verification-signoff.txt`）；(d) 更新 Scope Limitations 中"Multi-layer / full-model signoff is NOT claimed"——如果 8 checkpoint 通过，改为"8-checkpoint subset signoff"。Must NOT 改性能 calibration 状态（仍 ❌）；Must NOT claim BUG-RTL-SOC-007 Fixed（除非 todo 15 chain-level PASS）。
  Parallelization: Wave 4 | Blocked by: 16 | Blocks: —
  References: `.omo/plans/soc-rtl-verification-vplan.md:11-20`（汇总表），`:86-96`（3.2 gap rows），`:113-119`（4.2 gap rows），`:133-141`（5.2 gap rows），`:171-179`（差距总结）；`docs/func-model-signoff-checklist.md:205-234`（SoC Data-Path Hardening 段），`:259-272`（Scope Limitations）
  Acceptance criteria: `grep -c '✅ RTL' .omo/plans/soc-rtl-verification-vplan.md` ≥6（6 个 SoC interconnect gap 行有 RTL 级 ✅）；`grep -c 'task-' docs/func-model-signoff-checklist.md` 在 SoC 段 ≥13（13 个 F-FM-SOC 行有 evidence 引用）
  QA scenarios: happy — 汇总表 + gap 行 + signoff checklist 同步；failure — 汇总表仍显示 79% 或 gap 行仍标 FM 守卫。Evidence `build/evidence/task-17-soc-rtl-verification-signoff.txt`
  Commit: Y | docs(verify): update vplan + signoff checklist with RTL regression evidence

## Final verification wave
> Runs in parallel after ALL todos. ALL must APPROVE. Surface results and wait for the user's explicit okay before declaring complete.
- [x] F1. Plan compliance audit
  What: 逐条核对 17 个 todo 的 evidence 文件存在、acceptance 命令可复跑通过。
  Command: `TODO_MAX=17 FM_PLAN_NAME=soc-rtl-verification-signoff bash scripts/fm_hardening_f1_audit.sh`
  Pass: 全部 evidence 存在、17/17 acceptance 复跑通过（或 SKIP-ENV 标注）。
- [x] F2. Code quality review
  What: 新增 RTL testbench/cocotb 测试无 TODO/FIXME/HACK 残留；`bash -n` 通过；已有 FM-SOC 回归无新增 FAIL。
  Command: `bash scripts/fm_hardening_f2_code_quality.sh`
  Pass: 0 残留、0 新增失败、所有 shell 语法通过。
- [x] F3. Real manual QA
  What: 全量 RTL 回归在 sz0001 上跑通 + 扩展 checkpoint cos≥ladder + MobileNetV3 RTL chain spot check + attn_weight chain cycles>0。
  Command: `bash scripts/fm_hardening_f3_manual_qa.sh --dry-run`（本地 dry-run）+ sz0001 上实跑（todo 16 已覆盖）。
  Pass: 五项全过（FM-SOC-10X 已修；Spike smoke 已知问题标 known-issue）。
- [x] F4. Scope fidelity
  What: `git diff` 只含 sim/tests/ 新增、sim/cocotb_bridge.py 扩展、sim/rtl_soc_runner.py 扩展、sim/rtl_soc_segment_run.py 扩展、rtl/tb/ 新增、docs/、.omo/、scripts/；`rtl/` 产品代码零改动（除非 todo 11 SFU fix）；冻结文件零改动。
  Command: `bash scripts/fm_hardening_f4_scope_gate.sh`
  Pass: 无越界文件、冻结文件零改动（RTL product change 仅限 SFU wrapper fix 且有诊断证据）。

## Commit strategy
- 每个 todo 一个原子 commit（类型按 todo 的 Commit 行）
- evidence 文件随 todo 一并提交到 `build/evidence/`（gitignored，用 `git add -f`）
- RTL/firmware 修改走 feature branch（`soc-rtl-vsignoff/<component>`），先模块级回归再合并 main
- bug 台账更新（todo 1）和 waiver（todo 2）各一个 commit
- Wave 4 结束后总结合并提交：`docs(soc): closure note for soc-rtl-verification-signoff`

## Success criteria
- 9 个"部分覆盖"FM 守卫对应的 RTL 回归测试就位并 PASS（PCIe TLP / INTC THRESHOLD / crossbar fairness / APB conformance / Ibex shared addr / IRQ stall / boot assertions / ring-wrap / attn_weight chain）
- 2 个"无覆盖"FM 守卫对应的 RTL 首跑完成（MobileNetV3 chain / 36-layer checkpoint 扩展）
- FM-SOC-10X op00 RMSNorm 修复后 PASS（或 timebox 内转 waiver）
- BUG-RTL-SOC-002 正式 waiver 文件就位
- bug 台账 5 个过期条目更新（4 Fixed + 1 Waived + 1 Open with new evidence）
- vplan 汇总表从 79% 更新到新覆盖率（SoC 互联 + 固件/CPU + E2E gap 行改 ✅ RTL）
- signoff checklist SoC 段追加 RTL evidence 引用
- 全量 RTL 回归在 sz0001 上 33 FM-SOC + 新测试 + 扩展 checkpoint 无新增 FAIL
- F1-F4 全 APPROVE
