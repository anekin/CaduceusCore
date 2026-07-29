# Func Model SoC Golden Reference 与端到端软件栈评审报告

## 1. 评审信息

| 项目 | 内容 |
|---|---|
| 评审日期 | 2026-07-28 |
| 工作分支 | `feat_sw_stack` |
| 评审基线 | `9b4ae44`，与评审时 `origin/main` 一致 |
| 评审范围一 | Func Model 作为 SoC RTL golden reference 的验证充分性 |
| 评审范围二 | 主控 CPU 侧端到端软件栈在 Func Model 上的可运行性 |
| 不在本次结论内 | RTL 实现正确性、RTL 性能 signoff、FPGA 实机 signoff |

本报告基于当前仓库代码、测试、CI 配置和已提交 evidence 进行评审。结论强调可复现的执行路径，不仅依据 checklist 中的 PASS 标记。

## 2. 执行摘要

### 2.1 总体结论

| 评审维度 | 当前结论 | 说明 |
|---|---|---|
| NPU engine 数值 golden | **PASS，范围受限** | INT4 MMUL、SFU、Vector、full-shape Qwen2.5-3B blk.0 的 direct/tiled/connected 数值验证较完整，可以作为 NPU 计算单元的数值参考 |
| SoC Func Model 作为 SoC RTL golden reference | **PARTIAL，暂不可 signoff** | PCIe、Crossbar、DMA、Doorbell、INTC、RISC-V/firmware 等模型已经存在，也有较多分项测试；但还缺少一条由真实 SoC 前门贯穿所有关键部件、可同时重放到 RTL 的统一场景 |
| Host Runtime/协议基础 | **PARTIAL** | ABI schema、生成器、对象生命周期、buffer/fence/protocol 基础已经建立 |
| 软件栈在 Func Model 上端到端运行 | **FAIL，尚未达到 signoff** | 当前 llama.cpp 和 ExecuTorch backend 最终仍由 CPU 完成计算；Host Runtime 只能提交 NOP，FM transport 未转发真实命令 payload |
| 同一软件栈迁移到 RTL/FPGA | **BLOCKED** | `rtl://`/`fpga://` 的真实 transport 和硬件可重放路径尚未闭合；当前 `fpga://` 仍会落到 mock 行为 |

### 2.2 核心判断

当前 Func Model 已经具备较强的“计算功能模型”能力，但“计算 golden”不等于“整个 SoC RTL golden”。

如果 Func Model 要作为 SoC RTL 的 golden reference，比较对象不能只包括最终 tensor 数值，还必须包括软件可见和架构可见行为：

- PCIe/BAR/TLP 和 DMA 行为；
- Crossbar 地址译码、访问权限和并发次序；
- command ring、descriptor、doorbell 和 completion；
- firmware 对 descriptor 的解释和 engine 调度；
- status/error/interrupt 的产生、屏蔽、ACK 和清除；
- reset、超时、非法请求和 in-flight 操作的确定性结果。

软件栈方面，NPU backend 的控制逻辑本来就应该运行在主控 CPU 上，这是正确架构；但被支持的计算图必须通过 runtime/transport/firmware 真正交给 NPU Func Model 执行。当前实现只完成了 backend 在 host CPU 上运行，尚未完成“计算被 offload 到 Func Model”。

## 3. Signoff 边界定义

为避免把不同层次的 PASS 混在一起，本评审将 signoff 分为四层。

| 层次 | 验证对象 | 当前状态 |
|---|---|---|
| G0：算子数值 golden | MXU/SFU/Vector/DMA 的输入输出语义 | PASS |
| G1：NPU 子系统 golden | descriptor、tiling、engine chain、局部 firmware 调度 | 大部分 PASS |
| G2：SoC 集成 golden | Host 前门到 completion/IRQ/readback 的完整架构行为 | PARTIAL |
| G3：软件产品路径 | llama.cpp/ExecuTorch 经 Host Runtime 在 FM 上真实执行模型 | FAIL |

只有 G2 关闭后，才能宣称 Func Model 可作为整个 SoC RTL 的 signoff-grade golden reference。只有 G3 关闭后，才能宣称端到端软件栈已经在 Func Model 上运行。

## 4. 方向一：SoC RTL Golden Reference 评审

### 4.1 当前已经具备的能力

#### 4.1.1 NPU 数值参考较完整

