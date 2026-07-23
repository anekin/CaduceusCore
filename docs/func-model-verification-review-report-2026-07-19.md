# Caduceus Func Model 验证评审报告

> 评审日期：2026-07-19
>
> 评审对象：Caduceus Func Model 及其相关 Python/SoC/工作负载验证
>
> 评审性质：现状评审与改进建议，不启动新增验证执行
>
> 结论等级：**部分完备，可作为 LLM 主路径 RTL Golden Reference 与定向回归基线，暂不满足全产品 Func Model 验证签核条件**

## 1. 评审目标

本次评审关注以下问题：

1. 当前 Func Model 是否已经完整验证了 Caduceus 的功能行为；
2. Func Model 是否能够作为 RTL 功能验证的可信 Golden Reference；
3. 从 Python 功能模型、固件、ISA、SoC 数据路径、完整工作负载到性能模型之间是否形成闭环；
4. 当前验证证据是否可复现、可追踪并能够支持签核；
5. 后续应优先补齐哪些验证能力。

本报告只整理评审结果和后续建议。按照当前项目安排，建议项暂不执行；待当前验证阶段完成并复盘后，再结合实际缺陷、资源消耗和覆盖率调整实施范围及优先级。

## 2. 评审范围与依据

### 2.1 评审范围

- `sim/func_model.py`：Func Model 顶层组织与运行流程；
- `sim/miniv.py`：Python Firmware Model（当前代码中的 `NPUFirmware`）；
- `sim/engine/`：Golden Executor、ISA 和各计算引擎；
- `sim/models/`：PCIe、DMA、DRAM、SRAM、Crossbar、MXU、Vector 等模型；
- `sim/tests/`：算子、SoC、PCIe、双路径、CV 和 LLM 工作负载测试；
- `sim/timing/tests/`：Timing/Performance Model 测试；
- `sim/e2e_llamacpp.py`：与 llama.cpp 进行完整模型对比的入口；
- `rtl/testcase-list-soc-fm.md`、`sim/testplan.md`、`docs/verification_methodology.md`；
- `.omo/plans/phase6-rtl-verification.md`；
- `build/evidence/final-regression-summary.md` 等已有验证证据。

### 2.2 证据说明

现有最终回归摘要记录为：

- Python pytest：700 PASS、9 FAIL；
- FM-SOC：33/33 PASS；
- MXU：9/9 PASS；
- Vector：64/64 PASS；
- SFU：526/537，剩余问题主要涉及测试向量、容差或验证路径；
- Python 中的 9 个失败集中在 Engine/Performance Model 基线漂移。

本次评审以仓库源码、测试内容和已有回归证据为主要依据。当前桌面环境未能重新执行完整 pytest：系统 Python 不可用，工作区自带 Python 缺少 pytest。因此，本报告不将既有数字表述为本次新执行结果；这同时暴露了回归环境尚未完全一键复现的问题。

## 3. 总体结论

当前 Func Model 已经具备以下核心价值：

- 主要 LLM 算子具有较丰富的数值正确性测试；
- SoC 侧 PCIe、DMA、Descriptor、DRAM/SRAM 和双路径已有较系统的定向覆盖；
- Qwen Block0、28 Block 状态传播和反空洞检查可以发现常见的数据路径错误；
- Golden Executor 可以支撑 RTL 算子与 SoC 定向验证；
- 已有独立 llama.cpp 对比入口，可作为后续完整模型签核基础。

但当前验证尚未形成以下完整闭环：

```text
真实固件 ELF
    ↓
RISC-V/Spike 执行
    ↓
ISA 二进制编码与 MMIO/Command Ring
    ↓
PCIe/DMA/存储/调度
    ↓
真实尺寸、多 Tile、流式执行
    ↓
完整 LLM/CV 工作负载
    ↓
独立软件参考结果
    ↓
功能、性能、覆盖率和证据签核
```

因此，当前 Func Model 可以作为 **LLM 主路径的功能参考模型和 RTL 对照基线**，但不能据此宣布以下内容已经完备：

- 真实固件执行路径；
- 全 ISA 编解码路径；
- CV 端到端执行链；
- 真实尺寸完整模型；
- 多引擎竞争与 Weight Streaming 性能；
- 可量化的代码、功能和需求覆盖率；
- 完全可复现的自动化签核证据。

## 4. 分维度完备性评审

