# SoC RTL Verification Signoff Specification

> **Document ID**: SOC-RTL-VSS-001
> **Version**: v1.0-draft
> **Date**: 2026-08-24
> **Status**: Draft — 待团队评审批准后生效
> **Scope**: CaduceusCore SoC RTL（Ibex RV32IMC + MXU 64×64 + SFU 7-op + Vector 6-op + DMA axi_cdma + PCIe EP + AXI4 Crossbar M=6/S=2 + APB Decoder + INTC 7-source + Doorbell + SRAM 4MB + DRAM behavioral + Firmware）
> **前置文档**: `.omo/plans/soc-rtl-verification-vplan.md`（feature coverage 全景）、`docs/soc-fm-gap-spec.md`、`docs/bugs/bugs-soc-rtl.md`、`docs/func-model-signoff-checklist.md`
> **变更控制**: 本文档每次修订必须 bump version 并在 §11 变更日志记录；feature 清单增减需 §8 owner 矩阵中至少一名 RTL owner + 一名验证 owner 签字。

---

## 1. 目的与适用范围

### 1.1 目的

定义 CaduceusCore SoC RTL 验证的 **正式 signoff 标准**，包括：

- 完整 feature 清单与每个 feature 的 **acceptance criteria**（量化判据）
- **coverage 阈值**（feature 级 + code coverage 级）
- **缺口分级**（Blocking / Non-blocking / Waivable）
- **entry/exit gate**（进入 signoff 的前置条件 + signoff 完成判据）
- **waiver 规则**（批准权、有效期、回归要求）
- **回归 cadence**（RTL/firmware 改动后的重跑要求）
- **owner 责任矩阵**
- **版本控制**

### 1.2 适用范围

- SoC 全芯片 RTL 验证（`rtl/soc/`、`rtl/wrapper/`、`rtl/cpu/`、`rtl/ip/`、`rtl/intc/`）
- 模块级 RTL 验证（`rtl/mxu/`、`rtl/sfu/`、`rtl/vector/`）作为 SoC signoff 的前置条件
- Func Model 契约守卫（`sim/address_space.py`、`sim/command_ring.py` 等）作为 RTL signoff 的前置 gate
- 固件验证（`firmware/npu_firmware.c`）作为 SoC signoff 的依赖项
- **不适用**：Arc Model DSE、性能 spec signoff（已有独立 signoff 文档 `func-model-signoff-checklist.md` §Performance）

### 1.3 术语

| 术语 | 定义 |
|------|------|
| **Feature** | 一个可独立验证的 RTL 功能点（如"MXU ACCUMULATE 模式"、"AXI crossbar 并发压力"） |
| **Acceptance Criteria** | 该 feature 通过验证的量化判据（如"4096/4096 逐比特匹配"、"0 errors in 11,455 cycles"） |
| **Blocking Gap** | 缺口未补则 SoC RTL signoff **不可完成** |
| **Non-blocking Gap** | 缺口未补不阻塞 signoff，但必须在 signoff 后的 roadmap 中跟踪 |
| **Waivable Gap** | 缺口可通过正式 waiver 流程豁免（有时间限制 + 回归要求） |
| **Entry Gate** | 进入 SoC RTL signoff 流程的前置条件 |
| **Exit Gate** | SoC RTL signoff 完成的判据 |
| **Code Coverage** | line / branch / toggle / FSM state coverage，由 VCS `-cm` 采集 |

---

## 2. Entry Gate — 进入 signoff 的前置条件

SoC RTL signoff 流程 **不能启动**，除非以下全部满足：

| # | 前置条件 | 判据 | 验证命令 | 责任 |
|---|----------|------|----------|------|
| E1 | 模块级 RTL 回归全过 | MXU 8 场景 + SFU 319 场景 + Vector 63 场景 100% PASS | `python3 scripts/run_batch_regression.py` | RTL owner |
| E2 | Func Model 功能 signoff 通过 | v3 signoff F-FM-01..32 全 PASS | 见 `func-model-signoff-checklist.md` | FM owner |
| E3 | fm-hardening-phase10 契约守卫全过 | F-FM-H01..H08 全 PASS | `bash scripts/fm_hardening_f1_audit.sh` | 验证 owner |
| E4 | F4 范围门禁通过 | rtl/ 零非预期改动、冻结面零改动 | `bash scripts/fm_hardening_f4_scope_gate.sh` | 验证 owner |
| E5 | 反向依赖门禁 dry-run clean | 无未验证的 RTL/firmware 变更 | `./scripts/fm_reverse_dependency_gate.sh --dry-run` | 验证 owner |
| E6 | SoC elaboration 通过 | 47 模块 0 errors | `make -C sim/regression run_soc_elab` | RTL owner |
| E7 | 已知 Open Bug 已分类 | §7 表中每个 Open bug 已标注 Blocking / Non-blocking / Waivable | 人工审查 | 验证 owner + RTL owner |