`docs/func-model-signoff-checklist.md` 中 F-FM-01 至 F-FM-17 覆盖了：

- INT4 MMUL bit-exact reference；
- SFU FP16 tolerance comparison；
- Vector INT32 bit-exact reference；
- synthetic manifest、真实 GGUF provenance；
- full-shape Qwen2.5-3B blk.0 direct-MMIO；
- tiled scheduler；
- connected blk.0 dual-oracle；
- corruption、descriptor 和 boundary 测试。

这部分可以作为 RTL engine 和 NPU 子系统数值对拍的基础。尤其是 full-shape Qwen blk.0 已不再只是 scaled/single-tile smoke，验证价值明确。

#### 4.1.2 SoC 组件模型已经接入统一 FuncModel

`sim/func_model.py` 当前构造了共享 DRAM/SRAM，并实例化：

- `CrossbarModel`；
- `PCIeModel` 和 PCIe DMA；
- `MMIOBridge`；
- golden MXU/SFU/Vector；
- firmware；
- `RISCVMini`；
- IRQ 回调和 boot ROM。

因此，早期文档中“这些模型完全不存在”的描述已经过时。当前主要问题已经从“缺模型”转为“缺一条 signoff-grade 的集成前门路径和统一证据”。

#### 4.1.3 已有较多分项和局部集成验证

当前 checklist 还记录了 PCIe DMA、Crossbar 并发、Doorbell ring、INTC、Host CPU communication、Spike+firmware 等验证。这些结果说明各组件并非空壳，也为后续系统级场景复用提供了基础。

### 4.2 为什么当前仍不足以作为整个 SoC RTL golden

#### GR-01：缺少统一的 SoC 架构可观察契约

目前各测试分别验证数值、协议、firmware 或外设行为，但没有一份冻结的 golden contract 明确：

- 哪些寄存器、状态位、错误码和中断属于必须逐项对拍的架构行为；
- 哪些内存写入和 completion 顺序必须一致；
- 哪些行为允许实现相关；
- untimed Func Model 与 cycle-accurate RTL 的比较边界；
- reset、并发、异常条件下的确定性要求。

如果没有这个契约，RTL 对拍容易只比较最终数据，漏掉地址、状态、IRQ 和异常处理错误。

**建议：P0。** 以 ABI schema、regmap、descriptor、ring 和中断定义为单一事实源，形成 `SoC Golden Observable Contract`。Func Model、RTL testbench、firmware 和 Host Runtime 都从该契约生成或检查常量。

#### GR-02：缺少一条贯穿真实 SoC 前门的硬 gate

当前验证中存在 direct-MMIO、直接调用模型、直接构造 ring/descriptor 等路径。它们适合定位单元问题，但不能替代完整 SoC 前门。

必须新增至少一条不得绕过关键部件的场景：

```text
Host CPU software
  -> Host Runtime
  -> PCIe/BAR/TLP
  -> Crossbar/shared DRAM
  -> command ring + descriptor
  -> doorbell
  -> real firmware on RISC-V/Spike
  -> DMA/MMIO engine execution
  -> completion/status/INTC
  -> PCIe readback
  -> Host Runtime result
```

除初始化、镜像加载和测试观测外，场景中不应直接调用 `MMIOBridge`、engine model 或 firmware 内部方法。

**建议：P0，作为 SoC golden signoff 的第一硬 gate。**

#### GR-03：Python firmware 与真实 firmware 的语义尚未被等价锁定

`sim/func_model.py` 默认仍选择 `NPUFirmware`，只有显式设置 `use_spike=True` 或环境变量时才启用 Spike。大量快速测试使用 Python firmware 是合理的，但最终 signoff 不能默认依赖它。

目前 real-firmware runner 还会在 Python 中直接构造 `FuncModel(use_spike=True)` 并直接准备 ring/doorbell，绕过 Host Runtime。这证明了部分 firmware 路径可以执行，但没有证明生产软件路径能够驱动它。

需要对同一输入场景同时运行：

- Python `NPUFirmware`；
- Spike 上的真实 firmware ELF。

并比较 descriptor 消费、MMIO 序列、内存副作用、completion、error 和 IRQ。任何语义差异都必须有明确豁免。

**建议：P0。** Python firmware 保留为快速 oracle，真实 Spike firmware 作为最终 signoff gate。

