# Bug Tracking — PCIe DMA

> **阶段**: Wave 0 PCIe DMA 实现
> **关联 plan**: `.omo/plans/pcie-dma-implementation.md`

## 已知未覆盖

以下条目为 Phase 4 功能性验证（FM-SOC-001~032）已确认的 **测试覆盖空白**，属于已知但尚未实现验证的 PCIe DMA 功能。这些条目在 Wave 0 不作为 bug 追踪，而是作为后续波次的覆盖目标。

---

### UCOV-PCIE-001 — Completion Timeout Recovery 未覆盖

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-06 |
| **Block** | W0 |
| **Case** | FM-SOC-001~032 |
| **Severity** | Minor (验证缺口) |
| **Type** | Coverage |
| **Status** | Uncovered |

#### 描述

PCIe 协议规定 Requester 在发出 Memory Read Request 后，若 Completer 在超时窗口内未返回 Completion，Requester 应触发 Completion Timeout 机制并可选重试或报告错误（PCIe Base Spec r5.0 §2.3.2）。当前 PCIe EP wrapper （`pcie_ep_wrapper.v`）及验证环境未实现以下场景：

- NPU 作为 Requester 发起读请求后，RC 不返回 Completion
- Completion Timeout 定时器溢出后的行为（重试 / Abort / Error 上报）
- Timeout 恢复后链路是否正常工作

#### 原因

当前所有 SoC RTL cases（FM-SOC-001~032）均依赖 RC 及时返回 Completion。仿真中没有注入 completion timeout 的测试用例。`verilog-pcie` 的 `pcie_axi_master` 模块包含 Completion Timeout Counter（`COMPLETION_TIMEOUT` 参数），但 wrapper 层未向外暴露 timeout 中断或状态信号。

#### 后续计划

Wave 1 或 2 中增加以下验证：
- Testbench-level completion timeout injection（在 RC model 中延迟/丢弃特定 Completion）
- NPU firmware 侧的 timeout 处理循环
- 验证超时后链路恢复（retry 或 reset sequence）

---

### UCOV-PCIE-002 — 多 Function RC Model 未覆盖

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-06 |
| **Block** | W0 |
| **Case** | FM-SOC-001~032 |
| **Severity** | Minor (验证缺口) |
| **Type** | Coverage |
| **Status** | Uncovered |

#### 描述

PCIe 协议支持 Multi-Function 设备，即一个物理设备（Device）下包含最多 8 个独立 Function。当 NPU 作为 EP 时，若接入的 RC 暴露多个 Function（如 RC 自身 + 多个 Root Port），NPU 的 TLP 路由逻辑可能面临 Function ID 匹配问题。当前验证环境使用单一 Function RC model，未验证以下场景：

- RC 发送 TLP 时使用非零 Function Number
- NPU 作为 EP 需响应/忽略不同 Function ID 的配置请求
- Multi-Function 地址空间隔离

#### 原因

验证环境中的 `pcie_rc_bfm` 仅实现了简单的单 Function 行为模型。RTL 侧的 `pcie_ep_wrapper` 及 `verilog-pcie` 的 `pcie_axi_master` 未做 Multi-Function 适配。

#### 后续计划

Wave 2 中引入 Multi-Function RC Model 或使用 Synopsys SVT PCIe VIP 进行 Multi-Function 验证。

---

### UCOV-PCIE-003 — AER Error Reporting 未覆盖

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-06 |
| **Block** | W0 |
| **Case** | FM-SOC-001~032 |
| **Severity** | Minor (验证缺口) |
| **Type** | Coverage |
| **Status** | Uncovered |

#### 描述

PCIe Advanced Error Reporting（AER）是 PCIe Spec r5.0 §6.2 定义的增强错误报告机制，包括 Uncorrectable Error（UC）和 Correctable Error（CE）两大类。当前 NPU EP 的 PCIe 验证覆盖了以下错误注入场景：

