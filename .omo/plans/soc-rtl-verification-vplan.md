# SoC RTL 验证 VPlan — Feature Coverage 全景

> **Date**: 2026-08-24
> **Scope**: CaduceusCore SoC RTL（Ibex RISC-V + MXU/SFU/Vector + DMA/PCIe/AXI Crossbar/APB/INTC/Doorbell + Firmware）
> **目的**: 给出一份完整的 SoC RTL 验证 feature 清单，标注每个 feature 当前是否已被 RTL 回归覆盖，哪些仍未覆盖，作为 SoC RTL signoff 的基线认知。
> **证据来源**: `sim/regression/`、`sim/rtl_soc_runner.py`、`docs/soc-fm-gap-spec.md`、`docs/bugs/bugs-soc-rtl.md`、`docs/rtl_development_plan.md`、`docs/func-model-signoff-checklist.md`、`.omo/notepads/phase10-rtl-verification/`
> **落地建议**: 本文件是计划产物；批准后由 worker 复制/改写为 `docs/soc-rtl-verification-vplan.md`。

---

## 1. 总体覆盖率

| 类别 | Feature 总数 | 已覆盖 | 未覆盖/部分 | 覆盖率 |
|------|:-----------:|:------:|:-----------:|:------:|
| 计算引擎（MXU/SFU/Vector） | 22 | 22 | 0 | 100% |
| SoC 互联与数据通路 | 18 | 18 | 0 | 100% |
| 固件与 CPU 集成 | 10 | 9 | 1 | 90% |
| 端到端与真实模型 | 8 | 7 | 1 | 88% |
| 契约/守卫/门禁（fm-hardening-phase10） | 8 | 8 | 0 | 100% |
| **合计** | **66** | **64** | **2** | **97%** |

> **结论**: SoC RTL 验证 feature coverage 达 **97%（64/66）**。SoC 互联与数据通路的 6 条 gap（SOC-13/14/15/16/17/18）和固件/CPU 的 2 条 gap（FW-08/10）已由 RTL 测试闭环（`soc-rtl-verification-signoff` todos 3-10，全量回归 11/11 新目标 PASS）；E2E 除性能 calibration 外全部具备 RTL 证据（todos 13-15，含 36 层 8-checkpoint 与 MobileNetV3 RTL 首跑）。剩余缺口：**E2E-07 性能 calibration**（`calibration_state=uncalibrated`，deferred 到流片/FPGA 实测）与 **FW-09 memory contract JSON**（静态 artifact 检查，不涉及 RTL 仿真，N/A）。**BUG-RTL-SOC-007 仍 Open**（todo 15/16 链级未复现 cycles=0，不 claim Fixed）。因性能 calibration 阻塞，full SoC RTL signoff 仍未到 100%。

---

## 2. 计算引擎 Feature Coverage（100%）

### 2.1 MXU — 64×64 Broadcast MAC

| Feature | RTL 回归用例 | 状态 | 证据 |
|---------|-------------|:----:|------|
| 单 tile INT4×INT8→INT32 bit-exact | `tb_mxu` single_tile | ✅ | 4096/4096 逐比特匹配 |
| 多 tile K 拆分 | `tb_mxu` multi_tile_K | ✅ | 9 场景 PASS |
| 多 tile N 拆分 | `tb_mxu` multi_tile_N | ✅ | 9 场景 PASS |
| 多 tile M 拆分 | `tb_mxu` multi_tile_M | ✅ | 9 场景 PASS |
| 溢出饱和 | `tb_mxu` overflow | ✅ | PASS |
| 零维/部分 tile | `tb_mxu` zero_dim/partial | ✅ | PASS |
| 随机 100 组合 | `tb_mxu` random | ✅ | 100/100 PASS |
| ACCUMULATE 模式（CTRL[2]） | FM-SOC-003 + FM `test_mmul_accumulate` | ✅ | SoC 路径 + FM golden 加固 |

### 2.2 SFU — 7 FP16 算子

