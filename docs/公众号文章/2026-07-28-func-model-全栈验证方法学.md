# 用功能模型驱动 NPU 全栈验证：CaduceusCore 的方法论

> 芯片验证的传统剧本是：RTL 团队写 Verilog，软件团队等 FPGA，双方各自调试，联调时互相指认对方有 bug。CaduceusCore 换了一种做法——让一个 Python 写的功能模型同时充当 RTL 的黄金参考、软件的开发目标、以及 Agent 自动验证的驱动引擎。

---

## 一、问题：硬件和软件各说各话

一颗 NPU 从架构设计到能跑 Qwen3B，至少需要三拨人接力：架构师定规格、RTL 工程师实现、软件工程师写驱动和编译器。传统的做法是：

1. 架构师输出一份 Word 文档描述寄存器、指令、数据流
2. RTL 工程师照着文档写 Verilog，自己搭 testbench 验证
3. 软件工程师照着同一份文档写固件和驱动
4. 两边都写完，上 FPGA 联调，发现行为不一致——然后开始漫长的「谁对谁错」拉锯

问题出在中间那步：规格文档是自然语言写的，RTL 和软件是两种语言读的。同一条「MXU 的 K-tile 累加从 activation SRAM 哪个偏移开始读」，固件写的是 `k_start * 64`，硬件的 SRAM 偏移却取决于实际的 `M` 维度。联调时固件读到垃圾数据，两边都认为自己没错。

这不是一个 bug，是一类 bug。根源在于：没有一份可执行的、双方都能直接对照的权威规格。

---

## 二、Func Model 的三重角色

CaduceusCore 的做法是：用 Python 写一个完整的功能模型（Func Model），让它同时承担三件事。

### 角色一：RTL 的 Golden Reference

Func Model 按 SoC 的实际行为模拟每一拍：Host 写 descriptor 到 DRAM、写 doorbell 触发 RISC-V 固件、固件解析 descriptor 配置 DMA、DMA 从 DRAM 搬权重到 SRAM、MXU 分 tile 计算、SFU 做后处理、结果写回 DRAM。

每个步骤的输出都是 bit-exact 的——和 RTL 仿真做逐比特对比。同一组输入，Func Model 和 RTL 必须产出完全相同的 hex 文件。如果对不上，RTL 就是错的，不需要讨论。

### 角色二：性能模型

Func Model 上每个模块的 cycle 计数是真实的：

| 模块 | Cycle 模型 |
|---|---|
| MXU | 128×128 tile = 385 cycles（fill+drain） |
| DMA | 每 tile 搬运 = 191 cycles（LPDDR5-64b 带宽） |
| SFU | 每元素 1 cycle |
| RISC-V 固件 | 每指令 1 CPI |
| Doorbell 轮询 | 空转等待计入 |

跑 Qwen2.5-3B 的 decode，Func Model 输出的 22 tok/s 不是公式估算，是逐 cycle 模拟的结果。

### 角色三：RTL 开发的 Spec

模块划分、寄存器布局、指令集——全部以 Func Model 的代码为准。RTL 开发者不需要看几十页的架构文档，只需要看 Func Model 的接口：MXU 输入什么 shape、输出什么精度、地址空间怎么映射。代码比文档更精确、且不会过期——文档可能忘了更新，但 Func Model 改了 RTL 对不上测试就红。

---

## 三、统一软件栈：同一套 Runtime，三个后端

只靠 Func Model 把硬件行为定准还不够——还需要保证软件在不同阶段看到的硬件行为是一致的。

传统流程里，软件团队在 Func Model 阶段用 Python 写临时脚本驱动模拟器，到 RTL 阶段换成 Verilog testbench 的 C-DPI 接口，到 FPGA 阶段再换成 PCIe 驱动的内核模块。每次切换，驱动代码重写一遍，上次通过的测试无法继承。

CaduceusCore 的做法是定义一套稳定的 C Host Runtime ABI，通过 URI 切换后端：

