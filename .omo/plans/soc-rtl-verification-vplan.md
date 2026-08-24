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
| SoC 互联与数据通路 | 18 | 12 | 6 | 67% |
| 固件与 CPU 集成 | 10 | 7 | 3 | 70% |
| 端到端与真实模型 | 8 | 3 | 5 | 38% |
| 契约/守卫/门禁（fm-hardening-phase10） | 8 | 8 | 0 | 100% |
| **合计** | **66** | **52** | **14** | **79%** |

> **结论**: 当前 SoC RTL 验证 feature coverage **未到 100%**，不满足 full SoC RTL signoff。计算引擎和契约守卫已 100% 覆盖；SoC 集成数据通路、E2E 多层模型、MobileNetV3、性能 calibration 仍有缺口。

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
| RMSNorm two-pass | `tb_sfu` + FM-SOC-004 | ✅ | PASS |
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

## 3. SoC 互联与数据通路 Feature Coverage（67%）

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

### 3.2 未覆盖 / 部分（6 项）

| Feature | Gap # | 状态 | 说明 | 阻塞影响 |
|---------|:-----:|:----:|------|----------|
| PCIe TLP 功能模型（TLP 解析/BAR 路由/MSI-X） | #7 | ❌ 未覆盖 | Func Model `host_write_*` 直接写 DRAM，无 TLP 解析；RTL wrapper test 只验证 EP 侧，未覆盖 host→TLP→AXI→SRAM/DRAM 完整链 | host↔NPU 真实 PCIe 数据通路无 FM golden |
| AXI Crossbar 仲裁行为模型 | #8 | ⚠️ 部分 | RTL stress 验证了并发无错，但 Func Model MMIOBridge 绕过 crossbar 直接访问；无 Python 级仲裁序/反压模型 | 仲裁公平性/反压边界无 FM 预测 |
| APB-MMIO 统一寄存器模型 | #1 | ⚠️ 部分 | APB decoder 功能验证，但无统一寄存器抽象，per-engine `_handle_*` 各自重实现 | 寄存器语义一致性无单一事实源 |
| Ibex→AXI 共享地址空间 | #2 | ⚠️ 部分 | RISCVMini 独立 `self.mem`，未与 FuncModel SRAM/DRAM 共享地址空间；Ibex RTL boot 可跑但 FM 层无共享访存模型 | CPU 数据访问路径无 FM 对齐 |
| IRQ 链路（engine→INTC→CPU WFI 唤醒） | #9 | ⚠️ 部分 | INTC 模块级验证 + Ibex doorbell IRQ smoke，但 WFI 为 NOP，无完整 engine→INTC→CPU 中断唤醒链 | 中断驱动 firmware 调度无 FM 验证 |
| Ibex 固件 boot→DMEM→MMIO→poll IRQ 序列 | #11 | ⚠️ 部分 | NPUFirmware 绕过 RISCVMini，无 boot 序列模型；Ibex RTL 可 boot 到 main()，但 FM 层无等价 | 固件控制流与 FM 不对齐 |

---

## 4. 固件与 CPU 集成 Feature Coverage（70%）

### 4.1 已覆盖

| Feature | 回归用例 | 状态 | 证据 |
|---------|---------|:----:|------|
| 固件全 opcode 分发（MMUL/SFU/Vector/DMA） | `test_firmware.py` + FM-SOC-006 | ✅ | 4/4 PASS |
| Tile 级双缓冲调度 | `tile_scheduler.py` + FM-SOC-006 | ✅ | PASS |
| MMIO 寄存器映射一致（C/Python） | `check_mmio_map.py` | ✅ | 49 registers match |
| ABI 常量单一来源（schema→C header→Python） | `test_npu_abi_constants.py` | ✅ | fm-hardening todo 9 |
| Ibex boot ROM 加载固件 | Ibex boot smoke | ✅ | PASS |
| Doorbell HOST_TAIL/NPU_HEAD 轮询 | FM-SOC-001..032 + Ibex | ✅ | 33 cases |
| 地址空间/环布局契约 | `test_address_space.py` + `test_command_ring.py` | ✅ | fm-hardening todo 1/2/3 |

### 4.2 未覆盖 / 部分（3 项）