> **E1-E6 任一不满足 → signoff 流程不可启动。** E7 是分类步骤，不阻塞流程启动但必须在 exit gate 前完成。

---

## 3. Feature 清单与 Acceptance Criteria

### 3.1 计算引擎 — MXU（8 features，100% covered）

| ID | Feature | Acceptance Criteria | 回归用例 | 状态 | 证据 |
|----|---------|---------------------|----------|:----:|------|
| MXU-01 | 单 tile INT4×INT8→INT32 bit-exact | 4096/4096 值与 GoldenMXU 逐比特匹配，0 mismatch | `tb_mxu` single_tile | ✅ | 4096/4096 |
| MXU-02 | 多 tile K 拆分 | 9 场景全部 PASS，每个场景 golden 逐比特匹配 | `tb_mxu` multi_tile_K | ✅ | 9/9 |
| MXU-03 | 多 tile N 拆分 | 9 场景全部 PASS | `tb_mxu` multi_tile_N | ✅ | 9/9 |
| MXU-04 | 多 tile M 拆分 | 9 场景全部 PASS | `tb_mxu` multi_tile_M | ✅ | 9/9 |
| MXU-05 | 溢出饱和 | INT32 饱和钳位值与 golden 匹配，0 mismatch | `tb_mxu` overflow | ✅ | PASS |
| MXU-06 | 零维/部分 tile | 零维不崩溃、部分 tile padding 值正确 | `tb_mxu` zero_dim/partial | ✅ | PASS |
| MXU-07 | 随机 100 组合 | 100/100 与 golden 逐比特匹配 | `tb_mxu` random | ✅ | 100/100 |
| MXU-08 | ACCUMULATE 模式（CTRL[2]） | 两命令链结果 == partial0 + partial1，与 `matmul_int4_per_block` 组合 golden 一致，max_abs_diff=0.0；失败注入（accumulate=False）max_abs_diff>500 | FM-SOC-003 + `test_mmul_accumulate` | ✅ | bit-exact + 注入 diff=573 |

### 3.2 计算引擎 — SFU（7 features，100% covered）

| ID | Feature | Acceptance Criteria | 回归用例 | 状态 | 证据 |
|----|---------|---------------------|----------|:----:|------|
| SFU-01 | Softmax 8-stage | abs_tol=2e-3, rel_tol=1e-2，319/319 场景 PASS | `tb_sfu` + FM-SOC-004 | ✅ | 319/319 |
| SFU-02 | LayerNorm 6-stage | 同上容差，全场景 PASS | `tb_sfu` | ✅ | PASS |
| SFU-03 | RMSNorm two-pass | 同上容差，全场景 PASS | `tb_sfu` + FM-SOC-004 | ✅ | PASS |
| SFU-04 | RoPE 16-stage CORDIC | 同上容差，全场景 PASS | `tb_sfu` + FM-SOC-004 | ✅ | PASS |
| SFU-05 | GELU 4-segment | 同上容差，全场景 PASS | `tb_sfu` | ✅ | PASS |
| SFU-06 | SiLU Newton-Raphson | 同上容差，全场景 PASS | `tb_sfu` | ✅ | PASS |
| SFU-07 | exp LUT | 256-entry LUT 值与 numpy 参考一致 | `exp_lut_tb` | ✅ | 256-entry |

### 3.3 计算引擎 — Vector（7 features，100% covered）