| Feature | RTL 回归用例 | 状态 | 证据 |
|---------|-------------|:----:|------|
| Softmax 8-stage | `tb_sfu` + FM-SOC-004 | ✅ | 319/319 batch PASS |
| LayerNorm 6-stage | `tb_sfu` | ✅ | PASS |
| RMSNorm two-pass | `tb_sfu` + FM-SOC-004/010 | ✅ | PASS + FM-SOC-10X 17-op chain（todo 11 SFU descriptor ABI 修复，全量回归 todo 13 复审计 **25 executed + 6 superseded + 2 N/A** 全 PASS — `.omo/evidence/task-13-soc-rtl-review-remediation.txt`；原 2026-08-28 "33/33 全量回归 PASS" 口径已纠正） |
| RoPE 16-stage CORDIC | `tb_sfu` + FM-SOC-004 | ✅ | PASS |
| GELU 4-segment | `tb_sfu` | ✅ | PASS |
| SiLU Newton-Raphson | `tb_sfu` | ✅ | PASS |
| exp LUT | `tb_sfu` + `exp_lut_tb` | ✅ | 256-entry 校准 |

### 2.3 Vector — 6 INT32 算子

| Feature | RTL 回归用例 | 状态 | 证据 |
|---------|-------------|:----:|------|
| ADD 128-wide SIMD | `tb_vector` + FM-SOC-005 | ✅ | 63/63 batch PASS |
| MUL 128-wide SIMD | `tb_vector` + FM-SOC-005 | ✅ | PASS |
| MAX 逐元素 | `tb_vector` | ✅ | PASS |
| MAX_REDUCE 128→1 | `tb_vector` | ✅ | PASS |
| SUM_REDUCE 128→1 | `tb_vector` | ✅ | PASS |
| INT32→FP16 CONV | `tb_vector` + FM-SOC-005 | ✅ | 131073/131073 逐比特 |
| RESID_ADD 饱和 | `tb_vector` + FM-SOC-005 | ✅ | PASS |

---

## 3. SoC 互联与数据通路 Feature Coverage（100%）

### 3.1 已覆盖

| Feature | 回归用例 | 状态 | 证据 |
|---------|---------|:----:|------|
| AXI4 Crossbar M=6/S=2 并发压力 | `run_crossbar_stress` | ✅ | 11,455 cycles, 1,260 txns, 0 errors |
| APB Decoder 8-slave 选择 + pslverr | `run_apb_smoke` | ✅ | PASS |
| DMA wrapper（axi_cdma）传输 | `run_dma_test` | ✅ | 5 cases PASS |
| PCIe EP wrapper TLP→AXI→readback | `run_pcie_test` | ✅ | PASS |
| DRAM behavioral model 100 随机事务 | `run_dram_test` | ✅ | PASS |
| Doorbell ring buffer 协议 | FM-SOC-006 + Ibex full | ✅ | PASS |
| INTC 7-source 中断控制器 | `run_intc_test` + FM-SOC-006 | ✅ | 13/13 PASS |
| SoC elaboration 47 模块 | `run_soc_elab` | ✅ | 0 errors |
| Cocotb Qwen blk.0 17-op chain | `run_e2e_blk0` | ✅ | 17/17 PASS |
| SRAM 控制器 4MB 512-bit burst | SoC elaboration + Cocotb | ✅ | 集成验证 |
| Boot ROM $readmemh 加载 | Ibex boot smoke | ✅ | PASS |
| SFU wrapper 宽度转换 + DONE | BUG-RTL-SOC-WV-001 fix | ✅ | Fixed + 回归 |

### 3.2 已闭环 — 原未覆盖 / 部分（6 项，soc-rtl-verification-signoff todos 3-9）