#### GR-04：FM 与 RTL 的共享场景和差分框架还不够严格

当前差分框架具备基础，但 fault injection 的通过条件存在反空洞风险。`sim/verification/differential.py` 中，只要注入动作发生，即使 `detected_faults` 为空，也可能把 fault gate 判为通过。

这会导致“测试确实改坏了输入，但比较器没有真正发现错误”仍然 PASS。

需要：

- 同一份场景描述同时驱动 FM 和 RTL testbench；
- 对 input、descriptor、memory image、寄存器动作使用同一份序列化格式；
- 独立 golden 计算，而不是 FM 与 RTL 共享同一个可能出错的实现；
- 正向测试比较数据、状态、IRQ 和副作用；
- 负向测试必须证明指定 checker 实际报错，不能仅证明 mutation 已执行；
- 对故障类别定义 expected detector，并要求 detector 命中。

**建议：P0。** 在 RTL 尚未接入时，先用“正确 FM vs 被定点 mutation 的 FM”验证差分框架自身不空洞。

#### GR-05：系统级边界、并发和恢复验证需要扩展

现有分项测试不能完全代表组合状态空间。至少应增加以下 SoC 场景：

- BAR 边界、未映射地址、非法访问宽度和非对齐访问；
- DMA 跨页、跨 buffer、零长度、最大长度和越界；
- ring 空、满、wrap-around、多 entry、producer/consumer 竞争；
- doorbell 重复写、丢失写和 completion 顺序；
- ACK-before-PENDING、多个 IRQ 同时 pending、mask/unmask、重复 ACK；
- Crossbar 多 master 访问同一 slave、读写冲突和 backpressure；
- reset 时有 in-flight DMA/engine/firmware command；
- malformed descriptor、非法 opcode、超时和 engine error 的传播；
- host 提前释放 buffer、fence 销毁与 command 完成竞争；
- 多 queue 或多 context，如果产品架构允许。

**建议：P0 覆盖协议边界与恢复；P1 扩展随机并发和多 seed。**

#### GR-06：full-shape Qwen 需要通过完整 SoC 路径重放

现有 full-shape Qwen blk.0 数值验证可以关闭 G0/G1，但其 direct-MMIO、tiled scheduler 和 connected golden 结果不能自动关闭 G2。

SoC golden signoff 至少应选择一个代表性 workload：

- Qwen2.5-3B blk.0 full-shape；
- 真实 weights、activations、scales 和 descriptor；
- 由 Host Runtime 经完整前门提交；
- firmware 调度；
- 最终 output 与独立 oracle 比较；
- 中间关键 boundary、completion 和 IRQ 可检查；
- corruption 后必须被 checker 检出。

完整 decode token、多层和 KV cache 可以作为后续软件产品 gate，但 full-shape blk.0 的 SoC 前门执行应属于当前 SoC golden P0。

#### GR-07：evidence 可复现性和状态汇总仍需修正

当前仓库已有大量 evidence，但存在以下问题：

- 部分汇总文件引用的底层 evidence 未提交；
- 本地缺少 Spike binary，无法复现 real-firmware evidence；
- 某些状态汇总把 `BLOCKED` 优先于 `FAIL`，会遮蔽已有失败；
- aggregator 对未知 JSON 或非空普通日志可能默认 PASS；
- CI 使用 `--no-stale-check`，且部分关键任务 `continue-on-error`；
- checklist 的 PASS 范围与实际可执行路径有混用。

**建议：P0。** 每条 PASS 必须记录 commit、命令、工具版本、asset hash、退出码和原始结果。缺少底层 evidence、无法复现或依赖未构建产物时，只能标记 `BLOCKED` 或 `PARTIAL`，不能继承历史 PASS。

#### GR-08：明确 Func Model golden 与性能模型的边界

untimed Func Model 不应承担 cycle-accurate 性能 golden，但必须定义：

- 功能结果；
- memory/register side effects；
- command、completion 和 IRQ 的架构顺序；
- status/error 可见时机的抽象语义；
- 对 RTL 允许的 cycle 差异。

周期、吞吐、TTFT、TPOT 和 backpressure timing 应单独由性能模型/RTL 验证 signoff，不应混入功能 golden PASS。

### 4.3 SoC Golden 建议验收标准