```
"fm://"   → 驱动 Func Model（Python 模拟器）
"rtl://"  → 驱动 RTL 仿真（Cocotb）
"fpga://" → 驱动 FPGA 板卡（UIO/VFIO）
"mock://" → 纯软件 mock，用于 CI 快速测试
```

同一套 llama.cpp 后端代码，编译一次，运行时通过 URI 选择后端。在 Func Model 上跑通的 Qwen3B 推理，切到 RTL 后端不需要改一行代码——如果 RTL 的行为和 Func Model 一致，测试直接通过。这从机制上消除了「软件说硬件有问题，硬件说软件有问题」的扯皮空间。

---

## 四、ABI 单源生成：一个 JSON，五个 Target

统一 Runtime 的前提是硬件接口定义不能有多份拷贝。寄存器地址、opcode 编号、descriptor 布局、ring buffer 协议——这些信息如果分散在 SystemVerilog 头文件、C 头文件、Python 常量、固件宏定义里，同步必然出问题。

CaduceusCore 的做法是把所有硬件/软件接口定义收进一份 JSON：

```json
{
  "abi": { "major": 1, "minor": 0 },
  "address_regions": {
    "MXU": { "base": "0x40000000", "size": "0x00001000" },
    "SFU":  { "base": "0x40001000", "size": "0x00001000" }
  },
  "opcodes": {
    "MMUL": 0x01, "SFU_OP": 0x02, "DMA_XFER": 0x03
  },
  "capability_bits": {
    "SUPPORTS_INT4": 0, "SUPPORTS_BF16": 1
  }
}
```

从这份 JSON，一个脚本自动生成：

- **Python** 常量——Func Model 使用
- **C 头文件**——Host Runtime 和固件使用
- **C++ RAII wrapper**——上层应用使用
- **SystemVerilog package**——RTL 使用
- **Markdown 文档**——人类阅读

改一个 opcode 编号只需改一处，五个 target 自动同步。这个做法和工业界的 SystemRDL 标准思路一致，但覆盖范围更广——SystemRDL 只管寄存器，不管 opcode 和 descriptor。

---

## 五、验证闭环：共享场景 + 差分对比

有了 Golden Reference 和统一 Runtime，验证的核心流程变得简洁：

```
Scenario（同一个测试场景）
    ├→ FuncModelAdapter → Func Model → 输出 A
    └→ RTLAdapter       → RTL 仿真  → 输出 B
            ↓
    Differential Runner（A vs B，逐 bit 对比）
```

一个 Scenario 对象定义输入（权重矩阵、激活向量、模型参数）、操作序列（写 descriptor → 写 doorbell → 等完成 → 读结果）、以及容差范围。同一个 Scenario 同时发给 Func Model 和 RTL，输出自动对比。

这和 RISC-V 的 Spike 黄金模型对比 RTL 的思路一致，但 CaduceusCore 的 Scenario 层包含了完整的数据流验证——不仅验证指令执行结果，还验证 DMA 传输、doorbell 协议、中断链路。

固件集成是强制的：同一个 C 固件源码，用 RISC-V 交叉编译器编译，Spike ISS 执行，Func Model 通过 MMIO bridge 对接。不允许用 Python 脚本模拟固件行为来蒙混过关。

---

## 六、Agent 自动验证：零人工干预

上述所有验证，从 plan 分解到 evidence 生成，全部由 Agent 自动完成。

```
Plan → Todo 分解 → 执行 → Evidence 收集 → Signoff 判断
  ↑                    ↓
  └── Bug track + Learnings 沉淀 ←─┘
```

一个完整验证阶段的工作流：Agent 读取 plan（markdown 文件，由人类定义目标），自动拆解为可执行的 todo 列表，逐项执行——写测试代码、跑仿真、收集结果、判断是否通过。通过的写 evidence，失败的记录 bug track，然后把修正方案反馈到 learnings 里供下个阶段使用。