| 验证维度 | 当前状态 | 评审结论 | 主要缺口 |
| --- | --- | --- | --- |
| 算子数值正确性 | 较完善 | 可支持主路径 Golden Reference | 边界、随机和多 Oracle 覆盖仍可加强 |
| SoC 数据路径 | 较完善 | PCIe/DMA/Descriptor/存储已有较强定向验证 | 仍以模型内路径为主，缺少更多真实固件联动 |
| LLM Block 验证 | 较完善 | 适合状态传播和数据路径回归 | 存在维度截断、权重复用、单 Tile 等简化 |
| ISA 编解码 | 部分完善 | 执行器覆盖广于二进制编码覆盖 | 缺少全 opcode round-trip 和真实长度闭环 |
| 固件验证 | 不完善 | Python Firmware Model 为默认路径 | Spike/真实 ELF 未进入必跑回归 |
| CV 工作负载 | 不完善 | 当前更接近独立 Conv/MXU 数值测试 | 未经过完整 Func Model、固件、DMA、tiling 和跨层链路 |
| 完整模型 E2E | 部分完善 | 已有 llama.cpp 对比工具基础 | 尚未成为稳定、可复现的回归门禁 |
| 性能验证 | 不完善 | 已有公式和结构性测试 | 跨引擎竞争、流式加载、重叠执行和 RTL 校准未闭环 |
| 覆盖率与追踪 | 不完善 | 有 testcase 文档和反空洞测试 | 缺少代码覆盖、功能覆盖、需求追踪和自动汇总 |
| 回归可复现性 | 不完善 | 已有历史 evidence | 环境、依赖、模型文件和统计口径未完全固化 |

## 5. 主要发现

### 5.1 默认验证路径使用 Python Firmware Model

当前 `FuncModel` 默认使用 `NPUFirmware`。它不是虚假的数值模型，而是在 Python 中模拟固件行为，负责生成和提交 NPU 命令、Descriptor、地址与同步操作。只有显式启用 `CADUCEUS_USE_SPIKE` 且准备好相应产物时，才进入 Spike Firmware 路径。

两类路径的职责如下：

```text
快速路径：Python Test → Python Firmware Model → Func Model → Golden Engines
真实路径：Firmware ELF → Spike/RISC-V → MMIO/Command Ring → Func Model → Golden Engines
```

Python Firmware Model 适合快速回归、异常注入和边界场景构造，但无法单独证明：

- 真实固件能正确生成 Command 和 Descriptor；
- C/C++ 与 Python 数据结构布局一致；
- 对齐、字段偏移、大小端和指针宽度正确；
- Ring Buffer 指针、Doorbell 和内存屏障顺序正确；
- 固件编译后的 ISA 二进制能够表达测试中直接构造的操作。

为避免“Mock Firmware”产生歧义，后续文档建议统一使用 **Python Firmware Model** 或 **Firmware Behavioral Model**。

### 5.2 ISA 执行覆盖大于 ISA 编码覆盖

Golden Executor 已处理 MMUL、Softmax、LayerNorm、GELU、ReLU、RoPE、SiLU、Pooling、RMSNorm、DMA、Vector、KV Cache、Barrier 等操作，算子执行能力较完整。

但部分通用 SFU/Vector 指令的长度字段在编码路径中只能表达较小范围，而真实工作负载使用的长度可能为 64、128、2560 或更大。当前部分测试通过直接构造 Python Instruction 绕过二进制编码，因此可以证明 Executor 能执行，却不能证明固件能够正确编码并提交同一操作。

这是 Func Model 作为“真实指令 Golden Reference”之前必须关闭的合同缺口。

### 5.3 CV 验证尚未经过完整 Func Model 链路

当前 MobileNetV3 测试的主要方式是：

- 加载 TorchVision 预训练模型；
- 在测试文件中实现 `im2col_conv2d`；
- 直接调用 `GoldenMXU`；
- 将单个 Conv2D 层输出与 PyTorch 对比。

该测试能够证明部分 Conv2D 到矩阵乘的数值映射正确，但未覆盖：

- Func Model 顶层；
- Firmware Command/Descriptor；
- PCIe 与 DMA；
- 生产路径中的 im2col、tiling 和调度；
- 多层间数据布局与量化转换；
- 完整 MobileNetV3 输出或 Top-1 一致性。

此外，使用 `weights="DEFAULT"` 可能触发在线下载，且当前 PASS 判据只要求部分层达到阈值，尚不适合作为 CV 完整链路签核依据。

### 5.4 LLM 多层测试仍保留功能简化