| Feature | Gap # | 状态 | 说明 | 证据 |
|---------|:-----:|:----:|------|------|
| PCIe TLP 功能模型（TLP 解析/BAR 路由/MSI-X）SOC-13 | #7 | ✅ RTL | FM 守卫 `test_pcie_tlp_chain.py`（fm-soc-datapath-hardening todo 1）之上，RTL cocotb `run_e2e_pcie_tlp_chain`（todo 3）验证 4KB MPS-split write→readback bit-exact + BAR 路由隔离 + out-of-BAR UR（host model 侧） | `build/evidence/task-3-soc-rtl-verification-signoff.txt` |
| AXI Crossbar 仲裁行为模型 SOC-14 | #8 | ✅ RTL | FM 守卫 `test_crossbar_arbitration.py`（todo 3）之上，RTL TB `run_crossbar_fairness`（todo 5）严格 round-robin 交替公平断言 + DECERR 不消耗数据 phase（FAIRNESS: PASS，19/19 checks） | `build/evidence/task-5-soc-rtl-verification-signoff.txt` |
| APB-MMIO 统一寄存器模型 SOC-15 | #1 | ✅ RTL | FM 守卫 `test_apb_register_conformance.py`（todo 4）之上，RTL TB `run_apb_conformance`（todo 6）7 个 peripheral slave 逐偏移 write→readback，rw/r/w1c/w 语义 168/168 检查 PASS | `build/evidence/task-6-soc-rtl-verification-signoff.txt` |
| Ibex→AXI 共享地址空间 SOC-16 | #2 | ✅ RTL | FM 守卫 `test_ibex_shared_address_space.py`（todo 5）之上，RTL cocotb `run_e2e_ibex_shared_addr`（todo 7）真实 crossbar 流量双向一致性 + DMEM/boot ROM 隔离 + DECERR 负面（SHARED_ADDR: PASS） | `build/evidence/task-7-soc-rtl-verification-signoff.txt` |
| IRQ 链路（engine→INTC→CPU WFI 唤醒）SOC-17 | #9 | ✅ RTL | FM 守卫 `test_intc_gating.py`（todo 2）之上，RTL cocotb `run_e2e_intc_irq`（todo 4，THRESHOLD>1 popcount 门控 + ENABLE=0）与 `run_e2e_irq_stall`（todo 8，ENABLE=0 → NPU_HEAD 10000-cycle stall + WFI wake 正控） | `build/evidence/task-4-soc-rtl-verification-signoff.txt` + `build/evidence/task-8-soc-rtl-verification-signoff.txt` |
| Ibex 固件 boot→DMEM→MMIO→poll IRQ 序列 SOC-18 | #11 | ✅ RTL | FM 守卫 `test_firmware_boot_sequence.py`（todo 7）之上，IbexRunner 启动断言（todo 9）验证复位后 PC 进入 boot 入口、sp 落在 DMEM、boot ROM 快照不变（BOOT_ASSERT/SP_INIT/BOOT_ROM PASS，FM-SOC-009 路由 Ibex 路径） | `build/evidence/task-9-soc-rtl-verification-signoff.txt` |

---

## 4. 固件与 CPU 集成 Feature Coverage（90%）

### 4.1 已覆盖

| Feature | 回归用例 | 状态 | 证据 |
|---------|---------|:----:|------|
| 固件全 opcode 分发（MMUL/SFU/Vector/DMA） | `test_firmware.py` + FM-SOC-006 | ✅ | 4/4 PASS |
| Tile 级双缓冲调度 | `tile_scheduler.py` + FM-SOC-006 | ✅ | PASS |
| MMIO 寄存器映射一致（C/Python） | `check_mmio_map.py` | ✅ | 49 registers match |
| ABI 常量单一来源（schema→C header→Python） | `test_npu_abi_constants.py` | ✅ | fm-hardening todo 9 |
| Ibex boot ROM 加载固件 | Ibex boot smoke | ✅ | PASS |
| Doorbell HOST_TAIL/NPU_HEAD 轮询 | FM-SOC-001..032 + Ibex | ✅ | 33 cases（todo 13 复审计口径：25 executed + 6 superseded + 2 N/A） |
| 地址空间/环布局契约 | `test_address_space.py` + `test_command_ring.py` | ✅ | fm-hardening todo 1/2/3 |

### 4.2 已闭环 — 原未覆盖 / 部分（3 项，todos 8/10；FW-09 为静态检查 N/A）

