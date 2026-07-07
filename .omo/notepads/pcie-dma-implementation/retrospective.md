# pcie-dma-implementation 项目复盘

## 项目概述

在 CaduceusCore SoC 中集成 NPU 自治 PCIe DMA 引擎，使 NPU 可以通过 APB 寄存器描述符接口发起 PCIe Memory Read/Write 事务，实现 host→NPU 和 NPU→host 的自主数据传输。核心约束：不修改 vendored `rtl/ip/verilog-pcie/` 目录下的任何 RTL 源文件。

## 时间分布

Boulder 追踪的总耗时为 **11h 53m 28s**。各任务耗时如下（按降序排列）：

| 任务 | 耗时 | 占比 |
|------|------|------|
| R5 Review Gate (Wave 5 审计) | 3h 17m 4s | 27.6% |
| T2.1 `pcie_dma_wrapper.v` 实现 | 1h 20m 25s | 11.3% |
| T4.1 `npu-regmap.h` PCIe DMA 寄存器映射 | 1h 13m 45s | 10.4% |
| T6.1 Full SoC Regression | 38m 27s | 5.4% |
| T3.1 `axi_crossbar.v` NUM_M 6→7 | 17m 57s | 2.5% |
| F1 Plan Compliance 审查 | 12m 48s | 1.8% |

最耗时的三个环节：

1. **R5 Review Gate（3h 17m）**：价值最高的一笔时间开销 — Atlas 发现了 TC-SOC1 缺少 CplD 接口 dual-compare，以及 Makefile 假阳性结果检查问题。Review gate 在质量保障上的投入是合理的。

2. **T2.1 pcie_dma_wrapper.v（1h 20m）**：作为核心 RTL 模块，涉及 dma_if_pcie/dma_if_axi 的配适、APB 寄存器 FSM、2-phase 描述符状态机、RAM cross-connect，其复杂度决定了编码时间。

3. **T4.1 npu-regmap.h（1h 13m）**：Register map struct 定义和位域宏看似简单，但需要在 C struct packing 约束下与 RTL 寄存器布局保持完全一致，且需满足 plan C6 的 ≤32 字节验收条件。

## 核心成果

| 测试套件 | 结果 | 证据 |
|---------|------|------|
| Func Model pytest (`test_pcie_dma_fm.py`) | **8/8 PASS** | `.omo/evidence/fm_pcie_dma.log` |
| Standalone RTL TB (`pcie_dma_tb.sv`) | **5/5 PASS** | `.omo/evidence/pcie_dma_tb.log` |
| FM-SOC SoC regression (全量回归) | **33/33 PASS** | `.omo/evidence/soc_regression.log` |
| cocotb E2E (`test_soc_pcie_dma.py`) | **6/6 PASS** | `.omo/evidence/cocotb_e2e.log` |
| Vendored file gate (`rtl/ip/verilog-pcie/`) | **clean** | `git diff` 无匹配 |
| Final Wave F1-F4 | **全部 APPROVE** | `.omo/evidence/f1_plan_compliance.txt` |

Plan Compliance 全部 7 项成功标准均已达成：

1. Func Model DmaEngine 7/7 pytest PASS
2. Standalone RTL DMA 5/5 VCS PASS
3. Full SoC elaboration 0 errors, 0 undriven
4. 33 existing FM-SOC regression tests still PASS
5. Firmware builds + dispatches OP_PCIE_DMA correctly
6. 6/6 cocotb E2E tests PASS
7. No vendored verilog-pcie files modified

## 关键问题与修复

### 1. Wave 5 CplD Header 格式问题（STATUS 卡在 rd_busy）

**现象**：TC1（pcie_dma_read）等所有读路径测试失败，STATUS 寄存器一直为 `0x00000001`（rd_busy=1），`dma_if_pcie_rd` 的 CplD 响应没有到达。

**根因**：`cocotb_bridge.py` 的 `send_cpl_for_mrd()` 在 CplD header 中 RequesterID 取了 DW0 的内容而非 DW1 中正确的字段位置。TLP header 的位域布局必须严格对应 `dma_if_pcie_rd.v:971-992` 中的 RTL parser 实现。

**修复**：修正 RequesterID 从 header[95:80]（DW1 高 16 位）、Tag 从 header[79:72]（DW1 中 8 位）提取。三个字段位置全部对照 RTL 源文件校准。

### 2. Makefile 假阳性问题

**现象**：T5.2 QA 初期 6 个 cocotb 测试实际只有 TC2 通过（1/6 PASS），但 Makefile 报告 "all 6/6 tests passed"。

**根因**：Makefile 使用 VCS 的 `PIPESTATUS[0]` 判断结果。VCS 模拟器无论 cocotb 测试成功与否都返回 exit code 0。`grep` 的 log 检查也容易被日志文本混淆。

**修复**：切换到 cocotb JUnit XML 结果检查 — 检查 XML 中 `<testcase>` 元素是否存在，以及 `<failure>` / `<error>` 子元素是否为零。这是 cocotb 官方提供的结果格式，可靠无误。

### 3. TC3 Race Condition

**现象**：TC3（concurrent_bridge_dma）偶发超时，MRd 没有被 `receive_pcie_tlp()` 捕获。