| Feature | 状态 | 说明 |
|---------|:----:|------|
| Spike 固件与 Ibex 固件行为对齐 | ⚠️ 部分 | Spike 路径与 Ibex 路径的 ring 管理/调度细节未做交叉一致性 gate（fm-hardening AL1 deferred） |
| `firmware_memory_contract.json` 双向比对 | ❌ 未覆盖 | fm-hardening T1/T2 deferred：无 FM 生成内存契约 JSON 与 RTL 固件实际 DRAM 用量比对 |
| 中断驱动 firmware 调度（WFI 唤醒） | ⚠️ 部分 | 模块级 INTC + smoke，无完整中断驱动控制流验证 |

---

## 5. 端到端与真实模型 Feature Coverage（38%）

### 5.1 已覆盖

| Feature | 回归用例 | 状态 | 证据 |
|---------|---------|:----:|------|
| Qwen2.5-3B blk.0 4-instr smoke | `run_qwen_e2e` | ✅ | Cocotb PASS |
| Qwen2.5-3B blk.0 17-op 全链 | `run_e2e_blk0` | ✅ | 17/17 PASS |
| 3-layer 17-op RTL forward pass | `run_qwen25_3b_3layer` | ✅ | W1.3 PASS |

### 5.2 未覆盖 / 部分（5 项）

| Feature | 状态 | 说明 |
|---------|:----:|------|
| 多层（≥9 层）full-model forward pass | ❌ 未覆盖 | 当前段跑仅验证 checkpoint 子集；无完整 28 层 RTL signoff |
| MobileNetV3 全推理 | ❌ 未覆盖 | `rtl_development_plan.md` 明确“留待后续 Phase”；im2col→GEMM 通路引擎层已验证，但 SoC 级未跑 |
| Spike E2E forward pass tolerance | ⚠️ 部分 | Qwen2.5-1.5B forward 可跑但数值 gap vs llama.cpp 存在（BUG-SOC-FM-005 pre-existing） |
| 性能 calibration | ❌ 未覆盖 | `calibration_state=uncalibrated`；`soc-perf-report.md` 数字是 simulation proxy，非 silicon calibrated |
| attn_weight RTL dispatch | ⚠️ 部分 | BUG-RTL-SOC-007 Open；FM 侧已补 `test_mmul_attn_weight_shape` 覆盖，但 RTL dispatch 根因未修 |

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

## 7. 已知 Open Bug（阻塞 signoff）

| Bug ID | Severity | Status | 影响 |
|--------|:--------:|:------:|------|
| BUG-RTL-SOC-002 | Major | Open | DRAM 8MB 窗口越界，firmware 数据地址 >8MB 时报错 |
| BUG-RTL-SOC-007 | Critical/Major | Open | attn_weight op dispatch failure（cycles=0），3-layer forward pass 受影响 |
| BUG-RTL-SOC-P9-00A | Major | Open | Phase 9 遗留 |
| BUG-RTL-SOC-P9-00D | Major | Open | Phase 9 遗留 |

---

## 8. Signoff 差距总结

当前已 signoff 的是 **v3 Func Model 功能正确性**（带显式 deferred），不是 **full SoC RTL 验证覆盖完成**。距离 SoC RTL signoff 还需：

1. **补齐 6 条 SoC 数据通路 FM 模型**（`docs/soc-fm-gap-spec.md` Gap #1/2/7/8/9/11）
2. **E2E 多层/full-model RTL 回归**（从 blk.0 smoke 扩到 28 层 + MobileNetV3）
3. **清零 4 个 Open RTL bug**（至少 002/007）
4. **性能 calibration**（uncalibrated → calibrated）
5. **补齐 fm-hardening deferred 项**（T1/T2 `firmware_memory_contract.json`、AL1 FM↔C ring 对齐）

---

## 9. Feature 清单索引（按验证入口）

| 验证入口 | 命令 | 覆盖 feature 数 |
|----------|------|:---------------:|
| MXU 模块级 | `python3 scripts/gen_mxu_vectors.py --scenario all` + VCS | 8 |
| SFU 模块级 | `python3 scripts/gen_sfu_vectors.py --scenario all` + VCS | 7 |
| Vector 模块级 | `python3 scripts/gen_vector_vectors.py --scenario all` + VCS | 7 |
| SoC 模块级 Makefile | `make -C sim/regression all` | 8 |
| SoC FM 回归（33 用例） | `bash sim/regression/run_fm_soc_all.sh` | 26 |
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