- **覆盖**: 无（当前所有 case 带宽成功路径）
- **未覆盖**:
  - Correctable Error 注入（如 ECRC 错误、Replay Timeout）
  - Uncorrectable Non-Fatal Error（如 Malformed TLP、Completion Timeout -> UCOV-PCIE-001）
  - Uncorrectable Fatal Error（如 Poisoned TLP、Unexpected Completion）
  - AER Capability Structure 的配置空间访问验证
  - Error 上报后 driver 侧的恢复流程

#### 原因

AER 需要 RC 侧和 EP 侧同时支持 AER Capability。当前 `verilog-pcie` 的 `pcie_axi_master` 未实现 AER Capability Structure，验证环境的 RC BFM 也未实现错误注入接口。

#### 后续计划

Wave 2 中增加以下内容：
- 在 RC BFM 中增加 TLP 错误注入机制（反压、CRC 错误、Malformed TLP）
- 如需完整的 AER 验证，切换到 Synopsys DWC PCIe EP + SVT PCIe VIP

---

### UCOV-PCIE-004 — AXI DECERR 响应未覆盖

| 字段 | 内容 |
|------|------|
| **Date** | 2026-07-06 |
| **Block** | W0 |
| **Case** | FM-SOC-001~032 |
| **Severity** | Minor (验证缺口) |
| **Type** | Coverage |
| **Status** | Uncovered |

#### 描述

在 AXI4 协议中，Slave 在遇到无法处理的地址或传输时，通过 `RRESP[1:0]` / `BRESP[1:0]` 返回 DECERR（Decode Error，2'b11）。当前 PCIe EP wrapper 在 AXI 总线上作为 Master 时，收到 DECERR 响应的行为未被验证：
- NPU 内部 Master（MXU/SFU/Vector/DMA）发送读请求到 PCIe EP，PCIe EP 返回 DECERR
- NPU AXI Crossbar 的 Slave 端口（SRAM/DRAM）返回 DECERR 时 PCIe EP 的处理
- DECERR 上报给 firmware 的方式（中断 / STATUS 寄存器）
- DECERR 后的错误恢复流程

#### 原因

当前验证环境中 AXI 总线上的所有传输都是合法地址，不会触发 DECERR。未实现以下注入机制：
- Crossbar 或 memory controller 在特定地址范围返回 DECERR
- PCIe EP 在收到非法 TLP 时返回 AXI DECERR
- Firmware 侧的 DECERR 中断处理程序

#### 后续计划

Wave 1 中增加以下内容：
- 在验证环境中增加 AXI DECERR 注入 BFM
- 验证 EP wrapper 对 RRESP/BRESP 非 OKAY 响应的处理
- 确认 firmware 是否需要对 DECERR 做出响应（optional: 添加到 npu_wait_done 的错误路径）

## 覆盖范围说明

### 已覆盖（Wave 0）

| 编号 | 范围 | 状态 |
|------|------|------|
| COV-001 | PCIe EP 作为 AXI Master，读写 SRAM/DRAM | ✅ |
| COV-002 | DMA engine 通过 PCIe EP 完成 Host↔NPU 数据传输 | ✅ |
| COV-003 | PCIe MMIO 寄存器读写（配置空间 + BAR0） | ✅ |
| COV-004 | PCIe interrupt（MSI/MSI-X）发送 | ✅ |
| COV-005 | PCIe link training and initialization（BFM 级） | ✅ |

### 未覆盖总览

| 编号 | 主题 | 严重性 | 状态 |
|------|------|:------:|:----:|
| UCOV-PCIE-001 | Completion Timeout Recovery | Minor | Uncovered |
| UCOV-PCIE-002 | 多 Function RC Model | Minor | Uncovered |
| UCOV-PCIE-003 | AER Error Reporting | Minor | Uncovered |
| UCOV-PCIE-004 | AXI DECERR 响应 | Minor | Uncovered |

---

*Created 2026-07-06, Wave 0 of pcie-dma-implementation plan.*