以下条件全部满足后，才建议把“Func Model 作为 SoC RTL golden reference”标记为 PASS：

| ID | Signoff 条件 |
|---|---|
| SGR-01 | 架构可观察契约冻结，ABI/regmap/descriptor/ring/IRQ 无手工漂移 |
| SGR-02 | 至少一条 Host 前门到 result readback 的完整场景通过，关键部件无旁路 |
| SGR-03 | 同一场景在 Python firmware 与真实 Spike firmware 上结果及副作用一致 |
| SGR-04 | full-shape Qwen2.5-3B blk.0 经完整 SoC 路径通过独立数值 oracle |
| SGR-05 | 数据、状态、error、completion、IRQ 和关键 memory side effect 均纳入对拍 |
| SGR-06 | ring/DMA/BAR/IRQ/reset/非法 descriptor 的边界与恢复场景通过 |
| SGR-07 | 差分与 fault injection checker 通过反空洞验证 |
| SGR-08 | 同一场景可无语义改写地重放到 RTL testbench |
| SGR-09 | 所有 signoff evidence 可在固定 commit 和工具链上重新生成 |
| SGR-10 | 性能/timing 未被 Func Model 功能 PASS 越界声明 |

### 4.4 方向一结论

建议维持以下状态：

- `NPU engine numerical golden`: PASS；
- `NPU subsystem functional golden`: PASS/PARTIAL，按具体场景标注；
- `SoC RTL golden reference readiness`: PARTIAL；
- `SoC golden signoff`: 暂不批准。

主要阻塞不是某个 engine 算法错误，而是缺少统一、真实、无旁路、可重放到 RTL 的 SoC 前门场景，以及与之配套的架构可观察契约和可信 evidence。

## 5. 方向二：端到端软件栈在 Func Model 上运行评审

### 5.1 正确的软件/硬件职责边界

本项目是 NPU 协处理器，因此以下软件应运行在主控 CPU：

- llama.cpp/ExecuTorch framework；
- graph partition 和 supported-op 判断；
- tensor/buffer 生命周期管理；
- command lowering 和 descriptor 构造；
- Host Runtime；
- transport；
- queue/fence/error 管理。

NPU Func Model 应执行：

- firmware 命令消费和调度；
- DMA；
- MXU/SFU/Vector 等计算；
- completion、status 和 interrupt。

所以“backend 运行在主控 CPU”不是问题。真正的 signoff 判断是：支持的计算是否离开 CPU fallback，通过公开软件接口提交给 Func Model，并从 Func Model 读回结果。

### 5.2 当前已经具备的基础

#### 5.2.1 ABI 与生成链

`scripts/gen_npu_abi.py --check` 当前通过，生成产物与 schema 一致。相关 ABI 测试通过，说明寄存器、opcode 和结构体同步机制已经具备较好基础。

#### 5.2.2 Host Runtime 基础对象

当前 C Runtime 已有：

- device/context；
- command list；
- buffer；
- fence；
- mock/FM transport 抽象；
- 基础错误码和生命周期测试。

这些接口足以作为继续实现真实提交路径的骨架。

#### 5.2.3 FM device protocol/server 基础

`sim/device_server.py` 已实现 buffer、fence、submit、status、reset 等 protocol handler。FM transport 也已具备连接和部分资源操作能力。

#### 5.2.4 Framework backend 骨架

llama.cpp backend 已具备：

- backend 注册；
- supported-op/graph 检查；
- encoded blob 的构造和验证入口；
- CPU fallback。

ExecuTorch backend 也已经具备 delegate 初始化、blob 检查和 Runtime 调用骨架。

这些工作说明 framework 集成方向已经启动，但还没有跨过“真实计算 offload”门槛。

### 5.3 当前阻塞端到端运行的缺口

#### SW-01：Host Runtime 只能提交 NOP

`software/include/caduceus/runtime.h` 对外只暴露 `cadCommandListAppendNop()`。当前没有公开、稳定的接口把 lowered NPU command/descriptor blob 附加到 command list。

`software/src/runtime_core.c` 在 submit 时只传递 command list 指针和数量，真实 command serialization 仍未完成。

**结果：** framework 即使完成 lowering，也没有办法通过正式 Runtime API 提交真实 NPU 工作。

**建议：P0。** 设计并实现 typed command append 或 encoded command buffer append；Runtime 负责校验版本、长度、对齐、buffer handle、地址和生命周期。