| ID | Feature | Acceptance Criteria | 回归用例 | 状态 | 证据 |
|----|---------|---------------------|----------|:----:|------|
| VEC-01 | ADD 128-wide SIMD | INT32 逐比特匹配，63/63 PASS | `tb_vector` + FM-SOC-005 | ✅ | 63/63 |
| VEC-02 | MUL 128-wide SIMD | INT32 饱和乘法逐比特匹配 | `tb_vector` + FM-SOC-005 | ✅ | PASS |
| VEC-03 | MAX 逐元素 | 逐比特匹配 | `tb_vector` | ✅ | PASS |
| VEC-04 | MAX_REDUCE 128→1 | 逐比特匹配 | `tb_vector` | ✅ | PASS |
| VEC-05 | SUM_REDUCE 128→1 | INT64 中间累加，逐比特匹配 | `tb_vector` | ✅ | PASS |
| VEC-06 | INT32→FP16 CONV | 131073/131073 与 numpy float16 逐比特 | `tb_vector` + FM-SOC-005 | ✅ | 131073/131073 |
| VEC-07 | RESID_ADD 饱和 | INT32 饱和加法逐比特匹配 | `tb_vector` + FM-SOC-005 | ✅ | PASS |

### 3.4 SoC 互联与数据通路（18 features，12 covered，6 gap）

| ID | Feature | Acceptance Criteria | 回归用例 | 状态 | Gap # | 分级 |
|----|---------|---------------------|----------|:----:|:-----:|:----:|
| SOC-01 | AXI4 Crossbar M=6/S=2 并发压力 | ≥10,000 cycles，≥1,000 txns，0 errors | `run_crossbar_stress` | ✅ | — | — |
| SOC-02 | APB Decoder 8-slave 选择 + pslverr | 全部 8 slave 选择正确，unmapped 返回 pslverr | `run_apb_smoke` | ✅ | — | — |
| SOC-03 | DMA wrapper 传输 | 5 test cases ALL PASS，CMD.START + STATUS.BUSY 验证 | `run_dma_test` | ✅ | — | — |
| SOC-04 | PCIe EP wrapper TLP→AXI→readback | TLP 发送 + AXI 路由 + readback 数据匹配 | `run_pcie_test` | ✅ | — | — |
| SOC-05 | DRAM behavioral model | 100 随机 AXI4 事务，0 错误 | `run_dram_test` | ✅ | — | — |
| SOC-06 | Doorbell ring buffer 协议 | HOST_TAIL/NPU_HEAD 轮询正确，ring wrap 无误 | FM-SOC-006 + Ibex full | ✅ | — | — |
| SOC-07 | INTC 7-source 中断控制器 | 13/13 checks PASS（PENDING/ENABLE/THRESHOLD/ACK） | `run_intc_test` | ✅ | — | — |
| SOC-08 | SoC elaboration | 47 模块 0 errors，0 undriven | `run_soc_elab` | ✅ | — | — |
| SOC-09 | Cocotb Qwen blk.0 17-op chain | 17/17 ops PASS，逐 op golden 对比 | `run_e2e_blk0` | ✅ | — | — |
| SOC-10 | SRAM 控制器 4MB 512-bit burst | SoC 集成验证中 SRAM 读写正确 | SoC elab + Cocotb | ✅ | — | — |
| SOC-11 | Boot ROM $readmemh 加载 | Ibex boot 到 main()，固件加载成功 | Ibex boot smoke | ✅ | — | — |
| SOC-12 | SFU wrapper 宽度转换 + DONE | BUG-RTL-SOC-WV-001 fix 后回归 PASS | wrapper test | ✅ | — | — |
| SOC-13 | PCIe TLP 功能模型（host→TLP→AXI→SRAM/DRAM 完整链） | TLP 解析正确、BAR 路由正确、MSI-X 中断生成验证 | — | ❌ | #7 | **Blocking** |
| SOC-14 | AXI Crossbar 仲裁行为模型 | Python 级仲裁序/反压模型与 RTL 仲裁序一致，公平性边界验证 | — | ⚠️ | #8 | Non-blocking |
| SOC-15 | APB-MMIO 统一寄存器模型 | 统一寄存器抽象，per-engine 寄存器语义单一事实源 | — | ⚠️ | #1 | Non-blocking |
| SOC-16 | Ibex→AXI 共享地址空间 | RISCVMini 与 FuncModel 共享 SRAM/DRAM 地址空间，CPU 数据访问路径 FM 对齐 | — | ⚠️ | #2 | Non-blocking |
| SOC-17 | IRQ 链路（engine→INTC→CPU WFI 唤醒） | 完整中断唤醒链验证：engine IRQ → INTC → CPU WFI 退出 → firmware 调度 | — | ⚠️ | #9 | **Blocking** |
| SOC-18 | Ibex 固件 boot→DMEM→MMIO→poll IRQ 序列 | FM 层 boot 序列等价模型，固件控制流与 FM 对齐 | — | ⚠️ | #11 | Non-blocking |