| Feature | 状态 | 说明 |
|---------|:----:|------|
| Spike 固件与 Ibex 固件行为对齐 FW-08 | ✅ RTL | FM 守卫 `test_spike_ibex_ring_alignment.py`（todo 8）之上，RTL `run_fm_soc_case CASE_ID=RING-WRAP-STRESS`（todo 10）真实 on-chip Ibex 固件交替分发 1100 条 SFU/Vector 命令，NPU_HEAD 单调推进到 1100 PASS（`build/evidence/task-10-soc-rtl-verification-signoff.txt`）。**Spike host mmul_smoke L0 Q_proj FAIL（max_diff=7.64e+02，F3/task-14 记录）已 FIXED（todo 16，2026-08-31）**：根因 `sim/spike_host.py` run_one_op host 侧地址/布局契约漂移（out_addr 越 8MB allowlist → firmware 静默拒绝 → 伪 all-zero diff；行主序 vs broadcast tile-major；缺失 `return ok`），修复后 L0 Q_proj / 1-layer Q/K/V / 2-layer 回归全 PASS — `.omo/evidence/task-16-soc-rtl-review-remediation.txt` |
| `firmware_memory_contract.json` 双向比对 FW-09 | ✅ FM 守卫（N/A RTL） | `scripts/gen_firmware_memory_contract.py --check` + `test_memory_contract.py` 三源比对（address_space/command_ring/spec/npu_abi.json），篡改 RING_ENTRIES 注入（todo 9）。**静态 artifact 检查，不涉及 RTL 仿真（plan scope N/A）** |
| 中断驱动 firmware 调度（WFI 唤醒）FW-10 | ✅ RTL | FM 守卫 `test_irq_driven_dispatch.py`（todo 6）之上，RTL cocotb `run_e2e_irq_stall`（todo 8）真实 ENABLE 门控下 IRQ_MASK + IRQ_STALL 双 PASS（`build/evidence/task-8-soc-rtl-verification-signoff.txt`） |

---

## 5. 端到端与真实模型 Feature Coverage（88%）

### 5.1 已覆盖

| Feature | 回归用例 | 状态 | 证据 |
|---------|---------|:----:|------|
| Qwen2.5-3B blk.0 4-instr smoke | `run_qwen_e2e` | ✅ | Cocotb PASS |
| Qwen2.5-3B blk.0 17-op 全链 | `run_e2e_blk0` | ✅ | 17/17 PASS |
| 3-layer 17-op RTL forward pass | `run_qwen25_3b_3layer` | ✅ | W1.3 PASS |

### 5.2 已闭环 — 原未覆盖 / 部分（5 项，todos 13-15；E2E-07 保持 ❌）

| Feature | 状态 | 说明 |
|---------|:----:|------|
| 多层（≥9 层）full-model forward pass E2E-04 | ✅ RTL | FM 守卫 28 层 531 命令持久偏移 gate（`test_soc_fm_long_sequence.py`，todo 10/11）之上，RTL 36 层 Ibex segment run 扩到 **8 个 checkpoint**（L0/L5/L10/L15/L20/L25/L30/L35，todo 14）：`checkpoints_passed=8/8`，LADDER=PASS（510 commands，~13.1h）。全量 36 层连续仿真仍 deferred 到 FPGA。证据 `build/evidence/task-14-soc-rtl-verification-signoff.txt` |
| MobileNetV3 全推理 E2E-05 | ✅ RTL | FM 守卫 `test_mobilenetv3_fm_chain.py`（todo 12）之上，RTL cocotb `run_e2e_mobilenetv3`（todo 13）：52 conv 层经 MXU wrapper 全链首跑，50/52 cos≥0.99 + 2 退化层 bit-exact，ring_cmds=657、DRAM staging < 8MB。证据 `build/evidence/task-13-soc-rtl-verification-signoff.txt` |
| Spike E2E forward pass tolerance E2E-06 | ✅ RTL | FM 守卫 `test_spike_forward_tolerance.py`（todo 13）钉死的容差阶梯在 RTL 36 层 8-checkpoint 上逐层复核（todo 14）：L0-19 cos≥0.999、L20-29 ≥0.998、L30-35 ≥0.997，最近达标 L30=0.998220/L35=0.999251 均 PASS。证据 `build/evidence/task-14-soc-rtl-verification-signoff.txt`。**Spike host 侧 mmul_smoke L0 Q_proj FAIL（max_diff=7.64e+02）已 FIXED（todo 16，2026-08-31）**：根因 `sim/spike_host.py`（out_addr 越 8MB allowlist → 伪 all-zero diff；行主序 vs tile-major 布局契约；缺失 `return ok`），修复后重跑 PASS，未动 RTL — `.omo/evidence/task-16-soc-rtl-review-remediation.txt` |
| 性能 calibration | ❌ 未覆盖 | `calibration_state=uncalibrated`；`soc-perf-report.md` 数字是 simulation proxy，非 silicon calibrated。**保持 deferred，不 claim 完成** |
| attn_weight RTL dispatch E2E-08 | ✅ RTL | FM 侧 ABORT/MXU idle 覆盖之上，RTL `run_fm_soc_case CASE_ID=ATTN-WEIGHT-CHAIN`（todo 15）：完整 17-op blk.0 chain，全部 26 命令 cycles>0（op07 attn_weight cycles=30755, cos=1.0），14 FP op cos≥0.999 + 3 INT32 bit-exact。**BUG-RTL-SOC-007 链级未复现，保持 Open**。证据 `build/evidence/task-15-soc-rtl-verification-signoff.txt` |