现有 Block0 和 28 Block 测试已经覆盖了地址隔离、状态传播、命令链、反空洞和部分双路径行为，具有较高回归价值。

但部分测试仍采用：

- 将 MMUL 维度截断到较小规模；
- 跨层复用或缩放 Block0 权重；
- 为提高速度直接写 DRAM；
- 单 Tile 执行，未覆盖真实 Weight Streaming；
- 合成数据代替完整真实模型权重。

因此其结论应限定为“多层控制流和状态传播正确”，不能等同于“完整 Qwen 模型数值签核”。

仓库中的 `sim/e2e_llamacpp.py` 已提供与 llama.cpp 逐层对比的基础，但依赖本地模型和外部产物，尚未纳入稳定的自动化回归。

### 5.5 性能模型尚未形成独立校准与验收闭环

当前 Func Crossbar 在功能模型中会立即批准请求，不模拟周期级争用。Timing Model 虽然提供 NoC、双缓冲和引擎延迟抽象，但现有测试更多验证：

- 计算公式可执行；
- 字段和序列化结果存在；
- 结果不是 NaN；
- 总时间汇总逻辑正确。

仍需补齐：

- 跨引擎 SRAM/NoC 竞争；
- Weight Streaming stall；
- PCIe/DMA 与计算重叠；
- 不同 Tile 和队列深度下的背压；
- 使用独立 workload 验证校准参数，避免同一批 RTL 数据既用于拟合又用于验收。

现有 9 个 Engine/Performance Model 漂移失败不宜长期用“允许一定数量失败”的方式处理。功能门禁和性能漂移门禁应独立统计，已知偏差应关联明确 bug、负责人、基线和退出条件。

### 5.6 验证证据与文档存在口径漂移

仓库不同文档中存在 109、210、617、700 等不同测试数量或阶段统计。部分 testcase 表格、方法论文档和 issue 状态未随实际回归自动更新。

这类问题不会直接造成设计错误，但可能导致：

- 将历史 PASS 误认为当前 PASS；
- 将已替代用例误认为仍未覆盖；
- 只统计命令成功而未统计真实断言结果；
- 评审时无法追溯测试版本、模型版本和输入数据哈希。

建议以机器生成的 JUnit/JSON Manifest 作为唯一事实来源，Markdown 只展示由该 Manifest 汇总出的结果。

## 6. 建议改进项

### 6.1 P0：Func Model 签核前必须关闭

| 编号 | 改进项 | 建议验收标准 |
| --- | --- | --- |
| FM-P0-01 | 建立 Python Firmware Model 与 Spike Firmware 双回归 | PR 必跑快速路径；每日/里程碑必跑真实 ELF 路径；关键用例结果一致 |
| FM-P0-02 | 建立全 opcode ISA encode/decode/execute 闭环 | 每个 opcode、字段边界、保留位和非法编码均有自动化测试 |
| FM-P0-03 | 关闭 ISA 长度字段合同问题 | 固件、编码器、解码器、Func Model 与 RTL 使用同一规格，真实长度不再绕过编码 |
| FM-P0-04 | 固化验证环境 | 一条命令可安装/启动；Python 和依赖版本锁定；模型与向量带哈希；禁止隐式在线下载 |
| FM-P0-05 | 分离功能与性能门禁 | 功能回归零非预期失败；性能漂移单独报告，不使用笼统失败额度 |
| FM-P0-06 | 建立统一证据源 | 自动生成 testcase、版本、随机种子、输入哈希、结果和覆盖率 Manifest |

### 6.2 P1：补齐产品级工作负载

| 编号 | 改进项 | 建议验收标准 |
| --- | --- | --- |
| FM-P1-01 | 将 im2col、tiling 和 CV 调度移入生产路径 | 测试不再在 testcase 内实现关键算法，经过完整 Func Model 命令链 |
| FM-P1-02 | 建立完整 CV 代表性回归 | 至少一个 MobileNetV3 完整子网/全网，以及一个含 Attention 的 CV Block |
| FM-P1-03 | 建立真实尺寸 Qwen Block 回归 | 不截断关键矩阵维度，使用真实布局和 Weight Streaming |
| FM-P1-04 | 产品化 llama.cpp E2E 对比 | 模型、量化格式、工具版本固定，可自动生成逐层误差证据 |
| FM-P1-05 | 配置驱动资源边界 | SRAM、Ring Size、队列深度和最大命令数来自同一配置源，并验证越界行为 |
| FM-P1-06 | 完成跨引擎性能验证 | 覆盖资源竞争、流水重叠、背压和流式加载，与 RTL held-out workload 对比 |

