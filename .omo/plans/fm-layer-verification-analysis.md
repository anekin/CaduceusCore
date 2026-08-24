# Func Model 层面可验证的 SoC RTL Feature 分析

> **Date**: 2026-08-24
> **目的**: 逐条分析 SoC RTL vplan 中每个 feature 能否在 Func Model 层面先做验证，以便尽早在 FM 阶段暴露问题，避免到 RTL 段跑（7.5h）才发现。
> **前置**: `.omo/plans/soc-rtl-verification-vplan.md`（66 feature）、`docs/soc-fm-gap-spec.md`（6 条 gap 的 API 设计）
> **结论**: 14 项缺口中 **13 项可以在 FM 层面先做验证**，只有 1 项（性能 calibration）本质上需要 RTL 实测数据。

---

## 1. 分析方法

对 vplan 中 66 个 feature 逐条评估三个维度：

| 维度 | 含义 |
|------|------|
| **FM 可验证性** | 该 feature 的行为是否可以在纯 Python Func Model 中建模并断言 |
| **FM 已覆盖** | 当前是否已有 FM 层回归用例 |
| **RTL bug 预防价值** | 如果在 FM 层补这个验证，能多早暴露什么类问题（秒级 vs 7.5h） |

分类结果：
- **FM 已覆盖**（52 项）：已有 FM 回归，无需新增
- **FM 可新增验证**（13 项）：当前缺口但可以在 FM 层先做
- **FM 不可验证**（1 项）：本质上需要 RTL 硬件数据

---

## 2. 已覆盖的 52 项（无需新增 FM 验证）

| 类别 | Feature 数 | 说明 |
|------|:-----------:|------|
| MXU | 8 | FM 即 golden reference，$readmemh 验证的基础 |
| SFU | 7 | 同上 |
| Vector | 7 | 同上 |
| SoC 互联（已覆盖部分） | 12 | FM-SOC-001..032 回归 + 模块级 RTL |
| 固件/CPU（已覆盖部分） | 7 | `test_firmware.py` + 契约守卫 |
| E2E（已覆盖部分） | 3 | blk.0 smoke + 3-layer |
| 契约/门禁 | 8 | fm-hardening-phase10 全部 |

> 这 52 项的 FM 验证已经到位。以下只分析 14 项缺口。

---

## 3. 14 项缺口的 FM 可验证性逐条分析

### 3.1 可在 FM 层新增验证（13 项）

| # | vplan Feature ID | Feature | 当前状态 | FM 可验证？ | FM 层怎么做 | 能多早暴露什么 | 优先级 |
|---|------------------|---------|:--------:|:-----------:|-------------|---------------|:------:|
| 1 | SOC-13 | PCIe TLP 功能模型 | ❌ | ✅ | `docs/soc-fm-gap-spec.md` 已有完整 `PCIeModel` API 设计（TLP 解析/BAR 路由/MSI-X），实现后替换 `host_write_*` 直接写 DRAM | host↔NPU PCIe 数据通路错误（BAR 路由错、TLP 载荷截断）在 FM 秒级暴露，不用跑 RTL PCIe wrapper | **P0** |
| 2 | SOC-17 | IRQ 链路（engine→INTC→CPU WFI 唤醒） | ⚠️ | ✅ | 在 FM 中实现完整中断链：engine 完成 → INTC PENDING → CPU WFI 退出 → firmware 调度下一 op | 中断驱动调度错误（丢中断、WFI 不唤醒、优先级错）在 FM 秒级暴露 | **P0** |
| 3 | FW-10 | 中断驱动 firmware 调度 | ⚠️ | ✅ | 与 SOC-17 同一个 FM 模型：WFI 唤醒后 firmware 调度下一命令 | 同上 | **P0** |
| 4 | E2E-04 | 多层（≥9 层）full-model forward pass | ❌ | ✅ | fm-hardening todo 5 已做 11 层 208 命令长序列；扩展到 28 层 700+ 命令，断言每层 cos ≥ 0.99 | 多层累积偏移/环回绕/地址碰撞在 FM 秒级暴露（BUG-RTL-SOC-008 类） | **P0** |
| 5 | E2E-05 | MobileNetV3 全推理 | ❌ | ✅ | `sim/cv/` 已有 CV 模拟器、`cv_sim.py`、`cv_host_runner.py` 和 MobileNetV3 trace 基础设施；加一个 CV chain FM gate（im2col→GEMM→Pool 通路） | CV 通路布局/调度错误在 FM 秒级暴露 | **P1** |
| 6 | SOC-16 | Ibex→AXI 共享地址空间 | ⚠️ | ✅ | 修改 `RISCVMini` 使其与 FuncModel 共享 `self.sram`/`self.dram`（当前独立 `self.mem`），CPU 数据访问走统一地址空间 | CPU 数据访问地址错配在 FM 秒级暴露 | **P1** |
| 7 | SOC-18 | Ibex 固件 boot→DMEM→MMIO→poll IRQ 序列 | ⚠️ | ✅ | 在 FM 中实现 boot 序列模型：boot ROM → DMEM 初始化 → MMIO 配置 → doorbell poll → IRQ 响应，替代 NPUFirmware 直接绕过 | 固件控制流与 FM 不对齐在 FM 秒级暴露 | **P1** |
| 8 | SOC-14 | AXI Crossbar 仲裁行为模型 | ⚠️ | ✅ | 在 FM 中加 Python 级 crossbar 仲裁模型（round-robin 序 + 反压），MMIOBridge 路由先过仲裁再访问 SRAM/DRAM | 仲裁公平性/反压边界在 FM 秒级暴露 | **P2** |
| 9 | SOC-15 | APB-MMIO 统一寄存器模型 | ⚠️ | ✅ | 统一 per-engine `_handle_*` 为单一寄存器抽象，以 `regmap.py` 为唯一事实源 | 寄存器语义不一致在 FM 秒级暴露 | **P2** |
| 10 | FW-08 | Spike↔Ibex 固件行为对齐 | ⚠️ | ✅ | 跑 Spike 路径和 Ibex/NPUFirmware 路径同一组命令，交叉比对 ring 管理/调度结果 | 两条路径行为分歧在 FM 秒级暴露（fm-hardening AL1 deferred 项） | **P2** |
| 11 | FW-09 | `firmware_memory_contract.json` 双向比对 | ❌ | ✅ | FM 生成契约 JSON（ring base/size、descriptor range、completion range、max offset），RTL 段跑前比对 | 内存布局契约违反在 FM 秒级暴露（fm-hardening T1/T2 deferred 项） | **P2** |
| 12 | E2E-06 | Spike E2E forward pass tolerance | ⚠️ | ✅ | BUG-SOC-FM-005 是 FM 层 bug（数值 gap vs llama.cpp），可在 FM 层修复或正式 waiver | — | **P3** |
| 13 | E2E-08 | attn_weight RTL dispatch | ⚠️ | ✅（FM 已覆盖） | FM 侧已有 `test_mmul_attn_weight_shape`（fm-hardening todo 12）；**缺口在 RTL 侧**，不需要新增 FM 验证 | — | — |

