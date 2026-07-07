# PCIe DMA 规划增量经验 — 补充 CaduceusCore 验证原则

> **版本**: v1 | **日期**: 2026-07-06
> **用途**: 补充 `docs/caduceus-verification-lessons.md`，记录 PCIe DMA 子系统规划过程中新增的经验教训
> **来源**: CaduceusCore PCIe DMA feat_pcie 分支 4 轮 Metis+Momus 规划评审

---

## 原则 15：子系统验证范围必须明确，模块级和集成级用不同 TB

- PCIe DMA 子系统验证 ≠ 全 SoC 验证
- 模块级单独搭 testbench（快速迭代），集成/E2E 复用已有 SoC TB
- 在计划中显式声明"验证的是 PCIe 子系统，不测 MXU/SFU 计算正确性"

> **PCIe DMA 案例**: 计划明确划分 W2（独立 `pcie_dma_tb.sv`，快速迭代,秒级 VCS 编译）和 W5（共用 `tb_soc.v` + `cocotb_bridge.py`，全环境 E2E）。这种两级 TB 策略避免了"一个 TB 测所有"导致定位慢和仿真慢的问题。

## 原则 16：开源 BFM 作为参考模式库，不做直接 import

- 上游 verilog-pcie 的 `tb/pcie.py` 是 MyHDL/cocotb BFM，数据结构（`Signal`、`always`、`yield`）与纯 Python 周期级模拟器不兼容
- 正确的复用方式: 以它为"参考模式库"——抄 TLP 字段布局、DMA 测试场景、completion 生成逻辑
- 具体复用: TLP 头构造函数、DMA 测试的 TLP 序列、RootComplex→Endpoint completion 发送逻辑、MSI/MSI-X 字段
- 不直接 import，而是翻译成 Func Model 的纯 Python 实现

> **PCIe DMA 案例**: 当前自研 `sim/models/pcie.py`（172行）远不如上游 BFM 完整。计划 W1 明确要求 Func Model 的 TLP 头构造必须与上游 `_build_memwr_header` / `_build_memrd_header` 逐字段对齐，R1 Review Gate 要求输出对比日志作为证据。

## 原则 17：Func Model golden reference 就绪是硬门禁，不是软建议

- Func Model 必须先通过自身充分验证，才能作为 RTL 的 golden reference
- 自身验证至少包含: TLP 头格式对拍（对比上游参考实现）、边界分片（MPS 边界）、接口生命周期（tag 分配/回收不泄漏）、错误传播（UR/CA → status code）
- 已知未覆盖点（如 completion timeout recovery）在 Func Model 阶段就写入 bug tracking
- Review Gate 若发现 golden sufficiency 不足，必须退回补 Func Model

> **PCIe DMA 案例**: 计划中 W1→W2 之间的 R1 Review Gate 明确标注 "Hard gate: Func Model 全部 7 TCs PASS 且 golden sufficiency 5 项检查通过后，才允许进 W2 RTL。不通过则退回 W1 补 Func Model。" 这是从 Lessons 原则 1 的具体化。

## 原则 18：接口兼容性 > 功能完整性（选硬件模块时）

- 选 `dma_if_pcie` 而不是 `pcie_us_axi_dma` 的核心理由不是功能差异，而是接口兼容性
- `pcie_us_axi_dma` 功能更全（AXI master + descriptor + 流控），但它的 PCIe 接口是 Xilinx 专用 AXI-Stream（75/60-bit `tuser`，编码与通用 TLP 不兼容）
- `dma_if_pcie` 功能较简（RAM 接口），但它的 TLP 端口与现有 `pcie_axi_master` 和 cocotbext-pcie 完全兼容
- 决策逻辑: 先用接口兼容的模块跑通全链路，功能不足的部分在外层 adapter 补

> **PCIe DMA 案例**: 如果选了 `pcie_us_axi_dma`，需要额外写一个 AXI-Stream→TLP 转接桥，工作量等于重写 `dma_if_pcie`。4 轮计划评审中没有一个人质疑这个选择。

## 原则 19：参数覆盖值和计算公式必须在计划中逐项验证

- 所有参数覆盖不能只写"override to 512"，必须同时给出默认值、覆盖值、覆盖的文件和行号、以及计算公式
- 公式本身也要验证——`RAM_SEG_DATA_WIDTH = TLP_DATA_WIDTH * 2 / RAM_SEG_COUNT` 和实际需要的 `256` 矛盾，直到第 3 轮 Metis 评审才发现
- 计划阶段多花 5 分钟验证参数公式，RTL 阶段少浪费 5 小时 debug

> **PCIe DMA 案例**: C3 参数表中 `RAM_SEG_DATA_WIDTH` 公式写 `TLP_DATA_WIDTH*2/RAM_SEG_COUNT=512` 但与表格值 `256` 矛盾。正确的公式应为 `TLP_DATA_WIDTH/RAM_SEG_COUNT=256`。此错误在 Metis 第 2 轮评审中被发现并修正。

## 原则 20：Opcode/寄存器分配必须事前审计，不靠"后面再说"

- 看似简单的 opcode 号（`OP_PCIE_DMA = 5`）与已有 `ROPE` opcode 0x05 冲突
- 出问题不是因为架构复杂，而是因为"后面再确认"的思维惯性
- 在 plan 中就要确定 opcode 号、地址空间、中断位分配，并用 grep 确认无冲突