### 3.5 固件与 CPU 集成（10 features，7 covered，3 gap）

| ID | Feature | Acceptance Criteria | 回归用例 | 状态 | 分级 |
|----|---------|---------------------|----------|:----:|:----:|
| FW-01 | 全 opcode 分发 | MMUL/SFU/Vector/DMA 4/4 PASS | `test_firmware.py` + FM-SOC-006 | ✅ | — |
| FW-02 | Tile 级双缓冲调度 | tile_scheduler 调度结果与 golden 一致 | `tile_scheduler.py` + FM-SOC-006 | ✅ | — |
| FW-03 | MMIO 寄存器映射一致 | 49 registers C/Python 完全匹配 | `check_mmio_map.py` | ✅ | — |
| FW-04 | ABI 常量单一来源 | schema→C header→Python 数值比对全过，`gen_npu_abi.py --check` exit 0 | `test_npu_abi_constants.py` | ✅ | — |
| FW-05 | Ibex boot ROM 加载固件 | Ibex boot 到 main() PASS | Ibex boot smoke | ✅ | — |
| FW-06 | Doorbell 轮询 | 33 cases HOST_TAIL/NPU_HEAD 正确 | FM-SOC-001..032 | ✅ | — |
| FW-07 | 地址空间/环布局契约 | `contract_check()` 全过，注入 DESC_BASE=0x80001000 抛 OverlapError | `test_address_space.py` + `test_command_ring.py` | ✅ | — |
| FW-08 | Spike↔Ibex 固件行为对齐 | ring 管理/调度细节交叉一致性 gate PASS | — | ⚠️ | Non-blocking |
| FW-09 | `firmware_memory_contract.json` 双向比对 | FM 生成契约 JSON 与 RTL 固件 DRAM 用量比对 PASS | — | ❌ | Non-blocking |
| FW-10 | 中断驱动 firmware 调度 | 完整中断驱动控制流验证（WFI 唤醒→调度） | — | ⚠️ | **Blocking** |

### 3.6 端到端与真实模型（8 features，3 covered，5 gap）

| ID | Feature | Acceptance Criteria | 回归用例 | 状态 | 分级 |
|----|---------|---------------------|----------|:----:|:----:|
| E2E-01 | Qwen2.5-3B blk.0 4-instr smoke | 4 指令 Cocotb PASS | `run_qwen_e2e` | ✅ | — |
| E2E-02 | Qwen2.5-3B blk.0 17-op 全链 | 17/17 ops PASS | `run_e2e_blk0` | ✅ | — |
| E2E-03 | 3-layer 17-op RTL forward pass | 3 层全过，per-layer cos_sim ≥ 0.99 | `run_qwen25_3b_3layer` | ✅ | — |
| E2E-04 | 多层（≥9 层）full-model forward pass | 28 层全过，checkpoint cos_sim ≥ 0.99，无 NaN | — | ❌ | **Blocking** |
| E2E-05 | MobileNetV3 全推理 | 全推理 PASS，输出与 golden cos_sim ≥ 0.99 | — | ❌ | **Blocking** |
| E2E-06 | Spike E2E forward pass tolerance | Qwen2.5-1.5B forward max_abs ≤ 1e-1 vs llama.cpp | — | ⚠️ | Waivable |
| E2E-07 | 性能 calibration | `calibration_state=calibrated`，RTL cycle 实测 vs spec ≤ 20% | — | ❌ | Non-blocking |
| E2E-08 | attn_weight RTL dispatch | BUG-RTL-SOC-007 关闭或 waiver，attn_weight cycles > 0 | — | ⚠️ | **Blocking** |

### 3.7 契约/守卫/门禁（8 features，100% covered，fm-hardening-phase10）