#### SW-02：FM transport 丢弃真实命令 payload

`software/src/transport_fm.cpp` 的 `fm_submit()` 当前显式忽略 `cmd_data`。但 `sim/device_server.py` 的 submit handler 期望收到真实 ring entry 和 descriptor blob。

这是当前 host software 与 Func Model 之间最直接的断点。

**建议：P0。** 明确 command list 的 wire format，由 Runtime 序列化，FM transport 原样携带，server 解码后写入共享 DRAM/ring 并触发 doorbell。禁止在 transport 中重新解释 framework graph。

#### SW-03：llama.cpp backend 实际计算仍全部回退 CPU

`ggml-npu/ggml-npu.cpp` 当前会检查或构造 NPU blob，随后提交 NOP；最终调用 CPU backend 完成整张 graph 的计算。

因此已有 mock Qwen 测试只能证明：

- backend 生命周期和选择逻辑可运行；
- CPU fallback 结果正确；
- 某些 blob 结构可被生成或校验。

它不能证明任何 Qwen op 已在 Func Model 上执行。

**建议：P0。**

1. 从一个真实 MMUL 节点开始；
2. 支持节点进入 NPU partition；
3. unsupported 节点明确回退 CPU；
4. NPU partition 通过 Runtime/FM 执行；
5. 从 FM buffer 读回 tensor；
6. 用独立 CPU oracle 比较；
7. 统计实际 FM command、op 和 byte 数，禁止用配置值伪装执行计数；
8. 如果声明 NPU-only gate，发生 CPU fallback 必须失败。

#### SW-04：ExecuTorch delegate 同样只提交 NOP

`software/executorch/runtime/caduceus_npu_backend.cpp` 当前在完成 blob 检查后调用 `cadCommandListAppendNop()`。

**建议：P1。** 先关闭 Host Runtime 和 llama.cpp 的真实命令链，再让 ExecuTorch 复用同一 command IR、Runtime 和 transport。不要为 ExecuTorch 建立第二套私有提交协议。

#### SW-05：真实 firmware signoff 绕过 Host Runtime

`scripts/run_runtime_spike_signoff.py` 当前直接构造 ring/doorbell，并直接实例化 `FuncModel(use_spike=True)`。它验证了部分 Spike/firmware/FM 链路，但不是端到端 Host Runtime 软件路径。

**建议：P0。** 同一个 C/C++ Runtime 测试程序使用 `fm://`：

```text
cadDeviceCreate
  -> cadBufferAllocate/Write
  -> cadCommandListAppend...
  -> cadQueueSubmit
  -> cadFenceWait
  -> cadBufferRead
  -> compare golden
```

FM server 内部再选择真实 Spike firmware。测试程序不应知道 ring、doorbell 或 FuncModel Python 内部对象。

#### SW-06：buffer、地址和生命周期契约需要在真实路径验证

mock transport 可以掩盖以下问题：

- host handle 与 device address 混淆；
- buffer size/offset 未校验；
- command 引用的 buffer 提前释放；
- fence 完成前读回；
- output buffer cache/同步语义；
- transport 断开后的资源回收；
- server reset 后 stale handle。

**建议：P0。** 先建立单 op 真实 FM 测试，再覆盖越界、use-after-free、double-free、wrong-device handle、timeout 和 reset recovery。

#### SW-07：Qwen 端到端 gate 目前使用 mock，且没有 NPU 执行证明

CI 中 Qwen 软件 signoff 使用 `--device mock://`。已有 evidence 也记录 `device_uri: mock://`。CPU/NPU 输出一致在这种情况下只代表 mock/fallback 一致。

真正的 Qwen 软件 gate 至少需要分三级：

| Gate | 内容 |
|---|---|
| QSW-1 | 单个真实 Qwen MMUL/SFU/Vector 节点经 `fm://` 执行 |
| QSW-2 | full-shape blk.0 partition 经 `fm://` 执行，允许明确的 CPU fallback |
| QSW-3 | 单 token prefill/decode，经真实 firmware 执行并验证 KV/cache 状态 |

每级都必须报告：

- FM 实际执行的 op 数；
- CPU fallback 的 op 数和原因；
- command/bytes 提交量；
- firmware completion；
- 输出误差；
- corruption 后 checker 是否失败。