> **PCIe DMA 案例**: Momus 第 2 轮评审发现 `OP_PCIE_DMA = 5` 与 `npu_firmware.c:424` 中 `ROPE` opcode 0x05 冲突。修正为 `7`。如果没有评审，这个 bug 会直接 break 已有 ROPE 功能。

## 原则 21：跨分支工作必须记录分支策略和文件保护规则

- `feat_pcie` 从 `main` 分出——所有 commit 在 feat_pcie 上，不 force push
- 硬规则: `git diff --name-only origin/main..feat_pcie | grep 'rtl/ip/verilog-pcie/'` 必须为空
- 这条规则不是"建议"，是 T6.1 的硬性 acceptance criterion，Review Gate 执行
- 违反则 W6 不能通过

> **PCIe DMA 案例**: 计划中 D9 决策明确 "NEVER modify vendored verilog-pcie source files"。T6.1 把 vendored file gate 做进了 success criteria #7 和 final verification wave F4。这条约束贯穿了整个规划过程——每个集成决策（APB adapter 而不是改 dma_if_pcie、pcie_tlp_mux 在 wrapper 用而不是改源码）都受此约束。

---

## 补充验证清单（在原有 14 项基础上追加）

| # | 检查项 | 时机 |
|:--:|------|:--:|
| ☐ | 子系统验证范围明确——模块级独立 TB + 集成级复用 SoC TB | Plan 编写时 |
| ☐ | 开源 BFM/参考实现复用方案明确——不直接 import，做参考模式库 | Func Model 设计时 |
| ☐ | Func Model golden sufficiency 硬门禁——不通过则退回补 Func Model | Phase 1→2 过渡 |
| ☐ | 新硬件模块选型基于接口兼容性而非功能列表 | 架构决策时 |
| ☐ | 参数覆盖表逐项验证: 默认值 + 覆盖值 + 文件:行 + 公式正确性 | Plan 编写时 + Review |
| ☐ | Opcode/寄存器/中断位分配事前审计——grep 确认无冲突 | Plan 编写时 |
| ☐ | Git 分支策略和文件保护规则写入 plan 并作为硬 acceptance criterion | Plan 编写时 |

---

## 原则 22：工具和环境变量用脚本封装，不靠 agent 记忆

- Plan 中每条 QA 命令要写成脚本调用形式，而不是裸命令
- 原因：执行阶段会有不同 agent（不同 session、不同 memory）来跑同一段任务
- 裸命令依赖 agent 自己设置 `PYTHONPATH`、`module load`、`source activate` 等环境变量——任何遗漏都会导致重复踩坑
- 解决：每个任务如果涉及 2 个以上工具/环境变量的，必须在 repo 里放一个封装脚本（`scripts/run_*.sh`），agent 只需运行脚本
- 脚本里固化: EDA server SSH 跳转、Python 环境激活、PYTHONPATH/VCS_HOME/COCOTB 等变量、日志重定向到 `.omo/evidence/`
- 脚本即文档：后来者不需要追溯 agent 的 memory 来看当时是怎么跑通的

**脚本封装清单（PCIe DMA 计划需要新增的）**:

| 脚本 | 封装内容 | 使用 todo |
|------|----------|-----------|
| `scripts/run_fm_pcie_dma.sh` | PYTHONPATH=sim pytest sim/tests/test_pcie_dma_fm.py -v | T1.2 |
| `scripts/run_pcie_dma_elab.sh` | module load vcs; vcs + flists + top → simv | T2.1, T3.3 |
| `scripts/run_pcie_dma_sim.sh` | ./simv + test case selection | T2.2 |
| `scripts/run_soc_regression.sh` | bash sim/regression/run_fm_soc_all.sh | T3.3, T6.1 |
| `scripts/run_cocotb_pcie_dma.sh` | module load vcs; make run_pcie_dma_e2e | T5.2 |
| `scripts/run_spike_pcie_dma.sh` | PYTHONPATH=sim python sim/spike_host.py --mode pcie_dma | T4.2 |

> **CaduceusCore 案例**: 之前的任务里，不同 agent 重复犯同样的 `PYTHONPATH` 遗漏错误，或者用错 Python 环境（`miniv.py` mock 而非真实 Spike），根本原因是每个 agent 从零记忆里拼命令。如果用脚本封装，第一个 agent 踩完坑后固化到脚本，后续 agent 直接跑脚本。

---

## 附录: PCIe DMA 规划评审实录

| 轮次 | 评审方 | 关键发现 |
|:--:|--------|----------|
| R1 | Metis | 25 项发现, 3 BLOCKER: 描述符协议未确认、参数覆盖位置缺失、无 E2E 测试场景 |
| R1 | Momus | REJECT: 无 `.omo/plans/*.md` 文件 |
| R2 | Metis | 3 BLOCKER + 3 HIGH: INTC 扩展缺 todo、RAM_SEG_DATA_WIDTH 公式矛盾、MSI-X/IRQ 混淆 |
| R2 | Momus | 3 项: opcode 5 与 ROPE 冲突、INTC 缺 todo、pcie_tlp_demux_bar 不能路由 completion |
| R3 | Metis | T4.1 opcode 5→7 遗漏、TLP 重命名不明确、D1-D9 缺失 |
| R3 | Momus | OKAY — T4.1/W4 修复后无阻塞问题 |
| R4 | Metis | READY — D1-D9 标签全部到位 |
| R4 | Momus | OKAY — ready for execution |