**根因**：`tb_soc.v` 在仿真开始时将 `pcie_dma_tx_rd_req_tlp_ready` 初始化为 `1'b1`。DMA 引擎在 `START_RD` 之后的下一个时钟周期就可能发出 MRd。TC3 在启动 MRd 之后才调用 `receive_pcie_tlp()`，但中间插入了 128 字节的 `_sram_backdoor_write()` 推进了时钟周期，MRd 的 valid 脉冲已经过去了。

**修复**：用 `cocotb.start_soon()` 将 `receive_pcie_tlp()` 作为后台任务提前启动，在 SRAM backdoor write 之前就开始监控 TLP 端口。后台任务捕获 MRd 的同时，SRAM 写入可以并发执行。

### 4. TC-SOC1 Dual-Compare 缺失（R5 Finding 1）

**现象**：R5 审计发现 TC-SOC1（pcie_dma_read）只有 backdoor SRAM 回读验证，缺少独立的 PCIe TLP 接口级 CplD 载荷核对。

**修复**（Wave 6）：添加 `_monitor_cpld_payload()` 协程，在 `send_cpl_for_mrd()` 之前通过 `cocotb.start_soon()` 启动，捕获 `pcie_dma_rx_cpl_tlp_data` 的 512-bit 数据 beat。验证日志变更为 `backdoor=True, cpld_interface=True`。

### 5. F1 发现的 C1 计划偏差

**现象**：F1 首次审查发现 C1 要求实现 `pcie_tlp_mux` / `pcie_tlp_demux` 实例来合并桥接 TLP 和 DMA TLP，但实际 RTL 采用了分立的 TLP 端口组方案。

**处理**：修订计划 — C1 改名为 "TLP Porting"，明确文档化 D2 方案（SoC 边界上暴露两组独立 TLP 端口），将 mux/demux 推迟到未来需要单外部 TLP 链路时实现。修订后重新审查 APPROVE。

## 经验与教训

1. **TLP header 字段位置必须以 RTL parser 为准**。CplD header 中的 RequesterID、Tag、ByteCount 等字段的位置，正确参考是 `dma_if_pcie_rd.v:971-992`，而不是 PCIe spec 的通用描述或 Func Model 中的假设。每次 header 操作都要对照 RTL parser 的位提取代码。

2. **cocotb 结果检查必须用 cocotb JUnit XML，不能用 simulator exit code**。VCS 的 exit code 不代表 cocotb 测试通过与否。XML 中的 `<failure>` 元素是唯一可靠的判定依据。

3. **cocotb 并发监控必须用 background task（`cocotb.start_soon`）**，不能等操作完成后再启动监控。RTL 在 `ready=1` 时可以零周期发出 TLP。正确模式：先 `start_soon(monitor())`，再执行操作，最后 `await monitor` 收结果。

4. **Review gate 的价值不可替代**。R5（3h 17m）是单次耗时最长的任务，但它的投入直接发现了 dual-compare 缺失和假阳性检查两个关键问题。没有 review gate，TC-SOC1 的验证盲区会进入最终签收。

5. **计划与实现不一致时要及时修订计划或返工**。F1 发现 C1 偏差后，选择了计划修订方案 — 明确了架构决策、记录了 defer 原因、更新了验收标准。修订后的计划与实际实现一致，避免了 "计划归计划、实现归实现" 的脱节。

6. **Func Model-first 方法有效降低了 RTL debug 成本**。先实现 `DmaEngine` 的 7 个 pytest 用例覆盖了 TLP header 构造、tag 生命周期、max payload 分片、error 传播等核心行为。RTL 调试遇到的 CplD header 问题在 Func Model 阶段已经验证过正确的字段位置，side-by-side 对比加速了根因定位。

7. **小细节决定接口兼容性**。APB decoder 从 7 到 8 个 slave 的扩展看似一个参数修改，实际需要同步修改 `axi_crossbar.v`、`caduceus_soc_top.v`、`tb_soc.v`、`interconnect.yaml`、`intc_top.v` 等 10+ 个文件的数组宽度、testbench 断言、固件地址映射。任何一处遗漏都会导致仿真失败。

## 后续建议

1. **TLP mux/demux 集成**：当前 SoC 边界上暴露了两组独立的 TLP 端口（桥接 TLP 和 DMA TLP），在 `pcie_ep_wrapper.v` 内实现 `pcie_tlp_mux` / `pcie_tlp_demux`，将两组端口合并为单一外部 TLP 链路。这需要在 cocotb host model 中增加 combined TLP stream 的分离逻辑。

2. **多 beat CplD 测试扩展**：当前 cocotb E2E 测试的载荷都小于等于 64 字节（单 512-bit beat）。多 beat CplD（如 MPS=256 下的 64+ 字节完成）的 split-completion 处理已在 Func Model T1.2 中验证，但 cocotb 级缺少对应的 E2E 测试。建议增加 TC-SOC7 覆盖多 beat CplD 路径。

3. **WFI 下的 firmware doorbell 路径**：当前 cocotb 测试使用直接 APB 编程绕过了 firmware doorbell 路径（因 Ibex 在 WFI 状态可能阻塞）。如果 SoC 启用了 timer interrupt 或 doorbell IRQ 唤醒机制，应补充 true doorbell E2E 测试，验证 `OP_PCIE_DMA` 从 doorbell ring → Ibex 中断 → dispatch → completion 的完整链路。