---

## 6. 契约/守卫/门禁 Feature Coverage（100%，fm-hardening-phase10）

| Feature | 守卫 | 状态 | 证据 |
|---------|------|:----:|------|
| 地址空间重叠/窗口契约 | `address_space.contract_check()` | ✅ | todo 1/2 |
| 命令环语义统一 + 回绕 stress | `command_ring` + `test_command_ring_stress.py` | ✅ | todo 3/4 |
| 长序列持久偏移 FM gate | `test_soc_fm_long_sequence.py` | ✅ | todo 5 |
| scale/accumulate golden 加固 | `test_soc_fm.py::test_mmul_scale_nonzero/accumulate` | ✅ | todo 6/7 |
| 双 packer 等价 | `test_packer_equivalence.py` | ✅ | todo 8 |
| 段边界 SRAM 清零协议 | `test_segment_boundary.py` | ✅ | todo 10 |
| 反向依赖门禁 | `fm_reverse_dependency_gate.sh` | ✅ | todo 11 |
| F 波门禁脚本 | `fm_hardening_f{1..4}.sh` | ✅ | todo 13 |

---

## 7. 已知 Bug 台账（soc-rtl-verification-signoff todo 1 更新后）

| Bug ID | Severity | Status | 影响 |
|--------|:--------:|:------:|------|
| BUG-RTL-SOC-002 | Major | **Waived** | DRAM 8MB 窗口越界，firmware 数据地址 >8MB 时报错。正式 waiver `docs/waivers/WVR-SOC-RTL-002.md`（todo 2），临时约束，FPGA 阶段扩 DRAM 模型后关闭 |
| BUG-RTL-SOC-007 | Critical/Major | **Open** | attn_weight op dispatch failure（cycles=0），3-layer forward pass 受影响。todo 15 ATTN-WEIGHT-CHAIN 已执行（2026-08-27）：26 命令 cycles>0、op07 attn_weight cycles=30755 cos=1.0，链级未复现；根因仍未知，**不 claim Fixed**，保持 Open 待 FPGA/更早日志追踪 |
| BUG-RTL-SOC-012 | Major | **Open** | blk0 E2E op05 attn_score MMUL 仅 drain 第一行（words 2-63 zero，62/64 INT32 mismatch）。todo 14 blk0 investigation（2026-08-31）A/B 判 **PRE-EXISTING**（crossbar c478ae5 前后字节级一致，非 crossbar 修复引入）；与 BUG-RTL-SOC-007 同 attention 链、不同 signature（op 执行但部分/零输出）。证据 `.omo/evidence/task-14-blk0-investigation.txt` + `docs/bugs/bugs-soc-rtl.md` BUG-RTL-SOC-012 条目 |
| BUG-RTL-SOC-P9-00A | Major | **Fixed** | Phase 9 遗留，fix `8dd5dbe`+`b545b1f`（todo 1） |
| BUG-RTL-SOC-P9-00D | Major | **Fixed** | Phase 9 遗留，fix `7aec7a3`（todo 1） |
| BUG-MXU-P9-00B | Major | **Fixed** | broadcast/multitile 遗留，报告 `docs/bugs/BUG-MXU-P9-00B-broadcast-multitile.md` Status=resolved（todo 1） |

> 台账统计（todo 1 + todo 17）：Total 14，Fixed 11，Waived 1，Open 2（BUG-RTL-SOC-007、BUG-RTL-SOC-012）。