### 3.2 不可在 FM 层验证（1 项）

| # | vplan Feature ID | Feature | 原因 |
|---|------------------|---------|------|
| 14 | E2E-07 | 性能 calibration | `calibration_state=uncalibrated`：FM 性能模型已有，但 calibration 本质是拿 RTL 实测 cycle 数校准 FM 公式，这是一个 RTL→FM 反向过程，不能在 FM 层独立完成 |

---

## 4. 优先级排序与依据

### P0 — Blocking Gap，FM 补了能最高价值地预防 RTL bug

| 项 | 依据 |
|----|------|
| SOC-13 PCIe TLP | `docs/soc-fm-gap-spec.md` 已有完整 API 设计；host↔NPU 唯一通路，TLP 错误目前完全不可见 |
| SOC-17 + FW-10 IRQ 链路 | 中断驱动是 firmware 调度的核心；当前 WFI 为 NOP，中断链完全无验证 |
| E2E-04 多层 full-model | fm-hardening todo 5 已做 11 层 208 命令；扩展到 28 层是增量工作，且 BUG-RTL-SOC-008 正是多层累积才暴露 |

### P1 — Blocking Gap，FM 补了能扩大覆盖面

| 项 | 依据 |
|----|------|
| E2E-05 MobileNetV3 | `sim/cv/` 基础设施已就绪；CV 是产品线之一，FM 层从未跑过 CV chain |
| SOC-16 Ibex-AXI 共享地址空间 | 修改 RISCVMini 共享 mem 是 SOC-17/FW-10 的前提（CPU 要能真正访问 SRAM/DRAM） |
| SOC-18 固件 boot 序列 | 与 SOC-16 配套；NPUFirmware 绕过 boot 是控制流不对齐的根因 |

### P2 — Non-blocking Gap，FM 补了能增强守卫

| 项 | 依据 |
|----|------|
| SOC-14 AXI 仲裁模型 | RTL stress 已验证无错，FM 模型是增强 |
| SOC-15 APB-MMIO 统一模型 | APB decoder 功能已验证，统一抽象是 FM 层改进 |
| FW-08 Spike↔Ibex 对齐 | 两条路径各自验证通过，交叉 gate 是增强 |
| FW-09 memory contract JSON | fm-hardening deferred 项，不阻塞但能预防布局 bug |

### P3 — 已有 FM 覆盖或属 FM bug

| 项 | 依据 |
|----|------|
| E2E-06 Spike tolerance | BUG-SOC-FM-005 是 FM bug，需修复或 waiver |
| E2E-08 attn_weight | FM 侧已覆盖（todo 12），缺口在 RTL 侧 |

---

## 5. FM 层新增验证的实施建议

### 5.1 工作量估算