#### SW-08：`fpga://` 不能继续隐式映射到 mock

`software/src/runtime_core.c` 当前将 `fpga://` 映射到 mock transport。这会让上层误以为 FPGA 路径可用。

**建议：P0。**

- 未实现前，`fpga://` 应显式返回 `UNSUPPORTED`；
- 后续实现 PCIe driver/VFIO/UIO 等真实 transport；
- `fm://`、`rtl://`、`fpga://` 使用同一 Runtime API 和 command wire format；
- 只有 transport 和时序策略不同，上层 backend 不改代码。

#### SW-09：构建和 CI 目前不能提供可信的持续 signoff

评审时观察到：

- protocol CI 命令缺少生成代码目录的 `PYTHONPATH`，按原命令 collection 失败；
- 修正路径后仍有依赖 shared library 未构建而 skip 的测试；
- `software/build/libcaduceus_runtime.so` symlink 失效；
- Qwen gate 缺少 `build/llama/bin/llama`；
- strict Spike runner 缺少 `spike_src/build/spike`；
- Spike checks 使用 `continue-on-error`；
- Qwen gate使用 mock；
- aggregator 允许 stale check 关闭，并可能掩盖 FAIL。

**建议：P0。** CI 必须从 clean checkout 构建所有产物，关键 gate 不允许 skip、`continue-on-error`、mock 替代或历史 evidence 继承。

#### SW-10：错误、恢复、并发和安全性覆盖不足

端到端软件栈除 happy path 外，还应验证：

- malformed/truncated command blob；
- ABI version mismatch；
- unsupported opcode；
- buffer offset/size overflow；
- fence timeout/cancel/destroy；
- transport disconnect/reconnect；
- firmware/engine error 向 framework 的传播；
- 多线程 context/queue；
- graph 执行中 reset；
- command decoder fuzz；
- C/C++ Runtime 的 ASan/UBSan；
- backend 初始化失败和部分资源创建失败后的清理。

**建议：P1。** 其中 blob 边界、buffer 越界、timeout 和错误传播应提前到 P0。

### 5.4 软件栈建议验收标准

以下条件全部满足后，才建议把“端到端软件栈已在 Func Model 上运行”标记为 PASS：

| ID | Signoff 条件 |
|---|---|
| SSW-01 | 主控 CPU 上的 backend/runtime 能通过公开 API 提交非 NOP 的真实 NPU command |
| SSW-02 | FM transport 完整转发 command payload，server 按统一 wire format 消费 |
| SSW-03 | 至少一个真实 MMUL 从 allocate/write/submit/wait/read 全链路通过 |
| SSW-04 | 真实 Spike firmware 由 Host Runtime 驱动，无 Python 直接构造 ring/doorbell |
| SSW-05 | llama.cpp 至少一个 supported partition 真正在 FM 执行，CPU fallback 可观测 |
| SSW-06 | full-shape Qwen blk.0 经 `fm://` 执行并与独立 oracle 比较 |
| SSW-07 | unsupported op、非法 blob、buffer 越界、timeout 和 reset 错误可正确返回上层 |
| SSW-08 | 执行计数来自 FM/server/firmware 实际事件，而非 backend 配置或预期值 |
| SSW-09 | 同一应用二进制或同一 Runtime 调用序列可切换 `fm://`、`rtl://`、`fpga://` |
| SSW-10 | clean checkout CI 可构建并运行，关键 gate 无 mock、skip、stale evidence 或 masked failure |

### 5.5 方向二结论

当前软件工作建议标记为：

- `ABI/schema/generator`: PASS；
- `Host Runtime lifecycle and mock transport`: PASS，范围受限；
- `FM protocol/resource operations`: PARTIAL；
- `real command execution through Host Runtime`: FAIL；
- `llama.cpp computation on FM`: FAIL，当前为 CPU fallback；
- `ExecuTorch delegated computation on FM`: FAIL，当前为 NOP scaffold；
- `same stack on FPGA`: BLOCKED。

端到端软件栈的首要阻塞不是 framework 算子覆盖，而是 Runtime command payload 链尚未打通。应先解决 Runtime/transport/server/firmware 的单 op 真实闭环，再扩大到 Qwen graph。

## 6. 建议实施优先级

### Phase 0：冻结契约和可信 gate