Func Model Signoff v2（算子数学正确性）和 v3（SoC 集成通路）共计 50+ 项验证，全部是 Agent 自动执行和判断的。人类只需要看最终的 signoff 报告。

据我们调研，这种 Agent 驱动的全自动芯片验证闭环，目前在公开学术文献和工业界报道中尚无对标。D.E. Shaw 的 Anton 超算芯片验证是工程师手工跑 co-simulation，RISC-V 生态用 UVM testbench 依赖人写 testcase。商业工具如 Synopsys HECTOR 能做形式化等价检查，但前提是有人写好参考模型和验证约束。

---

## 七、方法和工业实践的对齐

虽然 Agent 自动化是独特的，但 CaduceusCore 的方法论在几个关键点上和工业最佳实践对齐：

| 维度 | 工业对标 |
|---|---|
| 功能模型作为 Golden Reference | D.E. Shaw Anton（C++ 架构模拟器）、RISC-V Spike |
| ABI 单源生成 | SystemRDL（Accellera 标准）、IP-XACT（IEEE 1685） |
| 统一 Host Runtime | Vulkan/CUDA 风格 host API |
| 差分验证 + 共享场景 | UVM scoreboard 概念的分层实现 |
| 编译器栈 | Qualcomm Hexagon-MLIR（开源 MLIR 编译器） |
| 开源优先 | llama.cpp + ExecuTorch + CMake/CTest |

每个单点技术上都有对标。组合方式——特别是「Agent 自动验证 + 统一 Runtime + ABI 单源」的工程闭环——是 CaduceusCore 的差异化设计。

---

## 八、当前状态和已知局限

截至 2026 年 7 月底：

**已完成：**
- Func Model 数学正确性签收：17/17 PASS
- SoC 集成通路签收：F1-F4 全部 APPROVE
- 统一 Host Runtime ABI 定义完成（C + C++ + Python）
- 固件 Spike 集成：9/9 场景通过
- llama.cpp 后端通过 Host Runtime 对接 Qwen3B：软件 gate 通过
- Phase 4 编译器栈计划已定稿（LLVM 后端 + AI Pass + ggml 前端）

**未完成：**
- RTL/FPGA 适配器目前为骨架实现，完整 differential 验证待 RTL 开发完成后执行
- 性能签收未做——当前签收只覆盖功能正确性，不包含功耗/面积/频率验证
- 编译器栈尚未实际实现
- Agent 自动验证依赖 Agent 自身的判断准确性，长尾场景的误判率需持续跟踪

**一个已知的风险：** 工业界不采用 Agent 自动验证，未必是没想到，更可能是评估后认为风险不可接受。CaduceusCore 能这样做，部分原因是项目规模尚在可控范围——当 RTL 代码量超过十万行、验证空间爆炸时，Agent 的判断力是否能保持可靠，还需要验证。

---

## 九、总结

CaduceusCore 的验证方法学可以概括为三句话：

**让 Python 功能模型成为唯一的真理源。** RTL 对不对、软件对不对，都以它为准。不存在「RTL 正确但软件理解有偏差」的灰色地带。

**让统一 Runtime 消解后端切换的摩擦。** 同样的软件在 Func Model 上跑通，切到 RTL 不需要改代码。不一致就是硬件 bug，不需要讨论。

**让 Agent 做验证的执行者，人做验证的定义者。** Plan 由人写，执行由 Agent 做，证据自动收集，结论对所有人透明。

这不是一套全新的理论——Golden Reference、ABI 生成、差分验证在工业界都有成熟实践。CaduceusCore 的贡献是把这些方法以极低的工程 overhead 组合在一起，并让 Agent 接管了其中重复性最高、最依赖纪律性的部分。

---

*本文基于 CaduceusCore 开源 NPU 项目的实际工程记录撰写。项目地址：github.com/anekin/CaduceusCore，Arc Model 独立仓库：github.com/anekin/npu_arc_model。*