### 6.3 P2：建立长期质量闭环

- 增加 Python line/branch coverage，并设置逐阶段而非一次性覆盖率门槛；
- 建立 Requirement → Feature → Testcase → Evidence 追踪矩阵；
- 增加 property-based、metamorphic 和随机指令序列测试；
- 对关键 Golden 算子进行 mutation/反空洞验证；
- 组合使用 NumPy、PyTorch、ONNX Runtime、llama.cpp 等独立 Oracle；
- 对失败进行自动聚类和最小化，沉淀为可重复的 regression seed；
- 对覆盖率、性能误差和缺陷逃逸率建立趋势看板。

## 7. 建议的分层回归结构

| 回归层级 | 主要内容 | 建议频率 | 目标 |
| --- | --- | --- | --- |
| L0 静态合同 | ISA、结构体布局、配置一致性 | 每次提交 | 快速发现接口漂移 |
| L1 算子级 | Golden Engine 数值、边界、随机测试 | 每次提交 | 保证基础数值正确性 |
| L2 Python Firmware Model | Command、DMA、存储、Block 快速回归 | 每次提交 | 高覆盖、快速定位 |
| L3 Spike Firmware | 真实 ELF、MMIO、Ring、Descriptor | 每日/关键提交 | 关闭固件与模型接口风险 |
| L4 工作负载 | 真实尺寸 LLM/CV Block 和完整模型 | 每日/里程碑 | 产品级功能签核 |
| L5 性能模型 | 引擎、NoC、DMA、Streaming、重叠 | 里程碑 | 与 RTL 形成独立校准和验收闭环 |
| L6 RTL 对照 | Func/Timing Model 与 RTL 逐层/逐事务对比 | 里程碑/发布 | 支撑 RTL 签核与模型可信度评估 |

## 8. 建议签核条件

在宣布“Func Model 验证完备”前，建议至少满足以下条件：

1. 所有支持的 opcode 均通过 ISA 二进制 round-trip 和执行验证；
2. Python Firmware Model 与 Spike Firmware 的关键回归结果一致；
3. 功能回归不存在未归档、未解释的失败；
4. 至少一个真实尺寸 LLM Block、一个完整 LLM 路径和一个 CV 代表性链路通过独立 Oracle 对比；
5. PCIe、DMA、存储边界、Ring Wrap、错误响应和多命令并发均有自动化证据；
6. 多 Tile、Weight Streaming、资源竞争和流水重叠完成 RTL 对比；
7. 需求、用例、结果、覆盖率和模型/数据版本能够双向追踪；
8. 回归环境能够在干净环境中通过单一入口复现；
9. 所有豁免项均有明确范围、风险、负责人和关闭日期；
10. 最终评审使用自动生成的证据包，不依赖手工维护的测试数量或 PASS 结论。

## 9. 与当前验证方案的关系

当前验证方案已经覆盖了大量 P0-P4 定向用例，并通过 FM-SOC、MXU、Vector、SFU 和 Python 回归积累了较好的基础证据。现阶段不建议立即改变正在执行的验证任务，以免打断当前阶段的基线和复盘输入。

建议在当前验证执行完成后，按以下顺序复盘：

1. 汇总真实发现的设计缺陷、模型缺陷、测试缺陷和环境缺陷；
2. 将缺陷映射到本报告的 P0/P1/P2 改进项；
3. 删除未被证据支持或收益不足的建议；
4. 调整回归频率、资源预算和签核阈值；
5. 将确认后的内容合并到 `agentic-ic-verification-plan`，形成可执行阶段计划。

## 10. 最终评审意见

当前 Func Model 的功能验证基础较扎实，尤其适合 LLM 主路径算子、Block、SoC 数据路径和 RTL 定向对照。其主要风险已经不再是“缺少基础测试”，而是测试层级之间尚未完全贯通：Python 构造的指令和真实固件之间、独立算子和完整 CV 工作负载之间、缩小规模的 Block 和真实尺寸模型之间、功能结果和周期级性能之间仍有断点。

因此本次评审意见为：

> **维持 Func Model 作为当前 RTL Golden Reference 和快速功能回归基线；暂不标记为全工作负载、真实固件与性能联合签核完成。待当前验证阶段结束后，以实际复盘证据为输入，优先关闭 P0 合同与可复现性问题，再决定 P1/P2 的实施范围。**