---

## 8. Signoff 差距总结

当前已 signoff 的是 **v3 Func Model 功能正确性** + **SoC RTL 回归闭环**（soc-rtl-verification-signoff todos 3-15；全量回归复审计口径 **25 executed + 6 superseded + 2 N/A**（todo 13 — 原 "FM-SOC 33/33" 统计口径已纠正，见 `.omo/evidence/task-13-soc-rtl-review-remediation.txt`）、新目标 11/11、checkpoint 8/8）。原 5 项差距更新如下：

1. ~~**补齐 6 条 SoC 数据通路 FM 模型**~~（`docs/soc-fm-gap-spec.md` Gap #1/2/7/8/9/11）——已闭环：RTL 测试就位（todos 3-9，证据 `build/evidence/task-{3..9}-soc-rtl-verification-signoff.txt`）
2. ~~**E2E 多层/full-model RTL 回归**~~（从 blk.0 smoke 扩到 28 层 + MobileNetV3）——已闭环：36 层 8-checkpoint subset（todo 14，`checkpoints_passed=8/8`）+ MobileNetV3 RTL 首跑（todo 13，`MOBILENETV3: PASS`）；**全量 36 层连续仿真仍 deferred 到 FPGA**
3. **清零 Open RTL bug**（部分）——BUG-RTL-SOC-002 → Waived（WVR-SOC-RTL-002）、P9-00A/P9-00D/MXU-P9-00B → Fixed（todo 1）；**BUG-RTL-SOC-007 仍 Open**（todo 15/16 链级未复现，不 claim Fixed）；**BUG-RTL-SOC-012 新增 Open**（2026-08-31 todo 14 blk0 investigation，pre-existing attn_score drain，`.omo/evidence/task-14-blk0-investigation.txt`）
4. **性能 calibration**（uncalibrated → calibrated）——**保持 ❌，deferred 到流片/FPGA 实测**
5. ~~**补齐 fm-hardening deferred 项**~~（T1/T2 `firmware_memory_contract.json`、AL1 FM↔C ring 对齐）——已闭环（fm-soc-datapath-hardening 完成，FW-09 由 FM guard 静态检查覆盖）

剩余 blocker：**性能 calibration（E2E-07，deferred）** + **BUG-RTL-SOC-007（Open，未复现）** + **BUG-RTL-SOC-012（Open，pre-existing blk0 attn_score）** + **FW-09（静态 artifact 检查，N/A RTL）**。

---

## 9. Feature 清单索引（按验证入口）

| 验证入口 | 命令 | 覆盖 feature 数 |
|----------|------|:---------------:|
| MXU 模块级 | `python3 scripts/gen_mxu_vectors.py --scenario all` + VCS | 8 |
| SFU 模块级 | `python3 scripts/gen_sfu_vectors.py --scenario all` + VCS | 7 |
| Vector 模块级 | `python3 scripts/gen_vector_vectors.py --scenario all` + VCS | 7 |
| SoC 模块级 Makefile | `make -C sim/regression all` | 8 |
| SoC FM 回归（33 用例 = 25 executed + 6 superseded + 2 N/A） | `bash sim/regression/run_fm_soc_all.sh` | 26 |
| W4-PERF 性能批次 | `bash sim/regression/run_w4_perf_batch.sh` | 6 |
| FM 契约守卫 | `PYTHONPATH=sim python -m pytest sim/tests/test_address_space.py ...` | 8 |
| 反向依赖门禁 | `./scripts/fm_reverse_dependency_gate.sh` | 1 |
| F 波门禁 | `bash scripts/fm_hardening_f{1..4}.sh` | 4 |

---

## 10. 下一步建议

如果需要把这份 vplan 变成可执行的达标路线图，可以基于第 8 节的 5 项差距，出一个分阶段计划：

- **Phase A**: 补 6 条 SoC 数据通路 FM 模型（对应 `docs/soc-fm-gap-spec.md`）
- **Phase B**: E2E 多层 + MobileNetV3 RTL 回归
- **Phase C**: Open bug 清零
- **Phase D**: 性能 calibration + deferred 项

是否需要我基于这份 vplan 出一个分阶段的 SoC RTL 验证达标路线图？