| ID | Feature | Acceptance Criteria | 守卫 | 状态 | 证据 |
|----|---------|---------------------|------|:----:|------|
| CON-01 | 地址空间重叠/窗口契约 | `contract_check()` 全过；注入 DESC_BASE=0x80001000 抛 OverlapError | `test_address_space.py` + `test_spike_host_overlap.py` | ✅ | todo 1/2 |
| CON-02 | 命令环语义统一 + 回绕 stress | `spike_host` 无 `% 64` 残留；140 命令跨 entry 128 + 1023→0 回绕 PASS | `test_command_ring.py` + `test_command_ring_stress.py` | ✅ | todo 3/4 |
| CON-03 | 长序列持久偏移 FM gate | ≥200 命令持久偏移跑通，末层 cos ≥ 0.999 | `test_soc_fm_long_sequence.py` | ✅ | todo 5 |
| CON-04 | scale golden 加固 | 非平凡 FP32 scale 输出与 golden max_abs_diff=0.0；FP16 注入 diff>800 | `test_mmul_scale_nonzero` | ✅ | todo 6 |
| CON-05 | accumulate golden 加固 | 两命令累加与 golden 一致；注入 diff>500 | `test_mmul_accumulate` | ✅ | todo 7 |
| CON-06 | 双 packer 等价 | 6 网格点逐字节相等；行主序注入不等 | `test_packer_equivalence.py` | ✅ | todo 8 |
| CON-07 | 段边界 SRAM 清零协议 | `clear_sram=True` 时空 sram 抛 SegmentBoundaryError；双段场景 PASS | `test_segment_boundary.py` | ✅ | todo 10 |
| CON-08 | 反向依赖门禁 | dry-run clean exit 0；触发变更 exit 1；full run exit 0 + state 写入 | `fm_reverse_dependency_gate.sh` | ✅ | todo 11 |

---

## 4. Coverage 阈值定义

### 4.1 Feature Coverage（本规范定义）

| 层级 | 要求 | 度量方法 |
|------|------|----------|
| **Feature 级** | §3 全部 feature 状态为 ✅ 或 Waivable | 人工 + 脚本统计 §3 表 |
| **Blocking Gap** | §3 中所有 Blocking 分级缺口必须 ✅ 或正式 waiver | §6 waiver 流程 |
| **Non-blocking Gap** | 可 signoff，但必须在 §9 roadmap 跟踪 | 不阻塞 exit gate |

### 4.2 Code Coverage（VCS `-cm` 采集，signoff 时必须报告）

| 覆盖类型 | 阈值 | 采集命令 | 适用模块 |
|----------|:----:|----------|----------|
| Line coverage | ≥ 95% | `vcs -cm line+branch+toggle ...` + `dve -cov` | `rtl/soc/`、`rtl/wrapper/`、`rtl/intc/` |
| Branch coverage | ≥ 90% | 同上 | 同上 |
| Toggle coverage | ≥ 85% | 同上 | `rtl/soc/axi_crossbar.v`、`rtl/soc/sram_ctrl.v`、`rtl/ip/dma_wrapper.v` |
| FSM state coverage | 100% | `vcs -cm fsm` | `rtl/mxu/controller.v`、`rtl/sfu/softmax_hw.v` 等含 FSM 模块 |

> Code coverage 阈值在首次 signoff 时为 **目标值**；未达标项必须在 §6 waiver 中注明原因和达标计划。

### 4.3 Assertion Coverage

| 要求 | 度量 |
|------|------|
| 每个 `BUG-RTL-SOC-*` 修复必须有对应回归用例 | §3 表中证据列 |
| fm-hardening-phase10 每个 CON feature 必须有失败注入用例 | CON-01..08 已满足 |
| SoC 级 SystemVerilog `assert` / `$error` 覆盖关键协议检查 | signoff 时 grep `assert` / `$error` 统计 |

---

## 5. Exit Gate — Signoff 完成判据

SoC RTL signoff **完成**，当且仅当以下全部满足：

| # | 判据 | 验证方法 | 责任 |
|---|------|----------|------|
| X1 | §3 全部 Feature 状态为 ✅ 或 Waivable | §3 表统计 | 验证 owner |
| X2 | 全部 Blocking Gap 已 ✅ 或正式 waiver | §3 分级列 + §6 waiver 记录 | 验证 owner + RTL owner |
| X3 | §4.2 Code Coverage 阈值达标或 waiver | VCS coverage report | RTL owner |
| X4 | §7 Open Bug 全部 Fixed 或 waived | `bugs-soc-rtl.md` Status 列 | RTL owner |
| X5 | 33-case SoC FM 回归全过 | `bash sim/regression/run_fm_soc_all.sh` PASS=33 FAIL=0 | 验证 owner |
| X6 | 反向依赖门禁 full run 通过 | `./scripts/fm_reverse_dependency_gate.sh` exit 0 | 验证 owner |
| X7 | F1-F4 全 APPROVE | `bash scripts/fm_hardening_f{1..4}.sh` 全 exit 0 | 验证 owner |
| X8 | Entry Gate E1-E7 全满足 | §2 表 | 验证 owner |