| 优先级 | 项 | 预估工作量 | 依赖 |
|:------:|----|:----------:|------|
| P0 | SOC-13 PCIe TLP 模型 | Medium（API 设计已就绪，实现 `PCIeModel` + 5 个测试） | 无 |
| P0 | SOC-17 + FW-10 IRQ 链路模型 | Medium（实现 engine→INTC→WFI→调度 + 测试） | SOC-16（共享地址空间） |
| P0 | E2E-04 28 层 FM full-model | Small（todo 5 已做 11 层，扩展到 28 层 + cos 断言） | 无 |
| P1 | E2E-05 MobileNetV3 FM chain | Medium（`sim/cv/` 已有基础，加 CV chain gate） | 无 |
| P1 | SOC-16 Ibex-AXI 共享地址空间 | Medium（修改 RISCVMini mem 共享） | 无 |
| P1 | SOC-18 固件 boot 序列 | Medium（NPUFirmware 加 boot 模型） | SOC-16 |
| P2 | SOC-14 AXI 仲裁模型 | Small（Python 仲裁序 + 反压） | 无 |
| P2 | SOC-15 APB-MMIO 统一模型 | Medium（重构 per-engine handle） | 无 |
| P2 | FW-08 Spike↔Ibex 交叉 gate | Small（跑两条路径比对） | SOC-16/18 |
| P2 | FW-09 memory contract JSON | Small（FM 生成 JSON + 比对脚本） | 无 |

### 5.2 建议实施顺序

```
Wave 1（P0，最高 RTL bug 预防价值）:
  SOC-13 PCIe TLP 模型  ←  无依赖，可并行
  E2E-04 28 层 FM full-model  ←  无依赖，可并行
  SOC-16 Ibex-AXI 共享地址空间  ←  SOC-17 前置

Wave 2（P0 依赖 Wave 1）:
  SOC-17 + FW-10 IRQ 链路 + 中断驱动调度  ←  依赖 SOC-16

Wave 3（P1，扩大覆盖面）:
  E2E-05 MobileNetV3 FM chain  ←  无依赖，可与 Wave 2 并行
  SOC-18 固件 boot 序列  ←  依赖 SOC-16

Wave 4（P2，增强守卫）:
  SOC-14 AXI 仲裁模型
  SOC-15 APB-MMIO 统一模型
  FW-08 Spike↔Ibex 交叉 gate  ←  依赖 SOC-16/18
  FW-09 memory contract JSON
```

### 5.3 与 fm-hardening-phase10 的关系

fm-hardening-phase10 已落地的 8 项契约守卫（CON-01..08）是本分析的 **基础层**。本计划提出的新增 FM 验证是在契约守卫之上 **补齐 SoC 数据通路的 FM 模型**，两者关系：

| 层 | 内容 | 状态 |
|----|------|:----:|
| 契约守卫层 | 地址空间/环/packer/常量/段边界/门禁 | ✅ 已落地 |
| SoC 数据通路 FM 模型层 | PCIe TLP / IRQ 链 / Ibex 共享地址 / boot 序列 / 仲裁 / 统一寄存器 | ❌ 本计划提出 |
| E2E FM 场景层 | 28 层 full-model / MobileNetV3 chain | ⚠️ 部分有（todo 5 = 11 层） |

> 契约守卫层解决的是"布局/常量/packer 分歧"（已在 7.5h RTL 段跑中暴露过的 bug 类）。
> SoC 数据通路 FM 模型层解决的是"集成数据通路行为不可见"（当前 6 条 gap）。
> E2E FM 场景层解决的是"短 smoke 放过长累积 bug"（BUG-RTL-SOC-008 教训）。

---

## 6. 不可 FM 验证项的处置

| 项 | 处置 |
|----|------|
| E2E-07 性能 calibration | 保留为 RTL signoff 后的 post-silicon calibration 任务；FM 性能模型公式验证已有（perf spec signoff），calibration 本身需 RTL cycle 实测数据 |

---

## 7. 总结

| 指标 | 数值 |
|------|-----:|
| vplan Feature 总数 | 66 |
| FM 已覆盖 | 52 |
| FM 可新增验证 | 13 |
| FM 不可验证 | 1 |
| **FM 覆盖率提升潜力** | 79% → **98%**（52+13=65/66） |

### 13 项 FM 新增验证按 RTL bug 预防价值排序

1. **PCIe TLP 模型** — host↔NPU 唯一通路，当前完全不可见
2. **IRQ 链路 + 中断驱动调度** — firmware 调度核心，当前 WFI 为 NOP
3. **28 层 FM full-model** — BUG-RTL-SOC-008 教训，累积偏移才暴露
4. **MobileNetV3 FM chain** — CV 产品线，`sim/cv/` 已有基础
5. **Ibex-AXI 共享地址空间** — IRQ 链路前置依赖
6. **固件 boot 序列** — 控制流对齐
7. **AXI 仲裁模型** — 仲裁序/反压
8. **APB-MMIO 统一模型** — 寄存器单一事实源
9. **Spike↔Ibex 交叉 gate** — 两路径行为对齐
10. **memory contract JSON** — 布局契约双向比对
11. **Spike forward tolerance** — FM bug 修复
12. **attn_weight** — FM 已覆盖，缺口在 RTL
13. （E2E-07 性能 calibration 不可 FM 验证）

如果需要，我可以把这 13 项出一个正式的实施计划（todo 分解 + 验收标准 + 依赖矩阵），作为 `fm-soc-datapath-hardening` 的下一阶段计划。是否要我出？