1. 冻结 SoC golden observable contract。
2. 统一 ABI、descriptor、ring、regmap 和 IRQ 定义。
3. 修正 evidence aggregator 和 CI 的 fail/blocked/stale 语义。
4. 建立 clean-checkout 可复现基线。

### Phase 1：打通单 op 软件真实闭环

1. 扩展 Host Runtime command list API。
2. 实现 command serialization。
3. FM transport 转发 payload。
4. device server 写 ring/descriptor 并触发 doorbell。
5. 真实 Spike firmware 消费命令。
6. Host Runtime 等待 fence 并读回结果。
7. 用单 MMUL 做独立 golden、corruption 和错误传播验证。

### Phase 2：形成 SoC golden 前门场景

1. 把 Phase 1 场景扩展为 MXU/SFU/Vector/DMA chain。
2. 覆盖 completion、INTC、reset、ring wrap 和错误场景。
3. 对 Python firmware 与 Spike firmware 做等价比较。
4. 将同一场景描述接入 RTL testbench。

### Phase 3：接入 llama.cpp

1. 实现真实 supported-op partition。
2. supported partition 走 `fm://`，unsupported op 明确 CPU fallback。
3. 增加真实执行计数和 silent-fallback hard fail。
4. 依次关闭单 op、full-shape blk.0、单 token gate。

### Phase 4：扩展 ExecuTorch 和 FPGA

1. ExecuTorch 复用同一 IR/Runtime/transport。
2. 实现 `rtl://` 和 `fpga://` transport。
3. 在 RTL、FPGA 上重放相同软件测试，不改 framework backend。

## 7. 建议的近期硬 Gate

为了最短路径同时推进两个目标，建议把下一阶段唯一的 P0 演示定义为：

> 一个主控 CPU C/C++ 程序通过公开 Host Runtime API，使用 `fm://` 分配和写入真实 buffer，提交一个非 NOP 的 Qwen shape MMUL command；FM server 将命令写入 ring，Spike 上的真实 firmware 消费 descriptor 并驱动 Func Model engine；程序等待真实 completion/IRQ 后读回结果，并与独立 CPU oracle 对比。相同场景随后能够无语义改写地交给 SoC RTL testbench。

这个 gate 一旦关闭，将同时证明：

- 软件栈不是 mock；
- command payload 链真实存在；
- firmware 不是 Python 旁路；
- Func Model 覆盖 SoC 主路径；
- 场景具备 RTL golden 重放价值。

在此之前，不建议优先扩展更多 framework 模型或增加更多 mock Qwen 用例，因为它们无法关闭当前最关键的系统断点。

## 8. 最终评审结论

### 8.1 Func Model 作为 SoC RTL golden reference

**评审结论：PARTIAL，不批准整个 SoC golden signoff。**

可以批准的子范围是 NPU engine 和 Qwen blk.0 的数值 golden。不能批准的范围是 Host 前门、真实 firmware、SoC 外设和架构可观察行为组成的完整系统 golden。

### 8.2 端到端软件栈在 Func Model 上运行

**评审结论：FAIL，不批准端到端软件 signoff。**

软件栈的主控 CPU 部分已经形成骨架，但真实 NPU command 没有经过 Host Runtime/FM transport 到达 Func Model；llama.cpp 和 ExecuTorch 当前最终仍使用 CPU 或 NOP。下一步必须先关闭非 NOP 单 op 全链路，再开展 Qwen graph signoff。

## 9. 主要代码与证据锚点

| 主题 | 文件 |
|---|---|
| Func Model SoC 组件集成 | `sim/func_model.py` |
| FM command server | `sim/device_server.py` |
| 差分与 fault gate | `sim/verification/differential.py` |
| Func Model 现有 checklist | `docs/func-model-signoff-checklist.md` |
| Host Runtime API | `software/include/caduceus/runtime.h` |
| Host Runtime core | `software/src/runtime_core.c` |
| FM transport | `software/src/transport_fm.cpp` |
| llama.cpp backend | `ggml-npu/ggml-npu.cpp` |
| ExecuTorch backend | `software/executorch/runtime/caduceus_npu_backend.cpp` |
| Spike runtime signoff runner | `scripts/run_runtime_spike_signoff.py` |
| 软件 signoff 汇总器 | `scripts/aggregate_software_signoff.py` |
| CI gate | `.github/workflows/caduceus-core-ci.yml` |