> **X1-X8 任一不满足 → signoff 未完成。** Non-blocking Gap 不阻塞 exit gate，但必须在 §9 roadmap 记录。

---

## 6. Waiver 规则

### 6.1 Waiver 分类

| 类型 | 批准权 | 有效期 | 回归要求 |
|------|--------|--------|----------|
| **Permanent Waiver** | RTL owner + 验证 owner + 项目负责人三方签字 | 永久（除非条件变化） | 每次 RTL release 重跑相关用例确认仍可 waiver |
| **Temporary Waiver** | RTL owner + 验证 owner 双方签字 | 至下一个 milestone（最多 4 周） | 到期前必须补齐或续签；续签需附延期理由 |
| **Environmental Waiver** | 验证 owner 单方签字 | 至环境修复 | 仅适用于"工具/环境缺失导致无法跑"，不适用于 RTL bug |

### 6.2 Waiver 必须包含

- Waiver ID（格式 `WVR-SOC-RTL-NNN`）
- 关联 Feature ID / Bug ID
- Waiver 类型
- 豁免理由（为什么当前不修）
- 风险评估（如果不修，最坏情况）
- 回归要求（到期前重跑什么）
- 批准人签字 + 日期
- 有效期

### 6.3 Waiver 登记

Waiver 记录在 `docs/bugs/bugs-soc-rtl.md` 对应 bug 条目的 Status 字段改为 `Waived (WVR-SOC-RTL-NNN, expires YYYY-MM-DD)`，并在本规范 §10 waiver log 追加。

### 6.4 当前建议 Waiver

| Gap/Bug | 建议 Waiver 类型 | 理由 |
|---------|:----------------:|------|
| E2E-06 Spike forward pass tolerance | Temporary | BUG-SOC-FM-005 pre-existing，非 RTL 回归，数值 gap 在 INT4 量化路径 |
| SOC-14 AXI 仲裁行为模型 | Non-blocking（不需要 waiver） | RTL stress 已验证无错，FM 模型缺失不阻塞 RTL signoff |
| SOC-15 APB-MMIO 统一模型 | Non-blocking | APB decoder 功能已验证，统一抽象是 FM 层改进 |
| SOC-16 Ibex-AXI 共享地址空间 | Non-blocking | Ibex RTL boot 可跑，FM 对齐是 FM 层改进 |
| SOC-18 固件 boot 序列模型 | Non-blocking | Ibex RTL boot 可跑，FM 对齐是 FM 层改进 |
| FW-08 Spike↔Ibex 对齐 | Non-blocking | 两条路径各自验证通过，交叉 gate 是增强 |
| FW-09 firmware_memory_contract.json | Non-blocking | deferred 项，不阻塞当前 RTL signoff |
| E2E-07 性能 calibration | Non-blocking | performance spec 有独立 signoff |

---

## 7. Open Bug 分类

| Bug ID | Severity | 当前 Status | signoff 分级 | 阻塞 exit gate? | 处置 |
|--------|:--------:|:-----------:|:------------:|:---------------:|------|
| BUG-RTL-SOC-002 | Major | Open | **Blocking** | 是 | 必须修复或 waiver（DRAM 窗口扩大或 firmware 地址限制 8MB 内） |
| BUG-RTL-SOC-007 | Critical/Major | Open | **Blocking** | 是 | 必须修复或 waiver（attn_weight dispatch 根因） |
| BUG-RTL-SOC-P9-00A | Major | Open | Non-blocking | 否 | Phase 9 遗留，不阻塞当前 SoC signoff |
| BUG-RTL-SOC-P9-00D | Major | Open | Non-blocking | 否 | Phase 9 遗留 |

---

## 8. Owner 责任矩阵

| 角色 | 职责 | signoff 权限 |
|------|------|:------------:|
| **RTL Owner** | RTL 设计、模块级回归、code coverage、Bug 修复 | Exit Gate X2/X3/X4 签字 |
| **验证 Owner** | vplan 维护、SoC FM 回归、契约守卫、F 波门禁、waiver 管理 | Entry Gate E1-E7 + Exit Gate X1/X5-X8 签字 |
| **FM Owner** | Func Model 正确性、契约守卫、ABI 常量、gap report | Entry Gate E2/E3 签字 |
| **Firmware Owner** | 固件控制流、ABI header、Spike/Ibex 对齐 | FW-01..10 签字 |
| **项目负责人** | waiver 最终批准、资源调度 | Permanent Waiver 签字 |

---

## 9. Signoff 后 Roadmap（Non-blocking Gap 跟踪）

Non-blocking Gap 不阻塞当前 signoff，但必须在 signoff 后按以下 roadmap 跟踪：

| Phase | 缺口 | 目标 | 优先级 |
|-------|------|------|:------:|
| Phase A | SOC-13/14/15/16/17/18 + FW-08/09/10 | 补齐 6 条 SoC 数据通路 FM 模型 + 3 项固件对齐 | High |
| Phase B | E2E-04/05 | 28 层 full-model + MobileNetV3 RTL 回归 | High |
| Phase C | BUG-RTL-SOC-002/007 | Open bug 清零 | High |
| Phase D | E2E-07 | 性能 calibration（uncalibrated → calibrated） | Medium |

---

## 10. Waiver Log

| Waiver ID | 关联 | 类型 | 理由 | 批准人 | 有效期 | 状态 |
|-----------|------|------|------|--------|--------|:----:|
| （首次 signoff 时填写） | | | | | | |

---

## 11. 变更日志

| Version | Date | 变更内容 | 变更人 |
|---------|------|----------|--------|
| v1.0-draft | 2026-08-24 | 初始草案，基于 vplan 升级 | Prometheus |

---

## 12. 回归 Cadence

| 触发条件 | 重跑内容 | 命令 | 责任 |
|----------|----------|------|------|
| RTL/firmware/桥接文件变更 | 反向依赖门禁 full run | `./scripts/fm_reverse_dependency_gate.sh` | 验证 owner |
| 每次合并到 main | F1-F4 门禁 | `bash scripts/fm_hardening_f{1..4}.sh` | 验证 owner |
| 每个 milestone | 33-case SoC FM 回归 + W4-PERF 6 批次 | `bash sim/regression/run_fm_soc_all.sh` + `bash sim/regression/run_w4_perf_batch.sh` | 验证 owner |
| signoff 前最终验证 | 全量：模块级 + SoC FM + W4-PERF + F1-F4 + coverage | §2 Entry + §5 Exit 全量 | 全部 owner |

---

## 13. Feature Coverage 汇总（当前状态）

| 类别 | Feature 总数 | ✅ Covered | ❌/⚠️ Gap | Blocking Gap | 覆盖率 |
|------|:-----------:|:----------:|:---------:|:------------:|:------:|
| MXU | 8 | 8 | 0 | 0 | 100% |
| SFU | 7 | 7 | 0 | 0 | 100% |
| Vector | 7 | 7 | 0 | 0 | 100% |
| SoC 互联 | 18 | 12 | 6 | 2 | 67% |
| 固件/CPU | 10 | 7 | 3 | 1 | 70% |
| E2E/模型 | 8 | 3 | 5 | 3 | 38% |
| 契约/门禁 | 8 | 8 | 0 | 0 | 100% |
| **合计** | **66** | **52** | **14** | **6** | **79%** |

### Blocking Gap 清单（6 项，必须修复或 waiver 才能 signoff）

1. **SOC-13** — PCIe TLP 功能模型（host→TLP→AXI→SRAM/DRAM 完整链）
2. **SOC-17** — IRQ 链路（engine→INTC→CPU WFI 唤醒完整链）
3. **FW-10** — 中断驱动 firmware 调度（WFI 唤醒→调度）
4. **E2E-04** — 多层（≥9 层）full-model forward pass
5. **E2E-05** — MobileNetV3 全推理
6. **E2E-08** — attn_weight RTL dispatch（BUG-RTL-SOC-007）

> **当前 signoff 状态：不可 signoff。** 6 项 Blocking Gap + 2 个 Open Blocking Bug 未关闭。
